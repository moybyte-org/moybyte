// The index's two imports on the host, over malloc: what the ctypes binding
// and the sanitizer builds link (tools/moy_index_spike.py).

#include <stdlib.h>

#include "moy_index.h"

void *moy_index_host_alloc(size_t n) {
    return calloc(1, n ? n : 1);
}

void moy_index_host_free(void *p, size_t n) {
    (void)n;
    free(p);
}
