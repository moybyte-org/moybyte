"""What the browser end-to-end suites need, and what an absence MEANS.

The `tests/test_web_*_e2e.py` suites are the only checks in the tree that
drive the HOSTED CONSOLE -- the wasm head a visitor to moybyte.com touches,
and the same page a board serves over WiFi -- in a real browser. All are gated
on `MOYBYTE_WEB_E2E` because they cost a Chrome window and a couple of
minutes, and the first two used to carry their own copy of the same
prerequisite ladder (chrome, node, a dist/ new enough to have the thing under
test) with a bare `pytest.skip` at every rung.

The skip is RIGHT on a bench: a laptop with no emsdk build is a bench fact, not
a regression, and failing there would only teach people to stop running the
suite. In CI that same skip INVERTS into the hazard it was protecting against
-- a job that asks for these suites and then skips every one of them is a green
check that proves nothing, which is exactly how the compiled-vs-compiled raster
check was absent from every runner for months (tests/unix_mp.py's docstring).

So the absence is loud in the shape this repo already uses for the desktop
MicroPython (`MOYBYTE_REQUIRE_UNIX_MP`), the moy_flush harness
(`MOYBYTE_REQUIRE_MOY_FLUSH`) and the baked web bundle
(`MOYBYTE_REQUIRE_WEB_BUNDLE`): warn locally, FAIL under `CI` or
`MOYBYTE_REQUIRE_WEB_E2E`. Note the two switches are not the same one --
`MOYBYTE_WEB_E2E` asks for the suite to RUN, this one says a prerequisite for
it is a broken job rather than a missing toolchain.

The dist/ probes are STALENESS probes, and they are per-feature on purpose: a
build from before the pin prompt can still prove the sync loop, and reporting
it as unable to would be a false red on the half that works.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "firmware" / "web_runner"
DIST = RUNNER / "dist"
CHROME = os.environ.get("MOY_CHROME", "google-chrome")

BUILD = "firmware/web_runner/build.sh"

# Feature -> (is it in this dist?, what its absence is). Every probe reads a
# file out of dist/, so `missing()` never reaches them without one.
FEATURES = {
    "sync": (lambda: "syncPump" in (DIST / "worker.js").read_text(),
             "dist/worker.js predates the sync client"),
    "store": (lambda: (DIST / "moy_store.mjs").exists(),
              "dist/ predates the browser store (no moy_store.mjs)"),
    "pin": (lambda: "__moyPinRestore" in (DIST / "index.html").read_text(),
            "dist/index.html predates the in-page pin prompt"),
    # #194. Two halves, both of which can be stale on their own: the page has
    # to carry the report card, and the WASM has to carry the frozen converter.
    # A dist/ with the first and not the second imports nothing and says so in
    # a way that reads exactly like a broken feature.
    "p8": (lambda: ("__moyReport" in (DIST / "index.html").read_text()
                    and b"p8_writer" in (DIST / "micropython.wasm").read_bytes()),
           "dist/ predates the PICO-8 drop (no report card, or the frozen "
           "console has no p8 converter in it)"),
    # #41/#53: the firmware strip and the one link surface it hands off to.
    # Page-side only -- nothing about updating the board that serves this page
    # lives in the wasm, so the .wasm is not part of this probe.
    "update": (lambda: "__moyLinkLost" in (DIST / "index.html").read_text(),
               "dist/index.html predates the firmware strip (#41/#53)"),
    # #124: the worker's carts pump, the page's file question, and the
    # frozen bridge in the wasm -- three halves that go stale separately.
    "carts": (lambda: ("cartsPump" in (DIST / "worker.js").read_text()
                       and "pkSend" in (DIST / "index.html").read_text()
                       and b"carts_link" in (DIST / "micropython.wasm").read_bytes()),
              "dist/ predates Get Carts in the browser (no carts pump, no file "
              "question, or no carts_link frozen in the wasm)"),
}


def missing(*features):
    """Every unmet prerequisite, as ready-to-print lines. Empty means ready."""
    out = []
    if shutil.which(CHROME) is None:
        out.append("no %s on PATH -- install Google Chrome, or point "
                   "MOY_CHROME at a Chromium binary" % CHROME)
    if shutil.which("node") is None:
        out.append("no node on PATH -- browsershot.mjs drives Chrome over the "
                   "DevTools Protocol with node 22's own WebSocket client")
    if not (DIST / "index.html").exists():
        out.append("no built firmware/web_runner/dist -- run %s (it clones "
                   "emsdk, ~1.7GB, the first time)" % BUILD)
        return out                  # every probe below reads a file in dist/
    for name in features:
        probe, why = FEATURES[name]
        if not probe():
            out.append("%s -- rebuild it with %s" % (why, BUILD))
    return out


def require(*features):
    """Ready to drive a real browser, or a LOUD absence."""
    import pytest                   # lazy, to match tests/unix_mp.py

    problems = missing(*features)
    if not problems:
        return
    text = "\n".join(["the browser end-to-end run did not happen:", ""]
                     + ["  - " + p for p in problems])
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_WEB_E2E"):
        pytest.fail(text + "\n\nMOYBYTE_WEB_E2E asked for this suite, so a "
                    "missing prerequisite here is a broken job and not a bench "
                    "fact: a run that skips every browser check is a green "
                    "tick over an untested hosted console.")
    warnings.warn(UserWarning(text), stacklevel=2)
    pytest.skip("browser e2e prerequisites missing (see the warning above)")


def free_port():
    """A port nothing is listening on, for the serve.py each suite starts.

    Racy by nature (the socket is closed before the server binds it), which is
    why it asks the kernel for an ephemeral one rather than picking a constant:
    two suites running under xdist must not collide on a fixed port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Host:
    """A static host over `{path: bytes}`, with or without CORS, that writes
    down every path it was asked for. A single range (`bytes=a-b`) answers
    206 with those bytes and is written down in `ranges`, as GitHub Pages
    answers one; a preflight (OPTIONS) is refused with 405, as Pages refuses
    it, and written down in `preflights` -- a page that needed one could not
    have read the host."""

    def __init__(self, cors):
        self.files = {}
        self.asked = []
        self.ranges = []
        self.preflights = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_OPTIONS(self):
                host.preflights.append(self.path)
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                host.asked.append(path)
                body = host.files.get(path)
                m = re.match(r"bytes=(\d+)-(\d+)$", self.headers.get("Range") or "")
                if body is not None and m:
                    a, b = int(m.group(1)), int(m.group(2))
                    host.ranges.append((path, a, b))
                    part = body[a:b + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(body)))
                    body = part
                else:
                    self.send_response(200 if body is not None else 404)
                if cors:
                    self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                if body:
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def serve(site, port):
    """serve.py over a static `site` (the page's own host), and its base URL."""
    import pytest
    p = subprocess.Popen([sys.executable, "serve.py", str(port), str(site)],
                         cwd=RUNNER, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
            return p, base
        except OSError:
            if p.poll() is not None:
                pytest.fail("serve.py died on startup")
            time.sleep(0.1)
    p.terminate()
    pytest.fail("serve.py never answered")


def run(tmp_path, name, query, steps, base, profile, env=None, boot_ms=15000):
    """One browsershot scenario against `base` in the Chrome profile
    `profile`: (its stdout, every `js` step's value in order)."""
    path = tmp_path / ("%s.json" % name)
    path.write_text(json.dumps({"query": query, "boot_ms": boot_ms, "steps": steps}))
    r = subprocess.run(
        ["node", "browsershot.mjs", str(path), str(tmp_path / ("shots_" + name))],
        cwd=RUNNER, env=dict(os.environ, MOY_BASE=base, MOY_PROFILE=str(profile),
                             **(env or {})),
        capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, "browsershot %s failed:\n%s\n%s" % (
        name, r.stdout[-4000:], r.stderr[-500:])
    js = [json.loads(j) for j in re.findall(r"js -> (.*)$", r.stdout, re.M)]
    return r.stdout, js
