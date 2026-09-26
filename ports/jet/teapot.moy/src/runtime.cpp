// The C library edges a compiled cart owns: the heap, and the calls wasi-libc's
// stdio makes to the host. The module links wasi-libc and libc++ for the rest
// (the containers, the string and number routines Jet's OBJ loader calls,
// libm) and imports nothing but "moy".
//
// The heap is the linear memory above the stack and the static data: from
// __heap_base to the end of the memory the manifest declares, which never
// grows. First fit over an address-ordered free list, neighbours merged on
// free; every block 16-aligned with a 16-byte header. Jet allocates its
// meshes and queues while the scene loads and its per-frame vectors keep their
// capacity, so the list stays short.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

extern "C" {
extern unsigned char __heap_base;

namespace {

struct Block {
    size_t size;    // the whole block, header included
    size_t used;
    Block *next;    // free blocks only
    size_t pad;
};

const size_t HEADER = sizeof(Block);
const size_t ALIGN = 16;

Block *free_list = nullptr;
uintptr_t brk_ptr = 0, heap_end = 0;
size_t peak = 0;

size_t round_up(size_t n) { return (n + ALIGN - 1) & ~(ALIGN - 1); }

void start()
{
    if (brk_ptr) return;
    brk_ptr = round_up((uintptr_t)&__heap_base);
    heap_end = (uintptr_t)__builtin_wasm_memory_size(0) * 65536u;
}

Block *header(void *p) { return (Block *)((unsigned char *)p - HEADER); }

void *payload(Block *b) { return (unsigned char *)b + HEADER; }

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
    if ((uintptr_t)b + b->size == brk_ptr) {
        Block **last = &free_list;
        while (*last != b) last = &(*last)->next;
        *last = nullptr;
        brk_ptr = (uintptr_t)b;
    }
}

}  // namespace

void *malloc(size_t n)
{
    start();
    if (n > heap_end) return nullptr;
    size_t need = round_up(n ? n : 1) + HEADER;
    for (Block **at = &free_list; *at; at = &(*at)->next) {
        Block *b = *at;
        if (b->size < need) continue;
        if (b->size - need >= HEADER + ALIGN) {
            Block *rest = (Block *)((unsigned char *)b + need);
            rest->size = b->size - need;
            rest->used = 0;
            rest->next = b->next;
            *at = rest;
            b->size = need;
        } else {
            *at = b->next;
        }
        b->used = 1;
        return payload(b);
    }
    if (heap_end - brk_ptr < need) return nullptr;
    Block *b = (Block *)brk_ptr;
    b->size = need;
    b->used = 1;
    brk_ptr += need;
    size_t in_use = brk_ptr - round_up((uintptr_t)&__heap_base);
    if (in_use > peak) peak = in_use;
    return payload(b);
}

void free(void *p)
{
    if (p) insert(header(p));
}

void *calloc(size_t n, size_t size)
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

size_t cart_heap_peak(void) { return peak; }
size_t cart_heap_size(void)
{
    start();
    return heap_end - round_up((uintptr_t)&__heap_base);
}
}
