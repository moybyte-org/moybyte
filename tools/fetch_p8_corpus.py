#!/usr/bin/env python3
"""Fetch the p8 conformance corpus -- twelve well-known BBS carts.

    python tools/fetch_p8_corpus.py [--dir DIR]

THE CARTS ARE NOT REDISTRIBUTED. They are their authors' work, several under
licences that forbid it, so they are never committed and never shipped -- this
downloads them to a cache outside the tree, where `tests/test_p8_corpus.py`
looks for them. Default: ~/.cache/moybyte/p8 (or $MOYBYTE_P8_CORPUS).

The list is chosen to STRESS DIFFERENT THINGS rather than to be a top ten by
rating: a platformer, a raycaster, a puzzler, a shmup, a world-gen sim, a
minified bytecode VM, and two carts whose graphics live in packed strings. A
corpus of twelve similar carts would have found perhaps three of the fifteen
dialect bugs the first pass over these found.
"""
import argparse
import os
import subprocess
import sys

# BBS cart ids ("lid"), which are also the filenames. The BBS serves each at
# /bbs/cposts/<first two chars>/<lid>.
CORPUS = [
    "bunnysurvivor-9",          # 60fps _update60 cart, btnp-driven upgrade menu
    "celeste_classic_2-5",      # the famous one; fillp, peek, stat
    "crimson_night-5",          # all five per-sfx audio filters, P8SCII glyphs
    "dank_tomb-0",              # reads its level data as memory (peek 0x2000)
    "dungeons_and_diagrams-5",  # menus, dget/dset save data, fillp
    "lowmemsky-1",              # procedural, fillp-heavy, memcpy
    "mossmoss-17",              # `if cond do` throughout; coroutines
    "nimudazus-27",             # Tempest 2000: minified to a bytecode VM
    "petal_quest-12",           # coroutine-driven scenes; button-glyph legend
    "picooffroad-5",            # 3D road; car mesh packed in a [[ ]] blob
    "poom_0-9",                 # Doom demake: renders by poking screen memory
    "terra_1cart-44",           # Terraria demake: world gen, sset-packed sheet
]

BASE = "https://www.lexaloffle.com/bbs/cposts/%s/%s.p8.png"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.environ.get("MOYBYTE_P8_CORPUS") or
                    os.path.join(os.path.expanduser("~"), ".cache", "moybyte", "p8"))
    args = ap.parse_args(argv[1:])
    os.makedirs(args.dir, exist_ok=True)

    missing = 0
    for lid in CORPUS:
        dest = os.path.join(args.dir, lid + ".p8.png")
        if os.path.exists(dest) and os.path.getsize(dest) > 4000:
            print("  have %s" % lid)
            continue
        url = BASE % (lid[:2], lid)
        r = subprocess.run(["curl", "-sS", "--max-time", "60", url, "-o", dest])
        size = os.path.getsize(dest) if os.path.exists(dest) else 0
        if r.returncode != 0 or size < 4000:
            if os.path.exists(dest):
                os.remove(dest)
            print("  MISSING %s (the BBS may have moved it)" % lid)
            missing += 1
        else:
            print("  got  %s (%d bytes)" % (lid, size))

    print("\ncorpus at %s" % args.dir)
    print("run it:  MOYBYTE_P8_CORPUS=%s .venv/bin/python -m pytest -q "
          "tests/test_p8_corpus.py" % args.dir)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
