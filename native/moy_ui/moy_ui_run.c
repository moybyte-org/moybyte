// The RUN face of the public UI API (moy_ui_run.h).

#include "moy_platform.h"

#include <stdlib.h>
#include <string.h>

#include "moy_kmem.h"

#define CTX_ALLOC(n) moy_kalloc((n), MOY_KMEM_PSRAM | MOY_KMEM_INTERNAL_OK)

#include "moy.h"
#include "moy_canvas.h"
#include "moy_theme.h"
#include "moy_ui_run.h"

// The RUN sink lends an RGB565 canvas as a row (a compile-time check, not a
// preprocessor one: MicroPython's qstr pass preprocesses without the build's
// definitions).
_Static_assert(sizeof(moy_pixel) == sizeof(uint16_t), "the RUN sink lends an RGB565 canvas");

// The generation the run's look was built under (0: none yet, which no live
// theme has), and the run's serial it was built for. A generation is counted
// by one console from its own start, so a later console (or run) can reach the
// same number on another theme: the look is rebuilt for each run.
static uint32_t s_gen, s_gen_serial;

// The live theme's token row and its name, copied when the generation differs
// from `have`: native/moy_app/moy_app_wasm.c's. Answers the generation, 0
// while no run is bound; weak, so an image without the app ABI draws no rows.
#if __has_include("moy_app_wasm.h")
#include "moy_app_wasm.h"
#else
uint32_t moy_app_wasm_theme(int32_t *out, uint32_t have, char *name, uint32_t cap);
#endif
#pragma weak moy_app_wasm_theme

// What every drawing row opens with: the RUN sink over the run's target, lent
// as `row`, the look current, the run's draw state saved in `saved` and reset.
typedef struct {
    moy_ui_sink_t s;
    moy_canvas_row_t row;
    moy_canvas *c;
    moy_canvas saved;
} run_t;

// The snapshot's face word (moy_ui_wasm.h's MOYCORE_UI_FACE_*), 0 with no run.
static int32_t run_face(void) {
    uint32_t tick = 0, serial = 0;
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    return snap != NULL ? snap[MOYCORE_UI_SNAP_FACE] : 0;
}

static int face_fs(int32_t face) {
    int fs = (face >> MOYCORE_UI_FACE_FS_SHIFT) & 0xFF;
    return fs > 1 ? fs : 1;
}

static int open_run(run_t *r, moy_canvas *c) {
    if (c == NULL || moy_app_wasm_theme == NULL) {
        return -MOY_UI_STALE;
    }
    uint32_t tick = 0, serial = 0;
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    int32_t face = snap != NULL ? snap[MOYCORE_UI_SNAP_FACE] : 0;
    int fs = face_fs(face);
    if (serial != s_gen_serial) {
        s_gen = 0;
        s_gen_serial = serial;
    }
    int32_t tok[MOY_UI_TOKENS];
    char name[MOY_UI_SHAPE_NAME];
    uint32_t gen = moy_app_wasm_theme(tok, s_gen, name, sizeof(name));
    if (gen == 0u) {
        return -MOY_UI_STALE;
    }
    if (gen != s_gen) {
        const moy_ui_shape_t *now = moy_ui_shape();
        // The live theme names another shape than the shell installed: it
        // is resolved into a block of its own (PSRAM), which the UI copies.
        moy_ui_shape_t *sh = name[0] && now != NULL && strcmp(name, now->name) != 0
            ? CTX_ALLOC(sizeof(*sh)) : NULL;
        if (sh != NULL) {
            moy_theme_shape(name, strlen(name), sh);
            moy_ui_shape_use(sh);
            free(sh);
        }
        moy_ui_look_set(MOY_UI_LOOK_RUN, tok);
        s_gen = gen;
    }
    r->c = c;
    r->saved = *c;
    moy_reset_state(c);
    memset(&r->row, 0, sizeof(r->row));
    r->row.px = (uint16_t *)c->pix;
    r->row.cap = (uint32_t)(c->w * c->h);
    r->row.w = (uint16_t)c->w;
    r->row.h = (uint16_t)c->h;
    r->row.st[MOY_ST_CX1] = r->row.st[MOY_ST_W] = c->w;
    r->row.st[MOY_ST_CY1] = r->row.st[MOY_ST_H] = c->h;
    r->row.st[MOY_ST_FONT_SCALE] = fs;
    memcpy(r->row.pal, c->store, MOY_CANVAS_BLOCK * sizeof(uint16_t));
    r->s.fs = (uint8_t)fs;
    r->s.dst = &r->row;
    if (face & MOYCORE_UI_FACE_LOOK) {
        // A responsive app's page is the shell's: the theme's block at
        // 64..127, as every glass row holds it, and outside the boundary.
        moy_ui_theme_pal(&r->row.pal[MOY_CANVAS_BLOCK]);
        r->s.kind = MOY_UI_SINK_ROW;
        r->s.look = MOY_UI_LOOK_APP;
        r->s.cart = 0;
    } else {
        // A cart's colours are MOY64 (its theme imports answer each role's
        // nearest index), so both halves are the canvas's.
        memcpy(&r->row.pal[MOY_CANVAS_BLOCK], c->store, MOY_CANVAS_BLOCK * sizeof(uint16_t));
        r->s.kind = MOY_UI_SINK_RUN;
        r->s.look = MOY_UI_LOOK_RUN;
        r->s.cart = (uint8_t)moy_ui_cart_on();
    }
    r->s.shown = (uint8_t)moy_ui_marks_shown();
    return 0;
}

static int close_run(run_t *r, int rc) {
    *r->c = r->saved;
    return rc == MOY_UI_OK ? 0 : -rc;
}

// -- the run's context ------------------------------------------------------------
//
// One run at a time, so one context: begun lazily by the first verb of each
// tick, over the run's snapshot, and reset when a new run opens. The first
// verb any run calls allocates it (CTX_ALLOC: PSRAM where the board has it)
// and it is kept: a static would hold its size in internal SRAM from boot on
// a console that never runs a cart on the API. NULL: none could be had.

static moy_ui_ctx_t *s_ctx;
static uint32_t s_tick, s_serial;
static int s_live;
static uint8_t s_paste[MOY_UI_API_PASTE];  // moy_ui_run_paste's, for the frame begun next
static size_t s_paste_n;
static int32_t s_rep, s_keyrep;            // moy_ui_run_steps', likewise

static moy_ui_ctx_t *run_ctx(const void *target) {
    const moy_canvas *c = (const moy_canvas *)target;
    uint32_t tick = 0, serial = 0;
    if (s_ctx == NULL && (s_ctx = CTX_ALLOC(sizeof(*s_ctx))) == NULL) {
        return NULL;
    }
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    if (!s_live || serial != s_serial) {
        moy_ui_api_reset_in(s_ctx, NULL);
        s_serial = serial;
        s_tick = tick - 1u;
        s_live = 1;
        (void)moycore_ui_dt_take();         // the clock starts with the run
    }
    if (tick != s_tick) {
        int32_t in[MOY_UI_IN_LEN] = {0};
        int flags = 0;
        if (snap != NULL) {
            int32_t face = snap[MOYCORE_UI_SNAP_FACE];
            flags = ((face & MOYCORE_UI_FACE_WINDOW) ? MOY_UI_WINDOW : 0)
                    | ((face & MOYCORE_UI_FACE_DESK) ? MOY_UI_DESK : 0)
                    | ((face & MOYCORE_UI_FACE_TAP) ? MOY_UI_TAP : 0);
            in[MOY_UI_IN_BTN] = snap[MOYCORE_UI_SNAP_BTN];
            in[MOY_UI_IN_BTNP] = snap[MOYCORE_UI_SNAP_BTNP];
            in[MOY_UI_IN_X] = snap[MOYCORE_UI_SNAP_X];
            in[MOY_UI_IN_Y] = snap[MOYCORE_UI_SNAP_Y];
            in[MOY_UI_IN_PTR] = snap[MOYCORE_UI_SNAP_PTR];
            in[MOY_UI_IN_KEY] = snap[MOYCORE_UI_SNAP_KEY];
        }
        in[MOY_UI_IN_REP] = s_rep;
        in[MOY_UI_IN_KEYREP] = s_keyrep;
        s_rep = s_keyrep = 0;
        in[MOY_UI_IN_DT] = moycore_ui_dt_take();   // the ticks' clock since the last frame
        // an app's frame is its content_rect() (the snapshot's room), a
        // game's the whole canvas
        moy_ui_box_t surf = s_ctx->surface;
        if (c != NULL) {
            surf.x = surf.y = 0;
            surf.w = (int16_t)c->w;
            surf.h = (int16_t)c->h;
            if (snap != NULL && snap[MOYCORE_UI_SNAP_ROOM_W] > 0
                && snap[MOYCORE_UI_SNAP_ROOM_H] > 0) {
                int x = snap[MOYCORE_UI_SNAP_ROOM_X], y = snap[MOYCORE_UI_SNAP_ROOM_Y];
                int x1 = x + snap[MOYCORE_UI_SNAP_ROOM_W], y1 = y + snap[MOYCORE_UI_SNAP_ROOM_H];
                x = x < 0 ? 0 : x;
                y = y < 0 ? 0 : y;
                x1 = x1 > c->w ? c->w : x1;
                y1 = y1 > c->h ? c->h : y1;
                if (x1 > x && y1 > y) {
                    surf.x = (int16_t)x;
                    surf.y = (int16_t)y;
                    surf.w = (int16_t)(x1 - x);
                    surf.h = (int16_t)(y1 - y);
                }
            }
        }
        moy_ui_api_begin(s_ctx, in, surf, flags, face_fs(run_face()));
        if (s_paste_n > 0) {
            moy_ui_api_paste(s_ctx, s_paste, s_paste_n);
            s_paste_n = 0;
        }
        s_tick = tick;
    }
    return s_ctx;
}

void moy_ui_run_steps(int32_t rep, int32_t keyrep) {
    s_rep = rep;
    s_keyrep = keyrep;
}

void moy_ui_run_paste(const uint8_t *text, size_t n) {
    if (n > sizeof s_paste) {
        n = sizeof s_paste;         // moy_ui_api_paste keeps whole characters
    }
    memcpy(s_paste, text, n);
    s_paste_n = n;
}

int moy_ui_run_back(int take) {
    uint32_t tick = 0, serial = 0;
    (void)moycore_ui_snap(&tick, &serial);
    return s_ctx != NULL && s_live && serial == s_serial ? moy_ui_api_back(s_ctx, take) : 0;
}

int moy_ui_run_cancel(int take) {
    uint32_t tick = 0, serial = 0;
    (void)moycore_ui_snap(&tick, &serial);
    return s_ctx != NULL && s_live && serial == s_serial ? moy_ui_api_cancel(s_ctx, take) : 0;
}

// The run's context while the run that drew in it is the one open, else NULL
// (a native or WASI app draws with a toolkit of its own, and no run is open).
static moy_ui_ctx_t *run_ctx_now(void) {
    uint32_t tick = 0, serial = 0;
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    return snap != NULL && s_ctx != NULL && s_live && serial == s_serial ? s_ctx : NULL;
}

int moy_ui_run_bar_lent(const char **title, int16_t *slots, int *n, uint32_t *off,
                        const char **chip) {
    moy_ui_ctx_t *c = run_ctx_now();
    int at = -1;
    if (c == NULL || !moy_ui_api_bar_lent(c, title, slots, n, off)) {
        return 0;
    }
    *chip = moy_ui_api_bar_chip(c, &at);
    return 1;
}

void moy_ui_run_bar_tap(int i) {
    moy_ui_ctx_t *c = run_ctx_now();
    if (c != NULL) {
        moy_ui_api_bar_tap(c, i);
    }
}

// What shows of the table's part open now (moy_ui_run_table_part), x0, y0,
// x1, y1 on the canvas: every verb drawn while it is open draws inside it,
// as the canvas's own verbs do.
static int32_t s_tp_in[4];

static void run_in_part(run_t *r, const moy_ui_ctx_t *c) {
    if (c->tp_open) {
        memcpy(&r->row.st[MOY_ST_CX0], s_tp_in, sizeof(s_tp_in));
    }
}

// A row's drawing opens the sink over `target` and the run's context.
#define OPEN(target)                                    \
    run_t r;                                            \
    moy_canvas *t_ = (moy_canvas *)(target);            \
    if (t_ == NULL) {                                   \
        return -MOY_UI_STALE;                           \
    }                                                   \
    int rc_ = open_run(&r, t_);                         \
    if (rc_ != 0) {                                     \
        return rc_;                                     \
    }                                                   \
    moy_ui_ctx_t *c = run_ctx(t_);                      \
    if (c == NULL) {                                    \
        return close_run(&r, MOY_UI_NOMEM);             \
    }                                                   \
    run_in_part(&r, c)

int moy_ui_run_page(void *target, moy_ui_span_t title, const moy_ui_over_t *o, int32_t out[4]) {
    OPEN(target);
    moy_ui_box_t b;
    moy_ui_api_page(c, &r.s, title, o, &b);
    out[0] = b.x;
    out[1] = b.y;
    out[2] = b.w;
    out[3] = b.h;
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_label(void *target, moy_ui_span_t text, const moy_ui_over_t *o) {
    OPEN(target);
    moy_ui_api_label(c, &r.s, text, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_button(void *target, moy_ui_span_t label, int kind, const moy_ui_over_t *o,
                      int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_button(c, &r.s, label, kind, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_toggle(void *target, moy_ui_span_t label, int on, const moy_ui_over_t *o,
                      int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_toggle(c, &r.s, label, on, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_slider(void *target, moy_ui_span_t label, int32_t v, int32_t lo, int32_t hi,
                      const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_slider(c, &r.s, label, v, lo, hi, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_list(void *target, moy_ui_span_t label, const uint8_t *items, size_t n,
                    int32_t cur, const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_list(c, &r.s, label, items, n, cur, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_part(void *target, moy_ui_span_t id, int w, int h, const moy_ui_over_t *o,
                    int32_t out[5]) {
    OPEN(target);
    moy_ui_api_part(c, &r.s, id, w, h, o, out);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_part_at(void *target, int32_t out[2]) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_part_at(c, out) : 0;
}

int moy_ui_run_split(void *target, moy_ui_span_t id, int32_t *sizes, int n, const int32_t *mins,
                     const int32_t *opt, int32_t *out, int32_t *cur) {
    OPEN(target);
    *cur = moy_ui_api_split(c, &r.s, id, sizes, n, mins, opt, out);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_focus(void *target, moy_ui_span_t id, char *buf, size_t cap) {
    moy_ui_ctx_t *c = run_ctx(target);
    if (c == NULL) {
        if (cap) {
            buf[0] = 0;
        }
        return -1;
    }
    return moy_ui_api_focus(c, id, buf, cap);
}

int32_t moy_ui_run_table_dist(void *target) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_table_dist(c) : 0;
}

int32_t moy_ui_run_table_floor(void *target, int32_t out[4]) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_table_floor(c, out) : 0;
}

int moy_ui_run_held(void *target, moy_ui_span_t id, int32_t key) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_held(c, id, key) : 0;
}

int moy_ui_run_held_last(void *target, moy_ui_span_t id, int32_t key) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_held_last(c, id, key) : 0;
}

int moy_ui_run_held_all(void *target) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_held_all(c) : 0;
}

int32_t moy_ui_run_slots(void *target, int32_t cap, int32_t key) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_slots(c, cap, key) : 0;
}

int32_t moy_ui_run_slot(void *target, moy_ui_span_t name, int32_t gen, int mint) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_slot(c, name, gen, mint) : -1;
}

int32_t moy_ui_run_slot_next(void *target) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_slot_next(c) : -1;
}

int moy_ui_run_typing(void) {
    uint32_t tick = 0, serial = 0;
    (void)moycore_ui_snap(&tick, &serial);
    return s_ctx != NULL && s_live && serial == s_serial ? moy_ui_api_typing(s_ctx) : 0;
}

int moy_ui_run_keys_taken(void *target) {
    moy_ui_ctx_t *c = run_ctx(target);
    return c != NULL ? moy_ui_api_keys_taken(c) : 0;
}

void moy_ui_run_at(void *target, int x, int y, int w, int h) {
    moy_ui_ctx_t *c = run_ctx(target);
    if (c != NULL) {
        moy_ui_api_at(c, x, y, w, h);
    }
}

int moy_ui_run_group(void *target, moy_ui_span_t caption, const moy_ui_over_t *o) {
    OPEN(target);
    moy_ui_api_group(c, &r.s, caption, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_value(void *target, int kind, moy_ui_span_t label, moy_ui_span_t value,
                     const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_value(c, &r.s, kind, label, value, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_choice(void *target, int kind, moy_ui_span_t label, const uint8_t *opts, size_t n,
                      int32_t cur, const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_choice(c, &r.s, kind, label, opts, n, cur, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_hints(void *target, const uint8_t *items, size_t n, const moy_ui_over_t *o) {
    OPEN(target);
    moy_ui_api_hints(c, &r.s, items, n, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_icon(void *target, moy_ui_span_t name, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_icon(c, name);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_progress(void *target, moy_ui_span_t label, int32_t v, int32_t hi,
                        const moy_ui_over_t *o) {
    OPEN(target);
    moy_ui_api_progress(c, &r.s, label, v, hi, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_table(void *target, moy_ui_span_t label, const uint8_t *cols, size_t cols_n,
                     const uint8_t *cells, size_t cells_n, int32_t cur, const moy_ui_over_t *o,
                     int32_t out[2]) {
    OPEN(target);
    moy_ui_api_table(c, &r.s, label, cols, cols_n, cells, cells_n, cur, o, out);
    return close_run(&r, MOY_UI_OK);
}

// The run's clip before the open part's, put back when it closes.
static int s_tp_on, s_tp_clip[4];

int moy_ui_run_table_part(void *target, int32_t out[8]) {
    OPEN(target);
    // the table's fades after its last part lie over the view, not the part
    r.row.st[MOY_ST_CX0] = r.row.st[MOY_ST_CY0] = 0;
    r.row.st[MOY_ST_CX1] = r.row.w;
    r.row.st[MOY_ST_CY1] = r.row.h;
    int32_t i = moy_ui_api_table_part(c, &r.s, out);
    close_run(&r, MOY_UI_OK);
    if (s_tp_on) {
        t_->clip_x0 = s_tp_clip[0];
        t_->clip_y0 = s_tp_clip[1];
        t_->clip_x1 = s_tp_clip[2];
        t_->clip_y1 = s_tp_clip[3];
        s_tp_on = 0;
    }
    if (i >= 0) {
        s_tp_clip[0] = t_->clip_x0;
        s_tp_clip[1] = t_->clip_y0;
        s_tp_clip[2] = t_->clip_x1;
        s_tp_clip[3] = t_->clip_y1;
        s_tp_on = 1;
        moy_clip(t_, out[4], out[5], out[6], out[7]);
        s_tp_in[0] = out[4];
        s_tp_in[1] = out[5];
        s_tp_in[2] = out[4] + out[6];
        s_tp_in[3] = out[5] + out[7];
    }
    return i;
}

// A verb that opens a nest: MOY_UI_DEEP where it answered MOY_UI_API_DEEP.
static int nest_rc(int32_t v) {
    return v == MOY_UI_API_DEEP ? MOY_UI_DEEP : MOY_UI_OK;
}

int moy_ui_run_scroll(void *target, moy_ui_span_t id, const moy_ui_over_t *o) {
    OPEN(target);
    return close_run(&r, nest_rc(moy_ui_api_scroll(c, &r.s, id, o)));
}

int moy_ui_run_text_view(void *target, moy_ui_span_t id, const moy_ui_lines_t *lines,
                         const int32_t *opt, const moy_ui_over_t *o, int32_t out[2]) {
    OPEN(target);
    return close_run(&r, nest_rc(moy_ui_api_text_view(c, &r.s, id, lines, opt, NULL, o, out)));
}

int moy_ui_run_field(void *target, moy_ui_span_t label, uint8_t *buf, size_t cap, size_t len,
                     const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_field(c, &r.s, label, buf, cap, len, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_dialog(void *target, moy_ui_span_t title, const uint8_t *buttons, size_t n,
                      int kind, const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    int32_t v = moy_ui_api_dialog(c, &r.s, title, buttons, n, kind, o);
    if (v == MOY_UI_API_DEEP) {
        return close_run(&r, MOY_UI_DEEP);
    }
    *out = v;
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_popover(void *target, moy_ui_span_t id, int w, const moy_ui_over_t *o,
                       int32_t *out) {
    OPEN(target);
    int v = moy_ui_api_popover(c, &r.s, id, w, o);
    if (v == MOY_UI_API_DEEP) {
        return close_run(&r, MOY_UI_DEEP);
    }
    *out = v;
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_menu(void *target, moy_ui_span_t label, const uint8_t *items, size_t n,
                    const uint8_t *icons, size_t in, const moy_ui_over_t *o, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_menu(c, &r.s, label, items, n, icons, in, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_tool(void *target, moy_ui_span_t icon, moy_ui_span_t label, const moy_ui_over_t *o,
                    int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_tool(c, &r.s, icon, label, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_rule(void *target) {
    OPEN(target);
    moy_ui_api_rule(c, &r.s);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_bubble(void *target, moy_ui_span_t text, moy_ui_span_t head, moy_ui_span_t status,
                      const int32_t *opt, int n, const moy_ui_over_t *o, int32_t out[5]) {
    OPEN(target);
    moy_ui_api_bubble(c, &r.s, text, head, status, opt, n, o, out);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_empty(void *target, moy_ui_span_t icon, moy_ui_span_t title, const uint8_t *lines,
                     size_t n, int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_empty(c, &r.s, icon, title, lines, n);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_note(void *target, moy_ui_span_t icon, const uint8_t *lines, size_t n,
                    int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_note(c, &r.s, icon, lines, n);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_bar(void *target, moy_ui_span_t title, const uint8_t *tools, size_t n, uint32_t off,
                   int32_t *out) {
    OPEN(target);
    *out = moy_ui_api_bar(c, &r.s, title, tools, n, off);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_grid(void *target, moy_ui_span_t id, int w, int h, const moy_ui_over_t *o) {
    OPEN(target);
    moy_ui_api_grid(c, &r.s, id, w, h, o);
    return close_run(&r, MOY_UI_OK);
}

int moy_ui_run_card(void *target, moy_ui_span_t title, moy_ui_span_t sub, const moy_ui_over_t *o,
                    int32_t out[5]) {
    OPEN(target);
    moy_ui_api_card(c, &r.s, title, sub, o, out);
    return close_run(&r, MOY_UI_OK);
}
