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

Architecture (a pure-stdlib HTTP server + a hand-rolled WebSocket -- no third-party
web deps). The page + assets load over plain HTTP; the LIVE channel is a persistent
WebSocket, the SAME transport the device speaks (the two share web_view.PAGE_HTML +
the ws_* framing), so there is ONE path -- no HTTP poll fallback:

  GET  /          -> the static HTML page (a scaled <canvas> + JS replayer).
  GET  /assets    -> the STATIC stuff the browser needs to render the command list:
                       { "w","h",                       # logical surface size
                         "palette": [[r,g,b], ...64],    # MOY64 index -> RGB
                         "font": {"first","w","h","glyphs":[[col,...8], ...]},
                         "sheet": {...} | null,          # open cart's sprite sheet
                         "tilemap": {...} | null,        # open cart's tilemap
                         "images": {name:{w,h,b64}}|null,# open cart's paint images (#63 F4)
                         "cart": "<title or null>" }     # for cart-change detection
                     The page refetches /assets when "cart" changes (new sheet).
  GET  /ws        -> (Upgrade: websocket) the PERSISTENT live channel. Per tick the
                     server steps the console ONE frame and PUSHES a frame_payload
                     ({"cmds","cart","gen","audio"}) text message down; the browser
                     pushes {"events":[...]} text messages UP, replayed through the
                     SAME host `ConsoleDriver` the pygame sim uses so the console
                     reacts identically:
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
    python tools/web_console.py --cart system_carts/star_catcher.moy

Open the printed URL -> the full Moybyte desktop, live in the browser, redrawn from
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
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from runtime import audio as _audio  # noqa: E402  (engine sample rate for the browser player)
from runtime import host_app  # noqa: E402  (runs the SHARED console.Workstation)
from runtime import palette  # noqa: E402  (MOY64 index -> RGB)
# The SHARED web-view core (canonical source; the DEVICE freezes the same file). The host is a
# thin http.server + WebSocket transport over it: it swaps in web_view.CommandCanvas as the
# console's system canvas, ships web_view.PAGE_HTML, drives the live channel with the SAME
# web_view.ws_* framing the device uses, and routes each pushed frame through web_view.ServedState
# so it runs the EXACT same serve path (serve-time defspr/deflayer + gen) the device does.
from runtime import web_view  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SAVE_DIR = os.path.expanduser("~/.moybyte/projects")
DEFAULT_PORT = 8080
DEFAULT_FPS = 30

# How long a slow client may stall one frame push before we drop it. The read
# timeout that paces _ws_loop (20ms) must NOT double as the send budget: a WiFi
# client in power-save routinely stalls >20ms, and socket.timeout is an OSError,
# so every such hiccup used to sever the socket -- the browser page sat in a
# reconnect loop. Each WS client runs in its own handler thread (unlike the
# device's single-threaded loop), so a generous budget stalls nobody else.
WS_SEND_BUDGET = 1.0


class _DriverPointerSink:
    """Adapts a ConsoleDriver's DEFERRED touch state (self._down / self._click, mixed
    into ws.pointer only at frame() time) to the Pointer-shaped interface
    runtime.web_view.apply_events expects (place() + a down/click PROPERTY pair) --
    the same sink shape the device's device_webview._PointerSink presents (#42
    Thread 3), so both transports drive the ONE shared event-decode path instead of
    two hand-rolled ones. place() writes straight through to the real ws.pointer
    (ConsoleDriver.touch() does too -- only down/click are deferred)."""

    def __init__(self, driver):
        self._d = driver

    def place(self, x, y):
        self._d.pointer.place(int(x), int(y))

    @property
    def down(self):
        return self._d._down

    @down.setter
    def down(self, v):
        self._d._down = bool(v)

    @property
    def click(self):
        return self._d._click

    @click.setter
    def click(self, v):
        if v:
            self._d._click = True


class WebConsole:
    """Owns the single live Workstation, swaps its canvas for a CommandCanvas, and
    serves the recorded command stream + the static render assets. All console
    mutation (frame step + input) is serialized under one lock so the threaded HTTP
    server never steps the console from two requests at once (the console is
    single-threaded, like the device loop)."""

    def __init__(self, save_dir, fps=DEFAULT_FPS, cart=None, sys_size=None,
                 font_scale=1, windowed=False):
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
        self.canvas = web_view.CommandCanvas(sw, sh, font_scale=self.ws._effective_font_scale())
        # Per-WM-surface recording (Stage 9): the host web console IS the web view, so record one
        # stream per wm.draw_stack() surface (bar / app-content / player-viewport). The console's
        # frame loop marks each surface via canvas.begin_surface (a no-op unless this is set), so
        # step_frame() ships the browser a per-surface frame it composites (a second WM backend).
        self.canvas._rec.surfaces_on = True
        self._last_surfaces = None    # the last stepped frame's served per-surface streams

        # SERVE-TIME defspr/deflayer ship-once + gen -- the SAME shared logic the device runs,
        # against this canvas's recorder. served_frame() prepends any not-yet-shipped deflayer
        # (host sprites are self-contained, so no defspr) so each pushed frame stays self-contained.
        self._served = web_view.ServedState(self.canvas._rec)
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
        self.ws.wifi = host_app.make_host_wifi(host_app.moy_carts, self.ws.carts_root)
        # The Picotron-style windowed desktop (#73) over the web transport: install
        # the windowed WM AFTER the recorder swap above, so it anchors its root
        # context (and its window buffers, via root.new_layer -> RecordingLayer)
        # to the RECORDING canvas -- window contents then ship to the browser as
        # cached off-screen layers. Needs the distinct big canvas path.
        if windowed and self.ws._sys_canvas is not None:
            from runtime.wm_windowed import WindowedWM
            self.ws.wm = WindowedWM(self.ws)
            self.ws.open_desk()        # two worlds (#105): boot onto the DESK
        if cart:
            self._open_named_cart(cart, save_dir)
        self.driver = host_app.ConsoleDriver(self.ws)
        self._pointer_sink = _DriverPointerSink(self.driver)
        self._lock = threading.Lock()

    def _open_named_cart(self, cart_path, carts_dir):
        """Copy a named .moy into the store (if needed), select + open it -- the
        same skip-the-launcher path as tools/simulate_desktop._open_named_cart."""
        name = os.path.basename(os.path.normpath(cart_path))
        dst = os.path.join(carts_dir, name)
        if os.path.abspath(cart_path) != os.path.abspath(dst) and not os.path.exists(dst):
            import shutil
            shutil.copytree(cart_path, dst)
        self.ws.launcher.items = host_app.moy_carts.scan(self.ws.carts_root)
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
        """Replay a batch of browser events through the host ConsoleDriver.

        Routed through the SHARED runtime.web_view.apply_events -- the same
        decode path the device's web view runs (#42 Thread 3) -- instead of a
        second hand-rolled copy, so the button-name allowlist and the key/press
        rules (e.g. a Backspace also firing HOME outside text mode) can't drift
        between the host and device transports."""
        d = self.driver
        with self._lock:
            web_view.apply_events(
                events, d.input, self._pointer_sink,
                on_press=d.press, on_pan=d.pan, on_key=d.type_char,
                on_esc=d.escape, on_hold=d.hold)

    # -- output --------------------------------------------------------------
    def step_frame(self):
        """Advance the console one frame and return (commands, cart_title, audio_b64).
        `commands` is None when the console SKIPPED the redraw (#44's dirty gate: a
        static screen changed nothing) -- the WS loop then pushes nothing, so an idle
        desktop costs ~zero bandwidth (the VPN/remote case). The browser retains its
        last frame; a (re)connecting page gets a keyframe because its /assets fetch
        arms ws._dirty (see assets()), forcing the next step to record in full.

        The console draws into the CommandCanvas during driver.frame(); we take the
        recorded list afterward. We clear any stale commands first so a partially
        recorded frame (shouldn't happen under the lock) can never leak in. We also
        drain the per-cart audio backend's last rendered PCM block (signed-16 LE mono)
        and base64 it -- the browser plays the FINISHED samples, so there's no second
        synth in JS. Empty string when nothing played this frame (no cart / silence)."""
        with self._lock:
            self.canvas.take_commands()      # drop anything stale (defensive)
            self.driver.frame(self.dt)
            # Route through the shared serve path. Per-WM-surface (Stage 9): served_surfaces()
            # runs the ship-once deflayer bookkeeping ONCE over the flat stream and returns BOTH
            # the flat served frame (kept as the step_frame return, so this stays a 3-tuple for
            # the existing callers) AND the per-surface streams the browser composites, with the
            # ship-once prefix delivered as a leading "_defs" surface. Host sprites are
            # self-contained (pixels inline), so no defspr is prepended -- only deflayers.
            flat = self.canvas.take_commands()
            cart = self._cart_title()
            au = getattr(self.ws, "audio", None)
            pcm = au.take_pcm() if (au is not None and hasattr(au, "take_pcm")) else b""
            audio_b64 = base64.b64encode(pcm).decode("ascii") if (pcm and any(pcm)) else ""
            if not flat:
                # Redraw skipped (static screen): nothing recorded, nothing to serve.
                self._last_surfaces = None
                return None, cart, audio_b64
            surfaces = self.canvas.take_surfaces()      # per-WM-surface slices, or None
            if surfaces is not None:
                cmds, self._last_surfaces = self._served.served_surfaces(flat, surfaces)
            else:
                cmds = self._served.served_frame(flat)
                self._last_surfaces = None
        return cmds, cart, audio_b64

    def assets(self):
        """The static render assets the browser needs: palette + font + the open
        cart's sheet/tilemap. Re-fetched by the client when the cart changes.

        A page load / cart change clears the browser's off-screen-layer cache, so forget
        which layer streams we've shipped -- the next pushed frame re-ships every referenced
        layer's deflayer (the layer twin of refetching the sprite sheet, #54/#43). The fetch
        also arms ws._dirty: the console must record ONE full keyframe for this (re)connected
        page, since idle frames otherwise push nothing at all (see step_frame)."""
        with self._lock:
            self._served.reset()
            self.ws._dirty = True
            sheet = getattr(self.ws, "sheet", None)
            tilemap = getattr(self.ws, "tilemap", None)
            cart = self._cart_title()
            # Paint images (#63 Fold 4): decode the open cart's .moyimg text -> (w, h, index
            # bytes) so /assets ships them ONCE (browser-cached) and the per-frame stream
            # references each by name via ["imgref", ...] -- threaded through like the sheet.
            decoded = {}
            raw = getattr(self.ws, "images", None)
            if raw:
                for name, blob in raw.items():
                    dec = host_app._decode_moyimg(blob)
                    if dec is not None:
                        decoded[name] = dec
            # Shelf covers (#113): ship every live cover ONCE -- the card
            # draws then reference them by name instead of inlining ~40KB
            # of b64 per redraw (the measured shelf-drag payload eater).
            # Prebuild the WHOLE shelf first (owner 2026-07-23): every cover
            # the next draw needs finishes NOW, so this one payload is
            # complete and no per-thumbnail cache-miss refetch loop runs.
            pb = getattr(self.ws, "prebuild_covers", None)
            if pb is not None:
                pb()
            decoded.update(self.ws.cover_assets())
            # #42 Thread 3: the EFFECTIVE input hint -- the cart's manifest hint only
            # while it owns the keyboard (playing), never in the Editor (typing!).
            input_kinds = web_view.effective_input_kinds(self.ws)
            # The SHARED assets builder (host passes the MOY64 RGB palette directly; the device
            # passes its RGB565 LUT and the builder decodes -- detected by element type).
            return web_view.assets_payload(
                self.canvas.w, self.canvas.h, palette.MOY64, sheet, tilemap, cart,
                _audio.AudioEngine().rate, decoded or None, input_kinds)


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
        # Assets must never be cached -- the page refetches the same URL on a cart change.
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
        elif path == "/ws":
            self._serve_ws()
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    # -- the WebSocket live channel (the device speaks the SAME protocol) ------
    def _serve_ws(self):
        """Upgrade GET /ws to a WebSocket and drive it in THIS handler thread (the live
        channel). Read Sec-WebSocket-Key, write the 101 handshake straight to the socket, then
        HIJACK self.connection and run the frame-push / input-drain loop with the SHARED
        web_view.ws_* framing -- the same wire the device serves. One browser at a time is the
        expected case; two connections would each run their own loop and interleave safely
        (WebConsole.step_frame / apply_events already serialize on WebConsole._lock)."""
        upgrade = (self.headers.get("Upgrade") or "").lower()
        key = self.headers.get("Sec-WebSocket-Key")
        if "websocket" not in upgrade or not key:
            self._send(400, b"expected a websocket upgrade", "text/plain; charset=utf-8")
            return
        self.close_connection = True            # this conn is now the WS; never reuse for HTTP
        try:
            self.connection.sendall(web_view.ws_handshake_response(key))
        except OSError:
            return
        self._ws_loop(self.connection)

    def _ws_loop(self, sock):
        """Push a stepped frame ~once per 1/fps and drain inbound input, non-blocking, until the
        peer closes. A short socket timeout paces the reads (no busy-spin); sends get the larger
        WS_SEND_BUDGET so a WiFi hiccup doesn't sever the socket. A broken/half-open socket just
        ends the loop. Robust to a client that closes mid-stream."""
        interval = self.console.dt              # 1/fps push cadence
        buf = b""
        try:
            sock.settimeout(0.02)               # read timeout -> loop pacing; sends get this budget
        except OSError:
            pass
        next_push = time.monotonic()
        last_sent = 0.0                     # last actual push (for the idle keepalive)
        # Per-connection serve state (the SHARED policy object, web_view.
        # WsClientState -- the same one the device's moy_webserver uses): the
        # #76 SurfaceDelta mirroring THIS browser's SURF cache, plus the
        # first-frame KEYFRAME LATCH -- the /assets dirty-kick can be consumed
        # by a still-live OLD connection's loop (a page reload overlaps
        # sockets), leaving the new page black until an input dirties the
        # console again (owner report 2026-07-23), so the loop re-arms the
        # dirty gate every push tick until IT has served a full frame.
        client = web_view.WsClientState()
        delta = client.delta

        def _mark_dirty():
            with self.console._lock:
                self.console.ws._dirty = True
        while True:
            # 1. Inbound: drain queued input frames (input UP the socket).
            try:
                chunk = sock.recv(4096)
                if chunk == b"":
                    break                       # clean peer close
                buf += chunk
            except socket.timeout:
                pass                            # no data this tick -> normal (loop pacing)
            except OSError:
                break                           # broken socket -> drop
            drop = False
            while True:
                op, payload, consumed = web_view.ws_decode(buf)
                if op is None:
                    break                       # partial frame -> keep the bytes, retry next read
                if consumed <= 0:               # ws_decode protocol error (-1) -> drop
                    drop = True
                    break
                buf = buf[consumed:]
                if op == 0x1:                   # text: an {"events":[...]} input batch
                    self._ws_apply(payload)
                elif op == 0x8:                 # close
                    drop = True
                    break
                elif op == 0x9:                 # ping -> pong
                    try:
                        sock.sendall(web_view.ws_encode(payload, 0xA))
                    except OSError:
                        drop = True
                        break
                # 0xA pong / continuation / other control frames: ignored
            if drop:
                break
            # 2. Outbound: step the console ONE frame and PUSH it down at ~1/fps.
            now = time.monotonic()
            if now < next_push:
                continue
            next_push = now + interval
            client.arm_keyframe(_mark_dirty)
            try:
                cmds, cart, audio = self.console.step_frame()   # lock-guarded inside
            except Exception:  # noqa: BLE001 -- a frame error must not kill the socket loop
                # Log it (once-ish) so a draw regression is VISIBLE in journalctl instead
                # of silently freezing the browser at its last frame.
                import traceback
                sys.stderr.write("[web] step_frame error:\n" + traceback.format_exc())
                sys.stderr.flush()
                continue
            client.note_frame(cmds is not None)
            gen = self.console.canvas._rec.atlas_gen            # host recorder gen (self-contained)
            if cmds is None:
                # The console skipped the redraw (a static screen): push NOTHING --
                # this is what makes an idle desktop ~free over a VPN. A 1s empty
                # keepalive frame still flows so the page's socket never looks dead
                # (and carries any stray audio tail).
                if not audio and now - last_sent < 1.0:
                    continue
                cmds = []
            last_sent = now
            # Stage 9: ship the per-WM-surface streams (the browser composites them); step_frame
            # stashed them on the console. None -> a flat frame (nothing changes for that path).
            # #76: delta-encode them per connection -- unchanged surfaces ship as stubs; the
            # flat cmds are dropped in surfaces mode (the page ignores them) to halve the wire.
            surfaces = self.console._last_surfaces
            kinds = web_view.effective_input_kinds(self.console.ws)
            if surfaces is not None:
                payload = json.dumps(web_view.frame_payload(
                    [], cart, gen, audio=audio, surfaces=delta.encode(surfaces, gen=gen),
                    input_kinds=kinds))
            else:
                payload = json.dumps(web_view.frame_payload(
                    cmds, cart, gen, audio=audio, input_kinds=kinds))
            try:
                # Send under WS_SEND_BUDGET, not the 20ms read-pacing timeout. A
                # sendall that times out has already written PART of the frame (the
                # stream is corrupt), so a client still stalled past the budget is
                # dropped -- but a routine WiFi hiccup no longer severs the socket.
                sock.settimeout(WS_SEND_BUDGET)
                sock.sendall(web_view.ws_encode(payload))
            except OSError:
                break                           # a stalled/closed client -> drop, don't wait
            finally:
                try:
                    sock.settimeout(0.02)       # restore the read-pacing timeout
                except OSError:
                    pass

    def _ws_apply(self, payload):
        # One shared decode path (web_view.apply_ws_text) so the wire format can't
        # drift between the host web console and the device transport. Guard it: a bad
        # input event must never crash the WS thread (silent freeze) -- log + swallow.
        try:
            web_view.apply_ws_text(payload, self.console.apply_events)
        except Exception:  # noqa: BLE001
            import traceback
            sys.stderr.write("[web] input apply error:\n" + traceback.format_exc())
            sys.stderr.flush()


def make_server(console, host="0.0.0.0", port=DEFAULT_PORT, html=None):
    """Build a ThreadingHTTPServer bound to (host, port) serving `console`.

    Returns the server; call serve_forever() (or use it in a test with a thread).
    Pass port=0 for an ephemeral port (tests read server.server_address[1])."""
    handler = type("_BoundHandler", (_Handler,), {
        "console": console,
        # The SHARED browser page (web_view.PAGE_HTML): loads /assets over HTTP, then opens the
        # persistent WebSocket (/ws) live channel -- the SAME transport the host now serves.
        "html": (html if html is not None
                 else web_view.PAGE_HTML.encode("utf-8")),
    })
    return ThreadingHTTPServer((host, port), handler)


def _lan_url(port):
    """Best-effort http URL on the real LAN IP (so a phone/another PC can reach it),
    falling back to localhost. Reuses host_app._real_local_ip (no packet sent)."""
    ip = host_app._real_local_ip()
    return "http://%s:%d/" % (ip or "127.0.0.1", port)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Moybyte web console (#22, draw-command streaming)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default 0.0.0.0 = reachable on the LAN)")
    ap.add_argument("--cart", help="open a single .moy directly (skip the launcher)")
    ap.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS,
                    help="console step + WS push rate (dt = 1/fps per pushed frame)")
    # Two-domain seam (#39): the SYSTEM canvas size the desktop fills in the browser
    # (default 320x240 = today). The game is the centered fixed-aspect viewport.
    ap.add_argument("--size", default="320x240", metavar="WxH",
                    help="system canvas size (default 320x240)")
    ap.add_argument("--font-scale", type=int, default=1, choices=(1, 2, 3),
                    help="initial system-UI font scale 1/2/3 (system.json overrides)")
    ap.add_argument("--windowed", action="store_true",
                    help="Picotron-style windowed desktop WM (#73; needs a big --size)")
    args = ap.parse_args(argv)

    w, _, h = args.size.lower().partition("x")
    console = WebConsole(args.save_dir, fps=args.fps, cart=args.cart,
                         sys_size=(int(w), int(h)), font_scale=args.font_scale,
                         windowed=args.windowed)
    server = make_server(console, host=args.host, port=args.port)
    url = _lan_url(server.server_address[1])
    print("Moybyte web console (draw-command streaming) serving the live desktop at:")
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
