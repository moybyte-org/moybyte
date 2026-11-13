// moy_sb: the one byte builder, header-only, in two kinds by how it is made.
//
//   GROWN  moy_sb_grown(mem): a block from `mem` (moy_htab_mem_t: a module's
//          allocator), 256 bytes and doubled as it fills, NUL-terminated
//          after every put. A block `mem` refuses sets `failed`: the bytes so
//          far stay, every later put is a no-op, and moy_sb_free releases
//          them either way.
//   FIXED  moy_sb_fixed(out, cap): the caller's `cap` bytes. A put copies
//          what fits and `n` counts every byte put, so `n > cap` says how many
//          the whole needs (snprintf's rule). Nothing is NUL-terminated until
//          moy_sb_end.

#ifndef MOY_SB_H
#define MOY_SB_H

#include <stddef.h>
#include <string.h>

#include "moy_htab.h"

// The larger functions are plain statics, not `inline`: the compiler keeps one
// copy per module and inlines them only where that pays, where the hint would
// copy each body into every call site of every module.
#ifndef MOY_SPINE_OUTLINE
#define MOY_SPINE_OUTLINE __attribute__((unused))
#endif

typedef struct {
    char *p;
    size_t n, cap;
    const moy_htab_mem_t *mem;      // NULL: FIXED
    int failed;
} moy_sb_t;

static inline moy_sb_t moy_sb_grown(const moy_htab_mem_t *mem) {
    moy_sb_t b = { NULL, 0, 0, mem, 0 };
    return b;
}

static inline moy_sb_t moy_sb_fixed(void *out, size_t cap) {
    moy_sb_t b = { (char *)out, 0, cap, NULL, 0 };
    return b;
}

static MOY_SPINE_OUTLINE void moy_sb_put(moy_sb_t *b, const void *s, size_t n) {
    if (b->mem == NULL) {
        if (b->n < b->cap) {
            size_t room = b->cap - b->n;
            memcpy(b->p + b->n, s, n < room ? n : room);
        }
        b->n += n;
        return;
    }
    if (b->failed) {
        return;
    }
    if (b->n + n + 1u > b->cap) {
        size_t cap = b->cap ? b->cap : 256u;
        while (cap < b->n + n + 1u) {
            cap *= 2u;
        }
        char *p = b->mem->alloc(cap);
        if (p == NULL) {
            b->failed = 1;
            return;
        }
        if (b->n) {
            memcpy(p, b->p, b->n);
        }
        if (b->p != NULL) {
            b->mem->release(b->p, b->cap);
        }
        b->p = p;
        b->cap = cap;
    }
    if (n) {
        memcpy(b->p + b->n, s, n);
    }
    b->n += n;
    b->p[b->n] = 0;
}

static inline void moy_sb_str(moy_sb_t *b, const char *s) {
    moy_sb_put(b, s, strlen(s));
}

static inline void moy_sb_byte(moy_sb_t *b, char c) {
    moy_sb_put(b, &c, 1);
}

// A FIXED builder's bytes NUL-terminated where they end or at its last byte,
// whichever comes first: the length kept (0 with no room at all).
static inline size_t moy_sb_end(moy_sb_t *b) {
    if (b->cap == 0) {
        return 0;
    }
    size_t k = b->n < b->cap ? b->n : b->cap - 1u;
    b->p[k] = 0;
    return k;
}

// A GROWN builder's block released, the builder empty again.
static inline void moy_sb_free(moy_sb_t *b) {
    if (b->p != NULL) {
        b->mem->release(b->p, b->cap);
    }
    b->p = NULL;
    b->n = b->cap = 0;
    b->failed = 0;
}

#endif // MOY_SB_H
