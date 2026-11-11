"""The browser declares its inputs as a board does (docs/ui_api_2026-10.md,
"Hints"): a phone's page (`(pointer: coarse)`, which page_tail.js tells the
worker at init) is on touch from boot, a key brings the keys back and the
page's pointer press puts touch back in use; a fine pointer is a mouse, no
finger, and its page stays on its keys."""

import json
import os
import sys
from pathlib import Path

import pytest

from runtime import ui_draw
from ws_helpers import build_ws, make_drv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def web_boot():
    """web_boot imported for its declarations and its pointer sink -- no VM,
    no canvas (as tests/test_moy_sync.py imports it)."""
    for p in ("firmware/web_runner", "device", "runtime"):
        d = os.path.join(ROOT, p)
        if d not in sys.path:
            sys.path.insert(0, d)
    try:
        import web_boot
    except Exception as exc:  # noqa: BLE001 -- a wasm-only dep would skip, not fail
        pytest.skip("web_boot not importable on the host: %s" % exc)
    yield web_boot
    ui_draw.input_kind("keys")


def test_a_coarse_pointer_page_starts_on_touch_and_a_key_brings_the_keys(web_boot,
                                                                         tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    ws.use_inputs(web_boot.WEB_TOUCH_INPUTS)
    assert ui_draw.input_kind() == "touch"
    drv.press("down")
    drv.frame(1 / 30)
    assert ui_draw.input_kind() == "keys"
    sink = web_boot._PointerSink(drv)
    sink.down = True
    assert ui_draw.input_kind() == "touch", "the page's pointer is a finger"


def test_a_fine_pointer_page_stays_on_its_keys(web_boot, tmp_path):
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    ws.use_inputs(web_boot.WEB_INPUTS)
    web_boot._PointerSink(drv).click = True
    assert ui_draw.input_kind() == "keys", "a mouse is no finger"


def _pad_hold(drv, name, down):
    """One hold from the page's on-screen gamepad, through the decode the
    worker runs (web_boot._apply's callbacks)."""
    from runtime import web_input
    web_input.apply_events([{"type": "hold", "name": name, "down": down, "src": "pad"}],
                           drv.input, drv.pointer, on_hold=drv.hold)


@pytest.mark.parametrize("coarse", [True, False])
def test_the_pages_gamepad_is_the_pad_and_its_keys_stay_the_keyboards(web_boot, tmp_path,
                                                                      coarse):
    """A tap on the page's joystick or A/B (design review R3-17) makes the
    gamepad the input in use, so the hints draw its buttons; a desk key after
    it brings the keyboard's back. The pad's hold is its own source's, so the
    button reads held through the table like any other."""
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    ws.use_inputs(web_boot.WEB_TOUCH_INPUTS if coarse else web_boot.WEB_INPUTS)
    _pad_hold(drv, "a", True)
    drv.frame(1 / 30)
    assert ui_draw.input_kind() == "pad"
    assert "a" in drv.input.held_names()
    _pad_hold(drv, "a", False)
    drv.frame(1 / 30)
    assert "a" not in drv.input.held_names()
    drv.press("down")
    drv.frame(1 / 30)
    assert ui_draw.input_kind() == "keys"


def test_the_pages_joystick_and_buttons_send_the_pad_source():
    """The page's joystick (jAp) and its A/B (wb) name the pad source on
    every hold, down and up; the keyboard's holds name none."""
    import re
    page = Path(ROOT, "firmware", "web_runner", "page_core.html").read_text(encoding="utf-8")
    jap = page[page.index("function jAp("):page.index("function jT(")]
    wb = page[page.index("function wb("):page.index('wb("ba"')]
    assert jap.count('src:"pad"') == 1 and 'type:"hold"' in jap
    assert wb.count('src:"pad"') == 2, "the press and the release"
    keys = page[page.index('cv.addEventListener("keydown"'):]
    assert not re.search(r'type:"hold"[^}]*src:', keys), "a key is the keyboard's"


def test_the_pages_mouse_reaches_the_consoles_pointer_as_a_mouse(web_boot, tmp_path):
    """A hover makes the console's pointer a mouse (it hovers, as a finger
    never does: the text views select under its drag), and a press holds the
    pointer's primary button in the input table."""
    from runtime import moy_input, web_input
    ws = build_ws(Path(tmp_path))
    drv = make_drv(ws)
    sink = web_boot._PointerSink(drv)
    web_input.apply_events([{"type": "hover", "x": 30, "y": 40}], drv.input, sink)
    assert ws.pointer.hovers and (ws.pointer.x, ws.pointer.y) == (30, 40)
    web_input.apply_events([{"type": "down", "x": 30, "y": 40, "mods": moy_input.SHIFT}],
                           drv.input, sink, on_mods=drv.set_mods)
    ws.input.begin_frame()
    assert ws.input.pointer_buttons == moy_input.PB_PRIMARY
    assert ws.input.mods & moy_input.SHIFT


def test_one_frame_takes_one_change_of_the_pointer():
    from runtime import web_input
    down, up = {"type": "down", "x": 1, "y": 2}, {"type": "up"}
    move = {"type": "move", "x": 5, "y": 2}
    assert web_input.frame_cut([]) == 0
    assert web_input.frame_cut([down, up, {"type": "hover", "x": 1, "y": 2}]) == 3
    assert web_input.frame_cut([down, up, down, up]) == 2
    assert web_input.frame_cut([{"type": "key", "code": 97}, down, up, down]) == 3
    assert web_input.frame_cut([down, move, up]) == 1
    assert web_input.frame_cut([move, move, up]) == 2
    assert web_input.frame_cut([up, {"type": "hover", "x": 1, "y": 2}, down]) == 3


def _web_code(web_boot, tmp_path, monkeypatch, text):
    from test_mouse_pointer import _code, _code_xy
    ws, d, ed = _code(Path(tmp_path), text)
    monkeypatch.setattr(web_boot, "_S", {"ws": ws, "driver": d,
                                         "sink": web_boot._PointerSink(d)})

    def at(row, col):
        return _code_xy(ws, ed, row, col)

    def send(*events):
        web_boot.apply_events_json(json.dumps({"events": list(events)}))

    x, y = at(0, 0)
    send({"type": "hover", "x": x, "y": y})
    web_boot.step_frame_json(1 / 30)
    return ed, at, send


def test_a_double_click_that_reaches_one_frame_takes_the_word(web_boot, tmp_path, monkeypatch):
    """A slow frame lets both presses of a double click reach the page's next
    frame in one batch. The pointer carries one press a frame, so the second
    goes to the frame after it: the Code tab sees two presses at one place
    and takes the word."""
    ed, at, send = _web_code(web_boot, tmp_path, monkeypatch, "alpha beta_2 gamma\nsecond")
    x, y = at(0, 8)
    click = [{"type": "down", "x": x, "y": y}, {"type": "up"}]
    send(*(click + click))
    web_boot.step_frame_json(1 / 30)
    assert not ed.has_selection(), "the first press only places the caret"
    web_boot.step_frame_json(1 / 30)
    assert ed.selected_text() == "beta_2"
    assert web_boot._S["evq"] == []


def test_a_drag_that_reaches_one_frame_selects_to_its_end(web_boot, tmp_path, monkeypatch):
    """A whole drag in one batch -- the press, its moves and the release --
    is the press's frame, the held moves' and the release's, so the
    selection reaches the place the button went up at."""
    ed, at, send = _web_code(web_boot, tmp_path, monkeypatch, "alpha beta_2 gamma\nsecond")
    (x0, y), (x2, _), (x4, _) = at(0, 0), at(0, 2), at(0, 4)
    send({"type": "down", "x": x0, "y": y}, {"type": "move", "x": x2, "y": y},
         {"type": "move", "x": x4, "y": y}, {"type": "up"})
    for _ in range(3):
        web_boot.step_frame_json(1 / 30)
    assert ed.selected_text() == "alph"
    assert web_boot._S["evq"] == []
