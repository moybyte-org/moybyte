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

// exFAT in FatFS (lib/oofatfs, whose ffconf.h reads this define; ESP32 ports
// leave it off). A card over 32 GB ships exFAT, and without it a read-only
// vfs.mount of one fails with ENODEV: the TF slot is the console's cart store
// when a card is in it (device/card_store.py), and a big card is the common one.
// FAT12/16/32 cards mount as before. LFN is already on, which exFAT needs.
#define MICROPY_FATFS_EXFAT                 (1)

// The Python heap may grow on demand (split heap), but never into this much
// of PSRAM: it is the Lua VM's, the panel DMA's and the layer pool's share.
// The biggest corpus cart's VM peaks near 1.8MB live and the pool that serves
// it holds up to half that again at its parse-time peak; a card with two
// dozen carts grows the launcher's heap 1.5MB at boot. 3MB keeps the biggest
// cart loadable behind that. See tools/esp32_build_lib.sh.
#define MOYBYTE_GC_SPLIT_RESERVE            (3072 * 1024)

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
//     stock                     fps 47     worst 42-47   mp_map_lookup 13-17%
//     re-aimed index, 128       fps 48     worst 46-48                 17.3%
//     re-aimed index, 512       fps 49     worst 46-48                 15.1%
//
// A smaller win than the T-Deck's (this board is 8-10% gc_alloc where the
// T-Deck is 1%, so the lookup is a smaller share of its frame), and the
// median's step is near this board's run-to-run noise; the tail and the
// sampled share move the same way, which is what carries it. Tables in #77.
#define MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE   (512)

// The WebAssembly engine's run thread (native/moy_wasm): its stack size and
// whether it lives in PSRAM. PSRAM, measured 2026-09-25 on the Guition S3 with
// the hello module: an internal stack costs a run its whole size in internal
// SRAM (17 KB with this one) against about 1 KB for a PSRAM one, and the two
// ran step(400000) in 276 vs 277 ms. With WiFi and BLE up this board has no
// 17 KB to give. 16 KB is eight times the hello module's high-water mark
// (2.2 KB); a stack overflow traps cleanly (the AOT stack check), it does not
// corrupt.
#define MOY_WASM_STACK_BYTES                (16 * 1024)
#define MOY_WASM_STACK_PSRAM                (1)

// The console's stdin ring, grown into PSRAM by native/moy_serial on the first
// `recv`: twice the [serial] window, so the window the host sends on an ack
// lands while the store writes the last one instead of stalling the USB
// endpoint at the port's 260 bytes. The module's header says which boards may.
#define MOY_SERIAL_RING_BYTES               (32768)

// The kernel's recovery floor (native/moy_kernel): moy_axs's entry points, its
// landscape 480x320 framebuffer in wire order (the module turns the bands onto
// the portrait glass), and the AXS15231's touch on I2C0, polled from C.
#define MOY_KERNEL_PANEL(fn)                moy_axs_k##fn
#define MOY_KERNEL_PANEL_W                  (480)
#define MOY_KERNEL_PANEL_H                  (320)
#define MOY_KERNEL_PANEL_ROT                (0)
#define MOY_KERNEL_PANEL_SWAP               (1)
#define MOY_KERNEL_TOUCH_AXS15231           (1)
#define MOY_KERNEL_TOUCH_SDA                MICROPY_HW_I2C0_SDA
#define MOY_KERNEL_TOUCH_SCL                MICROPY_HW_I2C0_SCL
#define MOY_KERNEL_TOUCH_ADDR               (0x3B)

// The kernel's I2C bus (native/moy_kernel/moy_bus.h): I2C0, the AXS15231 touch.
#define MOY_BUS_I2C_PORT                    (0)
#define MOY_BUS_I2C_SDA                     MICROPY_HW_I2C0_SDA
#define MOY_BUS_I2C_SCL                     MICROPY_HW_I2C0_SCL
#define MOY_BUS_I2C_HZ                      (400000)
