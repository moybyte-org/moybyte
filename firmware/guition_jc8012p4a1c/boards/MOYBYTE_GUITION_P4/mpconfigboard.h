// Moybyte Guition P4 (Guition JC8012P4A1C, mainline MicroPython). The
// Waveshare 7B's board header with this board's facts; performance levers
// live in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Moybyte Guition P4"
#endif

#ifndef MICROPY_HW_MCU_NAME
#define MICROPY_HW_MCU_NAME "ESP32P4"
#endif

// The radio link is the kernel's (native/moy_net/moy_link.c, moy_net.Link):
// it holds esp_now's one receive callback, so the port's espnow module is out
// of the image and Python cannot take the callback from it. The esp_now_*
// symbols it calls come from native/p4/moy_c6 -- thin wrappers over
// ESP-Hosted's custom RPC to the C6, where the real radio is. Against a slave
// with no shim (this board's factory C6 today), esp_now_init() times out and
// the link reports it down.
#define MICROPY_PY_ESPNOW                (0)
#define MOY_NET_LINK                     (1)
// The WiFi driver is the kernel's too (native/moy_net/moy_wifi.c): the station
// starts and stops as the spine's lease asks, and Python never constructs the
// port's network.WLAN on this board.
#define MOY_NET_WIFI                     (1)

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

// The REPL is the P4's OWN USB-Serial/JTAG on this board (no CH343 -- the
// USB-C goes straight to the SoC, enumerating 303a:1001). Mainline's P4 port
// drives that peripheral whenever USBDEV is off, which it is on every P4
// board def; the UART REPL stays on as upstream ships it (UART0 is on the
// pin header) and costs nothing here.
#define MICROPY_HW_ENABLE_UART_REPL     (1)

#define MICROPY_PY_MACHINE_I2S          (1)

// I2C0 is the touch bus (GSL3680 @ 0x40, shared with the ES8311/ES7210 codecs),
// the kernel's bus (MOY_BUS_I2C_* below): nothing opens a machine.I2C on port 0.
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

// The console's stdin ring, grown into PSRAM by native/moy_serial on the first
// `recv`: twice the [serial] window, so the window the host sends on an ack
// lands while the store writes the last one instead of stalling the USB
// endpoint at the port's 260 bytes. The module's header says which boards may.
#define MOY_SERIAL_RING_BYTES               (32768)

// The kernel's recovery floor (native/moy_kernel): moy_dsi's entry points, and
// the landscape 1280x800 floor turned onto the 800x1280 portrait framebuffer by
// guition_p4_display.ROTATION (270, counter-clockwise). No input reaches the
// floor -- the GSL3680 needs its firmware uploaded -- so serial drives it, and
// with no input for 30 s it starts SAFE.
#define MOY_KERNEL_PANEL(fn)                moy_dsi_k##fn
#define MOY_KERNEL_PANEL_W                  (1280)
#define MOY_KERNEL_PANEL_H                  (800)
#define MOY_KERNEL_PANEL_ROT                (270)
#define MOY_KERNEL_PANEL_SWAP               (0)
#define MOY_KERNEL_IDLE_SAFE_MS             (30000)

// The kernel's I2C bus (native/moy_kernel/moy_bus.h): I2C0, the GSL3680 touch
// and the ES8311/ES7210 codecs.
#define MOY_BUS_I2C_PORT                    (0)
#define MOY_BUS_I2C_SDA                     MICROPY_HW_I2C0_SDA
#define MOY_BUS_I2C_SCL                     MICROPY_HW_I2C0_SCL
#define MOY_BUS_I2C_HZ                      (400000)

// The speaker (native/moy_audio/moy_aud_out.c, #82): the ES8311 codec on the
// bus above, clocked from MCLK, and its power amplifier's enable.
#define MOY_AUDIO_CODEC_ES8311              (1)
#define MOY_AUDIO_I2S_MCLK                  (13)
#define MOY_AUDIO_I2S_BCK                   (12)
#define MOY_AUDIO_I2S_WS                    (10)
#define MOY_AUDIO_I2S_DOUT                  (9)
#define MOY_AUDIO_PA_GPIO                   (20)

// Input's BLE HID keyboard (native/moy_input/moy_ble_task.c): the kernel's
// central over NimBLE, its bonds in NVS. There is no `bluetooth` module.
#define MOY_INPUT_BLE                       (1)
#define MOY_INPUT_BLE_HOSTED                (1)     // the C6 over ESP-Hosted

// Input's touch (native/moy_input/moy_input_task.c): the GSL3680 at 0x40, RST
// 22, INT 21 (held low across a reset to pick the address), RAM-loaded with
// this glass's firmware (gsl_fw_jc8012.c). Calibrated on glass 2026-09-06: it
// reports LANDSCAPE, aligned with the desk at ROTATION 270 (no swap, no
// flips), its space starting ~10 x ~21 counts in and spanning 1640 x 865 over
// the 1280x800 glass. At ROTATION 90 both flips go true.
#define MOY_INPUT_TOUCH_KIND                (2)     // MOY_TOUCH_GSL3680
#define MOY_INPUT_TOUCH_ADDR                (0x40)
#define MOY_INPUT_TOUCH_RST                 (22)
#define MOY_INPUT_TOUCH_INT                 (21)
#define MOY_INPUT_TOUCH_FW                  moy_gsl_fw
#define MOY_INPUT_TOUCH_FW_LEN              moy_gsl_fw_LEN
#define MOY_INPUT_TOUCH_SWAP                (0)
#define MOY_INPUT_TOUCH_FLIP_X              (0)
#define MOY_INPUT_TOUCH_FLIP_Y              (0)
#define MOY_INPUT_TOUCH_RAW_X0              (10)
#define MOY_INPUT_TOUCH_RAW_Y0              (21)
#define MOY_INPUT_TOUCH_RAW_W               (1640)
#define MOY_INPUT_TOUCH_RAW_H               (865)

// The glass's layer pool (native/moy_glass/moy_buf.h): the most bytes of
// released layer buffers it keeps for the next run. A window's buffer is
// never pooled (the desk re-mints at every size); a cart's worlds are.
#define MOY_GLASS_POOL_BYTES                (2 * 1024 * 1024)
