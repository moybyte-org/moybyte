"""The handheld app bar's way back (docs/look_2026-10.md step 27a).

Back goes one level; a window's X closes it. On a console bar the way back
is the back chevron at the head of the zone, after the Moy button, wherever
the bar has somewhere to go and its layout places no `close`: the shipped
themes place none, a theme without a look (the `night` fixture) keeps its X.
The chevron, Esc and B do the same thing, and on an app's first page they
leave it.
"""

import pytest

from runtime.moy_input import InputTable
from ws_helpers import build_ws, make_drv, use_theme

DT = 1 / 30


def _frames(drv, n=3):
    for _ in range(n):
        drv.frame(DT)


def _press(ws, name):
    """One frame of a board's input with `name` pressed, through the
    console's router, as a BLE keyboard's Esc (`stop`) arrives."""
    was = ws.input
    i = InputTable()
    i.source("kbd").set_button(name, True)
    i.begin_frame()
    ws.input = i
    try:
        ws.handle_input()
    finally:
        ws.input = was


def _open(ws, app_id):
    ws.open_app(ws._apps_by_id[app_id])


def _tap_back(ws, drv, where):
    """Tap the chevron: the first square of `where`'s zone."""
    bl = ws.bar_layer
    bar, (x, y, w, h) = bl.cell(where, "zone")
    bx, by = bar[1][:2]
    drv.click(bx + x + 4, by + y + h // 2)


@pytest.mark.parametrize("app_id", ["calc", "appearance", "files", "getcarts",
                                    "storybook", "artwork"])
def test_every_app_bar_is_moy_back_and_title_with_no_x(tmp_path, app_id):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    _open(ws, app_id)
    _frames(drv)
    bl = ws.bar_layer
    assert bl.cell("app", "close") is None, "the console bar places no X"
    assert bl.cell("app", "menu") is not None, "the Moy button stays"
    assert bl.draws_back("app")
    assert not bl._zone_is_game("app"), "the look's bar, on the responsive layout"
    assert bl.content_rect("app")[1] == ws.layout.cy, "the app lays out under it"


def test_the_chevron_esc_and_b_leave_calc(tmp_path):
    for how in ("tap", "esc", "b", "stop"):
        ws = build_ws(tmp_path / how)
        drv = make_drv(ws)
        _open(ws, "calc")
        _frames(drv)
        if how == "tap":
            _tap_back(ws, drv, "app")
        elif how == "esc":
            drv.escape()
        else:
            _press(ws, how)
        _frames(drv)
        assert ws.screen == "launcher", how


def test_back_steps_an_apps_own_page_before_leaving_it(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    _open(ws, "appearance")
    _frames(drv)
    app = ws._apps_by_id["appearance"]
    app.view = "walls"
    _tap_back(ws, drv, "app")
    _frames(drv)
    assert ws.screen == "appearance" and app.view == "rows", "a page back first"
    _press(ws, "b")
    _frames(drv)
    assert ws.screen == "launcher", "then out of the app"


def test_settings_esc_is_its_b(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_settings()
    _frames(drv)
    assert ws.bar_layer.draws_back("settings")
    _press(ws, "stop")
    _frames(drv)
    assert ws.screen == "launcher"


def test_the_editor_bar_backs_out_with_no_x(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    cart = next(c for c in ws.carts.all if c["title"] == "Star Catcher")
    ws.open_in_editor(cart)
    _frames(drv)
    bl = ws.bar_layer
    assert bl.cell("menu", "close") is None and bl.draws_back("menu")
    _tap_back(ws, drv, "menu")
    _frames(drv)
    assert ws.screen == "launcher"


def test_the_home_at_its_floor_has_no_way_back(tmp_path):
    ws = build_ws(tmp_path)
    _frames(make_drv(ws))
    assert not ws.bar_layer.draws_back("home")


def test_a_theme_without_a_look_keeps_its_x(tmp_path):
    ws = build_ws(tmp_path)
    use_theme(ws, "night")
    drv = make_drv(ws)
    _open(ws, "calc")
    _frames(drv)
    bl = ws.bar_layer
    assert bl.cell("app", "close") is not None
    assert not bl.draws_back("app")
    bar, (x, y, w, h) = bl.cell("app", "close")
    drv.click(bar[1][0] + x + w // 2, bar[1][1] + y + h // 2)
    _frames(drv)
    assert ws.screen == "launcher", "the X still leaves"
