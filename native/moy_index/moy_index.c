// The store's index, the C twin (moy_index.h has the contract).
//
// Rows live in an array indexed by slot that grows by doubling to
// MOY_INDEX_SLOTS; a row's path is its own allocation, hashed once (FNV-1a).
// A path finds its slot through an open-addressed table of slot + 1 entries,
// kept at most three-quarters full, tombstones included.

#include <string.h>

#include "moy_index.h"

#define SLOT_MASK (MOY_INDEX_SLOTS - 1u)
#define EMPTY 0u
#define TOMB 0xffffu

typedef struct {
    uint32_t hash;
    uint32_t len;
    char path[];        // len bytes, then a NUL
} row_t;

typedef struct {
    uint32_t gen;
    row_t *row;         // NULL while the slot is free
} slot_t;

struct moy_index {
    slot_t *slot;
    uint16_t *tab;
    uint32_t cap;       // slots allocated
    uint32_t used;      // slots ever taken: the high-water mark
    uint32_t live;
    uint32_t free_lo;   // every slot below it is live
    uint32_t tab_cap;   // a power of two, or 0 before the first row
    uint32_t tab_fill;  // live entries plus tombstones
};

static uint32_t hash_of(const char *p, size_t n) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; i++) {
        h ^= (uint8_t)p[i];
        h *= 16777619u;
    }
    return h;
}

static inline uint32_t handle_of(const moy_index_t *ix, uint32_t s) {
    return (ix->slot[s].gen << MOY_INDEX_SLOT_BITS) | s;
}

// The slot `h` names, or MOY_INDEX_SLOTS.
static uint32_t check(const moy_index_t *ix, uint32_t h) {
    uint32_t s = h & SLOT_MASK;
    if (h == 0 || s >= ix->used || ix->slot[s].row == NULL
        || ix->slot[s].gen != (h >> MOY_INDEX_SLOT_BITS)) {
        return MOY_INDEX_SLOTS;
    }
    return s;
}

// slot + 1 of the row holding `p`, or 0.
static uint32_t lookup(const moy_index_t *ix, const char *p, size_t n,
                       uint32_t hash) {
    if (ix->tab_cap == 0) {
        return 0;
    }
    uint32_t mask = ix->tab_cap - 1u;
    for (uint32_t i = hash & mask;; i = (i + 1u) & mask) {
        uint32_t e = ix->tab[i];
        if (e == EMPTY) {
            return 0;
        }
        if (e != TOMB) {
            const row_t *r = ix->slot[e - 1u].row;
            if (r->hash == hash && r->len == n && memcmp(r->path, p, n) == 0) {
                return e;
            }
        }
    }
}

static void tab_put(uint16_t *tab, uint32_t tab_cap, uint32_t hash,
                    uint32_t s) {
    uint32_t mask = tab_cap - 1u, i = hash & mask;
    while (tab[i] != EMPTY && tab[i] != TOMB) {
        i = (i + 1u) & mask;
    }
    tab[i] = (uint16_t)(s + 1u);
}

// Room for one more entry: rebuilt (grown, or rid of its tombstones) when the
// next insert would take it past three-quarters full.
static int tab_reserve(moy_index_t *ix) {
    if ((ix->tab_fill + 1u) * 4u <= ix->tab_cap * 3u) {
        return MOY_INDEX_OK;
    }
    uint32_t cap = 8u;
    while (cap < (ix->live + 1u) * 2u) {
        cap <<= 1;
    }
    uint16_t *tab = moy_index_host_alloc(cap * sizeof(uint16_t));
    if (tab == NULL) {
        return MOY_INDEX_NOMEM;
    }
    for (uint32_t s = 0; s < ix->used; s++) {
        if (ix->slot[s].row != NULL) {
            tab_put(tab, cap, ix->slot[s].row->hash, s);
        }
    }
    moy_index_host_free(ix->tab, ix->tab_cap * sizeof(uint16_t));
    ix->tab = tab;
    ix->tab_cap = cap;
    ix->tab_fill = ix->live;
    return MOY_INDEX_OK;
}

static int slots_reserve(moy_index_t *ix) {
    if (ix->used < ix->cap) {
        return MOY_INDEX_OK;
    }
    uint32_t cap = ix->cap ? ix->cap * 2u : 8u;
    slot_t *slot = moy_index_host_alloc(cap * sizeof(slot_t));
    if (slot == NULL) {
        return MOY_INDEX_NOMEM;
    }
    if (ix->used) {
        memcpy(slot, ix->slot, ix->used * sizeof(slot_t));
    }
    moy_index_host_free(ix->slot, ix->cap * sizeof(slot_t));
    ix->slot = slot;
    ix->cap = cap;
    return MOY_INDEX_OK;
}

static size_t row_size(uint32_t len) {
    return sizeof(row_t) + (size_t)len + 1u;
}

moy_index_t *moy_index_new(void) {
    return moy_index_host_alloc(sizeof(moy_index_t));
}

void moy_index_free(moy_index_t *ix) {
    if (ix == NULL) {
        return;
    }
    for (uint32_t s = 0; s < ix->used; s++) {
        row_t *r = ix->slot[s].row;
        if (r != NULL) {
            moy_index_host_free(r, row_size(r->len));
        }
    }
    moy_index_host_free(ix->slot, ix->cap * sizeof(slot_t));
    moy_index_host_free(ix->tab, ix->tab_cap * sizeof(uint16_t));
    moy_index_host_free(ix, sizeof(moy_index_t));
}

int moy_index_intern(moy_index_t *ix, const char *path, size_t len,
                     uint32_t *h) {
    uint32_t hash = hash_of(path, len);
    uint32_t e = lookup(ix, path, len, hash);
    if (e) {
        *h = handle_of(ix, e - 1u);
        return MOY_INDEX_OK;
    }
    int fresh = ix->live == ix->used;
    if (fresh && ix->used == MOY_INDEX_SLOTS) {
        return MOY_INDEX_FULL;
    }
    if (len > UINT32_MAX - sizeof(row_t) - 1u) {
        return MOY_INDEX_NOMEM;
    }
    if ((fresh && slots_reserve(ix) != MOY_INDEX_OK)
        || tab_reserve(ix) != MOY_INDEX_OK) {
        return MOY_INDEX_NOMEM;
    }
    row_t *r = moy_index_host_alloc(row_size((uint32_t)len));
    if (r == NULL) {
        return MOY_INDEX_NOMEM;
    }
    r->hash = hash;
    r->len = (uint32_t)len;
    if (len) {
        memcpy(r->path, path, len);
    }
    uint32_t s;
    if (fresh) {
        s = ix->used++;
        ix->slot[s].gen = 1u;
        ix->free_lo = ix->used;
    } else {
        s = ix->free_lo;
        while (ix->slot[s].row != NULL) {
            s++;
        }
        ix->free_lo = s + 1u;
    }
    ix->slot[s].row = r;
    ix->live++;
    uint32_t mask = ix->tab_cap - 1u, i = hash & mask;
    while (ix->tab[i] != EMPTY && ix->tab[i] != TOMB) {
        i = (i + 1u) & mask;
    }
    if (ix->tab[i] == EMPTY) {
        ix->tab_fill++;
    }
    ix->tab[i] = (uint16_t)(s + 1u);
    *h = handle_of(ix, s);
    return MOY_INDEX_OK;
}

uint32_t moy_index_find(const moy_index_t *ix, const char *path, size_t len) {
    uint32_t e = lookup(ix, path, len, hash_of(path, len));
    return e ? handle_of(ix, e - 1u) : 0u;
}

int moy_index_path(const moy_index_t *ix, uint32_t h, const char **path,
                   size_t *len) {
    uint32_t s = check(ix, h);
    if (s == MOY_INDEX_SLOTS) {
        return MOY_INDEX_STALE;
    }
    *path = ix->slot[s].row->path;
    *len = ix->slot[s].row->len;
    return MOY_INDEX_OK;
}

int moy_index_valid(const moy_index_t *ix, uint32_t h) {
    return check(ix, h) != MOY_INDEX_SLOTS;
}

int moy_index_release(moy_index_t *ix, uint32_t h) {
    uint32_t s = check(ix, h);
    if (s == MOY_INDEX_SLOTS) {
        return MOY_INDEX_STALE;
    }
    row_t *r = ix->slot[s].row;
    uint32_t mask = ix->tab_cap - 1u, i = r->hash & mask;
    while (ix->tab[i] != s + 1u) {
        i = (i + 1u) & mask;
    }
    if (ix->tab[(i + 1u) & mask] == EMPTY) {
        ix->tab[i] = EMPTY;     // the end of a probe run needs no tombstone
        ix->tab_fill--;
    } else {
        ix->tab[i] = TOMB;
    }
    moy_index_host_free(r, row_size(r->len));
    ix->slot[s].row = NULL;
    ix->slot[s].gen = ix->slot[s].gen >= MOY_INDEX_GEN_MAX
        ? 1u : ix->slot[s].gen + 1u;
    ix->live--;
    if (s < ix->free_lo) {
        ix->free_lo = s;
    }
    return MOY_INDEX_OK;
}

uint32_t moy_index_count(const moy_index_t *ix) {
    return ix->live;
}

uint32_t moy_index_slots(const moy_index_t *ix) {
    return ix->used;
}

uint32_t moy_index_at(const moy_index_t *ix, uint32_t slot) {
    if (slot >= ix->used || ix->slot[slot].row == NULL) {
        return 0u;
    }
    return handle_of(ix, slot);
}
