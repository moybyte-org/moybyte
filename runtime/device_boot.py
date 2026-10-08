# Map (grep -n a name to jump there):
#   DeviceBoot                        the boot sequence's shared steps and its screen
#   report_update                     the OTA verdict, on the boot path
#   boot_ok                           the first frame proved the boot to the kernel
"""The device boot spine -- ONE implementation, both boards (#161). The frame
it hands over to is the kernel's (native/moy_kernel/moy_loop.c).

WHY THIS EXISTS. Each board used to author its own `run_desktop` boot
sequence, and the shape of that arrangement's bugs is always the same: a step
written on one board, forgotten on the other, and silent about it because
every consumer is capability-gated.

`_pace_debt` is the proof. It shipped 2026-08-10 (fd068fc) into the T-Deck's
loop only, four days before this file was written. Frameskip shipped on BOTH
boards (Settings -> FRAMESKIP, since retired by the tick model, #217), and the
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
                            its built-in fallback (`BootCarts`, in
                            boot_carts.py); the Lua runtime probe; the
                            four-stage internal-SRAM census; the OTA boot
                            verdict and the frame-loop rollback confirm; the
                            frame cadence, its debt and the sleep.

  DIFFERS BY VALUE (here,   the store root and its media word; the splash's
  as a parameter)           serial label; the backlight function; the log sink
                            (the T-Deck's goes into the offline diag ring
                            because that board has no serial RX to ask later).

  GENUINELY BOARD-SPECIFIC  the panel + canvas bring-up (esp_lcd strips vs DPI
  (stays in moy_runtime)    scan-out); which input the kernel's drivers bring
                            (keyboard + trackball + GT911 vs BLE HID + touch); the SD/panel bus gate; the
                            presentation tier install (WindowedWM); the P4's
                            serial dev channel, drag/swipe scripts and idle
                            screen blank; the T-Deck's diag ring and HITCH/LOOP
                            accounting; the P4's
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

try:
    from boot_carts import BootCarts
    from crash_guard import take_crash
except ImportError:  # pragma: no cover - host package lane
    from runtime.boot_carts import BootCarts
    from runtime.crash_guard import take_crash

try:
    import moy_kernel as _kernel
except ImportError:  # every tier but a console board's
    _kernel = None

try:
    from device_util import sram_census
except ImportError:  # pragma: no cover - host lane: no device tier staged
    def sram_census(stage):
        """No second region off-board, so nothing to weigh."""


class DeviceBoot(BootCarts):
    """The boot sequence's shared steps, and the screen that reports them.

    One instance per boot, constructed as soon as a board has a canvas and a
    compositor. `label` is the serial prefix ("Moybyte" / "Moybyte P4") and
    `set_backlight` the board's panel-light function -- the two things that
    differ between the boards in every line this class prints. The cart step
    (`load_carts`, `seed_progress`) is `BootCarts`, runtime/boot_carts.py.
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

    # -- the steps -----------------------------------------------------------

    def runtimes(self, ws, log=None):
        """The cart runtimes in this image (`ws.runtimes`), and a line each
        saying whether it is here.

        "lua" (#67): ONE runtime and no chooser (2026-08-13) -- moycore runs
        the cart's whole frame inside libmoy, `_update` and `_draw` back to back
        in C, one upcall per frame instead of hundreds -- and moybyte's superset
        verbs ride it as registered trampolines. "wasm"
        (docs/wasm_tier_plan_2026-09.md): the same console with libmoy's wasm
        import table on it, and the moy_wasm engine running the cart's module.
        A runtime this build lacks is an absent key, and a cart naming it opens
        the Player's runtime-missing panel -- the graceful floor.

        `log` defaults to the boot's own serial line; the T-Deck passes its diag
        sink so the answer also lands in the offline ring.
        """
        sram_census("console")
        rts = {}
        try:
            from moycore_glue import make_runtimes
            rts = make_runtimes(ws)
        except ImportError:
            pass
        say = log or self.say
        say("lua runtime %s" % ("ON (moycore)" if "lua" in rts else "ABSENT"))
        say("wasm runtime %s" % ("ON (moy_wasm)" if "wasm" in rts else "ABSENT"))
        return rts

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
        sram_census("desktop-up")
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
        boot_ok(ws)
        return True


def report_update(ws, log):
    """The OTA verdict, read on the boot path before anything can overwrite
    the evidence (#53): said on the boot's line and on the desktop. The
    rollback CONFIRM is not made here -- reaching the boot path proves only
    that the desktop was CONSTRUCTED (#56); the kernel's loop makes it after
    painted frames."""
    upd = getattr(ws, "updater", None)
    if upd is None:
        return None
    try:
        verdict = upd.boot_check()
        if verdict:
            log("last update %s (%s)" % verdict)
            ws.announce_update()   # and say so on the desktop, not just here
        return verdict
    except Exception as exc:  # noqa: BLE001 -- never block the desktop
        log("boot_check failed: %s" % (exc,))
        return None


def boot_ok(ws, kernel=None):
    """The console painted its first frame: it proved itself to the kernel
    (native/moy_kernel), which would otherwise send a boot that ends here to
    the recovery screen, and counts it out of the boot-loop guard. Then the
    crash the kernel recorded before this boot, if any, said once on the
    notice banner."""
    kernel = kernel or _kernel
    if kernel is None:
        return None
    kernel.boot_ok()
    rec = take_crash()
    if rec:
        who = rec.get("id") or rec.get("task") or "?"
        ws.notice("IT CRASHED LAST TIME", "%s: %s" % (who, rec.get("what") or rec.get("kind")),
                  "warn", 10000)
    return rec
