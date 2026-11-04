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
holds each board to it. The table reaches the C as defines
(`board_config.hw_defines`, staged into the board's build): `MOY_API_<API>`
for each API a class row names and `MOY_HW_<ROW>_<PIN>` for each named pin,
which the device APIs' drivers are built from. This is the Zero's pin allowlist
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
   (#158), so the LoRa radio's transactions run in the flush's band gaps
   (`moy_flush_gate_take`/`_give`, `native/moy_flush/moy_flush.h`: the
   feeder queues no band while one waits, lets the queued ones finish and
   steps aside), and SPI2 is never leased.
   **A Python lease hands back MicroPython `machine`-compatible objects**
   (`UART`, `I2C`, `SPI`, `Pin`, `PWM`, `ADC`; owner, 2026-11-03), so
   existing MicroPython drivers (micropython-lib, GitHub) work unchanged:
   the lease wraps the object, and the sweep invalidates it, so a driver
   kept past its process raises instead of driving a pin (phase 2).
   **`pins`** (owner, 2026-11-03) is a digital in/out subset of
   `lease:gpio` on every tier, prompted by name (#9's Lego case); UART, SPI,
   PWM and ADC are raw leases, prompted as "full access".
3. **Native code**: `tiers.md` §1.

**The lease table** is `native/moy_kernel/moy_lease.h` (P2.3): a lease is a
row of `MOY_KIND_LEASE` owned by the process's pid, holding a unit (a UART,
an SPI host) or a set of pins, each at most once. A board offers its `lease`
rows as one spec its build defines (`MOY_LEASE_ROWS`, from
`tools/board_config.py`'s `lease_spec`); a UART or SPI row rides the gpio row
(`bus = "gpio"`), its named pins the defaults a take may replace with others
of that row, since the P4's GPIO matrix routes a unit to any pin. A Python
process takes one with `moybyte.lease(name, **cfg)` (`runtime/system/leases.py`):
`"gpio<n>"` a `machine.Pin`, `"uart<n>"` a `machine.UART`, `"spi<n>"` a
`machine.SPI`, MicroPython's own object behind a wrapper that checks the
lease on every call. A leased Pin's `irq` opens its edges (`moy_edge.h`) as a
ring source read by a wake of the process alone (`moy_input_reader_only`).
`moybyte.pins()` lists each offered row and who holds it, and the `pins`
permission's `pin_write`/`pin_read` take their pins through the same table
(`leases.LocalPins`, the consoles' `ws.gpio`).

**Revocation.** The sweep that ends a process ends its leases: on exit, crash,
strike-out and kill (`moybyte.end`, which the Player's run end and the
scheduler's exit both reach). The Python sweep deinits each object, then drops
its lease, which closes its edge sources and returns its pins to a safe state
(input, no pull, interrupt off, no peripheral driving it, the UART driver
uninstalled); `moy_lease_pin_state` reads a pin back from the GPIO matrix. An
object kept past its process raises `OSError(EBADF)`. **A VM stop is refused
while a Python process holds a lease** (`MOY_LEASE_VM`, read by
`native/moy_play/moy_play_stop.c` beside the WiFi leases); a Lua, wasm or ELF
process's lease will live outside the VM, untouched by a stop.

**The device APIs as built** are `runtime/system/devices.py`'s faces,
`moybyte.http`, `.mic`, `.lora`, `.ble`, `.usb` and `.midi` (`None` where
the console lacks the class), each over a C driver, each checking its
permission row against the caller's grants and swept at the caller's end. The
mic, the radio, BLE, USB and MIDI have one holder at a time (`EBUSY` for
another); `http` requests are many. Their events are NET, LORA, BLE, USB and
MIDI records on each driver's ring source, which a face's `on(fn)` or `on_done` wakes its process
on.

- **`http`** (`native/moy_net/moy_fetch.h`): a GET a worker task serves
  through the kernel's client (`moy_ota.h`'s worker slot, which the VM's
  stop leaves alone), its body kept up to a cap; `https` and a redirect to
  it are refused for apps until TLS (P3.4), and a request holds the WiFi
  lease's `http` tag while it runs.
- **`mic`** (`native/moy_audio/moy_mic.h`): PCM16 mono at the speaker's
  rate from the ES7210 on the kernel's I²C bus, on the T-Deck's own I2S1, on
  a P4 the speaker's I2S0 opened full duplex, the ES7210's four inputs in
  TDM and the two slots' mean (`moy_mic.c` says why).
  `moybyte.mic.holder()` names the process recording. While one holds it
  the **recording indicator** stands at the glass's top right, a dot in the
  theme's `danger` (native/moy_ui's `moy_ui_rec`): the shell's `rec`
  overlay, drawn over everything but the cursor and turned off by nothing
  (`devices.MIC`, read by the WM's overlay signature), and the kernel's
  copy over a run it drives (`moy_chrome_overlay_draw`, which reads
  `moy_mic_owner`).
- **`lora`** (`native/moy_net/moy_lora.h`, the T-Deck): the SX1262 inside
  the band-gap gate, raw packets with RSSI and SNR, and LoRaWAN 1.0.x class
  A OTAA on EU868 over the OS's MAC (`moy_lorawan.h`, `moy_aes.h`), its
  receive windows timed by the driver's task. MAC commands are read past,
  not answered, and the DevNonce is random (1.0.2).
- **`ble`** (`native/moy_input/moy_gatt.h`): a GATT central (scan, connect,
  find by UUID, read, write, subscribe) and a non-connectable beacon, on the
  NimBLE host the keyboard machine owns and only while it runs (keyboard
  input on in Settings). The two share NimBLE's one GAP master procedure
  (`native/moy_input/moy_scan.h`): an app's scan joins the keyboard's
  discovery, the union of the two scans' kinds on the air and every
  advertisement to both, and a connection attempt pauses it, the other
  side's attempt answering `-EBUSY` until it ends.
  An app's own GATT server is not offered: NimBLE takes its services before
  the host starts.

- **`usb`** (`native/p4/moy_usb/moy_usb.h`, #83, the P4 boards: their
  `usb_host` row): the USB Host Library on the high-speed OTG port, which
  MicroPython's TinyUSB device no longer takes (`MICROPY_HW_ENABLE_USBDEV`
  0), started at the first open with its two tasks. Each device enumerates
  into a table (address, class, VID, PID, product name); the OS drives the
  first HID interface (a boot keyboard or mouse put in its boot protocol;
  each report kept and announced), CDC-ACM serial (line coding, DTR and
  RTS, bytes both ways) and USB-MIDI (its event packets, which `midi`
  decodes), and lists any other device by its class. The console's own
  keyboard and mouse over USB (#83's routing into the input table) are not
  built: a report reaches only the API's holder. The S3s have no OTG host
  port (their USB pads are the USB-Serial/JTAG) and no `usb`.
- **`midi`** (`native/moy_input/moy_midi.h`): ports on three transports,
  each message a MIDI record (status, two data bytes, the port). A UART
  port is a leased UART row (the P4s' `uart1`; `lease:uart1` beside
  `midi`), its bytes read off the FIFO by the driver's own task and parsed
  with running status, realtime bytes through whole and system exclusive
  read past. A USB port is a USB-MIDI device `usb` enumerated (its event
  packets reach the driver from `moy_usb`'s IN completions). A BLE port is
  a BLE-MIDI peripheral the face drives through `ble`'s central (connect,
  find the MIDI I/O characteristic, subscribe; each notification's header
  and timestamps read past). What an app sends is MIDI bytes, packed for the
  transport. The `midi` face's sweep runs ahead of the leases', so a UART's
  reader stops before its lease's driver goes.

The host's stand-ins are `runtime/host/mic_sim.py`, `lora_sim.py` (with a
LoRaWAN network server written apart from the C), `gatt_sim.py`,
`usb_sim.py` and `midi_sim.py` (a UART port's wire); `http` runs for real.

**Absence.** A board that lacks a lever reports `None`, never `0`. The
**simulator and browser** read the same board table (the simulator stands in
for a named board), answer device APIs with `None` unless a host stand-in
exists, and offer no leases (refused at install, as on the wrong board); a
host test offers a named board's rows over the stand-in
(`leases.stand_in`, `runtime/host/lease_sim.py`), whose pins live in the C
table's own stand-in. The
browser's exception is pins over the Zero's sync RPC, which is the lease
model with the board at the end of a cable.

**The rows' shape.** `[hardware]` holds `chip_gpio` (the chip's list) and
`unused`; each `[[hardware.resource]]` row a `name`, a `class`, its `pins`
(a table of named pins or a list), a `why` on an `os` or `never` row, the
device `api` (one name or a list) on a `class` row, and the `bus` it rides
(a `lease` row named `uart<n>` or `spi<n>` rides the `gpio` row and names its
pins `tx, rx` or `sck, mosi, miso`, pins of that row)
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
    uint8_t  kind;        // KEY_DOWN, KEY_UP, TEXT, BTN, TOUCH_DOWN/MOVE/UP, EDGE, UART, LINK, MEDIA, NET, BLE, LORA, WINCH, USB, MIDI
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
moybyte.http.get(url, cap, on_done) -> Request: state() / body() / readinto(buf) / close()
moybyte.lora.config(...) / listen() / send(bytes) / recv(into) -> (n, rssi, snr) | None   raw packets (§1)
moybyte.lora.join(dev_eui, join_eui, app_key) / uplink(port, bytes) / downlink(into) -> (n, port) | None
moybyte.ble.scan() / connect(addr) / find(uuid) / read(h) / write(h, bytes) / subscribe(h) / advertise(name, data)
moybyte.media.play(src) / pause() / resume() / stop() / volume(v) / now() -> dict   §3
```

Leases (Python gets them as `machine` objects, §1:
`moybyte.lease(name, **cfg)` and `moybyte.pins()`; wasm and ELF through
these verbs, which arrive with those tiers; `pins` is the digital subset every
tier may hold):

```
lease.open(name, cfg) -> h            refused if held; cfg is a versioned struct
lease.close(h)
uart.write(h, bytes); uart.read(h, into) -> n
gpio.mode(h, pin, mode); gpio.set(h, pin, v); gpio.get(h, pin) -> v
gpio.edges(h, pin, on)                events of kind EDGE, stamped in the ISR
pwm.start(h, pin, hz, duty); adc.sample(h, pin, hz, into)
```

In C (`native/moy_kernel/moy_lease.h`): `moy_lease_take(owner, cls, unit,
pins, flags, &h)`, `moy_lease_drop(h)`, `moy_lease_sweep(owner)`,
`moy_lease_edge(h, pin, depth, loop, &src)`; the module `moy_lease` binds them.

## 5. How it maps onto what exists

| design piece | today | what changes |
|---|---|---|
| hardware declaration | `[hardware]` in every ESP32 `board.toml`, every pin classified and checked (`tools/board_config.py`, `tests/test_board_toml.py`); the P4 READMEs' free pins are their `lease` rows (P2.1), with `uart1` and `spi2` riding them, offered as `MOY_LEASE_ROWS` | |
| the lease table | `native/moy_kernel/moy_lease.h` and `runtime/system/leases.py`: takes, the sweep, safe pins, `machine` objects, `pins` (P2.3; `tests/test_leases.py`) | I²C, PWM and ADC leases; the wasm and ELF verbs |
| the link ring | `native/moy_net/moy_link.h`, counting its drops | the link's records a ring source's (LINK) |
| GPIO edges | `native/moy_input/moy_edge.h`: a leased pin's edges as an ISR ring source, opened through `moy_lease_edge` and closed by the drop (P2.3) | |
| brightness | `runtime/system/settings_layer.py`: "stored and applied by nothing yet" | applied at boot and on change (P1.2); the backlight is a `class` row naming `brightness` on every console (P2.1), whose API is P2.3's |
