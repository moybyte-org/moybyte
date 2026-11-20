# Elecrow CrowPanel Advance 10.1" ESP32-P4 HMI (`elecrow_p4`)

The third ESP32-P4 board: the Waveshare 7B's tier (`firmware/esp32_p4_wifi6_touch_lcd_7b/`)
on a 16 MB flash and this PCB's pins. Same 1024x600 EK79007 MIPI-DSI glass
controller, same GT911, same C6 over SDIO, same CH34x UART console, so the
shared P4 tier (`native/p4/`, `device/p4_desktop.py`, the kernel's DSI
compositor) runs unchanged. What is this board's is `board.toml`,
`boards/MOYBYTE_ELECROW_P4/` and `modules/`.

Build: `make firmware-build-elecrow-p4` (`dist/elecrow_p4/`). Flash:
`tools/board.py elecrow_p4 flash` or `make firmware-flash-elecrow-p4 PORT=...`.

## The board

Elecrow's own name is "CrowPanel Advanced 10.1inch ESP32-P4 HMI AI Display
1024x600 IPS Touch Screen" (SKU DHE04310D). The one on the bench enumerates as
a CH34x USB-UART, `1a86:7522`, answers as an ESP32-P4 (revision v1.3, MAC
`e8:f6:0a:e9:e5:89`, 40 MHz crystal) and has a 16 MB flash (manufacturer `ba`,
device `4018`). Its factory flash was read back in full before anything was
written (`~/moybyte-backups/elecrow_p4_factory_20261120.bin`, 16,777,216 bytes,
verified against the flash): a table of `nvs`, `phy_init` and one 15 MB
`factory` app at 0x10000, IDF v5.5.2, the vendor's Brookesia demo. Restore it
with `esptool write_flash 0 <that file>`.

The PCB revision of the bench unit (V1.0, V1.1 or V1.2) is NOT confirmed;
this port's pins are the V1.2 schematic's. The revisions differ in the C6's
SDIO data-line order (below) and in the LoRa socket's control pins.

Sources, in order of authority: the vendor repository's Eagle schematic and
its V1.2 hardware guide, the vendor's examples, the wiki, issue threads.

- Repository: https://github.com/Elecrow-RD/CrowPanel-Advanced-10.1inch-ESP32-P4-HMI-AI-Display-1024x600-IPS-Touch-Screen
  (`Eagle_SCH&PCB/1.2/ESP32-P4 Display 10.1 inch V1.2.sch`, its `readme.md`
  hardware guide, the root `readme.md` pin lists, `factory_firmware/`)
- Wiki: https://www.elecrow.com/wiki/CrowPanel_Advanced_10.1inch_ESP32-P4_HMI_AI_Display_1024x600_IPS.html
- The C6 and SDIO wiring: https://github.com/Elecrow-RD/CrowPanel-Advanced-10.1inch-ESP32-P4-HMI-AI-Display-1024x600-IPS-Touch-Screen/issues/10
  (V1.2: factory C6 on ESP-Hosted 2.12.3, EN on GPIO32 with no inverter)
- C6 upgrade traps, a sibling board: https://github.com/lboshuizen/crowpanel-p4-c6-sdio-ota

## Pin map (schematic V1.2, nets read from the `.sch`)

| function | GPIO | status |
|---|---|---|
| MIPI-DSI, 2 data lanes + clock | dedicated pads | EK79007; vendor: 900 Mbps/lane, RGB565, 51 MHz pixel clock, horizontal 160/70/160, vertical 23/10/12 |
| panel reset (`LCD_RESET`) | 41 | schematic; the vendor firmware passes no reset GPIO, this port uses it (the 7B's panel is reset the same way) |
| backlight (MT9201 EN/PWM) | 31 | vendor: 30 kHz LEDC, 11 bit, duty rises with brightness: active HIGH |
| backlight supply gate (`LCD_BK_POWER`) | 29 | gate of Q11, marked `AO3401_NC` in the schematic (not fitted); the vendor firmware never drives it, this port does not either (`never` row) |
| touch GT911: SDA / SCL | 45 / 46 | I2C0, 400 kHz, pulled up to LDO4's rail; the codec-side devices sit behind BSS138 level shifters on the same pins |
| touch reset / interrupt | 40 / 42 | wired, pulled up to LDO4's rail, left undriven; address 0x5D or 0x14 by INT's level at reset, the driver probes both |
| I2S out: BCLK / LRCK / DOUT | 22 / 21 / 23 | the vendor examples drive these directly, with no master clock and no codec registers; this port does the same, 44.1 kHz stereo |
| I2S master clock net (`I2S_MCLK`) | 24 | the schematic routes it to the ES8311, the ES7210 and a PDM microphone; not driven by this port |
| amplifier control | 30 | the vendor code drives it LOW to play; the speakers sounded with it driven low and with it driven high, so it does not gate them on this unit |
| microphone path | 26 (ES7210 data), 24 (clock), 20 (L/R select) | never initialised by the vendor; absent from this port |
| microSD: CLK / CMD / D0 | 43 / 44 / 39 | SDMMC slot 0, 1-bit: D1 to D3 are not routed to the chip |
| UART0 (CH340K) | 37 TX / 38 RX | console and flasher |
| BOOT button (`SPI_BOOT`) | 35 | strap; low = download |
| C6 SDIO: CLK / CMD | 18 / 19 | SDMMC slot 1 |
| C6 SDIO: D0 / D1 / D2 / D3 | 17 / 16 / 15 / 14 | V1.1 and V1.2; V1.0 is 14 / 15 / 16 / 17 |
| C6 EN | 32 | no inverter: EN is driven directly, high = running |
| UART1 (J2, 3.3 V) | 47 TX / 48 RX | GPIO48 doubles as the vendor LED demo's pin |
| UART3 (J10, level-shifted 5 V input) | 34 TX / 33 RX | 34 is a strapping pin |
| J7 header | 2, 3, 4, 5, 25, 49-54 | |
| 32.768 kHz crystal | 0, 1 | |
| camera (FPC1, SC2336): SCCB | 12 / 13 | this unit has no camera: absent, pins leased |
| USB 2.0 Type-C (J16) | USB pads | CC pins pulled down: a device port |

Not fitted or not routed, so ABSENT from `board.toml` rather than zero: the
camera, and the high-speed USB host. The vendor firmware leaves `reset_gpio_num`
and the camera/audio resets at -1; the schematic has the nets.

### LoRa (not attached on the bench unit)

The vendor's LoRa socket is J9 (SPI + power) with J11 (control). Pins, V1.2
(the vendor Lesson14/15 headers and the schematic agree): SCLK 8, MISO 7,
MOSI 6, SX1262 NSS 10, BUSY 9, DIO1/IRQ 27, NRST 28 (an nRF24 module in the
same socket uses IRQ 9, CE 27, CS 28). V1.0 and V1.1 put IRQ and NRST on 53
and 54. GPIO27 and 28 are shared with UART2 on the same header; software must
not run both. The vendor's own parameters are 915 MHz, 22 dBm: a region's rules
apply. The kernel has an SX1262 driver (`native/moy_net/moy_lora.c`, named by a
`lora` class row with `sck`, `miso`, `mosi`, `cs`, `rst`, `busy`, `irq`); the
row waits for the module. GPIOs 6-10 and 28 are in the `gpio` lease row and 27
(a USB pad) in `never`; moving them into a `lora` row is the whole change.

## The traps this port carries

- **LDO4 powers the pad domain and the I2C pull-ups.** The vendor sequence
  acquires LDO3 at 2.5 V and LDO4 at 3.3 V first. `native/p4/moy_dsi` does both
  (`MOY_DSI_IO_LDO_MV`), because the panel comes up before anything probes I2C;
  `moy_sd` pokes no LDO register on this board (the Waveshare's does).
- **The C6's data lines are reversed and its EN is on GPIO32.**
  The ESP-Hosted component's defaults are the P4 function board's (GPIO54,
  D0 to D3 on 14 to 17); `boards/MOYBYTE_ELECROW_P4/sdkconfig.board`
  selects "no development board" so the component accepts the board's.
  The component's reset sense for an EN pin is "active HIGH" (it ends with the
  pin high, the C6 running). The first image set it LOW, which holds the C6 in
  reset: every CMD5 timed out (0x107) for the whole retry window. The vendor
  issue thread's advice to use active low is the opposite and is not what the
  component's reset sequence does. The SDIO clock is 20 MHz, the Guition P4's
  setting (unmeasured here).
- **The C6 keeps its factory firmware.** This build never touches it; the
  Waveshare's custom ESP-NOW shim slave is not built for this board, so the
  radio link (`moy_net.Link`, ESP-NOW) reports down against the stock slave.
  Wi-Fi and BLE ride ESP-Hosted. Sibling-board reports: a factory C6 on
  ESP-Hosted 2.3.0 drops Wi-Fi after minutes; there is no C6 rollback, and a
  bad C6 image reboots in a loop. The bench unit's C6 version is read from the
  first boot's log.
- **The SD slot is 1-bit.** A 4-bit bring-up would drive GPIO40-42, the touch
  reset, the panel reset and the touch interrupt.
- **Flash is 16 MB.** The partition table is the Guition P4's (two 6.5 MiB app
  slots, the store in the tail).
- **Rev < v3 silicon**: `CONFIG_ESP32P4_SELECTS_REV_LESS_V3` as on the 7B.
- **The panel's scan clock is a guess.** The vendor's 51 MHz is no divisor of
  the 240 MHz PLL; the port runs 60 MHz (65.8 Hz refresh over the vendor's
  1414x645 timing, hsync pulse 70, vsync pulse 10). 48 MHz (52.6 Hz) is the
  divisor below.

## Unconfirmed

Read from documents, not from this unit: the PCB revision; the touch
orientation (the vendor maps with no swap or mirror; the corner calibration,
`moy_runtime.run_touch_calibrate`, has not run); what GPIO30 does (it gates nothing audible); whether the ES8311 and ES7210 are fitted (neither answered on the bus);
that the panel is lit and its picture is right (only the framebuffer and the
scan counters have been read back); the TF slot (no card was in it);
the USB bridge's DTR/RTS rest state beyond "attaching and `reboot` work with
the Waveshare's"; the BOOT button's pin (35 from the net names); BLE over the
C6; the SDIO link under a sustained download at 20 MHz.

## First flash (2026-11-20)

The factory flash was saved first (above). `build.sh`'s image, flashed at
0x2000 with the board's `[flash]` facts, booted to the desk in 10 to 25
seconds; the kernel's first light was at 223 ms. Read back over the dev
channel: the DPI scan at about 66 refreshes a second with no underruns (the
60 MHz clock over the vendor's blanking), the GT911 answering at 0x5D with a
touch already read, the C6 on ESP-Hosted 2.12.3 (the component this build
bundles is 2.12.12) with a Wi-Fi scan returning networks, and `board.py
elecrow_p4 reboot` returning to the desk. The card slot reported
`card_init failed: 263` (a timeout) with no card in it, and the carts stayed on
the internal store. The ESP-NOW link reports down (`moy_c6.ping()` false): the
C6 runs the stock slave with no shim, which this board does not replace.

## Audio (2026-11-20)

The first image kept the schematic's ES8311 path (I2C codec, master clock on
GPIO24). The codec never answered: a SoftI2C scan of GPIO45/46 found only the
GT911 at 0x5D and a device at 0x2F (not an ES8311 at 0x18 or 0x19, nor an
ES7210), so the speaker's start returned "the ES8311 does not answer on the
bus", the output state went FAILED, `devices.present("media")` was false and the
Music app showed "No sound on this console". The vendor's own examples play
through plain I2S on GPIO22/21/23 with no master clock, so the speaker row
names those and the board takes the kernel's codec-less path (the one the
Guition S3 uses). With it the output starts, `devices.present("media")` is
true, a console tone and a cart session's tone were played at console volume
2 of 7 and the owner heard them.

The speakers: the V1.2 schematic has one speaker connector per side, J4
(`VOLP`/`VOLN`, left) and J6 (`VORP`/`VORN`, right). Its fitted path is the
ES8311 into U4, an NS4263B analog stereo amplifier; the alternative, not fitted
in the schematic, is two NS4168 I2S amplifiers (U13 for J4, U3 for J6) on the
same I2S lines, one per channel. This unit plays through plain I2S, so it has
the second arrangement or a variant of it: one amplifier per speaker, each
taking one slot. The speaker row names no master clock, and the board runs 44.1
kHz stereo (`MOY_AUDIO_RATE`, `MOY_AUDIO_STEREO`, the other P4s' setting, the
mix upsampled 2x), where the first codec-less image ran the default 22,050 Hz
mono; `moy_aud_out.c`'s codec-less slot format now follows `MOY_AUDIO_STEREO`.
