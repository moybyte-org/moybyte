// A store call's memory: every block it takes from moy_store_alloc, freed at
// once when the call is done. A call that raises through the binding is freed
// by the binding's unwind instead, since every block is scratch.

#ifndef MOY_ARENA_H
#define MOY_ARENA_H

#include <string.h>

#include "moy_vol.h"

typedef struct {
    void **p;
    size_t *n;
    size_t len, cap;
    int failed;                 // an allocation was refused
} moy_arena_t;

static inline void *moy_arena_alloc(moy_arena_t *a, size_t n) {
    if (a->len == a->cap) {
        size_t cap = a->cap ? a->cap * 2u : 16u;
        void **p = moy_store_alloc(cap * sizeof(void *));
        size_t *s = moy_store_alloc(cap * sizeof(size_t));
        if (p == NULL || s == NULL) {
            moy_store_free(p, cap * sizeof(void *));
            moy_store_free(s, cap * sizeof(size_t));
            a->failed = 1;
            return NULL;
        }
        if (a->len) {
            memcpy(p, a->p, a->len * sizeof(void *));
            memcpy(s, a->n, a->len * sizeof(size_t));
        }
        moy_store_free(a->p, a->cap * sizeof(void *));
        moy_store_free(a->n, a->cap * sizeof(size_t));
        a->p = p;
        a->n = s;
        a->cap = cap;
    }
    void *b = moy_store_alloc(n ? n : 1u);
    if (b == NULL) {
        a->failed = 1;
        return NULL;
    }
    a->p[a->len] = b;
    a->n[a->len++] = n ? n : 1u;
    return b;
}

// `n` bytes of `s` and a NUL.
static inline char *moy_arena_dup(moy_arena_t *a, const char *s, size_t n) {
    char *d = moy_arena_alloc(a, n + 1u);
    if (d != NULL) {
        memcpy(d, s, n);
        d[n] = 0;
    }
    return d;
}

// `x` and `y` joined by one "/".
static inline char *moy_arena_join(moy_arena_t *a, const char *x, const char *y) {
    size_t xn = strlen(x), yn = strlen(y);
    char *d = moy_arena_alloc(a, xn + yn + 2u);
    if (d != NULL) {
        memcpy(d, x, xn);
        size_t k = xn;
        if (k == 0 || d[k - 1u] != '/') {
            d[k++] = '/';
        }
        memcpy(d + k, y, yn + 1u);
    }
    return d;
}

static inline void moy_arena_free(moy_arena_t *a) {
    for (size_t i = 0; i < a->len; i++) {
        moy_store_free(a->p[i], a->n[i]);
    }
    moy_store_free(a->p, a->cap * sizeof(void *));
    moy_store_free(a->n, a->cap * sizeof(size_t));
    memset(a, 0, sizeof *a);
}

#endif // MOY_ARENA_H
