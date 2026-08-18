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

`Bench Lua` is the line-faithful twin (same phases, same LCG workload). The
SPEC.md 4.1 sandbox has no print, so the twin writes its report into PMEM
instead (layout v1, the comment block in bench.moy/main.py -- both carts write
the same cells), and this tool reads it live through `moycore.pmem_image` over
the dev channel, synthesizing the same BENCHCART lines -- so `show`, `--json`
and `--diff` speak one format for both twins. `--frame` still captures the
final screen if you want the glass itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))ok
# The board-facing imports (p4_autotest -> pyserial, the `device` extra that
# `make setup` deliberately does not install) live in main(), not here: the
# pmem-layout helpers below are imported by tests/test_bench_pmem_report.py,
# which must collect on a runner with no serial stack at all.


# PMEM REPORT LAYOUT v1 -- the carts' side of this is the comment block in
# system_carts/bench.moy/main.py; keep the three copies in lock-step.
PMEM_MAGIC = 45948
VERB_NAMES = ("cls", "rect", "circ", "line", "pix", "print", "rectb",
              "circb", "tri", "spr", "map", "sspr", "tline")
PHASE_NAMES = ("idle", "logic", "draw", "silent", "sound")


def pmem_lines(cells):
    """Synthesize BENCHCART lines from the carts' pmem report block, so the
    Lua twin (whose sandbox has no print) feeds the same parse/show/diff path
    the Python cart's serial lines do. Rows are id-checked because only the
    done FLAG is zeroed at cart start -- a stale row from an older layout
    must read as absent, not as a number."""
    if len(cells) < 128 or cells[0] != PMEM_MAGIC or cells[1] != 1:
        return []
    out = []
    for i in range(min(cells[2], len(VERB_NAMES))):
        vid, k, best = cells[8 + i * 3:8 + i * 3 + 3]
        if 0 <= vid < len(VERB_NAMES) and k > 0:
            out.append("BENCHCART verb=%s k=%d best_ms=%d"
                       % (VERB_NAMES[vid], k, best))
    for pid, name in enumerate(PHASE_NAMES):
        row = cells[64 + pid * 8:64 + pid * 8 + 8]
        if row[0] != pid or row[1] <= 0:
            continue
        out.append("BENCHCART phase=%s n=%d p50=%.1f p90=%.1f p99=%.1f"
                   " worst=%.1f fps=%.1f"
                   % (name, row[1], row[2] / 10.0, row[3] / 10.0,
                      row[4] / 10.0, row[5] / 10.0, row[6] / 10.0))
    return out


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
    # Arm the live pmem reader. moycore only fills it while a LUA run is
    # active (the Python cart's pmem is host-side, but that cart reports over
    # serial anyway); the poll below is a cheap no-op for the Python twin.
    board.pyexec("ws._bm = __import__('array').array('i', bytearray(1024))")
    poll = "__import__('moycore').pmem_image(ws._bm) and ws._bm[3]"
    n0 = len(board.lines)
    end = time.time() + secs
    cells = None
    while time.time() < end:
        board.drain(1.0)
        if any(l.startswith("BENCHCART phase=game_snd") for l in board.lines[n0:]):
            board.drain(1.0)
            break
        if board.pyval(poll, timeout=8.0) == 1:
            cells = board.pyval("list(ws._bm)", timeout=8.0)
            break
    lines = board.lines[n0:]
    if cells:
        lines = lines + pmem_lines(cells)
    return lines


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
        print("  (no verb numbers arrived -- neither BENCHCART serial lines"
              " nor a finished pmem report; did the cart run to DONE?)")
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
    ap.add_argument("--attach", action="store_true",
                    help="open with DTR/RTS HIGH and never pulse reset -- the"
                         " T-Deck arrangement (USB-Serial/JTAG: an open with"
                         " both lines low is a chip reset)")
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

    from p4_autotest import P4Board
    import p4_conformance as PC

    log = print if a.verbose else (lambda *x: None)
    board = P4Board(a.port, log=(lambda s: log("  | " + s[:120])),
                    dtr=a.attach, rts=a.attach)
    try:
        board.drain(0.5)
        if board.pyval("1", timeout=8.0) != 1:
            if a.attach:
                raise RuntimeError("board not answering (attach mode never resets)")
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
