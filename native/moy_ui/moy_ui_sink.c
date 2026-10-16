// The ROW sink (moy_ui.h): a canvas row of the glass, by its CANVAS handle.
// Its own file because it is the one call that reaches the glass's handle
// table; a library that draws only through the RUN sink (the host's compiled
// run, runtime/lua_binding.py) leaves it out.

#include "moy_ui.h"
#include "moy_canvas.h"

int moy_ui_sink_row(moy_ui_sink_t *s, uint32_t canvas, int look) {
    moy_canvas_row_t *row;
    if (moy_canvas_get(canvas, &row) != 0 || row->px == NULL) {
        return MOY_UI_STALE;
    }
    if (look < 0 || look >= MOY_UI_LOOKS) {
        return MOY_UI_BAD;
    }
    s->kind = MOY_UI_SINK_ROW;
    s->fs = (uint8_t)(row->st[MOY_ST_FONT_SCALE] > 0 ? row->st[MOY_ST_FONT_SCALE] : 1);
    s->look = (uint8_t)look;
    s->dst = row;
    return MOY_UI_OK;
}

int moy_ui_sink_mask(const moy_ui_sink_t *s, uint32_t font, int code, int x, int y, int ci) {
    moy_ui_count_call();
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    const int32_t *st = row->st;
    moy_text_target_t t = {
        row->px, row->cap, (int)st[MOY_ST_W], (int)st[MOY_ST_CAM_X], (int)st[MOY_ST_CAM_Y],
        (int)st[MOY_ST_CX0], (int)st[MOY_ST_CY0], (int)st[MOY_ST_CX1], (int)st[MOY_ST_CY1],
    };
    uint8_t b = (uint8_t)code;
    int w = moy_text(&t, font, MOY_TEXT_DATA, &b, 1, x, y, row->pal[ci & moy_ui_cmask()], 1,
                     MOY_TEXT_LEFT, -1);
    row->st[MOY_ST_N_TEXT]++;
    return w < 0 ? MOY_UI_BAD : MOY_UI_OK;
}
