// The kernel's handle table (moy_htab.h has the contract).
//
// Slots grow by doubling, from 8 to the table's maximum, in ONE block: the
// per-slot words first, then the rows, 8-aligned. A slot's word is its
// generation, with MOY_HTAB_LIVE set while a row holds it.

#include <string.h>

#include "moy_htab.h"

static size_t rows_offset(uint32_t cap) {
    return ((size_t)cap * sizeof(uint32_t) + 7u) & ~(size_t)7u;
}

static size_t block_size(uint32_t cap, uint32_t row_size) {
    return rows_offset(cap) + (size_t)cap * row_size;
}

moy_htab_t *moy_htab_new(const moy_htab_mem_t *mem, uint8_t kind,
                         uint32_t slots, size_t row_size) {
    uint32_t most = kind ? MOY_HTAB_SLOTS : MOY_HTAB_SLOTS_PLAIN;
    if (kind > 15u || slots == 0u || slots > most || row_size > 0xffffu) {
        return NULL;
    }
    moy_htab_t *t = mem->alloc(sizeof(moy_htab_t));
    if (t == NULL) {
        return NULL;
    }
    t->mem = mem;
    t->max = slots;
    t->row_size = (uint32_t)row_size;
    t->slot_mask = kind ? 0xffu : 0xfffu;
    t->kind_mask = kind ? 0xf00u : 0u;
    t->kind_bits = (uint32_t)kind << MOY_HTAB_KIND_SHIFT;
    return t;
}

void moy_htab_free(moy_htab_t *t) {
    if (t == NULL) {
        return;
    }
    if (t->meta != NULL) {
        t->mem->release(t->meta, block_size(t->cap, t->row_size));
    }
    t->mem->release(t, sizeof(moy_htab_t));
}

static int grow(moy_htab_t *t) {
    uint32_t cap = t->cap ? t->cap * 2u : 8u;
    if (cap > t->max) {
        cap = t->max;
    }
    uint8_t *block = t->mem->alloc(block_size(cap, t->row_size));
    if (block == NULL) {
        return MOY_HTAB_NOMEM;
    }
    uint32_t *meta = (uint32_t *)(void *)block;
    uint8_t *rows = block + rows_offset(cap);
    if (t->used) {
        memcpy(meta, t->meta, (size_t)t->used * sizeof(uint32_t));
        memcpy(rows, t->rows, (size_t)t->used * t->row_size);
    }
    if (t->meta != NULL) {
        t->mem->release(t->meta, block_size(t->cap, t->row_size));
    }
    t->meta = meta;
    t->rows = rows;
    t->cap = cap;
    return MOY_HTAB_OK;
}

int moy_htab_add(moy_htab_t *t, uint32_t *h, void **row) {
    int fresh = t->live == t->used;
    if (fresh) {
        if (t->used == t->max) {
            return MOY_HTAB_FULL;
        }
        if (t->used == t->cap && grow(t) != MOY_HTAB_OK) {
            return MOY_HTAB_NOMEM;
        }
    }
    uint32_t s;
    if (fresh) {
        s = t->used++;
        t->meta[s] = 1u;
        t->free_lo = t->used;
    } else {
        s = t->free_lo;
        while (t->meta[s] & MOY_HTAB_LIVE) {
            s++;
        }
        t->free_lo = s + 1u;
    }
    t->meta[s] |= MOY_HTAB_LIVE;
    t->live++;
    void *r = moy_htab_row(t, s);
    *h = moy_htab_handle(t, s);
    *row = r;
    return MOY_HTAB_OK;
}

int moy_htab_release(moy_htab_t *t, uint32_t h) {
    uint32_t s = moy_htab_slot_of(t, h);
    if (s == MOY_HTAB_NOSLOT) {
        return MOY_HTAB_STALE;
    }
    memset(moy_htab_row(t, s), 0, t->row_size);
    uint32_t g = t->meta[s] & ~MOY_HTAB_LIVE;
    t->meta[s] = g >= MOY_HTAB_GEN_MAX ? 1u : g + 1u;
    t->live--;
    if (s < t->free_lo) {
        t->free_lo = s;
    }
    return MOY_HTAB_OK;
}
