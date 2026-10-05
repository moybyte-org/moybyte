#!/usr/bin/env python3
"""The split heap's areas, one by one, and every area it added or freed.

    python3 tools/patch_gc_census.py <micropython checkout>

`gc.areas()` (tools/patch_gc_meters.py) says how many areas a
`MICROPY_GC_SPLIT_HEAP_AUTO` heap holds and their bytes. A census of who holds
them needs two more answers, which this patch adds to the stock `gc` module:

    gc.area_map() -> ((start, end, held, used, largest_free, heads), ...)
        One tuple per area, the first area first: its address range (the
        pool's), the bytes it takes from the system allocator (tables and
        pool, as `gc.areas()` counts them), the bytes of its blocks in use, the
        longest run of free blocks in bytes, and how many allocations head a
        block in it. Read after a collect, `used` is what is live in that
        area, and `start`/`end` place any object by its `id()`. One walk of
        every area's allocation table, under the gc lock -- a word's cost,
        never a frame's.

    gc.growths() -> ((ticks_ms, request, bytes), ...)
        Every area the heap added since boot -- the allocation that failed
        after a collect (`request`, bytes) and the area added for it (`bytes`)
        -- and every area a sweep freed (`request` 0, `bytes` negative), in
        order, on the `time.ticks_ms()` clock. The last 32 are kept; `request`
        and `bytes` wrap nothing.

    gc.refs(address) -> ((block, nbytes, word0), ...)
        Every allocated block in the heap holding the word `address`, by the
        address its allocation starts at, its size, and its first word -- an
        object's type, where the block is an object. At most 32. What a
        reference held only on the C stack or in a C static is never appears,
        so a buffer with no referrer here is held from there or by nothing.

A host without the patch has none of the three, and its readers report absence.

Applied by `moybyte_patch_gc_meters` in tools/esp32_build_lib.sh, beside
tools/patch_gc_meters.py, to `py/gc.c` and `py/modgc.c`. All-or-nothing:
every hunk must match exactly once or neither file is written and the exit is
non-zero, naming the hunk. A tree already carrying the marker is left as it
is. Independent of tools/patch_gc_meters.py and tools/patch_gc_run_hints.py:
no hunk of any of them touches another's lines, so a tree takes them in any
order and comes out the same.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: gc census"

_HELPERS = """\
// %s (tools/patch_gc_census.py): the areas the split heap added
// and freed, and a walk of each area's allocation table.
#if MICROPY_PY_GC
#include "py/mphal.h"
#define MOYBYTE_GC_EVENTS (32)
// [i][0] ticks_ms, [1] the request that grew the heap (0: a sweep freed the
// area), [2] the area's bytes; moybyte_gc_events counts every event. The ring
// is taken from PSRAM at the first event, so internal SRAM pays two words for
// it; an event with no ring to hold it is counted and dropped.
#if defined(ESP_PLATFORM)
#include "esp_heap_caps.h"
#define MOYBYTE_GC_RING_ALLOC(n) heap_caps_malloc((n), MALLOC_CAP_SPIRAM)
#else
#include <stdlib.h>
#define MOYBYTE_GC_RING_ALLOC(n) malloc(n)
#endif
uint32_t (*moybyte_gc_event)[3];
uint32_t moybyte_gc_events;
static void moybyte_gc_note(uint32_t request, uint32_t bytes) {
    if (moybyte_gc_event == NULL) {
        moybyte_gc_event = MOYBYTE_GC_RING_ALLOC(sizeof(uint32_t[MOYBYTE_GC_EVENTS][3]));
    }
    if (moybyte_gc_event != NULL) {
        uint32_t *e = moybyte_gc_event[moybyte_gc_events %% MOYBYTE_GC_EVENTS];
        e[0] = (uint32_t)mp_hal_ticks_ms();
        e[1] = request;
        e[2] = bytes;
    }
    moybyte_gc_events++;
}
#define MOYBYTE_GC_NOTE(request, bytes) moybyte_gc_note((uint32_t)(request), (uint32_t)(bytes))

// Six words per area into out[], at most max_areas areas; returns how many
// areas the heap has (which may exceed max_areas).
size_t moybyte_gc_area_map(size_t *out, size_t max_areas) {
    GC_ENTER();
    size_t n = 0;
    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL;
         area = NEXT_AREA(area), n++) {
        if (n >= max_areas) {
            continue;
        }
        size_t blocks = area->gc_alloc_table_byte_len * BLOCKS_PER_ATB;
        size_t used = 0, run = 0, best = 0, heads = 0;
        for (size_t b = 0; b < blocks; b++) {
            unsigned kind = ATB_GET_KIND(area, b);
            if (kind == AT_FREE) {
                if (++run > best) {
                    best = run;
                }
                continue;
            }
            run = 0;
            used++;
            if (kind != AT_TAIL) {
                heads++;
            }
        }
        size_t *o = out + 6 * n;
        o[0] = (size_t)area->gc_pool_start;
        o[1] = (size_t)area->gc_pool_end;
        o[2] = (size_t)(area->gc_pool_end
            - (area == &MP_STATE_MEM(area) ? area->gc_alloc_table_start : (byte *)area));
        o[3] = used * BYTES_PER_BLOCK;
        o[4] = best * BYTES_PER_BLOCK;
        o[5] = heads;
    }
    GC_EXIT();
    return n;
}

// Three words per referrer into out[]: the head block's address, its bytes,
// its first word. Every allocated block holding `word` is reported once, by
// the allocation it belongs to; returns how many, at most max_refs.
size_t moybyte_gc_refs(uintptr_t word, size_t *out, size_t max_refs) {
    GC_ENTER();
    size_t n = 0;
    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL && n < max_refs;
         area = NEXT_AREA(area)) {
        size_t blocks = area->gc_alloc_table_byte_len * BLOCKS_PER_ATB;
        size_t head = 0;
        size_t last = (size_t)-1;
        for (size_t b = 0; b < blocks && n < max_refs; b++) {
            unsigned kind = ATB_GET_KIND(area, b);
            if (kind == AT_FREE) {
                continue;
            }
            if (kind != AT_TAIL) {
                head = b;
            }
            if (head == last) {
                continue;
            }
            const uintptr_t *w = (const uintptr_t *)PTR_FROM_BLOCK(area, b);
            for (size_t k = 0; k < WORDS_PER_BLOCK; k++) {
                if (w[k] == word) {
                    uintptr_t at = PTR_FROM_BLOCK(area, head);
                    size_t tail = head + 1;
                    while (tail < blocks && ATB_GET_KIND(area, tail) == AT_TAIL) {
                        tail++;
                    }
                    out[3 * n] = at;
                    out[3 * n + 1] = (tail - head) * BYTES_PER_BLOCK;
                    out[3 * n + 2] = *(const uintptr_t *)at;
                    n++;
                    last = head;
                    break;
                }
            }
        }
    }
    GC_EXIT();
    return n;
}
#else
#define MOYBYTE_GC_NOTE(request, bytes)
#endif

""" % MARKER

_ACCESSORS = """\
// %s (tools/patch_gc_census.py).
extern uint32_t (*moybyte_gc_event)[3];
extern uint32_t moybyte_gc_events;
size_t moybyte_gc_area_map(size_t *out, size_t max_areas);

// area_map(): per area (start, end, held, used, largest free run, heads).
static mp_obj_t gc_area_map(void) {
    size_t raw[6 * 16];
    size_t n = moybyte_gc_area_map(raw, 16);
    if (n > 16) {
        n = 16;
    }
    mp_obj_t areas[16];
    for (size_t i = 0; i < n; i++) {
        mp_obj_t t[6];
        for (size_t k = 0; k < 6; k++) {
            t[k] = mp_obj_new_int_from_uint(raw[6 * i + k]);
        }
        areas[i] = mp_obj_new_tuple(6, t);
    }
    return mp_obj_new_tuple(n, areas);
}
MP_DEFINE_CONST_FUN_OBJ_0(gc_area_map_obj, gc_area_map);

// growths(): (ticks_ms, request, +bytes) per area added, (ticks_ms, 0,
// -bytes) per area freed, oldest first; the last 32.
static mp_obj_t gc_growths(void) {
    uint32_t total = moybyte_gc_event != NULL ? moybyte_gc_events : 0;
    uint32_t n = total < 32 ? total : 32;
    mp_obj_t out[32];
    for (uint32_t i = 0; i < n; i++) {
        uint32_t *e = moybyte_gc_event[(total - n + i) %% 32];
        mp_obj_t t[3] = {
            mp_obj_new_int_from_uint(e[0]),
            mp_obj_new_int_from_uint(e[1]),
            e[1] ? mp_obj_new_int_from_uint(e[2]) : mp_obj_new_int(-(mp_int_t)e[2]),
        };
        out[i] = mp_obj_new_tuple(3, t);
    }
    return mp_obj_new_tuple(n, out);
}
MP_DEFINE_CONST_FUN_OBJ_0(gc_growths_obj, gc_growths);

size_t moybyte_gc_refs(uintptr_t word, size_t *out, size_t max_refs);

// refs(address): per heap block holding that word, (block, nbytes, word0).
static mp_obj_t gc_refs(mp_obj_t addr_in) {
    size_t raw[3 * 32];
    size_t n = moybyte_gc_refs((uintptr_t)mp_obj_get_int_truncated(addr_in), raw, 32);
    mp_obj_t out[32];
    for (size_t i = 0; i < n; i++) {
        mp_obj_t t[3] = {
            mp_obj_new_int_from_uint(raw[3 * i]),
            mp_obj_new_int_from_uint(raw[3 * i + 1]),
            mp_obj_new_int_from_uint(raw[3 * i + 2]),
        };
        out[i] = mp_obj_new_tuple(3, t);
    }
    return mp_obj_new_tuple(n, out);
}
MP_DEFINE_CONST_FUN_OBJ_1(gc_refs_obj, gc_refs);

""" % MARKER

# (file, name, stock text, patched text). Each stock text must occur exactly
# once, or the whole patch is refused.
HUNKS = [
    ("py/gc.c", "helpers",
     "#if MICROPY_GC_SPLIT_HEAP\n"
     "void gc_add(void *start, void *end) {\n",
     _HELPERS +
     "#if MICROPY_GC_SPLIT_HEAP\n"
     "void gc_add(void *start, void *end) {\n"),
    ("py/gc.c", "area added",
     "    gc_add(new_heap, (void *)new_heap + to_alloc);\n"
     "\n"
     "    return true;\n",
     "    gc_add(new_heap, (void *)new_heap + to_alloc);\n"
     "    MOYBYTE_GC_NOTE(failed_alloc, to_alloc); // %s\n"
     "\n"
     "    return true;\n" % MARKER),
    ("py/gc.c", "area freed",
     '            DEBUG_printf("gc_sweep_free_blocks free empty area %p\\n", area);\n'
     "            NEXT_AREA(prev_area) = NEXT_AREA(area);\n",
     '            DEBUG_printf("gc_sweep_free_blocks free empty area %%p\\n", area);\n'
     "            MOYBYTE_GC_NOTE(0, area->gc_pool_end - (byte *)area); // %s\n"
     "            NEXT_AREA(prev_area) = NEXT_AREA(area);\n" % MARKER),
    ("py/modgc.c", "accessors",
     "#if MICROPY_GC_ALLOC_THRESHOLD\n"
     "static mp_obj_t gc_threshold(",
     _ACCESSORS +
     "#if MICROPY_GC_ALLOC_THRESHOLD\n"
     "static mp_obj_t gc_threshold("),
    ("py/modgc.c", "table",
     "    { MP_ROM_QSTR(MP_QSTR_mem_free), MP_ROM_PTR(&gc_mem_free_obj) },\n",
     "    { MP_ROM_QSTR(MP_QSTR_mem_free), MP_ROM_PTR(&gc_mem_free_obj) },\n"
     "    { MP_ROM_QSTR(MP_QSTR_area_map), MP_ROM_PTR(&gc_area_map_obj) }, // %s\n"
     "    { MP_ROM_QSTR(MP_QSTR_growths), MP_ROM_PTR(&gc_growths_obj) },\n"
     "    { MP_ROM_QSTR(MP_QSTR_refs), MP_ROM_PTR(&gc_refs_obj) },\n" % MARKER),
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
            sys.exit("!! gc census patch did not apply -- %s hunk %r matches %d times "
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
        print("== patched py/gc.c + py/modgc.c: gc.area_map(), gc.growths(), gc.refs()")


if __name__ == "__main__":
    main()
