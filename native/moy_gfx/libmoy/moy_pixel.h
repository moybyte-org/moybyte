/* The two places that touch a canvas buffer, and the only code in the library
 * that knows what a pixel is.
 *
 * moy_canvas.c's header says "moy_put and moy_rect are the only places that
 * touch c->pix", and that was true enough to duplicate moy_put into
 * moy_sprite.c by hand. Once the pixel type became a build option, one copy of
 * that duplicate would eventually be updated and the other not, so it lives
 * here instead -- still static, still inlinable into both translation units,
 * still unreachable from outside the library (which is what stops a host
 * writing past camera, clip and pal).
 *
 * NOTHING ELSE in the raster is format-aware. Every verb funnels through
 * moy_put or moy_rect, both of which write `c->store[index]` -- a value the
 * canvas precomputed -- so the per-pixel cost is one table lookup and one store
 * in both builds, and the geometry is byte-identical source either way.
 */

#ifndef MOY_PIXEL_H
#define MOY_PIXEL_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "moy.h"

/* The draw state every per-pixel verb needs, read ONCE.
 *
 * moy_put reads six fields through `moy_canvas *c` on every pixel, and because
 * the store to c->pix[] may alias the struct the compiler cannot hoist them.
 * On a bandwidth-bound board that is free; on one with a real cache it was
 * measured at 1.5-2x across spr, map, line, text and tri. So a verb that
 * writes more than a handful of pixels takes a copy up front and writes
 * through it. Same pixels, same order, same tests -- the conformance goldens
 * are the check, not this comment. */
typedef struct {
    moy_pixel *pix;
    int cw, cam_x, cam_y, cx0, cy0, cx1, cy1;
} moy_ds;

static inline moy_ds moy_ds_of(const moy_canvas *c)
{
    moy_ds d;
    d.pix = c->pix;   d.cw = c->w;
    d.cam_x = c->cam_x; d.cam_y = c->cam_y;
    d.cx0 = c->clip_x0; d.cy0 = c->clip_y0;
    d.cx1 = c->clip_x1; d.cy1 = c->clip_y1;
    return d;
}

static inline void moy_ds_put(const moy_ds *d, int x, int y, moy_pixel v)
{
    x -= d->cam_x;
    y -= d->cam_y;
    if (x < d->cx0 || x >= d->cx1 || y < d->cy0 || y >= d->cy1) return;
    d->pix[(size_t)y * (size_t)d->cw + (size_t)x] = v;
}

/* One camera-offset, clipped, pal-remapped pixel. */
static inline void moy_put(moy_canvas *c, int x, int y, int ci)
{
    x -= c->cam_x;
    y -= c->cam_y;
    if (x < c->clip_x0 || x >= c->clip_x1 || y < c->clip_y0 || y >= c->clip_y1)
        return;
    c->pix[y * c->w + x] = c->store[ci & 63];
}

/* n pixels of one colour, the span primitive under cls/rect/circ/tri/spr.
 *
 * memset on the index build. On the 565 build the same run is written through
 * 32-bit stores where alignment allows, so a span costs the same number of
 * STORES per byte in both builds and the format difference stays honest: twice
 * the bytes, not twice the bytes and twice the instructions. */
/* A run whose alignment the caller has already established, so the only test
 * left is the length. moy_rect uses this: every row of a rectangle starts at
 * the same 4-byte phase (the stride is even), so deciding alignment per ROW
 * throws the answer away 240 times a triangle. */
static inline void moy_fill_aligned(moy_pixel *dst, moy_pixel v, size_t n)
{
#ifdef MOY_PIXEL_RGB565
    uint32_t pair = (uint32_t)v | ((uint32_t)v << 16);
    while (n >= 8) {
        uint32_t *w = (uint32_t *)(void *)dst;
        w[0] = pair; w[1] = pair; w[2] = pair; w[3] = pair;
        dst += 8;
        n -= 8;
    }
    while (n >= 2) {
        *(uint32_t *)(void *)dst = pair;
        dst += 2;
        n -= 2;
    }
    if (n) *dst = v;
#else
    memset(dst, v, n);
#endif
}

static inline void moy_fill(moy_pixel *dst, moy_pixel v, size_t n)
{
#ifdef MOY_PIXEL_RGB565
    /* Short spans dominate: circ, tri and scaled spr emit one per row, often a
     * few pixels wide. The 32-bit-doubled path only pays for itself once a run
     * is long enough to amortise its alignment prologue, so it is gated rather
     * than always taken -- with it always on, ui measured 1.49x against
     * moybyte's fill_rect on a P4 and 1.02x with the index build's memset,
     * which is the whole gap. */
    if (n < 8) {
        while (n--) *dst++ = v;
        return;
    }
    {
        uint32_t pair = (uint32_t)v | ((uint32_t)v << 16);
        if ((uintptr_t)(void *)dst & 3u) { *dst++ = v; n--; }
        while (n >= 8) {
            uint32_t *w = (uint32_t *)(void *)dst;
            w[0] = pair; w[1] = pair; w[2] = pair; w[3] = pair;
            dst += 8;
            n -= 8;
        }
        while (n >= 2) {
            *(uint32_t *)(void *)dst = pair;
            dst += 2;
            n -= 2;
        }
        if (n) *dst = v;
    }
#else
    memset(dst, v, n);
#endif
}

#endif /* MOY_PIXEL_H */
