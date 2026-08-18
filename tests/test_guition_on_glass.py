"""On-glass Guition JC3248W535 console tests (#202): drive the real board.

Gated: they run only when MOYBYTE_GUITION_PORT is set (e.g.
`MOYBYTE_GUITION_PORT=/dev/ttyACM1 .venv/bin/python -m pytest
tests/test_guition_on_glass.py -v`) -- the T-Deck suite's shape pointed at the
third board, per the port checklist's stage-6 exit criterion
(docs/board_ports_2026-08.md).

DELIBERATELY NO RESET, the T-Deck's reason verbatim: this board's
USB-Serial/JTAG is ON the SoC, so a reset tears the USB device down under the
open handle and the reader looks exactly like a dead board. The suite ATTACHES
to the running desktop, asserts, and leaves the console on the launcher.

Tests share the session in file order.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PORT = os.environ.get("MOYBYTE_GUITION_PORT")

pytestmark = pytest.mark.skipif(
    not PORT,
    reason="MOYBYTE_GUITION_PORT not set (needs the Guition S3 on serial)")


@pytest.fixture(scope="module")
def board():
    # The shared line-pump driver; dtr/rts HIGH on open (USB-Serial/JTAG board
    # -- an open with both LOW chip-resets it). reset() is never called.
    from p4_autotest import P4Board
    b = P4Board(PORT, dtr=True, rts=True)
    b.drain(0.8)
    line = b.cmd("state", wait_for="STATE ", timeout=10.0)
    if line is None:
        raise RuntimeError(
            "the Guition did not answer `state` on %s -- is the desktop "
            "running? (this suite attaches, it does not reset)" % PORT)
    yield b
    b.close()


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    st = board.state()
    assert isinstance(st.get("frames"), int) and st["frames"] > 0
    assert st.get("stack"), st
    assert st["stack"][-1] == st["screen"]
    asleep, secs = st["psave"]
    assert asleep is False
    assert secs > 0


def test_the_system_canvas_is_the_landscape_glass(board):
    """The board's one structural novelty (#202): the first FULLSCREEN-tier
    console whose system canvas (480x320 landscape, rotated in moy_axs's band
    copy) is not its game canvas (320x240). Assert both sizes and the
    viewport seam through the live console."""
    line = board.cmd("py (ws.sys_canvas.w, ws.sys_canvas.h, ws.canvas.w, ws.canvas.h)",
                     wait_for="PY ")
    assert line == "PY (480, 320, 320, 240)", line
    # composite_game's placement: 1:1, centred both ways.
    line = board.cmd("py ws.wm.viewport()", wait_for="PY ")
    assert line == "PY (80, 40, 1)", line


def test_every_system_app_claims_exactly_one_cart(board):
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered?"
    wrong = {k: v for k, v in claims.items() if v != 1}
    assert not wrong, "app cart claims off (seed/title drift?): %r" % wrong


def test_py_probe_reaches_the_live_console(board):
    line = board.cmd("py ws._frames_drawn", wait_for="PY ")
    assert line is not None and line.startswith("PY "), line
    assert int(line.split("PY ", 1)[1]) > 0
    line = board.cmd("py boot.done", wait_for="PY ")
    assert line == "PY True", line


def test_diag_toggle_roundtrips(board):
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True
    board.cmd("diag 0", wait_for="REMOTE diag off")
    assert board.state()["diag"] is False


def test_swipe_rides_the_real_pointer_feed(board):
    """A horizontal fling over the home shelf, through the same pointer the
    AXS15231 feeds -- and the console is still on home afterwards."""
    frames0 = board.state()["frames"]
    board.swipe(400, 160, 80, 160, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert st["frames"] > frames0, "the gesture drew no frames"
    board.swipe(80, 160, 400, 160, frames=20)
    board.drain(0.8)


def test_a_cart_runs_and_exits(board):
    """The _GATE_SEQ blind-spot closer, on this board's glass from day one:
    launch through the real launcher path, assert the cart is ticking, exit
    through the cart-quit flag."""
    line = board.cmd("run star", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.0)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "quit did not pop to the caller: %r" % st
    assert st["stack"][-1] == "launcher"


def test_a_lua_cart_runs_and_exits(board):
    """moycore on the third board: the Lua tier is supposed to reach every
    board by default (the whole point of the shared native staging), so pin it
    with a real run, not just an import."""
    line = board.cmd("run sakura lua", wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.0)
    st = board.state()
    assert st.get("cart") == "Sakura Lua", st.get("cart")
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the Lua cart is not ticking"
    board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "quit did not pop to the caller: %r" % st
    assert st["stack"][-1] == "launcher"


def test_idle_screen_blank_and_wake(board):
    board.cmd("power 3", wait_for="REMOTE power")
    board.drain(0.3)
    board.drain(6.0)                       # say nothing; let the timer expire
    assert board.state()["psave"][0] is True, "the panel never blanked"
    board.drain(0.5)                       # the state query was activity
    assert board.state()["psave"][0] is False, "input did not wake the panel"

    board.cmd("power off", wait_for="REMOTE power")
    board.drain(1.0)
    assert board.state()["psave"][0] is True, "`power off` did not blank"

    board.cmd("power 300", wait_for="REMOTE power")   # restore the default
    board.drain(0.5)
    assert board.state()["psave"][0] is False
    assert board.state()["psave"][1] == 300


def test_mem_reports_the_heap(board):
    line = board.cmd("mem", wait_for="REMOTE mem")
    assert line is not None and "live=" in line and "free=" in line, line
