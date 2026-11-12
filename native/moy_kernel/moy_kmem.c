// moy_kmem: the board's half of the one allocator (moy_kmem.h has the rule).

#include "moy_kmem.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "moy_kernel.h"

void *moy_kalloc(size_t n, unsigned flags) {
    void *p = NULL;
    if (flags & MOY_KMEM_SMALL) {
        p = moy_kpool_alloc(n);
        if (p != NULL) {
            return p;
        }
    }
    uint32_t dma = (flags & MOY_KMEM_DMA) ? MALLOC_CAP_DMA : 0u;
    bool zero = (flags & MOY_KMEM_ZERO) != 0u;
    if (flags & MOY_KMEM_PSRAM) {
        uint32_t caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT | dma;
        p = zero ? heap_caps_calloc(1, n, caps) : heap_caps_malloc(n, caps);
        if (p == NULL && ((flags & MOY_KMEM_INTERNAL_OK)
                          || ((flags & MOY_KMEM_INTERNAL_IF_NONE)
                              && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0))) {
            caps = MALLOC_CAP_8BIT | dma;
            p = zero ? heap_caps_calloc(1, n, caps) : heap_caps_malloc(n, caps);
        }
        return p;
    }
    if (flags & MOY_KMEM_INTERNAL) {
        uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | dma;
        return zero ? heap_caps_calloc(1, n, caps) : heap_caps_malloc(n, caps);
    }
    return zero ? calloc(1, n) : malloc(n);
}

void *moy_krealloc(void *p, size_t n, unsigned flags) {
    uint32_t dma = (flags & MOY_KMEM_DMA) ? MALLOC_CAP_DMA : 0u;
    if (flags & MOY_KMEM_PSRAM) {
        return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT | dma);
    }
    if (flags & MOY_KMEM_INTERNAL) {
        return heap_caps_realloc(p, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT | dma);
    }
    return realloc(p, n);
}

void moy_kfree(void *p) {
    if (p == NULL || moy_kpool_free(p)) {
        return;
    }
    heap_caps_free(p);
}
