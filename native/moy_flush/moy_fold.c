// moy_fold: the game fold. See moy_fold.h for the latch, the fences, the
// snapshot and what is shared with the boards and what is not -- this file is
// only the mechanism those paragraphs describe.

#include <string.h>

#include "py/mpthread.h"

#include "esp_async_memcpy.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

#include "moy_flush.h"
#include "moy_fold.h"

moy_fold_t moy_fold;

// ---------------------------------------------------------------------------
// The snapshot engine
// ---------------------------------------------------------------------------

static async_memcpy_handle_t s_snap;     // installed on the first snapshot
static bool s_snap_dead;                 // never installed, or a copy that never landed

static bool moy_fold_snap_done(async_memcpy_handle_t h, async_memcpy_event_t *e,
                               void *arg) {
    (void)h; (void)e; (void)arg;
    moy_fold.copying = false;
    return false;                        // ISR: nothing to wake
}

// Both sides wait the same way: a bounded spin on the latch. The feeder has
// no GIL to release; the VM side wraps this in an exit/enter pair.
static void moy_fold_snap_wait(void) {
    if (!moy_fold.copying) {
        return;
    }
    int64_t deadline = esp_timer_get_time() + MOY_FLUSH_TIMEOUT_US;
    while (moy_fold.copying && esp_timer_get_time() < deadline) {
        esp_rom_delay_us(20);
    }
    if (moy_fold.copying) {
        // The bug fence, once: a copy that never signals would otherwise hold
        // every later frame for a whole deadline. The scratch may be torn for
        // this one frame; from here on every snapshot is a memcpy.
        moy_fold.copying = false;
        moy_fold.snap_timeouts++;
        s_snap_dead = true;
    }
}

// Start the DMA, or say why not. False means the caller memcpys instead --
// and never that anything went wrong the caller must report.
static bool moy_fold_snap_start(uint8_t *dst, const uint8_t *src, size_t len) {
    if (s_snap_dead) {
        return false;
    }
    if ((((uintptr_t)dst | (uintptr_t)src | (uintptr_t)len)
            & (MOY_FOLD_SNAP_ALIGN - 1)) != 0) {
        return false;                    // the engine would refuse, loudly
    }
    if (s_snap == NULL) {
        async_memcpy_config_t cfg = ASYNC_MEMCPY_DEFAULT_CONFIG();
        cfg.backlog = 2;                 // the fence keeps it to one in flight
        cfg.dma_burst_size = 64;         // the widest AHB burst: PSRAM throughput
        if (esp_async_memcpy_install(&cfg, &s_snap) != ESP_OK) {
            s_snap = NULL;
            s_snap_dead = true;
            return false;
        }
    }
    moy_fold.copying = true;             // BEFORE the submit: the ISR clears it
    esp_err_t err = esp_async_memcpy(s_snap, dst, (void *)src, len,
                                     moy_fold_snap_done, NULL);
    if (err != ESP_OK) {
        moy_fold.copying = false;
        if (err == ESP_ERR_INVALID_ARG) {
            s_snap_dead = true;          // a rule the pre-check missed: stop asking
        }
        return false;                    // a full queue is transient: memcpy this frame
    }
    return true;
}

// ---------------------------------------------------------------------------
// The latch (VM side, except moy_fold_end)
// ---------------------------------------------------------------------------

// Everything the synthesis assumes, checked ONCE: it runs on the feeder with
// no MP context, so a geometry it cannot express has to be refused here or it
// becomes an out-of-bounds read on core 0.
static bool moy_fold_geometry_ok(int vw, int vh, int ox, int oy, int scale,
                                 int fb_w, int fb_h) {
    return !(vw <= 0 || vh <= 0 || ox < 0 || oy < 0
             || scale < 1 || scale > MOY_FOLD_MAX_SCALE
             || vw > fb_w || vh > fb_h
             || ox + vw * scale > fb_w || oy + vh * scale > fb_h);
}

static void moy_fold_latch(const uint8_t *src, int vw, int vh, int sx,
                           int sstride, int ox, int oy, int scale, int fmt,
                           const uint16_t *lut) {
    moy_fold.src = src;
    moy_fold.vw = vw;
    moy_fold.vh = vh;
    moy_fold.sx = sx;
    moy_fold.sstride = sstride;
    moy_fold.ox = ox;
    moy_fold.oy = oy;
    moy_fold.scale = scale;
    moy_fold.fmt = fmt;
    moy_fold.lut = lut;
    moy_fold.sy = 0;
    moy_fold.npatch = 0;
    moy_fold.armed = true;
}

bool moy_fold_arm(const uint8_t *src, size_t len, int vw, int vh,
                  int ox, int oy, int scale, int fb_w, int fb_h) {
    if (src == NULL || !moy_fold_geometry_ok(vw, vh, ox, oy, scale, fb_w, fb_h)
            || len < (size_t)vw * (size_t)vh * 2u) {
        return false;
    }
    moy_fold_latch(src, vw, vh, 0, vw, ox, oy, scale, MOY_FOLD_WIRE, NULL);
    return true;
}

bool moy_fold_arm_snap(const uint8_t *live, size_t live_len, size_t live_off,
                       uint8_t *scratch, size_t scratch_len,
                       int vw, int vh, int sx, int sstride,
                       int ox, int oy, int scale, int fb_w, int fb_h,
                       bool *async_out) {
    *async_out = false;
    if (live == NULL || scratch == NULL || sx < 0 || sstride < sx + vw
            || !moy_fold_geometry_ok(vw, vh, ox, oy, scale, fb_w, fb_h)) {
        return false;
    }
    size_t len = (size_t)vh * (size_t)sstride * 2u;
    if (live_off > live_len || live_len - live_off < len || scratch_len < len) {
        return false;
    }
    // The caller fenced; this is the belt for a caller that did not, and it is
    // one compare when they did.
    moy_fold_snap_fence();
    if (moy_fold_snap_start(scratch, live + live_off, len)) {
        moy_fold.snaps++;
        *async_out = true;
    } else {
        memcpy(scratch, live + live_off, len);
        moy_fold.snaps_sync++;
    }
    moy_fold_latch(scratch, vw, vh, sx, sstride, ox, oy, scale, MOY_FOLD_WIRE,
                   NULL);
    return true;
}

bool moy_fold_arm_frame(const uint8_t *frame, size_t frame_len, int fmt,
                        const uint16_t *lut, uint8_t *scratch,
                        size_t scratch_len, int gw, int gh, int sx, int sy,
                        int vw, int vh, int ox, int oy, int scale, int fb_w,
                        int fb_h, const int16_t *rects, int nrects,
                        const uint8_t *canvas, size_t canvas_len,
                        size_t *kept_off, bool *async_out) {
    *async_out = false;
    *kept_off = 0;
    if (frame == NULL || scratch == NULL
            || (fmt != MOY_FOLD_LE565 && fmt != MOY_FOLD_IDX8)
            || (fmt == MOY_FOLD_IDX8 && lut == NULL)
            || gw <= 0 || gh <= 0 || sx < 0 || sy < 0
            || sx + vw > gw || sy + vh > gh
            || nrects < 0 || nrects > MOY_FOLD_MAX_PATCHES
            || (nrects > 0 && (rects == NULL || canvas == NULL
                               || canvas_len < (size_t)gw * (size_t)gh * 2u))
            || !moy_fold_geometry_ok(vw, vh, ox, oy, scale, fb_w, fb_h)) {
        return false;
    }
    const size_t bpp = (fmt == MOY_FOLD_IDX8) ? 1u : 2u;
    const size_t n = (size_t)gw * (size_t)gh * bpp;
    const uintptr_t a0 = (uintptr_t)frame & ~(uintptr_t)(MOY_FOLD_SNAP_ALIGN - 1);
    const size_t off = (size_t)((uintptr_t)frame - a0);
    const size_t span = (off + n + MOY_FOLD_SNAP_ALIGN - 1)
                        & ~(size_t)(MOY_FOLD_SNAP_ALIGN - 1);
    // The patches, clipped to the frame: a rect off it is no patch at all.
    moy_fold_patch_t patch[MOY_FOLD_MAX_PATCHES];
    int np = 0;
    size_t pbytes = 0;
    for (int i = 0; i < nrects; i++) {
        int x0 = rects[4 * i], y0 = rects[4 * i + 1];
        int x1 = x0 + rects[4 * i + 2], y1 = y0 + rects[4 * i + 3];
        if (x0 < 0) x0 = 0;
        if (y0 < 0) y0 = 0;
        if (x1 > gw) x1 = gw;
        if (y1 > gh) y1 = gh;
        if (x1 <= x0 || y1 <= y0) {
            continue;
        }
        patch[np].x = x0;
        patch[np].y = y0;
        patch[np].w = x1 - x0;
        patch[np].h = y1 - y0;
        pbytes += (size_t)patch[np].w * (size_t)patch[np].h * 2u;
        np++;
    }
    if (frame_len < n || (scratch_len & 1u) != 0 || pbytes > MOY_FOLD_PATCH_BYTES
            || scratch_len < span + MOY_FOLD_LUT_BYTES + pbytes) {
        return false;
    }
    moy_fold_snap_fence();
    // The span, not the frame: the engine takes 64-aligned ends only, and the
    // bytes either side of the frame are read and never used. A frame already
    // where the copy would land -- the last frame shown, shown again -- is
    // not copied at all.
    if (frame == scratch + off) {
        // in place
    } else if (moy_fold_snap_start(scratch, (const uint8_t *)a0, span)) {
        moy_fold.snaps++;
        *async_out = true;
    } else {
        memcpy(scratch + off, frame, n);
        moy_fold.snaps_sync++;
    }
    const size_t tail = (span + 1u) & ~(size_t)1u;
    uint16_t *lut_copy = NULL;
    if (fmt == MOY_FOLD_IDX8) {
        lut_copy = (uint16_t *)(scratch + tail);
        memcpy(lut_copy, lut, MOY_FOLD_LUT_BYTES);
    }
    uint16_t *pp = (uint16_t *)(scratch + tail + MOY_FOLD_LUT_BYTES);
    const uint16_t *cv = (const uint16_t *)canvas;
    for (int i = 0; i < np; i++) {
        moy_fold.patch[i] = patch[i];
        moy_fold.patch[i].pix = pp;
        for (int r = 0; r < patch[i].h; r++) {
            memcpy(pp, cv + (size_t)(patch[i].y + r) * (size_t)gw + patch[i].x,
                   (size_t)patch[i].w * 2u);
            pp += patch[i].w;
        }
    }
    moy_fold_latch(scratch + off + (size_t)sy * (size_t)gw * bpp, vw, vh, sx,
                   gw, ox, oy, scale, fmt, lut_copy);
    moy_fold.sy = sy;
    moy_fold.npatch = np;
    moy_fold.frame_arms++;
    *kept_off = off;
    return true;
}

bool moy_fold_consume(void) {
    // The feeder is idle here (every caller drains first), which is what makes
    // this pair of stores race-free against a band that reads `inflight`.
    moy_fold.inflight = moy_fold.armed;
    moy_fold.armed = false;
    if (moy_fold.inflight) {
        moy_fold.frames++;
    }
    return moy_fold.inflight;
}

void moy_fold_end(void) {
    moy_fold.inflight = false;
}

bool moy_fold_disarm(void) {
    if (!moy_fold.armed) {
        return false;
    }
    moy_fold.armed = false;
    moy_fold_snap_fence();               // the caller composites from `src` next
    return true;
}

void moy_fold_snap_fence(void) {
    if (!moy_fold.copying) {
        return;
    }
    int64_t t0 = esp_timer_get_time();
    MP_THREAD_GIL_EXIT();
    moy_fold_snap_wait();
    MP_THREAD_GIL_ENTER();
    moy_fold.snap_wait_us = (uint32_t)(esp_timer_get_time() - t0);
}

void moy_fold_fence(void) {
    // The scratch is about to be overwritten: no snapshot may still be
    // landing in it, and no flush may still be reading it. `src` is read
    // during SYNTHESIS only, and the feeder finishes that before the last
    // band ships -- so this waits for the FEED to complete, not for the
    // transfer, which is strictly earlier than a drain and is a few volatile
    // reads on the ordinary cadence.
    moy_fold_snap_fence();
    if (!moy_fold.inflight || !moy_flush.frame_busy) {
        return;
    }
    int64_t deadline = esp_timer_get_time() + MOY_FLUSH_TIMEOUT_US;
    MP_THREAD_GIL_EXIT();
    while (moy_flush.frame_busy
            && moy_flush.bnc_next < moy_flush.bnc_total
            && esp_timer_get_time() < deadline) {
        esp_rom_delay_us(20);
    }
    MP_THREAD_GIL_ENTER();
}

void moy_fold_reset(void) {
    moy_fold_snap_fence();               // the buffers are about to be freed
    moy_fold.armed = false;
    moy_fold.inflight = false;
    moy_fold.src = NULL;
}

// ---------------------------------------------------------------------------
// The pixels
// ---------------------------------------------------------------------------

// A source row -- `gy` of the rectangle -- and one pixel of it, in wire order.
static inline const uint8_t *moy_fold_row(const moy_fold_t *f, int gy) {
    const size_t bpp = (f->fmt == MOY_FOLD_IDX8) ? 1u : 2u;
    return f->src + ((size_t)gy * (size_t)f->sstride + (size_t)f->sx) * bpp;
}

static inline uint16_t moy_fold_px(const moy_fold_t *f, const uint8_t *row,
                                   int gx) {
    if (f->fmt == MOY_FOLD_IDX8) {
        return f->lut[row[gx]];
    }
    if (f->fmt == MOY_FOLD_LE565) {
        return (uint16_t)((row[2 * gx] << 8) | row[2 * gx + 1]);
    }
    return ((const uint16_t *)row)[gx];
}

// A little-endian RGB565 row into wire order: two pixels a word when both ends
// allow it, which a cart's frame on a 4-byte boundary does. Byte loads
// otherwise -- the S3 faults on a misaligned word, and a cart may hand over a
// frame at any address.
static void moy_fold_swap_row(uint16_t *d, const uint8_t *row, int n) {
    int i = 0;
    if ((((uintptr_t)d | (uintptr_t)row) & 3u) == 0) {
        const uint32_t *s = (const uint32_t *)row;
        uint32_t *o = (uint32_t *)d;
        for (; i + 1 < n; i += 2) {
            uint32_t w = *s++;
            *o++ = ((w & 0x00FF00FFu) << 8) | ((w >> 8) & 0x00FF00FFu);
        }
    }
    for (; i < n; i++) {
        d[i] = (uint16_t)((row[2 * i] << 8) | row[2 * i + 1]);
    }
}

// The patches over view row `gy`: each overwrites its columns of `d` (the
// row as expanded, `sc`x across) with its own pixels.
static void moy_fold_patch_row(uint16_t *d, const moy_fold_t *f, int gy,
                               int sc) {
    const int y = f->sy + gy;
    for (int i = 0; i < f->npatch; i++) {
        const moy_fold_patch_t *p = &f->patch[i];
        if (y < p->y || y >= p->y + p->h) {
            continue;
        }
        int x0 = p->x > f->sx ? p->x : f->sx;
        int x1 = p->x + p->w < f->sx + f->vw ? p->x + p->w : f->sx + f->vw;
        const uint16_t *src = p->pix + (size_t)(y - p->y) * (size_t)p->w;
        for (int x = x0; x < x1; x++) {
            uint16_t v = src[x - p->x];
            uint16_t *o = d + (size_t)(x - f->sx) * (size_t)sc;
            for (int s = 0; s < sc; s++) {
                o[s] = v;
            }
        }
    }
}

// One source row, all `vw` of it, into `d` at `sc`x across.
static void moy_fold_expand_row(uint16_t *d, const moy_fold_t *f,
                                const uint8_t *row, int sc) {
    const int n = f->vw;
    if (sc == 1) {
        if (f->fmt == MOY_FOLD_WIRE) {
            memcpy(d, row, (size_t)n * 2u);
        } else if (f->fmt == MOY_FOLD_LE565) {
            moy_fold_swap_row(d, row, n);
        } else {
            const uint16_t *lut = f->lut;
            for (int i = 0; i < n; i++) {
                d[i] = lut[row[i]];
            }
        }
        return;
    }
    if (f->fmt == MOY_FOLD_WIRE) {
        const uint16_t *g = (const uint16_t *)row;
        for (int gx = 0; gx < n; gx++) {
            uint16_t v = g[gx];
            for (int s = 0; s < sc; s++) {
                *d++ = v;
            }
        }
        return;
    }
    for (int gx = 0; gx < n; gx++) {
        uint16_t v = moy_fold_px(f, row, gx);
        for (int s = 0; s < sc; s++) {
            *d++ = v;
        }
    }
}

// View row `gy` into `d`, the frame's pixels and then any patch over them.
static void moy_fold_expand(uint16_t *d, const moy_fold_t *f, int gy, int sc) {
    moy_fold_expand_row(d, f, moy_fold_row(f, gy), sc);
    if (f->npatch > 0) {
        moy_fold_patch_row(d, f, gy, sc);
    }
}

// The composite the fold skips, and the reference every band below must
// reassemble into: `moy_gfx.fill(fb, 0)` + `moy_gfx.blit565_scale(...)`, which
// is nearest-neighbour replication with no clipping to do (the arm refused any
// geometry that would need some).
void moy_fold_composite(uint8_t *fb, int fb_w, int fb_h) {
    const moy_fold_t *f = &moy_fold;
    const int sc = f->scale;
    if (f->src == NULL || sc < 1 || fb_w <= 0 || fb_h <= 0
            || f->ox + f->vw * sc > fb_w || f->oy + f->vh * sc > fb_h) {
        return;
    }
    moy_fold_snap_wait();                // reads the snapshot, like a band
    uint16_t *dst = (uint16_t *)fb;
    memset(dst, 0, (size_t)fb_w * (size_t)fb_h * 2u);
    for (int gy = 0; gy < f->vh; gy++) {
        uint16_t *first = dst + (size_t)(f->oy + gy * sc) * (size_t)fb_w + f->ox;
        moy_fold_expand(first, f, gy, sc);
        for (int r = 1; r < sc; r++) {   // the row, replicated down
            memcpy(first + (size_t)r * (size_t)fb_w, first,
                   (size_t)f->vw * (size_t)sc * 2u);
        }
    }
}

void moy_fold_band(uint8_t *slot, int dst_w, int y, int rows) {
    const moy_fold_t *f = &moy_fold;
    const int sc = f->scale;
    const int rw = f->vw * sc, rh = f->vh * sc;
    const size_t row_bytes = (size_t)dst_w * 2u;
    uint16_t *dst = (uint16_t *)slot;
    moy_fold_snap_wait();                // the first band pays the residue
    // At scale > 1 consecutive destination rows share a source row, so the
    // gather runs once and the duplicates are a memcpy inside the slot --
    // internal SRAM, and it halves the PSRAM reads at scale 2.
    int last_gy = -1;
    const uint16_t *last_row = NULL;
    for (int r = 0; r < rows; r++, dst += dst_w) {
        int dy = (y + r) - f->oy;
        if (dy < 0 || dy >= rh) {
            memset(dst, 0, row_bytes);              // bezel row
            continue;
        }
        int gy = dy / sc;
        if (gy == last_gy) {
            memcpy(dst, last_row, row_bytes);
            continue;
        }
        if (f->ox > 0) {
            memset(dst, 0, (size_t)f->ox * 2u);
        }
        moy_fold_expand(dst + f->ox, f, gy, sc);
        if (f->ox + rw < dst_w) {
            memset(dst + f->ox + rw, 0,
                   (size_t)(dst_w - f->ox - rw) * 2u);
        }
        last_gy = gy;
        last_row = dst;
    }
}

// Whether a patch covers view row `gy`, and view pixel (gx, gy) with patches
// applied: the rotated gather's walk for the few rows the console drew on.
static bool moy_fold_patched(const moy_fold_t *f, int gy) {
    const int y = f->sy + gy;
    for (int i = 0; i < f->npatch; i++) {
        if (y >= f->patch[i].y && y < f->patch[i].y + f->patch[i].h) {
            return true;
        }
    }
    return false;
}

static uint16_t moy_fold_px_patched(const moy_fold_t *f, const uint8_t *row,
                                    int gy, int gx) {
    const int x = f->sx + gx, y = f->sy + gy;
    for (int i = 0; i < f->npatch; i++) {
        const moy_fold_patch_t *p = &f->patch[i];
        if (x >= p->x && x < p->x + p->w && y >= p->y && y < p->y + p->h) {
            return p->pix[(size_t)(y - p->y) * (size_t)p->w + (x - p->x)];
        }
    }
    return moy_fold_px(f, row, gx);
}

// The rotated gather's inner walk, once per layout so that no pixel pays for
// the choice: `grow` is the source row, `gxmap` the band's source columns.
#define MOY_FOLD_ROT_WALK(FETCH)                                               \
    for (int r = 0; r < rows; r++) {                                           \
        int gx = gxmap[r];                                                     \
        *d = (gx >= 0) ? (FETCH) : 0;                                          \
        d += stride;                                                           \
    }

void moy_fold_band_rot(uint8_t *slot, const moy_fold_rot_t *geom, int py0,
                       int rows) {
    const moy_fold_t *f = &moy_fold;
    const int sc = f->scale;
    const int rw = f->vw * sc, rh = f->vh * sc;
    const int stride = geom->win_w;
    uint16_t *dst = (uint16_t *)slot;
    if (rows > MOY_FOLD_MAX_BAND_ROWS) {
        rows = MOY_FOLD_MAX_BAND_ROWS;   // the board's _Static_assert prevents this
    }
    moy_fold_snap_wait();                // the first band pays the residue
    // The band's rows walk a LOGICAL COLUMN, so the source column is the same
    // for every px in the loop below: resolve it once per band into a map
    // rather than dividing per pixel. -1 is a bezel column.
    int16_t gxmap[MOY_FOLD_MAX_BAND_ROWS];
    const int lx0 = (geom->rot == 0) ? (geom->panel_h - 1 - py0) : py0;
    const int lxs = (geom->rot == 0) ? -1 : 1;
    for (int r = 0; r < rows; r++) {
        int dx = lx0 + r * lxs - f->ox;
        gxmap[r] = (dx >= 0 && dx < rw) ? (int16_t)(dx / sc) : (int16_t)-1;
    }
    const uint16_t *lut = f->lut;
    // Outer over px (= one LOGICAL ROW of the source, read sequentially in the
    // inner walk), inner over the band's physical rows: the same loop order the
    // root rotate uses, and for the same reason -- the scatter lands in
    // internal SRAM, where a 640 B stride is free.
    for (int px = geom->win_x; px < geom->win_x + stride; px++) {
        int ly = (geom->rot == 0) ? px : (geom->panel_w - 1 - px);
        uint16_t *d = dst + (px - geom->win_x);
        int dy = ly - f->oy;
        if (dy < 0 || dy >= rh) {
            for (int r = 0; r < rows; r++) {        // bezel row of the panel
                *d = 0;
                d += stride;
            }
            continue;
        }
        const int gy = dy / sc;
        const uint8_t *row = moy_fold_row(f, gy);
        if (f->npatch > 0 && moy_fold_patched(f, gy)) {
            for (int r = 0; r < rows; r++) {
                int gx = gxmap[r];
                *d = (gx >= 0) ? moy_fold_px_patched(f, row, gy, gx) : 0;
                d += stride;
            }
        } else if (f->fmt == MOY_FOLD_WIRE) {
            const uint16_t *grow = (const uint16_t *)row;
            MOY_FOLD_ROT_WALK(grow[gx])
        } else if (f->fmt == MOY_FOLD_IDX8) {
            MOY_FOLD_ROT_WALK(lut[row[gx]])
        } else {
            MOY_FOLD_ROT_WALK((uint16_t)((row[2 * gx] << 8) | row[2 * gx + 1]))
        }
    }
}

#undef MOY_FOLD_ROT_WALK
