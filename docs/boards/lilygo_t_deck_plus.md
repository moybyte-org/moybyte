# LilyGO T-Deck Plus Board Profile

Moybyte board id: `lilygo_t_deck_plus`

This profile tracks the user's LilyGO T-Deck Plus as the first concrete hardware
target.

Primary upstream source:

```text
https://github.com/Xinyuan-LilyGO/T-Deck
```

Source facts used by the Moybyte tooling:

```text
PlatformIO environment: T-Deck
MCU: ESP32-S3
Flash size: 16MB
PSRAM: 8M OPI PSRAM
```

Important T-Deck Plus note from upstream: the Grove interface pins are assigned
to GPS on the Plus variant, so the Grove interface should not be treated as
available.

Pin values currently captured from upstream `examples/UnitTest/utilities.h`:

```text
BOARD_POWERON       10
BOARD_I2C_SDA       18
BOARD_I2C_SCL       8
KEYBOARD I2C ADDR   0x55
BOARD_KEYBOARD_INT  46
BOARD_SDCARD_CS     39
BOARD_TFT_CS        12
BOARD_TFT_DC        11
BOARD_TFT_BACKLIGHT 42
BOARD_SPI_MOSI      41
BOARD_SPI_MISO      38
BOARD_SPI_SCK       40
BOARD_GPS_TX_PIN    43
BOARD_GPS_RX_PIN    44
BOARD_BOOT_PIN      0
```

*(This profile once carried a `moybyte board-info` / `export-device` workflow
producing `.kc8` bundles, and an Arduino serial-smoke firmware behind
`make firmware-smoke-lilygo`. All of it went with the `.moyproj` SDK on
2026-07-31 and the smoke firmware with it; the board is driven by
`firmware/lilygo_t_deck_plus_mainline/` now. What survives here is the pin
transcription, which `THIRD_PARTY.md` cites as its source.)*
