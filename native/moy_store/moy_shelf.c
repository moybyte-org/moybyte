// The shelf's record and its removal: moy_shelf.h has the contract.

#include <stdio.h>
#include <string.h>

#include "moy_cat.h"
#include "moy_files.h"
#include "moy_json.h"
#include "moy_shelf.h"

#define RECORD_HEAD "{\"version\": 1, \"carts\": "

// `name` beside the carts folder `root` (moy_store_base._sibling_path: the
// root up to its last '/', the root itself when it has none, `name` alone
// when that is empty): 1, or 0 when `cap` cannot hold it.
static int sibling(const char *root, const char *name, char *out, size_t cap) {
    const char *slash = strrchr(root, '/');
    size_t pn = slash != NULL ? (size_t)(slash - root) : strlen(root);
    int n = pn ? snprintf(out, cap, "%.*s/%s", (int)pn, root, name)
               : snprintf(out, cap, "%s", name);
    return n > 0 && (size_t)n < cap;
}

// The record's "carts" object in `text`, when the text is one object that
// has one.
static int carts_of(const moy_buf_t *b, const char **v, const char **ve) {
    const char *end = b->p + b->n;
    const char *s = moy_json_ws(b->p, end);
    const char *e = s < end ? moy_json_value(s, end, 1u) : NULL;
    if (e == NULL || moy_json_ws(e, end) != end || *s != '{') {
        return 0;
    }
    return moy_json_get(s, e, "carts", v, ve) && **v == '{';
}

int moy_shelf_record(const char *root, moy_buf_t *out) {
    char path[MOY_SHELF_PATH], bak[MOY_SHELF_PATH + 4u];
    const char *v, *ve;
    out->p = NULL;
    out->n = 0;
    if (!sibling(root, MOY_SHELF_RECORD, path, sizeof path)) {
        return MOY_EINVAL;
    }
    moy_buf_t b;
    int rc = moy_fs_read_file(path, (size_t)-1, &b);
    if (rc == MOY_ENOMEM) {
        return rc;
    }
    if (rc == 0) {
        if (carts_of(&b, &v, &ve)) {
            *out = b;
            return 0;
        }
        moy_buf_free(&b);
    }
    snprintf(bak, sizeof bak, "%s.bak", path);
    rc = moy_fs_read_stamped(bak, &b);
    if (rc != 0) {
        return rc == MOY_ENOMEM ? rc : MOY_FS_NONE;
    }
    if (!carts_of(&b, &v, &ve)) {
        moy_buf_free(&b);
        return MOY_FS_NONE;
    }
    moy_fs_write(path, b.p, b.n);       // heal: the last good copy published again
    *out = b;
    return 0;
}

// A string span decoded, NUL-terminated, into scratch; "" for none.
static char *decoded(const char *s, const char *se, size_t *alloc) {
    size_t n = s != NULL && *s == '"' ? moy_json_strlen(s, se) : 0u;
    char *p = moy_store_alloc(n + 1u);
    if (p != NULL) {
        if (n) {
            moy_json_str(s, se, p);
        }
        p[n] = '\0';
    }
    *alloc = n + 1u;
    return p;
}

int moy_shelf_each(const char *root, moy_shelf_fn fn, void *ctx) {
    moy_buf_t b;
    int rc = moy_shelf_record(root, &b);
    if (rc != 0) {
        return rc == MOY_FS_NONE ? 0 : rc;
    }
    const char *v, *ve, *k, *ke, *e, *ee;
    carts_of(&b, &v, &ve);
    moy_json_iter_t it;
    moy_json_iter(&it, v, ve);
    rc = 0;
    while (rc == 0 && moy_json_next(&it, &k, &ke, &e, &ee)) {
        const char *id = NULL, *ide = NULL, *nm = NULL, *nme = NULL, *ver, *vere;
        moy_shelf_entry_t ent = { NULL, NULL, NULL, 0 };
        if (*e == '{') {
            if (!moy_json_get(e, ee, "id", &id, &ide) || *id != '"') {
                id = NULL;
            }
            if (!moy_json_get(e, ee, "name", &nm, &nme) || *nm != '"') {
                nm = NULL;
            }
            int64_t n;
            if (moy_json_get(e, ee, "version", &ver, &vere) && moy_json_int(ver, vere, &n) == 1) {
                ent.version = n;
            }
        }
        size_t fa, ia, na;
        char *f = decoded(k, ke, &fa), *i = decoded(id, ide, &ia), *m = decoded(nm, nme, &na);
        if (f == NULL || i == NULL || m == NULL) {
            rc = MOY_ENOMEM;
        } else {
            ent.folder = f;
            ent.id = i;
            ent.name = m;
            rc = fn(ctx, &ent);
        }
        moy_store_free(f, fa);
        moy_store_free(i, ia);
        moy_store_free(m, na);
    }
    moy_buf_free(&b);
    return rc;
}

typedef struct {
    const char *ident;
    char *folder;
    size_t cap;
    int found;
} find_t;

static int find_one(void *ctx, const moy_shelf_entry_t *e) {
    find_t *f = ctx;
    if (strcmp(f->ident, e->folder) == 0 || strcmp(f->ident, e->id) == 0) {
        int n = snprintf(f->folder, f->cap, "%s", e->folder);
        f->found = n >= 0 && (size_t)n < f->cap ? 1 : -1;
    }
    return 0;
}

int moy_shelf_find(const char *root, const char *ident, char *folder, size_t cap) {
    find_t f = { ident, folder, cap, 0 };
    int rc = moy_shelf_each(root, find_one, &f);
    if (rc != 0) {
        return rc;
    }
    return f.found > 0 ? 0 : f.found < 0 ? MOY_EINVAL : MOY_FS_NONE;
}

int moy_shelf_without(const char *root, const char *folder, moy_buf_t *out) {
    moy_buf_t b;
    const char *v, *ve, *x, *xe;
    out->p = NULL;
    out->n = 0;
    int rc = moy_shelf_record(root, &b);
    if (rc != 0) {
        return rc;
    }
    carts_of(&b, &v, &ve);
    if (!moy_json_get(v, ve, folder, &x, &xe)) {
        moy_buf_free(&b);
        return MOY_FS_NONE;
    }
    size_t head = sizeof RECORD_HEAD - 1u;
    size_t n = moy_json_canon_set(v, ve, folder, NULL, NULL, 0);
    char *p = n == MOY_JSON_DEEP ? NULL : moy_store_alloc(head + n + 2u);
    if (p == NULL) {
        moy_buf_free(&b);
        return n == MOY_JSON_DEEP ? MOY_EINVAL : MOY_ENOMEM;
    }
    memcpy(p, RECORD_HEAD, head);
    moy_json_canon_set(v, ve, folder, NULL, p + head, n);
    p[head + n] = '}';
    moy_buf_free(&b);
    out->p = p;
    out->n = head + n + 1u;
    return 0;
}

int moy_shelf_take(const char *root, const char *folder, char *gone, size_t cap) {
    char stage[MOY_SHELF_PATH], cart[MOY_SHELF_PATH];
    if (!sibling(root, MOY_SHELF_STAGE, stage, sizeof stage)) {
        return MOY_EINVAL;
    }
    int a = snprintf(gone, cap, "%s/%s.gone", stage, folder);
    int c = snprintf(cart, sizeof cart, "%s/%s", root, folder);
    if (a <= 0 || (size_t)a >= cap || c <= 0 || (size_t)c >= sizeof cart) {
        return MOY_EINVAL;
    }
    moy_fs_mkdir(stage);
    if (moy_fs_exists(gone)) {
        moy_cat_rmtree(gone);
    }
    return moy_fs_rename(cart, gone);
}

int moy_shelf_publish(const char *root, const char *text, size_t n) {
    char path[MOY_SHELF_PATH];
    if (!sibling(root, MOY_SHELF_RECORD, path, sizeof path)) {
        return MOY_EINVAL;
    }
    return moy_fs_publish(path, text, n);
}

int moy_shelf_written(const char *root, const char *folder, char *out, size_t cap) {
    char cart[MOY_SHELF_PATH];
    int c = snprintf(cart, sizeof cart, "%s/%s", root, folder);
    if (c <= 0 || (size_t)c >= sizeof cart) {
        return MOY_EINVAL;
    }
    return moy_files_folder(cart, out, cap) == 0 ? 0 : MOY_EINVAL;
}

int moy_shelf_remove(const char *root, const char *folder, const char *text, size_t n) {
    char gone[MOY_SHELF_PATH];
    int rc = moy_shelf_take(root, folder, gone, sizeof gone);
    if (rc != 0) {
        return rc;
    }
    if (text != NULL) {
        rc = moy_shelf_publish(root, text, n);
        if (rc != 0) {
            return rc;
        }
    }
    moy_cat_rmtree(gone);
    if (moy_shelf_written(root, folder, gone, sizeof gone) == 0) {
        moy_cat_rmtree(gone);
    }
    return 0;
}
