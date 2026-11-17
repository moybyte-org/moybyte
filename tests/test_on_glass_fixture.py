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

import ast
import re

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
                 reset_is_damage=False, store=("celeste.moy", "doom.moy"),
                 marked=()):
        self.store = list(store)
        self.marked = set(marked)
        self.deleted = []
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

    def pyexec(self, code, timeout=30.0, strict=False):
        self.calls.append(("pyexec",))
        return True

    def pyval(self, expr, timeout=30.0, strict=False):
        """The store helpers `session()` calls (push_cart.STORE), answered from
        a list of folders: `_folders` lists them with their marks, `_remove`
        removes the ones it names, `_mark` marks one."""
        self.calls.append(("pyval", timeout))
        if expr.startswith("ws._g['_folders']"):
            return sorted([f, f in self.marked] for f in self.store)
        arg = ast.literal_eval(re.search(r"\]\((.*)\)$", expr).group(1))
        if expr.startswith("ws._g['_remove']"):
            self.deleted += [f for f in arg if f in self.store]
            self.store = [f for f in self.store if f not in arg]
            self.marked -= set(arg)
            return 1
        if expr.startswith("ws._g['_mark']"):
            self.marked.add(arg.rsplit("/", 2)[-2])
            return 1
        raise AssertionError("an expression session() does not send: %r" % expr)

    def close(self):
        self.calls.append(("close",))


def _no_shelf(calls):
    return [c for c in calls if c[0] not in ("pyval", "pyexec")]


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
    assert _no_shelf(b.calls) == [("drain", 0.8), ("cmd", "state"),
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
    assert _no_shelf(b.calls) == [("reset",), ("verify_board",),
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


def test_the_store_is_given_back_with_only_the_new_folders_removed(monkeypatch):
    """What a suite pushed goes; what was there stays -- the owner's games
    included, and a fixture written over a folder that was already there."""
    made = _install(monkeypatch, attach_only=True)
    with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
        b.store += ["wasm_tampered.moy", "wasm_hello", "custom.moy"]
        b.store += ["local.ring_py%d.moy" % i for i in range(9)]
    assert sorted(b.store) == ["celeste.moy", "doom.moy"]
    assert sorted(b.deleted) == sorted(
        ["wasm_tampered.moy", "wasm_hello", "custom.moy"]
        + ["local.ring_py%d.moy" % i for i in range(9)])
    assert made[0].calls[-1] == ("close",)


def test_a_marked_push_goes_and_one_an_earlier_session_left_goes_first(monkeypatch):
    """A session killed before it closed leaves its pushes marked; the next
    session removes them when it opens, before it records what to keep, and a
    push it marks itself goes when it closes."""
    _install(monkeypatch, attach_only=True,
             store=("celeste.moy", "elf_hello.moy"), marked=("elf_hello.moy",))
    with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
        assert b.store == ["celeste.moy"], "the earlier session's push survived the open"
        b.store.append("stuck_lua.moy")
        on_glass.mark_pushed(b, "/sd/moybyte/carts/stuck_lua.moy")
        assert b.marked == {"stuck_lua.moy"}
    assert b.store == ["celeste.moy"]
    assert b.deleted == ["elf_hello.moy", "stuck_lua.moy"]


def test_the_removal_goes_in_batches_with_a_long_timeout(monkeypatch):
    import push_cart
    _install(monkeypatch, attach_only=True)
    with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
        b.store += ["x%d.moy" % i for i in range(50)]
    removes = [c for c in b.calls if c[0] == "pyval" and c[1] >= push_cart.REMOVE_S]
    assert len(removes) >= 50 // push_cart.REMOVE_BATCH
    assert b.store == ["celeste.moy", "doom.moy"]


def test_a_failing_suite_still_gives_the_store_back(monkeypatch):
    made = _install(monkeypatch, attach_only=True)
    with pytest.raises(ValueError):
        with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
            b.store.append("stuck_lua.moy")
            raise ValueError("a test blew up")
    assert made[0].store == ["celeste.moy", "doom.moy"]
    assert made[0].calls[-1] == ("close",)


def test_a_board_that_never_listed_its_store_is_left_alone(monkeypatch):
    _install(monkeypatch, attach_only=True)
    monkeypatch.setattr(_FakeBoard, "pyval",
                        lambda self, expr, **kw: self.calls.append(
                            ("pyval", 0)))
    with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
        pass
    assert b.calls.count(("pyval", 0)) == 1, "nothing recorded, nothing removed"


def test_a_store_that_cannot_be_given_back_warns_and_does_not_raise(monkeypatch):
    _install(monkeypatch, attach_only=True)
    monkeypatch.setattr(_FakeBoard, "pyval", lambda self, expr, **kw: (
        sorted([f, False] for f in self.store) if "_folders" in expr else 1))
    with pytest.warns(UserWarning, match="left folders on the store"):
        with on_glass.session("/dev/ttyACM2", board_dir=TDECK) as b:
            b.store.append("stuck.moy")


class _ChunkedSerial:
    """A port that hands the reader one prepared read at a time: each entry of
    `reads` is what one `in_waiting`-sized read returns, so two lines in one
    entry arrive in the same `_pump`."""

    port = "fake"

    def __init__(self, reads):
        self.reads = [r.encode() for r in reads]

    @property
    def in_waiting(self):
        return len(self.reads[0]) if self.reads else 0

    def read(self, n=1):
        return self.reads.pop(0) if self.reads else b""

    def write(self, data):
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


def test_two_lines_in_one_read_are_both_found_in_order():
    """`KSTOP i/n before` and `after` print a short teardown apart and can
    reach the host in one serial read. `wait_line` reads only what is still
    to come, so the second line of that read is behind it; `_line_since`
    walks the transcript by index and finds both, in order."""
    burst = ("KSTOP 1/2 before psram=5/4\nKSTOP 1/2 after psram=7/5\n",
             "MPY: soft reboot\n",
             "KSTOP 2/2 before psram=5/4\nKSTOP 2/2 after psram=7/5\n")
    b = p4_autotest.P4Board("fake", board_dir=GUITION, ser=_ChunkedSerial(burst))
    assert b.wait_line("KSTOP ", 0.2).startswith("KSTOP 1/2 before")
    assert b.wait_line("KSTOP 1/2 after", 0.2) is None

    b = p4_autotest.P4Board("fake", board_dir=GUITION, ser=_ChunkedSerial(burst))
    seen, at = [], 0
    for _ in range(4):
        line, at = on_glass._line_since(b, at, "KSTOP ", 1.0)
        seen.append(line.split(" psram", 1)[0])
    assert seen == ["KSTOP 1/2 before", "KSTOP 1/2 after",
                    "KSTOP 2/2 before", "KSTOP 2/2 after"]
    assert on_glass._line_since(b, at, "KSTOP ", 0.1) == (None, at)
    assert on_glass._said_since(b, 0, "MPY: soft reboot", 0)
