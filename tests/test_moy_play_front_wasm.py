"""A compiled run in the kernel loop's front with the VM up (native/moy_play's
moy_play.h): where the board's compositor shows a compiled cart's frames
from the cart's memory (`moy_front_ops_t.frame`), the front takes the run,
and each front frame hands the frame the cart blitted to the board straight
from its memory -- the canvas is never written -- with the board's fence
before every hook. What each check watches for:

  * the Player takes the front for a compiled run only where the board
    shows frames: without a `frame` op the console's frame keeps it;
  * a front frame shows the cart's blit565 frame through `frame`, byte for
    byte, and the canvas holds none of it;
  * every way out leaves the console a canvas that holds the last frame
    shown, behind a fence: Super (the pause card, Resume takes the front
    again), the dev channel's `end`, quit(), and a trap, whose run the
    console's next frame reports;
  * a palette frame, which the stand-in (like a P4) does not show, is written
    into the canvas and presented as a Lua run's is.

A buffer stands in for the board (runtime/host/moy_play.py's `front_ops`);
the run is the real host Player's over the real WAMR binding.
"""

import ctypes
import os

import pytest

from tools import wasm_cart
from ws_helpers import build_ws, open_cart

mp = pytest.importorskip("runtime.moy_play")

W, H = 320, 240

# Each _draw fills the frame at 65536 with the word t * 0x0101 + y and hands
# it over with blit565; _update counts t and, from `trap_at` on (a global the
# variant sets), traps, or quits at `quit_at`. A palette variant blits the
# frame's low bytes through an all-white palette instead.
_WAT = """
(module
  (import "moy" "blit565" (func $blit565 (param i32)))
  (import "moy" "blit" (func $blit (param i32 i32)))
  (import "moy" "quit" (func $quit))
  (memory (export "memory") 5 5)
  %(data)s
  (global $t (mut i32) (i32.const 0))
  (func (export "_init"))
  (func (export "_update") (param $dt f32)
    (global.set $t (i32.add (global.get $t) (i32.const 1)))
    (if (i32.eq (global.get $t) (i32.const %(trap_at)d)) (then unreachable))
    (if (i32.eq (global.get $t) (i32.const %(quit_at)d)) (then (call $quit))))
  (func (export "_draw") (local $p i32) (local $y i32) (local $x i32) (local $v i32)
    (local.set $p (i32.const 65536))
    (local.set $y (i32.const 0))
    (block $rows (loop $row
      (br_if $rows (i32.ge_u (local.get $y) (i32.const 240)))
      (local.set $v (i32.add (i32.mul (global.get $t) (i32.const 257)) (local.get $y)))
      (local.set $x (i32.const 0))
      (block $cols (loop $col
        (br_if $cols (i32.ge_u (local.get $x) (i32.const 320)))
        (i32.store16 (local.get $p) (local.get $v))
        (local.set $p (i32.add (local.get $p) (i32.const 2)))
        (local.set $x (i32.add (local.get $x) (i32.const 1)))
        (br $col)))
      (local.set $y (i32.add (local.get $y) (i32.const 1)))
      (br $row)))
    %(blit)s))
"""

_BLIT565 = "(call $blit565 (i32.const 65536))"
_BLIT_PAL = "(call $blit (i32.const 65536) (i32.const 8192))"


def _cart(root, title, trap_at=-1, quit_at=-1, palette=False):
    src = root / ("src_" + title.replace(" ", "_"))
    (src / "src").mkdir(parents=True)
    (src / "src" / "main.wat").write_text(_WAT % {
        "trap_at": trap_at, "quit_at": quit_at,
        "blit": _BLIT_PAL if palette else _BLIT565,
        "data": '(data (i32.const 8192) "%s")' % ("\\ff" * 768) if palette else ""})
    (src / "manifest.json").write_text(
        '{"format": "moy-1", "title": "%s", "runtime": "wasm", "main": "main.wasm",'
        ' "memory": 5, "fps": "free"}' % title)
    dst = root / "carts" / (title.lower().replace(" ", "_") + ".moy")
    wasm_cart.build(str(src), str(dst))


def _expected(t):
    """The bytes of the frame _draw makes on tick `t`."""
    out = bytearray(2 * W * H)
    for y in range(H):
        v = (t * 257 + y) & 0xFFFF
        out[2 * W * y:2 * W * (y + 1)] = bytes((v & 0xFF, v >> 8)) * W
    return bytes(out)


def _swapped(b):
    out = bytearray(len(b))
    out[0::2] = b[1::2]
    out[1::2] = b[0::2]
    return bytes(out)


@pytest.fixture
def front(tmp_path):
    try:
        mp._play()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    from runtime import wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm binding")
    (tmp_path / "carts").mkdir()
    _cart(tmp_path, "Front Wasm")
    _cart(tmp_path, "Front Trap", trap_at=4)
    _cart(tmp_path, "Front Quit", quit_at=4)
    _cart(tmp_path, "Front Palette", palette=True)
    ws = build_ws(tmp_path)
    buf = (ctypes.c_uint16 * (W * H))()
    presents, frames = [], []
    ws.front_bind = lambda: mp.front_ops(buf, W, H, presents, frames)
    try:
        yield ws, buf, presents, frames
    finally:
        if mp.front_live():
            mp.front_end(mp.END_MENU)
        mp.front_ops(None, 0, 0)


def _start(ws, title):
    open_cart(ws, title)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    assert ws.run.player._front is True, "the Player did not take the front"
    mp.wasm_take(1)                   # blit565's frames, as a P4's glue asks


def test_a_compiled_run_shows_its_frames_from_its_memory(front):
    ws, buf, presents, frames = front
    _start(ws, "Front Wasm")
    del frames[:]
    for i in range(1, 6):
        assert mp.front_frame(1000 + 33 * i, 33333) == 1
    shown = [f for f in frames if f is not None]
    assert len(shown) == 5 and presents == [], "the frame op presents, not the canvas's"
    assert frames[0] is None, "no fence before the cart's hook"
    assert all(frames[2 * k] is None for k in range(5)), frames[:4]
    # Each frame shown is the cart's blit, byte for byte; the canvas has none.
    ticks = [next(t for t in range(64) if _expected(t) == f) for f in shown]
    assert ticks == list(range(ticks[0], ticks[0] + 5)), ticks
    assert not any(buf), "a blit wrote the canvas"


def test_super_leaves_the_last_frame_in_the_canvas_and_resume_takes_the_front(front):
    from runtime.moy_input import InputTable
    ws, buf, presents, frames = front
    ws.input = InputTable()
    _start(ws, "Front Wasm")
    inp = mp._BOUND[0]
    assert mp.front_frame(7000, 33333) == 1
    last = [f for f in frames if f is not None][-1]
    del frames[:]
    src = inp.source("kbd")
    src.set_button("start", True)
    inp.begin_frame()
    assert mp.front_frame(7033, 33333) == -1, "the press gives the frame back"
    assert frames and frames[0] is None, "handed back with no fence"
    # The canvas holds it in its wire words (byte-swapped on a swapped wire).
    assert bytes(buf) in (last, _swapped(last)), "the canvas lacks the frame the glass showed"
    assert not mp.front_live() and mp.info()[6] is False
    ws.handle_input()
    src.set_button("start", False)
    inp.begin_frame()
    ws.handle_input()
    assert ws.pause.open and ws.run.player.paused
    ws.frame(1 / 30)
    ws.pause.close()
    assert ws.run.player._front is True and mp.front_live(), "Resume gives the front back"


def test_the_dev_channels_end_fences_and_ends_the_run(front):
    ws, buf, presents, frames = front
    _start(ws, "Front Wasm")
    assert mp.front_frame(100, 33333) == 1
    del frames[:]
    assert mp.front_end(mp.END_MENU) is True
    assert frames == [None], "the run ended with the board still reading it"
    info = mp.info()
    assert info[6] is True and info[10] == mp.END_MENU, info
    ws.frame(1 / 30)
    assert ws.screen == "launcher", ws.screen


def test_quit_ends_the_run_and_the_console_takes_its_route(front):
    ws, buf, presents, frames = front
    _start(ws, "Front Quit")
    rc = [mp.front_frame(100 + 33 * i, 33333) for i in range(8)]
    assert -1 in rc, rc
    info = mp.info()
    assert info[6] is True and info[10] == mp.END_QUIT, info
    ws.frame(1 / 30)
    assert ws.screen == "launcher", ws.screen


def test_a_trap_hands_the_run_back_and_the_console_reports_it(front):
    ws, buf, presents, frames = front
    _start(ws, "Front Trap")
    rc = [mp.front_frame(100 + 33 * i, 33333) for i in range(8)]
    assert -1 in rc, rc
    assert not mp.front_live()
    ws.frame(1 / 30)
    assert ws.run.player._front is False
    assert ws.run.player.cart_error is not None, "the console did not report the trap"


def test_a_palette_frame_the_board_does_not_show_goes_through_the_canvas(front):
    ws, buf, presents, frames = front
    _start(ws, "Front Palette")
    mp.wasm_take(3)                   # both layouts, as an S3's glue asks
    for i in range(1, 4):
        assert mp.front_frame(500 + 33 * i, 33333) == 1
    assert presents == [True] * 3, "the canvas was not presented"
    assert not [f for f in frames if f is not None], "the stand-in showed a palette frame"
    assert any(buf), "the palette frame was not written into the canvas"


def test_a_board_without_a_frame_op_keeps_a_compiled_run_with_the_console(front):
    ws, buf, presents, frames = front
    ws.front_bind = lambda: mp.front_ops(buf, W, H, presents)
    open_cart(ws, "Front Wasm")
    assert ws.run.player.cart_error is None
    assert ws.run.player._front is False and not mp.front_live()


def test_a_game_full_screen_on_the_desk_takes_the_front_once_the_glass_is_still(tmp_path):
    """On the desk a game full screen at the top of the windows -- a window
    open behind it -- takes the front once the console has drawn the glass
    around it into every buffer (`play_viewport`), and a window that is not
    full screen never does."""
    try:
        mp._play()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    from runtime import wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm binding")
    from ws_helpers import build_desktop_ws, make_drv
    (tmp_path / "carts").mkdir()
    _cart(tmp_path, "Front Wasm")
    ws = build_desktop_ws(tmp_path)
    drv = make_drv(ws)
    buf = (ctypes.c_uint16 * (W * H))()
    frames = []
    ws.front_bind = lambda: (ws.wm.play_viewport() is not None
                             and mp.front_ops(buf, W, H, [], frames))
    try:
        drv.frame(1 / 30)
        ws.open_settings()                # a window open behind the game
        for _ in range(3):
            drv.frame(1 / 30)
        assert "settings" in ws.wm._order
        open_cart(ws, "Front Wasm")
        wm = ws.wm
        assert ws.run.player._front is False, "taken before the glass around it was drawn"
        n = 0
        while not ws.run.player._front and n < 20:
            drv.frame(1 / 30)
            n += 1
            assert wm.mode == "desktop" and wm.game_full()
        assert ws.run.player._front and mp.front_live(), "the desk's full-screen game kept the console"
        assert n > wm._retained_n(), n
        mp.front_end(mp.END_MENU)
        drv.frame(1 / 30)
        # A window that is not full screen: the console keeps it.
        open_cart(ws, "Front Wasm")
        drv.frame(1 / 30)
        win = wm._wins["desktop"]
        wm._toggle_max(win)
        for _ in range(12):
            drv.frame(1 / 30)
        assert win.saved is None and not wm.game_full()
        assert ws.run.player._front is False and not mp.front_live()
    finally:
        if mp.front_live():
            mp.front_end(mp.END_MENU)
        mp.front_ops(None, 0, 0)
