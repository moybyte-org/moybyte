// The store: declarations, state, the build's calls, events, the trace and
// the dump (moy_uitree.h).
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_uitree_int.h"

static size_t text_len(const char *s, size_t max)
{
    size_t n = 0;
    while (n < max && s[n])
        n++;
    return n;
}

// -- buffers ------------------------------------------------------------------

static int buf_need(uit_buf_t *b, size_t more)
{
    if (b->n + more + 1 <= b->cap)
        return 0;
    uint32_t cap = b->cap ? b->cap : 256;
    while (cap < b->n + more + 1)
        cap *= 2;
    char *p = UIT_REALLOC(b->p, cap);
    if (!p)
        return -1;
    b->p = p;
    b->cap = cap;
    return 0;
}

int uit_buf_put(uit_buf_t *b, const char *s, size_t n)
{
    if (buf_need(b, n))
        return -1;
    uint32_t at = b->n;
    if (n)
        memcpy(b->p + at, s, n);
    b->p[at + n] = 0;
    b->n = at + (uint32_t)n + 1;
    return (int)at;
}

int uit_buf_puts(uit_buf_t *b, const char *s)
{
    return uit_buf_put(b, s, strlen(s));
}

int uit_buf_printf(uit_buf_t *b, const char *fmt, ...)
{
    va_list ap, ap2;
    va_start(ap, fmt);
    va_copy(ap2, ap);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0 || buf_need(b, (size_t)n)) {
        va_end(ap2);
        return -1;
    }
    vsnprintf(b->p + b->n, (size_t)n + 1, fmt, ap2);
    va_end(ap2);
    b->n += (uint32_t)n;
    return n;
}

static void buf_raw(uit_buf_t *b, const char *s, size_t n)
{
    if (buf_need(b, n))
        return;
    memcpy(b->p + b->n, s, n);
    b->n += (uint32_t)n;
    b->p[b->n] = 0;
}

void uit_buf_repr(uit_buf_t *b, const char *s, size_t n)
{
    int sq = memchr(s, '\'', n) != NULL, dq = memchr(s, '"', n) != NULL;
    char q = (sq && !dq) ? '"' : '\'';
    buf_raw(b, &q, 1);
    for (size_t i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)s[i];
        char esc[8];
        if (ch == '\\')
            buf_raw(b, "\\\\", 2);
        else if (ch == (unsigned char)q)
            esc[0] = '\\', esc[1] = (char)q, buf_raw(b, esc, 2);
        else if (ch == '\n')
            buf_raw(b, "\\n", 2);
        else if (ch == '\t')
            buf_raw(b, "\\t", 2);
        else if (ch == '\r')
            buf_raw(b, "\\r", 2);
        else if (ch < 0x20 || ch == 0x7f)
            snprintf(esc, sizeof esc, "\\x%02x", ch), buf_raw(b, esc, 4);
        else
            buf_raw(b, (const char *)&s[i], 1);
    }
    buf_raw(b, &q, 1);
}

void uit_buf_free(uit_buf_t *b)
{
    UIT_FREE(b->p);
    b->p = NULL;
    b->n = b->cap = 0;
}

int uit_grow(void **p, int *cap, int need, size_t size)
{
    if (need <= *cap)
        return 0;
    int c = *cap ? *cap : 16;
    while (c < need)
        c *= 2;
    void *q = UIT_REALLOC(*p, (size_t)c * size);
    if (!q)
        return -1;
    *p = q;
    *cap = c;
    return 0;
}

int uit_fail(uit_t *e, int code, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(e->err, sizeof e->err, fmt, ap);
    va_end(ap);
    e->code[0] = 0;
    return code;
}

int uit_check_fail(uit_t *e, const char *code, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(e->err, sizeof e->err, fmt, ap);
    va_end(ap);
    memcpy(e->code, code, 3);
    e->code[3] = 0;
    return UIT_E_CHECK;
}

const char *uit_str(const uit_t *e, uint16_t off)
{
    return off == UIT_NONE ? "" : e->pool.p + off;
}

static int pool_put(uit_t *e, const char *s, size_t n)
{
    int at = uit_buf_put(&e->pool, s, n);
    if (at < 0 || at >= (int)UIT_NONE)
        return -1;
    return at;
}

static int pool_puts(uit_t *e, const char *s)
{
    return pool_put(e, s, strlen(s));
}

// -- the engine -----------------------------------------------------------------

uit_t *uit_new(void)
{
    uit_t *e = UIT_MALLOC(sizeof *e);
    if (!e)
        return NULL;
    memset(e, 0, sizeof *e);
    pool_put(e, "", 0);         // offset 0 is the empty text
    e->ccur = -1;
    return e;
}

static void free_drew(uit_t *e)
{
    for (int i = 0; i < e->ndrew; i++) {
        UIT_FREE(e->drew[i].rowkey);
        UIT_FREE(e->drew[i].val);
    }
    e->ndrew = 0;
}

void uit_free(uit_t *e)
{
    if (!e)
        return;
    for (int i = 0; i < e->nslots; i++) {
        UIT_FREE(e->slots[i].s);
        UIT_FREE(e->slots[i].cells);
        UIT_FREE(e->slots[i].at);
        UIT_FREE(e->slots[i].sync);
    }
    for (int i = 0; i < e->norders; i++) {
        UIT_FREE(e->orders[i].key);
        UIT_FREE(e->orders[i].keys);
    }
    for (int i = 0; i < e->ndrafts; i++) {
        UIT_FREE(e->drafts[i].key);
        UIT_FREE(e->drafts[i].buf);
    }
    free_drew(e);
    UIT_FREE(e->slots); UIT_FREE(e->acts); UIT_FREE(e->tmpls); UIT_FREE(e->ops);
    UIT_FREE(e->conds); UIT_FREE(e->terms); UIT_FREE(e->nodes); UIT_FREE(e->props);
    UIT_FREE(e->btns); UIT_FREE(e->ids); UIT_FREE(e->prev); UIT_FREE(e->evs);
    UIT_FREE(e->drew); UIT_FREE(e->orders); UIT_FREE(e->want); UIT_FREE(e->drafts);
    UIT_FREE(e->labels); UIT_FREE(e->tvl);
    uit_buf_free(&e->pool); uit_buf_free(&e->scratch); uit_buf_free(&e->evtext); uit_buf_free(&e->cells);
    uit_buf_free(&e->trace);
    UIT_FREE(e);
}

const char *uit_error(const uit_t *e)
{
    return e->err;
}

const char *uit_get_cell(const uit_t *e, int slot, int row, int f)
{
    if (slot < 0 || slot >= e->nslots || e->slots[slot].type != UIT_MODEL || row < 0 || row >= e->slots[slot].nrows ||
        f < 0 || f >= e->slots[slot].nf)
        return NULL;
    return uit_cell(e, slot, row, f);
}

const char *uit_error_code(const uit_t *e)
{
    return e->code;
}

// -- declarations ---------------------------------------------------------------

int uit_state(uit_t *e, const char *name, int type, const char *values, size_t vlen)
{
    if (e->decl_done)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("state %s declared after the first build", "%s"), name);
    if (type < UIT_BOOL || type > UIT_MODEL)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("state %s: no type %d", "state %s %d"), name, type);
    if (UIT_GROW(e, slots, nslots, cslots))
        return UIT_E_NOMEM;
    uit_slot_t *S = &e->slots[e->nslots];
    memset(S, 0, sizeof *S);
    int nm = pool_puts(e, name);
    if (nm < 0)
        return UIT_E_NOMEM;
    S->name = (uint16_t)nm;
    S->type = (uint8_t)type;
    S->model = -1;
    S->vals = UIT_NONE;
    if (type == UIT_ENUM && values) {
        int v = pool_put(e, values, vlen);
        if (v < 0)
            return UIT_E_NOMEM;
        S->vals = (uint16_t)v;
        for (size_t i = 0; i <= vlen; i++)
            if (i == vlen || values[i] == 0)
                S->nvals++;
    }
    return e->nslots++;
}

int uit_model(uit_t *e, int slot, const char *names, size_t nlen, const uint8_t *types, int nf, int key)
{
    if (slot < 0 || slot >= e->nslots || e->slots[slot].type != UIT_MODEL || nf < 1 || nf > UIT_FIELDS ||
        key < 0 || key >= nf)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("model: bad slot %d or %d fields", "model %d %d"), slot, nf);
    uit_slot_t *S = &e->slots[slot];
    S->nf = (uint8_t)nf;
    S->key = (uint8_t)key;
    size_t at = 0;
    for (int f = 0; f < nf; f++) {
        size_t n = text_len(names + at, nlen - at);
        int o = pool_put(e, names + at, n);
        if (o < 0)
            return UIT_E_NOMEM;
        S->fname[f] = (uint16_t)o;
        S->ftype[f] = types[f];
        at += n + 1;
    }
    return 0;
}

int uit_row_of(uit_t *e, int slot, int model_slot)
{
    if (slot < 0 || slot >= e->nslots || model_slot < 0 || model_slot >= e->nslots)
        return UIT_E_ARG;
    e->slots[slot].model = (int16_t)model_slot;
    return 0;
}

int uit_labels(uit_t *e, const char *name, const char *keys, size_t klen, const char *words,
               size_t wlen)
{
    if (e->decl_done)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("labels %s declared after the first build", "%s"), name);
    int n = 0;
    for (size_t i = 0; i <= klen; i++)
        n += i == klen || keys[i] == 0;
    int w = 0;
    for (size_t i = 0; i <= wlen; i++)
        w += i == wlen || words[i] == 0;
    if (n != w)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("labels %s: %d keys, %d words", "labels %s %d %d"), name, n, w);
    if (UIT_GROW(e, labels, nlabels, clabels))
        return UIT_E_NOMEM;
    int a = pool_puts(e, name), b = pool_put(e, keys, klen), c = pool_put(e, words, wlen);
    if (a < 0 || b < 0 || c < 0)
        return UIT_E_NOMEM;
    uit_labels_t L = {(uint16_t)a, (uint16_t)b, (uint16_t)c, (uint16_t)n};
    e->labels[e->nlabels] = L;
    return e->nlabels++;
}

const char *uit_label_word(const uit_t *e, int table, const char *key, size_t n)
{
    const uit_labels_t *L = &e->labels[table];
    const char *k = uit_str(e, L->keys), *w = uit_str(e, L->words), *dflt = NULL;
    for (int i = 0; i < L->n; i++) {
        size_t kn = strlen(k);
        if (kn == n && !memcmp(k, key, n))
            return w;
        if (kn == 1 && k[0] == '_')
            dflt = w;
        k += kn + 1;
        w += strlen(w) + 1;
    }
    return dflt ? dflt : "";
}

int uit_action(uit_t *e, const char *name, const char *label, const char *hint, const char *when,
               int off, int flags, const char *letters, const char *icon)
{
    if (e->decl_done)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("action %s declared after the first build", "%s"), name);
    if (UIT_GROW(e, acts, nacts, cacts))
        return UIT_E_NOMEM;
    uit_act_t A;
    memset(&A, 0, sizeof A);
    int a = pool_puts(e, name), b = pool_puts(e, label ? label : "");
    if (a < 0 || b < 0)
        return UIT_E_NOMEM;
    A.name = (uint16_t)a;
    A.label = (uint16_t)b;
    A.hint = (int16_t)uit_tmpl_compile(e, hint ? hint : (label ? label : ""), -1);
    if (A.hint < 0)
        return A.hint;
    A.when = -1;
    if (when) {
        int c = uit_cond_compile(e, when, -1);
        if (c < 0)
            return c;
        A.when = (int16_t)c;
    }
    A.off = (uint8_t)off;
    A.flags = (uint8_t)flags;
    int ls = pool_puts(e, letters ? letters : "");
    if (ls < 0)
        return UIT_E_NOMEM;
    A.letters = (uint16_t)ls;
    int ic = pool_puts(e, icon ? icon : "");
    if (ic < 0)
        return UIT_E_NOMEM;
    A.icon = (uint16_t)ic;
    A.letter = (uint8_t)(letters ? letters[0] : 0);
    A.row = -1;
    e->acts[e->nacts] = A;
    return e->nacts++;
}

int uit_action_row(uit_t *e, int a, int slot)
{
    if (a < 0 || a >= e->nacts || slot < 0 || slot >= e->nslots || e->slots[slot].type != UIT_ROWOF)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("action %d row=%d: no row_of", "%d %d"), a, slot);
    e->acts[a].row = (int16_t)slot;
    return 0;
}

// -- state ----------------------------------------------------------------------

static int slot_ok(uit_t *e, int slot)
{
    return slot >= 0 && slot < e->nslots;
}

int uit_set_int(uit_t *e, int slot, int32_t v)
{
    if (!slot_ok(e, slot))
        return UIT_E_ARG;
    uit_slot_t *S = &e->slots[slot];
    S->i = v;
    S->f = v;
    S->ver++;
    return 0;
}

int uit_set_num(uit_t *e, int slot, double v)
{
    if (!slot_ok(e, slot))
        return UIT_E_ARG;
    e->slots[slot].f = v;
    e->slots[slot].i = (int32_t)v;
    e->slots[slot].ver++;
    return 0;
}

int uit_set_text_q(uit_t *e, int slot, const char *s, size_t n)
{
    uit_slot_t *S = &e->slots[slot];
    if (n + 1 > S->scap) {
        char *p = UIT_REALLOC(S->s, n + 1);
        if (!p)
            return UIT_E_NOMEM;
        S->s = p;
        S->scap = (uint32_t)n + 1;
    }
    if (n)
        memcpy(S->s, s, n);
    S->s[n] = 0;
    S->sn = (uint32_t)n;
    return 0;
}

int uit_set_text(uit_t *e, int slot, const char *s, size_t n)
{
    if (!slot_ok(e, slot))
        return UIT_E_ARG;
    int r = uit_set_text_q(e, slot, s, n);
    if (r == 0)
        e->slots[slot].ver++;
    return r;
}

int uit_set_model(uit_t *e, int slot, int n, const char *cells, size_t len)
{
    if (slot < 0 || slot >= e->nslots || e->slots[slot].type != UIT_MODEL || n < 0)
        return UIT_E_ARG;
    uit_slot_t *S = &e->slots[slot];
    char *c = UIT_MALLOC(len + 1);
    uint32_t *at = UIT_MALLOC(sizeof(uint32_t) * (size_t)(n * S->nf + 1));
    if (!c || !at) {
        UIT_FREE(c);
        UIT_FREE(at);
        return UIT_E_NOMEM;
    }
    memcpy(c, cells, len);
    c[len] = 0;
    size_t p = 0;
    for (int i = 0; i < n * S->nf; i++) {
        if (p > len) {
            UIT_FREE(c);
            UIT_FREE(at);
            return uit_fail(e, UIT_E_ARG, UIT_MSG("model: %d cells short", "model %d"), n * S->nf - i);
        }
        at[i] = (uint32_t)p;
        p += text_len(c + p, len - p) + 1;
    }
    UIT_FREE(S->cells);
    UIT_FREE(S->at);
    S->cells = c;
    S->at = at;
    S->cells_n = (uint32_t)len;
    S->nrows = n;
    S->ver++;
    // the removal rule, for each list over this model with a cur= (settle)
    for (int k = 0; k < e->nnodes; k++)
        if (UIT_IS_LIST(e->nodes[k].kind) && e->nodes[k].slot == slot && e->nodes[k].cur >= 0)
            uit_after_removal(e, k);
    return 0;
}

int32_t uit_get_int(const uit_t *e, int slot)
{
    if (slot < 0 || slot >= e->nslots)
        return 0;
    return e->slots[slot].i;
}

const char *uit_get_text(const uit_t *e, int slot, size_t *n)
{
    if (slot < 0 || slot >= e->nslots)
        return NULL;
    const uit_slot_t *S = &e->slots[slot];
    if (S->type != UIT_TEXT && S->type != UIT_ENUM && S->type != UIT_ROWOF)
        return NULL;
    *n = S->sn;
    return S->s ? S->s : "";
}

void uit_slot_repr(uit_t *e, uit_buf_t *b, int slot)
{
    const uit_slot_t *S = &e->slots[slot];
    switch (S->type) {
    case UIT_BOOL:
        uit_buf_printf(b, "%s", S->i ? "True" : "False");
        break;
    case UIT_INT:
        uit_buf_printf(b, "%ld", (long)S->i);
        break;
    case UIT_NUM:
        uit_buf_printf(b, "%.17g", S->f);
        break;
    case UIT_MODEL:
        uit_buf_printf(b, "[%d rows]", S->nrows);
        break;
    default:
        uit_buf_repr(b, S->s ? S->s : "", S->sn);
    }
}

// -- the build ------------------------------------------------------------------

void uit_build_begin(uit_t *e, const char *page_id)
{
    if (!e->decl_done) {
        e->decl_done = 1;
        e->m_pool = e->pool.n;
        e->m_tmpls = e->ntmpls;
        e->m_ops = e->nops;
        e->m_conds = e->nconds;
        e->m_terms = e->nterms;
    }
    e->pool.n = e->m_pool;
    e->ntmpls = e->m_tmpls;
    e->nops = e->m_ops;
    e->nconds = e->m_conds;
    e->nterms = e->m_terms;
    e->nnodes = e->nprops = e->nbtns = 0;
    int p = pool_puts(e, page_id ? page_id : "");
    e->page_id = p < 0 ? 0 : (uint16_t)p;
    free_drew(e);
}

static uint16_t up(int k)
{
    return k < 0 ? UIT_NONE : (uint16_t)k;
}

int uit_node(uit_t *e, int kind, const char *key, int parent)
{
    if (kind < UIT_PAGE || kind >= UIT_KINDS)
        return uit_fail(e, UIT_E_KIND, UIT_MSG("no kind %d", "%d"), kind);
    if ((parent < 0) != (kind == UIT_PAGE) || parent >= e->nnodes)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("node %s: parent %d", "node %s %d"), key, parent);
    if (UIT_GROW(e, nodes, nnodes, cnodes))
        return UIT_E_NOMEM;
    int k = pool_puts(e, key);
    if (k < 0)
        return UIT_E_NOMEM;
    uit_node_t N;
    memset(&N, 0, sizeof N);
    N.kind = (uint8_t)kind;
    N.key = (uint16_t)k;
    N.parent = up(parent);
    N.kid = N.last = N.next = N.props = UIT_NONE;
    N.act = N.shown = N.focus = N.t_main = N.t_value = N.t_icon = N.t_aux = -1;
    N.slot = N.cur = N.changed = N.on = N.current = -1;
    if (kind == UIT_FIELD)
        N.btn = 32;
    if (kind == UIT_SPLIT)
        N.btn = N.nbtn = GAP_M, N.lit = 1;          // pad="m", gap="m", align="center"
    if (kind == UIT_SWITCH) {
        // keyed "switch:NAME": the enum or bool it switches on
        const char *name = strncmp(key, "switch:", 7) ? "" : key + 7;
        int s = uit_slot_find(e, name, strlen(name));
        if (s < 0 || (e->slots[s].type != UIT_ENUM && e->slots[s].type != UIT_BOOL))
            return uit_fail(e, UIT_E_NAME, UIT_MSG("switch %s: no enum or bool of that name", "switch %s"), key);
        N.slot = (int16_t)s;
    }
    int me = e->nnodes++;
    e->nodes[me] = N;
    if (parent >= 0) {
        uit_node_t *P = &e->nodes[parent];
        if (P->last == UIT_NONE)
            P->kid = (uint16_t)me;
        else
            e->nodes[P->last].next = (uint16_t)me;
        P->last = (uint16_t)me;
    }
    return me;
}

int uit_list_of(const uit_t *e, int node)
{
    for (int k = node; k >= 0 && k != (int)UIT_NONE; k = e->nodes[k].parent)
        if (UIT_IS_LIST(e->nodes[k].kind))
            return k;
    return -1;
}

int uit_model_of(uit_t *e, int node)
{
    for (int k = node; k >= 0 && k != (int)UIT_NONE; k = e->nodes[k].parent)
        if (UIT_IS_LIST(e->nodes[k].kind))
            return e->nodes[k].slot;
    return -1;
}

const char *const uit_kind_names[UIT_KINDS] = {"", "page", "group", "label", "info", "link", "button",
                                               "toggle", "rows", "dialog", "seg", "slider", "field",
                                               "empty", "progress", "text_view", "scroll", "menu",
                                               "item", "split", "canvas", "tabs", "switch", "case",
                                               "table", "col", "foot"};

static const char *TONES[] = {"ink", "dim", "value", "danger"};
static const char *BKINDS[] = {"normal", "primary", "tonal", "danger", "toggle"};

static int lit_index(const char *v, const char *const *tab, int n)
{
    for (int i = 0; i < n; i++)
        if (!strcmp(v, tab[i]))
            return i;
    return -1;
}

static int add_prop(uit_t *e, int node, const char *name, const char *src, const char *repr, int kind, int idx)
{
    if (UIT_GROW(e, props, nprops, cprops))
        return UIT_E_NOMEM;
    int a = pool_puts(e, name), b = pool_puts(e, src), c = repr ? pool_puts(e, repr) : (int)UIT_NONE;
    if (a < 0 || b < 0 || c < 0)
        return UIT_E_NOMEM;
    uit_prop_t P = {(uint16_t)a, (uint16_t)b, (uint16_t)c, (uint8_t)kind, (int16_t)idx, UIT_NONE};
    int me = e->nprops++;
    e->props[me] = P;
    uit_node_t *N = &e->nodes[node];
    if (N->props == UIT_NONE) {
        N->props = (uint16_t)me;
    } else {
        int k = N->props;
        while (e->props[k].next != UIT_NONE)
            k = e->props[k].next;
        e->props[k].next = (uint16_t)me;
    }
    return 0;
}

int uit_prop(uit_t *e, int node, const char *name, const char *src, const char *repr)
{
    if (node < 0 || node >= e->nnodes)
        return UIT_E_ARG;
    uit_node_t *N = &e->nodes[node];
    int model = uit_model_of(e, node);
    if (UIT_IS_LIST(N->kind))
        model = -1;             // a list's own props read the page, not its rows
    int kind = N->kind;
    int16_t *tslot = NULL;
    int list = uit_list_of(e, node);
    e->ccur = list >= 0 && list != node ? e->nodes[list].current : -1;
    if (!strcmp(name, "shown") || !strcmp(name, "focus")) {
        int c = uit_cond_compile(e, src, model);
        if (c < 0)
            return c;
        if (name[0] == 's')
            N->shown = (int16_t)c;
        else
            N->focus = (int16_t)c;
        return add_prop(e, node, name, src, repr, PK_COND, c);
    }
    if (kind == UIT_ROWS && !strcmp(name, "group")) {
        // a caption drawn where its value changes from the row before: over the rows
        model = N->slot;
        tslot = &N->t_main;
    } else if (kind == UIT_EMPTY && !strcmp(name, "lines")) {
        // one prop a line, each a template, drawn in order (an empty one left out)
        int t = uit_tmpl_compile(e, src, model);
        if (t < 0)
            return t;
        if (e->tmpls[t].type == UIT_BOOL || e->tmpls[t].type == UIT_MODEL)
            return uit_check_fail(e, "T03", UIT_MSG("empty lines=%s is a %s, not a text", "%s %s"), src,
                                  uit_type_name(e->tmpls[t].type));
        return add_prop(e, node, name, src, repr, PK_TMPL, t);
    } else if (kind == UIT_SEG && !strcmp(name, "text")) {
        // a seg over a row_of: each row's word, a template over its model's rows
        if (N->slot < 0 || e->slots[N->slot].type != UIT_ROWOF)
            return uit_fail(e, UIT_E_ARG, UIT_MSG("seg text=%s before its row_of", "%s"), src);
        model = e->slots[N->slot].model;
        tslot = &N->t_value;
    } else if (!strcmp(name, "title") || !strcmp(name, "caption") || !strcmp(name, "text") ||
               !strcmp(name, "label"))
        tslot = &N->t_main;
    else if (!strcmp(name, "value") || (!strcmp(name, "lo") && kind == UIT_SLIDER))
        tslot = &N->t_value;
    else if (!strcmp(name, "max") && kind == UIT_PROGRESS)
        tslot = &N->t_aux;
    else if (kind == UIT_SPLIT && (!strcmp(name, "dir") || !strcmp(name, "sizes"))) {
        if (name[0] == 'd' && !strchr(src, '{') && strcmp(src, "row") && strcmp(src, "col"))
            return uit_check_fail(e, "T03", UIT_MSG("ui.split dir=%s: one of row, col", "ui.split dir=%s"), src);
        tslot = name[0] == 'd' ? &N->t_value : &N->t_aux;
    }
    else if (!strcmp(name, "icon"))
        tslot = &N->t_icon;
    else if (!strcmp(name, "hint") && kind == UIT_TABLE)
        tslot = &N->t_value;
    else if ((!strcmp(name, "empty") && UIT_IS_LIST(kind)) ||
             (!strcmp(name, "hint") && (kind == UIT_TOGGLE || kind == UIT_FIELD)) ||
             (!strcmp(name, "hi") && kind == UIT_SLIDER))
        tslot = &N->t_aux;
    if (tslot) {
        int t = uit_tmpl_compile(e, src, model);
        if (t < 0)
            return t;
        const uit_tmpl_t *T = &e->tmpls[t];
        if ((kind == UIT_SLIDER && (!strcmp(name, "lo") || !strcmp(name, "hi"))) ||
            (kind == UIT_PROGRESS && (!strcmp(name, "value") || !strcmp(name, "max")))) {
            // an int prop: an int template, or the int literal a number lowers to
            const char *d = src + (src[0] == '-');
            int lit_int = T->lit && *d;
            for (; *d && lit_int; d++)
                lit_int = *d >= '0' && *d <= '9';
            if (T->type != UIT_INT && !lit_int)
                return uit_check_fail(e, "T03", UIT_MSG("%s %s=%s is a %s, not an int", "%s %s %s %s"), uit_kind_names[kind], name, src,
                                      uit_type_name(T->type));
        } else if (!(kind == UIT_SEG && !strcmp(name, "text"))) {      // a seg's row words: any type
            if (T->type == UIT_BOOL || T->type == UIT_MODEL)
                return uit_check_fail(e, "T03", UIT_MSG("%s %s=%s is a %s, not a text", "%s %s %s %s"), uit_kind_names[kind], name, src,
                                      uit_type_name(T->type));
        }
        *tslot = (int16_t)t;
        return add_prop(e, node, name, src, repr, PK_TMPL, t);
    }
    if (!strcmp(name, "tone") && kind == UIT_LABEL) {
        int i = lit_index(src, TONES, 4);
        if (i <= 0)
            return uit_fail(e, UIT_E_KIND, UIT_MSG("label tone=%s: an ink role this engine does not draw yet", "label tone=%s"), src);
        N->lit = (uint8_t)i;
        return add_prop(e, node, name, src, repr, PK_LIT, i);
    }
    if (kind == UIT_LABEL && (!strcmp(name, "wrap") || !strcmp(name, "face") || !strcmp(name, "align") ||
                              !strcmp(name, "lines"))) {
        static const char *const WRAPS[] = {"cut", "word", "char"}, *const ALIGNS[] = {"start", "center", "end"};
        static const char *const FACES[] = {"ui", "title", "mono", "caption", "key", "op", "result"};
        static const uint8_t CENTRE[] = {0, 1, 4};
        int i = name[0] == 'w' ? lit_index(src, WRAPS, 3) : name[0] == 'f' ? lit_index(src, FACES, 7)
                : name[0] == 'a' ? lit_index(src, ALIGNS, 3) : (int)strtol(src, NULL, 10);
        if (i < 0)
            return uit_fail(e, UIT_E_KIND, UIT_MSG("label %s=%s", "%s %s"), name, src);
        if (name[0] == 'w')
            N->nbtn = (uint16_t)((N->nbtn & ~LT_WRAP) | i);
        else if (name[0] == 'f')
            N->nbtn |= (uint16_t)((i + 1) << LT_FACE_SHIFT);
        else if (name[0] == 'a')
            N->nbtn |= (uint16_t)(CENTRE[i] << LT_ALIGN_SHIFT);
        else
            N->btn = (uint16_t)i, N->nbtn |= LT_LINES;
        return add_prop(e, node, name, src, repr, PK_LIT, i);
    }
    if (kind == UIT_CANVAS && (!strcmp(name, "draw") || !strcmp(name, "h"))) {
        // draw= names the front end's function (uit_on_draw); h= its height where it is a row
        if (name[0] == 'h')
            N->btn = (uint16_t)strtol(src, NULL, 10);
        return add_prop(e, node, name, src, repr, PK_LIT, 0);
    }
    if (kind == UIT_SPLIT && (!strcmp(name, "pad") || !strcmp(name, "gap") || !strcmp(name, "align"))) {
        static const char *const GAPS[] = {"s", "m", "l", "0"}, *const ALIGNS[] = {"start", "center", "end"};
        int i = name[0] == 'a' ? lit_index(src, ALIGNS, 3) : lit_index(src, GAPS, 4);
        if (i < 0)
            return uit_fail(e, UIT_E_KIND, UIT_MSG("split %s=%s", "%s %s"), name, src);
        if (name[0] == 'p')
            N->btn = (uint16_t)i;
        else if (name[0] == 'g')
            N->nbtn = (uint16_t)i;
        else
            N->lit = (uint8_t)i;
        return add_prop(e, node, name, src, repr, PK_LIT, i);
    }
    if (!strcmp(name, "mkind") && kind == UIT_ITEM) {
        N->lit = (uint8_t)!strcmp(src, "danger");
        return add_prop(e, node, name, src, repr, PK_LIT, N->lit);
    }
    if (!strcmp(name, "kind") && kind == UIT_BUTTON) {
        int i = lit_index(src, BKINDS, 5);
        N->lit = (uint8_t)(i < 0 ? 0 : i);
        return add_prop(e, node, name, src, repr, PK_LIT, i);
    }
    if (!strcmp(name, "field") && kind == UIT_TOGGLE) {
        // a toggle bound to a bool field of its list's row: the row's cell is its value
        int m = uit_model_of(e, node);
        int f = uit_field_find(e, m, src, strlen(src));
        if (f < 0 || e->slots[m].ftype[f] != UIT_BOOL)
            return uit_fail(e, UIT_E_NAME, UIT_MSG("toggle .%s: no bool field of its rows", "toggle .%s"), src);
        N->slot = (int16_t)m;
        N->cur = (int16_t)f;
        N->lit |= NL_FIELD;
        return 0;
    }
    if (!strcmp(name, "bind")) {
        int s = uit_slot_find(e, src, strlen(src));
        int t = s < 0 ? 0 : e->slots[s].type;
        int ok = kind == UIT_TOGGLE ? t == UIT_BOOL : UIT_IS_LIST(kind) ? t == UIT_MODEL
                 : kind == UIT_TABS ? t == UIT_ENUM
                 : kind == UIT_SEG ? t == UIT_ENUM || t == UIT_ROWOF : kind == UIT_SLIDER ? t == UIT_INT
                 : kind == UIT_FIELD ? t == UIT_TEXT : kind == UIT_TEXT_VIEW ? t == UIT_TEXT || t == UIT_MODEL : 0;
        if (!ok)
            return uit_fail(e, UIT_E_NAME, UIT_MSG("%s bound to %s: no such state of its type", "%s bound to %s"), uit_kind_names[kind], src);
        N->slot = (int16_t)s;
        return 0;               // the head, not a prop the dump lists
    }
    if (!strcmp(name, "labels") && (kind == UIT_SEG || kind == UIT_TABS)) {
        for (int i = 0; i < e->nlabels; i++)
            if (!strcmp(uit_str(e, e->labels[i].name), src)) {
                N->cur = (int16_t)i;
                return add_prop(e, node, name, src, repr, PK_LIT, i);
            }
        return uit_fail(e, UIT_E_NAME, UIT_MSG("%s labels=%s: no label table of that name", "%s labels=%s"),
                        uit_kind_names[kind], src);
    }
    if (!strcmp(name, "look") && (kind == UIT_SEG || kind == UIT_SLIDER || kind == UIT_PROGRESS)) {
        if (!strcmp(src, kind == UIT_SEG ? "cycle" : kind == UIT_SLIDER ? "steps" : "count"))
            N->lit |= kind == UIT_SEG ? NL_CYCLE : NL_STEPS;
        return add_prop(e, node, name, src, repr, PK_LIT, N->lit);
    }
    if (kind == UIT_FIELD && (!strcmp(name, "max") || !strcmp(name, "commit") || !strcmp(name, "secret"))) {
        if (name[0] == 'm')
            N->btn = (uint16_t)strtol(src, NULL, 10);
        else if (!strcmp(src, "True"))
            N->lit |= name[0] == 'c' ? NL_COMMIT : NL_SECRET;
        return add_prop(e, node, name, src, repr, PK_LIT, 0);
    }
    if ((!strcmp(name, "on") && (kind == UIT_TOGGLE || kind == UIT_SEG || kind == UIT_SLIDER ||
                                 kind == UIT_FIELD || kind == UIT_TABLE)) ||
        (!strcmp(name, "changed") && kind == UIT_FIELD)) {
        for (int a = 0; a < e->nacts; a++)
            if (!strcmp(uit_str(e, e->acts[a].name), src)) {
                if (name[0] == 'o')
                    N->on = (int16_t)a;
                else
                    N->changed = (int16_t)a;
                return add_prop(e, node, name, src, repr, PK_ACT, a);
            }
        return uit_fail(e, UIT_E_NAME, UIT_MSG("%s %s=%s: no such action", "%s %s=%s"), uit_kind_names[kind], name, src);
    }
    if (kind == UIT_TEXT_VIEW && !strcmp(name, "field")) {
        // a view of a model: each row's line, this text field
        int f = N->slot >= 0 ? uit_field_find(e, N->slot, src, strlen(src)) : -1;
        if (f < 0 || e->slots[N->slot].ftype[f] != UIT_TEXT)
            return uit_fail(e, UIT_E_NAME, UIT_MSG("text_view .%s: no text field of its model", "text_view .%s"), src);
        N->cur = (int16_t)f;
        return 0;
    }
    if (kind == UIT_TEXT_VIEW && (!strcmp(name, "follow") || !strcmp(name, "wrap") || !strcmp(name, "face") ||
                                  !strcmp(name, "syntax"))) {
        static const char *const SYNTAXES[] = {"plain", "python", "lua", "json", "sgr", "markdown"};
        if (name[0] == 's') {
            int i = lit_index(src, SYNTAXES, 6);
            N->btn = (uint16_t)(i < 0 ? 4 : i);
        } else if (name[0] == 'f' && name[1] == 'o') {
            N->lit = (uint8_t)((N->lit & ~TV_FOLLOW) | (!strcmp(src, "True") ? TV_FOLLOW : 0));
        } else if (name[0] == 'w') {
            N->lit = (uint8_t)((N->lit & ~TV_WRAP) | (!strcmp(src, "word") ? TV_WRAP : 0));
        } else {
            N->lit = (uint8_t)((N->lit & ~TV_PROSE) | (!strcmp(src, "ui") ? TV_PROSE : 0));
        }
        return add_prop(e, node, name, src, repr, PK_LIT, N->lit);
    }
    if (!strcmp(name, "current") && kind == UIT_ROWS) {
        int s = uit_slot_find(e, src, strlen(src));
        int t = s < 0 ? 0 : e->slots[s].type;
        if (t != UIT_TEXT && t != UIT_ROWOF && t != UIT_INT)
            return uit_fail(e, UIT_E_NAME, UIT_MSG("rows current=%s: no text, int or row_of of that name", "rows current=%s"), src);
        N->current = (int16_t)s;
        return add_prop(e, node, name, src, repr, PK_LIT, s);
    }
    if (!strcmp(name, "cur") && UIT_IS_LIST(kind)) {
        int s = uit_slot_find(e, src, strlen(src));
        if (s < 0 || e->slots[s].type != UIT_ROWOF)
            return uit_fail(e, UIT_E_NAME, UIT_MSG("%s cur=%s: no row_of of that name", "%s cur=%s"),
                            uit_kind_names[kind], src);
        N->cur = (int16_t)s;
        return add_prop(e, node, name, src, repr, PK_LIT, s);
    }
    if (!strcmp(name, "changed") && UIT_IS_LIST(kind)) {
        for (int a = 0; a < e->nacts; a++)
            if (!strcmp(uit_str(e, e->acts[a].name), src)) {
                N->changed = (int16_t)a;
                return add_prop(e, node, name, src, repr, PK_ACT, a);
            }
        return uit_fail(e, UIT_E_NAME, UIT_MSG("%s changed=%s: no such action", "%s changed=%s"),
                        uit_kind_names[kind], src);
    }
    if (!strcmp(name, "cols") && kind == UIT_TABLE)
        return add_prop(e, node, name, src, repr, PK_LIT, 0);      // a column's header, one prop each
    if (!strcmp(name, "look") && kind == UIT_FOOT) {
        // "share": its buttons share its width; the end look is not drawn yet
        if (strcmp(src, "share"))
            return uit_fail(e, UIT_E_KIND, UIT_MSG("foot look=%s: only share is drawn yet", "foot look=%s"), src);
        N->lit = 1;
        return add_prop(e, node, name, src, repr, PK_LIT, 1);
    }
    if (!strcmp(name, "buttons") && kind == UIT_DIALOG)
        return add_prop(e, node, name, src, repr, PK_LIT, 0);
    return uit_fail(e, UIT_E_KIND, UIT_MSG("%s=: a prop this engine does not draw on this kind (%d)", "%s= %d"), name, kind);
}

int uit_node_action(uit_t *e, int node, int action)
{
    if (node < 0 || node >= e->nnodes || action < 0 || action >= e->nacts)
        return UIT_E_ARG;
    uit_node_t *N = &e->nodes[node];
    if (N->kind == UIT_DIALOG) {
        if (UIT_GROW(e, btns, nbtns, cbtns))
            return UIT_E_NOMEM;
        if (N->nbtn == 0)
            N->btn = (uint16_t)e->nbtns;
        else if (N->btn + N->nbtn != e->nbtns)
            return UIT_E_ARG;
        e->btns[e->nbtns++] = (int16_t)action;
        N->nbtn++;
        return 0;
    }
    N->act = (int16_t)action;
    return 0;
}

static int names_engine_value(const char *src)
{
    return strstr(src, "@focus") || strstr(src, "@input") || strstr(src, "@touch");
}

// What the page's gen counts (`_cell_gens`, `_gen_safe`): every state but a
// list's cur= that nothing else reads (focus, which the toolkit draws); and
// whether it may carry one: no text of the build names an engine value.
static void gen_of_build(uit_t *e)
{
    e->gen_safe = 1;
    for (int t = e->m_tmpls; t < e->ntmpls; t++)
        if (names_engine_value(uit_str(e, e->tmpls[t].src)))
            e->gen_safe = 0;
    for (int c = e->m_conds; c < e->nconds; c++)
        if (names_engine_value(uit_str(e, e->conds[c].src)))
            e->gen_safe = 0;
    for (int i = 0; i < e->nslots; i++)
        e->slots[i].in_gen = 1;
    for (int k = 0; k < e->nnodes; k++) {
        int ck = e->nodes[k].cur;
        if (!UIT_IS_LIST(e->nodes[k].kind) || ck < 0)
            continue;
        int read = 0;
        for (int o = 0; o < e->nops && !read; o++)
            read = (e->ops[o].rk == RK_SLOT || e->ops[o].rk == RK_ROWOF) && e->ops[o].ref == ck;
        for (int t = 0; t < e->nterms && !read; t++)
            read = (e->terms[t].rk == RK_SLOT || e->terms[t].rk == RK_ROWOF) && e->terms[t].ref == ck;
        for (int j = 0; j < e->nnodes && !read; j++)
            read = !UIT_IS_LIST(e->nodes[j].kind) && e->nodes[j].slot == ck;
        if (!read)
            e->slots[ck].in_gen = 0;
    }
}

int uit_build_end(uit_t *e)
{
    for (int k = 0; k < e->nnodes; k++)
        if (e->nodes[k].kind == UIT_FOOT && !e->nodes[k].lit)
            return uit_fail(e, UIT_E_KIND, UIT_MSG("foot look=end: only share is drawn yet", "foot look=end"));
    e->gen++;
    e->fresh = 1;
    gen_of_build(e);
    return e->nnodes ? 0 : uit_fail(e, UIT_E_ARG, UIT_MSG("a build with no page", ""));
}

void uit_on_draw(uit_t *e, uit_draw_fn fn, void *ud)
{
    e->draw_fn = fn;
    e->draw_ud = ud;
}

void uit_open(uit_t *e)
{
    e->nids = e->nprev = 0;
    e->has_focused = 0;
    e->focus_key[0] = 0;
    uit_want_focus(e, NULL);
    for (int i = 0; i < e->nslots; i++) {
        UIT_FREE(e->slots[i].sync);
        e->slots[i].sync = NULL;
        e->slots[i].sync_set = 0;
    }
    for (int i = 0; i < e->norders; i++)
        e->orders[i].has = 0;
    for (int i = 0; i < e->ndrafts; i++)
        e->drafts[i].has = 0;
    e->nevs = e->evat = 0;
    e->evtext.n = 0;
    free_drew(e);
}

void uit_want_focus(uit_t *e, const char *key)
{
    UIT_FREE(e->want);
    e->want = NULL;
    if (key) {
        size_t n = strlen(key);
        e->want = UIT_MALLOC(n + 1);
        if (e->want)
            memcpy(e->want, key, n + 1);
    }
}

// -- events and the trace ---------------------------------------------------------

int uit_event(uit_t *e, int kind, int a, int b, int c, const char *text, size_t n)
{
    if (UIT_GROW(e, evs, nevs, cevs))
        return UIT_E_NOMEM;
    int at = uit_buf_put(&e->evtext, text ? text : "", text ? n : 0);
    uit_ev_t E = {(uint8_t)kind, (int16_t)a, (int16_t)b, (int16_t)c, (uint32_t)(at < 0 ? 0 : at)};
    e->evs[e->nevs++] = E;
    return 0;
}

int uit_next(uit_t *e, int32_t ev[3], char *buf, size_t cap)
{
    if (e->evat >= e->nevs) {
        e->nevs = e->evat = 0;
        e->evtext.n = 0;
        return UIT_EV_NONE;
    }
    const uit_ev_t *E = &e->evs[e->evat++];
    ev[0] = E->a;
    ev[1] = E->b;
    ev[2] = E->c;
    if (buf && cap) {
        const char *t = e->evtext.p + E->text;
        size_t n = strlen(t);
        if (n >= cap)
            n = cap - 1;
        memcpy(buf, t, n);
        buf[n] = 0;
    }
    return E->kind;
}

void uit_trace(uit_t *e, const char *kind, const char *name, const char *fmt, ...)
{
    if (!(e->flags & UIT_F_TRACE))
        return;
    uit_buf_t *b = &e->trace;
    uit_buf_printf(b, "%s\x01%s\x01", kind, name);
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof tmp - 1)
        n = sizeof tmp - 1;
    uit_buf_put(b, tmp, n < 0 ? 0 : (size_t)n);
}

int uit_trace_next(uit_t *e, char *buf, size_t cap)
{
    if (e->trace_at >= e->trace.n) {
        e->trace.n = e->trace_at = 0;
        return -1;
    }
    const char *t = e->trace.p + e->trace_at;
    size_t n = strlen(t);
    e->trace_at += (uint32_t)n + 1;
    if (n >= cap)
        n = cap - 1;
    memcpy(buf, t, n);
    buf[n] = 0;
    return (int)n;
}

// -- the dump ---------------------------------------------------------------------

static void dump_node(uit_t *e, uit_buf_t *b, int k, int depth, int model, int row)
{
    const uit_node_t *N = &e->nodes[k];
    for (int i = 0; i < depth; i++)
        uit_buf_printf(b, "  ");
    uit_buf_printf(b, "%s ", uit_kind_names[N->kind]);
    const char *key = uit_str(e, N->key);
    uit_buf_repr(b, key, strlen(key));
    if (N->act >= 0) {
        const uit_act_t *A = &e->acts[N->act];
        uit_buf_printf(b, "  action=%s", uit_str(e, A->name));
        if (!uit_act_can(e, N->act, -1, -1))
            uit_buf_printf(b, "(off: %s)", uit_str(e, e->conds[A->when].src));
    }
    if ((N->kind == UIT_TOGGLE || N->kind == UIT_SLIDER || N->kind == UIT_FIELD) && N->slot >= 0) {
        const uit_slot_t *S = &e->slots[N->slot];
        uit_buf_printf(b, "  bind=%s:%s = ", uit_str(e, S->name), uit_type_name(S->type));
        if (N->kind == UIT_FIELD && (N->lit & NL_SECRET)) {
            uit_buf_printf(b, "'");
            for (uint32_t i = 0; i < S->sn; i++)
                if ((S->s[i] & 0xC0) != 0x80)
                    uit_buf_printf(b, "*");
            uit_buf_printf(b, "'");
        } else {
            uit_slot_repr(e, b, N->slot);
        }
    }
    for (int p = N->props; p != (int)UIT_NONE; p = e->props[p].next) {
        const uit_prop_t *P = &e->props[p];
        const char *name = uit_str(e, P->name), *src = uit_str(e, P->src);
        if (P->kind == PK_LIT && (!strcmp(name, "buttons") || !strcmp(name, "labels")))
            continue;           // the dialog's are listed after; a seg's table is in its words
        uit_buf_printf(b, "  %s=", name);
        if (P->kind == PK_ACT) {
            uit_buf_printf(b, "%s", src);
        } else if (P->kind == PK_COND) {
            uit_buf_repr(b, src, strlen(src));
            uit_buf_printf(b, " = %s", uit_cond_eval(e, P->idx, model, row) ? "True" : "False");
        } else if (P->kind == PK_TMPL && P->repr != UIT_NONE && !strchr(src, '{')) {
            uit_buf_printf(b, "%s", uit_str(e, P->repr));
        } else if (P->kind == PK_TMPL) {
            uint32_t n;
            uint32_t at = uit_tmpl_render(e, P->idx, model, row, &n);
            if (strchr(src, '{')) {
                uit_buf_repr(b, src, strlen(src));
                uit_buf_printf(b, " = ");
            }
            uit_buf_repr(b, e->scratch.p + at, n);
        } else if (P->repr != UIT_NONE) {
            uit_buf_printf(b, "%s", uit_str(e, P->repr));
        } else {
            uit_buf_repr(b, src, strlen(src));
        }
    }
    if (N->kind == UIT_DIALOG) {
        uit_buf_printf(b, "  buttons=");
        for (int i = 0; i < N->nbtn; i++)
            uit_buf_printf(b, "%s%s", i ? "," : "", uit_str(e, e->acts[e->btns[N->btn + i]].name));
    }
    uit_buf_printf(b, "\n");
    if (N->kind == UIT_ROWS) {
        const uit_slot_t *M = &e->slots[N->slot];
        for (int i = 0; i < depth; i++)
            uit_buf_printf(b, "  ");
        uit_buf_printf(b, "  [%d rows of %s]\n", M->nrows, uit_str(e, M->name));
        for (int r = 0; r < M->nrows; r++) {
            for (int i = 0; i < depth; i++)
                uit_buf_printf(b, "  ");
            uit_buf_printf(b, "  row ");
            const char *rk = uit_cell(e, N->slot, r, M->key);
            uit_buf_repr(b, rk, strlen(rk));
            uit_buf_printf(b, "\n");
            for (int c = N->kid; c != (int)UIT_NONE; c = e->nodes[c].next)
                dump_node(e, b, c, depth + 2, N->slot, r);
        }
        return;
    }
    for (int c = N->kid; c != (int)UIT_NONE; c = e->nodes[c].next)
        dump_node(e, b, c, depth + 1, model, row);
}

int uit_dump(uit_t *e, char *buf, size_t cap)
{
    uit_buf_t b = {0};
    e->scratch.n = 0;
    if (e->nnodes)
        dump_node(e, &b, 0, 0, -1, -1);
    size_t n = b.n;
    if (n && b.p[n - 1] == '\n')
        n--;
    if (buf && cap) {
        size_t m = n < cap - 1 ? n : cap - 1;
        memcpy(buf, b.p, m);
        buf[m] = 0;
    }
    uit_buf_free(&b);
    return (int)n;
}

int uit_stats(const uit_t *e, int32_t out[8])
{
    out[0] = e->nnodes;
    out[1] = e->nnodes * (int32_t)sizeof(uit_node_t);
    out[2] = e->ntmpls * (int32_t)sizeof(uit_tmpl_t) + e->nops * (int32_t)sizeof(uit_op_t);
    out[3] = e->nconds * (int32_t)sizeof(uit_cond_t) + e->nterms * (int32_t)sizeof(uit_term_t);
    out[4] = (int32_t)e->pool.n;
    out[5] = e->nprops * (int32_t)sizeof(uit_prop_t);
    int32_t st = e->nslots * (int32_t)sizeof(uit_slot_t);
    for (int i = 0; i < e->nslots; i++)
        st += (int32_t)(e->slots[i].scap + e->slots[i].cells_n + 4u * (uint32_t)(e->slots[i].nrows * e->slots[i].nf));
    out[6] = st;
    out[7] = (int32_t)sizeof(uit_t) + e->cids * 2 * (int32_t)sizeof(uit_id_t) + (int32_t)e->scratch.cap;
    return 0;
}
