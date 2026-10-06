// The store's crash-safe write and its file verbs, over moy_vol
// (docs/kernel_store_2026-10.md section 5).
//
// runtime/moy_fs.py is the reference and its header the crash-safety story;
// this is that story on the medium, byte for byte: the marker line
// "#moyfs1 <chars> <crc32> <path>\n" in `<root>/.publish`, the backup
// `<path>.bak` led by its stamp line "#moyfs1 <chars> <crc32>\n", the file in
// place; a read that recovers only the prefix case; a spent backup claimed by
// a rename. A stamp counts code points as Python's len(str) does and takes the
// crc32 of the UTF-8 bytes, so a file one tier stamps reads whole on another.
//
// Paths are absolute (the host resolves each with moy_vol_at). Every call
// returns 0 or an errno value; MOY_FS_NONE is "nothing trustworthy", the
// Python twin's None. A buffer is n bytes and a NUL, freed by moy_buf_free.

#ifndef MOY_FS_H
#define MOY_FS_H

#include <stddef.h>
#include <stdint.h>

#include "moy_vol.h"

#define MOY_FS_NONE (-1)
#define MOY_FS_ROOTS 8

typedef struct {
    char *p;
    size_t n;
} moy_buf_t;

void moy_buf_free(moy_buf_t *b);

uint32_t moy_fs_crc32(uint32_t crc, const void *p, size_t n);
void moy_fs_stamp(const char *data, size_t n, uint32_t *chars, uint32_t *crc);

// The publish marker's roots: set_publish_root, and its cache dropped.
int moy_fs_root(const char *root);
void moy_fs_roots_clear(void);
void moy_fs_unmark(const char *path);

// The plain verbs. moy_fs_read_file refuses a file over `cap` with EFBIG.
int moy_fs_read_file(const char *path, size_t cap, moy_buf_t *out);
int moy_fs_read_vol(const moy_vol_t *v, const char *rest, size_t cap,
                    moy_buf_t *out);
int moy_fs_write(const char *path, const void *data, size_t n);
int moy_fs_write_bytes(const char *path, const void *data, size_t n);
int moy_fs_exists(const char *path);
int moy_fs_mkdir(const char *path);
int moy_fs_remove(const char *path);

// The crash-safe write and its readers.
int moy_fs_publish(const char *path, const char *data, size_t n);
int moy_fs_read(const char *path, const char *at, moy_buf_t *out);
// moy_fs_read with the file opened as `name` in the working folder `here`
// (moy_vol_enter), or by `name` (a path) when `here` is NULL.
int moy_fs_read_in(const char *path, moy_vol_here_t *here, const char *name,
                   moy_buf_t *out);
int moy_fs_read_stamped(const char *path, moy_buf_t *out);
int moy_fs_bak_stamp(const char *path, uint32_t *chars, uint32_t *crc);
int moy_fs_claim(const char *path, const char *dest, uint32_t chars,
                 uint32_t crc);                  // 1 taken, 0 not
void moy_fs_forget_bak(const char *path);

#endif // MOY_FS_H
