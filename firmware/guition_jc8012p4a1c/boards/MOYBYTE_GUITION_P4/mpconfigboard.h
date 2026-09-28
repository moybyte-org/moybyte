// Moybyte Guition P4 (Guition JC8012P4A1C, mainline MicroPython). The
// Waveshare 7B's board header with this board's facts; performance levers
// live in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Moybyte Guition P4"
#endif

#ifndef MICROPY_HW_MCU_NAME
#define MICROPY_HW_MCU_NAME "ESP32P4"
#endif

// The esp_now_* symbols modespnow.c needs come from native/p4/moy_c6 -- thin
// wrappers over ESP-Hosted's custom RPC to the C6, where the real radio is.
// Against a slave with no shim (this board's factory C6 today), esp_now_init()
// times out and raises: the module exists, the radio politely does not.
#define MICROPY_PY_ESPNOW                (1)

#define MICROPY_HW_ENABLE_SDCARD            (1)

#ifndef USB_SERIAL_JTAG_PACKET_SZ_BYTES
#define USB_SERIAL_JTAG_PACKET_SZ_BYTES (64)
#endif

// The REPL is the P4's OWN USB-Serial/JTAG on this board (no CH343 -- the
// USB-C goes straight to the SoC, enumerating 303a:1001). Mainline's P4 port
// drives that peripheral whenever USBDEV is off, which it is on every P4
// board def; the UART REPL stays on as upstream ships it (UART0 is on the
// pin header) and costs nothing here.
#define MICROPY_HW_ENABLE_UART_REPL     (1)

#define MICROPY_PY_MACHINE_I2S          (1)

// I2C0 is the touch bus (GSL3680 @ 0x40, shared with the ES8311/ES7210 codecs).
// guition_p4_input.py passes these pins explicitly; setting them here means a
// bare machine.I2C(0) is also right.
#define MICROPY_HW_I2C0_SCL                 (8)
#define MICROPY_HW_I2C0_SDA                 (7)

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
// three runs a side, stock against both halves together:
//
//     stock                     fps 56     worst 48-55   probe: 100 names 0.73 us
//     re-aimed index, 512       fps 56.5   worst 53-56          100 names 0.34 us
//
// The median is inside this board's noise, as on the Waveshare; the tail and
// tools/map_cache_probe.py move -- the stock image overflows the reachable
// slots at 100 distinct names already, which is the 32-slot REPR_C arithmetic
// showing on glass. Tables in #77.
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
