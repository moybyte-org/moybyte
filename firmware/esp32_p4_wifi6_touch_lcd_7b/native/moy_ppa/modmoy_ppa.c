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

static ppa_client_handle_t s_srm = NULL;

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
    s_submitted = 0;
    s_done = 0;
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_ppa_init_obj, moy_ppa_init);

static mp_obj_t moy_ppa_deinit(void) {
    if (s_srm != NULL) {
        ppa_unregister_client(s_srm);
        s_srm = NULL;
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

static const mp_rom_map_elem_t moy_ppa_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_ppa) },
    { MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&moy_ppa_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&moy_ppa_deinit_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_scale), MP_ROM_PTR(&moy_ppa_blit_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_async), MP_ROM_PTR(&moy_ppa_blit_async_obj) },
    { MP_ROM_QSTR(MP_QSTR_sync), MP_ROM_PTR(&moy_ppa_sync_obj) },
};
static MP_DEFINE_CONST_DICT(moy_ppa_module_globals, moy_ppa_module_globals_table);

const mp_obj_module_t moy_ppa_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_ppa_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_ppa, moy_ppa_module);
