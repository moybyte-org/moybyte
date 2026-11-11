"""The vendored ESP-IDF os_mmap keeps every cache line it syncs to itself.

On the ESP32-P4, os_icache_flush's invalidate drops the data cache's copy of
every line it touches, so a line a mapping shared with another heap block
would lose a write another task made there. The fork's os_mmap starts a
mapping on MMAP_ALIGN and rounds its size up to it, and os_icache_flush syncs
in lines of that size (native/moy_wasm/README.md, "The cache sync").

This compiles the vendored espidf_memmap.c on the host as the P4 builds it
(text in PSRAM, no dual-bus mirror) over a stub heap that hands out blocks at
every 4-byte offset, and a stub esp_cache_msync that records its ranges.
"""

import os
import subprocess

import pytest

from runtime import native_build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMMAP = os.path.join(ROOT, "native", "moy_wasm", "wamr", "core", "shared",
                      "platform", "esp-idf", "espidf_memmap.c")

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

VMCORE_H = r"""
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t uint32;
typedef int os_file_handle;
#define MMAP_PROT_NONE 0
#define MMAP_PROT_READ 1
#define MMAP_PROT_WRITE 2
#define MMAP_PROT_EXEC 4
#define MALLOC_CAP_EXEC (1 << 0)
#define MALLOC_CAP_8BIT (1 << 2)
#define MALLOC_CAP_SPIRAM (1 << 10)
#define WASM_ESPIDF_PSRAM_THRESHOLD 1024
#define CONFIG_SPIRAM 1
#define os_printf printf
void *heap_caps_malloc(size_t size, uint32_t caps);
void os_free(void *ptr);
void *os_mmap(void *hint, size_t size, int prot, int flags, os_file_handle f);
void os_munmap(void *addr, size_t size);
void *os_mremap(void *old_addr, size_t old_size, size_t new_size);
void os_icache_flush(void *start, size_t len);
static inline void *
os_mremap_slow(void *old_addr, size_t old_size, size_t new_size)
{
    void *p = os_mmap(NULL, new_size, MMAP_PROT_READ | MMAP_PROT_WRITE, 0, -1);
    if (!p)
        return NULL;
    memcpy(p, old_addr, new_size < old_size ? new_size : old_size);
    os_munmap(old_addr, old_size);
    return p;
}
"""

ESP_CACHE_H = r"""
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M (1 << 0)
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C (1 << 1)
#define ESP_CACHE_MSYNC_FLAG_TYPE_INST (1 << 4)
esp_err_t esp_cache_msync(void *addr, size_t size, int flags);
"""

HARNESS = r"""
#include <stdlib.h>
#include "platform_api_vmcore.h"
#include "esp_cache.h"

/* The heap: a block at a chosen 4-byte offset into an over-allocation, so
   every head alignment a TLSF heap can hand back is tried. */
static size_t shift;
static struct { char *raw, *block; size_t len; } blocks[8];
static uintptr_t sync_lo[4], sync_hi[4];
static int syncs;
static int bad;

void *heap_caps_malloc(size_t size, uint32_t caps)
{
    (void)caps;
    for (int i = 0; i < 8; i++) {
        if (!blocks[i].raw) {
            blocks[i].raw = malloc(size + 64);
            blocks[i].block = blocks[i].raw + shift;
            blocks[i].len = size;
            return blocks[i].block;
        }
    }
    return NULL;
}

void os_free(void *ptr)
{
    char *origin = (char *)*(uintptr_t *)((char *)ptr - sizeof(uintptr_t));
    for (int i = 0; i < 8; i++) {
        if (blocks[i].block == origin) {
            free(blocks[i].raw);
            blocks[i].raw = blocks[i].block = NULL;
            return;
        }
    }
    printf("free of a block the heap never gave: %p\n", ptr);
    bad++;
}

esp_err_t esp_cache_msync(void *addr, size_t size, int flags)
{
    (void)flags;
    if (syncs < 4) {
        sync_lo[syncs] = (uintptr_t)addr;
        sync_hi[syncs] = (uintptr_t)addr + size;
    }
    syncs++;
    return ESP_OK;
}

static size_t span_of(size_t size) { return (size + 63) & ~(size_t)63; }

static int block_of(char *p)
{
    for (int i = 0; i < 8; i++)
        if (blocks[i].raw && p >= blocks[i].block
            && p < blocks[i].block + blocks[i].len)
            return i;
    return -1;
}

/* 1 when the mapping and its lines lie in one block the heap gave. */
static int check(const char *what, char *p, size_t size, int cleared)
{
    size_t span = span_of(size);
    int was = bad;
    int b = block_of(p);
    if (!p || b < 0) {
        printf("%s %zu: no mapping\n", what, size);
        bad++;
        return 0;
    }
    if ((uintptr_t)p % 64) {
        printf("%s %zu: starts at %p\n", what, size, (void *)p);
        bad++;
    }
    if (p + span > blocks[b].block + blocks[b].len) {
        printf("%s %zu shift %zu: its last line runs %td bytes past its block\n",
               what, size, shift, (p + span) - (blocks[b].block + blocks[b].len));
        bad++;
    }
    for (size_t i = 0; cleared && i < span; i++)
        if (p[i]) {
            printf("%s %zu: byte %zu not cleared\n", what, size, i);
            bad++;
            break;
        }
    return bad == was;
}

static void check_syncs(char *p, size_t size, size_t off, size_t len)
{
    syncs = 0;
    os_icache_flush(p + off, len);
    if (syncs != 2) {
        printf("flush %zu+%zu: %d syncs\n", off, len, syncs);
        bad++;
        return;
    }
    for (int i = 0; i < 2; i++)
        if (sync_lo[i] < (uintptr_t)p
            || sync_hi[i] > (uintptr_t)p + span_of(size)) {
            printf("map %zu shift %zu: flush %zu+%zu syncs [%#lx, %#lx), "
                   "outside the mapping's lines [%p, +%zu)\n",
                   size, shift, off, len, (unsigned long)sync_lo[i],
                   (unsigned long)sync_hi[i], (void *)p, span_of(size));
            bad++;
        }
}

int main(void)
{
    static const size_t sizes[] = { 1, 4, 8, 56, 60, 63, 64, 65, 100, 127,
                                    128, 129, 1000, 1023, 1024, 1025, 4095,
                                    4096, 4097, 65536 + 12 };
    for (shift = 0; shift < 64; shift += 4) {
        for (size_t s = 0; s < sizeof sizes / sizeof *sizes; s++) {
            size_t size = sizes[s];
            for (int exec = 0; exec < 2; exec++) {
                int prot = MMAP_PROT_READ | MMAP_PROT_WRITE
                           | (exec ? MMAP_PROT_EXEC : 0);
                char *p = os_mmap(NULL, size, prot, 0, -1);
                if (!check("map", p, size, 1)) {
                    if (p)
                        os_munmap(p, size);
                    continue;
                }
                memset(p, 0xa5, span_of(size));
                check_syncs(p, size, 0, size);
                check_syncs(p, size, size / 2, size - size / 2);
                check_syncs(p, size, size - 1, 1);
                os_munmap(p, size);
            }
            char *p = os_mmap(NULL, size, MMAP_PROT_READ | MMAP_PROT_WRITE, 0, -1);
            memset(p, 0x5a, size);
            char *q = os_mremap(p, size, size * 2 + 3);
            check("remap", q, size * 2 + 3, 0);
            for (size_t i = 0; q && i < size; i++)
                if ((unsigned char)q[i] != 0x5a) {
                    printf("remap %zu: byte %zu not carried\n", size, i);
                    bad++;
                    break;
                }
            if (q)
                os_munmap(q, size * 2 + 3);
        }
    }
    for (int i = 0; i < 8; i++)
        if (blocks[i].raw) {
            printf("a block was never freed\n");
            bad++;
        }
    if (!bad)
        printf("ok\n");
    return bad != 0;
}
"""


def test_a_mapping_shares_no_cache_line_with_another_block(tmp_path):
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "platform_api_vmcore.h").write_text(VMCORE_H)
    (inc / "platform_api_extension.h").write_text("")
    (inc / "esp_cache.h").write_text(ESP_CACHE_H)
    (tmp_path / "harness.c").write_text(HARNESS)
    exe = tmp_path / "harness"
    built = subprocess.run([native_build.cc(), "-std=gnu11", "-O1", "-w",
                            "-DWASM_ESPIDF_EXEC_IN_PSRAM=1",
                            "-DWASM_MEM_DUAL_BUS_MIRROR=0", "-I", str(inc),
                            "-o", str(exe), str(tmp_path / "harness.c"), MEMMAP],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-2000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout[-2000:]
