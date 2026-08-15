// Moybyte T-Deck mainline port: ST7789 SPI panel backend, no LVGL, no fork.
//
// This is the T-Deck's answer to the P4's moy_dsi. The P4 scans a PSRAM
// framebuffer continuously (DPI, no flush); the T-Deck has to PUSH 320x240
// RGB565 down SPI2 every frame, so this module owns:
//
//   * the SPI2 bus (shared with the SD card -- see SD BUS SHARING below),
//   * the esp_lcd panel IO + ST7789 panel handle,
//   * the vendor init sequence IDF's own driver does not send (see INIT below),
//   * N PSRAM framebuffers Python draws into (fb(i)), and
//   * a banded flush that DMAs only from INTERNAL SRAM (see FLUSH below).
//
// INIT -- why IDF's ST7789 driver is not enough.
//   esp_lcd_panel_init() for ST7789 sends exactly SLPOUT / MADCTL / COLMOD, and
//   esp_lcd_new_panel_st7789() has no init_cmds/vendor_config hook to extend it.
//   Porch, gate/VCOM/power control, the two 14-byte gamma curves, INVON and
//   DISPON therefore go out here as our own esp_lcd_panel_io_tx_param() calls
//   after esp_lcd_panel_init(). The register VALUES are the ones already proven
//   on this exact glass -- transcribed from lvgl_micropython's
//   api_drivers/.../st7789/_st7789_init.py (MIT, (c) 2024-2025 Kevin G.
//   Schlosser); see THIRD_PARTY.md. Its `import lvgl` was only for orientation
//   constants, which are resolved here into one MADCTL byte (see ROTATION).
//
// ROTATION -- the T-Deck panel is portrait-native 240x320 and the console is
//   landscape 320x240. The fork reached that with LVGL rotation 270 over
//   _ORIENTATION_TABLE = (0, 160, 192, 96): index 3 = 0x60 = MV|MX, plus the
//   BGR bit 0x08 -> MADCTL 0x68. Here that is swap_xy(true) + mirror(x=true,
//   y=false) + rgb_ele_order BGR, which produces the same byte. set_madctl()
//   exists so a wrong-way-up panel can be corrected over serial without a
//   rebuild -- this driver has never been on glass, and that is the one thing a
//   compile cannot check.
//
// FLUSH -- the panel DMA only ever reads INTERNAL SRAM. Bands of BAND_ROWS rows
//   are memcpy'd PSRAM -> one of two internal DMA bounce buffers and queued.
//   That is the #66 SRAM-bounce design and it is here from the start for two
//   reasons: heavy PSRAM traffic during a PSRAM-sourced transfer starves the SPI
//   FIFO and clocks out garbage rows (the 2026-07-03 band artifacts), and a
//   PSRAM-direct transfer would additionally need the esp-idf spi_master patch.
//   Only the FIRST band carries a command (RAMWR); bands 2..N are sent with
//   lcd_cmd = -1, i.e. no command phase at all. This is what "a full-screen
//   flush must be a single tx_color" is really about: re-issuing a command
//   mid-stream is what glitches rows at the command->data boundary, and esp_lcd
//   blocks on a drained queue before any command. The window is armed once with
//   CASET/RASET, so the continuation bands just keep streaming into GRAM.
//
//   Completion is tracked by the on_color_trans_done ISR counter, so a bounce
//   slot is never refilled while its DMA is in flight -- show() does not rely on
//   spi_device_acquire_bus() happening to serialize the bands, which is exactly
//   what the #66 no-acquire patch removes when it is applied.
//
// SD BUS SHARING -- this module runs spi_bus_initialize() ONCE and never tears
//   it down. The SD card attaches to the ALREADY-INITIALIZED host through
//   native/moy_sd (sdspi_host_init_device, no bus re-init). Nothing may touch
//   SD before init() has run, no SD device may be torn down between ops, and no
//   panel flush may overlap an SD session -- see CLAUDE.md's hard constraints,
//   every line of which was learned by hanging a board.

#include <string.h>

#include "py/runtime.h"
#include "py/objarray.h"
#include "py/objtuple.h"
#include "py/mphal.h"
#include "py/mpthread.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"   // pulls in esp_lcd_panel_st7789.h
#include "esp_rom_sys.h"
#include "esp_timer.h"

// ---- board facts (firmware/lilygo_t_deck_plus_micropython/modules/tdeck_*.py)
#define MOY_LCD_W            320
#define MOY_LCD_H            240
#define MOY_LCD_FB_BYTES     (MOY_LCD_W * MOY_LCD_H * 2)
#define MOY_LCD_ROW_BYTES    (MOY_LCD_W * 2)

#define MOY_LCD_SPI_HOST     SPI2_HOST   // == 1, the host moy_sd attaches to
#define MOY_LCD_PIN_SCK      40
#define MOY_LCD_PIN_MOSI     41
#define MOY_LCD_PIN_MISO     38
#define MOY_LCD_PIN_DC       11
#define MOY_LCD_PIN_CS       12
#define MOY_LCD_PIN_BL       42
#define MOY_LCD_PIN_POWERON  10          // board power rail (peripherals + panel)
#define MOY_LCD_PIN_SD_CS    39
#define MOY_LCD_PIN_RADIO_CS 9           // unused LoRa module; park it high

// 80 MHz is requested, not delivered: none of MOSI/SCK/MISO are the S3's
// IOMUX-native FSPI pins, so every signal routes through the GPIO matrix, which
// caps a write-only LCD at ~40 MHz. esp_lcd's divider clamps to what it can do.
// Kept at the fork's requested value so the two builds are comparable; drop to
// 62500000 then 40000000 if the panel tears or garbles.
#define MOY_LCD_PCLK_HZ      (80 * 1000 * 1000)

// Bands of 48 rows = 30720 B. 24-row bands transfer in ~1.5ms, which on the
// fork build was FASTER than its 2ms pump timer, so the SPI starved between
// fires; 48 stays ahead. Two slots = 2 x 30720 B of internal DMA SRAM.
#define MOY_LCD_BAND_ROWS    48
#define MOY_LCD_BAND_BYTES   (MOY_LCD_BAND_ROWS * MOY_LCD_ROW_BYTES)
#define MOY_LCD_BOUNCE_SLOTS 2
#define MOY_LCD_MAX_FBS      3
#define MOY_LCD_FLUSH_TIMEOUT_US 500000  // a full frame is ~20ms; this is a bug fence

// ST7789 command bytes used directly (the ones esp_lcd's driver does not send).
#define CMD_NORON     0x13
#define CMD_CASET     0x2A
#define CMD_RASET     0x2B
#define CMD_RAMWR     0x2C
#define CMD_MADCTL    0x36
#define CMD_B6        0xB6   // undocumented on ST7789; proven on this glass
#define CMD_PORCTRL   0xB2
#define CMD_GCTRL     0xB7
#define CMD_VCOMS     0xBB
#define CMD_LCMCTRL   0xC0
#define CMD_VDVVRHEN  0xC2
#define CMD_VRHS      0xC3
#define CMD_VDVSET    0xC4
#define CMD_FRCTR2    0xC6
#define CMD_PWCTRL1   0xD0
#define CMD_PGC       0xE0
#define CMD_NGC       0xE1

static esp_lcd_panel_io_handle_t s_io;
static esp_lcd_panel_handle_t s_panel;
static uint8_t *s_fbs[MOY_LCD_MAX_FBS];
static int s_nfbs;
static uint8_t *s_bounce[MOY_LCD_BOUNCE_SLOTS];
static volatile uint32_t s_done;       // bands whose DMA completed (ISR)
static uint32_t s_flushes;
static uint32_t s_last_flush_us;
static bool s_bus_up;
static uint8_t s_madctl;

static void moy_lcd_check(esp_err_t err, const char *what) {
    if (err != ESP_OK) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd %s: err 0x%x"),
                          what, (unsigned)err);
    }
}

static void moy_lcd_require(void) {
    if (s_panel == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd not initialized"));
    }
}

static bool moy_lcd_trans_done(esp_lcd_panel_io_handle_t io,
                               esp_lcd_panel_io_event_data_t *edata, void *ctx) {
    (void)io; (void)edata; (void)ctx;
    s_done++;
    return false;   // no task woken
}

// One init-sequence entry: command + up to 14 params + a post-delay.
typedef struct {
    uint8_t cmd;
    uint8_t len;
    uint16_t delay_ms;
    uint8_t data[14];
} moy_lcd_cmd_t;

// The vendor block IDF's esp_lcd_panel_st7789 does not send. Order and values
// follow lvgl_micropython's _st7789_init.py (MIT, see the header note), minus
// the pieces esp_lcd_panel_reset/init already did (SWRESET, SLPOUT, MADCTL,
// COLMOD) and minus the I80-only RAMCTRL byte-swap branch, which does not apply
// to an SPI panel.
static const moy_lcd_cmd_t MOY_LCD_INIT[] = {
    { CMD_NORON,    0,  10, {0} },
    { CMD_B6,       2,   0, {0x0A, 0x82} },
    { CMD_PORCTRL,  5,   0, {0x0C, 0x0C, 0x00, 0x33, 0x33} },
    { CMD_GCTRL,    1,   0, {0x35} },
    { CMD_VCOMS,    1,   0, {0x28} },
    { CMD_LCMCTRL,  1,   0, {0x0C} },
    { CMD_VDVVRHEN, 1,   0, {0x01} },
    { CMD_VRHS,     1,   0, {0x13} },
    { CMD_VDVSET,   1,   0, {0x20} },
    { CMD_FRCTR2,   1,   0, {0x0F} },
    { CMD_PWCTRL1,  2,   0, {0xA4, 0xA1} },
    { CMD_PGC,     14,   0, {0xD0, 0x00, 0x02, 0x07, 0x0A, 0x28, 0x32,
                             0x44, 0x42, 0x06, 0x0E, 0x12, 0x14, 0x17} },
    { CMD_NGC,     14,   0, {0xD0, 0x00, 0x02, 0x07, 0x0A, 0x28, 0x31,
                             0x54, 0x47, 0x0E, 0x1C, 0x17, 0x1B, 0x1E} },
};

static void moy_lcd_park_pin(int gpio, int level) {
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << gpio,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
    gpio_set_level(gpio, level);
}

static void moy_lcd_free_all(void) {
    for (int i = 0; i < MOY_LCD_MAX_FBS; i++) {
        if (s_fbs[i]) { heap_caps_free(s_fbs[i]); s_fbs[i] = NULL; }
    }
    s_nfbs = 0;
    for (int i = 0; i < MOY_LCD_BOUNCE_SLOTS; i++) {
        if (s_bounce[i]) { heap_caps_free(s_bounce[i]); s_bounce[i] = NULL; }
    }
}

// init(nfbs=2, pclk_hz=80000000) -> None
static mp_obj_t moy_lcd_init(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_nfbs,    MP_ARG_INT, { .u_int = 2 } },
        { MP_QSTR_pclk_hz, MP_ARG_INT, { .u_int = MOY_LCD_PCLK_HZ } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos, kw, MP_ARRAY_SIZE(allowed), allowed, args);

    if (s_panel != NULL) {
        return mp_const_none;
    }
    int nfbs = args[0].u_int;
    if (nfbs < 1 || nfbs > MOY_LCD_MAX_FBS) {
        mp_raise_ValueError(MP_ERROR_TEXT("nfbs 1..3"));
    }

    // Board rail + every OTHER chip select on this bus parked inactive-high
    // BEFORE the bus exists. TFT_CS is parked too and then handed to esp_lcd,
    // which is what the fork's tdeck_board.init_board_pins does; what the hard
    // constraints forbid is re-creating a Pin on TFT_CS/SD_CS AFTERWARDS, while
    // a driver owns it.
    moy_lcd_park_pin(MOY_LCD_PIN_POWERON, 1);
    moy_lcd_park_pin(MOY_LCD_PIN_SD_CS, 1);
    moy_lcd_park_pin(MOY_LCD_PIN_RADIO_CS, 1);
    moy_lcd_park_pin(MOY_LCD_PIN_CS, 1);
    // Backlight OFF until the first composed frame has been flushed (#45): the
    // ST7789's power-on GRAM is noise and the user must never see it lit.
    moy_lcd_park_pin(MOY_LCD_PIN_BL, 0);
    gpio_set_pull_mode(MOY_LCD_PIN_MISO, GPIO_PULLUP_ONLY);

    // Framebuffers in PSRAM, bounce buffers in internal DMA-capable SRAM.
    // Allocated BEFORE the bus so a memory failure leaves nothing half-built.
    for (int i = 0; i < nfbs; i++) {
        s_fbs[i] = heap_caps_malloc(MOY_LCD_FB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_fbs[i] == NULL) {
            moy_lcd_free_all();
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_lcd: no PSRAM framebuffer"));
        }
        memset(s_fbs[i], 0, MOY_LCD_FB_BYTES);
    }
    s_nfbs = nfbs;
    for (int i = 0; i < MOY_LCD_BOUNCE_SLOTS; i++) {
        s_bounce[i] = heap_caps_malloc(MOY_LCD_BAND_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (s_bounce[i] == NULL) {
            moy_lcd_free_all();
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_lcd: no internal DMA bounce"));
        }
    }

    spi_bus_config_t bus_cfg = {
        .sclk_io_num = MOY_LCD_PIN_SCK,
        .mosi_io_num = MOY_LCD_PIN_MOSI,
        .miso_io_num = MOY_LCD_PIN_MISO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        // Sized for a whole frame even though the flush bands: it costs only DMA
        // descriptors, and it leaves room for a future PSRAM-direct transfer.
        .max_transfer_sz = MOY_LCD_FB_BYTES + 64,
    };
    esp_err_t err = spi_bus_initialize(MOY_LCD_SPI_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        moy_lcd_free_all();
        moy_lcd_check(err, "spi_bus_initialize");
    }
    s_bus_up = true;

    esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = MOY_LCD_PIN_CS,
        .dc_gpio_num = MOY_LCD_PIN_DC,
        .spi_mode = 0,
        .pclk_hz = args[1].u_int,
        .trans_queue_depth = 10,
        .on_color_trans_done = moy_lcd_trans_done,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    moy_lcd_check(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)MOY_LCD_SPI_HOST,
                                           &io_cfg, &s_io), "panel_io_spi");

    esp_lcd_panel_dev_config_t panel_cfg = {
        // The T-Deck does not wire a panel reset GPIO; esp_lcd falls back to a
        // SWRESET over SPI, which is what the fork's driver did too.
        .reset_gpio_num = -1,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel = 16,
    };
    moy_lcd_check(esp_lcd_new_panel_st7789(s_io, &panel_cfg, &s_panel), "panel_st7789");

    moy_lcd_check(esp_lcd_panel_reset(s_panel), "reset");
    mp_hal_delay_ms(120);                 // the fork waits 120ms; esp_lcd waits 20
    moy_lcd_check(esp_lcd_panel_init(s_panel), "init");   // SLPOUT + MADCTL + COLMOD
    mp_hal_delay_ms(10);

    // Landscape: MV|MX (+ the BGR bit the panel config already carries) == 0x68.
    moy_lcd_check(esp_lcd_panel_swap_xy(s_panel, true), "swap_xy");
    moy_lcd_check(esp_lcd_panel_mirror(s_panel, true, false), "mirror");
    s_madctl = 0x68;

    for (size_t i = 0; i < MP_ARRAY_SIZE(MOY_LCD_INIT); i++) {
        const moy_lcd_cmd_t *c = &MOY_LCD_INIT[i];
        moy_lcd_check(esp_lcd_panel_io_tx_param(s_io, c->cmd,
                                                c->len ? c->data : NULL, c->len), "init cmd");
        if (c->delay_ms) {
            mp_hal_delay_ms(c->delay_ms);
        }
    }

    moy_lcd_check(esp_lcd_panel_invert_color(s_panel, true), "invert");   // INVON
    moy_lcd_check(esp_lcd_panel_disp_on_off(s_panel, true), "disp_on");
    mp_hal_delay_ms(120);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(moy_lcd_init_obj, 0, moy_lcd_init);

static mp_obj_t moy_lcd_deinit(void) {
    if (s_panel) { esp_lcd_panel_del(s_panel); s_panel = NULL; }
    if (s_io) { esp_lcd_panel_io_del(s_io); s_io = NULL; }
    if (s_bus_up) { spi_bus_free(MOY_LCD_SPI_HOST); s_bus_up = false; }
    moy_lcd_free_all();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_deinit_obj, moy_lcd_deinit);

// fb([n]) -> writable memoryview over framebuffer n (default 0).
static mp_obj_t moy_lcd_fb(size_t n_args, const mp_obj_t *a) {
    if (s_nfbs == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd not initialized"));
    }
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    return mp_obj_new_memoryview('B' | MP_OBJ_ARRAY_TYPECODE_FLAG_RW,
                                 MOY_LCD_FB_BYTES, s_fbs[n]);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_fb_obj, 0, 1, moy_lcd_fb);

static mp_obj_t moy_lcd_nfbs(void) {
    return MP_OBJ_NEW_SMALL_INT(s_nfbs);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_nfbs_obj, moy_lcd_nfbs);

static void moy_lcd_arm_window(void) {
    uint8_t p[4];
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_W - 1) >> 8; p[3] = (MOY_LCD_W - 1) & 0xFF;
    moy_lcd_check(esp_lcd_panel_io_tx_param(s_io, CMD_CASET, p, 4), "caset");
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_H - 1) >> 8; p[3] = (MOY_LCD_H - 1) & 0xFF;
    moy_lcd_check(esp_lcd_panel_io_tx_param(s_io, CMD_RASET, p, 4), "raset");
}

// Wait until at least `target` band completions have been counted, or the
// deadline passes. Returns false on timeout. Runs with the GIL released.
static bool moy_lcd_wait_done(uint32_t target, int64_t deadline_us) {
    while (s_done < target) {
        if (esp_timer_get_time() > deadline_us) {
            return false;
        }
        esp_rom_delay_us(20);
    }
    return true;
}

// show(n=0): push framebuffer n to the panel and BLOCK until it is fully out.
// Blocking is deliberate at this stage: the SD card shares this SPI host, so a
// flush that outlived its call would have to be fenced before every SD op.
static mp_obj_t moy_lcd_show(size_t n_args, const mp_obj_t *a) {
    moy_lcd_require();
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    const uint8_t *src = s_fbs[n];
    int64_t t0 = esp_timer_get_time();

    moy_lcd_arm_window();
    s_done = 0;

    esp_err_t err = ESP_OK;
    bool timed_out = false;
    uint32_t queued = 0;

    // Only pure IDF work happens below, so the GIL can go -- an input poller
    // thread (#69) must not stall behind a ~20ms panel push.
    MP_THREAD_GIL_EXIT();
    int64_t deadline = t0 + MOY_LCD_FLUSH_TIMEOUT_US;
    for (int y = 0, k = 0; y < MOY_LCD_H; k++) {
        int rows = (y + MOY_LCD_BAND_ROWS <= MOY_LCD_H) ? MOY_LCD_BAND_ROWS : (MOY_LCD_H - y);
        size_t nbytes = (size_t)rows * MOY_LCD_ROW_BYTES;
        // Slot k % SLOTS is free once band k-SLOTS has completed.
        if (k >= MOY_LCD_BOUNCE_SLOTS) {
            if (!moy_lcd_wait_done((uint32_t)(k - MOY_LCD_BOUNCE_SLOTS + 1), deadline)) {
                timed_out = true;
                break;
            }
        }
        uint8_t *slot = s_bounce[k % MOY_LCD_BOUNCE_SLOTS];
        memcpy(slot, src + (size_t)y * MOY_LCD_ROW_BYTES, nbytes);
        // Band 0 carries RAMWR; every continuation band sends NO command, which
        // is what keeps esp_lcd from blocking on a drained queue and what keeps
        // the ST7789 streaming into the window armed above.
        err = esp_lcd_panel_io_tx_color(s_io, (k == 0) ? CMD_RAMWR : -1, slot, nbytes);
        if (err != ESP_OK) {
            break;
        }
        queued++;
        y += rows;
    }
    if (err == ESP_OK && !timed_out) {
        timed_out = !moy_lcd_wait_done(queued, deadline);
    }
    MP_THREAD_GIL_ENTER();

    moy_lcd_check(err, "tx_color");
    if (timed_out) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd: flush timed out"));
    }
    s_flushes++;
    s_last_flush_us = (uint32_t)(esp_timer_get_time() - t0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_show_obj, 0, 1, moy_lcd_show);

// backlight(on) -- active HIGH on GPIO42. Plain on/off; the fork drove PWM duty
// through LVGL's driver, which nothing in the console ever used for dimming.
static mp_obj_t moy_lcd_backlight(mp_obj_t on_in) {
    gpio_set_level(MOY_LCD_PIN_BL, mp_obj_is_true(on_in) ? 1 : 0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lcd_backlight_obj, moy_lcd_backlight);

// set_madctl(v) -- write MADCTL (0x36) directly. The bring-up escape hatch: if
// the first frame is upside down or mirrored, try 0x28 / 0xA8 / 0xE8 / 0x68 over
// serial instead of rebuilding. Bits: MY 0x80, MX 0x40, MV 0x20, BGR 0x08.
static mp_obj_t moy_lcd_set_madctl(mp_obj_t v_in) {
    moy_lcd_require();
    uint8_t v = (uint8_t)mp_obj_get_int(v_in);
    moy_lcd_check(esp_lcd_panel_io_tx_param(s_io, CMD_MADCTL, &v, 1), "madctl");
    s_madctl = v;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lcd_set_madctl_obj, moy_lcd_set_madctl);

static mp_obj_t moy_lcd_madctl(void) {
    return MP_OBJ_NEW_SMALL_INT(s_madctl);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_madctl_obj, moy_lcd_madctl);

// bars(fb=0) -- paint a recognisable test pattern into a framebuffer, in C, so
// stage 1 can be judged with no moy_gfx, no console and no Python raster in the
// picture. Eight vertical colour bars over the top 3/4; the bottom quarter is a
// white/black checker, which is where a byte-order or stride mistake shows.
//
// Pixels are written high byte FIRST: the panel takes RGB565 big-endian on the
// wire, so a canonical uint16 stored little-endian would come out swapped --
// which is exactly why the shared palette on this board is PAL565_SW.
static mp_obj_t moy_lcd_bars(size_t n_args, const mp_obj_t *a) {
    if (s_nfbs == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd not initialized"));
    }
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    static const uint16_t BARS[8] = {
        0xFFFF, // white
        0xFFE0, // yellow
        0x07FF, // cyan
        0x07E0, // green
        0xF81F, // magenta
        0xF800, // red
        0x001F, // blue
        0x0000, // black
    };
    uint8_t *fb = s_fbs[n];
    const int split = MOY_LCD_H * 3 / 4;
    for (int y = 0; y < MOY_LCD_H; y++) {
        uint8_t *row = fb + (size_t)y * MOY_LCD_ROW_BYTES;
        for (int x = 0; x < MOY_LCD_W; x++) {
            uint16_t c;
            if (y < split) {
                c = BARS[(x * 8) / MOY_LCD_W];
            } else {
                c = (((x >> 4) + (y >> 4)) & 1) ? 0xFFFF : 0x0000;
            }
            row[x * 2] = (uint8_t)(c >> 8);
            row[x * 2 + 1] = (uint8_t)(c & 0xFF);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_bars_obj, 0, 1, moy_lcd_bars);

// stats() -> (flushes, last_flush_us). The serial-only verification handle: this
// board's USB-CDC RX is dead under a takeover loop, so numbers have to come OUT.
static mp_obj_t moy_lcd_stats(void) {
    mp_obj_t t[2] = {
        mp_obj_new_int_from_uint(s_flushes),
        mp_obj_new_int_from_uint(s_last_flush_us),
    };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_stats_obj, moy_lcd_stats);

static const mp_rom_map_elem_t moy_lcd_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_ROM_QSTR(MP_QSTR_moy_lcd) },
    { MP_ROM_QSTR(MP_QSTR_init),       MP_ROM_PTR(&moy_lcd_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit),     MP_ROM_PTR(&moy_lcd_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_fb),         MP_ROM_PTR(&moy_lcd_fb_obj) },
    { MP_ROM_QSTR(MP_QSTR_nfbs),       MP_ROM_PTR(&moy_lcd_nfbs_obj) },
    { MP_ROM_QSTR(MP_QSTR_show),       MP_ROM_PTR(&moy_lcd_show_obj) },
    { MP_ROM_QSTR(MP_QSTR_backlight),  MP_ROM_PTR(&moy_lcd_backlight_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_madctl), MP_ROM_PTR(&moy_lcd_set_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_madctl),     MP_ROM_PTR(&moy_lcd_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_bars),       MP_ROM_PTR(&moy_lcd_bars_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats),      MP_ROM_PTR(&moy_lcd_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_WIDTH),      MP_ROM_INT(MOY_LCD_W) },
    { MP_ROM_QSTR(MP_QSTR_HEIGHT),     MP_ROM_INT(MOY_LCD_H) },
    // The framebuffer stores RGB565 with the two bytes already in wire order
    // (high byte first). The shared palette on this board is PAL565_SW for the
    // same reason; nothing downstream should byte-swap again.
    { MP_ROM_QSTR(MP_QSTR_BYTE_SWAP),  MP_ROM_TRUE },
    { MP_ROM_QSTR(MP_QSTR_SPI_HOST),   MP_ROM_INT(MOY_LCD_SPI_HOST) },
    { MP_ROM_QSTR(MP_QSTR_SD_CS),      MP_ROM_INT(MOY_LCD_PIN_SD_CS) },
};
static MP_DEFINE_CONST_DICT(moy_lcd_module_globals, moy_lcd_module_globals_table);

const mp_obj_module_t moy_lcd_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_lcd_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_lcd, moy_lcd_module);
