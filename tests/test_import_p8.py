"""Tests for the offline PICO-8 .p8 -> .moy importer (tools/import_p8.py).

Feeds a small hand-written synthetic .p8 (a few __gfx__ rows + a tiny __sfx__ +
one __music__ row) and asserts:
  * the emitted sprites.moygfx nibbles match the input __gfx__ (round-trip stable),
  * sounds.json parses via runtime.audio.AudioBank.from_dict and lands on the
    right NOTES -- checked in Hz through runtime.audio, never by restating the
    importer's own constant,
  * manifest.json is valid and well-shaped,
  * main.py keeps the Lua only as a comment (never executable),
  * the cart load()s cleanly via runtime.moy_carts.

WHY THE Hz. These tests used to assert `steps[0][0] == 0x1E` -- the p8 pitch
byte, unchanged -- with the comment "pitch maps 1:1". It looked like a tight
assertion and it was a copy of the bug: PICO-8's tracker LABELS its pitch 0 as
C0, but its synth tunes pitch 33 to 440 Hz, so the labels sit two octaves below
concert naming and every imported cart played two octaves flat. Restating a
constant cannot catch a wrong constant. Asking `note_to_freq` what the note
came out as can, so that is what these assert.

The converter itself is moy-spec's, vendored (tools/p8_import.py) --
tests/test_p8_import_vendor.py is what keeps the two copies from diverging
again.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import import_p8  # noqa: E402
from runtime import moy_carts  # noqa: E402
from runtime.audio import AudioBank, note_to_freq  # noqa: E402
from runtime.editors import SpriteSheet  # noqa: E402

# PICO-8's synth is key_to_freq(k) = 440 * 2^((k-33)/12), so ITS pitch 33 is
# A4. Written out here as Hz so the expectations below are notes, not indices.
P8_A4 = 33
HZ = {p: 440.0 * 2.0 ** ((p - P8_A4) / 12.0) for p in range(64)}


# A synthetic .p8: 3 distinct __gfx__ rows (the rest blank), one __sfx__ line
# (3 audible notes then silence), and a single __music__ row pointing at sfx 0.
# __gfx__ rows use a handful of palette indices 0-15.
GFX_ROW0 = "0123456789abcdef" + "0" * 112      # first 16 px are a palette ramp
GFX_ROW1 = "f0f0f0f0" + "0" * 120              # checker
GFX_ROW2 = "8" * 8 + "0" * 120                 # 8 red px

# __sfx__ line layout: [mode:2][duration:2][loopstart:2][loopend:2] + 32 notes*5.
# duration 0x10 (=16 ticks/row) -> speed = 120/16 = 7.5 steps/sec, kept exact
# (SPEC.md 8.1's speed is not integer-only; rounding it to 8 drifts the SFX
# against the row clock that _row_secs computes from the same tick count).
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
    """parse -> kgfx -> SpriteSheet.from_hex -> to_hex is a fixed point.

    This is the test that LICENSES the vendored converter to skip a
    normalization pass. This repo's copy used to run its output back through
    the real SpriteSheet before writing it; moy-spec's cannot (it is stdlib
    only) and does not need to, because the grid it builds is already exactly
    what the editor would emit. That claim is only worth anything while
    something checks it here, where SpriteSheet actually exists.
    """
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    kgfx = (out / "sprites.moygfx").read_text(encoding="utf-8")
    # spec=False: p8's __gfx__ is 128x128, the top half of a SPEC.md 3.2 cart
    # sheet, and the importer emits exactly that region.
    sheet = SpriteSheet.from_hex(kgfx, cols=16, rows=16, spec=False)
    assert sheet.to_hex() == kgfx


def test_sounds_parse_via_audiobank(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    data = json.loads((out / "sounds.json").read_text(encoding="utf-8"))
    bank = AudioBank.from_dict(data)            # must not raise
    assert len(bank.sfx) >= 1
    sfx0 = bank.sfx[0]
    # speed: duration 0x10 -> 120/16 == 7.5 steps/sec, not rounded to 8
    assert sfx0.speed == 7.5
    # the 3 authored notes survive (trailing rests trimmed)
    assert len(sfx0.steps) == 3
    # PITCH: the three notes must come out at the FREQUENCIES PICO-8 would have
    # played them at. Note 2 is p8 pitch 33, which is PICO-8's A4 -- so this
    # asserts, in Hz, that a ported A4 is still an A4 and not the A2 two
    # octaves down that a literal reading of p8's tracker labels produces.
    assert note_to_freq(sfx0.steps[0][0]) == pytest.approx(HZ[0x1E])
    assert note_to_freq(sfx0.steps[1][0]) == pytest.approx(440.0)
    assert note_to_freq(sfx0.steps[2][0]) == pytest.approx(HZ[0x18])
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
    assert man["canvas"] == "320x240"
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


# -- full-fidelity audio import (#170): effects, 8 waves, 4-channel rows -----

def test_sfx_effect_nibble_and_new_waves_import_verbatim():
    # instrument 1 (tilted saw) -> wave 6, 4 (pulse) -> 4, 5 (organ) -> 5,
    # 7 (phaser) -> 7; the effect nibble rides along in p8 numbering.
    line = ("00" + "10" + "00" + "00"
            + "1e" + "1" + "6" + "1"      # tilted saw, slide
            + "21" + "4" + "5" + "2"      # pulse, vibrato
            + "18" + "5" + "7" + "0"      # organ, no effect -> 3-element step
            + "30" + "7" + "6" + "7"      # phaser, arp slow
            + "00000" * 28)
    d = import_p8._sfx_line_to_dict(line)
    off = import_p8.PICO8_PITCH_C0
    assert d["steps"][0] == [0x1E + off, 6, 6, 1]
    assert d["steps"][1] == [0x21 + off, 4, 5, 2]
    assert d["steps"][2] == [0x18 + off, 5, 7]
    assert d["steps"][3] == [0x30 + off, 7, 6, 7]
    # and the offset is the right one -- p8's 0x21 (33) is A4 (see HZ above)
    assert note_to_freq(d["steps"][1][0]) == pytest.approx(440.0)


def test_music_rows_import_all_channels_with_fixed_positions():
    # ch0=sfx0, ch1 off, ch2=sfx2, ch3 off -> [0, -1, 2] (trailing off trimmed,
    # positions kept so the engine holds each channel on the same voice).
    assert import_p8._music_line_row("00 " + "00" + "41" + "02" + "43") == [0, -1, 2]
    # only ch0 -> collapses to the 1-channel int form
    assert import_p8._music_line_row("00 " + "05" + "41" + "42" + "43") == 5
    # all off -> None (a pattern-run break)
    assert import_p8._music_line_row("00 " + "41" + "42" + "43" + "44") is None


def test_multichannel_music_track_plays_on_the_engine(tmp_path):
    # End to end: a 2-channel __music__ row -> sounds.json -> AudioBank ->
    # the engine claims two voices for it.
    from runtime import audio as A
    sfx2 = "00" + "10" + "00" + "00" + "24" + "0" + "6" + "0" + "00000" * 31
    sounds, n_sfx, n_music = import_p8.sfx_music_to_sounds(
        [SFX_LINE, sfx2], ["01 " + "00" + "01" + "42" + "43"])
    assert n_music == 1
    bank = AudioBank.from_dict(sounds)
    assert bank.music[0].pattern == [[0, 1]]
    eng = A.AudioEngine(bank, rate=8000)
    eng.play_music(0)
    if eng.active_channels():                     # binding present (needs cc)
        assert eng.active_channels() & 0x0F == 0b1100   # voices 3 + 2 claimed
        assert any(b != 0 for b in eng.render(400))


# -- p8 loop ranges + pattern-length rule (#170 round 2) ---------------------

def test_sfx_loop_range_imports_as_looping_with_start():
    # header: dur=0x10, loop=[2,4) -> 4 steps kept (NO rest-trim inside the
    # range), loop=True, loop_start=2
    line = ("00" + "10" + "02" + "04"
            + "1e060" + "20060" + "22060" + "00000" + "00000" * 28)
    d = import_p8._sfx_line_to_dict(line)
    assert d["loop"] is True and d["loop_start"] == 2
    assert len(d["steps"]) == 4                      # incl. the rest at idx 3
    assert d["steps"][3][2] == 0                     # silent


def test_a_p8_rest_imports_as_a_keyed_rest_not_a_minus_one():
    """SPEC.md 8.1: a note with vol 0 but a real pitch is a KEYED rest --
    silent, yet still the note a following slide glides from; only pitch -1
    leaves that origin untouched. Every PICO-8 tracker slot has a key, so a
    silent p8 slot must keep its pitch or the next slide starts from whatever
    was playing two rows ago. This repo's old copy of the converter emitted
    [-1, wave, 0] and quietly broke every ported slide."""
    line = ("00" + "10" + "00" + "00"
            + "1e060"                     # audible
            + "24000"                     # p8 pitch 0x24, vol 0 -> keyed rest
            + "26060"                     # audible again
            + "00000" * 29)
    steps = import_p8._sfx_line_to_dict(line)["steps"]
    assert steps[1][2] == 0, "a vol-0 p8 slot must stay silent"
    assert steps[1][0] == 0x24 + import_p8.PICO8_PITCH_C0, (
        "and must keep its key, or a following slide has no origin")


def test_sfx_length_trick_loop_start_with_end_zero():
    # loop start 2, end 0 = "play 2 notes" (p8's short-sfx length trick)
    line = ("00" + "10" + "02" + "00"
            + "1e060" + "20060" + "22060" + "24060" + "00000" * 28)
    d = import_p8._sfx_line_to_dict(line)
    assert d["loop"] is False and "loop_start" not in d
    assert len(d["steps"]) == 2


def test_row_secs_follow_first_non_looping_channel():
    # ch0 loops (dur 8), ch1 does not (dur 32): the row lasts ch1's 32 notes
    # = 32*32/120 s -- zepto8's rule, NOT ch0's tempo.
    looper = "00" + "08" + "00" + "20" + "1e060" * 32
    lead = "00" + "20" + "00" + "00" + "24060" * 32
    metas = [import_p8._sfx_meta(looper), import_p8._sfx_meta(lead)]
    assert abs(import_p8._row_secs([0, 1], metas) - 32 * 32 / 120.0) < 1e-9
    # all channels looping -> the SLOWEST looping channel's 32 notes
    slow_looper = "00" + "18" + "00" + "20" + "1e060" * 32
    metas2 = [import_p8._sfx_meta(looper), import_p8._sfx_meta(slow_looper)]
    assert abs(import_p8._row_secs([0, 1], metas2) - 32 * 0x18 / 120.0) < 1e-9
    # length-trick reference channel: loop_start notes, not 32
    short = "00" + "20" + "05" + "00" + "24060" * 32
    metas3 = [import_p8._sfx_meta(short)]
    assert abs(import_p8._row_secs([0], metas3) - 5 * 32 / 120.0) < 1e-9


def test_multichannel_track_emits_row_secs_when_rows_differ(tmp_path):
    looper = "00" + "08" + "00" + "20" + "1e060" * 32
    lead = "00" + "20" + "00" + "00" + "24060" * 32
    fast_lead = "00" + "10" + "00" + "00" + "26060" * 32
    sounds, _n, _m = import_p8.sfx_music_to_sounds(
        [looper, lead, fast_lead],
        ["01 " + "00" + "01" + "42" + "43",     # row 0: looper + lead
         "02 " + "00" + "02" + "42" + "43"])    # row 1: looper + fast lead
    trk = sounds["music"][0]
    assert "row_secs" in trk
    assert abs(trk["row_secs"][0] - 32 * 32 / 120.0) < 1e-9
    assert abs(trk["row_secs"][1] - 32 * 16 / 120.0) < 1e-9
