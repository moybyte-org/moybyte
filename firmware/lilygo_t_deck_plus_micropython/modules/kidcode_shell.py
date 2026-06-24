import gc
import time

ENABLE_WATCHDOG = True
ENABLE_BOOT_SELF_TESTS = False
ENABLE_EXTERNAL_PROJECT_FILES = False
ENABLE_SD_PROJECT_SLOT = True
ENABLE_SD_PREFETCH = True
# Stage 2 spike: when True, boot runs the full-screen native compositor benchmark
# (NATIVE_CORE_PLAN.md) and never enters the normal game loop. Flip to True for a
# bench build, read `KidCode fullscreen bench ...` over serial, then flip back.
RUN_FULLSCREEN_BENCH = False
# Stage 3 device validation: when True, boot runs the dirty-rect compositor smoke
# (kc_gfx C kernel + kc_compositor) instead of the normal app. Read
# `KidCode compositor smoke ...` over serial. See STAGE3_PLAN.md.
RUN_COMPOSITOR_SMOKE = False
# Default device boot (v0.4): the fantasy workstation on the native compositor --
# cartridge launcher + carts + keyboard, same kid API as the host simulator (see
# kid_runtime.py). Supersedes the legacy 128x128 LVGL game loop below, which stays
# as a fallback. Set False to boot the legacy path.
RUN_DESKTOP = True
BENCH_WINDOW_MS = 3000
BENCH_BLOCK_PX = 64
BENCH_BREATHER_MS = 150
FRAME_DELAY_MS = 20
GAME_SELECT_MS = 2500
SPIKE_RESTART_CYCLES = 20
PROJECT_FILE_PATHS = (
    "/sd/kidcode/project.py",
    "/sd/kidcode/main.py",
    "/flash/kidcode/project.py",
    "kidcode_project.py",
)
GAME_SLOTS = (
    ("Tiny Runner", "module", "projects.tiny_runner"),
    ("Input Test", "module", "projects.input_test"),
    ("Bounce Box", "module", "projects.bounce_box"),
    ("SD Project", "sd", None),
)


def main():
    print("KidCode MicroPython shell starting")
    prefetched_sd_project = _prefetch_sd_project()
    prefetched_carts = _prefetch_carts() if RUN_DESKTOP else None
    lv, _display, _task_handler = _init_display()
    if lv is None:
        _serial_fallback_loop(None)
        return

    if RUN_FULLSCREEN_BENCH:
        _run_fullscreen_bench(_task_handler)
        return

    if RUN_COMPOSITOR_SMOKE:
        _run_compositor_smoke(_task_handler)
        return

    if RUN_DESKTOP:
        from kid_runtime import run_desktop
        run_desktop(_task_handler, prefetched_carts)
        return

    from kidcode.input import InputState, TDeckKeyboard
    from kidcode_lvgl_renderer import ConsoleRenderer
    from kidcode_project_loader import ProjectRunner

    input_state = InputState()
    renderer = ConsoleRenderer(lv)
    renderer.set_status("renderer ok")
    _safe_render_message(renderer, lv, "boot", ("display ok", "starting KidCode"))
    keyboard = TDeckKeyboard(input_state)
    runner = ProjectRunner(input_state, keyboard, renderer)
    # Bind the renderer's framebuffer straight into the runtime canvas so draw
    # calls rasterize directly to the RGB565 buffer, skipping the per-frame
    # command-dict allocation/parsing loop (direct framebuf mode on device).
    renderer.bind_canvas(runner.runtime.canvas)

    if ENABLE_BOOT_SELF_TESTS:
        _run_spike_checks(runner, renderer)
    wdt = _make_watchdog() if ENABLE_WATCHDOG else None
    game_slot = _select_game(renderer, keyboard, input_state, lv, wdt)
    _load_start_project(runner, renderer, game_slot, lv, wdt, prefetched_sd_project)
    _pump_lv(lv, count=3, delay_ms=10)

    renderer.set_status("loop starting kb=%d" % keyboard.available)
    last_ms = _ticks_ms()
    target_frame_ms = 16  # cap at ~60 FPS (native blitter sustains ~90 uncapped)
    # Frame-time benchmark accumulators (reset every ~1s and printed to serial).
    bench_count = 0
    bench_step_ms = 0
    bench_pump_ms = 0
    bench_kb_ms = 0
    bench_upd_ms = 0
    bench_drw_ms = 0
    bench_ms = _ticks_ms()
    while True:
        start_ms = _ticks_ms()
        dt = max(0.0, min(0.25, (start_ms - last_ms) / 1000.0))
        last_ms = start_ms
        _t = _ticks_ms()
        try:
            runner.step(dt)
        except Exception as exc:
            print("KidCode frame error:", exc)
            renderer.set_status("error: " + str(exc))
            runner.cleanup()
        bench_step_ms += _ticks_diff(_ticks_ms(), _t)
        bench_kb_ms += runner.bench["kb"]
        bench_upd_ms += runner.bench["upd"]
        bench_drw_ms += runner.bench["drw"]
        _t = _ticks_ms()
        _pump_lv(lv)
        bench_pump_ms += _ticks_diff(_ticks_ms(), _t)
        _feed(wdt)
        bench_count += 1
        if _ticks_diff(_ticks_ms(), bench_ms) >= 1000:
            if bench_count > 0:
                print(
                    "KidCode bench f=%d avg(ms) step=%d [kb=%d upd=%d drw=%d] pump=%d"
                    % (
                        bench_count,
                        bench_step_ms // bench_count,
                        bench_kb_ms // bench_count,
                        bench_upd_ms // bench_count,
                        bench_drw_ms // bench_count,
                        bench_pump_ms // bench_count,
                    )
                )
            bench_count = 0
            bench_step_ms = 0
            bench_pump_ms = 0
            bench_kb_ms = 0
            bench_upd_ms = 0
            bench_drw_ms = 0
            bench_ms = _ticks_ms()

        # Calculate how long rendering took and sleep for the remainder of the 33ms frame window
        elapsed = _ticks_diff(_ticks_ms(), start_ms)
        sleep_ms = max(1, target_frame_ms - elapsed)
        time.sleep_ms(sleep_ms)



def _init_display():
    try:
        from tdeck_display import init_display

        return init_display()
    except Exception as exc:
        print("KidCode display init failed:", exc)
        return None, None, None


def _make_watchdog():
    try:
        from machine import WDT

        return WDT(timeout=5000)
    except Exception as exc:
        print("KidCode watchdog unavailable:", exc)
        return None


def _feed(wdt):
    if wdt is not None:
        try:
            wdt.feed()
        except Exception:
            pass


def _select_game(renderer, keyboard, input_state, lv, wdt=None):
    selected = 0
    rendered = -1
    start_ms = _ticks_ms()

    while _ticks_diff(_ticks_ms(), start_ms) < GAME_SELECT_MS:
        keyboard.poll()
        input_state.begin_frame()
        if input_state.pressed("left") or input_state.pressed("up"):
            selected = (selected - 1) % _game_slot_count()
        if input_state.pressed("right") or input_state.pressed("down"):
            selected = (selected + 1) % _game_slot_count()
        if input_state.pressed("a") or input_state.pressed("run"):
            break
        if selected != rendered:
            _render_game_select(renderer, selected)
            _pump_lv(lv)
            rendered = selected
        _feed(wdt)
        time.sleep_ms(40)

    input_state.release_all()
    input_state.begin_frame()
    return GAME_SLOTS[selected]


def _game_slot_count():
    if ENABLE_SD_PROJECT_SLOT:
        return len(GAME_SLOTS)
    return len(GAME_SLOTS) - 1


def _render_game_select(renderer, selected):
    name, _kind, _target = GAME_SLOTS[selected]
    lines = (
        "Select game",
        "Left/right " + name,
        "A/Run starts",
        "wait starts selected",
    )
    renderer.render_message("game " + str(selected + 1), lines)


def _safe_render_message(renderer, lv, status, lines):
    try:
        renderer.render_message(status, lines)
        _pump_lv(lv, count=3, delay_ms=10)
    except Exception as exc:
        print("KidCode boot render failed:", exc)
        try:
            renderer.set_status("render error")
            _pump_lv(lv, count=3, delay_ms=10)
        except Exception:
            pass


def _run_spike_checks(runner, renderer):
    boot_free = _free_mem()
    syntax_ok = not runner.try_load_source("bad_syntax", "def broken(:\n    pass\n")
    before, after = runner.run_restart_cycles("projects.tiny_runner", SPIKE_RESTART_CYCLES)
    print(
        "KidCode spike metrics",
        "boot_free",
        boot_free,
        "restart_before",
        before,
        "restart_after",
        after,
        "syntax_probe",
        syntax_ok,
    )
    renderer.render_message(
        "spike checks",
        (
            "syntax ok " + str(int(syntax_ok)),
            "boot free " + str(boot_free),
            "20x free " + str(after),
        ),
    )
    time.sleep_ms(600)


def _load_start_project(runner, renderer, game_slot=None, lv=None, wdt=None, prefetched_sd_project=None):
    if game_slot is not None and game_slot[1] == "sd":
        if _load_sd_project(runner, renderer, lv, wdt, prefetched_sd_project):
            return
        renderer.render_message(
            "sd fallback",
            (
                "SD project missing",
                "loading Tiny Runner",
            ),
        )
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        time.sleep_ms(700)
        game_slot = GAME_SLOTS[0]

    if game_slot is not None and game_slot[1] == "module":
        _feed(wdt)
        _load_frozen_or_report(runner, renderer, game_slot[2])
        return

    if ENABLE_EXTERNAL_PROJECT_FILES:
        _feed(wdt)
        loaded_path = runner.try_load_file(PROJECT_FILE_PATHS)
        if loaded_path:
            renderer.set_status("loaded file")
            return
    _load_frozen_or_report(runner, renderer, "projects.tiny_runner")


def _load_frozen_or_report(runner, renderer, module_name):
    try:
        runner.load_frozen_project(module_name)
    except Exception as exc:
        renderer.set_status("project error: " + str(exc))
        print("KidCode project load failed:", exc)


def _prefetch_sd_project():
    if not ENABLE_SD_PROJECT_SLOT or not ENABLE_SD_PREFETCH:
        return None
    try:
        from kidcode_sd import read_first_project_source

        result = read_first_project_source()
        if result:
            path, source = result
            print("KidCode SD prefetched", path, "bytes", len(source))
            return result
        print("KidCode SD prefetch found no project")
    except Exception as exc:
        print("KidCode SD prefetch failed:", exc)
    return None


def _prefetch_carts():
    """Read all SD cartridges into RAM BEFORE display/LVGL init.

    SD shares the SPI bus with the panel; the documented-safe pattern (see the
    README SD section and _prefetch_sd_project) is to mount, seed/scan, and
    release the card while the panel is NOT running. Mounting after the panel is
    live hard-hangs the shared bus. Returns (carts, carts_root)."""
    try:
        from kid_runtime import _load_carts

        carts, root = _load_carts()
        print("KidCode prefetched %d carts (root=%s)" % (len(carts), root))
        return (carts, root)
    except Exception as exc:
        print("KidCode cart prefetch failed:", exc)
        return None


def _load_sd_project(runner, renderer, lv=None, wdt=None, prefetched_sd_project=None):
    if prefetched_sd_project is not None:
        path, source = prefetched_sd_project
        renderer.render_message("sd project", ("cached SD project", "loading"))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        _feed(wdt)
        if runner.try_load_source(path, source):
            print("KidCode cached SD project loaded", path)
            renderer.render_message("sd project", ("loaded SD project", "starting"))
            if lv is not None:
                _pump_lv(lv, count=3, delay_ms=10)
            time.sleep_ms(150)
            return True
        print("KidCode cached SD project load failed")
        renderer.render_message("sd failed", ("cached SD failed", "loading fallback"))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        return False

    try:
        from kidcode_sd import read_first_project_source

        renderer.render_message("sd project", ("mounting SD", "please wait"))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        _feed(wdt)
        result = read_first_project_source()
        if result is None:
            print("KidCode SD project missing")
            renderer.render_message("sd failed", ("SD project missing", "loading fallback"))
            if lv is not None:
                _pump_lv(lv, count=3, delay_ms=10)
            return False
        path, source = result
        print("KidCode SD project read", path, "bytes", len(source))
        renderer.render_message("sd project", ("SD project read", "loading"))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        _feed(wdt)
    except Exception as exc:
        print("KidCode SD read failed:", exc)
        renderer.render_message("sd failed", ("SD read failed", str(exc)[:24]))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        return False

    if runner.try_load_source(path, source):
        print("KidCode SD project loaded", path)
        renderer.render_message("sd project", ("loaded SD project", "starting"))
        if lv is not None:
            _pump_lv(lv, count=3, delay_ms=10)
        time.sleep_ms(150)
        return True
    print("KidCode SD project load failed")
    renderer.render_message("sd failed", ("SD project failed", "or missing file"))
    if lv is not None:
        _pump_lv(lv, count=3, delay_ms=10)
    return False


def _pump_lv(lv, count=1, delay_ms=0):
    for _index in range(count):
        try:
            lv.timer_handler()
        except Exception:
            return
        if delay_ms:
            time.sleep_ms(delay_ms)


def _native_takeover(handler):
    # Stop LVGL's background TaskHandler timer (machine.Timer @ 5ms scheduling
    # lv.task_handler) so it stops burning CPU and -- critically once the
    # compositor goes async -- stops contending for the SPI bus. See
    # NATIVE_CORE_PLAN.md "native takeover".
    if handler is None:
        return
    try:
        handler.deinit()
        print("KidCode LVGL TaskHandler stopped (native takeover)")
    except Exception as exc:
        print("KidCode native takeover failed:", exc)


def _run_fullscreen_bench(handler):
    # Stage 2 gate: measure the full-screen flush frame rate (the bus-bound
    # unknown). flush_ms is the pure, pacing-independent bus metric.
    _native_takeover(handler)
    try:
        import framebuf
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
    except Exception as exc:
        print("KidCode fullscreen bench unavailable:", exc)
        return

    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("KidCode fullscreen bench: no compositor (host/no bus)")
        return
    w, h = comp.size()
    fbuf = framebuf.FrameBuffer(comp.framebuffer(), w, h, framebuf.RGB565)
    print("KidCode fullscreen bench start %dx%d" % (w, h))
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
    # Does not touch flush_ms (measured per-flush above).
    time.sleep_ms(BENCH_BREATHER_MS)

    # Phase 2 -- partial update: redraw one full-width horizontal band (the
    # realistic "desktop strip" dirty region). x==0 & w==width hits the fast
    # contiguous path. Arbitrary x-cropped rects need a C row-packer (Stage 3) --
    # the per-row Python copy is too slow and is not what the desktop will use.
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
    # Stage 3 validation: exercise the dirty-rect compositor + kc_gfx C kernel
    # (clear/fill_rect/blit565/pack_strip) and flush only the dirty box. A clean
    # moving yellow sprite on blue, no trails, ~60+ FPS = Stage 3 v1 works.
    _native_takeover(handler)
    try:
        from tdeck_display import get_display_bus
        from kc_compositor import make_compositor
    except Exception as exc:
        print("KidCode compositor smoke unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("KidCode compositor smoke: no compositor (host/no bus)")
        return
    w, h = comp.size()
    print("KidCode compositor smoke start gfx=%d %dx%d" % (1 if comp.has_gfx() else 0, w, h))
    blue = 0x0010
    bs = 24
    # Yellow (RGB565 0xFFE0) sprite, native little-endian bytes E0 FF.
    spr = bytearray(bs * bs * 2)
    for i in range(0, len(spr), 2):
        spr[i] = 0xE0
        spr[i + 1] = 0xFF
    # Static full-flush correctness test (isolates flush() from animation):
    # paint the whole screen RED, then BLUE, then hold still. It MUST end SOLID
    # BLUE. Red bands during the hold = full flush() is not overwriting all rows
    # (e.g. the single DMA strip buffer reused before its transfer drains).
    comp.clear(0xF800)
    comp.flush()
    time.sleep_ms(800)
    comp.clear(blue)
    comp.flush()
    print("KidCode compositor smoke: STATIC blue hold 5s -- should be SOLID blue")
    time.sleep_ms(5000)
    print("KidCode compositor smoke: animating")
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
            print("KidCode compositor smoke f=%d fps=%d flush_ms=%d"
                  % (frames, frames, (flush_ms // frames) if frames else 0))
            frames = 0
            flush_ms = 0
            start = _ticks_ms()
        time.sleep_ms(1)


def _print_bench(label, frames, start, draw_ms, flush_ms):
    elapsed = _ticks_diff(_ticks_ms(), start)
    if frames <= 0 or elapsed <= 0:
        print("KidCode fullscreen bench %s: no frames" % label)
        return
    print(
        "KidCode fullscreen bench %s f=%d fps=%d flush_ms=%d draw_ms=%d"
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


def _free_mem():
    try:
        gc.collect()
        return gc.mem_free()
    except AttributeError:
        return -1


def _serial_fallback_loop(wdt):
    print("KidCode running serial fallback loop")
    counter = 0
    while True:
        print("KidCode MicroPython fallback heartbeat", counter)
        counter += 1
        _feed(wdt)
        time.sleep(1)
