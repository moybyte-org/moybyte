// The public UI API's wasm rows (moy_ui_wasm.h): each a compiled app's
// `moybyte.app` import, decoded onto the RUN face (moy_ui_run.c).

#include <string.h>

#include "moy_ui_wasm.h"

// A compiled run's draw target: the module that drives a compiled run's (a
// library with no compiled run answers none).
#pragma weak moycore_ui_target

static void *wasm_target(void) {
    return moycore_ui_target != NULL ? moycore_ui_target() : NULL;
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
    int rc = moy_ui_run_page(wasm_target(), moy_ui_span_of(title, tn), &o, b);
    if (rc == 0) {
        put_words(out, cap, b, 4);
    }
    return rc;
}

int32_t moy_ui_wasm_label(void *e, const uint8_t *text, uint32_t n, const uint8_t *ob,
                          uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_label(wasm_target(), moy_ui_span_of(text, n), &o);
}

int32_t moy_ui_wasm_button(void *e, const uint8_t *label, uint32_t n, int32_t kind,
                           const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_button(wasm_target(), moy_ui_span_of(label, n), kind, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_toggle(void *e, const uint8_t *label, uint32_t n, int32_t on_,
                           const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_toggle(wasm_target(), moy_ui_span_of(label, n), on_, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_slider(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t lo,
                           int32_t hi, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t got = v;
    int rc = moy_ui_run_slider(wasm_target(), moy_ui_span_of(label, n), v, lo, hi, &o, &got);
    return rc != 0 ? v : got;
}

int32_t moy_ui_wasm_list(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, int32_t cur, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t got = cur;
    int rc = moy_ui_run_list(wasm_target(), moy_ui_span_of(label, n), items, len, cur, &o, &got);
    return rc != 0 ? cur : got;
}

int32_t moy_ui_wasm_part(void *e, const uint8_t *id, uint32_t n, int32_t w, int32_t h,
                         const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[5];
    int rc = moy_ui_run_part(wasm_target(), moy_ui_span_of(id, n), w, h, &o, v);
    if (rc == 0) {
        put_words(out, cap, v, 5);
    }
    return rc;
}

int32_t moy_ui_wasm_part_at(void *e, uint8_t *out, uint32_t cap) {
    (void)e;
    int32_t v[2];
    if (!moy_ui_run_part_at(wasm_target(), v)) {
        return 0;
    }
    put_words(out, cap, v, 2);
    return 1;
}

int32_t moy_ui_wasm_split(void *e, const uint8_t *id, uint32_t n, uint8_t *sizes, uint32_t sn,
                          const uint8_t *mins, uint32_t mn, const uint8_t *opt, uint32_t on,
                          uint8_t *out, uint32_t cap) {
    (void)e;
    int k = (int)(sn / 4);
    if (k < 1 || k > MOY_UI_SPLIT_MAX || (mn != 0 && mn < sn)
        || cap < 4u * (uint32_t)(4 * k + MOY_UI_SPLIT_OUT)) {
        return -0x100 - MOY_UI_BAD;
    }
    int32_t sz[MOY_UI_SPLIT_MAX], mi[MOY_UI_SPLIT_MAX], o[MOY_UI_SPO_N];
    int32_t w[4 * MOY_UI_SPLIT_MAX + MOY_UI_SPLIT_OUT];
    memcpy(sz, sizes, 4u * (uint32_t)k);
    if (mn != 0) {
        memcpy(mi, mins, 4u * (uint32_t)k);
    }
    memset(o, 0, sizeof(o));
    memcpy(o, opt, on < sizeof(o) ? on : sizeof(o));
    int32_t cur = -1;
    int rc = moy_ui_run_split(wasm_target(), moy_ui_span_of(id, n), sz, k, mn != 0 ? mi : NULL, o, w, &cur);
    if (rc != 0) {
        return -0x100 + rc;
    }
    memcpy(sizes, sz, 4u * (uint32_t)k);
    put_words(out, cap, w, (uint32_t)(4 * k + MOY_UI_SPLIT_OUT));
    return cur;
}

int32_t moy_ui_wasm_focus(void *e, const uint8_t *id, uint32_t n, uint8_t *buf, uint32_t cap) {
    (void)e;
    moy_ui_span_t s = moy_ui_span_of(n ? id : NULL, n);
    int len = moy_ui_run_focus(wasm_target(), s, (char *)buf, cap);
    return len < 0 ? 0 : len;
}

int32_t moy_ui_wasm_table_floor(void *e, uint8_t *out, uint32_t cap) {
    (void)e;
    int32_t v[4];
    int32_t r = moy_ui_run_table_floor(wasm_target(), v);
    if (r > 0) {
        put_words(out, cap, v, 4);
    }
    return r;
}

int32_t moy_ui_wasm_held(void *e, const uint8_t *id, uint32_t n, int32_t key) {
    (void)e;
    return moy_ui_run_held(wasm_target(), moy_ui_span_of(n ? id : NULL, n), key);
}

int32_t moy_ui_wasm_held_last(void *e, const uint8_t *id, uint32_t n, int32_t key) {
    (void)e;
    return moy_ui_run_held_last(wasm_target(), moy_ui_span_of(n ? id : NULL, n), key);
}

int32_t moy_ui_wasm_held_all(void *e) {
    (void)e;
    return moy_ui_run_held_all(wasm_target());
}

int32_t moy_ui_wasm_slots(void *e, int32_t cap, int32_t key) {
    (void)e;
    return moy_ui_run_slots(wasm_target(), cap, key);
}

int32_t moy_ui_wasm_slot(void *e, const uint8_t *name, uint32_t n, int32_t gen, int32_t mint) {
    (void)e;
    return moy_ui_run_slot(wasm_target(), moy_ui_span_of(n ? name : NULL, n), gen, mint != 0);
}

int32_t moy_ui_wasm_slot_next(void *e) {
    (void)e;
    return moy_ui_run_slot_next(wasm_target());
}

int32_t moy_ui_wasm_table_dist(void *e) {
    (void)e;
    return moy_ui_run_table_dist(wasm_target());
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
    return moy_ui_run_group(wasm_target(), moy_ui_span_of(caption, n), &o);
}

static int32_t value_row(int kind, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *ob, uint32_t on) {
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_value(wasm_target(), kind, moy_ui_span_of(label, n), moy_ui_span_of(value, vn), &o, &v);
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
    int rc = moy_ui_run_choice(wasm_target(), kind, moy_ui_span_of(label, n), opts, len, cur, &o, &got);
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
    return moy_ui_run_grid(wasm_target(), moy_ui_span_of(id, n), w, h, &o);
}

int32_t moy_ui_wasm_card(void *e, const uint8_t *title, uint32_t n, const uint8_t *sub,
                         uint32_t sn, const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[5] = {0, 0, 0, 0, 0};
    int rc = moy_ui_run_card(wasm_target(), moy_ui_span_of(title, n), moy_ui_span_of(sub, sn), &o, v);
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
    // a compiled app passes the public verb's options (rows .. caret); the
    // editor's marks after them are the shell's
    int32_t words[MOY_UI_TXO_LEN] = MOY_UI_TXO_DEFAULTS;
    for (uint32_t k = 0; k < MOY_UI_TXO_SEL_R0 && 4 * k + 4 <= optn; k++) {
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
    int rc = moy_ui_run_text_view(wasm_target(), moy_ui_span_of(id, n), &L, words, &o, v);
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
    int rc = moy_ui_run_icon(wasm_target(), moy_ui_span_of(name, n), &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_progress(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t hi,
                             const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_progress(wasm_target(), moy_ui_span_of(label, n), v, hi, &o);
}

int32_t moy_ui_wasm_table(void *e, const uint8_t *label, uint32_t n, const uint8_t *cols,
                          uint32_t cn, const uint8_t *cells, uint32_t en, int32_t cur,
                          const uint8_t *ob, uint32_t on, uint8_t *out, uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t v[2] = {cur, 0};
    int rc = moy_ui_run_table(wasm_target(), moy_ui_span_of(label, n), cols, cn, cells, en, cur, &o, v);
    put_words(out, cap, v, 2);
    return rc;
}

int32_t moy_ui_wasm_scroll(void *e, const uint8_t *id, uint32_t n, const uint8_t *ob,
                           uint32_t on) {
    (void)e;
    OVER(ob, on);
    return moy_ui_run_scroll(wasm_target(), moy_ui_span_of(id, n), &o);
}

int32_t moy_ui_wasm_field(void *e, const uint8_t *label, uint32_t n, uint8_t *buf, uint32_t cap,
                          int32_t len, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = len;
    int rc = moy_ui_run_field(wasm_target(), moy_ui_span_of(label, n), buf, cap,
                              len < 0 ? 0u : (size_t)len, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_dialog(void *e, const uint8_t *title, uint32_t n, const uint8_t *buttons,
                           uint32_t bn, int32_t kind, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = -1;
    int rc = moy_ui_run_dialog(wasm_target(), moy_ui_span_of(title, n), buttons, bn, kind, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_popover(void *e, const uint8_t *id, uint32_t n, int32_t w, const uint8_t *ob,
                            uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_popover(wasm_target(), moy_ui_span_of(id, n), w, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_menu(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, const uint8_t *icons, uint32_t ilen, const uint8_t *ob,
                         uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = -1;
    int rc = moy_ui_run_menu(wasm_target(), moy_ui_span_of(label, n), items, len, icons, ilen, &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_tool(void *e, const uint8_t *icon, uint32_t in, const uint8_t *label,
                         uint32_t n, const uint8_t *ob, uint32_t on) {
    (void)e;
    OVER(ob, on);
    int32_t v = 0;
    int rc = moy_ui_run_tool(wasm_target(), moy_ui_span_of(icon, in), moy_ui_span_of(label, n), &o, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_bar(void *e, const uint8_t *title, uint32_t n, const uint8_t *tools,
                        uint32_t len, int32_t off) {
    (void)e;
    int32_t v = -1;
    int rc = moy_ui_run_bar(wasm_target(), moy_ui_span_of(title, n), tools, len, (uint32_t)off, &v);
    return rc != 0 ? rc : v;
}

int32_t moy_ui_wasm_rule(void *e) {
    (void)e;
    return moy_ui_run_rule(wasm_target());
}

int32_t moy_ui_wasm_bubble(void *e, const uint8_t *text, uint32_t n, const uint8_t *head,
                           uint32_t hn, const uint8_t *status, uint32_t sn, const uint8_t *opt,
                           uint32_t optn, const uint8_t *ob, uint32_t on, uint8_t *out,
                           uint32_t cap) {
    (void)e;
    OVER(ob, on);
    int32_t words[MOY_UI_BBO_WORDS];
    int k = 0;
    for (; k < MOY_UI_BBO_WORDS && 4u * (uint32_t)k + 4u <= optn; k++) {
        const uint8_t *b = opt + 4 * k;
        words[k] = (int32_t)((uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16)
                             | ((uint32_t)b[3] << 24));
    }
    int32_t v[5] = {0, 0, 0, 0, 0};
    int rc = moy_ui_run_bubble(wasm_target(), moy_ui_span_of(text, n), moy_ui_span_of(head, hn), moy_ui_span_of(status, sn),
                               words, k, &o, v);
    if (rc == 0) {
        put_words(out, cap, v, 5);
    }
    return rc;
}

int32_t moy_ui_wasm_empty(void *e, const uint8_t *icon, uint32_t in, const uint8_t *title,
                          uint32_t n, const uint8_t *lines, uint32_t len) {
    (void)e;
    int32_t y = 0;
    int rc = moy_ui_run_empty(wasm_target(), moy_ui_span_of(icon, in), moy_ui_span_of(title, n), lines, len, &y);
    return rc == 0 ? y : rc;
}

int32_t moy_ui_wasm_note(void *e, const uint8_t *icon, uint32_t in, const uint8_t *lines,
                         uint32_t len) {
    (void)e;
    int32_t y = 0;
    int rc = moy_ui_run_note(wasm_target(), moy_ui_span_of(icon, in), lines, len, &y);
    return rc == 0 ? y : rc;
}

#define FN(f) ((void *)(f))

// By import name, in the role table's order (moy_ui_wasm_rows.h, generated
// from native/os_calls.json with each row's import type).
static const struct { const char *name; void *fn; } NATIVES[MOY_UI_WASM_ROWS] =
    MOY_UI_WASM_NATIVES;

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
