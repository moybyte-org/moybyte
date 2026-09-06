# Moybyte on the Guition JC8012P4A1C (ESP32-P4 + ESP32-C6, 10.1")

The fifth build target and the **second ESP32-P4 board** (brought up
2026-09-06): a 10.1" 800×1280 IPS panel (JD9365, 2-lane MIPI-DSI) with a
Silead GSL3680 capacitive touch, an ESP32-P4NRW32 (400MHz dual-core RISC-V,
32MB PSRAM, 16MB flash) and an ESP32-C6-MINI-1U for WiFi 6 / BLE over
ESP-Hosted SDIO. Mainline MicroPython v1.28.0 (`ESP32_GENERIC_P4`, C6 WiFi
variant baked into `boards/MOYBYTE_GUITION_P4`) + our native modules via
`USER_C_MODULES` — the Waveshare 7B's build, on the same silicon.

**This port is a VARIANT of the Waveshare P4's**, and what it added to the tree
is mostly what it took OUT of that board's directory: the P4-silicon C modules
became the shared `native/p4/` tier (`moy_dsi` parameterized by a panel
define, `moy_ppa`, `moy_ble_hid`, `moy_c6`), the DSI compositor became
`device/dsi_panel.py`, the PPA system canvas `device/p4_canvas.py`, and the
two P4 patches moved to `patches/p4_*.patch` behind two shared build-lib
functions. This directory owns what this glass decides: the panel define, the
backlight, the touch driver + its firmware, and the portrait system canvas.
`docs/board_ports_2026-08.md` carries the checklist this port walked.

## What this glass decided (read before touching the board)

- **The console runs PORTRAIT, 800×1280, the panel's native scan.** The glass
  is portrait tablet glass and the P4's DSI scans the PSRAM framebuffer
  continuously — there is no per-frame flush to fold a rotation into (the trick
  that made the Guition S3's 320×480 landscape free). Landscape would cost
  either rotate-at-draw (scattered-stride writes through every kernel) or a
  full-frame PPA rotate per painted frame — 2MB in, 2MB out, against a scan-out
  that already reads ~123MB/s from the same PSRAM; the Waveshare measured a
  1:1 full-screen PPA copy at ~26ms for 1.2MB, so ~45ms here per chrome
  frame. The game composite could be rotated for free (the PPA SRM scales and
  rotates in one op), chrome could not without dirty-rect plumbing the WM
  does not expose. That is a design, not a bring-up, so the board ships
  portrait and the decision is the owner's — the doc that framed this board as
  "a size/legibility testbed, not a tier" predicted exactly this.
  **Two knobs flip the image without code**: `MOY_DSI_MIRROR_XY` in
  `mpconfigboard.cmake` (1 = the factory demo's 180° image, 0 = the panel's
  raw scan) and the three touch knobs in `guition_p4_input.py`.
- **The GSL3680 is RAM-LOADED**: it has no flash, so `device/gsl3680.py`
  streams the vendor firmware (`modules/gsl_fw_jc8012.py`, 4587 records
  transcribed by `tools/gen_gsl_fw.py`) into it over I2C after every reset —
  1.34s from MicroPython at 400kHz, measured — before it reports anything.
  The bring-up sequence is the vendor's factory driver's, cross-checked
  against Linux's `silead.c`; the chip answers `0x5A5A5A5A` at register 0xB0
  once its firmware runs (glass-confirmed). Silead's GPL finger-id algorithm
  (`gsl_point_id.c`) is deliberately NOT carried: raw register 0x80 gives one
  finger's position, which is all a console pointer needs.
- **Touch axes are UNCALIBRATED.** Bring-up was hands-off (no finger on the
  glass), so `guition_p4_input.py` ships the factory demo's net mapping
  (`FLIP_X=False, FLIP_Y=True, SWAP_XY=False` against the mirrored panel) as
  the starting guess. First thing to do with a finger:
  `import moy_runtime; moy_runtime.run_touch_calibrate()` from the REPL, tap
  the five targets, set the knobs so mapped == tapped, bake them in.
- **Backlight is GPIO23, ACTIVE-HIGH** (the Waveshare's is GPIO32 active-low —
  the one fact the two boards' display modules differ on). The vendor BSP
  drives it as an LEDC PWM channel, so a duty is one line away the day
  something wants dimming; today every caller asks for on/off.
- **Serial is the P4's own USB-Serial/JTAG** (`303a:1001`, no CH343): the
  S3 boards' rules apply, not the Waveshare's — open with DTR/RTS ASSERTED,
  never pulse a reset under an open handle (`attach_only` in `board.toml`),
  and the USB id is shared with the T-Deck, the Guition S3 and the Zero, so
  the harness settles identity by asking `_ota_build.BOARD`. Flashing needs
  no BOOT button: esptool's default reset drove `chip_id`, a full 16MB
  `read_flash` and every `write_flash` first try.
- **16MB flash, not 32**: the same OTA-shaped table (2×4MB app slots, otadata
  at 0xd000) leaves ~7.9MB for the auto-built VFS; the 36-cart seed roster
  fits with room for an OTA image to stage. The build prints the app slot
  headroom; nothing else about it is current.

## Build / flash

```bash
make firmware-build-guition-p4                       # -> dist/guition_p4/moybyte_guition_p4.bin
make firmware-flash-guition-p4 PORT=/dev/ttyACM4     # esptool @0x2000, otadata erased first
make firmware-monitor-guition-p4 PORT=/dev/ttyACM4   # miniterm @115200
MOYBYTE_GUITION_P4_PORT=/dev/ttyACM4 .venv/bin/python -m pytest tests/test_guition_p4_on_glass.py -v
```

`build.sh` clones its own MicroPython v1.28.0 into `.build/`, reuses the
Waveshare's ESP-IDF v5.5.1 checkout when present (its own clone otherwise —
CI), applies the same patch ladder (the two `p4_*` patches through the shared
lib, the ESP-Hosted 2.12.12 bump, REPR_C, native-code-free, the GC split
reserve), stages the shared modules + the `native/p4` tier per `board.toml`,
and builds `BOARD_DIR=boards/MOYBYTE_GUITION_P4`. The factory image (Guition's
xiaozhi assistant) is backed up locally at
`dist/guition_p4/factory_full_16MB_backup.bin` (gitignored; the vendor's own
copy is in the demo repo below) — restore with `write_flash 0 <file>`.

## Bring-up smokes (self-terminating, from the REPL)

```python
import moybyte_shell as s; s.MODE = "panel"; s.main()   # DSI bars, quadrants, fill timing
import guition_p4_smoke as g; g.touch()                # fw upload + 15s of samples
import moy_runtime; moy_runtime.run_touch_calibrate()  # corner targets, live knobs
import moy_runtime; moy_runtime.run_ppa_smoke()        # PPA vs CPU composite A/B
```

Ctrl-C in the desktop drops to the REPL (the USB-Serial/JTAG stays alive under
the loop, like the S3 boards); the dev channel is the shared one
(`state`/`tap`/`swipe`/`run`/`py`/`bl`/`power`… — `runtime/dev_channel.py`)
plus the P4 extras `bt`/`union`/`cache`.

## What's here

- `board.toml` — the declaration: `[native.shared]` + **`[native.p4]`** (the
  silicon tier, a second native source — `tools/board_config.py
  native_sources`), the device allowlist (adds `dsi_panel`, `p4_canvas`,
  `gsl3680`; `gt911` crosses for its HeldPoint only), `[flash]`/`[serial]`
  with the USB-Serial/JTAG facts.
- `boards/MOYBYTE_GUITION_P4/` — `mpconfigboard.cmake` (the C6_WIFI fragments,
  `MOY_DSI_PANEL_JD9365=1`, `MOY_DSI_MIRROR_XY=1`), `mpconfigboard.h`,
  `sdkconfig.board` (the Waveshare's levers carried over: PSRAM 200MHz, L2
  256KB, DSI ISR in IRAM, hosted mempool in PSRAM, the WIFI_RMT set, rollback;
  16MB flash + this board's partition CSV), `partitions-moybyte-guition-p4.csv`.
- `modules/` (tracked): `boot.py`/`main.py`/`moybyte_shell.py` (the Guition
  S3's MODE-string shell), `guition_p4_display.py` (backlight + the shared
  compositor), `guition_p4_input.py` (pins, knobs, the shared GSL3680 driver),
  `gsl_fw_jc8012.py` (the touch firmware — generated, but checked in: it is a
  panel fact), `guition_p4_smoke.py`, `moy_runtime.py` (`run_desktop` — the
  Waveshare's, with this board's parts). Everything else in `modules/` is
  staged at build and gitignored.
- `native/micropython.cmake` — includes only the generated `.staged/` list;
  this board authors no C.

## Board map (factory demo sources + vendor BSP + the ESPHome community profile)

| what | where |
|---|---|
| Panel | JD9365, 800×1280 portrait, 2-lane DSI @ 1500Mbps, DPI 60MHz, hsync 20/20/40 (pw/bp/fp), vsync 4/8/20; LCD reset **GPIO27**; DSI PHY = LDO channel 3 @ 2.5V |
| Backlight | **GPIO23, active-high** (LEDC-capable) |
| Touch | GSL3680 @ **0x40** on I2C0 **SDA=7 / SCL=8** (400kHz), **RST=22, INT=21** (INT held low through reset selects the address) |
| C6 (WiFi/BLE) | ESP-Hosted SDIO: CLK18 CMD19 D0–D3 = 14–17, reset **GPIO54** — the Waveshare's wiring pin for pin |
| Audio | ES8311 codec + ES7210 mics on the touch I2C bus; I2S MCLK13 BCLK12 LRCLK10 DOUT9 DIN11; PA enable **GPIO20** (Waveshare: 53). Unwired, like the Waveshare's (#82) |
| TF card | SDMMC slot 0: CLK43 CMD44 D0–D3 = 39–42, powered from **LDO channel 4** (the vendor BSP) — the Waveshare's arrangement; unused by the console |
| Camera | MIPI-CSI socket (OV02C10 in the vendor kit); not populated on the owner's unit as far as the port knows |
| USB | USB-C straight to the P4's USB-Serial/JTAG (`303a:1001`) |
| Factory firmware | `JC8012P4A1C_I_W_Y_xiaozhi2.0.4.bin` (P4) + `JC-C6-slave_v2.3.2.bin` (C6) in the vendor's demo repo |

Sources: Guition's demo zip (mirrored at
<https://github.com/DevinWatson/10.1-inch-ESP32P4-Xiaozhi-ESP32-C6-JC8012P4A1C_I_W_Y> —
`pins_config.h`, `jd9365_lcd.cpp`, `esp_lcd_gsl3680.c/.h`, the
`esp32_p4_function_ev_board` BSP with the SD/LDO4 and codec pins, and the
schematic PNGs under `5-Schematic/`), plus the ESPHome community's board
profile (<https://community.home-assistant.io/t/jc8012p4a1-guition-esp32-p4-esp32-c6/939971>)
for the C6/audio pins, which agree with the BSP.

## Hardware-confirmed at bring-up (2026-09-06, hands-off)

- JD9365 init through the shared `moy_dsi`: **298ms**, three framebuffers,
  **0 DSI underruns** through hardware bars, a framebuffer show and a full
  desktop boot — PSRAM at 200MHz holds the 800×1280@60Hz scan-out (~123MB/s,
  more than the Waveshare's 104). A full-screen native `moy_gfx.fill` is
  ~16.6ms (2MB).
- GSL3680: firmware upload 1.34s, `0xB0 == 5A5A5A5A`, clean polling. Axes
  uncalibrated (above).
- The console: 36 carts seeded on first boot, PPA registered, Lua runtime on,
  the desktop under `WindowedWM` at 800×1280; the first frame lands ~250ms
  after the desktop is built, and the desktop is built ~27s after reset on a
  seeded store — ~10s of which is the C6 (below). **On-glass suite 18/18**
  (`tests/test_guition_p4_on_glass.py`: state, portrait canvas, GSL3680 up,
  PPA live, settings window, picker, a Python cart and a Lua cart run and
  exit, idle blank + wake, PERF lines, 0 underruns after the tour).
- **WiFi/BLE against Guition's factory C6 slave**: BLE comes up (the keyboard
  scanner runs from the first frame), but ESP-Hosted 2.12.12's
  `Req_FeatureControl` RPC times out TWICE at boot (`rpc_core: Timeout waiting
  for Resp for [0x183]`, 5s each) before the WLAN service gives up — the
  factory slave (`JC-C6-slave_v2.3.2.bin`) predates that RPC. The console
  degrades exactly as designed (no WiFi row lit, `wifi=[False, None, None]`),
  and the fix is the same as the ESP-NOW one: flash the Waveshare's
  `c6_slave/` image (hosted 2.12.12 + the shim) to this C6. Until then a boot
  costs those 10s.

## Open items (what a human with a finger and a desk decides)

1. **Touch calibration** — `run_touch_calibrate()`, five taps, bake the knobs.
2. **Orientation** — portrait is the zero-cost answer; whether the desk wants
   landscape is the owner's call, and the numbers above are the bill.
3. **The C6 runs Guition's factory slave** (no ESP-NOW shim): `moy_espnow`
   fails into an inactive link by design. The Waveshare's `c6_slave/` image is
   the same chip and the same SDIO; flashing it here is Phase D of
   `docs/history/espnow_p4_2026-08.md` on this board, and the publisher does
   not stage a `c6` block in this board's manifest yet.
4. WiFi against the factory slave — two 5s RPC timeouts per boot and no WLAN
   (the section above); the shimmed slave image is the fix for 3 and 4 at once.
5. BLE keyboard (unverified: nothing paired), audio (#82, same codec as the
   Waveshare), the TF slot (LDO4, a future removable-cart workflow),
   backlight PWM (one line in `guition_p4_display.py` when wanted).
