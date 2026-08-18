# Moybyte on the Guition JC3248W535 (ESP32-S3, 3.5" 320x480)

The third board and the first provisioned through the #202 port kit
(`docs/board_ports_2026-08.md` -- read its checklist before touching this
port; #202 is the living status). A ~$15-class smart display: same chip as
the T-Deck, a **new port class** on every other axis -- QSPI panel
(AXS15231B, not an ST7789 over plain SPI), touch on the same controller
(no keyboard, no trackball), portrait 320x480 on the fullscreen tier.

## Hardware facts (pin provenance: the owner's working ESPHome definition,
`~/Documents/Work/esphome/JC3248W535.yaml` -- pins verified on the physical
board; tuning deliberately not copied, see `boards/.../sdkconfig.board`)

| subsystem | facts |
|---|---|
| panel | AXS15231B, 320x480 portrait-native, QSPI @ 40MHz: CLK 47, D0 21, D1 48, D2 40, D3 39, CS 45. No reset GPIO. Cannot swap axes (MADCTL MV) -- the console runs PORTRAIT. |
| touch | AXS15231 (same bridge), I2C0 SDA 4 / SCL 8, addr 0x3B. Raw coords are portrait panel coords (driver: `device/axs_touch.py`). |
| backlight | GPIO1, active high, PWM-capable (binary on/off for now -- owner call). |
| battery | ADC GPIO5, divider ~1.72x (unwired here yet). |
| flash/PSRAM | 16MB DIO @ 80MHz; octal PSRAM @ 80MHz. |
| SD | TF slot exists; pins UNVERIFIED -- stage 4 is open, carts live on the internal VFS (`/moy/carts`). |
| audio | speaker header exists; amp/pins UNVERIFIED -- stage 5 is open. |
| USB | the S3's native USB-Serial/JTAG (303a:1001). Console primary per #201, so serial RX works under the desktop. |

## The panel path

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
* 2026-08-18 (same night, on glass, first build) -- **the console runs**:
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
