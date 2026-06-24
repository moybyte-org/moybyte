# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

KidCode is a PC-first SDK + simulator for a future ESP32 kids' coding console, plus the firmware that runs it on real hardware (LilyGO T-Deck Plus). There are **two parallel project systems** living side by side — knowing which one you're touching is the single most important orientation fact:

1. **The `.kcproj` SDK (original, mature).** A kid writes a project; it runs on a PC sim and exports to firmware.
   - `kidcode/` — the runtime/API a project calls (`api`, `screen`, `sprites`, `input`, `audio`, `radio`, `manifest`, `permissions`).
   - `kidcode_cli/` — the `kidcode` console command (`run`, `new`, `pack`, `check-portable`, `export-device`, firmware header gen, board defs).
   - `kidcode_sim/` — sim backends (`pygame_backend`, `headless_backend`, fake audio/radio).
   - `kidcode_blocks/` — block-language → Python compiler.
   - Projects (`examples/*.kcproj`) must stay inside a **portable subset** enforced by `kidcode_cli/portable.py`: only `kidcode`/`math`/`random` imports, and no `eval`/`exec`/`open`/`getattr`/etc. `make check-portable` is the gate.

2. **The v0.4 `.kcart` console (newer, active direction).** A TIC-80-style "fantasy workstation" where *everything is a cartridge*. This is where current feature work happens.
   - `runtime/` — the **host reference** of the console (launcher → cartridge → cards editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
   - `firmware/lilygo_t_deck_plus_micropython/` — the **device port** of that same console (MicroPython).
   - `system_carts/*.kcart` — seed cartridges (folder = `manifest.json` + `main.py` + `config.json`).

The two systems share a design intent but **not code**. `.kcproj` is the old format; `.kcart` is the v0.4 format.

### The v0.4 portability contract (why the canvas is "indexed")

The v0.4 canvas works in **palette indices** (the `KID64` palette) with a plain-function drawing API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`) — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.kcart` is meant to run on the host (`runtime/canvas.py`) and on the device (`kc_compositor`, indices → RGB565), and eventually a Lua VM. When adding drawing features, add them to **both** backends and keep the API identical.

## Common commands

```bash
make setup          # python -m venv --system-site-packages + pip install -e '.[dev]'
make test           # pytest (all). The venv python is .venv/bin/python
make doctor         # environment sanity check via the kidcode CLI

# run a single test
.venv/bin/python -m pytest tests/test_v04_userland.py -k cards
.venv/bin/python -m pytest tests/test_micropython_spike.py::test_name
```

`.kcproj` SDK loop:
```bash
.venv/bin/kidcode run examples/tiny_runner.kcproj --headless --frames 60
.venv/bin/kidcode run examples/tiny_runner.kcproj --fps 30 --scale 4   # pygame window
make check-portable                                                     # portable-subset gate
```

v0.4 `.kcart` console (host):
```bash
python tools/simulate_desktop.py                                  # boots the launcher (needs a display)
python tools/simulate_desktop.py --cart system_carts/star_catcher.kcart
python tools/simulate_desktop.py --demo --gif demo.gif            # headless tour
```

## Firmware (LilyGO T-Deck Plus, MicroPython)

This is the active hardware target. Build → flash → monitor:

```bash
KIDCODE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython     # outputs to dist/current/
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0           # esptool, default_reset
make firmware-monitor-lilygo-micropython PORT=/dev/ttyACM0         # miniterm @115200
```

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/kc_gfx`, `kc_alloc`, and `kc_sd`) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone), freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- There is an older Arduino/PlatformIO firmware in `firmware/lilygo_t_deck_plus/` (`make firmware-build-lilygo`, `firmware-smoke-lilygo`). It's a serial smoke test, not the console; don't confuse the two.

### Host == device: the shared console (important)

The v0.4 console UI is now **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree** so the device freezes the identical code (same pattern,
re-staged every build, gitignored):

- `runtime/console.py` — `Launcher` + `Pointer` + `Workstation` + the cards / code /
  paint UI + layout. Backend-agnostic: injected `make_api` + cart store. (frozen as `console`)
- `runtime/editors.py` — `CodeEditor` / `SpriteSheet` / `PaintEditor` cores. (frozen as `editors`)
- `runtime/kid_carts.py` — the `.kcart` store (scan/load/save_*/create/duplicate/delete; only `json`+`os`). (frozen as `kid_carts`)
- `runtime/font.py` — petme128 8×8 font (host only; the device uses framebuf's copy).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()`, `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

The pre-unification host UI (`shell.py`, `workstation.py`, `engine.py`, `api.py`,
`cartridge.py`) is **legacy/superseded** by the shared console (kept only for its tests).

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `kidcode_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_FULLSCREEN_BENCH` / `RUN_COMPOSITOR_SMOKE` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE`; SD prefetch; native takeover.
- `kid_runtime.py` — the **device backend**: `DeviceCanvas` (framebuf over the compositor), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`kid_carts` and injects the device `make_api` + store into `console.Workstation`.
- `console.py` / `editors.py` / `kid_carts.py` — **staged from `runtime/` at build** (see above).
- `kidcode_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `kc_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** The boot prefetch (`kidcode_shell._prefetch_carts`) still reads carts via `machine.SDCard` **before `init_display()`** — that path re-runs `spi_bus_initialize()`, so calling it **after the panel is live hard-hangs the board** (gray screen, dead USB): `esp_lcd` and `machine.SDCard` are two driver stacks fighting over one host; CS-deselect alone is not enough. **Live reads/writes (post-display) go through the native `kc_sd` module** (`native/kc_sd/modkc_sd.c`), which *attaches* the card to the host `esp_lcd` already initialized (`sdspi_host_init_device`, no bus re-init — the ESP-IDF "Sharing the SPI Bus" pattern) and leaves the panel device intact. `kidcode_sd.with_sd_live(fn)` mounts via `kc_sd` **once and keeps the card resident** for the session, then just runs `fn`. **Do not tear the SD device down between ops** (learned the painful way): a per-op `sdspi_host_deinit` — or reconfiguring the panel's `TFT_CS` via `Pin(...)` — corrupts the shared bus/DMA state and the *next panel flush silent-hangs the board* (the write itself lands on SD, then resume freezes; no panic, USB stays enumerated but dead). So leave `TFT_CS`/`SD_CS` alone (driver-owned; only park the unused LoRa `RADIO_CS` high) and never flush the panel inside the session — the desktop loop is single-threaded, so SD ops run between frames. On-device writes are enabled (`Workstation.can_manage`, wired to `with_sd_live` in `run_desktop`).
- **The `run_desktop` native-takeover loop starves USB.** Once `KidCode desktop running` prints, there is **no serial / REPL / esptool reset** — the loop never services USB. Serial only flows during the ~2s boot. To capture boot logs, passively read `/dev/ttyACM*` (with reconnect, since native USB re-enumerates) **while physically pressing reset**.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard returns clean 1-byte ASCII — never enable its "raw" mode.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3`). It natively returns ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard). The `0x03` "raw matrix" command (`LILYGO_KB_MODE_RAW_CMD`) switches it to a 5-byte mode that only decoded a fixed WASD/ZX subset and **garbled the code editor** — and flipping a flag can't undo it (send `0x04`, `..._MODE_KEY_CMD`, to revert). So `TDeckKeyboard.__init__` does **not** enable raw mode; `poll()` uses the 1-byte path and `_buttons_for_key` maps letters to nav/game buttons (with a hold latch). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`KidCode_Console_Plan_v0_4.md`** (repo root); superseded v0.1/v0.3 docs are archived under `docs/history/`. API/format specs (the `.kcproj` SDK) live in `docs/`.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
