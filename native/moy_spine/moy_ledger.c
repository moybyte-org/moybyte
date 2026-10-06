// moy_ledger.h has the contract.

#include <string.h>

#include "moy_ledger.h"

// -- a scanner for text a settings row already validated ----------------------

static const char *skip_ws(const char *p, const char *e) {
    while (p < e && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
        p++;
    }
    return p;
}

static const char *skip_string(const char *p, const char *e) {
    p++;
    while (p < e && *p != '"') {
        p += (*p == '\\' && p + 1 < e) ? 2 : 1;
    }
    return p < e ? p + 1 : NULL;
}

// The end of the value that starts at `p`, or NULL.
static const char *skip_value(const char *p, const char *e) {
    if (p >= e) {
        return NULL;
    }
    if (*p == '"') {
        return skip_string(p, e);
    }
    if (*p == '{' || *p == '[') {
        int depth = 0;
        while (p < e) {
            if (*p == '"') {
                p = skip_string(p, e);
                if (p == NULL) {
                    return NULL;
                }
                continue;
            }
            if (*p == '{' || *p == '[') {
                depth++;
            } else if (*p == '}' || *p == ']') {
                if (--depth == 0) {
                    return p + 1;
                }
            }
            p++;
        }
        return NULL;
    }
    while (p < e && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\t'
           && *p != '\n' && *p != '\r') {
        p++;
    }
    return p;
}

int moy_jobj_members(const char *text, size_t n, moy_jmem_t *m, size_t cap) {
    const char *p = text, *e = text + n;
    int count = 0;
    p = skip_ws(p, e);
    if (p >= e || *p != '{') {
        return -1;
    }
    p = skip_ws(p + 1, e);
    if (p < e && *p == '}') {
        return 0;
    }
    for (;;) {
        if (p >= e || *p != '"') {
            return -1;
        }
        const char *k = p, *ke = skip_string(p, e);
        if (ke == NULL) {
            return -1;
        }
        p = skip_ws(ke, e);
        if (p >= e || *p != ':') {
            return -1;
        }
        p = skip_ws(p + 1, e);
        const char *v = p, *ve = skip_value(p, e);
        if (ve == NULL || ve == v) {
            return -1;
        }
        if ((size_t)count < cap) {
            m[count].k = k;
            m[count].kn = (size_t)(ke - k);
            m[count].v = v;
            m[count].vn = (size_t)(ve - v);
        }
        count++;
        p = skip_ws(ve, e);
        if (p < e && *p == ',') {
            p = skip_ws(p + 1, e);
            continue;
        }
        return (p < e && *p == '}') ? count : -1;
    }
}

static int key_is(const moy_jmem_t *m, const char *k, size_t kn) {
    return m->kn == kn && memcmp(m->k, k, kn) == 0;
}

static int name_is(const moy_jmem_t *m, const char *name) {
    size_t n = strlen(name);
    return m->kn == n + 2u && memcmp(m->k + 1, name, n) == 0;
}

static int span_is(const char *a, size_t an, const char *b, size_t bn) {
    return an == bn && memcmp(a, b, an) == 0;
}

static int find_member(const char *text, size_t n, const char *name, moy_jmem_t *out);

// -- queries --------------------------------------------------------------------

// Member `i` of the object `text` holds: 1 and its spans, else 0. A ledger holds
// a handful, so a walk from the start per call beats an array.
int moy_jobj_at(const char *text, size_t n, int i, moy_jmem_t *out) {
    moy_jmem_t m[1];
    const char *p = text, *e = text + n;
    int count = 0;
    p = skip_ws(p, e);
    if (p >= e || *p != '{') {
        return 0;
    }
    p = skip_ws(p + 1, e);
    while (p < e && *p == '"') {
        const char *k = p, *ke = skip_string(p, e);
        if (ke == NULL) {
            return 0;
        }
        p = skip_ws(ke, e);
        if (p >= e || *p != ':') {
            return 0;
        }
        p = skip_ws(p + 1, e);
        const char *v = p, *ve = skip_value(p, e);
        if (ve == NULL) {
            return 0;
        }
        if (count == i) {
            m[0].k = k;
            m[0].kn = (size_t)(ke - k);
            m[0].v = v;
            m[0].vn = (size_t)(ve - v);
            *out = m[0];
            return 1;
        }
        count++;
        p = skip_ws(ve, e);
        if (p < e && *p == ',') {
            p = skip_ws(p + 1, e);
        }
    }
    return 0;
}

int moy_ledger_get(const char *slot, size_t n, const char *name, const char **v,
                   size_t *vn) {
    moy_jmem_t m;
    if (slot == NULL || !find_member(slot, n, name, &m)) {
        return 0;
    }
    *v = m.v;
    *vn = m.vn;
    return 1;
}

static int find_member(const char *text, size_t n, const char *name, moy_jmem_t *out) {
    int c = moy_jobj_members(text, n, NULL, 0);
    for (int i = 0; i < c; i++) {
        if (moy_jobj_at(text, n, i, out) && name_is(out, name)) {
            return 1;
        }
    }
    return 0;
}

int moy_ledger_section(const char *slot, size_t n, const char *name,
                       const char **v, size_t *vn) {
    moy_jmem_t m;
    if (slot == NULL || !find_member(slot, n, name, &m) || m.v[0] != '{') {
        return 0;
    }
    *v = m.v;
    *vn = m.vn;
    return 1;
}

// int() of a member's value, as the Python ledger reads a strike count: an
// integer, a float cut toward zero, a bool, a string of digits; anything else 0.
static int32_t count_of(const char *v, size_t n) {
    if (n == 4 && memcmp(v, "true", 4) == 0) {
        return 1;
    }
    size_t i = 0;
    int neg = 0, quoted = 0;
    if (n > 0 && v[0] == '"') {
        quoted = 1;
        i = 1;
    }
    if (i < n && v[i] == '-') {
        neg = 1;
        i++;
    }
    if (i >= n || v[i] < '0' || v[i] > '9') {
        return 0;
    }
    int64_t x = 0;
    for (; i < n && v[i] >= '0' && v[i] <= '9'; i++) {
        x = x * 10 + (v[i] - '0');
        if (x > 1000000) {
            x = 1000000;
        }
    }
    if (quoted && !(i + 1 == n && v[i] == '"')) {
        return 0;                       // int("3x") raises: not a count
    }
    return (int32_t)(neg ? -x : x);
}

int32_t moy_ledger_strikes(const char *slot, size_t n, const char *id, size_t idn) {
    const char *s;
    size_t sn;
    if (!moy_ledger_section(slot, n, "strikes", &s, &sn)) {
        return 0;
    }
    int c = moy_jobj_members(s, sn, NULL, 0);
    for (int i = 0; i < c; i++) {
        moy_jmem_t m;
        if (moy_jobj_at(s, sn, i, &m) && key_is(&m, id, idn)) {
            return count_of(m.v, m.vn);
        }
    }
    return 0;
}

int moy_ledger_open_is(const char *slot, size_t n, const char *id, size_t idn) {
    moy_jmem_t m;
    return slot != NULL && find_member(slot, n, "open", &m)
           && span_is(m.v, m.vn, id, idn);
}

int moy_ledger_proven_is(const char *slot, size_t n, const char *id, size_t idn,
                         const char *proof, size_t pn) {
    const char *s;
    size_t sn;
    if (!moy_ledger_section(slot, n, "proven", &s, &sn)) {
        return 0;
    }
    int c = moy_jobj_members(s, sn, NULL, 0);
    for (int i = 0; i < c; i++) {
        moy_jmem_t m;
        if (moy_jobj_at(s, sn, i, &m) && key_is(&m, id, idn)) {
            return span_is(m.v, m.vn, proof, pn);
        }
    }
    return 0;
}

// -- edits ----------------------------------------------------------------------

typedef struct {
    char *p;
    size_t n, cap;
    const moy_htab_mem_t *mem;
    int bad;
} sbuf_t;

static void put(sbuf_t *b, const char *s, size_t n) {
    if (b->bad) {
        return;
    }
    if (b->n + n > b->cap) {
        size_t cap = b->cap ? b->cap : 256u;
        while (cap < b->n + n) {
            cap *= 2u;
        }
        char *q = b->mem->alloc(cap);
        if (q == NULL) {
            b->bad = 1;
            return;
        }
        if (b->n) {
            memcpy(q, b->p, b->n);
        }
        if (b->p) {
            b->mem->release(b->p, b->cap);
        }
        b->p = q;
        b->cap = cap;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
}

static void puts_(sbuf_t *b, const char *s) {
    put(b, s, strlen(s));
}

// A member list being edited: spans, a removed one marked by a NULL key, an
// added one pointing at text that outlives the edit.
typedef struct {
    moy_jmem_t *m;
    int n, cap;
    size_t bytes;
} sec_t;

static int sec_load(const moy_htab_mem_t *mem, sec_t *s, const char *text,
                    size_t n, int extra) {
    int c = text ? moy_jobj_members(text, n, NULL, 0) : 0;
    if (c < 0) {
        c = 0;
    }
    s->cap = c + extra;
    s->bytes = (size_t)s->cap * sizeof(moy_jmem_t);
    s->m = mem->alloc(s->bytes);
    if (s->m == NULL) {
        return 0;
    }
    s->n = c;
    for (int i = 0; i < c; i++) {
        moy_jobj_at(text, n, i, &s->m[i]);
    }
    return 1;
}

static void sec_free(const moy_htab_mem_t *mem, sec_t *s) {
    if (s->m) {
        mem->release(s->m, s->bytes);
        s->m = NULL;
    }
}

static int sec_find(const sec_t *s, const char *k, size_t kn) {
    for (int i = 0; i < s->n; i++) {
        if (s->m[i].k != NULL && key_is(&s->m[i], k, kn)) {
            return i;
        }
    }
    return -1;
}

static int sec_find_name(const sec_t *s, const char *name) {
    for (int i = 0; i < s->n; i++) {
        if (s->m[i].k != NULL && name_is(&s->m[i], name)) {
            return i;
        }
    }
    return -1;
}

static void sec_set(sec_t *s, const char *k, size_t kn, const char *v, size_t vn) {
    int i = sec_find(s, k, kn);
    if (i < 0) {
        i = s->n++;
        s->m[i].k = k;
        s->m[i].kn = kn;
    }
    s->m[i].v = v;
    s->m[i].vn = vn;
}

static int sec_remove(sec_t *s, const char *k, size_t kn) {
    int i = sec_find(s, k, kn);
    if (i < 0) {
        return 0;
    }
    s->m[i].k = NULL;
    return 1;
}

static void sec_put(sbuf_t *b, const sec_t *s) {
    int first = 1;
    puts_(b, "{");
    for (int i = 0; i < s->n; i++) {
        if (s->m[i].k == NULL) {
            continue;
        }
        if (!first) {
            puts_(b, ", ");
        }
        first = 0;
        put(b, s->m[i].k, s->m[i].kn);
        puts_(b, ": ");
        put(b, s->m[i].v, s->m[i].vn);
    }
    puts_(b, "}");
}

int moy_ledger_edit(const moy_htab_mem_t *mem, const char *slot, size_t n,
                    int op, const char *id, size_t idn, const char *proof,
                    size_t pn, char **out, size_t *outn) {
    static const char strikes_k[] = "\"strikes\"", open_k[] = "\"open\"",
                      proven_k[] = "\"proven\"", empty[] = "{}", null_v[] = "null";
    sec_t top = {0}, st = {0}, pv = {0};
    sbuf_t b = { NULL, 0, 0, mem, 0 };
    char num[16];
    int rc = MOY_LEDGER_NOMEM, edit_proven = (op == MOY_LEDGER_HEAL && proof != NULL);

    if (!sec_load(mem, &top, slot, n, 3)) {
        return MOY_LEDGER_NOMEM;
    }
    int ti = sec_find_name(&top, "strikes");
    if (ti >= 0 && top.m[ti].v[0] != '{') {
        top.m[ti].v = empty;               // replaced in place, as `_data` does
        top.m[ti].vn = 2;
    }
    if (ti < 0) {
        ti = top.n++;
        top.m[ti].k = strikes_k;
        top.m[ti].kn = sizeof strikes_k - 1u;
        top.m[ti].v = empty;
        top.m[ti].vn = 2;
    }
    if (!sec_load(mem, &st, top.m[ti].v, top.m[ti].vn, 1)) {
        goto done;
    }
    int oi = sec_find_name(&top, "open");
    int matches = oi >= 0 && span_is(top.m[oi].v, top.m[oi].vn, id, idn);
    int pi = -1;
    if (edit_proven) {
        pi = sec_find_name(&top, "proven");
        const char *pt = NULL;
        size_t ptn = 0;
        if (pi >= 0 && top.m[pi].v[0] == '{') {
            pt = top.m[pi].v;
            ptn = top.m[pi].vn;
        }
        if (pi < 0) {
            pi = top.n++;
            top.m[pi].k = proven_k;
            top.m[pi].kn = sizeof proven_k - 1u;
            top.m[pi].v = empty;
            top.m[pi].vn = 2;
        }
        if (!sec_load(mem, &pv, pt, ptn, 1)) {
            goto done;
        }
    }

    rc = 0;
    if (op == MOY_LEDGER_ARM) {
        int32_t cur = moy_ledger_strikes(slot, n, id, idn);
        int64_t v = (int64_t)cur + 1;
        int len = 0, neg = v < 0;
        char tmp[16];
        uint64_t u = neg ? (uint64_t)-v : (uint64_t)v;
        do {
            tmp[len++] = (char)('0' + u % 10u);
            u /= 10u;
        } while (u);
        size_t k = 0;
        if (neg) {
            num[k++] = '-';
        }
        while (len) {
            num[k++] = tmp[--len];
        }
        sec_set(&st, id, idn, num, k);
        if (oi < 0) {
            oi = top.n++;
            top.m[oi].k = open_k;
            top.m[oi].kn = sizeof open_k - 1u;
        }
        top.m[oi].v = id;
        top.m[oi].vn = idn;
    } else {
        int removed = sec_remove(&st, id, idn);
        if (op == MOY_LEDGER_FORGIVE && !removed && !matches) {
            goto done;
        }
        if (matches) {
            top.m[oi].v = null_v;
            top.m[oi].vn = 4;
        }
        if (edit_proven) {
            sec_set(&pv, id, idn, proof, pn);
        }
    }

    puts_(&b, "{");
    int first = 1;
    for (int i = 0; i < top.n; i++) {
        if (!first) {
            puts_(&b, ", ");
        }
        first = 0;
        put(&b, top.m[i].k, top.m[i].kn);
        puts_(&b, ": ");
        if (i == sec_find_name(&top, "strikes")) {
            sec_put(&b, &st);
        } else if (edit_proven && i == pi) {
            sec_put(&b, &pv);
        } else {
            put(&b, top.m[i].v, top.m[i].vn);
        }
    }
    puts_(&b, "}");
    if (b.bad) {
        rc = MOY_LEDGER_NOMEM;
        if (b.p) {
            mem->release(b.p, b.cap);
        }
    } else {
        char *exact = mem->alloc(b.n);       // sized to the text, as release() is told
        if (exact == NULL) {
            mem->release(b.p, b.cap);
            rc = MOY_LEDGER_NOMEM;
        } else {
            memcpy(exact, b.p, b.n);
            mem->release(b.p, b.cap);
            *out = exact;
            *outn = b.n;
            rc = 1;
        }
    }
done:
    sec_free(mem, &top);
    sec_free(mem, &st);
    sec_free(mem, &pv);
    return rc;
}
