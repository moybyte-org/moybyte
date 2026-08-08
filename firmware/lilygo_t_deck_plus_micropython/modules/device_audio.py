"""The device AUDIO backend (#16, #97): SPEC.md 8 to the T-Deck Plus's amp.

DeviceAudio is the LIVE counterpart of the host's fake audio, so the same cart
audio API runs host == device. What it does is narrow:

  * hands the cart's sound bank to the native `moy_audio` module ONCE per cart,
  * forwards the six SPEC.md 8.2 verbs,
  * owns the I2S plumbing.

THE SYNTH IS NOT HERE, AND NOT IN PYTHON (#97)
`moy_audio` is a binding over libmoy -- moy-spec's own C implementation of
SPEC.md 8, vendored into native/moy_audio/libmoy/ and compiled in. It owns the
bank, both sequencers and the mixer. This module owns none of that.

It used to own most of it. Python held the voices and the music scheduler and
pushed all four voices' entire state across the boundary every frame
(voice_set), C advanced them, Python read the cursor back (voice_read), and the
core-1 task snapshotted / mixed / folded back with a per-voice commit counter to
decide whose cursor was authoritative -- machinery that existed solely to keep
two copies of one state in step, and that had already produced one silent-drop
bug (a same-sfx retrigger aliasing as "unchanged"). There is one copy now, so
all of it is gone, along with the per-frame marshalling.

`self.engine` survives for the MODEL, not for playback: it holds the AudioBank
the Music editor edits in place and the store persists. When that bank changes
(AudioBank.rev) the next trigger re-pushes it.

Self-contained: imports the leaf device_util (diag + tick helpers) and lazily the
native `moy_audio` + `machine.I2S` inside its methods, so no moy_runtime cycle.
Device-only module (modules/, auto-frozen).

NEEDS ON-DEVICE VERIFICATION. The synth half is pinned off-hardware -- the same
native module, built into a desktop MicroPython, renders bit-identically to
libmoy across the whole parity suite (tests/test_audio_parity.py). I2S, the
core-1 task and the PSRAM bank placement cannot be reached that way. Do not claim
this plays on a board until a board has played it.
"""
import json

from device_util import _diag_note


# --- Audio backend (#16) -- I2S to the MAX98357 amp -------------------------
# The T-Deck Plus has a MAX98357 I2S class-D amp + speaker on a SEPARATE
# peripheral from the shared display/SD SPI host, so audio does NOT collide with
# the SD/display bus-takeover constraints (see CLAUDE.md). Pin map + power gate
# from the LilyGO reference (examples/I2SPlay/utilities.h):
#     I2S_BCK = GPIO 7, I2S_WS = GPIO 5 (LRCK), I2S_DOUT = GPIO 6
#     BOARD_POWERON = GPIO 10 must be HIGH (already driven at boot by tdeck_board)
# There is NO separate amp enable / gain GPIO on this board -- the amp's SD pin is
# hardwired and the only power gate is BOARD_POWERON (confirmed: the panel lives
# behind it too, and the panel works). I2S.MONO puts samples on the left slot,
# which is the MAX98357's mono input. So pins, power and format are all correct;
# if it is silent the failure is the I2S *init* (made loud below) or the *feed*.
#
# THE FEED -- THE CRACKLE FIX (#41): a dedicated core-1 audio task.
# The crackle's root cause was that the I2S feed was COUPLED to the render loop:
# tick() ran once per frame on core 0 (the MicroPython VM core) and a render
# frame is tens of ms, so the DMA ring drained and under-ran during a long draw.
# A deeper ring only helped a little, because the feed CADENCE was still the slow,
# jittery frame rate. The fix: feed I2S from a FreeRTOS task PINNED TO CORE 1, so
# the DMA is topped up continuously no matter how slow core 0's frame is. I2S is
# on its own pins, so core 1 owning it never touches the panel/SD path.
#
# FALLBACK (revert-able with NO rebuild): if the core-1 task can't start, tick()
# drives the render itself via machine.I2S non-blocking writes. Set
# MOY_AUDIO_CORE1 = False to force that path even when the task is available.

MOY_AUDIO_CORE1 = True

I2S_BCK = 7
I2S_WS = 5
I2S_DOUT = 6
# 8 kHz mono: matches the reference SimpleTone rate and keeps the mixer cheap.
AUDIO_RATE = 8000
# DMA ring buffer (bytes). ~0.5 s of 8 kHz/16-bit mono -- a deep cushion so the
# speaker never under-runs across slow/variable render frames plus jitter.
AUDIO_IBUF = 8192
AUDIO_IBUF_FRAMES = AUDIO_IBUF // 2
# Cap on a single tick's render/write. The legacy feed TOPS THE RING UP toward
# full rather than feeding exactly rate*dt (which kept it hovering near-empty --
# any 50-60 ms draw under-ran it), so the cap only bounds the cold-start fill.
AUDIO_MAX_FRAME = AUDIO_IBUF_FRAMES

# Log each sfx/music trigger to moybyte_diag, so the owner can read on serial/SD
# exactly what reached the mixer. Event-gated: one line per actual call.
AUDIO_DIAG = True

_AUDIO_BACKEND_SEQ = 0


class DeviceAudio:
    """I2S audio backend for the T-Deck. Forwards SPEC.md 8.2 to libmoy via the
    native `moy_audio` module, and feeds the result to the amp -- from a core-1
    task by default, or per-frame from tick() as a fallback.

    Without the native module (a build with moy_audio left out) it falls back to
    the shared Python AudioEngine, which is a twin of the same libmoy source, so
    a stripped build still makes the right noises -- just more slowly."""

    def __init__(self, engine):
        global _AUDIO_BACKEND_SEQ
        _AUDIO_BACKEND_SEQ += 1
        self.engine = engine            # the MODEL: bank + the host-side twin
        self._diag_seq = _AUDIO_BACKEND_SEQ
        # The shared engine is built at 11025 Hz; the device runs 8 kHz to match
        # the I2S port. Only the fallback render path reads this, live.
        engine.rate = AUDIO_RATE
        self.i2s = None
        self._core1 = False             # core-1 feeder task running
        self._reused_core1 = False      # audio_start found the task already alive
        self._bank_rev = None           # AudioBank.rev last pushed to libmoy
        # Legacy-feed double buffer: render alternates into bufs[_buf] and
        # write()s it non-blocking; the port copies it on a background task and
        # fires _on_done. Never touch a buffer while its copy is in flight
        # (_busy). Persistent bytearrays => the GC can't collect an in-flight one.
        self._bufs = (bytearray(AUDIO_MAX_FRAME * 2),
                      bytearray(AUDIO_MAX_FRAME * 2))
        self._buf = 0
        self._busy = False
        self._busy_ticks = 0
        # Software estimate of frames still queued in the DMA ring (legacy feed):
        # subtract what the speaker drained, add what we wrote, refill toward
        # full. Conservative -- it can only UNDER-state occupancy, so it never
        # tricks us into starving the ring.
        self._buffered = 0

        try:
            import moy_audio
            self._na = moy_audio
            print("Moybyte audio: native moy_audio (libmoy) ENABLED")
        except Exception:   # noqa: BLE001 -- no native module -> Python twin
            self._na = None
            print("Moybyte audio: native moy_audio absent, using Python engine")

        if self._na is not None:
            try:
                self._na.set_rate(AUDIO_RATE)
                self._push_bank()
                self._na.volume(engine.master)
            except Exception as exc:  # noqa: BLE001 -- never fail the boot
                _diag_note("audio", "bank push failed (%s)" % (exc,))

        # 1) Preferred: hand I2S to the dedicated core-1 task (the crackle fix).
        if MOY_AUDIO_CORE1 and self._na is not None:
            try:
                already = False
                try:
                    already = bool(self._na.running())
                except Exception:
                    already = False
                if self._na.audio_start(I2S_BCK, I2S_WS, I2S_DOUT, AUDIO_RATE):
                    self._core1 = True
                    self._reused_core1 = already
                    _diag_note("audio", "core-1 I2S task %s (%d Hz mono, "
                               "BCK=%d WS=%d DOUT=%d)"
                               % ("reused" if already else "started", AUDIO_RATE,
                                  I2S_BCK, I2S_WS, I2S_DOUT))
                    print("Moybyte audio: core-1 I2S feeder %s"
                          % ("REUSED" if already else "ENABLED"))
                else:
                    _diag_note("audio", "core-1 task unavailable, legacy feed")
            except Exception as exc:  # noqa: BLE001 -- any failure -> legacy feed
                _diag_note("audio", "core-1 start failed (%s), legacy feed" % (exc,))
                self._core1 = False

        # 2) Fallback: open machine.I2S for the per-frame feed. Skipped when the
        #    core-1 task owns the peripheral (two owners would clash).
        if not self._core1:
            try:
                from machine import I2S, Pin
                self.i2s = I2S(
                    0,
                    sck=Pin(I2S_BCK),
                    ws=Pin(I2S_WS),
                    sd=Pin(I2S_DOUT),
                    mode=I2S.TX,
                    bits=16,
                    format=I2S.MONO,
                    rate=AUDIO_RATE,
                    ibuf=AUDIO_IBUF,
                )
                # irq() flips the port into NON_BLOCKING mode and registers our
                # completion callback -- write() now returns immediately.
                self.i2s.irq(self._on_done)
                _diag_note("audio", "legacy I2S ready (%d Hz mono, BCK=%d WS=%d "
                           "DOUT=%d)" % (AUDIO_RATE, I2S_BCK, I2S_WS, I2S_DOUT))
            except Exception as exc:  # noqa: BLE001 -- no amp / no I2S -> silent
                # LOUD: if audio is silent on-device this is the line to look for
                # in the boot log AND the persisted diag dump.
                _diag_note("audio", "I2S UNAVAILABLE, silent: %s" % (exc,))
                self.i2s = None

    # -- the bank ---------------------------------------------------------

    def _push_bank(self):
        """Hand the cart's bank to libmoy. ONE crossing, as sounds.json text --
        libmoy's own parser reads it, so the device loads exactly what every
        other libmoy host loads.

        Costs a ~20 KB transient string for a big imported cart, which is why it
        happens on a cart load or an editor edit and never per frame."""
        na = self._na
        if na is None:
            return
        bank = self.engine.bank
        ok = na.bank_load(json.dumps(bank.to_dict()))
        self._bank_rev = bank.rev
        if not ok:
            # Malformed, or past libmoy's fixed capacities (64 sfx x 64 steps,
            # 32 tracks x 64 rows). The bank is left zeroed and silent rather
            # than half-loaded -- say so, since "no sound" is otherwise a mystery.
            _diag_note("audio", "bank REJECTED (bad json or over capacity): "
                       "%d sfx, %d music" % (len(bank.sfx), len(bank.music)))
            print("Moybyte audio: sound bank rejected by libmoy -- silent")

    def _sync_bank(self):
        """Re-push if the Music editor (or undo) moved the bank. An int compare
        on the hot path; the re-parse only happens after a real edit."""
        if self._na is not None and self.engine.bank.rev != self._bank_rev:
            self._push_bank()

    def _on_done(self, _i2s):
        """I2S non-blocking completion (legacy feed): the background copy of the
        last buffer into the DMA ring is done, so the next one can be rendered.
        Runs via mp_sched (between bytecodes), so it just clears the flag."""
        self._busy = False
        self._busy_ticks = 0

    def diag_state(self):
        """Tuple consumed by Player diagnostics:
        (backend_seq, core1, reused_task, native_running, active_voices, 0).
        Guarded so profiling never affects playback."""
        running = -1
        active = -1
        try:
            if self._na is not None:
                try:
                    running = 1 if self._na.running() else 0
                except Exception:
                    running = -1
                try:
                    mask = self._na.active() & 0x0F      # the four voices
                    active = 0
                    while mask:
                        active += mask & 1
                        mask >>= 1
                except Exception:
                    active = -1
        except Exception:
            pass
        return (self._diag_seq,
                1 if self._core1 else 0,
                1 if self._reused_core1 else 0,
                running, active, 0)

    # -- control surface (mirrors host FakeAudio / _SilentAudio) -----------

    def sfx(self, n, chan=None):
        if self._na is not None:
            self._sync_bank()
            self._na.sfx(int(n), -1 if chan is None else int(chan))
        else:
            self.engine.play_sfx(n, chan)
        if AUDIO_DIAG:
            self._diag_trigger("sfx", n, chan)

    def beep(self, freq, dur=0.15):
        if self._na is not None:
            self._na.beep(float(freq), float(dur))
        else:
            self.engine.play_beep(freq, dur)
        if AUDIO_DIAG:
            self._diag_trigger("beep", int(freq), None)

    def music(self, track, loop=True):
        if self._na is not None:
            self._sync_bank()
            self._na.music(int(track), 1 if loop else 0)
        else:
            self.engine.play_music(track, loop)
        if AUDIO_DIAG:
            self._diag_trigger("music", track, None)

    def music_stop(self):
        if self._na is not None:
            self._na.music_stop()
        else:
            self.engine.stop_music()

    def sound_stop(self, chan=None):
        if self._na is not None:
            self._na.sound_stop(-1 if chan is None else int(chan))
        else:
            self.engine.stop(chan)

    def volume(self, level):
        # Keep the Python model in step so a build without the native module --
        # and the Settings surface -- see the same master level.
        self.engine.set_volume(level)
        if self._na is not None:
            try:
                self._na.volume(self.engine.master)
            except Exception:   # noqa: BLE001 -- volume must never crash the loop
                pass

    def is_active(self):
        """True while anything is audible. libmoy owns the sequencers, so it is
        the only thing that knows -- the Music editor's preview and the console's
        redraw gate ask this rather than the Python engine's idle voices."""
        if self._na is not None:
            try:
                return bool(self._na.active())
            except Exception:   # noqa: BLE001
                return False
        return self.engine.is_active()

    def _diag_trigger(self, kind, n, chan):
        """Log one trigger to moybyte_diag (event-gated, one line per call).
        Reports the feed so the owner can tell at a glance which path is live."""
        try:
            _diag_note("AUDIO", "%s=%s chan=%s feed=%s"
                       % (kind, n, "auto" if chan is None else chan,
                          "core1" if self._core1 else "single"))
        except Exception:   # noqa: BLE001 -- diag must never crash a trigger
            pass

    # -- the feed ---------------------------------------------------------

    def tick(self, dt):
        """Per-frame audio work. In core-1 mode there is NONE -- the task renders
        and feeds I2S continuously, off the render core. The legacy fallback
        renders this frame's PCM and streams it to the DMA ring non-blockingly.
        Either way it must never stall the single-threaded desktop loop."""
        if self._core1:
            return

        # --- legacy single-core feed -- TOP-UP, the crackle fix ---
        # Render exactly rate*dt and the ring only ever holds one frame's worth:
        # it hovers near-empty and any long draw / GC pause drains it to an
        # under-run. So top the deep (~0.5 s) ring UP toward full each tick and
        # let the cushion absorb long frames.
        if self.i2s is None:
            return
        drained = int(AUDIO_RATE * dt)
        self._buffered -= drained
        if self._buffered < 0:
            self._buffered = 0
        if self._busy:
            # Previous buffer still in flight. Watchdog: if the completion irq
            # somehow never fired, force-clear after a few frames -- by then even
            # a full-ring buffer has long since been copied.
            self._busy_ticks += 1
            if self._busy_ticks < 4:
                return
            self._busy = False
            self._busy_ticks = 0
        if not self.is_active():
            # Nothing playing: let the ring drain to silence and reset the
            # estimate, so the next sound starts from a known-empty ring
            # (auto_clear emits silence, not stale DMA).
            self._buffered = 0
            return
        want = AUDIO_IBUF_FRAMES - self._buffered
        if want <= 0:
            return                  # ring already full -> no under-run possible
        n = want if want < AUDIO_MAX_FRAME else AUDIO_MAX_FRAME
        try:
            # Reuse the persistent buffer: no per-frame allocation, and the
            # buffer the port holds a pointer to stays alive. memoryview gives
            # write() exactly the rendered slice.
            buf = self._bufs[self._buf]
            if self._na is not None:
                self._na.render(buf, n)
            else:
                self.engine.render_into(buf, n)
            self._buf ^= 1
            self._busy = True
            self.i2s.write(memoryview(buf)[:n * 2])
            self._buffered += n
        except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
            print("Moybyte audio tick failed:", exc)
            self._busy = False
            self.i2s = None


def make_audio(engine):
    """Injected backend factory (#16): wrap an AudioEngine in the device I2S
    backend. run_desktop hands this to the shared Workstation, the mirror of the
    host's make_audio. NEEDS ON-DEVICE VERIFICATION (see module docstring)."""
    return DeviceAudio(engine)
