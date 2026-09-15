"""Moybyte Guition S3 boot shell: pick a boot mode and run it.

The ladder itself is `device/boot_shell.py`, shared by every console board.
This board's bring-up ladder is shorter than the T-Deck's -- no keyboard stage,
no SD stage yet, no audio stage yet -- so MODES lists what exists. Its REPL is
expected to stay alive under all of them (#201's console arrangement, baked in
from stage 0):

    import moybyte_shell as s; s.MODE = "touch"; s.main()
"""

import boot_shell

BOARD = "Guition S3"
SMOKE = "guition_smoke"

# The mode this image boots. Flipped to "desktop" 2026-08-18, the night the
# ladder below it passed on glass (panel first light, touch controller
# answering, console booting + running carts in both runtimes over the dev
# channel); the smokes stay reachable from the REPL.
MODE = "desktop"

MODES = ("panel", "touch", "desktop")


def main():
    boot_shell.main(BOARD, MODE, MODES, SMOKE)
