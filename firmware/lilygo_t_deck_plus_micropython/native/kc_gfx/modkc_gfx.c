// KidCode kc_gfx: VM-neutral RGB565 pixel kernel for the native compositor.
//
// Operates on caller-provided RGB565 buffers (bytearray/memoryview) with plain
// integer args -- no framebuf/LVGL/MicroPython-object dependency in the hot path,
// so the same C is reusable from a future Lua binding. Every op is fully
// bounds-clamped: a bad coordinate clips rather than overrunning the buffer.
//
// Used by modules/kc_compositor.py for fast clear/fill/blit and for packing
// dirty-region strips into the DMA buffer before lcd_bus.tx_color. See
// STAGE3_PLAN.md.

#include <string.h>
#include "py/obj.h"
#include "py/runtime.h"

static inline uint16_t *kc_gfx_buf_w(mp_obj_t obj, size_t *npix) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(obj, &bi, MP_BUFFER_WRITE);
    *npix = bi.len / 2u;
    return (uint16_t *)bi.buf;
}

static inline const uint16_t *kc_gfx_buf_r(mp_obj_t obj, size_t *npix) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(obj, &bi, MP_BUFFER_READ);
    *npix = bi.len / 2u;
    return (const uint16_t *)bi.buf;
}

// fill(buf, npix, color) -- set the first `npix` pixels (clamped to capacity).
static mp_obj_t kc_gfx_fill(mp_obj_t buf_obj, mp_obj_t npix_obj, mp_obj_t color_obj) {
    size_t cap;
    uint16_t *px = kc_gfx_buf_w(buf_obj, &cap);
    mp_int_t n = mp_obj_get_int(npix_obj);
    uint16_t c = (uint16_t)(mp_obj_get_int(color_obj) & 0xFFFF);
    if (n < 0) n = 0;
    if ((size_t)n > cap) n = (mp_int_t)cap;
    for (mp_int_t i = 0; i < n; i++) px[i] = c;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(kc_gfx_fill_obj, kc_gfx_fill);

// fill_rect(buf, stride_px, x, y, w, h, color) in an RGB565 buffer.
static mp_obj_t kc_gfx_fill_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *px = kc_gfx_buf_w(a[0], &cap);
    mp_int_t stride = mp_obj_get_int(a[1]);
    mp_int_t x = mp_obj_get_int(a[2]);
    mp_int_t y = mp_obj_get_int(a[3]);
    mp_int_t w = mp_obj_get_int(a[4]);
    mp_int_t h = mp_obj_get_int(a[5]);
    uint16_t c = (uint16_t)(mp_obj_get_int(a[6]) & 0xFFFF);
    if (stride <= 0) return mp_const_none;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x >= stride) return mp_const_none;
    if (x + w > stride) w = stride - x;
    if (w <= 0 || h <= 0) return mp_const_none;
    mp_int_t max_rows = (mp_int_t)(cap / (size_t)stride);
    if (y >= max_rows) return mp_const_none;
    if (y + h > max_rows) h = max_rows - y;
    for (mp_int_t row = 0; row < h; row++) {
        uint16_t *line = px + (size_t)(y + row) * (size_t)stride + (size_t)x;
        for (mp_int_t col = 0; col < w; col++) line[col] = c;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_gfx_fill_rect_obj, 7, 7, kc_gfx_fill_rect);

// blit565(dst, dw, dh, dx, dy, src, sw, sh, key[, cx0, cy0, cx1, cy1]) -- key=-1
// opaque, else skip source pixels equal to `key` (transparent color). The optional
// clip rect [cx0,cy0)..[cx1,cy1) (screen space, #11) further bounds the write region;
// when omitted it defaults to the full destination (cx0=cy0=0, cx1=dw, cy1=dh), so
// pre-#11 9-arg call sites are byte-for-byte unchanged.
static mp_obj_t kc_gfx_blit565(size_t n_args, const mp_obj_t *a) {
    size_t dcap, scap;
    uint16_t *dst = kc_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t dx = mp_obj_get_int(a[3]);
    mp_int_t dy = mp_obj_get_int(a[4]);
    const uint16_t *src = kc_gfx_buf_r(a[5], &scap);
    mp_int_t sw = mp_obj_get_int(a[6]);
    mp_int_t sh = mp_obj_get_int(a[7]);
    mp_int_t key = mp_obj_get_int(a[8]);
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return mp_const_none;
    if ((size_t)dw * (size_t)dh > dcap) dh = (mp_int_t)(dcap / (size_t)dw);
    if ((size_t)sw * (size_t)sh > scap) sh = (mp_int_t)(scap / (size_t)sw);
    // Clip rect, intersected with the buffer; defaults to the whole destination.
    mp_int_t cx0 = (n_args > 9) ? mp_obj_get_int(a[9]) : 0;
    mp_int_t cy0 = (n_args > 10) ? mp_obj_get_int(a[10]) : 0;
    mp_int_t cx1 = (n_args > 11) ? mp_obj_get_int(a[11]) : dw;
    mp_int_t cy1 = (n_args > 12) ? mp_obj_get_int(a[12]) : dh;
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    for (mp_int_t row = 0; row < sh; row++) {
        mp_int_t ty = dy + row;
        if (ty < cy0 || ty >= cy1) continue;
        const uint16_t *srow = src + (size_t)row * (size_t)sw;
        uint16_t *drow = dst + (size_t)ty * (size_t)dw;
        for (mp_int_t col = 0; col < sw; col++) {
            mp_int_t tx = dx + col;
            if (tx < cx0 || tx >= cx1) continue;
            uint16_t p = srow[col];
            if (key >= 0 && p == (uint16_t)key) continue;
            drow[tx] = p;
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_gfx_blit565_obj, 9, 13, kc_gfx_blit565);

// blit_map(dst, dw, dh, sx, sy, cells, map_w, map_h, mx, my, rw, rh,
//          atlas, ntiles, tile, scale, key[, cx0, cy0, cx1, cy1]) -- blit a (rw x rh)
// cell region of a tilemap in ONE C call (issue #32). `cells` is a byte grid where
// each cell holds `tile_id + 1` (0 = empty, skipped); a non-empty cell's tile is
// copied from the pre-converted RGB565 `atlas` (ntiles tiles of `tile`x`tile` pixels,
// tile-major, row-within-tile) into dst at screen (sx,sy), each source pixel expanded
// to a `scale` x `scale` block (so scale=2 => 16px tiles). Pixels equal to `key` are
// transparent. The optional clip rect [cx0,cy0)..[cx1,cy1) (#11) bounds the write
// region; omitted -> full destination, so pre-#11 17-arg calls are unchanged.
static mp_obj_t kc_gfx_blit_map(size_t n_args, const mp_obj_t *a) {
    size_t dcap, ccap, acap;
    uint16_t *dst = kc_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t sx = mp_obj_get_int(a[3]);
    mp_int_t sy = mp_obj_get_int(a[4]);
    mp_buffer_info_t cbi;
    mp_get_buffer_raise(a[5], &cbi, MP_BUFFER_READ);
    const uint8_t *cells = (const uint8_t *)cbi.buf;
    ccap = cbi.len;                              // cells is a byte grid (1 byte/cell)
    mp_int_t map_w = mp_obj_get_int(a[6]);
    mp_int_t map_h = mp_obj_get_int(a[7]);
    mp_int_t mx = mp_obj_get_int(a[8]);
    mp_int_t my = mp_obj_get_int(a[9]);
    mp_int_t rw = mp_obj_get_int(a[10]);
    mp_int_t rh = mp_obj_get_int(a[11]);
    const uint16_t *atlas = kc_gfx_buf_r(a[12], &acap);
    mp_int_t ntiles = mp_obj_get_int(a[13]);
    mp_int_t tile = mp_obj_get_int(a[14]);
    mp_int_t scale = mp_obj_get_int(a[15]);
    mp_int_t key = mp_obj_get_int(a[16]);
    if (dw <= 0 || dh <= 0 || map_w <= 0 || map_h <= 0 || tile <= 0 || ntiles <= 0)
        return mp_const_none;
    if (scale < 1) scale = 1;
    // Clip rect, intersected with the buffer; defaults to the whole destination.
    mp_int_t cx0 = (n_args > 17) ? mp_obj_get_int(a[17]) : 0;
    mp_int_t cy0 = (n_args > 18) ? mp_obj_get_int(a[18]) : 0;
    mp_int_t cx1 = (n_args > 19) ? mp_obj_get_int(a[19]) : dw;
    mp_int_t cy1 = (n_args > 20) ? mp_obj_get_int(a[20]) : dh;
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    mp_int_t tpx = tile * tile;                  // pixels per atlas tile
    if ((size_t)ntiles * (size_t)tpx > acap) return mp_const_none;  // atlas too small
    mp_int_t step = tile * scale;                // on-screen size of one cell
    for (mp_int_t cy = 0; cy < rh; cy++) {
        mp_int_t myy = my + cy;
        if (myy < 0 || myy >= map_h) continue;
        mp_int_t dy0 = sy + cy * step;
        for (mp_int_t cx = 0; cx < rw; cx++) {
            mp_int_t mxx = mx + cx;
            if (mxx < 0 || mxx >= map_w) continue;
            size_t ci = (size_t)myy * (size_t)map_w + (size_t)mxx;
            if (ci >= ccap) continue;
            mp_int_t v = cells[ci];
            if (v == 0) continue;                // empty cell
            mp_int_t tid = v - 1;
            if (tid >= ntiles) continue;
            const uint16_t *tsrc = atlas + (size_t)tid * (size_t)tpx;
            mp_int_t dx0 = sx + cx * step;
            // expand the tile's tile x tile pixels by `scale`, clip-bounded.
            for (mp_int_t row = 0; row < tile; row++) {
                const uint16_t *srow = tsrc + (size_t)row * (size_t)tile;
                for (mp_int_t sub_y = 0; sub_y < scale; sub_y++) {
                    mp_int_t ty = dy0 + row * scale + sub_y;
                    if (ty < cy0 || ty >= cy1) continue;
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (mp_int_t col = 0; col < tile; col++) {
                        uint16_t p = srow[col];
                        if (key >= 0 && p == (uint16_t)key) continue;
                        mp_int_t bx = dx0 + col * scale;
                        for (mp_int_t sub_x = 0; sub_x < scale; sub_x++) {
                            mp_int_t tx = bx + sub_x;
                            if (tx < cx0 || tx >= cx1) continue;
                            drow[tx] = p;
                        }
                    }
                }
            }
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_gfx_blit_map_obj, 17, 21, kc_gfx_blit_map);

// pack_strip(fb, fb_w, x, y, w, rows, dst) -- copy a (w x rows) window of the
// framebuffer into dst contiguously (row-major). Full-width is one memcpy;
// cropped rects are packed row-by-row in C (the slow Stage 2 Python path).
static mp_obj_t kc_gfx_pack_strip(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t fbcap, dcap;
    const uint16_t *fb = kc_gfx_buf_r(a[0], &fbcap);
    mp_int_t fb_w = mp_obj_get_int(a[1]);
    mp_int_t x = mp_obj_get_int(a[2]);
    mp_int_t y = mp_obj_get_int(a[3]);
    mp_int_t w = mp_obj_get_int(a[4]);
    mp_int_t rows = mp_obj_get_int(a[5]);
    uint16_t *dst = kc_gfx_buf_w(a[6], &dcap);
    if (fb_w <= 0 || w <= 0 || rows <= 0 || x < 0 || y < 0) return mp_const_none;
    if (x + w > fb_w) return mp_const_none;
    if ((size_t)(y + rows) * (size_t)fb_w > fbcap) return mp_const_none;
    if ((size_t)w * (size_t)rows > dcap) return mp_const_none;
    if (x == 0 && w == fb_w) {
        memcpy(dst, fb + (size_t)y * (size_t)fb_w, (size_t)w * (size_t)rows * 2u);
    } else {
        for (mp_int_t row = 0; row < rows; row++) {
            memcpy(dst + (size_t)row * (size_t)w,
                   fb + (size_t)(y + row) * (size_t)fb_w + (size_t)x,
                   (size_t)w * 2u);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kc_gfx_pack_strip_obj, 7, 7, kc_gfx_pack_strip);

static const mp_rom_map_elem_t kc_gfx_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_OBJ_NEW_QSTR(MP_QSTR_kc_gfx) },
    { MP_ROM_QSTR(MP_QSTR_fill),       MP_ROM_PTR(&kc_gfx_fill_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_rect),  MP_ROM_PTR(&kc_gfx_fill_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit565),    MP_ROM_PTR(&kc_gfx_blit565_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_map),   MP_ROM_PTR(&kc_gfx_blit_map_obj) },
    { MP_ROM_QSTR(MP_QSTR_pack_strip), MP_ROM_PTR(&kc_gfx_pack_strip_obj) },
};
static MP_DEFINE_CONST_DICT(kc_gfx_globals, kc_gfx_globals_table);

const mp_obj_module_t mp_module_kc_gfx = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&kc_gfx_globals,
};

MP_REGISTER_MODULE(MP_QSTR_kc_gfx, mp_module_kc_gfx);
