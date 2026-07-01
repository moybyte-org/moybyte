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

Local checks:

```bash
moybyte board-info lilygo_t_deck_plus
moybyte device-doctor --board lilygo_t_deck_plus
moybyte lilygo-next
moybyte device-port
moybyte export-device examples/tiny_runner.moyproj --board lilygo_t_deck_plus --out /tmp/moybyte_lilygo_t_deck_plus
```

The export step creates a `.kc8` bundle and `deploy.json`. Firmware scaffolding
should consume that directory rather than reading arbitrary project files.

After flashing, the first serial smoke test should print the board id, bundled
project id, non-zero bundle byte count, and a heartbeat. Save monitor output and
check it with:

```bash
moybyte firmware-smoke-check /tmp/moybyte_lilygo_serial.log --board lilygo_t_deck_plus --project-id tiny_runner
```

The smoke firmware now renders a centered 128x128 native `tiny_runner` canvas
and moves the player rectangle. It polls the keyboard over I2C address `0x55`;
use `WASD` or `HJKL` to move when the keyboard is detected. Use:

```bash
make firmware-smoke-lilygo PORT=/dev/ttyACM0
```
