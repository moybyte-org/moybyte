#!/usr/bin/env python3
"""Which carts run with no VM: the census (docs/kernel_cartpath_2026-10.md §2).

Every cart folder named (default: the seed carts, `ports/p8/*.moy` where they
were generated, and `ports/jet/*.moy` where they were built) through the
Player's own rule, `moy_play_vm_free` in native/moy_play, over the
catalogue entry the store's C reads. One line a cart: its runtime, `free` or
`vm`, and the clause that kept the VM. tests/test_vm_free_census.py pins the
verdicts against the real manifests.

    tools/vm_free_census.py [CART_DIR ...] [--no-lua] [--no-wasm]
    tools/vm_free_census.py --board BOARD [--secs S]

`--board` holds a console to the same census (the gate's census row and its
zero-upcall row, docs/kernel_cartpath_2026-10.md section 7): every cart on
its shelf the host census knows is run from the launcher for S seconds and
ended; the run's verdict in `state` must be the host's, and a VM-free run's
books from its launch to the exit's request (`state`'s `play` upcalls; the
request is the harness's own word, a crossing the run did not make) must hold
no CONSOLE crossing when the kernel drove its frame and no APP, SERVICE or
REFUSED crossing ever, and the run must then end. One line a cart, then the
disagreements; exit 1 when there is one.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime import moy_play  # noqa: E402


def default_carts(root=ROOT):
    out = sorted(glob.glob(os.path.join(root, "system_carts", "*.moy")))
    for pat in ("ports/p8/*.moy", "ports/jet/*.moy"):
        out += sorted(glob.glob(os.path.join(root, pat)))
    return out


def census(paths, lua=True, wasm=True):
    """[(folder, runtime, vm_free, why)]; a folder that is no cart (a compiled
    cart not built yet) is left out."""
    rows = []
    for p in paths:
        r = moy_play.census(p, lua=lua, wasm=wasm)
        if r is not None:
            rows.append((os.path.basename(os.path.normpath(p)),) + r)
    return rows


UPCALL_CLASSES = ("console", "app", "driver", "service", "refused")


def judge(want, run, books, ended, front):
    """One cart on glass against the host's census row `want` (runtime,
    vm_free, why): `run` is `state`'s run verdict while it played, `books`
    its upcalls by class from the launch to just before the exit's request
    (None when the Player did not have its frame), `ended` whether the run
    then ended, and `front` whether the kernel drove its frame. [] when it
    agrees, else what disagrees."""
    bad = []
    got = (None, None, None) if run is None else (run.get("runtime"),
                                                  run.get("vm_free"), run.get("why"))
    if tuple(got) != tuple(want):
        bad.append("verdict %r, the host's %r" % (got, tuple(want)))
    if want[1]:
        if books is None:
            bad.append("no run in the kernel's Player")
        else:
            if front and books[0]:
                bad.append("%d console crossings in a kernel-driven run" % books[0])
            for k in (1, 3, 4):
                if books[k]:
                    bad.append("%d %s crossings" % (books[k], UPCALL_CLASSES[k]))
        if not ended:
            bad.append("the run did not end")
    return bad


def on_glass(board, host, secs=3.0, say=print):
    """Run every cart on `board`'s shelf whose folder `host` (folder ->
    (runtime, vm_free, why)) knows; [(title, folder, want, run, books, front,
    frames, disagreements)]."""
    import time
    items = board.pyval("[(c.get('title'), c.get('path', '').rstrip('/').rsplit('/', 1)[-1]) "
                        "for c in ws.carts.all]", strict=True)
    rows = []
    for title, folder in items:
        want = host.get(folder)
        if want is None:
            continue
        # By folder: two shelves' carts can share a title, and an app is no
        # launcher item, so a title can land on another cart.
        line = board.cmd("run %s" % folder, wait_for="REMOTE run", timeout=30)
        if line is None or "no cart match" in line:
            continue
        board.drain(secs)
        st = board.state() or {}
        # A system app opens as its process, not a run: no verdict to hold.
        if st.get("cart") != title or not (st.get("front") or st.get("screen") == "desktop"):
            board.leave_cart()
            board.drain(1.0)
            continue
        run, front = st.get("run"), bool(st.get("front"))
        play = st.get("play") or {}
        books = play.get("upcalls")
        board.leave_cart()
        board.drain(1.0)
        ended = None
        if want[1]:
            info = board.pyval("__import__('moy_play').info()")
            ended = bool(info and info[6])
        bad = judge(want, run, books, ended or not want[1], front)
        rows.append((title, folder, want, run, books, front, play.get("frames"), bad))
        say("%-24s %-5s %-5s front=%-5s frames=%-5s upcalls=%s %s" % (
            title[:24], want[0], "free" if want[1] else "vm", front, play.get("frames"),
            books, "ok" if not bad else "; ".join(bad)))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("carts", nargs="*")
    ap.add_argument("--no-lua", action="store_true", help="an image with no Lua row")
    ap.add_argument("--no-wasm", action="store_true", help="an image with no wasm row")
    ap.add_argument("--board", help="hold this console's runs to the census")
    ap.add_argument("--secs", type=float, default=3.0, help="each run's play time")
    a = ap.parse_args(argv)
    if a.board:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import board as bd
        host = {name: (rt, free, why) for name, rt, free, why in census(default_carts())}
        b = bd.attach(a.board, bd.boards())
        try:
            rows = on_glass(b, host, a.secs)
        finally:
            b.close()
        bad = [r[0] for r in rows if r[7]]
        print("%d carts, %d VM-free, %d disagree%s" % (
            len(rows), sum(1 for r in rows if r[2][1]), len(bad),
            (": " + ", ".join(bad)) if bad else ""))
        return 1 if bad else 0
    rows = census(a.carts or default_carts(), lua=not a.no_lua, wasm=not a.no_wasm)
    w = max([len(r[0]) for r in rows] + [4])
    for name, rt, free, why in rows:
        print("%-*s  %-6s  %-4s  %s" % (w, name, rt, "free" if free else "vm", why))
    n = sum(1 for r in rows if r[2])
    print("%d carts, %d VM-free" % (len(rows), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
