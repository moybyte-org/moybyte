/* The host's RGB565 COMPOSITOR shim -- moy_gfx's surface, for CPython.
 *
 * WHAT THIS IS. `device_canvas.py` is the raster on both boards and in the
 * browser; it reaches its kernel through `import moy_gfx`. This file is the
 * other end of that name on the host, so the boards' canvas class runs here
 * unchanged and the two canvases are one.
 *
 * WHAT IS *NOT* HERE ANY MORE, AND THAT IS THE POINT. The compositor loops --
 * fill, fill_rect, blit565, blit565_scale, blit_window, scroll_rect,
 * blit_indices, text -- used to be ported into this file line for line from
 * modmoy_gfx.c. Two copies of 189 lines that had to agree pixel for pixel, in
 * exactly the layer this exercise exists to make single. They live in
 * `native/moy_gfx/moy_gfx_kernels.c` now, which BOTH this shim and the
 * MicroPython usermod compile; gfx_binding.py calls those `mg_*` symbols
 * directly, so there is not even a forwarder here to drift. Read that file's
 * header before adding a verb: a new compositor loop goes there, not here.
 *
 * WHAT REMAINS. Two things that are genuinely host-side.
 *
 * (1) The libmoy BRIDGE verbs. Sprites are libmoy's, and libmoy is compiled
 * -DMOY_PIXEL_RGB565=1 here -- which changes sizeof(moy_pixel) and therefore
 * the layout of every libmoy struct, so this cannot share a library with an
 * indexed build. (There used to be one: `moyhost_raster.c`, libmoy built
 * INDEXED for `runtime/canvas.py`. Both were deleted 2026-08-15 when the host
 * moved onto DeviceCanvas; native_build.py's header records it in the past
 * tense.) Every `hg_*` below marshals the caller's scalars into a borrowed
 * moy_canvas -- via the SHARED mg_canvas/mg_clip helpers -- and calls the
 * spec's own raster.
 *
 * (2) The ASYNC pair, which is deliberately a refusal. On the boards
 * copy_async is ESP-IDF GDMA; there is no host equivalent, and pretending
 * otherwise would mean a second code path to keep honest. Returning 0 puts the
 * caller on its synchronous fallback -- the same branch a board takes when its
 * DMA driver declines the copy -- so the host exercises a path the device also
 * has.
 *
 * BUFFERS. ctypes hands over a bare pointer, so the capacity the native module
 * derives from a MicroPython buffer object arrives here as an explicit
 * argument instead. Every bound and every clamp is kept, because those bounds
 * are what stop a bad call scribbling over the framebuffer.
 */

#include <stdint.h>
#include <string.h>

#include "moy.h"                 /* built RGB565 here -- see the note above */
#include "moy_gfx_kernels.h"     /* the shared compositor + the libmoy bridge */

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
 * The verbs below are the ones that are NOT pure compositing: they draw
 * sprites and shapes, and those are libmoy's. Each borrows a moy_canvas for
 * the duration of one call rather than inverting either side's ownership --
 * through mg_canvas/mg_canvas_solid/mg_clip, which modmoy_gfx.c borrows with
 * too, so the borrowing itself cannot differ between the two tiers. The LUT
 * arrives already folded through `pal` by the Python side (_wire_pal), which is
 * exactly what libmoy's store[] is, so pal[] stays identity and no palette is
 * rebuilt per call.
 */

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
    if (!mg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sh;
    /* ONE canvas for the whole batch -- which is the point of this verb. The
     * camera goes in the CANVAS rather than being subtracted per item: moy_spr
     * applies it, and its fast path hoists it out of the pixel loop. */
    mg_canvas(&c, dst, dw, dcap, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sh, (uint8_t *)sheet);
    for (int i = 0; i < n_items; i++) {
        const int16_t *it = quads + (size_t)i * 4u;   /* (tile, x, y, flip) */
        /* moy_spr refuses an out-of-range tile itself (SPEC.md: a blank tile is
         * legal, a bad id is not an error), so no guard is needed here. */
        moy_spr(&c, &sh, it[0], it[1], it[2], key, scale, it[3]);
    }
}

/* `dh` IS DELIBERATELY NOT TESTED, and that is a fix, not an oversight. This
 * file used to refuse `dh <= 0` on line/circ/circb/tri/sspr/tline; the board
 * never did -- modmoy_gfx.c marks the argument `(void)dh` and takes its row
 * count from the buffer capacity, which is the only number that can be trusted
 * anyway. So a zero-height canvas drew NOTHING here and fourteen pixels on
 * glass, and no test could see it because the goldens only ever replay a real
 * canvas. Found while extracting the compositor, 2026-08-15, and pinned by the
 * `dh = 0` ops in tests/test_gfx_binding.py's clamp script. The host moved to
 * the board's behaviour rather than the reverse: the board is what ships, and
 * changing it for an unreachable case would be risk with no return. */
#define HG_SOLID_PROLOGUE()                                                   \
    moy_canvas c;                                                             \
    if (dw <= 0) return;                                                      \
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);                                \
    mg_canvas_solid(&c, dst, dw, dcap, (uint16_t)col, cam_x, cam_y,           \
                    cx0, cy0, cx1, cy1)

void hg_line(uint16_t *dst, size_t dcap, int dw, int dh,
             int x0, int y0, int x1, int y1, int col,
             int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    HG_SOLID_PROLOGUE();
    moy_line(&c, x0, y0, x1, y1, 0);
}

void hg_circ(uint16_t *dst, size_t dcap, int dw, int dh,
             int ccx, int ccy, int r, int col,
             int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    HG_SOLID_PROLOGUE();
    moy_circ(&c, ccx, ccy, r, 0);
}

void hg_circb(uint16_t *dst, size_t dcap, int dw, int dh,
              int ccx, int ccy, int r, int col,
              int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    HG_SOLID_PROLOGUE();
    moy_circb(&c, ccx, ccy, r, 0);
}

void hg_tri(uint16_t *dst, size_t dcap, int dw, int dh,
            int x1, int y1, int x2, int y2, int x3, int y3, int col,
            int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    HG_SOLID_PROLOGUE();
    moy_tri(&c, x1, y1, x2, y2, x3, y3, 0);
}

void hg_sspr(uint16_t *dst, size_t dcap, int dw, int dh,
             int sx, int sy, int sw, int srch, int ddx, int ddy,
             int ddw, int ddh,
             const uint8_t *sheet, size_t sheet_len, int sheetw, int sheeth,
             const uint16_t *lut, const uint8_t *palt, int ck, int flip,
             int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0) return;                    /* `dh` unused -- see the note above */
    if (!mg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sheet_o;
    mg_canvas(&c, dst, dw, dcap, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sheet_o, (uint8_t *)sheet);
    moy_sspr(&c, &sheet_o, sx, sy, sw, srch, ddx, ddy, ddw, ddh, ck, flip);
}

void hg_tline(uint16_t *dst, size_t dcap, int dw, int dh,
              int x0, int y0, int x1, int y1, int u, int v, int du, int dv,
              const uint8_t *cells, size_t cells_len, int mw, int mh,
              const uint8_t *sheet, size_t sheet_len, int sheetw, int sheeth,
              const uint16_t *lut, const uint8_t *palt, int ck,
              int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1)
{
    if (dw <= 0 || mw <= 0 || mh <= 0) return;   /* `dh` unused, as above */
    if ((size_t)(mw * mh) > cells_len) return;
    if (!mg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    if (mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) return;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sheet_o;
    moy_map m;
    mg_canvas(&c, dst, dw, dcap, lut, palt, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sheet_o, (uint8_t *)sheet);
    moy_map_init(&m, (uint8_t *)cells, mw, mh);
    /* NOTE the argument order: libmoy takes (canvas, SHEET, MAP, ...) here,
     * where map_draw takes (canvas, MAP, SHEET, ...). Getting it backwards
     * compiles cleanly -- both are pointers -- and draws garbage. */
    moy_tline(&c, &sheet_o, &m, x0, y0, x1, y1, u, v, du, dv, ck);
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
    if (!mg_is_moy_sheet(sheetw, sheeth, sheet_len)) return;
    if (mw > MOY_MAP_MAX || mh > MOY_MAP_MAX) return;
    mg_clip(dw, dcap, &cx0, &cy0, &cx1, &cy1);
    moy_canvas c;
    moy_sheet sh;
    moy_map m;
    /* Camera is ZERO here on purpose: the caller has already resolved the
     * region's screen position into dsx/dsy (the destination may be a hidden
     * cache layer rather than the framebuffer, where a camera would mean
     * nothing), so applying one again would double the offset. */
    mg_canvas(&c, dst, dw, dcap, lut, palt, 0, 0, cx0, cy0, cx1, cy1);
    moy_sheet_init(&sh, (uint8_t *)sheet);
    moy_map_init(&m, (uint8_t *)cells, mw, mh);
    moy_map_draw(&c, &m, &sh, mx, my, rw, rh, dsx, dsy, ck, scale);
}
