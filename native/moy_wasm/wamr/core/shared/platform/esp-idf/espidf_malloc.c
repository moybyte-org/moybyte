/*
 * Copyright (C) 2019 Intel Corporation.  All rights reserved.
 * SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
 */

#include "platform_api_vmcore.h"
#include "platform_api_extension.h"
#include "esp_heap_caps.h"

/* The runtime's own allocations when it runs on the system allocator: from
 * WASM_ESPIDF_PSRAM_THRESHOLD bytes up they are PSRAM only on a board with
 * PSRAM, and a request PSRAM cannot serve fails rather than landing in the
 * internal SRAM the radios and the display's DMA share. */
static void *
data_alloc(size_t size)
{
#if CONFIG_SPIRAM
    if (size >= WASM_ESPIDF_PSRAM_THRESHOLD) {
        return heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
#endif
    return malloc(size);
}

static void *
data_realloc(void *ptr, size_t size)
{
#if CONFIG_SPIRAM
    if (size >= WASM_ESPIDF_PSRAM_THRESHOLD) {
        return heap_caps_realloc(ptr, size,
                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
#endif
    return realloc(ptr, size);
}

void *
os_malloc(unsigned size)
{
    void *buf_origin;
    void *buf_fixed;
    uintptr_t *addr_field;

    buf_origin = data_alloc(size + 8 + sizeof(uintptr_t));
    if (!buf_origin) {
        return NULL;
    }
    buf_fixed = buf_origin + sizeof(void *);
    if ((uintptr_t)buf_fixed & (uintptr_t)0x7) {
        buf_fixed = (void *)((uintptr_t)(buf_fixed + 8) & (~(uintptr_t)7));
    }

    addr_field = buf_fixed - sizeof(uintptr_t);
    *addr_field = (uintptr_t)buf_origin;

    return buf_fixed;
}

void *
os_realloc(void *ptr, unsigned size)
{
    void *mem_origin;
    void *mem_new;
    void *mem_new_fixed;
    uintptr_t *addr_field;

    if (!ptr) {
        return os_malloc(size);
    }

    addr_field = ptr - sizeof(uintptr_t);
    mem_origin = (void *)(*addr_field);
    mem_new = data_realloc(mem_origin, size + 8 + sizeof(uintptr_t));
    if (!mem_new) {
        return NULL;
    }

    if (mem_origin != mem_new) {
        mem_new_fixed = mem_new + sizeof(uintptr_t);
        if ((uint32)mem_new_fixed & 0x7) {
            mem_new_fixed =
                (void *)((uintptr_t)(mem_new + 8) & (~(uintptr_t)7));
        }

        addr_field = mem_new_fixed - sizeof(uintptr_t);
        *addr_field = (uintptr_t)mem_new;

        return mem_new_fixed;
    }

    return ptr;
}

void
os_free(void *ptr)
{
    void *mem_origin;
    uintptr_t *addr_field;

    if (ptr) {
        addr_field = ptr - sizeof(uintptr_t);
        mem_origin = (void *)(*addr_field);

        free(mem_origin);
    }
}

int
os_dumps_proc_mem_info(char *out, unsigned int size)
{
    return -1;
}
