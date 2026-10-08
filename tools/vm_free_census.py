#!/usr/bin/env python3
"""Which carts run with no VM: the census (docs/kernel_cartpath_2026-10.md §2).

Every cart folder named (default: the seed carts, `ports/p8/*.moy` where they
were generated, and `ports/jet/*.moy` where they were built) through the
Player's own rule, `moy_play_vm_free` in native/moy_play, over the
catalogue entry the store's C reads. One line a cart: its runtime, `free` or
`vm`, and the clause that kept the VM. tests/test_vm_free_census.py pins the
verdicts against the real manifests.

    tools/vm_free_census.py [CART_DIR ...] [--no-lua] [--no-wasm]
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("carts", nargs="*")
    ap.add_argument("--no-lua", action="store_true", help="an image with no Lua row")
    ap.add_argument("--no-wasm", action="store_true", help="an image with no wasm row")
    a = ap.parse_args(argv)
    rows = census(a.carts or default_carts(), lua=not a.no_lua, wasm=not a.no_wasm)
    w = max([len(r[0]) for r in rows] + [4])
    for name, rt, free, why in rows:
        print("%-*s  %-6s  %-4s  %s" % (w, name, rt, "free" if free else "vm", why))
    n = sum(1 for r in rows if r[2])
    print("%d carts, %d VM-free" % (len(rows), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
