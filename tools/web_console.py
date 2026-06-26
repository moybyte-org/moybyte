#!/usr/bin/env python3
"""Web console (#22 v1, host-first): the KidCode desktop, remote-rendered in a browser.

The browser does NOT reimplement anything. The existing host `Workstation` (the
shared `runtime/console.py`, the same UI the T-Deck runs) stays authoritative and
keeps running on the PC; the browser is a thin client that **renders its
framebuffer and forwards input** -- so you drive the full desktop (launcher /
carts / code+paint editors / everything) from your computer as if local.

Architecture (a pure-stdlib HTTP server, polling protocol -- no third-party web
deps, no hand-rolled WebSockets):

  GET  /            -> the static HTML page (a scaled <canvas> + JS poller).
  GET  /frame.png   -> the CURRENT framebuffer as a PNG. The server steps the
                       console (`driver.frame(dt)`) once per request, converts
                       `ws.canvas.buf` (320x240 KID64 palette indices) to RGB via
                       the KID64 table, and PIL-encodes a 320x240 PNG.
  POST /input       -> JSON of browser events; we replay them through the SAME
                       host `ConsoleDriver` the pygame sim uses, so the console
                       reacts identically:
                         {"type":"move", "x":..,"y":..}      -> touch_drag
                         {"type":"down", "x":..,"y":..}      -> touch (a tap)
                         {"type":"up"}                       -> touch_up
                         {"type":"pan",  "dx":..,"dy":..}    -> pan (trackball)
                         {"type":"press","name":"run|a|b|home|left|.."} -> press
                         {"type":"hold", "name":..,"down":bool}          -> hold
                         {"type":"key",  "code":<ascii>}     -> type_char
                         {"type":"esc"}                      -> escape

The browser is the *only* input source, so we map mouse->touch and keys->keyboard
EXACTLY as `runtime/host_app.ConsoleDriver` (and `tools/simulate_desktop.run_live`)
do -- by driving that very ConsoleDriver, not a reimplementation of it.

Run it:

    python tools/web_console.py                 # http://<lan-ip>:8080/
    python tools/web_console.py --port 9000
    python tools/web_console.py --cart system_carts/star_catcher.kcart

Open the printed URL -> the full KidCode desktop, live in the browser.

The device port (a MicroPython HTTP server streaming the device framebuffer over
WiFi) is the follow-up, gated on #38 (WiFi manager) and the single-threaded loop
(serve between frames, never mid-flush). The protocol here is what it mirrors.
"""

import argparse
import io
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from runtime import host_app  # noqa: E402  (runs the SHARED console.Workstation)
from runtime import palette  # noqa: E402  (KID64 index -> RGB)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SAVE_DIR = os.path.expanduser("~/.kidcode/projects")
DEFAULT_PORT = 8080
DEFAULT_FPS = 30

# The launcher nav / gameplay buttons a browser key can map to (mirrors the pygame
# sim's WASD nav + Enter/Z/X/H shortcuts). The browser sends a logical `name`; we
# only forward names the console actually knows so a stray key can't wedge it.
BUTTON_NAMES = frozenset(("left", "right", "up", "down", "a", "b", "run", "home"))


def _read_html():
    """Serve the static page from tools/web_console.html (next to this file)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_console.html")
    with open(path, "rb") as f:
        return f.read()


class WebConsole:
    """Owns the single live Workstation + its ConsoleDriver, and turns the current
    framebuffer into a PNG. All console mutation (frame step + input) is serialized
    under one lock so the threaded HTTP server never steps the console from two
    requests at once (the console is single-threaded, like the device loop)."""

    def __init__(self, save_dir, fps=DEFAULT_FPS, cart=None):
        self.dt = 1.0 / max(1, fps)
        self.ws = host_app.build_workstation(save_dir)
        # Live, real-connection-aware WiFi (your PC is online) -- matches the
        # interactive pygame run, so network carts test against real sockets.
        self.ws.wifi = host_app.make_host_wifi(host_app.kid_carts, self.ws.carts_root)
        if cart:
            self._open_named_cart(cart, save_dir)
        self.driver = host_app.ConsoleDriver(self.ws)
        self._lock = threading.Lock()
        self._rgb_table = [bytes(rgb) for rgb in palette.KID64]
        # PIL is available on the host (used by the GIF export too); import here so
        # the module imports even where Pillow is absent until a frame is requested.
        from PIL import Image  # noqa: F401  (validate availability eagerly)
        self._Image = Image

    def _open_named_cart(self, cart_path, carts_dir):
        """Copy a named .kcart into the store (if needed), select + open it -- the
        same skip-the-launcher path as tools/simulate_desktop._open_named_cart."""
        name = os.path.basename(os.path.normpath(cart_path))
        dst = os.path.join(carts_dir, name)
        if os.path.abspath(cart_path) != os.path.abspath(dst) and not os.path.exists(dst):
            import shutil
            shutil.copytree(cart_path, dst)
        self.ws.launcher.items = host_app.kid_carts.scan(self.ws.carts_root)
        for i, c in enumerate(self.ws.launcher.items):
            if os.path.abspath(c["path"]) == os.path.abspath(dst):
                self.ws.launcher.sel = i
                break
        self.ws.open()

    # -- input ---------------------------------------------------------------
    def apply_events(self, events):
        """Replay a batch of browser events through the host ConsoleDriver, exactly
        as the pygame sim would -- mouse->touch, keys->keyboard/buttons/trackball."""
        d = self.driver
        with self._lock:
            for ev in events:
                t = ev.get("type")
                if t == "down":
                    d.touch(ev.get("x", 0), ev.get("y", 0))      # tap (press edge)
                elif t == "move":
                    d.touch_drag(ev.get("x", 0), ev.get("y", 0))  # drag, button down
                elif t == "up":
                    d.touch_up()
                elif t == "pan":
                    d.pan(int(ev.get("dx", 0)), int(ev.get("dy", 0)))  # trackball
                elif t == "press":
                    name = ev.get("name")
                    if name in BUTTON_NAMES:
                        d.press(name)
                elif t == "hold":
                    name = ev.get("name")
                    if name in BUTTON_NAMES:
                        d.hold(name, bool(ev.get("down")))
                elif t == "key":
                    code = ev.get("code")
                    if isinstance(code, int) and 0 <= code <= 0xFF:
                        d.type_char(code)
                elif t == "esc":
                    d.escape()

    # -- output --------------------------------------------------------------
    def step_png(self):
        """Advance the console one frame and return the framebuffer as PNG bytes."""
        with self._lock:
            self.driver.frame(self.dt)
            cv = self.driver.current_canvas()
            w, h = cv.w, cv.h
            rgb = self._buf_to_rgb(cv.buf)
        img = self._Image.frombytes("RGB", (w, h), rgb)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), w, h

    def _buf_to_rgb(self, buf):
        # Indices -> RGB888 via the KID64 table. (Canvas.to_rgb888 does the same;
        # we use a cached table so we never re-pack the palette per frame.)
        table = self._rgb_table
        return b"".join(table[i] for i in buf)


class _Handler(BaseHTTPRequestHandler):
    # Set per-server (see make_server).
    console = None
    html = b""

    def log_message(self, *_args):
        # Quiet: one request per frame would flood stderr.
        pass

    def _send(self, code, body, content_type, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Frames must never be cached -- the page polls the same URL repeatedly.
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, self.html, "text/html; charset=utf-8")
        elif path == "/frame.png":
            try:
                png, _w, _h = self.console.step_png()
            except Exception as exc:  # noqa: BLE001 -- a frame error must not kill the server
                self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, png, "image/png")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/input":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            events = payload.get("events", payload if isinstance(payload, list) else [])
            if not isinstance(events, list):
                events = []
            self.console.apply_events(events)
        except Exception as exc:  # noqa: BLE001
            self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(200, b'{"ok":true}', "application/json")


def make_server(console, host="0.0.0.0", port=DEFAULT_PORT, html=None):
    """Build a ThreadingHTTPServer bound to (host, port) serving `console`.

    Returns the server; call serve_forever() (or use it in a test with a thread).
    Pass port=0 for an ephemeral port (tests read server.server_address[1])."""
    handler = type("_BoundHandler", (_Handler,), {
        "console": console,
        "html": html if html is not None else _read_html(),
    })
    return ThreadingHTTPServer((host, port), handler)


def _lan_url(port):
    """Best-effort http URL on the real LAN IP (so a phone/another PC can reach it),
    falling back to localhost. Reuses host_app._real_local_ip (no packet sent)."""
    ip = host_app._real_local_ip()
    return "http://%s:%d/" % (ip or "127.0.0.1", port)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KidCode web console (#22 v1, host-first)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = reachable on the LAN)")
    ap.add_argument("--cart", help="open a single .kcart directly (skip the launcher)")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS,
                    help="console step rate (dt = 1/fps per /frame.png request)")
    args = ap.parse_args(argv)

    console = WebConsole(args.save_dir, fps=args.fps, cart=args.cart)
    server = make_server(console, host=args.host, port=args.port)
    url = _lan_url(server.server_address[1])
    print("KidCode web console serving the live desktop at:")
    print("    %s" % url)
    print("    http://127.0.0.1:%d/  (localhost)" % server.server_address[1])
    print("Open it in a browser to drive the full console. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
