// The core carts and the removal record: moy_removed.h has the contract.

#include <stdio.h>
#include <string.h>

#include "moy_core.h"
#include "moy_removed.h"

#define CART_EXT ".moy"
#define PATH_CAP 320u

static const char *const CORE[] = { MOY_CORE_IDS };

// The id `name` (n bytes) names: its length without a folder's extension.
static size_t id_len(const char *name, size_t n) {
    size_t e = sizeof CART_EXT - 1u;
    return n >= e && memcmp(name + n - e, CART_EXT, e) == 0 ? n - e : n;
}

static int core_n(const char *name, size_t n) {
    size_t k = id_len(name, n);
    for (size_t i = 0; i < sizeof CORE / sizeof CORE[0]; i++) {
        if (strlen(CORE[i]) == k && memcmp(CORE[i], name, k) == 0) {
            return 1;
        }
    }
    return 0;
}

static int removable_n(const char *name, size_t n) {
    size_t ns = sizeof MOY_BUILTIN_NS - 1u;
    size_t k = id_len(name, n);
    return k > ns && memcmp(name, MOY_BUILTIN_NS, ns) == 0 && name[ns] == '.'
           && !core_n(name, n);
}

int moy_removed_core(const char *name) {
    return core_n(name, strlen(name));
}

int moy_removed_removable(const char *name) {
    return removable_n(name, strlen(name));
}

// `name` beside the carts folder `root` (moy_store_base._sibling_path).
static int sibling(const char *root, const char *name, char *out, size_t cap) {
    const char *slash = strrchr(root, '/');
    size_t pn = slash != NULL ? (size_t)(slash - root) : strlen(root);
    int n = pn ? snprintf(out, cap, "%.*s/%s", (int)pn, root, name)
               : snprintf(out, cap, "%s", name);
    return n > 0 && (size_t)n < cap;
}

static int is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f';
}

// Whether the lines `text` (n bytes, each ending in '\n') hold `line`.
static int holds(const char *text, size_t n, const char *line, size_t ln) {
    const char *p = text, *end = text + n;
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        size_t k = (size_t)(nl - p);
        if (k == ln && memcmp(p, line, ln) == 0) {
            return 1;
        }
        p = nl + 1;
    }
    return 0;
}

int moy_removed_load(const char *root, moy_buf_t *out) {
    char path[PATH_CAP];
    out->p = NULL;
    out->n = 0;
    moy_buf_t b = { NULL, 0 };
    int rc = sibling(root, MOY_REMOVED_NAME, path, sizeof path)
             ? moy_fs_read_file(path, (size_t)-1, &b) : MOY_EINVAL;
    if (rc == MOY_ENOMEM) {
        return rc;
    }
    size_t cap = (rc == 0 ? b.n : 0) + 2u;
    char *o = moy_store_alloc(cap);
    if (o == NULL) {
        moy_buf_free(&b);
        return MOY_ENOMEM;
    }
    size_t on = 0;
    const char *p = b.p, *end = b.p + (rc == 0 ? b.n : 0);
    while (p < end) {
        const char *nl = memchr(p, '\n', (size_t)(end - p));
        const char *le = nl != NULL ? nl : end;
        const char *s = p, *e = le;
        while (s < e && is_ws(*s)) {
            s++;
        }
        while (e > s && is_ws(e[-1])) {
            e--;
        }
        size_t k = (size_t)(e - s);
        if (k && removable_n(s, k) && !holds(o, on, s, k)) {
            memcpy(o + on, s, k);
            on += k;
            o[on++] = '\n';
        }
        p = le + 1;
    }
    moy_buf_free(&b);
    o[on] = '\0';
    // moy_buf_free frees n + 1 bytes: the buffer is handed out at that size.
    if (on + 1u < cap) {
        char *t = moy_store_alloc(on + 1u);
        if (t == NULL) {
            moy_store_free(o, cap);
            return MOY_ENOMEM;
        }
        memcpy(t, o, on + 1u);
        moy_store_free(o, cap);
        o = t;
    }
    out->p = o;
    out->n = on;
    return 0;
}

// The record `text` written crash-safe, its folder made first.
static int save(const char *root, const char *text, size_t n) {
    char path[PATH_CAP];
    if (!sibling(root, "system", path, sizeof path)) {
        return MOY_EINVAL;
    }
    moy_fs_mkdir(path);
    if (!sibling(root, MOY_REMOVED_NAME, path, sizeof path)) {
        return MOY_EINVAL;
    }
    return moy_fs_publish(path, text, n);
}

int moy_removed_note(const char *root, const char *folder) {
    size_t fn = strlen(folder);
    if (!removable_n(folder, fn)) {
        return MOY_FS_NONE;
    }
    moy_buf_t had;
    int rc = moy_removed_load(root, &had);
    if (rc != 0) {
        return rc;
    }
    if (holds(had.p, had.n, folder, fn)) {
        moy_buf_free(&had);
        return MOY_FS_NONE;
    }
    size_t n = had.n + fn + 1u;
    char *t = moy_store_alloc(n + 1u);
    if (t == NULL) {
        moy_buf_free(&had);
        return MOY_ENOMEM;
    }
    memcpy(t, had.p, had.n);
    memcpy(t + had.n, folder, fn);
    t[n - 1u] = '\n';
    moy_buf_free(&had);
    rc = save(root, t, n);
    moy_store_free(t, n + 1u);
    return rc;
}

int moy_removed_drop(const char *root, const char *folder) {
    size_t fn = strlen(folder);
    moy_buf_t had;
    int rc = moy_removed_load(root, &had);
    if (rc != 0) {
        return rc;
    }
    if (!fn || !holds(had.p, had.n, folder, fn)) {
        moy_buf_free(&had);
        return MOY_FS_NONE;
    }
    // The line cut in place: the record holds it once.
    char *p = had.p, *end = had.p + had.n;
    while (p < end) {
        char *nl = memchr(p, '\n', (size_t)(end - p));
        if ((size_t)(nl - p) == fn && memcmp(p, folder, fn) == 0) {
            memmove(p, nl + 1, (size_t)(end - nl - 1));
            break;
        }
        p = nl + 1;
    }
    rc = save(root, had.p, had.n - fn - 1u);
    moy_buf_free(&had);
    return rc;
}
