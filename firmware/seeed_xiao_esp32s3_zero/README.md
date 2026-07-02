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

The XIAO enters ROM download via esptool's `usb_reset`; if a running app holds the port,
hold **B (BOOT)** + tap **R (RESET)**, release B.

```bash
PY=.venv/bin/python
# 1. (optional) confirm chip: 8MB flash, 8MB PSRAM, USB-Serial/JTAG
$PY -m esptool --port /dev/ttyACM0 --before usb_reset --after no_reset flash_id
# 2. erase + write stock MicroPython (SPIRAM_OCT) at 0x0
$PY -m esptool --chip esp32s3 -p /dev/ttyACM0 --before no_reset --after no_reset erase_flash
$PY -m esptool --chip esp32s3 -p /dev/ttyACM0 --before no_reset --after hard_reset \
     write_flash -z 0x0 ESP32_GENERIC_S3-SPIRAM_OCT-*.bin
# 3. push the Zero files
$PY -m mpremote connect /dev/ttyACM0 cp zero_net.py :zero_net.py + cp main.py :main.py
```

## Files

- `main.py` — boot entry: brings up WiFi (AP by default), then starts the headless console
  (`moy_zero.run_zero`) serving the web view on port 80. A crash raises to the REPL.
- `zero_net.py` — WiFi bring-up: `start_ap()` (host `MoyByte-Zero`, join → http://192.168.4.1)
  or `start_sta(ssid, key)` (join your LAN). Drop a `zero_config.py` on the board with
  `MODE='sta'`, `WIFI_SSID`, `WIFI_KEY` to override.
- `moy_zero.py` — the **headless backend**. Reuses `console.Workstation` +
  `moy_runtime.make_api/Image` + `web_view` (DrawRecorder/TeeCanvas/ServedState) +
  `moy_webserver.WebServer`. The one new piece is the recording canvas:
  `TeeCanvas(_NullCanvas, DrawRecorder)` in atlas form — draws never rasterize, they only
  record atlas-form draw-commands the browser replays. `run_zero()` is the headless loop
  (draw → record → serve between frames). No native modules, no framebuffer, no flush.

### Staged shared modules (pushed via mpremote, from the T-Deck `modules/` tree)

`web_view.py`, `moy_webserver.py`, `console.py`, `editors.py`, `blocks.py`, `moy_carts.py`,
`audio.py`, `carts_data.py`, `moy_runtime.py`, and the `moybyte/` package. All pure Python;
`moy_runtime` imports cleanly on stock MicroPython (its native `moy_gfx`/compositor use is
lazy + fallback-guarded, never hit on the Zero). The full chain uses ~600 KB of heap.

> Not yet automated: a `stage.sh` to copy these + a freeze into a custom image (M2). For now
> the staging command lives in git history / this session.

## Status

- **M0 (done):** MicroPython flashed; PSRAM + 6 MB VFS confirmed; SoftAP `MoyByte-Zero`
  auto-starts on boot (`http://192.168.4.1`), verified broadcasting.
- **M1 (done, on-device verified):** headless console streams to the web view. Verified on
  the board: full import chain loads; headless frames produce a valid draw-command stream;
  `assets()`/`frame_payload` serialize; and a real-socket loopback test against the AP IP got
  `GET /` (page, 200), `GET /assets` (200), a WebSocket `101` handshake, and a live frame push
  carrying `cmds`. **Browser render is the remaining hand-off (phone test).**
- **M2 (next):** carts in flash (a `zero_config`-rooted `moy_carts` store + write path), browser
  input polish (buttons/keys), a `stage.sh` + optional frozen custom image, efficiency
  (switch launcher to fewer full-frame sprs).
