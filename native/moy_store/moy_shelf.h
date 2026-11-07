// The shelf's record and its removal: what the store installed, and a cart
// taken off it with everything it wrote. One implementation for every face:
// runtime/carts/cart_index.py's load_record, remove and find_installed call
// it (moy_store.shelf_* on a VM, runtime/host/moy_shelf_binding.py over
// ctypes on CPython), and the C shell's `pkg list` and `pkg remove` serve it
// with no VM (native/moy_sh/moy_sh_store.c).
//
// THE RECORD is `installed.json` beside the carts folder `root` (the parent of
// `root`, moy_store_base._sibling_path's rule): {"version": 1, "carts":
// {folder: entry}}, each entry {"id", "index", "name", "version", "module",
// "files"}. It is read as every system document is (moy_docs.
// _load_store_json): the file, else its crash-safe write's stamped backup,
// which is republished in its place; a record whose "carts" is not an object
// is no record.
//
// A REMOVAL is cart_index.remove_steps' order. The record without the cart is
// made first (moy_shelf_without), so a keeper (the browser's store of record)
// is handed it before anything is deleted; then `<root>/<folder>` is renamed
// to `install/<folder>.gone` beside the carts folder (moy_shelf_take: a cut
// mid-delete never leaves half a cart listed, and cart_index.recover deletes
// what a cut left there), the record is written crash-safe
// (moy_shelf_publish), and the folder and the files the cart wrote
// (`written/<id>`, moy_shelf_written) are deleted. moy_shelf_remove is the
// four in one call, the C shell's; the VM steps them a store op a frame,
// the trees an entry at a time. The consent the cart was installed with is
// the caller's to forget (runtime/carts/pkg.py's removed, the shell's pkg
// remove).

#ifndef MOY_SHELF_H
#define MOY_SHELF_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

#define MOY_SHELF_RECORD "installed.json"
#define MOY_SHELF_STAGE "install"
#define MOY_SHELF_PATH 320u            // a store path's bytes, the root's included

// The record's text: 0 and `out` (moy_buf_free it), MOY_FS_NONE when there is
// no usable record, or an errno value.
int moy_shelf_record(const char *root, moy_buf_t *out);

// One record entry, decoded: NUL-terminated strings, empty where the entry
// has none (an entry that is no object has none), `version` int() of its
// "version" or 0.
typedef struct {
    const char *folder, *id, *name;
    int64_t version;
} moy_shelf_entry_t;

// Called once per entry, in the record's order; a nonzero return stops the
// walk and is returned.
typedef int (*moy_shelf_fn)(void *ctx, const moy_shelf_entry_t *e);

// Every entry of the record: 0 (none when there is no record), the
// callback's return, or MOY_ENOMEM.
int moy_shelf_each(const char *root, moy_shelf_fn fn, void *ctx);

// The folder of the entry `ident` names -- its folder or its id, the last
// entry that matches -- into `folder`: 0, MOY_FS_NONE when none does, or an
// errno value.
int moy_shelf_find(const char *root, const char *ident, char *folder, size_t cap);

// What the record says once `folder` is gone, as cart_index.record_text
// writes it (CPython's json.dumps): 0 and `out`, MOY_FS_NONE when the record
// does not hold `folder`, or an errno value.
int moy_shelf_without(const char *root, const char *folder, moy_buf_t *out);

// `<root>/<folder>` renamed to `install/<folder>.gone`, its path in `gone`
// (one left by a cut deleted first): 0, or an errno value.
int moy_shelf_take(const char *root, const char *folder, char *gone, size_t cap);

// The record `text` written crash-safe beside the carts folder: 0, or an errno.
int moy_shelf_publish(const char *root, const char *text, size_t n);

// The folder of the files the cart in `folder` wrote (moy_files_folder): 0,
// or MOY_EINVAL when `out` cannot hold it.
int moy_shelf_written(const char *root, const char *folder, char *out, size_t cap);

// `folder` taken off the shelf (above); `text` the record without it, or NULL
// when the record did not hold it. 0, or the errno value of the rename out of
// the carts folder or of the record's write.
int moy_shelf_remove(const char *root, const char *folder, const char *text, size_t n);

#endif // MOY_SHELF_H
