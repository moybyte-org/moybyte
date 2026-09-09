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
backlight, the touch driver + its firmware, and the rotated (landscape) desk.
`docs/board_ports_2026-08.md` carries the checklist this port walked.

## What this glass decided (read before touching the board)

- **The console runs LANDSCAPE, 1280×800, on glass that scans PORTRAIT** (owner
  call 2026-09-06: "we want it landscape"). The P4's DSI scans the PSRAM
  framebuffer continuously — there is no per-frame flush to fold a rotation
  into — so the rotation is the compositor's: `device/dsi_panel.py`'s
  `RotatedCompositor` paints a persistent 1280×800 landscape buffer and
  rotates it onto the panel with the PPA. Two costs, and the design is about
  paying the small one as often as possible:
  - a FULL frame (any chrome paint) is a whole-buffer rotate, 2MB in and 2MB
    out over the PSRAM the DSI is reading at ~123MB/s — the measured cost is
    in the section below. An idle desk paints nothing and pays nothing.
  - a QUIET game frame — the game composite was the frame's only write, which
    the canvas's draw gates can tell (rect/rectb/print/pix are gated; a play
    frame moves none of them — measured over 34 frames) — is ONE PPA op: the
    320×240 game canvas scaled AND rotated straight into the scan buffer,
    plus the top bar's strip (an ungated blit every play frame) rotated from
    the paint buffer. Both the windowed player and fullscreen play take it;
    crisp pixels (bilinear PPA declined) composite into the paint buffer
    first and rotate the rect from there.
  - a DAMAGE frame (2026-09-08) — the WM painted and DESCRIBED what: a
    drag's gesture union, the window a scroll or a keystroke re-rendered
    (`WindowedWM._hand_damage` → the root canvas's `note_damage`, which only
    this compositor exposes) — rotates those landscape rects from the paint
    buffer plus the bar strip. The paint buffer is one persistent picture,
    so what the frame changed is exactly what the WM wrote, the same trust
    the WM's own backdrop restore places in its union. A frame the WM cannot
    describe (a window opening, a theme change, a toast in the stack, a
    visible cursor) notes nothing and pays the full rotate; a description
    dearer than 60% of the frame is declined for one.
  A frame's quietness is the canvas's word (`P4SystemCanvas._gates_unchanged`):
  the two native gate counters plus the surface's own CLEAR count, because a
  `cls` is the one whole-surface write the gates cannot see (moy_ppa.fill on
  the PPA, a direct moy_gfx.fill on the CPU) and the PLAY world's letterbox
  is one — invisible, the bezel frame read as quiet, only the game rect
  reached the scan buffers, and the two of them kept different Library
  pixels behind a fullscreen game (owner, 2026-09-09).
  Ping-pong scan buffers make rect frames dangerous — the buffer a rect lands
  in was last shown two frames ago — so each buffer keeps a STALE list of the
  portrait rects it has missed and is brought current before a rect frame is
  rotated into it: a stale rect this frame's rects do not cover is either
  copied 1:1 from the buffer on glass or, when growing one of the frame's
  paint-buffer rects to swallow it moves fewer pixels than the copy, rotated
  as part of that rect (a drag's union moves a few px a frame, so the grown
  rect is a few px larger and the window-sized copy is gone). A buffer that
  missed a full frame gets a full rotate. `tests/test_p4_display.py` pins the
  bookkeeping. **The quiet game frame is ASYNC**: its ops are queued (stale
  copies, the strip, a 1:1 copy of the game canvas into a scratch, the
  scale+rotate from the scratch) and the show waits for the next present,
  which fences everything but the scale+rotate (`moy_ppa.wait(1)`) before
  the cart's tick can write the game canvas; the rotate then overlaps the
  whole next frame. Full and damage frames stay blocking — their source is
  the paint buffer the WM writes next. `RETAINED_FRAMES` on the root is 1
  (the paint buffer persists), which the WM floors to its conservative 2.
  **Which way is up is ONE knob**: `guition_p4_display.ROTATION` (90 or 270,
  the PPA's counter-clockwise), live for a session as `py comp.set_angle(90)`
  over the dev channel — the panel is driven UNMIRRORED
  (`MOY_DSI_MIRROR_XY=0`) so that knob is the only one. **270 is up on the
  desk** (owner, 2026-09-06: the first build's 90 came up flipped).
- **The GSL3680 is RAM-LOADED**: it has no flash, so `device/gsl3680.py`
  streams the vendor firmware (`modules/gsl_fw_jc8012.py`, 4587 records
  transcribed by `tools/gen_gsl_fw.py`) into it over I2C after every reset —
  1.34s from MicroPython at 400kHz, measured — before it reports anything.
  The bring-up sequence is the vendor's factory driver's, cross-checked
  against Linux's `silead.c`; the chip answers `0x5A5A5A5A` at register 0xB0
  once its firmware runs (glass-confirmed). Silead's GPL finger-id algorithm
  (`gsl_point_id.c`) is deliberately NOT carried: raw register 0x80 gives one
  finger's position, which is all a console pointer needs.
- **Touch is CALIBRATED (2026-09-06, twice).** Three corner holds with the
  raw packets sampled over the dev channel gave the axes: the controller
  reports LANDSCAPE, aligned with the desk as mounted (ROTATION 270) — no
  swap, no flips — in the firmware's own space, not the glass's. The
  five-target tool (`run_touch_calibrate()`) then gave the fit: the raw
  origin sits (10, 21) counts in and the spans are 1640×865 over the
  1280×800 glass (`RAW_X0/RAW_Y0/RAW_W/RAW_H` in `guition_p4_input.py`,
  `raw_x0`…`raw_h` on the shared driver); the firmware's nominal 1664×896
  was ~10px off at the edges. **The same session found the bug behind "the
  touch feels inaccurate": the chip raises bit 14 of the raw Y on some
  packets, the vendor's decoder never masked it (Linux's does), and every
  such packet threw the pointer to the bottom edge.** `device/gsl3680.py`
  masks both axes to 12 bits now; `tests/test_gsl3680.py` pins the flagged
  packet and the five-target fit. The knobs stay live (`py touch.raw_x0 = 12`
  over the dev channel); turning the desk the other way up (ROTATION 90)
  means both flips go True.
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
- **The rotation, measured** (the compositor's `overlap_stats()` meters, read
  over the dev channel after a tour with a running cart; 2026-09-06): a
  FULL-frame rotate is **~48ms** (4MB of PSRAM traffic against the DSI's own
  ~123MB/s read — chrome, drags and scrolls move at ~20fps); a QUIET game
  frame is **~11ms** (Star Catcher fullscreen, 960×720 output: the one-op
  scale+rotate of the game canvas plus the bar strip) — 358 of them against
  58 full frames over the tour, 0 stale-rect copies, 0 PPA timeouts, 0 DSI
  underruns. An idle desk rotates nothing. Those were the blocking-rotate
  numbers; the async quiet frame and the WM's damage frames (2026-09-08)
  moved every one of them, and the current figures live in **#220** — the
  compositor's `damage_stats()` / `async_stats()` over the dev channel are
  how they are read.
- **Layer memory is owned, not collected.** A root-canvas layer (a window's
  content buffer, the drag backdrop, a bar strip) lives in heap_caps PSRAM
  outside the gc heap, and the WM / bar release it (`DeviceCanvas.release`)
  when the window dies, a resize rebuilds it, or a strip is evicted. Until
  2026-09-09 nothing did: every Library → CHANGE → home round leaked ~3.6MB
  (15.9MB of PSRAM free at boot, 0.8MB after four rounds), after which every
  later layer fell back onto the gc heap, where its pixels were scanned by
  every collect — a collect had grown from ~80ms fresh to 430-620ms, and the
  exit diag's largest-block probe (about twelve of those) made the PLAY tap
  five seconds. `esp32.idf_heap_info` over the dev channel is how the PSRAM
  free is read; it must not drift across rounds.
- The console: 36 carts seeded on first boot, PPA registered, Lua runtime on,
  the desktop under `WindowedWM` at 1280×800 landscape; the first frame lands ~300ms
  after the desktop is built, and the desktop is built ~27s after reset on a
  seeded store — ~10s of which is the C6 (below). **On-glass suite 18/18**
  (`tests/test_guition_p4_on_glass.py`: state, landscape canvas on portrait glass,
  the rect-rotate fast path under a running game, GSL3680 up,
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

1. ~~Touch calibration~~ — done on glass, 2026-09-06 (above).
2. ~~Orientation~~ — landscape, 270 up, owner-verified on the desk.
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
