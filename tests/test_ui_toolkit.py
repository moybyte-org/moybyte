"""runtime/ui.py -- the shared immediate-mode widget toolkit (visual identity
v1 Phase 3): rect algebra, the draw==tap Hits registry, themed widgets, and the
one ScrollRegion model. Pixel checks run on a real SystemCanvas so the drawing
path is the one every tier shares."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import ui
from runtime.canvas import SystemCanvas
from runtime.chrome import theme_colors


TH = theme_colors("machine")


# -- rect algebra -------------------------------------------------------------

def test_inset_and_cuts():
    r = (10, 20, 100, 50)
    assert ui.inset(r, 5) == (15, 25, 90, 40)
    assert ui.inset(r, 60) == (70, 80, 0, 0)          # clamped, never negative
    band, rest = ui.cut_top(r, 12)
    assert band == (10, 20, 100, 12) and rest == (10, 32, 100, 38)
    band, rest = ui.cut_bottom(r, 12)
    assert band == (10, 58, 100, 12) and rest == (10, 20, 100, 38)
    band, rest = ui.cut_left(r, 30)
    assert band == (10, 20, 30, 50) and rest == (40, 20, 70, 50)
    band, rest = ui.cut_right(r, 30)
    assert band == (80, 20, 30, 50) and rest == (10, 20, 70, 50)
    band, rest = ui.cut_top(r, 999)                    # oversized cut clamps
    assert band == r and rest[3] == 0


def test_splits_cover_without_overlap():
    r = (0, 0, 103, 30)
    cols = ui.hsplit(r, 3, gap=2)
    assert len(cols) == 3
    assert cols[0][0] == 0
    for a, b in zip(cols, cols[1:]):
        assert a[0] + a[2] + 2 == b[0]                 # gap respected
    assert cols[-1][0] + cols[-1][2] == 103            # slack goes to the last
    rows = ui.vsplit((0, 0, 10, 91), 4)
    assert rows[-1][1] + rows[-1][3] == 91


# -- Hits ----------------------------------------------------------------------

def test_hits_topmost_wins_and_clear():
    h = ui.Hits()
    h.add((0, 0, 100, 100), "under", 1)
    h.add((10, 10, 20, 20), "over", 2)
    assert h.at(15, 15) == ("over", 2)                 # drawn later = on top
    assert h.at(50, 50) == ("under", 1)
    assert h.at(200, 200) is None
    h.clear()
    assert h.at(15, 15) is None


# -- widgets --------------------------------------------------------------------

def _cv(w=320, h=120, fs=1):
    return SystemCanvas(w, h, font_scale=fs)


def test_button_kinds_paint_their_tokens():
    cv = _cv()
    ui.button(cv, TH, (10, 10, 80, 20), "PLAY", kind="play")
    assert cv.pix(12, 12) == TH["play"]
    assert cv.pix(10, 10) == 0                         # dark edge
    ui.button(cv, TH, (10, 50, 80, 20), "CHANGE", kind="normal")
    assert cv.pix(12, 52) == TH["surface"]
    ui.button(cv, TH, (10, 80, 80, 20), "DEL", kind="danger")
    assert cv.pix(12, 82) == TH["danger"]
    ui.button(cv, TH, (110, 10, 80, 20), "ON", on=True)
    assert cv.pix(112, 12) == TH["accent"]


def test_tab_row_geometry_and_overflow():
    tabs = [("cards", "CONFIG"), ("code", "CODE"), ("map", "MAP")]
    wide = ui.tab_row_rects((0, 0, 400, 18), tabs, 1)
    assert [t[0] for t in wide] == ["cards", "code", "map"]
    assert all(labels_on for _t, _r, labels_on in wide)
    # Too narrow for labels -> icon-only chips for EVERY tab (uniform collapse).
    slim = ui.tab_row_rects((0, 0, 90, 18), tabs, 1)
    assert slim and not any(labels_on for _t, _r, labels_on in slim)
    # Rects never leave the row.
    for _t, (x, y, w, h), _l in slim:
        assert x + w <= 90
    # Narrower still -> trailing chips drop (draw-what-fits), never overlap.
    tiny = ui.tab_row_rects((0, 0, 40, 18), tabs, 1)
    assert len(tiny) < len(tabs)


def test_tab_row_draws_active_selection_and_hits():
    cv = _cv(w=420)
    hits = ui.Hits()
    tabs = [("cards", "CONFIG", "edit"), ("code", "CODE", "code")]
    rects = ui.tab_row(cv, TH, (0, 0, 420, 18), tabs, "code", hits=hits)
    assert [r[0] for r in rects] == ["cards", "code"]
    _tid, (x, y, w, h), _l = rects[1]
    assert cv.pix(x + 1, y + 1) == TH["selection"]     # active tab field
    assert hits.at(x + 2, y + 2) == ("tab", "code")
    assert hits.at(rects[0][1][0] + 2, 2) == ("tab", "cards")


def test_panel_and_status_row_paint():
    cv = _cv()
    content = ui.panel(cv, TH, (20, 20, 200, 80), title="INSPECTOR")
    assert cv.pix(22, 40) in (TH["surface"], TH["title"])
    assert content[1] > 21 and content[3] < 80         # title strip consumed
    ui.status_row(cv, TH, (0, 100, 320, 14), ("Ln 3, Col 7", "SAVED"))
    assert cv.pix(2, 108) == TH["surface_alt"]


def test_focus_ring_paints_focus_token():
    cv = _cv()
    ui.focus_ring(cv, TH, (50, 50, 40, 30))
    assert cv.pix(50 - 2, 50 - 2) == TH["focus"]


# -- ScrollRegion ------------------------------------------------------------------

def test_scroll_region_clamps_and_shows():
    sr = ui.ScrollRegion()
    sr.set((0, 0, 100, 50), content_h=200)
    sr.scroll_by(-10)
    assert sr.offset == 0                              # clamped at the top
    sr.scroll_by(500)
    assert sr.offset == 150                            # content_h - view_h
    sr.scroll_to_show(0, 10)
    assert sr.offset == 0
    sr.scroll_to_show(120, 10)
    assert sr.offset == 80                             # bottom edge visible
    sr.set((0, 0, 100, 50), content_h=30)              # fits -> no bar, offset 0
    assert sr.offset == 0
    assert sr.bar_rect() is None


def test_scroll_region_drag_and_bar():
    sr = ui.ScrollRegion()
    sr.set((0, 0, 100, 50), content_h=200)
    sr.drag_start(40)
    assert sr.drag_move(30)                            # finger up -> content down
    assert sr.offset == 10
    sr.drag_end()
    assert not sr.drag_move(10)                        # ended drag is inert
    bar = sr.bar_rect()
    assert bar is not None
    x, y, w, h = bar
    assert x + w <= 100 and h >= 8
    cv = _cv()
    sr.draw_bar(cv, TH)                                # draws without error
