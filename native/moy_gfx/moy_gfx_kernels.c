/* The compositor loops, ONCE. moy_gfx_kernels.h says what is here and why.
 *
 * THE PRAGMA IS LOAD-BEARING -- do not drop it when moving code in or out of
 * this file. The MicroPython ports build usermods at -O2 and the cmake route
 * does not reach them (source-file properties are directory-scoped and the
 * objects are compiled by the micropython.elf target -- verified via
 * build.ninja, see the note in modmoy_gfx.c). modmoy_gfx.c has carried the same
 * in-source pragma since #77, and these loops used to sit inside it: extracting
 * them into a new translation unit WITHOUT the pragma would silently drop them
 * to -O2. That is not hypothetical -- it is exactly what happened when six
 * verbs moved into libmoy calls (libmoy_kernels.c's header records the
 * measurement: `line` went 31.2 -> 62.5 us/op on a P4, exactly 2x).
 *
 * -O3 and not -Ofast: -ffast-math reassociates float arithmetic and this raster
 * is conformance-checked against the spec's goldens. The raster is integer
 * throughout today, so there is nothing for -Ofast to perturb -- the rule
 * stands because "no float rewriting in a conformance-checked raster" is the
 * rule, not because of one call site.
 *
 * THE HOST INHERITS THE PRAGMA, deliberately. runtime/native_build.py builds
 * the host bindings at -O2 and argues for it; this file overrides that for
 * itself, because the alternative is a knob whose two settings are two
 * compilations of one file. Nothing is risked by it: these loops are integer,
 * the spec goldens run on the host, and
 * tests/test_gfx_binding.py::test_matches_the_native_moy_gfx diffs this exact
 * object against the board's compilation of it byte for byte.
 *
 * The pragma goes BEFORE the header include on purpose. mg_fill_run and the
 * canvas helpers are `static inline` in the header and are inlined into the
 * functions below; GCC refuses to inline across a mismatch in optimization
 * attributes, so a header included at -O2 into a -O3 file would turn the
 * per-row fill into a per-row CALL. Every file that includes the header must
 * establish its optimization level first -- modmoy_gfx.c does the same.
 */

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O3")
#endif

#include "moy_gfx_kernels.h"
#include "moy_text.h"

/* ---- the shape verbs under a fill pattern ------------------------------ */

void mg_shape(uint16_t *dst, size_t cap, int dw, int kind,
              int a0, int a1, int a2, int a3, int a4, int a5,
              int col, int pat, int hole,
              int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    moy_canvas c;
    if (dw <= 0) return;
    mg_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    mg_canvas_shape(&c, dst, dw, cap, (uint16_t)(col & 0xFFFF), pat, hole,
                    cam_x, cam_y, cx0, cy0, cx1, cy1);
    /* Colour index 0 throughout: mg_canvas_shape put the draw colour there and
     * the hole colour at 1, so libmoy resolves both out of store[] as it does
     * for any cart. */
    switch (kind) {
    case MG_SHAPE_LINE:  moy_line (&c, a0, a1, a2, a3, 0); break;
    case MG_SHAPE_RECT:  moy_rect (&c, a0, a1, a2, a3, 0); break;
    case MG_SHAPE_RECTB: moy_rectb(&c, a0, a1, a2, a3, 0); break;
    case MG_SHAPE_CIRC:  if (a2 >= 0) moy_circ (&c, a0, a1, a2, 0); break;
    case MG_SHAPE_CIRCB: if (a2 >= 0) moy_circb(&c, a0, a1, a2, 0); break;
    case MG_SHAPE_TRI:   moy_tri  (&c, a0, a1, a2, a3, a4, a5, 0); break;
    case MG_SHAPE_TRIB:  moy_trib (&c, a0, a1, a2, a3, a4, a5, 0); break;
    case MG_SHAPE_OVAL:  moy_oval (&c, a0, a1, a2, a3, 0); break;
    case MG_SHAPE_OVALB: moy_ovalb(&c, a0, a1, a2, a3, 0); break;
    default: break;
    }
}

/* ---- fill ------------------------------------------------------------- */

void mg_fill(uint16_t *px, size_t cap, int npix, int color)
{
    int n = npix;
    if (n < 0) n = 0;
    if ((size_t)n > cap) n = (int)cap;
    mg_fill_run(px, (size_t)n, (uint16_t)(color & 0xFFFF));
}

void mg_fill_rect(uint16_t *px, size_t cap, int stride,
                  int x, int y, int w, int h, int color)
{
    uint16_t c = (uint16_t)(color & 0xFFFF);
    if (stride <= 0) return;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x >= stride) return;
    if (x + w > stride) w = stride - x;
    if (w <= 0 || h <= 0) return;
    int max_rows = (int)(cap / (size_t)stride);
    if (y >= max_rows) return;
    if (y + h > max_rows) h = max_rows - y;
    for (int row = 0; row < h; row++) {
        mg_fill_run(px + (size_t)(y + row) * (size_t)stride + (size_t)x,
                    (size_t)w, c);
    }
}

// fill_spans' walk (#163/#167): n packed (x, y, w, h, ci) int16 quads, each
// offset by (ox, oy) and the camera, clipped, filled with `cov` when it is an
// RGB565 word (>= 0) or with pal[ci & 63] otherwise.
void mg_fill_spans(uint16_t *dst, size_t cap, int dw, const int16_t *q, int n,
                   int ox, int oy, int cov, const uint16_t *pal,
                   int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1) {
    if (dw <= 0 || (cov < 0 && pal == NULL)) {
        return;
    }
    mg_clip(dw, cap, &cx0, &cy0, &cx1, &cy1);
    for (int i = 0; i < n; i++) {
        const int16_t *p = q + i * 5;
        uint16_t col = (cov >= 0) ? (uint16_t)cov : pal[p[4] & 63];
        int x0 = (int)p[0] + ox - cam_x;
        int y0 = (int)p[1] + oy - cam_y;
        int x1 = x0 + (int)p[2];
        int y1 = y0 + (int)p[3];
        if (x0 < cx0) x0 = cx0;
        if (y0 < cy0) y0 = cy0;
        if (x1 > cx1) x1 = cx1;
        if (y1 > cy1) y1 = cy1;
        if (x1 <= x0 || y1 <= y0) {
            continue;
        }
        size_t run = (size_t)(x1 - x0);
        uint16_t *row = dst + (size_t)y0 * (size_t)dw + (size_t)x0;
        for (int y = y0; y < y1; y++) {
            mg_fill_run(row, run, col);
            row += (size_t)dw;
        }
    }
}

/* ---- scroll ----------------------------------------------------------- */

void mg_scroll_rect(uint16_t *px, size_t cap, int stride,
                    int rx, int ry, int rw, int rh, int dx, int dy)
{
    if (stride <= 0 || (dx == 0 && dy == 0)) return;
    int rows = (int)(cap / (size_t)stride);
    int x0 = rx < 0 ? 0 : rx;
    int y0 = ry < 0 ? 0 : ry;
    int x1 = rx + rw; if (x1 > stride) x1 = stride;
    int y1 = ry + rh; if (y1 > rows) y1 = rows;
    /* Destination span: the part of the rect whose source is also inside it. */
    int tx0 = x0 + (dx > 0 ? dx : 0);
    int tx1 = x1 + (dx < 0 ? dx : 0);
    int ty0 = y0 + (dy > 0 ? dy : 0);
    int ty1 = y1 + (dy < 0 ? dy : 0);
    if (tx0 >= tx1 || ty0 >= ty1) return;
    size_t cw = (size_t)(tx1 - tx0) * 2u;
    /* Per-row memmove (horizontal overlap safe); the vertical iteration order
     * follows dy so rows are read before they are overwritten. */
    if (dy > 0) {
        for (int ty = ty1 - 1; ty >= ty0; ty--) {
            memmove(px + (size_t)ty * (size_t)stride + (size_t)tx0,
                    px + (size_t)(ty - dy) * (size_t)stride + (size_t)(tx0 - dx),
                    cw);
        }
    } else {
        for (int ty = ty0; ty < ty1; ty++) {
            memmove(px + (size_t)ty * (size_t)stride + (size_t)tx0,
                    px + (size_t)(ty - dy) * (size_t)stride + (size_t)(tx0 - dx),
                    cw);
        }
    }
}

/* ---- blits ------------------------------------------------------------ */

void mg_blit565(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                const uint16_t *src, size_t scap, int sw, int sh, int key,
                int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    if ((size_t)sw * (size_t)sh > scap) sh = (int)(scap / (size_t)sw);
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    for (int row = 0; row < sh; row++) {
        int ty = dy + row;
        if (ty < cy0 || ty >= cy1) continue;
        const uint16_t *srow = src + (size_t)row * (size_t)sw;
        uint16_t *drow = dst + (size_t)ty * (size_t)dw;
        if (key < 0) {
            /* OPAQUE fast lane (#66 CHROMEBRK): no colorkey test means the
             * row's clipped span is one contiguous copy -- memcpy instead of
             * the per-pixel loop. Matters for blit_strip (the cached top bar
             * stamps a 320x18 strip every cart frame) and the paint-image
             * bakes. */
            int s0 = (dx < cx0) ? (cx0 - dx) : 0;
            int s1 = (dx + sw > cx1) ? (cx1 - dx) : sw;
            if (s1 > s0) {
                memcpy(drow + dx + s0, srow + s0, (size_t)(s1 - s0) * 2u);
            }
            continue;
        }
        for (int col = 0; col < sw; col++) {
            int tx = dx + col;
            if (tx < cx0 || tx >= cx1) continue;
            uint16_t p = srow[col];
            /* No `key >= 0` here: the branch above already took that case.
             * modmoy_gfx.c's copy carried the redundant test and the host's did
             * not -- the ONE difference between the two implementations of
             * these 189 lines, and dead on both. */
            if (p == (uint16_t)key) continue;
            drow[tx] = p;
        }
    }
}

void mg_blit565_scale(uint16_t *dst, size_t dcap, int dw, int dh,
                      int dx, int dy,
                      const uint16_t *src, size_t scap, int sw, int sh,
                      int scale)
{
    if (scale < 1) scale = 1;
    if (dw <= 0 || dh <= 0 || sw <= 0 || sh <= 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    if ((size_t)sw * (size_t)sh > scap) sh = (int)(scap / (size_t)sw);
    /* Visible destination-x span of the scaled image, clamped to the buffer. */
    int x0 = dx < 0 ? 0 : dx;
    int x1 = dx + sw * scale;
    if (x1 > dw) x1 = dw;
    if (x1 <= x0) return;
    for (int row = 0; row < sh; row++) {
        int ty0 = dy + row * scale;              /* first dst row of this src row */
        int ty1 = ty0 + scale;                   /* one past its last dst row */
        if (ty1 <= 0 || ty0 >= dh) continue;
        if (ty1 > dh) ty1 = dh;
        const uint16_t *srow = src + (size_t)row * (size_t)sw;
        int wy = ty0 < 0 ? 0 : ty0;              /* first VISIBLE dst row */
        uint16_t *first = dst + (size_t)wy * (size_t)dw;
        /* Expand the source row once into the first visible dst row (run-length
         * stepped: no per-pixel division)... */
        int off = x0 - dx;                       /* >= 0 by construction */
        const uint16_t *sp = srow + off / scale;
        int rep = scale - (off % scale);         /* copies left of the first col */
        uint16_t *out = first + x0;
        int remaining = x1 - x0;
        while (remaining > 0) {
            uint16_t v = *sp++;
            int n = rep < remaining ? rep : remaining;
            for (int i = 0; i < n; i++) out[i] = v;
            out += n;
            remaining -= n;
            rep = scale;
        }
        /* ...then duplicate it to the band's remaining visible rows. */
        for (int ty = wy + 1; ty < ty1; ty++) {
            memcpy(dst + (size_t)ty * (size_t)dw + x0, first + x0,
                   (size_t)(x1 - x0) * 2u);
        }
    }
}

/* One target row of mg_blit565_resize: `n` pixels sampled from `srow` at the
 * 16.16 column `u`, stepping `step`, written as pairs packed into 32-bit
 * stores (mg_fill_run's reason) once `out` is 4-byte aligned. The packing
 * puts the lower address in the low half: every tier is little-endian. */
static void mg_resize_row(uint16_t *out, int n, const uint16_t *srow,
                          uint32_t u, uint32_t step)
{
    if (((uintptr_t)out & 2u) != 0) {
        *out++ = srow[u >> 16];
        u += step;
        n--;
    }
    uint32_t *w = (uint32_t *)out;
    while (n >= 4) {
        uint32_t a = srow[u >> 16]; u += step;
        uint32_t b = srow[u >> 16]; u += step;
        uint32_t c = srow[u >> 16]; u += step;
        uint32_t d = srow[u >> 16]; u += step;
        w[0] = a | (b << 16);
        w[1] = c | (d << 16);
        w += 2;
        n -= 4;
    }
    if (n >= 2) {
        uint32_t a = srow[u >> 16]; u += step;
        uint32_t b = srow[u >> 16]; u += step;
        *w++ = a | (b << 16);
        n -= 2;
    }
    if (n) *((uint16_t *)w) = srow[u >> 16];
}

void mg_blit565_resize(uint16_t *dst, size_t dcap, int dw, int dh,
                       int dx, int dy, int tw, int th,
                       const uint16_t *src, size_t scap, int sw, int sh,
                       int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || dh <= 0 || tw <= 0 || th <= 0) return;
    if (sw <= 0 || sh <= 0 || sw > MG_RESIZE_MAX || sh > MG_RESIZE_MAX) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    int srows = (int)(scap / (size_t)sw);        /* source rows that exist */
    if (srows > sh) srows = sh;
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    int x0 = dx > cx0 ? dx : cx0;
    int x1 = (dx + tw) < cx1 ? (dx + tw) : cx1;
    int y0 = dy > cy0 ? dy : cy0;
    int y1 = (dy + th) < cy1 ? (dy + th) : cy1;
    if (x1 <= x0 || y1 <= y0) return;
    /* Pixel-centre sampling in 16.16: target column i reads source column
     * (i * sx + sx / 2) >> 16. i * sx stays under sw << 16, so a source side
     * up to MG_RESIZE_MAX keeps it in 32 bits. */
    uint32_t sx = ((uint32_t)sw << 16) / (uint32_t)tw;
    uint32_t sy = ((uint32_t)sh << 16) / (uint32_t)th;
    uint32_t u0 = (uint32_t)(x0 - dx) * sx + (sx >> 1);
    uint32_t v = (uint32_t)(y0 - dy) * sy + (sy >> 1);
    int n = x1 - x0;
    int last = -1;                               /* the source row just drawn */
    for (int ty = y0; ty < y1; ty++, v += sy) {
        int row = (int)(v >> 16);
        uint16_t *out = dst + (size_t)ty * (size_t)dw + x0;
        if (row >= srows) { last = -1; continue; }
        if (row == last) {
            /* An upscale repeats a source row: copy the target row above. */
            memcpy(out, out - dw, (size_t)n * 2u);
            continue;
        }
        mg_resize_row(out, n, src + (size_t)row * (size_t)sw, u0, sx);
        last = row;
    }
}

void mg_blit_window(uint16_t *dst, size_t dcap, int dw, int dh,
                    const uint16_t *src, size_t scap, int src_w, int sx, int sy)
{
    if (dw <= 0 || dh <= 0 || src_w <= 0) return;
    if (sx < 0) sx = 0;
    if (sy < 0) sy = 0;
    int cw = dw;                                  /* copy width; dw stays the stride */
    if (sx + cw > src_w) cw = src_w - sx;
    if (cw <= 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    int src_rows = (int)(scap / (size_t)src_w);
    if (sy + dh > src_rows) dh = src_rows - sy;
    if (dh <= 0) return;
    for (int row = 0; row < dh; row++) {
        memcpy(dst + (size_t)row * (size_t)dw,
               src + (size_t)(sy + row) * (size_t)src_w + (size_t)sx,
               (size_t)cw * 2u);
    }
}

void mg_blit_indices(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                     const uint8_t *idx, size_t icap, int iw, int ih,
                     const uint16_t *pal, size_t pcap)
{
    if (dw <= 0 || dh <= 0 || iw <= 0 || ih <= 0 || pcap == 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    for (int row = 0; row < ih; row++) {
        int ty = dy + row;
        if (ty < 0 || ty >= dh) continue;
        size_t srow = (size_t)row * (size_t)iw;
        int drow = ty * dw;
        for (int col = 0; col < iw; col++) {
            int tx = dx + col;
            if (tx < 0 || tx >= dw) continue;
            size_t si = srow + (size_t)col;
            if (si >= icap) continue;
            size_t p = (size_t)idx[si];
            if (p >= pcap) continue;            /* index past palette -> skip */
            dst[(size_t)drow + (size_t)tx] = pal[p];
        }
    }
}

/* An index's pixel, or MG_BIS_SKIP where the pixel is left as it was. */
#define MG_BIS_SKIP 0x10000u

/* One destination row of a scaled source row: columns [x0, x1), the first
 * block `first` pixels wide (the clip may cut it), every later one `scale`.
 * Returns whether it left any pixel as it was. */
static inline int mg_bis_row(uint16_t *d, const uint8_t *s, int x0, int x1,
                             int first, int scale, const uint32_t *t)
{
    uint32_t skipped = 0;
    if (scale == 1) {
        for (int x = x0; x < x1; x++) {
            uint32_t v = t[*s++];
            skipped |= v;
            if (!(v & MG_BIS_SKIP)) d[x] = (uint16_t)v;
        }
        return (skipped & MG_BIS_SKIP) != 0;
    }
    int x = x0, n = first;
    while (x < x1) {
        int e = x + n > x1 ? x1 : x + n;
        uint32_t v = t[*s++];
        skipped |= v;
        if (!(v & MG_BIS_SKIP)) {
            uint16_t c = (uint16_t)v;
            for (int i = x; i < e; i++) d[i] = c;
        }
        x = e;
        n = scale;
    }
    return (skipped & MG_BIS_SKIP) != 0;
}

void mg_blit_indices_scale(uint16_t *dst, size_t dcap, int dw, int dh,
                           const uint8_t *idx, size_t icap, int iw, int ih,
                           int sx, int sy, int sw, int sh, int dx, int dy, int scale,
                           const uint16_t *pal, size_t pcap, const uint8_t *palt,
                           int colorkey, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || dh <= 0 || iw <= 0 || ih <= 0 || sw <= 0 || sh <= 0 ||
        scale <= 0 || pcap == 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    if ((size_t)iw * (size_t)ih > icap) ih = (int)(icap / (size_t)iw);
    /* The source rect inside the bitmap, its destination moved with it. */
    int64_t ox = dx, oy = dy;
    if (sx < 0) { ox -= (int64_t)sx * scale; sw += sx; sx = 0; }
    if (sy < 0) { oy -= (int64_t)sy * scale; sh += sy; sy = 0; }
    if (sx >= iw || sy >= ih) return;
    if (sw > iw - sx) sw = iw - sx;
    if (sh > ih - sy) sh = ih - sy;
    if (sw <= 0 || sh <= 0) return;
    /* The destination rect inside the clip, inside the buffer. */
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    int64_t ex0 = ox > cx0 ? ox : cx0, ey0 = oy > cy0 ? oy : cy0;
    int64_t ex1 = ox + (int64_t)sw * scale, ey1 = oy + (int64_t)sh * scale;
    if (ex1 > cx1) ex1 = cx1;
    if (ey1 > cy1) ey1 = cy1;
    if (ex0 >= ex1 || ey0 >= ey1) return;
    /* Every byte's pixel, resolved once a call rather than once a pixel. */
    uint32_t t[256];
    for (int p = 0; p < 256; p++) {
        t[p] = (p == colorkey || (size_t)p >= pcap ||
                (palt != NULL && palt[p & 63])) ? MG_BIS_SKIP : pal[p];
    }
    int x0 = (int)ex0, x1 = (int)ex1, y0 = (int)ey0, y1 = (int)ey1;
    int u0 = (int)((ex0 - ox) / scale);
    int first = scale - (int)((ex0 - ox) % scale);
    size_t run = (size_t)(x1 - x0) * 2u;
    int v = (int)((ey0 - oy) / scale);
    int y = y0, yend = y0 + scale - (int)((ey0 - oy) % scale);
    while (y < y1) {
        if (yend > y1) yend = y1;
        const uint8_t *srow = idx + (size_t)(sy + v) * (size_t)iw + (size_t)(sx + u0);
        uint16_t *drow = dst + (size_t)y * (size_t)dw;
        int holes = mg_bis_row(drow, srow, x0, x1, first, scale, t);
        /* The block's other rows: a copy of the first, or the same writes
         * where it left pixels as they were. */
        for (int r = y + 1; r < yend; r++) {
            uint16_t *d = dst + (size_t)r * (size_t)dw;
            if (holes) {
                mg_bis_row(d, srow, x0, x1, first, scale, t);
            } else {
                memcpy(d + x0, drow + x0, run);
            }
        }
        y = yend;
        yend = y + scale;
        v++;
    }
}

/* ---- one byte a pixel ------------------------------------------------- */

static int mg_ibh(size_t cap, int bw, int bh)
{
    if (bw <= 0 || bh <= 0) return 0;
    if ((size_t)bw * (size_t)bh > cap) bh = (int)(cap / (size_t)bw);
    return bh;
}

/* Row y's [x0, x1] (inclusive) in `v`, clipped. */
static inline void mg_irun(uint8_t *buf, int bw, int bh, int x0, int x1, int y, uint8_t v)
{
    if (y < 0 || y >= bh) return;
    if (x0 < 0) x0 = 0;
    if (x1 >= bw) x1 = bw - 1;
    if (x1 < x0) return;
    memset(buf + (size_t)y * (size_t)bw + (size_t)x0, v, (size_t)(x1 - x0 + 1));
}

void mg_ifill(uint8_t *buf, size_t cap, int bw, int bh, int x, int y, int w, int h,
              uint8_t v)
{
    bh = mg_ibh(cap, bw, bh);
    if (bh <= 0 || w <= 0 || h <= 0) return;
    int y1 = y + h > bh ? bh : y + h;
    for (int r = y < 0 ? 0 : y; r < y1; r++) {
        mg_irun(buf, bw, bh, x, x + w - 1, r, v);
    }
}

static int mg_isqrt(int m)
{
    if (m <= 0) return 0;
    int s = 0;
    while ((s + 1) * (s + 1) <= m) s++;
    return s;
}

void mg_iline(uint8_t *buf, size_t cap, int bw, int bh, int x0, int y0, int x1, int y1,
              int d, int round, uint8_t v)
{
    bh = mg_ibh(cap, bw, bh);
    if (bh <= 0) return;
    if (d < 1) d = 1;
    if (d > MG_IDOT_MAX) d = MG_IDOT_MAX;
    /* each row's [lo, hi] from the dot's left, inclusive */
    int16_t lo[MG_IDOT_MAX], hi[MG_IDOT_MAX];
    int disc = round && d > 2;
    for (int r = 0; r < d; r++) {
        if (!disc) {
            lo[r] = 0;
            hi[r] = (int16_t)(d - 1);
            continue;
        }
        int R = 2 * r + 1 - d;
        int i1 = (d - 1 + mg_isqrt(d * d - R * R)) / 2;
        if (i1 > d - 1) i1 = d - 1;
        lo[r] = (int16_t)(d - 1 - i1);
        hi[r] = (int16_t)i1;
    }
    int a = d / 2;
    int dx = x1 > x0 ? x1 - x0 : x0 - x1;
    int dy = y1 > y0 ? y1 - y0 : y0 - y1;
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx - dy;
    for (;;) {
        int ox = x0 - a, oy = y0 - a;
        if (ox < bw && oy < bh && ox + d > 0 && oy + d > 0) {
            for (int r = 0; r < d; r++) {
                mg_irun(buf, bw, bh, ox + lo[r], ox + hi[r], oy + r, v);
            }
        }
        if (x0 == x1 && y0 == y1) return;
        int e2 = err + err;
        if (e2 > -dy) {
            err -= dy;
            x0 += sx;
        }
        if (e2 < dx) {
            err += dx;
            y0 += sy;
        }
    }
}

void mg_icopy(uint8_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
              const uint8_t *src, size_t scap, int sw, int sh, int sx, int sy,
              int w, int h, int from, int to)
{
    dh = mg_ibh(dcap, dw, dh);
    sh = mg_ibh(scap, sw, sh);
    if (dh <= 0 || sh <= 0) return;
    /* clip the rect to the source, then to the destination, moving both ends */
    if (sx < 0) { w += sx; dx -= sx; sx = 0; }
    if (sy < 0) { h += sy; dy -= sy; sy = 0; }
    if (dx < 0) { w += dx; sx -= dx; dx = 0; }
    if (dy < 0) { h += dy; sy -= dy; dy = 0; }
    if (sx + w > sw) w = sw - sx;
    if (sy + h > sh) h = sh - sy;
    if (dx + w > dw) w = dw - dx;
    if (dy + h > dh) h = dh - dy;
    if (w <= 0 || h <= 0) return;
    for (int r = 0; r < h; r++) {
        uint8_t *o = dst + (size_t)(dy + r) * (size_t)dw + (size_t)dx;
        const uint8_t *i = src + (size_t)(sy + r) * (size_t)sw + (size_t)sx;
        if (from < 0) {
            memmove(o, i, (size_t)w);
            continue;
        }
        for (int c = 0; c < w; c++) {
            uint8_t p = i[c];
            o[c] = p == (uint8_t)from ? (uint8_t)to : p;
        }
    }
}

typedef struct {
    uint8_t *buf;
    int bw, bh;
    uint8_t v;
    uint8_t *marks;
    size_t mcap;
    int t, tx;
    int out[4];
    int n;
} mg_ifl_t;

/* Row y's [l, r] joined the region. */
static void mg_ifl_run(mg_ifl_t *f, int l, int r, int y)
{
    memset(f->buf + (size_t)y * (size_t)f->bw + (size_t)l, f->v, (size_t)(r - l + 1));
    f->n += r - l + 1;
    if (l < f->out[0]) f->out[0] = l;
    if (y < f->out[1]) f->out[1] = y;
    if (r + 1 > f->out[2]) f->out[2] = r + 1;
    if (y + 1 > f->out[3]) f->out[3] = y + 1;
    if (f->t > 0) {
        size_t row = (size_t)(y / f->t) * (size_t)f->tx;
        for (int k = l / f->t; k <= r / f->t; k++) {
            if (row + (size_t)k < f->mcap) f->marks[row + (size_t)k] = 1;
        }
    }
}

int mg_iflood(uint8_t *buf, size_t cap, int bw, int bh, int x, int y, uint8_t v,
              uint8_t *marks, size_t mcap, int t, int tx, int16_t *stack, int n,
              int out[4])
{
    out[0] = out[1] = out[2] = out[3] = 0;
    bh = mg_ibh(cap, bw, bh);
    if (bh <= 0 || x < 0 || y < 0 || x >= bw || y >= bh) return 0;
    uint8_t old = buf[(size_t)y * (size_t)bw + (size_t)x];
    if (old == v) return 0;
    mg_ifl_t f = {buf, bw, bh, v, marks, mcap, t, tx, {bw, bh, 0, 0}, 0};
    int sp = 0, over = 0;
    if (n > 0) {
        stack[0] = (int16_t)x;
        stack[1] = (int16_t)y;
        sp = 1;
    } else {
        over = 1;
        mg_ifl_run(&f, x, x, y);
    }
    while (sp > 0) {
        sp--;
        int px = stack[2 * sp], py = stack[2 * sp + 1];
        uint8_t *row = buf + (size_t)py * (size_t)bw;
        if (row[px] != old) continue;
        int l = px, r = px;
        while (l > 0 && row[l - 1] == old) l--;
        while (r + 1 < bw && row[r + 1] == old) r++;
        mg_ifl_run(&f, l, r, py);
        for (int ny = py - 1; ny <= py + 1; ny += 2) {
            if (ny < 0 || ny >= bh) continue;
            const uint8_t *nrow = buf + (size_t)ny * (size_t)bw;
            int q = l;
            while (q <= r) {
                if (nrow[q] == old) {
                    if (sp < n) {
                        stack[2 * sp] = (int16_t)q;
                        stack[2 * sp + 1] = (int16_t)ny;
                        sp++;
                    } else {
                        over = 1;
                    }
                    while (q <= r && nrow[q] == old) q++;
                }
                q++;
            }
        }
    }
    /* The stack ran out: what is left of the region touches what was filled,
     * so sweep, down then up, until a pass adds nothing. */
    while (over) {
        over = 0;
        for (int pass = 0; pass < 2; pass++) {
            for (int k = 0; k < bh; k++) {
                int yy = pass ? bh - 1 - k : k;
                uint8_t *row = buf + (size_t)yy * (size_t)bw;
                const uint8_t *up = yy > 0 ? row - bw : NULL;
                const uint8_t *dn = yy + 1 < bh ? row + bw : NULL;
                for (int xx = 0; xx < bw; xx++) {
                    if (row[xx] != old) continue;
                    if (!((xx > 0 && row[xx - 1] == v) || (xx + 1 < bw && row[xx + 1] == v)
                          || (up && up[xx] == v) || (dn && dn[xx] == v))) {
                        continue;
                    }
                    int l = xx, r = xx;
                    while (l > 0 && row[l - 1] == old) l--;
                    while (r + 1 < bw && row[r + 1] == old) r++;
                    mg_ifl_run(&f, l, r, yy);
                    over = 1;
                    xx = r;
                }
            }
        }
    }
    memcpy(out, f.out, sizeof(f.out));
    return f.n;
}

/* ---- text ------------------------------------------------------------- */

void mg_text_raw(uint16_t *dst, size_t dcap, int dw,
                 const uint8_t *s, size_t slen, int x, int y, uint16_t col,
                 const uint8_t *font, int nglyphs, int first, int scale,
                 int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || nglyphs <= 0) return;
    if (scale < 1) scale = 1;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    x -= cam_x;
    y -= cam_y;
    int adv = 8 * scale;                         /* cell advance per character */
    if (y >= cy1 || y + adv <= cy0) return;      /* whole line off-clip */
    for (size_t i = 0; i < slen; i++, x += adv) {
        if (x >= cx1) break;                     /* rest is right of the clip */
        if (x + adv <= cx0) continue;            /* this glyph entirely left */
        int gi = (int)s[i] - first;
        if (gi < 0 || gi >= nglyphs) gi = 0;     /* out of range -> first glyph */
        const uint8_t *g = font + (size_t)gi * 8u;
        for (int j = 0; j < 8; j++) {
            uint8_t bits = g[j];
            if (bits == 0) continue;
            int bx = x + j * scale;
            if (bx >= cx1 || bx + scale <= cx0) continue;
            for (int row = 0; bits != 0; bits >>= 1, row++) {
                if (!(bits & 1)) continue;
                int by = y + row * scale;
                for (int sub_y = 0; sub_y < scale; sub_y++) {
                    int ty = by + sub_y;
                    if (ty < cy0 || ty >= cy1) continue;
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (int sub_x = 0; sub_x < scale; sub_x++) {
                        int tx = bx + sub_x;
                        if (tx < cx0 || tx >= cx1) continue;
                        drow[tx] = col;
                    }
                }
            }
        }
    }
}

/* A loaded font's row (mg_font_t): its column bytes and its advance table.
 * Petme128 stays on mg_text_raw's cell loop: this loop drew petme128's chrome
 * text 1.3x slower on the T-Deck, and still 1.06x with mg_text_raw's own loop
 * behind a row dispatch (S3 A/Bs), so petme128's lane is the
 * function it was and a loaded font is a lane of its own. */
void mg_text_font(uint16_t *dst, size_t dcap, int dw,
                  const uint8_t *s, size_t slen, int x, int y, uint16_t col,
                  const mg_font_t *f, int scale,
                  int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || f->count == 0) return;
    if (scale < 1) scale = 1;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    x -= cam_x;
    y -= cam_y;
    if (y >= cy1 || y + f->h * scale <= cy0) return;   /* whole line off-clip */
    const int w = f->w, bpc = f->bpc, first = f->first, count = f->count;
    const int gw = w * scale;                    /* a glyph's box */
    const uint8_t *adv = f->adv;
    for (size_t i = 0; i < slen; i++) {
        if (x >= cx1) break;                     /* rest is right of the clip */
        int gi = (int)s[i] - first;
        if (gi < 0 || gi >= count) gi = 0;       /* out of range -> first glyph */
        int step = adv[gi] * scale;
        if (x + gw <= cx0) {                     /* this glyph entirely left */
            x += step;
            continue;
        }
        const uint8_t *g = f->glyphs + (size_t)gi * (size_t)(w * bpc);
        for (int j = 0; j < w; j++) {
            int bx = x + j * scale;
            if (bx >= cx1 || bx + scale <= cx0) continue;
            for (int b = 0; b < bpc; b++) {
                uint8_t bits = g[j * bpc + b];
                for (int row = 8 * b; bits != 0; bits >>= 1, row++) {
                    if (!(bits & 1)) continue;
                    int by = y + row * scale;
                    for (int sub_y = 0; sub_y < scale; sub_y++) {
                        int ty = by + sub_y;
                        if (ty < cy0 || ty >= cy1) continue;
                        uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                        for (int sub_x = 0; sub_x < scale; sub_x++) {
                            int tx = bx + sub_x;
                            if (tx < cx0 || tx >= cx1) continue;
                            drow[tx] = col;
                        }
                    }
                }
            }
        }
        x += step;
    }
}

void mg_ink(mg_ink_t *k, uint16_t col, int bpp, int alpha, int32_t bg, int swap)
{
    const int top = (1 << bpp) - 1;
    if (alpha < 0) alpha = 0;
    if (alpha > 256) alpha = 256;
    k->col = col;
    k->swap = swap ? 1 : 0;
    k->colc = swap ? mg_bswap16(col) : col;
    uint16_t bgc = bg < 0 ? 0 : (swap ? mg_bswap16((uint16_t)bg) : (uint16_t)bg);
    for (int v = 0; v < 16; v++) {
        int wt = v > top ? alpha : (v * alpha + (top >> 1)) / top;
        k->wt[v] = (uint16_t)wt;
        uint16_t p = bg < 0 ? k->colc : mg_blend565(k->colc, bgc, wt);
        k->ramp[v] = swap ? mg_bswap16(p) : p;
    }
}

void mg_glyph_cov(uint16_t *dst, size_t dcap, int dw, const uint8_t *cov, int w, int h,
                  int bpp, int x, int y, const mg_ink_t *k, int known, int scale,
                  int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || w <= 0 || h <= 0) return;
    if (scale < 1) scale = 1;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    if (x >= cx1 || y >= cy1 || x + w * scale <= cx0 || y + h * scale <= cy0) return;
    const int top = (1 << bpp) - 1;
    const uint16_t col = k->col, colc = k->colc;
    if (scale == 1) {
        /* a pixel a pixel: the clip as row and column ranges, one store each */
        int r0 = cy0 - y > 0 ? cy0 - y : 0, r1 = cy1 - y < h ? cy1 - y : h;
        int c0 = cx0 - x > 0 ? cx0 - x : 0, c1 = cx1 - x < w ? cx1 - x : w;
        for (int r = r0; r < r1; r++) {
            uint16_t *drow = dst + (size_t)(y + r) * (size_t)dw;
            size_t bit = ((size_t)r * (size_t)w + (size_t)c0) * (size_t)bpp;
            for (int c = c0; c < c1; c++, bit += (size_t)bpp) {
                int v = (cov[bit >> 3] >> (8 - bpp - (int)(bit & 7))) & top;
                int wt = k->wt[v];
                if (wt == 0) continue;
                int X = x + c;
                if (known) {
                    drow[X] = k->ramp[v];
                } else if (wt == 256) {
                    drow[X] = col;
                } else if (k->swap) {
                    drow[X] = mg_bswap16(mg_blend565(colc, mg_bswap16(drow[X]), wt));
                } else {
                    drow[X] = mg_blend565(colc, drow[X], wt);
                }
            }
        }
        return;
    }
    for (int r = 0; r < h; r++) {
        int by = y + r * scale;
        if (by >= cy1) break;
        if (by + scale <= cy0) continue;
        int y0 = by < cy0 ? cy0 : by, y1 = by + scale > cy1 ? cy1 : by + scale;
        size_t bit = (size_t)r * (size_t)w * (size_t)bpp;
        for (int c = 0; c < w; c++, bit += (size_t)bpp) {
            int v = (cov[bit >> 3] >> (8 - bpp - (int)(bit & 7))) & top;
            int wt = k->wt[v];
            if (wt == 0) continue;
            int bx = x + c * scale;
            if (bx >= cx1) break;
            if (bx + scale <= cx0) continue;
            int x0 = bx < cx0 ? cx0 : bx, x1 = bx + scale > cx1 ? cx1 : bx + scale;
            if (known || wt == 256) {
                uint16_t p = known ? k->ramp[v] : col;
                for (int ty = y0; ty < y1; ty++) {
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (int tx = x0; tx < x1; tx++) drow[tx] = p;
                }
            } else if (k->swap) {
                for (int ty = y0; ty < y1; ty++) {
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (int tx = x0; tx < x1; tx++) {
                        drow[tx] = mg_bswap16(mg_blend565(colc, mg_bswap16(drow[tx]), wt));
                    }
                }
            } else {
                for (int ty = y0; ty < y1; ty++) {
                    uint16_t *drow = dst + (size_t)ty * (size_t)dw;
                    for (int tx = x0; tx < x1; tx++) drow[tx] = mg_blend565(colc, drow[tx], wt);
                }
            }
        }
    }
}

/* The scale-1 lane crossed into libmoy, was REVERTED on an S3 number of 1.21x,
 * and crossed again when that number was re-measured at 1.04x against current
 * libmoy -- the 1.21x had been taken before moy_print grew its whole-column
 * off-clip early-out. See libmoy/UPSTREAM.md; the reversal is recorded there
 * rather than tidied away.
 *
 * libmoy uses its own compiled-in font rather than the blob passed here. Both
 * are petme128 and SPEC.md 6 requires them byte-identical or text conformance
 * fails -- so the `text` and `text_bytes` scenes are what license this, not the
 * assumption. */
void mg_text(uint16_t *dst, size_t dcap, int dw,
             const uint8_t *s, size_t slen, int x, int y, int col,
             const uint8_t *font, int nglyphs, int first, int scale,
             int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    uint32_t pf = moy_text_print_font();
    if (scale == 1 && pf == MOY_FONT_PETME128) {
        moy_canvas c;
        if (dw <= 0) return;
        mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
        mg_canvas_solid(&c, dst, dw, dcap, (uint16_t)(col & 0xFFFF),
                        cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_print(&c, s, slen, x, y, 0);
        return;
    }
    // Above scale 1, or in the shell's print font (moy_text.h), this is system
    // text: the one UI text path, whose petme128 is libmoy's same font.
    (void)font;
    (void)nglyphs;
    (void)first;
    moy_text_target_t t = {dst, dcap, dw, cam_x, cam_y, cx0, cy0, cx1, cy1};
    moy_text(&t, pf, MOY_TEXT_PY, s, slen, x, y, (uint16_t)(col & 0xFFFF),
             scale, MOY_TEXT_LEFT, -1);
}

/* ---- the layer restore (moy_gfx_kernels.h has the contract) ------------ */

static bool none_start(uint16_t *dst, const uint16_t *src, size_t npix)
{
    (void)dst; (void)src; (void)npix;
    return false;
}

static bool now_start(uint16_t *dst, const uint16_t *src, size_t npix)
{
    memcpy(dst, src, npix * 2u);
    return true;
}

static bool done_wait(void)
{
    return true;
}

const mg_copy_engine_t mg_copy_none = { none_start, done_wait, NULL };
const mg_copy_engine_t mg_copy_now = { now_start, done_wait, NULL };

void mg_lr_init(mg_lrestore_t *s, bool async_ok)
{
    memset(s, 0, sizeof(*s));
    s->async_ok = async_ok ? 1 : 0;
}

void mg_lr_drain(mg_lrestore_t *s, const mg_copy_engine_t *e)
{
    if (s->fl_src == NULL) return;
    s->fl_src = NULL;
    if (!e->wait()) s->trips++;
}

void mg_lr_forget(mg_lrestore_t *s, const mg_copy_engine_t *e)
{
    mg_lr_drain(s, e);
    s->src = NULL;
}

bool mg_lr_kick(mg_lrestore_t *s, const mg_copy_engine_t *e, uint16_t *dst, size_t dcap)
{
    mg_lr_drain(s, e);           /* last frame's copy never consumed */
    const uint16_t *src = s->src;
    if (src == NULL) return false;
    s->src = NULL;
    if ((size_t)s->npix > dcap) return false;
    /* The prediction is a whole-screen window of a screen-wide layer, so its
     * rows are contiguous: npix pixels from row cam_y. */
    if (e->start(dst, src + (size_t)s->cam_y * (size_t)s->sw, (size_t)s->npix)) {
        s->fl_src = src;
        s->fl_cam_y = s->cam_y;
        return true;
    }
    s->async_ok = 0;             /* the driver refused: synchronous from now on */
    return false;
}

static void lr_arm(mg_lrestore_t *s, const uint16_t *src, size_t scap, int sw,
                   int dw, int dh, int cam_x, int cam_y)
{
    if (!s->async_ok || cam_x != 0 || sw != dw) return;
    if ((size_t)(cam_y + dh) * (size_t)dw > scap) return;
    s->src = src;
    s->sw = sw;
    s->cam_y = cam_y;
    s->npix = dw * dh;
}

void mg_lr_blit(mg_lrestore_t *s, const mg_copy_engine_t *e,
                uint16_t *dst, size_t dcap, int dw, int dh,
                const uint16_t *src, size_t scap, int sw, int sh,
                int cam_x, int cam_y, bool edited)
{
    uint32_t t0 = e->now_us ? e->now_us() : 0;
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
    if ((size_t)sw * (size_t)sh < scap) scap = (size_t)sw * (size_t)sh;
    if ((size_t)sh > scap / (size_t)sw) sh = (int)(scap / (size_t)sw);
    int mx = sw - dw, my = sh - dh;
    if (cam_x > mx) cam_x = mx;
    if (cam_x < 0) cam_x = 0;
    if (cam_y > my) cam_y = my;
    if (cam_y < 0) cam_y = 0;
    if (s->fl_src != NULL) {
        const uint16_t *fl = s->fl_src;
        int fl_y = s->fl_cam_y;
        bool hit = false;
        s->fl_src = NULL;
        if (!e->wait()) {
            s->trips++;          /* the copy may still land: the sync copy writes the same bytes */
        } else {
            hit = !edited && fl == src && fl_y == cam_y && cam_x == 0 && sw == dw;
        }
        if (hit) {
            if (e->now_us) s->us += e->now_us() - t0;
            lr_arm(s, src, scap, sw, dw, dh, cam_x, cam_y);
            return;
        }
    }
    mg_blit_window(dst, dcap, dw, dh, src, scap, sw, cam_x, cam_y);
    if (e->now_us) s->us += e->now_us() - t0;
    lr_arm(s, src, scap, sw, dw, dh, cam_x, cam_y);
}
