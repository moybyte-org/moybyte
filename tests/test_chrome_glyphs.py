"""The glyph vocabulary is data and its raster is one C body
(docs/kernel_toolkit_2026-10.md §5).

native/moy_ui/style.json's "glyphs" are the bitmaps runtime/chrome.py held,
and native/moy_ui's raster derives each glyph's spans from its rows at draw
time, at any scale. These pin that the C draws EXACTLY what the Python span
walk drew (`tests/ui_twin.py`'s `blit_glyph`, the old `_blit_glyph`): every
glyph, every scale, rects smaller than the glyph, a camera and a clip -- the
baselines are byte-identical contracts (#39). moy_chrome's GLYPH op draws
through the same raster; tests/test_moy_chrome.py holds its raster to the
canvas replay, which draws with `ui.glyph`.
"""

import json
import os

import ui_twin as twin
from runtime import ui
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECTS = ((4, 4, 40, 30), (10, 3, 12, 12), (20, 20, 5, 7), (-6, -3, 30, 9),
         (50, 40, 17, 23))


def test_the_style_file_holds_the_vocabulary():
    with open(os.path.join(ROOT, "native", "moy_ui", "style.json")) as f:
        glyphs = json.load(f)["glyphs"]
    assert list(glyphs) == list(twin._GLYPHS)
    for kind, rows in glyphs.items():
        bits = tuple(int(r.replace("#", "1").replace(".", "0"), 2) for r in rows)
        assert bits == twin._GLYPHS[kind], kind


def _both(kind, rect, fs, scale=None, camera=(0, 0), clip=None):
    out = []
    for draw in (lambda cv: twin.blit_glyph(cv, kind, rect, 9, scale),
                 lambda cv: ui.glyph(cv, kind, rect, 9, scale)):
        cv = SystemCanvas(96, 72, font_scale=fs)
        cv.camera(*camera)
        if clip:
            cv.clip(*clip)
        draw(cv)
        out.append(indices_of(cv))
    return out


def test_every_glyph_at_every_scale_matches_the_span_walk():
    for kind in twin._GLYPHS:
        for fs in (1, 2, 3):
            for rect in RECTS:
                py, c = _both(kind, rect, fs)
                assert py == c, (kind, fs, rect)


def test_the_camera_and_the_clip_apply():
    for kind in ("run", "gear", "move", "circle"):
        py, c = _both(kind, (10, 10, 30, 30), 2, camera=(3, -4),
                      clip=(12, 8, 20, 17))
        assert py == c, kind
        assert any(c), kind


def test_explicit_scale_overrides_the_canvas_font_scale():
    py, c = _both("star", (0, 0, 60, 60), 1, scale=4)
    assert py == c and any(c)
    big, _ = _both("star", (0, 0, 60, 60), 4)
    assert c == big


def test_unknown_kind_draws_nothing():
    for kind in ("no-such-glyph", None, ""):
        cv = SystemCanvas(32, 32)
        before = indices_of(cv)
        ui.glyph(cv, kind, (0, 0, 32, 32), 9)
        assert indices_of(cv) == before
        assert ui.glyph_id(kind) == -1


def test_a_glyph_is_one_fill_per_span():
    """The row's counter: a span is a fill, as the gate's fill_rects counts."""
    for kind in ("run", "map", "blocks"):
        cv = SystemCanvas(64, 64, font_scale=2)
        cv.gate_counts_reset()
        ui.glyph(cv, kind, (0, 0, 40, 40), 7)
        assert cv.gate_counts()[:2] == (len(twin.glyph_runs(kind, 2)), 0), kind


def test_one_raster_and_no_pushed_copy():
    """The glyphs live in style.json alone: no Python table in runtime/, no
    push into moy_chrome, no surface role row."""
    for d in ("runtime", "native", "device"):
        for base, _sub, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith((".py", ".c", ".h")):
                    continue
                with open(os.path.join(base, f), encoding="utf-8",
                          errors="replace") as fh:
                    src = fh.read()
                rel = os.path.relpath(os.path.join(base, f), ROOT)
                assert "_GLYPHS" not in src.replace("ICON_GLYPHS", "").replace(
                    "P8_GLYPHS", "").replace("MOY_UI_GLYPHS", "").replace(
                    "FONT_GLYPHS", ""), rel
                assert "chrome_glyph" not in src and "glyph_put" not in src, rel
    with open(os.path.join(ROOT, "native", "moy_app", "roles.json")) as f:
        rows = json.load(f)["rows"]
    assert not [r for r in rows if r["verb"] == "glyph"]
