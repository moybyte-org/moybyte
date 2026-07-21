"""Host-side tests for the DEVICE web view (#41/#22): the device serves its running
console to a phone/desktop browser over WiFi via the SAME draw-command protocol the
host web console uses (tools/web_console.py + web_console.html), so the same browser
page renders the device frames.

The device module firmware/.../modules/moy_webserver.py is written MicroPython-first but
imports + runs on CPython (it has ujson/usocket/utime fallbacks), so everything testable
OFF-device is exercised here:

  * THE RECORDER + TEE: a TeeCanvas forwards every draw call to the real device canvas
    AND, only while the recorder is ENABLED, records a JSON-serializable draw-command
    list in the EXACT format of tools/command_canvas.CommandCanvas. Disabled, it's a
    pure pass-through (the zero-cost normal path).
  * THE FAITHFULNESS CROSS-CHECK: replay the recorded commands onto a host rasterizing
    Canvas (the Python twin of the browser's JS replayer) and assert it reproduces the
    same pixels the (raster-equivalent) draws would -- proving the stream is complete.
  * THE PROTOCOL: /assets (palette + petme128 font + sheet/tilemap), the frame command
    list + cart title, and /input event parsing all serialize to the host's shape.
  * THE TRANSPORT (#41 WebSocket swap): the RFC 6455 handshake accept-key, WS frame
    encode/decode (masking + the 7/16/64-bit length forms), the cross-iteration partial-frame
    buffering, and a real-localhost WebSocket round-trip (input up + a pushed frame down)
    when a `websockets` client lib is available. The legacy HTTP /frame & /input fallbacks
    are still exercised over a real localhost socket (ephemeral port).

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

import moy_webserver as web  # noqa: E402  (the DEVICE TRANSPORT adapter)

from runtime import font as _font  # noqa: E402
from runtime import palette as _pal  # noqa: E402
from runtime import web_view  # noqa: E402  (the SHARED web-view core the transport imports)
from runtime.canvas import Canvas, Image  # noqa: E402
from runtime.editors import SpriteSheet  # noqa: E402

WIDTH, HEIGHT = 320, 240

# The PAYLOAD-DIET reference replayer is now the SHARED web_view.replay_to_canvas (the Python
# twin of the browser JS in web_view.PAGE_HTML): defspr / spr-by-index or self-contained / diet
# map / settiles / off-screen layers, all through the SAME Canvas rasterizer. Used here (and by
# the host web-console tests) to prove the device's recorded stream reproduces the panel
# pixel-for-pixel. `assets` (optional) seeds the sheet + tilemap for the diet map replay.
replay_diet = web_view.replay_to_canvas


def test_moved_code_is_re_exported_from_the_shared_module():
    """The recorder / Tee / payload builders / serve logic / page / constants moved into the
    SHARED web_view module; the DEVICE moy_webserver is a thin transport that IMPORTS + re-exports
    them, so every existing `web.X` reference still resolves to the shared module's object (no
    drift, no stale local copy). The invariant is that the re-exports ALIAS whatever web_view the
    transport imported (`web._wv`) -- NOT object-identity vs `runtime.web_view`: build.sh COPIES
    runtime/web_view.py to modules/web_view.py, so after a firmware build the transport's
    top-level `import web_view` resolves to that (identical) staged copy, a distinct object from
    `runtime.web_view` yet the same source. `web._wv` must be a real web_view module."""
    assert web._wv is not None and hasattr(web._wv, "DrawRecorder")
    for name in ("DrawRecorder", "RecordingLayer", "_LayerRecorder", "TeeCanvas", "ServedState",
                 "palette_rgb", "sheet_payload", "tilemap_payload", "assets_payload",
                 "frame_payload", "apply_events", "PAGE_HTML", "MAX_ATLAS",
                 "MAX_DEFSPR_BYTES_PER_FRAME", "WEB_FPS_CAP", "WEB_FRAME_INTERVAL_MS",
                 "WEB_MAX_BYTES_PER_SEC",
                 # The WebSocket handshake + byte framing also moved to the shared module (the
                 # host speaks WS too now), so these are re-exports as well -- not local defs.
                 "ws_accept_key", "ws_handshake_response", "ws_header_key", "is_ws_upgrade",
                 "ws_encode", "ws_decode", "WS_GUID", "WS_MAX_FRAME"):
        assert getattr(web, name) is getattr(web._wv, name), name


# The device's canonical RGB565 MOY64 LUT (a copy of moy_runtime.PAL565 -- the host
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
    spr_batch = spr_tile = map = reset_state = camera = clip = pal = palt = _bump

    def sync_back(self):
        self.calls += 1


def _build_tee():
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    dev = _FakeDeviceCanvas()
    tee = web.TeeCanvas(dev, rec)
    return tee, rec, dev


def _serveable(rec):
    """A WebServer wired to `rec` with no socket -- just to call served_frame()/reset_served()
    so a test can exercise the SERVE-TIME defspr delivery (the prepend + the `served` set) the
    way the live /frame handler does, without a real connection."""
    return web.WebServer(rec, _FakeProvider(), port=0)


def _served(rec, server):
    """The command list the browser actually RECEIVES for the recorder's last committed
    frame: served_frame() prepends any not-yet-shipped defsprs (serve-time defspr, #41)."""
    return server.served_frame(rec.frame())


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


def test_recorder_spr_records_index_only_defspr_delivered_at_serve_time():
    """SERVE-TIME defspr (#41): the recorder's `spr` records ONLY a tiny ["spr", index, x,
    y, scale, flip] -- it NEVER inlines the defspr -- and just registers the bitmap in the
    atlas (defspr_cmd reconstructs it). The WebServer PREPENDS the ["defspr", ...] at serve
    time, so the FRAME THE BROWSER RECEIVES is self-contained even if the recording frame
    that first saw the bitmap was dropped. The bitmap pixels travel only in the defspr."""
    tee, rec, _dev = _build_tee()
    rec.enabled = True
    rec.begin()
    img = Image(2, 2, [1, 2, 3, 4], transparent=0)
    tee.spr(img, 5, 6, 2, 1)
    rec.commit()
    # The recorder's raw frame is index-only (no defspr inlined at record time).
    assert rec.frame() == [["spr", 0, 5, 6, 2, 1]]
    # defspr_cmd reconstructs the bitmap for the atlas index from the held Image.
    assert rec.defspr_cmd(0) == ["defspr", 0, 2, 2, 0, [1, 2, 3, 4]]
    # The served frame (what the browser gets) prepends that defspr ONCE.
    server = _serveable(rec)
    assert _served(rec, server) == [
        ["defspr", 0, 2, 2, 0, [1, 2, 3, 4]],   # the bitmap, delivered at serve time
        ["spr", 0, 5, 6, 2, 1],                 # index, x, y, scale, flip
    ]


def test_served_defspr_sent_once_then_omitted_across_frames():
    """The SAME Image (id stable -- make_api reuses one per tile) is delivered as a defspr
    ONCE per browser session; every later SERVED frame -- including a later one -- is just
    a 6-number spr-by-index. This is the whole 10x: a 16x16 sprite drops from ~600 bytes/
    frame to ~20. The `served` set lives on the server (the browser keeps its ATL), so the
    dedup is across served frames, not record frames."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
    img = Image(2, 2, [1, 2, 3, 4], transparent=-1)
    rec.enabled = True
    # Frame 1: two blits of the same Image -> the served frame has one defspr + two sprs.
    rec.begin()
    tee.spr(img, 0, 0)
    tee.spr(img, 8, 0, 2)
    rec.commit()
    f1 = _served(rec, server)
    assert [c[0] for c in f1] == ["defspr", "spr", "spr"]
    assert f1[1] == ["spr", 0, 0, 0, 1, 0] and f1[2] == ["spr", 0, 8, 0, 2, 0]
    # Frame 2: the SAME Image again -> NO defspr in the served frame (already shipped).
    rec.begin()
    tee.spr(img, 16, 16)
    rec.commit()
    f2 = _served(rec, server)
    assert f2 == [["spr", 0, 16, 16, 1, 0]], "a shipped bitmap is never re-sent"
    # A DIFFERENT Image gets the next index, with its own one-time defspr at serve time.
    img2 = Image(2, 2, [5, 6, 7, 8], transparent=-1)
    rec.begin()
    tee.spr(img2, 0, 0)
    rec.commit()
    f3 = _served(rec, server)
    assert f3 == [["defspr", 1, 2, 2, -1, [5, 6, 7, 8]], ["spr", 1, 0, 0, 1, 0]]


def test_served_defspr_survives_a_dropped_frame():
    """The load-bearing BUG-1 regression: a sprite first referenced in a frame that is
    DROPPED (never served -- the frame cap discards frames and the browser polls only the
    latest) still gets its defspr delivered on the NEXT SERVED frame. With record-time
    defspr the bitmap rode the dropped frame and the browser drew nothing; serve-time
    defspr keys delivery off what the browser actually RECEIVES, so it can't be stranded."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
    img = Image(2, 2, [9, 9, 9, 9], transparent=-1)
    rec.enabled = True
    # Frame A: references the sprite -- but this frame is DROPPED (server never serves it).
    rec.begin()
    tee.spr(img, 4, 4)
    rec.commit()
    # (no _served() call here -> the frame that first saw the bitmap is dropped)
    # Frame B: the only frame the browser polls. The served frame must STILL carry the
    # defspr, because the server hasn't actually delivered it yet.
    rec.begin()
    tee.spr(img, 5, 5)
    rec.commit()
    fb = _served(rec, server)
    assert fb == [["defspr", 0, 2, 2, -1, [9, 9, 9, 9]], ["spr", 0, 5, 5, 1, 0]], (
        "a sprite first seen in a dropped frame must get its defspr on the next served frame")


def test_served_set_resets_on_assets_so_a_reconnecting_browser_refetches():
    """A page load / cart change makes the browser clear its atlas + refetch /assets; the
    server mirrors that with reset_served(), so the NEXT served frame re-ships every defspr
    its sprites reference (a fresh browser session must not be left with an empty atlas)."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
    img = Image(1, 1, [3], transparent=-1)
    rec.enabled = True
    rec.begin()
    tee.spr(img, 0, 0)
    rec.commit()
    assert _served(rec, server)[0][:2] == ["defspr", 0]      # first delivery ships it
    rec.begin()
    tee.spr(img, 1, 1)
    rec.commit()
    assert [c[0] for c in _served(rec, server)] == ["spr"]   # already shipped -> omitted
    # The browser reloads (/assets re-served) -> forget what it has.
    server.reset_served()
    rec.begin()
    tee.spr(img, 2, 2)
    rec.commit()
    assert _served(rec, server)[0][:2] == ["defspr", 0], "after /assets the defspr re-ships"


def test_recorder_reset_atlas_re_ships_bitmaps():
    """reset_atlas() (called when the cart/sheet changes) drops the atlas so the next
    frame re-ships defspr from index 0 -- a new cart's bitmaps never collide with a
    previous cart's stale indices. It bumps atlas_gen, which the server notices and uses
    to drop its `served` set, so the next served frame re-delivers the defspr."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
    img = Image(1, 1, [3], transparent=-1)
    rec.enabled = True
    rec.begin()
    tee.spr(img, 0, 0)
    rec.commit()
    assert _served(rec, server)[0][:2] == ["defspr", 0]
    gen0 = rec.atlas_gen
    rec.reset_atlas()                           # cart change
    assert rec.atlas_gen != gen0, "reset_atlas bumps the gen so the server drops `served`"
    rec.begin()
    tee.spr(img, 0, 0)                          # same Image, but atlas was reset
    rec.commit()
    assert _served(rec, server)[0][:2] == ["defspr", 0], "after reset, index 0 ships again"


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


def _map_sheet_and_tm():
    """A 2x1 sheet (tile 1 painted) + a real TileMap with cell (1,0) = tile 1."""
    sheet = SpriteSheet(2, 1)               # 2 tiles of 8x8
    for y in range(8):
        for x in range(8, 16):
            sheet.pset(x, y, 5)             # paint tile 1 solid
    from runtime.editors import TileMap
    tm = TileMap(2, 1)
    tm.mset(1, 0, 1)                        # cell (1,0) -> tile 1
    return sheet, tm


def test_tee_map_records_one_op_not_per_cell():
    """map() records ONE ["map", ...] op (was ~one fat spr per cell), preceded by a
    settiles syncing the browser's tilemap. The browser replays the cell walk itself from
    its cached sheet + tilemap, so a 20x15 map is ~1 op instead of ~300 fat sprs (#41)."""
    tee, rec, _dev = _build_tee()
    sheet, tm = _map_sheet_and_tm()
    rec.enabled = True
    rec.begin()
    tee.map(tm, sheet, 0, 0, 2, 1, 0, 0, -1, 1)
    rec.commit()
    cmds = rec.frame()
    assert [c[0] for c in cmds] == ["settiles", "map"]
    assert cmds[1] == ["map", 0, 0, 2, 1, 0, 0, 1, -1]   # mx,my,w,h,sx,sy,scale,colorkey
    # No per-cell spr/defspr commands at all -- that was the payload bomb.
    assert not any(c[0] in ("spr", "defspr") for c in cmds)


def test_tee_map_settiles_only_when_tilemap_changes():
    """settiles ships the tilemap ONCE (first map after a change); an UNCHANGED tilemap on
    a later map() does NOT re-ship it. A cart mutation (mset) re-emits settiles. This keeps
    a static-map cart at ~1 op/frame and only pays the ~w*h cells blob on an actual edit."""
    tee, rec, _dev = _build_tee()
    sheet, tm = _map_sheet_and_tm()
    rec.enabled = True
    # Frame 1: first map -> settiles + map.
    rec.begin()
    tee.map(tm, sheet, 0, 0, 2, 1, 0, 0, -1, 1)
    rec.commit()
    assert [c[0] for c in rec.frame()] == ["settiles", "map"]
    # Frame 2: tilemap UNCHANGED -> just the map op (no settiles re-ship).
    rec.begin()
    tee.map(tm, sheet, 0, 0, 2, 1, 0, 0, -1, 1)
    rec.commit()
    assert [c[0] for c in rec.frame()] == ["map"], "unchanged tilemap must not re-ship"
    # Frame 3: cart mutates the map (mset) -> settiles re-emitted before the map.
    tm.mset(0, 0, 1)                        # destroy/place a tile, like Battle City
    rec.begin()
    tee.map(tm, sheet, 0, 0, 2, 1, 0, 0, -1, 1)
    rec.commit()
    cmds = rec.frame()
    assert [c[0] for c in cmds] == ["settiles", "map"]
    assert cmds[0] == ["settiles", 2, 1, [2, 2]]     # both cells now tile 1 (id+1=2)


# ---------------------------------------------------------------------------
# The faithfulness cross-check: the recorded stream replays to valid pixels via the
# host rasterizer (the Python twin of the browser's JS replayer).
# ---------------------------------------------------------------------------


def test_recorded_stream_replays_to_pixels():
    """Record a varied frame through the Tee, then replay it onto a host Canvas (the
    same path the browser performs in JS) and assert it produced a non-blank frame --
    proving the device's command stream is complete + replayable."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
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
    replay_diet(_served(rec, server), cv)       # the browser replays the served frame
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
    server = _serveable(rec)                # ... and the served frame carries the defsprs
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
    # The browser replays the SERVED frame (defspr prepended at serve time), not the raw
    # recorder frame -- so the replayer must apply the serve-time-delivered defsprs.
    replay_diet(_served(rec, server), replayed)
    assert bytes(raster.buf) == bytes(replayed.buf), "the served stream must reproduce the rasterized panel"


# ---------------------------------------------------------------------------
# Off-screen LAYERS (#54 scroll + #43 cached top bar): ONE recorded-layer mechanism.
# The Tee mints a RecordingLayer (a real device-layer Canvas for the panel + a recorded
# indexed command stream); draw_layer/blit_strip record a tiny blit_layer; the WebServer
# ships the layer's stream ONCE as a deflayer (serve-time, like defspr). The cross-check
# drives a layer through the device Tee over a rasterizing Canvas, replays the SERVED
# stream, and asserts PIXEL-IDENTICAL -- proving the scroll layer + bar reproduce the
# panel. This is the device half of the Sky Run black-background + trails fix.
# ---------------------------------------------------------------------------


def test_scroll_layer_draw_layer_replays_pixel_identical_at_offset():
    """A wide scroll layer pre-rendered ONCE, then window-copied at a camera offset
    (draw_layer -> blit_window_from), replays byte-identically -- and its stream ships as
    ONE deflayer + a tiny windowed blit_layer per frame (no RGB565 pixels on the wire)."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    # Build a layer WIDER than the screen (the #54 scroll world) -- primitives only,
    # exactly like Sky Run's _build_world.
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(1)
    lay.rect(0, HEIGHT - 40, WIDTH * 2, 40, 3)
    for gx in range(0, WIDTH * 2, 60):
        lay.circ(gx + 10, 40, 8, 7)
        lay.rect(gx + 30, HEIGHT - 60, 6, 20, 4)
    # Frame: clear, window-copy the visible slice at a camera offset, actor on top.
    rec.begin()
    tee.cls(0)
    tee.blit_window_from(lay, 137, 0)
    tee.rect(150, 100, 12, 22, 8)
    rec.commit()
    served = _served(rec, server)
    assert [c[0] for c in served].count("deflayer") == 1, "the layer stream ships ONCE"
    assert [c[0] for c in served].count("blit_layer") == 1
    bl = next(c for c in served if c[0] == "blit_layer")
    assert bl == ["blit_layer", lay.id, 137, 0], "a windowed blit_layer (no 'full')"
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(served, replayed)
    assert bytes(raster.buf) == bytes(replayed.buf), "scroll draw_layer replay must match the panel"
    assert len(set(replayed.buf)) > 1, "the scroll frame must not replay to a flat (black) screen"


def test_cached_top_bar_blit_strip_replays_pixel_identical():
    """The cached top-bar strip (blit_strip) -- which on the device blits icon SPRITES into
    the layer -- uses the SAME mechanism: rendered once, then full-copied. The served stream
    carries ONE deflayer + a 'full' blit_layer; the layer's spr is self-contained (full-pixel
    in the layer stream), so the bar replays exactly with no atlas coupling."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    strip = tee.new_layer(WIDTH, 18)
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.rect(0, 17, WIDTH, 1, 5)
    strip.print("12:34", 280, 3, 6, 2)   # WITH a scale arg, exactly like the real top bar
    icon = Image(14, 14, [7] * (14 * 14), transparent=-1)   # an icon-like sprite into the bar
    strip.spr(icon, 8, 2)
    # Frame: content under the bar, then stamp the cached bar.
    rec.begin()
    tee.cls(2)
    tee.rect(40, 60, 100, 80, 9)
    tee.blit_strip(strip, 0, 0)
    rec.commit()
    served = _served(rec, server)
    assert [c[0] for c in served].count("deflayer") == 1
    bl = next(c for c in served if c[0] == "blit_layer")
    assert bl == ["blit_layer", strip.id, 0, 0, "full"], "a full blit_layer (the cached bar)"
    # The deflayer carries the icon spr as a SELF-CONTAINED full-pixel command (a pix list).
    dl = next(c for c in served if c[0] == "deflayer")
    spr_cmds = [k for k in dl[4] if k[0] == "spr"]
    assert spr_cmds and isinstance(spr_cmds[0][7], list), "layer spr is self-contained (full-pixel)"
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(served, replayed)
    assert bytes(raster.buf) == bytes(replayed.buf), "cached-bar blit_strip replay must match the panel"


def test_layer_ships_once_then_reference_only_across_served_frames():
    """A layer pre-rendered once ships its deflayer on the FIRST served frame referencing
    it, then every later served frame is a tiny blit_layer (the ship-once WiFi win). The
    `served_layers` tracking lives on the server (the browser keeps its LAY cache), so the
    dedup is across SERVED frames -- robust to dropped frames, like the defspr path."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(4)
    lay.rect(0, 100, WIDTH * 2, 40, 8)
    layers = {}                                  # the persistent browser-side LAY cache
    # Frame 1: deflayer + blit_layer.
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    f1 = _served(rec, server)
    assert [c[0] for c in f1].count("deflayer") == 1
    r1 = Canvas(WIDTH, HEIGHT); replay_diet(f1, r1, layers=layers)
    assert bytes(r1.buf) == bytes(raster.buf)
    # Frame 2: same layer, no redraw -> NO deflayer (already shipped), just blit_layer.
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 64, 0); rec.commit()
    f2 = _served(rec, server)
    assert not any(c[0] == "deflayer" for c in f2), "a shipped layer is not re-sent"
    r2 = Canvas(WIDTH, HEIGHT); replay_diet(f2, r2, layers=layers)   # resolves vs cached LAY
    assert bytes(r2.buf) == bytes(raster.buf)


def test_layer_reships_deflayer_on_redraw():
    """When a layer is REDRAWN (the cached bar repainted on a clock tick / theme change),
    its gen bumps and the next SERVED frame re-ships the deflayer with the fresh stream."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    strip = tee.new_layer(WIDTH, 18)
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:34", 280, 3, 6, 2)   # WITH a scale arg, exactly like the real top bar
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    f1 = _served(rec, server)
    assert "12:34" in str(next(c for c in f1 if c[0] == "deflayer"))
    # No redraw -> no deflayer next frame.
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    assert not any(c[0] == "deflayer" for c in _served(rec, server))
    # REDRAW the strip (a new clock minute) -> gen bumps -> re-ship the fresh stream.
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:35", 280, 3, 6, 2)   # WITH a scale arg, exactly like the real top bar
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    f3 = _served(rec, server)
    dl = next((c for c in f3 if c[0] == "deflayer"), None)
    assert dl is not None and "12:35" in str(dl), "a redrawn layer re-ships its fresh stream"


def test_layer_served_set_resets_on_assets():
    """A page load / cart change clears the browser's LAY cache; the server mirrors that
    with reset_served(), so the next served frame re-ships every referenced layer's
    deflayer (a reconnecting browser must not be left with an empty layer cache)."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(2)
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, server))     # first delivery
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert not any(c[0] == "deflayer" for c in _served(rec, server))  # already shipped
    server.reset_served()                                            # /assets re-served
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, server)), "after /assets the deflayer re-ships"


def test_layer_dropped_on_reset_atlas_reships_via_gen():
    """reset_atlas() (a cart change) drops the layer registry so a new cart's layer starts
    at id 0; it bumps atlas_gen, which the server uses to drop its served-layers set, so the
    new cart's layer re-ships its deflayer (no collision with the old cart's stale id)."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    lay = tee.new_layer(WIDTH, 18)
    lay.cls(3)
    rec.begin(); tee.cls(0); tee.blit_strip(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, server))
    rec.reset_atlas()                                # cart change drops layers + bumps gen
    assert rec._layers == []
    lay2 = tee.new_layer(WIDTH, 18)                  # the new cart's layer -> id 0 again
    assert lay2.id == 0
    lay2.cls(5)
    rec.begin(); tee.cls(0); tee.blit_strip(lay2, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, server)), "a new cart's layer re-ships"


def test_cached_bar_strip_reused_across_cart_change_self_heals_id_collision():
    """THE on-hardware bug (#54/#41): the console's top-bar strip is cached on the
    Workstation and REUSED across carts, so it OUTLIVES reset_atlas (the cart change),
    which clears _layers and restarts the new cart's layers at id 0. Without self-healing
    the stale bar (still id 0) COLLIDES with a scroll cart's fresh layer (also id 0): both
    emit ["blit_layer", 0] but reference DIFFERENT buffers, so the bar blits the scroll's
    pixels and the scroll blits the bar's -- exactly the "only the background / the previous
    cart's layer bleeds through / duplication" the device showed. _ensure_registered must
    re-id the orphaned bar so each replays to its OWN buffer (pixel-identical to the panel).
    Every prior layer test created fresh layers per gen, so none reused one across a reset."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    server = _serveable(rec)
    rec.enabled = True
    # Cart A: paint + ship the cached bar strip (the console keeps THIS object across carts).
    bar = tee.new_layer(WIDTH, 18)
    bar.rect(0, 0, WIDTH, 18, 7)             # a white bar -- visibly != the scroll world
    bar.print("12:34", 280, 3, 6, 2)
    rec.begin(); tee.cls(0); tee.blit_strip(bar, 0, 0); rec.commit()
    _served(rec, server)
    assert bar.id == 0
    # Cart change: reset_atlas drops _layers + bumps gen (mirrors WebView.begin_frame).
    rec.reset_atlas()
    # Cart B (a scroll cart): a NEW wide layer registers FRESH and reclaims id 0.
    scroll = tee.new_layer(WIDTH * 2, HEIGHT)
    scroll.cls(3)
    scroll.rect(0, 100, WIDTH * 2, 40, 8)
    assert scroll.id == 0, "the scroll layer claims id 0 in the new gen"
    # A frame referencing BOTH: the scroll world, then the SAME cached bar (NOT repainted,
    # so it still carries its stale id 0 -> the collision, unless healed on blit).
    rec.begin()
    tee.cls(0)
    tee.blit_window_from(scroll, 0, 0)
    tee.blit_strip(bar, 0, 0)
    rec.commit()
    served = _served(rec, server)
    assert bar.id != scroll.id, "the orphaned bar must re-register to a non-colliding id"
    assert [c[0] for c in served].count("deflayer") == 2, "both layers ship their own stream"
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(served, replayed)
    assert bytes(replayed.buf) == bytes(raster.buf), "no collision: bar=bar, world=world"


def test_tee_tolerates_plain_non_recording_layer():
    """A layer built on the RAW canvas BEFORE the web view bound this Tee -- e.g. the console's
    cached top-bar strip carried across the canvas swap -- is a plain canvas, NOT a
    RecordingLayer. The Tee must blit it to the panel and skip recording, not crash on a
    missing _end_batch/_c (the "'DeviceCanvas' object has no attribute '_end_batch'" frame
    error seen on hardware, which hit EVERY cart since the bar is drawn for all of them)."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    rec.enabled = True
    plain = Canvas(WIDTH, 18)                 # a plain layer (NOT a RecordingLayer)
    plain.rect(0, 0, WIDTH, 18, 9)
    rec.begin()
    tee.cls(0)
    tee.blit_strip(plain, 0, 0)               # must NOT raise
    tee.blit_window_from(plain, 0, 0)         # must NOT raise
    rec.commit()
    # Forwarded to the panel (the bar is visible there) but NOT recorded -- a plain layer has
    # no command stream, so it can't ride the deflayer/blit_layer protocol.
    assert not any(c[0] in ("blit_layer", "deflayer") for c in rec.frame())


def test_tee_declines_the_native_spr_gate():
    """#63 spr_gate: the Tee must explicitly REFUSE the native spr fast path. Its
    __getattr__ forwards unknown attrs to the real canvas, so without an explicit
    make_spr_gate override, make_api(tee) would fetch the REAL DeviceCanvas's gate
    -- and the C gate would append quads straight to the panel batch, pixels the
    recorder never sees (a browser cart with no sprites; stream mode rasterizing
    what it should skip). Declining keeps every spr on the recorded Python path."""
    tee, rec, dev = _build_tee()

    # Even if the real canvas offers a gate, the Tee must say no.
    dev.make_spr_gate = lambda sheet, fallback: object()
    assert tee.make_spr_gate(object(), lambda *a: None) is None
    # ...while ordinary unknown attrs still forward (the Tee contract).
    dev.some_marker = 42
    assert tee.some_marker == 42


def test_map_replays_pixel_identical_to_panel_via_cached_assets():
    """The strongest map() cross-check (#41): drive a real map() (+ a couple of sprites)
    through the device TeeCanvas over a rasterizing Canvas (the panel stand-in), then
    replay the ONE map op the browser receives -- against the SAME sheet + tilemap it got
    from /assets -- onto a fresh Canvas, and assert PIXEL-IDENTICAL. This proves the
    browser's cached-tilemap replay reproduces what the device panel drew, even though no
    per-cell pixels crossed the wire."""
    from runtime.editors import TileMap
    sheet = SpriteSheet(4, 4)
    # Paint a few distinct tiles so the map has real content (tiles 1, 2, 5).
    for tid, col in ((1, 8), (2, 11), (5, 14)):
        ox = (tid % sheet.cols) * sheet.TILE
        oy = (tid // sheet.cols) * sheet.TILE
        for yy in range(sheet.TILE):
            for xx in range(sheet.TILE):
                # a 2-colour checker so transparency (index 0 via colorkey) is exercised
                sheet.pset(ox + xx, oy + yy, col if (xx + yy) & 1 else 0)
    tm = TileMap(6, 4)
    tm.mset(0, 0, 1)
    tm.mset(2, 1, 2)
    tm.mset(5, 3, 5)
    tm.mset(3, 2, 1)

    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    rec.enabled = True
    rec.begin()
    tee.cls(1)
    # map() at scale 2 with colorkey 0 (so the painted-0 checker cells are transparent),
    # offset on-screen -- the same call a tile cart makes each frame.
    tee.map(tm, sheet, 0, 0, 6, 4, 16, 24, 0, 2)
    rec.commit()
    cmds = rec.frame()
    # The browser had the sheet + tilemap from /assets; replay the map op against them.
    assets = web.assets_payload(WIDTH, HEIGHT, PAL565, sheet, tm, "Tiles")
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(cmds, replayed, assets=assets)
    assert bytes(raster.buf) == bytes(replayed.buf), "map() replay must match the panel"


def test_map_settiles_mutation_replays_pixel_identical():
    """When a cart mutates the map mid-session (mset), the recorder ships a settiles and
    the browser's replay -- now driven by the UPDATED cells -- still matches the panel
    pixel-for-pixel (the Battle City destroyed-brick path)."""
    from runtime.editors import TileMap
    sheet = SpriteSheet(2, 1)
    for yy in range(8):
        for xx in range(8, 16):
            sheet.pset(xx, yy, 9)           # tile 1 solid
    tm = TileMap(4, 2)
    tm.mset(0, 0, 1)
    tm.mset(3, 1, 1)
    # The browser's starting cache is the ORIGINAL /assets tilemap.
    assets = web.assets_payload(WIDTH, HEIGHT, PAL565, sheet, tm, "BC")
    # Now the cart destroys a tile and adds another, THEN draws the map.
    tm.mset(0, 0, -1)                       # clear
    tm.mset(2, 0, 1)                        # place
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    rec.enabled = True
    rec.begin()
    tee.cls(0)
    tee.map(tm, sheet, 0, 0, 4, 2, 0, 0, -1, 1)
    rec.commit()
    cmds = rec.frame()
    assert any(c[0] == "settiles" for c in cmds), "a mutation must re-ship the tilemap"
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(cmds, replayed, assets=assets)   # settiles in the stream updates the cache
    assert bytes(raster.buf) == bytes(replayed.buf)


def test_spr_batch_dedups_tiles_across_frames():
    """spr_batch resolves tiles through the recorder's STABLE tile-image cache, so a
    repeated tile maps to the SAME atlas index across frames; the SERVED frame ships each
    bitmap ONCE (defspr) and every later item -- this frame and later frames -- is a 6-int
    spr-by-index. Without the stable cache each frame would re-key a fresh tile and re-ship
    its defspr (the explosion-heavy-frame payload bomb)."""
    tee, rec, _dev = _build_tee()
    server = _serveable(rec)
    sheet = SpriteSheet(4, 4)
    for tid, col in ((1, 8), (2, 11)):
        ox = (tid % sheet.cols) * sheet.TILE
        oy = (tid // sheet.cols) * sheet.TILE
        for yy in range(sheet.TILE):
            for xx in range(sheet.TILE):
                sheet.pset(ox + xx, oy + yy, col)
    rec.enabled = True
    # Frame 1: four items over two tiles -> the served frame has two defspr (tiles 1, 2)
    # delivered at serve time + four spr.
    rec.begin()
    tee.spr_batch(sheet, [(1, 0, 0), (2, 8, 0), (1, 16, 0), (2, 24, 0)], colorkey=-1)
    rec.commit()
    f1 = _served(rec, server)
    assert [c[0] for c in f1].count("defspr") == 2
    assert [c[0] for c in f1].count("spr") == 4
    # Frame 2: same tiles -> NO new defspr in the served frame (bitmaps already shipped).
    rec.begin()
    tee.spr_batch(sheet, [(1, 0, 0), (2, 8, 0)], colorkey=-1)
    rec.commit()
    f2 = _served(rec, server)
    assert [c[0] for c in f2] == ["spr", "spr"], "batch tiles are not re-shipped per frame"


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
    replay_diet(rec.frame(), cv)
    # Rasterize the same text directly with the host font onto a reference buffer.
    ref = Canvas(WIDTH, HEIGHT)
    ref.cls(0)
    ref.print("Kid", 20, 20, 7)
    assert bytes(cv.buf) == bytes(ref.buf)


# ---------------------------------------------------------------------------
# WM-SURFACE PARTITION (Stage 9 of docs/shell_ux_technical_plan_v1.md): one command
# stream PER window-manager surface (the wm.draw_stack() layers -- bar / app-content /
# player-viewport), tagged by id+domain, so the browser page becomes a SECOND window
# manager that COMPOSITES the surfaces (spec Section 3: the S3 fullscreen stack + the
# browser). OFF by default: the flat stream stays byte-identical and the surfaces are a
# sliced VIEW of it that composites (in order) to the SAME pixels.
# ---------------------------------------------------------------------------


def test_recorder_partitions_the_frame_into_wm_surfaces():
    """With surfaces_on, begin_surface(sid, domain) marks each WM-stack layer; commit() slices
    the flat frame into [[sid, domain, cmds], ...]. Each surface holds ONLY its layer's draws,
    and the surfaces CONCATENATE back to exactly the flat frame (they are a view of the same
    commands), so compositing them reproduces the flat frame's pixels."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.surfaces_on = True
    rec.begin()
    rec.begin_surface("desktop", "game")     # the player/cart viewport
    rec.cls(1)
    rec.rect(10, 10, 20, 20, 8)
    rec.begin_surface("perf", "game")        # a game-domain overlay
    rec.print("60", 300, 230, 7)
    rec.begin_surface("cursor", "system")    # the always-on cursor
    rec.spr(Image(2, 2, [7, 7, 7, 7], transparent=-1), 100, 100)
    rec.commit()
    surfs = rec.frame_surfaces()
    assert [(s[0], s[1]) for s in surfs] == [
        ("desktop", "game"), ("perf", "game"), ("cursor", "system")]
    assert [c[0] for c in surfs[0][2]] == ["cls", "rect"]
    assert [c[0] for c in surfs[1][2]] == ["print"]
    assert [c[0] for c in surfs[2][2]] == ["spr"]
    # Concatenation == the flat frame (so surfaces composite to the same pixels).
    assert [c for s in surfs for c in s[2]] == rec.frame()


def test_recorder_surfaces_off_is_flat_and_byte_identical():
    """surfaces_on defaults False: begin_surface is a NO-OP (marks nothing, never raises),
    frame_surfaces() is None, and the flat frame is exactly what it was before Stage 9 -- the
    zero-cost, byte-identical web-view-off path (the golden guarantee)."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    assert rec.surfaces_on is False
    rec.begin()
    rec.begin_surface("desktop", "game")     # a no-op while off
    rec.cls(2)
    rec.rect(0, 0, 5, 5, 4)
    rec.commit()
    assert rec.frame_surfaces() is None
    assert rec._surf_marks == []
    assert rec.frame() == [["cls", 2], ["rect", 0, 0, 5, 5, 4]]


def test_recorder_pre_surface_captures_draws_before_the_first_mark():
    """A draw recorded BEFORE any begin_surface rides a leading "_pre" surface, so nothing is
    dropped and the surfaces still concatenate to the flat frame."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.surfaces_on = True
    rec.begin()
    rec.cls(0)                               # drawn before any surface begun
    rec.begin_surface("launcher", "system")
    rec.rect(1, 1, 2, 2, 3)
    rec.commit()
    surfs = rec.frame_surfaces()
    assert surfs[0][0] == "_pre" and [c[0] for c in surfs[0][2]] == ["cls"]
    assert surfs[1][0] == "launcher" and [c[0] for c in surfs[1][2]] == ["rect"]
    assert [c for s in surfs for c in s[2]] == rec.frame()


def test_served_surfaces_prepend_defs_and_composite_pixel_identical():
    """served_surfaces() delivers the ship-once defspr prefix as a LEADING "_defs" surface,
    then each WM surface; a browser replaying the surfaces IN ORDER (the shared
    replay_surfaces_to_canvas twin) reproduces the panel PIXEL-IDENTICALLY -- and identically
    to replaying the flat served frame (the surfaces ARE a slice of it, "_defs" prepended).
    Primitives + atlas sprites (serve-time defspr) + text across three surfaces."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    st = web.ServedState(rec)
    rec.enabled = True
    rec.surfaces_on = True
    spr = Image(4, 4, [0, 8, 8, 0, 8, 7, 7, 8, 8, 7, 7, 8, 0, 8, 8, 0], transparent=0)
    rec.begin()
    rec.begin_surface("desktop", "game")     # the player viewport
    tee.cls(1)
    tee.rect(10, 10, 60, 40, 8)
    tee.spr(spr, 100, 100, 3)                 # atlas sprite -> its defspr rides "_defs"
    rec.begin_surface("perf", "game")
    tee.print("60 FPS", 8, 220, 7)
    rec.begin_surface("cursor", "system")
    tee.spr(spr, 150, 100, 2, 1)             # same bitmap -> reuses the atlas index
    rec.commit()
    served_flat, surfaces = st.served_surfaces(rec.frame(), rec.frame_surfaces())
    assert surfaces[0]["id"] == "_defs"
    assert any(c[0] == "defspr" for c in surfaces[0]["cmds"]), "the ship-once bitmap leads"
    assert [s["id"] for s in surfaces[1:]] == ["desktop", "perf", "cursor"]
    # Compositing the surfaces IN ORDER reproduces the panel pixel-for-pixel...
    comp = Canvas(WIDTH, HEIGHT)
    web_view.replay_surfaces_to_canvas(surfaces, comp)
    assert bytes(comp.buf) == bytes(raster.buf), "surface composite must match the panel"
    # ...and equals replaying the flat served frame.
    flatcv = Canvas(WIDTH, HEIGHT)
    replay_diet(served_flat, flatcv)
    assert bytes(flatcv.buf) == bytes(comp.buf)
    assert len(set(comp.buf)) > 1, "the composited frame must not be flat"


def test_surface_delta_stubs_unchanged_surfaces():
    """SurfaceDelta (#76): the second push of an unchanged surface is a {"same":1} stub
    (no cmds on the wire); a changed surface re-ships in full; "_defs" is never stubbed;
    a gen change or reset() forgets the cache (the page wiped SURF with ATL/LAY)."""
    delta = web.SurfaceDelta()
    frame1 = [
        {"id": "_defs", "domain": "system", "cmds": [["defspr", 0, 1, 1, -1, [7]]]},
        {"id": "launcher", "domain": "system", "cmds": [["cls", 1], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire1 = delta.encode(frame1, gen=0)
    assert wire1 == frame1, "a fresh client gets every surface in full"
    # Same content next frame -> stubs (except _defs, which is incremental by nature).
    frame2 = [
        {"id": "launcher", "domain": "system", "cmds": [["cls", 1], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire2 = delta.encode(frame2, gen=0)
    assert wire2 == [{"id": "launcher", "domain": "system", "same": 1},
                     {"id": "cursor", "domain": "system", "same": 1}]
    # One surface changes -> only it re-ships.
    frame3 = [
        {"id": "launcher", "domain": "system", "cmds": [["cls", 2], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire3 = delta.encode(frame3, gen=0)
    assert wire3[0] == frame3[0] and wire3[1] == {"id": "cursor", "domain": "system", "same": 1}
    # A gen bump (cart change wiped the browser's caches) re-ships everything.
    wire4 = delta.encode(frame3, gen=1)
    assert wire4 == frame3
    # reset() (fresh connection) likewise.
    delta.reset()
    assert delta.encode(frame3, gen=1) == frame3


def test_surface_delta_replay_pixel_identical_across_frames():
    """The wire-level proof (#76): two consecutive frames delta-encoded per connection,
    replayed through the browser twin (replay_delta_surfaces_to_canvas + a persistent
    SURF cache), reproduce the rasterized panel PIXEL-IDENTICALLY -- including a frame
    whose static surfaces arrived only as {"same":1} stubs."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    st = web.ServedState(rec)
    delta = web.SurfaceDelta()
    spr = Image(4, 4, [0, 8, 8, 0, 8, 7, 7, 8, 8, 7, 7, 8, 0, 8, 8, 0], transparent=0)
    rec.enabled = True
    rec.surfaces_on = True

    def record(rect_x):
        rec.begin()
        rec.begin_surface("desktop", "game")      # the animating surface
        tee.cls(1)
        tee.rect(rect_x, 10, 60, 40, 8)
        rec.begin_surface("bar", "system")        # the static chrome surface
        tee.print("12:00", 8, 2, 7)
        tee.spr(spr, 300, 2, 1)
        rec.commit()
        _flat, dicts = st.served_surfaces(rec.frame(), rec.frame_surfaces())
        return delta.encode(dicts, gen=rec.atlas_gen)

    cache = {}
    atlas = {}                                    # the browser's persistent ATL twin
    wire1 = record(10)
    cv1 = Canvas(WIDTH, HEIGHT)
    web_view.replay_delta_surfaces_to_canvas(wire1, cache, cv1, atlas=atlas)
    assert bytes(cv1.buf) == bytes(raster.buf), "frame 1 (full) must match the panel"
    wire2 = record(50)                            # desktop moved; bar unchanged
    ids = {s["id"]: s for s in wire2}
    assert "cmds" in ids["desktop"], "the changed surface re-ships"
    assert ids["bar"].get("same") == 1, "the static chrome ships as a stub"
    cv2 = Canvas(WIDTH, HEIGHT)
    web_view.replay_delta_surfaces_to_canvas(wire2, cache, cv2, atlas=atlas)
    assert bytes(cv2.buf) == bytes(raster.buf), (
        "frame 2 composited from the delta + the SURF cache must match the panel")


def test_surface_scroll_layer_deflayer_rides_the_defs_surface():
    """A scroll layer spanning the player surface ships its stream ONCE as a deflayer in the
    leading "_defs" surface (serve-time, like defspr); the tiny blit_layer reference stays in
    the surface that drew it. The composited surfaces replay pixel-identically to the panel."""
    raster = Canvas(WIDTH, HEIGHT)
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    tee = web.TeeCanvas(raster, rec)
    st = web.ServedState(rec)
    rec.enabled = True
    rec.surfaces_on = True
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(1)
    lay.rect(0, HEIGHT - 40, WIDTH * 2, 40, 3)
    for gx in range(0, WIDTH * 2, 60):
        lay.circ(gx + 10, 40, 8, 7)
    rec.begin()
    rec.begin_surface("desktop", "game")     # the scroll world + an actor
    tee.cls(0)
    tee.blit_window_from(lay, 137, 0)
    tee.rect(150, 100, 12, 22, 8)
    rec.begin_surface("cursor", "system")
    tee.rect(4, 4, 6, 6, 9)
    rec.commit()
    served_flat, surfaces = st.served_surfaces(rec.frame(), rec.frame_surfaces())
    assert surfaces[0]["id"] == "_defs"
    assert [c[0] for c in surfaces[0]["cmds"]].count("deflayer") == 1
    assert [s["id"] for s in surfaces[1:]] == ["desktop", "cursor"]
    assert any(c[0] == "blit_layer" for c in surfaces[1]["cmds"]), "the blit stays in its surface"
    comp = Canvas(WIDTH, HEIGHT)
    web_view.replay_surfaces_to_canvas(surfaces, comp)
    assert bytes(comp.buf) == bytes(raster.buf), "scroll surface composite must match the panel"
    flatcv = Canvas(WIDTH, HEIGHT)
    replay_diet(served_flat, flatcv)
    assert bytes(flatcv.buf) == bytes(comp.buf)


def test_frame_payload_carries_surfaces_only_when_present():
    """frame_payload gains an optional `surfaces` field (Stage 9): present -> the payload
    carries the per-surface streams the browser composites; absent (None) -> the exact flat
    shape as before (device + web-view-off), so no existing consumer changes."""
    flat = web.frame_payload([["cls", 1]], "Demo")
    assert "surfaces" not in flat and flat["cmds"] == [["cls", 1]]
    json.dumps(flat)
    surfs = [{"id": "desktop", "domain": "game", "cmds": [["cls", 1]]},
             {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 1, 2, 1, 0]]}]
    p = web.frame_payload([], "Demo", 2, surfaces=surfs)
    assert p["surfaces"] == surfs and p["gen"] == 2
    json.dumps(p)


def test_page_composites_per_surface_streams():
    """The browser page (the SECOND window manager) composites per-surface streams: df() reads
    f.surfaces and replays each surface in order -- a {"same":1} entry from its SURF cache, a
    full one updating the cache first (#76 delta) -- falling back to the flat f.cmds when a
    frame carries none. The SURF cache wipes with ATL/LAY on a gen change (lock-step with the
    server's SurfaceDelta). Guard the served source (the JS can't be unit-tested here)."""
    page = web.PAGE_HTML
    assert "f.surfaces" in page, "the page must branch on per-surface streams"
    assert "s.same" in page, "the page replays a same-stub from its SURF cache (#76)"
    assert "SURF[s.id]=s.cmds" in page, "a full surface updates the SURF cache"
    assert 'if(s.id!=="_defs")' in page, "the ship-once _defs surface is never cached"
    assert "SURF={};HUD.unknown=0;}" in page, "a gen change wipes SURF with ATL/LAY"
    assert "rep(f.cmds||[])" in page, "the flat fallback (device + web-view-off) stays"


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


def test_assets_palette_decodes_close_to_moy64():
    """The device sends its REAL panel colours (RGB565-decoded), which match MOY64 to
    within 565 quantization -- so the browser shows what the panel shows."""
    pal = web.palette_rgb(PAL565)
    assert pal[0] == [0, 0, 0]
    for i in range(len(PAL565)):
        for ch in range(3):
            assert abs(pal[i][ch] - _pal.MOY64[i][ch]) <= 8, (i, ch)


def test_assets_includes_sheet_when_a_cart_is_open():
    """With a sheet, /assets carries its cols/rows/tile + flat pixels (host shape)."""
    sheet = SpriteSheet(2, 2)
    a = web.assets_payload(WIDTH, HEIGHT, PAL565, sheet, None, "Star Catcher")
    s = a["sheet"]
    assert s["cols"] == 2 and s["rows"] == 2 and s["tile"] == sheet.TILE
    assert len(s["pix"]) == s["w"] * s["h"]
    assert a["cart"] == "Star Catcher"


def test_frame_payload_shape():
    """/frame is {cmds, cart, gen, audio, perf} -- matches the host minus PCM (the device
    web view doesn't stream audio). `perf` defaults to None and carries the device stats."""
    cmds = [["cls", 1], ["rect", 0, 0, 10, 10, 2]]
    p = web.frame_payload(cmds, "Pong")
    assert p["cmds"] == cmds and p["cart"] == "Pong" and p["audio"] == "" and p["perf"] is None
    json.dumps(p)
    p2 = web.frame_payload(cmds, "Pong", 3, {"heap": 1234, "pf": 9})
    assert p2["gen"] == 3 and p2["perf"] == {"heap": 1234, "pf": 9}
    json.dumps(p2)


def test_perf_snapshot_shape_and_push_count():
    """_perf_snapshot() is the tiny device-stats dict the perf log rides on. heap is gc.mem_free
    KB (0 on CPython -- gc.mem_free is MicroPython-only, guarded), pf is the running pushed-frame
    counter, and dr/gap/thr are the per-frame stutter instants the browser folds into a window
    max (worst draw+commit ms / worst inter-push gap / throttled-push flag)."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    server = _serveable(rec)
    snap = server._perf_snapshot()
    keys = ("heap", "pf", "js", "tx", "dr", "gap", "thr")
    assert set(snap.keys()) == set(keys)
    assert all(isinstance(snap[k], int) for k in keys)
    assert snap["pf"] == 0
    server._frames_pushed += 2                       # _push_frame bumps this per sent frame
    assert server._perf_snapshot()["pf"] == 2


def test_perf_draw_ms_spans_begin_to_commit():
    """dr = the begin_frame->commit_frame span (device draw + rasterize, sans the push), so a
    slow cart frame surfaces even with no browser recording. Set unconditionally, so it works
    on the no-WS path (begin_frame stamps the start, commit_frame closes it)."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    server = _serveable(rec)
    server.begin_frame()
    server.commit_frame()
    dr = server._perf_snapshot()["dr"]
    assert isinstance(dr, int) and dr >= 0


class _PushWS:
    """A stand-in persistent WS conn that _service_ws can push through: drains no input and
    just records sends, so a test can exercise the gap/throttle bookkeeping without a socket."""

    def __init__(self):
        self.alive = True
        self.last_recv = web.ticks_ms()
        self.sent = []

    def drain_input(self):
        return []

    def send(self, payload):
        self.sent.append(payload)


def test_perf_gap_and_throttle_flag():
    """A push records the REAL inter-push gap (the stutter signal) and flags whether the
    bandwidth cap raised its interval above the fps floor -- so the log can tell a
    throttle-limited launcher from a device-limited one. A light frame is not throttled; a
    heavy (launcher-sized) one is."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    server = _serveable(rec)
    server._ws = _PushWS()
    # A light frame at the fps cap -> not throttled, and a real gap is recorded.
    server._last_payload_bytes = 800
    server._last_push_ms = web.ticks_ms() - 1000     # force the push gate wide open
    server._service_ws()
    assert server._ws.sent, "the gate opened -> a frame was pushed"
    snap = server._perf_snapshot()
    assert snap["thr"] == 0
    assert snap["gap"] > 0
    # A heavy launcher-sized frame -> the interval is raised over the floor -> throttled.
    server._last_payload_bytes = 4300
    server._last_push_ms = web.ticks_ms() - 1000
    server._service_ws()
    assert server._perf_snapshot()["thr"] == 1


def test_push_interval_floors_on_bandwidth_for_heavy_frames():
    """The WS push interval is the fps-cap floor, RAISED for a big last frame so a heavy
    screen (the launcher, ~4.3KB) self-throttles under WEB_MAX_BYTES_PER_SEC instead of
    saturating WiFi + stalling the loop on a blocked send. A light game frame stays at the
    fps cap (games unaffected) -- the fix for the "recv bounces 11->40" launcher stutter."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    server = _serveable(rec)
    assert server._push_interval_ms() == web.WEB_FRAME_INTERVAL_MS   # nothing sent yet
    server._last_payload_bytes = 800                                 # a light game frame
    assert server._push_interval_ms() == web.WEB_FRAME_INTERVAL_MS   # stays at the fps cap
    server._last_payload_bytes = 4300                                # a heavy launcher frame
    iv = server._push_interval_ms()
    assert iv == 4300 * 1000 // web.WEB_MAX_BYTES_PER_SEC
    assert iv > web.WEB_FRAME_INTERVAL_MS                            # throttled below the cap
    # ~bandwidth-bounded (integer-floor of the interval lets the rate sit a couple % over the
    # budget -- fine, 80KB/s already sits under the ~100KB/s ceiling).
    assert 4300 / (iv / 1000.0) <= web.WEB_MAX_BYTES_PER_SEC * 1.05


def test_defspr_spread_caps_bytes_per_frame_and_defers_rest():
    """A burst of new sprites (a wave of enemies) must NOT ship all their bitmaps in one frame
    -- that was the ~10KB frame that stalled the WiFi send ~100ms mid-game (the 'peak 9.77KB,
    recv drops to 14' hitch). served_frame caps the defspr bytes per frame; the rest ride later
    frames (a sprite no-ops until its bitmap arrives). Every bitmap still ships EXACTLY once."""
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    server = _serveable(rec)
    rec.enabled = True
    N = 20
    imgs = [Image(16, 16, [i % 63] * (16 * 16), transparent=-1) for i in range(N)]

    def draw_burst():
        rec.begin()
        for i, im in enumerate(imgs):
            rec.spr(im, i, 0)                    # atlas index i (distinct Image -> distinct id)
        rec.commit()
        return rec.frame()

    served = server.served_frame(draw_burst())
    first = [c for c in served if c[0] == "defspr"]
    assert 0 < len(first) < N, "a burst is spread across frames, not all shipped at once"
    shipped = set(c[1] for c in first)
    for _ in range(N):                           # keep serving the same burst
        for c in server.served_frame(draw_burst()):
            if c[0] == "defspr":
                assert c[1] not in shipped, "each bitmap ships exactly once"
                shipped.add(c[1])
    assert len(shipped) == N, "every bitmap eventually ships"


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
# WebSocket transport (#41 transport swap): the RFC 6455 handshake + framing. These are
# the load-bearing pieces of the persistent live channel that replaced the per-frame HTTP
# poll; pure functions, so fully host-testable off-device.
# ---------------------------------------------------------------------------


def _mask_client_frame(payload, opcode=0x1, mask=b"\x37\xfa\x21\x3d"):
    """Build a MASKED client->server WebSocket frame (the shape a browser sends), so a test
    can feed ws_decode the real wire bytes. Mirrors RFC 6455 5.3: MASK bit set + 4-byte key,
    payload XOR mask[i%4]. Uses the 7/16/64-bit length form the size demands."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    b0 = 0x80 | (opcode & 0x0F)
    if n < 126:
        hdr = bytes((b0, 0x80 | n))
    elif n < 65536:
        hdr = bytes((b0, 0x80 | 126, (n >> 8) & 0xFF, n & 0xFF))
    else:
        hdr = bytes((b0, 0x80 | 127)) + bytes((n >> (8 * (7 - i))) & 0xFF for i in range(8))
    body = bytearray(payload)
    for i in range(n):
        body[i] ^= mask[i & 3]
    return hdr + mask + bytes(body)


def test_ws_accept_key_rfc6455_example():
    """The RFC 6455 4.2.2 worked example: key "dGhlIHNhbXBsZSBub25jZQ==" must produce accept
    "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" -- base64(sha1(key + magic GUID)). The handshake response
    must carry exactly that, plus the Upgrade/Connection switch + the 101 status line."""
    assert web.ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    resp = web.ws_handshake_response("dGhlIHNhbXBsZSBub25jZQ==")
    assert resp.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
    assert b"Upgrade: websocket\r\n" in resp
    assert b"Connection: Upgrade\r\n" in resp
    assert b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n" in resp


def test_ws_upgrade_header_detection():
    """The dispatcher sniffs a WebSocket upgrade off the raw request head: an Upgrade:
    websocket request is detected (case-insensitively) and its Sec-WebSocket-Key pulled out;
    a plain GET is not an upgrade."""
    raw = (b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
           b"Connection: Upgrade\r\nSec-WebSocket-Key: abc123==\r\n\r\n")
    assert web.is_ws_upgrade(raw) is True
    assert web.ws_header_key(raw) == "abc123=="
    plain = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
    assert web.is_ws_upgrade(plain) is False
    assert web.ws_header_key(plain) is None


def test_ws_encode_is_unmasked_fin_text():
    """A server->client frame is FIN+text (0x81), UNMASKED (no mask bit, no key), with the
    payload appended verbatim after the length byte(s)."""
    f = web.ws_encode("hi")               # 2 bytes < 126 -> 1-byte length
    assert f == b"\x81\x02hi"
    pong = web.ws_encode(b"\x01\x02", opcode=web.WS_OP_PONG)
    assert pong[0] == 0x8A and pong[1] == 0x02 and pong[2:] == b"\x01\x02"


def test_ws_encode_length_forms_7_16_64():
    """ws_encode picks the 7/16/64-bit length form by size (RFC 6455 5.2): <126 inline,
    <65536 a 126 marker + 2 bytes, else a 127 marker + 8 bytes. No mask in any form."""
    small = web.ws_encode(b"x" * 10)
    assert small[1] == 10                                   # 7-bit inline
    mid = web.ws_encode(b"x" * 200)
    assert mid[1] == 126 and mid[2] == 0 and mid[3] == 200  # 16-bit ext
    big = web.ws_encode(b"x" * 70000)
    assert big[1] == 127                                    # 64-bit ext
    # 70000 = 0x011170 -> the low bytes of the 8-byte length.
    n = 0
    for i in range(8):
        n = (n << 8) | big[2 + i]
    assert n == 70000


def test_ws_decode_roundtrip_masked_text():
    """A masked client text frame decodes back to (text opcode, the original payload, the
    full frame length consumed) -- the XOR-unmask must invert the browser's masking."""
    wire = _mask_client_frame('{"events":[{"type":"hold","name":"left","down":true}]}')
    op, payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT
    assert json.loads(payload.decode("utf-8"))["events"][0]["name"] == "left"
    assert consumed == len(wire)


def test_ws_decode_16bit_and_64bit_length_paths():
    """The 126 (16-bit) and 127 (64-bit, here a small value in the wide field) extended
    length forms both decode -- the parser must read the right number of length bytes."""
    mid_payload = "e" * 300                                  # > 125 -> 16-bit length
    op, payload, consumed = web.ws_decode(_mask_client_frame(mid_payload))
    assert op == web.WS_OP_TEXT and payload.decode("utf-8") == mid_payload and consumed > 300
    # Force the 64-bit form even for a small payload, like some clients do.
    p = b"hello"
    mask = b"\x01\x02\x03\x04"
    hdr = bytes((0x81, 0x80 | 127)) + bytes((len(p) >> (8 * (7 - i))) & 0xFF for i in range(8))
    body = bytearray(p)
    for i in range(len(p)):
        body[i] ^= mask[i & 3]
    wire = hdr + mask + bytes(body)
    op, payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT and payload == b"hello" and consumed == len(wire)


def test_ws_decode_incomplete_frame_yields_none():
    """A frame split across reads -> ws_decode returns (None, None, 0) so the conn keeps the
    partial bytes and retries next iteration (the cross-iteration buffering invariant). Every
    truncation point (header, ext-length, mask, payload) must be 'not yet', never a misparse."""
    wire = _mask_client_frame("x" * 300)        # uses the 16-bit length form
    for cut in (1, 2, 3, 5, 8, len(wire) - 1):
        assert web.ws_decode(wire[:cut]) == (None, None, 0), cut
    # The whole frame decodes once complete.
    op, _payload, consumed = web.ws_decode(wire)
    assert op == web.WS_OP_TEXT and consumed == len(wire)


def test_ws_decode_unmasked_client_frame_is_protocol_error():
    """A client frame MUST be masked (RFC 6455 5.3); an UNMASKED one is a protocol error
    (-1) so the conn is dropped rather than trusted. (ws_encode makes unmasked SERVER
    frames -- feeding one back in proves the mask-required guard.)"""
    server_frame = web.ws_encode("not a client frame")
    op, payload, consumed = web.ws_decode(server_frame)
    assert op == -1 and payload is None and consumed == 0


def test_ws_decode_oversize_frame_is_protocol_error():
    """A frame claiming more than WS_MAX_FRAME bytes is rejected (-1) BEFORE buffering its
    payload, so a malformed/hostile client can't make us wait on an unboundedly large frame."""
    n = web.WS_MAX_FRAME + 1
    # A masked 64-bit-length header advertising n bytes (we don't even supply the payload).
    hdr = bytes((0x81, 0x80 | 127)) + bytes((n >> (8 * (7 - i))) & 0xFF for i in range(8))
    wire = hdr + b"\x00\x00\x00\x00"            # mask key, no payload
    op, payload, consumed = web.ws_decode(wire)
    assert op == -1 and consumed == 0


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
        return (self._cmds, "Demo", None)

    def apply(self, events):
        self.applied.extend(events)


class _FakeWSConn:
    """A stand-in for moy_webserver._WSConn so a test can mark the server's persistent
    WebSocket 'connected' (the recording/stream-mode liveness gate, which now keys off a
    live WS conn instead of a recent /frame poll) without a real socket. last_recv = now
    keeps recording_wanted() True; set it in the past to simulate an idle/dead client."""

    def __init__(self, now=None):
        self.alive = True
        self.last_recv = web.ticks_ms() if now is None else now


def _make_live(srv, now=None):
    """Mark the server's WS channel live so recording_wanted()/stream_mode() are True --
    the WS twin of 'a browser just polled /frame' in the old poll-transport tests."""
    srv._ws = _FakeWSConn(now)
    return srv._ws


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


def test_server_serves_index_html(server):
    _srv, _prov, host, port = server
    status, ctype, body = _get(host, port, "/")
    assert status == 200 and "text/html" in ctype
    text = body.decode("utf-8")
    # The embedded page is the replayer thin client -- it fetches /assets once over HTTP
    # then opens the persistent WebSocket (/ws) for the live channel.
    assert "<canvas" in text
    assert "/assets" in text and "/ws" in text and "WebSocket" in text


def test_server_serves_assets(server):
    _srv, _prov, host, port = server
    status, ctype, body = _get(host, port, "/assets")
    assert status == 200 and "application/json" in ctype
    a = json.loads(body)
    assert a["w"] == WIDTH and len(a["palette"]) == len(PAL565)
    assert a["font"]["w"] == 8 and a["cart"] == "Demo"


# (The legacy HTTP poll endpoints -- GET/POST /frame + POST /input -- were REMOVED from the
# device server; the live channel is WebSocket-only now. The frame-push + input-drain path is
# covered end-to-end by test_ws_end_to_end_over_localhost + the _WSConn buffering/ping tests
# below, and a /frame or /input request now falls through to the 404 branch, exercised by
# test_server_404_for_unknown_path.)


def test_ws_conn_buffers_partial_frames_across_reads():
    """The cross-iteration buffering invariant (the tricky part): a _WSConn over a socket
    pair must yield a complete inbound frame ONLY once all its bytes have arrived, retaining
    the partial remainder between reads. Feed the masked frame ONE byte at a time and assert
    drain_input() returns nothing until the last byte, then exactly the decoded payload."""
    import socket as _sk
    a, b = _sk.socketpair()
    try:
        conn = web._WSConn(a)                  # the device side (non-blocking reads)
        wire = _mask_client_frame('{"events":[{"type":"press","name":"run"}]}')
        # All but the last byte: no complete frame yet (each drain returns []).
        for byte in wire[:-1]:
            b.send(bytes((byte,)))
            assert conn.drain_input() == [], "an incomplete frame must not decode early"
        # The final byte completes it -> exactly one decoded text payload.
        b.send(bytes((wire[-1],)))
        got = conn.drain_input()
        assert len(got) == 1
        assert json.loads(got[0].decode("utf-8"))["events"][0]["name"] == "run"
        assert conn.alive is True
    finally:
        a.close()
        b.close()


def test_ws_conn_ping_is_answered_with_pong():
    """A WS ping (opcode 0x9) is answered inline with a pong (0xA) carrying the same payload,
    and is NOT surfaced as input -- keepalive handled transparently in the conn."""
    import socket as _sk
    a, b = _sk.socketpair()
    try:
        conn = web._WSConn(a)
        b.send(_mask_client_frame(b"pingdata", opcode=web.WS_OP_PING))
        assert conn.drain_input() == [], "a ping is not input"
        # The device side should have sent an UNMASKED pong back to b.
        b.setblocking(False)
        time.sleep(0.05)
        reply = b.recv(64)
        op, payload, _ = (reply[0] & 0x0F, reply[2:], 0) if (reply[1] & 0x80) == 0 else (None, None, 0)
        assert op == web.WS_OP_PONG and payload == b"pingdata"
    finally:
        a.close()
        b.close()


def test_ws_end_to_end_over_localhost():
    """A real WebSocket round-trip against the live server (when the `websockets` client lib
    is available): connect to /ws, send an input batch UP, receive a pushed frame DOWN, and
    assert the frame replays to pixels + the input reached the provider. This exercises the
    actual handshake + framing + the server's _service_ws push/drain over a real socket --
    everything but the device's MicroPython socket stack + WiFi."""
    try:
        import asyncio
        import websockets
    except Exception:  # noqa: BLE001 -- client lib not installed in this CI
        pytest.skip("websockets client lib not available")

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

    async def _run():
        uri = "ws://127.0.0.1:%d/ws" % port
        async with websockets.connect(uri) as ws:
            # Input UP the socket.
            await ws.send(json.dumps({"events": [{"type": "hold", "name": "up", "down": True}]}))
            # Frame(s) PUSH down; take the first.
            txt = await asyncio.wait_for(ws.recv(), timeout=5)
            return txt

    try:
        txt = asyncio.get_event_loop().run_until_complete(_run())
    finally:
        stop.set()
        t.join(timeout=2)
        srv.stop()

    f = json.loads(txt)
    assert f["cmds"][0] == ["cls", 1] and f["cart"] == "Demo"
    cv = Canvas(WIDTH, HEIGHT)
    replay_diet(f["cmds"], cv)
    assert len(set(cv.buf)) > 1, "the pushed frame must replay to a non-blank screen"
    # The input we sent up must have reached the provider.
    deadline = time.time() + 1
    while not prov.applied and time.time() < deadline:
        time.sleep(0.01)
    assert prov.applied and prov.applied[0]["name"] == "up"


def test_apply_events_routes_hold_through_on_hold_hook():
    """`hold` events must go through on_hold (which the loop re-asserts in feed_input AFTER
    keyboard.poll) -- NOT a direct set_button, which the per-frame keyboard poll would wipe
    before the cart reads btn() (the joystick/WASD-not-reacting bug, #41). Falls back to a
    direct set_button when no hook is wired (back-compat)."""
    class _Inp:
        def __init__(self):
            self.calls = []

        def set_button(self, n, v):
            self.calls.append((n, v))

    held = []
    inp = _Inp()
    web.apply_events([{"type": "hold", "name": "left", "down": True}], inp, None,
                     on_hold=lambda n, d: held.append((n, d)))
    assert held == [("left", True)] and inp.calls == []   # routed to the hook, not set_button
    web.apply_events([{"type": "hold", "name": "right", "down": True}], inp, None)
    assert inp.calls == [("right", True)]                 # no hook -> direct (back-compat)


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


def test_server_frame_cap_skips_between_intervals(monkeypatch):
    """The fps cap (#41) decouples the web stream from the cart: even with a browser live,
    begin_frame() RECORDS at most one frame per WEB_FRAME_INTERVAL_MS and leaves the
    recorder DISABLED (pure pass-through) on the in-between frames. A skipped frame is a
    skipped frame -- the gate is decided once, so a frame records completely or not at all."""
    clock = {"t": 100000}
    monkeypatch.setattr(web, "ticks_ms", lambda: clock["t"])
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    srv = web.WebServer(rec, _FakeProvider(), port=0)
    assert srv.start("127.0.0.1") is True
    try:
        # Make a browser "live": a WS client is connected (the WS-transport liveness gate).
        wsc = _make_live(srv, clock["t"])
        # First frame with a live client records (last_record_ms starts at 0, far in the past).
        srv.begin_frame()
        assert rec.enabled is True
        # A few ms later (within the cap interval) -> skipped, pure pass-through.
        clock["t"] += web.WEB_FRAME_INTERVAL_MS // 2
        wsc.last_recv = clock["t"]                # client still active
        srv.begin_frame()
        assert rec.enabled is False, "within the cap interval the frame is not recorded"
        # Past the interval -> records again.
        clock["t"] += web.WEB_FRAME_INTERVAL_MS
        wsc.last_recv = clock["t"]
        srv.begin_frame()
        assert rec.enabled is True, "after the interval the next frame records"
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# STREAM MODE (#41 30fps lever): headless while a browser is actively playing -- the Tee
# RECORDS commands but does NOT forward draws to the real panel canvas, and run_desktop
# skips the panel flush. With NO browser, behaviour is identical to today (forward + flush).
# ---------------------------------------------------------------------------


def test_stream_mode_gate_records_only_when_a_browser_is_live(monkeypatch):
    """begin_frame() sets recorder.record_only True ONLY for a recorded frame while a
    browser is live (stream_mode). A skipped/in-between frame and the no-browser case leave
    record_only False, so record_only -> enabled always holds (the invariant the Tee/flush
    skip rely on). This is the gate that turns the device headless ONLY when watched."""
    clock = {"t": 100000}
    monkeypatch.setattr(web, "ticks_ms", lambda: clock["t"])
    rec = web.DrawRecorder(WIDTH, HEIGHT)
    srv = web.WebServer(rec, _FakeProvider(), port=0)
    # No socket yet -> not recording, not streaming.
    srv.begin_frame()
    assert rec.enabled is False and rec.record_only is False
    assert srv.stream_mode() is False
    assert srv.start("127.0.0.1") is True
    try:
        # No WS client connected -> a browser isn't live -> still no stream.
        srv.begin_frame()
        assert rec.enabled is False and rec.record_only is False
        assert srv.stream_mode() is False
        # A browser's WebSocket connects -> the recorded frame goes headless (record_only True).
        wsc = _make_live(srv, clock["t"])
        srv.begin_frame()
        assert rec.enabled is True and rec.record_only is True
        assert srv.stream_mode() is True
        # Within the fps-cap interval the frame is SKIPPED for RECORDING (enabled False),
        # but the device STAYS headless (record_only True) -- decoupled from the cap so the
        # loop's stream-mode edge fires once instead of flapping the panel on every capped
        # frame (#41 lag bug). A capped headless frame is a Tee no-op (skips panel, no record).
        clock["t"] += web.WEB_FRAME_INTERVAL_MS // 2
        wsc.last_recv = clock["t"]
        srv.begin_frame()
        assert rec.enabled is False and rec.record_only is True
    finally:
        srv.stop()
    # stop() clears both flags (a view toggled off mid-stream must resume the panel).
    assert rec.enabled is False and rec.record_only is False


def test_stream_mode_tee_records_only_skips_panel_forward():
    """While record_only, the Tee RECORDS draw commands but does NOT forward them to the
    real DeviceCanvas (no rasterization -> the device skips its own panel render). The cheap
    STATE ops (camera/clip/pal/palt/reset_state) STILL forward so the canvas state +
    camera()'s return stay correct. With record_only False it's the normal forward+record."""
    tee, rec, dev = _build_tee()
    # Normal recording (a browser live, but NOT streaming): forwards AND records.
    rec.enabled = True
    rec.record_only = False
    rec.begin()
    tee.cls(1)
    tee.rect(0, 0, 10, 10, 2)
    tee.spr(Image(2, 2, [1, 2, 3, 4], transparent=-1), 4, 4)
    rec.commit()
    assert dev.calls == 3, "not streaming -> every draw still reaches the panel canvas"
    assert [c[0] for c in rec.frame()] == ["cls", "rect", "spr"], "and is recorded"
    # STREAM MODE: record_only -> the pixel ops are recorded but NOT forwarded.
    dev.calls = 0
    rec.record_only = True
    rec.begin()
    tee.cls(3)
    tee.rect(1, 1, 5, 5, 4)
    tee.line(0, 0, 9, 9, 5)
    tee.circ(8, 8, 3, 6)
    tee.spr(Image(2, 2, [5, 6, 7, 8], transparent=-1), 2, 2)
    tee.print("HI", 0, 0, 7)
    rec.commit()
    assert dev.calls == 0, "streaming -> NO draw reaches the panel (device goes headless)"
    assert [c[0] for c in rec.frame()] == ["cls", "rect", "line", "circ", "spr", "print"], (
        "but every op is still recorded for the browser")
    # Cheap state ops STILL forward (they don't rasterize; camera()'s return must be right).
    dev.calls = 0
    tee.camera(2, 3)
    tee.clip(0, 0, 10, 10)
    tee.pal(1, 2)
    tee.palt(3, True)
    tee.reset_state()
    assert dev.calls == 5, "state ops forward even while streaming (canvas state stays live)"


def test_stream_mode_off_path_is_byte_for_byte_normal():
    """The whole point: with NO browser (record_only never set, enabled False) the Tee is
    the unchanged pass-through -- forwards every draw, records nothing, zero allocation."""
    tee, rec, dev = _build_tee()
    assert rec.enabled is False and rec.record_only is False
    tee.cls(1)
    tee.rect(0, 0, 10, 10, 2)
    tee.spr(Image(2, 2, [1, 2, 3, 4], transparent=-1), 0, 0)
    tee.print("x", 0, 0, 7)
    assert dev.calls == 4, "every draw reaches the panel, exactly as today"
    rec.begin(); rec.commit()
    assert rec.frame() == [], "nothing recorded with no browser"


def test_page_html_served_script_is_valid_js():
    """The embedded browser page must be valid JS *as served* -- i.e. against the
    EVALUATED PAGE_HTML, not a hand copy. A non-raw Python string once turned a JS-string
    `\\n` into a real newline, breaking the page with "Invalid or unexpected token"
    (#41). node --check the actual served <script> so that can't regress. Skips when node
    isn't installed (CI without node)."""
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    m = re.search(r"<script>(.*)</script>", web.PAGE_HTML, re.S)
    assert m, "PAGE_HTML has a <script> block"
    js = m.group(1)
    # The bug was a raw newline inside a JS string literal; guard the specific shape too.
    assert "KB/f\natlas" not in js, "raw newline inside the HUD string (the #41 regression)"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, "served page JS is invalid:\n" + r.stderr
    finally:
        os.unlink(path)
