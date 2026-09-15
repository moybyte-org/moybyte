"""Moybyte P4 boot shell (#58): pick a boot mode and run it.

The ladder itself is `device/boot_shell.py`, shared by every console board.
This board's CH343 UART REPL stays alive under the desktop loop, so Ctrl-C drops
cleanly back to the REPL (caught there) and `mpremote` keeps working for the dev
loop:

    import moybyte_shell as s; s.MODE = "panel"; s.main()

Its one smoke lives in this file rather than in a `*_smoke` module of its own,
so SMOKE names this module.
"""

import boot_shell

BOARD = "P4"
SMOKE = "moybyte_shell"

# The mode this image boots. "panel" is the bring-up mode: hardware colour bars
# straight from the DSI peripheral (no framebuffer involved), for separating
# "panel path broken" from "console broken".
MODE = "desktop"

MODES = ("panel", "desktop")


def main():
    boot_shell.main(BOARD, MODE, MODES, SMOKE)


def panel():
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
