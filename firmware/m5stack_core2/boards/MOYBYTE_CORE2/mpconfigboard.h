// Moybyte M5Stack Core2 v1.1 (mainline MicroPython). Board facts only --
// performance levers live in sdkconfig.board beside this file.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME               "Moybyte M5Stack Core2"
#endif
#define MICROPY_HW_MCU_NAME                 "ESP32"

// The internal I2C bus (I2C0, SDA 21 / SCL 22): the AXP2101 (0x34), the
// FT6336U touch (0x38), the BM8563 RTC (0x51), the MPU6886 IMU (0x68), and
// across the M5-Bus the Faces Gamepad3 behind the Faces Bottom3. It is the
// kernel's bus (MOY_BUS_I2C_* below): nothing opens a machine.I2C on port 0.
#define MICROPY_HW_I2C0_SCL                 (22)
#define MICROPY_HW_I2C0_SDA                 (21)

// The TF slot shares SPI2 with the panel (CS 4), the T-Deck's arrangement:
// machine.SDCard would re-initialise the host, so it is out, and the card is
// the native moy_sd's attach.
#define MICROPY_HW_ENABLE_SDCARD            (0)
#define MICROPY_FATFS_EXFAT                 (1)

#define MICROPY_PY_MACHINE_I2S              (1)

// The Python heap may grow, but never into this much of PSRAM. This chip maps
// 4 MB of its 8 MB PSRAM (the rest is himem, bank-switched, which no allocator
// here reaches), and at the launcher about 1.7 MB of it is free with the
// Python heap at 873 KB: the S3 boards' 3 MB reserve, or 2 MB, leaves the heap
// no room to grow at all, and the first cart launch raised MemoryError on a
// 32 KB allocation (2026-11-01). 1 MB keeps the Lua VM and the layer pool
// their room and lets the heap take the rest.
#define MOYBYTE_GC_SPLIT_RESERVE            (1024 * 1024)

#define MICROPY_PY_DEFLATE_COMPRESS         (1)
#define MICROPY_OPT_MAP_LOOKUP_CACHE_SIZE   (512)

#define MOY_WASM_STACK_BYTES                (16 * 1024)
#define MOY_WASM_STACK_PSRAM                (1)


// No MOY_SERIAL_RING_BYTES: the console is a UART whose ISR runs while a
// flash write has the cache off, so its ring stays the stdin-ring patch's
// internal one and never grows into PSRAM (native/moy_serial's header).

// ---- the panel: an ILI9342C on SPI2, taken through the T-Deck's moy_lcd ----
// Pins from M5Stack's Core2 schematic. SCK 18 / MOSI 23 are the VSPI IOMUX
// pins, so the clock reaches the panel without the GPIO matrix's ~40 MHz cap;
// MISO 38 is input-only and has no pull to set. There is no backlight GPIO,
// no power-on GPIO and no radio CS: the rail and the backlight are the
// AXP2101's (native/moy_core2), and the panel's reset is its rail.
#define MOY_LCD_PIN_SCK                     (18)
#define MOY_LCD_PIN_MOSI                    (23)
#define MOY_LCD_PIN_MISO                    (38)
#define MOY_LCD_PIN_DC                      (15)
#define MOY_LCD_PIN_CS                      (5)
#define MOY_LCD_PIN_BL                      (-1)
#define MOY_LCD_PIN_POWERON                 (-1)
#define MOY_LCD_PIN_SD_CS                   (4)
#define MOY_LCD_PIN_RADIO_CS                (-1)
#define MOY_LCD_MISO_NO_PULL                (1)
#define MOY_LCD_INIT_ST7789                 (0)
// Landscape-native glass: MADCTL is the BGR bit alone, and the panel needs
// INVON (M5GFX's Core2 config: invert = true).
#define MOY_LCD_MADCTL                      (0x08)
#define MOY_LCD_INVERT                      (1)
#define MOY_LCD_PCLK_HZ                     (40 * 1000 * 1000)
int moy_core2_power(void);
void moy_core2_backlight(int on);
#define MOY_LCD_POWER                       moy_core2_power
#define MOY_LCD_BACKLIGHT                   moy_core2_backlight

// The kernel's recovery floor: moy_lcd's entry points, landscape 320x240.
#define MOY_KERNEL_PANEL(fn)                moy_lcd_k##fn
#define MOY_KERNEL_PANEL_W                  (320)
#define MOY_KERNEL_PANEL_H                  (240)
#define MOY_KERNEL_PANEL_ROT                (0)
#define MOY_KERNEL_PANEL_SWAP               (1)

// The kernel's I2C bus (native/moy_kernel/moy_bus.h): I2C0.
#define MOY_BUS_I2C_PORT                    (0)
#define MOY_BUS_I2C_SDA                     MICROPY_HW_I2C0_SDA
#define MOY_BUS_I2C_SCL                     MICROPY_HW_I2C0_SCL
#define MOY_BUS_I2C_HZ                      (400000)

// Input's BLE HID keyboard: the kernel's central over NimBLE.
#define MOY_INPUT_BLE                       (1)
#define MICROPY_PY_BLUETOOTH                (0)

// Input's touch: the FT6336U at 0x38, landscape-native over the 320x240
// glass (its raw y runs on to 280 across the three touch buttons below it).
#define MOY_INPUT_TOUCH_KIND                (4)     // MOY_TOUCH_FT6X36
#define MOY_INPUT_TOUCH_ADDR                (0x38)
#define MOY_INPUT_TOUCH_SWAP                (0)
#define MOY_INPUT_TOUCH_FLIP_X              (0)
#define MOY_INPUT_TOUCH_FLIP_Y              (0)
#define MOY_INPUT_TOUCH_RAW_W               (320)
#define MOY_INPUT_TOUCH_RAW_H               (240)
#define MOY_INPUT_TOUCH_HOLD_MS             (90)

// The Faces Gamepad3 behind the Faces Bottom3: 0x08 on the same bus, its INT
// on the M5-Bus's G35 (input-only; the pad drives it). Its eight buttons are
// the table's d-pad, A, B, select and start.
#define MOY_INPUT_PAD_M5FACES               (1)
#define MOY_INPUT_PAD_INT                   (35)

// The drivers' passes on a core-0 task, one per frame's kick, as on the
// T-Deck: every device on this bus answers on the input task, never the VM's.
#define MOY_INPUT_TASK                      (1)
#define MOY_INPUT_TASK_STACK                (2560)

// Audio: the NS4168 amplifier on I2S (BCK 12, LRCK 0, DOUT 2), enabled by the
// AXP2101's ALDO3. The Faces Bottom3's RGB LED switch must sit on G25: its
// other position is G2, this data line.
#define MOY_AUDIO_I2S_BCK                   (12)
#define MOY_AUDIO_I2S_WS                    (0)
#define MOY_AUDIO_I2S_DOUT                  (2)

#define MOY_GLASS_POOL_BYTES                (160 * 1024)
#define MOY_GLASS_BANDED                    (1)

#define MICROPY_PY_ESPNOW                   (0)
#define MOY_NET_LINK                        (1)
#define MOY_NET_WIFI                        (1)
