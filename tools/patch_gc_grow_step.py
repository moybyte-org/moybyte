#!/usr/bin/env python3
"""A cap on how much one growth of MicroPython's split heap adds.

    python3 tools/patch_gc_grow_step.py <micropython checkout>

`gc_try_add_heap` (py/gc.c) adds an area as large as the whole heap, so the
heap doubles: 64 KB -> 128 KB -> ... -> 1 MB -> 2 MB -> 4 MB. The request that
triggers a growth is often small -- a 19 KB allocation the collect could not
place among a desk's holes adds a whole megabyte -- and an area holding one
survivor is never given back (docs/history/native_kernel_2026-09.md section
1.3), so on an 8 MB board the step, not the need, sets what the heap keeps.

With MOYBYTE_GC_GROW_MAX defined (a board's mpconfigboard.h), a growth adds
at most that many bytes, unless the request itself needs more: a big
allocation still gets an area of its own size. Undefined, the stock doubling
stands. The heap's ceiling stays MOYBYTE_GC_SPLIT_RESERVE's.

Applied by `moybyte_patch_gc_grow_step` in tools/esp32_build_lib.sh, to
`py/gc.c`. All-or-nothing: the hunk must match exactly once or nothing is
written and the exit is non-zero. A tree already carrying the marker is left
as it is. Independent of the other gc patches: no hunk touches their lines.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: growth step cap"

HUNKS = [
    ("py/gc.c", "step",
     "    // Round up size to the nearest multiple of BYTES_PER_BLOCK.\n"
     "    total_heap = (total_heap + BYTES_PER_BLOCK - 1) & (~(BYTES_PER_BLOCK - 1));\n",
     "    // Round up size to the nearest multiple of BYTES_PER_BLOCK.\n"
     "    total_heap = (total_heap + BYTES_PER_BLOCK - 1) & (~(BYTES_PER_BLOCK - 1));\n"
     "\n"
     "    #ifdef MOYBYTE_GC_GROW_MAX\n"
     "    // Moybyte: growth step cap -- an area adds at most MOYBYTE_GC_GROW_MAX\n"
     "    // bytes; `needed` below still gives a big request its own size.\n"
     "    if (total_heap > (size_t)(MOYBYTE_GC_GROW_MAX)) {\n"
     "        total_heap = (size_t)(MOYBYTE_GC_GROW_MAX);\n"
     "    }\n"
     "    #endif\n"),
]


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched" or "already", or
    exits naming the hunk that did not match exactly once."""
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
            sys.exit("!! gc growth-step patch did not apply -- %s hunk %r matches %d times "
                     "(the stock lines changed shape); nothing written" % (rel, name, n))
        texts[rel] = texts[rel].replace(old, new)
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(texts[rel])
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its py/gc.c)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched py/gc.c: a growth adds at most MOYBYTE_GC_GROW_MAX (when defined)")


if __name__ == "__main__":
    main()
