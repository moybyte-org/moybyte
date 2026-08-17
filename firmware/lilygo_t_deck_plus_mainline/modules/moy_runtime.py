"""Moybyte T-Deck device backend -- the shared console on the S3.

The `run_desktop` that had the least to invent: the shared boot spine
(`runtime/device_boot.py`) already owns the splash, the cart seed/scan, the Lua
probe, the OTA verdict and the frame cadence, and the shared
`console.Workstation` owns every pixel. What is left here is the part that is
genuinely this board's hardware.

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

import time

from console import Pointer, Workstation, wire_workstation_core, _cursor_delta
# The boot spine + frame pump, shared with the P4 (#161
# Phase 4/5, canonical: runtime/device_boot.py). The steps that used to be
# written per board -- boot splash, cart seed+scan, the Lua runtime probe, the
# OTA verdict + rollback confirm, the frame cadence and its pacing debt -- live
# there. Everything below that is not one of those is hardware.
from device_boot import DeviceBoot, FramePump, IdleBlank, OtaHealth
from carts_data import CARTS          # generated from system_carts/ at build time
from device_util import (_ticks_ms, _ticks_diff, _diag_note, _diag_log,
                         sram_census)
from device_wifi import make_wifi, autoconnect_wifi
from device_input import TrackBall, Touch
from device_audio import make_audio
from device_canvas import DeviceCanvas, _LayerComp
from device_api import make_api
from device_diag import (_diag_flush, _diag_perf_sample, _diag_hitch,
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


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts + keyboard + touch, carts on SD.

    The order below is the shared one (`wire_workstation_core` is the canonical
    service wiring for the host and both boards); what is board-specific is the
    panel bring-up at the top, the input trio, the SD/panel bus gate, and the
    serial channel.
    """
    import tdeck_panel
    from tdeck_panel import TDeckCompositor, set_backlight
    from moybyte.input import InputState, TDeckKeyboard, InputPoller
    import moybyte_sd
    import moy_carts

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
    # Shared with the P4 (#58) -- see IdleBlank for the three behaviours a
    # per-board copy got wrong. `power <secs>` retunes it; 0 disables.
    idle = IdleBlank(set_backlight, POWER_SAVE_MS)
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

    # `state`'s psave field reports the LIVE timeout; the dev channel's `power`
    # retune keeps it current from here on.
    ws._psave_ms = POWER_SAVE_MS

    serial = None
    if SERIAL_CMDS:
        from dev_channel import DevChannel
        # env: the loop objects `py` probes reach beyond ws/wm/pointer -- the
        # same names the P4 exposes, minus its game canvas.
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
                            env={"comp": comp, "boot": boot})
        _diag_log("boot", "serial dev channel %s"
                  % ("armed" if serial.armed else "unavailable"), diag)

    pump = FramePump(boot, _ota, fps_cap)
    if serial is not None:
        serial.env["pump"] = pump   # created just above; same py-scope as the P4
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
        _serial_ran = False
        if serial is not None:
            _serial_ran = serial.poll(ws)
            click = serial.click or click
            if serial.quit:
                print("Moybyte desktop: serial quit -> REPL")
                return
        # Idle screen blank, after EVERY input source has been read and before
        # the pointer reaches the console -- that ordering is what lets the
        # waking touch be swallowed instead of pressing what it landed on. A
        # dev command (or a scripted swipe frame) counts as activity, so an
        # unattended harness session never fights the blank.
        click = idle.tick(now, (tp is not None) or nx or ny or click
                          or _serial_ran
                          or bool(getattr(inp, "last_key", None)),
                          ws, pointer, click)
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
            # DRAWBRK says how much of the frame is `render`; this says WHICH
            # native op render is. Two of its buckets are the only instruments
            # this port has for the two things that separate it from the fork:
            # `layer=` is the draw_layer window copy, i.e. what the async layer
            # copy above is meant to take to ~0 on a full-screen-layer cart, and
            # `fill=` is the cls bucket, which is what a colour `background()`
            # actually costs (a 153,600 B PSRAM write -- Brick Siege's whole
            # `bg=`). Both are already measured every frame under perf capture;
            # until now nothing printed them.
            _diag_draw2(diag, ws)
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
            # The web console's SOCKET state. Without it, "serving but nobody
            # connected" and "never started" look identical from the outside --
            # which is how a bound-but-unpolled socket read as a network fault.
            _diag_webhost(diag, ws)
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

        # Serve the web console. It is created above but nothing drove it, so the
        # socket bound (the board answers a ping, the port is open) and no request
        # was ever accepted -- indistinguishable from a network problem, and
        # exactly the defect the fork carried until 607850d. Timed, so `web=` in
        # LOOP/HITCH stops being a constant and starts answering "is the transfer
        # what stalled this frame".
        _t_web = 0
        _wh = getattr(ws, "webhost", None)
        if _wh is not None and getattr(_wh, "serving", False):
            _t_w0 = _ticks_ms()
            try:
                _wh.poll()
            except Exception as _wexc:  # noqa: BLE001 -- never break a frame
                print("WEB ERR %s: %s" % (type(_wexc).__name__, _wexc))
            _t_web = _ticks_diff(_ticks_ms(), _t_w0)

        elapsed = _ticks_diff(_ticks_ms(), now)
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t_kbd, _t_inp, _t_sb, _t_ws,
                        _t_diag, _t_sd, _t_web, _t_hi, _t_hp)
        _t_sleep = pump.pace(ws, elapsed)
        # Accumulated BEFORE the sleep, so `frame` is work and `sleep` is carried
        # separately -- a paced loop must not read as a slow one.
        _acc[0] += 1
        _acc[1] += elapsed
        _acc[2] += _t_kbd
        _acc[3] += _t_inp
        _acc[4] += _t_sb
        _acc[5] += _t_ws
        _acc[6] += _t_web
        _acc[7] += _t_diag
        _acc[8] += _t_sd
        _acc[9] += _t_sleep
        _acc[10] += _t_hi
        _acc[11] += _t_hp
        if _t_sleep:
            time.sleep_ms(_t_sleep)

