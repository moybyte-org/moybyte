// moy_ppa: ESP32-P4 Pixel-Processing Accelerator (PPA) bindings (#58).
//
// The P4 has a dedicated 2D DMA engine (the PPA) that scales/rotates/mirrors,
// blends (alpha + color-key) and fills image blocks between PSRAM buffers with
// ZERO CPU involvement -- exactly the write-bound composite ops that dominate
// the desktop frame (game->window scale blit, wallpaper cover-crop, fills) and
// that the CPU moy_gfx kernel pays PSRAM-bandwidth-bound against the continuous
// DSI scan-out. This module is the thin MicroPython surface over the ESP-IDF
// esp_driver_ppa; the driver writes back the input window and INVALIDATES the
// output window (both row-scoped: pic_w x block_h from block_offset_y), so the
// caller has to hand it a cache-aligned OUTPUT buffer -- which the DSI
// framebuffer from esp_lcd_dpi_panel_get_frame_buffer already is -- and to
// write back its own dirty CPU lines in that window first, since the
// invalidate discards them.
//
// SRM = Scale-Rotate-Mirror. blit_scale() is the integer-upscale composite that
// mirrors moy_gfx.blit565_scale so run_ppa_smoke can A/B them on glass.

#include <string.h>
#include "py/obj.h"
#include "py/runtime.h"

#include "driver/ppa.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_async_memcpy.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/idf_additions.h"

// The ONE nearest-neighbour expand kernel (blit_crisp below). Staged sibling:
// build.sh places the shared moy_gfx at ../.staged/moy_gfx, and this module's
// micropython.cmake adds that include dir -- so the compiler checks the
// signature and the linker joins the single body; no transcribed twin.
#include "moy_gfx_kernels.h"

static ppa_client_handle_t s_srm = NULL;
static ppa_client_handle_t s_fill = NULL;

// Async fence (the composite-overlap lever): every submitted transaction bumps
// s_submitted; the PPA's done ISR bumps s_done. sync() spins until they meet.
// Single-writer per counter (main thread writes s_submitted, ISR writes s_done),
// so no atomics needed -- just volatile for fresh reads across the spin.
static volatile uint32_t s_submitted = 0;
static volatile uint32_t s_done = 0;

static volatile uint32_t s_timeouts = 0;

// A real op is microseconds; this is a bug fence, not a knob.
#define PPA_FENCE_TIMEOUT_US 500000

static SemaphoreHandle_t s_rb_ppa_sem = NULL;   // the bounce worker's wake-up
// Every engine submit from this module takes this. The IDF driver guards its
// queues with spinlocks but the submit -> engine-start handoff is not one
// critical section; with the bounce worker and the console submitting to the
// same client from two cores, transactions were lost -- never completed,
// never recycled -- until the client's 24-element pool was empty and every
// later submit was refused for the session (Guition P4, 2026-09-09).
static SemaphoreHandle_t s_submit_lock = NULL;

static esp_err_t ppa_submit_srm(ppa_srm_oper_config_t *op) {
    if (s_submit_lock != NULL) {
        xSemaphoreTake(s_submit_lock, portMAX_DELAY);
    }
    esp_err_t err = ppa_do_scale_rotate_mirror(s_srm, op);
    if (s_submit_lock != NULL) {
        xSemaphoreGive(s_submit_lock);
    }
    return err;
}
static volatile uint32_t s_rb_cb_flagged;

static bool ppa_trans_done_cb(ppa_client_handle_t client,
                              ppa_event_data_t *edata, void *user_data) {
    // Atomic: the bounce worker also retires a transaction it could not
    // submit (see rb_worker), so this counter has two writers now.
    __atomic_fetch_add(&s_done, 1, __ATOMIC_RELAXED);
    BaseType_t hp = pdFALSE;
    if (user_data != NULL) {
        // A bounce band: the SRAM band it read is free again, and the worker
        // may be blocked on exactly that.
        *(volatile bool *)user_data = false;
        s_rb_cb_flagged++;
        if (s_rb_ppa_sem != NULL) {
            xSemaphoreGiveFromISR(s_rb_ppa_sem, &hp);
        }
    }
    return hp == pdTRUE;
}

// In-flight transactions. SIGNED difference on purpose: a completion landing
// after a fence gave up pushes s_done PAST s_submitted, and an unsigned compare
// would then read as billions in flight and wedge every later fence for good.
static inline uint32_t ppa_inflight(void) {
    int32_t d = (int32_t)(s_submitted - s_done);
    return d > 0 ? (uint32_t)d : 0;
}

// Wait until at most `keep` transactions remain. False = it gave up.
//
// Every fence in this module goes through here, because a completion that never
// fires would otherwise spin forever INSIDE the frame loop. Giving up cannot
// raise -- a fence runs where a throw would take the desktop down -- so
// s_timeouts is the only place such a failure is ever visible.
static bool ppa_wait(uint32_t keep) {
    int64_t deadline = esp_timer_get_time() + PPA_FENCE_TIMEOUT_US;
    while (ppa_inflight() > keep) {
        if (esp_timer_get_time() > deadline) {
            s_timeouts++;
            // Counters bankrupt, rather than leave a phantom in flight that
            // every LATER fence waits out too. Safe only because a failed
            // fence's caller always bails instead of reusing the buffer it
            // waited on -- a new caller that reuses it breaks this.
            s_done = s_submitted;
            return false;
        }
    }
    return true;
}

// init() -> True once the SRM client is registered (idempotent). False on
// failure (no PPA / OOM), so the caller can fall back to the CPU kernel.
static mp_obj_t moy_ppa_init(void) {
    if (s_srm != NULL) {
        return mp_const_true;
    }
    ppa_client_config_t cfg = {
        .oper_type = PPA_OPERATION_SRM,
        // Pending slots for TWO frames of the rotated compositor's queued ops
        // (a drag frame is a stamp, up to six stale copies, three rects and
        // the strip, and the frame before it may still be flying at submit).
        // A full queue does NOT block: the driver fails the submit outright
        // (xQueueReceive with no wait, "exceed maximum pending transactions"),
        // which this module raises and the compositor answers with a fence
        // and a blocking retry -- correct, and a meter says it happened.
        // Sprite BATCHING via the queue was measured a dead end
        // -- 64x 16x16 queued = 4.57ms vs 0.70ms for the CPU (~10x vs spr_batch);
        // per-op submit overhead dwarfs a tiny blit. The PPA is a SCALE
        // accelerator (the upscale composite), not a sprite compositor.
        .max_pending_trans_num = 24,
    };
    esp_err_t err = ppa_register_client(&cfg, &s_srm);
    if (err != ESP_OK) {
        s_srm = NULL;
        return mp_const_false;
    }
    if (s_submit_lock == NULL) {
        s_submit_lock = xSemaphoreCreateMutex();
    }
    ppa_event_callbacks_t cbs = { .on_trans_done = ppa_trans_done_cb };
    ppa_client_register_event_callbacks(s_srm, &cbs);
    // A separate FILL client (#155). Why a DMA fill is worth having when a DMA
    // 1:1 COPY measured a wash against the CPU: a CPU write to PSRAM goes
    // through the cache with WRITE-ALLOCATE, so the line is READ IN before it
    // is written and a "pure write" actually moves twice its bytes. The PPA
    // writes PSRAM directly, without the allocate read -- so unlike a copy,
    // where both engines move the same bytes, a fill should cost the DMA half
    // what it costs the CPU.
    ppa_client_config_t fcfg = {
        .oper_type = PPA_OPERATION_FILL,
        .max_pending_trans_num = 1,
    };
    if (ppa_register_client(&fcfg, &s_fill) != ESP_OK) {
        s_fill = NULL;            // fill unavailable; the CPU path still works
    }
    s_submitted = 0;
    s_done = 0;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_init_obj, moy_ppa_init);

static void crisp_free_bands(void);
static void rb_free_bands(void);

static mp_obj_t moy_ppa_deinit(void) {
    crisp_free_bands();
    rb_free_bands();
    if (s_srm != NULL) {
        ppa_unregister_client(s_srm);
        s_srm = NULL;
    }
    if (s_fill != NULL) {
        ppa_unregister_client(s_fill);
        s_fill = NULL;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_deinit_obj, moy_ppa_deinit);

// blit_scale(dst, dw, dh, dx, dy, src, sw, sh, scale)
//   Integer-upscale the whole sw x sh RGB565 source into dst at (dx, dy) by
//   `scale` -- the hardware sibling of moy_gfx.blit565_scale. Blocking: returns
//   after the DMA completes (the driver has synced caches), so the framebuffer
//   is ready to scan out. dst must be the full-picture buffer (dw x dh); the
//   scaled block (sw*scale x sh*scale) must land inside it (no clip yet -- the
//   in-bounds game->window composite; cover-crop's negative offset is a
//   follow-up that crops on the INPUT side).
static mp_obj_t srm_blit(const mp_obj_t *args, ppa_trans_mode_t mode) {
    if (s_srm == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_ppa not init"));
    }
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t dx = mp_obj_get_int(args[3]);
    mp_int_t dy = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &src, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(args[6]);
    mp_int_t sh = mp_obj_get_int(args[7]);
    mp_int_t scale = mp_obj_get_int(args[8]);
    if (scale < 1) {
        scale = 1;
    }

    // The out picture is the ROWS the scaled block lands on, not the whole
    // framebuffer -- the same scoping rotate()/rotate_scale() do. The driver's
    // own invalidate is ALREADY row-scoped (ppa_srm.c syncs an "out_buffer
    // extended window" of pic_w * block_h from block_offset_y, not the buffer),
    // so the only whole-picture cache walk left per submit was the writeback
    // below: the entire framebuffer, per game composite and per drag stamp, for
    // a block that is a fraction of it. Rows OUTSIDE the block keep their dirty
    // CPU lines -- nothing invalidates them -- and moy_dsi.show() msyncs the
    // whole framebuffer before the scan-out switch, so they still reach memory.
    // The driver rejects an out buffer whose ADDRESS or SIZE is not cache-line
    // aligned; a row span inherits that only from a row stride that is a whole
    // number of lines (an RGB565 width that is a multiple of 32px), so a
    // picture that does not qualify keeps the whole buffer.
    mp_int_t ow = sw * scale, oh = sh * scale;
    uint8_t *out = (uint8_t *)dst.buf;
    size_t out_len = dst.len;
    mp_int_t out_h = dh, out_y = dy;
    if (dw > 0 && dh > 0 && dx >= 0 && dy >= 0
            && dx + ow <= dw && dy + oh <= dh
            && (mp_int_t)dst.len >= dw * dh * 2
            && ((uintptr_t)dst.buf & 63u) == 0 && (((size_t)dw * 2u) & 63u) == 0) {
        out = (uint8_t *)dst.buf + (size_t)dy * (size_t)dw * 2u;
        out_len = (size_t)oh * (size_t)dw * 2u;
        out_h = oh;
        out_y = 0;
    }

    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = src.buf,
            .pic_w = (uint32_t)sw,
            .pic_h = (uint32_t)sh,
            .block_w = (uint32_t)sw,
            .block_h = (uint32_t)sh,
            .block_offset_x = 0,
            .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = out,
            .buffer_size = (uint32_t)out_len,
            .pic_w = (uint32_t)dw,
            .pic_h = (uint32_t)out_h,
            .block_offset_x = (uint32_t)dx,
            .block_offset_y = (uint32_t)out_y,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = PPA_SRM_ROTATION_ANGLE_0,
        .scale_x = (float)scale,
        .scale_y = (float)scale,
        .mirror_x = false,
        .mirror_y = false,
        // Both source and destination are canonical little-endian RGB565 (the
        // moy_dsi PAL565_WIRE order), so no swap -- pass the bytes through.
        .rgb_swap = false,
        .byte_swap = false,
        .alpha_update_mode = PPA_ALPHA_NO_CHANGE,
        .mode = mode,
    };
    // WRITE BACK the destination's dirty CPU cache lines BEFORE submitting: the
    // IDF driver INVALIDATES the out window at submit, which otherwise DISCARDS
    // every not-yet-flushed CPU write of the current frame that falls in it
    // (glass-confirmed 2026-07-10: drag frames draw strips/chrome/bar/cursor by
    // CPU and then kick the deferred window stamp -- those writes vanished and
    // the pixels reverted two frames, leaving speed-scaled desktop trails). The
    // quiet-game composite never hit this because nothing else CPU-draws on
    // those frames. Scoped to the block's rows, which is exactly what the
    // driver invalidates (see the out picture above).
    esp_cache_msync(out, out_len,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    s_submitted++;
    esp_err_t err = ppa_submit_srm(&op);
    if (err != ESP_OK) {
        s_submitted--;   // no transaction queued -> no done callback will fire
        mp_raise_msg_varg(&mp_type_OSError,
                          MP_ERROR_TEXT("ppa srm failed: %d"), (int)err);
    }
    return mp_const_none;
}

// rotate(dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h, angle[, nb[, wb]])
//   Copy the w x h block at (sx, sy) of the sw x sh RGB565 source into dst
//   (dw x dh) at (dx, dy), ROTATED by `angle` degrees counter-clockwise (0,
//   90, 180, 270 -- the PPA's own convention) at 1:1 scale. The output block
//   is h x w for 90/270 and (dx, dy) is its top-left. Blocking unless `nb`
//   is true, when it is queued and returns at once (the caller fences with
//   wait()/sync() before touching either buffer). `wb` (default true) writes
//   the destination rows' CPU cache back before the submit; see the msync
//   below for when a caller may drop it. The landscape
//   console on a portrait DSI panel (the Guition P4, device/dsi_panel.py's
//   RotatedCompositor) is the consumer: the whole paint buffer per full
//   frame, one game rect per quiet frame, and angle 0 to bring a ping-pong
//   framebuffer up to date from its sibling.
static mp_obj_t moy_ppa_rotate(size_t n_args, const mp_obj_t *args) {
    if (s_srm == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_ppa not init"));
    }
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t dx = mp_obj_get_int(args[3]);
    mp_int_t dy = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &src, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(args[6]);
    mp_int_t sh = mp_obj_get_int(args[7]);
    mp_int_t sx = mp_obj_get_int(args[8]);
    mp_int_t sy = mp_obj_get_int(args[9]);
    mp_int_t w = mp_obj_get_int(args[10]);
    mp_int_t h = mp_obj_get_int(args[11]);
    mp_int_t angle = mp_obj_get_int(args[12]);
    bool nb = n_args > 13 && mp_obj_is_true(args[13]);
    bool wb = n_args <= 14 || mp_obj_is_true(args[14]);
    ppa_srm_rotation_angle_t rot;
    switch (angle) {
        case 0: rot = PPA_SRM_ROTATION_ANGLE_0; break;
        case 90: rot = PPA_SRM_ROTATION_ANGLE_90; break;
        case 180: rot = PPA_SRM_ROTATION_ANGLE_180; break;
        case 270: rot = PPA_SRM_ROTATION_ANGLE_270; break;
        default:
            mp_raise_ValueError(MP_ERROR_TEXT("angle 0/90/180/270"));
    }
    if (w <= 0 || h <= 0 || sx < 0 || sy < 0 || sx + w > sw || sy + h > sh
            || dx < 0 || dy < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("rotate block"));
    }
    mp_int_t ow = (angle == 90 || angle == 270) ? h : w;
    mp_int_t oh = (angle == 90 || angle == 270) ? w : h;
    if (dx + ow > dw || dy + oh > dh || (mp_int_t)dst.len < dw * dh * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("rotate dst block"));
    }
    // The out picture is the ROWS the block lands on, not the whole dst: the
    // driver writes back and invalidates the whole out picture per submit,
    // and so does the msync below, so a 2MB scan buffer cost ~2MB of cache
    // walking per op whatever the block. A row span of an RGB565 picture
    // whose width is a multiple of 32px starts cache-line aligned.
    uint8_t *rows = (uint8_t *)dst.buf + (size_t)dy * (size_t)dw * 2;
    size_t rows_len = (size_t)oh * (size_t)dw * 2;
    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = src.buf,
            .pic_w = (uint32_t)sw,
            .pic_h = (uint32_t)sh,
            .block_w = (uint32_t)w,
            .block_h = (uint32_t)h,
            .block_offset_x = (uint32_t)sx,
            .block_offset_y = (uint32_t)sy,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = rows,
            .buffer_size = (uint32_t)rows_len,
            .pic_w = (uint32_t)dw,
            .pic_h = (uint32_t)oh,
            .block_offset_x = (uint32_t)dx,
            .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = rot,
        .scale_x = 1.0f,
        .scale_y = 1.0f,
        .mirror_x = false,
        .mirror_y = false,
        .rgb_swap = false,
        .byte_swap = false,
        .alpha_update_mode = PPA_ALPHA_NO_CHANGE,
        .mode = nb ? PPA_TRANS_MODE_NON_BLOCKING : PPA_TRANS_MODE_BLOCKING,
    };
    // The same dst writeback srm_blit does (the driver invalidates the out
    // rows at submit, which DISCARDS a dirty CPU line there); the driver
    // writes back the in block itself.
    //
    // `wb` false skips it, and a caller may say so ONLY for a destination the
    // CPU never writes -- the rotated compositor's scan buffers (CPU-filled
    // once at init, and moy_dsi.show() msyncs the whole buffer at every
    // present) and its game-copy scratch (written by this engine alone). The
    // invalidate is what makes that safe rather than merely likely: it also
    // drops any dirty line a previous owner of the memory left behind, so
    // nothing can evict over the DMA's pixels afterwards. It must NOT be
    // skipped for a CPU-painted destination -- the paint buffer the drag
    // stamp lands in -- or that frame's chrome is discarded (the 2026-07-10
    // desktop trails this msync was added for).
    if (wb) {
        esp_cache_msync(rows, rows_len,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    }
    s_submitted++;
    esp_err_t err = ppa_submit_srm(&op);
    if (err != ESP_OK) {
        s_submitted--;
        mp_raise_msg_varg(&mp_type_OSError,
                          MP_ERROR_TEXT("ppa rotate failed: %d"), (int)err);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_rotate_obj, 13, 15, moy_ppa_rotate);

// rotate_bounce(dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h, angle[, nb])
//   rotate() with the SOURCE bounced through internal SRAM, off the CPU: a
//   worker task on the other core has the AXI GDMA copy the block's rows a
//   band at a time into one of two SRAM bands while the engine rotates the
//   previous band from the other. Returns the number of engine transactions
//   the job will submit (the caller's op count for its fences -- they are
//   counted as submitted HERE, so wait()/done()/sync() see the job's work
//   before the worker has started it), 0 when the queue is full this once,
//   or -1 when the pipeline is not to be had (the caller uses rotate()).
//
//   Why: the SRM engine reads PSRAM at ~40MB/s and internal SRAM at ~4x
//   that, while it writes PSRAM at ~140MB/s and the AXI GDMA reads PSRAM
//   into SRAM at ~130MB/s -- measured on the Guition P4 (2026-09-09): a
//   1092x559 block rotated from the paint buffer took 29ms of engine time,
//   and the rotated compositor's damage frames (a drag, a scroll, a
//   keystroke) were bound by it. The first cut copied the bands with the
//   CPU inside the call and made the frame SLOWER (a scroll 18 -> 29ms): the
//   engine's time used to overlap the next frame's draw, and a synchronous
//   copy took that overlap away. So the copy is DMA and the driver is a
//   task; the calling frame pays one cache writeback of the block's rows
//   and an enqueue.
//
//   The copy takes FULL ROWS of the source (the engine then reads its block
//   at an x offset inside the band): a per-row DMA transfer costs ~80us of
//   setup against ~15us of transfer, so rows are moved as one contiguous
//   span. A job is fenced behind every transaction submitted before it
//   (the drag stamp writes the very rows it reads), the source is written
//   back from the CPU cache at enqueue, and `dst` is a PPA-owned buffer
//   (the scan buffers): no destination writeback, exactly rotate()'s
//   wb=False. Two bands of RB_BAND_BYTES of internal SRAM, allocated on
//   first use and released with the crisp bands; a refusal latches.
#define RB_BAND_BYTES (40 * 1024)
#define RB_QUEUE_LEN 8
#define RB_TASK_PRIO 10
#define RB_TASK_CORE 1

typedef struct {
    uint8_t *dst;
    int32_t dw, dh, dx, dy;
    const uint8_t *src;
    int32_t sw, sx, sy, w, h;
    int32_t angle;
    int32_t band_rows;
    int32_t nbands;
    uint32_t fence;      // s_submitted before this job: what must land first
} rb_job_t;

static uint8_t *s_rb[2] = { NULL, NULL };
static volatile bool s_rb_busy[2] = { false, false };   // a band still feeding a rotate
static bool s_rb_failed = false;
static QueueHandle_t s_rb_q = NULL;
static SemaphoreHandle_t s_rb_dma_sem = NULL;
static TaskHandle_t s_rb_task = NULL;
static volatile uint32_t s_rb_pending = 0;               // jobs queued or running
static volatile uint32_t s_rb_fallbacks = 0;             // bands the CPU copied
// The pipeline's own meters: where a wait gave up, and how many completions
// carried a band flag. A pipeline that KEEPS stalling disables itself
// (rotate_bounce answers -1 while s_rb_stalls exceeds RB_MAX_STALLS) rather
// than turning the desk into a slideshow of fence timeouts; a job that runs
// clean pays a stall back, so the handful a first boot's flash writes cost
// (a non-IRAM completion ISR waits out a flash op) does not retire it for
// the session -- measured 5 at boot on the Guition P4, 2026-09-09, and none
// after.
static volatile uint32_t s_rb_t_fence = 0, s_rb_t_flag = 0, s_rb_t_dma = 0, s_rb_t_submit = 0;
static volatile uint32_t s_rb_stalls = 0;
#define RB_MAX_STALLS 4
static async_memcpy_handle_t s_mcp = NULL;
static bool s_mcp_dead = false;

static bool rb_dma_done_cb(async_memcpy_handle_t h, async_memcpy_event_t *e, void *arg) {
    (void)h; (void)e; (void)arg;
    BaseType_t hp = pdFALSE;
    xSemaphoreGiveFromISR(s_rb_dma_sem, &hp);
    return hp == pdTRUE;
}

static bool rb_dma_install(void) {
    if (s_mcp != NULL) {
        return true;
    }
    if (s_mcp_dead) {
        return false;
    }
    // The AXI engine: the AHB one the generic installer picks cannot reach
    // PSRAM on this chip.
    async_memcpy_config_t cfg = ASYNC_MEMCPY_DEFAULT_CONFIG();
    cfg.backlog = 4;
    cfg.dma_burst_size = 64;
    if (esp_async_memcpy_install_gdma_axi(&cfg, &s_mcp) != ESP_OK) {
        s_mcp = NULL;
        s_mcp_dead = true;
        return false;
    }
    return true;
}

static void rb_free_bands(void) {
    // A queued job would read a freed band: drain the worker first.
    int64_t deadline = esp_timer_get_time() + PPA_FENCE_TIMEOUT_US;
    while (s_rb_pending > 0 && esp_timer_get_time() < deadline) {
        vTaskDelay(1);
    }
    ppa_wait(0);
    for (int i = 0; i < 2; i++) {
        if (s_rb[i] != NULL) {
            heap_caps_free(s_rb[i]);
            s_rb[i] = NULL;
        }
        s_rb_busy[i] = false;
    }
    s_rb_failed = false;
}

static void rb_worker(void *arg);

static bool rb_setup(void) {
    if (s_rb_failed) {
        return false;
    }
    for (int i = 0; i < 2; i++) {
        if (s_rb[i] == NULL) {
            s_rb[i] = heap_caps_aligned_alloc(
                64, RB_BAND_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
            if (s_rb[i] == NULL) {
                rb_free_bands();
                s_rb_failed = true;                   // latch: never re-probe per frame
                return false;
            }
        }
    }
    if (s_rb_q == NULL) {
        s_rb_q = xQueueCreate(RB_QUEUE_LEN, sizeof(rb_job_t));
        s_rb_dma_sem = xSemaphoreCreateBinary();
        // BINARY, not counting: a completion the worker was not waiting for
        // must not bank a wake-up, or a backlog of them makes every later
        // wait return at once -- the first cut counted tries, saw twenty
        // instant returns as a timeout, ran ahead until the engine queue was
        // full and then slept a 10ms tick per refused submit (2026-09-09).
        s_rb_ppa_sem = xSemaphoreCreateBinary();
        if (s_rb_q == NULL || s_rb_dma_sem == NULL || s_rb_ppa_sem == NULL) {
            s_rb_failed = true;
            return false;
        }
    }
    if (s_rb_task == NULL) {
        if (xTaskCreatePinnedToCore(rb_worker, "moy_rb", 4096, NULL, RB_TASK_PRIO,
                                    &s_rb_task, RB_TASK_CORE) != pdPASS) {
            s_rb_task = NULL;
            s_rb_failed = true;
            return false;
        }
    }
    rb_dma_install();                                 // absent -> CPU copies, still off the frame
    return true;
}

// Wait for the ISR-cleared flag / the fence count, waking on completions,
// by DEADLINE: a wake-up that arrives for another band just re-checks.
#define RB_WAIT_US 500000

static bool rb_wait_flag(volatile bool *flag) {
    int64_t deadline = esp_timer_get_time() + RB_WAIT_US;
    while (*flag) {
        if (esp_timer_get_time() > deadline) {
            return false;                             // a completion that never came
        }
        xSemaphoreTake(s_rb_ppa_sem, pdMS_TO_TICKS(5));
    }
    return true;
}

static bool rb_wait_fence(uint32_t fence) {
    int64_t deadline = esp_timer_get_time() + RB_WAIT_US;
    while ((int32_t)(s_done - fence) < 0) {
        if (esp_timer_get_time() > deadline) {
            return false;
        }
        xSemaphoreTake(s_rb_ppa_sem, pdMS_TO_TICKS(5));
    }
    return true;
}

static void rb_worker(void *arg) {
    (void)arg;
    rb_job_t job;
    for (;;) {
        if (xQueueReceive(s_rb_q, &job, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        uint32_t stalls_before = s_rb_stalls;
        // Everything submitted before this job lands first: the drag stamp
        // writes the paint-buffer rows this job is about to read.
        if (!rb_wait_fence(job.fence)) {
            s_timeouts++;
            s_rb_t_fence++;
            s_rb_stalls++;
        }
        size_t sstride = (size_t)job.sw * 2u;
        ppa_srm_rotation_angle_t rot =
            job.angle == 90 ? PPA_SRM_ROTATION_ANGLE_90
            : job.angle == 180 ? PPA_SRM_ROTATION_ANGLE_180
            : job.angle == 270 ? PPA_SRM_ROTATION_ANGLE_270 : PPA_SRM_ROTATION_ANGLE_0;
        for (int32_t i = 0, y0 = 0; y0 < job.h; i++, y0 += job.band_rows) {
            int32_t bh = job.h - y0 < job.band_rows ? job.h - y0 : job.band_rows;
            if (bh < job.band_rows && job.h >= job.band_rows) {
                // NEVER A SLIVER: the last band overlaps the previous one so
                // it is a whole band too (the same rows rotated twice are
                // the same pixels). A 7-row tail -- a 7px-wide rotated
                // output block -- never completed in the engine: the 487-row
                // Settings window wedged every transaction behind it, the
                // same (1 fence, 23 flag, 9 submit) timeouts on every build,
                // while 800-, 559- and 201-row blocks ran clean (2026-09-09).
                y0 = job.h - job.band_rows;
                bh = job.band_rows;
            }
            int k = i & 1;
            uint8_t *band = s_rb[k];
            // The rotate that last read this band must be done with it.
            if (!rb_wait_flag(&s_rb_busy[k])) {
                s_rb_busy[k] = false;
                s_timeouts++;
                s_rb_t_flag++;
                s_rb_stalls++;
            }
            const uint8_t *from = job.src + (size_t)(job.sy + y0) * sstride;
            size_t n = (size_t)bh * sstride;
            bool copied = false;
            if (s_mcp != NULL
                    && esp_async_memcpy(s_mcp, band, (void *)from, n,
                                        rb_dma_done_cb, NULL) == ESP_OK) {
                copied = xSemaphoreTake(s_rb_dma_sem, pdMS_TO_TICKS(100)) == pdTRUE;
                if (!copied) {
                    s_timeouts++;
                    s_rb_t_dma++;
                    s_rb_stalls++;
                }
            }
            if (!copied) {
                // This core's cache may hold the rows from an earlier job.
                esp_cache_msync((void *)from, n,
                                ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
                memcpy(band, from, n);
                s_rb_fallbacks++;
            }
            // Where this band's rotated image lands inside the rotated block:
            // the engine turns counter-clockwise, so for 90 the block's top
            // row becomes its LEFT column (source rows walk right), for 270
            // its RIGHT column (rows walk left), for 180 the rows reverse.
            int32_t bx, by, bhh;
            switch (job.angle) {
                case 90:  bx = job.dx + y0;                    by = job.dy;                        bhh = job.w; break;
                case 270: bx = job.dx + (job.h - y0 - bh);     by = job.dy;                        bhh = job.w; break;
                case 180: bx = job.dx;                         by = job.dy + (job.h - y0 - bh);    bhh = bh;    break;
                default:  bx = job.dx;                         by = job.dy + y0;                   bhh = bh;    break;
            }
            uint8_t *rows = job.dst + (size_t)by * (size_t)job.dw * 2u;
            size_t rows_len = (size_t)bhh * (size_t)job.dw * 2u;
            s_rb_busy[k] = true;
            ppa_srm_oper_config_t op = {
                .in = {
                    .buffer = band,
                    .pic_w = (uint32_t)job.sw,
                    .pic_h = (uint32_t)bh,
                    .block_w = (uint32_t)job.w,
                    .block_h = (uint32_t)bh,
                    .block_offset_x = (uint32_t)job.sx,
                    .block_offset_y = 0,
                    .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
                },
                .out = {
                    .buffer = rows,
                    .buffer_size = (uint32_t)rows_len,
                    .pic_w = (uint32_t)job.dw,
                    .pic_h = (uint32_t)bhh,
                    .block_offset_x = (uint32_t)bx,
                    .block_offset_y = 0,
                    .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
                },
                .rotation_angle = rot,
                .scale_x = 1.0f,
                .scale_y = 1.0f,
                .mirror_x = false,
                .mirror_y = false,
                .rgb_swap = false,
                .byte_swap = false,
                .alpha_update_mode = PPA_ALPHA_NO_CHANGE,
                .mode = PPA_TRANS_MODE_NON_BLOCKING,
                .user_data = (void *)&s_rb_busy[k],
            };
            esp_err_t err = ppa_submit_srm(&op);
            if (err != ESP_OK) {
                // A full engine queue drains at completion rate: wait for one.
                int64_t deadline = esp_timer_get_time() + RB_WAIT_US;
                while (err != ESP_OK && esp_timer_get_time() < deadline) {
                    xSemaphoreTake(s_rb_ppa_sem, pdMS_TO_TICKS(5));
                    err = ppa_submit_srm(&op);
                }
            }
            if (err != ESP_OK) {
                // Counted as submitted at enqueue: retire it so no fence
                // waits on a transaction that never existed.
                s_rb_busy[k] = false;
                __atomic_fetch_add(&s_done, 1, __ATOMIC_RELAXED);
                s_timeouts++;
                s_rb_t_submit++;
            }
        }
        if (s_rb_stalls > 0 && s_rb_stalls == stalls_before) {
            s_rb_stalls--;                            // a clean job pays one back
        }
        __atomic_fetch_sub(&s_rb_pending, 1, __ATOMIC_RELAXED);
    }
}

static mp_obj_t moy_ppa_rotate_bounce(size_t n_args, const mp_obj_t *args) {
    if (s_srm == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_ppa not init"));
    }
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t dx = mp_obj_get_int(args[3]);
    mp_int_t dy = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &src, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(args[6]);
    mp_int_t sh = mp_obj_get_int(args[7]);
    mp_int_t sx = mp_obj_get_int(args[8]);
    mp_int_t sy = mp_obj_get_int(args[9]);
    mp_int_t w = mp_obj_get_int(args[10]);
    mp_int_t h = mp_obj_get_int(args[11]);
    mp_int_t angle = mp_obj_get_int(args[12]);
    bool nb = n_args > 13 && mp_obj_is_true(args[13]);
    if (angle != 0 && angle != 90 && angle != 180 && angle != 270) {
        mp_raise_ValueError(MP_ERROR_TEXT("angle 0/90/180/270"));
    }
    if (w <= 0 || h <= 0 || sx < 0 || sy < 0 || sx + w > sw || sy + h > sh
            || dx < 0 || dy < 0 || (mp_int_t)src.len < sw * sh * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("rotate block"));
    }
    bool turned = (angle == 90 || angle == 270);
    mp_int_t ow = turned ? h : w;
    mp_int_t oh = turned ? w : h;
    if (dx + ow > dw || dy + oh > dh || (mp_int_t)dst.len < dw * dh * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("rotate dst block"));
    }
    // A band is whole source rows, DMA-aligned: the row must fit a band and
    // start on a cache line.
    // -1: this picture never bounces (the caller stops asking); 0: not this
    // time (the queue is full), rotate() it and ask again next frame.
    mp_int_t band_rows = (mp_int_t)RB_BAND_BYTES / (sw * 2);
    if (band_rows < 1 || ((uintptr_t)src.buf & 63u) != 0 || ((sw * 2) & 63) != 0
            || !rb_setup()) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    if (h < band_rows) {
        return MP_OBJ_NEW_SMALL_INT(0);               // a sliver: rotate() it
    }
    if (s_rb_stalls > RB_MAX_STALLS) {
        return MP_OBJ_NEW_SMALL_INT(-1);              // a stalled pipeline retires itself
    }
    if (s_rb_pending >= RB_QUEUE_LEN) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    mp_int_t nbands = (h + band_rows - 1) / band_rows;
    // The CPU's paints of these rows reach memory before the DMA reads them.
    esp_cache_msync((uint8_t *)src.buf + (size_t)sy * (size_t)sw * 2u,
                    (size_t)h * (size_t)sw * 2u,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    rb_job_t job = {
        .dst = (uint8_t *)dst.buf, .dw = (int32_t)dw, .dh = (int32_t)dh,
        .dx = (int32_t)dx, .dy = (int32_t)dy,
        .src = (const uint8_t *)src.buf, .sw = (int32_t)sw,
        .sx = (int32_t)sx, .sy = (int32_t)sy, .w = (int32_t)w, .h = (int32_t)h,
        .angle = (int32_t)angle, .band_rows = (int32_t)band_rows,
        .nbands = (int32_t)nbands,
        .fence = s_submitted,
    };
    __atomic_fetch_add(&s_rb_pending, 1, __ATOMIC_RELAXED);
    s_submitted += (uint32_t)nbands;                  // planned: the fences see them now
    if (xQueueSend(s_rb_q, &job, 0) != pdTRUE) {
        s_submitted -= (uint32_t)nbands;
        __atomic_fetch_sub(&s_rb_pending, 1, __ATOMIC_RELAXED);
        return MP_OBJ_NEW_SMALL_INT(0);
    }
    if (!nb) {
        ppa_wait(0);
    }
    return MP_OBJ_NEW_SMALL_INT(nbands);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_rotate_bounce_obj, 13, 14,
                                           moy_ppa_rotate_bounce);

// rotate_bounce_stats() -> (pending jobs, CPU-copied bands, fence timeouts,
//   band-flag timeouts, DMA timeouts, submit failures, STALLS, flagged
//   completions, busy0, busy1): rotate_bounce's own meters.
//
//   `stalls` is the retirement gate and belongs in the tuple: rotate_bounce
//   answers -1 once it passes RB_MAX_STALLS, and -1 is also what it answers
//   for a picture that can never bounce. Without this counter a pipeline that
//   RETIRED reads exactly like one that was never eligible -- the whole lever
//   gone, and nothing on the board saying so.
//
//   Named for the verb it measures, and not `bounce_stats`: that name is
//   BandedCompositor's, a different 9-tuple (the moy_flush PUMP meters), and
//   one name over two unrelated meters is a meter nobody can ask for by name.
static mp_obj_t moy_ppa_rotate_bounce_stats(void) {
    mp_obj_t t[10] = {
        mp_obj_new_int_from_uint(s_rb_pending),
        mp_obj_new_int_from_uint(s_rb_fallbacks),
        mp_obj_new_int_from_uint(s_rb_t_fence),
        mp_obj_new_int_from_uint(s_rb_t_flag),
        mp_obj_new_int_from_uint(s_rb_t_dma),
        mp_obj_new_int_from_uint(s_rb_t_submit),
        mp_obj_new_int_from_uint(s_rb_stalls),
        mp_obj_new_int_from_uint(s_rb_cb_flagged),
        mp_obj_new_bool(s_rb_busy[0]),
        mp_obj_new_bool(s_rb_busy[1]),
    };
    return mp_obj_new_tuple(10, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_rotate_bounce_stats_obj,
                                 moy_ppa_rotate_bounce_stats);

static volatile uint32_t s_mcp_done = 0;

static bool rb_dma_diag_cb(async_memcpy_handle_t h, async_memcpy_event_t *e, void *arg) {
    (void)h; (void)e; (void)arg;
    s_mcp_done++;
    return false;
}

// dma_copy(dst, dst_off, src, src_off, nbytes) -> microseconds the copy took
//   (blocking), -1 when the engine is unavailable, -2 when it refused the
//   buffers (alignment: PSRAM ends want cache-line aligned address and size).
//   A measurement verb for the bounce pipeline's copy engine.
static mp_obj_t moy_ppa_dma_copy(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t doff = mp_obj_get_int(args[1]);
    mp_get_buffer_raise(args[2], &src, MP_BUFFER_READ);
    mp_int_t soff = mp_obj_get_int(args[3]);
    mp_int_t n = mp_obj_get_int(args[4]);
    if (n <= 0 || doff < 0 || soff < 0 || doff + n > (mp_int_t)dst.len
            || soff + n > (mp_int_t)src.len) {
        mp_raise_ValueError(MP_ERROR_TEXT("dma_copy span"));
    }
    if (!rb_dma_install()) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    uint8_t *d = (uint8_t *)dst.buf + doff;
    uint8_t *sp = (uint8_t *)src.buf + soff;
    esp_cache_msync(sp, (size_t)n, ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    uint32_t want = s_mcp_done + 1;
    int64_t t0 = esp_timer_get_time();
    esp_err_t err = esp_async_memcpy(s_mcp, d, sp, (size_t)n, rb_dma_diag_cb, NULL);
    if (err != ESP_OK) {
        return MP_OBJ_NEW_SMALL_INT(-2);
    }
    int64_t deadline = t0 + PPA_FENCE_TIMEOUT_US;
    while ((int32_t)(s_mcp_done - want) < 0 && esp_timer_get_time() < deadline) {
    }
    int64_t t1 = esp_timer_get_time();
    if ((int32_t)(s_mcp_done - want) < 0) {
        return MP_OBJ_NEW_SMALL_INT(-3);
    }
    return mp_obj_new_int((mp_int_t)(t1 - t0));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_dma_copy_obj, 5, 5, moy_ppa_dma_copy);

// rotate_scale(dst, dw, dh, dx, dy, src, sw, sh, scale, angle[, nb[, wb]])
//   The whole sw x sh RGB565 source, integer-upscaled by `scale` AND rotated
//   by `angle` degrees counter-clockwise, into dst (dw x dh) at (dx, dy) --
//   the quiet game frame of a landscape console on portrait glass in ONE
//   PPA op: the game canvas goes straight to the scan buffer (150KB read,
//   the scaled block written) instead of through the 2MB paint buffer.
//   Bilinear like blit_scale (the PPA has no nearest mode; crisp mode takes
//   the paint-buffer path). Blocking unless `nb` (see rotate).
static mp_obj_t moy_ppa_rotate_scale(size_t n_args, const mp_obj_t *args) {
    if (s_srm == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("moy_ppa not init"));
    }
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t dx = mp_obj_get_int(args[3]);
    mp_int_t dy = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &src, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(args[6]);
    mp_int_t sh = mp_obj_get_int(args[7]);
    mp_int_t scale = mp_obj_get_int(args[8]);
    mp_int_t angle = mp_obj_get_int(args[9]);
    bool nb = n_args > 10 && mp_obj_is_true(args[10]);
    bool wb = n_args <= 11 || mp_obj_is_true(args[11]);
    if (scale < 1) {
        scale = 1;
    }
    ppa_srm_rotation_angle_t rot;
    switch (angle) {
        case 0: rot = PPA_SRM_ROTATION_ANGLE_0; break;
        case 90: rot = PPA_SRM_ROTATION_ANGLE_90; break;
        case 180: rot = PPA_SRM_ROTATION_ANGLE_180; break;
        case 270: rot = PPA_SRM_ROTATION_ANGLE_270; break;
        default:
            mp_raise_ValueError(MP_ERROR_TEXT("angle 0/90/180/270"));
    }
    mp_int_t ow = (angle == 90 || angle == 270) ? sh * scale : sw * scale;
    mp_int_t oh = (angle == 90 || angle == 270) ? sw * scale : sh * scale;
    if (dx < 0 || dy < 0 || dx + ow > dw || dy + oh > dh
            || (mp_int_t)dst.len < dw * dh * 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("rotate_scale dst block"));
    }
    uint8_t *rows = (uint8_t *)dst.buf + (size_t)dy * (size_t)dw * 2;   // see rotate()
    size_t rows_len = (size_t)oh * (size_t)dw * 2;
    ppa_srm_oper_config_t op = {
        .in = {
            .buffer = src.buf,
            .pic_w = (uint32_t)sw,
            .pic_h = (uint32_t)sh,
            .block_w = (uint32_t)sw,
            .block_h = (uint32_t)sh,
            .block_offset_x = 0,
            .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = rows,
            .buffer_size = (uint32_t)rows_len,
            .pic_w = (uint32_t)dw,
            .pic_h = (uint32_t)oh,
            .block_offset_x = (uint32_t)dx,
            .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = rot,
        .scale_x = (float)scale,
        .scale_y = (float)scale,
        .mirror_x = false,
        .mirror_y = false,
        .rgb_swap = false,
        .byte_swap = false,
        .alpha_update_mode = PPA_ALPHA_NO_CHANGE,
        .mode = nb ? PPA_TRANS_MODE_NON_BLOCKING : PPA_TRANS_MODE_BLOCKING,
    };
    if (wb) {                                     // see rotate()
        esp_cache_msync(rows, rows_len,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    }
    s_submitted++;
    esp_err_t err = ppa_submit_srm(&op);
    if (err != ESP_OK) {
        s_submitted--;
        mp_raise_msg_varg(&mp_type_OSError,
                          MP_ERROR_TEXT("ppa rotate_scale failed: %d"), (int)err);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_rotate_scale_obj, 10, 12,
                                           moy_ppa_rotate_scale);

// wait(keep) -> bool: block until at most `keep` queued transactions remain
// in flight -- sync() with a tail left flying. Completion is FIFO, so a caller
// that submits the op it must not outrun FIRST and `keep` ops after it can
// fence that one op alone. False = the fence gave up (stats()[2] counts it).
static mp_obj_t moy_ppa_wait(mp_obj_t keep_in) {
    mp_int_t keep = mp_obj_get_int(keep_in);
    return mp_obj_new_bool(ppa_wait(keep < 0 ? 0 : (uint32_t)keep));
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_ppa_wait_obj, moy_ppa_wait);

// sync(): block until every submitted transaction has completed (the fence for a
// non-blocking composite). The PPA DMA runs on its own; this is a short busy-wait
// only reached when the caller deliberately overlaps then fences.
static mp_obj_t moy_ppa_sync(void) {
    ppa_wait(0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_sync_obj, moy_ppa_sync);

// stats() -> (submitted, done, timeouts). timeouts must stay 0.
static mp_obj_t moy_ppa_stats(void) {
    mp_obj_t t[3] = {
        mp_obj_new_int_from_uint(s_submitted),
        mp_obj_new_int_from_uint(s_done),
        mp_obj_new_int_from_uint(s_timeouts),
    };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_stats_obj, moy_ppa_stats);

// done() -> bool: True when every submitted transaction has completed -- the
// NON-BLOCKING probe of sync()'s fence (the triple-framebuffer present checks
// this per loop and shows the pending frame only once its DMA landed, instead
// of spinning; a caller that must wait still uses sync()).
static mp_obj_t moy_ppa_done(void) {
    return mp_obj_new_bool(ppa_inflight() == 0);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_done_obj, moy_ppa_done);

// blit_scale(...): blocking -- returns after the DMA + cache sync completes.
static mp_obj_t moy_ppa_blit_scale(size_t n_args, const mp_obj_t *args) {
    return srm_blit(args, PPA_TRANS_MODE_BLOCKING);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_blit_scale_obj, 9, 9,
                                           moy_ppa_blit_scale);

// blit_async(...): non-blocking -- enqueues and returns (blocks only if the
// pending queue is full). Drain with a following blocking blit_scale (FIFO), so
// N-1 async + 1 blocking = a batch fence. Measures whether queued submission
// beats the CPU batch blitter.
static mp_obj_t moy_ppa_blit_async(size_t n_args, const mp_obj_t *args) {
    return srm_blit(args, PPA_TRANS_MODE_NON_BLOCKING);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_blit_async_obj, 9, 9,
                                           moy_ppa_blit_async);


// -- CRISP composite: banded nearest-neighbour upscale over an SRAM bounce ----
// (#204 carries the full measurement ledger and the refuted alternatives.)
//
// The PPA's SRM scaler is fixed BILINEAR in silicon (no nearest mode, no
// flag), so a pixel-art cart composited by blit_scale comes out smeared. The
// pure-CPU alternative (moy_gfx.blit565_scale straight into the PSRAM
// framebuffer) measures 12.9ms at scale 2 on glass -- dominated not by the
// arithmetic but by the cache's WRITE-ALLOCATE on the PSRAM destination (the
// same mechanism the fill client's comment records: every "pure write" line
// is read in first). Probed on glass 2026-08-20: the same expansion into an
// internal-SRAM band costs 7.7ms, and a 1:1 PPA ship of the bands is
// ~100MB/s and byte-exact.
//
// So blit_crisp pipelines: expand band i on the CPU (into SRAM -- cheap
// writes) while band i-1's 1:1 PPA DMA ships to the framebuffer. The dest of
// each ship is a BAND-SCOPED picture (the strip's first row, not the whole
// framebuffer), because the driver invalidates the whole out-picture buffer
// per submit -- handing it 1.2MB per band cost ~0.5ms x N bands when this ran
// as Python moy_ppa calls, and scoping it is what a C body buys.
//
// Bands are allocated LAZILY on the first crisp composite and freed by
// crisp_release() (the Settings toggle turning crisp off): internal SRAM is
// the Lua allocator's preferred pool, so a mode nobody enabled must not tax
// it. Any refusal (no PPA / no SRAM / geometry) returns False and the caller
// falls back to the CPU kernel -- identical pixels, slower.

#define CRISP_BAND_BYTES (64 * 1024)
static uint16_t *s_band[2] = { NULL, NULL };
static bool s_band_failed = false;

static void crisp_free_bands(void) {
    // An in-flight ship still READS a band: fence everything first.
    ppa_wait(0);
    for (int i = 0; i < 2; i++) {
        if (s_band[i] != NULL) {
            heap_caps_free(s_band[i]);
            s_band[i] = NULL;
        }
    }
    s_band_failed = false;
}

// crisp_release(): return the bounce bands to the internal heap (crisp off).
static mp_obj_t moy_ppa_crisp_release(void) {
    crisp_free_bands();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_crisp_release_obj,
                                 moy_ppa_crisp_release);

// blit_crisp(dst, dw, dh, dx, dy, src, sw, sh, scale, defer) -> bool
//   Nearest-neighbour sibling of blit_scale/blit_async. defer != 0 returns
//   with the LAST band's DMA still in flight (the caller sets the
//   compositor's composite-pending flag; present_pending's sync() is the
//   fence, exactly the blit_async contract); defer == 0 fences before
//   returning so following CPU chrome can never race the DMA.
static mp_obj_t moy_ppa_blit_crisp(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    if (s_srm == NULL || s_band_failed) {
        return mp_const_false;
    }
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t dx = mp_obj_get_int(args[3]);
    mp_int_t dy = mp_obj_get_int(args[4]);
    mp_get_buffer_raise(args[5], &src, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(args[6]);
    mp_int_t sh = mp_obj_get_int(args[7]);
    mp_int_t scale = mp_obj_get_int(args[8]);
    bool defer = mp_obj_is_true(args[9]);
    if (scale < 1 || sw <= 0 || sh <= 0) {
        return mp_const_false;
    }
    int out_w = sw * scale, out_h = sh * scale;
    // Same fit gate as the bilinear path: the scaled block must land inside
    // the picture (the PPA cannot clip); a non-fit falls to the CPU kernel.
    if (dx < 0 || dy < 0 || dx + out_w > dw || dy + out_h > dh) {
        return mp_const_false;
    }
    // Each ship's out picture starts at a ROW of dst: that address and the
    // strip size must be cache-line aligned or the driver rejects the op.
    // (dw*2 == 2048 on the DSI framebuffer, so rows inherit its alignment.)
    if (((uintptr_t)dst.buf & 63u) != 0 || (((size_t)dw * 2u) & 63u) != 0) {
        return mp_const_false;
    }
    int band_rows = (int)(CRISP_BAND_BYTES / 2 / (size_t)out_w);
    band_rows -= band_rows % scale;          // bands align to whole src rows
    if (band_rows < scale) {
        return mp_const_false;
    }
    for (int i = 0; i < 2; i++) {
        if (s_band[i] == NULL) {
            s_band[i] = heap_caps_aligned_alloc(
                64, CRISP_BAND_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
            if (s_band[i] == NULL) {
                s_band_failed = true;        // latch: don't re-probe per frame
                crisp_free_bands();
                return mp_const_false;
            }
        }
    }
    for (int y = 0, nb = 0; y < out_h; y += band_rows, nb++) {
        int bh = out_h - y < band_rows ? out_h - y : band_rows;
        uint16_t *band = s_band[nb & 1];
        // Reuse fence: this band buffer may still feed the ship submitted two
        // bands ago -- wait until at most ONE transaction is in flight. Giving
        // up must NOT overwrite a band still being read, so bail to the CPU.
        if (!ppa_wait(1)) {
            return mp_const_false;
        }
        mg_blit565_scale(band, CRISP_BAND_BYTES / 2, out_w, bh, 0, 0,
                         (const uint16_t *)src.buf + (size_t)(y / scale) * (size_t)sw,
                         (size_t)(bh / scale) * (size_t)sw,
                         sw, bh / scale, scale);
        uint16_t *strip = (uint16_t *)dst.buf + (size_t)(dy + y) * (size_t)dw;
        size_t strip_len = (size_t)dw * (size_t)bh * 2u;
        ppa_srm_oper_config_t op = {
            .in = {
                .buffer = band,
                .pic_w = (uint32_t)out_w,
                .pic_h = (uint32_t)bh,
                .block_w = (uint32_t)out_w,
                .block_h = (uint32_t)bh,
                .block_offset_x = 0,
                .block_offset_y = 0,
                .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
            },
            .out = {
                .buffer = strip,
                .buffer_size = (uint32_t)strip_len,
                .pic_w = (uint32_t)dw,
                .pic_h = (uint32_t)bh,
                .block_offset_x = (uint32_t)dx,
                .block_offset_y = 0,
                .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
            },
            .rotation_angle = PPA_SRM_ROTATION_ANGLE_0,
            .scale_x = 1.0f,
            .scale_y = 1.0f,
            .mirror_x = false,
            .mirror_y = false,
            .rgb_swap = false,
            .byte_swap = false,
            .alpha_update_mode = PPA_ALPHA_NO_CHANGE,
            .mode = PPA_TRANS_MODE_NON_BLOCKING,
        };
        // srm_blit's cache contract, band-scoped: the driver invalidates the
        // whole out picture at submit, so any dirty CPU line in the strip
        // (window chrome beside the game content) must be written back first.
        esp_cache_msync(strip, strip_len,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M
                        | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
        s_submitted++;
        if (ppa_submit_srm(&op) != ESP_OK) {
            s_submitted--;
            ppa_wait(0);                        // fence what already flew
            return mp_const_false;              // caller repaints via the CPU
        }
    }
    if (!defer && !ppa_wait(0)) {
        return mp_const_false;                  // incomplete -> CPU repaint
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_blit_crisp_obj, 10, 10,
                                           moy_ppa_blit_crisp);


// fill(dst, dw, dh, x, y, w, h, rgb565) -> True when the PPA took it.
// Fills the (x, y, w, h) block of an RGB565 picture on the DMA engine, skipping
// the CPU cache's write-allocate read (see init). Blocking: the caller wants the
// pixels before it draws over them.
static mp_obj_t moy_ppa_fill(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    if (s_fill == NULL) {
        return mp_const_false;
    }
    mp_buffer_info_t dst;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(args[1]);
    mp_int_t dh = mp_obj_get_int(args[2]);
    mp_int_t x = mp_obj_get_int(args[3]);
    mp_int_t y = mp_obj_get_int(args[4]);
    mp_int_t w = mp_obj_get_int(args[5]);
    mp_int_t h = mp_obj_get_int(args[6]);
    uint32_t c565 = (uint32_t)(mp_obj_get_int(args[7]) & 0xFFFF);
    if (dw <= 0 || dh <= 0 || w <= 0 || h <= 0
        || x < 0 || y < 0 || x + w > dw || y + h > dh) {
        return mp_const_false;
    }
    // The IDF's fill validator requires the out-picture buffer's ADDRESS *and*
    // SIZE to be cache-line aligned, and rejects the op otherwise. Surfaces that
    // fail it (a window buffer / layer whose bytearray landed wherever the heap
    // had room) were reaching this and logging
    //   E ppa_fill: out.buffer addr or out.buffer_size not aligned
    // several times per gesture on glass (2026-07-26) -- the fill fell back to the
    // CPU correctly, but only after a pointless full-buffer writeback and a driver
    // error line. Decline here instead: the caller's CPU path is the same either
    // way, minus the noise. 64 is the P4's PSRAM cache line (internal SRAM's 32
    // divides it, so this is the conservative test for both).
    if (((uintptr_t)dst.buf & 63u) != 0 || (dst.len & 63u) != 0) {
        return mp_const_false;
    }
    // The PPA fill colour is ARGB8888; expand the RGB565 the console works in.
    uint32_t r = (c565 >> 11) & 0x1F, g = (c565 >> 5) & 0x3F, bch = c565 & 0x1F;
    color_pixel_argb8888_data_t argb = {
        .r = (uint8_t)((r << 3) | (r >> 2)),
        .g = (uint8_t)((g << 2) | (g >> 4)),
        .b = (uint8_t)((bch << 3) | (bch >> 2)),
        .a = 0xFF,
    };
    ppa_fill_oper_config_t op = {
        .out = {
            .buffer = dst.buf,
            .buffer_size = (uint32_t)dst.len,
            .pic_w = (uint32_t)dw,
            .pic_h = (uint32_t)dh,
            .block_offset_x = (uint32_t)x,
            .block_offset_y = (uint32_t)y,
            .fill_cm = PPA_FILL_COLOR_MODE_RGB565,
        },
        .fill_block_w = (uint32_t)w,
        .fill_block_h = (uint32_t)h,
        .fill_argb_color = argb,
        .mode = PPA_TRANS_MODE_BLOCKING,
    };
    // Same cache contract as the SRM path: the IDF driver INVALIDATES the whole
    // out-picture buffer at submit, so every dirty CPU line in it must be
    // written back first or this frame's CPU drawing is silently discarded. The
    // whole buffer, not just the block -- the invalidate is not block-scoped.
    esp_cache_msync(dst.buf, dst.len,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    if (ppa_do_fill(s_fill, &op) != ESP_OK) {
        return mp_const_false;
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_ppa_fill_obj, 8, 8, moy_ppa_fill);

static const mp_rom_map_elem_t moy_ppa_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ppa) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&moy_ppa_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&moy_ppa_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_scale), MP_ROM_PTR(&moy_ppa_blit_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill), MP_ROM_PTR(&moy_ppa_fill_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_async), MP_ROM_PTR(&moy_ppa_blit_async_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_crisp), MP_ROM_PTR(&moy_ppa_blit_crisp_obj) },
    { MP_ROM_QSTR(MP_QSTR_rotate), MP_ROM_PTR(&moy_ppa_rotate_obj) },
    { MP_ROM_QSTR(MP_QSTR_rotate_scale), MP_ROM_PTR(&moy_ppa_rotate_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_rotate_bounce), MP_ROM_PTR(&moy_ppa_rotate_bounce_obj) },
    { MP_ROM_QSTR(MP_QSTR_dma_copy), MP_ROM_PTR(&moy_ppa_dma_copy_obj) },
    { MP_ROM_QSTR(MP_QSTR_rotate_bounce_stats), MP_ROM_PTR(&moy_ppa_rotate_bounce_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_crisp_release), MP_ROM_PTR(&moy_ppa_crisp_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&moy_ppa_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_wait), MP_ROM_PTR(&moy_ppa_wait_obj) },
    { MP_ROM_QSTR(MP_QSTR_done), MP_ROM_PTR(&moy_ppa_done_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&moy_ppa_stats_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ppa_module_globals, moy_ppa_module_globals_table);

const mp_obj_module_t moy_ppa_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ppa_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ppa, moy_ppa_module);
