// Moybyte Zero on an ESP32-C3 (mainline MicroPython). Board facts only --
// everything tunable lives in sdkconfig.board beside this file. A line the
// XIAO's mpconfigboard.h (firmware/seeed_xiao_esp32s3_zero/boards/MOYBYTE_ZERO/)
// also carries keeps its reason there; what is written here is the C3's.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte Zero (ESP32-C3)"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32C3"

// USB: the C3's USB-Serial/JTAG, its only USB (there is no OTG peripheral to
// turn off). esptool resets it into the ROM loader and out again with nothing
// to hold, as on the XIAO since 2026-08-30.

// No Bluetooth, ESP-NOW, I2S or framebuf: the XIAO's four decisions and
// reasons. The first is REQUIRED with sdkconfig.ble left out of
// mpconfigboard.cmake, or the port compiles NimBLE against headers the build
// does not have.
#define MICROPY_PY_BLUETOOTH                (0)
#define MICROPY_PY_ESPNOW                   (0)
#define MICROPY_PY_MACHINE_I2S              (0)
#define MICROPY_PY_FRAMEBUF                 (0)

// UART0 REPL stays ON: GPIO21/20 (TX/RX), the recovery console for an image
// whose USB has wedged, held back from the GPIO allowlist for that reason.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

// I2C0's default pads. The port's C3 default is SCL 9 / SDA 8, which on this
// chip are BOOT and the LED's strapping pin, so a bare `machine.I2C(0)` would
// land on the two pins board.toml's `strap` row never hands out. 5 and 6 are
// in the allowlist, and naming a default claims nothing until something
// constructs the bus.
#define MICROPY_HW_I2C0_SDA                 (5)
#define MICROPY_HW_I2C0_SCL                 (6)

// No SD slot: the cart store is the internal VFS (/moy/carts).
#define MICROPY_HW_ENABLE_SDCARD            (0)

// The deflate compressor every board builds (the XIAO's reason: a .moyimg is a
// deflate stream, and the port's ROM level builds `deflate` read-only).
#define MICROPY_PY_DEFLATE_COMPRESS         (1)

// THE PYTHON HEAP'S FIRST AREA: 32 KB where the port's C3 default is 64. The
// port sizes it to double once and still start WiFi; on this board the native
// tier is the larger tenant (the sync apply, the webhost and the store are C,
// and the Zero's Python only drives them), and the split heap grows past this
// whenever the reserve below allows.
#define MICROPY_GC_INITIAL_HEAP_SIZE        (32 * 1024)

// THE PYTHON HEAP'S CEILING (moybyte_patch_gc_split_reserve): a growth of the
// split heap leaves this much of the internal heap outside it. On this chip
// that heap is the only one, shared by WiFi's buffers, lwIP, the webhost's and
// the shell's C blocks and every task stack, and stock growth takes it to the
// last byte. The first area (MicroPython's 64 KB) is not affected.
#define MOYBYTE_GC_SPLIT_RESERVE            (48 * 1024)

// The serial-only recovery floor takes SAFE after this long (the XIAO's
// reason: the board's only interface is its USB port).
#define MOY_KERNEL_IDLE_SAFE_MS             (30000)

// The kernel's WiFi driver, the board's own station and the mDNS name (the
// XIAO's reason).
#define MOY_NET_WIFI                        (1)
#define MOY_NET_WIFI_OWN                    (1)
#define MOY_NET_MDNS                        (1)

// THE /gpio ALLOWLIST (native/moy_net/moy_gpio.c): the pins a browser's cart
// may drive or read, by GPIO number: board.toml's `gpio` lease row, which the
// build emits as MOY_LEASE_GPIO_PINS; this board serves the route over it. A
// C3 Super Mini breaks out 0-10, 20 and 21. EXCLUDED:
//   2, 8, 9   strapping: 9 is BOOT, 8 the board's LED (active LOW) and the
//             ROM log's gate, 2 must idle high at reset;
//   20, 21    UART0, the REPL that recovers a wedged USB image;
//   11-17     the in-package flash and its VDD_SPI; 18, 19 the USB.
#define MOY_NET_GPIO_ROUTE                  (1)
