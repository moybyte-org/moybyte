"""The serial DEV CHANNEL: drive a running console over the board's serial line.

ONE implementation, every board that has a working stdin. Extracted from the
T-Deck mainline port on 2026-08-16, the day RX started working there -- and
UNIFIED 2026-08-17: until then this file had zero importers (the fork it was
extracted for was deleted the next day) while the T-Deck ran a verbatim copy
and the P4 ran an older inline loop with a diverged vocabulary. That triple is
exactly what this module's opening line warns against: three boards, three
vocabularies, is how `swipe` ends up meaning different things. Both boards
construct `DevChannel` now; the on-glass suites are the wire-compat pin.

WHY A CHANNEL AND NOT A REPL. The console owns the loop and never returns to
the REPL, so there is nothing to type at. This reads stdin a byte at a time
between frames and runs whole lines as commands, which is also what makes a
board scriptable: `tools/p4_autotest.py` and tests/test_p4_on_glass.py are built
on exactly this shape.

It NEVER calls readline: one byte at a time via sys.stdin.read(1), only after
poll(0) says MP_STREAM_POLL_RD, accumulating to a newline. A byte read is a byte
consumed, so line noise costs a bounded few bytes per frame and can never park
the loop; an over-long partial line is dropped. And it COUNTS what it swallowed
(`rx=`), so "something is injecting into stdin" is a number rather than a
mystery hang -- which is the diagnostic that proved RX dead on this board for
weeks (rx stuck at 1 while a host write was accepted and discarded).

BOARD BITS ARE INJECTED, not imported: `set_backlight` and an `idle`
(device_boot.IdleBlank). Both may be None, and the commands that need them say
so rather than raising -- a board without a backlight hook should decline `bl`,
not traceback into the frame loop.
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms
try:                       # device (device_util is staged from device/)
    from device_util import _diag_log
except ImportError:        # host / test -- no diag ring; print is it

    def _diag_log(tag, msg, diag):
        print("Moybyte", tag, msg)

# A partial line longer than this is noise; drop it. NOT sized for a human:
# the on-glass harness's `pyexec` uploads code in 768-char chunks wrapped in a
# `py ws._up.__setitem__(...)` line whose %r escaping can nearly double it --
# the first unification shipped this at 96 (the T-Deck's typed-command size)
# and every P4 pyexec line was silently dropped as noise, which presented as
# the RSA-verifier test hanging forever (2026-08-17).
SERIAL_LINE_MAX = 4096
# Bounded drain: noise cannot own the frame. 512 * read(1) is well under a
# millisecond on either board, and a harness chunk line arrives in ~4 frames
# instead of a human-scaled 13.
SERIAL_BYTES_PER_FRAME = 512
# Bytes that may arrive without EVER completing a command before the channel
# gives up on itself. A real operator types a line within a few dozen bytes and
# even a harness completes one inside a couple of chunk lines; sixteen
# kilobytes of newline-free traffic is a byte SOURCE, not a person. Rather
# than spend a slice of every frame chewing it forever, the channel disarms and
# says so once -- turning a permanent drag on the desktop into one serial line
# naming the condition. Re-arm by re-entering run_desktop.
SERIAL_NOISE_LIMIT = 16384


def _remote_state(ws):
    """One-line JSON snapshot for the `state` command -- the assertion source an
    on-glass harness reads instead of pixels. Every field best-effort: a broken
    subsystem reads as an error string, never a crash that kills the loop.

    ONE snapshot for every tier: the windowed fields (desk/order/focus/wins)
    appear when the WM has windows (`_wins`), the fullscreen back-stack when it
    has one (`_stack`). tests/test_p4_on_glass.py is the shape's consumer of
    record -- its assertions read these exact keys."""
    st = {}
    try:
        st["screen"] = ws.screen
        st["frames"] = getattr(ws, "_frames_drawn", None)
        st["cart"] = (getattr(ws, "cart", None) or {}).get("title")
        st["cart_error"] = getattr(ws, "cart_error", None)
        st["diag"] = bool(getattr(ws, "diag_live", False))
        # Idle screen blank: the harness has to be able to tell a blanked panel
        # from a hung one -- they look identical from the host end.
        st["psave"] = [bool(getattr(ws, "_psave_asleep", False)),
                       int(getattr(ws, "_psave_ms", 0) or 0) // 1000]
        # Expensive-event counters (ws.note_cost): cache builds + storage
        # reads. A cache that is silently missing shows up here as a count that
        # tracks the frame count.
        st["costs"] = dict(getattr(ws, "costs", {}) or {})
    except Exception as exc:  # noqa: BLE001
        st["ws_err"] = str(exc)
    try:
        wm = ws.wm
        if hasattr(wm, "_stack"):
            # The process back-stack, which on the fullscreen tier IS the whole
            # window model: `ws.screen` is a read-only projection of its top.
            st["stack"] = list(wm._stack or ())
        if hasattr(wm, "_wins"):
            # The windowed tier's window model (#73/#105).
            desk = getattr(wm, "desk_open", None)
            st["desk"] = bool(desk()) if desk is not None else None
            st["order"] = list(getattr(wm, "_order", ()) or ())
            st["focus"] = getattr(wm, "_focus", None)
            wins = {}
            for k in st["order"]:
                win = wm._wins.get(k)
                if win is not None:
                    wins[k] = [win.x, win.y, win.w, win.h, win.title_h,
                               win.kind, bool(win.minimized)]
            st["wins"] = wins
            # #113: a window buffer is ONE retained surface -- 2 here means a
            # blit-scroll would measure against the wrong paint and ghost.
            st["win_retained"] = dict(
                (k, getattr(wm._wins[k].buf, "RETAINED_FRAMES", None))
                for k in st["order"]
                if wm._wins.get(k) is not None and wm._wins[k].buf is not None)
    except Exception as exc:  # noqa: BLE001
        st["wm_err"] = str(exc)
    try:
        sl = ws.settings_layer
        sr = sl.scroll
        st["settings"] = {
            "set_top": sl.set_top, "sel": sl.set_msel,
            "rows": len(sl._settings_rows()),
            "offset": None if sr is None else sr.offset,
            "view": None if sr is None else list(sr.view),
            "content": None if sr is None else sr.content,
            "wifi_view": bool(sl.wifi_view),
            "bt_view": bool(getattr(sl, "bt_view", False)),
        }
        win = ws.wm._wins.get("settings") if hasattr(ws.wm, "_wins") else None
        if win is not None and win.ctx is not None:
            lay = win.ctx.layout
            st["settings"]["lay"] = [lay.set_x, lay.set_row_y0,
                                     lay.set_w, lay.set_row_h]
    except Exception as exc:  # noqa: BLE001
        st["settings_err"] = str(exc)
    try:
        st["wifi"] = list(ws.wifi.status()) if ws.wifi is not None else None
    except Exception as exc:  # noqa: BLE001
        st["wifi_err"] = str(exc)
    try:
        # Look system-app carts up by TITLE, never folder name: the device seeds
        # from the title slug and the host store copies the source folder, and
        # assuming either name is what broke `is_app` on the P4's glass.
        app = getattr(ws, "appearance_app", None)
        cart = None
        for c in ws._all_carts:
            if c.get("title") == "Appearance":
                cart = c
                break
        if cart is None:
            st["appearance_cart"] = None
        else:
            st["appearance_cart"] = {
                "title": cart.get("title"), "version": cart.get("version"),
                "path": cart.get("path"),
                "perms": list(cart.get("permissions") or ()),
                "is_app": bool(app.is_app(cart)) if app is not None else None,
            }
        claims = {}
        for _app, _text in getattr(ws, "_apps", ()):
            claims[_app.id] = sum(1 for c in ws._all_carts if _app.is_app(c))
        st["app_claims"] = claims
    except Exception as exc:  # noqa: BLE001
        st["app_err"] = str(exc)
    return st


class DevChannel:
    """Line commands over USB-CDC stdin, read one byte at a time.

    See SERIAL_CMDS above for why this exists at all and why the byte-at-a-time
    reader is not fussiness. In one sentence: `poll()` is trustworthy (the esp32
    port sets MP_STREAM_POLL_RD only when the stdin ring buffer is non-empty),
    `readline()` is not (it blocks per character until a newline that noise will
    never supply), so this reads exactly the bytes poll promised and no more.

    ONE vocabulary, every board. A command that needs a capability the board
    lacks says so ("no window open", "no backlight control") instead of not
    existing -- a declined verb is greppable, a missing one is a mystery.
    `tools/p4_autotest.py`'s approach -- drive the console over serial, assert
    against `state` -- points at this directly.

      state           one-line JSON snapshot (_remote_state above)
      tap <x> <y>     a synthetic tap at canvas coords
      tap <name>      tap a named bar button (any ws.layout.<name>_btn rect)
      run [name]      select the first cart whose title matches, and run it
      open <what>     settings|picker|appearance|wifi -- deterministic app open
      swipe x0 y0 x1 y1 [frames]   a touch gesture through the real pointer
                      feed: press edge, held interpolation, real release
      drag [frames] [step]         grab the TOP window's title strip and
                      oscillate it (windowed tier; declines with no window)
      diag 0|1        the diagnostic frame-eaters (perf_capture + the FPS chip)
      skip 0|1        the #77 frameskip gate
      gov 0|1         the #63 frame governor
      mem             a forced collect + the live/free split
      bl 0|1          panel backlight. The board keeps RENDERING either way, so
                      a dark screen is a fine way to bench unattended.
      vol <0-7>       master audio level; 0 is silent
      power <secs>    idle screen-blank timeout (0 disables); `power off` blanks
                      now, `power on` wakes now. The board keeps RENDERING while
                      dark, so an unattended bench run still produces frames.
      web [start]     serve the wasm console from this board (ws.webhost)
      py <code>       eval/exec one line against the LIVE console
      quit            leave the desktop for the REPL

    BOARD BITS ARE INJECTED: `set_backlight`, `idle` (an IdleBlank), `env`
    (extra names for `py` -- comp/game/boot/pump on the P4), and `extra`, a
    {name: handler(ws, parts, line)} dict of board-only commands (the P4's
    `bt`/`union`/`cache`). Extras dispatch AFTER the built-ins and cannot
    shadow them -- one vocabulary is the point.
    """

    def __init__(self, ws, pointer, set_backlight=None, idle=None,
                 extra=None, env=None):
        self.pointer = pointer
        self.click = False
        self.quit = False       # `quit` asked for the REPL; run_desktop returns
        self.buf = ""
        self.rx = 0             # bytes swallowed -- the "is something injecting?" number
        self.lines = 0          # complete commands dispatched
        self.dropped = 0        # over-long partial lines thrown away
        self.idle = idle        # an IdleBlank, for `power`; may be None
        self.set_backlight = set_backlight   # board's panel light; may be None
        self.extra = extra or {}             # board-only commands
        self.env = env or {}                 # extra names in the `py` scope
        self._drag = None       # `drag` playback state
        self._swipe = None      # `swipe` playback state
        self.armed = False
        self._poll = None
        self._stdin = None
        try:
            import select
            import sys
            self._stdin = sys.stdin
            self._poll = select.poll()
            # POLLIN and nothing else. A bare register() defaults to RD|WR, and
            # mphalport.c grants POLL_WR unconditionally -- so a bare
            # registration is truthy on EVERY call, forever, which looks exactly
            # like "poll reports stdin always-ready".
            self._poll.register(self._stdin, select.POLLIN)
            self.armed = True
        except Exception as exc:  # noqa: BLE001 -- the channel is optional sugar
            print("Moybyte serial channel unavailable:", exc)

    def poll(self, ws):
        """Drain up to SERIAL_BYTES_PER_FRAME bytes and run any complete lines.

        Returns True when a command ran (the caller treats that as activity).
        The drain is BOUNDED so that a stuck byte source costs a fixed slice of
        one frame rather than the frame.
        """
        if not self.armed:
            return False
        self.click = False
        ran = False
        for _ in range(SERIAL_BYTES_PER_FRAME):
            if not self._poll.poll(0):
                break
            try:
                ch = self._stdin.read(1)
            except Exception:  # noqa: BLE001 -- a dead stdin disarms the channel
                self.armed = False
                return ran
            if not ch:
                break
            self.rx += 1
            if ch in ("\n", "\r"):
                line = self.buf.strip()
                self.buf = ""
                if line:
                    self.lines += 1
                    ran = True
                    try:
                        self.run(ws, line)
                    except Exception as exc:  # noqa: BLE001 -- never kill the loop
                        print("REMOTE ERR %s: %s" % (type(exc).__name__, exc))
            else:
                self.buf += ch
                if len(self.buf) > SERIAL_LINE_MAX:
                    # Not a command -- a byte source with no newline in it. Drop
                    # the partial rather than growing a string forever.
                    self.dropped += 1
                    self.buf = ""
        if self.lines == 0 and self.rx >= SERIAL_NOISE_LIMIT:
            # Kilobytes in, not one command out. That is a byte SOURCE (UART0's
            # ISR shares this ring buffer -- a floating U0RXD reads exactly like
            # this), and chewing it costs a slice of every frame forever. Stop,
            # once, out loud: a named condition beats a permanent slow desktop.
            self.armed = False
            print("Moybyte serial channel DISARMED: %d bytes arrived and not one "
                  "complete command. Something is injecting into stdin -- most "
                  "likely UART0 (U0RXD/GPIO44 floats on the expansion header) "
                  "feeding the same ring buffer. Rebuild with "
                  "MICROPY_HW_ENABLE_UART_REPL (0) to take its ISR off it."
                  % self.rx)
        return self._scripts() or ran

    def _scripts(self):
        """Advance the swipe/drag playbacks: one pointer sample per frame,
        through the SAME pointer the glass feeds. Runs at the poll tail, i.e.
        after the board's touch poll, so a scripted sample outranks whatever
        the touch driver left behind (`fresh=True` is load-bearing for the
        kinetic-scroll velocity estimator). Returns True while one is active,
        which the caller counts as activity."""
        ran = False
        if self._drag is not None:
            s = self._drag
            i = s["i"]
            if i >= s["n"]:
                self.pointer.down = False
                self._drag = None
                print("REMOTE drag done")
            else:
                # Triangle wave around the grab point: continuous movement so
                # the drag stays engaged and every frame is dirty.
                t = i % 40
                tri = t if t < 20 else 40 - t          # 0..20..0
                off = 0 if i == 0 else (tri - 10) * s["step"]
                self.pointer.place(s["cx"] + off, s["cy"])
                self.pointer.down = True
                self.pointer.fresh = True
                if i == 0:
                    self.click = True                  # frame 0 arms the drag
                s["i"] = i + 1
            ran = True
        if self._swipe is not None:
            s = self._swipe
            i = s["i"]
            n = s["n"]
            if i > n:
                self._swipe = None
                print("REMOTE swipe done")
            else:
                # i==0 press edge at the start, i in 1..n-1 held interpolation,
                # i==n the release sample at the end point (down=False so the
                # gesture machines see a real release, fling velocity intact).
                f = min(i, n - 1) / (n - 1)
                x = s["x0"] + int((s["x1"] - s["x0"]) * f)
                y = s["y0"] + int((s["y1"] - s["y0"]) * f)
                self.pointer.place(x, y)
                self.pointer.down = i < n
                self.pointer.fresh = True
                if i == 0:
                    self.click = True
                s["i"] = i + 1
            ran = True
        return ran

    def report(self, diag):
        """One SERIAL line per diag tick, and it is the channel's self-diagnosis:
        `rx` climbing while `lines` stays 0 means something is injecting bytes
        into stdin that are not commands -- UART0's ISR shares this ring buffer,
        so a floating U0RXD (GPIO44, on the expansion header) reads exactly like
        this. That is a fact, printed, instead of a hang to be puzzled over."""
        _diag_log("SERIAL", "rx=%d lines=%d dropped=%d partial=%d"
                  % (self.rx, self.lines, self.dropped, len(self.buf)), diag)

    def run(self, ws, line):
        parts = line.split()
        cmd = parts[0]
        if cmd == "quit":
            # A FLAG, not a raised KeyboardInterrupt. MicroPython derives that
            # one from BaseException, so it would sail past every `except
            # Exception` between here and the top -- including the frame's --
            # and leave the panel mid-flush. run_desktop returns cleanly instead.
            print("REMOTE quit -> REPL")
            self.quit = True
            return
        if cmd == "state":
            import json
            print("STATE %s" % json.dumps(_remote_state(ws)))
            return
        if cmd == "tap":
            r = None
            if len(parts) == 3:
                try:
                    r = (int(parts[1]), int(parts[2]))
                except ValueError:
                    r = None
            elif len(parts) == 2:
                rect = getattr(ws.layout, parts[1] + "_btn", None)
                if rect:
                    r = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
            if r is None:
                print("REMOTE ? %s" % line)
                return
            self.pointer.place(r[0], r[1])
            self.pointer.down = True     # released next frame (touch reads None)
            self.click = True
            print("REMOTE tap %d %d" % r)
            return
        if cmd == "run":
            name = (" ".join(parts[1:])).lower() if len(parts) > 1 else ""
            items = getattr(ws.launcher, "items", [])
            for i in range(len(items)):
                it = items[i]
                if not it.get("path"):
                    continue
                if not name or name in str(it.get("title") or "").lower():
                    ws.launcher.sel = i
                    ws.launch_selected()
                    print("REMOTE run %s" % it.get("title"))
                    return
            print("REMOTE run: no cart match")
            return
        if cmd == "diag":
            on = not (len(parts) == 2 and parts[1] == "0")
            # Through set_diag_live, not around it: the 3s diag tick re-syncs
            # perf_capture FROM diag_live, so poking perf_capture alone would be
            # silently undone. persist=False -- a serial A/B must not rewrite the
            # kid's system.json.
            try:
                ws.set_diag_live(on, persist=False)
            except Exception:  # noqa: BLE001 -- older console: flag only
                ws.diag_live = on
            ws.perf_capture = on
            ws.show_fps = on
            ws._dirty = True
            print("REMOTE diag %s" % ("on" if on else "off"))
            return
        if cmd == "skip":
            on = not (len(parts) == 2 and parts[1] == "0")
            ws.set_frameskip(on, persist=False)
            print("REMOTE skip %s" % ("on" if on else "off"))
            return
        if cmd == "gov":
            on = not (len(parts) == 2 and parts[1] == "0")
            import console as _console_mod
            _console_mod.FPS_GOVERNOR = on
            print("REMOTE gov %s" % ("on" if on else "off"))
            return
        if cmd == "mem":
            import gc
            gc.collect()
            print("REMOTE mem live=%dk free=%dk"
                  % (gc.mem_alloc() // 1024, gc.mem_free() // 1024))
            return
        if cmd == "bl":
            on = not (len(parts) == 2 and parts[1] == "0")
            if self.set_backlight is None:
                print("REMOTE bl: no backlight control on this board")
                return
            self.set_backlight(on)
            # Keep IdleBlank's model honest, or `power` reports asleep=True over
            # a lit panel and the next idle tick declines to blank it.
            if self.idle is not None:
                if on:
                    self.idle.wake(_ticks_ms())
                else:
                    self.idle.asleep = True
            print("REMOTE bl %s" % ("on" if on else "off"))
            return
        if cmd == "vol":
            lvl = int(parts[1]) if len(parts) == 2 else 0
            # ws.audio exists only while a cart holds the backend -- at the
            # launcher it is None, which is not an error worth a traceback.
            au = getattr(ws, "audio", None)
            if au is None:
                print("REMOTE vol: no audio backend (no cart running)")
                return
            au.volume(lvl)
            print("REMOTE vol %d" % lvl)
            return
        if cmd == "power":
            # Act on the IdleBlank DIRECTLY rather than parking a request for the
            # loop to apply. The deferred version reported the value it had not
            # applied yet, so `power 0` answered "timeout=8s" -- twice, in two
            # different shapes, before the plumbing itself was the bug.
            idle = self.idle
            if idle is None:
                print("REMOTE power: no idle blank on this build")
                return
            if len(parts) == 2 and parts[1] == "off":
                idle.blank()
                print("REMOTE power off")
                return
            if len(parts) == 2 and parts[1] == "on":
                idle.wake(_ticks_ms())
                ws._psave_asleep = False
                ws._dirty = True
            elif len(parts) == 2:
                idle.timeout_ms = int(parts[1]) * 1000
                # Mirror onto the ws so `state`'s psave field reports the LIVE
                # timeout, not the boot default.
                ws._psave_ms = idle.timeout_ms
                idle.wake(_ticks_ms())
            print("REMOTE power timeout=%ds asleep=%s"
                  % (idle.timeout_ms // 1000, idle.asleep))
            return
        if cmd == "open":
            # Deterministic app open (no tile-hunting), so a drag/scroll can be
            # measured against a known surface. `open appearance` reports
            # open_app's claim result -- a False is the silent no-op an on-glass
            # harness needs to SEE; `open wifi` deep-links Settings -> wifi.
            what = parts[1] if len(parts) > 1 else ""
            if what == "appearance" and getattr(ws, "appearance_app", None) is not None:
                ok = ws.open_app(ws.appearance_app)
                print("REMOTE open appearance -> %s" % ok)
                return
            if what == "wifi" and hasattr(ws, "open_settings"):
                ws.open_settings()
                ws.settings_layer.open_wifi()
                print("REMOTE open wifi")
                return
            fn = {"settings": getattr(ws, "open_settings", None),
                  "picker": getattr(ws, "open_picker", None)}.get(what)
            if fn is not None:
                fn()
                print("REMOTE open %s" % what)
            else:
                print("REMOTE open ? %s" % line)
            return
        if cmd == "swipe" and len(parts) >= 5:
            # A synthetic touch gesture fed through the SAME pointer path as
            # the glass, so the harness can exercise scroll/drag/fling on any
            # surface. Playback is per-frame in _scripts().
            try:
                self._swipe = {"i": 0,
                               "x0": int(parts[1]), "y0": int(parts[2]),
                               "x1": int(parts[3]), "y1": int(parts[4]),
                               "n": max(2, int(parts[5]))
                               if len(parts) > 5 else 20}
                print("REMOTE swipe %d,%d -> %d,%d frames=%d"
                      % (self._swipe["x0"], self._swipe["y0"],
                         self._swipe["x1"], self._swipe["y1"],
                         self._swipe["n"]))
            except ValueError:
                self._swipe = None
                print("REMOTE swipe ? %s" % line)
            return
        if cmd == "drag":
            # Grab the TOP window's title strip and oscillate it for n frames,
            # so the PERF sampler reports DRAG-time fps. Windowed tier only --
            # a WM without windows declines, it does not traceback.
            order = getattr(ws.wm, "_order", None) or []
            if not order:
                print("REMOTE drag: no window open")
                return
            win = ws.wm._wins[order[-1]]
            n = 120
            step = 6
            if len(parts) >= 2:
                try:
                    n = max(8, int(parts[1]))
                except ValueError:
                    pass
            if len(parts) >= 3:
                try:
                    step = max(1, int(parts[2]))  # px/frame amplitude scale
                except ValueError:
                    pass
            self._drag = {"i": 0, "n": n, "step": step,
                          "cx": win.x + 30,
                          "cy": win.y + max(6, win.title_h // 2)}
            print("REMOTE drag win=%s cx=%d cy=%d frames=%d step=%d"
                  % (order[-1], self._drag["cx"], self._drag["cy"], n, step))
            return
        if cmd == "web":
            # Serve the wasm console FROM this board (moy_webhost). A dev
            # command rather than only a Settings row because the endpoint is
            # unsecured -- see moy_webhost's module docstring.
            try:
                wh = getattr(ws, "webhost", None)
                if wh is None:
                    print("WEB no service")
                else:
                    if not wh.serving:
                        ws.toggle_webhost()
                    print("WEB %s %s" % (wh.url(), wh.error or ""))
            except Exception as exc:  # noqa: BLE001
                print("WEB ERR %s: %s" % (type(exc).__name__, exc))
            return
        if cmd == "py" and len(parts) > 1:
            code = line.split(None, 1)[1]
            env = {"ws": ws, "wm": ws.wm, "pointer": self.pointer}
            env.update(self.env)
            try:
                try:
                    print("PY %r" % (eval(code, env),))
                except SyntaxError:
                    exec(code, env)       # noqa: S102 -- dev-board serial only
                    print("PY ok")
            except Exception as exc:  # noqa: BLE001
                print("PY ERR %s: %s" % (type(exc).__name__, exc))
            return
        handler = self.extra.get(cmd)
        if handler is not None:
            handler(ws, parts, line)
            return
        print("REMOTE ? %s" % line)

