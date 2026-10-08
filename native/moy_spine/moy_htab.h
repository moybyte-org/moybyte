// The kernel's handle table: rows in slots, named by checked integers.
//
// runtime/moy_spine.py's Table is the interface and the reference; this is its
// native form, and the one implementation of the discipline every handle in the
// kernel follows (the store index, native/moy_index, takes its slot bookkeeping
// from here too). Everything above it calls only what is declared below.
//
// A handle is gen << 12 | kind << 8 | slot: never 0, always below 2**30. KIND is
// 1..15 and part of the handle, so a handle made by one table and given to
// another is refused instead of read as a row there; such a table has up to 256
// slots. KIND 0 is the unkinded table (the store index's): the handle is
// gen << 12 | slot, with 12 bits of slot, up to 4096. GEN is 1..GEN_MAX and wraps
// to 1, never 0. A freed slot is reused lowest-first under its next generation.
//
// A row is `row_size` bytes of the client's struct, zeroed when it is taken and
// when it is released. Its address is stable until the next add grows the
// table, and for good once moy_htab_reserve has allocated every slot. The
// table owns the bytes, never what a row points at: a client frees that before
// it releases the row (or walks the table before it frees it).
//
// Every byte comes from the table's allocator, which the client passes in
// (moy_htab_mem_t): PSRAM on a board, malloc on the host, a failure-injecting
// counter under the fuzzer. A table keeps no global state.

#ifndef MOY_HTAB_H
#define MOY_HTAB_H

#include <stddef.h>
#include <stdint.h>

#define MOY_HTAB_KIND_SHIFT 8u
#define MOY_HTAB_GEN_SHIFT 12u
#define MOY_HTAB_GEN_MAX ((1u << 18) - 1u)
#define MOY_HTAB_SLOTS 256u         // the most slots a kinded table has
#define MOY_HTAB_SLOTS_PLAIN 4096u  // and an unkinded one

// The kinds, one per client table. A later sprint adds its own here.
enum {
    MOY_KIND_NONE = 0,
    MOY_KIND_APP = 1,       // the registered system apps (moy_route)
    MOY_KIND_BUF = 2,       // off-heap buffers: layers, bakes, scratches, the pool
    MOY_KIND_CANVAS = 3,    // canvases: a buffer, its size, clip, camera, palette
    MOY_KIND_SURF = 4,      // the surface table (docs/surface_model_v1.md)
    MOY_KIND_OWNER = 5,     // lifetimes that hold loans: a run, a window, an app
    MOY_KIND_SRC = 6,       // input sources: keyboards, touch, the browser, net slots
    MOY_KIND_PEER = 7,      // the radio link's peers
    MOY_KIND_AUDIO = 8,     // audio sessions, one per owner
    MOY_KIND_CLIP = 9,      // the sample voice's clips (native/moy_audio)
    MOY_KIND_IMAGE = 10,    // a run's decoded pictures (native/moy_play)
    MOY_KIND_ACTOR = 11,    // a run's scene actors (native/moy_play)
};

enum {
    MOY_HTAB_OK = 0,
    MOY_HTAB_STALE = 1,     // the handle names no live row of this table
    MOY_HTAB_FULL = 2,      // every slot is taken: the callers' ENOSPC
    MOY_HTAB_NOMEM = 3,     // the allocator refused; nothing changed
};

typedef struct {
    void *(*alloc)(size_t n);               // n zeroed bytes, 8-aligned, or NULL
    void (*release)(void *p, size_t n);     // n as it was allocated
} moy_htab_mem_t;

#define MOY_HTAB_LIVE 0x80000000u
#define MOY_HTAB_NOSLOT 0xffffffffu

typedef struct {
    const moy_htab_mem_t *mem;
    uint32_t *meta;         // per slot: generation, MOY_HTAB_LIVE while taken
    uint8_t *rows;          // cap rows of row_size bytes
    uint32_t cap;           // slots allocated
    uint32_t used;          // slots ever taken: the high-water mark
    uint32_t live;
    uint32_t free_lo;       // every slot below it is live
    uint32_t max;           // slots the table may have
    uint32_t row_size;
    uint32_t slot_mask;     // 0xff for a kinded table, 0xfff for an unkinded
    uint32_t kind_mask;     // the handle's kind bits this table checks
    uint32_t kind_bits;     // and what they must be
} moy_htab_t;

// A table of `slots` rows of `row_size` bytes under `kind` (0 for none), or
// NULL for a kind or size it cannot name, or when the allocator refuses.
moy_htab_t *moy_htab_new(const moy_htab_mem_t *mem, uint8_t kind,
                         uint32_t slots, size_t row_size);
void moy_htab_free(moy_htab_t *t);              // NULL is a no-op

// Take the lowest free slot: OK, FULL or NOMEM. `h` and `row` (a zeroed row)
// are set on OK.
int moy_htab_add(moy_htab_t *t, uint32_t *h, void **row);
// OK, or STALE when the handle names no live row.
int moy_htab_release(moy_htab_t *t, uint32_t h);
// Allocate every slot the table may have now, so no later add moves a row:
// OK or NOMEM.
int moy_htab_reserve(moy_htab_t *t);

// The slot `h` names, or MOY_HTAB_NOSLOT. Any uint32_t is a legal argument.
static inline uint32_t moy_htab_slot_of(const moy_htab_t *t, uint32_t h) {
    uint32_t s = h & t->slot_mask;
    if (h == 0u || (h & t->kind_mask) != t->kind_bits || s >= t->used) {
        return MOY_HTAB_NOSLOT;
    }
    uint32_t m = t->meta[s];
    if ((m & MOY_HTAB_LIVE) == 0u
        || (m & ~MOY_HTAB_LIVE) != (h >> MOY_HTAB_GEN_SHIFT)) {
        return MOY_HTAB_NOSLOT;
    }
    return s;
}

static inline void *moy_htab_row(const moy_htab_t *t, uint32_t slot) {
    return t->rows + (size_t)slot * t->row_size;
}

static inline int moy_htab_get(const moy_htab_t *t, uint32_t h, void **row) {
    uint32_t s = moy_htab_slot_of(t, h);
    if (s == MOY_HTAB_NOSLOT) {
        return MOY_HTAB_STALE;
    }
    *row = moy_htab_row(t, s);
    return MOY_HTAB_OK;
}

static inline int moy_htab_live(const moy_htab_t *t, uint32_t slot) {
    return slot < t->used && (t->meta[slot] & MOY_HTAB_LIVE) != 0u;
}

// The handle of the live row in `slot`.
static inline uint32_t moy_htab_handle(const moy_htab_t *t, uint32_t slot) {
    return ((t->meta[slot] & ~MOY_HTAB_LIVE) << MOY_HTAB_GEN_SHIFT)
           | t->kind_bits | slot;
}

static inline uint32_t moy_htab_count(const moy_htab_t *t) {
    return t->live;
}

static inline int moy_htab_full(const moy_htab_t *t) {
    return t->live == t->max;
}

// The live rows in slot order: slots() bounds the walk, at(slot) is the handle
// of the row in `slot`, or 0 when it is free or past the end.
static inline uint32_t moy_htab_slots(const moy_htab_t *t) {
    return t->used;
}

static inline uint32_t moy_htab_at(const moy_htab_t *t, uint32_t slot) {
    return moy_htab_live(t, slot) ? moy_htab_handle(t, slot) : 0u;
}

#endif // MOY_HTAB_H
