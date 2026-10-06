// The store's index as a C ABI: one row per cart folder, named by a handle.
//
// runtime/moy_index.py defines the interface and is the reference; this header
// is its native form, call for call. Sprint 1a (docs/native_kernel_2026-09.md
// section 5) implements it in C, moy_index.c here, and everything above it
// (modmoy_index.c for MicroPython, the ctypes binding on the host, the fuzz
// driver) calls only what is declared below.
//
// A handle is slot | generation << MOY_INDEX_SLOT_BITS: never 0, always below
// 2**30. A freed slot is reused lowest-first under its next generation, which
// wraps from MOY_INDEX_GEN_MAX to 1. Every call that takes a handle checks it,
// and one that names no live row is MOY_INDEX_STALE; any uint32_t is a legal
// argument.
//
// A path is `len` bytes, compared bytewise (the bindings pass a str's UTF-8).
// The pointer moy_index_path hands back stays valid until the next call that
// changes the table.
//
// Every byte an implementation holds comes from moy_index_host_alloc and goes
// back through moy_index_host_free, which the HOST defines: the gc heap under
// MicroPython (modmoy_index.c), malloc on the host (moy_index_host.c), a
// failure-injecting counter under the fuzzer (fuzz_index.c). An implementation
// allocates nothing else and keeps no global state.

#ifndef MOY_INDEX_H
#define MOY_INDEX_H

#include <stddef.h>
#include <stdint.h>

#define MOY_INDEX_SLOT_BITS 12u
#define MOY_INDEX_SLOTS (1u << MOY_INDEX_SLOT_BITS)
#define MOY_INDEX_GEN_MAX ((1u << 18) - 1u)

enum {
    MOY_INDEX_OK = 0,
    MOY_INDEX_STALE = 1,    // the handle names no live row
    MOY_INDEX_FULL = 2,     // every slot is taken: the store's ENOSPC
    MOY_INDEX_NOMEM = 3,    // the host's allocator refused; nothing changed
};

typedef struct moy_index moy_index_t;

moy_index_t *moy_index_new(void);               // an empty table, or NULL
void moy_index_free(moy_index_t *ix);           // NULL is a no-op

int moy_index_intern(moy_index_t *ix, const char *path, size_t len,
                     uint32_t *h);
uint32_t moy_index_find(const moy_index_t *ix, const char *path, size_t len);
int moy_index_path(const moy_index_t *ix, uint32_t h, const char **path,
                   size_t *len);
int moy_index_valid(const moy_index_t *ix, uint32_t h);
int moy_index_release(moy_index_t *ix, uint32_t h);
uint32_t moy_index_count(const moy_index_t *ix);

// The live rows in slot order: slots() bounds the walk, at(slot) is the
// handle of the row in `slot`, or 0 when it is free.
uint32_t moy_index_slots(const moy_index_t *ix);
uint32_t moy_index_at(const moy_index_t *ix, uint32_t slot);

// Imported from the host.
void *moy_index_host_alloc(size_t n);           // n zeroed bytes, malloc-aligned, or NULL
void moy_index_host_free(void *p, size_t n);    // n as it was allocated

#endif // MOY_INDEX_H
