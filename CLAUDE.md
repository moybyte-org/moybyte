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

2. **The `.moy` console (newer, active direction).** A TIC-80-style "fantasy workstation" where *everything is a cartridge* — now running the shipped **v0.5 shell** (everything-is-a-process: launcher / Player / Editor apps over a fullscreen-stack WM; spec `docs/shell_ux_v1.md`). This is where current feature work happens.
   - `runtime/` — the **host reference** of the console (launcher → Player → tabbed Editor). Pure host, fast dev loop. See `runtime/README.md` for the per-file map; don't duplicate it.
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

`.moy` console (host):
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

- The build (`firmware/lilygo_t_deck_plus_micropython/build.sh`) **clones `lvgl_micropython` into `.build/`**, stages the native C modules (`native/moy_gfx`, `moy_alloc`, and `moy_sd`) into its `ext_mod` tree (re-staged every build because `ext_mod` is wiped on re-clone) and the shared `runtime/` modules (`console` + `project`/`player`/`editor_app`/`wm`, `editors`, `moy_carts`, the `*_layer.py`/`*_ui.py` surfaces, `blocks`, `web_view`, …) into `modules/`, freezes the `modules/` Python, and emits `app` + full-flash images to `dist/` (both gitignored). It needs the ESP-IDF 5.5 toolchain (`IDF_PYTHON ?= ~/.espressif/.../idf5.5_py3.10_env/bin/python`).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.moyproj` game-loop boot path were removed; git history has them.)

### Host == device: the shared console (important)

The console UI is **one codebase** that both the host simulator and the
device run — they render the *same* 320×240 pixels with the *same* petme128 font.
The canonical sources live in `runtime/`; `build.sh` **stages copies into the
firmware `modules/` tree** so the device freezes the identical code (same pattern,
re-staged every build, gitignored). The shipped **v0.5 shell** (spec
`docs/shell_ux_v1.md`) is everything-is-a-process: two concerns — authoring and
playing — joined by one primitive, `run(cart)` plays until exit and returns control
to whoever called it.

- `runtime/console.py` — the shell **kernel**: `Workstation`, shrunk by the v0.5
  refactor to a compositor/router — the Layer-stack frame/input/pointer loop, the
  shared draw toolkit (`_glyph`/`_icon`/`_btn`), store/service attach points
  (`carts_store`/`wifi`/`updater`/`web_hook`) and the spawn/exit verbs; everything
  the user sees is an app it runs. Backend-agnostic: injected `make_api` + cart
  store. (frozen as `console`)
- `runtime/project.py` / `player.py` / `editor_app.py` / `wm.py` — the v0.5 shell
  split (build-staged like the rest): **`Project`** = the open cart's live workspace
  (cart/config/sheet/tilemap/images/pmem + the `commit_*` persistence verbs; a commit
  also appends the undo journal and runs graduation detection). **`Player`** = the
  `run(cart) → plays → returns` black box: starts the cart under the frozen
  `make_api`, ticks it, turns any crash into the error panel, owns the transient
  hold-to-exit toast — zero knowledge of who launched it; exit pops to the run
  CALLER (launcher→launcher; Editor-PLAY→the same tab). **`EditorApp`** = ONE
  authoring app opened on a `Project`: the tab ladder Config→Blocks→Code→Sprites→
  Map→Music (+ PROJECTS/PLAY/SAVE in its lent bar zone), whose tabs ARE the
  extracted `*_layer.py` surfaces — all six tabs are **system-domain responsive**
  (#39 step 3: `PaintLayout`/`MapLayout`/`MusicLayout`/`CardsLayout` join
  `CodeLayout`/`BlockLayout`, each `_base`-verbatim byte-identical at 320×240/1×),
  so the whole Editor reflows to any panel/window size and only a RUNNING CART still
  draws on the fixed 320×240 game canvas. **`FullscreenStackWM`** = the small-screen
  tier's WM: the process back-stack (`screen` is a read-only projection of its top),
  the **memoized** visible/draw stack (zero per-frame list churn, #66), and the
  game↔system viewport composite. **`runtime/wm_windowed.py`** (`WindowedWM` —
  host/P4 only, deliberately NOT staged to the S3 build) is the second presentation
  tier (#73/#58 "Desktop look"): the Picotron-style windowed desktop — launcher =
  the desktop root, every pushed process a window with a WM title strip
  (minimize/maximize/close), draggable by the strip and resizable by the
  bottom-right grip (apply-on-release rubber band); open windows appear as
  **taskbar chips** in the desktop's OS bar. The **picker + Editor share ONE
  "Make" window** (`_GROUP`): picking a project swaps its content to the Editor,
  PROJECTS/its X swap back (the back-stack keeps both kinds — presentation-only
  merge). **Input focus is decoupled from the back-stack**: clicking a window or
  its chip moves keyboard+highlight WITHOUT popping — a playtest keeps ticking
  (it stays the stack top) while the Editor beside it is typed in, its pointer
  feed click-stripped so the background cart never eats editor taps; only an
  explicit exit ends a run (strip X / hold-BACKSPACE while focused / app verb).
  True multi-cart (N games ticking) stays out of scope per #73.
  `ws.windowed_chrome` makes the zoned bar suppress its OS right zone + the dock
  inside windows, so a window's bar row is purely the app's toolbar — the desktop
  bar is the ONE taskbar. A running cart composites integer-scaled + centered in
  its window (no minimize — hiding a game would silently pause it); per-window
  **layout contexts** re-run the #39 responsive layouts at each window's size,
  and `Wallpaper.draw` composites/fills the SYSTEM canvas (cover-crop backdrop)
  so the big desktop backdrop is real. **Panel THEMES** (`chrome.THEMES`,
  Settings → THEME, persisted): named token sets (`panel`/`edge`/`title`/
  `title_ink`/`accent`/`hilite`/`dim`) that every panel surface reads per draw —
  Settings panel, picker backdrop, window strips/chips, launcher selection
  accents; the default "night" is the moybyte site colorway and keeps the frozen
  colors byte-identical. **WiFi setup lives in Settings** (#38, spec §10): a
  WIFI row + panel (scan/password/connect/forget over the injected `ws.wifi`),
  so it works while a game runs — the bar's wifi icon deep-links there in
  windowed mode (fullscreen tiers keep launching the wifi.moy tool). The default
  wallpaper is **`moy_night.moy`** (static brand-colorway scene — a static
  wallpaper keeps the idle desktop free under the redraw gate AND ~0 KB/s on the
  web view). Works over the WEB transport too
  (`tools/web_console.py --windowed`): window buffers become `RecordingLayer`s
  (retained windows blit by reference, the #54/#43 deflayer mechanism), the
  game/wallpaper composites ship as one self-contained spr, scaled system text
  records as rect blocks (incl. inside layers), and the bar renders direct
  (uncached) on recording layers. Try it:
  `python tools/simulate_desktop.py --size 1024x600 --font-scale 2 --windowed`.
- **Editor-as-an-app UX (this replaced the maker/player tap-mode):** a launcher tap
  **always RUNS the cart** — no mode, no type dispatch. The pinned **"Make ✏️"
  tile** opens the Editor **project-picker** (the same grid over every editable cart
  + a ＋New tile), which owns project management (＋New / Copy / Delete — delete is
  a two-tap confirm); the launcher home has no new/dup/del. **Wallpapers are
  backdrop-only**: excluded from the run-grid, chosen in Settings → WALLPAPER,
  still editable via the picker.
- **The zoned top bar (#46, macOS-menu-bar model):** one OS-owned 18px bar. RIGHT
  zone = OS status (clock/wifi/batt/≡ + a **context-X** that exits the active app;
  the launcher root draws no X). LEFT zone = LENT to the active app (`draw_zone`):
  the launcher shows the selected cart's name, the Editor its PROJECTS/tab-ladder/
  PLAY/SAVE icons. Icons stay 16×16 sprites from the editable `IconSheet`
  (Settings → EDIT ICONS). **The bar hides entirely while a GAME plays** (the cart
  owns all 320×240); tools/apps run WITH a minimal bar (title + status + X) so
  they're always exitable.
- **Exit model (#71's pause machinery is retired):** a fullscreen GAME exits on a
  sustained **hold-BACKSPACE (~700ms)** — raw-matrix mode streams the held key, a
  transient progress toast fills, the pop returns to the caller; a quick tap is a
  plain cart key. Taskbar tools/apps exit via the context-X, so BACKSPACE stays an
  ordinary key there (the wifi password field's delete works with zero
  special-casing). A `textmode(True)` game provides its OWN exit via the additive
  cart verb **`quit()`** (in text mode BACKSPACE arrives as a typed delete and the
  keyboard has no autorepeat, so the console's gesture can't reach it) — Letter
  Blitz models it with a tap-✕. (The plan's triple-tap alias was dropped after
  on-device testing.)
- `runtime/editors.py` — `CodeEditor` / `SpriteSheet` / `PaintEditor` cores, plus
  `IconSheet` (16×16 themeable system-bar icon tiles; Settings → EDIT ICONS repaints it). (frozen as `editors`)
- `runtime/moy_carts.py` — the `.moy` store (scan/load/save_*/create/duplicate/delete;
  versioned `seed_builtins` re-seed; `system_icons.moygfx` bar theme; only `json`+`os`)
  plus the **per-project undo/redo journal** (`journal.jsonl` append-only + full-file
  snapshots + an atomic cursor, torn-snapshot-safe; commits fire on a typing-idle
  autosave debounce + hard commits on tab-leave/PLAY; Ctrl+Z/Y walks it in the code
  editor) and **blocks↔code graduation** (MakeCode model: a diverging code commit —
  conservative recompile-and-normalize compare — stores `"graduated": true` in the
  manifest with a journal rider; the Blocks tab goes read-only + celebrates; undoing
  past the graduating commit un-graduates). (frozen as `moy_carts`)
- `runtime/font.py` — petme128 8×8 font, the ONE glyph source both backends rasterize (#62): the host draws it per-pixel, the device passes its blob to the native `moy_gfx.text` kernel (staged as `moy_font` at build; framebuf.text — same glyphs, no clip rect — is the no-gfx fallback).
- `runtime/host_app.py` — host glue: host `make_api`, `build_workstation()`, `ConsoleDriver` (mouse=touch, arrows=trackball). Not on device.

(The pre-unification host UI — `shell.py`/`workstation.py`/`engine.py`/`api.py`/
`cartridge.py` — was removed once the shared console replaced it; issue #17.)

### Device module map (`firmware/lilygo_t_deck_plus_micropython/modules/`)

- `moybyte_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_FULLSCREEN_BENCH` / `RUN_COMPOSITOR_SMOKE` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE`; SD prefetch; native takeover.
- `moy_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `moy_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`moy_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`moy_carts` and injects the device `make_api` + store into `console.Workstation`. **Input runs on a poller thread (#69, `MOY_INPUT_POLLER`)**: `moybyte.input.InputPoller` owns every I2C0 transaction (kbd + GT911 + mode switches) off the frame loop, so the C3's 40-60ms clock-stretch stalls block only that thread — requires the build's `esp32_i2c_gil_release.patch` (machine.I2C frees the GIL across its blocking wait); falls back to synchronous polling if `_thread`/the thread dies.
- `console.py` / `project.py` / `player.py` / `editor_app.py` / `wm.py` / `editors.py` / `moy_carts.py` (+ the `*_layer.py`/`*_ui.py` surfaces, `blocks.py`, `web_view.py`) — **staged from `runtime/` at build** (see above).
- `moybyte_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `moy_compositor.py` — native RGB565 framebuffer + DMA flush.
- `tdeck_display.py` — display/LVGL + SPI bus bootstrap.
- `moy_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Phase 3 adds WiFi download — `check_online`/`begin_download`/`download_step` stream a manifest-described `.bin` over a raw socket straight to SD (sha256-verified, never buffering the whole 3MB), reusing the injected `wifi` service. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels), wires the wifi service, and calls `mark_valid()` at a healthy boot to cancel rollback.
- `moy_webserver.py` — device WEB VIEW (#41/#22): serves the **running console** to a browser on the same WiFi via the **same draw-command protocol** (`defspr`/`spr`-by-index/`map`/`settiles`/primitives, serve-time defspr, atlas `gen` lock-step), so the device page renders device frames. The **live channel is a persistent WebSocket** (`GET /ws`, RFC 6455 handshake): frames PUSH down as text messages, input pushes up as `{"events":[...]}` text — one socket, **no per-frame HTTP handshake** (the #41 transport swap; the old transport opened a new TCP conn per `/frame`, capping ~20-25fps). The page + assets still load over plain HTTP (`GET /`, `GET /assets`); the legacy `GET/POST /frame` + `POST /input` remain as a poll **fallback**. Records the cart's per-frame draw calls (a `DrawRecorder` fed by a `TeeCanvas` that forwards to the real `DeviceCanvas`, format identical to `tools/command_canvas.py`) — **never** the raw framebuffer (WiFi ~72KB/s, 153KB/frame is unplayable). Non-blocking listening socket + a non-blocking persistent `_WSConn` (cross-iteration read buffer for split frames; blocking-budget sends, stalled client dropped); `moy_runtime.run_desktop`'s single-threaded loop services it **BETWEEN frames** via the `WebView` controller (`begin_frame`/`commit_frame`/`poll`). **Liveness/stream-mode now key on a connected WebSocket** (not a recent `/frame` poll). **Off by default → `ws.canvas` stays the raw `DeviceCanvas` (zero per-draw cost); Settings → WEB VIEW swaps the Tee in** (and rebinds wallpaper/cart). WiFi STA ≠ display SPI, so it doesn't touch the SD/panel bus — but **WiFi↔LCD-DMA RAM coexistence (#38/#40) + the socket/WebSocket layer are UNVERIFIED on hardware** (host-tested via `tests/test_moy_webserver.py`, incl. a real-localhost WS round-trip). WS removes the per-frame handshake (smoother, lower-latency input) but **not** the ~72KB/s ceiling: light screens ~30-40fps, the heavy launcher ~18fps. **Per-WM-surface streams (v0.5 shell Stage 9):** the shared recorder can slice each frame into one command stream per WM surface (`web_view.surfaces_on` — bar / app content / player viewport, a view over the same flat stream); the **host** web console renders them, the **device keeps the flag off** (flat frames) — wiring the device transport to per-surface render is a standing gate.

### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display.** **SD is no longer mounted before `init_display()` (#56).** The old boot prefetch read carts via `machine.SDCard` *before* the panel came up; that re-runs `spi_bus_initialize()`, and on a **populated** card the mount succeeds but leaves the shared host claimed, so the next `init_display()` intermittently failed with `can't convert '' to int` (the "no-SD / empty-SD boots, SD-with-files doesn't" bug, confirmed + fixed on hardware). So `moybyte_shell.main()` now defaults `PREFETCH_SD_BEFORE_DISPLAY=False`: **nothing touches SD before the panel is up**, and `run_desktop` loads carts *after* init via `with_sd_live` (`prefetched=None → _load_carts(with_sd_live)`), degrading to built-in carts on any SD failure (so this can only make display init MORE reliable). Mounting `machine.SDCard` **after** the panel is live still hard-hangs the board (gray screen, dead USB): `esp_lcd` and `machine.SDCard` are two driver stacks fighting over one host and CS-deselect alone is not enough — which is exactly why the post-display path uses the native `moy_sd` attach (below), not `machine.SDCard`. **Live reads/writes (post-display) go through the native `moy_sd` module** (`native/moy_sd/modmoy_sd.c`), which *attaches* the card to the host `esp_lcd` already initialized (`sdspi_host_init_device`, no bus re-init — the ESP-IDF "Sharing the SPI Bus" pattern) and leaves the panel device intact. `moybyte_sd.with_sd_live(fn)` mounts via `moy_sd` **once and keeps the card resident** for the session, then just runs `fn`. **Do not tear the SD device down between ops** (learned the painful way): a per-op `sdspi_host_deinit` — or reconfiguring the panel's `TFT_CS` via `Pin(...)` — corrupts the shared bus/DMA state and the *next panel flush silent-hangs the board* (the write itself lands on SD, then resume freezes; no panic, USB stays enumerated but dead). So leave `TFT_CS`/`SD_CS` alone (driver-owned; only park the unused LoRa `RADIO_CS` high) and never flush the panel inside the session — the desktop loop is single-threaded, so SD ops run between frames. On-device writes are enabled (`Workstation.can_manage`, wired to `with_sd_live` in `run_desktop`).
- **The `run_desktop` native-takeover loop starves USB.** Once `Moybyte desktop running` prints, there is **no serial / REPL / esptool reset** — the loop never services USB. Serial only flows during the ~2s boot. To capture boot logs, passively read `/dev/ttyACM*` (with reconnect, since native USB re-enumerates) **while physically pressing reset**.
- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3`). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).

## Conventions

- The current design doc is **`moybyte_Console_Plan_v0_5.md`** (repo root); superseded v0.1/v0.3/v0.4 docs are archived under `docs/history/`. The **current `.moy` cart API** is documented in **`docs/moy_cart_api.md`**; the legacy `.moyproj` SDK specs (api / project-format / runtime-contract) are archived under `docs/history/` too. The shipped v0.5 shell's UX reference is **`docs/shell_ux_v1.md`** (corrected to the as-built reality); `docs/shell_architecture_v1.md` (privileged system carts + layered compositor) is the standing direction doc; the three implemented shell plan docs (`shell_ux_technical_plan_v1` / `shell_os_architecture_v1` / `shell_layers_refactor_v1`) are archived under `docs/history/`.
- **Issue mirror (`docs/issues/`, gitignored):** a **local, un-committed** snapshot of every GitHub issue, split into `open/` and `closed/` (files named `NNNN-slug.md`) plus `INDEX.md`, so an issue number referenced in a commit, doc, or chat resolves without network access. GitHub is the source of truth — this is a generated read-only mirror (not in git, to avoid churn), so a fresh checkout won't have it: **build it with `make sync-issues`** (wrapper over `tools/sync_issues.py`; needs the `gh` CLI, authed). The script wipes and rewrites both folders from `gh`, so state changes and edits never leave a stale copy. **Run `make sync-issues` at the start of any session that reads or reasons about issues, and again after EVERY issue you open/close/comment/edit** — the mirror is only trustworthy if syncing is a reflex, and living-body issues (like the #66 performance ledger) go stale locally the moment the body is edited on GitHub. Don't hand-edit the files.
- Tests run against the host packages only; firmware tests (`tests/test_micropython_spike.py`) grep the frozen device modules' source rather than executing them.
- **Cart versioning (#47):** every `system_carts/*/manifest.json` carries an integer `"version"`. `seed_builtins` re-seeds an on-SD built-in only when the baked version is **newer**, and preserves the kid's data (`pmem.json` saves + `config.json` tuning) across the re-seed. **Bump a built-in's manifest `version` whenever you change its content**, or an already-seeded device keeps the stale copy.
- **Device performance — the single source of truth is issue #66** (the living "performance ledger": current per-cart fps, the frame-budget model, shipped/reverted/open levers, how to measure). **Edit #66's body when new hardware numbers land** (comments = changelog), then `make sync-issues`; do NOT scatter numbers into this file, the plan, or new docs — they go stale. Snapshot for orientation only (2026-07-08, hardware-confirmed, UNCAPPED — the frame governor ships OFF, `console.FPS_GOVERNOR`, owner measurement mode): Hop Quest 52-56, Sky Run 45-49, Letter Blitz 33-45, Tap Only Red 36-38, Sakura 32-37, Battle City 25-33; owner feel verdict "without the diag knob there is no stutter". **The engine-side lever chain is exhausted** — every feed/dispatch/GC-cost lever tried and either shipped (auto-native carts #67, live-set diet, pal-state variant cache #72, layer pool, `background()`) or reverted with a recorded verdict (Fold-2 auto map cache, third bounce slot — the latter also retired the core-1 feeder unbuilt); remaining fps levers are per-cart render diets (BC field layer), the #67 Lua tier (strategic), and the P4 (#58). Every draw verb is native (#43/#32/#62/#63) and the "draw LESS" idioms are both modeled by the seed carts and taught in docs/moy_cart_api.md → "Make it fast" — kids copy the carts, the carts model the doc. Remaining play defects: the #74 touch stalls (fingerprinted: boot-wake one-shot + rare steady-state; INT-pin gating is next) and the launcher live-wallpaper cost (~10fps launcher; the Make picker went static-black). Kid mode (#68): Settings → PERF DIAG (default OFF) gates the diag frame-eaters — **measurement sessions need it ON** — and DIAG SD LOG separately gates the periodic diag→SD write (keep OFF for stutter-free serial measurement). Three interpreter-vs-kid-idiom taxes were found and fixed ENGINE-SIDE, kid API untouched: per-draw-call dispatch (#43 batch + #63 native `spr_gate`), call-frame heap-spill (#63), and **float boxing** (#66: REPR_A allocated 16B per float result — 73KB/frame in sakura — whose heap-wrap gc collect was the long-standing ~150ms micro-stutter; fixed by the REPR_C build patch, unboxed 30-bit floats). Banding is structurally gone (#66 SRAM-bounce flush: the panel DMA only reads internal SRAM; the esp_lcd no-acquire patch makes banded tx_color queue-only). Remaining known frame spikes are tracked: #68 (diag-caused, kid-mode gate) and #69 (keyboard+touch I2C stalls, sized via I2CSTAT). Diagnostics: `PERF`/`DRAWBRK`/`DRAW2`(now with per-verb map/text/fill)/`BATCH`/`FLUSHBRK`/`CHROMEBRK`/`PUMP`/`I2CSTAT`/`CALIB`/`HITCH` serial lines, gated behind `perf_capture`.
- **OTA / on-device firmware update (#53):** the build is now **dual-OTA** (`build.sh --ota` → `otadata + ota_0 + ota_1 + vfs`, both app slots 4MB on the 16MB part), so the device can flash a new `.bin` from `/sd/update` into the **inactive** slot and ping-pong (`moy_ota.OtaUpdater` + Settings → UPDATE FW). The running slot is never touched and rollback is on (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`), so a bad image self-heals — `moy_runtime.run_desktop` calls `updater.mark_valid()` once the desktop is up to confirm the new image. **The app (ota_0) moved off 0x10000 → 0x20000**, so `build.sh` merges the full image at the derived offset and the Makefile uses `MPY_APP_OFFSET`. **Switching a deployed device to OTA needs ONE full USB reflash** (`make firmware-flash-lilygo-micropython-full-erase` — rewrites the partition table + clears `otadata` so the bootloader boots ota_0); after that, updates are SD/wireless. **Phase 3** adds Settings → UPDATE ONLINE: it reads `/sd/update/ota.json` (`{"manifest_url": ...}`), fetches a manifest (`version`/`url`/`sha256`/`size`), and if newer than `moy_ota.FIRMWARE_VERSION` streams the `.bin` to SD then installs it. **Bump `moy_ota.FIRMWARE_VERSION` on every release** (like cart versioning) or the online check won't offer the update. Still **NEEDS ON-HARDWARE VERIFICATION** (flash, reboot-into-new-slot, rollback, and the WiFi download — the WLAN stack vs LCD-DMA RAM coexistence is the open #38 risk).
- **Two OTA channels (#53):** STABLE (master) and UNSTABLE/BETA (current dev tree). Settings → CHANNEL toggles which the device checks; `ota.json` now carries `{"channels": {"stable": url, "unstable": url}}`. The build STAMPS its identity into a gitignored `modules/_ota_build.py` (CHANNEL/VERSION/LABEL) from `MOYBYTE_OTA_CHANNEL` (default `stable`), so the channel is a **build choice, not a per-branch source edit** (clean across merges) — `moy_ota` imports it and offers an install when the manifest's channel **differs** from the running one (a switch — incl. beta→stable rollback) **or** is a higher version **within** the channel. A beta's version is the build epoch (auto-newer each publish), shown via a human `label`. **Publish the current working tree (uncommitted OK) as a beta the device pulls over WiFi:** `make ota-publish-unstable` (builds with the unstable stamp → `OTA_ROOT/unstable/{firmware.bin,latest.json}`), served by a persistent host (`make ota-serve-install` → systemd `--user` unit `tools/moybyte-ota.service`). The first two-channel firmware still needs one USB flash; after that betas are OTA. `make ota-publish-stable` does the same from master.
