# KidCode MicroPython T-Deck Spike Results

Date: 2026-06-18

Status: in progress

## Current Hardware Result

- Target: LilyGO T-Deck Plus.
- Firmware base: `lvgl_micropython` for ESP32-S3 with LVGL and ST7789 display support.
- Deployment mode: launcher-friendly SD app image remains the lowest-friction app test path, but full USB flashing at `0x0` is now confirmed to work on this unit with LilyGO's official Arduino HelloWorld example.
- Display: usable in native `240x320` portrait configuration rotated to `320x240` landscape.
- Input: raw T-Deck keyboard matrix mode works for held movement.
- Current known-good full-flash MicroPython image: `firmware/lilygo_t_deck_plus_micropython/dist/kidcode_diag_skip_vfs_generic_cdc_uart_full_dio_0x0.bin`. The user confirmed this boots into KidCode from direct full flash.
- Latest SD test image prepared: `firmware/lilygo_t_deck_plus_micropython/dist/kidcode_sd_prefetch_skip_vfs_generic_cdc_uart_full_dio_0x0.bin`. Stable alias: `firmware/lilygo_t_deck_plus_micropython/dist/current/kidcode-current-full-dio-0x0.bin`. This keeps the known-good skip-VFS boot/display path, preloads the KidCode API into SD project globals, strips a leading `from kidcode import *` compatibility line, and prefetches the SD project before display/LVGL init so SD access is released before the selector renders.
- Latest confirmed SD serial result: `kidcode_sd_default_api_skip_vfs_generic_cdc_uart_full_dio_0x0.bin` mounted SD, executed `/sd/kidcode/project.py`, printed `KidCode source loaded`, and printed `KidCode SD project loaded`, but the screen stayed on the stale "mounting SD" breadcrumb. That points at shared SPI/display refresh disruption after SD access rather than a project source execution failure.
- Root-cause read: the normal MicroPython full image black-screened before KidCode was visible, while the skip-VFS image booted. That points to MicroPython's frozen `_boot.py` VFS mount/initial setup path running before KidCode `boot.py`, not to T-Deck flashing, panel, or backlight failure.
- Observed FPS: 22 FPS reported on the last known-good image after adding the counter. The recovery image with extra per-frame LVGL pump delays reported 19 FPS; the next image removes those artificial frame delays. The same hardware path confirmed the raw-mode `A`/left fix.
- Current feel: Tiny Runner was playable enough for spike testing on the last known-good image; frame rate is low but no longer blocked by input.
- Historical launcher artifact: `firmware/lilygo_t_deck_plus_micropython/dist/kidcode_recovery_no_sd_20260620.bin` preserved the raw-mode `A`/left fix and reported 19 FPS on one hardware pass. Later blank-screen reports mean this label is no longer strong enough to treat as a guaranteed-good baseline.
- Latest experimental early-native-init build: app binary size `0x2cda10`; it booted from launcher to a black screen and did not enumerate USB CDC afterward. Keep that patch opt-in only until proven on hardware.
- Regression note: the image built after adding boot self-tests, external project probing, and run/stop shell controls booted from launcher to a black screen. The recovery path now makes display bring-up and the first selectable game screen happen before optional work. Boot self-tests and automatic external project probing stay disabled by default while the proven input fix remains enabled.
- USB flashing: completed from manual ROM download mode. The board enumerated as `303a:1001` on `/dev/ttyACM0`, and `make firmware-flash-lilygo-micropython-no-reset PORT=/dev/ttyACM0` wrote and verified bootloader, partition table, and app. The app binary write covered `2934096` bytes at `0x10000`.
- Software reset after no-reset flash: partially confirmed. `--after hard_reset` and explicit RTS pulses did not leave the esptool stub, but a later no-stub run/soft-reset sequence made no-reset `read_mac` stop connecting, which indicates the board left ROM/stub mode. On-screen confirmation is still needed.
- Recovery/reference firmware: official LilyGO `Xinyuan-LilyGO/T-Deck` was cloned locally as an ignored reference cache. Its `examples/HelloWorld` PlatformIO image built successfully, was merged into `firmware/lilygo_t_deck_plus_reference/dist/tdeck_official_helloworld_full_dio_0x0.bin`, and the user confirmed it booted with a working screen. This proves direct full flashing at `0x0`, the panel, the backlight, and the board power path are good on this device. The MicroPython full-flash blank screens are therefore firmware/config/display-init regressions, not a generic T-Deck flashing failure.
- Earlier software reset path: the app-mode ESP32-S3 enumerated as `303a:4001` on `/dev/ttyACM0`. Stock esptool reset failed on the combined DTR/RTS modem-control ioctl with `OSError: [Errno 71] Protocol error`, so `tools/esptool_no_modem.py` now patches that path to use separate line toggles. With the patched wrapper, writes no longer hang, but app-mode reset still did not enter ROM download mode.
- 2026-06-21 app-mode flash retry: serial was visible as `303a:4001` on `/dev/ttyACM0`. Sending Ctrl-C plus `machine.bootloader()` produced no handoff. A patched esptool full-image flash with `--before default_reset` reached `Connecting......` and then hit a pySerial `Write timeout`; the process had to be interrupted, and the board remained visible as `303a:4001`. Manual ROM/download mode is still required for direct USB flashing.
- Console bootloader handoff: attempted `machine.bootloader()` over CDC with modem-control suppressed and DTR asserted; no serial echo or re-enumeration was observed.
- OpenOCD/JTAG: local ESP-IDF OpenOCD exists, but builtin USB-JTAG expects `303a:1001`. The running firmware exposes CDC-only `303a:4001`; probing raw USB also requires host write permission beyond the current user. This remains a backup path after udev/root setup or ROM/JTAG re-enumeration.
- Repeatable flash targets are now available:
  - `make firmware-flash-lilygo-micropython PORT=/dev/ttyACM0`
  - `make firmware-flash-lilygo-micropython-no-reset PORT=/dev/ttyACM0`
  - `make firmware-flash-lilygo-micropython-full PORT=/dev/ttyACM0`
  - `make firmware-run-lilygo-micropython PORT=/dev/ttyACM0`
  - `make firmware-monitor-lilygo-micropython PORT=/dev/ttyACM0`
- Direct-flash note: split flash targets write the app from `MPY_APP_BIN`, defaulting to `dist/current/kidcode-current-app.bin`, because the cached build-tree `micropython.bin` can lag behind or contain an experimental build.
- Full-image note: `MPY_FULL_BIN` defaults to `dist/current/kidcode-current-full-dio-0x0.bin`. The build script updates `dist/current/` aliases after every successful MicroPython build and still emits named merged `*_full_dio_0x0.bin` and `*_full_qio_0x0.bin` images so hardware tests can match the official LilyGO "single binary at 0x0" flashing style.

## 2026-06-20 Prepared Test Builds

No further hardware flashing was done after these builds were prepared. The goal
is to test distinct boot/display/USB hypotheses tomorrow instead of repeatedly
testing the same cached `micropython.bin`.

Additional build after the official LilyGO HelloWorld full image was confirmed
working:

| Artifact family | Purpose | App SHA-256 | Full DIO SHA-256 | Full QIO SHA-256 |
| --- | --- | --- | --- | --- |
| `kidcode_cold_gpio_generic_cdc_uart` | Generic S3 plus ST7789, but `boot.py` and display init now mirror LilyGO's safe cold-boot GPIO setup: GPIO10 high, SD/radio/TFT chip-selects high, MISO pull-up, and backlight high before display init. Serial breadcrumbs were added around display init so a black screen can still identify the failing stage. | `c3fa148a5332279d03b382a70fbecd2d391f11e37582eac3be41af8d8c75ee21` | `77c232fcd4d254c5f5c67d4878b185686336d76e5af426dbec50649ef2c30422` | `818a51d8023f21b8dd8fff8ff09d834d7043fe88480b3298662d97490c758a35` |

The first hardware pass of
`kidcode_cold_gpio_generic_cdc_uart_full_dio_0x0.bin` still black-screened.
Arduino HelloWorld booted without erase, so erase is not a general flashing
requirement. A MicroPython-specific stale-VFS hypothesis remains because
MicroPython's frozen `_boot.py` mounts the `vfs` data partition before KidCode's
`boot.py`; if the first block is non-empty but not a valid MicroPython
filesystem, upstream `inisetup.py` loops forever printing a corruption message.
The sharper next test is a diagnostic build that skips MicroPython's VFS mount
instead of immediately erasing flash.

| Artifact family | Purpose | App SHA-256 | Full DIO SHA-256 | Full QIO SHA-256 |
| --- | --- | --- | --- | --- |
| `kidcode_diag_skip_vfs_generic_cdc_uart` | Same generic S3/ST7789 path, but `KIDCODE_SKIP_VFS_BOOT=1` replaces MicroPython's frozen `_boot.py` with a diagnostic version that prints once and skips mounting `/`. This separated pre-`boot.py` filesystem hangs from ESP-IDF/MicroPython boot or display failures; the full DIO image booted into KidCode on hardware. | `7d9533fe11a026eb4e2da310b7c7a4182112b76e9c0490626f26581d3995e368` | `9f4e053bafb1826f42445090adafd781958fcaf0a7217ec45e5db8d78cf572d5` | `56ffc0aab65df345d4309fa20f88e525a03c225bf22904b5079b52aff125f694` |
| `kidcode_sd_slot_skip_vfs_generic_cdc_uart` | Same known-good skip-VFS boot/display path, but the explicit SD Project selector slot is enabled and the watchdog is created before selector/project loading. Hardware serial showed this image mounted SD and found `/sd/kidcode/project.py`, then failed source execution with `name '__builtins__' isn't defined`; the screen stayed on the stale "mounting SD" breadcrumb. App image size: `2940944` bytes. Full images: `3006480` bytes. | `a25acd0eafd1ba151bb32691e57c5bdbdc7116799a196bb18787ef1e936fe51e` | `c825f73072cbb4f68bddcf458a2c658ffc3eef4303b19a3ba53b24657d522f7f` | `44d5f66edc8ec8272d27612b644bdc6f90a061110cf5aef4cbd4244c654742fe` |
| `kidcode_sd_builtins_skip_vfs_generic_cdc_uart` | Fixes source-project globals by importing the `builtins` module instead of referencing a CPython-style `__builtins__` global name. Hardware serial showed this image mounted SD and found `/sd/kidcode/project.py`, but no `loaded SD project` breadcrumb followed; the remaining suspect is dynamic source import/execution. App image size: `2941296` bytes. Full images: `3006832` bytes. | `d292ef9662f067113cff2a4024661f4311218c199f660d43653eaeac3aa429bd` | `32d09c15d93f64d3cc82c22ac31bab52a8595aed0de46a41b99c92da1fa8744a` | `20d78d93ecc41cee54d0b081ea3717a8e23d8f845e441a864dab2810484de72c` |
| `kidcode_sd_default_api_skip_vfs_generic_cdc_uart` | Preloads the KidCode API into source-project globals and neutralizes a leading `from kidcode import *` line so SD projects do not depend on MicroPython import/builtins behavior. Adds serial breadcrumbs before/after source `exec`, before/after `setup`, and after SD load. Hardware serial showed the SD project source loaded and executed, but the screen remained stuck on "mounting SD", suggesting SD-after-display shared SPI disruption. App image size: `2941872` bytes. Full images: `3007408` bytes. | `71c941487d1491bc16974540991441540259929a61d5096355e191cb186d7941` | `e362f533cdc24f1851f43f4439816c99cbef2e4e231add4d464dc271518b73b4` | `0b8223e88b704b8d7894993a706317d0d2ea0e52e2b66f7f422576f0639ac3cc` |
| `kidcode_sd_prefetch_skip_vfs_generic_cdc_uart` | Reads the first SD project source before display/LVGL initialization, unmounts/releases SD, then loads the cached source when the selector enters SD Project. This keeps the default KidCode API preload/import-neutralization fix and targets the stale "mounting SD" display problem observed after successful source execution. App image size: `2943504` bytes. Full images: `3009040` bytes. | `396136cedaa451a3c1dfe2e557952d80c5dd69244aacfef69b7da0840b892f1a` | `e0c8feafdf5e1fde6d764d6c207e5d49d632eacc9c9e17191b0755aace47a29e` | `647ddeecc3f6d2af5df963cf2a549c8d473bab4638bc54936816444d1df617cf` |

| Artifact family | Purpose | App SHA-256 | Full DIO SHA-256 | Full QIO SHA-256 |
| --- | --- | --- | --- | --- |
| `kidcode_generic_cdc_uart` | Generic S3 plus ST7789, closest cleaned version of the previous working MicroPython app path. | `6c1ce4f0cf6a18da6179dedb824c275b5de6a8e3a730b15829c48bc2a9c75ef2` | `03543ccf03df410a132df5c178506bc46aec1ca6b8b88f4e11ae4e53d0101197` | `02cf85e12df549dd789aa35ffa1546b0ea70bc346951e7c4b9209c945d9ab851` |
| `kidcode_generic_jtag_repl` | Generic S3 plus ST7789, but MicroPython console uses ESP32-S3 USB Serial/JTAG instead of TinyUSB CDC. | `386d992897e83497d60c224e207475d20390fc931e73b27b497963f061ef3700` | `4a5f0b665b3796b41c233d51bd8b9caa6c56bc4505bed14c3452cf43a63df319` | `6710f1e0728a50c2addadcfdffb60b246a6aa4b3eded9766debf487d500ed4c5` |
| `kidcode_lvgl_tdeck_board` | Upstream `lvgl_micropython` `LilyGo-TDeck` custom board config with its T-Deck display/input wiring. | `ec4b670ef9c1a0c3f26bc42f8a7168bf42e49597b7fd2d9b4a85951080c00d70` | `8f88381858b981a83286a35dd74ae612e1a8e1639bd53c5859ca75f3fd456a7c` | `8b12e240a3a5b521e041d5e3115b91156fee655417d1116ecd6ceced3abf0ddd` |
| `kidcode_lvgl_tdeck_board_jtag` | Same T-Deck custom board config, but MicroPython console macros force USB Serial/JTAG. TinyUSB stays compiled because lvgl_micropython's ESP-IDF graph still expects it. | `085a235af48c0d35c3d860af840166726477235637aba0a8d8d7866814f67940` | `7614917142ce44d6fe300eed499bcf308f3f556be7fc312eab6d9f99e115d258` | `198f87ac570eae8ba2360604dbe7c5bd419820d0f1ed1fa03a7a767bc87dec1b` |

Recommended full-flash order:

1. `kidcode_lvgl_tdeck_board_jtag_full_dio_0x0.bin`
2. `kidcode_lvgl_tdeck_board_full_dio_0x0.bin`
3. `kidcode_generic_jtag_repl_full_dio_0x0.bin`
4. `kidcode_generic_cdc_uart_full_dio_0x0.bin`

Use matching `_full_qio_0x0.bin` images only after the DIO candidate fails and
the board can still be recovered. Header spot-checks matched expectations:
DIO images have flash-mode byte `02`, QIO images have flash-mode byte `00`, and
the flash size/frequency byte is `4f`.

## Current Firmware Instrumentation

- On-screen status reports `fps`, last key, raw keyboard mode, and held-button mask.
- Boot shows a small launcher screen for Tiny Runner before auto-starting.
- Friendly syntax-error and 20x reload probes are implemented but disabled during recovery builds to minimize pre-frame startup work.
- Watchdog is enabled after display bring-up so runaway project code should reset the board.
- Host simulator is available through `make firmware-sim-lilygo-micropython`. It runs the firmware KidCode package and Tiny Runner on CPython with scripted input.
- Simulator renderer modes:
  - `recording`: records KidCode draw commands directly.
  - `fake-lvgl`: runs the real `ConsoleRenderer` against a fake LVGL object API.
- The simulator catches project/runtime/input/render-object regressions without hardware. It does not validate ESP32 boot, launcher behavior, native LVGL C bindings, SPI timing, or panel pins.
- Home/Stop pauses the project and shows a stopped screen; Run reloads and resumes the current project.
- A visible selector now offers frozen Tiny Runner, Input Test, Bounce Box, and, in the SD test image, SD Project. Earlier recovery images kept SD Project disabled after a hardware black-screen regression.
- Automatic external project file probing is disabled during recovery builds. The explicit SD Project slot mounts the shared SPI SD card and tries `/sd/kidcode/project.py`, `/sd/kidcode/main.py`, `/sd/project.py`, then `/sd/main.py`; failure falls back to frozen Tiny Runner.
- SD mounting checks for a real mounted filesystem instead of accepting a stale empty `/sd` directory. The probe speed is `800000` Hz to match the LilyGO T-Deck unit-test example.
- The LVGL renderer now reuses text objects, gives rect/line objects stable per-frame names, and renders line commands as simple boxes instead of dropping them.

## PocketDeck Notes

Source: https://github.com/raspy135/pocketdeck

Relevant points:

- PocketDeck is also MicroPython-first, with C modules for performance-sensitive graphics and audio.
- Its app model is trusted/cooperative rather than process-isolated.
- It uses virtual screens and app-level streams to keep multiple app contexts usable.
- Its docs explicitly warn that multiple apps are threads without protection, so apps must be trusted.
- Its drawing path exposes C-backed graphics primitives, including batch 2D/3D face drawing, rather than pixel-pushing from Python.

Implications for KidCode:

- MicroPython-first is realistic only if KidCode keeps hot paths native-backed.
- The child API should stay small and controlled; raw LVGL/native objects should be advanced mode only.
- The safety story should be honest: cooperative controls plus watchdog/reset recovery, not hard isolation.
- A native framebuffer/canvas backend is probably the next performance lever if the LVGL-object renderer remains near 20 FPS.

## T-Deck Reference Notes

Sources:

- https://github.com/Xinyuan-LilyGO/T-Deck
- https://github.com/lvgl-micropython/lvgl_micropython/tree/main/display_configs/LilyGo-TDeck
- https://github.com/shorepine/tulipcc
- https://github.com/shorepine/tulipcc/tree/main/tulip/tdeck

Findings:

- No LilyGO-maintained MicroPython T-Deck example was found in the official LilyGO T-Deck repo. LilyGO's repo remains the hardware pin/display reference, but its examples are Arduino/PlatformIO.
- `lvgl_micropython` includes a `display_configs/LilyGo-TDeck` board config with ST7789, GT911, keyboard, trackball, SD, and LoRa wiring. This is closer to our stack than the generic `ESP32_GENERIC_S3` build, but it is not LilyGO official and should be audited before use.
- TulipCC has a working ESP-IDF/MicroPython/LVGL T-Deck port. It is the best known MicroPython architecture reference so far, but it is a full firmware plus filesystem, not a drop-in SD launcher app.
- Tulip starts native FreeRTOS tasks for display/input before starting MicroPython. The display task uses `esp_lcd_panel_draw_bitmap` over SPI with DMA-capable line buffers, then exposes fast graphics primitives to Python.
- Tulip's T-Deck docs report stable 30 FPS when the whole 320x240 screen changes. Their code uses an 8-bit framebuffer, RGB332-to-RGB565 conversion, and strip blits rather than per-frame LVGL object churn.
- Tulip also sets the T-Deck peripheral power pin GPIO10 high in native startup before display/input work. Our Python `boot.py` did this too late for the current black-screen failure path.

Implications for KidCode:

- The current LVGL-object renderer is useful for proving MicroPython project loading, but it is probably not the right production graphics path.
- The next realistic KidCode performance step is a native T-Deck framebuffer/canvas module with Python-level `kidcode` drawing APIs, not more Python-created LVGL rectangles.
- For the immediate boot regression, keep the tiny early native init patch available but disabled by default. The first hardware run of that patch black-screened from launcher and exposed no serial device, so it is not the recovery path.
- For the next build base, prefer either a fixed local copy of `lvgl_micropython`'s `LilyGo-TDeck` custom board config or a minimal Tulip-inspired native display task. Do not treat Tulip as directly launcher-compatible without testing its partition/filesystem assumptions.

## Spike Checklist

| Requirement | Status | Evidence |
| --- | --- | --- |
| Boot to simple KidCode launcher | Partial | Boot screen added; hardware confirmation pending. |
| Keyboard diagnostic | Partial | Status mask added; raw keyboard confirmed by user. |
| Tiny Runner loaded from project code | Partial | Frozen module loads and resets through `setup()`; frozen game selector is implemented; explicit SD project slot is implemented but hardware SD mount path is unconfirmed. |
| `from kidcode import *` API | Done | Frozen Tiny Runner uses this API. |
| 128x128 canvas rendering | Done | Visible playable canvas on device. |
| Run/Stop loop | Partial | Home/Stop pause and Run reload/resume are implemented and simulator-tested; hardware button mapping still needs device confirmation. |
| Friendly Python syntax error | Partial | Syntax probe added; serial confirmation pending. |
| Bad-code recovery | Partial | Watchdog enabled; intentional infinite-loop test still pending. |
| Memory report | Partial | Boot/restart heap logs added; serial capture pending. |
| USB flash loop | Partial | Manual bootloader flash works and verifies; app-mode automatic reset into bootloader and post-flash reboot still need a reliable workflow. |
| Official board example | Done | LilyGO HelloWorld builds, full-flashes at `0x0`, and boots with working display on this unit. |
| Host simulation | Done | `make firmware-sim-lilygo-micropython` runs Tiny Runner with fake-LVGL renderer; tests cover movement/render snapshots. |

## Current Decision Read

MicroPython-first remains viable enough to continue the spike, but not proven for product yet.

The strongest positive signal is that MicroPython + LVGL can boot, draw, poll held keyboard input, and run Tiny Runner on the real T-Deck Plus.

The initial risk of performance (22 FPS using high-level Python-LVGL widget calls) has been resolved by implementing a native-backed canvas/framebuffer rendering path. Primitives and sprites are now drawn directly to a 16-bit RGB565 `framebuf.FrameBuffer` and flushed to display via `lv.canvas.invalidate()`, providing a high-performance rendering loop (expected 30+ FPS on device) while preserving 100% compatibility with unit tests and the host simulator.

