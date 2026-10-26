"""The keys reach the bar (owner call 2026-10-26, docs/shell_layout_2026-10.md
§8): up from the home's tab row or past the top of the surface under a bar
walks focus onto the bar's items, left and right walk them, confirm opens one
as a tap does, down and back come off it.
"""

import pytest

from runtime import bar_layer, host_app, ui_draw
from test_v04_userland import select_first_game


@pytest.fixture(autouse=True)
def _tdeck_keys():
    was = ui_draw.input_kind()
    ui_draw.input_kind("tdeck")
    yield
    ui_draw.input_kind(was)


def _home(tmp_path):
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    select_first_game(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def _press(drv, name, frames=2):
    drv.press(name)
    for _ in range(frames):
        drv.frame(1 / 30)


def _editor(ws, drv):
    ws._open_workspace(ws.launcher.selected())
    ws._open_menu()
    for _ in range(3):
        drv.frame(1 / 30)


def _cell_x(ws, where, name):
    bar, r = ws.bar_layer.cell(where, name)
    return r[0]


def test_up_from_the_homes_tabs_walks_onto_the_bar_and_opens_a_widget(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    assert ws.launcher_layer._tab_focus and bl.focus is None
    _press(drv, "up")
    assert bl.focus == 0 and not ws.launcher_layer._tab_focus
    battery = _cell_x(ws, "home", "battery")
    while bl.focus_item[1][0] != battery:
        k = bl.focus
        _press(drv, "right")
        assert bl.focus == k + 1, "right walks the bar's items"
    _press(drv, "a")
    assert ws.sysmenu.open and ws.sysmenu.widget == "battery", "confirm opens its popover"
    _press(drv, "b")
    assert not ws.sysmenu.open and bl.focus is not None, "the popover closes onto the bar"
    _press(drv, "down")
    assert bl.focus is None and ws.launcher_layer._tab_focus, "down comes back to the tabs"


def test_the_trackball_walks_the_bar(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "up")
    assert ws.ball_steps()
    k = bl.focus
    drv.pan(1, 0)
    for _ in range(2):
        drv.frame(1 / 30)
    drv.pan(0, 0)
    for _ in range(4):
        drv.frame(1 / 30)
    assert bl.focus == k + 1 and not ws.pointer.visible


def test_the_focused_item_is_marked_in_the_bars_strip(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    x, y, w, h = bl.bar_rects("home")[0]
    stride = ws.sys_canvas.w * 2

    def band():
        buf = bytes(ws.sys_canvas._buf)
        return b"".join(buf[stride * (y + r) + 2 * x:stride * (y + r) + 2 * (x + w)]
                        for r in range(h))
    rest = band()
    _press(drv, "up")
    _press(drv, "up")
    assert bl.focus is not None
    assert band() != rest, "the focus mark draws on the bar"
    _press(drv, "down")
    _press(drv, "down")
    assert band() == rest, "and goes with the focus"


def test_up_past_the_top_of_settings_walks_onto_its_bar(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    ws.open_settings()
    for _ in range(3):
        drv.frame(1 / 30)
    for _ in range(4):
        _press(drv, "up")
        if bl.focus is not None:
            break
    assert bl.focus is not None and bl.focus_where == "settings"
    _press(drv, "down")
    assert bl.focus is None and ws.screen == "settings"


def test_the_editors_bar_lands_on_its_open_tab_and_switches_tabs(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _editor(ws, drv)
    assert ws.menu_view == "cards"
    for _ in range(4):
        _press(drv, "up")
        if bl.focus is not None:
            break
    assert bl.focus_where == "menu"
    assert bl.focus_item[1][0] <= ws.bar_open_x("menu") < bl.focus_item[1][0] + \
        bl.focus_item[1][2], "the keys land on the open tab"
    _press(drv, "right")
    _press(drv, "a")
    assert ws.menu_view != "cards", "confirm on a tab opens it"
    assert bl.focus is not None, "the keys stay on the bar to walk on"
    _press(drv, "down")
    assert bl.focus is None


def test_a_menu_over_the_bar_takes_the_keys(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "up")
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    k, sel = bl.focus, ws.sysmenu.sel
    _press(drv, "down")
    assert bl.focus == k and ws.sysmenu.sel != sel


def test_the_bar_key_carries_the_focus():
    assert 12 in bar_layer._KEY_ALWAYS and bar_layer._KEY_N == 13


def test_the_homes_strip_names_the_bars_keys_while_on_it(tmp_path):
    from runtime import launcher_layer
    ws, drv = _home(tmp_path)
    assert launcher_layer.modal_keys(ws) is None
    _press(drv, "up")
    _press(drv, "up")
    assert launcher_layer.modal_keys(ws) == ui_draw.BAR_KEYS
    _press(drv, "down")
    assert launcher_layer.modal_keys(ws) is None


def test_a_pages_strip_names_the_bars_keys_while_on_it(tmp_path, monkeypatch):
    from runtime import ui
    seen = []
    real = ui._C.api_hints
    monkeypatch.setattr(ui._C, "api_hints",
                        lambda *a: (seen.append(a[3]), real(*a))[1])
    ws, drv = _home(tmp_path)
    ws.open_settings()
    for _ in range(3):
        drv.frame(1 / 30)
    page = seen[-1]
    for _ in range(4):
        _press(drv, "up")
        if ws.bar_layer.focus is not None:
            break
    drv.frame(1 / 30)
    assert seen[-1] == ui._opts(ui_draw.BAR_KEYS) != page
    _press(drv, "down")
    assert seen[-1] == page and not ui_draw.on_bar()


def test_the_bars_keys_are_keys_every_input_has(tmp_path):
    """change, confirm and back are named by every input's own keys -- the
    T-Deck's A D and the ball, Enter, K -- never a Tab or an Esc it lacks."""
    ws, _drv = _home(tmp_path)
    for inp in ("keys", "tdeck", "pad"):
        ui_draw.input_kind(inp)
        spans = ui_draw.hints(ws.sys_canvas, ws.theme_colors, (0, 0, 320, 20),
                              ui_draw.BAR_KEYS, draw=False)
        assert len(spans) == 3 and all(x1 > x0 for x0, x1 in spans), inp


def test_the_keys_come_off_the_bar_when_its_surface_goes(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "up")
    assert bl.focus_where == "home"
    ws.open_settings()
    drv.frame(1 / 30)
    assert bl.focus is None and not ui_draw.on_bar()


def test_a_popover_the_bar_opened_names_its_own_keys(tmp_path):
    from runtime import launcher_layer
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "up")
    battery = _cell_x(ws, "home", "battery")
    while bl.focus_item[1][0] != battery:
        _press(drv, "right")
    _press(drv, "a")
    assert ws.sysmenu.open and not ui_draw.on_bar()
    assert launcher_layer.modal_keys(ws) != ui_draw.BAR_KEYS
    _press(drv, "b")
    assert not ws.sysmenu.open and ui_draw.on_bar()


def test_a_page_under_the_bar_lets_its_focus_go_while_the_keys_are_on_it(tmp_path):
    """One focus mark a screen: the page's goes while the bar holds the
    keys, and comes back on its first row."""
    ws, drv = _home(tmp_path)
    ws.open_settings()
    for _ in range(3):
        drv.frame(1 / 30)
    first = ws.settings_layer._focused
    assert first
    _press(drv, "up")
    _press(drv, "up")
    assert ws.bar_layer.focus is not None
    assert not ws.settings_layer._focused
    _press(drv, "down")
    assert ws.settings_layer._focused == first
