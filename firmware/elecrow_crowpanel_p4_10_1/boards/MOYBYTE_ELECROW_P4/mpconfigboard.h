// Both of these can be set by mpconfigboard.cmake if a BOARD_VARIANT is
// specified.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Generic ESP32P4 module"
#endif

#ifndef MICROPY_HW_MCU_NAME
#define MICROPY_HW_MCU_NAME "ESP32P4"
#endif

// The radio link is the kernel's (native/moy_net/moy_link.c, moy_net.Link):
// it holds esp_now's one receive callback, so the port's espnow module is out
// of the image and Python cannot take the callback from it. The esp_now_*
// symbols it calls come from native/p4/moy_c6 -- thin wrappers over
// ESP-Hosted's custom RPC to the C6, where the real radio is. Against a slave
// with no shim, esp_now_init() times out and the link reports it down.
#define MICROPY_PY_ESPNOW                (0)
#define MOY_NET_LINK                     (1)
// The WiFi driver is the kernel's too (native/moy_net/moy_wifi.c): the station
// starts and stops as the radio's lease asks, and Python never constructs the
// port's network.WLAN on this board.
#define MOY_NET_WIFI                     (1)
// The mDNS name (`<name>.local`, Settings' Name) the console answers to while
// its SSH server serves (runtime/system/ssh_service.py).
#define MOY_NET_MDNS                     (1)

#define MICROPY_HW_ENABLE_SDCARD            (1)

// The TF card's power is LDO channel 4, which native/p4/moy_dsi acquires at
// boot (MOY_DSI_IO_LDO_MV in mpconfigboard.cmake), so moy_sd pokes no LDO
// register here (native/moy_sd/moy_sd.h). Its SDMMC slot 0 pins are board.toml's
// `sdmmc0` row: CLK, CMD and D0 only, a 1-bit bus.

// exFAT in FatFS (lib/oofatfs, whose ffconf.h reads this define; ESP32 ports
// leave it off). A card over 32 GB ships exFAT, and without it a read-only
// vfs.mount of one fails with ENODEV: the TF slot is the console's cart store
// when a card is in it (device/card_store.py), and a big card is the common one.
// FAT12/16/32 cards mount as before. LFN is already on, which exFAT needs.
#define MICROPY_FATFS_EXFAT                 (1)
// FatFS takes the store's lock around every call on the card's volume, which
// a program's own task works too (native/moy_store/moy_vol.h, THE STORE'S LOCK);
// its long-name buffer is on the calling task's stack (tools/patch_fatfs_lfn.py).
#define MICROPY_FATFS_REENTRANT             (1)
#define MICROPY_FATFS_ENABLE_LFN            (2)
#define MICROPY_FATFS_SYNC_T                uintptr_t

#ifndef USB_SERIAL_JTAG_PACKET_SZ_BYTES
#define USB_SERIAL_JTAG_PACKET_SZ_BYTES (64)
#endif

// Enable UART REPL for modules that have an external USB-UART and don't use native USB.
#define MICROPY_HW_ENABLE_UART_REPL     (1)

// The high-speed OTG port is the `usb` device API's host (native/p4/moy_usb):
// MicroPython's TinyUSB device, which mainline brings up there by default,
// stays out of the image. The REPL is the CH340K's UART either way.
#define MICROPY_HW_ENABLE_USBDEV        (0)

#define MICROPY_PY_MACHINE_I2S          (1)

// Disable Wi-Fi and Bluetooth by default, these are re-enabled in the WIFI variants
#ifndef MICROPY_PY_NETWORK_WLAN
#define MICROPY_PY_NETWORK_WLAN         (0)
#endif
#ifndef MICROPY_PY_BLUETOOTH
#define MICROPY_PY_BLUETOOTH            (0)
#endif

// Paint SAVES pictures, and since 2026-09-07 a `.moyimg` is a deflate stream --
// ONE format, the compressed one (runtime/carts/moy_image.py). The esp32 port sits at
// MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES, which builds `deflate` READ-ONLY:
// upstream gates the compressor at FULL_FEATURES, so without this line a board
// can open every picture on the card and cannot write one.
// tests/test_moy_image.py pins all five boards, because a board that is missed
// fails at the moment a kid presses save and nowhere earlier.
#define MICROPY_PY_DEFLATE_COMPRESS         (1)

// The map-lookup cache, 128 -> 512 slots (384 bytes of .bss) -- the T-Deck's
// mpconfigboard.h explains the mechanism and the REPR_C half the shared build
// applies. A/B'd on the Waveshare 7B's glass (this board's twin, not re-measured here), 2026-09-21, Brick Siege, diag on,
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

// A compiled cart's timed lanes (native/moy_wasm/moy_wasm_session.h): the
// P4-only set of docs/os_2026-11.md section 5.
#define MOY_WASM_TIMED_LANES                (1)

// The engine's runtime pool, its fixed part (moy_wasm_footprint.h). A RISC-V
// load holds more than the S3's for the same cart -- its text relocations --
// and the peak follows the data segments and relocations, not the text:
// Doom's P4 module peaked at 460,392 B at 838 KB and 461,984 B at 790 KB,
// where 256 KB plus a quarter of the module sizes 471 KB and 459 KB (the
// second a load that fails). 320 KB holds it with 63 KB to spare and keeps
// holding it as the text shrinks.
#define MOY_WASM_POOL_BYTES                 (320 * 1024)

// The kernel's recovery floor (native/moy_kernel): moy_dsi's entry points, the
// 1024x600 DPI framebuffer. Its input is the GT911 touch below (MOY_INPUT_TOUCH_*).
#define MOY_KERNEL_PANEL(fn)                moy_dsi_k##fn
#define MOY_KERNEL_PANEL_W                  (1024)
#define MOY_KERNEL_PANEL_H                  (600)
#define MOY_KERNEL_PANEL_ROT                (0)
#define MOY_KERNEL_PANEL_SWAP               (0)

// The kernel's I2C bus (native/moy_kernel/moy_bus.h): I2C0 (its pins are
// board.toml's `i2c0` row, MOY_HW_I2C0_SDA/_SCL), the GT911 touch
// and the ES8311/ES7210 codecs. Its pull-ups sit on LDO4's rail.
#define MOY_BUS_I2C_PORT                    (0)
#define MOY_BUS_I2C_HZ                      (400000)

// The speaker (native/moy_audio/moy_aud_out.c, #82): the I2S amplifiers the
// vendor firmware drives directly, on the pins of board.toml's `speaker` row
// (MOY_HW_SPEAKER_*), with no codec and no master clock: the schematic's ES8311
// does not answer on this unit's I2C bus (2026-11-20), and the vendor's
// examples play through plain I2S. The amplifier's control pin is active LOW
// (the vendor firmware drives it low to play); it does not gate the speakers on
// this unit.
#define MOY_AUDIO_PA_ACTIVE_LOW             (1)
// 44.1 kHz stereo out as on the other P4s (docs/os_2026-11/hardware.md section
// 3): the mix, 22,050 Hz mono, upsampled 2x by the feeder, the streams added
// after it. Two speakers (the J4 and J6 connectors), one amplifier each, so no
// MOY_AUDIO_DAC_MONO: a codec reading one slot is what that is for.
#define MOY_AUDIO_RATE                      (44100)
#define MOY_AUDIO_STEREO                    (1)
// The feeder on core 0, off the VM's (the P4 boards' setting).
#define MOY_AUDIO_CORE                      (0)

// Input's BLE HID keyboard (native/moy_input/moy_ble_task.c): the kernel's
// central over NimBLE, its bonds in NVS. There is no `bluetooth` module.
#define MOY_INPUT_BLE                       (1)
#define MOY_INPUT_BLE_HOSTED                (1)     // the C6 over ESP-Hosted

// Input's touch (native/moy_input/moy_input_task.c): the GT911, whose address
// (0x5D or 0x14) its INT pin's level at reset picks and which this board leaves
// undriven, so the driver probes both (ADDR 0); self-configured for the
// 1024x600 panel, its status cleared before the point is read. The vendor
// firmware maps the touch with no swap or mirror; the calibration on this glass
// has not been run.
#define MOY_INPUT_TOUCH_KIND                (1)     // MOY_TOUCH_GT911
#define MOY_INPUT_TOUCH_ADDR                (0)
#define MOY_INPUT_TOUCH_CLEAR_FIRST         (1)
#define MOY_INPUT_TOUCH_SWAP                (0)
#define MOY_INPUT_TOUCH_FLIP_X              (0)
#define MOY_INPUT_TOUCH_FLIP_Y              (0)

// The glass's layer pool (native/moy_glass/moy_buf.h): the most bytes of
// released layer buffers it keeps for the next run. A window's buffer is
// never pooled (the desk re-mints at every size); a cart's worlds are.
#define MOY_GLASS_POOL_BYTES                (2 * 1024 * 1024)

// THE SHARES' ROWS (native/moy_play/moy_play.h): this image's line, named by
// its board.toml OTA id (tests/test_board_toml.py holds the two together).
#define MOY_SHARES_BOARD                    "elecrow_p4"

// The VM abort: the scheduler stops a background Python callback that never
// returns while the shell goes on (native/moy_kernel/modmoy_guard.c).
#define MICROPY_ENABLE_VM_ABORT             (1)
