// Moybyte T-Deck (mainline MicroPython). Board facts only -- everything tuned
// for performance lives in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte T-Deck (mainline)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// The T-Deck's USB-C is the S3's NATIVE USB (OTG), so TinyUSB CDC is the REPL
// (the port turns MICROPY_PY_TINYUSB on for the S3, which makes
// MICROPY_HW_USB_CDC default to 1 and USB-Serial/JTAG default to 0). UART REPL
// stays on as well -- the board exposes TX/RX on the header and it costs
// nothing. This mirrors the fork build's MOYBYTE_REPL=cdc_uart default.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

// I2C0 is the T-Deck's peripheral bus: the ESP32-C3 keyboard (0x55) and the
// GT911 touch controller (0x5D/0x14) share it. device_input.py passes these
// pins explicitly; setting them here means a bare machine.I2C(0) is also right.
#define MICROPY_HW_I2C0_SCL                 (8)
#define MICROPY_HW_I2C0_SDA                 (18)

// The SD card shares SPI2 with the panel and is mounted through the native
// moy_sd attach (never machine.SDCard -- see CLAUDE.md's hard constraints), so
// the port's own SD support is deliberately NOT enabled.
#define MICROPY_HW_ENABLE_SDCARD            (0)

// Audio is a MAX98357 mono I2S amp (BCK 7 / WS 5 / DOUT 6), driven by the
// `moy_audio` usermod's core-1 feeder task. `machine.I2S` is on because
// device_audio.py falls back to it when that task cannot be created -- the
// usermod owns the peripheral otherwise, and two owners would clash.
//
// (An earlier revision of this line said ES8311. That is the P4's codec, not
// this board's; the mistake was harmless because the pins live in
// device_audio.py, but it is the kind of thing that gets copied.)
#define MICROPY_PY_MACHINE_I2S              (1)
