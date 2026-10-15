// The toolkit's rows for a compiled app (moy_ui_wasm.h).

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

static int open_run(run_t *r) {
    moy_ui_theme_fn theme = s_theme != NULL ? s_theme : moy_app_wasm_theme;
    moy_canvas *c = moycore_ui_target();
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

static moy_ui_box_t box(int32_t x, int32_t y, int32_t w, int32_t h) {
    moy_ui_box_t b = { (int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h };
    return b;
}

static moy_ui_span_t span(const uint8_t *p, uint32_t n) {
    moy_ui_span_t s = { p, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n) };
    return s;
}

// A buffer the app keeps its state in: in its memory, 4-byte aligned (a
// board's core faults on an unaligned word) and at least `need` bytes.
static void *state(uint8_t *p, uint32_t n, uint32_t need) {
    return p != NULL && ((uintptr_t)p & 3u) == 0u && n >= need ? p : NULL;
}

int32_t moy_ui_wasm_kind(void *e, const uint8_t *name, uint32_t n) {
    (void)e;
    return moy_ui_kind((const char *)name, n);
}

int32_t moy_ui_wasm_glyph_id(void *e, const uint8_t *name, uint32_t n) {
    (void)e;
    return moy_ui_glyph_id((const char *)name, n);
}

int32_t moy_ui_wasm_button(void *e, int32_t x, int32_t y, int32_t w, int32_t h, int32_t kind,
                           int32_t flags, int32_t interact, const uint8_t *label, uint32_t ln,
                           int32_t glyph) {
    (void)e;
    run_t r;
    int rc = open_run(&r);
    if (rc != 0) {
        return rc;
    }
    moy_ui_button_t k;
    k.label = span(label, ln);
    k.glyph = (int16_t)glyph;
    k.fs = r.s.fs;
    k.colors[0] = k.colors[1] = k.colors[2] = MOY_UI_FROM_STYLE;
    k.app = -1;
    // The app's flags are the state's (ON, HOT, DISABLED); a glyph is its id.
    flags &= MOY_UI_F_ON | MOY_UI_F_HOT | MOY_UI_F_DISABLED;
    if (glyph >= 0) {
        flags |= MOY_UI_F_GLYPH;
    }
    return close_run(&r, moy_ui_button(&r.s, box(x, y, w, h), kind, flags, interact, &k,
                                       MOY_UI_PHASE_ALL));
}

int32_t moy_ui_wasm_label(void *e, int32_t x, int32_t y, int32_t w, int32_t h,
                          const uint8_t *text, uint32_t n, int32_t ink, int32_t align) {
    (void)e;
    run_t r;
    int rc = open_run(&r);
    if (rc != 0) {
        return rc;
    }
    int got = moy_ui_label(&r.s, box(x, y, w, h), span(text, n), ink, align);
    close_run(&r, MOY_UI_OK);
    return got;
}

int32_t moy_ui_wasm_list(void *e, int32_t x, int32_t y, int32_t w, int32_t h, int32_t kind,
                         const uint8_t *items, uint32_t n, int32_t count, int32_t row_h,
                         int32_t offset, int32_t sel) {
    (void)e;
    run_t r;
    int rc = open_run(&r);
    if (rc != 0) {
        return rc;
    }
    int nm = 0;
    const int16_t *m = moy_ui_metrics(MOY_UI_K_SCROLLBAR, &nm);
    int bar_w = m != NULL && nm > MOY_UI_M_SCROLLBAR_W ? m[MOY_UI_M_SCROLLBAR_W] : 0;
    return close_run(&r, moy_ui_list(&r.s, box(x, y, w, h), kind, items, n, count, row_h,
                                     offset, sel, r.s.fs, bar_w));
}

int32_t moy_ui_wasm_hits_clear(void *e, uint8_t *buf, uint32_t n) {
    (void)e;
    int32_t *b = state(buf, n, MOY_UI_HITS_HDR * 4u);
    if (b == NULL) {
        return -MOY_UI_BAD;
    }
    moy_ui_hits_clear(b);
    return 0;
}

int32_t moy_ui_wasm_hits_add(void *e, uint8_t *buf, uint32_t n, int32_t x, int32_t y,
                             int32_t w, int32_t h, int32_t id) {
    (void)e;
    int32_t *b = state(buf, n, MOY_UI_HITS_HDR * 4u);
    if (b == NULL) {
        return -MOY_UI_BAD;
    }
    return moy_ui_hits_add(b, n / 4u, x, y, w, h, id) == MOY_UI_OK ? 0 : -MOY_UI_FULL;
}

int32_t moy_ui_wasm_hits_pump(void *e, uint8_t *buf, uint32_t n, int32_t px, int32_t py,
                              int32_t down, int32_t pointing, uint8_t *pump, uint32_t pn) {
    (void)e;
    int32_t *b = state(buf, n, MOY_UI_HITS_HDR * 4u);
    moy_ui_pump_t *p = state(pump, pn, sizeof(moy_ui_pump_t));
    if (b == NULL || p == NULL) {
        return -MOY_UI_BAD;
    }
    moy_ui_hits_pump(b, px, py, down != 0, pointing != 0, p);
    return 0;
}

int32_t moy_ui_wasm_dragtap(void *e, uint8_t *st, uint32_t n, int32_t x, int32_t y, int32_t w,
                            int32_t h, int32_t horizontal, int32_t px, int32_t py,
                            int32_t click, int32_t down, int32_t slop, int32_t content,
                            float dt_ms) {
    (void)e;
    moy_ui_wasm_drag_t *d = state(st, n, sizeof(moy_ui_wasm_drag_t));
    if (d == NULL) {
        return -MOY_UI_BAD;
    }
    int32_t view[4] = { x, y, w, h };
    return moy_ui_dragtap_frame(&d->tap, &d->scroll, view, horizontal != 0, px, py,
                                click != 0, down != 0, slop, dt_ms, &d->offset, content,
                                &d->tx, &d->ty);
}

#define FN(f) ((void *)(f))

// In roles.json's order; native/moy_app/moy_app_wasm.c's table carries each
// row's import type (tests/test_roles.py holds the two to roles.json).
static const struct { const char *name; void *fn; } NATIVES[MOY_UI_WASM_ROWS] = {
    { "ui_kind", FN(moy_ui_wasm_kind) },
    { "ui_glyph_id", FN(moy_ui_wasm_glyph_id) },
    { "ui_button", FN(moy_ui_wasm_button) },
    { "ui_label", FN(moy_ui_wasm_label) },
    { "ui_list", FN(moy_ui_wasm_list) },
    { "ui_hits_clear", FN(moy_ui_wasm_hits_clear) },
    { "ui_hits_add", FN(moy_ui_wasm_hits_add) },
    { "ui_hits_pump", FN(moy_ui_wasm_hits_pump) },
    { "ui_dragtap", FN(moy_ui_wasm_dragtap) },
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
