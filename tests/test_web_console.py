"""Tests for the host-first web console (#22 v1, tools/web_console.py).

The web console wraps the live host `Workstation` and serves its framebuffer as
PNG + accepts browser input as JSON -- a thin client over the real console. These
tests boot the actual stdlib HTTP server on 127.0.0.1 on an EPHEMERAL port (no
external network) and drive it with http.client, asserting:

  * GET /            -> the HTML page (a <canvas> + the poller JS).
  * GET /frame.png   -> a valid 320x240 PNG of the desktop framebuffer.
  * POST /input      -> a tap on a cart tile actually advances console state
                        (launcher -> desktop), proving input reaches the live
                        Workstation and is mapped exactly like the pygame sim.

Run headless: SDL_VIDEODRIVER=dummy is irrelevant here (no pygame window) but the
suite sets it globally; PIL decodes the PNG to verify size + that it isn't blank.
"""

import http.client
import io
import json
import os
import threading
import time

import pytest

from tools import web_console


@pytest.fixture()
def server(tmp_path):
    """A web console serving an isolated carts dir on an ephemeral localhost port.

    Yields (host_console, base_url) and tears the server down afterward. The
    carts dir is seeded with the system carts by build_workstation, so the
    launcher has real tiles to tap."""
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


def _post_input(host, port, events):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps({"events": events}).encode("utf-8")
        conn.request("POST", "/input", body,
                     {"Content-Type": "application/json"})
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
    # The page is the thin client: a scaled <canvas> + a poller that fetches
    # /frame.png and POSTs to /input. Assert the load-bearing pieces exist.
    assert "<canvas" in text
    assert "/frame.png" in text
    assert "/input" in text


def test_frame_png_is_valid_320x240(server):
    from PIL import Image

    _console, host, port = server
    status, ctype, body = _get(host, port, "/frame.png")
    assert status == 200
    assert ctype == "image/png"
    img = Image.open(io.BytesIO(body))
    img.load()
    assert img.size == (320, 240)        # the console's logical surface
    assert img.format == "PNG"
    # The launcher draws something -> the frame isn't a single flat color.
    assert len(set(img.convert("RGB").getdata())) > 1


def test_frame_advances_each_request(server):
    """Each GET /frame.png steps the console one frame -- so a state change made
    via POST /input shows up in a later frame. We capture the launcher, open a
    cart, and confirm the rendered framebuffer actually changes (the screen is
    live, not a frozen still)."""
    from PIL import Image

    console, host, port = server
    ws = console.ws
    _s, _c, before = _get(host, port, "/frame.png")
    # Tap-open tile 0 (launcher -> desktop), then grab a frame.
    _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    _get(host, port, "/frame.png")
    _post_input(host, port, [{"type": "up"}])
    deadline = time.time() + 3
    after = before
    while time.time() < deadline:
        _s, _c, after = _get(host, port, "/frame.png")
        if ws.screen == "desktop":
            break
    assert ws.screen == "desktop"
    # The launcher frame and the open-cart desktop frame are different pixels.
    assert (Image.open(io.BytesIO(before)).tobytes()
            != Image.open(io.BytesIO(after)).tobytes())


def test_post_input_tap_opens_a_cart(server):
    """A tap on a launcher tile (POST down+up at tile-0's coords), then a few
    frame steps, must advance the live Workstation from the launcher into the
    desktop -- proving browser input reaches the real console and is mapped like
    the pygame sim (mouse -> touch tap on release)."""
    console, host, port = server
    ws = console.ws
    assert ws.screen == "launcher"
    assert ws.launcher.items, "system carts should be seeded so a tile exists"

    # Tile 0 spans y in [36, 70); its center is ~(160, 52). A touch tap is a
    # `down` then `up` with no drag -> opens on release.
    status, _ = _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    assert status == 200
    # Step a frame so handle_pointer sees the press.
    _get(host, port, "/frame.png")
    status, _ = _post_input(host, port, [{"type": "up"}])
    assert status == 200

    # Step until the release is processed and the cart opens (open() is synchronous
    # but we step a handful of frames to be safe).
    deadline = time.time() + 3
    while ws.screen == "launcher" and time.time() < deadline:
        _get(host, port, "/frame.png")
    assert ws.screen == "desktop", "tapping a cart tile should open it (launcher -> desktop)"


def test_post_input_home_button_returns_to_launcher(server):
    """The browser key 'H' maps to the `home` button press; from the desktop it
    returns to the launcher -- a second proof that mapped key input drives state."""
    console, host, port = server
    ws = console.ws
    # Open a cart first (reuse the tap path).
    _post_input(host, port, [{"type": "down", "x": 160, "y": 52}])
    _get(host, port, "/frame.png")
    _post_input(host, port, [{"type": "up"}])
    deadline = time.time() + 3
    while ws.screen == "launcher" and time.time() < deadline:
        _get(host, port, "/frame.png")
    assert ws.screen == "desktop"

    # Now press HOME (the 'H' key -> {"type":"press","name":"home"}).
    _post_input(host, port, [{"type": "press", "name": "home"}])
    deadline = time.time() + 3
    while ws.screen != "launcher" and time.time() < deadline:
        _get(host, port, "/frame.png")
    assert ws.screen == "launcher", "the home button should return to the launcher"


def test_unknown_button_name_is_ignored(server):
    """A stray/unknown button name must not reach the console (the server filters
    to InputState.BUTTONS), so a malicious or buggy client can't wedge it."""
    console, host, port = server
    status, _ = _post_input(host, port, [{"type": "press", "name": "self_destruct"}])
    assert status == 200            # accepted, but dropped
    # The console is unharmed: still on the launcher, still framing.
    s, c, _ = _get(host, port, "/frame.png")
    assert s == 200 and c == "image/png"


def test_lan_url_falls_back_to_localhost(monkeypatch):
    """_lan_url uses the real LAN IP when available, else localhost -- never raises."""
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: None)
    assert web_console._lan_url(8080) == "http://127.0.0.1:8080/"
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: "10.0.0.5")
    assert web_console._lan_url(1234) == "http://10.0.0.5:1234/"
