// The store's imports on the host, over malloc and the POSIX volume: what the
// ctypes binding links (tools/moy_index_spike.py --component fs). Every path
// is its own POSIX path; moy_store_host_live is the bytes held now, so a test
// can see a call leave nothing behind.

#include <stdlib.h>

#include "moy_vol.h"

static long live;

long moy_store_host_live(void) {
    return live;
}

int moy_vol_at(const char *path, moy_vol_t *v, const char **rest) {
    v->kind = MOY_VOL_KIND_POSIX;
    v->fs = NULL;
    *rest = path;
    return 0;
}

void moy_store_tick(void) {
}

void *moy_store_alloc(size_t n) {
    void *p = calloc(1, n ? n : 1);
    if (p != NULL) {
        live += (long)n;
    }
    return p;
}

void *moy_store_keep(size_t n) {
    return moy_store_alloc(n);
}

void moy_store_free(void *p, size_t n) {
    if (p != NULL) {
        live -= (long)n;
        free(p);
    }
}
