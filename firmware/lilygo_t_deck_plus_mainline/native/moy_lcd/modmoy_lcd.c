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
//
//   THE ENGINE IS SHARED: native/moy_flush (staged via board.toml) owns the
//   frame state machine, the core-0 feeder, the bounce slots and their pacing,
//   and the meters -- one body with the Guition's moy_axs since 2026-08-21,
//   because when this board's flush moved onto that board's feeder design the
//   concurrency half became a verbatim copy. READ moy_flush.h FIRST: the
//   handoff protocol, the reset-order invariant and the feeder's placement all
//   live there. What stays in this file is the ST7789/esp_lcd TRANSPORT --
//   which is exactly what Phase C said could not share -- as the engine's three
//   hooks (moy_lcd_frame_begin / _queue_band / _frame_end) plus this board's
//   own SD guard.
//
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
//     kick(n)   prepare the frame's bookkeeping and hand it to the shared
//               engine's CORE-0 FEEDER, which owns the whole flush: window
//               arm, band copy+queue, tail wait. Returns in us.
//     pump()    a kept no-op since the feeder (2026-08-21) -- the band feed no
//               longer needs the VM core at all. The verb survives for
//               verb-set parity with moy_axs; nothing in the tree calls it
//               (tdeck_panel no longer sets pump_if_pending, so DeviceCanvas's
//               probe finds nothing and moy_gfx's set_pump is never armed).
//     drain()   wait the feeder's frame out (GIL released); the fence verb.
//               Called at the top of the next flush (where most of it has
//               already happened behind the render) and before any SD op --
//               the card shares this SPI host.
//
//   WHO FEEDS THE BANDS: THE CORE-0 FEEDER, which is moy_flush's (ported from
//   moy_axs 2026-08-21, where it shipped 2026-08-19; shared as one body the
//   day after). Until then the feed ran on the VM core -- a 2 ms machine.Timer
//   through mp_sched plus pokes from the big native draw verbs -- which billed
//   the band memcpys to every frame AND set a floor on band size: a band had
//   to transfer LONGER than the 2 ms timer period or the SPI starved between
//   fires (48 rows = 3.07 ms; 32-row bands measured 53.9 -> 51.8 fps on Brick
//   Siege under the timer, 2026-08-21). The feeder retires both; the VM core
//   neither copies bands nor fields per-band interrupts, and the band-size
//   floor is gone (there is no timer to outlast). A dead feeder cannot degrade
//   quietly: init() FAILS if moy_flush_start() does -- there is no feederless
//   flush path.
//
//   show(n) remains kick+drain in one blocking call, because the bring-up smokes
//   want one number and no ping-pong reasoning.
//
// THREADING -- what esp_lcd does and does not promise (learned porting the
//   feeder, 2026-08-21):
//   * esp_lcd's SPI panel-io is NOT thread-safe per io handle: tx_param and
//     tx_color share one transaction pool and a plain num_trans_inflight
//     counter with no lock, so every io call must come from ONE task at a
//     time. The rule here: while a frame is in flight the FEEDER owns the io
//     handle exclusively; every VM-side caller (set_madctl, deinit, init's
//     soft-reset, the SD sync fence) drains first. kick() itself never
//     touches the io.
//   * tx_param recycles ALL queued color transactions before its polling
//     command (spi_device_get_trans_result x num_trans_inflight, in the
//     driver). Two things rest on that: the engine restarts the band counters
//     AFTER frame_begin (moy_flush.h's RESET-ORDER INVARIANT), so a timed-out
//     frame's stale bands have all completed -- and their completion ISRs run
//     -- before moy_flush.done restarts at 0; and num_trans_inflight cannot
//     creep across frames, because every frame's arm recycles the previous
//     frame's bands. THIS BOARD IS WHY THAT INVARIANT IS WORDED THE WAY IT IS.
//   * the done-ISR may (and must) yield itself: esp_lcd's spi post_cb IGNORES
//     the on_color_trans_done bool return, so vTaskNotifyGiveFromISR +
//     portYIELD_FROM_ISR happen inside the callback or a woken feeder waits
//     out the rest of the FreeRTOS tick (10 ms at this build's HZ=100). That
//     is moy_flush_band_done_from_isr(), static inline for the placement.
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
#include "py/mphal.h"

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"   // pulls in esp_lcd_panel_st7789.h
#include "esp_rom_sys.h"
#include "esp_timer.h"

// The SHARED banded-flush engine (native/moy_flush, staged per board.toml).
// It brings the FreeRTOS headers, the traceISR_EXIT_TO_SCHEDULER guard the
// yield needs, and the state this file's verbs report on.
#include "moy_flush.h"

// ---- board facts (device/tdeck_*.py)
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

// Bands of 32 rows = 20480 B; two slots = 40960 B of internal DMA SRAM.
//
// WAS 48 (30720 B, 61440 B for the pair -- the single largest thing this
// board owned). 48 was a TIMER artifact: under the 2 ms machine.Timer pump a
// band had to transfer LONGER than the timer period or the SPI starved
// between fires (24-row bands measured that on the fork; 32-row bands
// measured 53.9 -> 51.8 fps on Brick Siege under the timer, 2026-08-21, with
// only ~50 us of margin at 2.05 ms/band). The CORE-0 FEEDER retired the
// timer -- it is woken by the done-ISR itself, so there is no period to
// outlast -- and the shrink measured FREE on glass the same night (feeder
// build, Brick Siege, WiFi up: 58.0 fps median at 48 rows, 58.6 at 32,
// against 53.9 under the timer), handing 20480 B of internal SRAM back
// (cart-running main-region free: 19,264 timer/48 -> 14,872 feeder/48 ->
// 35,380 feeder/32 -- the feeder task's 4 KB stack is what the middle
// number pays).
#define MOY_LCD_BAND_ROWS    32
#define MOY_LCD_BAND_BYTES   (MOY_LCD_BAND_ROWS * MOY_LCD_ROW_BYTES)
#define MOY_LCD_BOUNCE_SLOTS 2
#define MOY_LCD_MAX_FBS      3

// A BAND MUST BE ONE DMA CHUNK, and the margin is only 6.7%. esp_lcd caps a
// transfer at MIN(max_transfer_sz, SPI_LL_DMA_MAX_BIT_LEN/8), and the S3's
// SPI_LL_DMA_MAX_BIT_LEN is 1<<18 -- so 32768 B. At 52 rows or more each band
// splits in two, and three things break QUIETLY: esp_lcd sets en_trans_done_cb
// only on the LAST chunk (so moy_flush.done still counts bands, which hides
// it), the first chunk carries SPI_TRANS_CS_KEEP_ACTIVE, and
// num_trans_inflight doubles to 10 == trans_queue_depth, at which point tx_color takes its recycle branch
// and BLOCKS on spi_device_get_trans_result(portMAX_DELAY). The pump would stop
// being non-blocking with nothing to show for it but lost fps. The comment
// above invites tuning this number; the assert is what makes that safe.
_Static_assert(MOY_LCD_BAND_BYTES <= 32768,
               "a band must fit one SPI DMA transaction (S3: 32768 B) or the "
               "pump silently becomes blocking -- see the note above");

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
static bool s_bus_up;
static uint8_t s_madctl;

// --- the in-flight flush (see OVERLAP in the header) -------------------------
// The bookkeeping, the two internal-SRAM bounce slots, the CORE-0 FEEDER and
// the handoff protocol are all `moy_flush`'s -- one body with the Guition's
// moy_axs since 2026-08-21. READ moy_flush.h. What this file supplies is the
// three transport hooks at the bottom of this section and the ops struct that
// names them.
//
// SD SESSION GUARD (hardware-learned 2026-08-21, first flash of the feeder).
// The SD card shares SPI2 with the panel, and the pre-feeder world kept the
// two apart by CONSTRUCTION: SD polling transactions and band queue_trans all
// ran on the one VM thread, so the two drivers' host-level calls could never
// EXECUTE concurrently (their DMAs overlapped in time, which spi_master
// arbitrates fine). The feeder broke that: boot's per-cart splash repaint
// kicks a frame whose bands the feeder queues FROM CORE 0 while the VM sits
// inside an sdspi polling transaction on core 1 -- and that concurrency
// panics the board with a Cache/MMU entry fault within seconds of cart
// loading (both cores' dumps: core 0 in the band memcpy, core 1 in sdspi
// poll_busy). So while an SD session is open (sd_guard(True), set by the
// session brackets in moy_runtime), kick() DRAINS before returning -- the
// frame still ships through the feeder, but the VM waits it out, so panel
// and SD host calls are serialized in time exactly as the moy_sd header
// requires ("the caller never flushes the panel mid-transaction"). The cost
// is a synchronous ~17 ms per paint during SD sessions only (boot progress,
// commits) -- play frames never pay it.
//
// A DEPTH, not a flag: sessions nest (a bracketed op that itself brackets),
// and an inner sd_guard(False) that cleared the bracket would leave the OUTER
// session running unserialized against the feeder -- the panic above, with
// nothing to point at it.
static volatile int s_sd_guard;

// THE BOARD'S HALF of the engine (moy_flush.h): the ST7789/esp_lcd transport,
// which is the one thing Phase C said the two S3 panels can never share. All
// three run on the FEEDER task and so must never raise -- they return
// esp_err_t and the engine latches it for the next kick/show to report.
static esp_err_t moy_lcd_frame_begin(void);
static esp_err_t moy_lcd_queue_band(uint8_t *slot, const uint8_t *src, int k,
                                    int y, int rows, bool last);
static void moy_lcd_frame_end(bool ok);

static const moy_flush_ops_t MOY_LCD_FLUSH_OPS = {
    .frame_begin   = moy_lcd_frame_begin,
    .queue_band    = moy_lcd_queue_band,
    .frame_end     = moy_lcd_frame_end,
    .task_name     = "moy_lcd_feed",
    .band_rows     = MOY_LCD_BAND_ROWS,
    .band_bytes    = MOY_LCD_BAND_BYTES,
    .bounce_slots  = MOY_LCD_BOUNCE_SLOTS,
};

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
// the last chunk of a tx_color, and a 20 KB band is one chunk). The counting
// and the feeder wake are the engine's, static inline so this callback keeps
// its own placement; the `return false` is because esp_lcd's spi post_cb
// IGNORES it, which is why the yield happens inside (THREADING above).
static bool moy_lcd_trans_done(esp_lcd_panel_io_handle_t io,
                               esp_lcd_panel_io_event_data_t *edata, void *ctx) {
    (void)io; (void)edata; (void)ctx;
    moy_flush_band_done_from_isr();
    return false;
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
    // The band state points into buffers that no longer exist.
    moy_flush_reset();
    moy_flush.frame_clean = true;
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
        // Already up -- a second compositor, or a SOFT RESET, which wipes the
        // Python side but not these statics (nor the FEEDER task and its
        // bounce slots, which survive a soft reset by design). Wait any
        // in-flight FEEDER frame out, then clear the band bookkeeping: a
        // leftover bnc_total would make the next frame re-feed a dead frame's
        // bands into a window that no longer describes them.
        moy_flush_drain();
        moy_flush_reset();
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
    // THE SHARED ENGINE: the internal-SRAM bounce slots plus the core-0 feeder
    // and its semaphores, all idempotent across a soft reset. Started HERE,
    // before the bus, for the same reason the buffers were allocated here --
    // a memory failure leaves nothing half-built -- and the task simply waits
    // on its semaphore until the first kick(), which cannot happen until this
    // function has returned. A dead feeder is fatal: there is no feederless
    // flush path to fall back to.
    if (!moy_flush_start(&MOY_LCD_FLUSH_OPS)) {
        moy_lcd_free_all();
        mp_raise_msg(&mp_type_MemoryError,
                     MP_ERROR_TEXT("moy_lcd: no internal DMA bounce / feeder"));
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
        // The done-ISR lands on CORE 0 with the FEEDER (the VM core never
        // fields per-band interrupts); the AUTO default would pin it to the
        // init caller's core, which is the VM's. moy_sd shares this host, so
        // SD completion ISRs move to core 0 with it -- also a win.
        .isr_cpu_id = ESP_INTR_CPU_AFFINITY_0,
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
    // would still be reading. moy_flush_stop() waits the feeder out and frees
    // the slots -- before the panel io goes away under it. When it CANNOT, it
    // frees nothing and says so, and this must not proceed either: the feeder
    // is still queueing bands on the io that is about to be deleted.
    if (s_panel) { moy_flush_drain(); }
    if (!moy_flush_stop()) {
        mp_raise_msg(&mp_type_OSError,
                     MP_ERROR_TEXT("moy_lcd: feeder still running"));
    }
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

// ---- the board's half of the shared engine (moy_flush.h) -------------------
// All three hooks run on the FEEDER task, so none may raise: errors return and
// the engine latches them into moy_flush.tx_err for the next MP-side kick() or
// show() to report. This is the ST7789/esp_lcd TRANSPORT -- the one part Phase
// C said the two S3 panels can never share.

// frame_begin: arm the ST7789's write window for the whole frame.
//
// esp_lcd's tx_param also RECYCLES every stale in-flight color transaction
// before its polling command, which is the timed-out-flush recovery this
// board's THREADING note describes -- and the reason the engine restarts its
// band counters only AFTER this returns (moy_flush.h's RESET-ORDER INVARIANT).
static esp_err_t moy_lcd_frame_begin(void) {
    uint8_t p[4];
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_W - 1) >> 8; p[3] = (MOY_LCD_W - 1) & 0xFF;
    esp_err_t err = esp_lcd_panel_io_tx_param(s_io, CMD_CASET, p, 4);
    if (err != ESP_OK) {
        return err;
    }
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_H - 1) >> 8; p[3] = (MOY_LCD_H - 1) & 0xFF;
    return esp_lcd_panel_io_tx_param(s_io, CMD_RASET, p, 4);
}

// queue_band: one 20 KB PSRAM -> SRAM memcpy into the slot the engine has
// already verified is free, then one esp_lcd_panel_io_tx_color.
//
// Band 0 carries RAMWR; every continuation band sends NO command (lcd_cmd =
// -1), which is what keeps esp_lcd from blocking on a drained queue and what
// keeps the ST7789 streaming into the window armed at the frame's start. The
// continuation bands also skip spi_device_acquire_bus entirely thanks to the
// #66 no-acquire patch, so they queue and return instead of waiting out the
// band before them. (`last` is the Guition's CS chain; this panel cycles CS
// per transaction and has no use for it.)
//
// Cross-core PSRAM reads are coherent: the S3 has ONE dcache shared by both
// cores, so the framebuffer core 1 just drew reads back exactly on core 0
// (moy_axs proved this shape on glass first).
static esp_err_t moy_lcd_queue_band(uint8_t *slot, const uint8_t *src, int k,
                                    int y, int rows, bool last) {
    (void)last;
    size_t nbytes = (size_t)rows * MOY_LCD_ROW_BYTES;
    memcpy(slot, src + (size_t)y * MOY_LCD_ROW_BYTES, nbytes);
    return esp_lcd_panel_io_tx_color(s_io, (k == 0) ? CMD_RAMWR : -1,
                                     slot, nbytes);
}

// frame_end: nothing to release. esp_lcd arbitrates the bus per transaction
// (unlike moy_axs, which HOLDS it for the whole frame because its QSPI bridge
// needs one CS assertion), so the window arm takes nothing back.
static void moy_lcd_frame_end(bool ok) {
    (void)ok;
}

// The board's FULL-frame band count -- what pump_stats() reports. The engine
// derives the in-flight count from kick()'s row argument instead, because the
// Guition windows some frames and this one never does.
static int moy_lcd_bands(void) {
    return (MOY_LCD_H + MOY_LCD_BAND_ROWS - 1) / MOY_LCD_BAND_ROWS;
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
    moy_flush_drain();              // defensive: kick without a flush() before it
    // The FEEDER runs the SPI, so an error surfaces one frame late: raise the
    // finished frame's before handing this one over.
    moy_lcd_check(moy_flush_take_err(), "tx_color");
    moy_flush_kick(s_fbs[n], MOY_LCD_H);
    if (s_sd_guard) {
        moy_flush_drain();          // SD session live: no overlap (see the guard)
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_kick_obj, 0, 1, moy_lcd_kick);

// sd_guard(on): bracket an SD session (see the SD SESSION GUARD note above).
// Nesting-safe: the bracket lifts on the OUTERMOST close. Turning it on also
// drains, so a frame already in the feeder's hands cannot straddle the session
// start.
static mp_obj_t moy_lcd_sd_guard(mp_obj_t on_in) {
    if (mp_obj_is_true(on_in)) {
        s_sd_guard++;
        if (s_panel != NULL) {
            moy_flush_drain();
        }
    } else if (s_sd_guard > 0) {
        s_sd_guard--;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lcd_sd_guard_obj, moy_lcd_sd_guard);

// pump(_=None): a kept no-op since the CORE-0 FEEDER (2026-08-21) -- nothing
// on the VM core needs to feed a flush anymore. The verb survives for verb-set
// parity with moy_axs (whose pump kept the same shape for the same reason); it
// wires nothing: tdeck_panel no longer sets pump_if_pending, so DeviceCanvas's
// probe finds nothing and no caller in the tree reaches here.
static mp_obj_t moy_lcd_pump(size_t n_args, const mp_obj_t *a) {
    (void)n_args; (void)a;
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
    bool ok = moy_flush_drain();
    return ok ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_drain_obj, moy_lcd_drain);

// pending() -> True while a flush is in flight (fed or not). What an SD op or a
// teardown has to see as False.
static mp_obj_t moy_lcd_pending(void) {
    return moy_flush.frame_busy ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_pending_obj, moy_lcd_pending);

// show(n=0): push framebuffer n and BLOCK until it is fully out. kick + drain in
// one call -- what the bring-up smokes want, and the always-correct fallback.
static mp_obj_t moy_lcd_show(size_t n_args, const mp_obj_t *a) {
    moy_lcd_require();
    int n = moy_lcd_fb_index(n_args, a);
    moy_flush_drain();
    (void)moy_flush_take_err();     // show reports its OWN frame's errors
    moy_flush_kick(s_fbs[n], MOY_LCD_H);
    bool ok = moy_flush_drain();
    moy_lcd_check(moy_flush_take_err(), "tx_color");
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
    // THREADING: the io handle is not thread-safe, so never race a command
    // into a frame the feeder is still shipping. (This drain was missing until
    // 2026-08-21 -- the rule was written down two paragraphs from a verb that
    // broke it, and moy_axs's twin verb had it from the start.)
    moy_flush_drain();
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

// stats() -> (flushes, last_flush_us) and pump_stats() -> (pump, idle, gaps,
// feed, bands, blocked, timeouts, errs, stopfails). Both tuples are the shared
// engine's (moy_flush.h documents every field, and both boards export them
// verbatim); the shared BandedCompositor.bounce_stats() hands all nine of
// pump_stats straight to the PUMP diag line (#66 lever 4). `bands` is the FULL-frame
// count, which on this board is every frame -- the Guition is the one that
// windows.
//
// `last_flush_us` is the kick -> fully-out WALL span of the last completed
// frame, i.e. the real cost of moving 153,600 B. It does NOT shrink when the
// overlap lands -- the transfer still takes what it takes; what shrinks is how
// much of it the CPU waits for, which is pump_stats()[5], and the console's
// own `flush=` measures the same thing from the Python side.
static mp_obj_t moy_lcd_stats(void) {
    return moy_flush_stats_tuple();
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_stats_obj, moy_lcd_stats);

static mp_obj_t moy_lcd_pump_stats(void) {
    return moy_flush_pump_stats_tuple(moy_lcd_bands());
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
    { MP_ROM_QSTR(MP_QSTR_sd_guard),   MP_ROM_PTR(&moy_lcd_sd_guard_obj) },
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
