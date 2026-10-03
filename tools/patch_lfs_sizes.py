#!/usr/bin/env python3
"""LittleFS on a P4's flash store: programs 1 KB at a time, and looks ahead
over 2048 blocks for free ones.

    python3 tools/patch_lfs_sizes.py <micropython checkout>

The P4 boards' cart store is the LittleFS the esp32 port's `_boot.py` mounts
at `/` with VfsLfs2's defaults, and two of those defaults cost every write:

  * `progsize=32` gives it a 128-byte cache (MicroPython sizes the cache at
    four times the larger of the read and program sizes), so every 128 bytes
    a file takes is one `Partition.writeblocks` -- one flash operation with
    the cache off, which the SPI flash driver issues 64 bytes at a time.
    `progsize=256` makes the cache 1 KB: a 4 KB block goes down in four
    programs instead of thirty-two, and reads take a quarter of the calls.
  * `lookahead=32` is a bitmap of 256 blocks. LittleFS finds free blocks by
    walking the whole filesystem into that bitmap, so on a store a few
    megabytes full it walks once per megabyte written, and more often the
    fuller the store is. `lookahead=256` covers 2048 blocks per walk.

Measured on the boards, 2026-10-01. A fresh LittleFS on the Waveshare's flash
wrote 1 MB in 20.3 s at the stock sizes and 15.5 s at progsize 256; the
Waveshare's own store, mostly empty, went from 33 to 56 KB/s in 3 KB writes
and from 1.0 to 3.2 MB/s reading. On a store 73% full on the Guition P4's
flash, lookahead 256 wrote 512 KB 13-33% faster than 32. The 4 KB erase
LittleFS issues per block is most of what is left.

Neither size is part of the on-disk format -- the superblock records the
block size and count, and a commit carries its own padding -- so a store
written under either reads and writes under the other. Measured both ways on
the Waveshare's flash before this was taken: files of 0 to 70,000 bytes
written, appended and read back across the two configurations, every byte
intact. The change is to the defaults, so `mkfs` and every mount that names no
size take it.

The S3 boards' stores are their SD cards, and they decline. Applied by
`moybyte_patch_lfs_sizes` in tools/esp32_build_lib.sh, to `extmod/vfs_lfs.c`.
All-or-nothing: every hunk must match exactly once or nothing is written and
the exit is non-zero, naming the hunk. A tree already carrying the marker is
left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: LittleFS sizes for a flash store"
PROG_SIZE = 256
LOOKAHEAD = 256

HUNKS = [
    ("extmod/vfs_lfs.c", "progsize default",
     "    { MP_QSTR_progsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} },\n",
     "    // %s (tools/patch_lfs_sizes.py).\n"
     "    { MP_QSTR_progsize, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = %d} },\n"
     % (MARKER, PROG_SIZE)),
    ("extmod/vfs_lfs.c", "lookahead default",
     "    { MP_QSTR_lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = 32} },\n",
     "    { MP_QSTR_lookahead, MP_ARG_KW_ONLY | MP_ARG_INT, {.u_int = %d} },\n"
     % LOOKAHEAD),
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
            sys.exit("!! LittleFS size patch did not apply -- %s hunk %r matches %d "
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
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its extmod is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched extmod/vfs_lfs.c: LittleFS progsize %d, lookahead %d"
              % (PROG_SIZE, LOOKAHEAD))


if __name__ == "__main__":
    main()
