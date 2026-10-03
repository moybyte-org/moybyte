#!/usr/bin/env python3
"""A page in headless Chrome as a PNG, and the browser console served.

    tools/web.py shot URL [--out f.png] [--wait-for SEL] [--click SEL]
                          [--width W] [--height H] [--settle MS]
    tools/web.py shot /                 this tree's browser console, served for the shot
    tools/web.py serve [--port N] [serve.py's flags: --carts D, --update ...]
                                        (a free port unless --port; it prints the URL)

`shot` prints the PNG's path and then every page error and console error, one
a line, and nothing else. A URL that starts with `/` is this tree's
`firmware/web_runner/dist/` (the browser console), served by `serve.py` on a
free port for the length of the shot. `--click` is repeatable and runs in
order, after `--wait-for`; `--settle` is the wait between the last step and
the picture.

The browser is the SYSTEM Chrome (`channel="chrome"`), driven by Playwright --
the `web` extra in pyproject.toml. No browser is downloaded: never run
`playwright install`.

`serve` is `firmware/web_runner/serve.py` over this tree's dist, its flags
passed through.

The browser suites (`tests/web_e2e.py`) drive Chrome through
`firmware/web_runner/browsershot.mjs` scenarios; this is the one-off look.
"""

import argparse
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, "firmware", "web_runner")
DIST = os.path.join(WEB, "dist")
SERVE_PY = os.path.join(WEB, "serve.py")
NO_DIST = ("no browser console in this tree (%s) -- firmware/web_runner/build.sh "
           "builds it" % os.path.relpath(DIST, ROOT))


class WebError(RuntimeError):
    """A step that cannot go on, in words a person can act on."""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_listening(port, proc, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise WebError("serve.py exited (%d) before it listened" % proc.returncode)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise WebError("serve.py did not listen on %d within %.0fs" % (port, timeout))


def start_server(port, extra=(), quiet=True):
    if not os.path.isdir(DIST):
        raise WebError(NO_DIST)
    out = subprocess.DEVNULL if quiet else None
    return subprocess.Popen([sys.executable, SERVE_PY, str(port), DIST] + list(extra),
                            stdout=out, stderr=out)


def shoot(url, out, width=1280, height=800, wait_for=None, clicks=(),
          settle_ms=500, timeout_ms=30000):
    """Load `url` in headless system Chrome, write the PNG; returns the page's
    errors (uncaught exceptions and console errors) in the order they came."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise WebError("no playwright in this python -- pip install -e '.[web]'")
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
            page.on("console", lambda m: errors.append("console: %s" % m.text)
                    if m.type == "error" else None)
            page.goto(url, wait_until="load", timeout=timeout_ms)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout_ms)
            for sel in clicks:
                page.click(sel, timeout=timeout_ms)
            if settle_ms:
                page.wait_for_timeout(settle_ms)
            page.screenshot(path=out)
        finally:
            browser.close()
    return errors


def cmd_shot(a):
    out = a.out or os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                "web-shot-%d.png" % os.getpid())
    server = None
    url = a.url
    try:
        if url.startswith("/"):
            port = free_port()
            server = start_server(port)
            wait_listening(port, server)
            url = "http://127.0.0.1:%d%s" % (port, url)
        errors = shoot(url, out, a.width, a.height, a.wait_for, a.click or (),
                       a.settle)
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=5)
    print(out)
    for e in errors:
        print(e)
    return 0


def cmd_serve(a, extra):
    port = a.port or free_port()
    proc = start_server(port, extra, quiet=False)
    wait_listening(port, proc)
    print("serving %s on http://127.0.0.1:%d/" % (os.path.relpath(DIST, ROOT), port),
          flush=True)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("shot", help="a page as a PNG")
    p.add_argument("url", help="http(s)://... or /path on this tree's console")
    p.add_argument("--out", help="the PNG (default: one under $TMPDIR)")
    p.add_argument("--wait-for", help="a CSS selector to wait for after load")
    p.add_argument("--click", action="append",
                   help="a CSS selector to click (repeatable, in order)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--settle", type=int, default=500,
                   help="ms to wait before the picture (default 500)")
    p = sub.add_parser("serve", help="serve this tree's browser console")
    p.add_argument("--port", type=int,
                   help="default: a free one (8321 is the owner's console)")
    return ap


def main(argv=None):
    a, extra = parser().parse_known_args(argv)
    if extra and a.cmd != "serve":
        parser().error("unrecognized arguments: %s" % " ".join(extra))
    try:
        return cmd_shot(a) if a.cmd == "shot" else cmd_serve(a, extra)
    except WebError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
