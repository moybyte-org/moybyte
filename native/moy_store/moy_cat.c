// The catalogue (moy_cat.h has the contract; runtime/moy_carts.py's `_load`
// and `_each` are the reference every branch below follows).

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_arena.h"
#include "moy_cat.h"
#include "moy_json.h"
#include "moy_load.h"

#define SHEET "sprites.moygfx"
#define SCENES "scenes"
#define SCENE_EXT ".moyscene"
#define MANIFEST "manifest.json"

// -- an entry's memory: moy_arena ---------------------------------------------------

typedef moy_arena_t arena_t;
#define aalloc moy_arena_alloc
#define adup moy_arena_dup
#define ajoin moy_arena_join
#define afree moy_arena_free

// -- a folder's listing ---------------------------------------------------------------

typedef struct {
    const char *name;
    size_t n;
    uint8_t dir;
    uint32_t size;
} ent_t;

typedef struct {
    arena_t *a;
    ent_t *e;
    size_t len, cap;
} lst_t;

static int lst_add(void *ctx, const char *name, size_t n, int is_dir,
                   uint32_t size) {
    lst_t *l = ctx;
    if (l->len == l->cap) {
        size_t cap = l->cap ? l->cap * 2u : 16u;
        ent_t *e = aalloc(l->a, cap * sizeof(ent_t));
        if (e == NULL) {
            return MOY_ENOMEM;
        }
        if (l->len) {
            memcpy(e, l->e, l->len * sizeof(ent_t));
        }
        l->e = e;
        l->cap = cap;
    }
    const char *d = adup(l->a, name, n);
    if (d == NULL) {
        return MOY_ENOMEM;
    }
    l->e[l->len++] = (ent_t){ d, n, (uint8_t)(is_dir != 0), size };
    return 0;
}

static int ascii_lower(int c) {
    return c >= 'A' && c <= 'Z' ? c + 32 : c;
}

static const ent_t *lst_exact(const lst_t *l, const char *name, size_t n) {
    for (size_t i = 0; i < l->len; i++) {
        if (l->e[i].n == n && memcmp(l->e[i].name, name, n) == 0) {
            return &l->e[i];
        }
    }
    return NULL;
}

static int lst_fold(const lst_t *l, const char *name, size_t n) {
    for (size_t i = 0; i < l->len; i++) {
        if (l->e[i].n != n) {
            continue;
        }
        size_t k = 0;
        while (k < n && ascii_lower((unsigned char)l->e[i].name[k])
                        == ascii_lower((unsigned char)name[k])) {
            k++;
        }
        if (k == n) {
            return 1;
        }
    }
    return 0;
}

int moy_cat_plain(const char *name, size_t n) {
    if (n == 0 || name[0] == '.' || name[0] == ' ' || name[n - 1u] == '.'
        || name[n - 1u] == ' ') {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
              || (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.')) {
            return 0;
        }
    }
    return 1;
}

// `_absent`: the listing proves `name` is not in its folder.
static int absent(const lst_t *l, const char *name) {
    size_t n = strlen(name);
    if (l == NULL || lst_exact(l, name, n) != NULL || lst_fold(l, name, n)) {
        return 0;
    }
    return moy_cat_plain(name, n);
}

// -- reading a folder -------------------------------------------------------------

typedef struct {
    arena_t a;
    moy_vol_here_t *here;       // NULL: every read is by full path
    const char *path;           // the folder, absolute
    const lst_t *lst;           // NULL when the folder would not list
} rd_t;

// `name` in the folder: its path to open, and the volume it is on.
static int at(rd_t *r, const char *name, moy_vol_t *v, const char **rest) {
    if (r->here != NULL) {
        *v = r->here->v;
        *rest = moy_vol_here_name(r->here, name);
        return *rest == NULL ? MOY_ENOMEM : 0;
    }
    char *full = ajoin(&r->a, r->path, name);
    if (full == NULL) {
        return MOY_ENOMEM;
    }
    return moy_vol_at(full, v, rest);
}

// `_has`: the listing's answer, or a lookup when it cannot say.
static int has(rd_t *r, const char *name) {
    if (r->lst != NULL && lst_exact(r->lst, name, strlen(name)) != NULL) {
        return 1;
    }
    if (absent(r->lst, name)) {
        return 0;
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_stat_t st;
    return at(r, name, &v, &rest) == 0 && moy_vol_stat(&v, rest, &st) == 0;
}

static const char *errno_name(int e) {
    switch (e) {
        case 1: return "EPERM";
        case 2: return "ENOENT";
        case 5: return "EIO";
        case 9: return "EBADF";
        case 12: return "ENOMEM";
        case 13: return "EACCES";
        case 17: return "EEXIST";
        case 19: return "ENODEV";
        case 21: return "EISDIR";
        case 22: return "EINVAL";
        case 28: return "ENOSPC";
        case 30: return "EROFS";
        default: return NULL;
    }
}

// The title a folder's own name gives a cart whose manifest cannot say.
static size_t project_title(const char *path, const char **name) {
    const char *s = strrchr(path, '/'), *b = strrchr(path, '\\');
    if (b != NULL && (s == NULL || b > s)) {
        s = b;
    }
    const char *nm = s != NULL ? s + 1 : path;
    size_t n = strlen(nm);
    if (n >= 4u && memcmp(nm + n - 4u, MOY_CAT_EXT, 4u) == 0) {
        *name = nm;
        return n - 4u;
    }
    if (n == 0) {
        *name = "cart";
        return 4u;
    }
    *name = nm;
    return n;
}

static int valid_utf8(const char *p, size_t n) {
    const unsigned char *s = (const unsigned char *)p, *e = s + n;
    while (s < e) {
        unsigned c = *s;
        size_t k;
        uint32_t cp;
        if (c < 0x80u) {
            s++;
            continue;
        } else if ((c & 0xe0u) == 0xc0u) {
            k = 2;
            cp = c & 0x1fu;
        } else if ((c & 0xf0u) == 0xe0u) {
            k = 3;
            cp = c & 0x0fu;
        } else if ((c & 0xf8u) == 0xf0u) {
            k = 4;
            cp = c & 0x07u;
        } else {
            return 0;
        }
        if ((size_t)(e - s) < k) {
            return 0;
        }
        for (size_t i = 1; i < k; i++) {
            if ((s[i] & 0xc0u) != 0x80u) {
                return 0;
            }
            cp = (cp << 6) | (s[i] & 0x3fu);
        }
        if ((k == 2 && cp < 0x80u) || (k == 3 && cp < 0x800u)
            || (k == 4 && (cp < 0x10000u || cp > 0x10ffffu))
            || (cp >= 0xd800u && cp <= 0xdfffu)) {
            return 0;
        }
        s += k;
    }
    return 1;
}

static moy_cat_json_t field(const char *man, const char *end, const char *key) {
    moy_cat_json_t f = { NULL, NULL };
    if (!moy_json_get(man, end, key, &f.v, &f.e)) {
        f.v = f.e = NULL;
    }
    return f;
}

// A field of the manifest's "moybyte" object, where Moybyte's own fields
// live (moy_store_base.VENDOR_KEY); absent when there is no such object.
static moy_cat_json_t vfield(const char *man, const char *end, const char *key) {
    moy_cat_json_t v = field(man, end, "moybyte");
    if (v.v == NULL || *v.v != '{') {
        moy_cat_json_t none = { NULL, NULL };
        return none;
    }
    return field(v.v, v.e, key);
}

// A cart id as SPEC.md 3.1 spells it (moy_store_base.is_cart_id): two parts
// of a-z, 0-9 and _ joined by one dot.
static int is_cart_id(const char *s, size_t n) {
    size_t dot = 0, dots = 0;
    for (size_t i = 0; i < n; i++) {
        char ch = s[i];
        if (ch == '.') {
            dot = i;
            dots++;
        } else if (!((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_')) {
            return 0;
        }
    }
    return dots == 1u && dot > 0u && dot + 1u < n;
}

static int is_str(moy_cat_json_t f, const char *s) {
    return f.v != NULL && *f.v == '"' && moy_json_str_is(f.v, f.e, s, strlen(s));
}

// A string field decoded into the arena; NULL when it is not a string.
static char *str_of(arena_t *a, moy_cat_json_t f, size_t *n) {
    if (f.v == NULL || *f.v != '"') {
        return NULL;
    }
    size_t k = moy_json_strlen(f.v, f.e);
    char *d = aalloc(a, k + 1u);
    if (d != NULL) {
        *n = moy_json_str(f.v, f.e, d);
        d[*n] = 0;
    }
    return d;
}

// _normalize_icon: (tile, w, h), or 0.
static int norm_icon(moy_cat_json_t f, int64_t out[3]) {
    if (f.v == NULL) {
        return 0;
    }
    int k = moy_json_kind(f.v, f.e);
    if (k == MOY_JSON_INT) {
        int64_t n;
        if (moy_json_int(f.v, f.e, &n) != 1 || n < 0) {
            return 0;
        }
        out[0] = n;
        out[1] = out[2] = 1;
        return 1;
    }
    if (k != MOY_JSON_ARR) {
        return 0;
    }
    moy_json_iter_t it;
    const char *v, *ve;
    int64_t x[3] = { 0, 1, 1 };
    int i = 0;
    moy_json_iter(&it, f.v, f.e);
    while (moy_json_next(&it, NULL, NULL, &v, &ve)) {
        if (i == 3) {
            return 0;
        }
        if (moy_json_int(v, ve, &x[i]) != 1) {
            return 0;
        }
        i++;
    }
    if (i == 0 || x[0] < 0 || x[1] < 1 || x[1] > 4 || x[2] < 1 || x[2] > 4) {
        return 0;
    }
    memcpy(out, x, sizeof x);
    return 1;
}

static const char *const input_kinds[MOY_CAT_INPUT_KINDS] = {
    "buttons", "touch", "keyboard",
};

static const struct {
    const char *name;
    uint16_t w, h;
} canvases[] = { { "320x240", 320, 240 }, { "160x120", 160, 120 },
                 { "128x128", 128, 128 } };

static int canvas_of(uint16_t w, uint16_t h) {
    for (size_t i = 0; i < sizeof canvases / sizeof canvases[0]; i++) {
        if (canvases[i].w == w && canvases[i].h == h) {
            return 1;
        }
    }
    return 0;
}

static void norm_canvas(moy_cat_entry_t *e, moy_cat_json_t f) {
    e->canvas = 0;
    e->canvas_raw = f;
    if (f.v == NULL || *f.v == 'n') {
        return;
    }
    e->canvas = 2;
    if (*f.v == '"') {
        for (size_t i = 0; i < sizeof canvases / sizeof canvases[0]; i++) {
            if (moy_json_str_is(f.v, f.e, canvases[i].name, strlen(canvases[i].name))) {
                e->canvas = 1;
                e->canvas_w = canvases[i].w;
                e->canvas_h = canvases[i].h;
            }
        }
    } else if (*f.v == '{') {
        moy_cat_json_t w = field(f.v, f.e, "width"), h = field(f.v, f.e, "height");
        int64_t iw, ih;
        if (w.v != NULL && h.v != NULL && moy_json_int(w.v, w.e, &iw) == 1
            && moy_json_int(h.v, h.e, &ih) == 1 && iw >= 0 && ih >= 0
            && iw < 65536 && ih < 65536 && canvas_of((uint16_t)iw, (uint16_t)ih)) {
            e->canvas = 1;
            e->canvas_w = (uint16_t)iw;
            e->canvas_h = (uint16_t)ih;
        }
    }
}

// -- the scene names and the icon rows ---------------------------------------------------

typedef struct {
    arena_t *a;
    const char **name;
    size_t *n;
    size_t len, cap;
} names_t;

static int names_add(names_t *s, const char *p, size_t n) {
    if (s->len == s->cap) {
        size_t cap = s->cap ? s->cap * 2u : 8u;
        const char **nm = aalloc(s->a, cap * sizeof(char *));
        size_t *nn = aalloc(s->a, cap * sizeof(size_t));
        if (nm == NULL || nn == NULL) {
            return MOY_ENOMEM;
        }
        if (s->len) {
            memcpy(nm, s->name, s->len * sizeof(char *));
            memcpy(nn, s->n, s->len * sizeof(size_t));
        }
        s->name = nm;
        s->n = nn;
        s->cap = cap;
    }
    s->name[s->len] = p;
    s->n[s->len++] = n;
    return 0;
}

static int scene_ent(void *ctx, const char *name, size_t n, int is_dir,
                     uint32_t size) {
    (void)is_dir;
    (void)size;
    names_t *s = ctx;
    size_t xn = sizeof(SCENE_EXT) - 1u;
    if (n < xn || memcmp(name + n - xn, SCENE_EXT, xn) != 0) {
        return 0;
    }
    char *d = adup(s->a, name, n - xn);
    return d == NULL ? MOY_ENOMEM : names_add(s, d, n - xn);
}

static int bytes_cmp(const char *a, size_t an, const char *b, size_t bn) {
    int c = memcmp(a, b, an < bn ? an : bn);
    return c ? c : (an < bn ? -1 : an > bn);
}

static void sort_names(const char **nm, size_t *nn, size_t len) {
    for (size_t i = 1; i < len; i++) {
        const char *p = nm[i];
        size_t n = nn[i], j = i;
        while (j > 0 && bytes_cmp(nm[j - 1u], nn[j - 1u], p, n) > 0) {
            nm[j] = nm[j - 1u];
            nn[j] = nn[j - 1u];
            j--;
        }
        nm[j] = p;
        nn[j] = n;
    }
}

// `_scene_files` then `scene_names`: 0, MOY_ENOMEM, or 1 when the manifest's
// order holds a value `in` cannot hash (the twin raises, and drops the cart).
static int scene_names(rd_t *r, const char *man, const char *mend,
                       moy_cat_entry_t *e, const names_t *given) {
    names_t blobs = { &r->a, NULL, NULL, 0, 0 };
    const ent_t *sd = r->lst != NULL ? lst_exact(r->lst, SCENES, sizeof SCENES - 1u)
                                     : NULL;
    if (given != NULL) {
        blobs = *given;
    } else if (!(r->lst != NULL && ((sd != NULL && !sd->dir) || absent(r->lst, SCENES)))) {
        moy_vol_t v;
        const char *rest;
        if (at(r, SCENES, &v, &rest) == 0) {
            int rc = moy_vol_list(&v, rest, scene_ent, &blobs);
            if (rc == MOY_ENOMEM) {
                return rc;
            }
            if (rc != 0) {
                blobs.len = 0;
            }
        }
    }
    sort_names(blobs.name, blobs.n, blobs.len);
    uint8_t *taken = aalloc(&r->a, blobs.len + 1u);
    names_t out = { &r->a, NULL, NULL, 0, 0 };
    if (taken == NULL) {
        return MOY_ENOMEM;
    }
    moy_cat_json_t assets = vfield(man, mend, "assets");
    if (assets.v != NULL && *assets.v == '{') {
        moy_cat_json_t listed = field(assets.v, assets.e, "scenes");
        if (listed.v != NULL && *listed.v == '[') {
            moy_json_iter_t it;
            const char *v, *ve;
            moy_json_iter(&it, listed.v, listed.e);
            while (moy_json_next(&it, NULL, NULL, &v, &ve)) {
                if (*v == '[' || *v == '{') {
                    return 1;
                }
                if (*v != '"') {
                    continue;
                }
                for (size_t i = 0; i < blobs.len; i++) {
                    if (!taken[i] && moy_json_str_is(v, ve, blobs.name[i], blobs.n[i])) {
                        taken[i] = 1;
                        if (names_add(&out, blobs.name[i], blobs.n[i]) != 0) {
                            return MOY_ENOMEM;
                        }
                        break;
                    }
                }
            }
        }
    }
    for (size_t i = 0; i < blobs.len; i++) {
        if (!taken[i] && names_add(&out, blobs.name[i], blobs.n[i]) != 0) {
            return MOY_ENOMEM;
        }
    }
    e->scenes_n = out.len;
    e->scenes = out.name;
    e->scene_n = out.n;
    return 0;
}

static int py_space(unsigned char c) {
    return c == ' ' || (c >= 0x09 && c <= 0x0d) || (c >= 0x1c && c <= 0x1f);
}

static int ink(unsigned char c) {
    return (c >= '1' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

typedef struct {
    int64_t n, tw, th;
    int ox, oy, pw, ph, w, h;
    int y, inked, done;
    const char **want;
    size_t *want_n;
} rows_t;

// One line of the sheet (stripped here); 1 when the icon is in hand.
static int sheet_line(arena_t *a, rows_t *s, const char *p, size_t n) {
    while (n && py_space((unsigned char)*p)) {
        p++;
        n--;
    }
    while (n && py_space((unsigned char)p[n - 1u])) {
        n--;
    }
    if (n == 0) {
        return 0;
    }
    if (s->y >= s->h) {
        return 1;
    }
    if (!s->inked) {
        size_t lim = n < (size_t)s->w ? n : (size_t)s->w;
        for (size_t i = 0; i < lim; i++) {
            if (ink((unsigned char)p[i])) {
                s->inked = 1;
                break;
            }
        }
    }
    if (s->y >= s->oy && s->y < s->oy + s->ph) {
        size_t from = (size_t)s->ox < n ? (size_t)s->ox : n;
        size_t to = (size_t)(s->ox + s->pw) < n ? (size_t)(s->ox + s->pw) : n;
        char *d = adup(a, p + from, to - from);
        if (d == NULL) {
            return -1;
        }
        s->want[s->y - s->oy] = d;
        s->want_n[s->y - s->oy] = to - from;
    }
    s->y++;
    return s->inked && s->y >= s->oy + s->ph;
}

// `_sheet_icon`: the icon's rows off the top of the sheet.
static int sheet_icon(rd_t *r, moy_cat_entry_t *e) {
    e->rows = 0;
    if (absent(r->lst, SHEET)) {
        return 0;
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f;
    if (at(r, SHEET, &v, &rest) != 0 || moy_vol_open(&v, rest, MOY_VOL_READ, &f) != 0) {
        return 0;
    }
    rows_t s;
    memset(&s, 0, sizeof s);
    s.w = 16 * 8;
    s.h = 32 * 8;
    int64_t n = 0, tw = 1, th = 1;
    if (e->icon_ok) {
        n = e->icon[0];
        tw = e->icon[1];
        th = e->icon[2];
    }
    if (n < 0 || n >= 16 * 32) {
        n = 0;
        tw = th = 1;
    }
    s.ox = (int)(n % 16) * 8;
    s.oy = (int)(n / 16) * 8;
    if (tw > (s.w - s.ox) / 8) {
        tw = (s.w - s.ox) / 8;
    }
    if (th > (s.h - s.oy) / 8) {
        th = (s.h - s.oy) / 8;
    }
    s.pw = (int)tw * 8;
    s.ph = (int)th * 8;
    s.want = aalloc(&r->a, (size_t)s.ph * sizeof(char *));
    s.want_n = aalloc(&r->a, (size_t)s.ph * sizeof(size_t));
    char *buf = aalloc(&r->a, 1024u);
    size_t lcap = 256u, ln = 0;
    char *line = moy_store_alloc(lcap);
    int bad = s.want == NULL || s.want_n == NULL || buf == NULL || line == NULL;
    int stop = 0;
    while (!bad && !stop) {
        size_t got = 0;
        if (moy_vol_read(f, buf, 1024u, &got) != 0) {
            bad = 1;
            break;
        }
        if (got == 0) {
            break;
        }
        for (size_t i = 0; i < got && !stop; i++) {
            if (buf[i] == '\n') {
                int k = sheet_line(&r->a, &s, line, ln);
                ln = 0;
                if (k < 0) {
                    bad = 1;
                }
                stop = k != 0;
                continue;
            }
            if (ln == lcap) {
                char *nl = moy_store_alloc(lcap * 2u);
                if (nl == NULL) {
                    bad = 1;
                    break;
                }
                memcpy(nl, line, ln);
                moy_store_free(line, lcap);
                line = nl;
                lcap *= 2u;
            }
            line[ln++] = buf[i];
        }
    }
    if (!bad && !stop && ln) {
        if (sheet_line(&r->a, &s, line, ln) < 0) {
            bad = 1;
        }
    }
    if (line != NULL) {
        moy_store_free(line, lcap);
    }
    moy_vol_close(f);
    if (bad || !s.inked) {
        return 0;
    }
    e->rows = 1;
    e->pw = (uint16_t)s.pw;
    e->ph = (uint16_t)s.ph;
    e->want = s.want;
    e->want_n = s.want_n;
    return 0;
}

// -- one cart -------------------------------------------------------------------------

static const char *const known[] = { "sprites.moygfx", "map.moymap",
                                     "flags.moyflags", "config.json" };

// `_load(path, False, folder)`: 1 and the entry, or 0 for no cart.
static int load_payloads(rd_t *r, moy_cart_t *c, const char *man,
                         const char *mend, moy_cat_note_fn note, void *ctx);

// `_load(path, whole, folder)`: 1 and the entry -- the cart whole into `c`
// when it is not NULL -- or 0 for no cart.
static int read_entry(rd_t *r, moy_cat_entry_t *e, moy_cart_t *c,
                      moy_cat_note_fn note, void *ctx) {
    memset(e, 0, sizeof *e);
    e->input_n = MOY_CAT_NO_INPUT;
    const char *slash = strrchr(r->path, '/');
    e->folder = slash != NULL ? slash + 1 : r->path;
    e->folder_n = strlen(e->folder);

    char *mpath = ajoin(&r->a, r->path, MANIFEST);
    if (mpath == NULL) {
        return 0;
    }
    moy_buf_t b = { NULL, 0 };
    int rc = moy_fs_read_in(mpath, r->here, r->here != NULL ? MANIFEST : NULL, &b);
    char *broken = NULL;
    if (rc != 0) {
        broken = aalloc(&r->a, 72u);
        if (broken != NULL) {
            const char *nm = errno_name(rc);
            if (nm != NULL) {
                snprintf(broken, 72u, "manifest.json: [Errno %d] %s", rc, nm);
            } else {
                snprintf(broken, 72u, "manifest.json: [Errno %d]", rc);
            }
        }
    } else {
        char *keep = adup(&r->a, b.p, b.n);
        size_t n = b.n;
        moy_buf_free(&b);
        if (keep == NULL) {
            return 0;
        }
        if (!valid_utf8(keep, n)) {
            broken = "manifest.json: invalid UTF-8";
        } else if (moy_json_valid(keep, n) != MOY_JSON_OK) {
            broken = "manifest.json: syntax error in JSON";
        } else if (*moy_json_ws(keep, keep + n) != '{') {
            broken = "manifest.json: not an object";
        } else {
            const char *s = moy_json_ws(keep, keep + n);
            const char *end = moy_json_value(s, keep + n, 1u);
            e->man = s;
            e->man_n = (size_t)(end - s);
        }
    }
    if (broken != NULL && !has(r, MANIFEST)) {
        if (note != NULL) {
            note(ctx, "manifest bad", r->path, broken);
        }
        return 0;
    }
    if (broken != NULL) {
        if (note != NULL) {
            note(ctx, "manifest bad", r->path, broken);
        }
        e->broken = broken;
        const char *t;
        size_t tn = project_title(r->path, &t);
        size_t qn = moy_json_quote(t, tn, NULL, 0);
        char *m = aalloc(&r->a, qn + 16u);
        if (m == NULL) {
            return 0;
        }
        memcpy(m, "{\"title\": ", 10);
        moy_json_quote(t, tn, m + 10, qn);
        m[10 + qn] = '}';
        e->man = m;
        e->man_n = qn + 11u;
    }
    const char *man = e->man, *mend = e->man + e->man_n;

    e->format = field(man, mend, "format");
    e->spec = is_str(e->format, "moy-1");
    e->runtime = field(man, mend, "runtime");
    e->compiled = is_str(e->runtime, "wasm");
    moy_cat_json_t mainf = field(man, mend, "main");
    const char *main;
    size_t main_n = 0;
    if (mainf.v != NULL) {
        main = str_of(&r->a, mainf, &main_n);
        if (main == NULL) {
            if (note != NULL) {
                note(ctx, "unreadable", r->path, "main is not a name");
            }
            return 0;
        }
    } else {
        main = e->compiled ? "main.wasm" : e->spec ? "main.lua" : "main.py";
        main_n = strlen(main);
    }
    if (broken != NULL && !has(r, main)) {
        static const char *const alts[] = { "main.py", "main.lua" };
        for (int i = 0; i < 2; i++) {
            if (has(r, alts[i])) {
                main = alts[i];
                main_n = strlen(main);
                break;
            }
        }
    }
    if (c != NULL && !e->compiled) {
        moy_buf_t b = { NULL, 0 };
        char *full = ajoin(&r->a, r->path, main);
        int src_rc = full == NULL ? MOY_ENOMEM
                     : moy_fs_read_in(full, r->here, r->here != NULL ? main : NULL, &b);
        if (src_rc == 0) {
            c->src = adup(&r->a, b.p, b.n);
            c->src_n = b.n;
            moy_buf_free(&b);
            if (c->src == NULL) {
                return 0;
            }
            if (!valid_utf8(c->src, c->src_n)) {
                if (note != NULL) {
                    note(ctx, "unreadable", r->path, "main is not UTF-8");
                }
                return 0;
            }
        } else if (broken == NULL) {
            if (note != NULL) {
                note(ctx, "main missing", r->path, NULL);
            }
            return 0;
        } else {
            c->src = "";
        }
    } else if (broken == NULL && !has(r, main)) {
        if (note != NULL) {
            note(ctx, "main missing", r->path, NULL);
        }
        return 0;
    } else if (c != NULL) {
        c->src = "";                    // a module has no text; src/ is the code
    }
    e->main = main;
    e->main_n = main_n;

    e->title = field(man, mend, "title");
    e->author = field(man, mend, "author");
    e->type = vfield(man, mend, "type");
    e->fps = field(man, mend, "fps");
    e->palette = field(man, mend, "palette");
    e->extensions = field(man, mend, "extensions");
    e->edit = vfield(man, mend, "edit");
    e->permissions = vfield(man, mend, "permissions");
    moy_cat_json_t v = field(man, mend, "version");
    if (v.v != NULL) {
        e->version.kind = moy_json_int(v.v, v.e, &e->version.value);
        e->version.json = v;
    }
    if (e->compiled) {
        moy_cat_json_t m = field(man, mend, "memory");
        if (m.v != NULL) {
            e->memory.kind = moy_json_int(m.v, m.e, &e->memory.value);
            e->memory.json = m;
        }
        e->writable = field(man, mend, "writable");
        if (e->writable.v != NULL && *e->writable.v == '[') {
            moy_json_iter_t it;
            const char *x, *xe;
            e->writable_ok = 1;
            moy_json_iter(&it, e->writable.v, e->writable.e);
            while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
                if (*x != '"') {
                    e->writable_ok = 0;
                }
            }
        }
    }
    moy_cat_json_t g = vfield(man, mend, "graduated");
    e->graduated = g.v != NULL && moy_json_truthy(g.v, g.e);
    e->icon_ok = norm_icon(field(man, mend, "icon"), e->icon);
    moy_cat_json_t in = field(man, mend, "input");
    if (in.v != NULL && *in.v == '[') {
        moy_json_iter_t it;
        const char *x, *xe;
        size_t cnt = 0;
        moy_json_iter(&it, in.v, in.e);
        while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
            cnt++;
        }
        uint8_t *kinds = aalloc(&r->a, cnt + 1u);
        if (kinds == NULL) {
            return 0;
        }
        size_t k = 0;
        moy_json_iter(&it, in.v, in.e);
        while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
            for (int i = 0; i < MOY_CAT_INPUT_KINDS; i++) {
                if (*x == '"' && moy_json_str_is(x, xe, input_kinds[i],
                                                 strlen(input_kinds[i]))) {
                    kinds[k++] = (uint8_t)i;
                }
            }
        }
        if (k) {
            e->input_n = k;
            e->input = kinds;
        }
    }
    norm_canvas(e, field(man, mend, "canvas"));

    moy_cat_json_t id = field(man, mend, "id");
    size_t idn = 0;
    char *ids = broken == NULL ? str_of(&r->a, id, &idn) : NULL;
    if (ids != NULL && is_cart_id(ids, idn)) {
        e->id = ids;
        e->id_n = idn;
    } else {
        e->id = e->folder;
        e->id_n = e->folder_n;
        if (e->id_n >= 4u && memcmp(e->id + e->id_n - 4u, MOY_CAT_EXT, 4u) == 0) {
            e->id_n -= 4u;
        }
    }

    if (r->lst != NULL) {
        if (lst_exact(r->lst, main, main_n) != NULL) {
            e->has |= MOY_CAT_HAS_MAIN;
        }
        for (size_t i = 0; i < 4; i++) {
            if (lst_exact(r->lst, known[i], strlen(known[i])) != NULL) {
                e->has |= (i == 0 ? MOY_CAT_HAS_SPRITES : i == 1 ? MOY_CAT_HAS_MAP
                           : i == 2 ? MOY_CAT_HAS_FLAGS : MOY_CAT_HAS_CONFIG);
            }
        }
        const ent_t *x = lst_exact(r->lst, SCENES, sizeof SCENES - 1u);
        if (x != NULL && x->dir) {
            e->has |= MOY_CAT_HAS_SCENES;
        }
        x = lst_exact(r->lst, "cover.png", 9u);
        if (x != NULL && !x->dir) {
            e->has |= MOY_CAT_HAS_COVER;
            e->cover_size = x->size;
        }
        x = lst_exact(r->lst, "journal", 7u);
        if (x != NULL && x->dir) {
            e->has |= MOY_CAT_HAS_JOURNAL;
        }
    }
    if (e->graduated) {
        e->has |= MOY_CAT_GRADUATED;
    }
    if (broken != NULL) {
        e->has |= MOY_CAT_BROKEN;
    }
    if (e->compiled) {
        e->has |= MOY_CAT_COMPILED;
    }

    if (c != NULL) {
        return load_payloads(r, c, man, mend, note, ctx) && !r->a.failed;
    }
    rc = scene_names(r, man, mend, e, NULL);
    if (rc != 0) {
        if (note != NULL && rc == 1) {
            note(ctx, "unreadable", r->path, "unhashable scene name");
        }
        return 0;
    }
    sheet_icon(r, e);
    return !r->a.failed;
}

// -- the rest of a cart, whole ----------------------------------------------------------

// A file of the folder read whole into the arena: 0, or the read's errno.
static int slurp(rd_t *r, const char *name, const char **out, size_t *n) {
    moy_vol_t v;
    const char *rest;
    moy_buf_t b = { NULL, 0 };
    int rc = at(r, name, &v, &rest);
    if (rc == 0) {
        rc = moy_fs_read_vol(&v, rest, (size_t)-1, &b);
    }
    if (rc != 0) {
        return rc;
    }
    *out = adup(&r->a, b.p, b.n);
    *n = b.n;
    moy_buf_free(&b);
    return *out == NULL ? MOY_ENOMEM : 0;
}

// What a text payload read with `except OSError` makes of the file: 1 and the
// text, 0 for absent (None), -1 for text that is not UTF-8 (the cart fails).
static int text(rd_t *r, const char *name, const char **out, size_t *n) {
    *out = NULL;
    *n = 0;
    if (slurp(r, name, out, n) != 0) {
        *out = NULL;
        return 0;
    }
    return valid_utf8(*out, *n) ? 1 : -1;
}

// What `json.loads(_read(...))` under `except (OSError, ValueError)` makes of
// the file: its value's span, or none.
static moy_load_json_t json_file(rd_t *r, const char *name) {
    moy_load_json_t j = { NULL, NULL };
    const char *t;
    size_t n;
    if (slurp(r, name, &t, &n) == 0 && valid_utf8(t, n)) {
        const char *s = moy_json_ws(t, t + n);
        const char *e = moy_json_value(s, t + n, 1u);
        if (e != NULL && moy_json_ws(e, t + n) == t + n) {
            j.p = s;
            j.e = e;
        }
    }
    return j;
}

// Whether dict(x) -- or a dict's update(x) -- takes the value: a mapping, or
// pairs (each a two-element list whose first is hashable), or "".
static int dict_takes(const char *v, const char *ve) {
    if (*v == '{') {
        return 1;
    }
    if (*v == '"') {
        return ve - v == 2;
    }
    if (*v != '[') {
        return 0;
    }
    moy_json_iter_t it, pair;
    const char *x, *xe, *k, *ke, *y, *ye, *z, *ze;
    moy_json_iter(&it, v, ve);
    while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
        if (*x != '[') {
            return 0;
        }
        moy_json_iter(&pair, x, xe);
        if (!moy_json_next(&pair, NULL, NULL, &k, &ke)
            || !moy_json_next(&pair, NULL, NULL, &y, &ye)
            || moy_json_next(&pair, NULL, NULL, &z, &ze)
            || *k == '[' || *k == '{') {
            return 0;
        }
    }
    return 1;
}

typedef struct {
    rd_t *r;
    moy_load_file_t *f;
    size_t len, cap;
    const char *sub, *ext;
    int bad;                    // a file that is not UTF-8
    int skip_bad;               // ...is skipped rather than failing the cart
} files_t;

static int files_add(files_t *l, const char *name, size_t nn, const char *t,
                     size_t n) {
    if (l->len == l->cap) {
        size_t cap = l->cap ? l->cap * 2u : 8u;
        moy_load_file_t *f = aalloc(&l->r->a, cap * sizeof(moy_load_file_t));
        if (f == NULL) {
            return MOY_ENOMEM;
        }
        if (l->len) {
            memcpy(f, l->f, l->len * sizeof(moy_load_file_t));
        }
        l->f = f;
        l->cap = cap;
    }
    char *nm = adup(&l->r->a, name, nn);
    if (nm == NULL) {
        return MOY_ENOMEM;
    }
    l->f[l->len++] = (moy_load_file_t){ nm, nn, t, n };
    return 0;
}

// One entry of an assets folder (images/, scenes/, src/): read, kept under
// its name less the extension.
static int asset_ent(void *ctx, const char *name, size_t n, int is_dir,
                     uint32_t size) {
    (void)is_dir;
    (void)size;
    files_t *l = ctx;
    size_t xn = strlen(l->ext);
    if (n < xn || memcmp(name + n - xn, l->ext, xn) != 0) {
        return 0;
    }
    return files_add(l, name, n, NULL, 0);
}

static int read_assets(files_t *l) {
    moy_vol_t v;
    const char *rest;
    if (at(l->r, l->sub, &v, &rest) != 0) {
        return 0;
    }
    char *dir = adup(&l->r->a, rest, strlen(rest));
    if (dir == NULL) {
        return MOY_ENOMEM;
    }
    int rc = moy_vol_list(&v, dir, asset_ent, l);
    if (rc == MOY_ENOMEM) {
        return rc;
    }
    if (rc != 0) {
        l->len = 0;
        return 0;
    }
    size_t keep = 0;
    for (size_t i = 0; i < l->len; i++) {
        size_t pn = strlen(l->sub) + l->f[i].name_n + 2u;
        char *p = aalloc(&l->r->a, pn);
        if (p == NULL) {
            return MOY_ENOMEM;
        }
        snprintf(p, pn, "%s/%s", l->sub, l->f[i].name);
        const char *t;
        size_t tn;
        int k = text(l->r, p, &t, &tn);
        if (k == 0) {
            continue;                   // an unreadable entry: fewer assets
        }
        if (k < 0) {
            if (l->skip_bad) {
                continue;
            }
            l->bad = 1;
            return 0;
        }
        moy_load_file_t f = l->f[i];
        f.text = t;
        f.n = tn;
        size_t xn = strlen(l->ext);
        f.name_n -= xn;
        ((char *)f.name)[f.name_n] = 0;
        l->f[keep++] = f;
    }
    l->len = keep;
    return 0;
}

static int by_name(const void *a, const void *b) {
    const moy_load_file_t *x = a, *y = b;
    return bytes_cmp(x->name, x->name_n, y->name, y->name_n);
}

static int load_payloads(rd_t *r, moy_cart_t *c, const char *man,
                         const char *mend, moy_cat_note_fn note, void *ctx) {
    moy_cat_entry_t *e = &c->e;
    files_t before = { r, NULL, 0, 0, NULL, NULL, 0, 0 };
    files_t after = { r, NULL, 0, 0, NULL, NULL, 0, 0 };
    const char *why = NULL;
    if (e->compiled) {
        files_t src = { r, NULL, 0, 0, "src", "", 0, 1 };
        if (read_assets(&src) != 0) {
            return 0;
        }
        if (src.len > 1) {
            qsort(src.f, src.len, sizeof(moy_load_file_t), by_name);
        }
        for (size_t i = 0; i < src.len; i++) {      // named "src/<file>"
            size_t nn = src.f[i].name_n + 4u;
            char *nm = aalloc(&r->a, nn + 1u);
            if (nm == NULL) {
                return 0;
            }
            snprintf(nm, nn + 1u, "src/%s", src.f[i].name);
            src.f[i].name = nm;
            src.f[i].name_n = nn;
        }
        after = src;
    } else {
        moy_cat_json_t names = field(man, mend, "sources");
        if (names.v != NULL && moy_json_truthy(names.v, names.e)) {
            if (*names.v != '[') {
                why = "sources is not a list";
            } else {
                moy_json_iter_t it;
                const char *x, *xe;
                int has_main = 0, seen_main = 0;
                moy_json_iter(&it, names.v, names.e);
                while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
                    if (*x == '"' && moy_json_str_is(x, xe, e->main, e->main_n)) {
                        has_main = 1;
                    }
                }
                if (!has_main) {
                    why = "sources without main";
                }
                moy_json_iter(&it, names.v, names.e);
                while (why == NULL && moy_json_next(&it, NULL, NULL, &x, &xe)) {
                    if (*x != '"') {
                        why = "a source that is not a name";
                        break;
                    }
                    size_t nn = moy_json_strlen(x, xe);
                    char *nm = aalloc(&r->a, nn + 1u);
                    if (nm == NULL) {
                        return 0;
                    }
                    moy_json_str(x, xe, nm);
                    nm[nn] = 0;
                    if (nn == e->main_n && memcmp(nm, e->main, nn) == 0) {
                        seen_main = 1;
                        continue;
                    }
                    const char *t;
                    size_t tn;
                    int k = text(r, nm, &t, &tn);
                    if (k < 0) {
                        why = "a source that is not UTF-8";
                        break;
                    }
                    if (k == 0) {
                        if (e->broken == NULL) {
                            why = "source missing";
                            break;
                        }
                        continue;
                    }
                    if (files_add(seen_main ? &after : &before, nm, nn, t, tn) != 0) {
                        return 0;
                    }
                }
            }
        }
    }
    if (why != NULL) {
        if (note != NULL) {
            note(ctx, "unreadable", r->path, why);
        }
        return 0;
    }
    c->before = before.f;
    c->before_n = before.len;
    c->after = after.f;
    c->after_n = after.len;

    moy_cat_json_t cfg = vfield(man, mend, "config");
    if (cfg.v != NULL && !dict_takes(cfg.v, cfg.e)) {
        why = "config is not a mapping";
    }
    c->cfg_manifest.p = cfg.v;
    c->cfg_manifest.e = cfg.e;
    moy_load_json_t file = json_file(r, "config.json");
    if (why == NULL && file.p != NULL && !dict_takes(file.p, file.e)) {
        why = "config.json is not a mapping";
    }
    c->cfg_file = file;
    int k;
    if (why == NULL && (k = text(r, "flags.moyflags", &c->flags, &c->flags_n)) < 0) {
        why = "flags are not UTF-8";
    }
    if (why == NULL && (k = text(r, SHEET, &c->sprites, &c->sprites_n)) < 0) {
        why = "sprites are not UTF-8";
    }
    if (why == NULL && (k = text(r, "map.moymap", &c->map, &c->map_n)) < 0) {
        why = "map is not UTF-8";
    }
    c->sounds = json_file(r, "sounds.json");
    c->blocks = json_file(r, "blocks.json");
    files_t images = { r, NULL, 0, 0, "images", ".moyimg", 0, 0 };
    files_t scenes = { r, NULL, 0, 0, SCENES, SCENE_EXT, 0, 0 };
    if (why == NULL && (read_assets(&images) != 0 || read_assets(&scenes) != 0)) {
        return 0;
    }
    if (why == NULL && (images.bad || scenes.bad)) {
        why = "an asset is not UTF-8";
    }
    if (why != NULL) {
        if (note != NULL) {
            note(ctx, "unreadable", r->path, why);
        }
        return 0;
    }
    if (scenes.len > 1) {
        qsort(scenes.f, scenes.len, sizeof(moy_load_file_t), by_name);
    }
    c->images = images.f;
    c->images_n = images.len;
    c->scenes = scenes.f;
    c->scenes_n = scenes.len;
    names_t blobs = { &r->a, NULL, NULL, 0, 0 };
    for (size_t i = 0; i < scenes.len; i++) {
        if (names_add(&blobs, scenes.f[i].name, scenes.f[i].name_n) != 0) {
            return 0;
        }
    }
    int rc = scene_names(r, man, mend, e, &blobs);
    if (rc != 0) {
        if (note != NULL && rc == 1) {
            note(ctx, "unreadable", r->path, "unhashable scene name");
        }
        return 0;
    }
    return 1;
}

// One folder, entered: its listing taken, its entry read and handed on.
static int one(const char *path, const moy_vol_t *v, const char *vpath,
               moy_cat_fn fn, moy_cat_note_fn note, void *ctx) {
    rd_t r;
    memset(&r, 0, sizeof r);
    r.path = path;
    moy_vol_here_t h;
    lst_t lst = { &r.a, NULL, 0, 0 };
    int entered = v != NULL && moy_vol_enter(v, vpath, &h) == 0;
    if (entered) {
        r.here = &h;
        const char *dot = moy_vol_here_name(&h, "");
        if (dot != NULL && moy_vol_list(&h.v, dot, lst_add, &lst) == 0) {
            r.lst = &lst;
        }
    }
    moy_cat_entry_t e;
    int ok = read_entry(&r, &e, NULL, note, ctx);
    int rc = ok ? fn(ctx, &e) : MOY_FS_NONE;
    if (entered) {
        moy_vol_leave(&h);
    }
    afree(&r.a);
    return rc;
}

typedef struct {
    arena_t *a;
    const char **name;
    size_t *n;
    size_t len, cap;
} dirs_t;

static int root_ent(void *ctx, const char *name, size_t n, int is_dir,
                    uint32_t size) {
    (void)size;
    dirs_t *d = ctx;
    size_t xn = sizeof(MOY_CAT_EXT) - 1u;
    if (!is_dir || n < xn || memcmp(name + n - xn, MOY_CAT_EXT, xn) != 0) {
        return 0;
    }
    char *c = adup(d->a, name, n);
    return c == NULL ? MOY_ENOMEM : names_add((names_t *)d, c, n);
}

int moy_cat_scan(const char *root, moy_cat_fn fn, moy_cat_note_fn note,
                 void *ctx) {
    moy_vol_t v;
    const char *rest;
    arena_t a;
    memset(&a, 0, sizeof a);
    if (moy_vol_at(root, &v, &rest) != 0) {
        return MOY_FS_NONE;
    }
    dirs_t d = { &a, NULL, NULL, 0, 0 };
    int rc = moy_vol_list(&v, rest, root_ent, &d);
    if (rc != 0) {
        afree(&a);
        return rc == MOY_ENOMEM ? rc : MOY_FS_NONE;
    }
    sort_names(d.name, d.n, d.len);
    rc = 0;
    for (size_t i = 0; i < d.len && rc == 0; i++) {
        char *path = ajoin(&a, root, d.name[i]);
        char *vpath = ajoin(&a, rest, d.name[i]);
        if (path == NULL || vpath == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        int r1 = one(path, &v, vpath, fn, note, ctx);
        if (r1 != 0 && r1 != MOY_FS_NONE) {
            rc = r1;
        }
    }
    afree(&a);
    return rc;
}

int moy_cat_entry(const char *path, moy_cat_fn fn, moy_cat_note_fn note,
                  void *ctx) {
    moy_vol_t v;
    const char *rest;
    if (moy_vol_at(path, &v, &rest) != 0) {
        rd_t r;
        memset(&r, 0, sizeof r);
        r.path = path;
        moy_cat_entry_t e;
        int ok = read_entry(&r, &e, NULL, note, ctx);
        int rc = ok ? fn(ctx, &e) : MOY_FS_NONE;
        afree(&r.a);
        return rc;
    }
    return one(path, &v, rest, fn, note, ctx);
}

int moy_cat_load(const char *path, moy_load_fn fn, moy_cat_note_fn note,
                 void *ctx) {
    rd_t r;
    memset(&r, 0, sizeof r);
    r.path = path;
    moy_vol_t v;
    const char *rest;
    moy_vol_here_t h;
    int entered = moy_vol_at(path, &v, &rest) == 0 && moy_vol_enter(&v, rest, &h) == 0;
    if (entered) {
        r.here = &h;
    }
    moy_cart_t c;
    memset(&c, 0, sizeof c);
    int ok = read_entry(&r, &c.e, &c, note, ctx);
    int rc = ok ? fn(ctx, &c) : MOY_FS_NONE;
    if (entered) {
        moy_vol_leave(&h);
    }
    afree(&r.a);
    return rc;
}

// -- whole folders ---------------------------------------------------------------------

static int collect(void *ctx, const char *name, size_t n, int is_dir,
                   uint32_t size) {
    (void)size;
    lst_t *l = ctx;
    return lst_add(l, name, n, is_dir, 0);
}

static void rmtree(const char *path, int depth) {
    arena_t a;
    memset(&a, 0, sizeof a);
    lst_t l = { &a, NULL, 0, 0 };
    moy_vol_t v;
    const char *rest;
    if (moy_vol_at(path, &v, &rest) != 0) {
        return;
    }
    if (moy_vol_list(&v, rest, collect, &l) == 0) {
        for (size_t i = 0; i < l.len; i++) {
            char *p = ajoin(&a, path, l.e[i].name);
            if (p == NULL) {
                break;
            }
            if (l.e[i].dir && depth < 16) {
                rmtree(p, depth + 1);
            } else {
                moy_fs_remove(p);
            }
        }
        moy_fs_remove(path);
    }
    afree(&a);
}

void moy_cat_rmtree(const char *path) {
    rmtree(path, 0);
}

static int copy_file(const char *src, const char *dst) {
    moy_vol_t a, b;
    const char *ra, *rb;
    moy_vol_file_t *fi, *fo;
    if (moy_vol_at(src, &a, &ra) != 0 || moy_vol_open(&a, ra, MOY_VOL_READ, &fi) != 0) {
        return 0;
    }
    int rc = moy_vol_at(dst, &b, &rb);
    if (rc == 0) {
        rc = moy_vol_open(&b, rb, MOY_VOL_WRITE, &fo);
    }
    if (rc != 0) {
        moy_vol_close(fi);
        return rc == MOY_ENOSPC ? rc : 0;
    }
    char *buf = moy_store_alloc(4096u);
    rc = buf == NULL ? MOY_ENOMEM : 0;
    while (rc == 0) {
        size_t got = 0;
        if (moy_vol_read(fi, buf, 4096u, &got) != 0 || got == 0) {
            break;
        }
        rc = moy_vol_write(fo, buf, got);
    }
    moy_store_free(buf, 4096u);
    moy_vol_close(fi);
    int rc2 = moy_vol_close(fo);
    if (rc == 0) {
        rc = rc2;
    }
    return rc == MOY_ENOSPC ? rc : 0;
}

static int skip(const char *name, size_t n, const char *main) {
    static const char *const names[] = { "manifest.json", "config.json", "pmem.json",
                                         "thumbs", "journal", "journal.jsonl",
                                         "cursor.json", "s" };
    for (size_t i = 0; i < sizeof names / sizeof names[0]; i++) {
        if (strcmp(name, names[i]) == 0) {
            return 1;
        }
    }
    return strcmp(name, main) == 0 || strncmp(name, "journal", 7) == 0
           || (n >= 4 && (memcmp(name + n - 4, ".bak", 4) == 0
                          || memcmp(name + n - 4, ".tmp", 4) == 0));
}

int moy_cat_copy(const char *src, const char *dst, const char *main) {
    arena_t a;
    memset(&a, 0, sizeof a);
    lst_t l = { &a, NULL, 0, 0 };
    moy_vol_t v;
    const char *rest;
    int rc = 0;
    if (moy_vol_at(src, &v, &rest) != 0 || moy_vol_list(&v, rest, collect, &l) != 0) {
        afree(&a);
        return 0;
    }
    for (size_t i = 0; i < l.len && rc == 0; i++) {
        if (skip(l.e[i].name, l.e[i].n, main)) {
            continue;
        }
        char *s = ajoin(&a, src, l.e[i].name), *d = ajoin(&a, dst, l.e[i].name);
        if (s == NULL || d == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        if (!l.e[i].dir) {
            rc = copy_file(s, d);
            continue;
        }
        lst_t kids = { &a, NULL, 0, 0 };
        moy_vol_t kv;
        const char *kr;
        if (moy_vol_at(s, &kv, &kr) != 0 || moy_vol_list(&kv, kr, collect, &kids) != 0) {
            continue;
        }
        moy_fs_mkdir(d);
        for (size_t k = 0; k < kids.len && rc == 0; k++) {
            char *ks = ajoin(&a, s, kids.e[k].name), *kd = ajoin(&a, d, kids.e[k].name);
            if (ks == NULL || kd == NULL) {
                rc = MOY_ENOMEM;
                break;
            }
            if (!kids.e[k].dir) {
                rc = copy_file(ks, kd);
            }
        }
    }
    afree(&a);
    return rc == MOY_ENOSPC ? rc : 0;
}
