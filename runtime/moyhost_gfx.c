/* The host's RGB565 COMPOSITOR shim -- moy_gfx's surface, for CPython.
 *
 * `moyhost_raster.c` next door is libmoy built INDEXED, and it serves
 * `runtime/canvas.py`. This is the other half of the same story: the verbs
 * `device_canvas.py` calls, so that the boards' canvas class can run on the
 * host unchanged and the two canvases can become one.
 *
 * WHY A SECOND SHIM AND NOT NINE MORE FUNCTIONS IN THE FIRST. `moy_gfx` is
 * compiled -DMOY_PIXEL_RGB565=1, which changes sizeof(moy_pixel) and therefore
 * the layout of every libmoy struct. A pixel here is a uint16_t of RGB565; a
 * pixel there is a byte of palette index. The two cannot share a library, and
 * the difference is a build option rather than a fork -- the same sources, the
 * same define the boards set.
 *
 * WHAT THESE ARE. Not libmoy: the functions below are moy_gfx's OWN compositor,
 * which the spec's raster has no counterpart for -- viewport-aware fill and
 * fill_rect, blit565, blit_window, scroll_rect. They are ported from
 * modmoy_gfx.c line for line, with the MicroPython argument marshalling
 * removed and the buffer capacity passed in explicitly (ctypes hands over a
 * pointer, so the guard `moy_gfx_buf_w` used to derive has to arrive as an
 * argument). Every bound and every clamp is kept, because those bounds are
 * what stop a bad call scribbling over the framebuffer, and because a verb
 * that clipped differently here than on glass would be a silent divergence in
 * exactly the layer this whole exercise is trying to make single.
 *
 * The ASYNC pair is deliberately a refusal. On the boards copy_async is
 * ESP-IDF GDMA; there is no host equivalent, and pretending otherwise would
 * mean a second code path to keep honest. Returning 0 puts the caller on its
 * synchronous fallback -- the same branch a board takes when its DMA driver
 * declines the copy -- so the host exercises a path the device also has.
 */

#include <stdint.h>
#include <string.h>

#include "moy.h"        /* built RGB565 here -- see the note above */

/* ---- fill ------------------------------------------------------------- */

/* modmoy_gfx.c's moy_gfx_fill_run, verbatim: align to 4 bytes so the 32-bit
 * stores never straddle a boundary, then write pairs. */
static inline void hg_fill_run(uint16_t *px, size_t n, uint16_t c)
{
    if (n == 0) return;
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

void hg_fill(uint16_t *px, size_t cap, int npix, int color)
{
    long n = npix;
    if (n < 0) n = 0;
    if ((size_t)n > cap) n = (long)cap;
    hg_fill_run(px, (size_t)n, (uint16_t)(color & 0xFFFF));
}

void hg_fill_rect(uint16_t *px, size_t cap, int stride,
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
        hg_fill_run(px + (size_t)(y + row) * (size_t)stride + (size_t)x,
                    (size_t)w, c);
    }
}

/* ---- scroll ----------------------------------------------------------- */

void hg_scroll_rect(uint16_t *px, size_t cap, int stride,
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

void hg_blit565(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
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
            /* OPAQUE fast lane: no colorkey test means the row's clipped span
             * is one contiguous copy -- memcpy instead of the per-pixel loop.
             * (On glass this is what makes blit_strip and the paint-image bakes
             * cheap; keeping it here keeps the two implementations one.) */
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
            if (p == (uint16_t)key) continue;
            drow[tx] = p;
        }
    }
}

void hg_blit_window(uint16_t *dst, size_t dcap, int dw, int dh,
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

/* ---- the async pair, which refuses ------------------------------------ */

int hg_copy_async(uint16_t *dst, size_t dcap, int dst_off,
                  const uint16_t *src, size_t scap, int src_off, int npix)
{
    (void)dst; (void)dcap; (void)dst_off;
    (void)src; (void)scap; (void)src_off; (void)npix;
    return 0;       /* no GDMA here: caller takes its synchronous path */
}

int hg_copy_wait(void)
{
    return 1;       /* nothing was ever in flight */
}

/* ---- the libmoy bridge ------------------------------------------------ */
/*
 * The two verbs below are the ones that are NOT pure compositing: they draw
 * sprites, and sprites are libmoy's. modmoy_gfx.c borrows a moy_canvas for the
 * duration of one call rather than inverting either side's ownership, and this
 * does the same thing with the same fields -- because "the same thing" is the
 * entire requirement. The LUT arrives already folded through `pal` by the
 * Python side (_wire_pal), which is exactly what libmoy's store[] is, so pal[]
 * stays identity and no palette is rebuilt per call.
 */

static void hg_clip_rect(int dw, size_t cap, int *cx0, int *cy0,
                         int *cx1, int *cy1)
{
    int max_rows = (int)(cap / (size_t)dw);
    if (*cx0 < 0) *cx0 = 0;
    if (*cy0 < 0) *cy0 = 0;
    if (*cx1 > dw) *cx1 = dw;
    if (*cy1 > max_rows) *cy1 = max_rows;
}

/* `h` is DERIVED from the buffer capacity rather than trusted, exactly as the
 * device does it: a canvas whose h exceeds its buffer would let libmoy's own
 * clamping write past the end. */
static void hg_canvas(moy_canvas *c, uint16_t *dst, int dw, size_t cap,
                      const uint16_t *lut, const uint8_t *palt,
                      int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    c->pix = dst;
    c->w = dw;
    c->h = (int)(cap / (size_t)dw);
    c->cam_x = cam_x;
    c->cam_y = cam_y;
    c->clip_x0 = cx0;
    c->clip_y0 = cy0;
    c->clip_x1 = cx1;
    c->clip_y1 = cy1;
    for (int i = 0; i < MOY_PALETTE; i++) {
        c->pal[i] = (uint8_t)i;
        c->store[i] = lut[i];
        c->wire[i] = lut[i];
    }
    if (palt != NULL) memcpy(c->palt, palt, MOY_PALETTE);
    else              memset(c->palt, 0, MOY_PALETTE);
}

/* libmoy addresses a sheet with SPEC.md 3.2's FIXED geometry, so anything else
 * would read at the wrong stride or past the end. Silent, like the device's:
 * a draw verb that throws mid-frame takes the cart down. */
static int hg_is_moy_sheet(int w, int h, size_t len)
{
    return w == MOY_SHEET_W && h == MOY_SHEET_H &&
           len >= (size_t)(MOY_SHEET_W * MOY_SHEET_H);
}

void hg_blit_batch(uint16_t *dst, size_t dcap, int dw, int dh,
                   const int16_t *quads, int n_items,
                   const uint8_t *sheet, size_t sheet_len,
                   int sheetw, int sheeth,
                   const uint16_t *lut, const uint8_t *palt,
                   int key, int scale, int cam_x, int cam_y,
                   int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || dh <= 0 || n_items <= 0) return;
    if (scale < 1) scale = 1;
    if (!hg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    hg_clip_rect(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sh;
    /* ONE canvas for the whole batch -- which is the point of this verb. The
     * camera goes in the CANVAS rather than being subtracted per item: moy_spr
     * applies it, and its fast path hoists it out of the pixel loop. */
    hg_canvas(&c, dst, dw, dcap, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sh, (uint8_t *)sheet);
    for (int i = 0; i < n_items; i++) {
        const int16_t *it = quads + (size_t)i * 4u;   /* (tile, x, y, flip) */
        /* moy_spr refuses an out-of-range tile itself (SPEC.md: a blank tile is
         * legal, a bad id is not an error), so no guard is needed here. */
        moy_spr(&c, &sh, it[0], it[1], it[2], key, scale, it[3]);
    }
}

void hg_blit_map(uint16_t *dst, size_t dcap, int dw, int dsx, int dsy,
                 const uint8_t *cells, size_t cells_len, int mw, int mh,
                 int mx, int my, int rw, int rh,
                 const uint8_t *sheet, size_t sheet_len, int sheetw, int sheeth,
                 const uint16_t *lut, const uint8_t *palt,
                 int ck, int scale, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || mw <= 0 || mh <= 0 || rw <= 0 || rh <= 0) return;
    if ((size_t)(mw * mh) > cells_len) return;
    if (!hg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    if (mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) return;
    hg_clip_rect(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sh;
    moy_map m;
    /* Camera is ZERO here on purpose: the caller has already resolved the
     * region's screen position into dsx/dsy (the destination may be a hidden
     * cache layer rather than the framebuffer, where a camera would mean
     * nothing), so applying one again would double the offset. */
    hg_canvas(&c, dst, dw, dcap, lut, palt, 0, 0, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sh, (uint8_t *)sheet);
    moy_map_init(&m, (uint8_t *)cells, mw, mh);
    moy_map_draw(&c, &m, &sh, mx, my, rw, rh, dsx, dsy, ck, scale);
}
