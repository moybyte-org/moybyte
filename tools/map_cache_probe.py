#!/usr/bin/env python3
"""How much the map-lookup cache can buy a lookup, measured on a board.

    python3 tools/map_cache_probe.py --board tdeck
    python3 tools/map_cache_probe.py --board p4 --names 24 100 160 300

MicroPython's `mp_map_lookup` consults one shared hint table
(`MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE` slots, a byte each) keyed by the KEY
object alone, before hashing or scanning a map. This times an instance
attribute lookup against the number of DISTINCT attribute names in the hot
set: a set that fits the reachable slots hits, one that overflows them falls
through to the full probe, and the step between the two is the ceiling of
what any cache size can give a real cart per lookup. It says nothing about
how many of a cart's lookups are misses -- `moy_prof`'s `mp_map_lookup`
share does that -- which is why the frame A/B (#77) needed both.

The names intern consecutively, so they land in consecutive slots: this is
a capacity reading, not a collision-pattern one. Per-call overhead is
measured with an empty function and subtracted. `--board` is required for
the reason every serial tool here gives: the line state at open is per
board and opposite.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import add_board_args, board_from_args   # noqa: E402

# Uploaded once into the harness's persistent namespace (`ws._g`), then
# called per size. Everything stays on the board: the generated function is
# one LOAD_ATTR per name, so the loop measures the lookup and nothing else.
_DEFINE = """
import time
def _mlc(n):
    class K:
        pass
    k = K()
    for i in range(n):
        setattr(k, 'n%d' % i, i)
    src = 'def g(k):\\n' + ''.join('    k.n%d\\n' % i for i in range(n))
    d = {}
    exec(src, d)
    g = d['g']
    it = max(1, 60000 // n)
    def g0(k):
        pass
    g(k)
    t0 = time.ticks_us()
    for _ in range(it):
        g0(k)
    t1 = time.ticks_us()
    for _ in range(it):
        g(k)
    t2 = time.ticks_us()
    return (time.ticks_diff(t2, t1) - time.ticks_diff(t1, t0)) / (it * n)
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_board_args(ap)
    ap.add_argument("--names", type=int, nargs="+", default=[24, 100, 160],
                    help="hot-set sizes to time (default 24 100 160)")
    ap.add_argument("--runs", type=int, default=3, help="runs per size; the median prints")
    a = ap.parse_args(argv)

    board = board_from_args(a, log=lambda s: None)
    if not board.pyexec(_DEFINE):
        sys.exit("could not define the probe: %s" % board.last_error)
    print("  names   us/lookup   runs")
    for n in a.names:
        runs = []
        for _ in range(a.runs):
            r = board.pyval("ws._g['_mlc'](%d)" % n, timeout=60)
            if r is None:
                sys.exit("probe failed at n=%d: %s" % (n, board.last_error))
            runs.append(r)
        print("  %5d   %9.3f   %s" % (n, sorted(runs)[len(runs) // 2],
                                      " ".join("%.3f" % x for x in runs)), flush=True)


if __name__ == "__main__":
    main()
