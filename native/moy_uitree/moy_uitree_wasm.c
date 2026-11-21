// The UI engine's wasm rows (moy_uitree_wasm.h): each a compiled app's
// `moybyte.app` import, over the run's one store.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy_uitree.h"
#include "moy_uitree_int.h"
#include "moy_uitree_wasm.h"
#include "moy_ui_run.h"

#if defined(__has_include) && __has_include("moy_log.h")
#include "moy_log.h"
#pragma weak moy_log_write
#define LOG(level, text, n) \
    do { if (moy_log_write != NULL) moy_log_write((level), MOY_LOG_CURRENT, (text), (n)); } while (0)
#else
#define LOG(level, text, n) ((void)(level), (void)(text), (void)(n))
#endif
enum { LOG_ERR = 3, LOG_INFO = 6 };

// The module that drives a compiled run (none in a library with no compiled run).
#pragma weak moycore_ui_target
#pragma weak moycore_ui_snap

// What a run's app keeps beside its store.
typedef struct {
    uit_t *e;
    uint32_t serial;            // the run it is of
    int live;                   // a run made it
    int err;                    // the run's first refusal (0 none), latched
    char errline[320];
    int handler;                // the action whose handler runs (handed out by next), -1 none
    int opened;                 // the visit is open: a write is traced from here on
    int hook_open;              // the `opened` hook, owed to the next `next` (-1 none)
    uint32_t frames;
    int page;                   // the page node of the build, -1 none
    uit_buf_t when;             // each action's when= source, NUL-terminated, by `when_at`
    uint32_t *when_at;
    int nwhen, cwhen;
    uit_buf_t tmp;              // the call's texts, NUL-terminated
} app_t;

static app_t s_app;

static void app_drop(app_t *a) {
    if (a->e != NULL) {
        uit_free(a->e);
    }
    uit_buf_free(&a->when);
    uit_buf_free(&a->tmp);
    UIT_FREE(a->when_at);
    memset(a, 0, sizeof *a);
}

void moy_uitree_wasm_end(void) {
    app_drop(&s_app);
}

// The run's app: made by the run's first row, dropped when another run opens.
static app_t *app(void) {
    app_t *a = &s_app;
    uint32_t tick = 0, serial = 0;
    if (moycore_ui_snap != NULL) {
        (void)moycore_ui_snap(&tick, &serial);
    }
    if (a->live && a->serial != serial) {
        app_drop(a);
    }
    if (!a->live) {
        a->e = uit_new();
        if (a->e == NULL) {
            return NULL;
        }
        a->live = 1;
        a->serial = serial;
        a->handler = -1;
        a->hook_open = -1;
        a->page = -1;
    }
    return a;
}

// -- refusals ------------------------------------------------------------------

static const char *code_of(const app_t *a, int r) {
    switch (r) {
    case UIT_E_CHECK: {
        const char *c = uit_error_code(a->e);
        return c != NULL && c[0] ? c : "E00";
    }
    case UIT_E_NAME: return "NAME";
    case UIT_E_LANG: return "LANG";
    case UIT_E_KIND: return "KIND";
    case UIT_E_NOMEM: return "NOMEM";
    case UIT_E_BLOB: return "BLOB";
    default: return "ARG";
    }
}

// A refusal of row `row`: latched, logged once, answered.
static int refuse(app_t *a, int r, const char *row, const char *where, const char *msg) {
    if (a->err == 0) {
        a->err = r;
        snprintf(a->errline, sizeof a->errline, "%s uitree_%s(%s): %s", code_of(a, r), row, where,
                 msg != NULL && msg[0] ? msg : uit_error(a->e));
        LOG(LOG_ERR, a->errline, strlen(a->errline));
    }
    return r;
}

static int check(app_t *a, int r, const char *row, const char *where) {
    return r < 0 ? refuse(a, r, row, where, NULL) : r;
}

// The row's app, or below 0: none could be made, or the run's refusal again.
#define APP(a)                                  \
    app_t *a = app();                           \
    if (a == NULL) {                            \
        return UIT_E_NOMEM;                     \
    }                                           \
    if (a->err != 0) {                          \
        return a->err;                          \
    }

// Node `n`'s key path from the page ("page/group#0/info#0") into buf.
static void key_path(const app_t *a, int n, char *buf, size_t cap) {
    int chain[16], k = 0;
    while (n >= 0 && n < a->e->nnodes && k < 16) {
        chain[k++] = n;
        uint16_t p = a->e->nodes[n].parent;
        n = p == UIT_NONE ? -1 : (int)p;
    }
    size_t at = 0;
    buf[0] = 0;
    while (k-- > 0 && at + 1 < cap) {
        int w = snprintf(buf + at, cap - at, "%s%s", at ? "/" : "", uit_str(a->e, a->e->nodes[chain[k]].key));
        at += w > 0 ? (size_t)w : 0;
    }
}

// -- texts across the line --------------------------------------------------------

// The call's texts: each put NUL-terminated into the scratch (its offset),
// read back as pointers once every one is in (a put may move the scratch).
static int put(app_t *a, const uint8_t *p, uint32_t n) {
    return uit_buf_put(&a->tmp, (const char *)p, n);
}

static const char *at(const app_t *a, int off) {
    return off < 0 ? NULL : a->tmp.p + off;
}

#define TEXTS_BEGIN(a) ((a)->tmp.n = 0)

// -- the trace ------------------------------------------------------------------

static void trace(app_t *a, const char *kind, const char *name, const char *fmt, ...) {
    char line[600];
    uint32_t ms = a->frames * 1000u / 30u;
    int n = snprintf(line, sizeof line, "[trace] %02u:%02u:%02u.%03u  %-6s  %-8s ", (unsigned)(ms / 3600000u),
                     (unsigned)(ms / 60000u % 60u), (unsigned)(ms / 1000u % 60u), (unsigned)(ms % 1000u),
                     kind, name);
    if (n < 0 || n >= (int)sizeof line) {
        return;
    }
    va_list ap;
    va_start(ap, fmt);
    int m = vsnprintf(line + n, sizeof line - (size_t)n, fmt, ap);
    va_end(ap);
    n += m < 0 ? 0 : m;
    if (n >= (int)sizeof line) {
        n = (int)sizeof line - 1;
    }
    LOG(LOG_INFO, line, (size_t)n);
}

// A write's trace: the slot's value before (`was`), the value now, and the
// handler it was made in. Nothing is traced before the first frame (the
// values the app starts with) nor where the value did not move.
static void traced_write(app_t *a, int slot, const char *was) {
    if (!a->opened) {
        return;
    }
    uit_buf_t now = {0};
    uit_slot_repr(a->e, &now, slot);
    if (now.p != NULL && strcmp(now.p, was) != 0) {
        const char *in = a->handler >= 0 ? uit_str(a->e, a->e->acts[a->handler].name) : "the app";
        trace(a, "state", uit_str(a->e, a->e->slots[slot].name), "%s -> %s   (in %s)", was, now.p, in);
    }
    uit_buf_free(&now);
}

static int slot_ok(const app_t *a, int slot) {
    return slot >= 0 && slot < a->e->nslots;
}

// The value a write moves, as the trace spells it, into `b` (a model's is its row count).
static void was_of(app_t *a, int slot, uit_buf_t *b) {
    b->n = 0;
    uit_slot_repr(a->e, b, slot);
    if (b->p == NULL) {
        uit_buf_puts(b, "");
    }
}

// -- declare -------------------------------------------------------------------

int32_t moy_uitree_wasm_state(void *x, const uint8_t *name, uint32_t name_n, int32_t type,
                              const uint8_t *values, uint32_t values_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int n = put(a, name, name_n), v = values_n ? put(a, values, values_n) : -1;
    return check(a, uit_state(a->e, at(a, n), (int)type, at(a, v), values_n), "state", at(a, n));
}

int32_t moy_uitree_wasm_model(void *x, int32_t slot, const uint8_t *names, uint32_t names_n,
                              const uint8_t *types, uint32_t types_n, int32_t key) {
    (void)x;
    APP(a);
    char where[24];
    snprintf(where, sizeof where, "slot %d", (int)slot);
    return check(a, uit_model(a->e, (int)slot, (const char *)names, names_n, types, (int)types_n, (int)key),
                 "model", where);
}

int32_t moy_uitree_wasm_row_of(void *x, int32_t slot, int32_t model) {
    (void)x;
    APP(a);
    char where[32];
    snprintf(where, sizeof where, "slot %d, model %d", (int)slot, (int)model);
    return check(a, uit_row_of(a->e, (int)slot, (int)model), "row_of", where);
}

int32_t moy_uitree_wasm_labels(void *x, const uint8_t *name, uint32_t name_n, const uint8_t *keys,
                               uint32_t keys_n, const uint8_t *words, uint32_t words_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int n = put(a, name, name_n);
    return check(a, uit_labels(a->e, at(a, n), (const char *)keys, keys_n, (const char *)words, words_n),
                 "labels", at(a, n));
}

int32_t moy_uitree_wasm_action(void *x, const uint8_t *name, uint32_t name_n, const uint8_t *label,
                               uint32_t label_n, const uint8_t *hint, uint32_t hint_n,
                               const uint8_t *when, uint32_t when_n, int32_t off, int32_t flags,
                               const uint8_t *letters, uint32_t letters_n, const uint8_t *icon,
                               uint32_t icon_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int n = put(a, name, name_n), l = put(a, label, label_n), h = put(a, hint, hint_n);
    int w = when_n ? put(a, when, when_n) : -1, k = put(a, letters, letters_n), i = put(a, icon, icon_n);
    int r = uit_action(a->e, at(a, n), at(a, l), at(a, h), at(a, w), (int)off, (int)flags, at(a, k), at(a, i));
    if (r < 0) {
        return refuse(a, r, "action", at(a, n), NULL);
    }
    if (r >= a->cwhen) {
        int c = a->cwhen ? a->cwhen * 2 : 8;
        while (c <= r) {
            c *= 2;
        }
        uint32_t *g = UIT_REALLOC(a->when_at, (size_t)c * sizeof *g);
        if (g == NULL) {
            return refuse(a, UIT_E_NOMEM, "action", at(a, n), "no memory for the action's when=");
        }
        a->when_at = g;
        a->cwhen = c;
    }
    int off_w = uit_buf_put(&a->when, w >= 0 ? at(a, w) : "", w >= 0 ? when_n : 0);
    if (off_w < 0) {
        return refuse(a, UIT_E_NOMEM, "action", at(a, n), "no memory for the action's when=");
    }
    a->when_at[r] = (uint32_t)off_w;
    if (r >= a->nwhen) {
        a->nwhen = r + 1;
    }
    return r;
}

int32_t moy_uitree_wasm_action_row(void *x, int32_t act, int32_t slot) {
    (void)x;
    APP(a);
    char where[32];
    snprintf(where, sizeof where, "action %d, slot %d", (int)act, (int)slot);
    return check(a, uit_action_row(a->e, (int)act, (int)slot), "action_row", where);
}

// -- build ---------------------------------------------------------------------

int32_t moy_uitree_wasm_build(void *x, const uint8_t *page, uint32_t page_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int p = put(a, page, page_n);
    if (p < 0) {
        return refuse(a, UIT_E_NOMEM, "build", "", "no memory for the page's id");
    }
    uit_build_begin(a->e, at(a, p));
    a->page = -1;
    return 0;
}

int32_t moy_uitree_wasm_node(void *x, int32_t kind, const uint8_t *key, uint32_t key_n, int32_t parent) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int k = put(a, key, key_n);
    int r = uit_node(a->e, (int)kind, at(a, k), (int)parent);
    if (r < 0) {
        char where[160];
        key_path(a, (int)parent, where, sizeof where);
        size_t w = strlen(where);
        snprintf(where + w, sizeof where - w, "%s%s", w ? "/" : "", at(a, k));
        return refuse(a, r, "node", where, NULL);
    }
    if (kind == UIT_PAGE) {
        a->page = r;
    }
    return r;
}

int32_t moy_uitree_wasm_prop(void *x, int32_t node, const uint8_t *name, uint32_t name_n,
                             const uint8_t *src, uint32_t src_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int n = put(a, name, name_n), s = put(a, src, src_n);
    int r = uit_prop(a->e, (int)node, at(a, n), at(a, s), NULL);
    if (r < 0) {
        char where[200];
        key_path(a, (int)node, where, sizeof where);
        size_t w = strlen(where);
        snprintf(where + w, sizeof where - w, " %s=", at(a, n));
        return refuse(a, r, "prop", where, NULL);
    }
    return r;
}

int32_t moy_uitree_wasm_node_action(void *x, int32_t node, int32_t act) {
    (void)x;
    APP(a);
    int r = uit_node_action(a->e, (int)node, (int)act);
    if (r < 0) {
        char where[160];
        key_path(a, (int)node, where, sizeof where);
        size_t w = strlen(where);
        snprintf(where + w, sizeof where - w, " action %d", (int)act);
        return refuse(a, r, "node_action", where, "no such node or action");
    }
    return r;
}

int32_t moy_uitree_wasm_build_end(void *x) {
    (void)x;
    APP(a);
    return check(a, uit_build_end(a->e), "build_end", "");
}

// -- visit and frame --------------------------------------------------------------

int32_t moy_uitree_wasm_open(void *x) {
    (void)x;
    APP(a);
    uit_open(a->e);
    a->opened = 1;
    a->hook_open = -1;
    for (int k = 0; k < a->e->nacts; k++) {
        if ((a->e->acts[k].flags & UIT_A_HOOK) && strcmp(uit_str(a->e, a->e->acts[k].name), "opened") == 0) {
            a->hook_open = k;
        }
    }
    return 0;
}

int32_t moy_uitree_wasm_focus(void *x, const uint8_t *key, uint32_t key_n) {
    (void)x;
    APP(a);
    TEXTS_BEGIN(a);
    int k = key_n ? put(a, key, key_n) : -1;
    uit_want_focus(a->e, at(a, k));
    return 0;
}

int32_t moy_uitree_wasm_frame(void *x, int32_t flags) {
    (void)x;
    APP(a);
    if (flags != 0) {
        return refuse(a, UIT_E_ARG, "frame", "flags", "flags is 0: no frame flag is defined yet");
    }
    if (a->page < 0) {
        return refuse(a, UIT_E_ARG, "frame", "", "no page is built: uitree_build, the nodes, uitree_build_end, "
                      "then uitree_open, before the first frame");
    }
    void *target = moycore_ui_target != NULL ? moycore_ui_target() : NULL;
    a->handler = -1;
    a->frames++;
    int r = uit_run_frame(a->e, target, UIT_F_BAND | UIT_F_TRACE);
    if (r < 0) {
        return refuse(a, r, "frame", "", "no compiled run is open");
    }
    char line[600];
    while (uit_trace_next(a->e, line, sizeof line) >= 0) {
        char *name = strchr(line, '\x01');
        char *text = name != NULL ? strchr(name + 1, '\x01') : NULL;
        if (text == NULL) {
            continue;
        }
        *name++ = 0;
        *text++ = 0;
        trace(a, line, name, "%s", text);
    }
    return r;
}

int32_t moy_uitree_wasm_next(void *x, uint8_t *ev, uint32_t ev_cap) {
    (void)x;
    APP(a);
    if (ev_cap < 8u) {
        return refuse(a, UIT_E_ARG, "next", "ev", "ev holds two words (8 bytes): the model slot and the row");
    }
    a->handler = -1;
    if (a->hook_open >= 0) {
        a->handler = a->hook_open;
        a->hook_open = -1;
        trace(a, "action", "opened", "from the engine (open)");
        memset(ev, 0xFF, 8);        // -1, -1: no model, no row
        return a->handler;
    }
    char src[200];
    int32_t w[3];
    int k;
    while ((k = uit_next(a->e, w, src, sizeof src)) != UIT_EV_NONE) {
        if (k != UIT_EV_ACTION) {
            continue;               // a control's write: the slot holds it
        }
        char *cut = strchr(src, '\x02');
        if (cut != NULL) {
            *cut = 0;
        }
        const char *name = uit_str(a->e, a->e->acts[w[0]].name);
        if (!uit_can(a->e, w[0], w[2])) {
            const char *cond = w[0] < a->nwhen ? a->when.p + a->when_at[w[0]] : "";
            int16_t row = a->e->acts[w[0]].row;
            if (cond[0]) {
                trace(a, "skip", name, "from %s (when %s is false)", src, cond);
            } else if (row >= 0) {
                trace(a, "skip", name, "from %s (%s holds no row)", src, uit_str(a->e, a->e->slots[row].name));
            }
            continue;
        }
        trace(a, "action", name, "from %s", src);
        a->handler = w[0];
        memcpy(ev, &w[1], 8);       // the app's buffer need not be aligned
        return w[0];
    }
    return -1;
}

// -- state -----------------------------------------------------------------------

#define SLOT(a, slot, row)                                                      \
    if (!slot_ok(a, slot)) {                                                    \
        char where_[24];                                                        \
        snprintf(where_, sizeof where_, "slot %d", (int)(slot));                \
        return refuse(a, UIT_E_ARG, row, where_, "no such slot");               \
    }

int32_t moy_uitree_wasm_set_int(void *x, int32_t slot, int32_t v) {
    (void)x;
    APP(a);
    SLOT(a, slot, "set_int");
    uit_buf_t was = {0};
    was_of(a, (int)slot, &was);
    int r = uit_set_int(a->e, (int)slot, v);
    if (r >= 0) {
        traced_write(a, (int)slot, was.p);
    }
    uit_buf_free(&was);
    return check(a, r, "set_int", uit_str(a->e, a->e->slots[slot].name));
}

int32_t moy_uitree_wasm_set_text(void *x, int32_t slot, const uint8_t *s, uint32_t s_n) {
    (void)x;
    APP(a);
    SLOT(a, slot, "set_text");
    uit_buf_t was = {0};
    was_of(a, (int)slot, &was);
    int r = uit_set_text(a->e, (int)slot, (const char *)s, s_n);
    if (r >= 0) {
        traced_write(a, (int)slot, was.p);
    }
    uit_buf_free(&was);
    return check(a, r, "set_text", uit_str(a->e, a->e->slots[slot].name));
}

int32_t moy_uitree_wasm_set_absent(void *x, int32_t slot) {
    (void)x;
    APP(a);
    SLOT(a, slot, "set_absent");
    return check(a, uit_set_absent(a->e, (int)slot), "set_absent", uit_str(a->e, a->e->slots[slot].name));
}

// A cell as the trace shows a value of field type `t`: a text quoted, a bool
// True or False, a number as it is.
static void show_cell(uit_buf_t *b, int t, const char *v) {
    if (t == UIT_BOOL) {
        uit_buf_printf(b, "%s", v[0] == '1' ? "True" : "False");
    } else if (t == UIT_INT || t == UIT_NUM) {
        uit_buf_printf(b, "%s", v);
    } else {
        uit_buf_printf(b, "\"%s\"", v);
    }
}

// A model's rows before a write: its cells and their offsets, copied.
typedef struct {
    int n, nf, key;
    char *cells;
    uint32_t *at;
} rows_t;

static const char *cell_of(const rows_t *r, int row, int f) {
    return r->cells + r->at[row * r->nf + f];
}

static int row_keyed(const rows_t *r, const char *key) {
    for (int i = 0; i < r->n; i++) {
        if (strcmp(cell_of(r, i, r->key), key) == 0) {
            return i;
        }
    }
    return -1;
}

// What a write of `now` over `was` did, as the layers trace a model's set: up
// to two changed rows by field where only fields changed, else the counts
// "[N rows] +added -gone ~changed moved M". 0 where nothing moved.
static int model_detail(const uit_t *e, const uit_slot_t *S, const rows_t *was, const rows_t *now, uit_buf_t *b) {
    int add = 0, gone = 0, chg = 0, moved = 0, nch = 0;
    uit_buf_t ch = {0};
    for (int i = 0; i < now->n; i++) {
        int j = row_keyed(was, cell_of(now, i, now->key));
        if (j < 0) {
            add++;
            continue;
        }
        int first = 1;
        for (int f = 0; f < now->nf; f++) {
            const char *o = cell_of(was, j, f), *v = cell_of(now, i, f);
            if (strcmp(o, v) == 0) {
                continue;
            }
            if (first) {
                chg++;
                if (nch++) {
                    uit_buf_printf(&ch, "%s", "; ");
                }
                show_cell(&ch, S->mf->ftype[now->key], cell_of(now, i, now->key));
                uit_buf_printf(&ch, "%s", ": ");
                first = 0;
            } else {
                uit_buf_printf(&ch, "%s", ", ");
            }
            uit_buf_printf(&ch, "%s ", uit_str(e, S->mf->fname[f]));
            show_cell(&ch, S->mf->ftype[f], o);
            uit_buf_printf(&ch, "%s", " -> ");
            show_cell(&ch, S->mf->ftype[f], v);
        }
    }
    for (int j = 0; j < was->n; j++) {
        if (row_keyed(now, cell_of(was, j, was->key)) < 0) {
            gone++;
        }
    }
    // moved: kept rows whose place among the kept rows changed
    for (int i = 0, j = 0; i < now->n; i++) {
        if (row_keyed(was, cell_of(now, i, now->key)) < 0) {
            continue;
        }
        while (j < was->n && row_keyed(now, cell_of(was, j, was->key)) < 0) {
            j++;
        }
        if (j < was->n && strcmp(cell_of(was, j, was->key), cell_of(now, i, now->key)) != 0) {
            moved++;
        }
        j++;
    }
    int any = add || gone || chg || moved || now->n != was->n;
    if (any) {
        if (add + gone + moved == 0 && chg <= 2 && nch > 0) {
            uit_buf_printf(b, "%s", ch.p);
        } else {
            uit_buf_printf(b, "[%d rows] +%d -%d ~%d moved %d", now->n, add, gone, chg, moved);
        }
    }
    uit_buf_free(&ch);
    return any;
}

int32_t moy_uitree_wasm_set_model(void *x, int32_t slot, int32_t n, const uint8_t *cells, uint32_t cells_n) {
    (void)x;
    APP(a);
    SLOT(a, slot, "set_model");
    uit_slot_t *S = &a->e->slots[slot];
    const char *name = uit_str(a->e, S->name);
    if (S->type != UIT_MODEL || S->mf == NULL) {
        return refuse(a, UIT_E_ARG, "set_model", name, "not a model state, or its fields are not declared "
                      "(uitree_model)");
    }
    rows_t was = {a->opened ? S->nrows : 0, S->nf, S->key, NULL, NULL};
    if (was.n > 0) {
        size_t cn = S->cells_n + 1, an = sizeof(uint32_t) * (size_t)(was.n * was.nf);
        was.cells = UIT_MALLOC(cn);
        was.at = UIT_MALLOC(an);
        if (was.cells == NULL || was.at == NULL) {
            UIT_FREE(was.cells);
            UIT_FREE(was.at);
            return refuse(a, UIT_E_NOMEM, "set_model", name, "no memory for the trace's copy of the rows");
        }
        memcpy(was.cells, S->cells, cn);
        memcpy(was.at, S->at, an);
    }
    int r = uit_set_model(a->e, (int)slot, (int)n, (const char *)cells, cells_n);
    if (r >= 0 && a->opened) {
        S = &a->e->slots[slot];
        rows_t now = {S->nrows, S->nf, S->key, S->cells, S->at};
        uit_buf_t d = {0};
        if (model_detail(a->e, S, &was, &now, &d)) {
            const char *in = a->handler >= 0 ? uit_str(a->e, a->e->acts[a->handler].name) : "the app";
            trace(a, "state", name, "%s   (in %s)", d.p, in);
        }
        uit_buf_free(&d);
    }
    UIT_FREE(was.cells);
    UIT_FREE(was.at);
    return check(a, r, "set_model", name);
}

int32_t moy_uitree_wasm_get_int(void *x, int32_t slot) {
    (void)x;
    APP(a);
    SLOT(a, slot, "get_int");
    return uit_get_int(a->e, (int)slot);
}

static int32_t copy_out(const char *p, size_t n, uint8_t *out, uint32_t cap) {
    memcpy(out, p, n < cap ? n : cap);
    return (int32_t)n;
}

int32_t moy_uitree_wasm_get_text(void *x, int32_t slot, uint8_t *out, uint32_t out_cap) {
    (void)x;
    APP(a);
    SLOT(a, slot, "get_text");
    size_t n = 0;
    const char *p = uit_get_text(a->e, (int)slot, &n);
    if (p == NULL) {
        return refuse(a, UIT_E_ARG, "get_text", uit_str(a->e, a->e->slots[slot].name),
                      "not a text, enum or row_of state: read it with uitree_get_int");
    }
    return copy_out(p, n, out, out_cap);
}

int32_t moy_uitree_wasm_get_cell(void *x, int32_t slot, int32_t row, int32_t f, uint8_t *out, uint32_t out_cap) {
    (void)x;
    APP(a);
    SLOT(a, slot, "get_cell");
    const char *p = uit_get_cell(a->e, (int)slot, (int)row, (int)f);
    if (p == NULL) {
        char where[64];
        snprintf(where, sizeof where, "%s row %d field %d", uit_str(a->e, a->e->slots[slot].name), (int)row,
                 (int)f);
        return refuse(a, UIT_E_ARG, "get_cell", where, "no such row or field in this model");
    }
    return copy_out(p, strlen(p), out, out_cap);
}

// -- errors and the dump ------------------------------------------------------------

int32_t moy_uitree_wasm_error(void *x, uint8_t *out, uint32_t out_cap) {
    (void)x;
    app_t *a = app();
    if (a == NULL || a->err == 0) {
        return 0;
    }
    return copy_out(a->errline, strlen(a->errline), out, out_cap);
}

int32_t moy_uitree_wasm_dump(void *x, uint8_t *out, uint32_t out_cap) {
    (void)x;
    APP(a);
    return uit_dump(a->e, (char *)out, out_cap);
}
