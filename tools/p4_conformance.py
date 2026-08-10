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
CHUNK = 4096                    # wire bytes per base64 read: each one is a
                                # serial ROUND TRIP, and at 1024 the 76800-byte
                                # raw frame cost 75 of them


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
        #
        # Each chunk decodes STRAIGHT into the open file. The first shape of
        # this accumulated the whole file's base64 in one board-side string
        # via repeated += -- O(n^2) reallocation in whatever RAM the running
        # console left behind, which is how a 16KB map.moymap could fail to
        # upload on a board with megabytes free. Three wire realities shape
        # the replacement (all learned by tripping over them): the device's
        # `py` handler builds a FRESH env per command, so state must hang on
        # `ws`, never on a bare name or an import; cmd() RESENDS on a lost
        # reply, so each write is seek-addressed (a resend rewrites the same
        # bytes -- idempotent, per the p4_autotest warning); and chunks stay
        # a multiple of 4 so every piece decodes independently.
        b64 = binascii.b2a_base64(blob).decode().strip()
        step = (board.CHUNK - 72) & ~3          # ~55-char prefix + margin
        board.pyexec("ws._b2 = __import__('binascii')")
        if not board.pyexec("ws._pf = open(%r, 'wb')" % ("%s/%s" % (dst, fn))):
            raise RuntimeError("open of %s failed" % fn)
        for i in range(0, len(b64), step):
            part = ("(ws._pf.seek(%d), ws._pf.write(ws._b2.a2b_base64(%r)))"
                    % (i // 4 * 3, b64[i:i + step]))
            if not board.pyexec(part):
                # one retry after a collect: a transient alloc failure mid-
                # session is exactly what a gc pass clears
                board.pyexec("__import__('gc').collect()")
                if not board.pyexec(part):
                    board.pyexec("ws._pf.close()")
                    raise RuntimeError("upload of %s failed" % fn)
        if not board.pyexec("ws._pf.close()"):
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
    # RUN-LENGTH ENCODE before it goes near the wire. A conformance frame is
    # flat by construction -- fills, spans, a background -- and the wire is a
    # 115200-baud serial line, so the raw 76800 bytes cost ~9s of the ~15s a
    # scene took. Runs of (count, value), count 1..255; if that comes out BIGGER
    # than the raw frame (a dithered or noise-heavy scene would), the raw frame
    # is sent instead and the leading byte says which. Lossless either way --
    # this is a transport, and a golden comparison would catch it if it were not.
    enc = bytearray()
    i = 0
    while i < n:
        v = out[i]
        j = i + 1
        end = i + 255
        if end > n:
            end = n
        while j < end and out[j] == v:
            j += 1
        enc.append(j - i)
        enc.append(v)
        i = j
    if len(enc) < n:
        ws._grabbed = b'\x01' + bytes(enc)
    else:
        ws._grabbed = b'\x00' + bytes(out)
    return bad
"""


def capture(board, log=print):
    """The board's current frame as FRAME_BYTES of palette indices."""
    # Install the helper ONCE per board session. It uploads in 120-char chunks
    # and the device reads one command per frame, so this snippet alone is ~21
    # serial round trips -- the single biggest cost in a scene, and pure waste
    # from the second scene onward. The namespace (ws._g) persists, so asking is
    # one round trip and the answer is usually yes.
    if board.pyval("1 if 'ws' in dir() and '_grab' in getattr(ws, '_g', {}) "
                   "else 0", timeout=8.0) != 1:
        if not board.pyexec(CAPTURE_SETUP):
            raise RuntimeError("could not install the capture helper")
    bad = board.pyval("ws._g['_grab']()", timeout=60.0)
    if bad is None:
        raise RuntimeError("the capture helper did not run")
    if bad:
        log("  WARNING: %d pixels were not palette colours (reported as 0)" % bad)
    n = board.pyval("len(ws._grabbed)")
    if not isinstance(n, int) or n < 1:
        raise RuntimeError("device frame is %s bytes" % n)
    wire = bytearray()
    while len(wire) < n:
        i = len(wire)
        piece = board.pyval(
            "__import__('binascii').b2a_base64(ws._grabbed[%d:%d])" % (i, i + CHUNK),
            timeout=30.0)
        if not piece:
            raise RuntimeError("frame read stalled at byte %d of %d" % (i, n))
        wire.extend(binascii.a2b_base64(piece))
    wire = bytes(wire[:n])
    log("  %d bytes on the wire (%.1f%% of raw)"
        % (n, 100.0 * n / (FRAME_BYTES + 1)))
    if wire[0] == 1:
        out = bytearray()
        for k in range(1, len(wire) - 1, 2):
            out.extend(bytes((wire[k + 1],)) * wire[k])
    else:
        out = bytearray(wire[1:])
    if len(out) != FRAME_BYTES:
        raise RuntimeError("frame decoded to %d bytes, expected %d"
                           % (len(out), FRAME_BYTES))
    return bytes(out)


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


# --- the server -------------------------------------------------------------
#
# OPENING THE PORT REBOOTS THE BOARD. Not the RTS pulse -- P4Board already holds
# dtr/rts low across open for exactly that reason -- but the CH343 resets on
# enumeration anyway, and the board comes up reporting rst:0x1 (POWERON). So a
# player command that opens the port per scene pays a full boot per scene: ~40s
# of waiting for the desktop and 34 carts to load, against ~5s of actual work.
# Ten scenes spent twelve minutes doing ninety seconds of testing.
#
# The suite's player protocol is one process per scene and should stay that way
# -- it is what lets any implementation be a shell command. So the port is held
# by a SERVER instead, and the per-scene process becomes a thin client:
#
#     python3 tools/p4_conformance.py --serve &          # boots the board once
#     python3 conformance/run.py --player "python3 .../p4_conformance.py {cart} {out}"
#
# With no server running the client does the whole thing itself, exactly as
# before, so nothing has to know about this to work -- it is only slow.

SOCKET = "/tmp/moy-p4-conformance.sock"


def serve(port, sock_path, log):
    import socket
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    board = P4Board(port)
    try:
        board.drain(0.4)
        if board.pyval("1", timeout=8.0) != 1:
            log("board booting...")
            board.reset()
        log("board ready; listening on %s" % sock_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(4)
        while True:
            conn, _ = srv.accept()
            try:
                req = json.loads(conn.makefile("r").readline() or "{}")
                if req.get("op") == "stop":
                    conn.sendall(b'{"ok": true}\n')
                    break
                t0 = time.time()
                # One retry, for TRANSPORT failures only. The serial link drops
                # a reply now and then (see P4Board.cmd) and a scene that dies
                # that way is not a result. A frame that comes back WRONG raises
                # nothing and is not retried -- so a real raster bug still fails,
                # which is the distinction that makes this safe.
                try:
                    frame = run_scene(board, req["cart"], log=log)
                except RuntimeError as exc:
                    log("  transport failure (%s); retrying once" % exc)
                    frame = run_scene(board, req["cart"], log=log)
                with open(req["out"], "wb") as f:
                    f.write(frame)
                log("  %s in %.1fs" % (os.path.basename(req["cart"]), time.time() - t0))
                conn.sendall(b'{"ok": true}\n')
            except Exception as exc:            # noqa: BLE001
                # A scene that fails must not take the server down with it: the
                # next one may well pass, and a dead server turns one bad scene
                # into a whole suite that cannot run.
                log("  ERROR: %s" % exc)
                conn.sendall(json.dumps({"ok": False, "error": str(exc)}).encode() + b"\n")
            finally:
                conn.close()
    finally:
        board.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    return 0


def via_server(sock_path, cart, out):
    """Ask a running server for this scene. None if there is no server."""
    import socket
    if not os.path.exists(sock_path):
        return None
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(180.0)
        c.connect(sock_path)
    except OSError:
        return None                 # a stale socket file, not a live server
    try:
        c.sendall(json.dumps({"cart": os.path.abspath(cart),
                              "out": os.path.abspath(out)}).encode() + b"\n")
        reply = json.loads(c.makefile("r").readline() or "{}")
    finally:
        c.close()
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "the board server failed"))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cart", nargs="?", help="the conformance cart folder")
    ap.add_argument("out", nargs="?", help="where to write the raw frame")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--reset", action="store_true",
                    help="hard-reset first (slow; only needed if the board is wedged)")
    ap.add_argument("--serve", action="store_true",
                    help="hold the board and answer scene requests (see above)")
    ap.add_argument("--socket", default=SOCKET)
    ap.add_argument("--no-server", action="store_true",
                    help="ignore a running server and drive the board directly")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    log = print if (a.verbose or a.serve) else (lambda *x: None)
    if a.serve:
        return serve(a.port, a.socket, log)
    if not a.cart or not a.out:
        ap.error("cart and out are required unless --serve")

    if not a.no_server and via_server(a.socket, a.cart, a.out):
        return 0

    board = P4Board(a.port)
    try:
        # Opening the port already rebooted the board (see above), so this is
        # not so much a probe as a wait -- but it stays a probe, because a
        # future cable or port that does NOT reset gets the fast path for free.
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
