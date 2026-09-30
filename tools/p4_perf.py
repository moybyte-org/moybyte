#!/usr/bin/env python3
"""Per-cart fps on ANY board, over its own serial dev commands.

    python3 tools/p4_perf.py --board p4                       # the roster
    python3 tools/p4_perf.py --board tdeck "Brick Siege"      # named carts
    python3 tools/p4_perf.py --board guition_s3 --secs 12 --diag

--board IS REQUIRED and deliberately has no default (the same argument
tools/push_cart.py makes): the boards differ in the serial line state at open,
and opening an S3 with both lines low CHIP-RESETS it -- after which the USB
device re-enumerates under this handle and every read returns nothing forever,
which reads exactly like a dead board. Until 2026-08-28 this tool took only
--port and built the driver on the P4's defaults, so it could not be pointed at
either S3 board without wrecking the session. The port now resolves from the
board's own [serial] usb id unless --port says otherwise.

It also could not READ two of the three: the T-Deck's samples went through the
diag ring, whose `Moybyte <uptime> ` stamp defeated the `startswith("PERF ")`
filter here, and the Guition printed a different field set from the P4's. There
is ONE format now (runtime/perf_line.py), written by one body on every board,
and this parses it with the module that writes it.

Under Settings -> PERF DIAG the board prints a PERF line every ~2s carrying
drawn-fps and the frame budget split (draw / flush / logic / render / chrome).
This runs each cart, waits for the numbers to settle, and reports the median of
the samples it saw -- median rather than mean because a GC spike lands in
exactly one sample and should not move the answer.

THE DIAG IS ON FOR EVERY MEASUREMENT, and put back after. With PERF DIAG off
(kid mode, the default) a board writes no periodic line at all (owner call
2026-09-30: each is garbage its collector stops the frame for), so the line is
there only while this has the diag on. That makes every reading a PERF DIAG
reading: `perf_capture` and the FPS chip ride along, and they are frame eaters
of their own (#68) -- keep that in mind against a number taken before
2026-09-30 with the diag off. --diag prints the per-phase ms as well.

A row marked LINKED is NOT that cart's fps. A second console left in the same
two-player cart forms a real ESP-NOW match, and a linked game draws on the
shared 30Hz tick by design (#65) -- so Brick Siege reads 30 against the 62 it
runs solo. Move the other console off the cart and re-measure.

THE BOARD SAYS THAT ITSELF NOW (2026-08-27): every PERF line carries `net=`,
the lockstep tick rate, or `-` when no session is gating frames -- so this reads
the samples it already has instead of asking the radio afterwards, which is both
per-sample and free. A board whose firmware predates the field prints no `net=`
at all and its rows stay unmarked, which is the honest reading of "this board
did not say".

Numbers live in issue #66, not here. This tool produces them; it does not
remember them.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import add_board_args, board_from_args   # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runtime.perf_line import parse_perf, slug     # noqa: E402

# The carts worth watching: the historically slowest (Brick Siege), the two Lua
# twins against their Python originals (the #67 comparison), and a couple of
# cheap ones as a control -- if a control moves, the change was not in the
# verbs. The 3D-verb row used to be Ray Lua's; those scenes are phases of the
# two Bench carts now, and `p4_cart_bench.py` is what reads them.
DEFAULT_ROSTER = [
    "Brick Siege", "Brick Siege Lua",
    "Sakura", "Sakura Lua",
    "Hop Quest", "Sky Run", "Letter Blitz", "Star Catcher",
]


def measure(board, title, secs, log):
    """Run `title` from the launcher and read its PERF samples for `secs`;
    the caller has PERF DIAG on, which is what makes the board write them.
    None if the board has no such cart; RuntimeError if it started and then
    failed, or if no sample names it. `tools/board.py perf` is the other
    caller."""
    board.leave_cart()
    line = board.cmd("run %s" % title, wait_for="REMOTE run", timeout=20.0)
    if line is None:
        raise RuntimeError("board never acknowledged `run %s`" % title)
    if "no cart match" in line:
        return None
    # The title the console matched, which is what its PERF lines name.
    running = line.split("REMOTE run ", 1)[-1].strip() or title
    # Discard the first samples: a cart's opening frames build sprite caches and
    # touch cold flash, which is real but is not what it runs at.
    board.drain(4.0)
    n0 = len(board.lines)
    board.drain(secs)
    st = board.state()
    if st.get("cart_error") or st.get("notice"):
        raise RuntimeError("%s did not run: %s"
                           % (running, st.get("cart_error") or st.get("notice")))
    seen = [p for p in (parse_perf(l) for l in board.lines[n0:]) if p]
    # Only this cart's samples: a line printed from the launcher before the
    # cart came up is the launcher's frame, not the cart's.
    samples = [p for p in seen if p.get("cart") == slug(running)]
    if seen and not samples:
        raise RuntimeError("PERF names cart=%s, not %s"
                           % ("/".join(sorted({str(p.get("cart")) for p in seen})),
                              slug(running)))
    if not samples:
        return None
    # fps arrives as (drawn, looped); drawn is the one this reports.
    fps = [s["fps"][0] for s in samples if s.get("fps")]
    if not fps:
        return None
    # What this run WAS, from the samples themselves: `net=` is a number only
    # while a lockstep session is gating frames (#65), and `-` when nothing is.
    # A peer left on the desk in the same two-player cart makes a correct 30
    # that reads exactly like a regression -- 2026-08-27, a T-Deck parked in
    # Brick Siege cost a night of paired carve-vs-dev captures. parse_perf
    # float()s what it can, so a rate arrives as a float and the absent marker
    # stays the string "-"; a missing key means the firmware predates the field.
    ticks = [s["net"] for s in samples if isinstance(s.get("net"), float)]
    return {
        "title": running,
        "n": len(samples),
        "linked": statistics.median(ticks) if ticks else None,
        "fps": statistics.median(fps),
        "min": min(fps),
        "cart": samples[-1].get("cart") or title,
        "phases": {k: _median(s.get(k) for s in samples)
                   for k in ("draw", "flush", "logic", "render", "chrome")},
    }


def _median(values):
    """The median of the numeric values, or None: a phase a board did not
    measure prints `-` (runtime/perf_line.py), and `-` is not a zero."""
    nums = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(nums) if nums else None


def phase_text(phases):
    return "/".join("-" if phases[k] is None else "%.0f" % phases[k]
                    for k in ("draw", "flush", "logic", "render", "chrome"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("carts", nargs="*", help="cart titles (default: the roster)")
    add_board_args(ap)
    ap.add_argument("--secs", type=float, default=8.0, help="sample window per cart")
    ap.add_argument("--diag", action="store_true",
                    help="print the per-phase ms too (every measurement runs "
                         "under PERF DIAG: its lines are the reading)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    log = print if a.verbose else (lambda *x: None)
    roster = a.carts or DEFAULT_ROSTER
    board = board_from_args(a, log=(lambda s: log("  | " + s[:120])))
    try:
        board.drain(0.5)
        # A board that declares attach_only is ATTACHED to, never reset: the
        # pulse re-enumerates its USB under this handle and strands it. Where a
        # reset is safe it is still the way to recover a wedged console.
        if board.pyval("1", timeout=8.0) != 1 and not board.attach_only:
            board.reset()
        diag_was = bool(board.state().get("diag"))
        board.cmd("diag 1", wait_for="REMOTE diag")
        try:
            board.drain(0.5)
            print("%-18s %6s %6s %5s   %s"
                  % ("cart", "fps", "worst", "n",
                     "draw/flush/logic/render/chrome ms" if a.diag else ""))
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
                print("%-18s %6.1f %6.1f %5d   %s%s"
                      % (title, r["fps"], r["min"], r["n"],
                         phase_text(r["phases"]) if a.diag else "",
                         ("  LINKED (net=%.0f ticks/s)" % r["linked"])
                         if r["linked"] is not None else ""))
                linked = linked or r["linked"] is not None
                rows.append((title, r))
            board.leave_cart()
        finally:
            if not diag_was:
                board.cmd("diag 0", wait_for="REMOTE diag")
        if linked:
            print("\nLINKED: the board's own PERF line reported a lockstep tick "
                  "rate (net=), so another\nconsole on this desk is in the same "
                  "two-player cart and that run was a real\nESP-NOW match "
                  "drawing on the shared tick (#65). That number is the match's,"
                  "\nnot the cart's -- move the peer off the cart and re-measure.")
        return 0 if rows else 1
    finally:
        board.close()


if __name__ == "__main__":
    sys.exit(main())
