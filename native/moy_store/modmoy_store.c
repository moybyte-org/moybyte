// moy_store's MicroPython binding: the store's crash-safe write and file verbs
// (moy_fs.h) as the module runtime/moy_fs.py delegates to wherever it imports
// -- every board, the browser and the desktop MicroPython. Names follow
// moy_fs.py's: publish is its _write_atomic, read_recover its _read_recover,
// claim its _claim_bak, and the plain verbs its _read, _write, _write_bytes,
// _read_bytes, _remove, _mkdir and _exists.
//
// THE BORROW (docs/kernel_store_2026-10.md section 3). moy_vol_at resolves a
// path through MicroPython's mount table to the VM's own instance: a VfsFat's
// FATFS, a VfsLfs2's lfs2_t (refused loudly when the private layout this file
// mirrors stops matching), or POSIX under a VfsPosix at "/". A sector read
// through a Python block device can raise, so every call runs under nlr_push
// and an exception closes the files the call opened and frees its scratch
// before it goes on. Scratch and the marker cache are PSRAM on a board,
// malloc elsewhere, never the gc heap.

#include <string.h>

#include "py/mperrno.h"
#include "py/objstr.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "py/parsenum.h"
#include "py/gc.h"
#include "py/stackctrl.h"
#include <math.h>
#include "extmod/vfs.h"

#if MICROPY_VFS_FAT
#ifndef FFCONF_H
#define FFCONF_H "lib/oofatfs/ffconf.h"
#endif
#include "extmod/vfs_fat.h"
#endif
#if MICROPY_VFS_LFS2
#include "lib/littlefs/lfs2.h"
#endif
#if MICROPY_VFS_POSIX
#include "extmod/vfs_posix.h"
#endif

#if __has_include("esp_heap_caps.h")
#define MOY_STORE_PSRAM 1
#include "esp_heap_caps.h"
#else
#define MOY_STORE_PSRAM 0
#include <stdlib.h>
#endif

#include "moy_cat.h"
#include "moy_fs.h"
#include "moy_journal.h"
#include "moy_json.h"
#include "moy_load.h"
#include "moy_pack.h"
#include "moy_seed.h"

// -- the allocator -------------------------------------------------------------
//
// Every block carries a header; a scratch block is also on a list, which an
// exception frees whole.

typedef struct blk {
    struct blk *prev, *next;
    size_t n;
    size_t keep;
} blk_t;

static blk_t *scratch;
static size_t mem_now, mem_high;

static void *raw_alloc(size_t n) {
    #if MOY_STORE_PSRAM
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
    }
    return p;
    #else
    return calloc(1, n);
    #endif
}

// A block outside the gc heap that is never freed: the owned card volume's.
void *moy_store_raw_alloc(size_t n) {
    return raw_alloc(n);
}

static void raw_free(void *p) {
    #if MOY_STORE_PSRAM
    heap_caps_free(p);
    #else
    free(p);
    #endif
}

static void *alloc_blk(size_t n, int keep) {
    blk_t *b = raw_alloc(sizeof(blk_t) + (n ? n : 1u));
    if (b == NULL) {
        return NULL;
    }
    b->n = n;
    b->keep = (size_t)keep;
    if (!keep) {
        b->next = scratch;
        if (scratch != NULL) {
            scratch->prev = b;
        }
        scratch = b;
    }
    mem_now += n;
    if (mem_now > mem_high) {
        mem_high = mem_now;
    }
    return b + 1;
}

void *moy_store_alloc(size_t n) {
    return alloc_blk(n, 0);
}

// The kernel's task watchdog (native/moy_kernel), on a board that takes it:
// fed while the frame loop has it armed, at most every half second, so a long
// store call never reads as a hang. Weak, since not every image links it.
#if MOY_STORE_PSRAM
extern bool moy_kernel_watchdog(uint32_t *timeout_ms, uint32_t *max_gap_ms,
                                uint32_t *frames, bool reset) __attribute__((weak));
extern void moy_kernel_feed(void) __attribute__((weak));
#endif

void moy_store_tick(void) {
    #if MOY_STORE_PSRAM
    static mp_uint_t last;
    uint32_t t, g, f;
    mp_uint_t now = mp_hal_ticks_ms();
    if (moy_kernel_feed != NULL && moy_kernel_watchdog != NULL && now - last >= 500u
        && moy_kernel_watchdog(&t, &g, &f, false)) {
        last = now;
        moy_kernel_feed();
    }
    #endif
}

void *moy_store_keep(size_t n) {
    return alloc_blk(n, 1);
}

void moy_store_free(void *p, size_t n) {
    (void)n;
    if (p == NULL) {
        return;
    }
    blk_t *b = (blk_t *)p - 1;
    if (!b->keep) {
        if (b->prev != NULL) {
            b->prev->next = b->next;
        } else {
            scratch = b->next;
        }
        if (b->next != NULL) {
            b->next->prev = b->prev;
        }
    }
    mem_now -= b->n;
    raw_free(b);
}

// What a raised call leaves: its open files closed, its scratch freed.
static void unwind(void) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        moy_vol_unwind();
        nlr_pop();
    }
    while (scratch != NULL) {
        moy_store_free(scratch + 1, scratch->n);
    }
}

// The same, for a store call made from another module's binding (moy_net's
// webhost) whose own nlr frame caught the raise.
void moy_store_unwind(void) {
    unwind();
}

#define GUARD_BEGIN { nlr_buf_t nlr_; if (nlr_push(&nlr_) == 0) {
#define GUARD_END nlr_pop(); } else { unwind(); nlr_jump(nlr_.ret_val); } }

// -- the borrow ------------------------------------------------------------------

#if MICROPY_VFS_LFS2
// extmod/vfs_lfs.c's VfsLfs2, which it keeps private: checked on every resolve.
typedef struct {
    mp_obj_base_t base;
    mp_vfs_blockdev_t blockdev;
    bool enable_mtime;
    vstr_t cur_dir;
    struct lfs2_config config;
    lfs2_t lfs;
} lfs2_vfs_t;
extern const mp_obj_type_t mp_type_vfs_lfs2;
#endif

// The kernel's internal volume (moy_kvfs.c), where the image has one.
extern void *moy_kvfs_lfs(mp_obj_t obj) __attribute__((weak));
extern void moy_kvol_state(int *mounted, int *err, uint32_t *blocks,
                           uint32_t *bsize) __attribute__((weak));

// While no VM runs (its mount table is gone: docs/kernel_cartpath_2026-10.md
// section 5.3) the kernel's own volumes answer: the card's FATFS at /sd, held
// open across the stop (moy_card.c), and the internal flash's littlefs at /.
void *moy_store_card_fatfs(void);
#if MICROPY_VFS_LFS2
extern lfs2_t *moy_kvol_lfs(void) __attribute__((weak));
#endif

static int vol_at_kernel(const char *path, moy_vol_t *v, const char **rest) {
    if (path[0] != '/') {
        return MP_ENODEV;
    }
    #if MICROPY_VFS_FAT
    if (strncmp(path, "/sd", 3) == 0 && (path[3] == '/' || path[3] == 0)) {
        void *fs = moy_store_card_fatfs();
        if (fs == NULL) {
            return MP_ENODEV;
        }
        v->kind = MOY_VOL_KIND_FAT;
        v->fs = fs;
        *rest = path[3] ? path + 3 : "/";
        return 0;
    }
    #endif
    #if MICROPY_VFS_LFS2
    lfs2_t *k = moy_kvol_lfs != NULL ? moy_kvol_lfs() : NULL;
    if (k != NULL) {
        v->kind = MOY_VOL_KIND_LFS2;
        v->fs = k;
        *rest = path;
        return 0;
    }
    #endif
    return MP_ENODEV;
}

int moy_vol_at(const char *path, moy_vol_t *v, const char **rest) {
    if (MP_STATE_VM(vfs_mount_table) == NULL) {
        return vol_at_kernel(path, v, rest);
    }
    const char *r = path;
    mp_vfs_mount_t *m = mp_vfs_lookup_path(path, &r);
    if (m == MP_VFS_NONE || m == MP_VFS_ROOT) {
        return MP_ENODEV;
    }
    #if MICROPY_VFS_LFS2
    if (moy_kvfs_lfs != NULL) {
        void *k = moy_kvfs_lfs(m->obj);
        if (k != NULL) {
            v->kind = MOY_VOL_KIND_LFS2;
            v->fs = k;
            *rest = r;
            return 0;
        }
    }
    #endif
    const mp_obj_type_t *t = mp_obj_get_type(m->obj);
    #if MICROPY_VFS_FAT
    if (t == &mp_fat_vfs_type) {
        v->kind = MOY_VOL_KIND_FAT;
        v->fs = &((fs_user_mount_t *)MP_OBJ_TO_PTR(m->obj))->fatfs;
        *rest = r;
        return 0;
    }
    #endif
    #if MICROPY_VFS_LFS2
    if (t == &mp_type_vfs_lfs2) {
        lfs2_vfs_t *o = MP_OBJ_TO_PTR(m->obj);
        if (o->lfs.cfg != &o->config) {
            mp_raise_msg(&mp_type_RuntimeError,
                         MP_ERROR_TEXT("moy_store: VfsLfs2's layout changed"));
        }
        v->kind = MOY_VOL_KIND_LFS2;
        v->fs = &o->lfs;
        *rest = r;
        return 0;
    }
    #endif
    #if MICROPY_VFS_POSIX
    if (t == &mp_type_vfs_posix && m->len == 1) {
        v->kind = MOY_VOL_KIND_POSIX;
        v->fs = NULL;
        *rest = path;
        return 0;
    }
    #endif
    (void)t;
    return MP_ENODEV;
}

// -- arguments and results ----------------------------------------------------------

// The path as an absolute one: a relative path joins the VFS's working folder.
static const char *path_arg(mp_obj_t p) {
    const char *s = mp_obj_str_get_str(p);
    if (s[0] == '/') {
        return s;
    }
    size_t cn;
    const char *cwd = mp_obj_str_get_data(mp_vfs_getcwd(), &cn);
    size_t sn = strlen(s);
    char *out = m_new(char, cn + sn + 2u);
    memcpy(out, cwd, cn);
    size_t k = cn;
    if (k == 0 || out[k - 1u] != '/') {
        out[k++] = '/';
    }
    memcpy(out + k, s, sn + 1u);
    return out;
}

static MP_NORETURN void raise_rc(int rc) {
    if (rc == MP_ENOMEM) {
        mp_raise_type(&mp_type_MemoryError);
    }
    mp_raise_OSError(rc);
}

// A buffer as a str (`text`) or bytes, and freed.
static mp_obj_t take(moy_buf_t *b, int text) {
    mp_obj_t o;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        o = text ? mp_obj_new_str(b->p, b->n) : mp_obj_new_bytes((const byte *)b->p, b->n);
        nlr_pop();
    } else {
        moy_buf_free(b);
        nlr_jump(nlr.ret_val);
    }
    moy_buf_free(b);
    return o;
}

// -- the module -------------------------------------------------------------------------

static mp_obj_t mod_set_publish_root(mp_obj_t root) {
    int rc = 0;
    const char *r = mp_obj_str_get_str(root);
    GUARD_BEGIN
    rc = moy_fs_root(r);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_publish_root_obj, mod_set_publish_root);

static mp_obj_t mod_publish(mp_obj_t path, mp_obj_t data) {
    const char *p = path_arg(path);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(data, &bi, MP_BUFFER_READ);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_publish(p, bi.buf, bi.len);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_publish_obj, mod_publish);

static mp_obj_t mod_write(mp_obj_t path, mp_obj_t data) {
    const char *p = path_arg(path);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(data, &bi, MP_BUFFER_READ);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_write(p, bi.buf, bi.len);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_write_obj, mod_write);

static mp_obj_t mod_write_bytes(mp_obj_t path, mp_obj_t data) {
    const char *p = path_arg(path);
    mp_buffer_info_t bi;
    mp_get_buffer_raise(data, &bi, MP_BUFFER_READ);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_write_bytes(p, bi.buf, bi.len);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_write_bytes_obj, mod_write_bytes);

static mp_obj_t mod_read_recover(size_t n_args, const mp_obj_t *args) {
    const char *p = path_arg(args[0]);
    const char *at = NULL;
    if (n_args > 1 && args[1] != mp_const_none) {
        at = mp_obj_str_get_str(args[1]);
    }
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    if (at != NULL) {
        // A name relative to the working folder opens more cheaply only where
        // the volume keeps that folder itself: FAT, the same FATFS.
        moy_vol_t a, w;
        const char *ra, *rw;
        if (moy_vol_at(p, &a, &ra) != 0 || moy_vol_at(at, &w, &rw) != 0
            || a.kind != MOY_VOL_KIND_FAT || w.kind != a.kind || w.fs != a.fs) {
            at = NULL;
        }
    }
    rc = moy_fs_read(p, at, &b);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_read_recover_obj, 1, 2, mod_read_recover);

static mp_obj_t mod_read(mp_obj_t path) {
    const char *p = path_arg(path);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_read_file(p, (size_t)-1, &b);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_read_obj, mod_read);

static mp_obj_t mod_read_bytes(mp_obj_t path, mp_obj_t cap) {
    const char *p = path_arg(path);
    mp_int_t c = mp_obj_get_int(cap);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_read_file(p, c < 0 ? 0 : (size_t)c, &b);
    GUARD_END
    if (rc == MOY_EFBIG) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 0);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_read_bytes_obj, mod_read_bytes);

static mp_obj_t mod_read_stamped(mp_obj_t path) {
    const char *p = path_arg(path);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_fs_read_stamped(p, &b);
    GUARD_END
    if (rc == MOY_FS_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_read_stamped_obj, mod_read_stamped);

static mp_obj_t stamp_tuple(uint32_t chars, uint32_t crc) {
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(chars), mp_obj_new_int_from_uint(crc) };
    return mp_obj_new_tuple(2, t);
}

static mp_obj_t mod_bak_stamp(mp_obj_t path) {
    const char *p = path_arg(path);
    uint32_t chars = 0, crc = 0;
    int ok = 0;
    GUARD_BEGIN
    ok = moy_fs_bak_stamp(p, &chars, &crc);
    GUARD_END
    return ok ? stamp_tuple(chars, crc) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_bak_stamp_obj, mod_bak_stamp);

static mp_obj_t mod_stamp(mp_obj_t data) {
    mp_buffer_info_t bi;
    mp_get_buffer_raise(data, &bi, MP_BUFFER_READ);
    uint32_t chars, crc;
    moy_fs_stamp(bi.buf, bi.len, &chars, &crc);
    return stamp_tuple(chars, crc);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_stamp_obj, mod_stamp);

static mp_obj_t mod_claim(mp_obj_t path, mp_obj_t dest, mp_obj_t stamp) {
    const char *p = path_arg(path), *d = path_arg(dest);
    mp_obj_t *st;
    mp_obj_get_array_fixed_n(stamp, 2, &st);
    uint32_t chars = (uint32_t)mp_obj_get_int_truncated(st[0]);
    uint32_t crc = (uint32_t)mp_obj_get_int_truncated(st[1]);
    int ok = 0;
    GUARD_BEGIN
    ok = moy_fs_claim(p, d, chars, crc);
    GUARD_END
    return mp_obj_new_bool(ok);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_claim_obj, mod_claim);

static mp_obj_t mod_forget_bak(mp_obj_t path) {
    const char *p = path_arg(path);
    GUARD_BEGIN
    moy_fs_forget_bak(p);
    GUARD_END
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_forget_bak_obj, mod_forget_bak);

static mp_obj_t mod_unmark(mp_obj_t path) {
    moy_fs_unmark(path_arg(path));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_unmark_obj, mod_unmark);

// _remove and _mkdir: an error is the caller's to ignore, as the twin does.
static mp_obj_t mod_remove(mp_obj_t path) {
    const char *p = path_arg(path);
    GUARD_BEGIN
    moy_fs_remove(p);
    GUARD_END
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_remove_obj, mod_remove);

static mp_obj_t mod_mkdir(mp_obj_t path) {
    const char *p = path_arg(path);
    GUARD_BEGIN
    moy_fs_mkdir(p);
    GUARD_END
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_mkdir_obj, mod_mkdir);

static mp_obj_t mod_exists(mp_obj_t path) {
    const char *p = path_arg(path);
    int ok = 0;
    GUARD_BEGIN
    ok = moy_fs_exists(p);
    GUARD_END
    return mp_obj_new_bool(ok);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_exists_obj, mod_exists);

// -- JSON as Python objects -------------------------------------------------------------
//
// What json.loads makes of a scanned span: a dict (a repeated key keeps its
// last value), a list, a str, an int (any size), a float, True, False, None.

static mp_obj_t json_str(const char *v, const char *ve) {
    size_t n = moy_json_strlen(v, ve);
    vstr_t vs;
    vstr_init_len(&vs, n);
    moy_json_str(v, ve, vs.buf);
    return mp_obj_new_str_from_vstr(&vs);
}

// `depth` containers deep (the outermost is the first): past MOY_JSON_DEPTH,
// or with the C stack short, it raises instead of recursing.
static mp_obj_t json_obj(const char *v, const char *ve, uint32_t depth) {
    switch (moy_json_kind(v, ve)) {
        case MOY_JSON_STR:
            return json_str(v, ve);
        case MOY_JSON_INT:
            return mp_parse_num_integer(v, (size_t)(ve - v), 10, NULL);
        case MOY_JSON_FLOAT:
            #if MICROPY_PY_BUILTINS_FLOAT
            if (*v == 'N') {
                return mp_obj_new_float(MICROPY_FLOAT_C_FUN(nan)(""));
            }
            if (*v == 'I' || (*v == '-' && v[1] == 'I')) {
                mp_float_t inf = (mp_float_t)INFINITY;
                return mp_obj_new_float(*v == '-' ? -inf : inf);
            }
            return mp_parse_num_float(v, (size_t)(ve - v), false, NULL);
            #else
            return mp_const_none;
            #endif
        case MOY_JSON_ARR: {
            if (depth > MOY_JSON_DEPTH) {
                mp_raise_ValueError(MP_ERROR_TEXT("JSON nested too deep"));
            }
            MP_STACK_CHECK();
            mp_obj_t l = mp_obj_new_list(0, NULL);
            moy_json_iter_t it;
            const char *e, *ee;
            moy_json_iter(&it, v, ve);
            while (moy_json_next(&it, NULL, NULL, &e, &ee)) {
                mp_obj_list_append(l, json_obj(e, ee, depth + 1u));
            }
            return l;
        }
        case MOY_JSON_OBJ: {
            if (depth > MOY_JSON_DEPTH) {
                mp_raise_ValueError(MP_ERROR_TEXT("JSON nested too deep"));
            }
            MP_STACK_CHECK();
            mp_obj_t d = mp_obj_new_dict(0);
            moy_json_iter_t it;
            const char *k, *ke, *e, *ee;
            moy_json_iter(&it, v, ve);
            while (moy_json_next(&it, &k, &ke, &e, &ee)) {
                mp_obj_dict_store(d, json_str(k, ke), json_obj(e, ee, depth + 1u));
            }
            return d;
        }
        case MOY_JSON_TRUE:
            return mp_const_true;
        case MOY_JSON_FALSE:
            return mp_const_false;
        default:
            return mp_const_none;
    }
}

static mp_obj_t json_or(moy_cat_json_t f, mp_obj_t dflt) {
    return f.v != NULL ? json_obj(f.v, f.e, 1u) : dflt;
}

static mp_obj_t int_or(const moy_cat_int_t *i, mp_obj_t dflt) {
    if (i->kind == 1) {
        return mp_obj_new_int_from_ll(i->value);
    }
    if (i->kind == 2) {
        const char *v = i->json.v, *ve = i->json.e;
        if (moy_json_kind(v, ve) == MOY_JSON_INT) {
            return mp_parse_num_integer(v, (size_t)(ve - v), 10, NULL);
        }
        #if MICROPY_PY_BUILTINS_FLOAT
        if (moy_json_kind(v, ve) == MOY_JSON_FLOAT) {
            return mp_obj_new_int_from_float(mp_obj_get_float(json_obj(v, ve, 1u)));
        }
        #endif
        mp_obj_t s = json_obj(v, ve, 1u);
        return mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_int), s);
    }
    return dflt;
}

static mp_obj_t new_str(const char *p, size_t n) {
    return mp_obj_new_str(p, n);
}

static void put(mp_obj_t d, qstr k, mp_obj_t v) {
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(k), v);
}

typedef struct {
    mp_obj_t out;               // the list, or the one entry
    const char *root;           // the path's prefix, or the whole path
    size_t root_n;
    int whole_path;
} cat_ctx_t;

static void cat_note(void *ctx, const char *what, const char *path,
                     const char *detail) {
    (void)ctx;
    if (detail != NULL) {
        mp_printf(&mp_plat_print, "Moybyte cart %s: %s %s\n", what, path, detail);
    } else {
        mp_printf(&mp_plat_print, "Moybyte cart %s: %s\n", what, path);
    }
}

static mp_obj_t entry_dict(const cat_ctx_t *c, const moy_cat_entry_t *e) {
    mp_obj_t d = mp_obj_new_dict(24);
    if (c->whole_path) {
        put(d, MP_QSTR_path, new_str(c->root, c->root_n));
    } else {
        vstr_t vs;
        vstr_init(&vs, c->root_n + e->folder_n + 1u);
        vstr_add_strn(&vs, c->root, c->root_n);
        vstr_add_byte(&vs, '/');
        vstr_add_strn(&vs, e->folder, e->folder_n);
        put(d, MP_QSTR_path, mp_obj_new_str_from_vstr(&vs));
    }
    put(d, MP_QSTR_id, new_str(e->id, e->id_n));
    put(d, MP_QSTR_title, json_or(e->title, MP_OBJ_NEW_QSTR(MP_QSTR_cart)));
    put(d, MP_QSTR_author, json_or(e->author, MP_OBJ_NEW_QSTR(MP_QSTR_)));
    put(d, MP_QSTR_type, json_or(e->type, MP_OBJ_NEW_QSTR(e->spec ? MP_QSTR_game : MP_QSTR_app)));
    put(d, MP_QSTR_runtime, json_or(e->runtime,
                                    MP_OBJ_NEW_QSTR(e->spec ? MP_QSTR_lua : MP_QSTR_python)));
    put(d, MP_QSTR_main, new_str(e->main, e->main_n));
    put(d, MP_QSTR_memory, e->compiled ? int_or(&e->memory, mp_const_none) : mp_const_none);
    put(d, MP_QSTR_writable, e->compiled && e->writable_ok ? json_or(e->writable, mp_const_none)
                                                          : mp_const_none);
    put(d, MP_QSTR_version, int_or(&e->version, MP_OBJ_NEW_SMALL_INT(0)));
    put(d, MP_QSTR_format, json_or(e->format, mp_obj_new_str(MOY_CAT_FORMAT,
                                                             strlen(MOY_CAT_FORMAT))));
    put(d, MP_QSTR_graduated, mp_obj_new_bool(e->graduated));
    put(d, MP_QSTR_fps, json_or(e->fps, MP_OBJ_NEW_SMALL_INT(e->spec ? 30 : 0)));
    put(d, MP_QSTR_palette, json_or(e->palette, mp_const_none));
    put(d, MP_QSTR_extensions, json_or(e->extensions, mp_obj_new_list(0, NULL)));
    if (e->icon_ok) {
        mp_obj_t t[3] = { mp_obj_new_int_from_ll(e->icon[0]),
                          mp_obj_new_int_from_ll(e->icon[1]),
                          mp_obj_new_int_from_ll(e->icon[2]) };
        put(d, MP_QSTR_icon, mp_obj_new_tuple(3, t));
    } else {
        put(d, MP_QSTR_icon, mp_const_none);
    }
    put(d, MP_QSTR_edit, json_or(e->edit, mp_obj_new_list(0, NULL)));
    put(d, MP_QSTR_permissions, json_or(e->permissions, mp_obj_new_list(0, NULL)));
    if (e->input_n == MOY_CAT_NO_INPUT) {
        put(d, MP_QSTR_input, mp_const_none);
    } else {
        static const qstr kinds[MOY_CAT_INPUT_KINDS] = {
            MP_QSTR_buttons, MP_QSTR_touch, MP_QSTR_keyboard,
        };
        mp_obj_t t = mp_obj_new_tuple(e->input_n, NULL);
        for (size_t i = 0; i < e->input_n; i++) {
            ((mp_obj_tuple_t *)MP_OBJ_TO_PTR(t))->items[i] = MP_OBJ_NEW_QSTR(kinds[e->input[i]]);
        }
        put(d, MP_QSTR_input, t);
    }
    if (e->canvas == 1) {
        mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(e->canvas_w), MP_OBJ_NEW_SMALL_INT(e->canvas_h) };
        put(d, MP_QSTR_canvas, mp_obj_new_tuple(2, t));
    } else {
        put(d, MP_QSTR_canvas, e->canvas == 2 ? json_or(e->canvas_raw, mp_const_none)
                                              : mp_const_none);
    }
    if (e->broken != NULL) {
        put(d, MP_QSTR_broken, mp_obj_new_str(e->broken, strlen(e->broken)));
    }
    mp_obj_t sn = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < e->scenes_n; i++) {
        mp_obj_list_append(sn, new_str(e->scenes[i], e->scene_n[i]));
    }
    put(d, MP_QSTR_scene_names, sn);
    if (e->rows) {
        mp_obj_t want = mp_obj_new_list(e->ph, NULL);
        for (size_t i = 0; i < e->ph; i++) {
            ((mp_obj_list_t *)MP_OBJ_TO_PTR(want))->items[i] =
                e->want[i] != NULL ? new_str(e->want[i], e->want_n[i]) : mp_const_none;
        }
        mp_obj_t t[3] = { MP_OBJ_NEW_SMALL_INT(e->pw), MP_OBJ_NEW_SMALL_INT(e->ph), want };
        put(d, MP_QSTR_icon_rows, mp_obj_new_tuple(3, t));
    } else {
        put(d, MP_QSTR_icon_rows, mp_const_none);
    }
    return d;
}

static int cat_list(void *ctx, const moy_cat_entry_t *e) {
    cat_ctx_t *c = ctx;
    mp_obj_list_append(c->out, entry_dict(c, e));
    return 0;
}

static int cat_one(void *ctx, const moy_cat_entry_t *e) {
    cat_ctx_t *c = ctx;
    c->out = entry_dict(c, e);
    return 0;
}

// catalogue(root): every cart folder's entry, in folder order; None when the
// root will not list.
static mp_obj_t mod_catalogue(mp_obj_t root) {
    cat_ctx_t c;
    c.root = mp_obj_str_get_data(root, &c.root_n);
    c.whole_path = 0;
    c.out = mp_obj_new_list(0, NULL);
    const char *p = path_arg(root);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_cat_scan(p, cat_list, cat_note, &c);
    GUARD_END
    if (rc == MOY_FS_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return c.out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_catalogue_obj, mod_catalogue);

// entry(path): the folder's entry, or None when it is no cart.
static mp_obj_t mod_entry(mp_obj_t path) {
    cat_ctx_t c;
    c.root = mp_obj_str_get_data(path, &c.root_n);
    c.whole_path = 1;
    c.out = mp_const_none;
    const char *p = path_arg(path);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_cat_entry(p, cat_one, cat_note, &c);
    GUARD_END
    if (rc && rc != MOY_FS_NONE) {
        raise_rc(rc);
    }
    return c.out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_entry_obj, mod_entry);

// -- loading a cart ---------------------------------------------------------------------

// A text as a str, once more after a collect if the heap said no: the main
// script is the largest single allocation a load makes (moy_carts._read_main).
static mp_obj_t big_str(const char *p, size_t n) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t o = mp_obj_new_str(p, n);
        nlr_pop();
        return o;
    }
    if (!mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(((mp_obj_base_t *)nlr.ret_val)->type),
                                 MP_OBJ_FROM_PTR(&mp_type_MemoryError))) {
        nlr_jump(nlr.ret_val);
    }
    gc_collect();
    return mp_obj_new_str(p, n);
}

static mp_obj_t text_or_none(const char *p, size_t n) {
    return p != NULL ? mp_obj_new_str(p, n) : mp_const_none;
}

static mp_obj_t files_list(const moy_load_file_t *f, size_t n) {
    mp_obj_t l = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < n; i++) {
        mp_obj_t t[2] = { mp_obj_new_str(f[i].name, f[i].name_n),
                          mp_obj_new_str(f[i].text, f[i].n) };
        mp_obj_list_append(l, mp_obj_new_tuple(2, t));
    }
    return l;
}

static mp_obj_t files_dict(const moy_load_file_t *f, size_t n) {
    mp_obj_t d = mp_obj_new_dict(n);
    for (size_t i = 0; i < n; i++) {
        mp_obj_dict_store(d, mp_obj_new_str(f[i].name, f[i].name_n),
                          mp_obj_new_str(f[i].text, f[i].n));
    }
    return d;
}

// dict.update(x) for what moy_cat's dict_takes let through: a mapping or pairs.
static void update_from(mp_obj_t d, const char *v, const char *ve) {
    if (v == NULL) {
        return;
    }
    moy_json_iter_t it, pair;
    const char *k, *ke, *x, *xe, *y, *ye;
    if (*v == '{') {
        moy_json_iter(&it, v, ve);
        while (moy_json_next(&it, &k, &ke, &x, &xe)) {
            mp_obj_dict_store(d, json_str(k, ke), json_obj(x, xe, 1u));
        }
    } else if (*v == '[') {
        moy_json_iter(&it, v, ve);
        while (moy_json_next(&it, NULL, NULL, &x, &xe)) {
            moy_json_iter(&pair, x, xe);
            moy_json_next(&pair, NULL, NULL, &k, &ke);
            moy_json_next(&pair, NULL, NULL, &y, &ye);
            mp_obj_dict_store(d, json_obj(k, ke, 1u), json_obj(y, ye, 1u));
        }
    }
}

static int load_one(void *ctx, const moy_cart_t *c) {
    cat_ctx_t *cc = ctx;
    mp_obj_t d = entry_dict(cc, &c->e);
    mp_obj_dict_delete(d, MP_OBJ_NEW_QSTR(MP_QSTR_icon_rows));
    mp_obj_t cfg = mp_obj_new_dict(0);
    update_from(cfg, c->cfg_manifest.p, c->cfg_manifest.e);
    update_from(cfg, c->cfg_file.p, c->cfg_file.e);
    put(d, MP_QSTR_cfg, cfg);
    put(d, MP_QSTR_flags, text_or_none(c->flags, c->flags_n));
    put(d, MP_QSTR_src, big_str(c->src, c->src_n));
    put(d, MP_QSTR_src_before, files_list(c->before, c->before_n));
    put(d, MP_QSTR_src_after, files_list(c->after, c->after_n));
    put(d, MP_QSTR_sprites, text_or_none(c->sprites, c->sprites_n));
    put(d, MP_QSTR_sounds, c->sounds.p != NULL ? json_obj(c->sounds.p, c->sounds.e, 1u)
                                                : mp_const_none);
    put(d, MP_QSTR_map, text_or_none(c->map, c->map_n));
    put(d, MP_QSTR_blocks, c->blocks.p != NULL ? json_obj(c->blocks.p, c->blocks.e, 1u)
                                                : mp_const_none);
    put(d, MP_QSTR_images, files_dict(c->images, c->images_n));
    put(d, MP_QSTR_scenes, files_dict(c->scenes, c->scenes_n));
    cc->out = d;
    return 0;
}

// load(path): the cart whole, or None when it will not load.
static mp_obj_t mod_load(mp_obj_t path) {
    cat_ctx_t c;
    c.root = mp_obj_str_get_data(path, &c.root_n);
    c.whole_path = 1;
    c.out = mp_const_none;
    const char *p = path_arg(path);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_cat_load(p, load_one, cat_note, &c);
    GUARD_END
    if (rc && rc != MOY_FS_NONE) {
        raise_rc(rc);
    }
    return c.out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_load_obj, mod_load);

// -- the journal ---------------------------------------------------------------------------

// files: None (every file) or a sequence of names, as C strings on the gc heap.
static const char *const *file_list(mp_obj_t files, size_t *n) {
    if (files == mp_const_none) {
        *n = 0;
        return NULL;
    }
    mp_obj_t *items;
    mp_obj_get_array(files, n, &items);
    const char **out = m_new(const char *, *n ? *n : 1u);
    for (size_t i = 0; i < *n; i++) {
        out[i] = mp_obj_str_get_str(items[i]);
    }
    return out;
}

// journal_append(cart, file, data, grad, ops_json, ts) -> seq | None
static mp_obj_t mod_journal_append(size_t n_args, const mp_obj_t *args) {
    const char *cart = path_arg(args[0]);
    const char *file = mp_obj_str_get_str(args[1]);
    size_t dn;
    const char *data = mp_obj_str_get_data(args[2], &dn);
    int grad = args[3] == mp_const_none ? -1 : (mp_obj_get_int(args[3]) != 0);
    size_t on = 0;
    const char *ops = args[4] == mp_const_none ? NULL : mp_obj_str_get_data(args[4], &on);
    int64_t ts = mp_obj_get_int(args[5]);
    uint32_t seq = 0;
    int rc = 0;
    GUARD_BEGIN
    rc = moy_journal_append(cart, file, data, dn, grad, ops, on, ts, &seq);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return seq ? mp_obj_new_int_from_uint(seq) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_journal_append_obj, 6, 6, mod_journal_append);

static mp_obj_t walk(mp_obj_t cart_o, mp_obj_t files_o, int redo) {
    const char *cart = path_arg(cart_o);
    size_t nf;
    const char *const *files = file_list(files_o, &nf);
    char out[256];
    int rc = 0;
    GUARD_BEGIN
    rc = redo ? moy_journal_redo(cart, files, nf, out, sizeof out)
              : moy_journal_undo(cart, files, nf, out, sizeof out);
    GUARD_END
    if (rc < 0) {
        raise_rc(-rc);
    }
    return rc ? mp_obj_new_str(out, strlen(out)) : mp_const_none;
}

static mp_obj_t mod_journal_undo(mp_obj_t cart, mp_obj_t files) {
    return walk(cart, files, 0);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_journal_undo_obj, mod_journal_undo);

static mp_obj_t mod_journal_redo(mp_obj_t cart, mp_obj_t files) {
    return walk(cart, files, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_journal_redo_obj, mod_journal_redo);

static mp_obj_t mod_journal_can(mp_obj_t cart_o, mp_obj_t redo, mp_obj_t files_o) {
    const char *cart = path_arg(cart_o);
    size_t nf;
    const char *const *files = file_list(files_o, &nf);
    int ok = 0;
    GUARD_BEGIN
    ok = moy_journal_can(cart, mp_obj_is_true(redo), files, nf);
    GUARD_END
    return mp_obj_new_bool(ok);
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_journal_can_obj, mod_journal_can);

static mp_obj_t mod_journal_compact(mp_obj_t cart_o) {
    const char *cart = path_arg(cart_o);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_journal_compact(cart);
    GUARD_END
    if (rc < 0) {
        raise_rc(-rc);
    }
    return MP_OBJ_NEW_SMALL_INT(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_journal_compact_obj, mod_journal_compact);

static int list_ent(void *ctx, uint32_t seq, const char *line, size_t n) {
    (void)seq;
    mp_obj_list_append(*(mp_obj_t *)ctx, json_obj(line, line + n, 1u));
    return 0;
}

static mp_obj_t mod_journal_list(mp_obj_t cart_o, mp_obj_t file_o) {
    const char *cart = path_arg(cart_o);
    const char *file = file_o == mp_const_none ? NULL : mp_obj_str_get_str(file_o);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_journal_list(cart, file, list_ent, &out);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_journal_list_obj, mod_journal_list);

static mp_obj_t mod_journal_snap(mp_obj_t cart_o, mp_obj_t seq) {
    const char *cart = path_arg(cart_o);
    uint32_t s = (uint32_t)mp_obj_get_int(seq);
    moy_buf_t b = { NULL, 0 };
    int rc = 0;
    GUARD_BEGIN
    rc = moy_journal_snap(cart, s, &b);
    GUARD_END
    if (rc == MOY_FS_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return take(&b, 1);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_journal_snap_obj, mod_journal_snap);

static mp_obj_t mod_journal_restore(mp_obj_t cart_o, mp_obj_t seq, mp_obj_t ts) {
    const char *cart = path_arg(cart_o);
    uint32_t s = (uint32_t)mp_obj_get_int(seq), out = 0;
    int64_t t = mp_obj_get_int(ts);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_journal_restore(cart, s, t, &out);
    GUARD_END
    if (rc == MOY_FS_NONE) {
        return mp_const_none;
    }
    if (rc) {
        raise_rc(rc);
    }
    return out ? mp_obj_new_int_from_uint(out) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_journal_restore_obj, mod_journal_restore);

static mp_obj_t mod_graduate(mp_obj_t cart_o, mp_obj_t value) {
    const char *cart = path_arg(cart_o);
    int done = 0;
    GUARD_BEGIN
    done = moy_journal_graduate(cart, mp_obj_is_true(value));
    GUARD_END
    return mp_obj_new_bool(done);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_graduate_obj, mod_graduate);

// -- a cart as it travels ------------------------------------------------------------------

static mp_obj_t mod_skip(mp_obj_t name, mp_obj_t history) {
    size_t n;
    const char *s = mp_obj_str_get_data(name, &n);
    return mp_obj_new_bool(moy_store_skip(s, n, mp_obj_is_true(history)));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_skip_obj, mod_skip);

// pack(cart, folder, dest, history) -> files written
static mp_obj_t mod_pack(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    const char *cart = path_arg(args[0]), *dest = path_arg(args[2]);
    const char *folder = mp_obj_str_get_str(args[1]);
    int history = mp_obj_is_true(args[3]);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_pack(cart, folder, dest, history);
    GUARD_END
    if (rc < 0) {
        raise_rc(-rc);
    }
    return MP_OBJ_NEW_SMALL_INT(rc);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_pack_obj, 4, 4, mod_pack);

// unpack(archive, dest) -> (files, top folder)
static mp_obj_t mod_unpack(mp_obj_t archive, mp_obj_t dest) {
    const char *a = path_arg(archive), *d = path_arg(dest);
    char top[128];
    int rc = 0;
    GUARD_BEGIN
    rc = moy_unpack(a, d, top, sizeof top);
    GUARD_END
    if (rc < 0) {
        raise_rc(-rc);
    }
    mp_obj_t t[2] = { MP_OBJ_NEW_SMALL_INT(rc), mp_obj_new_str(top, strlen(top)) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_unpack_obj, mod_unpack);

static mp_obj_t mod_adopt(mp_obj_t stage, mp_obj_t target) {
    const char *s = path_arg(stage), *t = path_arg(target);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_adopt(s, t);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_adopt_obj, mod_adopt);

static mp_obj_t mod_rmtree(mp_obj_t path) {
    const char *p = path_arg(path);
    GUARD_BEGIN
    moy_cat_rmtree(p);
    GUARD_END
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_rmtree_obj, mod_rmtree);

static mp_obj_t mod_copy_files(mp_obj_t src, mp_obj_t dst, mp_obj_t main) {
    const char *s = path_arg(src), *d = path_arg(dst), *m = mp_obj_str_get_str(main);
    int rc = 0;
    GUARD_BEGIN
    rc = moy_cat_copy(s, d, m);
    GUARD_END
    if (rc) {
        raise_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_copy_files_obj, mod_copy_files);

// canon(text): what json.dumps(json.loads(text)) writes, the C's way.
static mp_obj_t mod_canon(mp_obj_t text) {
    size_t n;
    const char *t = mp_obj_str_get_data(text, &n);
    const char *s = moy_json_ws(t, t + n);
    const char *e = moy_json_value(s, t + n, 1u);
    if (e == NULL || moy_json_ws(e, t + n) != t + n) {
        mp_raise_ValueError(MP_ERROR_TEXT("syntax error in JSON"));
    }
    size_t k = moy_json_canon(s, e, NULL, 0);
    if (k == MOY_JSON_DEEP) {
        mp_raise_ValueError(MP_ERROR_TEXT("JSON nested too deep"));
    }
    vstr_t vs;
    vstr_init_len(&vs, k);
    moy_json_canon(s, e, vs.buf, k);
    return mp_obj_new_str_from_vstr(&vs);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_canon_obj, mod_canon);

// loads(text): json.loads, through the store's scanner.
static mp_obj_t mod_loads(mp_obj_t text) {
    size_t n;
    const char *t = mp_obj_str_get_data(text, &n);
    const char *s = moy_json_ws(t, t + n);
    const char *e = moy_json_value(s, t + n, 1u);
    if (e == NULL || moy_json_ws(e, t + n) != t + n) {
        mp_raise_ValueError(MP_ERROR_TEXT("syntax error in JSON"));
    }
    return json_obj(s, e, 1u);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_loads_obj, mod_loads);

// -- the seed -------------------------------------------------------------------------------

// seed(root, roster, present, progress, ns): the packed roster's carts written
// into `root` where `present` ({folder: version}, the scan's) lacks them or
// holds an older version; `progress(index, total, title)` before each, dropped
// once it raises. The folders written, in roster order.
static mp_obj_t mod_seed(size_t n_args, const mp_obj_t *args) {
    const char *root = path_arg(args[0]);
    size_t total;
    mp_obj_t *items;
    mp_obj_get_array(args[1], &total, &items);
    mp_obj_t present = args[2];
    mp_obj_t progress = n_args > 3 ? args[3] : mp_const_none;
    const char *ns = n_args > 4 && args[4] != mp_const_none ? mp_obj_str_get_str(args[4]) : NULL;
    mp_obj_t wrote = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < total; i++) {
        mp_obj_t *ent;
        mp_obj_get_array_fixed_n(items[i], 3, &ent);
        size_t tn;
        const char *title = mp_obj_str_get_data(ent[0], &tn);
        if (progress != mp_const_none) {
            nlr_buf_t nlr;
            if (nlr_push(&nlr) == 0) {
                mp_obj_t a[3] = { MP_OBJ_NEW_SMALL_INT(i), MP_OBJ_NEW_SMALL_INT(total), ent[0] };
                mp_call_function_n_kw(progress, 3, 0, a);
                nlr_pop();
            } else {
                progress = mp_const_none;
            }
        }
        size_t fn = moy_seed_folder(title, tn, ns, NULL, 0);
        vstr_t folder;
        vstr_init_len(&folder, fn);
        moy_seed_folder(title, tn, ns, folder.buf, fn + 1u);
        mp_obj_t fobj = mp_obj_new_str_from_vstr(&folder);
        mp_map_elem_t *had = mp_map_lookup(mp_obj_dict_get_map(present), fobj, MP_MAP_LOOKUP);
        if (had != NULL && mp_binary_op(MP_BINARY_OP_LESS_EQUAL,
                                        mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_int), ent[1]),
                                        had->value) == mp_const_true) {
            continue;
        }
        mp_buffer_info_t bi;
        mp_get_buffer_raise(ent[2], &bi, MP_BUFFER_READ);
        const char *f = mp_obj_str_get_str(fobj);
        int rc = 0;
        GUARD_BEGIN
        moy_buf_t text = { NULL, 0 };
        rc = moy_seed_inflate(bi.buf, bi.len, &text);
        if (rc == 0) {
            rc = moy_seed_write(root, f, text.p, text.n);
            rc = rc < 0 ? -rc : rc == 1 ? -1 : 0;
        }
        moy_buf_free(&text);
        GUARD_END
        if (rc == -1) {
            mp_obj_list_append(wrote, fobj);
        } else if (rc) {
            raise_rc(rc);
        }
    }
    return wrote;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_seed_obj, 3, 5, mod_seed);

// folder(title, ns): the folder a seeded cart takes (moy_seed_folder).
static mp_obj_t mod_folder(mp_obj_t title, mp_obj_t ns) {
    size_t tn;
    const char *t = mp_obj_str_get_data(title, &tn);
    const char *n = ns != mp_const_none ? mp_obj_str_get_str(ns) : NULL;
    size_t fn = moy_seed_folder(t, tn, n, NULL, 0);
    vstr_t vs;
    vstr_init_len(&vs, fn);
    moy_seed_folder(t, tn, n, vs.buf, fn + 1u);
    return mp_obj_new_str_from_vstr(&vs);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_folder_obj, mod_folder);

static mp_obj_t mod_mem(void) {
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(mem_now),
                      mp_obj_new_int_from_uint(mem_high) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mem_obj, mod_mem);

#if MICROPY_VFS_FAT
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moy_store_card_stats_obj);
#endif

// The kernel's internal volume's VFS type (moy_kvfs.c), where it compiles.
#if MICROPY_VFS_LFS2 && ((defined(__has_include) && __has_include("esp_partition.h")) \
    || (defined(__unix__) && !defined(__EMSCRIPTEN__)))
#define MOY_STORE_KVFS 1
extern const mp_obj_type_t moy_kvfs_type;
#if !(defined(__has_include) && __has_include("esp_partition.h"))
// The unix port's medium, for the host's tests: the RAM image as bytes, and a
// new one loaded (None: formatted).
int moy_kvol_ram_load(const uint8_t *img, size_t n);
const uint8_t *moy_kvol_ram(size_t *n);

static mp_obj_t mod_kvol_image(void) {
    size_t n;
    const uint8_t *p = moy_kvol_ram(&n);
    return p ? mp_obj_new_bytes(p, n) : mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_kvol_image_obj, mod_kvol_image);

static mp_obj_t mod_kvol_load(mp_obj_t img) {
    int rc;
    if (img == mp_const_none) {
        rc = moy_kvol_ram_load(NULL, 0);
    } else {
        mp_buffer_info_t b;
        mp_get_buffer_raise(img, &b, MP_BUFFER_READ);
        rc = moy_kvol_ram_load(b.buf, b.len);
    }
    if (rc != 0) {
        mp_raise_OSError(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_kvol_load_obj, mod_kvol_load);
#define MOY_STORE_KVOL_RAM 1
#endif
#endif

// kvol() -> (mounted, the mount's errno, blocks, block size) of the kernel's
// internal volume, or None in an image without one.
static mp_obj_t mod_kvol(void) {
    if (moy_kvol_state == NULL) {
        return mp_const_none;
    }
    int mounted, err;
    uint32_t blocks, bsize;
    moy_kvol_state(&mounted, &err, &blocks, &bsize);
    mp_obj_t t[4] = {mp_obj_new_bool(mounted), MP_OBJ_NEW_SMALL_INT(err),
                     mp_obj_new_int_from_uint(blocks), mp_obj_new_int_from_uint(bsize)};
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_kvol_obj, mod_kvol);

static const mp_rom_map_elem_t moy_store_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR_kvol), MP_ROM_PTR(&mod_kvol_obj)},
    #if defined(MOY_STORE_KVFS)
    {MP_ROM_QSTR(MP_QSTR_KVfs), MP_ROM_PTR(&moy_kvfs_type)},
    #endif
    #if defined(MOY_STORE_KVOL_RAM)
    {MP_ROM_QSTR(MP_QSTR_kvol_image), MP_ROM_PTR(&mod_kvol_image_obj)},
    {MP_ROM_QSTR(MP_QSTR_kvol_load), MP_ROM_PTR(&mod_kvol_load_obj)},
    #endif
    #if MICROPY_VFS_FAT
    { MP_ROM_QSTR(MP_QSTR_card), MP_ROM_PTR(&moy_store_card_obj) },
    { MP_ROM_QSTR(MP_QSTR_card_stats), MP_ROM_PTR(&moy_store_card_stats_obj) },
    #endif
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_store) },
    { MP_ROM_QSTR(MP_QSTR_set_publish_root), MP_ROM_PTR(&mod_set_publish_root_obj) },
    { MP_ROM_QSTR(MP_QSTR_publish), MP_ROM_PTR(&mod_publish_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mod_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_write_bytes), MP_ROM_PTR(&mod_write_bytes_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_recover), MP_ROM_PTR(&mod_read_recover_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mod_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_bytes), MP_ROM_PTR(&mod_read_bytes_obj) },
    { MP_ROM_QSTR(MP_QSTR_read_stamped), MP_ROM_PTR(&mod_read_stamped_obj) },
    { MP_ROM_QSTR(MP_QSTR_bak_stamp), MP_ROM_PTR(&mod_bak_stamp_obj) },
    { MP_ROM_QSTR(MP_QSTR_stamp), MP_ROM_PTR(&mod_stamp_obj) },
    { MP_ROM_QSTR(MP_QSTR_claim), MP_ROM_PTR(&mod_claim_obj) },
    { MP_ROM_QSTR(MP_QSTR_forget_bak), MP_ROM_PTR(&mod_forget_bak_obj) },
    { MP_ROM_QSTR(MP_QSTR_unmark), MP_ROM_PTR(&mod_unmark_obj) },
    { MP_ROM_QSTR(MP_QSTR_remove), MP_ROM_PTR(&mod_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_mkdir), MP_ROM_PTR(&mod_mkdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_exists), MP_ROM_PTR(&mod_exists_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&mod_mem_obj) },
    { MP_ROM_QSTR(MP_QSTR_catalogue), MP_ROM_PTR(&mod_catalogue_obj) },
    { MP_ROM_QSTR(MP_QSTR_entry), MP_ROM_PTR(&mod_entry_obj) },
    { MP_ROM_QSTR(MP_QSTR_load), MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_skip), MP_ROM_PTR(&mod_skip_obj) },
    { MP_ROM_QSTR(MP_QSTR_pack), MP_ROM_PTR(&mod_pack_obj) },
    { MP_ROM_QSTR(MP_QSTR_unpack), MP_ROM_PTR(&mod_unpack_obj) },
    { MP_ROM_QSTR(MP_QSTR_adopt), MP_ROM_PTR(&mod_adopt_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_append), MP_ROM_PTR(&mod_journal_append_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_undo), MP_ROM_PTR(&mod_journal_undo_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_redo), MP_ROM_PTR(&mod_journal_redo_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_can), MP_ROM_PTR(&mod_journal_can_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_compact), MP_ROM_PTR(&mod_journal_compact_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_list), MP_ROM_PTR(&mod_journal_list_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_snap), MP_ROM_PTR(&mod_journal_snap_obj) },
    { MP_ROM_QSTR(MP_QSTR_journal_restore), MP_ROM_PTR(&mod_journal_restore_obj) },
    { MP_ROM_QSTR(MP_QSTR_graduate), MP_ROM_PTR(&mod_graduate_obj) },
    { MP_ROM_QSTR(MP_QSTR_rmtree), MP_ROM_PTR(&mod_rmtree_obj) },
    { MP_ROM_QSTR(MP_QSTR_copy_files), MP_ROM_PTR(&mod_copy_files_obj) },
    { MP_ROM_QSTR(MP_QSTR_canon), MP_ROM_PTR(&mod_canon_obj) },
    { MP_ROM_QSTR(MP_QSTR_seed), MP_ROM_PTR(&mod_seed_obj) },
    { MP_ROM_QSTR(MP_QSTR_folder), MP_ROM_PTR(&mod_folder_obj) },
    { MP_ROM_QSTR(MP_QSTR_loads), MP_ROM_PTR(&mod_loads_obj) },
};
static MP_DEFINE_CONST_DICT(moy_store_globals, moy_store_globals_table);

const mp_obj_module_t moy_store_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_store_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_store, moy_store_module);
