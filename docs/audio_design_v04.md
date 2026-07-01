# Moybyte v0.4 Audio — design + vertical slice

**Issue:** #16 (Audio: sound effects + music, plus an on-device music editor)
**Status:** host vertical slice landed; device I2S path implemented (non-blocking feed, NEEDS ON-DEVICE VERIFICATION)
**Scope of this doc:** the cart-facing audio API, the shared sound data model, the
host backend, the device (T-Deck Plus I2S) backend, on-cart storage, and where the
on-device music/SFX editor fits the existing console UI.

This follows the same **portability contract** as the rest of v0.4 (see
`CLAUDE.md`): the cart-facing API and the sound *data model* are one codebase
shared by host and device; only the audio *output backend* differs. The same
`.moy` must sound the same on the PC simulator and on the T-Deck.

---

## 1. The cart-facing API (host == device, TIC-80/PICO-8 flavored)

Injected into the cart namespace by `make_api` (host: `runtime/host_app.py`,
device: `firmware/.../modules/moy_runtime.py`), exactly like the draw API:

```python
sfx(n, [chan])         # play sound effect n (0..63) now, optionally on a channel
beep(freq, [dur])      # one-shot raw tone: freq in Hz, dur in seconds (default 0.15)
music(track, [loop])   # start playing music track t (loops by default)
music_stop()           # stop the music track
sound_stop([chan])     # stop a channel (or all channels if omitted)
volume(level)          # master volume 0.0..1.0 (a parent/permission-respecting cap)
```

Design notes:

- **Indexed and data-driven**, like the palette and sprite sheet. `sfx(3)` plays
  SFX #3 from the cart's sound bank — the SFX *definition* is cart data, not code.
  This is what lets the future SFX editor edit "just data" and lets a cart be
  remixed without touching `main.py`.
- **Non-blocking.** `sfx`/`music` only *enqueue* on a software mixer; they never
  block the frame. Playback is advanced once per frame by the runtime
  (`audio.tick(dt)`), so it fits the **single-threaded desktop loop** — the same
  constraint that governs SD ops today (see `CLAUDE.md`). No background task is
  required for v1 (see §6).
- **`beep` is the zero-data escape hatch** — a cart with no sound bank can still
  make a tone. It is sugar for "a one-step SFX at this frequency".
- **`volume` is the only stateful global** and is clamped. The manifest carries a
  `"sound"` permission (matching plan §13) but the v0.4 console does **not** yet
  enforce *any* cart permissions (graphics/input aren't gated either), so wiring
  sound to the permission is deferred with the rest of the permission model; the
  `_SilentAudio` no-op backend is the natural hook when it lands.

---

## 2. The shared sound data model (`runtime/audio.py`)

One canonical, dependency-light module (only `math`), shared like
`runtime/editors.py`: imported as `runtime.audio` on the host and frozen as the
top-level `audio` module on the device (staged by `build.sh`). It contains **no
I/O and no hardware** — just the data model + a pure-Python software synth/mixer
that turns notes into PCM samples. Backends consume its sample stream.

### 2.1 Notes

A note is the atom both SFX and `beep` reduce to:

```text
note = (pitch, wave, vol)
  pitch : semitone index 0..95 (C0..B7), or -1 for "rest/off"
  wave  : 0 square, 1 triangle, 2 sawtooth, 3 noise  (PICO-8-ish small set)
  vol   : 0..7 (0 = silent)
```

`note_to_freq(pitch)` maps a semitone to Hz (A4=440, equal temperament). Helper
`name_to_pitch("C4")` lets the editor and carts use note names.

### 2.2 SFX

An SFX is a short sequence of steps played at a tempo — TIC-80's SFX tab / PICO-8's
SFX editor, simplified:

```json
{
  "speed": 8,          // steps-per-second
  "loop": false,
  "steps": [
    [48, 0, 6],        // [pitch, wave, vol] per step; pitch -1 = rest
    [50, 0, 5],
    [52, 0, 4]
  ]
}
```

`speed` is **steps-per-second** (kid-legible: "8 = eight blips a second"). A step's
duration is `1/speed` seconds; the mixer holds that step's note for that long.

### 2.3 Music tracks

A music track is an ordered list of SFX ids forming a phrase, with looping — a
deliberately tiny "tracker" for v1 (one channel of phrase playback). This is
enough for the PICO-8-style background loop the issue asks for, and it composes
from SFX the kid already authored:

```json
{ "speed": 4, "loop": true, "pattern": [0, 0, 1, 2] }
```

`speed` is SFX-slots-per-second; each entry in `pattern` is an SFX id played in
sequence, then looped. (A richer multi-channel tracker — TIC-80's `music` with
patterns × channels — is the natural v2; the data model leaves room: `pattern`
could become a list of rows of channels.)

### 2.4 The bank

A cart's whole sound bank is one object: a list of SFX and a list of tracks.

```json
{ "sfx": [ {..sfx0..}, {..sfx1..} ], "music": [ {..track0..} ] }
```

`AudioBank.from_dict` / `to_dict` round-trip it; `AudioBank.default()` is a small
friendly starter bank (a coin blip, a jump, a tiny loop) so a new cart and the
editor are never empty.

### 2.5 The mixer (`AudioEngine`)

Pure Python, no backend:

- `AudioEngine(bank, rate=11025)` — low sample rate keeps the device CPU/RAM sane.
- `play_sfx(n, chan=...)`, `play_beep(freq, dur)`, `play_music(track, loop=True)`,
  `stop_music()`, `stop(chan=None)`, `set_volume(v)`.
- `render(nframes) -> bytes` — pull `nframes` of signed-16-bit mono PCM, mixing
  all active channels + music. Advances internal phase/step cursors. This is the
  single primitive every backend needs: **host** feeds it to an audio device (or
  records it, for tests); **device** feeds it to the I2S DMA buffer.
- A fixed small number of channels (`CHANNELS = 4`): `sfx` round-robins across the
  SFX channels, music owns the last channel.

`render` is the seam that keeps host and device identical: both compute the *same
samples* from the *same bank*; they differ only in where those bytes go.

---

## 3. Storage: where a cart's audio lives

Alongside `main.py` / `sprites.moygfx` / `config.json`, a new file:

```text
my_cart.moy/
  manifest.json
  main.py
  config.json
  sprites.moygfx
  sounds.json     <-- the AudioBank (sfx + music), JSON
```

`sounds.json` is plain JSON (kid-inspectable, diff-friendly, MicroPython
`json`-only — same constraints as `moy_carts.py`). Added to `runtime/moy_carts.py`:

- `load()` reads `sounds.json` into `cart["sounds"]` (None if absent).
- `save_sounds(cart, bank_dict)` writes it **atomically** via the existing
  `_write_atomic` (the same crash-safe path sprites/code use).
- `seed_builtins` writes a cart's `sounds` blob if the seed carries one.

The runtime builds an `AudioEngine` from `cart["sounds"]` (or `AudioBank.default()`
if absent) when a cart opens, the mirror of how `_build_sheet()` builds the
SpriteSheet.

The manifest carries a `"sound"` permission (matches §13 of the plan doc), but
audio is not yet gated on it — see §1 (the whole permission model is future work).

---

## 4. Host backend (`runtime/host_app.py`)

`make_api` gains the audio functions, bound to a host audio backend:

- **`FakeAudio`** (default for tests/headless): records every call
  (`("sfx", n, chan)`, `("beep", f, d)`, `("music", t, loop)`, ...) and still
  drives the `AudioEngine` so `render()` is exercised. Mirrors the existing sim
  fakes (`moybyte_sim` fake audio, `moybyte/audio.py` `AudioService.calls`).
  **No sound hardware required** — this is what the headless tests use.
- **`SdlAudio`** (optional, only when real playback is wanted): opens a pygame/SDL
  audio stream and pushes `engine.render()` from a callback. Gated behind
  availability so `SDL_VIDEODRIVER=dummy` test runs never touch it. (Designed;
  the slice ships `FakeAudio` + the engine; `SdlAudio` is a thin follow-on.)

The backend wraps an `AudioEngine`; `make_api`'s functions call into it. The host
loop can pull `engine.render()` from an audio callback (SdlAudio) — for the fake
backend the calls + the engine state are enough to assert behavior headlessly.

---

## 5. Device backend (T-Deck Plus, I2S MAX98357) — STUB, NEEDS ON-DEVICE VERIFICATION

The T-Deck Plus has a MAX98357 I2S class-D amp + speaker. From the LilyGO
reference (`firmware/lilygo_t_deck_plus_reference/examples/I2SPlay/utilities.h`):

```text
I2S_BCK  = GPIO 7   (bit clock)
I2S_WS   = GPIO 5   (word select / LRCLK)
I2S_DOUT = GPIO 6   (data out)
```

**Crucially, I2S is a *separate* peripheral from the shared SPI host** that the
display + SD fight over — so audio does **not** collide with the display/SD bus
takeover constraints (`CLAUDE.md`). That removes the scariest risk; the open risk
is purely CPU budget in the single-threaded loop.

Also crucial: **`BOARD_POWERON` (GPIO 10) must be HIGH** to power the board
peripherals — the amp *and* the panel sit behind it (the reference sets it in every
`setup()`). Moybyte already drives it at boot (`tdeck_board.init_board_pins`), and
the panel works, so the amp is powered. There is **no separate amp enable / SD-mode
/ gain GPIO** on the T-Deck Plus (the only audio pins are BCK/WS/DOUT; the ES7210 is
the *mic*, unrelated). So pins + power + format are all correct — silence is the
I2S *init* or the *feed*, not the wiring.

> **Correction (the original sketch was wrong about blocking).** MicroPython
> `machine.I2S.write()` is **BLOCKING by default** — it copies the whole buffer into
> the DMA ring before returning, *waiting* if the ring is full. Rendering `rate*dt`
> samples and blocking-`write()`ing them every frame stalls the single-threaded loop
> for ~one frame of audio per frame: that is the observed **FPS drop**, and the
> jitter under-runs the DMA into **crackle**. Non-blocking is **opt-in** via
> `I2S.irq(handler)` (which switches the port into non-blocking mode: `write()`
> returns immediately, the port copies your buffer on a background FreeRTOS task,
> and fires `handler` on completion) or the uasyncio path. The ESP32 non-blocking
> `write()` keeps a *pointer* to the caller buffer until that copy finishes, with a
> queue depth of 1 (a second in-flight write is silently dropped).

The implemented `DeviceAudio` (`moy_runtime.py`, behind a try/except so a board
without the amp degrades to silence, never a crash):

```python
from machine import I2S, Pin
i2s = I2S(0, sck=Pin(7), ws=Pin(5), sd=Pin(6),
          mode=I2S.TX, bits=16, format=I2S.MONO,
          rate=8000, ibuf=4096)
i2s.irq(self._on_done)          # -> NON-blocking mode; _on_done clears self._busy
# each frame, between draws (single-threaded, like SD ops):
if not self._busy and engine.is_active():
    n = min(int(engine.rate * dt), AUDIO_MAX_FRAME)
    engine.render_into(buf, n)  # ONE persistent double-buffer, no per-frame alloc
    self._busy = True
    i2s.write(memoryview(buf)[:n * 2])   # returns immediately; ibuf rides out jitter
```

Two persistent buffers are alternated so the buffer the port still holds a pointer
to is never reused or GC'd mid-copy; `_busy` (cleared by the irq) gates the next
write so a write is issued only when the previous copy is done — the DMA `ibuf`
covers any skipped frame. Rate is **8 kHz** (the reference `SimpleTone` rate) to
halve the per-frame mixer cost; `render_into` skips all work when nothing plays.

**This path is written but NOT verified on hardware in this environment.** What a
hardware spike must confirm:

1. The I2S pins/format above actually drive the MAX98357 audibly (boot log prints
   `Moybyte audio: I2S ready ...` on success, or `I2S UNAVAILABLE, silent: <exc>`
   if the constructor raised — read it during the ~2 s boot window).
2. The pure-Python mixer at 8 kHz fits the per-frame CPU budget at 30 FPS without
   dropping the desktop below playable (measure). If still too slow, a native
   `moy_audio` C mixer (like `moy_gfx`) is the escalation; the model/format/`render_into`
   seam stay identical.
3. Non-blocking `write()` + the `_busy` gate never stalls a frame and never crackles
   (the ibuf should absorb jitter).
4. Whether a uasyncio background feeder is worth it (§6) vs. the synchronous
   per-frame non-blocking `write`.

---

## 6. Open question answered: background task vs. per-frame

**v1: serve audio per-frame in the single-threaded desktop loop** — same as SD ops.
Each frame the runtime renders `min(rate*dt, AUDIO_MAX_FRAME)` samples and **issues a
non-blocking I2S `write()`** (host: the audio callback pulls). The write returns
immediately; the port copies on its own task and the DMA `ibuf` (a few KB) absorbs
frame jitter — at 8 kHz / 30 FPS that's ~267 samples/frame, well within one DMA
buffer. (The earlier note assumed `write()` was non-blocking by default; it is not —
see the §5 correction. The single-threaded loop still works, but only because the
write is made non-blocking via `I2S.irq()`.) A background uasyncio feeder is a
**possible v2 optimization** if music + heavy draws ever starve the buffer, but it
adds the multitasking the v0.4 plan explicitly defers (plan §6.3). Keep v1 the
synchronous non-blocking per-frame feed.

---

## 7. On-device music/SFX editor — fits the existing console UI

The console already tabs Cards / Code / Paint (`runtime/console.py` `menu_view`,
`set_menu_view`, overlay buttons `_MENU_BTN`/`_PAINT_BTN`, full-screen editors).
A **Sound** view slots in identically:

- `menu_view == "sound"`, a `SoundEditor` core in `runtime/editors.py` (pure logic,
  like `PaintEditor`): current SFX id, current step, and edit ops
  (`set_pitch`/`set_wave`/`set_vol`/`set_speed`/`select_sfx`/`preview`).
- A `_SOUND_BTN` overlay on the desktop + a `SOUND` icon, plus tab routing in
  `_open_menu` / `set_menu_view` (build the editor, no keyboard text mode).
- Layout mirrors the paint editor: a **piano-roll grid** (steps × pitch) the kid
  taps to place notes, wave/vol pickers, a speed gauge, a PLAY button that calls
  `engine.play_sfx(n)`, and SAVE (→ `moy_carts.save_sounds`) / CLOSE.
- A tiny **music** sub-tab: an ordered strip of SFX-id slots (the `pattern`), tap a
  slot to set its SFX, PLAY to loop.

This is **designed, not built** in this slice — the issue scopes the slice to the
audio *core*, not a full tracker UI. `SoundEditor` is the clean next PR: it has no
new backend dependencies (it drives the already-landed `AudioEngine`), so it is a
pure-host-verifiable addition the same way the paint editor was.

---

## 8. What this slice delivers vs. defers

**Delivered (host-verified):**

- `runtime/audio.py`: notes, SFX, music, `AudioBank`, `AudioEngine` (pure-Python
  synth + mixer + `render()`), dependency-light (only `math`).
- `runtime/host_app.py`: `sfx`/`beep`/`music`/`music_stop`/`sound_stop`/`volume`
  in `make_api`, bound to a `FakeAudio` backend (records calls + drives the mixer).
- `runtime/moy_carts.py`: `sounds.json` load + `save_sounds` (atomic) + seed.
- `system_carts/beeper.moy`: a tiny demo cart that plays a beep + an SFX on tap.
- `tests/test_audio.py`: headless tests of the model, mixer, API surface, store
  round-trip, and the demo cart making sound through the fake backend.

**Deferred (clearly flagged):**

- `DeviceAudio` I2S backend in `moy_runtime.py` is **stubbed and unverified** —
  needs the hardware spike in §5.
- The `SoundEditor` UI (§7) is **designed only**.
- Multi-channel tracker, instruments/envelopes, note slides, a native `moy_audio`
  C mixer, and parent volume policy are v2.

## 9. Sensible next step

Land `SoundEditor` (§7) as the next PR — pure host logic over the already-verified
`AudioEngine`, tabbed into the console like Paint — then do the §5 hardware spike
to wire `DeviceAudio` and confirm the I2S path + CPU budget on the T-Deck.
