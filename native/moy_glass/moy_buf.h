// The glass's off-heap buffers and their owners (docs/kernel_survival_2026-10.md
// §3.1, §3.2).
//
// Every buffer a canvas draws into or reads from outside the VM's heap is a row
// of kind BUF: its bytes, its ROLE (what it is for), its ORIGIN (where its
// bytes came from, which decides what giving it back does), the OWNER it is on
// loan to and a HOLDER tag the client sets (the canvas or image it backs).
// Owners are rows of kind OWNER, minted by whatever starts a lifetime (a cart
// run, the wallpaper, a canvas's map cache, the Paint document) and ended when
// it ends: ending one returns every loan, and the generation in its handle
// keeps a recycled lifetime from returning another's.
//
// THE POOL is the set of free BUF rows, keyed by byte size: a layer a dead
// owner gave back waits there for the next layer of its size, up to the pool's
// bound in bytes; above it a released buffer is freed. When the allocator
// refuses, the pool is evicted and the allocation tried once more; NOMEM after
// that is the binding's to answer (a gc-heap buffer, recorded as a HEAP row,
// which the gate expects never to see).
//
// What the kernel's share counts is the CLASS of a row's owner: rows on loan to
// no owner (the system canvas, the paint buffers, the bar's strip cache) and
// the pool's free rows are the kernel's; rows on loan to a cart-class owner are
// cart memory, counted against what a cart has and gone with the run.
//
// Every byte of the tables comes from the moy_htab_mem_t the glass is made
// with (PSRAM on a board), every pixel byte from its moy_glass_px_t.

#ifndef MOY_BUF_H
#define MOY_BUF_H

#include <stddef.h>
#include <stdint.h>

#include "moy_htab.h"

enum {
    MOY_GLASS_OK = MOY_HTAB_OK,
    MOY_GLASS_STALE = MOY_HTAB_STALE,
    MOY_GLASS_FULL = MOY_HTAB_FULL,
    MOY_GLASS_NOMEM = MOY_HTAB_NOMEM,
    MOY_GLASS_BAD = 4,          // an argument no row could take
};

// What a buffer is for.
enum {
    MOY_ROLE_LAYER = 1,         // a canvas's pixels: a layer, a window, a run canvas
    MOY_ROLE_BAKE = 2,          // an image's RGB565 bake
    MOY_ROLE_SCRATCH = 3,       // the view crop, the fold's snapshot
    MOY_ROLE_CACHE = 4,         // the bar's strip, the map cache
    MOY_ROLE_PAINT = 5,         // a compositor's own paint buffers
    MOY_ROLE_POOL = 6,          // a free row in the pool
};

// Where a buffer's bytes came from.
enum {
    MOY_ORIGIN_HEAP = 0,        // the VM's heap: the binding holds the bytes
    MOY_ORIGIN_POOL = 1,        // a pooled buffer, re-lent
    MOY_ORIGIN_ALLOC = 2,       // the pixel allocator, fresh
};

// Whose memory a row is.
enum {
    MOY_CLASS_KERNEL = 0,
    MOY_CLASS_CART = 1,
};

#define MOY_GLASS_ROWS 256u         // each of the glass's kinded tables
#define MOY_OWNER_TAG 15u

typedef struct {
    // n zeroed bytes, 64-byte aligned and DMA-reachable, or NULL.
    void *(*alloc)(size_t n);
    void (*release)(void *p, size_t n);
    // The pixel heap's free total and its largest free block, for the census.
    size_t (*free_total)(void);
    size_t (*largest)(void);
} moy_glass_px_t;

typedef struct {
    uint8_t *px;                // NULL for a HEAP row
    uint32_t nbytes;
    uint32_t owner;             // OWNER handle, or 0: the kernel's
    uint32_t holder;            // the client's tag
    uint8_t role;
    uint8_t origin;
    uint8_t cls;                // MOY_CLASS_*: the owner's, fixed at the loan
    uint8_t pad;
} moy_buf_row_t;

typedef struct {
    char tag[MOY_OWNER_TAG + 1u];
    uint32_t loans;
    uint8_t cls;
} moy_owner_row_t;

// The counts the census and the gate read.
typedef struct {
    uint32_t rows;                      // live BUF rows, the pool's included
    uint32_t peak;                      // the most there have been at once
    uint32_t pool_rows;
    uint32_t pool_bytes;
    uint32_t pool_bound;
    uint32_t heap_rows;                 // HEAP-origin rows: the gate wants 0
    uint32_t heap_bytes;
    uint32_t kernel_bytes;              // kernel-class loans, the pool not counted
    uint32_t cart_bytes;
    uint32_t owners;
    uint32_t evictions;                 // NOMEM retries that freed the pool
    uint32_t px_free;                   // the pixel heap now
    uint32_t px_largest;
} moy_glass_stats_t;

// The glass's state, once per image: the tables are made at the first call.
// `pool_bound` is the pool's most bytes (MOY_GLASS_POOL_BYTES on a board).
int moy_glass_init(const moy_htab_mem_t *mem, const moy_glass_px_t *px,
                   uint32_t pool_bound);
// Free every buffer and every table (the host's teardown, the fuzzer's).
void moy_glass_deinit(void);
int moy_glass_ready(void);

void moy_glass_set_pool_bound(uint32_t bytes);  // evicts down to it
void moy_glass_evict(void);                     // frees every pool row
void moy_glass_stats(moy_glass_stats_t *out);

// A buffer of `nbytes` on loan to `owner` (0: the kernel's): the pool's free row
// of that size first, then the allocator, then -- after evicting the pool -- the
// allocator once more. OK, FULL, NOMEM, STALE (a dead owner) or BAD.
int moy_buf_new(uint32_t *h, size_t nbytes, uint8_t role, uint32_t owner);
// A HEAP row: bytes the binding holds on the VM's heap, counted.
int moy_buf_heap(uint32_t *h, size_t nbytes, uint8_t role, uint32_t owner);
// The row `h` names, or STALE.
int moy_buf_get(uint32_t h, moy_buf_row_t **row);
int moy_buf_set_holder(uint32_t h, uint32_t holder);
// Give the buffer back: a lent layer goes to the pool while the pool has room,
// everything else is freed. OK or STALE.
int moy_buf_release(uint32_t h);

int moy_owner_new(uint32_t *h, const char *tag, uint8_t cls);
int moy_owner_get(uint32_t h, moy_owner_row_t **row);
// Return every loan of `owner` whose role is in `roles` (a mask of 1 << role;
// 0 for every role). OK or STALE.
int moy_owner_reclaim(uint32_t h, uint32_t roles);
// Every loan returned, then the row. OK or STALE.
int moy_owner_end(uint32_t h);

// The BUF table's walk: live rows in slot order, for the census and the trace.
uint32_t moy_buf_slots(void);
uint32_t moy_buf_at(uint32_t slot);

#endif // MOY_BUF_H
