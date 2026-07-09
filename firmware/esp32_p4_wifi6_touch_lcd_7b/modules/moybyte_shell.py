"""Moybyte P4 boot shell (#58): mode flags + main().

Unlike the T-Deck there is no native-takeover USB starvation -- the CH343 UART
REPL stays alive under the desktop loop, so Ctrl-C drops cleanly back to the
REPL (caught below) and `mpremote` keeps working for the dev loop.
"""

# Boot mode flags. Default = the shared console under the windowed WM
# (moy_runtime.run_desktop). RUN_PANEL_SMOKE is the bring-up mode: hardware
# color bars straight from the DSI peripheral (no framebuffer involved), for
# separating "panel path broken" from "console broken".
RUN_PANEL_SMOKE = False
RUN_DESKTOP = True


def main():
    print("Moybyte P4 shell starting")
    if RUN_PANEL_SMOKE:
        _panel_smoke()
        return
    if RUN_DESKTOP:
        try:
            from moy_runtime import run_desktop
            run_desktop()
        except KeyboardInterrupt:
            print("Moybyte P4 desktop interrupted -> REPL")
        return
    print("Moybyte P4: no boot mode selected (REPL)")


def _panel_smoke():
    import time
    import moy_dsi
    from p4_display import set_backlight

    moy_dsi.init()
    set_backlight(True)
    print("Moybyte P4 panel smoke: vertical bars 5s, horizontal 5s")
    moy_dsi.set_pattern(1)
    time.sleep(5)
    moy_dsi.set_pattern(2)
    time.sleep(5)
    moy_dsi.set_pattern(0)
    print("Moybyte P4 panel smoke done -> REPL")
