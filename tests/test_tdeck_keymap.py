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
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
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
    "l": "a", "k": "b",
}
# ...plus the non-letters, which are not in EXPECTED because the uppercase test
# below folds case and they have none.
EXPECTED_CODES = {0x0D: "run", 0x08: "home", 0x20: "a"}
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


def test_the_p4_ble_keyboard_is_an_arrow_host_too():
    """A BLE keyboard has arrows, so the P4 gets arrows + Z/X, not the T-Deck's
    L/K. Its table said it was "shared with the T-Deck keyboard's game-button
    mapping" and was a fourth hand-written copy -- still carrying hjkl as a
    d-pad and R as `run` after the others had moved on."""
    src = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"
           / "p4_ble_keyboard.py").read_text(encoding="utf-8")
    tbl = re.search(r"BUTTON_FOR_KEY = \{(.+?)\n\}", src, re.S).group(1)
    for want in ('ord("z"): "a"', 'ord("x"): "b"', 'ord(" "): "a"',
                 '0x0D: "run"', '0x08: "home"'):
        assert want in tbl, want
    for gone in ('ord("l")', 'ord("k")', 'ord("h")', 'ord("j")', 'ord("r")'):
        assert gone not in tbl, "%s still fires a button on the P4" % gone
    # the HID arrow usages are what make it an arrow host in the first place
    direct = re.search(r"_DIRECT_BUTTON = \{(.+?)\n\}", src, re.S).group(1)
    for want in ('0x4F: "right"', '0x50: "left"',
                 '0x51: "down"', '0x52: "up"'):
        assert want in direct, want


def test_the_two_arrow_key_hosts_agree_with_each_other():
    """A host WITH arrow keys uses arrows + Z/X. Both of them must.

    The pygame sim and the browser page are one tier wearing two coats, and they
    had drifted the other way from the device: the page has done arrows-as-d-pad
    + Z/X since 2026-07-31 while the sim still steered a trackball with the
    arrows and had no d-pad at all. Each file was self-consistent, which is
    exactly why neither could notice.
    """
    sim = (ROOT / "tools" / "simulate_desktop.py").read_text(encoding="utf-8")
    page = (ROOT / "firmware" / "web_runner"
            / "page_core.html").read_text(encoding="utf-8")

    shortcuts = re.search(r"shortcuts = \{(.+?)\}", sim, re.S).group(1)
    assert "K_z: \"a\"" in shortcuts and "K_x: \"b\"" in shortcuts
    assert "K_RETURN: \"run\"" in shortcuts
    assert "K_SPACE: \"a\"" in shortcuts, "space = jump is every tier's"
    assert "K_r:" not in shortcuts, "R stopped being `run` on 2026-08-14"

    sc = re.search(r"SC=\{(.+?)\}", page).group(1)
    for want in ('z:"a"', 'x:"b"', 'Enter:"run"', '" ":"a"'):
        assert want in sc, (want, sc)

    arrows = re.search(r"arrow_keys = \{(.+?)\}", sim, re.S).group(1)
    for key, button in (("K_LEFT", "left"), ("K_RIGHT", "right"),
                        ("K_UP", "up"), ("K_DOWN", "down")):
        assert key in arrows and button in arrows
    assert "AN={ArrowLeft:\"left\"" in page


def test_the_device_and_the_arrow_hosts_differ_ON_PURPOSE():
    """A/B are L/K on the T-Deck and Z/X on a host, and that is not a bug.

    Recorded as a test because it looks exactly like one. The T-Deck has NO
    arrow keys (see the vendor matrix) so it needs WASD, and its Z/X are
    bottom-row keys under the same thumb WASD wants -- while on a desktop
    keyboard arrows + Z/X is what every emulator and PICO-8 itself uses. Same
    console, two keyboards, two ergonomics. The shared part is the BUTTON set,
    and `space` = jump and `Enter` = run everywhere.
    """
    KEY_BUTTON = _input_module().KEY_BUTTON
    assert KEY_BUTTON[ord("l")] == "a" and KEY_BUTTON[ord("k")] == "b"
    assert ord("z") not in KEY_BUTTON and ord("x") not in KEY_BUTTON

    sim = (ROOT / "tools" / "simulate_desktop.py").read_text(encoding="utf-8")
    shortcuts = re.search(r"shortcuts = \{(.+?)\}", sim, re.S).group(1)
    assert "K_l:" not in shortcuts and "K_k:" not in shortcuts

    # ...and what the two DO share.
    assert KEY_BUTTON[0x0D] == "run" and KEY_BUTTON[0x20] == "a"
