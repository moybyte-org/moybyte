# KidCode MicroPython-First T-Deck Spike

This directory is a parallel firmware spike for the LilyGO T-Deck Plus.

It does not replace the existing Arduino smoke-test firmware in
`firmware/lilygo_t_deck_plus`. The goal is to produce a launcher-friendly `.bin`
that can be copied to SD and started from the user's existing launcher, without
using USB flashing for the first test.

## Goal

Answer one question:

```text
Can a MicroPython + LVGL KidCode shell on the T-Deck Plus run Tiny Runner with
acceptable display, input, reset recovery, and memory behavior?
```

## Architecture

```text
lvgl_micropython firmware
  frozen boot.py/main.py
  frozen kidcode Python package
  frozen Tiny Runner project
  T-Deck display/input bootstrap
```

There is one MicroPython VM. The shell loads a child project into a controlled
namespace, then calls `update(dt)` and `draw()` each frame.

## Build

The build script fetches `lvgl_micropython` into `.build/`, freezes the local
KidCode spike modules, and copies the SD-launcher app image into `dist/`.

```bash
make firmware-build-lilygo-micropython
```

The wrapper intentionally throttles heavy builds by default:

- `KIDCODE_BUILD_JOBS=2`
- `KIDCODE_BUILD_NICE=15`
- idle IO priority through `ionice` when available

For a gentler build while using the machine for other work:

```bash
KIDCODE_BUILD_JOBS=1 make firmware-build-lilygo-micropython
```

Expected output:

```text
firmware/lilygo_t_deck_plus_micropython/dist/kidcode_micropython_tdeck.bin
```

Named hardware-test builds can be produced by setting `KIDCODE_ARTIFACT_NAME`.
The wrapper also supports two board bases:

- `KIDCODE_BOARD_CONFIG=generic`: generic `ESP32_GENERIC_S3` plus ST7789.
- `KIDCODE_BOARD_CONFIG=tdeck`: upstream `lvgl_micropython` `LilyGo-TDeck`
  custom board config with its T-Deck display/input wiring.

The T-Deck custom board path needs upstream TOML parsing, so the wrapper uses
`.venv/bin/python` when it exists. `KIDCODE_BUILD_PYTHON` can override this.

Current planned test builds:

```bash
KIDCODE_BUILD_JOBS=1 KIDCODE_ARTIFACT_NAME=kidcode_generic_cdc_uart KIDCODE_BOARD_CONFIG=generic KIDCODE_REPL=cdc_uart make firmware-build-lilygo-micropython
KIDCODE_BUILD_JOBS=1 KIDCODE_ARTIFACT_NAME=kidcode_generic_jtag_repl KIDCODE_BOARD_CONFIG=generic KIDCODE_REPL=jtag make firmware-build-lilygo-micropython
KIDCODE_BUILD_JOBS=1 KIDCODE_ARTIFACT_NAME=kidcode_lvgl_tdeck_board KIDCODE_BOARD_CONFIG=tdeck make firmware-build-lilygo-micropython
KIDCODE_BUILD_JOBS=1 KIDCODE_ARTIFACT_NAME=kidcode_lvgl_tdeck_board_jtag KIDCODE_BOARD_CONFIG=tdeck KIDCODE_REPL=jtag make firmware-build-lilygo-micropython
```

Each build emits an SD-launcher app image and two merged full-flash images:

```text
dist/<name>.bin
dist/<name>_full_dio_0x0.bin
dist/<name>_full_qio_0x0.bin
```

## Host development

The v0.4 console runs on the PC from the **same shared code** this firmware
freezes — see `tools/simulate_desktop.py` (it renders the same launcher / carts /
code+paint editors). The old fake-LVGL `.kcproj` simulator
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
  `kidcode_*_jtag*` builds are comparison images for that console path; they do
  not port Tulip's native display task.

An early native init patch exists as an experiment so GPIO10 stays high and
shared SPI chip-selects stay deselected before frozen Python starts. It is
disabled by default after producing a launcher black-screen build on this unit;
set `KIDCODE_EARLY_BOARD_INIT=1` only when testing that path deliberately.
Longer term, the Tulip-style native framebuffer/canvas path is likely a better
fit for KidCode games than per-frame LVGL object updates.

For the SD launcher, use `kidcode_micropython_tdeck.bin`. It is the ESP32 app
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
- `10`: KidCode action `a`
- `20`: KidCode action `b`

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

- `/sd/kidcode/project.py`
- `/sd/kidcode/main.py`
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
`firmware/lilygo_t_deck_plus_micropython/dist/current/kidcode-current-app.bin`.
The build script refreshes this alias on every successful MicroPython build, so
the flash target does not accidentally use a stale experimental `micropython.bin`
from the build directory.

Merged full image flash:

```bash
make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0
```

This writes `MPY_FULL_BIN` at `0x0`, defaulting to
`firmware/lilygo_t_deck_plus_micropython/dist/current/kidcode-current-full-dio-0x0.bin`.
Use this for test images named `*_full_dio_0x0.bin` or `*_full_qio_0x0.bin`; it
is closest to the official LilyGO prebuilt firmware flow.

Example:

```bash
make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0 MPY_FULL_BIN=firmware/lilygo_t_deck_plus_micropython/dist/kidcode_lvgl_tdeck_board_jtag_full_dio_0x0.bin
```

If a full MicroPython image black-screens after a different firmware or
partition layout was on the board, flash erase is still available as a
MicroPython-specific diagnostic. Arduino HelloWorld can boot without erase, so
this is not a general flashing requirement. The reason it can still matter for
MicroPython is that its frozen `_boot.py` mounts the `vfs` data partition before
the user's `boot.py`; stale non-MicroPython filesystem data can stop execution
before KidCode code runs.

```bash
make firmware-flash-lilygo-micropython-full-erase PORT=/dev/ttyACM0 MPY_FULL_BIN=firmware/lilygo_t_deck_plus_micropython/dist/kidcode_cold_gpio_generic_cdc_uart_full_dio_0x0.bin
```

To test that path without erasing flash, build with MicroPython's automatic VFS
mount disabled:

```bash
KIDCODE_BUILD_JOBS=1 KIDCODE_ARTIFACT_NAME=kidcode_diag_skip_vfs_generic_cdc_uart KIDCODE_BOARD_CONFIG=generic KIDCODE_REPL=cdc_uart KIDCODE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython
```

Known-good hardware result:

```text
firmware/lilygo_t_deck_plus_micropython/dist/kidcode_diag_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

This image has booted into KidCode on the LilyGO T-Deck Plus from direct full
flash. It is still a diagnostic baseline because it skips MicroPython's normal
`/` filesystem mount; frozen KidCode modules work, but features that depend on
MicroPython's internal writable filesystem need a proper VFS fix.

SD Project test image:

```text
firmware/lilygo_t_deck_plus_micropython/dist/kidcode_sd_slot_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

This keeps the known-good skip-VFS boot/display path, enables only the explicit
SD Project selector slot, and starts the watchdog before selector/project
loading. Put a project at `/kidcode/project.py` on the SD card, then choose
`SD Project` in the selector. The checked-in sample lives at
`firmware/lilygo_t_deck_plus_micropython/sdcard/kidcode/project.py`.

The current fixed SD source-loader image is:

```text
firmware/lilygo_t_deck_plus_micropython/dist/kidcode_sd_prefetch_skip_vfs_generic_cdc_uart_full_dio_0x0.bin
```

The stable alias for the same latest DIO full-flash image is:

```text
firmware/lilygo_t_deck_plus_micropython/dist/current/kidcode-current-full-dio-0x0.bin
```

It keeps the same SD test scope, preloads KidCode API names for SD projects,
neutralizes a leading `from kidcode import *` line for compatibility, and reads
the SD project before display/LVGL init. After the SD source is cached, it
unmounts/releases the card and loads the cached project from the selector. This
is meant to avoid stale display updates caused by SD access on the shared SPI bus
after the panel is already running.

Expected prefetch breadcrumbs:

- Serial before the selector: `KidCode SD prefetched /sd/kidcode/project.py bytes ...`
- Screen after choosing SD Project: `cached SD project`, then `loaded SD project`

If the screen still says `mounting SD`, the prefetch path did not find the file
before display init and the firmware fell back to the older after-display SD
read path. Check serial for `KidCode SD prefetch failed` or
`KidCode SD prefetch found no project`.

### Live SD reads/writes while the panel is running (`kc_sd`)

The boot prefetch above mounts SD with `machine.SDCard`, which works only
*before* `init_display()` because it re-runs `spi_bus_initialize()` on the host
the panel later claims. For SD access *after* the panel is live (cart saves,
re-scans, create/duplicate/delete in the workstation) the firmware uses the
native `kc_sd` module (`native/kc_sd/modkc_sd.c`).

`kc_sd` follows the ESP-IDF "Sharing the SPI Bus Among SD Cards and Other SPI
Devices" pattern: it does **not** re-initialize the bus. `esp_lcd` already ran
`spi_bus_initialize()`, so `kc_sd` only `sdspi_host_init_device()`s the card as a
second device on that same host and probes it. The panel device is left attached,
so the display keeps working afterward. `kidcode_sd.with_sd_live(fn)` mounts the
card (FAT via a `kc_sd`-backed block device) **once and keeps it resident**, then
runs `fn`. The desktop loop is single-threaded with LVGL's task handler stopped
(native takeover), so an SD session runs strictly between frames and never
collides with a `tx_color` flush. This is why `Workstation.can_manage` is now
enabled on device (`run_desktop` wires `_with_sd = kidcode_sd.with_sd_live`).

**Do not tear the SD device down between ops.** The first cut unmounted +
`sdspi_host_deinit`'d after every write and also forced `TFT_CS` high via
`Pin(...)`; both corrupt the shared bus/DMA state, and the *next* panel flush
**silent-hangs** the board — the write lands on SD, then resume freezes with no
panic and USB still enumerated but dead (confirmed over serial: nothing after
`KidCode desktop running`). Keep the card mounted, leave `TFT_CS`/`SD_CS` to their
drivers, and only park the unused LoRa `RADIO_CS` high.

Recommended full-flash order for the next hardware pass:

1. `kidcode_lvgl_tdeck_board_jtag_full_dio_0x0.bin`: custom T-Deck board config
   plus USB Serial/JTAG console comparison.
2. `kidcode_lvgl_tdeck_board_full_dio_0x0.bin`: custom T-Deck board config with
   normal CDC-style MicroPython USB.
3. `kidcode_generic_jtag_repl_full_dio_0x0.bin`: generic S3 display path plus
   USB Serial/JTAG console.
4. `kidcode_generic_cdc_uart_full_dio_0x0.bin`: generic cleaned build closest to
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

## Current limitations

- This is a spike, not the production runtime.
- Display orientation and color order may need one hardware test pass.
- T-Deck keyboard behavior is still provisional.
- Watchdog reset recovery is enabled during flashed spike testing so runaway
  project code should reboot instead of hanging forever.
- Project loading falls back to a frozen Tiny Runner project; SD project loading
  is experimental and currently uses a pre-display SD prefetch path to avoid
  shared SPI conflicts with the panel.
