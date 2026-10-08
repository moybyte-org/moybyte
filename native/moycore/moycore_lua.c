// A Lua cart's VM over the console, with no MicroPython in it (moycore_lua.h
// has the contract; native/moycore/README.md the allocator's design).

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moycore_layers.h"
#include "moycore_lua.h"
#include "moycore_run.h"
#include "moycore_superset.h"

moycore_run moycore_RUN;
#define RUN moycore_RUN

#define HOOK(name, ...) moycore_lua_hooks.name(__VA_ARGS__)

// The board allocator, probed the way moy_lua probes it: present on an ESP-IDF
// build, absent on the host/unix/wasm ones, which then use plain realloc.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define MOYCORE_PSRAM 1
#endif
// Which REGION a pointer is in, for the allocator census. Its own probe and
// its own INCLUDE: the S3 gets esp_ptr_internal transitively through
// esp_heap_caps.h and the P4 does not, so a probe that tests for the header
// without including it compiles on one board and fails on the other with an
// implicit declaration. (It did.)
#if __has_include("esp_memory_utils.h")
#include "esp_memory_utils.h"
#define MOYCORE_PTR_REGION 1
#endif
#endif

// Internal-SRAM headroom the VM must leave for the WiFi/DMA pools. 48KB at
// boot, for the same reason moy_lua sizes it that way -- room for a WiFi stack
// that might start at any moment -- and a RUNTIME KNOB for the same reason
// moy_lua made it one: the #66 census showed that on the S3's 269KB internal
// heap a 48KB floor leaves celeste's Lua about 9KB, i.e. 97% PSRAM, which is
// precisely the measured-2x regime the SRAM-first allocator exists to avoid.
// run_desktop drops it to 24KB once everything with a boot-time internal claim
// has taken it.
//
// This was missing when moycore shipped, and silently: run_desktop lowered
// moy_lua's floor and moycore's stayed at 48KB, so moving a cart to the new
// runtime handed most of the SRAM-first win back. Nothing would have said so
// except the cart being slower.
#ifndef MOYCORE_SRAM_FLOOR
#define MOYCORE_SRAM_FLOOR (48 * 1024)
#endif
static size_t g_sram_floor = MOYCORE_SRAM_FLOOR;

// The census the floor is set from: live bytes per region, the peak, and how
// often the floor sent an allocation to PSRAM. Deliberately four numbers where
// moy_lua reports sixteen -- the size-class buckets were for CHOOSING the
// policy and the policy is chosen; these are for confirming it took.
//
// Kept on EVERY build, not just the board ones. Off-board there is one region
// and mc_region calls it region 1, so sram_live and sram_denied read zero --
// but g_live_total is what proves the small-object pool below gives every byte
// back at close, and the only place that check can actually RUN is the desktop
// MicroPython (tests/test_moycore_pool.py). A guard that compiles out on the
// only tier that runs it is not a guard.
static size_t g_live_r[2], g_live_total, g_peak;
static uint32_t g_sram_denied;

// What the RUN had (#211): the low-water mark of free internal SRAM while it
// ran, and whether the floor ever pushed it into the ~2x-slower PSRAM regime.
// Both are read where big_realloc already knows the free figure, so the
// accounting is one compare and NO extra syscall -- a per-frame sample would
// have cost a heap_caps_get_free_size the allocator does not otherwise need.
// The consequence is worth stating: the mark is sampled at the VM's big
// allocations, which is the moment the floor decision is actually made, and
// not between them.
//
// SIZE_MAX means "never sampled", which reports as None -- a run whose Lua
// never allocated big is not a run with zero headroom.
static size_t g_sram_free_min = SIZE_MAX;
static uint8_t g_psram_fallback;
#ifndef MOYCORE_PSRAM
// Off-board there is ONE region, so the split above cannot happen and the
// report is None. `sram_sim(bytes)` supplies the free figure the boards read
// from the heap, which is what makes the floor arithmetic and the fallback
// flag testable on the tier the tests run on (tests/test_moycore_pool.py).
// A TEST verb, like pool_check: it cannot exist in a firmware build.
static size_t g_sram_sim;
#endif

static inline int mc_region(const void *p)
{
#ifdef MOYCORE_PTR_REGION
    return esp_ptr_internal(p) ? 0 : 1;
#else
    (void)p;                     // no way to ask: report everything as PSRAM
    return 1;
#endif
}

static inline void census_add(const void *p, size_t n)
{
    g_live_r[mc_region(p)] += n;
    g_live_total += n;
    if (g_live_total > g_peak) g_peak = g_live_total;
}

// A freed block's bytes come off the region it was IN, so the caller reads the
// region while the pointer is still valid and passes it here. gcc 12's
// -Wuse-after-free rejects touching a pointer after realloc at all -- even for
// the address-range compare mc_region does, which never dereferences -- and it
// is right that the standard makes the value indeterminate either way.
static inline void census_sub_region(int region, size_t n)
{
    g_live_r[region] -= n;
    g_live_total -= n;
}

static inline void census_sub(const void *p, size_t n)
{
    census_sub_region(mc_region(p), n);
}

// The BIG half of the VM's allocator: the system heap, NOT MicroPython's gc
// heap. That is the point -- a cart's whole Lua world stays outside what MP
// sweeps, so cart churn cannot lengthen a shell collect.
//
// INTERNAL SRAM FIRST on the boards, which is not a preference but a measured
// requirement: moy_lua found the all-PSRAM version made the S3's whole _update
// about twice as slow, because the VM's hot working set (the Lua stack, the
// cart's TValue arrays) is latency-bound and the S3's PSRAM is a 120MHz OCT
// bus. Same 48KB headroom floor so the WiFi/DMA pools cannot be starved, and
// the same PSRAM fallback so a big cart still loads, just slower. Off-board
// (host, unix pin, wasm) this is plain realloc.
//
// UNCHANGED by the small-object pool below, and deliberately: the structures
// that measurement was about -- the Lua stack (one StackValue array, over a
// kilobyte for any real cart), a big table's node array, a Proto's code -- are
// all far above the pool's 256-byte ceiling and still land in internal SRAM.
static void *big_realloc(void *ptr, size_t osize, size_t nsize)
{
    if (nsize == 0) {
        if (ptr != NULL) { census_sub(ptr, osize); free(ptr); }
        return NULL;
    }
    // BEFORE the realloc: after it, `ptr` may have been freed, and its region
    // is not readable from an indeterminate value (see census_sub_region).
    int oregion = (ptr != NULL) ? mc_region(ptr) : 0;
    void *np;
#ifdef MOYCORE_PSRAM
    int tried_sram = 0;
    np = NULL;
    size_t sram_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (sram_free < g_sram_free_min) g_sram_free_min = sram_free;
    if (sram_free >= nsize + g_sram_floor) {
        tried_sram = 1;
        np = heap_caps_realloc(ptr, nsize, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (np == NULL) {
        if (!tried_sram) g_sram_denied++;   // the FLOOR turned it away, not a
        np = heap_caps_realloc(ptr, nsize,  // failed internal allocation
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (np != NULL) g_psram_fallback = 1;
    }
#else
    if (g_sram_sim) {                       // the host's simulated split
        if (g_sram_sim < g_sram_free_min) g_sram_free_min = g_sram_sim;
        if (g_sram_sim < nsize + g_sram_floor) {
            g_sram_denied++;
            g_psram_fallback = 1;
        }
    }
    np = realloc(ptr, nsize);
#endif
    if (np != NULL) {
        if (ptr != NULL) census_sub_region(oregion, osize);
        census_add(np, nsize);
    }
    return np;                       // NULL: Lua runs an emergency GC and retries
}

// -- the small-object pool ---------------------------------------------------
//
// MEASURED on the T-Deck (S3 at 240MHz, VM heap in PSRAM, IDF poisoning off):
// about 9us for one malloc and 2us for one free, because the IDF allocator's
// own control structures sit in PSRAM. That is what a Lua `{}` costs -- 9.7us
// against 0.15us for an empty loop iteration and 0.95us for a function call --
// and a two-field constructor 21us. A PICO-8 port builds a table per vector
// operation and a string key per tile, every tick, and the S3 owes a frame
// every 33ms, so on those carts the ALLOCATOR is the frame.
//
// Every hot Lua object is small: a TValue is 8 bytes here, a hash Node 16, a
// Table 32, a short TString 16 + len, and UpVal / CallInfo / LClosure /
// CClosure all fit inside 100. Eight size classes to 256 bytes cover the
// churn, carved out of chunks by a bump cursor and recycled through a free
// list threaded through the free blocks themselves -- pop and push, no search,
// no coalescing, no per-block header.
//
// NO HEADER because Lua's realloc contract hands the size back: when ptr is
// non-NULL, osize is the size the block was allocated with (it is a type TAG
// only when ptr is NULL, which l_alloc normalises to 0 first). So a block's
// class is a function of osize alone -- and that is exactly why the invariant
// below has to hold absolutely:
//
//     a request of 1..256 bytes is ALWAYS a pool block and never anything
//     else, because the free path has nothing but the size to decide with.
//
// Which is why pool exhaustion returns NULL and lets Lua run its emergency GC
// and retry, rather than falling back to big_realloc: one heap pointer pushed
// onto a free list would be handed out later as a pool block and then leaked
// past close, and one pool pointer passed to free() would be worse.
//
// ONE CLASS PER CHUNK, which is what lets a chunk go BACK. A chunk is aligned
// to its own size, so `p & ~(chunk-1)` is the chunk a block came from in one
// AND; the header there carries that class's free list, its bump cursor and a
// LIVE COUNT, so the free that empties a chunk is the free that returns it --
// O(1) worst case, with no free list to walk, because every entry that could
// point into the chunk IS the chunk's own list. Shared chunks cannot do that
// at any price: measured here, moss moss's parse burst left 6.4MB of chunks
// holding 11KB of live blocks, and one survivor pins the whole chunk.
//
// The price is a floor: a class touched at all holds a chunk, so eight classes
// hold eight chunks. Hence 8KB -- a 64KB floor, and 512 blocks of the
// commonest class have to die together to give one back.
//
// ONE CHUNK SIZE PER RUN. The halving retry still runs, but only for the first
// chunk: the mask is what makes the lookup a single AND, and a second size
// would need a second mask. A later chunk that cannot be had returns NULL, and
// Lua's emergency GC does free chunks before the retry.
//
// ONE EMPTY CHUNK IS KEPT BACK, retyped to whatever class asks for the next
// one. Without it, a loop allocating and freeing one block of a class whose
// other chunks are full pays a malloc AND a free every iteration; with it that
// oscillation costs nothing, and two classes oscillating together cost one
// pair per cycle rather than one per allocation.
//
// WHERE THE MEMORY LIVES. Chunks are PSRAM-only, and so are the free-list
// heads -- they live in the chunk header, and what stays in the static
// (internal SRAM) is the per-class CURRENT CHUNK. The header's hot fields are
// its first 32 bytes on purpose: one cache line per active chunk, eight lines
// in the steady state. The blocks stay out of SRAM because giving the VM more
// internal SRAM measured SLOWER -- the rest of the board (WiFi/DMA pools, the
// flush bounce, the poller) starves for it. So the pool never competes with the SRAM floor, and big_realloc's
// SRAM-first policy above is untouched.
//
// MOYCORE_POOL=0 compiles the whole thing out, which is the A/B: the P4 has
// abundant internal SRAM and a different allocator profile, and per-board
// verdicts do not transfer.

#if MOYCORE_POOL

#define POOL_CLASSES 8
#define POOL_MAX     256
static const uint16_t POOL_SZ[POOL_CLASSES] = {16, 32, 48, 64, 96, 128, 192, 256};

#ifndef MOYCORE_POOL_CHUNK
#define MOYCORE_POOL_CHUNK (8 * 1024)
#endif
#define MOYCORE_POOL_CHUNK_MIN (4 * 1024)

#if !defined(MOYCORE_PSRAM) && \
    (defined(__unix__) || defined(__APPLE__) || defined(__EMSCRIPTEN__))
// Declared here rather than reached through a feature macro: which of
// _POSIX_C_SOURCE / _DEFAULT_SOURCE stdlib.h wants depends on the port's -std,
// and the host builds do not agree about it.
extern int posix_memalign(void **memptr, size_t alignment, size_t size);
#endif

typedef struct mc_chunk {
    void            *freed;           // this chunk's free blocks, one class
    uint8_t         *carve;           // the tail nobody has been handed yet
    uint32_t         carve_left;
    uint32_t         live;            // handed out and not yet freed
    uint8_t          cls;             // POOL_CLASSES while it is the spare
    uint8_t          roomed;          // on g_room[cls]
    struct mc_chunk *gnext, *gprev;   // every chunk the pool holds
    struct mc_chunk *rnext, *rprev;   // ...and this class's, those with room
    void            *raw;             // what the heap returned: see chunk_new
} mc_chunk;

// Rounded to 16 so every block stays 16-aligned, which every class size is.
#define POOL_HDR (((sizeof(mc_chunk) + 15u) / 16u) * 16u)

static mc_chunk *g_room[POOL_CLASSES];   // a static: internal SRAM on a board
static mc_chunk *g_chunks, *g_spare;
static size_t    g_chunk_sz, g_chunk_usable;
static uintptr_t g_chunk_mask;
static size_t    g_pool_live, g_pool_cap;
static uint32_t  g_chunk_n;

// size -> class, or -1 for "not a pool size" (0, or over the ceiling).
// Arithmetic rather than a lookup table on purpose: this runs on every
// allocation and a const table on the S3 is a flash read.
static inline int pool_class(size_t n)
{
    if (n == 0 || n > POOL_MAX) return -1;
    if (n <= 64)  return (int)((n + 15) >> 4) - 1;   //  16  32  48  64
    if (n <= 128) return (int)((n + 31) >> 5) + 1;   //  96 128
    return (int)((n + 63) >> 6) + 3;                 // 192 256
}

// sz is a power of two AND the alignment, because the free path recovers the
// chunk from a block with one AND. On the boards that is heap_caps_aligned_
// alloc, whose TLSF trims both the leading gap and the tail back into the heap
// -- so the alignment costs a bigger search, not a bigger allocation.
static mc_chunk *chunk_new(size_t sz)
{
    void *raw, *base;
#ifdef MOYCORE_PSRAM
    raw = heap_caps_aligned_alloc(sz, sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (raw == NULL) return NULL;
    base = raw;
#elif defined(__unix__) || defined(__APPLE__) || defined(__EMSCRIPTEN__)
    if (posix_memalign(&raw, sz, sz) != 0) return NULL;
    base = raw;
#else
    raw = malloc(sz + sz - 1);        // no aligned allocator: over-allocate,
    if (raw == NULL) return NULL;     // align by hand, and free the raw pointer
    base = (void *)(((uintptr_t)raw + sz - 1) & ~(uintptr_t)(sz - 1));
#endif
    ((mc_chunk *)base)->raw = raw;
    return (mc_chunk *)base;
}

static void chunk_free(mc_chunk *c)
{
#ifdef MOYCORE_PSRAM
    heap_caps_free(c->raw);           // aligned or not: TLSF blocks are the same
#else
    free(c->raw);
#endif
}

static inline void room_link(mc_chunk *c)
{
    c->rprev = NULL;
    c->rnext = g_room[c->cls];
    if (c->rnext != NULL) c->rnext->rprev = c;
    g_room[c->cls] = c;
    c->roomed = 1;
}

static inline void room_unlink(mc_chunk *c)
{
    if (c->rprev != NULL) c->rprev->rnext = c->rnext;
    else                  g_room[c->cls] = c->rnext;
    if (c->rnext != NULL) c->rnext->rprev = c->rprev;
    c->roomed = 0;
}

static void chunk_reset(mc_chunk *c, int cls)
{
    c->cls = (uint8_t)cls;
    c->freed = NULL;
    c->carve = (uint8_t *)c + POOL_HDR;
    c->carve_left = (uint32_t)g_chunk_usable;
    c->live = 0;
}

static mc_chunk *pool_add_chunk(int cls)
{
    mc_chunk *c = g_spare;
    if (c != NULL) {
        g_spare = NULL;              // already on the global list and counted
    } else {
        size_t want = g_chunk_sz;
        if (want == 0) {             // the first chunk of a run picks the size
            want = MOYCORE_POOL_CHUNK;
            for (;;) {
                c = chunk_new(want);
                if (c != NULL) break;
                if (want <= MOYCORE_POOL_CHUNK_MIN) return NULL;
                want >>= 1;
            }
            g_chunk_sz = want;
            g_chunk_mask = ~(uintptr_t)(want - 1);
            g_chunk_usable = want - POOL_HDR;
        } else {
            c = chunk_new(want);
            if (c == NULL) return NULL;
        }
        c->gprev = NULL;
        c->gnext = g_chunks;
        if (c->gnext != NULL) c->gnext->gprev = c;
        g_chunks = c;
        g_pool_cap += g_chunk_usable;
        g_chunk_n++;
    }
    chunk_reset(c, cls);
    room_link(c);
    return c;
}

// The last block of a chunk went back. Keep one empty chunk as the spare and
// give the rest to the heap -- pool_cap comes down with them, which is the
// number the burst-then-steady check watches.
static void pool_drop(mc_chunk *c)
{
    if (c->roomed) room_unlink(c);
    if (g_spare == NULL) {
        c->cls = POOL_CLASSES;       // classless until it is handed out again
        g_spare = c;
        return;
    }
    if (c->gprev != NULL) c->gprev->gnext = c->gnext;
    else                  g_chunks = c->gnext;
    if (c->gnext != NULL) c->gnext->gprev = c->gprev;
    g_pool_cap -= g_chunk_usable;
    g_chunk_n--;
    chunk_free(c);
}

static void *pool_alloc(int cls)
{
    mc_chunk *c = g_room[cls];       // by invariant: NULL, or it has room
    if (c == NULL) {
        c = pool_add_chunk(cls);
        if (c == NULL) return NULL;
    }
    size_t sz = POOL_SZ[cls];
    void *b = c->freed;
    if (b != NULL) {
        c->freed = *(void **)b;
    } else {
        b = c->carve;
        c->carve += sz;
        c->carve_left -= (uint32_t)sz;
    }
    c->live++;
    if (c->freed == NULL && c->carve_left < sz) room_unlink(c);
    g_pool_live += sz;
    return b;
}

static inline void pool_free(void *p, int cls)
{
    mc_chunk *c = (mc_chunk *)((uintptr_t)p & g_chunk_mask);
    *(void **)p = c->freed;
    c->freed = p;
    g_pool_live -= POOL_SZ[cls];
    if (--c->live == 0)   pool_drop(c);
    else if (!c->roomed)  room_link(c);
}

// Every chunk goes back at close, so nothing survives a run: the pool belongs
// to one lua_State and l_alloc serves no other caller, so by the time
// lua_close has returned, every block is back on a free list. mod_close is the
// only caller besides run_begin's own failure path.
static void pool_release(void)
{
    mc_chunk *c = g_chunks;
    while (c != NULL) {
        mc_chunk *next = c->gnext;
        chunk_free(c);
        c = next;
    }
    g_chunks = NULL;
    g_spare = NULL;
    memset(g_room, 0, sizeof(g_room));
    g_chunk_n = 0;
    g_pool_live = 0;
    g_pool_cap = 0;
    g_chunk_sz = 0;
    g_chunk_usable = 0;
    g_chunk_mask = 0;
}

// The invariants the whole design rests on, checked from the outside, because
// the one that matters most -- a block masks back to its own chunk -- is
// invisible from Python and silent when it breaks: a wrong mask corrupts
// another chunk's header instead of faulting. 0 is clean; the codes are the
// order of the checks and are documented in README.md.
static int pool_check(void)
{
    size_t live = 0, seen = 0;
    for (mc_chunk *c = g_chunks; c != NULL; c = c->gnext) {
        if (g_chunk_sz == 0) return -1;
        if (((uintptr_t)c & (uintptr_t)(g_chunk_sz - 1)) != 0) return -2;
        if (c->gnext != NULL && c->gnext->gprev != c) return -3;
        if (c->cls > POOL_CLASSES) return -4;
        seen++;
        if (c == g_spare) {
            if (c->live != 0 || c->roomed) return -5;
            continue;
        }
        if (c->cls >= POOL_CLASSES) return -4;
        size_t sz = POOL_SZ[c->cls];
        size_t carved = (g_chunk_usable - c->carve_left) / sz;
        size_t freed = 0;
        for (void *b = c->freed; b != NULL; b = *(void **)b) {
            if ((mc_chunk *)((uintptr_t)b & g_chunk_mask) != c) return -6;
            if (((uintptr_t)b - ((uintptr_t)c + POOL_HDR)) % sz != 0) return -7;
            if (++freed > carved) return -8;      // a cycle, or a double free
        }
        if (c->live + freed != carved) return -9;
        // Room is not an opinion: a chunk is on its class's list exactly when
        // it can serve the next allocation without a new chunk.
        if (c->roomed != (c->freed != NULL || c->carve_left >= sz)) return -10;
        live += c->live * sz;
    }
    if (g_spare != NULL && g_spare->cls != POOL_CLASSES) return -11;
    if (seen != g_chunk_n) return -12;
    if (g_pool_cap != (size_t)g_chunk_n * g_chunk_usable) return -13;
    if (live != g_pool_live) return -14;
    for (int cls = 0; cls < POOL_CLASSES; cls++)
        for (mc_chunk *c = g_room[cls]; c != NULL; c = c->rnext)
            if (c->cls != cls || !c->roomed) return -15;
    return 0;
}
#endif  // MOYCORE_POOL

// The VM's allocator. Small requests come off the pool, everything else off
// the heap, and a block that crosses the ceiling in either direction is copied
// -- Lua's contract gives the true old size, so both ends of the move know
// exactly how many bytes are live.
static void *l_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
    (void)ud;
    if (ptr == NULL) osize = 0;      // lua_Alloc contract: a type tag, not a size
#if MOYCORE_POOL
    int oc = (ptr != NULL) ? pool_class(osize) : -1;
    int nc = pool_class(nsize);      // nsize == 0 -> -1, which is the free path
    if (oc >= 0 && nc >= 0) {
        if (oc == nc) {              // grow or shrink inside one class
            census_sub(ptr, osize);  // the BLOCK does not move; the bytes Lua
            census_add(ptr, nsize);  // asked for did, and the free subtracts
            return ptr;              // the new number, so re-charge it here
        }
        void *np = pool_alloc(nc);
        if (np == NULL) return NULL;
        memcpy(np, ptr, nsize < osize ? nsize : osize);
        pool_free(ptr, oc);
        census_sub(ptr, osize);
        census_add(np, nsize);
        return np;
    }
    if (oc >= 0) {                   // leaving the pool: freed, or grown past 256
        if (nsize == 0) {
            pool_free(ptr, oc);
            census_sub(ptr, osize);
            return NULL;
        }
        void *np = big_realloc(NULL, 0, nsize);
        if (np == NULL) return NULL;
        memcpy(np, ptr, osize);
        pool_free(ptr, oc);
        census_sub(ptr, osize);
        return np;
    }
    if (nc >= 0) {                   // entering it: fresh, or shrunk into range
        void *np = pool_alloc(nc);
        if (np == NULL) return NULL;
        census_add(np, nsize);
        if (ptr != NULL) {
            memcpy(np, ptr, nsize);
            big_realloc(ptr, osize, 0);
        }
        return np;
    }
#endif
    return big_realloc(ptr, osize, nsize);
}

// -- the p8 shim's masked map walk (#66 M0) ----------------------------------
//
// Carried over from moy_lua, which is the point: without it a ported p8 cart
// runs its map through the shim's LUA cell loop -- 4.5ms of celeste's S3
// render, measured by difference -- and moving the cart to moycore would have
// silently handed that back. "One runtime" has to mean the fast one.
//
// Simpler here than in moy_lua by exactly the amount moycore is worth: there,
// each surviving cell had to be appended as a quad through the batch protocol
// so blit_batch's walk drew it; here the raster IS libmoy and a cell is a call
// to the same moy_spr the cart's own spr() reaches. No new raster either way.
//
// Deliberately NOT named `map`: the console's map() keeps its Python lane (it
// owns the Fold-2 cache). These are shim machinery with p8 semantics -- tile 0
// never draws, colorkey 0, scale 1, no flip -- and the __gff__ flag byte gates
// each cell against the mask. The shim nil-guards both and keeps its Lua loop,
// which is what a build without them still takes.

// MOY_FLAGS wide: libmoy's own fget/fset/map(..., layers) read the console's
// flag table (SPEC.md 3.5), and this is that table here.

// The PICO-8 machine (libmoy moy_p8.c): 64KB of memory and a ROM snapshot,
// reseeded per run by moy_p8_open. The buffers are the caller's, handed over
// by moycore_lua_p8_memory (modmoycore.c's p8_memory hands Python's), like
// the framebuffer, the sheet and the map -- NOT taken from the ESP heap. The S3 boards' MicroPython heap owns
// all but ~1.5KB of the PSRAM region, so an 81KB heap_caps_malloc there takes
// the last of Lua's own PSRAM fallback and every Lua cart dies with "not
// enough memory" -- seen on both S3 boards.
// See moy-spec proposals/p8-memory-map.md for what a byte costs.
static moy_p8 g_p8;
static uint8_t *g_p8mem, *g_p8rom;

static int mc_unhex(int ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

// __moy_map_flags(hex): the baked __gff__ table, two hex chars per sprite,
// crossed ONCE at shim boot. A bad nibble reads as 0, exactly like the shim's
// own tonumber(..., 16). Non-string clears.
static int l_map_flags(lua_State *L)
{
    size_t len = 0, n, i;
    const char *s;
    memset(RUN.c.flags, 0, sizeof(RUN.c.flags));
    s = (lua_type(L, 1) == LUA_TSTRING) ? lua_tolstring(L, 1, &len) : NULL;
    if (s == NULL) { lua_pushboolean(L, 0); return 1; }
    n = len / 2;
    if (n > sizeof(RUN.c.flags)) n = sizeof(RUN.c.flags);
    for (i = 0; i < n; i++) {
        int hi = mc_unhex((unsigned char)s[2 * i]);
        int lo = mc_unhex((unsigned char)s[2 * i + 1]);
        if (hi >= 0 && lo >= 0) RUN.c.flags[i] = (uint8_t)((hi << 4) | lo);
    }
    lua_pushboolean(L, 1);
    return 1;
}

// __moy_map_masked(celx, cely, sx, sy, cw, ch, mask) -> bool: true when the
// walk ran (even if every cell was masked out); false when the caller must
// take its Lua-loop fallback (no sheet/map, odd args). Map cells store tile
// id + 1 (0 = empty), so a cell draws when its id > 0 and, under a nonzero
// mask, when (gff[id] & mask) != 0 -- the shim's exact condition.
static int l_map_masked(lua_State *L)
{
    int v[7], i, cx, cy;
    int celx, cely, sx, sy, cw, ch, mask, mw, mh;
    const uint8_t *cells;
    if (!RUN.c.con.sheet || !RUN.c.con.map || lua_gettop(L) != 7) {
        lua_pushboolean(L, 0);
        return 1;
    }
    for (i = 0; i < 7; i++) {
        if (!lua_isnumber(L, i + 1)) { lua_pushboolean(L, 0); return 1; }
        v[i] = (int)lua_tointeger(L, i + 1);
    }
    celx = v[0]; cely = v[1]; sx = v[2]; sy = v[3];
    cw = v[4]; ch = v[5]; mask = v[6];
    cells = RUN.c.con.map->cells;
    mw = RUN.c.con.map->w;
    mh = RUN.c.con.map->h;
    for (cy = 0; cy < ch; cy++) {
        int my = cely + cy;
        const uint8_t *row;
        int py = sy + cy * 8;
        if (my < 0 || my >= mh) continue;     // off-map rows read empty
        row = cells + (size_t)my * (size_t)mw;
        for (cx = 0; cx < cw; cx++) {
            int mx = celx + cx, id;
            if (mx < 0 || mx >= mw) continue;
            id = (int)row[mx] - 1;
            if (id <= 0) continue;            // empty, or p8's never-drawn 0
            if (mask != 0 && (RUN.c.flags[id] & mask) == 0) continue;
            moy_spr(RUN.c.con.canvas, RUN.c.con.sheet, id,
                    sx + cx * 8, py, 0, 1, 0);
        }
    }
    lua_pushboolean(L, 1);
    return 1;
}

// lua_gc_mode(mode [, a [, b [, c]]]) -> (heap_kb, running, generational), or
// None with no run. ARMS: the mode is re-applied at the next load(), because
// every A/B tool here relaunches the cart to change one thing.
//
// WHY THIS IS A KNOB AT ALL. moss moss holds a ONE MEGABYTE Lua heap --
// moycore.gc() right after the cart starts, against 151KB for dungeons &
// diagrams through the same importer and 75KB for a hand-written Lua cart --
// so the emitted shim is ~60-75KB of it and the rest is the cart's own data,
// out of 141 lines of PICO-8 source. A collector walking a megabyte every few
// frames is frame time, and unlike the shim and unlike libmoy it is NOT
// vendored: the VM is opened in this file, so its collector is ours to tune.
//
// It is also the one cost the sampling profiler above can UNDER-report, which
// is why they shipped together. Collection runs inside the allocator at a
// checkGC point rather than as counted VM instructions, so it generates no
// samples of its own: it lands in `cyc` -- the wall clock between samples,
// charged to whichever function tripped it -- and never in `smp`. A function
// whose cyc share far exceeds its smp share is ALLOCATING, not computing, and
// that gap is the collector. Reading smp alone would call it the cart's code.
//
//   mode -1  read only          0  stop             1  restart
//        2   incremental(pause, stepmul, stepsize)
//        3   generational(minormul, majormul)
//
// A parameter of -1 leaves that one alone (Lua spells that 0; -1 is used here
// so a caller can pass 0 for "the engine default" without meaning "keep").
static int g_gc_mode = -1;                 // armed request, -1 = leave alone
static int g_gc_a = -1, g_gc_b = -1, g_gc_c = -1;
static uint8_t g_gc_gen;                   // the live mode, tracked: asking
                                           // Lua costs a mode change

static void lua_gc_apply(lua_State *L, int mode, int a, int b, int c)
{
    switch (mode) {
    case 0: lua_gc(L, LUA_GCSTOP); break;
    case 1: lua_gc(L, LUA_GCRESTART); break;
    case 2:
        lua_gc(L, LUA_GCINC, a < 0 ? 0 : a, b < 0 ? 0 : b, c < 0 ? 0 : c);
        lua_gc(L, LUA_GCRESTART);
        g_gc_gen = 0;
        break;
    case 3:
        lua_gc(L, LUA_GCGEN, a < 0 ? 0 : a, b < 0 ? 0 : b);
        lua_gc(L, LUA_GCRESTART);
        g_gc_gen = 1;
        break;
    default: break;
    }
}

void moycore_lua_gc_arm(int mode, int a, int b, int c)
{
    g_gc_mode = mode;
    g_gc_a = a; g_gc_b = b; g_gc_c = c;
    if (RUN.L) lua_gc_apply(RUN.L, mode, a, b, c);
}

int moycore_lua_gc_generational(void)
{
    return g_gc_gen;
}

void moycore_lua_p8_memory(uint8_t *mem, uint8_t *rom)
{
    g_p8mem = mem;
    g_p8rom = rom;
}

// -- the meters ----------------------------------------------------------------

void moycore_lua_meters(moycore_lua_meters_t *out)
{
    out->sram_live = g_live_r[0];
    out->psram_live = g_live_r[1];
    out->peak = g_peak;
    out->sram_denied = g_sram_denied;
#if MOYCORE_POOL
    out->pool_live = g_pool_live;
    out->pool_cap = g_pool_cap;
    out->pool_chunks = g_chunk_n;
#else
    out->pool_live = out->pool_cap = 0;
    out->pool_chunks = 0;
#endif
    out->sram_free_min = g_sram_free_min;
    out->psram_fallback = g_psram_fallback;
    out->sram_floor = g_sram_floor;
}

void moycore_lua_meters_reset(void)
{
    g_sram_free_min = SIZE_MAX;
    g_psram_fallback = 0;
}

size_t moycore_lua_set_sram_floor(size_t bytes)
{
#ifdef MOYCORE_PSRAM
    if (bytes < 16 * 1024) bytes = 16 * 1024;
    if (bytes > 256 * 1024) bytes = 256 * 1024;
    g_sram_floor = bytes;
    return bytes;
#else
    (void)bytes;
    return MOYCORE_SRAM_FLOOR;
#endif
}

int moycore_lua_two_regions(void)
{
#ifdef MOYCORE_PSRAM
    return 1;
#else
    return g_sram_sim != 0;
#endif
}

size_t moycore_lua_sram_sim(size_t bytes)
{
#ifdef MOYCORE_PSRAM
    (void)bytes;
    return 0;
#else
    g_sram_sim = bytes;
    return g_sram_sim;
#endif
}

int moycore_lua_pool_check(void)
{
#if MOYCORE_POOL
    return pool_check();
#else
    return 0;
#endif
}

// -- the run -------------------------------------------------------------------

static void put_err(char *err, size_t n, const char *msg)
{
    if (err != NULL && n > 0) {
        snprintf(err, n, "%s", msg != NULL ? msg : "load failed");
    }
}

int moycore_lua_open(char *err, size_t n)
{
    RUN.L = lua_newstate(l_alloc, NULL);
    if (RUN.L == NULL) {
        put_err(err, n, "moycore: no VM");
        return -1;
    }
    if (moy_lua_open(RUN.L, &RUN.c.con) != 0) {
        lua_close(RUN.L);
        RUN.L = NULL;
#if MOYCORE_POOL
        pool_release();              // a run that never opened still owns chunks
#endif
        put_err(err, n, "moycore: sandbox failed");
        return -1;
    }
    // The p8 shim's helpers, installed before anything the cart can capture.
    // Plain globals, not verb shadows: the shim's own map() picks them up
    // nil-safely and the console's map() is untouched.
    lua_pushcfunction(RUN.L, l_map_masked);
    lua_setglobal(RUN.L, "__moy_map_masked");
    lua_pushcfunction(RUN.L, l_map_flags);
    lua_setglobal(RUN.L, "__moy_map_flags");
    // The layer glue's two natives, captured and cleared by the prelude.
    moycore_layers_open(RUN.L, &RUN.layers, &RUN.c.con);
    moycore_superset_open(RUN.L);
    // ONE table: libmoy's fget/fset/map(..., layers) and the p8 shim's masked
    // walk read the same 512 bytes, seeded from the cart's file. The shim's
    // __moy_map_flags(gff) overwrites it at cart boot, which is what a p8
    // import wants -- its flags ride in the shim, not in a sidecar.
    RUN.c.con.flags = RUN.c.flags;
    // The PICO-8 machine, opened for every run that has its buffers: the shim
    // probes for it, a moy cart never sees the globals it does not ask for. No
    // buffers means no machine and the shim's sparse table -- never a failed
    // run.
    if (g_p8mem) moy_p8_open(RUN.L, &RUN.c.con, &g_p8, g_p8mem, g_p8rom);
    g_gc_gen = 0;                    // a fresh lua_State is incremental
    return 0;
}

int moycore_lua_exec(const char *src, size_t len, const char *name, char *err, size_t n)
{
    if (luaL_loadbufferx(RUN.L, src, len, name, "t") != LUA_OK
        || lua_pcall(RUN.L, 0, 0, 0) != LUA_OK) {
        put_err(err, n, lua_tostring(RUN.L, -1));
        lua_pop(RUN.L, 1);
        return -1;
    }
    return 0;
}

void moycore_lua_load_begin(void)
{
    HOOK(load_begin, RUN.L);
}

int moycore_lua_load_finish(char *err, size_t n)
{
    HOOK(load_end, RUN.L);
    // _init runs here, before any tick has stamped the frame base, and it may
    // call time(). Stamp it now so the elapsed term starts from zero instead
    // of from whatever the counter last held.
    moycore_run_tick_begin();
    if (moy_lua_init(RUN.L, err, n) != 0) {
        return -1;
    }
    // The parse burst is over, and it is the run's high-water mark: a 130KB
    // source becomes tokens, short strings and tables that are all garbage by
    // the time _init returns. Nothing else collects here -- a Lua collector
    // only ever runs in steps at the allocator, and moy_lua_init does not
    // ask -- so the burst would sit in the pool until the cart's own churn
    // walked it out, one step at a time, holding chunks the frame loop is
    // about to need. One full collect, once, before the first frame; on moss
    // moss it is the difference between the cart loading and `not enough
    // memory`.
    lua_gc(RUN.L, LUA_GCCOLLECT);
    // ...and then GENERATIONAL, because Lua's default INCREMENTAL collector is
    // not incremental at a frame's scale. `incstep` paces itself against the
    // ALLOCATION RATE -- it does (stepmul/WORK2MEM) work units per byte the
    // cart allocates, 12.5 of them at the default stepmul, i.e. about a
    // hundred bytes of traversal per byte allocated -- so once a cycle starts
    // it walks a heap several times over within one frame's worth of churn and
    // the cart pays the whole cycle as ONE stop-the-world pause. `gcstepsize`
    // does not divide it: it sets how often a step runs, never how much of the
    // cycle is left to do, and lowering it measured NULL. A minor collection
    // instead traverses only what was allocated since the last one, which is a
    // frame's worth by construction.
    //
    // That cycle was #107's residual stutter, and on glass (P4, dank tomb,
    // 2026-09-20) one 60s soak of it held one 42ms frame in every ~49 -- a
    // dropped tick at the cart's 60Hz logic, and felt. The same soak
    // generational held none above 27ms, with the VM's heap peak a third
    // lower; #107 carries the arms.
    lua_gc_apply(RUN.L, 3, -1, -1, -1);
    // AFTER that collect, never before: an armed `stop` has to not apply to
    // the load burst, which is the run's high-water mark and the one thing
    // that must still be collected. And after the line above, so `luagc inc`
    // can put a run back on the default schedule for an A/B.
    if (g_gc_mode >= 0) lua_gc_apply(RUN.L, g_gc_mode, g_gc_a, g_gc_b, g_gc_c);
    return 0;
}

int moycore_lua_tick(float dt, int draw, char *err, size_t n)
{
    // The hooks bracket the cart's OWN halves and nothing else: a frame that
    // errors out never reaches frame_end, because half a tick would move the
    // profilers' ratios without being a tick.
    uint32_t pm[4];
    HOOK(frame_begin, pm);
    uint32_t t0 = moycore_run_now_us();
    if (moy_lua_update(RUN.L, dt, err, n) != 0) {
        return -1;
    }
    uint32_t t1 = moycore_run_now_us();
    HOOK(frame_mid, pm);
    if (draw && moy_lua_draw(RUN.L, err, n) != 0) {
        return -1;
    }
    moycore_run_set_split(t1 - t0, draw ? moycore_run_now_us() - t1 : 0);
    HOOK(frame_end, draw, pm);
    return 0;
}

void moycore_lua_close(void)
{
    if (RUN.L) lua_close(RUN.L);
    RUN.L = NULL;
#if MOYCORE_POOL
    // AFTER lua_close, never before: until it returns, the chunks still hold
    // live Lua objects. Nothing may survive a run -- a cart that churned its
    // way to twenty chunks must not keep them while the launcher is up.
    pool_release();
#endif
}
