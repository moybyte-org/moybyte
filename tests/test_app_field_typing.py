"""A field an app's page focuses takes the keyboard (native/moy_ui's
`api_typing`, applied by `Player._sync_typing`): while it holds focus the
console is in text mode, so the T-Deck's W A S D type letters and press no
direction (native/moy_input/moy_kbd.c, THE MOVE KEYS), and off it they step
the page again. One rule for an app on every runtime, the Python verbs' and
the run face's a Lua app draws through; a cart's own `textmode()` still
holds when no field is focused. The trackball steps an app's page, as it
does every surface that takes the keys (`Workstation.ball_steps`), and does
not show the cursor. Mic (the T-Deck's cancel), Esc and B cancel a field's
edit in progress: the field gets back the text the edit began from and
focus steps up off it (native/moy_ui's `api_cancel`, `Player.cancel_field`);
with no edit, Mic stays the console's key."""

import json

from runtime import host_app, ui_draw
from runtime.moy_input import InputTable
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

LUA_EDIT = """
function _draw()
  cls(0)
  ui.page("Channel")
  ui.button("Top")
  NAME = ui.field("Name", NAME or "Srbija", 11)
  ui.button("Save")
  if not STARTED then
    ui.focus("Name")
    STARTED = true
  end
  FOCUS = ui.focus()
end
"""

PY_EDIT = """
name = "Srbija"
focus = ""
started = False


def _draw():
    global name, focus, started
    cls(0)
    ui.page("Channel")
    ui.button("Top")
    name, done = ui.field("Name", name, 11)
    ui.button("Save")
    if not started:
        ui.focus("Name")
        started = True
    focus = ui.focus()
"""

LUA_TWO = """
function _draw()
  cls(0)
  ui.page("Radio")
  FIRST = ui.field("First", FIRST or "aa", 11)
  SECOND = ui.field("Second", SECOND or "bb", 11)
  if not STARTED then
    ui.focus("Second")
    STARTED = true
  end
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


def _name(ws):
    lua = ws.run.player._lua
    if lua is not None:
        return lua.get_global("NAME")
    return ws.run.player.ns["name"]


def _type(drv, text):
    for ch in text:
        drv.type_char(ord(ch))
        drv.frame(DT)
    drv.frame(DT)


def _mic(ws, drv):
    """The T-Deck's Mic let go, as console_input answers it (the host's
    table has no `start`): a field's cancel, else the console's key."""
    if not ws.cancel_field():
        ws.system_key()
    for _ in range(3):
        drv.frame(DT)


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


@pytest.mark.parametrize("runtime", ["lua", "python"])
def test_mic_cancels_an_apps_field_edit(tmp_path, runtime):
    ws, drv = _open(tmp_path, "Edit App", LUA_EDIT if runtime == "lua" else PY_EDIT, runtime)
    assert _focus(ws) == "Name" and ws.input.text_mode
    _type(drv, "xy")
    assert _name(ws) == "Srbijaxy"
    _mic(ws, drv)
    assert _name(ws) == "Srbija", "Mic gives the field back the text the edit began from"
    assert _focus(ws) == "Top", "and focus steps up off the field"
    assert not ws.input.text_mode
    assert not ws.sysmenu.open, "a cancel is not the console's key"


@pytest.mark.parametrize("runtime", ["lua", "python"])
def test_mic_on_a_field_with_no_edit_opens_the_menu(tmp_path, runtime):
    ws, drv = _open(tmp_path, "Edit App", LUA_EDIT if runtime == "lua" else PY_EDIT, runtime)
    assert _focus(ws) == "Name"
    assert not ws.run.player.cancel_field()
    _type(drv, "x")
    drv.type_char(8)                     # typed and deleted: nothing to give back
    for _ in range(2):
        drv.frame(DT)
    assert _name(ws) == "Srbija"
    _mic(ws, drv)
    assert ws.sysmenu.open, "with no edit in progress Mic is the console's key"
    assert _name(ws) == "Srbija"


def test_enter_keeps_what_it_confirmed(tmp_path):
    ws, drv = _open(tmp_path, "Edit App", LUA_EDIT, "lua")
    _type(drv, "1")
    drv.type_char(13)
    for _ in range(2):
        drv.frame(DT)
    assert _name(ws) == "Srbija1"
    assert not ws.run.player.cancel_field(), "Enter ends the edit: nothing to give back"
    _type(drv, "2")
    _mic(ws, drv)
    assert _name(ws) == "Srbija1"


def _stop(ws):
    """One frame of a board's input with a keyboard's Esc (`stop`) pressed,
    through the console's router."""
    was = ws.input
    i = InputTable()
    i.source("kbd").set_button("stop", True)
    i.begin_frame()
    ws.input = i
    try:
        ws.handle_input()
    finally:
        ws.input = was


@pytest.mark.parametrize("how", ["escape", "stop"])
def test_esc_cancels_a_field_edit_before_it_goes_back(tmp_path, how):
    ws, drv = _open(tmp_path, "Edit App", PY_EDIT, "python")
    _type(drv, "xy")
    assert ws.run.player.has_page(), "an edit in progress is the app's to step out of"
    if how == "escape":
        drv.escape()
    else:
        _stop(ws)
    for _ in range(3):
        drv.frame(DT)
    assert _name(ws) == "Srbija"
    assert _focus(ws) == "Top"
    assert ws.screen == "desktop", "the first Esc cancels the edit, the app stays"


def test_a_cancel_onto_the_field_above_gives_each_its_own_text(tmp_path):
    """Focus steps up from the cancelled field onto another field: the one
    it left gets its text back, the one it reaches keeps its own, and an
    edit there cancels to that."""
    ws, drv = _open(tmp_path, "Two Fields", LUA_TWO, "lua")
    lua = ws.run.player._lua
    assert _focus(ws) == "Second"
    _type(drv, "x")
    _mic(ws, drv)
    assert (lua.get_global("FIRST"), lua.get_global("SECOND")) == ("aa", "bb")
    assert _focus(ws) == "First" and ws.input.text_mode
    _type(drv, "y")
    assert lua.get_global("FIRST") == "aay"
    _mic(ws, drv)
    assert (lua.get_global("FIRST"), lua.get_global("SECOND")) == ("aa", "bb")
    assert not ws.sysmenu.open


# -- the shell's own apps ---------------------------------------------------------
#
# A shell app's field is drawn through its AppHost's context, so Mic reaches it
# there (`Workstation.cancel_field` asks `ui_draw.app_cancel` where the app has
# no `cancel_field` of its own). Each row opens the app's field on a draft and
# names where the app keeps it.

def _files_rename(ws):
    app = ws.files_app
    app.open_place("documents")
    app._act("NAME", "notes")
    return lambda: app.rename_text


def _appearance_name(ws):
    app = ws.appearance_app
    app._open_name()
    app.name_text = "bubble"
    app._damage.all()
    return lambda: app.name_text


def _storybook_rename(ws):
    app = ws.storybook_app
    app.typed = "Mine"
    app.asking = "rename"
    app._damage.all()
    return lambda: app.typed


def _myart_rename(ws):
    app = ws.myart_app
    app._verb("rename", "kite")
    return lambda: app.renaming[1]


SHELL_FIELDS = {"files": _files_rename, "appearance": _appearance_name,
                "storybook": _storybook_rename, "myart": _myart_rename}


@pytest.mark.parametrize("app_id", sorted(SHELL_FIELDS))
def test_mic_cancels_a_shell_apps_field(tmp_path, app_id):
    carts = str(tmp_path / "carts")
    from runtime import moy_carts
    moy_carts.save_file("docs", "notes", "x", carts)
    moy_carts.save_file("drawings", "kite",
                        moy_carts.encode_moyimg(8, 8, bytes((14,)) * 64), carts)
    ws = host_app.build_workstation(carts)
    drv = host_app.ConsoleDriver(ws)
    ws.open_app(ws._apps_by_id[app_id])
    for _ in range(3):
        drv.frame(DT)
    draft = SHELL_FIELDS[app_id](ws)
    for _ in range(3):
        drv.frame(DT)
    was = draft()
    _type(drv, "xy")
    assert draft() == was + "xy", "the field types each key once"
    _mic(ws, drv)
    assert draft() == was, "Mic gives the field back the text the edit began from"
    assert not ws.sysmenu.open, "a cancel is not the console's key"
    assert ws.wm.top_kind() == app_id, "and the app stays"
