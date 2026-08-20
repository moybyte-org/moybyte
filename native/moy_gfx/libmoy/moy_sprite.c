/* Sprites, the sheet, the tilemap and text (SPEC.md 3.2, 3.3, 6, 7.1, 7.2).
 *
 * Split from the primitives because these are the verbs that read ASSETS, and
 * because they are where the transparency rules live -- three independent
 * sources (an image's own key, the call's colorkey, the cart's palt) that all
 * have to be consulted, since they come from three different places.
 */

#include <string.h>

#include "moy.h"
#include "moy_pixel.h"

/* moy_put comes from moy_pixel.h -- internal to the library, so a host still
 * cannot reach past camera/clip/pal, and there is now one copy rather than the
 * two this file used to keep in step by hand. */

/* ---------------------------------------------------------------- text -- */

void moy_print(moy_canvas *c, const uint8_t *s, size_t len, int x, int y, int col)
{
    /* One 8px cell per BYTE (SPEC.md 6). Column j left to right, bit 0 of each
     * column byte is the TOP row, and every byte advances -- including the ones
     * with no glyph, which is what keeps two implementations agreeing about
     * where the text AFTER a stray byte lands. */
    size_t k;
    int cx = x;
    /* Hoisted for the same reason as line(): a glyph is up to 64 moy_put calls
     * and every one of them would re-read camera, clip and the palette entry
     * through the canvas pointer. */
    moy_pixel v = c->store[col & 63];
    int cam_x = c->cam_x, cam_y = c->cam_y, cw = c->w;
    int cx0 = c->clip_x0, cy0 = c->clip_y0, cx1 = c->clip_x1, cy1 = c->clip_y1;
    moy_pixel *pix = c->pix;
    for (k = 0; k < len; k++) {
        int code = s[k];
        if (code >= 0x20 && code <= 0x7F) {
            const uint8_t *g = moy_font_data + (code - 0x20) * 8;
            int j;
            for (j = 0; j < 8; j++) {
                int bits = g[j], py = y, gx = cx + j - cam_x;
                if (gx < cx0 || gx >= cx1) continue;   /* whole column off-clip */
                while (bits) {
                    if (bits & 1) {
                        int gy = py - cam_y;
                        if (gy >= cy0 && gy < cy1)
                            pix[(size_t)gy * (size_t)cw + (size_t)gx] = v;
                    }
                    bits >>= 1;
                    py++;
                }
            }
        }
        cx += 8;
    }
}

/* --------------------------------------------------------------- sheet -- */

void moy_sheet_init(moy_sheet *s, uint8_t *pix) { s->pix = pix; }

int moy_sheet_pget(const moy_sheet *s, int x, int y)
{
    /* 0 outside the sheet, so sspr reading past the edge samples blank rather
     * than trapping. */
    if (x < 0 || x >= MOY_SHEET_W || y < 0 || y >= MOY_SHEET_H) return 0;
    return s->pix[y * MOY_SHEET_W + x];
}

void moy_spr(moy_canvas *c, const moy_sheet *s, int n, int x, int y,
             int colorkey, int scale, int flip)
{
    int ox, oy, sy, sx, fx, fy;
    if (n < 0 || n >= MOY_TILES) return;      /* a blank tile is legal, not an error */
    ox = (n % MOY_SHEET_COLS) * MOY_TILE;     /* SPEC.md 3.2 addressing */
    oy = (n / MOY_SHEET_COLS) * MOY_TILE;
    fx = flip & MOY_FLIP_X;
    fy = (flip >> 1) & 1;
    if (scale < 1) scale = 1;
    if (scale == 1) {
        /* The hot path, and the reason it is written out rather than left to
         * moy_put: at scale 1 the camera offset and the clip rect are the same
         * for all 64 pixels, so resolving them ONCE into loop bounds removes
         * two subtractions and four comparisons per pixel. Measured on an
         * ESP32-P4 -- where a 256 KB L2 makes instruction count the limit
         * rather than memory bandwidth -- the per-pixel form cost 1.8x on spr
         * and 2.1x on map (which is spr in a loop) against a kernel that
         * hoisted them. A bandwidth-bound board hides that entirely, which is
         * why it went unnoticed: the ESP32-S3 measured 1.06x for the same code.
         *
         * The pixels are identical by construction: same source order, same
         * store, and the same transparency answer -- the general path's third
         * test, `p < 0`, cannot fire on either, because a sheet pixel is a
         * uint8_t. The conformance goldens are what say so rather than this
         * comment. */
        int px0 = x - c->cam_x, py0 = y - c->cam_y;
        int gx0 = px0 > c->clip_x0 ? px0 : c->clip_x0;
        int gy0 = py0 > c->clip_y0 ? py0 : c->clip_y0;
        int gx1 = px0 + MOY_TILE < c->clip_x1 ? px0 + MOY_TILE : c->clip_x1;
        int gy1 = py0 + MOY_TILE < c->clip_y1 ? py0 + MOY_TILE : c->clip_y1;
        const uint8_t *spix = s->pix;
        const uint8_t *palt = c->palt;
        const moy_pixel *store = c->store;
        int cw = c->w, py, px;
        if (gx0 >= gx1 || gy0 >= gy1) return;
        for (py = gy0; py < gy1; py++) {
            int ty = py - py0;
            const uint8_t *srow = spix + (size_t)(oy + (fy ? MOY_TILE - 1 - ty : ty))
                                       * MOY_SHEET_W + ox;
            moy_pixel *drow = c->pix + (size_t)py * (size_t)cw;
            for (px = gx0; px < gx1; px++) {
                int tx = px - px0;
                int p = srow[fx ? MOY_TILE - 1 - tx : tx];
                if (p == colorkey || palt[p & 63]) continue;
                drow[px] = store[p & 63];
            }
        }
        return;
    }
    {
        /* scale > 1: the SAME hoist, generalised. This used to emit one
         * moy_rect per SOURCE pixel -- 64 calls for one 8x8 tile, each
         * re-deriving the camera offset, four clip clamps, an early-out and an
         * alignment phase, to write a scale x scale block. moy_rect earns that
         * setup when it is filling a span; at 2x2 it is nearly all of the work.
         * A zoomed PICO-8 port makes the shape obvious: celeste at 2x draws its
         * map twice a frame, so ~512 sprites became ~32k moy_rect calls.
         *
         * Measured 1.12x on the celeste-shaped workload (a 16x16 map at 2x,
         * drawn twice) on x86 at -O2. Modest, and worth saying plainly: the
         * scaled path was never the disaster the call count suggests, so this
         * is not what makes a 2x port cheap. Drawing 3.75x the pixels is.
         *
         * Pixel-identical to the moy_rect form by construction: same source
         * order, same clip (max/min against the same rect), same destination
         * addressing (c->pix + py*c->w), same store[p & 63]. The dropped
         * `p < 0` test cannot fire -- a sheet pixel is a uint8_t -- which is
         * the argument the scale-1 path above already makes. The conformance
         * goldens are what say so rather than this comment. */
        int span = MOY_TILE * scale;
        int px0 = x - c->cam_x, py0 = y - c->cam_y;
        int gx0 = px0 > c->clip_x0 ? px0 : c->clip_x0;
        int gy0 = py0 > c->clip_y0 ? py0 : c->clip_y0;
        int gx1 = px0 + span < c->clip_x1 ? px0 + span : c->clip_x1;
        int gy1 = py0 + span < c->clip_y1 ? py0 + span : c->clip_y1;
        const uint8_t *spix = s->pix;
        const uint8_t *palt = c->palt;
        const moy_pixel *store = c->store;
        moy_pixel *cpix = c->pix;
        int cw = c->w;
        int sx_lo, sx_hi, sy_lo, sy_hi;
        if (gx0 >= gx1 || gy0 >= gy1) return;
        /* Walk the SOURCE pixels that survive the clip, not the destination
         * ones: the transparency question then costs the same 64 asks the old
         * form paid, and each answer fills its whole scale x scale block. (An
         * earlier rewrite walked destination rows instead and asked once per
         * ROW -- scale times too often -- which measured 0.78x, i.e. slower
         * than what it replaced. Hence this note: the win here is dropping the
         * per-source-pixel moy_rect CALL, not asking anything less often.) */
        sx_lo = (gx0 - px0) / scale;          /* gx0 >= px0, so these floor cleanly */
        sx_hi = (gx1 - 1 - px0) / scale + 1;
        sy_lo = (gy0 - py0) / scale;
        sy_hi = (gy1 - 1 - py0) / scale + 1;
        for (sy = sy_lo; sy < sy_hi; sy++) {
            int dy0 = py0 + sy * scale, dy1 = dy0 + scale, dy;
            const uint8_t *srow;
            if (dy0 < gy0) dy0 = gy0;
            if (dy1 > gy1) dy1 = gy1;
            srow = spix + (size_t)(oy + (fy ? MOY_TILE - 1 - sy : sy))
                        * MOY_SHEET_W + ox;
            for (sx = sx_lo; sx < sx_hi; sx++) {
                int p = srow[fx ? MOY_TILE - 1 - sx : sx];
                int dx0, dx1, dx;
                moy_pixel v;
                if (p == colorkey || palt[p & 63]) continue;
                dx0 = px0 + sx * scale;
                dx1 = dx0 + scale;
                if (dx0 < gx0) dx0 = gx0;
                if (dx1 > gx1) dx1 = gx1;
                v = store[p & 63];
                for (dy = dy0; dy < dy1; dy++) {
                    moy_pixel *drow = cpix + (size_t)dy * (size_t)cw;
                    for (dx = dx0; dx < dx1; dx++) drow[dx] = v;
                }
            }
        }
    }

}

void moy_sspr(moy_canvas *c, const moy_sheet *s, int sx, int sy, int sw, int sh,
              int dx, int dy, int dw, int dh, int colorkey, int flip)
{
    /* Addresses the sheet in PIXELS and scales arbitrarily -- that is the whole
     * difference from spr. Nearest-neighbour. PROVISIONAL (SPEC.md 6.1). */
    int i, j, fx = flip & 1, fy = (flip >> 1) & 1;
    moy_ds d;
    const uint8_t *palt;
    const moy_pixel *store;
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
    d = moy_ds_of(c);
    palt = c->palt;
    store = c->store;
    /* The source step stays a DIVISION rather than an incremental error term.
     * Stepping looks like the obvious win and measured a LOSS: the common
     * shape here is a 1-pixel-wide column (a raycaster wall slice), where
     * dw == 1 makes the division trivial and the carry loop pure overhead --
     * 6477 -> 6796 us on an ESP32-P4. Hoisting the draw state out of the
     * per-pixel path is where the real gain was. */
    for (j = 0; j < dh; j++) {
        int v = (j * sh) / dh;
        int ty = dy + j;
        if (fy) v = sh - 1 - v;
        for (i = 0; i < dw; i++) {
            int u = (i * sw) / dw, p;
            if (fx) u = sw - 1 - u;
            p = moy_sheet_pget(s, sx + u, sy + v);
            if (p != colorkey && !palt[p & 63])
                moy_ds_put(&d, dx + i, ty, store[p & 63]);
        }
    }
}

void moy_tline(moy_canvas *c, const moy_sheet *s, const moy_map *m,
               int x0, int y0, int x1, int y1,
               int32_t u, int32_t v, int32_t du, int32_t dv, int colorkey)
{
    /* Exactly moy_line's pixels, sampling the MAP as a virtual texture --
     * (m->w*8 x m->h*8 pixels), texel (uu>>16, vv>>16), advancing u,v for
     * EVERY walked pixel, clipped or empty alike. PROVISIONAL (SPEC.md 6.1).
     *
     * The texture arithmetic is reduced ONCE, before the loop:
     * (a + n*b) mod T == ((a mod T) + n*(b mod T)) mod T, so wrapping the
     * start and the step leaves every sample identical while the loop needs
     * no division at all -- one conditional correction per axis per pixel
     * keeps the cursor in [0, T). This is not a micro-nicety: the naive form
     * takes two 64-bit modulos per texel, which on a 32-bit target are two
     * SOFTWARE LIBRARY CALLS, and measured ~660ns/texel against ~74ns for a
     * plain fill. Reduction also proves the accumulators fit int32 (T is at
     * most 1024<<16 = 2^26), so no 64-bit arithmetic survives either.
     *
     * After the wrap, px/py are in range BY CONSTRUCTION, so the map cell and
     * sheet pixel are read directly rather than through the bounds-checked
     * accessors. The write still goes through moy_put: camera, clip and pal
     * are exactly line()'s. Pixel-for-pixel identity with the reference is
     * held by the conformance golden, which is what lets this loop be fast
     * without being trusted. */
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y0 - y1 : y1 - y0;
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    int tw = m->w * MOY_TILE, th = m->h * MOY_TILE;
    int32_t tu, tv, uu, vv, dur, dvr;
    const uint8_t *cells, *spix, *palt;
    const moy_pixel *store;
    moy_ds d;
    int mw;
    if (tw <= 0 || th <= 0) return;
    d = moy_ds_of(c);
    palt = c->palt;
    store = c->store;
    tu = (int32_t)tw << 16;
    tv = (int32_t)th << 16;
    uu = u % tu;   if (uu < 0) uu += tu;
    vv = v % tv;   if (vv < 0) vv += tv;
    dur = du % tu;
    dvr = dv % tv;
    cells = m->cells;
    spix = s->pix;
    mw = m->w;
    for (;;) {
        int px = (int)(uu >> 16), py = (int)(vv >> 16);
        int cell = cells[(py >> 3) * mw + (px >> 3)];
        if (cell) {                              /* 0 = empty (SPEC.md 3.3) */
            int tid = cell - 1;
            int p = spix[((tid >> 4) * MOY_TILE + (py & 7)) * MOY_SHEET_W
                         + (tid & 15) * MOY_TILE + (px & 7)];
            if (p != colorkey && !palt[p & 63])
                moy_ds_put(&d, x0, y0, store[p & 63]);
        }
        uu += dur; if (uu >= tu) uu -= tu; else if (uu < 0) uu += tu;
        vv += dvr; if (vv >= tv) vv -= tv; else if (vv < 0) vv += tv;
        if (x0 == x1 && y0 == y1) break;
        {
            int e2 = 2 * err;
            if (e2 >= dy) { err += dy; x0 += sx; }
            if (e2 <= dx) { err += dx; y0 += sy; }
        }
    }
}

/* ----------------------------------------------------------------- map -- */

void moy_map_init(moy_map *m, uint8_t *cells, int w, int h)
{
    m->cells = cells;
    m->w = w;
    m->h = h;
}

int moy_mget(const moy_map *m, int x, int y)
{
    /* -1 for empty AND for out of range, deliberately collapsed (SPEC.md 7.2):
     * a cart walking off the edge of its level should see the same nothing it
     * sees in a hole, so collision code needs no bounds check of its own. */
    if (x < 0 || x >= m->w || y < 0 || y >= m->h) return -1;
    return (int)m->cells[y * m->w + x] - 1;
}

void moy_mset(moy_map *m, int x, int y, int tile)
{
    if (x < 0 || x >= m->w || y < 0 || y >= m->h) return;
    if (tile < 0) {
        m->cells[y * m->w + x] = 0;
        return;
    }
    if (tile > MOY_MAP_MAX_ID) tile = MOY_MAP_MAX_ID;
    m->cells[y * m->w + x] = (uint8_t)(tile + 1);
}

void moy_map_draw(moy_canvas *c, const moy_map *m, const moy_sheet *s,
                  int mx, int my, int w, int h, int sx, int sy,
                  int colorkey, int scale)
{
    /* Straight per-cell blit through moy_spr, so camera, clip, pal and palt all
     * apply and a map tile is pixel-identical to the same tile drawn by hand.
     * Empty cells are skipped, leaving whatever was underneath -- which is what
     * makes a tilemap composable with a background. */
    int cy, cx, step;
    if (scale < 1) scale = 1;
    step = MOY_TILE * scale;
    for (cy = 0; cy < h; cy++) {
        for (cx = 0; cx < w; cx++) {
            int tid = moy_mget(m, mx + cx, my + cy);
            if (tid < 0) continue;
            moy_spr(c, s, tid, sx + cx * step, sy + cy * step,
                    colorkey, scale, MOY_FLIP_NONE);
        }
    }
}
