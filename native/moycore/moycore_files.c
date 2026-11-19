// A cart's files (moycore_files.h has the contract).

#include <string.h>

// libmoy's path rules: beside this file on a VM build, in the host's one
// build folder on the host (runtime/host/lua_binding.py).
#if __has_include("libmoy/moy_cartpath.h")
#include "libmoy/moy_cartpath.h"
#else
#include "moy_cartpath.h"
#endif
#include "moy_files.h"
#include "moy_kmem.h"
#include "moy_vol.h"
#include "moycore_files.h"
#include "moycore_lua.h"

#if __has_include("py/nlr.h")
#include "py/nlr.h"
#define FILES_NLR 1
void moy_store_unwind(void);
#else
#define FILES_NLR 0
#endif

#pragma weak moycore_keep_refuses
#pragma weak moycore_keep_log

typedef struct moycore_files {
    moy_files_t *f;
    char *writable;              // libmoy's form: each entry NUL-ended, then a NUL
    char dir[192];               // the cart's folder: `read`'s only root
    char keep_dir[320];          // where its written files are kept
} moycore_files_t;

enum { OP_OPEN, OP_CLOSE, OP_READ, OP_WRITTEN, OP_WRITE, OP_ERASE, OP_LIST };

typedef struct {
    int op;
    moycore_files_t *s;
    const char *path;            // the path, read's name, or list's prefix
    const uint8_t *data;
    uint32_t offset, index;
    uint8_t *dst;
    uint32_t len;
    int32_t r;                   // the answer; preset to the refusal
} fop_t;

static void fop_body(fop_t *q) {
    moycore_files_t *s = q->s;
    size_t n = q->path ? strlen(q->path) : 0;
    switch (q->op) {
        case OP_OPEN:
            s->f = moy_files_open(s->dir);
            break;
        case OP_CLOSE:
            moy_files_close(s->f);
            s->f = NULL;
            break;
        case OP_READ:
            q->r = moy_files_read(s->f, q->path, q->offset, q->dst, q->len);
            break;
        case OP_WRITTEN:
            q->r = moy_files_read_written(s->f, (const uint8_t *)q->path, n, q->offset,
                                          q->dst, q->len);
            break;
        case OP_WRITE:
            if (moycore_keep_refuses != NULL && moycore_keep_refuses(q->len)) {
                q->r = MOY_FILES_NO_ROOM;
                break;
            }
            q->r = moy_files_write(s->f, (const uint8_t *)q->path, n, q->data, q->len);
            if (q->r == 0 && moycore_keep_log != NULL) moycore_keep_log('p', s->keep_dir, q->path);
            break;
        case OP_ERASE:
            q->r = moy_files_erase(s->f, (const uint8_t *)q->path, n);
            if (q->r == 0 && moycore_keep_log != NULL) moycore_keep_log('d', s->keep_dir, q->path);
            break;
        default:
            q->r = moy_files_name(s->f, (const uint8_t *)q->path, n, q->index, q->dst, q->len);
            break;
    }
}

// One store op, inside the gate; a volume that raised is the op's refusal.
static int32_t fop(fop_t *q) {
    if (q->s == NULL || (q->op != OP_OPEN && q->s->f == NULL)) return q->r;
    int gated = moy_vol_gate_enter(q->s->dir);
#if FILES_NLR
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        fop_body(q);
        nlr_pop();
    } else {
        moy_store_unwind();
        if (q->op == OP_OPEN || q->op == OP_CLOSE) q->s->f = NULL;
    }
#else
    fop_body(q);
#endif
    moy_vol_gate_leave(gated);
    return q->r;
}

static uint32_t h_read(void *user, const char *name, uint32_t offset, uint8_t *dst,
                       uint32_t len) {
    fop_t q = { OP_READ, user, name, NULL, offset, 0, dst, len, 0 };
    int32_t r = fop(&q);
    return r < 0 ? 0u : (uint32_t)r;
}

static int32_t h_written(void *user, const char *path, uint32_t offset, uint8_t *dst,
                         uint32_t len) {
    fop_t q = { OP_WRITTEN, user, path, NULL, offset, 0, dst, len, -1 };
    return fop(&q);
}

static int32_t h_write(void *user, const char *path, const uint8_t *data, uint32_t len) {
    fop_t q = { OP_WRITE, user, path, data, 0, 0, NULL, len, MOY_FILES_FAILED };
    return fop(&q);
}

static int32_t h_erase(void *user, const char *path) {
    fop_t q = { OP_ERASE, user, path, NULL, 0, 0, NULL, 0, -1 };
    return fop(&q);
}

static int32_t h_list(void *user, const char *prefix, uint32_t index, uint8_t *dst,
                      uint32_t len) {
    fop_t q = { OP_LIST, user, prefix, NULL, 0, index, dst, len, -1 };
    return fop(&q);
}

void moycore_files_close(moycore_files_t *s) {
    if (s == NULL) return;
    fop_t q = { OP_CLOSE, s, NULL, NULL, 0, 0, NULL, 0, 0 };
    fop(&q);
    moy_kfree(s->writable);
    moy_kfree(s);
}

moycore_files_t *moycore_files_open(const char *dir, const char *writable, size_t n) {
    size_t dn = strlen(dir);
    moycore_files_t *s = moy_kalloc(sizeof(*s), MOY_KMEM_PSRAM);
    if (s == NULL || dn >= sizeof(s->dir)) {
        moy_kfree(s);
        return NULL;
    }
    memset(s, 0, sizeof(*s));
    memcpy(s->dir, dir, dn + 1);
    if (writable != NULL && n > 0) {
        // Two NULs end libmoy's list, whatever the joined form ended with.
        if ((s->writable = moy_kalloc(n + 2u, MOY_KMEM_PSRAM)) == NULL) {
            moy_kfree(s);
            return NULL;
        }
        memcpy(s->writable, writable, n);
        s->writable[n] = 0;
        s->writable[n + 1] = 0;
    }
    fop_t q = { OP_OPEN, s, NULL, NULL, 0, 0, NULL, 0, 0 };
    fop(&q);
    if (s->f == NULL) {
        moy_kfree(s->writable);
        moy_kfree(s);
        return NULL;
    }
    moy_files_folder(dir, s->keep_dir, sizeof(s->keep_dir));
    return s;
}

// The verbs, held to the rules libmoy's are (moy_lua.c's l_read, l_write,
// l_erase and l_list).
int32_t moycore_files_read(moycore_files_t *s, const char *name, size_t n,
                           uint32_t offset, uint8_t *dst, uint32_t len) {
    if (s == NULL || n > MOY_FILE_NAME_MAX || memchr(name, 0, n) || !moy_path_inside(name))
        return 0;
    if (n <= MOY_FILE_PATH_MAX && moy_path_writable(s->writable, name)) {
        int32_t r = h_written(s, name, offset, dst, len);
        if (r >= 0) return r;
    }
    return (int32_t)h_read(s, name, offset, dst, len);
}

int32_t moycore_files_write(moycore_files_t *s, const char *path, size_t n,
                            const uint8_t *data, uint32_t len) {
    char buf[MOY_FILE_PATH_MAX + 1];
    const char *p = s ? moy_path_written(s->writable, (const uint8_t *)path, n, buf) : NULL;
    if (p == NULL) return MOY_FILE_NOT_WRITABLE;
    if (len > MOY_FILE_WRITE_MAX) return MOY_FILE_NO_ROOM;
    int32_t r = h_write(s, p, data, len);
    return r == 0 || r == MOY_FILE_NO_ROOM ? r : MOY_FILE_FAILED;
}

int32_t moycore_files_erase(moycore_files_t *s, const char *path, size_t n) {
    char buf[MOY_FILE_PATH_MAX + 1];
    const char *p = s ? moy_path_written(s->writable, (const uint8_t *)path, n, buf) : NULL;
    return p != NULL && h_erase(s, p) == 0 ? 0 : -1;
}

int32_t moycore_files_list(moycore_files_t *s, const char *prefix, size_t n,
                           uint32_t index, uint8_t *dst, uint32_t cap) {
    if (s == NULL || n > MOY_FILE_NAME_MAX || memchr(prefix, 0, n)) return -1;
    return h_list(s, prefix, index, dst, cap);
}

static void unbind(moy_console *con, moycore_files_t **slot) {
    moycore_files_t *s = *slot;
    moy_host *h = &con->host;
    h->read = NULL;
    h->writable = NULL;
    h->written = NULL;
    h->write = NULL;
    h->erase = NULL;
    h->list = NULL;
    h->files_user = NULL;
    *slot = NULL;
    moycore_files_close(s);
}

int moycore_files_bind(moy_console *con, moycore_files_t **slot, const char *dir,
                       const char *writable, size_t n) {
    unbind(con, slot);
    if (dir == NULL) return 0;
    moycore_files_t *s = moycore_files_open(dir, writable, n);
    if (s == NULL) return -1;
    moy_host *h = &con->host;
    h->read = h_read;
    h->writable = s->writable;
    h->written = h_written;
    h->write = h_write;
    h->erase = h_erase;
    h->list = h_list;
    h->files_user = s;
    *slot = s;
    return 0;
}

int moycore_lua_files(const char *dir, const char *writable, size_t n) {
    return moycore_files_bind(&moycore_RUN.c.con, &moycore_RUN.files, dir, writable, n);
}
