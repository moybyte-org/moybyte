"""Native 1x on the big boards, and the 2x option (docs/look_2026-10.md step 9).

A board whose declared glass is a desk's lays its chrome out on the desk's
metric class (`layout_base.CLASSES`): a 40-row bar, list rows 28, Places 34,
title strips 44, desk tiles 44 on a 74-row pitch, its handheld-sized parts at
the desk's unit of 2, at chrome size 1 and text scale 1. The handheld keeps
the numbers every formula was written at. A desk theme asking for
`render.scale: 2` draws the shell on a half-size surface the glass doubles,
with the desk's metrics halved onto it, and the pointer lands in its pixels.
The pixel nets are the goldens (`tests/test_shell_goldens.py`); this file
holds the numbers.
"""

import json
import os

import pytest

from runtime import host_app, layout_base, ui_draw
from runtime.app_shell import ListShellLayout
from runtime.chrome import text_scale_floor
from runtime.files_app import FilesLayout
from runtime.storybook_app import StorybookLayout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "native", "moy_ui", "fonts")

DESKS = (("p4", (1024, 600), 7.0), ("guition_p4", (1280, 800), 10.1))


def _ws(tmp_path, size=(1024, 600), diag=7.0, **kw):
    kw.setdefault("font_scale", 1)
    kw.setdefault("windowed", True)
    return host_app.build_workstation(str(tmp_path / "carts"), sys_size=size,
                                      panel_diagonal_in=diag, **kw)


def _frames(ws, n=4):
    drv = host_app.ConsoleDriver(ws)
    for _ in range(n):
        ws.mark_dirty()
        drv.frame(1 / 30)


@pytest.fixture(autouse=True)
def _handheld_after():
    yield
    layout_base.place_class(layout_base.HANDHELD)


# -- the classes -----------------------------------------------------------------

def test_a_declared_desk_sized_glass_is_a_desk_and_nothing_else_is():
    assert layout_base.class_of(None) == layout_base.HANDHELD
    assert layout_base.class_of(2.8) == layout_base.HANDHELD
    assert layout_base.class_of(3.5) == layout_base.HANDHELD
    assert layout_base.class_of(7.0) == layout_base.DESK
    assert layout_base.class_of(10.1) == layout_base.DESK


def test_the_handheld_metrics_are_the_formulas_it_always_ran():
    layout_base.place_class(layout_base.HANDHELD)
    for s in (1, 2, 3):
        assert layout_base.bar_rows(s) == layout_base.BAR_ROWS * s
        assert layout_base.metric("row", s) == 20 * s
        assert layout_base.metric("title", s) == 18 * s
        assert layout_base.unit() == 1


@pytest.mark.parametrize("render, bar, row, place, title, tile, unit", [
    (1, 40, 28, 34, 44, 44, 2),
    (2, 20, 14, 17, 22, 22, 1),
])
def test_the_desk_metrics_at_each_render_scale(render, bar, row, place, title,
                                               tile, unit):
    layout_base.place_class(layout_base.DESK, render)
    assert layout_base.unit() == unit
    cs = unit                                  # chrome size 1
    assert layout_base.bar_rows(cs) == bar
    assert layout_base.metric("row", 1) == row
    assert layout_base.metric("place", 1) == place
    assert layout_base.metric("title", 1) == title
    assert layout_base.metric("tile", 1) == tile
    assert layout_base.bar_rows(2 * cs) == 2 * bar     # chrome size 2


def test_a_handheld_takes_no_render_scale():
    layout_base.place_class(layout_base.HANDHELD, 2)
    assert layout_base.render_scale() == 1


@pytest.mark.parametrize("name, size, diag", DESKS)
def test_a_desk_boots_at_chrome_size_1_on_its_metrics(tmp_path, name, size, diag):
    ws = _ws(tmp_path, size, diag)
    assert ws.metric_class == layout_base.DESK, name
    assert ws.look.chrome_size() == 1 and ws.look.effective_font_scale() == 1
    assert ws.layout.cs == 2 and ws.layout.fs == 1
    assert ws.layout.status_h == 40 and ws.layout.bar_icon == 32
    assert ws.layout.cy == 40


def test_the_desk_windows_take_its_title_strip_and_content_sizes(tmp_path):
    ws = _ws(tmp_path)
    ws.open_desk()
    for app_id, size in (("files", (548, 534)), ("calc", (344, 488))):
        ws.open_app(ws._apps_by_id[app_id])
        _frames(ws, 2)
        win = ws.wm._wins[app_id]
        assert win.title_h == 44
        assert (win.w, win.h) == size, app_id


def test_the_desk_lists_take_its_rows_and_places(tmp_path):
    layout_base.place_class(layout_base.DESK)
    lay = FilesLayout(548, 490, 1, True, 2)
    assert lay.row_h == 28
    assert lay.two and lay.side[2] == FilesLayout.SIDE      # the places beside
    layout_base.place_class(layout_base.HANDHELD)
    lay = FilesLayout(320, 240, 1, False, 1)
    assert lay.row_h == 20 and not lay.two


def test_the_desk_icon_column_is_the_targets(tmp_path):
    ws = _ws(tmp_path)
    ws.open_desk()
    rects = ws.wm._backdrop_layer._icon_rects()
    boxes = [r[1] for r in rects]
    assert boxes[0][1] == 40 + 18
    assert all(b[2] == b[3] == 44 for b in boxes)
    assert boxes[1][1] - boxes[0][1] == 74


# -- the text floor ----------------------------------------------------------------

@pytest.mark.parametrize("name, size, diag", DESKS)
def test_the_text_floor_answers_1_on_the_desks_for_the_font_set(name, size, diag):
    """Every face of the font set (native/moy_ui/fonts) clears MIN_TEXT_MM at
    1x on both P4 boards: text is never lifted there."""
    faces = sorted(f for f in os.listdir(FONTS) if f.endswith(".fnt"))
    assert faces
    for face in faces:
        rc, fid = ui_draw._C.font_load(open(os.path.join(FONTS, face), "rb").read())
        assert rc == 0, face
        try:
            asc = ui_draw.font_info(fid)[3]
            assert text_scale_floor(size[0], size[1], diag, asc) == 1, (name, face)
        finally:
            ui_draw._C.font_free(fid)


# -- line metrics ------------------------------------------------------------------

def test_the_list_apps_read_the_print_fonts_line(tmp_path):
    """A taller print font grows the rows and bands that hold its text; under
    petme128 they are the 8px cell's numbers."""
    layout_base.place_class(layout_base.HANDHELD)
    lay = StorybookLayout(320, 240, 1)
    assert (lay.band_h, lay.row_h) == (24, 20)
    sample = os.path.join(ROOT, "tests", "fixtures", "fonts", "sample6x9.fnt")
    rc, fid = ui_draw._C.font_load(open(sample, "rb").read())
    assert rc == 0
    try:
        ui_draw.use_font("ui", fid)
        line = ui_draw.font("ui")[2]
        big = StorybookLayout(320, 240, 2)
        assert big.row_h >= line * 2 + 4 * 2
        assert big.band_h >= line * 2 + 8 * 2
        assert isinstance(big, ListShellLayout)
        assert big.cut("A LABEL FAR TOO LONG FOR ITS BOX", 60) \
            == ui_draw.print_cut("A LABEL FAR TOO LONG FOR ITS BOX", 60, 2)
    finally:
        ui_draw.use_font("ui", 0)
        ui_draw._C.font_free(fid)


# -- the 2x option -----------------------------------------------------------------

def _double(name="double", base="moy"):
    rc, _notes = ui_draw._C.theme_install(json.dumps(
        {"format": 2, "name": name, "inherits": base,
         "look": {"render": {"scale": 2}}}).encode())
    assert rc == 0


def test_a_render_scale_2_theme_draws_the_desk_at_half_and_doubles_it(tmp_path):
    ws = _ws(tmp_path)
    _double()
    try:
        ws.look.set_theme("double", persist=False, variant="light")
        assert ws.render_scale == 2
        assert (ws.sys_canvas.w, ws.sys_canvas.h) == (512, 300)
        assert (ws.glass_canvas.w, ws.glass_canvas.h) == (1024, 600)
        assert ws.wm._root_canvas is ws.sys_canvas
        assert ws.layout.status_h == 20 and ws.layout.cs == 1
        ws.open_desk()
        ws.open_app(ws._apps_by_id["files"])
        _frames(ws)
        assert ws.wm._wins["files"].title_h == 22
        half, glass = bytes(ws.sys_canvas._buf), bytes(ws.glass_canvas._buf)
        for y in range(0, 300, 13):
            for x in range(0, 512, 17):
                px = half[2 * (y * 512 + x):2 * (y * 512 + x) + 2]
                for dy in (0, 1):
                    for dx in (0, 1):
                        o = 2 * ((2 * y + dy) * 1024 + 2 * x + dx)
                        assert glass[o:o + 2] == px, (x, y)
        ws.pointer.place(401, 299)
        assert (ws.pointer.x, ws.pointer.y) == (200, 149)
    finally:
        ws.look.set_theme("moy", persist=False, variant="light")
        ui_draw._C.theme_uninstall()
    assert ws.render_scale == 1 and ws.sys_canvas is ws.glass_canvas
    assert (ws.sys_canvas.w, ws.layout.status_h) == (1024, 40)
    assert ws.pointer.div == 1


def test_render_scale_2_is_a_desks_alone(tmp_path):
    hh = _ws(tmp_path / "hh", (480, 320), 3.5, windowed=False)
    _double()
    try:
        hh.look.set_theme("double", persist=False)
        assert hh.render_scale == 1 and hh.sys_canvas.w == 480
    finally:
        ui_draw._C.theme_uninstall()
