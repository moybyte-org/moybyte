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

  2. A pure-Python software SYNTH + MIXER (AudioEngine) that turns that model into
     signed-16-bit mono PCM via render(nframes). render() is the single seam every
     backend consumes: the host feeds it to an SDL stream (or a test recorder), the
     device feeds it to the I2S DMA buffer. Same bank -> same samples on both.

See docs/audio_design_v04.md for the full design and the device-verification TODO.
"""

import math

# -- waveforms ---------------------------------------------------------------
# 0-3 are the original four (frozen -- existing banks index them); 4-7 are the
# PICO-8-parity additions (#170: "p8 is the least fidelity we offer"), so the
# eight cover every p8 instrument 1:1 and a ported cart keeps its timbres.
WAVE_SQUARE = 0
WAVE_TRIANGLE = 1
WAVE_SAW = 2
WAVE_NOISE = 3
WAVE_PULSE = 4      # narrow square (1/3 duty) -- thinner, reedier than square
WAVE_ORGAN = 5      # triangle + a quieter octave-up triangle
WAVE_TILTED = 6     # tilted saw (slow rise, fast fall) -- softer than saw
WAVE_PHASER = 7     # two slightly-detuned triangles beating (~freq/128 Hz)

# -- per-note effects (the optional 4th field; PICO-8 numbering, #170) --------
FX_NONE = 0
FX_SLIDE = 1        # pitch+vol glide from the channel's previous note
FX_VIBRATO = 2      # +-0.25 semitone triangle wobble at 7.5 Hz
FX_DROP = 3         # frequency falls to 0 across the step
FX_FADE_IN = 4      # volume ramps 0 -> vol
FX_FADE_OUT = 5     # volume ramps vol -> 0
FX_ARP_FAST = 6     # arpeggio over the step's group of 4 at 30 notes/sec
FX_ARP_SLOW = 7     # same at 15 notes/sec

# Channels: a few simultaneous voices. Music claims voices from the TOP (a
# 1-channel phrase owns voice 3 exactly as it always did; an N-channel track
# claims voices 3..4-N), and SFX round-robin across the remainder so an effect
# never steals the background loop.
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
        """Row i's duration in seconds (None = hold forever)."""
        if self.row_secs and 0 <= i < len(self.row_secs):
            v = self.row_secs[i]
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


def _sample_wave(wave, phase, noise_state, phase2=0.0):
    """One sample in [-1, 1] for a waveform at `phase` in [0, 1).
    Returns (sample, new_noise_state). noise_state is a small LCG int.
    `phase2` is the detuned secondary phase only WAVE_PHASER reads (#170).
    The C mixer (native/moy_audio) mirrors this arithmetic exactly -- change
    both together or host and device drift apart."""
    if wave == WAVE_SQUARE:
        return (1.0 if phase < 0.5 else -1.0), noise_state
    if wave == WAVE_TRIANGLE:
        # 0->1->0->-1->0 triangle
        return (4.0 * abs(phase - 0.5) - 1.0), noise_state
    if wave == WAVE_SAW:
        return (2.0 * phase - 1.0), noise_state
    if wave == WAVE_PULSE:
        # narrow square, 1/3 duty -- thinner and reedier than the square
        return (1.0 if phase < (1.0 / 3.0) else -1.0), noise_state
    if wave == WAVE_ORGAN:
        # triangle + a quieter octave-up triangle, renormalized to [-1, 1]
        p2 = (phase * 2.0) % 1.0
        s = (4.0 * abs(phase - 0.5) - 1.0) + 0.5 * (4.0 * abs(p2 - 0.5) - 1.0)
        return (s / 1.5), noise_state
    if wave == WAVE_TILTED:
        # tilted saw: rises over 7/8 of the period, falls over the last 1/8
        if phase < 0.875:
            return (2.0 * phase / 0.875 - 1.0), noise_state
        return (2.0 * (1.0 - phase) / 0.125 - 1.0), noise_state
    if wave == WAVE_PHASER:
        # two detuned triangles (the second runs at freq*127/128) beating
        s = (4.0 * abs(phase - 0.5) - 1.0) + (4.0 * abs(phase2 - 0.5) - 1.0)
        return (s * 0.5), noise_state
    # noise: a tiny LCG, sampled (phase-independent); state advances each call
    noise_state = (noise_state * 1103515245 + 12345) & 0x7FFFFFFF
    return ((noise_state / 0x3FFFFFFF) - 1.0), noise_state


class _Voice:
    """A single playing channel: a step sequence (SFX or beep) with a step cursor
    and an oscillator phase. Pure state advanced by the engine's render()."""

    def __init__(self):
        self.active = False
        self.steps = []         # list of [pitch, wave, vol(, eff)]
        self.step_dur = 0.0     # seconds per step
        self.loop = False
        self.loop_start = 0     # where a looping voice wraps back to (#170)
        self.idx = 0            # current step index
        self.t = 0.0            # seconds into the current step
        self.phase = 0.0        # oscillator phase 0..1
        self.phase2 = 0.0       # detuned secondary phase (WAVE_PHASER, #170)
        self.noise = 12345      # noise LCG state
        # The channel's previous sounding note (pitch, vol) -- what FX_SLIDE
        # glides FROM. Survives play() so a slide works across music rows /
        # retriggers, exactly like a p8 channel; -1 means "no previous note"
        # (a slide then degenerates to the note itself).
        self.prev_pitch = -1
        self.prev_vol = 0
        # Monotonic trigger counter: bumped on EVERY (re)trigger/stop so a consumer
        # can detect a fresh play unambiguously. The device core-1 feed (#41) uses
        # this to decide which voices to commit to the C task each frame -- it MUST
        # NOT rely on id(steps), which the GC can reuse for a freshly allocated list
        # at the same address (a rapid retrigger of the same SFX then reads as
        # "unchanged" and silently never reaches the mixer -- the Brick Siege bug).
        self.gen = 0

    def play(self, steps, step_dur, loop, loop_start=0):
        self.active = bool(steps)
        self.steps = steps
        self.step_dur = step_dur
        self.loop = loop
        self.loop_start = loop_start if loop_start < len(steps) else 0
        self.idx = 0
        self.t = 0.0
        self.phase = 0.0
        self.phase2 = 0.0
        # prev_pitch/prev_vol deliberately survive (FX_SLIDE glides across rows)
        self.gen += 1           # a new trigger -> commit it (see self.gen above)

    def stop(self):
        self.active = False
        self.steps = []
        self.gen += 1           # a stop is also a state change to commit

    def advance_step(self):
        """Move to the next step; deactivate (or loop) at the end. Records the
        finished step as the channel's previous sounding note (FX_SLIDE)."""
        if self.idx < len(self.steps):
            st = self.steps[self.idx]
            if st[0] >= 0 and st[2] > 0:
                self.prev_pitch = st[0]
                self.prev_vol = st[2]
        self.idx += 1
        self.t = 0.0
        if self.idx >= len(self.steps):
            if self.loop:
                self.idx = self.loop_start   # p8 loop range: wrap to loop_start
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
        # music phrase state. Music claims voices from the TOP: row channel j
        # plays on voice MUSIC_CHANNEL - j, so a 1-channel track sits exactly
        # where it always did (voice 3) and an N-channel track (#170) claims
        # voices 3..4-N. _music_nch is how many voices the CURRENT track claims
        # (1 while idle, so sfx keep their 0..2 round-robin either way).
        self._music = None
        self._music_loop = False
        self._music_slot_dur = 0.0
        self._music_slot = 0
        self._music_t = 0.0
        self._music_nch = 1

    # -- control ---------------------------------------------------------

    def set_volume(self, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return
        self.volume = 0.0 if v < 0 else (1.0 if v > 1 else v)

    def _free_sfx_channel(self):
        """Pick an SFX channel among the voices music has NOT claimed: prefer
        an idle one, else round-robin (steal). When a 4-channel track owns
        every voice, steal voice 0 (the track's last channel -- typically the
        least melodic line) rather than dropping the effect."""
        lim = CHANNELS - self._music_nch
        if lim <= 0:
            return 0
        for c in range(lim):
            if not self.voices[c].active:
                return c
        c = self._sfx_rr % lim
        self._sfx_rr = (c + 1) % lim
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
        self.voices[chan].play([list(s) for s in sfx.steps], 1.0 / sfx.speed,
                               sfx.loop, sfx.loop_start)

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

    @staticmethod
    def _track_channels(m):
        """How many voices a track claims: the widest row's channel count."""
        w = 1
        for r in m.pattern:
            if isinstance(r, (list, tuple)) and len(r) > w:
                w = len(r)
        return w if w < CHANNELS else CHANNELS

    def _trigger_music_row(self, row):
        """Start one pattern row: channel j's SFX on voice MUSIC_CHANNEL - j.
        A -1 (or missing) channel is silent this row -> its voice stops."""
        ids = row if isinstance(row, (list, tuple)) else (row,)
        for j in range(self._music_nch):
            sid = ids[j] if j < len(ids) else -1
            chan = MUSIC_CHANNEL - j
            if sid is None or sid < 0:
                self.voices[chan].stop()
            else:
                self.play_sfx(sid, chan=chan)

    def play_music(self, track, loop=True):
        """Start music track `track` (a phrase of SFX-id rows). `loop` overrides
        the track's own loop flag when given. No-op if out of range."""
        m = self.bank.get_music(int(track))
        if m is None or not m.pattern:
            self.stop_music()
            return
        self.stop_music()               # release any previous track's voices
        self._music = m
        self._music_nch = self._track_channels(m)
        self._music_loop = loop if loop is not None else m.loop
        self._music_slot_dur = m.row_dur(0)   # None = hold this row forever
        self._music_slot = 0
        self._music_t = 0.0
        # trigger the first phrase slot immediately
        self._trigger_music_row(m.pattern[0])

    def stop_music(self):
        self._music = None
        for j in range(self._music_nch):
            self.voices[MUSIC_CHANNEL - j].stop()
        self._music_nch = 1

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
            self.stop_music()       # releases every voice the track claimed

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
        if m is None or self._music_slot_dur is None:
            return                       # hold-forever row (every channel loops)
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
            self._music_slot_dur = m.row_dur(self._music_slot)
            self._trigger_music_row(m.pattern[self._music_slot])
            if self._music_slot_dur is None:
                return                   # entered a hold-forever row

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
                eff = step[3] if len(step) > 3 else 0
                if pitch >= 0 and vol > 0:
                    volf = float(vol)
                    if eff:
                        # per-note effects (#170, p8 numbering). frac is the
                        # 0..1 progress through the step; all shapes below are
                        # mirrored EXACTLY in the C mixer (moy_mix_block).
                        frac = (v.t / v.step_dur) if v.step_dur > 0 else 0.0
                        if eff == FX_SLIDE:
                            pp = v.prev_pitch if v.prev_pitch >= 0 else pitch
                            pv = v.prev_vol if v.prev_pitch >= 0 else vol
                            pitch = pp + (pitch - pp) * frac
                            volf = pv + (vol - pv) * frac
                        elif eff == FX_VIBRATO:
                            ph = (v.t * 7.5) % 1.0
                            pitch = pitch + (4.0 * abs(ph - 0.5) - 1.0) * 0.25
                        elif eff == FX_FADE_IN:
                            volf = vol * frac
                        elif eff == FX_FADE_OUT:
                            volf = vol * (1.0 - frac)
                        elif eff >= FX_ARP_FAST:
                            # arpeggiate the step's group of 4 (30/15 notes/s)
                            rate = 30.0 if eff == FX_ARP_FAST else 15.0
                            k = (v.idx // 4) * 4 + int(v.t * rate) % 4
                            if k < len(v.steps):
                                pitch = v.steps[k][0]
                    freq = note_to_freq(pitch)
                    if eff == FX_DROP:
                        freq *= 1.0 - ((v.t / v.step_dur)
                                       if v.step_dur > 0 else 0.0)
                    if pitch >= 0:
                        s, v.noise = _sample_wave(wave, v.phase, v.noise,
                                                  v.phase2)
                        acc += s * (volf / 7.0)
                        v.phase += freq * inv_rate
                        if v.phase >= 1.0:
                            v.phase -= int(v.phase)
                        if wave == WAVE_PHASER:
                            v.phase2 += freq * (127.0 / 128.0) * inv_rate
                            if v.phase2 >= 1.0:
                                v.phase2 -= int(v.phase2)
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
