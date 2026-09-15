"""Moybyte T-Deck (mainline) boot shell: pick a boot mode and run it.

The ladder itself is `device/boot_shell.py`, shared by every console board --
its docstring carries the one-string argument and the self-terminating rule.
This board declares its name, the six subsystems it brings up in stages, and
where its smokes live:

    import moybyte_shell as s; s.MODE = "touch"; s.main()
"""

import boot_shell

BOARD = "T-Deck (mainline)"
SMOKE = "tdeck_smoke"

# The mode this image boots. Set by hand as the port advances; stage 6 makes
# "desktop" the default and the rest stay reachable from the REPL.
MODE = "desktop"

MODES = ("panel", "touch", "keyboard", "sd", "audio", "desktop")


def main():
    boot_shell.main(BOARD, MODE, MODES, SMOKE)
