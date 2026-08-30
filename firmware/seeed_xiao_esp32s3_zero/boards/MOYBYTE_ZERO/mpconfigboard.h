// Moybyte Zero (Seeed XIAO ESP32-S3, mainline MicroPython). Board facts only --
// everything tunable lives in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte Zero (XIAO ESP32-S3)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// USB: the S3's USB-Serial/JTAG peripheral, the same arrangement the three
// console boards use. This board ran TinyUSB CDC from 2026-08-25 and the A/B
// its old note asked for was settled on 2026-08-30, by the board.
//
// WHAT THE OLD REASONING GOT RIGHT AND WHAT IT MISSED. It argued the #201
// promotion was not NECESSARY here -- true: that fix is for a board whose
// desktop frame loop owns the CPU forever and never returns to a REPL, and
// this board's `serve()` loop is interruptible. But "not necessary" is not
// "free", and the price of staying on CDC was never measured. It is:
//
//   THERE IS NO SOFTWARE PATH INTO THE ROM LOADER. Every flash needs a human
//   holding BOOT while the board is power-cycled, on the one board in the
//   roster with no screen. Three things were tried on the hardware:
//     * `machine.bootloader()` -- upstream MicroPython implements it fully only
//       for ARDUINO_NANO_ESP32; elsewhere it enters an endless loop, which is
//       exactly what was observed (unresponsive, never re-enumerates, three
//       times in one session).
//     * `esptool --before default_reset` against a HEALTHY board -- a pySerial
//       write timeout. Espressif's own troubleshooting says the reset
//       activation works over UART, not over the OTG/USB interface.
//     * the 1200-baud touch (the Arduino/TinyUSB convention) -- not implemented
//       by MicroPython's CDC; the board ignored it.
//   And an esptool DTR dance against a running CDC WEDGES the USB device, so
//   the failed attempt costs the power cycle it was trying to avoid.
//
// The promotion buys the thing the other three S3 boards already have: esptool
// resets them into the loader and back out with nothing to hold. Two facts move
// with it and are recorded in board.toml's [serial] -- the USB id becomes
// 303a:1001 (USB-Serial/JTAG) rather than 303a:4001 ("Espressif Device",
// TinyUSB), and opening with DTR/RTS LOW is a CHIP RESET on this peripheral, so
// a raw client opens HIGH instead of asserting DTR to wake a silent REPL.
//
// The REPL still works, which is the half the old note was protecting:
// `serve()` is interruptible either way, and mpremote reaches it over
// USB-Serial/JTAG exactly as it does over CDC. The recovery if any of this is
// wrong is unchanged -- hold BOOT while powering on -- so the downside is the
// status quo.
#define MICROPY_HW_ENABLE_USBDEV            (0)

// UART REPL OFF, same reason as the console boards: it shares stdin_ringbuf
// with the USB path, and U0RXD is a floating pin whose noise reads as typed
// input exactly while the USB path is being debugged.
#define MICROPY_HW_ENABLE_UART_REPL         (0)

// NO BLUETOOTH IN THIS IMAGE, and this line is REQUIRED rather than tidy.
// mpconfigport.h defaults MICROPY_PY_BLUETOOTH to 1 on every esp32 target, and
// esp32_common.cmake then compiles mpnimbleport.c + extmod/nimble against
// headers that only exist when the IDF `bt` component is in the build. Leaving
// `boards/sdkconfig.ble` out of mpconfigboard.cmake (see the reasoning there)
// removes the component but not the sources, so the build fails at
// `nimble/nimble_port.h: No such file or directory` -- measured here on this
// board's first build, 2026-08-29. Dropping the fragment and clearing this flag
// are two halves of one decision.
#define MICROPY_PY_BLUETOOTH                (0)

// UART0 REPL stays ON (the generic S3 board's default): D6/D7 are GPIO43/44,
// they are broken out on this board's header, and `zero_gpio.PINS` holds them
// back from the GPIO allowlist precisely BECAUSE they are the recovery console
// for an image whose USB has wedged. Turning it off would delete that path.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

// I2C0's default pads: D4/D5 = GPIO5/GPIO6, which is where the XIAO's silk
// prints SDA/SCL. Nothing on this board is on I2C -- there is no panel, no
// touch controller and no keyboard -- so this only decides where a bare
// `machine.I2C(0)` lands if a kid wires a sensor to the labelled pads. (Both
// pins are also in zero_gpio's digital allowlist; defining a default bus
// claims nothing until something constructs one.)
#define MICROPY_HW_I2C0_SDA                 (5)
#define MICROPY_HW_I2C0_SCL                 (6)

// No SD slot on this board at all: the cart store is the internal VFS
// (/moy/carts), which is also where OTA payloads are staged.
#define MICROPY_HW_ENABLE_SDCARD            (0)
