// A page compiled ahead of time: the store as it stands after a build's
// lowering (its declarations and its one page) written as one blob
// (`uit_save`), and a new store filled from one (`uit_load`) with no build
// call. The format is native/moy_uitree/README.md's "A compiled page".
//
// Every record is written field by field, each field's bytes little-endian
// at its own width, so a blob is the same on a 64-bit host and a 32-bit board
// whatever their structs' padding. The fields each record carries are the
// tables below; tests/test_uitree_aot.py fails when a struct gains a member
// no table lists (or a slot member the visit owns is not named as such).
#include <stddef.h>
#include <string.h>

#include "moy_uitree_int.h"

typedef struct {
    uint16_t off;
    uint8_t w;
} uit_fld_t;

#define FLD(T, m) {(uint16_t)offsetof(T, m), (uint8_t)sizeof(((T *)0)->m)}
#define NFLD(t) ((int)(sizeof(t) / sizeof((t)[0])))

// A slot's declaration. The rest of a slot (ver, i, f, s, sn, scap, nrows,
// cells, at, cells_n, sync, sync_set, in_gen, absent) is the visit's or build_end's;
// mf is its own record (`f_fields`), after a byte saying whether it is there.
static const uit_fld_t f_slot[] = {
    FLD(uit_slot_t, name), FLD(uit_slot_t, type), FLD(uit_slot_t, nf), FLD(uit_slot_t, key),
    FLD(uit_slot_t, model), FLD(uit_slot_t, vals), FLD(uit_slot_t, nvals),
};
static const uit_fld_t f_fields[] = {FLD(uit_fields_t, ftype), FLD(uit_fields_t, fname)};
static const uit_fld_t f_act[] = {
    FLD(uit_act_t, name), FLD(uit_act_t, label), FLD(uit_act_t, hint), FLD(uit_act_t, when),
    FLD(uit_act_t, off), FLD(uit_act_t, flags), FLD(uit_act_t, letter), FLD(uit_act_t, letters),
    FLD(uit_act_t, icon), FLD(uit_act_t, row),
};
static const uit_fld_t f_labels[] = {
    FLD(uit_labels_t, name), FLD(uit_labels_t, keys), FLD(uit_labels_t, words), FLD(uit_labels_t, n),
};
static const uit_fld_t f_tmpl[] = {
    FLD(uit_tmpl_t, src), FLD(uit_tmpl_t, code), FLD(uit_tmpl_t, ncode), FLD(uit_tmpl_t, row),
    FLD(uit_tmpl_t, lit), FLD(uit_tmpl_t, type),
};
static const uit_fld_t f_op[] = {
    FLD(uit_op_t, op), FLD(uit_op_t, rk), FLD(uit_op_t, ref), FLD(uit_op_t, f), FLD(uit_op_t, c),
    FLD(uit_op_t, d),
};
static const uit_fld_t f_cond[] = {
    FLD(uit_cond_t, src), FLD(uit_cond_t, first), FLD(uit_cond_t, n), FLD(uit_cond_t, any),
};
static const uit_fld_t f_term[] = {
    FLD(uit_term_t, neg), FLD(uit_term_t, rk), FLD(uit_term_t, cmp), FLD(uit_term_t, ref),
    FLD(uit_term_t, f), FLD(uit_term_t, li), FLD(uit_term_t, lf), FLD(uit_term_t, ls),
};
static const uit_fld_t f_node[] = {
    FLD(uit_node_t, kind), FLD(uit_node_t, lit), FLD(uit_node_t, parent), FLD(uit_node_t, kid),
    FLD(uit_node_t, last), FLD(uit_node_t, next), FLD(uit_node_t, key), FLD(uit_node_t, act),
    FLD(uit_node_t, shown), FLD(uit_node_t, focus), FLD(uit_node_t, t_main), FLD(uit_node_t, t_value),
    FLD(uit_node_t, t_icon), FLD(uit_node_t, t_aux), FLD(uit_node_t, slot), FLD(uit_node_t, cur),
    FLD(uit_node_t, current), FLD(uit_node_t, changed), FLD(uit_node_t, on), FLD(uit_node_t, props),
    FLD(uit_node_t, btn), FLD(uit_node_t, nbtn), FLD(uit_node_t, mark),
};
static const uit_fld_t f_prop[] = {
    FLD(uit_prop_t, name), FLD(uit_prop_t, src), FLD(uit_prop_t, repr), FLD(uit_prop_t, kind),
    FLD(uit_prop_t, idx), FLD(uit_prop_t, next),
};

typedef struct {
    const uit_fld_t *f;
    int n;
} uit_rec_t;

static const uit_rec_t RECS[] = {
    {f_slot, NFLD(f_slot)}, {f_fields, NFLD(f_fields)}, {f_act, NFLD(f_act)},
    {f_labels, NFLD(f_labels)}, {f_tmpl, NFLD(f_tmpl)}, {f_op, NFLD(f_op)},
    {f_cond, NFLD(f_cond)}, {f_term, NFLD(f_term)}, {f_node, NFLD(f_node)}, {f_prop, NFLD(f_prop)},
};

static uint32_t fnv(uint32_t h, const void *p, size_t n)
{
    const uint8_t *b = p;
    for (size_t i = 0; i < n; i++)
        h = (h ^ b[i]) * 16777619u;
    return h;
}

static uint32_t fnv_int(uint32_t h, int32_t v)
{
    uint8_t b[4] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)};
    return fnv(h, b, 4);
}

uint32_t uit_layout_hash(void)
{
    static uint32_t h;
    if (h)
        return h;
    uint32_t x = fnv_int(2166136261u, UIT_FORMAT);
    for (size_t r = 0; r < sizeof RECS / sizeof RECS[0]; r++) {
        x = fnv_int(x, RECS[r].n);
        for (int i = 0; i < RECS[r].n; i++)
            x = fnv_int(x, RECS[r].f[i].w);
    }
    // what the records' numbers mean: the kinds by name, and the last of each numbering
    x = fnv_int(x, UIT_KINDS);
    for (int k = 1; k < UIT_KINDS; k++)
        x = fnv(x, uit_kind_names[k], strlen(uit_kind_names[k]) + 1);
    static const int32_t lasts[] = {
        UIT_MODEL, OP_MAP, RK_CURRENT, SYS_TOUCH, PK_STATE, UIT_FIELDS, UIT_NONE, NL_FIELD, GAP_0,
        TV_PROSE, TL_LEND, BT_ALONE, LT_FIT, LT_ALIGN_SHIFT, UIT_A_ROW, UIT_ID_MAX,
    };
    for (size_t i = 0; i < sizeof lasts / sizeof lasts[0]; i++)
        x = fnv_int(x, lasts[i]);
    h = x ? x : 1;
    return h;
}

// -- writing -------------------------------------------------------------------------

static int raw(uit_buf_t *b, const void *p, size_t n)
{
    if (uit_buf_put(b, p, n) < 0)
        return -1;
    b->n--;                     // uit_buf_put's NUL is not the blob's
    return 0;
}

static int u32(uit_buf_t *b, uint32_t v)
{
    uint8_t x[4] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)};
    return raw(b, x, 4);
}

static int rec(uit_buf_t *b, const void *base, const uit_fld_t *f, int n)
{
    for (int i = 0; i < n; i++)
        if (raw(b, (const char *)base + f[i].off, f[i].w))
            return -1;
    return 0;
}

static int recs(uit_buf_t *b, const void *arr, int count, size_t size, const uit_fld_t *f, int n)
{
    if (u32(b, (uint32_t)count))
        return -1;
    for (int i = 0; i < count; i++)
        if (rec(b, (const char *)arr + (size_t)i * size, f, n))
            return -1;
    return 0;
}

static int text8(uit_buf_t *b, const char *s)
{
    size_t n = strlen(s);
    if (n > 255)
        return -1;
    uint8_t l = (uint8_t)n;
    return raw(b, &l, 1) || raw(b, s, n);
}

static void put32(char *p, uint32_t v)
{
    p[0] = (char)v;
    p[1] = (char)(v >> 8);
    p[2] = (char)(v >> 16);
    p[3] = (char)(v >> 24);
}

static uint32_t get32(const char *p)
{
    const uint8_t *b = (const uint8_t *)p;
    return b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}

static int save(uit_t *e, uit_buf_t *b, const char *variant, uint32_t source, const char *front, size_t nfront)
{
    char head[UIT_BLOB_HEAD] = "MUIT";
    head[4] = (char)UIT_FORMAT;
    head[5] = (char)(UIT_FORMAT >> 8);
    head[6] = (char)UIT_BLOB_HEAD;
    head[7] = 0;
    uint8_t placed = e->placed;
    if (raw(b, head, sizeof head) || text8(b, uit_str(e, e->page_id)) || text8(b, variant) ||
        raw(b, &placed, 1))
        return -1;
    if (u32(b, e->pool.n) || raw(b, e->pool.p, e->pool.n) || u32(b, (uint32_t)e->nslots))
        return -1;
    for (int i = 0; i < e->nslots; i++) {
        const uit_slot_t *S = &e->slots[i];
        uint8_t has = S->mf != NULL;
        uit_fields_t mf = {{0}, {0}};   // the fields past nf are not the model's: written as 0
        for (int f = 0; has && f < S->nf; f++) {
            mf.ftype[f] = S->mf->ftype[f];
            mf.fname[f] = S->mf->fname[f];
        }
        if (rec(b, S, f_slot, NFLD(f_slot)) || raw(b, &has, 1) || (has && rec(b, &mf, f_fields, NFLD(f_fields))))
            return -1;
    }
    if (recs(b, e->acts, e->nacts, sizeof *e->acts, f_act, NFLD(f_act)) ||
        recs(b, e->labels, e->nlabels, sizeof *e->labels, f_labels, NFLD(f_labels)) ||
        recs(b, e->tmpls, e->ntmpls, sizeof *e->tmpls, f_tmpl, NFLD(f_tmpl)) ||
        recs(b, e->ops, e->nops, sizeof *e->ops, f_op, NFLD(f_op)) ||
        recs(b, e->conds, e->nconds, sizeof *e->conds, f_cond, NFLD(f_cond)) ||
        recs(b, e->terms, e->nterms, sizeof *e->terms, f_term, NFLD(f_term)) ||
        recs(b, e->nodes, e->nnodes, sizeof *e->nodes, f_node, NFLD(f_node)) ||
        recs(b, e->props, e->nprops, sizeof *e->props, f_prop, NFLD(f_prop)) ||
        u32(b, (uint32_t)e->nbtns) || raw(b, e->btns, (size_t)e->nbtns * sizeof *e->btns))
        return -1;
    if (u32(b, e->m_pool) || u32(b, (uint32_t)e->m_tmpls) || u32(b, (uint32_t)e->m_ops) ||
        u32(b, (uint32_t)e->m_conds) || u32(b, (uint32_t)e->m_terms) || u32(b, e->page_id))
        return -1;
    uint32_t fo = b->n;
    if (raw(b, front ? front : "", nfront))
        return -1;
    put32(b->p + 8, uit_layout_hash());
    put32(b->p + 12, source);
    put32(b->p + 16, fnv(2166136261u, b->p + UIT_BLOB_HEAD, b->n - UIT_BLOB_HEAD));
    put32(b->p + 20, b->n);
    put32(b->p + 24, fo);
    put32(b->p + 28, (uint32_t)nfront);
    return 0;
}

int uit_save(uit_t *e, char *buf, size_t cap, const char *variant, uint32_t source, const char *front,
             size_t nfront)
{
    if (!e->decl_done || !e->nnodes)
        return uit_fail(e, UIT_E_ARG, UIT_MSG("a blob of a store with no page built", "save"));
    uit_buf_t b = {0};
    if (save(e, &b, variant ? variant : "", source, front, nfront)) {
        uit_buf_free(&b);
        return UIT_E_NOMEM;
    }
    int n = (int)b.n;
    if (buf && cap >= b.n)
        memcpy(buf, b.p, b.n);
    uit_buf_free(&b);
    return n;
}

// -- reading -------------------------------------------------------------------------

typedef struct {
    const char *p;
    size_t at, n;
} rd_t;

static int take(rd_t *r, void *out, size_t n)
{
    if (n > r->n - r->at)
        return -1;
    memcpy(out, r->p + r->at, n);
    r->at += n;
    return 0;
}

static int take32(rd_t *r, uint32_t *v)
{
    if (4 > r->n - r->at)
        return -1;
    *v = get32(r->p + r->at);
    r->at += 4;
    return 0;
}

static int take_rec(rd_t *r, void *base, const uit_fld_t *f, int n)
{
    for (int i = 0; i < n; i++)
        if (take(r, (char *)base + f[i].off, f[i].w))
            return -1;
    return 0;
}

static int take_recs(rd_t *r, void **arr, int *count, int *cap, size_t size, const uit_fld_t *f, int n)
{
    uint32_t c;
    if (take32(r, &c) || c > UIT_NONE || uit_grow(arr, cap, (int)c, size))
        return -1;
    memset(*arr, 0, (size_t)c * size);
    for (uint32_t i = 0; i < c; i++)
        if (take_rec(r, (char *)*arr + i * size, f, n))
            return -1;
    *count = (int)c;
    return 0;
}

#define TAKE_RECS(r, e, arr, n, cap, f) take_recs(r, (void **)&(e)->arr, &(e)->n, &(e)->cap, sizeof *(e)->arr, f, NFLD(f))

static int text_is(rd_t *r, const char *want)
{
    uint8_t l;
    if (take(r, &l, 1) || l > r->n - r->at)
        return -1;
    int same = strlen(want) == l && memcmp(r->p + r->at, want, l) == 0;
    r->at += l;
    return same ? 0 : 1;
}

static int load(uit_t *e, rd_t *r)
{
    uint32_t n;
    if (take32(r, &n) || n < 1 || n > r->n - r->at)
        return -1;
    e->pool.n = 0;
    if (raw(&e->pool, r->p + r->at, n) || e->pool.p[n - 1])
        return -1;
    r->at += n;
    if (take32(r, &n) || n > UIT_NONE || uit_grow((void **)&e->slots, &e->cslots, (int)n, sizeof *e->slots))
        return -1;
    memset(e->slots, 0, n * sizeof *e->slots);
    e->nslots = (int)n;
    for (uint32_t i = 0; i < n; i++) {
        uit_slot_t *S = &e->slots[i];
        uint8_t has;
        if (take_rec(r, S, f_slot, NFLD(f_slot)) || take(r, &has, 1))
            return -1;
        if (has && (!(S->mf = UIT_MALLOC(sizeof *S->mf)) || take_rec(r, S->mf, f_fields, NFLD(f_fields))))
            return -1;
    }
    if (TAKE_RECS(r, e, acts, nacts, cacts, f_act) || TAKE_RECS(r, e, labels, nlabels, clabels, f_labels) ||
        TAKE_RECS(r, e, tmpls, ntmpls, ctmpls, f_tmpl) || TAKE_RECS(r, e, ops, nops, cops, f_op) ||
        TAKE_RECS(r, e, conds, nconds, cconds, f_cond) || TAKE_RECS(r, e, terms, nterms, cterms, f_term) ||
        TAKE_RECS(r, e, nodes, nnodes, cnodes, f_node) || TAKE_RECS(r, e, props, nprops, cprops, f_prop))
        return -1;
    if (take32(r, &n) || n > UIT_NONE || uit_grow((void **)&e->btns, &e->cbtns, (int)n, sizeof *e->btns) ||
        take(r, e->btns, n * sizeof *e->btns))
        return -1;
    e->nbtns = (int)n;
    uint32_t m[6];
    for (int i = 0; i < 6; i++)
        if (take32(r, &m[i]))
            return -1;
    if (m[0] > e->pool.n || (int)m[1] > e->ntmpls || (int)m[2] > e->nops || (int)m[3] > e->nconds ||
        (int)m[4] > e->nterms || m[5] >= e->pool.n)
        return -1;
    e->m_pool = m[0];
    e->m_tmpls = (int)m[1];
    e->m_ops = (int)m[2];
    e->m_conds = (int)m[3];
    e->m_terms = (int)m[4];
    e->page_id = (uint16_t)m[5];
    e->decl_done = 1;
    return 0;
}

int uit_load(uit_t *e, const char *blob, size_t n, const char *page_id, const char *variant)
{
    if (n < UIT_BLOB_HEAD || memcmp(blob, "MUIT", 4))
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("not a compiled page (no MUIT)", "blob magic"));
    int fmt = (uint8_t)blob[4] | (uint8_t)blob[5] << 8;
    if (fmt != UIT_FORMAT)
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page of format %d; this engine reads format %d",
                                               "blob format %d %d"), fmt, UIT_FORMAT);
    uint32_t lay = get32(blob + 8);
    if (lay != uit_layout_hash())
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page of layout %08lx; this engine's layout is %08lx",
                                               "blob layout %08lx %08lx"),
                        (unsigned long)lay, (unsigned long)uit_layout_hash());
    if (get32(blob + 20) != n || (uint8_t)blob[6] != UIT_BLOB_HEAD ||
        get32(blob + 16) != fnv(2166136261u, blob + UIT_BLOB_HEAD, n - UIT_BLOB_HEAD))
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page damaged: its length or checksum is not its own",
                                               "blob damaged"));
    if (e->nslots || e->nacts || e->nlabels || e->nnodes || e->decl_done)
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page loads into a new store only", "blob store"));
    rd_t r = {blob, UIT_BLOB_HEAD, get32(blob + 24)};
    int id = text_is(&r, page_id ? page_id : ""), var = id < 0 ? -1 : text_is(&r, variant ? variant : "");
    if (id || var)
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page of another page or variant than %s %s",
                                               "blob page %s %s"), page_id ? page_id : "", variant ? variant : "");
    uint8_t placed = 0;
    if (take(&r, &placed, 1) || placed != e->placed)
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page %s; this store is %s",
                                               "blob placed %s %s"), placed ? "placed" : "not placed",
                        e->placed ? "placed" : "not placed");
    if (load(e, &r) || r.at != r.n) {
        for (int i = 0; i < e->nslots; i++) {
            UIT_FREE(e->slots[i].mf);
            e->slots[i].mf = NULL;
        }
        e->nslots = e->nacts = e->nlabels = e->ntmpls = e->nops = e->nconds = e->nterms = 0;
        e->nnodes = e->nprops = e->nbtns = e->decl_done = 0;
        e->pool.n = 0;
        uit_buf_put(&e->pool, "", 0);
        return uit_fail(e, UIT_E_BLOB, UIT_MSG("a compiled page whose sections do not read", "blob sections"));
    }
    return uit_build_end(e);
}
