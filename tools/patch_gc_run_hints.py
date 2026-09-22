#!/usr/bin/env python3
"""Size-class free-run hints for MicroPython's `gc_alloc` (#66).

    python3 tools/patch_gc_run_hints.py <micropython checkout>

`gc_alloc` finds a run of blocks by walking the allocation table from ONE hint
per heap area. A single-block allocation advances that hint past itself, a
multi-block one never does, and every collect resets it to the start -- so
each multi-block allocation walks from the hint through every hole too small
for it, and the next one walks the same holes again. With the heap a running
cart leaves (about a megabyte live, sprinkled with holes) that walk measured
100-800 us per allocation on the ESP32-S3 boards (`tools/gc_alloc_probe.py`),
and `moy_prof` priced `gc_alloc` at 3-14% of a Brick Siege frame by board.

The patch adds a hint per run length, 2..32 blocks: an ATB index before which
no free run that long starts. An allocation of n blocks scans from its class's
hint, and the run it finds raises every class of n or more to it (no run that
long starts earlier, or the scan would have met it); one longer than any class
raises nothing. A free lowers the classes the merged run can now serve, and a
collect resets them all. Single-block allocations keep the stock hint alone,
so the common path pays nothing new. One stock line is also corrected on the
way: the "this area is full" marker was written in block units to a byte index
and landed a quarter of the way in.

Applied by `moybyte_patch_gc_run_hints` in tools/esp32_build_lib.sh, to
`py/gc.c` and `py/mpstate.h`. All-or-nothing: every hunk must match exactly
once or neither file is written and the exit is non-zero, naming the hunk. A
tree already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: size-class run hints"

_HELPERS = """\
// Moybyte: size-class run hints (#66). Stock keeps ONE hint per area, which a
// single-block allocation advances past itself and every collect resets, so a
// multi-block allocation walks the table from it through every hole too small
// for it -- and the next one walks the same holes again. gc_last_free_run_index[c]
// is an ATB index before which no free run of c + 2 or more blocks starts. The
// classes stay monotone (a longer run is also a shorter one), so raising walks
// up and lowering walks down, each stopping at the first entry already there.
#define MOYBYTE_GC_RUN_MAX (MOYBYTE_GC_RUN_CLASSES + 1)
#define MOYBYTE_GC_RUN_CLASS(n) (((n) < MOYBYTE_GC_RUN_MAX ? (n) : MOYBYTE_GC_RUN_MAX) - 2)
#define MOYBYTE_GC_SCAN_START(area, n) ((n) == 1 ? (area)->gc_last_free_atb_index \\
    : MAX((area)->gc_last_free_atb_index, (area)->gc_last_free_run_index[MOYBYTE_GC_RUN_CLASS(n)]))

// The first free run of n_blocks or more from the class's hint starts at ATB
// index atb (or none does, and atb is the table's end): no run that long, or
// longer, starts before it. A run longer than any class says nothing about
// the classes below it.
static void moybyte_gc_run_hints_raise(mp_state_mem_area_t *area, size_t n_blocks, size_t atb) {
    if (n_blocks < 2 || n_blocks > MOYBYTE_GC_RUN_MAX) {
        return;
    }
    for (size_t c = MOYBYTE_GC_RUN_CLASS(n_blocks); c < MOYBYTE_GC_RUN_CLASSES && area->gc_last_free_run_index[c] < atb; c++) {
        area->gc_last_free_run_index[c] = atb;
    }
}

// Blocks [start, end) were just freed: the run they join, with any free
// neighbours, may now serve classes whose hints lie past it.
static void moybyte_gc_run_hints_lower(mp_state_mem_area_t *area, size_t start, size_t end) {
    size_t limit = area->gc_alloc_table_byte_len * BLOCKS_PER_ATB;
    size_t run = end - start;
    while (start > 0 && run < MOYBYTE_GC_RUN_MAX && ATB_GET_KIND(area, start - 1) == AT_FREE) {
        start--;
        run++;
    }
    while (end < limit && run < MOYBYTE_GC_RUN_MAX && ATB_GET_KIND(area, end) == AT_FREE) {
        end++;
        run++;
    }
    if (run < 2) {
        return;
    }
    size_t atb = start / BLOCKS_PER_ATB;
    for (size_t c = MOYBYTE_GC_RUN_CLASS(run) + 1; c > 0 && area->gc_last_free_run_index[c - 1] > atb; c--) {
        area->gc_last_free_run_index[c - 1] = atb;
    }
}

"""

# (file, name, stock text, patched text). Each stock text must occur exactly
# once, or the whole patch is refused.
HUNKS = [
    ("py/mpstate.h", "run-class count",
     "typedef struct _mp_state_mem_area_t {\n",
     "// %s (#66) -- one hint per free-run length, 2..32 blocks.\n"
     "#ifndef MOYBYTE_GC_RUN_CLASSES\n"
     "#define MOYBYTE_GC_RUN_CLASSES (31)\n"
     "#endif\n"
     "typedef struct _mp_state_mem_area_t {\n" % MARKER),
    ("py/mpstate.h", "area field",
     "    size_t gc_last_used_block; // The block ID of the highest block allocated in the area\n"
     "} mp_state_mem_area_t;\n",
     "    size_t gc_last_used_block; // The block ID of the highest block allocated in the area\n"
     "    size_t gc_last_free_run_index[MOYBYTE_GC_RUN_CLASSES]; // Moybyte: no free run of c + 2 or more blocks starts before [c]\n"
     "} mp_state_mem_area_t;\n"),
    ("py/gc.c", "area init",
     "    area->gc_last_free_atb_index = 0;\n"
     "    area->gc_last_used_block = 0;\n",
     "    area->gc_last_free_atb_index = 0;\n"
     "    area->gc_last_used_block = 0;\n"
     "    memset(area->gc_last_free_run_index, 0, sizeof(area->gc_last_free_run_index)); // %s\n" % MARKER),
    ("py/gc.c", "collect end",
     "    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL; area = NEXT_AREA(area)) {\n"
     "        area->gc_last_free_atb_index = 0;\n"
     "    }\n",
     "    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL; area = NEXT_AREA(area)) {\n"
     "        area->gc_last_free_atb_index = 0;\n"
     "        memset(area->gc_last_free_run_index, 0, sizeof(area->gc_last_free_run_index)); // Moybyte: size-class run hints\n"
     "    }\n"),
    ("py/gc.c", "helpers",
     "void *gc_alloc(size_t n_bytes, unsigned int alloc_flags) {\n",
     _HELPERS + "void *gc_alloc(size_t n_bytes, unsigned int alloc_flags) {\n"),
    ("py/gc.c", "scan start",
     "            for (i = area->gc_last_free_atb_index; i < area->gc_alloc_table_byte_len; i++) {\n",
     "            for (i = MOYBYTE_GC_SCAN_START(area, n_blocks); i < area->gc_alloc_table_byte_len; i++) { // Moybyte: size-class run hints\n"),
    ("py/gc.c", "area exhausted",
     "            #if MICROPY_GC_SPLIT_HEAP\n"
     "            if (n_blocks == 1) {\n"
     "                area->gc_last_free_atb_index = (i + 1) / BLOCKS_PER_ATB; // or (size_t)-1\n"
     "            }\n"
     "            #endif\n",
     "            #if MICROPY_GC_SPLIT_HEAP\n"
     "            if (n_blocks == 1) {\n"
     "                area->gc_last_free_atb_index = i; // Moybyte: i is the ATB byte index here, the table's end\n"
     "            }\n"
     "            #endif\n"
     "            moybyte_gc_run_hints_raise(area, n_blocks, area->gc_alloc_table_byte_len); // Moybyte: no run this long is left in this area\n"),
    ("py/gc.c", "run found",
     "    if (n_free == 1) {\n"
     "        #if MICROPY_GC_SPLIT_HEAP\n"
     "        MP_STATE_MEM(gc_last_free_area) = area;\n"
     "        #endif\n"
     "        area->gc_last_free_atb_index = (i + 1) / BLOCKS_PER_ATB;\n"
     "    }\n",
     "    if (n_free == 1) {\n"
     "        #if MICROPY_GC_SPLIT_HEAP\n"
     "        MP_STATE_MEM(gc_last_free_area) = area;\n"
     "        #endif\n"
     "        area->gc_last_free_atb_index = (i + 1) / BLOCKS_PER_ATB;\n"
     "    }\n"
     "    moybyte_gc_run_hints_raise(area, n_free, start_block / BLOCKS_PER_ATB); // Moybyte: the first run this long starts here\n"),
    ("py/gc.c", "free",
     "    // free head and all of its tail blocks\n"
     "    do {\n"
     "        ATB_ANY_TO_FREE(area, block);\n"
     "        block += 1;\n"
     "    } while (ATB_GET_KIND(area, block) == AT_TAIL);\n",
     "    // free head and all of its tail blocks\n"
     "    size_t run_start = block; // Moybyte: size-class run hints\n"
     "    do {\n"
     "        ATB_ANY_TO_FREE(area, block);\n"
     "        block += 1;\n"
     "    } while (ATB_GET_KIND(area, block) == AT_TAIL);\n"
     "    moybyte_gc_run_hints_lower(area, run_start, block);\n"),
    ("py/gc.c", "realloc shrink",
     "        for (size_t bl = block + new_blocks, count = n_blocks - new_blocks; count > 0; bl++, count--) {\n"
     "            ATB_ANY_TO_FREE(area, bl);\n"
     "        }\n",
     "        for (size_t bl = block + new_blocks, count = n_blocks - new_blocks; count > 0; bl++, count--) {\n"
     "            ATB_ANY_TO_FREE(area, bl);\n"
     "        }\n"
     "        moybyte_gc_run_hints_lower(area, block + new_blocks, block + n_blocks); // Moybyte: size-class run hints\n"),
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
    if any(MARKER in texts[rel] for rel in paths):
        sys.exit("!! %s: only some of %s carry the marker -- a half-applied tree"
                 % (MARKER, ", ".join(paths)))
    for rel, name, old, new in HUNKS:
        n = texts[rel].count(old)
        if n != 1:
            sys.exit("!! gc run-hints patch did not apply -- %s hunk %r matches %d times "
                     "in %s (the stock line changed shape); nothing written"
                     % (rel, name, n, rel))
        texts[rel] = texts[rel].replace(old, new)
    for rel in paths:
        with open(os.path.join(mpy_dir, rel), "w", encoding="utf-8") as fh:
            fh.write(texts[rel])
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its py/ is what changes)")
    a = ap.parse_args(argv)
    what = apply(a.mpy_dir)
    if what == "patched":
        print("== patched py/gc.c + py/mpstate.h: size-class run hints for gc_alloc (#66)")


if __name__ == "__main__":
    main()
