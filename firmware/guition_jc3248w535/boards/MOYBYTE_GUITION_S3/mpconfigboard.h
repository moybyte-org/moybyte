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

// Stage 4 decided (owner call 2026-08-20): the TF slot is the CART STORE when
// a card is present. It lives on its OWN SPI (SPI3: CS 10 / MOSI 11 / SCK 12 /
// MISO 13 -- community pin map, verified on this glass), sharing NOTHING with
// the QSPI panel on SPI2 -- so the port's plain machine.SDCard is the RIGHT
// driver here. The (0) this shipped with was the T-Deck template's foot-gun
// guard (there SD shares the panel host and machine.SDCard wedges the board);
// that hazard does not exist on this wiring.
#define MICROPY_HW_ENABLE_SDCARD            (1)

// The Python heap may grow on demand (split heap), but never into this much
// of PSRAM: it is the Lua VM's, the panel DMA's and the layer pool's share.
// The biggest corpus cart's VM peaks near 1.8MB live and the pool that serves
// it holds up to half that again at its parse-time peak; a card with two
// dozen carts grows the launcher's heap 1.5MB at boot. 3MB keeps the biggest
// cart loadable behind that. See tools/esp32_build_lib.sh.
#define MOYBYTE_GC_SPLIT_RESERVE            (3072 * 1024)
