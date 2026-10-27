"""The home's tab row (owner call 2026-10-27, docs/shell_layout_2026-10.md
§8): left and right move a focus along the tabs and stop at the row's ends,
never onto the bar beside them; confirm opens the tab the focus is on and
takes the keys to its cards; a tap opens a tab at once. The focused tab
wears the focus pill and the open one its current mark, so the two read
apart. A trackball roll along the row that drifts across it stays on it.
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


def test_the_row_stops_at_its_ends_and_never_walks_onto_the_bar(tmp_path):
    ws, drv = _home(tmp_path)
    ll = ws.launcher_layer
    _press(drv, "up")
    for _ in range(3):
        _press(drv, "left")
    assert ll.focused_tab() == "games" and ws.bar_layer.focus is None
    for _ in range(8):
        _press(drv, "right")
    assert ll.focused_tab() == "make" and ws.bar_layer.focus is None
    assert ws.launcher.tab == "games"
    _press(drv, "up")
    assert ws.bar_layer.focus is not None, "only up goes onto the bar"


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


def test_a_sideways_roll_that_drifts_up_stays_on_the_row(tmp_path):
    ws, _drv = _home(tmp_path)
    ws._ball_roll(2, 0)
    assert ws._ball_qx == 1
    ws._ball_qx = 0
    ws._ball_roll(0, -1)
    ws._ball_roll(0, -1)
    ws._ball_roll(0, -1)
    assert ws._ball_qy == 0, "a pulse or three across a roll is drift"
    ws._ball_roll(0, -1)
    assert ws._ball_qy == -2, "a turn of BALL_TURN pulses steps"


def test_a_turn_after_the_roll_stops_steps_at_once(tmp_path):
    ws, _drv = _home(tmp_path)
    ws._ball_roll(2, 0)
    ws._ball_qx = 0
    ws._ball_axis_at -= ws.BALL_ROLLING_MS + 1
    ws._ball_roll(0, -2)
    assert ws._ball_qy == -1

