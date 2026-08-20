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

void mg_blit_window(uint16_t *dst, size_t dcap, int dw, int dh,
                    const uint16_t *src, size_t scap, int src_w, int sx, int sy)
{
    if (dw <= 0 || dh <= 0 || src_w <= 0) return;
    if (sx < 0) sx = 0;
    if (sy < 0) sy = 0;
    if (sx + dw > src_w) dw = src_w - sx;         /* clamp window to source */
    if (dw <= 0) return;
    if ((size_t)dw * (size_t)dh > dcap) dh = (int)(dcap / (size_t)dw);
    int src_rows = (int)(scap / (size_t)src_w);
    if (sy + dh > src_rows) dh = src_rows - sy;
    if (dh <= 0) return;
    for (int row = 0; row < dh; row++) {
        memcpy(dst + (size_t)row * (size_t)dw,
               src + (size_t)(sy + row) * (size_t)src_w + (size_t)sx,
               (size_t)dw * 2u);
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
    if (scale == 1) {
        moy_canvas c;
        if (dw <= 0) return;
        mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
        mg_canvas_solid(&c, dst, dw, dcap, (uint16_t)(col & 0xFFFF),
                        cam_x, cam_y, cx0, cy0, cx1, cy1);
        moy_print(&c, s, slen, x, y, 0);
        return;
    }
    mg_text_raw(dst, dcap, dw, s, slen, x, y, (uint16_t)(col & 0xFFFF),
                font, nglyphs, first, scale, cam_x, cam_y,
                cx0, cy0, cx1, cy1);
}
