#!/usr/bin/env python3
"""Run a moy conformance cart on the P4 and dump the frame it actually renders.

    python tools/p4_conformance.py <cart-dir> <out-file> [--port /dev/ttyACM0]

Speaks moy-spec's player protocol (conformance/run.py --player), so:

    python3 conformance/run.py --player \\
      "python3 /path/to/moybyte/tools/p4_conformance.py {cart} {out}"

checks the BOARD against the same golden frames the WebAssembly player is
checked against.

WHY THIS IS THE INTERESTING ONE. The web player and moycore both rasterize in
software the suite can reach easily; the board is where the C moy_gfx kernel,
the RGB565 framebuffer and SPEC.md 1.1's memory floor actually live, and none
of it had ever been checked against the spec. The two bugs the suite found in
the web player -- the console API's 8-argument cap and print losing a frame on
any byte past ASCII -- were both in shared code, so both were on the boards
too. They were simply never run.

HOW IT GETS PIXELS. ws.canvas is the DeviceCanvas the cart draws into, and its
_buf is the live RGB565 framebuffer. DeviceCanvas hands moy_gfx colours straight
out of PAL565_WIRE, so a buffer halfword IS a wire palette entry and maps back
to an index by reverse lookup -- no conversion, no tolerance, and a pixel that
does not map is a real failure rather than something to round.

The index conversion happens ON THE DEVICE, which halves what crosses the wire
(76800 index bytes instead of 153600 RGB565) and lets the base64 hop cost 4/3
rather than the 2x hex would.

The FPS chip is turned off first. It draws into the cart's own canvas, so a
captured frame would otherwise carry host chrome -- the same reason the spec
bundle boots with hud=False.
"""

from __future__ import annotations

import argparse
import binascii
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import P4Board            # noqa: E402

W, H = 320, 240
FRAME_BYTES = W * H
CART_ROOT = "/moy/carts"
CHUNK = 1024                    # index bytes per base64 read


# --- getting the cart onto the board ----------------------------------------

def push_cart(board, cart_dir, name, log=print):
    """Write a cart folder into /moy/carts/<name>.moy and make the launcher see
    it, without rebooting -- a reset costs ~40s and the suite has nine scenes."""
    dst = "%s/%s.moy" % (CART_ROOT, name)
    board.pyexec(
        "import os\n"
        "try:\n"
        "    os.mkdir(%r)\n"
        "except OSError:\n"
        "    pass\n" % dst)
    for fn in sorted(os.listdir(cart_dir)):
        path = os.path.join(cart_dir, fn)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            blob = f.read()
        # base64 rather than a repr: a cart's main.lua may hold any byte (the
        # text_bytes scene prints 0xFF on purpose), and a repr of that is not
        # something to round-trip through a serial REPL.
        b64 = binascii.b2a_base64(blob).decode().strip()
        board.pyexec("ws._blob = ''")
        for i in range(0, len(b64), board.CHUNK - 40):
            part = b64[i:i + board.CHUNK - 40]
            if not board.pyexec("ws._blob += %r" % part):
                raise RuntimeError("upload of %s failed" % fn)
        ok = board.pyexec(
            "import binascii\n"
            "_f = open(%r, 'wb')\n"
            "_f.write(binascii.a2b_base64(ws._blob))\n"
            "_f.close()\n" % ("%s/%s" % (dst, fn)))
        if not ok:
            raise RuntimeError("write of %s failed" % fn)
        log("    %-16s %d bytes" % (fn, len(blob)))
    # Rebuild the cart list in place. _all_carts is what the launcher's items
    # are derived from, so re-scanning and re-deriving is the no-reboot path.
    ok = board.pyexec(
        "import moy_carts\n"
        "ws._all_carts = moy_carts.scan(%r)\n"
        "ws.launcher.items = ws._launcher_items(ws._all_carts)\n" % CART_ROOT)
    if not ok:
        raise RuntimeError("could not refresh the cart list")


def cart_title(cart_dir):
    with open(os.path.join(cart_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)["title"]


# --- capturing ---------------------------------------------------------------

CAPTURE_SETUP = """
import device_canvas as _dc
_pal = _dc.PAL565_WIRE
_rev = {}
for _i in range(len(_pal)):
    if _pal[_i] not in _rev:
        _rev[_pal[_i]] = _i

def _order(b):
    # Which way round the framebuffer's halfwords are, decided by asking rather
    # than assuming: sample a stride across the buffer and see which reading
    # lands on actual palette entries. array('H', b) does NOT work here --
    # bytes iterate as ints, so it would build one halfword per BYTE -- and
    # memoryview.cast is not on every MicroPython build.
    lo = hi = 0
    for i in range(0, len(b) - 1, 512):
        if (b[i] | (b[i + 1] << 8)) in _rev:
            lo += 1
        if ((b[i] << 8) | b[i + 1]) in _rev:
            hi += 1
    return 'little' if lo >= hi else 'big'

def _grab():
    # RGB565 -> palette indices, on the device: half as many bytes to send, and
    # the mapping is exact. DeviceCanvas only ever writes colours it looked up
    # in PAL565_WIRE, so a halfword that is not one means something wrote this
    # framebuffer that should not have -- reported, never rounded away.
    b = ws.canvas._buf
    n = len(b) // 2
    out = bytearray(n)
    bad = 0
    if _order(b) == 'little':
        for i in range(n):
            v = _rev.get(b[2 * i] | (b[2 * i + 1] << 8))
            if v is None:
                bad += 1
                v = 0
            out[i] = v
    else:
        for i in range(n):
            v = _rev.get((b[2 * i] << 8) | b[2 * i + 1])
            if v is None:
                bad += 1
                v = 0
            out[i] = v
    ws._grabbed = out
    return bad
"""


def capture(board, log=print):
    """The board's current frame as FRAME_BYTES of palette indices."""
    if not board.pyexec(CAPTURE_SETUP):
        raise RuntimeError("could not install the capture helper")
    bad = board.pyval("ws._g['_grab']()", timeout=60.0)
    if bad is None:
        raise RuntimeError("the capture helper did not run")
    if bad:
        log("  WARNING: %d pixels were not palette colours (reported as 0)" % bad)
    n = board.pyval("len(ws._grabbed)")
    if n != FRAME_BYTES:
        raise RuntimeError("device frame is %s bytes, expected %d" % (n, FRAME_BYTES))
    out = bytearray()
    while len(out) < FRAME_BYTES:
        i = len(out)
        piece = board.pyval(
            "__import__('binascii').b2a_base64(ws._grabbed[%d:%d])" % (i, i + CHUNK),
            timeout=30.0)
        if not piece:
            raise RuntimeError("frame read stalled at byte %d" % i)
        out.extend(binascii.a2b_base64(piece))
    return bytes(out[:FRAME_BYTES])


# --- driving -----------------------------------------------------------------

def run_scene(board, cart_dir, log=print, frames=1.5):
    name = os.path.basename(cart_dir.rstrip("/"))
    if name.endswith(".moy"):
        name = name[:-4]
    title = cart_title(cart_dir)

    # Leave whatever is running, so a repeat call starts from the desk.
    board.pyexec("ws.exit()")
    board.drain(0.6)

    log("  pushing %s" % name)
    push_cart(board, cart_dir, name, log=log)

    # Host chrome must not appear in a golden frame.
    board.pyexec("ws.show_fps = False\nws.perf_hud = False")

    log("  running %r" % title)
    if board.cmd("run %s" % title, wait_for="REMOTE run", timeout=20.0) is None:
        raise RuntimeError("the board never acknowledged `run`")
    board.drain(frames)          # let the cart draw a settled frame
    return capture(board, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cart", help="the conformance cart folder")
    ap.add_argument("out", help="where to write the raw frame")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--reset", action="store_true",
                    help="hard-reset first (slow; only needed if the board is wedged)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    log = print if a.verbose else (lambda *x: None)
    board = P4Board(a.port)
    try:
        # A reset costs ~40s and the suite is nine scenes, so only do it when
        # the board is genuinely wedged. Drain first: opening the port leaves
        # whatever the desktop last printed in the buffer, and a probe that
        # reads that instead of its own reply looks like a dead board.
        board.drain(0.4)
        if a.reset or board.pyval("1", timeout=8.0) != 1:
            log("  board not answering; resetting")
            board.reset()
        frame = run_scene(board, a.cart, log=log)
    finally:
        board.close()
    with open(a.out, "wb") as f:
        f.write(frame)
    log("  wrote %d bytes -> %s" % (len(frame), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
