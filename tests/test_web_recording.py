"""The shared draw-command recording stack (runtime/web_view.py) -- the WASM
HEAD's rendering substrate until stage 4 re-rasters.

Salvaged from the old tests/test_moy_webserver.py when the 2026-08 streaming
sunset (docs/moycore_plan_2026-08.md 3.2) deleted the device web view and the
host web console. What these tests pin is the machinery the wasm head still
stands on: the recorder command format, the recorder/replayer pixel parity
(the property pageshot cross-checks against the real page), the off-screen
LAYER ship-once lane (deflayer -- the windowed web tier's window buffers), the
per-WM-surface partition + SurfaceDelta (#76 -- web_boot's push path), the
payload builders, apply_events, and the page JS.

Deliberately NOT ported: the atlas/defspr ship-once lane's tests and the diet
["map"]/["settiles"] op tests -- their only PRODUCER was the device TeeCanvas
(the wasm head's CommandCanvas records self-contained sprs and expands map()
per cell), so those lanes are orphaned code that dies with the stack at
stage 4. The recorder here runs self_contained=True, the live configuration.

The Tee double below is a TEST INSTRUMENT, not shipped code: it forwards draws
to a real rasterizing Canvas AND to the recorder, which is what lets stream
replay be pinned against raster truth.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from runtime import font as _font
from runtime import palette as _pal
from runtime import web_view
from runtime.canvas import Canvas, Image
from runtime.editors import SpriteSheet

WIDTH, HEIGHT = 320, 240

# The device's canonical RGB565 MOY64 LUT (a copy of moy_runtime.PAL565 -- the
# host can't import the device backend, which pulls in framebuf/machine).
PAL565 = (
    0x0000, 0x194A, 0x792A, 0x042A, 0xAA86, 0x5AA9, 0xC618, 0xFF9D,
    0xF809, 0xFD00, 0xFF64, 0x0726, 0x2D7F, 0x83B3, 0xFBB5, 0xFE75,
)

replay_diet = web_view.replay_to_canvas


class _RecordingTee:
    """Forward every draw to a real rasterizing Canvas AND the recorder --
    the parity harness. (The shipped device TeeCanvas this mirrors died in the
    sunset; the pin -- stream replays == raster truth -- outlives it.)"""

    def __init__(self, real, rec):
        self._c = real
        self._r = rec
        self.w = real.w
        self.h = real.h

    def _fwd(name):  # noqa: N805 -- tiny local factory
        def m(self, *a):
            getattr(self._c, name)(*a)
            getattr(self._r, name)(*a)
        return m

    cls = _fwd("cls")
    pix = _fwd("pix")
    line = _fwd("line")
    rect = _fwd("rect")
    rectb = _fwd("rectb")
    circ = _fwd("circ")
    circb = _fwd("circb")
    camera = _fwd("camera")
    clip = _fwd("clip")
    pal = _fwd("pal")
    palt = _fwd("palt")
    reset_state = _fwd("reset_state")
    del _fwd

    def spr(self, img, x, y, scale=1, flip=0):
        self._c.spr(img, x, y, scale, flip)
        self._r.spr(img, x, y, scale, flip)

    def print(self, s, x, y, c, scale=2):
        self._c.print(s, x, y, c, scale)
        self._r.print(s, x, y, c)

    def new_layer(self, w, h):
        return web_view.RecordingLayer(self._c.new_layer(w, h), self._r)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        layer._end_batch()
        self._c.blit_window_from(layer._c, int(cam_x), int(cam_y))
        self._r.blit_layer_window(layer, int(cam_x), int(cam_y))

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        layer._end_batch()
        self._c.blit_strip(layer._c, dst_x, dst_y)
        self._r.blit_layer_full(layer, dst_x, dst_y)


def _build(self_contained=True):
    raster = Canvas(WIDTH, HEIGHT)
    rec = web_view.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.self_contained = self_contained
    tee = _RecordingTee(raster, rec)
    served = web_view.ServedState(rec)
    return raster, rec, tee, served


def _served(rec, st):
    return st.served_frame(rec.frame())


# ---------------------------------------------------------------------------
# The recorder core: command format + frame swap.
# ---------------------------------------------------------------------------


def test_recorder_command_format_is_the_wire_truth():
    """The ONE command format the page replays -- literal tuples, so a shape
    drift in either producer (recorder) or consumer (replayer/page) fails here."""
    _raster, rec, tee, _st = _build()
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
    assert rec.frame() == [
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


def test_recorder_begin_commit_swap():
    """begin() starts a fresh frame; commit() publishes it; a partial frame is
    dropped by the next begin(). frame() is always the last COMMITTED frame."""
    rec = web_view.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.begin()
    rec.cls(1)
    rec.commit()
    assert rec.frame() == [["cls", 1]]
    rec.begin()
    rec.rect(0, 0, 1, 1, 2)
    assert rec.frame() == [["cls", 1]]
    rec.commit()
    assert rec.frame() == [["rect", 0, 0, 1, 1, 2]]


# ---------------------------------------------------------------------------
# The faithfulness cross-check: recorded stream == raster truth.
# ---------------------------------------------------------------------------


def test_recorded_stream_replays_pixel_identical_to_the_raster():
    """Drive a varied frame through the parity tee, replay the served stream
    onto a fresh Canvas, and require PIXEL IDENTITY with the raster half --
    sprites with transparency + scaling + flip included (self-contained
    pixels, the live wasm-head configuration)."""
    raster, rec, tee, st = _build()
    rec.begin()
    tee.cls(1)
    tee.rect(10, 10, 60, 40, 8)
    tee.rectb(80, 10, 60, 40, 7)
    tee.line(0, 239, 319, 0, 6)
    tee.circ(160, 120, 25, 11)
    tee.circb(220, 120, 18, 9)
    tee.pix(5, 5, 10)
    spr = Image(4, 4, [0, 8, 8, 0, 8, 7, 7, 8, 8, 7, 7, 8, 0, 8, 8, 0],
                transparent=0)
    tee.spr(spr, 100, 100, 3)
    tee.spr(spr, 150, 100, 2, 1)
    tee.print("WASM HEAD", 8, 220, 7)
    rec.commit()
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(_served(rec, st), replayed)
    assert bytes(raster.buf) == bytes(replayed.buf)
    assert len(set(replayed.buf)) > 1


# ---------------------------------------------------------------------------
# Off-screen LAYERS: the deflayer ship-once lane (windowed web tier buffers).
# ---------------------------------------------------------------------------


def test_scroll_layer_draw_layer_replays_pixel_identical_at_offset():
    raster, rec, tee, st = _build()
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(1)
    lay.rect(0, HEIGHT - 40, WIDTH * 2, 40, 3)
    for gx in range(0, WIDTH * 2, 60):
        lay.circ(gx + 10, 40, 8, 7)
        lay.rect(gx + 30, HEIGHT - 60, 6, 20, 4)
    rec.begin()
    tee.cls(0)
    tee.blit_window_from(lay, 137, 0)
    tee.rect(150, 100, 12, 22, 8)
    rec.commit()
    served = _served(rec, st)
    assert [c[0] for c in served].count("deflayer") == 1
    assert ["blit_layer", lay.id, 137, 0] in served
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(served, replayed)
    assert bytes(raster.buf) == bytes(replayed.buf)
    assert len(set(replayed.buf)) > 1


def test_layer_ships_once_then_reference_only_across_served_frames():
    raster, rec, tee, st = _build()
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(4)
    lay.rect(0, 100, WIDTH * 2, 40, 8)
    layers = {}                                  # the browser's LAY cache
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    f1 = _served(rec, st)
    assert [c[0] for c in f1].count("deflayer") == 1
    r1 = Canvas(WIDTH, HEIGHT)
    replay_diet(f1, r1, layers=layers)
    assert bytes(r1.buf) == bytes(raster.buf)
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 64, 0); rec.commit()
    f2 = _served(rec, st)
    assert not any(c[0] == "deflayer" for c in f2), "a shipped layer is not re-sent"
    r2 = Canvas(WIDTH, HEIGHT)
    replay_diet(f2, r2, layers=layers)
    assert bytes(r2.buf) == bytes(raster.buf)


def test_layer_reships_deflayer_on_redraw():
    _raster, rec, tee, st = _build()
    strip = tee.new_layer(WIDTH, 18)
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:34", 280, 3, 6, 2)
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    f1 = _served(rec, st)
    assert "12:34" in str(next(c for c in f1 if c[0] == "deflayer"))
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    assert not any(c[0] == "deflayer" for c in _served(rec, st))
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:35", 280, 3, 6, 2)
    rec.begin(); tee.cls(0); tee.blit_strip(strip, 0, 0); rec.commit()
    dl = next((c for c in _served(rec, st) if c[0] == "deflayer"), None)
    assert dl is not None and "12:35" in str(dl)


def test_layer_served_set_resets_on_assets():
    _raster, rec, tee, st = _build()
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(2)
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, st))
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert not any(c[0] == "deflayer" for c in _served(rec, st))
    st.reset()                                   # /assets re-served
    rec.begin(); tee.cls(0); tee.blit_window_from(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, st))


def test_layer_dropped_on_reset_atlas_reships_via_gen():
    _raster, rec, tee, st = _build()
    lay = tee.new_layer(WIDTH, 18)
    lay.cls(3)
    rec.begin(); tee.cls(0); tee.blit_strip(lay, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, st))
    rec.reset_atlas()                            # cart change
    assert rec._layers == []
    lay2 = tee.new_layer(WIDTH, 18)
    assert lay2.id == 0
    lay2.cls(5)
    rec.begin(); tee.cls(0); tee.blit_strip(lay2, 0, 0); rec.commit()
    assert any(c[0] == "deflayer" for c in _served(rec, st))


def test_cached_bar_strip_reused_across_cart_change_self_heals_id_collision():
    """THE on-hardware bug (#54/#41): a cached layer outliving reset_atlas
    collides with the new cart's id 0 unless _ensure_registered re-ids it."""
    raster, rec, tee, st = _build()
    bar = tee.new_layer(WIDTH, 18)
    bar.rect(0, 0, WIDTH, 18, 7)
    bar.print("12:34", 280, 3, 6, 2)
    rec.begin(); tee.cls(0); tee.blit_strip(bar, 0, 0); rec.commit()
    _served(rec, st)
    assert bar.id == 0
    rec.reset_atlas()
    scroll = tee.new_layer(WIDTH * 2, HEIGHT)
    scroll.cls(3)
    scroll.rect(0, 100, WIDTH * 2, 40, 8)
    assert scroll.id == 0
    rec.begin()
    tee.cls(0)
    tee.blit_window_from(scroll, 0, 0)
    tee.blit_strip(bar, 0, 0)
    rec.commit()
    served = _served(rec, st)
    assert bar.id != scroll.id
    assert [c[0] for c in served].count("deflayer") == 2
    replayed = Canvas(WIDTH, HEIGHT)
    replay_diet(served, replayed)
    assert bytes(replayed.buf) == bytes(raster.buf)


# ---------------------------------------------------------------------------
# Per-WM-surface partition (Stage 9) + the #76 SurfaceDelta -- web_boot's push.
# ---------------------------------------------------------------------------


def test_recorder_partitions_the_frame_into_wm_surfaces():
    rec = web_view.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.surfaces_on = True
    rec.begin()
    rec.begin_surface("desktop", "game")
    rec.cls(1)
    rec.rect(10, 10, 20, 20, 8)
    rec.begin_surface("perf", "game")
    rec.print("60", 300, 230, 7)
    rec.begin_surface("cursor", "system")
    rec.spr(Image(2, 2, [7, 7, 7, 7], transparent=-1), 100, 100)
    rec.commit()
    surfs = rec.frame_surfaces()
    assert [(s[0], s[1]) for s in surfs] == [
        ("desktop", "game"), ("perf", "game"), ("cursor", "system")]
    assert [c[0] for c in surfs[0][2]] == ["cls", "rect"]
    assert [c[0] for c in surfs[1][2]] == ["print"]
    assert [c[0] for c in surfs[2][2]] == ["spr"]
    assert [c for s in surfs for c in s[2]] == rec.frame()


def test_recorder_surfaces_off_is_flat_and_byte_identical():
    rec = web_view.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    assert rec.surfaces_on is False
    rec.begin()
    rec.begin_surface("desktop", "game")
    rec.cls(2)
    rec.rect(0, 0, 5, 5, 4)
    rec.commit()
    assert rec.frame_surfaces() is None
    assert rec._surf_marks == []
    assert rec.frame() == [["cls", 2], ["rect", 0, 0, 5, 5, 4]]


def test_recorder_pre_surface_captures_draws_before_the_first_mark():
    rec = web_view.DrawRecorder(WIDTH, HEIGHT)
    rec.enabled = True
    rec.surfaces_on = True
    rec.begin()
    rec.cls(0)
    rec.begin_surface("launcher", "system")
    rec.rect(1, 1, 2, 2, 3)
    rec.commit()
    surfs = rec.frame_surfaces()
    assert surfs[0][0] == "_pre" and [c[0] for c in surfs[0][2]] == ["cls"]
    assert surfs[1][0] == "launcher" and [c[0] for c in surfs[1][2]] == ["rect"]
    assert [c for s in surfs for c in s[2]] == rec.frame()


def test_surface_delta_stubs_unchanged_surfaces():
    delta = web_view.SurfaceDelta()
    frame1 = [
        {"id": "_defs", "domain": "system", "cmds": [["defspr", 0, 1, 1, -1, [7]]]},
        {"id": "launcher", "domain": "system",
         "cmds": [["cls", 1], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire1 = delta.encode(frame1, gen=0)
    assert wire1 == frame1, "a fresh client gets every surface in full"
    frame2 = [
        {"id": "launcher", "domain": "system",
         "cmds": [["cls", 1], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire2 = delta.encode(frame2, gen=0)
    assert wire2 == [{"id": "launcher", "domain": "system", "same": 1},
                     {"id": "cursor", "domain": "system", "same": 1}]
    frame3 = [
        {"id": "launcher", "domain": "system",
         "cmds": [["cls", 2], ["rect", 1, 1, 2, 2, 3]]},
        {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 5, 5, 1, 0]]},
    ]
    wire3 = delta.encode(frame3, gen=0)
    assert wire3[0] == frame3[0]
    assert wire3[1] == {"id": "cursor", "domain": "system", "same": 1}
    wire4 = delta.encode(frame3, gen=1)          # gen bump re-ships everything
    assert wire4 == frame3
    delta.reset()                                # fresh connection likewise
    assert delta.encode(frame3, gen=1) == frame3


def test_surface_delta_replay_pixel_identical_across_frames():
    raster, rec, tee, st = _build()
    rec.surfaces_on = True
    delta = web_view.SurfaceDelta()
    spr = Image(4, 4, [0, 8, 8, 0, 8, 7, 7, 8, 8, 7, 7, 8, 0, 8, 8, 0],
                transparent=0)

    def record(rect_x):
        rec.begin()
        rec.begin_surface("desktop", "game")
        tee.cls(1)
        tee.rect(rect_x, 10, 60, 40, 8)
        rec.begin_surface("bar", "system")
        tee.print("12:00", 8, 2, 7)
        tee.spr(spr, 300, 2, 1)
        rec.commit()
        _flat, dicts = st.served_surfaces(rec.frame(), rec.frame_surfaces())
        return delta.encode(dicts, gen=rec.atlas_gen)

    cache = {}
    atlas = {}
    wire1 = record(10)
    cv1 = Canvas(WIDTH, HEIGHT)
    web_view.replay_delta_surfaces_to_canvas(wire1, cache, cv1, atlas=atlas)
    assert bytes(cv1.buf) == bytes(raster.buf)
    wire2 = record(50)                           # desktop moved; bar unchanged
    ids = {s["id"]: s for s in wire2}
    assert "cmds" in ids["desktop"]
    assert ids["bar"].get("same") == 1
    cv2 = Canvas(WIDTH, HEIGHT)
    web_view.replay_delta_surfaces_to_canvas(wire2, cache, cv2, atlas=atlas)
    assert bytes(cv2.buf) == bytes(raster.buf)


def test_surface_scroll_layer_deflayer_rides_the_defs_surface():
    raster, rec, tee, st = _build()
    rec.surfaces_on = True
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(1)
    lay.rect(0, HEIGHT - 40, WIDTH * 2, 40, 3)
    for gx in range(0, WIDTH * 2, 60):
        lay.circ(gx + 10, 40, 8, 7)
    rec.begin()
    rec.begin_surface("desktop", "game")
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
    assert any(c[0] == "blit_layer" for c in surfaces[1]["cmds"])
    comp = Canvas(WIDTH, HEIGHT)
    web_view.replay_surfaces_to_canvas(surfaces, comp)
    assert bytes(comp.buf) == bytes(raster.buf)
    flatcv = Canvas(WIDTH, HEIGHT)
    replay_diet(served_flat, flatcv)
    assert bytes(flatcv.buf) == bytes(comp.buf)


def test_ws_client_state_keyframe_latch_policy():
    """WsClientState (web_boot's per-session policy object): arm_keyframe fires
    the dirty hook every poll until note_frame records a REAL served frame,
    then stops -- the black-until-tap reload hole stays closed."""
    st = web_view.WsClientState()
    fired = []
    for want in (1, 2):
        st.arm_keyframe(lambda: fired.append(1))
        assert len(fired) == want and not st.served_full
    st.note_frame(False)                         # an empty frame doesn't latch
    st.arm_keyframe(lambda: fired.append(1))
    assert len(fired) == 3 and not st.served_full
    st.note_frame(True)                          # a real frame closes the latch
    assert st.served_full
    st.arm_keyframe(lambda: fired.append(1))
    assert len(fired) == 3, "armed no more once served"


# ---------------------------------------------------------------------------
# Payload builders + the page.
# ---------------------------------------------------------------------------


def test_frame_payload_carries_surfaces_only_when_present():
    flat = web_view.frame_payload([["cls", 1]], "Demo")
    assert "surfaces" not in flat and flat["cmds"] == [["cls", 1]]
    json.dumps(flat)
    surfs = [{"id": "desktop", "domain": "game", "cmds": [["cls", 1]]},
             {"id": "cursor", "domain": "system", "cmds": [["spr", 0, 1, 2, 1, 0]]}]
    p = web_view.frame_payload([], "Demo", 2, surfaces=surfs)
    assert p["surfaces"] == surfs and p["gen"] == 2
    json.dumps(p)


def test_page_composites_per_surface_streams():
    page = web_view.PAGE_HTML
    assert "f.surfaces" in page
    assert "s.same" in page
    assert "SURF[s.id]=s.cmds" in page
    assert 'if(s.id!=="_defs")' in page
    assert "SURF={};HUD.unknown=0;}" in page
    assert "rep(f.cmds||[])" in page


def test_assets_payload_shape():
    a = web_view.assets_payload(WIDTH, HEIGHT, PAL565, None, None, None,
                                audio_rate=8000)
    assert a["w"] == WIDTH and a["h"] == HEIGHT
    assert len(a["palette"]) == len(PAL565)
    f = a["font"]
    assert f["first"] == _font.FIRST and f["w"] == _font.WIDTH \
        and f["h"] == _font.HEIGHT
    assert len(f["glyphs"]) == len(_font._FONT) // _font.WIDTH
    assert all(len(g) == _font.WIDTH for g in f["glyphs"])
    assert a["sheet"] is None and a["tilemap"] is None and a["cart"] is None
    assert a["audio_rate"] == 8000
    json.dumps(a)


def test_assets_palette_decodes_close_to_moy64():
    pal = web_view.palette_rgb(PAL565)
    assert pal[0] == [0, 0, 0]
    for i in range(len(PAL565)):
        for ch in range(3):
            assert abs(pal[i][ch] - _pal.MOY64[i][ch]) <= 8, (i, ch)


def test_assets_includes_sheet_when_a_cart_is_open():
    sheet = SpriteSheet(2, 2)
    a = web_view.assets_payload(WIDTH, HEIGHT, PAL565, sheet, None,
                                "Star Catcher")
    s = a["sheet"]
    assert s["cols"] == 2 and s["rows"] == 2 and s["tile"] == sheet.TILE
    assert len(s["pix"]) == s["w"] * s["h"]
    assert a["cart"] == "Star Catcher"


def test_frame_payload_shape():
    cmds = [["cls", 1], ["rect", 0, 0, 10, 10, 2]]
    p = web_view.frame_payload(cmds, "Pong")
    assert p["cmds"] == cmds and p["cart"] == "Pong" and p["audio"] == "" \
        and p["perf"] is None
    json.dumps(p)
    p2 = web_view.frame_payload(cmds, "Pong", 3, {"heap": 1234, "pf": 9})
    assert p2["gen"] == 3 and p2["perf"] == {"heap": 1234, "pf": 9}
    json.dumps(p2)


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
    web_view.apply_events([{"type": "down", "x": 30, "y": 40}], inp, ptr)
    assert (ptr.x, ptr.y, ptr.down, ptr.click) == (30, 40, True, True)
    web_view.apply_events([{"type": "move", "x": 50, "y": 60}], inp, ptr)
    assert (ptr.x, ptr.y, ptr.down) == (50, 60, True)
    web_view.apply_events([{"type": "up"}], inp, ptr)
    assert ptr.down is False


def test_apply_events_hold_button():
    inp, ptr = _FakeInput(), _FakePointer()
    web_view.apply_events([{"type": "hold", "name": "left", "down": True}], inp, ptr)
    assert "left" in inp.held
    web_view.apply_events([{"type": "hold", "name": "left", "down": False}], inp, ptr)
    assert "left" not in inp.held


def test_apply_events_unknown_button_ignored():
    inp, ptr = _FakeInput(), _FakePointer()
    web_view.apply_events([{"type": "hold", "name": "self_destruct", "down": True}],
                          inp, ptr)
    assert inp.held == set()


def test_apply_events_press_pan_key_esc_hooks():
    inp, ptr = _FakeInput(), _FakePointer()
    fired = {"press": [], "pan": [], "key": [], "esc": 0}
    web_view.apply_events(
        [
            {"type": "press", "name": "run"},
            {"type": "press", "name": "nope"},
            {"type": "pan", "dx": 1, "dy": -1},
            {"type": "key", "code": 0x41},
            {"type": "key", "code": 999},
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
    inp, ptr = _FakeInput(), _FakePointer()
    web_view.apply_events(
        [{"type": "down"}, "garbage", {"type": "hold", "name": "a", "down": True}],
        inp, ptr,
    )
    assert "a" in inp.held


def test_page_html_served_script_is_valid_js():
    """node --check the ACTUAL served <script> (a non-raw Python string once
    turned a JS `\\n` into a real newline, #41). Skips without node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    m = re.search(r"<script>(.*)</script>", web_view.PAGE_HTML, re.S)
    assert m, "PAGE_HTML has a <script> block"
    js = m.group(1)
    assert "KB/f\natlas" not in js
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, "served page JS is invalid:\n" + r.stderr
    finally:
        os.unlink(path)
