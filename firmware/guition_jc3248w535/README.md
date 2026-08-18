# Moybyte on the Guition JC3248W535 (ESP32-S3, 3.5" 320x480)

The third board and the first provisioned through the #202 port kit
(`docs/board_ports_2026-08.md` -- read its checklist before touching this
port; #202 is the living status). A ~$15-class smart display: same chip as
the T-Deck, a **new port class** on every other axis -- QSPI panel
(AXS15231B, not an ST7789 over plain SPI), touch on the same controller
(no keyboard, no trackball), and a 480x320 LANDSCAPE console on the
fullscreen tier, rotated out of the portrait-native glass in the panel
driver's band copy.

## Hardware facts (pin provenance: the owner's working ESPHome definition,
`~/Documents/Work/esphome/JC3248W535.yaml` -- pins verified on the physical
board; tuning deliberately not copied, see `boards/.../sdkconfig.board`)

| subsystem | facts |
|---|---|
| panel | AXS15231B, 320x480 portrait-native, QSPI @ 40MHz: CLK 47, D0 21, D1 48, D2 40, D3 39, CS 45. No reset GPIO. MADCTL MV is DEAD on this glass (tested 0x60 + 0x20 live, both scramble; Arduino_GFX writes the bit, the LVGL-forum reports match ours) -- the console runs LANDSCAPE 480x320 via the rotate in moy_axs's band copy (owner call 2026-08-18). |
| touch | AXS15231 (same bridge), I2C0 SDA 4 / SCL 8, addr 0x3B. Raw coords are portrait panel coords (driver: `device/axs_touch.py`). |
| backlight | GPIO1, active high, PWM-capable (binary on/off for now -- owner call). |
| battery | ADC GPIO5, divider ~1.72x (unwired here yet). |
| flash/PSRAM | 16MB DIO; octal PSRAM. BOTH at 120MHz since 2026-08-19 (the T-Deck's experimental MSPI profile, A/B'd on this glass: carts +25-29%, pump -23%, SPI starvation -77%; needs the #169 retune patch, applied by build.sh). |
| SD | TF slot exists; pins UNVERIFIED -- stage 4 is open, carts live on the internal VFS (`/moy/carts`). |
| audio | speaker header exists; amp/pins UNVERIFIED -- stage 5 is open. |
| USB | the S3's native USB-Serial/JTAG (303a:1001). Console primary per #201, so serial RX works under the desktop. |

## The panel path

**The game fold + the game window** (2026-08-19, #190's cousin, then the
owner's bezel insight the same evening): on a play frame the game composite
never touches the root framebuffer -- `DeviceCanvas.blit_game`'s existing
#190 plumbing arms `moy_axs`, whose flush synthesizes every band from the
scratch snapshot directly. And because the bezels never change while the
panel's GRAM persists, only the FIRST folded flush ships full-screen (laying
the bezels); every steady play frame after it arms CASET/RASET to the game's
physical rectangle (240x320, 8-aligned) and ships the game alone -- the
T-Deck's exact payload at a quarter of this bus's full-frame time. Proven
byte-identical to the composite path on the device itself
(`moy_axs.fold_test`, both passes, 0 mismatched bytes); `fold_stats`'
windowed counter tracks folded flushes 1:1 minus the bezel-layers. Overlays
disarm through the shared frame walk and pay the old cost. Measured ladder
on this glass (Star Catcher / Sakura Lua): 80MHz bring-up 24/21 -> 120MHz
MSPI 30/27 -> fold 35/30 -> game window **42/34fps**. The remaining gap to
the T-Deck's 60/50 is CPU-side (busy 23ms); recorded follow-ups: the pump's
SPI idle rose with the small bands (~4ms starved -- slot count / feed pacing
tuning), and pump-on-core-1 stays the strategic lever.


`native/moy_axs` -- raw `spi_master`, NOT esp_lcd, because the AXS15231B's
QSPI protocol wants the whole frame under ONE CS assertion behind a 4-byte
1-line opcode header (`0x32 00 2C 00`), which is the opposite of esp_lcd's
per-call CS cycling. The band/bounce/kick-pump-drain machinery is moy_lcd's
design carried over: bands of 48 rows memcpy'd PSRAM -> two internal-SRAM DMA
bounce slots, queued with `SPI_TRANS_CS_KEEP_ACTIVE`, completion counted by a
`post_cb` ISR. `modules/guition_panel.py` is the compositor over it
(tdeck_panel's twin). Init sequence provenance: ESPHome's AXS15231 model plus
its generated DCS tail -- the exact sequence the owner's ESPHome build runs on
this exact glass.

## Build / flash / monitor

```bash
make firmware-build-guition-s3
make firmware-flash-guition-s3 PORT=/dev/ttyACM1
make firmware-monitor-guition-s3 PORT=/dev/ttyACM1
```

Bring-up smokes: `modules/moybyte_shell.py`'s `MODE` ("panel" / "touch" /
"desktop"), all self-terminating, all re-runnable from the live REPL:

```python
import moybyte_shell as s; s.MODE = "touch"; s.main()
```

## Bring-up log

* 2026-08-18 -- port authored (stage 0 skeleton through stage 6 code):
  moy_axs + guition_panel + axs_touch + run_desktop; `make test` green with
  the board in the staging-closure/board-toml suites.
* 2026-08-19 (the morning after, owner's eyes on the glass) -- three hardware
  verdicts and the LANDSCAPE flip:
  * **the panel discards writes until CASET/RASET are armed** -- first light's
    "coloured static" was power-on GRAM noise under a fully-successful flush;
    arming the window live fixed it, and moy_axs arms it every kick now.
  * **MADCTL MV is dead**: 0x60 and 0x20 both scramble the write path while
    0x00 stays clean. Landscape therefore rotates in the band copy
    (rotate-gather: sequential PSRAM reads, scattered writes into the
    uncached SRAM bounce -- same read traffic as the memcpy it replaced), with
    `moy_axs.set_rot(0|1)` as the direction knob; rot 0 confirmed upright.
  * **touch has two failure modes that present identically** (both in
    `device/axs_touch.py`'s docstring): a SECOND machine.I2C(0) instance
    reads constant bytes while the driver's first instance works -- so never
    diagnose touch with a side probe, go through the live console -- and a
    BOOT RACE where the constructor's single probe read loses and
    `available` latches False for the session (the episode that looked like
    a hardware wedge until two power cycles cleared it). The ctor retries
    now, poll() re-probes every ~2s, and a constant-byte streak is named on
    serial after ~5s instead of reading as "nobody is touching the screen".
  * touch mapping CALIBRATED on glass (SWAP_XY + FLIP_X, rot 0): taps land
    under the finger, corners included.
  * `tests/test_guition_on_glass.py` re-passed **10/10** on the landscape
    console (viewport seam now `(80, 40, 1)`).
  * **kinetic-scroll hiccup, measured and named** (open perf item, #202/#66
    style): a fling feels like start-stop-continue because the first
    repaint at a gesture transition bills ~80-84ms (frame-timer trace: idle
    frames cost 0 under the redraw gate, steady drag frames 27ms, fling
    shift frames ~0ms at 60fps -- and NO gc, the heap never jumps). It is
    the #113 retained-ring's full-paints-before-shift-eligibility cost at
    480x320. The lever is the launcher first-paint diet, not the driver.
  * lived pain: entering a game with no way out (the touch-only exit
    gesture, already on #202) -- the owner had to power cycle to leave a
    cart. Rising priority (a paired BLE keyboard's hold-BACKSPACE now
    also serves).
  * **the scroll feel, closed the same evening** (owner verdict: "perfect,
    looks better than tdeck"): the drag-hang-then-phantom-fling was the
    driver waiting the GT911's 400ms no-news bound to believe a lift on a
    controller whose only silence IS the lift. Fixed in device/axs_touch.py
    with measured constants: a 90ms per-controller bound (2x the worst
    touched gap) plus hold-window EXTRAPOLATION (the pointer glides on its
    measured velocity through the <=90ms release window instead of
    freezing -- pixels, not physics: extrapolated frames stay stale for
    the velocity EMA). A shared-console "still finger" decay rule was
    built on the wrong model the same day and REVERTED on data -- the
    trace that killed it (a resting finger streams 88% fresh) is in the
    console.py docstring. GPIO3 is CONFIRMED the touch INT (pulses while
    touched, silent after lift) -- the recorded next lever if release
    latency ever needs to drop below ~90ms.
* 2026-08-18 (first night, on glass, first build) -- **the console runs**:
  * stage 1: `moy_axs` first light on the first attempt -- init accepted,
    banded bounce flush at **19.4ms/frame** (51.6fps ceiling; pump 5.4ms CPU,
    idle 1.1ms, blocked only 1.1ms -- the kick/pump/drain overlap works,
    0 timeouts / 0 queue errors over the session).
  * stage 2 (half): the AXS15231 touch controller answers at 0x3B and
    reports no-touch correctly. The MAPPING knobs are still the
    ESPHome-derived guess -- run `guition_smoke.touch()` with a finger and
    bake the winners into `device/axs_touch.py`.
  * stage 6: boots to the desktop (first frame 270ms after a seeded boot;
    34 carts seeded to `/moy/carts` on the first boot), OTA confirm fired
    (`marked app valid (slot ota_0)`), and
    **`tests/test_guition_on_glass.py` passed 10/10** -- state shape, the
    320x480-system/320x240-game viewport seam `(0, 120, 1)`, swipes through
    the real pointer feed, a Python cart (Star Catcher, ~28fps) and a Lua
    cart (Sakura Lua via moycore, ~24fps) both run and exit, idle blank +
    wake, mem. `MODE = "desktop"` is the shipped default.
  * NOT yet verified (needs eyes/fingers): the pattern's orientation, colors
    and checker squareness on the physical glass, and the touch calibration
    pass. One anomaly on file: a single
    `frame error: 'NoneType' object isn't iterable` fired once, on the
    first-ever cart exit of the first seeded session, and never reproduced
    (not on later exits, not on a fresh boot); `_frame_error` now prints the
    full traceback so a recurrence names its line.
  * open stages/decisions: SD (stage 4 -- pins unverified), audio (stage 5 --
    amp/pins unverified), backlight PWM dimming (owner call), and a
    **touch-only exit gesture for fullscreen games** -- this board has no
    BACKSPACE to hold, so a running game currently has no on-glass exit path
    (tools/apps keep the bar's context-X; games hide the bar). #202 carries
    it.
