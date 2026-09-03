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
// checklist says, and REVISITED 2026-08-21 -- the answer split in two):
//
//   THE TRANSPORT STANDS ALONE, and that verdict is unchanged. The two panels
//   disagree about the one thing the band machinery touches per band -- what a
//   "continuation" is. The ST7789 is a plain 4-wire panel: bands 2..N go out as
//   command-less tx_color calls (lcd_cmd = -1), each its own CS cycle, and the
//   GRAM write pointer just keeps walking. The AXS15231B is a QSPI bridge:
//   every CS assertion must begin with a 4-byte 1-line opcode header, so a
//   command-less transaction is garbage by construction, and the whole frame
//   has to ship under ONE CS assertion (header + data chunks with
//   CS_KEEP_ACTIVE -- byte-for-byte the stream the proven ESPHome driver on
//   this exact glass produces). That difference reaches every transaction this
//   file queues, which is why the band machinery here drives spi_master RAW
//   instead of going through esp_lcd's panel-io (whose per-call CS cycling is
//   the ST7789 shape).
//
//   THE CONCURRENCY IS ONE BODY: native/moy_flush. What used to "transfer from
//   moy_lcd as a design" -- bounce bands in internal DMA SRAM, the ISR
//   completion counter, kick/pump/drain -- became literal copies the night the
//   T-Deck's flush moved onto THIS board's core-0 feeder (d9aa73e), so the
//   feeder, the two-slot pacing, the handoff protocol and its races, the
//   reset-order invariant and the PUMP meters live in moy_flush.h now and this
//   file supplies three hooks (moy_axs_frame_begin / _queue_band / _frame_end).
//   READ moy_flush.h before touching any of that; what is genuinely this
//   board's stays here -- the QSPI protocol below, the window arming, the
//   ROTATE band synthesis and the one-DMA-chunk-per-band static assert. The
//   GAME FOLD went the same way -- its latch, fence and BOTH gathers are
//   native/moy_flush/moy_fold.h, and what stays here is the game WINDOW this
//   panel's persistent GRAM allows.
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
//   kick() arms it before every frame now. Arm-every-frame is the DESIGN, and
//   deliberately not a recovery mechanism -- because on this path there is no
//   recovery to hang off it. moy_lcd can lean on esp_lcd recycling its own
//   in-flight transactions after a bad flush; here the retrieve loop only
//   reclaims results the done-ISR has COUNTED, so a band that was queued and
//   never completed (the timed-out flush) is never retrieved and its
//   spi_master queue slot is gone until reboot. Tracked in the moy_axs
//   hardening issue; a next kick that finds the queue full latches ESP_ERR
//   and reports it, which is a symptom, not a cure.
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
//   kick(n)   decide this frame's window, then hand the frame to the shared
//             engine's CORE-0 FEEDER, which owns the whole flush: bus acquire,
//             window, pixel header, bands, tail, release.
//   pump()    a kept no-op since the feeder -- the feed no longer needs the
//             VM core at all (tdeck_panel-shaped callers still probe it).
//   drain()   wait the feeder's frame out (GIL released); the fence verb.
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

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"

// The SHARED banded-flush engine (native/moy_flush, staged per board.toml).
// It brings the FreeRTOS headers, the traceISR_EXIT_TO_SCHEDULER guard the
// yield needs, and the state this file's verbs report on.
#include "moy_flush.h"
#include "moy_fold.h"     // the GAME FOLD: the latch, the fence, both gathers

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

// Bands of 32 PHYSICAL rows = 20480 B. 480/32 = 15 bands a frame; a band is
// ~1.0ms of transfer at 40MHz x 4 lines, so two slots buffer ~2ms.
//
// WAS 48 (30720 B), which cost 61440 B of INTERNAL SRAM for the pair -- the
// single largest thing this board owns, on a board that measured 244 BYTES
// free with a cart + BLE + WiFi all up (2026-08-20). 32 rows hands 20480 B
// back. The T-Deck's moy_lcd defends 48 on a starvation measurement taken
// against its 2ms machine.Timer pump; that argument does NOT transfer here,
// because this board feeds bands from a core-0 FreeRTOS task (#202), so there
// is no timer to stay ahead of -- only the feeder, which two 1.0ms slots keep
// fed as well as two 1.5ms ones did. Measured on glass: 42.8 fps before,
// see below for after.
#define MOY_AXS_BAND_ROWS    32
#define MOY_AXS_BAND_BYTES   (MOY_AXS_BAND_ROWS * MOY_AXS_PANEL_ROW_BYTES)
#define MOY_AXS_BOUNCE_SLOTS 2
#define MOY_AXS_BANDS        ((MOY_AXS_PANEL_H + MOY_AXS_BAND_ROWS - 1) / MOY_AXS_BAND_ROWS)
#define MOY_AXS_MAX_FBS      3

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
static uint8_t s_madctl;
static bool s_bus_held;                // spi_device_acquire_bus is ours right now

// The in-flight flush -- the bookkeeping, the two internal-SRAM bounce slots,
// the CORE-0 FEEDER (#202's recorded strategic lever, shipped here 2026-08-19)
// and the handoff protocol are all `moy_flush`'s, one body with the T-Deck's
// moy_lcd since 2026-08-21. READ moy_flush.h. What this file supplies is the
// three transport hooks further down and the ops struct that names them; the
// board state they need is below.
//
// The feeder is why this board's flush stopped costing the VM core: before it,
// the band feed -- the rotate/fold synthesis plus the queueing -- ran ON the
// VM core, billed to every frame (~6.6ms of pump CPU at the game window's
// size, Star Catcher on this glass), and STARVED whenever the VM sat inside a
// long native call (a moycore tick pumps nothing), which held the measured
// flush wall at ~12.4ms against a ~7.7ms game-window transfer.

// One transaction struct per band, stable for the whole frame (queued
// transactions must outlive their retrieval), plus the retrieval counter --
// spi_master's queue slots free only at spi_device_get_trans_result.
static spi_transaction_t s_band_trans[MOY_AXS_BANDS];
static int s_retrieved;                // results collected so far this frame
// The pixel-write header, DMA-visible (polling tx still wants a real buffer).
static DMA_ATTR uint8_t s_pix_hdr[4] = { AXS_OP_PIX4, 0x00, CMD_RAMWR, 0x00 };

// THE BOARD'S HALF of the engine (moy_flush.h): the QSPI transport, which is
// the one thing Phase C said the two S3 panels can never share. All three run
// on the FEEDER task and so must never raise -- they return esp_err_t and the
// engine latches it for the next kick/show to report.
static esp_err_t moy_axs_frame_begin(void);
static esp_err_t moy_axs_queue_band(uint8_t *slot, const uint8_t *src, int k,
                                    int y, int rows, bool last);
static void moy_axs_frame_end(bool ok);

static const moy_flush_ops_t MOY_AXS_FLUSH_OPS = {
    .frame_begin   = moy_axs_frame_begin,
    .queue_band    = moy_axs_queue_band,
    .frame_end     = moy_axs_frame_end,
    .task_name     = "moy_axs_feed",
    .band_rows     = MOY_AXS_BAND_ROWS,
    .band_bytes    = MOY_AXS_BAND_BYTES,
    .bounce_slots  = MOY_AXS_BOUNCE_SLOTS,
};

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
// (scratch-snapshotted) game buffer, at any integer scale -- and the root
// framebuffer is neither written by a composite nor read by the pump.
//
// The LATCH, the FENCE and the gather are `moy_fold` (moy_fold.h); what is this
// board's is the game WINDOW below, which only a panel with persistent GRAM can
// have. `_Static_assert` because the rotated gather
// builds one axis map per band on its stack.
_Static_assert(MOY_AXS_BAND_ROWS <= MOY_FOLD_MAX_BAND_ROWS,
               "moy_fold_band_rot's per-band axis map is MOY_FOLD_MAX_BAND_ROWS long");
static uint32_t s_fold_win_frames;     // folded flushes that shipped game-window-only

// THE GAME WINDOW (owner's insight, 2026-08-19: "it doesn't move double the
// pixels -- the banding around the game never changes"). The panel's GRAM
// persists, so the bezels only need shipping when they might have changed:
// the FIRST folded flush after anything else goes full-screen (bezels black
// + game), and every folded flush after it, same geometry, arms CASET/RASET
// to just the game's physical rectangle (8-aligned -- the AXS draw-rounding)
// and ships game bytes alone. For the stock viewport that is 240x320 =
// 153,600 B, a ~7.7ms transfer against the full frame's ~15.4 -- the flush
// ceiling for play frames roughly doubles, and the synthesis halves with it.
// Every flush (windowed or not) runs through one window state, full-panel by
// default:
static int s_win_x, s_win_y, s_win_w, s_win_h;   // physical rect this flush
static int s_win_bands;                          // ceil(s_win_h / BAND_ROWS)
// Whether the bezels currently on the glass belong to this fold geometry
// (rot + viewport + scale). Set when a FULL folded flush ships; cleared by any
// non-folded flush, a rot flip, or a geometry change.
static bool s_bezels_valid;
static int s_bez_rot, s_bez_ox, s_bez_oy, s_bez_vw, s_bez_vh, s_bez_scale;

// This panel's half of the fold: the rotation and the window rect. The gather
// itself is moy_fold_band_rot -- one body with the T-Deck's straight-through
// form, which is what makes both testable off a board.
static void moy_axs_fold_band(uint8_t *slot, int k, int rows) {
    const moy_fold_rot_t geom = {
        MOY_AXS_PANEL_W, MOY_AXS_PANEL_H, s_rot, s_win_x, s_win_y, s_win_w,
    };
    moy_fold_band_rot(slot, &geom, s_win_y + k * MOY_AXS_BAND_ROWS, rows);
}

// Fill a bounce slot with physical band `k`, rotating from the logical
// landscape framebuffer. The loop order is the whole trick: outer over px
// (= a LOGICAL ROW of the source), inner over the band's physical rows
// (= consecutive lx, sequential PSRAM reads -- every 32B cache line fully
// used, same total read traffic as a straight memcpy); the writes scatter
// with a 640B stride, into internal SRAM, where a scatter is free.
static void moy_axs_rotate_band(uint8_t *slot, const uint8_t *fb, int k,
                                int rows) {
    const uint16_t *src = (const uint16_t *)fb;
    uint16_t *dst = (uint16_t *)slot;
    int py0 = s_win_y + k * MOY_AXS_BAND_ROWS;
    if (s_rot == 0) {
        // lx = PANEL_H-1-py, descending as py ascends: read backwards.
        for (int px = s_win_x; px < s_win_x + s_win_w; px++) {
            const uint16_t *srow = src + (size_t)px * MOY_AXS_W
                                   + (MOY_AXS_PANEL_H - 1 - py0);
            uint16_t *d = dst + (px - s_win_x);
            for (int r = 0; r < rows; r++) {
                *d = srow[-r];
                d += s_win_w;
            }
        }
    } else {
        // ly = PANEL_W-1-px, lx = py: read forwards.
        for (int px = s_win_x; px < s_win_x + s_win_w; px++) {
            const uint16_t *srow = src
                + (size_t)(MOY_AXS_PANEL_W - 1 - px) * MOY_AXS_W + py0;
            uint16_t *d = dst + (px - s_win_x);
            for (int r = 0; r < rows; r++) {
                *d = srow[r];
                d += s_win_w;
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
        // The counting and the feeder wake are the engine's, static inline so
        // this callback keeps its IRAM placement.
        moy_flush_band_done_from_isr();
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
    s_retrieved = 0;
    moy_flush_reset();
    moy_fold_reset();          // the fold's src was one of the buffers just freed
    moy_flush.frame_clean = true;
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

// Arm the write window to a physical rect. Bus must be acquired. See THE
// WINDOW MUST BE ARMED above -- without this the panel discards every pixel
// write; and see THE GAME WINDOW for why the rect is not always full-screen.
static esp_err_t moy_axs_arm_window_acquired(int x, int y, int w, int h) {
    uint8_t p[4];
    int x1 = x + w - 1, y1 = y + h - 1;
    p[0] = x >> 8; p[1] = x & 0xFF;
    p[2] = x1 >> 8; p[3] = x1 & 0xFF;
    esp_err_t err = moy_axs_cmd_acquired(0x2A, p, 4);   // CASET
    if (err != ESP_OK) {
        return err;
    }
    p[0] = y >> 8; p[1] = y & 0xFF;
    p[2] = y1 >> 8; p[3] = y1 & 0xFF;
    return moy_axs_cmd_acquired(0x2B, p, 4);            // RASET
}

// The fold geometry's physical rect, 8-aligned outward (the AXS draw-rounding;
// the synthesis paints any alignment sliver black). Writes s_win_*.
static void moy_axs_set_game_window(void) {
    // The LOGICAL rect the fold paints -- the game rectangle at its scale, not
    // the game's own size. The two differ only above scale 1.
    const int lw = moy_fold.vw * moy_fold.scale;
    const int lh = moy_fold.vh * moy_fold.scale;
    const int lx = moy_fold.ox, ly = moy_fold.oy;
    int px0, pw, py0, ph;
    if (s_rot == 0) {
        px0 = ly;                                 pw = lh;
        py0 = MOY_AXS_PANEL_H - lx - lw;          ph = lw;
    } else {
        px0 = MOY_AXS_PANEL_W - ly - lh;          pw = lh;
        py0 = lx;                                 ph = lw;
    }
    int px1 = (px0 + pw + 7) & ~7;
    int py1 = (py0 + ph + 7) & ~7;
    px0 &= ~7;
    py0 &= ~7;
    if (px1 > MOY_AXS_PANEL_W) { px1 = MOY_AXS_PANEL_W; }
    if (py1 > MOY_AXS_PANEL_H) { py1 = MOY_AXS_PANEL_H; }
    s_win_x = px0; s_win_w = px1 - px0;
    s_win_y = py0; s_win_h = py1 - py0;
    s_win_bands = (s_win_h + MOY_AXS_BAND_ROWS - 1) / MOY_AXS_BAND_ROWS;
}

static void moy_axs_set_full_window(void) {
    s_win_x = 0; s_win_y = 0;
    s_win_w = MOY_AXS_PANEL_W; s_win_h = MOY_AXS_PANEL_H;
    s_win_bands = MOY_AXS_BANDS;
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
        // lesson: a leftover bnc_total makes the next drain feed a dead
        // frame) and let a stuck bus hold go.
        moy_flush_drain();          // wait any in-flight FEEDER frame out
        if (s_bus_held) {
            spi_device_release_bus(s_dev);
            s_bus_held = false;
        }
        moy_flush_reset();
        moy_fold_reset();
        s_retrieved = 0;
        s_bezels_valid = false;
        moy_axs_set_full_window();
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
    // THE SHARED ENGINE: the internal-SRAM bounce slots plus the core-0 feeder
    // and its semaphores, all idempotent across a soft reset. Started HERE,
    // before the bus, so a memory failure leaves nothing half-built; the task
    // simply waits on its semaphore until the first kick(), which cannot
    // happen until this function has returned. A dead feeder is fatal: there
    // is no feederless flush path to fall back to.
    if (!moy_flush_start(&MOY_AXS_FLUSH_OPS)) {
        moy_axs_free_all();
        mp_raise_msg(&mp_type_MemoryError,
                     MP_ERROR_TEXT("moy_axs: no internal DMA bounce / feeder"));
    }

    spi_bus_config_t bus_cfg = {
        .sclk_io_num = MOY_AXS_PIN_SCK,
        .data0_io_num = MOY_AXS_PIN_D0,
        .data1_io_num = MOY_AXS_PIN_D1,
        .data2_io_num = MOY_AXS_PIN_D2,
        .data3_io_num = MOY_AXS_PIN_D3,
        .max_transfer_sz = MOY_AXS_BAND_BYTES + 64,
        .flags = SPICOMMON_BUSFLAG_MASTER | SPICOMMON_BUSFLAG_QUAD,
        // The done-ISR lands on CORE 0 with the feeder (the VM core never
        // fields per-band interrupts); the AUTO default would pin it to the
        // init caller's core, which is the VM's.
        .isr_cpu_id = ESP_INTR_CPU_AFFINITY_0,
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
    moy_axs_set_full_window();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(moy_axs_init_obj, 0, moy_axs_init);

static mp_obj_t moy_axs_deinit(void) {
    // moy_flush_stop() waits the feeder out and frees the bounce slots --
    // before the SPI device goes away under them. When it CANNOT, it frees
    // nothing and says so, and this must not proceed either: the feeder is
    // still queueing bands on the device that is about to be removed.
    if (s_dev_up) { moy_flush_drain(); }
    if (!moy_flush_stop()) {
        mp_raise_msg(&mp_type_OSError,
                     MP_ERROR_TEXT("moy_axs: feeder still running"));
    }
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

// ---- the board's half of the shared engine (moy_flush.h) -------------------
// All three hooks run on the FEEDER task, so none may raise: errors return and
// the engine latches them into moy_flush.tx_err for the next MP-side kick() or
// show() to report. This is the QSPI TRANSPORT -- the one part Phase C said
// the two S3 panels can never share.

// Retrieve every completed queued result so the queue slots free. Non-blocking
// for results the ISR has already counted; anything not yet done is left.
static void moy_axs_retrieve(void) {
    spi_transaction_t *rt;
    while (s_retrieved < (int)moy_flush.done) {
        if (spi_device_get_trans_result(s_dev, &rt, 0) != ESP_OK) {
            break;
        }
        s_retrieved++;
    }
}

// frame_begin: acquire the bus (CS_KEEP_ACTIVE demands the holder), arm the
// window EVERY frame (the discard rule -- THE WINDOW MUST BE ARMED above),
// then ship the 1-line pixel-write header with CS held for the bands that
// follow.
//
// The window rect is whatever the MP-side decision left in s_win_* (THE GAME
// WINDOW). The retrieval counter restarts here, in lockstep with the band
// counters the engine restarts the instant this returns OK -- on an ERROR
// path it is deliberately left alone, because nothing new was queued and the
// previous frame's frame_end already reconciled it.
static esp_err_t moy_axs_frame_begin(void) {
    esp_err_t err = spi_device_acquire_bus(s_dev, portMAX_DELAY);
    if (err != ESP_OK) {
        return err;
    }
    s_bus_held = true;
    err = moy_axs_arm_window_acquired(s_win_x, s_win_y, s_win_w, s_win_h);
    if (err == ESP_OK) {
        spi_transaction_t hdr = { 0 };
        hdr.length = 32;
        hdr.tx_buffer = s_pix_hdr;
        hdr.flags = SPI_TRANS_CS_KEEP_ACTIVE;
        err = spi_device_polling_transmit(s_dev, &hdr);
    }
    if (err != ESP_OK) {
        // Whatever is on the glass is no longer a picture this fold laid.
        s_bezels_valid = false;
        return err;
    }
    s_retrieved = 0;
    return ESP_OK;
}

// queue_band: synthesize physical band k into the slot the engine has already
// verified is free -- the rotate-gather from the logical landscape buffer, or
// the fold's bezels+game synthesis -- and queue it on 4 lines with CS held
// until the LAST band ends the write.
//
// Cheap and non-blocking: we hold the bus for the whole frame, so there is no
// acquire wait anywhere in here.
static esp_err_t moy_axs_queue_band(uint8_t *slot, const uint8_t *src, int k,
                                    int y, int rows, bool last) {
    (void)y;
    moy_axs_retrieve();
    if (moy_fold.inflight) {
        moy_axs_fold_band(slot, k, rows);
    } else {
        moy_axs_rotate_band(slot, src, k, rows);
    }
    spi_transaction_t *t = &s_band_trans[k];
    memset(t, 0, sizeof(*t));
    t->length = (size_t)rows * (size_t)s_win_w * 2 * 8;
    t->tx_buffer = slot;
    t->user = (void *)1;            // post_cb counts only bands
    t->flags = SPI_TRANS_MODE_QIO;  // data on 4 lines
    if (!last) {
        t->flags |= SPI_TRANS_CS_KEEP_ACTIVE;
    }
    // A queue-full here is either a bookkeeping bug (queue_size covers a whole
    // frame) or the LEAK from an earlier timed-out flush: bands that never
    // completed are never retrieved, so their queue slots stay taken for the
    // rest of the boot (see the header note; the moy_axs hardening issue
    // tracks it). Either way, all the engine can do is latch, count, and let
    // kick report it.
    return spi_device_queue_trans(s_dev, t, 0);
}

// frame_end: collect every queued result (which frees the queue slots) -- even
// after an error or a timeout, whatever completed must be retrieved or the
// queue jams for good -- and hand the bus back.
static void moy_axs_frame_end(bool ok) {
    (void)ok;
    if (s_bus_held) {
        moy_axs_retrieve();
        spi_device_release_bus(s_dev);
        s_bus_held = false;
    }
    moy_fold_end();
}

// MP-side: decide THIS frame's window before handing it to the engine.
//
// Consume the one-shot fold arm FIRST -- the window decision needs it (THE
// GAME WINDOW above): a folded flush whose geometry matches the bezels already
// on the glass ships the game rect alone; the first folded flush (or any
// geometry/rot change) ships full-screen to lay the bezels; a non-folded flush
// is always full-screen and invalidates them. Runs with the feeder IDLE (every
// caller drains first), which is what keeps the fold/bezel history race-free.
static void moy_axs_decide_window(void) {
    bool windowed = false;
    if (moy_fold_consume()) {
        if (s_bezels_valid && s_bez_rot == s_rot
                && s_bez_ox == moy_fold.ox && s_bez_oy == moy_fold.oy
                && s_bez_vw == moy_fold.vw && s_bez_vh == moy_fold.vh
                && s_bez_scale == moy_fold.scale) {
            windowed = true;
        } else {
            s_bez_rot = s_rot;
            s_bez_ox = moy_fold.ox; s_bez_oy = moy_fold.oy;
            s_bez_vw = moy_fold.vw; s_bez_vh = moy_fold.vh;
            s_bez_scale = moy_fold.scale;
            s_bezels_valid = true;      // this full flush lays them
        }
    } else {
        s_bezels_valid = false;
    }
    if (windowed) {
        moy_axs_set_game_window();
        s_fold_win_frames++;
    } else {
        moy_axs_set_full_window();
    }
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
    moy_flush_drain();
    // The FEEDER runs the SPI, so an error surfaces one frame late: raise the
    // finished frame's before handing this one over.
    moy_axs_check(moy_flush_take_err(), "flush");
    moy_axs_decide_window();
    // s_win_h is what the engine slices into bands -- the same ceil() that
    // produced s_win_bands, which fold_test still walks.
    moy_flush_kick(s_fbs[n], s_win_h);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_kick_obj, 0, 1, moy_axs_kick);

static mp_obj_t moy_axs_pump(size_t n_args, const mp_obj_t *a) {
    // The CORE-0 FEEDER owns the feed; nothing on the VM core needs to feed a
    // flush anymore. The verb survives only for VERB-SET PARITY with moy_lcd
    // (guition_panel's docstring says the same), so a reader comparing the two
    // modules is not left wondering which half is missing -- it is NOT wiring
    // anything up: guition_panel deliberately never sets `pump_if_pending`, so
    // DeviceCanvas's probe finds nothing and no caller in the tree reaches
    // here. Droppable the day that parity stops being worth a stub.
    (void)n_args; (void)a;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_pump_obj, 0, 1, moy_axs_pump);

static mp_obj_t moy_axs_drain(void) {
    if (!s_dev_up) {
        return mp_const_true;
    }
    bool ok = moy_flush_drain();
    return ok ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_drain_obj, moy_axs_drain);

static mp_obj_t moy_axs_pending(void) {
    return moy_flush.frame_busy ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_pending_obj, moy_axs_pending);

static mp_obj_t moy_axs_show(size_t n_args, const mp_obj_t *a) {
    moy_axs_require();
    int n = moy_axs_fb_index(n_args, a);
    moy_flush_drain();
    (void)moy_flush_take_err();     // show reports its OWN frame's errors
    moy_axs_decide_window();
    moy_flush_kick(s_fbs[n], s_win_h);
    bool ok = moy_flush_drain();
    moy_axs_check(moy_flush_take_err(), "flush");
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
    moy_flush_drain();         // never race a command into a live write
    moy_axs_cmd(CMD_MADCTL, &v, 1);
    s_madctl = v;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_set_madctl_obj, moy_axs_set_madctl);

static mp_obj_t moy_axs_madctl(void) {
    return MP_OBJ_NEW_SMALL_INT(s_madctl);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_madctl_obj, moy_axs_madctl);

// arm_fold(buf, vw, vh, ox, oy, scale=1) -- register THIS frame's game
// composite as the flush's job. The buffer must stay alive and unwritten until
// the next fold_fence()/drain -- which is exactly the scratch snapshot
// DeviceCanvas.blit_game hands over. Geometry the synthesis cannot express is
// REFUSED here (moy_fold_arm), and guition_panel composites it instead.
static mp_obj_t moy_axs_arm_fold(size_t n_args, const mp_obj_t *a) {
    moy_axs_require();
    mp_buffer_info_t buf;
    mp_get_buffer_raise(a[0], &buf, MP_BUFFER_READ);
    if (!moy_fold_arm((const uint8_t *)buf.buf, buf.len,
                      mp_obj_get_int(a[1]), mp_obj_get_int(a[2]),
                      mp_obj_get_int(a[3]), mp_obj_get_int(a[4]),
                      (n_args > 5) ? mp_obj_get_int(a[5]) : 1,
                      MOY_AXS_W, MOY_AXS_H)) {
        mp_raise_ValueError(MP_ERROR_TEXT("fold geometry"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_axs_arm_fold_obj, 5, 6, moy_axs_arm_fold);

// fold_fence() -- block until no in-flight flush still reads the fold source
// (called by blit_game before it overwrites the scratch). A two-compare no-op
// on the ordinary cadence, a drain only when frames outrun the flush.
static mp_obj_t moy_axs_fold_fence(void) {
    moy_fold_fence();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_fold_fence_obj, moy_axs_fold_fence);

// disarm_fold(back_fb) -- an overlay is about to paint the root: perform the
// SKIPPED composite (black bezels + the game rect at scale) into framebuffer
// `back_fb` so the overlay lands on a current picture, and clear the arm. The
// frame then flushes through the ordinary rotate path.
static mp_obj_t moy_axs_disarm_fold(mp_obj_t back_in) {
    int n = mp_obj_get_int(back_in);
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    if (!moy_fold_disarm()) {
        return mp_const_false;
    }
    moy_fold_composite(s_fbs[n], MOY_AXS_W, MOY_AXS_H);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_axs_disarm_fold_obj, moy_axs_disarm_fold);

// fold_stats() -> (frames_folded, armed, inflight, windowed) -- the proof:
// `windowed` climbing 1:1 with `frames_folded` = play frames shipping the
// game rect alone (the first of a run stays full to lay the bezels). The
// fourth field is this panel's alone; moy_lcd ships three and says why.
static mp_obj_t moy_axs_fold_stats(void) {
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(moy_fold.frames),
        moy_fold.armed ? mp_const_true : mp_const_false,
        moy_fold.inflight ? mp_const_true : mp_const_false,
        mp_obj_new_int_from_uint(s_fold_win_frames),
    };
    return mp_obj_new_tuple(4, t);
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
    if (!moy_fold.armed) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("no fold armed"));
    }
    int n = mp_obj_get_int(back_in);
    if (n < 0 || n >= s_nfbs) {
        mp_raise_ValueError(MP_ERROR_TEXT("fb index"));
    }
    moy_flush_drain();         // the bounce slots are our scratch here
    const uint8_t *keep_src = moy_flush.bnc_src;
    moy_flush.bnc_src = s_fbs[n];
    uint32_t bad = 0;
    // Pass 1: FULL window -- fold synthesis vs the rotate of the reference
    // composite the caller painted into fb n. Pass 2: the GAME window --
    // both paths again, over the sub-rect a steady play frame ships.
    for (int pass = 0; pass < 2; pass++) {
        if (pass == 0) {
            moy_axs_set_full_window();
        } else {
            moy_axs_set_game_window();
        }
        for (int k = 0; k < s_win_bands; k++) {
            int y = k * MOY_AXS_BAND_ROWS;
            int rows = (y + MOY_AXS_BAND_ROWS <= s_win_h)
                       ? MOY_AXS_BAND_ROWS : (s_win_h - y);
            moy_axs_fold_band(moy_flush.bounce[0], k, rows);
            moy_axs_rotate_band(moy_flush.bounce[1], moy_flush.bnc_src, k, rows);
            const uint8_t *p0 = moy_flush.bounce[0];
            const uint8_t *p1 = moy_flush.bounce[1];
            for (size_t i = 0; i < (size_t)rows * (size_t)s_win_w * 2; i++) {
                if (p0[i] != p1[i]) {
                    bad++;
                }
            }
        }
    }
    moy_axs_set_full_window();
    moy_flush.bnc_src = keep_src;
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
    moy_flush_drain();         // never flip mid-frame
    s_rot = (int)v;
    s_bezels_valid = false;
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
    moy_flush_drain();
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

// stats() -> (flushes, last_flush_us) and pump_stats() -> (pump, idle, gaps,
// feed, bands, blocked, timeouts, errs, stopfails). Both tuples are the shared
// engine's (moy_flush.h documents every field, and both boards export them
// verbatim); the shared BandedCompositor.bounce_stats() hands all nine up
// -- on THIS board to the dev channel's `state` snapshot, not to a PUMP diag
// line, because board.toml denies device_diag. `bands` is the FULL-frame count
// on purpose -- a play frame ships fewer (THE GAME WINDOW), and fold_stats() is
// where that shows, reached from Python as GuitionCompositor.fold_count.
static mp_obj_t moy_axs_stats(void) {
    return moy_flush_stats_tuple();
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_axs_stats_obj, moy_axs_stats);

static mp_obj_t moy_axs_pump_stats(void) {
    return moy_flush_pump_stats_tuple(MOY_AXS_BANDS);
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
