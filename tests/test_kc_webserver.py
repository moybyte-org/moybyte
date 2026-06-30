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

WIDTH, HEIGHT = 320, 240


# ---------------------------------------------------------------------------
# The PAYLOAD-DIET reference replayer (#41): the Python twin of the browser JS in
# kc_webserver.PAGE_HTML (the defspr / spr-by-index / map / settiles format). It mirrors
# the JS exactly so the host test and the browser share the same replay LOGIC, proving the
# device's recorded stream reproduces the panel pixel-for-pixel. Sprites + map cells go
# through Canvas.spr (the SAME rasterizer the device/host draw with -- camera/clip/pal/
# palt/scale/flip), and the atlas/sheet/tilemap caches mirror the browser's ATL/SHEET/TM.
# `assets` (optional) seeds the sheet + tilemap exactly as the browser's GET /assets does.
# ---------------------------------------------------------------------------


def replay_diet(commands, canvas, assets=None):
    atlas = {}                                  # index -> Image (browser's ATL)
    sheet_pix = sheet_cols = sheet_tile = sheet_w = None
    tm = None                                   # {"w","h","cells"} (browser's TM)
    if assets is not None:
        sh = assets.get("sheet")
        if sh is not None:
            sheet_pix = list(sh["pix"])
            sheet_cols = sh["cols"]
            sheet_tile = sh["tile"]
            sheet_w = sh["w"]
        tmp = assets.get("tilemap")
        if tmp is not None:
            tm = {"w": tmp["w"], "h": tmp["h"], "cells": list(tmp["cells"])}

    def _tile_image(tid, colorkey):
        # Build a tile's Image from the cached sheet, exactly like the browser's mp() slice
        # (and the device SpriteSheet.tile_image): row-major tile origin, colorkey transparent.
        ox = (tid % sheet_cols) * sheet_tile
        oy = (tid // sheet_cols) * sheet_tile
        pix = []
        for ly in range(sheet_tile):
            base = (oy + ly) * sheet_w + ox
            for lx in range(sheet_tile):
                pix.append(sheet_pix[base + lx])
        return Image(sheet_tile, sheet_tile, pix, transparent=colorkey)

    for cmd in commands:
        op = cmd[0]
        if op == "cls":
            canvas.cls(cmd[1])
        elif op == "pix":
            canvas.pix(cmd[1], cmd[2], cmd[3])
        elif op == "line":
            canvas.line(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "rect":
            canvas.rect(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "rectb":
            canvas.rectb(cmd[1], cmd[2], cmd[3], cmd[4], cmd[5])
        elif op == "circ":
            canvas.circ(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "circb":
            canvas.circb(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "defspr":
            idx, w, h, t, pix = cmd[1:6]
            atlas[idx] = Image(w, h, list(pix), transparent=t)
        elif op == "spr":
            idx, x, y, scale = cmd[1:5]
            flip = cmd[5] if len(cmd) > 5 else 0
            im = atlas.get(idx)
            if im is not None:
                canvas.spr(im, x, y, scale, flip)
        elif op == "settiles":
            tm = {"w": cmd[1], "h": cmd[2], "cells": list(cmd[3])}
        elif op == "map":
            mx, my, w, h, sx, sy, scale, colorkey = cmd[1:9]
            if sheet_pix is not None and tm is not None:
                if scale < 1:
                    scale = 1
                step = sheet_tile * scale
                cache = {}
                for cy in range(h):
                    ty = my + cy
                    for cx in range(w):
                        gx = mx + cx
                        if 0 <= gx < tm["w"] and 0 <= ty < tm["h"]:
                            tid = tm["cells"][ty * tm["w"] + gx] - 1
                        else:
                            tid = -1
                        if tid < 0:
                            continue
                        im = cache.get(tid)
                        if im is None:
                            im = _tile_image(tid, colorkey)
                            cache[tid] = im
                        canvas.spr(im, sx + cx * step, sy + cy * step, scale)
        elif op == "print":
            canvas.print(cmd[1], cmd[2], cmd[3], cmd[4])
        elif op == "reset_state":
            canvas.reset_state()
        elif op == "camera":
            canvas.camera(cmd[1], cmd[2])
        elif op == "clip":
            if len(cmd) > 1:
                canvas.clip(cmd[1], cmd[2], cmd[3], cmd[4])
            else:
                canvas.clip()
        elif op == "pal":
            if len(cmd) > 1:
                canvas.pal(cmd[1], cmd[2])
            else:
                canvas.pal()
        elif op == "palt":
            if len(cmd) > 1:
                canvas.palt(cmd[1], bool(cmd[2]))
            else:
                canvas.palt()
        # unknown ops ignored (forward-compatible)
    return canvas

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
    replay_diet(cmds, cv)
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


def test_server_frame_post_applies_input_and_returns_frame(server):
    """The page now sends input + fetches the frame in ONE round-trip (#41): a POST /frame
    with an events body applies the input (provider.apply) AND returns the frame -- halving
    requests/tick vs a separate POST /input + GET /frame, which stalled the view per input."""
    _srv, prov, host, port = server
    status, body = _post(host, port, "/frame",
                         {"events": [{"type": "hold", "name": "left", "down": True}]})
    assert status == 200
    f = json.loads(body)
    assert f["cmds"][0] == ["cls", 1] and f["cart"] == "Demo"   # returned the frame...
    deadline = time.time() + 3
    while not prov.applied and time.time() < deadline:
        time.sleep(0.01)
    assert prov.applied and prov.applied[0]["name"] == "left"   # ...AND applied the input


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
        # Make a browser "live": pretend a /frame fetch just happened.
        srv._last_frame_req = clock["t"]
        # First frame after a fetch records (last_record_ms starts at 0, far in the past).
        srv.begin_frame()
        assert rec.enabled is True
        # A few ms later (within the cap interval) -> skipped, pure pass-through.
        clock["t"] += web.WEB_FRAME_INTERVAL_MS // 2
        srv._last_frame_req = clock["t"]          # browser still polling
        srv.begin_frame()
        assert rec.enabled is False, "within the cap interval the frame is not recorded"
        # Past the interval -> records again.
        clock["t"] += web.WEB_FRAME_INTERVAL_MS
        srv._last_frame_req = clock["t"]
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
        # No /frame fetched -> a browser isn't live -> still no stream.
        srv.begin_frame()
        assert rec.enabled is False and rec.record_only is False
        assert srv.stream_mode() is False
        # A browser polls -> the recorded frame goes headless (record_only True).
        srv._last_frame_req = clock["t"]
        srv.begin_frame()
        assert rec.enabled is True and rec.record_only is True
        assert srv.stream_mode() is True
        # Within the fps-cap interval the frame is SKIPPED for RECORDING (enabled False),
        # but the device STAYS headless (record_only True) -- decoupled from the cap so the
        # loop's stream-mode edge fires once instead of flapping the panel on every capped
        # frame (#41 lag bug). A capped headless frame is a Tee no-op (skips panel, no record).
        clock["t"] += web.WEB_FRAME_INTERVAL_MS // 2
        srv._last_frame_req = clock["t"]
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
