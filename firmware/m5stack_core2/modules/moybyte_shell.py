"""Moybyte M5Stack Core2 boot shell: pick a boot mode and run it.

The ladder itself is `device/boot_shell.py`, shared by every console board.
This board has one mode, the desktop: it came up on the T-Deck's panel module
and the kernel's drivers, so it has no staged bring-up and no smoke module.
"""

import boot_shell

BOARD = "M5Stack Core2"
SMOKE = None
MODE = "desktop"
MODES = ("desktop",)


def main():
    boot_shell.main(BOARD, MODE, MODES, SMOKE)
