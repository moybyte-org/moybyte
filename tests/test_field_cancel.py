"""A text field's Done and Cancel (owner call, 2026-10-18): while a field is
being typed into, Enter is Done -- the text kept, focus leaves the field --
and Cancel puts back the text it had when typing began and leaves it. The
T-Deck's Cancel is Mic, which never types (its K types a letter there);
outside a field Mic stays the console's key. Esc and a pad's B cancel too,
and the first back in Settings' Name field cancels it rather than leaving
Settings with what was typed (design review N3-13)."""

import pytest

from runtime import ui_draw
from ws_helpers import build_ws, make_drv
from test_system_key import MIC, _tdeck_frame
from test_hint_inputs import _strip

_DT = 1 / 30


@pytest.fixture(autouse=True)
def _keys_after():
    yield
    ui_draw.input_kind("keys")


def _name_field(tmp_path, stored="Ada"):
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.system.set("name", stored)
    ws.open_settings()
    sl = ws.settings_layer
    sl.show_row("name")
    for _ in range(3):
        drv.frame(_DT)
    assert sl.editing_field() and ws.input.text_mode
    return ws, drv, sl


def _type(drv, text):
    for ch in text:
        drv.type_char(ord(ch))
        drv.frame(_DT)
        drv.frame(_DT)


def test_enter_keeps_the_text_and_leaves_the_field(tmp_path):
    ws, drv, sl = _name_field(tmp_path)
    _type(drv, "x")
    drv.type_char(13)
    drv.frame(_DT)
    drv.frame(_DT)
    assert ws.system.get("name") == "Adax"
    assert not sl.editing_field(), "Done leaves the field"


def test_the_tdeck_mic_cancels_the_field_and_puts_its_text_back(tmp_path):
    ws, drv, sl = _name_field(tmp_path)
    _type(drv, "xyz")
    ui_draw.input_kind("tdeck")
    _tdeck_frame(ws, MIC, text=True)
    assert not ws.sysmenu.open, "inside a field Mic is Cancel, not the console's key"
    drv.frame(_DT)
    drv.frame(_DT)
    assert ws.system.get("name") == "Ada"
    assert not sl.editing_field() and ws.screen == "settings"


def test_mic_outside_a_field_is_still_the_console_key(tmp_path):
    ws, drv, sl = _name_field(tmp_path)
    ui_draw.input_kind("tdeck")
    _tdeck_frame(ws, MIC, text=True)            # cancels: focus leaves the field
    drv.frame(_DT)
    drv.frame(_DT)
    ui_draw.input_kind("tdeck")
    _tdeck_frame(ws, MIC)
    assert ws.sysmenu.open


def test_esc_cancels_first_then_leaves(tmp_path):
    ws, drv, sl = _name_field(tmp_path)
    _type(drv, "q")
    sl.back()                                   # Esc, B, the bar's chevron
    drv.frame(_DT)
    drv.frame(_DT)
    assert ws.screen == "settings" and ws.system.get("name") == "Ada"
    sl.back()
    assert ws.screen != "settings"


def test_mic_cancels_the_wifi_password_prompt(tmp_path):
    class _Wifi:
        def status(self):
            return (False, None, None)

        def scan(self):
            return [("Cafe", 60, True)]

        def known(self):
            return []
    ws = build_ws(tmp_path)
    drv = make_drv(ws)
    ws.wifi = _Wifi()
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_wifi()
    sl._wifi_ask("Cafe")
    for _ in range(3):
        drv.frame(_DT)
    _type(drv, "abc")
    assert sl.editing_field()
    ui_draw.input_kind("tdeck")
    _tdeck_frame(ws, MIC, text=True)
    assert sl.wifi_pick is None and not ws.sysmenu.open


def test_the_home_search_is_a_field_mic_cancels(tmp_path):
    ws = build_ws(tmp_path)
    make_drv(ws).frame(_DT)
    ws.open_search()
    ws.set_search_query("sta")
    ui_draw.input_kind("tdeck")
    _tdeck_frame(ws, MIC, text=True)
    assert not ws.search_typing and ws.search_query == "" and not ws.sysmenu.open


def test_a_fields_hints_read_done_and_cancel_with_each_inputs_keys():
    from runtime import settings_layer
    from runtime import chrome
    th = chrome.theme_colors("night")
    assert settings_layer._HINTS["field"] == ("confirm", "Done", "cancel", "Cancel")
    for kind, keys in (("tdeck", "Mic"), ("keys", "Esc"), ("pad", "B")):
        assert _strip(th, ("cancel", "Cancel"), kind) == _strip(th, (keys, "Cancel"), kind)
    assert _strip(th, ("confirm", "Done", "cancel", "Cancel"), "tdeck")[0] \
        == _strip(th, ("enter", "Done", "Mic", "Cancel"), "tdeck")[0]
