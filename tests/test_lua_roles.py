"""A Lua app's roles (docs/os_2026-11/tiers.md §2): the role binding over the
app ABI's table and its ROLE door, the rows a compiled app's imports reach
(native/moy_app/moy_app_wasm.h), and Lua's `io`/`os` over the files tree
under the app's permissions.

A Lua app holding a role gets its verbs; one not holding it, or one whose
permission Settings turned off, has no name for them (`nil` in Lua: never 0,
never a stub). `io` and `os`'s file verbs reach only the files kind the grant
names: a path that names anything else is refused, never resolved. The
Python face and the Lua face answering the same calls the same way is the
roles trace for a Lua app (tests/test_semantic_traces.py).
"""

import json
from pathlib import Path

from runtime import app_perms
from ws_helpers import build_ws, select_on_home

DT = 1.0 / 30.0


def _write_app(carts_dir, name, src, perms, lua=True):
    d = Path(carts_dir) / (name + ".moy")
    d.mkdir(parents=True, exist_ok=True)
    man = {"title": name, "canvas": "320x240",
           "moybyte": {"type": "app", "permissions": list(perms)}}
    if lua:
        man.update({"runtime": "lua", "main": "main.lua"})
    (d / "manifest.json").write_text(json.dumps(man))
    (d / ("main.lua" if lua else "main.py")).write_text(src)
    (d / "config.json").write_text("{}")


def _run(tmp_path, src, perms, off=(), frames=2):
    """Launch the Lua app `src` holding `perms` (less `off`, turned off in
    Settings) and run `frames` frames: the workstation and its Lua run."""
    _write_app(tmp_path / "carts", "Roles", src, perms)
    ws = build_ws(tmp_path)
    cart = select_on_home(ws, "Roles")
    assert cart is not None
    for p in off:
        ws.consent.turn(app_perms.app_id(cart), p, False)
    ws.open()
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(DT)
    assert ws.player.cart_error is None, ws.player.cart_error
    return ws, ws.player._lua


NAMES_LUA = """
SEEN = ""
function _init()
  local t = {}
  for _, n in ipairs({"files", "prefs", "set_theme", "themes", "open_app",
                      "io", "os", "clipboard"}) do
    t[#t + 1] = n .. "=" .. type(_G[n])
  end
  if os then
    t[#t + 1] = "os.remove=" .. type(os.remove)
    t[#t + 1] = "os.rename=" .. type(os.rename)
    t[#t + 1] = "os.time=" .. type(os.time)
  end
  SEEN = table.concat(t, " ")
end
function _draw() cls(0) end
"""


def test_a_lua_app_holding_a_role_gets_its_verbs(tmp_path):
    _ws, lua = _run(tmp_path, NAMES_LUA,
                    ["graphics", "files:docs", "prefs", "appearance", "launch",
                     "clipboard"])
    assert lua.get_global("SEEN") == (
        "files=table prefs=table set_theme=function themes=function "
        "open_app=function io=table os=table clipboard=nil "
        "os.remove=function os.rename=function os.time=function")


def test_a_lua_app_without_a_role_has_no_name_for_it(tmp_path):
    _ws, lua = _run(tmp_path, NAMES_LUA, ["graphics", "input"])
    assert lua.get_global("SEEN") == (
        "files=nil prefs=nil set_theme=nil themes=nil open_app=nil io=nil "
        "os=table clipboard=nil os.remove=nil os.rename=nil os.time=function")


def test_a_permission_turned_off_reads_nil_in_lua(tmp_path):
    _ws, lua = _run(tmp_path, NAMES_LUA, ["graphics", "files:docs", "prefs"],
                    off=("files:docs", "prefs"))
    assert lua.get_global("SEEN") == (
        "files=nil prefs=nil set_theme=nil themes=nil open_app=nil io=nil "
        "os=table clipboard=nil os.remove=nil os.rename=nil os.time=function")


def test_a_lua_game_has_no_app_names(tmp_path):
    """A cart that is not an app makes no grant: nothing of the binding."""
    d = tmp_path / "carts" / "Game.moy"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"format": "moy-1", "title": "Game", "runtime": "lua", "main": "main.lua",
         "moybyte": {"type": "game"}}))
    (d / "main.lua").write_text(NAMES_LUA)
    (d / "config.json").write_text("{}")
    ws = build_ws(tmp_path)
    assert select_on_home(ws, "Game") is not None
    ws.open()
    ws.input.begin_frame()
    ws.frame(DT)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player._lua.get_global("SEEN") == (
        "files=nil prefs=nil set_theme=nil themes=nil open_app=nil io=nil "
        "os=nil clipboard=nil")


IO_LUA = """
LOG = {}
local function say(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end
  LOG[#LOG + 1] = table.concat(t, " ")
end

function _init()
  local f = assert(io.open("notes.txt", "w"))
  say("type", io.type(f), io.type(42))
  say("write", f:write("one\\n", 12, "\\n", "last") == f)
  f:close()
  say("closed", io.type(f), tostring(f))
  f = assert(io.open("notes.txt"))
  say("read l", f:read("l"))
  say("read n", f:read("n"))
  say("read a", f:read("a"))
  say("read eof", f:read("l"), f:read("a"))
  say("seek", f:seek("set", 4), f:read(2), f:seek())
  f:close()
  local lines = {}
  for line in io.lines("notes.txt") do lines[#lines + 1] = line end
  say("lines", #lines, lines[1], lines[3])
  f = assert(io.open("notes.txt", "a"))
  f:write("!")
  f:close()
  f = assert(io.open("notes.txt", "rb"))
  say("appended", f:read("a"))
  f:close()
  say("missing", io.open("missing.txt"))
  say("climb", io.open("../carts/Roles.moy/main.lua"))
  say("absolute", io.open("/etc/password"))
  say("folder", io.open("drawings/pic"))
  local ok, why = pcall(io.open, "notes.txt", "x")
  say("mode", ok, why:match("invalid mode"))
  say("rename", os.rename("notes.txt", "moved.txt"))
  say("renamed", io.open("notes.txt") == nil, io.open("moved.txt") ~= nil)
  say("remove", os.remove("moved.txt"))
  say("removed", os.remove("moved.txt"))
  say("remove out", os.remove("/sd/moybyte/carts"))
  say("rename out", os.rename("x.txt", "../x.txt"))
  say("time", math.type(os.time()), os.time() > 1600000000,
      math.type(os.clock()))
  TRACE = table.concat(LOG, "\\n")
end
function _draw() cls(0) end
"""


def test_lua_io_and_os_reach_only_the_granted_files(tmp_path):
    ws, lua = _run(tmp_path, IO_LUA, ["graphics", "files:docs"])
    assert lua.get_global("TRACE").split("\n") == [
        "type file nil",
        "write true",
        "closed closed file file (closed)",
        "read l one",
        "read n 12",
        "read a ",
        "last",
        "read eof nil ",
        "seek 4 12 6",
        "lines 3 one last",
        "appended one",
        "12",
        "last!",
        "missing nil missing.txt: No such file or directory 2",
        "climb nil ../carts/Roles.moy/main.lua: Permission denied 13",
        "absolute nil /etc/password: Permission denied 13",
        "folder nil drawings/pic: Permission denied 13",
        "mode false invalid mode",
        "rename true",
        "renamed true true",
        "remove true",
        "removed nil moved.txt: No such file or directory 2",
        "remove out nil /sd/moybyte/carts: Permission denied 13",
        "rename out nil ../x.txt: Permission denied 13",
        "time integer true float",
    ]
    # What io wrote is the files role's item, and the trash took the remove.
    files = ws.app_context("check", ("files",), prefs_ns="check").files
    assert files.load("docs", "moved.txt") == (None, None)
    assert ("docs", "moved.txt") in files.trash_list()[0]
    assert not (Path(ws.carts_root) / "x.txt").exists()


def test_lua_io_writes_a_document_python_reads(tmp_path):
    src = """
function _init()
  local f = assert(io.open("hello.txt", "w"))
  f:write("from lua")
  f:close()
  f = assert(io.open("plan", "w"))
  f:write("# a note")
  f:close()
end
function _draw() cls(0) end
"""
    ws, _lua = _run(tmp_path, src, ["graphics", "files"])
    files = ws.app_context("check", ("files",), prefs_ns="check").files
    assert files.load("docs", "hello.txt") == ("from lua", None)
    assert files.load("docs", "plan") == ("# a note", None)
    assert sorted(files.list("docs")[0]) == ["hello.txt", "plan"]


def test_a_lua_app_of_another_kind_cannot_reach_docs(tmp_path):
    src = """
OUT = ""
function _init()
  local a, b = files.load("note")
  local f, err = io.open("../docs/note.md")
  OUT = tostring(a) .. "|" .. tostring(b) .. "|" .. tostring(f) .. "|" .. err
end
function _draw() cls(0) end
"""
    _ws, lua = _run(tmp_path, src, ["graphics", "files:music"])
    assert lua.get_global("OUT") == (
        "nil|nil|nil|../docs/note.md: Permission denied")
