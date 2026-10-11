// The one glyph raster (moy_ui.h): a glyph's spans derived from its rows at
// draw time, at any scale, so no per-scale pack is kept. The toolkit,
// moy_chrome's GLYPH op and a compiled app's ui_glyph all draw through it.

#include <string.h>

#include "moy_ui.h"

int moy_ui_glyph_id(const char *name, size_t n) {
    for (int i = 0; i < MOY_UI_GLYPHS; i++) {
        if (strlen(moy_ui_glyph_names[i]) == n && memcmp(moy_ui_glyph_names[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

static int floordiv2(int v) {
    return v >= 0 ? v / 2 : -((1 - v) / 2);
}

int moy_ui_glyph_draw(const moy_text_target_t *t, int g, int x, int y, int w, int h,
                      uint16_t col, int scale) {
    if (g < 0 || g >= MOY_UI_GLYPHS) {
        return 0;                       // an unknown glyph draws nothing
    }
    if (scale < 1) {
        scale = 1;
    }
    const int n = MOY_UI_GLYPH_SIZE;
    const uint16_t *rows = moy_ui_glyph_rows[g];
    int span = n * scale;
    int ox = x + floordiv2(w - span);
    int oy = y + floordiv2(h - span);
    int spans = 0;
    for (int r = 0; r < n; r++) {
        int run = 0;
        for (int c = 0; c <= n; c++) {
            if (c < n && (rows[r] & (1u << (n - 1 - c)))) {
                run++;
            } else if (run) {
                moy_target_fill(t, ox + (c - run) * scale, oy + r * scale, run * scale, scale,
                                col);
                spans++;
                run = 0;
            }
        }
    }
    return spans;
}
