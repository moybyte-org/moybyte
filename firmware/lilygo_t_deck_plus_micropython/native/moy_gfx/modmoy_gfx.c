// Moybyte moy_gfx: VM-neutral RGB565 pixel kernel for the native compositor.
//
// Operates on caller-provided RGB565 buffers (bytearray/memoryview) with plain
// integer args -- no framebuf/LVGL/MicroPython-object dependency in the hot path,
// so the same C is reusable from a future Lua binding. Every op is fully
// bounds-clamped: a bad coordinate clips rather than overrunning the buffer.
//
// Used by modules/moy_compositor.py for fast clear/fill/blit and for packing
// dirty-region strips into the DMA buffer before lcd_bus.tx_color. See
// STAGE3_PLAN.md.

#include <string.h>
#include <math.h>
#include "py/obj.h"
#include "py/runtime.h"
#include "py/mphal.h"        // mp_hal_ticks_us -- the draw gates' DRAW2 timers

// #77: build the pixel kernel at -O3 (the ports default to -O2). In-source
// pragma, NOT cmake: source-file properties are directory-scoped and the linked
// object is compiled by the micropython.elf target, which never sees them
// (verified via build.ninja -- the cmake route left the linked object at -O2).
// -O3 not -Ofast: -ffast-math would perturb the one sqrt() in circ. Measured
// NULL on the P4 (render slice is MP dispatch, not C compute); kept as the S3
// experiment -- its slower PSRAM (120MHz OCT vs 200MHz HEX) and smaller flash
// cache make the compute/bandwidth split land differently there.
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O3")
#endif

// Async layer copy (#54 Stage 2 / #63): GDMA-driven PSRAM->PSRAM memcpy so the
// per-frame draw_layer background restore (~7ms CPU for a full screen) can run
// WHILE the cart's _update executes. Guarded so this file stays VM-neutral: the
// unix-port build (tools/bench_unix_mp.py) has no esp_async_memcpy.h and simply
// doesn't export copy_async/copy_wait -- Python falls back to the sync path.
#if defined(__has_include)
#if __has_include("esp_async_memcpy.h")
#include "esp_async_memcpy.h"
#define MOY_GFX_HAS_ASYNC_COPY 1
#endif
#endif

static inline uint16_t *moy_gfx_buf_w(mp_obj_t obj, size_t *npix) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(obj, &bi, MP_BUFFER_WRITE);
    *npix = bi.len / 2u;
    return (uint16_t *)bi.buf;
}

static inline const uint16_t *moy_gfx_buf_r(mp_obj_t obj, size_t *npix) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(obj, &bi, MP_BUFFER_READ);
    *npix = bi.len / 2u;
    return (const uint16_t *)bi.buf;
}

// A run of `n` RGB565 pixels set to `c`, written 32 bits at a time.
//
// WHY (measured on P4 glass 2026-07-26): a full-screen 1.2MB fill through the
// naive uint16 loop took 18.8ms -- while memcpy moved TWICE the bytes (a 1.2MB
// read plus a 1.2MB write) in 26.9ms, i.e. ~40% less time per byte. A copy
// cannot beat a fill on a memory-bound path, so the loop was the bottleneck,
// not the bus: one 16-bit store per pixel leaves half of every 32-bit bus beat
// unused and pays full loop overhead per pixel. Pairing the pixels into 32-bit
// stores and unrolling by four fixes both. (RV32 has no wider scalar store, so
// 32 bits is the ceiling here.)
static inline void moy_gfx_fill_run(uint16_t *px, size_t n, uint16_t c) {
    if (n == 0) return;
    // Align to a 4-byte boundary so the 32-bit stores never straddle one.
    if (((uintptr_t)px & 2u) != 0) {
        *px++ = c;
        n--;
    }
    uint32_t c2 = ((uint32_t)c << 16) | c;
    uint32_t *w = (uint32_t *)px;
    size_t pairs = n >> 1;
    while (pairs >= 4) {
        w[0] = c2; w[1] = c2; w[2] = c2; w[3] = c2;
        w += 4;
        pairs -= 4;
    }
    while (pairs--) *w++ = c2;
    if (n & 1) *((uint16_t *)w) = c;
}

// fill(buf, npix, color) -- set the first `npix` pixels (clamped to capacity).
static mp_obj_t moy_gfx_fill(mp_obj_t buf_obj, mp_obj_t npix_obj, mp_obj_t color_obj) {
    size_t cap;
    uint16_t *px = moy_gfx_buf_w(buf_obj, &cap);
    mp_int_t n = mp_obj_get_int(npix_obj);
    uint16_t c = (uint16_t)(mp_obj_get_int(color_obj) & 0xFFFF);
    if (n < 0) n = 0;
    if ((size_t)n > cap) n = (mp_int_t)cap;
    moy_gfx_fill_run(px, (size_t)n, c);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_gfx_fill_obj, moy_gfx_fill);

// fill_rect(buf, stride_px, x, y, w, h, color) in an RGB565 buffer.
static mp_obj_t moy_gfx_fill_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *px = moy_gfx_buf_w(a[0], &cap);
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
        moy_gfx_fill_run(px + (size_t)(y + row) * (size_t)stride + (size_t)x,
                         (size_t)w, c);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_fill_rect_obj, 7, 7, moy_gfx_fill_rect);

// blit565(dst, dw, dh, dx, dy, src, sw, sh, key[, cx0, cy0, cx1, cy1]) -- key=-1
// opaque, else skip source pixels equal to `key` (transparent color). The optional
// clip rect [cx0,cy0)..[cx1,cy1) (screen space, #11) further bounds the write region;
// when omitted it defaults to the full destination (cx0=cy0=0, cx1=dw, cy1=dh), so
// pre-#11 9-arg call sites are byte-for-byte unchanged.
static mp_obj_t moy_gfx_blit565(size_t n_args, const mp_obj_t *a) {
    size_t dcap, scap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t dx = mp_obj_get_int(a[3]);
    mp_int_t dy = mp_obj_get_int(a[4]);
    const uint16_t *src = moy_gfx_buf_r(a[5], &scap);
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
        if (key < 0) {
            // OPAQUE fast lane (#66 CHROMEBRK): no colorkey test means the row's
            // clipped span is one contiguous copy -- memcpy instead of the
            // per-pixel loop. Matters for blit_strip (the cached top bar stamps
            // a 320x18 strip every cart frame) and the paint-image bakes.
            mp_int_t s0 = (dx < cx0) ? (cx0 - dx) : 0;
            mp_int_t s1 = (dx + sw > cx1) ? (cx1 - dx) : sw;
            if (s1 > s0) {
                memcpy(drow + dx + s0, srow + s0, (size_t)(s1 - s0) * 2u);
            }
            continue;
        }
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
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit565_obj, 9, 13, moy_gfx_blit565);

// blit565_scale(dst, dw, dh, dx, dy, src, sw, sh, scale) -- integer-upscale an
// RGB565 source into dst with its top-left at (dx, dy). dx/dy may be NEGATIVE
// (the cover-crop wallpaper backdrop overhangs the desktop); writes clamp to the
// destination. Opaque, no colorkey. This is the P4 windowed composite primitive
// (#58/#73): the 320x240 game frame -> its window viewport, and the wallpaper
// frame -> the full-desktop cover backdrop, each in ONE C call (the host does
// these with Python index-buffer loops; an RGB565 device canvas has no index
// buffer, and a per-frame Python expansion of ~600k pixels is unusable). Each
// expanded source row is built once, then memcpy'd to its scale-1 duplicates.
static mp_obj_t moy_gfx_blit565_scale(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap, scap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t dx = mp_obj_get_int(a[3]);
    mp_int_t dy = mp_obj_get_int(a[4]);
    const uint16_t *src = moy_gfx_buf_r(a[5], &scap);
    mp_int_t sw = mp_obj_get_int(a[6]);
    mp_int_t sh = mp_obj_get_int(a[7]);
    mp_int_t scale = mp_obj_get_int(a[8]);
    if (scale < 1) scale = 1;
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return mp_const_none;
    if ((size_t)dw * (size_t)dh > dcap) dh = (mp_int_t)(dcap / (size_t)dw);
    if ((size_t)sw * (size_t)sh > scap) sh = (mp_int_t)(scap / (size_t)sw);
    // Visible destination-x span of the scaled image, clamped to the buffer.
    mp_int_t x0 = dx < 0 ? 0 : dx;
    mp_int_t x1 = dx + sw * scale;
    if (x1 > dw) x1 = dw;
    if (x1 <= x0) return mp_const_none;
    for (mp_int_t row = 0; row < sh; row++) {
        mp_int_t ty0 = dy + row * scale;         // first dst row of this src row
        mp_int_t ty1 = ty0 + scale;              // one past its last dst row
        if (ty1 <= 0 || ty0 >= dh) continue;
        if (ty1 > dh) ty1 = dh;
        const uint16_t *srow = src + (size_t)row * (size_t)sw;
        mp_int_t wy = ty0 < 0 ? 0 : ty0;         // first VISIBLE dst row
        uint16_t *first = dst + (size_t)wy * (size_t)dw;
        // Expand the source row once into the first visible dst row (run-length
        // stepped: no per-pixel division)...
        mp_int_t off = x0 - dx;                  // >= 0 by construction
        const uint16_t *sp = srow + off / scale;
        mp_int_t rep = scale - (off % scale);    // copies left of the first col
        uint16_t *out = first + x0;
        mp_int_t remaining = x1 - x0;
        while (remaining > 0) {
            uint16_t v = *sp++;
            mp_int_t n = rep < remaining ? rep : remaining;
            for (mp_int_t i = 0; i < n; i++) out[i] = v;
            out += n;
            remaining -= n;
            rep = scale;
        }
        // ...then duplicate it to the band's remaining visible rows.
        for (mp_int_t ty = wy + 1; ty < ty1; ty++) {
            memcpy(dst + (size_t)ty * (size_t)dw + x0, first + x0,
                   (size_t)(x1 - x0) * 2u);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit565_scale_obj, 9, 9, moy_gfx_blit565_scale);

// blit_map(dst, dw, dh, sx, sy, cells, map_w, map_h, mx, my, rw, rh,
//          atlas, ntiles, tile, scale, key[, cx0, cy0, cx1, cy1]) -- blit a (rw x rh)
// cell region of a tilemap in ONE C call (issue #32). `cells` is a byte grid where
// each cell holds `tile_id + 1` (0 = empty, skipped); a non-empty cell's tile is
// copied from the pre-converted RGB565 `atlas` (ntiles tiles of `tile`x`tile` pixels,
// tile-major, row-within-tile) into dst at screen (sx,sy), each source pixel expanded
// to a `scale` x `scale` block (so scale=2 => 16px tiles). Pixels equal to `key` are
// transparent. The optional clip rect [cx0,cy0)..[cx1,cy1) (#11) bounds the write
// region; omitted -> full destination, so pre-#11 17-arg calls are unchanged.
static mp_obj_t moy_gfx_blit_map(size_t n_args, const mp_obj_t *a) {
    size_t dcap, ccap, acap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
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
    const uint16_t *atlas = moy_gfx_buf_r(a[12], &acap);
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
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_map_obj, 17, 21, moy_gfx_blit_map);

// blit_batch(dst, dw, dh, items, atlas, ntiles, tile, scale, key,
//            cam_x, cam_y, cx0, cy0, cx1, cy1) -- draw N sprite tiles in ONE C call
// (issue #43): the sprite analogue of blit_map (#32). `items` is a list/tuple of
// (tile, x, y) or (tile, x, y, flip) tuples; each tile is copied from the
// pre-converted RGB565 `atlas` (ntiles tiles of `tile`x`tile` pixels, tile-major,
// row-within-tile -- exactly what _sheet_atlas bakes, the SAME atlas map() uses)
// into dst at world (x,y) minus the camera, each source pixel expanded to a
// `scale` x `scale` block. Pixels equal to `key` are transparent. `flip` mirrors
// per item (TIC-80: 0=none, 1=h, 2=v, 3=both) -- the one thing blit_map has no need
// of, so we read the source pixel from the mirrored tile-local index but write it
// to the un-mirrored block. The clip rect [cx0,cy0)..[cx1,cy1) (#11) bounds the
// write region, intersected with the buffer. Collapses N per-sprite MicroPython->C
// blit565 calls into one walk, which is the device's draw-call bottleneck.
//
// ARRAY MODE (#63 spr_gate): `items` may instead be the canvas batch array -- an
// array('h') laid out [next, colorkey, scale, token, (tile x y flip)*N...] with
// items starting at index 4 and `next` = 4 + 4*N. This is the queue the spr_gate
// fast path fills from C with ZERO Python-object churn; passing it here draws the
// whole run without ever materialising tuples. Detected via the buffer protocol
// (a list/tuple of tuples has no buffer -> classic mode).
static mp_obj_t moy_gfx_blit_batch(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap, acap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    size_t n_items;
    mp_obj_t *item_arr = NULL;
    const int16_t *qitems = NULL;          // array-mode int16 quads (or NULL)
    mp_buffer_info_t qbi;
    if (mp_get_buffer(a[3], &qbi, MP_BUFFER_READ) && qbi.len >= 2 * 4) {
        const int16_t *q = (const int16_t *)qbi.buf;
        size_t qlen = qbi.len / 2u;
        mp_int_t next = q[0];
        if (next < 4) next = 4;
        if ((size_t)next > qlen) next = (mp_int_t)qlen;
        n_items = (size_t)(next - 4) / 4u;
        qitems = q + 4;
    } else {
        mp_obj_get_array(a[3], &n_items, &item_arr);  // list or tuple of item tuples
    }
    const uint16_t *atlas = moy_gfx_buf_r(a[4], &acap);
    mp_int_t ntiles = mp_obj_get_int(a[5]);
    mp_int_t tile = mp_obj_get_int(a[6]);
    mp_int_t scale = mp_obj_get_int(a[7]);
    mp_int_t key = mp_obj_get_int(a[8]);
    mp_int_t cam_x = mp_obj_get_int(a[9]);
    mp_int_t cam_y = mp_obj_get_int(a[10]);
    if (dw <= 0 || dh <= 0 || tile <= 0 || ntiles <= 0) return mp_const_none;
    if (scale < 1) scale = 1;
    // Clip rect, intersected with the buffer.
    mp_int_t cx0 = mp_obj_get_int(a[11]);
    mp_int_t cy0 = mp_obj_get_int(a[12]);
    mp_int_t cx1 = mp_obj_get_int(a[13]);
    mp_int_t cy1 = mp_obj_get_int(a[14]);
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    mp_int_t tpx = tile * tile;                  // pixels per atlas tile
    if ((size_t)ntiles * (size_t)tpx > acap) return mp_const_none;  // atlas too small
    for (size_t i = 0; i < n_items; i++) {
        mp_int_t tid, dx0, dy0, flip;
        if (qitems != NULL) {
            const int16_t *it = qitems + i * 4u;   // (tile, x, y, flip) int16 quad
            tid = it[0];
            dx0 = (mp_int_t)it[1] - cam_x;
            dy0 = (mp_int_t)it[2] - cam_y;
            flip = it[3];
        } else {
            size_t ilen;
            mp_obj_t *ielem;
            mp_obj_get_array(item_arr[i], &ilen, &ielem);  // (tile, x, y[, flip])
            if (ilen < 3) continue;
            tid = mp_obj_get_int(ielem[0]);
            dx0 = mp_obj_get_int(ielem[1]) - cam_x;
            dy0 = mp_obj_get_int(ielem[2]) - cam_y;
            flip = (ilen > 3) ? mp_obj_get_int(ielem[3]) : 0;
        }
        if (tid < 0 || tid >= ntiles) continue;
        mp_int_t fx = flip & 1;
        mp_int_t fy = (flip >> 1) & 1;
        const uint16_t *tsrc = atlas + (size_t)tid * (size_t)tpx;
        // expand the tile's tile x tile pixels by `scale`, clip-bounded, mirroring
        // the SOURCE read per flip but writing to the un-mirrored block (like spr).
        for (mp_int_t row = 0; row < tile; row++) {
            mp_int_t ssy = fy ? (tile - 1 - row) : row;
            const uint16_t *srow = tsrc + (size_t)ssy * (size_t)tile;
            for (mp_int_t sub_y = 0; sub_y < scale; sub_y++) {
                mp_int_t ty = dy0 + row * scale + sub_y;
                if (ty < cy0 || ty >= cy1) continue;
                uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                for (mp_int_t col = 0; col < tile; col++) {
                    mp_int_t ssx = fx ? (tile - 1 - col) : col;
                    uint16_t p = srow[ssx];
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
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_batch_obj, 15, 15, moy_gfx_blit_batch);

// --- spr_gate: the kid-facing native spr() fast path (#63) -------------------
//
// WHY THIS EXISTS (measured on ESP32-S3, warm 1MB heap): a MicroPython function
// whose call frame exceeds ~11 machine words (params + locals + VM stack) heap-
// allocates that frame on EVERY call, and finding a contiguous multi-block hole
// in a warm, fragmented gc heap costs ~1.5ms PER CALL. The kid API's 8-param
// `spr` closure and `spr_tile` both spill, so a kid's innocent
// `for e in enemies: spr(...)` loop over 120 objects costs ~150ms/frame once the
// heap warms up (sakura's 29->12fps collapse). A native callable has NO Python
// frame at all: the same loop lands here at ~2-5us per call, allocation-free.
//
// The gate wraps ONE cart sheet + the canvas batch array (array('h'):
// [next, colorkey, scale, token, (tile x y flip)*N]). The hot path parses
// (n, x, y[, colorkey[, scale[, flip]]]) -- ints or floats -- clamps to int16
// and appends a quad; blit_batch (array mode) later draws the whole run in one
// call. Anything unusual (kwargs, w/h spans, an Image `n`, a weird type) is
// delegated verbatim to `fallback`, the original Python spr closure, so
// semantics are IDENTICAL -- this is purely the fast lane. Run state breaks
// (first item, colorkey/scale change, another writer's token, full queue) call
// the canvas's Python begin_batch, which flushes any pending run and
// re-registers -- rare, so its cost is irrelevant.
typedef struct _moy_gfx_spr_gate_obj_t {
    mp_obj_base_t base;
    mp_obj_t canvas;     // DeviceCanvas (begin_batch/flush_batch on rare paths)
    mp_obj_t sheet;      // the cart's SpriteSheet (registered on run start)
    mp_obj_t arr;        // the batch array('h') -- held so gc keeps the buffer
    mp_obj_t fallback;   // the Python spr closure (full semantics)
    int16_t *q;          // arr's int16 data (stable: MicroPython gc never moves)
    size_t qlen;         // arr length in int16 elements
    mp_int_t token;      // unique per-gate run tag, mirrored in q[3]
} moy_gfx_spr_gate_obj_t;

static mp_obj_t spr_gate_call(mp_obj_t self_in, size_t n_args, size_t n_kw,
                              const mp_obj_t *args) {
    moy_gfx_spr_gate_obj_t *g = MP_OBJ_TO_PTR(self_in);
    if (n_kw != 0 || n_args < 3 || n_args > 6) {
        // kwargs / w,h span args -> full Python semantics
        return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
    }
    mp_int_t v[6] = {0, 0, 0, -1, 1, 0};   // n, x, y, colorkey, scale, flip
    for (size_t i = 0; i < n_args; i++) {
        mp_obj_t o = args[i];
        if (mp_obj_is_small_int(o)) {
            v[i] = MP_OBJ_SMALL_INT_VALUE(o);
        } else if (mp_obj_is_float(o)) {
            v[i] = (mp_int_t)mp_obj_get_float(o);   // kid float coords: truncate
        } else {
            // an Image (ASCII-art sprite) or anything exotic -> Python path
            return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
        }
    }
    int16_t *q = g->q;
    mp_int_t k = q[0];
    if (k < 4) k = 4;
    if (k == 4 || (size_t)(k + 4) > g->qlen
        || q[3] != (int16_t)g->token
        || q[1] != (int16_t)v[3] || q[2] != (int16_t)v[4]) {
        // run break (first item / state change / foreign run / full queue):
        // canvas.begin_batch flushes any pending run and registers this one.
        mp_obj_t dest[2 + 4];
        mp_load_method(g->canvas, MP_QSTR_begin_batch, dest);
        dest[2] = g->sheet;
        dest[3] = MP_OBJ_NEW_SMALL_INT(v[3]);
        dest[4] = MP_OBJ_NEW_SMALL_INT(v[4]);
        dest[5] = MP_OBJ_NEW_SMALL_INT(g->token);
        mp_call_method_n_kw(4, 0, dest);
        k = q[0];
        if (k < 4 || (size_t)(k + 4) > g->qlen) {
            return mp_const_none;          // defensive: queue unusable, drop
        }
    }
    // clamp to int16: an off-screen coord clamps to a still-off-screen value
    // (blit_batch clips), an out-of-range tile id becomes invalid (skipped).
    mp_int_t tid = v[0];
    if (tid < -32768 || tid > 32767) tid = -1;
    mp_int_t x = v[1];
    if (x < -32768) x = -32768; else if (x > 32767) x = 32767;
    mp_int_t y = v[2];
    if (y < -32768) y = -32768; else if (y > 32767) y = 32767;
    q[k] = (int16_t)tid;
    q[k + 1] = (int16_t)x;
    q[k + 2] = (int16_t)y;
    q[k + 3] = (int16_t)(v[5] & 3);
    q[0] = (int16_t)(k + 4);
    return mp_const_none;
}

static MP_DEFINE_CONST_OBJ_TYPE(
    moy_gfx_spr_gate_type,
    MP_QSTR_spr_gate,
    MP_TYPE_FLAG_NONE,
    call, spr_gate_call
);

// make_spr_gate(canvas, sheet, arr, token, fallback) -> spr_gate callable.
static mp_obj_t moy_gfx_make_spr_gate(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[2], &bi, MP_BUFFER_RW);
    if (bi.len < 2 * 8) {
        mp_raise_ValueError(MP_ERROR_TEXT("batch array too small"));
    }
    moy_gfx_spr_gate_obj_t *g = mp_obj_malloc(moy_gfx_spr_gate_obj_t,
                                              &moy_gfx_spr_gate_type);
    g->canvas = a[0];
    g->sheet = a[1];
    g->arr = a[2];
    g->fallback = a[4];
    g->q = (int16_t *)bi.buf;
    g->qlen = bi.len / 2u;
    g->token = mp_obj_get_int(a[3]);
    return MP_OBJ_FROM_PTR(g);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_make_spr_gate_obj, 5, 5,
                                           moy_gfx_make_spr_gate);

#ifdef MOY_GFX_HAS_ASYNC_COPY
// --- GDMA async copy (#54 Stage 2 / #63) -------------------------------------
// copy_async(dst, dst_off_px, src, src_off_px, npix) -> True if the DMA copy
// started (False -> caller must do the sync copy). copy_wait() blocks until the
// in-flight copy completes. One copy in flight at a time (the layer restore);
// the driver is installed lazily on first use and kept for the session.
static async_memcpy_handle_t moy_gfx_mcp = NULL;
static volatile int moy_gfx_copy_busy = 0;

static bool moy_gfx_copy_done_cb(async_memcpy_handle_t h,
                                 async_memcpy_event_t *e, void *arg) {
    (void)h; (void)e; (void)arg;
    moy_gfx_copy_busy = 0;
    return false;                       // ISR context: no ctx switch needed
}

static mp_obj_t moy_gfx_copy_wait(void) {
    // Spin on the ISR-set flag. A full-frame PSRAM copy is ~1-2ms of GDMA time;
    // when the copy overlapped the cart's _update this returns immediately.
    // Bounded so a lost interrupt can never hang the board. #66: the bound was
    // 4M iterations (~a whole visible-hitch worth of ms if the flag were ever
    // missed); now ~250k (~a few ms) and the TRIP is REPORTED: returns True on
    // completion, False on trip WITHOUT clearing busy (the copy may genuinely
    // still be running; the caller must fall back to the sync path, which
    // writes the same bytes, and count the trip for diagnostics).
    for (uint32_t spins = 0; moy_gfx_copy_busy && spins < 250000u; spins++) {
    }
    if (moy_gfx_copy_busy) {
        return mp_const_false;
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_gfx_copy_wait_obj, moy_gfx_copy_wait);

static mp_obj_t moy_gfx_copy_async(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    if (moy_gfx_mcp == NULL) {
        async_memcpy_config_t cfg = ASYNC_MEMCPY_DEFAULT_CONFIG();
        cfg.backlog = 4;
        cfg.dma_burst_size = 64;        // widest AHB burst: best PSRAM throughput
        if (esp_async_memcpy_install(&cfg, &moy_gfx_mcp) != ESP_OK) {
            moy_gfx_mcp = NULL;
            return mp_const_false;      // caller falls back to the sync copy
        }
    }
    if (moy_gfx_copy_busy) {            // defensive: never queue a second copy
        if (moy_gfx_copy_wait() == mp_const_false) {
            return mp_const_false;      // prior copy stuck: refuse, caller goes sync
        }
    }
    size_t dcap, scap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dst_off = mp_obj_get_int(a[1]);
    const uint16_t *src = moy_gfx_buf_r(a[2], &scap);
    mp_int_t src_off = mp_obj_get_int(a[3]);
    mp_int_t npix = mp_obj_get_int(a[4]);
    if (dst_off < 0 || src_off < 0 || npix <= 0
        || (size_t)(dst_off + npix) > dcap
        || (size_t)(src_off + npix) > scap) {
        return mp_const_false;
    }
    moy_gfx_copy_busy = 1;
    esp_err_t err = esp_async_memcpy(moy_gfx_mcp, dst + dst_off,
                                     (void *)(src + src_off),
                                     (size_t)npix * 2u,
                                     moy_gfx_copy_done_cb, NULL);
    if (err != ESP_OK) {
        moy_gfx_copy_busy = 0;
        return mp_const_false;          // e.g. alignment refusal -> sync fallback
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_copy_async_obj, 5, 5,
                                           moy_gfx_copy_async);
#endif // MOY_GFX_HAS_ASYNC_COPY

// Moybyte #66: synchronous byte copy between buffer-protocol objects at byte
// offsets -- copy(dst, dst_off, src, src_off, nbytes). The SRAM-bounce flush
// pump copies 30KB bands PSRAM->internal per call; MicroPython's memoryview
// slice-assign for that costs ~1ms+ (measured as FLUSHBRK setup=2.5ms for two
// bands), a plain memcpy ~0.15ms. VM-neutral, no esp-idf dependency.
static mp_obj_t moy_gfx_copy(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t dst, src;
    mp_get_buffer_raise(args[0], &dst, MP_BUFFER_WRITE);
    mp_int_t dst_off = mp_obj_get_int(args[1]);
    mp_get_buffer_raise(args[2], &src, MP_BUFFER_READ);
    mp_int_t src_off = mp_obj_get_int(args[3]);
    mp_int_t nbytes = mp_obj_get_int(args[4]);
    if (nbytes < 0 || dst_off < 0 || src_off < 0
        || (size_t)dst_off + (size_t)nbytes > dst.len
        || (size_t)src_off + (size_t)nbytes > src.len) {
        mp_raise_ValueError(MP_ERROR_TEXT("copy out of range"));
    }
    memcpy((uint8_t *)dst.buf + dst_off,
           (const uint8_t *)src.buf + src_off, (size_t)nbytes);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_copy_obj, 5, 5, moy_gfx_copy);

// --- native vector primitives (#43 follow-up) -------------------------------
//
// circ/circb/line move the per-scanline / per-pixel rasterizers out of the cart's
// MicroPython loop (one MP->C call each instead of N) -- the same draw-call win as
// blit_batch/blit_map, for the carts built from shapes (tap_red etc.). They take a
// pre-resolved RGB565 `color`, the camera offset (cam_x, cam_y), and the screen-space
// clip rect [cx0,cy0)..[cx1,cy1), and reproduce the host canvas rasterizers pixel-
// for-pixel (circ = scanline spans like rect(); circb/line = Bresenham through a
// clipped pixel put). The clip rect is intersected with the buffer so a bad
// coordinate clips rather than overrunning.

// Clamp the clip rect to the buffer (cols to dw, rows to capacity/dw). Returns the
// usable row count via *max_rows; callers then test cx0<=x<cx1 && cy0<=y<cy1.
static inline void moy_gfx_clip(mp_int_t dw, size_t cap, mp_int_t *cx0, mp_int_t *cy0,
                               mp_int_t *cx1, mp_int_t *cy1) {
    mp_int_t max_rows = (mp_int_t)(cap / (size_t)dw);
    if (*cx0 < 0) *cx0 = 0;
    if (*cy0 < 0) *cy0 = 0;
    if (*cx1 > dw) *cx1 = dw;
    if (*cy1 > max_rows) *cy1 = max_rows;
}

static inline void moy_gfx_put(uint16_t *dst, mp_int_t dw, mp_int_t x, mp_int_t y,
                              uint16_t col, mp_int_t cam_x, mp_int_t cam_y,
                              mp_int_t cx0, mp_int_t cy0, mp_int_t cx1, mp_int_t cy1) {
    x -= cam_x;
    y -= cam_y;
    if (x < cx0 || x >= cx1 || y < cy0 || y >= cy1) return;
    dst[(size_t)y * (size_t)dw + (size_t)x] = col;
}

// circ(dst, dw, dh, cx, cy, r, color, cam_x, cam_y, cx0, cy0, cx1, cy1) -- FILLED
// circle: each scanline a clipped, camera-offset span (matches host canvas circ()).
static mp_obj_t moy_gfx_circ(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t cx = mp_obj_get_int(a[3]);
    mp_int_t cy = mp_obj_get_int(a[4]);
    mp_int_t r = mp_obj_get_int(a[5]);
    uint16_t col = (uint16_t)(mp_obj_get_int(a[6]) & 0xFFFF);
    mp_int_t cam_x = mp_obj_get_int(a[7]);
    mp_int_t cam_y = mp_obj_get_int(a[8]);
    mp_int_t cx0 = mp_obj_get_int(a[9]);
    mp_int_t cy0 = mp_obj_get_int(a[10]);
    mp_int_t cx1 = mp_obj_get_int(a[11]);
    mp_int_t cy1 = mp_obj_get_int(a[12]);
    (void)dh;
    if (dw <= 0 || r < 0) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    for (mp_int_t dy = -r; dy <= r; dy++) {
        mp_int_t span = (mp_int_t)sqrt((double)(r * r - dy * dy));
        mp_int_t y = cy + dy - cam_y;
        if (y < cy0 || y >= cy1) continue;
        mp_int_t x0 = cx - span - cam_x;
        mp_int_t x1 = x0 + 2 * span + 1;          // exclusive end
        if (x0 < cx0) x0 = cx0;
        if (x1 > cx1) x1 = cx1;
        if (x1 <= x0) continue;
        moy_gfx_fill_run(dst + (size_t)y * (size_t)dw + (size_t)x0,
                         (size_t)(x1 - x0), col);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_circ_obj, 13, 13, moy_gfx_circ);

// circb(dst, dw, dh, cx, cy, r, color, cam_x, cam_y, cx0, cy0, cx1, cy1) -- circle
// OUTLINE: Bresenham midpoint circle, 8 octant points per step (matches host circb()).
static mp_obj_t moy_gfx_circb(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t cx = mp_obj_get_int(a[3]);
    mp_int_t cy = mp_obj_get_int(a[4]);
    mp_int_t r = mp_obj_get_int(a[5]);
    uint16_t col = (uint16_t)(mp_obj_get_int(a[6]) & 0xFFFF);
    mp_int_t cam_x = mp_obj_get_int(a[7]);
    mp_int_t cam_y = mp_obj_get_int(a[8]);
    mp_int_t cx0 = mp_obj_get_int(a[9]);
    mp_int_t cy0 = mp_obj_get_int(a[10]);
    mp_int_t cx1 = mp_obj_get_int(a[11]);
    mp_int_t cy1 = mp_obj_get_int(a[12]);
    (void)dh;
    if (dw <= 0 || r < 0) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    mp_int_t x = r, y = 0, err = 0;
    while (x >= y) {
        moy_gfx_put(dst, dw, cx + x, cy + y, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx + y, cy + x, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx - y, cy + x, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx - x, cy + y, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx - x, cy - y, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx - y, cy - x, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx + y, cy - x, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_gfx_put(dst, dw, cx + x, cy - y, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        y += 1;
        if (err <= 0) {
            err += 2 * y + 1;
        } else {
            x -= 1;
            err -= 2 * x + 1;
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_circb_obj, 13, 13, moy_gfx_circb);

// line(dst, dw, dh, x0, y0, x1, y1, color, cam_x, cam_y, cx0, cy0, cx1, cy1) --
// Bresenham line through a clipped pixel put (matches host canvas line()).
static mp_obj_t moy_gfx_line(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t x0 = mp_obj_get_int(a[3]);
    mp_int_t y0 = mp_obj_get_int(a[4]);
    mp_int_t x1 = mp_obj_get_int(a[5]);
    mp_int_t y1 = mp_obj_get_int(a[6]);
    uint16_t col = (uint16_t)(mp_obj_get_int(a[7]) & 0xFFFF);
    mp_int_t cam_x = mp_obj_get_int(a[8]);
    mp_int_t cam_y = mp_obj_get_int(a[9]);
    mp_int_t cx0 = mp_obj_get_int(a[10]);
    mp_int_t cy0 = mp_obj_get_int(a[11]);
    mp_int_t cx1 = mp_obj_get_int(a[12]);
    mp_int_t cy1 = mp_obj_get_int(a[13]);
    (void)dh;
    if (dw <= 0) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    mp_int_t dx = x1 > x0 ? x1 - x0 : x0 - x1;
    mp_int_t dy = y1 > y0 ? y0 - y1 : y1 - y0;    // -abs(y1-y0)
    mp_int_t sx = x0 < x1 ? 1 : -1;
    mp_int_t sy = y0 < y1 ? 1 : -1;
    mp_int_t err = dx + dy;
    for (;;) {
        moy_gfx_put(dst, dw, x0, y0, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        if (x0 == x1 && y0 == y1) break;
        mp_int_t e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_line_obj, 14, 14, moy_gfx_line);

// blit_window(dst, dw, dh, src, src_w, sx, sy) -- copy a dw x dh window from a wider
// RGB565 `src` (src_w px/row) at (sx, sy) into `dst` (dw px/row, contiguous). The scroll
// engine's core op (#43): a flat per-row memcpy, no tile lookup / colorkey / scale, so
// it's far cheaper than re-running map() over a scrolling background -- the cart pre-
// renders the level into a wide buffer once, then each frame blits the camera window.
// Bounds-clamped to both buffers.
static mp_obj_t moy_gfx_blit_window(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap, scap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    const uint16_t *src = moy_gfx_buf_r(a[3], &scap);
    mp_int_t src_w = mp_obj_get_int(a[4]);
    mp_int_t sx = mp_obj_get_int(a[5]);
    mp_int_t sy = mp_obj_get_int(a[6]);
    if (dw <= 0 || dh <= 0 || src_w <= 0) return mp_const_none;
    if (sx < 0) sx = 0;
    if (sy < 0) sy = 0;
    if (sx + dw > src_w) dw = src_w - sx;            // clamp window to source width
    if (dw <= 0) return mp_const_none;
    if ((size_t)dw * (size_t)dh > dcap) dh = (mp_int_t)(dcap / (size_t)dw);  // dst guard
    mp_int_t src_rows = (mp_int_t)(scap / (size_t)src_w);
    if (sy + dh > src_rows) dh = src_rows - sy;      // src guard
    if (dh <= 0) return mp_const_none;
    for (mp_int_t row = 0; row < dh; row++) {
        memcpy(dst + (size_t)row * (size_t)dw,
               src + (size_t)(sy + row) * (size_t)src_w + (size_t)sx,
               (size_t)dw * 2u);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_window_obj, 7, 7, moy_gfx_blit_window);

// scroll_rect(buf, stride_px, rx, ry, rw, rh, dx, dy) -- shift the pixels inside
// rect (rx, ry, rw, rh) of an RGB565 buffer by (dx, dy) IN PLACE: the #113
// scroll-as-blit primitive (a scrolled UI view keeps its already-correct pixels;
// the caller repaints only the exposed band). Pixels that would leave the rect
// are dropped; the strip shifted in from outside keeps its stale content. Rect
// clamped to the buffer. Per-row memmove (horizontal overlap safe); the vertical
// iteration order follows dy so rows are read before they are overwritten.
static mp_obj_t moy_gfx_scroll_rect(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *px = moy_gfx_buf_w(a[0], &cap);
    mp_int_t stride = mp_obj_get_int(a[1]);
    mp_int_t rx = mp_obj_get_int(a[2]);
    mp_int_t ry = mp_obj_get_int(a[3]);
    mp_int_t rw = mp_obj_get_int(a[4]);
    mp_int_t rh = mp_obj_get_int(a[5]);
    mp_int_t dx = mp_obj_get_int(a[6]);
    mp_int_t dy = mp_obj_get_int(a[7]);
    if (stride <= 0 || (dx == 0 && dy == 0)) return mp_const_none;
    mp_int_t rows = (mp_int_t)(cap / (size_t)stride);
    mp_int_t x0 = rx < 0 ? 0 : rx;
    mp_int_t y0 = ry < 0 ? 0 : ry;
    mp_int_t x1 = rx + rw; if (x1 > stride) x1 = stride;
    mp_int_t y1 = ry + rh; if (y1 > rows) y1 = rows;
    // Destination span: the part of the rect whose source is also inside it.
    mp_int_t tx0 = x0 + (dx > 0 ? dx : 0);
    mp_int_t tx1 = x1 + (dx < 0 ? dx : 0);
    mp_int_t ty0 = y0 + (dy > 0 ? dy : 0);
    mp_int_t ty1 = y1 + (dy < 0 ? dy : 0);
    if (tx0 >= tx1 || ty0 >= ty1) return mp_const_none;
    size_t cw = (size_t)(tx1 - tx0) * 2u;
    if (dy > 0) {
        for (mp_int_t ty = ty1 - 1; ty >= ty0; ty--) {
            memmove(px + (size_t)ty * (size_t)stride + (size_t)tx0,
                    px + (size_t)(ty - dy) * (size_t)stride + (size_t)(tx0 - dx),
                    cw);
        }
    } else {
        for (mp_int_t ty = ty0; ty < ty1; ty++) {
            memmove(px + (size_t)ty * (size_t)stride + (size_t)tx0,
                    px + (size_t)(ty - dy) * (size_t)stride + (size_t)(tx0 - dx),
                    cw);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_scroll_rect_obj, 8, 8, moy_gfx_scroll_rect);

// blit_indices(dst, dw, dh, dx, dy, indices, iw, ih, pal565) -- place an iw x ih palette-
// INDEX bitmap (1 byte/pixel, like blit_map's cells) at (dx, dy) into the RGB565 `dst` (dw
// px/row), converting each index through `pal565` (an index->RGB565 table, 1 uint16/entry).
// The "images are data, not draw calls" bake (#63 Fold 3): one C call turns a paint-app
// index bitmap into pixels, replacing the thousands of rect() replays the old background-
// paint anti-pattern used. Opaque (a painted background fills every pixel -- no colorkey);
// an index past the palette is skipped (leaves dst). Bounds-clamped to dst.
static mp_obj_t moy_gfx_blit_indices(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap, pcap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_int_t dx = mp_obj_get_int(a[3]);
    mp_int_t dy = mp_obj_get_int(a[4]);
    mp_buffer_info_t ibi;
    mp_get_buffer_raise(a[5], &ibi, MP_BUFFER_READ);
    const uint8_t *idx = (const uint8_t *)ibi.buf;   // index bytes (1 byte/pixel)
    size_t icap = ibi.len;
    mp_int_t iw = mp_obj_get_int(a[6]);
    mp_int_t ih = mp_obj_get_int(a[7]);
    const uint16_t *pal = moy_gfx_buf_r(a[8], &pcap); // index -> RGB565
    if (dw <= 0 || dh <= 0 || iw <= 0 || ih <= 0 || pcap == 0) return mp_const_none;
    if ((size_t)dw * (size_t)dh > dcap) dh = (mp_int_t)(dcap / (size_t)dw);
    for (mp_int_t row = 0; row < ih; row++) {
        mp_int_t ty = dy + row;
        if (ty < 0 || ty >= dh) continue;
        size_t srow = (size_t)row * (size_t)iw;
        mp_int_t drow = ty * dw;
        for (mp_int_t col = 0; col < iw; col++) {
            mp_int_t tx = dx + col;
            if (tx < 0 || tx >= dw) continue;
            size_t si = srow + (size_t)col;
            if (si >= icap) continue;
            size_t p = (size_t)idx[si];
            if (p >= pcap) continue;                 // index past palette -> skip
            dst[(size_t)drow + (size_t)tx] = pal[p];
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_indices_obj, 9, 9, moy_gfx_blit_indices);

// The petme128 rasterizer, shared by the `text` op and the print draw gate. The
// clip rect is intersected with the buffer here, so every caller is bounds-safe.
static void moy_gfx_text_raw(uint16_t *dst, size_t dcap, mp_int_t dw,
                             const uint8_t *s, size_t slen,
                             mp_int_t x, mp_int_t y, uint16_t col,
                             const uint8_t *font, mp_int_t nglyphs,
                             mp_int_t first, mp_int_t scale,
                             mp_int_t cam_x, mp_int_t cam_y,
                             mp_int_t cx0, mp_int_t cy0,
                             mp_int_t cx1, mp_int_t cy1) {
    if (dw <= 0 || nglyphs <= 0) return;
    if (scale < 1) scale = 1;
    moy_gfx_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    x -= cam_x;
    y -= cam_y;
    mp_int_t adv = 8 * scale;                    // cell advance per character
    if (y >= cy1 || y + adv <= cy0) return;      // whole line off-clip
    for (size_t i = 0; i < slen; i++, x += adv) {
        if (x >= cx1) break;                     // rest of the string is right of clip
        if (x + adv <= cx0) continue;            // this glyph entirely left of clip
        mp_int_t gi = (mp_int_t)s[i] - first;
        if (gi < 0 || gi >= nglyphs) gi = 0;     // out of range -> first glyph (space)
        const uint8_t *g = font + (size_t)gi * 8u;
        for (mp_int_t j = 0; j < 8; j++) {
            uint8_t bits = g[j];
            if (bits == 0) continue;
            mp_int_t bx = x + j * scale;
            if (bx >= cx1 || bx + scale <= cx0) continue;
            for (mp_int_t row = 0; bits != 0; bits >>= 1, row++) {
                if (!(bits & 1)) continue;
                mp_int_t by = y + row * scale;
                for (mp_int_t sub_y = 0; sub_y < scale; sub_y++) {
                    mp_int_t ty = by + sub_y;
                    if (ty < cy0 || ty >= cy1) continue;
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (mp_int_t sub_x = 0; sub_x < scale; sub_x++) {
                        mp_int_t tx = bx + sub_x;
                        if (tx < cx0 || tx >= cx1) continue;
                        drow[tx] = col;
                    }
                }
            }
        }
    }
}

// text(dst, dw, dh, s, x, y, color, font, first, scale, cam_x, cam_y,
//      cx0, cy0, cx1, cy1) -- render a whole string in ONE C call (issue #62): the
// text analogue of blit_batch. `font` is a petme128-layout glyph blob (8 bytes per
// glyph, column-major, LSB = top row -- exactly runtime/font.py's _FONT, the SAME
// bytes the host rasterizes), `first` its first codepoint. Each set glyph pixel
// becomes a `scale` x `scale` block of the pre-resolved RGB565 `color`; camera
// offset and the screen-space clip rect [cx0,cy0)..[cx1,cy1) are honoured per
// pixel like the other vector ops (framebuf.text could do neither). Glyph advance
// is 8*scale. The string is walked as BYTES (like framebuf.text); an out-of-range
// byte renders the font's first glyph (space), matching runtime/font.py glyph().
static mp_obj_t moy_gfx_text(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_buffer_info_t sbi;
    mp_get_buffer_raise(a[3], &sbi, MP_BUFFER_READ);
    mp_buffer_info_t fbi;
    mp_get_buffer_raise(a[7], &fbi, MP_BUFFER_READ);
    moy_gfx_text_raw(dst, dcap, dw,
                     (const uint8_t *)sbi.buf, sbi.len,
                     mp_obj_get_int(a[4]), mp_obj_get_int(a[5]),
                     (uint16_t)(mp_obj_get_int(a[6]) & 0xFFFF),
                     (const uint8_t *)fbi.buf, (mp_int_t)(fbi.len / 8u),
                     mp_obj_get_int(a[8]), mp_obj_get_int(a[9]),
                     mp_obj_get_int(a[10]), mp_obj_get_int(a[11]),
                     mp_obj_get_int(a[12]), mp_obj_get_int(a[13]),
                     mp_obj_get_int(a[14]), mp_obj_get_int(a[15]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_text_obj, 16, 16, moy_gfx_text);

// --- draw gates: the console CHROME's native fast path (#155) ----------------
//
// WHY (measured on P4 glass 2026-07-26, warm heap):
//     cv.rect(x, y, w, h, c)      50.2 us
//     moy_gfx.fill_rect(...)       5.2 us   <- the same pixels
//     an EMPTY 5-arg Python method 5.5 us   <- the dispatch floor
//     cv.print("Hello", ...)      71.1 us
//     moy_gfx.text(...)           31.9 us
// So ~90% of a chrome rect and ~55% of a chrome print is MicroPython wrapper --
// two Python frames (rect -> _fill), four int() calls, a palette double-index --
// around a kernel that is already fast. A windowed Settings scroll issues ~94
// rects + ~29 prints per frame, so that overhead alone is ~6ms of a ~70ms frame,
// and it scales with every new panel and every glyph a smaller font fits.
//
// NOT A DEFERRED QUEUE. The spr_gate (#63) batches because ITS win is amortising
// the MP->C boundary over a run of sprites. Here the boundary is only 5.2us while
// the Python frame is 45us, so a queue would buy ~0.3ms/frame more than drawing
// immediately -- and would cost a draw-ORDER contract across every other verb
// (cls/pix/line/circ/spr/map/blit_*/scroll_rect all read or write the same
// buffer). These gates therefore draw IMMEDIATELY: same pixels, same order, same
// frame, just without the Python frame. Nothing else in the canvas changes.
//
// A gate reads live canvas state through a shared `DrawCtx`: an array('i') the
// Python side updates whenever camera/clip/font-scale change (rare) and a 64-entry
// uint16 palette already resolved through pal() -- so the hot path is pure C. The
// destination buffer is pushed in by sync_back (the DPI ping-pong swaps it every
// frame). Anything unusual -- kwargs, a wrong arg count, a non-numeric coord, a
// non-string print -- delegates verbatim to `fallback`, the original bound Python
// method, so semantics are IDENTICAL and this is purely a fast lane.

enum {
    ST_CAM_X = 0, ST_CAM_Y,
    ST_CX0, ST_CY0, ST_CX1, ST_CY1,
    ST_W, ST_H,
    ST_FONT_SCALE,
    ST_PROF,                 // 1 = accumulate the DRAW2 timers below
    ST_N_FILL, ST_N_TEXT,    // call counts (always) -- proof the gate is live
    ST_T_FILL, ST_T_TEXT,    // microseconds (only while ST_PROF)
    ST_LEN
};

enum { GATE_RECT = 0, GATE_RECTB, GATE_PRINT, GATE_PIX };

typedef struct _moy_gfx_draw_ctx_obj_t {
    mp_obj_base_t base;
    mp_obj_t canvas;         // for the rare flush_batch upcall
    mp_obj_t buf_obj;        // held so gc keeps the destination alive
    mp_obj_t state_obj;      // held: array('i')
    mp_obj_t pal_obj;        // held: array('H')
    mp_obj_t batch_obj;      // held: the sprite queue array('h'), or None
    mp_obj_t font_obj;       // held: the petme128 blob
    uint16_t *px;            // destination (gc never moves objects)
    size_t cap;              // destination capacity in pixels
    int32_t *st;
    uint16_t *pal;
    size_t npal;
    int16_t *batch;          // sprite queue, or NULL
    const uint8_t *font;
    mp_int_t nglyphs;
    mp_int_t first;
} moy_gfx_draw_ctx_obj_t;

// set_buf(buf): re-point at the compositor's current back buffer. Called once
// per frame from DeviceCanvas.sync_back -- the DPI double buffer ping-pongs, so
// a cached pointer would otherwise draw into the buffer being scanned out.
static mp_obj_t moy_gfx_draw_ctx_set_buf(mp_obj_t self_in, mp_obj_t buf_obj) {
    moy_gfx_draw_ctx_obj_t *c = MP_OBJ_TO_PTR(self_in);
    size_t cap;
    c->px = moy_gfx_buf_w(buf_obj, &cap);
    c->cap = cap;
    c->buf_obj = buf_obj;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(moy_gfx_draw_ctx_set_buf_obj,
                                 moy_gfx_draw_ctx_set_buf);

static inline void gate_fill(moy_gfx_draw_ctx_obj_t *c, mp_int_t x, mp_int_t y,
                             mp_int_t w, mp_int_t h, uint16_t col);

// fill_rects(arr, n, ox, oy, c): the #163 span-batch verb -- draw n packed
// quads (x, y, w, h, ci as int16, 5 slots each) through the same gate_fill the
// rect gate uses (camera/clip/pal identical), in ONE MP->C call. ox/oy shift
// every quad (relative span lists -- chrome glyphs); c >= 0 overrides every
// quad's ci (one-color packs cached across theme changes). n < 0 means "the
// whole array". Counted as n fills in ST_N_FILL so DRAW2 gate liveness and the
// per-frame fill attribution stay honest.
static mp_obj_t moy_gfx_draw_ctx_fill_rects(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_gfx_draw_ctx_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    if (c->px == NULL) {
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("fill_rects: no destination buffer"));
    }
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[1], &bi, MP_BUFFER_READ);
    const int16_t *q = (const int16_t *)bi.buf;
    mp_int_t nmax = (mp_int_t)(bi.len / (2 * 5));
    mp_int_t n = mp_obj_get_int(a[2]);
    if (n < 0 || n > nmax) n = nmax;
    mp_int_t ox = mp_obj_get_int(a[3]);
    mp_int_t oy = mp_obj_get_int(a[4]);
    mp_int_t cov = mp_obj_get_int(a[5]);
    // #63 order rule: a pending sprite run must land before any other primitive.
    if (c->batch != NULL && c->batch[0] > 4) {
        mp_obj_t dest[2];
        mp_load_method(c->canvas, MP_QSTR_flush_batch, dest);
        mp_call_method_n_kw(0, 0, dest);
    }
    int32_t *st = c->st;
    uint32_t t0 = st[ST_PROF] ? (uint32_t)mp_hal_ticks_us() : 0;
    uint16_t col = 0;
    bool have_col = false;
    if (cov >= 0) {
        col = c->pal[(size_t)(cov & 63) % c->npal];
        have_col = true;
    }
    for (mp_int_t i = 0; i < n; i++) {
        const int16_t *p = q + i * 5;
        uint16_t cc = have_col
            ? col : c->pal[(size_t)(p[4] & 63) % c->npal];
        gate_fill(c, (mp_int_t)p[0] + ox, (mp_int_t)p[1] + oy,
                  (mp_int_t)p[2], (mp_int_t)p[3], cc);
    }
    st[ST_N_FILL] += n;
    if (st[ST_PROF]) st[ST_T_FILL] += (int32_t)(mp_hal_ticks_us() - t0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_draw_ctx_fill_rects_obj,
                                           6, 6, moy_gfx_draw_ctx_fill_rects);

static const mp_rom_map_elem_t moy_gfx_draw_ctx_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_set_buf), MP_ROM_PTR(&moy_gfx_draw_ctx_set_buf_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_rects),
      MP_ROM_PTR(&moy_gfx_draw_ctx_fill_rects_obj) },
};
static MP_DEFINE_CONST_DICT(moy_gfx_draw_ctx_locals,
                            moy_gfx_draw_ctx_locals_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    moy_gfx_draw_ctx_type,
    MP_QSTR_DrawCtx,
    MP_TYPE_FLAG_NONE,
    locals_dict, &moy_gfx_draw_ctx_locals
);

typedef struct _moy_gfx_draw_gate_obj_t {
    mp_obj_base_t base;
    moy_gfx_draw_ctx_obj_t *ctx;
    mp_obj_t fallback;
    uint8_t kind;
} moy_gfx_draw_gate_obj_t;

// A coordinate/colour arg: int or float (kid + layout code both produce floats).
static inline bool gate_num(mp_obj_t o, mp_int_t *out) {
    if (mp_obj_is_small_int(o)) {
        *out = MP_OBJ_SMALL_INT_VALUE(o);
        return true;
    }
    if (mp_obj_is_float(o)) {
        *out = (mp_int_t)mp_obj_get_float(o);
        return true;
    }
    return false;
}

// The _fill body: camera-offset, intersect with the clip rect, fill. Mirrors
// DeviceCanvas._fill exactly (same clamp order, same empty-rect early out) so
// gated and ungated pixels are identical.
static inline void gate_fill(moy_gfx_draw_ctx_obj_t *c, mp_int_t x, mp_int_t y,
                             mp_int_t w, mp_int_t h, uint16_t col) {
    const int32_t *st = c->st;
    mp_int_t stride = st[ST_W];
    if (stride <= 0) return;
    x -= st[ST_CAM_X];
    y -= st[ST_CAM_Y];
    mp_int_t x0 = st[ST_CX0], y0 = st[ST_CY0];
    if (x > x0) x0 = x;
    if (y > y0) y0 = y;
    mp_int_t x1 = x + w, y1 = y + h;
    if (x1 > st[ST_CX1]) x1 = st[ST_CX1];
    if (y1 > st[ST_CY1]) y1 = st[ST_CY1];
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > stride) x1 = stride;
    mp_int_t rows = (mp_int_t)(c->cap / (size_t)stride);
    if (y1 > rows) y1 = rows;
    if (x1 <= x0 || y1 <= y0) return;
    for (mp_int_t row = y0; row < y1; row++) {
        moy_gfx_fill_run(c->px + (size_t)row * (size_t)stride + (size_t)x0,
                         (size_t)(x1 - x0), col);
    }
}

static mp_obj_t draw_gate_call(mp_obj_t self_in, size_t n_args, size_t n_kw,
                               const mp_obj_t *args) {
    moy_gfx_draw_gate_obj_t *g = MP_OBJ_TO_PTR(self_in);
    moy_gfx_draw_ctx_obj_t *c = g->ctx;
    uint8_t kind = g->kind;
    if (n_kw != 0 || c->px == NULL) {
        return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
    }
    // A pending sprite run must land before any other primitive (#63 order).
    if (c->batch != NULL && c->batch[0] > 4) {
        mp_obj_t dest[2];
        mp_load_method(c->canvas, MP_QSTR_flush_batch, dest);
        mp_call_method_n_kw(0, 0, dest);
    }
    int32_t *st = c->st;
    uint32_t t0 = st[ST_PROF] ? (uint32_t)mp_hal_ticks_us() : 0;

    if (kind == GATE_PRINT) {
        // print(s, x, y, c[, scale]) -- the legacy per-call `scale` is IGNORED
        // (system-UI scaling is the #39 font_scale path), exactly like the
        // Python DeviceCanvas.print / P4SystemCanvas.print it replaces.
        if (n_args < 4 || n_args > 5 || !mp_obj_is_str(args[0])) {
            return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
        }
        mp_int_t x, y, ci;
        if (!gate_num(args[1], &x) || !gate_num(args[2], &y)
            || !gate_num(args[3], &ci)) {
            return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
        }
        size_t slen;
        const char *s = mp_obj_str_get_data(args[0], &slen);
        moy_gfx_text_raw(c->px, c->cap, st[ST_W], (const uint8_t *)s, slen,
                         x, y, c->pal[(size_t)(ci & 63) % c->npal],
                         c->font, c->nglyphs, c->first, st[ST_FONT_SCALE],
                         st[ST_CAM_X], st[ST_CAM_Y],
                         st[ST_CX0], st[ST_CY0], st[ST_CX1], st[ST_CY1]);
        st[ST_N_TEXT]++;
        if (st[ST_PROF]) st[ST_T_TEXT] += (int32_t)(mp_hal_ticks_us() - t0);
        return mp_const_none;
    }

    if (kind == GATE_PIX) {
        // pix(x, y, c) writes; the 2-arg READ form returns a value -> Python.
        mp_int_t x, y, ci;
        if (n_args != 3 || !gate_num(args[0], &x) || !gate_num(args[1], &y)
            || !gate_num(args[2], &ci)) {
            return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
        }
        gate_fill(c, x, y, 1, 1, c->pal[(size_t)(ci & 63) % c->npal]);
        st[ST_N_FILL]++;
        if (st[ST_PROF]) st[ST_T_FILL] += (int32_t)(mp_hal_ticks_us() - t0);
        return mp_const_none;
    }

    // rect / rectb (x, y, w, h, c)
    mp_int_t v[5];
    if (n_args != 5) {
        return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
    }
    for (size_t i = 0; i < 5; i++) {
        if (!gate_num(args[i], &v[i])) {
            return mp_call_function_n_kw(g->fallback, n_args, n_kw, args);
        }
    }
    uint16_t col = c->pal[(size_t)(v[4] & 63) % c->npal];
    if (kind == GATE_RECT) {
        gate_fill(c, v[0], v[1], v[2], v[3], col);
    } else {
        // rectb = outline: the same four clipped fills the Python path issues.
        gate_fill(c, v[0], v[1], v[2], 1, col);
        gate_fill(c, v[0], v[1] + v[3] - 1, v[2], 1, col);
        gate_fill(c, v[0], v[1], 1, v[3], col);
        gate_fill(c, v[0] + v[2] - 1, v[1], 1, v[3], col);
    }
    st[ST_N_FILL]++;
    if (st[ST_PROF]) st[ST_T_FILL] += (int32_t)(mp_hal_ticks_us() - t0);
    return mp_const_none;
}

static MP_DEFINE_CONST_OBJ_TYPE(
    moy_gfx_draw_gate_type,
    MP_QSTR_draw_gate,
    MP_TYPE_FLAG_NONE,
    call, draw_gate_call
);

// make_draw_ctx(canvas, state, pal, batch, font, first) -> DrawCtx.
//   state -- array('i') of ST_LEN entries (see the enum above)
//   pal   -- array('H'), index -> RGB565 with the pal() remap already applied
//   batch -- the canvas's sprite queue array('h'), or None
//   font  -- the petme128 glyph blob; first -- its first codepoint
// The destination buffer arrives separately via set_buf (it ping-pongs).
static mp_obj_t moy_gfx_make_draw_ctx(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t sbi, pbi, fbi;
    mp_get_buffer_raise(a[1], &sbi, MP_BUFFER_RW);
    if (sbi.len < ST_LEN * (int)sizeof(int32_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("state array too small"));
    }
    mp_get_buffer_raise(a[2], &pbi, MP_BUFFER_READ);
    if (pbi.len < 2) {
        mp_raise_ValueError(MP_ERROR_TEXT("palette too small"));
    }
    mp_get_buffer_raise(a[4], &fbi, MP_BUFFER_READ);
    moy_gfx_draw_ctx_obj_t *c = mp_obj_malloc(moy_gfx_draw_ctx_obj_t,
                                              &moy_gfx_draw_ctx_type);
    c->canvas = a[0];
    c->buf_obj = mp_const_none;
    c->state_obj = a[1];
    c->pal_obj = a[2];
    c->batch_obj = a[3];
    c->font_obj = a[4];
    c->px = NULL;
    c->cap = 0;
    c->st = (int32_t *)sbi.buf;
    c->pal = (uint16_t *)pbi.buf;
    c->npal = pbi.len / 2u;
    c->batch = NULL;
    if (a[3] != mp_const_none) {
        mp_buffer_info_t bbi;
        if (mp_get_buffer(a[3], &bbi, MP_BUFFER_RW) && bbi.len >= 2 * 4) {
            c->batch = (int16_t *)bbi.buf;
        }
    }
    c->font = (const uint8_t *)fbi.buf;
    c->nglyphs = (mp_int_t)(fbi.len / 8u);
    c->first = mp_obj_get_int(a[5]);
    return MP_OBJ_FROM_PTR(c);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_make_draw_ctx_obj, 6, 6,
                                           moy_gfx_make_draw_ctx);

// make_draw_gate(ctx, kind, fallback) -> a callable replacing one canvas verb.
// kind: 0 rect, 1 rectb, 2 print, 3 pix.
static mp_obj_t moy_gfx_make_draw_gate(mp_obj_t ctx_in, mp_obj_t kind_in,
                                       mp_obj_t fallback) {
    if (!mp_obj_is_type(ctx_in, &moy_gfx_draw_ctx_type)) {
        mp_raise_TypeError(MP_ERROR_TEXT("need a DrawCtx"));
    }
    mp_int_t kind = mp_obj_get_int(kind_in);
    if (kind < 0 || kind > GATE_PIX) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad gate kind"));
    }
    moy_gfx_draw_gate_obj_t *g = mp_obj_malloc(moy_gfx_draw_gate_obj_t,
                                               &moy_gfx_draw_gate_type);
    g->ctx = MP_OBJ_TO_PTR(ctx_in);
    g->fallback = fallback;
    g->kind = (uint8_t)kind;
    return MP_OBJ_FROM_PTR(g);
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_gfx_make_draw_gate_obj,
                                 moy_gfx_make_draw_gate);

// decode_runs(dst, npix, packed) -> pixels written, or -1 on a corrupt stream.
// Expands a MOY64 run-length stream -- byte pairs (count, value), count >= 1,
// value <= 63 -- into an indexed bitmap. The cover-art decode (#155).
//
// This is the operation the whole time-sliced _CoverJob machinery existed for:
// interpreted, one 320x240 cover cost 0.5-1.7s, so it had to be spread over
// frames and cached to a sidecar. In C it is a memset loop. That also undoes the
// sidecar trade: the raw source is 77KB and this board reads its internal flash
// at ~470KB/s (measured: 164ms per source read), while the RLE blob it came from
// is a fraction of the size -- so reading the small blob and decoding natively
// beats reading a big pre-decoded one.
static mp_obj_t moy_gfx_decode_runs(mp_obj_t dst_obj, mp_obj_t npix_obj,
                                    mp_obj_t packed_obj) {
    mp_buffer_info_t dbi, pbi;
    mp_get_buffer_raise(dst_obj, &dbi, MP_BUFFER_WRITE);
    mp_get_buffer_raise(packed_obj, &pbi, MP_BUFFER_READ);
    mp_int_t total = mp_obj_get_int(npix_obj);
    if (total < 0 || (size_t)total > dbi.len) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    uint8_t *dst = (uint8_t *)dbi.buf;
    const uint8_t *p = (const uint8_t *)pbi.buf;
    size_t n = pbi.len & ~(size_t)1;          // whole (count, value) pairs only
    mp_int_t pos = 0;
    for (size_t i = 0; i < n; i += 2) {
        mp_int_t count = p[i];
        uint8_t value = p[i + 1];
        if (count < 1 || value > 63 || pos + count > total) {
            return MP_OBJ_NEW_SMALL_INT(-1);
        }
        memset(dst + pos, value, (size_t)count);
        pos += count;
    }
    return MP_OBJ_NEW_SMALL_INT(pos);
}
static MP_DEFINE_CONST_FUN_OBJ_3(moy_gfx_decode_runs_obj, moy_gfx_decode_runs);

// crop_index(dst, dw, dh, src, sw, sh, ox, oy, cw, ch) -- nearest-sample the
// (ox, oy, cw, ch) window of an INDEXED source (1 byte/pixel) into a dw x dh
// indexed destination. The cover-art crop (#155).
//
// Covers are MOY64 indices, not RGB565, so this stays in the index domain: the
// shared console caches one indexed blittable that the host draws per-pixel and
// the device bakes to RGB565 once. Routing it through the PPA instead would mean
// converting to 565 first and handing back a device-only representation, to save
// a fraction of a millisecond -- the crop is only ~20k pixels. The DECODE was
// the expensive half (0.5-1.7s) and that is now cached; this is what is left.
//
// Reproduces runtime/console.py _CoverJob's crop EXACTLY (same integer floors,
// same column map), so a native and a Python crop are byte-identical.
static mp_obj_t moy_gfx_crop_index(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    mp_buffer_info_t dbi, sbi;
    mp_get_buffer_raise(a[0], &dbi, MP_BUFFER_WRITE);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_get_buffer_raise(a[3], &sbi, MP_BUFFER_READ);
    mp_int_t sw = mp_obj_get_int(a[4]);
    mp_int_t sh = mp_obj_get_int(a[5]);
    mp_int_t ox = mp_obj_get_int(a[6]);
    mp_int_t oy = mp_obj_get_int(a[7]);
    mp_int_t cw = mp_obj_get_int(a[8]);
    mp_int_t ch = mp_obj_get_int(a[9]);
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0 || cw <= 0 || ch <= 0
        || ox < 0 || oy < 0 || ox + cw > sw || oy + ch > sh
        || (size_t)(sw * sh) > sbi.len
        || (size_t)(dw * dh) > dbi.len) {
        return mp_const_false;
    }
    uint8_t *dst = (uint8_t *)dbi.buf;
    const uint8_t *src = (const uint8_t *)sbi.buf;
    for (mp_int_t dy = 0; dy < dh; dy++) {
        const uint8_t *srow = src + (size_t)(oy + dy * ch / dh) * (size_t)sw;
        uint8_t *drow = dst + (size_t)dy * (size_t)dw;
        for (mp_int_t dx = 0; dx < dw; dx++) {
            drow[dx] = srow[ox + dx * cw / dw];
        }
    }
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_crop_index_obj, 10, 10,
                                           moy_gfx_crop_index);

// pack_strip(fb, fb_w, x, y, w, rows, dst) -- copy a (w x rows) window of the
// framebuffer into dst contiguously (row-major). Full-width is one memcpy;
// cropped rects are packed row-by-row in C (the slow Stage 2 Python path).
static mp_obj_t moy_gfx_pack_strip(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t fbcap, dcap;
    const uint16_t *fb = moy_gfx_buf_r(a[0], &fbcap);
    mp_int_t fb_w = mp_obj_get_int(a[1]);
    mp_int_t x = mp_obj_get_int(a[2]);
    mp_int_t y = mp_obj_get_int(a[3]);
    mp_int_t w = mp_obj_get_int(a[4]);
    mp_int_t rows = mp_obj_get_int(a[5]);
    uint16_t *dst = moy_gfx_buf_w(a[6], &dcap);
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
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_pack_strip_obj, 7, 7, moy_gfx_pack_strip);

// native_code_free_all() -> bool: reclaim the esp32 port's @micropython.native
// exec arena (#66 repeat-run cliff fix -- the arena is GROW-ONLY until soft
// reset; the cart loader frees it on a compile miss, when no cart native
// function objects are live). The symbol gains external linkage via the
// build's esp32_native_code_free.patch; declared WEAK here so this same file
// still links on builds without the patch (the unix bench port, the mainline
// P4 until its build.sh applies the twin patch) -- there the address is NULL
// and the call reports False (no reclaim available).
extern void esp_native_code_free_all(void) __attribute__((weak));

static mp_obj_t moy_gfx_native_code_free_all(void) {
    if (&esp_native_code_free_all == NULL) {
        return mp_const_false;
    }
    esp_native_code_free_all();
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_gfx_native_code_free_all_obj, moy_gfx_native_code_free_all);

static const mp_rom_map_elem_t moy_gfx_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_OBJ_NEW_QSTR(MP_QSTR_moy_gfx) },
    { MP_ROM_QSTR(MP_QSTR_native_code_free_all), MP_ROM_PTR(&moy_gfx_native_code_free_all_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill),       MP_ROM_PTR(&moy_gfx_fill_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_rect),  MP_ROM_PTR(&moy_gfx_fill_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit565),    MP_ROM_PTR(&moy_gfx_blit565_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit565_scale), MP_ROM_PTR(&moy_gfx_blit565_scale_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_map),   MP_ROM_PTR(&moy_gfx_blit_map_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_batch), MP_ROM_PTR(&moy_gfx_blit_batch_obj) },
    { MP_ROM_QSTR(MP_QSTR_make_spr_gate), MP_ROM_PTR(&moy_gfx_make_spr_gate_obj) },
    { MP_ROM_QSTR(MP_QSTR_make_draw_ctx), MP_ROM_PTR(&moy_gfx_make_draw_ctx_obj) },
    { MP_ROM_QSTR(MP_QSTR_make_draw_gate), MP_ROM_PTR(&moy_gfx_make_draw_gate_obj) },
    #ifdef MOY_GFX_HAS_ASYNC_COPY
    { MP_ROM_QSTR(MP_QSTR_copy_async), MP_ROM_PTR(&moy_gfx_copy_async_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_wait), MP_ROM_PTR(&moy_gfx_copy_wait_obj) },
    #endif
    { MP_ROM_QSTR(MP_QSTR_copy),       MP_ROM_PTR(&moy_gfx_copy_obj) },
    { MP_ROM_QSTR(MP_QSTR_circ),       MP_ROM_PTR(&moy_gfx_circ_obj) },
    { MP_ROM_QSTR(MP_QSTR_circb),      MP_ROM_PTR(&moy_gfx_circb_obj) },
    { MP_ROM_QSTR(MP_QSTR_line),       MP_ROM_PTR(&moy_gfx_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_window), MP_ROM_PTR(&moy_gfx_blit_window_obj) },
    { MP_ROM_QSTR(MP_QSTR_scroll_rect), MP_ROM_PTR(&moy_gfx_scroll_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_indices), MP_ROM_PTR(&moy_gfx_blit_indices_obj) },
    { MP_ROM_QSTR(MP_QSTR_text),       MP_ROM_PTR(&moy_gfx_text_obj) },
    { MP_ROM_QSTR(MP_QSTR_decode_runs), MP_ROM_PTR(&moy_gfx_decode_runs_obj) },
    { MP_ROM_QSTR(MP_QSTR_crop_index), MP_ROM_PTR(&moy_gfx_crop_index_obj) },
    { MP_ROM_QSTR(MP_QSTR_pack_strip), MP_ROM_PTR(&moy_gfx_pack_strip_obj) },
};
static MP_DEFINE_CONST_DICT(moy_gfx_globals, moy_gfx_globals_table);

const mp_obj_module_t mp_module_moy_gfx = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_gfx_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_gfx, mp_module_moy_gfx);
