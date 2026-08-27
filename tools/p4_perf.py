#!/usr/bin/env python3
"""Per-cart fps on the P4, over the board's own serial dev commands.

    python3 tools/p4_perf.py                       # the default roster
    python3 tools/p4_perf.py "Brick Siege" Sakura  # named carts
    python3 tools/p4_perf.py --secs 12 --diag      # longer, with the frame eaters

The board prints a PERF line every ~2s carrying drawn-fps and the frame budget
split (draw / flush / logic / render / chrome). This runs each cart, waits for
the numbers to settle, and reports the median of the samples it saw -- median
rather than mean because a GC spike lands in exactly one sample and should not
move the answer.

DIAG IS OFF BY DEFAULT and that matters: `perf_capture` and the on-screen FPS
chip are themselves frame eaters (#68), so a measurement taken with them on is
not the shipping number. The fps= field stays valid either way -- it reads
_frames_drawn, not the EMAs -- so the only thing lost with them off is the
per-phase ms breakdown, which --diag turns back on when you want it.

A row marked LINKED is NOT that cart's fps, and this tool asks so it can say so
(2026-08-27). A second console left in the same two-player cart forms a real
ESP-NOW match, and a linked game draws on the shared 30Hz tick by design (#65) --
so Brick Siege reads 30 against the 62 it runs solo, with nothing in the PERF
line naming the cause. Move the other console off the cart and re-measure.

Numbers live in issue #66, not here. This tool produces them; it does not
remember them.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import P4Board            # noqa: E402

# The carts worth watching: the historically slowest (Brick Siege), the two Lua
# twins against their Python originals (the #67 comparison), the 3D-verb carts
# that this raster work actually touches, and a couple of cheap ones as a
# control -- if a control moves, the change was not in the verbs.
DEFAULT_ROSTER = [
    "Brick Siege", "Brick Siege Lua",
    "Sakura", "Sakura Lua",
    "Ray Lua",
    "Hop Quest", "Sky Run", "Letter Blitz", "Star Catcher",
]


def parse_perf(line):
    """A PERF line -> dict of its fields, or None."""
    if not line.startswith("PERF "):
        return None
    out = {}
    for tok in line[5:].split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        if k == "fps":
            v = v.split("/")[0]           # drawn/looped -- drawn is the one
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out or None


def measure(board, title, secs, log):
    board.pyexec("ws.exit()")
    board.drain(0.8)
    if board.cmd("run %s" % title, wait_for="REMOTE run", timeout=20.0) is None:
        raise RuntimeError("board never acknowledged `run %s`" % title)
    line = board.lines[-1]
    if "no cart match" in line:
        return None
    # Discard the first samples: a cart's opening frames build sprite caches and
    # touch cold flash, which is real but is not what it runs at.
    board.drain(4.0)
    n0 = len(board.lines)
    board.drain(secs)
    samples = [p for p in (parse_perf(l) for l in board.lines[n0:]) if p]
    if not samples:
        return None
    # Ask the RADIO what this run was, while it is still running: a peer on the
    # desk makes a two-player cart a match, and #65's lockstep gate then draws
    # on the shared 30Hz tick -- a correct 30 that reads exactly like a
    # regression, because no PERF field names it (2026-08-27: a T-Deck parked
    # in Brick Siege cost a night of paired carve-vs-dev captures).
    #
    # Bare bool, not repr(): the device reprs the result already, so a wrapped
    # expression hands back the STRING 'True' -- truthy, not True, marks
    # nothing. A board that cannot answer stays unmarked, which is the honest
    # reading of "this board did not say".
    linked = board.pyval(
        "bool(getattr(ws, 'link', None) and ws.link.status()[2])",
        timeout=15.0) is True
    return {
        "n": len(samples),
        "linked": linked,
        "fps": statistics.median(s.get("fps", 0) for s in samples),
        "min": min(s.get("fps", 0) for s in samples),
        "cart": samples[-1].get("cart", title),
        "phases": {k: statistics.median(s.get(k, 0) for s in samples)
                   for k in ("draw", "flush", "logic", "render", "chrome")},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("carts", nargs="*", help="cart titles (default: the roster)")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--secs", type=float, default=8.0, help="sample window per cart")
    ap.add_argument("--diag", action="store_true",
                    help="leave perf_capture + the FPS chip ON (per-phase ms, "
                         "but NOT the shipping fps)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    log = print if a.verbose else (lambda *x: None)
    roster = a.carts or DEFAULT_ROSTER
    board = P4Board(a.port, log=(lambda s: log("  | " + s[:120])))
    try:
        board.drain(0.5)
        if board.pyval("1", timeout=8.0) != 1:
            board.reset()
        board.cmd("diag %d" % (1 if a.diag else 0), wait_for="REMOTE diag")
        board.drain(0.5)
        print("%-18s %6s %6s %5s   %s"
              % ("cart", "fps", "worst", "n", "draw/flush/logic/render/chrome ms"
                 if a.diag else ""))
        rows = []
        linked = False
        for title in roster:
            try:
                r = measure(board, title, a.secs, log)
            except RuntimeError as exc:
                print("%-18s  ERROR %s" % (title, exc))
                continue
            if r is None:
                print("%-18s  (not on this board)" % title)
                continue
            p = r["phases"]
            print("%-18s %6.1f %6.1f %5d   %s%s"
                  % (title, r["fps"], r["min"], r["n"],
                     ("%.0f/%.0f/%.0f/%.0f/%.0f"
                      % (p["draw"], p["flush"], p["logic"], p["render"],
                         p["chrome"])) if a.diag else "",
                     "  LINKED" if r["linked"] else ""))
            linked = linked or r["linked"]
            rows.append((title, r))
        board.pyexec("ws.exit()")
        if linked:
            print("\nLINKED: another console on this desk is in the same "
                  "two-player cart, so that\nrun was a real ESP-NOW match and "
                  "drew on the shared 30Hz tick (#65). That\nnumber is the "
                  "match's, not the cart's -- move the peer off the cart and "
                  "re-measure.")
        return 0 if rows else 1
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())
