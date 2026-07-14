# Moybyte on ESP32-P4 (Waveshare ESP32-P4-WIFI6-Touch-LCD-7B)

The second device backend (#58): a 7" 1024×600 MIPI-DSI "desktop workstation"
tier next to the T-Deck pocket handheld. **Not** an lvgl_micropython build —
this is mainline MicroPython v1.28.0 (`ESP32_GENERIC_P4`, C6 WiFi variant baked
into our out-of-tree board def) + our native modules via `USER_C_MODULES`.

Status (2026-07-09): REPL / WiFi-via-C6 / GT911 touch / SD / DSI panel / **the
console on glass** all hardware-confirmed. Launcher runs under `WindowedWM`;
colors (canonical RGB565 vs the T-Deck's byte-swapped wire order → `PAL565_WIRE`),
flicker (DPI `num_fbs=2` ping-pong scan-out), touch (180° panel mount →
`p4_input.FLIP_X/Y`), popup/wallpaper geometry all fixed on-glass. Play perf
comes from three levers: the quiet-frame partial repaint
(`WindowedWM.draw_stack`), the hardware-PPA game composite, and the
async-composite overlap. Battle City 35→51→56; most carts ~60fps. The
`_BackdropLayer` retained backdrop cache gives ~15fps app-window drags. See #58
for the living status.

**BLE-HID keyboard support is hardware-paired (2026-07-13; latency fast path
2026-07-14).** `p4_ble_keyboard.py` uses the C6_WIFI build's existing
MicroPython NimBLE central/GATT-client path over ESP-Hosted SDIO: it discovers
HOGP service `0x1812`, bonds, prefers the profile's deterministic Boot Host path
(writes Protocol Mode `0x00`, then subscribes only to Boot Keyboard Input), and
feeds real make/break state + ASCII into the shared `InputState`. Put a **BLE**
keyboard in pairing mode before boot; serial shows `scanning → connected → boot
protocol → native input queue → ready`. Steady-state notifications bypass the
ESP32 port's synchronous Python BLE IRQ/GIL path: the P4-only `moy_ble_hid`
module copies registered HID reports immediately on the NimBLE host task into a
64-entry native queue, then `keyboard.poll()` drains it before the frame's input
edge snapshot. `bt status` reports `(received, dropped, queued, max_depth,
enabled)` for that queue; `bt trace 1` adds host-queue age in microseconds to
each decoded report. Fast make+break pairs are preserved across the frame
boundary instead of losing the tap. Report-only keyboards remain on a traced
standard-report fallback; arbitrary/NKRO Report Maps, Classic-Bluetooth-only
keyboards, mouse, media keys and gamepads are not supported. USB-HID remains
#83's wired/multi-device path.

**The hardware PPA (Pixel-Processing Accelerator) is wired for the game
composite** (`moy_ppa`, ESP-IDF `esp_driver_ppa` SRM client, patched into
IDF_COMPONENTS like `esp_lcd`; `P4SystemCanvas.blit_game` uses it, CPU fallback).
Colors verified pixel-identical via framebuffer readback. Two findings on
record:
- **The PPA only wins on UPSCALE composites.** The game→window scale is 2.6×
  (12.95→4.98ms; tiny source read + hardware scale). A full-screen 1:1 copy (the
  drag backdrop restore) is ~identical CPU vs PPA (~26ms, PSRAM-bandwidth-bound
  vs the DSI scan-out), and **sprite draws lose ~10× to `spr_batch`** even queued
  non-blocking (64× 16×16 = 4.57ms PPA vs 0.70ms CPU; per-op submit dwarfs a tiny
  blit). So both copies and sprites stay on the CPU — the PPA is scale-only.
- **Async-composite overlap** (`blit_async` + `moy_ppa.sync` fence + a done-ISR
  counter): a quiet game frame defers the scan-out switch to the next loop
  (`P4Compositor.present_pending`), overlapping the PPA DMA with the input poll.
  +2–5fps; full paints stay blocking (`blit_game(defer=not full)`) so chrome
  never races the DMA.

`moy_runtime.run_ppa_smoke()` A/Bs the composite on glass. Perf follow-ups: the
RENDER overlap (double game canvas + triple framebuffer) to lock 60 on the
heaviest carts (Battle City 56, Letter Blitz 53); a PPA cover-crop for drags.
Also open: wired USB-HID keyboard/mouse/gamepad, audio (ES8311), OTA/web-view
wiring.

### Serial dev commands (the REPL-alive board's affordance)

`run_desktop`'s loop reads whole lines from `sys.stdin` (the CH343 never
starves under the desktop, unlike the T-Deck), so a host script can drive the
UI while watching the glass:

- `tap <x> <y>` / `tap sysmenu` — synthetic tap at system coords / a named bar button
- `open settings|picker` — pop an app window deterministically
- `run <name>` — select the first cart whose title matches and RUN it
- `drag [frames] [step]` — grab the top window's title strip and oscillate it (step = px/frame amplitude scale, default 6; 30 ≈ a violent finger drag)
- `cache 0|1` — A/B the drag backdrop cache
- `union 0|1` — A/B the dirty-union gesture restore (window-sized backdrop re-stamp vs full-screen)
- `skip 0|1` — A/B the #77 frameskip (logic full-rate, render halved; non-persisting)
- `bt status|scan|forget` — inspect/restart BLE-keyboard discovery or clear its local bond keys (`fast=(rx, drops, queued, peak, enabled)`)
- `bt trace 0|1` — print raw HID notification bytes, native queue age, and decoded held input state
- `quit` — leave the desktop for the REPL

`moy_runtime.run_touch_calibrate()` (REPL-invokable) draws corner targets and
dumps raw/mapped GT911 samples for re-calibrating the `p4_input` knobs.

## Build / flash

```bash
make firmware-build-p4                             # -> dist/p4/moybyte_p4.bin
make firmware-flash-p4 PORT=/dev/ttyACM0           # esptool @0x2000 (the P4 app offset)
make firmware-monitor-p4 PORT=/dev/ttyACM0         # miniterm @115200
```

(Raw equivalent: `.venv/bin/python -m esptool --chip esp32p4 --port /dev/ttyACM0
--baud 921600 write_flash 0x2000 dist/p4/moybyte_p4.bin`.)

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
  - `p4_ble_keyboard.py` — pure-MicroPython BLE HID central over the hosted C6:
    scan/pair/bond/discover/subscribe plus standard keyboard-report →
    `InputState`/`last_key` mapping. Bond keys persist in
    `/moy/ble_keyboard.json`; radio or protocol failures degrade to touch-only.
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
- **Bluetooth is C6-hosted BLE, not Classic Bluetooth.** The board definition
  already enables MicroPython Bluetooth central/GATT client + pairing/bonding
  (`CONFIG_ESP_HOSTED_NIMBLE_HCI_VHCI`); HCI shares the C6's SDIO transport with
  WiFi. Only BLE/HOGP keyboards can work — a Bluetooth 3.0/BR-EDR-only keyboard
  will never appear in the scan. Discovery retries with a 5-second idle gap so
  an absent keyboard does not keep the shared radio in a continuous scan. Keep
  `CONFIG_BT_NIMBLE_TRANSPORT_ACL_FROM_LL_COUNT=64` in `sdkconfig.board`: the
  upstream 24-packet host pool was hardware-confirmed to exhaust during keyboard
  autorepeat while a synchronous MicroPython BLE IRQ waited behind a long render
  (`vhci_drv: Rx: alloc_acl_from_ll failed`), dropping HID input reports. The
  larger ACL pool is burst protection, not the latency solution: registered HID
  notifications are intercepted by `moy_ble_hid_queue_on_notify` before Python
  IRQ dispatch and drained by the frame loop. Pairing/bonding/discovery still use
  the normal synchronous MicroPython path.
- **USER_C_MODULES cannot add IDF components** — the usermod cmake is skipped
  during idf.py's early-expansion phase, which is when component `REQUIRES`
  are collected. `build.sh` patches `esp32_common.cmake`'s `IDF_COMPONENTS`
  list instead (idempotent sed).
- **A root-level VFS dir named like a frozen module SHADOWS it** (`''` precedes
  `.frozen` on `sys.path`): the first console boot seeded `/moybyte/carts` and
  the next boot died with `ImportError: no module named 'moybyte.input'`. The
  flash store root is therefore **`/moy/carts`** — never name a VFS root dir
  after an importable module.
- **The PPA driver INVALIDATES the whole out-picture buffer at submit** — any
  CPU frame writes not yet flushed from cache are silently DISCARDED (pixels
  revert to stale PSRAM content; glass-confirmed as speed-scaled desktop
  droppings during drags). `moy_ppa.srm_blit` therefore does a C2M writeback of
  the dst buffer before every submit, and an async op must still be the frame's
  LAST framebuffer write (the WM registers the drag stamp; `P4Compositor.flush`
  kicks it after the bar/chips/cursor have drawn).

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
