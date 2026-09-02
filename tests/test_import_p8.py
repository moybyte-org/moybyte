"""Tests for the offline PICO-8 .p8 -> .moy importer (tools/import_p8.py).

Feeds a small hand-written synthetic .p8 (a few __gfx__ rows + a tiny __sfx__ +
one __music__ row) and asserts:
  * the emitted sprites.moygfx nibbles match the input __gfx__ (round-trip stable),
  * sounds.json parses via runtime.audio.AudioBank.from_dict and lands on the
    right NOTES -- checked in Hz through runtime.audio, never by restating the
    importer's own constant,
  * manifest.json is valid and well-shaped,
  * main.lua carries the cart's own code, converted and RUNNING under the
    generated PICO-8 shim -- not a comment, and not a Python stub,
  * the cart load()s cleanly via runtime.moy_carts.

...and, since #194 made this importer the browser's too:
  * every import declares the view(128, 120) ZOOM HINT -- the one guaranteed
    footgun, and the reason it is not a flag any more,
  * a `.p8` and the same cart as a `.p8.png` produce byte-identical folders,
  * a file that is not a cart produces a SENTENCE, not a traceback.

Two sibling suites carry the halves this one cannot: tests/test_p8_micropython.py
runs the same import on a real MicroPython (the browser's interpreter), and
tests/test_web_p8_e2e.py drives the drop in a real browser.

THE PYTHON PORTING SCAFFOLD IS GONE (2026-08-29), and with it the tests that
pinned it. `__lua__` used to become a commented reference block with a runnable
Python stub and `# PORT NOTE:` guidance, because moy-spec's Lua porter was the
only route that ran and this repo did not have it. It is vendored now, so the
drop emits a cart that PLAYS -- and the checks that used to assert "the code did
NOT come across" assert that it did, and that it ticks.

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
import p8_fixture  # noqa: E402
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
    # A moy-spec cart (SPEC.md 3.1) -- Lua by definition, which is what
    # moy_carts.load reads "runtime": "lua" and "main.lua" out of.
    assert man["format"] == "moy-1"
    assert man["main"] == "main.lua"
    assert man["fps"] == 30                      # PICO-8's own rate
    assert man["ported_from"] == "pico-8"
    # A p8 cart is 128x128 (SPEC.md 1/3.1 carries that size to inherit the
    # PICO-8 back catalogue at native res), NOT the 320x240 default -- and the
    # size has to be in moy_carts.CANVAS_SIZES or Player.start refuses the cart.
    assert man["canvas"] == "128x128"
    assert man["canvas"] in moy_carts.CANVAS_SIZES
    # Imported, not authored (#194): republishing somebody else's cart is not
    # what this feature is for, so nothing downstream may treat it as mine.
    assert man["safe_to_share"] is False
    # title comes from the first real lua comment line
    assert summary["title"] == "my test cart"
    assert man["title"] == "my test cart"


def test_main_lua_carries_the_converted_cart_code(tmp_path):
    """The inversion of the old `test_main_py_keeps_lua_as_comment_only`: the
    cart's Lua is CODE now, mechanically converted to Lua 5.4, under a shim."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    src = (out / "main.lua").read_text(encoding="utf-8")
    assert not (out / "main.py").exists(), "the Python stub is gone"
    # the shim, then the cart's own body -- as code, not as a comment block
    assert "PICO-8 compatibility shim" in src
    assert "function p8_draw()" in src, \
        "_draw is renamed so the shim can pace it at PICO-8's 30fps"
    # the p8 dialect is converted, not carried through
    assert " x = x - (1)" in src, "`x -= 1` must expand to Lua 5.4"
    assert "-=" not in src.split("end shim")[-1]
    # every body line is live: nothing from the cart is commented out wholesale
    body = src.split("end shim ")[-1]
    assert "cls(0)" in body and not any(
        ln.strip().startswith("-- cls(0)") for ln in body.split("\n"))


def test_cart_loads_via_moy_carts(tmp_path):
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    cart = moy_carts.load(str(out))
    assert cart is not None
    assert cart["title"] == "my test cart"
    assert cart["runtime"] == "lua" and cart["main"] == "main.lua"
    assert cart["sprites"] is not None
    assert cart["sounds"] is not None
    # the loaded sprite hex is the same grid we emitted
    assert cart["sprites"].split("\n")[0] == GFX_ROW0
    # the loaded sounds round-trip through the audio model
    AudioBank.from_dict(cart["sounds"])


def test_the_map_and_the_flags_come_across_now(tmp_path):
    """`__map__` and `__gff__` used to be DEFERRED -- noted in the report and
    written nowhere, because the tilemap writer lived only in moy-spec. It is
    vendored now, so both land: the map as the console's own `map.moymap` (the
    Map editor opens it) and the flags baked into main.lua for fget()."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))

    names = sorted(p.name for p in out.iterdir())
    # flags.moyflags since 2026-09: SPEC.md 3.5's sidecar, __gff__ byte for byte.
    assert names == ["flags.moyflags", "main.lua", "manifest.json", "map.moymap",
                     "sounds.json", "sprites.moygfx"]
    head, first = (out / "map.moymap").read_text(
        encoding="utf-8").split("\n")[:2]
    assert head == "128 64", "all 64 rows, not just __map__'s 32"
    # p8 cell ids 01 02 03 04 store as id+1 (0 means empty in .moymap)
    assert first.startswith("0203040500")
    assert "__p8_gff" in (out / "main.lua").read_text(encoding="utf-8")


def test_empty_sections_handled(tmp_path):
    """A .p8 with no assets still produces a loadable cart (no sprites/sounds)."""
    p8 = tmp_path / "bare.p8"
    p8.write_text("pico-8 cartridge\nversion 41\n__lua__\ncls(0)\n", encoding="utf-8")
    out = tmp_path / "bare.moy"
    summary = import_p8.import_p8(str(p8), str(out))
    assert not (out / "sprites.moygfx").exists()
    assert not (out / "sounds.json").exists()
    assert not (out / "map.moymap").exists()
    assert any("sprites.moygfx" in e for e in summary["empty"])
    cart = moy_carts.load(str(out))
    assert cart is not None
    assert cart["sprites"] is None
    assert cart["sounds"] is None


# -- the compatibility report, aimed at the SHIM (#194) ---------------------
# What replaced the guided PICO-8 -> Python porting scaffold (#36). The old
# tests here pinned `# PORT NOTE:` lines and a port checklist inside a generated
# main.py; there is no main.py any more, and the question a report has to answer
# changed with it -- not "what will you have to rewrite" but "where will the
# cart that is already running stop agreeing with PICO-8".

def test_the_report_names_only_the_gaps_this_cart_reaches(tmp_path):
    """The synthetic cart calls cls/btn/rectfill/circ/spr -- all of which the
    shim implements -- and nothing else. So its report must name NO gaps, which
    is the half a table of advice gets wrong first: boilerplate for verbs the
    cart never mentions."""
    p8 = _write_p8(tmp_path)
    summary = import_p8.import_p8(str(p8), str(tmp_path / "out.moy"))
    assert summary["unsupported"] == []
    assert summary["differs"] == []
    assert summary["verbs"] == []
    text = "\n".join(import_p8.report_lines(summary))
    assert "not supported" not in text and "works differently" not in text


def test_the_report_separates_the_three_kinds_of_gap(tmp_path):
    """Three different failures, and a kid needs them said differently.

    MISSING stops the cart -- nothing answers to that name. STUBBED lets it run
    and come out wrong in a stated way. DIFFERS means a verb of that name
    exists and disagrees.

    `dset` moved from MISSING to STUBBED on 2026-09-01 and is the good case to
    pin: the report has to stop calling it "not supported" the moment the shim
    starts answering, or it is telling a kid to rewrite working code.
    """
    p8, _png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    summary = import_p8.import_p8(p8, str(tmp_path / "out.moy"))
    assert summary["verbs"] == ["dset"]
    assert summary["unsupported"] == [], "dset is answered now, not missing"
    assert any("dset()" in u for u in summary["lossy"])
    text = "\n".join(import_p8.report_lines(summary))
    assert "approximated: cartdata()/dget()/dset()" in text

    # The MECHANISM, not the census: every kind must reach the reader as its
    # own sentence even when this cart happens not to trip it. Deleting the
    # check because a table is momentarily empty is how the distinction stops
    # working before the next entry arrives.
    from p8_writer import report_lines
    lines = report_lines({"title": "x",
                          "differs": ["sspr() takes flip flags"],
                          "unsupported": ["cocreate() is a coroutine"],
                          "lossy": ["fillp() sets a dither pattern"]})
    assert any(l.startswith("works differently: ") for l in lines)
    assert any(l.startswith("not supported: ") for l in lines)
    assert any(l.startswith("approximated: ") for l in lines)


def test_a_stubbed_verb_is_answered_and_a_missing_one_is_not():
    """The two tables have to agree with the SHIM, not with each other.

    A name in SHIM_STUBS must be something the port provides -- that is what
    "stubbed" claims. A name in SHIM_GAPS must be provided by nothing. Getting
    this backwards ships a kid a "rewrite that part" note about a verb that
    works, or silence about one that will stop their cart.
    """
    import p8_lua_port
    from p8_writer import SHIM_GAPS, SHIM_STUBS, MISSING, STUBBED
    from runtime.lua_ext import LIBMOY_VERBS

    provided = set(p8_lua_port.P8_API) | set(LIBMOY_VERBS)
    for name, (kind, advice) in sorted(SHIM_STUBS.items()):
        assert kind == STUBBED, name
        assert name in provided, (
            "SHIM_STUBS calls %r stubbed, but nothing provides that name -- "
            "the cart will stop, so it belongs in SHIM_GAPS." % name)
        assert advice.strip() and name in advice, \
            "%r's advice should name the verb it is about" % name
    for name, (kind, advice) in sorted(SHIM_GAPS.items()):
        if kind == MISSING:
            assert name not in provided, (
                "SHIM_GAPS calls %r missing, but the port provides it now -- "
                "move it to SHIM_STUBS." % name)
        assert advice.strip() and name in advice, \
            "%r's advice should name the verb it is about" % name
    assert not (set(SHIM_GAPS) & set(SHIM_STUBS)), "a verb is in both tables"

def test_every_declared_gap_is_really_a_gap():
    """The table cannot rot into a lie. A name in SHIM_GAPS marked "missing"
    must be absent from the generated shim's own API list AND from the console's
    Lua verb table; one marked "differs" must be present in exactly one of them,
    because "it exists and disagrees" is what that word claims.

    This is the guard that makes the report survive a re-vendor: upstream
    growing a shim verb turns a stale "not supported" line red here instead of
    shipping it to a kid."""
    import p8_lua_port
    from p8_writer import SHIM_GAPS, MISSING
    from runtime.lua_ext import LIBMOY_VERBS

    provided = set(p8_lua_port.P8_API) | set(LIBMOY_VERBS)
    for name, (kind, advice) in sorted(SHIM_GAPS.items()):
        if kind == MISSING:
            assert name not in provided, (
                "SHIM_GAPS calls %r missing, but the port provides it now -- "
                "drop the entry (or re-file it as `differs`)." % name)
        else:
            assert name in provided, (
                "SHIM_GAPS calls %r a difference, but nothing provides that "
                "name at all -- it is `missing`." % name)
        assert advice.strip() and name in advice, \
            "%r's advice should name the verb it is about" % name


def test_scan_lua_verbs_word_boundaries():
    """scan_lua_verbs matches whole-word calls only, at BOTH ends -- a short
    name must not fire inside a longer one, and a name must not fire as a
    method call."""
    found = import_p8.scan_lua_verbs(["stat(0)", "sset(1,1,2)"])
    assert found == {"stat", "sset"}
    assert import_p8.scan_lua_verbs(["local n = settings(1)"]) == set()
    assert import_p8.scan_lua_verbs(["obj:flip()", "obj.flip()"]) == set()
    assert import_p8.scan_lua_verbs(["flip()"]) == {"flip"}
    # a bare mention that is not a call does not count
    assert import_p8.scan_lua_verbs(["-- peek is not available"]) == set()


def test_the_two_languages_doc_exists():
    """The scaffold's cheatsheet was `docs/porting_pico8.md` and is now
    `docs/two_languages.md` -- repurposed, not deleted, because the kid who
    opens an imported cart is looking at Lua on a console that also runs
    Python, which is a real question and no longer a PICO-8 one."""
    doc = ROOT / "docs" / "two_languages.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "elseif" in text and "end" in text        # Lua's own shapes
    assert "def " in text and "_update(dt)" in text  # ...beside Python's
    assert "runtime" in text                         # what picks the language
    assert not (ROOT / "docs" / "porting_pico8.md").exists()


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


# -- the zoom hint, which must not be an option anybody can forget (#194) ----

def test_every_import_declares_the_view_zoom_hint(tmp_path):
    """THE footgun. A 128x128 p8 cart without `view(128, 120)` letterboxes at 1x
    instead of compositing centred at the biggest integer scale that fits -- a
    regeneration on 2026-08-11 dropped moy-spec's `--zoom` and shipped a tiny
    Celeste to the glass. In a browser that failure would fire on EVERY drop, so
    here the hint is unconditional: no flag, no default, no way to omit it."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    src = (out / "main.lua").read_text(encoding="utf-8")
    # The porter takes the crop as an ARGUMENT (`--zoom` on its own CLI); this
    # importer passes it as data, from p8_writer.P8_CROP, so there is no flag on
    # any tier and nothing to forget.
    assert "local P8_VH = 120" in src
    assert "if P8_VH < 128 then view(128, P8_VH) end" in src
    from p8_writer import P8_CROP, P8_VIEW_H
    assert 128 - P8_CROP[0] - P8_CROP[1] == P8_VIEW_H


def test_the_imported_cart_is_a_lua_cart_on_the_declared_canvas(tmp_path):
    """#194's first requirement, at the store level: what the launcher gets is a
    real Lua cart on a canvas Player.start will accept."""
    p8 = _write_p8(tmp_path)
    out = tmp_path / "out.moy"
    import_p8.import_p8(str(p8), str(out))
    cart = moy_carts.load(str(out))
    assert cart["canvas"] == (128, 128), \
        "an out-of-set canvas is refused BY NAME in Player.start"
    assert cart["canvas"] in {v for v in moy_carts.CANVAS_SIZES.values()} \
        or "128x128" in moy_carts.CANVAS_SIZES
    assert cart["runtime"] == "lua"
    assert cart["src"].startswith("-- my test cart")


# -- the .p8.png (BBS steganographic) path ----------------------------------

def test_both_cart_forms_produce_the_same_moy_folder(tmp_path):
    """#194's done-when, at the file level: a `.p8` and the SAME cart as a
    `.p8.png` must import to byte-identical carts.

    The PNG twin is built here (tests/p8_fixture.py) rather than committed --
    the fixture is a cart we wrote, and the stego encoder is the exact inverse
    of the converter's own reader, so this exercises the real chunk walk, the
    real unfilter (all five PNG filter types) and the real ROM unpack."""
    p8, png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    a, b = tmp_path / "from_p8.moy", tmp_path / "from_png.moy"
    sa = import_p8.import_p8(p8, str(a))
    sb = import_p8.import_p8(png, str(b))
    assert sa["title"] == sb["title"] == "tiny dash"
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir())
    for name in names:
        assert (a / name).read_text(encoding="utf-8") == \
               (b / name).read_text(encoding="utf-8"), \
            "%s differs between the .p8 and the .p8.png form" % name


def test_the_png_cart_loads_and_keeps_its_assets(tmp_path):
    p8, png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    out = tmp_path / "png.moy"
    import_p8.import_p8(png, str(out))
    cart = moy_carts.load(str(out))
    assert cart is not None
    assert cart["sprites"] is not None and cart["sounds"] is not None
    sheet = SpriteSheet.from_hex(cart["sprites"], cols=16, rows=16, spec=False)
    assert sheet.to_hex().split("\n")[0].startswith("0123456789abcdef")
    AudioBank.from_dict(cart["sounds"])


# -- report, don't crash (#194) ---------------------------------------------

def test_the_report_says_the_code_DID_come_across(tmp_path):
    """The headline INVERTED on 2026-08-29. It used to have to say the code did
    not run, because the scaffold kept the Lua as a comment; it runs now, and a
    report still saying otherwise would be the most misleading line on the
    page."""
    p8, _png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    summary = import_p8.import_p8(p8, str(tmp_path / "out.moy"))
    text = "\n".join(import_p8.report_lines(summary))
    assert "tiny dash" in text
    assert '"tiny dash" imported.' in text
    assert "CODE did NOT" not in text
    # The one line the report keeps about the code: a kid who opens an imported
    # cart finds Lua, and this is where they are told why.
    assert "cart's own Lua" in text
    # the map is no longer a "not imported" line -- it is a file
    assert "not imported: __map__" not in text


def test_png_guards_name_what_the_file_actually_is():
    """A frozen build at opt=3 strips the converter's assert-based PNG
    validation, so malformed input would fail deep inside a struct unpack. These
    guards are what stands in for those asserts on every tier."""
    from p8_writer import looks_like_png, png_problem

    assert not looks_like_png(b"pico-8 cartridge\n")
    assert png_problem(b"pico-8 cartridge\n") == "that file is not a PNG"
    assert "truncated" in png_problem(b"\x89PNG\r\n\x1a\n")
    # a real PNG, but a picture rather than a cart: 8x8 RGBA
    import struct
    import zlib

    def _png(w, h, depth=8, ctype=6):
        raw = b"".join(b"\x00" + b"\x00" * (w * 4) for _ in range(h))

        def chunk(t, b):
            return (struct.pack(">I", len(b)) + t + b
                    + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

    assert "160x205" in png_problem(_png(8, 8))
    assert "8-bit RGBA" in png_problem(_png(160, 205, ctype=2))
    assert png_problem(_png(160, 205)) is None      # the right SHAPE passes


def test_a_file_that_is_not_a_cart_reports_instead_of_importing():
    from p8_writer import sections_problem
    assert sections_problem(import_p8.parse_p8("hello, I am a readme\n"))
    assert sections_problem(import_p8.parse_p8(SYNTHETIC_P8)) is None


def test_a_nonsense_pxa_stream_raises_rather_than_returning_half_a_program(tmp_path):
    """PICO-8 >= 0.2.0 packs its Lua with the `pxa` scheme, which the converter
    READS since 2026-08-30 -- it used to refuse it and tell the reader to
    re-save the cart as text, which is a fair instruction and a bad answer.

    What has to stay true is the failure shape. A header that says `pxa` over
    bytes that are not one decodes to nonsense, and the honest answer is a
    raise: half a program that silently loses its second half is the one
    outcome worse than a refusal. `firmware/web_runner/web_p8.py` turns this
    into a sentence for the browser.
    """
    rom = bytearray(p8_fixture.sections_to_rom(
        import_p8.parse_p8(p8_fixture.read_p8_text())))
    # A `pxa` header claiming 4KB of code, over the cart's plain ASCII.
    rom[p8_fixture.CODE_AT:p8_fixture.CODE_AT + 8] = b"\x00pxa\x10\x00\x10\x00"
    pxa = tmp_path / "pxa.p8.png"
    pxa.write_bytes(p8_fixture.rom_to_png(bytes(rom)))

    with pytest.raises(ValueError) as exc:
        import_p8.read_p8(str(pxa))
    assert "pxa" in str(exc.value)


def test_the_filename_title_is_the_same_on_every_tier(tmp_path):
    """The companion to tests/test_p8_micropython.py's shim check: the title a
    comment-less cart gets from its FILENAME must be the same string on CPython
    (where `os.path.basename` is real) and on MicroPython (where p8_writer
    injects one). Two tiers, one rule -- upstream's."""
    p8 = tmp_path / "space-blaster_2.p8"
    p8.write_text("pico-8 cartridge\nversion 41\n__lua__\nx = 1\n",
                  encoding="utf-8")
    summary = import_p8.import_p8(str(p8), str(tmp_path / "out.moy"))
    assert summary["title"] == "space blaster 2"


# -- IT RUNS: the imported cart's own code executing (#194's whole point) -----

def _need_lua():
    """Skip unless the host Lua binding BUILT (a C compiler, not a package --
    the host runs the BOARDS' Lua now; see runtime/lua_host.py's header)."""
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")


def test_the_imported_cart_runs_its_own_code_under_the_player(tmp_path):
    """The claim the whole vendoring is for, checked without a browser.

    Every other test here proves the FILES are right. This one starts the
    imported cart on the same Player the console runs, ticks it, and reads the
    cart's OWN Lua global back out of the live state -- `ticks` is incremented
    by tiny_dash's `_update`, which the port renamed `p8_update` and the shim
    paces at PICO-8's fixed 30fps. A stub cart, a shim that never called the
    body, or a lifecycle that never got wired would all leave it at 0.
    """
    _need_lua()
    from ws_helpers import build_ws

    p8, _png = p8_fixture.write_pair(str(tmp_path), import_p8.parse_p8)
    ws = build_ws(tmp_path)
    out = Path(ws.carts_root) / "tiny_dash.moy"
    import_p8.import_p8(p8, str(out))

    cart = moy_carts.load(str(out))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player._lua is not None, "the lua runtime seam never started"

    # 30 console frames at the cart's own 1/30 dt -> 30 p8 ticks. The driver
    # normally opens each frame's input window; there is no driver here.
    for _ in range(30):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.player.cart_error is None, ws.player.cart_error
    assert ws.player._lua.get_global("ticks") == 30, \
        "the cart's own _update did not run 30 times"

    # ...and the cart's own state moved when a button was held, through the
    # shim's numeric btn() over the console's named one.
    before = ws.player._lua.get_global("x")
    ws.input.set_held("right", True)
    for _ in range(10):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.player._lua.get_global("x") == before + 10, \
        "btn(1) did not reach the cart through the shim"


# -- the pxa code compression ------------------------------------------------
#
# Every PICO-8 >= 0.2.0 writes its code this way, so before 2026-08-30 most of
# the BBS was refused with "save it as text .p8 first". The decoder is in the
# VENDORED converter (moy-spec's p8_import.py) and was verified there against
# four real carts -- celeste_classic_2, crimson_night, mossmoss, picooffroad --
# each decoding to EXACTLY the byte count its own header declares.
#
# Those carts are other people's work and are not committed (the same rule
# `ports/celeste.moy` lives under), so what runs here is an ENCODER written
# separately from the same reference, driven over payloads chosen to make each
# chunk kind appear. It is a weaker check than a real cart on its own -- an
# encoder and a decoder written by one reader can share a misreading -- which
# is why the real-cart verification is recorded above rather than assumed.


class _PxaWriter:
    """The smallest valid pxa encoder: LSB-first bits, literals through the
    move-to-front table, and back-references for repeats."""

    def __init__(self):
        self.out = bytearray()
        self.bit = 1

    def put_bit(self, v):
        if self.bit == 1:
            self.out.append(0)
        if v:
            self.out[-1] |= self.bit
        self.bit = 1 if self.bit == 128 else self.bit << 1

    def put_val(self, val, bits):
        for i in range(bits):
            self.put_bit((val >> i) & 1)

    def put_chain(self, val, link_bits):
        top = (1 << link_bits) - 1
        while True:
            v = min(val, top)
            self.put_val(v, link_bits)
            val -= v
            if v != top:
                return

    def literal(self, table, byte):
        lpos = table.index(byte)
        self.put_bit(1)                       # block_type: literal
        # The unary run widens the window in steps of 4 bits; lpos < 16 needs
        # none, so a single 0 ends the run.
        nbits, base = 4, 0
        run = 0
        while lpos >= base + (1 << nbits):
            base += 1 << nbits
            nbits += 1
            run += 1
        for _ in range(run):
            self.put_bit(1)
        self.put_bit(0)
        self.put_val(lpos - base, nbits)
        table.insert(0, table.pop(lpos))      # move to front

    def ref(self, offset, length):
        self.put_bit(0)                       # block_type: back-reference
        self.put_bit(0)                       # chain(1,2) -> 0 -> a 15-bit distance
        self.put_val(offset - 1, 15)
        self.put_chain(length - 3, 3)

    def finish(self, raw_len):
        body = bytes(self.out)
        head = bytearray(b"\x00pxa")
        head += bytes((raw_len >> 8, raw_len & 0xFF))
        total = len(body) + 8
        head += bytes((total >> 8, total & 0xFF))
        return bytes(head) + body


def _pxa_encode(payload):
    """`payload` -> a pxa stream, using a reference wherever a run repeats so
    the REF path (including a self-overlapping one) is actually exercised."""
    w = _PxaWriter()
    table = list(range(256))
    i = 0
    while i < len(payload):
        best_off = best_len = 0
        for off in range(1, min(i, 32767) + 1):
            n = 0
            while (i + n < len(payload) and n < 200
                   and payload[i + n - off] == payload[i + n]):
                n += 1
            if n > best_len:
                best_off, best_len = off, n
        if best_len >= 3:
            # NO move-to-front here, and that is the reference's rule rather
            # than an omission: only a CHR literal touches the table, so bytes
            # that arrive through a REF leave it exactly as they found it. This
            # encoder did update it, and the short payloads still round-tripped
            # -- they had no literal AFTER a reference, so nothing ever read
            # the table the two sides disagreed about. The full-cart case did.
            w.ref(best_off, best_len)
            i += best_len
        else:
            w.literal(table, payload[i])
            i += 1
    return w.finish(len(payload))


@pytest.mark.parametrize("name,payload", [
    ("literals only", b"function _draw() cls(1) end"),
    # A long self-overlapping run: the reference reaches into what the copy is
    # writing, which is how pico-8 spells a repeat. A slice copy passes the
    # short cases and produces garbage here.
    ("overlapping run", b"ab" + b"xy" * 40),
    ("repeated blocks", b"local t={}\n" * 12),
    ("the whole byte range", bytes(range(256)) + bytes(range(256))),
    ("one byte", b"x"),
])
def test_a_pxa_stream_round_trips(name, payload):
    rom = bytearray(0x8000)
    stream = _pxa_encode(payload)
    rom[0x4300:0x4300 + len(stream)] = stream
    import p8_import

    assert p8_import._pxa_decompress(rom, 0x4300) == payload, name


def test_a_pxa_cart_imports_end_to_end(tmp_path):
    """The reader's actual gesture: a .p8.png whose code is pxa-packed goes in,
    a cart with that code in it comes out. Everything else about the cart --
    gfx, map, sfx -- rides the same ROM and must be untouched by the change."""
    text = p8_fixture.read_p8_text()
    sections = import_p8.parse_p8(text)
    lua = "\n".join(sections["lua"]).encode("ascii", "replace")

    rom = bytearray(p8_fixture.sections_to_rom(sections))
    stream = _pxa_encode(lua)
    rom[0x4300:] = b"\x00" * (len(rom) - 0x4300)
    rom[0x4300:0x4300 + len(stream)] = stream
    png = tmp_path / "packed.p8.png"
    png.write_bytes(p8_fixture.rom_to_png(bytes(rom)))

    got = import_p8.read_p8(str(png))
    assert "\n".join(got["lua"]) == lua.decode("ascii")
    # ...and the rest of the ROM came through, so this reads a cart and not
    # just a code section. Compared over the rows the source declares: a ROM
    # always carries all 128 gfx / 32 map rows, and a .p8 text stops writing
    # them once the rest are blank, so the tails differ by construction.
    assert got["gfx"][:len(sections["gfx"])] == sections["gfx"]
    assert got["map"][:len(sections["map"])] == sections["map"]
    assert set("".join(got["gfx"][len(sections["gfx"]):])) <= {"0"}, \
        "the rows past the source's own are not blank -- the ROM shifted"


def test_button_glyphs_become_numbers_in_code_and_letters_in_text():
    """PICO-8's six button glyphs are ORDINARY CHARACTERS in cart source, and
    they mean two different things depending on where they sit.

    In an expression -- `btn(<right>)` -- they are the button NUMBER, and Lua
    5.4 will not take the character at all, so the cart does not load. In a
    string -- `"press <x> to start"` -- they are an icon the cart draws, and
    the console's font is petme128, ASCII and nothing else, so they drew as
    blank: the cart's own control legend, missing.

    The text side deliberately becomes A and B rather than any attempt at
    PICO-8's own badges. This console's buttons ARE named A and B (the shim
    maps p8 button 4 to `a` and 5 to `b`), so an O/X icon would be a faithful
    copy of the wrong instruction.
    """
    import p8_lua_port
    LEFT, RIGHT, UP, DOWN = "\x8b", "\x91", "\x94", "\x83"
    O, X = "\x8e", "\x97"

    code = p8_lua_port.p8_lua_to_lua54([
        "if btn(%s) and btnp(%s) then y=1 end" % (RIGHT, X),
        'print("press %s to start")' % X,
        'print("%s%s%s%s")' % (LEFT, UP, RIGHT, DOWN),
        'print("%s/z")' % O,
    ])
    # The glyph becomes a NAME the shim predefines to the button number --
    # not the bare number -- because a cart may also ASSIGN to a glyph
    # (see the squiddy case below). The VALUE is pinned at runtime.
    assert "btn(_p8g145) and btnp(_p8g151)" in code
    assert '"press B to start"' in code
    assert '"<^>v"' in code
    assert '"A/z"' in code
    assert not any(ord(c) > 126 for c in code), "no glyph may survive to Lua"


def test_the_utf8_spelling_of_the_glyphs_maps_the_same_way():
    """A `.p8.png` ROM stores P8SCII bytes; a text `.p8` stores the UTF-8
    emoji. Same six buttons, two spellings, and a cart imported either way has
    to come out the same."""
    import p8_lua_port
    code = p8_lua_port.p8_lua_to_lua54([
        'if btn(\u27a1\ufe0f) then y=1 end',
        'print("press \u274e now")',
    ])
    assert "btn(_p8g145)" in code
    assert '"press B now"' in code


def test_a_glyph_with_no_ascii_stand_in_is_reported_not_guessed():
    """The cat, the face and the house have no honest ASCII analogue, so they
    are left alone -- and SAID, because a wrong icon is worse than a missing
    one and a missing one says nothing by itself."""
    import p8_lua_port
    from p8_writer import report_lines
    assert "\x82" not in p8_lua_port._GLYPH_TEXT      # the cat
    lines = report_lines({"title": "x", "unsupported": [], "differs": [],
                          "lossy": ["2 PICO-8 picture characters the console's "
                                    "font has no letter for -- they draw as "
                                    "blank space"]})
    assert any("picture characters" in ln for ln in lines)


def test_the_picture_glyphs_are_escaped_not_written_as_bytes():
    """A P8SCII byte in a string leaves as `\\ddd`, never as itself.

    main.lua is written UTF-8, so a literal 0x87 becomes TWO bytes on disk --
    and the shim's print() reads BYTES, so it looked up 0xC2 and the heart
    never drew. The bug lived entirely inside one file write, which is why a
    probe cart whose escaped glyphs rendered and whose literal ones did not was
    what found it."""
    import p8_lua_port
    code = p8_lua_port.p8_lua_to_lua54(['print("a\x87b\x92")'])
    assert '"a\\135b\\146"' in code
    assert not any(ord(c) > 126 for c in code)


def test_every_picture_glyph_round_trips_through_its_packed_hex():
    """The art is authored as ASCII rows and shipped as packed bytes; this is
    the only thing standing between a typo in the packing and a wrong icon."""
    import p8_lua_port
    hx = p8_lua_port._wide_glyph_hex()
    assert len(hx) == 26 * 10, "26 codes, 5 rows, one byte each"
    for code in range(0x80, 0x9A):
        rows = p8_lua_port._P8_WIDE_ART.get(code)
        off = (code - 0x80) * 10
        for r in range(5):
            v = int(hx[off + r * 2:off + r * 2 + 2], 16)
            if rows is None:
                assert v == 0, "%s should pack as blank" % hex(code)
                continue
            got = "".join("#" if v >> c & 1 else "." for c in range(7))
            assert got == rows[r], (hex(code), r, got, rows[r])


def test_the_six_buttons_are_letters_and_never_drawn_as_badges():
    """Deliberate, and the reason belongs next to the assertion: this console's
    buttons ARE named A and B, so a pixel-perfect PICO-8 badge would be a
    faithful copy of the wrong instruction."""
    import p8_lua_port
    for code in (0x8b, 0x91, 0x94, 0x83, 0x8e, 0x97):
        assert code not in p8_lua_port._P8_WIDE_ART, hex(code)
        assert chr(code) in p8_lua_port._GLYPH_TEXT, hex(code)


def test_a_button_code_reads_the_same_by_every_route(tmp_path):
    """Literal, `\\151` escape, and chr(151) must all draw the same thing.

    The import rewrites the LITERAL, which is what makes the ported source
    readable -- but a cart that escapes it, or builds it at runtime, never
    passes through that rewrite. So print() and chr() resolve the codes too,
    and this runs the cart to prove the three agree rather than trusting that
    three code paths were each remembered."""
    _need_lua()
    from ws_helpers import build_ws

    src = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
           "function _draw()\n"
           " cls(0)\n"
           ' print("\\151",0,0,7)\n'
           ' print(chr(151),0,8,7)\n'
           ' print("B",0,16,7)\n'
           "end\n")
    p8 = tmp_path / "btn.p8"
    p8.write_text(src, encoding="utf-8")

    ws = build_ws(tmp_path)
    out = Path(ws.carts_root) / "btn.moy"
    import_p8.import_p8(str(p8), str(out))
    cart = moy_carts.load(str(out))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(4):
        ws.input.begin_frame()
        ws.frame(1 / 30)
    assert ws.player.cart_error is None, ws.player.cart_error

    canvas = ws.canvas
    rows = [[canvas.pix(x, y0 + dy) for dy in range(5) for x in range(4)]
            for y0 in (0, 8, 16)]
    assert rows[0] == rows[2], "the escape must draw what the letter draws"
    assert rows[1] == rows[2], "chr() must draw what the letter draws"
    assert any(v for v in rows[2]), "the probe drew nothing at all"


def test_the_title_comes_from_the_header_block_not_any_comment(tmp_path):
    """PICO-8's convention is `-- title` above the first line of code, and
    "above the first line of code" is the rule that matters.

    Scanning the whole cart for a comment finds whatever the author last
    commented OUT. `bunnysurvivor` has no header at all and imported under the
    title `print("kb"..stat(0),(playerx)-64,(player` -- a debug line it
    disabled on line 67."""
    header = {"lua": ["-- pico off road", "-- by assembler bot", "x=1"]}
    assert import_p8._title_from(header, "cart.p8") == "pico off road"

    # a blank line before the header is still the header
    padded = {"lua": ["", "-- moss moss", "-- by noel cody", "x=1"]}
    assert import_p8._title_from(padded, "cart.p8") == "moss moss"

    # NO header: the first `--` is code the author disabled, deep in the cart
    headless = {"lua": ["is_draw = 0", "kills = 0"] + [""] * 60
                       + ['--print("kb"..stat(0),(playerx)-64,2)']}
    assert import_p8._title_from(headless, "bunny_survivor-9.p8.png") \
        == "bunny survivor 9"

    # a cart whose author only credited themselves still gets a real name
    only_by = {"lua": ["-- by someone", "x=1"]}
    assert import_p8._title_from(only_by, "star_catcher.p8") == "star catcher"


def _run_p8(tmp_path, body, frames=60, dt=1.0 / 60):
    """Import a scrap of p8 source and run it on the real Player."""
    from ws_helpers import build_ws

    src = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
           + body)
    p8 = tmp_path / "probe.p8"
    p8.write_text(src, encoding="utf-8")
    ws = build_ws(tmp_path)
    out = Path(ws.carts_root) / "probe.moy"
    import_p8.import_p8(str(p8), str(out))
    cart = moy_carts.load(str(out))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(frames):
        ws.input.begin_frame()
        ws.frame(dt)
    assert ws.player.cart_error is None, ws.player.cart_error
    return ws


def test_a_cart_that_defines_update60_actually_updates(tmp_path):
    """A cart picks its rate by which lifecycle it DEFINES, and reading only
    `_update` did not make a 60fps cart slow -- it made it DEAD.

    `bunnysurvivor` defines `_update60` and nothing else, so its update never
    ran once: it drew its first frame forever and answered no input. Nothing
    errored, which is why it took playing the cart to notice. The report even
    said the import was clean."""
    _need_lua()
    ws = _run_p8(tmp_path, "ticks = 0\n"
                           "function _update60() ticks = ticks + 1 end\n"
                           "function _draw() cls(0) end\n",
                 frames=60, dt=1.0 / 60)
    assert ws.player._lua.get_global("ticks") == 60, \
        "_update60 must run once per frame at 60fps"


def test_update60_beats_update_when_a_cart_defines_both(tmp_path):
    """PICO-8's own precedence, and a cart that ships both is relying on it."""
    _need_lua()
    ws = _run_p8(tmp_path, "slow, fast = 0, 0\n"
                           "function _update() slow = slow + 1 end\n"
                           "function _update60() fast = fast + 1 end\n"
                           "function _draw() cls(0) end\n",
                 frames=60, dt=1.0 / 60)
    assert ws.player._lua.get_global("fast") == 60
    assert ws.player._lua.get_global("slow") == 0


def test_a_plain_update_cart_still_ticks_at_thirty(tmp_path):
    """The rate lock must not have moved the default."""
    _need_lua()
    ws = _run_p8(tmp_path, "ticks = 0\n"
                           "function _update() ticks = ticks + 1 end\n"
                           "function _draw() cls(0) end\n",
                 frames=30, dt=1.0 / 30)
    assert ws.player._lua.get_global("ticks") == 30


def test_rnd_of_a_table_returns_an_element_of_it(tmp_path):
    """`rnd(t)` on a TABLE is a different verb wearing the same name (p8
    0.2.0): it picks an element. The numeric form multiplied the table and the
    cart died on `attempt to perform arithmetic on a table value` -- on the
    first frame its update ever ran, which is how one bug hid behind another
    here."""
    _need_lua()
    ws = _run_p8(tmp_path, "picked, empty, num = 0, -1, -1\n"
                           "function _update()\n"
                           " picked = rnd({7, 7, 7})\n"
                           " empty = rnd({}) or -1\n"
                           " num = rnd(0)\n"
                           "end\n"
                           "function _draw() cls(0) end\n",
                 frames=4, dt=1.0 / 30)
    assert ws.player._lua.get_global("picked") == 7, "must pick an element"
    assert ws.player._lua.get_global("empty") == -1, "an empty table is nil"
    assert ws.player._lua.get_global("num") == 0, "the numeric form still works"


_BTNP_PROBE = ("edges, held, ticks = 0, 0, 0\n"
               "function %s()\n"
               " ticks = ticks + 1\n"
               " if btnp(1) then edges = edges + 1 end\n"
               " if btn(1) then held = held + 1 end\n"
               "end\n"
               "function _draw() cls(0) end\n")


def _tap(tmp_path, lifecycle, host_dt, hold_frames):
    """Import a btnp probe, hold `right` for `hold_frames` console frames."""
    from ws_helpers import build_ws

    src = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
           + _BTNP_PROBE % lifecycle)
    (tmp_path / "probe.p8").write_text(src, encoding="utf-8")
    ws = build_ws(tmp_path)
    out = Path(ws.carts_root) / "probe.moy"
    import_p8.import_p8(str(tmp_path / "probe.p8"), str(out))
    cart = moy_carts.load(str(out))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(host_dt)
    ws.input.set_held("right", True)
    for _ in range(hold_frames):
        ws.input.begin_frame()
        ws.frame(host_dt)
    ws.input.set_held("right", False)
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(host_dt)
    assert ws.player.cart_error is None, ws.player.cart_error
    lua = ws.player._lua
    return (lua.get_global("edges"), lua.get_global("held"),
            lua.get_global("ticks"))


@pytest.mark.parametrize("lifecycle", ["_update", "_update60"])
@pytest.mark.parametrize("host_dt", [1.0 / 60, 1.0 / 30])
def test_one_tap_is_one_btnp_edge_at_every_pair_of_rates(tmp_path, lifecycle,
                                                         host_dt):
    """The cart's rate and the host's are independent, and ONE press has to be
    one edge across every combination of them.

    Both directions have bitten. Reading the engine's edge directly ate half of
    a 30fps cart's presses, because that edge lives one CONSOLE frame and the
    cart ticks every other one. Latching fixed that and introduced the
    opposite: a 60fps cart on a 30fps host runs two ticks inside one console
    frame, the first cleared the latch, and the second still saw the engine's
    edge -- so one tap of left moved TWO slots in an upgrade menu. btnp reads
    the latch and nothing else now."""
    edges, held, _ = _tap(tmp_path, lifecycle, host_dt, hold_frames=1)
    assert edges == 1, "one tap must be one edge"
    assert held >= 1, "btn() must see the press at all"


@pytest.mark.parametrize("host_dt", [1.0 / 60, 1.0 / 30])
def test_a_short_hold_does_not_repeat_before_the_delay(tmp_path, host_dt):
    """The repeat below must not turn a slightly-long tap into two moves."""
    edges, _, _ = _tap(tmp_path, "_update60", host_dt, hold_frames=5)
    assert edges == 1


def test_btnp_repeats_while_held_on_the_carts_own_clock(tmp_path):
    """p8's btnp fires again after a 15-tick delay, then every 4 -- that is
    what scrolls a menu on a held button, and the cadence is counted in the
    CART's ticks, so it is the same hold at either host rate."""
    fast, _, ticks_fast = _tap(tmp_path, "_update60", 1.0 / 60, hold_frames=40)
    assert ticks_fast >= 40
    assert fast == 1 + (40 - 15 - 1) // 4 + 1, \
        "one press then a repeat every 4 ticks after 15"

    # Same CART ticks, half the console frames: the cadence must not change.
    slow, _, _ = _tap(tmp_path, "_update60", 1.0 / 30, hold_frames=20)
    assert slow == fast, "the repeat is the cart's clock, not the host's"


def test_releasing_resets_the_repeat(tmp_path):
    """Otherwise the next tap inherits the last hold's counter and fires twice."""
    from ws_helpers import build_ws

    src = ("pico-8 cartridge // http://www.pico-8.com\nversion 42\n__lua__\n"
           + _BTNP_PROBE % "_update60")
    (tmp_path / "probe.p8").write_text(src, encoding="utf-8")
    ws = build_ws(tmp_path)
    out = Path(ws.carts_root) / "probe.moy"
    import_p8.import_p8(str(tmp_path / "probe.p8"), str(out))
    cart = moy_carts.load(str(out))
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open()
    for held in (True, False, True, False):
        ws.input.set_held("right", held)
        for _ in range(20 if held else 5):
            ws.input.begin_frame()
            ws.frame(1.0 / 60)
    assert ws.player.cart_error is None, ws.player.cart_error
    per_hold = 1 + (20 - 15 - 1) // 4 + 1
    assert ws.player._lua.get_global("edges") == 2 * per_hold, \
        "each hold must start its repeat clock from zero"


def test_a_transparent_fillp_does_not_paint_the_screen_black(tmp_path):
    """p8's fillp() marks colour 1 TRANSPARENT with the pattern's fractional
    0.5 bit, and carts fade between scenes by setting one and filling the whole
    screen. The console fills solid, so ignoring that bit turns every fade into
    a black rectangle over the game -- strictly worse than the flat fill the
    rest of the pattern degrades to.

    The dithering itself is still not drawn; this pins the half that CAN be
    honoured exactly.
    """
    _need_lua()
    ws = _run_p8(tmp_path,
                 "function _update() end\n"
                 "function _draw()\n"
                 " cls(0)\n"
                 " rectfill(0,0,127,127,12)\n"          # a scene
                 " fillp(0b0011001100110011.1)\n"       # transparent overlay
                 " rectfill(0,0,127,127,0)\n"
                 " fillp()\n"
                 "end\n",
                 frames=6, dt=1.0 / 30)
    cv = ws.canvas
    lit = sum(1 for y in range(0, 100, 7) for x in range(0, 100, 7)
              if cv.pix(x, y) != 0)
    assert lit > 100, ("the transparent overlay painted the scene out -- "
                       "only %d of the sampled pixels survived" % lit)


def test_an_opaque_fillp_still_fills(tmp_path):
    """The transparency skip must not swallow an ordinary fill: a pattern with
    no fractional bit is opaque, and the console draws it solid."""
    _need_lua()
    ws = _run_p8(tmp_path,
                 "function _update() end\n"
                 "function _draw()\n"
                 " cls(0)\n"
                 " fillp(0b0011001100110011)\n"
                 " rectfill(0,0,127,127,12)\n"
                 " fillp()\n"
                 "end\n",
                 frames=6, dt=1.0 / 30)
    cv = ws.canvas
    lit = sum(1 for y in range(0, 100, 7) for x in range(0, 100, 7)
              if cv.pix(x, y) != 0)
    assert lit > 100, "an opaque fillp must still draw (%d lit)" % lit


def test_a_glyph_works_as_a_value_and_as_a_variable(tmp_path):
    """p8 lets a glyph be either, and carts use both.

    `fillp(<shade>)` wants the character's own code; `squiddy`, a 1k-jam cart,
    writes `<right> = 0` and uses the glyph as a VARIABLE to save bytes.
    Choosing one reading breaks the other -- emitting the bare number turned
    that assignment into `1 = 0`, which is not Lua. A predefined name is both.
    """
    _need_lua()
    ws = _run_p8(tmp_path,
                 "btnval, shade, asvar = -1, -1, -1\n"
                 "function _update()\n"
                 " btnval = \u27a1\ufe0f\n"       # the right-arrow glyph
                 " shade = \x87\n"                 # a non-button P8SCII glyph
                 " \u2b06\ufe0f = 42\n"           # assign THROUGH a glyph
                 " asvar = \u2b06\ufe0f\n"
                 "end\n"
                 "function _draw() cls(0) end\n",
                 frames=4, dt=1.0 / 30)
    lua = ws.player._lua
    assert lua.get_global("btnval") == 1, "the right arrow is button 1"
    assert lua.get_global("shade") == 0x87, \
        "a non-button glyph carries its own P8SCII code"
    assert lua.get_global("asvar") == 42, "a glyph must work as a variable"


# ---------------------------------------------------------------------------
# The importer also runs on MICROPYTHON -- the browser console converts a
# dropped cart in wasm, and that is how a cart reaches a Zero. That VM decodes
# UTF-8 and ONLY UTF-8: it accepts a codec name, ignores it, and raises
# UnicodeError with an EMPTY message on the first byte >= 0x80. It ignores an
# `errors` argument the same way. So `.decode("latin-1")` -- correct on CPython
# and the only decode that keeps a P8SCII glyph byte -- made every pxa-packed
# cart fail in the browser and nowhere else, reported as "did not decode ()".
# ---------------------------------------------------------------------------

def test_the_byte_decode_keeps_every_high_byte():
    """codepoint == byte value, for all 256, which is what latin-1 means and
    what the glyph mapping downstream depends on."""
    import p8_import
    raw = bytes(range(256))
    got = p8_import._latin1(raw)
    assert len(got) == 256
    assert [ord(c) for c in got] == list(range(256))


def test_the_importer_asks_for_no_codec_micropython_lacks():
    """THE PIN. A `.decode()` naming anything but utf-8, or passing an errors
    argument, works on the host and raises on the browser tier -- the exact
    shape of a bug that passed every test here while no cart could be imported
    there. Route byte-wise decodes through `_latin1`.

    Over the AST, not the text: the helper's own docstring quotes the call it
    exists to replace, and a regex cannot tell prose from code."""
    import ast
    tree = ast.parse((ROOT / "tools" / "p8_import.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "decode"):
            continue
        assert not node.keywords and len(node.args) <= 1, (
            "p8_import line %d passes an errors argument to .decode() -- "
            "MicroPython ignores it and raises anyway" % node.lineno)
        if node.args:
            codec = getattr(node.args[0], "value", None)
            assert codec in ("utf-8", "utf8"), (
                "p8_import line %d calls .decode(%r) -- MicroPython raises on "
                "any codec but utf-8; use _latin1()" % (node.lineno, codec))


def test_the_decode_runs_on_the_micropython_the_browser_uses(tmp_path):
    """The check that would actually have caught this, rather than describing
    it: run p8_import's byte decode on the OTHER VM.

    The static pin above says "do not name a codec"; this says "and the thing
    you wrote instead works there". The bug shipped because every test ran on
    CPython, where latin-1 exists -- so the tier that could not import a single
    cart was the one tier nothing exercised."""
    sys.path.insert(0, str(ROOT / "tests"))
    from unix_mp import require_unix_mp
    mp_exe = require_unix_mp(
        why="Without it the importer's decode is only ever run on CPython, "
            "which is the tier that was NOT broken.")
    script = tmp_path / "dec.py"
    script.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n" % str(ROOT / "tools") +
        "import p8_import\n"
        "got = p8_import._latin1(bytes(range(256)))\n"
        "print(len(got), min(ord(c) for c in got), max(ord(c) for c in got))\n",
        encoding="utf-8")
    import subprocess
    r = subprocess.run([str(mp_exe), str(script)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[:500]
    assert r.stdout.split() == ["256", "0", "255"], (r.stdout, r.stderr[:300])
