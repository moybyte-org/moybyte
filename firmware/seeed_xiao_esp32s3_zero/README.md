# MoyByte Zero — Seeed XIAO ESP32-S3 port

The **Zero** tier (see `docs/hardware_lineup.md`): a headless ESP32-S3 with no
screen/keyboard/SD. Its entire UI is **streamed to a browser** over WiFi — the device
runs cart *logic* and ships draw-commands; the browser rasterizes ("the browser is the
GPU", #41/#22). Because it never rasterizes, the Zero needs **none** of the T-Deck's
native modules (`moy_gfx`/`moy_sd`/`moy_alloc`) — it's pure Python.

## Why this port is different from the T-Deck

The T-Deck firmware (`firmware/lilygo_t_deck_plus_micropython/`) is a **custom
lvgl_micropython build** (ESP-IDF 5.5, native C modules, frozen modules). The Zero
deliberately does **not** need that. It runs **stock MicroPython** + pushed `.py`
files, so the dev loop is `mpremote cp` with no build step.

## Board

- Seeed **XIAO ESP32-S3** (ESP32-S3R8): 8 MB flash (quad), **8 MB octal PSRAM**.
- Console is the **native USB-Serial/JTAG** (`303a:1001`), i.e. `/dev/ttyACM0`.
- Firmware: **MicroPython v1.28.0**, variant `ESP32_GENERIC_S3-SPIRAM_OCT`
  (the *octal* SPIRAM build — matches the R8 PSRAM; ~8 MB heap free, ~6 MB VFS).

## ⚠️ Talking to the board: assert DTR

The USB-Serial/JTAG CDC only flushes TX **once the host asserts DTR**. A serial client
that opens the port with DTR de-asserted sees a **silent REPL** (no banner, no echo) —
this looks like a dead/mis-flashed board but isn't. `mpremote` asserts DTR, so it works
once the board has finished booting; a raw `pyserial` client must set `s.dtr = True`.

## Flash (stock MicroPython)

### Getting into ROM download mode (no physical button, usually)

The XIAO's USB is the ESP32-S3's **USB-Serial/JTAG** (`303a:1001`) — a ROM peripheral that can be
commanded into download mode over USB, so flashing usually needs no button. Which path works
depends on what's currently running:

- **From another firmware / a fresh board** (USB is still the ROM JTAG `303a:1001`): esptool's
  `--before usb_reset` drops it into the bootloader. This is how the initial Meshtastic →
  MicroPython flash was done. (`--before default_reset` — the classic DTR/RTS auto-reset — can
  fail with an `Input/output error` when the running app holds the port; use `usb_reset`.)
- **From running MicroPython** (USB is now the app's TinyUSB **CDC** `303a:4001`): the software
  reset paths are **unreliable** here. `machine.bootloader()` over the CDC produced *no handoff*
  in the spike (`docs/history/SPIKE_RESULTS.md`) — try it, but don't count on it.

> ⚠️ **Do NOT run `esptool --before default_reset` against the running CDC (`303a:4001`).** The
> DTR/RTS dance into TinyUSB can **wedge the CDC at the USB level** — the port then gives
> `EPROTO` / `Protocol error` on open with no re-enumeration, and is unrecoverable without a
> `USBDEVFS_RESET` (root) or a physical **replug**.

- **Reliable fallback (always works):** hold **B (BOOT)**, tap **R (RESET)**, release B → the ROM
  bootloader comes up as `303a:1001`; then flash normally.

### Flash + stage

```bash
PY=.venv/bin/python
# 1. (optional) confirm chip: 8MB flash, 8MB PSRAM, USB-Serial/JTAG
$PY -m esptool --port /dev/ttyACM0 --before usb_reset --after no_reset flash_id
# 2. erase + write stock MicroPython (SPIRAM_OCT) at 0x0
$PY -m esptool --chip esp32s3 -p /dev/ttyACM0 --before no_reset --after no_reset erase_flash
$PY -m esptool --chip esp32s3 -p /dev/ttyACM0 --before no_reset --after hard_reset \
     write_flash -z 0x0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin
# 3. push the console (shared modules + the Zero backend) -- see "Flashing / staging" below
firmware/seeed_xiao_esp32s3_zero/stage.sh /dev/ttyACM0
```

## Files

- `main.py` — boot entry: starts the headless console (`moy_zero.run_zero`) on port 80. The
  network is chosen at boot (see below): **join a saved WiFi (STA)**, else **host the AP**.
- `zero_net.py` — WiFi bring-up helpers: `start_ap()` (host `MoyByte-Zero` → http://192.168.4.1)
  and `start_sta(ssid, key)` (join a LAN).
- `moy_zero.py` — the **headless backend**. Reuses `console.Workstation` +
  `moy_runtime.make_api/Image` + `web_view` (DrawRecorder/TeeCanvas/ServedState) +
  `moy_webserver.WebServer` + `moy_carts` (a flash-rooted store). The one new piece is the
  recording canvas: `TeeCanvas(_NullCanvas, DrawRecorder)` in atlas form — draws never
  rasterize, they only record atlas-form draw-commands the browser replays. `run_zero()` brings
  up the network (STA-from-saved-creds → else AP) then runs the headless loop (draw → record →
  serve between frames). No native modules, no framebuffer, no flush.
- `stage.sh` — push everything to a XIAO already running MicroPython (see Flashing below).

## WiFi: provisioning + why STA matters

The Zero streams over the **same** web-view infra the T-Deck uses (`web_view` + `moy_webserver`,
identical modules). An ESP32 **SoftAP** has much weaker throughput than **STA** (joining a
router), so an AP-mode Zero suffers periodic multi-second send-stalls that trip the server's
idle-reaper → the browser reconnects. So the Zero prefers STA and only hosts an AP to provision:

1. Boot with no saved network → **hosts `MoyByte-Zero`** (key `moybyte123`, http://192.168.4.1).
2. Join it, open the console, run the **WiFi** cart (`system_carts/wifi.moy`), pick your network,
   save. Creds persist to `/moybyte/wifi.json` on flash (a root-parameterized `moy_carts` store —
   the same store code the T-Deck uses on SD).
3. Reboot (≡ menu → Reboot, or power-cycle) → it **joins your WiFi (STA)**, reachable at
   **http://moybyte.local** (mDNS `network.hostname`) or its router IP. Streaming is smooth.

Dev shortcut: drop a `zero_config.py` with `WIFI_SSID`/`WIFI_KEY` to preseed a network into the
store (STA without the cart) — handy for testing.

## Flashing / staging

One-time: flash stock MicroPython (the esptool block above). Then push the Python:

```bash
firmware/seeed_xiao_esp32s3_zero/stage.sh [PORT]     # PORT defaults to the first /dev/ttyACM*
```

`stage.sh` pushes the shared console modules — from the **single source**, the T-Deck `modules/`
tree, so the two ports can't drift (`web_view` `moy_webserver` `console` `editors` `blocks`
`moy_carts` `audio` `carts_data` `moy_runtime` + the `moybyte/` package) — plus the Zero's own
`zero_net`/`moy_zero`/`main`, over mpremote in one connection. `moy_runtime` imports cleanly on
stock MicroPython (its native `moy_gfx`/compositor use is lazy + fallback-guarded, never hit on
the headless Zero); the full chain uses ~600 KB of heap. A frozen custom image is a later option;
pushed `.py` is the fast dev loop.

## Status

- **M0 (done):** MicroPython flashed; PSRAM + 6 MB VFS confirmed.
- **M1 (done, hardware-verified):** headless console streams to a browser — launcher, editors,
  and carts (Sky Run / Brick Siege ~30 fps steady). Confirmed on a phone.
- **WiFi provisioning (done, hardware-verified):** AP-by-default → WiFi cart saves a network →
  STA on reboot; STA eliminates the AP-mode disconnects.
- **M2 (next):** on-flash cart CRUD (`can_manage`), browser button/key input polish, an optional
  frozen image, launcher payload efficiency.

## Known quirks

- The USB-Serial/JTAG REPL is **silent unless the host asserts DTR** (see above); this is why a
  raw `pyserial` probe and even `mpremote` look dead until DTR is raised.
- Interrupting the *running* console loop over USB while WiFi is active can reset the board and
  flip its USB PID (`303a:1001` ↔ `303a:4001`, port `ttyACM0` ↔ `ttyACM1`) — a dev-tooling
  quirk, not a runtime issue (it streams stably in normal use). Drop to a bare REPL before
  `stage.sh`, and detect the port dynamically.
