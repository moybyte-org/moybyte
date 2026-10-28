// moy_files: a compiled cart's written files (moy-spec SPEC.md 16.12), the
// store half, in C (docs/kernel_cartpath_2026-10.md §1, `cart_files.py`'s
// row). runtime/cart_files.py is the reference, rule for rule, and
// tests/test_moy_files.py holds the two together.
//
// WHERE: beside the carts store, `written/<cart>/`, <cart> the cart folder's
// name less ".moy", so an install, an update or a push replaces the cart's
// folder and never touches them.
//
// ONE FLAT FOLDER PER CART: a path is one file named by its KEY: a-z, 0-9,
// '_', '-' and an inner '.' as they are, every other byte '%' and two
// lowercase hex digits, and a name Windows keeps for a device escaped at its
// first byte. A key never holds '~', and decodes back to its path for `list`.
//
// A WRITE IS WHOLE OR NOT AT ALL: "<key>~part", then "<key>~done" once the file
// is closed, then over "<key>". Opening a cart's files finishes what a power
// loss left: a "~done" replaces its file, a "~part" goes.
//
// ONE HELD FILE: a read opens the file it reads and keeps it open (moy_vol's
// MOY_VOL_HELD), so a cart streaming its data file in chunks opens it once;
// a write, an erase and the close let it go.
//
// ONE WRITER: the cart's session is the only thing that writes the folder, so
// what is written is read once at the open and kept; `where` and `name` touch
// the volume only for the cart's own folder's listing, once.
//
// Every path crosses as bytes (`n` long, no NUL needed). Pure C over moy_vol:
// nothing here knows which thread it runs on or what gate a board's volume
// needs; the caller holds both.

#ifndef MOY_FILES_H
#define MOY_FILES_H

#include <stddef.h>
#include <stdint.h>

#define MOY_FILES_NO_ROOM (-2)      // SPEC.md 16.12's write answers
#define MOY_FILES_FAILED (-3)
#define MOY_FILES_DEPTH 16          // how deep the listing walks the cart's folder
#define MOY_FILES_PATH 255          // the longest path a cart names, in bytes

// The key `path` is kept under, NUL-terminated in `out`: its length, or 0 when
// `cap` cannot hold it (a key is at most 3n + 1 bytes).
size_t moy_files_key(const uint8_t *path, size_t n, char *out, size_t cap);
// A key back to its path: the path's length, or -1 for a name that is no key
// (a "~" in it, a bad escape) or a path longer than `cap`.
int moy_files_path_of(const char *name, size_t n, uint8_t *out, size_t cap);
// The folder the cart at `cart_path` keeps its written files in: 0, or -1
// when `cap` is too small.
int moy_files_folder(const char *cart_path, char *out, size_t cap);

typedef struct moy_files moy_files_t;

// The written files of the cart at `cart_path`, for one session: what a power
// loss left put right, and what is written read. NULL with no memory.
moy_files_t *moy_files_open(const char *cart_path);
void moy_files_close(moy_files_t *f);

// The file holding the written copy of `path`, NUL-terminated in `out`: 1, or
// 0 with no written copy (or no room in `out`).
int moy_files_where(moy_files_t *f, const uint8_t *path, size_t n, char *out, size_t cap);
// Replace the written copy of `path` with `data`, whole: 0, MOY_FILES_NO_ROOM
// or MOY_FILES_FAILED.
int32_t moy_files_write(moy_files_t *f, const uint8_t *path, size_t n,
                        const uint8_t *data, uint32_t len);
// read (SPEC.md 16.12) on `name` in the cart's own folder: its bytes from
// `offset`, at most `len` of them into `dst`, or with `len` 0 how many remain;
// 0 when it is absent or unreadable.
int32_t moy_files_read(moy_files_t *f, const char *name, uint32_t offset, uint8_t *dst,
                       uint32_t len);
// The same on the written copy of `path`: -1 when there is none.
int32_t moy_files_read_written(moy_files_t *f, const uint8_t *path, size_t n, uint32_t offset,
                               uint8_t *dst, uint32_t len);
// Remove the written copy: 0, or -1 when there is none.
int32_t moy_files_erase(moy_files_t *f, const uint8_t *path, size_t n);
// The `index`-th path, in bytewise order, of the cart's files that begin with
// `prefix` -- its folder's and the written ones, each path once -- copied into
// `dst` (at most `cap` bytes): its full length, or -1 past the last.
int32_t moy_files_name(moy_files_t *f, const uint8_t *prefix, size_t pn, uint32_t index,
                       uint8_t *dst, uint32_t cap);

#endif // MOY_FILES_H
