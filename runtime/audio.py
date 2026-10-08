"""Backend-agnostic audio core shared by the host and device consoles (#16).

Like runtime/editors.py, this is pure logic -- no I/O, no hardware, no canvas --
so the *same* file backs the host reference (imported as runtime.audio) and the
MicroPython device port (frozen as the top-level module `audio`, staged by
build.sh).

It holds the sound DATA MODEL -- the kid-authored, JSON-serializable cart audio:
       note   = [pitch, wave, vol] or [pitch, wave, vol, eff]   (the atom)
       SFX     = {speed, loop, steps:[note]}   (a short blip/effect)
       music   = {speed, loop, pattern:[row]}  (rows: one SFX id, or a list of
                                                up to 4 ids -- one per channel)
       AudioBank = {sfx:[SFX], music:[track]}  (the whole cart bank -> sounds.json)

The PLAYING is the kernel's: native/moy_audio (sessions, the mix, the speaker)
over vendored libmoy, reached through runtime/audio_session.py on every tier.

THERE IS NO PYTHON SYNTH (#97, moycore stage 0). The host binds the vendored
C itself (runtime/audio_binding.py, compiled DOUBLE-WIDENED, the parity
harness's recipe); without a C compiler the host has no audio module and plays
nothing (owner decision 2026-08-11: no fallback, KISS). tests/test_audio_parity.py
pins the binding against the reference render.

SPEC.md 8.3 exempts audio from pixel conformance, so cart mixes are balanced
against PICO-8's deliberately unequal instrument loudness (via zepto8/fake-08);
fix a wrong-sounding cart in its sounds.json, never by touching libmoy locally
(see native/moy_audio/libmoy/UPSTREAM.md -- fixes go upstream, `make
vendor-libmoy` brings them back).

See docs/audio_design_v04.md for the data-model design history.
"""

import math

# -- waveforms ---------------------------------------------------------------
# 0-3 are the original four (frozen -- existing banks index them); 4-7 are the
# PICO-8-parity additions (#170: "p8 is the least fidelity we offer"), so the
# eight cover every p8 instrument 1:1 and a ported cart keeps its timbres.
# Loudness is deliberately UNEQUAL between families (SPEC.md 8.3): the square
# family peaks at 0.25, the triangle family at 0.5. That is PICO-8's own mix, and
# ported music is balanced against it -- render them equal and every square lead
# shouts down its own accompaniment.
WAVE_SQUARE = 0
WAVE_TRIANGLE = 1
WAVE_SAW = 2
WAVE_NOISE = 3      # LCG walk through a one-pole low-pass that tracks the note
WAVE_PULSE = 4      # narrow square (1/3 duty) -- thinner, reedier than square
WAVE_ORGAN = 5      # triangle with a quieter octave-up partner
WAVE_TILTED = 6     # tilted saw (rise over 7/8 of the period, fall over 1/8)
WAVE_PHASER = 7     # two triangles, the second detuned to freq*109/110, beating

# -- per-note effects (the optional 4th field; PICO-8 numbering, #170) --------
FX_NONE = 0
FX_SLIDE = 1        # glide from the channel's previous note -- linear in Hz,
                    # not in semitones (PICO-8/zepto8); on a wide slide the two
                    # curves are audibly different
FX_VIBRATO = 2      # +-0.25 semitone triangle wobble at 7.5 Hz
FX_DROP = 3         # frequency falls linearly to 0 across the step
FX_FADE_IN = 4      # volume ramps 0 -> vol
FX_FADE_OUT = 5     # volume ramps vol -> 0
FX_ARP_FAST = 6     # arpeggio over the step's group of 4 at 30 notes/sec --
                    # 60 on a fast SFX (15+ steps/s), as PICO-8 does
FX_ARP_SLOW = 7     # the same at 15 notes/sec (30 on a fast SFX)

# Channels: four simultaneous voices. Music claims voices from the TOP (a
# 1-channel phrase owns voice 3; an N-channel track claims voices 3..4-N), and
# SFX round-robin across whatever music leaves free, so an effect never cuts the
# background loop. With NO music playing all four are free to effects.
CHANNELS = 4
MUSIC_CHANNEL = CHANNELS - 1

# A rest/off pitch (silent step). Real pitches are semitone indices 0..95 (C0..B7).
REST = -1

_A4_PITCH = 57          # semitone index of A4 (octave 4, note A) -> 440 Hz
_A4_FREQ = 440.0

# Note names -> offset within an octave (sharps only; kid editor uses these).
_NOTE_OFFSETS = {
    "C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
    "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11,
}


def note_to_freq(pitch):
    """Equal-temperament Hz for a semitone index (A4=440). REST/negative -> 0."""
    if pitch is None or pitch < 0:
        return 0.0
    return _A4_FREQ * (2.0 ** ((pitch - _A4_PITCH) / 12.0))


def name_to_pitch(name):
    """'C4' / 'A#3' -> semitone index 0..95. Lets carts/editor use note names."""
    s = str(name).strip().upper()
    if not s:
        return REST
    # split letter(+#) from the trailing octave digit
    i = 1
    if len(s) > 1 and s[1] == "#":
        i = 2
    key = s[:i]
    try:
        octave = int(s[i:])
    except ValueError:
        return REST
    off = _NOTE_OFFSETS.get(key)
    if off is None:
        return REST
    return octave * 12 + off


def _clampi(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# -- data model --------------------------------------------------------------

class SFX:
    """A short sound effect: a list of [pitch, wave, vol] (or [pitch, wave, vol,
    eff]) steps at `speed` steps/second. Plain data; round-trips through
    to_dict/from_dict (JSON). The 4th field is the OPTIONAL per-note effect
    (#170, PICO-8 numbering, FX_*); a step without one serializes 3-element so
    pre-#170 banks stay byte-identical on disk."""

    def __init__(self, steps=None, speed=8, loop=False, loop_start=0,
                 filters=0):
        # normalize each step to a [pitch, wave, vol(, eff)] list of ints
        self.steps = [self._norm(s) for s in (steps or [])]
        # FRACTIONAL speeds are legal, exactly as libmoy declares them
        # ("float speed; fractions legal"; <= 0 falls to the SPEC default 8).
        # The old max(1, int(speed)) was a pre-#151 leftover -- the music side
        # learned fractional speeds, this side never did -- and it truncated
        # the p8 imports' 7.5/3.75 melodies so every phrase overran its music
        # row and got RETRIGGERED early: the tune audibly hurries, while every
        # tempo clock measures exact (the 2026-08-10 "sped up on device" hunt --
        # device AND host sim both played the truncation; only a raw-file
        # libmoy host played the cart as authored).
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 8.0
        self.speed = speed if speed > 0 else 8.0
        self.loop = bool(loop)
        # Where a looping SFX jumps BACK to (#170: the p8 loop range -- play
        # 0..end once, then repeat loop_start..end). 0 = loop the whole list,
        # which is the pre-#170 behaviour, so old banks are untouched.
        self.loop_start = max(0, int(loop_start))
        # PICO-8's per-sfx FILTER byte, carried verbatim the way `eff` is:
        # noiz 0x2, buzz 0x4, then detune/reverb/dampen as base-3 digits at
        # /8, /24 and /72. 0 is the dry sound, so every bank written before
        # this is unchanged. libmoy's moy_audio.h holds the accessors.
        try:
            self.filters = _clampi(int(filters), 0, 255)
        except (TypeError, ValueError):
            self.filters = 0

    @staticmethod
    def _norm(s):
        pitch = int(s[0]) if len(s) > 0 and s[0] is not None else REST
        wave = int(s[1]) if len(s) > 1 else WAVE_SQUARE
        vol = int(s[2]) if len(s) > 2 else 6
        eff = int(s[3]) if len(s) > 3 else 0
        step = [pitch, _clampi(wave, 0, 7), _clampi(vol, 0, 7)]
        if eff:
            step.append(_clampi(eff, 0, 7))
        return step

    def to_dict(self):
        d = {"speed": self.speed, "loop": self.loop,
             "steps": [list(s) for s in self.steps]}
        if self.loop_start:
            d["loop_start"] = self.loop_start
        if self.filters:
            d["filters"] = self.filters
        return d

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("steps"), d.get("speed", 8), d.get("loop", False),
                   d.get("loop_start", 0), d.get("filters", 0))


class MusicTrack:
    """A looping phrase: an ordered list of pattern ROWS played at `speed`
    slots/second. A row is one SFX id (the original 1-channel form) OR a list
    of up to CHANNELS ids by channel position (#170 -- multi-channel music, the
    p8-parity form); -1 in a list means that channel is silent this row. Ints
    stay ints through to_dict so pre-#170 banks serialize unchanged.

    `row_secs` (optional, #170) is a parallel list of PER-ROW durations in
    seconds, overriding the uniform speed clock -- what a p8 song needs, since
    its pattern length follows the first NON-LOOPING channel and that channel's
    tempo differs row to row. An entry of 0 means "hold this row forever"
    (every channel loops -- p8's infinite pattern); music_stop()/music(n) still
    end it. Absent (the kid-authored case) the speed clock rules alone."""

    def __init__(self, pattern=None, speed=4, loop=True, row_secs=None):
        self.pattern = [self._norm_row(r) for r in (pattern or [])]
        # fractional speeds are legal (#151: a ported PICO-8 row lasts its
        # whole 32-note SFX -- e.g. 0.117 slots/sec); int carts unchanged.
        self.speed = max(0.01, float(speed))
        self.loop = bool(loop)
        self.row_secs = ([max(0.0, float(v)) for v in row_secs]
                         if row_secs else None)

    @staticmethod
    def _norm_row(r):
        if isinstance(r, (list, tuple)):
            row = [int(n) for n in r][:CHANNELS]
            return row if row else -1
        return int(r)

    def to_dict(self):
        d = {"speed": self.speed, "loop": self.loop,
             "pattern": [list(r) if isinstance(r, list) else r
                         for r in self.pattern]}
        if self.row_secs:
            d["row_secs"] = list(self.row_secs)
        return d

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("pattern"), d.get("speed", 4), d.get("loop", True),
                   d.get("row_secs"))


class AudioBank:
    """A cart's whole sound bank: SFX list + music tracks. Serializes to the
    cart's sounds.json via to_dict/from_dict."""

    def __init__(self, sfx=None, music=None):
        self.sfx = list(sfx or [])
        self.music = list(music or [])
        # Revision counter, bumped by touch() on every edit. The Music editor
        # mutates this bank IN PLACE and the running cart hears the result, but
        # the synth is the kernel's (native/moy_audio), holding its own parsed
        # copy per session. A session compares rev before a verb it plays and
        # re-pushes the bank when it moved, and touch() pushes to every session
        # open over this bank, because a Lua or compiled cart's verbs play in C
        # and never reach the Python session at all.
        self.rev = 0
        self.sessions = []

    def touch(self):
        """Mark the bank edited (see `rev`), and push it to every session
        open over it."""
        self.rev += 1
        for s in self.sessions:
            s._sync()

    def get_sfx(self, n):
        if 0 <= n < len(self.sfx):
            return self.sfx[n]
        return None

    def get_music(self, n):
        if 0 <= n < len(self.music):
            return self.music[n]
        return None

    def to_dict(self):
        return {"sfx": [s.to_dict() for s in self.sfx],
                "music": [m.to_dict() for m in self.music]}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        sfx = [SFX.from_dict(s) for s in d.get("sfx", [])]
        music = [MusicTrack.from_dict(m) for m in d.get("music", [])]
        return cls(sfx, music)

    @classmethod
    def default(cls):
        """A small friendly starter bank so a new cart / the editor is never empty:
          sfx 0 -- a rising coin blip
          sfx 1 -- a short jump
          sfx 2 -- a low thud
          music 0 -- a tiny looping phrase built from those SFX
        """
        coin = SFX([[name_to_pitch("E5"), WAVE_SQUARE, 6],
                    [name_to_pitch("A5"), WAVE_SQUARE, 6],
                    [name_to_pitch("C6"), WAVE_SQUARE, 5]], speed=16)
        jump = SFX([[name_to_pitch("C4"), WAVE_TRIANGLE, 6],
                    [name_to_pitch("G4"), WAVE_TRIANGLE, 6],
                    [name_to_pitch("C5"), WAVE_TRIANGLE, 4]], speed=20)
        thud = SFX([[name_to_pitch("C2"), WAVE_NOISE, 7],
                    [name_to_pitch("C2"), WAVE_NOISE, 3]], speed=14)
        loop = MusicTrack([0, 1, 0, 2], speed=4, loop=True)
        return cls([coin, jump, thud], [loop])


def freq_to_pitch(freq):
    """Nearest semitone index for a frequency (inverse of note_to_freq)."""
    return int(round(_A4_PITCH + 12.0 * math.log(freq / _A4_FREQ, 2)))
