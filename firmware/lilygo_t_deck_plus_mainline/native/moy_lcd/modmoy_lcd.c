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
//     kick(n)   prepare the frame's bookkeeping and hand it to the CORE-0
//               FEEDER task (the statics block below), which owns the whole
//               flush: window arm, band copy+queue, tail wait. Returns in us.
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
//   WHO FEEDS THE BANDS: THE CORE-0 FEEDER (ported from moy_axs, where it
//   shipped 2026-08-19). Until 2026-08-21 the feed ran on the VM core -- a
//   2 ms machine.Timer through mp_sched plus pokes from the big native draw
//   verbs -- which billed the band memcpys to every frame AND set a floor on
//   band size: a band had to transfer LONGER than the 2 ms timer period or
//   the SPI starved between fires (48 rows = 3.07 ms; 32-row bands measured
//   53.9 -> 51.8 fps on Brick Siege under the timer, 2026-08-21). The feeder
//   retires both: MicroPython's VM task is pinned to core 1 (mphalport.h
//   MP_TASK_COREID), so the feed moves to a FreeRTOS task on core 0 (shared
//   with the mostly-idle WiFi/BT stacks, priority BELOW both -- the two
//   bounce slots absorb a radio burst), woken per-band by the done-ISR,
//   itself pinned to core 0 via isr_cpu_id. The VM core neither copies bands
//   nor fields per-band interrupts, and the band-size floor is gone (there is
//   no timer to outlast). A dead feeder cannot degrade quietly: init() FAILS
//   if the task cannot be created -- there is no feederless flush path.
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
//     driver). Two things rest on that: the feeder arms the window BEFORE
//     resetting the band counters, so a timed-out frame's stale bands have
//     all completed -- and their completion ISRs run -- before s_done
//     restarts at 0 (the pre-feeder kick kept the same order for the same
//     recovery); and num_trans_inflight cannot creep across frames, because
//     every frame's arm recycles the previous frame's bands.
//   * the done-ISR may (and must) yield itself: esp_lcd's spi post_cb IGNORES
//     the on_color_trans_done bool return, so vTaskNotifyGiveFromISR +
//     portYIELD_FROM_ISR happen inside the callback or a woken feeder waits
//     out the rest of the FreeRTOS tick (10 ms at this build's HZ=100).
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

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/idf_additions.h"   // xTaskCreatePinnedToCore

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"   // pulls in esp_lcd_panel_st7789.h
#include "esp_rom_sys.h"
#include "esp_timer.h"

// portYIELD_FROM_ISR's ARG form expands this IDF trace hook, whose empty
// default lives at the tail of FreeRTOS.h -- which this TU's include order
// (py/mpstate.h pulls freertos headers first) never reaches. Same empty
// default, defensively. (moy_axs carries the identical guard.)
#ifndef traceISR_EXIT_TO_SCHEDULER
#define traceISR_EXIT_TO_SCHEDULER()
#endif

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
#define MOY_LCD_FLUSH_TIMEOUT_US 500000  // a full frame is ~20ms; this is a bug fence

// A BAND MUST BE ONE DMA CHUNK, and the margin is only 6.7%. esp_lcd caps a
// transfer at MIN(max_transfer_sz, SPI_LL_DMA_MAX_BIT_LEN/8), and the S3's
// SPI_LL_DMA_MAX_BIT_LEN is 1<<18 -- so 32768 B. At 52 rows or more each band
// splits in two, and three things break QUIETLY: esp_lcd sets en_trans_done_cb
// only on the LAST chunk (so s_done still counts bands, which hides it), the
// first chunk carries SPI_TRANS_CS_KEEP_ACTIVE, and num_trans_inflight doubles
// to 10 == trans_queue_depth, at which point tx_color takes its recycle branch
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
static uint8_t *s_bounce[MOY_LCD_BOUNCE_SLOTS];
static volatile uint32_t s_done;       // bands whose DMA completed (ISR)
static volatile uint32_t s_done_us;    // when the last one completed (ISR)
static volatile uint32_t s_flushes;
static volatile uint32_t s_last_flush_us;
static bool s_bus_up;
static uint8_t s_madctl;

// --- the in-flight flush (see OVERLAP in the header) -------------------------
// The band index and the queued count are separate because they are separately
// paced: s_bnc_next is what the FEEDER has FED, s_target is what esp_lcd has
// been HANDED, and s_done is what the panel has ACCEPTED. Band k may reuse
// bounce slot k % SLOTS once band k-SLOTS has completed, i.e. once
// s_done >= k-(SLOTS-1). Volatile where the FEEDER and the VM core both look:
// these statics live in internal SRAM, which the S3 does not cache, so plain
// in-order stores are visible cross-core -- volatile (plus the handoff
// barrier below) only stops GCC from caching a pre-handoff read.
static volatile int s_bnc_total;       // bands in the frame being shipped
static volatile int s_bnc_next;        // next band to copy + queue
static const uint8_t *s_bnc_src;       // the FRONT framebuffer (immutable while it ships)
static uint32_t s_target;              // bands queued so far this frame (feeder-side)
static bool s_in_pump;                 // reentrancy guard (feeder-only now; kept cheap)
static volatile esp_err_t s_tx_err;    // a queue failure, reported by the next kick/show
static int64_t s_flush_t0;             // kick -> fully-out span, for stats()
static volatile uint32_t s_timeouts;
static volatile uint32_t s_tx_errs;    // queue failures since boot (pump_stats)

// --- THE CORE-0 FEEDER (ported from moy_axs, 2026-08-21; see the header).
// MicroPython's VM task is pinned to core 1 (mphalport.h MP_TASK_COREID); the
// feeder owns the whole flush -- window arm, band synthesis, tail wait -- on a
// task pinned to core 0, priority BELOW the WiFi (23) and lwIP (18) tasks,
// woken per-band by the done-ISR (also on core 0 via isr_cpu_id). Handoff
// protocol, copied from moy_axs verbatim: kick may only run with the feeder
// idle (its callers drain first); the feeder clears s_frame_busy LAST and
// then gives s_done_sem; and kick clears a stale done credit before every
// handoff, so the binary semaphore never carries a previous frame's give into
// the next.
#define MOY_LCD_FEED_CORE   0
#define MOY_LCD_FEED_PRIO   12
#define MOY_LCD_FEED_STACK  4096
static TaskHandle_t volatile s_feed_task;
static SemaphoreHandle_t s_kick_sem;   // MP -> feeder: a prepared frame waits
static SemaphoreHandle_t s_done_sem;   // feeder -> MP: that frame finished
static volatile bool s_frame_busy;     // the feeder owns the io + bookkeeping
static volatile bool s_frame_clean = true;  // the finished frame's verdict
static volatile bool s_task_exit;
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
static volatile bool s_sd_guard;

// The FEEDER handoff's compiler barrier. The hardware needs nothing (the
// shared statics live in uncached internal SRAM and each core's stores are in
// order); what must be stopped is GCC, which may legally cache a file-scope
// static whose address never escapes ACROSS an external call -- i.e. read a
// pre-semaphore copy after the wait. One clobber on each side of both
// semaphores pins every handoff-crossing value.
#define MOY_LCD_HANDOFF_BARRIER() __asm__ volatile ("" ::: "memory")

static void moy_lcd_feed_task_fn(void *arg);

// Feed PACING for the frame in flight, latched into the *_last pair at the next
// kick (the frame is complete by then -- drain ran first). This is the data the
// PUMP diag line prints, and the reason it exists is that "the flush is slow" and
// "the flush is fed late" look identical from the outside: idle_us ~ 0 means the
// ceiling is real transfer time and a bigger band / faster feeder buys nothing.
static volatile uint32_t s_pump_us;    // FEEDER CPU us inside pump() this frame
static volatile uint32_t s_idle_us;    // us the SPI sat starved waiting to be fed
static volatile uint32_t s_idle_n;     // how many bands were fed that late
static volatile int32_t s_feed_us = -1;  // frame start -> last band queued
static uint32_t s_kick_us;             // when the feeder started (us, wrapping)
static uint32_t s_block_us;            // VM CPU us BLOCKED in drain this frame
static uint32_t s_pump_last_us;
static uint32_t s_idle_last_us;
static uint32_t s_idle_last_n;
static int32_t s_feed_last_us = -1;
static uint32_t s_block_last_us;

static bool moy_lcd_drain_locked(void);   // defined below; deinit fences on it
static mp_obj_t moy_lcd_deinit(void);     // init's feeder-failure unwind

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
    // Wake the FEEDER: a bounce slot just freed (or the tail completed). The
    // yield must happen HERE -- esp_lcd's spi post_cb ignores this callback's
    // return value (see THREADING in the header), so returning true wakes
    // nobody until the next tick.
    if (s_feed_task != NULL) {
        BaseType_t hp = pdFALSE;
        vTaskNotifyGiveFromISR((TaskHandle_t)s_feed_task, &hp);
        portYIELD_FROM_ISR(hp);
    }
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
    s_frame_busy = false;
    s_frame_clean = true;
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
        // Python side but not these statics (nor the FEEDER task, which
        // survives a soft reset by design). Wait any in-flight FEEDER frame
        // out, then clear the band bookkeeping: a leftover s_bnc_total would
        // make the next frame re-feed a dead frame's bands into a window that
        // no longer describes them.
        moy_lcd_drain_locked();
        s_bnc_total = 0;
        s_bnc_next = 0;
        s_bnc_src = NULL;
        s_done = 0;
        s_target = 0;
        s_tx_err = ESP_OK;
        s_frame_busy = false;
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

    // THE CORE-0 FEEDER: semaphores + task, created once, kept across soft
    // resets (deinit stops the task; a failed create tears the whole init
    // down -- there is no feederless flush path to fall back to, and the 2 ms
    // timer the feeder replaced is gone from tdeck_panel).
    if (s_kick_sem == NULL) {
        s_kick_sem = xSemaphoreCreateBinary();
    }
    if (s_done_sem == NULL) {
        s_done_sem = xSemaphoreCreateBinary();
    }
    bool feed_ok = (s_kick_sem != NULL && s_done_sem != NULL);
    if (feed_ok && s_feed_task == NULL) {
        s_task_exit = false;
        feed_ok = xTaskCreatePinnedToCore(moy_lcd_feed_task_fn, "moy_lcd_feed",
                                          MOY_LCD_FEED_STACK, NULL,
                                          MOY_LCD_FEED_PRIO,
                                          (TaskHandle_t *)&s_feed_task,
                                          MOY_LCD_FEED_CORE) == pdPASS;
    }
    if (!feed_ok) {
        moy_lcd_deinit();       // full unwind: panel, io, bus, buffers
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_lcd: no feeder task"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(moy_lcd_init_obj, 0, moy_lcd_init);

static mp_obj_t moy_lcd_deinit(void) {
    // Never tear a driver down under a live DMA: the bounce buffers are freed
    // below and the SPI host is handed back, both of which an in-flight band
    // would still be reading.
    if (s_panel) { moy_lcd_drain_locked(); }
    if (s_feed_task != NULL) {
        // Stop the FEEDER before the panel io goes away under it.
        s_task_exit = true;
        xSemaphoreGive(s_kick_sem);
        for (int i = 0; i < 100 && s_feed_task != NULL; i++) {
            mp_hal_delay_ms(1);
        }
        s_task_exit = false;
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

// Runs on the FEEDER, so it may not raise: errors return, and the caller
// latches them into s_tx_err for the next MP-side kick/show to report.
static esp_err_t moy_lcd_arm_window(void) {
    uint8_t p[4];
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_W - 1) >> 8; p[3] = (MOY_LCD_W - 1) & 0xFF;
    esp_err_t err = esp_lcd_panel_io_tx_param(s_io, CMD_CASET, p, 4);
    if (err != ESP_OK) {
        return err;
    }
    p[0] = 0; p[1] = 0; p[2] = (MOY_LCD_H - 1) >> 8; p[3] = (MOY_LCD_H - 1) & 0xFF;
    return esp_lcd_panel_io_tx_param(s_io, CMD_RASET, p, 4);
}

static int moy_lcd_bands(void) {
    return (MOY_LCD_H + MOY_LCD_BAND_ROWS - 1) / MOY_LCD_BAND_ROWS;
}

// Copy + queue every band whose bounce slot has freed. One 30 KB PSRAM->SRAM
// memcpy and one esp_lcd_panel_io_tx_color per band, and the continuation
// bands (lcd_cmd = -1) skip spi_device_acquire_bus entirely thanks to the #66
// no-acquire patch, so they queue and return instead of waiting out the band
// before them. Runs on the CORE-0 FEEDER task only since 2026-08-21 (it used
// to run on the VM core, GIL held, poked from draw ops and a 2 ms soft timer
// -- both retired with the feeder). Cross-core PSRAM reads are coherent: the
// S3 has ONE dcache shared by both cores, so the framebuffer core 1 just drew
// reads back exactly on core 0 (moy_axs proved this shape on glass first).
//
// Errors latch instead of raising -- this task has no MP context (an nlr
// raise here would abort), and the latch also disarms the guard below.
static void moy_lcd_pump_locked(void) {
    if (s_in_pump || s_bnc_total == 0 || s_bnc_next >= s_bnc_total
            || s_tx_err != ESP_OK) {
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
        // the ST7789 streaming into the window armed at the frame's start.
        esp_err_t err = esp_lcd_panel_io_tx_color(s_io, (k == 0) ? CMD_RAMWR : -1,
                                                  slot, nbytes);
        if (err != ESP_OK) {
            // Do not raise: no MP context on the feeder. Latch it (which also
            // disarms the pump above), count it, and let the next MP-side
            // kick()/show() surface it as an exception on the frame that can
            // actually report one.
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

// MP-side kick: latch the last frame's diag, set up the band bookkeeping and
// hand the frame to the CORE-0 FEEDER. No esp_lcd call happens on this thread
// -- the window arm and every band are the feeder's -- so this returns in
// microseconds. The caller must have drained first (kick/show do), which is
// what makes the diag latch and the counter writes race-free here.
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
    // A new frame starts on a clean error slate -- the previous one's latched
    // error has done its jobs by now (it disarmed the pump and stopped the
    // feeder's loop, it is counted in s_tx_errs, and the caller raised it
    // before calling here). Leaving it set would keep the pump disarmed.
    s_tx_err = ESP_OK;
    s_bnc_next = 0;
    s_bnc_src = s_fbs[n];
    s_bnc_total = moy_lcd_bands();
    // s_done/s_target are NOT reset here: the feeder resets them after its
    // window arm, whose tx_param recycles every stale in-flight band first
    // (see THREADING in the header) -- the same arm-before-reset order the
    // pre-feeder kick kept, for the same timed-out-flush recovery.
    s_frame_clean = false;
    // A stale done credit survives when drain's fast path never took the
    // semaphore; clear it so the next give is THIS frame's (see the FEEDER
    // handoff protocol).
    xSemaphoreTake(s_done_sem, 0);
    MOY_LCD_HANDOFF_BARRIER();
    s_frame_busy = true;
    xSemaphoreGive(s_kick_sem);
}

// FEEDER-side: one whole flush, on core 0 (THE CORE-0 FEEDER above). This is
// what kick_locked + drain_locked used to do on the VM core: arm the window
// (whose tx_param recycles the previous frame's transactions -- the recovery
// point after a timed-out flush), then copy + queue every band as its bounce
// slot frees -- SLEEPING on the completion ISR's task notify instead of
// waiting on a 2 ms timer to fire -- then wait the tail out.
static void moy_lcd_run_frame(void) {
    esp_err_t werr = moy_lcd_arm_window();
    if (werr != ESP_OK) {
        s_tx_err = werr;
        s_tx_errs++;
        goto out;
    }
    // Every stale completion ISR has run (the arm recycled its transaction),
    // so the counters restart clean.
    s_done = 0;
    s_target = 0;
    s_flush_t0 = esp_timer_get_time();
    s_kick_us = (uint32_t)s_flush_t0;
    int64_t deadline = s_flush_t0 + MOY_LCD_FLUSH_TIMEOUT_US;
    bool ok = true;
    // A queue error stops the FEED (there is no point copying more bands into
    // a stream esp_lcd refused) but the tail wait below still runs: already-
    // queued bands are unaffected and still reading a bounce buffer.
    while (s_bnc_next < s_bnc_total && s_tx_err == ESP_OK) {
        int before = s_bnc_next;
        moy_lcd_pump_locked();
        if (s_bnc_next == before && s_tx_err == ESP_OK) {
            if (esp_timer_get_time() > deadline) {
                ok = false;
                break;
            }
            // The done-ISR's notify is the wake; the tick timeout is only
            // insurance. 2 ticks, NOT pdMS_TO_TICKS(a small ms): FREERTOS_HZ
            // is 100 on this build, so pdMS_TO_TICKS(5) is ZERO ticks -- a
            // busy spin (moy_axs's lesson, kept).
            ulTaskNotifyTake(pdTRUE, 2);
        }
    }
    // Whatever was QUEUED must finish before this frame may be handed back as
    // done -- this is the promise the SD sync fence relies on.
    while (ok && s_tx_err == ESP_OK && s_done < s_target) {
        if (esp_timer_get_time() > deadline) {
            ok = false;
            break;
        }
        ulTaskNotifyTake(pdTRUE, 2);
    }
    if (ok && s_tx_err == ESP_OK) {
        s_flushes++;
        // kick -> LAST COMPLETION, taken from the ISR's own stamp, not from
        // the clock now: this task is reached late under the overlap, so
        // `now - s_flush_t0` would fold in scheduling latency. (The original
        // form of this lesson: tdeck_smoke.panel() sleeps 120ms between
        // flushes and would have reported the transfer as 120ms.)
        s_last_flush_us = s_done_us - (uint32_t)s_flush_t0;
        s_frame_clean = true;
    } else if (!ok) {
        s_timeouts++;
    }
out:
    s_bnc_total = 0;
    s_bnc_next = 0;
    s_bnc_src = NULL;
    // LAST: hand the frame back (the handoff protocol in the FEEDER prose).
    MOY_LCD_HANDOFF_BARRIER();
    s_frame_busy = false;
    xSemaphoreGive(s_done_sem);
}

static void moy_lcd_feed_task_fn(void *arg) {
    (void)arg;
    for (;;) {
        if (xSemaphoreTake(s_kick_sem, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (s_task_exit) {
            break;
        }
        MOY_LCD_HANDOFF_BARRIER();
        moy_lcd_run_frame();
    }
    s_feed_task = NULL;
    vTaskDelete(NULL);
}

// MP-side drain: wait the FEEDER's frame out. The fast path -- the frame
// already finished, the ordinary overlap cadence -- is one volatile read;
// otherwise the GIL is released across a semaphore wait. The feeder's own
// deadline guarantees the wait terminates; the bounded loop is insurance
// against a wedged feeder, sized never to fire. Returns false on a timeout
// (bands may still be in flight -- the next frame's arm_window recovers,
// because a command recycles the queue first).
static bool moy_lcd_drain_locked(void) {
    if (!s_frame_busy) {
        MOY_LCD_HANDOFF_BARRIER();
        return s_frame_clean;
    }
    uint32_t b0 = (uint32_t)esp_timer_get_time();
    MP_THREAD_GIL_EXIT();
    for (int i = 0; s_frame_busy && i < 4; i++) {
        xSemaphoreTake(s_done_sem, pdMS_TO_TICKS(300));
    }
    MP_THREAD_GIL_ENTER();
    MOY_LCD_HANDOFF_BARRIER();
    s_block_us += (uint32_t)esp_timer_get_time() - b0;
    return s_frame_clean && !s_frame_busy;
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
    moy_lcd_drain_locked();         // defensive: kick without a flush() before it
    // The FEEDER runs the SPI, so an error surfaces one frame late: raise the
    // finished frame's before handing this one over.
    if (s_tx_err != ESP_OK) {
        esp_err_t e = s_tx_err;
        s_tx_err = ESP_OK;
        moy_lcd_check(e, "tx_color");
    }
    moy_lcd_kick_locked(n);
    if (s_sd_guard) {
        moy_lcd_drain_locked();     // SD session live: no overlap (see the guard)
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lcd_kick_obj, 0, 1, moy_lcd_kick);

// sd_guard(on): bracket an SD session (see the SD SESSION GUARD note above).
// Turning it ON also drains, so a frame already in the feeder's hands cannot
// straddle the session start.
static mp_obj_t moy_lcd_sd_guard(mp_obj_t on_in) {
    bool on = mp_obj_is_true(on_in);
    s_sd_guard = on;
    if (on && s_panel != NULL) {
        moy_lcd_drain_locked();
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
    bool ok = moy_lcd_drain_locked();
    return ok ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_drain_obj, moy_lcd_drain);

// pending() -> True while a flush is in flight (fed or not). What an SD op or a
// teardown has to see as False.
static mp_obj_t moy_lcd_pending(void) {
    return s_frame_busy ? mp_const_true : mp_const_false;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lcd_pending_obj, moy_lcd_pending);

// show(n=0): push framebuffer n and BLOCK until it is fully out. kick + drain in
// one call -- what the bring-up smokes want, and the always-correct fallback.
static mp_obj_t moy_lcd_show(size_t n_args, const mp_obj_t *a) {
    moy_lcd_require();
    int n = moy_lcd_fb_index(n_args, a);
    moy_lcd_drain_locked();
    s_tx_err = ESP_OK;              // show reports its OWN frame's errors
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
//   pump    CPU us inside pump() -- the band memcpys. Since the CORE-0 FEEDER
//           (2026-08-21) this runs on core 0 and is NOT billed to the frame;
//           it stays reported because a rising value still means real work
//           (and a zero means the feeder never ran)
//   idle    us the SPI sat starved because a band was fed after the previous
//           one had already finished. THE pacing number: ~0 means the ceiling is
//           real transfer time and a faster feeder buys nothing
//   gaps    how many bands were fed that late
//   feed    frame start -> last band queued
//   blocked us the VM CPU actually spent waiting in drain -- what the feeder
//           is supposed to drive toward ~0 from the old kick+drain residue
//   timeouts / errs  both must stay 0. A queue error that happens during a
//           drain cannot be raised (drain must not throw into the frame loop),
//           so `errs` is the only place it is visible at all.
static mp_obj_t moy_lcd_pump_stats(void) {
    mp_obj_t t[8] = {
        mp_obj_new_int_from_uint(s_pump_last_us),
        mp_obj_new_int_from_uint(s_idle_last_us),
        mp_obj_new_int_from_uint(s_idle_last_n),
        mp_obj_new_int(s_feed_last_us),
        MP_OBJ_NEW_SMALL_INT(moy_lcd_bands()),
        mp_obj_new_int_from_uint(s_block_last_us),
        mp_obj_new_int_from_uint(s_timeouts),
        mp_obj_new_int_from_uint(s_tx_errs),
    };
    return mp_obj_new_tuple(8, t);
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
