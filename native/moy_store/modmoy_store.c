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

#include "moy_fs.h"

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

int moy_vol_at(const char *path, moy_vol_t *v, const char **rest) {
    const char *r = path;
    mp_vfs_mount_t *m = mp_vfs_lookup_path(path, &r);
    if (m == MP_VFS_NONE || m == MP_VFS_ROOT) {
        return MP_ENODEV;
    }
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

static mp_obj_t mod_mem(void) {
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(mem_now),
                      mp_obj_new_int_from_uint(mem_high) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mem_obj, mod_mem);

static const mp_rom_map_elem_t moy_store_globals_table[] = {
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
};
static MP_DEFINE_CONST_DICT(moy_store_globals, moy_store_globals_table);

const mp_obj_module_t moy_store_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_store_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_store, moy_store_module);
