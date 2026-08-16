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
//   slot is never refilled while its DMA is in flight -- the flush does not rely
//   on spi_device_acquire_bus() happening to serialize the bands, which is
//   exactly what the #66 no-acquire patch removes when it is applied.
//
// OVERLAP (#66/#43) -- the flush is SPLIT, and that split is the whole point.
//   320x240x2 = 153,600 B is ~17 ms on this bus. Paid synchronously it caps the
//   loop near 58 fps before a single pixel is drawn, which is exactly what the
//   first mainline console measured (flush=16.8..20.2 vs the fork's 2.1). The
//   fork does not pay it in the frame: it queues the first bands and RETURNS,
//   then feeds the rest while the CPU renders the NEXT frame into the other
//   ping-pong buffer, so a frame costs max(render, transfer) instead of their
//   sum. The same three verbs are here now:
//
//     kick(n)   arm the window, reset the band bookkeeping, copy+queue the first
//               BOUNCE_SLOTS bands (~6 ms of transfer buffered), RETURN.
//     pump()    copy+queue every band whose bounce slot has since freed. Costs
//               ~0.8 ms per band (one 30 KB PSRAM->SRAM memcpy + an async queue,
//               non-blocking thanks to the no-acquire patch). A no-op when
//               nothing is in flight, so it is safe to call from anywhere.
//     drain()   finish feeding, then wait out the tail. Called at the top of the
//               next flush (where most of it has already happened behind the
//               render) and before any SD op -- the card shares this SPI host.
//
//   WHO CALLS pump(). Two feeders, both from tdeck_panel/DeviceCanvas, and both
//   are needed: a 2 ms machine.Timer (esp32 timers schedule via mp_sched, so the
//   callback lands between bytecodes -- the only feeder during a cart's long
//   Python _update), and a poke from the big native draw verbs (the soft timer
//   CANNOT fire while the interpreter sits inside one 15 ms C fill; that
//   measured as PUMP idle=2-6 ms of starved SPI on the fork). A dead feeder
//   degrades to drain() doing all the work -- a serialized flush, i.e. exactly
//   today's cost -- never to corruption: the front buffer is immutable while it
//   ships, so bands are tear-free by construction.
//
//   show(n) remains kick+drain in one blocking call, because the bring-up smokes
//   want one number and no ping-pong reasoning.
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
static volatile uint32_t s_done_us;    // when the last one completed (ISR)
static uint32_t s_flushes;
static uint32_t s_last_flush_us;
static bool s_bus_up;
static uint8_t s_madctl;

// --- the in-flight flush (see OVERLAP in the header) -------------------------
// s_bnc_total == 0 means "nothing in flight"; it is the one idle test. The band
// index and the queued count are separate because they are separately paced:
// s_bnc_next is what the CPU has FED, s_target is what esp_lcd has been HANDED,
// and s_done is what the panel has ACCEPTED. Band k may reuse bounce slot
// k % SLOTS once band k-SLOTS has completed, i.e. once s_done >= k-(SLOTS-1).
static int s_bnc_total;                // bands in the frame being shipped
static int s_bnc_next;                 // next band to copy + queue
static const uint8_t *s_bnc_src;       // the FRONT framebuffer (immutable while it ships)
static uint32_t s_target;              // bands queued so far this frame
static bool s_in_pump;                 // reentrancy guard (timer fire inside a poke)
static esp_err_t s_tx_err;             // a queue failure, reported by the next drain
static int64_t s_flush_t0;             // kick -> fully-out span, for stats()
static uint32_t s_timeouts;

// Feed PACING for the frame in flight, latched into the *_last pair at the next
// kick (the frame is complete by then -- drain ran first). This is the data the
// PUMP diag line prints, and the reason it exists is that "the flush is slow" and
// "the flush is fed late" look identical from the outside: idle_us ~ 0 means the
// ceiling is real transfer time and a bigger band / faster feeder buys nothing.
static uint32_t s_pump_us;             // CPU us spent inside pump() this frame
static uint32_t s_idle_us;             // us the SPI sat starved waiting to be fed
static uint32_t s_idle_n;              // how many bands were fed that late
static int32_t s_feed_us = -1;         // kick -> last band queued
static uint32_t s_kick_us;             // when the kick happened (us, wrapping)
static uint32_t s_block_us;            // CPU us BLOCKED in kick+drain this frame
static uint32_t s_pump_last_us;
static uint32_t s_idle_last_us;
static uint32_t s_idle_last_n;
static int32_t s_feed_last_us = -1;
static uint32_t s_block_last_us;

static bool moy_lcd_drain_locked(void);   // defined below; deinit fences on it

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

// SPI completion ISR: one call per band (esp_lcd sets en_trans_done_cb only on
// the last chunk of a tx_color, and a 30 KB band is one chunk). Both stores are
// single 32-bit words, so the pump can read them without a lock; s_done_us is
// deliberately the LOW 32 bits of the timer, because a 64-bit store would tear
// against a reader and the only use is a wrap-safe uint32 subtraction.
static bool moy_lcd_trans_done(esp_lcd_panel_io_handle_t io,
                               esp_lcd_panel_io_event_data_t *edata, void *ctx) {
    (void)io; (void)edata; (void)ctx;
    s_done++;
    s_done_us = (uint32_t)esp_timer_get_time();
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
    // The band state points into buffers that no longer exist.
    s_bnc_total = 0;
    s_bnc_next = 0;
    s_bnc_src = NULL;
    s_done = 0;
    s_target = 0;
    s_tx_err = ESP_OK;
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
    // Never tear a driver down under a live DMA: the bounce buffers are freed
    // below and the SPI host is handed back, both of which an in-flight band
    // would still be reading.
    if (s_panel && s_bnc_total != 0) { moy_lcd_drain_locked(); }
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

static int moy_lcd_bands(void) {
    return (MOY_LCD_H + MOY_LCD_BAND_ROWS - 1) / MOY_LCD_BAND_ROWS;
}

// Copy + queue every band whose bounce slot has freed. The whole overlap rests
// on this being CHEAP and NON-BLOCKING: one 30 KB PSRAM->SRAM memcpy and one
// esp_lcd_panel_io_tx_color per band, and the continuation bands (lcd_cmd = -1)
// skip spi_device_acquire_bus entirely thanks to the #66 no-acquire patch, so
// they queue and return instead of waiting out the band before them.
//
// Reentrancy: the 2 ms timer schedules through mp_sched, which can land between
// any two bytecodes -- including inside a draw verb that is itself poking the
// pump. The GIL means the two cannot interleave mid-body, but a nested call
// would still double-feed a slot, so it no-ops instead.
//
// The GIL is deliberately HELD here. The body is ~0.8 ms and it runs from inside
// native draw ops; releasing and reacquiring per call would cost more than it
// could ever hand to the poller thread, whose stalls are 20-60 ms anyway.
static void moy_lcd_pump_locked(void) {
    if (s_in_pump || s_bnc_total == 0 || s_bnc_next >= s_bnc_total) {
        return;
    }
    s_in_pump = true;
    uint32_t p0 = (uint32_t)esp_timer_get_time();
    int k = s_bnc_next;
    const int slots = MOY_LCD_BOUNCE_SLOTS;
    while (k < s_bnc_total && (int32_t)s_done >= k - (slots - 1)) {
        // Pacing probe: about to feed band k while everything already queued has
        // COMPLETED means the SPI has been idle since that last completion. Band
        // 0 follows a drained bus by design, so it is never a gap.
        if (k > 0 && s_done >= s_target) {
            uint32_t gap = (uint32_t)esp_timer_get_time() - s_done_us;
            if (gap > 0 && gap < 1000000u) {   // a torn/absurd read is not a gap
                s_idle_us += gap;
                s_idle_n++;
            }
        }
        int y = k * MOY_LCD_BAND_ROWS;
        int rows = (y + MOY_LCD_BAND_ROWS <= MOY_LCD_H)
                   ? MOY_LCD_BAND_ROWS : (MOY_LCD_H - y);
        size_t nbytes = (size_t)rows * MOY_LCD_ROW_BYTES;
        uint8_t *slot = s_bounce[k % slots];
        memcpy(slot, s_bnc_src + (size_t)y * MOY_LCD_ROW_BYTES, nbytes);
        // Band 0 carries RAMWR; every continuation band sends NO command, which
        // is what keeps esp_lcd from blocking on a drained queue and what keeps
        // the ST7789 streaming into the window armed at the kick.
        esp_err_t err = esp_lcd_panel_io_tx_color(s_io, (k == 0) ? CMD_RAMWR : -1,
                                                  slot, nbytes);
        if (err != ESP_OK) {
            s_tx_err = err;     // the next drain reports it; do not raise in a timer
            break;
        }
        s_target++;
        k++;
        s_bnc_next = k;
    }
    uint32_t now = (uint32_t)esp_timer_get_time();
    s_pump_us += now - p0;
    if (s_bnc_next >= s_bnc_total && s_feed_us < 0) {
        s_feed_us = (int32_t)(now - s_kick_us);
    }
    s_in_pump = false;
}

// Start shipping framebuffer n and return. Caller must have drained first (this
// asserts it by draining itself, which is a no-op on the ordinary path).
//
// Order matters: arm_window() goes out BEFORE the counters are reset, because
// esp_lcd's tx_param recycles every in-flight colour transaction before it sends
// a command. That is what makes the reset safe -- every completion ISR of the
// previous frame has already run, so none of them can land on the new count. It
// is also the recovery point after a timed-out flush left bands in flight.
static void moy_lcd_kick_locked(int n) {
    // Latch the pacing of the frame that just finished (drain ran before this).
    s_pump_last_us = s_pump_us;
    s_idle_last_us = s_idle_us;
    s_idle_last_n = s_idle_n;
    s_feed_last_us = s_feed_us;
    s_block_last_us = s_block_us;
    s_pump_us = 0;
    s_idle_us = 0;
    s_idle_n = 0;
    s_feed_us = -1;
    s_block_us = 0;

    uint32_t b0 = (uint32_t)esp_timer_get_time();
    moy_lcd_arm_window();
    s_done = 0;
    s_target = 0;
    s_bnc_next = 0;
    s_bnc_src = s_fbs[n];
    s_bnc_total = moy_lcd_bands();
    s_flush_t0 = esp_timer_get_time();
    s_kick_us = (uint32_t)s_flush_t0;
    moy_lcd_pump_locked();          // fill both bounce slots, then RETURN
    s_block_us += (uint32_t)esp_timer_get_time() - b0;
}

// Finish the in-flight flush: feed whatever the pump has not, then wait out the
// tail. Returns false on a timeout (bands may still be in flight -- the next
// kick's arm_window recovers, because a command waits for the queue to drain).
static bool moy_lcd_drain_locked(void) {
    if (s_bnc_total == 0) {
        return true;
    }
    uint32_t b0 = (uint32_t)esp_timer_get_time();
    int64_t deadline = esp_timer_get_time() + MOY_LCD_FLUSH_TIMEOUT_US;
    bool ok = true;
    // A queue error stops the FEED (there is no point copying more bands into a
    // stream esp_lcd refused) but it is not a reason to stop the loop early
    // without it: without the s_tx_err term the next iteration would wait on a
    // completion that has already arrived, find s_bnc_next unmoved, and spin
    // there until the deadline.
    while (s_bnc_next < s_bnc_total && s_tx_err == ESP_OK) {
        int before = s_bnc_next;
        moy_lcd_pump_locked();
        if (s_bnc_next == before && s_tx_err == ESP_OK) {
            // No slot free. Band s_bnc_next needs completion s_bnc_next-SLOTS+1.
            int need = s_bnc_next - MOY_LCD_BOUNCE_SLOTS + 1;
            if (need < 1) {
                need = 1;
            }
            MP_THREAD_GIL_EXIT();
            ok = moy_lcd_wait_done((uint32_t)need, deadline);
            MP_THREAD_GIL_ENTER();
            if (!ok) {
                break;
            }
        }
    }
    // Whatever was QUEUED must finish before this may claim the bus is idle --
    // including after a queue error, whose already-queued bands are unaffected
    // and still reading a bounce buffer. This is the promise SD relies on.
    if (ok) {
        MP_THREAD_GIL_EXIT();
        ok = moy_lcd_wait_done(s_target, deadline);
        MP_THREAD_GIL_ENTER();
    }
    bool clean = ok && (s_tx_err == ESP_OK);
    s_bnc_total = 0;
    s_bnc_next = 0;
    s_bnc_src = NULL;
    if (clean) {
        s_flushes++;
        s_last_flush_us = (uint32_t)(esp_timer_get_time() - s_flush_t0);
    } else if (!ok) {
        s_timeouts++;
    }
    s_block_us += (uint32_t)esp_timer_get_time() - b0;
    return clean;
}

static int moy_lcd_fb_index(size_t n_args, const mp_obj_t *a) {
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    return (int)n;
}

// kick(n=0): begin shipping framebuffer n; returns immediately. The caller owns
// the ping-pong -- n must be the buffer it has STOPPED drawing into.
static mp_obj_t moy_lcd_kick(size_t n_args, const mp_obj_t *a) {
    moy_lcd_require();
    int n = moy_lcd_fb_index(n_args, a);
    if (s_bnc_total != 0) {
        moy_lcd_drain_locked();     // defensive: kick without a flush() before it
    }
    moy_lcd_kick_locked(n);
    if (s_tx_err != ESP_OK) {
        esp_err_t e = s_tx_err;
        s_tx_err = ESP_OK;
        moy_lcd_check(e, "tx_color");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_kick_obj, 0, 1, moy_lcd_kick);

// pump(_=None): feed the in-flight flush. A no-op (a handful of compares) when
// nothing is pending, so every caller can call it unconditionally. Takes an
// optional argument so it can BE the machine.Timer callback, which is handed the
// timer object -- one C call per fire, no Python frame in between.
static mp_obj_t moy_lcd_pump(size_t n_args, const mp_obj_t *a) {
    (void)n_args; (void)a;
    if (s_bnc_total != 0 && s_bnc_next < s_bnc_total) {
        moy_lcd_pump_locked();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_pump_obj, 0, 1, moy_lcd_pump);

// drain() -> True if the frame went out cleanly. Never raises: it is called at
// the top of every flush and before every SD op, and a torn frame is a better
// outcome there than an exception through the frame loop. pump_stats()[6] counts
// the timeouts; a queue error is raised by the next kick.
static mp_obj_t moy_lcd_drain(void) {
    if (s_panel == NULL) {
        return mp_const_true;
    }
    bool ok = moy_lcd_drain_locked();
    return ok ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_drain_obj, moy_lcd_drain);

// pending() -> True while a flush is in flight (fed or not). What an SD op or a
// teardown has to see as False.
static mp_obj_t moy_lcd_pending(void) {
    return (s_bnc_total != 0) ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_pending_obj, moy_lcd_pending);

// show(n=0): push framebuffer n and BLOCK until it is fully out. kick + drain in
// one call -- what the bring-up smokes want, and the always-correct fallback.
static mp_obj_t moy_lcd_show(size_t n_args, const mp_obj_t *a) {
    moy_lcd_require();
    int n = moy_lcd_fb_index(n_args, a);
    if (s_bnc_total != 0) {
        moy_lcd_drain_locked();
    }
    moy_lcd_kick_locked(n);
    bool ok = moy_lcd_drain_locked();
    if (s_tx_err != ESP_OK) {
        esp_err_t e = s_tx_err;
        s_tx_err = ESP_OK;
        moy_lcd_check(e, "tx_color");
    }
    if (!ok) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_lcd: flush timed out"));
    }
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

// stats() -> (flushes, last_flush_us). `last_flush_us` is the kick -> fully-out
// WALL span of the last completed frame, i.e. the real cost of moving 153,600 B.
// It does NOT shrink when the overlap lands -- the transfer still takes what it
// takes; what shrinks is how much of it the CPU waits for. That number is
// pump_stats()[5], and the console's own `flush=` measures the same thing from
// the Python side.
static mp_obj_t moy_lcd_stats(void) {
    mp_obj_t t[2] = {
        mp_obj_new_int_from_uint(s_flushes),
        mp_obj_new_int_from_uint(s_last_flush_us),
    };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_stats_obj, moy_lcd_stats);

// pump_stats() -> (pump_us, idle_us, idle_n, feed_us, bands, blocked_us,
// timeouts) for the last fully-shipped frame. tdeck_panel.bounce_stats() hands
// the first five straight to the PUMP diag line (#66 lever 4):
//   pump    CPU us inside pump() -- the band memcpys, wherever they ran
//   idle    us the SPI sat starved because a band was fed after the previous
//           one had already finished. THE pacing number: ~0 means the ceiling is
//           real transfer time and a faster feeder buys nothing
//   gaps    how many bands were fed that late
//   feed    kick -> last band queued
//   blocked us the CPU actually spent inside kick+drain -- what the overlap is
//           supposed to drive toward ~2 ms from ~17
static mp_obj_t moy_lcd_pump_stats(void) {
    mp_obj_t t[7] = {
        mp_obj_new_int_from_uint(s_pump_last_us),
        mp_obj_new_int_from_uint(s_idle_last_us),
        mp_obj_new_int_from_uint(s_idle_last_n),
        mp_obj_new_int(s_feed_last_us),
        MP_OBJ_NEW_SMALL_INT(moy_lcd_bands()),
        mp_obj_new_int_from_uint(s_block_last_us),
        mp_obj_new_int_from_uint(s_timeouts),
    };
    return mp_obj_new_tuple(7, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_pump_stats_obj, moy_lcd_pump_stats);

static const mp_rom_map_elem_t moy_lcd_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_ROM_QSTR(MP_QSTR_moy_lcd) },
    { MP_ROM_QSTR(MP_QSTR_init),       MP_ROM_PTR(&moy_lcd_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit),     MP_ROM_PTR(&moy_lcd_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_fb),         MP_ROM_PTR(&moy_lcd_fb_obj) },
    { MP_ROM_QSTR(MP_QSTR_nfbs),       MP_ROM_PTR(&moy_lcd_nfbs_obj) },
    { MP_ROM_QSTR(MP_QSTR_show),       MP_ROM_PTR(&moy_lcd_show_obj) },
    // The #66 overlap split: kick -> pump* -> drain. See OVERLAP at the top.
    { MP_ROM_QSTR(MP_QSTR_kick),       MP_ROM_PTR(&moy_lcd_kick_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump),       MP_ROM_PTR(&moy_lcd_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_drain),      MP_ROM_PTR(&moy_lcd_drain_obj) },
    { MP_ROM_QSTR(MP_QSTR_pending),    MP_ROM_PTR(&moy_lcd_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_backlight),  MP_ROM_PTR(&moy_lcd_backlight_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_madctl), MP_ROM_PTR(&moy_lcd_set_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_madctl),     MP_ROM_PTR(&moy_lcd_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_bars),       MP_ROM_PTR(&moy_lcd_bars_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats),      MP_ROM_PTR(&moy_lcd_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump_stats), MP_ROM_PTR(&moy_lcd_pump_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_WIDTH),      MP_ROM_INT(MOY_LCD_W) },
    { MP_ROM_QSTR(MP_QSTR_HEIGHT),     MP_ROM_INT(MOY_LCD_H) },
    { MP_ROM_QSTR(MP_QSTR_BAND_ROWS),  MP_ROM_INT(MOY_LCD_BAND_ROWS) },
    { MP_ROM_QSTR(MP_QSTR_BOUNCE_SLOTS), MP_ROM_INT(MOY_LCD_BOUNCE_SLOTS) },
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
