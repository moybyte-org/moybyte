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

(test_micropython_spike has covered a few individual raw keys since #71 -- the
backspace/q/e trio -- so the map was not untested; what was missing is a check
that the two decoders say the same thing, which is the failure that actually
happened.)

Both now resolve through one `KEY_BUTTON` table, so what is worth pinning is
(a) that the table is what the owner chose, (b) that the raw bit positions are
the vendor firmware's, and (c) that the two paths produce the same answer for
every key.
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_micropython"
VENDOR_KBD = (ROOT / "firmware" / "lilygo_t_deck_plus_reference" / "examples"
              / "Keyboard_ESP32C3" / "Keyboard_ESP32C3.ino")


def _input_module():
    path = TDECK / "modules" / "moybyte" / "input.py"
    spec = importlib.util.spec_from_file_location("_tdeck_input_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
    "l": "a", "k": "b", "r": "run",
}


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
    assert KEY_BUTTON.get(0x08) == "home", "BACKSPACE is THE console key"


def test_the_keys_that_deliberately_stopped_being_buttons():
    """A clean break: one answer to "which key jumps", not five.

    Z/X were A/B; space and enter were also A; hjkl was a d-pad. All of them are
    ordinary letters now -- which is the point, because a typing cart (Letter
    Blitz) reads them via key() and a letter that ALSO moves the player is a
    stolen letter. Asserting their ABSENCE is the only way this stays true: an
    alias is exactly the kind of thing that gets added back as a kindness.
    """
    KEY_BUTTON = _input_module().KEY_BUTTON
    for ch in ("z", "x", "h", "j", "q", "e", " "):
        assert ord(ch) not in KEY_BUTTON, "%r fires a button again" % ch
    assert 0x0D not in KEY_BUTTON, "enter fires a button again"


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
    """Only one `key` fits in the report, and BACKSPACE is THE console key in
    every input mode -- so a hold-to-exit must not be swallowed by a direction
    the kid is still leaning on. That is what RAW_KEYS' order buys, and order
    is invisible unless something asserts it."""
    mod = _input_module()
    data = bytearray(5)
    for idx, bit, code in mod.RAW_KEYS:
        if code in (ord("w"), 0x08):
            data[idx] |= bit
    buttons, key = mod.decode_raw(data)
    assert key == 0x08
    assert set(buttons) == {"up", "home"}


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


def test_the_host_simulator_uses_the_same_action_keys():
    """A kid who learns the keys in the sim must find them there on glass.

    The sim had Z/X long after the board did; the two are edited in different
    files and only a human notices.
    """
    src = (ROOT / "tools" / "simulate_desktop.py").read_text(encoding="utf-8")
    assert "pygame.K_l: \"a\"" in src and "pygame.K_k: \"b\"" in src
    assert "pygame.K_z: \"a\"" not in src and "pygame.K_x: \"b\"" not in src
    nav = re.search(r"nav_keys = \{(.+?)\}", src, re.S).group(1)
    for key, button in (("K_a", "left"), ("K_d", "right"),
                        ("K_w", "up"), ("K_s", "down")):
        assert key in nav and button in nav
