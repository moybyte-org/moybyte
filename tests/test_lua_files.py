"""A Lua cart's files (moy-spec SPEC.md 9.1) on the host: libmoy's `read`,
`write`, `erase` and `list` over the store a compiled cart's are kept in
(native/moycore/moycore_files.h, native/moy_store/moy_files.h), held to the
manifest's "writable" and the 1 MiB cap, and kept across a restart of the
run.

The cart asserts every answer, so a wrong one is the run's error, named.
"""

import json
import os

import pytest

from ws_helpers import build_ws

_CART = r'''
local function same(got, want)
  assert(#got == #want, "listed " .. table.concat(got, ","))
  for i = 1, #want do assert(got[i] == want[i], "listed " .. tostring(got[i])) end
end

function _init()
  local run = read("saves/run")
  if run == "" then
    assert(read("readme.txt") == "not yours")
    assert(read("readme.txt", 4, 2) == "yo")
    assert(read("../readme.txt") == "" and read("missing") == "")
    assert(write("readme.txt", "x") == -1, "an undeclared path is refused")
    assert(write("saves/" .. string.rep("b", 59), "x") == -1, "65 bytes")
    assert(write("saves/big", string.rep("x", 1048577)) == -2, "the quota")
    assert(read("saves/big") == "")
    assert(write("saves/slot1", string.pack("<i4i4", 7, -300)) == 0)
    local a, b = string.unpack("<i4i4", read("saves/slot1"))
    assert(a == 7 and b == -300)
    assert(write("saves/Case", "C") == 0 and write("saves/case", "c") == 0)
    assert(write("saves/bin", "a\0b\255") == 0 and read("saves/bin") == "a\0b\255")
    assert(write("options.cfg", "volume=9") == 0 and read("options.cfg") == "volume=9")
    assert(write("saves/tmp", "t") == 0)
    assert(erase("saves/tmp") == 0 and erase("saves/tmp") == -1)
    assert(erase("readme.txt") == -1)
    same(list("saves/"), {"saves/Case", "saves/bin", "saves/case", "saves/slot1"})
    local seen = {}
    for _, p in ipairs(list()) do seen[p] = true end
    assert(seen["main.lua"] and seen["readme.txt"] and seen["saves/slot1"])
    assert(write("saves/run", "1") == 0)
    pmem(0, 1)
  else
    assert(run == "1", "a third run")
    local a, b = string.unpack("<i4i4", read("saves/slot1"))
    assert(a == 7 and b == -300, "the first run's save is kept")
    assert(read("options.cfg") == "volume=9")
    assert(erase("options.cfg") == 0 and read("options.cfg") == "volume=5",
           "the shipped file answers again")
    same(list("saves/"), {"saves/Case", "saves/bin", "saves/case", "saves/run",
                          "saves/slot1"})
    pmem(0, 2)
  end
end
'''


def _lua_or_skip():
    from runtime import lua_binding
    if not lua_binding.HostLuaRun.available():
        pytest.skip("host lua binding not built (needs a C compiler)")


def _cart(tmp_path, ws, writable):
    from runtime import moy_carts
    cart = moy_carts.create("Lua Files", str(tmp_path / "carts"), src=_CART,
                            runtime="lua", main="main.lua", type="game")
    with open(os.path.join(cart["path"], "readme.txt"), "w") as f:
        f.write("not yours")
    with open(os.path.join(cart["path"], "options.cfg"), "w") as f:
        f.write("volume=5")
    man = os.path.join(cart["path"], "manifest.json")
    with open(man) as f:
        m = json.load(f)
    m["writable"] = writable
    with open(man, "w") as f:
        json.dump(m, f)
    cart["writable"] = writable
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    return cart


def _play(ws):
    ws.open()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    pm = ws.run.project.pmem
    ws.spine.exit_to_caller()
    return list(pm.cells)


def test_a_lua_cart_writes_reads_lists_and_erases_its_files_across_a_restart(tmp_path):
    _lua_or_skip()
    ws = build_ws(tmp_path)
    cart = _cart(tmp_path, ws, ["saves/", "options.cfg"])
    assert _play(ws)[0] == 1, "the first run's checks"
    assert _play(ws)[0] == 2, "the second run, on what the first left"
    from runtime import cart_files
    kept = os.listdir(cart_files.folder_of(cart["path"]))
    assert "saves%2fslot1" in kept and "options.cfg" not in kept, kept


def test_a_lua_cart_with_no_writable_writes_nothing(tmp_path):
    _lua_or_skip()
    ws = build_ws(tmp_path)
    from runtime import moy_carts
    cart = moy_carts.create("Lua No Files", str(tmp_path / "carts"), runtime="lua",
                            main="main.lua", type="game", src=(
                                'function _init()\n'
                                '  pmem(0, write("saves/a", "x"))\n'
                                '  pmem(1, #read("main.lua"))\n'
                                'end\n'))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    cells = _play(ws)
    assert cells[0] == -1, "nothing declared: -1"
    assert cells[1] == os.path.getsize(os.path.join(cart["path"], "main.lua"))


_BOARD = r'''
import moycore
from array import array

def run(path, src):
    fb = bytearray(16 * 16 * 2)
    snap = array("i", bytearray(4 * moycore.SNAP_LEN))
    aq = array("i", bytearray(4 * (1 + moycore.AQ_SLOTS * moycore.AQ_MAX)))
    moycore.run_begin(fb, 16, 16, None, None, None, 0, 0, snap, aq, None,
                      None, None, True, None)
    moycore.lua_files(path, "saves/\0options.cfg")
    print("LOAD", moycore.load([(src, "@cart")]))
    print("R", moycore.get_global("R"))
    moycore.close()

run(PATH, 'R = write("saves/a", "one") .. write("x", "y") .. read("readme.txt", 4, 2)')
run(PATH, 'R = read("saves/a") .. table.concat(list("saves/"), ",") .. erase("saves/a")'
          ' .. #list("saves/")')
'''


def test_the_boards_binding_keeps_a_lua_runs_files(tmp_path):
    """The same verbs through the boards' module (native/moycore/modmoycore.c,
    moycore_files.c) on the desktop MicroPython: bound after run_begin, let go
    at close, and found again by the next run."""
    import subprocess

    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moycore", why="Without it nothing runs a Lua run's files through the boards' binding.")
    cart = tmp_path / "board.moy"
    cart.mkdir()
    (cart / "readme.txt").write_text("not yours")
    p = subprocess.run([exe, "-c", "PATH = %r\n" % str(cart) + _BOARD],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.splitlines() == ["LOAD None", "R 0-1yo",
                                     "LOAD None", "R onesaves/a00"], p.stdout
