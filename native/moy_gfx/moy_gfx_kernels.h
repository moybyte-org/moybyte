/* moy_gfx's COMPOSITOR, once -- the pixel loops both the MicroPython usermod
 * and the host's ctypes shim run.
 *
 * WHAT LIVES HERE AND WHY IT IS NOT libmoy. The nine SPEC.md verbs (tri, circ,
 * circb, line, sspr, tline, blit_map, blit_batch, print) are libmoy's and both
 * sides CALL them -- see libmoy/UPSTREAM.md. What libmoy has no counterpart for
 * is this console's compositor: a viewport-aware fill and fill_rect, the RGB565
 * blit with its opaque memcpy lane, the integer-upscale blit the windowed WM
 * composites through, blit_window, scroll_rect, blit_indices, and the
 * system-font (scale > 1) text rasterizer. These loops were once written
 * twice (moyhost_gfx.c + modmoy_gfx.c) and had already drifted; this file is
 * the one copy.
 *
 * WHY IT MATTERS MORE THAN A TIDY-UP. The goldens
 * (tests/test_spec_conformance.py) replay CART draws, and a cart's coordinates
 * reach these loops already camera-shifted and clip-intersected by
 * DeviceCanvas._fill, in Python. So conformance only ever exercises the
 * clipped, positive, in-bounds regime; the CLAMPING branches -- the ones that
 * stop a bad call scribbling over the framebuffer -- were covered on each side
 * separately and agreed only by hand. The failure shape that arrangement
 * produces is a host-green build whose board writes past a framebuffer edge.
 *
 * THE SPLIT BETWEEN THIS FILE AND moy_gfx_kernels.c. Everything here is
 * `static inline` because it has HOT callers that must keep inlining it:
 * mg_fill_run is called per ROW by modmoy_gfx.c's fill_spans and gate_fill,
 * mg_clip/mg_canvas/mg_canvas_solid once per verb by ~20 call sites across both
 * files. The big loops are in the .c, which carries the -O3 pragma; that file's
 * header explains why the pragma has to be where it is, and why every TU that
 * includes THIS header must have the same optimization level as the functions
 * it inlines these into (GCC refuses to inline across an optimize-attribute
 * mismatch, which would turn mg_fill_run back into a per-row call).
 *
 * TYPES ARE PLAIN C. The device used mp_int_t throughout; that is MicroPython's
 * word type, and on both boards it is exactly int32_t. Plain `int` here is
 * therefore identical on glass, and on the 64-bit unix build it makes the two
 * sides agree MORE closely -- gfx_binding.py already marshals every one of
 * these through ctypes.c_int.
 */

#ifndef MOY_GFX_KERNELS_H
#define MOY_GFX_KERNELS_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "moy.h"        /* built MOY_PIXEL_RGB565 on every tier that sees this */

/* ---- inline helpers: hot callers on both sides ------------------------ */

/* A run of `n` RGB565 pixels set to `c`, written 32 bits at a time.
 *
 * WHY (measured on P4 glass 2026-07-26): a full-screen 1.2MB fill through the
 * naive uint16 loop took 18.8ms -- while memcpy moved TWICE the bytes (a 1.2MB
 * read plus a 1.2MB write) in 26.9ms, i.e. ~40% less time per byte. A copy
 * cannot beat a fill on a memory-bound path, so the loop was the bottleneck,
 * not the bus: one 16-bit store per pixel leaves half of every 32-bit bus beat
 * unused and pays full loop overhead per pixel. Pairing the pixels into 32-bit
 * stores and unrolling by four fixes both. (RV32 has no wider scalar store, so
 * 32 bits is the ceiling here.) */
static inline void mg_fill_run(uint16_t *px, size_t n, uint16_t c)
{
    if (n == 0) return;
    /* Align to a 4-byte boundary so the 32-bit stores never straddle one. */
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

/* Clamp the clip rect to the buffer (cols to dw, rows to capacity/dw). Callers
 * then test cx0 <= x < cx1 && cy0 <= y < cy1. */
static inline void mg_clip(int dw, size_t cap, int *cx0, int *cy0,
                           int *cx1, int *cy1)
{
    int max_rows = (int)(cap / (size_t)dw);
    if (*cx0 < 0) *cx0 = 0;
    if (*cy0 < 0) *cy0 = 0;
    if (*cx1 > dw) *cx1 = dw;
    if (*cy1 > max_rows) *cy1 = max_rows;
}

/* libmoy addresses a sheet with SPEC.md 3.2's FIXED geometry (128 x 256, 16
 * tiles per row) rather than a width passed per call, so handing it anything
 * else would read at the wrong stride or past the end. A cart sheet is exactly
 * that shape -- Project._build_sheet makes a 16x32 SpriteSheet and from_hex
 * pads a short (pre-512) blob into the top half -- so this is a guard against a
 * wrong caller, not a case that happens. Silent rather than raising, like the
 * other malformed-input guards: a draw verb that throws mid-frame takes the
 * cart down. */
static inline int mg_is_moy_sheet(int w, int h, size_t len)
{
    return w == MOY_SHEET_W && h == MOY_SHEET_H &&
           len >= (size_t)(MOY_SHEET_W * MOY_SHEET_H);
}

/* -- the libmoy bridge -------------------------------------------------------
 *
 * libmoy holds camera, clip and the index->pixel table in a moy_canvas; both
 * moy_gfx surfaces pass them as scalars on every call. Rather than invert
 * either side, a canvas is BORROWED for the duration of one call: it points at
 * the caller's buffer and is filled in from the same arguments the hand-written
 * kernels use.
 *
 * The cost is the store[] copy -- 128 bytes for the 64-entry table. That is the
 * whole bridge, and it is why this is affordable per call: no allocation, no
 * palette rebuild (the LUT arrives already folded through `pal`, which is
 * exactly what libmoy's store[] is), nothing that scales with the pixels drawn.
 *
 * `h` is DERIVED from the buffer capacity rather than trusted, the same way
 * mg_clip does it: a canvas whose h exceeds its buffer would let libmoy's own
 * clamping write past the end. */
static inline void mg_canvas(moy_canvas *c, uint16_t *dst, int dw, size_t cap,
                             const uint16_t *lut, const uint8_t *palt,
                             int cam_x, int cam_y,
                             int cx0, int cy0, int cx1, int cy1)
{
    c->pix = dst;
    c->w = dw;
    c->h = (int)(cap / (size_t)dw);
    c->cam_x = cam_x;
    c->cam_y = cam_y;
    c->clip_x0 = cx0;
    c->clip_y0 = cy0;
    c->clip_x1 = cx1;
    // The fill pattern every libmoy shape kernel reads: a canvas built here by
    // hand rather than through moy_canvas_init must say "solid" itself, or the
    // kernel reads a stack pattern and draws holes. (It did: the semantic pin
    // caught a circle with 383 of them.)
    c->fillp = 0;
    c->fillp_col = -1;
    c->spal_identity = 1;
    c->clip_y1 = cy1;
    /* pal is already folded into `lut` by the Python side (_wire_pal), so
     * store[] IS that table and pal[] stays identity -- libmoy reads store[] on
     * every pixel and pal[] only when rebuilding, which never happens here. */
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

/* A canvas for the verbs that take ONE already-resolved colour. moybyte's
 * kernels are handed a 565 word; libmoy's take an index and look it up. Filling
 * the table with that one word and passing index 0 bridges the two exactly --
 * libmoy reads store[col & 63] and cannot tell that the other 63 slots agree. */
static inline void mg_canvas_solid(moy_canvas *c, uint16_t *dst, int dw,
                                   size_t cap, uint16_t col,
                                   int cam_x, int cam_y,
                                   int cx0, int cy0, int cx1, int cy1)
{
    uint16_t lut[MOY_PALETTE];
    for (int i = 0; i < MOY_PALETTE; i++) lut[i] = col;
    mg_canvas(c, dst, dw, cap, lut, NULL, cam_x, cam_y, cx0, cy0, cx1, cy1);
}

/* ---- the PRE-KERNEL GUARDS, once ---------------------------------------
 *
 * These ran twice -- modmoy_gfx.c and moyhost_gfx.c -- and drifted, in exactly
 * the way that arrangement drifts. `dh` IS DELIBERATELY NOT TESTED: the board
 * marks the argument `(void)dh` and takes its row count from the buffer
 * capacity, which is the only number that can be trusted, while the host used
 * to refuse `dh <= 0` on line/circ/circb/tri/sspr/tline -- so a zero-height
 * canvas drew NOTHING on the host and fourteen pixels on glass, and no test
 * could see it (the goldens only ever replay a real canvas). Pinned by the
 * `dh = 0` ops in tests/test_gfx_binding.py's clamp script.
 */

/* The solid-colour verbs' whole prologue (line/circ/circb/tri): refuse a
 * degenerate width, clamp the clip to the buffer, borrow a one-colour canvas.
 * 0 = nothing to draw. A function and not a macro so neither surface has to
 * name its locals to match the other's. */
static inline int mg_solid_prologue(moy_canvas *c, uint16_t *dst, int dw,
                                    size_t cap, int col, int cam_x, int cam_y,
                                    int *cx0, int *cy0, int *cx1, int *cy1)
{
    if (dw <= 0) return 0;
    mg_clip(dw, cap, cx0, cy0, cx1, cy1);
    mg_canvas_solid(c, dst, dw, cap, (uint16_t)col, cam_x, cam_y,
                    *cx0, *cy0, *cx1, *cy1);
    return 1;
}

/* The map verbs' cells guard (tline/blit_map). SPEC.md 3.3's bound is checked
 * BEFORE the area, so mw * mh cannot overflow on the way to being compared. */
static inline int mg_map_ok(int mw, int mh, size_t cells_len)
{
    return mw > 0 && mh > 0 && mw <= MOY_MAP_MAX && mh <= MOY_MAP_MAX
           && (size_t)(mw * mh) <= cells_len;
}

/* ---- the compositor loops (moy_gfx_kernels.c) ------------------------- */

/* fill(px, cap, npix, color) -- the first `npix` pixels, clamped to capacity. */
void mg_fill(uint16_t *px, size_t cap, int npix, int color);

/* fill_rect in an RGB565 buffer of `stride` px/row, every edge clamped. */
void mg_fill_rect(uint16_t *px, size_t cap, int stride,
                  int x, int y, int w, int h, int color);

/* Shift the pixels inside (rx, ry, rw, rh) by (dx, dy) IN PLACE: the #113
 * scroll-as-blit primitive. Pixels that would leave the rect are dropped; the
 * strip shifted in from outside keeps its stale content. */
void mg_scroll_rect(uint16_t *px, size_t cap, int stride,
                    int rx, int ry, int rw, int rh, int dx, int dy);

/* key < 0 opaque (memcpy lane), else source pixels equal to `key` are skipped.
 * The clip rect [cx0,cy0)..[cx1,cy1) is intersected with the destination. */
void mg_blit565(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                const uint16_t *src, size_t scap, int sw, int sh, int key,
                int cx0, int cy0, int cx1, int cy1);

/* Integer-upscale an RGB565 source into dst at (dx, dy), which may be NEGATIVE
 * (the cover-crop wallpaper backdrop overhangs the desktop on every side).
 * Opaque, no colorkey. The windowed composite primitive (#58/#73). */
void mg_blit565_scale(uint16_t *dst, size_t dcap, int dw, int dh,
                      int dx, int dy,
                      const uint16_t *src, size_t scap, int sw, int sh,
                      int scale);

/* Copy a dw x dh window from a wider RGB565 source: the scroll engine's core
 * op (#43), clamped to BOTH buffers. */
void mg_blit_window(uint16_t *dst, size_t dcap, int dw, int dh,
                    const uint16_t *src, size_t scap, int src_w, int sx, int sy);

/* Place an iw x ih palette-INDEX bitmap (1 byte/pixel) at (dx, dy), resolving
 * through `pal`. Opaque; an index past the palette is skipped (#63 Fold 3). */
void mg_blit_indices(uint16_t *dst, size_t dcap, int dw, int dh, int dx, int dy,
                     const uint8_t *idx, size_t icap, int iw, int ih,
                     const uint16_t *pal, size_t pcap);

/* The petme128 rasterizer behind the scale > 1 lane of `text` and behind the
 * print draw gate. The clip rect is intersected with the buffer here, so every
 * caller is bounds-safe. */
void mg_text_raw(uint16_t *dst, size_t dcap, int dw,
                 const uint8_t *s, size_t slen, int x, int y, uint16_t col,
                 const uint8_t *font, int nglyphs, int first, int scale,
                 int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1);

/* text (#62): a whole string in ONE call, camera and clip honoured per pixel.
 * Scale 1 is a CART's text, which SPEC.md 6 fixes at 8px, so it goes through
 * libmoy's moy_print and libmoy's own compiled-in font -- the `font` blob is
 * IGNORED on that lane. Scale > 1 is never a cart: it is this console's CHROME
 * at the #39 system font size, which is host business (SPEC.md 0) and stays in
 * mg_text_raw. */
void mg_text(uint16_t *dst, size_t dcap, int dw,
             const uint8_t *s, size_t slen, int x, int y, int col,
             const uint8_t *font, int nglyphs, int first, int scale,
             int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1);

#endif /* MOY_GFX_KERNELS_H */
