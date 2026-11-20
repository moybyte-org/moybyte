// The frame: the page walked through the toolkit's verbs, call for call as
// the reference replay makes them (runtime/uitree/uitree_app.py's
// `Engine._replay_page` and its `_r_*`), with focus, the hints band, a
// list's cursor and the queue of what the user did.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_text.h"
#include "moy_uitree_int.h"

#define NODE(k) (&e->nodes[k])
#define KIDS(k, c) for (int c = NODE(k)->kid; c != (int)UIT_NONE; c = NODE(c)->next)

static moy_ui_span_t span_of(const char *p, size_t n)
{
    moy_ui_span_t s = {(const uint8_t *)p, (uint16_t)n};
    return s;
}

static moy_ui_span_t sspan(uit_t *e, uint32_t at, uint32_t n)
{
    return span_of(e->scratch.p + at, n);
}

// -- actions and shown ------------------------------------------------------------

int uit_act_can(uit_t *e, int a, int model, int row)
{
    const uit_act_t *A = &e->acts[a];
    if (A->row >= 0 && row < 0) {
        // row=: off while its row_of names no row of its model
        const uit_slot_t *R = &e->slots[A->row];
        if (uit_row_find(e, R->model, R->s ? R->s : "", R->sn) < 0)
            return 0;
    }
    return A->when < 0 || uit_cond_eval(e, A->when, model, row);
}

int uit_can(uit_t *e, int a, int row)
{
    if (a < 0 || a >= e->nacts)
        return 0;
    return uit_act_can(e, a, -1, row);
}

int uit_letter(uit_t *e, int ch)
{
    if (ch >= 'A' && ch <= 'Z')
        ch += 32;
    if (ch <= 0 || ch > 127 || uit_dialog(e) >= 0)
        return -1;
    for (int a = 0; a < e->nacts; a++)
        if (strchr(uit_str(e, e->acts[a].letters), ch) && uit_act_can(e, a, -1, -1))
            return a;
    return -1;
}

int uit_shown(uit_t *e, int node, int model, int row)
{
    const uit_node_t *N = NODE(node);
    return N->shown < 0 || uit_cond_eval(e, N->shown, model, row);
}

static int is_stop(int kind)
{
    return kind == UIT_LINK || kind == UIT_BUTTON || kind == UIT_TOGGLE || kind == UIT_SEG ||
           kind == UIT_SLIDER || kind == UIT_FIELD || kind == UIT_ITEM || kind == UIT_TABS ||
           kind == UIT_CARD;
}

int uit_case_of(uit_t *e, int k)
{
    const uit_slot_t *S = &e->slots[NODE(k)->slot];
    const char *v = S->type == UIT_BOOL ? (S->i ? "True" : "False") : S->s ? S->s : "";
    size_t vn = S->type == UIT_BOOL ? strlen(v) : S->sn;
    int other = -1;
    KIDS(k, c) {
        const char *cv = uit_str(e, NODE(c)->key) + 5;     // "case:VALUE"
        if (strlen(cv) == vn && !memcmp(cv, v, vn))
            return c;
        if (other < 0 && !strcmp(cv, "_"))
            other = c;
    }
    return other;
}

static const char *row_key(uit_t *e, int model, int row)
{
    return model < 0 || row < 0 ? "" : uit_cell(e, model, row, e->slots[model].key);
}

// The id node `k` draws under on row `row`: KEY or ROWKEY/KEY, cut to the
// toolkit's length.
static void node_id(uit_t *e, int k, int model, int row, char out[UIT_ID_MAX + 1])
{
    if (row < 0)
        snprintf(out, UIT_ID_MAX + 1, "%s", uit_str(e, NODE(k)->key));
    else
        snprintf(out, UIT_ID_MAX + 1, "%s/%s", row_key(e, model, row), uit_str(e, NODE(k)->key));
}

// -- the trace's "drew": a text prop's value, compared with the last drawn ----

static void drew(uit_t *e, int k, int t, int model, int row, const char *v, size_t n)
{
    if (!(e->flags & UIT_F_TRACE))
        return;
    int prop = t;
    const char *src = uit_str(e, e->tmpls[t].src);
    if (!strchr(src, '{'))
        return;
    const char *rk = row_key(e, model, row);
    uit_drew_t *D = NULL;
    for (int i = 0; i < e->ndrew; i++)
        if (e->drew[i].node == k && e->drew[i].prop == prop && !strcmp(e->drew[i].rowkey, rk)) {
            D = &e->drew[i];
            break;
        }
    if (D && (strlen(D->val) != n || memcmp(D->val, v, n))) {
        uit_buf_t b = {0};
        if (row >= 0)
            uit_buf_printf(&b, "'%s/%s' ", rk, uit_str(e, NODE(k)->key));
        uit_buf_repr(&b, src, strlen(src));
        uit_buf_printf(&b, " -> ");
        uit_buf_repr(&b, v, n);
        uit_trace(e, "drew", uit_kind_names[NODE(k)->kind], "%s", b.p);
        uit_buf_free(&b);
    }
    if (!D) {
        if (uit_grow((void **)&e->drew, &e->cdrew, e->ndrew + 1, sizeof *e->drew))
            return;
        D = &e->drew[e->ndrew++];
        D->node = (int16_t)k;
        D->prop = (int16_t)prop;
        D->rowkey = UIT_MALLOC(strlen(rk) + 1);
        if (D->rowkey)
            strcpy(D->rowkey, rk);
        D->val = NULL;
    }
    UIT_FREE(D->val);
    D->val = UIT_MALLOC(n + 1);
    if (D->val) {
        memcpy(D->val, v, n);
        D->val[n] = 0;
    }
}

// A text prop now (`_t`), template `t` of node `k` (-1: none given): an
// action's label for the main text a link or button names none of.
static uint32_t text_t(uit_t *e, int k, int t, int model, int row, uint32_t *n)
{
    const uit_node_t *N = NODE(k);
    if (t < 0) {
        const char *s = t == N->t_main && N->act >= 0 ? uit_str(e, e->acts[N->act].label) : "";
        *n = (uint32_t)strlen(s);
        return (uint32_t)uit_buf_put(&e->scratch, s, *n);
    }
    uint32_t at = uit_tmpl_render(e, t, model, row, n);
    drew(e, k, t, model, row, e->scratch.p + at, *n);
    return at;
}

// The main text (prop 0) or the value (prop 1).
static uint32_t text(uit_t *e, int k, int prop, int model, int row, uint32_t *n)
{
    return text_t(e, k, prop == 0 ? NODE(k)->t_main : NODE(k)->t_value, model, row, n);
}

// -- ids --------------------------------------------------------------------------

static uit_id_t *id_find(uit_id_t *ids, int n, const char *id)
{
    for (int i = 0; i < n; i++)
        if (!strcmp(ids[i].id, id))
            return &ids[i];
    return NULL;
}

static void id_put(uit_t *e, int k, int model, int row, const char *id)
{
    uit_id_t *I = id_find(e->ids, e->nids, id);
    if (I) {
        if (I->node != k)
            e->dup = 1;
    } else {
        if (uit_grow((void **)&e->ids, &e->cids, e->nids + 1, sizeof *e->ids))
            return;
        I = &e->ids[e->nids++];
        strcpy(I->id, id);
    }
    I->node = (int16_t)k;
    I->row = (int16_t)row;
    I->act = NODE(k)->act;
    I->kind = NODE(k)->kind;
    I->gen = e->gen;
    (void)model;
}

// The override a stop draws with (`_o`): its id, `disabled` where its
// action is greyed off, its icon given to the toolkit.
static void over_for(uit_t *e, int k, int model, int row, moy_ui_ctx_t *c, moy_ui_over_t *o, char id[UIT_ID_MAX + 1])
{
    moy_ui_over_none(o);
    node_id(e, k, model, row, id);
    id_put(e, k, model, row, id);
    moy_ui_over_text(o, MOY_UI_OV_ID, span_of(id, strlen(id)));
    const uit_node_t *N = NODE(k);
    if (N->act >= 0 && e->acts[N->act].off == 2 && !uit_act_can(e, N->act, model, row)) {
        int32_t one = 1;
        moy_ui_over_put(o, MOY_UI_OV_DISABLED, &one);
    }
    if (row >= 0) {
        int list = uit_list_of(e, k);
        if (list >= 0 && NODE(list)->current >= 0) {
            int32_t on = uit_row_current(e, list, model, row);
            moy_ui_over_put(o, MOY_UI_OV_CURRENT, &on);
        }
    }
    if (N->t_icon >= 0) {
        uint32_t n, at = uit_tmpl_render(e, N->t_icon, model, row, &n);
        if (n)
            moy_ui_api_icon(c, sspan(e, at, n));
    }
}

// -- events -----------------------------------------------------------------------

static void fire(uit_t *e, int a, int k, int model, int row, const char *how)
{
    uit_buf_t b = {0};
    char id[UIT_ID_MAX * 2 + 2];
    if (row < 0)
        snprintf(id, sizeof id, "%s", uit_str(e, NODE(k)->key));
    else
        snprintf(id, sizeof id, "%s/%s", row_key(e, model, row), uit_str(e, NODE(k)->key));
    uit_buf_printf(&b, "%s ", how ? how : uit_kind_names[NODE(k)->kind]);
    uit_buf_repr(&b, id, strlen(id));
    uit_buf_printf(&b, " on ");
    uint32_t n, at = uit_tmpl_render(e, NODE(0)->t_main, -1, -1, &n);
    uit_buf_repr(&b, e->scratch.p + at, n);
    uit_buf_printf(&b, "\x02%s", id);           // the node that fired it (ui.push's opener)
    uit_event(e, UIT_EV_ACTION, a, model, row, b.p, b.n);
    uit_buf_free(&b);
}

void uit_cur_moved(uit_t *e, int node, const char *v, size_t n, const char *why)
{
    int ck = NODE(node)->cur;
    uit_slot_t *S = &e->slots[ck];
    uit_buf_t b = {0};
    uit_buf_repr(&b, S->s ? S->s : "", S->sn);
    uit_buf_printf(&b, " -> ");
    uit_buf_repr(&b, v, n);
    uit_buf_printf(&b, "   (%s)", why);
    uit_set_text(e, ck, v, n);
    UIT_FREE(S->sync);
    S->sync = UIT_MALLOC(n + 1);
    if (S->sync) {
        memcpy(S->sync, v, n);
        S->sync[n] = 0;
    }
    S->sync_set = 1;
    uit_trace(e, "state", uit_str(e, S->name), "%s", b.p);
    uit_buf_free(&b);
    uit_event(e, UIT_EV_WRITE, ck, -1, -1, NULL, 0);
    int ch = NODE(node)->changed;
    if (ch >= 0) {
        int m = NODE(node)->slot, r = uit_row_find(e, m, v, n);
        if (r >= 0) {
            uit_buf_t s = {0};
            uit_buf_printf(&s, "%s ", uit_kind_names[NODE(node)->kind]);
            const char *key = uit_str(e, NODE(node)->key);
            uit_buf_repr(&s, key, strlen(key));
            uit_buf_printf(&s, " cur moved");
            uit_event(e, UIT_EV_ACTION, ch, m, r, s.p, s.n);
            uit_buf_free(&s);
        }
    }
}

// G15's removal rule: the current row gone, the next row in the old order
// that is still there, else the one before it, else none.
void uit_after_removal(uit_t *e, int node)
{
    const uit_node_t *N = NODE(node);
    int m = N->slot;
    const uit_slot_t *M = &e->slots[m];
    const char *lkey = uit_str(e, N->key);
    uit_order_t *O = NULL;
    for (int i = 0; i < e->norders; i++)
        if (!strcmp(e->orders[i].key, lkey))
            O = &e->orders[i];
    if (O && O->has && O->ver == M->ver && O->slot == m)
        return;                 // its rows as last taken: a frame that pushed no model does no row's work
    // the new order
    uit_buf_t nb = {0};
    for (int r = 0; r < M->nrows; r++) {
        const char *k = uit_cell(e, m, r, M->key);
        uit_buf_put(&nb, k, strlen(k));
    }
    int had = O && O->has;
    char *old = had ? O->keys : NULL;
    uint32_t oldn = had ? O->n : 0;
    if (!O) {
        if (uit_grow((void **)&e->orders, &e->corders, e->norders + 1, sizeof *e->orders)) {
            uit_buf_free(&nb);
            return;
        }
        O = &e->orders[e->norders++];
        O->key = UIT_MALLOC(strlen(lkey) + 1);
        if (O->key)
            strcpy(O->key, lkey);
    }
    O->keys = nb.p;
    O->n = nb.n;
    O->has = 1;
    O->ver = M->ver;
    O->slot = (int16_t)m;
    if (N->cur < 0 || !had) {
        UIT_FREE(old);
        return;
    }
    const uit_slot_t *C = &e->slots[N->cur];
    const char *cur = C->s ? C->s : "";
    size_t cn = C->sn;
    int in_new = uit_row_find(e, m, cur, cn) >= 0, in_old = -1, i = 0;
    for (uint32_t p = 0; p < oldn; p += (uint32_t)strlen(old + p) + 1, i++)
        if (!strcmp(old + p, cur))
            in_old = i;
    if (in_new || in_old < 0) {
        UIT_FREE(old);
        return;
    }
    const char *nxt = NULL;
    i = 0;
    for (uint32_t p = 0; p < oldn; p += (uint32_t)strlen(old + p) + 1, i++)
        if (i > in_old && uit_row_find(e, m, old + p, strlen(old + p)) >= 0) {
            nxt = old + p;
            break;
        }
    if (!nxt) {
        i = 0;
        for (uint32_t p = 0; p < oldn; p += (uint32_t)strlen(old + p) + 1, i++)
            if (i < in_old && uit_row_find(e, m, old + p, strlen(old + p)) >= 0)
                nxt = old + p;
    }
    char why[96];
    uit_buf_t q = {0};
    uit_buf_repr(&q, cur, cn);
    snprintf(why, sizeof why, "removal of %s", q.p);
    uit_buf_free(&q);
    uit_cur_moved(e, node, nxt ? nxt : "", nxt ? strlen(nxt) : 0, why);
    UIT_FREE(e->slots[N->cur].sync);   // focus follows cur onto that row
    e->slots[N->cur].sync = NULL;
    e->slots[N->cur].sync_set = 1;
    UIT_FREE(old);
}

// -- two-way controls: the engine's writes ------------------------------------------

// The repr of text `s` into `b`, its characters starred for a secret field.
static void repr_of(uit_buf_t *b, const char *s, size_t n, int secret)
{
    if (!secret) {
        uit_buf_repr(b, s, n);
        return;
    }
    uit_buf_printf(b, "'");
    for (size_t i = 0; i < n; i++)
        if ((s[i] & 0xC0) != 0x80)
            uit_buf_printf(b, "*");
    uit_buf_printf(b, "'");
}

// Node `k`'s state written (`_two_way`): its trace line `was -> now   (how)`,
// the write's event, then the action `then` names (-1 none).
static void wrote(uit_t *e, int k, const char *was, const char *now, const char *how, int then)
{
    const uit_node_t *N = NODE(k);
    if (e->flags & UIT_F_TRACE)
        uit_trace(e, "state", uit_str(e, e->slots[N->slot].name), "%s -> %s   (%s)", was, now, how);
    uit_event(e, UIT_EV_WRITE, N->slot, -1, -1, NULL, 0);
    if (then >= 0)
        fire(e, then, k, -1, -1, NULL);
}

// A toggle bound to its row's bool field written (`_row_write`): the cell in
// place, the model's version, its trace line `KEY: FIELD old -> new   (from
// toggle .FIELD)`, the cell's event, then its on= handed the row.
static void row_wrote(uit_t *e, int k, int model, int row, int now)
{
    const uit_node_t *N = NODE(k);
    uit_slot_t *M = &e->slots[model];
    M->cells[M->at[row * M->nf + N->cur]] = now ? '1' : '0';
    M->ver++;
    if (e->flags & UIT_F_TRACE) {
        uit_buf_t key = {0};
        const char *kv = uit_cell(e, model, row, M->key), *f = uit_str(e, M->mf->fname[N->cur]);
        if (M->mf->ftype[M->key] == UIT_TEXT)
            uit_buf_repr(&key, kv, strlen(kv));
        else
            uit_buf_printf(&key, "%s", kv);
        uit_trace(e, "state", uit_str(e, M->name), "%s: %s %s -> %s   (from %s .%s)", key.p, f,
                  now ? "False" : "True", now ? "True" : "False", uit_kind_names[N->kind], f);
        uit_buf_free(&key);
    }
    uit_event(e, UIT_EV_CELL, model, row, N->cur, NULL, 0);
    if (N->on >= 0)
        fire(e, N->on, k, model, row, NULL);
}

// Node `k`'s text state written from `s`: the trace's reprs, the write, `then`.
static void wrote_text(uit_t *e, int k, const char *s, size_t n, const char *how, int then)
{
    uit_slot_t *S = &e->slots[NODE(k)->slot];
    int secret = NODE(k)->kind == UIT_FIELD && (NODE(k)->lit & NL_SECRET);
    uit_buf_t was = {0}, now = {0}, why = {0};
    if (e->flags & UIT_F_TRACE) {
        repr_of(&was, S->s ? S->s : "", S->sn, secret);
        repr_of(&now, s, n, secret);
        // how: "from" (a control's own write), "typed" or "field" (a commit)
        if (how[0] == 'f' && how[1] == 'r')
            uit_buf_printf(&why, "from %s ", uit_kind_names[NODE(k)->kind]);
        else
            uit_buf_printf(&why, how[0] == 't' ? "typed in field " : "field ");
        const char *key = uit_str(e, NODE(k)->key);
        uit_buf_repr(&why, key, strlen(key));
        if (how[0] == 'f' && how[1] == 'i')
            uit_buf_printf(&why, " committed");
    }
    uit_set_text(e, NODE(k)->slot, s, n);
    wrote(e, k, was.p ? was.p : "", now.p ? now.p : "", why.p ? why.p : "", then);
    uit_buf_free(&was);
    uit_buf_free(&now);
    uit_buf_free(&why);
}

static uit_draft_t *draft_find(uit_t *e, const char *key)
{
    for (int i = 0; i < e->ndrafts; i++)
        if (!strcmp(e->drafts[i].key, key))
            return &e->drafts[i];
    return NULL;
}

// Field `key`'s buffer, `cap` bytes at least.
static uit_draft_t *draft_of(uit_t *e, const char *key, size_t cap)
{
    uit_draft_t *D = draft_find(e, key);
    if (!D) {
        if (uit_grow((void **)&e->drafts, &e->cdrafts, e->ndrafts + 1, sizeof *e->drafts))
            return NULL;
        D = &e->drafts[e->ndrafts];
        memset(D, 0, sizeof *D);
        D->key = UIT_MALLOC(strlen(key) + 1);
        if (!D->key)
            return NULL;
        strcpy(D->key, key);
        e->ndrafts++;
    }
    if (cap < 1)
        cap = 1;
    if (D->cap < cap) {
        char *p = UIT_REALLOC(D->buf, cap);
        if (!p)
            return NULL;
        D->buf = p;
        D->cap = (uint16_t)cap;
    }
    return D;
}

const char *uit_draft(const uit_t *e, const char *key, size_t *n)
{
    uit_draft_t *D = draft_find((uit_t *)e, key);
    if (!D || !D->has)
        return NULL;
    *n = D->n;
    return D->buf;
}

// A commit field's draft written to its state (`_commit_field`): Enter, focus
// leaving it, every way out. changed= runs when the state moves.
static void commit_field(uit_t *e, int k)
{
    uit_draft_t *D = draft_find(e, uit_str(e, NODE(k)->key));
    if (!D || !D->has)
        return;
    D->has = 0;
    const uit_slot_t *S = &e->slots[NODE(k)->slot];
    if (D->n == S->sn && !memcmp(D->buf, S->s ? S->s : "", D->n))
        return;
    wrote_text(e, k, D->buf, D->n, "field", NODE(k)->changed);
}

int uit_leave(uit_t *e)
{
    for (int i = 0; i < e->nids; i++) {
        const uit_id_t *I = &e->ids[i];
        if (I->kind == UIT_FIELD && I->gen == e->gen && I->node >= 0 && I->node < e->nnodes)
            commit_field(e, I->node);
    }
    return e->nevs - e->evat;
}

int uit_stop(const uit_t *e, int i, char *buf, size_t cap)
{
    if (i < 0 || i >= e->nids)
        return -1;
    const uit_id_t *I = &e->ids[i];
    if (buf && cap)
        snprintf(buf, cap, "%s", I->id);
    int k = I->kind;
    if (k == UIT_SEG && I->gen == e->gen && (e->nodes[I->node].lit & NL_CYCLE))
        k = UIT_KINDS;
    return k;
}

int uit_focused(const uit_t *e, char *buf, size_t cap)
{
    if (buf && cap)
        snprintf(buf, cap, "%s", e->focus_key);
    return (int)strlen(e->focus_key);
}

// The items of a seg at the scratch's end, NUL-separated (`words`): their
// count into *count, the current one's index into *at (-1 none).
static uint32_t seg_items(uit_t *e, const uit_node_t *N, uint32_t *len, int *count, int *at)
{
    const uit_slot_t *S = &e->slots[N->slot];
    const char *cur = S->s ? S->s : "";
    uint32_t start = e->scratch.n;
    *at = -1;
    *count = 0;
    if (S->type == UIT_ENUM) {
        const char *v = uit_str(e, S->vals);
        for (int i = 0; i < S->nvals; i++) {
            size_t vn = strlen(v);
            if (vn == S->sn && !memcmp(v, cur, vn))
                *at = i;
            const char *w = N->cur >= 0 ? uit_label_word(e, N->cur, v, vn) : v;
            uit_buf_puts(&e->scratch, w);
            v += vn + 1;
        }
        *count = S->nvals;
        if (*at < 0)
            *at = 0;
    } else {
        int m = S->model;
        const uit_slot_t *M = &e->slots[m];
        for (int r = 0; r < M->nrows; r++) {
            const char *key = uit_cell(e, m, r, M->key);
            if (strlen(key) == S->sn && !memcmp(key, cur, S->sn))
                *at = r;
            uint32_t n;
            uit_tmpl_render(e, N->t_value, m, r, &n);
        }
        *count = M->nrows;
    }
    *len = e->scratch.n > start ? e->scratch.n - start - 1 : 0;
    return start;
}

// The value seg `N`'s item `i` stands for (an enum's value, a row's key).
static const char *seg_key(uit_t *e, const uit_node_t *N, int i)
{
    const uit_slot_t *S = &e->slots[N->slot];
    if (S->type == UIT_ENUM) {
        const char *v = uit_str(e, S->vals);
        while (i-- > 0)
            v += strlen(v) + 1;
        return v;
    }
    return uit_cell(e, S->model, i, e->slots[S->model].key);
}

// -- the stops, in the page's order -------------------------------------------------

typedef struct {
    int16_t node, row;
} stop_t;

typedef struct {
    stop_t *s;
    int n, cap;
} stops_t;

static void stop_put(stops_t *out, int node, int row)
{
    if (uit_grow((void **)&out->s, &out->cap, out->n + 1, sizeof *out->s))
        return;
    out->s[out->n].node = (int16_t)node;
    out->s[out->n].row = (int16_t)row;
    out->n++;
}

static void stops_of(uit_t *e, int k, int model, int row, stops_t *out)
{
    KIDS(k, c) {
        const uit_node_t *N = NODE(c);
        if (!uit_shown(e, c, model, row))
            continue;
        if (N->kind == UIT_ROWS || N->kind == UIT_CARDS) {
            for (int r = 0; r < e->slots[N->slot].nrows; r++)
                stops_of(e, c, N->slot, r, out);
        } else if (N->kind == UIT_TABLE) {
            if (e->slots[N->slot].nrows)
                stop_put(out, c, 0);        // one stop, as its first row
        } else if (N->kind == UIT_SWITCH) {
            int cs = uit_case_of(e, c);
            if (cs >= 0)
                stops_of(e, cs, model, row, out);
        } else if (N->kind == UIT_DIALOG || N->kind == UIT_POPOVER) {
            continue;
        } else if (is_stop(N->kind)) {
            if (N->act < 0 || uit_act_can(e, N->act, model, row))
                stop_put(out, c, row);
        } else {
            stops_of(e, c, model, row, out);
        }
    }
}

// The id node KEY (or ROWKEY/KEY) draws under this frame, as the walk will
// draw: shown, its action on, its row in the model. 0: it will not draw.
static int drawn_id(uit_t *e, int k, const char *rowkey, size_t rn, const char *nodekey, char out[UIT_ID_MAX + 1])
{
    KIDS(k, c) {
        const uit_node_t *N = NODE(c);
        if (!uit_shown(e, c, -1, -1))
            continue;
        if (N->kind == UIT_ROWS || N->kind == UIT_CARDS) {
            if (!rn)
                continue;
            int r = uit_row_find(e, N->slot, rowkey, rn);
            if (r < 0)
                continue;
            KIDS(c, g) {
                if (!strcmp(uit_str(e, NODE(g)->key), nodekey) && uit_shown(e, g, N->slot, r) &&
                    (NODE(g)->act < 0 || uit_act_can(e, NODE(g)->act, N->slot, r))) {
                    snprintf(out, UIT_ID_MAX + 1, "%.*s/%s", (int)rn, rowkey, nodekey);
                    return 1;
                }
            }
            continue;
        }
        if (N->kind == UIT_SWITCH) {
            int cs = uit_case_of(e, c);
            if (cs >= 0 && drawn_id(e, cs, rowkey, rn, nodekey, out))
                return 1;
            continue;
        }
        if (N->kind == UIT_TABLE) {
            if (rn || strcmp(uit_str(e, N->key), nodekey))
                continue;
            snprintf(out, UIT_ID_MAX + 1, "%s", nodekey);     // one stop, its key the id
            return 1;
        }
        if (N->act >= 0 && !uit_act_can(e, N->act, -1, -1))
            continue;
        if (!rn && !strcmp(uit_str(e, N->key), nodekey)) {
            snprintf(out, UIT_ID_MAX + 1, "%s", nodekey);
            return 1;
        }
        if (drawn_id(e, c, rowkey, rn, nodekey, out))
            return 1;
    }
    return 0;
}

static int drawn_key(uit_t *e, const char *key, char out[UIT_ID_MAX + 1])
{
    const char *slash = strrchr(key, '/');
    if (!slash)
        return drawn_id(e, 0, "", 0, key, out);
    return drawn_id(e, 0, key, (size_t)(slash - key), slash + 1, out);
}

// The key of the first node a fresh page's first frame draws whose focus= holds.
static int fresh_focus(uit_t *e, int k)
{
    KIDS(k, c) {
        const uit_node_t *N = NODE(c);
        if (!uit_shown(e, c, -1, -1) || N->kind == UIT_DIALOG || N->kind == UIT_POPOVER || UIT_IS_LIST(N->kind))
            continue;
        if (N->kind == UIT_SWITCH) {
            int cs = uit_case_of(e, c), got = cs >= 0 ? fresh_focus(e, cs) : -1;
            if (got >= 0)
                return got;
            continue;
        }
        if (N->focus >= 0 && uit_cond_eval(e, N->focus, -1, -1))
            return c;
        int got = fresh_focus(e, c);
        if (got >= 0)
            return got;
    }
    return -1;
}

int uit_dialog(uit_t *e)
{
    if (!e->nnodes)
        return -1;
    KIDS(0, c)
    if (NODE(c)->kind == UIT_DIALOG && uit_shown(e, c, -1, -1))
        return c;
    return -1;
}

// The shown popover at the page's root, or -1.
static int popover_of(uit_t *e)
{
    if (!e->nnodes)
        return -1;
    KIDS(0, c)
    if (NODE(c)->kind == UIT_POPOVER && uit_shown(e, c, -1, -1))
        return c;
    return -1;
}

int uit_back_popover(uit_t *e, char *buf, size_t cap)
{
    int p = popover_of(e);
    if (p < 0)
        return -2;
    if (buf && cap)
        snprintf(buf, cap, "%s", uit_str(e, NODE(p)->key));
    return NODE(p)->on;
}

int uit_unfocused(const uit_t *e)
{
    return !e->has_focused;
}

int uit_back_dialog(uit_t *e, char *buf, size_t cap)
{
    int d = uit_dialog(e);
    if (d < 0 || !NODE(d)->nbtn)
        return -1;
    if (buf && cap)
        snprintf(buf, cap, "%s", uit_str(e, NODE(d)->key));
    return e->btns[NODE(d)->btn];
}

// -- the hints band -------------------------------------------------------------------

static void hint_add(uit_buf_t *b, const char *s, size_t n)
{
    uit_buf_put(b, s, n);
}

static void hint_act(uit_t *e, uit_buf_t *b, int a)
{
    uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
    hint_add(b, e->scratch.p + at, n);
}

static int role_action(uit_t *e, int flag)
{
    for (int a = 0; a < e->nacts; a++)
        if (e->acts[a].flags & flag)
            return a;
    return -1;
}

// The band's items, NUL-separated into b (its length b->n, the last NUL
// included).
static void hints(uit_t *e, uit_buf_t *b, int pages)
{
    b->n = 0;
    int d = uit_dialog(e);
    if (d >= 0) {
        const uit_node_t *D = NODE(d);
        if (D->nbtn == 1) {
            hint_add(b, "back", 4);
            hint_act(e, b, e->btns[D->btn]);
        } else if (D->nbtn > 1) {
            hint_add(b, "confirm", 7);
            hint_act(e, b, e->btns[D->btn + D->nbtn - 1]);
            hint_add(b, "back", 4);
            hint_act(e, b, e->btns[D->btn]);
        }
        return;
    }
    int pop = popover_of(e);
    if (pop >= 0) {
        // a popover: Back closes it; what lies under it takes no keys
        const uit_id_t *F = &e->focused;
        int in = 0;
        for (int k = e->has_focused && F->gen == e->gen ? F->node : -1; k >= 0 && k != (int)UIT_NONE;
             k = NODE(k)->parent)
            if (k == pop)
                in = 1;
        if (in && F->act >= 0 && uit_act_can(e, F->act, -1, -1)) {
            hint_add(b, "confirm", 7);
            hint_act(e, b, F->act);
        }
        hint_add(b, "back", 4);
        if (NODE(pop)->on >= 0)
            hint_act(e, b, NODE(pop)->on);
        else
            hint_add(b, "Close", 5);
        return;
    }
    if (e->has_focused) {
        const uit_id_t *F = &e->focused;
        int a = F->act;
        int on = a >= 0 && uit_act_can(e, a, -1, -1);
        if (F->kind == UIT_FIELD && F->gen == e->gen && (NODE(F->node)->lit & NL_COMMIT)) {
            static const char DONE[] = "confirm\0Done\0cancel\0Cancel";
            uit_buf_put(b, DONE, sizeof DONE - 1);
            return;
        }
        if (F->kind == UIT_SEG || F->kind == UIT_SLIDER) {
            hint_add(b, "change", 6);
            hint_add(b, "Change", 6);
        }
        if (F->kind == UIT_TOGGLE) {
            int t = F->gen == e->gen && F->node >= 0 ? NODE(F->node)->t_aux : -1;
            hint_add(b, "confirm", 7);
            if (t >= 0) {
                int m = uit_model_of(e, F->node);
                uint32_t n, at = uit_tmpl_render(e, t, m, m >= 0 ? F->row : -1, &n);
                hint_add(b, e->scratch.p + at, n);
            } else {
                hint_add(b, "Change", 6);
            }
        } else if (F->kind == UIT_TABLE) {
            int t = F->gen == e->gen ? NODE(F->node)->t_value : -1;
            uint32_t n = 0, at = t >= 0 ? uit_tmpl_render(e, t, -1, -1, &n) : 0;
            if (n) {
                hint_add(b, "confirm", 7);
                hint_add(b, e->scratch.p + at, n);
            }
        } else if (on) {
            uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
            if (n) {
                hint_add(b, "confirm", 7);
                hint_add(b, e->scratch.p + at, n);
            }
        }
        if (F->kind == UIT_TEXT_VIEW) {
            hint_add(b, "scroll", 6);       // a text that runs on: the arrows scroll it
            hint_add(b, "Scroll", 6);
        }
    } else {
        int a = role_action(e, UIT_A_ROLE_CONFIRM);
        if (a >= 0 && uit_act_can(e, a, -1, -1)) {
            uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
            if (n) {
                hint_add(b, "confirm", 7);
                hint_add(b, e->scratch.p + at, n);
            }
        }
    }
    if ((e->flags & UIT_F_DESK) && e->tv_stop && !(e->has_focused && e->focused.kind == UIT_TEXT_VIEW)) {
        hint_add(b, "PgUp PgDn", 9);        // the page's text runs on: PgUp and PgDn page it
        hint_add(b, "Scroll", 6);
    }
    for (int a = 0; a < e->nacts; a++)
        if (e->acts[a].letter && uit_act_can(e, a, -1, -1)) {
            char up = (char)(e->acts[a].letter >= 'a' && e->acts[a].letter <= 'z'
                             ? e->acts[a].letter - 32 : e->acts[a].letter);
            hint_add(b, &up, 1);
            hint_act(e, b, a);
        }
    int a = pages == 1 ? role_action(e, UIT_A_ROLE_BACK) : -1;
    if (a < 0) {
        hint_add(b, "back", 4);
        hint_add(b, "Back", 4);
        return;
    }
    if (uit_act_can(e, a, -1, -1)) {
        uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
        if (n) {
            hint_add(b, "back", 4);
            hint_add(b, e->scratch.p + at, n);
        }
    }
}

// -- the walk ---------------------------------------------------------------------------

static void walk(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row);

// A field (`_r_field`): a commit field types into its draft, written by
// Enter or focus leaving it; another writes its state at each edit.
static void draw_field(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    moy_ui_over_t o;
    char id[UIT_ID_MAX + 1];
    uint32_t hn = 0, ha = 0, ln, la;
    over_for(e, k, model, row, c, &o, id);
    if (N->t_aux >= 0)
        ha = text_t(e, k, N->t_aux, model, row, &hn);
    la = text(e, k, 0, model, row, &ln);
    size_t max = N->btn;
    uit_draft_t *D = draft_of(e, uit_str(e, N->key), max);
    if (!D)
        return;
    const uit_slot_t *S = &e->slots[N->slot];
    int commit = (N->lit & NL_COMMIT) != 0;
    const char *cur = commit && D->has ? D->buf : (S->s ? S->s : "");
    size_t cn = commit && D->has ? D->n : S->sn;
    uint32_t was = (uint32_t)uit_buf_put(&e->scratch, cur, cn);
    size_t len = cn;
    if (len > max) {
        len = max;
        while (len > 0 && ((uint8_t)e->scratch.p[was + len] & 0xC0) == 0x80)
            len--;              // never half a character
    }
    memmove(D->buf, e->scratch.p + was, len);
    if (N->lit & NL_SECRET) {
        int32_t one = 1;
        moy_ui_over_put(&o, MOY_UI_OV_SECRET, &one);
    }
    if (N->t_aux >= 0)
        moy_ui_over_text(&o, MOY_UI_OV_HINT, sspan(e, ha, hn));
    int32_t r = moy_ui_api_field(c, s, sspan(e, la, ln), (uint8_t *)D->buf, max, len, &o);
    size_t nl = (size_t)(r & 0xffff);
    int done = (r & 0x10000) != 0;
    int moved = nl != cn || memcmp(D->buf, e->scratch.p + was, nl);
    if (commit) {
        if (moved) {
            D->has = 1;
            D->n = (uint16_t)nl;
            e->edits++;
        }
        if (done) {
            commit_field(e, k);
            if (N->on >= 0)
                fire(e, N->on, k, -1, -1, NULL);
        }
        return;
    }
    if (moved)
        wrote_text(e, k, D->buf, nl, "typed", N->changed);
    if (done && N->on >= 0)
        fire(e, N->on, k, -1, -1, NULL);
}

// A text view's lines: a model's rows (each its text field), or a text cut at its newlines (none
// for an empty text), where each starts kept while the text's version holds.
typedef struct {
    uit_t *e;
    int slot, f;
} tv_src_t;

static const uint8_t *tv_line(void *src, int32_t i, size_t *len)
{
    tv_src_t *t = src;
    uit_t *e = t->e;
    const uit_slot_t *S = &e->slots[t->slot];
    if (t->f >= 0) {
        const char *v = uit_cell(e, t->slot, i, t->f);
        *len = strlen(v);
        return (const uint8_t *)v;
    }
    uint32_t a = e->tvl[i], b = i + 1 < e->tvl_n ? e->tvl[i + 1] - 1 : S->sn;
    *len = b - a;
    return (const uint8_t *)S->s + a;
}

static int tv_lines(uit_t *e, int slot)
{
    const uit_slot_t *S = &e->slots[slot];
    if (e->tvl_slot == slot && e->tvl_ver == S->ver && e->tvl_s == S->s && e->tvl)
        return e->tvl_n;
    e->tvl_n = 0;
    if (S->sn) {
        for (uint32_t i = 0; i <= S->sn; i++) {
            if (i && S->s[i - 1] != '\n')
                continue;
            if (uit_grow((void **)&e->tvl, &e->tvl_cap, e->tvl_n + 1, sizeof *e->tvl))
                return e->tvl_n = 0;
            e->tvl[e->tvl_n++] = i;
        }
    }
    e->tvl_slot = (int16_t)slot, e->tvl_ver = S->ver, e->tvl_s = S->s;
    return e->tvl_n;
}

static void draw_text_view(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    moy_ui_over_t o;
    char id[UIT_ID_MAX + 1];
    tv_src_t src = {e, N->slot, N->cur};
    moy_ui_lines_t L = {&src, N->cur >= 0 ? e->slots[N->slot].nrows : tv_lines(e, N->slot), tv_line};
    int32_t opt[MOY_UI_TXO_LEN] = MOY_UI_TXO_DEFAULTS, out[2];
    opt[MOY_UI_TXO_SYNTAX] = N->btn;
    opt[MOY_UI_TXO_KEYS] = 1;           // a stop where it runs on
    opt[MOY_UI_TXO_FACE] = (N->lit & TV_PROSE) ? MOY_UI_TXF_UI : MOY_UI_TXF_MONO;
    opt[MOY_UI_TXO_WRAP] = (N->lit & TV_WRAP) != 0;
    opt[MOY_UI_TXO_FOLLOW] = (N->lit & TV_FOLLOW) != 0;
    over_for(e, k, model, row, c, &o, id);
    moy_ui_api_text_view(c, s, span_of(id, strlen(id)), &L, opt, NULL, &o, out);
    int32_t box[4];
    if (moy_ui_api_box(c, span_of(id, strlen(id)), box))
        e->tv_stop_now = 1;
}

// A label's rows as the toolkit's labels, drawn; or with `h`, measured (their steps added to *h).
// A label's rows drawn, or with `h` measured: each row's `part` of the measure (3 its height,
// 4 how far the stack moves past it) added to *h.
static void label_rows_m(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row, int32_t *h,
                         int part)
{
    const uit_node_t *N = NODE(k);
    moy_ui_over_t o;
    uint32_t n1, a1;
    if (N->t_icon >= 0 && !h) {
        uint32_t n, at = uit_tmpl_render(e, N->t_icon, model, row, &n);
        if (n)
            moy_ui_api_icon(c, sspan(e, at, n));
    }
    a1 = text(e, k, 0, model, row, &n1);
    int t = N->nbtn, opts = N->lit || (t & ~LT_CHAR);
    int lines = (t & LT_WRAP) == LT_WORD || (t & LT_LINES) ? (N->btn ? N->btn : 16) : 0;
    // wrap="char": lines of what fits the page's width, cut anywhere, each a label
    uint32_t per = 0;
    if ((t & LT_WRAP) == LT_CHAR) {
        moy_font_info_t fi;
        moy_ui_font(s->cart, MOY_UI_FONT_UI, &fi);
        int cw = fi.w * e->fs;
        per = (uint32_t)((e->pw - 32) / (cw > 1 ? cw : 1));
        if (per < 8)
            per = 8;
    }
    // `per` characters a line (a UTF-8 sequence is one); one line however long unwrapped,
    // none for an empty text cut by character (the reference's range over its length)
    if ((t & LT_WRAP) == LT_CHAR && !n1)
        return;
    const char *txt = e->scratch.p + a1;
    uint32_t at = 0;
    do {
        uint32_t m = at, chars = 0;
        while (m < n1 && chars < per) {
            m++;
            while (m < n1 && (txt[m] & 0xc0) == 0x80)
                m++;
            chars++;
        }
        if ((t & LT_WRAP) != LT_CHAR)
            m = n1;
        if (opts)
            moy_ui_api_text(c, lines, (t >> LT_ALIGN_SHIFT) & 7, N->lit, ((t >> LT_FACE_SHIFT) & 7) - 1);
        moy_ui_over_none(&o);
        if (h) {
            int32_t out[5];
            moy_ui_api_measure(c, s, MOY_UI_MEASURE_LABEL, sspan(e, a1 + at, m - at), opts ? &o : NULL, out);
            *h += out[part];
        } else {
            moy_ui_api_label(c, s, sspan(e, a1 + at, m - at), opts ? &o : NULL);
        }
        at = m;
    } while (at < n1);
}

static void label_rows(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row, int32_t *h)
{
    label_rows_m(e, c, s, k, model, row, h, 4);
}

// A gap's pixels: the handheld's at its font scale, the desk's as named (`_gap`).
static int gap_px(uit_t *e, int i)
{
    static const uint8_t GAPS[4][2] = {{6, 10}, {8, 16}, {16, 24}, {0, 0}};
    return (e->flags & UIT_F_DESK) ? GAPS[i & 3][1] : GAPS[i & 3][0] * e->fs;
}

// How tall a split's column draws, measured before it draws: -1 where it holds a kind with no
// measure, which lays it out from its cell's top (`_col_h`).
static int col_h(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    int32_t h = 0;
    KIDS(k, g) {
        const uit_node_t *N = NODE(g);
        if (!uit_shown(e, g, model, row) ||
            (N->act >= 0 && !uit_act_can(e, N->act, model, row) && e->acts[N->act].off != 2))
            continue;
        if (N->kind == UIT_LABEL) {
            label_rows(e, c, s, g, model, row, &h);
        } else if (N->kind == UIT_BUTTON) {
            uint32_t n, a = text(e, g, 0, model, row, &n);
            moy_ui_over_t o;
            moy_ui_over_none(&o);
            int32_t one = 1, out[5];
            moy_ui_over_put(&o, MOY_UI_OV_FIT, &one);
            moy_ui_api_measure(c, s, MOY_UI_MEASURE_BUTTON, sspan(e, a, n), &o, out);
            h += out[4];
        } else {
            return -1;
        }
    }
    return h;
}

static void walk(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row);
static void draw(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row);

// An int prop's value (template `t`, `dflt` where none is given).
static int32_t int_of(uit_t *e, int t, int model, int row, int32_t dflt)
{
    if (t < 0)
        return dflt;
    uint32_t n, mark = e->scratch.n, a = uit_tmpl_render(e, t, model, row, &n);
    int32_t v = (int32_t)strtol(e->scratch.p + a, NULL, 10);
    e->scratch.n = mark;
    return v;
}

// A size a build gives (w=, h=, side=): the handheld's at its font scale, the desk's as given.
static int32_t size_of(uit_t *e, int32_t v)
{
    return (e->flags & UIT_F_DESK) ? v : v * e->fs;
}

// A card's or a picture's marks (`ui.card`'s o): its icon tile where it names an icon, its tone,
// the lock where its tone is "locked".
static void card_mark(uit_t *e, moy_ui_ctx_t *c, const uit_node_t *N, int model, int row)
{
    static const char *const TONE_NAMES[] = {"ink", "dim", "value", "danger"};
    uint32_t tn = 0, ta = N->t_aux >= 0 ? uit_tmpl_render(e, N->t_aux, model, row, &tn) : 0;
    int tone = 0, locked = tn == 6 && !memcmp(e->scratch.p + ta, "locked", 6);
    for (int i = 0; i < 4 && tn && !locked; i++)
        if (strlen(TONE_NAMES[i]) == tn && !memcmp(e->scratch.p + ta, TONE_NAMES[i], tn))
            tone = i;
    if (N->t_icon < 0 && !tn)
        return;
    uint32_t in = 0, ia = N->t_icon >= 0 ? uit_tmpl_render(e, N->t_icon, model, row, &in) : 0;
    moy_ui_api_card_mark(c, span_of("", 0), locked ? 4 : tone, sspan(e, ia, in));
}

// A card of a cards list (`_r_cards`' per row): its id, its marks, its label and sub line.
static void draw_card(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    moy_ui_over_t o;
    char id[UIT_ID_MAX + 1];
    over_for(e, k, model, row, c, &o, id);
    card_mark(e, c, N, model, row);
    uint32_t n1, n2, a1 = text(e, k, 0, model, row, &n1), a2 = text(e, k, 1, model, row, &n2);
    int32_t out[5];
    moy_ui_api_card(c, s, sspan(e, a1, n1), sspan(e, a2, n2), &o, out);
    if ((out[4] & 2) && N->act >= 0 && uit_act_can(e, N->act, model, row))
        fire(e, N->act, k, model, row, NULL);
}

// A cards list (`_r_cards`): its own scrolled region, a grid of its cards, a caption and a grid
// of its own where its group= changes.
static void draw_cards(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k)
{
    const uit_node_t *N = NODE(k);
    const char *key = uit_str(e, N->key);
    int m = N->slot, desk = (e->flags & UIT_F_DESK) != 0, subs = 0;
    moy_ui_over_t go;
    int32_t gen = (e->flags & UIT_F_GEN) && e->gen_safe ? uit_cell_gen(e, k) : -1;
    moy_ui_over_none(&go);
    if (gen >= 0)
        moy_ui_over_put(&go, MOY_UI_OV_GEN, &gen);
    moy_ui_api_scroll(c, s, span_of(key, strlen(key)), gen >= 0 ? &go : NULL);
    KIDS(k, g)
    if (NODE(g)->t_value >= 0)
        subs = 1;
    int margin = desk ? 16 : 8, foot = desk ? (subs ? 52 : 30) : 26 + (subs ? 6 : 0);
    int32_t w = size_of(e, int_of(e, N->t_value, -1, -1, 0)), h = size_of(e, int_of(e, N->t_aux, -1, -1, 0));
    int grid = N->t_main < 0, capped = 0;
    if (grid) {
        moy_ui_api_grid_spread(c, 0, foot, 4, margin, 2);
        moy_ui_api_grid(c, s, span_of(key, strlen(key)), w, h, NULL);
    }
    uit_after_removal(e, k);
    uit_buf_t cap = {0}, gid = {0};
    for (int r = 0; r < e->slots[m].nrows; r++) {
        uint32_t mark = e->scratch.n;
        if (N->t_main >= 0) {
            uint32_t n, a = uit_tmpl_render(e, N->t_main, m, r, &n);
            if (!capped || n != cap.n || memcmp(e->scratch.p + a, cap.p, n)) {
                if (grid)
                    moy_ui_api_grid(c, s, span_of("", 0), 0, 0, NULL);
                if (capped)
                    moy_ui_api_group(c, s, span_of("", 0), NULL);
                moy_ui_api_group(c, s, sspan(e, a, n), NULL);
                cap.n = 0;
                uit_buf_printf(&cap, "%.*s", (int)n, e->scratch.p + a);
                gid.n = 0;
                uit_buf_printf(&gid, "%s %.*s", key, (int)n, e->scratch.p + a);
                moy_ui_api_grid_spread(c, 0, foot, 4, margin, 2);
                moy_ui_api_grid(c, s, span_of(gid.p, gid.n), w, h, NULL);
                grid = capped = 1;
            }
        }
        KIDS(k, g)
        if (NODE(g)->kind == UIT_CARD)
            draw_card(e, c, s, g, m, r);
        e->scratch.n = mark;
    }
    uit_buf_free(&cap);
    uit_buf_free(&gid);
    if (grid)
        moy_ui_api_grid(c, s, span_of("", 0), 0, 0, NULL);
    if (capped)
        moy_ui_api_group(c, s, span_of("", 0), NULL);
    moy_ui_api_end(c, s);
    moy_ui_api_scroll(c, s, span_of("", 0), NULL);
}

// One picture at its own place (`_r_picture`): a card with no words, its icon on a tinted tile.
static void draw_picture(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    int32_t side = size_of(e, int_of(e, N->t_value, model, row, 64)), one = 1, out[5];
    moy_ui_over_t o;
    moy_ui_over_none(&o);
    moy_ui_over_put(&o, MOY_UI_OV_DISABLED, &one);
    moy_ui_api_grid_spread(c, 1, -1, 4, (e->flags & UIT_F_DESK) ? 16 : 8, 0);
    moy_ui_api_grid(c, s, span_of("pic ", 4), side, side, NULL);
    card_mark(e, c, N, model, row);
    moy_ui_api_card(c, s, span_of("", 0), span_of("", 0), &o, out);
    moy_ui_api_grid(c, s, span_of("", 0), 0, 0, NULL);
}

// A split's cells' sizes across `span` (`_spans`): its first `limit` sizes, with a weight ("N*")
// among them each int a fixed size in desk units and the weights sharing the rest, else every int
// a weight; the last takes what rounding leaves. The count into *count.
enum { SPLIT_CELLS = 8 };
static int32_t split_spans(uit_t *e, const uit_node_t *N, int model, int row, int32_t room, int32_t gap,
                           int limit, int32_t spans[SPLIT_CELLS], int *count)
{
    uint32_t zn, za = uit_tmpl_render(e, N->t_aux, model, row, &zn);
    int32_t v[SPLIT_CELLS];
    uint8_t weight[SPLIT_CELLS];
    int nz = 0, any_w = 0;
    const char *p = e->scratch.p + za, *end = p + zn;
    while (p < end && nz < SPLIT_CELLS && nz < limit) {
        while (p < end && *p == ' ')
            p++;
        if (p >= end)
            break;
        char *stop;
        long x = strtol(p, &stop, 10);
        weight[nz] = stop < end && *stop == '*';
        if (weight[nz])
            x = stop == p ? 1 : x, stop++, any_w = 1;
        v[nz++] = (int32_t)x;
        p = stop;
        while (p < end && *p != ' ')
            p++;
    }
    int32_t sum = 0, span = room - gap * (nz - 1);
    if (any_w) {
        int32_t fixed = 0, ws = 0;
        for (int i = 0; i < nz; i++)
            if (weight[i])
                ws += v[i];
            else
                fixed += size_of(e, v[i]);
        int32_t rest = span - fixed > 0 ? span - fixed : 0;
        for (int i = 0; i < nz; i++)
            spans[i] = weight[i] ? rest * v[i] / (ws > 1 ? ws : 1) : size_of(e, v[i]);
    } else {
        int32_t tot = 0;
        for (int i = 0; i < nz; i++)
            tot += v[i];
        for (int i = 0; i < nz; i++)
            spans[i] = span * v[i] / (tot > 1 ? tot : 1);
    }
    for (int i = 0; i < nz; i++)
        sum += spans[i];
    if (nz)
        spans[nz - 1] += span - sum;
    *count = nz;
    return span;
}

static void label_rows_m(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row, int32_t *h,
                         int part);

// How tall a popover split's kid draws (`_pop_kid_h`): a label's lines, a progress row.
static int32_t pop_kid_h(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    int32_t h = 0, out[5];
    if (NODE(k)->kind == UIT_LABEL) {
        label_rows_m(e, c, s, k, model, row, &h, 3);
        return h;
    }
    if (NODE(k)->kind == UIT_PROGRESS) {
        moy_ui_api_measure(c, s, MOY_UI_MEASURE_PROGRESS, span_of("", 0), NULL, out);
        return out[3];
    }
    return 0;
}

static int32_t pop_cell_h(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    if (N->kind == UIT_PICTURE)
        return size_of(e, int_of(e, N->t_value, model, row, 64)) + size_of(e, 8);
    if (N->kind != UIT_GROUP)
        return pop_kid_h(e, c, s, k, model, row);
    int32_t h = 0;
    KIDS(k, g)
    if (uit_shown(e, g, model, row))
        h += pop_kid_h(e, c, s, g, model, row);
    return h;
}

// A split in a popover's body (`_pop_split`, dir "row"): its cells side by side on the body's
// rows, in by its pad, as tall as its tallest cell; a group cell its kids one under another.
static void pop_split(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    int kids[SPLIT_CELLS], nk = 0;
    int32_t hs[SPLIT_CELLS], top = 0, out[5];
    KIDS(k, g)
    if (nk < SPLIT_CELLS && uit_shown(e, g, model, row)) {
        hs[nk] = pop_cell_h(e, c, s, g, model, row);
        if (hs[nk] > top)
            top = hs[nk];
        kids[nk++] = g;
    }
    moy_ui_api_measure(c, s, MOY_UI_MEASURE_PROGRESS, span_of("", 0), NULL, out);   // where the body's stack stands
    int pad = gap_px(e, N->btn), gap = gap_px(e, N->nbtn);
    moy_ui_api_space(c, top + 2 * pad);
    if (!out[2])
        return;
    int32_t x = out[0] + pad, y = out[1] + pad, w = out[2] - 2 * pad > 0 ? out[2] - 2 * pad : 0;
    int32_t spans[SPLIT_CELLS];
    int nz;
    split_spans(e, N, model, row, w, gap, nk, spans, &nz);
    int32_t at = x;
    for (int i = 0; i < nk && i < nz; i++) {
        int g = kids[i];
        if (NODE(g)->kind == UIT_GROUP) {
            int32_t cy = y;
            KIDS(g, gg) {
                if (!uit_shown(e, gg, model, row))
                    continue;
                int32_t kh = pop_kid_h(e, c, s, gg, model, row);
                moy_ui_api_at(c, at, cy, spans[i], kh);
                draw(e, c, s, gg, model, row);
                cy += kh;
            }
        } else {
            moy_ui_api_at(c, at, y, spans[i], hs[i]);
            draw(e, c, s, g, model, row);
        }
        at += spans[i] + gap;
    }
}

// A popover (`_r_popover`): hung under its anchor's stop (a list's current row for "LIST.cur"),
// washing what it covers; its close= fired the frame it is dismissed.
static void draw_popover(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    const char *key = uit_str(e, N->key);
    int32_t w = size_of(e, int_of(e, N->t_aux, model, row, 200));
    if (N->t_value >= 0) {
        uint32_t n, mark = e->scratch.n, a = uit_tmpl_render(e, N->t_value, model, row, &n);
        const char *want = e->scratch.p + a;
        size_t wn = n;
        if (n > 4 && !memcmp(want + n - 4, ".cur", 4))
            for (int l = 0; l < e->nnodes; l++) {
                const uit_node_t *L = NODE(l);
                const char *mn = uit_str(e, e->slots[L->slot].name);
                if (UIT_IS_LIST(L->kind) && L->cur >= 0 && strlen(mn) == n - 4 && !memcmp(mn, want, n - 4)) {
                    const uit_slot_t *C = &e->slots[L->cur];
                    want = C->s ? C->s : "", wn = C->sn;
                    break;
                }
            }
        for (int i = 0; i < e->nids; i++) {
            const uit_id_t *I = &e->ids[i];
            const char *got = I->row >= 0 ? row_key(e, uit_model_of(e, I->node), I->row) : uit_str(e, NODE(I->node)->key);
            int32_t box[4];
            if (strlen(got) == wn && !memcmp(got, want, wn)) {
                if (moy_ui_api_box(c, span_of(I->id, strlen(I->id)), box))
                    moy_ui_api_at(c, box[0], box[1] - 8, w, 0);
                break;
            }
        }
        e->scratch.n = mark;
    }
    moy_ui_over_t o;
    int32_t one = 1;
    moy_ui_over_none(&o);
    moy_ui_over_put(&o, MOY_UI_OV_WASH, &one);
    if (!moy_ui_api_popover(c, s, span_of(key, strlen(key)), w, &o) && N->on >= 0)
        fire(e, N->on, k, -1, -1, NULL);
    e->in_pop = 1;
    walk(e, c, s, k, model, row);
    e->in_pop = 0;
    moy_ui_api_popover(c, s, span_of("", 0), 0, NULL);
}

// A split (not in a popover): its cells across the page's room in by its pad, each a fixed size
// in desk units or a weight of what is left ("N*"; with none, every size a weight); a group cell
// its own scrolled column, any other kind drawn as it stands.
// The width a link row `k` needs to draw its label and its value whole (the
// toolkit's MOY_UI_MEASURE_LINK), 0 where it is not shown.
static int32_t link_w(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    if (!uit_shown(e, k, model, row))
        return 0;
    char t[256];
    uint32_t n1, a1 = text(e, k, 0, model, row, &n1);
    size_t m = n1 < sizeof t - 1 ? n1 : sizeof t - 1;
    memcpy(t, e->scratch.p + a1, m);
    t[m++] = '\t';
    uint32_t n2, a2 = text(e, k, 1, model, row, &n2);
    size_t v = n2 < sizeof t - m ? n2 : sizeof t - m;
    memcpy(t + m, e->scratch.p + a2, v);
    int32_t out[5];
    moy_ui_api_measure(c, s, MOY_UI_MEASURE_LINK, span_of(t, m + v), NULL, out);
    return out[2];
}

// The widest link row in region `k` (`_row_w`): every row of a rows' model, a group's own.
static int32_t row_w(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    int32_t w = 0, x;
    KIDS(k, g) {
        const uit_node_t *N = NODE(g);
        if (N->kind == UIT_ROWS) {
            for (int r = 0; r < e->slots[N->slot].nrows; r++)
                KIDS(g, kk)
                if (NODE(kk)->kind == UIT_LINK && (x = link_w(e, c, s, kk, N->slot, r)) > w)
                    w = x;
        } else if (N->kind == UIT_LINK) {
            if ((x = link_w(e, c, s, g, model, row)) > w)
                w = x;
        } else if (N->kind == UIT_GROUP) {
            if ((x = row_w(e, c, s, g, model, row)) > w)
                w = x;
        }
    }
    return w;
}

// A row split's widths (`_fit_spans`): each group cell whose link rows need more than its size
// gives it widened to their widest, the cells beside it giving up at most half their share.
static void fit_spans(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, const int *kids, int nk,
                      int model, int row, int32_t *spans, int nz)
{
    int32_t need[SPLIT_CELLS] = {0}, floor[SPLIT_CELLS];
    int any = 0;
    for (int i = 0; i < nk && i < nz; i++)
        if (NODE(kids[i])->kind == UIT_GROUP)
            any |= (need[i] = row_w(e, c, s, kids[i], model, row)) > 0;
    if (!any)
        return;
    for (int j = 0; j < nz; j++)
        floor[j] = spans[j] / 2 > need[j] ? spans[j] / 2 : need[j];
    for (int i = 0; i < nk && i < nz; i++) {
        int32_t short_ = need[i] - spans[i];
        for (int j = 0; j < nz && short_ > 0; j++) {
            int32_t give = j != i ? spans[j] - floor[j] : 0;
            give = give < short_ ? give : short_;
            if (give > 0) {
                spans[j] -= give;
                spans[i] += give;
                short_ -= give;
            }
        }
    }
}

static void draw_split(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    if (e->in_pop) {
        pop_split(e, c, s, k, model, row);
        return;
    }
    uint32_t dn, da = uit_tmpl_render(e, N->t_value, model, row, &dn);
    int across = dn == 3 && !memcmp(e->scratch.p + da, "row", 3);
    int pad = gap_px(e, N->btn), gap = gap_px(e, N->nbtn), nz;
    int32_t r[4], spans[SPLIT_CELLS];
    moy_ui_inset32(e->rect, pad, pad, r);
    int32_t span = split_spans(e, N, model, row, across ? r[2] : r[3], gap, SPLIT_CELLS, spans, &nz);
    int kids[SPLIT_CELLS], nk = 0;
    KIDS(k, g)
    if (nk < SPLIT_CELLS && uit_shown(e, g, model, row))
        kids[nk++] = g;
    if (across)
        fit_spans(e, c, s, kids, nk, model, row, spans, nz);
    int32_t at = 0;
    for (int i = 0; i < nk && i < nz; i++) {
        int g = kids[i];
        int32_t sz = i < nk - 1 ? spans[i] : span - at;
        int32_t cell[4] = {across ? r[0] + at : r[0], across ? r[1] : r[1] + at, across ? sz : r[2],
                           across ? r[3] : sz};
        at += sz + gap;
        if (NODE(g)->kind == UIT_CANVAS) {
            if (e->draw_fn)             // a cell of its own that only draws: drawn into the cell
                e->draw_fn(e->draw_ud, g, cell[0], cell[1], cell[2], cell[3]);
            continue;
        }
        if (NODE(g)->kind != UIT_GROUP) {
            draw(e, c, s, g, model, row);
            continue;
        }
        char sid[UIT_ID_MAX + 1];
        snprintf(sid, sizeof sid, "split %s %d", uit_str(e, N->key), i);
        moy_ui_api_at(c, cell[0], cell[1], cell[2], cell[3]);
        int32_t cg = (e->flags & UIT_F_GEN) && e->gen_safe ? uit_cell_gen(e, g) : -1;
        moy_ui_over_t go;           // the region carries a gen of its own, as a cards list's does
        moy_ui_over_none(&go);
        if (cg >= 0)
            moy_ui_over_put(&go, MOY_UI_OV_GEN, &cg);
        moy_ui_api_scroll(c, s, span_of(sid, strlen(sid)), cg >= 0 ? &go : NULL);
        uint8_t was = e->in_cell;
        e->in_cell = 1;
        if (across && N->lit == 1) {
            int used = col_h(e, c, s, g, model, row);
            if (used >= 0)
                moy_ui_api_space(c, (cell[3] - used) / 2);
        }
        walk(e, c, s, g, model, row);
        e->in_cell = was;
        moy_ui_api_end(c, s);
        moy_ui_api_scroll(c, s, span_of("", 0), NULL);
        moy_ui_api_at(c, e->rect[0], e->rect[1], e->rect[2], e->rect[3]);
    }
}

// A menu: its items in order, a `rows` among them an item per row of its model, each shown and
// its action on; an item's id is its text (its letter after a tab where the band shows keys).
typedef struct {
    int16_t node, model, row;
} item_t;

static int item_add(uit_buf_t *b, int k, int model, int row)
{
    item_t it = {(int16_t)k, (int16_t)model, (int16_t)row};
    return uit_buf_put(b, (const char *)&it, sizeof it) < 0 ? -1 : 0;
}

static void draw_menu(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    uit_buf_t its = {0}, labels = {0}, icons = {0};
    KIDS(k, c2) {
        const uit_node_t *N = NODE(c2);
        if (N->kind == UIT_ROWS) {
            for (int r = 0; r < e->slots[N->slot].nrows; r++)
                KIDS(c2, g) {
                    if (uit_shown(e, g, N->slot, r) && uit_act_can(e, NODE(g)->act, N->slot, r))
                        item_add(&its, g, N->slot, r);
                }
        } else if (uit_shown(e, c2, model, row) && uit_act_can(e, N->act, model, row)) {
            item_add(&its, c2, model, row);
        }
    }
    int n = (int)(its.n / (sizeof(item_t) + 1));
    static const char BACK[] = "back\0Back", BAR[] = "change\0Move\0confirm\0Open\0back\0Back";
    int keys = (e->flags & UIT_F_ON_BAR) ? moy_ui_api_hints_w(c, s, (const uint8_t *)BAR, sizeof BAR - 1) > 0
               : moy_ui_api_hints_w(c, s, (const uint8_t *)BACK, sizeof BACK - 1) > 0;
    int32_t danger = 0;
    for (int i = 0; i < n; i++) {
        item_t it;
        memcpy(&it, its.p + i * (sizeof(item_t) + 1), sizeof it);
        const uit_node_t *N = NODE(it.node);
        const uit_act_t *A = &e->acts[N->act];
        uint32_t ln, la = text(e, it.node, 0, it.model, it.row, &ln);
        if (i)
            uit_buf_printf(&labels, "%c", 0), uit_buf_printf(&icons, "%c", 0);
        uit_buf_printf(&labels, "%.*s", (int)ln, e->scratch.p + la);
        const char *hot = uit_str(e, A->letters);
        if (hot[0] && keys)
            uit_buf_printf(&labels, "\t%c", hot[0] >= 'a' && hot[0] <= 'z' ? hot[0] - 32 : hot[0]);
        if (N->t_icon >= 0) {
            uint32_t in, ia = uit_tmpl_render(e, N->t_icon, it.model, it.row, &in);
            uit_buf_printf(&icons, "%.*s", (int)in, e->scratch.p + ia);
        } else {
            uit_buf_printf(&icons, "%s", uit_str(e, A->icon));
        }
        if (N->lit && i < 32)
            danger |= (int32_t)1 << i;
    }
    if (danger)
        moy_ui_api_menu_danger(c, danger);
    uint32_t cn, ca = text(e, k, 0, model, row, &cn);
    int got = moy_ui_api_menu(c, s, sspan(e, ca, cn), (const uint8_t *)(labels.p ? labels.p : ""), labels.n,
                              (const uint8_t *)(icons.p ? icons.p : ""), icons.n, NULL);
    // each item's id: its text, cut to what the toolkit keeps
    const char *l = labels.p;
    for (int i = 0; i < n; i++) {
        item_t it;
        memcpy(&it, its.p + i * (sizeof(item_t) + 1), sizeof it);
        char id[UIT_ID_MAX + 1];
        snprintf(id, sizeof id, "%s", l);
        id_put(e, it.node, it.model, it.row, id);
        l += strlen(l) + 1;
        if (i == got && uit_act_can(e, NODE(it.node)->act, it.model, it.row))
            fire(e, NODE(it.node)->act, it.node, it.model, it.row, NULL);
    }
    uit_buf_free(&its), uit_buf_free(&labels), uit_buf_free(&icons);
}

// A rect filled in theme token `token` (the reference's `cv.rect(..., th[ROLE])`).
static void fill_token(const moy_ui_sink_t *s, int32_t x, int32_t y, int32_t w, int32_t h, int token)
{
    moy_ui_box_t b = {(int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h}, none = {0, 0, 0, 0};
    moy_ui_fill_uncovered(s, b, none, moy_ui_token(s->look, token));
}

// An enum's tab strip across the page's top (`_r_tabs`): a band cut from its room.
static void draw_tabs(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k)
{
    const uit_node_t *N = NODE(k);
    int desk = (e->flags & UIT_F_DESK) != 0, mn;
    int bh = moy_ui_metrics(MOY_UI_K_VALUE_ROW, &mn)[desk ? 17 : 16] * e->fs, pad = desk ? 8 : 0;
    int32_t band[4], rest[4];
    moy_ui_cut(e->rect, MOY_UI_CUT_TOP, bh + 2 * pad, band, rest);
    memcpy(e->rect, rest, sizeof rest);
    if (pad)
        moy_ui_inset32(band, 2 * pad, pad, band);
    moy_ui_api_at(c, band[0], band[1], band[2], band[3]);
    int count, at;
    uint32_t len, items = seg_items(e, N, &len, &count, &at);
    moy_ui_over_t o;
    char id[UIT_ID_MAX + 1];
    over_for(e, k, -1, -1, c, &o, id);
    int got = moy_ui_api_choice(c, s, 9, span_of("", 0), (const uint8_t *)e->scratch.p + items, len, at, &o);
    moy_ui_api_at(c, e->rect[0], e->rect[1], e->rect[2], e->rect[3]);
    if (got >= 0 && got < count && got != at) {
        const char *v = seg_key(e, N, got);
        wrote_text(e, k, v, strlen(v), "from", N->on);
    }
}

// A table (`_r_table`): its columns' headers and its rows' cells, one stop
// whose current row follows focus and cur=.
static void draw_table(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k)
{
    const uit_node_t *N = NODE(k);
    int m = N->slot;
    uit_after_removal(e, k);
    const uit_slot_t *M = &e->slots[m];
    int at = 0;
    if (N->cur >= 0) {
        const uit_slot_t *C = &e->slots[N->cur];
        int r = uit_row_find(e, m, C->s ? C->s : "", C->sn);
        if (r >= 0)
            at = r;
    }
    const char *key = uit_str(e, N->key);
    moy_ui_api_at(c, e->rect[0], e->rect[1], e->rect[2], e->rect[3]);
    id_put(e, k, m, M->nrows ? at : -1, key);
    if (!M->nrows) {
        uint32_t n = 0, a = N->t_aux >= 0 ? uit_tmpl_render(e, N->t_aux, -1, -1, &n) : 0;
        moy_ui_api_label(c, s, n ? sspan(e, a, n) : span_of("", 0), NULL);
        return;
    }
    // its cells made again only where the build, its model or a state its columns read moved
    uit_buf_t *B = &e->cells;
    int engine = 0;
    uint32_t ckey = (uint32_t)e->gen * 65537u + (uint32_t)M->ver * 257u + (uint32_t)M->nrows;
    KIDS(k, g0)
    if (NODE(g0)->kind == UIT_COL)
        ckey += uit_tmpl_key(e, NODE(g0)->t_main, &engine);
    int ncol = 0;
    uint32_t cols_n = 0;
    for (int p = N->props; p != (int)UIT_NONE; p = e->props[p].next)
        if (!strcmp(uit_str(e, e->props[p].name), "cols")) {
            cols_n += (uint32_t)strlen(uit_str(e, e->props[p].src)) + 1;
            ncol++;
        }
    if (engine || e->cells_node != k + 1 || e->cells_key != ckey || B->n < cols_n) {
        B->n = 0;
        for (int p = N->props; p != (int)UIT_NONE; p = e->props[p].next)
            if (!strcmp(uit_str(e, e->props[p].name), "cols"))
                uit_buf_puts(B, uit_str(e, e->props[p].src));
        uint32_t mark = e->scratch.n;
        for (int r = 0; r < M->nrows; r++) {
            int i = 0;
            KIDS(k, g) {
                if (NODE(g)->kind != UIT_COL || i >= ncol)
                    continue;
                uint32_t n = 0, a = NODE(g)->t_main >= 0 ? uit_tmpl_render(e, NODE(g)->t_main, m, r, &n) : 0;
                uit_buf_put(B, e->scratch.p + a, n);
                e->scratch.n = mark;
                i++;
            }
            for (; i < ncol; i++)
                uit_buf_put(B, "", 0);
        }
        e->cells_node = (int16_t)(k + 1);
        e->cells_key = ckey;
    }
    moy_ui_over_t o;
    moy_ui_over_none(&o);
    moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(key, strlen(key)));
    int32_t out[2] = {at, 0};
    moy_ui_api_table(c, s, span_of("", 0), (const uint8_t *)B->p, cols_n, (const uint8_t *)B->p + cols_n,
                     B->n - cols_n, at, &o, out);
    int got = out[0];
    if (got < 0 || got >= M->nrows)
        return;
    const char *gk = uit_cell(e, m, got, M->key);
    size_t gn = strlen(gk);
    if (N->cur >= 0) {
        const uit_slot_t *C = &e->slots[N->cur];
        if (gn != C->sn || memcmp(gk, C->s ? C->s : "", gn))
            uit_cur_moved(e, k, gk, gn, "table");
    }
    id_put(e, k, m, got, key);
    if (out[1] && N->on >= 0)
        fire(e, N->on, k, m, got, NULL);
}

// A shared foot's label (`_foot_label`): its first shown label with a text, else -1.
static int foot_label(uit_t *e, int k)
{
    if (!NODE(k)->lit)
        return -1;
    KIDS(k, g) {
        if (NODE(g)->kind != UIT_LABEL || NODE(g)->t_main < 0 || !uit_shown(e, g, -1, -1))
            continue;
        uint32_t n, mark = e->scratch.n;
        uit_tmpl_render(e, NODE(g)->t_main, -1, -1, &n);
        e->scratch.n = mark;
        if (n)
            return g;
    }
    return -1;
}

// The foot's line over its buttons (`_foot_line`): its label's line, else 0.
static int foot_line(uit_t *e, int k)
{
    if (foot_label(e, k) < 0)
        return 0;
    moy_font_info_t fi;
    int line = moy_font_info(moy_text_print_font(), &fi) ? fi.line : 8;
    return line + ((e->flags & UIT_F_DESK) ? 8 : 4 * e->fs);
}

// The page's shown foot (`_foot_of`): at its root, or in the shown case of a switch there.
static int foot_of(uit_t *e)
{
    KIDS(0, k) {
        if (!uit_shown(e, k, -1, -1))
            continue;
        if (NODE(k)->kind == UIT_FOOT)
            return k;
        if (NODE(k)->kind == UIT_SWITCH) {
            int cs = uit_case_of(e, k);
            if (cs < 0)
                continue;
            KIDS(cs, g)
            if (NODE(g)->kind == UIT_FOOT && uit_shown(e, g, -1, -1))
                return g;
        }
    }
    return -1;
}

static void draw(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row);

// The page's docked foot, look="share" (`_r_foot`): its buttons share its
// width, its label a line of its own over them. On a handheld it lies
// outside the page on its own ground under a rule; on the desk it is the
// page's (`ui.dock`), on the page's ground.
static void draw_foot(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    int desk = (e->flags & UIT_F_DESK) != 0, fs = e->fs;
    int32_t x = e->foot_r[0], y = e->foot_r[1], w = e->foot_r[2], h = e->foot_r[3];
    int fh = desk ? 30 : 20 * fs, pad = desk ? 7 : fs, m = desk ? 16 : 4 * fs;
    int by = y + h - fh - pad;
    if (!desk) {
        fill_token(s, x, y, w, h, MOY_UI_T_PANEL);
        fill_token(s, x + m, y, w - 2 * m, 1, MOY_UI_T_BORDER);
    }
    int16_t btn[16];
    int nb = 0;
    KIDS(k, g) {
        const uit_node_t *G = NODE(g);
        if (G->kind == UIT_BUTTON && nb < 16 && uit_shown(e, g, model, row) &&
            (G->act < 0 || uit_act_can(e, G->act, model, row)))
            btn[nb++] = (int16_t)g;
    }
    if (!NODE(k)->lit) {
        // the end look: its labels before its end
        int32_t bx = x + w - m;
        KIDS(k, g)
        if (NODE(g)->kind == UIT_LABEL && uit_shown(e, g, model, row)) {
            moy_ui_api_at(c, x + 26, by, bx - x - 26 > 0 ? bx - x - 26 : 0, fh);
            label_rows(e, c, s, g, model, row, NULL);
        }
        return;
    }
    int lab = foot_label(e, k);
    if (lab >= 0) {
        moy_ui_api_at(c, x + m, y + pad, w - 2 * m, by - y - pad);
        label_rows(e, c, s, lab, model, row, NULL);
    }
    if (!nb)
        return;
    int32_t r[4] = {x + m, by, w - 2 * m, fh}, cells[16][4];
    if (nb == 1)
        memcpy(cells[0], r, sizeof r);
    else
        moy_ui_split(r, nb, desk ? 8 : 4 * fs, 1, cells, 16);
    for (int i = 0; i < nb; i++) {
        moy_ui_api_at(c, cells[i][0], cells[i][1], cells[i][2], cells[i][3]);
        draw(e, c, s, btn[i], model, row);
    }
}

static void draw(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    moy_ui_over_t o;
    char id[UIT_ID_MAX + 1];
    uint32_t n1, n2, a1, a2;
    switch (N->kind) {
    case UIT_GROUP:
        a1 = text(e, k, 0, model, row, &n1);
        moy_ui_api_group(c, s, sspan(e, a1, n1), NULL);
        walk(e, c, s, k, model, row);
        moy_ui_api_group(c, s, span_of("", 0), NULL);
        break;
    case UIT_LABEL:
        label_rows(e, c, s, k, model, row, NULL);
        break;
    case UIT_INFO:
    case UIT_LINK:
        a1 = text(e, k, 0, model, row, &n1);
        a2 = text(e, k, 1, model, row, &n2);
        over_for(e, k, model, row, c, &o, id);
        if (moy_ui_api_value(c, s, N->kind == UIT_INFO ? MOY_UI_V_INFO : MOY_UI_V_LINK, sspan(e, a1, n1),
                             sspan(e, a2, n2), &o) &&
            N->kind == UIT_LINK && uit_act_can(e, N->act, model, row))
            fire(e, N->act, k, model, row, NULL);
        break;
    case UIT_BUTTON:
        a1 = text(e, k, 0, model, row, &n1);
        over_for(e, k, model, row, c, &o, id);
        if (e->in_cell) {
            int32_t one = 1;
            moy_ui_over_put(&o, MOY_UI_OV_FIT, &one);
        }
        if (moy_ui_api_button(c, s, sspan(e, a1, n1), N->lit, &o) && uit_act_can(e, N->act, model, row))
            fire(e, N->act, k, model, row, NULL);
        break;
    case UIT_TOGGLE: {
        uit_slot_t *S = &e->slots[N->slot];
        int field = (N->lit & NL_FIELD) != 0;
        int cur = field ? uit_cell(e, model, row, N->cur)[0] == '1' : S->i != 0;
        a1 = text(e, k, 0, model, row, &n1);
        over_for(e, k, model, row, c, &o, id);
        int got = moy_ui_api_toggle(c, s, sspan(e, a1, n1), cur, &o) != 0;
        if (got != cur && field) {
            row_wrote(e, k, model, row, got);
        } else if (got != cur) {
            uit_set_int(e, N->slot, got);
            uit_buf_t b = {0};
            const char *key = uit_str(e, N->key);
            uit_buf_printf(&b, "from toggle ");
            uit_buf_repr(&b, key, strlen(key));
            wrote(e, k, cur ? "True" : "False", got ? "True" : "False", b.p, N->on);
            uit_buf_free(&b);
        }
        break;
    }
    case UIT_SEG: {
        a1 = text(e, k, 0, model, row, &n1);
        int count, at;
        uint32_t len, items = seg_items(e, N, &len, &count, &at);
        over_for(e, k, model, row, c, &o, id);
        int got = moy_ui_api_choice(c, s, (N->lit & NL_CYCLE) ? 3 : 4, sspan(e, a1, n1),
                                    (const uint8_t *)e->scratch.p + items, len, at, &o);
        if (got >= 0 && got < count && got != at) {
            const char *v = seg_key(e, N, got);
            wrote_text(e, k, v, strlen(v), "from", N->on);
        }
        break;
    }
    case UIT_SLIDER: {
        uint32_t ln, hn, la = uit_tmpl_render(e, N->t_value, model, row, &ln);
        int32_t lo = (int32_t)strtol(e->scratch.p + la, NULL, 10);
        uint32_t ha = uit_tmpl_render(e, N->t_aux, model, row, &hn);
        int32_t hi = (int32_t)strtol(e->scratch.p + ha, NULL, 10);
        a1 = text(e, k, 0, model, row, &n1);
        over_for(e, k, model, row, c, &o, id);
        if (N->lit & NL_STEPS) {
            int32_t one = 1;
            moy_ui_over_put(&o, MOY_UI_OV_STEPS, &one);
        }
        int32_t cur = e->slots[N->slot].i;
        int32_t got = moy_ui_api_slider(c, s, sspan(e, a1, n1), cur, lo, hi, &o);
        if (got != cur) {
            uit_set_int(e, N->slot, got);
            char was[16], now[16];
            snprintf(was, sizeof was, "%ld", (long)cur);
            snprintf(now, sizeof now, "%ld", (long)got);
            uit_buf_t b = {0};
            const char *key = uit_str(e, N->key);
            uit_buf_printf(&b, "from slider ");
            uit_buf_repr(&b, key, strlen(key));
            wrote(e, k, was, now, b.p, N->on);
            uit_buf_free(&b);
        }
        break;
    }
    case UIT_FIELD:
        draw_field(e, c, s, k, model, row);
        break;
    case UIT_ROWS: {
        const uit_slot_t *M = &e->slots[N->slot];
        uit_after_removal(e, k);
        if (!M->nrows && N->t_aux >= 0) {
            uint32_t n, at = uit_tmpl_render(e, N->t_aux, -1, -1, &n);
            if (n)
                moy_ui_api_label(c, s, sspan(e, at, n), NULL);
        }
        // group=: a caption where its text changes from the row before, the rows under it on its card
        uint32_t cap_at = 0, cap_n = 0;
        int capped = 0;
        for (int r = 0; r < e->slots[N->slot].nrows; r++) {
            if (N->t_main >= 0) {
                uint32_t n, at = uit_tmpl_render(e, N->t_main, N->slot, r, &n);
                if (!capped || n != cap_n || memcmp(e->scratch.p + at, e->scratch.p + cap_at, n)) {
                    if (capped)
                        moy_ui_api_group(c, s, span_of("", 0), NULL);
                    moy_ui_api_group(c, s, sspan(e, at, n), NULL);
                    cap_at = at, cap_n = n, capped = 1;
                }
            }
            walk(e, c, s, k, N->slot, r);
        }
        if (capped)
            moy_ui_api_group(c, s, span_of("", 0), NULL);
        break;
    }
    case UIT_TEXT_VIEW:
        draw_text_view(e, c, s, k, model, row);
        break;
    case UIT_MENU:
        draw_menu(e, c, s, k, model, row);
        break;
    case UIT_SPLIT:
        draw_split(e, c, s, k, model, row);
        break;
    case UIT_CANVAS: {
        // a row of the page that only draws: no stop, no ring
        const char *key = uit_str(e, N->key);
        int32_t one = 1, r[5];
        moy_ui_over_none(&o);
        moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(key, strlen(key)));
        moy_ui_over_put(&o, MOY_UI_OV_DISABLED, &one);
        moy_ui_api_part(c, s, span_of(key, strlen(key)), 0, N->btn, &o, r);
        if (e->draw_fn)
            e->draw_fn(e->draw_ud, k, r[0], r[1], r[2], r[3]);
        break;
    }
    case UIT_TABS:
        draw_tabs(e, c, s, k);
        break;
    case UIT_SWITCH: {
        int cs = uit_case_of(e, k);
        if (cs >= 0)
            walk(e, c, s, cs, model, row);
        break;
    }
    case UIT_TABLE:
        draw_table(e, c, s, k);
        break;
    case UIT_FOOT:
        draw_foot(e, c, s, k, model, row);
        break;
    case UIT_CARDS:
        draw_cards(e, c, s, k);
        break;
    case UIT_PICTURE:
        draw_picture(e, c, s, k, model, row);
        break;
    case UIT_POPOVER:
        draw_popover(e, c, s, k, model, row);
        break;
    case UIT_SCROLL: {
        const char *key = uit_str(e, N->key);
        moy_ui_api_scroll(c, s, span_of(key, strlen(key)), NULL);
        walk(e, c, s, k, model, row);
        moy_ui_api_scroll(c, s, span_of("", 0), NULL);
        break;
    }
    case UIT_PROGRESS: {
        uint32_t vn, hn, va = uit_tmpl_render(e, N->t_value, model, row, &vn);
        int32_t v = (int32_t)strtol(e->scratch.p + va, NULL, 10);
        uint32_t ha = uit_tmpl_render(e, N->t_aux, model, row, &hn);
        int32_t hi = (int32_t)strtol(e->scratch.p + ha, NULL, 10);
        a1 = text(e, k, 0, model, row, &n1);
        if (!(N->lit & NL_STEPS)) {
            moy_ui_api_progress(c, s, sspan(e, a1, n1), v, hi, NULL);
            break;
        }
        // look="count": the label, where there is one, says the count in its own words
        char cnt[32];
        moy_ui_span_t l = sspan(e, a1, n1);
        if (!n1)
            l = span_of(cnt, hi ? (size_t)snprintf(cnt, sizeof cnt, "%ld of %ld", (long)v, (long)hi) : 0);
        int32_t one = 1;
        moy_ui_over_none(&o);
        moy_ui_over_put(&o, MOY_UI_OV_STEPS, &one);
        moy_ui_api_progress(c, s, l, v, hi, &o);
        break;
    }
    case UIT_EMPTY: {
        uint32_t ni, ai = N->t_icon >= 0 ? uit_tmpl_render(e, N->t_icon, model, row, &ni) : 0;
        if (N->t_icon < 0 || !ni)
            ai = 0, ni = 0;
        a1 = text(e, k, 0, model, row, &n1);
        uit_buf_t lines = {0};
        int any = 0;
        for (int p = N->props; p != (int)UIT_NONE; p = e->props[p].next) {
            const uit_prop_t *P = &e->props[p];
            if (strcmp(uit_str(e, P->name), "lines"))
                continue;
            uint32_t n, at = uit_tmpl_render(e, P->idx, model, row, &n);
            if (!n)
                continue;
            if (any++)
                uit_buf_printf(&lines, "%c", 0);
            uit_buf_printf(&lines, "%.*s", (int)n, e->scratch.p + at);
        }
        moy_ui_api_empty(c, s, ni ? sspan(e, ai, ni) : span_of("warn", 4), sspan(e, a1, n1),
                         (const uint8_t *)(lines.p ? lines.p : ""), lines.n);
        uit_buf_free(&lines);
        break;
    }
    case UIT_DIALOG: {
        a1 = text(e, k, 0, model, row, &n1);
        uit_buf_t b = {0};
        for (int i = 0; i < N->nbtn; i++) {
            const char *l = uit_str(e, e->acts[e->btns[N->btn + i]].label);
            if (i)
                uit_buf_printf(&b, "%c", 0);
            uit_buf_printf(&b, "%s", l);
        }
        // the labels joined by NULs: b.n counts them (uit_buf_printf of "%c", 0 adds one byte)
        int pick = moy_ui_api_dialog(c, s, sspan(e, a1, n1), (const uint8_t *)(b.p ? b.p : ""), b.n, 1, NULL);
        uit_buf_free(&b);
        walk(e, c, s, k, model, row);
        moy_ui_api_dialog(c, s, span_of("", 0), (const uint8_t *)"", 0, 0, NULL);
        if (pick >= 0 && pick < N->nbtn) {
            int a = e->btns[N->btn + pick];
            uit_buf_t t = {0};
            const char *nm = uit_str(e, e->acts[a].name);
            uit_buf_printf(&t, "button ");
            uit_buf_repr(&t, nm, strlen(nm));
            uit_buf_printf(&t, " of dialog ");
            uint32_t n, at = uit_tmpl_render(e, N->t_main, -1, -1, &n);
            uit_buf_repr(&t, e->scratch.p + at, n);
            uit_event(e, UIT_EV_ACTION, a, -1, -1, t.p, t.n);
            uit_buf_free(&t);
        }
        break;
    }
    }
}

static void walk(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    KIDS(k, c2) {
        const uit_node_t *N = NODE(c2);
        if (!uit_shown(e, c2, model, row))
            continue;
        if (N->act >= 0 && !uit_act_can(e, N->act, model, row) && e->acts[N->act].off != 2)
            continue;
        draw(e, c, s, c2, model, row);
    }
}

// -- focus, before the page draws and after ---------------------------------------------

static int id_in(uit_t *e, const char *want, char out[UIT_ID_MAX + 1])
{
    for (int i = 0; i < e->nprev; i++) {
        const uit_id_t *I = &e->prev[i];
        int hit;
        if (I->row >= 0) {
            int m = I->gen == e->gen ? uit_model_of(e, I->node) : -1;
            hit = m >= 0 && !strcmp(row_key(e, m, I->row), want);
        } else {
            hit = !strcmp(I->id, want);
        }
        if (hit) {
            strcpy(out, I->id);
            return 1;
        }
    }
    return 0;
}

static int row_stop_id(uit_t *e, int lst, const char *key, size_t n, char out[UIT_ID_MAX + 1])
{
    if (uit_row_find(e, NODE(lst)->slot, key, n) < 0)
        return 0;
    if (NODE(lst)->kind == UIT_TABLE) {
        snprintf(out, UIT_ID_MAX + 1, "%s", uit_str(e, NODE(lst)->key));     // one stop, its key the id
        return 1;
    }
    KIDS(lst, g)
    if (is_stop(NODE(g)->kind) && NODE(g)->kind != UIT_SEG) {
        snprintf(out, UIT_ID_MAX + 1, "%.*s/%s", (int)n, key, uit_str(e, NODE(g)->key));
        return 1;
    }
    return 0;
}

static void focus_to(moy_ui_ctx_t *c, const char *id)
{
    moy_ui_api_focus(c, span_of(id, strlen(id)), NULL, 0);
}

// `_land_focus`: (whether focus moved, a node foreseen into ahead).
static int land_focus(uit_t *e, moy_ui_ctx_t *c, char ahead[UIT_ID_MAX + 1])
{
    int moved = 0;
    ahead[0] = 0;
    char fid[UIT_ID_MAX + 1];
    int fresh_prev = e->fresh && e->nprev && e->prev[0].gen == e->gen;    // not another page's
    if (fresh_prev) {
        e->fresh = 0;
    } else if (e->fresh && e->nnodes) {
        int k = fresh_focus(e, 0);
        if (k >= 0 && drawn_key(e, uit_str(e, NODE(k)->key), fid)) {
            moy_ui_api_focus_ahead(c, span_of(fid, strlen(fid)));
            moved = 1;
            e->fresh = 0;
        }
    }
    if (e->want) {
        const char *key = e->want;
        if (id_find(e->prev, e->nprev, key))
            snprintf(fid, sizeof fid, "%s", key);
        else if (!id_in(e, key, fid)) {
            if (drawn_key(e, key, fid))
                strcpy(ahead, fid);
            else
                fid[0] = 0;
        }
        if (fid[0]) {
            focus_to(c, fid);
            moved = 1;
            uit_want_focus(e, NULL);
        }
    }
    if (fresh_prev) {
        for (int i = 0; i < e->nprev; i++) {
            const uit_id_t *I = &e->prev[i];
            if (I->gen == e->gen && NODE(I->node)->focus >= 0 &&
                uit_cond_eval(e, NODE(I->node)->focus, uit_model_of(e, I->node), I->row)) {
                focus_to(c, I->id);
                moved = 1;
                break;
            }
        }
    }
    for (int k = 0; k < e->nnodes; k++) {
        const uit_node_t *L = NODE(k);
        if (!UIT_IS_LIST(L->kind) || L->cur < 0)
            continue;
        uit_slot_t *S = &e->slots[L->cur];
        const char *v = S->s ? S->s : "";
        if (S->sync_set && S->sync && !strcmp(S->sync, v))
            continue;
        if (!id_in(e, v, fid)) {
            if (!(*v && row_stop_id(e, k, v, S->sn, fid)))
                continue;
        }
        focus_to(c, fid);
        moved = 1;
        UIT_FREE(S->sync);
        S->sync = UIT_MALLOC(S->sn + 1);
        if (S->sync)
            memcpy(S->sync, v, S->sn + 1);
        S->sync_set = 1;
    }
    return moved;
}

static void focus_after(uit_t *e, moy_ui_ctx_t *c, int moved, const char *ahead, const uit_id_t *last)
{
    if (ahead[0] && !id_find(e->ids, e->nids, ahead))
        uit_want_focus(e, ahead);
    char f[UIT_ID_MAX + 1];
    int fl = moy_ui_api_focus(c, span_of(NULL, 0), f, sizeof f);
    const uit_id_t *I = fl >= 0 ? id_find(e->ids, e->nids, f) : NULL;
    e->has_focused = I != NULL;
    if (I)
        e->focused = *I;
    snprintf(e->focus_key, sizeof e->focus_key, "%s", fl > 0 ? f : "");
    if (last && last->kind == UIT_FIELD && last->gen == e->gen &&
        (!I || I->node != last->node || I->gen != last->gen))
        commit_field(e, last->node);    // focus left a commit field: its draft is written
    if (!I || moved)
        return;
    int m = uit_model_of(e, I->node);
    int lst = -1;
    for (int k = I->node; k >= 0 && k != (int)UIT_NONE; k = NODE(k)->parent)
        if (UIT_IS_LIST(NODE(k)->kind)) {
            lst = k;
            break;
        }
    if (lst < 0 || NODE(lst)->cur < 0)
        return;
    const char *v = I->row >= 0 ? row_key(e, m, I->row) : "";
    const uit_slot_t *S = &e->slots[NODE(lst)->cur];
    if (strlen(v) != S->sn || memcmp(v, S->s ? S->s : "", S->sn))
        uit_cur_moved(e, lst, v, strlen(v), "focus");
}

// -- the frame ----------------------------------------------------------------------------

// Every state's version, the builds and the drafts' edits: moves whenever anything a frame reads may have.
static uint32_t still_sum(const uit_t *e)
{
    uint32_t g = (uint32_t)e->gen * 65537u + e->edits;
    for (int i = 0; i < e->nslots; i++)
        g += (uint32_t)e->slots[i].ver * (uint32_t)(2 * i + 1);
    return g;
}

int uit_still(uit_t *e, const moy_ui_ctx_t *c, int x, int y, int w, int h, int flags, int fs, int input)
{
    if (!e->still_ok || !e->nnodes || e->fresh || e->want || e->evat < e->nevs || e->trace_at < e->trace.n)
        return 0;
    if ((flags & (UIT_F_UP | UIT_F_DOWN)) || flags != e->flags || fs != e->fs || input != e->input
        || x != e->cx || y != e->cy || w != e->cw || h != e->ch)
        return 0;
    if (still_sum(e) != e->still_ver || e->has_focused != e->still_had
        || (e->has_focused && (e->focused.node != e->still_focus.node || e->focused.row != e->still_focus.row
                               || e->focused.gen != e->still_focus.gen
                               || strcmp(e->focused.id, e->still_focus.id))))
        return 0;
    const int32_t *in = c->in;
    if (in[MOY_UI_IN_BTNP] || in[MOY_UI_IN_KEY] || in[MOY_UI_IN_REP] || in[MOY_UI_IN_KEYREP]
        || in[MOY_UI_IN_WHEEL_X] || in[MOY_UI_IN_WHEEL_Y] || (in[MOY_UI_IN_PTR] & (MOY_UI_P_HELD | MOY_UI_P_EDGE))
        || c->act || c->press)
        return 0;
    return !moy_ui_api_moved(c) && !moy_ui_api_moving(c) && !moy_ui_api_owes(c);
}

int uit_frame(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int x, int y, int w, int h,
              int flags, int fs, int input)
{
    if (!e->nnodes)
        return 0;
    e->flags = flags & ~(UIT_F_UP | UIT_F_DOWN);
    e->fs = fs;
    e->input = input;
    e->cx = (int16_t)x, e->cy = (int16_t)y, e->cw = (int16_t)w, e->ch = (int16_t)h;
    e->scratch.n = 0;
    int pages = (flags >> 8) & 0xff;
    uit_id_t last = e->focused;             // the last frame's focus
    int had_last = e->has_focused;
    // the page
    moy_ui_over_t o;
    moy_ui_over_none(&o);
    const char *pid = uit_str(e, e->page_id);
    moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(pid, strlen(pid)));
    if (flags & UIT_F_BARE) {
        int32_t one = 1;
        moy_ui_over_put(&o, MOY_UI_OV_BARE, &one);
    }
    // the engine values the page's text names, as this frame begins (its gens count them)
    e->gv = 0;
    if (e->gen_vals & GV_FOCUS) {
        char fv[UIT_ID_MAX + 1];
        int n = moy_ui_api_focus(c, span_of(NULL, 0), fv, sizeof fv);
        uint32_t h = 2166136261u;
        for (int i = 0; i < n; i++)
            h = (h ^ (uint8_t)fv[i]) * 16777619u;
        e->gv += h;
    }
    if (e->gen_vals & GV_INPUT)
        e->gv += (uint32_t)(input + 1) * 40503u;
    if ((flags & UIT_F_GEN) && e->gen_safe) {
        uint32_t g = (uint32_t)e->gen + e->edits + e->gv;
        for (int i = 0; i < e->nslots; i++)
            if (e->slots[i].in_gen)
                g += e->slots[i].ver;
        int32_t gen = (int32_t)(g & 0x7fffffff);
        moy_ui_over_put(&o, MOY_UI_OV_GEN, &gen);
    }
    // a handheld's foot lies outside the page, the keys band (the content's last line) under it
    e->foot = (int16_t)foot_of(e);
    int32_t kband[4];
    int has_kband = 0;
    if (e->foot >= 0 && !(flags & UIT_F_DESK)) {
        int32_t rest[4] = {x, y, w, h}, body[4], tmp[4];
        if (moy_ui_api_hints_w(c, s, (const uint8_t *)"back\0Back", 9) > 0) {
            moy_ui_cut(rest, MOY_UI_CUT_BOTTOM, 20 * fs, kband, tmp);
            memcpy(rest, tmp, sizeof tmp);
            has_kband = 1;
        }
        moy_ui_cut(rest, MOY_UI_CUT_BOTTOM, 22 * fs + foot_line(e, e->foot), e->foot_r, body);
        moy_ui_api_at(c, body[0], body[1], body[2], body[3]);
    }
    uint32_t n, at = text(e, 0, 0, -1, -1, &n);
    moy_ui_box_t box;
    moy_ui_api_page(c, s, sspan(e, at, n), &o, &box);
    e->pw = box.w;
    e->rect[0] = box.x, e->rect[1] = box.y, e->rect[2] = box.w, e->rect[3] = box.h;
    // what focus is on as the page begins
    char f[UIT_ID_MAX + 1];
    int fl = moy_ui_api_focus(c, span_of(NULL, 0), f, sizeof f);
    const uit_id_t *now = fl >= 0 ? id_find(e->ids, e->nids, f) : NULL;
    if (now) {
        e->focused = *now;
        e->has_focused = 1;
    } else if (fl == 0 && e->has_focused) {
        int d = (flags & UIT_F_DOWN) ? 1 : (flags & UIT_F_UP) ? -1 : 0;
        e->has_focused = 0;
        if (d) {
            stops_t st = {0};
            stops_of(e, 0, -1, -1, &st);
            for (int i = 0; i < st.n; i++)
                if (e->focused.gen == e->gen && st.s[i].node == e->focused.node && st.s[i].row == e->focused.row) {
                    int j = i + d;
                    if (j < 0 || j >= st.n)
                        j = i;
                    const uit_node_t *N = NODE(st.s[j].node);
                    e->focused.node = st.s[j].node;
                    e->focused.row = st.s[j].row;
                    e->focused.act = N->act;
                    e->focused.kind = N->kind;
                    e->has_focused = 1;
                    break;
                }
            UIT_FREE(st.s);
        }
    }
    if (!e->has_focused && !e->want) {
        stops_t st = {0};
        stops_of(e, 0, -1, -1, &st);
        if (st.n) {
            const uit_node_t *N = NODE(st.s[0].node);
            e->focused.node = st.s[0].node;
            e->focused.row = st.s[0].row;
            e->focused.act = N->act;
            e->focused.kind = N->kind;
            e->focused.gen = e->gen;
            e->has_focused = 1;
        }
        UIT_FREE(st.s);
    }
    // the hints band
    uit_buf_t hb = {0};
    hints(e, &hb, pages ? pages : 1);
    if (hb.n && (flags & UIT_F_BAND)) {
        static const char BAR[] = "change\0Move\0confirm\0Open\0back\0Back";
        const uint8_t *hp = (flags & UIT_F_ON_BAR) ? (const uint8_t *)BAR : (const uint8_t *)hb.p;
        size_t hn = (flags & UIT_F_ON_BAR) ? sizeof BAR - 1 : hb.n - 1;
        int placed = e->foot >= 0;
        KIDS(0, k)
        if (NODE(k)->kind == UIT_SPLIT && uit_shown(e, k, -1, -1))
            placed = 1;
        if (!placed) {
            moy_ui_api_hints(c, s, hp, hn, NULL);
        } else if (has_kband) {
            // under the foot, outside the page's ground
            fill_token(s, kband[0], kband[1], kband[2], kband[3], MOY_UI_T_PANEL);
            moy_ui_api_at(c, kband[0], kband[1], kband[2], kband[3]);
            moy_ui_api_hints(c, s, hp, hn, NULL);
            moy_ui_api_at(c, e->rect[0], e->rect[1], e->rect[2], e->rect[3]);
        } else if (moy_ui_api_hints_w(c, s, hp, hn) > 0) {
            // a placed page's band: a handheld's its room's last line, the desk's at the page's foot
            int32_t band[4], rest[4];
            int mn, hh = 20 * fs;
            if (flags & UIT_F_DESK)
                hh = moy_ui_metrics(MOY_UI_K_VALUE_ROW, &mn)[17] * fs;
            moy_ui_cut(e->rect, 1, hh, band, rest);
            if (!(flags & UIT_F_DESK))
                moy_ui_api_at(c, band[0], band[1], band[2], band[3]);
            moy_ui_api_hints(c, s, hp, hn, NULL);
            memcpy(e->rect, rest, sizeof rest);
        }
    }
    if (e->foot >= 0 && (flags & UIT_F_DESK)) {
        // the desk's foot: docked above the band, on the page's ground
        int32_t got[4];
        if (moy_ui_api_dock(c, 44 + foot_line(e, e->foot), got)) {
            memcpy(e->foot_r, got, sizeof got);
            e->rect[3] = got[1] - e->rect[1] > 0 ? got[1] - e->rect[1] : 0;
        }
    }
    // this frame's ids
    uit_id_t *t = e->prev;
    int tc = e->cprev;
    e->prev = e->ids;
    e->cprev = e->cids;
    e->nprev = e->nids;
    e->ids = t;
    e->cids = tc;
    e->nids = 0;
    e->dup = 0;
    char ahead[UIT_ID_MAX + 1];
    int moved = land_focus(e, c, ahead);
    e->tv_stop_now = 0;
    walk(e, c, s, 0, -1, -1);
    e->tv_stop = e->tv_stop_now;
    // after: what holds focus, and whether the band named another node's verbs
    int had = e->has_focused;
    uit_id_t was = e->focused;
    focus_after(e, c, moved, ahead, had_last ? &last : NULL);
    int owe = 0;
    if (had != e->has_focused || (had && (was.node != e->focused.node || was.gen != e->focused.gen))) {
        uit_buf_t hb2 = {0};
        hints(e, &hb2, pages ? pages : 1);
        owe = hb2.n != hb.n || (hb.n && memcmp(hb.p, hb2.p, hb.n));
        uit_buf_free(&hb2);
    }
    uit_buf_free(&hb);
    e->still_ver = still_sum(e);
    e->still_focus = e->focused;
    e->still_had = (uint8_t)e->has_focused;
    e->still_ok = !owe;
    return (e->nevs - e->evat) | (owe ? 1 << 16 : 0);
}
