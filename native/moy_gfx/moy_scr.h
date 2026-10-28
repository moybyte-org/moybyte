// moy_scr: a whole screen drawn with no canvas -- fills, rounded rects, the
// outline faces (moy_path.h) and Moy's pixel art -- into an RGB565
// framebuffer, turned a quarter where the panel is (docs/look_2026-10.md
// step 33, GAPS 55).
//
// The kernel draws its screens through it before any VM exists (first
// light, the recovery floor, the plain web-console screen:
// native/moy_kernel), and the console's boot splash draws the same picture
// through it (console.draw_splash, moy_gfx.splash), so the two are one
// picture by construction.
//
// Coordinates are the LOGICAL screen (w x h). `rot` turns it onto the
// framebuffer counter-clockwise, the PPA's convention: 90 maps (x, y) to
// (y, w-1-x), 270 to (h-1-y, x). A turned screen draws in bands of
// `band_rows` logical rows into `band` and turns each into the framebuffer;
// an unturned one draws straight into it. With `scratch` lent, nothing
// allocates (moy_path_lend). Colours are RGB565, high bits red; `swap` says
// the framebuffer holds them high byte first.
//
// Portable C: the host builds it for the canvas binding and for
// tests/test_moy_kernel.py.

#ifndef MOY_SCR_H
#define MOY_SCR_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct moy_scr {
    uint16_t *fb;                   // the framebuffer, panel-native
    int fb_w;                       // its row, in pixels
    int w, h;                       // the logical screen
    int rot;                        // 0, 90, 180 or 270
    int swap;
    uint16_t *band;                 // a turned screen's band: w * band_rows pixels
    int band_rows;
    void *scratch;                  // lent to the outline faces for the draw; NULL: theirs
    size_t scratch_n;
    // the pass being drawn: its rows [y0, y1) at `px`, `stride` a row
    uint16_t *px;
    int stride, y0, y1;
    int fails;                      // draws that could not finish (a scratch too small)
} moy_scr_t;

typedef void (*moy_scr_paint_t)(moy_scr_t *s, const void *ctx);

// Draw the screen `paint` paints, once for an unturned screen, once a band
// for a turned one. The draws that failed (0: none).
int moy_scr_draw(moy_scr_t *s, moy_scr_paint_t paint, const void *ctx);

// The faces (moy_path.h's).
enum { MOY_SCR_SANS = 0, MOY_SCR_BOLD = 1, MOY_SCR_MONO = 2 };

void moy_scr_fill(moy_scr_t *s, int x, int y, int w, int h, uint16_t c);
// A rect with corners of radius r, anti-aliased; `t` > 0 draws only its
// edge, t wide.
void moy_scr_rrect(moy_scr_t *s, int x, int y, int w, int h, int r, int t, uint16_t c);
// `n` bytes of UTF-8 in `face` at `size` px, the baseline's left end at
// (x, base). The advance, px.
int moy_scr_text(moy_scr_t *s, int face, int size, int x, int base, const char *t, size_t n,
                 uint16_t c);
int moy_scr_width(int face, int size, const char *t, size_t n);
// The face's cap height at `size` px.
int moy_scr_cap(int face, int size);
// One of the smooth set's marks the kernel draws, in a `size` px box at
// (x, y): Try again's, Home's and the code's.
enum { MOY_SCR_ICON_RETRY, MOY_SCR_ICON_HOME, MOY_SCR_ICON_CODE, MOY_SCR_ICONS };
void moy_scr_icon(moy_scr_t *s, int icon, int x, int y, int size, uint16_t c);
// Moy, the console's mascot (chrome.py's "moy" art), 16 cells at `scale`.
void moy_scr_moy(moy_scr_t *s, int x, int y, int scale);
// a + (b - a) * k / 256, per channel.
uint16_t moy_scr_mix(uint16_t a, uint16_t b, int k);

// ---- the boot splash ------------------------------------------------------------
//
// Moy at min(w, h) / 56 times (at least 3) over the `moybyte` wordmark in
// the bold face, on the theme's ground; `permille` >= 0 adds a track under
// it and `status` a line in the dim ink. `pal` is the ground, the ink, the
// dim ink, the value, the track and the track's edge (the track's own colour
// for none) -- `moy_splash_day` before a theme is stored.

enum { MOY_SPLASH_BG, MOY_SPLASH_INK, MOY_SPLASH_DIM, MOY_SPLASH_VALUE, MOY_SPLASH_TRACK,
       MOY_SPLASH_EDGE, MOY_SPLASH_PAL };

extern const uint16_t moy_splash_day[MOY_SPLASH_PAL];

int moy_splash(moy_scr_t *s, const uint16_t *pal, int permille, const char *status, size_t n);

// The splash into the (x0, y0) w x h window of an unturned buffer `stride`
// pixels a row, `cap` pixels long: the canvas's (moy_gfx.splash). The
// failed draws, or -1 for a window the buffer does not hold.
int moy_splash_buf(uint16_t *buf, size_t cap, int stride, int x0, int y0, int w, int h,
                   int swap, const uint16_t *pal, int permille, const char *status, size_t n);

#ifdef __cplusplus
}
#endif

#endif // MOY_SCR_H
