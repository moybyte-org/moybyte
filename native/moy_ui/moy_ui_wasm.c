// The RUN face of the public UI API and its wasm rows (moy_ui_wasm.h).

#include <stdlib.h>
#include <string.h>

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define CTX_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#define CTX_ALLOC(n) malloc(n)
#endif

#include "moy.h"
#include "moy_canvas.h"
#include "moy_theme.h"
#include "moy_ui_wasm.h"

// The RUN sink lends an RGB565 canvas as a row (a compile-time check, not a
// preprocessor one: MicroPython's qstr pass preprocesses without the build's
// definitions).
_Static_assert(sizeof(moy_pixel) == sizeof(uint16_t), "the RUN sink lends an RGB565 canvas");

// The generation the run's look was built under (0: none yet, which no live
// theme has), and the run's serial it was built for. A generation is counted
// by one console from its own start, so a later console (or run) can reach the
// same number on another theme: the look is rebuilt for each run.
static uint32_t s_gen, s_gen_serial;

// A compiled run's draw target: the module that drives a compiled run's (a
// library with no compiled run answers none).
void *moycore_ui_target(void) __attribute__((weak));

static void *wasm_target(void) {
    return moycore_ui_target != NULL ? moycore_ui_target() : NULL;
}

// The live theme's token row and its name, copied when the generation differs
// from `have`: native/moy_app/moy_app_wasm.c's. Answers the generation, 0
// while no run is bound; weak, so an image without the app ABI draws no rows.
uint32_t moy_app_wasm_theme(int32_t out[MOY_UI_TOKENS], uint32_t have, char *name,
                            uint32_t cap) __attribute__((weak));

// The shape installed when the live theme names another than the one the
// shell installed; while the shell's is this theme's, nothing is written.
static moy_ui_shape_t s_shape;

// What every drawing row opens with: the RUN sink over the run's target, lent
// as `row`, the look current, the run's draw state saved in `saved` and reset.
typedef struct {
    moy_ui_sink_t s;
    moy_canvas_row_t row;
    moy_canvas *c;
    moy_canvas saved;
} run_t;

static int open_run(run_t *r, moy_canvas *c) {
    if (c == NULL || moy_app_wasm_theme == NULL) {
        return -MOY_UI_STALE;
    }
    uint32_t tick = 0, serial = 0;
    (void)moycore_ui_snap(&tick, &serial);
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
        if (name[0] && now != NULL && strcmp(name, now->name) != 0) {
            moy_theme_shape(name, strlen(name), &s_shape);
            moy_ui_shape_use(&s_shape);
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
    r->row.st[MOY_ST_FONT_SCALE] = 1;
    // A compiled app is a cart: its colours are MOY64 (its theme imports
    // answer each role's nearest index), so both halves are the canvas's.
    memcpy(r->row.pal, c->store, MOY_CANVAS_BLOCK * sizeof(uint16_t));
    memcpy(&r->row.pal[MOY_CANVAS_BLOCK], c->store, MOY_CANVAS_BLOCK * sizeof(uint16_t));
    r->s.kind = MOY_UI_SINK_RUN;
    r->s.fs = 1;
    r->s.look = MOY_UI_LOOK_RUN;
    r->s.dst = &r->row;
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

static moy_ui_ctx_t *run_ctx(const void *target) {
    const moy_canvas *c = (const moy_canvas *)target;
    uint32_t tick = 0, serial = 0;
    if (s_ctx == NULL && (s_ctx = CTX_ALLOC(sizeof(*s_ctx))) == NULL) {
        return NULL;
    }
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    if (!s_live || serial != s_serial) {
        moy_ui_api_reset(s_ctx);
        s_serial = serial;
        s_tick = tick - 1u;
        s_live = 1;
        (void)moycore_ui_dt_take();         // the clock starts with the run
    }
    if (tick != s_tick) {
        int32_t in[MOY_UI_IN_LEN] = {0};
        int flags = 0;
        if (snap != NULL) {
            flags = snap[MOYCORE_UI_SNAP_WINDOW] ? MOY_UI_WINDOW : 0;
            in[MOY_UI_IN_BTN] = snap[MOYCORE_UI_SNAP_BTN];
            in[MOY_UI_IN_BTNP] = snap[MOYCORE_UI_SNAP_BTNP];
            in[MOY_UI_IN_X] = snap[MOYCORE_UI_SNAP_X];
            in[MOY_UI_IN_Y] = snap[MOYCORE_UI_SNAP_Y];
            in[MOY_UI_IN_PTR] = snap[MOYCORE_UI_SNAP_PTR];
            in[MOY_UI_IN_KEY] = snap[MOYCORE_UI_SNAP_KEY];
        }
        in[MOY_UI_IN_DT] = moycore_ui_dt_take();   // the ticks' clock since the last frame
        moy_ui_box_t surf = s_ctx->surface;
        if (c != NULL) {
            surf.x = surf.y = 0;
            surf.w = (int16_t)c->w;
            surf.h = (int16_t)c->h;
        }
        moy_ui_api_begin(s_ctx, in, surf, flags, 1);
        s_tick = tick;
    }
    return s_ctx;
}

static moy_ui_span_t span(const uint8_t *p, size_t n) {
    moy_ui_span_t s = { p, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n) };
    return s;
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
    (void)0

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
    return close_run(&r, nest_rc(moy_ui_api_text_view(c, &r.s, id, lines, opt, o, out)));
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

// -- the rows ----------------------------------------------------------------------

static int over_of(moy_ui_over_t *o, const uint8_t *p, uint32_t n) {
    return moy_ui_over_block(o, p, n) == MOY_UI_OK ? 0 : -MOY_UI_BAD;
}

static void put_words(uint8_t *out, uint32_t cap, const int32_t *v, uint32_t n) {
    if (out != NULL && cap >= 4u * n) {
        memcpy(out, v, 4u * n);     // the app's buffer need not be aligned
    }
}

#define OVER(p, n)                                      \
    moy_ui_over_t o;                                    \
    if (over_of(&o, (p), (n)) != 0) {                   \
        return -MOY_UI_BAD;                             \
    }

int32_t moy_ui_wasm_page(void *e, const uint8_t *title, uint32_t tn, const uint8_t *ob,
                         uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t b[4];
    int rc = moy_ui_run_page(wasm_target(), span(title, tn), &o, b);
    if (rc == 0) {
        put_words(out, cap, b, 4);
    }
    return rc;
}

int32_t moy_ui_wasm_label(void *e, const uint8_t *text, uint32_t n, const uint8_t *ob,
                          uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_label(wasm_target(), span(text, n), &o);
}

int32_t moy_ui_wasm_button(void *e, const uint8_t *label, uint32_t n, int32_t kind,
                           const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_button(wasm_target(), span(label, n), kind, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_toggle(void *e, const uint8_t *label, uint32_t n, int32_t on_,
                           const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_toggle(wasm_target(), span(label, n), on_, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_slider(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t lo,
                           int32_t hi, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t got = v;
    int rc = moy_ui_run_slider(wasm_target(), span(label, n), v, lo, hi, &o, &got);
    return rc != 0 ? v : got;
}

int32_t moy_ui_wasm_list(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t got = cur;
    int rc = moy_ui_run_list(wasm_target(), span(label, n), items, len, cur, &o, &got);
    return rc != 0 ? cur : got;
}

int32_t moy_ui_wasm_part(void *e, const uint8_t *id, uint32_t n, int32_t w, int32_t h,
                         const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[5];
    int rc = moy_ui_run_part(wasm_target(), span(id, n), w, h, &o, v);
    if (rc == 0) {
        put_words(out, cap, v, 5);
    }
    return rc;
}

int32_t moy_ui_wasm_focus(void *e, const uint8_t *id, uint32_t n, uint8_t *buf, uint32_t cap) {
    (void)e;
    moy_ui_span_t s = span(n ? id : NULL, n);
    int len = moy_ui_run_focus(wasm_target(), s, (char *)buf, cap);
    return len < 0 ? 0 : len;
}

int32_t moy_ui_wasm_keys_taken(void *e) {
    (void)e;
    return moy_ui_run_keys_taken(wasm_target());
}

int32_t moy_ui_wasm_at(void *e, int32_t x, int32_t y, int32_t w, int32_t h) {
    (void)e;
    moy_ui_run_at(wasm_target(), x, y, w, h);
    return 0;
}

int32_t moy_ui_wasm_group(void *e, const uint8_t *caption, uint32_t n, const uint8_t *ob,
                          uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_group(wasm_target(), span(caption, n), &o);
}

static int32_t value_row(int kind, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *ob, uint32_t on) {
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_value(wasm_target(), kind, span(label, n), span(value, vn), &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_info(void *e, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *ob, uint32_t on) {
    (void)e;
    return value_row(MOY_UI_V_INFO, label, n, value, vn, ob, on);
}

int32_t moy_ui_wasm_link(void *e, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *ob, uint32_t on) {
    (void)e;
    return value_row(MOY_UI_V_LINK, label, n, value, vn, ob, on);
}

static int32_t choice_row(int kind, const uint8_t *label, uint32_t n, const uint8_t *opts,
                          uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    OVER(ob, on);
    int32_t got = cur;
    int rc = moy_ui_run_choice(wasm_target(), kind, span(label, n), opts, len, cur, &o, &got);
    return rc != 0 ? cur : got;
}

int32_t moy_ui_wasm_cycle(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                          uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    return choice_row(MOY_UI_V_CYCLE, label, n, opts, len, cur, ob, on);
}

int32_t moy_ui_wasm_seg(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                        uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    return choice_row(MOY_UI_V_SEG, label, n, opts, len, cur, ob, on);
}

int32_t moy_ui_wasm_swatches(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                             uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    return choice_row(MOY_UI_V_SWATCH, label, n, opts, len, cur, ob, on);
}

int32_t moy_ui_wasm_tabs(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                         uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    return choice_row(MOY_UI_V_TABS, label, n, opts, len, cur, ob, on);
}

int32_t moy_ui_wasm_grid(void *e, const uint8_t *id, uint32_t n, int32_t w, int32_t h,
                         const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_grid(wasm_target(), span(id, n), w, h, &o);
}

int32_t moy_ui_wasm_card(void *e, const uint8_t *title, uint32_t n, const uint8_t *sub,
                         uint32_t sn, const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[5] = {0, 0, 0, 0, 0};
    int rc = moy_ui_run_card(wasm_target(), span(title, n), span(sub, sn), &o, v);
    if (rc == 0) {
        put_words(out, cap, v, 5);
    }
    return rc;
}

// ui_text_view's lines: NUL-separated text at `p`, found by walking on from
// the line found last (a view asks for its lines in order).
typedef struct {
    const uint8_t *p;
    uint32_t n, at;             // the text's bytes; where line `line` starts
    int32_t line;
} wasm_lines_t;
static wasm_lines_t s_wl;

static const uint8_t *wasm_line(void *src, int32_t i, size_t *n) {
    wasm_lines_t *w = (wasm_lines_t *)src;
    if (i < w->line) {
        w->line = 0;
        w->at = 0;
    }
    while (w->line < i && w->at < w->n) {
        while (w->at < w->n && w->p[w->at] != 0) {
            w->at++;
        }
        w->at++;
        w->line++;
    }
    if (w->line != i || w->at > w->n) {
        *n = 0;
        return NULL;
    }
    uint32_t e = w->at;
    while (e < w->n && w->p[e] != 0) {
        e++;
    }
    *n = e - w->at;
    return w->p + w->at;
}

int32_t moy_ui_wasm_table_part(void *e, uint8_t *out, uint32_t cap) {
    (void)e;
    int32_t v[8];
    int32_t i = moy_ui_run_table_part(wasm_target(), v);
    if (i >= 0) {
        put_words(out, cap, v, 4);
    }
    return i;
}

int32_t moy_ui_wasm_text_view(void *e, const uint8_t *id, uint32_t n, const uint8_t *lines,
                              uint32_t ln, const uint8_t *opt, uint32_t optn, const uint8_t *ob,
                              uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t words[MOY_UI_TXO_LEN] = {0, 0, MOY_UI_SYN_SGR, 0, 0, -1, -1};
    for (uint32_t k = 0; k < MOY_UI_TXO_LEN && 4 * k + 4 <= optn; k++) {
        const uint8_t *b = opt + 4 * k;
        words[k] = (int32_t)((uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16)
                             | ((uint32_t)b[3] << 24));
    }
    int32_t count = 0;
    for (uint32_t k = 0; k < ln; k++) {
        count += lines[k] == 0;
    }
    count += ln > 0 && lines[ln - 1] != 0;     // a last line with no NUL after it
    s_wl.p = lines;
    s_wl.n = ln;
    s_wl.at = 0;
    s_wl.line = 0;
    moy_ui_lines_t L = {&s_wl, count, wasm_line};
    int32_t v[2] = {0, 0};
    int rc = moy_ui_run_text_view(wasm_target(), span(id, n), &L, words, &o, v);
    put_words(out, cap, v, 2);
    return rc;
}

int32_t moy_ui_wasm_hints(void *e, const uint8_t *items, uint32_t n, const uint8_t *ob,
                          uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_hints(wasm_target(), items, n, &o);
}

int32_t moy_ui_wasm_icon(void *e, const uint8_t *name, uint32_t n, const uint8_t *ob,
                         uint32_t on) {
    (void)e;
    OVER(ob, on);
    (void)o;
    int32_t v = 0;
    int rc = moy_ui_run_icon(wasm_target(), span(name, n), &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_progress(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t hi,
                             const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_progress(wasm_target(), span(label, n), v, hi, &o);
}

int32_t moy_ui_wasm_table(void *e, const uint8_t *label, uint32_t n, const uint8_t *cols,
                          uint32_t cn, const uint8_t *cells, uint32_t en, int32_t cur,
                          const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[2] = {cur, 0};
    int rc = moy_ui_run_table(wasm_target(), span(label, n), cols, cn, cells, en, cur, &o, v);
    put_words(out, cap, v, 2);
    return rc;
}

int32_t moy_ui_wasm_scroll(void *e, const uint8_t *id, uint32_t n, const uint8_t *ob,
                           uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_scroll(wasm_target(), span(id, n), &o);
}

int32_t moy_ui_wasm_field(void *e, const uint8_t *label, uint32_t n, uint8_t *buf, uint32_t cap,
                          int32_t len, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = len;
    int rc = moy_ui_run_field(wasm_target(), span(label, n), buf, cap,
                              len < 0 ? 0u : (size_t)len, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_dialog(void *e, const uint8_t *title, uint32_t n, const uint8_t *buttons,
                           uint32_t bn, int32_t kind, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = -1;
    int rc = moy_ui_run_dialog(wasm_target(), span(title, n), buttons, bn, kind, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_popover(void *e, const uint8_t *id, uint32_t n, int32_t w, const uint8_t *ob,
                            uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_popover(wasm_target(), span(id, n), w, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_menu(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, const uint8_t *icons, uint32_t ilen, const uint8_t *ob,
                         uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = -1;
    int rc = moy_ui_run_menu(wasm_target(), span(label, n), items, len, icons, ilen, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_tool(void *e, const uint8_t *icon, uint32_t in, const uint8_t *label,
                         uint32_t n, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_tool(wasm_target(), span(icon, in), span(label, n), &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_bar(void *e, const uint8_t *title, uint32_t n, const uint8_t *tools,
                        uint32_t len, int32_t off) {
    (void)e;
    int32_t v = -1;
    int rc = moy_ui_run_bar(wasm_target(), span(title, n), tools, len, (uint32_t)off, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_rule(void *e) {
    (void)e;
    return moy_ui_run_rule(wasm_target());
}

#define FN(f) ((void *)(f))

// In roles.json's order; native/moy_app/moy_app_wasm.c's table carries each
// row's import type (tests/test_roles.py holds the two to roles.json).
static const struct { const char *name; void *fn; } NATIVES[MOY_UI_WASM_ROWS] = {
    { "ui_page", FN(moy_ui_wasm_page) },
    { "ui_label", FN(moy_ui_wasm_label) },
    { "ui_button", FN(moy_ui_wasm_button) },
    { "ui_toggle", FN(moy_ui_wasm_toggle) },
    { "ui_slider", FN(moy_ui_wasm_slider) },
    { "ui_list", FN(moy_ui_wasm_list) },
    { "ui_part", FN(moy_ui_wasm_part) },
    { "ui_focus", FN(moy_ui_wasm_focus) },
    { "ui_keys_taken", FN(moy_ui_wasm_keys_taken) },
    { "ui_at", FN(moy_ui_wasm_at) },
    { "ui_group", FN(moy_ui_wasm_group) },
    { "ui_info", FN(moy_ui_wasm_info) },
    { "ui_link", FN(moy_ui_wasm_link) },
    { "ui_cycle", FN(moy_ui_wasm_cycle) },
    { "ui_seg", FN(moy_ui_wasm_seg) },
    { "ui_swatches", FN(moy_ui_wasm_swatches) },
    { "ui_hints", FN(moy_ui_wasm_hints) },
    { "ui_icon", FN(moy_ui_wasm_icon) },
    { "ui_progress", FN(moy_ui_wasm_progress) },
    { "ui_table", FN(moy_ui_wasm_table) },
    { "ui_scroll", FN(moy_ui_wasm_scroll) },
    { "ui_field", FN(moy_ui_wasm_field) },
    { "ui_dialog", FN(moy_ui_wasm_dialog) },
    { "ui_tabs", FN(moy_ui_wasm_tabs) },
    { "ui_grid", FN(moy_ui_wasm_grid) },
    { "ui_card", FN(moy_ui_wasm_card) },
    { "ui_popover", FN(moy_ui_wasm_popover) },
    { "ui_menu", FN(moy_ui_wasm_menu) },
    { "ui_tool", FN(moy_ui_wasm_tool) },
    { "ui_bar", FN(moy_ui_wasm_bar) },
    { "ui_rule", FN(moy_ui_wasm_rule) },
    { "ui_text_view", FN(moy_ui_wasm_text_view) },
    { "ui_table_part", FN(moy_ui_wasm_table_part) },
};

void *moy_ui_wasm_native(const char *name) {
    for (int i = 0; i < MOY_UI_WASM_ROWS; i++) {
        if (strcmp(NATIVES[i].name, name) == 0) {
            return NATIVES[i].fn;
        }
    }
    return NULL;
}

const char *moy_ui_wasm_name(int i) {
    return i >= 0 && i < MOY_UI_WASM_ROWS ? NATIVES[i].name : NULL;
}
