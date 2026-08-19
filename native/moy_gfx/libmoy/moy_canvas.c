/* The raster (SPEC.md 6, 7.1, 7.2).
 *
 * Every verb funnels through moy_put (per-pixel) or moy_rect (spans), and
 * those two apply all four pieces of draw state -- camera, clip, pal, palt --
 * so a verb added later inherits them by construction rather than by someone
 * remembering to. The two are the only places that touch c->pix.
 *
 * These are transcriptions of moy-spec's moycore/canvas.py, which is itself
 * verified byte-for-byte against the reference console. Where a choice looked
 * arbitrary it was kept anyway: `circ`'s truncating sqrt, `circb`'s midpoint
 * error term, Bresenham's tie-breaking. Those ARE the specification at the
 * pixel level, and "cleaning them up" would silently fork the raster.
 */

/* No math.h: the raster is integer-only since circ stopped calling sqrt. */
#include <string.h>

#include "moy.h"
#include "moy_pixel.h"

/* ------------------------------------------------------------------ state */

/* What index `i` becomes in the buffer, before pal is applied. The only
 * function in the library that knows the difference between the two builds
 * beyond moy_pixel.h. */
static moy_pixel moy_wire_of(const moy_canvas *c, int i)
{
#ifdef MOY_PIXEL_RGB565
    return c->wire[i & 63];
#else
    (void)c;
    return (moy_pixel)(i & 63);
#endif
}

static void moy_store_rebuild(moy_canvas *c)
{
    int i;
    for (i = 0; i < MOY_PALETTE; i++) c->store[i] = moy_wire_of(c, c->pal[i]);
}

void moy_canvas_init(moy_canvas *c, moy_pixel *pix, int w, int h)
{
    c->pix = pix;
    c->w = w;
    c->h = h;
#ifdef MOY_PIXEL_RGB565
    /* Canonical RGB565 from the SPEC.md 2.2 palette, so an uninitialised wire
     * table is right rather than black. moy_canvas_wire overrides it. */
    {
        int i;
        for (i = 0; i < MOY_PALETTE; i++) {
            const uint8_t *e = moy_palette_default + i * 3;
            c->wire[i] = (uint16_t)(((e[0] & 0xF8) << 8) |
                                    ((e[1] & 0xFC) << 3) | (e[2] >> 3));
        }
    }
#endif
    moy_reset_state(c);
}

#ifdef MOY_PIXEL_RGB565
void moy_canvas_wire(moy_canvas *c, const uint16_t tab[MOY_PALETTE])
{
    int i;
    for (i = 0; i < MOY_PALETTE; i++) c->wire[i] = tab[i];
    moy_store_rebuild(c);
}
#endif

void moy_reset_state(moy_canvas *c)
{
    int i;
    c->cam_x = c->cam_y = 0;
    c->clip_x0 = c->clip_y0 = 0;
    c->clip_x1 = c->w;
    c->clip_y1 = c->h;
    for (i = 0; i < MOY_PALETTE; i++) {
        c->pal[i] = (uint8_t)i;
        c->palt[i] = 0;
    }
    moy_store_rebuild(c);
}

void moy_camera(moy_canvas *c, int x, int y) { c->cam_x = x; c->cam_y = y; }
void moy_camera_reset(moy_canvas *c)         { c->cam_x = c->cam_y = 0; }

void moy_clip(moy_canvas *c, int x, int y, int w, int h)
{
    /* Clamped to the canvas, so an oversized rect is a full screen rather than
     * a buffer overrun. Screen space: applied AFTER the camera offset, which is
     * what lets a scrolling cart pin a HUD clip while the world moves. */
    int x1 = x + w, y1 = y + h;
    c->clip_x0 = x < 0 ? 0 : x;
    c->clip_y0 = y < 0 ? 0 : y;
    c->clip_x1 = x1 > c->w ? c->w : x1;
    c->clip_y1 = y1 > c->h ? c->h : y1;
}

void moy_clip_reset(moy_canvas *c)
{
    c->clip_x0 = c->clip_y0 = 0;
    c->clip_x1 = c->w;
    c->clip_y1 = c->h;
}

void moy_pal(moy_canvas *c, int c0, int c1)
{
    c->pal[c0 & 63] = (uint8_t)(c1 & 63);
    c->store[c0 & 63] = moy_wire_of(c, c1);   /* O(1): only this entry moved */
}

void moy_pal_reset(moy_canvas *c)
{
    int i;
    for (i = 0; i < MOY_PALETTE; i++) c->pal[i] = (uint8_t)i;
    moy_store_rebuild(c);
}

void moy_palt(moy_canvas *c, int col, int on) { c->palt[col & 63] = on ? 1 : 0; }

void moy_palt_reset(moy_canvas *c) { memset(c->palt, 0, MOY_PALETTE); }

/* ------------------------------------------------------------- primitives */

/* floor(n*k / d) for k = 0, 1, 2, ... -- an edge walk, carried forward.
 *
 * The reference walks triangle edges with Python's //, which floors rather than
 * truncating toward zero, and for a negative numerator the two disagree by one
 * pixel -- a whole column of a leaning triangle. Rather than divide twice a
 * row to get that, the quotient is kept alongside its remainder and both are
 * stepped: exactly the same floor, no division in the loop. The correction
 * loops run |inc|/d times per step and |inc| times over the whole edge, so the
 * cost is linear in the triangle's width however steep it is. */
typedef struct { int q, rem, d, iq, ir; } moy_edge;

static void moy_edge_init(moy_edge *e, int d, int inc)
{
    /* Split the per-row increment ONCE into whole rows and a remainder, both
     * floored, so a step is an add and a single conditional.
     *
     * The obvious version -- carry the raw numerator and correct with a while
     * loop -- is a trap, and measured like one: a wide shallow triangle has
     * |inc| many times d, so the loop runs |inc|/d times a row and lost to the
     * plain division it replaced (3752 -> 3953 us on an ESP32-P4). Splitting
     * up front bounds the correction at one, whatever the slope. */
    e->d = d;
    e->iq = inc >= 0 ? inc / d : -(((-inc) + d - 1) / d);
    e->ir = inc - e->iq * d;              /* 0 <= ir < d, by construction */
    e->q = 0;
    e->rem = 0;
}

static void moy_edge_step(moy_edge *e)
{
    /* rem and ir are both in [0, d), so their sum is below 2d: one conditional
     * is enough, never a loop. */
    e->q += e->iq;
    e->rem += e->ir;
    if (e->rem >= e->d) { e->rem -= e->d; e->q++; }
}

/* moy_put now lives in moy_pixel.h, so moy_sprite.c gets the same one rather
 * than a hand-kept copy. */

void moy_cls(moy_canvas *c, int col)
{
    /* Ignores camera and clip: a full-surface reset, not a rect. It DOES honour
     * pal, so a cart running a global recolour clears to the remapped colour
     * instead of punching an unremapped hole in its own effect. */
    moy_fill(c->pix, c->store[col & 63], (size_t)c->w * (size_t)c->h);
}

void moy_pix(moy_canvas *c, int x, int y, int col) { moy_put(c, x, y, col); }

int moy_pget(const moy_canvas *c, int x, int y)
{
    /* Camera-relative like the write side, so a cart that sets a camera and
     * probes a world coordinate gets the pixel it drew there.
     *
     * SPEC.md 6 says this returns an INDEX, and it does in both builds -- the
     * direct-colour one searches its wire table back. That is 64 comparisons on
     * a verb carts call rarely and never per pixel, and the alternative
     * (returning the raw word) would make pget mean two different things on two
     * hosts, which is precisely what the indexed contract exists to prevent.
     * Two indices sharing a wire word is a degenerate palette; the lower index
     * wins, deterministically. */
    x -= c->cam_x;
    y -= c->cam_y;
    if (x < 0 || x >= c->w || y < 0 || y >= c->h) return 0;
#ifdef MOY_PIXEL_RGB565
    {
        uint16_t w = c->pix[y * c->w + x];
        int i;
        for (i = 0; i < MOY_PALETTE; i++) if (c->wire[i] == w) return i;
        return 0;
    }
#else
    return c->pix[y * c->w + x];
#endif
}

void moy_rect(moy_canvas *c, int x, int y, int w, int h, int col)
{
    /* The span path: camera-offset the corner, intersect with the clip rect,
     * write whole rows. Every span-shaped verb (circ, tri, scaled spr) routes
     * through here so they all clip identically. */
    int x0, y0, x1, y1, yy, n;
    moy_pixel ci;
    x -= c->cam_x;
    y -= c->cam_y;
    x0 = x > c->clip_x0 ? x : c->clip_x0;
    y0 = y > c->clip_y0 ? y : c->clip_y0;
    x1 = (x + w) < c->clip_x1 ? (x + w) : c->clip_x1;
    y1 = (y + h) < c->clip_y1 ? (y + h) : c->clip_y1;
    if (x1 <= x0 || y1 <= y0) return;
    ci = c->store[col & 63];
    n = x1 - x0;
    {
        /* Alignment is decided ONCE. Every row starts at the same 4-byte phase
         * -- the stride does not change between them -- so the per-row test
         * moy_fill would repeat is answerable here, and a span-shaped verb
         * emits thousands of these. Rows that start odd write one pixel to get
         * aligned and hand the rest over; rows that start aligned skip even
         * that branch. */
        moy_pixel *row = c->pix + (size_t)y0 * (size_t)c->w + (size_t)x0;
        size_t step = (size_t)c->w;
        int odd;
        if (y1 - y0 == 1) {
            /* A single span has nothing to amortise the decision over, and
             * one-row rects are not a corner case: circ emits one per row and
             * so does every scanline verb that routes through here. Hoisting
             * unconditionally cost circ 1636 -> 1703 us. */
            moy_fill(row, ci, (size_t)n);
            return;
        }
        odd = (sizeof(moy_pixel) == 2) && (((uintptr_t)(void *)row & 3u) != 0);
        for (yy = y0; yy < y1; yy++, row += step) {
            if (odd && n > 0) {
                row[0] = ci;
                moy_fill_aligned(row + 1, ci, (size_t)n - 1);
            } else {
                moy_fill_aligned(row, ci, (size_t)n);
            }
        }
    }
}

void moy_rectb(moy_canvas *c, int x, int y, int w, int h, int col)
{
    /* Four one-pixel rects, so the corners are written twice and the whole
     * thing clips like any other span verb. */
    moy_rect(c, x,         y,         w, 1, col);
    moy_rect(c, x,         y + h - 1, w, 1, col);
    moy_rect(c, x,         y,         1, h, col);
    moy_rect(c, x + w - 1, y,         1, h, col);
}

void moy_line(moy_canvas *c, int x0, int y0, int x1, int y1, int col)
{
    /* Bresenham, both endpoints inclusive. */
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y0 - y1 : y1 - y0;   /* negative |dy|, as the classic form */
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy, e2;
    /* Camera, clip and the stored colour are loop-invariant, so they are read
     * once instead of per pixel. moy_put would re-read all six through `c`,
     * and the store to c->pix[] may alias the struct, so the compiler cannot
     * hoist them for us -- worth 1.5x on a board where instruction count is
     * the limit rather than memory bandwidth. */
    moy_pixel v = c->store[col & 63];
    int cam_x = c->cam_x, cam_y = c->cam_y, cw = c->w;
    int cx0 = c->clip_x0, cy0 = c->clip_y0, cx1 = c->clip_x1, cy1 = c->clip_y1;
    moy_pixel *pix = c->pix;
    int lo, hi, a, b;

    /* AXIS-ALIGNED. Bresenham degenerates on these: with dy == 0 only x can
     * advance, with dx == 0 only y can, so the pixel SET is exactly a run and
     * writing it as one is not an approximation of the general loop, it is the
     * same pixels without the per-step error arithmetic. Horizontal rules,
     * borders and bar charts are most of what carts draw with line(). */
    if (y0 == y1) {
        int py = y0 - cam_y;
        if (py < cy0 || py >= cy1) return;
        lo = (x0 < x1 ? x0 : x1) - cam_x;
        hi = (x0 < x1 ? x1 : x0) + 1 - cam_x;
        if (lo < cx0) lo = cx0;
        if (hi > cx1) hi = cx1;
        if (hi > lo) moy_fill(pix + (size_t)py * (size_t)cw + (size_t)lo, v,
                              (size_t)(hi - lo));
        return;
    }
    if (x0 == x1) {
        int px = x0 - cam_x, yy;
        if (px < cx0 || px >= cx1) return;
        lo = (y0 < y1 ? y0 : y1) - cam_y;
        hi = (y0 < y1 ? y1 : y0) + 1 - cam_y;
        if (lo < cy0) lo = cy0;
        if (hi > cy1) hi = cy1;
        for (yy = lo; yy < hi; yy++)
            pix[(size_t)yy * (size_t)cw + (size_t)px] = v;
        return;
    }

    /* WHOLLY VISIBLE. A line inside the clip rect cannot leave it -- both
     * endpoints are in and the walk is monotone in each axis -- so the four
     * comparisons per pixel are answerable once, here, for the whole line. */
    a = (x0 < x1 ? x0 : x1) - cam_x;  b = (x0 < x1 ? x1 : x0) - cam_x;
    lo = (y0 < y1 ? y0 : y1) - cam_y; hi = (y0 < y1 ? y1 : y0) - cam_y;
    if (a >= cx0 && b < cx1 && lo >= cy0 && hi < cy1) {
        for (;;) {
            pix[(size_t)(y0 - cam_y) * (size_t)cw + (size_t)(x0 - cam_x)] = v;
            if (x0 == x1 && y0 == y1) break;
            e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
        return;
    }

    for (;;) {
        int px = x0 - cam_x, py = y0 - cam_y;
        if (px >= cx0 && px < cx1 && py >= cy0 && py < cy1)
            pix[(size_t)py * (size_t)cw + (size_t)px] = v;
        if (x0 == x1 && y0 == y1) break;
        e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

void moy_circ(moy_canvas *c, int cx, int cy, int r, int col)
{
    /* One span per row, half-width from the circle equation TRUNCATED toward
     * zero. r < 0 draws nothing; r == 0 is a single pixel.
     *
     * Computed with integers rather than a sqrt per row: `span` is the
     * largest s with s*s <= r*r - dy*dy, which is exactly what truncating the
     * correctly-rounded sqrt of an exact integer gives, and it moves by at most
     * a little between adjacent rows, so walking it costs O(r) over the whole
     * circle instead of 2r+1 library calls. This was the slowest verb in the
     * console by 3x. It also leaves the raster with no libm dependency. */
    int dy, span = 0;
    /* Read once, then written through for the whole circle -- see moy_ds. The
     * span used to go through moy_rect, which re-derives the camera offset and
     * re-clamps against the clip rect for every one of the 2r+1 rows; a filled
     * circle is nothing BUT spans, so that prologue was most of the verb.
     * Identical pixels -- same clamp, same moy_fill -- measured 1.1x faster on
     * an ESP32-P4 through a host that had hand-written the direct form. */
    moy_pixel v = c->store[col & 63];
    moy_pixel *pix = c->pix;
    int cam_x = c->cam_x, cam_y = c->cam_y, cw = c->w;
    int cx0 = c->clip_x0, cy0 = c->clip_y0, cx1 = c->clip_x1, cy1 = c->clip_y1;
    if (r < 0) return;
    for (dy = -r; dy <= r; dy++) {
        int t = r * r - dy * dy;
        int px0, px1, py;
        while ((span + 1) * (span + 1) <= t) span++;
        while (span > 0 && span * span > t) span--;
        py = cy + dy - cam_y;
        if (py < cy0 || py >= cy1) continue;
        px0 = cx - span - cam_x;
        px1 = cx + span + 1 - cam_x;
        if (px0 < cx0) px0 = cx0;
        if (px1 > cx1) px1 = cx1;
        if (px1 > px0)
            moy_fill(pix + (size_t)py * (size_t)cw + (size_t)px0, v,
                     (size_t)(px1 - px0));
    }
}

void moy_circb(moy_canvas *c, int cx, int cy, int r, int col)
{
    /* Midpoint, eight-way symmetry. NOT the boundary of circ() -- an outline
     * and a fill are different rasterizations, which is why the spec keeps them
     * as separate verbs. */
    int x = r, y = 0, err = 0;
    moy_ds d = moy_ds_of(c);
    moy_pixel v = c->store[col & 63];
    while (x >= y) {
        moy_ds_put(&d, cx + x, cy + y, v);
        moy_ds_put(&d, cx + y, cy + x, v);
        moy_ds_put(&d, cx - y, cy + x, v);
        moy_ds_put(&d, cx - x, cy + y, v);
        moy_ds_put(&d, cx - x, cy - y, v);
        moy_ds_put(&d, cx - y, cy - x, v);
        moy_ds_put(&d, cx + y, cy - x, v);
        moy_ds_put(&d, cx + x, cy - y, v);
        y += 1;
        if (err <= 0) {
            err += 2 * y + 1;
        } else {
            x -= 1;
            err -= 2 * x + 1;
        }
    }
}

/* --------------------------------------------------- provisional (6.1) --- */

void moy_tri(moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col)
{
    int t, y, dy_long, dy_top, dy_bot;
    moy_edge ea, etop, ebot;
    /* Read once, then written through for the whole triangle -- see moy_ds. */
    moy_pixel v = c->store[col & 63];
    moy_pixel *pix = c->pix;
    int cam_x = c->cam_x, cam_y = c->cam_y, cw = c->w;
    int cx0 = c->clip_x0, cy0 = c->clip_y0, cx1 = c->clip_x1, cy1 = c->clip_y1;
    /* sort by y */
    if (y1 > y2) { t = x1; x1 = x2; x2 = t; t = y1; y1 = y2; y2 = t; }
    if (y1 > y3) { t = x1; x1 = x3; x3 = t; t = y1; y1 = y3; y3 = t; }
    if (y2 > y3) { t = x2; x2 = x3; x3 = t; t = y2; y2 = y3; y3 = t; }
    if (y3 == y1) {                       /* flat: one span through all three x */
        int lo = x1 < x2 ? x1 : x2, hi = x1 > x2 ? x1 : x2;
        if (x3 < lo) lo = x3;
        if (x3 > hi) hi = x3;
        moy_rect(c, lo, y1, hi - lo + 1, 1, col);
        return;
    }
    dy_long = y3 - y1;
    dy_top  = y2 - y1;
    dy_bot  = y3 - y2;
    moy_edge_init(&ea, dy_long, x3 - x1);
    if (dy_top) moy_edge_init(&etop, dy_top, x2 - x1);
    if (dy_bot) moy_edge_init(&ebot, dy_bot, x3 - x2);
    for (y = y1; y <= y3; y++) {
        int xa = x1 + ea.q, xb;
        if (y < y2)      xb = x1 + etop.q;
        else if (dy_bot) xb = x2 + ebot.q;
        else             xb = x3;
        if (xa > xb) { t = xa; xa = xb; xb = t; }
        /* The span, written directly rather than through moy_rect: a triangle
         * emits one per row and moy_rect would re-derive the camera offset and
         * re-clamp against the clip rect every time. Identical pixels -- same
         * clamp, same moy_fill -- with the invariants read once above. */
        {
            int px0 = xa - cam_x, px1 = xb + 1 - cam_x, py = y - cam_y;
            if (py >= cy0 && py < cy1) {
                if (px0 < cx0) px0 = cx0;
                if (px1 > cx1) px1 = cx1;
                if (px1 > px0)
                    moy_fill(pix + (size_t)py * (size_t)cw + (size_t)px0, v,
                             (size_t)(px1 - px0));
            }
        }
        moy_edge_step(&ea);
        if (y < y2) { if (dy_top) moy_edge_step(&etop); }
        else if (dy_bot) moy_edge_step(&ebot);
    }
}

void moy_trib(moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col)
{
    moy_line(c, x1, y1, x2, y2, col);
    moy_line(c, x2, y2, x3, y3, col);
    moy_line(c, x3, y3, x1, y1, col);
}

/* -------------------------------------------------------------- readout -- */

void moy_palette_rgb888(const moy_canvas *c, const uint8_t *pal, uint8_t *out)
{
    size_t i, n = (size_t)c->w * (size_t)c->h;
#ifdef MOY_PIXEL_RGB565
    /* The buffer already lost the low bits when colour was resolved, so this
     * expands 5/6/5 back to 8/8/8 by bit replication rather than consulting
     * `pal`. Exact for any colour whose channels survive the round trip and
     * approximate otherwise -- if you need the true palette values, hash or
     * export from the index build, which is the one the goldens are made on. */
    (void)pal;
    for (i = 0; i < n; i++) {
        uint16_t w = c->pix[i];
        uint8_t r = (uint8_t)((w >> 11) & 0x1F), g = (uint8_t)((w >> 5) & 0x3F);
        uint8_t b = (uint8_t)(w & 0x1F);
        out[i * 3 + 0] = (uint8_t)((r << 3) | (r >> 2));
        out[i * 3 + 1] = (uint8_t)((g << 2) | (g >> 4));
        out[i * 3 + 2] = (uint8_t)((b << 3) | (b >> 2));
    }
#else
    if (!pal) pal = moy_palette_default;
    for (i = 0; i < n; i++) {
        const uint8_t *e = pal + (size_t)c->pix[i] * 3;
        out[i * 3 + 0] = e[0];
        out[i * 3 + 1] = e[1];
        out[i * 3 + 2] = e[2];
    }
#endif
}

void moy_palette_rgb565(const moy_canvas *c, const uint8_t *pal, uint16_t *out)
{
    size_t n = (size_t)c->w * (size_t)c->h;
#ifdef MOY_PIXEL_RGB565
    /* Already resolved -- and resolved through YOUR wire table, so `pal` would
     * be the wrong authority to apply now. A copy, which is the whole point of
     * this build: the flush stops being a lookup. */
    (void)pal;
    memcpy(out, c->pix, n * sizeof *out);
#else
    uint16_t tab[MOY_PALETTE];
    size_t i;
    if (!pal) pal = moy_palette_default;
    for (i = 0; i < MOY_PALETTE; i++) {
        const uint8_t *e = pal + i * 3;
        tab[i] = (uint16_t)(((e[0] & 0xF8) << 8) | ((e[1] & 0xFC) << 3) | (e[2] >> 3));
    }
    for (i = 0; i < n; i++) out[i] = tab[c->pix[i] & 63];
#endif
}

/* ------------------------------------------------------------- console --- */

void moy_console_init(moy_console *con, moy_canvas *c, moy_sheet *s, moy_map *m)
{
    memset(con, 0, sizeof *con);
    con->canvas = c;
    con->sheet = s;
    con->map = m;
    con->rng = 1;
}

void moy_srand(moy_console *con, uint32_t seed)
{
    con->rng = seed ? seed : 0x9E3779B9u;
}

float moy_rnd(moy_console *con, float n)
{
    /* xorshift32. SPEC.md 9 fixes rnd()'s RANGE and says nothing about its
     * sequence, so two conforming hosts may disagree on every number and both
     * be right -- which is exactly why no conformance scene may call it. A
     * defined generator here at least makes the question askable. */
    uint32_t x = con->rng ? con->rng : 0x9E3779B9u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    con->rng = x;
    return (float)((double)x / 4294967296.0) * n;
}

/* SPEC.md 6 layers. See moy.h for why this ignores camera, clip and pal:
 * it composites a finished buffer rather than drawing into one, which is the
 * same reason cls ignores them.
 *
 * The clamp is what makes a layer usable as a scrolling world without the cart
 * doing bounds arithmetic: a window that runs off the edge repeats the edge
 * column or row instead of reading outside the buffer. A host that wants the
 * other behaviour clamps its own camera before calling. */
void moy_blit_window(moy_canvas *dst, const moy_canvas *src, int cam_x, int cam_y)
{
    int y;
    if (!dst || !src || !dst->pix || !src->pix) return;
    if (src->w <= 0 || src->h <= 0) return;
    for (y = 0; y < dst->h; y++) {
        int sy = cam_y + y;
        int x;
        const moy_pixel *srow;
        moy_pixel *drow;
        if (sy < 0) sy = 0;
        if (sy >= src->h) sy = src->h - 1;
        srow = src->pix + (size_t)sy * (size_t)src->w;
        drow = dst->pix + (size_t)y * (size_t)dst->w;
        for (x = 0; x < dst->w; x++) {
            int sx = cam_x + x;
            if (sx < 0) sx = 0;
            if (sx >= src->w) sx = src->w - 1;
            drow[x] = srow[sx];
        }
    }
}
