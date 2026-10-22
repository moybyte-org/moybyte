"""The handheld Editor's Map, Scene, Music and Blocks tabs on the public UI
API (docs/look_2026-10.md step 30; docs/media/look_2026-10/EDITOR_LAYOUT.md's
`*-editor` pictures): each tab's pages driven through the host's pointer
and keys, and a settled tab painting nothing."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from runtime import host_app                # noqa: E402

DT = 1 / 30


@pytest.fixture
def hh(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    cart = next(c for c in ws.carts.all if c.get("title") == "Star Catcher")
    ws.open_in_editor(cart)
    return ws, drv


def _open(hh, tab):
    ws, drv = hh
    ws.set_menu_view(tab)
    ws.pointer.visible = False
    ws._toast_until = 0
    for _ in range(3):
        drv.frame(DT)
    return ws, drv


def _tap(drv, rect, dx=None):
    x, y, w, h = rect
    drv.click(x + (w // 2 if dx is None else dx), y + h // 2)
    for _ in range(3):
        drv.frame(DT)


def _settles(ws, drv):
    for _ in range(4):
        drv.frame(DT)
    drawn = ws._frames_drawn
    for _ in range(8):
        drv.frame(DT)
    return ws._frames_drawn == drawn


# -- Music ------------------------------------------------------------------------------

def _music(hh):
    ws, drv = _open(hh, "music")
    mu = ws.music_ui
    if mu.musicedit is None:
        pytest.skip("no sound bank on this build")
    assert mu.layout.verbs
    return ws, drv, mu, mu.page()


def test_the_music_pad_edits_the_step_under_the_cursor(hh):
    ws, drv, mu, page = _music(hh)
    me = mu.musicedit
    before = me.cur_step()[0]
    _tap(drv, page.host.box("Pad pitch+"))
    assert me.cur_step()[0] == before + 1
    n = me.step_count()
    _tap(drv, page.host.box("Pad add"))
    assert me.step_count() == n + 1
    _tap(drv, page.host.box("Row 0"))
    assert me.step == 0


def test_the_music_tabs_chevrons_and_hear(hh):
    ws, drv, mu, page = _music(hh)
    me = mu.musicedit
    _tap(drv, page.host.box("Next"))
    assert me.sfx_idx == 1
    x, y, w, h = page.host.box("View")
    _tap(drv, (x, y, w, h), dx=w * 3 // 4)
    assert me.view == me.SONG_VIEW
    assert page.host.box("Pad slot+") is not None
    _tap(drv, page.host.box("Hear"))
    assert mu.music_preview is not None
    _tap(drv, page.host.box("Hear"))
    assert mu.music_preview is None


def test_a_sound_with_a_name_is_named_and_one_without_is_numbered(hh):
    from runtime.editor_music import sound_name
    ws, drv, mu, page = _music(hh)
    bank = mu.musicedit.bank
    bank.sfx[0].name = "tune"
    assert sound_name(bank, 0) == "0 tune"
    bank.sfx[0].name = None
    assert sound_name(bank, 0) == "Sound 0"


def test_the_speed_popover_sets_the_speed_and_b_closes_it(hh):
    ws, drv, mu, page = _music(hh)
    me = mu.musicedit
    _tap(drv, page.host.box("Speed"))
    assert page.pop == "speed"
    was = me.cur_sfx().speed
    drv.press("right")                 # the focused slider takes the arrows
    for _ in range(2):
        drv.frame(DT)
    assert me.cur_sfx().speed == was + 1
    drv.press("b")
    drv.frame(DT)
    assert page.pop is None
    assert ws.menu_view == "music"


def test_a_settled_music_tab_paints_nothing(hh):
    ws, drv, mu, page = _music(hh)
    assert _settles(ws, drv)
    _tap(drv, page.host.box("Pad pitch+"))
    assert _settles(ws, drv)


# -- Map --------------------------------------------------------------------------------

def _map(hh):
    ws, drv = _open(hh, "map")
    mu = ws.map_ui
    assert mu.layout.verbs and mu.mapedit is not None
    return ws, drv, mu, mu.page()


def _view_cell(mu, cx, cy):
    x0, y0, cell, _c, _r = mu._mv_metrics()
    me = mu.mapedit
    return (x0 + (cx - me.cam_x) * cell + cell // 2, y0 + (cy - me.cam_y) * cell + cell // 2)


def test_a_tap_in_the_view_paints_and_a_brush_tap_picks(hh):
    ws, drv, mu, page = _map(hh)
    lay = mu.layout
    tm = ws.project.tilemap
    drv.click(lay.tp_x0 + 22 + 5, lay.tp_y0 + 5)     # the second brush
    drv.frame(DT)
    assert mu.mapedit.n == mu._map_palette_ids()[1]
    x, y = _view_cell(mu, 2, 3)
    drv.click(x, y)
    for _ in range(2):
        drv.frame(DT)
    assert tm.mget(2, 3) == mu.mapedit.n
    assert mu._hover_cell == (2, 3)


def test_a_still_hold_in_the_view_picks_the_cells_tile(hh):
    ws, drv, mu, page = _map(hh)
    tm = ws.project.tilemap
    tm.mset(4, 4, 7)
    mu.mapedit.n = 2
    x, y = _view_cell(mu, 4, 4)
    drv.touch(x, y)
    for _ in range(mu.HOLD_FRAMES + 2):
        drv.frame(DT)
    drv.touch_up()
    drv.frame(DT)
    assert mu.mapedit.n == 7
    assert tm.mget(4, 4) == 7, "the hold took its stamp back"


def test_the_foot_picks_the_tools_the_size_and_the_zoom(hh):
    ws, drv, mu, page = _map(hh)
    _tap(drv, page.host.box("Tool Box"))
    assert mu.map_tool == "rect"
    _tap(drv, page.host.box("Tool Erase"))
    assert (mu.map_tool, mu.map_erase) == ("stamp", True)
    _tap(drv, page.host.box("Tool Pen"))
    assert (mu.map_tool, mu.map_erase) == ("stamp", False)
    size = mu.mapedit.size
    _tap(drv, page.host.box("Brush size"))
    assert mu.mapedit.size != size
    _tap(drv, page.host.box("Zoom"))
    assert mu.map_zoom == 1
    assert page.zoom_word().startswith("2")
    _tap(drv, page.host.box("Tool Select"))
    assert page.host.box("Copy") is None, "no selection: Copy is off"
    mu.mapedit.set_selection(0, 0, 1, 1)
    ws._dirty = True
    drv.frame(DT)
    _tap(drv, page.host.box("Copy"))
    assert mu.mapedit.has_clip


def test_resize_grows_the_map_from_its_popover(hh):
    ws, drv, mu, page = _map(hh)
    tm = ws.project.tilemap
    w = tm.w
    _tap(drv, page.host.box("Resize"))
    assert page.pop == "resize"
    _tap(drv, page.host.box("Wider"))
    assert tm.w == w + 1
    drv.press("b")
    drv.frame(DT)
    assert page.pop is None


def test_a_settled_map_tab_paints_nothing(hh):
    ws, drv, mu, page = _map(hh)
    assert _settles(ws, drv)
    _tap(drv, page.host.box("Tool Box"))
    assert _settles(ws, drv)

