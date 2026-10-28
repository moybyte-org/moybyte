"""The home's tab row (owner calls 2026-10-27, docs/shell_layout_2026-10.md
§8): where the bar holds the tabs, the tabs and the bar's items are one row
in screen order -- left and right walk all of them and stop at its ends, up
stays, down goes to the cards. The walk moves a focus; confirm opens the tab
the focus is on and takes the keys to its cards; a tap opens a tab at once.
The focused tab wears the focus pill and the open one its current mark, so
the two read apart. A trackball roll along the row that drifts across it
stays on it.
"""

import pytest

from runtime import host_app, launcher_layer, ui_draw
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


def _bar(ws):
    x, y, w, h = ws.bar_layer.bar_rects("home")[0]
    stride = ws.sys_canvas.w * 2
    buf = bytes(ws.sys_canvas._buf)
    return b"".join(buf[stride * (y + r) + 2 * x:stride * (y + r) + 2 * (x + w)]
                    for r in range(h))


def test_left_and_right_move_the_focus_and_confirm_opens_its_tab(tmp_path):
    ws, drv = _home(tmp_path)
    ll = ws.launcher_layer
    _press(drv, "up")
    assert ll.focused_tab() == "games"
    _press(drv, "right")
    assert ll.focused_tab() == "mine" and ws.launcher.tab == "games", \
        "the focus moves; the open tab stays"
    _press(drv, "right")
    _press(drv, "run")
    assert ws.launcher.tab == "apps", "confirm opens the focused tab"
    assert not ll._tab_focus, "and the keys go to its cards"


def _at(ws):
    """What the keys are on in the home's row: a tab id, or a bar item's
    widget name."""
    tab = ws.launcher_layer.focused_tab()
    if tab is not None:
        return tab
    it = ws.bar_layer.focus_item
    return None if it is None else it[2]


_ROW = ["menu", "games", "mine", "apps", "make", "wifi", "battery", "clock"]


def test_the_tabs_and_the_bars_items_walk_as_one_row(tmp_path):
    """On the T-Deck the tabs sit in the bar between the system button and
    the status widgets: left and right walk all of them in screen order and
    stop at the row's ends, and the walk opens no tab."""
    ws, drv = _home(tmp_path)
    xs = [r[0] for _t, r, _on in ws.launcher_layer.tabs_in_bar()]
    bl = ws.bar_layer
    bx = bl.bar_rects("home")[0][0]
    items = {it[2]: bx + it[1][0] for it in bl.focus_items("home")}
    on_screen = sorted([(items[n], n) for n in items]
                       + [(x, t) for x, (t, _l) in zip(xs, launcher_layer.HOME_TABS)])
    assert [n for _x, n in on_screen] == _ROW
    _press(drv, "up")
    for _ in range(2):
        _press(drv, "left")
    seen = [_at(ws)]
    for _ in range(len(_ROW)):
        _press(drv, "right")
        seen.append(_at(ws))
    assert seen == _ROW + ["clock"]
    back = []
    for _ in range(len(_ROW)):
        _press(drv, "left")
        back.append(_at(ws))
    assert back == _ROW[::-1][1:] + ["menu"]
    assert ws.launcher.tab == "games", "the walk opens no tab"


@pytest.mark.parametrize("on", ["games", "make", "wifi", "menu"])
def test_up_stays_on_the_row_and_down_goes_to_the_cards(tmp_path, on):
    ws, drv = _home(tmp_path)
    _press(drv, "up")
    _press(drv, "left")
    while _at(ws) != on:
        _press(drv, "right")
    _press(drv, "up")
    assert _at(ws) == on, "the row is the top line"
    _press(drv, "down")
    assert _at(ws) is None and not ws.launcher_layer._tab_focus
    assert ws.launcher.tab == "games"


def test_confirm_on_a_tab_reached_from_the_bar_opens_it(tmp_path):
    ws, drv = _home(tmp_path)
    _press(drv, "up")
    while _at(ws) != "wifi":
        _press(drv, "right")
    _press(drv, "left")
    assert _at(ws) == "make"
    _press(drv, "run")
    assert ws.launcher.tab == "make" and not ws.launcher_layer._tab_focus


def test_down_and_back_leave_the_row_without_switching(tmp_path):
    ws, drv = _home(tmp_path)
    ll = ws.launcher_layer
    for leave in ("down", "b"):
        _press(drv, "up")
        _press(drv, "right")
        _press(drv, leave)
        assert not ll._tab_focus and ws.launcher.tab == "games", leave
        assert ll.focused_tab() is None


def test_a_tap_opens_a_tab_at_once(tmp_path):
    ws, drv = _home(tmp_path)
    tid, r, _on = ws.launcher_layer.tab_rects()[2]
    drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
    for _ in range(2):
        drv.frame(1 / 30)
    assert ws.launcher.tab == tid


def test_the_focused_tab_and_the_open_one_read_apart(tmp_path):
    """The bar strip differs between the focus on the open tab, the focus
    on its neighbour, and no focus."""
    ws, drv = _home(tmp_path)
    rest = _bar(ws)
    _press(drv, "up")
    on_open = _bar(ws)
    _press(drv, "right")
    on_next = _bar(ws)
    assert len({rest, on_open, on_next}) == 3


def test_the_rows_strip_names_its_keys(tmp_path):
    ws, drv = _home(tmp_path)
    seen = []
    real = ui_draw.hints
    ui_draw.hints = lambda cv, th, rect, items, *a, **k: (
        seen.append(tuple(items)), real(cv, th, rect, items, *a, **k))[1]
    try:
        _press(drv, "up")
        ws.launcher_layer._full_streak = 0
        ws._dirty = True
        drv.frame(1 / 30)
    finally:
        ui_draw.hints = real
    assert launcher_layer._TAB_ROW_KEYS in seen


def _stepped(ws):
    """The steps `_ball_roll` delivers, as (name, n), instead of the table."""
    got = []
    ws._ball_steps = lambda name, k, coast=True: got.append((name, k))
    return got


def test_a_sideways_roll_that_drifts_down_stays_on_the_row(tmp_path):
    ws, _drv = _home(tmp_path)
    got = _stepped(ws)
    ws._ball_roll(2, 0)
    assert got == [("right", 1)]
    ws._ball_roll(0, 1)
    ws._ball_roll(0, 1)
    ws._ball_roll(0, 1)
    assert got == [("right", 1)], "a pulse or three across a roll is drift"
    ws._ball_roll(0, 1)
    assert got[1:] == [("down", 2)], "a turn of BALL_TURN pulses steps"


def test_a_turn_after_the_roll_stops_steps_at_once(tmp_path):
    ws, _drv = _home(tmp_path)
    got = _stepped(ws)
    ws._ball_roll(2, 0)
    ws._ball_axis_at -= ws.BALL_ROLLING_MS + 1
    ws._ball_roll(0, -2)
    assert got == [("right", 1), ("up", 1)]


def test_a_touch_takes_the_keys_off_the_row_and_the_bar(tmp_path):
    """Touch shows no focus, and the strips stay up: so a touch leaves the
    keys on no row the strip would name."""
    ws, drv = _home(tmp_path)
    ws.use_inputs((("local", "tdeck"), ("touch", "touch")))
    _press(drv, "up")
    ws.input_used("touch")
    assert not ws.launcher_layer._tab_focus
    ws.input_used("local")
    _press(drv, "up")
    _press(drv, "left")
    assert ws.bar_layer.focus is not None
    ws.input_used("touch")
    assert ws.bar_layer.focus is None and not ws.launcher_layer._tab_focus
    assert launcher_layer.modal_keys(ws) is None


def test_the_trackball_rolls_off_the_tabs_onto_the_bar_and_back(tmp_path):
    ws, drv = _home(tmp_path)
    _press(drv, "up")
    while _at(ws) != "make":
        _press(drv, "right")

    def roll(dx):
        drv.pan(dx, 0)
        for _ in range(2):
            drv.frame(1 / 30)
        drv.pan(0, 0)
        for _ in range(4):
            drv.frame(1 / 30)
    roll(1)
    assert _at(ws) == "wifi"
    roll(-1)
    assert _at(ws) == "make" and ws.launcher.tab == "games"


def test_the_cards_let_their_mark_go_while_the_bar_holds_the_keys(tmp_path):
    """One focus mark a screen, anywhere on the row."""
    ws, drv = _home(tmp_path)
    _press(drv, "up")
    while _at(ws) != "wifi":
        assert ws.launcher.tab_focus, _at(ws)
        _press(drv, "right")
    assert ws.launcher.tab_focus
    _press(drv, "down")
    assert not ws.launcher.tab_focus
