"""Moybyte T-Deck device backend, MAINLINE build -- the shared console on the S3.

The third `run_desktop` in the tree, and the one that had the least to invent:
the shared boot spine (`runtime/device_boot.py`) already owns the splash, the
cart seed/scan, the Lua probe, the OTA verdict and the frame cadence, and the
shared `console.Workstation` owns every pixel. What is left here is the part
that is genuinely this board's hardware, and one thing that is new.

WHAT DIFFERS FROM THE FORK BUILD'S `moy_runtime.py`, which drives the same glass:

  * the panel. There, a Python `moy_compositor.Compositor` drives `lcd_bus`
    band by band and owns the bounce buffers, the completion counter and the
    pacing stats. Here that whole machine is `moy_lcd`, one C module that also
    owns the SPI host, and `tdeck_panel.TDeckCompositor` is the thin ping-pong
    over its kick/pump/drain split. The STRATEGY is the same one, including the
    overlap: `flush()` queues the first bands and returns, the rest are fed
    while this loop renders the next frame, and `comp.sync()` is a real fence.
  * no LVGL, so no `handler.deinit()` takeover and no `tdeck_display`.
  * a SERIAL DEV CHANNEL, which that board does not have. See below -- it is
    the most consequential difference and the one most likely to be doubted.

WHAT IS DELIBERATELY IDENTICAL: the input drivers, the SD lifecycle, the cart
API, the audio backend, the WiFi service, the OTA updater and every module the
console is built from are the SAME FILES, staged from the fork build's tree by
`board.toml`. Two builds of one board must not become two consoles.
"""

import time

from console import Pointer, Workstation, wire_workstation_core, _cursor_delta
# The boot spine + frame pump, shared with the P4 and the fork build (#161
# Phase 4/5, canonical: runtime/device_boot.py). The steps that used to be
# written per board -- boot splash, cart seed+scan, the Lua runtime probe, the
# OTA verdict + rollback confirm, the frame cadence and its pacing debt -- live
# there. Everything below that is not one of those is hardware.
from device_boot import DeviceBoot, FramePump, OtaHealth
from carts_data import CARTS          # generated from system_carts/ at build time
from device_util import (_ticks_ms, _ticks_diff, _diag_note, _diag_log,
                         sram_census)
from device_wifi import make_wifi, autoconnect_wifi
from device_input import TrackBall, Touch
from device_audio import make_audio
from device_canvas import DeviceCanvas, _LayerComp
from device_api import make_api
from device_diag import (_diag_flush, _diag_perf_sample, _diag_hitch,
                         _diag_drawbrk, _diag_loop, _diag_i2cstat, _diag_pump,
                         HITCH_MS)

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
# THE LORE SAYS THIS CANNOT WORK ON THIS BOARD. The lore is half right, and the
# half that is wrong cost the project a test harness, so the reasoning is here
# rather than in a commit message.
#
# What was recorded (CLAUDE.md, and the revert 4faf07a): "this fork's USB-CDC
# stack has no at-arrival interrupt-char scan, so Ctrl-C/commands never arrive",
# and "select.poll reports stdin ALWAYS-READY even when empty, so poll-then-
# readline becomes a blocking read that stalled the loop ~30s".
#
# The first clause is FALSE, and checkably so. The shipping fork is MicroPython
# v1.27.0 and this build is v1.28.0, and every file on the CDC receive path --
# shared/tinyusb/mp_usbd_cdc.c, mp_usbd.c, shared/runtime/interrupt_char.c,
# sys_stdio_mphal.c, ports/esp32/usb.c, uart.c, main.c -- is byte-identical
# between them. `tud_cdc_rx_cb` does scan for the interrupt char at arrival
# (mp_usbd_cdc.c: `if (data_char == mp_interrupt_char) { ...
# mp_sched_keyboard_interrupt(); }`) and is linked into the shipping image. The
# same revert commit says so out loud in its own summary: "without a reader in
# flight, Ctrl-C drops to a live REPL".
#
# The second clause is a REAL observation with a different cause. Two mechanisms
# explain it, and neither is a broken poll:
#
#   1. `sys.stdin.readline()` blocks PER CHARACTER (sys_stdio_mphal.c's
#      stdio_read loops on mp_hal_stdin_rx_chr, which never returns empty). So
#      ONE byte in the ring buffer makes poll correctly report ready, and then
#      readline waits for a newline that may never come. That is the ~30s stall,
#      exactly.
#   2. Something was putting that byte there. `MICROPY_HW_ENABLE_UART_REPL` is
#      on, and UART0's ISR feeds the SAME `stdin_ringbuf` -- so noise on a
#      floating U0RXD (GPIO44, exposed on this board's expansion header) is
#      indistinguishable from a typed character.
#
# So this channel is built to survive both. It NEVER calls readline: it reads
# ONE BYTE at a time with `sys.stdin.read(1)`, only ever after `poll(0)` said
# MP_STREAM_POLL_RD (which mphalport.c sets only when `ringbuf_peek() != -1`),
# accumulating until a newline. A byte read is a byte consumed, so noise costs a
# bounded few bytes per frame and can never park the loop; a partial line is
# dropped when it grows past LINE_MAX. And it COUNTS what it swallowed, so the
# PERF line reports `rx=` -- if bytes stream in and no command ever completes,
# that number says "something is injecting into stdin" instead of leaving a
# mystery hang.
#
# Set False to remove the channel entirely (the loop is then byte-identical to
# one without it). If `rx=` climbs on an idle board, mechanism 2 is real: build
# with `MICROPY_HW_ENABLE_UART_REPL (0)` in the board header, which takes UART0's
# ISR off the shared ring buffer. If RX turns out to be genuinely unusable, the
# S3's USB-Serial/JTAG peripheral fills the ring from a TRUE hardware ISR
# (usb_serial_jtag.c) -- that is what the P4's UART behaves like, and on the S3
# it is mutually exclusive with CDC.
SERIAL_CMDS = True
SERIAL_LINE_MAX = 96        # a partial line longer than this is noise; drop it
SERIAL_BYTES_PER_FRAME = 64  # bounded drain: noise cannot own the frame
# Bytes that may arrive without EVER completing a command before the channel
# gives up on itself. A real operator types a line within a few dozen bytes;
# four kilobytes of newline-free traffic is a byte SOURCE, not a person. Rather
# than spend a slice of every frame chewing it forever, the channel disarms and
# says so once -- turning a permanent drag on the desktop into one serial line
# naming the condition. Re-arm from the REPL by re-entering run_desktop.
SERIAL_NOISE_LIMIT = 4096

# #183: print a phase bracket around every SD session. This board has no REPL to
# interrogate once the desktop owns the loop, so the trace IS the diagnostic --
# and it only fires on commits, never per frame.
SD_TRACE = True


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts + keyboard + touch, carts on SD.

    The order below is the shared one (`wire_workstation_core` is the canonical
    service wiring for the host and both boards); what is board-specific is the
    panel bring-up at the top, the input trio, the SD/panel bus gate, and the
    serial channel.
    """
    from tdeck_panel import TDeckCompositor, set_backlight
    from moybyte.input import InputState, TDeckKeyboard, InputPoller
    import moybyte_sd
    import moy_carts

    comp = TDeckCompositor(nfbs=2)
    canvas = DeviceCanvas(comp)

    # -- the shared boot spine (#45/#161) ----------------------------------
    # DeviceBoot owns the boot splash + its progress bar, the cart seed/scan,
    # the Lua runtime probe and the "first frame in Nms" report. The two things
    # that differ here are its arguments: the serial prefix and the panel light.
    #
    # The panel is dark until the first frame ships, which keeps the ST7789's
    # power-on GRAM noise off the glass -- at the cost of making a slow boot
    # look like a dead board, and a FIRST boot is slow because every built-in
    # cartridge is written to SD before anything composes. The splash is how
    # that wait becomes legible, on the glass and on the wire.
    boot = DeviceBoot(canvas, comp, set_backlight, "Moybyte")
    boot.note("starting")

    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    # Share the keyboard's I2C object: one bus, one driver instance, so the
    # poller below owns every transaction on it.
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    pointer = Pointer(canvas.w, canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    poller = None
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

    sram_census("rd-entry")
    boot.note("loading cartridges")
    # SD shares the panel's SPI host, so the mount must bracket the whole
    # seed+scan; `with_sd_live` attaches once and keeps the card resident.
    carts, carts_root = boot.load_carts(moy_carts, CARTS,
                                        session=moybyte_sd.with_sd_live,
                                        media="SD")
    sram_census("carts")
    boot.note("building the desktop")
    ws = Workstation(comp, canvas, inp, carts)
    sram_census("console")

    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster plays on its own off-screen canvas and `wm.composite_game` upscales
    # it through `DeviceCanvas.blit_game`. No native kernel -> None, so the
    # Player refuses the cart cleanly instead of crawling per-pixel.
    def _mk_game_canvas(w, h):
        if getattr(canvas, "_gfx", None) is None:
            return None
        return DeviceCanvas(_LayerComp(int(w), int(h), canvas._gfx))

    ws.make_game_canvas = _mk_game_canvas
    lua_runtime = boot.lua_runtime(ws, log=lambda m: _diag_note("carts", m))

    _sd_traced = [False]

    def _with_sd_synced(fn):
        """Every SD session on this board, with the panel drained first.

        `comp.sync()` is load-bearing now that the flush overlaps: a frame's
        bands can still be in flight when this is called, and an SD op that
        overlaps a panel DMA on the shared host is the documented way to hang
        this board. It drains; it is not a formality.

        The BRACKET is the diagnostic (#183). An editor commit can wedge this
        board with nothing on serial, so each phase says its name and whichever
        line is LAST before the silence identifies the op:
          "> sync" -- the pre-op drain
          "> op"   -- the SD write itself
          "< op"   -- the NEXT PANEL FLUSH, i.e. the shared-bus corruption;
                      "= panel ok" below is what says it did not happen.
        Costs nothing when quiet: SD sessions happen on commits, not per frame.
        """
        if not SD_TRACE:
            comp.sync()
            return moybyte_sd.with_sd_live(fn)
        print("SD > sync")
        _t = _ticks_ms()
        comp.sync()
        print("SD > op (sync %dms)" % _ticks_diff(_ticks_ms(), _t))
        _t = _ticks_ms()
        try:
            return moybyte_sd.with_sd_live(fn)
        finally:
            print("SD < op %dms" % _ticks_diff(_ticks_ms(), _t))
            _sd_traced[0] = True

    def _before_slim(_ws):
        # Set BEFORE slim_carts so the store can reload what the diet drops.
        _ws._with_sd = _with_sd_synced
        try:
            import moy_ota
            _ws.updater = moy_ota.OtaUpdater(_with_sd_synced)
        except Exception as exc:  # noqa: BLE001
            print("Moybyte: OTA updater unavailable:", exc)

    # The shared service wiring: api/audio/lua + store/root/can_manage + WiFi +
    # the #66 slim_carts diet + pointer/keyboard + the boot loads. One canonical
    # order for the host and every board.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          make_audio=make_audio,
                          lua_runtime=lua_runtime, before_slim=_before_slim,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    if getattr(ws, "updater", None) is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:  # noqa: BLE001
            print("Moybyte: OTA wifi wiring failed:", exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte: reboot hook unavailable:", exc)
    # WEB CONSOLE: the wasm console, BAKED into this image (native/moy_web) and
    # served from the board. Constructed, not started -- injecting it only makes
    # the Settings row appear, and the row is what brings the radio up.
    #
    # The risk that is this board's alone: the WLAN stack reserves internal RAM
    # the LCD DMA flush needs, which is why boot does NOT autoconnect. Turning
    # the row on takes that risk knowingly, exactly as UPDATE ONLINE does.
    try:
        from moy_webhost import make_webhost, TDECK_WEB_DIR
        ws.webhost = make_webhost(ws, carts_root, TDECK_WEB_DIR,
                                  autoconnect=autoconnect_wifi,
                                  with_sd=_with_sd_synced)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte: web console unavailable:", exc)

    try:
        import moybyte_diag as diag
    except Exception:  # noqa: BLE001
        diag = None
    if diag is not None:
        try:
            ws.perf_capture = bool(getattr(ws, "diag_live", False))
        except Exception:  # noqa: BLE001
            pass
    _diag_log("boot", "desktop running kb=%d ball=%d touch=%d poller=%d"
              % (1 if keyboard.available else 0, 1 if ball.available else 0,
                 1 if touch.available else 0, 1 if poller is not None else 0),
              diag)
    sram_census("desktop-up")

    # #66/#67 SRAM diet: everything needing boot-time internal RAM has taken it
    # by here, so the Lua allocator's headroom floor drops 48->24KB. BOTH
    # runtimes -- moycore has its own allocator with its own floor, and a cart
    # left on the 48KB floor sits at ~97% PSRAM, the measured-2x-slower regime,
    # with nothing saying so.
    for _mod in ("moy_lua", "moycore"):
        try:
            _m = __import__(_mod)
            _fl = getattr(_m, "set_sram_floor", None)
            if _fl is not None:
                _diag_log("boot", "%s sram floor=%dKB" % (_mod, _fl(24)), diag)
        except Exception:  # noqa: BLE001
            pass

    # Say what became of the last update before anything overwrites the evidence
    # (#53). The rollback CONFIRM is NOT made here: reaching this line proves the
    # desktop was CONSTRUCTED, not that a pixel reached the glass, and an image
    # that never paints has shipped here before (#56). FramePump.tail fires it
    # from the loop, once frames are really going out.
    _ota = OtaHealth(ws, log=lambda m: _diag_log("ota", m, diag))
    _ota.boot_check()

    import gc
    gc.collect()        # defrag after the heavy boot so the flush bounce has SRAM

    serial = _SerialChannel(ws, pointer) if SERIAL_CMDS else None
    if serial is not None:
        _diag_log("boot", "serial dev channel %s"
                  % ("armed" if serial.armed else "unavailable"), diag)

    pump = FramePump(boot, _ota, fps_cap)
    _backlight_on = boot.lit
    _diag_at = _ticks_ms() + 3000
    _flush_at = _ticks_ms() + 5000
    _prev_cart_err = None
    _cart_prev = False
    # [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep, hi, hp] ms per frame,
    # averaged and zeroed every diag tick. HITCH only fires on SPIKES, so a
    # steady per-frame cost that never crosses HITCH_MS is invisible without it.
    _acc = [0] * 12
    boot.start_frames(ws)

    while True:
        now, dt = pump.begin()
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
        _t_kbd = _ticks_diff(_ticks_ms(), now)

        _t0 = _ticks_ms()
        inp.begin_frame()
        counts, click = ball.poll()
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        if ws.screen == "menu" and ws.menu_view == "code":
            ws.nav(nx, ny)                      # in the editor the ball moves the caret
        else:
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)
        tp = touch.poll()
        pointer.down = tp is not None
        # Touch.poll holds a held finger's last point across the frames the GT911
        # produced no fresh sample for (#74), so `down` is a real LEVEL and a drag
        # survives them; `fresh` marks the repeats so kinetic scrolling doesn't
        # measure finger speed against a sample the hardware never took.
        pointer.fresh = getattr(touch, "fresh", True)
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:
                click = True
        if serial is not None:
            if serial.poll(ws):
                click = serial.click or click
            if serial.quit:
                print("Moybyte desktop: serial quit -> REPL")
                return
        pointer.click = click
        pointer.tick(now)
        _t_inp = _ticks_diff(_ticks_ms(), _t0)

        _frames_before = getattr(ws, "_frames_drawn", 0)
        _t0 = _ticks_ms()
        canvas.sync_back()      # re-point at the compositor's new BACK buffer
        _t_sb = _ticks_diff(_ticks_ms(), _t0)
        _t0 = _ticks_ms()
        _t_hi = 0               # defined before the try: a crash mid-frame
        _t_hp = 0               # still logs a LOOP line
        try:
            ws.handle_input()
            _t_hi = _ticks_diff(_ticks_ms(), _t0)
            ws.handle_pointer()
            _t_hp = _ticks_diff(_ticks_ms(), _t0) - _t_hi
            ws.frame(dt)        # draw + composite + flush
        except Exception as exc:  # noqa: BLE001 -- one bad frame must not brick it
            _diag_log("frame error", exc, diag)
            print("Moybyte frame error:", exc)
            _diag_flush(diag, ws)
            gc.collect()
        _t_ws = _ticks_diff(_ticks_ms(), _t0)

        # #183: close the SD bracket. A DRAWN frame here means the first panel
        # flush after the SD session completed, so the bus survived it.
        if _sd_traced[0] and getattr(ws, "_frames_drawn", 0) != _frames_before:
            _sd_traced[0] = False
            print("SD = panel ok")

        # THE IDLE-BAND DRAIN (#40/#66). The overlapped flush RETURNS with bands
        # still queued, and `console.frame()`'s redraw gate returns BEFORE
        # comp.flush() on a frame that changes nothing -- so a UI that goes quiet
        # right after a paint leaves the last frame partly on the glass, with the
        # 2ms pump timer as the only thing that finishes it. That is a real
        # dependency on a feeder whose constructor is allowed to fail (see
        # tdeck_panel): if the timer never started, the bottom of the screen
        # would sit stale until the next repaint. So when THIS frame did not
        # draw, drain. It fires once per idle stretch (the drain zeroes the band
        # state) and is a no-op the rest of the time.
        #
        # `device_boot`'s docstring names this as one of the genuinely
        # board-specific steps that stay in run_desktop; it is the fork's
        # moy_runtime line, and it was missing here.
        if getattr(ws, "_frames_drawn", 0) == _frames_before:
            try:
                comp.sync()
            except Exception:  # noqa: BLE001 -- an idle tidy-up must never throw
                pass

        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _prev_cart_err:
                _prev_cart_err = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _prev_cart_err = None

        # #45: the backlight booted OFF so the ST7789's power-on GRAM noise is
        # never lit. Turn it on the instant a real frame has been composed AND
        # flushed -- _frames_drawn ticks past 0 only inside frame() after
        # comp.flush() -- so the first sight is the desktop, not garbage.
        if not _backlight_on and getattr(ws, "_frames_drawn", 0) > 0:
            try:
                set_backlight(True)
            except Exception as _bl:  # noqa: BLE001
                print("Moybyte backlight on failed:", _bl)
            _backlight_on = True

        # The shared once-only tail: the splash hands the glass over and reports
        # how long the desktop took to reach it, and the OTA rollback confirm
        # fires now that frames are really going out. Both self-disarming; both
        # AFTER the backlight gate, which stays here because it is board hardware.
        pump.tail(ws)

        _tnow = _ticks_ms()
        _t_diag = 0
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_at) >= 0:
            _diag_at = _tnow + 3000
            if ws.perf_capture != _live:
                ws.perf_capture = _live     # capture follows Settings -> PERF DIAG
            try:
                diag.ECHO_LIVE = _live
            except Exception:  # noqa: BLE001
                pass
            _diag_perf_sample(diag, ws)
            _diag_drawbrk(diag, ws)
            _diag_loop(diag, ws, _acc)
            for _i in range(12):
                _acc[_i] = 0
            # #66 lever 4: the bounce-feed pacing of the flush overlap. This is
            # the ONE line that says whether a disappointing fps is the bus or
            # the feeder -- idle/gaps ~ 0 means the bands go out as fast as the
            # SPI takes them and the ceiling is real transfer time. It prints
            # nothing unless comp.bounce_flush, so a serialized build is silent
            # rather than lying, and the LINE APPEARING is itself the first
            # proof the overlap is live in an image.
            _diag_pump(diag, comp)
            _diag_i2cstat(diag, keyboard, touch)
            if serial is not None:
                serial.report(diag)
            _t_diag = _ticks_diff(_ticks_ms(), _tnow)

        # #68 kid mode: the periodic diag->SD write costs 80-120ms and IS a
        # felt stutter during play, so it needs PERF DIAG *and* DIAG SD LOG.
        # The cart-exit and crash flushes below stay unconditional.
        _cart_now = ws.cart is not None
        _t_sd = 0
        if diag is not None and _cart_prev and not _cart_now:
            _t_sd = _diag_flush(diag, ws)   # cart exited: persist the ring
        _cart_prev = _cart_now
        if (diag is not None and _live and getattr(ws, "diag_sd", False)
                and _ticks_diff(_tnow, _flush_at) >= 0):
            _flush_at = _tnow + (20000 if ws.cart is not None else 5000)
            _t_sd = _diag_flush(diag, ws)

        elapsed = _ticks_diff(_ticks_ms(), now)
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t_kbd, _t_inp, _t_sb, _t_ws,
                        _t_diag, _t_sd, 0, _t_hi, _t_hp)
        _t_sleep = pump.pace(ws, elapsed)
        # Accumulated BEFORE the sleep, so `frame` is work and `sleep` is carried
        # separately -- a paced loop must not read as a slow one.
        _acc[0] += 1
        _acc[1] += elapsed
        _acc[2] += _t_kbd
        _acc[3] += _t_inp
        _acc[4] += _t_sb
        _acc[5] += _t_ws
        _acc[7] += _t_diag
        _acc[8] += _t_sd
        _acc[9] += _t_sleep
        _acc[10] += _t_hi
        _acc[11] += _t_hp
        if _t_sleep:
            time.sleep_ms(_t_sleep)


class _SerialChannel:
    """Line commands over USB-CDC stdin, read one byte at a time.

    See SERIAL_CMDS above for why this exists at all and why the byte-at-a-time
    reader is not fussiness. In one sentence: `poll()` is trustworthy (the esp32
    port sets MP_STREAM_POLL_RD only when the stdin ring buffer is non-empty),
    `readline()` is not (it blocks per character until a newline that noise will
    never supply), so this reads exactly the bytes poll promised and no more.

    The command set is the P4's, minus what this board does not have (no
    windows, no BLE, no PPA) and minus `swipe`/`drag`, which want a windowed
    desktop to gesture at. `tools/p4_autotest.py`'s approach -- drive the
    console over serial, assert against `state` -- points at this directly.

      state           one-line JSON: screen / frames / cart / wifi / scroll
      tap <x> <y>     a synthetic tap at canvas coords
      tap <name>      tap a named bar button (any ws.layout.<name>_btn rect)
      run [name]      select the first cart whose title matches, and run it
      diag 0|1        the diagnostic frame-eaters (perf_capture + the FPS chip)
      skip 0|1        the #77 frameskip gate
      gov 0|1         the #63 frame governor
      mem             a forced collect + the live/free split
      py <code>       eval/exec one line against the LIVE console
      quit            leave the desktop for the REPL
    """

    def __init__(self, ws, pointer):
        self.pointer = pointer
        self.click = False
        self.quit = False       # `quit` asked for the REPL; run_desktop returns
        self.buf = ""
        self.rx = 0             # bytes swallowed -- the "is something injecting?" number
        self.lines = 0          # complete commands dispatched
        self.dropped = 0        # over-long partial lines thrown away
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
        if cmd == "py" and len(parts) > 1:
            code = line.split(None, 1)[1]
            env = {"ws": ws, "wm": ws.wm, "pointer": self.pointer}
            try:
                try:
                    print("PY %r" % (eval(code, env),))
                except SyntaxError:
                    exec(code, env)       # noqa: S102 -- dev-board serial only
                    print("PY ok")
            except Exception as exc:  # noqa: BLE001
                print("PY ERR %s: %s" % (type(exc).__name__, exc))
            return
        print("REMOTE ? %s" % line)


def _remote_state(ws):
    """One-line JSON snapshot for the `state` command -- the assertion source an
    on-glass harness reads instead of pixels. Every field best-effort: a broken
    subsystem reads as an error string, never a crash that kills the loop."""
    st = {}
    try:
        st["screen"] = ws.screen
        st["frames"] = getattr(ws, "_frames_drawn", None)
        st["cart"] = (getattr(ws, "cart", None) or {}).get("title")
        st["cart_error"] = getattr(ws, "cart_error", None)
        st["diag"] = bool(getattr(ws, "diag_live", False))
        st["costs"] = dict(getattr(ws, "costs", {}) or {})
        # The process back-stack, which on this tier IS the whole window
        # model: `ws.screen` is only a read-only projection of its top.
        st["stack"] = list(getattr(ws.wm, "_stack", ()) or ())
    except Exception as exc:  # noqa: BLE001
        st["ws_err"] = str(exc)
    try:
        sl = ws.settings_layer
        sr = sl.scroll
        st["settings"] = {
            "set_top": sl.set_top, "sel": sl.set_msel,
            "rows": len(sl._settings_rows()),
            "offset": None if sr is None else sr.offset,
            "wifi_view": bool(sl.wifi_view),
        }
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
        claims = {}
        for _app, _text in getattr(ws, "_apps", ()):
            claims[_app.id] = sum(1 for c in ws._all_carts if _app.is_app(c))
        st["app_claims"] = claims
    except Exception as exc:  # noqa: BLE001
        st["app_err"] = str(exc)
    return st
