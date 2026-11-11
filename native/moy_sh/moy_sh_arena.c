// moy_sh_arena: the session's region (moy_sh_arena.h).
//
// A block is a header word (its size, bit 0 set while in use, bit 1 set
// while the block below it is in use) and its payload; a free block also
// carries its free-list links after the header and its size in its last
// word, so a freed block finds a free neighbour below it. Offsets, not
// pointers, link the list, and the arena's own record sits at the region's
// base, so the region restored from a snapshot is a consistent allocator.

#include <stdint.h>
#include <string.h>

#include "moy_sh_arena.h"

#if defined(__SANITIZE_ADDRESS__)
#define SH_ASAN 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define SH_ASAN 1
#endif
#endif

#ifdef SH_ASAN
#include <sanitizer/asan_interface.h>
#define POISON(p, n) ASAN_POISON_MEMORY_REGION((p), (n))
#define UNPOISON(p, n) ASAN_UNPOISON_MEMORY_REGION((p), (n))
#define NO_ASAN __attribute__((no_sanitize_address))
#else
#define POISON(p, n) ((void)(p), (void)(n))
#define UNPOISON(p, n) ((void)(p), (void)(n))
#define NO_ASAN
#endif

#define ALIGN 16u
#define USED 1u
#define PREV_USED 2u
#define FLAGS 3u
#define HDR sizeof(size_t)
#define MIN_BLOCK 32u               // a free block's header, links and footer

struct moy_sh_arena {
    size_t size;                    // the region's bytes
    size_t top;                     // the offset where the never-used top starts
    size_t free_head;               // offset of the first free block, 0 none
    size_t pad;
};

typedef struct {
    size_t next, prev;              // offsets, 0 none
} links_t;

#define BASE(a) ((unsigned char *)(a))
#define AT(a, off) (BASE(a) + (off))
#define FIRST (((sizeof(struct moy_sh_arena) + HDR + ALIGN - 1) / ALIGN) * ALIGN - HDR)

static size_t *hdr(moy_sh_arena_t *a, size_t off) {
    return (size_t *)AT(a, off);
}

static size_t bsize(size_t h) {
    return h & ~(size_t)FLAGS;
}

static links_t *links(moy_sh_arena_t *a, size_t off) {
    return (links_t *)AT(a, off + HDR);
}

static void footer(moy_sh_arena_t *a, size_t off, size_t n) {
    *(size_t *)AT(a, off + n - HDR) = n;
}

static void list_add(moy_sh_arena_t *a, size_t off) {
    links_t *l = links(a, off);
    l->prev = 0;
    l->next = a->free_head;
    if (a->free_head != 0) {
        links(a, a->free_head)->prev = off;
    }
    a->free_head = off;
}

static void list_del(moy_sh_arena_t *a, size_t off) {
    links_t *l = links(a, off);
    if (l->prev != 0) {
        links(a, l->prev)->next = l->next;
    } else {
        a->free_head = l->next;
    }
    if (l->next != 0) {
        links(a, l->next)->prev = l->prev;
    }
}

static size_t round_up(size_t n) {
    size_t need = n + HDR;
    if (need < MIN_BLOCK) {
        need = MIN_BLOCK;
    }
    return (need + ALIGN - 1) & ~(size_t)(ALIGN - 1);
}

moy_sh_arena_t *moy_sh_arena_init(void *mem, size_t n) {
    moy_sh_arena_t *a = mem;
    if (mem == NULL || ((uintptr_t)mem & (ALIGN - 1)) != 0 || n < FIRST + 4096) {
        return NULL;
    }
    memset(a, 0, sizeof *a);
    a->size = n & ~(size_t)(ALIGN - 1);
    a->top = FIRST;
    a->free_head = 0;
    POISON(AT(a, FIRST), a->size - FIRST);
    return a;
}

// Mark the block at `off` used and flag the block above it.
static void set_used(moy_sh_arena_t *a, size_t off, size_t n, size_t prev_used) {
    *hdr(a, off) = n | USED | prev_used;
    size_t up = off + n;
    if (up < a->top) {
        *hdr(a, up) |= PREV_USED;
    }
}

void *moy_sh_arena_alloc(moy_sh_arena_t *a, size_t n) {
    size_t want = round_up(n);
    if (want < n) {
        return NULL;
    }
    for (size_t off = a->free_head; off != 0; off = links(a, off)->next) {
        size_t h = *hdr(a, off);
        size_t have = bsize(h);
        if (have < want) {
            continue;
        }
        list_del(a, off);
        UNPOISON(AT(a, off), have);
        if (have - want >= MIN_BLOCK) {
            size_t rest = off + want;
            *hdr(a, rest) = (have - want) | PREV_USED;
            footer(a, rest, have - want);
            list_add(a, rest);
            POISON(AT(a, rest + HDR + sizeof(links_t)), have - want - HDR - sizeof(links_t) - HDR);
            *hdr(a, off) = want | USED | (h & PREV_USED);
        } else {
            set_used(a, off, have, h & PREV_USED);
            want = have;
        }
        UNPOISON(AT(a, off + HDR), want - HDR);
        POISON(AT(a, off + HDR + n), want - HDR - n);
        return AT(a, off + HDR);
    }
    if (want > a->size - a->top) {
        return NULL;
    }
    size_t off = a->top;
    // the block below the top is in use unless it is a free block whose
    // footer sits just under it (a free block never touches the top: freeing
    // the highest block lowers the top instead)
    size_t prev_used = PREV_USED;
    UNPOISON(AT(a, off), HDR);
    a->top += want;
    *hdr(a, off) = want | USED | prev_used;
    UNPOISON(AT(a, off + HDR), n);
    return AT(a, off + HDR);
}

void moy_sh_arena_free(moy_sh_arena_t *a, void *p) {
    if (p == NULL) {
        return;
    }
    size_t off = (size_t)((unsigned char *)p - BASE(a)) - HDR;
    size_t h = *hdr(a, off);
    size_t n = bsize(h);
    UNPOISON(AT(a, off + HDR), n - HDR);
    // merge the free block above
    size_t up = off + n;
    if (up < a->top && !(*hdr(a, up) & USED)) {
        list_del(a, up);
        n += bsize(*hdr(a, up));
    }
    // and the free block below
    if (!(h & PREV_USED)) {
        size_t below = *(size_t *)AT(a, off - HDR);
        size_t boff = off - below;
        list_del(a, boff);
        off = boff;
        n += below;
        h = *hdr(a, off);
    }
    if (off + n == a->top) {
        a->top = off;
        POISON(AT(a, off), a->size - off);
        return;
    }
    *hdr(a, off) = n | (h & PREV_USED);
    footer(a, off, n);
    *hdr(a, off + n) &= ~(size_t)PREV_USED;
    list_add(a, off);
    POISON(AT(a, off + HDR + sizeof(links_t)), n - HDR - sizeof(links_t) - HDR);
}

void *moy_sh_arena_realloc(moy_sh_arena_t *a, void *p, size_t n) {
    if (p == NULL) {
        return moy_sh_arena_alloc(a, n);
    }
    size_t off = (size_t)((unsigned char *)p - BASE(a)) - HDR;
    size_t have = bsize(*hdr(a, off)) - HDR;
    if (n <= have) {
        UNPOISON(p, n);
        POISON((unsigned char *)p + n, have - n);
        return p;
    }
    void *q = moy_sh_arena_alloc(a, n);
    if (q == NULL) {
        return NULL;
    }
    UNPOISON(p, have);
    memcpy(q, p, have);
    moy_sh_arena_free(a, p);
    return q;
}

size_t moy_sh_arena_used(const moy_sh_arena_t *a) {
    return a->top;
}

size_t moy_sh_arena_free_bytes(const moy_sh_arena_t *a) {
    moy_sh_arena_t *m = (moy_sh_arena_t *)a;
    size_t n = a->size - a->top;
    for (size_t off = a->free_head; off != 0; off = links(m, off)->next) {
        n += bsize(*hdr(m, off));
    }
    return n;
}

static NO_ASAN void raw_copy(unsigned char *to, const unsigned char *from, size_t n) {
    for (size_t i = 0; i < n; i++) {
        to[i] = from[i];
    }
}

void moy_sh_arena_save(const moy_sh_arena_t *a, void *to) {
    raw_copy(to, (const unsigned char *)a, a->top);
}

// After a restore the poison follows the restored map: every block walked
// from the first, the free ones (past their links) and the top poisoned.
static void repoison(moy_sh_arena_t *a) {
#ifdef SH_ASAN
    UNPOISON(AT(a, FIRST), a->size - FIRST);
    size_t off = FIRST;
    while (off < a->top) {
        size_t h = *hdr(a, off);
        size_t n = bsize(h);
        if (!(h & USED)) {
            POISON(AT(a, off + HDR + sizeof(links_t)), n - HDR - sizeof(links_t) - HDR);
        }
        off += n;
    }
    POISON(AT(a, a->top), a->size - a->top);
#else
    (void)a;
#endif
}

void moy_sh_arena_restore(moy_sh_arena_t *a, const void *from, size_t n) {
    UNPOISON(BASE(a), a->size);
    raw_copy((unsigned char *)a, from, n);
    repoison(a);
}
