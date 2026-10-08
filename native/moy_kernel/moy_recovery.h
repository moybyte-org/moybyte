// moy_recovery: the recovery screen's raster (docs/kernel_spine_2026-10.md §7).
//
// What the kernel draws when the console cannot come up: a filled rect and an
// 8x8 glyph from libmoy's compiled-in `moy_font_data` at an integer scale,
// rotated per pixel, straight into a panel module's framebuffer 0. No canvas
// state, no compositor, no PPA, no allocation. Portable C: the host builds it
// for tests/test_moy_kernel.py, which renders every console's geometry and
// pins the hashes the boards print.
//
// The screen is LOGICAL landscape (w x h); `rot` turns it onto the panel's
// framebuffer counter-clockwise, the PPA's convention (device/dsi_panel.py's
// rotate_rect): 90 maps (x, y) to (y, w-1-x), 270 maps it to (h-1-y, x).

#ifndef MOY_RECOVERY_H
#define MOY_RECOVERY_H

#include <stdint.h>

#include "moy_crash.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int w, h;                        // the logical screen
    int fb_w, fb_h;                  // the framebuffer, panel-native
    int rot;                         // 0, 90, 180 or 270
    int swap;                        // the framebuffer holds RGB565 high byte first
    int scale;                       // the glyph scale: one 8x8 cell is 8*scale
} moy_rgeom_t;

#define MOY_RV_LINES   8
#define MOY_RV_COLS    40
#define MOY_RV_CHOICES 3             // RETRY, SAFE, REPL: MOY_BOOT_START + index

typedef struct {
    char line[MOY_RV_LINES][MOY_RV_COLS];
    int nlines;
    char hint[MOY_RV_COLS];
    int sel;                         // the highlighted choice, 0..2
} moy_rview_t;

void moy_rgeom_init(moy_rgeom_t *g, int w, int h, int rot, int swap);

// The lines the screen says: why it is up, what the record names, the
// firmware. `rec` may be NULL.
void moy_recovery_view(moy_rview_t *v, int why, const moy_crash_rec_t *rec,
                       const char *label, const char *hint);

void moy_recovery_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v);

// The kernel's plain screen: a titled bar, the lines and the hint, no choices.
void moy_recovery_render_plain(uint16_t *fb, const moy_rgeom_t *g, const char *title,
                               const moy_rview_t *v);
// What the plain web-console screen says: the address to open, the pin, the
// firmware.
void moy_web_screen_view(moy_rview_t *v, const char *url, const char *pin,
                         const char *label);

// The choice under a logical point, or -1.
int moy_recovery_hit(const moy_rgeom_t *g, int x, int y);

const char *moy_recovery_choice_name(int choice);

#ifdef __cplusplus
}
#endif

#endif // MOY_RECOVERY_H
