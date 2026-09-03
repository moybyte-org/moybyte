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
    python serve.py ... --carts D --update glass|headless
                                            # ...and a board's FIRMWARE routes
                                            # (GET/POST /update) on top, faked.
                                            # `headless` is a Zero -- the page
                                            # IS its update screen, so the
                                            # whole check/offer/download/
                                            # install/reboot arc plays out
                                            # there. `glass` is a console: the
                                            # trigger hands its own screen back
                                            # and this page stops being it.
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
import time

_here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:]]
carts_dir = None
pin = None
if "--carts" in args:
    i = args.index("--carts")
    carts_dir = os.path.abspath(args[i + 1])
    del args[i:i + 2]
update_mode = None
if "--update" in args:
    i = args.index("--update")
    update_mode = args[i + 1]
    del args[i:i + 2]
    if update_mode not in ("glass", "headless"):
        raise SystemExit("--update takes glass or headless")
    if "--carts" not in sys.argv:
        # The firmware routes ride the BOARD-TWIN handler; without --carts this
        # is a plain static server and the flag would be accepted and ignored,
        # which is how a scenario ends up testing nothing.
        raise SystemExit("--update needs --carts (it is part of the board twin)")
# THE GOODBYE, on a timer. A board that is going away on purpose -- WEB CONSOLE
# switched off, an update about to reboot -- keeps answering for a few seconds
# to SAY so (moy_webhost.stop's closing window), because from the browser a
# deliberate shutdown and an unplugged board look identical. `--close-after N`
# makes this twin do the same N seconds in, which is the only way to drive that
# path from a browser test: killing the process proves the opposite case.
close_after = None
_started = time.time()
if "--close-after" in args:
    i = args.index("--close-after")
    close_after = float(args[i + 1])
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

    class _TwinOta:
        """The handful of things `moy_webhost.update_status` asks an updater."""

        def __init__(self):
            self.boot_verdict = None

        def version(self):
            return 5

        def version_label(self):
            return "0.8"

        def channel(self):
            return "stable"

        def slot(self):
            return "ota_0"

    class TwinUpdate:
        """A SCRIPTED /update backend -- the wire SHAPE, not a state machine.

        The document goes through `moy_webhost.update_status`, the one body
        both real backends call, so a twin cannot drift from a board about what
        the page reads. What is faked is the behaviour behind it, and it is
        faked DETERMINISTICALLY: a request is answered without doing it (as a
        board's is -- the work happens in its poll loop), and the next STATUS
        READ is what advances things. That is what lets a browser walk the
        whole check -> offer -> download -> install -> reboot arc in a bounded
        number of polls with no board, no network and no clock.

        `screen` is the whole difference between the two modes, and it is the
        difference between the two real backends too: with glass, a trigger
        hands the glass back and this page stops being the console; without,
        this page is the only place the update can be watched.
        """

        DL_TOTAL = 2100000
        SLICES = 3

        def __init__(self, screen):
            self.screen = screen
            self.ota = _TwinOta()
            self.state = "idle"
            self.error = None
            self._want = None
            self._n = 0

        def request(self, action):
            if action not in ("check", "install", "cancel"):
                return False, "action must be check, install or cancel"
            if action == "cancel":
                self.state = "idle"
                self._want = None
                return True, "cancelled"
            if self.screen:
                if self.state == "glass":
                    return False, "the console already has this on its screen"
                self._want = "glass"
                return True, "the console took this onto its own screen"
            if self.state in ("downloading", "installing", "reboot"):
                return False, "busy: " + self.state
            self._want = "install" if action == "install" else "check"
            return True, "queued"

        def _advance(self):
            """One slice, taken on a status READ (a board takes it in its own
            poll loop; this twin has none)."""
            want, self._want = self._want, None
            if want == "glass":
                self.state = "glass"
                return
            if want == "check":
                self.state = "offer"
                return
            if want == "install":
                self.state = "downloading"
                self._n = 0
                return
            if self.state == "downloading":
                self._n += 1
                if self._n >= self.SLICES:
                    self.state = "installing"
                    self._n = 0
            elif self.state == "installing":
                self._n += 1
                if self._n >= self.SLICES:
                    self.state = "reboot"

        def status(self, advance=True):
            if advance:
                self._advance()
            offer = progress = None
            if self.state in ("offer", "downloading", "installing"):
                offer = {"version": 6, "label": "0.9", "channel": "stable",
                         "size": self.DL_TOTAL}
            if self.state == "downloading":
                progress = {"done": self._n * self.DL_TOTAL // self.SLICES,
                            "total": self.DL_TOTAL}
            elif self.state == "installing":
                progress = {"done": self._n * self.DL_TOTAL // self.SLICES,
                            "total": self.DL_TOTAL}
            return moy_webhost.update_status(
                self.ota, self.state, self.screen, error=self.error,
                offer=offer, progress=progress)

    update = TwinUpdate(update_mode == "glass") if update_mode else None

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

        def _closing(self):
            """True (and the 503 already sent) once the goodbye window opens.

            Ahead of routing AND ahead of the pin, exactly as the board does it:
            a page that was never given a pin still deserves to know the console
            was switched off rather than be left to decide it vanished.
            """
            if close_after is None or (time.time() - _started) < close_after:
                return False
            self._send(503, b'{"error":"closing","why":"off"}')
            return True

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
            if self._closing():
                return
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
            if path == "/update":
                # Both methods gate on the QUERY here, exactly as a board's do
                # -- see moy_webhost._update for why one endpoint may not read
                # its credential from two places.
                if update is None:
                    self._send(404, b'{"error":"not found"}')
                    return
                if self._refused(_query(self.path, "pin")):
                    return
                self._send(200, json.dumps(update.status()).encode())
                return
            super().do_GET()          # the boot assets: never gated

        def do_POST(self):
            if self._closing():
                return
            path = self.path.split("?", 1)[0]
            if path == "/update":
                if update is None:
                    self._send(404, b'{"error":"not found"}')
                    return
                if self._refused(_query(self.path, "pin")):
                    return
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    doc = json.loads(self.rfile.read(n) or b"{}")
                    action = doc.get("action") or "check"
                except ValueError:
                    self._send(400, b'{"error":"bad json"}')
                    return
                ok, msg = update.request(action)
                # ANSWERED WITHOUT ADVANCING, like a board: the request is
                # queued and the reader is what sees it happen.
                out = update.status(advance=False)
                out["ok"] = ok
                out["message"] = msg
                self._send(200 if ok else 409, json.dumps(out).encode())
                return
            if path != "/sync":
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
    if update_mode:
        note += " + a faked /update (%s)" % update_mode
    print("serving %s at http://127.0.0.1:%d/%s%s"
          % (root, port, "?pin=%s" % pin if pin else "", note))
    srv.serve_forever()
