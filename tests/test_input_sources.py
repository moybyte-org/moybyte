"""The multi-source input model: every producer owns a source, the shared
state is their MERGE.

THE BUG THIS SUITE EXISTS FOR. `InputState` was one flat held-set plus one
`last_key`, and every driver wrote it asserting FULL AUTHORITY -- release_all()
and then "here is everything I hold". Correct with one writer, silently wrong
with two. The T-Deck has two (its physical C3 keyboard and an optional BLE HID
keyboard whose reports land from a radio IRQ between polls), so the keyboard
poll erased every BLE keypress within a frame: held buttons died immediately,
`last_key` survived only as a race, and the BLE keyboard did nothing at all on
that board.

Every behavioural test below runs against BOTH InputState classes, because
there are two and they diverge on purpose (different button vocabularies, in
different orders, with a different primary verb -- runtime/input.py's
`set_held` vs device/moybyte/input.py's `set_button`). A model that landed on
one tier and not the other would be exactly the shape of failure
`button_masks`'s docstring records: no crash, no failing test, no frame hash.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


device_input = _load(ROOT / "device" / "moybyte" / "input.py", "moy_device_input")
blekbd = _load(ROOT / "device" / "ble_keyboard.py", "moy_ble_under_test")
from runtime.input import InputState as HostInputState        # noqa: E402


TIERS = ("host", "device")


def _state(tier):
    return HostInputState() if tier == "host" else device_input.InputState()


def _set(src, name, down=True):
    """Both spellings of the same verb exist on a source, on both tiers, so a
    shared driver can write one without knowing which InputState it landed on."""
    src.set_button(name, down)


# -- the merge --------------------------------------------------------------

@pytest.mark.parametrize("tier", TIERS)
def test_two_sources_holding_different_buttons_both_appear_in_the_union(tier):
    inp = _state(tier)
    kbd = inp.source("kbd")
    ble = inp.source("ble")

    _set(kbd, "up")
    _set(ble, "a")
    inp.begin_frame()

    assert inp.held("up") and inp.held("a")
    assert inp.pressed("up") and inp.pressed("a")


@pytest.mark.parametrize("tier", TIERS)
def test_one_sources_release_all_does_not_clear_the_other(tier):
    # THE LIVE BUG, at the level of the model: `release_all` on a source means
    # "*I* hold nothing", not "everybody let go".
    inp = _state(tier)
    kbd = inp.source("kbd")
    ble = inp.source("ble")
    _set(kbd, "up")
    _set(ble, "a")
    inp.begin_frame()

    kbd.release_all()
    assert inp.held("a")            # ...immediately, not only after begin_frame
    assert not inp.held("up")
    inp.begin_frame()
    assert inp.held("a") and not inp.held("up")
    assert inp.released("up") and not inp.released("a")


@pytest.mark.parametrize("tier", TIERS)
def test_a_button_two_sources_hold_survives_one_of_them_letting_go(tier):
    inp = _state(tier)
    a = inp.source("a")
    b = inp.source("b")
    _set(a, "up")
    _set(b, "up")
    inp.begin_frame()
    assert inp.held("up")

    _set(a, "up", False)
    assert inp.held("up")           # b still holds it
    inp.begin_frame()
    assert inp.held("up") and not inp.released("up")

    _set(b, "up", False)
    inp.begin_frame()
    assert not inp.held("up") and inp.released("up")


@pytest.mark.parametrize("tier", TIERS)
def test_the_shared_release_all_still_means_everybody_let_go(tier):
    # cards_layer / block_editor_ui call this when a modal opens; it must keep
    # dropping every button from every source.
    inp = _state(tier)
    a = inp.source("a")
    b = inp.source("b")
    _set(a, "up")
    _set(b, "a")
    inp.begin_frame()

    inp.release_all()
    assert not inp.held("up") and not inp.held("a")
    inp.begin_frame()
    assert not inp.held("up") and not inp.held("a")


@pytest.mark.parametrize("tier", TIERS)
def test_todays_flat_api_keeps_working_through_an_implicit_default_source(tier):
    inp = _state(tier)
    if tier == "host":
        inp.set_held("up", True)
    else:
        inp.set_button("up", True)
    inp.begin_frame()
    assert inp.held("up") and inp.pressed("up")

    # ...and it is a SOURCE, so it merges with the rest rather than owning
    # everything: a driver source's release_all leaves it alone.
    ble = inp.source("ble")
    _set(ble, "a")
    inp.begin_frame()
    ble.release_all()
    inp.begin_frame()
    assert inp.held("up")


# -- last_key ---------------------------------------------------------------

@pytest.mark.parametrize("tier", TIERS)
def test_a_source_that_did_not_type_never_zeroes_anothers_last_key(tier):
    # This is the one line the live bug was on: TDeckKeyboard._apply wrote
    # `self.input.last_key = key` (0 when idle) on EVERY poll.
    inp = _state(tier)
    kbd = inp.source("kbd")
    ble = inp.source("ble")

    ble.last_key = ord("x")
    assert inp.last_key == ord("x")

    for _ in range(5):              # the physical keyboard polling, idle
        kbd.last_key = 0
        inp.begin_frame()
        assert inp.last_key == ord("x")


@pytest.mark.parametrize("tier", TIERS)
def test_the_most_recent_keypress_wins_and_a_held_key_does_not_re_steal_it(tier):
    inp = _state(tier)
    kbd = inp.source("kbd")
    ble = inp.source("ble")

    ble.last_key = ord("x")         # held on the BLE keyboard
    kbd.last_key = ord("a")         # a NEW press on the physical one wins
    assert inp.last_key == ord("a")

    ble.last_key = ord("x")         # merely re-reporting a key it already held
    assert inp.last_key == ord("a")

    kbd.last_key = 0                # the owner goes quiet -> hand the slot back
    assert inp.last_key == ord("x")

    ble.last_key = 0
    assert inp.last_key == 0


@pytest.mark.parametrize("tier", TIERS)
def test_the_owner_re_asserts_so_a_stray_direct_write_heals(tier):
    # `inp.last_key` stays a plain attribute (it is read several times a frame
    # and a property get is ~10x a plain one on MicroPython), so a legacy
    # writer can still clobber the merged value. The next write from the owner
    # must put it back rather than latch the stale 0.
    inp = _state(tier)
    ble = inp.source("ble")
    ble.last_key = ord("x")
    inp.last_key = 0                        # a legacy direct write
    ble.last_key = ord("x")                 # the owner's next poll
    assert inp.last_key == ord("x")


# -- players ----------------------------------------------------------------

@pytest.mark.parametrize("tier", TIERS)
def test_player_views_separate_while_the_shell_view_stays_the_union(tier):
    inp = _state(tier)
    p1 = inp.source("kbd")                  # player 0 by default
    p2 = inp.source("ble")
    p2.player = 1

    _set(p1, "up")
    _set(p2, "a")
    inp.begin_frame()

    # The OS/shell view: any connected controller drives the console.
    assert inp.held("up") and inp.held("a")
    # The cart view (btn(name, player)).
    assert inp.held("up", 0) and not inp.held("a", 0)
    assert inp.held("a", 1) and not inp.held("up", 1)
    assert inp.pressed("a", 1) and not inp.pressed("a", 0)
    assert not inp.held("up", 2)

    inp.begin_frame()                       # a second frame: still held, no edge
    assert inp.held("a", 1) and not inp.pressed("a", 1)

    assert inp.player_count() == 2
    assert set(inp.source_players()) == {0, 1}


@pytest.mark.parametrize("tier", TIERS)
def test_one_player_is_the_fast_path_and_answers_slot_zero(tier):
    inp = _state(tier)
    _set(inp.source("kbd"), "up")
    inp.begin_frame()
    assert inp.held("up", 0) is True
    assert inp.held("up", 1) is False
    assert inp.player_count() == 1
    assert inp.source_players() == (0,)


# -- button_masks -----------------------------------------------------------

@pytest.mark.parametrize("tier", TIERS)
def test_button_masks_merges_sources_and_still_answers_the_same_two_integers(tier):
    # moycore's per-frame snapshot calls button_masks(order) and reads exactly
    # two integers; growing a `player` argument must not move that call.
    inp = _state(tier)
    order = ("left", "right", "up", "down", "a", "b")
    kbd = inp.source("kbd")
    ble = inp.source("ble")
    _set(kbd, "up")
    _set(ble, "a")
    inp.begin_frame()

    held, pressed = inp.button_masks(order)
    assert held == (1 << 2) | (1 << 4)
    assert pressed == held


@pytest.mark.parametrize("tier", TIERS)
def test_button_masks_packs_one_players_sources_when_asked(tier):
    inp = _state(tier)
    order = ("left", "right", "up", "down", "a", "b")
    kbd = inp.source("kbd")
    ble = inp.source("ble")
    ble.player = 1
    _set(kbd, "up")
    _set(ble, "a")
    inp.begin_frame()

    assert inp.button_masks(order) == ((1 << 2) | (1 << 4),) * 2   # the union
    assert inp.button_masks(order, 0) == (1 << 2, 1 << 2)
    assert inp.button_masks(order, 1) == (1 << 4, 1 << 4)
    assert inp.button_masks(order, 2) == (0, 0)


def test_the_device_source_still_refuses_an_unknown_button():
    inp = device_input.InputState()
    with pytest.raises(ValueError):
        inp.source("kbd").set_button("zorp", True)


# -- the two real drivers, on one InputState --------------------------------

class _FakeI2C:
    """The T-Deck keyboard's five raw-matrix bytes, on demand."""

    def __init__(self, frame=b"\x00\x00\x00\x00\x00"):
        self.frame = frame

    def readfrom(self, _addr, _size):
        return self.frame


def _tdeck_keyboard(inp):
    kbd = device_input.TDeckKeyboard.__new__(device_input.TDeckKeyboard)
    kbd.input = inp
    kbd.available = True
    kbd.raw_mode = True
    kbd._i2c = _FakeI2C()
    return kbd


def test_the_physical_keyboards_poll_no_longer_erases_the_ble_keyboard():
    """THE REGRESSION, with both real drivers on one real InputState.

    Before the multi-source model this failed on the first assertion: the
    physical keyboard's `_apply` did `input.last_key = key` (0 when idle) then
    `input.release_all()` and set only its own buttons, so a BLE press that
    arrived from the radio IRQ between polls was gone within the frame.
    """
    inp = device_input.InputState()
    inp.text_mode = False
    kbd = _tdeck_keyboard(inp)
    ble = blekbd.BleHidKeyboard(inp, store_path=None, auto_start=False)

    # A BLE report lands (usage 0x1A = W -> the `up` button, key 'w').
    ble._reports[7] = (0, (0x1A,))
    ble.poll()

    # ...and the physical keyboard polls an EMPTY matrix, over and over.
    for _ in range(5):
        kbd.poll()
        inp.begin_frame()
        assert inp.held("up"), "the physical keyboard's poll erased the BLE hold"
        assert inp.last_key == ord("w"), "an idle source zeroed another's key"

    # Both keyboards at once: the union, and each keeps its own half.
    kbd._i2c.frame = bytes([0x00, 0x04, 0, 0, 0])     # 'd' -> the `right` button
    kbd.poll()
    ble.poll()
    inp.begin_frame()
    assert inp.held("up") and inp.held("right")

    # The BLE keyboard lets go; the physical one keeps what it holds.
    ble._reports[7] = (0, ())
    ble.poll()
    kbd.poll()
    inp.begin_frame()
    assert inp.held("right") and not inp.held("up")
    assert inp.last_key == ord("d")

    # ...and the other way round.
    kbd._i2c.frame = b"\x00\x00\x00\x00\x00"
    ble._reports[7] = (0, (0x1A,))
    ble.poll()
    kbd.poll()
    inp.begin_frame()
    assert inp.held("up") and not inp.held("right")
    assert inp.last_key == ord("w")


def test_the_two_keyboards_can_be_two_players():
    """Player assignment lives on the SOURCE, so making the BLE keyboard
    player 2 is one attribute -- no transport, no _Slot, no netcode."""
    inp = device_input.InputState()
    inp.text_mode = False
    kbd = _tdeck_keyboard(inp)
    ble = blekbd.BleHidKeyboard(inp, store_path=None, auto_start=False)
    ble.poll()                                   # binds its source
    ble.src.player = 1

    kbd._i2c.frame = bytes([0x00, 0x04, 0, 0, 0])    # 'd' -> `right`
    ble._reports[7] = (0, (0x1A,))                   # 'w' -> `up`
    kbd.poll()
    ble.poll()
    inp.begin_frame()

    assert inp.held("right", 0) and not inp.held("up", 0)
    assert inp.held("up", 1) and not inp.held("right", 1)
    assert inp.held("right") and inp.held("up")      # the shell still sees both
    assert inp.player_count() == 2


def test_the_router_reads_source_players_so_btn_and_players_just_work():
    """#65's btn(name, player)/players() ARE the per-player view: a source
    given a player needs no transport to become a real slot."""
    from runtime import players as players_mod

    inp = HostInputState()
    router = players_mod.PlayerRouter(inp)
    assert router.count() == 1

    pad = inp.source("pad")
    pad.player = 1
    pad.set_held("a", True)
    inp.begin_frame()

    assert router.count() == 2
    assert router.held("a", 1) is True
    assert router.pressed("a", 1) is True
    # Slot 0 is the local console, and the pad has left it.
    assert router.held("a", 0) is False
    # A transport slot and a source slot with the SAME index are one player.
    router.add_player(1)
    assert router.count() == 2


def test_a_one_player_cart_responds_to_any_controller():
    """The owner's requirement, as a test: a cart that never mentions a player
    is driven by whatever is connected -- including a source somebody assigned
    to player 2."""
    from runtime import host_app, players as players_mod

    class _Stub:
        w = h = 64

        def __getattr__(self, _name):
            return lambda *a, **k: None

    inp = HostInputState()
    inp.players = players_mod.PlayerRouter(inp)
    ns = host_app.make_api(_Stub(), inp, {})

    pad = inp.source("pad")
    pad.player = 1
    pad.set_held("a", True)
    inp.begin_frame()

    assert ns["btn"]("a") is True            # no player named -> any controller
    assert ns["btnp"]("a") is True
    assert ns["btn"]("a", 1) is True         # ...and it is addressable as P2
    assert ns["btn"]("a", 0) is False        # ...and it has left slot 0
    assert ns["players"]() == 2
