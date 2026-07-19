import time

# Boot mode flags. Default = the v0.4 fantasy-workstation console (run_desktop in
# moy_runtime). The others are bring-up/validation modes -- flip one True, read the
# serial output, flip it back. (The old LVGL 128x128 `.moyproj` game loop, the
# STAGE3/NATIVE_CORE bring-up benches (RUN_FULLSCREEN_BENCH / RUN_COMPOSITOR_SMOKE)
# and the #56 pre-display SD prefetch A/B toggle were removed once superseded --
# the #63 perf bench (a MOYBYTE_BENCH=1 build) is the current benchmark harness;
# git history has the rest.)
RUN_TOUCH_CALIBRATE = False     # GT911 touch calibration dump (USB-friendly)
RUN_KEYBOARD_PROBE = False      # keyboard byte dump over serial (USB-friendly)
RUN_DESKTOP = True              # the v0.4 console: launcher + carts + editors


def main():
    print("Moybyte MicroPython shell starting")
    # SD <-> display handoff (#56): touch NO SD before init_display(). A pre-display
    # machine.SDCard mount can leave the shared SPI host claimed and intermittently
    # break display init on a populated card ("can't convert '' to int"). run_desktop
    # loads carts AFTER the panel is up via the bus-safe with_sd_live attach
    # (prefetched=None -> _load_carts(with_sd_live)), degrading to the built-in carts
    # on any SD failure.
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

    # Perf bench build (#63): a MOYBYTE_BENCH=1 build stamps modules/_moy_bench.py
    # and boots into the self-terminating pipeline bench instead of the desktop --
    # it prints BENCH lines and RETURNS to the REPL, so a headless bench board
    # (XIAO S3, no buttons) stays reflashable. Absent stamp -> normal boot.
    try:
        import _moy_bench
        _bench = getattr(_moy_bench, "BENCH", False)
    except ImportError:
        _bench = False
    if _bench:
        from moy_runtime import run_perf_bench
        run_perf_bench(_task_handler)
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
        run_desktop(_task_handler, None)
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


def _serial_fallback_loop(wdt):
    print("Moybyte running serial fallback loop")
    counter = 0
    while True:
        print("Moybyte MicroPython fallback heartbeat", counter)
        counter += 1
        _feed(wdt)
        time.sleep(1)
