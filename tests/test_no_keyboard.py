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
    ui_draw.input_kind(kind)
    assert ws.can_type() is types
    ws.soft_keyboard = True
    assert ws.can_type(), "a page's soft keyboard types under any input"


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


@pytest.mark.parametrize("kind", ["pad", "touch"])
def test_the_home_search_says_to_pair_a_keyboard_where_nothing_types(tmp_path, kind):
    ws = build_ws_with_shelf(tmp_path, 4)
    drv = make_drv(ws)
    drv.frame(1 / 30)
    ws.open_search()
    ui_draw.input_kind(kind)
    assert ws.no_keyboard_text in _printed(ws, drv)
    ui_draw.input_kind("keys")
    text = _printed(ws, drv)
    assert ws.no_keyboard_text not in text and "SEARCH: _" in text
