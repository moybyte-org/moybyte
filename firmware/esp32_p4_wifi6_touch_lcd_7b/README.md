# Moybyte on ESP32-P4 (Waveshare ESP32-P4-WIFI6-Touch-LCD-7B)

The second device backend (#58): a 7" 1024×600 MIPI-DSI "desktop workstation"
tier next to the T-Deck pocket handheld. **Not** an lvgl_micropython build —
this is mainline MicroPython v1.28.0 (`ESP32_GENERIC_P4`, C6 WiFi variant baked
into our out-of-tree board def) + our native modules via `USER_C_MODULES`.

Status (2026-07-08): REPL / WiFi-via-C6 / GT911 touch / SD / **DSI panel first
light** all hardware-confirmed. The **console is staged and boots** — launcher
under `WindowedWM`, `moy_gfx`/`moy_alloc` re-homed to `USER_C_MODULES`, 15
carts seeded on the internal-flash VFS, error-free frame loop, Ctrl-C drops to
the REPL (serial-verified; the first eyes-on-glass pass — visuals + touch
orientation — is still pending). See #58 for the living status.

## Build / flash

```bash
firmware/esp32_p4_wifi6_touch_lcd_7b/build.sh     # -> dist/p4/moybyte_p4.bin
.venv/bin/python -m esptool --port /dev/ttyACM0 --baud 921600 \
    write_flash 0x2000 dist/p4/moybyte_p4.bin      # P4 flashes at 0x2000
```

- `build.sh` clones MicroPython v1.28.0 into `.build/`, reuses the T-Deck
  build's ESP-IDF v5.5.1 checkout when present (clones its own otherwise),
  patches `esp_lcd` into the port's `IDF_COMPONENTS` (see below), and builds
  with `BOARD_DIR=boards/MOYBYTE_P4`.
- Serial is the CH343 USB-UART bridge (`/dev/ttyACM0`); `mpremote` works, and
  unlike the T-Deck there is no native-takeover USB starvation — the REPL stays
  available.

## What's here

- `boards/MOYBYTE_P4/` — out-of-tree board def: the `C6_WIFI` variant's
  sdkconfig fragments + `sdkconfig.board` (PSRAM @ 200MHz, 32MB flash, the
  custom partition table) + `partitions-moybyte-p4.csv` (OTA-shaped 2×4MB app
  slots — the default 4MiBplus table's ~1.94MB app can't hold the frozen
  console — with the ~24MB tail left unlisted so mainline auto-builds the vfs
  over it).
- `native/moy_dsi/` — the panel module: vendored `esp_lcd_ek79007` v2.0.2
  (Apache-2.0, ESP component registry) + `modmoy_dsi.c` exposing
  `init() / fb() / flush() / set_pattern() / deinit()` and `WIDTH/HEIGHT`.
  DPI mode: the DSI peripheral **continuously scans a PSRAM framebuffer** —
  there is no per-frame flush transfer (the T-Deck's ~28ms tx_color ceiling
  does not exist on this board); `flush()` is only a CPU-cache msync so the
  scan-out DMA sees writes. Native `framebuf` over `fb()` does a full-screen
  redraw in ~29ms; MicroPython memoryview slice writes are the usual
  interpreted tax (~10s/screen) — real rendering goes through native code.
- `native/micropython.cmake` — the `USER_C_MODULES` entry point: `moy_dsi` +
  the shared `moy_gfx`/`moy_alloc` staged from the T-Deck tree into
  `native/.staged/` by build.sh (single source of truth stays in
  `firmware/lilygo_t_deck_plus_micropython/native/`; both are plain-C usermods
  whose S3-only pieces are include-guarded, so they compile unchanged on the
  P4's RISC-V). `moy_gfx` grew `blit565_scale` for this port — the ONE-call
  integer-upscale composite the windowed presentation needs.
- `modules/` — the P4-authored device backend (tracked) + build-staged copies
  (gitignored; see `.gitignore`'s whitelist):
  - `moybyte_shell.py` — boot entry (`main()`); `RUN_PANEL_SMOKE` flips to the
    DSI hardware test pattern. Ctrl-C in the desktop loop drops to the REPL
    (no native-takeover USB starvation on this board).
  - `p4_display.py` — `P4Compositor`: the compositor shim over `moy_dsi`
    (size/framebuffer/back_buffer/gfx/flush/sync; single-buffered, flush =
    cache msync) + the active-low GPIO32 backlight (held dark until the first
    composed frame).
  - `p4_input.py` — GT911 polling driver (I2C0 SDA7/SCL8 @ 0x5D, native
    1024×600 coords; `FLIP_X`/`FLIP_Y` knobs for the 180° panel mount if touch
    lands mirrored).
  - `moy_runtime.py` — the P4 backend: `P4SystemCanvas` (a `DeviceCanvas` over
    the DSI framebuffer + the system-surface contract: `font_scale` text via
    the native text kernel, font-scale window layers, and the `blit_game` /
    `blit_cover` native composite hooks `wm_windowed`/`wallpaper` probe for)
    and `run_desktop()` — constructs the shared `Workstation` with a distinct
    1024×600 system canvas + the fixed 320×240 off-screen game canvas and
    installs **`WindowedWM`** (#73's tier, on its intended hardware). Carts
    live on the internal-flash VFS (`/moybyte/carts`); SD is optional here.
  - Staged at build (canonical sources elsewhere): the whole shared console
    from `runtime/` (incl. `wm_windowed.py`, which is deliberately NOT staged
    to the S3 build), `device_canvas`/`device_api`/`device_wifi`/`device_util`
    + the `moybyte` input package from the T-Deck modules tree, and the
    generated `carts_data.py`.

## Hard board constraints (hardware-confirmed; don't re-learn these)

- **PSRAM must run at 200MHz** (`CONFIG_SPIRAM_SPEED_200M` +
  `CONFIG_IDF_EXPERIMENTAL_FEATURES`, set in `sdkconfig.board`). At the
  default speed the 1024×600@60Hz scan-out (~104MB/s) underruns
  ("can't fetch data from external memory fast enough").
- **The SD slot is powered from the P4's internal LDO channel 4** — stock
  MicroPython never enables it, so `machine.SDCard` times out card-or-no-card.
  Until the board-init owns this, the pure-Python poke (verified):
  `mem32[0x501151D8] |= (1<<7)|(1<<14)` then `|= (1<<8)` (PMU_EXT_LDO_P1_0P2A:
  SW-own, tie to 3.3V rail, power on). SD then works:
  `SDCard(slot=0, width=4, sck=43, cmd=44, data=(39,40,41,42))`.
- **SDMMC slot 1 belongs to the C6** (ESP-Hosted WiFi transport, pins
  CLK18/CMD19/D0-3=14-17/reset 54). Constructing `machine.SDCard(slot=1)`
  panics the board. SD card = slot 0, C6 = slot 1, panel = DSI — three
  separate buses, so the T-Deck's #56 SD↔display war does not exist here.
- **WiFi needs NO C6 flash**: the factory ESP-Hosted slave firmware on the C6
  is compatible with v1.28's hosted host and the wiring matches the Espressif
  Function EV board the stock config targets.
- **USER_C_MODULES cannot add IDF components** — the usermod cmake is skipped
  during idf.py's early-expansion phase, which is when component `REQUIRES`
  are collected. `build.sh` patches `esp32_common.cmake`'s `IDF_COMPONENTS`
  list instead (idempotent sed).
- **A root-level VFS dir named like a frozen module SHADOWS it** (`''` precedes
  `.frozen` on `sys.path`): the first console boot seeded `/moybyte/carts` and
  the next boot died with `ImportError: no module named 'moybyte.input'`. The
  flash store root is therefore **`/moy/carts`** — never name a VFS root dir
  after an importable module.

## Board map (from the factory xiaozhi boot log + Waveshare/xiaozhi sources)

- Panel: **EK79007**, 2-lane MIPI-DSI @ 900Mbps, 1024×600 RGB565, DPI clock
  52MHz; LCD reset GPIO33; **panel mounted 180° rotated**; DSI PHY power =
  LDO chan 3 @ 2500mV; **backlight GPIO32, active-low**.
- Touch: **GT911** (same chip as the T-Deck) on I2C **SDA=7 / SCL=8** @ 0x5D,
  INT/RST not wired; self-configured for 1024×600; plain status-register
  polling works.
- Audio: ES8311 codec (0x18) + ES7210 quad-mic (0x40) on the same I2C bus;
  I2S MCLK13/WS10/BCLK12/DIN11/DOUT9; PA enable GPIO53.
- Boot button GPIO35. No camera fitted (empty MIPI-CSI socket).
- Factory firmware (xiaozhi AI assistant) backup: `dist/p4/factory_full_32MB_backup.bin`
  (local only, gitignored) — restore with `write_flash 0 <file>`.
