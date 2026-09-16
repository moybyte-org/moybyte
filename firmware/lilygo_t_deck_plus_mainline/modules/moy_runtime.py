"""Moybyte T-Deck device backend -- the shared console on the S3.

The `run_desktop` that had the least to invent: the shared boot spine
(`device/desktop_spine.py` over `runtime/device_boot.py`) owns the splash,
the cart seed/scan, the Lua probe, the service wiring, the OTA verdict and
the frame loop, and the shared `console.Workstation` owns every pixel. What is
left here is the part that is genuinely this board's hardware.

This port replaced the lvgl_micropython fork build of the same glass (deleted
2026-08-17). What structurally changed with it: the panel machine is
`native/moy_lcd`, one C module that also owns the SPI host, with
`tdeck_panel.TDeckCompositor` the thin ping-pong over its kick/pump/drain
split (the fork's Python `moy_compositor` + `lcd_bus` banding died with it,
strategy retained -- `flush()` queues the first bands and returns, the rest
are fed while this loop renders the next frame, `comp.sync()` is a real
fence); there is no LVGL; and the SERIAL DEV CHANNEL works, which on the fork
it never did.

Everything the console is built from -- input drivers, SD lifecycle, cart API,
audio backend, WiFi service, OTA updater -- is staged from the shared
`runtime/` and `device/` trees by `board.toml`: one console, per-board hardware
underneath.
"""

from console import _cursor_delta
from desktop_spine import build_desktop
from device_boot import apply_touch
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_util import _ticks_ms, _ticks_diff, _diag_note, _diag_log
from device_input import TrackBall, Touch
from device_audio import make_audio
from device_canvas import DeviceCanvas
from device_diag import (_diag_flush, _diag_hitch,
                         _diag_drawbrk, _diag_draw2, _diag_loop, _diag_i2cstat, _diag_webhost,
                         _diag_pump, HITCH_MS)

# --- #69 the input-poller thread ---------------------------------------------
#
# Every I2C0 transaction (keyboard + GT911 + mode switches) moves to a dedicated
# Python thread; the frame loop only consumes staged state. A C3 clock-stretch
# stall then blocks the poller instead of a frame. Requires the build's
# GIL-release patch to bite -- stage 3's `tdeck_smoke.keyboard()` is the on-glass
# A/B that says whether it is in this image. False (or no `_thread`, or a dead
# thread) falls back to synchronous polling with no rebuild.
MOY_INPUT_POLLER = True

# --- the serial dev channel ---------------------------------------------------
#
# ONE implementation for every board: `dev_channel.DevChannel` (staged from
# runtime/), which reads stdin one byte at a time after poll(0) -- NEVER
# readline, which blocks per character -- and disarms itself, out loud, if
# kilobytes arrive without a single complete command. Its module docstring
# carries the reader's design; what is THIS board's alone is mechanism 2 of the
# original stall: `MICROPY_HW_ENABLE_UART_REPL` would put UART0's ISR on the
# same stdin ring buffer, so noise on the floating U0RXD (GPIO44, exposed on
# the expansion header) reads exactly like typed input. The board header keeps
# UART_REPL off (#201); if `SERIAL rx=` ever climbs on an idle board, that is
# the mechanism to suspect. (The full history of why RX was thought impossible
# here -- and why the fork's never worked -- is in CLAUDE.md's RX section and
# git history at 4faf07a/24ccb0b.)
#
# Set False to remove the channel entirely (the loop is then byte-identical to
# one without it).
SERIAL_CMDS = True

# Idle screen blank (shared with the P4 via device_boot.IdleBlank). Overridable
# before boot (`import moy_runtime; moy_runtime.POWER_SAVE_MS = ...`) and at
# runtime over the dev channel (`power <secs>`, `power off`). Same 5 minutes the
# P4 ships, so the two boards behave alike unless a board has a reason not to.
POWER_SAVE_MS = 300000          # 5 minutes; 0 disables

# #183: print a phase bracket around every SD session. This board has no REPL to
# interrogate once the desktop owns the loop, so the trace IS the diagnostic --
# and it only fires on commits, never per frame.
SD_TRACE = True

# WHERE THE STORE LIVES WHEN THERE IS NO CARD. This board's carts normally live
# on the TF card (moy_carts.CARTS_DIR, /sd/moybyte/carts) -- but a T-Deck with
# an empty slot used to boot to the EMBEDDED carts with a None root, which
# `wire_workstation_core` turns into can_manage=False: a read-only console, no
# saving, no editing, no importing, and the only explanation one serial line on
# a board whose USB-CDC RX is dead under the desktop. These are the same paths
# the P4 has always used for the same reason, and a card put in later is picked
# up on the next boot -- ONE store per boot, decided before anything is written,
# because two live stores is the arrangement where a kid's save goes to the one
# they are not looking at.
FLASH_CARTS_ROOT = "/moy/carts"
FLASH_UPDATE_DIR = "/moy/update"


class _Storage:
    """This boot's store -- the TF card when it takes the store, internal
    flash when it does not -- and the SESSION every store write goes through.

    Every SD session on this board is drained first and the panel SERIALIZED
    for the session's whole span (comp.sd_bracket -> moy_lcd's sd_guard). The
    bracket exists because the seed/scan PAINTS a progress frame per cart
    INSIDE the session, and since the core-0 feeder (2026-08-21) an
    unbracketed paint queues panel bands from core 0 while the VM sits inside
    an sdspi transaction on the same SPI host -- measured as a Cache/MMU panic
    at "loading cartridges 1/35". `comp.sync()` is load-bearing now that the
    flush overlaps: a frame's bands can still be in flight, and an SD op that
    overlaps a panel DMA on the shared host is the documented way to hang
    this board.

    The TRACE is the diagnostic (#183). An editor commit can wedge this board
    with nothing on serial, so each phase says its name and whichever line is
    LAST before the silence identifies the op:
      "SD > sync" -- the pre-op drain
      "SD > op"   -- the SD write itself
      "SD < op"   -- the NEXT PANEL FLUSH, i.e. the shared-bus corruption;
                     "SD = panel ok" (the frame tail) is what says it did not
                     happen.
    Costs nothing when quiet: SD sessions happen on commits, not per frame.
    """

    def __init__(self, comp):
        self._comp = comp
        self.on_sd = False      # did the card take the store this boot
        self.traced = False     # a traced session awaits its "panel ok"

    def _bracketed(self, fn, trace=False):
        if trace:
            print("SD > sync")
        t = _ticks_ms()
        self._comp.sync()
        if trace:
            print("SD > op (sync %dms)" % _ticks_diff(_ticks_ms(), t))
            t = _ticks_ms()
        bracket = getattr(self._comp, "sd_bracket", None)
        if bracket is not None:
            bracket(True)
        try:
            import moybyte_sd
            return moybyte_sd.with_sd_live(fn)
        finally:
            if bracket is not None:
                bracket(False)
            if trace:
                print("SD < op %dms" % _ticks_diff(_ticks_ms(), t))
                self.traced = True

    def load(self, boot, store):
        """The cart store: seed + scan on the card, bracketed (`with_sd_live`
        attaches once and keeps the card resident); internal flash when there
        is no card. The OTA image stages wherever the store went -- an updater
        aimed at /sd/update on a card-less board would stage onto a card that
        is not there."""
        carts, root = boot.load_carts(store, CARTS, session=self._bracketed,
                                      media="SD",
                                      fallback_root=FLASH_CARTS_ROOT)
        self.on_sd = root is not None and root.startswith("/sd")
        return carts, root, (None if self.on_sd else FLASH_UPDATE_DIR)

    def session(self, fn):
        """Every store op: the bracket while the card is the store; a plain
        call while internal flash is -- it shares no bus with the panel, and
        routing it through the bracket would fail every write."""
        if not self.on_sd:
            return fn()
        return self._bracketed(fn, SD_TRACE)


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts + keyboard + touch, carts on SD.

    The boot order and the service set are the shared spine's; what is here is
    the panel bring-up, the input trio and its poller thread, the SD/panel bus
    gate, and the diag ticks this board's offline ring records.
    """
    import tdeck_panel
    from tdeck_panel import TDeckCompositor, set_backlight
    from moybyte.input import InputState, TDeckKeyboard, InputPoller

    # #54 St.2: arm the async layer copy BEFORE the first canvas exists.
    # `DeviceCanvas` latches `_async_ok` in __init__, so this has to precede the
    # construction below or it reaches nothing. It is an assignment rather than
    # an edit because `device_canvas.py` is staged from the shared `device/`
    # tree and is not this board's to change; the flag lives in the compositor
    # module (`tdeck_panel`) because the fact it rests on is the compositor's.
    # Read the block beside the constant for why the 2026-07-03 verdict against
    # this lever does not apply to `moy_lcd`, and for which carts it can and
    # cannot move.
    import device_canvas
    device_canvas.LAYER_COPY_ASYNC = tdeck_panel.LAYER_COPY_ASYNC

    comp = TDeckCompositor(nfbs=2)
    canvas = DeviceCanvas(comp)
    try:
        import moybyte_diag as diag
    except Exception:  # noqa: BLE001
        diag = None

    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    touch = None
    poller = None

    def _inputs():
        """The GT911 and the #69 poller thread, behind the splash. The touch
        shares the keyboard's I2C object: one bus, one driver instance, so the
        poller owns every transaction on it."""
        nonlocal touch, poller
        touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
        if MOY_INPUT_POLLER:
            try:
                _p = InputPoller(keyboard, touch)
                if _p.start():
                    poller = _p
                    keyboard._poller_owned = True
                    touch._source = poller.consume_touch
                    _diag_note("input", "poller thread running (#69, %dms cadence)"
                               % poller.period)
            except Exception as exc:  # noqa: BLE001 -- input must never fail closed
                _diag_note("input", "poller setup failed: %s" % (exc,))
                poller = None
        return touch

    # BLE HID keyboard (#26): a SECOND, optional input source on this board.
    # On the touch-only boards a paired BLE keyboard is the only keyboard and
    # becomes ws.keyboard outright; here the physical C3 keyboard keeps that
    # slot and the BLE driver hangs off ws.ble_keyboard. Both write into the
    # SAME InputState, so nothing in the shared console needs to know which
    # one a keypress came from.
    #
    # auto_start=False deliberately: scanning is what makes BLE expensive, and
    # a board whose keyboard already works should not pay for a radio nobody
    # asked for. Settings starts it when the kid opens the panel.
    #
    # The bond store is on the INTERNAL VFS, not beside the carts the way the
    # touch-only boards do it: this board's carts live on SD, whose writes have
    # to go through the with_sd_live gate, and a pairing that fails because a
    # card is missing would be a bad first experience for a feature whose whole
    # point is "my keyboard works now".
    _ble = None
    try:
        from ble_keyboard import BleHidKeyboard
        _ble = BleHidKeyboard(inp, store_path="/ble_keyboard.json",
                              auto_start=False)
    except Exception as exc:  # noqa: BLE001 -- a build without the module, or no radio
        print("Moybyte: BLE keyboard unavailable:", exc)

    store = _Storage(comp)

    def _before_slim(_ws):
        # Set BEFORE slim_carts so the store can reload what the diet drops.
        _ws._with_sd = store.session

    def _after_services(ws):
        _diag_log("boot", "desktop running kb=%d ball=%d touch=%d poller=%d"
                  % (1 if keyboard.available else 0, 1 if ball.available else 0,
                     1 if touch.available else 0, 1 if poller is not None else 0),
                  diag)
        # #66/#67 SRAM diet: everything needing boot-time internal RAM has taken
        # it by here, so the Lua allocator's headroom floor drops 48->24KB. BOTH
        # runtimes -- moycore has its own allocator with its own floor, and a
        # cart left on the 48KB floor sits at ~97% PSRAM, the measured-2x-slower
        # regime, with nothing saying so.
        for _mod in ("moy_lua", "moycore"):
            try:
                _m = __import__(_mod)
                _fl = getattr(_m, "set_sram_floor", None)
                if _fl is not None:
                    _diag_log("boot", "%s sram floor=%dKB" % (_mod, _fl(24)), diag)
            except Exception:  # noqa: BLE001
                pass

    def _perf_emit(line):
        """TWO SINKS, ONE LINE (#206 item 2).

        PRINTED like every other board -- until 2026-08-28 this board's samples
        went only through the diag ring, whose `Moybyte <uptime> ` stamp made
        `tools/p4_perf.py` (which filters on `PERF `) drop every one of them, so
        the board whose fps needed measuring was invisible to the tool that
        measures it. And through the ring as well, uptime-stamped, because this
        board's serial RX was dead for months and the SD log is why anything was
        known about it at all -- the ring is what survives a hang.

        Ringed WITHOUT the live echo: `diag.log` would print it a second time.
        """
        print(line)
        if diag is not None:
            try:
                diag.ring("PERF", line[5:])
            except Exception:  # noqa: BLE001 -- a diag never breaks a frame
                pass

    d = build_desktop("Moybyte", "tdeck", comp, canvas, set_backlight, inp,
                      inputs=_inputs, keyboard=keyboard, seed_carts=CARTS,
                      power_save_ms=POWER_SAVE_MS,
                      load_carts=store.load, with_sd=store.session,
                      before_slim=_before_slim, after_services=_after_services,
                      make_audio=make_audio, ble_keyboard=_ble,
                      serial=SERIAL_CMDS, perf_emit=_perf_emit,
                      log=lambda tag, msg: _diag_log(tag, msg, diag),
                      fps_cap=fps_cap)
    ws = d.ws
    pointer = d.pointer
    serial = d.serial
    perf_account = d.perf.account

    # Per-frame phase costs the diag lines read. Mutable containers because the
    # hooks below are CLOSURES over this scope (the FrameLoop owns the order,
    # this board owns the hardware inside each hook -- #202 Phase B).
    _diag_at = [_ticks_ms() + 3000]
    _flush_at = [_ticks_ms() + 5000]
    _prev_cart_err = [None]
    _cart_prev = [False]
    # [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep, hi, hp] ms per frame,
    # averaged and zeroed every diag tick. HITCH only fires on SPIKES, so a
    # steady per-frame cost that never crosses HITCH_MS is invisible without it.
    _acc = [0] * 12
    _t = {"kbd": 0, "inp": 0, "sb": 0, "diag": 0, "sd": 0, "web": 0}

    def _poll_inputs(now):
        """Every input source on this board: the #69 poller (with its death
        fallback), the keyboard, the BLE keyboard, the trackball (caret in the
        code editor, cursor everywhere else), the GT911. Returns (click,
        active) for the shared loop; the dev channel and idle blank run THERE,
        in the one order that lets the waking touch be swallowed.

        The two keyboards each write their OWN InputSource and inp.begin_frame()
        merges them, so the order they poll in carries no authority -- which is
        the whole point of the multi-source model (device/moybyte/input.py)."""
        nonlocal poller
        # If the poller thread ever dies, detach and fall back to synchronous
        # polling -- input never goes dark.
        if poller is not None and not poller.alive:
            _diag_note("input", "poller thread died -> synchronous fallback")
            keyboard._poller_owned = False
            touch._source = None
            poller = None
        try:
            if poller is not None:
                poller.consume()
            else:
                keyboard.poll()
        except Exception:  # noqa: BLE001
            pass
        # The BLE keyboard's reports arrive on a radio IRQ; poll() is what
        # turns them into held buttons + a key, and it also advances
        # scan/reconnect and flushes a new bond outside the IRQ.
        if _ble is not None:
            try:
                _ble.poll()
            except Exception as exc:  # noqa: BLE001 -- BLE must fail keyboard-only
                print("Moybyte BLE keyboard poll failed:", exc)
        _t["kbd"] = _ticks_diff(_ticks_ms(), now)
        _t0 = _ticks_ms()
        inp.begin_frame()
        counts, click = ball.poll()
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        # The ball is this board's arrow keys, so a surface holding a CARET
        # gets them and everything else gets the cursor. ws.nav owns that
        # decision (the code editor AND a cart's focused editor handle, #181)
        # and says whether it spent them.
        if not ws.nav(nx, ny):
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)
        touched, tclick = apply_touch(touch, pointer)
        if tclick:
            click = True
        _t["inp"] = _ticks_diff(_ticks_ms(), _t0)
        return click, (touched or nx or ny or click
                       or bool(getattr(inp, "last_key", None)))

    def _present():
        _t0 = _ticks_ms()
        canvas.sync_back()      # re-point at the compositor's new BACK buffer
        _t["sb"] = _ticks_diff(_ticks_ms(), _t0)

    def _frame_error(exc):
        _diag_log("frame error", exc, diag)
        print("Moybyte frame error:", exc)
        _diag_flush(diag, ws)
        import gc
        gc.collect()

    def _tail(now):
        loop = d.loop
        # #183: close the SD bracket. A DRAWN frame here means the first panel
        # flush after the SD session completed, so the bus survived it.
        if store.traced and loop.drew:
            store.traced = False
            print("SD = panel ok")

        # THE IDLE-BAND DRAIN (#40/#66). The overlapped flush RETURNS with bands
        # still queued, and `console.frame()`'s redraw gate returns BEFORE
        # comp.flush() on a frame that changes nothing. Under the 2ms pump
        # timer this drain was LOAD-BEARING (the timer's constructor was
        # allowed to fail, and without it the bottom of the screen sat stale
        # until the next repaint); since moy_lcd's core-0 feeder (2026-08-21)
        # the flush always completes without VM-side help, so this is now a
        # cheap fence -- one volatile read once the feeder is idle -- kept so
        # an idle console still GUARANTEES nothing is in flight before
        # whatever comes next (SD, sleep, serial py snippets).
        if not loop.drew:
            try:
                comp.sync()
            except Exception:  # noqa: BLE001 -- an idle tidy-up must never throw
                pass

        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _prev_cart_err[0]:
                _prev_cart_err[0] = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _prev_cart_err[0] = None

        _tnow = _ticks_ms()
        _t["diag"] = 0
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_at[0]) >= 0:
            _diag_at[0] = _tnow + 3000
            if ws.perf_capture != _live:
                ws.perf_capture = _live     # capture follows Settings -> PERF DIAG
            try:
                diag.ECHO_LIVE = _live
            except Exception:  # noqa: BLE001
                pass
            # The PERF sample rides the shared FrameLoop.account hook with the
            # other boards (#206 item 2), on their 2s cadence.
            _diag_drawbrk(diag, ws)
            # DRAWBRK says how much of the frame is `render`; this says WHICH
            # native op render is: `layer=` is the draw_layer window copy (what
            # the async layer copy is meant to take to ~0 on a full-screen-layer
            # cart), `fill=` is the cls bucket (what a colour `background()`
            # costs -- a 153,600 B PSRAM write, Brick Siege's whole `bg=`).
            _diag_draw2(diag, ws)
            _diag_loop(diag, ws, _acc)
            for _i in range(12):
                _acc[_i] = 0
            # #66 lever 4: the bounce-feed pacing of the flush overlap -- the ONE
            # line that says whether a disappointing fps is the bus or the feeder.
            # Prints nothing unless comp.bounce_flush, so a serialized build is
            # silent rather than lying.
            _diag_pump(diag, comp)
            _diag_i2cstat(diag, keyboard, touch)
            # The web console's SOCKET state: "serving but nobody connected" and
            # "never started" look identical from the outside without it.
            _diag_webhost(diag, ws)
            if serial is not None:
                serial.report(diag)
            _t["diag"] = _ticks_diff(_ticks_ms(), _tnow)

        # #68 kid mode: the periodic diag->SD write costs 80-120ms and IS a
        # felt stutter during play, so it needs PERF DIAG *and* DIAG SD LOG.
        # The cart-exit and crash flushes stay unconditional.
        _cart_now = ws.cart is not None
        _t["sd"] = 0
        if diag is not None and _cart_prev[0] and not _cart_now:
            _t["sd"] = _diag_flush(diag, ws)   # cart exited: persist the ring
        _cart_prev[0] = _cart_now
        if (diag is not None and _live and getattr(ws, "diag_sd", False)
                and _ticks_diff(_tnow, _flush_at[0]) >= 0):
            _flush_at[0] = _tnow + (20000 if ws.cart is not None else 5000)
            _t["sd"] = _diag_flush(diag, ws)

        # The shared tail: the web console (timed, so `web=` in LOOP/HITCH
        # answers "is the transfer what stalled this frame") then the radio.
        _t["web"] = d.tail(now)

    def _account(now, elapsed, sleep_ms):
        loop = d.loop
        perf_account(now, elapsed, sleep_ms)
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t["kbd"], _t["inp"], _t["sb"],
                        loop.t_ws, _t["diag"], _t["sd"], _t["web"],
                        loop.t_hi, loop.t_hp)
        # Accumulated BEFORE the sleep, so `frame` is work and `sleep` is
        # carried separately -- a paced loop must not read as a slow one.
        _acc[0] += 1
        _acc[1] += elapsed
        _acc[2] += _t["kbd"]
        _acc[3] += _t["inp"]
        _acc[4] += _t["sb"]
        _acc[5] += loop.t_ws
        _acc[6] += _t["web"]
        _acc[7] += _t["diag"]
        _acc[8] += _t["sd"]
        _acc[9] += sleep_ms
        _acc[10] += loop.t_hi
        _acc[11] += loop.t_hp

    return d.run(_poll_inputs, present=_present, tail=_tail,
                 account=_account, frame_error=_frame_error)
