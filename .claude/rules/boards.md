---
paths:
  - "firmware/**"
  - "device/**"
  - "native/**"
  - "tools/board_*.py"
  - "tools/p4_*.py"
---

<!-- Boards, ports, and the constraints that hang one. -->

## Firmware (LilyGO T-Deck Plus, MicroPython)

This is the active hardware target. Build → flash → monitor:

```bash
make firmware-build-tdeck-mainline                                 # -> dist/tdeck_mainline/
make firmware-flash-tdeck-mainline PORT=/dev/ttyACM0               # merged image at 0x0
#   ... then a SEPARATE esptool --before default_reset --after hard_reset to START it:
#   write_flash's own trailing reset does not, and the board otherwise sits in the loader
make firmware-monitor-tdeck-mainline PORT=/dev/ttyACM0             # miniterm @115200
#   (the fork-era firmware-*-lilygo-micropython names survive as aliases)
```

- The build (`firmware/lilygo_t_deck_plus_mainline/build.sh`) **clones mainline MicroPython v1.28 and
  esp-idf v5.5.1 into `.build/`** (no LVGL, no fork — see below), applies the port's patches under
  marker guards, stages the shared native C modules plus its own `native/moy_lcd`
  through `USER_C_MODULES`, and freezes the Python. **Which shared modules cross is DATA, not a list
  in the script** (#161 Phase 3, completed 2026-08-17): each board carries a `board.toml` with a
  **denylist** over `runtime/*.py` (every `[[deny]]` names the file, a kind and a prose `why`), an
  **allowlist** over `device/*.py`, and a **`[native.shared]` denylist** over the C modules in
  `native/` (the P4 denies `moy_sd`/`moy_audio` with reasons; the T-Deck denies nothing —
  `tools/board_config.py stage-native` stages the copies and generates the `.staged/micropython.cmake`
  include list, so neither build.sh nor the tracked cmake names a shared module). The **web runner
  carries a `board.toml` too** (the last hand-rolled DENY list, converted the same day), and
  `tests/test_staging_closure.py` derives every target's frozen set from the declarations. A new
  shared module reaches every board by default and staying off one is a written decision; the old
  per-board allowlists are what let the T-Deck silently miss the web console. The `device/` allowlist
  stays an allowlist because `runtime/` is a shared tree whose default answer is "yes" and the device
  tier's is not — **which is also why the headless Zero inverts it** (2026-08-29): that board has no
  console, so `runtime/`'s default answer there is "no", and `strategy = "allowlist"` with a group
  per capability is the honest shape. Which shape a board uses is DECLARED
  (`board_config.shared_strategy`) and pinned in both directions, because a board silently changing
  shape silently changes what it freezes. The stager also **prunes untracked strays** — `modules/` is gitignored and never
  cleaned while the freeze takes the whole DIRECTORY, so deleted modules kept shipping in images
  built on a warm tree. **The shared half of every board build is `tools/esp32_build_lib.sh`**
  (sourced by every board's build.sh; landed 2026-08-17 with two, the Guition was provisioned on it
  the next day, and the Zero graduated onto it 2026-08-29): toolchain setup with the export.sh probe +
  install self-heal, IDF_COMPONENTS append, the native-code-free patch, native staging + the web
  blob (generated into the STAGED copy — a build must never write into the shared `native/` tree),
  the OTA identity stamp (every board reads `device/moy_ota.py`, one path), the frozen manifest +
  md5 fingerprint, the stale-sdkconfig guard and the #168 size guard; what stays per-board is the
  patch ladder and the sdkconfig facts. **A board's sdkconfig facts are DATA the build READS**
  (2026-08-21): `boards/<BOARD>/sdkconfig.board` is the one store, prose and all, and no script
  restates any of it. Each `build.sh` used to hand the guard a hand-typed subset of its own
  fragment, where anything left out silently did nothing on a warm build tree — `082fb9e` hit
  exactly that, twice in one commit. `moybyte_sdkconfig_guard <board_def_dir> <generated_sdkconfig>`
  replaces it: a fingerprint over the fragment + `mpconfigboard.cmake` + `MPY_TAG` decides whether
  the tree is stale (so a DELETED line counts too, and so does a change to which upstream fragments
  the board pulls — `CONFIG_SPIRAM_MODE_OCT` is MicroPython's value, not ours), and only once that
  matches does it check the generated config, where a missing option can now mean one thing:
  Kconfig REFUSED it. That case prints the fragment's own comment block and fails under
  `CI`/`MOYBYTE_REQUIRE_SDKCONFIG`. It caught a live one immediately —
  `CONFIG_BT_CTRL_BLE_ADV_REPORT_FLOW_CTRL_NUM=20` is below IDF's `range 50 1000`, so both S3
  boards silently kept 100 and the saving that commit describes never happened (now 50). A
  disable -- `CONFIG_X=` or the idiomatic `CONFIG_X=n` -- is fingerprinted but never grepped: a
  disabled bool renders "is not set" and a hidden choice member is absent from a generated config
  entirely, so a grep for either false-alarms (the `=n` spelling joined 2026-08-25 after
  `CONFIG_BT_HCI_LOG_DEBUG_EN=n` failed every CI p4 build while local builds only warned). The partition CSV is likewise named once,
  in `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME`, and exported as `BOARD_PARTITION_CSV`. Full codegen
  of the fragment stays DECLINED — `docs/board_ports_2026-08.md` carries that entry and the
  reasoning. **The cable-flash facts are board.toml data too** (`[flash]`/
  `[monitor]`, read by `tools/board_flash.py`, #202 Phase A): image path, offset, baud, the otadata
  region (T-Deck 0x1d000, P4 0xd000 — erased FIRST so a board that has OTA'd boots the slot the
  flash just wrote) and the reset strategy (the T-Deck declares `usb_reset` — measured, its
  USB-Serial/JTAG write-times-out under `default_reset` when wedged); the Makefile flash/monitor
  targets are two lines and the CI matrix is one include-row per board. **The serial-console facts
  are board.toml data as well** (`[serial]`, read by `tools/push_cart.py`): the line state at open
  (asserted on the two SoC-USB S3 boards, low on the P4's CH343), whether the board may be reset at
  all (`attach_only`), and the upload chunk (256 on the P4's UART — its stdin ring has no flow
  control and 768 corrupts silently, measured 2026-08-19), which is what lets **ONE cart-push tool
  serve every board**: `python tools/push_cart.py <cart.moy> --board tdeck|p4|guition_s3` (the names
  are the board files' own `[board] ota` ids, required on purpose — a default would be a silent
  wrong transport) copies a cart folder onto the live console's store, whose path is DISCOVERED from
  `ws.carts_root` rather than declared (the Guition's is a TF card when one is in the slot and the
  internal VFS when not). **The frame loop is shared
  too** (#202 Phase B, `device_boot.FrameLoop`): the invariant order — inputs → dev channel → idle
  tick → pointer → present → frame → backlight gate → pump.tail → tail → pace — lives ONCE, pinned
  by order tests in `tests/test_device_boot.py`; each board's `run_desktop` supplies
  `poll_inputs`/`present`/`tail`/`account` hooks and its hardware. The GT911's no-news contract
  (hold / stale-mark / bound) is one copy in `device/gt911.py` (#202 Phase C). Images land in
  `dist/tdeck_mainline/` (gitignored), and an oversized one is a BUILD FAILURE on every board.

- **THE lvgl_micropython FORK IS DELETED (2026-08-17).** The T-Deck ships the mainline port, which
  measured FASTER on the Bench referee — the gap is in the console FLOOR, not the raster: per-op verb
  costs are identical and it shows on `idle` as much as `draw` (numbers in #66) — and
  is the only build whose serial dev channel works. What used to live in that board directory and was
  never board-specific now sits at the repo root, so no board reads a sibling board's tree:
  **`native/`** (the shared C modules), **`patches/`** (the IDF/MicroPython patches), **`device/`**
  (the device tier every board stages — `device_canvas`, `device_api`, `device_diag`, `moy_ota`,
  `moy_webhost`, `moybyte_sd`, `moycore_glue`, the `moybyte` package …). LVGL is gone with it; the
  panel comes up through `native/moy_lcd` + `modules/tdeck_panel.py`, and that is now the ONLY panel
  driver in the tree. `patches/` was pruned to its three consumers on 2026-08-17: five orphans were
  DELETED (git history has them) — `esp32_i2c_new_driver` (reachable only through the fork's knob,
  the #69 decision), `esp32_repr_c_floats` + `esp32_i2c_gil_release` (both live on as build.sh's
  guarded sed/heredoc, steps 3b/3c — the patch files were unapplied second copies),
  `esp32_tdeck_early_board_init` and `spi_master_psram_tx_dma` (fork-only mechanisms; the mainline
  flush never DMAs from PSRAM).
- The MicroPython console is the only firmware. (The older Arduino/PlatformIO serial-smoke firmware and the legacy LVGL `.moyproj` game-loop boot path were removed; git history has them.)


### Second device target: ESP32-P4 (Waveshare 7B) — bring-up (#58)

- `firmware/esp32_p4_wifi6_touch_lcd_7b/` — the desktop-tier board (7″ 1024×600
  MIPI-DSI, GT911 touch, C6 WiFi over SDIO, 32MB PSRAM/flash), mainline
  MicroPython v1.28 with an out-of-tree board def (`boards/MOYBYTE_P4`).
  **Read that dir's README before touching this board** — it holds the
  hardware-learned constraints. Build/flash/monitor: `make firmware-build-p4` /
  `make firmware-flash-p4 PORT=…` / `make firmware-monitor-p4 PORT=…`; serial is
  a CH343 and the REPL stays alive.
  - **`native/moy_dsi` scans, it does not push.** DPI mode: the DSI peripheral
    scans a PSRAM framebuffer continuously, so there is **no per-frame flush** and
    the T-Deck's tx_color ceiling does not exist here. This board denies
    `moy_flush` in board.toml for that reason.
  - **The hardware rules that cost sessions** (the README is the authority): SD
    power comes from the internal LDO4 that stock MicroPython never enables;
    SDMMC slot 1 belongs to the C6 and claiming it panics the board; PSRAM must
    run at 200MHz or the DSI scan-out underruns; WiFi needs no C6 flash; and a
    root-level VFS dir named like a frozen module SHADOWS it — which is why the
    store root is `/moy/carts`, never `/moybyte/...`.
  - **The game composite runs on the hardware PPA with an async overlap**: a quiet
    frame composites via `moy_ppa.blit_async` and DEFERS the scan-out switch to the
    next loop's `present_pending()`, so the DMA overlaps the loop tail and the
    input poll. Full paints stay blocking so chrome never races the DMA. **An async
    PPA op must be the frame's LAST write**, and `moy_ppa` must C2M-writeback dst
    before submit, because the IDF PPA driver invalidates the whole out buffer at
    submit and would discard unflushed CPU writes.
  - **The PPA only helps UPSCALE composites.** A full-screen 1:1 copy (the backdrop
    restore) is ~identical CPU vs PPA, PSRAM-bandwidth-bound against the scan-out;
    and **sprite BATCHING is a dead end** (~10× worse than `spr_batch` — per-op
    submit dwarfs a tiny blit). Both stay on the CPU.
  - **The PPA scaler is fixed BILINEAR in silicon**, so pixel art composites
    smeared. Settings → CRISP PIXELS (default OFF, capability-gated, serial
    `crisp 0|1`) reroutes through `moy_ppa.blit_crisp` — a banded internal-SRAM
    bounce, byte-exact against the CPU kernel. Ledger: #204.
  - App-window drags use the **dirty-union restore** with a body-subtract trail and
    a deferred content stamp; resize is live-body. The **triple framebuffer**
    shipped; the **double game canvas was built, measured and REVERTED** (`26e1f9f`
    — the game fence was already ~free and the retention memcpy cost more than it
    saved), and #159's L2 cache 128→256KB closed the game chapter (512KB does not
    boot — internal/DMA pool 0x101).
  - Status and numbers: **#58**. Open: USB-HID keyboard, audio (ES8311).


### Fourth build target: the Zero (Seeed XIAO ESP32-S3) — HEADLESS (#41)

`firmware/seeed_xiao_esp32s3_zero/` became a build target on **2026-08-29**
(owner call, reversing its own "DELIBERATELY NOT A BUILD TARGET"; its board.toml
records the reversal). No panel, no touch, no frame loop, and no cart RUNTIME —
the browser is the console and this board is the store behind it. **That dir's
README is the authority**; what belongs here is only what bites:

- **It is the shape a port takes when the checklist's stages 1-6 are all
  absent** — no panel, touch, input, storage or audio to bring up, and nothing
  for `run_desktop`/`FrameLoop`/an on-glass suite to construct. Stage 0 is the
  whole port.
- **8MB of flash: the console table does not fit, and the bootloader rejects an
  oversized one into a silent boot loop** — on a board with no screen that is
  indistinguishable from dead. Its CSV is authored, not inherited.
- **It carries the seed roster COMPRESSED, and it is the only board that does**
  (2026-08-30). `tools/gen_device_carts.py --packed` emits `CARTS_Z` — the same
  carts as one raw-deflate stream each, 202 KB against the plain form's 732 KB
  of source — and `moy_carts.seed_packed()` inflates them ONE CART AT A TIME
  into an **empty** store on first boot. The gate is emptiness and not #47's
  version compare, because this board's store is the RECORD (the only copy of a
  browser-made cart, with a journal behind it) where a console board's is a
  cache. Until that day the board shipped with no carts and the roster arrived
  over a USB cable, or never — which the website's flasher made a real product
  gap.
- **Its patch ladder is empty and says so** (`# DECLINED <fn>` per patch, which
  is the mechanism `moybyte_patch_repr_c`'s header already specified). Do not
  give it the #169 retune without the 120MHz profile; the spike suite refuses
  that pairing.
- **It keeps TinyUSB CDC** rather than the #201 promotion (which exists for a
  board that never returns to the REPL; this one is interrupted into it on every
  provision). So `303a:4001`, and DTR must be asserted at open. In via
  `machine.bootloader()`, out via `--after watchdog_reset`, never `hard_reset`.
- **A pushed `.py` SHADOWS the frozen one** — `/` is searched before `.frozen` —
  so its module push is opt-in and undoable, and the board announces it at boot.


### Hard device constraints (learned the painful way — respect these)

- **SD shares the SPI host with the display, and getting it wrong HANGS the
  board** — gray screen, dead USB, no panic. Three rules, each learned on
  hardware:
  - **Nothing touches SD before the panel is up** (#56). A pre-display mount
    re-runs `spi_bus_initialize()`, and on a POPULATED card it succeeds while
    leaving the shared host claimed, so the next `init_display()` intermittently
    failed — the "no-SD boots, SD-with-files doesn't" bug.
    `PREFETCH_SD_BEFORE_DISPLAY=False`; carts load after init and degrade to the
    built-ins on any SD failure.
  - **After the panel is live, never `machine.SDCard`** — `esp_lcd` and that
    driver fight over one host and a CS-deselect is not enough. Live reads and
    writes go through the native `moy_sd` ATTACH (`sdspi_host_init_device`, no bus
    re-init — the ESP-IDF "Sharing the SPI Bus" pattern), which leaves the panel
    device intact. `moybyte_sd.with_sd_live(fn)` mounts once and keeps the card
    RESIDENT for the session.
  - **Do not tear the SD device down between ops, and do not touch the CS pins.**
    A per-op `sdspi_host_deinit`, or reconfiguring `TFT_CS` via `Pin(...)`,
    corrupts the shared bus/DMA state and the NEXT PANEL FLUSH silently hangs the
    board — the write itself lands, then resume freezes. Leave `TFT_CS`/`SD_CS`
    alone (driver-owned); park only the unused LoRa `RADIO_CS`. Never flush the
    panel inside a session: the loop is single-threaded, so SD ops run between
    frames. (`tests/test_moybyte_sd.py` pins which lifecycle touches which pin.)
- **T-Deck serial RX WORKS, and the fix was three things at once (#201, 2026-08-16).** TX always
  streamed (PERF/HITCH lines flow for hours). RX did not, and the explanations in this file were
  wrong twice: first "this fork's USB-CDC stack has no at-arrival interrupt-char scan" (false —
  `tud_cdc_rx_cb` is linked and does scan), then micropython#18581's "CDC only initialises at the
  REPL" (true of CDC, but not the reason — the image had NO stdin path at all). `nm` settled it:
  `tud_cdc_rx_cb` present, `tusb_init` ABSENT, `usb_serial_jtag_isr_handler` ABSENT, and stdin bound
  to `uart_stdout_init` on U0RXD — a header pin with nothing attached. Bytes written to the
  enumerated interface were accepted by the host stack and dropped.

  The mainline port fixes it with three changes that are only sufficient TOGETHER, which is why each
  was measured as a failure on its own:

    1. `MICROPY_HW_ENABLE_USBDEV (0)` — `MICROPY_HW_USB_CDC = USBDEV` forces
       `MICROPY_HW_ESP_USB_SERIAL_JTAG` to 0 on the S3 (`SOC_USB_OTG_PERIPH_NUM == 1`), compiling out
       the ISR that fills `stdin_ringbuf`. It also gives MicroPython its own TX.
    2. USB-Serial/JTAG as the **PRIMARY** ESP-IDF console. A SECONDARY console is output-only by
       design, so input was never possible while it was secondary.
    3. `MICROPY_HW_ENABLE_UART_REPL (0)` — UART0 shared the ringbuf, and its floating pin is where
       every `SERIAL rx=1` stray byte came from.

  (2) alone HANGS the board: with USBDEV still on, `mp_hal_stdout_tx_strn` falls through to IDF's
  blocking primary console. So it needs (1)'s non-blocking `usb_serial_jtag_tx_strn`, which gives an
  absent host one 200ms timeout then latches `terminal_connected = false`.

  **The fork could not be fixed this way and was never made to work.** Its `MOYBYTE_REPL=jtag` mode
  had three independent bugs (documented in the deletion commit); with all three fixed it boots and
  PRINTS but still takes no input, on an identical console config and identical linked symbols. The
  remaining difference is the MicroPython base itself. The fork is gone, so this is history, not a
  TODO.

  **Do NOT use the USB product id as the RX tell** — the old note said `303a:1001` = RX dead,
  `303a:4001` = RX works. On this port a WORKING board enumerates `1001`, because that is the
  USB-Serial/JTAG peripheral doing its job. `4001` means TinyUSB CDC, which is now the arrangement
  that does NOT take input here.

  Flashing: esptool works, but `write_flash`'s own trailing reset does not start the app — a SEPARATE
  `esptool --before default_reset --after hard_reset` does, so no human reset is needed between flash
  and boot. The ROM-loader entry by hand (**hold the trackball in — it is GPIO0 — while powering on**)
  is still the recovery path when an image wedges the USB device.

  Serial reads are unreliable ACROSS a reset: the device node is torn down under an open handle, so a
  reader that opens too early sees zero bytes and looks exactly like a dead board. Three separate
  "the board is silent" conclusions in one session were this. Read with miniterm, or open after the
  boot settles.

- **Full-screen flush must be a single `tx_color`** from a PSRAM DMA buffer; multiple `tx_color` calls glitch rows at the command→data boundary.
- **The keyboard has two modes; the console flips between them per screen.** The T-Deck keyboard is a separate ESP32-C3 (I2C 0x55; firmware in `firmware/lilygo_t_deck_plus_reference/examples/Keyboard_ESP32C3` — an UNTRACKED vendor reference tree, so a fresh checkout will not have it; THIRD_PARTY.md's scope note explains why). In its default mode it returns clean 1-byte ASCII (shift→uppercase, sym→symbols/digits, all resolved on-keyboard) but reports each key **once on the press edge with no autorepeat** — so a *held* key can't be detected, only faked for `KEY_HOLD_MS` by `TDeckKeyboard`'s latch (movement stalls while you hold). For true hold-to-move, a running cart switches the keyboard to **raw-matrix mode** (`0x03`, `LILYGO_KB_MODE_RAW_CMD`): it then streams the full key matrix each read, so a held direction keeps firing. `Workstation._set_text_mode` → `TDeckKeyboard.set_game_mode(on)` drives this: ASCII for the code editor (so typing is clean — `last_key`), raw everywhere else. The revert is `0x04` (`..._MODE_KEY_CMD`) — the step an earlier attempt missed, which is why raw mode used to garble the editor *irreversibly*. **`__init__` boots in ASCII and never enables raw**; raw needs keyboard fw **≥ 2025-06-12** (`T-Keyboard_..._250620.bin`), and on older fw the `0x03` is ignored — `_read_raw_buttons` detects the stray ASCII byte and sticks the session back on the 1-byte + latch path (`_raw_unsupported`; class flag `RAW_GAME_MODE` force-disables raw). The keyboard has **no `=` `[ ] { } < > %`** keys at all → the code editor shows an on-screen symbol palette for those. (`0x01 <duty>` over I2C sets the keyboard backlight.) Use `RUN_KEYBOARD_PROBE` to dump keys over serial (USB-friendly, no takeover).


### Device module map

The T-Deck's own board code is `firmware/lilygo_t_deck_plus_mainline/modules/`
(six tracked files — the rest of that directory is STAGED at build and
gitignored). Everything both boards share moved to the repo root when the fork
went: the device tier is **`device/`**, the C modules **`native/`**.

- `moybyte_shell.py` — boot/`main()`; mode flags `RUN_DESKTOP` / `RUN_TOUCH_CALIBRATE` / `RUN_KEYBOARD_PROBE` (the STAGE3/NATIVE_CORE bring-up benches and the pre-display SD-prefetch A/B toggle were removed; the #63 `MOYBYTE_BENCH=1` build is the benchmark harness).
- `moy_runtime.py` — the **device backend**: `DeviceCanvas` (hot ops `cls`/`rect`/`circ`/`spr` go through the native `moy_gfx` kernel — `fill`/`fill_rect`/`blit565` straight into the compositor's RGB565 buffer — with framebuf for text/lines and as the no-`moy_gfx` fallback; `spr` blits a per-sprite pre-scaled RGB565 cache, and `make_api` reuses one tile `Image` per `(id, colorkey)` so the cache survives across frames), `make_api`, embedded fallback `CARTS`, `TrackBall`, `Touch`, `run_desktop()`, `run_keyboard_probe()`. Imports the shared `console`/`editors`/`moy_carts` and injects the device `make_api` + store into `console.Workstation`. **Input runs on a poller thread (#69, `MOY_INPUT_POLLER`)**: `moybyte.input.InputPoller` owns every I2C0 transaction (kbd + GT911 + mode switches) off the frame loop, so the C3's 40-60ms clock-stretch stalls block only that thread — requires the build's `esp32_i2c_gil_release.patch` (machine.I2C frees the GIL across its blocking wait); falls back to synchronous polling if `_thread`/the thread dies.
- `console.py` / `project.py` / `player.py` / `editor_app.py` / `wm.py` / `editors.py` / `moy_carts.py` (+ the `*_layer.py`/`*_ui.py` surfaces and `blocks.py`) — **staged from `runtime/` at build** (see above).
- `device/moybyte_sd.py` — SD mount on the shared SPI bus; `with_sd(fn)` = mount → run → unmount + deselect.
- `tdeck_panel.py` + `native/moy_lcd/` — the panel backend, replacing the fork's
  `tdeck_display.py` (LVGL bootstrap) and `moy_compositor.py` (Python banding).
  `TDeckCompositor` is the ping-pong + `ASYNC_FLUSH`/`LAYER_COPY_ASYNC` levers and
  the `bounce_stats`/`pump_last_us` meters; `moy_lcd` owns the ST7789, the banded
  flush and the `kick`/`pump`/`drain` protocol. The hard-won rules moved INTO the C
  with it — DMA only from internal SRAM, only the first band carries a command
  (what "a full-screen flush must be a single `tx_color`" really meant), and a band
  must fit one SPI DMA transaction, that last one as a compile-time assert. The
  #190 flush-bounce scale fold is BACK (2026-09) and is now shared C:
  `native/moy_flush/moy_fold` carries the latch, the fence and both boards'
  gathers, so a small-canvas play frame skips the root composite AND the root
  read-back on either S3. The 2026-08 decline ("it needs `moy_gfx` kernels
  writing into the bounce slots") was wrong about the shape — the synthesis
  runs on the FEEDER and no slot crosses into Python. The game WINDOW stays the
  Guition's: shipping the game rect alone needs a panel whose GRAM keeps the
  bezels. See `tdeck_panel.py`'s header and `moy_fold.h`.
- `device/moy_ota.py` — OTA firmware updater (#53): `OtaUpdater` flashes a new app image from `/sd/update/*.bin` into the **inactive** OTA slot via `esp32.Partition` (block-erase `writeblocks`), then `set_boot` + `machine.reset`. Phase 3 adds WiFi download — `check_online`/`begin_download`/`download_step` stream a manifest-described `.bin` over a raw socket straight to SD (sha256-verified, never buffering the whole 3MB), reusing the injected `wifi` service. Device-only; `run_desktop` injects it into the shared `Workstation` (which owns all the update-screen pixels), wires the wifi service, and calls `mark_valid()` at a healthy boot to cancel rollback.
- `device/moy_webserver.py` — the device **socket/HTTP/WebSocket transport core**. Until 2026-08-12 this was the device WEB VIEW (#41/#22, owner-verified once on-glass 2026-08-01, #182) — the streaming browser mirror. **The whole streaming stack was DELETED in the 2026-08 sunset** (`docs/history/moycore_plan_2026-08.md` §3.2, owner decision; `tests/test_streaming_sunset.py` pins the absences): the frame push, `device_webview.py`, the recording `TeeCanvas`, stream mode, the Settings WEB VIEW row, `ws.web_hook`, the host `tools/web_console.py` + its VM deploy recipe, and the decline-the-Tee guards in `moy_lua_glue`. The browser's job belongs to the **wasm head** (`firmware/web_runner`), to be synced per §3.4; mirror-of-glass is an accepted loss (a screenshot verb on the sync RPC was the recorded successor and was DROPPED, owner 2026-08-25 — the browser IS the console, so show-and-tell happens there). What survives here — deliberately, for the §3.4 sync RPC to ride — is the bare transport: non-blocking listener, `parse_request`/`http_response`, the RFC 6455 upgrade + framing (shared `web_view_ws`, the only file of that lineage the boards still freeze), one persistent non-blocking `_WSConn` (cross-iteration read buffer, blocking-budget sends, idle reaper), and a `WebServer` with `handle_http`/`on_text`/`send_text` seams, no consumer wired. **The recording stack is GONE as of stage 4** (2026-08-12): the wasm head rasterizes, so `runtime/web_view.py` and `runtime/web_view_page.py` were deleted outright with the recorder, CommandCanvas, RecordingLayer, ServedState, SurfaceDelta, WsClientState, the wire protocol and the page's JS replayer. Two pieces of that module were never about rasterizing and survive on their own: `runtime/web_input.py` (browser events → InputState/Pointer, which the §3.4 RPC also speaks) and `web_view_ws.py`. `runtime/surface.py` and `wm_windowed`'s `if not self._recording` guards deliberately STAY, unreachable — `docs/surface_model_v1.md` §13 records why, and is the place to argue with it. The XIAO Zero port stood entirely on the deleted stream; the owner re-based it the next day (plan §3.2): the browser runs the wasm head, and the Zero becomes the pocketable cart-store + GPIO peripheral it pairs with (#41 direction, #9 pins) — its rebuild rides the §3.4 track.

