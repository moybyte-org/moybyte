# M5Stack Core2 (+ Faces Bottom3 + Faces Gamepad3)

The first classic-ESP32 board. `board.toml` is the build's declaration, this
file the hardware's authority.

| | |
|---|---|
| SoC | ESP32-D0WDQ6-V3, revision 3.1 (read by esptool off this unit), 240 MHz dual core |
| flash / PSRAM | 16 MB QIO 80 MHz / 8 MB quad PSRAM at 80 MHz, of which 4 MB is mapped |
| USB | CH9102 USB-UART (`1a86:55d4`), DTR/RTS auto-reset; the console is UART0 at 115200 |
| panel | ILI9342C, 2.0" 320x240 landscape-native, SPI2 (SCK 18, MOSI 23, MISO 38, CS 5, DC 15) |
| touch | FT6336U at 0x38 on I2C0 (SDA 21, SCL 22) |
| power | AXP2101 at 0x34 on I2C0 (chip id 0x4A: a v1.1 board) |
| TF slot | on the panel's SPI2, CS 4: the T-Deck's arrangement |
| audio | NS4168 amplifier, I2S BCK 12, LRCK 0, DOUT 2; enabled by the AXP2101's ALDO3 |
| gamepad | Faces Gamepad3 at 0x08 on I2C0, behind the Faces Bottom3; INT on G35 |
| also on I2C0 | BM8563 RTC (0x51); an unidentified device at 0x40 that answers `71 27` / `27 10` |

## The power IC is the panel's power and reset

Nothing on the panel works until the AXP2101 says so. `native/moy_core2`
drives it over the kernel's I2C bus, and the panel module calls it through
two hooks (`MOY_LCD_POWER`, `MOY_LCD_BACKLIGHT` in `mpconfigboard.h`):

| rail | enable bit (reg 0x90) | what it powers |
|---|---|---|
| ALDO1 | 0x01 | always-on peripherals |
| ALDO2 | 0x02 | the panel's and the touch's reset: off then on is the panel reset |
| ALDO3 | 0x04 | the speaker amplifier |
| ALDO4 | 0x08 | the TF slot |
| BLDO1 | 0x10 | the backlight; its voltage (reg 0x96) is the brightness |
| BLDO2 | 0x20 | the panel's logic rail |
| DLDO1 | 0x80 | the vibration motor, never turned on |

The factory firmware left 0x90 at 0x7B and every voltage at 3.3 V, read
2026-11-01; M5Stack's M5Unified/M5GFX name the same rails. The AXP2101 is
powered by the battery and USB, not the ESP32, so its registers survive an
ESP32 reset and every power-up states the full target.

## The panel is the T-Deck's module

`moy_lcd` lives in `firmware/lilygo_t_deck_plus_mainline/native/moy_lcd` and
this board's `native/micropython.cmake` includes it from there. Its board
facts are defines with the T-Deck's values as defaults: this board's pins,
`MOY_LCD_MADCTL` 0x08 (landscape-native, BGR), `MOY_LCD_INVERT`, no ST7789
vendor table, and the power hooks above. SD on the panel's host carries every
T-Deck rule in `.claude/rules/boards.md`.

## The gamepad

The Gamepad3 is an STM32 running M5Stack's firmware (model id 0x03 at 0xD0,
firmware 0x04). It is event-driven: a change of its eight buttons pulls INT low
with one frame ready, register 0x00 is the button byte, active-low (bit 0..7:
up, down, left, right, A, B, select, start), and the read consumes the frame;
a read with no frame answers 0x00. M5Stack's `M5Faces` library is the
protocol's authority. The kernel's driver (`MOY_INPUT_PAD_M5FACES` in
`native/moy_input/moy_input_task.c`) writes the "pad" source: the d-pad, A, B,
`select` and `start`, the console's key.

**The Faces Bottom3's RGB LED switch must be on G25.** Its other position is
G2, the speaker's I2S data line.

## What the classic ESP32 does not have

- No GDMA or async-memcpy engine: the fold's snapshot and the layer restore
  are CPU copies (`SOC_ASYNC_MEMCPY_SUPPORTED` guards in `moy_fold.c` and
  `modmoy_gfx.c`), and `LAYER_COPY_ASYNC` is off.
- No USB-Serial/JTAG: the console is a UART, so the board takes the stdin
  ring patch and declines the USJ one, and `[serial]` opens with both lines
  deasserted.
- 4 MB of mapped PSRAM, so `MOYBYTE_GC_SPLIT_RESERVE` is 1 MB, not the S3
  boards' 3 (`mpconfigboard.h` says why).

## Restoring the factory firmware

A full 16 MB image of the factory flash was read 2026-11-01 before the first
Moybyte flash (kept outside the repository). `esptool.py write_flash 0x0
<image>` puts it back.
