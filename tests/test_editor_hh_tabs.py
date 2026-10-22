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
