// The frame: the page walked through the toolkit's verbs, call for call as
// the reference replay makes them (runtime/uitree/uitree_app.py's
// `Engine._replay_page` and its `_r_*`), with focus, the hints band, a
// list's cursor and the queue of what the user did.
#include <stdio.h>
#include <string.h>

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
    return A->when < 0 || uit_cond_eval(e, A->when, model, row);
}

int uit_can(uit_t *e, int a, int row)
{
    if (a < 0 || a >= e->nacts)
        return 0;
    return uit_act_can(e, a, -1, row);
}

int uit_shown(uit_t *e, int node, int model, int row)
{
    const uit_node_t *N = NODE(node);
    return N->shown < 0 || uit_cond_eval(e, N->shown, model, row);
}

static int is_stop(int kind)
{
    return kind == UIT_LINK || kind == UIT_BUTTON || kind == UIT_TOGGLE;
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

static void drew(uit_t *e, int k, int prop, int model, int row, const char *v, size_t n)
{
    if (!(e->flags & UIT_F_TRACE))
        return;
    int t = prop == 0 ? NODE(k)->t_main : NODE(k)->t_value;
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
        static const char *KN[UIT_KINDS] = {"", "page", "group", "label", "info", "link", "button",
                                            "toggle", "rows", "dialog"};
        if (row >= 0)
            uit_buf_printf(&b, "'%s/%s' ", rk, uit_str(e, NODE(k)->key));
        uit_buf_repr(&b, src, strlen(src));
        uit_buf_printf(&b, " -> ");
        uit_buf_repr(&b, v, n);
        uit_trace(e, "drew", KN[NODE(k)->kind], "%s", b.p);
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

// A text prop now (0 the main one, 1 the value): an action's label where a
// link or button names none.
static uint32_t text(uit_t *e, int k, int prop, int model, int row, uint32_t *n)
{
    const uit_node_t *N = NODE(k);
    int t = prop == 0 ? N->t_main : N->t_value;
    if (t < 0) {
        const char *s = prop == 0 && N->act >= 0 ? uit_str(e, e->acts[N->act].label) : "";
        *n = (uint32_t)strlen(s);
        return (uint32_t)uit_buf_put(&e->scratch, s, *n);
    }
    uint32_t at = uit_tmpl_render(e, t, model, row, n);
    drew(e, k, prop, model, row, e->scratch.p + at, *n);
    return at;
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
    static const char *KN[UIT_KINDS] = {"", "page", "group", "label", "info", "link", "button",
                                        "toggle", "rows", "dialog"};
    uit_buf_printf(&b, "%s ", how ? how : KN[NODE(k)->kind]);
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
            uit_buf_printf(&s, "rows ");
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

// -- the stops, in the page's order -------------------------------------------------

typedef struct {
    int16_t node, row;
} stop_t;

typedef struct {
    stop_t *s;
    int n, cap;
} stops_t;

static void stops_of(uit_t *e, int k, int model, int row, stops_t *out)
{
    KIDS(k, c) {
        const uit_node_t *N = NODE(c);
        if (!uit_shown(e, c, model, row))
            continue;
        if (N->kind == UIT_ROWS) {
            for (int r = 0; r < e->slots[N->slot].nrows; r++)
                stops_of(e, c, N->slot, r, out);
        } else if (N->kind == UIT_DIALOG) {
            continue;
        } else if (is_stop(N->kind)) {
            if (N->act < 0 || uit_act_can(e, N->act, model, row)) {
                if (uit_grow((void **)&out->s, &out->cap, out->n + 1, sizeof *out->s))
                    return;
                out->s[out->n].node = (int16_t)c;
                out->s[out->n].row = (int16_t)row;
                out->n++;
            }
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
        if (N->kind == UIT_ROWS) {
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
        if (!uit_shown(e, c, -1, -1) || N->kind == UIT_DIALOG || N->kind == UIT_ROWS)
            continue;
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
    if (e->has_focused) {
        const uit_id_t *F = &e->focused;
        int a = F->act;
        int on = a >= 0 && uit_act_can(e, a, -1, -1);
        if (F->kind == UIT_TOGGLE) {
            int t = F->gen == e->gen && F->node >= 0 ? NODE(F->node)->t_aux : -1;
            hint_add(b, "confirm", 7);
            if (t >= 0) {
                uint32_t n, at = uit_tmpl_render(e, t, -1, -1, &n);
                hint_add(b, e->scratch.p + at, n);
            } else {
                hint_add(b, "Change", 6);
            }
        } else if (on) {
            uint32_t n, at = uit_tmpl_render(e, e->acts[a].hint, -1, -1, &n);
            if (n) {
                hint_add(b, "confirm", 7);
                hint_add(b, e->scratch.p + at, n);
            }
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
        if (N->t_icon >= 0) {
            uint32_t n, at = uit_tmpl_render(e, N->t_icon, model, row, &n);
            if (n)
                moy_ui_api_icon(c, sspan(e, at, n));
        }
        a1 = text(e, k, 0, model, row, &n1);
        if (N->lit) {
            moy_ui_over_none(&o);
            moy_ui_api_text(c, 0, 0, N->lit, -1);
            moy_ui_api_label(c, s, sspan(e, a1, n1), &o);
        } else {
            moy_ui_api_label(c, s, sspan(e, a1, n1), NULL);
        }
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
        if (moy_ui_api_button(c, s, sspan(e, a1, n1), N->lit, &o) && uit_act_can(e, N->act, model, row))
            fire(e, N->act, k, model, row, NULL);
        break;
    case UIT_TOGGLE: {
        uit_slot_t *S = &e->slots[N->slot];
        int cur = S->i != 0;
        a1 = text(e, k, 0, model, row, &n1);
        over_for(e, k, model, row, c, &o, id);
        int got = moy_ui_api_toggle(c, s, sspan(e, a1, n1), cur, &o) != 0;
        if (got != cur) {
            uit_set_int(e, N->slot, got);
            const char *key = uit_str(e, N->key);
            uit_buf_t b = {0};
            uit_buf_repr(&b, key, strlen(key));
            uit_trace(e, "state", uit_str(e, S->name), "%s -> %s   (from toggle %s)", cur ? "True" : "False",
                      got ? "True" : "False", b.p);
            uit_buf_free(&b);
            uit_event(e, UIT_EV_WRITE, N->slot, -1, -1, NULL, 0);
        }
        break;
    }
    case UIT_ROWS: {
        const uit_slot_t *M = &e->slots[N->slot];
        uit_after_removal(e, k);
        if (!M->nrows && N->t_aux >= 0) {
            uint32_t n, at = uit_tmpl_render(e, N->t_aux, -1, -1, &n);
            if (n)
                moy_ui_api_label(c, s, sspan(e, at, n), NULL);
        }
        for (int r = 0; r < e->slots[N->slot].nrows; r++)
            walk(e, c, s, k, N->slot, r);
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
    KIDS(lst, g)
    if (is_stop(NODE(g)->kind)) {
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
    int fresh_prev = e->fresh && e->nprev;
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
        if (L->kind != UIT_ROWS || L->cur < 0)
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

static void focus_after(uit_t *e, moy_ui_ctx_t *c, int moved, const char *ahead)
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
    if (!I || moved)
        return;
    int m = uit_model_of(e, I->node);
    int lst = -1;
    for (int k = I->node; k >= 0 && k != (int)UIT_NONE; k = NODE(k)->parent)
        if (NODE(k)->kind == UIT_ROWS) {
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
    // the page
    moy_ui_over_t o;
    moy_ui_over_none(&o);
    const char *pid = uit_str(e, e->page_id);
    moy_ui_over_text(&o, MOY_UI_OV_ID, span_of(pid, strlen(pid)));
    if (flags & UIT_F_BARE) {
        int32_t one = 1;
        moy_ui_over_put(&o, MOY_UI_OV_BARE, &one);
    }
    uint32_t n, at = text(e, 0, 0, -1, -1, &n);
    moy_ui_box_t box;
    moy_ui_api_page(c, s, sspan(e, at, n), &o, &box);
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
        if (flags & UIT_F_ON_BAR) {
            static const char BAR[] = "change\0Move\0confirm\0Open\0back\0Back";
            moy_ui_api_hints(c, s, (const uint8_t *)BAR, sizeof BAR - 1, NULL);
        } else {
            moy_ui_api_hints(c, s, (const uint8_t *)hb.p, hb.n - 1, NULL);
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
    walk(e, c, s, 0, -1, -1);
    // after: what holds focus, and whether the band named another node's verbs
    int had = e->has_focused;
    uit_id_t was = e->focused;
    focus_after(e, c, moved, ahead);
    int owe = 0;
    if (had != e->has_focused || (had && (was.node != e->focused.node || was.gen != e->focused.gen))) {
        uit_buf_t hb2 = {0};
        hints(e, &hb2, pages ? pages : 1);
        owe = hb2.n != hb.n || (hb.n && memcmp(hb.p, hb2.p, hb.n));
        uit_buf_free(&hb2);
    }
    uit_buf_free(&hb);
    return (e->nevs - e->evat) | (owe ? 1 << 16 : 0);
}
