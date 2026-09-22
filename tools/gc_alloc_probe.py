#!/usr/bin/env python3
"""What one heap allocation costs a running cart, measured on a board.

    python3 tools/gc_alloc_probe.py --board tdeck
    python3 tools/gc_alloc_probe.py --board guition_s3 --cart "Brick Siege"

MicroPython's `gc_alloc` finds a block run by walking the allocation table
from ONE hint per heap area. A single-block allocation advances the hint past
itself; a multi-block one does not, and every collect resets the hint to the
start of the area. So after a collect each multi-block allocation walks the
table from the hint through every hole too small for it, and the walk is as
long as the live region is fragmented -- `moy_prof` shows it as `gc_alloc`,
and it is the one symbol whose share differs per board on the same cart.

This times tuple allocations of 1, 2, 4, 16 and 64 blocks with the cart UP,
so the heap is the shape the game left it, twice: as found, and directly
after `gc.collect()`. It also reports the live set, where a fresh block lands
(internal SRAM or PSRAM, by address), and how fast the heap fills while the
cart plays, which is how often it collects. A tuple built by `(0,) * k` is
exactly one allocation of 3 + k words.

`--board` is required for the reason every serial tool here gives: the line
state at open is per board and opposite.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p4_autotest import add_board_args, board_from_args   # noqa: E402

# Tuple lengths whose allocation is exactly 1, 2, 4, 16 and 64 blocks of 16
# bytes (3 header words + k items, 4-byte words).
_K_FOR_BLOCKS = {1: 1, 2: 5, 4: 13, 16: 61, 64: 253}

_DEFINE = """
import gc, time
def _gap(k, cnt, collect):
    if collect:
        gc.collect()
    keep = [None] * cnt
    t0 = time.ticks_us()
    for i in range(cnt):
        keep[i] = (0,) * k
    dt = time.ticks_diff(time.ticks_us(), t0)
    return dt / cnt
def _gcs():
    return (gc.mem_alloc(), gc.mem_free(), hex(id([0])), hex(id((0,) * 1000)))
def _gca():
    return gc.mem_alloc()
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_board_args(ap)
    ap.add_argument("--cart", default="Brick Siege",
                    help="cart to run while probing (default: Brick Siege)")
    ap.add_argument("--count", type=int, default=100, help="allocations per size (default 100)")
    ap.add_argument("--runs", type=int, default=3, help="runs per size; the median prints")
    ap.add_argument("--watch", type=int, default=0, metavar="N",
                    help="instead: sample the as-found 4-block cost N times a second apart, "
                         "never collecting, to see whether the hint sits still or drifts")
    a = ap.parse_args(argv)
    board = board_from_args(a, log=lambda s: None)
    board.leave_cart()
    try:
        if board.cmd("run %s" % a.cart, wait_for="REMOTE run", timeout=20.0) is None:
            sys.exit("board never acknowledged `run %s`" % a.cart)
        if "no cart match" in board.lines[-1]:
            sys.exit("no cart matches %r" % a.cart)
        board.drain(4.0)
        if not board.pyexec(_DEFINE):
            sys.exit("could not define the probe: %s" % board.last_error)

        if a.watch:
            print("  as-found 4-block us, one sample a second:")
            for _ in range(a.watch):
                r = board.pyval("ws._g['_gap'](13, %d, 0)" % a.count, timeout=60)
                m = board.pyval("ws._g['_gca']()", timeout=30)
                print("  %8.1f   used=%dK" % (r if r is not None else -1,
                                             (m or 0) // 1024), flush=True)
                board.drain(1.0)
            return

        st = board.pyval("ws._g['_gcs']()", timeout=30)
        if st is None:
            sys.exit("heap stats failed: %s" % board.last_error)
        used, free, small, big = st
        print("  heap as found: used=%dK free=%dK  1-block lands at %s, 64-block at %s"
              % (used // 1024, free // 1024, small, big), flush=True)
        m0 = board.pyval("ws._g['_gca']()", timeout=30)
        board.drain(2.0)
        m1 = board.pyval("ws._g['_gca']()", timeout=30)
        rate = (m1 - m0) / 2.0 if m0 is not None and m1 is not None else None

        print("  blocks   as-found us   post-collect us")
        for nb, k in sorted(_K_FOR_BLOCKS.items()):
            row = []
            for collect in (0, 1):
                runs = []
                for _ in range(a.runs):
                    r = board.pyval("ws._g['_gap'](%d, %d, %d)" % (k, a.count, collect),
                                    timeout=60)
                    if r is None:
                        sys.exit("probe failed at %d blocks: %s" % (nb, board.last_error))
                    runs.append(r)
                row.append(sorted(runs)[len(runs) // 2])
            print("  %6d   %11.1f   %15.1f" % (nb, row[0], row[1]), flush=True)

        st = board.pyval("ws._g['_gcs']()", timeout=30)
        if st is not None:
            print("  live set after collect: used=%dK free=%dK" % (st[0] // 1024, st[1] // 1024))
            if rate:
                print("  fills at %dK/s while playing -> a collect every ~%.0fs"
                      % (rate // 1024, st[1] / rate if rate > 0 else float("inf")))
    finally:
        board.leave_cart()


if __name__ == "__main__":
    main()
