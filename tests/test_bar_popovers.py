"""The bar widgets' popovers (runtime/system/popover.py,
docs/theme_and_shell_2026-11.md §8.6) as a user drives them: the clock's
month, paged by the arrows and, on the desk and under touch, by its
chevrons, back on this month at each open; the desk's power rows and a
handheld's control center, whose sliders move Settings' levels by keys and
taps and whose Wi-Fi row opens the Wi-Fi popover; every one closed by B, a
tap outside it and a tap on its anchor. An app's status section in them is
test_status_items.py's."""

import pytest

from runtime.popover import shift_month
from ws_helpers import build_desktop_ws, build_ws, make_drv


def _desk(tmp_path):
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0)
    ws.home.open_desk()
    drv = make_drv(ws)
    drv.frame(1 / 30)
    return ws, drv


def _handheld(tmp_path):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    return ws, drv


def _press(drv, name, frames=2):
    drv.press(name)
    for _ in range(frames):
        drv.frame(1 / 30)


def _tap(drv, x, y, frames=2):
    drv.click(x, y)
    for _ in range(frames):
        drv.frame(1 / 30)


def _cell(ws, where, name):
    _bar, (x, y, w, h) = ws.bar_layer.cell(where, name)
    return x + w // 2, y + h // 2


def _open_by_tap(ws, drv, where, name):
    ws.bar_layer.handle_bar_tap(where, *_cell(ws, where, name))
    drv.frame(1 / 30)
    assert ws.sysmenu.open and ws.sysmenu.widget == name


def _shown(ws):
    """The month the open clock popover shows: (year, month)."""
    y, m, _d = ws.sysmenu.comp.month()
    return y, m


def _this_month(ws):
    y, m, _d = ws.bar_layer.today()
    return y, m


def _box(ws, key):
    """The rect of the open popover's row `key` ("volume", "brightness")."""
    return ws.sysmenu.comp.host.box({"volume": "Volume", "brightness": "Brightness"}[key])


def _chevrons(ws, drv):
    """Tap along the head of the open month, from its right, until a tap
    pages it: the steps the taps made, in order (the next month's chevron
    stands right of the previous one's)."""
    px, py, pw, ph = ws.sysmenu.panel_rect()
    steps = []
    for dx in range(4, pw // 2, 4):
        for dy in range(4, min(ph, 72), 4):
            before = _shown(ws)
            _tap(drv, px + pw - dx, py + dy)
            assert ws.sysmenu.open, "a tap inside the card keeps it open"
            now = _shown(ws)
            if now != before:
                k = (now[0] * 12 + now[1]) - (before[0] * 12 + before[1])
                if not steps or steps[-1] != k:
                    steps.append(k)
                break
        if len(steps) == 2:
            break
    return steps


# -- the clock's month --------------------------------------------------------

def test_the_desk_clock_opens_on_this_month_and_its_arrows_page_it(tmp_path):
    ws, drv = _desk(tmp_path)
    _open_by_tap(ws, drv, "desk", "clock")
    y, m = _this_month(ws)
    assert _shown(ws) == (y, m)
    assert ws.sysmenu.comp.month()[2] == ws.bar_layer.today()[2], "today is marked"
    _press(drv, "right")
    _press(drv, "right")
    assert _shown(ws) == shift_month(y, m, 2)
    assert ws.sysmenu.comp.month()[2] == 0, "another month marks no day"
    _press(drv, "left")
    _press(drv, "left")
    _press(drv, "left")
    assert _shown(ws) == shift_month(y, m, -1)
    _press(drv, "b")
    assert not ws.sysmenu.open
    _open_by_tap(ws, drv, "desk", "clock")
    assert _shown(ws) == (y, m), "each open starts on this month"


def test_the_desk_month_pages_by_its_chevrons(tmp_path):
    ws, drv = _desk(tmp_path)
    _open_by_tap(ws, drv, "desk", "clock")
    assert _chevrons(ws, drv) == [1, -1]


def test_the_clock_popover_closes_on_its_anchor_and_a_tap_outside(tmp_path):
    ws, drv = _desk(tmp_path)
    _open_by_tap(ws, drv, "desk", "clock")
    ws.bar_layer.handle_bar_tap("desk", *_cell(ws, "desk", "clock"))
    drv.frame(1 / 30)
    assert not ws.sysmenu.open, "the anchor tapped again closes it"
    _open_by_tap(ws, drv, "desk", "clock")
    px, py, pw, ph = ws.sysmenu.panel_rect()
    _tap(drv, px - 40, py + ph + 40)
    assert not ws.sysmenu.open, "a tap outside the card closes it"


def test_a_handheld_opens_the_clock_from_the_bar_by_keys_alone(tmp_path):
    ws, drv = _handheld(tmp_path)
    _press(drv, "up")
    for _ in range(8):
        it = ws.bar_layer.focus_item
        if it is not None and it[2] == "clock":
            break
        _press(drv, "right")
    assert ws.bar_layer.focus_item[2] == "clock"
    _press(drv, "a")
    assert ws.sysmenu.open and ws.sysmenu.widget == "clock"
    y, m = _this_month(ws)
    _press(drv, "right")
    assert _shown(ws) == shift_month(y, m, 1)
    _press(drv, "left")
    _press(drv, "left")
    assert _shown(ws) == shift_month(y, m, -1)
    _press(drv, "b")
    assert not ws.sysmenu.open


def test_a_handheld_under_touch_pages_the_month_by_its_chevrons(tmp_path):
    ws, drv = _handheld(tmp_path)
    ws.use_inputs([("local", "touch")])
    drv.frame(1 / 30)
    _tap(drv, *_cell(ws, "home", "clock"))
    assert ws.sysmenu.open and ws.sysmenu.widget == "clock"
    assert _chevrons(ws, drv) == [1, -1]


# -- the power rows (the desk) and the control center (a handheld) ------------

def test_the_desk_power_rows_move_the_levels_by_keys(tmp_path):
    ws, drv = _desk(tmp_path)
    _open_by_tap(ws, drv, "desk", "battery")
    sl = ws.settings_layer
    vol, bri = sl.level("volume")[0], sl.level("brightness")[0]
    _press(drv, "left")
    assert sl.level("volume")[0] == vol - 1 and sl.level("brightness")[0] == bri
    _press(drv, "down")
    _press(drv, "right")
    assert sl.level("brightness")[0] == bri + 1 and sl.level("volume")[0] == vol - 1
    _press(drv, "b")
    assert not ws.sysmenu.open


def test_the_desk_power_rows_move_the_levels_by_taps(tmp_path):
    ws, drv = _desk(tmp_path)
    _open_by_tap(ws, drv, "desk", "battery")
    sl = ws.settings_layer
    lo, hi = sl.level("brightness")[1:]
    x, y, w, h = _box(ws, "brightness")
    _tap(drv, x + w - 6, y + h // 2)
    assert sl.level("brightness")[0] == hi, "a tap at the track's end takes its top"
    x, y, w, h = _box(ws, "volume")
    vol = sl.level("volume")[0]
    _tap(drv, x + w * 3 // 5, y + h // 2)
    assert lo <= sl.level("volume")[0] < vol, "a tap inside the track takes its value there"
    assert ws.sysmenu.open


def test_the_handheld_control_center_moves_the_levels_and_opens_wifi(tmp_path):
    ws, drv = _handheld(tmp_path)
    ws.toggle_popover("battery")
    drv.frame(1 / 30)
    assert type(ws.sysmenu.comp).__name__ == "ControlCenter"
    sl = ws.settings_layer
    vol, bri = sl.level("volume")[0], sl.level("brightness")[0]
    _press(drv, "left")
    assert sl.level("volume")[0] == vol - 1, "Volume is the first stop"
    _press(drv, "down")
    _press(drv, "right")
    assert sl.level("brightness")[0] == bri + 1
    _press(drv, "down")
    _press(drv, "a")
    assert ws.sysmenu.open and ws.sysmenu.widget == "wifi", "the Wi-Fi row opens Wi-Fi's popover"
    _press(drv, "b")
    assert not ws.sysmenu.open


@pytest.mark.parametrize("where", ["desk", "handheld"])
def test_the_power_popover_opens_from_the_bar_and_closes_on_b(tmp_path, where):
    if where == "desk":
        ws, drv = _desk(tmp_path)
        _open_by_tap(ws, drv, "desk", "battery")
    else:
        ws, drv = _handheld(tmp_path)
        _press(drv, "up")
        for _ in range(8):
            it = ws.bar_layer.focus_item
            if it is not None and it[2] == "cluster":
                break
            _press(drv, "right")
        _press(drv, "a")
        assert ws.sysmenu.open and ws.sysmenu.widget == "battery"
    _press(drv, "b")
    assert not ws.sysmenu.open
