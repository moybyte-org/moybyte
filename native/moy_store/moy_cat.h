// The catalogue (docs/kernel_store_2026-10.md section 6): the shelf's scan of
// a store root, one cart's entry read again, and the cart verbs that work on a
// whole folder.
//
// runtime/moy_carts.py's `_load(path, whole=False)` and `_each` are the
// reference: the scan keeps their access pattern -- one listing of the root,
// then per `.moy` folder one enter (moy_vol_enter), one listing, and an open
// of each file the listing shows -- and reads each manifest through moy_fs's
// recovering reader, normalised as `_load` normalises it. An entry is handed
// to the caller's function whole and freed after it; the binding builds the
// dict the shelf takes. Fields the store does not interpret cross as the JSON
// text of their value. A folder that will not read is no entry, never an
// error: one bad folder never takes the shelf down.

#ifndef MOY_CAT_H
#define MOY_CAT_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

#define MOY_CAT_EXT ".moy"
#define MOY_CAT_FORMAT "moybyte-cart-v1"

// A JSON value's span in the entry's manifest text; `v` NULL when the
// manifest does not have it.
typedef struct {
    const char *v, *e;
} moy_cat_json_t;

// An int as Python's int() makes it of a JSON value (moy_json_int): kind 0 is
// int() raising (the default stands), 1 the value, 2 an int beyond 64 bits
// whose text is `json`.
typedef struct {
    int kind;
    int64_t value;
    moy_cat_json_t json;
} moy_cat_int_t;

// What a folder's listing says it holds: the row's bits.
enum {
    MOY_CAT_HAS_MAIN = 1u << 0,
    MOY_CAT_HAS_SPRITES = 1u << 1,
    MOY_CAT_HAS_MAP = 1u << 2,
    MOY_CAT_HAS_FLAGS = 1u << 3,
    MOY_CAT_HAS_CONFIG = 1u << 4,
    MOY_CAT_HAS_SCENES = 1u << 5,
    MOY_CAT_HAS_COVER = 1u << 6,
    MOY_CAT_HAS_JOURNAL = 1u << 7,
    MOY_CAT_GRADUATED = 1u << 8,
    MOY_CAT_BROKEN = 1u << 9,
    MOY_CAT_COMPILED = 1u << 10,
};

// The input kinds a manifest's "input" may name, in INPUT_KINDS's order.
#define MOY_CAT_INPUT_KINDS 3

// One cart's entry. Every pointer is good until the function it was handed to
// returns.
typedef struct {
    const char *folder;         // its name in the root, NUL-terminated
    size_t folder_n;
    const char *id;             // #162: the manifest's "id", else the folder's stem
    size_t id_n;
    const char *broken;         // NULL, or why the manifest would not read
    const char *man;            // the manifest's text (a synthesised one when broken)
    size_t man_n;
    int spec;                   // "format": "moy-1"
    int compiled;               // "runtime": "wasm"
    const char *main;           // the main script's name, decoded
    size_t main_n;
    // The manifest's fields as they stand; the defaults are the binding's.
    moy_cat_json_t title, author, type, runtime, format, fps, palette,
        extensions, edit, permissions, writable;
    int writable_ok;            // a compiled cart's "writable" is a list of strings
    moy_cat_int_t version, memory;
    int graduated;
    int icon_ok;                // the normalised icon: tile, w, h
    int64_t icon[3];
    size_t input_n;             // -1 cast: None; else the kinds, in order
    const uint8_t *input;
    int canvas;                 // 0 None, 1 (w, h) in the set, 2 the raw value
    uint16_t canvas_w, canvas_h;
    moy_cat_json_t canvas_raw;
    uint32_t has;               // MOY_CAT_HAS_* from the listing
    uint32_t cover_size;
    size_t scenes_n;            // scene_names, in order
    const char *const *scenes;
    const size_t *scene_n;
    int rows;                   // icon_rows: 0 None, else (pw, ph, want)
    uint16_t pw, ph;
    const char *const *want;    // ph rows; NULL is None
    const size_t *want_n;
} moy_cat_entry_t;

#define MOY_CAT_NO_INPUT ((size_t)-1)

// Called once per entry; a nonzero return stops the walk and is returned.
typedef int (*moy_cat_fn)(void *ctx, const moy_cat_entry_t *e);

// What a folder that is no entry says, for the binding to print: `what` is
// "manifest bad", "main missing" or "unreadable", `detail` NULL or why.
typedef void (*moy_cat_note_fn)(void *ctx, const char *what, const char *path,
                                const char *detail);

// Every cart folder under `root` (an absolute path), in folder-name order:
// 0, MOY_FS_NONE when the root will not list, or the callback's return.
int moy_cat_scan(const char *root, moy_cat_fn fn, moy_cat_note_fn note,
                 void *ctx);
// The entry of the folder at `path`: 0 after `fn`, MOY_FS_NONE when it is no
// cart, or the callback's return.
int moy_cat_entry(const char *path, moy_cat_fn fn, moy_cat_note_fn note,
                  void *ctx);

// A folder and everything in it removed; errors are ignored, as _rmtree does.
void moy_cat_rmtree(const char *path);
// A cart folder's files copied into a fresh copy, one level of subfolders
// deep, leaving out what the copy writes itself or must not inherit (its
// manifest, config, `main`, saves, journal, thumbs, crash-safety leftovers).
// An unreadable entry is skipped; only ENOSPC is returned.
int moy_cat_copy(const char *src, const char *dst, const char *main);

// Python's `_plain` and the listing's case rule, for the binding's tests.
int moy_cat_plain(const char *name, size_t n);

#endif // MOY_CAT_H
