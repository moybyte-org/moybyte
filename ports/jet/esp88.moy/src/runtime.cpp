// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Nikola Jovicic
//
// The C library edges a compiled cart owns: the heap, and the calls wasi-libc's
// stdio makes to the host. The module links wasi-libc and libc++ for the rest
// (the containers, the string and number routines Jet's OBJ loader calls,
// libm) and imports nothing but "moy".
//
// The heap is the linear memory above the stack and the static data: from
// __heap_base to __heap_end, the end of the memory the manifest declares,
// which never grows. Both are the linker's, never `memory.size`: a module
// that asks for its memory's size anywhere is one the AOT compiler assumes
// can grow, and then every function reloads linear memory's base and bound
// after every call it makes.
//
// Best fit over an address-ordered free list, neighbours merged on free, every
// block 16-aligned with a 16-byte header -- and two-ended: a block of LARGE
// bytes or more comes from the top of the heap down, a smaller one from the
// bottom up. Jet's per-frame queues are large and outlive every scene, and
// ESP 88 frees its city and rebuilds it at each cut, mostly in small blocks;
// in one run of blocks the queues' growth landed above a city and the next,
// larger city built over and past it, so the heap's high-water mark climbed
// a queue's size a cut (to 1.4 MB playing on from the boulevard, against the
// 1.24 MB the film ever holds at once). Kept apart, the cities churn below
// and the queues settle above. The list is walked when a scene loads, not in
// a frame: Jet's per-frame vectors keep their capacity.
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <__verbose_abort>

#include "moy_cart.h"

extern "C" {
extern unsigned char __heap_base;
extern unsigned char __heap_end;

namespace {

struct Block {
    size_t size;    // the whole block, header included
    size_t used;
    Block *next;    // free blocks only
    size_t pad;
};

const size_t HEADER = sizeof(Block);
const size_t ALIGN = 16;
const size_t LARGE = 4096;

Block *free_list = nullptr;
// The small blocks' end and the large blocks' start: [low, high) is unused.
uintptr_t heap_start = 0, low = 0, high = 0, heap_end = 0;
size_t peak = 0;

size_t round_up(size_t n) { return (n + ALIGN - 1) & ~(ALIGN - 1); }

void start()
{
    if (heap_end) return;
    heap_start = low = round_up((uintptr_t)&__heap_base);
    heap_end = high = (uintptr_t)&__heap_end & ~(ALIGN - 1);
}

Block *header(void *p) { return (Block *)((unsigned char *)p - HEADER); }

void *payload(Block *b) { return (unsigned char *)b + HEADER; }

void unlink(Block *b)
{
    Block **at = &free_list;
    while (*at != b) at = &(*at)->next;
    *at = b->next;
}

void insert(Block *b)
{
    b->used = 0;
    Block **at = &free_list;
    while (*at && *at < b) at = &(*at)->next;
    b->next = *at;
    *at = b;
    if (b->next && (uintptr_t)b + b->size == (uintptr_t)b->next) {
        b->size += b->next->size;
        b->next = b->next->next;
    }
    if (at != &free_list) {
        Block *prev = (Block *)((unsigned char *)at - offsetof(Block, next));
        if ((uintptr_t)prev + prev->size == (uintptr_t)b) {
            prev->size += b->size;
            prev->next = b->next;
            b = prev;
        }
    }
    if ((uintptr_t)b + b->size == low) {
        unlink(b);
        low = (uintptr_t)b;
    } else if ((uintptr_t)b == high) {
        unlink(b);
        high += b->size;
    }
}

}  // namespace

void *malloc(size_t n)
{
    start();
    if (n > heap_end) return nullptr;
    const size_t need = round_up(n ? n : 1) + HEADER;
    const bool large = need >= LARGE;
    Block **best = nullptr;
    for (Block **at = &free_list; *at; at = &(*at)->next) {
        if ((*at)->size < need || (best && (*best)->size <= (*at)->size)) continue;
        best = at;
        if ((*at)->size == need) break;
    }
    Block *b;
    if (best) {
        b = *best;
        if (b->size - need < HEADER + ALIGN) {
            *best = b->next;
        } else if (large) {
            b->size -= need;
            b = (Block *)((unsigned char *)b + b->size);
            b->size = need;
        } else {
            Block *rest = (Block *)((unsigned char *)b + need);
            rest->size = b->size - need;
            rest->used = 0;
            rest->next = b->next;
            *best = rest;
            b->size = need;
        }
    } else {
        if (high - low < need) return nullptr;
        if (large) {
            high -= need;
            b = (Block *)high;
        } else {
            b = (Block *)low;
            low += need;
        }
        b->size = need;
        const size_t in_use = (low - heap_start) + (heap_end - high);
        if (in_use > peak) peak = in_use;
    }
    b->used = 1;
    return payload(b);
}

void free(void *p)
{
    if (p) insert(header(p));
}

// Not inlined: clang 18's dead-store pass crashes on this malloc inlined
// here beside the memset.
__attribute__((noinline)) void *calloc(size_t n, size_t size)
{
    if (size && n > (size_t)-1 / size) return nullptr;
    void *p = malloc(n * size);
    if (p) memset(p, 0, n * size);
    return p;
}

void *realloc(void *p, size_t n)
{
    if (!p) return malloc(n);
    if (!n) {
        free(p);
        return nullptr;
    }
    size_t have = header(p)->size - HEADER;
    if (have >= n) return p;
    void *q = malloc(n);
    if (q) {
        memcpy(q, p, have);
        free(p);
    }
    return q;
}

void *aligned_alloc(size_t align, size_t n)
{
    return align <= ALIGN ? malloc(n) : nullptr;
}

int posix_memalign(void **out, size_t align, size_t n)
{
    void *p = aligned_alloc(align, n);
    if (!p) return 12;  // ENOMEM
    *out = p;
    return 0;
}

// wasi-libc's own routines allocate through these names.
void *__libc_malloc(size_t n) { return malloc(n); }
void *__libc_calloc(size_t n, size_t size) { return calloc(n, size); }
void __libc_free(void *p) { free(p); }
size_t malloc_usable_size(void *p) { return p ? header(p)->size - HEADER : 0; }

// stdout and stderr: what Jet prints and what the C++ library writes before it
// aborts. A cart has no terminal, so a write succeeds and goes nowhere, and
// the streams are not terminals and never seek or close. Defining these is what
// keeps them from being imported from "wasi_snapshot_preview1".
struct Iovec {
    uint32_t buf, len;
};

int32_t __imported_wasi_snapshot_preview1_fd_write(int32_t fd, int32_t iovs, int32_t n,
                                                    int32_t nwritten)
{
    (void)fd;
    const Iovec *v = (const Iovec *)(uintptr_t)iovs;
    uint32_t total = 0;
    for (int32_t i = 0; i < n; i++) total += v[i].len;
    *(uint32_t *)(uintptr_t)nwritten = total;
    return 0;
}

const int32_t WASI_EBADF = 8;

int32_t __imported_wasi_snapshot_preview1_fd_seek(int32_t fd, int64_t offset, int32_t whence,
                                                   int32_t out)
{
    (void)fd, (void)offset, (void)whence, (void)out;
    return WASI_EBADF;
}

int32_t __imported_wasi_snapshot_preview1_fd_close(int32_t fd)
{
    (void)fd;
    return WASI_EBADF;
}

int32_t __imported_wasi_snapshot_preview1_fd_fdstat_get(int32_t fd, int32_t out)
{
    (void)fd, (void)out;
    return WASI_EBADF;
}

// par's items: the cart's one _par export runs the job par was handed, on a
// stack of its own for each item. The heap above is not for them: an item
// allocates nothing.
struct CartJob {
    void (*fn)(int i, void *ctx);
    void *ctx;
};

const int ITEMS = 4;
const size_t ITEM_STACK = 2 * 1024;
alignas(16) unsigned char item_stacks[ITEMS][ITEM_STACK];

__attribute__((export_name("_par"))) void cart_item(int32_t i, int32_t arg)
{
    const CartJob *job = (const CartJob *)(uintptr_t)arg;
    job->fn(i, job->ctx);
}

void cart_par(int n, void (*fn)(int i, void *ctx), void *ctx)
{
    CartJob job = {fn, ctx};
    if (n > ITEMS) __builtin_trap();
    moy_par(n, (int32_t)(uintptr_t)&job, item_stacks, (int32_t)ITEM_STACK);
}

// The most any item's stack held, in bytes: the untouched tail of each is
// still the zeros it started as.
size_t cart_item_stack_peak(void)
{
    size_t most = 0;
    for (int k = 0; k < ITEMS; k++) {
        size_t low = 0;
        while (low < ITEM_STACK && !item_stacks[k][low]) low++;
        if (ITEM_STACK - low > most) most = ITEM_STACK - low;
    }
    return most;
}

size_t cart_heap_peak(void) { return peak; }
size_t cart_heap_size(void)
{
    start();
    return heap_end - heap_start;
}

// A line logged to stderr, and the messages the C++ libraries format before
// they abort (exceptions are off, so a container that cannot allocate ends
// there): a cart has no terminal, and formatting any of them links printf's
// whole machinery into the module. An abort still traps, as the libraries'
// do.
int fprintf(FILE *stream, const char *format, ...)
{
    (void)stream, (void)format;
    return 0;
}

void abort_message(const char *format, ...)
{
    (void)format;
    __builtin_trap();
}
}

void std::__libcpp_verbose_abort(const char *format, ...)
{
    (void)format;
    __builtin_trap();
}
