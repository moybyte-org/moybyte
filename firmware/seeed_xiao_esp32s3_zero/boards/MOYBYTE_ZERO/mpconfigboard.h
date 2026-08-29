// Moybyte Zero (Seeed XIAO ESP32-S3, mainline MicroPython). Board facts only --
// everything tunable lives in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte Zero (XIAO ESP32-S3)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32S3"

// USB: TinyUSB CDC, which is the port's own S3 default and is DELIBERATELY NOT
// the two console S3 boards' arrangement.
//
// Those boards set MICROPY_HW_ENABLE_USBDEV (0) and promote USB-Serial/JTAG to
// the primary IDF console -- the #201 three-part fix, whose whole subject is a
// board that never returns to the REPL: the desktop frame loop owns the CPU
// forever, so stdin has to work without one. This board's `serve()` loop is
// interruptible and IS interrupted, every time anyone provisions it
// (`mpremote exec` enters the raw REPL through this exact CDC path, which is
// how provision.sh quiets the filesystem before it copies). So the condition
// that made the promotion necessary does not hold here, and the arrangement
// that is PROVEN on this board -- stock ESP32_GENERIC_S3-SPIRAM_OCT, since
// 2026-08-25 -- is the one it keeps.
//
// Two facts hang off this and are recorded in board.toml's [serial]: the USB
// id stays 303a:4001 ("Espressif Device", TinyUSB) rather than the S3
// USB-Serial/JTAG's 303a:1001, and DTR MUST be asserted at open or the REPL is
// silent. Flipping to USB-Serial/JTAG would change both, and is an A/B for
// somebody holding the board -- not a paper decision, and not one to make on
// the only interface a headless board has.

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
