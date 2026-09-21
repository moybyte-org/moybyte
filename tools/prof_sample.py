#!/usr/bin/env python3
"""Whole-image PC profile of a running board, symbolized against its own ELF.

    python3 tools/prof_sample.py --board tdeck --cart "Brick Siege Lua"
    python3 tools/prof_sample.py --board tdeck --frame 1     # attribute to CALLER
    python3 tools/prof_sample.py --board guition_s3 --secs 12 --hz 2000

This is the meter the others could not be. `VERBS` times C verbs, `LUAPROF`
samples the Lua interpreter by function, `PERFCNT` reads the CPU's counters --
each sees ONE tier, so a cost living between them is unattributable, and
docs/perf_native_gap_v1.md §9 has been carrying exactly such a hole. This
samples wherever the program counter actually is, so one capture spans libmoy's
raster kernels, the MicroPython VM's internals, the Lua verbs, IDF driver code,
the audio task and the idle hook at once.

--board IS REQUIRED and has no default, for the reason tools/p4_perf.py gives:
the boards differ in the serial line state at open and guessing wrecks the
session.

WHAT THE NUMBERS MEAN, and the two ways to misread them:

  * `--frame 0` (the default) attributes a sample to the function the CPU was
    IN. `--frame 1` attributes it to that function's CALLER, which is how you
    tell "all the time is in memcpy" from "who keeps calling memcpy". Run both;
    they answer different questions. The offset is resolved against the board's
    own `moy_prof.SELF`, so the same flag means the same thing on either arch.
  * CALLER ATTRIBUTION IS XTENSA-ONLY TODAY. The P4 samples `mepc`, which is
    exact but is one frame deep, so `--frame 1` has nothing to report there.
  * `??` rows are addresses with no symbol -- ROM routines and the
    @micropython.native exec arena, which is generated at runtime and is in no
    ELF. A large `??` share means the cart is running native-compiled code, not
    that the profile is broken.

COST, measured on the T-Deck 2026-09-20 against Brick Siege: at 1000Hz the
median fps does not move (59 armed, 59 disarmed); at 2000Hz it costs ~2fps.
1000 is the default for that reason.

The sample ring is internal SRAM and is freed between windows, because with a
cart up the T-Deck has ~50KB free and the Guition far less. It SHRINKS TO FIT
on its own -- if it cannot arm it halves and retries, and says so -- so
`--samples` is a ceiling rather than something to tune by hand.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from p4_autotest import P4Board, board_dirs, add_board_args   # noqa: E402

# Which toolchain's addr2line resolves which board's image. Keyed by the target
# the board's build produces, not by the board -- the two P4s share one.
#
# Rooted at IDF_TOOLS_PATH (or its documented default) rather than a literal
# home directory: tests/test_no_machine_paths.py rejects the latter, and it is
# right to -- a hardcoded /home/<someone> works on exactly one machine.
_TOOLS = os.environ.get("IDF_TOOLS_PATH") or os.path.expanduser("~/.espressif")
_ADDR2LINE = {
    "xtensa": os.path.join(_TOOLS, "tools", "xtensa-esp-elf", "*", "xtensa-esp-elf",
                           "bin", "xtensa-esp32s3-elf-addr2line"),
    "riscv": os.path.join(_TOOLS, "tools", "riscv32-esp-elf", "*", "riscv32-esp-elf",
                          "bin", "riscv32-esp-elf-addr2line"),
}


def board_elf(board_dir):
    """The image the board is (presumably) running, from its own build tree."""
    hits = glob.glob(os.path.join(
        board_dir, ".build", "micropython", "ports", "esp32",
        "build-MOYBYTE_*", "micropython.elf"))
    if not hits:
        sys.exit("no built ELF under %s -- build the board first, and note the\n"
                 "profile is only true of the image actually flashed." % board_dir)
    return hits[0]


def addr2line_for(elf):
    """Pick a toolchain by asking the ELF what machine it is."""
    out = subprocess.run(["file", "-b", elf], capture_output=True, text=True).stdout
    arch = "riscv" if "RISC-V" in out else "xtensa"
    hits = sorted(glob.glob(os.path.expanduser(_ADDR2LINE[arch])))
    if not hits:
        sys.exit("no %s addr2line found; is the IDF toolchain installed?" % arch)
    return hits[-1]


def symbolize(elf, a2l, addrs):
    if not addrs:
        return []
    out = subprocess.run([a2l, "-f", "-e", elf] + [hex(a) for a in addrs],
                         capture_output=True, text=True).stdout.split("\n")
    return out[0::2][:len(addrs)]


def capture(board, hz, samples, frame, secs, log):
    """Run windows back to back and accumulate. One window is ring/hz long;
    the ring is freed between them so a cart keeps its internal SRAM."""
    # The ring is internal SRAM, and how much of that exists depends on what is
    # running -- a cart leaves the T-Deck ~50KB and the Guition far less. So
    # the ring SHRINKS TO FIT rather than making the caller guess: halve and
    # retry until it arms. The window shortens with it, and the window count
    # rises to keep --secs honest.
    while True:
        board.pyval("__import__('moy_prof').start(%d, %d)" % (hz, samples), timeout=30)
        if not board.last_error:
            board.pyval("__import__('moy_prof').stop()", timeout=25)
            board.pyval("__import__('moy_prof').free()", timeout=25)
            break
        if "MemoryError" not in str(board.last_error) or samples <= 64:
            sys.exit("could not arm: %s" % board.last_error)
        samples //= 2
        log("  ring did not fit; retrying at %d samples" % samples)

    window = max(0.2, samples / float(hz))
    windows = max(1, int(round(secs / window)))
    tally, taken = {}, 0
    for i in range(windows):
        board.pyval("__import__('moy_prof').start(%d, %d)" % (hz, samples), timeout=30)
        if board.last_error:
            sys.exit("could not arm: %s" % board.last_error)
        time.sleep(window * 1.05)
        board.pyval("__import__('moy_prof').stop()", timeout=25)
        st = board.pyval("__import__('moy_prof').stats()", timeout=25)
        hist = board.pyval(
            "[(p,c) for p,c in __import__('moy_prof').hist(%d, 120)]" % frame,
            timeout=120)
        board.pyval("__import__('moy_prof').free()", timeout=25)
        if hist:
            for pc, count in hist:
                tally[pc] = tally.get(pc, 0) + count
            if st:
                taken += min(st[1], st[2])
        log("  window %d/%d: %d samples, %d distinct"
            % (i + 1, windows, st[1] if st else 0, len(tally)))
    return tally, taken


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_board_args(ap)
    ap.add_argument("--cart", help="run this cart first (default: profile whatever is up)")
    ap.add_argument("--hz", type=int, default=1000, help="sample rate (default 1000)")
    ap.add_argument("--samples", type=int, default=1200,
                    help="ring size; internal SRAM, 24B each. Shrinks to fit if it does not arm (default 1200)")
    ap.add_argument("--secs", type=float, default=8.0, help="total capture (default 8)")
    ap.add_argument("--frame", type=int, default=0,
                    help="0 = the interrupted function, 1 = its caller "
                         "(offset from the board's moy_prof.SELF; default 0)")
    ap.add_argument("--top", type=int, default=25, help="rows to print (default 25)")
    a = ap.parse_args()

    bdirs = board_dirs()
    if a.board not in bdirs:
        sys.exit("unknown board %r; have %s" % (a.board, ", ".join(sorted(bdirs))))
    bdir = bdirs[a.board]
    elf = board_elf(bdir)
    a2l = addr2line_for(elf)

    def log(msg):
        print(msg, flush=True)

    board = P4Board(port=a.port, board_dir=bdir, log=lambda s: None)
    if a.cart:
        board.leave_cart()
        if board.cmd("run %s" % a.cart, wait_for="REMOTE run", timeout=25.0) is None:
            sys.exit("board never acknowledged `run %s`" % a.cart)
        if "no cart match" in board.lines[-1]:
            sys.exit("no cart matching %r" % a.cart)
        log("running %s; letting it settle past its cold opening frames" % a.cart)
        time.sleep(4)

    self_idx = board.pyval("__import__('moy_prof').SELF", timeout=30)
    if self_idx is None:
        sys.exit("board has no moy_prof.SELF -- is moy_prof in the flashed image?")
    frames = board.pyval("__import__('moy_prof').FRAMES", timeout=30) or 1
    idx = self_idx + a.frame
    if idx >= frames:
        sys.exit("--frame %d asks for frame %d of %d: this board records no caller "
                 "frame (the P4 samples mepc, which is one deep)."
                 % (a.frame, idx, frames))
    try:
        tally, _taken = capture(board, a.hz, a.samples, idx, a.secs, log)
    finally:
        # Leave the board where we found it -- on the desk, and with the ring
        # released. Every other driver in this tree makes that promise
        # (p4_autotest: "left rebooted onto the desk afterwards, ready for a
        # human") and it is not cosmetic: a cart left running is state the next
        # on-glass suite does not expect, which is exactly how this tool made
        # tests/test_tdeck_on_glass.py fail two tests that pass on their own.
        board.pyval("__import__('moy_prof').free()", timeout=25)
        if a.cart:
            board.leave_cart(settle=0.5)
    if not tally:
        sys.exit("no samples -- is moy_prof in this image?")

    addrs = sorted(tally, key=lambda p: -tally[p])
    names = symbolize(elf, a2l, addrs)
    agg = {}
    for pc, nm in zip(addrs, names):
        agg[nm] = agg.get(nm, 0) + tally[pc]
    total = sum(agg.values())

    what = "the interrupted function" if a.frame == 0 else "caller frame +%d" % a.frame
    print("\n%s @ %dHz -- %d samples attributed to %s\n"
          % (a.cart or "(whatever was running)", a.hz, total, what))
    print("  share  samples  symbol")
    for nm, c in sorted(agg.items(), key=lambda kv: -kv[1])[:a.top]:
        print("  %5.1f%%  %7d  %s" % (100.0 * c / total, c, nm))
    shown = sum(c for _, c in sorted(agg.items(), key=lambda kv: -kv[1])[:a.top])
    if shown < total:
        print("  %5.1f%%  %7d  (%d further symbols)"
              % (100.0 * (total - shown) / total, total - shown, len(agg) - a.top))


if __name__ == "__main__":
    main()
