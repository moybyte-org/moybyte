"""print() carries BYTES, all the way down (moy SPEC.md 6).

A Lua string is a byte string and may hold anything; a MicroPython str must be
valid UTF-8. Every layer between a cart and a pixel has to agree on which of
those it is holding, and for a long time they did not: the device's C text
kernel walked bytes while the host font walked a decoded str, so the two tiers
put the same cart's text in different places the moment it left ASCII. A cart
doing print("\\255") did not even get that far -- it lost the whole frame to a
UnicodeError at the Lua->Python boundary.

The rule, once, so the layers can be checked against it: one 8px cell per BYTE.
A two-byte UTF-8 character occupies two cells. Bytes with no glyph draw nothing
and still advance.

Found by moy-spec's conformance suite (its text_bytes scene), which is the only
thing that had ever printed a byte past ASCII.

There used to be a third tier here: the WIRE, where non-ASCII crossed as a list
of byte values because JSON cannot hold 0xFF inside a string and the browser's
replayer would have counted codepoints where the device counts bytes. That wire
is gone -- at moycore stage 4 the wasm head started rasterizing with the same
kernel the device uses, so text reaches the browser as pixels and there is no
encoding left to disagree about.
"""

import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import font as host_font  # noqa: E402


def cells(s):
    """How many 8px cells print() spends on `s` -- one per BYTE (SPEC.md 6)."""
    return len(host_font.as_bytes(s))


def _device_canvas_module():
    """device_canvas.py's module-level helpers, loaded without the firmware
    tree's import environment (they depend on nothing but builtins)."""
    path = ROOT / "device/device_canvas.py"
    src = path.read_text(encoding="utf-8")
    # Take just the two helpers, by their exact extent: importing the module
    # needs the device's framebuf / moy_gfx / device_util, none of which exist
    # under CPython, and the parity suite already covers the class itself.
    start = src.index("def _text_bytes(")
    tail = "return out.decode()"
    end = src.index(tail, start) + len(tail)
    ns = {}
    exec(compile(src[start:end], str(path), "exec"), ns)
    assert "_text_bytes" in ns and "_fb_text" in ns
    return ns


DEV = _device_canvas_module()


# --- the rule -------------------------------------------------------------

def test_ascii_is_one_cell_per_character():
    assert host_font.as_bytes("AB") == b"AB"
    assert len(host_font.as_bytes("hello")) == 5


def test_str_is_utf8_encoded_not_one_byte_per_character():
    # "cafe" + U+00E9. Two bytes, so TWO cells -- reading the str one byte per
    # character would give one, and the device (which is handed the str's
    # buffer, i.e. its UTF-8) would draw two. That disagreement is the bug.
    assert host_font.as_bytes("café") == b"caf\xc3\xa9"
    assert cells("café") == 5


def test_bytes_pass_through_untouched():
    # What moy_lua hands back for a Lua string that is not valid UTF-8. Three
    # bytes, three cells; encoding it would make four.
    assert host_font.as_bytes(b"G\xffH") == b"G\xffH"
    assert cells(b"G\xffH") == 3


def test_bytes_without_a_glyph_draw_nothing_but_still_advance():
    drawn = []
    host_font.draw(lambda px, py: drawn.append((px, py)), b"\xff", 0, 0)
    assert drawn == []
    assert cells(b"\xff") == 1


# --- the device -----------------------------------------------------------

def test_device_takes_the_same_bytes_as_the_host():
    for s in ("hi", "café", b"G\xffH"):
        assert bytes(DEV["_text_bytes"](s)) == bytes(host_font.as_bytes(s))


def test_device_bytes_helper_does_not_stringify_a_bytes_object():
    # The regression this file exists for: str(b"G\xffH") is "b'G\\xffH'", which
    # would have DRAWN that literal -- eight visible characters where the cart
    # asked for three.
    assert bytes(DEV["_text_bytes"](b"G\xffH")) == b"G\xffH"


def test_framebuf_fallback_keeps_the_cell_count():
    # framebuf.text needs a str and no str holds 0xFF. Bytes with no glyph draw
    # nothing in the native path anyway, so the fallback maps them to a SPACE:
    # same pixels, same advance, cursor stays in step.
    assert DEV["_fb_text"](b"G\xffH") == "G H"
    assert DEV["_fb_text"](b"\x00\x1fA") == "  A"
    assert len(DEV["_fb_text"]("café")) == 5


def test_framebuf_fallback_leaves_printable_ascii_alone():
    assert DEV["_fb_text"]("Score: 100") == "Score: 100"
    assert DEV["_fb_text"](b"\x7f") == "\x7f"      # 0x7F has a glyph
