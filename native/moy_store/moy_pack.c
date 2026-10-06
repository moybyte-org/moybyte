// A cart as it travels (moy_pack.h has the contract; moy_store.mjs's zipStore
// and unzip are the codec this mirrors).

#include <string.h>

#include "moy_arena.h"
#include "moy_cat.h"
#include "moy_pack.h"
#include "moy_seed.h"

#ifdef MOY_STORE_MICROPYTHON
#include "py/mpconfig.h"
#define MOY_PACK_INFLATE MICROPY_PY_DEFLATE
#else
#define MOY_PACK_INFLATE 1
#endif
#if MOY_PACK_INFLATE
#include "lib/uzlib/uzlib.h"
#endif

#define DEPTH 6

int moy_store_skip(const char *name, size_t n, int history) {
    static const char *const dirs[] = { "thumbs", "__pycache__", "journal", "journal.jsonl" };
    for (int i = 0; i < 4; i++) {
        if ((i < 2 || !history) && strlen(dirs[i]) == n && memcmp(name, dirs[i], n) == 0) {
            return 1;
        }
    }
    return n >= 4 && (memcmp(name + n - 4, ".bak", 4) == 0
                      || memcmp(name + n - 4, ".tmp", 4) == 0);
}

// -- writing -----------------------------------------------------------------------

typedef struct {
    char *name;
    size_t n;
    uint32_t crc, size, offset;
} zent_t;

typedef struct {
    moy_arena_t a;
    moy_vol_file_t *out;
    uint32_t at;
    zent_t *e;
    size_t len, cap;
    int history;
    int rc;
} zw_t;

static void le16(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void le32(uint8_t *p, uint32_t v) {
    le16(p, v);
    le16(p + 2, v >> 16);
}

static void put(zw_t *z, const void *p, size_t n) {
    if (z->rc == 0) {
        z->rc = moy_vol_write(z->out, p, n);
        z->at += (uint32_t)n;
    }
}

typedef struct {
    moy_arena_t *a;
    char **name;
    uint8_t *dir;
    size_t len, cap;
} ls_t;

static int ls_add(void *ctx, const char *name, size_t n, int is_dir, uint32_t size) {
    (void)size;
    ls_t *l = ctx;
    if (l->len == l->cap) {
        size_t cap = l->cap ? l->cap * 2u : 16u;
        char **nm = moy_arena_alloc(l->a, cap * sizeof(char *));
        uint8_t *d = moy_arena_alloc(l->a, cap);
        if (nm == NULL || d == NULL) {
            return MOY_ENOMEM;
        }
        if (l->len) {
            memcpy(nm, l->name, l->len * sizeof(char *));
            memcpy(d, l->dir, l->len);
        }
        l->name = nm;
        l->dir = d;
        l->cap = cap;
    }
    l->name[l->len] = moy_arena_dup(l->a, name, n);
    l->dir[l->len] = (uint8_t)(is_dir != 0);
    return l->name[l->len++] == NULL ? MOY_ENOMEM : 0;
}

static void sort(ls_t *l) {
    for (size_t i = 1; i < l->len; i++) {
        char *p = l->name[i];
        uint8_t d = l->dir[i];
        size_t j = i;
        while (j > 0 && strcmp(l->name[j - 1u], p) > 0) {
            l->name[j] = l->name[j - 1u];
            l->dir[j] = l->dir[j - 1u];
            j--;
        }
        l->name[j] = p;
        l->dir[j] = d;
    }
}

static int add_file(zw_t *z, const char *path, const char *rel) {
    moy_buf_t b;
    int rc = moy_fs_read_file(path, (size_t)-1, &b);
    if (rc != 0) {
        return 0;                       // an unreadable file: left out
    }
    if (z->len == z->cap) {
        size_t cap = z->cap ? z->cap * 2u : 16u;
        zent_t *e = moy_arena_alloc(&z->a, cap * sizeof(zent_t));
        if (e == NULL) {
            moy_buf_free(&b);
            return MOY_ENOMEM;
        }
        if (z->len) {
            memcpy(e, z->e, z->len * sizeof(zent_t));
        }
        z->e = e;
        z->cap = cap;
    }
    zent_t *e = &z->e[z->len++];
    e->n = strlen(rel);
    e->name = moy_arena_dup(&z->a, rel, e->n);
    e->crc = moy_fs_crc32(0, b.p, b.n);
    e->size = (uint32_t)b.n;
    e->offset = z->at;
    uint8_t h[30];
    memset(h, 0, sizeof h);
    le32(h, 0x04034b50u);
    le16(h + 4, 20);
    le16(h + 12, 0x21);
    le32(h + 14, e->crc);
    le32(h + 18, e->size);
    le32(h + 22, e->size);
    le16(h + 26, (uint32_t)e->n);
    put(z, h, sizeof h);
    put(z, rel, e->n);
    put(z, b.p, b.n);
    moy_buf_free(&b);
    return e->name == NULL ? MOY_ENOMEM : z->rc;
}

static int walk(zw_t *z, const char *path, const char *rel, int depth) {
    if (depth > DEPTH) {
        return 0;
    }
    moy_vol_t v;
    const char *rest;
    ls_t l = { &z->a, NULL, NULL, 0, 0 };
    if (moy_vol_at(path, &v, &rest) != 0 || moy_vol_list(&v, rest, ls_add, &l) != 0) {
        return 0;
    }
    sort(&l);
    for (size_t i = 0; i < l.len; i++) {
        const char *nm = l.name[i];
        if (moy_store_skip(nm, strlen(nm), z->history)) {
            continue;
        }
        char *p = moy_arena_join(&z->a, path, nm), *r = moy_arena_join(&z->a, rel, nm);
        if (p == NULL || r == NULL) {
            return MOY_ENOMEM;
        }
        int rc = l.dir[i] ? walk(z, p, r, depth + 1) : add_file(z, p, r);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

int moy_pack(const char *cart, const char *folder, const char *dest, int history) {
    zw_t z;
    memset(&z, 0, sizeof z);
    z.history = history;
    moy_vol_t v;
    const char *rest;
    int rc = moy_vol_at(dest, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_WRITE, &z.out);
    }
    if (rc != 0) {
        return -rc;
    }
    rc = walk(&z, cart, folder, 0);
    uint32_t cen = z.at;
    for (size_t i = 0; rc == 0 && i < z.len; i++) {
        zent_t *e = &z.e[i];
        uint8_t h[46];
        memset(h, 0, sizeof h);
        le32(h, 0x02014b50u);
        le16(h + 4, 20);
        le16(h + 6, 20);
        le16(h + 14, 0x21);
        le32(h + 16, e->crc);
        le32(h + 20, e->size);
        le32(h + 24, e->size);
        le16(h + 28, (uint32_t)e->n);
        le32(h + 42, e->offset);
        put(&z, h, sizeof h);
        put(&z, e->name, e->n);
        rc = z.rc;
    }
    if (rc == 0) {
        uint8_t end[22];
        memset(end, 0, sizeof end);
        le32(end, 0x06054b50u);
        le16(end + 8, (uint32_t)z.len);
        le16(end + 10, (uint32_t)z.len);
        le32(end + 12, z.at - cen);
        le32(end + 16, cen);
        put(&z, end, sizeof end);
        rc = z.rc;
    }
    int rc2 = moy_vol_close(z.out);
    if (rc == 0) {
        rc = rc2;
    }
    int files = (int)z.len;
    moy_arena_free(&z.a);
    if (rc != 0) {
        moy_fs_remove(dest);
        return -rc;
    }
    return files;
}

// -- reading -----------------------------------------------------------------------

static uint32_t rd16(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8);
}

static uint32_t rd32(const uint8_t *p) {
    return rd16(p) | (rd16(p + 2) << 16);
}

// moy_sync.safe_segments's rule: no empty, "." or ".." segment, no absolute
// path, nothing the wire skips.
static int safe(const char *p, size_t n) {
    if (n == 0 || p[0] == '/' || p[0] == '\\') {
        return 0;
    }
    size_t s = 0;
    for (size_t i = 0; i <= n; i++) {
        if (i == n || p[i] == '/' || p[i] == '\\') {
            size_t k = i - s;
            if (k == 0 || (k == 1 && p[s] == '.') || (k == 2 && p[s] == '.' && p[s + 1] == '.')
                || moy_store_skip(p + s, k, 0)) {
                return 0;
            }
            s = i + 1;
        }
    }
    return 1;
}

static int inflate_to(const uint8_t *src, size_t n, uint8_t *dst, size_t want) {
    #if MOY_PACK_INFLATE
    uzlib_uncomp_t *d = moy_store_alloc(sizeof(uzlib_uncomp_t));
    if (d == NULL) {
        return MOY_ENOMEM;
    }
    uzlib_uncompress_init(d, NULL, 0);
    d->source = src;
    d->source_limit = src + n;
    d->source_read_cb = NULL;
    d->dest_start = d->dest = dst;
    d->dest_limit = dst + want;
    int st = want ? uzlib_uncompress(d) : UZLIB_DONE;
    int ok = (st == UZLIB_DONE || st == UZLIB_OK) && d->dest == dst + want;
    moy_store_free(d, sizeof(uzlib_uncomp_t));
    return ok ? 0 : MOY_EINVAL;
    #else
    (void)src;
    (void)n;
    (void)dst;
    (void)want;
    return MOY_ENODEV;
    #endif
}

// Every folder above `path`'s last segment made under `dest`.
static void make_dirs(moy_arena_t *a, const char *dest, const char *rel) {
    size_t n = strlen(rel);
    for (size_t i = 0; i < n; i++) {
        if (rel[i] == '/') {
            char *sub = moy_arena_dup(a, rel, i);
            char *p = sub != NULL ? moy_arena_join(a, dest, sub) : NULL;
            if (p != NULL) {
                moy_fs_mkdir(p);
            }
        }
    }
}

int moy_unpack(const char *archive, const char *dest, char *top, size_t cap) {
    moy_buf_t b;
    if (cap) {
        top[0] = 0;
    }
    int rc = moy_fs_read_file(archive, (size_t)-1, &b);
    if (rc != 0) {
        return -rc;
    }
    const uint8_t *z = (const uint8_t *)b.p;
    size_t zn = b.n;
    long eocd = -1;
    for (long i = (long)zn - 22; i >= 0 && i >= (long)zn - 66000; i--) {
        if (rd32(z + i) == 0x06054b50u) {
            eocd = i;
            break;
        }
    }
    if (eocd < 0) {
        moy_buf_free(&b);
        return -MOY_EINVAL;
    }
    moy_arena_t a;
    memset(&a, 0, sizeof a);
    uint32_t count = rd16(z + eocd + 10), at = rd32(z + eocd + 16);
    // the one top folder every entry sits under, if there is one
    const char *tp = NULL;
    size_t tn = 0;
    int has_top = 1;
    uint32_t p = at;
    for (uint32_t i = 0; i < count && has_top; i++) {
        if ((size_t)p + 46u > zn || rd32(z + p) != 0x02014b50u) {
            break;
        }
        uint32_t nl = rd16(z + p + 28), xl = rd16(z + p + 30), cl = rd16(z + p + 32);
        if ((size_t)p + 46u + nl > zn) {
            break;
        }
        const char *nm = (const char *)z + p + 46;
        const char *sl = memchr(nm, '/', nl);
        if (sl == NULL) {
            has_top = 0;
        } else if (tp == NULL) {
            tp = nm;
            tn = (size_t)(sl - nm);
        } else if ((size_t)(sl - nm) != tn || memcmp(nm, tp, tn) != 0) {
            has_top = 0;
        }
        p += 46u + nl + xl + cl;
    }
    if (!has_top || tp == NULL) {
        tn = 0;
    } else if (cap) {
        size_t k = tn < cap - 1u ? tn : cap - 1u;
        memcpy(top, tp, k);
        top[k] = 0;
    }
    moy_fs_mkdir(dest);
    int files = 0;
    rc = 0;
    p = at;
    for (uint32_t i = 0; i < count && rc == 0; i++) {
        if ((size_t)p + 46u > zn || rd32(z + p) != 0x02014b50u) {
            break;
        }
        uint32_t method = rd16(z + p + 10), csize = rd32(z + p + 20), usize = rd32(z + p + 24);
        uint32_t nl = rd16(z + p + 28), xl = rd16(z + p + 30), cl = rd16(z + p + 32);
        uint32_t local = rd32(z + p + 42);
        if ((size_t)p + 46u + nl > zn) {
            break;
        }
        const char *nm = (const char *)z + p + 46;
        p += 46u + nl + xl + cl;
        if (nl == 0 || nm[nl - 1u] == '/') {
            continue;                   // a folder entry
        }
        const char *rel = tn ? nm + tn + 1u : nm;
        size_t rn = tn ? nl - tn - 1u : nl;
        if (!safe(rel, rn) || (size_t)local + 30u > zn || rd32(z + local) != 0x04034b50u) {
            continue;
        }
        size_t start = (size_t)local + 30u + rd16(z + local + 26) + rd16(z + local + 28);
        if (start + csize > zn || (method != 0 && method != 8) || usize > MOY_SEED_MAX) {
            continue;
        }
        char *r = moy_arena_dup(&a, rel, rn);
        char *path = r != NULL ? moy_arena_join(&a, dest, r) : NULL;
        if (path == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        make_dirs(&a, dest, r);
        if (method == 0) {
            rc = moy_fs_write(path, z + start, csize);
        } else {
            uint8_t *out = moy_store_alloc(usize + 1u);
            if (out == NULL) {
                rc = MOY_ENOMEM;
                break;
            }
            rc = inflate_to(z + start, csize, out, usize);
            if (rc == 0) {
                rc = moy_fs_write(path, out, usize);
            } else {
                rc = 0;                 // a damaged entry: left out
                moy_store_free(out, usize + 1u);
                continue;
            }
            moy_store_free(out, usize + 1u);
        }
        files += rc == 0;
    }
    moy_arena_free(&a);
    moy_buf_free(&b);
    return rc ? -rc : files;
}

// -- adopt ---------------------------------------------------------------------------

int moy_adopt(const char *stage, const char *target) {
    moy_arena_t a;
    memset(&a, 0, sizeof a);
    size_t sn = strlen(stage);
    char *aside = moy_arena_alloc(&a, sn + 5u);
    if (aside == NULL) {
        return MOY_ENOMEM;
    }
    memcpy(aside, stage, sn);
    memcpy(aside + sn, ".old", 5);
    moy_vol_t v;
    const char *rs, *rt, *ra;
    int rc = moy_vol_at(stage, &v, &rs);
    moy_vol_t vt;
    if (rc == 0) {
        rc = moy_vol_at(target, &vt, &rt);
    }
    if (rc == 0 && (vt.kind != v.kind || vt.fs != v.fs)) {
        rc = MOY_EXDEV;
    }
    if (rc == 0 && moy_fs_exists(target)) {
        if (moy_fs_exists(aside)) {
            moy_cat_rmtree(aside);
        }
        rc = moy_vol_at(aside, &v, &ra);
        if (rc == 0) {
            rc = moy_vol_rename(&v, rt, ra);
        }
        if (rc == 0) {
            rc = moy_vol_rename(&v, rs, rt);
        }
        if (rc == 0) {
            moy_cat_rmtree(aside);
        }
    } else if (rc == 0) {
        rc = moy_vol_rename(&v, rs, rt);
    }
    moy_arena_free(&a);
    return rc;
}
