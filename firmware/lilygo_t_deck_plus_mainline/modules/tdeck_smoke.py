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
  Under the deleted fork build this board's serial RX died once a takeover loop
  started, and whether this port had the same hole was one of the questions the
  bring-up existed to answer (it does not -- #201, RX works) -- so a bring-up
  program must never be the thing that costs the owner a REPL they might have
  had. Stage 6's desktop is the first loop that does not return.

  IT PRINTS THE NUMBER, not just a verdict. "touch works" is worth much less
  than the raw GT911 coordinates beside the mapped ones, because the second form
  says WHICH axis is inverted when it does not work. Serial TX is this board's
  only channel back; every smoke assumes the owner is reading it.
"""

import time

import moy_glass
import moy_lcd

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
    comp = moy_glass.BandedCompositor(moy_lcd, nfbs=nfbs)
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
    comp = moy_glass.BandedCompositor(moy_lcd, nfbs=2)
    w, h = comp.size()
    print("Moybyte panel: %dx%d nfbs=%d madctl=0x%02x gfx=%s"
          % (w, h, moy_lcd.nfbs(), moy_lcd.madctl(), comp.has_gfx()))

    # Paint the pattern into EVERY framebuffer before lighting the backlight, so
    # the ping-pong can never present a buffer of power-on noise.
    for i in range(moy_lcd.nfbs()):
        moy_lcd.bars(i)
    for _ in range(moy_lcd.nfbs()):
        comp.flush()
    # The comment above is only true with a fence under it: flush() returns with
    # the frame still going out, so lighting here would light the bottom of a
    # buffer of power-on noise for the ~10ms the pump takes to finish it.
    comp.sync()
    comp.set_backlight(True)
    print("Moybyte panel: backlight ON -- expect 8 colour bars over a checker")

    # Re-flush a few times so `us=` is a steady-state number rather than the
    # first-transfer outlier, and so a tear/flicker would be visible.
    for _ in range(frames):
        comp.flush()
        time.sleep_ms(120)
    # flush() RETURNS with the frame still going out (the #66 overlap), so the
    # last one has to be fenced before its span is readable -- otherwise this
    # prints the frame before it. `us` is the wall transfer time either way: the
    # overlap hides it behind render, it does not make the bus faster.
    comp.sync()
    n, us = comp.stats()
    print("Moybyte panel: flushes=%d last=%dus (%.1f fps ceiling)"
          % (n, us, 1000000.0 / us if us else 0.0))
    print("Moybyte panel: pump %s" % (comp.bounce_stats(),))
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

      NOT FOUND     -> available=0. The controller is on the same I2C0 (SCL 8 /
                       SDA 18) as the keyboard C3, at 0x5D or 0x14 depending on
                       how the INT line was strapped at reset.
      FOUND, MAPPED WRONG -> taps land in the wrong box. `raw=` vs `map=` on the
                       serial line says which axis: the driver's swap_xy /
                       flip_x / flip_y are live attributes, poked from the REPL
                       with this smoke re-run and NO rebuild, then baked into
                       mpconfigboard.h's MOY_INPUT_TOUCH_*.
      FOUND, MAPPED RIGHT, SLOW -> the I2CSTAT line. The GT911 clock-stretches
                       20-45ms on most reads taken while a finger is DOWN (#74),
                       which is why the kernel passes it on the input task.

    The screen repaints only when the touch state changes, so an idle board is
    not flushing the panel 60 times a second while the owner reads serial.
    """
    import moy_input

    comp, canvas = _canvas()
    w, h = canvas.w, canvas.h
    tp_dev = moy_input.touch(w, h)
    print("Moybyte touch: available=%d addr=0x%02x gate=%s"
          % (1 if tp_dev.available else 0, tp_dev.addr,
             "on" if tp_dev.gate else "OFF (blind polling)"))
    print("Moybyte touch: map knobs swap=%s flip_x=%s flip_y=%s raw=%dx%d"
          % (tp_dev.swap_xy, tp_dev.flip_x, tp_dev.flip_y, tp_dev.raw_w, tp_dev.raw_h))

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
            canvas.print("map=%d,%d raw=%s" % (x, y, _raw_str(tp_dev.raw)),
                         6, h - 16, WHITE)
        _present(comp, canvas)

    _paint(None, False)
    comp.set_backlight(True)

    t_end = time.ticks_add(time.ticks_ms(), secs * 1000)
    t_beat = time.ticks_ms()
    while time.ticks_diff(t_end, time.ticks_ms()) > 0:
        moy_input.kick()
        time.sleep_ms(10)
        pt = tp_dev.poll()
        state = None if pt is None else (pt[0], pt[1])
        if state != last:
            last = state
            _paint(pt, bool(pt and pt[2]))
        if pt is not None and pt[2]:
            taps += 1
            print("TAP %d map=(%d,%d) raw=%s" % (taps, pt[0], pt[1], _raw_str(tp_dev.raw)))
        if time.ticks_diff(time.ticks_ms(), t_beat) >= 3000:
            t_beat = time.ticks_ms()
            print("Moybyte touch: %s" % _i2cstat(tp_dev))

    print("Moybyte touch: taps=%d %s" % (taps, _i2cstat(tp_dev)))
    print("Moybyte touch smoke done -> REPL")


# ---------------------------------------------------------------------------
# STAGE 3 -- the ESP32-C3 keyboard on I2C0, on the kernel's input task (#69).
# ---------------------------------------------------------------------------

# Buttons drawn as a held/not-held row: the ones the T-Deck matrix can
# produce (native/moy_input/moy_kbd.c's scheme): the WASD d-pad, L/space = A,
# K = B, ENTER = run, BACKSPACE = home.
_KBD_BUTTONS = ("up", "down", "left", "right", "a", "b", "run", "home")

# Seconds per phase. Two phases, so the whole smoke is ~2x this plus the
# wrap-up -- long enough to hold a key down and see it repeat, short enough
# that the owner is not standing over the board.
_KBD_PHASE_S = 15


def keyboard(phase_s=_KBD_PHASE_S):
    """Keyboard bring-up and the #69 A/B, in one program: the kernel's driver
    passed on the input task, one pass a frame.

      1. ASCII -- the mode the code editor runs in. Each key reports ONCE on
         the press edge with no autorepeat, which is why a held key can only be
         faked (MOY_KBD_HOLD_MS) and why raw mode has to exist.
      2. RAW MATRIX -- `0x03`, five bytes per read, one bitmask per column. A
         HELD direction keeps firing here, which is what a running cart needs.
         Needs C3 firmware >= 2025-06-12; older firmware ignores the command and
         keeps sending ASCII, which the driver detects and falls back on.

    WHAT IT MEASURES. The C3 CLOCK-STRETCHES: real stalls of 21-60ms have been
    measured on this board. On the input task a stall blocks that task and
    never the loop, so each phase prints the loop's worst iteration beside the
    driver's worst transaction: `loop_max` should stay near the panel flush
    while `i2c max=` stays just as bad as the bus makes it.

    The screen shows a MOVING BAR. A frozen bar is a frozen loop, which is the
    one failure this program exists to make visible without a stopwatch.
    """
    import moy_input

    comp, canvas = _canvas()
    inp = moy_input.kernel()
    kbd = moy_input.keyboard()
    print("Moybyte kbd: available=%d raw_allowed=%s task_stack_free=%d"
          % (1 if kbd.available else 0, kbd.RAW_GAME_MODE, moy_input.task_stack_free()))
    if not kbd.available:
        print("Moybyte kbd: NOT FOUND on I2C0 -- nothing further to measure")

    comp.set_backlight(True)
    typed = []

    def _run_phase(label, raw, secs):
        kbd.set_game_mode(raw)
        for _ in range(4):                  # the flip is applied by a pass
            moy_input.kick()
            time.sleep_ms(20)
        base_n = kbd.stat_n
        base_o5 = kbd.stat_over5
        base_o20 = kbd.stat_over20
        base_to = kbd.stat_timeouts
        worst = 0
        over20 = 0
        frames = 0
        t_end = time.ticks_add(time.ticks_ms(), secs * 1000)
        while time.ticks_diff(t_end, time.ticks_ms()) > 0:
            t0 = time.ticks_ms()
            inp.begin_frame()
            k = inp.last_key
            if k and 0x20 <= k <= 0x7E:
                typed.append(chr(k))
                del typed[:-24]
            _paint_kbd(comp, canvas, label, kbd, inp, None, typed, frames, worst)
            moy_input.kick()
            frames += 1
            el = time.ticks_diff(time.ticks_ms(), t0)
            if el > worst:
                worst = el
            if el >= 20:
                over20 += 1
        print("Moybyte kbd: %-10s frames=%d loop_max=%dms over20=%d | i2c reads=%d "
              "max=%.1fms over5=%d over20=%d timeouts=%d raw_mode=%s"
              % (label, frames, worst, over20, kbd.stat_n - base_n,
                 kbd.stat_max_us / 1000.0, kbd.stat_over5 - base_o5,
                 kbd.stat_over20 - base_o20, kbd.stat_timeouts - base_to,
                 kbd.raw_mode))

    _run_phase("1 ascii", False, phase_s)
    _run_phase("2 raw", True, phase_s)
    if kbd.raw_unsupported:
        print("Moybyte kbd: RAW MODE UNSUPPORTED -- the C3 firmware ignored 0x03 "
              "(pre-2025-06-12). The driver fell back to ASCII + the hold latch, "
              "which is correct behaviour, but hold-to-move will stall.")
    # Back to ASCII (0x04). Sending the revert is the step an earlier attempt
    # missed, and skipping it leaves the keyboard streaming matrix bytes at the
    # code editor.
    kbd.set_game_mode(False)
    for _ in range(4):
        moy_input.kick()
        time.sleep_ms(20)
    print("Moybyte kbd: reverted to ASCII -- raw_mode=%s task_stack_free=%d"
          % (kbd.raw_mode, moy_input.task_stack_free()))
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


# ---------------------------------------------------------------------------
# STAGE 4 -- the SD card, which shares SPI2 with the panel.
# ---------------------------------------------------------------------------

SD_TEST_DIR = "/sd/moybyte"
SD_TEST_FILE = SD_TEST_DIR + "/mainline_smoke.txt"
SD_ROUNDS = 10          # write / flush / read / flush cycles in the torture loop
SD_PAYLOAD = 4096       # bytes per round -- several sectors, so DMA is exercised


def sd(rounds=SD_ROUNDS):
    """SD bring-up on the LIVE panel bus -- the stage that hangs boards.

    Every line of this is shaped by damage. The card and the ST7789 share ONE
    SPI host, and the ways that goes wrong do not announce themselves: the board
    stops, USB stays enumerated but dead, and there is no panic to read. So:

      NOTHING TOUCHES SD BEFORE THE PANEL. `moy_lcd.init()` runs
      `spi_bus_initialize()` once. `machine.SDCard` would run it AGAIN on a host
      esp_lcd already owns -- on a POPULATED card the mount even succeeds, and
      then the next panel init fails with something that names nothing
      ("can't convert '' to int"). The card attaches through the native `moy_sd`
      module instead: `sdspi_host_init_device` on the already-initialised host,
      the ESP-IDF "Sharing the SPI Bus" pattern, no bus re-init.

      THE DEVICE IS NEVER TORN DOWN. `with_sd_live` mounts once and keeps the
      card resident for the session. A per-op `sdspi_host_deinit` corrupts the
      shared bus/DMA state and the NEXT PANEL FLUSH silent-hangs the board --
      the write lands on SD, then resume freezes.

      CS PINS ARE LEFT ALONE. `TFT_CS` (12) and `SD_CS` (39) are driver-owned;
      re-creating a `Pin` on either afterwards causes the same hang. Only the
      unused LoRa `RADIO_CS` (9) is parked high.

      NO FLUSH INSIDE A SESSION. The desktop loop is single-threaded so SD ops
      run between frames; this smoke keeps that discipline and calls `comp.sync()`
      first, exactly as stage 6's `_with_sd_synced` does.

    THE BRACKET IS THE DIAGNOSTIC. Each phase prints before and after, so if the
    board does hang, the LAST LINE names the op that wedged it:

      `SD > sync` last   -> the pre-op DMA drain
      `SD > op` last     -> the SD transaction itself
      `SD < op` last     -> the next PANEL FLUSH, i.e. the shared-bus corruption
                            this whole design exists to avoid. `SD = panel ok`
                            is the line that says it did not happen.

    Then it does that `rounds` times, because one clean write proves nothing:
    bus/DMA corruption is cumulative, and a design that survives ten
    write-flush-read-flush cycles is a design that works.
    """
    import os
    import moybyte_sd

    comp, canvas = _canvas()
    log = _Log(comp, canvas, "SD SMOKE")
    comp.set_backlight(True)

    # The two modules each carry the bus facts, and they MUST agree: attaching
    # the card to the wrong host id is a hang with no message, and a constant
    # that drifted is exactly how that would happen.
    log.say("host lcd=%d sd=%d  cs lcd_sd=%d sd=%d"
            % (moy_lcd.SPI_HOST, moybyte_sd.SPI_HOST,
               moy_lcd.SD_CS, moybyte_sd.SD_CS))
    if (moy_lcd.SPI_HOST != moybyte_sd.SPI_HOST
            or moy_lcd.SD_CS != moybyte_sd.SD_CS):
        log.say("MISMATCH -- refusing to attach", RED)
        print("Moybyte sd: ABORT, moy_lcd and moybyte_sd disagree about the bus")
        return

    def _session(label, fn):
        """One bracketed SD session + the panel flush that proves the bus lived."""
        print("SD > sync   (%s)" % label)
        comp.sync()
        print("SD > op     (%s)" % label)
        t0 = time.ticks_ms()
        try:
            out = moybyte_sd.with_sd_live(fn)
            ms = time.ticks_diff(time.ticks_ms(), t0)
            print("SD < op     (%s) %dms" % (label, ms))
        except Exception as exc:        # noqa: BLE001 -- a failure is a RESULT
            ms = time.ticks_diff(time.ticks_ms(), t0)
            print("SD ! op     (%s) %dms FAILED: %s: %s"
                  % (label, ms, type(exc).__name__, exc))
            log.say("%s FAILED: %s" % (label, exc), RED)
            # The important half: a FAILED mount must not have poisoned the bus.
            log.say("panel after failure...", YELLOW)
            log.say("panel ok", GREEN)
            print("SD = panel ok after a failed %s" % label)
            return None, ms, exc
        log.say("%s ok %dms" % (label, ms), GREEN)
        print("SD = panel ok (%s)" % label)
        return out, ms, None

    # 1) Mount. The first with_sd_live is the one that attaches; every later
    #    call is a plain call-through, which is the point of keeping it resident.
    def _mount_probe():
        import moy_sd
        return (moy_sd.sector_count(), os.listdir("/sd"))

    out, _ms, exc = _session("mount", _mount_probe)
    if exc is not None:
        print("Moybyte sd: no card, or the attach failed. The panel survived it, "
              "which is the other thing this stage had to prove.")
        print("Moybyte sd smoke done -> REPL")
        return
    sectors, root = out
    log.say("card %d sectors (%d MB)" % (sectors, sectors // 2048))
    log.say("/sd: %s" % ", ".join(root[:6]) if root else "/sd: (empty)")
    print("Moybyte sd: sectors=%d (%dMB) root=%s" % (sectors, sectors // 2048, root))

    def _statvfs():
        st = os.statvfs("/sd")
        return (st[0] * st[2], st[0] * st[3])       # total, free bytes

    out, _ms, exc = _session("statvfs", _statvfs)
    if out:
        log.say("fs %dMB total %dMB free" % (out[0] >> 20, out[1] >> 20))
        print("Moybyte sd: fs total=%d free=%d" % out)

    # 2) The torture loop. One clean write proves nothing -- shared-bus and DMA
    #    corruption is cumulative, and the documented failure is "the write
    #    lands, then the NEXT flush freezes". So: write, flush, read back,
    #    flush, `rounds` times, verifying the bytes every round.
    payload = bytes(bytearray((i * 7 + 13) & 0xFF for i in range(SD_PAYLOAD)))
    bad = 0
    worst_w = 0
    worst_r = 0
    for n in range(rounds):
        def _write():
            try:
                os.mkdir(SD_TEST_DIR)
            except OSError:
                pass
            with open(SD_TEST_FILE, "wb") as fh:
                fh.write(payload)
            return len(payload)

        def _read():
            with open(SD_TEST_FILE, "rb") as fh:
                return fh.read()

        _o, wms, exc = _session("write %d/%d" % (n + 1, rounds), _write)
        if exc is not None:
            bad += 1
            break
        got, rms, exc = _session("read %d/%d" % (n + 1, rounds), _read)
        if exc is not None:
            bad += 1
            break
        if got != payload:
            bad += 1
            log.say("round %d: BYTES DIFFER" % (n + 1), RED)
            print("SD ! round %d: read back %d bytes, differ" % (n + 1, len(got)))
        worst_w = wms if wms > worst_w else worst_w
        worst_r = rms if rms > worst_r else worst_r

    # 3) A full-rate flush burst AFTER all that: the corruption this design
    #    guards against shows up as a hang on a LATER flush, not the next one.
    log.say("flush burst...", YELLOW)
    t0 = time.ticks_ms()
    for _ in range(60):
        comp.flush()
    comp.sync()          # the 60th is still going out -- time all of it
    burst = time.ticks_diff(time.ticks_ms(), t0)
    log.say("60 flushes in %dms" % burst, GREEN)
    print("Moybyte sd: 60 post-session flushes in %dms (%.1fms each)"
          % (burst, burst / 60.0))

    log.say("rounds=%d bad=%d" % (rounds, bad), GREEN if not bad else RED)
    print("Moybyte sd: rounds=%d bad=%d write_max=%dms read_max=%dms"
          % (rounds, bad, worst_w, worst_r))
    print("Moybyte sd smoke done -> REPL "
          "(the card stays MOUNTED at /sd -- that is deliberate)")


# ---------------------------------------------------------------------------
# STAGE 5 -- I2S audio into the MAX98357 mono amp.
# ---------------------------------------------------------------------------

# An ascending phrase, as (Hz, seconds). Deliberately NOT a single tone: a
# rising scale makes a wrong sample rate or a stuck oscillator audible, where
# one steady beep sounds fine at any speed.
_AUDIO_SCALE = ((262, 0.25), (330, 0.25), (392, 0.25), (523, 0.45))


def audio():
    """I2S bring-up -- and a measurement that works even if the amp is silent.

    THE PROBLEM WITH TESTING AUDIO BY EAR. "I hear nothing" has at least four
    causes -- no native module, no I2S channel, a synth producing silence, or an
    amp that is not wired/powered -- and an ear cannot tell them apart. So this
    smoke instruments the SEAM: `moy_audio.stats()[1]` is the frames the I2S
    peripheral has actually ACCEPTED, which is the last thing measurable on
    this side of the wire.

      frames climbing at ~22050/s  -> the synth renders and the peripheral
                                      consumes. Silence past this point is the
                                      AMP or its wiring, not the firmware.
      frames flat                  -> nothing is feeding I2S. The output line
                                      above says why.
      frames climbing at the WRONG rate -> the clock. Everything would play at
                                      the wrong pitch, which by ear just sounds
                                      like "a bit off".

    That is the same rule the project learned the expensive way on the audio
    seam: measure BOTH sides, and prefer a number to an ear.

    The bank is `AudioBank.default()` -- the coin/jump/thud starter set every
    empty cart gets -- so this needs no card and no cart.
    """
    from audio import AudioBank
    from audio_session import AudioSession, console_volume, native

    comp, canvas = _canvas()
    log = _Log(comp, canvas, "AUDIO SMOKE", keep=18)
    comp.set_backlight(True)

    na = native()
    if na is None:
        log.say("moy_audio ABSENT -- silent by design", RED)
        print("Moybyte audio: no native module in this image; nothing to measure")
        print("Moybyte audio smoke done -> REPL")
        return
    bank = AudioBank.default()
    dev = AudioSession(bank, "cart")        # the first focus starts the output
    st, why = na.out()
    rate = na.rate()
    ok_out = st == na.OUT_RUNNING
    log.say("output: %s %s @%dHz" % ("running" if ok_out else "DOWN", why, rate),
            GREEN if ok_out else RED)
    print("Moybyte audio: out=%d (%s) rate=%d bank sfx=%d music=%d"
          % (st, why, rate, len(bank.sfx), len(bank.music)))

    def _frames():
        return na.stats()[1]

    def _play(label, fn, secs):
        """Run `fn`, hold for `secs`, and report the frames the peripheral took."""
        f0 = _frames()
        t0 = time.ticks_ms()
        fn()
        t_end = time.ticks_add(t0, int(secs * 1000))
        log.say("%s ..." % label, GREY)
        i = 0
        while time.ticks_diff(t_end, time.ticks_ms()) > 0:
            i += 1
            if i % 5 == 0:      # ~10Hz: a live counter, not a repaint benchmark
                log.say_replace("%s  frames+%d  active=%s"
                                % (label, _frames() - f0, dev.is_active()), GREY)
            time.sleep_ms(20)
        ms = time.ticks_diff(time.ticks_ms(), t0)
        got = _frames() - f0
        hz = (got * 1000 // ms) if (ms and got >= 0) else -1
        ok = hz > 0 and abs(hz - rate) * 20 < rate      # within 5%
        log.say_replace("%-10s %6d frames %5dHz" % (label, got, hz),
                        GREEN if ok else RED)
        print("Moybyte audio: %-10s %dms frames=%d measured=%dHz (nominal %d)%s"
              % (label, ms, got, hz, rate, "" if ok else "  <-- OFF"))
        return hz

    rates = []
    rates.append(_play("silence", lambda: None, 1.0))
    for (hz, dur) in _AUDIO_SCALE:
        _play("beep %d" % hz, lambda hz=hz, dur=dur: dev.beep(hz, dur), dur + 0.1)
    for n in range(min(3, len(bank.sfx))):
        rates.append(_play("sfx %d" % n, lambda n=n: dev.sfx(n), 1.0))
    if bank.music:
        rates.append(_play("music 0", lambda: dev.music(0, True), 5.0))
        dev.music_stop()
    # The console's level is a real verb: 0 must SILENCE the amp while the
    # peripheral keeps taking frames -- the pair of facts the frames counter
    # can show and an ear cannot.
    was = console_volume()
    console_volume(0)
    _play("vol 0", lambda: dev.sfx(0), 1.2)
    console_volume(was)

    good = [r for r in rates if r > 0]
    if good and all(abs(r - rate) * 20 < rate for r in good):
        log.say("I2S consuming at rate -- OK", GREEN)
        print("Moybyte audio: VERDICT the peripheral consumes at the nominal "
              "rate. If it was silent, look at the amp and its wiring, not here.")
    else:
        log.say("frame rate WRONG or flat", RED)
        print("Moybyte audio: VERDICT the output did not take frames at %dHz -- "
              "the feed is the suspect, not the amp. out=%d %s" % (rate, st, why))
    dev.close()
    print("Moybyte audio smoke done -> REPL (the feeder task keeps running)")


class _Log:
    """A scrolling status list on the glass, so a stage is watchable without a
    serial terminal in front of you.

    Repaints the whole list every time. That is not laziness: 20 lines of 8px
    text is nothing beside a 4KB SD write or an I2S block, and a partial-repaint
    scheme here would be a second thing that could be wrong in a program whose
    only job is to say what IS wrong.
    """

    def __init__(self, comp, canvas, title="SMOKE", keep=20):
        self.comp = comp
        self.canvas = canvas
        self.title = title
        self.keep = keep
        self.rows = []

    def say(self, msg, col=WHITE):
        self.rows.append((msg, col))
        del self.rows[:-self.keep]
        self.paint()

    def say_replace(self, msg, col=WHITE):
        """Overwrite the last row -- a live counter, not another line."""
        if self.rows:
            self.rows[-1] = (msg, col)
        else:
            self.rows.append((msg, col))
        self.paint()

    def paint(self):
        c = self.canvas
        c.cls(DARK)
        c.print(self.title, 6, 4, YELLOW)
        y = 18
        for (text, colour) in self.rows:
            c.print(text[:39], 6, y, colour)
            y += 10
        _present(self.comp, c)


def _raw_str(r):
    """The last RAW GT911 sample, straight off the wire.

    The driver's mapping turns raw into canvas coords, so printing both is what
    makes a mirrored axis a two-second diagnosis instead of a guess: raw rising
    while mapped falls names the flipped axis outright.
    """
    return "-" if r is None else "(%d,%d)" % (r[0], r[1])


def _i2cstat(t):
    return ("reads=%d max=%.1fms over5=%d over20=%d int_edges=%d skipped=%d"
            % (t.stat_n, t.stat_max_us / 1000.0, t.stat_over5, t.stat_over20,
               t.stat_int_edges, t.stat_skipped))
