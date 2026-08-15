"""Bring-up smokes for the mainline T-Deck -- one per port stage.

WHY THESE EXIST AT ALL, and why they are not tests. Nothing in this port can be
verified by the host suite: `make test` proves the shared console's logic and
says nothing about whether an ST7789 comes up, whether the GT911 answers, or
whether the SD card can be attached to a live SPI bus without hanging the board.
The only instrument is the owner's eyes plus the serial TX stream, so each stage
ships a self-terminating program that makes ONE subsystem say something a human
can check, in a form a human can check it in.

Two rules every smoke here follows.

  SELF-TERMINATING. Each returns to the REPL rather than taking the loop over.
  Under the fork build this board's USB-CDC RX dies once a takeover loop starts
  (CLAUDE.md's hard constraints), and whether mainline's CDC stack has the same
  hole is one of the questions this port exists to answer -- so a bring-up
  program must never be the thing that costs the owner a REPL they might have
  had. Stage 6's desktop is the first loop that does not return.

  IT PRINTS THE NUMBER, not just a verdict. "touch works" is worth much less
  than the raw GT911 coordinates beside the mapped ones, because the second form
  says WHICH axis is inverted when it does not work. Serial TX is this board's
  only channel back; every smoke assumes the owner is reading it.
"""

import time

import moy_lcd
from tdeck_panel import TDeckCompositor

# MOY64 palette indices used by the smoke screens. Spelled out rather than
# imported from `console.NAMES`: a bring-up program that pulls in the whole
# shared console to name a colour can fail for a reason that has nothing to do
# with the hardware it is testing.
BLACK = 0
DARK = 1
GREY = 6
WHITE = 7
RED = 8
YELLOW = 10
GREEN = 11
BLUE = 12


def _canvas(nfbs=2):
    """Panel up, canvas over it, backlight still OFF (#45).

    Returns (compositor, canvas). The caller lights the backlight once it has
    composed a frame -- a fresh ST7789's GRAM is noise and must never be lit.
    """
    comp = TDeckCompositor(nfbs=nfbs)
    from device_canvas import DeviceCanvas
    canvas = DeviceCanvas(comp)
    return comp, canvas


def _present(comp, canvas):
    """Finish the frame: drain any batched sprite ops, push, re-point at the
    new back buffer. `sync_back` is not hygiene -- flush() ping-pongs the two
    PSRAM framebuffers, so without it the next frame paints the buffer the
    panel is showing."""
    canvas.flush_batch()
    comp.flush()
    canvas.sync_back()


# ---------------------------------------------------------------------------
# STAGE 1 -- the panel.
# ---------------------------------------------------------------------------


def panel(frames=6):
    """Bring the panel up and paint a test pattern in C. Returns to the REPL.

    Deliberately draws through `moy_lcd.bars()` and not through the canvas:
    stage 1 is answering "does an ST7789 come up on mainline with no LVGL", and
    a pattern that needs moy_gfx, a palette and a Python raster to appear would
    make three answers out of one question.

    What "the panel works" means, in the order this proves it:
      1. moy_lcd.init() returns     -> SPI2 came up, esp_lcd took the ST7789,
                                       the vendor init sequence was accepted
      2. the backlight lights       -> GPIO42 and the board power rail (GPIO10)
      3. bars appear, right way up  -> MADCTL/rotation, byte order, stride
      4. the checker is square      -> no row shear, and no band seam at
                                       y=48/96/144/192 (the flush banding)
      5. FLUSH us= prints           -> the completion fence ran; the number is
                                       the real per-frame panel cost
    """
    print("Moybyte panel: init")
    comp = TDeckCompositor(nfbs=2)
    w, h = comp.size()
    print("Moybyte panel: %dx%d nfbs=%d madctl=0x%02x gfx=%s"
          % (w, h, moy_lcd.nfbs(), moy_lcd.madctl(), comp.has_gfx()))

    # Paint the pattern into EVERY framebuffer before lighting the backlight, so
    # the ping-pong can never present a buffer of power-on noise.
    for i in range(moy_lcd.nfbs()):
        moy_lcd.bars(i)
    for _ in range(moy_lcd.nfbs()):
        comp.flush()
    comp.set_backlight(True)
    print("Moybyte panel: backlight ON -- expect 8 colour bars over a checker")

    # Re-flush a few times so `us=` is a steady-state number rather than the
    # first-transfer outlier, and so a tear/flicker would be visible.
    for _ in range(frames):
        comp.flush()
        time.sleep_ms(120)
    n, us = comp.stats()
    print("Moybyte panel: flushes=%d last=%dus (%.1f fps ceiling)"
          % (n, us, 1000000.0 / us if us else 0.0))
    print("Moybyte panel smoke done -> REPL "
          "(moy_lcd.set_madctl(0x28|0x68|0xA8|0xE8) if the image is turned)")


# ---------------------------------------------------------------------------
# STAGE 2 -- GT911 touch on I2C0.
# ---------------------------------------------------------------------------

# Corner + centre targets, as (cx, cy_from_edge, label). Built at run time from
# the canvas size so nothing here restates 320x240.
_TOUCH_MARGIN = 26
_TOUCH_BOX = 20


def _touch_targets(w, h):
    m = _TOUCH_MARGIN
    return ((m, m, "1 TL"), (w - 1 - m, m, "2 TR"),
            (m, h - 1 - m, "3 BL"), (w - 1 - m, h - 1 - m, "4 BR"),
            (w // 2, h // 2, "5 MID"))


def touch(secs=60):
    """GT911 bring-up: tap the boxes, watch the crosshair and the serial.

    This is the stage's whole verification, because touch has three independent
    ways to be wrong and only the raw numbers separate them:

      NOT FOUND     -> "GT911 not found on I2C0". The controller is on the same
                       I2C0 (SCL 8 / SDA 18) as the keyboard C3, at 0x5D or
                       0x14 depending on how the INT line was strapped at reset.
      FOUND, MAPPED WRONG -> taps land in the wrong box. `raw=` vs `map=` on the
                       serial line says which axis: device_input's TOUCH_SWAP /
                       TOUCH_FLIP_X / TOUCH_FLIP_Y are the three knobs, and they
                       are module globals, so they can be poked from the REPL
                       and this smoke re-run with NO rebuild.
      FOUND, MAPPED RIGHT, SLOW -> the I2CSTAT line. The GT911 clock-stretches
                       20-45ms on most reads taken while a finger is DOWN (#74),
                       which is why stage 3's poller thread exists at all. Seeing
                       `over20` climb HERE, in a single-threaded program, is the
                       measurement that justifies it.

    The screen repaints only when the touch state changes, so an idle board is
    not flushing the panel 60 times a second while the owner reads serial.
    """
    from device_input import Touch
    import device_input

    comp, canvas = _canvas()
    w, h = canvas.w, canvas.h
    tp_dev = Touch(w, h)
    # Tap the driver's RAW sample on its way past, so the serial line can show
    # raw beside mapped. Wrapped HERE, on the instance, rather than adding a
    # debug field to `device_input` -- that module is staged from the SHIPPING
    # build's tree and a bring-up program has no business editing it.
    raw_seen = [None]
    _read_raw = tp_dev.read_raw

    def _tapped_read_raw():
        r = _read_raw()
        if r is not None and r is not False:
            raw_seen[0] = r
        return r

    tp_dev.read_raw = _tapped_read_raw
    print("Moybyte touch: available=%d addr=%s int_pin=%s gate=%s"
          % (1 if tp_dev.available else 0,
             hex(tp_dev.addr) if tp_dev.addr else "-",
             device_input.Touch.INT_PIN,
             "on" if tp_dev._int_pin is not None else "OFF (blind polling)"))
    print("Moybyte touch: map knobs swap=%s flip_x=%s flip_y=%s raw=%dx%d"
          % (device_input.TOUCH_SWAP, device_input.TOUCH_FLIP_X,
             device_input.TOUCH_FLIP_Y, device_input.TOUCH_RAW_W,
             device_input.TOUCH_RAW_H))

    targets = _touch_targets(w, h)
    last = None
    taps = 0

    def _paint(pt, tap):
        canvas.cls(DARK)
        for (cx, cy, label) in targets:
            canvas.rectb(cx - _TOUCH_BOX, cy - _TOUCH_BOX,
                         _TOUCH_BOX * 2, _TOUCH_BOX * 2, YELLOW)
            canvas.print(label, cx - 12, cy - 3, GREY)
        canvas.print("TOUCH THE BOXES", w // 2 - 60, 8, WHITE)
        if pt is None:
            canvas.print("no finger", w // 2 - 36, h - 16, GREY)
        else:
            x, y = pt[0], pt[1]
            col = RED if tap else GREEN
            canvas.line(x - 12, y, x + 12, y, col)
            canvas.line(x, y - 12, x, y + 12, col)
            canvas.print("map=%d,%d raw=%s" % (x, y, _raw_str(raw_seen[0])),
                         6, h - 16, WHITE)
        _present(comp, canvas)

    _paint(None, False)
    comp.set_backlight(True)

    t_end = time.ticks_add(time.ticks_ms(), secs * 1000)
    t_beat = time.ticks_ms()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        pt = tp_dev.poll()
        state = None if pt is None else (pt[0], pt[1])
        if state != last:
            last = state
            _paint(pt, bool(pt and pt[2]))
        if pt is not None and pt[2]:
            taps += 1
            print("TAP %d map=(%d,%d) raw=%s" % (taps, pt[0], pt[1],
                                                 _raw_str(raw_seen[0])))
        if time.ticks_diff(time.ticks_ms(), t_beat) >= 3000:
            t_beat = time.ticks_ms()
            print("Moybyte touch: %s" % _i2cstat(tp_dev))
        time.sleep_ms(10)

    print("Moybyte touch: taps=%d %s" % (taps, _i2cstat(tp_dev)))
    print("Moybyte touch smoke done -> REPL")


# ---------------------------------------------------------------------------
# STAGE 3 -- the ESP32-C3 keyboard on I2C0, and the #69 poller thread.
# ---------------------------------------------------------------------------

# Buttons drawn as a held/not-held row. Not every name in InputState.BUTTONS --
# these are the ones the T-Deck matrix can actually produce (moybyte/input.py's
# KEY_BUTTON): the WASD d-pad, L/space = A, K = B, ENTER = run, BACKSPACE = home.
_KBD_BUTTONS = ("up", "down", "left", "right", "a", "b", "run", "home")

# Seconds per phase. Three phases, so the whole smoke is ~3x this plus the
# wrap-up -- long enough to hold a key down and see it repeat, short enough
# that the owner is not standing over the board.
_KBD_PHASE_S = 15


def keyboard(phase_s=_KBD_PHASE_S):
    """Keyboard bring-up AND the #69 poller A/B, in one program.

    THREE PHASES, and the third is the point:

      1. ASCII (synchronous)  -- the mode the code editor runs in. Each key
         reports ONCE on the press edge with no autorepeat, which is why a held
         key can only be faked (`KEY_HOLD_MS`) and why raw mode has to exist.
      2. RAW MATRIX (synchronous) -- `0x03`, five bytes per read, one bitmask
         per column, bit N = row N. A HELD direction keeps firing here, which is
         what a running cart needs. Needs C3 firmware >= 2025-06-12; older
         firmware ignores the command and keeps sending ASCII, which the driver
         detects and falls back on -- the smoke says so if it happens.
      3. RAW MATRIX (POLLER THREAD) -- the same reads, moved off the frame loop
         onto `moybyte.input.InputPoller`.

    WHAT PHASE 3 MEASURES, and why it is the whole reason the port carries the
    #69 GIL patch. The C3 is a bit-banged I2C slave that CLOCK-STRETCHES: real
    stalls of 21-60ms have been measured on this board. In phases 1 and 2 that
    stall lands inside the loop and IS the frame. In phase 3 it lands on the
    poller thread instead -- but ONLY if `machine_i2c.c` releases the GIL across
    the blocking transaction, because MicroPython threads share one GIL, so
    without the patch the stall freezes the VM from whichever thread took it.

    So the loop's worst iteration is printed for every phase, and the phase-2
    vs phase-3 pair is the on-glass proof that the patch is doing its job:
    phase 3's `max=` should collapse toward the panel flush cost while its
    `i2c max=` stays just as bad. Both numbers staying bad means the patch is
    not in the image; both improving means the C3 simply was not stalling and
    the test needs a harder workout (hold several keys).

    The screen shows a MOVING BAR. A frozen bar is a frozen loop, which is the
    one failure this program exists to make visible without a stopwatch.
    """
    from moybyte.input import InputState, TDeckKeyboard, InputPoller

    comp, canvas = _canvas()
    w, h = canvas.w, canvas.h
    inp = InputState()
    kbd = TDeckKeyboard(inp)
    # Watch the raw five bytes go past. Instance-level, like the touch smoke's
    # wrapper: `moybyte/input.py` is the SHIPPING build's keyboard driver and a
    # bring-up program does not get to add debug fields to it.
    raw_seen = [None]
    _timed_read = kbd._timed_read

    def _tapped_timed_read(n):
        d = _timed_read(n)
        if n == 5 and d is not None and len(d) == 5:
            raw_seen[0] = bytes(d)
        return d

    kbd._timed_read = _tapped_timed_read

    print("Moybyte kbd: available=%d addr=0x%02x raw_allowed=%s timeout_us=%s"
          % (1 if kbd.available else 0, kbd.KEYBOARD_ADDR, kbd.RAW_GAME_MODE,
             kbd.I2C_TIMEOUT_US))
    if not kbd.available:
        print("Moybyte kbd: NOT FOUND on I2C0 -- nothing further to measure")

    comp.set_backlight(True)
    typed = []
    results = []

    def _run_phase(label, raw, poller, secs):
        """One phase: drive input for `secs`, draw every frame, and return the
        loop's own worst iteration beside the I2C driver's worst transaction."""
        kbd.set_game_mode(raw)
        if poller is not None:
            # The poller owns the bus, so the mode switch it was just handed is
            # applied by the poller between reads, not from here. Give it a beat
            # to take effect before measuring, or phase 3 spends its first
            # samples in the mode phase 2 left behind.
            time.sleep_ms(80)
        base_n = kbd.stat_n
        base_max = kbd.stat_max_us
        base_o5 = kbd.stat_over5
        base_o20 = kbd.stat_over20
        base_to = kbd.stat_timeouts
        kbd.stat_max_us = 0             # per-phase worst; restored below
        worst = 0
        over20 = 0
        frames = 0
        t_end = time.ticks_add(time.ticks_ms(), secs * 1000)
        while time.ticks_diff(t_end, time.ticks_ms()) > 0:
            t0 = time.ticks_ms()
            inp.begin_frame()
            if poller is not None:
                poller.consume()
            else:
                kbd.poll()
            k = inp.last_key
            if k and 0x20 <= k <= 0x7E:
                typed.append(chr(k))
                del typed[:-24]
            _paint_kbd(comp, canvas, label, kbd, inp, raw_seen[0], typed,
                       frames, worst)
            frames += 1
            el = time.ticks_diff(time.ticks_ms(), t0)
            if el > worst:
                worst = el
            if el >= 20:
                over20 += 1
        line = ("%-18s frames=%d loop_max=%dms over20=%d | i2c reads=%d "
                "max=%.1fms over5=%d over20=%d timeouts=%d raw_mode=%s"
                % (label, frames, worst, over20, kbd.stat_n - base_n,
                   kbd.stat_max_us / 1000.0, kbd.stat_over5 - base_o5,
                   kbd.stat_over20 - base_o20, kbd.stat_timeouts - base_to,
                   kbd.raw_mode))
        if kbd.stat_max_us < base_max:
            kbd.stat_max_us = base_max      # keep the session maximum honest
        print("Moybyte kbd: " + line)
        results.append((label, worst))

    _run_phase("1 ascii sync", False, None, phase_s)
    _run_phase("2 raw sync", True, None, phase_s)
    if kbd._raw_unsupported:
        print("Moybyte kbd: RAW MODE UNSUPPORTED -- the C3 firmware ignored 0x03 "
              "(pre-2025-06-12). The driver fell back to ASCII + the hold latch, "
              "which is correct behaviour, but hold-to-move will stall.")

    # Phase 3: the same reads, off the loop. `touch=None` is deliberate -- this
    # phase is about the keyboard's stall, and adding the GT911's would make the
    # two numbers uninterpretable.
    poller = InputPoller(kbd, None)
    if poller.start():
        kbd._poller_owned = True
        print("Moybyte kbd: poller thread up (%dms cadence)" % poller.period)
        _run_phase("3 raw poller", True, poller, phase_s)
        poller.stop()
        kbd._poller_owned = False
        time.sleep_ms(50)
        print("Moybyte kbd: poller thread alive=%s after stop" % poller.alive)
    else:
        print("Moybyte kbd: poller thread FAILED to start -- no _thread or no RAM; "
              "the console falls back to synchronous polling, which is phase 2")

    # Back to ASCII (0x04). Sending the revert is the step an earlier attempt
    # missed, and skipping it leaves the keyboard streaming matrix bytes at the
    # code editor -- irreversibly garbled text, from the next boot's point of
    # view, because nothing re-sends it.
    kbd.set_game_mode(False)
    kbd.poll()
    print("Moybyte kbd: reverted to ASCII -- raw_mode=%s" % kbd.raw_mode)
    if len(results) >= 3:
        print("Moybyte kbd: GIL VERDICT loop_max sync=%dms poller=%dms "
              "(poller should be the smaller; both large = the #69 I2C "
              "GIL-release patch is not in this image)"
              % (results[1][1], results[2][1]))
    print("Moybyte kbd smoke done -> REPL")


def _paint_kbd(comp, canvas, label, kbd, inp, raw, typed, frame, worst):
    w, h = canvas.w, canvas.h
    canvas.cls(DARK)
    canvas.print(label, 6, 6, YELLOW)
    canvas.print("raw_mode=%s" % kbd.raw_mode, w - 110, 6, GREY)
    # The moving bar: a frozen loop is a frozen bar, which is the only way to
    # SEE a stall without a stopwatch.
    canvas.rect(6 + (frame * 4) % (w - 24), 20, 12, 6, GREEN)
    canvas.print("key=0x%02x '%s'"
                 % (inp.last_key,
                    chr(inp.last_key) if 0x20 <= inp.last_key <= 0x7E else "."),
                 6, 36, WHITE)
    canvas.print("bytes=%s" % (" ".join("%02x" % b for b in raw) if raw else "-"),
                 6, 50, GREY)
    x = 6
    for name in _KBD_BUTTONS:
        held = inp.held(name)
        canvas.print(name, x, 70, WHITE if held else 1)
        if held:
            canvas.rectb(x - 2, 68, len(name) * 8 + 4, 12, GREEN)
        x += len(name) * 8 + 10
    canvas.print("typed: " + "".join(typed), 6, 92, WHITE)
    canvas.print("loop max %dms" % worst, 6, h - 16, GREY)
    _present(comp, canvas)


def _raw_str(r):
    """The last RAW GT911 sample, straight off the wire.

    `Touch._map` is what turns raw into canvas coords, so printing both is what
    makes a mirrored axis a two-second diagnosis instead of a guess: raw rising
    while mapped falls names the flipped axis outright.
    """
    return "-" if r is None else "(%d,%d)" % (r[0], r[1])


def _i2cstat(t):
    return ("reads=%d max=%.1fms over5=%d over20=%d int_edges=%d skipped=%d"
            % (t.stat_n, t.stat_max_us / 1000.0, t.stat_over5, t.stat_over20,
               t.stat_int_edges, t.stat_skipped))
