# Moybyte MicroPython-First T-Deck Spike

This directory is a parallel firmware spike for the LilyGO T-Deck Plus.

It does not replace the existing Arduino smoke-test firmware in
`firmware/lilygo_t_deck_plus`. The goal is to produce a launcher-friendly `.bin`
that can be copied to SD and started from the user's existing launcher, without
using USB flashing for the first test.

## Goal

Answer one question:

```text
Can a MicroPython + LVGL Moybyte shell on the T-Deck Plus run Tiny Runner with
acceptable display, input, reset recovery, and memory behavior?
```

## Architecture

```text
lvgl_micropython firmware
  frozen boot.py/main.py
  frozen moybyte Python package
  frozen Tiny Runner project
  T-Deck display/input bootstrap
```

There is one MicroPython VM. The shell loads a child project into a controlled
namespace, then calls `update(dt)` and `draw()` each frame.

## Build

The build script fetches `lvgl_micropython` into `.build/`, freezes the local
Moybyte spike modules, and copies the SD-launcher app image into `dist/`.

```bash
make firmware-build-lilygo-micropython
```

The wrapper intentionally throttles heavy builds by default:

- `MOYBYTE_BUILD_JOBS=2`
- `MOYBYTE_BUILD_NICE=15`
- idle IO priority through `ionice` when available

For a gentler build while using the machine for other work:

```bash
MOYBYTE_BUILD_JOBS=1 make firmware-build-lilygo-micropython
```

Expected output:

```text
firmware/lilygo_t_deck_plus_micropython/dist/moybyte_micropython_tdeck.bin
```

Named hardware-test builds can be produced by setting `MOYBYTE_ARTIFACT_NAME`.
The wrapper also supports two board bases:

- `MOYBYTE_BOARD_CONFIG=generic`: generic `ESP32_GENERIC_S3` plus ST7789.
- `MOYBYTE_BOARD_CONFIG=tdeck`: upstream `lvgl_micropython` `LilyGo-TDeck`
  custom board config with its T-Deck display/input wiring.

The T-Deck custom board path needs upstream TOML parsing, so the wrapper uses
`.venv/bin/python` when it exists. `MOYBYTE_BUILD_PYTHON` can override this.

Current planned test builds:

```bash
MOYBYTE_BUILD_JOBS=1 MOYBYTE_ARTIFACT_NAME=moybyte_generic_cdc_uart MOYBYTE_BOARD_CONFIG=generic MOYBYTE_REPL=cdc_uart make firmware-build-lilygo-micropython
MOYBYTE_BUILD_JOBS=1 MOYBYTE_ARTIFACT_NAME=moybyte_generic_jtag_repl MOYBYTE_BOARD_CONFIG=generic MOYBYTE_REPL=jtag make firmware-build-lilygo-micropython
MOYBYTE_BUILD_JOBS=1 MOYBYTE_ARTIFACT_NAME=moybyte_lvgl_tdeck_board MOYBYTE_BOARD_CONFIG=tdeck make firmware-build-lilygo-micropython
MOYBYTE_BUILD_JOBS=1 MOYBYTE_ARTIFACT_NAME=moybyte_lvgl_tdeck_board_jtag MOYBYTE_BOARD_CONFIG=tdeck MOYBYTE_REPL=jtag make firmware-build-lilygo-micropython
```

Each build emits an SD-launcher app image and two merged full-flash images:

```text
dist/<name>.bin
dist/<name>_full_dio_0x0.bin
dist/<name>_full_qio_0x0.bin
```

## Host development

The console runs on the PC from the **same shared code** this firmware
freezes — see `tools/simulate_desktop.py` (it renders the same launcher / carts /
code+paint editors). The old fake-LVGL `.moyproj` simulator
(`simulate_micropython_spike.py`) was removed with the legacy game loop.

## Hardware References

The official LilyGO T-Deck repository is the hardware pin/display reference, but
its examples are Arduino/PlatformIO rather than MicroPython. Two MicroPython
references are useful but not drop-in replacements:

- `lvgl_micropython/display_configs/LilyGo-TDeck` has a T-Deck custom board
  config for LVGL MicroPython.
- TulipCC has an ESP-IDF/MicroPython/LVGL T-Deck port that starts native
  display/input tasks before MicroPython and uses a native framebuffer blit path
  for stable full-screen refresh.
- Tulip also uses the ESP32-S3 USB Serial/JTAG console path on T-Deck. The
  `moybyte_*_jtag*` builds are comparison images for that console path; they do
  not port Tulip's native display task.

An early native init patch exists as an experiment so GPIO10 stays high and
shared SPI chip-selects stay deselected before frozen Python starts. It is
disabled by default after producing a launcher black-screen build on this unit;
set `MOYBYTE_EARLY_BOARD_INIT=1` only when testing that path deliberately.
Longer term, the Tulip-style native framebuffer/canvas path is likely a better
fit for Moybyte games than per-frame LVGL object updates.

For the SD launcher, use `moybyte_micropython_tdeck.bin`. It is the ESP32 app
image, matching the style of a normal Arduino/PlatformIO `firmware.bin`.

Do not use the merged full-flash image generated internally by the upstream
builder with the SD launcher. On the T-Deck launcher that path reports an
update error.

Launcher-based boot is still the preferred quick app-test loop for this unit,
but full USB flashing at `0x0` is confirmed to work when the image is known
good. LilyGO's official PlatformIO `examples/HelloWorld` image was built,
merged as `dist/tdeck_official_helloworld_full_dio_0x0.bin` in the local
reference checkout, flashed at `0x0`, and confirmed on hardware. Current
MicroPython full-flash blank screens should be treated as MicroPython
firmware/config/display-init regressions rather than a bad flashing method.

After the boot marker, the firmware shows a short game selector. If untouched,
it starts Tiny Runner. Left/right chooses a slot, and A/Run starts the selected
slot.

Current slots:

- Tiny Runner: the baseline movement/coin test.
- Input Test: shows held button state.
- Bounce Box: simple movement/bounce render test.
- SD Project: explicitly tries to mount SD and load project source.

The on-screen status line reports `fps`, last key, raw keyboard mode, and a held
button mask. The mask bits are:

- `01`: left
- `02`: right
- `04`: up
- `08`: down
- `10`: Moybyte action `a`
- `20`: Moybyte action `b`

Shell controls:

- Home or Stop pauses the running project and shows a stopped screen.
- Run reloads the current project and resumes execution.

Automatic external project probing is disabled during boot so it cannot block
the first visible frame. The current SD test build enables the explicit SD
Project selector slot; earlier recovery builds kept it disabled after a
hardware black-screen regression. Frozen Tiny Runner, Input Test, and Bounce Box
remain available.

When the SD Project slot is re-enabled, it mounts the shared SPI SD card and
tries these files:

- `/sd/moybyte/project.py`
- `/sd/moybyte/main.py`
- `/sd/project.py`
- `/sd/main.py`

The mount helper does not treat an empty `/sd` directory as a mounted card; this
avoids a stale mount point after a failed SD attempt. If mount or load fails, the
shell reports the failure and falls back to frozen Tiny Runner.

## Display Notes

The ST7789 panel is configured as native `240x320` portrait and then rotated to
the console's landscape `320x240` shell. A `320x240` native configuration boots
but leaves the right side black because only 240 native columns are addressed.

## USB flashing

### Getting the board into the ROM loader

**There is no BOOT button on this board — the trackball click is GPIO0.** Hold
the trackball in while you power the board on (cable in, or the power switch),
then let go: it comes up in the ROM download loader instead of the console.

**And when the write finishes, press RST.** The board stays in the loader until
you do — the new firmware is on flash but nothing is running it.

Both ends are manual for the same reason: the USB port is the ESP32-S3's own,
and nothing can drive this board's reset line over it. That is why
`tools/esptool_no_modem.py` exists (see below — the combined RTS/DTR ioctl fails
on this CDC node) and why the website's flasher connects with `no_reset` and
does not attempt a reset afterwards. On the P4, by contrast, the CH343 bridge
resets into the loader and back out again on its own, and none of this applies.

USB full flashing is valid on this board, but use it deliberately because it
replaces launcher. The stable app-development loop is still: build the
launcher-friendly `.bin`, copy it to SD, and launch it from the restored
launcher. Use full flash when testing bootloader, partition, USB console, or
early native display changes.

Normal full flash:

```bash
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0
```

This target uses `tools/esptool_no_modem.py`, which avoids the combined
RTS/DTR ioctl that fails on the observed T-Deck Plus USB CDC node.
It writes the app from `MPY_APP_BIN`, defaulting to
`firmware/lilygo_t_deck_plus_micropython/dist/current/moybyte-current-app.bin`.
The build script refreshes this alias on every successful MicroPython build, so
the flash target does not accidentally use a stale experimental `micropython.bin`
from the build directory.

Merged full image flash:

```bash
make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0
```

This writes `MPY_FULL_BIN` at `0x0`, defaulting to
`firmware/lilygo_t_deck_plus_micropython/dist/current/moybyte-current-full-dio-0x0.bin`.
Use this for test images named `*_full_dio_0x0.bin` or `*_full_qio_0x0.bin`; it
is closest to the official LilyGO prebuilt firmware flow.

Example:

```bash
make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0 MPY_FULL_BIN=firmware/lilygo_t_deck_plus_micropython/dist/moybyte_lvgl_tdeck_board_jtag_full_dio_0x0.bin
```

If a full MicroPython image black-screens after a different firmware or
partition layout was on the board, flash erase is still available as a
MicroPython-specific diagnostic. Arduino HelloWorld can boot without erase, so
this is not a general flashing requirement. The reason it can still matter for
MicroPython is that its frozen `_boot.py` mounts the `vfs` data partition before
the user's `boot.py`; stale non-MicroPython filesystem data can stop execution
before Moybyte code runs.

```bash
make firmware-flash-lilygo-micropython-full-erase PORT=/dev/ttyACM0 MPY_FULL_BIN=firmware/lilygo_t_deck_plus_micropython/dist/moybyte_cold_gpio_generic_cdc_uart_full_dio_0x0.bin
```

To test that path without erasing flash, build with MicroPython's automatic VFS
mount disabled:

```bash
MOYBYTE_BUILD_JOBS=1 MOYBYTE_ARTIFACT_NAME=moybyte_diag_skip_vfs_generic_cdc_uart MOYBYTE_BOARD_CONFIG=generic MOYBYTE_REPL=cdc_uart MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython
```

Known-good hardware result:

```text
firmware/lilygo_t_deck_plus_micropython/dist/moybyte_diag_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

This image has booted into Moybyte on the LilyGO T-Deck Plus from direct full
flash. It is still a diagnostic baseline because it skips MicroPython's normal
`/` filesystem mount; frozen Moybyte modules work, but features that depend on
MicroPython's internal writable filesystem need a proper VFS fix.

SD Project test image:

```text
firmware/lilygo_t_deck_plus_micropython/dist/moybyte_sd_slot_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

This keeps the known-good skip-VFS boot/display path, enables only the explicit
SD Project selector slot, and starts the watchdog before selector/project
loading. Put a project at `/moybyte/project.py` on the SD card, then choose
`SD Project` in the selector. The checked-in sample lives at
`firmware/lilygo_t_deck_plus_micropython/sdcard/moybyte/project.py`.

The current fixed SD source-loader image is:

```text
firmware/lilygo_t_deck_plus_micropython/dist/moybyte_sd_prefetch_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

The stable alias for the same latest DIO full-flash image is:

```text
firmware/lilygo_t_deck_plus_micropython/dist/current/moybyte-current-full-dio-0x0.bin
```

It keeps the same SD test scope, preloads Moybyte API names for SD projects,
neutralizes a leading `from moybyte import *` line for compatibility, and reads
the SD project before display/LVGL init. After the SD source is cached, it
unmounts/releases the card and loads the cached project from the selector. This
is meant to avoid stale display updates caused by SD access on the shared SPI bus
after the panel is already running.

Expected prefetch breadcrumbs:

- Serial before the selector: `Moybyte SD prefetched /sd/moybyte/project.py bytes ...`
- Screen after choosing SD Project: `cached SD project`, then `loaded SD project`

If the screen still says `mounting SD`, the prefetch path did not find the file
before display init and the firmware fell back to the older after-display SD
read path. Check serial for `Moybyte SD prefetch failed` or
`Moybyte SD prefetch found no project`.

### Live SD reads/writes while the panel is running (`moy_sd`)

The boot prefetch above mounts SD with `machine.SDCard`, which works only
*before* `init_display()` because it re-runs `spi_bus_initialize()` on the host
the panel later claims. For SD access *after* the panel is live (cart saves,
re-scans, create/duplicate/delete in the workstation) the firmware uses the
native `moy_sd` module (`native/moy_sd/modmoy_sd.c`).

`moy_sd` follows the ESP-IDF "Sharing the SPI Bus Among SD Cards and Other SPI
Devices" pattern: it does **not** re-initialize the bus. `esp_lcd` already ran
`spi_bus_initialize()`, so `moy_sd` only `sdspi_host_init_device()`s the card as a
second device on that same host and probes it. The panel device is left attached,
so the display keeps working afterward. `moybyte_sd.with_sd_live(fn)` mounts the
card (FAT via a `moy_sd`-backed block device) **once and keeps it resident**, then
runs `fn`. The desktop loop is single-threaded with LVGL's task handler stopped
(native takeover), so an SD session runs strictly between frames and never
collides with a `tx_color` flush. This is why `Workstation.can_manage` is now
enabled on device (`run_desktop` wires `_with_sd = moybyte_sd.with_sd_live`).

**Do not tear the SD device down between ops.** The first cut unmounted +
`sdspi_host_deinit`'d after every write and also forced `TFT_CS` high via
`Pin(...)`; both corrupt the shared bus/DMA state, and the *next* panel flush
**silent-hangs** the board — the write lands on SD, then resume freezes with no
panic and USB still enumerated but dead (confirmed over serial: nothing after
`Moybyte desktop running`). Keep the card mounted, leave `TFT_CS`/`SD_CS` to their
drivers, and only park the unused LoRa `RADIO_CS` high.

Recommended full-flash order for the next hardware pass:

1. `moybyte_lvgl_tdeck_board_jtag_full_dio_0x0.bin`: custom T-Deck board config
   plus USB Serial/JTAG console comparison.
2. `moybyte_lvgl_tdeck_board_full_dio_0x0.bin`: custom T-Deck board config with
   normal CDC-style MicroPython USB.
3. `moybyte_generic_jtag_repl_full_dio_0x0.bin`: generic S3 display path plus
   USB Serial/JTAG console.
4. `moybyte_generic_cdc_uart_full_dio_0x0.bin`: generic cleaned build closest to
   the previous working app-image path.

Try the matching `_full_qio_0x0.bin` only if the DIO image does not boot and the
board is still recoverable through launcher/ROM flashing.

If the board is already in ROM download mode and DTR/RTS reset control is
failing:

```bash
make firmware-flash-lilygo-micropython-no-reset PORT=/dev/ttyACM0
```

The no-reset target intentionally leaves the board in ROM/stub mode after a
successful write. If software reset does not start the app, release BOOT and
press the board reset button.

To try leaving ROM mode without writing flash again:

```bash
make firmware-run-lilygo-micropython PORT=/dev/ttyACM0
```

Serial monitor:

```bash
make firmware-monitor-lilygo-micropython PORT=/dev/ttyACM0
```

If esptool reports `Failed to connect to ESP32-S3: No serial data received`, the
board is visible but did not enter ROM download mode. Put the board into
bootloader/download mode manually, then retry the no-reset flash command.

OpenOCD/JTAG flashing requires the board to expose the ESP USB-JTAG interface
(`303a:1001`) and host write permission to the raw USB node. The current running
firmware enumerates as CDC-only `303a:4001`, so OpenOCD is not the default loop.

## OTA firmware updates (#53)

The device can flash a new firmware image **from the SD card** — no USB cable after
the first install.

How it works:

- The build now ships a **dual-OTA partition table** (`build.sh --ota` →
  `nvs + otadata + phy_init + ota_0 + ota_1 + vfs`, both app slots 4MB on the 16MB
  part). A new image is written to the **inactive** slot, then `set_boot()` + reset
  ping-pongs between `ota_0`/`ota_1` — the running slot is never touched, so a
  failed/half-written update cannot brick the board.
- **Rollback is the safety net.** `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`: a
  freshly-flashed app that never calls `esp32.Partition.mark_app_valid_cancel_rollback()`
  is reverted by the bootloader on the next boot. `run_desktop` calls it once the
  desktop is up, so a bad image self-heals back to the previous slot.
- The image source is `/sd/update/*.bin` (the **app-only** image — i.e.
  `moybyte_micropython_tdeck.bin`, *not* the merged `..._full_*_0x0.bin`). SD shares
  the panel SPI host, so the updater reads through the same `with_sd_live` path as
  cart saves (see the SD section above). Device code: `modules/moy_ota.py`
  (`OtaUpdater`); the UI is the shared console's **Settings → UPDATE FW** screen,
  which drives the install one 32K chunk per frame with a progress bar.

### One-time switch to OTA — DONE on this board

**This T-Deck is already on the dual-OTA layout** (owner-confirmed 2026-08-15; it
took a real OTA on 2026-08-02 — see the WiFi-download section below). So the
step here is HISTORY for it, and an ordinary
`make firmware-flash-lilygo-micropython` is what a dev flash needs.

It still applies to a board that has never carried the OTA table — a fresh unit,
or one recovered to a legacy single-app image. For those, the **first** flash of
an OTA build must be a full-image USB flash that also rewrites the partition
table and clears `otadata`:

```bash
MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython
make firmware-flash-lilygo-micropython-full-erase PORT=/dev/ttyACM0   # erases + lays down ota_0
```

After that, updates are wireless/SD: copy `moybyte_micropython_tdeck.bin` to
`/sd/update/` on the card, then on the device open **Settings → UPDATE FW → INSTALL**.
The device flashes the inactive slot and reboots into the new firmware. (App-only USB
reflashes during dev now target the `ota_0` offset `0x20000` via `MPY_APP_OFFSET`; the
`-full`/`-full-erase` images already bake the correct offsets.)

### WiFi download (Settings → UPDATE ONLINE, Phase 3)

If the device is online, it can pull the image itself instead of you copying it to SD:

- **Out of the box there is nothing to configure.** The two channels are the two
  branches, and CI publishes a `latest.json` on each one's rolling release, which
  `moy_ota.DEFAULT_CHANNEL_URLS` points at:

  | Settings → CHANNEL | branch | release | what it is |
  |---|---|---|---|
  | STABLE | `master` | `firmware-latest` | tested; also what the site's flasher writes |
  | BETA | `dev` | `firmware-beta` | every dev push, untested by definition |

  (Release assets redirect to GitHub's CDN, so the updater follows redirects —
  see `_http_open`. **This path is unverified on glass**; the LAN one below is
  the hardware-confirmed one.)
- **Manifests from those urls must be SIGNED.** The TLS here does not verify
  certificates, so without a signature anyone who can answer for `github.com` on
  the local network could hand the board their own firmware — and the manifest's
  `sha256` would not help, since they would be writing the manifest too. CI signs
  with a key whose public half is baked into the image you flashed
  (`OTA_PUBLIC_KEYS`); the device rebuilds the PKCS#1 block and compares it whole.
  A manifest from your OWN `ota.json` need not be signed — putting a config on the
  card is a physical act of consent, and it keeps the LAN loop below key-free —
  but a signature that is present is always checked either way.
  `make ota-keygen` generates the key, prints the `gh secret set` line, and prints
  the constant to paste. Back the key up: it is baked into every image in the
  field, so replacing a lost one costs everybody a USB reflash.
- To point a board somewhere else — a LAN host, an offline classroom, your own
  build — put a config on the card at **`/sd/update/ota.json`**, which WINS over
  the defaults:
  ```json
  { "channels": { "stable": "https://your-host/moybyte/stable/latest.json",
                  "unstable": "https://your-host/moybyte/unstable/latest.json" } }
  ```
  (The older single-channel `{ "manifest_url": ... }` is still honoured.)
- The **manifest** at that URL describes the latest build:
  ```json
  { "version": 2, "url": "https://your-host/moybyte/moybyte_micropython_tdeck.bin",
    "size": 3332752, "sha256": "<hex sha256 of the .bin>" }
  ```
  Don't hand-write it — generate it from the built image so `size`/`sha256`/`version`
  can't drift (`version` is read back out of `moy_ota.FIRMWARE_VERSION`):
  ```bash
  make ota-manifest                                   # -> dist/latest.json (http://<LAN-IP>:8000)
  make ota-manifest OTA_BASE_URL=https://your-host/moybyte
  make ota-serve                                      # local static server over dist/ (test loop)
  ```
  (`make ota-manifest` prints the exact `ota.json` line to drop on the SD card. To
  *test* the update path without bumping the firmware, pass a higher version:
  `python tools/gen_ota_manifest.py --version 2`.)
- **Settings → UPDATE ONLINE** connects (reusing a network the kid already joined via
  the WiFi cart — it never asks for a password), fetches the manifest, and if
  `version > FIRMWARE_VERSION` it **streams the `.bin` straight to `/sd/update/firmware.bin`**
  (raw socket → SD, never buffering the whole 3 MB in RAM), verifying `size` + `sha256`.
  It then hands off to the same confirm → install → reboot path as above.
- **`FIRMWARE_VERSION` is bumped by `make release`**, which is also what merges `dev`
  into `master` — the merge is the release, so the two happen together and the
  constant is never hand-edited. The manifest's `version` always matches the image
  it describes: CI reads it back out of the identity `build.sh` stamped into the
  build (a beta's is a build epoch, so every dev push reads as newer). The online
  check offers an update when the manifest is strictly newer within the channel, or
  belongs to the other channel (same convention as cart versioning).

`http://` and `https://` are both supported (TLS via the frozen `ssl`). The downloaded
image is checksummed before it can be flashed, and a corrupt/truncated download (or a
bad flash) still falls back to the rollback safety net above.

> Status: the **local SD install** path (UPDATE FW) is host-tested end to end (build
> wiring, updater API, confirm→install→done UI). The **WiFi download** path (UPDATE
> ONLINE) is host-tested for orchestration with a fake, but the real socket/TLS + the
> WiFi↔display RAM coexistence (the #38 caveat — the WLAN stack and the LCD DMA flush
> compete for internal RAM) are **UNVERIFIED on hardware**. The on-device
> flash/reboot/rollback/download pass is still **TODO** — verify on a real T-Deck
> before relying on it.

## Web view — deleted 2026-08-12

Gone: the browser mirror that streamed this board's frames over WiFi
(Settings row, controller module, Tee recording lane). The reasoning, the
accepted losses and every detail live in `docs/moycore_plan_2026-08.md` §3.2
and CLAUDE.md's `moy_webserver.py` entry — not repeated here. Kept on this
board: `modules/moy_webserver.py` as a plain network substrate (listener +
request parsing + WebSocket plumbing, exercised over real localhost sockets
by `tests/test_moy_webserver.py`), waiting for the cart-sync RPC to give it
a consumer again.

## Current limitations

- This is a spike, not the production runtime.
- Display orientation and color order may need one hardware test pass.
- T-Deck keyboard behavior is still provisional.
- Watchdog reset recovery is enabled during flashed spike testing so runaway
  project code should reboot instead of hanging forever.
- Project loading falls back to a frozen Tiny Runner project; SD project loading
  is experimental and currently uses a pre-display SD prefetch path to avoid
  shared SPI conflicts with the panel.
