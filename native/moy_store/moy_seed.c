// The seed (moy_seed.h has the contract; runtime/moy_seed.py's seed_builtins
// is the reference every branch below follows).

#include <stdio.h>
#include <string.h>

#include "moy_cat.h"
#include "moy_json.h"
#include "moy_seed.h"

#ifdef MOY_STORE_MICROPYTHON
#include "py/mpconfig.h"
#define MOY_SEED_INFLATE MICROPY_PY_DEFLATE
#else
#define MOY_SEED_INFLATE 1
#endif
#if MOY_SEED_INFLATE
#include "lib/uzlib/uzlib.h"
#endif

size_t moy_seed_folder(const char *title, size_t n, const char *ns, char *out,
                       size_t cap) {
    size_t k = 0;
    char c;
#define PUT(ch) do { c = (ch); if (k + 1u < cap) { out[k] = c; } k++; } while (0)
    size_t start = 0;
    if (ns != NULL && ns[0]) {
        for (const char *p = ns; *p; p++) {
            unsigned char u = (unsigned char)*p;
            if (u >= 'A' && u <= 'Z') {
                PUT((char)(u + 32));
            } else if ((u >= 'a' && u <= 'z') || (u >= '0' && u <= '9')) {
                PUT((char)u);
            } else if (u == ' ' || u == '-' || u == '_') {
                PUT('_');
            }
        }
        if (k == 0) {
            PUT('c');
            PUT('a');
            PUT('r');
            PUT('t');
        }
        PUT('.');
    }
    start = k;
    for (size_t i = 0; i < n; i++) {
        unsigned char u = (unsigned char)title[i];
        if (u >= 'A' && u <= 'Z') {
            PUT((char)(u + 32));
        } else if ((u >= 'a' && u <= 'z') || (u >= '0' && u <= '9')) {
            PUT((char)u);
        } else if (u == ' ' || u == '-' || u == '_') {
            PUT('_');
        }
    }
    if (k == start) {
        PUT('c');
        PUT('a');
        PUT('r');
        PUT('t');
    }
    PUT('.');
    PUT('m');
    PUT('o');
    PUT('y');
#undef PUT
    if (cap) {
        out[k < cap ? k : cap - 1u] = 0;
    }
    return k;
}

int moy_seed_inflate(const uint8_t *blob, size_t n, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    #if !MOY_SEED_INFLATE
    (void)blob;
    (void)n;
    return MOY_ENODEV;      // an image with no `deflate` links no inflater
    #else
    size_t cap = n * 6u + 1024u;
    if (cap > MOY_SEED_MAX) {
        cap = MOY_SEED_MAX;
    }
    unsigned char *buf = moy_store_alloc(cap + 1u);
    uzlib_uncomp_t *d = moy_store_alloc(sizeof(uzlib_uncomp_t));
    if (buf == NULL || d == NULL) {
        moy_store_free(buf, cap + 1u);
        moy_store_free(d, sizeof(uzlib_uncomp_t));
        return MOY_ENOMEM;
    }
    uzlib_uncompress_init(d, NULL, 0);
    d->source = blob;
    d->source_limit = blob + n;
    d->source_read_cb = NULL;
    d->dest_start = d->dest = buf;
    d->dest_limit = buf + cap;
    int rc = 0;
    for (;;) {
        int st = uzlib_uncompress(d);
        if (st == UZLIB_DONE) {
            break;
        }
        if (st != UZLIB_OK || d->eof) {
            rc = MOY_EINVAL;
            break;
        }
        if (d->dest < d->dest_limit) {
            continue;
        }
        if (cap >= MOY_SEED_MAX) {
            rc = MOY_EFBIG;
            break;
        }
        size_t ncap = cap * 2u > MOY_SEED_MAX ? MOY_SEED_MAX : cap * 2u;
        unsigned char *nb = moy_store_alloc(ncap + 1u);
        if (nb == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        size_t have = (size_t)(d->dest - buf);
        memcpy(nb, buf, have);
        moy_store_free(buf, cap + 1u);
        buf = nb;
        cap = ncap;
        d->dest_start = buf;
        d->dest = buf + have;
        d->dest_limit = buf + cap;
    }
    size_t have = (size_t)(d->dest - buf);
    moy_store_free(d, sizeof(uzlib_uncomp_t));
    if (rc != 0) {
        moy_store_free(buf, cap + 1u);
        return rc;
    }
    char *t = moy_store_alloc(have + 1u);       // sized as moy_buf_free frees it
    if (t == NULL) {
        moy_store_free(buf, cap + 1u);
        return MOY_ENOMEM;
    }
    memcpy(t, buf, have);
    moy_store_free(buf, cap + 1u);
    out->p = t;
    out->n = have;
    return 0;
    #endif
}

// -- writing -------------------------------------------------------------------------

typedef struct {
    char *p;
    size_t n, cap;
    int failed;
} sb_t;

static void sb_put(sb_t *b, const char *s, size_t n) {
    if (b->failed) {
        return;
    }
    if (b->n + n + 1u > b->cap) {
        size_t cap = b->cap ? b->cap : 256u;
        while (cap < b->n + n + 1u) {
            cap *= 2u;
        }
        char *p = moy_store_alloc(cap);
        if (p == NULL) {
            b->failed = 1;
            return;
        }
        if (b->n) {
            memcpy(p, b->p, b->n);
        }
        moy_store_free(b->p, b->cap);
        b->p = p;
        b->cap = cap;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
}

static void sb_str(sb_t *b, const char *s) {
    sb_put(b, s, strlen(s));
}

static void sb_canon(sb_t *b, const char *v, const char *ve) {
    size_t n = moy_json_canon(v, ve, NULL, 0);
    char *t = n == MOY_JSON_DEEP ? NULL : moy_store_alloc(n + 1u);
    if (t == NULL) {
        b->failed = 1;
        return;
    }
    moy_json_canon(v, ve, t, n);
    sb_put(b, t, n);
    moy_store_free(t, n + 1u);
}

static void sb_free(sb_t *b) {
    moy_store_free(b->p, b->cap);
    memset(b, 0, sizeof *b);
}

typedef struct {
    const char *v, *e;
} span_t;

static span_t get(const char *obj, const char *end, const char *key) {
    span_t s = { NULL, NULL };
    if (!moy_json_get(obj, end, key, &s.v, &s.e)) {
        s.v = s.e = NULL;
    }
    return s;
}

static int truthy(span_t s) {
    return s.v != NULL && moy_json_truthy(s.v, s.e);
}

// A string value decoded, scratch: NULL when it is not a string.
static char *decoded(span_t s, size_t *n) {
    if (s.v == NULL || *s.v != '"') {
        return NULL;
    }
    size_t k = moy_json_strlen(s.v, s.e);
    char *d = moy_store_alloc(k + 1u);
    if (d != NULL) {
        *n = moy_json_str(s.v, s.e, d);
        d[*n] = 0;
    }
    return d;
}

static char *path3(const char *a, const char *b, const char *c, size_t *n) {
    size_t an = strlen(a), bn = strlen(b), cn = c ? strlen(c) : 0;
    *n = an + bn + cn + 3u;
    char *p = moy_store_alloc(*n);
    if (p != NULL) {
        memcpy(p, a, an);
        p[an] = '/';
        memcpy(p + an + 1, b, bn);
        if (c != NULL) {
            p[an + 1 + bn] = '/';
            memcpy(p + an + 2 + bn, c, cn);
        }
    }
    return p;
}

// `text` written at `dir`/`name`: 0 or an errno value.
static int put_file(const char *dir, const char *name, const char *text,
                    size_t n, int bytes) {
    size_t pn;
    char *p = path3(dir, name, NULL, &pn);
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    int rc = bytes ? moy_fs_write_bytes(p, text, n) : moy_fs_write(p, text, n);
    moy_store_free(p, pn);
    return rc;
}

// A string member written as a file: 0, or EINVAL when it is not a string.
static int put_str(const char *dir, const char *name, span_t s) {
    size_t n;
    char *t = decoded(s, &n);
    if (t == NULL) {
        return s.v != NULL && *s.v == '"' ? MOY_ENOMEM : MOY_EINVAL;
    }
    int rc = put_file(dir, name, t, n, 0);
    moy_store_free(t, n + 1u);
    return rc;
}

static int put_json(const char *dir, const char *name, span_t s) {
    sb_t b = { NULL, 0, 0, 0 };
    sb_canon(&b, s.v, s.e);
    int rc = b.failed ? MOY_ENOMEM : put_file(dir, name, b.p, b.n, 0);
    sb_free(&b);
    return rc;
}

static int b64v(unsigned char c) {
    if (c >= 'A' && c <= 'Z') {
        return c - 'A';
    }
    if (c >= 'a' && c <= 'z') {
        return c - 'a' + 26;
    }
    if (c >= '0' && c <= '9') {
        return c - '0' + 52;
    }
    if (c == '+') {
        return 62;
    }
    if (c == '/') {
        return 63;
    }
    return -1;
}

// binascii.a2b_base64's reading: characters outside the alphabet skipped, the
// first pad ends the data.
static size_t b64_decode(const char *s, size_t n, uint8_t *out) {
    uint32_t acc = 0;
    int bits = 0;
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '=') {
            break;
        }
        int v = b64v((unsigned char)s[i]);
        if (v < 0) {
            continue;
        }
        acc = (acc << 6) | (uint32_t)v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out[k++] = (uint8_t)(acc >> bits);
        }
    }
    return k;
}

// The members of a `{name: text}` object, each written as `sub`/name + ext.
static int put_folder(const char *d, const char *sub, const char *ext, span_t obj) {
    size_t sn;
    char *dir = path3(d, sub, NULL, &sn);
    if (dir == NULL) {
        return MOY_ENOMEM;
    }
    moy_fs_mkdir(dir);
    moy_json_iter_t it;
    const char *k, *ke, *v, *ve;
    int rc = 0;
    moy_json_iter(&it, obj.v, obj.e);
    while (rc == 0 && moy_json_next(&it, &k, &ke, &v, &ve)) {
        size_t kn = moy_json_strlen(k, ke), xn = strlen(ext);
        char *name = moy_store_alloc(kn + xn + 1u);
        if (name == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        moy_json_str(k, ke, name);
        memcpy(name + kn, ext, xn + 1u);
        span_t s = { v, ve };
        rc = put_str(dir, name, s);
        moy_store_free(name, kn + xn + 1u);
    }
    moy_store_free(dir, sn);
    return rc;
}

// The version of the cart in `d`, 0 for none; `*whole` says whether it has a
// manifest that reads as one, which is what a seeded folder is once whole.
static int64_t cart_version(const char *d, int *whole) {
    size_t pn;
    char *p = path3(d, "manifest.json", NULL, &pn);
    int64_t v = 0;
    moy_buf_t b;
    *whole = 0;
    if (p != NULL && moy_fs_read(p, NULL, &b) == 0) {
        const char *s = moy_json_ws(b.p, b.p + b.n);
        const char *e = moy_json_value(s, b.p + b.n, 1u);
        if (e != NULL && moy_json_ws(e, b.p + b.n) == b.p + b.n && *s == '{') {
            *whole = 1;
            span_t f = get(s, e, "version");
            int64_t x;
            if (f.v == NULL) {
                v = 0;
            } else if (moy_json_int(f.v, f.e, &x) == 1) {
                v = x;
            } else if (moy_json_int(f.v, f.e, &x) == 2 && *f.v != '-') {
                v = INT64_MAX;
            }
        }
        moy_buf_free(&b);
    }
    moy_store_free(p, pn);
    return v;
}

// `v` in decimal: the store's own, since an image's printf may lack %lld.
static const char *i64(int64_t v, char *buf) {
    char *p = buf + 23;
    uint64_t u = v < 0 ? (uint64_t)0 - (uint64_t)v : (uint64_t)v;
    *p = 0;
    do {
        *--p = (char)('0' + u % 10u);
        u /= 10u;
    } while (u);
    if (v < 0) {
        *--p = '-';
    }
    return p;
}

// The manifest seed_builtins writes, in its key order: the spec's fields, then
// Moybyte's own under "moybyte". `folder` is the cart's folder, whose name
// without the extension is its id.
static void manifest(sb_t *b, const char *c, const char *ce, int64_t ver,
                     const char *folder) {
    span_t f;
    sb_str(b, "{\"format\": ");
    f = get(c, ce, "format");
    if (f.v != NULL) {
        sb_canon(b, f.v, f.e);
    } else {
        sb_str(b, "\"" MOY_CAT_FORMAT "\"");
    }
    sb_str(b, ", \"title\": ");
    f = get(c, ce, "title");
    sb_canon(b, f.v, f.e);
    size_t fn = strlen(folder);
    if (fn >= 4u && memcmp(folder + fn - 4u, ".moy", 4u) == 0) {
        fn -= 4u;
    }
    sb_str(b, ", \"id\": \"");
    sb_put(b, folder, fn);              // the store's own name: [a-z0-9_.] only
    sb_str(b, "\"");
    sb_str(b, ", \"runtime\": ");
    f = get(c, ce, "runtime");
    if (f.v != NULL) {
        sb_canon(b, f.v, f.e);
    } else {
        sb_str(b, "\"python\"");
    }
    span_t main = get(c, ce, "main");
    sb_str(b, ", \"main\": ");
    if (main.v != NULL) {
        sb_canon(b, main.v, main.e);
    } else {
        sb_str(b, "\"main.py\"");
    }
    char num[24];
    sb_str(b, ", \"version\": ");
    sb_str(b, i64(ver, num));
    span_t pre = get(c, ce, "src_before"), post = get(c, ce, "src_after");
    if (truthy(pre) || truthy(post)) {
        int first = 1;
        sb_str(b, ", \"sources\": [");
        for (int part = 0; part < 3; part++) {
            if (part == 1) {
                if (!first) {
                    sb_str(b, ", ");
                }
                first = 0;
                if (main.v != NULL) {
                    sb_canon(b, main.v, main.e);
                } else {
                    sb_str(b, "\"main.py\"");
                }
                continue;
            }
            span_t l = part == 0 ? pre : post;
            if (!truthy(l) || *l.v != '[') {
                continue;
            }
            moy_json_iter_t it, pair;
            const char *v, *ve, *nm, *nme;
            moy_json_iter(&it, l.v, l.e);
            while (moy_json_next(&it, NULL, NULL, &v, &ve)) {
                if (*v != '[') {
                    continue;
                }
                moy_json_iter(&pair, v, ve);
                if (!moy_json_next(&pair, NULL, NULL, &nm, &nme)) {
                    continue;
                }
                if (!first) {
                    sb_str(b, ", ");
                }
                first = 0;
                sb_canon(b, nm, nme);
            }
        }
        sb_str(b, "]");
    }
    f = get(c, ce, "fps");
    if (truthy(f)) {
        sb_str(b, ", \"fps\": ");
        sb_canon(b, f.v, f.e);
    }
    f = get(c, ce, "icon");
    if (truthy(f)) {
        sb_str(b, ", \"icon\": ");
        sb_canon(b, f.v, f.e);
    }
    f = get(c, ce, "canvas");
    if (f.v != NULL && *f.v != 'n') {
        sb_str(b, ", \"canvas\": ");
        moy_json_iter_t it;
        const char *x, *xe, *y, *ye, *z, *ze;
        int64_t w, h;
        if (*f.v == '[') {
            moy_json_iter(&it, f.v, f.e);
            if (moy_json_next(&it, NULL, NULL, &x, &xe)
                && moy_json_next(&it, NULL, NULL, &y, &ye)
                && !moy_json_next(&it, NULL, NULL, &z, &ze)
                && moy_json_int(x, xe, &w) == 1 && moy_json_int(y, ye, &h) == 1) {
                sb_str(b, "\"");
                sb_str(b, i64(w, num));
                sb_str(b, "x");
                sb_str(b, i64(h, num));
                sb_str(b, "\"");
            } else {
                sb_canon(b, f.v, f.e);
            }
        } else {
            sb_canon(b, f.v, f.e);
        }
    }
    f = get(c, ce, "input");
    if (f.v != NULL && *f.v != 'n') {
        sb_str(b, ", \"input\": ");
        sb_canon(b, f.v, f.e);
    }
    sb_str(b, ", \"moybyte\": {\"type\": ");
    f = get(c, ce, "type");
    sb_canon(b, f.v, f.e);
    sb_str(b, ", \"edit\": ");
    f = get(c, ce, "edit");
    if (f.v != NULL) {
        sb_canon(b, f.v, f.e);
    } else {
        sb_str(b, "[]");
    }
    f = get(c, ce, "permissions");
    if (f.v != NULL && *f.v != 'n') {
        sb_str(b, ", \"permissions\": ");
        sb_canon(b, f.v, f.e);
    }
    span_t scenes = get(c, ce, "scenes");
    if (truthy(scenes) && *scenes.v == '{') {
        sb_str(b, ", \"assets\": {\"scenes\": ");
        f = get(c, ce, "scene_order");
        if (truthy(f)) {
            sb_canon(b, f.v, f.e);
        } else {
            // sorted(scenes.keys()): the distinct keys in code point order
            moy_json_iter_t it;
            const char *k, *ke, *v, *ve;
            const char *last = NULL, *last_e = NULL;
            int first = 1;
            sb_str(b, "[");
            for (;;) {
                const char *best = NULL, *best_e = NULL;
                size_t bn = 0;
                char *bs = NULL;
                moy_json_iter(&it, scenes.v, scenes.e);
                while (moy_json_next(&it, &k, &ke, &v, &ve)) {
                    size_t kn;
                    char *ks = decoded((span_t){ k, ke }, &kn);
                    if (ks == NULL) {
                        b->failed = 1;
                        return;
                    }
                    int after = 1;
                    if (last != NULL) {
                        size_t ln;
                        char *ls = decoded((span_t){ last, last_e }, &ln);
                        if (ls == NULL) {
                            moy_store_free(ks, kn + 1u);
                            b->failed = 1;
                            return;
                        }
                        int cmp = memcmp(ks, ls, kn < ln ? kn : ln);
                        after = cmp > 0 || (cmp == 0 && kn > ln);
                        moy_store_free(ls, ln + 1u);
                    }
                    int better = 0;
                    if (after) {
                        if (bs == NULL) {
                            better = 1;
                        } else {
                            int cmp = memcmp(ks, bs, kn < bn ? kn : bn);
                            better = cmp < 0 || (cmp == 0 && kn < bn);
                        }
                    }
                    if (better) {
                        if (bs != NULL) {
                            moy_store_free(bs, bn + 1u);
                        }
                        bs = ks;
                        bn = kn;
                        best = k;
                        best_e = ke;
                    } else {
                        moy_store_free(ks, kn + 1u);
                    }
                }
                if (best == NULL) {
                    break;
                }
                moy_store_free(bs, bn + 1u);
                if (!first) {
                    sb_str(b, ", ");
                }
                first = 0;
                sb_canon(b, best, best_e);
                last = best;
                last_e = best_e;
            }
            sb_str(b, "]");
        }
        sb_str(b, "}");
    }
    sb_str(b, "}}");
}

// What a re-seed keeps of the cart it replaces: the kid's saves and config
// (their crash backups too), and the manifest, which the new one replaces
// last, so a cut re-seed still reads as the older version.
static int kept_name(const char *n) {
    static const char *const keep[] = { "pmem.json", "config.json", "manifest.json" };
    for (int i = 0; i < 3; i++) {
        size_t k = strlen(keep[i]);
        if (strncmp(n, keep[i], k) == 0 && (n[k] == 0 || strcmp(n + k, ".bak") == 0)) {
            return 1;
        }
    }
    return 0;
}

typedef struct {
    char **name;
    size_t *n;
    size_t len, cap;
} names_t;

static int note_name(void *ctx, const char *name, size_t n, int is_dir,
                     uint32_t size) {
    (void)is_dir;
    (void)size;
    names_t *l = ctx;
    if (kept_name(name)) {
        return 0;
    }
    if (l->len == l->cap) {
        size_t cap = l->cap ? l->cap * 2u : 16u;
        char **nm = moy_store_alloc(cap * sizeof(char *));
        size_t *nn = moy_store_alloc(cap * sizeof(size_t));
        if (nm == NULL || nn == NULL) {
            moy_store_free(nm, cap * sizeof(char *));
            moy_store_free(nn, cap * sizeof(size_t));
            return MOY_ENOMEM;
        }
        if (l->len) {
            memcpy(nm, l->name, l->len * sizeof(char *));
            memcpy(nn, l->n, l->len * sizeof(size_t));
        }
        moy_store_free(l->name, l->cap * sizeof(char *));
        moy_store_free(l->n, l->cap * sizeof(size_t));
        l->name = nm;
        l->n = nn;
        l->cap = cap;
    }
    char *c = moy_store_alloc(n + 1u);
    if (c == NULL) {
        return MOY_ENOMEM;
    }
    memcpy(c, name, n);
    l->name[l->len] = c;
    l->n[l->len++] = n;
    return 0;
}

// Everything in the folder `d` but what a re-seed keeps, removed.
static void prune(const char *d) {
    moy_vol_t v;
    const char *rest;
    names_t l = { NULL, NULL, 0, 0 };
    if (moy_vol_at(d, &v, &rest) == 0) {
        moy_vol_list(&v, rest, note_name, &l);
    }
    for (size_t i = 0; i < l.len; i++) {
        size_t pn;
        char *p = path3(d, l.name[i], NULL, &pn);
        if (p != NULL) {
            moy_cat_rmtree(p);          // a folder, or else
            moy_fs_remove(p);           // a file
            moy_store_free(p, pn);
        }
        moy_store_free(l.name[i], l.n[i] + 1u);
    }
    moy_store_free(l.name, l.cap * sizeof(char *));
    moy_store_free(l.n, l.cap * sizeof(size_t));
}

int moy_seed_write(const char *root, const char *folder, const char *json,
                   size_t n) {
    const char *c = moy_json_ws(json, json + n);
    const char *ce = moy_json_value(c, json + n, 1u);
    if (ce == NULL || *c != '{') {
        return -MOY_EINVAL;
    }
    span_t title = get(c, ce, "title"), type = get(c, ce, "type");
    span_t src = get(c, ce, "src"), cfg = get(c, ce, "cfg");
    if (title.v == NULL || type.v == NULL || src.v == NULL || cfg.v == NULL) {
        return -MOY_EINVAL;
    }
    int64_t ver = 0;
    span_t vf = get(c, ce, "version");
    if (vf.v != NULL && moy_json_int(vf.v, vf.e, &ver) != 1) {
        return -MOY_EINVAL;
    }
    span_t mainf = get(c, ce, "main");
    size_t main_n = 7;
    char *main = mainf.v != NULL ? decoded(mainf, &main_n) : NULL;
    if (mainf.v != NULL && main == NULL) {
        return -MOY_EINVAL;
    }
    size_t dn;
    char *d = path3(root, folder, NULL, &dn);
    if (d == NULL) {
        moy_store_free(main, main_n + 1u);
        return -MOY_ENOMEM;
    }
    int rc = 0;
    if (moy_fs_exists(d)) {
        int whole;
        int64_t had = cart_version(d, &whole);
        if (whole && ver <= had) {
            moy_store_free(d, dn);
            moy_store_free(main, main_n + 1u);
            return 0;
        }
        if (whole) {
            prune(d);                   // an older cart: its saves and config stay
        } else {
            moy_cat_rmtree(d);          // a seed the power cut: nothing in it is the kid's
        }
    }
    moy_fs_mkdir(d);
    rc = put_str(d, main != NULL ? main : "main.py", src);
    for (int part = 0; part < 2 && rc == 0; part++) {
        span_t l = get(c, ce, part == 0 ? "src_before" : "src_after");
        if (!truthy(l) || *l.v != '[') {
            continue;
        }
        moy_json_iter_t it, pair;
        const char *v, *ve, *nm, *nme, *tx, *txe;
        moy_json_iter(&it, l.v, l.e);
        while (rc == 0 && moy_json_next(&it, NULL, NULL, &v, &ve)) {
            if (*v != '[') {
                rc = MOY_EINVAL;
                break;
            }
            moy_json_iter(&pair, v, ve);
            if (!moy_json_next(&pair, NULL, NULL, &nm, &nme)
                || !moy_json_next(&pair, NULL, NULL, &tx, &txe)) {
                rc = MOY_EINVAL;
                break;
            }
            size_t sn;
            char *script = decoded((span_t){ nm, nme }, &sn);
            if (script == NULL) {
                rc = MOY_EINVAL;
                break;
            }
            rc = put_str(d, script, (span_t){ tx, txe });
            moy_store_free(script, sn + 1u);
        }
    }
    if (rc == 0) {
        size_t pn;
        char *p = path3(d, "config.json", NULL, &pn);
        if (p == NULL) {
            rc = MOY_ENOMEM;
        } else if (!moy_fs_exists(p)) {     // the kid's config stands
            rc = put_json(d, "config.json", cfg);
        }
        moy_store_free(p, pn);
    }
    span_t f;
    if (rc == 0 && truthy(f = get(c, ce, "sprites"))) {
        rc = put_str(d, "sprites.moygfx", f);
    }
    if (rc == 0 && truthy(f = get(c, ce, "sounds"))) {
        rc = put_json(d, "sounds.json", f);
    }
    if (rc == 0 && truthy(f = get(c, ce, "map"))) {
        rc = put_str(d, "map.moymap", f);
    }
    if (rc == 0 && truthy(f = get(c, ce, "flags"))) {
        rc = put_str(d, "flags.moyflags", f);
    }
    if (rc == 0 && truthy(f = get(c, ce, "cover"))) {
        size_t tn;
        char *t = decoded(f, &tn);
        uint8_t *bytes = t != NULL ? moy_store_alloc(tn + 1u) : NULL;
        if (bytes == NULL) {
            rc = t == NULL ? MOY_EINVAL : MOY_ENOMEM;
        } else {
            size_t bn = b64_decode(t, tn, bytes);
            rc = put_file(d, "cover.png", (const char *)bytes, bn, 1);
        }
        moy_store_free(bytes, tn + 1u);
        if (t != NULL) {
            moy_store_free(t, tn + 1u);
        }
    }
    if (rc == 0 && truthy(f = get(c, ce, "images"))) {
        rc = *f.v == '{' ? put_folder(d, "images", ".moyimg", f) : MOY_EINVAL;
    }
    if (rc == 0 && truthy(f = get(c, ce, "blocks"))) {
        rc = put_json(d, "blocks.json", f);
    }
    if (rc == 0 && truthy(f = get(c, ce, "scenes"))) {
        rc = *f.v == '{' ? put_folder(d, "scenes", ".moyscene", f) : MOY_EINVAL;
    }
    // The manifest last, and published: a folder whose manifest reads is
    // whole, and a cut before it leaves the older version's (or none), which
    // the next boot's seed writes again.
    if (rc == 0) {
        sb_t m = { NULL, 0, 0, 0 };
        size_t pn;
        char *p = path3(d, "manifest.json", NULL, &pn);
        manifest(&m, c, ce, ver, folder);
        rc = m.failed || p == NULL ? MOY_ENOMEM : moy_fs_publish(p, m.p, m.n);
        moy_store_free(p, pn);
        sb_free(&m);
    }
    moy_store_free(d, dn);
    moy_store_free(main, main_n + 1u);
    return rc ? -rc : 1;
}
