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

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/kc_gfx`, `kc_alloc`, and `kc_sd`) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone) and the shared `runtime/` modules (`editors`/`console`/`kid_carts`) into `modules/`, freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.kcproj` game-loop boot path were removed; git history has them.)

### Host == device: the shared console (important)

The v0.4 console UI is now **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree** so the device freezes the identical code (same pattern,
re-staged every build, gitignored):

- `runtime/console.py` — `Launcher` + `Pointer` + `Workstation` + the cards / code /
  paint UI + layout + the **unified 18px top bar** (icon-only mode switchers home/edit/
  paint/map/blocks left, clock/wifi/batt/gear right, new/dup/del on home — #46), whose
  icons are 16×16 sprites blitted from an editable `IconSheet`. Backend-agnostic:
  injected `make_api` + cart store. (frozen as `console`)
- `runtime/editors.py` — `CodeEditor` / `SpriteSheet` / `PaintEditor` cores, plus
  `IconSheet` (16×16 themeable system-bar icon tiles; Settings → EDIT ICONS repaints it). (frozen as `editors`)
- `runtime/kid_carts.py` — the `.kcart` store (scan/load/save_*/create/duplicate/delete;
  versioned `seed_builtins` re-seed; `system_icons.kgfx` bar theme; only `json`+`os`). (frozen as `kid_carts`)
- `runtime/font.py` — petme128 8×8 font (host only; the device uses framebuf's copy).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()`, `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `kidcode_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_FULLSCREEN_BENCH` / `RUN_COMPOSITOR_SMOKE` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE`; SD prefetch; native takeover.
- `kid_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `kc_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`kc_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`kid_carts` and injects the device `make_api` + store into `console.Workstation`.
- `console.py` / `editors.py` / `kid_carts.py` — **staged from `runtime/` at build** (see above).
- `kidcode_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `kc_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.
- `kc_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels) and calls `mark_valid()` at a healthy boot to cancel rollback.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** The boot prefetch (`kidcode_shell._prefetch_carts`) still reads carts via `machine.SDCard` **before `init_display()`** — that path re-runs `spi_bus_initialize()`, so calling it **after the panel is live hard-hangs the board** (gray screen, dead USB): `esp_lcd` and `machine.SDCard` are two driver stacks fighting over one host; CS-deselect alone is not enough. **Live reads/writes (post-display) go through the native `kc_sd` module** (`native/kc_sd/modkc_sd.c`), which *attaches* the card to the host `esp_lcd` already initialized (`sdspi_host_init_device`, no bus re-init — the ESP-IDF "Sharing the SPI Bus" pattern) and leaves the panel device intact. `kidcode_sd.with_sd_live(fn)` mounts via `kc_sd` **once and keeps the card resident** for the session, then just runs `fn`. **Do not tear the SD device down between ops** (learned the painful way): a per-op `sdspi_host_deinit` — or reconfiguring the panel's `TFT_CS` via `Pin(...)` — corrupts the shared bus/DMA state and the *next panel flush silent-hangs the board* (the write itself lands on SD, then resume freezes; no panic, USB stays enumerated but dead). So leave `TFT_CS`/`SD_CS` alone (driver-owned; only park the unused LoRa `RADIO_CS` high) and never flush the panel inside the session — the desktop loop is single-threaded, so SD ops run between frames. On-device writes are enabled (`Workstation.can_manage`, wired to `with_sd_live` in `run_desktop`).
- **The `run_desktop` native-takeover loop starves USB.** Once `KidCode desktop running` prints, there is **no serial / REPL / esptool reset** — the loop never services USB. Serial only flows during the ~2s boot. To capture boot logs, passively read `/dev/ttyACM*` (with reconnect, since native USB re-enumerates) **while physically pressing reset**.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3`). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`KidCode_Console_Plan_v0_4.md`** (repo root); superseded v0.1/v0.3 docs are archived under `docs/history/`. API/format specs (the `.kcproj` SDK) live in `docs/`.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
- **Device perf ceiling (#43, the next-session lever):** frame time = draw + flush, and flush is a hard ~20ms (153KB PSRAM→SPI), so **full-frame 60fps is physically impossible**; ~18-22fps is the v1 full-res ceiling. The cheap wins are spent (native `spr_batch`, the static chrome cached into the bar, double-buffer flush). The remaining lever is **dirty-rect** (redraw + flush only the changed region) — big for static-background games, ~no help for full-screen scrollers. The MicroPython interpreter is NOT the bottleneck (cart logic ~1-2ms per `DRAWBRK`), so a runtime rewrite (#6) buys no FPS. Diagnostics: `PERF`/`FLUSHBRK`/`DRAWBRK` serial lines, gated behind `perf_capture`.
- **OTA / on-device firmware update (#53):** the build is now **dual-OTA** (`build.sh --ota` → `otadata + ota_0 + ota_1 + vfs`, both app slots 4MB on the 16MB part), so the device can flash a new `.bin` from `/sd/update` into the **inactive** slot and ping-pong (`kc_ota.OtaUpdater` + Settings → UPDATE FW). The running slot is never touched and rollback is on (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`), so a bad image self-heals — `kid_runtime.run_desktop` calls `updater.mark_valid()` once the desktop is up to confirm the new image. **The app (ota_0) moved off 0x10000 → 0x20000**, so `build.sh` merges the full image at the derived offset and the Makefile uses `MPY_APP_OFFSET`. **Switching a deployed device to OTA needs ONE full USB reflash** (`make firmware-flash-lilygo-micropython-full-erase` — rewrites the partition table + clears `otadata` so the bootloader boots ota_0); after that, updates are SD/wireless. Still **NEEDS ON-HARDWARE VERIFICATION** (flash, reboot-into-new-slot, and the rollback path).
