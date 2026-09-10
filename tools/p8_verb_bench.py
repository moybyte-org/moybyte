#!/usr/bin/env python3
"""What ONE p8 verb costs a cart, on a board, in nanoseconds per call.

    python3 tools/p8_verb_bench.py --board tdeck --push     # first run
    python3 tools/p8_verb_bench.py --board tdeck            # carts already there
    python3 tools/p8_verb_bench.py --board tdeck --json after.json
    python3 tools/p8_verb_bench.py --diff before.json after.json

WHY THIS EXISTS. The p8 tier's levers are verbs: a shim body moves into C and
the cart is meant to get faster. Nothing here could PRICE one. `p4_perf.py`
reports a cart's fps -- too coarse to see a verb, and moved by the tick
divisor. moycore's `verbs` profiler reports per-verb ms a frame, but it times
the wrapper, so it cannot see the crossing that reaches it and it perturbs the
frame it measures. `p4_cart_bench.py` prices DRAW verbs through the moy cart
API; a p8 verb only exists inside a ported cart's shim and never appears there.

So on 2026-09-10 a bit-lane change cost dank tomb 18% of its draw and the
question "did the verbs get slower?" had no instrument. The theory (they did)
survived two profilers and was wrong: this bench says `band` costs 2,047 ns on
two integers and 3,279 with a fraction, IDENTICAL before and after, and an
optimisation was nearly built on the wrong number. Keep the instrument.

HOW IT WORKS. One tiny cart per operation, each running `n` calls of exactly
one expression in `_update`, plus a control that runs the empty loop. The
board's own `tick_split` reports the update half; subtract the control and
divide by `n`. No profiler, no wrapper, no perturbation -- the cost includes
the Lua call opcode and the crossing, which is what a cart actually pays and
what deleting a call actually saves.

READ IT AGAINST THE OTHER NUMBER. The `verbs` profiler's ~1.0 us is a verb's
C-side body; this is what the cart pays to reach it (1,534 ns for `flr` on the
T-Deck). Both are true. Use the first to reason about a verb's insides and
this one to reason about deleting a call.

ADDING A VERB? Add a row to OPS. That is the whole cost of keeping this
current, and it is why the table lives here rather than in a cart.

PASS `--port`. Four boards declare usb id 303a:1001, so resolving a port by id
probes every candidate and the headless Zero costs a full timeout each run.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Board-facing imports live in main(), not here: the pure helpers below are
# imported by tests/test_p8_verb_bench.py, which collects on a runner with no
# serial stack at all (the same rule p4_cart_bench.py follows).

CALLS = 2000          # calls per tick: ~6ms at 3us, well inside a 30fps period
CONTROL = "control"

# (slug, expression, what it prices). The control MUST come first: every other
# row is quoted net of it. An expression may not be constant-folded by Lua --
# a call never is, and a bare `i` keeps the loop honest.
OPS = (
    (CONTROL,  "a=1.5",                "the empty loop, subtracted from all"),
    ("flr",    "a=flr(1.5)",           "the call floor: a C verb that does nothing"),
    ("bandi",  "a=band(3,5)",          "band, two integers (the fast lane)"),
    ("bandf",  "a=band(1.5,-1)",       "band, a fraction (the 16.16 lane)"),
    ("shr",    "a=shr(3,1)",           "shr, the verb"),
    ("shl",    "a=shl(1,4)",           "shl, the verb"),
    ("opshr",  "a=3>>1",               "`>>`, the operator (same C body)"),
    ("opband", "a=1.5&-1",             "`&`, the operator, a fraction"),
    ("rnd",    "a=rnd(8)",             "rnd (C verb since moy-spec 5633c6d)"),
    ("mget",   "a=mget(1,1)",          "mget: an int verb, for reference"),
    ("peek",   "a=peek(0x4300)",       "peek: the cheapest memory verb"),
    ("add",    "a=i+1",                "no call at all: one VM instruction"),
)

P8_HEAD = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n")


def p8_source(expr, calls=CALLS):
    """The cart for one operation. Pure text, so a test can port and run it."""
    return (P8_HEAD +
            "-- tools/p8_verb_bench.py: one operation, %d times a tick. the\n"
            "-- host reads the update half off the board's tick_split and\n"
            "-- subtracts the control cart, which runs the empty loop.\n"
            "n=%d\n"
            "function _update()\n"
            " local a=0\n"
            " for i=1,n do %s end\n"
            "end\n"
            "function _draw() cls(0) end\n" % (calls, calls, expr))


def title_for(slug):
    """The cart title, and the name `run` addresses it by."""
    return "P8Bench %s" % slug


def build_carts(out_dir, ops=OPS, calls=CALLS, spec=None):
    """Port every row to `out_dir`; returns [(slug, title, cart_dir)].

    Uses the VENDORED porter, so the carts are the ones this tree emits."""
    sys.path.insert(0, spec or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
    import p8_lua_port                                          # noqa: E402
    out = []
    for slug, expr, _why in ops:
        src = os.path.join(out_dir, "%s.p8" % slug)
        with open(src, "w", encoding="utf-8") as f:
            f.write(p8_source(expr, calls))
        cart = os.path.join(out_dir, "%s.moy" % slug)
        p8_lua_port.port(src, cart, title=title_for(slug), force=True)
        out.append((slug, title_for(slug), cart))
    return out


def per_call_ns(samples, control_ms, calls=CALLS):
    """Median update ms -> nanoseconds per call, net of the control."""
    return (statistics.median(samples) - control_ms) * 1e6 / calls


def report(rows, ops=OPS):
    why = {s: w for s, _e, w in ops}
    print("%-9s %10s %10s  %s" % ("op", "update ms", "ns/call", "what it prices"))
    for slug, ms, ns in rows:
        print("%-9s %10.3f %10s  %s"
              % (slug, ms, "-" if slug == CONTROL else "%.0f" % ns, why.get(slug, "")))


def diff(before, after):
    b = {r["op"]: r for r in before["ops"]}
    print("%-9s %10s %10s %9s" % ("op", "before", "after", "delta"))
    for r in after["ops"]:
        if r["op"] == CONTROL or r["op"] not in b:
            continue
        was, now = b[r["op"]]["ns"], r["ns"]
        print("%-9s %10.0f %10.0f %+9.0f" % (r["op"], was, now, now - was))
    return 0


def _reset(board_dir, port, attach_only):
    """Make the launcher rescan, which is the ONLY way a pushed cart becomes
    runnable -- and the failure is silent: `run <new title>` leaves whatever
    was already loaded running, so every arm reads the same number and looks
    like a result. An attach-only board cannot take an RTS pulse (it would
    strand this handle), so it goes through esptool's USB-JTAG path."""
    if attach_only:
        subprocess.run([sys.executable, "-m", "esptool", "--port", port,
                        "--after", "hard_reset", "read_mac"],
                       check=True, capture_output=True, timeout=120)
        return True
    return False


def main(argv=None):
    from p4_autotest import P4Board                             # noqa: E402
    import time
    import tempfile

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    boards = {"tdeck": "firmware/lilygo_t_deck_plus_mainline",
              "guition_s3": "firmware/guition_jc3248w535",
              "p4": "firmware/esp32_p4_wifi6_touch_lcd_7b",
              "guition_p4": "firmware/guition_jc8012p4a1c"}
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # --board has no default, for the reason push_cart and p4_perf give: the
    # boards' line state at open differs and a wrong guess resets an S3.
    ap.add_argument("--board", choices=sorted(boards), help="which board to drive")
    # PASS THIS. Four boards declare usb id 303a:1001, so resolving by id
    # probes each candidate in turn and the headless Zero costs a full timeout
    # every time -- "auto" is not wrong here, it is slow enough to look hung.
    ap.add_argument("--port", help="the board's port; PASS IT (four boards "
                                   "share one usb id, so auto-resolve probes "
                                   "each and can look hung)")
    ap.add_argument("--push", action="store_true",
                    help="build the carts, push them, and reset so they are seen")
    ap.add_argument("--secs", type=float, default=12.0, help="sample window per op")
    ap.add_argument("--json", help="write the run here")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    a = ap.parse_args(argv)
    if a.diff:
        with open(a.diff[0]) as f0, open(a.diff[1]) as f1:
            return diff(json.load(f0), json.load(f1))
    if not a.board:
        ap.error("--board is required (or --diff two json files)")

    board_dir = os.path.join(root, boards[a.board])
    work = tempfile.mkdtemp(prefix="p8bench-")
    carts = build_carts(work)
    if a.push:
        import push_cart                                        # noqa: E402
        probe = P4Board(a.port or "auto", board_dir=board_dir)
        port, attach_only = probe.ser.port, probe.attach_only
        probe.close()
        for i, (slug, _title, cart) in enumerate(carts, 1):
            print("push %2d/%d  %s" % (i, len(carts), slug), flush=True)
            rc = push_cart.main([cart, "--board", a.board, "--port", port,
                                 "--force"])
            if rc:
                raise SystemExit("push failed for %s" % cart)
        print("reset, so the launcher rescans", flush=True)
        _reset(board_dir, port, attach_only)
        time.sleep(30 if attach_only else 5)
    b = P4Board(a.port or "auto", board_dir=board_dir)
    try:
        b.cmd("diag 0", timeout=10)
        rows, control_ms = [], None
        for i, (slug, title, _cart) in enumerate(carts, 1):
            print("run  %2d/%d  %-8s" % (i, len(carts), slug), end="", flush=True)
            b.cmd("run %s" % title, wait_for="REMOTE run", timeout=25)
            time.sleep(3.0)
            got = (b.state() or {}).get("cart")
            if got != title:
                raise SystemExit(
                    "the board is running %r, not %r -- the launcher's roster "
                    "is built at boot, so push with --push (which resets) "
                    "before measuring." % (got, title))
            samples = []
            t0 = time.time()
            while time.time() - t0 < a.secs:
                v = b.pyval("str(ws.player._lua.frame_split()[0])", timeout=20)
                try:
                    samples.append(float(v))
                except (TypeError, ValueError):
                    pass
            if not samples:
                raise SystemExit("%s answered no tick_split -- is this a Lua "
                                 "cart on a moycore build?" % title)
            ms = statistics.median(samples)
            if slug == CONTROL:
                control_ms = ms
            ns = per_call_ns(samples, control_ms or 0.0)
            rows.append((slug, ms, ns))
            print("  %8.3f ms  %s" % (ms, "-" if slug == CONTROL else "%.0f ns" % ns),
                  flush=True)
            b.cmd("tap 300 8", timeout=8)
            time.sleep(1.0)
    finally:
        b.close()
    report(rows)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"board": a.board, "calls": CALLS,
                       "ops": [{"op": s, "ms": m, "ns": n} for s, m, n in rows]},
                      f, indent=1)
        print("\nwrote %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
