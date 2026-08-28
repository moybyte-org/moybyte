#!/usr/bin/env python3
"""Put a folder of files onto a board over WiFi.

    python tools/p4_push_web.py --port /dev/ttyACM1              # the web console
    python tools/p4_push_web.py --dir ports/celeste.moy \
        --dest /moy/carts/celeste.moy --port /dev/ttyACM1        # any cart

The board serves `firmware/web_runner/dist` from `/moy/web` (P4) or `/sd/web`
(T-Deck); this is how those ~1.17MB get there.

Since 2026-08-15 the bundle also rides the FIRMWARE IMAGE (`native/moy_web`),
and storage still wins over it -- which is what keeps this tool worth having:
baking made a flashed board current-by-default, this makes a new web build
testable in seconds instead of a reflash. Delete `/moy/web` when you are done
with an experiment, or the board keeps serving your copy; it says which one it
is serving on the serial line `WEBHOST ...` when the row is switched on.

**The board PULLS.** This script stands up a throwaway HTTP server on the
machine you run it from and hands the board a URL over serial; the board then
downloads the four files over WiFi. The obvious alternative -- pushing the bytes
down the serial link -- means ~1500 base64'd chunks through a REPL for
`micropython.wasm` alone, and it cannot carry binary without escaping it. The
board already has WiFi up (the WEB CONSOLE row needs it anyway) and pulls at
tens of KB/s, so this takes seconds and needs no firmware support at all.

That last part is the point: this works against a board that has ALREADY been
flashed. Deploying a new web build costs no rebuild and no reflash.

It is deliberately not a `make` target. It needs a board on a serial port and
both machines on one network, which is a bench operation, not a build step.

WHY IT ALSO PUSHES CARTS (2026-08-15). `tools/push_cart.py` sends a cart down
the SERIAL link as base64 chunks, and on a 43KB main.lua that failed: the board
stalled ~7.5s (a BLE keyboard scan), its UART receive buffer overflowed mid-line
while it was not draining, the harness resent the chunk it had heard nothing
about, and the resend landed on the truncated remains as a SyntaxError. No chunk
size fixes a multi-second stall -- but the board is already on WiFi and already
knows how to pull, so the same downloader that carries a 1MB wasm carries a cart
in one request.

That stall mechanism is real and still is. What it does NOT mean, and what this
paragraph used to read as, is that serial cart push does not work. Dropping the
chunk 768 -> 256 (the P4's UART stdin ring is ~256 bytes with no flow control;
board.toml's [serial] block carries the measurement) took the same 44KB cart
clean on the FIRST try, 2026-08-19 -- 88s there, 45s on the T-Deck, whose RX
#201 fixed three days earlier. So tools/push_cart.py is the ordinary route for a
cart on any of the three boards; THIS downloader stays the right answer for the
~1MB web bundle, where one HTTP request beats thousands of base64 chunks.
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
from p4_autotest import P4Board                                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "firmware", "web_runner", "dist")

# What the board serves, and nothing else -- moy_webhost.ASSETS is the allowlist
# on the device side, so pushing a file that is not in it would just sit there,
# and pushing FEWER means the board keeps serving its baked copy (the pushed set
# only wins when it is complete). Read from the one list, not restated: this
# tuple was a hand copy of four names, and when moy_store.mjs joined ASSETS
# (2026-08-25) the push kept quietly shipping an incomplete set that could never
# take over. carts.json is NOT here on purpose: the board GENERATES that from
# its own store, which is the entire point of serving the console from the
# console.
import gen_web_blob                                            # noqa: E402
FILES = tuple(gen_web_blob.asset_names())


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
    ap.add_argument("--port", default="/dev/ttyACM1", help="board serial port")
    ap.add_argument("--web-dir", default="/moy/web",
                    help="destination on the board (/sd/web on the T-Deck)")
    ap.add_argument("--dist", default=DIST)
    ap.add_argument("--dir", help="push THIS folder instead of the web bundle")
    ap.add_argument("--dest", help="destination for --dir (e.g. /moy/carts/x.moy)")
    ap.add_argument("--http-port", type=int, default=8731)
    args = ap.parse_args(argv)

    global FILES
    if args.dir:
        args.dist = args.dir.rstrip("/")
        if not args.dest:
            sys.exit("--dir needs --dest (e.g. /moy/carts/celeste.moy)")
        args.web_dir = args.dest
        FILES = tuple(sorted(f for f in os.listdir(args.dist)
                             if os.path.isfile(os.path.join(args.dist, f))
                             and not f.startswith(".")))
        if not FILES:
            sys.exit("no files in " + args.dist)

    # Push the PRE-GZIPPED copy when the build made one: the board serves
    # `<name>.gz` with Content-Encoding and never inflates anything, so this
    # halves both the push and every later page load (1.13MB -> 0.56MB). Raw
    # stays the fallback for a dist built before build.sh emitted .gz.
    if not args.dir:
        FILES = tuple((f + ".gz") if os.path.exists(
            os.path.join(args.dist, f + ".gz")) else f for f in FILES)

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
    board = P4Board(args.port, log=lambda s: None)
    ok = False
    try:
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
