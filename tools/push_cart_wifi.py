#!/usr/bin/env python3
"""Put a folder of files onto a board over WiFi -- normally a cart.

    python tools/push_cart_wifi.py --board guition_s3 \
        --dir ports/celeste.moy --dest /sd/carts/celeste.moy

--board is REQUIRED and has no default, for the reason push_cart.py and
p4_perf.py spell out: the boards differ in the serial line state at open, and
opening an S3 with the P4's low lines CHIP-RESETS it.

**The board PULLS.** This stands up a throwaway HTTP server on the machine you
run it from and hands the board a URL over serial; the board then downloads the
files over WiFi. The board already has WiFi up (the WEB CONSOLE row needs it
anyway) and pulls at tens of KB/s, so a whole cart takes seconds and this needs
no firmware support at all.

WHY THIS EXISTED, AND WHAT IS LEFT OF THE REASON. `tools/push_cart.py` used to
send a cart as base64 inside `py` lines at roughly 500 B/s -- minutes per cart
-- and that path resent a chunk after a stall, so a multi-second one (a BLE
keyboard scan) could overflow the board's receive buffer mid-line and land the
resend on the truncated remains as a SyntaxError. Both facts are gone: that
push was DELETED on 2026-09-02 and the serial route is now the dev channel's
raw `recv`, which carries the payload 8 bits wide under a window/ack discipline
and never resends. Serial is the ordinary route and is no longer the slow one.
What this still buys is a transfer the console does not stop for: `recv` blocks
the frame loop until the last byte lands, while the board pulls these files
over WiFi. It also carries files nothing else does -- an arbitrary --dir to an
arbitrary --dest.

It is deliberately not a `make` target. It needs a board on a serial port and
both machines on one network, which is a bench operation, not a build step.
"""

from __future__ import annotations

import argparse
import http.server
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p4_autotest import P4Board, board_dirs                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _lan_ip():
    """This machine's address ON THE BOARD'S NETWORK.

    A UDP connect to a routable address picks the interface the kernel would
    actually use, without sending anything. `gethostname()` is the usual
    shortcut and it resolves to 127.0.0.1 on most desktop Linux, which the board
    cannot reach and which fails as a timeout rather than an error.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 1))
        return s.getsockname()[0]
    finally:
        s.close()


# The downloader that runs ON the board. Plain sockets: `urequests` is not in
# this build, and the response is BINARY (a wasm module), so it reads bytes and
# writes "wb" rather than decoding anything.
DOWNLOADER = '''
import socket, os

def _mkdirp(d):
    parts = d.strip("/").split("/")
    at = ""
    for p in parts:
        at += "/" + p
        try:
            os.mkdir(at)
        except OSError:
            pass

def _get(host, port, name, dest):
    ai = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(20)
    try:
        s.connect(ai)
        s.send(b"GET /" + name.encode() + b" HTTP/1.0\\r\\nHost: x\\r\\n\\r\\n")
        buf = b""
        while b"\\r\\n\\r\\n" not in buf:
            c = s.recv(512)
            if not c:
                raise OSError("no header")
            buf += c
        head, _, rest = buf.partition(b"\\r\\n\\r\\n")
        if b"200" not in head.split(b"\\r\\n")[0]:
            raise OSError("http %s" % head.split(b"\\r\\n")[0])
        f = open(dest, "wb")
        n = 0
        try:
            if rest:
                f.write(rest)
                n += len(rest)
            while True:
                c = s.recv(1024)
                if not c:
                    break
                f.write(c)
                n += len(c)
        finally:
            f.close()
        return n
    finally:
        s.close()

_mkdirp(WEBDIR)
for _name in FILES:
    _dest = WEBDIR + "/" + _name
    try:
        _n = _get(HOST, PORT, _name, _dest)
        print("PUSH ok %s %d" % (_name, _n))
    except Exception as _e:
        print("PUSH err %s %s" % (_name, _e))
print("PUSH done")
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", required=True, choices=sorted(board_dirs()),
                    help="which board; supplies the serial line state at open")
    ap.add_argument("--port", help="override the port --board resolves to")
    ap.add_argument("--dir", required=True, help="the folder to push")
    ap.add_argument("--dest", required=True,
                    help="destination on the board (e.g. /sd/carts/x.moy)")
    ap.add_argument("--http-port", type=int, default=8731)
    args = ap.parse_args(argv)

    global FILES
    args.dist = args.dir.rstrip("/")
    args.web_dir = args.dest
    FILES = tuple(sorted(f for f in os.listdir(args.dist)
                         if os.path.isfile(os.path.join(args.dist, f))
                         and not f.startswith(".")))
    if not FILES:
        sys.exit("no files in " + args.dist)

    missing = [f for f in FILES if not os.path.exists(os.path.join(args.dist, f))]
    if missing:
        sys.exit("error: %s missing from %s\n  build it: cd firmware/web_runner "
                 "&& ./build.sh" % (", ".join(missing), args.dist))
    sizes = {f: os.path.getsize(os.path.join(args.dist, f)) for f in FILES}
    total = sum(sizes.values())
    print("serving %s (%d files, %.2f MB)" % (args.dist, len(FILES),
                                              total / 1024.0 / 1024.0))

    dist = args.dist

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=dist, **kw)

        def log_message(self, *a):        # one line per file is enough
            pass

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", args.http_port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    ip = _lan_ip()
    print("  http://%s:%d/  ->  %s on the board" % (ip, args.http_port,
                                                    args.web_dir))
    board = P4Board(args.port or "auto", board_dir=board_dirs()[args.board],
                    log=lambda s: None)
    ok = False
    try:
        # A CH343 board gets a clean slate first. An attach_only board must not
        # be reset at all -- its USB serial is on the SoC, so the pulse strands
        # this handle -- and does not need to be: the downloader below runs
        # against whatever state it is already in.
        if board.attach_only:
            board.drain(1.0)
        else:
            board.reset(boot_timeout=90)
        time.sleep(2)
        # The WEB CONSOLE row owns the WiFi bring-up (and its 12s link wait), so
        # turning it on is also how this gets a network. Idempotent: already-on
        # stays on.
        board.pyval("(ws.webhost_serving() or ws.toggle_webhost()) or 1",
                    timeout=90)
        print("board wifi:", board.pyval("ws.webhost_label()"))
        code = ("HOST = %r\nPORT = %d\nWEBDIR = %r\nFILES = %r\n"
                % (ip, args.http_port, args.web_dir, list(FILES))) + DOWNLOADER
        board.lines.clear()
        board.pyexec(code, timeout=240)
        time.sleep(2)
        got = {}
        for line in board.lines:
            line = line.strip()
            if line.startswith("PUSH ok "):
                _, _, name, n = line.split()
                got[name] = int(n)
                print("  %-18s %8d bytes" % (name, int(n)))
            elif line.startswith("PUSH err"):
                print("  " + line)
        bad = [f for f in FILES if got.get(f) != sizes[f]]
        if bad:
            print("MISMATCH: %s" % ", ".join(
                "%s got %s want %d" % (f, got.get(f), sizes[f]) for f in bad))
        else:
            ok = True
            print("all %d files match. open http://%s in a browser."
                  % (len(FILES),
                     (board.pyval("ws.webhost_label()") or "the board")))
    finally:
        board.close()
        httpd.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
