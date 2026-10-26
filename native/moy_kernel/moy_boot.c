// moy_boot: the boot logo's raster. The header has the design.

#include "moy_boot.h"

#include <string.h>

#include "moy_scr.h"

int moy_boot_logo_render(uint16_t *fb, const moy_rgeom_t *g, const uint16_t *pal) {
    moy_scr_t s;
    memset(&s, 0, sizeof(s));
    s.fb = fb;
    s.fb_w = g->fb_w;
    s.w = g->w;
    s.h = g->h;
    s.rot = g->rot;
    s.swap = g->swap;
    if (g->rot != 0) {
        s.band = g->band;
        s.band_rows = g->band_rows;
    }
    s.scratch = g->scratch;
    s.scratch_n = g->scratch_n;
    return moy_splash(&s, pal, -1, NULL, 0);
}
