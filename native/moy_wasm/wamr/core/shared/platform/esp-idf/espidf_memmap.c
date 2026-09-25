/*
 * Copyright (C) 2019 Intel Corporation.  All rights reserved.
 * SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
 */

#include "platform_api_vmcore.h"
#include "platform_api_extension.h"
#if WASM_ESPIDF_EXEC_IN_PSRAM != 0
#include "esp_cache.h"
#endif
#if (WASM_MEM_DUAL_BUS_MIRROR != 0)
#include "soc/soc.h"

/* PSRAM at data address D is fetched as instructions at
 * D + (SOC_IROM_LOW - SOC_DROM_LOW): the S3 MMU serves both buses from one
 * table. (SOC_IROM_LOW - SOC_IROM_HIGH) lands outside every mapped range on
 * IDF 5.5. */
#define MEM_DUAL_BUS_OFFSET (SOC_IROM_LOW - SOC_DROM_LOW)

#define in_ibus_ext(addr) \
    (((uint32)addr >= SOC_IROM_LOW) && ((uint32)addr < SOC_IROM_HIGH))
#endif

/* Where a mapping comes from, on a board with PSRAM:
 *
 * - Executable memory (AOT text) is PSRAM on the ESP32-S3, fetched through
 *   the instruction-bus alias, and on the ESP32-P4, whose external RAM
 *   carries no PMP entry. A load PSRAM cannot serve FAILS: it is never
 *   served from the internal exec heap, which on these boards is the same
 *   SRAM WiFi, BLE and the display's DMA live on.
 * - Data mappings (linear memory, AOT data sections) of
 *   WASM_ESPIDF_PSRAM_THRESHOLD bytes or more are PSRAM only, and fail the
 *   same way; smaller ones take the heap's default order.
 *
 * Other targets keep the internal exec heap. */
static uint32_t
mmap_caps(size_t size, int prot)
{
    if (prot & MMAP_PROT_EXEC) {
#if WASM_ESPIDF_EXEC_IN_PSRAM != 0
        return MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
#else
        return MALLOC_CAP_EXEC;
#endif
    }
#if CONFIG_SPIRAM
    if (size >= WASM_ESPIDF_PSRAM_THRESHOLD) {
        return MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    }
#endif
    (void)size;
    return MALLOC_CAP_8BIT;
}

void *
os_mmap(void *hint, size_t size, int prot, int flags, os_file_handle file)
{
    /* heap_caps_malloc returns 4-byte aligned blocks: reserve room to align
       to 8 and to keep the originally allocated address just below the
       returned one, where os_free finds it. */
    void *buf_origin =
        heap_caps_malloc(size + 4 + sizeof(uintptr_t), mmap_caps(size, prot));
    if (!buf_origin) {
        return NULL;
    }
    void *buf_fixed = buf_origin + sizeof(void *);
    if ((uintptr_t)buf_fixed & (uintptr_t)0x7) {
        buf_fixed = (void *)((uintptr_t)(buf_fixed + 4) & (~(uintptr_t)7));
    }

    uintptr_t *addr_field = buf_fixed - sizeof(uintptr_t);
    *addr_field = (uintptr_t)buf_origin;

    /* Cleared through the data bus: the S3's instruction alias is fetch-only
       (a store through it is StoreProhibited). */
    memset(buf_fixed, 0, size);
#if (WASM_MEM_DUAL_BUS_MIRROR != 0)
    if (prot & MMAP_PROT_EXEC) {
        return buf_fixed + MEM_DUAL_BUS_OFFSET;
    }
#endif
    return buf_fixed;
}

void *
os_mremap(void *old_addr, size_t old_size, size_t new_size)
{
    return os_mremap_slow(old_addr, old_size, new_size);
}

void
os_munmap(void *addr, size_t size)
{
    char *ptr = (char *)addr;

#if (WASM_MEM_DUAL_BUS_MIRROR != 0)
    if (in_ibus_ext(ptr)) {
        ptr -= MEM_DUAL_BUS_OFFSET;
    }
#endif
    // We don't need special handling of the executable allocations
    // here, free() of esp-idf handles it properly
    return os_free(ptr);
}

int
os_mprotect(void *addr, size_t size, int prot)
{
    return 0;
}

void
os_dcache_flush()
{
    /* Nothing to do here: the loader calls os_icache_flush over the text
       after copying it and again once its relocations are applied, and that
       sync is scoped to the text's own range. A whole-cache write-back would
       need the instruction cache disabled, which faults the other core if it
       is executing from flash or PSRAM at that moment -- and on a console it
       always is. */
}

/* One cache line on every target this file syncs (the S3's 32 B and the P4's
   64 B lines both divide it), so an aligned range is aligned for both. */
#define CACHE_SYNC_ALIGN 64

void
os_icache_flush(void *start, size_t len)
{
#if WASM_ESPIDF_EXEC_IN_PSRAM != 0
    /* The text was written through the data cache. Write that range back
       (C2M) and drop whatever the instruction cache holds for it (M2C, INST).
       Both are range operations under IDF's cross-core cache lock, so they
       are safe while the other core runs; neither disables a cache. */
    if (!start || !len) {
        return;
    }
    uintptr_t ibus = (uintptr_t)start & ~(uintptr_t)(CACHE_SYNC_ALIGN - 1);
    size_t n = (((uintptr_t)start + len + CACHE_SYNC_ALIGN - 1)
                & ~(uintptr_t)(CACHE_SYNC_ALIGN - 1))
               - ibus;
#if (WASM_MEM_DUAL_BUS_MIRROR != 0)
    /* The bus aliases differ by a page-aligned constant, so the data range
       aligns exactly as the instruction one does. */
    uintptr_t dbus =
        (uintptr_t)os_get_dbus_mirror(start) - ((uintptr_t)start - ibus);
#else
    uintptr_t dbus = ibus;
#endif
    if (esp_cache_msync((void *)dbus, n, ESP_CACHE_MSYNC_FLAG_DIR_C2M)
            != ESP_OK
        || esp_cache_msync((void *)ibus, n,
                           ESP_CACHE_MSYNC_FLAG_DIR_M2C
                               | ESP_CACHE_MSYNC_FLAG_TYPE_INST)
               != ESP_OK) {
        os_printf("WAMR: cache sync failed for text at %p (+%u)\n", start,
                  (unsigned)len);
    }
#if CONFIG_IDF_TARGET_ARCH_RISCV
    __asm__ volatile("fence.i" ::: "memory");
#endif
#else
    (void)start;
    (void)len;
#endif
}

#if (WASM_MEM_DUAL_BUS_MIRROR != 0)
/* One flash partition mapped twice: ESP_PARTITION_MMAP_DATA for the loader's
 * reads, ESP_PARTITION_MMAP_INST for execution. */
static const char *s_xip_dbus, *s_xip_ibus;
static size_t s_xip_size;

void
os_register_xip_window(const void *ibus, const void *dbus, size_t size)
{
    s_xip_ibus = ibus;
    s_xip_dbus = dbus;
    s_xip_size = size;
}

void *
os_get_ibus_mirror(void *dbus)
{
    const char *p = dbus;
    if (s_xip_size && p >= s_xip_dbus && p < s_xip_dbus + s_xip_size) {
        return (void *)(s_xip_ibus + (p - s_xip_dbus));
    }
    if ((uint32_t)p >= SOC_EXTRAM_DATA_LOW && (uint32_t)p < SOC_EXTRAM_DATA_HIGH) {
        return (void *)(p + MEM_DUAL_BUS_OFFSET);
    }
    return dbus;
}

void *
os_get_dbus_mirror(void *ibus)
{
    const char *p = ibus;
    if (s_xip_size && p >= s_xip_ibus && p < s_xip_ibus + s_xip_size) {
        return (void *)(s_xip_dbus + (p - s_xip_ibus));
    }
    if (in_ibus_ext(ibus)) {
        return (void *)((char *)ibus - MEM_DUAL_BUS_OFFSET);
    }
    else {
        return ibus;
    }
}
#endif
