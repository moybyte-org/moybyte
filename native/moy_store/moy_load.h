// Loading a cart (docs/kernel_store_2026-10.md section 7): its entry and
// every payload's bytes, read from inside its folder into one PSRAM arena.
//
// runtime/moy_carts.py's `_load(path, whole=True)` is the reference: the
// main script through moy_fs's recovering reader, the other scripts in the
// manifest's `sources` order (a compiled cart's `src/` texts instead), the
// config (the manifest's under config.json's), the tile flags, the sheet,
// sounds, map, blocks, images and scenes, each optional one absent as NULL.
// A text that is not UTF-8 makes the cart unreadable, as a str would. The
// arena lives until the function handed the cart returns; in sprint 4 the
// cart path keeps it.

#ifndef MOY_LOAD_H
#define MOY_LOAD_H

#include "moy_cat.h"

typedef struct {
    const char *name;           // NUL-terminated
    size_t name_n;
    const char *text;           // NUL-terminated
    size_t n;
} moy_load_file_t;

typedef struct {
    const char *p, *e;          // a JSON value's span; p NULL for none
} moy_load_json_t;

typedef struct {
    moy_cat_entry_t e;          // scenes/scene_n: the loaded scenes' order
    const char *src;            // the main script ("" for a compiled cart)
    size_t src_n;
    const moy_load_file_t *before, *after;
    size_t before_n, after_n;
    moy_load_json_t cfg_manifest;   // the manifest's "config" (dict() of it)
    moy_load_json_t cfg_file;       // config.json, when it reads as JSON
    const char *flags, *sprites, *map;  // NULL: no such file
    size_t flags_n, sprites_n, map_n;
    moy_load_json_t sounds, blocks;     // when the file reads as JSON
    const moy_load_file_t *images, *scenes;
    size_t images_n, scenes_n;
} moy_cart_t;

typedef int (*moy_load_fn)(void *ctx, const moy_cart_t *c);

// The cart at `path` whole: 0 after `fn`, MOY_FS_NONE when it is no cart (or
// will not load), or the callback's return.
int moy_cat_load(const char *path, moy_load_fn fn, moy_cat_note_fn note,
                 void *ctx);

#endif // MOY_LOAD_H
