---
paths:
  - "firmware/**"
  - "device/**"
  - "native/**"
  - "tools/board.py"
  - "tools/board_*.py"
  - "tools/p4_*.py"
---

<!-- Boards: the constraints that hang one, and the decisions not to redo. -->

Each board dir's README is the authority on its hardware; the procedures —
ports, flash, push, reboot, screenshots — are the `on-glass` skill
(`tools/board.py`). What follows is what bites whoever touches `firmware/`,
`device/` or `native/`.

## One build system, five boards

- **The shared half of every `build.sh` is `tools/esp32_build_lib.sh`**: the
  toolchain and its self-heal, IDF components, native staging, the web blob
  (generated into the STAGED copy — a build never writes into `native/`), the
  OTA identity stamp from `device/moy_ota.py`, the frozen manifest and its
  fingerprint, the stale-sdkconfig guard and the #168 size guard. What stays per
  board is its patch ladder and its sdkconfig facts. Builds clone mainline
  MicroPython v1.28 and ESP-IDF v5.5.1 into the board's `.build/`, and an
  oversized image is a build FAILURE on every board.
- **What crosses into an image is board.toml DATA** (#161), staged by
  `tools/board_config.py`: a denylist over `runtime/*.py` (each `[[deny]]` names
  a kind and a `why`), an allowlist over `device/*.py`, and a
  `[native.shared]` denylist over `native/` (a P4 board also declares
  `[native.p4]`). A new shared module reaches every board by default; staying
  off one is a written decision. The headless Zero inverts the runtime default
  (`strategy = "allowlist"`, one group per capability), and which shape a board
  uses is declared (`board_config.shared_strategy`) and pinned both ways. `make board-modules BOARD=...` answers
  what crosses; `tests/test_staging_closure.py` derives every frozen set.
- **The stager prunes untracked files** from the gitignored `modules/` tree the
  freeze takes whole, so a new board module is `git add`ed (and whitelisted in
  `.gitignore`) before its first build.
- **A board's sdkconfig facts are DATA the build reads**:
  `boards/<BOARD>/sdkconfig.board`, prose and all, and nothing restates it.
  `moybyte_sdkconfig_guard` stamps the fragment, `mpconfigboard.cmake` and
  `MPY_TAG` (so a deleted line counts), and only on a matching stamp greps the
  generated config — where a missing option means Kconfig REFUSED it, reported
  and fatal under `CI`/`MOYBYTE_REQUIRE_SDKCONFIG`. It caught
  `CONFIG_BT_CTRL_BLE_ADV_REPORT_FLOW_CTRL_NUM=20` below IDF's `range 50 1000`
  on both S3s (now 50), after `082fb9e` had twice fed the old guard a
  hand-typed subset. A disable (`CONFIG_X=` or `CONFIG_X=n`) is fingerprinted,
  never grepped: a disabled bool renders "is not set" and a hidden choice
  member is absent from a generated config, so a grep false-alarms (the `=n`
  spelling joined 2026-08-25, after `CONFIG_BT_HCI_LOG_DEBUG_EN=n` failed every
  CI P4 build while local builds only warned). Full codegen of the fragment is DECLINED
  (`docs/board_ports_2026-08.md`).
- **Flash and serial facts are board.toml data too**: `[flash]`/`[monitor]`
  (image, offset, baud, the otadata region erased FIRST so a board that has
  taken an OTA boots the slot just written, and the reset strategy — the T-Deck
  declares `usb_reset`) for `tools/board_flash.py`; `[serial]` (the line state
  at open, `attach_only`, the `py` chunk, the `recv` window) for `P4Board` and
  `tools/push_cart.py`. No tool restates them, and the CI matrix is one row a
  board.
- **The frame loop is shared** (`device_boot.FrameLoop`, #202 Phase B): inputs
  → dev channel → idle tick → pointer → present → frame → backlight gate →
  pump.tail → tail → pace, once, pinned by order tests in
  `tests/test_device_boot.py`. A board's `run_desktop` supplies hooks and its
  hardware. The GT911's no-news contract (hold / stale-mark / bound) is one copy
  in `device/gt911.py`.
- **Never reset ONE patched file in a board's `.build/micropython` by hand.**
  `moybyte_patch_native_code_free` patches the esp32 port's `main.c` and
  `mpconfigport.h` as one unit and keys "already applied" on the header; a
  `git checkout` of the header alone (2026-09-23) left `main.c` patched, a
  `.rej` behind, and the build stopped before REPR_C re-applied. Reset the two
  together, delete any `.rej`, rebuild: every patcher is idempotent from stock.
- **Build the two P4s one at a time.** They share the component manager's git
  cache (`~/.cache/Espressif/ComponentManager`) and race on its `index.lock`:
  "Unable to create index.lock: File exists" (2026-09-22), and a retry passes.
  The two S3s build side by side.

## Settled

- **The lvgl_micropython fork is DELETED (2026-08-17).** The mainline port
  measured faster on the Bench referee — the gap was the console floor, not the
  raster (numbers in #66) — and is the only build whose serial dev channel
  works; the fork's `MOYBYTE_REPL=jtag` mode, with its three bugs fixed, printed
  but never took input on identical config and symbols. A recorded verdict, not
  a TODO. LVGL went with it: `native/moy_lcd` + `modules/tdeck_panel.py` is the
  only panel driver, and what was never board-specific lives at the root
  (`native/`, `patches/`, `device/`). The MicroPython console is the only
  firmware; the Arduino smoke firmware and the LVGL `.moyproj` boot path are in
  git history.
- **Do NOT use the USB product id as the T-Deck's RX tell**: `303a:1001` IS
  the working arrangement (USB-Serial/JTAG as the primary console), and CDC's
  `303a:4001` is the one without input. The board's README tells the
  three-part fix.
- The P4's PPA verdicts: it only helps UPSCALE composites (a 1:1 full-screen
  copy is the same CPU or PPA, PSRAM-bound against the scan-out); sprite
  BATCHING on it is a dead end (~10× worse than `spr_batch`: per-op submit
  dwarfs a tiny blit); its scaler is fixed BILINEAR in silicon, so pixel art
  goes through Settings → CRISP PIXELS (`moy_ppa.blit_crisp`, a banded SRAM
  bounce byte-exact against the CPU kernel, #204). The triple framebuffer
  shipped; the double game canvas was built, measured and REVERTED (`26e1f9f` —
  the game fence was already ~free and the retention memcpy cost more); #159's
  L2 cache 128→256 KB closed the game chapter (512 KB does not boot — the
  internal/DMA pool, 0x101).
- **Do NOT give the S3 VM more internal SRAM**: lowering the floor 48 → 16 KB
  made the tick SLOWER (the drivers starve). The VM's win in internal memory is
  instruction placement (`MOY_HOT`); its allocator is a small-object pool
  (`native/moycore/README.md`).

## Hard constraints — each one hung or bricked a board

- **Line state at open is per board and OPPOSITE** (`[serial]`). The Waveshare
  P4's CH343 rests both lines LOW, and the open ORDER matters: the kernel raises
  both on open and pyserial lowers DTR first, and RTS-high-DTR-low is the
  auto-reset EN pulse — a plain open power-cycles the board into a 60 s boot.
  `P4Board` opens with DTR raised, drops RTS, then DTR (`1725d40`). Every other
  board's USB serial is on the SoC: open with both HIGH, because both LOW is a
  chip reset under the open handle, after which every read returns nothing,
  forever. The console boards among them are `attach_only`: never pulsed,
  reset through esptool.
- **On the S3 boards the Python heap and the C side share one 8 MB PSRAM**, and
  the Python heap grows by doubling and never shrinks.
  `MOYBYTE_GC_SPLIT_RESERVE` (each board's `mpconfigboard.h`, applied by
  `moybyte_patch_gc_split_reserve`) keeps 3 MB outside it; without it one
  fragmented allocation takes every byte and every big cart fails to load until
  a hard reset.
- **The WiFi radio is a LEASE (2026-09-07)**: off unless something holds it.
  `Workstation.wifi_hold(tag)` powers the STA up and the last
  `wifi_release(tag)` powers it down (`esp_wifi_stop`), which keeps the S3's
  WLAN-vs-LCD-DMA internal-RAM fight to when the network is needed. The holders
  are `web` (released when the webhost's socket closes), `update` (taken before
  the hand-off releases `web`), `settings` (the WIFI panel), `cart` (a run with
  the "network" permission) and `link` (a match; taken before `link.start()`).
  A new network consumer takes a tag and releases it on every way out, or the
  radio never goes off again — `tests/test_wifi.py`'s lease section is the
  guard and `state`'s `wifi_held` names the holders. Constructing `network.WLAN`
  initialises the driver, so `radio_off` never constructs one. The Zero is
  outside this: WiFi is its only I/O.
- **SD shares the SPI host with the display, and getting it wrong HANGS the
  board** — gray screen, dead USB, no panic:
  - nothing touches SD before the panel is up (#56): a pre-display mount
    re-runs `spi_bus_initialize()` and leaves the host claimed on a populated
    card (`PREFETCH_SD_BEFORE_DISPLAY=False`; carts load after init and fall
    back to the built-ins on any SD failure). `moybyte_sd` has one lifecycle;
  - after the panel is live, never `machine.SDCard`: live SD goes through the
    native `moy_sd` ATTACH (`sdspi_host_init_device`, no bus re-init), and
    `moybyte_sd.with_sd_live(fn)` mounts once and keeps the card RESIDENT —
    once resident a session is `fn()` and nothing else, not even an `import`
    (`test_the_resident_session_imports_nothing`);
  - never tear the SD device down between ops, never re-create a `Pin` on
    `TFT_CS`/`SD_CS` (driver-owned; park only the LoRa `RADIO_CS`), never flush
    the panel inside a session. `tests/test_moybyte_sd.py` pins which lifecycle
    touches which pin.
- **On the S3 boards a play frame can skip the root canvas.** The #190 fold
  (`native/moy_flush/moy_fold`, shared C: the latch, the fence and both boards'
  gathers) scales a small game canvas into the panel bands on the feeder,
  skipping the root composite and its read-back, and no bounce slot crosses
  into Python. A compiled cart's frame folds straight from the cart's memory
  (blit's palette or blit565's byte swap resolved per band), so no canvas holds
  it; the P4s keep the blit, because a windowed desk re-composites the game
  canvas while the cart is not running. The game WINDOW is the Guition S3's
  alone — it needs a panel whose GRAM keeps the bezels. `tdeck_panel.py`'s
  header and `moy_fold.h` are the authority.
- **The panel rules live in `native/moy_lcd`'s C**: DMA only from internal
  SRAM, only the first band carries a command (what "a full-screen flush must
  be a single `tx_color`" meant), and a band fits one SPI DMA transaction — a
  compile-time assert.
- **The T-Deck's input poller is paced by the FRAME, never a timer.**
  `moybyte.input.InputPoller` owns every I2C0 transaction off the frame loop
  (#69; needs build.sh's `machine_i2c.c` GIL-release patch, and falls back to
  synchronous polling without `_thread`). It blocks on a lock `_poll_inputs`
  releases once a frame (`poller.kick()`), then yields with `_sleep_ms(0)`. A
  thread that sleeps re-takes the GIL every tick and starves under a
  free-running cart: on 2026-09-23 a key was read twice a second under Brick
  Siege, and lockstep netplay shipped that stale mask. Both halves of the
  handoff are load-bearing.
- **The T-Deck keyboard (a separate ESP32-C3) switches mode per screen**:
  ASCII for text, raw matrix for games.
  `_disable_raw_mode` drains after the `0x04` revert and a text surface seeds
  its edges with the byte already held — the README has the mechanism.
- **Serial reads are unreliable ACROSS a reset** on the SoC-USB boards: a reset
  removes the device node from under an open handle, and a reader that opens
  early sees nothing, which reads as a dead board. Attach after the boot
  settles (`tools/board.py X wait`).

## The P4 boards

- **`native/p4/` is the shared P4 silicon tier (2026-09-06)**: `moy_dsi`
  (parameterized by the board's `MOY_DSI_PANEL_*` define), `moy_ppa`,
  `moy_ble_hid`, `moy_c6`, declared by a P4 board as a second `[native.p4]`
  source — never seen by the S3 scan — plus `device/dsi_panel.py` (the
  compositor; the board injects its backlight), `device/p4_canvas.py` and
  `device/p4_desktop.py` over `device/desktop_spine.py`. The two P4 patches
  are `patches/p4_*.patch` behind `moybyte_patch_p4_ble_hid_fastpath` /
  `moybyte_patch_p4_dsi_underrun`. Status and numbers: #58, #202.
- **`moy_dsi` scans, it does not push**: DPI mode reads a PSRAM framebuffer
  continuously, so there is no per-frame flush (the P4 boards deny
  `moy_flush`). SD power, the C6's SDMMC slot, the 200 MHz PSRAM floor and the
  VFS shadowing rule (the store root is `/moy/carts`) are the Waveshare
  README's.
- **An async PPA op must be the frame's LAST write**, and `moy_ppa` must
  C2M-writeback a CPU-painted destination before submit, because the IDF
  driver invalidates the out window at submit. Both are ROW-SCOPED
  (`pic_w * block_h` from `block_offset_y`), so an op walks the rows it lands
  on; a destination the CPU never writes skips the writeback (`rotate`'s `wb`
  flag). Full paints stay blocking so chrome never races the DMA.
- **The Guition P4's desk is LANDSCAPE on PORTRAIT glass**
  (`device/dsi_panel.RotatedCompositor`, owner call 2026-09-06, pinned by
  `tests/test_p4_display.py`): one persistent landscape buffer the PPA rotates
  onto the panel, with per-buffer stale rects so a ping-pong buffer is never
  shown behind. Up is `guition_p4_display.ROTATION` (90/270), live as
  `py comp.set_angle(270)`. Its GSL3680 touch is RAM-loaded
  (`device/gsl3680.py` uploads `modules/gsl_fw_jc8012.py` after every reset);
  its serial is the P4's own USB-Serial/JTAG (the S3 rules, and esptool needs
  no BOOT button); its backlight GPIO23 is active-HIGH where the Waveshare's is
  active-low; its C6 runs Guition's factory slave, so BLE works and ESP-NOW
  fails inert until the Waveshare's `c6_slave/` image is flashed to it.

## The Zero (headless, #41)

A build target since 2026-08-29 (its board.toml records the reversal): the
browser is the console and this board is the store behind it, so a port with
stages 1–6 all absent. Its README is the authority; what bites:

- 8 MB of flash: the console table does not fit, and the bootloader rejects an
  oversized table into a silent boot loop. Its partition CSV is authored.
- It carries the seed roster COMPRESSED (`tools/gen_device_carts.py --packed`,
  `CARTS_Z`), inflated one cart at a time into an EMPTY store on first boot —
  gated on emptiness, not #47's version compare, because its store is the
  record (the only copy of a browser-made cart), where a console's is a cache.
- Its patch ladder is empty and says so (`# DECLINED <fn>`). Do not give it the
  #169 retune without the 120 MHz profile; the spike suite refuses the pairing.
- USB-Serial/JTAG since 2026-08-30: opens like the console S3s, reaches the ROM
  loader through esptool's default reset and leaves it with `--after
  watchdog_reset` (never `hard_reset`; `machine.bootloader()` loops forever on
  this chip). Holding BOOT while plugging in is the recovery. It has no dev
  channel, so `tools/board.py` knows it by the USB serial in its board.toml.
- A pushed `.py` SHADOWS the frozen one (`/` is searched before `.frozen`), so
  its module push is opt-in and undoable, and the board announces it at boot.
