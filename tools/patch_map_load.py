#!/usr/bin/env python3
"""A hash map grows while a quarter of its slots are still free.

    python3 tools/patch_map_load.py <micropython checkout>

py/map.c grows a hash table only when it is completely full: an insert walks
the probe sequence back to where it started before `mp_map_rehash` runs. A
lookup that MISSES walks the linear probe until it meets an empty slot, so in a
nearly full table a miss reads most of it. Every method call on an instance is
one: `self.paint()` looks in the instance's members first and falls through to
the class only after that walk. The console's long-lived objects carry dozens
of attributes (the home carousel 70 in a 73-slot table), and their methods are
called every frame.

The patch keeps a table above twelve slots at most three quarters full: an
insert that would pass that grows it first, and a rehash picks the first size
on the ladder that holds the map below the line. Twelve slots and under keep
the stock rule; those tables fit one to three GC blocks exactly and their
longest miss is twelve probes. Lookups, deletes and the order of the ladder
are unchanged. On the board model it took 6-9% of the interpreter's
instructions out of a carousel fling, an icons drag, a desk window drag and a
Brick Siege frame, for ~45 KB more heap in a booted console
(docs/perf_native_gap_v1.md §6 has the table).

Applied by `moybyte_patch_map_load` in tools/esp32_build_lib.sh, by
firmware/web_runner/build.sh and by `make unix-micropython`, to `py/map.c`.
All-or-nothing: each hunk must match exactly once or nothing is written and the
exit is non-zero. A tree already carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: a hash map grows at three quarters full"
FILE = "py/map.c"

HUNKS = (
    (
        "static void mp_map_rehash(mp_map_t *map) {\n"
        "    size_t old_alloc = map->alloc;\n"
        "    size_t new_alloc = get_hash_alloc_greater_or_equal_to(map->alloc + 1);\n",

        "// %s (tools/patch_map_load.py):\n"
        "// a lookup that misses walks the probe to an empty slot, so a table\n"
        "// above twelve slots keeps a quarter of them empty.\n"
        "#define MOY_MAP_CROWDED(used, alloc) ((size_t)(alloc) > 12 && (size_t)(used) > (size_t)(alloc) - ((size_t)(alloc) >> 2))\n"
        "\n"
        "static void mp_map_rehash(mp_map_t *map) {\n"
        "    size_t old_alloc = map->alloc;\n"
        "    size_t new_alloc = get_hash_alloc_greater_or_equal_to(map->alloc + 1);\n"
        "    while (MOY_MAP_CROWDED(map->used + 1, new_alloc)) {\n"
        "        new_alloc = get_hash_alloc_greater_or_equal_to(new_alloc + 1);\n"
        "    }\n" % MARKER,
    ),
    (
        "            // found NULL slot, so index is not in table\n"
        "            if (lookup_kind == MP_MAP_LOOKUP_ADD_IF_NOT_FOUND) {\n"
        "                map->used += 1;\n",

        "            // found NULL slot, so index is not in table\n"
        "            if (lookup_kind == MP_MAP_LOOKUP_ADD_IF_NOT_FOUND) {\n"
        "                if (MOY_MAP_CROWDED(map->used + 1, map->alloc)) {\n"
        "                    mp_map_rehash(map);\n"
        "                    start_pos = pos = hash % map->alloc;\n"
        "                    avail_slot = NULL;\n"
        "                    continue;\n"
        "                }\n"
        "                map->used += 1;\n",
    ),
)


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when a stock hunk does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    for i, (stock, _patched) in enumerate(HUNKS):
        n = text.count(stock)
        if n != 1:
            sys.exit("!! map-load patch did not apply -- %s's hunk %d matches %d times "
                     "(the stock lines changed shape); nothing written" % (FILE, i + 1, n))
    for stock, patched in HUNKS:
        text = text.replace(stock, patched)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir", help="the MicroPython checkout to patch (its py/map.c is what changes)")
    a = ap.parse_args(argv)
    if apply(a.mpy_dir) == "patched":
        print("== patched py/map.c: a hash map grows at three quarters full")


if __name__ == "__main__":
    main()
