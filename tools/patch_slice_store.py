#!/usr/bin/env python3
"""An equal-length slice store moves the slice and nothing else.

    python3 tools/patch_slice_store.py <micropython checkout>

`buf[a:b] = row` on a bytearray, an array or a list whose length does not grow
runs `mp_seq_replace_slice_no_grow` (py/obj.h): one memmove for the slice, then
a second that closes the gap behind it by moving the whole tail, `dest_len -
end` items, from `end` to `beg + slice_len`. When the slice is the length it
replaces, those two offsets are the same and the second memmove copies the
tail onto itself. That changes no byte and costs a pass over the tail, which
is the rest of the buffer: on the Waveshare P4, 100 equal-length 512-byte
stores into a 153 KB bytearray in PSRAM took 219 ms, about 2.2 ms a store, and
a 1024x600 drawing's starter garden (runtime/artwork.py, row stores into one
bytearray) ran past the task watchdog. The patch skips the second memmove when
`beg + slice_len == end`; every other store moves exactly what it did.

Applied by `moybyte_patch_slice_store` in tools/esp32_build_lib.sh, and by
firmware/web_runner/build.sh, to `py/obj.h`. All-or-nothing: the hunk must
match exactly once or nothing is written and the exit is non-zero. A tree
already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: an equal-length slice store moves no tail"
FILE = "py/obj.h"

STOCK = (
    "#define mp_seq_replace_slice_no_grow(dest, dest_len, beg, end, slice, slice_len, item_sz) \\\n"
    "    memmove(((char *)dest) + (beg) * (item_sz), slice, slice_len * (item_sz)); \\\n"
    "    memmove(((char *)dest) + (beg + slice_len) * (item_sz), ((char *)dest) + (end) * (item_sz), (dest_len - end) * (item_sz));\n"
)

PATCHED = (
    "// %s (tools/patch_slice_store.py):\n"
    "// when beg + slice_len == end the tail would be moved onto itself.\n"
    "#define mp_seq_replace_slice_no_grow(dest, dest_len, beg, end, slice, slice_len, item_sz) \\\n"
    "    memmove(((char *)dest) + (beg) * (item_sz), slice, slice_len * (item_sz)); \\\n"
    "    if ((size_t)((beg) + (slice_len)) != (size_t)(end)) { \\\n"
    "        memmove(((char *)dest) + (beg + slice_len) * (item_sz), ((char *)dest) + (end) * (item_sz), (dest_len - end) * (item_sz)); \\\n"
    "    }\n" % MARKER
)


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when the stock macro does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    n = text.count(STOCK)
    if n != 1:
        sys.exit("!! slice-store patch did not apply -- %s's mp_seq_replace_slice_no_grow "
                 "matches %d times (the stock macro changed shape); nothing written"
                 % (FILE, n))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(STOCK, PATCHED))
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its py/obj.h is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched py/obj.h: an equal-length slice store moves no tail")


if __name__ == "__main__":
    main()
