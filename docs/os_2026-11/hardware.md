# The OS (2026-11): hardware, timing and media

Phase 2 of the order of work, the event rings phase 1 starts and phase 3's
TLS. Read `docs/os_2026-11.md` first.

---

## 1. The hardware model

**Decision (owner, 2026-11-01): the board declares its hardware; the OS owns
what it uses; apps lease the rest.** Every resource is one of:

| class | meaning | T-Deck examples |
|---|---|---|
| `os` | the OS drives it for itself | SPI2 (panel, SD **and the SX1262's CS 9, MISO 38**: `firmware/lilygo_t_deck_plus_mainline/native/moy_lcd/modmoy_lcd.c`), I2C0 (keyboard, touch), the trackball, the speaker, the power rail |
| `class` | the OS drives it and exposes a **device API** | the ES7210 mic, the LoRa radio (raw packets and LoRaWAN), the GPS on UART0, battery, brightness |
| `lease` | handed raw to one process at a time | none on the T-Deck, whose every pad is wired; a P4's free pins |
| `never` | present, off limits, with the reason | strapping, PSRAM and USB pins |

The table is the `[hardware]` section of every ESP32 board's `board.toml`
(the browser has none of its own), read and checked by
`tools/board_config.py`'s `hardware`. **Every GPIO of the chip is in exactly
one row** (os, class, lease, never, or the board's `unused` list), against
a per-chip GPIO list (`CHIP_GPIO`) that names the strapping, flash, PSRAM
and USB pads, none of which a `lease` row may hold; every `class` row names a
device API (`DEVICE_APIS`), and where that API has a module the image stages
it; every `os` and `never` row has a `why`. `tests/test_board_toml.py`
holds each board to it. This is the Zero's pin allowlist
generalised (`firmware/web_runner/gpio_link.py`: "the allowlist is the
security model").

**Three access levels.**

1. **Device APIs**: the OS writes the driver; the verb set is the same on
   every board that has the class, `None` where the board cannot answer, and
   the simulator stands in. In demand order: `battery`, `brightness`,
   `alarms`, `mic` (with a recording indicator in kernel chrome), `http`
   (#133), `media` (§3), `gps`, `lora`, `ble`, USB host classes (#83),
   `midi` over any transport, `camera`. **LoRa on the T-Deck is device-API
   only (owner, 2026-11-03)**, its one radio being on the OS's SPI2, and the
   API offers both **raw LoRa packets** (send and receive with RSSI and SNR:
   the mesh case, since a Meshtastic-style mesh is not LoRaWAN) and
   **LoRaWAN** (join, uplink, downlink, the receive windows timed in C). A
   user's own radio driver needs a P4 and an external module on a leased
   SPI. **TLS (owner, 2026-11-03)** verifies for `http` and streams: a CA
   bundle on the P4s; on the S3s, verification when the bundle fits
   `headroom_floor_kib`, otherwise `https` is refused for apps, and
   `VERIFY_NONE` (`native/moy_net/moy_net_port.c`) stays for signed OTA only.
2. **Bus leases**: a named UART, I²C, SPI or GPIO set from the board's
   `lease` rows, with PWM, ADC and edge timestamps on GPIO. Exclusive, for
   the process's life. On a bus the OS also uses there is no lease: a device
   on the P4's touch I²C (the codec) is a `class` row, and the kernel's I²C
   master (`native/moy_kernel/moy_bus.h`) is the only driver. **SPI has no
   arbiter**: the T-Deck's panel feed holds SPI2 for most of every frame
   (#158), so a transaction gate that runs in the flush's band gaps is the
   design for the LoRa `class` row (phase 2), and SPI2 is never leased.
   **A Python lease hands back MicroPython `machine`-compatible objects**
   (`UART`, `I2C`, `SPI`, `Pin`, `PWM`, `ADC`; owner, 2026-11-03), so
   existing MicroPython drivers (micropython-lib, GitHub) work unchanged:
   the lease wraps the object, and the sweep invalidates it, so a driver
   kept past its process raises instead of driving a pin (phase 2).
   **`pins`** (owner, 2026-11-03) is a digital in/out subset of
   `lease:gpio` on every tier, prompted by name (#9's Lego case); UART, SPI,
   PWM and ADC are raw leases, prompted as "full access".
3. **Native code**: `tiers.md` §1.

**Revocation.** A lease is a row of `MOY_KIND_LEASE` (a new handle kind,
beside `MOY_KIND_PROC`) under the process's
OWNER, so the sweep that ends a run's loans ends its leases: on exit, crash,
strike-out and kill. Ending a lease returns its pins to a safe state (input,
no pull, PWM stopped, the UART driver uninstalled). **A VM stop is refused
while a Python process holds a lease**, the rule the WiFi leases already
follow (`native/moy_kernel/moy_kernel.h`); a Lua, wasm or ELF process's lease
lives outside the VM and is untouched by a stop.

**Absence.** A board that lacks a lever reports `None`, never `0`. The
**simulator and browser** read the same board table (the simulator stands in
for a named board), answer device APIs with `None` unless a host stand-in
exists, and offer no leases (refused at install, as on the wrong board). The
browser's exception is pins over the Zero's sync RPC, which is the lease
model with the board at the end of a cable.

**The rows' shape.** `[hardware]` holds `chip_gpio` (the chip's list) and
`unused`; each `[[hardware.resource]]` row a `name`, a `class`, its `pins`
(a table of named pins or a list), a `why` on an `os` or `never` row, the
device `api` (one name or a list) on a `class` row, and the `bus` it rides
where that is another row. The T-Deck's (`firmware/lilygo_t_deck_plus_mainline/board.toml`)
is a whole one; each board's README is the authority on its pins.

## 2. Timing

Cart code runs once per frame (buttons sampled per frame, a key delivered as
a one-frame level from an 8-deep queue, `native/moy_input/moy_input.h`
`MOY_INPUT_KEYQ`; nothing timestamped; `time()` in ms). Three mechanisms, in
order of preference:

1. **Drivers stamp events in C, into rings.** Every source (keys, touch,
   buttons, BLE HID, GPIO edges under a lease, a UART's bytes, the link)
   writes a record (below) carrying its **stamp provenance**: ISR, hardware
   FIFO, or poll. Only ISR and FIFO stamps are better than the frame: the
   T-Deck's keyboard and GT911 are **polled once per frame by design**
   (`.claude/rules/boards.md`, #69), so "no tap lost" holds for ISR and FIFO
   sources and a GPIO lease, not for a polled source. A ring written from an
   ISR with the cache off is small and internal, as the latches are today,
   under the kernel's 4 KiB internal cap; a ring written by a task is PSRAM,
   a take from THE SHARES' RINGS row (`native/moy_play/moy_play.h`), and a
   source whose ring the row cannot hold has none. A source a lease or a
   device API brings is a **ring source** (`native/moy_input/moy_input.h`):
   its ring alone, no latch, opened with the lease or the API and closed with
   it, read by the same readers as the input table's sources. An ISR writes
   its ring through a writer that touches only the ring, its lock and the
   clock, so its drops are counted where its readers find them. A record's
   `src` names its source (`moybyte.source(src)`), its `prov` how good its
   stamp is.
   Ring depth is configuration: the source's peak rate × the worst frame (a
   GC frame). Each ring counts its drops. Carts get `ticks_us()` (wrapping,
   allocation-free) and `events(into)` filling a caller's buffer, since a
   Python frame allocates nothing (`tests/test_frame_alloc.py`); `btn` and
   `key` stay, read from the latches the same driver call writes beside its
   record; a reader has its own cursor per source and a kind mask, and a
   source mask lets focus route sources to it (P1.7).
2. **Hardware engines keep time.** Steppers and servos ride RMT and MCPWM; a
   fixed-rate sample rides ADC DMA; MIDI and serial ride a UART's FIFO;
   LoRaWAN's receive windows are timed by the OS-side MAC in C. User code
   never runs on the deadline.
3. **Low latency is a capability, not a tier policy (owner, 2026-11-03).**
   Every tier renders audio **ahead** into an SPSC ring the OS mixer drains,
   at a buffer depth the app chooses: deeper is safer, shallower is lower
   latency. **Timed lanes**, the OS calling `moy_audio`/`moy_timer` on a
   high-priority thread, are open to every tier that can run off the VM's
   thread: wasm, AOT or interpreted, and ELF. Today's par lanes run on core 0
   *below* every task there and are taken back when late
   (`native/moy_wasm/README.md`), which a deadline cannot ride; the audio
   feeder is on core 1 near the top priority (`native/moy_audio/moy_aud_out.c`).
   A timed lane is therefore a new thing: `moy_audio(buf, n)` fills the
   app's ring (never inside the feeder), and
   `moy_timer(t_us)` runs from an OS-owned `esp_timer` with the tick's
   resolution stated in the binding. Rules: no imports; a declared ring in
   linear memory is how a driver's events reach a lane; a **deadline monitor**
   suspends the lane's task, mutes and strikes (WAMR terminates only at an
   import return, and the task watchdog does not check the idle tasks in
   these images, so a spinning lane would otherwise starve its core
   silently); flash writes stall PSRAM code (`SPI_FLASH_AUTO_SUSPEND` is off),
   so a ring's depth is sized for a store write. A lane that misses its
   deadline is muted and struck: the risk is the app's, so an interpreted
   module is allowed and pays for its speed in misses. The latency target
   is at most the stream pipeline's (`moy_aud_out.c`'s shallow pipeline),
   measured in #158. S3 lanes wait on the core-0 spike (phase 2) and on a
   LANES row, which THE SHARES do not give the S3s.
   **Python and Lua get no lanes**: a lane must run whenever its deadline
   says, and Python's threads share the one VM's GIL and stop for every GC
   (`processes.md` §3), while Lua's state runs on its own task. Lua renders
   ahead from its loop; Python from its loop or a `_thread`, at a ring depth
   greater than a GC pause, which serves streams and noise, its speed coming
   from `@micropython.viper` and `@micropython.native` over arrays.
   Low-latency lanes stay with wasm and ELF.

**The event record (sketch).**

```c
typedef struct {          // version 1; 16 bytes
    uint32_t t_us;        // wrapping kernel clock
    uint16_t src;         // SRC handle: slot and generation
    uint8_t  kind;        // KEY_DOWN, KEY_UP, TEXT, BTN, TOUCH_DOWN/MOVE/UP, EDGE, UART, LINK, MEDIA, NET, BLE, LORA, WINCH
    uint8_t  prov;        // ISR, FIFO, POLL
    uint16_t a;           // KEY: USB HID usage (page 7, low byte) | modifier bits (high byte);
                          // BTN bit, EDGE pin, UART byte count, MEDIA consumer-page usage
    uint16_t id;          // touch id, UART ring offset
    uint32_t b;           // x<<16|y, the level, the TEXT codepoint
} moy_event_t;
```

A KEY carries the **USB HID usage code** (owner, 2026-11-03), so a USB and a
BLE keyboard map straight through and the T-Deck's matrix and the browser
translate to it; a **keyboard layout is data** (a file under `system/`
mapping usage and modifiers to a codepoint) that turns KEY into TEXT.

One ring per source; per-reader cursors. A full ring overwrites its oldest
record; a reader that missed records reads one LOST record (`b` = how many)
where the gap was, and the source counts only the drops a reader missed.
A KEY's `b` is the console key event; TEXT is its own record.

## 3. The media service

An **OS service** in C (`+native/moy_media/`): fetches a stream or opens a
file, decodes it (MP3 first, minimp3, on a PSRAM task stack that never runs a
flash operation), and feeds a **background stream** into the mix
(`native/moy_audio/moy_aud.h`) **ducked** under the focused session. Decode
cost is the stream's (internet radio is 44.1 kHz stereo), not the output's.
While it plays the feeder is no longer lazy, its I²S DMA ring is permanent
internal SRAM, and the stream holds the WiFi lease, whose internal cost never
comes back (#158). **The output format (owner, 2026-11-03)**: 44.1 kHz
stereo on the P4s, 22.05 kHz mono on the S3s (22,050 Hz mono everywhere
today, `native/moy_audio/moy_audio_snd.h`), the synth resampled once in the
mix; so a stream plays at 44.1 kHz stereo on the P4s. An S3 would decode
below the flush priority to 22.05 kHz mono, but THE SHARES give neither S3
a MEDIA row (`native/moy_play/moy_play.h`: the 8 MiB is spent, the budget in
#246), so the service's fit check refuses it there until a row is given; the
Guition S3 has no audio at all. Verbs: `media.play(src)`,
`pause`, `resume`, `stop`, `volume`, `now`; `play` takes a file, an HTTP
stream (with **ICY metadata**, the station's now-playing text) or an
**M3U/PLS playlist**, and **media keys** (play, pause, next, previous) on a
keyboard arrive as events of kind `MEDIA` (§2) the service answers when
no focused app takes them (owner, 2026-11-03). **Permissions:** `media` for a
file or the console's own library; `media:url` and `http` for a URL (a
`play(url)` without `http` would be a fetch of any URL by an app that never
asked for the network, child mode included). **A stream lives as long as
its process (owner, 2026-11-03)**: closing the app ends it, minimising
keeps it (`processes.md` §1), and a visible stop control in kernel chrome
stops it from anywhere. A Python app making its own sound renders ahead from
a thread or its loop (`processes.md` §3), at a depth greater than a GC pause.

## 4. Verbs (sketch)

Device APIs (every tier; `None` where the board cannot answer):

```
moybyte.events(into, max) -> n         records since this reader's cursor, in order; §2
moybyte.battery() -> (percent, charging) | None
moybyte.brightness(level=None) -> level   needs "brightness"
moybyte.mic.read(buf) -> n             PCM16 mono at the board's rate; needs "mic"
moybyte.gps() -> (lat, lon, alt, t_us) | None
moybyte.lora.send(bytes) / recv(into) -> (n, rssi, snr) | None   raw packets (§1)
moybyte.lora.join() / uplink(port, bytes) / downlink(into) -> n | None   LoRaWAN over the OS MAC
moybyte.media.play(src) / pause() / resume() / stop() / volume(v) / now() -> dict   §3
```

Leases (Python gets them as `machine` objects, §1; wasm and ELF through
these verbs; `pins` is the digital subset every tier may hold):

```
lease.open(name, cfg) -> h            refused if held; cfg is a versioned struct
lease.close(h)
uart.write(h, bytes); uart.read(h, into) -> n
gpio.mode(h, pin, mode); gpio.set(h, pin, v); gpio.get(h, pin) -> v
gpio.edges(h, pin, on)                events of kind EDGE, stamped in the ISR
pwm.start(h, pin, hz, duty); adc.sample(h, pin, hz, into)
```

In C: `moy_lease_take(owner, res, cfg, &h)`, `moy_lease_drop(h)`,
`moy_lease_sweep(owner)`.

## 5. How it maps onto what exists

| design piece | today | what changes |
|---|---|---|
| hardware declaration | `[hardware]` in every ESP32 `board.toml`, every pin classified and checked (`tools/board_config.py`, `tests/test_board_toml.py`); the P4 READMEs' free pins are their `lease` rows (P2.1) | the lease table and sweep over those rows (P2.3) |
| the link ring | `native/moy_net/moy_link.h`, counting its drops | the link's records a ring source's (LINK) |
| GPIO edges | `native/moy_input/moy_edge.h`: a free pin's edges as an ISR ring source, the stand-in a lease adopts (P2.2) | `lease:gpio` opens it, the lease table checking the pin and the sweep closing it (P2.3) |
| brightness | `runtime/system/settings_layer.py`: "stored and applied by nothing yet" | applied at boot and on change (P1.2); the backlight is a `class` row naming `brightness` on every console (P2.1), whose API is P2.3's |
