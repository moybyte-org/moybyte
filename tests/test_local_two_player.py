"""LOCAL two-player: a paired Bluetooth keyboard is player two (#65 Phase 1).

Two kids, two real keyboards, one screen, and no radio between consoles. The
whole mechanism is the #26 source model doing exactly what it was built for: a
source carries a player, two sources disagreeing IS multiplayer, so `players()`
reports 2 with no transport, no session and no netcode anywhere in this file.

It is capability-gated to a board with a SECOND keyboard -- the T-Deck's paired
Bluetooth one alongside the physical C3 it already has. On the touch-only boards
a BLE keyboard IS `ws.keyboard`, the only one there is, so handing it to player
two would leave player one with nothing to press.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device"))
from moybyte import input as _imod  # noqa: E402
import ble_keyboard as _ble  # noqa: E402
sys.path.remove(str(ROOT / "device"))

from runtime import players as players_mod  # noqa: E402


class _FakeBle:
    """A BLE keyboard reduced to what the player slot touches: a source, a
    connection state, and the two methods the console calls."""

    def __init__(self, state, connected=True):
        self.src = state.source("ble")
        self.state = "ready" if connected else "idle"
        self._want_player = 0

    set_player = _ble.BleHidKeyboard.set_player
    _sync_player = _ble.BleHidKeyboard._sync_player


class _Prefs:
    """The slice of `SystemStore` the setting touches: the dict `ws.system`
    aliases, and a write that goes nowhere."""

    def __init__(self):
        self.settings = {}

    def persist(self):
        pass


class _Ws:
    """The slice of the Workstation the setting touches."""

    two_player = False
    _dirty = False

    def __init__(self, keyboard=None, ble=None):
        self.keyboard = keyboard
        self.ble_keyboard = ble
        self.prefs = _Prefs()
        self.system = self.prefs.settings

    second_keyboard = None      # bound below from the real Workstation


def _ws(**kw):
    from runtime.console import Workstation
    w = _Ws(**kw)
    w.second_keyboard = Workstation.second_keyboard.__get__(w)
    w.set_two_player = Workstation.set_two_player.__get__(w)
    # The setter's tail (mirror + repaint mark + persisted copy) is the one
    # body every SETTINGS_TOGGLES verb shares since #209 section 7; what stays
    # this setter's own is the keyboard hand-over above it.
    w._set_toggle = Workstation._set_toggle.__get__(w)
    return w


# -- the mechanism ----------------------------------------------------------

def test_a_connected_bluetooth_keyboard_becomes_player_two():
    inp = _imod.InputState()
    kbd = inp.source("kbd")                 # the board's own keyboard
    ble = _FakeBle(inp)
    router = players_mod.PlayerRouter(inp)
    assert router.count() == 1, "both keyboards drive one player to begin with"

    ble.set_player(1)
    kbd.set_button("left", True)
    ble.src.set_button("right", True)
    inp.begin_frame()

    assert router.count() == 2, "a source with a player IS a player"
    assert router.held("left", 0) is True and router.held("right", 0) is False
    assert router.held("right", 1) is True and router.held("left", 1) is False
    # The OS view is still the union: either keyboard drives the shell.
    assert inp.held("left") is True and inp.held("right") is True


def test_an_unconnected_keyboard_does_not_hold_a_player_slot():
    """A cart must not field a second character nobody can move. The slot is an
    INTENT resolved against the live connection, not a latch."""
    inp = _imod.InputState()
    inp.source("kbd")
    ble = _FakeBle(inp, connected=False)
    router = players_mod.PlayerRouter(inp)

    ble.set_player(1)
    inp.begin_frame()
    assert router.count() == 1, "not connected, not a player"

    ble.state = "ready"                     # it pairs
    ble._sync_player()                      # what poll() does every frame
    inp.begin_frame()
    assert router.count() == 2

    ble.state = "idle"                      # ...and walks away again
    ble._sync_player()
    inp.begin_frame()
    assert router.count() == 1, "the slot is released, not stranded"


def test_the_slot_is_resolved_every_poll_without_thrashing_the_source():
    inp = _imod.InputState()
    ble = _FakeBle(inp)
    ble.set_player(1)
    before = inp._multi
    for _ in range(5):
        ble._sync_player()                  # idempotent: no rescan storm
    assert inp._multi == before
    assert ble.src.player == 1


def test_turning_it_off_returns_the_console_to_one_player():
    inp = _imod.InputState()
    inp.source("kbd")
    ble = _FakeBle(inp)
    router = players_mod.PlayerRouter(inp)
    ble.set_player(1)
    inp.begin_frame()
    assert router.count() == 2

    ble.set_player(0)
    inp.begin_frame()
    assert router.count() == 1
    assert inp.player_count() == 1


def test_the_real_driver_carries_the_verbs_the_console_calls():
    """The fake above borrows them, so pin that they exist on the real class."""
    assert callable(_ble.BleHidKeyboard.set_player)
    assert callable(_ble.BleHidKeyboard._sync_player)
    src = (ROOT / "device" / "ble_keyboard.py").read_text(encoding="utf-8")
    body = src[src.index("    def poll(self):"):]
    assert "_sync_player()" in body[:400], (
        "poll() must resolve the slot -- a keyboard that disconnects mid-game "
        "would otherwise keep a player nobody can move")


# -- the capability gate ----------------------------------------------------

def test_only_a_board_with_a_second_keyboard_can_do_this():
    inp = _imod.InputState()
    ble = _FakeBle(inp)

    # The T-Deck: a physical keyboard, and a BLE one beside it.
    tdeck = _ws(keyboard=object(), ble=ble)
    assert tdeck.second_keyboard() is ble

    # A touch-only board: the BLE keyboard IS the only keyboard.
    guition = _ws(keyboard=ble, ble=ble)
    assert guition.second_keyboard() is None, (
        "handing the only keyboard to player two leaves player one with nothing")

    # No BLE at all.
    assert _ws(keyboard=object(), ble=None).second_keyboard() is None


def test_the_setting_refuses_where_it_cannot_work():
    """Reporting ON with nothing able to produce a second player's buttons is
    the frozen-meter bug in another costume."""
    inp = _imod.InputState()
    ble = _FakeBle(inp)
    guition = _ws(keyboard=ble, ble=ble)
    guition.set_two_player(True, persist=False)
    assert guition.two_player is False
    assert ble.src.player == 0


def test_the_setting_drives_the_keyboard_and_persists():
    inp = _imod.InputState()
    ble = _FakeBle(inp)
    ws = _ws(keyboard=object(), ble=ble)

    ws.set_two_player(True)
    assert ws.two_player is True and ble.src.player == 1
    assert ws.system["two_player"] is True

    ws.set_two_player(False)
    assert ws.two_player is False and ble.src.player == 0
    assert ws.system["two_player"] is False


def test_the_settings_row_appears_only_where_the_option_works(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    rows = ws.settings_layer._settings_rows()
    assert not any(r[0] == "two_player" for r in rows), "no second keyboard, no row"

    ws.keyboard = object()
    ws.ble_keyboard = _FakeBle(_imod.InputState())
    rows = ws.settings_layer._settings_rows()
    keys = [r[0] for r in rows]
    assert "two_player" in keys
    # It sits with FRAMESKIP -- both are play-time trades.
    assert keys.index("two_player") == keys.index("frameskip") - 1
