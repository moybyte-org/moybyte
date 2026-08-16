// Moybyte T-Deck (mainline MicroPython). Board facts only -- everything tuned
// for performance lives in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte T-Deck (mainline)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// USB: the S3's USB-Serial/JTAG peripheral, NOT TinyUSB CDC. This is what makes
// serial RX work under the desktop, and it is the whole reason the board has a
// dev channel at all.
//
// The obvious setting is the wrong one. The T-Deck's USB-C is the S3's native
// USB, so CDC looks right, and the port defaults there (MICROPY_HW_ENABLE_USBDEV
// = SOC_USB_OTG_SUPPORTED). But MicroPython only calls tusb_init() when the REPL
// is reached (micropython#18581, a v1.27 regression) and this console never
// returns to the REPL -- so CDC never comes up. Meanwhile
// MICROPY_HW_USB_CDC = MICROPY_HW_ENABLE_USBDEV forces
// MICROPY_HW_ESP_USB_SERIAL_JTAG to 0 on the S3 (SOC_USB_OTG_PERIPH_NUM == 1),
// which drops usb_serial_jtag_init() and its ISR from the build entirely.
//
// The result is a board with NO stdin at all: measured 2026-08-16, the image
// carried tud_cdc_rx_cb but no tusb_init and no usb_serial_jtag_isr_handler,
// stdin came from uart_stdout_init on U0RXD -- a header pin with nothing
// attached -- and a host write() to the enumerated 303a:1001 interface was
// accepted and then went nowhere (SERIAL rx= never moved off a single stray
// byte). Output worked the whole time, because ESP-IDF's SECONDARY console is
// output-only.
//
// Turning USBDEV off inverts both gates: USB-Serial/JTAG links in, and mp_task
// installs its ISR BEFORE any Python runs, so RX does not depend on reaching a
// REPL. TX is safe -- usb_serial_jtag_tx_strn gives an absent host 200ms once,
// then latches terminal_connected = false and returns immediately.
//
// Do not "restore" this to CDC without checking `nm` for usb_serial_jtag_isr_handler
// and tusb_init: the two mechanisms are mutually exclusive on this chip, and the
// failure mode of picking wrong is silent in one direction only.
#define MICROPY_HW_ENABLE_USBDEV            (0)

// UART REPL OFF. It shares stdin_ringbuf with the USB path, and U0RXD is an
// exposed header pin with nothing attached -- which is where the single stray
// byte behind every `SERIAL rx=1` came from. Leaving it on makes a received
// byte's ORIGIN ambiguous exactly while the USB path is being debugged. Turn it
// back on to drive the board over the header pins.
#define MICROPY_HW_ENABLE_UART_REPL         (0)

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
