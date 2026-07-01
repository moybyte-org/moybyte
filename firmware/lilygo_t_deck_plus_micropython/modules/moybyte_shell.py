import gc
import time

# Boot mode flags. Default = the v0.4 fantasy-workstation console (run_desktop in
# moy_runtime). The others are bring-up/validation modes -- flip one True, read the
# serial output, flip it back. (The old LVGL 128x128 `.moyproj` game loop that used
# to live here was removed once the v0.4 console replaced it.)
RUN_FULLSCREEN_BENCH = False    # native full-screen flush benchmark (docs/history/NATIVE_CORE_PLAN.md)
RUN_COMPOSITOR_SMOKE = False    # dirty-rect compositor + moy_gfx smoke (docs/history/STAGE3_PLAN.md)
RUN_TOUCH_CALIBRATE = False     # GT911 touch calibration dump (USB-friendly)
RUN_KEYBOARD_PROBE = False      # keyboard byte dump over serial (USB-friendly)
RUN_DESKTOP = True              # the v0.4 console: launcher + carts + editors

# SD <-> display shared-SPI handoff (#56). When True, carts + the diag dump are read from
# SD BEFORE init_display() via machine.SDCard -- but a successful pre-display mount (card
# has files) leaves the shared SPI host claimed and intermittently breaks display init
# ("display init failed: can't convert '' to int"). When False (default), NOTHING touches
# SD before the panel is up: run_desktop loads carts AFTER init via the bus-safe
# moy_sd/with_sd_live attach (its _load_carts(with_sd_live) path), so a populated SD can't
# clobber the display bus. Flip True only to compare against the old pre-display path.
PREFETCH_SD_BEFORE_DISPLAY = False

BENCH_WINDOW_MS = 3000
BENCH_BLOCK_PX = 64
BENCH_BREATHER_MS = 150


def main():
    print("Moybyte MicroPython shell starting")
    # SD <-> display handoff (#56): by DEFAULT touch NO SD before init_display(). A
    # pre-display machine.SDCard mount (the diag dump + the cart prefetch) can leave the
    # shared SPI host claimed and intermittently break display init on a populated card
    # ("can't convert '' to int"). Instead, run_desktop loads carts AFTER the panel is up
    # via the bus-safe with_sd_live attach (prefetched=None -> _load_carts(with_sd_live)),
    # and on any SD failure it degrades to the built-in carts -- so this can only make
    # display init MORE reliable, never less. PREFETCH_SD_BEFORE_DISPLAY=True restores the
    # old pre-display path (which also re-enables the boot diag dump -- it needs that
    # serial-alive, pre-panel window).
    if PREFETCH_SD_BEFORE_DISPLAY:
        _dump_diag()
        prefetched_carts = _prefetch_carts() if (RUN_DESKTOP and not RUN_KEYBOARD_PROBE) else None
    else:
        prefetched_carts = None
    lv, _display, _task_handler = _init_display()
    if lv is None:
        _serial_fallback_loop(None)
        return

    # The backlight now boots OFF (#45) and the desktop path turns it on only after
    # its first composed frame. The bring-up modes below draw straight to the panel
    # with no such hook, so light it now -- they were always meant to be watched on
    # the screen and never had a GRAM-flash concern.
    if not RUN_DESKTOP:
        try:
            from tdeck_display import set_backlight
            set_backlight(True)
        except Exception as exc:
            print("Moybyte backlight on failed:", exc)

    if RUN_FULLSCREEN_BENCH:
        _run_fullscreen_bench(_task_handler)
        return

    if RUN_COMPOSITOR_SMOKE:
        _run_compositor_smoke(_task_handler)
        return

    if RUN_TOUCH_CALIBRATE:
        from moy_runtime import run_touch_calibrate
        run_touch_calibrate(_task_handler)
        return

    if RUN_KEYBOARD_PROBE:
        from moy_runtime import run_keyboard_probe
        run_keyboard_probe(_task_handler)
        return

    if RUN_DESKTOP:
        from moy_runtime import run_desktop
        run_desktop(_task_handler, prefetched_carts)
        return

    # No boot mode selected -> idle on serial rather than hang.
    _serial_fallback_loop(None)


def _init_display():
    try:
        from tdeck_display import init_display

        return init_display()
    except Exception as exc:
        print("Moybyte display init failed:", exc)
        return None, None, None


def _feed(wdt):
    if wdt is not None:
        try:
            wdt.feed()
        except Exception:
            pass


def _dump_diag():
    """Print the previous session's diagnostics log to serial, then let the new
    session start overwriting it. Runs before init_display() so the read uses the
    bus-safe pre-display machine.SDCard path and serial is still alive. A diag
    failure must never block the boot, so the whole thing is guarded."""
    try:
        import moybyte_diag

        moybyte_diag.dump_previous_to_serial()
    except Exception as exc:
        print("Moybyte diag dump failed:", exc)


def _prefetch_carts():
    """Read all SD cartridges into RAM BEFORE display/LVGL init.

    SD shares the SPI bus with the panel; the documented-safe pattern is to mount,
    seed/scan, and release the card while the panel is NOT running -- mounting after
    the panel is live hard-hangs the shared bus. Returns (carts, carts_root)."""
    try:
        from moy_runtime import _load_carts

        carts, root = _load_carts()
        print("Moybyte prefetched %d carts (root=%s)" % (len(carts), root))
        return (carts, root)
    except Exception as exc:
        print("Moybyte cart prefetch failed:", exc)
        return None


def _native_takeover(handler):
    # Stop LVGL's background TaskHandler timer (machine.Timer @ 5ms scheduling
    # lv.task_handler) so it stops burning CPU and -- critically once the
    # compositor goes async -- stops contending for the SPI bus. See
    # docs/history/NATIVE_CORE_PLAN.md "native takeover".
    if handler is None:
        return
    try:
        handler.deinit()
        print("Moybyte LVGL TaskHandler stopped (native takeover)")
    except Exception as exc:
        print("Moybyte native takeover failed:", exc)


def _run_fullscreen_bench(handler):
    # Stage 2 gate: measure the full-screen flush frame rate (the bus-bound
    # unknown). flush_ms is the pure, pacing-independent bus metric.
    _native_takeover(handler)
    try:
        import framebuf
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
    except Exception as exc:
        print("Moybyte fullscreen bench unavailable:", exc)
        return

    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("Moybyte fullscreen bench: no compositor (host/no bus)")
        return
    w, h = comp.size()
    fbuf = framebuf.FrameBuffer(comp.framebuffer(), w, h, framebuf.RGB565)
    print("Moybyte fullscreen bench start %dx%d" % (w, h))
    while True:
        _bench_pass(comp, fbuf, w, h)


def _bench_pass(comp, fbuf, w, h):
    # Phase 1 -- full-redraw: cheap full-screen draw + whole-frame flush.
    frames = 0
    draw_ms = 0
    flush_ms = 0
    start = _ticks_ms()
    while _ticks_diff(_ticks_ms(), start) < BENCH_WINDOW_MS:
        t = _ticks_ms()
        fbuf.fill((frames * 8) & 0xFFFF)
        fbuf.fill_rect((frames * 4) % w, h // 2 - 16, 32, 32, 0xFFFF)
        draw_ms += _ticks_diff(_ticks_ms(), t)
        t = _ticks_ms()
        comp.flush()
        flush_ms += _ticks_diff(_ticks_ms(), t)
        frames += 1
        time.sleep_ms(1)
    _print_bench("full-redraw", frames, start, draw_ms, flush_ms)
    # Breather: let TinyUSB drain the print + service the CDC after a phase of
    # busy-waiting in tx_color (otherwise USB serial starves and the port drops).
    time.sleep_ms(BENCH_BREATHER_MS)

    # Phase 2 -- partial update: redraw one full-width horizontal band (the
    # realistic "desktop strip" dirty region). x==0 & w==width hits the fast
    # contiguous path.
    bs = BENCH_BLOCK_PX
    band_y = h // 2 - bs // 2
    fbuf.fill(0x0010)
    comp.flush()
    px = 0
    frames = 0
    draw_ms = 0
    flush_ms = 0
    start = _ticks_ms()
    while _ticks_diff(_ticks_ms(), start) < BENCH_WINDOW_MS:
        px = (px + 4) % (w - bs)
        t = _ticks_ms()
        fbuf.fill_rect(0, band_y, w, bs, 0x0010)
        fbuf.fill_rect(px, band_y, bs, bs, 0xFFE0)
        draw_ms += _ticks_diff(_ticks_ms(), t)
        t = _ticks_ms()
        comp.flush_rect(0, band_y, w, bs)
        flush_ms += _ticks_diff(_ticks_ms(), t)
        frames += 1
        time.sleep_ms(1)
    _print_bench("band %dx%d" % (w, bs), frames, start, draw_ms, flush_ms)
    time.sleep_ms(BENCH_BREATHER_MS)


def _run_compositor_smoke(handler):
    # Stage 3 validation: exercise the dirty-rect compositor + moy_gfx C kernel
    # (clear/fill_rect/blit565/pack_strip) and flush only the dirty box. A clean
    # moving yellow sprite on blue, no trails, ~60+ FPS = Stage 3 v1 works.
    _native_takeover(handler)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
    except Exception as exc:
        print("Moybyte compositor smoke unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("Moybyte compositor smoke: no compositor (host/no bus)")
        return
    w, h = comp.size()
    print("Moybyte compositor smoke start gfx=%d %dx%d" % (1 if comp.has_gfx() else 0, w, h))
    blue = 0x0010
    bs = 24
    # Yellow (RGB565 0xFFE0) sprite, native little-endian bytes E0 FF.
    spr = bytearray(bs * bs * 2)
    for i in range(0, len(spr), 2):
        spr[i] = 0xE0
        spr[i + 1] = 0xFF
    # Static full-flush correctness test: paint RED, then BLUE, then hold; it MUST
    # end SOLID BLUE (red bands = full flush() not overwriting all rows).
    comp.clear(0xF800)
    comp.flush()
    time.sleep_ms(800)
    comp.clear(blue)
    comp.flush()
    print("Moybyte compositor smoke: STATIC blue hold 5s -- should be SOLID blue")
    time.sleep_ms(5000)
    print("Moybyte compositor smoke: animating")
    y = h // 2 - bs // 2
    px = 0
    frames = 0
    flush_ms = 0
    start = _ticks_ms()
    while True:
        old = px
        px = (px + 4) % (w - bs)
        comp.fill_rect(old, y, bs, bs, blue)   # erase previous
        comp.blit(spr, px, y, bs, bs, -1)      # draw new
        _t = _ticks_ms()
        comp.flush_dirty()                     # cropped dirty-box flush (C pack)
        flush_ms += _ticks_diff(_ticks_ms(), _t)
        frames += 1
        if _ticks_diff(_ticks_ms(), start) >= 1000:
            print("Moybyte compositor smoke f=%d fps=%d flush_ms=%d"
                  % (frames, frames, (flush_ms // frames) if frames else 0))
            frames = 0
            flush_ms = 0
            start = _ticks_ms()
        time.sleep_ms(1)


def _print_bench(label, frames, start, draw_ms, flush_ms):
    elapsed = _ticks_diff(_ticks_ms(), start)
    if frames <= 0 or elapsed <= 0:
        print("Moybyte fullscreen bench %s: no frames" % label)
        return
    print(
        "Moybyte fullscreen bench %s f=%d fps=%d flush_ms=%d draw_ms=%d"
        % (label, frames, (frames * 1000) // elapsed, flush_ms // frames, draw_ms // frames)
    )


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(end_ms, start_ms):
    try:
        return time.ticks_diff(end_ms, start_ms)
    except AttributeError:
        return end_ms - start_ms


def _serial_fallback_loop(wdt):
    print("Moybyte running serial fallback loop")
    counter = 0
    while True:
        print("Moybyte MicroPython fallback heartbeat", counter)
        counter += 1
        _feed(wdt)
        time.sleep(1)
