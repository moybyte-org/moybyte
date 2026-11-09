// A cart as it travels (moy_pack.h has the contract).

#include <string.h>

#include "moy_arena.h"
#include "moy_cat.h"
#include "moy_pack.h"

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
// What an archive's file passes through, either way: a member of any size
// costs CHUNK (and, inflating, the deflate window), never its own size.
#define CHUNK 4096u
#define WINDOW 32768u

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
    uint8_t *buf;               // CHUNK bytes: every file passes through it
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

// The file at `path` read a CHUNK at a time into `buf`, at most `cap` bytes:
// each piece handed to the crc (`crc` given) or to the archive (`z` given).
// `*n` gets the bytes read.
static int stream_file(const char *path, uint8_t *buf, uint32_t cap, uint32_t *crc,
                       zw_t *z, uint32_t *n) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    *n = 0;
    int rc = moy_vol_at(path, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    if (rc != 0) {
        return rc;
    }
    while (rc == 0 && *n < cap) {
        size_t got = 0;
        size_t want = cap - *n < CHUNK ? cap - *n : CHUNK;
        rc = moy_vol_read(f, buf, want, &got);
        if (rc != 0 || got == 0) {
            break;
        }
        if (crc != NULL) {
            *crc = moy_fs_crc32(*crc, buf, got);
        } else {
            put(z, buf, got);
            rc = z->rc;
        }
        *n += (uint32_t)got;
    }
    int rc2 = moy_vol_close(f);
    return rc ? rc : rc2;
}

// One file into the archive, read twice a CHUNK at a time -- its crc, then
// its bytes -- so a file of any size costs the archive one CHUNK.
static int add_file(zw_t *z, const char *path, const char *rel) {
    uint32_t crc = 0, size = 0;
    if (stream_file(path, z->buf, (uint32_t)-1, &crc, NULL, &size) != 0) {
        return 0;                       // an unreadable file: left out
    }
    if (z->len == z->cap) {
        size_t cap = z->cap ? z->cap * 2u : 16u;
        zent_t *e = moy_arena_alloc(&z->a, cap * sizeof(zent_t));
        if (e == NULL) {
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
    e->crc = crc;
    e->size = size;
    e->offset = z->at;
    if (e->name == NULL) {
        return MOY_ENOMEM;
    }
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
    uint32_t copied = 0;
    int rc = z->rc ? z->rc : stream_file(path, z->buf, size, NULL, z, &copied);
    if (rc == 0 && copied != size) {
        rc = MOY_EIO;                   // the file shrank between the passes
    }
    return rc ? rc : z->rc;
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
    z.buf = moy_store_alloc(CHUNK);
    rc = z.buf == NULL ? MOY_ENOMEM : walk(&z, cart, folder, 0);
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
    if (z.buf != NULL) {
        moy_store_free(z.buf, CHUNK);
    }
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

// One member's bytes: `left` of them still in the archive `f`, read a
// CHUNK at a time into `in`.
typedef struct {
    moy_vol_file_t *f;
    uint8_t *in;
    uint32_t left;
    int rc;
    #if MOY_PACK_INFLATE
    uzlib_uncomp_t *d;
    #endif
} zr_t;

static size_t refill(zr_t *r) {
    size_t got = 0;
    size_t want = r->left < CHUNK ? r->left : CHUNK;
    if (want && r->rc == 0) {
        r->rc = moy_vol_read(r->f, r->in, want, &got);
    }
    r->left -= (uint32_t)got;
    return got;
}

#if MOY_PACK_INFLATE
static int next_byte(void *ctx) {
    zr_t *r = ctx;
    size_t got = refill(r);
    if (got == 0) {
        return -1;
    }
    r->d->source = r->in + 1;
    r->d->source_limit = r->in + got;
    return r->in[0];
}
#endif

// The member `r` reads written to `out`: stored, copied a CHUNK at a time;
// deflated, inflated through the WINDOW into `buf` a CHUNK at a time. 0 once
// all `usize` bytes are written, MOY_EINVAL for a member that does not
// inflate to them.
static int member_to(zr_t *r, uint32_t method, uint32_t usize, uint8_t *buf,
                     uint8_t *window, moy_vol_file_t *out) {
    uint32_t wrote = 0;
    int rc = 0;
    if (method == 0) {
        size_t got;
        while (rc == 0 && (got = refill(r)) != 0) {
            rc = moy_vol_write(out, r->in, got);
            wrote += (uint32_t)got;
        }
        rc = rc ? rc : r->rc;
        return rc ? rc : (wrote == usize ? 0 : MOY_EINVAL);
    }
    #if MOY_PACK_INFLATE
    uzlib_uncompress_init(r->d, window, WINDOW);
    r->d->source = NULL;
    r->d->source_limit = NULL;
    r->d->source_read_cb = next_byte;
    r->d->source_read_data = r;
    int st = usize ? UZLIB_OK : UZLIB_DONE;
    while (rc == 0 && st == UZLIB_OK && wrote < usize) {
        r->d->dest_start = r->d->dest = buf;
        r->d->dest_limit = buf + (usize - wrote < CHUNK ? usize - wrote : CHUNK);
        st = uzlib_uncompress(r->d);
        size_t n = (size_t)(r->d->dest - buf);
        if (n) {
            rc = moy_vol_write(out, buf, n);
            wrote += (uint32_t)n;
        }
    }
    rc = rc ? rc : r->rc;
    return rc ? rc : (st >= 0 && wrote == usize ? 0 : MOY_EINVAL);
    #else
    (void)buf;
    (void)window;
    (void)usize;
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

// `n` bytes of the archive `f` at `at` into `buf`.
static int read_at(moy_vol_file_t *f, uint32_t at, uint8_t *buf, size_t n) {
    size_t got = 0;
    int rc = moy_vol_seek(f, at);
    if (rc == 0) {
        rc = moy_vol_read(f, buf, n, &got);
    }
    return rc ? rc : (got == n ? 0 : MOY_EINVAL);
}

// The archive is read by its central directory, held whole (it names the
// members, a few dozen bytes each), and each member streamed to its file:
// an archive of any size costs its directory, two CHUNKs and the WINDOW.
int moy_unpack(const char *archive, const char *dest, char *top, size_t cap) {
    if (cap) {
        top[0] = 0;
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    int rc = moy_vol_at(archive, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    if (rc != 0) {
        return -rc;
    }
    moy_arena_t a;
    memset(&a, 0, sizeof a);
    uint32_t zn = 0;
    rc = moy_vol_size(f, &zn);
    // the end record, in the archive's last 64 KiB
    uint32_t tail = zn < 66022u ? zn : 66022u;
    uint8_t *t = rc == 0 && tail >= 22u ? moy_arena_alloc(&a, tail) : NULL;
    long eocd = -1;
    if (t != NULL && read_at(f, zn - tail, t, tail) == 0) {
        for (long i = (long)tail - 22; i >= 0; i--) {
            if (rd32(t + i) == 0x06054b50u) {
                eocd = i;
                break;
            }
        }
    }
    uint32_t count = 0, at = 0, cn = 0;
    uint8_t *z = NULL;
    if (eocd >= 0) {
        count = rd16(t + eocd + 10);
        cn = rd32(t + eocd + 12);
        at = rd32(t + eocd + 16);
        if ((uint64_t)at + cn <= zn) {
            z = moy_arena_alloc(&a, cn ? cn : 1u);
        }
    }
    if (z == NULL || read_at(f, at, z, cn) != 0) {
        moy_vol_close(f);
        moy_arena_free(&a);
        return -MOY_EINVAL;
    }
    // the one top folder every entry sits under, if there is one
    const char *tp = NULL;
    size_t tn = 0;
    int has_top = 1;
    uint32_t p = 0;
    for (uint32_t i = 0; i < count && has_top; i++) {
        if ((size_t)p + 46u > cn || rd32(z + p) != 0x02014b50u) {
            break;
        }
        uint32_t nl = rd16(z + p + 28), xl = rd16(z + p + 30), cl = rd16(z + p + 32);
        if ((size_t)p + 46u + nl > cn) {
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
    zr_t r;
    memset(&r, 0, sizeof r);
    r.f = f;
    r.in = moy_arena_alloc(&a, CHUNK);
    uint8_t *buf = moy_arena_alloc(&a, CHUNK);
    uint8_t *window = NULL;
    rc = r.in == NULL || buf == NULL ? MOY_ENOMEM : 0;
    moy_fs_mkdir(dest);
    int files = 0;
    p = 0;
    for (uint32_t i = 0; i < count && rc == 0; i++) {
        if ((size_t)p + 46u > cn || rd32(z + p) != 0x02014b50u) {
            break;
        }
        uint32_t method = rd16(z + p + 10), csize = rd32(z + p + 20), usize = rd32(z + p + 24);
        uint32_t nl = rd16(z + p + 28), xl = rd16(z + p + 30), cl = rd16(z + p + 32);
        uint32_t local = rd32(z + p + 42);
        if ((size_t)p + 46u + nl > cn) {
            break;
        }
        const char *nm = (const char *)z + p + 46;
        p += 46u + nl + xl + cl;
        if (nl == 0 || nm[nl - 1u] == '/') {
            continue;                   // a folder entry
        }
        const char *rel = tn ? nm + tn + 1u : nm;
        size_t rn = tn ? nl - tn - 1u : nl;
        uint8_t h[30];
        if (!safe(rel, rn) || (uint64_t)local + 30u > zn || read_at(f, local, h, 30) != 0
            || rd32(h) != 0x04034b50u) {
            continue;
        }
        uint64_t start = (uint64_t)local + 30u + rd16(h + 26) + rd16(h + 28);
        if (start + csize > zn || (method != 0 && method != 8)) {
            continue;
        }
        #if MOY_PACK_INFLATE
        if (method == 8 && window == NULL) {
            window = moy_arena_alloc(&a, WINDOW);
            r.d = moy_arena_alloc(&a, sizeof(uzlib_uncomp_t));
            if (window == NULL || r.d == NULL) {
                rc = MOY_ENOMEM;
                break;
            }
        }
        #endif
        char *rl = moy_arena_dup(&a, rel, rn);
        char *path = rl != NULL ? moy_arena_join(&a, dest, rl) : NULL;
        if (path == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        make_dirs(&a, dest, rl);
        moy_vol_t ov;
        const char *orest;
        moy_vol_file_t *out;
        rc = moy_vol_at(path, &ov, &orest);
        if (rc == 0) {
            rc = moy_vol_open(&ov, orest, MOY_VOL_WRITE, &out);
        }
        if (rc != 0) {
            break;
        }
        r.left = csize;
        r.rc = moy_vol_seek(f, (uint32_t)start);
        rc = member_to(&r, method, usize, buf, window, out);
        int rc2 = moy_vol_close(out);
        rc = rc ? rc : rc2;
        if (rc == MOY_EINVAL || rc == MOY_ENODEV) {
            moy_fs_remove(path);        // a damaged or unreadable member: left out
            rc = 0;
            continue;
        }
        files += rc == 0;
    }
    moy_vol_close(f);
    moy_arena_free(&a);
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
