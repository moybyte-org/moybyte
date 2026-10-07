// The sync RPC's receiving half (moy_net.h): one batch's ops applied into a
// store through native/moy_store, one op at a time.
//
// A path is an allowlist of shape: forward slashes only, at most 256
// characters, no empty or dot segment, no backslash, NUL, CR or LF inside one,
// and nothing the wire's skip rule keeps home (moy_store_skip), so a batch can
// never name journal/ or a .bak. A whole file publishes through moy_fs's
// crash-safe write; a chunked one stages in `<path>.tmp` and publishes by the
// rename rotation that leaves the previous file as an unstamped `.bak`. On the
// carts root of a store of record, every text file that lands is a journal
// commit (moy_journal_append), after the file is durable: a journal that
// cannot be written costs a history entry, never the write.

#include <stdio.h>
#include <string.h>

#include "moy_fs.h"
#include "moy_journal.h"
#include "moy_json.h"
#include "moy_net.h"
#include "moy_vol.h"

#define REL_CHARS 256
#define REL_BYTES (REL_CHARS * 2)
#define SEGS (REL_CHARS / 2 + 1)
#define RMTREE_DEPTH 6
#define PATH_CAP (REL_BYTES + 128)

typedef struct {
    const moy_sync_store_t *s;
    int root;
    const char *dir;                // the root's path
    char rel[REL_BYTES + 1];
    size_t rel_n;
    uint16_t seg[SEGS];             // each segment's offset in rel
    uint16_t seg_n[SEGS];
    int nseg;
    char tree[PATH_CAP];            // rmtree's one path, grown and cut in place
    char full[PATH_CAP];            // the op's file
    char a[PATH_CAP], b[PATH_CAP];  // scratch paths: never on the VM's stack
} op_t;

static const char *const SHELF[] = {"manifest.json", "sheet.json", "cover.png"};

static const char *last_seg(const op_t *o) {
    return o->rel + o->seg[o->nseg - 1];
}

static int is_shelf(const char *name) {
    for (size_t i = 0; i < sizeof(SHELF) / sizeof(SHELF[0]); i++) {
        if (strcmp(name, SHELF[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int is_binary(const char *name) {
    return strcmp(name, "cover.png") == 0;
}

// The code points in n bytes of UTF-8, as Python's len(str) counts them.
static size_t chars(const char *s, size_t n) {
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        k += ((unsigned char)s[i] & 0xC0) != 0x80;
    }
    return k;
}

// The decoded string value of `key` in the op, or NULL when it has none or it
// is not a string. `*n` its length; the buffer is moy_store_alloc'd.
static char *str_field(const char *op, const char *op_end, const char *key,
                       size_t *n, size_t *cap) {
    const char *v, *ve;
    if (!moy_json_get(op, op_end, key, &v, &ve)
        || moy_json_kind(v, ve) != MOY_JSON_STR) {
        return NULL;
    }
    *cap = moy_json_strlen(v, ve) + 1u;
    char *out = moy_store_alloc(*cap);
    if (out == NULL) {
        return NULL;
    }
    *n = moy_json_str(v, ve, out);
    out[*n] = '\0';
    return out;
}

static int truthy(const char *op, const char *op_end, const char *key) {
    const char *v, *ve;
    return moy_json_get(op, op_end, key, &v, &ve) && moy_json_truthy(v, ve);
}

// Whether `key` is present and not null.
static int has(const char *op, const char *op_end, const char *key,
               const char **v, const char **ve) {
    return moy_json_get(op, op_end, key, v, ve)
           && moy_json_kind(*v, *ve) != MOY_JSON_NULL;
}

// The op's `part`: -1 when there is none (absent or null), 0 for the first
// (a value json.loads makes equal to 0), 1 for any other.
static int part_of(const char *op, const char *op_end) {
    const char *v, *ve;
    if (!has(op, op_end, "part", &v, &ve)) {
        return -1;
    }
    return moy_json_truthy(v, ve) ? 1 : 0;
}

static int safe_rel(op_t *o, const char *p, size_t n) {
    if (n == 0 || n > REL_BYTES || chars(p, n) > REL_CHARS) {
        return 0;
    }
    memcpy(o->rel, p, n);
    o->rel[n] = '\0';
    o->rel_n = n;
    o->nseg = 0;
    size_t a = 0;
    for (size_t i = 0; i <= n; i++) {
        if (i < n && o->rel[i] != '/') {
            char c = o->rel[i];
            if (c == '\\' || c == '\0' || c == '\r' || c == '\n') {
                return 0;
            }
            continue;
        }
        size_t k = i - a;
        if (k == 0 || (k == 1 && o->rel[a] == '.')
            || (k == 2 && o->rel[a] == '.' && o->rel[a + 1] == '.')
            || moy_store_skip(o->rel + a, k, 0)) {
            return 0;
        }
        o->seg[o->nseg] = (uint16_t)a;
        o->seg_n[o->nseg] = (uint16_t)k;
        o->nseg++;
        a = i + 1;
    }
    return 1;
}

// `<dir>/<the first k segments><suffix>` into out (NUL-terminated), or 0.
static int path_of(const op_t *o, int k, const char *suffix, char *out,
                   size_t cap) {
    size_t end = k >= o->nseg ? o->rel_n
                 : (size_t)(o->seg[k - 1] + o->seg_n[k - 1]);
    int w = snprintf(out, cap, "%s/%.*s%s", o->dir, (int)end, o->rel, suffix);
    return w > 0 && (size_t)w < cap;
}

static void mkdirs(op_t *o) {
    char *path = o->a;
    if (o->root == MOY_SYNC_FILES) {
        moy_fs_mkdir(o->dir);
    }
    for (int k = 1; k < o->nseg; k++) {
        if (path_of(o, k, "", path, PATH_CAP)) {
            moy_fs_mkdir(path);
        }
    }
}

// `src` and `suf` into dst (PATH_CAP), or 0 when they do not fit.
static int suffixed(char *dst, const char *src, const char *suf) {
    size_t n = strlen(src), k = strlen(suf);
    if (n + k + 1u > PATH_CAP) {
        return 0;
    }
    memcpy(dst, src, n);
    memcpy(dst + n, suf, k + 1u);
    return 1;
}

static int at(const char *path, moy_vol_t *v, const char **rest) {
    return moy_vol_at(path, v, rest);
}

static int append(const char *path, const void *data, size_t n) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    int rc = at(path, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_APPEND, &f);
    }
    if (rc == 0) {
        rc = moy_vol_write(f, data, n);
        int rc2 = moy_vol_close(f);
        rc = rc ? rc : rc2;
    }
    return rc;
}

static int copy(const char *src, const char *dst) {
    moy_buf_t b;
    int rc = moy_fs_read_file(src, (size_t)-1 / 2, &b);
    if (rc == 0) {
        rc = moy_fs_write(dst, b.p, b.n);
        moy_buf_free(&b);
    }
    return rc;
}

static int rename_to(const char *src, const char *dst) {
    moy_vol_t a, b;
    const char *ra, *rb;
    int rc = at(src, &a, &ra);
    if (rc == 0) {
        rc = at(dst, &b, &rb);
    }
    if (rc == 0 && (a.kind != b.kind || a.fs != b.fs)) {
        rc = MOY_EXDEV;
    }
    return rc ? rc : moy_vol_rename(&a, ra, rb);
}

// `<path>.tmp` published as `path`, the previous file rotated to `.bak`.
static int publish_tmp(op_t *o) {
    const char *path = o->full;
    char *tmp = o->a, *bak = o->b;
    if (!suffixed(tmp, path, ".tmp") || !suffixed(bak, path, ".bak")
        || !moy_fs_exists(tmp)) {
        return MOY_ENODEV;          // the caller names it: no staged tmp
    }
    if (moy_fs_exists(path)) {
        moy_fs_remove(bak);
        if (rename_to(path, bak) != 0) {
            copy(path, bak);
        }
    }
    if (rename_to(tmp, path) != 0) {
        int rc = copy(tmp, path);
        if (rc != 0) {
            return rc;
        }
        moy_fs_remove(tmp);
    }
    return 0;
}

typedef struct {
    char *names;            // each NUL-terminated, then its is_dir byte
    size_t n, cap;
    int fail;
} names_t;

static int collect(void *ctx, const char *name, size_t len, int is_dir,
                   uint32_t size) {
    (void)size;
    names_t *l = ctx;
    if (l->n + len + 2u > l->cap) {
        size_t cap = (l->cap ? l->cap * 2u : 256u) + len + 2u;
        char *p = moy_store_alloc(cap);
        if (p == NULL) {
            l->fail = 1;
            return 1;
        }
        if (l->names != NULL) {
            memcpy(p, l->names, l->n);
            moy_store_free(l->names, l->cap);
        }
        l->names = p;
        l->cap = cap;
    }
    memcpy(l->names + l->n, name, len);
    l->names[l->n + len] = '\0';
    l->names[l->n + len + 1u] = (char)(is_dir != 0);
    l->n += len + 2u;
    return 0;
}

// The tree at o->tree (`n` bytes) removed, bottom up.
static void rmtree(op_t *o, size_t n, int depth) {
    if (depth > RMTREE_DEPTH) {
        return;
    }
    moy_vol_t v;
    const char *rest;
    names_t l = {0};
    if (at(o->tree, &v, &rest) == 0 && moy_vol_list(&v, rest, collect, &l) == 0
        && !l.fail) {
        for (size_t i = 0; i < l.n;) {
            const char *name = l.names + i;
            size_t len = strlen(name);
            int dir = l.names[i + len + 1u];
            i += len + 2u;
            if (n + 1u + len >= sizeof(o->tree)) {
                continue;
            }
            o->tree[n] = '/';
            memcpy(o->tree + n + 1u, name, len + 1u);
            if (dir) {
                rmtree(o, n + 1u + len, depth + 1);
            } else {
                moy_fs_remove(o->tree);
            }
            o->tree[n] = '\0';
        }
    }
    if (l.names != NULL) {
        moy_store_free(l.names, l.cap);
    }
    moy_fs_remove(o->tree);
}

// The file just published, as the journal's commit of it.
static void journal(op_t *o, const char *text, size_t n) {
    const char *full = o->full;
    const moy_sync_store_t *s = o->s;
    if (!s->journal || o->root != MOY_SYNC_CARTS || is_binary(last_seg(o))) {
        return;
    }
    char *cart = o->b;
    if (!path_of(o, 1, "", cart, PATH_CAP)) {
        return;
    }
    moy_buf_t b = {0};
    if (text == NULL) {
        if (moy_fs_read_file(full, (size_t)-1 / 2, &b) != 0
            || !moy_utf8_valid(b.p, b.n)) {
            moy_buf_free(&b);
            return;
        }
        text = b.p;
        n = b.n;
    }
    uint32_t seq;
    const char *file = o->rel + o->seg[1];
    moy_journal_append(cart, file, text, n, -1, NULL, 0, s->ts, &seq);
    moy_buf_free(&b);
}

// base64 as binascii.a2b_base64 reads it: bytes outside the alphabet are
// passed over, the first '=' that completes a quantum ends the data, and a
// quantum left short is an error. -1, or the decoded length.
static long unb64(const char *s, size_t n, unsigned char *out) {
    uint32_t acc = 0;
    int bits = 0, q = 0;
    long k = 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        int d;
        if (c >= 'A' && c <= 'Z') {
            d = c - 'A';
        } else if (c >= 'a' && c <= 'z') {
            d = c - 'a' + 26;
        } else if (c >= '0' && c <= '9') {
            d = c - '0' + 52;
        } else if (c == '+') {
            d = 62;
        } else if (c == '/') {
            d = 63;
        } else if (c == '=') {
            if (q >= 2) {
                return k;           // the padding that ends a short quantum
            }
            continue;
        } else {
            continue;
        }
        acc = (acc << 6) | (uint32_t)d;
        bits += 6;
        q = (q + 1) & 3;
        if (bits >= 8) {
            bits -= 8;
            out[k++] = (unsigned char)(acc >> bits);
        }
    }
    return q == 0 ? k : -1;
}

static void why(char *out, const char *text) {
    snprintf(out, 40, "%s", text);
}

static void oserr(char *out, int rc) {
    snprintf(out, 40, "OSError: [Errno %d]", rc);
}

static int write_part(op_t *o, int part, const void *data, size_t n,
                      int bytes) {
    char *tmp = o->a;
    if (!suffixed(tmp, o->full, ".tmp")) {
        return MOY_EINVAL;
    }
    if (part == 0) {
        return bytes ? moy_fs_write_bytes(tmp, data, n)
               : moy_fs_write(tmp, data, n);
    }
    return append(tmp, data, n);
}

static int shelf_dirty(const op_t *o, int new_item) {
    return o->root == MOY_SYNC_CARTS && (new_item || is_shelf(last_seg(o)));
}

static int in_kinds(const char *kinds, const char *seg, size_t n) {
    for (const char *k = kinds; *k; k += strlen(k) + 1u) {
        if (strlen(k) == n && memcmp(k, seg, n) == 0) {
            return 1;
        }
    }
    return 0;
}

// One op: 0 and *shelf, or 1 with the refusal in `out`.
static int apply_one(op_t *o, const char *op, const char *op_end, int *shelf,
                     char *out) {
    *shelf = 0;
    if (moy_json_kind(op, op_end) != MOY_JSON_OBJ) {
        why(out, "not an op");
        return 1;
    }
    size_t pn = 0, pcap = 0;
    char *p = str_field(op, op_end, "p", &pn, &pcap);
    int ok = p != NULL && safe_rel(o, p, pn);
    if (p != NULL) {
        moy_store_free(p, pcap);
    }
    if (!ok) {
        why(out, "bad path");
        return 1;
    }
    int dc = truthy(op, op_end, "dc");
    if (o->root == MOY_SYNC_FILES) {
        if (o->s->kinds == NULL) {
            why(out, "no files layer");
            return 1;
        }
        if (!in_kinds(o->s->kinds, o->rel, o->seg_n[0])) {
            why(out, "not a file kind");
            return 1;
        }
        if (o->nseg < 2) {
            why(out, dc ? "dc wants an item" : "not a file");
            return 1;
        }
    }
    char *full = o->full;
    if (!path_of(o, o->nseg, ".tmp", full, PATH_CAP)
        || !path_of(o, o->nseg, "", full, PATH_CAP)) {
        why(out, "bad path");       // no room for its staging name
        return 1;
    }
    if (dc) {
        if (o->root == MOY_SYNC_CARTS ? o->nseg != 1 : o->nseg < 2) {
            why(out, "bad dc target");
            return 1;
        }
        memcpy(o->tree, full, strlen(full) + 1u);
        rmtree(o, strlen(full), 0);
        *shelf = o->root == MOY_SYNC_CARTS;
        return 0;
    }
    if (o->nseg < 2) {
        why(out, "not a store file");
        return 1;
    }
    int new_item = 0;
    if (o->root == MOY_SYNC_CARTS) {
        path_of(o, 1, "", o->a, PATH_CAP);
        new_item = !moy_fs_exists(o->a);
    }
    const char *last = last_seg(o);
    if (truthy(op, op_end, "d")) {
        moy_fs_remove(full);
        *shelf = o->root == MOY_SYNC_CARTS && is_shelf(last);
        return 0;
    }
    if (truthy(op, op_end, "pub")) {
        int rc = publish_tmp(o);
        if (rc == MOY_ENODEV) {
            snprintf(out, 40, "OSError: no staged tmp");
            return 1;
        }
        if (rc != 0) {
            oserr(out, rc);
            return 1;
        }
        journal(o, NULL, 0);
        *shelf = shelf_dirty(o, new_item);
        return 0;
    }
    int part = part_of(op, op_end);
    const char *bv, *bve;
    if (has(op, op_end, "b", &bv, &bve)) {
        if (!is_binary(last)) {
            why(out, "not a binary file");
            return 1;
        }
        if (moy_json_kind(bv, bve) != MOY_JSON_STR
            || chars(bv, (size_t)(bve - bv)) > MOY_SYNC_PART_MAX * 2u + 2u) {
            why(out, "op too large");
            return 1;
        }
        size_t tn, tcap;
        char *t = str_field(op, op_end, "b", &tn, &tcap);
        if (t == NULL) {
            oserr(out, MOY_ENOMEM);
            return 1;
        }
        unsigned char *raw = moy_store_alloc(tn / 4u * 3u + 3u);
        long rn = raw == NULL ? -2 : unb64(t, tn, raw);
        moy_store_free(t, tcap);
        if (rn < 0) {
            if (raw != NULL) {
                moy_store_free(raw, tn / 4u * 3u + 3u);
            }
            if (rn == -2) {
                oserr(out, MOY_ENOMEM);
            } else {
                why(out, "bad base64");
            }
            return 1;
        }
        mkdirs(o);
        int rc = part < 0 ? moy_fs_write_bytes(full, raw, (size_t)rn)
                 : write_part(o, part, raw, (size_t)rn, 1);
        moy_store_free(raw, tn / 4u * 3u + 3u);
        if (rc != 0) {
            oserr(out, rc);
            return 1;
        }
        *shelf = part < 0 && shelf_dirty(o, new_item);
        return 0;
    }
    size_t tn = 0, tcap = 0;
    char *t = str_field(op, op_end, "t", &tn, &tcap);
    if (t == NULL) {
        why(out, "no text");
        return 1;
    }
    int rc = 0;
    if (chars(t, tn) > MOY_SYNC_PART_MAX * 2u) {
        why(out, "op too large");
        rc = -1;
    } else {
        mkdirs(o);
        if (part < 0) {
            rc = moy_fs_publish(full, t, tn);
            if (rc == 0) {
                journal(o, t, tn);
            }
        } else {
            rc = write_part(o, part, t, tn, 0);
        }
        if (rc != 0) {
            oserr(out, rc);
        }
    }
    moy_store_free(t, tcap);
    if (rc != 0) {
        return 1;
    }
    *shelf = part < 0 && shelf_dirty(o, new_item);
    return 0;
}

void moy_sync_apply(const moy_sync_store_t *s, int root, const char *ops,
                    const char *ops_end, moy_sync_result_t *res) {
    memset(res, 0, sizeof(*res));
    op_t *o = moy_store_alloc(sizeof(op_t));   // zeroed; never the VM's stack
    if (o == NULL) {
        return;
    }
    o->s = s;
    o->root = root;
    o->dir = root == MOY_SYNC_FILES ? s->files : s->carts;
    moy_json_iter_t it;
    moy_json_iter(&it, ops, ops_end);
    const char *k, *ke, *v, *ve;
    uint32_t i = 0;
    char out[40];
    while (moy_json_next(&it, &k, &ke, &v, &ve)) {
        int shelf = 0;
        int bad = o->dir == NULL ? (why(out, "no such store"), 1)
                  : apply_one(o, v, ve, &shelf, out);
        if (bad) {
            if (res->nerr < MOY_SYNC_ERRS) {
                res->err[res->nerr].index = i;
                memcpy(res->err[res->nerr].why, out, sizeof(out));
                res->nerr++;
            }
            res->refused++;
        } else {
            res->applied++;
            res->shelf |= shelf;
        }
        i++;
    }
    moy_store_free(o, sizeof(op_t));
}
