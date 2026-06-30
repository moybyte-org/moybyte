"""Host-side tests for the DEVICE web view (#41/#22): the device serves its running
console to a phone/desktop browser over WiFi via the SAME draw-command protocol the
host web console uses (tools/web_console.py + web_console.html), so the same browser
page renders the device frames.

The device module firmware/.../modules/kc_webserver.py is written MicroPython-first but
imports + runs on CPython (it has ujson/usocket/utime fallbacks), so everything testable
OFF-device is exercised here:

  * THE RECORDER + TEE: a TeeCanvas forwards every draw call to the real device canvas
    AND, only while the recorder is ENABLED, records a JSON-serializable draw-command
    list in the EXACT format of tools/command_canvas.CommandCanvas. Disabled, it's a
    pure pass-through (the zero-cost normal path).
  * THE FAITHFULNESS CROSS-CHECK: replay the recorded commands onto a host rasterizing
    Canvas (the Python twin of the browser's JS replayer) and assert it reproduces the
    same pixels the (raster-equivalent) draws would -- proving the stream is complete.
  * THE PROTOCOL: /assets (palette + petme128 font + sheet/tilemap), /frame (the command
    list + cart title), and /input event parsing all serialize to the host's shape.
  * THE SERVER: the HTTP request parser + response builder + non-blocking socket server
    over a real localhost socket (ephemeral port).

The MicroPython socket layer + WiFi<->LCD coexistence are NOT exercisable in CI; those
are called out in the device-verification checklist, not tested here.
"""

import http.client
import json
import os
import sys
import threading
import time

import pytest

# Import the device module straight off the firmware modules tree (it runs on CPython).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython", "modules")
if MODULES not in sys.path:
    sys.path.insert(0, MODULES)

import kc_webserver as web  # noqa: E402

from runtime import font as _font  # noqa: E402
from runtime import palette as _pal  # noqa: E402
from runtime.canvas import Canvas, Image  # noqa: E402
from runtime.editors import SpriteSheet  # noqa: E402
from tools.command_canvas import replay_to_canvas  # noqa: E402

WIDTH, HEIGHT = 320, 240

# The device's canonical RGB565 KID64 LUT (a copy of kid_runtime.PAL565 -- the host
# can't import the device backend, which pulls in framebuf/machine).
PAL565 = (
    0x0000, 0x194A, 0x792A, 0x042A, 0xAA86, 0x5AA9, 0xC618, 0xFF9D,
    0xF809, 0xFD00, 0xFF64, 0x0726, 0x2D7F, 0x83B3, 0xFBB5, 0xFE75,
)


# ---------------------------------------------------------------------------
# A minimal stand-in for the device DeviceCanvas: it records nothing, it just
# satisfies the TeeCanvas's delegation (so the Tee's "forward to the real canvas"
# half is exercised) and lets us prove the recorder half independently. We don't
# rasterize here -- the faithfulness check rasterizes the RECORDED stream.
# ---------------------------------------------------------------------------


class _FakeDeviceCanvas:
    """Counts forwarded calls so a test can prove the Tee always forwards to the panel
    canvas regardless of the recorder gate. Exposes w/h + a no-op draw surface + the
    layer/sync hooks the console reaches through __getattr__."""

    def __init__(self, w=WIDTH, h=HEIGHT):
        self.w = w
        self.h = h
        self.calls = 0
        self.buf = bytearray(w * h)        # so a 'pix' read returns something

    def _bump(self, *_a, **_k):
        self.calls += 1

    cls = pix = line = rect = rectb = circ = circb = spr = print = _bump
    spr_batch = map = reset_state = camera = clip = pal = palt = _bump

    def sync_back(self):
        self.calls += 1


def _build_tee():
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    dev = _FakeDeviceCanvas()
    tee = web.TeeCanvas(dev, rec)
    return tee, rec, dev


# ---------------------------------------------------------------------------
# The recorder + Tee gate.
# ---------------------------------------------------------------------------


def test_recorder_disabled_is_a_pure_passthrough():
    """With recording DISABLED (no browser), the Tee forwards every call to the real
    device canvas and records NOTHING -- the zero-cost normal path."""
    tee, rec, dev = _build_tee()
    assert rec.enabled is False
    tee.cls(1)
    tee.rect(0, 0, 10, 10, 2)
    tee.print("hi", 4, 4, 7)
    assert dev.calls == 3, "every draw must still reach the panel canvas"
    rec.begin()
    rec.commit()
    assert rec.frame() == [], "nothing recorded while disabled"


def test_recorder_enabled_tees_to_both():
    """Enabled, the Tee forwards to the device canvas AND records commands."""
    tee, rec, dev = _build_tee()
    rec.enabled = True
    rec.begin()
    tee.cls(3)
    tee.rect(1, 2, 8, 9, 4)
    tee.rectb(0, 0, 5, 5, 5)
    tee.circ(10, 10, 4, 6)
    tee.circb(20, 20, 3, 7)
    tee.line(0, 0, 9, 9, 8)
    tee.pix(2, 2, 9)
    tee.print("yo", 1, 1, 10)
    rec.commit()
    assert dev.calls == 8, "all eight draws reach the panel canvas"
    ops = [c[0] for c in rec.frame()]
    assert ops == ["cls", "rect", "rectb", "circ", "circb", "line", "pix", "print"]


def test_recorder_command_format_matches_command_canvas():
    """The device recorder must emit the EXACT command shapes tools/command_canvas
    does, so the same web_console.html replays both. Check the literal tuples."""
    tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    tee.cls(2)
    tee.rect(3, 4, 5, 6, 7)
    tee.rectb(1, 1, 2, 2, 3)
    tee.line(0, 0, 4, 4, 9)
    tee.circ(8, 8, 2, 10)
    tee.circb(9, 9, 1, 11)
    tee.print("AB", 5, 6, 12)
    tee.camera(2, 3)
    tee.clip(0, 0, 10, 10)
    tee.pal(1, 2)
    tee.palt(3, True)
    tee.reset_state()
    rec.commit()
    cmds = rec.frame()
    assert cmds == [
        ["cls", 2],
        ["rect", 3, 4, 5, 6, 7],
        ["rectb", 1, 1, 2, 2, 3],
        ["line", 0, 0, 4, 4, 9],
        ["circ", 8, 8, 2, 10],
        ["circb", 9, 9, 1, 11],
        ["print", "AB", 5, 6, 12],
        ["camera", 2, 3],
        ["clip", 0, 0, 10, 10],
        ["pal", 1, 2],
        ["palt", 3, 1],
        ["reset_state"],
    ]


def test_recorder_spr_carries_raw_pixels_and_flip():
    """spr records [x,y,scale,w,h,t,pix,flip] with the raw indexed pixels, so the
    stream is self-contained (the browser needs no sheet lookup to be correct)."""
    tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    img = Image(2, 2, [1, 2, 3, 4], transparent=0)
    tee.spr(img, 5, 6, 2, 1)
    rec.commit()
    cmd = rec.frame()[0]
    assert cmd == ["spr", 5, 6, 2, 2, 2, 0, [1, 2, 3, 4], 1]


def test_recorder_begin_commit_swap():
    """begin() starts a fresh frame; commit() publishes it; a partial frame is dropped
    by the next begin(). frame() always returns the last COMMITTED frame."""
    _tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    rec.cls(1)
    rec.commit()
    assert rec.frame() == [["cls", 1]]
    rec.begin()                 # start a new frame
    rec.rect(0, 0, 1, 1, 2)
    # not committed yet -> frame() still the previous committed one
    assert rec.frame() == [["cls", 1]]
    rec.commit()
    assert rec.frame() == [["rect", 0, 0, 1, 1, 2]]


def test_tee_delegates_unknown_attrs_to_device_canvas():
    """A pixel READ (pix with two args) and framebuffer-ish attrs go straight to the
    real device canvas via __getattr__ -- the Tee never shadows reads."""
    tee, _rec, dev = _build_tee()
    assert tee.w == WIDTH and tee.h == HEIGHT
    tee.pix(0, 0)               # a read forwards (no recording)
    assert dev.calls == 1
    tee.sync_back()             # reached through the Tee's wrapped surface
    assert dev.calls == 2


def test_tee_map_expands_to_spr_commands():
    """map() records one spr command per non-empty cell (mirrors CommandCanvas.map), so
    the browser needs no map op and replays pixel-identically."""
    tee, rec, _dev = _build_tee()
    sheet = SpriteSheet(2, 1)               # 2 tiles of 8x8
    # paint tile 1 a solid color so it's non-empty; tile 0 stays blank (0).
    for y in range(8):
        for x in range(8, 16):
            sheet.pset(x, y, 5)

    class _TM:
        w = 2
        h = 1

        def mget(self, x, y):
            return 1 if x == 1 and y == 0 else 0

    rec.enabled = True
    rec.begin()
    tee.map(_TM(), sheet, 0, 0, 2, 1, 0, 0, -1, 1)
    rec.commit()
    cmds = rec.frame()
    # Both cells are non-empty (colorkey -1 => tile 0 isn't transparent), so two sprs.
    sprs = [c for c in cmds if c[0] == "spr"]
    assert len(sprs) == 2
    assert sprs[1][1] == 8 and sprs[1][2] == 0      # cell (1,0) at x=8


# ---------------------------------------------------------------------------
# The faithfulness cross-check: the recorded stream replays to valid pixels via the
# host rasterizer (the Python twin of the browser's JS replayer).
# ---------------------------------------------------------------------------


def test_recorded_stream_replays_to_pixels():
    """Record a varied frame through the Tee, then replay it onto a host Canvas (the
    same path the browser performs in JS) and assert it produced a non-blank frame --
    proving the device's command stream is complete + replayable."""
    tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    tee.cls(1)
    tee.rect(10, 10, 40, 30, 8)
    tee.rectb(60, 10, 40, 30, 7)
    tee.circ(160, 120, 20, 11)
    tee.line(0, 0, 319, 239, 6)
    img = Image(3, 3, [9] * 9, transparent=-1)
    tee.spr(img, 100, 100, 4)
    tee.print("HELLO", 8, 200, 7)
    rec.commit()
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(rec.frame(), cv)
    assert len(cv.buf) == WIDTH * HEIGHT
    assert len(set(cv.buf)) > 1, "the recorded frame should not replay to a flat color"


def test_tee_over_real_canvas_is_pixel_identical_to_its_stream():
    """The strongest cross-check (the device's own TeeCanvas + DrawRecorder): drive a
    varied frame through the device TeeCanvas whose "real" side is a host rasterizing
    Canvas (the device panel's stand-in -- same draw surface), then replay the recorded
    command stream onto a FRESH Canvas and assert the two buffers are PIXEL-IDENTICAL.
    This proves the device's recorded stream reproduces exactly what the panel drew --
    the same approach the host web console test (TeeCanvas) and the map() C kernel used.
    Sprites + transparency + scaling + flip are exercised so the stream's self-contained
    pixels are validated, not just primitives."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)        # the "real" side IS a real rasterizer
    rec.enabled = True
    rec.begin()
    tee.cls(1)
    tee.rect(10, 10, 60, 40, 8)
    tee.rectb(80, 10, 60, 40, 7)
    tee.line(0, 239, 319, 0, 6)
    tee.circ(160, 120, 25, 11)
    tee.circb(220, 120, 18, 9)
    tee.pix(5, 5, 10)
    spr = Image(4, 4, [0, 8, 8, 0, 8, 7, 7, 8, 8, 7, 7, 8, 0, 8, 8, 0], transparent=0)
    tee.spr(spr, 100, 100, 3)               # scaled, with a transparent index
    tee.spr(spr, 150, 100, 2, 1)            # h-flipped
    tee.print("DEVICE WEB", 8, 220, 7)
    rec.commit()
    replayed = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(rec.frame(), replayed)
    assert bytes(raster.buf) == bytes(replayed.buf), "the recorded stream must reproduce the rasterized panel"


def test_recorded_print_replays_pixel_identically_to_petme128():
    """The device sends the petme128 glyphs in /assets (baked from the SAME font.py),
    so a recorded `print` replays to the EXACT pixels the host font.draw produces."""
    tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    tee.cls(0)
    tee.print("Kid", 20, 20, 7)
    rec.commit()
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(rec.frame(), cv)
    # Rasterize the same text directly with the host font onto a reference buffer.
    ref = Canvas(WIDTH, HEIGHT)
    ref.cls(0)
    ref.print("Kid", 20, 20, 7)
    assert bytes(cv.buf) == bytes(ref.buf)


# ---------------------------------------------------------------------------
# The protocol payloads (shape parity with tools/web_console.py).
# ---------------------------------------------------------------------------


def test_assets_payload_shape_matches_host():
    """/assets carries w/h + a 64-entry palette + the petme128 font + sheet/tilemap +
    cart title + audio_rate -- the SAME keys tools/web_console.WebConsole.assets uses."""
    a = web.assets_payload(WIDTH, HEIGHT, PAL565, None, None, None, audio_rate=8000)
    assert a["w"] == WIDTH and a["h"] == HEIGHT
    assert len(a["palette"]) == len(PAL565)
    f = a["font"]
    assert f["first"] == _font.FIRST and f["w"] == _font.WIDTH and f["h"] == _font.HEIGHT
    assert len(f["glyphs"]) == len(_font._FONT) // _font.WIDTH
    assert all(len(g) == _font.WIDTH for g in f["glyphs"])
    assert a["sheet"] is None and a["tilemap"] is None and a["cart"] is None
    assert a["audio_rate"] == 8000
    # The whole payload must be JSON-serializable (it goes over the wire).
    json.dumps(a)


def test_assets_palette_decodes_close_to_kid64():
    """The device sends its REAL panel colours (RGB565-decoded), which match KID64 to
    within 565 quantization -- so the browser shows what the panel shows."""
    pal = web.palette_rgb(PAL565)
    assert pal[0] == [0, 0, 0]
    for i in range(len(PAL565)):
        for ch in range(3):
            assert abs(pal[i][ch] - _pal.KID64[i][ch]) <= 8, (i, ch)


def test_assets_includes_sheet_when_a_cart_is_open():
    """With a sheet, /assets carries its cols/rows/tile + flat pixels (host shape)."""
    sheet = SpriteSheet(2, 2)
    a = web.assets_payload(WIDTH, HEIGHT, PAL565, sheet, None, "Star Catcher")
    s = a["sheet"]
    assert s["cols"] == 2 and s["rows"] == 2 and s["tile"] == sheet.TILE
    assert len(s["pix"]) == s["w"] * s["h"]
    assert a["cart"] == "Star Catcher"


def test_frame_payload_shape():
    """/frame is {cmds, cart, audio} -- matches the host minus PCM (the device web view
    doesn't stream audio)."""
    cmds = [["cls", 1], ["rect", 0, 0, 10, 10, 2]]
    p = web.frame_payload(cmds, "Pong")
    assert p["cmds"] == cmds and p["cart"] == "Pong" and p["audio"] == ""
    json.dumps(p)


# ---------------------------------------------------------------------------
# Input event parsing (apply_events): browser events -> InputState/Pointer/hooks.
# ---------------------------------------------------------------------------


class _FakeInput:
    def __init__(self):
        self.held = set()
        self.last_key = 0

    def set_button(self, name, on):
        if on:
            self.held.add(name)
        else:
            self.held.discard(name)


class _FakePointer:
    def __init__(self):
        self.x = 0
        self.y = 0
        self.down = False
        self.click = False

    def place(self, x, y):
        self.x = int(x)
        self.y = int(y)


def test_apply_events_pointer_tap_and_drag():
    inp, ptr = _FakeInput(), _FakePointer()
    web.apply_events([{"type": "down", "x": 30, "y": 40}], inp, ptr)
    assert (ptr.x, ptr.y, ptr.down, ptr.click) == (30, 40, True, True)
    web.apply_events([{"type": "move", "x": 50, "y": 60}], inp, ptr)
    assert (ptr.x, ptr.y, ptr.down) == (50, 60, True)
    web.apply_events([{"type": "up"}], inp, ptr)
    assert ptr.down is False


def test_apply_events_hold_button():
    inp, ptr = _FakeInput(), _FakePointer()
    web.apply_events([{"type": "hold", "name": "left", "down": True}], inp, ptr)
    assert "left" in inp.held
    web.apply_events([{"type": "hold", "name": "left", "down": False}], inp, ptr)
    assert "left" not in inp.held


def test_apply_events_unknown_button_ignored():
    """A stray button name must never reach the console (a buggy client can't wedge it)."""
    inp, ptr = _FakeInput(), _FakePointer()
    web.apply_events([{"type": "hold", "name": "self_destruct", "down": True}], inp, ptr)
    assert inp.held == set()


def test_apply_events_press_pan_key_esc_hooks():
    inp, ptr = _FakeInput(), _FakePointer()
    fired = {"press": [], "pan": [], "key": [], "esc": 0}
    web.apply_events(
        [
            {"type": "press", "name": "run"},
            {"type": "press", "name": "nope"},          # filtered out
            {"type": "pan", "dx": 1, "dy": -1},
            {"type": "key", "code": 0x41},
            {"type": "key", "code": 999},                # out of range -> ignored
            {"type": "esc"},
        ],
        inp, ptr,
        on_press=lambda n: fired["press"].append(n),
        on_pan=lambda dx, dy: fired["pan"].append((dx, dy)),
        on_key=lambda c: fired["key"].append(c),
        on_esc=lambda: fired.__setitem__("esc", fired["esc"] + 1),
    )
    assert fired["press"] == ["run"]
    assert fired["pan"] == [(1, -1)]
    assert fired["key"] == [0x41]
    assert fired["esc"] == 1


def test_apply_events_malformed_event_is_skipped():
    """A malformed event must be skipped, not raise -- the whole batch still applies."""
    inp, ptr = _FakeInput(), _FakePointer()
    web.apply_events(
        [{"type": "down"}, "garbage", {"type": "hold", "name": "a", "down": True}],
        inp, ptr,
    )
    assert "a" in inp.held               # the good event after the garbage still applied


# ---------------------------------------------------------------------------
# HTTP request parsing + response building.
# ---------------------------------------------------------------------------


def test_parse_request_get_strips_query():
    m, p, clen, end = web.parse_request(b"GET /frame?t=1 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert m == "GET" and p == "/frame" and clen == 0 and end > 0


def test_parse_request_post_reads_content_length():
    raw = b"POST /input HTTP/1.1\r\nContent-Length: 11\r\n\r\nhello world"
    m, p, clen, end = web.parse_request(raw)
    assert m == "POST" and p == "/input" and clen == 11
    assert raw[end:end + clen] == b"hello world"


def test_parse_request_incomplete_headers():
    m, p, clen, end = web.parse_request(b"GET /frame HTTP/1.1\r\nHost: x")
    assert end == -1 and m is None


def test_http_response_well_formed():
    r = web.http_response(200, '{"ok":true}')
    head, _, body = r.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert b"Content-Type: application/json" in head
    assert b"Content-Length: 11" in head
    assert b"Cache-Control: no-store" in head
    assert body == b'{"ok":true}'


# ---------------------------------------------------------------------------
# The non-blocking socket server over a real localhost socket.
# ---------------------------------------------------------------------------


class _FakeProvider:
    """A provider feeding the server fixed assets/frame + capturing applied input."""

    def __init__(self):
        self.applied = []
        self._cmds = [["cls", 1], ["rect", 10, 10, 40, 30, 5],
                      ["circ", 160, 120, 20, 8], ["print", "HI", 8, 8, 7]]

    def assets(self):
        return web.assets_payload(WIDTH, HEIGHT, PAL565, None, None, "Demo", 8000)

    def frame(self):
        return (self._cmds, "Demo")

    def apply(self, events):
        self.applied.extend(events)


@pytest.fixture()
def server():
    """A WebServer on an ephemeral localhost port, polled by a background thread so the
    cooperative poll() model is exercised against a real socket."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    prov = _FakeProvider()
    srv = web.WebServer(rec, prov, port=0)
    assert srv.start("127.0.0.1") is True
    port = srv.sock.getsockname()[1]
    stop = threading.Event()

    def _pump():
        while not stop.is_set():
            srv.begin_frame()
            srv.commit_frame()
            srv.poll()
            time.sleep(0.005)

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    try:
        yield srv, prov, "127.0.0.1", port
    finally:
        stop.set()
        t.join(timeout=2)
        srv.stop()


def _get(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        conn.close()


def _post(host, port, path, obj):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps(obj).encode("utf-8")
        conn.request("POST", path, body, {"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_server_serves_index_html(server):
    _srv, _prov, host, port = server
    status, ctype, body = _get(host, port, "/")
    assert status == 200 and "text/html" in ctype
    text = body.decode("utf-8")
    # The embedded page is the replayer thin client.
    assert "<canvas" in text
    assert "/frame" in text and "/assets" in text and "/input" in text


def test_server_serves_assets(server):
    _srv, _prov, host, port = server
    status, ctype, body = _get(host, port, "/assets")
    assert status == 200 and "application/json" in ctype
    a = json.loads(body)
    assert a["w"] == WIDTH and len(a["palette"]) == len(PAL565)
    assert a["font"]["w"] == 8 and a["cart"] == "Demo"


def test_server_serves_frame_commands(server):
    _srv, _prov, host, port = server
    status, _ctype, body = _get(host, port, "/frame")
    assert status == 200
    f = json.loads(body)
    assert f["cmds"][0] == ["cls", 1] and f["cart"] == "Demo"


def test_server_frame_replays_to_pixels(server):
    """The streamed command list replays (via the Python reference replayer, the JS
    twin) to a non-blank 320x240 frame -- end-to-end over the wire."""
    _srv, _prov, host, port = server
    _s, _c, body = _get(host, port, "/frame")
    cmds = json.loads(body)["cmds"]
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(cmds, cv)
    assert len(set(cv.buf)) > 1


def test_server_accepts_input(server):
    _srv, prov, host, port = server
    status, _ = _post(host, port, "/input",
                      {"events": [{"type": "hold", "name": "a", "down": True}]})
    assert status == 200
    deadline = time.time() + 3
    while not prov.applied and time.time() < deadline:
        time.sleep(0.01)
    assert prov.applied and prov.applied[0]["name"] == "a"


def test_server_404_for_unknown_path(server):
    _srv, _prov, host, port = server
    status, _ctype, _body = _get(host, port, "/nope")
    assert status == 404


def test_server_recording_gate_idle():
    """recording_wanted() is False with no recent /frame fetch (the gate that keeps the
    Tee a pure pass-through unless a browser is actually polling)."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    srv = web.WebServer(rec, _FakeProvider(), port=0)
    assert srv.recording_wanted() is False        # server not started
    assert srv.start("127.0.0.1") is True
    try:
        assert srv.recording_wanted() is False    # no /frame fetched yet
        srv.begin_frame()
        assert rec.enabled is False               # disabled -> no recording
    finally:
        srv.stop()
