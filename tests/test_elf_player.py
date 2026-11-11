"""A native game in the Player (native/moy_elf/moy_os.h, A GAME;
runtime/run/elf_run.py): tests/fixtures/elf/elf_game.moy, built for the host
by the SDK (tools/elf_cart.py), opened from the home. It draws its screen
with libmoy's raster linked into the module and with a pixel it writes
itself, and the Player shows what it presents, feeds it the buttons, pauses
it, ends it on its quit and on its stop, and shows the stop card for a trap.
"""

import json
import os
import shutil
import subprocess

import pytest

from tests.test_os_table import MACHINE, _table, check  # noqa: F401 -- the fixture
from tests.test_sdk_libm import _applied, _readelf
from ws_helpers import open_cart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "elf", "elf_game.moy")
DT = 1 / 30


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from tools import elf_cart
    dst = str(tmp_path_factory.mktemp("elfgame") / "elf_game.moy")
    elf_cart.build(FIXTURE, dst, ["host"], signed=False)
    return dst


@pytest.fixture
def ws(tmp_path, built):
    from runtime.host_app import build_workstation
    from runtime.moy_input import InputTable
    carts = tmp_path / "carts"
    carts.mkdir()
    shutil.copytree(built, str(carts / "elf_game.moy"))
    ws = build_workstation(str(carts))
    if "elf" not in ws.runtimes:
        pytest.skip("no host native tier")
    ws.input = InputTable()
    yield ws
    lua = ws.run.player._lua
    if lua is not None:
        lua.close()


def _frames(ws, n=1, press=None):
    src = ws.input.source("kbd")
    for i in range(n):
        if press is not None:
            src.set_button(press, i == 0)
        ws.input.begin_frame()
        ws.frame(DT)


def _square(ws):
    """Where the game's 8x8 square of 8 is on the run's canvas: its top-left."""
    cv = ws.run.canvas
    for y in range(cv.h):
        for x in range(cv.w):
            if cv.pix(x, y) == 8:
                return x, y
    return None


def _start(ws):
    open_cart(ws, "Game Native")
    p = ws.run.player
    assert p.cart_error is None, p.cart_error
    _frames(ws, 3)
    return p


def test_a_native_game_plays_its_frames_in_the_player(ws):
    p = _start(ws)
    from runtime.elf_run import ElfGame
    assert isinstance(p._lua, ElfGame), "the Player did not open the native run"
    cv = ws.run.canvas
    assert _square(ws) == (16, 16), "libmoy's rectfill is not on the canvas"
    assert cv.pix(0, 0) == 1, "the screen libmoy cleared is not on the canvas"
    assert cv.pix(cv.w - 1, cv.h - 1) == 11, "the pixel the game wrote itself is not there"


def test_it_takes_its_buttons(ws):
    _start(ws)
    src = ws.input.source("kbd")
    src.set_button("right", True)
    for _ in range(4):
        ws.input.begin_frame()
        ws.frame(DT)
    src.set_button("right", False)
    _frames(ws, 3)
    x, y = _square(ws)
    assert y == 16 and x >= 16 + 4 * 2, (x, y)


def test_a_pause_holds_it_and_resume_goes_on(ws):
    p = _start(ws)
    pid = p._lua.pid
    from runtime import elf_binding
    ws.pause.show()
    assert ws.pause.open and p.paused
    _frames(ws, 1)
    before = elf_binding.info(pid)["events"]
    _frames(ws, 4)
    assert elf_binding.info(pid)["events"] == before, "a paused game was sent frames"
    ws.pause.close()
    src = ws.input.source("kbd")
    src.set_button("down", True)
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(DT)
    src.set_button("down", False)
    _frames(ws, 2)
    assert _square(ws)[1] > 16, "the resumed game did not move"


def test_its_quit_ends_the_run(ws):
    p = _start(ws)
    pid = p._lua.pid
    _frames(ws, 3, press="b")
    _frames(ws, 2)
    assert p.cart_error is None, p.cart_error
    assert ws.screen == "launcher", ws.screen
    from runtime import elf_binding
    assert elf_binding.info(pid) is None, "the game's process outlived its run"


def test_home_stops_the_game(ws):
    p = _start(ws)
    pid = p._lua.pid
    from runtime import elf_binding
    assert elf_binding.info(pid) is not None
    ws.pause.show()
    ws.pause.choose_row("Quit")
    _frames(ws, 2)
    assert elf_binding.info(pid) is None, "the game's process outlived its run"


def test_a_trap_shows_the_stop_card(ws):
    p = _start(ws)
    pid = p._lua.pid
    _frames(ws, 3, press="a")
    _frames(ws, 2)
    assert p.cart_error is not None and "SIGSEGV" in p.cart_error, p.cart_error
    card = ws.pause
    assert card.open and card.stopped
    assert card.rows == (card.RESTART, card.HOME), "a native cart has no code to see"
    from runtime import elf_binding
    assert elf_binding.info(pid) is None
    card.choose_row(card.RESTART)
    _frames(ws, 3)
    assert p.cart_error is None and _square(ws) == (16, 16), "Restart did not play it again"


def test_the_stop_text_names_the_signal_and_the_fault():
    from runtime.elf_run import stop_text
    assert stop_text(139) == "native code stopped: SIGSEGV, a memory fault"
    assert stop_text(3) == "native code ended with status 3"
    got = stop_text(132, (7, "elf.game", 2, 0x1a4, 0))
    assert got.startswith("native code stopped: SIGILL") and "0x1a4" in got


def test_a_game_too_big_for_the_console_gets_the_fit_notice(ws, monkeypatch):
    rt = ws.runtimes["elf"]
    monkeypatch.setattr(rt, "memory", lambda: (1024, 1024))
    open_cart(ws, "Game Native")
    p = ws.run.player
    assert p.notice is not None and p._lua is None, p.cart_error


def test_a_command_has_no_screen_to_present_to(tmp_path):
    """moy_screen and moy_present answer ENODEV outside a game."""
    from runtime import elf_binding
    from tools import elf_cart
    if not elf_binding.available():
        pytest.skip("no host native tier")
    src = tmp_path / "noscreen.c"
    src.write_text('#include <errno.h>\n#include "moybyte/game.h"\n'
                   "int main(void) { int w, h; char b[4] = {0};\n"
                   "  if (moy_screen(&w, &h) != -1 || errno != ENODEV) return 1;\n"
                   "  if (moy_present(b, 4) != -1 || errno != ENODEV) return 2;\n"
                   "  return 0; }\n")
    mod = elf_cart.build_module([str(src)], "host", str(tmp_path / "noscreen.elf"), signed=False)
    err = elf_binding.service(mod, 9301, "noscreen", elf_binding.UNSIGNED)
    assert err is None, err
    import time
    end = time.time() + 5
    got = []
    while not got and time.time() < end:
        got = [c for p, c in elf_binding.reap() if p == 9301]
        time.sleep(0.01)
    assert got == [0], got


@pytest.mark.parametrize("chip", ["esp32s3", "esp32p4"])
def test_the_game_links_libmoy_for_each_chip_under_the_loader_s_rules(tmp_path, chip, check):
    """The fixture game links for a board's chip with libmoy's raster in it:
    a passing verdict, no import, only the relocations the chip's loader
    applies, and libmoy brings no writable data."""
    import re
    from tools import elf_cart
    if not elf_cart.available(chip):
        pytest.skip("no %s toolchain" % chip)
    dst = str(tmp_path / "g.moy")
    elf_cart.build(FIXTURE, dst, [chip], signed=False)
    mod = os.path.join(dst, elf_cart.module_name(chip))
    why, _info = check(open(mod, "rb").read(), MACHINE[chip], len(_table()["rows"]))
    assert why is None, why
    readelf = _readelf(chip)
    syms = subprocess.run([readelf, "--dyn-syms", "-W", mod], capture_output=True,
                          text=True, check=True).stdout
    assert [ln for ln in syms.splitlines() if re.search(r"\sUND\s+\S+$", ln)] == []
    rel = subprocess.run([readelf, "-r", "-W", mod], capture_output=True, text=True,
                         check=True).stdout
    types = set(re.findall(r"\s(R_[A-Z0-9_]+)\s", rel))
    assert types <= _applied(chip), types - _applied(chip)
    size = elf_cart._tool(chip, "size")
    out = subprocess.run([size, elf_cart.libmoyos(chip)], capture_output=True, text=True,
                         check=True).stdout
    raster = [ln for ln in out.splitlines() if "moy_moy_" in ln]
    assert len(raster) == len(elf_cart.LIBMOY_SOURCES), out
    for line in raster:
        _text, data, bss = (int(v) for v in line.split()[:3])
        assert data == 0 and bss == 0, line
    with open(os.path.join(dst, "manifest.json")) as f:
        assert json.load(f)["moybyte"]["native"] == [chip]

ALLOC_SCRIPT = r'''
import gc
import sys
sys.path.insert(0, @TREE@)
import elf_run


class Engine:
    CHIP = "host"

    def __init__(self):
        self.px = bytearray(32)
        self.n = 0

    def game(self, *args):
        return None

    def screen(self, pid):
        return memoryview(self.px)

    def frame(self, pid, held, pressed, ms, wait, words=None):
        self.n += 1
        return 3 if self.n & 1 else 0

    def game_stop(self, pid):
        return 0


class Input:
    cart_quit = False

    def button_masks(self, order, player, out):
        out[0] = 5
        out[1] = 1
        return out


class Canvas:
    w = 8
    h = 4

    def blit_indices(self, px, w, h, x, y):
        pass


class Player:
    pid = 7


class Run:
    canvas = Canvas()
    player = Player()


class WS:
    input = Input()
    run = Run()


class Runtime:
    engine = Engine()

    def module(self, cart):
        return "main.host.elf"

    def _flags(self, cart, net):
        return 0


g = elf_run.ElfGame(Runtime(), WS(), {"id": "elf.game"}, None)
upd, drw = g.update, g.draw
dt = 1 / 30
for _ in range(20):
    upd(dt)
    drw()
gc.collect()
gc.disable()
i = 0
a0 = gc.mem_alloc()
while i < 200:
    upd(dt)
    drw()
    i += 1
a1 = gc.mem_alloc()
gc.enable()
print("ALLOC", a1 - a0)
'''


def test_a_native_game_s_frame_allocates_nothing(tmp_path):
    """The Player's half of a native game's frame -- the buttons, the frame
    call, the blit of what it presented -- allocates nothing on a board's
    model of MicroPython (.claude/rules/shell.md, a play frame allocates
    NOTHING)."""
    from unix_mp import find_unix_mp, require_unix_mp, runtime_tree
    exe = find_unix_mp(board_model=True) or require_unix_mp(why="the native game's frame")
    script = tmp_path / "alloc.py"
    script.write_text(ALLOC_SCRIPT.replace("@TREE@", repr(runtime_tree())))
    done = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ALLOC 0\n" in done.stdout, done.stdout
