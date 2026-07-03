# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Moybyte is a PC-first SDK + simulator for a future ESP32 kids' coding console, plus the firmware that runs it on real hardware (LilyGO T-Deck Plus). There are **two parallel project systems** living side by side — knowing which one you're touching is the single most important orientation fact:

1. **The `.moyproj` SDK (original, mature).** A kid writes a project; it runs on a PC sim and exports to firmware.
   - `moybyte/` — the runtime/API a project calls (`api`, `screen`, `sprites`, `input`, `audio`, `radio`, `manifest`, `permissions`).
   - `moybyte_cli/` — the `moybyte` console command (`run`, `new`, `pack`, `check-portable`, `export-device`, firmware header gen, board defs).
   - `moybyte_sim/` — sim backends (`pygame_backend`, `headless_backend`, fake audio/radio).
   - `moybyte_blocks/` — block-language → Python compiler.
   - Projects (`examples/*.moyproj`) must stay inside a **portable subset** enforced by `moybyte_cli/portable.py`: only `moybyte`/`math`/`random` imports, and no `eval`/`exec`/`open`/`getattr`/etc. `make check-portable` is the gate.

2. **The v0.4 `.moy` console (newer, active direction).** A TIC-80-style "fantasy workstation" where *everything is a cartridge*. This is where current feature work happens.
   - `runtime/` — the **host reference** of the console (launcher → cartridge → cards editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
   - `firmware/lilygo_t_deck_plus_micropython/` — the **device port** of that same console (MicroPython).
   - `system_carts/*.moy` — seed cartridges (folder = `manifest.json` + `main.py` + `config.json`).

The two systems share a design intent but **not code**. `.moyproj` is the old format; `.moy` is the v0.4 format.

### The v0.4 portability contract (why the canvas is "indexed")

The v0.4 canvas works in **palette indices** (the `MOY64` palette) with a plain-function drawing API (`cls/pset/line/rect/rectfill/circ/circfill/spr/print`) — no dependency on `framebuf`, LVGL, or even Python. This is deliberate: the *same* `.moy` is meant to run on the host (`runtime/canvas.py`) and on the device (`moy_compositor`, indices → RGB565), and eventually a Lua VM. When adding drawing features, add them to **both** backends and keep the API identical.

## Common commands

```bash
make setup          # python -m venv --system-site-packages + pip install -e '.[dev]'
make test           # pytest (all). The venv python is .venv/bin/python
make doctor         # environment sanity check via the moybyte CLI

# run a single test
.venv/bin/python -m pytest tests/test_v04_userland.py -k cards
.venv/bin/python -m pytest tests/test_micropython_spike.py::test_name
```

`.moyproj` SDK loop:
```bash
.venv/bin/moybyte run examples/tiny_runner.moyproj --headless --frames 60
.venv/bin/moybyte run examples/tiny_runner.moyproj --fps 30 --scale 4   # pygame window
make check-portable                                                     # portable-subset gate
```

v0.4 `.moy` console (host):
```bash
python tools/simulate_desktop.py                                  # boots the launcher (needs a display)
python tools/simulate_desktop.py --cart system_carts/star_catcher.moy
python tools/simulate_desktop.py --demo --gif demo.gif            # headless tour
```

## Firmware (LilyGO T-Deck Plus, MicroPython)

This is the active hardware target. Build → flash → monitor:

```bash
MOYBYTE_SKIP_VFS_BOOT=1 make firmware-build-lilygo-micropython     # outputs to dist/current/
make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0           # esptool, default_reset
make firmware-monitor-lilygo-micropython PORT=/dev/ttyACM0         # miniterm @115200
```

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/moy_gfx`, `moy_alloc`, and `moy_sd`) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone) and the shared `runtime/` modules (`editors`/`console`/`moy_carts`) into `modules/`, freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.moyproj` game-loop boot path were removed; git history has them.)

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
- `runtime/moy_carts.py` — the `.moy` store (scan/load/save_*/create/duplicate/delete;
  versioned `seed_builtins` re-seed; `system_icons.moygfx` bar theme; only `json`+`os`). (frozen as `moy_carts`)
- `runtime/font.py` — petme128 8×8 font (host only; the device uses framebuf's copy).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()`, `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `moybyte_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_FULLSCREEN_BENCH` / `RUN_COMPOSITOR_SMOKE` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE`; SD prefetch; native takeover.
- `moy_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `moy_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`moy_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`moy_carts` and injects the device `make_api` + store into `console.Workstation`.
- `console.py` / `editors.py` / `moy_carts.py` — **staged from `runtime/` at build** (see above).
- `moybyte_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `moy_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.
- `moy_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Phase 3 adds WiFi download — `check_online`/`begin_download`/`download_step` stream a manifest-described `.bin` over a raw socket straight to SD (sha256-verified, never buffering the whole 3MB), reusing the injected `wifi` service. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels), wires the wifi service, and calls `mark_valid()` at a healthy boot to cancel rollback.
- `moy_webserver.py` — device WEB VIEW (#41/#22): serves the **running console** to a browser on the same WiFi via the **same draw-command protocol** (`defspr`/`spr`-by-index/`map`/`settiles`/primitives, serve-time defspr, atlas `gen` lock-step), so the device page renders device frames. The **live channel is a persistent WebSocket** (`GET /ws`, RFC 6455 handshake): frames PUSH down as text messages, input pushes up as `{"events":[...]}` text — one socket, **no per-frame HTTP handshake** (the #41 transport swap; the old transport opened a new TCP conn per `/frame`, capping ~20-25fps). The page + assets still load over plain HTTP (`GET /`, `GET /assets`); the legacy `GET/POST /frame` + `POST /input` remain as a poll **fallback**. Records the cart's per-frame draw calls (a `DrawRecorder` fed by a `TeeCanvas` that forwards to the real `DeviceCanvas`, format identical to `tools/command_canvas.py`) — **never** the raw framebuffer (WiFi ~72KB/s, 153KB/frame is unplayable). Non-blocking listening socket + a non-blocking persistent `_WSConn` (cross-iteration read buffer for split frames; blocking-budget sends, stalled client dropped); `moy_runtime.run_desktop`'s single-threaded loop services it **BETWEEN frames** via the `WebView` controller (`begin_frame`/`commit_frame`/`poll`). **Liveness/stream-mode now key on a connected WebSocket** (not a recent `/frame` poll). **Off by default → `ws.canvas` stays the raw `DeviceCanvas` (zero per-draw cost); Settings → WEB VIEW swaps the Tee in** (and rebinds wallpaper/cart). WiFi STA ≠ display SPI, so it doesn't touch the SD/panel bus — but **WiFi↔LCD-DMA RAM coexistence (#38/#40) + the socket/WebSocket layer are UNVERIFIED on hardware** (host-tested via `tests/test_moy_webserver.py`, incl. a real-localhost WS round-trip). WS removes the per-frame handshake (smoother, lower-latency input) but **not** the ~72KB/s ceiling: light screens ~30-40fps, the heavy launcher ~18fps.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** **SD is no longer mounted before `init_display()` (#56).** The old boot prefetch read carts via `machine.SDCard` *before* the panel came up; that re-runs `spi_bus_initialize()`, and on a **populated** card the mount succeeds but leaves the shared host claimed, so the next `init_display()` intermittently failed with `can't convert '' to int` (the "no-SD / empty-SD boots, SD-with-files doesn't" bug, confirmed + fixed on hardware). So `moybyte_shell.main()` now defaults `PREFETCH_SD_BEFORE_DISPLAY=False`: **nothing touches SD before the panel is up**, and `run_desktop` loads carts *after* init via `with_sd_live` (`prefetched=None → _load_carts(with_sd_live)`), degrading to built-in carts on any SD failure (so this can only make display init MORE reliable). Mounting `machine.SDCard` **after** the panel is live still hard-hangs the board (gray screen, dead USB): `esp_lcd` and `machine.SDCard` are two driver stacks fighting over one host and CS-deselect alone is not enough — which is exactly why the post-display path uses the native `moy_sd` attach (below), not `machine.SDCard`. **Live reads/writes (post-display) go through the native `moy_sd` module** (`native/moy_sd/modmoy_sd.c`), which *attaches* the card to the host `esp_lcd` already initialized (`sdspi_host_init_device`, no bus re-init — the ESP-IDF "Sharing the SPI Bus" pattern) and leaves the panel device intact. `moybyte_sd.with_sd_live(fn)` mounts via `moy_sd` **once and keeps the card resident** for the session, then just runs `fn`. **Do not tear the SD device down between ops** (learned the painful way): a per-op `sdspi_host_deinit` — or reconfiguring the panel's `TFT_CS` via `Pin(...)` — corrupts the shared bus/DMA state and the *next panel flush silent-hangs the board* (the write itself lands on SD, then resume freezes; no panic, USB stays enumerated but dead). So leave `TFT_CS`/`SD_CS` alone (driver-owned; only park the unused LoRa `RADIO_CS` high) and never flush the panel inside the session — the desktop loop is single-threaded, so SD ops run between frames. On-device writes are enabled (`Workstation.can_manage`, wired to `with_sd_live` in `run_desktop`).
- **The `run_desktop` native-takeover loop starves USB.** Once `Moybyte desktop running` prints, there is **no serial / REPL / esptool reset** — the loop never services USB. Serial only flows during the ~2s boot. To capture boot logs, passively read `/dev/ttyACM*` (with reconnect, since native USB re-enumerates) **while physically pressing reset**.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3`). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`moybyte_Console_Plan_v0_5.md`** (repo root); superseded v0.1/v0.3/v0.4 docs are archived under `docs/history/`. The **current `.moy` cart API** is documented in **`docs/moy_cart_api.md`**; the legacy `.moyproj` SDK specs (api / project-format / runtime-contract) are archived under `docs/history/` too.
- **Issue mirror (`docs/issues/`, gitignored):** a **local, un-committed** snapshot of every GitHub issue, split into `open/` and `closed/` (files named `NNNN-slug.md`) plus `INDEX.md`, so an issue number referenced in a commit, doc, or chat resolves without network access. GitHub is the source of truth — this is a generated read-only mirror (not in git, to avoid churn), so a fresh checkout won't have it: **build it with `make sync-issues`** (wrapper over `tools/sync_issues.py`; needs the `gh` CLI, authed). The script wipes and rewrites both folders from `gh`, so state changes and edits never leave a stale copy. **Run `make sync-issues` at the start of any session that reads or reasons about issues, and again after EVERY issue you open/close/comment/edit** — the mirror is only trustworthy if syncing is a reflex, and living-body issues (like the #66 performance ledger) go stale locally the moment the body is edited on GitHub. Don't hand-edit the files.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
- **Device performance — the single source of truth is issue #66** (the living "performance ledger": current per-cart fps, the frame-budget model, shipped/reverted/open levers, how to measure). **Edit #66's body when new hardware numbers land** (comments = changelog), then `make sync-issues`; do NOT scatter numbers into this file, the plan, or new docs — they go stale. Snapshot for orientation only (2026-07-03): Sakura 24-25fps (interpreter-logic-bound — the #63 frame-spill pathology, fixed by the native spr_gate + doubled caches, keeps this the hot regime), Sky Run 47-48 (flush-bound ceiling), Hop Quest 32, Battle City 26-31 (render-bound, Python prims). The old "interpreter is not the bottleneck" claim is WRONG for float-physics carts — see #63. Diagnostics: `PERF`/`DRAWBRK`/`DRAW2`/`BATCH`/`FLUSHBRK`/`CALIB` serial lines, gated behind `perf_capture`.
- **OTA / on-device firmware update (#53):** the build is now **dual-OTA** (`build.sh --ota` → `otadata + ota_0 + ota_1 + vfs`, both app slots 4MB on the 16MB part), so the device can flash a new `.bin` from `/sd/update` into the **inactive** slot and ping-pong (`moy_ota.OtaUpdater` + Settings → UPDATE FW). The running slot is never touched and rollback is on (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`), so a bad image self-heals — `moy_runtime.run_desktop` calls `updater.mark_valid()` once the desktop is up to confirm the new image. **The app (ota_0) moved off 0x10000 → 0x20000**, so `build.sh` merges the full image at the derived offset and the Makefile uses `MPY_APP_OFFSET`. **Switching a deployed device to OTA needs ONE full USB reflash** (`make firmware-flash-lilygo-micropython-full-erase` — rewrites the partition table + clears `otadata` so the bootloader boots ota_0); after that, updates are SD/wireless. **Phase 3** adds Settings → UPDATE ONLINE: it reads `/sd/update/ota.json` (`{"manifest_url": ...}`), fetches a manifest (`version`/`url`/`sha256`/`size`), and if newer than `moy_ota.FIRMWARE_VERSION` streams the `.bin` to SD then installs it. **Bump `moy_ota.FIRMWARE_VERSION` on every release** (like cart versioning) or the online check won't offer the update. Still **NEEDS ON-HARDWARE VERIFICATION** (flash, reboot-into-new-slot, rollback, and the WiFi download — the WLAN stack vs LCD-DMA RAM coexistence is the open #38 risk).
- **Two OTA channels (#53):** STABLE (master) and UNSTABLE/BETA (current dev tree). Settings → CHANNEL toggles which the device checks; `ota.json` now carries `{"channels": {"stable": url, "unstable": url}}`. The build STAMPS its identity into a gitignored `modules/_ota_build.py` (CHANNEL/VERSION/LABEL) from `MOYBYTE_OTA_CHANNEL` (default `stable`), so the channel is a **build choice, not a per-branch source edit** (clean across merges) — `moy_ota` imports it and offers an install when the manifest's channel **differs** from the running one (a switch — incl. beta→stable rollback) **or** is a higher version **within** the channel. A beta's version is the build epoch (auto-newer each publish), shown via a human `label`. **Publish the current working tree (uncommitted OK) as a beta the device pulls over WiFi:** `make ota-publish-unstable` (builds with the unstable stamp → `OTA_ROOT/unstable/{firmware.bin,latest.json}`), served by a persistent host (`make ota-serve-install` → systemd `--user` unit `tools/moybyte-ota.service`). The first two-channel firmware still needs one USB flash; after that betas are OTA. `make ota-publish-stable` does the same from master.
