#!/usr/bin/env python3
"""Collection pauses and heap areas, read through MicroPython's `gc` module.

    python3 tools/patch_gc_meters.py <micropython checkout>

MicroPython neither counts nor times its collections, and does not say how many
areas a `MICROPY_GC_SPLIT_HEAP_AUTO` heap has grown to. The patch adds both, as
two functions on the stock `gc` module:

    gc.pauses() -> (collections, pause_us, longest_us)
        Every collection `gc_collect()` runs -- an explicit `gc.collect()` and
        the automatic one inside `gc_alloc` alike -- timed from the top of
        `gc_collect_start` to the end of `gc_collect_end`: one
        `mp_hal_ticks_us()` read at each end. The first two count since boot
        and wrap at 2**32; the third is the longest pause since the previous
        call, which resets it, so it has ONE reader: the PERF line
        (`runtime/frame_loop.PerfSampler`). `gc_sweep_all` at a VM stop runs
        `gc_collect_end` without `gc_collect_start` and is not counted.

    gc.areas() -> (areas, held_bytes)
        The heap's areas and the bytes they take from the system allocator,
        tables and pool. Read by the dev channel's `heapcaps` word.

A host without the patch has neither function, and its readers report absence.

Applied by `moybyte_patch_gc_meters` in tools/esp32_build_lib.sh, to
`py/gc.c` and `py/modgc.c`. All-or-nothing: every hunk must match exactly once
or neither file is written and the exit is non-zero, naming the hunk. A tree
already carrying the marker is left as it is. Independent of
tools/patch_gc_run_hints.py: no hunk of either touches the other's lines.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: gc meters"

_COUNTERS = """\
// %s (tools/patch_gc_meters.py): [0] collections and [1] their pause us,
// since boot, wrapping; [2] the longest pause since gc.pauses() last read it.
// Built where the gc module is: mpy-cross compiles this file with no clock.
#if MICROPY_PY_GC
uint32_t moybyte_gc_pause[3];
static uint32_t moybyte_gc_pause_t0;
static bool moybyte_gc_pause_open;
#define MOYBYTE_GC_PAUSE_START() do { \\
        moybyte_gc_pause_t0 = (uint32_t)mp_hal_ticks_us(); \\
        moybyte_gc_pause_open = true; \\
} while (0)
#define MOYBYTE_GC_PAUSE_END() do { \\
        if (moybyte_gc_pause_open) { \\
            uint32_t us = (uint32_t)mp_hal_ticks_us() - moybyte_gc_pause_t0; \\
            moybyte_gc_pause_open = false; \\
            moybyte_gc_pause[0] += 1; \\
            moybyte_gc_pause[1] += us; \\
            if (us > moybyte_gc_pause[2]) { \\
                moybyte_gc_pause[2] = us; \\
            } \\
        } \\
} while (0)
#else
#define MOYBYTE_GC_PAUSE_START()
#define MOYBYTE_GC_PAUSE_END()
#endif

""" % MARKER

_ACCESSORS = """\
// %s (tools/patch_gc_meters.py).
extern uint32_t moybyte_gc_pause[3];

// pauses(): (collections, pause us, longest pause us). The longest is since
// the previous call, which resets it.
static mp_obj_t gc_pauses(void) {
    uint32_t n = moybyte_gc_pause[0];
    uint32_t us = moybyte_gc_pause[1];
    uint32_t longest = moybyte_gc_pause[2];
    moybyte_gc_pause[2] = 0;
    mp_obj_t t[3] = {
        mp_obj_new_int_from_uint(n),
        mp_obj_new_int_from_uint(us),
        mp_obj_new_int_from_uint(longest),
    };
    return mp_obj_new_tuple(3, t);
}
MP_DEFINE_CONST_FUN_OBJ_0(gc_pauses_obj, gc_pauses);

// areas(): (areas, bytes held). An area after the first starts with its own
// struct; the first's is static.
static mp_obj_t gc_areas(void) {
    size_t n = 0;
    size_t held = 0;
    for (mp_state_mem_area_t *area = &MP_STATE_MEM(area); area != NULL;) {
        n++;
        held += (size_t)(area->gc_pool_end
            - (area == &MP_STATE_MEM(area) ? area->gc_alloc_table_start : (byte *)area));
        #if MICROPY_GC_SPLIT_HEAP
        area = area->next;
        #else
        area = NULL;
        #endif
    }
    mp_obj_t t[2] = {MP_OBJ_NEW_SMALL_INT(n), mp_obj_new_int_from_uint(held)};
    return mp_obj_new_tuple(2, t);
}
MP_DEFINE_CONST_FUN_OBJ_0(gc_areas_obj, gc_areas);

""" % MARKER

# (file, name, stock text, patched text). Each stock text must occur exactly
# once, or the whole patch is refused.
HUNKS = [
    ("py/gc.c", "include",
     '#include "py/gc.h"\n'
     '#include "py/runtime.h"\n',
     '#include "py/gc.h"\n'
     '#include "py/runtime.h"\n'
     '#if MICROPY_PY_GC // %s\n'
     '#include "py/mphal.h"\n'
     '#endif\n' % MARKER),
    ("py/gc.c", "collect start",
     "void gc_collect_start(void) {\n"
     "    gc_collect_start_common();\n",
     _COUNTERS +
     "void gc_collect_start(void) {\n"
     "    MOYBYTE_GC_PAUSE_START(); // Moybyte: gc meters\n"
     "    gc_collect_start_common();\n"),
    ("py/gc.c", "collect end",
     "    GC_EXIT();\n"
     "    #if MICROPY_PY_WEAKREF\n"
     "    gc_weakref_sweep();\n"
     "    #endif\n"
     "}\n",
     "    GC_EXIT();\n"
     "    #if MICROPY_PY_WEAKREF\n"
     "    gc_weakref_sweep();\n"
     "    #endif\n"
     "    MOYBYTE_GC_PAUSE_END(); // Moybyte: gc meters\n"
     "}\n"),
    ("py/modgc.c", "accessors",
     "static const mp_rom_map_elem_t mp_module_gc_globals_table[] = {\n",
     _ACCESSORS +
     "static const mp_rom_map_elem_t mp_module_gc_globals_table[] = {\n"),
    ("py/modgc.c", "table",
     "    { MP_ROM_QSTR(MP_QSTR_mem_alloc), MP_ROM_PTR(&gc_mem_alloc_obj) },\n",
     "    { MP_ROM_QSTR(MP_QSTR_mem_alloc), MP_ROM_PTR(&gc_mem_alloc_obj) },\n"
     "    { MP_ROM_QSTR(MP_QSTR_pauses), MP_ROM_PTR(&gc_pauses_obj) }, // %s\n"
     "    { MP_ROM_QSTR(MP_QSTR_areas), MP_ROM_PTR(&gc_areas_obj) },\n" % MARKER),
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
            sys.exit("!! gc meters patch did not apply -- %s hunk %r matches %d times "
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
        print("== patched py/gc.c + py/modgc.c: gc.pauses() and gc.areas()")


if __name__ == "__main__":
    main()
