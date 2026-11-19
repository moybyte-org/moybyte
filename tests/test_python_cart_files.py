"""A Python cart's files (moy-spec SPEC.md 9.1): `read`, `write`, `erase` and
`list_files` (Python keeps its own `list`) over moycore's CartFiles, the store
a Lua and a compiled cart's are kept in (native/moycore/moycore_files.h over
native/moy_store/moy_files.h), on the host by ctypes as modmoycore.c's on a VM.
"""

import json
import os

import pytest

from runtime import cart_files
from ws_helpers import build_ws

_CART = '''
import struct


def _init():
    run = read("saves/run")
    if run == b"":
        assert read("readme.txt") == b"not yours"
        assert read("readme.txt", 4, 2) == b"yo"
        assert read("../readme.txt") == b"" and read("missing") == b""
        assert write("readme.txt", "x") == -1
        assert write("saves/" + "b" * 59, "x") == -1
        assert write("saves/big", bytes(1048577)) == -2
        assert write("saves/slot1", struct.pack("<ii", 7, -300)) == 0
        assert struct.unpack("<ii", read("saves/slot1")) == (7, -300)
        assert write("saves/Case", "C") == 0 and write("saves/case", "c") == 0
        assert write("options.cfg", "volume=9") == 0 and read("options.cfg") == b"volume=9"
        assert write("saves/tmp", "t") == 0
        assert erase("saves/tmp") == 0 and erase("saves/tmp") == -1
        assert list_files("saves/") == ["saves/Case", "saves/case", "saves/slot1"]
        assert "main.py" in list_files() and list(range(2)) == [0, 1]
        assert write("saves/run", "1") == 0
        pmem(0, 1)
    else:
        assert run == b"1"
        assert struct.unpack("<ii", read("saves/slot1")) == (7, -300)
        assert erase("options.cfg") == 0 and read("options.cfg") == b"volume=5"
        pmem(0, 2)
'''


def _cart(tmp_path, ws):
    from runtime import moy_carts
    cart = moy_carts.create("Py Files", str(tmp_path / "carts"), src=_CART, type="game")
    for name, text in (("readme.txt", "not yours"), ("options.cfg", "volume=5")):
        with open(os.path.join(cart["path"], name), "w") as f:
            f.write(text)
    man = os.path.join(cart["path"], "manifest.json")
    with open(man) as f:
        m = json.load(f)
    m["writable"] = cart["writable"] = ["saves/", "options.cfg"]
    with open(man, "w") as f:
        json.dump(m, f)
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    return cart


def _play(ws):
    ws.open()
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    ws.input.begin_frame()
    ws.frame(1 / 30)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    pm = ws.run.project.pmem
    ws.spine.exit_to_caller()
    return list(pm.cells)


def test_a_python_cart_writes_reads_lists_and_erases_its_files_across_a_restart(tmp_path):
    _binding_or_skip()
    ws = build_ws(tmp_path)
    _cart(tmp_path, ws)
    assert _play(ws)[0] == 1
    assert _play(ws)[0] == 2


def _binding_or_skip():
    try:
        import moycore
    except ImportError as exc:
        pytest.skip("no host moycore: %s" % exc)
    return moycore


def test_a_python_carts_store_finishes_what_a_power_loss_left(tmp_path):
    moycore = _binding_or_skip()
    cart = tmp_path / "carts" / "cut.moy"
    cart.mkdir(parents=True)
    keep = cart_files.folder_of(str(cart))
    os.makedirs(keep)
    k = cart_files.key(b"saves/a")
    with open(os.path.join(keep, k + "~done"), "wb") as f:
        f.write(b"whole")
    with open(os.path.join(keep, cart_files.key(b"saves/b") + "~part"), "wb") as f:
        f.write(b"torn")
    files = moycore.CartFiles(str(cart), "saves/")
    try:
        assert files.read("saves/a", 0, -1) == b"whole"
        assert sorted(os.listdir(keep)) == [k]
        assert files.list("saves/") == ["saves/a"]
    finally:
        files.close()


_BOARD = r'''
import moycore
f = moycore.CartFiles(PATH, "saves/\0options.cfg")
print(f.write("saves/a", b"one"), f.write("x", "y"), f.write("saves/big", bytes(1048577)))
print(f.read("readme.txt", 4, 2), f.read("saves/a", 0, -1), f.read("../readme.txt", 0, -1))
f.close()
f = moycore.CartFiles(PATH, "saves/\0options.cfg")
print(f.list("saves/"), f.erase("saves/a"), f.erase("saves/a"), f.list("saves/"))
f.close()
'''


def test_the_boards_binding_keeps_a_python_carts_files(tmp_path):
    """modmoycore.c's CartFiles on the desktop MicroPython: the same C the
    host reaches by ctypes, on a VM."""
    import subprocess

    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moycore", why="Without it nothing runs a Python cart's files through the boards' binding.")
    cart = tmp_path / "board.moy"
    cart.mkdir()
    (cart / "readme.txt").write_text("not yours")
    p = subprocess.run([exe, "-c", "PATH = %r\n" % str(cart) + _BOARD],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.splitlines() == ["0 -1 -2", "b'yo' b'one' b''",
                                     "['saves/a'] 0 -1 []"], p.stdout
