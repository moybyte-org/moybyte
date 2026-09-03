"""The three on-glass suites' shared body (#206 item 3).

`tests/test_{p4,tdeck,guition}_on_glass.py` each drive a REAL board over
serial, and 82 of the two S3 suites' lines were the same lines: the attach
fixture, the skip-if-no-port gate, and the state / `py` / diag / mem / cart
checks that ask nothing board-specific. The P4's third copy had already
drifted -- the same predicates, worse failure text.

None of it is a test. The suites keep their own `def test_*`, so the collected
ids stay per board and a failure names the board it happened on; these are the
bodies those tests call.

What is deliberately NOT here is every genuine difference, which stays in the
suite that owns it: the P4's windowed-desk tour, its OTA-verifier trio and its
own cart exit path (`ws.exit()`, so the T-Deck keeps the kid-facing `quit()`
flag pinned), the Guition's landscape canvas and sync-RPC tests, each board's
swipe coordinates, and the tail of the idle-blank check -- the P4 pins `power
0`, the S3s pin the restore.
"""

import contextlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))          # p4_autotest.P4Board


def gate(env_var, board):
    """`(port, skip-marker)` for a suite gated on a board being plugged in."""
    port = os.environ.get(env_var)
    return port, pytest.mark.skipif(
        not port, reason="%s not set (needs %s on serial)" % (env_var, board))


@contextlib.contextmanager
def session(port, board_dir):
    """One board, held open for a whole suite.

    RESET OR ATTACH IS THE BOARD'S OWN DECLARATION, not a choice made here.
    `attach_only` in its `[serial]` block says the USB serial sits ON the SoC,
    so a reset pulse re-enumerates the device under this open handle and every
    read afterwards returns nothing, forever -- indistinguishable from a dead
    board. Such a board is attached to a desktop that is ALREADY running and
    left where it was found; `P4Board.reset()` refuses one outright. The same
    block supplies the line state at open, which is the other half of that
    hardware fact: an open with both lines LOW chip-resets a SoC-USB board.
    """
    from p4_autotest import P4Board
    b = P4Board(port, board_dir=board_dir)
    try:
        if b.attach_only:
            # A first drain absorbs whatever diag lines are mid-flight before
            # the first command's reply is awaited.
            b.drain(0.8)
            if b.cmd("state", wait_for="STATE ", timeout=10.0) is None:
                raise RuntimeError(
                    "%s did not answer `state` on %s -- is the desktop "
                    "running? (this suite attaches, it does not reset)"
                    % (b.expect_board or Path(board_dir).name, port))
            # Identity, not just liveness: both S3s share usb id 303a:1001 and
            # answer `state` just as happily, so an answer alone proves only
            # that SOME console is listening. A `PORT=auto` resolves it; an
            # explicit port aimed at the wrong board raises here.
            b.verify_board()
        else:
            b.reset()                  # verifies identity once the desk is up
        yield b
    finally:
        b.close()


# -- the checks every fullscreen-tier board shares ---------------------------


def fullscreen_tier_state(board):
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


def wifi_status_is_readable(board):
    st = board.state()
    assert "wifi_err" not in st, st.get("wifi_err")
    assert st.get("wifi") is None or isinstance(st["wifi"], list)


def every_app_claims_one_cart(board):
    """Every registered system app claims exactly one cart. Naming the wrong
    ones is the point: the failure this catches is seed/title drift in ONE
    app, and a dump of the whole claims table buries it."""
    claims = board.state().get("app_claims") or {}
    assert claims, "no system apps registered?"
    wrong = {k: v for k, v in claims.items() if v != 1}
    assert not wrong, "app cart claims off (seed/title drift?): %r" % wrong


def py_probe_reaches_the_console(board):
    line = board.cmd("py ws._frames_drawn", wait_for="PY ")
    assert line is not None and line.startswith("PY "), line
    assert int(line.split("PY ", 1)[1]) > 0
    # The loop objects are in the py scope (comp/boot/pump).
    line = board.cmd("py boot.done", wait_for="PY ")
    assert line == "PY True", line


def diag_toggle_roundtrips(board):
    board.cmd("diag 1", wait_for="REMOTE diag on")
    assert board.state()["diag"] is True
    board.cmd("diag 0", wait_for="REMOTE diag off")
    assert board.state()["diag"] is False


def home_shelf_fling(board, x0, x1, y):
    """A horizontal fling over the home shelf: the gesture machinery (press
    edge, held interpolation, real release) through the same pointer the glass
    feeds -- and the console is still on home afterwards, so the suite's
    leave-it-where-you-found-it contract holds.

    The coordinates come from the caller because they are the one thing here
    that is not shared: this tier's glass is a different size per board."""
    frames0 = board.state()["frames"]
    board.swipe(x0, y, x1, y, frames=20)
    st = board.state()
    assert st["stack"][-1] == "launcher", st["stack"]
    assert st["frames"] > frames0, "the gesture drew no frames"
    # Swipe back so the shelf rests near where it started.
    board.swipe(x1, y, x0, y, frames=20)
    board.drain(0.8)                       # let the fling settle


def cart_runs_and_exits(board, spec, title=None):
    """THE test the 2026-08-17 _GATE_SEQ regression bought: a staged-constant
    deletion broke make_spr_gate -- and therefore EVERY cart start -- while
    both boards' suites stayed green, because nothing on glass ever RAN a
    cart. The host pyflakes net caught it one staged build later; this makes
    the glass able to catch its own. Launch through the real launcher path,
    assert the cart is ticking, exit through the cart-quit flag the Player
    honors (the same flag the cart-API quit() verb sets)."""
    line = board.cmd("run %s" % spec, wait_for="REMOTE run")
    assert line is not None and "no cart match" not in line, line
    board.drain(2.0)
    st = board.state()
    assert st.get("cart"), "the cart never started: %r" % st
    if title is not None:
        assert st["cart"] == title, st["cart"]
    assert not st.get("cart_error"), st["cart_error"]
    f0 = st["frames"]
    board.drain(1.0)
    assert board.state()["frames"] > f0, "the cart is not ticking"
    board.cmd("py ws.input.cart_quit = True", wait_for="PY")
    board.drain(1.5)
    st = board.state()
    assert not st.get("cart"), "quit did not pop to the caller: %r" % st
    assert st["stack"][-1] == "launcher"


def idle_blank_and_wake(board):
    """The idle blank (shared IdleBlank + shared power verb): blank on silence,
    wake on the next serial command, `power off` outranking its own arrival.

    Retuned to a few seconds here, restored by the caller -- the shipped
    default is 5 minutes. Silence is the actual stimulus, so the wait must send
    nothing."""
    board.cmd("power 3", wait_for="REMOTE power")
    board.drain(0.3)
    board.drain(6.0)                       # say nothing; let the timer expire
    assert board.state()["psave"][0] is True, "the panel never blanked"
    # ...and that state query was serial traffic, which counts as activity, so
    # the panel is already awake again by the following frame.
    board.drain(0.5)
    assert board.state()["psave"][0] is False, "input did not wake the panel"

    # `power off` blanks immediately. It arrives ON the serial channel, which is
    # itself activity -- the explicit blank has to outrank that or it wakes in
    # the same iteration (it did, before _ps_force).
    board.cmd("power off", wait_for="REMOTE power")
    board.drain(1.0)
    assert board.state()["psave"][0] is True, "`power off` did not blank"


def idle_timeout_restored(board):
    """Put the shipped default back, and prove it took."""
    board.cmd("power 300", wait_for="REMOTE power")
    board.drain(0.5)
    assert board.state()["psave"][0] is False
    assert board.state()["psave"][1] == 300


def mem_reports_the_heap(board):
    line = board.cmd("mem", wait_for="REMOTE mem")
    assert line is not None and "live=" in line and "free=" in line, line


def perf_line_is_the_one_format(board):
    """The PERF line, on real glass, in the one shape every board emits
    (#206 item 2).

    It had three shapes under one name, and the T-Deck's went through the diag
    ring -- whose `Moybyte <uptime> ` stamp made both readers filter it out, so
    the board whose fps most needed measuring was invisible to the tool that
    measures it. Hence: parse with the module that WRITES the line, assert every
    declared field arrived, and assert absence is spelled `-`.

    An idle desk paints nothing, so fps= reads 0/<loop rate>. That is the
    idle-paints-zero invariant, and this line is its witness."""
    from runtime.perf_line import FIELDS, parse_perf
    n0 = len(board.lines)
    board.drain(5.0)
    lines = board.perf_lines(n0)
    assert lines, "no PERF lines in 5s"
    got = parse_perf(lines[-1])
    missing = [n for n, _s, _u in FIELDS if n not in got]
    assert not missing, "%s: fields missing from %r" % (missing, lines[-1])
    fps = got["fps"]
    # Drawn frames cannot exceed looped ones -- the pair is the whole point of
    # `fps=<drawn>/<looped>`, and an idle desk sits at 0 drawn.
    assert isinstance(fps, tuple) and fps[1] > 0, lines[-1]
    assert 0 <= fps[0] <= fps[1], lines[-1]
    for name, _spec, _unit in FIELDS:
        v = got[name]
        assert v is None or isinstance(v, (float, tuple, str)), (name, v)
    # WHICH columns are `-` is the board's own capability claim, so the suites
    # assert that themselves -- this body is shared by a board that fills them.
    return got
