"""The device AUDIO backend (#16), extracted from moy_runtime.py.

DeviceAudio wraps the shared AudioEngine and pushes mixed PCM to the T-Deck Plus's
MAX98357 I2S class-D amp -- the LIVE counterpart of the host's fake audio, so the
same cart audio API runs host == device. The heavy per-sample mix runs through the
native `moy_audio` kernel, optionally on a core-1 task (MOY_AUDIO_CORE1) so it
never steals frame-loop time. run_desktop injects make_audio into the shared
Workstation.

Self-contained: imports the leaf device_util (diag + tick helpers) and lazily the
native `moy_audio` + `machine.I2S` inside its methods, so no moy_runtime cycle.
Device-only module (modules/, auto-frozen).

NEEDS ON-DEVICE VERIFICATION -- this is a pure code move of the existing backend;
the native I2S + core-1 audio paths cannot be exercised by the host test shim
(moy_audio isn't present under CPython), so a board smoke is required before
trusting this extraction.
"""
from device_util import _diag_note, _ticks_ms, _ticks_diff


# --- Audio backend (#16) -- I2S to the MAX98357 amp -------------------------
# The T-Deck Plus has a MAX98357 I2S class-D amp + speaker on a SEPARATE peripheral
# from the shared display/SD SPI host, so audio does NOT collide with the SD/display
# bus-takeover constraints (see CLAUDE.md). Pin map + power gate from the LilyGO
# reference (examples/I2SPlay/utilities.h + I2SPlay.ino / SimpleTone.ino):
#     I2S_BCK = GPIO 7, I2S_WS = GPIO 5 (LRCK), I2S_DOUT = GPIO 6
#     BOARD_POWERON = GPIO 10 must be HIGH (already driven at boot by tdeck_board)
# There is NO separate amp enable / SD-mode / gain GPIO on this board -- the amp's
# SD pin is hardwired and the only power gate is BOARD_POWERON (confirmed: the panel
# also lives behind it, and the panel works, so the amp is powered too). I2S.MONO
# puts samples on the left slot, which is the MAX98357's mono input. So pins, power
# and format are all correct; if it is silent the failure is the I2S *init* (made
# loud below) or the *feed*, not the wiring.
#
# THE FEED -- THE CRACKLE FIX (#41): a dedicated core-1 audio task.
# The root cause of the crackle was that the I2S feed was COUPLED to the render loop:
# DeviceAudio.tick() ran once per frame on core 0 (the MicroPython VM core), and a
# render frame is ~50-80 ms (12-14 fps). During a long draw the I2S DMA ring drained
# and under-ran -> crackle. A deeper ring + bigger feed cap only helped "a bit"
# because the feed CADENCE was still the slow, jittery frame rate.
#
# The fix (owner's call, matches PixelRoot / most MCU games): feed I2S from a
# DEDICATED native FreeRTOS task PINNED TO CORE 1, decoupled from rendering, so the
# DMA is topped up continuously no matter how slow core 0's frame is. The ESP32-S3 is
# dual-core; MicroPython's VM is single-core (core 0), but the native moy_audio C task
# runs on core 1 fully independently. I2S is on its OWN pins (separate from the shared
# SPI display/SD bus -- see #40), so core 1 owning I2S never touches the panel/SD path.
#
# THE SPLIT (see native/moy_audio/modmoy_audio.c for the C side):
#   core 0 (this Python): owns the model + control surface + music scheduler. Each
#       frame tick() runs the music scheduler and COMMITS every voice's state into the
#       shared C moy_voices[] (moy_audio.voice_commit, bracketed by voice_lock/unlock so
#       the commit is atomic vs. the task's snapshot). It does NO per-sample work and
#       NO I2S write. To advance phrases it reads moy_audio.active_mask() -- the bit set
#       the core-1 task last published per still-playing voice.
#   core 1 (C task): owns the IDF i2s_std channel + the write loop. Each block it
#       snapshots moy_voices[] under the mutex, mixes (the heavy per-sample loop), and
#       writes to I2S (blocking on the DMA drain -- which paces it to the audio clock,
#       on core 1, so the VM never stalls), then folds the advanced cursor back.
#
# FALLBACK (revert-able with NO rebuild): if the core-1 task can't start (no moy_audio,
# old build, channel/task creation fails) DeviceAudio uses the LEGACY single-core feed
# -- machine.I2S non-blocking writes fed per-frame from tick(), the per-block
# voice_set/render/voice_read kernel. Set MOY_AUDIO_CORE1 = False below to force that
# path even when the task is available (e.g. to A/B a bad result).
#
# The legacy feed's mechanics (for reference): MicroPython machine.I2S.write() is
# BLOCKING by default; I2S.irq() flips it NON_BLOCKING (the port copies our buffer on
# its own task and fires our callback). The non-blocking write keeps a POINTER to the
# caller's buffer until the copy finishes, so we keep the buffer alive (a persistent
# double-buffer) and only write when the previous copy is done.
#
# STILL NEEDS ON-DEVICE VERIFICATION: that the core-1 task actually drives the amp
# audibly with no crackle and no FPS drop. Do NOT claim it is tested on hardware.

# Master switch for the core-1 audio task (#41). True: prefer the dedicated core-1
# I2S feeder (the crackle fix); fall back to the legacy per-frame feed if it can't
# start. Set False to FORCE the legacy single-core feed (revert without a rebuild).
MOY_AUDIO_CORE1 = True

I2S_BCK = 7
I2S_WS = 5
I2S_DOUT = 6
# 8 kHz mono: matches the reference SimpleTone rate and halves the per-frame mixer
# cost vs. 11025. DeviceAudio retunes the shared engine to this rate in __init__ so
# render_into() sizes its blocks to match the I2S port.
AUDIO_RATE = 8000
# DMA ring buffer (bytes). ~0.5 s of 8 kHz/16-bit mono -- a deep cushion so the
# speaker never under-runs across the slow/variable render frames (12-15 fps today
# -> 66-83 ms apart) plus jitter. The ring is internal DMA RAM but tiny in bytes
# (8 KB), so a generous cushion is cheap.
AUDIO_IBUF = 8192
# Ring capacity in FRAMES (16-bit mono -> 2 bytes/frame). The single-core feed tops
# the ring up TOWARD this each tick (see below) instead of feeding exactly rate*dt.
AUDIO_IBUF_FRAMES = AUDIO_IBUF // 2
# Cap on a single tick's render/write, in frames. SINGLE-CORE CRACKLE FIX: tick()
# no longer feeds exactly rate*dt (which kept the ring hovering near-empty -> any
# 50-60 ms draw under-ran it). Instead it TOPS THE RING UP toward AUDIO_IBUF_FRAMES
# every tick, so the deep ~0.5 s ring stays full and rides out long draws + jitter.
# A single non-blocking write can therefore be as large as the whole ring (the
# native moy_audio mixer makes a big block cheap), so the cap is the full ring -- it
# only bounds the rare cold-start fill, never a steady-state top-up.
AUDIO_MAX_FRAME = AUDIO_IBUF_FRAMES

# Audio diagnostics (moybyte_diag): log each sfx/music trigger and, in core-1 mode,
# a periodic "active=N committed=M" sample, so the owner can read on serial/SD
# exactly what reached the mixer (the Battle City rapid-sfx case). Gated so it can
# NEVER flood the diag ring: triggers log on the event (each sfx/music call), and
# the core-1 active sample logs at most once every AUDIO_DIAG_SAMPLE_MS.
AUDIO_DIAG = True
AUDIO_DIAG_SAMPLE_MS = 1000

_AUDIO_BACKEND_SEQ = 0


class DeviceAudio:
    """I2S audio backend for the T-Deck. Wraps the shared AudioEngine. Two feeds:

    * CORE-1 task (#41, default -- the crackle fix): the native moy_audio C module owns
      the IDF i2s_std channel and a FreeRTOS task pinned to core 1 that mixes + writes
      I2S continuously, decoupled from the render loop. tick() only runs the music
      scheduler and commits voice state across to C -- no per-sample mix, no I2S write
      on core 0. This is what stops the crackle (audio is fed no matter how slow a
      frame is).
    * LEGACY single-core feed (fallback): if the core-1 task can't start, fall back to
      machine.I2S non-blocking writes fed per-frame from tick(), with the native
      per-block kernel (or the pure-Python mixer if moy_audio is absent). Set the
      module-level MOY_AUDIO_CORE1 = False to FORCE this path (revert with no rebuild).

    Constructed behind try/except at every step so a board/build without moy_audio or
    I2S degrades to a quieter mode (or silence), never a crash.

    NEEDS ON-DEVICE VERIFICATION -- written to the reference pins/power/format but
    unproven on hardware in this environment (see the module comment above)."""

    def __init__(self, engine):
        global _AUDIO_BACKEND_SEQ
        _AUDIO_BACKEND_SEQ += 1
        self.engine = engine
        self._diag_seq = _AUDIO_BACKEND_SEQ
        # The shared engine is built at its default 11025 Hz; the device renders at
        # 8 kHz to halve the per-frame mixer cost (only render_into reads .rate, live,
        # so retuning it here is safe) and to match the I2S port's configured rate.
        engine.rate = AUDIO_RATE
        self.i2s = None
        self._core1 = False        # True once the core-1 feeder task is running
        self._reused_core1 = False # True when audio_start found the global task alive
        # Core-1 commit tracking: the C task owns per-sample advancement (idx/t/phase)
        # once a voice is committed, so we must NOT re-commit a voice's (now stale)
        # Python cursor every frame -- that would reset it to step 0 and stutter. We
        # only commit a voice the frame it is (re)triggered or stopped. THE BATTLE CITY
        # FIX (#41): detect that by the voice's monotonic _Voice.gen counter (bumped on
        # EVERY play()/stop()), NOT by (id(steps), active). id(steps) is unreliable --
        # MicroPython's GC can hand a freshly allocated steps list the SAME address as
        # the just-freed previous one, so a rapid retrigger of the same SFX (Battle City
        # fires many sfx/s) read as "unchanged" and was silently never committed -> the
        # note never reached the mixer. gen changes on every trigger, so every sfx --
        # rapid, overlapping, channel-reused -- now reliably commits.
        self._commit_gen = [None] * len(engine.voices)
        # Per-voice flag: a voice committed-active whose play the core-1 task has NOT
        # yet confirmed in active_mask() (the task snapshots ~every block, ~32 ms, so a
        # fresh trigger may not be reflected for a frame or two). While pending we do
        # NOT let a stale clear mask mark the voice free -- otherwise a fast frame could
        # see the just-started voice as idle and steal the channel mid-note.
        self._await_active = [False] * len(engine.voices)
        # Diag: a periodic core-1 "active=N committed=M" sample (rate-limited) + a
        # running count of triggers committed since the last sample, so the owner can
        # read whether Battle City's rapid sfx reach the task. _diag_t0 is the last
        # sample's ticks_ms; _diag_committed accumulates triggers between samples.
        self._diag_t0 = 0
        self._diag_committed = 0
        # Legacy-feed double buffer (only used in the fallback path): render alternates
        # into bufs[_buf], write()s it non-blocking; the port copies it on a background
        # task and fires _on_done. We never touch a buffer while its copy is in flight
        # (_busy). Persistent bytearrays => the GC can't collect an in-flight one.
        self._bufs = (bytearray(AUDIO_MAX_FRAME * 2), bytearray(AUDIO_MAX_FRAME * 2))
        self._buf = 0
        self._busy = False
        self._busy_ticks = 0       # watchdog: frames the busy flag has been stuck set
        # Single-core TOP-UP accounting (#41 single-core crackle fix): a software
        # estimate of how many frames are still queued in the I2S DMA ring. Each tick
        # we subtract what the speaker consumed (rate*dt) and add what we wrote, then
        # refill toward AUDIO_IBUF_FRAMES. This is the lever that keeps the ring full
        # (a deep cushion) instead of hovering near-empty. Conservative: it can only
        # UNDER-estimate occupancy (we floor at 0 and the ring's own back-pressure --
        # a full ring drops the tail of an over-long write -- caps the real depth), so
        # it never tricks us into starving the ring.
        self._buffered = 0
        # NATIVE moy_audio (#16/#41): the per-sample mix is the device bottleneck (the
        # pure-Python render_into runs the beeper at ~12 FPS and crackles), and the
        # I2S feed must run off the render core (#41). When moy_audio is frozen in we
        # prefer it for both; Python still owns the model/control/music scheduler. A
        # build WITHOUT moy_audio falls back to engine.render_into + machine.I2S, so the
        # firmware still works and the host is unaffected.
        try:
            import moy_audio
            self._moy_audio = moy_audio
            print("Moybyte audio: native moy_audio mixer ENABLED")
        except Exception:   # noqa: BLE001 -- no native module -> Python mixer fallback
            self._moy_audio = None
            print("Moybyte audio: native moy_audio absent, using Python mixer")

        # 1) Preferred path: hand I2S to the dedicated core-1 task (the crackle fix).
        if MOY_AUDIO_CORE1 and self._moy_audio is not None:
            try:
                self._moy_audio.set_master(engine.volume)
                already = False
                try:
                    already = bool(self._moy_audio.running())
                except Exception:
                    already = False
                if self._moy_audio.audio_start(I2S_BCK, I2S_WS, I2S_DOUT, AUDIO_RATE):
                    self._core1 = True
                    self._reused_core1 = already
                    verb = "reused" if already else "started"
                    _diag_note("audio", "core-1 I2S task %s (%d Hz mono, "
                               "BCK=%d WS=%d DOUT=%d)"
                               % (verb, AUDIO_RATE, I2S_BCK, I2S_WS, I2S_DOUT))
                    print("Moybyte audio: core-1 I2S feeder %s"
                          % ("REUSED" if already else "ENABLED"))
                else:
                    _diag_note("audio", "core-1 task unavailable, legacy feed")
            except Exception as exc:  # noqa: BLE001 -- any failure -> legacy feed
                _diag_note("audio", "core-1 start failed (%s), legacy feed" % (exc,))
                self._core1 = False

        # 2) Fallback path: open machine.I2S for the legacy per-frame feed. Skip it
        #    when the core-1 task owns the I2S peripheral (two owners would clash).
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
                _diag_note("audio", "legacy I2S ready (%d Hz mono, BCK=%d WS=%d DOUT=%d)"
                           % (AUDIO_RATE, I2S_BCK, I2S_WS, I2S_DOUT))
            except Exception as exc:  # noqa: BLE001 -- no amp / no I2S -> stay silent
                # LOUD: if audio is silent on-device this is the line to look for in
                # the ~2 s boot log AND the persisted diag dump (the takeover loop
                # starves serial, so the offline diag is the only post-boot view).
                _diag_note("audio", "I2S UNAVAILABLE, silent: %s" % (exc,))
                self.i2s = None

    def _on_done(self, _i2s):
        """I2S non-blocking completion callback (legacy feed): the background copy of
        the last buffer into the DMA ring is done, so it's safe to render into / write
        the next one. Runs via mp_sched (between bytecodes), so just clears the flag."""
        self._busy = False
        self._busy_ticks = 0

    def diag_state(self):
        """Tuple consumed by Player diagnostics:
        (backend_seq, core1, reused_task, native_running, active_voices,
        committed_since_sample). Guarded so profiling never affects playback."""
        running = -1
        active = -1
        try:
            if self._moy_audio is not None:
                try:
                    running = 1 if self._moy_audio.running() else 0
                except Exception:
                    running = -1
                try:
                    mask = self._moy_audio.active_mask()
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
                running,
                active,
                self._diag_committed)

    # control surface (mirrors host FakeAudio / _SilentAudio) -------------
    def sfx(self, n, chan=None):
        self.engine.play_sfx(n, chan)
        if AUDIO_DIAG:
            self._diag_trigger("sfx", n, chan)

    def beep(self, freq, dur=0.15):
        self.engine.play_beep(freq, dur)
        if AUDIO_DIAG:
            self._diag_trigger("beep", int(freq), None)

    def music(self, track, loop=True):
        self.engine.play_music(track, loop)
        if AUDIO_DIAG:
            self._diag_trigger("music", track, None)

    def _diag_trigger(self, kind, n, chan):
        """Log one sfx/beep/music trigger to moybyte_diag (event-gated, so it cannot
        flood -- one line per actual call). Reports the path so the owner can tell at
        a glance which feed is live: feed=core1 vs feed=single. Fully guarded."""
        try:
            feed = "core1" if self._core1 else "single"
            ch = "auto" if chan is None else chan
            _diag_note("AUDIO", "%s=%s chan=%s feed=%s" % (kind, n, ch, feed))
        except Exception:   # noqa: BLE001 -- diag must never crash a trigger
            pass

    def music_stop(self):
        self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.engine.stop(chan)

    def volume(self, level):
        self.engine.set_volume(level)
        # Publish the live master volume to the core-1 task (read each mix block).
        if self._core1:
            try:
                self._moy_audio.set_master(self.engine.volume)
            except Exception:   # noqa: BLE001 -- volume must never crash the loop
                pass

    # -- core-1 feed: commit voice state across, advance the scheduler ----
    def _tick_core1(self, dt):
        """The crackle fix's per-frame core-0 work: run the music scheduler in Python,
        commit every voice that was (re)triggered/stopped this frame into the shared C
        moy_voices[] (atomically, under the moy_audio mutex), and read the core-1 task's
        published active mask back so the scheduler / is_active() see the truth. NO
        per-sample mix and NO I2S write happen here -- the core-1 task does both,
        continuously, off the render core. Intentionally cheap (a few C calls), so a
        slow frame can never starve the speaker.

        WHY ONLY DIRTY VOICES: once a voice is committed the C task owns its per-sample
        advancement (idx/t/phase/noise). The Python _Voice's cursor goes stale (we do
        NOT pull it back -- that would be a chatty cross-core read), so re-committing it
        every frame would reset the C voice to step 0 and stutter. A voice only needs a
        fresh commit when Python (re)triggers or stops it, which we detect by a change
        in _Voice.gen (bumped on every play()/stop()). gen -- not (id(steps), active) --
        is what fixes the Battle City regression: id(steps) aliases on GC reuse, so a
        rapid same-SFX retrigger read as unchanged and was never committed (#41)."""
        eng = self.engine
        ka = self._moy_audio
        voices = eng.voices
        nv = len(voices)
        # Read the task's published activity FIRST, into the Python voices that the C
        # task owns, so the scheduler's free-channel pick / is_active() reflect voices
        # the task has finished playing.
        try:
            mask = ka.active_mask()
        except Exception:   # noqa: BLE001 -- never crash the loop on a status read
            mask = None
        if mask is not None:
            for c in range(nv):
                v = voices[c]
                bit_set = bool(mask & (1 << c))
                if bit_set:
                    # task confirms this play is live -> a later clear is now trusted.
                    self._await_active[c] = False
                elif (v.active and not self._await_active[c]
                      and v.gen == self._commit_gen[c]):
                    # task says voice c is done AND we've already seen it go live AND
                    # Python hasn't RE-triggered it since our last commit (gen still
                    # matches) -> this clear is real, reflect it so the scheduler can
                    # reuse the channel. The gen guard is critical: if the cart already
                    # fired a fresh sfx on this channel this frame (gen advanced), the
                    # commit below owns it -- we must NOT clobber its active flag here.
                    v.active = False
        # Music scheduler (Python) -- step it by the real elapsed frame time. It may
        # retrigger SFX onto voices; those bump gen and are committed below.
        eng._advance_music(dt)
        # Commit EVERY voice whose gen changed since our last commit, atomically vs. the
        # task's snapshot (voice_lock brackets the whole set). Every (re)trigger bumps
        # gen, so rapid/overlapping/channel-reused sfx all commit -- nothing is dropped.
        dirty = []
        for c in range(nv):
            if voices[c].gen != self._commit_gen[c]:
                dirty.append(c)
        if dirty:
            ka.voice_lock()
            try:
                for c in dirty:
                    v = voices[c]
                    ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop,
                                 v.idx, v.t, v.phase, v.noise)
            finally:
                ka.voice_unlock()
            for c in dirty:
                v = voices[c]
                self._commit_gen[c] = v.gen
                # A freshly committed ACTIVE voice must not be cleared by a stale mask
                # until the task confirms it live at least once (see __init__).
                self._await_active[c] = bool(v.active)
                if v.active:
                    self._diag_committed += 1
        if AUDIO_DIAG:
            self._diag_core1_sample(mask)

    def _diag_core1_sample(self, mask):
        """Rate-limited core-1 health sample: at most once per AUDIO_DIAG_SAMPLE_MS log
        the active-voice count (from the task's published mask) + how many triggers we
        committed since the last sample. Lets the owner confirm Battle City's rapid sfx
        are actually reaching the task (committed climbs, active>0). Fully guarded so it
        can never crash the loop, and gated so it can never flood the diag ring."""
        try:
            now = _ticks_ms()
            if _ticks_diff(now, self._diag_t0) < AUDIO_DIAG_SAMPLE_MS:
                return
            self._diag_t0 = now
            active = 0
            if mask:
                m = mask
                while m:
                    active += m & 1
                    m >>= 1
            committed = self._diag_committed
            self._diag_committed = 0
            # Only emit a line when something is going on, so a silent UI never logs.
            if active or committed:
                _diag_note("AUDIO", "core1 active=%d committed=%d" % (active, committed))
        except Exception:   # noqa: BLE001 -- diag must never crash the loop
            pass

    def tick(self, dt):
        """Per-frame audio work. In core-1 mode this only schedules + commits voice
        state (the core-1 task feeds I2S); in the legacy fallback it renders this
        frame's PCM and streams it to the DMA ring NON-BLOCKINGLY. Either way it must
        never stall the single-threaded desktop loop."""
        if self._core1:
            try:
                self._tick_core1(dt)
            except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
                print("Moybyte audio tick (core1) failed:", exc)
            return

        # --- legacy single-core feed (fallback) -- TOP-UP, the crackle fix ---
        # CRACKLE ROOT CAUSE (single-core): the old feed rendered exactly rate*dt per
        # frame, so the DMA ring only ever held about one frame's worth -- it hovered
        # near-empty and ANY 50-60 ms long draw / GC pause drained it to an under-run
        # (the crackle). THE FIX: top the deep (~0.5 s) ring UP toward full each tick
        # instead of just replacing what was consumed, so the cushion absorbs long
        # frames + jitter. We track buffered frames in software (_buffered): subtract
        # what the speaker drained since the last tick, then refill toward the cap.
        if self.i2s is None:
            return
        # Account for what the DMA drained since the last tick (real elapsed audio
        # time). Floor at 0 so the estimate can only UNDER-state occupancy (safe: we
        # over-fill rather than starve; the ring's own back-pressure caps the truth).
        drained = int(self.engine.rate * dt)
        self._buffered -= drained
        if self._buffered < 0:
            self._buffered = 0
        if self._busy:
            # Previous buffer still in flight -> the port is still copying it into the
            # ring, so we can't reuse the buffer yet. Watchdog: if the completion irq
            # somehow never fired (so _busy would stick and silence the rest of the
            # session), force-clear after a few frames -- by then even a full-ring
            # buffer has long since been copied, so a fresh write is safe.
            self._busy_ticks += 1
            if self._busy_ticks < 4:
                return
            self._busy = False
            self._busy_ticks = 0
        if not self.engine.is_active():
            # Nothing playing: let the ring drain to silence; reset the estimate so the
            # next sound starts from a known-empty ring (auto_clear emits silence).
            self._buffered = 0
            return
        # Refill toward a FULL ring. want = the deficit; render that much (capped to
        # the persistent buffer / a single write). The native moy_audio mixer makes a
        # big block cheap, so a deep top-up costs little and buys a long cushion.
        want = AUDIO_IBUF_FRAMES - self._buffered
        if want <= 0:
            return                  # ring already full -> skip this tick, no under-run
        n = want
        if n > AUDIO_MAX_FRAME:
            n = AUDIO_MAX_FRAME
        try:
            # render reuses our persistent buffer (no per-frame allocation, and the
            # buffer the port holds a pointer to stays alive); memoryview gives
            # write() exactly the rendered slice. Prefer the native moy_audio kernel
            # for the heavy mix; fall back to the pure-Python render_into when the
            # native module isn't frozen in (so a build without it still works).
            buf = self._bufs[self._buf]
            if self._moy_audio is not None:
                self._render_native(buf, n)
            else:
                self.engine.render_into(buf, n)
            self._buf ^= 1
            self._busy = True
            self.i2s.write(memoryview(buf)[:n * 2])
            self._buffered += n      # n more frames now queued toward the ring
        except Exception as exc:  # noqa: BLE001 -- audio must never crash the loop
            print("Moybyte audio tick failed:", exc)
            self._busy = False
            self.i2s = None

    def _render_native(self, buf, n):
        """LEGACY per-block feed: render `n` frames into `buf` using the native moy_audio
        kernel for the heavy per-sample loop, keeping the Python AudioEngine the single
        source of truth. Same sequence as AudioEngine.render_into, ONLY the inner sample
        loop delegated to C:
          1. advance the music phrase scheduler in Python (it may retrigger SFX),
          2. push each voice's exact state into the C mirror (voice_set),
          3. C mixes the whole block (the part that was too slow in MicroPython),
          4. read the advanced render state back into the Python voices (voice_read)
             so is_active() / the next block's scheduler see the truth.
        Because C holds no cross-block state, the output is identical to the pure-
        Python mixer -- same .moy, same samples on host and device. (Used only in the
        legacy single-core feed; the core-1 task uses its own snapshot/mix loop.)"""
        eng = self.engine
        ka = self._moy_audio
        # 1. music scheduler (Python) -- same dt_frame math as render_into.
        eng._advance_music(n / float(eng.rate))
        voices = eng.voices
        # 2. push exact voice state into C.
        for c in range(len(voices)):
            v = voices[c]
            ka.voice_set(c, v.active, v.steps, v.step_dur, v.loop,
                         v.idx, v.t, v.phase, v.noise)
        # 3. the heavy mix, in C.
        ka.render(buf, n, eng.rate, eng.volume)
        # 4. read the advanced state back into the Python voices.
        for c in range(len(voices)):
            st = ka.voice_read(c)
            if st is not None:
                v = voices[c]
                v.active = st[0]
                v.idx = st[1]
                v.t = st[2]
                v.phase = st[3]
                v.noise = st[4]


def make_audio(engine):
    """Injected backend factory (#16): wrap an AudioEngine in the device I2S
    backend. run_desktop hands this to the shared Workstation, the mirror of the
    host's make_audio. NEEDS ON-DEVICE VERIFICATION (see module comment)."""
    return DeviceAudio(engine)
