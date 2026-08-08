#!/usr/bin/env python3
"""Run the Bench CART on the P4 and collect its per-verb numbers.

    python3 tools/p4_cart_bench.py                     # Bench (python)
    python3 tools/p4_cart_bench.py --cart "Bench Lua"  # the Lua twin
    python3 tools/p4_cart_bench.py --json after.json
    python3 tools/p4_cart_bench.py --diff before.json after.json

Not to be confused with `p4_bench.py`, which benches the console's own UI
panels end-to-end. This one drives `system_carts/bench.moy`: a MICRO phase
timing one draw VERB per frame in adaptively-sized batches (best-of-8, so a GC
landing is excluded rather than averaged in), then a busy scene. It prints
`BENCHCART` lines to serial, which is what this reads. Use it to A/B a change to
the raster kernel -- same workload every run, no play skill, no feel.

`Bench Lua` is the line-faithful twin (same phases, same LCG workload), but the
SPEC.md 4.1 sandbox has no print, so its report only reaches the glass. Ask for
it with --cart and pass --frame to capture the final screen instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import P4Board            # noqa: E402
import p4_conformance as PC                 # noqa: E402


def run_bench(board, title, secs, log):
    board.pyexec("ws.exit()")
    board.drain(0.8)
    # Frame eaters OFF: perf_capture and the FPS chip are themselves a cost
    # (#68), and a verb timed with them on is not the shipping number.
    board.cmd("diag 0", wait_for="REMOTE diag")
    if board.cmd("run %s" % title, wait_for="REMOTE run", timeout=20.0) is None:
        raise RuntimeError("board never acknowledged `run %s`" % title)
    if "no cart match" in board.lines[-1]:
        raise RuntimeError("no cart titled %r on this board" % title)
    n0 = len(board.lines)
    end = time.time() + secs
    while time.time() < end:
        board.drain(1.0)
        if any(l.startswith("BENCHCART phase=game_snd") for l in board.lines[n0:]):
            board.drain(1.0)
            break
    return board.lines[n0:]


def parse(lines):
    verbs, phases, spread = {}, {}, {}
    for ln in lines:
        if not ln.startswith("BENCHCART "):
            continue
        f = {}
        for tok in ln[10:].split():
            if "=" in tok:
                key, val = tok.split("=", 1)
                f[key] = val
        if "verb" in f:
            try:
                k = max(1, int(f["k"]))
                verbs[f["verb"]] = float(f["best_ms"]) / k
                # The spread is what says whether the number means anything.
                # `line` once reported 31.2us/op and 62.5 on the same build,
                # and only the min was recorded, so nothing in the output
                # hinted that the measurement was bimodal.
                if "max_ms" in f:
                    spread[f["verb"]] = (float(f["med_ms"]) / k,
                                         float(f["max_ms"]) / k, k)
            except (ValueError, KeyError):
                pass
        elif "phase" in f:
            phases[f["phase"]] = {k: v for k, v in f.items() if k != "phase"}
    return {"verbs": verbs, "phases": phases, "spread": spread}


def show(res):
    v = res["verbs"]
    sp = res.get("spread") or {}
    if not v:
        print("  (no BENCHCART verb lines -- a Lua cart reports on the glass only)")
    else:
        print("  %-8s %10s %10s %10s %7s   %s"
              % ("verb", "min", "med", "max", "k", "spread"))
        for name in sorted(v, key=lambda k: -v[k]):
            lo = v[name] * 1000.0
            if name in sp:
                med, mx, k = sp[name][0] * 1000.0, sp[name][1] * 1000.0, sp[name][2]
                # Flag anything whose max is more than 25% off its min: that is
                # a verb the harness is not measuring cleanly, and its number
                # should not be quoted in a comparison.
                flag = "  <-- UNSTABLE" if mx > lo * 1.25 else ""
                print("  %-8s %10.1f %10.1f %10.1f %7d   %4.2fx%s"
                      % (name, lo, med, mx, k, mx / lo if lo else 0.0, flag))
            else:
                print("  %-8s %10.1f %10s %10s" % (name, lo, "-", "-"))
    # k is printed because it is the OTHER instability, and the spread column
    # cannot see it: spread is the variation WITHIN a batch size, while the
    # batch size itself is chosen per run by a doubling ladder that stops at
    # the first rung over 25ms. `line` read 31.2us/op at k=800 and 62.5 at
    # k=400 on the same build -- two rungs, one of them wrong, and nothing in
    # the old output said which had been used. Comparing two builds means
    # checking they landed on the same k.
    for ph, f in sorted(res["phases"].items()):
        print("  phase %-9s %s" % (ph, " ".join("%s=%s" % kv for kv in sorted(f.items()))))


def diff(a, b):
    """A/B two runs. NOTE THE NOISE FLOOR: the cart sizes its batches against a
    ms clock, so a verb can move ~5% between two builds whose code for it is
    byte-identical. Treat anything under 1.10x as unmoved, and confirm a real
    change by re-running rather than by believing one number."""
    va, vb = a["verbs"], b["verbs"]
    print("%-8s %10s %10s %8s" % ("verb", "before us", "after us", "ratio"))
    for name in sorted(set(va) | set(vb)):
        x, y = va.get(name), vb.get(name)
        if x is None or y is None or not x:
            print("%-8s %10s %10s %8s" % (name, x or "-", y or "-", "-"))
            continue
        r = y / x
        flag = "  <-- SLOWER" if r > 1.10 else ("  faster" if r < 0.90 else "")
        print("%-8s %10.1f %10.1f %7.2fx%s"
              % (name, x * 1000.0, y * 1000.0, r, flag))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cart", default="Bench")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--secs", type=float, default=180.0)
    ap.add_argument("--json", help="write the parsed result here")
    ap.add_argument("--frame", help="also dump the final frame (raw indices)")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    if a.diff:
        with open(a.diff[0]) as f:
            before = json.load(f)
        with open(a.diff[1]) as f:
            after = json.load(f)
        diff(before, after)
        return 0

    log = print if a.verbose else (lambda *x: None)
    board = P4Board(a.port, log=(lambda s: log("  | " + s[:120])))
    try:
        board.drain(0.5)
        if board.pyval("1", timeout=8.0) != 1:
            board.reset()
        res = parse(run_bench(board, a.cart, a.secs, log))
        show(res)
        if a.frame:
            with open(a.frame, "wb") as f:
                f.write(PC.capture(board, log=log))
            print("  frame -> %s" % a.frame)
        if a.json:
            with open(a.json, "w") as f:
                json.dump(res, f, indent=2, sort_keys=True)
            print("  -> %s" % a.json)
        board.pyexec("ws.exit()")
    finally:
        board.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
