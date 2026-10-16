// The RUN face of the public UI API and its wasm rows (moy_ui_wasm.h).

#include <string.h>

#include "moy.h"
#include "moy_canvas.h"
#include "moy_theme.h"
#include "moy_ui_wasm.h"

// The RUN sink lends an RGB565 canvas as a row (a compile-time check, not a
// preprocessor one: MicroPython's qstr pass preprocesses without the build's
// definitions).
_Static_assert(sizeof(moy_pixel) == sizeof(uint16_t), "the RUN sink lends an RGB565 canvas");

// The generation the run's look was built under (0: none yet, which no live
// theme has).
static uint32_t s_gen;
static moy_ui_theme_fn s_theme;

void moy_ui_wasm_theme_from(moy_ui_theme_fn fn) {
    s_theme = fn;
    s_gen = 0;
}

// A compiled run's draw target: the module that drives a compiled run's (a
// library with no compiled run answers none).
void *moycore_ui_target(void) __attribute__((weak));

static void *wasm_target(void) {
    return moycore_ui_target != NULL ? moycore_ui_target() : NULL;
}

uint32_t moy_app_wasm_theme(int32_t out[MOY_UI_TOKENS], uint32_t have, char *name,
                            uint32_t cap) __attribute__((weak));

// The shape a library of its own (the host's) installs to draw as the shell
// does; on an image the shell's is already this theme's, and nothing is
// written.
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
    moy_ui_theme_fn theme = s_theme != NULL ? s_theme : moy_app_wasm_theme;
    if (c == NULL || theme == NULL) {
        return -MOY_UI_STALE;
    }
    int32_t tok[MOY_UI_TOKENS];
    char name[MOY_UI_SHAPE_NAME];
    uint32_t gen = theme(tok, s_gen, name, sizeof(name));
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
// tick, over the run's snapshot, and reset when a new run opens.

static moy_ui_ctx_t s_ctx;
static uint32_t s_tick, s_serial;
static int s_live;

static moy_ui_ctx_t *run_ctx(const void *target) {
    const moy_canvas *c = (const moy_canvas *)target;
    uint32_t tick = 0, serial = 0;
    const int32_t *snap = moycore_ui_snap(&tick, &serial);
    if (!s_live || serial != s_serial) {
        moy_ui_api_reset(&s_ctx);
        s_serial = serial;
        s_tick = tick - 1u;
        s_live = 1;
    }
    if (tick != s_tick) {
        int32_t in[MOY_UI_IN_LEN] = {0};
        if (snap != NULL) {
            in[MOY_UI_IN_BTN] = snap[MOYCORE_UI_SNAP_BTN];
            in[MOY_UI_IN_BTNP] = snap[MOYCORE_UI_SNAP_BTNP];
            in[MOY_UI_IN_X] = snap[MOYCORE_UI_SNAP_X];
            in[MOY_UI_IN_Y] = snap[MOYCORE_UI_SNAP_Y];
            in[MOY_UI_IN_PTR] = snap[MOYCORE_UI_SNAP_PTR];
            in[MOY_UI_IN_KEY] = snap[MOYCORE_UI_SNAP_KEY];
        }
        moy_ui_box_t surf = s_ctx.surface;
        if (c != NULL) {
            surf.x = surf.y = 0;
            surf.w = (int16_t)c->w;
            surf.h = (int16_t)c->h;
        }
        moy_ui_api_begin(&s_ctx, in, surf, 0, 1);
        s_tick = tick;
    }
    return &s_ctx;
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
    moy_ui_ctx_t *c = run_ctx(t_)

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
    return moy_ui_api_focus(run_ctx(target), id, buf, cap);
}

int moy_ui_run_keys_taken(void *target) {
    return moy_ui_api_keys_taken(run_ctx(target));
}

void moy_ui_run_at(void *target, int x, int y, int w, int h) {
    moy_ui_api_at(run_ctx(target), x, y, w, h);
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
