// The volume seam (docs/kernel_store_2026-10.md section 3): one interface over
// the file-system libraries an image already links -- oofatfs on a card,
// littlefs2 on internal flash, POSIX on the host and in the browser.
//
// A volume is a library and the one instance of it that owns a path. Until a
// volume is the kernel's, the store BORROWS the VM's instance for the length of
// a call: the host's moy_vol_at resolves a path through MicroPython's mount
// table to a VfsFat's FATFS or a VfsLfs2's lfs2_t, and the pointer never
// outlives the call.
//
// Every call returns 0 or an errno value (MicroPython's numbering, which is
// Linux's; a POSIX volume hands back its C library's own, as VfsPosix does).
// Paths are NUL-terminated and relative to the volume: what moy_vol_at hands
// back as `rest`. A file is allocated from moy_store_alloc and freed by
// moy_vol_close; nothing is open when a store call returns, and
// moy_vol_unwind closes whatever a raised call left open.

#ifndef MOY_VOL_H
#define MOY_VOL_H

#include <stddef.h>
#include <stdint.h>

#ifdef MOY_STORE_MICROPYTHON
#include "py/mpconfig.h"
#ifndef MOY_VOL_FAT
#define MOY_VOL_FAT MICROPY_VFS_FAT
#endif
#ifndef MOY_VOL_LFS2
#define MOY_VOL_LFS2 MICROPY_VFS_LFS2
#endif
#ifndef MOY_VOL_POSIX
#define MOY_VOL_POSIX MICROPY_VFS_POSIX
#endif
#endif
#ifndef MOY_VOL_FAT
#define MOY_VOL_FAT 0
#endif
#ifndef MOY_VOL_LFS2
#define MOY_VOL_LFS2 0
#endif
#ifndef MOY_VOL_POSIX
#define MOY_VOL_POSIX 1
#endif

// The errno values the store returns itself.
#define MOY_EIO 5
#define MOY_EBADF 9
#define MOY_ENOMEM 12
#define MOY_EACCES 13
#define MOY_EBUSY 16
#define MOY_EEXIST 17
#define MOY_EXDEV 18
#define MOY_ENODEV 19
#define MOY_EISDIR 21
#define MOY_EINVAL 22
#define MOY_EMFILE 24
#define MOY_EFBIG 27
#define MOY_ENOSPC 28
#define MOY_EROFS 30

enum {
    MOY_VOL_KIND_POSIX = 1,
    MOY_VOL_KIND_FAT = 2,       // fs is a FATFS *
    MOY_VOL_KIND_LFS2 = 3,      // fs is an lfs2_t *
};

typedef struct {
    uint8_t kind;
    void *fs;
} moy_vol_t;

enum {
    MOY_VOL_READ = 1,
    MOY_VOL_WRITE = 2,          // created, or truncated
};

typedef struct moy_vol_file moy_vol_file_t;

typedef struct {
    uint8_t is_dir;
    uint32_t size;
} moy_vol_stat_t;

// One entry of a listing: its name (`len` bytes, NUL-terminated), whether it
// is a folder, and its size. A nonzero return stops the walk and is returned.
typedef int (*moy_vol_ent_fn)(void *ctx, const char *name, size_t len,
                              int is_dir, uint32_t size);

int moy_vol_open(const moy_vol_t *v, const char *path, int mode,
                 moy_vol_file_t **out);
int moy_vol_read(moy_vol_file_t *f, void *buf, size_t n, size_t *got);
int moy_vol_write(moy_vol_file_t *f, const void *buf, size_t n);
int moy_vol_size(moy_vol_file_t *f, uint32_t *n);
int moy_vol_close(moy_vol_file_t *f);           // frees f; the first error wins
void moy_vol_unwind(void);                      // close every file still open

int moy_vol_stat(const moy_vol_t *v, const char *path, moy_vol_stat_t *st);
int moy_vol_list(const moy_vol_t *v, const char *dir, moy_vol_ent_fn fn,
                 void *ctx);
int moy_vol_mkdir(const moy_vol_t *v, const char *path);
int moy_vol_remove(const moy_vol_t *v, const char *path);
// `dst` must not exist, on every backend, as FAT has it: EEXIST.
int moy_vol_rename(const moy_vol_t *v, const char *src, const char *dst);
// `src` over `dst`: the medium's own atomic rename where it has one (littlefs,
// POSIX), and on FAT the rename after `dst` is removed.
int moy_vol_replace(const moy_vol_t *v, const char *src, const char *dst);

// Imported from the host. moy_vol_at resolves `path` to the volume that owns
// it and the path within it; moy_store_alloc is a call's scratch (zeroed),
// moy_store_keep what outlives the call (the marker cache); both are freed by
// moy_store_free with the size they were made with.
int moy_vol_at(const char *path, moy_vol_t *v, const char **rest);
void *moy_store_alloc(size_t n);
void *moy_store_keep(size_t n);
void moy_store_free(void *p, size_t n);

#endif // MOY_VOL_H
