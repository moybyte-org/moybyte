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
   As built (P3.4), every console carries ESP-IDF's common bundle
   (`esp_crt_bundle`, in each `sdkconfig.board`), the S3s' included: every
   https request but the updater's (`MOY_HTTPC_SIGNED`, `moy_ota.h`) checks
   the chain to a root of it, the host's name and the dates, `http`, media
   streams, Get Carts and `pkg` alike. The dates are read against a floor:
   the newest of the image's build day and the newest start date of a leaf
   certificate the board has seen verify, kept in NVS (written when it rises
   by a day). NTP's answer (below) is trusted from the floor to 400 days past
   it (`MOY_CLOCK_LEAP_MAX`, `moy_net.h`), and the dates then read the clock;
   an answer outside that window, or none yet (a board's clock counts from
   power-on), reads as the floor, and a not-yet-valid certificate is let
   pass. So a certificate that expired before the floor is refused whatever
   NTP says, and a forged answer can move the dates neither back past the
   floor nor more than 400 days ahead. A handshake in the first 15 s of an
   unsynced client's asking waits up to 3 s for its answer first
   (`moy_net_port.c`). **Network time (owner, 2026-11-06):**
   whenever the station holds an address, for whichever lease holder raised
   it, the WiFi driver runs ESP-IDF's SNTP client (`esp_netif_sntp`,
   `native/moy_net/moy_wifi.c`) against `pool.ntp.org`, or the
   `ntp_server` key of `system/settings`, and stops it with the radio; it
   never raises the radio itself. The clock's state is `moy_clock_state`
   (`moy_net.h`: unsynced, synced, or asking), `moy_net.clock_state()` in
   Python and `moybyte.clock_synced()` for an app. The last answer's mark
   lives in RTC memory beside the time the chip's RTC keeps, so the clock
   stays synced across a soft reset and a software reset, and reads
   unsynced after a power-off until the next answer. The time zone is a
   POSIX TZ string in `system/settings`' `tz` (UTC when absent), picked in
   Settings -> Time zone from a list of places (`runtime/system/wallclock.py`'s
   `ZONES`, each with its summer rules) and converted in Python on every
   tier; the bar's clock shows local time once synced and `--:--` before,
   where a board's clock would read as a time of day. The host, the unix
   port and the browser read their machine's clock, synced. A build
   without the bundle (the host, the Zero) refuses those requests with
   `EPROTONOSUPPORT`, and a certificate that does not verify fails with
   `EKEYREJECTED`. The bundle's flash cost and
   the handshake's memory are #246's.
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
lease on every call; `"pwm<n>"` and `"adc<n>"` a `machine.PWM` and a
`machine.ADC` on a pin, and `"i2c"` a `machine.SoftI2C` on two pins of the
gpio row (CPU-clocked, so it holds pins and no unit; a hardware I²C unit
would be a class of the table like UART's), each needing `lease:gpio`. A
leased Pin's `irq` opens its edges (`moy_edge.h`) as a ring source read by a
wake of the process alone (`moy_input_reader_only`).
`moybyte.pins()` lists each offered row and who holds it, and the `pins`
permission's `pin_write`/`pin_read` take their pins through the same table
(`leases.LocalPins`, the consoles' `ws.gpio`), as a compiled program's
`pins` handle does (§4).

**The compiled tiers' leases** (a compiled app, a WASI program, a native
module) are the same objects held by HANDLE: the `moybyte.dev_*` rows
(`native/os_calls.json`, ELF ordinals 103 to 109), served by the door's
Python half as the calling process (`runtime/system/dev_handles.py`, which
holds every name, key and answer, over `leases.LEASES.take` with the
process's pid). `dev_open(name, cfg)` answers a handle, `cfg` words
`key=value`; `dev_read`, `dev_write`, `dev_xfer`, `dev_set` and `dev_get`
act on it and `dev_close` gives it back. One table holds every tier's, so
one holder a pin or a unit whatever the tiers: a Python object's resource
is BUSY to a handle and the reverse. A ring's records are read through the
handle on every tier (a pin's edges after `dev_set(h, "edges=...")`, 8
bytes each from `dev_read`; a packet; the microphone's samples), never
posted on the process's event queue: a compiled program polls its handles
where it polls its messages, a device's rate and binary records stay off a
queue built for the console's text messages, and the reader picks its own
buffer. Python keeps its callbacks.

**Revocation.** The sweep that ends a process ends its leases: on exit, crash,
strike-out and kill (`moybyte.end`, which the Player's run end and the
scheduler's exit both reach). The Python sweep deinits each object, then drops
its lease, which closes its edge sources and returns its pins to a safe state
(input, no pull, interrupt off, no peripheral driving it, the UART driver
uninstalled); `moy_lease_pin_state` reads a pin back from the GPIO matrix. An
object kept past its process raises `OSError(EBADF)`; a compiled process's
handles close at its end (`dev_handles.sweep`, ahead of the leases' in
`moybyte.ENDS`), a handle closed answers ABSENT. **A VM stop is refused
while a lease is held** (`MOY_LEASE_VM`, read by
`native/moy_play/moy_play_stop.c` beside the radio's row): every tier's
lease is a VM object, a compiled program's too, and its rows are served in
Python, so an app that imports them keeps the VM (moy_play's verdict). The
boards that offer leases, the P4s, keep the VM for every run.

**The device APIs as built** are `runtime/system/devices.py`'s faces,
`moybyte.http`, `.mic`, `.lora`, `.ble`, `.usb`, `.midi` and `.camera`
(`None` where the console lacks the class), each over a C driver, each checking its
permission row against the caller's grants and swept at the caller's end. The
mic, the radio, BLE, USB and MIDI have one holder at a time (`EBUSY` for
another); `http` requests are many. The microphone's, the LoRa radio's, the
BLE central's and the media service's holder is a lease of the device's class
(`moy_lease.h`'s DEVICES), taken by the driver's own open for whoever opens it
(a Python app's face, a process of another tier, the shell as pid 1) and
swept with its holder's other leases, so the lease listing (`lease`) names
it; the shell's `mic`, `lora` and `ble scan` are served in C over their drivers and
those leases (`native/moy_sh/moy_sh_board.c`). The WiFi station's holders
are rows of the same table, the radio's (`MOY_LEASE_WIFI`, `moy_lease.h`'s
THE RADIO), each a mask of tags (`web`, `update`, `cart`, `media`, ...): the
console's own in one row the shell owns, and a process's (`cart`, `http`,
`media`, `native`) in one row the process owns, so its end lets exactly its
holds go and `state`'s `wifi_held` names it (`http:12`). The station is held
while any row is; `Workstation.wifi_hold` and `wifi_release` take and let go,
and a VM stop keeps the rows; the station's address coming and going is a NET record on the
table's ring source `radio`, beside BLE's host (`between_apps.md` §4). The
faces' events are NET, LORA, BLE, USB and
MIDI records on each driver's ring source, which a face's `on(fn)` or `on_done` wakes its process
on. A compiled program holds `http`, `lora`, `mic`, `ble`, `usb`, `midi`, `camera` and `media` by handle
through the same rows as its leases (above): an `http` handle a GET whose
body it reads in chunks (`cfg` `url=... cap=N`, or `window=N` for a body of
any length), a `lora` handle the radio's raw packets and LoRaWAN's join,
uplinks and downlinks, a `mic` handle PCM16 samples, a `ble` handle the
face's verbs and its events as records, a `usb` handle the USB host's
devices, their reports and serial lines, as records, a `midi` handle one
port's messages, a `camera` handle its latest frame, a `media`
handle the media service's stream (§3, §4), playing a file only from its
cart's folder and the one its `files` permission names: any other is DENIED,
there or not (`runtime/system/dev_handles.py`).

- **`http`** (`native/moy_net/moy_fetch.h`): a GET a worker task serves
  through the kernel's client (`moy_ota.h`'s worker slot, which the VM's
  stop leaves alone), its body kept up to a cap; `https` verifies (above),
  `moybyte.http.tls()` says whether it can here, and a request holds the WiFi
  lease's `http` tag under its process's pid while it runs. The `http` permission gates
  `moybyte.http` and nothing else: a Python app's `socket` and `requests`
  reach the network with or without it (Python is trusted, `processes.md`
  §2).
- **`mic`** (`native/moy_audio/moy_mic.h`): PCM16 mono at the speaker's
  rate (44,100 Hz on a P4, 22,050 on the T-Deck: a reader takes `rate()`)
  from the ES7210 on the kernel's I²C bus, on the T-Deck's own I2S1, on
  a P4 the speaker's I2S0 opened full duplex, the ES7210's four inputs in
  TDM and the two slots' mean (`moy_mic.c` says why).
  `moybyte.mic.holder()` names the process recording. While one holds it
  the **recording indicator** stands at the glass's top right, a dot in the
  theme's `danger` (native/moy_ui's `moy_ui_rec`): the shell's `rec`
  overlay, drawn over everything but the cursor and turned off by nothing
  (`devices.MIC`, read by the WM's overlay signature), and the kernel's
  copy over a run it drives (`moy_chrome_overlay_draw`, which reads
  `moy_mic_owner`).
- **`lora`** (`native/moy_net/moy_lora.h`, the T-Deck and the Zero): the
  SX1262, raw packets with RSSI and SNR, and LoRaWAN 1.0.x class A OTAA on
  EU868 over the OS's MAC (`moy_lorawan.h`, `moy_aes.h`), its receive
  windows timed by the driver's task. MAC commands are read past, not
  answered, and the DevNonce is random (1.0.2). The T-Deck's radio rides the
  panel's SPI2 inside the band-gap gate; the Zero's Wio-SX1262 names its own
  bus in its `lora` row, so the driver brings SPI2 up itself, with no gate,
  and switches the module's receive path (`rxen`). The Zero runs no apps, so
  it carries the driver (the module `moy_lora`, reached from its REPL) and
  not the face. The two radios round-trip raw packets with each other.
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
- **`camera`** (`runtime/system/dev_camera.py`): `size`, `capture` (the
  latest frame, RGB565 in the glass's byte order) and `holder`, one holder.
  No console has a camera: the P4s' MIPI-CSI sockets are empty (their
  `camera` rows name the socket), so no image carries a driver and
  `moybyte.camera` is `None` on every board. The driver's module,
  `moy_camera`, is the host's stand-in alone (`runtime/host/moy_camera.py`).

The host's stand-ins are `runtime/host/mic_sim.py`, `lora_sim.py` (with a
LoRaWAN network server written apart from the C), `gatt_sim.py`,
`usb_sim.py`, `midi_sim.py` (a UART port's wire) and `camera_sim.py`;
`http` runs for real.

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
   feeder is near the top priority on its board's `MOY_AUDIO_CORE`, core 1 on
   the T-Deck and core 0 on the P4s (`native/moy_audio/moy_aud_out.c`).
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
   measured in #158. S3 lanes wait on the core-0 spike (phase 2); THE
   SHARES give each S3 a LANES row of 64 KiB.
   **Python and Lua get no lanes**: a lane must run whenever its deadline
   says, and Python's threads share the one VM's GIL and stop for every GC
   (`processes.md` §3), while Lua's state runs on its own task. Lua renders
   ahead from its loop; Python from its loop or a `_thread`, at a ring depth
   greater than a GC pause, which serves streams and noise, its speed coming
   from `@micropython.viper` and `@micropython.native` over arrays.
   Low-latency lanes stay with wasm and ELF.

   **As built (P2.5).** The ring is the mix's one render-ahead stream,
   libmoy's `moy_stream` under the mix's lock (one writer at a time, the
   feeder its reader), 22,050 Hz mono (moy-spec's) added to the mix at the
   output's rate, `MOY_AUDIO_SND_MIN`..`_MAX` frames
   deep (`native/moy_audio/moy_audio_snd.h`): a depth up to moy-spec's 2,048
   uses the ring reserved at boot, a deeper one is PSRAM taken from THE
   SHARES' RINGS row at the open and given back at the close. A compiled
   cart's `snd` writes it, as deep as its manifest's `"moybyte":
   {"audio_ring": n}` asks (the default and an out-of-range value being
   2,048); a Python process opens it with `moybyte.snd_open(frames)` and
   writes it with `moybyte.snd(buf)`, from its loop or a `_thread`, until
   `snd_close` or its end (`runtime/system/moybyte.py`). One holder at a
   time. Lua has no writer: its verb would be a row of moy-spec's table.
   AUDIORATE's `snd=` field is the ring's queued/played/starved/room/depth.
   The timed lanes are built on the P4s for wasm, AOT or interpreted
   (`MOY_WASM_TIMED_LANES`; `native/moy_wasm/moy_wasm_session.h` is the
   contract and `moy_wasm_tlane.c` the code): the OS finds a lane by its
   export, `moy_audio(buf, n)` or `moy_timer(t_us)`, with
   `moy_lane_stack(lane)` naming the lane's stack in linear memory; the
   manifest's `"lanes"` is not read. Each lane is a pthread on the core the
   session is not on, above the board's own tasks there and below the
   esp_timer task, running a sibling instance of the cart; its stack is a
   take from the LANES row. The monitor is a 1 ms esp_timer that strikes a
   call past `MOY_WASM_TLANE_DEADLINE_US` (suspends the lane's task, mutes
   the ring); a trap, an import among them, strikes too. `moy_wasm.tlanes()`
   reads what each lane did. ELF's lanes arrive with P5.4; the S3s have
   none.

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

An **OS service** in C (`native/moy_media/moy_media.h`, the module
`moy_media`; its face `runtime/system/dev_media.py`, `moybyte.media`, and a
compiled program's `media` handle, §4): one background stream for one
process, from an HTTP stream or a file. MP3 is
decoded by minimp3 (vendored, `native/moy_media/UPSTREAM.md`) and resampled
once to the output's rate and channels (below: on a P4 a 44.1 kHz stereo
stream is copied as it is, its channels kept; on the T-Deck it is mixed down
to mono), and the mix pulls it as its
**background stream** (`moy_aud_bg`, `native/moy_audio/moy_aud.h`), at a
quarter of its level while the focused session sounds (**ducked**). On a
board a task on core 0 below the flush priority (`MOY_MEDIA_PRIO`) reads the
connection and decodes, on a 24 KiB PSRAM stack (a frame's decode takes 16.7
KB) that never runs a flash operation. **A file is the C's from end to end**
(owner, #246 review 2026-11-07: one path for the Music app, the shell's
`media` and the browser): `moy_media_play` builds the list (a playlist file
read through the store's volume seam), and a job of the console's frame
(`moy_loop_job`, on the VM's task) opens the current file, reads it into the
stream buffer a few KiB at a time with the card's bus gate around each read,
sizes it and seeks it, so a store op never runs on the PSRAM stack and no
Python runs while a file plays. The shell's `media play SRC` names a file
by the session's folder: the C shell makes it the store's path
(`moy_sh_resolve`) before it crosses the door, a URL crossing as it is. The
host has no task; the same job runs one
pass of its loop. The job and a teardown (close, the lease's end, the VM's
end) take turns at the service's life gate, so a teardown from any task
frees nothing the job is reading, and the job, finding no service, is dropped
by the loop on its own task. Decode cost is the stream's (internet radio is 44.1 kHz
stereo), not the output's. On the T-Deck that cost reaches a heavy cart on
core 1 through the cache and the memory bus the cores share, and the decode
itself is the cost: its state in internal SRAM bought back a fifth of it for
about 30 KiB the S3 cannot spare, and decoding in bursts or building the
decoder for size bought nothing (A/B'd 2026-11-04; the numbers are #246's).
On the Waveshare P4 a stream costs a heavy cart a few frames a second too.
The P4s' feeder runs on core 0, off the VM's core: on core 1 its stereo
stage cost a heavy cart frames whether a stream played or not (A/B'd
2026-11-05). While it plays the feeder
is no longer lazy (the open starts the output), its I²S DMA ring is permanent
internal SRAM (6 KiB on the T-Deck, 24 KiB on a P4: 12 × 512 stereo
frames), and a
stream whose list holds a URL holds the WiFi lease under `media` and its
process's pid, whose
internal cost never comes back (#158).

**Its memory** is THE SHARES' MEDIA row (`native/moy_play/moy_play.h`): the
open takes `moy_media.share()` from it (156,680 bytes on the T-Deck,
189,448 on a P4: the service's state, its 32 KiB stream buffer, its 16 KiB
decoder window, the decoder, a decoded ring of 16,384 frames, 32 KiB mono or
64 KiB stereo, the list, and the open file's path and tag, one PSRAM
block; the task's stack; the
URL client's buffers) and the close gives it back. Where the row is absent
the open is refused (ENOSPC) and `moybyte.media` is `None`: the T-Deck has a
MEDIA row of 192 KiB, the Guition S3 none (owner, 2026-11-04: the one
running thing tier for media; it has no speaker, and its image denies the
module).

**The output format (owner, 2026-11-03; two rates, 2026-11-04)**: 44.1 kHz
stereo on the P4s, 22.05 kHz mono on the S3s, the host and the browser's
page (`web_boot._AUDIO_RATE_HZ`). Every
board's sessions (the synth and the sample voices) mix at 22,050 Hz mono, so
a cart sounds the same everywhere; on a P4 the feeder upsamples that mix
once, 2× (`moy_aud_render_st` and `moy_aud_up2`, `native/moy_audio/moy_aud.h`:
a half-band FIR flat to 10 kHz whose images at or above 12.05 kHz are 74 dB
down, `tests/test_audio_upsample.py`), then adds the render-ahead ring and
the media's stereo stream at 44.1 kHz (`MOY_AUDIO_STEREO` in the board's
`mpconfigboard.h`). The ES8311 on both P4s is a mono DAC that reads one I²S
slot, so the feeder hands it the two channels' mean (`MOY_AUDIO_DAC_MONO`):
the stereo stops at the codec, and an output that plays two slots takes it
whole. The microphone, on the speaker's clocks, records at 44.1 kHz there.

**Verbs:** `play(src)`, `pause`, `resume`, `stop`, `next`, `prev`, `seek`,
`volume`, `now`, `probe`. `play` takes a file (an MP3), an `http://` stream (asked for its **ICY
metadata**: `icy-name` is the station, `StreamTitle` the now-playing text) or
an **M3U/PLS playlist** (a file, or a URL known by its content type or path),
whose entries play in turn: an entry that ends or fails moves to the next,
and a live stream (no Content-Length) that drops after playing is opened
again twice first. `seek(seconds)` starts the current file again at that
place (its byte from the bytes its frames have taken against the frames they
made), its decoded tail dropped; a stream has no place. A file's ID3v2 tag
gives `now()` its title and artist, and `probe(path)` reads a song's length
and tag without playing it (Music's folder list). An `https` stream verifies
as `http` does, and `moybyte.media.tls()` says whether one plays here.
**Media keys** (play, pause, next, previous) on a keyboard are to arrive as
events of kind `MEDIA` (§2) the service answers when no focused app takes
them (owner, 2026-11-03); no keyboard driver posts a MEDIA record yet.
**Permissions:** `media` for a file or the console's own library;
`media:url` and `http` for a URL (a `play(url)` without `http` would be a
fetch of any URL by an app that never asked for the network). **A stream lives as long as its process (owner, 2026-11-03)**:
closing the app ends it (its end sweeps it), minimising keeps it
(`processes.md` §1); a control standing on the glass in kernel chrome, as the
recording indicator does, is not built. A Python app making
its own sound renders ahead from a thread or its loop (`processes.md` §3), at
a depth greater than a GC pause.

**In the browser (#251)** the same C decodes, compiled into the wasm
(`native/moy_media/micropython.mk`) and pulled by the page's mix: no task
(the frame's job runs the loop, as on the host) and no HTTP client
(`MOY_MEDIA_CLIENT` 0). A page keeps no song in its VFS: a static host's are
the browser's OPFS (`files/Music`, where a dropped MP3 lands), a board-served
page's are the board's music folder, read through its sealed `GET /media` a
range a request (`native/moy_net/moy_webhost.c`, THE MEDIA ROUTE), since the
sync wire carries no binaries. So a URL's bytes, and a song's, are the
page's: the C arms the entry for the face, which feeds it from the page's
reader (`ws.media_net`, `firmware/web_runner/media_link.py`) from the byte
the C asks for (a seek's), with no ICY metadata (a page may not ask for it);
a file that is in the VFS the frame's job reads, as on a board. A station plays
only if it sends CORS headers; one that does not fails with EACCES, which
Music shows as "Blocks browsers".

## 4. Verbs (sketch)

Device APIs (every tier; `None` where the board cannot answer):

```
moybyte.events(into, max) -> n         records since this reader's cursor, in order; §2
moybyte.battery() -> (percent, charging) | None
moybyte.brightness(level=None) -> level   needs "brightness"
moybyte.mic.read(buf) -> n             PCM16 mono at the board's rate; needs "mic"
moybyte.gps() -> (lat, lon, alt, t_us) | None
moybyte.http.get(url, cap, on_done) -> Request: state() / body() / readinto(buf) / close()
moybyte.http.stream(url, window) -> Stream: state() / available() / ended() / readinto(buf) / close()   a body of any length through a window
moybyte.lora.config(...) / listen() / send(bytes) / recv(into) -> (n, rssi, snr) | None   raw packets (§1)
moybyte.lora.join(dev_eui, join_eui, app_key) / uplink(port, bytes) / downlink(into) -> (n, port) | None / busy()
moybyte.ble.scan() / connect(addr) / find(uuid) / read(h) / write(h, bytes) / subscribe(h) / advertise(name, data)
moybyte.media.play(src) / pause() / resume() / stop() / next() / prev() / seek(s) / volume(v) / now() -> dict / probe(path)   §3
```

Leases (Python gets them as `machine` objects, §1:
`moybyte.lease(name, **cfg)` and `moybyte.pins()`; `pins` is the digital
subset every tier may hold), and on the compiled tiers leases and the
`http`, `lora`, `mic`, `ble`, `usb`, `midi`, `camera` and `media` devices, the
`pins` and the radio link's ports (`net`) by handle (`runtime/system/dev_handles.py`;
moybyte.app's `moybyte_dev_*`, moybyte.os's `dev_*`, C's `moy_dev_*`):

```
dev_open(name, cfg) -> h              BUSY while another holds it; cfg "key=value ..."
dev_close(h)
dev_read(h, into) -> n                what waits: bytes, edges (8 bytes each), a packet, samples
dev_write(h, bytes) -> n
dev_xfer(h, bytes, into) -> n         SPI full duplex; an I2C write then read
dev_set(h, cfg); dev_get(h, what) -> n
```

The radios ride the same rows. A word of a radio's `dev_set` starts one
action, and `dev_write` carries bytes to where a word pointed it:

```
ble   scan=<ms>  connect=<addr>|off  find=<uuid>  read=<h>  subscribe=<h>  unsubscribe=<h>
      advertise=<name>|off [data=<hex> interval=<ms>]   write=<h> [response=0|1]: where dev_write goes
      dev_read: the face's events since the last read, whole records:
                event u16, n u16, a i32, b i32 (moy_gatt.h's), then n bytes --
                ADV the address and the name, READ the value, NOTIFY the notification
      dev_get: connected
lora  join=otaa dev_eui=<hex> join_eui=<hex> app_key=<hex> [dr]
      send=<port>|raw [confirmed=0|1 dr]: what dev_write sends, an uplink or a packet
      dev_read: the next packet or downlink; dev_get: port, joined, fcnt, busy
usb   serial=<addr> [baud=<n>]  write=<addr>: where dev_write goes
      dev_read: the USB face's events, whole records with ble's head (a the address):
                ATTACH (one for each device plugged in at the open) its class,
                kind, vid, pid and name; REPORT the report; DATA the bytes
                sent; DETACH and SENT none
midi  open: uart=<row> [baud=<n>] | usb=<addr> | ble=<addr>, one port a handle
      dev_write: whole MIDI messages; dev_read: its messages, 3 bytes each
      (status, data1, data2); dev_get: port, state (0 connecting, 1 ready, 2 failed)
camera dev_read: the latest frame, whole, w*h RGB565 big-endian; dev_get: w, h
media dev_write: play the source the bytes name; volume=<0..100> pause=1|0 seek=<s>
      skip=next|prev stop=1; dev_get: state, paused, entry, entries, at, len, volume,
      stamp, mine, ...; dev_read: title, artist, station, src (four lines);
      dev_xfer: a file's probe (seconds u32, then title and artist)
pins  gpio<n>=0|1 ...: each pin driven; dev_get gpio<n>: one read (an input if not driven)
net   open: port=<1..65535> [from=any|match], one port a handle (docs/netplay_v1.md §2)
      dev_write: one message to every console in range on the port, 240 bytes at most
      dev_read: the port's messages, whole records: the sender's MAC (6 bytes),
                port u16, n u16, then n bytes; dev_get: port, drops, peers
```

A `ble`, `usb` or `midi` handle reads its driver's ring through a reader
of its own (only that source; a `midi` handle only its port's messages),
as an edge handle reads its pin's; the records are the ones the face's
`on` wakes Python on, each with the bytes a read, a notification, a report
or a serial line carried. Each is under the permission Python's face needs
(`ble`, `lora`, `usb`, `midi`, `camera`, `media` or `media:url`); one
holder rules across the tiers, a process opens a radio, the USB host, the
camera or the media service once, and its end closes the handle,
which drops the connection, the beacon and the radio, lets the USB host
go, closes the MIDI port (and its UART's lease) or stops the stream, as
Python's face sweep does.

In C (`native/moy_kernel/moy_lease.h`): `moy_lease_take(owner, cls, unit,
pins, flags, &h)`, `moy_lease_drop(h)`, `moy_lease_sweep(owner)`,
`moy_lease_edge(h, pin, depth, loop, &src)`; the module `moy_lease` binds them.

## 5. How it maps onto what exists

| design piece | today | what changes |
|---|---|---|
| hardware declaration | `[hardware]` in every ESP32 `board.toml`, every pin classified and checked (`tools/board_config.py`, `tests/test_board_toml.py`); the P4 READMEs' free pins are their `lease` rows (P2.1), with `uart1` and `spi2` riding them, offered as `MOY_LEASE_ROWS` | |
| the lease table | `native/moy_kernel/moy_lease.h` and `runtime/system/leases.py`: takes, the sweep, safe pins, `machine` objects (Pin, UART, SPI, PWM, ADC, a SoftI2C on the pins), `pins` (P2.3; `tests/test_leases.py`); the compiled tiers' handles, `runtime/system/dev_handles.py` (`tests/test_dev_handles.py`, and each tier's fixture) | a hardware I²C unit as a class of the table |
| the link ring | `native/moy_net/moy_link.h`, counting its drops | the link's records a ring source's (LINK) |
| GPIO edges | `native/moy_input/moy_edge.h`: a leased pin's edges as an ISR ring source, opened through `moy_lease_edge` and closed by the drop (P2.3) | |
| brightness | `runtime/system/settings_layer.py`: "stored and applied by nothing yet" | applied at boot and on change (P1.2); the backlight is a `class` row naming `brightness` on every console (P2.1), whose API is P2.3's |
