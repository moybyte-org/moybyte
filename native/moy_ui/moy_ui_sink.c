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
