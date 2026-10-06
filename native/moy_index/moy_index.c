// The store's index, the C twin (moy_index.h has the contract).
//
// The slots are native/moy_spine's handle table (moy_htab.h), unkinded: its
// handle is this header's, slot | generation << 12, so the bookkeeping --
// generations, lowest-first reuse, the check on every use -- is the kernel's one
// implementation. A row holds a pointer to its path's own allocation, hashed
// once (FNV-1a). A path finds its slot through an open-addressed table of
// slot + 1 entries, kept at most three-quarters full, tombstones included.

#include <string.h>

#include "moy_htab.h"
#include "moy_index.h"

#define EMPTY 0u
#define TOMB 0xffffu

typedef char slot_bits_agree[MOY_INDEX_SLOT_BITS == MOY_HTAB_GEN_SHIFT ? 1 : -1];
typedef char slots_agree[MOY_INDEX_SLOTS == MOY_HTAB_SLOTS_PLAIN ? 1 : -1];
typedef char gen_max_agrees[MOY_INDEX_GEN_MAX == MOY_HTAB_GEN_MAX ? 1 : -1];

typedef struct {
    uint32_t hash;
    uint32_t len;
    char path[];        // len bytes, then a NUL
} row_t;

struct moy_index {
    moy_htab_t *t;      // rows of one row_t *
    uint16_t *tab;
    uint32_t tab_cap;   // a power of two, or 0 before the first row
    uint32_t tab_fill;  // live entries plus tombstones
};

static const moy_htab_mem_t mem = { moy_index_host_alloc, moy_index_host_free };

static inline row_t *row_at(const moy_index_t *ix, uint32_t s) {
    return *(row_t **)moy_htab_row(ix->t, s);
}

static uint32_t hash_of(const char *p, size_t n) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; i++) {
        h ^= (uint8_t)p[i];
        h *= 16777619u;
    }
    return h;
}

// The slot `h` names, or MOY_HTAB_NOSLOT.
static inline uint32_t check(const moy_index_t *ix, uint32_t h) {
    return moy_htab_slot_of(ix->t, h);
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
            const row_t *r = row_at(ix, e - 1u);
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
    while (cap < (moy_htab_count(ix->t) + 1u) * 2u) {
        cap <<= 1;
    }
    uint16_t *tab = moy_index_host_alloc(cap * sizeof(uint16_t));
    if (tab == NULL) {
        return MOY_INDEX_NOMEM;
    }
    for (uint32_t s = 0, n = moy_htab_slots(ix->t); s < n; s++) {
        if (moy_htab_live(ix->t, s)) {
            tab_put(tab, cap, row_at(ix, s)->hash, s);
        }
    }
    moy_index_host_free(ix->tab, ix->tab_cap * sizeof(uint16_t));
    ix->tab = tab;
    ix->tab_cap = cap;
    ix->tab_fill = moy_htab_count(ix->t);
    return MOY_INDEX_OK;
}

static size_t row_size(uint32_t len) {
    return sizeof(row_t) + (size_t)len + 1u;
}

moy_index_t *moy_index_new(void) {
    moy_index_t *ix = moy_index_host_alloc(sizeof(moy_index_t));
    if (ix == NULL) {
        return NULL;
    }
    ix->t = moy_htab_new(&mem, MOY_KIND_NONE, MOY_INDEX_SLOTS, sizeof(row_t *));
    if (ix->t == NULL) {
        moy_index_host_free(ix, sizeof(moy_index_t));
        return NULL;
    }
    return ix;
}

void moy_index_free(moy_index_t *ix) {
    if (ix == NULL) {
        return;
    }
    for (uint32_t s = 0, n = moy_htab_slots(ix->t); s < n; s++) {
        if (moy_htab_live(ix->t, s)) {
            row_t *r = row_at(ix, s);
            moy_index_host_free(r, row_size(r->len));
        }
    }
    moy_htab_free(ix->t);
    moy_index_host_free(ix->tab, ix->tab_cap * sizeof(uint16_t));
    moy_index_host_free(ix, sizeof(moy_index_t));
}

int moy_index_intern(moy_index_t *ix, const char *path, size_t len,
                     uint32_t *h) {
    uint32_t hash = hash_of(path, len);
    uint32_t e = lookup(ix, path, len, hash);
    if (e) {
        *h = moy_htab_handle(ix->t, e - 1u);
        return MOY_INDEX_OK;
    }
    if (moy_htab_full(ix->t)) {
        return MOY_INDEX_FULL;
    }
    if (len > UINT32_MAX - sizeof(row_t) - 1u) {
        return MOY_INDEX_NOMEM;
    }
    if (tab_reserve(ix) != MOY_INDEX_OK) {
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
    row_t **slot;
    uint32_t handle;
    if (moy_htab_add(ix->t, &handle, (void **)&slot) != MOY_HTAB_OK) {
        moy_index_host_free(r, row_size((uint32_t)len));
        return MOY_INDEX_NOMEM;
    }
    *slot = r;
    uint32_t s = handle & (MOY_INDEX_SLOTS - 1u);
    uint32_t mask = ix->tab_cap - 1u, i = hash & mask;
    while (ix->tab[i] != EMPTY && ix->tab[i] != TOMB) {
        i = (i + 1u) & mask;
    }
    if (ix->tab[i] == EMPTY) {
        ix->tab_fill++;
    }
    ix->tab[i] = (uint16_t)(s + 1u);
    *h = handle;
    return MOY_INDEX_OK;
}

uint32_t moy_index_find(const moy_index_t *ix, const char *path, size_t len) {
    uint32_t e = lookup(ix, path, len, hash_of(path, len));
    return e ? moy_htab_handle(ix->t, e - 1u) : 0u;
}

int moy_index_path(const moy_index_t *ix, uint32_t h, const char **path,
                   size_t *len) {
    uint32_t s = check(ix, h);
    if (s == MOY_HTAB_NOSLOT) {
        return MOY_INDEX_STALE;
    }
    const row_t *r = row_at(ix, s);
    *path = r->path;
    *len = r->len;
    return MOY_INDEX_OK;
}

int moy_index_valid(const moy_index_t *ix, uint32_t h) {
    return check(ix, h) != MOY_HTAB_NOSLOT;
}

int moy_index_release(moy_index_t *ix, uint32_t h) {
    uint32_t s = check(ix, h);
    if (s == MOY_HTAB_NOSLOT) {
        return MOY_INDEX_STALE;
    }
    row_t *r = row_at(ix, s);
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
    moy_htab_release(ix->t, h);
    return MOY_INDEX_OK;
}

uint32_t moy_index_count(const moy_index_t *ix) {
    return moy_htab_count(ix->t);
}

uint32_t moy_index_slots(const moy_index_t *ix) {
    return moy_htab_slots(ix->t);
}

uint32_t moy_index_at(const moy_index_t *ix, uint32_t slot) {
    return moy_htab_at(ix->t, slot);
}
