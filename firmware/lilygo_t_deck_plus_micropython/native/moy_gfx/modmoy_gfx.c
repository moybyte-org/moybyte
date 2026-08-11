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

// libmoy -- moy-spec's own raster, vendored into libmoy/ and compiled in with
// MOY_PIXEL_RGB565 (see libmoy/UPSTREAM.md). Some verbs below are thin wrappers
// over it; the rest are still moybyte's own, for reasons that file records.
#include "moy.h"

// The C draw API exported to sibling native modules (moy_lua's libmoy-direct
// verbs, #189) -- declarations only; the definitions sit with the DrawCtx
// machinery they wrap, below.
#include "moy_gfx_capi.h"

// The libmoy bridge helpers live further down, next to the per-pixel put they
// belong with; blit_map sits above them and needs them. Declared here rather
// than moved, because their neighbours are the reason they are readable.
static inline void moy_gfx_clip(mp_int_t dw, size_t cap, mp_int_t *cx0, mp_int_t *cy0,
                                mp_int_t *cx1, mp_int_t *cy1);
static inline void moy_gfx_canvas(moy_canvas *c, uint16_t *dst, mp_int_t dw,
                                  size_t cap, const uint16_t *lut,
                                  const uint8_t *palt, mp_int_t cam_x,
                                  mp_int_t cam_y, mp_int_t cx0, mp_int_t cy0,
                                  mp_int_t cx1, mp_int_t cy1);
static inline bool moy_gfx_is_moy_sheet(mp_int_t w, mp_int_t h, size_t len);

// #77: build the pixel kernel at -O3 (the ports default to -O2). In-source
// pragma, NOT cmake: source-file properties are directory-scoped and the linked
// object is compiled by the micropython.elf target, which never sees them
// (verified via build.ninja -- the cmake route left the linked object at -O2).
// -O3 not -Ofast: -ffast-math reassociates float arithmetic, and this kernel's
// job is to agree with the goldens pixel for pixel. (The specific reason used
// to be circ's sqrt; that is now an integer walk, so the raster is integer
// throughout and -Ofast would have nothing left to perturb -- the rule stands
// because "no float rewriting in a conformance-checked raster" is the rule,
// not because of one call site.) Measured NULL on the P4 (render slice is MP
// dispatch, not C compute); kept as the S3 experiment -- its slower PSRAM
// (120MHz OCT vs 200MHz HEX) and smaller flash cache make the
// compute/bandwidth split land differently there.
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
//          sheet, sheetw, sheeth, lut, palt, colorkey, scale, cx0, cy0, cx1, cy1)
// -- blit a (rw x rh) cell region of a tilemap in ONE C call (issue #32), through
// libmoy's moy_map_draw (#97). `cells` is a byte grid holding tile_id + 1 (0 =
// empty, skipped, leaving whatever was underneath -- which is what makes a
// tilemap composable with a background).
//
// IT READS THE SHEET, not a pre-baked RGB565 atlas. That atlas was this verb's
// whole design: bake the sheet once with pal and colorkey folded in, then copy
// 16-bit words. Re-measured on real glass, libmoy reading the 4-bit sheet and
// resolving through store[] is FASTER on both boards -- 0.78x on an ESP32-S3,
// 0.92x on a P4 -- because moy_spr's scale-1 path resolves the clip into loop
// bounds once per tile and the atlas never removed the per-pixel loop, only its
// lookups. map is the console's most expensive verb (640us/op, four times the
// next), so this is the largest single raster gain on either board.
static mp_obj_t moy_gfx_blit_map(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &dcap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dsx = mp_obj_get_int(a[3]), dsy = mp_obj_get_int(a[4]);
    mp_buffer_info_t cb, sb, pb, lb;
    mp_get_buffer_raise(a[5], &cb, MP_BUFFER_READ);
    mp_int_t mw = mp_obj_get_int(a[6]), mh = mp_obj_get_int(a[7]);
    mp_int_t mx = mp_obj_get_int(a[8]), my = mp_obj_get_int(a[9]);
    mp_int_t rw = mp_obj_get_int(a[10]), rh = mp_obj_get_int(a[11]);
    mp_get_buffer_raise(a[12], &sb, MP_BUFFER_READ);
    mp_int_t sheetw = mp_obj_get_int(a[13]), sheeth = mp_obj_get_int(a[14]);
    mp_get_buffer_raise(a[15], &lb, MP_BUFFER_READ);
    const uint16_t *lut = (const uint16_t *)lb.buf;
    const uint8_t *palt = NULL;
    if (a[16] != mp_const_none) {
        mp_get_buffer_raise(a[16], &pb, MP_BUFFER_READ);
        palt = (const uint8_t *)pb.buf;
    }
    mp_int_t ck = mp_obj_get_int(a[17]);
    mp_int_t scale = mp_obj_get_int(a[18]);
    mp_int_t cx0 = mp_obj_get_int(a[19]), cy0 = mp_obj_get_int(a[20]);
    mp_int_t cx1 = mp_obj_get_int(a[21]), cy1 = mp_obj_get_int(a[22]);
    if (dw <= 0 || mw <= 0 || mh <= 0 || rw <= 0 || rh <= 0) return mp_const_none;
    if ((size_t)(mw * mh) > cb.len) return mp_const_none;
    if (!moy_gfx_is_moy_sheet(sheetw, sheeth, sb.len)) return mp_const_none;
    if (mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) return mp_const_none;
    moy_gfx_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    {
        moy_canvas c;
        moy_sheet sh;
        moy_map m;
        moy_gfx_canvas(&c, dst, dw, dcap, lut, palt, 0, 0, cx0, cy0, cx1, cy1);
        moy_sheet_init(&sh, (uint8_t *)sb.buf);
        moy_map_init(&m, (uint8_t *)cb.buf, (int)mw, (int)mh);
        // Camera is ZERO here on purpose: the caller has already resolved the
        // region's screen position into dsx/dsy (this destination may be a
        // hidden cache layer rather than the framebuffer, where a camera would
        // mean nothing), so applying one again would double the offset.
        moy_map_draw(&c, &m, &sh, (int)mx, (int)my, (int)rw, (int)rh,
                     (int)dsx, (int)dsy, (int)ck, (int)scale);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_map_obj, 23, 23, moy_gfx_blit_map);

// blit_batch(dst, dw, dh, items, sheet, sheetw, sheeth, lut, palt, key, scale,
//            cam_x, cam_y, cx0, cy0, cx1, cy1) -- draw N sprite tiles in ONE C call
// (issue #43): the sprite analogue of blit_map (#32). `items` is a list/tuple of
// (tile, x, y) or (tile, x, y, flip) tuples; each is drawn from the INDEX sheet
// through libmoy's moy_spr (#97), resolving colour via `lut` (the 64-entry table
// with pal folded in) and treating a pixel as transparent if it equals `key` or
// its `palt` entry is set. `flip` is TIC-80's (0=none, 1=h, 2=v, 3=both).
// Collapses N per-sprite MicroPython->C calls into one walk, which is the
// device's draw-call bottleneck -- that part is unchanged and is the whole
// reason the verb exists.
//
// ARRAY MODE (#63 spr_gate): `items` may instead be the canvas batch array -- an
// array('h') laid out [next, colorkey, scale, token, (tile x y flip)*N...] with
// items starting at index 4 and `next` = 4 + 4*N. This is the queue the spr_gate
// fast path fills from C with ZERO Python-object churn; passing it here draws the
// whole run without ever materialising tuples. Detected via the buffer protocol
// (a list/tuple of tuples has no buffer -> classic mode).
static mp_obj_t moy_gfx_blit_batch(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t dcap;
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
    mp_buffer_info_t sb, lb, pb;
    mp_get_buffer_raise(a[4], &sb, MP_BUFFER_READ);
    mp_int_t sheetw = mp_obj_get_int(a[5]), sheeth = mp_obj_get_int(a[6]);
    mp_get_buffer_raise(a[7], &lb, MP_BUFFER_READ);
    const uint16_t *lut = (const uint16_t *)lb.buf;
    const uint8_t *palt = NULL;
    if (a[8] != mp_const_none) {
        mp_get_buffer_raise(a[8], &pb, MP_BUFFER_READ);
        palt = (const uint8_t *)pb.buf;
    }
    mp_int_t key = mp_obj_get_int(a[9]);
    mp_int_t scale = mp_obj_get_int(a[10]);
    mp_int_t cam_x = mp_obj_get_int(a[11]);
    mp_int_t cam_y = mp_obj_get_int(a[12]);
    mp_int_t cx0 = mp_obj_get_int(a[13]);
    mp_int_t cy0 = mp_obj_get_int(a[14]);
    mp_int_t cx1 = mp_obj_get_int(a[15]);
    mp_int_t cy1 = mp_obj_get_int(a[16]);
    if (dw <= 0 || dh <= 0) return mp_const_none;
    if (scale < 1) scale = 1;
    if (!moy_gfx_is_moy_sheet(sheetw, sheeth, sb.len)) return mp_const_none;
    moy_gfx_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    {
        // ONE canvas for the whole batch -- which is the point of this verb. The
        // #43 protocol exists because N per-sprite MicroPython->C calls were the
        // device's draw-call bottleneck; that is unchanged, and what moved is
        // only what happens inside: libmoy's moy_spr reading the INDEX sheet,
        // rather than a copy out of a pre-baked RGB565 atlas.
        //
        // Re-measured on real glass, the atlas LOSES: 0.79x on an ESP32-S3 and
        // 0.83x on a P4. It never removed the per-pixel loop, only its two
        // lookups, and moy_spr's scale-1 path buys more back by resolving the
        // clip into loop bounds once per sprite. Losing it also hands back its
        // 64 KB, which on the S3 is the scarcest memory there is.
        //
        // The camera goes in the CANVAS rather than being subtracted per item:
        // moy_spr applies it, and its fast path hoists it out of the pixel loop.
        moy_canvas c;
        moy_sheet sh;
        moy_gfx_canvas(&c, dst, dw, dcap, lut, palt,
                       cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_sheet_init(&sh, (uint8_t *)sb.buf);
        for (size_t i = 0; i < n_items; i++) {
            mp_int_t tid, ix, iy, flip;
            if (qitems != NULL) {
                const int16_t *it = qitems + i * 4u;   // (tile, x, y, flip) quad
                tid = it[0];
                ix = it[1];
                iy = it[2];
                flip = it[3];
            } else {
                size_t ilen;
                mp_obj_t *ielem;
                mp_obj_get_array(item_arr[i], &ilen, &ielem);  // (tile, x, y[, flip])
                if (ilen < 3) continue;
                tid = mp_obj_get_int(ielem[0]);
                ix = mp_obj_get_int(ielem[1]);
                iy = mp_obj_get_int(ielem[2]);
                flip = (ilen > 3) ? mp_obj_get_int(ielem[3]) : 0;
            }
            // moy_spr refuses an out-of-range tile itself (SPEC.md: a blank tile
            // is legal, a bad id is not an error), so no guard is needed here.
            moy_spr(&c, &sh, (int)tid, (int)ix, (int)iy,
                    (int)key, (int)scale, (int)flip);
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_blit_batch_obj, 17, 17, moy_gfx_blit_batch);

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

// -- the libmoy bridge -------------------------------------------------------
//
// libmoy holds camera, clip and the index->pixel table in a moy_canvas; this
// module passes them as scalars on every call. Rather than invert either side,
// a canvas is BORROWED for the duration of one call: it points at the caller's
// buffer and is filled in from the same arguments the hand-written kernel used.
//
// The cost is the store[] copy -- 128 bytes for the 64-entry table. That is the
// whole bridge, and it is why this is affordable per call: no allocation, no
// palette rebuild (the LUT arrives already folded through `pal`, which is
// exactly what libmoy's store[] is), nothing that scales with the pixels drawn.
//
// `dh` is derived from the buffer capacity rather than trusted, the same way
// moy_gfx_clip does it: a canvas whose h exceeds its buffer would let libmoy's
// own clamping write past the end.
static inline void moy_gfx_canvas(moy_canvas *c, uint16_t *dst, mp_int_t dw,
                                  size_t cap, const uint16_t *lut,
                                  const uint8_t *palt, mp_int_t cam_x,
                                  mp_int_t cam_y, mp_int_t cx0, mp_int_t cy0,
                                  mp_int_t cx1, mp_int_t cy1) {
    c->pix = dst;
    c->w = (int)dw;
    c->h = (int)(cap / (size_t)dw);
    c->cam_x = (int)cam_x;
    c->cam_y = (int)cam_y;
    c->clip_x0 = (int)cx0;
    c->clip_y0 = (int)cy0;
    c->clip_x1 = (int)cx1;
    c->clip_y1 = (int)cy1;
    // pal is already folded into `lut` by the Python side (_wire_pal), so
    // store[] IS that table and pal[] stays identity -- libmoy reads store[]
    // on every pixel and pal[] only when rebuilding, which never happens here.
    for (int i = 0; i < MOY_PALETTE; i++) {
        c->pal[i] = (uint8_t)i;
        c->store[i] = lut[i];
        c->wire[i] = lut[i];
    }
    if (palt != NULL) {
        memcpy(c->palt, palt, MOY_PALETTE);
    } else {
        memset(c->palt, 0, MOY_PALETTE);
    }
}

// libmoy addresses a sheet with SPEC.md 3.2's FIXED geometry (128 x 256, 16
// tiles per row) rather than a width passed per call, so handing it anything
// else would read at the wrong stride or past the end. A cart sheet is exactly
// that shape -- Project._build_sheet makes a 16x32 SpriteSheet and from_hex
// pads a short (pre-512) blob into the top half -- so this is a guard against
// a wrong caller, not a case that happens. Silent rather than raising, like the
// other malformed-input guards here: a draw verb that throws mid-frame takes
// the cart down.
static inline bool moy_gfx_is_moy_sheet(mp_int_t w, mp_int_t h, size_t len) {
    return w == MOY_SHEET_W && h == MOY_SHEET_H &&
           len >= (size_t)(MOY_SHEET_W * MOY_SHEET_H);
}

// A canvas for the verbs that take ONE already-resolved colour. moybyte's
// kernels are handed a 565 word; libmoy's take an index and look it up. Filling
// the table with that one word and passing index 0 bridges the two exactly --
// libmoy reads store[col & 63] and cannot tell that the other 63 slots agree.
static inline void moy_gfx_canvas_solid(moy_canvas *c, uint16_t *dst, mp_int_t dw,
                                        size_t cap, uint16_t col, mp_int_t cam_x,
                                        mp_int_t cam_y, mp_int_t cx0, mp_int_t cy0,
                                        mp_int_t cx1, mp_int_t cy1) {
    uint16_t lut[MOY_PALETTE];
    for (int i = 0; i < MOY_PALETTE; i++) lut[i] = col;
    moy_gfx_canvas(c, dst, dw, cap, lut, NULL, cam_x, cam_y, cx0, cy0, cx1, cy1);
}

static inline void moy_gfx_put(uint16_t *dst, mp_int_t dw, mp_int_t x, mp_int_t y,
                              uint16_t col, mp_int_t cam_x, mp_int_t cam_y,
                              mp_int_t cx0, mp_int_t cy0, mp_int_t cx1, mp_int_t cy1) {
    x -= cam_x;
    y -= cam_y;
    if (x < cx0 || x >= cx1 || y < cy0 || y >= cy1) return;
    dst[(size_t)y * (size_t)dw + (size_t)x] = col;
}

// fill_spans(dst, dw, dh, arr, n, ox, oy, col, pal, cam_x, cam_y, cx0, cy0, cx1, cy1)
// -- the #163 span batch WITHOUT a DrawCtx (#167).
//
// Why this exists next to DrawCtx.fill_rects: the T-Deck ROOT canvas never
// installs the draw gates (its _fill has to poke the SRAM-bounce flush pump
// between native ops, #66, and a C gate has no cheap way back into Python), so
// on that board DrawCtx.fill_rects is unreachable and DeviceCanvas.fill_rects
// falls back to ONE INTERPRETER rect() PER SPAN -- precisely the dispatch cost
// the batch exists to delete. Measured on glass: a 160-span software-3D frame
// cost ~10ms of Python there while the gated P4 ran the same batch in ~1ms.
//
// So this takes the buffer, camera and clip as plain arguments exactly like
// circ/line above, which makes it work on EVERY canvas, gated or not.
// `col` >= 0 is an already-resolved RGB565 override for every quad; otherwise
// quad slot 4 is a palette index into `pal` (a 64-entry RGB565 table).
static mp_obj_t moy_gfx_fill_spans(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t dh = mp_obj_get_int(a[2]);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[3], &bi, MP_BUFFER_READ);
    const int16_t *q = (const int16_t *)bi.buf;
    mp_int_t nmax = (mp_int_t)(bi.len / (2 * 5));
    mp_int_t n = mp_obj_get_int(a[4]);
    if (n < 0 || n > nmax) n = nmax;
    mp_int_t ox = mp_obj_get_int(a[5]);
    mp_int_t oy = mp_obj_get_int(a[6]);
    mp_int_t cov = mp_obj_get_int(a[7]);
    const uint16_t *pal = NULL;
    if (a[8] != mp_const_none) {
        mp_buffer_info_t pb;
        mp_get_buffer_raise(a[8], &pb, MP_BUFFER_READ);
        pal = (const uint16_t *)pb.buf;
    }
    mp_int_t cam_x = mp_obj_get_int(a[9]);
    mp_int_t cam_y = mp_obj_get_int(a[10]);
    mp_int_t cx0 = mp_obj_get_int(a[11]);
    mp_int_t cy0 = mp_obj_get_int(a[12]);
    mp_int_t cx1 = mp_obj_get_int(a[13]);
    mp_int_t cy1 = mp_obj_get_int(a[14]);
    (void)dh;
    if (dw <= 0 || (cov < 0 && pal == NULL)) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    for (mp_int_t i = 0; i < n; i++) {
        const int16_t *p = q + i * 5;
        uint16_t col = (cov >= 0) ? (uint16_t)cov : pal[p[4] & 63];
        mp_int_t x0 = (mp_int_t)p[0] + ox - cam_x;
        mp_int_t y0 = (mp_int_t)p[1] + oy - cam_y;
        mp_int_t x1 = x0 + (mp_int_t)p[2];
        mp_int_t y1 = y0 + (mp_int_t)p[3];
        if (x0 < cx0) x0 = cx0;
        if (y0 < cy0) y0 = cy0;
        if (x1 > cx1) x1 = cx1;
        if (y1 > cy1) y1 = cy1;
        if (x1 <= x0 || y1 <= y0) continue;
        size_t run = (size_t)(x1 - x0);
        uint16_t *row = dst + (size_t)y0 * (size_t)dw + (size_t)x0;
        for (mp_int_t y = y0; y < y1; y++) {
            moy_gfx_fill_run(row, run, col);
            row += (size_t)dw;
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_fill_spans_obj, 15, 15,
                                           moy_gfx_fill_spans);

// --- the SPEC.md 6.1 3D verbs (#167, shape B) --------------------------------
//
// The geometry below is line-for-line libmoy's (moy-spec/libmoy/src/), which is
// what the conformance goldens pin; only the WRITE differs -- these resolve the
// palette index at draw time and store RGB565, which is this canvas's format.
// Same standalone shape as circ/fill_spans: buffer, camera and clip as plain
// args, so they work on every canvas, gated or not.

// tri(dst, dw, dh, x1, y1, x2, y2, x3, y3, color, cam_x, cam_y, cx0, cy0, cx1, cy1)
// -- FILLED triangle, whole scanline walk in C: sort by y, walk both edges with
// FLOOR division (C truncation differs by one for negative numerators -- a whole
// column on a leaning edge), one clipped span per scanline. Replaces the Python
// tri_spans walk that measured 7.5ms/op on this board's bench (#66) -- the span
// ARITHMETIC was the cost, not the fill, which is why the kernel owns both.
static mp_obj_t moy_gfx_tri(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_int_t x1 = mp_obj_get_int(a[3]), y1 = mp_obj_get_int(a[4]);
    mp_int_t x2 = mp_obj_get_int(a[5]), y2 = mp_obj_get_int(a[6]);
    mp_int_t x3 = mp_obj_get_int(a[7]), y3 = mp_obj_get_int(a[8]);
    uint16_t col = (uint16_t)mp_obj_get_int(a[9]);
    mp_int_t cam_x = mp_obj_get_int(a[10]), cam_y = mp_obj_get_int(a[11]);
    mp_int_t cx0 = mp_obj_get_int(a[12]), cy0 = mp_obj_get_int(a[13]);
    mp_int_t cx1 = mp_obj_get_int(a[14]), cy1 = mp_obj_get_int(a[15]);
    if (dw <= 0) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    // libmoy's moy_tri (#97). This verb used to be a hand transcription of it;
    // the transcription is gone rather than kept beside it, because two copies
    // that have to agree is the arrangement this is replacing.
    //
    // The colour arrives already resolved to a 565 word, so the canvas gets a
    // one-entry table and index 0 -- libmoy writes store[col & 63] per span and
    // does not care that the other 63 slots are the same word.
    {
        moy_canvas c;
        moy_gfx_canvas_solid(&c, dst, dw, cap, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_tri(&c, (int)x1, (int)y1, (int)x2, (int)y2, (int)x3, (int)y3, 0);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_tri_obj, 16, 16, moy_gfx_tri);

// sspr(dst, dw, dh, sheet, sheetw, sheeth, sx, sy, sw, sh, dx, dy, ddw, ddh,
//      colorkey, flip, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1)
// -- stretch a sheet PIXEL region to an arbitrary rect, nearest-neighbour,
// source texel (i*sw)//dw exactly as the host canvas computes it. `sheet` is
// the INDEX bytearray (SpriteSheet.pix); `lut` the 64-entry RGB565 table with
// pal already folded in (_wire_pal); `palt` the 64-byte transparency mask or
// None. colorkey compares against the RAW sheet index, before masking, exactly
// like the Python lane. Out-of-range sheet reads sample 0, like pget.
static mp_obj_t moy_gfx_sspr(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_buffer_info_t sb, pb, lb;
    mp_get_buffer_raise(a[3], &sb, MP_BUFFER_READ);
    const uint8_t *sheet = (const uint8_t *)sb.buf;
    mp_int_t sheetw = mp_obj_get_int(a[4]), sheeth = mp_obj_get_int(a[5]);
    mp_int_t sx = mp_obj_get_int(a[6]), sy = mp_obj_get_int(a[7]);
    mp_int_t sw = mp_obj_get_int(a[8]), sh = mp_obj_get_int(a[9]);
    mp_int_t dx = mp_obj_get_int(a[10]), dy = mp_obj_get_int(a[11]);
    mp_int_t ddw = mp_obj_get_int(a[12]), ddh = mp_obj_get_int(a[13]);
    mp_int_t ck = mp_obj_get_int(a[14]);
    mp_int_t flip = mp_obj_get_int(a[15]);
    mp_get_buffer_raise(a[16], &lb, MP_BUFFER_READ);
    const uint16_t *lut = (const uint16_t *)lb.buf;
    const uint8_t *palt = NULL;
    if (a[17] != mp_const_none) {
        mp_get_buffer_raise(a[17], &pb, MP_BUFFER_READ);
        palt = (const uint8_t *)pb.buf;
    }
    mp_int_t cam_x = mp_obj_get_int(a[18]), cam_y = mp_obj_get_int(a[19]);
    mp_int_t cx0 = mp_obj_get_int(a[20]), cy0 = mp_obj_get_int(a[21]);
    mp_int_t cx1 = mp_obj_get_int(a[22]), cy1 = mp_obj_get_int(a[23]);
    if (dw <= 0 || sw <= 0 || sh <= 0 || ddw <= 0 || ddh <= 0) return mp_const_none;
    if (!moy_gfx_is_moy_sheet(sheetw, sheeth, sb.len)) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    // libmoy's moy_sspr (#97).
    {
        moy_canvas c;
        moy_sheet s;
        moy_gfx_canvas(&c, dst, dw, cap, lut, palt,
                       cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_sheet_init(&s, (uint8_t *)sheet);
        moy_sspr(&c, &s, (int)sx, (int)sy, (int)sw, (int)sh,
                 (int)dx, (int)dy, (int)ddw, (int)ddh, (int)ck, (int)flip);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_sspr_obj, 24, 24, moy_gfx_sspr);

// tline(dst, dw, dh, cells, mw, mh, sheet, sheetw, sheeth, x0, y0, x1, y1,
//       u, v, du, dv, colorkey, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1)
// -- SPEC.md 6.1's textured line: exactly line()'s Bresenham pixels, sampling
// the MAP as a virtual texture in 16.16 fixed point. The start and step are
// reduced ONCE ((a + n*b) mod T == ((a mod T) + n*(b mod T)) mod T) so the
// loop needs no division at all -- the naive per-texel modulo measured
// ~660ns/texel on this board (moy-spec 06fe1ba) and this shape ~210ns. `cells`
// is the TileMap bytearray (id+1, 0 = empty); coordinates wrap modulo the
// map's pixel size; the cursor advances for EVERY walked pixel, drawn or not.
static mp_obj_t moy_gfx_tline(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    size_t cap;
    uint16_t *dst = moy_gfx_buf_w(a[0], &cap);
    mp_int_t dw = mp_obj_get_int(a[1]);
    mp_buffer_info_t cb, sb, pb, lb;
    mp_get_buffer_raise(a[3], &cb, MP_BUFFER_READ);
    const uint8_t *cells = (const uint8_t *)cb.buf;
    mp_int_t mw = mp_obj_get_int(a[4]), mh = mp_obj_get_int(a[5]);
    mp_get_buffer_raise(a[6], &sb, MP_BUFFER_READ);
    const uint8_t *sheet = (const uint8_t *)sb.buf;
    mp_int_t sheetw = mp_obj_get_int(a[7]), sheeth = mp_obj_get_int(a[8]);
    mp_int_t x0 = mp_obj_get_int(a[9]), y0 = mp_obj_get_int(a[10]);
    mp_int_t x1 = mp_obj_get_int(a[11]), y1 = mp_obj_get_int(a[12]);
    int32_t u = (int32_t)mp_obj_get_int(a[13]), v = (int32_t)mp_obj_get_int(a[14]);
    int32_t du = (int32_t)mp_obj_get_int(a[15]), dv = (int32_t)mp_obj_get_int(a[16]);
    mp_int_t ck = mp_obj_get_int(a[17]);
    mp_get_buffer_raise(a[18], &lb, MP_BUFFER_READ);
    const uint16_t *lut = (const uint16_t *)lb.buf;
    const uint8_t *palt = NULL;
    if (a[19] != mp_const_none) {
        mp_get_buffer_raise(a[19], &pb, MP_BUFFER_READ);
        palt = (const uint8_t *)pb.buf;
    }
    mp_int_t cam_x = mp_obj_get_int(a[20]), cam_y = mp_obj_get_int(a[21]);
    mp_int_t cx0 = mp_obj_get_int(a[22]), cy0 = mp_obj_get_int(a[23]);
    mp_int_t cx1 = mp_obj_get_int(a[24]), cy1 = mp_obj_get_int(a[25]);
    if (dw <= 0 || mw <= 0 || mh <= 0) return mp_const_none;
    if ((size_t)(mw * mh) > cb.len || (size_t)(sheetw * sheeth) > sb.len)
        return mp_const_none;
    if (!moy_gfx_is_moy_sheet(sheetw, sheeth, sb.len)) return mp_const_none;
    if (mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) return mp_const_none;
    moy_gfx_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    // libmoy's moy_tline (#97). The hand transcription this replaces passed
    // every conformance scene on the HOST and failed provisional_tline on the
    // board by 2773 pixels -- which is the whole argument for calling the
    // spec's raster instead of keeping a copy of it in step by hand.
    {
        moy_canvas c;
        moy_sheet s;
        moy_map m;
        moy_gfx_canvas(&c, dst, dw, cap, lut, palt,
                       cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_sheet_init(&s, (uint8_t *)sheet);
        moy_map_init(&m, (uint8_t *)cells, (int)mw, (int)mh);
        moy_tline(&c, &s, &m, (int)x0, (int)y0, (int)x1, (int)y1,
                  u, v, du, dv, (int)ck);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_tline_obj, 26, 26, moy_gfx_tline);

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
    // libmoy's moy_circ (#97). It walks the half-width as an integer rather
    // than calling sqrt per row -- which is where the 8.4x came from when that
    // algorithm was first adopted by hand here (10,880us -> 1,297us on a P4).
    // This is the same code, now as a call rather than a copy of it.
    {
        moy_canvas c;
        moy_gfx_canvas_solid(&c, dst, dw, cap, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_circ(&c, (int)cx, (int)cy, (int)r, 0);
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
    // libmoy's moy_circb (#97) -- the midpoint error term, eight octant points
    // per step. A fill and an outline are different rasterizations, which is
    // why the spec keeps them as separate verbs.
    {
        moy_canvas c;
        moy_gfx_canvas_solid(&c, dst, dw, cap, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_circb(&c, (int)cx, (int)cy, (int)r, 0);
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
    // libmoy's moy_line (#97): Bresenham, with its axis-aligned and
    // wholly-visible fast paths -- the shapes carts actually draw most.
    {
        moy_canvas c;
        moy_gfx_canvas_solid(&c, dst, dw, cap, col, cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_line(&c, (int)x0, (int)y0, (int)x1, (int)y1, 0);
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
    // THE CART's text is scale 1 and goes through libmoy (#97). SPEC.md 6 is
    // explicit that "print has no scale parameter; text is always 8px", and
    // moybyte agrees -- DeviceCanvas.print hardcodes 1 and documents its own
    // `scale` argument as accepted-and-ignored. So scale > 1 is never a cart:
    // it is this console's CHROME at the #39 system font size, which is host
    // business (SPEC.md 0) and keeps the kernel below.
    //
    // This crossed, was REVERTED on an S3 number of 1.21x, and crossed again
    // when that number was re-measured at 1.04x against current libmoy -- the
    // 1.21x had been taken before moy_print grew its whole-column off-clip
    // early-out. See libmoy/UPSTREAM.md; the reversal is recorded there rather
    // than tidied away.
    //
    // libmoy uses its own compiled-in font rather than the blob passed here.
    // Both are petme128 and SPEC.md 6 requires them byte-identical or text
    // conformance fails -- so the `text` and `text_bytes` scenes are what
    // license this, not the assumption.
    if (mp_obj_get_int(a[9]) == 1) {
        moy_canvas c;
        mp_int_t cam_x = mp_obj_get_int(a[10]), cam_y = mp_obj_get_int(a[11]);
        mp_int_t cx0 = mp_obj_get_int(a[12]), cy0 = mp_obj_get_int(a[13]);
        mp_int_t cx1 = mp_obj_get_int(a[14]), cy1 = mp_obj_get_int(a[15]);
        if (dw <= 0) return mp_const_none;
        moy_gfx_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
        moy_gfx_canvas_solid(&c, dst, dw, dcap,
                             (uint16_t)(mp_obj_get_int(a[6]) & 0xFFFF),
                             cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_print(&c, (const uint8_t *)sbi.buf, sbi.len,
                  mp_obj_get_int(a[4]), mp_obj_get_int(a[5]), 0);
        return mp_const_none;
    }
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
    mp_obj_t pump;           // #163 door 1: bounce-pump upcall, or mp_const_none
    int32_t pump_ctr;        // gated ops until the next pump upcall
    uint16_t *px;            // destination (gc never moves objects)
    size_t cap;              // destination capacity in pixels
    int32_t *st;
    uint16_t *pal;
    size_t npal;
    int16_t *batch;          // sprite queue, or NULL
    size_t batch_len;        // queue capacity in int16 slots (clamps q[0])
    const uint8_t *font;
    mp_int_t nglyphs;
    mp_int_t first;
    // #67 stage-1 (moycore): the C-side batch source -- the cart's INDEXED
    // sheet + palt, registered by the Lua glue (set_batch_src) so a run break
    // or the #63 order-rule flush never upcalls (moy_gfx_capi_flush_batch).
    // Objects held so the gc keeps the buffers alive; set_batch_src(None)
    // clears. bpalt may be NULL (all-opaque), like blit_batch's None.
    mp_obj_t bsrc_obj;
    mp_obj_t bpalt_obj;
    const uint8_t *bsrc;
    const uint8_t *bpalt;
    // #67 stage-1b: the tilemap source for the direct tline (set_map_src) --
    // same live-read/held-object rules as the batch source above.
    mp_obj_t msrc_obj;
    const uint8_t *msrc;
    mp_int_t msrc_w, msrc_h;     // in TILES, like TileMap.w/h
} moy_gfx_draw_ctx_obj_t;

// #163 door 1: how many gated ops run between two bounce-pump upcalls on a
// canvas that registered one (set_pump -- the T-Deck ROOT canvas). The
// per-op Python poke was the reason the gates were refused there; the pump
// only FEEDS the in-flight partial flush, so its cadence just has to beat
// the SPI draining a bounce strip -- at ~8us/gated-op, 16 ops is ~128us
// between pokes, denser than the per-fill pokes were at the old ~65us/call
// whenever more than 2 fills run. A starved pump costs a longer synchronous
// tail in comp.flush(), never a glitch.
#define GATE_PUMP_EVERY 16

static inline void gate_pump(moy_gfx_draw_ctx_obj_t *c, mp_int_t nops) {
    if (c->pump == mp_const_none) return;
    c->pump_ctr -= (int32_t)nops;
    if (c->pump_ctr > 0) return;
    c->pump_ctr = GATE_PUMP_EVERY;
    mp_call_function_0(c->pump);
}

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

// set_pump(fn): register the bounce-pump upcall (#163 door 1 -- the T-Deck
// root canvas; every other canvas never calls this). fn is called every
// GATE_PUMP_EVERY gated ops; None unregisters. Its PRESENCE is also the
// version probe _install_draw_gates uses before gating a pumped canvas.
static mp_obj_t moy_gfx_draw_ctx_set_pump(mp_obj_t self_in, mp_obj_t fn) {
    moy_gfx_draw_ctx_obj_t *c = MP_OBJ_TO_PTR(self_in);
    c->pump = fn;
    c->pump_ctr = GATE_PUMP_EVERY;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(moy_gfx_draw_ctx_set_pump_obj,
                                 moy_gfx_draw_ctx_set_pump);

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
    gate_pump(c, n);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_draw_ctx_fill_rects_obj,
                                           6, 6, moy_gfx_draw_ctx_fill_rects);

// set_batch_src(sheet_pix, sheetw, sheeth, palt) / set_batch_src(None):
// register (or clear) the C-side sprite-batch source (#67 stage-1). The sheet
// must be moy-shaped (the same is_moy_sheet gate blit_batch applies -- a
// refusal raises so the caller can fall back to the upcall protocol); `palt`
// is the canvas's 64-byte transparency table or None. The registered OBJECTS
// are held on the ctx, so live in-place edits (a paint stroke mid-run) are
// seen by the very next flush -- the buffers are read fresh each time.
static mp_obj_t moy_gfx_draw_ctx_set_batch_src(size_t n_args, const mp_obj_t *a) {
    moy_gfx_draw_ctx_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    if (n_args == 2 && a[1] == mp_const_none) {
        c->bsrc_obj = mp_const_none;
        c->bpalt_obj = mp_const_none;
        c->bsrc = NULL;
        c->bpalt = NULL;
        return mp_const_none;
    }
    if (n_args != 5) {
        mp_raise_TypeError(MP_ERROR_TEXT("set_batch_src(pix, w, h, palt)"));
    }
    mp_buffer_info_t sb;
    mp_get_buffer_raise(a[1], &sb, MP_BUFFER_READ);
    mp_int_t w = mp_obj_get_int(a[2]);
    mp_int_t h = mp_obj_get_int(a[3]);
    if (!moy_gfx_is_moy_sheet(w, h, sb.len)) {
        mp_raise_ValueError(MP_ERROR_TEXT("set_batch_src: not a moy sheet"));
    }
    const uint8_t *palt = NULL;
    mp_obj_t palt_obj = mp_const_none;
    if (a[4] != mp_const_none) {
        mp_buffer_info_t pb;
        mp_get_buffer_raise(a[4], &pb, MP_BUFFER_READ);
        if (pb.len < 64) {
            mp_raise_ValueError(MP_ERROR_TEXT("set_batch_src: palt too small"));
        }
        palt = (const uint8_t *)pb.buf;
        palt_obj = a[4];
    }
    c->bsrc_obj = a[1];
    c->bpalt_obj = palt_obj;
    c->bsrc = (const uint8_t *)sb.buf;
    c->bpalt = palt;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_draw_ctx_set_batch_src_obj,
                                           2, 5, moy_gfx_draw_ctx_set_batch_src);

// set_map_src(cells, mw, mh) / set_map_src(None): register (or clear) the
// tilemap the direct tline samples (#67 stage-1b). `cells` is the TileMap
// bytearray (id+1, 0 = empty), mw/mh in TILES -- the same shape the tline
// verb takes, with its guards applied at registration so the hot path never
// checks. Held + read live, like set_batch_src.
static mp_obj_t moy_gfx_draw_ctx_set_map_src(size_t n_args, const mp_obj_t *a) {
    moy_gfx_draw_ctx_obj_t *c = MP_OBJ_TO_PTR(a[0]);
    if (n_args == 2 && a[1] == mp_const_none) {
        c->msrc_obj = mp_const_none;
        c->msrc = NULL;
        c->msrc_w = 0;
        c->msrc_h = 0;
        return mp_const_none;
    }
    if (n_args != 4) {
        mp_raise_TypeError(MP_ERROR_TEXT("set_map_src(cells, mw, mh)"));
    }
    mp_buffer_info_t cb;
    mp_get_buffer_raise(a[1], &cb, MP_BUFFER_READ);
    mp_int_t mw = mp_obj_get_int(a[2]);
    mp_int_t mh = mp_obj_get_int(a[3]);
    if (mw <= 0 || mh <= 0 || (size_t)(mw * mh) > cb.len
        || mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) {
        mp_raise_ValueError(MP_ERROR_TEXT("set_map_src: not a moy map"));
    }
    c->msrc_obj = a[1];
    c->msrc = (const uint8_t *)cb.buf;
    c->msrc_w = mw;
    c->msrc_h = mh;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_gfx_draw_ctx_set_map_src_obj,
                                           2, 4, moy_gfx_draw_ctx_set_map_src);

static const mp_rom_map_elem_t moy_gfx_draw_ctx_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_set_buf), MP_ROM_PTR(&moy_gfx_draw_ctx_set_buf_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_pump), MP_ROM_PTR(&moy_gfx_draw_ctx_set_pump_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_batch_src),
      MP_ROM_PTR(&moy_gfx_draw_ctx_set_batch_src_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_map_src),
      MP_ROM_PTR(&moy_gfx_draw_ctx_set_map_src_obj) },
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
        gate_pump(c, 1);
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
        gate_pump(c, 1);
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
    gate_pump(c, 1);
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
    c->pump = mp_const_none;   // #163 door 1: set_pump registers one (root canvas)
    c->pump_ctr = GATE_PUMP_EVERY;
    c->px = NULL;
    c->cap = 0;
    c->st = (int32_t *)sbi.buf;
    c->pal = (uint16_t *)pbi.buf;
    c->npal = pbi.len / 2u;
    c->batch = NULL;
    c->batch_len = 0;
    if (a[3] != mp_const_none) {
        mp_buffer_info_t bbi;
        if (mp_get_buffer(a[3], &bbi, MP_BUFFER_RW) && bbi.len >= 2 * 4) {
            c->batch = (int16_t *)bbi.buf;
            c->batch_len = bbi.len / 2u;
        }
    }
    c->bsrc_obj = mp_const_none;
    c->bpalt_obj = mp_const_none;
    c->bsrc = NULL;
    c->bpalt = NULL;
    c->msrc_obj = mp_const_none;
    c->msrc = NULL;
    c->msrc_w = 0;
    c->msrc_h = 0;
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

// --- the exported C draw API (moy_gfx_capi.h -- moy_lua's direct verbs) -----
//
// Thin non-static wrappers over the SAME machinery the gates and MP verbs use:
// gate_fill for the fill family, moy_gfx_canvas_solid + the libmoy kernels for
// the shapes, moy_gfx_text_raw for print (the gate's lane -- at cart scale it
// and libmoy's moy_print are pinned byte-identical by the text conformance
// scenes). Nothing here parses args, upcalls, allocates, or raises: the
// callers run inside Lua C functions where an MP exception must not longjmp.

moy_gfx_draw_ctx_t *moy_gfx_capi_ctx(mp_obj_t obj) {
    if (!mp_obj_is_type(obj, &moy_gfx_draw_ctx_type)) {
        return NULL;
    }
    return MP_OBJ_TO_PTR(obj);
}

bool moy_gfx_capi_ready(const moy_gfx_draw_ctx_t *c) {
    return c->px != NULL && c->st[ST_W] > 0;
}

bool moy_gfx_capi_batch_pending(const moy_gfx_draw_ctx_t *c) {
    return c->batch != NULL && c->batch[0] > 4;
}

mp_obj_t moy_gfx_capi_canvas(const moy_gfx_draw_ctx_t *c) {
    return c->canvas;
}

bool moy_gfx_capi_prof(const moy_gfx_draw_ctx_t *c) {
    return c->st[ST_PROF] != 0;
}

mp_obj_t moy_gfx_capi_pump_due(moy_gfx_draw_ctx_t *c, int nops) {
    if (c->pump == mp_const_none) {
        return MP_OBJ_NULL;
    }
    c->pump_ctr -= (int32_t)nops;
    if (c->pump_ctr > 0) {
        return MP_OBJ_NULL;
    }
    c->pump_ctr = GATE_PUMP_EVERY;
    return c->pump;
}

static inline uint16_t capi_col(const moy_gfx_draw_ctx_t *c, int ci) {
    return c->pal[(size_t)(ci & 63) % c->npal];
}

void moy_gfx_capi_fill(moy_gfx_draw_ctx_t *c, int x, int y, int w, int h, int ci) {
    gate_fill(c, x, y, w, h, capi_col(c, ci));
    c->st[ST_N_FILL]++;
}

void moy_gfx_capi_rectb(moy_gfx_draw_ctx_t *c, int x, int y, int w, int h, int ci) {
    // The same four clipped fills the rect gate and the Python path issue.
    uint16_t col = capi_col(c, ci);
    gate_fill(c, x, y, w, 1, col);
    gate_fill(c, x, y + h - 1, w, 1, col);
    gate_fill(c, x, y, 1, h, col);
    gate_fill(c, x + w - 1, y, 1, h, col);
    c->st[ST_N_FILL]++;
}

// Borrow a solid-colour libmoy canvas from the ctx state, exactly the way the
// MP shape verbs build one from their scalar args (clip clamped to the buffer
// first). False when the ctx has no usable destination.
static bool capi_solid(moy_gfx_draw_ctx_t *c, moy_canvas *mc, int ci) {
    const int32_t *st = c->st;
    mp_int_t dw = st[ST_W];
    if (dw <= 0 || c->px == NULL) {
        return false;
    }
    mp_int_t cx0 = st[ST_CX0], cy0 = st[ST_CY0];
    mp_int_t cx1 = st[ST_CX1], cy1 = st[ST_CY1];
    moy_gfx_clip(dw, c->cap, &cx0, &cy0, &cx1, &cy1);
    moy_gfx_canvas_solid(mc, c->px, dw, c->cap, capi_col(c, ci),
                         st[ST_CAM_X], st[ST_CAM_Y], cx0, cy0, cx1, cy1);
    return true;
}

void moy_gfx_capi_line(moy_gfx_draw_ctx_t *c, int x0, int y0, int x1, int y1,
                       int ci) {
    moy_canvas mc;
    if (capi_solid(c, &mc, ci)) {
        moy_line(&mc, x0, y0, x1, y1, 0);
    }
}

void moy_gfx_capi_circ(moy_gfx_draw_ctx_t *c, int cx, int cy, int r, int ci,
                       bool outline) {
    moy_canvas mc;
    if (r < 0 || !capi_solid(c, &mc, ci)) {
        return;
    }
    if (outline) {
        moy_circb(&mc, cx, cy, r, 0);
    } else {
        moy_circ(&mc, cx, cy, r, 0);
    }
}

void moy_gfx_capi_tri(moy_gfx_draw_ctx_t *c, int x1, int y1, int x2, int y2,
                      int x3, int y3, int ci) {
    moy_canvas mc;
    if (capi_solid(c, &mc, ci)) {
        moy_tri(&mc, x1, y1, x2, y2, x3, y3, 0);
    }
}

void moy_gfx_capi_print(moy_gfx_draw_ctx_t *c, const uint8_t *s, size_t slen,
                        int x, int y, int ci) {
    const int32_t *st = c->st;
    moy_gfx_text_raw(c->px, c->cap, st[ST_W], s, slen, x, y, capi_col(c, ci),
                     c->font, c->nglyphs, c->first, st[ST_FONT_SCALE],
                     st[ST_CAM_X], st[ST_CAM_Y],
                     st[ST_CX0], st[ST_CY0], st[ST_CX1], st[ST_CY1]);
    c->st[ST_N_TEXT]++;
}

bool moy_gfx_capi_batch_src(const moy_gfx_draw_ctx_t *c) {
    return c->bsrc != NULL;
}

// The pending run, flushed in pure C (#67 stage-1): the SAME array-mode walk
// blit_batch performs -- header reset FIRST (flush_batch's re-entrancy rule),
// then libmoy's moy_spr per quad against the registered sheet, colour via the
// ctx's pal-resolved table, camera/clip from the state array. Token-guarded:
// a run stamped by another writer flushes against canvas._batch_sheet, which
// only the Python flush knows -- return false and let the caller upcall.
bool moy_gfx_capi_flush_batch(moy_gfx_draw_ctx_t *c, int token) {
    int16_t *q = c->batch;
    if (q == NULL) {
        return true;                   // no queue: nothing to own
    }
    mp_int_t next = q[0];
    if (next <= 4) {
        return true;                   // empty: done trivially
    }
    if (c->bsrc == NULL || c->px == NULL || q[3] != (int16_t)token) {
        return false;                  // caller must upcall canvas.flush_batch
    }
    mp_int_t dw = c->st[ST_W];
    if (dw <= 0) {
        return false;
    }
    if ((size_t)next > c->batch_len) {
        next = (mp_int_t)c->batch_len;
    }
    mp_int_t key = q[1];
    mp_int_t scale = q[2];
    if (scale < 1) {
        scale = 1;
    }
    mp_int_t cx0 = c->st[ST_CX0], cy0 = c->st[ST_CY0];
    mp_int_t cx1 = c->st[ST_CX1], cy1 = c->st[ST_CY1];
    moy_gfx_clip(dw, c->cap, &cx0, &cy0, &cx1, &cy1);
    q[0] = 4;                          // reset FIRST -- mirror flush_batch
    moy_canvas cv;
    moy_sheet sh;
    moy_gfx_canvas(&cv, c->px, dw, c->cap, c->pal, c->bpalt,
                   c->st[ST_CAM_X], c->st[ST_CAM_Y], cx0, cy0, cx1, cy1);
    moy_sheet_init(&sh, (uint8_t *)c->bsrc);
    for (mp_int_t i = 4; i + 4 <= next; i += 4) {
        moy_spr(&cv, &sh, (int)q[i], (int)q[i + 1], (int)q[i + 2],
                (int)key, (int)scale, (int)(q[i + 3] & 3));
    }
    return true;
}

bool moy_gfx_capi_map_src(const moy_gfx_draw_ctx_t *c) {
    return c->msrc != NULL;
}

const uint8_t *moy_gfx_capi_map_cells(const moy_gfx_draw_ctx_t *c,
                                      int *mw, int *mh) {
    if (c->msrc == NULL) {
        return NULL;
    }
    *mw = (int)c->msrc_w;
    *mh = (int)c->msrc_h;
    return c->msrc;
}

// Borrow a full libmoy canvas (pal LUT + palt, unlike capi_solid's one-colour
// table) from the ctx state -- what the sheet-sampling verbs need. False when
// the ctx has no usable destination.
static bool capi_texture_canvas(moy_gfx_draw_ctx_t *c, moy_canvas *mc) {
    const int32_t *st = c->st;
    mp_int_t dw = st[ST_W];
    if (dw <= 0 || c->px == NULL) {
        return false;
    }
    mp_int_t cx0 = st[ST_CX0], cy0 = st[ST_CY0];
    mp_int_t cx1 = st[ST_CX1], cy1 = st[ST_CY1];
    moy_gfx_clip(dw, c->cap, &cx0, &cy0, &cx1, &cy1);
    moy_gfx_canvas(mc, c->px, dw, c->cap, c->pal, c->bpalt,
                   st[ST_CAM_X], st[ST_CAM_Y], cx0, cy0, cx1, cy1);
    return true;
}

// sspr against the REGISTERED sheet (set_batch_src) -- the same moy_sspr call
// the MP verb makes, state from the ctx. Caller gates on capi_batch_src.
void moy_gfx_capi_sspr(moy_gfx_draw_ctx_t *c, int sx, int sy, int sw, int sh,
                       int dx, int dy, int ddw, int ddh, int ck, int flip) {
    moy_canvas mc;
    moy_sheet s;
    if (c->bsrc == NULL || sw <= 0 || sh <= 0 || ddw <= 0 || ddh <= 0
        || !capi_texture_canvas(c, &mc)) {
        return;
    }
    moy_sheet_init(&s, (uint8_t *)c->bsrc);
    moy_sspr(&mc, &s, sx, sy, sw, sh, dx, dy, ddw, ddh, ck, flip);
}

// tline against the registered sheet + map (set_batch_src + set_map_src).
// u/v/du/dv are 16.16 fixed-point ints, exactly the MP verb's contract.
void moy_gfx_capi_tline(moy_gfx_draw_ctx_t *c, int x0, int y0, int x1, int y1,
                        int32_t u, int32_t v, int32_t du, int32_t dv, int ck) {
    moy_canvas mc;
    moy_sheet s;
    moy_map m;
    if (c->bsrc == NULL || c->msrc == NULL || !capi_texture_canvas(c, &mc)) {
        return;
    }
    moy_sheet_init(&s, (uint8_t *)c->bsrc);
    moy_map_init(&m, (uint8_t *)c->msrc, (int)c->msrc_w, (int)c->msrc_h);
    moy_tline(&mc, &s, &m, x0, y0, x1, y1, u, v, du, dv, ck);
}

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

// --- membench (#66/#67, 2026-08-10): price the indexed-SRAM-canvas idea ----
//
// The P4 A/B'd indexed-vs-RGB565 and declined indexed, but the T-Deck was
// recorded as "the untested opposite shape: it already pays an SRAM bounce
// copy the resolve could ride". This measures that shape's raw ingredients on
// whatever board runs it: per-pixel fill and tile-blit cost by destination
// REGION (internal SRAM vs PSRAM) and pixel WIDTH (8-bit index vs RGB565),
// plus the chunked 8->16 LUT resolve a bounce-riding flush would add and the
// chunked PSRAM->SRAM memcpy the flush pays TODAY. All kernels are plain
// per-pixel loops -- the raster's shape -- so the numbers compare shapes, not
// compiler tricks. Returns a 10-tuple of us (min of 3 passes), -1 where the
// buffer would not allocate (a 153KB internal RGB565 canvas is EXPECTED to
// fail on the S3 -- that failure is the point of the 8-bit candidate):
//   (fill16_sram, fill16_psram, fill8_sram, fill8_psram,
//    blit16_sram, blit16_psram, blit8_sram, blit8_psram, resolve, bounce)
#ifdef MOY_GFX_HAS_ASYNC_COPY   // proxy for "an ESP-IDF build with heap_caps"
#include "esp_heap_caps.h"
#define MOY_GFX_HAS_MEMBENCH 1

#define MB_W 320
#define MB_H 240
#define MB_PX (MB_W * MB_H)
#define MB_TILE 16
#define MB_TILES 300             /* 300 x 16x16 = 76800 px = one frame */
#define MB_CHUNK_PX 9600         /* resolve/bounce chunk: 8 chunks a frame */
#define MB_REPS 3

static uint32_t mb_fill16(uint16_t *d) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    for (int y = 0; y < MB_H; y++) {
        uint16_t *row = d + y * MB_W;
        for (int x = 0; x < MB_W; x++) row[x] = 0xAAAA;
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

static uint32_t mb_fill8(uint8_t *d) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    for (int y = 0; y < MB_H; y++) {
        uint8_t *row = d + y * MB_W;
        for (int x = 0; x < MB_W; x++) row[x] = 0x2A;
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

static uint32_t mb_blit16(uint16_t *d, const uint16_t *src, size_t src_px) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    uint32_t o = 12345;
    for (int t = 0; t < MB_TILES; t++) {
        o = o * 1103515245u + 12345u;            /* scattered dst + src reads */
        int dx = (int)(o % (MB_W - MB_TILE));
        int dy = (int)((o >> 8) % (MB_H - MB_TILE));
        const uint16_t *s = src + (o % (src_px - MB_TILE * MB_TILE));
        for (int y = 0; y < MB_TILE; y++) {
            uint16_t *dr = d + (dy + y) * MB_W + dx;
            const uint16_t *sr = s + y * MB_TILE;
            for (int x = 0; x < MB_TILE; x++) dr[x] = sr[x];
        }
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

static uint32_t mb_blit8(uint8_t *d, const uint8_t *src, size_t src_px) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    uint32_t o = 12345;
    for (int t = 0; t < MB_TILES; t++) {
        o = o * 1103515245u + 12345u;
        int dx = (int)(o % (MB_W - MB_TILE));
        int dy = (int)((o >> 8) % (MB_H - MB_TILE));
        const uint8_t *s = src + (o % (src_px - MB_TILE * MB_TILE));
        for (int y = 0; y < MB_TILE; y++) {
            uint8_t *dr = d + (dy + y) * MB_W + dx;
            const uint8_t *sr = s + y * MB_TILE;
            for (int x = 0; x < MB_TILE; x++) dr[x] = sr[x];
        }
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

static uint32_t mb_resolve(const uint8_t *s8, uint16_t *chunk,
                           const uint16_t *lut) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    for (int c = 0; c < MB_PX / MB_CHUNK_PX; c++) {
        const uint8_t *s = s8 + c * MB_CHUNK_PX;
        for (int i = 0; i < MB_CHUNK_PX; i++) chunk[i] = lut[s[i] & 63];
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

static uint32_t mb_bounce(const uint16_t *s16, uint16_t *chunk) {
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    for (int c = 0; c < MB_PX / MB_CHUNK_PX; c++) {
        memcpy(chunk, s16 + c * MB_CHUNK_PX, MB_CHUNK_PX * 2);
    }
    return (uint32_t)mp_hal_ticks_us() - t0;
}

#define MB_BEST(dst, call) do { \
        uint32_t best = 0xFFFFFFFFu; \
        for (int r = 0; r < MB_REPS; r++) { \
            uint32_t us = (call); \
            if (us < best) best = us; \
        } \
        (dst) = (mp_int_t)best; \
    } while (0)

static mp_obj_t moy_gfx_membench(void) {
    uint16_t *d16s = heap_caps_malloc(MB_PX * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    uint16_t *d16p = heap_caps_malloc(MB_PX * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    uint8_t *d8s = heap_caps_malloc(MB_PX, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    uint8_t *d8p = heap_caps_malloc(MB_PX, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    size_t src_px = 64 * 1024;               /* sprite caches live in PSRAM */
    uint16_t *src16 = heap_caps_malloc(src_px * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    uint8_t *src8 = heap_caps_malloc(src_px, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    uint16_t *chunk = heap_caps_malloc(MB_CHUNK_PX * 2, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    mp_int_t r[10];
    for (int i = 0; i < 10; i++) r[i] = -1;
    if (src16 != NULL) {
        for (size_t i = 0; i < src_px; i++) src16[i] = (uint16_t)(i * 2654435761u >> 8);
    }
    if (src8 != NULL) {
        for (size_t i = 0; i < src_px; i++) src8[i] = (uint8_t)(i * 2654435761u >> 8);
    }
    if (d16s) MB_BEST(r[0], mb_fill16(d16s));
    if (d16p) MB_BEST(r[1], mb_fill16(d16p));
    if (d8s) MB_BEST(r[2], mb_fill8(d8s));
    if (d8p) MB_BEST(r[3], mb_fill8(d8p));
    if (d16s && src16) MB_BEST(r[4], mb_blit16(d16s, src16, src_px));
    if (d16p && src16) MB_BEST(r[5], mb_blit16(d16p, src16, src_px));
    if (d8s && src8) MB_BEST(r[6], mb_blit8(d8s, src8, src_px));
    if (d8p && src8) MB_BEST(r[7], mb_blit8(d8p, src8, src_px));
    if (d8s && chunk) {
        static uint16_t lut[64];
        for (int i = 0; i < 64; i++) lut[i] = (uint16_t)(i * 1031);
        MB_BEST(r[8], mb_resolve(d8s, chunk, lut));
    }
    if (d16p && chunk) MB_BEST(r[9], mb_bounce(d16p, chunk));
    free(d16s); free(d16p); free(d8s); free(d8p);
    free(src16); free(src8); free(chunk);
    mp_obj_t items[10];
    for (int i = 0; i < 10; i++) items[i] = mp_obj_new_int(r[i]);
    return mp_obj_new_tuple(10, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_gfx_membench_obj, moy_gfx_membench);
#endif  // MOY_GFX_HAS_MEMBENCH

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
    { MP_ROM_QSTR(MP_QSTR_fill_spans), MP_ROM_PTR(&moy_gfx_fill_spans_obj) },
    { MP_ROM_QSTR(MP_QSTR_tri),        MP_ROM_PTR(&moy_gfx_tri_obj) },
    { MP_ROM_QSTR(MP_QSTR_sspr),       MP_ROM_PTR(&moy_gfx_sspr_obj) },
    { MP_ROM_QSTR(MP_QSTR_tline),      MP_ROM_PTR(&moy_gfx_tline_obj) },
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
    #ifdef MOY_GFX_HAS_MEMBENCH
    { MP_ROM_QSTR(MP_QSTR_membench),   MP_ROM_PTR(&moy_gfx_membench_obj) },
    #endif
};
static MP_DEFINE_CONST_DICT(moy_gfx_globals, moy_gfx_globals_table);

const mp_obj_module_t mp_module_moy_gfx = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_gfx_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_gfx, mp_module_moy_gfx);
