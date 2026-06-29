#!/usr/bin/env python3
"""Web console (#22, cutoff #2): the web is a new CANVAS BACKEND (draw-command
streaming), not a pixel viewer.

The browser does NOT reimplement the console. The existing host `Workstation` (the
shared `runtime/console.py`, the same UI the T-Deck runs) stays authoritative and
keeps running on the PC. v1 streamed its rasterized framebuffer as a PNG; this
cutoff replaces that transport: the console draws through an *injected* canvas, so
we swap in a `CommandCanvas` that **records each draw call** (`cls/rect/spr/print/
map/...`) into a per-frame, JSON-serializable command list. We ship that list to
the browser, and a JS replayer redraws the commands on a scaled <canvas> (crisp,
resolution-independent). The wire is a few calls per frame, not 76,800 pixels.

The swap is done WITHOUT touching the shared tree: `Workstation` reads `self.canvas`
every frame, so after `host_app.build_workstation()` we just reassign
`ws.canvas = CommandCanvas(...)`. (`ws.comp` stays the host `_NullComp`, whose
flush() is a no-op -- there's no panel to flush on the host.)

Architecture (a pure-stdlib HTTP server, polling protocol -- no third-party web
deps, no hand-rolled WebSockets):

  GET  /          -> the static HTML page (a scaled <canvas> + JS replayer/poller).
  GET  /assets    -> the STATIC stuff the browser needs to render the command list:
                       { "w","h",                       # logical surface size
                         "palette": [[r,g,b], ...64],    # KID64 index -> RGB
                         "font": {"first","w","h","glyphs":[[col,...8], ...]},
                         "sheet": {...} | null,          # open cart's sprite sheet
                         "tilemap": {...} | null,        # open cart's tilemap
                         "cart": "<title or null>" }     # for cart-change detection
                     The page refetches /assets when "cart" changes (new sheet).
  GET  /frame     -> JSON {"cmds":[...], "cart":"<title>"}: the recorded draw calls
                     for ONE freshly-stepped frame. Each request: clear the canvas's
                     buffer, `driver.frame(dt)` (the console draws into CommandCanvas),
                     then `take_commands()` and return them. "cart" lets the client
                     notice a cart change and refetch /assets.
  POST /input     -> JSON of browser events; replayed through the SAME host
                     `ConsoleDriver` the pygame sim uses, so the console reacts
                     identically (unchanged from v1):
                       {"type":"move", "x":..,"y":..}      -> touch_drag
                       {"type":"down", "x":..,"y":..}      -> touch (a tap)
                       {"type":"up"}                       -> touch_up
                       {"type":"pan",  "dx":..,"dy":..}    -> pan (trackball)
                       {"type":"press","name":"run|a|b|home|left|.."} -> press
                       {"type":"hold", "name":..,"down":bool}          -> hold
                       {"type":"key",  "code":<ascii>}     -> type_char
                       {"type":"esc"}                      -> escape

Run it:

    python tools/web_console.py                 # http://<lan-ip>:8080/
    python tools/web_console.py --port 9000
    python tools/web_console.py --cart system_carts/star_catcher.kcart

Open the printed URL -> the full KidCode desktop, live in the browser, redrawn from
the command stream.

The device port (a MicroPython command-recording canvas + an HTTP server over WiFi)
is the follow-up, gated on #38 (WiFi manager) and the single-threaded loop (serve
between frames, never mid-flush). It is MUCH lighter than v1's pixel transport: the
device only records the calls it already makes -- it never has to read back or
encode its framebuffer.
"""

import argparse
import base64
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from runtime import font  # noqa: E402  (petme128 glyphs -> JSON for the replayer)
from runtime import audio as _audio  # noqa: E402  (engine sample rate for the browser player)
from runtime import host_app  # noqa: E402  (runs the SHARED console.Workstation)
from runtime import palette  # noqa: E402  (KID64 index -> RGB)
from tools.command_canvas import CommandCanvas  # noqa: E402  (the recording backend)

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


def font_glyphs():
    """Export the petme128 font as JSON: each glyph is its 8 column-bytes (LSB =
    top row, exactly how runtime.font.draw scans it), so the JS replayer renders
    `print` pixel-identically to the host Canvas.print / device framebuf.text."""
    glyphs = []
    n = len(font._FONT) // font.WIDTH
    for i in range(n):
        col = font._FONT[i * font.WIDTH:(i + 1) * font.WIDTH]
        glyphs.append(list(col))
    return {"first": font.FIRST, "w": font.WIDTH, "h": font.HEIGHT, "glyphs": glyphs}


def sheet_json(sheet):
    """The open cart's sprite sheet as JSON (cols/rows/TILE + flat 16-color pixels).
    Sent so a future id-based `spr` can resolve on the browser; correctness never
    depends on it (spr commands carry their own pixels)."""
    if sheet is None:
        return None
    return {
        "cols": sheet.cols, "rows": sheet.rows, "tile": sheet.TILE,
        "w": sheet.w, "h": sheet.h,
        "pix": list(sheet.pix),
    }


def tilemap_json(tilemap):
    """The open cart's tilemap as JSON (w/h + flat cells, each = tile_id+1, 0=empty)
    -- mirrors TileMap's storage. Sent for completeness / a future id-based map."""
    if tilemap is None:
        return None
    return {"w": tilemap.w, "h": tilemap.h, "cells": list(tilemap.cells)}


class WebConsole:
    """Owns the single live Workstation, swaps its canvas for a CommandCanvas, and
    serves the recorded command stream + the static render assets. All console
    mutation (frame step + input) is serialized under one lock so the threaded HTTP
    server never steps the console from two requests at once (the console is
    single-threaded, like the device loop)."""

    def __init__(self, save_dir, fps=DEFAULT_FPS, cart=None, sys_size=None,
                 font_scale=1):
        self.dt = 1.0 / max(1, fps)
        # Two-domain seam (#39): `sys_size` is the SYSTEM canvas size the desktop
        # renders on (default 320x240 = today); the game stays a fixed 320x240,
        # composited as a centered viewport. The browser already reads canvas.w/h,
        # so a larger system canvas just fills more of the page.
        self.ws = host_app.build_workstation(save_dir, sys_size=sys_size,
                                             font_scale=font_scale)
        # The decided architecture: the web is a NEW CANVAS BACKEND. The shared
        # console draws the desktop chrome through the SYSTEM canvas every frame, so
        # we reassign THAT to a recording CommandCanvas WITHOUT touching
        # runtime/console.py. (ws.comp stays the host _NullComp; its flush() is a
        # no-op -- nothing to flush on the host.)
        sw, sh = (self.ws.sys_canvas.w, self.ws.sys_canvas.h)
        self.canvas = CommandCanvas(sw, sh, font_scale=self.ws._effective_font_scale())
        if self.ws._sys_canvas is None:
            # Degradation (320x240): one surface -- the game canvas IS the system
            # canvas, so swap ws.canvas (the cart draws into the recorder too). The
            # game canvas keeps 8px text regardless, so the recorder's font_scale must
            # be 1 here (no distinct system canvas to scale).
            self.canvas.set_font_scale(1)
            self.ws.canvas = self.canvas
        else:
            # Distinct system canvas: record the chrome on the system canvas; the game
            # canvas stays a real rasterizer so _composite_game can read its pixels
            # and emit the viewport as one spr command into the recorder. Relayout so
            # the launcher grid + chrome reflect the recorder's size/scale.
            self.ws._sys_canvas = self.canvas
            self.ws._relayout()
        # Rebind the wallpaper to the recording canvas we just swapped in. build_workstation
        # compiled the wallpaper cart's draw API against the ORIGINAL canvas, so without
        # this its cls()/backdrop draws go to the orphaned canvas and never reach the
        # stream -- the browser's retained buffer is then never cleared and the chrome
        # ghosts across redraws (the device clears its framebuffer regardless, so it looks
        # fine there). Recompiling re-runs make_api against the current ws.canvas.
        if getattr(self.ws, "wallpaper_id", None):
            self.ws.select_wallpaper(self.ws.wallpaper_id, persist=False)
        # Live, real-connection-aware WiFi (your PC is online) -- matches the
        # interactive pygame run, so network carts test against real sockets.
        self.ws.wifi = host_app.make_host_wifi(host_app.kid_carts, self.ws.carts_root)
        if cart:
            self._open_named_cart(cart, save_dir)
        self.driver = host_app.ConsoleDriver(self.ws)
        self._lock = threading.Lock()

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

    def _cart_title(self):
        """A stable id for the open cart (its title) so the client can detect a cart
        change and refetch /assets. None on the launcher/desktop home."""
        cart = getattr(self.ws, "cart", None)
        if cart is None:
            return None
        return cart.get("title")

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
    def step_frame(self):
        """Advance the console one frame and return (commands, cart_title, audio_b64).

        The console draws into the CommandCanvas during driver.frame(); we take the
        recorded list afterward. We clear any stale commands first so a partially
        recorded frame (shouldn't happen under the lock) can never leak in. We also
        drain the per-cart audio backend's last rendered PCM block (signed-16 LE mono)
        and base64 it -- the browser plays the FINISHED samples, so there's no second
        synth in JS. Empty string when nothing played this frame (no cart / silence)."""
        with self._lock:
            self.canvas.take_commands()      # drop anything stale (defensive)
            self.driver.frame(self.dt)
            cmds = self.canvas.take_commands()
            cart = self._cart_title()
            au = getattr(self.ws, "audio", None)
            pcm = au.take_pcm() if (au is not None and hasattr(au, "take_pcm")) else b""
            audio_b64 = base64.b64encode(pcm).decode("ascii") if (pcm and any(pcm)) else ""
        return cmds, cart, audio_b64

    def assets(self):
        """The static render assets the browser needs: palette + font + the open
        cart's sheet/tilemap. Re-fetched by the client when the cart changes."""
        with self._lock:
            sheet = sheet_json(getattr(self.ws, "sheet", None))
            tilemap = tilemap_json(getattr(self.ws, "tilemap", None))
            cart = self._cart_title()
        return {
            "w": self.canvas.w, "h": self.canvas.h,
            "palette": [list(rgb) for rgb in palette.KID64],
            "font": font_glyphs(),
            "sheet": sheet,
            "tilemap": tilemap,
            "cart": cart,
            "audio_rate": _audio.AudioEngine().rate,   # PCM sample rate for the browser player
        }


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
        # Frames/assets must never be cached -- the page polls the same URLs.
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, self.html, "text/html; charset=utf-8")
        elif path == "/assets":
            try:
                assets = self.console.assets()
            except Exception as exc:  # noqa: BLE001 -- must not kill the server
                self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send_json(200, assets)
        elif path == "/frame":
            try:
                cmds, cart, audio = self.console.step_frame()
            except Exception as exc:  # noqa: BLE001 -- a frame error must not kill the server
                self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send_json(200, {"cmds": cmds, "cart": cart, "audio": audio})
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
    ap = argparse.ArgumentParser(description="KidCode web console (#22, draw-command streaming)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = reachable on the LAN)")
    ap.add_argument("--cart", help="open a single .kcart directly (skip the launcher)")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS,
                    help="console step rate (dt = 1/fps per /frame request)")
    # Two-domain seam (#39): the SYSTEM canvas size the desktop fills in the browser
    # (default 320x240 = today). The game is the centered fixed-aspect viewport.
    ap.add_argument("--size", default="320x240", metavar="WxH",
                    help="system canvas size (default 320x240)")
    ap.add_argument("--font-scale", type=int, default=1, choices=(1, 2, 3),
                    help="initial system-UI font scale 1/2/3 (system.json overrides)")
    args = ap.parse_args(argv)

    w, _, h = args.size.lower().partition("x")
    console = WebConsole(args.save_dir, fps=args.fps, cart=args.cart,
                         sys_size=(int(w), int(h)), font_scale=args.font_scale)
    server = make_server(console, host=args.host, port=args.port)
    url = _lan_url(server.server_address[1])
    print("KidCode web console (draw-command streaming) serving the live desktop at:")
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
