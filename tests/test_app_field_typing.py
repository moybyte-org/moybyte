"""A field an app's page focuses takes the keyboard (native/moy_ui's
`api_typing`, applied by `Player._sync_typing`): while it holds focus the
console is in text mode, so the T-Deck's W A S D type letters and press no
direction (native/moy_input/moy_kbd.c, THE MOVE KEYS), and off it they step
the page again. One rule for an app on every runtime, the Python verbs' and
the run face's a Lua app draws through; a cart's own `textmode()` still
holds when no field is focused. The trackball steps an app's page, as it
does every surface that takes the keys (`Workstation.ball_steps`), and does
not show the cursor."""

import json

from runtime import host_app, ui_draw
from ws_helpers import build_ws, select_on_home

import pytest

DT = 1.0 / 30.0

LUA_APP = """
function _draw()
  cls(0)
  ui.page("Channel")
  NAME = ui.field("Name", NAME or "", 11)
  if ui.button("Save") then SAVED = (SAVED or 0) + 1 end
  FOCUS = ui.focus()
end
"""

LUA_TEXTMODE = """
function _init() textmode(true) end
function _draw() cls(0) end
"""

PY_APP = """
name = ""
focus = ""


def _draw():
    global name, focus
    cls(0)
    ui.page("Channel")
    name, done = ui.field("Name", name, 11)
    ui.button("Save")
    focus = ui.focus()
"""


@pytest.fixture(autouse=True)
def _tdeck_keys():
    was = ui_draw.input_kind()
    ui_draw.input_kind("tdeck")
    yield
    ui_draw.input_kind(was)


def _write(carts, name, src, runtime):
    d = carts / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    main = "main.lua" if runtime == "lua" else "main.py"
    (d / "manifest.json").write_text(json.dumps(
        {"title": name, "canvas": "320x240", "runtime": runtime, "main": main,
         "moybyte": {"type": "app", "permissions": ["graphics"]}}))
    (d / main).write_text(src)
    (d / "config.json").write_text("{}")


def _open(tmp_path, name, src, runtime):
    _write(tmp_path / "carts", name, src, runtime)
    ws = build_ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    assert select_on_home(ws, name) is not None
    ws.open()
    for _ in range(3):
        drv.frame(DT)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.screen == "desktop"
    return ws, drv


def _focus(ws):
    lua = ws.run.player._lua
    if lua is not None:
        return lua.get_global("FOCUS")
    return ws.run.player.ns["focus"]


def _step(drv, button):
    drv.press(button)
    for _ in range(3):
        drv.frame(DT)


@pytest.mark.parametrize("runtime", ["lua", "python"])
def test_a_focused_field_holds_the_keyboard_in_text_mode(tmp_path, runtime):
    ws, drv = _open(tmp_path, "Field App", LUA_APP if runtime == "lua" else PY_APP, runtime)
    assert _focus(ws) == "Name"
    assert ws.input.text_mode, "a focused field types: W A S D are letters there"
    _step(drv, "down")
    assert _focus(ws) == "Save"
    assert not ws.input.text_mode, "off the field the move keys step the page"
    _step(drv, "up")
    assert _focus(ws) == "Name" and ws.input.text_mode
    for _ in range(10):
        drv.frame(DT)
    assert ws.input.text_mode, "a page drawn on change keeps its field's text mode"


def test_a_lua_field_types_the_keys(tmp_path):
    ws, drv = _open(tmp_path, "Field App", LUA_APP, "lua")
    for ch in "wasd":
        drv.type_char(ord(ch))
        drv.frame(DT)
    drv.frame(DT)
    assert ws.run.player._lua.get_global("NAME") == "wasd"
    assert _focus(ws) == "Name"


def test_a_lua_carts_textmode_reaches_the_keyboard(tmp_path):
    ws, drv = _open(tmp_path, "Typing Cart", LUA_TEXTMODE, "lua")
    assert ws.input.text_mode, "textmode(true) from a Lua cart is the console's text mode"


@pytest.mark.parametrize("runtime", ["lua", "python"])
def test_the_trackball_steps_an_apps_page_and_shows_no_cursor(tmp_path, runtime):
    ws, drv = _open(tmp_path, "Field App", LUA_APP if runtime == "lua" else PY_APP, runtime)
    assert ws.ball_steps(), "an app's page takes the trackball as its W A S D"
    ws._ball_axis_at -= ws.BALL_ROLLING_MS + 1
    drv.pan(0, 1)
    for _ in range(2):
        drv.frame(DT)
    drv.pan(0, 0)
    for _ in range(3):
        drv.frame(DT)
    assert _focus(ws) == "Save", "a roll down is a step down the page"
    assert not ws.pointer.visible
