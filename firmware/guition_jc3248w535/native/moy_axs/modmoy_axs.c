// Moybyte Guition JC3248W535 port (#202): AXS15231B QSPI panel backend.
//
// This is the board's answer to the T-Deck's moy_lcd and the P4's moy_dsi: the
// third panel class -- a 320x480 portrait panel behind a QUAD-SPI bridge. It
// owns the SPI2 bus, the panel init, N PSRAM framebuffers (fb(i)) and a banded
// flush that DMAs only from internal SRAM, with moy_lcd's kick/pump/drain
// overlap split. Same verbs, same semantics, so `guition_panel.py` is the
// twin of `tdeck_panel.py` down to the pump timer.
//
// SHARE-OR-STAND-ALONE (the Phase C question, decided at bring-up as the
// checklist says): this module does NOT share moy_lcd's C body, because the
// two panels disagree about the one thing the band machinery touches per band
// -- what a "continuation" is. The ST7789 is a plain 4-wire panel: bands 2..N
// go out as command-less tx_color calls (lcd_cmd = -1), each its own CS cycle,
// and the GRAM write pointer just keeps walking. The AXS15231B is a QSPI
// bridge: every CS assertion must begin with a 4-byte 1-line opcode header,
// so a command-less transaction is garbage by construction, and the whole
// frame has to ship under ONE CS assertion (header + data chunks with
// CS_KEEP_ACTIVE -- byte-for-byte the stream the proven ESPHome driver on
// this exact glass produces). That difference reaches every transaction this
// file queues, which is why the band machinery here drives spi_master RAW
// instead of going through esp_lcd's panel-io (whose per-call CS cycling is
// the ST7789 shape). What DID transfer from moy_lcd is the design: bounce
// bands in internal DMA SRAM, an ISR completion counter, kick/pump/drain, and
// the one-DMA-chunk-per-band static assert.
//
// THE QSPI PROTOCOL (deduced by ESPHome/LovyanGFX from vendor code, verified
// on this glass by the owner's working ESPHome build):
//   * command:  CS low -> 4 bytes on ONE line: 0x02 0x00 <cmd> 0x00 -> params
//               on one line -> CS high.
//   * pixels:   CS low -> 4 bytes on ONE line: 0x32 0x00 0x2C 0x00 -> pixel
//               data on FOUR lines (SPI_TRANS_MODE_QIO), any number of chunks,
//               CS held low across all of them -> CS high ends the write.
//   Pixel bytes are RGB565 high byte first, so the framebuffer stores wire
//   order (BYTE_SWAP below; the shared palette on this board is PAL565_SW).
//
//   THE WINDOW MUST BE ARMED (hardware-learned 2026-08-18, first light's one
//   real bug): the AXS15231B DISCARDS memory writes until CASET/RASET have
//   been sent at least once -- the first image showed the panel's power-on
//   GRAM noise under a fully "successful" flush, and arming the window over
//   the live dev channel fixed it with no other change. Every proven driver
//   (ESPHome, esp_lcd's AXS component) arms the window before every write;
//   kick() arms it before every frame now, which is also moy_lcd's recovery
//   point after a timed-out flush.
//
// LANDSCAPE, ROTATED IN THE BAND COPY (owner call, 2026-08-18/19). The
//   console runs 480x320 landscape; the glass is portrait-native and the
//   AXS15231B's MADCTL MV (row/column exchange) is DEAD ON THIS GLASS --
//   tested live, twice: 0x60 (MX|MV, Arduino_GFX's landscape value) and 0x20
//   (MV alone) both scramble the write path to a sliver of pixels while
//   0x00 portrait writes stay clean, matching the LVGL-forum field reports
//   and contradicting the Arduino_GFX driver that writes the bit. So the
//   rotation happens where the bytes already move: the PSRAM->SRAM band copy
//   becomes a rotate-gather. The loop order makes it nearly free -- outer
//   loop over LOGICAL rows (sequential PSRAM reads, every cache line fully
//   used: the same read traffic as the straight memcpy), inner writes
//   scattered into the bounce slot, which is INTERNAL SRAM and uncached, so
//   the scatter costs nothing. Measured cost lives in guition_panel's
//   docstring once benched; the design estimate is ~2ms/frame of CPU.
//   `set_rot(0|1)` flips which 90-degree direction ships (a live-glass
//   calibration knob, like set_madctl); the baked default is the one the
//   owner confirmed upright.
//
// INIT -- the vendor block is ESPHome's AXS15231 model (3 commands), plus the
//   standard DCS tail ESPHome generates around it (PIXFMT 0x55, MADCTL 0x00,
//   INVOFF, SLPOUT, DISPON). That exact sequence runs on this exact board
//   under ESPHome, which is the only init provenance available -- there is no
//   public AXS15231B datasheet worth the name. The panel is portrait-native
//   320x480 and the AXS15231B cannot swap axes (ESPHome models it with
//   swap_xy undefined and does rotation in software), so the console runs
//   PORTRAIT and MADCTL stays 0.
//
// FLUSH -- the panel DMA only ever reads INTERNAL SRAM, the #66 rule carried
//   over unmeasured on purpose: ESPHome does DMA a PSRAM buffer on this board,
//   but ESPHome also isn't running a MicroPython VM whose gc heap lives in
//   that PSRAM behind one small dcache. Bands of BAND_ROWS rows are memcpy'd
//   PSRAM -> one of two internal bounce slots and queued; the header ships at
//   kick; CS stays low until the last band clears CS_KEEP_ACTIVE.
//
//   kick(n)   acquire the bus (CS_KEEP_ACTIVE demands it), send the pixel
//             header, queue the first BOUNCE_SLOTS bands, RETURN.
//   pump()    copy+queue every band whose bounce slot has freed. Non-blocking
//             by construction: spi_device_queue_trans is the async API and
//             needs no bus wait (we hold the bus for the whole frame).
//   drain()   feed the rest, wait out the tail, retrieve every queued result,
//             release the bus.
//   show(n)   kick + drain, blocking -- the bring-up verb.
//
//   A gap between bands leaves CS low with the clock idle; the bridge latches
//   per byte and does not time out (ESPHome idles mid-frame the same way when
//   its chunk loop is preempted). If the glass ever disagrees, ASYNC_FLUSH in
//   guition_panel.py serializes the flush in one reflash.
//
// BACKLIGHT -- GPIO1, active high, plain on/off here (#45: parked LOW at init
//   so power-on GRAM noise is never lit). The board's backlight is PWM-capable
//   (ESPHome drives it ledc @5kHz); a duty argument is an owner call for
//   later, and it would land here.

#include <string.h>

#include "py/runtime.h"
#include "py/objarray.h"
#include "py/objtuple.h"
#include "py/mphal.h"
#include "py/mpthread.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

// ---- board facts (Guition JC3248W535; pins from the owner's working ESPHome
// definition -- treat as verified)
// LOGICAL geometry (what the console draws; WIDTH/HEIGHT exports): landscape.
#define MOY_AXS_W            480
#define MOY_AXS_H            320
// PHYSICAL geometry (what the panel scans; the window + the bands): portrait.
#define MOY_AXS_PANEL_W      320
#define MOY_AXS_PANEL_H      480
#define MOY_AXS_FB_BYTES     (MOY_AXS_W * MOY_AXS_H * 2)
#define MOY_AXS_PANEL_ROW_BYTES (MOY_AXS_PANEL_W * 2)

#define MOY_AXS_SPI_HOST     SPI2_HOST
#define MOY_AXS_PIN_SCK      47
#define MOY_AXS_PIN_D0       21
#define MOY_AXS_PIN_D1       48
#define MOY_AXS_PIN_D2       40
#define MOY_AXS_PIN_D3       39
#define MOY_AXS_PIN_CS       45
#define MOY_AXS_PIN_BL       1

// 40 MHz -- the ESPHome data_rate proven on this glass. The QSPI bridge's
// ceiling is unpublished; do not raise without an on-glass A/B.
#define MOY_AXS_PCLK_HZ      (40 * 1000 * 1000)

// Bands of 48 PHYSICAL rows = 30720 B (the T-Deck's numbers transfer exactly:
// same row-bytes, same 32768 B S3 DMA chunk cap). 480/48 = 10 bands a frame;
// a band is ~1.5ms of transfer at 40MHz x 4 lines, so two slots buffer ~3ms.
#define MOY_AXS_BAND_ROWS    48
#define MOY_AXS_BAND_BYTES   (MOY_AXS_BAND_ROWS * MOY_AXS_PANEL_ROW_BYTES)
#define MOY_AXS_BOUNCE_SLOTS 2
#define MOY_AXS_BANDS        ((MOY_AXS_PANEL_H + MOY_AXS_BAND_ROWS - 1) / MOY_AXS_BAND_ROWS)
#define MOY_AXS_MAX_FBS      3
#define MOY_AXS_FLUSH_TIMEOUT_US 500000

// A band must be ONE DMA transaction (S3: 1<<18 bits = 32768 B) -- moy_lcd's
// assert, same reason: an oversized band splits, the split's second chunk
// arrives with no accounting, and the pump silently blocks.
_Static_assert(MOY_AXS_BAND_BYTES <= 32768,
               "a band must fit one SPI DMA transaction (S3: 32768 B)");

// The QSPI opcodes (see THE QSPI PROTOCOL above).
#define AXS_OP_CMD1          0x02   // header byte 0: params follow on 1 line
#define AXS_OP_PIX4          0x32   // header byte 0: data follows on 4 lines
#define CMD_INVOFF           0x20
#define CMD_SLPOUT           0x11
#define CMD_DISPON           0x29
#define CMD_MADCTL           0x36
#define CMD_COLMOD           0x3A
#define CMD_RAMWR            0x2C

static spi_device_handle_t s_dev;
static bool s_bus_up;
static bool s_dev_up;
static uint8_t *s_fbs[MOY_AXS_MAX_FBS];
static int s_nfbs;
static uint8_t *s_bounce[MOY_AXS_BOUNCE_SLOTS];
static volatile uint32_t s_done;       // band DMAs completed (ISR post_cb)
static volatile uint32_t s_done_us;
static uint32_t s_flushes;
static uint32_t s_last_flush_us;
static uint8_t s_madctl;

// The in-flight flush (moy_lcd's bookkeeping, verbatim in spirit):
// s_bnc_total == 0 means idle. s_bnc_next = bands FED, s_target = bands
// QUEUED, s_done = bands the panel ACCEPTED. Band k reuses slot k % SLOTS
// once band k-SLOTS completed.
static int s_bnc_total;
static int s_bnc_next;
static const uint8_t *s_bnc_src;
static uint32_t s_target;
static bool s_in_pump;
static esp_err_t s_tx_err;
static int64_t s_flush_t0;
static uint32_t s_timeouts;
static uint32_t s_tx_errs;
static bool s_bus_held;                // spi_device_acquire_bus is ours right now

// One transaction struct per band, stable for the whole frame (queued
// transactions must outlive their retrieval), plus the retrieval counter --
// spi_master's queue slots free only at spi_device_get_trans_result.
static spi_transaction_t s_band_trans[MOY_AXS_BANDS];
static int s_retrieved;                // results collected so far this frame
// The pixel-write header, DMA-visible (polling tx still wants a real buffer).
static DMA_ATTR uint8_t s_pix_hdr[4] = { AXS_OP_PIX4, 0x00, CMD_RAMWR, 0x00 };

// Feed pacing for the PUMP diag line, latched at kick like moy_lcd's.
static uint32_t s_pump_us, s_idle_us, s_idle_n;
static int32_t s_feed_us = -1;
static uint32_t s_kick_us, s_block_us;
static uint32_t s_pump_last_us, s_idle_last_us, s_idle_last_n;
static int32_t s_feed_last_us = -1;
static uint32_t s_block_last_us;

static bool moy_axs_drain_locked(void);

// Which 90-degree rotation ships (see LANDSCAPE above). Two mappings from the
// LOGICAL landscape buffer L[ly][lx] (320 rows x 480 cols) to the PHYSICAL
// panel P[py][px] (480 rows x 320 cols):
//   rot 0:  P[py][px] = L[px][PANEL_H-1-py]   (logical top edge -> one side)
//   rot 1:  P[py][px] = L[PANEL_W-1-px][py]   (the other way up)
// Both read the logical buffer SEQUENTIALLY along lx in the inner walk, so
// they cost the same; the default is the direction the owner confirmed
// upright on glass.
static int s_rot = 0;

// --- the #190-cousin GAME FOLD (armed by DeviceCanvas.blit_game through
// guition_panel; see that module's prose). While armed, the flush SYNTHESIZES
// every band -- black bezels + the game pixels read STRAIGHT from the
// (scratch-snapshotted) game buffer -- and the root framebuffer is neither
// written by a composite nor read by the pump. Scale 1 only (the Python glue
// declines and composites itself otherwise). One-shot: kick() consumes the
// arm; disarm performs the skipped composite into a caller-named fb.
static bool s_fold_armed;              // this frame's composite is the flush's job
static bool s_fold_inflight;           // the in-flight flush reads s_fold_src
static const uint8_t *s_fold_src;      // game pixels, RGB565 wire order
static int s_fold_vw, s_fold_vh;       // game geometry (logical px)
static int s_fold_ox, s_fold_oy;       // viewport origin in the logical frame
static uint32_t s_fold_frames;         // flushes folded since boot (diag)

// Fill a bounce slot with physical band `k` under the GAME FOLD: bezels are
// black, the game region reads the game buffer directly -- same loop shape as
// the plain rotate below, with bounds. The root fb is not touched.
static void moy_axs_fold_band(uint8_t *slot, int k, int rows) {
    const uint16_t *g = (const uint16_t *)s_fold_src;
    uint16_t *dst = (uint16_t *)slot;
    int py0 = k * MOY_AXS_BAND_ROWS;
    for (int px = 0; px < MOY_AXS_PANEL_W; px++) {
        // rot 0: ly = px; rot 1: ly = PANEL_W-1-px.
        int ly = (s_rot == 0) ? px : (MOY_AXS_PANEL_W - 1 - px);
        uint16_t *d = dst + px;
        int gy = ly - s_fold_oy;
        if (gy < 0 || gy >= s_fold_vh) {
            for (int r = 0; r < rows; r++) {
                *d = 0;
                d += MOY_AXS_PANEL_W;
            }
            continue;
        }
        const uint16_t *grow = g + (size_t)gy * s_fold_vw;
        for (int r = 0; r < rows; r++) {
            int lx = (s_rot == 0) ? (MOY_AXS_PANEL_H - 1 - (py0 + r))
                                  : (py0 + r);
            int gx = lx - s_fold_ox;
            *d = (gx >= 0 && gx < s_fold_vw) ? grow[gx] : 0;
            d += MOY_AXS_PANEL_W;
        }
    }
}

// Fill a bounce slot with physical band `k`, rotating from the logical
// landscape framebuffer. The loop order is the whole trick: outer over px
// (= a LOGICAL ROW of the source), inner over the band's physical rows
// (= consecutive lx, sequential PSRAM reads -- every 32B cache line fully
// used, same total read traffic as a straight memcpy); the writes scatter
// with a 640B stride, into internal SRAM, where a scatter is free.
static void moy_axs_rotate_band(uint8_t *slot, int k, int rows) {
    const uint16_t *src = (const uint16_t *)s_bnc_src;
    uint16_t *dst = (uint16_t *)slot;
    int py0 = k * MOY_AXS_BAND_ROWS;
    if (s_rot == 0) {
        // lx = PANEL_H-1-py, descending as py ascends: read backwards.
        for (int px = 0; px < MOY_AXS_PANEL_W; px++) {
            const uint16_t *srow = src + (size_t)px * MOY_AXS_W
                                   + (MOY_AXS_PANEL_H - 1 - py0);
            uint16_t *d = dst + px;
            for (int r = 0; r < rows; r++) {
                *d = srow[-r];
                d += MOY_AXS_PANEL_W;
            }
        }
    } else {
        // ly = PANEL_W-1-px, lx = py: read forwards.
        for (int px = 0; px < MOY_AXS_PANEL_W; px++) {
            const uint16_t *srow = src
                + (size_t)(MOY_AXS_PANEL_W - 1 - px) * MOY_AXS_W + py0;
            uint16_t *d = dst + px;
            for (int r = 0; r < rows; r++) {
                *d = srow[r];
                d += MOY_AXS_PANEL_W;
            }
        }
    }
}

static void moy_axs_check(esp_err_t err, const char *what) {
    if (err != ESP_OK) {
        mp_raise_msg_varg(&mp_type_OSError, MP_ERROR_TEXT("moy_axs %s: err 0x%x"),
                          what, (unsigned)err);
    }
}

static void moy_axs_require(void) {
    if (!s_dev_up) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_axs not initialized"));
    }
}

// SPI post-transfer callback, ISR context: count BAND completions only. The
// command/param/header polling transmits also come through here, so bands mark
// themselves via trans->user.
static IRAM_ATTR void moy_axs_post_cb(spi_transaction_t *t) {
    if (t->user != NULL) {
        s_done++;
        s_done_us = (uint32_t)esp_timer_get_time();
    }
}

static void moy_axs_park_pin(int gpio, int level) {
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

static void moy_axs_free_all(void) {
    for (int i = 0; i < MOY_AXS_MAX_FBS; i++) {
        if (s_fbs[i]) { heap_caps_free(s_fbs[i]); s_fbs[i] = NULL; }
    }
    s_nfbs = 0;
    for (int i = 0; i < MOY_AXS_BOUNCE_SLOTS; i++) {
        if (s_bounce[i]) { heap_caps_free(s_bounce[i]); s_bounce[i] = NULL; }
    }
    s_bnc_total = 0;
    s_bnc_next = 0;
    s_bnc_src = NULL;
    s_done = 0;
    s_target = 0;
    s_retrieved = 0;
    s_tx_err = ESP_OK;
}

// One panel command over the 1-line opcode path, bus ALREADY ACQUIRED by the
// caller. `data` is COPIED to the stack first: the init table lives in flash
// rodata and the SPI DMA cannot read flash-mapped memory -- the stack
// (internal SRAM) is DMA-reachable.
static esp_err_t moy_axs_cmd_acquired(uint8_t cmd, const uint8_t *data, size_t len) {
    uint8_t hdr[4] = { AXS_OP_CMD1, 0x00, cmd, 0x00 };
    uint8_t pbuf[16];
    if (len > sizeof(pbuf)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (len) {
        memcpy(pbuf, data, len);
    }
    spi_transaction_t t = { 0 };
    t.length = 32;
    t.tx_buffer = hdr;
    if (len) {
        t.flags = SPI_TRANS_CS_KEEP_ACTIVE;
    }
    esp_err_t err = spi_device_polling_transmit(s_dev, &t);
    if (err == ESP_OK && len) {
        spi_transaction_t d = { 0 };
        d.length = len * 8;
        d.tx_buffer = pbuf;
        err = spi_device_polling_transmit(s_dev, &d);
    }
    return err;
}

// The same, acquiring the bus itself (init-time / REPL escape hatches).
static void moy_axs_cmd(uint8_t cmd, const uint8_t *data, size_t len) {
    moy_axs_check(spi_device_acquire_bus(s_dev, portMAX_DELAY), "acquire");
    esp_err_t err = moy_axs_cmd_acquired(cmd, data, len);
    spi_device_release_bus(s_dev);
    moy_axs_check(err, "cmd");
}

// Arm the full-screen write window. Bus must be acquired. See THE WINDOW MUST
// BE ARMED above -- without this the panel discards every pixel write.
static esp_err_t moy_axs_arm_window_acquired(void) {
    uint8_t p[4];
    p[0] = 0; p[1] = 0;
    p[2] = (MOY_AXS_PANEL_W - 1) >> 8; p[3] = (MOY_AXS_PANEL_W - 1) & 0xFF;
    esp_err_t err = moy_axs_cmd_acquired(0x2A, p, 4);   // CASET
    if (err != ESP_OK) {
        return err;
    }
    p[2] = (MOY_AXS_PANEL_H - 1) >> 8; p[3] = (MOY_AXS_PANEL_H - 1) & 0xFF;
    return moy_axs_cmd_acquired(0x2B, p, 4);            // RASET
}

typedef struct {
    uint8_t cmd;
    uint8_t len;
    uint16_t delay_ms;
    uint8_t data[8];
} moy_axs_cmd_t;

// ESPHome's AXS15231 model init, verbatim, plus the DCS tail its display.py
// appends (PIXFMT/MADCTL/INVOFF/SLPOUT/DISPON) -- the sequence proven on this
// exact glass by the owner's working ESPHome build. See INIT above.
static const moy_axs_cmd_t MOY_AXS_INIT[] = {
    { 0xBB, 8,   0, {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x5A, 0xA5} },
    { 0xC1, 1,   0, {0x33} },
    { 0xBB, 8,   0, {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00} },
    { CMD_COLMOD, 1, 0, {0x55} },      // RGB565
    { CMD_MADCTL, 1, 0, {0x00} },      // portrait-native, RGB order, no flips
    { CMD_INVOFF, 0, 0, {0} },
    { CMD_SLPOUT, 0, 130, {0} },
    { CMD_DISPON, 0, 20, {0} },
};

// init(nfbs=2, pclk_hz=40000000) -> None
static mp_obj_t moy_axs_init(size_t n_args, const mp_obj_t *pos, mp_map_t *kw) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_nfbs,    MP_ARG_INT, { .u_int = 2 } },
        { MP_QSTR_pclk_hz, MP_ARG_INT, { .u_int = MOY_AXS_PCLK_HZ } },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos, kw, MP_ARRAY_SIZE(allowed), allowed, args);

    if (s_dev_up) {
        // Soft reset: the Python side is gone but these statics are not, and
        // the DMA is long finished. Clear the band bookkeeping (moy_lcd's
        // lesson: a leftover s_bnc_total makes the next drain feed a dead
        // frame) and let a stuck bus hold go.
        if (s_bus_held) {
            spi_device_release_bus(s_dev);
            s_bus_held = false;
        }
        s_bnc_total = 0;
        s_bnc_next = 0;
        s_bnc_src = NULL;
        s_done = 0;
        s_target = 0;
        s_retrieved = 0;
        s_tx_err = ESP_OK;
        return mp_const_none;
    }
    int nfbs = args[0].u_int;
    if (nfbs < 1 || nfbs > MOY_AXS_MAX_FBS) {
        mp_raise_ValueError(MP_ERROR_TEXT("nfbs 1..3"));
    }

    // Backlight LOW before anything else (#45): power-on GRAM is noise.
    moy_axs_park_pin(MOY_AXS_PIN_BL, 0);

    for (int i = 0; i < nfbs; i++) {
        s_fbs[i] = heap_caps_malloc(MOY_AXS_FB_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_fbs[i] == NULL) {
            moy_axs_free_all();
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_axs: no PSRAM framebuffer"));
        }
        memset(s_fbs[i], 0, MOY_AXS_FB_BYTES);
    }
    s_nfbs = nfbs;
    for (int i = 0; i < MOY_AXS_BOUNCE_SLOTS; i++) {
        s_bounce[i] = heap_caps_malloc(MOY_AXS_BAND_BYTES, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (s_bounce[i] == NULL) {
            moy_axs_free_all();
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_axs: no internal DMA bounce"));
        }
    }

    spi_bus_config_t bus_cfg = {
        .sclk_io_num = MOY_AXS_PIN_SCK,
        .data0_io_num = MOY_AXS_PIN_D0,
        .data1_io_num = MOY_AXS_PIN_D1,
        .data2_io_num = MOY_AXS_PIN_D2,
        .data3_io_num = MOY_AXS_PIN_D3,
        .max_transfer_sz = MOY_AXS_BAND_BYTES + 64,
        .flags = SPICOMMON_BUSFLAG_MASTER | SPICOMMON_BUSFLAG_QUAD,
    };
    esp_err_t err = spi_bus_initialize(MOY_AXS_SPI_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        moy_axs_free_all();
        moy_axs_check(err, "spi_bus_initialize");
    }
    s_bus_up = true;

    spi_device_interface_config_t dev_cfg = {
        .clock_speed_hz = args[1].u_int,
        .mode = 0,
        .spics_io_num = MOY_AXS_PIN_CS,
        // Bands per frame + margin: queue slots free only at result retrieval,
        // and drain retrieves once at the end of the frame.
        .queue_size = MOY_AXS_BANDS + 2,
        .flags = SPI_DEVICE_HALFDUPLEX,
        .post_cb = moy_axs_post_cb,
    };
    err = spi_bus_add_device(MOY_AXS_SPI_HOST, &dev_cfg, &s_dev);
    if (err != ESP_OK) {
        spi_bus_free(MOY_AXS_SPI_HOST);
        s_bus_up = false;
        moy_axs_free_all();
        moy_axs_check(err, "spi_bus_add_device");
    }
    s_dev_up = true;

    // No reset GPIO on this board; the panel takes SLPOUT ~120ms after power.
    mp_hal_delay_ms(10);
    for (size_t i = 0; i < MP_ARRAY_SIZE(MOY_AXS_INIT); i++) {
        const moy_axs_cmd_t *c = &MOY_AXS_INIT[i];
        moy_axs_cmd(c->cmd, c->len ? c->data : NULL, c->len);
        if (c->delay_ms) {
            mp_hal_delay_ms(c->delay_ms);
        }
    }
    s_madctl = 0x00;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(moy_axs_init_obj, 0, moy_axs_init);

static mp_obj_t moy_axs_deinit(void) {
    if (s_dev_up && s_bnc_total != 0) { moy_axs_drain_locked(); }
    if (s_dev_up) { spi_bus_remove_device(s_dev); s_dev = NULL; s_dev_up = false; }
    if (s_bus_up) { spi_bus_free(MOY_AXS_SPI_HOST); s_bus_up = false; }
    moy_axs_free_all();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_deinit_obj, moy_axs_deinit);

static mp_obj_t moy_axs_fb(size_t n_args, const mp_obj_t *a) {
    if (s_nfbs == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_axs not initialized"));
    }
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    return mp_obj_new_memoryview('B' | MP_OBJ_ARRAY_TYPECODE_FLAG_RW,
                                 MOY_AXS_FB_BYTES, s_fbs[n]);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_fb_obj, 0, 1, moy_axs_fb);

static mp_obj_t moy_axs_nfbs(void) {
    return MP_OBJ_NEW_SMALL_INT(s_nfbs);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_nfbs_obj, moy_axs_nfbs);

static bool moy_axs_wait_done(uint32_t target, int64_t deadline_us) {
    while (s_done < target) {
        if (esp_timer_get_time() > deadline_us) {
            return false;
        }
        esp_rom_delay_us(20);
    }
    return true;
}

// Retrieve every completed queued result so the queue slots free. Non-blocking
// for results the ISR has already counted; anything not yet done is left.
static void moy_axs_retrieve(void) {
    spi_transaction_t *rt;
    while (s_retrieved < (int)s_done) {
        if (spi_device_get_trans_result(s_dev, &rt, 0) != ESP_OK) {
            break;
        }
        s_retrieved++;
    }
}

// Copy + queue every band whose bounce slot has freed. Cheap and non-blocking:
// one 30KB PSRAM->SRAM memcpy plus one spi_device_queue_trans per band -- we
// hold the bus for the whole frame, so there is no acquire wait anywhere in
// here. GIL deliberately HELD (moy_lcd's reasoning: the body is <1ms and runs
// inside native draw ops).
static void moy_axs_pump_locked(void) {
    if (s_in_pump || s_bnc_total == 0 || s_bnc_next >= s_bnc_total
            || s_tx_err != ESP_OK) {
        return;
    }
    s_in_pump = true;
    uint32_t p0 = (uint32_t)esp_timer_get_time();
    moy_axs_retrieve();
    int k = s_bnc_next;
    const int slots = MOY_AXS_BOUNCE_SLOTS;
    while (k < s_bnc_total && (int32_t)s_done >= k - (slots - 1)) {
        if (k > 0 && s_done >= s_target) {
            uint32_t gap = (uint32_t)esp_timer_get_time() - s_done_us;
            if (gap > 0 && gap < 1000000u) {
                s_idle_us += gap;
                s_idle_n++;
            }
        }
        int y = k * MOY_AXS_BAND_ROWS;
        int rows = (y + MOY_AXS_BAND_ROWS <= MOY_AXS_PANEL_H)
                   ? MOY_AXS_BAND_ROWS : (MOY_AXS_PANEL_H - y);
        size_t nbytes = (size_t)rows * MOY_AXS_PANEL_ROW_BYTES;
        uint8_t *slot = s_bounce[k % slots];
        if (s_fold_inflight) {
            moy_axs_fold_band(slot, k, rows);
        } else {
            moy_axs_rotate_band(slot, k, rows);
        }
        spi_transaction_t *t = &s_band_trans[k];
        memset(t, 0, sizeof(*t));
        t->length = nbytes * 8;
        t->tx_buffer = slot;
        t->user = (void *)1;            // post_cb counts only bands
        // Data on 4 lines; CS stays low until the LAST band ends the write.
        t->flags = SPI_TRANS_MODE_QIO;
        if (k != s_bnc_total - 1) {
            t->flags |= SPI_TRANS_CS_KEEP_ACTIVE;
        }
        esp_err_t err = spi_device_queue_trans(s_dev, t, 0);
        if (err != ESP_OK) {
            // Queue full would be a bookkeeping bug (queue_size covers a whole
            // frame); either way: latch, count, let kick report it.
            s_tx_err = err;
            s_tx_errs++;
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

// Begin shipping framebuffer n: acquire the bus, send the pixel header, feed
// the first bands, return.
static void moy_axs_kick_locked(int n) {
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
    s_tx_err = ESP_OK;

    uint32_t b0 = (uint32_t)esp_timer_get_time();
    // CS_KEEP_ACTIVE requires the caller to hold the bus for the whole span.
    if (!s_bus_held) {
        esp_err_t aerr = spi_device_acquire_bus(s_dev, portMAX_DELAY);
        if (aerr != ESP_OK) {
            s_tx_err = aerr;
            s_tx_errs++;
            return;
        }
        s_bus_held = true;
    }
    // Arm the window EVERY frame (the discard rule above; also the recovery
    // point after a timed-out flush, exactly like moy_lcd's arm_window).
    esp_err_t werr = moy_axs_arm_window_acquired();
    if (werr != ESP_OK) {
        s_tx_err = werr;
        s_tx_errs++;
        spi_device_release_bus(s_dev);
        s_bus_held = false;
        return;
    }
    // The pixel-write header: 1 line, CS held for the bands that follow.
    spi_transaction_t hdr = { 0 };
    hdr.length = 32;
    hdr.tx_buffer = s_pix_hdr;
    hdr.flags = SPI_TRANS_CS_KEEP_ACTIVE;
    esp_err_t err = spi_device_polling_transmit(s_dev, &hdr);
    if (err != ESP_OK) {
        s_tx_err = err;
        s_tx_errs++;
        spi_device_release_bus(s_dev);
        s_bus_held = false;
        return;
    }
    s_done = 0;
    s_target = 0;
    s_retrieved = 0;
    s_bnc_next = 0;
    s_bnc_src = s_fbs[n];
    s_bnc_total = MOY_AXS_BANDS;
    // Consume the one-shot fold arm: THIS flush synthesizes its bands.
    s_fold_inflight = s_fold_armed;
    s_fold_armed = false;
    if (s_fold_inflight) {
        s_fold_frames++;
    }
    s_flush_t0 = esp_timer_get_time();
    s_kick_us = (uint32_t)s_flush_t0;
    moy_axs_pump_locked();
    s_block_us += (uint32_t)esp_timer_get_time() - b0;
}

// Finish the in-flight flush; always leaves the bus released and the queue
// empty. Returns false on timeout.
static bool moy_axs_drain_locked(void) {
    if (s_bnc_total == 0) {
        return true;
    }
    uint32_t b0 = (uint32_t)esp_timer_get_time();
    int64_t deadline = esp_timer_get_time() + MOY_AXS_FLUSH_TIMEOUT_US;
    bool ok = true;
    while (s_bnc_next < s_bnc_total && s_tx_err == ESP_OK) {
        int before = s_bnc_next;
        moy_axs_pump_locked();
        if (s_bnc_next == before && s_tx_err == ESP_OK) {
            int need = s_bnc_next - MOY_AXS_BOUNCE_SLOTS + 1;
            if (need < 1) {
                need = 1;
            }
            MP_THREAD_GIL_EXIT();
            ok = moy_axs_wait_done((uint32_t)need, deadline);
            MP_THREAD_GIL_ENTER();
            if (!ok) {
                break;
            }
        }
    }
    if (ok) {
        MP_THREAD_GIL_EXIT();
        ok = moy_axs_wait_done(s_target, deadline);
        MP_THREAD_GIL_ENTER();
    }
    // Collect every queued result (frees the queue slots) and give the bus
    // back -- even after an error or timeout, whatever completed must be
    // retrieved or the queue jams for good.
    moy_axs_retrieve();
    bool clean = ok && (s_tx_err == ESP_OK);
    s_bnc_total = 0;
    s_bnc_next = 0;
    s_bnc_src = NULL;
    s_fold_inflight = false;
    if (s_bus_held) {
        spi_device_release_bus(s_dev);
        s_bus_held = false;
    }
    if (clean) {
        s_flushes++;
        // kick -> LAST COMPLETION, from the ISR's own stamp (moy_lcd's
        // smoke-sleeps lesson: `now` here is a frame late under the overlap).
        s_last_flush_us = s_done_us - (uint32_t)s_flush_t0;
    } else if (!ok) {
        s_timeouts++;
    }
    s_block_us += (uint32_t)esp_timer_get_time() - b0;
    return clean;
}

static int moy_axs_fb_index(size_t n_args, const mp_obj_t *a) {
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    return (int)n;
}

static mp_obj_t moy_axs_kick(size_t n_args, const mp_obj_t *a) {
    moy_axs_require();
    int n = moy_axs_fb_index(n_args, a);
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();
    }
    moy_axs_kick_locked(n);
    if (s_tx_err != ESP_OK) {
        esp_err_t e = s_tx_err;
        s_tx_err = ESP_OK;
        moy_axs_check(e, "queue_trans");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_kick_obj, 0, 1, moy_axs_kick);

static mp_obj_t moy_axs_pump(size_t n_args, const mp_obj_t *a) {
    (void)n_args; (void)a;
    if (s_bnc_total != 0 && s_bnc_next < s_bnc_total) {
        moy_axs_pump_locked();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_pump_obj, 0, 1, moy_axs_pump);

static mp_obj_t moy_axs_drain(void) {
    if (!s_dev_up) {
        return mp_const_true;
    }
    bool ok = moy_axs_drain_locked();
    return ok ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_drain_obj, moy_axs_drain);

static mp_obj_t moy_axs_pending(void) {
    return (s_bnc_total != 0) ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_pending_obj, moy_axs_pending);

static mp_obj_t moy_axs_show(size_t n_args, const mp_obj_t *a) {
    moy_axs_require();
    int n = moy_axs_fb_index(n_args, a);
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();
    }
    moy_axs_kick_locked(n);
    bool ok = moy_axs_drain_locked();
    if (s_tx_err != ESP_OK) {
        esp_err_t e = s_tx_err;
        s_tx_err = ESP_OK;
        moy_axs_check(e, "queue_trans");
    }
    if (!ok) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_axs: flush timed out"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_show_obj, 0, 1, moy_axs_show);

// backlight(on) -- GPIO1, active high, binary (see BACKLIGHT above).
static mp_obj_t moy_axs_backlight(mp_obj_t on_in) {
    gpio_set_level(MOY_AXS_PIN_BL, mp_obj_is_true(on_in) ? 1 : 0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_backlight_obj, moy_axs_backlight);

// set_madctl(v) -- the bring-up escape hatch. NOTE the AXS15231B is not
// expected to honor MV (ESPHome models it as unable to swap axes); this exists
// to probe MX/MY on real glass without a rebuild.
static mp_obj_t moy_axs_set_madctl(mp_obj_t v_in) {
    moy_axs_require();
    uint8_t v = (uint8_t)mp_obj_get_int(v_in);
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();     // never race a command into a live write
    }
    moy_axs_cmd(CMD_MADCTL, &v, 1);
    s_madctl = v;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_set_madctl_obj, moy_axs_set_madctl);

static mp_obj_t moy_axs_madctl(void) {
    return MP_OBJ_NEW_SMALL_INT(s_madctl);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_madctl_obj, moy_axs_madctl);

// arm_fold(buf, vw, vh, ox, oy) -- register THIS frame's game composite as
// the flush's job (scale-1 geometry; the Python glue declines others). The
// buffer must stay alive and unwritten until the next fold_fence()/drain --
// which is exactly the scratch snapshot DeviceCanvas.blit_game hands over.
static mp_obj_t moy_axs_arm_fold(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_axs_require();
    mp_buffer_info_t buf;
    mp_get_buffer_raise(a[0], &buf, MP_BUFFER_READ);
    int vw = mp_obj_get_int(a[1]);
    int vh = mp_obj_get_int(a[2]);
    int ox = mp_obj_get_int(a[3]);
    int oy = mp_obj_get_int(a[4]);
    if (vw <= 0 || vh <= 0 || ox < 0 || oy < 0
            || ox + vw > MOY_AXS_W || oy + vh > MOY_AXS_H
            || buf.len < (size_t)vw * vh * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("fold geometry"));
    }
    s_fold_src = (const uint8_t *)buf.buf;
    s_fold_vw = vw;
    s_fold_vh = vh;
    s_fold_ox = ox;
    s_fold_oy = oy;
    s_fold_armed = true;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_arm_fold_obj, 5, 5, moy_axs_arm_fold);

// fold_fence() -- block until no in-flight flush still reads the fold source
// (called by blit_game before it overwrites the scratch). A two-compare no-op
// on the ordinary cadence, a drain only when frames outrun the flush.
static mp_obj_t moy_axs_fold_fence(void) {
    if (s_fold_inflight && s_bnc_total != 0) {
        moy_axs_drain_locked();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_fold_fence_obj, moy_axs_fold_fence);

// disarm_fold(back_fb) -- an overlay is about to paint the root: perform the
// SKIPPED composite (black bezels + game rows) into framebuffer `back_fb` so
// the overlay lands on a current picture, and clear the arm. The frame then
// flushes through the ordinary rotate path.
static mp_obj_t moy_axs_disarm_fold(mp_obj_t back_in) {
    if (!s_fold_armed) {
        return mp_const_false;
    }
    s_fold_armed = false;
    int n = mp_obj_get_int(back_in);
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    uint16_t *fb = (uint16_t *)s_fbs[n];
    memset(fb, 0, MOY_AXS_FB_BYTES);            // the bezels (black)
    const uint8_t *g = s_fold_src;
    for (int gy = 0; gy < s_fold_vh; gy++) {
        memcpy(fb + (size_t)(s_fold_oy + gy) * MOY_AXS_W + s_fold_ox,
               g + (size_t)gy * s_fold_vw * 2, (size_t)s_fold_vw * 2);
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_disarm_fold_obj, moy_axs_disarm_fold);

// fold_stats() -> (frames_folded, armed, inflight) -- the on-glass proof.
static mp_obj_t moy_axs_fold_stats(void) {
    mp_obj_t t[3] = {
        mp_obj_new_int_from_uint(s_fold_frames),
        s_fold_armed ? mp_const_true : mp_const_false,
        s_fold_inflight ? mp_const_true : mp_const_false,
    };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_fold_stats_obj, moy_axs_fold_stats);

// fold_test() -- the eyes-free pixel proof: with a fold ARMED and the current
// back fb holding the REFERENCE composite (the ordinary bezels+game blit,
// performed by the caller in Python), synthesize every band BOTH ways --
// fold_band from the game buffer, rotate_band from the reference fb -- and
// count mismatching bytes. 0 = the fold is pixel-identical to the path it
// replaces, proven on-device with no glass needed. Consumes nothing.
static mp_obj_t moy_axs_fold_test(mp_obj_t back_in) {
    moy_axs_require();
    if (!s_fold_armed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no fold armed"));
    }
    int n = mp_obj_get_int(back_in);
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();     // the bounce slots are our scratch here
    }
    const uint8_t *keep_src = s_bnc_src;
    s_bnc_src = s_fbs[n];
    uint32_t bad = 0;
    bool keep_inflight = s_fold_inflight;
    s_fold_inflight = true;         // fold_band reads the armed geometry
    for (int k = 0; k < MOY_AXS_BANDS; k++) {
        int y = k * MOY_AXS_BAND_ROWS;
        int rows = (y + MOY_AXS_BAND_ROWS <= MOY_AXS_PANEL_H)
                   ? MOY_AXS_BAND_ROWS : (MOY_AXS_PANEL_H - y);
        moy_axs_fold_band(s_bounce[0], k, rows);
        moy_axs_rotate_band(s_bounce[1], k, rows);
        const uint8_t *p0 = s_bounce[0];
        const uint8_t *p1 = s_bounce[1];
        for (size_t i = 0; i < (size_t)rows * MOY_AXS_PANEL_ROW_BYTES; i++) {
            if (p0[i] != p1[i]) {
                bad++;
            }
        }
    }
    s_fold_inflight = keep_inflight;
    s_bnc_src = keep_src;
    return mp_obj_new_int_from_uint(bad);
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_fold_test_obj, moy_axs_fold_test);

// set_rot(0|1) -- which 90-degree direction the band rotation ships (the
// live-glass calibration knob; the default is the owner-confirmed one).
static mp_obj_t moy_axs_set_rot(mp_obj_t v_in) {
    mp_int_t v = mp_obj_get_int(v_in);
    if (v != 0 && v != 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("rot 0|1"));
    }
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();     // never flip mid-frame
    }
    s_rot = (int)v;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_set_rot_obj, moy_axs_set_rot);

static mp_obj_t moy_axs_rot(void) {
    return MP_OBJ_NEW_SMALL_INT(s_rot);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_rot_obj, moy_axs_rot);

// cmd(c, data=b"") -- raw command escape hatch for bring-up probing (e.g.
// INVON 0x21 if the colors come up inverted). Drains first.
static mp_obj_t moy_axs_cmd_py(size_t n_args, const mp_obj_t *a) {
    moy_axs_require();
    if (s_bnc_total != 0) {
        moy_axs_drain_locked();
    }
    uint8_t c = (uint8_t)mp_obj_get_int(a[0]);
    mp_buffer_info_t buf = { 0 };
    if (n_args > 1) {
        mp_get_buffer_raise(a[1], &buf, MP_BUFFER_READ);
    }
    moy_axs_cmd(c, buf.len ? (const uint8_t *)buf.buf : NULL, buf.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_cmd_obj, 1, 2, moy_axs_cmd_py);

// bars(fb=0) -- stage-1 test pattern in C: 8 vertical bars over the top 3/4,
// white/black checker below (where byte-order and stride mistakes show).
// Written high byte first, the wire order (BYTE_SWAP).
static mp_obj_t moy_axs_bars(size_t n_args, const mp_obj_t *a) {
    if (s_nfbs == 0) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_axs not initialized"));
    }
    mp_int_t n = (n_args > 0) ? mp_obj_get_int(a[0]) : 0;
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    static const uint16_t BARS[8] = {
        0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000,
    };
    uint8_t *fb = s_fbs[n];
    const int split = MOY_AXS_H * 3 / 4;
    for (int y = 0; y < MOY_AXS_H; y++) {
        uint8_t *row = fb + (size_t)y * MOY_AXS_W * 2;
        for (int x = 0; x < MOY_AXS_W; x++) {
            uint16_t c;
            if (y < split) {
                c = BARS[(x * 8) / MOY_AXS_W];
            } else {
                c = (((x >> 4) + (y >> 4)) & 1) ? 0xFFFF : 0x0000;
            }
            row[x * 2] = (uint8_t)(c >> 8);
            row[x * 2 + 1] = (uint8_t)(c & 0xFF);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_bars_obj, 0, 1, moy_axs_bars);

static mp_obj_t moy_axs_stats(void) {
    mp_obj_t t[2] = {
        mp_obj_new_int_from_uint(s_flushes),
        mp_obj_new_int_from_uint(s_last_flush_us),
    };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_stats_obj, moy_axs_stats);

static mp_obj_t moy_axs_pump_stats(void) {
    mp_obj_t t[8] = {
        mp_obj_new_int_from_uint(s_pump_last_us),
        mp_obj_new_int_from_uint(s_idle_last_us),
        mp_obj_new_int_from_uint(s_idle_last_n),
        mp_obj_new_int(s_feed_last_us),
        MP_OBJ_NEW_SMALL_INT(MOY_AXS_BANDS),
        mp_obj_new_int_from_uint(s_block_last_us),
        mp_obj_new_int_from_uint(s_timeouts),
        mp_obj_new_int_from_uint(s_tx_errs),
    };
    return mp_obj_new_tuple(8, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_pump_stats_obj, moy_axs_pump_stats);

static const mp_rom_map_elem_t moy_axs_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_ROM_QSTR(MP_QSTR_moy_axs) },
    { MP_ROM_QSTR(MP_QSTR_init),       MP_ROM_PTR(&moy_axs_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit),     MP_ROM_PTR(&moy_axs_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_fb),         MP_ROM_PTR(&moy_axs_fb_obj) },
    { MP_ROM_QSTR(MP_QSTR_nfbs),       MP_ROM_PTR(&moy_axs_nfbs_obj) },
    { MP_ROM_QSTR(MP_QSTR_show),       MP_ROM_PTR(&moy_axs_show_obj) },
    { MP_ROM_QSTR(MP_QSTR_kick),       MP_ROM_PTR(&moy_axs_kick_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump),       MP_ROM_PTR(&moy_axs_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_drain),      MP_ROM_PTR(&moy_axs_drain_obj) },
    { MP_ROM_QSTR(MP_QSTR_pending),    MP_ROM_PTR(&moy_axs_pending_obj) },
    { MP_ROM_QSTR(MP_QSTR_backlight),  MP_ROM_PTR(&moy_axs_backlight_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_madctl), MP_ROM_PTR(&moy_axs_set_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_madctl),     MP_ROM_PTR(&moy_axs_madctl_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_rot),    MP_ROM_PTR(&moy_axs_set_rot_obj) },
    { MP_ROM_QSTR(MP_QSTR_rot),        MP_ROM_PTR(&moy_axs_rot_obj) },
    { MP_ROM_QSTR(MP_QSTR_arm_fold),   MP_ROM_PTR(&moy_axs_arm_fold_obj) },
    { MP_ROM_QSTR(MP_QSTR_fold_fence), MP_ROM_PTR(&moy_axs_fold_fence_obj) },
    { MP_ROM_QSTR(MP_QSTR_disarm_fold), MP_ROM_PTR(&moy_axs_disarm_fold_obj) },
    { MP_ROM_QSTR(MP_QSTR_fold_stats), MP_ROM_PTR(&moy_axs_fold_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_fold_test),  MP_ROM_PTR(&moy_axs_fold_test_obj) },
    { MP_ROM_QSTR(MP_QSTR_cmd),        MP_ROM_PTR(&moy_axs_cmd_obj) },
    { MP_ROM_QSTR(MP_QSTR_bars),       MP_ROM_PTR(&moy_axs_bars_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats),      MP_ROM_PTR(&moy_axs_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_pump_stats), MP_ROM_PTR(&moy_axs_pump_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_WIDTH),      MP_ROM_INT(MOY_AXS_W) },
    { MP_ROM_QSTR(MP_QSTR_HEIGHT),     MP_ROM_INT(MOY_AXS_H) },
    { MP_ROM_QSTR(MP_QSTR_BAND_ROWS),  MP_ROM_INT(MOY_AXS_BAND_ROWS) },
    { MP_ROM_QSTR(MP_QSTR_BOUNCE_SLOTS), MP_ROM_INT(MOY_AXS_BOUNCE_SLOTS) },
    // The framebuffer stores RGB565 high byte first (wire order): the shared
    // palette on this board is PAL565_SW, same as the T-Deck.
    { MP_ROM_QSTR(MP_QSTR_BYTE_SWAP),  MP_ROM_TRUE },
};
static MP_DEFINE_CONST_DICT(moy_axs_module_globals, moy_axs_module_globals_table);

const mp_obj_module_t moy_axs_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_axs_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_axs, moy_axs_module);
