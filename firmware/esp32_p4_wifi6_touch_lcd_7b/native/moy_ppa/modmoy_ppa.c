// moy_ppa: ESP32-P4 Pixel-Processing Accelerator (PPA) bindings (#58).
//
// The P4 has a dedicated 2D DMA engine (the PPA) that scales/rotates/mirrors,
// blends (alpha + color-key) and fills image blocks between PSRAM buffers with
// ZERO CPU involvement -- exactly the write-bound composite ops that dominate
// the desktop frame (game->window scale blit, wallpaper cover-crop, fills) and
// that the CPU moy_gfx kernel pays PSRAM-bandwidth-bound against the continuous
// DSI scan-out. This module is the thin MicroPython surface over the ESP-IDF
// esp_driver_ppa; the driver owns all cache management (it writes back the
// input window and invalidates the output window itself), so the caller only
// has to hand it a cache-aligned OUTPUT buffer -- which the DSI framebuffer
// from esp_lcd_dpi_panel_get_frame_buffer already is.
//
// SRM = Scale-Rotate-Mirror. blit_scale() is the integer-upscale composite that
// mirrors moy_gfx.blit565_scale so run_ppa_smoke can A/B them on glass.

#include "py/obj.h"
#include "py/runtime.h"

#include "driver/ppa.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"

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

static bool ppa_trans_done_cb(ppa_client_handle_t client,
                              ppa_event_data_t *edata, void *user_data) {
    s_done++;
    return false;   // no higher-priority task to wake
}

// init() -> True once the SRM client is registered (idempotent). False on
// failure (no PPA / OOM), so the caller can fall back to the CPU kernel.
static mp_obj_t moy_ppa_init(void) {
    if (s_srm != NULL) {
        return mp_const_true;
    }
    ppa_client_config_t cfg = {
        .oper_type = PPA_OPERATION_SRM,
        // A few pending slots: enough for the composite-overlap lever (double
        // buffer + slack). Sprite BATCHING via the queue was measured a dead end
        // -- 64x 16x16 queued = 4.57ms vs 0.70ms for the CPU (~10x vs spr_batch);
        // per-op submit overhead dwarfs a tiny blit. The PPA is a SCALE
        // accelerator (the upscale composite), not a sprite compositor.
        .max_pending_trans_num = 3,
    };
    esp_err_t err = ppa_register_client(&cfg, &s_srm);
    if (err != ESP_OK) {
        s_srm = NULL;
        return mp_const_false;
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

static mp_obj_t moy_ppa_deinit(void) {
    crisp_free_bands();
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
            .buffer = dst.buf,
            .buffer_size = (uint32_t)dst.len,
            .pic_w = (uint32_t)dw,
            .pic_h = (uint32_t)dh,
            .block_offset_x = (uint32_t)dx,
            .block_offset_y = (uint32_t)dy,
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
    // IDF driver INVALIDATES the whole out-picture buffer at submit, which
    // otherwise DISCARDS every not-yet-flushed CPU write of the current frame
    // (glass-confirmed 2026-07-10: drag frames draw strips/chrome/bar/cursor by
    // CPU and then kick the deferred window stamp -- those writes vanished and
    // the pixels reverted two frames, leaving speed-scaled desktop trails). The
    // quiet-game composite never hit this because nothing else CPU-draws on
    // those frames. C2M writeback of a 1.2MB range costs well under a ms.
    esp_cache_msync(dst.buf, dst.len,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    s_submitted++;
    esp_err_t err = ppa_do_scale_rotate_mirror(s_srm, &op);
    if (err != ESP_OK) {
        s_submitted--;   // no transaction queued -> no done callback will fire
        mp_raise_msg_varg(&mp_type_OSError,
                          MP_ERROR_TEXT("ppa srm failed: %d"), (int)err);
    }
    return mp_const_none;
}

// sync(): block until every submitted transaction has completed (the fence for a
// non-blocking composite). The PPA DMA runs on its own; this is a short busy-wait
// only reached when the caller deliberately overlaps then fences.
static mp_obj_t moy_ppa_sync(void) {
    while (s_done != s_submitted) {
        // volatile reload each iteration; the done ISR advances s_done
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_sync_obj, moy_ppa_sync);

// done() -> bool: True when every submitted transaction has completed -- the
// NON-BLOCKING probe of sync()'s fence (the triple-framebuffer present checks
// this per loop and shows the pending frame only once its DMA landed, instead
// of spinning; a caller that must wait still uses sync()).
static mp_obj_t moy_ppa_done(void) {
    return mp_obj_new_bool(s_done == s_submitted);
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
    while (s_done != s_submitted) { }
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
        // bands ago -- wait until at most ONE transaction is in flight.
        while ((uint32_t)(s_submitted - s_done) > 1) { }
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
        if (ppa_do_scale_rotate_mirror(s_srm, &op) != ESP_OK) {
            s_submitted--;
            while (s_done != s_submitted) { }   // fence what already flew
            return mp_const_false;              // caller repaints via the CPU
        }
    }
    if (!defer) {
        while (s_done != s_submitted) { }
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
    { MP_ROM_QSTR(MP_QSTR_crisp_release), MP_ROM_PTR(&moy_ppa_crisp_release_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&moy_ppa_sync_obj) },
    { MP_ROM_QSTR(MP_QSTR_done), MP_ROM_PTR(&moy_ppa_done_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ppa_module_globals, moy_ppa_module_globals_table);

const mp_obj_module_t moy_ppa_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ppa_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ppa, moy_ppa_module);
