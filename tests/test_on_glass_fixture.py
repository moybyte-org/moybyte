"""The shared on-glass fixture, driven with no board on the desk (#206 item 3).

`tests/on_glass.py`'s `session()` is now the ONE place all three hardware
suites decide whether a board is RESET or merely ATTACHED to -- and every one
of those suites is env-gated, so CI never executes a line of it. That is the
shape this repo keeps paying for: a mechanism promoted into one body with
nothing executable guarding it. This is the guard.

The board is a FAKE. `session()` resolves `P4Board` through `p4_autotest` at
call time, so a stand-in can record the call order and the branch is provable
on a host with nothing plugged in. What this cannot prove is that a console
answers on the far side; that is each board's own on-glass suite, and those
stay the gate.
"""

import pytest

import on_glass
from on_glass import ROOT

# on_glass puts ROOT/"tools" on sys.path -- that is its job, not this file's.
import p4_autotest

TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
GUITION = ROOT / "firmware" / "guition_jc3248w535"
P4 = ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"


class _FakeBoard:
    """Records the call order `session()` drives a board through.

    Only the surface `session()` touches. `reset()` is the dangerous verb, so
    `reset_is_damage` makes it RAISE rather than record -- see the attach-only
    test for why recording it would not be enough."""

    def __init__(self, port, board_dir=None, attach_only=True,
                 expect_board="tdeck", state_reply="STATE {}",
                 reset_is_damage=False):
        self.port = port
        self.board_dir = board_dir
        self.attach_only = attach_only
        self.expect_board = expect_board
        self.calls = []
        self._state_reply = state_reply
        self._reset_is_damage = reset_is_damage

    def drain(self, secs):
        self.calls.append(("drain", secs))

    def cmd(self, text, **kw):
        self.calls.append(("cmd", text))
        return self._state_reply if text == "state" else "REMOTE"

    def reset(self):
        if self._reset_is_damage:
            raise AssertionError(
                "session() RESET a board that declares attach_only -- on a "
                "SoC-USB board that strands the handle")
        # The real reset() waits for the boot banner and verifies identity.
        self.calls.append(("reset",))
        self.calls.append(("verify_board",))

    def verify_board(self):
        self.calls.append(("verify_board",))

    def close(self):
        self.calls.append(("close",))


def _install(monkeypatch, **kw):
    """Point `session()` at a fake board; returns the list it builds into."""
    made = []

    def factory(port, board_dir=None):
        made.append(_FakeBoard(port, board_dir, **kw))
        return made[-1]

    monkeypatch.setattr(p4_autotest, "P4Board", factory)
    return made


def test_an_attach_only_board_is_probed_in_order_and_never_reset(monkeypatch):
    """The two S3 boards' path, asserted as an ORDER because every step earns
    its place: drain what is mid-flight, ask `state` (is a desktop even
    running?), then verify WHICH board answered -- both S3s share usb id
    303a:1001, so an answer alone proves only that SOME console is listening."""
    made = _install(monkeypatch, attach_only=True)
    with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
        pass
    assert b is made[0]
    assert b.calls == [("drain", 0.8), ("cmd", "state"),
                       ("verify_board",), ("close",)]
    assert b.board_dir == TDECK, "the suite's own board.toml is what was read"


def test_a_board_that_declares_attach_only_is_never_reset(monkeypatch):
    """THE failure this guard exists for, and the reason the fake FAILS on
    reset instead of recording it.

    `reset()` pulses RTS. On a board whose USB serial is on the SoC that
    re-enumerates the device under the open handle and every read afterwards
    returns nothing, forever -- indistinguishable from a dead board, and it has
    cost this project whole sessions of "the board is silent". The driver
    refuses it too (`P4Board.reset()`, pinned by test_board_toml); this pins
    that `session()` never asks."""
    for board_dir in (TDECK, GUITION):
        assert p4_autotest.declared_serial(board_dir)["attach_only"] is True, (
            "%s stopped declaring attach_only -- this test would be guarding a "
            "board that does not exist" % board_dir.name)
        made = _install(monkeypatch, attach_only=True, reset_is_damage=True)
        with on_glass.session("/dev/ttyACM2", board_dir=board_dir):
            pass
        assert ("reset",) not in made[0].calls


def test_the_reset_capable_board_is_reset_and_never_probed_for_state(monkeypatch):
    """The P4's CH343 is an external USB-UART that survives a chip reset, so
    its suite resets and takes a known-fresh desktop. `reset()` waits for the
    boot banner and verifies identity itself, so a `state` probe here would be
    asking a question already answered -- and would answer it against a board
    that is still booting."""
    assert p4_autotest.declared_serial(P4)["attach_only"] is False
    made = _install(monkeypatch, attach_only=False, expect_board="p4")
    with on_glass.session("/dev/ttyACM3", board_dir=P4) as b:
        b.cmd("diag 1")                     # what the P4 suite does with it
    assert b is made[0]
    assert b.calls == [("reset",), ("verify_board",),
                       ("cmd", "diag 1"), ("close",)]


def test_a_silent_attach_only_board_raises_and_still_closes(monkeypatch):
    """No `state` reply is the ordinary "the desktop is not running" case and
    has to SAY so, naming the board and the port. It must also not leak the
    handle: a port left open reads as a busy port on the next attempt, which
    is a second, different-looking fault."""
    made = _install(monkeypatch, attach_only=True, expect_board="guition_s3",
                    state_reply=None)
    with pytest.raises(RuntimeError) as exc:
        with on_glass.session("/dev/ttyACM1", board_dir=GUITION):
            raise AssertionError("a silent board yielded a session")
    assert "guition_s3 did not answer `state`" in str(exc.value)
    assert "/dev/ttyACM1" in str(exc.value)
    assert "it does not reset" in str(exc.value)
    assert made[0].calls[-1] == ("close",), "the port leaked on a silent board"


def test_a_failure_inside_the_suite_still_closes_the_port(monkeypatch):
    """The fixture is module-scoped, so it holds the port for a whole suite. A
    close skipped on the failure path would make the NEXT run fail at the open,
    reporting the wrong thing about a healthy board."""
    made = _install(monkeypatch, attach_only=True)
    with pytest.raises(ValueError):
        with on_glass.session("/dev/ttyACM2", board_dir=TDECK):
            raise ValueError("a test blew up")
    assert made[0].calls[-1] == ("close",)


def test_all_three_suites_go_through_the_shared_session():
    """The other half. A guard on `session()` is worth nothing if a suite
    hand-rolls its fixture again -- which is exactly what the three of them did
    until #206, each with its own copy of the line state and the never-reset
    rule."""
    for suite in ("test_p4_on_glass.py", "test_tdeck_on_glass.py",
                  "test_guition_on_glass.py"):
        src = (ROOT / "tests" / suite).read_text(encoding="utf-8")
        assert "on_glass.session(" in src, (
            "%s no longer reaches the shared session" % suite)
        assert "P4Board(" not in src, (
            "%s builds the driver itself again -- the attach/reset decision "
            "has left the one place that is guarded" % suite)
