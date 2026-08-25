#!/usr/bin/env python3
"""Serve the web runner's dist/ statically for local testing.

Plain `python -m http.server` minus two gotchas: .mjs must ship as
text/javascript (older CPython mimetypes map it to text/plain, which browsers
refuse for ES modules) and .wasm as application/wasm (streaming compile).

    python serve.py [port] [dir]            # defaults: 8321, dist/
                                            # dir may be absolute (the _site build)
    python serve.py [port] [dir] --carts D  # BOARD-TWIN mode: serve carts.json
                                            # (and files.json, the #108 layer
                                            # beside D) LIVE and accept POST
                                            # /sync back into either -- the same
                                            # pull+push pair moy_webhost serves
                                            # on a board, against a plain
                                            # directory. The no-hardware way to
                                            # watch a browser edit land as files
                                            # on disk.
    python serve.py ... --carts D --pin NNNN  # ...with a board's PIN GATE on
                                            # top. The dev loop stays PINLESS by
                                            # default and is meant to: a
                                            # password in the way of a rebuild
                                            # is a password people route around.
                                            # This flag exists so the gate and
                                            # the page's pin prompt can be
                                            # driven in a real browser with no
                                            # board on the desk.

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
pin = None
if "--carts" in args:
    i = args.index("--carts")
    carts_dir = os.path.abspath(args[i + 1])
    del args[i:i + 2]
if "--pin" in args:
    i = args.index("--pin")
    pin = args[i + 1]
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
    from moy_webserver import query_param as _query      # noqa: E402
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

        def _refused(self, sent):
            """True (and the 403 already sent) when this request lacks the pin.

            The predicate is moy_webhost's, imported rather than restated: a
            twin that gates differently from a board is a twin that proves
            nothing about the board."""
            if moy_webhost.pin_ok(pin, sent):
                return False
            self._send(403, moy_webhost.PIN_REFUSED.encode())
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/sync":
                # The MODE MARKER (#193): a host that answers this owns the
                # carts, so the page keeps nothing locally. Static hosting
                # 404s here, which is how moybyte.com gets a browser store.
                # OPEN even under --pin, exactly as a board's is: it says a
                # board lives here and nothing else, and the page has to decide
                # its mode before it has a pin to offer.
                self._send(200, b'{"sync":1}')
                return
            if path == "/carts.json":
                # The board's own packer, over a plain directory -- one body,
                # so this twin cannot drift from what a board serves.
                if self._refused(_query(self.path, "pin")):
                    return
                body = json.dumps(moy_webhost.pack_store(carts_dir)).encode()
                self._send(200, body)
                return
            if path == "/files.json":
                # The #108 user files beside the carts, kind-filtered so
                # .history/ and trash/ stay home -- and serving this at all is
                # what tells the page this twin speaks files.
                if self._refused(_query(self.path, "pin")):
                    return
                body = json.dumps(moy_webhost.pack_store(
                    moy_sync.files_root(carts_dir),
                    tops=moy_sync.file_kinds())).encode()
                self._send(200, body)
                return
            super().do_GET()          # the boot assets: never gated

        def do_POST(self):
            if self.path.split("?", 1)[0] != "/sync":
                self._send(404, b'{"error":"not found"}')
                return
            n = int(self.headers.get("Content-Length") or 0)
            ops, sent, root_id = moy_sync.parse_batch(self.rfile.read(n))
            if ops is None:
                self._send(400, b'{"error":"bad batch"}')
                return
            if self._refused(sent):
                return
            target = (moy_sync.files_root(carts_dir)
                      if root_id == moy_sync.FILES_ROOT_ID else carts_dir)
            # JOURNALED, like a board's apply: this directory is the store of
            # record for the page it serves, so a browser commit lands in the
            # cart's own journal/ here too (moy_sync's docstring has the
            # doctrine). It is also what lets the E2E prove the journal half
            # with no hardware.
            applied, errors, _ = moy_sync.apply_ops(target, ops, root_id,
                                                    journal=True)
            if errors:
                print("sync: %d applied, %d refused: %s"
                      % (applied, len(errors), errors[0][1]))
            self._send(200, json.dumps(
                {"ok": applied, "err": [list(e) for e in errors[:8]]}).encode())

    handler = Handler

with http.server.ThreadingHTTPServer(("0.0.0.0", port), handler) as srv:
    note = (" + carts/sync over %s" % carts_dir) if carts_dir else ""
    print("serving %s at http://127.0.0.1:%d/%s%s"
          % (root, port, "?pin=%s" % pin if pin else "", note))
    srv.serve_forever()
