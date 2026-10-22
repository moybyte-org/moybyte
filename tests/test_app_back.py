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
from runtime import ui_draw as _ui_draw
from runtime.settings_layer import TOGGLE_CONFIRMS
from ws_helpers import build_desktop_ws, build_ws, make_drv, use_theme

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


# -- the desk: a window's back chevron -----------------------------------------
#
# On the desk a window's title strip shows the back chevron only while its app
# has a page of it open (`has_page`); the chevron, and Esc and B while the
# window holds the focus, step out of that page and never close the window.


def _desk_app(tmp_path, app_id, theme=None):
    ws = build_desktop_ws(tmp_path)
    if theme is not None:
        use_theme(ws, *theme)
    drv = make_drv(ws)
    ws.open_desk()
    _open(ws, app_id)
    _frames(drv)
    return ws, drv, ws._apps_by_id[app_id], ws.wm._wins[app_id]


def _strip(ws, win):
    return dict(ws.wm._strip_buttons(win))


@pytest.mark.parametrize("theme", [None, ("night", "dark")], ids=["moy", "night"])
def test_a_desk_window_shows_its_back_only_on_a_page(tmp_path, theme):
    ws, drv, app, win = _desk_app(tmp_path, "appearance", theme)
    assert "back" not in _strip(ws, win), "the rows are the app's first page"
    icon = _ui_draw.frame_icon((win.x, win.y, win.w, win.h), win.title_h)
    app.view = "walls"
    ws._dirty = True
    _frames(drv)
    btns = ws.wm._frame_buttons(win)
    assert btns[0][0] == "back" and not btns[0][2], "the chevron heads the strip"
    if icon is not None:
        moved = _ui_draw.frame_icon((win.x, win.y, win.w, win.h), win.title_h,
                                    _ui_draw.FRAME_MIN | _ui_draw.FRAME_BACK)
        assert moved[0] >= btns[0][1][0] + btns[0][1][2], "the icon moves past it"
    bx, by, bw, bh = btns[0][1]
    drv.click(bx + bw // 2, by + bh // 2)
    _frames(drv)
    assert app.view == "rows", "the chevron steps out of the page"
    assert "appearance" in ws.wm._wins, "and never closes the window"
    assert "back" not in _strip(ws, win)


def test_the_window_chrome_repaints_when_its_back_comes_and_goes(tmp_path):
    """The chrome's freeze signature carries the chevron: a quiet window
    whose app opens a page paints its strip again."""
    ws, drv, app, win = _desk_app(tmp_path, "appearance")
    _frames(drv, 6)
    cv = ws.wm._root_canvas

    def strip():
        out = bytearray()
        for j in range(win.y, win.y + win.title_h):
            out += cv._buf[2 * (j * cv.w + win.x):2 * (j * cv.w + win.x + win.w)]
        return bytes(out)

    rest = strip()
    app.view = "walls"
    ws._dirty = True
    _frames(drv)
    assert strip() != rest, "the chevron drew"
    app.back()
    ws._dirty = True
    _frames(drv)
    assert strip() == rest, "and went with the page"


@pytest.mark.parametrize("key", ["b", "stop"])
def test_esc_and_b_in_a_focused_window_are_its_back(tmp_path, key):
    ws, drv, app, win = _desk_app(tmp_path, "files")
    app.layout.two = False
    app.level = "files"
    assert ws.app_has_page("files")
    _press(ws, key)
    _frames(drv)
    assert app.level == "places" and "files" in ws.wm._wins
    assert not ws.app_has_page("files")
    _press(ws, "stop")
    _frames(drv)
    assert "files" in ws.wm._wins, "with no page open Esc leaves the window be"


def test_the_settings_window_shows_its_back_on_a_page(tmp_path):
    """Settings answers `has_page` like the apps: its window's strip heads
    with the chevron while a page is open over its rows, and the chevron
    closes the page, never the window."""
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_desk()
    ws.open_settings()
    _frames(drv)
    sl, win = ws.settings_layer, ws.wm._wins["settings"]
    assert not ws.app_has_page("settings")
    assert "back" not in _strip(ws, win)
    sl.open_confirm(next(iter(TOGGLE_CONFIRMS)))
    _frames(drv)
    assert ws.app_has_page("settings")
    btns = ws.wm._frame_buttons(win)
    assert btns[0][0] == "back"
    bx, by, bw, bh = btns[0][1]
    drv.click(bx + bw // 2, by + bh // 2)
    _frames(drv)
    assert sl.confirm_key is None, "the chevron closes the page"
    assert "settings" in ws.wm._wins, "and never the window"
    assert "back" not in _strip(ws, win)


def test_the_hosts_esc_steps_a_desk_windows_page(tmp_path):
    ws, drv, app, win = _desk_app(tmp_path, "storybook")
    assert not drv.takes_escape()
    app.page = "deck"
    assert drv.takes_escape()
    drv.escape()
    assert app.page == "stories" and "storybook" in ws.wm._wins


@pytest.mark.parametrize("app_id,page", [
    ("appearance", lambda a: setattr(a, "view", "walls")),
    ("files", lambda a: setattr(a, "dialog", object())),
    ("getcarts", lambda a: setattr(a, "phase", "cart")),
    ("storybook", lambda a: setattr(a, "page", "view")),
    ("artwork", lambda a: setattr(a, "page", "show")),
    ("myart", lambda a: setattr(a, "menu", True)),
])
def test_every_app_with_pages_answers_has_page(tmp_path, app_id, page):
    ws = build_ws(tmp_path)
    _open(ws, app_id)
    _frames(make_drv(ws))
    app = ws._apps_by_id[app_id]
    assert not app.has_page() and not app.back(), "the first page is no page"
    page(app)
    assert app.has_page()
