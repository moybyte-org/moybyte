#!/usr/bin/env python3
"""Serve the web runner's dist/ statically for local testing.

Plain `python -m http.server` minus two gotchas: .mjs must ship as
text/javascript (older CPython mimetypes map it to text/plain, which browsers
refuse for ES modules) and .wasm as application/wasm (streaming compile).

    python serve.py [port] [dir]            # defaults: 8321, dist/
                                            # dir may be absolute (the _site build)
    python serve.py [port] [dir] --carts D  # BOARD-TWIN mode: serve carts.json
                                            # LIVE from D and accept POST /sync
                                            # back into it -- the same pull+push
                                            # pair moy_webhost serves on a board,
                                            # against a plain directory. The
                                            # no-hardware way to watch a browser
                                            # edit land as files on disk.

Bind the directory by PATH, never by chdir. Every build here REPLACES its output
folder (build.sh and site/build.py both remove and recreate it), and a server
sitting inside the old one keeps a deleted inode as its cwd: the socket stays
open, and every request then dies in os.getcwd() with FileNotFoundError. Binding
by path re-resolves per request, so a rebuild mid-session is invisible.
"""

import functools
import http.server
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:]]
carts_dir = None
if "--carts" in args:
    i = args.index("--carts")
    carts_dir = os.path.abspath(args[i + 1])
    del args[i:i + 2]
root = os.path.join(_here, args[1] if len(args) > 1 else "dist")
port = int(args[0]) if args else 8321

http.server.SimpleHTTPRequestHandler.extensions_map[".mjs"] = "text/javascript"
http.server.SimpleHTTPRequestHandler.extensions_map[".js"] = "text/javascript"
http.server.SimpleHTTPRequestHandler.extensions_map[".wasm"] = "application/wasm"

if carts_dir is None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=root)
else:
    _repo = os.path.dirname(os.path.dirname(_here))
    sys.path.insert(0, _repo)
    sys.path.insert(0, os.path.join(_repo, "device"))   # flat sibling imports
    import moy_webhost                                  # noqa: E402
    from runtime import moy_sync                        # noqa: E402

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/sync":
                # The MODE MARKER (#193): a host that answers this owns the
                # carts, so the page keeps nothing locally. Static hosting
                # 404s here, which is how moybyte.com gets a browser store.
                self._send(200, b'{"sync":1}')
                return
            if self.path.split("?", 1)[0] == "/carts.json":
                # The board's own packer, over a plain directory -- one body,
                # so this twin cannot drift from what a board serves.
                body = json.dumps(moy_webhost.pack_store(carts_dir)).encode()
                self._send(200, body)
                return
            super().do_GET()

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/sync":
                self._send(404, b'{"error":"not found"}')
                return
            n = int(self.headers.get("Content-Length") or 0)
            ops, _pin = moy_sync.parse_batch(self.rfile.read(n))
            if ops is None:
                self._send(400, b'{"error":"bad batch"}')
                return
            applied, errors, _ = moy_sync.apply_ops(carts_dir, ops)
            if errors:
                print("sync: %d applied, %d refused: %s"
                      % (applied, len(errors), errors[0][1]))
            self._send(200, json.dumps(
                {"ok": applied, "err": [list(e) for e in errors[:8]]}).encode())

    handler = Handler

with http.server.ThreadingHTTPServer(("0.0.0.0", port), handler) as srv:
    note = (" + carts/sync over %s" % carts_dir) if carts_dir else ""
    print("serving %s at http://127.0.0.1:%d/%s" % (root, port, note))
    srv.serve_forever()
