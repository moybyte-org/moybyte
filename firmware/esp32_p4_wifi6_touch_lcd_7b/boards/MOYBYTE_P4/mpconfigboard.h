// Both of these can be set by mpconfigboard.cmake if a BOARD_VARIANT is
// specified.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Generic ESP32P4 module"
#endif

#ifndef MICROPY_HW_MCU_NAME
#define MICROPY_HW_MCU_NAME "ESP32P4"
#endif

// ON since 2026-08-24 (the espnow-on-p4 track, docs/espnow_p4_2026-08.md):
// the esp_now_* symbols modespnow.c needs come from native/moy_c6 -- thin
// wrappers over ESP-Hosted's custom RPC to the C6, where the real radio is.
// Against a slave with no shim, esp_now_init() times out and raises: the
// module exists, the radio politely does not.
#define MICROPY_PY_ESPNOW                (1)

#define MICROPY_HW_ENABLE_SDCARD            (1)

// exFAT in FatFS (lib/oofatfs, whose ffconf.h reads this define; ESP32 ports
// leave it off). A card over 32 GB ships exFAT, and without it a read-only
// vfs.mount of one fails with ENODEV: the TF slot is the console's cart store
// when a card is in it (device/card_store.py), and a big card is the common one.
// FAT12/16/32 cards mount as before. LFN is already on, which exFAT needs.
#define MICROPY_FATFS_EXFAT                 (1)

#ifndef USB_SERIAL_JTAG_PACKET_SZ_BYTES
#define USB_SERIAL_JTAG_PACKET_SZ_BYTES (64)
#endif

// Enable UART REPL for modules that have an external USB-UART and don't use native USB.
#define MICROPY_HW_ENABLE_UART_REPL     (1)

#define MICROPY_PY_MACHINE_I2S          (1)

// Disable Wi-Fi and Bluetooth by default, these are re-enabled in the WIFI variants
#ifndef MICROPY_PY_NETWORK_WLAN
#define MICROPY_PY_NETWORK_WLAN         (0)
#endif
#ifndef MICROPY_PY_BLUETOOTH
#define MICROPY_PY_BLUETOOTH            (0)
#endif

// Paint SAVES pictures, and since 2026-09-07 a `.moyimg` is a deflate stream --
// ONE format, the compressed one (runtime/moy_image.py). The esp32 port sits at
// MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES, which builds `deflate` READ-ONLY:
// upstream gates the compressor at FULL_FEATURES, so without this line a board
// can open every picture on the card and cannot write one.
// tests/test_moy_image.py pins all five boards, because a board that is missed
// fails at the moment a kid presses save and nowhere earlier.
#define MICROPY_PY_DEFLATE_COMPRESS         (1)

// The map-lookup cache, 128 -> 512 slots (384 bytes of .bss) -- the T-Deck's
// mpconfigboard.h explains the mechanism and the REPR_C half the shared build
// applies. A/B'd on this board's own glass, 2026-09-21, Brick Siege, diag on,
// three runs a side:
//
//     stock                     fps 55.5   worst 54-56   mp_map_lookup 10-13%
//     re-aimed index, 128       fps 55     worst 49-55                 12.8%
//     re-aimed index, 512       fps 56.5   worst 55-56                 11.7%
//
// The median's step is inside this board's noise: Brick Siege leaves this
// chip 38% idle, so a cheaper lookup mostly buys more idle. What carries it
// is that the sampled share and the on-board lookup probe move exactly as on
// the S3 boards, for 384 bytes on a 32MB board. Tables in #77.
#define MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE   (512)

// The WebAssembly engine's run thread (native/moy_wasm): its stack size and
// whether it lives in PSRAM -- the S3 boards' setting, where an internal stack
// costs a run its whole size in internal SRAM for no speed (their
// mpconfigboard.h carries the numbers). This board has the internal SRAM to
// spare; it keeps the same setting so a cart meets one stack on every board.
#define MOY_WASM_STACK_BYTES                (16 * 1024)
#define MOY_WASM_STACK_PSRAM                (1)

// The engine's runtime pool, its fixed part (moy_wasm_footprint.h). A RISC-V
// load holds more than the S3's for the same cart -- its text relocations --
// and the peak follows the data segments and relocations, not the text:
// Doom's P4 module peaked at 460,392 B at 838 KB and 461,984 B at 790 KB,
// where 256 KB plus a quarter of the module sizes 471 KB and 459 KB (the
// second a load that fails). 320 KB holds it with 63 KB to spare and keeps
// holding it as the text shrinks.
#define MOY_WASM_POOL_BYTES                 (320 * 1024)

// The kernel's recovery floor (native/moy_kernel): moy_dsi's entry points, the
// 1024x600 DPI framebuffer, and the BOOT button (GPIO35, active low) as its one
// button.
#define MOY_KERNEL_PANEL(fn)                moy_dsi_k##fn
#define MOY_KERNEL_PANEL_W                  (1024)
#define MOY_KERNEL_PANEL_H                  (600)
#define MOY_KERNEL_PANEL_ROT                (0)
#define MOY_KERNEL_PANEL_SWAP               (0)
#define MOY_KERNEL_BUTTON_GPIO              (35)
#define MOY_KERNEL_BUTTON_NAME              "BOOT"

// The kernel's I2C bus (native/moy_kernel/moy_bus.h): I2C0, the GT911 touch
// and the ES8311/ES7210 codecs.
#define MOY_BUS_I2C_PORT                    (0)
#define MOY_BUS_I2C_SDA                     (7)
#define MOY_BUS_I2C_SCL                     (8)
#define MOY_BUS_I2C_HZ                      (400000)
