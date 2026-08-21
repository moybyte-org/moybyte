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

The second thing pinned here: `begin_frame` is the union's SOLE author and
consumers read after it. The tests below make both halves of that a failure
rather than a convention.
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
    inp.begin_frame()               # the merge is what publishes it
    assert inp.held("a")
    assert not inp.held("up")
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
    inp.begin_frame()
    assert inp.held("up") and not inp.released("up")    # b still holds it

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


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("holder", ("state", "source"))
@pytest.mark.parametrize("verb", ("set_held", "set_button"))
def test_both_spellings_of_the_verb_exist_on_both_tiers(tier, holder, verb):
    """The tiers keep different PRIMARY spellings on purpose, so every object a
    shared driver can be handed carries both. `runtime/web_input.py` calls
    `set_button` on whatever InputState it is given; the host state carried only
    `set_held`, so the browser's hold events would have raised there. It was
    unreached only because every real host path supplies `on_hold`.
    """
    inp = _state(tier)
    target = inp if holder == "state" else inp.source("kbd")
    getattr(target, verb)("up", True)
    inp.begin_frame()
    assert inp.held("up")
    getattr(target, verb)("up", False)
    inp.begin_frame()
    assert not inp.held("up")


# -- the union has exactly ONE author ---------------------------------------
#
# `_held` is DERIVED: a source write moves the SOURCE, and the frame's union is
# whatever the last begin_frame merged.

@pytest.mark.parametrize("tier", TIERS)
def test_a_source_write_does_not_move_the_union_until_begin_frame(tier):
    """A mid-frame read answers for the frame that is still current -- which is
    what keeps a report landing from a radio IRQ from changing the buttons
    under the code already reading them."""
    inp = _state(tier)
    kbd = inp.source("kbd")

    _set(kbd, "up")
    assert not inp.held("up")               # the source has it; this frame does not
    assert "up" in kbd._held
    assert inp.button_masks(("up",)) == (0, 0)
    inp.begin_frame()
    assert inp.held("up") and inp.pressed("up")
    assert inp.button_masks(("up",)) == (1, 1)

    _set(kbd, "up", False)
    assert inp.held("up")                   # ...and a release is just as lazy
    inp.begin_frame()
    assert not inp.held("up") and inp.released("up")

    # A SOURCE's release_all is the same rule (it is a driver's every-poll
    # verb, so this is the hot one).
    _set(kbd, "a")
    inp.begin_frame()
    assert inp.held("a")
    kbd.release_all()
    assert inp.held("a")
    assert not kbd._held
    inp.begin_frame()
    assert not inp.held("a")


@pytest.mark.parametrize("tier", TIERS)
def test_the_shared_release_all_stays_immediate_and_stays_consistent(tier):
    """The one write to the union outside the merge, and not a second author of
    it: it empties every SOURCE first, so what it leaves behind is exactly what
    the next merge would build. Its callers (cards_layer._open_meta,
    block_editor_ui._blk_arm_prompt) blank the edge sets in the same breath and
    need it to have taken effect."""
    inp = _state(tier)
    a = inp.source("a")
    _set(a, "up")
    inp.begin_frame()

    inp.release_all()
    assert not inp.held("up")               # immediately
    before = set(inp._held)
    inp._merge()                            # ...and the merge agrees
    assert inp._held == before


@pytest.mark.parametrize("tier", TIERS)
def test_no_source_mutator_writes_the_shared_union(tier):
    """The ratchet: a mirror into the shared union is two lines, and exactly
    the sort of line that comes back the next time someone wants a mid-frame
    read to be live."""
    path = (ROOT / "runtime" / "input.py") if tier == "host" \
        else (ROOT / "device" / "moybyte" / "input.py")
    src = path.read_text()
    body = src[src.index("class InputSource"):src.index("class InputState")]
    assert "state._held" not in body, "the union mirror is back in InputSource"
    assert "_drop" not in body, "the incremental union drop is back"
    # ...and its helpers stayed deleted rather than lingering unused.
    for gone in ("def _drop(", "def _only_holder(", "def _release_shared("):
        assert gone not in src, gone


@pytest.mark.parametrize("tier", TIERS)
def test_the_merge_is_reached_only_through_begin_frame(tier):
    """One caller, so `begin_frame` is a real frame boundary and not just the
    usual one."""
    path = (ROOT / "runtime" / "input.py") if tier == "host" \
        else (ROOT / "device" / "moybyte" / "input.py")
    src = path.read_text()
    lines = [ln.split("#", 1)[0] for ln in src.splitlines()]
    calls = [i for i, ln in enumerate(lines)
             if "_merge()" in ln and "def _merge" not in ln]
    assert len(calls) == 1, [lines[i] for i in calls]
    at = calls[0]
    assert lines[at].strip() == "self._merge()"
    # ...and the enclosing def is begin_frame: walk back to the nearest one.
    owner = next(ln for ln in reversed(lines[:at]) if ln.lstrip().startswith("def "))
    assert owner.strip() == "def begin_frame(self):", owner


# -- the frame loops read AFTER the merge -----------------------------------

BOARD_RUNTIMES = {
    "guition": "firmware/guition_jc3248w535/modules/moy_runtime.py",
    "p4": "firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py",
    "tdeck": "firmware/lilygo_t_deck_plus_mainline/modules/moy_runtime.py",
}

# Everything that WRITES an InputSource inside a board's _poll_inputs. The
# trackball's `ball.poll()` is deliberately absent: it feeds the pointer and
# ws.nav, never a button, so it legitimately runs after the merge.
SOURCE_WRITERS = ("poller.consume()", "keyboard.poll()", "_ble.poll()")


def _code_lines(src, start_at, stop_at):
    """The CODE of one block: docstrings and comments stripped, because the
    thing being measured is what runs, and both boards' `_poll_inputs`
    docstrings say the words `inp.begin_frame()` before the call does."""
    body = src[src.index(start_at):]
    body = body[:body.index(stop_at, len(start_at))]
    out = []
    quoted = False
    for ln in body.splitlines():
        if ln.count('"""') == 1:
            quoted = not quoted
            continue
        if quoted or ln.count('"""') == 2:
            continue
        out.append(ln.split("#", 1)[0])
    return out


def _line_of(lines, needle, board):
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    assert hits, (board, needle)
    return hits[0]


@pytest.mark.parametrize("board", sorted(BOARD_RUNTIMES))
def test_every_board_writes_every_source_before_begin_frame(board):
    """The consumer half of the contract: with the union derived once per
    frame, a source written AFTER the merge is read one frame late -- silently,
    and only on that board."""
    src = (ROOT / BOARD_RUNTIMES[board]).read_text()
    lines = _code_lines(src, "def _poll_inputs(", "\n    def ")
    merge = _line_of(lines, "inp.begin_frame()", board)
    seen = 0
    for writer in SOURCE_WRITERS:
        if any(writer in ln for ln in lines):
            seen += 1
            assert _line_of(lines, writer, board) < merge, (board, writer)
    assert seen, board


# The one shipped consumer that reads the union as a bare attribute. `active`
# holds the backlight on (device_boot.IdleBlank), so a stale read here blanks
# the screen under a held button -- on glass, with no host test failing. It is
# safe only because it runs AFTER inp.begin_frame().
ACTIVE_READ = "bool(inp._held)"


@pytest.mark.parametrize("board", sorted(BOARD_RUNTIMES))
def test_a_board_that_reads_the_union_for_its_idle_check_reads_it_after_the_merge(board):
    src = (ROOT / BOARD_RUNTIMES[board]).read_text()
    lines = _code_lines(src, "def _poll_inputs(", "\n    def ")
    if not any(ACTIVE_READ in ln for ln in lines):
        # The T-Deck spells its `active` differently (trackball counts + the
        # streamed last_key, which a held raw-matrix key sets every frame), so
        # it reads no union at all. Recorded, not required.
        assert board == "tdeck", board
        return
    merge = _line_of(lines, "inp.begin_frame()", board)
    assert _line_of(lines, ACTIVE_READ, board) > merge, board


def test_the_boards_idle_check_still_sees_a_held_button_after_the_merge():
    """...and the same thing behaviourally, through the real driver, in the
    boards' own order: poll every source, merge, then read."""
    inp = device_input.InputState()
    inp.text_mode = False
    kbd = _tdeck_keyboard(inp)

    kbd._i2c.frame = bytes([0x00, 0x04, 0, 0, 0])     # 'd' -> the `right` button
    kbd.poll()
    inp.begin_frame()
    assert bool(inp._held) and bool(inp.last_key)     # active -> backlight stays on

    kbd._i2c.frame = b"\x00\x00\x00\x00\x00"
    kbd.poll()
    inp.begin_frame()
    assert not inp._held and not inp.last_key         # idle -> the blank may arm


def test_the_tdeck_keyboard_smoke_stage_polls_before_it_merges():
    """A merge taken before the poll makes the button row lag the key by a
    frame."""
    src = (ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules"
           / "tdeck_smoke.py").read_text()
    lines = _code_lines(src, "def _run_phase(", "\ndef ")
    merge = _line_of(lines, "inp.begin_frame()", "tdeck_smoke")
    assert _line_of(lines, "kbd.poll()", "tdeck_smoke") < merge
    assert _line_of(lines, "poller.consume()", "tdeck_smoke") < merge


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
