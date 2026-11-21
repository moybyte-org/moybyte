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
    if (N->mark >= 0) {
        int32_t on = uit_cond_eval(e, N->mark, model, row) != 0;
        moy_ui_over_put(o, MOY_UI_OV_CURRENT, &on);
    }
    if (N->t_icon >= 0) {
        uint32_t n, at = uit_tmpl_render(e, N->t_icon, model, row, &n);
        if (n)
            moy_ui_api_icon(c, sspan(e, at, n));
    } else if (N->act >= 0 && uit_str(e, e->acts[N->act].icon)[0]) {
        const char *ic = uit_str(e, e->acts[N->act].icon);      // the action's, where the node names none
        moy_ui_api_icon(c, span_of(ic, strlen(ic)));
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
        } else if (N->kind == UIT_DIALOG || N->kind == UIT_POPOVER || (N->kind == UIT_TOOLS && (N->lit & TL_LEND))) {
            continue;               // drawn over the page, or at the site it is lent to
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
        if (N->kind == UIT_TOOLS && (N->lit & TL_LEND))
            continue;
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
static void placed_popover(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k);

static void draw_popover(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    if (e->placed) {
        placed_popover(e, c, s, k);
        return;
    }
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

// -- a long rows list: the rows in view ------------------------------------------------------
//
// A rows list of more than UIT_VROWS_MIN rows stacked on the page itself (its ancestors the page,
// groups and switches; ui.view answers) lays out only the rows the view shows, UIT_VROWS_MARGIN
// more each side, widened to whole blocks of UIT_VROWS_BLOCK (so a shift frame's stops match the
// last frame's but where the window crosses a block), and the focused row: every other row is
// space as tall as it last drew (a row never drawn: the height of the first that drew). The
// page's rows total is what a whole walk makes where the rows are alike, its stops only the
// drawn rows'. uitree_app.Engine._r_rows lays out the same rows.
#define UIT_VROWS_MIN 32
#define UIT_VROWS_MARGIN 2
#define UIT_VROWS_BLOCK 4

static int vrows_ok(uit_t *e, int k)
{
    for (int p = NODE(k)->parent; p >= 0 && p != (int)UIT_NONE; p = NODE(p)->parent) {
        int kd = NODE(p)->kind;
        if (kd != UIT_PAGE && kd != UIT_GROUP && kd != UIT_SWITCH && kd != UIT_CASE)
            return 0;
    }
    return 1;
}

static uit_vrows_t *vrows_of(uit_t *e, int k, int n, int w, int fs)
{
    const char *key = uit_str(e, NODE(k)->key);
    uint32_t hk = 2166136261u;
    for (const char *p = key; *p; p++)
        hk = (hk ^ (uint8_t)*p) * 16777619u;
    uit_vrows_t *V = NULL;
    for (int i = 0; i < e->nvr; i++)
        if (e->vr[i].key == hk)
            V = &e->vr[i];
    if (!V) {
        if (e->nvr == e->cvr) {
            int cap = e->cvr ? 2 * e->cvr : 2;
            uit_vrows_t *a = UIT_REALLOC(e->vr, (size_t)cap * sizeof(*a));
            if (!a)
                return NULL;
            e->vr = a, e->cvr = cap;
        }
        V = &e->vr[e->nvr++];
        memset(V, 0, sizeof(*V));
        V->key = hk;
        V->unit = -1;
        V->n = -1;
    }
    if (V->n != n || V->w != w || V->fs != fs) {
        if (V->cap < n) {
            int16_t *h = UIT_REALLOC(V->h, (size_t)n * sizeof(int16_t));
            if (!h)
                return NULL;
            V->h = h, V->cap = n;
        }
        for (int i = 0; i < n; i++)
            V->h[i] = -1;
        V->n = n, V->w = (int16_t)w, V->fs = (uint8_t)fs;
    }
    return V;
}

static void walk(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row);

// v: ui.view's answer. 0 where it could not keep its heights (the whole list walks instead).
static int draw_vrows(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, const int32_t *v)
{
    const uit_node_t *N = NODE(k);
    int nr = e->slots[N->slot].nrows;
    uit_vrows_t *V = vrows_of(e, k, nr, v[3], v[4]);
    if (!V)
        return 0;
    int16_t *h = V->h;
    int next = 0;
    if (V->unit < 0) {
        int y0 = c->y;
        walk(e, c, s, k, N->slot, 0);
        h[0] = (int16_t)(c->y - y0);
        V->unit = h[0] > 0 ? h[0] : 1;
        next = 1;
    }
    int u = V->unit, top = v[1], bot = v[1] + v[2], pos = v[0], first = -1, last = nr;
    for (int i = 0; i < nr; i++) {
        if (pos >= bot) {
            last = i;
            break;
        }
        pos += h[i] >= 0 ? h[i] : u;
        if (first < 0 && pos > top)
            first = i;
    }
    if (first < 0)
        first = last;
    first -= UIT_VROWS_MARGIN;
    first = first < 0 ? 0 : first - first % UIT_VROWS_BLOCK;
    last += UIT_VROWS_MARGIN + UIT_VROWS_BLOCK - 1;
    last -= last % UIT_VROWS_BLOCK;
    last = last > nr ? nr : last;
    // the focused row, where it lies outside them
    int f = -1;
    char fid[UIT_ID_MAX + 1];
    if (moy_ui_api_focus(c, span_of(NULL, 0), fid, sizeof fid) > 0) {
        const char *sl = strrchr(fid, '/');
        size_t fn = sl ? (size_t)(sl - fid) : 0;
        for (int r = first; sl && r < last && f < 0; r++) {
            const char *rk = row_key(e, N->slot, r);
            if (strlen(rk) == fn && !memcmp(rk, fid, fn))
                f = r;
        }
        if (sl && f < 0)
            f = uit_row_find(e, N->slot, fid, fn);
    }
    int skip = 0;
    for (int r = next; r < nr; r++) {
        if ((r < first || r >= last) && r != f) {
            skip += h[r] >= 0 ? h[r] : u;
            continue;
        }
        moy_ui_api_space(c, skip);
        skip = 0;
        int y0 = c->y;
        walk(e, c, s, k, N->slot, r);
        h[r] = (int16_t)(c->y - y0);
    }
    moy_ui_api_space(c, skip);
    return 1;
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
        if ((N->btn & BT_ALONE) && (N->t_icon >= 0 || (N->act >= 0 && uit_str(e, e->acts[N->act].icon)[0])))
            n1 = 0;                 // its icon alone
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
        int32_t vw[5];
        if (N->t_main < 0 && M->nrows > UIT_VROWS_MIN && vrows_ok(e, k) && moy_ui_api_view(c, vw)
            && draw_vrows(e, c, s, k, vw))
            break;
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
            // a row drawn last frame that its model still holds (a list filtered since keeps
            // fewer): never "" for a row past its end
            int m = I->gen == e->gen ? uit_model_of(e, I->node) : -1;
            hit = m >= 0 && I->row < e->slots[m].nrows && *want && !strcmp(row_key(e, m, I->row), want);
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

static int is_menu_rows(uit_t *e, int k);
static void menu_row_id(uit_t *e, int link, int m, int r, char id[UIT_ID_MAX + 1]);

static int row_stop_id(uit_t *e, int lst, const char *key, size_t n, char out[UIT_ID_MAX + 1])
{
    int r = uit_row_find(e, NODE(lst)->slot, key, n);
    if (r < 0)
        return 0;
    if (e->placed && is_menu_rows(e, lst)) {
        menu_row_id(e, NODE(lst)->kid, NODE(lst)->slot, r, out);     // a menu row's id is its text
        return 1;
    }
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
// -- the placed layout ------------------------------------------------------------------------
//
// One rule, the engine's for every placed surface (the Editor's pages; the window manager's
// bar, dock and chrome by the same rule): a container gives each shown child a rect along its
// axis, a child fixed (its kind's height, `fixed_h`) or a fill (the fills share what the fixed
// leave); a text editor takes whole lines and hands the rest to the child under it; a row lent
// to a site takes none; popovers and dialogs draw last, over it. A flowing page is the case
// whose one fill is the page itself, which the toolkit stacks (`walk`); a placed page
// (uit_set_placed) is stacked by this rule from its root. The pass that draws a child records
// its rect (`uit_rect`); `uit_place` runs the pass with nothing drawn.

enum { PLACE_KIDS = 48 };

void uit_set_placed(uit_t *e, int on)
{
    e->placed = (uint8_t)(on != 0);
}

void uit_set_ground(uit_t *e, int x, int y, int w, int h)
{
    e->ground[0] = (int16_t)x, e->ground[1] = (int16_t)y, e->ground[2] = (int16_t)w, e->ground[3] = (int16_t)h;
}

static int32_t fdiv(int32_t a, int32_t b)
{
    int32_t q = a / b;
    return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q;
}

// The rect table made ready for a pass: every node unplaced.
static int rects_ready(uit_t *e)
{
    if (e->crects < e->nnodes) {
        int16_t *r = UIT_REALLOC(e->rects, (size_t)e->nnodes * 4 * sizeof *r);
        if (!r)
            return 0;
        e->rects = r, e->crects = e->nnodes;
    }
    for (int i = 0; i < e->nnodes; i++)
        e->rects[4 * i + 2] = -1;
    return 1;
}

static void put_rect(uit_t *e, int k, const int32_t r[4])
{
    if (k < e->crects) {
        int16_t *p = e->rects + 4 * k;
        p[0] = (int16_t)r[0], p[1] = (int16_t)r[1], p[2] = (int16_t)r[2], p[3] = (int16_t)r[3];
    }
}

int uit_rect(const uit_t *e, const char *key, int32_t out[4])
{
    for (int k = (e->crects < e->nnodes ? e->crects : e->nnodes) - 1; k >= 0; k--) {
        const int16_t *p = e->rects + 4 * k;
        if (p[2] >= 0 && !strcmp(uit_str(e, e->nodes[k].key), key)) {
            out[0] = p[0], out[1] = p[1], out[2] = p[2], out[3] = p[3];
            return 1;
        }
    }
    return 0;
}

// A placed row's height: 48 on the desk, 22 at the scale on a handheld.
static int32_t h_row(const uit_t *e)
{
    return (e->flags & UIT_F_DESK) ? 48 : 22 * e->fs;
}

// Whether font `f` is a version 2 face (moy_text.h: its line holds its leading).
static int font_v2(uint32_t f)
{
    moy_font_info_t i;
    return f && moy_font_info(f, &i) && i.first == 0;
}

static uint32_t role_font(int role, moy_font_info_t *fi)
{
    return moy_ui_font(moy_ui_cart_on(), role, fi);
}

// A text editor's line pitch: a version 2 mono face's glyph box, else its line and a lead.
static int32_t pitch(const uit_t *e)
{
    moy_font_info_t fi, gi;
    uint32_t f = role_font(MOY_UI_FONT_MONO, &fi);
    int v2 = f && moy_font_info(f, &gi) && gi.first == 0;
    return (v2 ? gi.h : fi.line + 2) * e->fs;
}

// A status band's height: 20 under a version 2 `ui` face, 12 under petme128, never under the
// face's line and a pixel above and below.
static int32_t status_h(const uit_t *e)
{
    moy_font_info_t fi;
    uint32_t f = role_font(MOY_UI_FONT_UI, &fi);
    int32_t least = font_v2(f) ? 20 : 12;
    return (least > fi.line + 2 ? least : fi.line + 2) * e->fs;
}

// A child's height along a stack: > 0 fixed, -1 a fill, 0 none (a row lent to a site).
static int32_t fixed_h(uit_t *e, int k)
{
    const uit_node_t *N = NODE(k);
    switch (N->kind) {
    case UIT_CANVAS:
        return N->btn ? N->btn * ((e->flags & UIT_F_DESK) ? 1 : e->fs) : -1;
    case UIT_SWITCH:
    case UIT_CASE:
    case UIT_PANES:
    case UIT_PANE:
    case UIT_TEXT_EDITOR:
    case UIT_SLOT:
    case UIT_PANEL:
    case UIT_LEGACY:
        return -1;
    case UIT_STATUS:
        return status_h(e);
    case UIT_TOOLS:
        if ((N->lit & TL_LEND) || (N->parent != UIT_NONE && NODE(N->parent)->kind == UIT_PANE &&
                                   (e->flags & UIT_F_DESK)))
            return 0;
        break;
    case UIT_LABEL:
        if ((N->nbtn & LT_LINES) || (N->nbtn & LT_WRAP) == LT_WORD) {
            moy_font_info_t fi;
            role_font(MOY_UI_FONT_UI, &fi);
            return (((N->nbtn & LT_LINES) ? N->btn : 2) * (fi.line + 2) + 6) * e->fs;
        }
        break;
    case UIT_ROW: {
        int st = 0, field = 0;
        if (N->lit & RW_GROW)
            return -1;                  // a growing row: a fill of its stack
        KIDS(k, g) {
            int gk = NODE(g)->kind;
            if (gk == UIT_CASE || gk == UIT_SWITCH || gk == UIT_PANEL)
                return -1;              // a row of regions: the rule turned, a fill of its stack
            st |= gk == UIT_STATUS;
            field |= gk == UIT_FIELD;
        }
        if (st && !field)
            return status_h(e);         // a status line's
        break;
    }
    }
    return h_row(e);
}

// Shown in a placed container: its condition holds and its action is on (or greys).
static int placed_shown(uit_t *e, int k, int model, int row)
{
    const uit_node_t *N = NODE(k);
    return uit_shown(e, k, model, row) &&
           (N->act < 0 || uit_act_can(e, N->act, model, row) || e->acts[N->act].off == 2);
}

static const char SEP[] = " \xc2\xb7 ";           // a status template's fields are split at it

// The width of `n` bytes in font `f` at `scale`.
static int32_t text_px(uint32_t f, const char *p, size_t n, int scale)
{
    return moy_text_width(f, (const uint8_t *)p, n, scale);
}

static void place_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                     const int32_t r[4]);
static void pn_draw(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                    const int32_t r[4]);

// `n` bytes onto a line of text (its NUL kept past its length).
static void line_put(uit_buf_t *b, const char *p, size_t n)
{
    if (uit_buf_put(b, p, n) >= 0)
        b->n--;
}

// The shown children of `k` down `r`: fixed rows their height, the fills sharing the rest.
static void stack(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                  const int32_t r[4])
{
    int16_t kids[PLACE_KIDS];
    int32_t hs[PLACE_KIDS], fixed = 0;
    int n = 0, fills = 0;
    int body = NODE(k)->kind == UIT_PANE;      // a pane's body: its tools are its header's
    KIDS(k, g) {
        int kind = NODE(g)->kind;
        if (n == PLACE_KIDS || kind == UIT_POPOVER || kind == UIT_DIALOG || (body && kind == UIT_TOOLS) ||
            !placed_shown(e, g, model, row))
            continue;
        kids[n] = (int16_t)g;
        hs[n] = fixed_h(e, g);
        if (hs[n] > 0)
            fixed += hs[n];
        else if (hs[n] < 0)
            fills++;
        n++;
    }
    int32_t room = r[3] - fixed > 0 ? r[3] - fixed : 0, spare = 0, y = r[1];
    if (!fills)
        fills = 1;
    for (int i = 0; i < n; i++) {
        int32_t kh = hs[i];
        if (!kh)
            continue;
        kh = (kh < 0 ? room / fills : kh) + spare;
        spare = 0;
        if (NODE(kids[i])->kind == UIT_TEXT_EDITOR && i < n - 1) {
            int32_t p = pitch(e);
            spare = p > 0 ? kh % p : 0;     // whole lines; the child under it takes the rest
            kh -= spare;
        }
        int32_t cell[4] = {r[0], y, r[2], kh};
        place_at(e, c, s, kids[i], model, row, cell);
        y += kh;
    }
    if (e->placing)
        return;
    KIDS(k, g) {
        int kind = NODE(g)->kind;
        if ((kind == UIT_POPOVER || kind == UIT_DIALOG) && e->nlate < (int)(sizeof e->late / sizeof *e->late) &&
            placed_shown(e, g, model, row))
            e->late[e->nlate++] = (int16_t)g;       // over every child, after the page
    }
}

// A status line: its first field always, the others while they fit, joined by the `ui`
// face's separator, its words on the page's text column (the text view's inset).
static void draw_status(uit_t *e, const moy_ui_sink_t *s, int k, int model, int row, const int32_t r[4])
{
    int mn, fs = e->fs;
    int pad = moy_ui_metrics(MOY_UI_K_STATUS, &mn)[1] * fs;
    uint32_t n, at = text(e, k, 0, model, row, &n);
    moy_font_info_t fi;
    uint32_t f = role_font(MOY_UI_FONT_UI, &fi);
    const char *p = e->scratch.p + at, *end = p + n;
    const char *sep = font_v2(f) ? "  \xc2\xb7  " : " - ";
    size_t sl = strlen(sep), ql = sizeof SEP - 1;
    uit_buf_t line = {0};
    const char *cut = p;
    while (cut + ql <= end && memcmp(cut, SEP, ql))
        cut++;
    if (cut + ql > end)
        cut = end;
    line_put(&line, p, (size_t)(cut - p));
    int32_t room = r[2] - 2 * pad;
    while (cut < end) {
        const char *f0 = cut + ql, *f1 = f0;
        while (f1 + ql <= end && memcmp(f1, SEP, ql))
            f1++;
        if (f1 + ql > end)
            f1 = end;
        size_t was = line.n;
        line_put(&line, sep, sl);
        line_put(&line, f0, (size_t)(f1 - f0));
        if (text_px(f, line.p, line.n, fs) > room) {
            line.n = was;
            break;
        }
        cut = f1;
    }
    int32_t x = r[0], tx = x + 8 * fs - pad;
    moy_ui_box_t b = {(int16_t)x, (int16_t)r[1], (int16_t)r[2], (int16_t)r[3]};
    if (tx > x) {
        moy_ui_box_t g = {(int16_t)x, (int16_t)r[1], (int16_t)(tx - x), (int16_t)r[3]};
        moy_ui_status(s, g, (const uint8_t *)"", 0, 0, s->fs);
        b.x = (int16_t)tx, b.w = (int16_t)(r[2] - (tx - x));
    }
    moy_ui_status(s, b, (const uint8_t *)(line.p ? line.p : ""), line.n, 1, s->fs);
    uit_buf_free(&line);
}

// A placed row's unit: the host's (`uit_set_unit`), else 1 on a desk and the font scale else.
static int32_t row_unit(const uit_t *e)
{
    return e->unit > 0 ? e->unit : (e->flags & UIT_F_DESK) ? 1 : e->fs;
}

// A length prop (template `t`) in pixels: a literal in the row's unit, an int state as the host
// measured it; `dflt` (pixels) where none is given.
static int32_t len_of(uit_t *e, int t, int model, int row, int32_t dflt)
{
    if (t < 0)
        return dflt;
    int32_t v = int_of(e, t, model, row, 0);
    return e->tmpls[t].lit ? v * row_unit(e) : v;
}

static int32_t row_gap(const uit_t *e)
{
    return (e->flags & UIT_F_DESK) ? 4 : 2;
}

static int row_kids(uit_t *e, int k, int model, int row, int16_t *kids, int *st, int *st_shown)
{
    int n = 0;
    KIDS(k, g) {
        int kind = NODE(g)->kind;
        *st |= kind == UIT_STATUS;
        if (n == PLACE_KIDS || !placed_shown(e, g, model, row))
            continue;
        *st_shown |= kind == UIT_STATUS;
        kids[n++] = (int16_t)g;
    }
    return n;
}

// A row cell's natural width, or -1 where it grows (`_natural`).
static int32_t natural(uit_t *e, int k, int model, int row, int32_t h)
{
    const uit_node_t *N = NODE(k);
    uint32_t n, at;
    if (N->kind == UIT_BUTTON) {
        if (N->t_value >= 0)
            return len_of(e, N->t_value, model, row, 0);     // a bar item: its own width
        if (N->t_icon >= 0 && (N->btn & BT_ALONE))
            return h;
        at = text(e, k, 0, model, row, &n);
        return text_px(moy_text_print_font(), e->scratch.p + at, n, 1) + 28;
    }
    if (N->kind == UIT_STATUS) {
        at = text(e, k, 0, model, row, &n);
        const char *p = e->scratch.p + at;
        size_t ql = sizeof SEP - 1;
        for (size_t i = 0; i + ql <= n; i++)
            if (!memcmp(p + i, SEP, ql))
                return -1;              // a line of fields grows
        moy_font_info_t fi;
        int mn, pad = moy_ui_metrics(MOY_UI_K_STATUS, &mn)[1] * e->fs;
        return text_px(role_font(MOY_UI_FONT_UI, &fi), p, n, e->fs) + 2 * pad + 2;
    }
    if (N->kind == UIT_CANVAS && N->t_value >= 0 && !(N->lit & CV_GROW)) {
        if (e->tmpls[N->t_value].lit && !int_of(e, N->t_value, model, row, 0))
            return -1;                  // w=0 grows
        return len_of(e, N->t_value, model, row, 0);     // a fixed cell along x, as h= down
    }
    if (N->kind == UIT_ROW) {
        // a row in a row: its cells' widths and gaps where none grows; one with nothing shown
        // a spacer
        int16_t kids[PLACE_KIDS];
        int st = 0, sts = 0, nk = row_kids(e, k, model, row, kids, &st, &sts);
        int32_t sum = 0;
        if (!nk)
            return -1;
        for (int i = 0; i < nk; i++) {
            int32_t v = natural(e, kids[i], model, row, h);
            if (v < 0)
                return -1;
            sum += v;
        }
        return sum + len_of(e, N->t_value, model, row, row_gap(e)) * (nk > 1 ? nk - 1 : 0) +
               2 * len_of(e, N->t_aux, model, row, 0);
    }
    return -1;
}

// Cells i0..i1 their widths: a fixed one its own, the fills their basis and an even share of what
// `room` leaves, the last of them the remainder.
static void row_share(const int32_t *nat, const int32_t *base, int32_t *ws, int i0, int i1, int32_t room)
{
    int grow = 0, last = -1;
    for (int i = i0; i < i1; i++)
        if (nat[i] < 0)
            grow++, last = i, room -= base[i];
        else
            room -= nat[i];
    int32_t each = fdiv(room, grow > 1 ? grow : 1);
    for (int i = i0; i < i1; i++) {
        int32_t v = nat[i] >= 0 ? nat[i] : base[i] + each + (i == last ? room - each * (grow > 1 ? grow : 1) : 0);
        ws[i] = v < 0 ? 0 : v;
    }
}

// A row's cells across it: each its natural width, the growing ones sharing the rest, inside
// its pad= and gap= apart; a mid= row centred on it, each side's fills sharing that side's room.
static void row_cells(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                      const int32_t r[4])
{
    int16_t kids[PLACE_KIDS];
    int32_t nat[PLACE_KIDS], ws[PLACE_KIDS], base[PLACE_KIDS];
    int st = 0, st_shown = 0, mid = -1, grow = 0;
    int n = row_kids(e, k, model, row, kids, &st, &st_shown);
    const uit_node_t *R = NODE(k);
    if (st && !st_shown && !e->placing) {
        moy_ui_box_t b = {(int16_t)r[0], (int16_t)r[1], (int16_t)r[2], (int16_t)r[3]};
        moy_ui_status(s, b, (const uint8_t *)"", 0, 0, s->fs);     // a status row's band, its line hidden
    }
    for (int i = 0; i < n; i++) {
        nat[i] = natural(e, kids[i], model, row, r[3]);
        grow += nat[i] < 0;
        if (mid < 0 && NODE(kids[i])->kind == UIT_ROW && (NODE(kids[i])->lit & RW_MID))
            mid = i;
    }
    if (!grow)
        for (int i = 0; i < n; i++)
            if (NODE(kids[i])->kind == UIT_TABS) {
                nat[i] = -1;            // a row of fixed cells: its tabs take the rest
                break;
            }
    for (int i = 0; i < n; i++)         // a fill's basis: a growing canvas's w=, its share over it
        base[i] = nat[i] < 0 && NODE(kids[i])->kind == UIT_CANVAS ? len_of(e, NODE(kids[i])->t_value, model, row, 0) : 0;
    int32_t gap = R->kind == UIT_ROW ? len_of(e, R->t_value, model, row, row_gap(e)) : row_gap(e);
    int32_t pad = R->kind == UIT_ROW ? len_of(e, R->t_aux, model, row, 0) : 0;
    int32_t x0 = r[0] + pad, x1 = r[0] + r[2] - pad, ms = 0, b = 0;
    if (mid < 0)
        row_share(nat, base, ws, 0, n, x1 - x0 - gap * (n > 1 ? n - 1 : 0));
    else {
        int32_t a = x0 + gap * mid, cx = r[0] + fdiv(r[2], 2);
        b = x1 - gap * (n - 1 - mid);
        for (int i = 0; i < n; i++)
            if (i != mid) {
                int32_t v = nat[i] >= 0 ? nat[i] : base[i];
                if (i < mid)
                    a += v;
                else
                    b -= v;
            }
        if (nat[mid] < 0) {             // a mid of fills: the widest span centred that clears both
            int32_t half = cx - a < b - cx ? cx - a : b - cx;
            if (half < 0)
                half = 0;
            ms = cx - half, ws[mid] = 2 * half;
        } else
            ms = r[0] + fdiv(r[2] - nat[mid], 2), ws[mid] = nat[mid];
        row_share(nat, base, ws, 0, mid, ms - x0 - gap * mid);
        row_share(nat, base, ws, mid + 1, n, x1 - (ms + ws[mid]) - gap * (n - 1 - mid));
    }
    int32_t x = x0;
    for (int i = 0; i < n; i++) {
        int g = kids[i];
        if (i == mid)
            x = ms;
        else if (mid >= 0 && i == mid + 1) {
            x = x1 - gap * (n - 2 - mid);
            for (int j = mid + 1; j < n; j++)
                x -= ws[j];
        }
        int32_t kw = ws[i];
        int32_t cell[4] = {x, r[1] + 2, kw, r[3] - 4};
        int gk = NODE(g)->kind;
        if (gk == UIT_STATUS || gk == UIT_CASE || gk == UIT_SWITCH || gk == UIT_PANEL || gk == UIT_CANVAS ||
            gk == UIT_ROW || (gk == UIT_BUTTON && NODE(g)->lit == MOY_UI_BUTTON_BAR))
            cell[1] = r[1], cell[3] = r[3];     // a band, a region, a bar item: the row's height
        if (e->placing || gk == UIT_ROW)
            place_at(e, c, s, g, model, row, cell);     // a placement records the cells, drawing none
        else if (gk == UIT_BUTTON) {
            put_rect(e, g, cell);
            moy_ui_api_at(c, cell[0], cell[1], cell[2], cell[3]);
            draw(e, c, s, g, model, row);
        } else {
            moy_ui_api_at(c, cell[0], cell[1], cell[2], cell[3]);
            place_at(e, c, s, g, model, row, cell);
        }
        x += kw + gap;
    }
}

// A handheld's tool row: a cell each across the width, a word's its width (two cells at
// most), the icons sharing the rest.
static void tool_cells(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                       const int32_t r[4])
{
    int16_t kids[PLACE_KIDS];
    int32_t ws[PLACE_KIDS], words = 0;
    int n = 0, icons = 0;
    KIDS(k, g)
    if (n < PLACE_KIDS && placed_shown(e, g, model, row))
        kids[n++] = (int16_t)g;
    int nk = n > 1 ? n : 1;
    for (int i = 0; i < n; i++) {
        if (NODE(kids[i])->btn & BT_ALONE) {
            ws[i] = -1;
            icons++;
            continue;
        }
        int32_t v = natural(e, kids[i], model, row, r[3]), most = fdiv(2 * r[2], nk);
        ws[i] = v < most ? v : most;
        words += ws[i];
    }
    int32_t rest = r[2] - words, x = r[0];
    int j = 0, ic = icons > 1 ? icons : 1;
    for (int i = 0; i < n; i++) {
        int32_t kw = ws[i];
        if (kw < 0) {
            kw = fdiv(rest * (j + 1), ic) - fdiv(rest * j, ic);
            j++;
        }
        int32_t cell[4] = {x, r[1] + 1, kw - 1, r[3] - 2};
        put_rect(e, kids[i], cell);
        moy_ui_api_at(c, cell[0], cell[1], cell[2], cell[3]);
        draw(e, c, s, kids[i], model, row);
        x += kw;
    }
}

// A panel: the desk's and the handheld's arrangements stacked; any other its ground and its
// words down its middle.
static void draw_panel(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                       const int32_t r[4])
{
    const char *key = uit_str(e, NODE(k)->key);
    if (!strcmp(key, "hand") || !strcmp(key, "desk")) {
        stack(e, c, s, k, model, row, r);
        return;
    }
    if (e->placing)
        return;
    int32_t g = (e->flags & UIT_F_DESK) ? 6 : 4;
    fill_token(s, r[0] + g, r[1] + g, r[2] - 2 * g, r[3] - 2 * g, MOY_UI_T_PANEL);
    int32_t ty = r[1] + fdiv(r[3], 2) - 4;
    KIDS(k, l) {
        if (NODE(l)->kind != UIT_LABEL || !placed_shown(e, l, model, row))
            continue;
        int32_t cell[4] = {r[0] + g, ty, r[2] - 2 * g, 16};
        place_at(e, c, s, l, model, row, cell);
        ty += 18;
    }
}

// A canvas in a placed page: a part of its own the front end draws into (its clip and ground
// the front end's).
static void draw_canvas_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row)
{
    const char *key = uit_str(e, NODE(k)->key);
    moy_ui_over_t o;
    int32_t got[5];
    moy_ui_over_none(&o);
    moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(key, strlen(key)));
    if (e->flags & UIT_F_BARE) {
        int32_t one = 1;
        moy_ui_over_put(&o, MOY_UI_OV_BARE, &one);       // a bare page's canvases sink no well
    }
    id_put(e, k, model, row, key);
    moy_ui_api_part(c, s, span_of(key, strlen(key)), 0, 0, &o, got);
    if (e->draw_fn)
        e->draw_fn(e->draw_ud, k, got[0], got[1], got[2], got[3]);
}

// Node `k` in its rect: the rect recorded, the node drawn there (in a placement, only the
// regions a host places are walked).
static void place_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                     const int32_t r[4])
{
    const uit_node_t *N = NODE(k);
    put_rect(e, k, r);
    switch (N->kind) {
    case UIT_PANES:
    case UIT_PANE:
    case UIT_LEGACY:
        pn_draw(e, c, s, k, model, row, r);
        return;
    case UIT_CASE:
        stack(e, c, s, k, model, row, r);
        return;
    case UIT_SWITCH: {
        int cs = uit_case_of(e, k);
        if (cs >= 0)
            stack(e, c, s, cs, model, row, r);
        return;
    }
    case UIT_PANEL:
        draw_panel(e, c, s, k, model, row, r);
        return;
    case UIT_ROW:
        row_cells(e, c, s, k, model, row, r);
        return;
    }
    if (e->placing)
        return;
    switch (N->kind) {
    case UIT_TOOLS:
        if (N->lit & TL_LEND)
            return;                     // drawn at the site it is lent to
        if (e->flags & UIT_F_DESK)
            row_cells(e, c, s, k, model, row, r);
        else
            tool_cells(e, c, s, k, model, row, r);
        return;
    case UIT_STATUS:
        draw_status(e, s, k, model, row, r);
        return;
    case UIT_BUTTON: {
        int32_t hh = h_row(e);
        moy_ui_api_at(c, r[0], r[1], r[2], r[3] < hh ? r[3] : hh);
        draw(e, c, s, k, model, row);
        return;
    }
    case UIT_CANVAS:
        moy_ui_api_at(c, r[0], r[1], r[2], r[3]);
        draw_canvas_at(e, c, s, k, model, row);
        return;
    case UIT_TEXT_EDITOR: {
        // its document is the front end's: it draws the editor's rows in the rect, under the
        // node's id (a stop)
        const char *key = uit_str(e, N->key);
        id_put(e, k, model, row, key);
        if (e->draw_fn)
            e->draw_fn(e->draw_ud, k, r[0], r[1], r[2], r[3]);
        return;
    }
    }
    moy_ui_api_at(c, r[0], r[1], r[2], r[3]);
    draw(e, c, s, k, model, row);
}

// -- the panes ------------------------------------------------------------------------
//
// A split (`ui.panes`) is the rule's container along its axis: each child held at its size
// (its size= state, else its start=, never under its minimum) and the one that grows the fill;
// where the fill falls under its minimum the held children give it room, last first, down to
// their own (the desk Editor's rule, `uitree_editor.fit`). Under the outermost panes' least size,
// or with a pane grown, one pane stands over the whole area. A placement records the
// arrangement and each hosted body's rect for the host; the dividers are `moy_ui_api_split`'s,
// drawn inline in the page's pass or into a host's own (`uit_splits`).

enum { PN_GUTTER = 6, PN_HEADER = 32, PN_FLOOR_W = 64, PN_FLOOR_H = PN_HEADER + 16, PN_DOUBLE_MS = 400 };

static int pn_shown(uit_t *e, int k);
static int offered(uit_t *e, int k, const char *v);

// Pane `k` offers tab value `v` (its offered= condition holds, or it names none for `v`).
static int pn_offers(uit_t *e, int k, const char *v)
{
    return offered(e, k, v);
}

// A pane's tab values on offer, counted; the first into *first (NULL none).
static int pn_tabs(uit_t *e, int k, const char **first)
{
    const uit_node_t *N = NODE(k);
    if (first)
        *first = NULL;
    if (N->slot < 0)
        return -1;
    const uit_slot_t *S = &e->slots[N->slot];
    const char *v = uit_str(e, S->vals);
    int n = 0;
    for (int i = 0; i < S->nvals; i++, v += strlen(v) + 1)
        if (pn_offers(e, k, v)) {
            if (first && !n)
                *first = v;
            n++;
        }
    return n;
}

static int pn_shown(uit_t *e, int k)
{
    const uit_node_t *N = NODE(k);
    if (!placed_shown(e, k, -1, -1))
        return 0;
    if (N->kind == UIT_PANE)
        return N->slot < 0 || pn_tabs(e, k, NULL) > 0;     // a pane left with no tab goes
    if (N->kind == UIT_PANES) {
        KIDS(k, g) if (pn_shown(e, g)) return 1;           // ...and a split left empty
        return 0;
    }
    return 1;
}

// A child's least size along `axis`, in pixels, its gutters in.
static int32_t pn_min(uit_t *e, int k, int axis)
{
    const uit_node_t *N = NODE(k);
    int32_t du = e->du;
    if (N->kind == UIT_PANE) {
        int32_t floor = axis ? PN_FLOOR_H : PN_FLOOR_W, d = floor, most = 0;
        const int16_t *m = e->btns + (N->nbtn ? N->mark : 0);
        if (!N->nbtn)
            return floor * du;
        if (m[0] == PN_MIN_ONE)
            return m[1 + axis] * du;
        for (int i = 0; i < N->nbtn; i++)
            if (m[3 * i] == PN_MIN_ANY)
                d = m[3 * i + 1 + axis];
        const uit_slot_t *S = &e->slots[N->slot];
        const char *v = uit_str(e, S->vals);
        for (int j = 0; j < S->nvals; j++, v += strlen(v) + 1) {      // by tab: the largest offered
            if (!pn_offers(e, k, v))
                continue;
            int32_t got = d;
            for (int i = 0; i < N->nbtn; i++)
                if (m[3 * i] != PN_MIN_ANY && !strcmp(uit_str(e, (uint16_t)m[3 * i]), v))
                    got = m[3 * i + 1 + axis];
            if (got > most)
                most = got;
        }
        return most * du;
    }
    if (N->kind != UIT_PANES)
        return 0;
    int32_t sum = 0, most = 0;
    int n = 0;
    KIDS(k, g) {
        if (!pn_shown(e, g))
            continue;
        int32_t m = pn_min(e, g, axis);
        sum += m, n++;
        if (m > most)
            most = m;
    }
    if (!!(N->lit & PN_COL) == axis)
        return sum + (int32_t)N->nbtn * du * (n > 1 ? n - 1 : 0);
    return most;
}

// A child's held size now, in pixels: its state, else its start (desk units); -1 it grows.
static int32_t pn_held(uit_t *e, int k)
{
    const uit_node_t *N = NODE(k);
    if ((N->lit & PN_GROW) || N->current < 0)
        return -1;
    const uit_slot_t *S = &e->slots[N->current];
    if (!S->absent)
        return S->i;
    int32_t st = (N->lit & PN_START) ? (int16_t)N->btn
                 : N->changed >= 0 && !e->slots[N->changed].absent ? e->slots[N->changed].i : 0;
    return st * e->du;
}

// The children's sizes along a span `total` long (`uitree_editor.fit`).
static void pn_fit(const int32_t *held, const int32_t *mins, int n, int32_t total, int32_t *sizes)
{
    int flex = n - 1;
    for (int i = n - 1; i >= 0; i--)
        if (held[i] < 0)
            flex = i;
    int32_t rest = total;
    for (int i = 0; i < n; i++) {
        sizes[i] = held[i] < 0 ? 0 : held[i] > mins[i] ? held[i] : mins[i];
        if (i != flex)
            rest -= sizes[i];
    }
    int32_t need = mins[flex] - rest;
    for (int i = n - 1; i >= 0 && need > 0; i--) {
        if (i == flex)
            continue;
        int32_t give = sizes[i] - mins[i] < need ? sizes[i] - mins[i] : need;
        if (give > 0)
            sizes[i] -= give, rest += give, need -= give;
    }
    sizes[flex] = rest > 0 ? rest : 0;
}

// The first shown pane under `k` in reading order whose node is `want` (-1: any), else -1.
static int pn_pane_in(uit_t *e, int k, int want)
{
    KIDS(k, g) {
        if (!pn_shown(e, g))
            continue;
        if (NODE(g)->kind == UIT_PANE && (want < 0 || g == want))
            return g;
        if (NODE(g)->kind == UIT_PANES) {
            int got = pn_pane_in(e, g, want);
            if (got >= 0)
                return got;
        }
    }
    return -1;
}

// The shown pane under `k` keyed `key` (n bytes), else -1.
static int pn_pane_keyed(uit_t *e, int k, const char *key, size_t n)
{
    KIDS(k, g) {
        if (!pn_shown(e, g))
            continue;
        if (NODE(g)->kind == UIT_PANE) {
            const char *gk = uit_str(e, NODE(g)->key);
            if (strlen(gk) == n && !memcmp(gk, key, n))
                return g;
        } else if (NODE(g)->kind == UIT_PANES) {
            int got = pn_pane_keyed(e, g, key, n);
            if (got >= 0)
                return got;
        }
    }
    return -1;
}

// The pane shown over the whole panes area, else -1: the grown one, else the focused one
// where the area is under the outermost panes' least size.
static int pn_alone(uit_t *e, int k, const int32_t r[4])
{
    const uit_node_t *N = NODE(k);
    if (N->cur >= 0 && !e->slots[N->cur].absent) {
        const uit_slot_t *S = &e->slots[N->cur];
        int p = pn_pane_keyed(e, k, S->s ? S->s : "", S->sn);
        if (p >= 0)
            return p;
    }
    if (r[2] < pn_min(e, k, 0) || r[3] < pn_min(e, k, 1)) {
        int p = e->pane_cur >= 0 ? pn_pane_in(e, k, e->pane_cur) : -1;
        return p >= 0 ? p : pn_pane_in(e, k, -1);
    }
    return -1;
}

static void pn_rec(uit_pa_t *a, int k, const int32_t r[4], const uit_arr_t *A)
{
    a->node = (int16_t)k;
    for (int i = 0; i < 4; i++)
        a->r[i] = (int16_t)r[i];
    a->depth = A->depth;
    memcpy(a->path, A->path, PN_DEPTH);
}

// The split's id as the toolkit keeps its dividers: "split:" and its children's keys.
static size_t pn_sid(uit_t *e, const uit_ps_t *P, char *out, size_t cap)
{
    size_t n = (size_t)snprintf(out, cap, "split:");
    for (int j = 0; j < P->nk && n < cap; j++)
        n += (size_t)snprintf(out + n, cap - n, "%s%s", j ? "/" : "", uit_str(e, NODE(P->kids[j])->key));
    return n < cap ? n : cap - 1;
}

// Held size `k`'s state written (`v` -1: put back, absent), traced `why` and queued.
static void pn_write(uit_t *e, int k, int32_t v, const char *why, int i, const char *sid)
{
    int sl = NODE(k)->current;
    uit_slot_t *S = &e->slots[sl];
    int32_t was = S->absent ? -1 : S->i;
    if (v < 0)
        uit_set_absent(e, sl);
    else
        uit_set_int(e, sl, v);
    if (e->flags & UIT_F_TRACE) {
        char a[16], b[16];
        snprintf(a, sizeof a, was < 0 ? "None" : "%d", (int)was);
        snprintf(b, sizeof b, v < 0 ? "None" : "%d", (int)v);
        uit_buf_t t = {0};
        uit_buf_printf(&t, "%s -> %s   (divider %d of ", a, b, i);
        uit_buf_repr(&t, sid, strlen(sid));
        uit_buf_printf(&t, " %s)", why);
        uit_trace(e, "state", uit_str(e, S->name), "%s", t.p ? t.p : "");
        uit_buf_free(&t);
    }
    uit_event(e, UIT_EV_WRITE, sl, -1, -1, NULL, 0);
    e->arr->writes++;
}

static int pn_holds(uit_t *e, int k)
{
    return NODE(k)->current >= 0 && !(NODE(k)->lit & PN_GROW);
}

// The pane of a split's child the keys reach, its index, else -1.
static int pn_keys_child(uit_t *e, const uit_ps_t *P)
{
    if (e->pane_cur < 0 || e->arr->root < 0 || pn_pane_in(e, e->arr->root, e->pane_cur) < 0)
        return -1;
    for (int j = 0; j < P->nk; j++)
        if (P->kids[j] == e->pane_cur)
            return j;
    return -1;
}

// Split `si`'s dividers drawn on `c`: a press on one held, its drag's let-go writing the held
// sizes beside it, a double tap putting them back. `out` (UIT_SP_*) where one is held.
static int pn_split(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int si, int32_t *out)
{
    uit_arr_t *A = e->arr;
    uit_ps_t *P = &A->splits[si];
    int32_t u = e->du, n = P->nk;
    int32_t opt[MOY_UI_SPO_N] = {0}, got[PN_KIDS], w[4 * PN_KIDS + 4];
    opt[MOY_UI_SPO_FLAGS] = (P->axis ? MOY_UI_SPLIT_Y : 0) | MOY_UI_SPLIT_DRAG | MOY_UI_SPLIT_FOCUS;
    opt[MOY_UI_SPO_GAP] = P->gap;
    opt[MOY_UI_SPO_CUR] = pn_keys_child(e, P);
    opt[MOY_UI_SPO_SLOP] = A->mouse ? 0 : 8 * u;
    if (!A->mouse)
        opt[MOY_UI_SPO_HIT_W] = 16 * u, opt[MOY_UI_SPO_HIT_L] = 48 * u;
    opt[MOY_UI_SPO_HANDLE_W] = 6 * u, opt[MOY_UI_SPO_HANDLE_L] = 36 * u;
    opt[MOY_UI_SPO_LINE] = 2 * u;
    memcpy(got, P->sizes, (size_t)n * sizeof *got);
    char sid[96];
    size_t sn = pn_sid(e, P, sid, sizeof sid);
    moy_ui_api_at(c, P->r[0], P->r[1], P->r[2], P->r[3]);
    moy_ui_api_split(c, s, span_of(sid, sn), got, n, P->mins, opt, w);
    int32_t i = w[4 * n], at = w[4 * n + 1], stop = w[4 * n + 2], bits = w[4 * n + 3];
    if (i < 0 || i >= n - 1)
        return 0;
    if (bits & MOY_UI_SPD_LET_GO) {
        if (bits & MOY_UI_SPD_LIVE) {
            for (int j = i; j <= i + 1; j++) {
                int k = P->kids[j];
                if (pn_holds(e, k) && (pn_held(e, k) != got[j] || e->slots[NODE(k)->current].absent))
                    pn_write(e, k, got[j], "let go", i, sid);
            }
        } else {
            uint32_t key = 2166136261u;
            for (size_t b = 0; b < sn; b++)
                key = (key ^ (uint8_t)sid[b]) * 16777619u;
            key = key * 31u + (uint32_t)i + 1u;
            if (A->tap_key != key || (uint32_t)(A->clock - A->tap_at) >= PN_DOUBLE_MS) {
                A->tap_key = key, A->tap_at = A->clock;
                return 0;
            }
            A->tap_key = 0;
            for (int j = i; j <= i + 1; j++) {
                int k = P->kids[j];
                if (pn_holds(e, k) && !e->slots[NODE(k)->current].absent)
                    pn_write(e, k, -1, "tapped twice", i, sid);
            }
        }
        return 1;
    }
    out[UIT_SP_SPLIT] = si, out[UIT_SP_DIV] = i, out[UIT_SP_AT] = at, out[UIT_SP_STOP] = stop;
    out[UIT_SP_LIVE] = (bits & MOY_UI_SPD_LIVE) != 0;
    if ((bits & MOY_UI_SPD_LIVE) && stop >= 0) {
        int p = P->kids[i + stop];
        if (NODE(p)->kind == UIT_PANES)
            p = pn_pane_in(e, p, -1);
        out[UIT_SP_FLASH] = p;
        out[UIT_SP_MIN] = p >= 0 ? pn_min(e, p, P->axis) : 0;
    }
    return 0;
}

static void pn_lay(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                   const int32_t r[4])
{
    uit_arr_t *A = e->arr;
    const uit_node_t *N = NODE(k);
    int16_t kids[PN_KIDS];
    int n = 0;
    KIDS(k, g) if (n < PN_KIDS && pn_shown(e, g)) kids[n++] = (int16_t)g;
    if (n == 1) {
        pn_draw(e, c, s, kids[0], model, row, r);
        return;
    }
    if (!n)
        return;
    int axis = (N->lit & PN_COL) != 0;
    int32_t gap = (int32_t)N->nbtn * e->du, span = r[2 + axis] - gap * (n - 1);
    uit_ps_t P;
    memset(&P, 0, sizeof P);
    P.node = (int16_t)k, P.nk = (uint8_t)n, P.axis = (uint8_t)axis, P.gap = (int16_t)gap;
    for (int i = 0; i < 4; i++)
        P.r[i] = (int16_t)r[i];
    P.depth = A->depth;
    memcpy(P.path, A->path, PN_DEPTH);
    for (int j = 0; j < n; j++) {
        P.kids[j] = kids[j];
        P.held[j] = pn_held(e, kids[j]);
        P.mins[j] = pn_min(e, kids[j], axis);
    }
    pn_fit(P.held, P.mins, n, span, P.sizes);
    int si = -1;
    if (!A->ghost && A->nsplits < PN_SPLITS) {
        si = A->nsplits++;
        A->splits[si] = P;
    }
    if (!e->placing && si >= 0 && pn_split(e, c, s, si, A->held))
        A->owe = 1;          // the page is drawn again with the sizes written
    int record = A->arranging && e->placing;
    int32_t x = r[0], y = r[1], w = r[2], h = r[3];
    uint8_t base = A->depth;
    for (int j = 0; j < n; j++) {
        int32_t sz = j < n - 1 ? P.sizes[j] : (axis ? h : w);
        int32_t cell[4] = {x, y, axis ? w : sz, axis ? sz : h}, div[4] = {0, 0, 0, 0};
        if (j < n - 1) {
            if (!axis) {
                div[0] = x + sz, div[1] = y, div[2] = gap, div[3] = h;
                x += sz + gap, w -= sz + gap;
            } else {
                div[0] = x, div[1] = y + sz, div[2] = w, div[3] = gap;
                y += sz + gap, h -= sz + gap;
            }
        }
        if (base < PN_DEPTH)
            A->path[base] = (uint8_t)j;
        A->depth = (uint8_t)(base + 1);
        pn_draw(e, c, s, kids[j], model, row, cell);
        A->depth = base;
        if (record && j < n - 1 && A->ndivs < PN_MAX) {
            uit_pd_t *D = &A->divs[A->ndivs++];
            for (int i = 0; i < 4; i++)
                D->r[i] = (int16_t)div[i];
            D->depth = base, D->i = (uint8_t)j, D->axis = (uint8_t)axis;
            memcpy(D->path, A->path, PN_DEPTH);
        }
    }
}

static void pane_head(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                      const int32_t r[4], int32_t hh)
{
    const uit_node_t *N = NODE(k);
    int32_t x = r[0], y = r[1], w = r[2];
    fill_token(s, x, y, w, hh - 1, MOY_UI_T_PANEL);
    fill_token(s, x, y + hh - 1, w, 1, MOY_UI_T_BORDER);
    int32_t tw = 0;
    KIDS(k, t) {
        if (NODE(t)->kind != UIT_TOOLS || !placed_shown(e, t, model, row))
            continue;
        int nk = 0;
        KIDS(t, g) {
            if (!placed_shown(e, g, model, row))
                continue;
            int32_t v = natural(e, g, model, row, hh - 4);
            tw += (v > 0 ? v : 0) + 4, nk++;
        }
        int32_t cell[4] = {x + w - tw - 4, y + 2, tw, hh - 4};
        put_rect(e, t, cell);
        row_cells(e, c, s, t, model, row, cell);
        break;
    }
    if (N->slot >= 0) {
        // its tabs, every value of its enum (`_r_tabs`)
        moy_ui_api_at(c, x + 6, y + 2, w - tw - 16, hh - 4);
        int count, at;
        uint32_t len, items = seg_items(e, N, &len, &count, &at);
        const char *name = uit_str(e, e->slots[N->slot].name);
        int got = moy_ui_api_choice(c, s, 9, span_of(name, strlen(name)), (const uint8_t *)e->scratch.p + items,
                                    len, at, NULL);
        id_put(e, k, model, row, name);
        if (got >= 0 && got < count && got != at) {
            const char *v = seg_key(e, N, got);
            const uit_slot_t *S = &e->slots[N->slot];
            uit_buf_t b = {0};
            uit_buf_repr(&b, S->s ? S->s : "", S->sn);
            uit_buf_printf(&b, " -> ");
            uit_buf_repr(&b, v, strlen(v));
            uit_buf_printf(&b, "   (from tabs ");
            uit_buf_repr(&b, name, strlen(name));
            uit_buf_printf(&b, ")");
            uit_set_text(e, N->slot, v, strlen(v));
            if (e->flags & UIT_F_TRACE)
                uit_trace(e, "state", name, "%s", b.p);
            uit_buf_free(&b);
            uit_event(e, UIT_EV_WRITE, N->slot, -1, -1, NULL, 0);
        }
        return;
    }
    moy_ui_api_at(c, x + 10, y + 2, w - tw - 20, hh - 4);
    uint32_t n, at;
    if (N->t_icon >= 0) {
        at = uit_tmpl_render(e, N->t_icon, model, row, &n);
        moy_ui_api_icon(c, sspan(e, at, n));
    }
    at = text(e, k, 0, model, row, &n);
    moy_ui_api_label(c, s, sspan(e, at, n), NULL);
}

static void pane_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                    const int32_t r[4])
{
    uit_arr_t *A = e->arr;
    if (A->arranging && e->placing && A->npanes < PN_MAX)
        pn_rec(&A->panes[A->npanes++], k, r, A);
    if (A->ghost)
        return;
    int32_t hh = (NODE(k)->lit & PN_HEADLESS) ? 0 : PN_HEADER * e->du;
    if (A->norder < PN_MAX)
        pn_rec(&A->order[A->norder++], k, r, A);
    if (!e->placing && hh)
        pane_head(e, c, s, k, model, row, r, hh);
    int32_t body[4] = {r[0], r[1] + hh, r[2], r[3] - hh > 0 ? r[3] - hh : 0};
    stack(e, c, s, k, model, row, body);
}

// A hosted body: placed, its rect the host's; in the pass, one stop over it that draws nothing.
static void legacy_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                      const int32_t r[4])
{
    uit_arr_t *A = e->arr;
    if (e->placing) {
        if (A->nplaced < PN_MAX)
            pn_rec(&A->placed[A->nplaced++], k, r, A);
        return;
    }
    const char *key = uit_str(e, NODE(k)->key);
    moy_ui_over_t o;
    int32_t one = 1, got[5];
    moy_ui_over_none(&o);
    moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(key, strlen(key)));
    moy_ui_over_put(&o, MOY_UI_OV_BARE, &one);
    moy_ui_api_at(c, r[0], r[1], r[2], r[3]);
    moy_ui_api_part(c, s, span_of(key, strlen(key)), 0, 0, &o, got);
    id_put(e, k, model, row, key);
}

static void panes_at(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                     const int32_t r[4])
{
    uit_arr_t *A = e->arr;
    int p = NODE(k)->parent;
    if (p == (int)UIT_NONE || (NODE(p)->kind != UIT_PANE && NODE(p)->kind != UIT_PANES)) {
        A->root = (int16_t)k;
        A->depth = 0;
        if (A->arranging && e->placing) {
            A->npanes = A->ndivs = 0;
            A->least[0] = (int16_t)pn_min(e, k, 0), A->least[1] = (int16_t)pn_min(e, k, 1);
        }
        int alone = pn_alone(e, k, r);
        if (A->arranging && e->placing)
            A->alone = (int16_t)alone;
        if (alone >= 0) {
            if (e->placing) {
                // the arrangement under the pane alone, as a host's placement answers it
                A->ghost = 1;
                pn_lay(e, c, s, k, model, row, r);
                A->ghost = 0;
            }
            A->arranging = 0;
            pn_draw(e, c, s, alone, model, row, r);
            A->arranging = 1;
            return;
        }
    }
    pn_lay(e, c, s, k, model, row, r);
}

static void pn_draw(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k, int model, int row,
                    const int32_t r[4])
{
    if (!e->arr)
        return;
    switch (NODE(k)->kind) {
    case UIT_PANES:
        panes_at(e, c, s, k, model, row, r);
        return;
    case UIT_PANE:
        pane_at(e, c, s, k, model, row, r);
        return;
    case UIT_LEGACY:
        legacy_at(e, c, s, k, model, row, r);
        return;
    }
    place_at(e, c, s, k, model, row, r);
}

// A pass begins: its splits and the panes it draws let go (a placement's bodies too).
static int pn_pass(uit_t *e)
{
    if (!e->has_panes)
        return 1;
    if (!e->arr) {
        e->arr = UIT_MALLOC(sizeof *e->arr);
        if (!e->arr)
            return 0;
        memset(e->arr, 0, sizeof *e->arr);
        e->arr->alone = -1;
    }
    uit_arr_t *A = e->arr;
    A->nsplits = A->norder = 0;
    A->root = -1;
    A->depth = A->ghost = 0;
    A->arranging = 1;
    if (e->placing)
        A->nplaced = 0;
    for (int i = 0; i < UIT_SP_N; i++)
        A->held[i] = -1;
    return 1;
}

void uit_panes_set(uit_t *e, int du, const char *pane_cur, int mouse, uint32_t clock)
{
    e->du = (int16_t)(du > 0 ? du : 1);
    e->pane_cur = -1;
    for (int k = 0; pane_cur && pane_cur[0] && k < e->nnodes; k++)
        if (NODE(k)->kind == UIT_PANE && !strcmp(uit_str(e, NODE(k)->key), pane_cur))
            e->pane_cur = (int16_t)k;
    if (e->arr)
        e->arr->mouse = (uint8_t)(mouse != 0), e->arr->clock = clock;
}

void uit_set_unit(uit_t *e, int unit)
{
    e->unit = (int16_t)(unit > 0 ? unit : 0);
}

static int pn_put(int32_t *out, int at, int max, const uit_pa_t *a)
{
    if (at + 10 > max)
        return at;
    out[at] = a->node;
    for (int i = 0; i < 4; i++)
        out[at + 1 + i] = a->r[i];
    out[at + 5] = a->depth;
    for (int i = 0; i < PN_DEPTH; i++)
        out[at + 6 + i] = a->path[i];
    return at + 10;
}

int uit_panes(const uit_t *e, int32_t *out, int max)
{
    const uit_arr_t *A = e->arr;
    if (!A || max < UIT_PN_HEAD)
        return 0;
    out[0] = A->root, out[1] = A->alone, out[2] = A->least[0], out[3] = A->least[1];
    out[4] = A->npanes, out[5] = A->ndivs, out[6] = A->nplaced, out[7] = A->norder, out[8] = A->nsplits;
    int at = UIT_PN_HEAD;
    for (int i = 0; i < A->npanes; i++)
        at = pn_put(out, at, max, &A->panes[i]);
    for (int i = 0; i < A->ndivs; i++) {
        const uit_pd_t *D = &A->divs[i];
        if (at + 11 > max)
            break;
        for (int j = 0; j < 4; j++)
            out[at + j] = D->r[j];
        out[at + 4] = D->depth;
        for (int j = 0; j < PN_DEPTH; j++)
            out[at + 5 + j] = D->path[j];
        out[at + 9] = D->i, out[at + 10] = D->axis;
        at += 11;
    }
    for (int i = 0; i < A->nplaced; i++)
        at = pn_put(out, at, max, &A->placed[i]);
    for (int i = 0; i < A->norder; i++)
        at = pn_put(out, at, max, &A->order[i]);
    for (int i = 0; i < A->nsplits; i++) {
        const uit_ps_t *P = &A->splits[i];
        if (at + 12 + 4 * P->nk > max)
            break;
        out[at] = P->node, out[at + 1] = P->nk, out[at + 2] = P->axis, out[at + 3] = P->gap;
        for (int j = 0; j < 4; j++)
            out[at + 4 + j] = P->r[j];
        out[at + 8] = P->depth;
        for (int j = 0; j < PN_DEPTH; j++)
            out[at + 9 + j] = P->path[j];
        at += 9 + PN_DEPTH;
        for (int j = 0; j < P->nk; j++) {
            out[at] = P->kids[j], out[at + 1] = P->sizes[j], out[at + 2] = P->mins[j], out[at + 3] = P->held[j];
            at += 4;
        }
    }
    return at;
}

int uit_pane_ask(uit_t *e, int node, int what, int arg)
{
    if (node < 0 || node >= e->nnodes || (NODE(node)->kind != UIT_PANE && NODE(node)->kind != UIT_PANES))
        return UIT_E_ARG;
    switch (what) {
    case UIT_PQ_SHOWN:
        return pn_shown(e, node);
    case UIT_PQ_MIN:
        return pn_min(e, node, arg != 0);
    case UIT_PQ_HELD:
        return pn_held(e, node);
    case UIT_PQ_OFFERS: {
        const uit_node_t *N = NODE(node);
        if (N->kind != UIT_PANE || N->slot < 0 || arg < 0 || arg >= e->slots[N->slot].nvals)
            return UIT_E_ARG;
        const char *v = uit_str(e, e->slots[N->slot].vals);
        for (int i = 0; i < arg; i++)
            v += strlen(v) + 1;
        return pn_offers(e, node, v);
    }
    }
    return UIT_E_ARG;
}

int uit_splits(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int flags, int32_t out[UIT_SP_N])
{
    for (int i = 0; i < UIT_SP_N; i++)
        out[i] = -1;
    out[UIT_SP_WRITES] = out[UIT_SP_OWE] = 0;
    uit_arr_t *A = e->arr;
    if (!A)
        return 0;
    e->flags = (e->flags & ~UIT_F_TRACE) | (flags & UIT_F_TRACE);
    A->writes = 0;
    for (int i = 0; i < A->nsplits; i++)
        if (pn_split(e, c, s, i, out))
            out[UIT_SP_OWE] = 1;
    out[UIT_SP_WRITES] = A->writes;
    return e->nevs - e->evat;
}

int uit_place(uit_t *e, int x, int y, int w, int h, int flags, int fs)
{
    if (!e->nnodes)
        return 0;
    if (!rects_ready(e))
        return UIT_E_NOMEM;
    e->flags = flags & ~(UIT_F_UP | UIT_F_DOWN);
    e->fs = fs;
    e->placing = 1;
    int32_t r[4] = {x, y, w, h};
    if (!pn_pass(e)) {
        e->placing = 0;
        return UIT_E_NOMEM;
    }
    stack(e, NULL, NULL, 0, -1, -1, r);
    e->placing = 0;
    return 0;
}

// A placed page's band hints: a dialog's, else the focused stop's; none while nothing or a text
// editor holds focus (a text surface keeps its keys).
// The innermost shown row or group under `k` whose close= says what Back does there, else -1
// (`_closer`: a switch its one case, a list's rows none).
static int closer_of(uit_t *e, int k)
{
    const uit_node_t *N = NODE(k);
    if (!uit_shown(e, k, -1, -1))
        return -1;
    if (N->kind == UIT_SWITCH) {
        int cs = uit_case_of(e, k);
        return cs < 0 ? -1 : closer_of(e, cs);
    }
    KIDS(k, g) {
        if (UIT_IS_LIST(NODE(g)->kind))
            continue;
        int got = closer_of(e, g);
        if (got >= 0)
            return got;
    }
    return (N->kind == UIT_ROW || N->kind == UIT_GROUP) && N->on >= 0 ? k : -1;
}

int uit_back_closer(uit_t *e, char *buf, size_t cap)
{
    int g = e->nnodes && uit_dialog(e) < 0 ? closer_of(e, 0) : -1;
    if (g < 0)
        return -2;
    if (buf && cap)
        snprintf(buf, cap, "%s", uit_str(e, NODE(g)->key));
    return uit_act_can(e, NODE(g)->on, -1, -1) ? NODE(g)->on : -1;
}

// An action's hint into the band where it renders any.
static void hint_named(uit_t *e, uit_buf_t *b, const char *verb, int a)
{
    uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
    if (n) {
        hint_add(b, verb, strlen(verb));
        hint_add(b, e->scratch.p + at, n);
    }
}

// Whether popover `p` holds words alone (See why): nothing in it takes the keys.
static int pop_note(uit_t *e, int p)
{
    KIDS(p, g) {
        int kind = NODE(g)->kind;
        if (kind == UIT_FIELD || kind == UIT_ROWS || kind == UIT_LINK || kind == UIT_BUTTON || kind == UIT_MENU)
            return 0;
    }
    return 1;
}

int uit_take_focused(uit_t *e)
{
    const uit_id_t *F = &e->focused;
    if (!e->has_focused || F->gen != e->gen || F->kind != UIT_LINK || popover_of(e) < 0)
        return 0;
    int m = F->row >= 0 ? uit_model_of(e, F->node) : -1;
    if (F->act < 0 || !uit_act_can(e, F->act, m, F->row))
        return 1;
    fire(e, F->act, F->node, m, F->row, NULL);
    return 1;
}

void uit_anchor(uit_t *e, const char *name, int x, int y, int w, int h)
{
    if (e->nanc >= 4)
        return;
    snprintf(e->anc_name[e->nanc], UIT_ID_MAX + 1, "%s", name);
    int16_t *a = e->anc[e->nanc++];
    a[0] = (int16_t)x, a[1] = (int16_t)y, a[2] = (int16_t)w, a[3] = (int16_t)h;
}

// The rect a popover hangs from (`_part_anchor`): a part the front end placed this frame
// ("code.caret"), or a text editor's or a slot's whole rect; 0 none.
static int anchor_rect(uit_t *e, const char *name, size_t n, int32_t out[4])
{
    for (int i = 0; i < e->nanc; i++)
        if (strlen(e->anc_name[i]) == n && !memcmp(e->anc_name[i], name, n)) {
            for (int j = 0; j < 4; j++)
                out[j] = e->anc[i][j];
            return 1;
        }
    if (memchr(name, '.', n) || !e->rects)
        return 0;
    for (int k = 0; k < e->nnodes; k++) {
        const uit_node_t *N = NODE(k);
        const char *key = uit_str(e, N->key);
        if ((N->kind == UIT_TEXT_EDITOR || N->kind == UIT_SLOT) && strlen(key) == n && !memcmp(key, name, n)) {
            const int16_t *r = e->rects + 4 * k;
            if (r[2] < 0)
                return 0;
            for (int j = 0; j < 4; j++)
                out[j] = r[j];
            return 1;
        }
    }
    return 0;
}

// Whether rows node `k` is a popover's list of one link a row (`_menu_rows`).
static int is_menu_rows(uit_t *e, int k)
{
    const uit_node_t *L = NODE(k);
    return L->kind == UIT_ROWS && L->parent != UIT_NONE && NODE(L->parent)->kind == UIT_POPOVER &&
           L->kid != UIT_NONE && NODE(L->kid)->next == UIT_NONE && NODE(L->kid)->kind == UIT_LINK;
}

// A menu row's text and id: the link's label, its value after a tab, cut to what the toolkit keeps.
static void menu_row_id(uit_t *e, int link, int m, int r, char id[UIT_ID_MAX + 1])
{
    uint32_t mark = e->scratch.n, ln, la = text(e, link, 0, m, r, &ln), vn = 0, va = 0;
    if (NODE(link)->t_value >= 0)
        va = text(e, link, 1, m, r, &vn);
    if (vn)
        snprintf(id, UIT_ID_MAX + 1, "%.*s\t%.*s", (int)ln, e->scratch.p + la, (int)vn, e->scratch.p + va);
    else
        snprintf(id, UIT_ID_MAX + 1, "%.*s", (int)ln, e->scratch.p + la);
    e->scratch.n = mark;
}

// A popover's list of one link a row is a menu (`_menu_rows`): a choice row each, its value at
// the row's right, its id its text; the row taken runs the link's action on it.
static void menu_rows(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k)
{
    const uit_node_t *L = NODE(k);
    int link = L->kid, m = L->slot, nr = e->slots[m].nrows;
    if (!nr) {
        uint32_t n, at = L->t_aux >= 0 ? uit_tmpl_render(e, L->t_aux, -1, -1, &n) : 0;
        if (L->t_aux >= 0 && n) {
            moy_ui_over_t o;
            moy_ui_over_none(&o);
            moy_ui_api_text(c, 0, 0, 1, -1);      // tone "dim"
            moy_ui_api_label(c, s, sspan(e, at, n), &o);
        }
        return;
    }
    uit_buf_t labels = {0};
    for (int r = 0; r < nr; r++) {
        if (r)
            uit_buf_printf(&labels, "%c", 0);
        char id[UIT_ID_MAX + 1];
        menu_row_id(e, link, m, r, id);
        uit_buf_printf(&labels, "%s", id);
        id_put(e, link, m, r, id);
    }
    int got = moy_ui_api_menu(c, s, span_of("", 0), (const uint8_t *)labels.p, labels.n,
                              (const uint8_t *)"", 0, NULL);
    if (got >= 0 && got < nr && uit_act_can(e, NODE(link)->act, m, got))
        fire(e, NODE(link)->act, link, m, got, NULL);
    uit_buf_free(&labels);
}

// A popover on a placed page (`uitree_editor.Engine._r_popover`): hung from its anchor= (a
// whole text editor, or a part of one), `w=` wide; one that opens on a field hung from a whole
// node stands centred over that node's top. Its field, its words as they flow, its menu rows,
// anything else as it draws; a note (words alone) names its close in its own band.
static void placed_popover(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int k)
{
    const uit_node_t *N = NODE(k);
    const char *key = uit_str(e, N->key);
    int32_t w = int_of(e, N->t_aux, -1, -1, 200), a[4];
    if (N->t_value >= 0) {
        uint32_t n, mark = e->scratch.n, at = uit_tmpl_render(e, N->t_value, -1, -1, &n);
        const char *name = e->scratch.p + at;
        if (n && anchor_rect(e, name, n, a)) {
            if (!memchr(name, '.', n) && N->kid != UIT_NONE && NODE(N->kid)->kind == UIT_FIELD) {
                int32_t most = a[2] - 16 * e->fs;
                w = w < most ? w : most;
                a[0] += fdiv(a[2] - w, 2), a[1] += 4 * e->fs, a[2] = w, a[3] = 0;
            }
            moy_ui_api_at(c, a[0], a[1], a[2], a[3]);
        }
        e->scratch.n = mark;
    }
    if (!moy_ui_api_popover(c, s, span_of(key, strlen(key)), w, NULL) && N->on >= 0)
        fire(e, N->on, k, -1, -1, NULL);
    KIDS(k, g) {
        if (!uit_shown(e, g, -1, -1))
            continue;
        if (is_menu_rows(e, g))
            menu_rows(e, c, s, g);
        else
            draw(e, c, s, g, -1, -1);
    }
    if (pop_note(e, k) && N->on >= 0) {
        uint32_t n, at = uit_tmpl_render(e, e->acts[N->on].hint, -1, -1, &n);
        if (n) {
            uint32_t hb = e->scratch.n;
            uit_buf_put(&e->scratch, "cancel", 6);
            uit_buf_put(&e->scratch, e->scratch.p + at, n);
            moy_ui_api_hints(c, s, (const uint8_t *)e->scratch.p + hb, e->scratch.n - hb - 1, NULL);
        }
    }
    moy_ui_api_popover(c, s, span_of("", 0), 0, NULL);
}

// A placed page's band hints (`uitree_editor.Engine._hints`): a dialog's; none over a note; under
// a popover or a closer row, the arrows' walk of its list, what Enter does on the focused stop
// (and Shift+Enter in a field) and what Back closes; else none while nothing or a text editor
// holds focus (a text surface keeps its keys), the focused stop's otherwise.
static void placed_hints(uit_t *e, uit_buf_t *b, int pages)
{
    b->n = 0;
    if (uit_dialog(e) >= 0) {
        hints(e, b, pages);
        return;
    }
    int pop = popover_of(e);
    if (pop >= 0 && pop_note(e, pop))
        return;
    int closer = pop >= 0 ? pop : closer_of(e, 0);
    if (closer < 0) {
        if (e->has_focused && e->focused.kind != UIT_TEXT_EDITOR)
            hints(e, b, pages);
        return;
    }
    if (pop >= 0)
        KIDS(pop, g)
        if (NODE(g)->kind == UIT_ROWS && NODE(g)->cur >= 0) {
            static const char PICK[] = "scroll\0Pick";
            uit_buf_put(b, PICK, sizeof PICK - 1);
            break;
        }
    if (e->has_focused && e->focused.gen == e->gen) {
        const uit_id_t *F = &e->focused;
        int a = F->kind == UIT_FIELD ? NODE(F->node)->on : F->act;
        if (a >= 0 && uit_act_can(e, a, -1, F->row))
            hint_named(e, b, "confirm", a);
        if (F->kind == UIT_FIELD)
            for (int s = 0; s < e->nacts; s++)
                if (e->acts[s].flags & UIT_A_SHIFT_ENTER) {
                    if (uit_act_can(e, s, -1, -1))
                        hint_named(e, b, "Shift+enter", s);
                    break;
                }
    }
    if (NODE(closer)->on >= 0)
        hint_named(e, b, "back", NODE(closer)->on);
}

// A placed page's band: the page's last row, cut from its room, where it names anything.
static void placed_band(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, uit_buf_t *hb, int pages)
{
    placed_hints(e, hb, pages);
    if (!hb->n)
        return;
    static const char BAR[] = "change\0Move\0confirm\0Open\0back\0Back";
    const uint8_t *hp = (e->flags & UIT_F_ON_BAR) ? (const uint8_t *)BAR : (const uint8_t *)hb->p;
    size_t hn = (e->flags & UIT_F_ON_BAR) ? sizeof BAR - 1 : hb->n - 1;
    if (moy_ui_api_hints_w(c, s, hp, hn) <= 0)
        return;
    int32_t band[4], rest[4];
    moy_ui_cut(e->rect, MOY_UI_CUT_BOTTOM, h_row(e), band, rest);
    moy_ui_api_at(c, band[0], band[1], band[2], band[3]);
    moy_ui_api_hints(c, s, hp, hn, NULL);
    memcpy(e->rect, rest, sizeof rest);
}

// A placed page's pass: its children stacked down its room, its popovers and dialogs last.
static void placed_walk(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s)
{
    if (!rects_ready(e) || !pn_pass(e))
        return;
    e->nlate = 0;
    stack(e, c, s, 0, -1, -1, e->rect);
    for (int i = 0; i < e->nlate; i++) {
        moy_ui_api_at(c, e->rect[0], e->rect[1], e->rect[2], e->rect[3]);
        draw(e, c, s, e->late[i], -1, -1);
    }
}

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
    e->nanc = 0;
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
    int ground = e->placed && e->ground[2] > 0;
    if (ground)
        moy_ui_api_at(c, e->ground[0], e->ground[1], e->ground[2], e->ground[3]);
    uint32_t n, at = text(e, 0, 0, -1, -1, &n);
    moy_ui_box_t box;
    moy_ui_api_page(c, s, sspan(e, at, n), &o, &box);
    e->pw = box.w;
    e->rect[0] = box.x, e->rect[1] = box.y, e->rect[2] = box.w, e->rect[3] = box.h;
    if (ground)
        e->rect[0] = x, e->rect[1] = y, e->rect[2] = w, e->rect[3] = h;
    uit_buf_t hb = {0};
    if (e->placed) {
        placed_band(e, c, s, &hb, pages ? pages : 1);
        goto ids;
    }
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
ids:;
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
    if (e->placed)
        placed_walk(e, c, s);
    else
        walk(e, c, s, 0, -1, -1);
    e->tv_stop = e->tv_stop_now;
    // after: what holds focus, and whether the band named another node's verbs
    int had = e->has_focused;
    uit_id_t was = e->focused;
    focus_after(e, c, moved, ahead, had_last ? &last : NULL);
    int owe = 0;
    if (e->arr && e->arr->owe)
        owe = 1, e->arr->owe = 0;       // a divider's let-go wrote held sizes
    if (had != e->has_focused || (had && (was.node != e->focused.node || was.gen != e->focused.gen))) {
        uit_buf_t hb2 = {0};
        if (e->placed)
            placed_hints(e, &hb2, pages ? pages : 1);
        else
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

// -- a row lent to a site ---------------------------------------------------------

// The source of node `k`'s prop `name`, else NULL.
static const char *prop_src(uit_t *e, int k, const char *name)
{
    for (int p = NODE(k)->props; p != (int)UIT_NONE; p = e->props[p].next)
        if (!strcmp(uit_str(e, e->props[p].name), name))
            return uit_str(e, e->props[p].src);
    return NULL;
}

// The shown `tools lend=SITE` under `k` (a hidden node hides its subtree, a switch shows its
// one case, a list holds none), else -1.
static int lender(uit_t *e, int k, const char *site)
{
    const uit_node_t *N = NODE(k);
    if (!uit_shown(e, k, -1, -1) || UIT_IS_LIST(N->kind))
        return -1;
    if (N->kind == UIT_TOOLS && (N->lit & TL_LEND)) {
        const char *s = prop_src(e, k, "lend");
        return s && !strcmp(s, site) ? k : -1;
    }
    if (N->kind == UIT_SWITCH) {
        int cs = uit_case_of(e, k);
        return cs < 0 ? -1 : lender(e, cs, site);
    }
    KIDS(k, g) {
        int r = lender(e, g, site);
        if (r >= 0)
            return r;
    }
    return -1;
}

// Whether tabs node `k` offers value `v`: no offered= for it, or its condition holds.
static int offered(uit_t *e, int k, const char *v)
{
    for (int p = NODE(k)->props; p != (int)UIT_NONE; p = e->props[p].next) {
        const uit_prop_t *P = &e->props[p];
        if (P->kind == PK_COND && P->repr != UIT_NONE && !strcmp(uit_str(e, P->name), "offered") &&
            !strcmp(uit_str(e, P->repr), v))
            return uit_cond_eval(e, P->idx, -1, -1);
    }
    return 1;
}

// A cell's four texts onto `text` at `*at`: 0, or -1 where they do not fit.
static int lent_texts(char *text, size_t cap, size_t *at, const char *const t[4], const size_t n[4])
{
    for (int i = 0; i < 4; i++) {
        if (*at + n[i] + 1 > cap)
            return -1;
        memcpy(text + *at, t[i], n[i]);
        text[*at + n[i]] = 0;
        *at += n[i] + 1;
    }
    return 0;
}

int uit_lent(uit_t *e, const char *site, int32_t *cells, int max, char *text, size_t cap)
{
    if (!e->nnodes)
        return -1;
    int k = lender(e, 0, site);
    if (k < 0)
        return -1;
    uint32_t mark = e->scratch.n;
    size_t at = 0;
    int n = 0, r = 0;
    KIDS(k, g) {
        if (n >= max)
            break;
        const uit_node_t *G = NODE(g);
        if (!placed_shown(e, g, -1, -1))
            continue;
        const char *key = uit_str(e, G->key);
        if (G->kind == UIT_TABS) {
            const uit_slot_t *S = &e->slots[G->slot];
            const char *v = uit_str(e, S->vals);
            for (int i = 0; i < S->nvals && n < max; i++, v += strlen(v) + 1) {
                size_t vn = strlen(v);
                if (!offered(e, g, v))
                    continue;
                const char *t[4] = {key, v, G->cur >= 0 ? uit_label_word(e, G->cur, v, vn) : v,
                                    G->nbtn ? uit_label_word(e, G->nbtn - 1, v, vn) : ""};
                size_t tn[4] = {strlen(t[0]), vn, strlen(t[2]), strlen(t[3])};
                int32_t *c = cells + n * UIT_LC_INTS;
                c[0] = UIT_LC_TAB, c[1] = g, c[2] = i, c[4] = G->on;
                c[3] = vn == S->sn && S->s && !memcmp(v, S->s, vn) ? UIT_LC_CUR : 0;
                if (lent_texts(text, cap, &at, t, tn) < 0)
                    goto small;
                n++;
            }
        } else if (G->kind == UIT_BUTTON || G->kind == UIT_LABEL) {
            uint32_t ln = 0, in = 0, la = text_t(e, g, G->t_main, -1, -1, &ln), ia = 0;
            if (G->kind == UIT_BUTTON && G->t_icon >= 0)
                ia = uit_tmpl_render(e, G->t_icon, -1, -1, &in);
            const char *t[4] = {key, "", e->scratch.p + la, in ? e->scratch.p + ia : ""};
            size_t tn[4] = {strlen(key), 0, ln, in};
            int32_t *c = cells + n * UIT_LC_INTS;
            c[0] = G->kind == UIT_LABEL ? UIT_LC_TEXT : UIT_LC_BUTTON, c[1] = g, c[2] = -1, c[3] = 0, c[4] = -1;
            if (G->kind == UIT_BUTTON) {
                c[4] = G->act;
                c[3] = ((G->btn & BT_ALONE) ? UIT_LC_ALONE : 0) | (G->lit == 1 ? UIT_LC_PRIMARY : 0) |
                       (G->act < 0 || uit_act_can(e, G->act, -1, -1) ? UIT_LC_ON : 0) |
                       (G->mark >= 0 && uit_cond_eval(e, G->mark, -1, -1) ? UIT_LC_MARKED : 0);
            }
            if (lent_texts(text, cap, &at, t, tn) < 0)
                goto small;
            n++;
        } else {
            r = uit_fail(e, UIT_E_KIND, UIT_MSG("a %s in a row lent to %s: tabs, buttons and a label",
                                                "%s lent to %s"), uit_kind_names[G->kind], site);
            break;
        }
    }
    e->scratch.n = mark;
    return r < 0 ? r : n;
small:
    e->scratch.n = mark;
    return uit_fail(e, UIT_E_ARG, UIT_MSG("the lent row's texts pass %u bytes", "lent %u"), (unsigned)cap);
}

// The lent row's rule (the bar's zone): its cells pack from the left in their order, the run
// of buttons at its end stands at the zone's right end, END short of it, and a hairline closes
// a run of tabs and opens that end run. GAP, LEAST, GROUP and END are at fs 1.
enum { LZ_GAP = 5, LZ_LEAST = 2, LZ_GROUP = 8, LZ_END = 6 };
#define LP(i) (cells + (i) * UIT_LP_INTS)

// Whether cell `i` shows its label at `density` (`words`: a button its word at density 1).
static int lp_named(const int32_t *c, int density, int words)
{
    if (!c[2])
        return 0;
    if (c[0] == UIT_LC_TAB)
        return density == 2 || (density == 1 && (c[1] & UIT_LC_CUR));
    return c[0] == UIT_LC_BUTTON && (density == 2 || (density == 1 && words)) && !(c[1] & UIT_LC_ALONE);
}

static int32_t lp_width(const int32_t *c, int density, int words, int32_t h, int32_t pad)
{
    if (c[0] == UIT_LC_TEXT)
        return 0;
    if (lp_named(c, density, words))
        return c[2];
    return c[0] == UIT_LC_TAB && (c[1] & UIT_LC_CUR) ? h + 2 * pad : h;
}

int uit_lent_place(int32_t *cells, int n, int32_t x0, int32_t w, int32_t h, int fs, int32_t pad,
                   int32_t *hair, int *nhair, int32_t *need)
{
    int end = n, lines = 0, tablines = 0, text = 0, density = 2, words = 1;
    w -= LZ_END * fs;
    if (w < 0)
        w = 0;
    while (end > 0 && LP(end - 1)[0] == UIT_LC_BUTTON)
        end--;
    // a line before cell i: after a run of tabs, and before the end run
#define LINE(i) ((i) > 0 && (i) < n && ((LP((i) - 1)[0] == UIT_LC_TAB && LP(i)[0] != UIT_LC_TAB) || \
                                        ((i) == end && end > 0)))
    for (int i = 0; i < n; i++) {
        text |= LP(i)[0] == UIT_LC_TEXT;
        lines += LINE(i);
        tablines += i > 0 && LP(i - 1)[0] == UIT_LC_TAB && LP(i)[0] != UIT_LC_TAB;
    }
    int32_t gaps = n - 1 - lines, lg = 0, least = 0;
    static const uint8_t TRY[4][2] = {{2, 1}, {1, 1}, {1, 0}, {0, 0}};
    for (int t = 0; t < 4; t++) {
        density = TRY[t][0], words = TRY[t][1];
        lg = density == 1 ? 0 : LZ_LEAST * fs;      // the current tab's name goes before the gaps do
        least = lg * (gaps > 0 ? gaps : 0) + LZ_GROUP * fs * lines;
        int32_t cur = -1, widest = -1;
        for (int i = 0; i < n; i++) {
            int32_t *c = LP(i);
            least += lp_width(c, density, words, h, pad);
            if (c[0] == UIT_LC_TAB && (c[1] & UIT_LC_CUR) && cur < 0)
                cur = lp_width(c, density, words, h, pad);
            if (c[0] == UIT_LC_TAB && c[2] && c[2] > widest)
                widest = c[2];
        }
        // the buttons keep their words only where every tab named in the current one's place holds them
        int32_t more = density == 1 && words && cur >= 0 && widest >= 0 && widest > cur ? widest - cur : 0;
        if (least + more <= w || density == 0)
            break;
    }
    int32_t spare = w - least > 0 ? w - least : 0, gap = lg;
    if (!text) {
        int32_t g = spare / (gaps > 1 ? gaps : 1), most = LZ_GAP * fs - lg;
        gap = lg + (g < most ? g : most);
    }
    int nh = 0;
    int32_t x = x0;
    for (int i = 0; i < n; i++) {
        int32_t *c = LP(i);
        int32_t cw = lp_width(c, density, words, h, pad);
        if (i) {
            if (LINE(i)) {
                x += LZ_GROUP * fs;
                if (i == end && !text) {
                    int32_t rest = 0;
                    for (int j = end; j < n; j++)
                        rest += lp_width(LP(j), density, words, h, pad);
                    int32_t at = x0 + w - rest - gap * (n - end - 1);
                    if (at > x)
                        x = at;
                }
                if (nh < n)
                    hair[nh++] = x - (LZ_GROUP * fs + fs) / 2;
            } else {
                x += gap;
            }
        }
        if (c[0] == UIT_LC_TEXT) {
            cw = spare;                 // a line of text takes the room left
            spare = 0;
        }
        if (x + cw > x0 + w) {
            for (int j = i; j < n; j++)
                LP(j)[4] = -1;
            break;
        }
        c[3] = x, c[4] = cw, c[5] = c[0] == UIT_LC_TEXT || lp_named(c, density, words);
        x += cw;
    }
#undef LINE
    *nhair = nh;
    int32_t icons = LZ_LEAST * fs * (n - 1 - tablines > 0 ? n - 1 - tablines : 0) + LZ_GROUP * fs * tablines + LZ_END * fs;
    for (int i = 0; i < n; i++)
        icons += lp_width(LP(i), 0, 0, h, pad);
    *need = icons;
    return density;
}
#undef LP
