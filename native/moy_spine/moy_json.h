// The kernel's JSON scanner: one body for the settings store (moy_settings.c)
// and the cart store (native/moy_store: manifests, journal lines, the seed
// roster). A scanner, not a tree: values are spans of the text they were read
// from, walked in place.
//
// What it accepts is CPython's json.loads over text: RFC 8259, plus the NaN,
// Infinity and -Infinity tokens, nesting at most MOY_JSON_DEPTH containers
// deep (the outermost counts as the first). It reads untrusted bytes:
// native/moy_spine/fuzz_spine.c and native/moy_store/fuzz_fs.c run it under
// the sanitizers, and tests/test_moy_spine_twins.py and tests/test_store_native.py
// hold it to CPython's json.
//
// A value span starts at the value's first byte and ends one past its last;
// the functions that take one assume it was scanned (moy_json_value) first.

#ifndef MOY_JSON_H
#define MOY_JSON_H

#include <stddef.h>
#include <stdint.h>

#define MOY_JSON_DEPTH 32u

enum {
    MOY_JSON_OK = 0,
    MOY_JSON_BAD = 1,           // not JSON, or nested too deep
};

// The kinds moy_json_kind tells apart.
enum {
    MOY_JSON_STR = 's',
    MOY_JSON_INT = 'i',         // a number with no fraction and no exponent
    MOY_JSON_FLOAT = 'f',       // any other number, NaN and the infinities
    MOY_JSON_OBJ = 'o',
    MOY_JSON_ARR = 'a',
    MOY_JSON_TRUE = 'T',
    MOY_JSON_FALSE = 'F',
    MOY_JSON_NULL = 'z',
};

const char *moy_json_ws(const char *p, const char *end);

// One value at `p`, `depth` containers deep: the pointer past it, or NULL.
const char *moy_json_value(const char *p, const char *end, uint32_t depth);

// The string whose opening quote is at `p`: the pointer past its closing quote,
// or NULL. With `decode`, a lone surrogate escape is refused and the decoded
// UTF-8 goes to `out` (when not NULL; moy_json_strlen bounds it) and its length
// to `*n`; without, the string is only checked.
const char *moy_json_string(const char *p, const char *end, int decode,
                            char *out, size_t *n);

// OK when `text` is exactly one JSON value, whitespace around it allowed.
int moy_json_valid(const char *text, size_t len);

// The members of the object `text` holds, in order: `fn` gets each key's span
// (quote to quote) and value's span, and the walk stops at its first nonzero,
// which is returned. BAD for anything that is not exactly one object.
typedef int (*moy_json_member_fn)(void *ctx, const char *key,
                                  const char *key_end, const char *val,
                                  const char *val_end);
int moy_json_object(const char *text, size_t len, moy_json_member_fn fn,
                    void *ctx);

// Walking a scanned object or array: moy_json_next gives the next member (key
// span NULL for an array's element) and returns 1, or 0 past the last.
typedef struct {
    const char *p, *end;
    char obj;
} moy_json_iter_t;

void moy_json_iter(moy_json_iter_t *it, const char *v, const char *v_end);
int moy_json_next(moy_json_iter_t *it, const char **key, const char **key_end,
                  const char **val, const char **val_end);

int moy_json_kind(const char *v, const char *v_end);

// A string span's decoded length, for sizing moy_json_string's `out`; lone
// surrogates count three bytes, as U+FFFD.
size_t moy_json_strlen(const char *s, const char *s_end);
// The decoded string into `out` (moy_json_strlen bytes): its bytes as they
// stand and each escape as UTF-8, a lone surrogate escape as U+FFFD; returns
// the length.
size_t moy_json_str(const char *s, const char *s_end, char *out);

// Whether the key or string span `s` decodes to the `n` bytes at `want`.
int moy_json_str_is(const char *s, const char *s_end, const char *want,
                    size_t n);
// Whether two string spans decode to the same string.
int moy_json_str_eq(const char *a, const char *a_end, const char *b,
                    const char *b_end);

// The value of `key` in the scanned object `obj`, the LAST member by that name
// as json.loads keeps it: 1 and its span, or 0.
int moy_json_get(const char *obj, const char *obj_end, const char *key,
                 const char **v, const char **v_end);

// Python's bool() of the value json.loads makes of the span.
int moy_json_truthy(const char *v, const char *v_end);

// Python's int() of the value json.loads makes of the span: 1 and the value
// when it fits 64 bits, 2 when it is an int that does not (the caller converts
// the text itself), 0 when int() raises (null, a container, a string that is
// no decimal literal, NaN, an infinity).
int moy_json_int(const char *v, const char *v_end, int64_t *out);

// What CPython's json.dumps(json.loads(v)) writes for the span: the default
// separators, ASCII only, a repeated key at its first place with its last
// value, numbers as Python repr()s them. Writes at most `cap` bytes to `out`
// (no NUL) and returns the length the whole needs, as snprintf does.
size_t moy_json_canon(const char *v, const char *v_end, char *out, size_t cap);

// moy_json_canon of the scanned object `obj` with `key` set to the JSON text
// `val` (in its place, or last when the object has none), or removed when
// `val` is NULL: what json.dumps writes after `obj[key] = v` or `obj.pop(key)`.
size_t moy_json_canon_set(const char *obj, const char *obj_end, const char *key,
                          const char *val, char *out, size_t cap);

// A string as json.dumps writes it, quotes included, into `out` up to `cap`;
// returns the length the whole needs.
size_t moy_json_quote(const char *s, size_t n, char *out, size_t cap);

#endif // MOY_JSON_H
