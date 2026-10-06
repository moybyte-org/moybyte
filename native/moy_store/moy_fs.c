// The store's crash-safe write (moy_fs.h has the contract; runtime/moy_fs.py's
// header has the story every branch below follows).

#include <stdio.h>
#include <string.h>

#include "moy_fs.h"

#define STAMP "#moyfs1 "
#define STAMP_LEN 8u
#define PUBLISH "/.publish"
#define HEAD_MAX 1100u      // the longest marker or stamp line read

enum { MARK_UNREAD = 0, MARK_NONE = 1, MARK_SET = 2 };

typedef struct {
    char *root;             // keep-allocated, len + 1
    size_t len;
    uint8_t state;
    char *mpath;            // the marker's path while MARK_SET
    size_t mlen;
    uint32_t chars, crc;
} root_t;

static root_t *roots;       // MOY_FS_ROOTS entries, longest root first
static int nroots;

// -- small things --------------------------------------------------------------

static const uint32_t crc_nib[16] = {
    0x00000000u, 0x1db71064u, 0x3b6e20c8u, 0x26d930acu, 0x76dc4190u,
    0x6b6b51f4u, 0x4db26158u, 0x5005713cu, 0xedb88320u, 0xf00f9344u,
    0xd6d6a3e8u, 0xcb61b38cu, 0x9b64c2b0u, 0x86d3d2d4u, 0xa00ae278u,
    0xbdbdf21cu,
};

uint32_t moy_fs_crc32(uint32_t crc, const void *p, size_t n) {
    const uint8_t *b = p;
    crc = ~crc;
    while (n--) {
        crc ^= *b++;
        crc = (crc >> 4) ^ crc_nib[crc & 15u];
        crc = (crc >> 4) ^ crc_nib[crc & 15u];
    }
    return ~crc;
}

void moy_fs_stamp(const char *data, size_t n, uint32_t *chars, uint32_t *crc) {
    uint32_t c = 0;
    for (size_t i = 0; i < n; i++) {
        c += ((uint8_t)data[i] & 0xc0u) != 0x80u;
    }
    *chars = c;
    *crc = moy_fs_crc32(0, data, n);
}

static int fits(const char *p, size_t n, uint32_t chars, uint32_t crc) {
    uint32_t c, k;
    moy_fs_stamp(p, n, &c, &k);
    return c == chars && k == crc;
}

void moy_buf_free(moy_buf_t *b) {
    if (b->p != NULL) {
        moy_store_free(b->p, b->n + 1u);
    }
    b->p = NULL;
    b->n = 0;
}

// `a` and `b` joined, scratch: NULL when there is no memory.
static char *join(const char *a, size_t an, const char *b, size_t *out_n) {
    size_t bn = strlen(b), n = an + bn + 1u;
    char *s = moy_store_alloc(n);
    if (s != NULL) {
        memcpy(s, a, an);
        memcpy(s + an, b, bn);
        *out_n = n;
    }
    return s;
}

// A decimal number that fits 32 bits, the whole of [p, p + n).
static int number(const char *p, size_t n, uint32_t *out) {
    uint64_t v = 0;
    if (n == 0 || n > 10) {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (p[i] < '0' || p[i] > '9') {
            return 0;
        }
        v = v * 10u + (uint64_t)(p[i] - '0');
    }
    if (v > 0xffffffffu) {
        return 0;
    }
    *out = (uint32_t)v;
    return 1;
}

// "#moyfs1 <chars> <crc>\n" -- `line` is `n` bytes, the newline included.
static int parse_stamp(const char *line, size_t n, uint32_t *chars,
                       uint32_t *crc) {
    if (n < STAMP_LEN + 1u || memcmp(line, STAMP, STAMP_LEN) != 0
        || line[n - 1u] != '\n') {
        return 0;
    }
    const char *p = line + STAMP_LEN, *end = line + n - 1u;
    const char *sp = memchr(p, ' ', (size_t)(end - p));
    if (sp == NULL || memchr(sp + 1, ' ', (size_t)(end - sp - 1)) != NULL) {
        return 0;
    }
    return number(p, (size_t)(sp - p), chars)
           && number(sp + 1, (size_t)(end - sp - 1), crc);
}

// "#moyfs1 <chars> <crc> <path>\n": the path is the rest, never empty.
static int parse_mark(const char *line, size_t n, uint32_t *chars,
                      uint32_t *crc, const char **path, size_t *plen) {
    if (n < STAMP_LEN + 1u || memcmp(line, STAMP, STAMP_LEN) != 0
        || line[n - 1u] != '\n') {
        return 0;
    }
    const char *p = line + STAMP_LEN, *end = line + n - 1u;
    const char *s1 = memchr(p, ' ', (size_t)(end - p));
    if (s1 == NULL) {
        return 0;
    }
    const char *s2 = memchr(s1 + 1, ' ', (size_t)(end - s1 - 1));
    if (s2 == NULL || s2 + 1 == end) {
        return 0;
    }
    if (!number(p, (size_t)(s1 - p), chars)
        || !number(s1 + 1, (size_t)(s2 - s1 - 1), crc)) {
        return 0;
    }
    *path = s2 + 1;
    *plen = (size_t)(end - s2 - 1);
    return 1;
}

static int resolve(const char *path, moy_vol_t *v, const char **rest) {
    return moy_vol_at(path, v, rest);
}

// -- the plain verbs --------------------------------------------------------------

int moy_fs_read_file(const char *path, size_t cap, moy_buf_t *out) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    out->p = NULL;
    out->n = 0;
    int rc = resolve(path, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    if (rc != 0) {
        return rc;
    }
    uint32_t size = 0;
    rc = moy_vol_size(f, &size);
    if (rc == 0 && size > cap) {
        rc = MOY_EFBIG;
    }
    size_t want = size, have = 0;
    char *buf = NULL;
    while (rc == 0) {
        char probe = 0;
        if (buf != NULL && have == want) {
            size_t got = 0;         // as long as it said, unless it grew since
            rc = moy_vol_read(f, &probe, 1, &got);
            if (rc != 0 || got == 0) {
                break;
            }
            if (want >= cap) {
                rc = MOY_EFBIG;
                break;
            }
        }
        if (buf == NULL || have == want) {
            size_t grow = buf == NULL ? want : want + 4096u;
            if (buf != NULL && grow < want + 1u) {
                grow = want + 1u;
            }
            if (grow > cap) {
                grow = cap;
            }
            char *nb = moy_store_alloc(grow + 1u);
            if (nb == NULL) {
                rc = MOY_ENOMEM;
                break;
            }
            if (buf != NULL) {
                memcpy(nb, buf, have);
                moy_store_free(buf, want + 1u);
                nb[have++] = probe;
            }
            buf = nb;
            want = grow;
            if (have == want) {
                continue;
            }
        }
        size_t got = 0;
        rc = moy_vol_read(f, buf + have, want - have, &got);
        if (rc != 0 || got == 0) {
            break;
        }
        have += got;
    }
    int crc_ = moy_vol_close(f);
    if (rc == 0) {
        rc = crc_;
    }
    if (rc != 0) {
        if (buf != NULL) {
            moy_store_free(buf, want + 1u);
        }
        return rc;
    }
    if (buf == NULL) {
        buf = moy_store_alloc(1);
        want = 0;
        if (buf == NULL) {
            return MOY_ENOMEM;
        }
    }
    if (have != want) {             // shrink to what was read, so free sizes agree
        char *nb = moy_store_alloc(have + 1u);
        if (nb == NULL) {
            moy_store_free(buf, want + 1u);
            return MOY_ENOMEM;
        }
        memcpy(nb, buf, have);
        moy_store_free(buf, want + 1u);
        buf = nb;
    }
    buf[have] = 0;
    out->p = buf;
    out->n = have;
    return 0;
}

// The file's first line, newline included, or all of a file with none; at
// most HEAD_MAX bytes into `line`.
static int read_head(const char *path, char *line, size_t *n) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    *n = 0;
    int rc = resolve(path, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    if (rc != 0) {
        return rc;
    }
    size_t have = 0;
    while (have < HEAD_MAX) {
        size_t got = 0;
        rc = moy_vol_read(f, line + have, HEAD_MAX - have, &got);
        if (rc != 0 || got == 0) {
            break;
        }
        const char *nl = memchr(line + have, '\n', got);
        have += got;
        if (nl != NULL) {
            have = (size_t)(nl - line) + 1u;
            break;
        }
    }
    moy_vol_close(f);
    *n = have;
    return rc;
}

static int write_parts(const char *path, const void *a, size_t an,
                       const void *b, size_t bn) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    int rc = resolve(path, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_WRITE, &f);
    }
    if (rc != 0) {
        return rc;
    }
    if (an) {
        rc = moy_vol_write(f, a, an);
    }
    if (rc == 0 && bn) {
        rc = moy_vol_write(f, b, bn);
    }
    int crc_ = moy_vol_close(f);
    return rc ? rc : crc_;
}

int moy_fs_write(const char *path, const void *data, size_t n) {
    return write_parts(path, data, n, NULL, 0);
}

int moy_fs_exists(const char *path) {
    moy_vol_t v;
    const char *rest;
    moy_vol_stat_t st;
    return resolve(path, &v, &rest) == 0 && moy_vol_stat(&v, rest, &st) == 0;
}

int moy_fs_mkdir(const char *path) {
    moy_vol_t v;
    const char *rest;
    int rc = resolve(path, &v, &rest);
    return rc ? rc : moy_vol_mkdir(&v, rest);
}

int moy_fs_remove(const char *path) {
    moy_vol_t v;
    const char *rest;
    int rc = resolve(path, &v, &rest);
    return rc ? rc : moy_vol_remove(&v, rest);
}

// src and dst on one volume, or EXDEV.
static int rename2(const char *src, const char *dst, int replace) {
    moy_vol_t a, b;
    const char *ra, *rb;
    int rc = resolve(src, &a, &ra);
    if (rc == 0) {
        rc = resolve(dst, &b, &rb);
    }
    if (rc != 0) {
        return rc;
    }
    if (a.kind != b.kind || a.fs != b.fs) {
        return MOY_EXDEV;
    }
    return replace ? moy_vol_replace(&a, ra, rb) : moy_vol_rename(&a, ra, rb);
}

int moy_fs_write_bytes(const char *path, const void *data, size_t n) {
    size_t tn;
    char *tmp = join(path, strlen(path), ".tmp", &tn);
    if (tmp == NULL) {
        return MOY_ENOMEM;
    }
    int rc = moy_fs_write(tmp, data, n);
    if (rc == 0) {
        rc = rename2(tmp, path, 1);
    }
    moy_store_free(tmp, tn);
    return rc;
}

// -- the marker -----------------------------------------------------------------

static void drop_mark(root_t *r) {
    if (r->mpath != NULL) {
        moy_store_free(r->mpath, r->mlen + 1u);
    }
    r->mpath = NULL;
    r->mlen = 0;
}

static void set_mark(root_t *r, const char *path, size_t plen, uint32_t chars,
                     uint32_t crc) {
    drop_mark(r);
    r->mpath = moy_store_keep(plen + 1u);
    if (r->mpath == NULL) {
        r->state = MARK_NONE;
        return;
    }
    memcpy(r->mpath, path, plen);
    r->mlen = plen;
    r->chars = chars;
    r->crc = crc;
    r->state = MARK_SET;
}

int moy_fs_root(const char *root) {
    size_t len = strlen(root);
    while (len && root[len - 1u] == '/') {
        len--;
    }
    if (len == 0) {
        return 0;
    }
    if (roots == NULL) {
        roots = moy_store_keep(MOY_FS_ROOTS * sizeof(root_t));
        if (roots == NULL) {
            return MOY_ENOMEM;
        }
    }
    root_t r;
    int at = -1;
    for (int i = 0; i < nroots; i++) {
        if (roots[i].len == len && memcmp(roots[i].root, root, len) == 0) {
            at = i;
            break;
        }
    }
    if (at >= 0) {
        r = roots[at];
        memmove(&roots[at], &roots[at + 1], (size_t)(nroots - at - 1) * sizeof(root_t));
        nroots--;
    } else {
        memset(&r, 0, sizeof r);
        r.root = moy_store_keep(len + 1u);
        if (r.root == NULL) {
            return MOY_ENOMEM;
        }
        memcpy(r.root, root, len);
        r.len = len;
    }
    // The newest goes first, then a stable sort by length, longest first: an
    // insertion at the first root no longer than it.
    int k = 0;
    while (k < nroots && roots[k].len > len) {
        k++;
    }
    if (nroots == MOY_FS_ROOTS) {           // the shortest falls off
        root_t *last = &roots[nroots - 1];
        if (k == nroots) {
            drop_mark(&r);
            moy_store_free(r.root, r.len + 1u);
            return 0;
        }
        drop_mark(last);
        moy_store_free(last->root, last->len + 1u);
        nroots--;
    }
    memmove(&roots[k + 1], &roots[k], (size_t)(nroots - k) * sizeof(root_t));
    roots[k] = r;
    nroots++;
    return 0;
}

void moy_fs_roots_clear(void) {
    for (int i = 0; i < nroots; i++) {
        drop_mark(&roots[i]);
        moy_store_free(roots[i].root, roots[i].len + 1u);
    }
    nroots = 0;
    if (roots != NULL) {
        moy_store_free(roots, MOY_FS_ROOTS * sizeof(root_t));
        roots = NULL;
    }
}

static root_t *root_for(const char *path) {
    size_t n = strlen(path);
    for (int i = 0; i < nroots; i++) {
        root_t *r = &roots[i];
        if (n > r->len && path[r->len] == '/' && memcmp(path, r->root, r->len) == 0) {
            return r;
        }
    }
    return NULL;
}

static void load_mark(root_t *r) {
    if (r->state != MARK_UNREAD) {
        return;
    }
    r->state = MARK_NONE;
    size_t pn;
    char *p = join(r->root, r->len, PUBLISH, &pn);
    char *line = moy_store_alloc(HEAD_MAX);
    size_t n;
    if (p != NULL && line != NULL && read_head(p, line, &n) == 0) {
        uint32_t chars, crc;
        const char *mp;
        size_t ml;
        if (parse_mark(line, n, &chars, &crc, &mp, &ml)) {
            set_mark(r, mp, ml, chars, crc);
        }
    }
    if (line != NULL) {
        moy_store_free(line, HEAD_MAX);
    }
    if (p != NULL) {
        moy_store_free(p, pn);
    }
}

void moy_fs_unmark(const char *path) {
    root_t *r = root_for(path);
    if (r != NULL) {
        drop_mark(r);
        r->state = MARK_NONE;
    }
}

int moy_fs_bak_stamp(const char *path, uint32_t *chars, uint32_t *crc) {
    size_t bn, n;
    char *bak = join(path, strlen(path), ".bak", &bn);
    char *line = moy_store_alloc(HEAD_MAX);
    int ok = 0;
    if (bak != NULL && line != NULL && read_head(bak, line, &n) == 0) {
        ok = parse_stamp(line, n, chars, crc);
    }
    if (line != NULL) {
        moy_store_free(line, HEAD_MAX);
    }
    if (bak != NULL) {
        moy_store_free(bak, bn);
    }
    return ok;
}

// What the published `path` should look like: 1 and its stamp, or 0.
static int stamp_for(const char *path, uint32_t *chars, uint32_t *crc) {
    root_t *r = root_for(path);
    if (r == NULL) {
        return moy_fs_bak_stamp(path, chars, crc);
    }
    load_mark(r);
    if (r->state != MARK_SET || r->mlen != strlen(path)
        || memcmp(r->mpath, path, r->mlen) != 0) {
        return 0;
    }
    *chars = r->chars;
    *crc = r->crc;
    return 1;
}

// -- the crash-safe write and its readers ---------------------------------------------

int moy_fs_read_stamped(const char *path, moy_buf_t *out) {
    moy_buf_t b;
    if (moy_fs_read_file(path, (size_t)-1, &b) != 0) {
        return MOY_FS_NONE;
    }
    const char *nl = memchr(b.p, '\n', b.n);
    size_t hn = nl ? (size_t)(nl - b.p) + 1u : b.n;
    uint32_t chars, crc;
    if (!parse_stamp(b.p, hn, &chars, &crc)) {
        if (b.n == 0) {             // made, and the power went before its stamp
            moy_buf_free(&b);
            return MOY_FS_NONE;
        }
        *out = b;                   // legacy: unstamped, whole
        return 0;
    }
    if (!fits(b.p + hn, b.n - hn, chars, crc)) {
        moy_buf_free(&b);
        return MOY_FS_NONE;         // torn: refused, never published
    }
    moy_buf_t r = { moy_store_alloc(b.n - hn + 1u), b.n - hn };
    if (r.p == NULL) {
        moy_buf_free(&b);
        return MOY_ENOMEM;
    }
    memcpy(r.p, b.p + hn, r.n);
    moy_buf_free(&b);
    *out = r;
    return 0;
}

static int read_bak(const char *path, moy_buf_t *out) {
    size_t bn;
    char *bak = join(path, strlen(path), ".bak", &bn);
    if (bak == NULL) {
        return MOY_ENOMEM;
    }
    int rc = moy_fs_read_stamped(bak, out);
    moy_store_free(bak, bn);
    return rc;
}

void moy_fs_forget_bak(const char *path) {
    size_t bn;
    char *bak = join(path, strlen(path), ".bak", &bn);
    if (bak != NULL) {
        moy_fs_remove(bak);
        moy_store_free(bak, bn);
    }
}

int moy_fs_publish(const char *path, const char *data, size_t n) {
    uint32_t chars, crc;
    moy_fs_stamp(data, n, &chars, &crc);
    size_t plen = strlen(path);
    root_t *r = root_for(path);
    if (r != NULL) {
        size_t mn = r->len + sizeof(PUBLISH);
        char *mark = moy_store_alloc(mn);
        char *line = moy_store_alloc(plen + 32u);
        int rc = MOY_ENOMEM;
        if (mark != NULL && line != NULL) {
            memcpy(mark, r->root, r->len);
            memcpy(mark + r->len, PUBLISH, sizeof(PUBLISH));
            int ln = snprintf(line, plen + 32u, STAMP "%lu %lu ",
                              (unsigned long)chars, (unsigned long)crc);
            memcpy(line + ln, path, plen);
            line[ln + (int)plen] = '\n';
            rc = moy_fs_write(mark, line, (size_t)ln + plen + 1u);
        }
        if (line != NULL) {
            moy_store_free(line, plen + 32u);
        }
        if (mark != NULL) {
            moy_store_free(mark, mn);
        }
        if (rc == 0) {
            set_mark(r, path, plen, chars, crc);
        } else {
            drop_mark(r);           // this save publishes without detection
            r->state = MARK_NONE;
        }
    }
    size_t bn;
    char *bak = join(path, plen, ".bak", &bn);
    if (bak == NULL) {
        return MOY_ENOMEM;
    }
    char head[32];
    int hn = snprintf(head, sizeof head, STAMP "%lu %lu\n",
                      (unsigned long)chars, (unsigned long)crc);
    int rc = write_parts(bak, head, (size_t)hn, data, n);
    if (rc != 0) {
        moy_fs_remove(bak);         // free the half-written backup
        moy_store_free(bak, bn);
        return rc;
    }
    moy_store_free(bak, bn);
    return moy_fs_write(path, data, n);
}

static void heal(const char *path, const moy_buf_t *b) {
    moy_fs_write(path, b->p, b->n);     // best effort
}

int moy_fs_read(const char *path, const char *at, moy_buf_t *out) {
    int rc = moy_fs_read_file(at != NULL ? at : path, (size_t)-1, out);
    moy_buf_t rec;
    if (rc != 0) {
        if (read_bak(path, &rec) != 0) {
            return rc;              // the original error
        }
        heal(path, &rec);
        *out = rec;
        return 0;
    }
    uint32_t chars, crc;
    if (!stamp_for(path, &chars, &crc) || fits(out->p, out->n, chars, crc)) {
        return 0;
    }
    if (read_bak(path, &rec) != 0) {
        moy_fs_unmark(path);        // a torn backup: keep the file we can read
        return 0;
    }
    if (rec.n >= out->n && memcmp(rec.p, out->p, out->n) == 0) {
        moy_buf_free(out);          // a truncated publish: finish it
        heal(path, &rec);
        *out = rec;
        return 0;
    }
    moy_buf_free(&rec);             // someone else published here
    moy_fs_forget_bak(path);
    moy_fs_unmark(path);
    return 0;
}

// Whether `a` and `b` name entries of one folder.
static int same_folder(const char *a, const char *b) {
    const char *sa = strrchr(a, '/'), *sb = strrchr(b, '/');
    size_t na = sa ? (size_t)(sa - a) : 0, nb = sb ? (size_t)(sb - b) : 0;
    return na == nb && memcmp(a, b, na) == 0;
}

int moy_fs_claim(const char *path, const char *dest, uint32_t chars,
                 uint32_t crc) {
    moy_vol_t v;
    const char *rest;
    // littlefs (2.11) cut between the two commits of a rename across folders
    // loses the source folder's other entries -- the live file beside the
    // backup -- so there the claim declines and the caller writes its copy.
    if (resolve(path, &v, &rest) != 0
        || (v.kind == MOY_VOL_KIND_LFS2 && !same_folder(path, dest))) {
        return 0;
    }
    uint32_t c, k;
    if (!moy_fs_bak_stamp(path, &c, &k) || c != chars || k != crc) {
        return 0;
    }
    size_t bn;
    char *bak = join(path, strlen(path), ".bak", &bn);
    if (bak == NULL) {
        return 0;
    }
    int rc = rename2(bak, dest, 0);
    moy_store_free(bak, bn);
    return rc == 0;
}
