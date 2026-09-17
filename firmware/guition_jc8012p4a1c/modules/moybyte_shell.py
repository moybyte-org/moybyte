"""Moybyte Guition P4 boot shell: pick a boot mode and run it.

The ladder itself is `device/boot_shell.py`, shared by every console board.
This board's REPL -- the P4's own USB-Serial/JTAG -- stays alive under every
mode:

    import moybyte_shell as s; s.MODE = "touch"; s.main()
"""

import boot_shell

BOARD = "Guition P4"
SMOKE = "guition_p4_smoke"

# The mode this image boots. "desktop" from stage 0: the smokes stay reachable
# from the REPL, and the console is what a flashed board is for.
MODE = "desktop"

MODES = ("panel", "touch", "desktop")


def main():
    boot_shell.main(BOARD, MODE, MODES, SMOKE)
