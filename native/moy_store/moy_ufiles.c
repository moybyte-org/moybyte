// The user-files layer (moy_ufiles.h has the contract).

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "moy_cat.h"
#include "moy_img.h"
#include "moy_journal.h"
#include "moy_json.h"
#include "moy_png.h"
#include "moy_ufiles.h"

#if defined(__has_include)
#if __has_include("lib/uzlib/uzlib.h")
#include "lib/uzlib/uzlib.h"
#else
#include "uzlib.h"
#endif
#else
#include "lib/uzlib/uzlib.h"
#endif

// -- the registry ---------------------------------------------------------------------

static const struct {
    const char *name, *ext, *base;
    uint8_t folder;
} KINDS[MOY_UF_KINDS] = {
    { "drawings", ".moyimg", "drawing", 0 },
    { "docs", ".md", "doc", 0 },
    { "sprites", ".moygfx", "sheet", 0 },
    { "music", ".moysong", "song", 0 },
    { "recordings", "", "recording", 1 },
};
#define DOCS 1

static const char *const VAULT_EXTS[] = { ".py", ".lua", ".txt", ".json" };
#define NSCRIPT 2u
#define NVAULT 4u

static const char FILES_DIR[] = "files";
static const char TRASH_DIR[] = "trash";
static const char HISTORY_DIR[] = ".history";
static const char HISTORY_EXT[] = ".jsonl";
static const char PROJECT[] = "project:";
static const char COVER[] = "cover.png";
static const char ARTWORK[] = "artwork.moyimg";

// The order a project lists in: the two documents a person edits, the program,
// then its assets. Then the rest, sorted, then the two subfolders' items.
static const char *const PROJECT_ORDER[] = {
    "manifest.json", "config.json", "main.py", "main.lua", "sprites.moygfx",
    "map.moymap", "flags.moyflags", "sounds.json", "blocks.json",
};
#define NORDER (sizeof(PROJECT_ORDER) / sizeof(PROJECT_ORDER[0]))
static const char *const PROJECT_SUBDIRS[][2] = {
    { "scenes", ".moyscene" }, { "images", ".moyimg" },
};

const char *moy_uf_kind(int i) {
    return i >= 0 && i < MOY_UF_KINDS ? KINDS[i].name : NULL;
}

const char *moy_uf_kind_ext(int i) {
    return i >= 0 && i < MOY_UF_KINDS ? KINDS[i].ext : NULL;
}

int moy_uf_kind_folder(int i) {
    return i >= 0 && i < MOY_UF_KINDS ? KINDS[i].folder : 0;
}

const char *moy_uf_kind_base(int i) {
    return i >= 0 && i < MOY_UF_KINDS ? KINDS[i].base : NULL;
}

int moy_uf_kind_of(const char *kind) {
    for (int i = 0; i < MOY_UF_KINDS; i++) {
        if (strcmp(kind, KINDS[i].name) == 0) {
            return i;
        }
    }
    return -1;
}

static int ends(const char *s, size_t n, const char *ext) {
    size_t e = strlen(ext);
    return n > e && memcmp(s + n - e, ext, e) == 0;
}

static size_t ext_from(const char *name, size_t nexts) {
    size_t n = strlen(name);
    for (size_t i = 0; i < nexts; i++) {
        if (ends(name, n, VAULT_EXTS[i])) {
            return strlen(VAULT_EXTS[i]);
        }
    }
    return 0;
}

size_t moy_uf_vault_ext(const char *name) {
    return ext_from(name, NVAULT);
}

size_t moy_uf_script_ext(const char *name) {
    return ext_from(name, NSCRIPT);
}

const char *moy_uf_project_folder(const char *kind) {
    size_t n = sizeof(PROJECT) - 1u;
    return strncmp(kind, PROJECT, n) == 0 ? kind + n : "";
}

// -- strings ---------------------------------------------------------------------------

// A string the store allocated, its size in front of it.
static char *str_new(size_t n) {
    size_t *p = moy_store_alloc(sizeof(size_t) + n + 1u);
    if (p == NULL) {
        return NULL;
    }
    p[0] = n + 1u;
    return (char *)(p + 1);
}

static void str_free(char *s) {
    if (s != NULL) {
        size_t *p = (size_t *)s - 1;
        moy_store_free(p, sizeof(size_t) + p[0]);
    }
}

// The pieces joined (NULL ends the list), or NULL with no memory.
static char *cat(const char *a, ...) {
    va_list ap;
    size_t n = 0;
    va_start(ap, a);
    for (const char *s = a; s != NULL; s = va_arg(ap, const char *)) {
        n += strlen(s);
    }
    va_end(ap);
    char *out = str_new(n);
    if (out == NULL) {
        return NULL;
    }
    size_t at = 0;
    va_start(ap, a);
    for (const char *s = a; s != NULL; s = va_arg(ap, const char *)) {
        size_t k = strlen(s);
        memcpy(out + at, s, k);
        at += k;
    }
    va_end(ap);
    out[at] = 0;
    return out;
}

// The store's slug (moy_store_base.slug): ASCII letters lowered and digits
// kept, a space, '-' or '_' as '_', the rest dropped; "cart" for nothing.
static size_t slug_into(const char *s, size_t n, char *out) {
    size_t k = 0;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (c >= 'A' && c <= 'Z') {
            c = (char)(c - 'A' + 'a');
        }
        if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {
            out[k++] = c;
        } else if (c == ' ' || c == '-' || c == '_') {
            out[k++] = '_';
        }
    }
    if (k == 0) {
        memcpy(out, "cart", 4);
        k = 4;
    }
    out[k] = 0;
    return k;
}

static int has_alnum(const char *s) {
    for (; *s; s++) {
        char c = *s;
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) {
            return 1;
        }
    }
    return 0;
}

// The extension a whole-named vault item keeps: its length, 0 for another
// kind or name.
static size_t whole_ext(int kind, const char *name) {
    return kind == DOCS ? moy_uf_vault_ext(name) : 0u;
}

// A title slugged the kind's way into `out` (MOY_UF_NAME_MAX + 1): the
// vault extension kept. BAD for a name too long.
static int slug_item(int kind, const char *title, char *out) {
    size_t n = strlen(title), e = whole_ext(kind, title);
    if (n > MOY_UF_NAME_MAX) {
        return MOY_UF_BAD;
    }
    size_t k = slug_into(title, n - e, out);
    memcpy(out + k, title + n - e, e);
    out[k + e] = 0;
    return 0;
}

// -- paths ------------------------------------------------------------------------------

// The folder beside the carts folder (moy_store_base._sibling_path).
static char *sibling(const char *root, const char *name) {
    const char *cut = strrchr(root, '/');
    if (cut == NULL) {
        return cat(root, "/", name, NULL);
    }
    if (cut == root) {
        return cat(name, NULL);
    }
    char *parent = str_new((size_t)(cut - root));
    if (parent == NULL) {
        return NULL;
    }
    memcpy(parent, root, (size_t)(cut - root));
    parent[cut - root] = 0;
    char *out = cat(parent, "/", name, NULL);
    str_free(parent);
    return out;
}

static char *files_root(const char *root) {
    return sibling(root, FILES_DIR);
}

static const char *item_ext(int kind, const char *name) {
    return whole_ext(kind, name) ? "" : KINDS[kind].ext;
}

static char *kind_dir(const char *root, int kind) {
    char *f = files_root(root);
    char *out = f ? cat(f, "/", KINDS[kind].name, NULL) : NULL;
    str_free(f);
    return out;
}

static char *file_path(const char *root, int kind, const char *name) {
    char *d = kind_dir(root, kind);
    char *out = d ? cat(d, "/", name, item_ext(kind, name), NULL) : NULL;
    str_free(d);
    return out;
}

static char *trash_dir(const char *root, int kind) {
    char *f = files_root(root);
    char *out = f ? cat(f, "/", TRASH_DIR, "/", KINDS[kind].name, NULL) : NULL;
    str_free(f);
    return out;
}

static char *trash_path(const char *root, int kind, const char *name) {
    char *d = trash_dir(root, kind);
    char *out = d ? cat(d, "/", name, item_ext(kind, name), NULL) : NULL;
    str_free(d);
    return out;
}

static char *history_path(const char *root, int kind, const char *name) {
    char *f = files_root(root);
    char *out = f ? cat(f, "/", HISTORY_DIR, "/", KINDS[kind].name, "/", name,
                        HISTORY_EXT, NULL) : NULL;
    str_free(f);
    return out;
}

static char *history_trash_path(const char *root, int kind, const char *name) {
    char *f = files_root(root);
    char *out = f ? cat(f, "/", TRASH_DIR, "/", HISTORY_DIR, "/", KINDS[kind].name,
                        "/", name, HISTORY_EXT, NULL) : NULL;
    str_free(f);
    return out;
}

static char *project_dir(const char *root, const char *kind) {
    return cat(root, "/", moy_uf_project_folder(kind), NULL);
}

// A project file's path: `name` holds at most one folder and never climbs
// (`\` read as `/`). NULL and `*rc` BAD for a name refused.
static char *project_file(const char *root, const char *kind, const char *name,
                          int *rc) {
    size_t n = strlen(name);
    char *norm = str_new(n);
    if (norm == NULL) {
        *rc = MOY_ENOMEM;
        return NULL;
    }
    size_t slashes = 0, cut = 0;
    for (size_t i = 0; i < n; i++) {
        norm[i] = name[i] == '\\' ? '/' : name[i];
        if (norm[i] == '/') {
            slashes++;
            cut = i;
        }
    }
    norm[n] = 0;
    const char *first = norm, *last = slashes ? norm + cut + 1 : norm;
    size_t first_n = slashes ? cut : n;
    int bad = slashes > 1u || *last == 0 || first_n == 0
              || (first_n == 1 && first[0] == '.')
              || (first_n == 2 && first[0] == '.' && first[1] == '.');
    if (slashes == 1u && (strcmp(last, ".") == 0 || strcmp(last, "..") == 0)) {
        bad = 1;
    }
    char *out = NULL;
    if (bad) {
        *rc = MOY_UF_BAD;
    } else {
        char *d = project_dir(root, kind);
        out = d ? cat(d, "/", norm, NULL) : NULL;
        str_free(d);
        *rc = out ? 0 : MOY_ENOMEM;
    }
    str_free(norm);
    return out;
}

// The kind an item verb takes: its index, or -1 and `*rc` BAD.
static int kind_arg(const char *kind, int *rc) {
    int k = moy_uf_kind_of(kind);
    *rc = k < 0 ? MOY_UF_BAD : 0;
    return k;
}

int moy_uf_path(int which, const char *root, const char *kind,
                const char *name, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    char *p = NULL;
    int rc = 0;
    if (which == MOY_UF_PATH_FILES) {
        p = files_root(root);
    } else if (which == MOY_UF_PATH_PROJECT || which == MOY_UF_PATH_PROJECT_FILE) {
        if (*moy_uf_project_folder(kind) == 0) {
            return MOY_UF_BAD;
        }
        p = which == MOY_UF_PATH_PROJECT ? project_dir(root, kind)
            : project_file(root, kind, name, &rc);
        if (rc != 0) {
            return rc;
        }
    } else {
        int k = kind_arg(kind, &rc);
        if (k < 0) {
            return rc;
        }
        switch (which) {
            case MOY_UF_PATH_KIND: p = kind_dir(root, k); break;
            case MOY_UF_PATH_FILE: p = file_path(root, k, name); break;
            case MOY_UF_PATH_HISTORY: p = history_path(root, k, name); break;
            case MOY_UF_PATH_TRASH: p = trash_path(root, k, name); break;
            case MOY_UF_PATH_HISTORY_TRASH: p = history_trash_path(root, k, name); break;
            default: return MOY_UF_BAD;
        }
    }
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    size_t n = strlen(p);
    out->p = moy_store_alloc(n + 1u);
    if (out->p == NULL) {
        str_free(p);
        return MOY_ENOMEM;
    }
    memcpy(out->p, p, n + 1u);
    out->n = n;
    str_free(p);
    return 0;
}

// -- the medium ---------------------------------------------------------------------------

static int exists(const char *path) {
    return path != NULL && moy_fs_exists(path);
}

static int is_dir(const char *path) {
    moy_vol_stat_t st;
    return moy_fs_stat(path, &st) == 0 && st.is_dir;
}

// The store's folders (moy_store_base.ensure_dirs): the carts folder and its
// parent, which is where the publish marker lives.
static int ensure_dirs(const char *root) {
    const char *cut = strrchr(root, '/');
    char *parent = NULL;
    if (cut != NULL && cut != root) {
        parent = str_new((size_t)(cut - root));
        if (parent == NULL) {
            return MOY_ENOMEM;
        }
        memcpy(parent, root, (size_t)(cut - root));
        parent[cut - root] = 0;
        moy_fs_mkdir(parent);
    }
    moy_fs_mkdir(root);
    int rc = moy_fs_root(parent != NULL ? parent : root);
    str_free(parent);
    return rc;
}

static int mkdirs(const char *root, int with_root, const char *a, const char *b,
                  const char *c) {
    int rc = with_root ? ensure_dirs(root) : 0;
    char *f = files_root(root);
    if (f == NULL) {
        return MOY_ENOMEM;
    }
    moy_fs_mkdir(f);
    const char *parts[3] = { a, b, c };
    char *at = f;
    for (int i = 0; i < 3 && parts[i] != NULL; i++) {
        char *next = cat(at, "/", parts[i], NULL);
        str_free(at);
        if (next == NULL) {
            return MOY_ENOMEM;
        }
        moy_fs_mkdir(next);
        at = next;
    }
    str_free(at);
    return rc;
}

static int ensure_kind_dir(const char *root, int kind) {
    return mkdirs(root, 1, KINDS[kind].name, NULL, NULL);
}

static int ensure_history_dir(const char *root, int kind) {
    return mkdirs(root, 1, HISTORY_DIR, KINDS[kind].name, NULL);
}

static int ensure_history_trash_dir(const char *root, int kind) {
    return mkdirs(root, 0, TRASH_DIR, HISTORY_DIR, KINDS[kind].name);
}

// A plain copy, read whole and written over `dst` in place (moy_fs._copy).
static int copy_file(const char *src, const char *dst) {
    moy_buf_t b;
    int rc = moy_fs_read_file(src, (size_t)-1, &b);
    if (rc == 0) {
        rc = moy_fs_write(dst, b.p, b.n);
        moy_buf_free(&b);
    }
    return rc;
}

// A sidecar follows its file: moved where the medium renames, else copied
// and removed. Nothing to move is nothing done.
static void sidecar_move(const char *src, const char *dst) {
    if (!exists(src)) {
        return;
    }
    if (moy_fs_rename(src, dst) != 0 && copy_file(src, dst) == 0) {
        moy_fs_remove(src);
    }
}

// -- listing ---------------------------------------------------------------------------------

typedef struct {
    char *name;
    int64_t mtime;
    int kind;                   // the trash listing's
} entry_t;

typedef struct {
    entry_t *e;
    size_t n, cap;
    int rc;
} entries_t;

static void entries_free(entries_t *es) {
    for (size_t i = 0; i < es->n; i++) {
        str_free(es->e[i].name);
    }
    if (es->e != NULL) {
        moy_store_free(es->e, es->cap * sizeof(entry_t));
    }
    es->e = NULL;
    es->n = es->cap = 0;
}

static int entries_add(entries_t *es, const char *name, size_t n, int64_t mt, int kind) {
    if (es->n == es->cap) {
        size_t cap = es->cap ? es->cap * 2u : 16u;
        entry_t *e = moy_store_alloc(cap * sizeof(entry_t));
        if (e == NULL) {
            return MOY_ENOMEM;
        }
        if (es->e != NULL) {
            memcpy(e, es->e, es->n * sizeof(entry_t));
            moy_store_free(es->e, es->cap * sizeof(entry_t));
        }
        es->e = e;
        es->cap = cap;
    }
    char *s = str_new(n);
    if (s == NULL) {
        return MOY_ENOMEM;
    }
    memcpy(s, name, n);
    s[n] = 0;
    es->e[es->n].name = s;
    es->e[es->n].mtime = mt;
    es->e[es->n].kind = kind;
    es->n++;
    return 0;
}

static int list_cb(void *ctx, const char *name, size_t len, int dir, uint32_t size) {
    (void)size;
    entries_t *es = ctx;
    return entries_add(es, name, len, dir, 0);
}

// A folder's entries (`mtime` holds whether each is a folder), or `*missing`
// the errno value when it cannot be listed (os.listdir's OSError).
static int list_dir(const char *dir, entries_t *out, int *missing) {
    moy_vol_t v;
    const char *rest;
    memset(out, 0, sizeof *out);
    *missing = 0;
    int rc = moy_vol_at(dir, &v, &rest);
    if (rc == 0) {
        rc = moy_vol_list(&v, rest, list_cb, out);
    }
    if (rc == MOY_ENOMEM) {
        entries_free(out);
        return rc;
    }
    if (rc != 0) {
        entries_free(out);
        *missing = rc;
    }
    return 0;
}

static int cmp_name(const char *a, const char *b) {
    return strcmp(a, b);        // UTF-8 byte order is code point order
}

// Newest first, then by kind, then by name.
static int entry_before(const entry_t *a, const entry_t *b) {
    if (a->mtime != b->mtime) {
        return a->mtime > b->mtime;
    }
    if (a->kind != b->kind) {
        return strcmp(KINDS[a->kind].name, KINDS[b->kind].name) < 0;
    }
    return cmp_name(a->name, b->name) < 0;
}

static void sort_entries(entries_t *es) {
    for (size_t i = 1; i < es->n; i++) {         // insertion: stable, small lists
        entry_t t = es->e[i];
        size_t j = i;
        while (j > 0 && entry_before(&t, &es->e[j - 1])) {
            es->e[j] = es->e[j - 1];
            j--;
        }
        es->e[j] = t;
    }
}

static int ends_any(const char *name, size_t n, size_t nexts) {
    for (size_t i = 0; i < nexts; i++) {
        if (ends(name, n, VAULT_EXTS[i])) {
            return 1;
        }
    }
    return 0;
}

// The kind's items in `dir` with their mtimes (moy_files._kind_entries),
// sorted newest first, each entry's kind `kind`.
static int kind_entries(const char *dir, int kind, entries_t *out) {
    entries_t raw;
    int missing;
    memset(out, 0, sizeof *out);
    int rc = list_dir(dir, &raw, &missing);
    if (rc != 0 || missing) {
        return rc;
    }
    const char *ext = KINDS[kind].ext;
    size_t en = strlen(ext), nwhole = kind == DOCS ? NVAULT : 0u;
    for (size_t i = 0; i < raw.n && rc == 0; i++) {
        const char *n = raw.e[i].name;
        size_t nl = strlen(n);
        char *p = cat(dir, "/", n, NULL);
        if (p == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        moy_vol_stat_t st;
        int dir = 0;
        int64_t mt = 0;
        if (moy_fs_stat(p, &st) == 0) {
            dir = st.is_dir;
            mt = st.mtime;
        }
        if (KINDS[kind].folder) {
            if (dir) {
                rc = entries_add(out, n, nl, mt, kind);
            }
        } else if (ends_any(n, nl, nwhole) && !dir) {
            rc = entries_add(out, n, nl, mt, kind);
        } else if (nl > en && memcmp(n + nl - en, ext, en) == 0 && !dir) {
            rc = entries_add(out, n, nl - en, mt, kind);
        }
        str_free(p);
    }
    entries_free(&raw);
    if (rc != 0) {
        entries_free(out);
        return rc;
    }
    sort_entries(out);
    return 0;
}

// Names, each NUL-terminated, into one buffer.
static int pack(const entries_t *es, int with_kind, moy_buf_t *out, uint32_t *count) {
    size_t n = 0;
    for (size_t i = 0; i < es->n; i++) {
        n += strlen(es->e[i].name) + 1u;
        if (with_kind) {
            n += strlen(KINDS[es->e[i].kind].name) + 1u;
        }
    }
    out->p = moy_store_alloc(n + 1u);
    if (out->p == NULL) {
        return MOY_ENOMEM;
    }
    size_t at = 0;
    for (size_t i = 0; i < es->n; i++) {
        if (with_kind) {
            const char *k = KINDS[es->e[i].kind].name;
            memcpy(out->p + at, k, strlen(k) + 1u);
            at += strlen(k) + 1u;
        }
        size_t k = strlen(es->e[i].name) + 1u;
        memcpy(out->p + at, es->e[i].name, k);
        at += k;
    }
    out->n = n;
    *count = (uint32_t)es->n;
    return 0;
}

static int in_order(const char *n) {
    for (size_t i = 0; i < NORDER; i++) {
        if (strcmp(n, PROJECT_ORDER[i]) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static void sort_names(entries_t *es) {
    for (size_t i = 1; i < es->n; i++) {
        entry_t t = es->e[i];
        size_t j = i;
        while (j > 0 && cmp_name(t.name, es->e[j - 1].name) < 0) {
            es->e[j] = es->e[j - 1];
            j--;
        }
        es->e[j] = t;
    }
}

// One project's files: PROJECT_ORDER first, the rest sorted, then each
// subfolder's items as `<dir>/<name>`.
static int project_list(const char *root, const char *kind, entries_t *out) {
    char *d = project_dir(root, kind);
    if (d == NULL) {
        return MOY_ENOMEM;
    }
    memset(out, 0, sizeof *out);
    entries_t raw, flat;
    int missing;
    int rc = list_dir(d, &raw, &missing);
    if (rc != 0 || missing) {
        str_free(d);
        return rc;
    }
    sort_names(&raw);
    memset(&flat, 0, sizeof flat);
    for (size_t i = 0; i < raw.n && rc == 0; i++) {
        const char *n = raw.e[i].name;
        size_t nl = strlen(n);
        if (ends(n, nl, ".bak") || ends(n, nl, ".tmp")) {
            continue;
        }
        char *p = cat(d, "/", n, NULL);
        if (p == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        if (!is_dir(p)) {
            rc = entries_add(&flat, n, nl, 0, 0);
        }
        str_free(p);
    }
    for (size_t k = 0; k < NORDER && rc == 0; k++) {
        for (size_t i = 0; i < flat.n; i++) {
            if (strcmp(flat.e[i].name, PROJECT_ORDER[k]) == 0) {
                rc = entries_add(out, PROJECT_ORDER[k], strlen(PROJECT_ORDER[k]), 0, 0);
                break;
            }
        }
    }
    for (size_t i = 0; i < flat.n && rc == 0; i++) {
        if (in_order(flat.e[i].name) < 0) {
            rc = entries_add(out, flat.e[i].name, strlen(flat.e[i].name), 0, 0);
        }
    }
    for (size_t s = 0; s < 2u && rc == 0; s++) {
        char *sd = cat(d, "/", PROJECT_SUBDIRS[s][0], NULL);
        entries_t kids;
        if (sd == NULL) {
            rc = MOY_ENOMEM;
            break;
        }
        rc = list_dir(sd, &kids, &missing);
        str_free(sd);
        if (rc != 0 || missing) {
            continue;
        }
        sort_names(&kids);
        const char *ext = PROJECT_SUBDIRS[s][1];
        size_t en = strlen(ext);
        for (size_t i = 0; i < kids.n && rc == 0; i++) {
            const char *n = kids.e[i].name;
            size_t nl = strlen(n);
            if (nl >= en && memcmp(n + nl - en, ext, en) == 0) {
                char *both = cat(PROJECT_SUBDIRS[s][0], "/", n, NULL);
                rc = both ? entries_add(out, both, strlen(both), 0, 0) : MOY_ENOMEM;
                str_free(both);
            }
        }
        entries_free(&kids);
    }
    entries_free(&flat);
    entries_free(&raw);
    str_free(d);
    if (rc != 0) {
        entries_free(out);
    }
    return rc;
}

int moy_uf_list(const char *root, const char *kind, moy_buf_t *out,
                uint32_t *count) {
    out->p = NULL;
    out->n = 0;
    *count = 0;
    entries_t es;
    int rc;
    if (*moy_uf_project_folder(kind)) {
        rc = project_list(root, kind, &es);
    } else {
        int k = kind_arg(kind, &rc);
        if (k < 0) {
            return rc;
        }
        char *d = kind_dir(root, k);
        if (d == NULL) {
            return MOY_ENOMEM;
        }
        rc = kind_entries(d, k, &es);
        str_free(d);
    }
    if (rc == 0) {
        rc = pack(&es, 0, out, count);
        entries_free(&es);
    }
    return rc;
}

int moy_uf_count(const char *root, const char *kind, uint32_t *count) {
    *count = 0;
    int rc;
    if (*moy_uf_project_folder(kind)) {
        entries_t es;
        rc = project_list(root, kind, &es);
        if (rc == 0) {
            *count = (uint32_t)es.n;
            entries_free(&es);
        }
        return rc;
    }
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    char *d = kind_dir(root, k);
    if (d == NULL) {
        return MOY_ENOMEM;
    }
    entries_t raw;
    int missing;
    rc = list_dir(d, &raw, &missing);
    if (rc == 0 && !missing) {
        const char *ext = KINDS[k].ext;
        size_t en = strlen(ext), nwhole = k == DOCS ? NVAULT : 0u;
        for (size_t i = 0; i < raw.n && rc == 0; i++) {
            const char *n = raw.e[i].name;
            size_t nl = strlen(n);
            if (KINDS[k].folder) {
                char *p = cat(d, "/", n, NULL);
                if (p == NULL) {
                    rc = MOY_ENOMEM;
                    break;
                }
                *count += (uint32_t)is_dir(p);
                str_free(p);
            } else if (ends_any(n, nl, nwhole)
                       || (nl > en && memcmp(n + nl - en, ext, en) == 0)) {
                (*count)++;
            }
        }
        entries_free(&raw);
    }
    str_free(d);
    return rc;
}

// -- load and save -------------------------------------------------------------------------

static int fs_none(int rc) {
    return rc == MOY_ENOMEM ? rc : MOY_UF_NONE;
}

int moy_uf_load(const char *root, const char *kind, const char *name,
                moy_buf_t *out, int *binary) {
    out->p = NULL;
    out->n = 0;
    *binary = 0;
    int rc;
    if (*moy_uf_project_folder(kind)) {
        char *p = project_file(root, kind, name, &rc);
        if (p == NULL) {
            return rc;
        }
        if (strcmp(name, COVER) == 0) {
            *binary = 1;
            rc = moy_fs_read_file(p, MOY_UF_COVER_MAX, out);
        } else {
            rc = moy_fs_read(p, NULL, out);
        }
        str_free(p);
        return rc == 0 ? 0 : fs_none(rc);
    }
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    if (KINDS[k].folder) {
        return MOY_UF_NONE;
    }
    char *p = file_path(root, k, name);
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    rc = moy_fs_read(p, NULL, out);         // a publish a cut left torn reads healed
    str_free(p);
    return rc == 0 ? 0 : fs_none(rc);
}

static int64_t s_clock = -1;

void moy_uf_clock_fix(int64_t ts) {
    s_clock = ts;
}

static int name_out_set(char *name_out, const char *name) {
    size_t n = strlen(name);
    if (n > MOY_UF_NAME_MAX) {
        return MOY_UF_BAD;
    }
    memcpy(name_out, name, n + 1u);
    return 0;
}

static int project_save(const char *root, const char *kind, const char *name,
                        const char *data, size_t n, char *name_out) {
    int rc;
    char *p = project_file(root, kind, name, &rc);
    if (p == NULL) {
        return rc;
    }
    rc = name_out_set(name_out, name);
    if (rc == 0 && strcmp(name, COVER) == 0) {
        rc = moy_fs_write_bytes(p, data, n);
    } else if (rc == 0) {
        rc = moy_fs_publish(p, data, n);
        if (rc == 0) {
            // The journal is the project's undo; a failed append loses one
            // undo step and never the save.
            char *d = project_dir(root, kind);
            uint32_t seq;
            if (d != NULL) {
                int64_t ts = s_clock >= 0 ? s_clock : (int64_t)time(NULL);
                moy_journal_append(d, name, data, n, -1, NULL, 0, ts, &seq);
                str_free(d);
            }
        }
    }
    str_free(p);
    return rc;
}

int moy_uf_save(const char *root, const char *kind, const char *name,
                const char *data, size_t n, char *name_out) {
    if (*moy_uf_project_folder(kind)) {
        return project_save(root, kind, name, data, n, name_out);
    }
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    if (KINDS[k].folder) {
        return MOY_UF_BAD;          // a folder item is written by its own tools
    }
    rc = slug_item(k, name, name_out);
    if (rc == 0) {
        rc = ensure_kind_dir(root, k);
    }
    char *p = rc == 0 ? file_path(root, k, name_out) : NULL;
    if (rc == 0 && p == NULL) {
        rc = MOY_ENOMEM;
    }
    if (rc == 0) {
        rc = moy_fs_publish(p, data, n);
    }
    str_free(p);
    return rc;
}

// -- naming ---------------------------------------------------------------------------------

typedef char *(*path_fn)(const char *root, int kind, const char *name);

// `name` if it is free at `path`, else name_2, name_3, ... before a vault
// extension: the one collision probe every move rides on.
static int unique_name(const char *root, int kind, const char *name, path_fn path,
                       char *out) {
    char *p = path(root, kind, name);
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    int taken = exists(p);
    str_free(p);
    if (!taken) {
        return name_out_set(out, name);
    }
    size_t n = strlen(name), e = whole_ext(kind, name);
    char stem[MOY_UF_NAME_MAX + 1];
    if (n > MOY_UF_NAME_MAX) {
        return MOY_UF_BAD;
    }
    memcpy(stem, name, n - e);
    stem[n - e] = 0;
    for (unsigned long i = 2;; i++) {
        char num[16];
        snprintf(num, sizeof num, "_%lu", i);
        if (n + strlen(num) > MOY_UF_NAME_MAX) {
            return MOY_UF_BAD;
        }
        char *cand = cat(stem, num, name + n - e, NULL);
        if (cand == NULL) {
            return MOY_ENOMEM;
        }
        p = path(root, kind, cand);
        if (p == NULL) {
            str_free(cand);
            return MOY_ENOMEM;
        }
        taken = exists(p);
        str_free(p);
        if (!taken) {
            int rc = name_out_set(out, cand);
            str_free(cand);
            return rc;
        }
        str_free(cand);
    }
}

int moy_uf_new_name(const char *root, const char *kind, const char *base,
                    char *name_out) {
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    char b[MOY_UF_NAME_MAX + 1];
    if (base != NULL && *base) {
        if (strlen(base) > MOY_UF_NAME_MAX - 12u) {
            return MOY_UF_BAD;
        }
        slug_into(base, strlen(base), b);
    } else {
        strcpy(b, KINDS[k].base);
    }
    for (unsigned long i = 1;; i++) {
        char num[16];
        snprintf(num, sizeof num, "_%lu", i);
        char *cand = cat(b, num, NULL);
        char *p = cand ? file_path(root, k, cand) : NULL;
        if (p == NULL) {
            str_free(cand);
            return MOY_ENOMEM;
        }
        int taken = exists(p);
        str_free(p);
        if (!taken) {
            rc = name_out_set(name_out, cand);
            str_free(cand);
            return rc;
        }
        str_free(cand);
    }
}

int moy_uf_free_name(const char *root, const char *kind, const char *title,
                     char *name_out) {
    if (!has_alnum(title)) {
        return moy_uf_new_name(root, kind, NULL, name_out);
    }
    int rc;
    int k = moy_uf_kind_of(kind);
    char s[MOY_UF_NAME_MAX + 1];
    rc = slug_item(k, title, s);
    if (rc != 0) {
        return rc;
    }
    if (k < 0) {
        return MOY_UF_BAD;
    }
    return unique_name(root, k, s, file_path, name_out);
}

// -- moves ----------------------------------------------------------------------------------

static int path_pair(char **a, char **b) {
    if (*a == NULL || *b == NULL) {
        str_free(*a);
        str_free(*b);
        *a = *b = NULL;
        return MOY_ENOMEM;
    }
    return 0;
}

int moy_uf_rename(const char *root, const char *kind, const char *name,
                  const char *title, char *name_out) {
    if (!has_alnum(title)) {
        return name_out_set(name_out, name);
    }
    int k = moy_uf_kind_of(kind);
    char s[MOY_UF_NAME_MAX + 1];
    int rc = slug_item(k, title, s);
    if (rc != 0) {
        return rc;
    }
    if (strcmp(s, name) == 0) {
        return name_out_set(name_out, name);
    }
    if (k < 0) {
        return MOY_UF_BAD;
    }
    rc = unique_name(root, k, s, file_path, name_out);
    if (rc != 0) {
        return rc;
    }
    char *src = file_path(root, k, name), *dst = file_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    rc = moy_fs_rename(src, dst);
    if (rc == 0) {
        moy_fs_forget_bak(src);
        rc = ensure_history_dir(root, k);
    }
    str_free(src);
    str_free(dst);
    if (rc != 0) {
        return rc;
    }
    src = history_path(root, k, name);
    dst = history_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    sidecar_move(src, dst);
    str_free(src);
    str_free(dst);
    return 0;
}

static int copy_tree(const char *src, const char *dst) {
    moy_fs_mkdir(dst);
    entries_t raw;
    int missing;
    int rc = list_dir(src, &raw, &missing);
    if (rc != 0) {
        return rc;
    }
    if (missing) {
        return missing;
    }
    for (size_t i = 0; i < raw.n && rc == 0; i++) {
        char *s = cat(src, "/", raw.e[i].name, NULL);
        char *d = cat(dst, "/", raw.e[i].name, NULL);
        if ((rc = path_pair(&s, &d)) != 0) {
            break;
        }
        rc = is_dir(s) ? copy_tree(s, d) : copy_file(s, d);
        str_free(s);
        str_free(d);
    }
    entries_free(&raw);
    return rc;
}

int moy_uf_duplicate(const char *root, const char *kind, const char *name,
                     char *name_out) {
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    rc = unique_name(root, k, name, file_path, name_out);
    if (rc != 0) {
        return rc;
    }
    char *src = file_path(root, k, name), *dst = file_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    if (KINDS[k].folder) {
        rc = copy_tree(src, dst);
    } else {
        moy_buf_t b;
        rc = moy_fs_read(src, NULL, &b);
        if (rc == 0) {
            rc = moy_fs_publish(dst, b.p, b.n);
            moy_buf_free(&b);
        }
    }
    str_free(src);
    str_free(dst);
    if (rc == 0) {
        rc = ensure_history_dir(root, k);
    }
    if (rc != 0) {
        return rc;
    }
    src = history_path(root, k, name);
    dst = history_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    if (exists(src)) {
        copy_file(src, dst);
    }
    str_free(src);
    str_free(dst);
    return 0;
}

int moy_uf_delete(const char *root, const char *kind, const char *name,
                  char *name_out) {
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    rc = mkdirs(root, 0, TRASH_DIR, KINDS[k].name, NULL);
    if (rc == 0) {
        rc = unique_name(root, k, name, trash_path, name_out);
    }
    if (rc != 0) {
        return rc;
    }
    char *src = file_path(root, k, name), *dst = trash_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    rc = moy_fs_rename(src, dst);
    if (rc == 0) {
        moy_fs_forget_bak(src);     // the backup does not follow it
        rc = ensure_history_trash_dir(root, k);
    }
    str_free(src);
    str_free(dst);
    if (rc != 0) {
        return rc;
    }
    src = history_path(root, k, name);
    dst = history_trash_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    sidecar_move(src, dst);
    str_free(src);
    str_free(dst);
    return moy_uf_prune_trash(root, MOY_UF_TRASH_KEEP);
}

int moy_uf_restore(const char *root, const char *kind, const char *name,
                   char *name_out) {
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    rc = ensure_kind_dir(root, k);
    if (rc == 0) {
        rc = unique_name(root, k, name, file_path, name_out);
    }
    if (rc != 0) {
        return rc;
    }
    char *src = trash_path(root, k, name), *dst = file_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    rc = moy_fs_rename(src, dst);
    if (rc == 0) {
        moy_fs_forget_bak(src);
        rc = ensure_history_dir(root, k);
    }
    str_free(src);
    str_free(dst);
    if (rc != 0) {
        return rc;
    }
    src = history_trash_path(root, k, name);
    dst = history_path(root, k, name_out);
    if ((rc = path_pair(&src, &dst)) != 0) {
        return rc;
    }
    sidecar_move(src, dst);
    str_free(src);
    str_free(dst);
    return 0;
}

// -- the trash ------------------------------------------------------------------------------

static int trash_entries(const char *root, entries_t *out) {
    memset(out, 0, sizeof *out);
    for (int k = 0; k < MOY_UF_KINDS; k++) {
        char *d = trash_dir(root, k);
        entries_t es;
        if (d == NULL) {
            entries_free(out);
            return MOY_ENOMEM;
        }
        int rc = kind_entries(d, k, &es);
        str_free(d);
        for (size_t i = 0; i < es.n && rc == 0; i++) {
            rc = entries_add(out, es.e[i].name, strlen(es.e[i].name), es.e[i].mtime, k);
        }
        entries_free(&es);
        if (rc != 0) {
            entries_free(out);
            return rc;
        }
    }
    sort_entries(out);
    return 0;
}

int moy_uf_trash_list(const char *root, moy_buf_t *out, uint32_t *count) {
    out->p = NULL;
    out->n = 0;
    *count = 0;
    entries_t es;
    int rc = trash_entries(root, &es);
    if (rc == 0) {
        rc = pack(&es, 1, out, count);
        entries_free(&es);
    }
    return rc;
}

static void remove_trash_entry(const char *root, int k, const char *name) {
    char *p = trash_path(root, k, name);
    char *h = history_trash_path(root, k, name);
    if (p != NULL) {
        if (is_dir(p)) {
            moy_cat_rmtree(p);
        } else {
            moy_fs_remove(p);
        }
        moy_fs_forget_bak(p);
    }
    if (h != NULL) {
        moy_fs_remove(h);
    }
    str_free(p);
    str_free(h);
}

int moy_uf_prune_trash(const char *root, uint32_t keep) {
    size_t total = 0;
    for (int k = 0; k < MOY_UF_KINDS; k++) {
        char *d = trash_dir(root, k);
        entries_t raw;
        int missing;
        if (d == NULL) {
            return MOY_ENOMEM;
        }
        int rc = list_dir(d, &raw, &missing);
        str_free(d);
        if (rc != 0) {
            return rc;
        }
        total += raw.n;
        entries_free(&raw);
    }
    if (total <= keep) {
        return 0;
    }
    entries_t es;
    int rc = trash_entries(root, &es);
    if (rc != 0) {
        return rc;
    }
    for (size_t i = keep; i < es.n; i++) {
        remove_trash_entry(root, es.e[i].kind, es.e[i].name);
    }
    entries_free(&es);
    return 0;
}

// -- the sidecars ----------------------------------------------------------------------------

// One good record of a sidecar: its span, and whether it is a keyframe.
typedef struct {
    const char *p, *end;
    int kf;
} rec_t;

typedef struct {
    moy_buf_t text;
    rec_t *r;
    size_t n, cap;
} recs_t;

static void recs_free(recs_t *rs) {
    moy_buf_free(&rs->text);
    if (rs->r != NULL) {
        moy_store_free(rs->r, rs->cap * sizeof(rec_t));
    }
    rs->r = NULL;
    rs->n = rs->cap = 0;
}

// The sidecar's records in file order: a line that is not one JSON object
// whose "t" is "kf" or "seg" is dropped, every good one around it kept. A
// sidecar that is not there is no records.
static int recs_load(const char *path, recs_t *rs) {
    memset(rs, 0, sizeof *rs);
    int rc = moy_fs_read_file(path, (size_t)-1, &rs->text);
    if (rc != 0) {
        rs->text.p = NULL;
        rs->text.n = 0;
        return rc == MOY_ENOMEM ? rc : 0;
    }
    const char *p = rs->text.p, *end = rs->text.p + rs->text.n;
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *le = nl != NULL ? nl : end;
        const char *v = moy_json_ws(p, le);
        const char *ve = v < le ? moy_json_value(v, le, 1u) : NULL;
        const char *t, *te;
        if (ve != NULL && moy_json_ws(ve, le) == le && *v == '{'
            && moy_json_get(v, ve, "t", &t, &te)) {
            int kf = moy_json_str_is(t, te, "kf", 2);
            if (kf || moy_json_str_is(t, te, "seg", 3)) {
                if (rs->n == rs->cap) {
                    size_t cap = rs->cap ? rs->cap * 2u : 16u;
                    rec_t *r = moy_store_alloc(cap * sizeof(rec_t));
                    if (r == NULL) {
                        recs_free(rs);
                        return MOY_ENOMEM;
                    }
                    if (rs->r != NULL) {
                        memcpy(r, rs->r, rs->n * sizeof(rec_t));
                        moy_store_free(rs->r, rs->cap * sizeof(rec_t));
                    }
                    rs->r = r;
                    rs->cap = cap;
                }
                rs->r[rs->n].p = v;
                rs->r[rs->n].end = ve;
                rs->r[rs->n].kf = kf;
                rs->n++;
            }
        }
        p = nl != NULL ? nl + 1 : end;
    }
    return 0;
}

static ptrdiff_t last_kf(const recs_t *rs) {
    ptrdiff_t last = -1;
    for (size_t i = 0; i < rs->n; i++) {
        if (rs->r[i].kf) {
            last = (ptrdiff_t)i;
        }
    }
    return last;
}

// A growing answer.
typedef struct {
    char *p;
    size_t n, cap;
    int rc;
} sink_t;

static void sink_put(sink_t *s, const char *p, size_t n) {
    if (s->rc != 0) {
        return;
    }
    if (s->n + n + 1u > s->cap) {
        size_t cap = s->cap ? s->cap : 256u;
        while (cap < s->n + n + 1u) {
            cap *= 2u;
        }
        char *q = moy_store_alloc(cap);
        if (q == NULL) {
            s->rc = MOY_ENOMEM;
            return;
        }
        if (s->p != NULL) {
            memcpy(q, s->p, s->n);
            moy_store_free(s->p, s->cap);
        }
        s->p = q;
        s->cap = cap;
    }
    memcpy(s->p + s->n, p, n);
    s->n += n;
    s->p[s->n] = 0;
}

static void sink_s(sink_t *s, const char *z) {
    sink_put(s, z, strlen(z));
}

// The sink as a moy_buf_t (moy_buf_free frees `n + 1` bytes).
static int sink_take(sink_t *s, moy_buf_t *out) {
    if (s->rc != 0) {
        if (s->p != NULL) {
            moy_store_free(s->p, s->cap);
        }
        return s->rc;
    }
    if (s->p == NULL) {
        sink_put(s, "", 0);
        if (s->rc != 0) {
            return s->rc;
        }
    }
    out->p = moy_store_alloc(s->n + 1u);
    if (out->p == NULL) {
        moy_store_free(s->p, s->cap);
        return MOY_ENOMEM;
    }
    memcpy(out->p, s->p, s->n + 1u);
    out->n = s->n;
    moy_store_free(s->p, s->cap);
    return 0;
}

// The sidecar of an item verb: the path, or NULL with `*rc` set (BAD for a
// kind that is none; a project kind is NONE: it has no sidecar).
static char *sidecar(const char *root, const char *kind, const char *name, int *rc) {
    if (*moy_uf_project_folder(kind)) {
        *rc = MOY_UF_NONE;
        return NULL;
    }
    int k = kind_arg(kind, rc);
    if (k < 0) {
        return NULL;
    }
    char *p = history_path(root, k, name);
    *rc = p != NULL ? 0 : MOY_ENOMEM;
    return p;
}

static int empty_array(moy_buf_t *out) {
    sink_t s = { NULL, 0, 0, 0 };
    sink_s(&s, "[]");
    return sink_take(&s, out);
}

int moy_uf_history(const char *root, const char *kind, const char *name,
                   moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    int rc;
    char *path = sidecar(root, kind, name, &rc);
    if (path == NULL) {
        return rc == MOY_UF_NONE ? empty_array(out) : rc;
    }
    recs_t rs;
    rc = recs_load(path, &rs);
    str_free(path);
    if (rc != 0) {
        return rc;
    }
    sink_t s = { NULL, 0, 0, 0 };
    sink_s(&s, "[");
    for (size_t i = 0; i < rs.n; i++) {
        if (i) {
            sink_s(&s, ", ");
        }
        sink_put(&s, rs.r[i].p, (size_t)(rs.r[i].end - rs.r[i].p));
    }
    sink_s(&s, "]");
    recs_free(&rs);
    return sink_take(&s, out);
}

int moy_uf_history_ops(const char *root, const char *kind, const char *name,
                       moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    int rc;
    char *path = sidecar(root, kind, name, &rc);
    if (path == NULL) {
        return rc == MOY_UF_NONE ? empty_array(out) : rc;
    }
    recs_t rs;
    rc = recs_load(path, &rs);
    str_free(path);
    if (rc != 0) {
        return rc;
    }
    sink_t s = { NULL, 0, 0, 0 };
    int first = 1;
    sink_s(&s, "[");
    for (size_t i = (size_t)(last_kf(&rs) + 1); i < rs.n; i++) {
        const char *v, *ve;
        if (rs.r[i].kf || !moy_json_get(rs.r[i].p, rs.r[i].end, "ops", &v, &ve)
            || *v != '[') {
            continue;
        }
        moy_json_iter_t it;
        const char *k, *ke, *e, *ee;
        moy_json_iter(&it, v, ve);
        while (moy_json_next(&it, &k, &ke, &e, &ee)) {
            if (!first) {
                sink_s(&s, ", ");
            }
            first = 0;
            sink_put(&s, e, (size_t)(ee - e));
        }
    }
    sink_s(&s, "]");
    recs_free(&rs);
    return sink_take(&s, out);
}

// Each kept record as json.dumps writes it, one a line.
static int canon_line(sink_t *s, const rec_t *r) {
    size_t need = moy_json_canon(r->p, r->end, NULL, 0);
    if (need == MOY_JSON_DEEP) {
        return MOY_UF_BAD;
    }
    char *tmp = moy_store_alloc(need + 1u);
    if (tmp == NULL) {
        return MOY_ENOMEM;
    }
    moy_json_canon(r->p, r->end, tmp, need);
    sink_put(s, tmp, need);
    sink_s(s, "\n");
    moy_store_free(tmp, need + 1u);
    return 0;
}

static int prune(const char *path, uint32_t keep, uint32_t *dropped) {
    *dropped = 0;
    recs_t rs;
    int rc = recs_load(path, &rs);
    if (rc != 0 || rs.n == 0) {
        return rc;
    }
    ptrdiff_t kf = last_kf(&rs);
    size_t from = kf >= 0 ? (size_t)kf + 1u : 0u;
    size_t segs = 0;
    for (size_t i = from; i < rs.n; i++) {
        segs += !rs.r[i].kf;
    }
    size_t skip = keep && segs > keep ? segs - keep : 0u;
    size_t kept = (kf >= 0 ? 1u : 0u) + segs - skip;
    if (kept == rs.n) {
        recs_free(&rs);
        return 0;
    }
    sink_t s = { NULL, 0, 0, 0 };
    if (kf >= 0) {
        rc = canon_line(&s, &rs.r[kf]);
    }
    for (size_t i = from, seg = 0; i < rs.n && rc == 0; i++) {
        if (rs.r[i].kf) {
            continue;
        }
        if (seg++ >= skip) {
            rc = canon_line(&s, &rs.r[i]);
        }
    }
    moy_buf_t text = { NULL, 0 };
    if (rc == 0) {
        rc = sink_take(&s, &text);
    } else if (s.p != NULL) {
        moy_store_free(s.p, s.cap);
    }
    if (rc == 0) {
        rc = moy_fs_publish(path, text.p, text.n);
        moy_buf_free(&text);
    }
    if (rc == 0) {
        *dropped = (uint32_t)(rs.n - kept);
    }
    recs_free(&rs);
    return rc;
}

// One JSON value as json.dumps(json.loads(text)) writes it: BAD for text that
// is not one.
static int canon_text(const char *text, size_t n, moy_buf_t *out) {
    const char *end = text + n;
    const char *v = moy_json_ws(text, end);
    const char *ve = v < end ? moy_json_value(v, end, 1u) : NULL;
    if (ve == NULL || moy_json_ws(ve, end) != end) {
        return MOY_UF_BAD;
    }
    size_t need = moy_json_canon(v, ve, NULL, 0);
    if (need == MOY_JSON_DEEP) {
        return MOY_UF_BAD;
    }
    out->p = moy_store_alloc(need + 1u);
    if (out->p == NULL) {
        return MOY_ENOMEM;
    }
    moy_json_canon(v, ve, out->p, need);
    out->n = need;
    return 0;
}

static uint32_t s_prune_fails;

uint32_t moy_uf_prune_fails(void) {
    return s_prune_fails;
}

int moy_uf_history_commit(const char *root, const char *kind, const char *name,
                          const char *ops, size_t ops_n, const char *kf,
                          size_t kf_n, int *prune_err) {
    *prune_err = 0;
    if (*moy_uf_project_folder(kind) || (ops == NULL && kf == NULL)) {
        return 0;                   // a project journals its saves itself
    }
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    rc = ensure_history_dir(root, k);
    char *path = rc == 0 ? history_path(root, k, name) : NULL;
    if (rc == 0 && path == NULL) {
        rc = MOY_ENOMEM;
    }
    // Each as json.dumps writes it, whatever the binding's own json wrote.
    moy_buf_t cops = { NULL, 0 }, ckf = { NULL, 0 };
    if (rc == 0 && ops != NULL) {
        rc = canon_text(ops, ops_n, &cops);
    }
    if (rc == 0 && kf != NULL) {
        rc = canon_text(kf, kf_n, &ckf);
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    if (rc == 0) {
        rc = moy_vol_at(path, &v, &rest);
    }
    if (rc == 0) {
        rc = moy_vol_open(&v, rest, MOY_VOL_APPEND, &f);
    }
    if (rc == 0) {
        static const char KF[] = "{\"t\": \"kf\", \"doc\": ";
        static const char SEG[] = "{\"t\": \"seg\", \"ops\": ";
        if (kf != NULL) {
            rc = moy_vol_write(f, KF, sizeof KF - 1u);
            rc = rc ? rc : moy_vol_write(f, ckf.p, ckf.n);
            rc = rc ? rc : moy_vol_write(f, "}\n", 2);
        }
        if (rc == 0 && ops != NULL) {
            rc = moy_vol_write(f, SEG, sizeof SEG - 1u);
            rc = rc ? rc : moy_vol_write(f, cops.p, cops.n);
            rc = rc ? rc : moy_vol_write(f, "}\n", 2);
        }
        int crc = moy_vol_close(f);
        rc = rc ? rc : crc;
    }
    if (rc == 0) {
        uint32_t dropped;
        int prc = prune(path, MOY_UF_HISTORY_KEEP, &dropped);
        if (prc != 0) {
            s_prune_fails++;
            *prune_err = prc;
        }
    }
    moy_buf_free(&cops);
    moy_buf_free(&ckf);
    str_free(path);
    return rc;
}

int moy_uf_prune_history(const char *root, const char *kind, const char *name,
                         uint32_t keep, uint32_t *dropped) {
    *dropped = 0;
    int rc;
    char *path = sidecar(root, kind, name, &rc);
    if (path == NULL) {
        return rc == MOY_UF_NONE ? 0 : rc;
    }
    rc = prune(path, keep, dropped);
    str_free(path);
    return rc;
}

int moy_uf_clear_history(const char *root, const char *kind, const char *name) {
    int rc;
    int k = kind_arg(kind, &rc);
    if (k < 0) {
        return rc;
    }
    char *path = history_path(root, k, name);
    if (path == NULL) {
        return MOY_ENOMEM;
    }
    moy_fs_remove(path);
    str_free(path);
    return 0;
}

// -- provenance --------------------------------------------------------------------------------

// The next code point of UTF-8 `p` (a byte that starts none is itself).
static uint32_t next_cp(const unsigned char **p, const unsigned char *end) {
    const unsigned char *s = *p;
    uint32_t c = *s++;
    int more = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : c >= 0xC0 ? 1 : 0;
    if (more && end - s >= more) {
        c &= 0x3Fu >> more;
        for (int i = 0; i < more; i++) {
            c = c << 6 | (*s++ & 0x3Fu);
        }
    }
    *p = s;
    return c;
}

uint32_t moy_uf_sig(const char *text, size_t n) {
    if (n == 0) {
        return 0;
    }
    const unsigned char *p = (const unsigned char *)text, *end = p + n;
    uint32_t cps[64];
    size_t len = 0, ring = 0;
    uint32_t s = 0;
    while (p < end) {
        uint32_t c = next_cp(&p, end);
        if (len < 64u) {
            s += c;
        }
        cps[ring] = c;
        ring = (ring + 1u) & 63u;
        len++;
    }
    size_t tail = len < 64u ? len : 64u;
    size_t at = (ring + 64u - tail) & 63u;
    for (size_t i = 0; i < tail; i++) {
        s = (s * 3u + cps[(at + i) & 63u]) & 0xFFFFFFu;
    }
    return (uint32_t)((uint64_t)len * 2654435761u + s);
}

// `blob`'s object span: 1, or 0 when it is not exactly one JSON object.
static int object_of(const char *blob, size_t n, const char **v, const char **ve) {
    const char *end = blob + n;
    *v = moy_json_ws(blob, end);
    if (*v >= end || **v != '{') {
        return 0;
    }
    *ve = moy_json_value(*v, end, 1u);
    return *ve != NULL && moy_json_ws(*ve, end) == end;
}

static int canon_set(const char *v, const char *ve, const char *key,
                     const char *val, moy_buf_t *out) {
    size_t need = moy_json_canon_set(v, ve, key, val, NULL, 0);
    if (need == MOY_JSON_DEEP) {
        return MOY_UF_NONE;
    }
    out->p = moy_store_alloc(need + 1u);
    if (out->p == NULL) {
        return MOY_ENOMEM;
    }
    moy_json_canon_set(v, ve, key, val, out->p, need);
    out->n = need;
    return 0;
}

int moy_uf_stamp(const char *blob, size_t n, const char *kind, const char *name,
                 uint32_t sig, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    const char *v, *ve;
    if (!object_of(blob, n, &v, &ve)) {
        return MOY_UF_NONE;
    }
    char *src = cat(kind, "/", name, NULL);
    if (src == NULL) {
        return MOY_ENOMEM;
    }
    size_t sn = strlen(src);
    size_t qn = moy_json_quote(src, sn, NULL, 0);
    char *q = str_new(qn);
    if (q == NULL) {
        str_free(src);
        return MOY_ENOMEM;
    }
    moy_json_quote(src, sn, q, qn);
    q[qn] = 0;
    str_free(src);
    moy_buf_t one;
    int rc = canon_set(v, ve, "src", q, &one);
    str_free(q);
    if (rc != 0) {
        return rc;
    }
    char num[16];
    snprintf(num, sizeof num, "%lu", (unsigned long)sig);
    rc = canon_set(one.p, one.p + one.n, "sig", num, out);
    moy_buf_free(&one);
    return rc;
}

int moy_uf_provenance(const char *blob, size_t n, moy_buf_t *src, int64_t *sig) {
    src->p = NULL;
    src->n = 0;
    *sig = 0;
    const char *v, *ve, *s, *se;
    if (!object_of(blob, n, &v, &ve) || !moy_json_get(v, ve, "src", &s, &se)
        || moy_json_kind(s, se) != MOY_JSON_STR) {
        return MOY_UF_NONE;
    }
    size_t sn = moy_json_strlen(s, se);
    src->p = moy_store_alloc(sn + 1u);
    if (src->p == NULL) {
        return MOY_ENOMEM;
    }
    src->n = moy_json_str(s, se, src->p);
    src->p[src->n] = 0;
    if (memchr(src->p, '/', src->n) == NULL) {
        moy_buf_free(src);
        return MOY_UF_NONE;
    }
    const char *g, *ge;
    int64_t got;
    if (moy_json_get(v, ve, "sig", &g, &ge) && moy_json_int(g, ge, &got) == 1) {
        *sig = got;
    }
    return 0;
}

// -- the codecs ------------------------------------------------------------------------------

// The console palette (device_canvas.MOY64_RGB): R, G, B, 64 entries.
static const uint8_t MOY64[192] = {
    0x00, 0x00, 0x00, 0x1d, 0x2b, 0x53, 0x7e, 0x25, 0x53, 0x00, 0x87, 0x51,
    0xab, 0x52, 0x36, 0x5f, 0x57, 0x4f, 0xc2, 0xc3, 0xc7, 0xff, 0xf1, 0xe8,
    0xff, 0x00, 0x4d, 0xff, 0xa3, 0x00, 0xff, 0xec, 0x27, 0x00, 0xe4, 0x36,
    0x29, 0xad, 0xff, 0x83, 0x76, 0x9c, 0xff, 0x77, 0xa8, 0xff, 0xcc, 0xaa,
    0xe6, 0xa1, 0xa1, 0xe6, 0xb9, 0xa1, 0xe6, 0xd2, 0xa1, 0xae, 0xe6, 0xa1,
    0xa1, 0xe6, 0xd1, 0xa1, 0xd1, 0xe6, 0xa1, 0xb4, 0xe6, 0xb7, 0xa1, 0xe6,
    0xd8, 0xa1, 0xe6, 0xe6, 0xa1, 0xca, 0xe6, 0xa1, 0xb1, 0xa1, 0xe6, 0xe6,
    0x8c, 0x6b, 0x4d, 0xa8, 0x8d, 0x79, 0x66, 0x50, 0x2e, 0xcc, 0xbf, 0x9f,
    0x50, 0x73, 0x47, 0x57, 0x38, 0x2b, 0x85, 0x7a, 0x6d, 0x9e, 0x93, 0x6a,
    0xe0, 0x58, 0x2d, 0xe0, 0x99, 0x2d, 0xd2, 0xe0, 0x2d, 0x30, 0xe0, 0x2d,
    0x2d, 0xe0, 0x8a, 0x2d, 0xe0, 0xe0, 0x2d, 0x8a, 0xe0, 0x51, 0x2d, 0xe0,
    0xa7, 0x2d, 0xe0, 0xe0, 0x2d, 0xc4, 0xe0, 0x2d, 0x78, 0x71, 0xe0, 0x2d,
    0xda, 0xe1, 0xf2, 0xaf, 0xb6, 0xc7, 0x82, 0x88, 0x99, 0x58, 0x5d, 0x6b,
    0xe6, 0xdc, 0xd3, 0x8c, 0x85, 0x7e, 0x39, 0x3d, 0x47, 0x29, 0x29, 0x29,
    0x66, 0x2e, 0x2e, 0x66, 0x49, 0x2e, 0x2e, 0x66, 0x55, 0x2e, 0x55, 0x66,
    0x2e, 0x3e, 0x66, 0x40, 0x2e, 0x66, 0x66, 0x2e, 0x60, 0x66, 0x2e, 0x45,
};

const uint8_t *moy_uf_palette(void) {
    return MOY64;
}

// A ZLIB stream at MOY_UF_WBITS, as the boards' `deflate.DeflateIO` writes
// it: the header, uzlib's LZ77 over a window of its own, the Adler-32.
typedef struct {
    sink_t *out;
    uzlib_lz77_state_t lz;
    uint32_t a, b;
    uint8_t hist[1u << MOY_UF_WBITS];
} zw_t;

static void zw_byte(void *data, uint8_t byte) {
    zw_t *z = data;
    char c = (char)byte;
    sink_put(z->out, &c, 1);
}

static zw_t *zw_begin(sink_t *out) {
    zw_t *z = moy_store_alloc(sizeof(zw_t));
    if (z == NULL) {
        return NULL;
    }
    z->out = out;
    z->a = 1;
    z->b = 0;
    uzlib_lz77_init(&z->lz, z->hist, sizeof z->hist);
    z->lz.dest_write_data = z;
    z->lz.dest_write_cb = zw_byte;
    char head[2] = { 0x08, (char)0x80 };
    head[0] = (char)(head[0] | (MOY_UF_WBITS - 8) << 4);
    head[1] = (char)(head[1] | (31 - (((uint8_t)head[0] * 256 + 0x80) % 31)));
    sink_put(out, head, 2);
    uzlib_start_block(&z->lz);
    return z;
}

static void zw_write(zw_t *z, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        z->a = (z->a + p[i]) % 65521u;
        z->b = (z->b + z->a) % 65521u;
    }
    uzlib_lz77_compress(&z->lz, p, (unsigned)n);
}

static void zw_end(zw_t *z) {
    uzlib_finish_block(&z->lz);
    uint32_t sum = z->b << 16 | z->a;
    char tail[4] = { (char)(sum >> 24), (char)(sum >> 16), (char)(sum >> 8), (char)sum };
    sink_put(z->out, tail, 4);
    moy_store_free(z, sizeof(zw_t));
}

static const char B64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

int moy_uf_encode_image(uint32_t w, uint32_t h, const uint8_t *pix, size_t n,
                        moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    if (w == 0 || h == 0 || (uint64_t)w * h != n) {
        return MOY_UF_BAD;
    }
    sink_t z = { NULL, 0, 0, 0 };
    zw_t *zw = zw_begin(&z);
    if (zw == NULL) {
        return MOY_ENOMEM;
    }
    zw_write(zw, pix, n);
    zw_end(zw);
    if (z.rc != 0) {
        if (z.p != NULL) {
            moy_store_free(z.p, z.cap);
        }
        return z.rc;
    }
    sink_t s = { NULL, 0, 0, 0 };
    char head[96];
    snprintf(head, sizeof head, "{\"format\": \"moyimg-v1\", \"w\": %lu, \"h\": %lu, \"data\": \"",
             (unsigned long)w, (unsigned long)h);
    sink_s(&s, head);
    const uint8_t *b = (const uint8_t *)z.p;
    for (size_t i = 0; i < z.n; i += 3) {
        uint32_t v = (uint32_t)b[i] << 16;
        size_t left = z.n - i;
        if (left > 1) {
            v |= (uint32_t)b[i + 1] << 8;
        }
        if (left > 2) {
            v |= b[i + 2];
        }
        char q[4] = { B64[v >> 18 & 63], B64[v >> 12 & 63],
                      left > 1 ? B64[v >> 6 & 63] : '=', left > 2 ? B64[v & 63] : '=' };
        sink_put(&s, q, 4);
    }
    sink_s(&s, "\"}");
    moy_store_free(z.p, z.cap);
    return sink_take(&s, out);
}

int moy_uf_decode_image(const char *text, size_t n, moy_buf_t *pix,
                        uint32_t *w, uint32_t *h) {
    pix->p = NULL;
    pix->n = 0;
    if (moy_img_head(text, n, w, h) != MOY_IMG_OK
        || (uint64_t)*w * *h > MOY_UF_PICTURE_MAX) {
        return MOY_UF_NONE;
    }
    size_t cap = (size_t)*w * *h + 1u;
    size_t wn = moy_img_work_size();
    void *work = moy_store_alloc(wn);
    pix->p = moy_store_alloc(cap);
    if (work == NULL || pix->p == NULL) {
        if (work != NULL) {
            moy_store_free(work, wn);
        }
        if (pix->p != NULL) {
            moy_store_free(pix->p, cap);
            pix->p = NULL;
        }
        return MOY_ENOMEM;
    }
    int rc = moy_img_decode(text, n, (uint8_t *)pix->p, cap, w, h, work);
    moy_store_free(work, wn);
    if (rc != MOY_IMG_OK) {
        moy_store_free(pix->p, cap);
        pix->p = NULL;
        return MOY_UF_NONE;
    }
    pix->n = cap - 1u;          // moy_buf_free frees n + 1
    return 0;
}

static void be32(char *p, uint32_t v) {
    p[0] = (char)(v >> 24);
    p[1] = (char)(v >> 16);
    p[2] = (char)(v >> 8);
    p[3] = (char)v;
}

static void chunk(sink_t *s, const char *tag, const char *body, size_t n) {
    char head[8];
    be32(head, (uint32_t)n);
    memcpy(head + 4, tag, 4);
    sink_put(s, head, 8);
    sink_put(s, body, n);
    uint32_t crc = moy_fs_crc32(moy_fs_crc32(0, tag, 4), body, n);
    char tail[4];
    be32(tail, crc);
    sink_put(s, tail, 4);
}

int moy_uf_encode_cover(const uint8_t *pix, size_t n, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    if (n != (size_t)MOY_UF_COVER_SIDE * MOY_UF_COVER_SIDE) {
        return MOY_UF_BAD;
    }
    sink_t z = { NULL, 0, 0, 0 };
    zw_t *zw = zw_begin(&z);
    if (zw == NULL) {
        return MOY_ENOMEM;
    }
    static const uint8_t ZERO = 0;
    for (int y = 0; y < MOY_UF_COVER_SIDE; y++) {
        zw_write(zw, &ZERO, 1);
        zw_write(zw, pix + (size_t)y * MOY_UF_COVER_SIDE, MOY_UF_COVER_SIDE);
    }
    zw_end(zw);
    sink_t s = { NULL, 0, 0, 0 };
    sink_put(&s, "\x89PNG\r\n\x1a\n", 8);
    static const char IHDR[13] = { 0, 0, 0, MOY_UF_COVER_SIDE, 0, 0, 0,
                                   MOY_UF_COVER_SIDE, 8, 3, 0, 0, 0 };
    chunk(&s, "IHDR", IHDR, sizeof IHDR);
    chunk(&s, "PLTE", (const char *)MOY64, sizeof MOY64);
    if (z.rc == 0) {
        chunk(&s, "IDAT", z.p, z.n);
    } else if (s.rc == 0) {
        s.rc = z.rc;
    }
    chunk(&s, "IEND", "", 0);
    if (z.p != NULL) {
        moy_store_free(z.p, z.cap);
    }
    return sink_take(&s, out);
}

int moy_uf_decode_cover(const uint8_t *data, size_t n, moy_buf_t *pix) {
    pix->p = NULL;
    pix->n = 0;
    size_t on = moy_png_out_size(1, MOY_PNG_INDEX);
    void *work = moy_store_alloc(MOY_PNG_WORK);
    char *o = moy_store_alloc(on + 1u);
    int rc = MOY_ENOMEM;
    if (work != NULL && o != NULL) {
        rc = MOY_UF_NONE;
        if (moy_png_begin(work, MOY_PNG_WORK, data, n, 1, MOY_PNG_INDEX, MOY64, 64) == 1
            && moy_png_rows(work, data, n, (uint8_t *)o, on, MOY_UF_COVER_SIDE) == 1) {
            pix->p = o;
            pix->n = on;
            o = NULL;
            rc = 0;
        }
    }
    if (work != NULL) {
        moy_store_free(work, MOY_PNG_WORK);
    }
    if (o != NULL) {
        moy_store_free(o, on + 1u);
    }
    return rc;
}

// -- the wallpaper's copy -------------------------------------------------------------------

int moy_uf_copy_load(const char *root, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    char *p = sibling(root, ARTWORK);
    if (p == NULL) {
        return MOY_ENOMEM;
    }
    int rc = moy_fs_read(p, NULL, out);
    str_free(p);
    return rc == 0 ? 0 : fs_none(rc);
}

int moy_uf_copy_save(const char *root, const char *data, size_t n) {
    int rc = ensure_dirs(root);
    char *p = rc == 0 ? sibling(root, ARTWORK) : NULL;
    if (rc == 0 && p == NULL) {
        rc = MOY_ENOMEM;
    }
    if (rc == 0) {
        rc = moy_fs_publish(p, data, n);
    }
    str_free(p);
    return rc;
}

// -- the table ----------------------------------------------------------------------------

const moy_uf_ops_t moy_uf_ops = {
    moy_uf_list, moy_uf_count, moy_uf_load, moy_uf_save, moy_uf_new_name,
    moy_uf_free_name, moy_uf_rename, moy_uf_duplicate, moy_uf_delete,
    moy_uf_restore, moy_uf_trash_list, moy_uf_prune_trash, moy_uf_history,
    moy_uf_history_ops, moy_uf_history_commit, moy_uf_sig, moy_uf_stamp,
    moy_uf_provenance, moy_uf_encode_image, moy_uf_decode_image,
    moy_uf_encode_cover, moy_uf_decode_cover, moy_uf_copy_load,
    moy_uf_copy_save, moy_buf_free, moy_vol_gate_enter, moy_vol_gate_leave,
};
