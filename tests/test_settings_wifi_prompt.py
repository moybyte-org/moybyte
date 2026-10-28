"""Settings' Wi-Fi password prompt is the engine's field in a dialog over the
network list (runtime/settings_layer.py `_wifi_prompt`): focus opens on the
field, which every typed key reaches; Enter in it or Connect connects with
what was typed, Esc or Cancel goes back to the list. It draws as every
field does -- an outline that clears its ground 3:1 and a bar caret --
apart from its buttons, which sit side by side at the dialog's foot, and
a focused Connect shows its focus on top of its primary fill.
"""

from pathlib import Path

import pytest

from runtime import host_app
from tests.test_ui_parts import _ratio

_DT = 1 / 30
_FIVE = (("moy", "dark"), ("moy", "light"), ("blocks", "light"), ("contrast", "dark"),
         ("contrast", "light"))


class _Wifi(host_app.FakeWifi):
    def __init__(self):
        super().__init__()
        self.tried = []

    def connect(self, ssid, password=""):
        self.tried.append((ssid, password))
        return super().connect(ssid, password)


def _prompt(tmp_path, theme=("moy", "dark")):
    ws = host_app.build_workstation(str(Path(tmp_path) / "carts"))
    ws.look.set_theme(theme[0], persist=False, variant=theme[1])
    ws.wifi = _Wifi()
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_wifi()
    drv.frame(_DT)
    ws.input.last_key = 0x0D                      # the Enter that picked the row
    sl._wifi_ask("Home WiFi")
    drv.frame(_DT)
    ws.input.last_key = 0
    drv.frame(_DT)
    drv.frame(_DT)
    return ws, drv, sl


def _type(drv, text):
    for ch in text:
        drv.type_char(ord(ch))
        drv.frame(_DT)
        drv.frame(_DT)


def _px(ws):
    cv = ws.sys_canvas
    b = cv._buf
    return lambda x, y: b[2 * (y * cv.w + x)] << 8 | b[2 * (y * cv.w + x) + 1]


def test_the_prompt_types_into_its_field_and_enter_connects(tmp_path):
    ws, drv, sl = _prompt(tmp_path)
    assert sl.wifi_pick == "Home WiFi" and ws.wifi.tried == [], \
        "the Enter that opened the prompt connects nothing"
    assert sl._sub.box("password") is not None, "the prompt is the engine's field"
    _type(drv, "hunter2")
    assert sl.wifi_pw.text == "hunter2"
    drv.type_char(8)                               # Backspace takes a character
    drv.frame(_DT)
    drv.frame(_DT)
    assert sl.wifi_pw.text == "hunter"
    drv.type_char(13)
    drv.frame(_DT)
    assert ws.wifi.tried == [("Home WiFi", "hunter")] and sl.wifi_pick is None


def test_esc_cancels_the_prompt_back_to_the_list(tmp_path):
    ws, drv, sl = _prompt(tmp_path)
    _type(drv, "abc")
    drv.type_char(27)
    drv.frame(_DT)
    assert sl.wifi_pick is None and ws.wifi.tried == [] and not ws.input.text_mode


def _buttons(ws, sl):
    """The dialog's buttons under its field, where the engine drew them: the
    row their tops run along, and each button's (x0, x1) on it."""
    boxes = [sl._sub.box(n) for n in ("Cancel", "Connect")]
    if None in boxes:
        return None, []
    return boxes[0][1], [(x, x + w) for x, _y, w, _h in boxes]


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_the_prompts_field_and_buttons_draw_apart_and_outlined(tmp_path, theme):
    ws, drv, sl = _prompt(tmp_path, theme)
    _type(drv, "hunter2")
    px = _px(ws)
    fx, fy, fw, fh = sl._sub.box("password")
    top, runs = _buttons(ws, sl)
    assert len(runs) == 2, "Cancel and Connect side by side: %s" % runs
    assert runs[1][0] - runs[0][1] >= 4, "the buttons stand apart"
    assert top >= fy + fh + 8, "8 px or more between the field's row and the buttons"
    # the field's outline, found from its right end inwards, clears the
    # dialog's card under it 3:1 (the field under its caption, the stop's
    # lower part)
    mid = fy + fh * 3 // 4
    card = px(fx + fw - 1, mid)
    ring = next(px(x, mid) for x in range(fx + fw - 1, fx, -1) if px(x, mid) != card)
    assert _ratio(ring, card) >= 3.0, "the field's outline %.2f:1" % _ratio(ring, card)


@pytest.mark.parametrize("theme", _FIVE, ids=lambda t: "-".join(t))
def test_a_focused_connect_shows_its_focus_over_its_fill(tmp_path, theme):
    """Connect is the primary button: focused, it draws differently from
    the primary at rest, by a mark that clears what it covers 3:1 (Moy's
    focused button and its primary were one fill, so focus on Connect
    could not be told)."""
    ws, drv, sl = _prompt(tmp_path, theme)
    top, runs = _buttons(ws, sl)
    x0, x1 = runs[1]
    box = [(x, y) for x in range(x0 - 3, x1 + 3) for y in range(top - 3, top + 30)]
    rest = [_px(ws)(x, y) for x, y in box]
    for _ in range(3):                               # the field, then Cancel, then Connect
        drv.press("down") if _ == 0 else drv.press("right")
        drv.frame(_DT)
        drv.frame(_DT)
        if sl._sub_focused == "Connect":
            break
    assert sl._sub_focused == "Connect"
    focused = [_px(ws)(x, y) for x, y in box]
    changed = [(a, b) for a, b in zip(rest, focused) if a != b]
    assert len(changed) >= (x1 - x0) // 2, "focus on Connect draws a mark"
    assert any(_ratio(a, b) >= 3.0 for a, b in changed), "a mark that clears what it covers 3:1"
