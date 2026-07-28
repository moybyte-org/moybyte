"""Backend-agnostic audio core shared by the host and device consoles (#16).

Like runtime/editors.py, this is pure logic -- no I/O, no hardware, no canvas --
so the *same* file backs the host reference (imported as runtime.audio) and the
MicroPython device port (frozen as the top-level module `audio`, staged by
build.sh). Only `math` is imported, so it freezes cleanly and runs under both
CPython and MicroPython.

It holds two things:

  1. The sound DATA MODEL -- the kid-authored, JSON-serializable cart audio:
       note   = [pitch, wave, vol]            (the atom)
       SFX     = {speed, loop, steps:[note]}   (a short blip/effect)
       music   = {speed, loop, pattern:[sfx]}  (a tiny one-channel phrase)
       AudioBank = {sfx:[SFX], music:[track]}  (the whole cart bank -> sounds.json)

  2. A pure-Python software SYNTH + MIXER (AudioEngine) that turns that model into
     signed-16-bit mono PCM via render(nframes). render() is the single seam every
     backend consumes: the host feeds it to an SDL stream (or a test recorder), the
     device feeds it to the I2S DMA buffer. Same bank -> same samples on both.

See docs/audio_design_v04.md for the full design and the device-verification TODO.
"""

import math

# -- waveforms ---------------------------------------------------------------
WAVE_SQUARE = 0
WAVE_TRIANGLE = 1
WAVE_SAW = 2
WAVE_NOISE = 3

# Channels: a few simultaneous voices. SFX round-robin across 0..CHANNELS-2; the
# music phrase owns the last channel so an effect never steals the background loop.
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
    """A short sound effect: a list of [pitch, wave, vol] steps at `speed`
    steps/second. Plain data; round-trips through to_dict/from_dict (JSON)."""

    def __init__(self, steps=None, speed=8, loop=False):
        # normalize each step to a [pitch, wave, vol] list of ints
        self.steps = [self._norm(s) for s in (steps or [])]
        self.speed = max(1, int(speed))
        self.loop = bool(loop)

    @staticmethod
    def _norm(s):
        pitch = int(s[0]) if len(s) > 0 and s[0] is not None else REST
        wave = int(s[1]) if len(s) > 1 else WAVE_SQUARE
        vol = int(s[2]) if len(s) > 2 else 6
        return [pitch, _clampi(wave, 0, 3), _clampi(vol, 0, 7)]

    def to_dict(self):
        return {"speed": self.speed, "loop": self.loop, "steps": [list(s) for s in self.steps]}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("steps"), d.get("speed", 8), d.get("loop", False))


class MusicTrack:
    """A tiny one-channel phrase: an ordered list of SFX ids played at `speed`
    slots/second, looping by default."""

    def __init__(self, pattern=None, speed=4, loop=True):
        self.pattern = [int(n) for n in (pattern or [])]
        # fractional speeds are legal (#151: a ported PICO-8 row lasts its
        # whole 32-note SFX -- e.g. 0.117 slots/sec); int carts unchanged.
        self.speed = max(0.01, float(speed))
        self.loop = bool(loop)

    def to_dict(self):
        return {"speed": self.speed, "loop": self.loop, "pattern": list(self.pattern)}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("pattern"), d.get("speed", 4), d.get("loop", True))


class AudioBank:
    """A cart's whole sound bank: SFX list + music tracks. Serializes to the
    cart's sounds.json via to_dict/from_dict."""

    def __init__(self, sfx=None, music=None):
        self.sfx = list(sfx or [])
        self.music = list(music or [])

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

_TWO_PI = 2.0 * math.pi


def _sample_wave(wave, phase, noise_state):
    """One sample in [-1, 1] for a waveform at `phase` in [0, 1).
    Returns (sample, new_noise_state). noise_state is a small LCG int."""
    if wave == WAVE_SQUARE:
        return (1.0 if phase < 0.5 else -1.0), noise_state
    if wave == WAVE_TRIANGLE:
        # 0->1->0->-1->0 triangle
        return (4.0 * abs(phase - 0.5) - 1.0), noise_state
    if wave == WAVE_SAW:
        return (2.0 * phase - 1.0), noise_state
    # noise: a tiny LCG, sampled (phase-independent); state advances each call
    noise_state = (noise_state * 1103515245 + 12345) & 0x7FFFFFFF
    return ((noise_state / 0x3FFFFFFF) - 1.0), noise_state


class _Voice:
    """A single playing channel: a step sequence (SFX or beep) with a step cursor
    and an oscillator phase. Pure state advanced by the engine's render()."""

    def __init__(self):
        self.active = False
        self.steps = []         # list of [pitch, wave, vol]
        self.step_dur = 0.0     # seconds per step
        self.loop = False
        self.idx = 0            # current step index
        self.t = 0.0            # seconds into the current step
        self.phase = 0.0        # oscillator phase 0..1
        self.noise = 12345      # noise LCG state
        # Monotonic trigger counter: bumped on EVERY (re)trigger/stop so a consumer
        # can detect a fresh play unambiguously. The device core-1 feed (#41) uses
        # this to decide which voices to commit to the C task each frame -- it MUST
        # NOT rely on id(steps), which the GC can reuse for a freshly allocated list
        # at the same address (a rapid retrigger of the same SFX then reads as
        # "unchanged" and silently never reaches the mixer -- the Battle City bug).
        self.gen = 0

    def play(self, steps, step_dur, loop):
        self.active = bool(steps)
        self.steps = steps
        self.step_dur = step_dur
        self.loop = loop
        self.idx = 0
        self.t = 0.0
        self.phase = 0.0
        self.gen += 1           # a new trigger -> commit it (see self.gen above)

    def stop(self):
        self.active = False
        self.steps = []
        self.gen += 1           # a stop is also a state change to commit

    def advance_step(self):
        """Move to the next step; deactivate (or loop) at the end."""
        self.idx += 1
        self.t = 0.0
        if self.idx >= len(self.steps):
            if self.loop:
                self.idx = 0
            else:
                self.active = False


class AudioEngine:
    """Pure-Python software synth + mixer. Holds the bank and the live voices;
    render(nframes) pulls signed-16-bit mono PCM (little-endian bytes), mixing all
    active voices. This is the single primitive backends consume."""

    def __init__(self, bank=None, rate=11025):
        self.bank = bank if bank is not None else AudioBank.default()
        self.rate = int(rate)
        self.voices = [_Voice() for _ in range(CHANNELS)]
        self.volume = 1.0
        self._sfx_rr = 0        # round-robin cursor over the SFX channels
        # music phrase state (separate from the per-step voice on MUSIC_CHANNEL)
        self._music = None
        self._music_loop = False
        self._music_slot_dur = 0.0
        self._music_slot = 0
        self._music_t = 0.0

    # -- control ---------------------------------------------------------

    def set_volume(self, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        self.volume = 0.0 if v < 0 else (1.0 if v > 1 else v)

    def _free_sfx_channel(self):
        """Pick an SFX channel: prefer an idle one, else round-robin (steal)."""
        for c in range(MUSIC_CHANNEL):
            if not self.voices[c].active:
                return c
        c = self._sfx_rr
        self._sfx_rr = (self._sfx_rr + 1) % MUSIC_CHANNEL
        return c

    def play_sfx(self, n, chan=None):
        """Play SFX `n` from the bank now. `chan` forces a channel; default picks
        a free SFX channel. No-op if the SFX id is out of range."""
        sfx = self.bank.get_sfx(int(n))
        if sfx is None or not sfx.steps:
            return
        if chan is None:
            chan = self._free_sfx_channel()
        chan = _clampi(int(chan), 0, CHANNELS - 1)
        self.voices[chan].play([list(s) for s in sfx.steps], 1.0 / sfx.speed, sfx.loop)

    def play_beep(self, freq, dur=0.15, wave=WAVE_SQUARE, vol=6):
        """One-shot raw tone -- the zero-data escape hatch. Built as a single-step
        voice at the requested frequency (mapped to the nearest semitone)."""
        try:
            freq = float(freq)
        except (TypeError, ValueError):
            return
        if freq <= 0:
            return
        pitch = self.freq_to_pitch(freq)
        chan = self._free_sfx_channel()
        self.voices[chan].play([[pitch, wave, vol]], max(0.01, float(dur)), False)

    @staticmethod
    def freq_to_pitch(freq):
        """Nearest semitone index for a frequency (inverse of note_to_freq)."""
        return int(round(_A4_PITCH + 12.0 * math.log(freq / _A4_FREQ, 2)))

    def play_music(self, track, loop=True):
        """Start music track `track` (a phrase of SFX ids). `loop` overrides the
        track's own loop flag when given. No-op if out of range."""
        m = self.bank.get_music(int(track))
        if m is None or not m.pattern:
            self.stop_music()
            return
        self._music = m
        self._music_loop = loop if loop is not None else m.loop
        self._music_slot_dur = 1.0 / m.speed
        self._music_slot = 0
        self._music_t = 0.0
        # trigger the first phrase slot immediately
        self.play_sfx(m.pattern[0], chan=MUSIC_CHANNEL)

    def stop_music(self):
        self._music = None
        self.voices[MUSIC_CHANNEL].stop()

    def stop(self, chan=None):
        """Stop one channel, or all channels + music when chan is None."""
        if chan is None:
            for v in self.voices:
                v.stop()
            self.stop_music()
            return
        c = _clampi(int(chan), 0, CHANNELS - 1)
        self.voices[c].stop()
        if c == MUSIC_CHANNEL:
            self._music = None

    def is_active(self):
        """True if any voice or the music phrase is currently producing sound."""
        if self._music is not None:
            return True
        for v in self.voices:
            if v.active:
                return True
        return False

    # -- music phrase scheduler -----------------------------------------

    def _advance_music(self, dt):
        m = self._music
        if m is None:
            return
        self._music_t += dt
        while self._music_t >= self._music_slot_dur:
            self._music_t -= self._music_slot_dur
            self._music_slot += 1
            if self._music_slot >= len(m.pattern):
                if self._music_loop:
                    self._music_slot = 0
                else:
                    self.stop_music()
                    return
            self.play_sfx(m.pattern[self._music_slot], chan=MUSIC_CHANNEL)

    # -- rendering -------------------------------------------------------

    def render_into(self, out, nframes):
        """Mix `nframes` of signed-16-bit little-endian mono PCM into the caller's
        `out` bytearray (which must hold at least nframes*2 bytes), advancing every
        voice + the music scheduler. Returns the number of frames written.

        This is the allocation-free core render() and the device I2S backend share:
        the device reuses one persistent buffer per frame (so the I2S non-blocking
        write, which holds a pointer to it, never sees a GC'd / reallocated buffer),
        while render() wraps this for the host's bytes-returning sample-pull."""
        nframes = int(nframes)
        if nframes <= 0:
            return 0
        rate = self.rate
        dt_frame = nframes / float(rate)
        # advance the music phrase scheduler for this whole block
        self._advance_music(dt_frame)
        inv_rate = 1.0 / rate
        master = self.volume
        voices = self.voices
        for i in range(nframes):
            acc = 0.0
            for v in voices:
                if not v.active or not v.steps:
                    continue
                step = v.steps[v.idx]
                pitch, wave, vol = step[0], step[1], step[2]
                if pitch >= 0 and vol > 0:
                    freq = note_to_freq(pitch)
                    s, v.noise = _sample_wave(wave, v.phase, v.noise)
                    acc += s * (vol / 7.0)
                    v.phase += freq * inv_rate
                    if v.phase >= 1.0:
                        v.phase -= int(v.phase)
                # advance time within the step
                v.t += inv_rate
                if v.t >= v.step_dur:
                    v.advance_step()
            # mix down: scale by channels to avoid clipping, apply master volume
            acc = acc * master / CHANNELS
            if acc > 1.0:
                acc = 1.0
            elif acc < -1.0:
                acc = -1.0
            val = int(acc * 32767)
            out[2 * i] = val & 0xFF
            out[2 * i + 1] = (val >> 8) & 0xFF
        return nframes

    def render(self, nframes):
        """Pull `nframes` of signed-16-bit little-endian mono PCM as bytes, mixing
        all active voices. Advances every voice's step/phase cursor and the music
        scheduler. This is the seam the host/device backends consume; the device
        prefers render_into() to avoid a per-frame allocation."""
        nframes = int(nframes)
        if nframes <= 0:
            return b""
        out = bytearray(nframes * 2)
        self.render_into(out, nframes)
        return bytes(out)

    def tick(self, dt):
        """API-symmetry seam: a poll-based device feeder could render `rate*dt`
        samples here. The host's sample-pull backends call render() directly, so
        this is a no-op by default."""
        return None
