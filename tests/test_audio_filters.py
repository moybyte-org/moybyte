"""SPEC.md 8.1's per-sfx `filters` byte -- PICO-8's noiz/buzz/detune/reverb/
dampen -- proved to BITE, not merely to be carried.

These five went missing for a reason worth remembering: nothing crashed and no
test failed when the import dropped the byte, because a dropped filter is not
an error, it is a quieter, duller, drier cart. `moy_audio.c`'s own header even
said it implemented "the non-buzz variants", which was true, and was silently
half the timbre of every cart written since PICO-8 0.2.4. One measured cart
(`crimson night`) used reverb on 17 of its 23 sounds, dampen on 14, detune on
13, buzz on 12 and noiz on 8.

So each test here asserts a MEASURABLE consequence -- a spectrum tilted, a tail
that outlives its note, a beat that was not there -- rather than that a field
survived a round trip. A field surviving is what we already had.
"""
import array
import math
import os
import shutil

import pytest

from runtime.audio import AudioBank, AudioEngine

RATE = 22050
A4 = 57                      # moy semitone 57 = A4 = 440 Hz
TRIANGLE, SAW, NOISE = 1, 2, 3

# The byte's five fields, spelled the way moy_audio.h reads them.
NOIZ, BUZZ = 0x2, 0x4
DETUNE1, DETUNE2 = 8, 16
REVERB1, REVERB2 = 24, 48
DAMPEN1, DAMPEN2 = 72, 144

# Without a C compiler the host plays SILENCE by design (owner call
# 2026-08-11), so there is nothing here to measure -- skip rather than assert
# that silence equals silence.
pytestmark = pytest.mark.skipif(
    not (os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")),
    reason="no C compiler to build the libmoy binding")


def render(filters=0, wave=TRIANGLE, steps=None, frames=8192, loop=True,
           speed=2):
    bank = AudioBank.from_dict({
        "sfx": [{"speed": speed, "loop": loop, "filters": filters,
                 "steps": steps or [[A4, wave, 7], [A4, wave, 7]]}],
        "music": [],
    })
    engine = AudioEngine(bank, rate=RATE)
    engine.play_sfx(0)
    pcm = array.array("h")
    pcm.frombytes(bytes(engine.render(frames)))
    return list(pcm)


def mag(pcm, freq):
    """|X(freq)|, probed exactly ON the harmonic.

    Probing NEAR one measures spectral leakage instead, which moves with phase
    and says nothing about a filter -- the first draft of this file did that
    and reported a linear filter boosting its own input by 7 dB."""
    w = 2.0 * math.pi * freq / RATE
    re = sum(s * math.cos(w * i) for i, s in enumerate(pcm))
    im = sum(s * math.sin(w * i) for i, s in enumerate(pcm))
    return math.hypot(re, im)


def db(after, before):
    return 20.0 * math.log10(max(after, 1e-9) / max(before, 1e-9))


def rms(pcm):
    return math.sqrt(sum(float(s) * s for s in pcm) / max(1, len(pcm)))


def test_a_bank_with_no_filters_is_byte_identical():
    """The whole set is ADDITIVE: every cart written before it is untouched."""
    assert render(0) == render(0)
    plain = AudioBank.from_dict({"sfx": [{"speed": 2, "loop": False,
                                          "steps": [[A4, TRIANGLE, 7]]}],
                                 "music": []})
    assert "filters" not in plain.sfx[0].to_dict()


@pytest.mark.parametrize("filters,knee,floor_db", [
    (DAMPEN1, 2400.0, -6.0),
    (DAMPEN2, 1000.0, -12.0),
])
def test_dampen_is_a_high_shelf_at_its_stated_corner(filters, knee, floor_db):
    """Flat at DC, half the shelf at the corner, the full shelf well above it.

    That three-point shape is what makes it a SHELF rather than "quieter" --
    a plain gain would fail the first point and a low-pass the third."""
    dry, wet = render(0), render(filters)
    at_dc = db(mag(wet, 440.0), mag(dry, 440.0))
    assert abs(at_dc) < 1.0, "a high shelf must leave the fundamental alone"

    # The cookbook's corner is where the shelf is HALF applied (in dB).
    harmonic = min((440.0 * k for k in (1, 3, 5, 7, 9, 11, 13)),
                   key=lambda f: abs(f - knee))
    at_knee = db(mag(wet, harmonic), mag(dry, harmonic))
    assert floor_db < at_knee < 0.0

    top = db(mag(wet, 5720.0), mag(dry, 5720.0))
    assert abs(top - floor_db) < 1.5, "the shelf must reach its stated depth"


def test_dampen_2_cuts_deeper_than_dampen_1():
    dry, one, two = render(0), render(DAMPEN1), render(DAMPEN2)
    f = 3080.0
    assert db(mag(two, f), mag(dry, f)) < db(mag(one, f), mag(dry, f))


# A 1/8 s blip inside a 1/2 s render: the note is long over by `TAIL`, so
# anything still sounding there came from the delay line and nowhere else.
BLIP = dict(steps=[[A4, TRIANGLE, 7]], frames=int(RATE * 0.5), loop=False,
            speed=8)
TAIL = int(RATE * 0.20)          # 75 ms after the blip ends, well inside the
                                 # decay but well outside the note


@pytest.mark.parametrize("filters", [REVERB1, REVERB2])
def test_reverb_outlives_the_note_that_made_it(filters):
    """The tail is the point, and it is the part a per-note effect cannot do."""
    dry, wet = render(0, **BLIP), render(filters, **BLIP)
    assert rms(dry[TAIL:]) < 1.0, "the dry blip must really be over"
    assert rms(wet[TAIL:]) > 4.0, "the reverb tail must still be sounding"


def test_reverb_2_rings_longer_than_reverb_1():
    """33 ms of delay line holds more of a finished note than 16.6 ms does."""
    one, two = render(REVERB1, **BLIP), render(REVERB2, **BLIP)
    assert rms(two[TAIL:]) > rms(one[TAIL:])


@pytest.mark.parametrize("filters", [DETUNE1, DETUNE2])
def test_detune_adds_a_second_oscillator(filters):
    """Detune is a PARTNER, not a pitch offset: the note keeps its own
    fundamental and something new appears beside it."""
    dry, wet = render(0), render(filters)
    assert db(mag(wet, 440.0), mag(dry, 440.0)) > -3.0, "the note itself stays"
    assert rms(wet) > rms(dry) * 1.02, "and there is audibly more sound"


def test_buzz_selects_a_different_waveform_not_a_filter():
    """Every instrument has a harsher twin. The triangle's averages in a
    tilted saw, which shows up as harmonics the plain triangle does not have.
    """
    dry, wet = render(0, wave=TRIANGLE), render(BUZZ, wave=TRIANGLE)
    assert dry != wet
    even = sum(mag(wet, 880.0 * k) for k in (1, 2, 3))
    even_dry = sum(mag(dry, 880.0 * k) for k in (1, 2, 3))
    assert even > even_dry, "buzz must add harmonics, not just level"


def test_buzz_reaches_every_pitched_instrument():
    """It is per-INSTRUMENT arithmetic, so a twin missing from one shape is a
    silent hole -- exactly the class of gap this whole file exists for.

    NOISE is the deliberate exception: PICO-8 gives it `noiz` and no buzz
    variant, so it is asserted exempt here rather than left unmentioned."""
    for wave in range(8):
        if wave == NOISE:
            assert render(0, wave=wave) == render(BUZZ, wave=wave), \
                "noise must stay buzz-exempt"
            continue
        assert render(0, wave=wave) != render(BUZZ, wave=wave), \
            "wave %d has no buzz variant" % wave


def test_noiz_shapes_the_noise_instrument_and_only_that_one():
    """`noiz` multiplies the noise walk by a sawtooth. It is the one filter
    that is defined for a single instrument, and claiming otherwise would make
    every other cart's timbre drift for no reason."""
    assert render(0, wave=NOISE) != render(NOIZ, wave=NOISE)
    for wave in (TRIANGLE, SAW):
        assert render(0, wave=wave) == render(NOIZ, wave=wave)


def test_the_five_fields_are_independent():
    """They share one byte via /8, /24, /72 base-3 digits, so an arithmetic
    slip in one accessor would quietly turn on another."""
    seen = {}
    for name, bits in (("noiz", NOIZ), ("buzz", BUZZ), ("detune1", DETUNE1),
                       ("detune2", DETUNE2), ("reverb1", REVERB1),
                       ("reverb2", REVERB2), ("dampen1", DAMPEN1),
                       ("dampen2", DAMPEN2)):
        seen[name] = render(bits, wave=SAW)
    assert len(set(map(tuple, seen.values()))) == len(seen), \
        "two settings produced identical audio: %r" % sorted(seen)
