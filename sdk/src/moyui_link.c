// The front of the toolkit a native app links (moybyte/ui.h): the app's own
// copy of native/moy_ui, its RUN face (moy_ui_wasm.c) drawing on a canvas of
// the app's own, the RGB565 screen moy_present hands to the console. What
// the RUN face reads from a compiled run's engine it reads from here: the
// frame's input words (moycore_ui_snap), its milliseconds
// (moycore_ui_dt_take), the canvas (moycore_ui_target) and the theme
// (moy_app_wasm_theme), which the console's theme block brings in through the
// moy_ui_pull and moy_ui_font_pull rows (moy_ui.h's moy_ui_theme_pull). The
// app's `_layout`, where it defines one, hears the surface the frame's layout
// words name whenever they move.

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "moy.h"
#include "moy_text.h"
#include "moy_ui.h"
#include "moy_ui_api.h"
#include "moy_ui_wasm.h"
#include "moybyte/frame.h"
#include "moybyte/os.h"
#include "moybyte/ui.h"

// The run's snapshot as moycore_run.h lays it out (SNAP_LEN words).
#define SNAP_LEN 19

static moy_canvas s_cv;
static moy_pixel *s_px;
static int s_w, s_h;
static int32_t s_snap[SNAP_LEN];
static uint32_t s_tick;
static int32_t s_dt;
static uint16_t s_canon[MOY_PALETTE];   // the palette's words as the canvas began
static int32_t s_swap;
static int32_t s_lay[3];                // the (w, h, fs) `_layout` last heard
static uint8_t s_paste[MOY_UI_API_PASTE];  // the console's last "paste" message's text
static size_t s_paste_n;

int moy_ui_link_open(void) {
    if (s_px != NULL) {
        return 0;
    }
    int w = 0, h = 0;
    if (moy_screen(&w, &h) != 0) {
        return -1;
    }
    size_t cap = moy_ui_theme_size();
    void *theme = malloc(cap);
    moy_pixel *px = malloc((size_t)w * (size_t)h * sizeof(moy_pixel));
    if (theme == NULL || px == NULL) {
        free(theme);
        free(px);
        return -1;
    }
    memset(px, 0, (size_t)w * (size_t)h * sizeof(moy_pixel));
    moy_canvas_init(&s_cv, px, w, h);
    memcpy(s_canon, s_cv.store, sizeof s_canon);
    s_px = px;
    s_w = w;
    s_h = h;
    moy_ui_cart_text(1);
    moy_ui_inst_pull(NULL, moy_ui_pull, theme, cap, moy_ui_font_pull);
    return 0;
}

// The console's "paste" message (moy_os.h's MOY_OS_PASTE): its one string,
// whose escapes are a quote's and a backslash's alone, kept for the frame
// that carries its Ctrl+V.
static void heard(const moy_evq_ev_t *ev, const char *args) {
    if (ev->from > 1u || strcmp(ev->name, "paste") != 0) {
        return;
    }
    const char *a = strchr(args, '"');
    size_t n = 0;
    if (a != NULL) {
        for (a++; *a != 0 && *a != '"' && n < sizeof s_paste; a++) {
            if (*a == '\\' && a[1] != 0) {
                a++;
            }
            s_paste[n++] = (uint8_t)*a;
        }
    }
    s_paste_n = n;
}

int moy_ui_link_frame(moy_frame_t *f) {
    if (s_px == NULL && moy_ui_link_open() != 0) {
        return 0;
    }
    if (!moy_game_frame(f, heard)) {
        return 0;
    }
    if (f->in[MOY_FRAME_KEY] == 0x16 && s_paste_n > 0) {
        moy_ui_run_paste(s_paste, s_paste_n);      // Ctrl+V: the console's Paste
        s_paste_n = 0;
    }
    s_snap[MOYCORE_UI_SNAP_BTN] = (int32_t)f->held;
    s_snap[MOYCORE_UI_SNAP_BTNP] = (int32_t)f->pressed;
    s_snap[MOYCORE_UI_SNAP_X] = f->in[MOY_FRAME_X];
    s_snap[MOYCORE_UI_SNAP_Y] = f->in[MOY_FRAME_Y];
    s_snap[MOYCORE_UI_SNAP_PTR] = f->in[MOY_FRAME_PTR];
    s_snap[MOYCORE_UI_SNAP_KEY] = f->in[MOY_FRAME_KEY];
    s_snap[MOYCORE_UI_SNAP_FACE] = f->in[MOY_FRAME_FACE];
    s_snap[MOYCORE_UI_SNAP_ROOM_X] = f->in[MOY_FRAME_ROOM_X];
    s_snap[MOYCORE_UI_SNAP_ROOM_Y] = f->in[MOY_FRAME_ROOM_Y];
    s_snap[MOYCORE_UI_SNAP_ROOM_W] = f->in[MOY_FRAME_ROOM_W];
    s_snap[MOYCORE_UI_SNAP_ROOM_H] = f->in[MOY_FRAME_ROOM_H];
    if (f->in[MOY_FRAME_SWAP] != s_swap) {
        uint16_t wire[MOY_PALETTE];
        for (int i = 0; i < MOY_PALETTE; i++) {
            wire[i] = f->in[MOY_FRAME_SWAP] ? (uint16_t)((s_canon[i] << 8) | (s_canon[i] >> 8))
                                            : s_canon[i];
        }
        moy_canvas_wire(&s_cv, wire);
        moy_text_wire(f->in[MOY_FRAME_SWAP] != 0);
        s_swap = f->in[MOY_FRAME_SWAP];
    }
    if (_layout != NULL && f->in[MOY_FRAME_LAYOUT_W] > 0
        && memcmp(s_lay, &f->in[MOY_FRAME_LAYOUT_W], sizeof s_lay) != 0) {
        memcpy(s_lay, &f->in[MOY_FRAME_LAYOUT_W], sizeof s_lay);
        _layout(s_lay[0], s_lay[1], s_lay[2]);
    }
    s_dt += (int32_t)f->ms;
    s_tick++;
    moy_ui_inst_sync(NULL);
    return 1;
}

int moy_ui_link_show(void) {
    if (s_px == NULL) {
        return -1;
    }
    return moy_present(s_px, (size_t)s_w * (size_t)s_h * sizeof(moy_pixel));
}

void *moy_ui_link_target(void) {
    return s_px != NULL ? &s_cv : NULL;
}

const int32_t *moycore_ui_snap(uint32_t *tick, uint32_t *serial) {
    *tick = s_tick;
    *serial = 1u;
    return s_snap;
}

int32_t moycore_ui_dt_take(void) {
    int32_t dt = s_dt;
    s_dt = 0;
    return dt;
}

void *moycore_ui_target(void) {
    return moy_ui_link_target();
}

// The installed block's generation and the run look it carries (the
// console keeps its MOY_UI_LOOK_RUN on the live theme's nearest tokens); no
// shape name, the block's shape being the one installed.
uint32_t moy_app_wasm_theme(int32_t out[MOY_UI_TOKENS], uint32_t have, char *name,
                            uint32_t cap) {
    (void)have;
    uint32_t gen = moy_ui_inst_main()->theme_gen;
    if (gen == 0u) {
        return 0u;
    }
    moy_ui_look_get(MOY_UI_LOOK_RUN, out);
    if (cap > 0u) {
        name[0] = 0;
    }
    return gen;
}
