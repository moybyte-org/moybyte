"""The device boot spine and frame pump -- ONE implementation, both boards (#161).

WHY THIS EXISTS. Each board used to author its own `run_desktop` boot
sequence, and the shape of that arrangement's bugs is always the same: a step
written on one board, forgotten on the other, and silent about it because
every consumer is capability-gated.

`_pace_debt` is the proof. It shipped 2026-08-10 (fd068fc) into the T-Deck's
loop only, four days before this file was written. Frameskip ships on BOTH
boards (Settings -> FRAMESKIP; the P4 also has serial `skip 0|1`), and the
pathology it fixes -- a full frame that overruns the budget, padded to cadence,
so the skip PAIR runs 83ms instead of 66 -- is a property of the pacing
arithmetic, not of a panel. Nothing pointed at the P4's absence. Nothing could:
there is no test that can see a lever one board has and the other does not when
each board writes its own loop.

WHAT IS SHARED AND WHAT IS A HOOK. The rule is the one #161 states for
`board.toml`: a difference that is REAL stays, named, with the reason recorded
beside it. The nineteen steps sort into three kinds --

  IDENTICAL (here)          the boot splash + its progress bar + the "first
                            frame in Nms" report; the cart load/seed/scan with
                            its built-in fallback; the Lua runtime probe; the
                            OTA boot verdict and the frame-loop rollback
                            confirm; the frame cadence, its debt and the sleep.

  DIFFERS BY VALUE (here,   the store root and its media word; the splash's
  as a parameter)           serial label; the backlight function; the log sink
                            (the T-Deck's goes into the offline diag ring
                            because that board has no serial RX to ask later).

  GENUINELY BOARD-SPECIFIC  the panel + canvas bring-up (esp_lcd strips vs DPI
  (stays in moy_runtime)    scan-out); input (trackball + I2C poller thread vs
                            BLE HID + GT911); the SD/panel bus gate; the
                            presentation tier install (WindowedWM); the P4's
                            serial dev channel, drag/swipe scripts and idle
                            screen blank; the T-Deck's diag ring, HITCH/LOOP
                            accounting and SRAM census; the P4's
                            `present_pending` async-PPA overlap; the T-Deck's
                            `comp.sync()` idle-band drain. None of those is a
                            missing feature on the other board -- each is a
                            different piece of hardware.

Every method below keeps each board's OBSERVABLE boot byte-for-byte: the
same serial lines in the same order, the same values, the same guards.
`tests/test_device_boot.py` executes them against fakes, pins those strings,
and pins that both boards call the steps in one order.

Nothing here imports a board module. Every board-specific object arrives as an
argument, which is what lets this file live in `runtime/` -- staged to both
boards by their `board.toml` denylists and to the wasm head by its `DENY` glob,
importable on all three (`tests/test_staging_closure.py`).
"""

try:
    from console import draw_splash
except ImportError:  # pragma: no cover - host package lane
    from runtime.console import draw_splash

try:
    from chrome import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host package lane
    from runtime.chrome import _ticks_ms, _ticks_diff


class DeviceBoot:
    """The boot sequence's shared steps, and the screen that reports them.

    One instance per boot, constructed as soon as a board has a canvas and a
    compositor. `label` is the serial prefix ("Moybyte" / "Moybyte P4") and
    `set_backlight` the board's panel-light function -- the two things that
    differ between the boards in every line this class prints.
    """

    def __init__(self, canvas, comp, set_backlight=None, label="Moybyte"):
        self.canvas = canvas
        self.comp = comp
        self.set_backlight = set_backlight
        self.label = label
        # The panel boots DARK on both boards (#45) so the ST7789's power-on
        # GRAM noise / an uninitialised DSI framebuffer never reaches the glass.
        # `lit` says a composed splash frame has already turned it on, which is
        # also what tells the caller not to re-arm the logo (see start_frames).
        self.lit = False
        self.done = False           # the desktop owns the glass; stop painting
        self._first_at = 0

    # -- the screen ----------------------------------------------------------

    def say(self, msg):
        """One serial line, board-prefixed. The T-Deck's USB-CDC RX is dead
        under the desktop, so TX is that board's ONLY channel: a status nobody
        prints is a status nobody can ask for."""
        print("%s %s" % (self.label, msg))

    def note(self, msg, frac=None):
        """Compose a boot-splash frame saying what is happening, and say it on
        the wire too.

        The panel is dark until a frame ships, which is right and which makes a
        slow boot indistinguishable from a dead board -- a FIRST boot writes
        every built-in cartridge out before anything composes (17.5s of the
        P4's 25s). So this paints the SHIPPED boot logo (console.draw_splash --
        the same picture arm_splash holds, or the machine appears to start
        twice) with a bar and a status line under it.

        `frac` given means this is a progress repaint: the bar moves, the wire
        stays quiet. `frac=None` is a STAGE and goes to serial as well.

        `canvas.sync_back()` is load-bearing, not hygiene: the canvas caches its
        framebuffer pointer and flush() rotates the back buffer (three of them
        on the P4's render-overlap triple buffer), so without it the splash
        repaints one buffer while the panel shows the others -- two frames in
        three stale, which reads as a strobe.
        """
        if self.done:
            return
        if frac is None:
            self.say("boot: " + msg)
        try:
            self.canvas.sync_back()
            draw_splash(self.canvas, frac=frac, status=msg)
            self.comp.flush()
            if not self.lit:
                # #45, and it needs a FENCE, not just an ordering. On a backend
                # whose flush overlaps (the T-Deck's banded SRAM-bounce push,
                # which returns with most of the frame still going out) the
                # backlight would come on over rows the panel has not been
                # written yet -- i.e. over the ST7789's power-on GRAM noise,
                # which is the single thing this gate exists to prevent. Once
                # per boot, on the first light only.
                _sync = getattr(self.comp, "sync", None)
                if _sync is not None:
                    _sync()
                if self.set_backlight is not None:
                    self.set_backlight(True)
                self.lit = True
        except Exception as exc:  # noqa: BLE001 -- a splash must never fail a boot
            self.say("splash unavailable: %s" % (exc,))

    def seed_progress(self, done, total, title):
        """`seed_builtins`' progress callback: one repaint per cart, one serial
        line every eighth.

        A repaint costs nothing against ~550ms of flash writes per cart
        (measured: the P4 boot stays at 25.4s), and this is the only stretch of
        the boot that knows how much of itself is left. Every eighth also goes
        to the wire, because a repaint says nothing to someone watching over
        serial -- and one line per cart would drown the boot log.
        """
        if done % 8 == 0:
            self.say("boot: loading cartridges %d/%d" % (done + 1, total))
        self.note("loading cartridges  %d/%d" % (done + 1, total),
                  frac=float(done) / total if total else 1.0)

    # -- the steps -----------------------------------------------------------

    def load_carts(self, store, seed, root=None, session=None, media="SD"):
        """Seed + scan the cart store, falling back to the embedded carts.

        Returns `(carts, carts_root)`; a None root means "management disabled",
        which is what `wire_workstation_core` turns into `can_manage=False`.

        `session` is the board's storage lifecycle wrapper -- on the T-Deck
        `moybyte_sd.with_sd_live` (SD shares the panel's SPI host, so the mount
        must bracket the whole seed+scan), on the P4 nothing at all, because the
        store is internal flash and races no one. `media` is the word that
        appears in the serial lines ("SD" / "flash").
        """
        if root is None:
            root = store.CARTS_DIR
        try:
            def _seed_and_scan():
                store.ensure_dirs(root)
                store.seed_builtins(seed, root, progress=self.seed_progress)
                return store.scan(root)

            carts = _seed_and_scan() if session is None else session(_seed_and_scan)
            if carts:
                self.say("loaded %d carts from %s" % (len(carts), media))
                return carts, root
        except Exception as exc:  # noqa: BLE001 -- any store failure degrades
            self.say("%s carts unavailable: %s" % (media, exc))
        self.say("using built-in carts")
        return [dict(c) for c in seed], None

    def lua_runtime(self, ws, log=None):
        """The #67 Lua cart runtime, and a line saying whether it is in this image.

        ONE runtime and no chooser (2026-08-13): moycore runs the cart's whole
        frame inside libmoy -- `_update` and `_draw` back to back in C, one
        upcall per frame instead of hundreds -- and moybyte's superset verbs
        ride it as registered trampolines. A build without the module returns
        None and a `"runtime": "lua"` cart opens the Player's runtime-missing
        panel, which is the same graceful floor a build without a Lua VM always
        had.

        `log` defaults to the boot's own serial line; the T-Deck passes its diag
        sink so the answer also lands in the offline ring.
        """
        rt = None
        try:
            from moycore_glue import make_moycore_runtime
            rt = make_moycore_runtime(ws)
        except ImportError:
            pass
        (log or self.say)("lua runtime %s"
                          % ("ON (moycore)" if rt is not None else "ABSENT"))
        return rt

    def start_frames(self, ws):
        """The last boot step: say the desktop is about to paint, start the
        first-frame clock, and arm the logo only if the splash never came up.

        NOT a second logo. `arm_splash` holds the boot picture for a beat once
        the desktop is ready, which is right on a board that boots straight into
        it -- but this splash has held that exact picture for the whole boot, so
        arming it again would replay the splash and delay the launcher. Armed
        only when the splash's own draw failed, the one case where the logo
        would otherwise go unseen.
        """
        self.note("drawing the first frame")
        self._first_at = _ticks_ms()
        if not self.lit:
            ws.arm_splash()

    def first_frame(self, ws):
        """Hand the glass over, once, and say how long the desktop took to
        reach it -- the number that was missing when a black screen had to be
        diagnosed by guesswork. Returns True on the frame it fires."""
        if self.done or getattr(ws, "_frames_drawn", 0) <= 0:
            return False
        self.done = True
        self.say("first frame in %dms" % _ticks_diff(_ticks_ms(), self._first_at))
        return True


class OtaHealth:
    """"Did the update work?" -- the two halves of it (#53).

    The boot VERDICT (`boot_check`) is read on the boot path, before anything
    can overwrite the evidence. The rollback CONFIRM (`tick`) is NOT: reaching
    the end of the boot path proves only that the desktop was CONSTRUCTED, and
    an image that never paints a pixel has shipped once already (#56). So the
    confirm is fired from the frame loop, where `confirm_when_healthy` counts
    real painted frames and surviving loop iterations before it certifies the
    image. Cheap (an int compare) and self-disarming after it fires.

    `log` is the board's sink for these lines: `print` on the P4, the diag ring
    on the T-Deck, which has no serial RX to be asked afterwards.
    """

    def __init__(self, ws, log=None):
        self.ws = ws
        self.log = log or print
        # Cleared once the confirm has fired (or on a non-OTA build), so the
        # frame loop stops asking.
        self.ota = getattr(ws, "updater", None)

    def boot_check(self):
        ota = self.ota
        if ota is None:
            return
        try:
            verdict = ota.boot_check()
            if verdict:
                self.log("last update %s (%s)" % verdict)
                self.ws.announce_update()   # and say so on the desktop, not just here
        except Exception as exc:  # noqa: BLE001 -- never block the desktop
            self.log("boot_check failed: %s" % (exc,))

    def tick(self):
        ota = self.ota
        if ota is None:
            return
        try:
            if ota.confirm_when_healthy(getattr(self.ws, "_frames_drawn", 0)):
                self.log("marked app valid (slot %s)" % ota.slot())
            if ota.confirmed:
                self.ota = None       # fired (or a non-OTA build): stop asking
        except Exception as exc:  # noqa: BLE001 -- never break a frame over this
            self.log("confirm failed: %s" % (exc,))
            self.ota = None


class FramePump:
    """The frame loop's shared head and tail: the dt clock, the once-only boot
    housekeeping, and the cadence.

    Deliberately NOT the whole loop. The middle of a frame is where the two
    boards genuinely diverge -- trackball + poller thread + SRAM-bounce flush on
    one, BLE HID + serial dev channel + async-PPA `present_pending` on the other
    -- and flattening that into a hook-per-line abstraction would hide real
    hardware behind a false shared shape. What IS shared is the arithmetic
    around it, which is exactly where the asymmetry had grown.
    """

    def __init__(self, boot, ota=None, fps_cap=60):
        self.boot = boot
        self.ota = ota
        self.frame_ms = 1000 // fps_cap
        # Pacing debt (#77, 2026-08-10): ms the loop is BEHIND its cadence.
        # See pace() for what it buys.
        self.debt = 0
        # Sleep-overshoot slack (#202, 2026-08-17): a learned estimate of how
        # much longer time.sleep_ms actually sleeps than asked, subtracted
        # from future sleeps. Measured on the P4: FREERTOS_HZ=100 (a 10ms
        # tick, upstream MicroPython's own sdkconfig) makes every paced sleep
        # overshoot by ~4.2ms -- and a MEMORYLESS pace() pays that every
        # frame, which is exactly how a roster that runs 74fps uncapped
        # paced itself down to 48. The slack is an integer EMA fed by begin()
        # (the one place that owns the real clock), floored at 0 and capped
        # small so a hitch stays debt's business; on a platform whose sleeps
        # are exact it converges to 0 and changes nothing.
        self.slack = 0
        self._expected = 0      # what pace() scheduled the last frame to total
        self._slept = False     # ...and whether it actually asked for a sleep
        self.last = _ticks_ms()

    def begin(self):
        """Top of the loop: `(now, dt)`, with dt clamped to 0..100ms so a hitch
        (a 200ms GC, an SD write) can't teleport a cart's physics. Also the
        slack learner: the real period of the frame that just ended, compared
        against what pace() scheduled for it -- only on frames that SLEPT
        (a no-sleep frame's overrun is debt's business), with the per-sample
        error clamped so one GC pause cannot slam the estimate."""
        now = _ticks_ms()
        real = _ticks_diff(now, self.last)
        if self._slept:
            over = real - self._expected
            # A saturating +-1ms/frame walker, not an integer EMA (whose
            # floor-division stalls 1-3ms under the true overshoot): converges
            # in a handful of frames and then dithers +-1ms around it, which
            # at a 16ms budget is fps noise. Clamps keep a hitch from slamming
            # it; debt owns real overruns.
            if over > 0 and self.slack < 8:
                self.slack += 1
            elif over < 0 and self.slack > 0:
                self.slack -= 1
        dt = max(0.0, min(0.1, real / 1000.0))
        self.last = now
        return now, dt

    def tail(self, ws):
        """The once-only frame housekeeping both boards run after `ws.frame()`:
        the splash hand-over + timing report, and the OTA rollback confirm.

        Called AFTER the board's own backlight gate, which stays board-side --
        the P4's idle screen blank owns that panel light too, and its
        `not _asleep` guard is a real difference, not an oversight.
        """
        self.boot.first_frame(ws)
        if self.ota is not None:
            self.ota.tick()

    def pace(self, ws, elapsed):
        """How long to sleep after a frame that took `elapsed` ms. Pure integer
        arithmetic on an INJECTED elapsed -- never a clock -- so a test can walk
        an exact trajectory (same rule as ui.ScrollRegion's physics).

        A running GAME locks to a steady cadence (#63: 30fps default, manifest
        `"fps": 60` for carts that sustain it) -- a LOCKED 30 feels smoother
        than a 38-55 swing, and the freed headroom absorbs GC/SD hitches.
        Console screens and tools keep the loop's own fps_cap so the pointer
        stays responsive. Re-read every iteration: it changes on cart open/exit.

        THE DEBT (#77, 2026-08-10, learned on zoomed celeste). A per-frame clamp
        can only slow FAST frames, so a cart whose full frame overruns the 33ms
        budget produced 50 + 33-padded pairs = 83ms under frameskip -- the game
        20% slow (audio still ahead) at 12fps, worse on both axes than no skip
        at all. An over-budget frame now accrues debt that the following frames'
        sleeps pay down, so the PAIR totals two budget slots (50 + 16 = 66ms):
        the shim quantizes to tick-every-frame, the game runs its true 30Hz,
        render an even 15fps. Capped at one pair so a real hitch (a 200ms GC)
        doesn't eat the sleeps for a second afterwards.

        This ran on the T-Deck only for four days. It is pacing arithmetic, not
        a panel property, and frameskip ships on both boards -- so the P4 has it
        now. It is inert while frames fit their budget (debt stays 0).
        """
        try:
            fms = 1000 // ws.frame_cap_fps()
        except Exception:  # noqa: BLE001 -- pacing must never kill the loop
            fms = self.frame_ms
        if fms < self.frame_ms:
            fms = self.frame_ms                 # never pace FASTER than the loop cap
        if elapsed < fms:
            sleep = fms - elapsed
            if self.debt:                       # pay the debt out of this sleep
                take = sleep if sleep < self.debt else self.debt
                sleep -= take
                self.debt -= take
            cut = 0
            if self.slack and sleep:            # #202: pre-pay the overshoot
                cut = self.slack if self.slack < sleep else sleep
                sleep -= cut
            self._expected = fms                # the slot this frame should total
            # A frame whose sleep the slack CUT (even to zero) stays learnable:
            # if the cut was too deep the real period lands under the slot,
            # over goes negative and the walker steps back down. Marking only
            # sleep>0 frames froze the walker at its ceiling the moment it
            # swallowed a whole sleep -- measured as the roster sailing PAST
            # the cap (73fps under a 60 cap) with slack stuck at 8.
            self._slept = sleep > 0 or cut > 0
            return sleep
        self._expected = elapsed
        self._slept = False
        self.debt += elapsed - fms
        if self.debt > 2 * fms:
            self.debt = 2 * fms                 # unpayable: just run flat out
        return 0


class IdleBlank:
    """Blank the panel backlight after a spell with no input, restore it on the next.

    ONE implementation for both boards. The P4 shipped this first (#58) and the
    T-Deck grew a hand-rolled second copy on 2026-08-16 that got three things
    wrong -- all three are behaviours, not details, and all three are why this is
    shared rather than re-typed per board:

      1. The touch that WAKES the screen must not also press what it landed on,
         or a wake tap launches a cart.
      2. `ws._dirty` has to be set on wake. The panel may still hold a frame from
         before the blank and the partial-paint machinery will happily leave it
         there.
      3. An EXPLICIT blank has to outrank activity. `power off` arrives on the
         serial channel, which is itself activity, so without this it wakes again
         in the very same iteration.

    Drives the backlight and nothing else: the board keeps RENDERING while dark,
    so an unattended bench run still produces the frames it is measuring.

    `set_backlight` is injected because the two boards reach their panel
    differently, and `ws` is passed per-tick rather than held so this owns no
    console reference.
    """

    def __init__(self, set_backlight, timeout_ms=300000):
        self._set = set_backlight
        self.timeout_ms = timeout_ms   # 0 disables
        self.asleep = False
        self.force = False             # an explicit blank is pending
        self._idle_at = 0

    def wake(self, now):
        self._idle_at = now
        return self._resume() if self.asleep else False

    def _resume(self):
        self.asleep = False
        self._set(True)
        return True

    def blank(self):
        """Ask for a blank at the next tick, outranking that tick's activity."""
        self.force = True

    def tick(self, now, active, ws, pointer=None, click=False):
        """Returns the (possibly cleared) click for this frame.

        Call after EVERY input source has been read and before the pointer is
        handed to the console -- that ordering is what lets the waking touch be
        swallowed.
        """
        if self.force:
            self.force = False
            self._idle_at = now
            if not self.asleep:
                self.asleep = True
                self._set(False)
            ws._psave_asleep = True
            return click
        if active:
            self._idle_at = now
            if self.asleep:
                self._resume()
                ws._psave_asleep = False
                ws._dirty = True            # (2)
                if pointer is not None:     # (1)
                    pointer.down = False
                return False
            return click
        if (self.timeout_ms and not self.asleep
                and _ticks_diff(now, self._idle_at) >= self.timeout_ms):
            self.asleep = True
            self._set(False)
            ws._psave_asleep = True
            # Say so. The panel going dark is indistinguishable from a hang or a
            # dead backlight otherwise, and this is the one event here nobody
            # can see happen.
            print("Moybyte power save: screen off (idle %ds)"
                  % (self.timeout_ms // 1000))
        return click


def _sleep_ms(ms):
    """MicroPython's sleep_ms, with the host shim the tick helpers get for
    free -- FrameLoop is host-executed by tests/test_device_boot.py."""
    import time
    try:
        time.sleep_ms(ms)
    except AttributeError:
        time.sleep(ms / 1000.0)


class FrameLoop:
    """The device frame loop's INVARIANT ORDER, one copy for every board
    (#202 Phase B -- the extraction #161 declined while the loop middles were
    still large and the T-Deck had no on-glass harness; both premises expired
    on 2026-08-17).

    What this class owns is exactly the ordering whose per-board copies are
    where this repo's worst bugs have lived (#56 was an order bug; so was
    PURR's F13, quoted in #161):

      pump.begin -> poll_inputs (EVERY input source) -> dev channel ->
      idle.tick (the wake-swallow needs all inputs read first) ->
      pointer.click/tick -> present (pre-frame buffer work: the P4's
      present_pending must precede sync_back, which re-points at the freed
      buffer) -> ws.handle_input/handle_pointer/frame -> the first-frame
      backlight gate -> pump.tail -> tail -> pace -> account -> sleep.

    Boards supply the hooks; everything hardware stays theirs:

      poll_inputs(now) -> (click, active)  read every input source, feed the
                          pointer's place/down/fresh. `active` is the idle
                          blank's wake condition MINUS the dev channel (the
                          loop adds `ran` itself).
      present()         pre-frame buffer work (sync_back and friends), or None.
      frame_error(exc)  the board's crash note (default: print). Runs for any
                          Exception; KeyboardInterrupt always propagates (the
                          Ctrl-C -> shell -> REPL contract).
      tail(now)         per-frame services after pump.tail (webhost poll, diag
                          ticks, SD flush cadence), counted INSIDE the frame's
                          elapsed, or None.
      account(now, elapsed, sleep_ms)  frame accounting after pace (HITCH,
                          LOOP accumulators, PERF samplers), or None.

    The loop also exposes the per-frame ws-phase splits every board's
    diagnostics want (t_hi/t_hp/t_ws -- handle_input/handle_pointer/frame ms)
    and the drew/frames_before pair the T-Deck's SD bracket and idle-band
    drain read. run() returns "quit" when the dev channel asked for the REPL;
    the board prints its own goodbye.
    """

    def __init__(self, ws, pump, pointer, poll_inputs,
                 idle=None, serial=None, present=None, tail=None,
                 account=None, frame_error=None,
                 set_backlight=None, lit=False):
        self.ws = ws
        self.pump = pump
        self.pointer = pointer
        self.poll_inputs = poll_inputs
        self.idle = idle
        self.serial = serial
        self.present = present
        self.tail = tail
        self.account = account
        self.frame_error = frame_error
        self.set_backlight = set_backlight
        self._lit = lit
        self.t_hi = 0            # ws.handle_input ms, this frame
        self.t_hp = 0            # ws.handle_pointer ms
        self.t_ws = 0            # the whole ws phase (input+pointer+frame) ms
        self.frames_before = 0   # _frames_drawn entering the ws phase
        self.drew = False        # did this frame reach the glass

    def step(self):
        """One frame. Returns "quit" when the dev channel asked for the REPL,
        else None. Split from run() so a test can drive single frames."""
        ws = self.ws
        pointer = self.pointer
        now, dt = self.pump.begin()
        click, active = self.poll_inputs(now)
        ran = False
        if self.serial is not None:
            ran = self.serial.poll(ws)
            click = self.serial.click or click
            if self.serial.quit:
                return "quit"
        if self.idle is not None:
            # After EVERY input source (poll_inputs + the dev channel) and
            # before the pointer reaches the console -- the ordering that lets
            # the waking touch be swallowed instead of pressing what it landed
            # on. A dev command or scripted gesture frame counts as activity.
            click = self.idle.tick(now, bool(active) or ran, ws, pointer, click)
        pointer.click = click
        pointer.tick(now)
        self.frames_before = getattr(ws, "_frames_drawn", 0)
        if self.present is not None:
            self.present()
        t0 = _ticks_ms()
        self.t_hi = 0
        self.t_hp = 0
        try:
            ws.handle_input()
            self.t_hi = _ticks_diff(_ticks_ms(), t0)
            ws.handle_pointer()
            self.t_hp = _ticks_diff(_ticks_ms(), t0) - self.t_hi
            ws.frame(dt)         # draw + composite + flush
        except KeyboardInterrupt:
            raise                # Ctrl-C -> shell -> REPL, never swallowed
        except Exception as exc:  # noqa: BLE001 -- one bad frame must not brick it
            if self.frame_error is not None:
                self.frame_error(exc)
            else:
                print("Moybyte frame error:", exc)
        self.t_ws = _ticks_diff(_ticks_ms(), t0)
        self.drew = getattr(ws, "_frames_drawn", 0) != self.frames_before
        # First composed frame lights the panel (#45): _frames_drawn ticks past
        # 0 only inside frame() after the flush, so the first sight is the
        # desktop, not power-on GRAM noise. `not idle.asleep` keeps the gate
        # from re-lighting a deliberately blanked panel (the boards keep
        # RENDERING while dark).
        if not self._lit and (self.idle is None or not self.idle.asleep) \
                and getattr(ws, "_frames_drawn", 0) > 0:
            if self.set_backlight is not None:
                try:
                    self.set_backlight(True)
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte backlight on failed:", exc)
            self._lit = True
        self.pump.tail(ws)
        if self.tail is not None:
            self.tail(now)
        elapsed = _ticks_diff(_ticks_ms(), now)
        sleep_ms = self.pump.pace(ws, elapsed)
        if self.account is not None:
            self.account(now, elapsed, sleep_ms)
        if sleep_ms:
            _sleep_ms(sleep_ms)
        return None

    def run(self):
        while True:
            if self.step() == "quit":
                return "quit"
