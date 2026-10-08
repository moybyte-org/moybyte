// A compiled cart's written files (moy_files.h has the rules).

#include <stdio.h>
#include <string.h>

#include "moy_files.h"
#include "moy_vol.h"

#define PART "~part"
#define DONE "~done"
#define SUFFIX 5                        // strlen(PART), strlen(DONE)
#define FULL 512                        // the longest full path a session composes

// -- the key -------------------------------------------------------------------

static int kept(uint8_t c) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
}

static size_t encode(const uint8_t *path, size_t n, int first_out, char *out, size_t cap) {
    static const char HEX[] = "0123456789abcdef";
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        uint8_t c = path[i];
        int keep = kept(c) || (c == '.' && i > 0 && i + 1 < n);
        if (keep && !(i == 0 && first_out)) {
            if (o + 1 >= cap) {
                return 0;
            }
            out[o++] = (char)c;
        } else {
            if (o + 3 >= cap) {
                return 0;
            }
            out[o++] = '%';
            out[o++] = HEX[c >> 4];
            out[o++] = HEX[c & 15];
        }
    }
    if (o >= cap) {
        return 0;
    }
    out[o] = 0;
    return o;
}

static int device_stem(const char *k, size_t n) {
    size_t s = 0;
    while (s < n && k[s] != '.') {
        s++;
    }
    static const char *const DEV[] = { "con", "prn", "aux", "nul" };
    if (s == 3) {
        for (size_t i = 0; i < 4; i++) {
            if (memcmp(k, DEV[i], 3) == 0) {
                return 1;
            }
        }
    }
    return s == 4 && (memcmp(k, "com", 3) == 0 || memcmp(k, "lpt", 3) == 0)
           && k[3] >= '1' && k[3] <= '9';
}

size_t moy_files_key(const uint8_t *path, size_t n, char *out, size_t cap) {
    size_t k = encode(path, n, 0, out, cap);
    if (k != 0 && device_stem(out, k)) {
        k = encode(path, n, 1, out, cap);
    }
    return k;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

int moy_files_path_of(const char *name, size_t n, uint8_t *out, size_t cap) {
    size_t o = 0;
    if (n == 0) {
        return -1;
    }
    for (size_t i = 0; i < n; i++) {
        uint8_t c = (uint8_t)name[i];
        int v;
        if (c == '%') {
            int hi = i + 2 < n ? hexval(name[i + 1]) : -1;
            int lo = i + 2 < n ? hexval(name[i + 2]) : -1;
            if (hi < 0 || lo < 0) {
                return -1;
            }
            v = hi << 4 | lo;
            i += 2;
        } else if (kept(c) || c == '.') {
            v = c;
        } else {
            return -1;                  // '~', and anything no key holds
        }
        if (o >= cap) {
            return -1;
        }
        out[o++] = (uint8_t)v;
    }
    return (int)o;
}

int moy_files_folder(const char *cart_path, char *out, size_t cap) {
    size_t n = strlen(cart_path);
    while (n > 0 && cart_path[n - 1] == '/') {
        n--;
    }
    // root/folder at the last '/'; no '/' is a folder in the root "".
    size_t slash = n;
    while (slash > 0 && cart_path[slash - 1] != '/') {
        slash--;
    }
    const char *folder = cart_path + slash;
    size_t fn = n - slash;
    size_t root_n = slash > 0 ? slash - 1 : 0;
    if (fn > 4 && memcmp(folder + fn - 4, ".moy", 4) == 0) {
        fn -= 4;
    }
    // The root's parent: the root up to its own last '/', or the root itself
    // when it has none (cart_files.py's _sibling_path, rsplit's rule).
    size_t parent = root_n;
    while (parent > 0 && cart_path[parent - 1] != '/') {
        parent--;
    }
    parent = parent > 0 ? parent - 1 : root_n;
    int w = parent > 0
        ? snprintf(out, cap, "%.*s/written/%.*s", (int)parent, cart_path, (int)fn, folder)
        : snprintf(out, cap, "written/%.*s", (int)fn, folder);
    return w < 0 || (size_t)w >= cap ? -1 : 0;
}

// -- a set of byte strings, sorted bytewise ------------------------------------

typedef struct {
    uint8_t **v;                        // each: a uint16 length, then the bytes
    uint32_t n, cap;
} bset_t;

static size_t blen(const uint8_t *e) {
    return (size_t)e[0] | (size_t)e[1] << 8;
}

static int bcmp_(const uint8_t *a, size_t an, const uint8_t *b, size_t bn) {
    size_t m = an < bn ? an : bn;
    int c = memcmp(a, b, m);
    if (c != 0) {
        return c;
    }
    return an < bn ? -1 : an > bn ? 1 : 0;
}

// The first index whose entry is not below `s`.
static uint32_t bset_lower(const bset_t *s, const uint8_t *b, size_t n) {
    uint32_t lo = 0, hi = s->n;
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (bcmp_(s->v[mid] + 2, blen(s->v[mid]), b, n) < 0) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo;
}

static int bset_has(const bset_t *s, const uint8_t *b, size_t n) {
    uint32_t i = bset_lower(s, b, n);
    return i < s->n && bcmp_(s->v[i] + 2, blen(s->v[i]), b, n) == 0;
}

// 0, or -1 with no memory. An entry already there is not added again.
static int bset_add(bset_t *s, const uint8_t *b, size_t n) {
    if (n > 0xFFFFu) {
        return -1;
    }
    uint32_t i = bset_lower(s, b, n);
    if (i < s->n && bcmp_(s->v[i] + 2, blen(s->v[i]), b, n) == 0) {
        return 0;
    }
    if (s->n == s->cap) {
        uint32_t cap = s->cap ? s->cap * 2u : 16u;
        uint8_t **v = moy_store_alloc(cap * sizeof(uint8_t *));
        if (v == NULL) {
            return -1;
        }
        if (s->v != NULL) {
            memcpy(v, s->v, s->n * sizeof(uint8_t *));
            moy_store_free(s->v, s->cap * sizeof(uint8_t *));
        }
        s->v = v;
        s->cap = cap;
    }
    uint8_t *e = moy_store_alloc(n + 2);
    if (e == NULL) {
        return -1;
    }
    e[0] = (uint8_t)n;
    e[1] = (uint8_t)(n >> 8);
    memcpy(e + 2, b, n);
    memmove(s->v + i + 1, s->v + i, (s->n - i) * sizeof(uint8_t *));
    s->v[i] = e;
    s->n++;
    return 0;
}

static void bset_del(bset_t *s, const uint8_t *b, size_t n) {
    uint32_t i = bset_lower(s, b, n);
    if (i < s->n && bcmp_(s->v[i] + 2, blen(s->v[i]), b, n) == 0) {
        moy_store_free(s->v[i], blen(s->v[i]) + 2);
        memmove(s->v + i, s->v + i + 1, (s->n - i - 1) * sizeof(uint8_t *));
        s->n--;
    }
}

static void bset_free(bset_t *s) {
    for (uint32_t i = 0; i < s->n; i++) {
        moy_store_free(s->v[i], blen(s->v[i]) + 2);
    }
    if (s->v != NULL) {
        moy_store_free(s->v, s->cap * sizeof(uint8_t *));
    }
    memset(s, 0, sizeof(*s));
}

// -- the session ---------------------------------------------------------------

struct moy_files {
    char cart[FULL];
    char dir[FULL];
    bset_t written;
    bset_t shipped;                     // the cart folder's own, walked once
    bset_t names;                       // shipped and written, built on demand
    uint8_t walked, listed, made;
};

static int vol(const char *path, moy_vol_t *v, const char **rest) {
    return moy_vol_at(path, v, rest);
}

static int fs_remove(const char *path) {
    moy_vol_t v;
    const char *r;
    return vol(path, &v, &r) != 0 ? MOY_ENODEV : moy_vol_remove(&v, r);
}

static int fs_mkdir(const char *path) {
    moy_vol_t v;
    const char *r;
    return vol(path, &v, &r) != 0 ? MOY_ENODEV : moy_vol_mkdir(&v, r);
}

static int fs_is_dir(const char *path) {
    moy_vol_t v;
    const char *r;
    moy_vol_stat_t st;
    return vol(path, &v, &r) == 0 && moy_vol_stat(&v, r, &st) == 0 && st.is_dir;
}

// `src` over `dst`, as the medium allows (moy_vol_replace).
static int fs_replace(const char *src, const char *dst) {
    moy_vol_t v;
    const char *rs, *rd;
    if (vol(src, &v, &rs) != 0) {
        return MOY_ENODEV;
    }
    moy_vol_t v2;
    if (vol(dst, &v2, &rd) != 0 || v2.fs != v.fs || v2.kind != v.kind) {
        return MOY_EXDEV;
    }
    return moy_vol_replace(&v, rs, rd);
}

static int fs_rename(const char *src, const char *dst) {
    moy_vol_t v, v2;
    const char *rs, *rd;
    if (vol(src, &v, &rs) != 0 || vol(dst, &v2, &rd) != 0) {
        return MOY_ENODEV;
    }
    return moy_vol_rename(&v, rs, rd);
}

typedef struct {
    bset_t *names;                      // the entries, as their bytes
    bset_t *dirs;                       // and which of them are folders
    int bad;
} listing_t;

static int one_entry(void *ctx, const char *name, size_t len, int is_dir, uint32_t size) {
    listing_t *l = ctx;
    (void)size;
    if (bset_add(l->names, (const uint8_t *)name, len) != 0
        || (is_dir && bset_add(l->dirs, (const uint8_t *)name, len) != 0)) {
        l->bad = 1;
        return 1;
    }
    return 0;
}

// The folder's entries: 0, or an errno (the folder is absent, or no memory).
static int list_dir(const char *path, bset_t *names, bset_t *dirs) {
    moy_vol_t v;
    const char *r;
    listing_t l = { names, dirs, 0 };
    if (vol(path, &v, &r) != 0) {
        return MOY_ENODEV;
    }
    int rc = moy_vol_list(&v, r, one_entry, &l);
    return l.bad ? MOY_ENOMEM : rc;
}

static int join(char *out, size_t cap, const char *dir, const char *name, size_t n,
                const char *suffix) {
    int w = snprintf(out, cap, "%s/%.*s%s", dir, (int)n, name, suffix);
    return w < 0 || (size_t)w >= cap ? -1 : 0;
}

static int ends(const uint8_t *s, size_t n, const char *suffix) {
    return n >= SUFFIX && memcmp(s + n - SUFFIX, suffix, SUFFIX) == 0;
}

// What a power loss left, put right, and what is written, known.
static void recover(moy_files_t *f) {
    bset_t names = { 0 }, dirs = { 0 };
    char a[FULL], b[FULL];
    if (list_dir(f->dir, &names, &dirs) != 0) {
        bset_free(&names);
        bset_free(&dirs);
        return;
    }
    f->made = 1;
    for (uint32_t i = 0; i < names.n; i++) {
        const uint8_t *e = names.v[i] + 2;
        size_t n = blen(names.v[i]);
        if (ends(e, n, DONE)) {
            if (join(a, sizeof(a), f->dir, (const char *)e, n, "") == 0
                && join(b, sizeof(b), f->dir, (const char *)e, n - SUFFIX, "") == 0) {
                fs_replace(a, b);
            }
        } else if (ends(e, n, PART)) {
            if (join(a, sizeof(a), f->dir, (const char *)e, n, "") == 0) {
                fs_remove(a);
            }
        }
    }
    bset_free(&names);
    bset_free(&dirs);
    if (list_dir(f->dir, &names, &dirs) == 0) {
        uint8_t p[MOY_FILES_PATH];
        for (uint32_t i = 0; i < names.n; i++) {
            int pn = moy_files_path_of((const char *)names.v[i] + 2, blen(names.v[i]), p,
                                       sizeof(p));
            if (pn >= 0) {
                bset_add(&f->written, p, (size_t)pn);
            }
        }
    }
    bset_free(&names);
    bset_free(&dirs);
}

moy_files_t *moy_files_open(const char *cart_path) {
    moy_files_t *f = moy_store_keep(sizeof(*f));
    if (f == NULL) {
        return NULL;
    }
    memset(f, 0, sizeof(*f));
    size_t n = strlen(cart_path);
    while (n > 0 && cart_path[n - 1] == '/') {
        n--;
    }
    if (n >= sizeof(f->cart) || moy_files_folder(cart_path, f->dir, sizeof(f->dir)) != 0) {
        moy_store_free(f, sizeof(*f));
        return NULL;
    }
    memcpy(f->cart, cart_path, n);
    f->cart[n] = 0;
    recover(f);
    return f;
}

void moy_files_close(moy_files_t *f) {
    if (f == NULL) {
        return;
    }
    bset_free(&f->written);
    bset_free(&f->shipped);
    bset_free(&f->names);
    moy_store_free(f, sizeof(*f));
}

int moy_files_where(moy_files_t *f, const uint8_t *path, size_t n, char *out, size_t cap) {
    char k[3 * MOY_FILES_PATH + 1];
    if (!bset_has(&f->written, path, n) || moy_files_key(path, n, k, sizeof(k)) == 0) {
        return 0;
    }
    int w = snprintf(out, cap, "%s/%s", f->dir, k);
    return w < 0 || (size_t)w >= cap ? 0 : 1;
}

// The folder made, with its two parents: 0, or MOY_ENOSPC when it is not there
// after (cart_files.py raises ENOSPC for the same).
static int ensure(moy_files_t *f) {
    if (f->made) {
        return 0;
    }
    char root[FULL], parent[FULL];
    snprintf(root, sizeof(root), "%s", f->dir);
    char *s = strrchr(root, '/');
    if (s != NULL) {
        *s = 0;
    }
    snprintf(parent, sizeof(parent), "%s", root);
    s = strrchr(parent, '/');
    if (s != NULL) {
        *s = 0;
    }
    fs_mkdir(parent);
    fs_mkdir(root);
    fs_mkdir(f->dir);
    if (!fs_is_dir(f->dir)) {
        return MOY_ENOSPC;
    }
    f->made = 1;
    return 0;
}

static int32_t answer(int err) {
    return err == MOY_ENOSPC ? MOY_FILES_NO_ROOM : MOY_FILES_FAILED;
}

int32_t moy_files_write(moy_files_t *f, const uint8_t *path, size_t n,
                        const uint8_t *data, uint32_t len) {
    char k[3 * MOY_FILES_PATH + 1], base[FULL], part[FULL], done[FULL];
    size_t kn = moy_files_key(path, n, k, sizeof(k));
    if (kn == 0 || join(base, sizeof(base), f->dir, k, kn, "") != 0
        || join(part, sizeof(part), f->dir, k, kn, PART) != 0
        || join(done, sizeof(done), f->dir, k, kn, DONE) != 0) {
        return MOY_FILES_FAILED;
    }
    int rc = ensure(f);
    if (rc != 0) {
        return answer(rc);
    }
    moy_vol_t v;
    const char *r;
    moy_vol_file_t *fh = NULL;
    rc = vol(part, &v, &r);
    if (rc == 0) {
        rc = moy_vol_open(&v, r, MOY_VOL_WRITE, &fh);
    }
    if (rc == 0) {
        int wr = len ? moy_vol_write(fh, data, len) : 0;
        int cl = moy_vol_close(fh);
        rc = wr != 0 ? wr : cl;
    }
    if (rc != 0) {
        fs_remove(part);
        return answer(rc);
    }
    fs_remove(done);
    rc = fs_rename(part, done);
    if (rc == 0) {
        rc = fs_replace(done, base);
    }
    if (rc != 0) {
        return answer(rc);
    }
    if (!bset_has(&f->written, path, n)) {
        if (bset_add(&f->written, path, n) != 0) {
            return MOY_FILES_FAILED;
        }
        f->listed = 0;
    }
    return 0;
}

int32_t moy_files_erase(moy_files_t *f, const uint8_t *path, size_t n) {
    char k[3 * MOY_FILES_PATH + 1], base[FULL];
    if (!bset_has(&f->written, path, n)) {
        return -1;
    }
    size_t kn = moy_files_key(path, n, k, sizeof(k));
    if (kn == 0 || join(base, sizeof(base), f->dir, k, kn, "") != 0
        || fs_remove(base) != 0) {
        return -1;
    }
    bset_del(&f->written, path, n);
    f->listed = 0;
    return 0;
}

// The cart folder's files, `rel` the bytes before each name, MOY_FILES_DEPTH
// folders deep at most.
static void walk(moy_files_t *f, const char *folder, const uint8_t *rel, size_t rn,
                 int depth) {
    bset_t names = { 0 }, dirs = { 0 };
    if (list_dir(folder, &names, &dirs) != 0) {
        bset_free(&names);
        bset_free(&dirs);
        return;
    }
    for (uint32_t i = 0; i < names.n; i++) {
        const uint8_t *e = names.v[i] + 2;
        size_t n = blen(names.v[i]);
        uint8_t r[FULL];
        if (rn + n + 1 > sizeof(r)) {
            continue;
        }
        memcpy(r, rel, rn);
        memcpy(r + rn, e, n);
        if (bset_has(&dirs, e, n)) {
            char sub[FULL];
            if (depth < MOY_FILES_DEPTH
                && join(sub, sizeof(sub), folder, (const char *)e, n, "") == 0) {
                r[rn + n] = '/';
                walk(f, sub, r, rn + n + 1, depth + 1);
            }
        } else {
            bset_add(&f->shipped, r, rn + n);
        }
    }
    bset_free(&names);
    bset_free(&dirs);
}

int32_t moy_files_name(moy_files_t *f, const uint8_t *prefix, size_t pn, uint32_t index,
                       uint8_t *dst, uint32_t cap) {
    if (!f->walked) {
        walk(f, f->cart, (const uint8_t *)"", 0, 0);
        f->walked = 1;
    }
    if (!f->listed) {
        bset_free(&f->names);
        for (uint32_t i = 0; i < f->shipped.n; i++) {
            bset_add(&f->names, f->shipped.v[i] + 2, blen(f->shipped.v[i]));
        }
        for (uint32_t i = 0; i < f->written.n; i++) {
            bset_add(&f->names, f->written.v[i] + 2, blen(f->written.v[i]));
        }
        f->listed = 1;
    }
    uint32_t i = bset_lower(&f->names, prefix, pn);
    if (i + index < i || i + index >= f->names.n) {
        return -1;
    }
    const uint8_t *e = f->names.v[i + index] + 2;
    size_t n = blen(f->names.v[i + index]);
    if (n < pn || memcmp(e, prefix, pn) != 0) {
        return -1;
    }
    memcpy(dst, e, n < cap ? n : cap);
    return (int32_t)n;
}
