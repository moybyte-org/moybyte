"""The keys on the desk's bar (desktop mode): up past the top of the focused
window's page puts them on the bar, left and right walk its items -- the
system button, a chip per window, the status items, Wi-Fi, the battery and
the clock, each a stop of the bar page -- confirm runs the item, and down,
back and stop hand them back to the window. A keyboard's arrows and a
gamepad's d-pad walk the same stops, and a tap reaches each item alone."""

from runtime import moy_input as _mi
from ws_helpers import build_desktop_ws, make_drv

WALK = ["menu", "settings/chip", "files/chip", "wifi", "battery", "clock"]


def _desk(tmp_path):
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0)
    drv = make_drv(ws)
    for _ in range(3):
        drv.frame(1 / 30)
    ws.open_settings()
    ws.spine.open_app(ws.app("files"))
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.wm.desk_open() and ws.wm.focus_key == "files"
    return ws, drv


def _pad(drv, name):
    drv.hold(name, True, source="pad")
    drv.frame(1 / 30)
    drv.hold(name, False, source="pad")
    drv.frame(1 / 30)


def _arrow(drv, name):
    code = {"up": _mi.KEY_UP, "down": _mi.KEY_DOWN, "left": _mi.KEY_LEFT,
            "right": _mi.KEY_RIGHT}.get(name)
    if code is None:
        drv.press(name)
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        return
    drv.key_down(code)
    drv.frame(1 / 30)
    drv.key_up(code)
    drv.frame(1 / 30)


def _stop(ws):
    bl = ws.bar_layer
    return bl._page_stop("desk", bl.focus_item) if bl.focus is not None else None


def _onto_bar(ws, step):
    for _ in range(8):
        step("up")
        if ws.bar_layer.focus is not None:
            return
    raise AssertionError("up past the window's top never reached the desk's bar")


def _walk(ws, step):
    _onto_bar(ws, step)
    assert ws.bar_layer.focus_where == "desk"
    got = [_stop(ws)]
    for _ in range(len(WALK) + 1):
        step("right")
        got.append(_stop(ws))
    return got


def test_the_keyboard_walks_every_item_of_the_desk_s_bar(tmp_path):
    ws, drv = _desk(tmp_path)
    got = _walk(ws, lambda n: _arrow(drv, n))
    assert got == WALK + [WALK[-1], WALK[-1]]


def test_the_gamepad_walks_every_item_of_the_desk_s_bar(tmp_path):
    ws, drv = _desk(tmp_path)
    got = _walk(ws, lambda n: _pad(drv, n))
    assert got == WALK + [WALK[-1], WALK[-1]]


def test_a_stop_on_the_bar_wears_the_page_s_focus_mark(tmp_path):
    ws, drv = _desk(tmp_path)
    bl = ws.bar_layer
    bx, by, bw, bh = bl.bar_rects("desk")[0]
    cv = ws.glass_canvas

    def strip():
        return bytes(cv._buf[2 * by * cv.w:2 * (by + bh) * cv.w])

    before = strip()
    _onto_bar(ws, lambda n: _arrow(drv, n))
    seen = {strip()}
    for _ in range(len(WALK) - 1):
        _arrow(drv, "right")
        seen.add(strip())
    assert before not in seen and len(seen) == len(WALK)


def test_confirm_on_a_chip_focuses_its_window_and_down_hands_the_keys_back(tmp_path):
    ws, drv = _desk(tmp_path)
    step = lambda n: _arrow(drv, n)     # noqa: E731
    _onto_bar(ws, step)
    step("right")
    assert _stop(ws) == "settings/chip"
    step("a")
    assert ws.wm.focus_key == "settings"
    assert _stop(ws) == "settings/chip"         # the keys stay on the chip
    step("down")
    assert ws.bar_layer.focus is None
    assert ws.wm.focus_key == "settings"


def test_confirm_on_the_system_button_opens_the_system_menu(tmp_path):
    ws, drv = _desk(tmp_path)
    _onto_bar(ws, lambda n: _pad(drv, n))
    assert _stop(ws) == "menu"
    _pad(drv, "a")
    assert ws.sysmenu.open and ws.sysmenu.widget == "menu"


def test_a_tap_reaches_every_item_of_the_desk_s_bar(tmp_path):
    ws, drv = _desk(tmp_path)
    bl = ws.bar_layer
    bx, by = bl.bar_rects("desk")[0][:2]
    items = bl.focus_items("desk")
    assert [bl._page_stop("desk", it) for it in items] == WALK
    for it in items:
        if ws.sysmenu.open:
            ws.sysmenu.close()
        for _ in range(2):
            drv.frame(1 / 30)
        x, y, w, h = it[1]
        drv.click(bx + x + w // 2, by + y + h // 2)
        for _ in range(2):
            drv.frame(1 / 30)
        stop = bl._page_stop("desk", it)
        if stop.endswith("/chip"):
            assert ws.wm.focus_key == stop[:-5], stop
        else:
            assert ws.sysmenu.open and ws.sysmenu.widget == stop, stop


def test_no_keys_reach_the_desk_s_bar_under_a_full_screen_window(tmp_path):
    ws, drv = _desk(tmp_path)
    assert ws.wm.full_key()
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.router.bar_where() is None
    for _ in range(4):
        _arrow(drv, "up")
    assert ws.bar_layer.focus is None
