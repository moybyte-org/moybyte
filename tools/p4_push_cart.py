#!/usr/bin/env python3
"""Copy a cart folder onto the P4's internal store, over the serial console.

    python tools/p4_push_cart.py ports/celeste.moy
    python tools/p4_push_cart.py ports/celeste.moy --dest /moy/carts/celeste.moy
    python tools/p4_push_cart.py ports/celeste.moy --only main.lua --force

WHY THIS EXISTS. This board has no SD card: its cartridges live on the internal
VFS at /moy/carts, seeded from the build for system carts and put there by hand
for anything else -- which meant a hand-carried cart arrived by whatever route
that session improvised, with no record. One did: the P4 was carrying a celeste
whose `local P8_VH = 128` made its own `if view ~= nil and P8_VH < 128` guard
never fire, so it never declared view(128, 120) and played letterboxed at 1x.
That is the missing `--zoom` at port time, shipped to glass, and nobody could
say how it got there. A cart is data; putting data on the board should be a
command, not an improvisation.

Skips files whose hash already matches, so re-running is cheap and a partial
transfer resumes. ~20s per 40KB at 768-byte chunks -- serial, not a bulk pipe;
push one cart, not a library.

FOUR THINGS THIS GETS RIGHT, each of which cost an attempt:

  1. `P4Board.pyexec` stages ITS OWN snippet in `ws._up`, so every helper has to
     be defined BEFORE the payload goes there or the upload is silently wiped.
  2. `open(p, 'wb').write(d)` returns the byte count and leaves the file for the
     gc to finalise whenever. It reported 43658 bytes written and then read the
     file back EMPTY. Close it.
  3. Keep the expressions the device evaluates trivial. A list comprehension
     inside its eval env does not resolve names the way it does locally.
  4. Verify the hash of a `.new` and rename only then. A half-written main.lua
     is a cart that will not load, and the board is not where you want to
     discover that.

The T-Deck has no equivalent and cannot: its USB-CDC RX is dead under the
desktop (see CLAUDE.md's hard constraints). That board takes carts by SD card.
"""
import argparse
import base64
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import P4Board                                  # noqa: E402

HELPERS = """
import binascii, hashlib, os
def _wr(path):
    d = binascii.a2b_base64(''.join(ws._up[k] for k in sorted(ws._up)))
    f = open(path, 'wb')
    f.write(d)
    f.close()
    return len(d)
def _sha(path):
    try:
        return hashlib.sha256(open(path, 'rb').read()).digest().hex()[:12]
    except Exception:
        return None
def _mkdir(path):
    try:
        os.mkdir(path)
    except Exception:
        pass
    return 1
ws._g['_wr'] = _wr
ws._g['_sha'] = _sha
ws._g['_mkdir'] = _mkdir
"""


def push_file(b, src, dst, verbose=False):
    """One file, hash-verified through a .new. True if it was written."""
    raw = open(src, "rb").read()
    want = hashlib.sha256(raw).hexdigest()[:12]
    if b.pyval("ws._g['_sha'](%r)" % dst) == want:
        print("  = %-16s %d B (already current)" % (os.path.basename(src), len(raw)))
        return False
    b64 = base64.b64encode(raw).decode()
    tmp = dst + ".new"
    b.pyval("setattr(ws, '_up', {}) or 1")
    ch = b.CHUNK
    n = (len(b64) + ch - 1) // ch
    t0 = time.time()
    for k, i in enumerate(range(0, len(b64), ch)):
        r = b.cmd("py ws._up.__setitem__(%d, %r) or 1" % (k, b64[i:i + ch]),
                  wait_for="PY", timeout=30.0) or ""
        if "PY ERR" in r:
            raise RuntimeError("chunk %d/%d failed: %s" % (k + 1, n, r.strip()))
        if verbose and (k % 20 == 0 or k == n - 1):
            print("     chunk %d/%d" % (k + 1, n))
    b.pyval("ws._g['_wr'](%r)" % tmp)
    got = b.pyval("ws._g['_sha'](%r)" % tmp)
    if got != want:
        b.pyval("__import__('os').remove(%r) or 1" % tmp)
        raise RuntimeError("%s: hash %s != %s -- left the old file in place"
                           % (os.path.basename(src), got, want))
    b.pyval("__import__('os').remove(%r) or 1" % dst)     # no-op if absent
    b.pyval("__import__('os').rename(%r, %r) or 1" % (tmp, dst))
    print("  > %-16s %d B in %.0fs  sha %s"
          % (os.path.basename(src), len(raw), time.time() - t0, want))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cart", help="the cart folder (e.g. ports/celeste.moy)")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--dest", help="target path (default /moy/carts/<foldername>)")
    ap.add_argument("--only", action="append",
                    help="push just this file (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="push even when the hash already matches")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    cart = a.cart.rstrip("/")
    if not os.path.isdir(cart):
        sys.exit("not a cart folder: " + cart)
    names = sorted(f for f in os.listdir(cart)
                   if os.path.isfile(os.path.join(cart, f))
                   and not f.startswith("."))
    if a.only:
        missing = [f for f in a.only if f not in names]
        if missing:
            sys.exit("not in the cart: " + ", ".join(missing))
        names = [f for f in names if f in a.only]
    dest = a.dest or ("/moy/carts/" + os.path.basename(cart))
    print("%s -> %s  (%d file%s)"
          % (cart, dest, len(names), "" if len(names) == 1 else "s"))

    b = P4Board(a.port, log=(print if a.verbose else (lambda s: None)))
    try:
        b.reset()
        if not b.pyexec(HELPERS):
            sys.exit("could not install the upload helpers")
        b.pyval("ws._g['_mkdir'](%r)" % dest)
        wrote = 0
        for f in names:
            if a.force:
                b.pyval("__import__('os').remove(%r) or 1" % (dest + "/" + f))
            wrote += push_file(b, os.path.join(cart, f), dest + "/" + f,
                               verbose=a.verbose)
        print("%d file%s written, %d already current"
              % (wrote, "" if wrote == 1 else "s", len(names) - wrote))
        # The store is scanned at boot, so a pushed cart appears on the next one.
        print("reset the board (or `machine.reset()`) for the launcher to rescan")
    finally:
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
