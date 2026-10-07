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

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import moy_input as _mi  # noqa: E402
from runtime import players as players_mod  # noqa: E402


class _Stack:
    """NimBLE reduced to accepting every request."""

    def __getattr__(self, name):
        return lambda *a: 0

    def ms(self):
        return 1000


class _FakeBle:
    """The kernel's BLE keyboard machine (native/moy_input/moy_hid.c) over a
    stack that accepts everything: `state` drives it to ready or away, and
    the two verbs the console calls are the machine's."""

    ADDR = (0, b"\x01\x02\x03\x04\x05\x06")

    def __init__(self, table, connected=True):
        self.m = _mi.HidMachine(table, _Stack())
        self.src = self.m.src
        self.m.started(True)
        if connected:
            self.state = "ready"

    @property
    def state(self):
        return self.m.state

    @state.setter
    def state(self, value):
        m = self.m
        if value == "ready":
            m.scan_result(self.ADDR, -40, bytes((3, 0x03, 0x12, 0x18)))
            m.on_scan_done()
            m.connected(3, self.ADDR)
            m.on_svc(3, 1, 12, _mi.HID_SERVICE)
            m.on_svc_done(3, 0)
            m.on_chr(3, 2, 3, _mi.HID_NOTIFY, _mi.HID_REPORT)
            m.on_chr_done(3, 0)
            m.on_dsc(3, 4, _mi.HID_CCCD)
            m.on_dsc_done(3, 0)
            m.on_write_done(3, 4, 0)
        else:
            m.on_disconnect(3)

    def set_player(self, slot):
        self.m.set_player(slot)

    def poll(self):
        self.m.frame()


class _Prefs:
    """The slice of `SystemStore` the setting touches: the rows `ws.system`
    aliases, over a save hook that writes nowhere."""

    def __init__(self):
        from runtime.moy_spine import Settings
        self.rows = Settings(lambda text: True)


class _Ws:
    """The slice of the Workstation the setting touches."""

    two_player = False
    _dirty = False

    def __init__(self, keyboard=None, ble=None):
        self.keyboard = keyboard
        self.ble_keyboard = ble
        self.prefs = _Prefs()
        self.system = self.prefs.rows

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
    inp = _mi.InputTable()
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
    inp = _mi.InputTable()
    inp.source("kbd")
    ble = _FakeBle(inp, connected=False)
    router = players_mod.PlayerRouter(inp)

    ble.set_player(1)
    inp.begin_frame()
    assert router.count() == 1, "not connected, not a player"

    ble.state = "ready"                     # it pairs
    ble.poll()                              # what a frame does
    inp.begin_frame()
    assert router.count() == 2

    ble.state = "idle"                      # ...and walks away again
    ble.poll()
    inp.begin_frame()
    assert router.count() == 1, "the slot is released, not stranded"


def test_the_slot_is_resolved_every_poll_without_thrashing_the_source():
    inp = _mi.InputTable()
    ble = _FakeBle(inp)
    ble.set_player(1)
    before = inp.multi()
    for _ in range(5):
        ble.poll()                          # idempotent: no rescan storm
    assert inp.multi() == before
    assert ble.src.player == 1


def test_turning_it_off_returns_the_console_to_one_player():
    inp = _mi.InputTable()
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
    """A frame resolves the slot -- a keyboard that disconnects mid-game would
    otherwise keep a player nobody can move."""
    src = (ROOT / "native" / "moy_input" / "moy_hid.c").read_text(encoding="utf-8")
    body = src[src.index("void moy_hid_frame(moy_hid_t *h) {"):]
    assert "sync_player(h);" in body[:200]
    binding = (ROOT / "native" / "moy_input" / "modmoy_input.c").read_text(encoding="utf-8")
    assert "MP_QSTR_set_player" in binding and "moy_hid_frame(" in binding


# -- the capability gate ----------------------------------------------------

def test_only_a_board_with_a_second_keyboard_can_do_this():
    inp = _mi.InputTable()
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
    inp = _mi.InputTable()
    ble = _FakeBle(inp)
    guition = _ws(keyboard=ble, ble=ble)
    guition.set_two_player(True, persist=False)
    assert guition.two_player is False
    assert ble.src.player == 0


def test_the_setting_drives_the_keyboard_and_persists():
    inp = _mi.InputTable()
    ble = _FakeBle(inp)
    ws = _ws(keyboard=object(), ble=ble)

    ws.set_two_player(True)
    assert ws.two_player is True and ble.src.player == 1
    assert ws.system.get("two_player") is True

    ws.set_two_player(False)
    assert ws.two_player is False and ble.src.player == 0
    assert ws.system.get("two_player") is False


def test_the_settings_row_appears_only_where_the_option_works(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    rows = ws.settings_layer._settings_rows()
    assert not any(r[0] == "two_player" for r in rows), "no second keyboard, no row"

    ws.keyboard = object()
    ws.ble_keyboard = _FakeBle(_mi.InputTable())
    rows = ws.settings_layer._settings_rows()
    keys = [r[0] for r in rows]
    assert "two_player" in keys
    # It sits with STEADY -- both are play-time trades.
    assert keys.index("two_player") == keys.index("steady") - 1
