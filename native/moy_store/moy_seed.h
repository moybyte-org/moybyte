// The seed (docs/kernel_store_2026-10.md section 7): a built-in from the
// packed roster written into a store root.
//
// runtime/moy_seed.py's `seed_builtins` is the reference, file for file and
// byte for byte on the host: the manifest regenerated from the cart's fields
// in its key order, every payload the cart carries, and a cart's saves and
// config (pmem.json, config.json) kept across a re-seed. A roster blob is one
// cart's JSON as raw deflate (tools/gen_device_carts.py --packed), inflated
// with the uzlib the image links for `deflate` into one PSRAM buffer, which is
// freed before the call returns. What to write is the caller's: the scan's
// rows carry each present built-in's version (moy_catalogue.seed).

#ifndef MOY_SEED_H
#define MOY_SEED_H

#include <stddef.h>
#include <stdint.h>

#include "moy_fs.h"

#define MOY_SEED_WBITS 15
#define MOY_SEED_MAX (4u << 20)     // what one cart may inflate to

// The folder a cart titled `title` is seeded into: `<ns>.<slug>.moy`
// (runtime/moy_store_base.py's cart_folder). Writes up to `cap` bytes and a
// NUL, returns the length the whole needs.
size_t moy_seed_folder(const char *title, size_t n, const char *ns, char *out,
                       size_t cap);

// The roster blob `blob` inflated: 0 and its JSON text, or an errno value
// (EINVAL for a stream that is not one cart).
int moy_seed_inflate(const uint8_t *blob, size_t n, moy_buf_t *out);

// One cart written at `root`/`folder` from the JSON text of its dict: 1
// written, 0 left (the folder holds a cart at the cart's version or newer),
// or a negative errno value.
int moy_seed_write(const char *root, const char *folder, const char *json,
                   size_t n);

#endif // MOY_SEED_H
