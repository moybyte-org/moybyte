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
# The board table and the repo root are push_cart's: one place derives the
# flashable boards from their board.toml files, and this tool only needs to
# name one of them to open its port the way that board declares.
from push_cart import BOARDS, ROOT  # noqa: E402
from p4_autotest import P4Board            # noqa: E402

W, H = 320, 240
FRAME_BYTES = W * H
CHUNK = 4096                    # wire bytes per base64 read: each one is a
                                # serial ROUND TRIP, and at 1024 the 76800-byte
                                # raw frame cost 75 of them


# --- getting the cart onto the board ----------------------------------------

def carts_root(board):
    """The cart store the CONSOLE says it uses, asked once per board session.

    DISCOVERED, NOT DECLARED -- the rule tools/push_cart.py states and follows.
    The store is not the same path on every board and on the Guition it is not
    even the same path on every boot: a TF card, when present, IS the store
    (/sd/carts), otherwise the internal VFS is (#202). A constant here would be
    wrong on that board half the time and a second source of truth on the two
    where it happens to be right."""
    root = getattr(board, "_carts_root", None)
    if root is None:
        root = board.pyval("str(ws.carts_root)", timeout=20.0)
        # A device path or nothing: `str()` keeps a lost reply and an unset
        # store from arriving as the plausible-looking string "None".
        if not isinstance(root, str) or not root.startswith("/"):
            raise RuntimeError("could not read ws.carts_root from the board (%r)"
                               % (root,))
        root = root.rstrip("/")
        board._carts_root = root
    return root


def push_cart(board, cart_dir, name, log=print):
    """Write a cart folder into <ws.carts_root>/<name>.moy and make the launcher
    see it, without rebooting -- a reset costs ~40s and the suite has nine
    scenes."""
    root = carts_root(board)
    dst = "%s/%s.moy" % (root, name)
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
    # Make the launcher see THIS cart, without rescanning the whole store.
    # `ws.carts.all` is the roster the launcher's items derive from (#209
    # landing C moved it off the console). A full moy_carts.scan(root) re-loads
    # every folder on the card -- 25s on the T-Deck, 130s on the Guition's TF
    # card, and WORSE as a long session fragments the heap (measured 2026-09-03:
    # a fresh Guition scans in 25s, a day-worn one in 52s). One cart's load is
    # ~0.1s, so load just the folder we pushed and splice it in: same visible
    # result, ~600x less time, and no dependence on the heap's state.
    ok = board.pyexec(
        "import moy_carts\n"
        "_c = moy_carts.load(%r)\n"
        "if _c:\n"
        "    ws.carts.all = [x for x in ws.carts.all"
        " if x.get('path') != _c['path']] + [_c]\n"
        "    ws.launcher.items = ws._launcher_items(ws.carts.all)\n" % dst)
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
    b = ws.canvas._buf
    n = len(b) // 2
    little = _order(b) == 'little'
    enc = bytearray()
    bad = 0
    rv = -1
    rn = 0
    for i in range(n):
        if little:
            v = _rev.get(b[2 * i] | (b[2 * i + 1] << 8))
        else:
            v = _rev.get((b[2 * i] << 8) | b[2 * i + 1])
        if v is None:
            bad += 1
            v = 0
        if v == rv and rn < 255:
            rn += 1
        else:
            if rn:
                enc.append(rn)
                enc.append(rv)
            rv = v
            rn = 1
        if len(enc) > n:
            ws._grabbed = None
            return -1
    if rn:
        enc.append(rn)
        enc.append(rv)
    ws._grabbed = b'\x01' + bytes(enc)
    return bad
"""


def _capture_raw(board, log):
    """A frame the board could not RLE (it ran past raw size): pull the RGB565
    words in slices and reduce them HERE. Slower on the wire (153600 bytes
    instead of a few thousand) and never a whole-frame allocation on the
    board -- which is the constraint the whole helper now lives under: an S3
    with the desk up has hundreds of KB free and no 76KB run of it (measured on
    both S3 boards, 2026-09-03), so the old `out = bytearray(n)` was a
    MemoryError on every scene there. Each `py` runs at a frame boundary and a
    conformance scene is static, so the slices agree."""
    from runtime import host_canvas
    host_canvas.install()
    import device_canvas as _dc
    import array as _array
    n = board.pyval("len(ws.canvas._buf)", timeout=10.0)
    swapped = board.pyval(
        "int(__import__('device_canvas').PAL565_WIRE is not "
        "__import__('device_canvas').PAL565)", timeout=10.0)
    raw = bytearray()
    step = 1536
    while len(raw) < n:
        piece = board.pyval(
            "__import__('binascii').b2a_base64(ws.canvas._buf[%d:%d])"
            % (len(raw), len(raw) + step), timeout=30.0)
        if not piece:
            raise RuntimeError("raw frame read stalled at byte %d of %d" % (len(raw), n))
        raw.extend(binascii.a2b_base64(piece))
    log("  %d bytes on the wire (raw: the frame did not compress)" % n)
    wire = _array.array("H", _dc.PAL565_SW if swapped else _dc.PAL565)
    out = _dc.to_indices(bytes(raw[:n]), None if wire == _dc.PAL565_WIRE else wire, False)
    if len(out) != FRAME_BYTES:
        raise RuntimeError("frame decoded to %d bytes, expected %d" % (len(out), FRAME_BYTES))
    return bytes(out)


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
    bad = board.pyval("ws._g['_grab']()", timeout=120.0)
    if bad is None:
        raise RuntimeError("the capture helper did not run (%s)" % board.last_error)
    if bad == -1:
        return _capture_raw(board, log)
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
    # The diag stream OFF for the session: its PERF lines share this UART
    # with the base64 push below and the capture after, and a line landing
    # mid-chunk is a decode error or a stalled read -- the same finding
    # push_cart.py records for its raw window. `diag` does not persist.
    board.cmd("diag 0", wait_for="REMOTE diag", timeout=8.0)
    board.drain(0.4)

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
# WHY A SERVER. Opening the port per scene used to reboot the board (the
# CH343's auto-reset circuit fires on the DTR/RTS order the kernel and pyserial
# produce at open; P4Board has opened in the order that does NOT fire it since
# 2026-09-02, and the board's board.toml [serial] carries the why). Even with
# that fixed, one process per scene re-installs the capture helper (~21 round
# trips) and re-learns the store; holding the board across the suite keeps the
# helper installed and the desk where it is. What still costs ~60s a scene is
# push_cart's whole-store rescan below, which a reboot would only make worse.
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


def serve(port, sock_path, log, board_dir=None):
    import socket
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    board = P4Board(port, board_dir=board_dir)
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
                if not req:
                    continue            # a probe (connect-and-close): not a scene
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
                try:
                    conn.sendall(json.dumps({"ok": False,
                                             "error": str(exc)}).encode() + b"\n")
                except OSError:
                    pass                # client gone: a dead pipe must not
                                        # take the server down either
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
    ap.add_argument("--board", default="p4", choices=sorted(BOARDS),
                    help="whose [serial] declaration to open the port with -- "
                         "the P4's line state chip-resets an S3 (default p4, "
                         "which is what this tool was written for)")
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
    board_dir = os.path.join(ROOT, BOARDS[a.board])
    if a.serve:
        return serve(a.port, a.socket, log, board_dir)
    if not a.cart or not a.out:
        ap.error("cart and out are required unless --serve")

    if not a.no_server and via_server(a.socket, a.cart, a.out):
        return 0

    board = P4Board(a.port, board_dir=board_dir)
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
