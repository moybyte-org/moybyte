"""On-glass T-Deck console tests (#201): drive the REAL board over serial.

Gated: they run only when MOYBYTE_TDECK_PORT is set (e.g.
`MOYBYTE_TDECK_PORT=/dev/ttyACM0 .venv/bin/python -m pytest
tests/test_tdeck_on_glass.py -v`), so the normal host suite never needs
hardware. The suite that #201 said was "worth more than the ~0.5MB of flash
the LVGL removal frees" -- the T-Deck's RX works on the mainline port, the
dev channel is the shared `runtime/dev_channel.py`, and this is the P4
suite's pattern pointed at the second board.

DELIBERATELY NO RESET, unlike the P4 suite. The P4's CH343 is an external
USB-UART that survives a chip reset; the T-Deck's USB-Serial/JTAG is ON the
SoC, so a reset tears the USB device down under the open handle and a reader
that reopens too early sees zero bytes and looks exactly like a dead board
(CLAUDE.md's RX section -- three separate "the board is silent" conclusions
in one session were this). So this suite ATTACHES to the running desktop,
asserts, and leaves the console where it found it: on the launcher.

Tests share the session in file order, each leaving the console in the state
the next one expects.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PORT = os.environ.get("MOYBYTE_TDECK_PORT")

pytestmark = pytest.mark.skipif(
    not PORT, reason="MOYBYTE_TDECK_PORT not set (needs the T-Deck on serial)")


@pytest.fixture(scope="module")
def board():
    # The driver is the P4's -- a plain line-pump over pyserial -- pointed at
    # THIS board's [serial] declaration: dtr/rts HIGH at open, attach_only, and
    # a 768-byte chunk. Every one of those is a measurement (its board.toml
    # carries the why), and the same block push_cart.py and the flash targets
    # read. Encoding them here was three copies of one fact.
    from p4_autotest import P4Board
    b = P4Board(PORT, board_dir=ROOT / "firmware" / "lilygo_t_deck_plus_mainline")
    # The board is already running; a first drain absorbs whatever diag lines
    # are mid-flight before the first command's reply is awaited.
    b.drain(0.8)
    line = b.cmd("state", wait_for="STATE ", timeout=10.0)
    if line is None:
        raise RuntimeError(
            "the T-Deck did not answer `state` on %s -- is the desktop "
            "running? (this suite attaches, it does not reset)" % PORT)
    yield b
    b.close()


def test_state_snapshot_has_the_fullscreen_tier_shape(board):
    st = board.state()
    assert isinstance(st.get("frames"), int) and st["frames"] > 0
    # The process back-stack IS this tier's window model, and `ws.screen` is a
    # read-only projection of its top -- assert the documented invariant.
    assert st.get("stack"), st
    assert st["stack"][-1] == st["screen"]
    # psave: [asleep, timeout_secs] -- the idle blank is live and awake.
    asleep, secs = st["psave"]
    assert asleep is False
    assert secs > 0


def test_wifi_status_is_readable(board):
    st = board.state()
    assert "wifi_err" not in st, st.get("wifi_err")
    assert st.get("wifi") is None or isinstance(st["wifi"], list)


def test_every_system_app_claims_exactly_one_cart(board):
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered?"
    wrong = {k: v for k, v in claims.items() if v != 1}
    assert not wrong, "app cart claims off (seed/title drift?): %r" % wrong


def test_py_probe_reaches_the_live_console(board):
    line = board.cmd("py ws._frames_drawn", wait_for="PY ")
    assert line is not None and line.startswith("PY "), line
    assert int(line.split("PY ", 1)[1]) > 0
    # The loop objects are in the py scope (comp/boot/pump), same as the P4.
    line = board.cmd("py boot.done", wait_for="PY ")
    assert line == "PY True", line


def test_diag_toggle_roundtrips(board):
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True
    board.cmd("diag 0", wait_for="REMOTE diag off")
    assert board.state()["diag"] is False


def test_swipe_rides_the_real_pointer_feed(board):
    """A horizontal fling over the home shelf: the gesture machinery (press
    edge, held interpolation, real release) through the same pointer the glass
    feeds -- and the console is still on home afterwards, so the suite's
    leave-it-where-you-found-it contract holds."""
    frames0 = board.state()["frames"]
    board.swipe(260, 140, 60, 140, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert st["frames"] > frames0, "the gesture drew no frames"
    # Swipe back so the shelf rests near where it started.
    board.swipe(60, 140, 260, 140, frames=20)
    board.drain(0.8)                       # let the fling settle


def test_a_cart_runs_and_exits(board):
    """THE test the 2026-08-17 _GATE_SEQ regression bought: a staged-constant
    deletion broke make_spr_gate -- and therefore EVERY cart start -- while
    both boards' suites stayed green, because nothing on glass ever RAN a
    cart. The host pyflakes net caught it one staged build later; this makes
    the glass able to catch its own. Launch through the real launcher path,
    assert the cart is ticking, exit through the cart-quit flag the Player
    honors (the same flag the cart-API quit() verb sets)."""
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


def test_idle_screen_blank_and_wake(board):
    """The idle blank on THIS board (shared IdleBlank + shared power verb):
    blank on silence, wake on the next serial command, `power off` outranks
    its own arrival. The P4 suite's test, ported."""
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
