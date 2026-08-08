"""Backend-agnostic audio core shared by the host and device consoles (#16).

Like runtime/editors.py, this is pure logic -- no I/O, no hardware, no canvas --
so the *same* file backs the host reference (imported as runtime.audio) and the
MicroPython device port (frozen as the top-level module `audio`, staged by
build.sh). Only `math` is imported, so it freezes cleanly and runs under both
CPython and MicroPython.

It holds two things:

  1. The sound DATA MODEL -- the kid-authored, JSON-serializable cart audio:
       note   = [pitch, wave, vol] or [pitch, wave, vol, eff]   (the atom)
       SFX     = {speed, loop, steps:[note]}   (a short blip/effect)
       music   = {speed, loop, pattern:[row]}  (rows: one SFX id, or a list of
                                                up to 4 ids -- one per channel)
       AudioBank = {sfx:[SFX], music:[track]}  (the whole cart bank -> sounds.json)

  2. A software SYNTH + MIXER (AudioEngine) that turns that model into signed-16-bit
     mono PCM via render(nframes). render() is the seam the HOST backends consume
     (an SDL stream, a test recorder, the web view's per-frame PCM).

THE SYNTH IS A TWIN OF libmoy, NOT AN ORIGINAL (#97)
The synthesis below -- the eight waveshapes, the effect table, the sequencers, the
mixdown -- is a line-for-line port of the vendored
firmware/lilygo_t_deck_plus_micropython/native/moy_audio/libmoy/moy_audio.c, which
is moy-spec's own C implementation of SPEC.md 8. The DEVICE and the web runner
compile that file directly, so their audio is the spec by construction; this
module exists because the host sim runs CPython and linking C would put a compiler
in `make setup`.

That makes this file the one place moybyte's audio can drift from the spec, and
SPEC.md 8.3 deliberately exempts audio from the pixel conformance that catches
such drift everywhere else. So it is pinned instead: tests/test_audio_parity.py
compiles the vendored source and compares level, pitch and waveform shape
scenario by scenario. **Change the synthesis here only to follow libmoy**, and run
that test -- the arithmetic is PICO-8's measured output (via zepto8/fake-08), not
anything that should be "improved" locally.

See docs/audio_design_v04.md for the full design and the device-verification TODO.
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

    def __init__(self, steps=None, speed=8, loop=False, loop_start=0):
        # normalize each step to a [pitch, wave, vol(, eff)] list of ints
        self.steps = [self._norm(s) for s in (steps or [])]
        self.speed = max(1, int(speed))
        self.loop = bool(loop)
        # Where a looping SFX jumps BACK to (#170: the p8 loop range -- play
        # 0..end once, then repeat loop_start..end). 0 = loop the whole list,
        # which is the pre-#170 behaviour, so old banks are untouched.
        self.loop_start = max(0, int(loop_start))

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
        return d

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("steps"), d.get("speed", 8), d.get("loop", False),
                   d.get("loop_start", 0))


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

    def row_dur(self, i):
        """Row i's duration in seconds (None = hold forever).

        Once a track carries row_secs at all, it governs EVERY row: libmoy holds
        a fixed row_secs array that the parser zero-fills, and 0 means hold, so a
        row past the end of the authored list holds rather than quietly
        reverting to the speed clock. Matching that keeps a short-row_secs cart
        sounding the same here and on a libmoy host."""
        if self.row_secs:
            v = self.row_secs[i] if 0 <= i < len(self.row_secs) else 0.0
            return None if v <= 0 else v
        return 1.0 / self.speed

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
        # mutates this bank IN PLACE and the running cart hears the result, which
        # works for free on the host (one object, one engine) but not on a device
        # or the web runner, where the synth is libmoy holding its own parsed
        # copy. Those backends compare rev before a trigger and re-push the bank
        # when it moved -- an int compare per sfx() rather than a re-parse.
        self.rev = 0

    def touch(self):
        """Mark the bank edited (see `rev`)."""
        self.rev += 1

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


# -- synth + mixer -----------------------------------------------------------
#
# Line-for-line from native/moy_audio/libmoy/moy_audio.c (see the module
# docstring). Function names and the order of operations deliberately track that
# file so the two can be diffed by eye; where the C has a pointer or a fixed
# array this has the Python object the data model already provides.


# The semitone ratios libmoy's pitch_hz interpolates between. Copied digit for
# digit, INCLUDING the 7-digit truncation -- see _pitch_hz.
_SEMI = (
    1.0, 1.059463, 1.122462, 1.189207, 1.259921, 1.334840,
    1.414214, 1.498307, 1.587401, 1.681793, 1.781797, 1.887749,
)


def _pitch_hz(semitone):
    """Equal-temperament Hz for a (possibly fractional -- effects bend it)
    semitone index, A4 = 57 = 440 Hz.

    Octave doublings, a 12-entry table, and a quadratic for the FRACTIONAL
    semitone that vibrato and slide produce. libmoy computes it this way so a
    firmware needn't link libm; CPython has math and `_A4_FREQ * 2.0 ** ((n -
    _A4_PITCH) / 12.0)` would be shorter AND more accurate -- but it is not the
    same, and "more accurate" is the wrong axis. The table is truncated at seven
    digits and the quadratic is good to about a hundredth of a cent, so the
    closed form drifts from it by ~6e-6 relative. Inaudible, and still enough to
    move a square-wave edge by a whole sample after a few thousand phase
    accumulations -- which is exactly the difference between a twin that can be
    proven bit-identical to libmoy and one that can only be argued about. The
    tuning is part of what was adopted."""
    n = semitone - 57.0
    oct_ = 0
    while n < 0.0:
        n += 12.0
        oct_ -= 1
    while n >= 12.0:
        n -= 12.0
        oct_ += 1
    idx = int(n)
    frac = n - idx
    base = _SEMI[idx] * (1.0 + frac * (0.0577623 + frac * 0.0016682))
    while oct_ > 0:
        base *= 2.0
        oct_ -= 1
    while oct_ < 0:
        base *= 0.5
        oct_ += 1
    return 440.0 * base


def _tri_wave(p):
    """The triangle LFO the vibrato effect rides (NOT the triangle instrument)."""
    d = p - 0.5
    if d < 0.0:
        d = -d
    return 4.0 * d - 1.0


def _lcg_unit(rng):
    """One step of the noise LCG -> (sample in [-1, 1), new state)."""
    rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
    return (((rng >> 16) & 0x7FFF) / 16384.0 - 1.0), rng


def _wave_sample(wave, p, phase2, nto):
    """SPEC.md 8.3's eight shapes at phase `p` in [0, 1).

    This is PICO-8's synthesis arithmetic (from zepto8/fake-08's reverse
    engineering), INCLUDING the deliberately unequal loudness per instrument:
    the square family peaks at 0.25, the triangle family at 0.5. Ported music is
    balanced against exactly this. Noise is the one stateful shape -- it is
    computed where the note's frequency is known and merely held here."""
    if wave == WAVE_SQUARE:
        return 0.25 if p < 0.5 else -0.25
    if wave == WAVE_TRIANGLE:
        return (1.0 - abs(4.0 * p - 2.0)) * 0.5
    if wave == WAVE_SAW:
        return 0.653 * (p if p < 0.5 else p - 1.0)
    if wave == WAVE_NOISE:
        return nto
    if wave == WAVE_PULSE:
        return 0.25 if p < (1.0 / 3.0) else -0.25
    if wave == WAVE_ORGAN:
        return ((3.0 - abs(24.0 * p - 6.0)) if p < 0.5
                else (1.0 - abs(16.0 * p - 12.0))) / 9.0
    if wave == WAVE_TILTED:
        return ((2.0 * p / 0.875 - 1.0) if p < 0.875
                else (2.0 * (1.0 - p) / 0.125 - 1.0)) * 0.5
    # phaser: two triangles, the second detuned to freq * 109/110
    return (2.0 - abs(8.0 * p - 4.0) + 1.0 - abs(4.0 * phase2 - 2.0)) / 6.0


class _Voice:
    """One channel. The mirror of libmoy's `moy_voice`: a reference to the SFX
    being played plus the cursor and oscillator state that advance under it."""

    def __init__(self):
        self.owner = 0          # 0 free, 1 the sfx verb, 2 music
        self.sfx = None         # the SFX object being played (libmoy's v->s)
        self.step = 0           # index into sfx.steps
        self.samp = 0           # samples into the current step -- an INTEGER,
                                # so a step boundary stays exact at any length
        self.phase = 0.0        # oscillator phase 0..1
        self.phase2 = 0.0       # the phaser's detuned partner
        self.rng = 0            # noise LCG state
        self.nfrom = 0.0        # noise: low-pass filter state
        self.nto = 0.0          # noise: shaped output, held between updates
        self.amp = 0.0          # de-click amplitude slew (current gain)
        # The channel's previous SOUNDING note -- what FX_SLIDE glides from.
        # Survives retriggers on purpose: SPEC.md 8.1 says a slide carries
        # across a row boundary. -1 means no previous note yet.
        self.prev_pitch = -1.0
        self.prev_vol = 0.0
        # Monotonic trigger counter, bumped on every (re)trigger and stop. Not
        # part of libmoy: it lets a consumer detect a fresh play unambiguously
        # without comparing object identity, which the GC makes unreliable.
        self.gen = 0

    @property
    def active(self):
        return self.owner != 0

    def start(self, sfx, owner):
        """Trigger `sfx` on this channel. prev_pitch/prev_vol and the noise
        filter state deliberately SURVIVE -- the slide's origin is whatever this
        channel last played, and PICO-8 keeps its per-channel noise walk
        running. The amplitude slew restarts from 0: a retrigger resets the
        oscillator phase, and the short ramp is what keeps that from clicking."""
        self.owner = owner
        self.sfx = sfx
        self.step = 0
        self.samp = 0
        self.phase = 0.0
        self.phase2 = 0.0
        self.amp = 0.0
        if not self.rng:
            self.rng = 0x2F9E2B1
        self.gen += 1

    def stop(self):
        self.owner = 0
        self.sfx = None
        self.gen += 1


class AudioEngine:
    """Software synth + mixer. Holds the bank and the live voices; render(nframes)
    pulls signed-16-bit mono PCM, mixing every active voice plus the beep
    oscillator. This is the single primitive the host backends consume."""

    def __init__(self, bank=None, rate=11025):
        self.bank = bank if bank is not None else AudioBank.default()
        self.rate = int(rate)
        self.voices = [_Voice() for _ in range(CHANNELS)]
        self.master = 7             # SPEC.md 8.2 volume(level): 0..7
        self._rr = 0                # sfx round-robin cursor
        # music sequencer
        self.track = None
        self._track_width = 0
        self._mrow = 0
        self._msamp = 0             # samples into the row, same integer rule
        self._mloop = True
        # beep: engine-native, OUTSIDE the four channels, so a beep never steals
        # a voice from music or an effect (SPEC.md 8.2).
        self._bfreq = 0.0
        self._bleft = 0.0
        self._bphase = 0.0

    # -- control ---------------------------------------------------------

    def set_volume(self, level):
        """Master output level, 0..7 -- the same scale as a note's `vol`, and
        what libmoy and SPEC.md 8.2 mean by volume(level)."""
        try:
            v = int(level)
        except (TypeError, ValueError):
            return
        self.master = 0 if v < 0 else (7 if v > 7 else v)

    def play_sfx(self, n, chan=None):
        """Play SFX `n`. `chan` forces a channel; otherwise round-robin whatever
        music leaves free -- a track of width W owns voices 3..4-W, so the pool
        is 0..3-W. When a 4-channel track owns every voice, steal voice 0 (the
        track's last, typically least melodic, line) rather than drop the
        effect."""
        b = self.bank
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if b is None or n < 0 or n >= len(b.sfx):
            return
        s = b.sfx[n]
        if chan is not None:
            c = int(chan)
            if 0 <= c < CHANNELS:
                self.voices[c].start(s, 1)
                return
        free_top = CHANNELS - (self._track_width if self.track is not None else 0)
        if free_top <= 0:
            self.voices[0].start(s, 1)
            return
        for i in range(free_top):           # prefer an idle voice, in order
            if not self.voices[i].owner:
                self.voices[i].start(s, 1)
                return
        self.voices[self._rr % free_top].start(s, 1)
        self._rr = (self._rr % free_top + 1) % free_top

    def play_beep(self, freq, dur=0.15):
        """A tone at `freq` Hz for `dur` seconds -- square at vol 6 (SPEC.md
        8.2). The zero-data escape hatch: an exact frequency (not snapped to a
        semitone) on the engine's own oscillator, so it costs no channel."""
        try:
            freq = float(freq)
            dur = float(dur)
        except (TypeError, ValueError):
            return
        if freq <= 0.0 or dur <= 0.0:
            return
        self._bfreq = freq
        self._bleft = dur
        self._bphase = 0.0

    @staticmethod
    def freq_to_pitch(freq):
        """Nearest semitone index for a frequency (inverse of note_to_freq)."""
        return int(round(_A4_PITCH + 12.0 * math.log(freq / _A4_FREQ, 2)))

    @staticmethod
    def _track_channels(m):
        """How many voices a track claims: the widest row's channel count."""
        w = 0
        for r in m.pattern:
            n = len(r) if isinstance(r, (list, tuple)) else 1
            if n > w:
                w = n
        return w if w < CHANNELS else CHANNELS

    def _music_row_start(self):
        """Start the current row: channel j plays on voice 3-j (SPEC.md 8.1).
        A -1 (or out-of-range) id means that channel is silent this row."""
        m = self.track
        b = self.bank
        row = m.pattern[self._mrow]
        ids = row if isinstance(row, (list, tuple)) else (row,)
        for j in range(self._track_width):
            v = self.voices[MUSIC_CHANNEL - j]
            sid = ids[j] if j < len(ids) else -1
            if sid is None or sid < 0 or sid >= len(b.sfx):
                v.stop()
            else:
                v.start(b.sfx[sid], 2)

    def play_music(self, track, loop=True):
        """Start music track `track`, claiming channels from the top. `loop`
        overrides the track's own flag. Out of range is a no-op."""
        b = self.bank
        try:
            t = int(track)
        except (TypeError, ValueError):
            return
        if b is None or t < 0 or t >= len(b.music):
            return
        for v in self.voices:               # release the old track's voices
            if v.owner == 2:
                v.stop()
        m = b.music[t]
        self.track = m
        self._track_width = self._track_channels(m)
        self._mrow = 0
        self._msamp = 0
        self._mloop = m.loop if loop is None else bool(loop)
        if not m.pattern:
            self.track = None
            self._track_width = 0
            return
        self._music_row_start()

    def stop_music(self):
        self.track = None
        self._track_width = 0
        for v in self.voices:
            if v.owner == 2:
                v.stop()

    def stop(self, chan=None):
        """Stop one channel, or everything (voices, music and the beep) when
        chan is None."""
        if chan is not None:
            c = int(chan)
            if 0 <= c < CHANNELS:
                self.voices[c].stop()
            return
        for v in self.voices:
            v.stop()
        self._bleft = 0.0
        self.track = None
        self._track_width = 0

    def is_active(self):
        """True if anything is currently producing sound."""
        if self.track is not None or self._bleft > 0.0:
            return True
        for v in self.voices:
            if v.owner:
                return True
        return False

    # -- rendering -------------------------------------------------------

    def _voice_sample(self, v, dt, rate):
        """One sample from one voice: SPEC.md 8.1's effect table, evaluated.

        Time is counted in integer SAMPLES and converted by one multiply, so a
        step boundary lands within a sample of where `speed` says it should --
        a float seconds accumulator drifts, and at 30 steps/s that is audible.

        The last stage is a ~1.5 ms amplitude slew toward the note's level. It
        is not in the spec and needs not to be: it is de-clicking, the same
        smoothing PICO-8 applies. A retriggered oscillator restarts at phase 0,
        and without the ramp every fast sfx chain carries a click per step."""
        s = v.sfx
        if s is None or not s.steps:
            return 0.0
        steps = s.steps
        nsteps = len(steps)
        step_dur = 1.0 / s.speed
        t = v.samp * dt
        n = steps[v.step]
        pitch_i = n[0]
        pitch = float(pitch_i)
        wave = n[1]
        vol = float(n[2])
        eff = n[3] if len(n) > 3 else 0
        slide_from = -1.0

        # Arpeggio cycles the step's group of four -- the PITCH only; volume and
        # wave stay the step's own. 30/15 notes/s, doubled on a fast sfx.
        if eff == FX_ARP_FAST or eff == FX_ARP_SLOW:
            nps = (30.0 if eff == FX_ARP_FAST else 15.0) * \
                  (2.0 if s.speed >= 15.0 else 1.0)
            k = (v.step // 4) * 4 + int(t * nps) % 4
            if k < nsteps:
                pitch_i = steps[k][0]
                pitch = float(pitch_i)

        u = t / step_dur                    # 0..1 through the step
        if u > 1.0:
            u = 1.0

        if eff == FX_SLIDE:
            # Glide from the channel's previous note; with none yet, from
            # itself. Linear in FREQUENCY, not semitones (PICO-8/zepto8) -- on a
            # wide slide the two curves differ audibly.
            if v.prev_pitch >= 0.0:
                slide_from = _pitch_hz(v.prev_pitch)
                vol = v.prev_vol + (vol - v.prev_vol) * u
        elif eff == FX_VIBRATO:
            ph = t * 7.5
            ph -= int(ph)
            pitch += 0.25 * _tri_wave(ph)
        elif eff == FX_FADE_IN:
            vol *= u
        elif eff == FX_FADE_OUT:
            vol *= 1.0 - u

        g = 0.0 if (pitch_i < 0 or vol <= 0.0) else vol / 7.0
        if g > 0.0:
            freq = _pitch_hz(pitch)
            if slide_from > 0.0:
                freq = slide_from + (freq - slide_from) * u
            if eff == FX_DROP:
                freq *= 1.0 - u             # falls linearly to 0
            v.phase += freq * dt
            v.phase -= int(v.phase)
            if wave == WAVE_PHASER:
                v.phase2 += freq * (109.0 / 110.0) * dt
                v.phase2 -= int(v.phase2)
            if wave == WAVE_NOISE:
                # PICO-8's noise: an LCG random walk through a one-pole low-pass
                # whose cutoff tracks the note (zepto8's constant), then a bass
                # lift -- low keys up to 3x.
                scale = freq * dt * 8.858923
                p8key = pitch - 24.0        # moy 57=A4 <-> p8 33=A4
                if p8key < 0.0:
                    p8key = 0.0
                elif p8key > 63.0:
                    p8key = 63.0
                factor = 1.0 - p8key / 63.0
                r, v.rng = _lcg_unit(v.rng)
                v.nfrom = (v.nfrom + scale * r) / (1.0 + scale)
                v.nto = v.nfrom * 1.5 * (1.0 + factor * factor)

        # On a rest the phase holds and the slew rides the held level to zero --
        # that IS the release de-click.
        w = _wave_sample(wave, v.phase, v.phase2, v.nto)

        slew = dt / 0.0015
        if v.amp < g:
            v.amp += slew
            if v.amp > g:
                v.amp = g
        else:
            v.amp -= slew
            if v.amp < g:
                v.amp = g

        # Advance the sequencer. Any KEYED step -- volume 0 included -- becomes
        # the channel's previous note: in PICO-8 every tracker slot has a key,
        # so a rest is still a slide origin, and only pitch -1 records nothing.
        v.samp += 1
        if v.samp >= step_dur * rate:
            fin = steps[v.step]
            if fin[0] >= 0:
                v.prev_pitch = float(fin[0])
                v.prev_vol = float(fin[2])
            v.samp = 0
            v.step += 1
            if v.step >= nsteps:
                if s.loop:
                    ls = s.loop_start
                    v.step = ls if ls < nsteps else 0
                else:
                    v.stop()
        return w * v.amp

    def render_into(self, out, nframes):
        """Mix `nframes` of signed-16-bit little-endian mono PCM into the caller's
        `out` bytearray (at least nframes*2 bytes), advancing every voice, the
        music row clock and the beep. Returns the frames written.

        This is the allocation-free core: the device I2S backend reuses one
        persistent buffer per frame (a non-blocking write holds a pointer to it,
        so it must never see a GC'd buffer), while render() wraps it for the
        host's bytes-returning sample-pull."""
        nframes = int(nframes)
        if nframes <= 0:
            return 0
        rate = self.rate
        dt = 1.0 / rate
        master = self.master / 7.0
        voices = self.voices
        bfreq = self._bfreq
        for f in range(nframes):
            mix = 0.0

            # the music clock -- per SAMPLE, so a row boundary is exact. (It was
            # once advanced once per BLOCK, which silently swallowed any track
            # whose rows were shorter than the block: a 0.34 s row_secs song
            # ended before its first sample was rendered.)
            m = self.track
            if m is not None:
                row_dur = m.row_dur(self._mrow)
                if row_dur is not None:     # None: hold this row forever (8.1)
                    self._msamp += 1
                    if self._msamp >= row_dur * rate:
                        self._msamp = 0
                        self._mrow += 1
                        if self._mrow >= len(m.pattern):
                            if self._mloop:
                                self._mrow = 0
                                self._music_row_start()
                            else:
                                self.stop_music()
                        else:
                            self._music_row_start()

            for v in voices:
                if v.owner:
                    mix += self._voice_sample(v, dt, rate)

            if self._bleft > 0.0:           # beep: square at vol 6 (8.2)
                bg = 6.0 / 7.0
                if self._bleft < 0.0015:    # ...with its own release de-click
                    bg *= self._bleft / 0.0015
                self._bphase += bfreq * dt
                self._bphase -= int(self._bphase)
                mix += (0.25 if self._bphase < 0.5 else -0.25) * bg
                self._bleft -= dt

            # Sum, scale, saturate. The instruments themselves peak at 0.25-0.5
            # (PICO-8's mix), so 0.5 here is the headroom for four voices.
            val = int(mix * master * 0.5 * 32767.0)
            if val > 32767:
                val = 32767
            elif val < -32768:
                val = -32768
            out[2 * f] = val & 0xFF
            out[2 * f + 1] = (val >> 8) & 0xFF
        return nframes

    def render(self, nframes):
        """Pull `nframes` of signed-16-bit little-endian mono PCM as bytes. The
        seam the host backends consume; the device prefers render_into()."""
        nframes = int(nframes)
        if nframes <= 0:
            return b""
        out = bytearray(nframes * 2)
        self.render_into(out, nframes)
        return bytes(out)

    def tick(self, dt):
        """API-symmetry seam: a poll-based feeder could render rate*dt samples
        here. The host's sample-pull backends call render() directly."""
        return None
