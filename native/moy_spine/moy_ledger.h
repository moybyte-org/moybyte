// moy_ledger: the strike ledger's slot (runtime/crash_guard.py), in C, over the
// settings rows' JSON text. A slot is the object
//
//     {"strikes": {id: n, ...}, "open": id | null, "proven": {id: proof, ...}}
//
// stored under one settings key per role. The functions read a slot's text and
// make the text the Python ledger's json.dumps would write for the same change,
// byte for byte (members keep their order, a new one goes last, `", "` and
// `": "`), so a slot either implementation wrote reads back in the other. An id
// and a proof are JSON strings: the caller passes them as json.dumps wrote them,
// quotes included. A slot that is not an object reads as an empty one, as the
// Python ledger's `_data` does.
//
// Every byte of an edit's result comes from the moy_htab_mem_t the caller passes.

#ifndef MOY_LEDGER_H
#define MOY_LEDGER_H

#include <stddef.h>
#include <stdint.h>

#include "moy_htab.h"

#ifdef __cplusplus
extern "C" {
#endif

// A member of a JSON object: its key and its value, as spans of the text.
typedef struct {
    const char *k;
    size_t kn;       // the key's text, quotes included
    const char *v;
    size_t vn;
} moy_jmem_t;

// The members of the object `text` holds: their count, and up to `cap` of them
// in `m`. -1 when the text is not one object.
int moy_jobj_members(const char *text, size_t n, moy_jmem_t *m, size_t cap);

// Member `i` of the object `text` holds: 1 and its spans, else 0.
int moy_jobj_at(const char *text, size_t n, int i, moy_jmem_t *out);

// The slot's member `name`: 1 and its value's text, else 0.
int moy_ledger_get(const char *slot, size_t n, const char *name, const char **v,
                   size_t *vn);

enum { MOY_LEDGER_ARM = 1, MOY_LEDGER_HEAL, MOY_LEDGER_FORGIVE };
enum { MOY_LEDGER_NOMEM = -1 };

// Failed opens recorded against `id` (int() of the value, 0 when it is not one).
int32_t moy_ledger_strikes(const char *slot, size_t n, const char *id, size_t idn);

// 1 when the slot's "open" is the string `id`.
int moy_ledger_open_is(const char *slot, size_t n, const char *id, size_t idn);

// 1 when the slot records `proof` as `id`'s proven run.
int moy_ledger_proven_is(const char *slot, size_t n, const char *id, size_t idn,
                         const char *proof, size_t pn);

// The slot's member `name` (a key without its quotes) when its value is an
// object: 1 and its text, else 0.
int moy_ledger_section(const char *slot, size_t n, const char *name,
                       const char **v, size_t *vn);

// The slot after one edit. `slot` is NULL when the store has no row.
//   ARM      strikes[id] += 1, open = id
//   HEAL     strikes[id] removed, open = null when it was id, proven[id] = proof
//            (`proof` NULL records none)
//   FORGIVE  strikes[id] removed, open = null when it was id; nothing to write
//            when the slot knew neither
// Returns 1 with the new text in `*out` (`*outn` bytes, from `mem`: release it
// with mem->release(*out, *outn)), 0 when there is nothing to write, or
// MOY_LEDGER_NOMEM.
int moy_ledger_edit(const moy_htab_mem_t *mem, const char *slot, size_t n,
                    int op, const char *id, size_t idn, const char *proof,
                    size_t pn, char **out, size_t *outn);

#ifdef __cplusplus
}
#endif

#endif // MOY_LEDGER_H
