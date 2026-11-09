#!/usr/bin/env python3
"""FatFS's long-name buffer as a board chooses it: the port's
`MICROPY_FATFS_ENABLE_LFN` kept unless the board's configuration set one.

    python3 tools/patch_fatfs_lfn.py <micropython checkout>

The consoles build oofatfs re-entrant (`MICROPY_FATFS_REENTRANT`, each
console's mpconfigboard.h and `make unix-micropython`'s flags): every FatFS
call on the card's volume takes the store's lock, because a compiled or Lua
program's own task works that volume as the VM does
(native/moy_store/moy_vol.h, THE STORE'S LOCK). FatFS refuses a re-entrant
build whose long-name work area is one static buffer (`FF_USE_LFN` 1, ff.c's
`#error`): two tasks in two volumes' calls would share it. The esp32 and unix
ports define `MICROPY_FATFS_ENABLE_LFN` as 1 with no `#ifndef`, so a board's
mpconfigboard.h (read first) cannot choose 2, the buffer on the calling task's
stack. This makes the port's line a default. A board that sets nothing builds
as before.

On the stack the buffer is `(FF_MAX_LFN + 1) * 2` bytes, and with exFAT its
directory block beside it: about 1.1 KB a FatFS call, on whichever task makes
the call.

Applied by `moybyte_patch_fatfs_lfn` in tools/esp32_build_lib.sh and by `make
unix-micropython`. All-or-nothing: every hunk must match exactly once or
nothing is written and the exit is non-zero, naming the hunk. A tree already
carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: a board chooses FatFS's long-name buffer"

HUNKS = [
    ("ports/esp32/mpconfigport.h", "esp32 LFN default",
     "#define MICROPY_FATFS_ENABLE_LFN            (1)\n",
     "// %s (tools/patch_fatfs_lfn.py).\n"
     "#ifndef MICROPY_FATFS_ENABLE_LFN\n"
     "#define MICROPY_FATFS_ENABLE_LFN            (1)\n"
     "#endif\n" % MARKER),
    ("ports/unix/mpconfigport.h", "unix LFN default",
     "#define MICROPY_FATFS_ENABLE_LFN       (1)\n",
     "// %s (tools/patch_fatfs_lfn.py).\n"
     "#ifndef MICROPY_FATFS_ENABLE_LFN\n"
     "#define MICROPY_FATFS_ENABLE_LFN       (1)\n"
     "#endif\n" % MARKER),
]


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit naming the hunk that did not match exactly once."""
    paths = sorted({f for f, _, _, _ in HUNKS})
    texts = {}
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), encoding="utf-8") as fh:
            texts[rel] = fh.read()
    if all(MARKER in texts[rel] for rel in paths):
        return "already"
    for rel, name, old, new in HUNKS:
        n = texts[rel].count(old)
        if n != 1:
            sys.exit("!! FatFS LFN patch did not apply -- %s hunk %r matches %d "
                     "times (the stock line changed shape); nothing written"
                     % (rel, name, n))
        texts[rel] = texts[rel].replace(old, new)
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(texts[rel])
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its ports' mpconfigport.h)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched ports/{esp32,unix}/mpconfigport.h: a board chooses FatFS's LFN buffer")


if __name__ == "__main__":
    main()
