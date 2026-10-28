"""The T-Deck's two keyboard decoders must agree, and match the vendor matrix.

There are two paths into `btn()` on that board and there always will be:

  * RAW MATRIX (`0x03`) while a cart runs -- the only mode that reports a HELD
    key, because the ASCII mode reports each key once on the press edge with no
    autorepeat.
  * TYPED ASCII everywhere else -- the code editor and the launcher, where the
    C3 resolves shift/sym on-keyboard and hands back one clean byte.

They are two decoders of one keyboard, and until 2026-08-14 they were two
hand-written mappings with nothing checking that they AGREED. They had drifted:
`hjkl` was a full vim d-pad on the raw path and only partly present on the
other, and an audit of the pair misread it an hour before this was written.

(test_tdeck_input has covered a few individual raw keys since #71 -- the
backspace/q/e trio -- so the map was not untested; what was missing is a check
that the two decoders say the same thing, which is the failure that actually
happened.)

Both now resolve through one `KEY_BUTTON` table, so what is worth pinning is
(a) that the table is what the owner chose, (b) that the raw bit positions are
the vendor firmware's, and (c) that the two paths produce the same answer for
every key.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENDOR_KBD = (ROOT / "firmware" / "lilygo_t_deck_plus_reference" / "examples"
              / "Keyboard_ESP32C3" / "Keyboard_ESP32C3.ino")


def _input_module():
    """The keyboard's decoders as the kernel has them (native/moy_input/
    moy_kbd.c), over the host's binding: the ASCII table read back key by key,
    the matrix table entry by entry, and the raw decode itself."""
    from runtime import moy_input as mi

    class TDeckKeyboard:
        @staticmethod
        def _buttons_for_key(key):
            return mi.buttons_for_key(key)

    class mod:
        KEY_BUTTON = {c: mi.buttons_for_key(c)[0] for c in range(128)
                      if not 65 <= c <= 90 and mi.buttons_for_key(c)}
        RAW_KEYS = tuple(e[:3] for e in mi.raw_keys())
        RAW_SYMS = {(e[0], e[1]): e[3] for e in mi.raw_keys() if e[3]}
        decode_raw = staticmethod(mi.decode_raw)

    mod.TDeckKeyboard = TDeckKeyboard
    return mod


def _vendor_matrix():
    """{(col, row): char} from the vendor keyboard firmware's `keyboard[][]`.

    The reference tree is UNTRACKED (see THIRD_PARTY.md), so a fresh checkout
    will not have it and the tests that need it skip. That is deliberate: it is
    a vendor dump, not ours to ship -- but when it IS present it is the only
    authority on which bit is which key, and guessing is what this file exists
    to stop.
    """
    src = VENDOR_KBD.read_text(encoding="utf-8", errors="replace")
    out = {}
    for col, row, ch in re.findall(
            r"keyboard\[(\d+)\]\[(\d+)\]\s*=\s*'(.)'", src):
        out[(int(col), int(row))] = ch
    return out


# -- the scheme the owner chose ---------------------------------------------

EXPECTED = {
    "w": "up", "s": "down", "a": "left", "d": "right",
    "l": "a", "k": "b",
}
# ...plus the non-letters, which are not in EXPECTED because the uppercase test
# below folds case and they have none.
EXPECTED_CODES = {0x0D: "run", 0x20: "a"}
# SPACE is the one deliberate alias: `a` is reachable as L *and* as space,
# because a kid arrives already knowing space = jump.
ALIASES = {0x20: "a"}


def test_the_button_scheme_is_wasd_plus_l_and_k():
    """Left thumb steers, right thumb fires (owner call, 2026-08-14).

    Spelled out rather than derived, because this is a HUMAN decision about a
    physical keyboard -- Z/X were rejected for sitting on the bottom row under
    the same thumb as WASD. Nothing in the code can re-derive that, so if it
    changes, it changes here first and deliberately.
    """
    KEY_BUTTON = _input_module().KEY_BUTTON
    for ch, button in EXPECTED.items():
        assert KEY_BUTTON.get(ord(ch)) == button, ch
    for code, button in EXPECTED_CODES.items():
        assert KEY_BUTTON.get(code) == button, hex(code)
    # One key per button, and EXACTLY ONE alias. The table is small enough to
    # say so exactly, and saying so exactly is what stops the next convenience
    # alias landing quietly -- every one of them is somebody's habit, so the
    # only way the map stays small is if adding to it has to argue here first.
    assert len(KEY_BUTTON) == len(EXPECTED) + len(EXPECTED_CODES) + 1, \
        sorted(KEY_BUTTON.items())          # +1 = ESC/stop, ASCII path only
    seen = {}
    for code, button in KEY_BUTTON.items():
        if button in seen:
            pair = {code, seen[button]}
            assert pair & set(ALIASES), \
                "undeclared alias: %r and %r both mean %r" % (
                    chr(min(pair)), chr(max(pair)), button)
        seen[button] = code


def test_the_keys_that_deliberately_stopped_being_buttons():
    """A clean break: one answer to "which key jumps", not five.

    Z/X were A/B; space and enter were also A; hjkl was a d-pad. All of them are
    ordinary letters now -- which is the point, because a typing cart (Letter
    Blitz) reads them via key() and a letter that ALSO moves the player is a
    stolen letter. Asserting their ABSENCE is the only way this stays true: an
    alias is exactly the kind of thing that gets added back as a kindness.
    """
    KEY_BUTTON = _input_module().KEY_BUTTON
    for ch in ("z", "x", "h", "j", "q", "e", "r"):
        assert ord(ch) not in KEY_BUTTON, "%r fires a button again" % ch
    # `r` is on that list because ENTER took `run` -- one key per button, so R
    # goes back to being a letter a typing cart can read.
    assert KEY_BUTTON.get(0x0D) == "run"
    # SPACE is NOT on that list: it kept `a`, deliberately (see ALIASES).
    assert KEY_BUTTON.get(0x20) == "a"


def test_enter_confirms_in_the_launcher_the_way_r_does():
    """The launcher opens on `pressed("a") or pressed("run")`, so ENTER opening
    a cart is not a special case anywhere -- it falls out of ENTER being `run`.

    Pinned because the clean break briefly took ENTER out of the button table
    entirely, which silently removed "press enter to open" from a menu. It was
    the right removal of the wrong thing: ENTER should not be a jump button, but
    a menu still needs a confirm key.
    """
    mod = _input_module()
    kbd = mod.TDeckKeyboard.__new__(mod.TDeckKeyboard)
    assert kbd._buttons_for_key(0x0D) == ("run",)
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code == 0x0D:
            data[idx] |= bit
    assert mod.decode_raw(data) == (("run",), 0x0D)


# -- the two decoders agree --------------------------------------------------

def test_both_decoders_give_the_same_button_for_every_key():
    """The drift check, and the reason `decode_raw` is a pure function.

    Feed each raw matrix bit; feed the same byte as typed ASCII; require the
    same answer. Before the split the raw decode lived inside a method full of
    I2C and fallback state, so it could only be exercised on hardware -- which
    is how the two paths were free to disagree about `hjkl` for months.
    """
    mod = _input_module()
    kbd = mod.TDeckKeyboard.__new__(mod.TDeckKeyboard)   # no I2C needed
    for idx, bit, code in mod.RAW_KEYS:
        data = bytearray(5)
        data[idx] = bit
        want = mod.KEY_BUTTON.get(code)
        expect = (want,) if want else ()
        assert kbd._buttons_for_key(code) == expect, (code, "ascii")
        assert mod.decode_raw(data) == (expect, code), (code, "raw")


def test_two_keys_at_once_come_back_as_two_buttons():
    """Raw mode's whole reason to exist is simultaneous held keys -- walk and
    fire. The ASCII path physically cannot do this (one byte per read), so it
    is the raw decoder alone that has to get it right."""
    mod = _input_module()
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code in (ord("d"), ord("l")):
            data[idx] |= bit
    buttons, _key = mod.decode_raw(data)
    assert set(buttons) == {"right", "a"}, buttons


def test_backspace_wins_the_key_slot_over_a_held_letter():
    """Only one `key` fits in the report. BACKSPACE is a plain key, and it is
    the last match in RAW_KEYS, so it beats a letter held with it -- a game
    that reads key() sees the delete, and `up` is still held."""
    mod = _input_module()
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code in (ord("w"), 0x08):
            data[idx] |= bit
    buttons, key = mod.decode_raw(data)
    assert key == 0x08
    assert set(buttons) == {"up"}


def test_backspace_is_a_plain_key_on_every_path():
    """BACKSPACE stopped being `home` (owner call, 2026-10-18): no button on the
    ASCII path, the matrix or a BLE keyboard, and no hold gesture anywhere reads
    it. A cart reads it with key() as 0x08."""
    mod = _input_module()
    kbd = mod.TDeckKeyboard.__new__(mod.TDeckKeyboard)
    assert kbd._buttons_for_key(0x08) == ()
    assert 0x08 not in mod.KEY_BUTTON
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code == 0x08:
            data[idx] |= bit
    assert mod.decode_raw(data) == ((), 0x08)
    from runtime import moy_input as mi
    assert mi.hid_buttons_for_key(0x08) == ()


def test_mic_is_the_console_key_in_both_decoders():
    """Mic -> `start`, the console's key, and nothing else (owner call,
    2026-10-18): the raw matrix's d0 bit 6, and the ASCII mode's one spelling
    of it, Shift+Mic = 0xE0 (the C3's NULL entry minus 32). It takes no key
    slot, so a held letter keeps it."""
    mod = _input_module()
    kbd = mod.TDeckKeyboard.__new__(mod.TDeckKeyboard)
    assert mod.decode_raw(bytes([0x40, 0, 0, 0, 0])) == (("start",), 0)
    buttons, key = mod.decode_raw(bytes([0x40 | 0x02, 0, 0, 0, 0]))
    assert set(buttons) == {"up", "start"} and key == ord("w")
    assert kbd._buttons_for_key(0xE0) == ("start",)
    # no other byte the C3 can send is the console's key
    for code in range(256):
        if code != 0xE0:
            assert "start" not in kbd._buttons_for_key(code), hex(code)
    # and no matrix bit but Mic's
    for idx in range(5):
        for bit in range(7):
            data = bytearray(5)
            data[idx] = 1 << bit
            assert ("start" in mod.decode_raw(data)[0]) == (idx == 0 and bit == 6)


@pytest.mark.skipif(not VENDOR_KBD.exists(),
                    reason="vendor keyboard reference not checked out")
def test_the_mic_position_and_its_ascii_byte_are_the_vendor_firmwares():
    """The facts the Mic decode rests on, read from the vendor firmware: d0
    bit 6 is `keyboard[0][6]`, whose plain entry is NULL (so a bare Mic sends
    the idle byte 0x00 and cannot be seen in ASCII mode), and a Shift is
    `toPrint - 32`, which is 0xE0 for it."""
    src = VENDOR_KBD.read_text(encoding="utf-8", errors="replace")
    assert re.search(r"keyboard\[0\]\[6\]\s*=\s*NULL;\s*//\s*Mic", src)
    assert "toPrint - 32" in src
    assert (0 - 32) & 0xFF == 0xE0
    mod = _input_module()
    assert mod.decode_raw(bytes([1 << 6, 0, 0, 0, 0]))[0] == ("start",)


def test_uppercase_types_the_same_button_as_lowercase():
    """Shift+D must still be `right`. The old table listed both cases by hand
    for every key; one missing pair would be silent."""
    mod = _input_module()
    kbd = mod.TDeckKeyboard.__new__(mod.TDeckKeyboard)
    for ch, button in EXPECTED.items():
        assert kbd._buttons_for_key(ord(ch.upper())) == (button,), ch


# -- the raw bits are the vendor firmware's ---------------------------------

@pytest.mark.skipif(not VENDOR_KBD.exists(),
                    reason="vendor keyboard reference not checked out")
def test_every_raw_bit_is_the_vendor_matrix_position_for_that_key():
    """`d4 & 0x40` is unreadable and unverifiable by eye; this reads the real
    `keyboard[col][row]` table and checks each entry.

    Worth having because the cost of one wrong bit is a key that does the wrong
    thing on hardware and nowhere else -- exactly the shape of the d-pad bug
    found the same day, which no host test could see.
    """
    mod = _input_module()
    matrix = _vendor_matrix()
    assert matrix, "parsed no keys out of the vendor firmware"
    for idx, bit, code in mod.RAW_KEYS:
        if code in (0x08, 0x0D):
            continue                    # Backspace/Enter are NULL in that table
        row = bit.bit_length() - 1
        assert bin(bit).count("1") == 1, (idx, bit)
        assert matrix.get((idx, row)) == chr(code), (
            "RAW_KEYS says d%d bit %d is %r; vendor matrix says %r"
            % (idx, row, chr(code), matrix.get((idx, row))))


def test_the_p4_ble_keyboard_is_an_arrow_host_too():
    """A BLE keyboard has arrows, so the P4 gets arrows + Z/X, not the T-Deck's
    L/K. Its table said it was "shared with the T-Deck keyboard's game-button
    mapping" and was a fourth hand-written copy -- still carrying hjkl as a
    d-pad and R as `run` after the others had moved on."""
    from runtime import moy_input as mi
    for key, button in ((ord("z"), "a"), (ord("x"), "b"), (ord(" "), "a"),
                        (0x0D, "run")):
        assert mi.hid_buttons_for_key(key) == (button,), hex(key)
    assert mi.hid_buttons_for_key(0x08) == (), "Backspace is a plain key on the P4 too"
    for gone in "lkhjr":
        assert mi.hid_buttons_for_key(ord(gone)) == (), "%s fires a button on the P4" % gone
    # the HID arrow usages are what make it an arrow host in the first place
    src = (ROOT / "native" / "moy_input" / "moy_hid.c").read_text(encoding="utf-8")
    direct = src[src.index("static uint32_t direct_button("):]
    direct = direct[:direct.index("\n}\n")]
    for want in ("0x4F: return B_RIGHT", "0x50: return B_LEFT", "0x51: return B_DOWN",
                 "0x52: return B_UP"):
        assert want in direct, want


def test_the_two_arrow_key_hosts_share_one_table():
    """A host WITH arrow keys uses arrows + Z/X, and both of them use ONE
    table: the pygame sim and the browser page send keys as codes and
    modifiers, and ConsoleDriver.key_down decides what each presses.

    They had drifted twice: the page did arrows-as-d-pad + Z/X while the sim
    steered a trackball, and both sent WASD as the d-pad on every surface, so
    S in the sprite tab shifted the sprite as it selected.
    """
    from runtime import host_api, moy_input as mi
    sim = (ROOT / "tools" / "simulate_desktop.py").read_text(encoding="utf-8")
    page = (ROOT / "firmware" / "web_runner"
            / "page_core.html").read_text(encoding="utf-8")
    assert "driver.key_down" in sim and "driver.key_up" in sim
    assert 'type:"kd"' in page and 'type:"ku"' in page
    for gone in ("NAV={", "SC={", "shortcuts = {", "nav_keys"):
        assert gone not in sim and gone not in page, gone

    assert host_api.DESK_KEYS == {mi.KEY_LEFT: "left", mi.KEY_RIGHT: "right",
                                  mi.KEY_UP: "up", mi.KEY_DOWN: "down", 0x0D: "run"}
    cart = host_api.CART_KEYS
    assert cart[ord("z")] == "a" and cart[ord("x")] == "b"
    assert cart[ord(" ")] == "a", "space = jump is every tier's"
    assert ord("r") not in cart, "R stopped being `run` on 2026-08-14"
    # ...and the BLE keyboard's twin agrees, key for key.
    for k in range(0x20, 0x7F):
        want = cart.get(k | 0x20 if 0x41 <= k <= 0x5A else k)
        assert mi.hid_buttons_for_key(k, cart=True) == ((want,) if want else ()), chr(k)
        assert mi.hid_buttons_for_key(k, cart=False) == (), chr(k)


def test_the_device_and_the_arrow_hosts_differ_ON_PURPOSE():
    """A/B are L/K on the T-Deck and Z/X on a host, and that is not a bug.

    Recorded as a test because it looks exactly like one. The T-Deck has NO
    arrow keys (see the vendor matrix) so it needs WASD, and its Z/X are
    bottom-row keys under the same thumb WASD wants -- while on a desktop
    keyboard arrows + Z/X is what every emulator and PICO-8 itself uses. Same
    console, two keyboards, two ergonomics. The shared part is the BUTTON set,
    and `space` = jump and `Enter` = run everywhere.
    """
    from runtime import host_api
    KEY_BUTTON = _input_module().KEY_BUTTON
    assert KEY_BUTTON[ord("l")] == "a" and KEY_BUTTON[ord("k")] == "b"
    assert ord("z") not in KEY_BUTTON and ord("x") not in KEY_BUTTON
    assert ord("l") not in host_api.CART_KEYS and ord("k") not in host_api.CART_KEYS

    # ...and what the two DO share.
    assert KEY_BUTTON[0x0D] == "run" and KEY_BUTTON[0x20] == "a"


# -- the matrix types what the ASCII mode types ------------------------------

def _chord(mod, *codes, sym=False, shift=False):
    """The five matrix bytes for these plain keys held, with Sym and Shift."""
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code in codes:
            data[idx] |= bit
    if sym:
        data[0] |= 0x04
    if shift:
        data[1] |= 0x40
    return data


def test_the_homes_verb_keys_arrive_in_raw_mode():
    """The T-Deck home runs the keyboard as the raw matrix (`_set_text_mode`
    puts every surface but a text one there, so Mic, the console's key, and a
    held direction both read). Every key the home's strip names must
    therefore be one the matrix decoder delivers; a key outside RAW_KEYS is a
    hint that does nothing on the one keyboard the strip is drawn for. Search's
    `/` is Sym+G, the chord printed on that key."""
    from runtime import launcher_layer as ll
    mod = _input_module()
    keys = {ord(ch) for ch in ll._VERB_KEY} | {ord(ll._FAVOURITE_KEY), ord("q"), ord("e")}
    for cap, _words in ll._VERB_HINT.values():
        if len(cap) == 1:
            keys.add(ord(cap.lower()))
    plain = {code for _i, _b, code in mod.RAW_KEYS}
    for k in sorted(keys):
        if k in plain:
            assert mod.decode_raw(_chord(mod, k))[1] == k, chr(k)
        else:
            hits = [mod.decode_raw(_chord(mod, code, sym=True))[1]
                    for code in plain if code not in (0x08, 0x0D, 0x20)]
            assert k in hits, "%r reaches the home in neither the plain nor the Sym layer" % chr(k)
    assert mod.decode_raw(_chord(mod, ord("g"), sym=True)) == ((), ord("/"))


def test_shift_and_sym_type_without_moving_the_player():
    """Shift folds a letter to upper case, as the C3 does, and keeps its
    button (Shift+D is still `right`); Sym gives the key its symbol, which is
    no button -- Sym+W types '1' and is not `up`, as in the ASCII mode."""
    mod = _input_module()
    assert mod.decode_raw(_chord(mod, ord("d"), shift=True)) == (("right",), ord("D"))
    assert mod.decode_raw(_chord(mod, ord("w"), sym=True)) == ((), ord("1"))
    assert mod.decode_raw(_chord(mod, ord("c"))) == ((), ord("c"))


def _home(tmp_path):
    from runtime import host_app
    from ws_helpers import build_ws_with_shelf
    ws = build_ws_with_shelf(tmp_path, 6)
    drv = host_app.ConsoleDriver(ws)
    drv.frame(1 / 30)
    return ws, drv


def _matrix_press(ws, drv, data):
    """One frame of the T-Deck's keyboard in raw mode (the C3's five bytes ->
    moy_kbd.c -> a board's table) into the console's router, then two plain
    frames for what the verb deferred."""
    from test_system_key import _tdeck_frame
    _tdeck_frame(ws, bytes(data))
    drv.frame(1 / 30)
    drv.frame(1 / 30)


def _real_card(ws):
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    return ws.launcher.selected()


def test_the_tdeck_home_acts_on_letters_from_the_matrix(tmp_path):
    """Design review R3-02, end to end on the host: the home's verb keys
    arrive through the board's own keyboard driver in raw mode, the mode the
    home runs in, and act -- F stars the card, Sym+G opens Search, C
    changes it (the Editor); on Make, Y copies and X asks before it deletes."""
    mod = _input_module()
    ws, drv = _home(tmp_path)
    assert ws.launcher.tab == "games"
    cart = _real_card(ws)
    _matrix_press(ws, drv, _chord(mod, ord("f")))
    assert ws.carts.is_favorite(cart) is True
    _matrix_press(ws, drv, _chord(mod, ord("f")))       # F again: the star comes off
    assert ws.carts.is_favorite(cart) is False
    _matrix_press(ws, drv, _chord(mod, ord("g"), sym=True))
    assert ws.search_typing
    ws.close_search(clear=True)
    drv.frame(1 / 30)
    _real_card(ws)
    _matrix_press(ws, drv, _chord(mod, ord("c")))
    assert ws.screen == "menu", "C on a Games card is Change: the Editor"
    ws.go_home()
    drv.frame(1 / 30)
    ws.set_home_tab("make")
    _real_card(ws)
    n = len(ws.carts.all)
    _matrix_press(ws, drv, _chord(mod, ord("y")))
    assert len(ws.carts.all) == n + 1, "Y on Make is Copy"
    _real_card(ws)
    _matrix_press(ws, drv, _chord(mod, ord("x")))
    assert ws.confirm.open, "X on Make asks first (tests/test_delete_confirm.py)"
    assert len(ws.carts.all) == n + 1


def test_the_console_puts_the_keyboard_on_the_matrix_at_boot(tmp_path):
    """The C3 boots in ASCII, where a bare Mic sends nothing; the console
    boots on the home, so attaching the keyboard asks for the matrix and the
    console's key reads from the first frame, not only after a game."""
    from test_tdeck_input import _frame, _keyboard
    from ws_helpers import build_ws
    ws = build_ws(tmp_path)
    table, kbd, fake = _keyboard(bytes(5))
    assert not kbd.raw_mode
    ws.keyboard = kbd
    ws.sync_keyboard_mode()
    _frame(kbd, table)
    assert kbd.raw_mode and b"\x03" in fake.writes


@pytest.mark.skipif(not VENDOR_KBD.exists(),
                    reason="vendor keyboard reference not checked out")
def test_the_matrix_carries_every_key_and_symbol_the_ascii_mode_types():
    """Every position the vendor firmware types a byte for is in RAW_KEYS, and
    its Sym entry is the vendor's `keyboard_symbol` -- so a raw-mode surface
    (the home, a game) reads the same bytes a text surface does."""
    mod = _input_module()
    src = VENDOR_KBD.read_text(encoding="utf-8", errors="replace")
    syms = {}
    for col, row, ch in re.findall(
            r"keyboard_symbol\[(\d+)\]\[(\d+)\]\s*=\s*'(\\?.)'", src):
        syms[(int(col), int(row))] = ch[-1]
    plain = _vendor_matrix()
    ours = {(idx, bit.bit_length() - 1): code for idx, bit, code in mod.RAW_KEYS}
    for pos, ch in plain.items():
        assert ours.get(pos) == ord(ch), (pos, ch)
    for (idx, row), ch in syms.items():
        if (idx, row) == (0, 6):
            continue                    # Sym+Mic: Mic is the console's button
        assert mod.RAW_SYMS.get((idx, 1 << row)) == ord(ch), ((idx, row), ch)
