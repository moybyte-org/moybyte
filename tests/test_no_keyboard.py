"""A text field where nothing can type (owner call 2026-10-18, the interim
for #239's on-screen keyboard): with touch alone or a gamepad the input in
use, a field says "Pair a keyboard to type" instead of a hint that promises a
keystroke. A keyboard types (the desk's, the T-Deck's), and so does a page
with a soft keyboard of its own (the browser's)."""

import pytest

from runtime import ui_draw
from ws_helpers import build_ws, build_ws_with_shelf, make_drv


@pytest.fixture(autouse=True)
def _keys_after():
    yield
    ui_draw.input_kind("keys")


@pytest.mark.parametrize("kind,types", [("keys", True), ("tdeck", True),
                                        ("pad", False), ("touch", False)])
def test_only_a_keyboard_types(tmp_path, kind, types):
    ws = build_ws(tmp_path)
    ws.use_inputs([("local", kind)])
    assert ws.can_type() is types
    ws.soft_keyboard = True
    assert ws.can_type(), "a page's soft keyboard types under any input"


def test_a_tap_leaves_the_t_decks_keyboard_typing(tmp_path):
    """The T-Deck Plus has a keyboard and a touch panel: a tap makes touch
    the input in use (its hints hide) and the keyboard can still type (R4-07)."""
    ws = build_ws(tmp_path)
    ws.use_inputs([("kbd", "tdeck"), ("ble", "keys"), ("touch", "touch")])
    ws.input_used("touch")
    assert ui_draw.input_kind() == "touch"
    assert ws.can_type()


class _Ble:
    settings_capable = True

    def __init__(self, state):
        self.state = state

    def settings_status(self):
        return True, self.state, "Pocket Keys", None, None


def test_a_bluetooth_keyboard_types_while_it_is_connected(tmp_path):
    """A touch board declares its Bluetooth keyboard from boot: it types
    once one is connected, whatever was touched last."""
    ws = build_ws(tmp_path)
    ws.use_inputs([("touch", "touch"), ("ble", "keys")])
    ws.keyboard = kbd = _Ble("scanning")
    assert not ws.can_type()
    kbd.state = "ready"
    assert ws.can_type()
    ws.input_used("touch")
    assert ws.can_type()


def _printed(ws, drv):
    """The text one repaint of the home prints."""
    out = []
    cv = ws.sys_canvas
    real = cv.print

    def spy(text, *a, **kw):
        out.append(str(text))
        return real(text, *a, **kw)
    cv.print = spy
    try:
        ws._dirty = True
        drv.frame(1 / 30)
    finally:
        cv.print = real
    return " | ".join(out)


@pytest.mark.parametrize("kind,field", [("keys", True), ("tdeck", True), ("pad", False),
                                        ("touch", False)])
def test_settings_name_is_a_field_only_where_something_types(tmp_path, kind, field):
    """Under touch alone or a gamepad the Name row shows the saved name,
    read-only, and says to pair a keyboard to change it (an info row, no
    stop to type into, no Done hint)."""
    from runtime import settings_layer
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.system.set("name", "Moy")
    ws.open_settings()
    ws.use_inputs([("local", kind)])
    ws.settings_layer.show_row("name")
    rows = []
    ui = settings_layer._ui
    real = ui.info, ui.label
    ui.info = lambda label, value, o=None: (rows.append((label, value)), real[0](label, value, o))
    ui.label = lambda text, o=None: (rows.append((text,)), real[1](text, o))
    try:
        for _ in range(3):
            ws._dirty = True
            drv.frame(1 / 30)
    finally:
        ui.info, ui.label = real
    assert ("field" in ws.settings_layer._kinds.values()) is field
    if not field:
        assert ("Name", "Moy") in rows and (settings_layer.NAME_NO_KEYBOARD,) in rows, rows


@pytest.mark.parametrize("kind,hints", [("keys", ("confirm", "Connect", "back", "Cancel")),
                                        ("pad", ("back", "Cancel")),
                                        ("touch", ("back", "Cancel"))])
def test_the_wifi_prompt_promises_connect_only_where_something_types(tmp_path, kind, hints):
    """The password prompt's strip names Connect only where a field is there
    to type into; elsewhere it is Cancel alone (R4-08)."""
    from runtime import ui as ui_api
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.open_settings()
    ws.use_inputs([("local", kind)])
    sl = ws.settings_layer
    sl.open_wifi()
    sl.wifi_pick = "Cafe"
    got = []
    real = ui_api.hints

    def spy(items, *a, **kw):
        got.append(tuple(items))
        return real(items, *a, **kw)
    ui_api.hints = spy
    try:
        for _ in range(2):
            ws._dirty = True
            drv.frame(1 / 30)
    finally:
        ui_api.hints = real
    assert hints in got and all(h == hints for h in got if "Cancel" in h), got


@pytest.mark.parametrize("kind", ["pad", "touch"])
def test_the_home_search_says_to_pair_a_keyboard_where_nothing_types(tmp_path, kind):
    ws = build_ws_with_shelf(tmp_path, 4)
    drv = make_drv(ws)
    drv.frame(1 / 30)
    ws.open_search()
    ws.use_inputs([("local", kind)])
    assert ws.no_keyboard_text in _printed(ws, drv)
    ws.use_inputs([("local", "keys")])
    text = _printed(ws, drv)
    assert ws.no_keyboard_text not in text and "SEARCH: _" in text
