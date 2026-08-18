// Moybyte Guition JC3248W535 (mainline MicroPython). Board facts only --
// performance levers live in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte Guition S3 (JC3248W535)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// USB: the S3's USB-Serial/JTAG peripheral, NOT TinyUSB CDC -- copied from the
// T-Deck's #201 verdict, which is a CHIP fact, not a board fact: with USBDEV
// on, MICROPY_HW_ESP_USB_SERIAL_JTAG is forced to 0 on the S3 and the image
// ships with no stdin path at all (tusb_init is only reached from a REPL this
// console never returns to). USBDEV off + the sdkconfig's primary-console
// promotion is what makes the serial dev channel work under the desktop.
// Do not "restore" CDC without reading the T-Deck's mpconfigboard.h block.
#define MICROPY_HW_ENABLE_USBDEV            (0)

// UART REPL OFF, same reason as the T-Deck: it shares stdin_ringbuf with the
// USB path, and U0RXD is a floating pin whose noise reads as typed input
// exactly while the USB path is being debugged.
#define MICROPY_HW_ENABLE_UART_REPL         (0)

// I2C0 is the AXS15231's touch bus (addr 0x3B). device/axs_touch.py passes
// these pins explicitly; setting them here means a bare machine.I2C(0) is also
// right. (Pins from the owner's working ESPHome definition.)
#define MICROPY_HW_I2C0_SCL                 (8)
#define MICROPY_HW_I2C0_SDA                 (4)

// The board has a TF slot, but SD is NOT part of this bring-up (stage 4 is an
// open decision -- carts live on the internal-flash VFS like the P4). Keep the
// port's SD support off until that stage decides otherwise.
#define MICROPY_HW_ENABLE_SDCARD            (0)
