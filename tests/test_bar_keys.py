"""The keys reach the bar (owner call 2026-10-26, docs/shell_layout_2026-10.md
§8): up past the top of the surface under a bar walks focus onto the bar's
items, left and right walk them, confirm opens one as a tap does, down and
back come off it. On the home the bar's items and its tabs are one row
(tests/test_home_tab_row.py).

The system menu carries Home off the home: the way out of a surface with no
back of its own, as the home key goes.

The theme mode's Config names no trackball in its hints: its caret takes the
ball, and Mic's menu, on its Home row, is the way out.
"""

import pytest

from runtime import bar_layer, host_app, theme_editor, ui_draw
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


def _walk_to(ws, drv, name):
    """Right along the home's row (the bar's items and the tabs) onto the
    bar's `name`."""
    x = _cell_x(ws, "home", name)
    for _ in range(12):
        it = ws.bar_layer.focus_item
        if it is not None and it[1][0] == x:
            return
        _press(drv, "right")
    raise AssertionError(name)


def test_the_homes_row_walks_onto_the_bar_and_opens_a_widget(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    assert ws.launcher_layer._tab_focus and bl.focus is None
    _press(drv, "left")
    assert bl.focus == 0 and not ws.launcher_layer._tab_focus, \
        "left off the first tab is the item left of the tabs"
    _walk_to(ws, drv, "wifi")
    k = bl.focus
    _press(drv, "right")
    assert bl.focus == k + 1, "right walks the bar's items"
    _press(drv, "a")
    assert ws.sysmenu.open and ws.sysmenu.widget == "battery", "confirm opens its popover"
    _press(drv, "b")
    assert not ws.sysmenu.open and bl.focus is not None, "the popover closes onto the bar"
    _press(drv, "down")
    assert bl.focus is None and not ws.launcher_layer._tab_focus, \
        "down comes off the row to the cards"
    assert ws.launcher.tab == "games"


def test_the_trackball_walks_the_bar(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _walk_to(ws, drv, "wifi")
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
    _press(drv, "left")
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
    assert ws.menu_view == "cards", "the walk moves the focus and opens nothing"
    _press(drv, "a")
    assert ws.menu_view != "cards", "confirm on a tab opens it"
    assert bl.focus is None and not ui_draw.on_bar(), \
        "and takes the keys down into it, as the home's row does"


def _editor_row(ws):
    """The Editor's bar on the T-Deck, left to right: (x, what), what a
    tab id, "undo", "redo", "play" or a bar widget's name."""
    from runtime import editor_app
    bl = ws.bar_layer
    bar = bl.cell("menu", "zone")[0]
    zone = bl.lent_zone("menu", bar)
    named = {}
    for tab, _glyph, r in ws.editor_app._ladder_layout(zone)[0]:
        named[r[0]] = {editor_app._ZONE_UNDO: "undo", editor_app._ZONE_REDO: "redo",
                       None: "play"}.get(tab, tab)
    return [(r[0], name or named[r[0]]) for _bi, r, name in bl.focus_items("menu")]


def test_the_editors_tabs_and_the_bars_items_walk_as_one_row(tmp_path):
    """On the T-Deck the Editor's tab ladder sits in the bar between the
    system button and the status widgets: left and right walk all of them in
    screen order and stop at the row's ends, and the walk opens no tab."""
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _editor(ws, drv)
    row = _editor_row(ws)
    assert [w for _x, w in row] == ["menu", "cards", "blocks", "code", "paint", "map",
                                    "scene", "music", "undo", "redo", "play",
                                    "battery", "clock"]
    _press(drv, "up")
    assert row[bl.focus][1] == "cards", "the keys land on the open tab"
    seen = []
    for _ in range(len(row) + 2):
        _press(drv, "right")
        seen.append(row[bl.focus][1])
        assert ws.menu_view == "cards", "the walk opens nothing"
    assert seen[-3:] == ["clock"] * 3, "the row stops at its right end"
    for _ in range(len(row) + 2):
        _press(drv, "left")
    assert row[bl.focus][1] == "menu", "and at its left end"
    assert not ws.sysmenu.open and ws.menu_view == "cards"
    _press(drv, "up")
    assert bl.focus_where == "menu", "up stays on the row"


def test_confirm_on_undo_keeps_the_keys_on_the_editors_bar(tmp_path):
    """Undo and redo are verbs, not tabs: confirm runs one and the keys
    stay on the row to run it again."""
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _editor(ws, drv)
    row = [w for _x, w in _editor_row(ws)]
    _press(drv, "up")
    while row[bl.focus] != "undo":
        _press(drv, "right")
    _press(drv, "a")
    assert bl.focus is not None and row[bl.focus] == "undo"
    assert ws.menu_view == "cards"
    _press(drv, "down")
    assert bl.focus is None


def test_a_menu_over_the_bar_takes_the_keys(tmp_path):
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "left")
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
    _press(drv, "left")
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
    _press(drv, "left")
    assert bl.focus_where == "home"
    ws.open_settings()
    drv.frame(1 / 30)
    assert bl.focus is None and not ui_draw.on_bar()


def test_a_popover_the_bar_opened_names_its_own_keys(tmp_path):
    from runtime import launcher_layer
    ws, drv = _home(tmp_path)
    bl = ws.bar_layer
    _press(drv, "up")
    _press(drv, "left")
    _walk_to(ws, drv, "battery")
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


def _rows(ws):
    return [r[4] for r in ws.menu_ui._sysmenu_items() if r[0] == "item"]


def test_the_system_menu_has_home_off_the_home(tmp_path):
    ws, drv = _home(tmp_path)
    assert "Home" not in _rows(ws)
    _editor(ws, drv)
    assert ws.screen == "menu" and "Home" in _rows(ws)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    m = ws.sysmenu
    m.sel = next(i for i, r in enumerate(m.items) if r[0] == "item" and r[4] == "Home")
    _press(drv, "run")
    assert ws.screen == "launcher" and not m.open, "Home leaves the Editor for the home"


def test_settings_has_home_too(tmp_path):
    ws, drv = _home(tmp_path)
    ws.open_settings()
    drv.frame(1 / 30)
    assert "Home" in _rows(ws)


def _home_row(ws, drv):
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    m = ws.sysmenu
    m.sel = next(i for i, r in enumerate(m.items) if r[0] == "item" and r[4] == "Home")
    _press(drv, "run")
    assert not m.open


def test_home_leaves_settings_an_app_and_a_running_cart(tmp_path):
    from ws_helpers import select_on_home
    ws, drv = _home(tmp_path)
    ws.open_settings()
    drv.frame(1 / 30)
    _home_row(ws, drv)
    assert ws.screen == "launcher"

    select_on_home(ws, "Files")
    ws.open()
    drv.frame(1 / 30)
    assert ws.screen != "launcher"
    _home_row(ws, drv)
    assert ws.screen == "launcher"

    select_on_home(ws, "Notes")
    ws.open()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.wm.top_is_player() and ws.cart is not None
    _home_row(ws, drv)
    assert ws.screen == "launcher" and ws.cart is None and not ws.wm.top_is_player()


def test_home_from_the_editor_keeps_an_edit(tmp_path):
    """Home leaves the Editor by the home key's path, which commits what
    is being typed: no prompt, nothing lost."""
    ws, drv = _home(tmp_path)
    _editor(ws, drv)
    ed = ws.editor_app
    ed.save_current()
    calls = []
    real = ed.save_current
    ed.save_current = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    _home_row(ws, drv)
    assert ws.screen == "launcher" and calls, "the leave path saves the open tab"


def test_the_theme_modes_config_names_no_trackball_on_the_tdeck():
    keys = theme_editor._hints_for(theme_editor.TAB_CONFIG)
    assert "ball" not in keys and "ball_lr" not in keys
    assert keys == ("menu", "Home")


@pytest.mark.parametrize("theme,variant", [("moy", "dark"), ("moy", "light"),
                                           ("contrast", "dark"), ("contrast", "light"),
                                           ("blocks", "light"), ("blocks", "dark")])
def test_the_bars_focus_pill_keeps_the_items_ink(tmp_path, theme, variant):
    """The bar's mark is the pill whatever the theme's focus style: a plate
    under the item and a ring round it, so an invert theme's mark never
    blots the icon out. Each status widget's icon box keeps more than one
    colour under it, and the ring draws where the bar was plain."""
    ws, drv = _home(tmp_path)
    ws.look.set_theme(theme, persist=False, variant=variant)
    for _ in range(2):
        drv.frame(1 / 30)
    bl = ws.bar_layer
    bx, by = bl.bar_rects("home")[0][:2]
    cv = ws.sys_canvas

    def px(x, y):
        k = 2 * ((by + y) * cv.w + bx + x)
        return bytes(cv._buf[k:k + 2])
    _press(drv, "up")
    _press(drv, "left")
    for name in ("wifi", "battery"):
        _walk_to(ws, drv, name)
        box, _r, _u = bl._pill("home", 0, bl.focus_item)
        r = bl.focus_item[1]
        icon = {px(r[0] + i, r[1] + j) for i in range(r[2]) for j in range(r[3])}
        assert len(icon) > 2, (name, "the icon shows through the mark")
        ring = {px(box[0] + box[2] // 2, box[1] + k) for k in range(2)}
        _press(drv, "down")
        drv.frame(1 / 30)
        plain = {px(box[0] + box[2] // 2, box[1] + k) for k in range(2)}
        assert ring != plain, (name, "the ring draws on the bar")
        _press(drv, "up")
        _press(drv, "left")
