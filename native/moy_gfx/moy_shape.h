/* moy_shape: the 2026-10 look's shapes (docs/look_2026-10.md step 6, GAPS
 * 5-8): rounded rects with anti-aliased or stepped corners, rings, bevels,
 * paints and shadows, into an RGB565 target (moy_text.h's
 * moy_text_target_t: camera-offset, clipped to its clip and its buffer).
 *
 * CORNERS. A corner is a quarter-circle COVERAGE TABLE per radius: 4 bits a
 * pixel, 15 inside the arc, 0 outside, the fraction of the pixel a 4x4
 * supersample finds inside between (128 bytes at r = 16). A table is baked
 * the first time its radius draws and kept in a small cache; radii above
 * MOY_SHAPE_RMAX draw at MOY_SHAPE_RMAX. A STEPPED corner reads the same
 * table thresholded at half (a pixel is in or out). Interiors are span
 * fills; only a corner's partly covered pixels read the target and blend.
 * A circle is a rect whose radius is half its shorter side; a pill past it.
 *
 * A RING of width `line` is the outer shape less the shape inset by `line`
 * with its radius less `line`, so its arcs are concentric with the fill's.
 * With a fill and a ring in one call each pixel is composed once:
 * under * (1 - outer) + ring * (outer - inner) + fill * inner. A BEVEL is a
 * ring whose top and left take `line_hi` and whose bottom and right take
 * `line_lo`; in the top-right and bottom-left corners a pixel nearer the top
 * (or the left) edge takes `line_hi`.
 *
 * MASKS. MOY_SHAPE_INNER draws only the pixels the outline covers whole
 * (and a ring's pixels it covers half or more of, opaque in the ring's
 * colour), MOY_SHAPE_FRINGE only the rest it covers in part, which blend over
 * what is under them. The two together are the whole shape. A window's frame is the
 * inner, drawn over pixels it paints itself, so it repeats to the same
 * pixels; its fringe belongs to whatever repaints the pixels under it (the
 * desk, runtime/wm_desk.py, or a window behind, runtime/wm_chrome.py), so a
 * fringe never blends twice.
 *
 * PAINTS (GAPS 7). A fill is a paint: up to MOY_SHAPE_STOPS stops (t in
 * thousandths down the paint's rows, a colour), in mode SMOOTH (one colour a
 * row, the stops' channels interpolated and rounded to RGB565), STEPS (N
 * bands, the first the first stop's colour and the last the last's) or
 * DITHER (the two stops either side of a row in a 4x4 Bayer pattern
 * anchored to the buffer, its density the row's place between them, as
 * libmoy's fillp draws two colours). One stop is a solid colour.
 *
 * SHADOWS (GAPS 6). A soft shadow is the caster's rounded rect, offset by
 * (dx, dy), blurred `blur` pixels either side of its edge: its alpha at a
 * pixel is a smoothstep of the pixel's signed distance to the rounded rect,
 * full `blur` pixels inside, none `blur` outside, quantised to 4 bits. The
 * MASK is that function sampled once per (radius, blur): a corner block of
 * (blur + max(radius, blur)) pixels square and a profile across the edge,
 * which serve every size of rect by symmetry (each pixel reads the corner
 * nearest it). Masks are cached; a draw reads the mask and blends `col` at
 * `alpha` * mask / 15 over the target. Blur 0 is a HARD shadow: the offset
 * shape filled at `alpha`. A HOLE (a rect and its radius) is left untouched:
 * the caster's own body, which covers it.
 *
 * COVERS (steps 15 and 17). A cover is a picture's sub-rect scaled into a
 * rounded rect: its interior a straight nearest-neighbour sample, its
 * corners' partly covered pixels the picture over the target at their
 * coverage. A TINT copies a sub-rect out with a colour over it, the dimmed
 * neighbour a cache keeps rather than a blend every frame.
 *
 * COLOURS are RGB565 in the buffer's wire order; moy_shape_wire_swapped says
 * which order that is (the S3 boards, the host and the browser hold it high
 * byte first, the P4's DPI does not: device/device_canvas.py's WIRE_SWAPPED).
 */

#ifndef MOY_SHAPE_H
#define MOY_SHAPE_H

#include <stddef.h>
#include <stdint.h>

#include "moy_text.h"

#define MOY_SHAPE_RMAX 48
#define MOY_SHAPE_STOPS 6
#define MOY_SHAPE_BLUR_MAX 64

enum { MOY_SHAPE_ALL = 0, MOY_SHAPE_INNER = 1, MOY_SHAPE_FRINGE = 2 };
enum { MOY_SHAPE_SMOOTH = 0, MOY_SHAPE_STEPS = 1, MOY_SHAPE_DITHER = 2 };
// the corners of a corner mask, a bit each
enum { MOY_SHAPE_TL = 1, MOY_SHAPE_TR = 2, MOY_SHAPE_BR = 4, MOY_SHAPE_BL = 8 };

typedef struct {
    uint8_t n, mode, steps, pad;
    int16_t t[MOY_SHAPE_STOPS];     // thousandths, ascending
    uint16_t c[MOY_SHAPE_STOPS];    // wire order
    int y0, h;                      // the rows the paint spans; h <= 0: the shape's
} moy_shape_paint_t;

typedef struct {
    int x, y, w, h, r;              // w <= 0: no hole
} moy_shape_hole_t;

typedef struct {
    uint8_t r[4];                   // TL, TR, BR, BL
    uint8_t stepped, mask, line, pad;
    int32_t line_hi, line_lo;       // the ring's colours (lo < 0: hi all round)
    const moy_shape_paint_t *fill;  // NULL: no fill
    moy_shape_hole_t hole;
} moy_shape_rrect_t;

typedef struct {
    int r, blur, dx, dy;
    int32_t col;                    // wire order
    int alpha;                      // 0..255
    uint8_t stepped;                // a hard shadow's corners
} moy_shape_shadow_t;

// The buffer's word order (1: high byte first). Process-wide; 1 until set.
void moy_shape_wire_swapped(int swapped);
// A canonical RGB565 word in that order.
uint16_t moy_shape_wire(uint16_t canonical);

// A rect with corners `r` (each clamped to half the shorter side and to
// MOY_SHAPE_RMAX). Answers the pixels written.
int moy_shape_rrect(const moy_text_target_t *t, int x, int y, int w, int h,
                    const moy_shape_rrect_t *s);

// The shadow box (x, y, w, h) casts. Answers the pixels written.
int moy_shape_shadow(const moy_text_target_t *t, int x, int y, int w, int h,
                     const moy_shape_shadow_t *s, const moy_shape_hole_t *hole);

// Canonical `f` over canonical `b` at `alpha` (0..255): the colour a shadow
// of `f` at `alpha` leaves over `b` inside its corners, so a solid fill that
// stands for a tint, painted beside one, matches it to the bit.
uint16_t moy_shape_over(uint16_t f, uint16_t b, int alpha);

// The columns [*x0, *x1) of row i of a w x h rect with corners `r` (clamped as
// moy_shape_rrect clamps them), `stepped`, ringed `line` wide, that its
// MOY_SHAPE_INNER draws paint opaque: what a shape drawn over it after it
// may cover without blending twice (x0 == x1: none).
void moy_shape_inner_row(int w, int h, const int r[4], int stepped, int line, int i, int *x0,
                         int *x1);

// How far a shadow reaches past its caster on each side: left, top, right,
// bottom (what a caller's damage rect grows by).
void moy_shape_shadow_pad(const moy_shape_shadow_t *s, int out[4]);

// The coverage of pixel (i, j) of a corner box of radius r (row i, column j
// from the box's outer corner), 0..15: the reference the tests read.
int moy_shape_coverage(int r, int i, int j);

// Counters since the last reset: corner tables baked, shadow masks baked,
// shadows drawn, rects drawn, pixels blended.
enum { MOY_SHAPE_ST_COV_BAKES = 0, MOY_SHAPE_ST_SHADOW_BAKES = 1, MOY_SHAPE_ST_SHADOWS = 2,
       MOY_SHAPE_ST_RRECTS = 3, MOY_SHAPE_ST_BLENDS = 4, MOY_SHAPE_STATS = 5 };
void moy_shape_stats(uint32_t out[MOY_SHAPE_STATS]);
// Zero the counters; with `caches`, drop every baked table and mask too.
void moy_shape_reset(int caches);

// The canvas verbs' flat form, over a buffer with the canvas's camera and
// clip (modmoy_gfx.c and runtime/gfx_binding.py call these, nothing between).
// opts: the corners (MOY_SHAPE_TL..BL), bit 4 stepped, bits 5-6 the mask,
// bits 8-15 the ring's width. A NULL fill fills nothing.
#define MOY_SHAPE_OPT_STEPPED 16
#define MOY_SHAPE_OPT_MASK(m) ((m) << 5)
#define MOY_SHAPE_OPT_LINE(n) ((n) << 8)
// `hole` (NULL: none) is left alone, and its pixels are not counted: a caller
// that fills a solid rrect's middle itself (a DMA fill) draws the rest here.
int mg_rrect(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h, int r, int opts,
             int32_t hi, int32_t lo, const moy_shape_paint_t *fill, int cam_x, int cam_y,
             int cx0, int cy0, int cx1, int cy1, const moy_shape_hole_t *hole);
int mg_shadow(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h, int r, int blur,
              int dx, int dy, int32_t col, int alpha, int stepped, const moy_shape_hole_t *hole,
              int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1);

// A home card's cover (docs/look_2026-10.md steps 15 and 17): the sub-rect
// (sx, sy, cw, ch) of an RGB565 picture `sw` words a row, in the wire order,
// scaled nearest-neighbour to w x h at (x, y) -- target column i samples
// sx + ((i * fx + fx / 2) >> 16) with fx = (cw << 16) / w, rows alike, as
// mg_blit565_resize does -- inside corners of radius r, anti-aliased or
// `stepped`: a corner pixel the shape covers in part is the picture over what
// is there. A sub-rect past the picture's rows draws nothing. Answers the
// pixels written.
int mg_cover(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h,
             const uint16_t *src, size_t scap, int sw, int sx, int sy, int cw, int ch,
             int r, int stepped, int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1);
// The tint (step 15): the sub-rect (sx, sy, w, h) of a picture `sw` words a
// row copied into `dst` at (dx, dy), `dw` words a row (0: w, a picture of its
// own), `col` over each pixel at `alpha` (0..255), all in the wire order: a
// dimmed neighbour's cover, made once and kept, or a dimmed picture drawn
// straight onto a surface. Answers the pixels written: 0 where the rect does
// not fit either buffer.
int mg_tint(uint16_t *dst, size_t dcap, const uint16_t *src, size_t scap, int sw, int sx,
            int sy, int w, int h, uint16_t col, int alpha, int dw, int dx, int dy);

#endif
