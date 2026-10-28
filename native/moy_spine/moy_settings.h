// The settings store as a C ABI: system.json as rows of JSON text.
//
// runtime/moy_spine.py's Settings is the interface and the reference; this is
// its native form (docs/kernel_spine_2026-10.md section 5). One row per
// top-level key, each holding its value's JSON text, kept as it was written and
// never re-serialised: a value read from the file goes back byte for byte.
// Keys are held decoded (UTF-8, possibly with NUL), written as JSON strings
// with only the escapes JSON requires.
//
// Loading is a scanner, not a tree: it walks the top-level object, splits key
// and value spans and refuses what is not JSON (RFC 8259, plus the NaN /
// Infinity / -Infinity tokens CPython's json reads and writes), a file nested
// deeper than MOY_SETTINGS_DEPTH containers (the file's object is the first,
// so a row's value holds at most MOY_SETTINGS_DEPTH - 1), and a
// key with a lone surrogate escape, which holds no UTF-8. A refused file
// changes nothing. The scanner reads untrusted bytes; fuzz_spine.c runs it
// under the sanitizers, and tests/test_moy_spine_twins.py holds it to CPython's
// json.
//
// A store counts the changes made since it was last clean: set and delete add
// one, a load or moy_settings_clean zeroes it. What persists a dirty store is
// its saver, which the binding registers (its save hook, behind a thunk), and
// moy_settings_flush calls it: a write made through C (native/moy_app's prefs)
// persists by the same path a write made through the binding does.
//
// Every byte comes from the moy_htab_mem_t the store is made with.

#ifndef MOY_SETTINGS_H
#define MOY_SETTINGS_H

#include <stddef.h>
#include <stdint.h>

#include "moy_htab.h"

#define MOY_SETTINGS_DEPTH 32u

enum {
    MOY_SETTINGS_OK = 0,
    MOY_SETTINGS_BADJSON = 1,   // not a JSON object / value, or too deep
    MOY_SETTINGS_NOMEM = 3,     // the allocator refused; nothing changed
    MOY_SETTINGS_BADKEY = 5,    // an empty key
};

typedef struct moy_settings moy_settings_t;

moy_settings_t *moy_settings_new(const moy_htab_mem_t *mem);    // or NULL
void moy_settings_free(moy_settings_t *s);                      // NULL is a no-op

// OK when `text` is exactly one JSON value (whitespace around it allowed) that
// a row may hold, nested at most MOY_SETTINGS_DEPTH - 1 deep, else BADJSON.
int moy_settings_validate(const char *text, size_t len);

// Replace every row with the object `text` holds: OK and the row count in
// `rows`, BADJSON or NOMEM. A repeated key keeps its first position and its
// last value. The store is clean after it.
int moy_settings_load(moy_settings_t *s, const char *text, size_t len,
                      uint32_t *rows);

// 1 and the row's text when the key has one, else 0.
int moy_settings_get(const moy_settings_t *s, const char *key, size_t key_len,
                     const char **json, size_t *json_len);
// OK, BADKEY, BADJSON (`json` is not one value) or NOMEM. A new key goes last.
// The store is dirty after it.
int moy_settings_set(moy_settings_t *s, const char *key, size_t key_len,
                     const char *json, size_t json_len);
// 1 when the key had a row, else 0; the store is dirty after a 1.
int moy_settings_delete(moy_settings_t *s, const char *key, size_t key_len);

// What writes the file: called with the dump; nonzero when it landed.
typedef int (*moy_settings_save_fn)(void *ctx, const char *text, size_t len);
// Register `fn` (NULL: none) and its `ctx`; moy_settings_saver_ctx reads it back.
void moy_settings_saver(moy_settings_t *s, moy_settings_save_fn fn, void *ctx);
void *moy_settings_saver_ctx(const moy_settings_t *s);
// 1 when the store is clean afterwards: it was, or the saver landed the dump;
// 0 when it stays dirty (no saver, the saver failed, the allocator refused).
int moy_settings_flush(moy_settings_t *s);

// The changes since the store was last clean.
uint32_t moy_settings_dirty(const moy_settings_t *s);
void moy_settings_clean(moy_settings_t *s);

uint32_t moy_settings_count(const moy_settings_t *s);
// Row `i` in order: 1 and its key and text, or 0 past the end.
int moy_settings_at(const moy_settings_t *s, uint32_t i, const char **key,
                    size_t *key_len, const char **json, size_t *json_len);

// The file: "{" rows joined with ", " as `"key": text` "}". Writes up to `cap`
// bytes (no NUL) and returns the length the whole needs, as snprintf does; `out`
// may be NULL when `cap` is 0.
size_t moy_settings_dump(const moy_settings_t *s, char *out, size_t cap);

#endif // MOY_SETTINGS_H
