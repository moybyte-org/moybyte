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

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/kc_gfx`, plus `kc_alloc`) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone), freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- There is an older Arduino/PlatformIO firmware in `firmware/lilygo_t_deck_plus/` (`make firmware-build-lilygo`, `firmware-smoke-lilygo`). It's a serial smoke test, not the console; don't confuse the two.

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `kidcode_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_FULLSCREEN_BENCH` / `RUN_COMPOSITOR_SMOKE`; SD prefetch; native takeover.
- `kid_runtime.py` — the device console: `DeviceCanvas`, `Workstation` (launcher + cards editor + trackball-as-pointer), `run_desktop()` loop. Mirrors `runtime/` on a different canvas backend.
- `kid_carts.py` — SD `.kcart` store (seed/scan/load/save/create/duplicate/delete).
- `kidcode_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `kc_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** Read all carts from SD **before `init_display()`** (the pre-display prefetch in `kidcode_shell._prefetch_carts`). Mounting/accessing SD **after the panel is live hard-hangs the board** (gray screen, dead USB) — `esp_lcd` and `machine.SDCard` are two SPI driver stacks fighting over one host; CS-deselect alone is not enough. Live SD **writes** therefore need a bus handoff (`lcd_bus.SPIBus.deinit()` releases the host → mount → write → reinit). On-device writes are currently gated off (`Workstation.can_manage = False`).
- **The `run_desktop` native-takeover loop starves USB.** Once `KidCode desktop running` prints, there is **no serial / REPL / esptool reset** — the loop never services USB. Serial only flows during the ~2s boot. To capture boot logs, passively read `/dev/ttyACM*` (with reconnect, since native USB re-enumerates) **while physically pressing reset**.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.

## Conventions

- Design docs live at repo **root** as `KidCode_*.md` (e.g. `KidCode_Console_Plan_v0_4.md`); API/format specs live in `docs/`.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
