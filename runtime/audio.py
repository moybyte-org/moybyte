"""Backend-agnostic audio core shared by the host and device consoles (#16).

Like runtime/editors.py, this is pure logic -- no I/O, no hardware, no canvas --
so the *same* file backs the host reference (imported as runtime.audio) and the
MicroPython device port (frozen as the top-level module `audio`, staged by
build.sh).

It holds two things:

  1. The sound DATA MODEL -- the kid-authored, JSON-serializable cart audio:
       note   = [pitch, wave, vol] or [pitch, wave, vol, eff]   (the atom)
       SFX     = {speed, loop, steps:[note]}   (a short blip/effect)
       music   = {speed, loop, pattern:[row]}  (rows: one SFX id, or a list of
                                                up to 4 ids -- one per channel)
       AudioBank = {sfx:[SFX], music:[track]}  (the whole cart bank -> sounds.json)

  2. AudioEngine -- the per-cart engine object. On the HOST it synthesizes by
     driving the vendored libmoy C through runtime/audio_binding.py (a ctypes
     .so built from the exact source the boards compile), and render(nframes)
     pulls signed-16-bit mono PCM -- the seam the host backends consume (the
     SDL stream, the test recorder, the web console's per-frame PCM). On the
     DEVICE the binding is absent by design and the class is the MODEL holder
     only: it carries the bank the Music editor edits and the master level,
     while playback lives in the native moy_audio module (device_audio.py).

THERE IS NO PYTHON SYNTH ANY MORE (#97, moycore stage 0)
This module used to carry a hand-maintained line-for-line Python twin of
libmoy's synthesizer, pinned by a bit-exact parity suite. The twin's whole
drift class had a body count anyway (the equal-loudness bug, the truncated
fractional SFX speeds -- both were twin bugs the pin caught late), so the host
now binds the vendored C itself: one synthesizer, every tier. The binding
compiles the source DOUBLE-WIDENED (the parity harness's own recipe), which the
old strict suite had already proven bit-identical to the twin -- the swap moved
no sample the host ever played. Without a C compiler the host plays SILENCE
(owner decision 2026-08-11: no fallback, KISS -- the degradation lane's job was
never playback quality). tests/test_audio_parity.py still gates: it now pins
the BINDING against the reference render, marshalling instead of arithmetic.

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

    def __init__(self, steps=None, speed=8, loop=False, loop_start=0):
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


# -- the engine ---------------------------------------------------------------
#
# AudioEngine drives the vendored libmoy synthesizer through the ctypes binding
# (runtime/audio_binding.py). The class is constructed everywhere -- host and
# device -- so the binding import is guarded and every playback verb degrades to
# a silent no-op without it (on the boards, playback lives in the native
# moy_audio module instead; this object is their bank/model holder). Argument
# guards mirror what the retired twin did; the bounds/ownership logic itself is
# libmoy's own (moy_audio.c checks them all), so nothing here re-implements the
# spec.

_binding_mod = None      # the audio_binding module, or False once import failed


def _binding():
    """The loaded C binding, or None (MicroPython, or no compiler)."""
    global _binding_mod
    if _binding_mod is False:
        return None
    if _binding_mod is None:
        try:
            try:
                from runtime import audio_binding as _ab
            except ImportError:
                import audio_binding as _ab
            _binding_mod = _ab
        except Exception:        # MicroPython: no such module, by design
            _binding_mod = False
            return None
    return _binding_mod.get()


class AudioEngine:
    """The per-cart audio engine: holds the AudioBank (the MODEL -- the Music
    editor mutates it in place and bumps bank.rev) and, on the host, a libmoy
    engine handle that does the actual synthesis. render(nframes) pulls
    signed-16-bit little-endian mono PCM; without the binding it pulls silence.

    The control surface (play_sfx/play_beep/play_music/stop_music/stop/
    set_volume/is_active) survives the retired Python twin unchanged, so
    FakeAudio, SdlAudio and the Music editor drive exactly what they always
    drove. The bank crosses to C ONCE per edit generation, as sounds.json text
    through libmoy's own parser -- the same single crossing the device makes,
    re-checked by an identity+int compare at each trigger (AudioBank.rev)."""

    def __init__(self, bank=None, rate=11025):
        self.bank = bank if bank is not None else AudioBank.default()
        self.rate = int(rate)
        self.master = 7             # SPEC.md 8.2 volume(level): 0..7
        lib = _binding()
        self._h = lib.new(self.rate) if lib is not None else None
        self._lib = lib if self._h else None
        self._pushed_bank = None    # strong ref: the bank last handed to C --
        self._pushed_rev = -1       # object identity, so a reused id() can
        self._pushed_rate = -1      # never alias a fresh bank as "unchanged"

    def __del__(self):
        lib, h = self._lib, self._h
        self._lib = None
        self._h = None
        if lib is not None and h:
            try:
                lib.free(h)
            except Exception:       # interpreter teardown: ctypes may be gone
                pass

    def _sync(self):
        """Hand the bank to libmoy when it (or the rate) moved since the last
        trigger. One compare on the hot path; the JSON crossing only happens
        after a real edit. Returns True when the C engine is live."""
        lib, h = self._lib, self._h
        if h is None:
            return False
        b = self.bank
        if (b is self._pushed_bank and b.rev == self._pushed_rev
                and self.rate == self._pushed_rate):
            return True
        if self.rate != self._pushed_rate:
            lib.set_rate(h, self.rate)
        import json
        lib.bank_load(h, json.dumps(b.to_dict()))
        lib.volume(h, self.master)  # bank_load resets the engine, master too
        self._pushed_bank = b
        self._pushed_rev = b.rev
        self._pushed_rate = self.rate
        return True

    # -- control ---------------------------------------------------------

    def set_volume(self, level):
        """Master output level, 0..7 -- the same scale as a note's `vol`, and
        what libmoy and SPEC.md 8.2 mean by volume(level)."""
        try:
            v = int(level)
        except (TypeError, ValueError):
            return
        self.master = 0 if v < 0 else (7 if v > 7 else v)
        if self._h is not None:
            self._lib.volume(self._h, self.master)

    def play_sfx(self, n, chan=None):
        """Play SFX `n`. `chan` forces a channel; otherwise (or out of range)
        libmoy round-robins whatever music leaves free -- see moy_audio_sfx."""
        try:
            n = int(n)
            c = -1 if chan is None else int(chan)
        except (TypeError, ValueError):
            return
        if self._sync():
            self._lib.sfx(self._h, n, c)

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
        if self._h is not None:
            self._lib.beep(self._h, freq, dur)

    @staticmethod
    def freq_to_pitch(freq):
        """Nearest semitone index for a frequency (inverse of note_to_freq)."""
        return int(round(_A4_PITCH + 12.0 * math.log(freq / _A4_FREQ, 2)))

    def play_music(self, track, loop=True):
        """Start music track `track`, claiming channels from the top (8.1).
        `loop` overrides the track's own flag; None keeps the track's. Out of
        range (or an empty pattern) is a no-op, exactly as libmoy treats it."""
        try:
            t = int(track)
        except (TypeError, ValueError):
            return
        m = self.bank.get_music(t)
        if m is None:
            return
        lp = m.loop if loop is None else bool(loop)
        if self._sync():
            self._lib.music(self._h, t, 1 if lp else 0)

    def stop_music(self):
        if self._h is not None:
            self._lib.music_stop(self._h)

    def stop(self, chan=None):
        """Stop one channel, or everything (voices, music and the beep) when
        chan is None."""
        if self._h is None:
            return
        try:
            c = -1 if chan is None else int(chan)
        except (TypeError, ValueError):
            return
        self._lib.sound_stop(self._h, c)

    def active_channels(self):
        """Bit mask of what is sounding: bits 0..3 the four voices, bit 4 the
        music track, bit 5 the beep -- the device module's active() layout, so
        behavior tests read the same instrument on every tier. 0 is silence,
        and all a binding-less build ever reports."""
        if self._h is None:
            return 0
        return self._lib.active(self._h)

    def is_active(self):
        """True if anything is currently producing sound."""
        return self.active_channels() != 0

    # -- rendering -------------------------------------------------------

    def render_into(self, out, nframes):
        """Mix `nframes` of signed-16-bit mono PCM into the caller's `out`
        bytearray (at least nframes*2 bytes). Returns the frames written.
        Allocation-free when the binding is live: libmoy writes into the
        buffer directly, in native byte order (little-endian on every host the
        sim runs on, matching what the seam always produced)."""
        nframes = int(nframes)
        if nframes <= 0:
            return 0
        if self._h is not None:
            self._lib.render_into(self._h, out, nframes)
        else:
            out[:2 * nframes] = b"\x00" * (2 * nframes)
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
