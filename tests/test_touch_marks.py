"""Under touch the shell draws no focus mark (design review R3-15): a phone
shows none until a key or pad is used. The engine holds it for every page
(tests/test_ui_parts.py); here the shell's own menus over the home, whose
row selection is fed to the page's focus, are held to it on the shell the
goldens build: the card's menu a long press opens, and the system menu."""

import pytest

from runtime import ui_draw
from test_home_card_verbs import _card_centre, _frames, _select, _ws
from ws_helpers import make_drv


@pytest.fixture(autouse=True)
def _clean():
    yield
    import moy_ui
    moy_ui.theme_uninstall()
    ui_draw.input_kind("keys")


def _marks_drawn(ws, drv):
    ws._dirty = True
    ui_draw.marks(None, True)
    _frames(drv, 2)
    return ui_draw.marks()[1]


def test_the_card_menu_a_long_press_opens_draws_no_focus_mark(tmp_path):
    ws = _ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    _select(ws, "games", "cart")
    ui_draw.input_kind("touch")
    _frames(drv)
    x, y = _card_centre(ws)
    drv.touch(x, y)
    _frames(drv, 20)
    drv.touch_up()
    _frames(drv, 1)
    assert ws.sysmenu.open and ws.sysmenu.widget == "card"
    assert _marks_drawn(ws, drv) == 0, "a focus mark under touch"
    ui_draw.input_kind("pad")
    assert _marks_drawn(ws, drv) >= 1, "a pad's press brings the menu's mark back"


def test_the_system_menu_draws_no_focus_mark_under_touch(tmp_path):
    ws = _ws(tmp_path)
    drv = make_drv(ws)
    _frames(drv)
    ui_draw.input_kind("touch")
    ws.toggle_sysmenu() if hasattr(ws, "toggle_sysmenu") else ws.system_key()
    _frames(drv)
    assert ws.sysmenu.open
    assert _marks_drawn(ws, drv) == 0, "a focus mark under touch"
