/* moy_backdrop: a theme's backdrop rasterised (docs/media/look_2026-10/GAPS.md
 * gap 9) -- the shapes the loader reads from a theme's `backdrop.home` and
 * `backdrop.desk` (native/moy_ui/moy_theme.c, moy_theme_backdrop), drawn over
 * the surface's fill into the one wallpaper bake (docs/theming_2026-09.md D6),
 * or straight onto a canvas, clipped, where no bake is kept.
 *
 * A record, little-endian, one per shape:
 *   kind, mode, steps, alpha (0..255), n_stops (1..4), n_at
 *   n_stops x (t: int16 thousandths of the shape's rows, r, g, b)
 *   n_at x int16 coordinates, in thousandths of the surface: x and widths of
 *   its width; y, heights and radii of its height
 * kind is style.json's theme.look.backdrop.kinds in order, each with its
 * coordinate count:
 *   fill 0 | band y0 y1 | circle cx cy r | ellipse cx cy rx ry |
 *   rrect x y w h r | pie cx cy r a0 a1 (degrees, clockwise from 3 o'clock) |
 *   wave x0 y0 .. xn yn (2..8 points; the area under the curve through them)
 * mode is a paint's (smooth: one colour per row; steps:N, N flat bands;
 * dither: the 4x4 ordered blend of the two nearest stops), its stops spread
 * over the shape's own rows. Edges are anti-aliased at 16 coverage levels and
 * a shape's coverage is scaled by its alpha.
 *
 * The paint and the coverage are this file's own; the paints and AA shapes
 * of the toolkit (GAPS gaps 5 and 7) are separate kernels. */

#ifndef MOY_BACKDROP_H
#define MOY_BACKDROP_H

#include <stddef.h>
#include <stdint.h>

enum {
    MG_BD_FILL, MG_BD_BAND, MG_BD_CIRCLE, MG_BD_ELLIPSE, MG_BD_RRECT, MG_BD_PIE, MG_BD_WAVE,
    MG_BD_KINDS
};

#define MG_BD_SHAPES 16
#define MG_BD_STOPS 4
#define MG_BD_POINTS 8

/* Fill the w x h surface at (ox, oy) of `px` (`cap` words, `stride` per row)
 * with `bg`, then draw the records `rec` over it, all within the clip
 * [cx0, cx1) x [cy0, cy1) of `px`. bg is a word as `px` holds it; `swap` set
 * when `px` holds byte-swapped RGB565. The shapes drawn, or -1: the records
 * are malformed and only the fill is drawn. */
int mg_backdrop(uint16_t *px, size_t cap, int stride, int ox, int oy, int w, int h,
                const uint8_t *rec, size_t n, uint16_t bg, int swap,
                int cx0, int cy0, int cx1, int cy1);

#endif
