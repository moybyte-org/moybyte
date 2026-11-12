// moy_kmem: the one allocator for native state outside the VM's heap, its
// placement written in the call's flags (moy_kernel.h, KERNEL DATA IS PSRAM).
//
// moy_kalloc(n, flags) tries, in order:
//   MOY_KMEM_SMALL       the kernel's small-block pool (moy_kernel.h), for a
//                        block of at most MOY_KPOOL_MAX bytes that outlives
//                        the call while a VM may be up;
//   MOY_KMEM_PSRAM       PSRAM;
//   MOY_KMEM_INTERNAL    internal SRAM (alone: a latency-bound block);
// then, when PSRAM gave nothing,
//   MOY_KMEM_INTERNAL_OK any byte-addressable heap, internal first;
//   MOY_KMEM_INTERNAL_IF_NONE the same, only on a board with no PSRAM at all.
// MOY_KMEM_DMA adds DMA capability to every heap it asks, MOY_KMEM_ZERO zeroes
// the block. With none of SMALL, PSRAM or INTERNAL (MOY_KMEM_LIBC) it is the C
// library's malloc, placed by ESP-IDF's own rule.
//
// Off a board every block is the C library's, whatever the flags say.
// moy_kfree takes any block moy_kalloc gave, NULL included. moy_krealloc
// grows or shrinks a block made without MOY_KMEM_SMALL, in the heaps its
// flags name (PSRAM or INTERNAL; neither: the C library's), unzeroed.

#ifndef MOY_KMEM_H
#define MOY_KMEM_H

#include "moy_platform.h"

#include <stddef.h>
#include <stdlib.h>

#define MOY_KMEM_LIBC 0x00u
#define MOY_KMEM_SMALL 0x01u
#define MOY_KMEM_PSRAM 0x02u
#define MOY_KMEM_INTERNAL 0x04u
#define MOY_KMEM_INTERNAL_OK 0x08u
#define MOY_KMEM_INTERNAL_IF_NONE 0x10u
#define MOY_KMEM_DMA 0x20u
#define MOY_KMEM_ZERO 0x40u

#ifdef MOY_ON_BOARD
void *moy_kalloc(size_t n, unsigned flags);
void *moy_krealloc(void *p, size_t n, unsigned flags);
void moy_kfree(void *p);
#else
static inline void *moy_kalloc(size_t n, unsigned flags) {
    return (flags & MOY_KMEM_ZERO) ? calloc(1, n) : malloc(n);
}

static inline void *moy_krealloc(void *p, size_t n, unsigned flags) {
    (void)flags;
    return realloc(p, n);
}

static inline void moy_kfree(void *p) {
    free(p);
}
#endif

#endif // MOY_KMEM_H
