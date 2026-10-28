"""The trackball where a surface takes the keys (owner call 2026-10-26): a
roll is the W A S D press it matches and the click is Enter, and no cursor
shows -- the home and every fullscreen surface; a running cart keeps the
pointer, and a caret takes it as the arrows
(`Workstation.ball`). The host's arrows-as-trackball (`ConsoleDriver.pan`,
`ball_click`) spend it the way the T-Deck's handle_input does.
"""

from runtime import host_app, ui_draw
from ws_helpers import build_ws

import pytest


@pytest.fixture(autouse=True)
def _tdeck_keys():
    was = ui_draw.input_kind()
    ui_draw.input_kind("tdeck")
    yield
    ui_draw.input_kind(was)


def _drv(tmp_path):
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def _roll(drv, dx, dy, pulses):
    """`pulses` frames of a one-pulse roll, then a few frames after it.
    Each is a roll of its own, after the last one stopped."""
    drv.ws._ball_axis_at -= drv.ws.BALL_ROLLING_MS + 1
    drv.pan(dx, dy)
    for _ in range(pulses):
        drv.frame(1 / 30)
    drv.pan(0, 0)
    for _ in range(3):
        drv.frame(1 / 30)


def test_a_roll_over_the_power_popover_steps_and_changes_the_volume(tmp_path):
    ws, drv = _drv(tmp_path)
    ws.toggle_popover("battery")
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.ball_steps()
    sl = ws.settings_layer
    before = sl.level("volume")[0]
    _roll(drv, -1, 0, 2)                      # one step left: quieter
    after = sl.level("volume")[0]
    assert after == before - 1, "a roll left is one step of the focused Volume"
    _roll(drv, 0, 1, 2)                       # one step down: Brightness
    bright = sl.level("brightness")[0]
    _roll(drv, -1, 0, 2)
    assert sl.level("brightness")[0] == bright - 1
    assert sl.level("volume")[0] == after
    assert not ws.pointer.visible, "no cursor over a surface that takes the keys"
    assert ws.sysmenu.open


def test_a_step_is_two_pulses_of_one_roll(tmp_path):
    ws, drv = _drv(tmp_path)
    ws.BALL_IDLE_MS = 10 ** 6                 # the test's frames are no pause
    seen = []
    real = ws.input.add_steps

    def spy(name, n):
        seen.extend([name] * n)
        real(name, n)
    ws.input.add_steps = spy
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    _roll(drv, 0, 1, 1)
    assert seen == [], "one pulse is no step"
    _roll(drv, 0, 1, 1)
    assert seen == ["down"], "the roll's second pulse is its step"
    _roll(drv, 0, -4, 1)
    assert seen == ["down", "up", "up"]


def test_a_roll_steps_the_system_menu_and_a_click_confirms_it(tmp_path):
    ws, drv = _drv(tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    m = ws.sysmenu
    sel = m.sel
    _roll(drv, 0, 1, 2)
    assert m.sel != sel, "a roll down moved the selection"
    assert not ws.pointer.visible
    picked = []
    m.activate = lambda: picked.append(m.sel)
    drv.ball_click()
    for _ in range(3):
        drv.frame(1 / 30)
    assert picked == [m.sel], "the click is Enter on the focused row"


def _home(tmp_path):
    from test_v04_userland import select_first_game
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    select_first_game(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def test_a_roll_on_the_home_moves_its_selection(tmp_path):
    ws, drv = _home(tmp_path)
    assert ws.screen == "launcher" and ws.ball_steps()
    first = ws.launcher.selected()
    x = ws.pointer.x
    _roll(drv, 1, 0, 2)
    for _ in range(20):                       # the carousel's glide
        drv.frame(1 / 30)
    assert ws.launcher.selected() is not first, "a roll right is the next card"
    assert ws.pointer.x == x and not ws.pointer.visible


def test_a_click_on_the_home_plays_the_selected_card(tmp_path):
    ws, drv = _home(tmp_path)
    title = ws.launcher.selected()["title"]
    drv.ball_click()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.screen == "desktop", "the click is Enter: the card plays"
    assert (ws.cart or {}).get("title") == title


def test_a_running_cart_keeps_the_pointer(tmp_path):
    ws, drv = _home(tmp_path)
    drv.ball_click()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.screen == "desktop" and not ws.ball_steps()
    x = ws.pointer.x
    drv.pan(1, 0)
    drv.frame(1 / 30)
    drv.pan(0, 0)
    assert ws.pointer.x > x and ws.pointer.visible
    assert not ws.ball_owed


def _code_tab(tmp_path):
    from test_v04_userland import select_first_game
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    select_first_game(ws)
    drv.press("run")
    drv.frame(1 / 30)
    ws._open_menu()
    if ws.menu_view == "cards":
        ws.set_menu_view("code")
    ws.editor.set_text("\n".join("r%02d" % i for i in range(20)))
    drv.frame(1 / 30)
    return ws, drv


def test_a_caret_takes_the_roll(tmp_path):
    ws, drv = _code_tab(tmp_path)
    assert not ws.ball_steps()
    x, row = ws.pointer.x, ws.editor.row
    _roll(drv, 0, 1, 1)
    assert ws.editor.row == row + 1, "a pulse is a caret step"
    assert ws.pointer.x == x and not ws.pointer.visible


def test_a_menu_over_the_caret_takes_the_roll_instead(tmp_path):
    ws, drv = _code_tab(tmp_path)
    ws.toggle_sysmenu()
    drv.frame(1 / 30)
    row, sel = ws.editor.row, ws.sysmenu.sel
    _roll(drv, 0, 1, 2)
    assert ws.editor.row == row and ws.sysmenu.sel != sel
