// moy_fold: the game fold. See moy_fold.h for the latch, the fence and what is
// shared with the boards and what is not -- this file is only the mechanism
// those paragraphs describe.

#include <string.h>

#include "py/mpthread.h"

#include "esp_rom_sys.h"
#include "esp_timer.h"

#include "moy_flush.h"
#include "moy_fold.h"

moy_fold_t moy_fold;

// ---------------------------------------------------------------------------
// The latch (VM side, except moy_fold_end)
// ---------------------------------------------------------------------------

bool moy_fold_arm(const uint8_t *src, size_t len, int vw, int vh,
                  int ox, int oy, int scale, int fb_w, int fb_h) {
    // Everything the synthesis assumes, checked ONCE: it runs on the feeder
    // with no MP context, so a geometry it cannot express has to be refused
    // here or it becomes an out-of-bounds read on core 0.
    if (src == NULL || vw <= 0 || vh <= 0 || ox < 0 || oy < 0
            || scale < 1 || scale > MOY_FOLD_MAX_SCALE
            || vw > fb_w || vh > fb_h
            || ox + vw * scale > fb_w || oy + vh * scale > fb_h
            || len < (size_t)vw * (size_t)vh * 2u) {
        return false;
    }
    moy_fold.src = src;
    moy_fold.vw = vw;
    moy_fold.vh = vh;
    moy_fold.ox = ox;
    moy_fold.oy = oy;
    moy_fold.scale = scale;
    moy_fold.armed = true;
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
    return true;
}

void moy_fold_fence(void) {
    // `src` is read during SYNTHESIS only, and the feeder finishes that in the
    // first few ms of a flush -- so this waits for the FEED to complete, not
    // for the transfer, which is strictly earlier than a drain and is two
    // volatile reads on the ordinary cadence.
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
    moy_fold.armed = false;
    moy_fold.inflight = false;
    moy_fold.src = NULL;
}

// ---------------------------------------------------------------------------
// The pixels
// ---------------------------------------------------------------------------

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
    uint16_t *dst = (uint16_t *)fb;
    memset(dst, 0, (size_t)fb_w * (size_t)fb_h * 2u);
    const uint16_t *g = (const uint16_t *)f->src;
    for (int gy = 0; gy < f->vh; gy++) {
        const uint16_t *grow = g + (size_t)gy * (size_t)f->vw;
        uint16_t *first = dst + (size_t)(f->oy + gy * sc) * (size_t)fb_w + f->ox;
        if (sc == 1) {
            memcpy(first, grow, (size_t)f->vw * 2u);
            continue;
        }
        uint16_t *o = first;
        for (int gx = 0; gx < f->vw; gx++) {
            uint16_t v = grow[gx];
            for (int s = 0; s < sc; s++) {
                *o++ = v;
            }
        }
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
    const uint16_t *g = (const uint16_t *)f->src;
    uint16_t *dst = (uint16_t *)slot;
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
        const uint16_t *grow = g + (size_t)gy * (size_t)f->vw;
        if (sc == 1) {
            memcpy(dst + f->ox, grow, (size_t)f->vw * 2u);
        } else {
            uint16_t *o = dst + f->ox;
            for (int gx = 0; gx < f->vw; gx++) {
                uint16_t v = grow[gx];
                for (int s = 0; s < sc; s++) {
                    *o++ = v;
                }
            }
        }
        if (f->ox + rw < dst_w) {
            memset(dst + f->ox + rw, 0,
                   (size_t)(dst_w - f->ox - rw) * 2u);
        }
        last_gy = gy;
        last_row = dst;
    }
}

void moy_fold_band_rot(uint8_t *slot, const moy_fold_rot_t *geom, int py0,
                       int rows) {
    const moy_fold_t *f = &moy_fold;
    const int sc = f->scale;
    const int rw = f->vw * sc, rh = f->vh * sc;
    const int stride = geom->win_w;
    const uint16_t *g = (const uint16_t *)f->src;
    uint16_t *dst = (uint16_t *)slot;
    if (rows > MOY_FOLD_MAX_BAND_ROWS) {
        rows = MOY_FOLD_MAX_BAND_ROWS;   // the board's _Static_assert prevents this
    }
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
        const uint16_t *grow = g + (size_t)(dy / sc) * (size_t)f->vw;
        for (int r = 0; r < rows; r++) {
            int gx = gxmap[r];
            *d = (gx >= 0) ? grow[gx] : 0;
            d += stride;
        }
    }
}
