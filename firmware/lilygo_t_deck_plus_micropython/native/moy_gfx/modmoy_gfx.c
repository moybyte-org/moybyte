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

// fill(buf, npix, color) -- set the first `npix` pixels (clamped to capacity).
static mp_obj_t moy_gfx_fill(mp_obj_t buf_obj, mp_obj_t npix_obj, mp_obj_t color_obj) {
    size_t cap;
    uint16_t *px = moy_gfx_buf_w(buf_obj, &cap);
    mp_int_t n = mp_obj_get_int(npix_obj);
    uint16_t c = (uint16_t)(mp_obj_get_int(color_obj) & 0xFFFF);
    if (n < 0) n = 0;
    if ((size_t)n > cap) n = (mp_int_t)cap;
    for (mp_int_t i = 0; i < n; i++) px[i] = c;
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
        uint16_t *line = px + (size_t)(y + row) * (size_t)stride + (size_t)x;
        for (mp_int_t col = 0; col < w; col++) line[col] = c;
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
        uint16_t *line = dst + (size_t)y * (size_t)dw;
        for (mp_int_t x = x0; x < x1; x++) line[x] = col;
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

static const mp_rom_map_elem_t moy_gfx_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),   MP_OBJ_NEW_QSTR(MP_QSTR_moy_gfx) },
    { MP_ROM_QSTR(MP_QSTR_fill),       MP_ROM_PTR(&moy_gfx_fill_obj) },
    { MP_ROM_QSTR(MP_QSTR_fill_rect),  MP_ROM_PTR(&moy_gfx_fill_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit565),    MP_ROM_PTR(&moy_gfx_blit565_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_map),   MP_ROM_PTR(&moy_gfx_blit_map_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_batch), MP_ROM_PTR(&moy_gfx_blit_batch_obj) },
    { MP_ROM_QSTR(MP_QSTR_make_spr_gate), MP_ROM_PTR(&moy_gfx_make_spr_gate_obj) },
    { MP_ROM_QSTR(MP_QSTR_circ),       MP_ROM_PTR(&moy_gfx_circ_obj) },
    { MP_ROM_QSTR(MP_QSTR_circb),      MP_ROM_PTR(&moy_gfx_circb_obj) },
    { MP_ROM_QSTR(MP_QSTR_line),       MP_ROM_PTR(&moy_gfx_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_window), MP_ROM_PTR(&moy_gfx_blit_window_obj) },
    { MP_ROM_QSTR(MP_QSTR_blit_indices), MP_ROM_PTR(&moy_gfx_blit_indices_obj) },
    { MP_ROM_QSTR(MP_QSTR_pack_strip), MP_ROM_PTR(&moy_gfx_pack_strip_obj) },
};
static MP_DEFINE_CONST_DICT(moy_gfx_globals, moy_gfx_globals_table);

const mp_obj_module_t mp_module_moy_gfx = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_gfx_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_gfx, mp_module_moy_gfx);
