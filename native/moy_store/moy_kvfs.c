// The internal flash volume, the kernel's (docs/kernel_survival_2026-10.md
// section 6.6, docs/kernel_store_2026-10.md section 3): one lfs2_t over the
// "vfs" partition with C callbacks over esp_partition, its config and caches
// in kernel memory, mounted at the kernel's first ask and kept across every
// VM. Python reaches it through `KVfs`, a VFS type of the kernel's that the
// VM's start mounts at "/" in place of the port's `_boot.py` (which would mount
// a VfsLfs2 of its own over the same flash). A partition the kernel cannot
// mount is the port's again, _boot.py and its inisetup included. The
// store's C (moy_vol) resolves "/" to this instance, never a borrowed one.
//
// KVfs is modelled on MicroPython's own littlefs VFS (extmod/vfs_lfsx.c and
// vfs_lfsx_file.c, MIT): the same methods, the same errors, the same on-disk
// shape -- 4 KB blocks, 32-byte reads and programs, a 128-byte cache, and each
// file's mtime as attribute 1 (nanoseconds since 1970, little-endian) -- so a
// store written by either reads whole on the other. What differs is ownership:
// the instance is not the VM's, and a VM's open files and listings are gc
// objects linked into it, so a new VM starts by unlinking whatever the last
// one left (its sweep closes them first; this is the guard).
//
// Two media: the "vfs" partition on a board, and on the unix port a RAM image
// (MOY_KVFS_RAM_BLOCKS blocks) the host's tests format, fill and read back
// through VfsLfs2 (tests/test_store_on_vfs.py). An image with no littlefs, or
// neither medium (the browser's), compiles this file to nothing.

#include "py/mpconfig.h"
#if MICROPY_VFS_LFS2
#if defined(__has_include) && __has_include("esp_partition.h")
#define MOY_KVFS 1
#define MOY_KVFS_FLASH 1
#elif defined(__unix__) && !defined(__EMSCRIPTEN__)
#define MOY_KVFS 1
#define MOY_KVFS_FLASH 0
#define MOY_KVFS_RAM_BLOCKS 4096
#endif
#endif

#if defined(MOY_KVFS)

#include <stdlib.h>
#include <string.h>

#if MOY_KVFS_FLASH
#include "esp_heap_caps.h"
#include "esp_partition.h"
#endif
#include "extmod/vfs.h"
#include "lib/littlefs/lfs2.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/objstr.h"
#include "py/runtime.h"
#include "py/stream.h"
#include "shared/timeutils/timeutils.h"

#define KV_READ 32
#define KV_PROG 32
#define KV_LOOKAHEAD 32
#define KV_CACHE 128                    // MIN(block, 4 * MAX(read, prog)), VfsLfs2's
#define KV_ATTR_MTIME 1

typedef struct {
    #if MOY_KVFS_FLASH
    const esp_partition_t *part;
    #else
    uint8_t *ram;
    #endif
    struct lfs2_config cfg;
    lfs2_t lfs;
    uint8_t read[KV_CACHE], prog[KV_CACHE], look[KV_LOOKAHEAD];
    int err;                            // the mount's, 0 when it holds
} kvol_t;

static kvol_t *s_kv;

#if MOY_KVFS_FLASH
static int kv_read(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off, void *buf,
                   lfs2_size_t n) {
    const kvol_t *k = c->context;
    return esp_partition_read(k->part, b * c->block_size + off, buf, n) == ESP_OK ? 0 : LFS2_ERR_IO;
}

static int kv_prog(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off,
                   const void *buf, lfs2_size_t n) {
    const kvol_t *k = c->context;
    return esp_partition_write(k->part, b * c->block_size + off, buf, n) == ESP_OK ? 0 : LFS2_ERR_IO;
}

static int kv_erase(const struct lfs2_config *c, lfs2_block_t b) {
    const kvol_t *k = c->context;
    return esp_partition_erase_range(k->part, b * c->block_size, c->block_size) == ESP_OK
           ? 0 : LFS2_ERR_IO;
}
#else
static int kv_read(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off, void *buf,
                   lfs2_size_t n) {
    const kvol_t *k = c->context;
    memcpy(buf, k->ram + b * c->block_size + off, n);
    return 0;
}

static int kv_prog(const struct lfs2_config *c, lfs2_block_t b, lfs2_off_t off,
                   const void *buf, lfs2_size_t n) {
    const kvol_t *k = c->context;
    memcpy(k->ram + b * c->block_size + off, buf, n);
    return 0;
}

static int kv_erase(const struct lfs2_config *c, lfs2_block_t b) {
    const kvol_t *k = c->context;
    memset(k->ram + b * c->block_size, 0xff, c->block_size);
    return 0;
}
#endif

static int kv_sync(const struct lfs2_config *c) {
    (void)c;
    return 0;
}

// The kernel's instance, mounted at the first ask: the lfs2_t, or NULL.
static kvol_t *kvol_new(void) {
    #if MOY_KVFS_FLASH
    const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                                        ESP_PARTITION_SUBTYPE_ANY, "vfs");
    if (p == NULL) {
        return NULL;
    }
    kvol_t *k = heap_caps_calloc(1, sizeof(kvol_t), MALLOC_CAP_SPIRAM);
    if (k == NULL) {
        k = heap_caps_calloc(1, sizeof(kvol_t), MALLOC_CAP_8BIT);
    }
    if (k == NULL) {
        return NULL;
    }
    k->part = p;
    uint32_t blocks = p->size / 4096;
    #else
    kvol_t *k = calloc(1, sizeof(kvol_t));
    uint32_t blocks = MOY_KVFS_RAM_BLOCKS;
    if (k == NULL || (k->ram = malloc(blocks * 4096u)) == NULL) {
        free(k);
        return NULL;
    }
    memset(k->ram, 0xff, blocks * 4096u);
    #endif
    struct lfs2_config *c = &k->cfg;
    c->context = k;
    c->read = kv_read;
    c->prog = kv_prog;
    c->erase = kv_erase;
    c->sync = kv_sync;
    c->read_size = KV_READ;
    c->prog_size = KV_PROG;
    c->block_size = 4096;
    c->block_count = blocks;
    c->block_cycles = 100;
    c->cache_size = KV_CACHE;
    c->lookahead_size = KV_LOOKAHEAD;
    c->read_buffer = k->read;
    c->prog_buffer = k->prog;
    c->lookahead_buffer = k->look;
    return k;
}

// The kernel's instance, mounted at the first ask: the lfs2_t, or NULL.
lfs2_t *moy_kvol_lfs(void) {
    if (s_kv == NULL) {
        s_kv = kvol_new();
        if (s_kv == NULL) {
            return NULL;
        }
        // Never a format: a partition that does not mount is left to the
        // port's _boot.py and inisetup, as before the kernel owned it, and the
        // next boot mounts what they made.
        int rc = lfs2_mount(&s_kv->lfs, &s_kv->cfg);
        s_kv->err = rc < 0 ? -rc : 0;
    }
    return s_kv->err == 0 ? &s_kv->lfs : NULL;
}

#if !MOY_KVFS_FLASH
// The host's medium: its image as bytes, a new image (formatted when `img` is
// NULL), mounted again either way. 0, or an errno.
int moy_kvol_ram_load(const uint8_t *img, size_t n) {
    if (s_kv == NULL && (s_kv = kvol_new()) == NULL) {
        return MP_ENOMEM;
    }
    if (s_kv->err == 0) {
        lfs2_unmount(&s_kv->lfs);
    }
    size_t cap = (size_t)MOY_KVFS_RAM_BLOCKS * 4096u;
    if (img != NULL) {
        if (n != cap) {
            return MP_EINVAL;
        }
        memcpy(s_kv->ram, img, n);
    } else {
        memset(s_kv->ram, 0xff, cap);
        lfs2_format(&s_kv->lfs, &s_kv->cfg);
    }
    int rc = lfs2_mount(&s_kv->lfs, &s_kv->cfg);
    s_kv->err = rc < 0 ? -rc : 0;
    return s_kv->err;
}

const uint8_t *moy_kvol_ram(size_t *n) {
    *n = s_kv != NULL ? (size_t)MOY_KVFS_RAM_BLOCKS * 4096u : 0;
    return s_kv != NULL ? s_kv->ram : NULL;
}
#endif

// -- KVfs: the VM's view of the kernel's volume ---------------------------------------

typedef struct {
    mp_obj_base_t base;
    lfs2_t *lfs;
    vstr_t cur_dir;
} kvfs_t;

typedef struct {
    mp_obj_base_t base;
    kvfs_t *vfs;                        // NULL once closed
    uint8_t mtime[8];
    lfs2_file_t file;
    struct lfs2_file_config cfg;
    struct lfs2_attr attrs[1];
    uint8_t file_buffer[KV_CACHE];
} kvfs_file_t;

extern const mp_obj_type_t moy_kvfs_type;
#define kvfs_type moy_kvfs_type
static const mp_obj_type_t kvfs_fileio_type;
static const mp_obj_type_t kvfs_textio_type;

static void raise_lfs(int ret) {
    mp_raise_OSError(-ret);
}

static void get_mtime(uint8_t buf[8]) {
    uint64_t ns = timeutils_nanoseconds_since_epoch_to_nanoseconds_since_1970(mp_hal_time_ns());
    for (size_t i = 0; i < 8; ++i) {
        buf[i] = (uint8_t)ns;
        ns >>= 8;
    }
}

static const char *make_path(kvfs_t *self, mp_obj_t path_in) {
    const char *path = mp_obj_str_get_str(path_in);
    if (path[0] != '/') {
        size_t l = vstr_len(&self->cur_dir);
        if (l > 0) {
            vstr_add_str(&self->cur_dir, path);
            path = vstr_null_terminated_str(&self->cur_dir);
            self->cur_dir.len = l;
        }
    }
    return path;
}

// The VM's KVfs over the kernel's instance, or an OSError.
static mp_obj_t kvfs_new(void) {
    lfs2_t *lfs = moy_kvol_lfs();
    if (lfs == NULL) {
        mp_raise_OSError(s_kv != NULL && s_kv->err ? s_kv->err : MP_ENODEV);
    }
    kvfs_t *self = m_new_obj(kvfs_t);
    self->base.type = &kvfs_type;
    self->lfs = lfs;
    vstr_init(&self->cur_dir, 16);
    vstr_add_byte(&self->cur_dir, '/');
    return MP_OBJ_FROM_PTR(self);
}

static mp_obj_t kvfs_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw,
                              const mp_obj_t *args) {
    (void)type;
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    return kvfs_new();
}

// -- files --------------------------------------------------------------------------

static void file_check(kvfs_file_t *f) {
    if (f->vfs == NULL) {
        mp_raise_ValueError(NULL);
    }
}

static mp_obj_t kvfs_open(mp_obj_t self_in, mp_obj_t path_in, mp_obj_t mode_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    int flags = 0;
    const mp_obj_type_t *type = &kvfs_textio_type;
    for (const char *m = mp_obj_str_get_str(mode_in); *m; ++m) {
        int nf = 0;
        switch (*m) {
            case 'r':
                nf = LFS2_O_RDONLY;
                break;
            case 'w':
                nf = LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_TRUNC;
                break;
            case 'x':
                nf = LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_EXCL;
                break;
            case 'a':
                nf = LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_APPEND;
                break;
            case '+':
                flags |= LFS2_O_RDWR;
                break;
            case 'b':
                type = &kvfs_fileio_type;
                break;
            case 't':
                type = &kvfs_textio_type;
                break;
        }
        if (nf) {
            if (flags) {
                mp_raise_ValueError(NULL);
            }
            flags = nf;
        }
    }
    if (flags == 0) {
        flags = LFS2_O_RDONLY;
    }
    kvfs_file_t *o = mp_obj_malloc_with_finaliser(kvfs_file_t, type);
    memset(&o->file, 0, sizeof(o->file));
    memset(&o->cfg, 0, sizeof(o->cfg));
    o->vfs = self;
    o->cfg.buffer = o->file_buffer;
    get_mtime(o->mtime);
    o->attrs[0].type = KV_ATTR_MTIME;
    o->attrs[0].buffer = o->mtime;
    o->attrs[0].size = sizeof(o->mtime);
    o->cfg.attrs = o->attrs;
    o->cfg.attr_count = 1;
    const char *path = make_path(self, path_in);
    int ret = lfs2_file_opencfg(self->lfs, &o->file, path, flags, &o->cfg);
    if (ret < 0) {
        o->vfs = NULL;
        raise_lfs(ret);
    }
    return MP_OBJ_FROM_PTR(o);
}
static MP_DEFINE_CONST_FUN_OBJ_3(kvfs_open_obj, kvfs_open);

static mp_uint_t file_read(mp_obj_t self_in, void *buf, mp_uint_t size, int *errcode) {
    kvfs_file_t *self = MP_OBJ_TO_PTR(self_in);
    file_check(self);
    lfs2_ssize_t sz = lfs2_file_read(self->vfs->lfs, &self->file, buf, size);
    if (sz < 0) {
        *errcode = -sz;
        return MP_STREAM_ERROR;
    }
    return (mp_uint_t)sz;
}

static mp_uint_t file_write(mp_obj_t self_in, const void *buf, mp_uint_t size, int *errcode) {
    kvfs_file_t *self = MP_OBJ_TO_PTR(self_in);
    file_check(self);
    get_mtime(self->mtime);
    lfs2_ssize_t sz = lfs2_file_write(self->vfs->lfs, &self->file, buf, size);
    if (sz < 0) {
        *errcode = -sz;
        return MP_STREAM_ERROR;
    }
    return (mp_uint_t)sz;
}

static mp_uint_t file_ioctl(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    kvfs_file_t *self = MP_OBJ_TO_PTR(self_in);
    if (request == MP_STREAM_SEEK) {
        file_check(self);
        struct mp_stream_seek_t *s = (struct mp_stream_seek_t *)arg;
        int res = lfs2_file_seek(self->vfs->lfs, &self->file, s->offset, s->whence);
        if (res >= 0) {
            res = lfs2_file_tell(self->vfs->lfs, &self->file);
        }
        if (res < 0) {
            *errcode = -res;
            return MP_STREAM_ERROR;
        }
        s->offset = res;
        return 0;
    }
    if (request == MP_STREAM_FLUSH) {
        file_check(self);
        int res = lfs2_file_sync(self->vfs->lfs, &self->file);
        if (res < 0) {
            *errcode = -res;
            return MP_STREAM_ERROR;
        }
        return 0;
    }
    if (request == MP_STREAM_CLOSE) {
        if (self->vfs == NULL) {
            return 0;
        }
        int res = lfs2_file_close(self->vfs->lfs, &self->file);
        self->vfs = NULL;
        if (res < 0) {
            *errcode = -res;
            return MP_STREAM_ERROR;
        }
        return 0;
    }
    *errcode = MP_EINVAL;
    return MP_STREAM_ERROR;
}

static void file_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    (void)kind;
    mp_printf(print, "<io.%s>", mp_obj_get_type_str(self_in));
}

static const mp_rom_map_elem_t file_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mp_stream_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_readlines), MP_ROM_PTR(&mp_stream_unbuffered_readlines_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mp_stream_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_flush), MP_ROM_PTR(&mp_stream_flush_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mp_stream_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_seek), MP_ROM_PTR(&mp_stream_seek_obj) },
    { MP_ROM_QSTR(MP_QSTR_tell), MP_ROM_PTR(&mp_stream_tell_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&mp_stream_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___enter__), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR___exit__), MP_ROM_PTR(&mp_stream___exit___obj) },
};
static MP_DEFINE_CONST_DICT(file_locals, file_locals_table);

static const mp_stream_p_t fileio_stream = {
    .read = file_read,
    .write = file_write,
    .ioctl = file_ioctl,
};

static MP_DEFINE_CONST_OBJ_TYPE(
    kvfs_fileio_type, MP_QSTR_FileIO, MP_TYPE_FLAG_ITER_IS_STREAM,
    print, file_print,
    protocol, &fileio_stream,
    locals_dict, &file_locals);

static const mp_stream_p_t textio_stream = {
    .read = file_read,
    .write = file_write,
    .ioctl = file_ioctl,
    .is_text = true,
};

static MP_DEFINE_CONST_OBJ_TYPE(
    kvfs_textio_type, MP_QSTR_TextIOWrapper, MP_TYPE_FLAG_ITER_IS_STREAM,
    print, file_print,
    protocol, &textio_stream,
    locals_dict, &file_locals);

// -- the folder verbs -----------------------------------------------------------------

typedef struct {
    mp_obj_base_t base;
    mp_fun_1_t iternext;
    mp_fun_1_t finaliser;
    bool is_str;
    kvfs_t *vfs;
    lfs2_dir_t dir;
} kvfs_ilistdir_t;

static mp_obj_t ilistdir_next(mp_obj_t self_in) {
    kvfs_ilistdir_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->vfs == NULL) {
        return MP_OBJ_STOP_ITERATION;
    }
    struct lfs2_info info;
    for (;;) {
        int ret = lfs2_dir_read(self->vfs->lfs, &self->dir, &info);
        if (ret == 0) {
            lfs2_dir_close(self->vfs->lfs, &self->dir);
            self->vfs = NULL;
            return MP_OBJ_STOP_ITERATION;
        }
        if (!(info.name[0] == '.' && (info.name[1] == '\0'
                                      || (info.name[1] == '.' && info.name[2] == '\0')))) {
            break;
        }
    }
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(4, NULL));
    t->items[0] = self->is_str ? mp_obj_new_str_from_cstr(info.name)
                  : mp_obj_new_bytes((const byte *)info.name, strlen(info.name));
    t->items[1] = MP_OBJ_NEW_SMALL_INT(info.type == LFS2_TYPE_REG ? MP_S_IFREG : MP_S_IFDIR);
    t->items[2] = MP_OBJ_NEW_SMALL_INT(0);
    t->items[3] = MP_OBJ_NEW_SMALL_INT(info.size);
    return MP_OBJ_FROM_PTR(t);
}

static mp_obj_t ilistdir_del(mp_obj_t self_in) {
    kvfs_ilistdir_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->vfs != NULL) {
        lfs2_dir_close(self->vfs->lfs, &self->dir);
        self->vfs = NULL;
    }
    return mp_const_none;
}

static mp_obj_t kvfs_ilistdir(size_t n_args, const mp_obj_t *args) {
    kvfs_t *self = MP_OBJ_TO_PTR(args[0]);
    bool is_str = true;
    const char *path;
    if (n_args == 2) {
        is_str = mp_obj_get_type(args[1]) != &mp_type_bytes;
        path = make_path(self, args[1]);
    } else {
        path = vstr_null_terminated_str(&self->cur_dir);
    }
    kvfs_ilistdir_t *it = mp_obj_malloc_with_finaliser(kvfs_ilistdir_t,
                                                       &mp_type_polymorph_iter_with_finaliser);
    it->iternext = ilistdir_next;
    it->finaliser = ilistdir_del;
    it->is_str = is_str;
    it->vfs = NULL;
    int ret = lfs2_dir_open(self->lfs, &it->dir, path);
    if (ret < 0) {
        raise_lfs(ret);
    }
    it->vfs = self;
    return MP_OBJ_FROM_PTR(it);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(kvfs_ilistdir_obj, 1, 2, kvfs_ilistdir);

static mp_obj_t kvfs_remove(mp_obj_t self_in, mp_obj_t path_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    int ret = lfs2_remove(self->lfs, make_path(self, path_in));
    if (ret < 0) {
        raise_lfs(ret);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(kvfs_remove_obj, kvfs_remove);

static mp_obj_t kvfs_rename(mp_obj_t self_in, mp_obj_t old_in, mp_obj_t new_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    const char *old = make_path(self, old_in);
    const char *path = mp_obj_str_get_str(new_in);
    vstr_t nu;
    vstr_init(&nu, vstr_len(&self->cur_dir));
    if (path[0] != '/') {
        vstr_add_strn(&nu, vstr_str(&self->cur_dir), vstr_len(&self->cur_dir));
    }
    vstr_add_str(&nu, path);
    int ret = lfs2_rename(self->lfs, old, vstr_null_terminated_str(&nu));
    vstr_clear(&nu);
    if (ret < 0) {
        raise_lfs(ret);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(kvfs_rename_obj, kvfs_rename);

static mp_obj_t kvfs_mkdir(mp_obj_t self_in, mp_obj_t path_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    int ret = lfs2_mkdir(self->lfs, make_path(self, path_in));
    if (ret < 0) {
        raise_lfs(ret);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(kvfs_mkdir_obj, kvfs_mkdir);

static mp_obj_t kvfs_chdir(mp_obj_t self_in, mp_obj_t path_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    const char *path = make_path(self, path_in);
    if (path[1] != '\0') {
        struct lfs2_info info;
        int ret = lfs2_stat(self->lfs, path, &info);
        if (ret < 0 || info.type != LFS2_TYPE_DIR) {
            mp_raise_OSError(MP_ENOENT);
        }
    }
    if (path == vstr_str(&self->cur_dir)) {
        self->cur_dir.len = strlen(path);
    } else {
        vstr_reset(&self->cur_dir);
        vstr_add_str(&self->cur_dir, path);
    }
    // A trailing '/' outside the root, then "." and ".." and repeated slashes
    // folded out, as VfsLfs2 keeps its working folder.
    if (vstr_len(&self->cur_dir) != 1) {
        vstr_add_byte(&self->cur_dir, '/');
        size_t to = 1, from = 1;
        char *cwd = vstr_str(&self->cur_dir);
        while (from < vstr_len(&self->cur_dir)) {
            for (; from < vstr_len(&self->cur_dir) && cwd[from] == '/'; ++from) {
            }
            if (from > to) {
                vstr_cut_out_bytes(&self->cur_dir, to, from - to);
                from = to;
            }
            for (; from < vstr_len(&self->cur_dir) && cwd[from] != '/'; ++from) {
            }
            if ((from - to) == 1 && cwd[to] == '.') {
                vstr_cut_out_bytes(&self->cur_dir, to, ++from - to);
                from = to;
            } else if ((from - to) == 2 && cwd[to] == '.' && cwd[to + 1] == '.') {
                if (to > 1) {
                    for (--to; to > 1 && cwd[to - 1] != '/'; --to) {
                    }
                }
                vstr_cut_out_bytes(&self->cur_dir, to, ++from - to);
                from = to;
            } else {
                to = ++from;
            }
        }
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(kvfs_chdir_obj, kvfs_chdir);

static mp_obj_t kvfs_getcwd(mp_obj_t self_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    if (vstr_len(&self->cur_dir) == 1) {
        return MP_OBJ_NEW_QSTR(MP_QSTR__slash_);
    }
    return mp_obj_new_str(self->cur_dir.buf, self->cur_dir.len - 1);
}
static MP_DEFINE_CONST_FUN_OBJ_1(kvfs_getcwd_obj, kvfs_getcwd);

static mp_obj_t kvfs_stat(mp_obj_t self_in, mp_obj_t path_in) {
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    const char *path = make_path(self, path_in);
    struct lfs2_info info;
    int ret = lfs2_stat(self->lfs, path, &info);
    if (ret < 0) {
        raise_lfs(ret);
    }
    mp_timestamp_t mtime = 0;
    uint8_t b[8];
    if (lfs2_getattr(self->lfs, path, KV_ATTR_MTIME, b, sizeof(b)) == (lfs2_ssize_t)sizeof(b)) {
        uint64_t ns = 0;
        for (size_t i = sizeof(b); i > 0; --i) {
            ns = ns << 8 | b[i - 1];
        }
        mtime = timeutils_seconds_since_epoch_from_nanoseconds_since_1970(ns);
    }
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(10, NULL));
    t->items[0] = MP_OBJ_NEW_SMALL_INT(info.type == LFS2_TYPE_REG ? MP_S_IFREG : MP_S_IFDIR);
    for (int i = 1; i < 6; i++) {
        t->items[i] = MP_OBJ_NEW_SMALL_INT(0);
    }
    t->items[6] = mp_obj_new_int_from_uint(info.size);
    t->items[7] = timeutils_obj_from_timestamp(mtime);
    t->items[8] = timeutils_obj_from_timestamp(mtime);
    t->items[9] = timeutils_obj_from_timestamp(mtime);
    return MP_OBJ_FROM_PTR(t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(kvfs_stat_obj, kvfs_stat);

static int count_block(void *data, lfs2_block_t bl) {
    (void)bl;
    *(uint32_t *)data += 1;
    return 0;
}

static mp_obj_t kvfs_statvfs(mp_obj_t self_in, mp_obj_t path_in) {
    (void)path_in;
    kvfs_t *self = MP_OBJ_TO_PTR(self_in);
    uint32_t used = 0;
    int ret = lfs2_fs_traverse(self->lfs, count_block, &used);
    if (ret < 0) {
        raise_lfs(ret);
    }
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(10, NULL));
    t->items[0] = MP_OBJ_NEW_SMALL_INT(self->lfs->cfg->block_size);
    t->items[1] = t->items[0];
    t->items[2] = MP_OBJ_NEW_SMALL_INT(self->lfs->cfg->block_count);
    t->items[3] = MP_OBJ_NEW_SMALL_INT(self->lfs->cfg->block_count - used);
    t->items[4] = t->items[3];
    for (int i = 5; i < 9; i++) {
        t->items[i] = MP_OBJ_NEW_SMALL_INT(0);
    }
    t->items[9] = MP_OBJ_NEW_SMALL_INT(LFS2_NAME_MAX);
    return MP_OBJ_FROM_PTR(t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(kvfs_statvfs_obj, kvfs_statvfs);

static mp_obj_t kvfs_mount(mp_obj_t self_in, mp_obj_t readonly, mp_obj_t mkfs) {
    (void)self_in, (void)readonly, (void)mkfs;
    return mp_const_none;               // the kernel mounted it; it stays mounted
}
static MP_DEFINE_CONST_FUN_OBJ_3(kvfs_mount_obj, kvfs_mount);

static mp_obj_t kvfs_umount(mp_obj_t self_in) {
    (void)self_in;
    return mp_const_none;               // the VM lets go; the kernel keeps it
}
static MP_DEFINE_CONST_FUN_OBJ_1(kvfs_umount_obj, kvfs_umount);

static const mp_rom_map_elem_t kvfs_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_open), MP_ROM_PTR(&kvfs_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_ilistdir), MP_ROM_PTR(&kvfs_ilistdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_mkdir), MP_ROM_PTR(&kvfs_mkdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_rmdir), MP_ROM_PTR(&kvfs_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_chdir), MP_ROM_PTR(&kvfs_chdir_obj) },
    { MP_ROM_QSTR(MP_QSTR_getcwd), MP_ROM_PTR(&kvfs_getcwd_obj) },
    { MP_ROM_QSTR(MP_QSTR_remove), MP_ROM_PTR(&kvfs_remove_obj) },
    { MP_ROM_QSTR(MP_QSTR_rename), MP_ROM_PTR(&kvfs_rename_obj) },
    { MP_ROM_QSTR(MP_QSTR_stat), MP_ROM_PTR(&kvfs_stat_obj) },
    { MP_ROM_QSTR(MP_QSTR_statvfs), MP_ROM_PTR(&kvfs_statvfs_obj) },
    { MP_ROM_QSTR(MP_QSTR_mount), MP_ROM_PTR(&kvfs_mount_obj) },
    { MP_ROM_QSTR(MP_QSTR_umount), MP_ROM_PTR(&kvfs_umount_obj) },
};
static MP_DEFINE_CONST_DICT(kvfs_locals, kvfs_locals_table);

static mp_import_stat_t kvfs_import_stat(void *self_in, const char *path) {
    kvfs_t *self = self_in;
    struct lfs2_info info;
    mp_obj_str_t path_obj = { { &mp_type_str }, 0, 0, (const byte *)path };
    path = make_path(self, MP_OBJ_FROM_PTR(&path_obj));
    if (lfs2_stat(self->lfs, path, &info) == 0) {
        return info.type == LFS2_TYPE_REG ? MP_IMPORT_STAT_FILE : MP_IMPORT_STAT_DIR;
    }
    return MP_IMPORT_STAT_NO_EXIST;
}

static const mp_vfs_proto_t kvfs_proto = {
    .import_stat = kvfs_import_stat,
};

MP_DEFINE_CONST_OBJ_TYPE(
    moy_kvfs_type, MP_QSTR_KVfs, MP_TYPE_FLAG_NONE,
    make_new, kvfs_make_new,
    protocol, &kvfs_proto,
    locals_dict, &kvfs_locals);

// -- the kernel's side -------------------------------------------------------------

// The VM's start: unlink what the last VM left open in the instance (its sweep
// closed its files and listings; a gc object is never trusted across a VM),
// then mount a KVfs at "/". True when it did, and the port's _boot.py is then
// not run.
bool moy_kvol_vm_mount(void) {
    lfs2_t *lfs = moy_kvol_lfs();
    if (lfs == NULL) {
        return false;
    }
    lfs->mlist = NULL;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t args[2] = {kvfs_new(), MP_OBJ_NEW_QSTR(MP_QSTR__slash_)};
        mp_call_function_n_kw(MP_OBJ_FROM_PTR(&mp_vfs_mount_obj), 2, 0, args);
        nlr_pop();
        return true;
    }
    return false;
}

// The store's resolve (moy_vol_at): the kernel's lfs2_t when `obj` is a KVfs.
void *moy_kvfs_lfs(mp_obj_t obj) {
    return mp_obj_get_type(obj) == &kvfs_type ? ((kvfs_t *)MP_OBJ_TO_PTR(obj))->lfs : NULL;
}

// (mounted, the mount's error, blocks, block size)
void moy_kvol_state(int *mounted, int *err, uint32_t *blocks, uint32_t *bsize) {
    *mounted = s_kv != NULL && s_kv->err == 0;
    *err = s_kv != NULL ? s_kv->err : MP_ENODEV;
    *blocks = s_kv != NULL ? s_kv->cfg.block_count : 0;
    *bsize = s_kv != NULL ? s_kv->cfg.block_size : 0;
}

#endif
