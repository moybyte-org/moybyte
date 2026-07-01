"""Tests for the offline PICO-8 .p8 -> .moy asset importer (tools/import_p8.py).

Feeds a small hand-written synthetic .p8 (a few __gfx__ rows + a tiny __sfx__ +
one __music__ row) and asserts:
  * the emitted sprites.moygfx nibbles match the input __gfx__ (round-trip stable),
  * sounds.json parses via runtime.audio.AudioBank.from_dict and is lossy-correct,
  * manifest.json is valid and well-shaped,
  * main.py keeps the Lua only as a comment (never executable),
  * the cart load()s cleanly via runtime.moy_carts.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import import_p8  # noqa: E402
from runtime import moy_carts  # noqa: E402
from runtime.audio import AudioBank  # noqa: E402
from runtime.editors import SpriteSheet  # noqa: E402


# A synthetic .p8: 3 distinct __gfx__ rows (the rest blank), one __sfx__ line
# (3 audible notes then silence), and a single __music__ row pointing at sfx 0.
# __gfx__ rows use a handful of palette indices 0-15.
GFX_ROW0 = "0123456789abcdef" + "0" * 112      # first 16 px are a palette ramp
GFX_ROW1 = "f0f0f0f0" + "0" * 120              # checker
GFX_ROW2 = "8" * 8 + "0" * 120                 # 8 red px

# __sfx__ line layout: [mode:2][duration:2][loopstart:2][loopend:2] + 32 notes*5.
# duration 0x10 (=16 ticks/row) -> speed = round(120/16) = 8.
# notes: pitch=0x1E inst=0 vol=6 eff=0 ; pitch=0x21 inst=3 vol=5 eff=0 ;
#        pitch=0x18 inst=6 vol=7 eff=0 ; then all rest (vol 0).
_HEADER = "00" + "10" + "00" + "00"
_NOTE_A = "1e" + "0" + "6" + "0"     # pitch 30, instrument 0 (triangle->wave1), vol 6
_NOTE_B = "21" + "3" + "5" + "0"     # pitch 33, instrument 3 (square->wave0),  vol 5
_NOTE_C = "18" + "6" + "7" + "0"     # pitch 24, instrument 6 (noise->wave3),   vol 7
_REST = "00000"
SFX_LINE = _HEADER + _NOTE_A + _NOTE_B + _NOTE_C + _REST * 29

# __music__ row, in PICO-8's real on-disk form `<flags> <ch0ch1ch2ch3>`:
# flags=00, then ch0=sfx0 (00), ch1..3 off (0x41/0x42/0x43 -> bit6 set).
MUSIC_LINE = "00 " + "00" + "41" + "42" + "43"

SYNTHETIC_P8 = "\n".join([
    "pico-8 cartridge // http://www.pico-8.com",
    "version 41",
    "__lua__",
    "-- my test cart",
    "-- by tester",
    "function _draw()",
    " cls(0)",
    " if btn(0) then x -= 1 end",
    " rectfill(x, 60, x+8, 68, 8)",
    " circ(64, 64, 10, 7)",
    " spr(0,10,10)",
    "end",
    "__gfx__",
    GFX_ROW0,
    GFX_ROW1,
    GFX_ROW2,
    "__gff__",
    "00010000000000000000000000000000",
    "__map__",
    "0102030400000000",
    "__sfx__",
    SFX_LINE,
    "__music__",
    MUSIC_LINE,
    "",
]) + "\n"


def _write_p8(tmp_path):
    p8 = tmp_path / "my_test_cart.p8"
    p8.write_text(SYNTHETIC_P8, encoding="utf-8")
    return p8


def test_gfx_nibbles_match_input(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    kgfx = (out / "sprites.moygfx").read_text(encoding="utf-8")
    rows = kgfx.split("\n")
    # Exactly the full 128x128 grid.
    assert len(rows) == 128
    assert all(len(r) == 128 for r in rows)
    # The three authored rows survive byte-for-byte (palette is identical).
    assert rows[0] == GFX_ROW0
    assert rows[1] == GFX_ROW1
    assert rows[2] == GFX_ROW2
    # Everything after is blank padding.
    assert all(r == "0" * 128 for r in rows[3:])


def test_gfx_roundtrip_stable(tmp_path):
    """parse -> kgfx -> SpriteSheet.from_hex -> to_hex is a fixed point."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    kgfx = (out / "sprites.moygfx").read_text(encoding="utf-8")
    sheet = SpriteSheet.from_hex(kgfx, cols=16, rows=16)
    assert sheet.to_hex() == kgfx


def test_sounds_parse_via_audiobank(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    data = json.loads((out / "sounds.json").read_text(encoding="utf-8"))
    bank = AudioBank.from_dict(data)            # must not raise
    assert len(bank.sfx) >= 1
    sfx0 = bank.sfx[0]
    # speed: duration 0x10 -> round(120/16) == 8
    assert sfx0.speed == 8
    # the 3 authored notes survive (trailing rests trimmed)
    assert len(sfx0.steps) == 3
    # pitch maps 1:1 (PICO-8 pitch == Moybyte semitone index)
    assert sfx0.steps[0][0] == 0x1E
    assert sfx0.steps[1][0] == 0x21
    assert sfx0.steps[2][0] == 0x18
    # instruments folded to waveforms: tri->1, square->0, noise->3
    assert sfx0.steps[0][1] == 1
    assert sfx0.steps[1][1] == 0
    assert sfx0.steps[2][1] == 3
    # volumes preserved
    assert [s[2] for s in sfx0.steps] == [6, 5, 7]
    # one music track, flattened to channel 0's sfx id (0)
    assert len(bank.music) == 1
    assert bank.music[0].pattern == [0]
    assert bank.music[0].loop is True


def test_manifest_valid(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    summary = import_p8.import_p8(str(p8), str(out))

    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["format"] == "moybyte-cart-v1"
    assert man["type"] == "game"
    assert man["main"] == "main.py"
    assert man["canvas"] == {"width": 320, "height": 240, "palette": "moy64"}
    assert "graphics" in man["permissions"]
    assert man["config"] == {}
    assert man["edit"] == []
    # title comes from the first real lua comment line
    assert summary["title"] == "my test cart"
    assert man["title"] == "my test cart"


def test_main_py_keeps_lua_as_comment_only(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    src = (out / "main.py").read_text(encoding="utf-8")
    # the stub is real, runnable Python
    compile(src, "main.py", "exec")
    assert "def _draw():" in src
    # every original lua body line is present but commented out
    assert "# function _draw()" in src
    assert "# end" in src
    # no bare (uncommented) lua leaked into executable scope
    for line in src.split("\n"):
        if "function _draw()" in line or line.strip() == "end":
            assert line.lstrip().startswith("#")


def test_cart_loads_via_moy_carts(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    cart = moy_carts.load(str(out))
    assert cart is not None
    assert cart["title"] == "my test cart"
    assert cart["type"] == "game"
    assert cart["sprites"] is not None
    assert cart["sounds"] is not None
    # the loaded sprite hex is the same grid we emitted
    assert cart["sprites"].split("\n")[0] == GFX_ROW0
    # the loaded sounds round-trip through the audio model
    AudioBank.from_dict(cart["sounds"])


def test_deferred_sections_noted_not_imported(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    summary = import_p8.import_p8(str(p8), str(out))

    # __map__ and __gff__ are reported as deferred, and no files are written.
    deferred = " ".join(summary["deferred"])
    assert "__map__" in deferred
    assert "__gff__" in deferred
    assert not (out / "map.moymap").exists()
    # the cart folder holds exactly the v1 importer outputs
    names = sorted(p.name for p in out.iterdir())
    assert names == ["config.json", "main.py", "manifest.json",
                     "sounds.json", "sprites.moygfx"]


def test_empty_sections_handled(tmp_path):
    """A .p8 with no assets still produces a loadable cart (no sprites/sounds)."""
    p8 = tmp_path / "bare.p8"
    p8.write_text("pico-8 cartridge\nversion 41\n__lua__\ncls(0)\n", encoding="utf-8")
    out = tmp_path / "bare.moy"
    summary = import_p8.import_p8(str(p8), str(out))
    assert not (out / "sprites.moygfx").exists()
    assert not (out / "sounds.json").exists()
    assert any("sprites.moygfx" in e for e in summary["empty"])
    cart = moy_carts.load(str(out))
    assert cart is not None
    assert cart["sprites"] is None
    assert cart["sounds"] is None


# -- guided PICO-8 -> Moybyte porting (#36) --------------------------------

def test_port_notes_for_used_verbs_only(tmp_path):
    """The synthetic cart's Lua uses rectfill, circ, and btn(0). The generated
    main.py must carry the matching PORT NOTEs (rect inversion + arg-shape, circ
    inversion, btn names) and MUST NOT emit notes for verbs the cart never uses."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    src = (out / "main.py").read_text(encoding="utf-8")

    # gotcha 1+2: rectfill -> rect with corner->extent arg change
    assert "# PORT NOTE:" in src
    assert "rectfill() = FILLED rect" in src
    assert "rect()" in src
    assert "x1-x+1" in src                       # the corner->w,h conversion rule
    # gotcha 1: circ outline -> circb
    assert "circ(x,y,r,c) = OUTLINE circle" in src
    assert "circb()" in src
    # gotcha 3: numeric buttons -> names
    assert "btn(i) uses NUMBERS 0..5" in src
    assert "btn('left')" in src

    # The cart does NOT use these -> no PORT NOTE should mention them.
    assert "peek()" not in src
    assert "poke()" not in src
    assert "camera(" not in src
    assert "map()" not in src
    assert "sspr(" not in src
    assert "pal()" not in src

    # `rect` token must NOT fire just from being inside `rectfill` (word-boundary).
    # The cart has no standalone PICO-8 `rect(` call, so the outline-rect note
    # ("rect() = OUTLINE") must be absent.
    assert "rect() = OUTLINE" not in src


def test_port_checklist_and_cheatsheet_pointer(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    src = (out / "main.py").read_text(encoding="utf-8")

    # stretch goal: a port checklist with the boxes for this cart's verbs
    assert "PORT CHECKLIST" in src
    assert "[ ] rectfill" in src
    assert "[ ] circ outline -> circb" in src
    assert "[ ]" in src and "btn" in src
    # and a pointer to the cheatsheet
    assert "docs/porting_pico8.md" in src
    # still valid, runnable Python (the notes are all comments)
    compile(src, "main.py", "exec")


def test_no_port_notes_when_no_known_verbs(tmp_path):
    """A cart whose Lua uses no known PICO-8 verbs gets no PORT NOTE / checklist."""
    p8 = tmp_path / "plain.p8"
    p8.write_text(
        "pico-8 cartridge\nversion 41\n__lua__\nx = 1 + 2\nfoo = bar(x)\n",
        encoding="utf-8")
    out = tmp_path / "plain.moy"
    import_p8.import_p8(str(p8), str(out))
    src = (out / "main.py").read_text(encoding="utf-8")
    assert "# PORT NOTE:" not in src
    assert "PORT CHECKLIST" not in src
    # the cheatsheet pointer is always in the header regardless
    assert "docs/porting_pico8.md" in src


def test_scan_lua_verbs_word_boundaries():
    """_scan_lua_verbs matches whole-word calls only: rect != rectfill, and a
    word like 'sprint' must not trigger 'spr'/'print'."""
    found = import_p8._scan_lua_verbs(["rectfill(0,0,1,1,8)", "sprint = 3"])
    assert "rectfill" in found
    assert "rect" not in found      # not fired by the substring in rectfill
    assert "spr" not in found       # not fired by 'sprint'
    assert "print" not in found
    # a real spr() call does fire
    assert "spr" in import_p8._scan_lua_verbs(["spr(1, 0, 0)"])


def test_cheatsheet_doc_exists():
    doc = ROOT / "docs" / "porting_pico8.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    # the 3 gotchas + the key verb mappings are documented
    assert "rectfill" in text and "rectb" in text
    assert "circb" in text
    assert 'btn("left")' in text
    assert "peek" in text and "poke" in text
