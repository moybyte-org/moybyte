"""Tests for the web console (#22, cutoff #2: draw-command streaming).

The web is now a NEW CANVAS BACKEND, not a pixel viewer. The shared host
`Workstation` draws through an injected canvas; the web server swaps in a
`CommandCanvas` (tools/command_canvas.py) that RECORDS each draw call into a
per-frame, JSON-serializable command list, ships it via GET /frame, and a JS
replayer redraws it in the browser. These tests cover both halves:

  * THE FAITHFULNESS CROSS-CHECK (the key test): render the launcher and a running
    cart TWO ways -- (a) the normal rasterizing host `Canvas` (ws.canvas.buf), and
    (b) a Python reference replayer that executes the `CommandCanvas` command list
    onto a fresh host `Canvas` -- and assert the two buffers are PIXEL-IDENTICAL.
    This proves the command stream captures everything the renderer needs (the same
    way the map() agent validated its C kernel against the Python rasterizer).

  * THE SERVER (localhost only, ephemeral port, no external network):
      GET  /          -> the HTML page (a <canvas> + the replayer/poller JS).
      GET  /assets    -> JSON palette + petme128 font + the open cart's sheet.
      GET  /frame     -> JSON {"cmds":[...], "cart":...}; each request steps the
                         console one frame and returns the recorded draw calls.
      POST /input     -> a tap on a cart tile advances the live Workstation.
"""

import http.client
import json
import os
import threading
import time

import pytest

from runtime import host_app
from runtime.canvas import Canvas
from tools import web_console
from tools.command_canvas import CommandCanvas, replay_to_canvas

WIDTH, HEIGHT = host_app.WIDTH, host_app.HEIGHT


# ---------------------------------------------------------------------------
# Faithfulness cross-check: CommandCanvas stream replays pixel-identically.
# ---------------------------------------------------------------------------
#
# The check must compare the SAME frame two ways. Driving two independent consoles
# would diverge on non-deterministic per-frame state (the wall clock in the status
# strip, the FPS readout, time-based wallpaper/cart animation), so instead we run
# ONE console through a TeeCanvas: every draw call is BOTH rasterized into a real
# host Canvas AND recorded as a command, in a single pass over identical state.
# Replaying the recorded commands onto a fresh Canvas must then reproduce the
# rasterized buffer byte-for-byte -- proving the stream captures everything the
# renderer needs (the same approach the map() agent used to validate its C kernel).


class TeeCanvas:
    """A test canvas that forwards every draw call to a real rasterizing host Canvas
    AND to a CommandCanvas recorder, so both see identical calls from one frame. It
    exposes the full Canvas surface the console/carts use; `buf` aliases the
    rasterized buffer and `take_commands()` the recorder's list."""

    def __init__(self, width=WIDTH, height=HEIGHT):
        self.w = width
        self.h = height
        self.raster = Canvas(width, height)
        self.rec = CommandCanvas(width, height)

    @property
    def buf(self):
        return self.raster.buf

    def take_commands(self):
        return self.rec.take_commands()

    def cls(self, c=0):
        self.raster.cls(c); self.rec.cls(c)

    def pix(self, x, y, c=None):
        if c is None:
            return self.raster.pix(x, y)        # a read goes to the real buffer
        self.raster.pix(x, y, c); self.rec.pix(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        self.raster.line(x0, y0, x1, y1, c); self.rec.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c):
        self.raster.rect(x, y, w, h, c); self.rec.rect(x, y, w, h, c)

    def rectb(self, x, y, w, h, c):
        self.raster.rectb(x, y, w, h, c); self.rec.rectb(x, y, w, h, c)

    def circ(self, cx, cy, r, c):
        self.raster.circ(cx, cy, r, c); self.rec.circ(cx, cy, r, c)

    def circb(self, cx, cy, r, c):
        self.raster.circb(cx, cy, r, c); self.rec.circb(cx, cy, r, c)

    def spr(self, img, x, y, scale=1, flip=0):
        self.raster.spr(img, x, y, scale, flip); self.rec.spr(img, x, y, scale, flip)

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        self.raster.spr_batch(sheet, items, colorkey, scale)
        self.rec.spr_batch(sheet, items, colorkey, scale)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        self.raster.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)
        self.rec.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    # Draw state (#11): forward to BOTH so the recorded stream replays identically.
    def reset_state(self):
        self.raster.reset_state(); self.rec.reset_state()

    def camera(self, x=0, y=0):
        self.rec.camera(x, y)
        return self.raster.camera(x, y)

    def clip(self, x=None, y=None, w=None, h=None):
        self.raster.clip(x, y, w, h); self.rec.clip(x, y, w, h)

    def pal(self, c0=None, c1=None):
        self.raster.pal(c0, c1); self.rec.pal(c0, c1)

    def palt(self, c=None, on=None):
        self.raster.palt(c, on); self.rec.palt(c, on)

    def print(self, s, x, y, c, scale=1):
        self.raster.print(s, x, y, c, scale); self.rec.print(s, x, y, c, scale)

    def to_rgb888(self):
        return self.raster.to_rgb888()


def _build_tee(save_dir):
    """A Workstation over a seeded carts dir, drawing through a TeeCanvas (single
    source of truth for both the raster + recorded views). Returns (ws, driver, tee)."""
    ws = host_app.build_workstation(save_dir)
    tee = TeeCanvas(WIDTH, HEIGHT)
    ws.canvas = tee                       # the decided swap: reassign ws.canvas
    return ws, host_app.ConsoleDriver(ws), tee


def _assert_frame_identical(ws, drv, tee, dt=1.0 / 30, label=""):
    """Step the console one frame, then assert the rasterized buffer equals a fresh
    replay of that same frame's recorded command list, byte for byte."""
    drv.frame(dt)
    raster = bytes(tee.buf)
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(tee.take_commands(), cv)
    replayed = bytes(cv.buf)
    assert len(raster) == len(replayed) == WIDTH * HEIGHT
    if raster != replayed:
        diff = sum(1 for a, b in zip(raster, replayed) if a != b)
        first = next(i for i, (a, b) in enumerate(zip(raster, replayed)) if a != b)
        pytest.fail("%s: command replay differs from rasterizer in %d/%d px "
                    "(first at index %d: raster=%d replay=%d)"
                    % (label, diff, len(raster), first,
                       raster[first], replayed[first]))


def _open_tile0(ws, drv):
    """Tap-open tile 0 (launcher -> desktop) on a TeeCanvas-driven console."""
    drv.touch(160, 52)
    drv.frame(1.0 / 30)
    drv.touch_up()
    for _ in range(8):
        drv.frame(1.0 / 30)
        if ws.screen == "desktop":
            return
    raise AssertionError("tile 0 should open into the desktop")


def test_crosscheck_launcher_is_pixel_identical(tmp_path):
    """The DESKTOP/launcher home -- wallpaper + cart icon grid (spr) + status strip +
    dock + text -- recorded as commands and replayed must equal the rasterized frame.
    Exercises cls/rect/rectb/spr/print on the real launcher."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    assert ws.screen == "launcher"
    for n in range(3):
        _assert_frame_identical(ws, drv, tee, label="launcher#%d" % n)


def test_crosscheck_running_cart_is_pixel_identical(tmp_path):
    """Open a cart and run it: its per-frame draw (cls/spr/print/rect/circ/line/map,
    whatever it calls) recorded + replayed must match the rasterized cart frame."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    assert ws.launcher.items, "system carts should be seeded"
    _open_tile0(ws, drv)
    # Run a stretch of frames (motion + draw variety) and check each.
    for n in range(12):
        _assert_frame_identical(ws, drv, tee, label="cart#%d" % n)


def test_crosscheck_named_cart_with_map(tmp_path):
    """A cart that uses map()/spr (battle_city has a tilemap) round-trips identically
    -- proves map() expansion to per-cell spr commands replays pixel-perfect."""
    cart_path = os.path.join(host_app.ROOT, "system_carts", "battle_city.kcart")
    if not os.path.isdir(cart_path):
        pytest.skip("battle_city.kcart not present")
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if os.path.basename(c["path"]) == "battle_city.kcart":
            ws.launcher.sel = i
            break
    else:
        pytest.skip("battle_city.kcart not in the seeded store")
    ws.open()
    assert ws.screen == "desktop"
    for n in range(10):
        _assert_frame_identical(ws, drv, tee, label="map#%d" % n)


def test_take_commands_clears_per_frame(tmp_path):
    """take_commands() returns the frame's list AND resets it, so commands never
    accumulate across frames (the wire stays a few calls/frame)."""
    cv = CommandCanvas(WIDTH, HEIGHT)
    cv.cls(1)
    cv.rect(0, 0, 10, 10, 2)
    first = cv.take_commands()
    assert first and first[0] == ["cls", 1]
    assert cv.take_commands() == []          # cleared
    cv.print("hi", 4, 4, 7)
    second = cv.take_commands()
    assert len(second) == 1 and second[0][0] == "print"


def test_command_canvas_api_matches_canvas():
    """CommandCanvas must expose the same public draw surface as the host Canvas, so
    it's a drop-in ws.canvas. Assert every drawing method the console/carts call
    exists and is callable."""
    cv = CommandCanvas(WIDTH, HEIGHT)
    for name in ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
                 "spr", "map", "print", "to_rgb888"):
        assert callable(getattr(cv, name)), name
    assert cv.w == WIDTH and cv.h == HEIGHT
    # pix as a read returns 0 (a command stream has no buffer to read back).
    assert cv.pix(5, 5) == 0


# ---------------------------------------------------------------------------
# The server (localhost only, ephemeral port).
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path):
    """A web console serving an isolated carts dir on an ephemeral localhost port.
    Yields (host_console, host, port) and tears the server down afterward."""
    save_dir = str(tmp_path / "carts")
    console = web_console.WebConsole(save_dir, fps=30)
    srv = web_console.make_server(console, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield console, "127.0.0.1", port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def _get(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def _get_json(host, port, path):
    status, ctype, body = _get(host, port, path)
    return status, ctype, json.loads(body.decode("utf-8"))


def _post_input(host, port, events):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps({"events": events}).encode("utf-8")
        conn.request("POST", "/input", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_index_serves_html_page(server):
    _console, host, port = server
    status, ctype, body = _get(host, port, "/")
    assert status == 200
    assert "text/html" in ctype
    text = body.decode("utf-8")
    # The page is the replayer thin client: a scaled <canvas>, fetches /assets and
    # /frame, replays commands, and POSTs to /input. Assert the load-bearing pieces.
    assert "<canvas" in text
    assert "/frame" in text
    assert "/assets" in text
    assert "/input" in text


def test_assets_returns_palette_font_and_sheet(server):
    _console, host, port = server
    status, ctype, obj = _get_json(host, port, "/assets")
    assert status == 200
    assert "application/json" in ctype
    assert obj["w"] == WIDTH and obj["h"] == HEIGHT
    # KID64 palette: 64 [r,g,b] triples.
    assert len(obj["palette"]) == 64
    assert obj["palette"][7] == [0xFF, 0xF1, 0xE8]      # KID64 index 7 = white
    # petme128 font: glyphs as 8 column-bytes each, ASCII 0x20..0x7f.
    font = obj["font"]
    assert font["first"] == 0x20 and font["w"] == 8 and font["h"] == 8
    assert len(font["glyphs"]) >= 0x60
    assert all(len(g) == 8 for g in font["glyphs"])
    # On the launcher there's no open cart, so sheet/tilemap are null; cart is null.
    assert obj["cart"] is None


def test_assets_includes_open_cart_sheet(server):
    """After opening a cart, /assets carries that cart's sprite sheet + tilemap and
    the cart title (so the client refetches on a cart change)."""
    console, host, port = server
    # Open tile 0.
    _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    _get(host, port, "/frame")
    _post_input(host, port, [{"type": "up"}])
    deadline = time.time() + 3
    while console.ws.screen != "desktop" and time.time() < deadline:
        _get(host, port, "/frame")
    assert console.ws.screen == "desktop"
    status, _ctype, obj = _get_json(host, port, "/assets")
    assert status == 200
    assert obj["cart"] is not None
    assert obj["sheet"] is not None
    sheet = obj["sheet"]
    assert sheet["tile"] == 8
    assert len(sheet["pix"]) == sheet["w"] * sheet["h"]


def test_frame_returns_command_list(server):
    """GET /frame steps the console and returns a JSON command list (the recorded
    draw calls), not pixels. The launcher draws something, so the list is non-empty
    and starts with a clear/fill."""
    _console, host, port = server
    status, ctype, obj = _get_json(host, port, "/frame")
    assert status == 200
    assert "application/json" in ctype
    cmds = obj["cmds"]
    assert isinstance(cmds, list) and len(cmds) > 0
    # Every command is a list whose head is a known op name.
    ops = {"cls", "pix", "line", "rect", "rectb", "circ", "circb", "spr", "print",
           "reset_state", "camera", "clip", "pal", "palt"}    # +draw state (#11)
    assert all(isinstance(c, list) and c[0] in ops for c in cmds)
    # The frame opens by clearing/painting the wallpaper backdrop.
    assert any(c[0] in ("cls", "rect") for c in cmds)


def test_frame_is_replayable_to_valid_pixels(server):
    """The streamed command list must replay (via the Python reference replayer) to
    a non-blank 320x240 frame -- the same render path the browser performs in JS."""
    _console, host, port = server
    _s, _c, obj = _get_json(host, port, "/frame")
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(obj["cmds"], cv)
    assert len(cv.buf) == WIDTH * HEIGHT
    assert len(set(cv.buf)) > 1, "the launcher frame should not be a single flat color"


def test_frame_advances_each_request(server):
    """Each GET /frame steps the console one frame, so a state change made via POST
    /input shows up in a later frame's commands (the screen is live)."""
    console, host, port = server
    ws = console.ws
    _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    _get(host, port, "/frame")
    _post_input(host, port, [{"type": "up"}])
    deadline = time.time() + 3
    while time.time() < deadline:
        _get(host, port, "/frame")
        if ws.screen == "desktop":
            break
    assert ws.screen == "desktop"


def test_post_input_tap_opens_a_cart(server):
    """A tap on a launcher tile advances the live Workstation from launcher into the
    desktop -- proving browser input reaches the real console (mapped like pygame)."""
    console, host, port = server
    ws = console.ws
    assert ws.screen == "launcher"
    assert ws.launcher.items, "system carts should be seeded so a tile exists"

    status, _ = _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    assert status == 200
    _get(host, port, "/frame")
    status, _ = _post_input(host, port, [{"type": "up"}])
    assert status == 200
    deadline = time.time() + 3
    while ws.screen == "launcher" and time.time() < deadline:
        _get(host, port, "/frame")
    assert ws.screen == "desktop"


def test_post_input_home_button_returns_to_launcher(server):
    """The browser key 'H' maps to the `home` button press; from the desktop it
    returns to the launcher -- a second proof that mapped key input drives state."""
    console, host, port = server
    ws = console.ws
    _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    _get(host, port, "/frame")
    _post_input(host, port, [{"type": "up"}])
    deadline = time.time() + 3
    while ws.screen == "launcher" and time.time() < deadline:
        _get(host, port, "/frame")
    assert ws.screen == "desktop"

    _post_input(host, port, [{"type": "press", "name": "home"}])
    deadline = time.time() + 3
    while ws.screen != "launcher" and time.time() < deadline:
        _get(host, port, "/frame")
    assert ws.screen == "launcher", "the home button should return to the launcher"


def test_unknown_button_name_is_ignored(server):
    """A stray/unknown button name must not reach the console, so a buggy client
    can't wedge it. The console stays on the launcher and keeps framing."""
    console, host, port = server
    status, _ = _post_input(host, port, [{"type": "press", "name": "self_destruct"}])
    assert status == 200
    s, c, obj = _get_json(host, port, "/frame")
    assert s == 200 and isinstance(obj["cmds"], list)


def test_lan_url_falls_back_to_localhost(monkeypatch):
    """_lan_url uses the real LAN IP when available, else localhost -- never raises."""
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: None)
    assert web_console._lan_url(8080) == "http://127.0.0.1:8080/"
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: "10.0.0.5")
    assert web_console._lan_url(1234) == "http://10.0.0.5:1234/"
