// moycore: the cart's whole frame in C (moycore plan stage 2).
//
// ONE upcall per frame: `run_begin()` builds a libmoy console over the buffers
// the console already owns, and `tick(dt)` runs the cart's _update and _draw
// end to end in C. (The per-verb upcall design it replaced left ~190ms GC
// sweeps from marshalling garbage -- do not reintroduce per-draw crossings.)
//
// THE ENGINE IS NOT WRITTEN HERE, AND THAT IS THE POINT. `libmoy/moy_lua.c` is
// moy-spec's own Lua binding: it registers all 38 SPEC.md verbs as C functions
// against a `moy_console`, and `moy.h` exports the loop
// (`moy_lua_open`/`init`/`update`/`draw`) with the error text crossing as a
// buffer. Re-implementing any of that here -- which is what "finish stage 1 by
// crossing cls, map, camera, clip and pal" would have meant -- would be a
// second C implementation of code that already exists upstream, which is the
// duplication this whole project exists to end. See the plan's 6.0.
//
// What this file IS, therefore, is the HOST half: the glue that says what a
// moybyte console is made of.
//
//   * The canvas is the DeviceCanvas framebuffer, not a copy. libmoy's canvas
//     takes a caller-owned `pix` and (on the RGB565 build) a caller-supplied
//     wire table, explicitly so the device's byte order stays out of the cart
//     contract -- so the cart draws straight into the buffer the compositor is
//     about to present.
//   * Input, time and the pointer arrive through a SNAPSHOT the frame loop
//     refreshes before the tick, not through callbacks into Python. A cart
//     polling btn() 60 times a frame must not cost 60 crossings.
//   * Audio goes the other way, into a small command QUEUE the host drains
//     after the tick. sfx() from inside a cart is a two-int append, and the
//     Python side plays it when the frame is over -- same order, same frame.
//   * pmem lives in a C array with a dirty flag, which is the shape the device
//     already deferred it to (#66): RAM during play, persisted at boundaries.
//
// Verbs moybyte adds ON TOP of the spec -- layers/images, scenes,
// view() -- are not IMPLEMENTED here, and they do not need to be: they
// are REGISTERED here, as Lua globals backed by the same Python closures they
// always had (register() below).
//
// That distinction is the whole design, and getting it wrong cost a rewrite:
// EVERY Lua cart runs here -- libmoy's table first, extra verbs on top as
// trampolines. A cart needing a Python-backed verb needs ONE engine that can
// hold one, never a second runtime (the first cut shipped two and the
// deletion commit records what that cost).

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "py/obj.h"
#include "py/objarray.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "py/objlist.h"
#include "py/objstr.h"

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"

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

// The snapshot the host refreshes before every tick. Plain int32 slots in a
// buffer Python owns, so a cart's btn()/time()/touch() are array reads on this
// side of the wall and one array write on the other.
enum {
    SNAP_BTN = 0,        // held bitmask, player 0 (moy_button bit positions)
    SNAP_BTNP,           // pressed-this-tick bitmask, player 0
    SNAP_BTN_P1,         // ...and player 1, for the two-player forms
    SNAP_BTNP_P1,
    SNAP_PLAYERS,        // always >= 1
    SNAP_TIME_MS,        // since the cart started
    SNAP_TOUCH_X,
    SNAP_TOUCH_Y,
    SNAP_TOUCH_DOWN,     // 0 = no pointer at all (touch() reads nil)
    SNAP_TOUCH_MS,       // how long the current press has lasted
    SNAP_KEY,            // last typed code, or 0
    SNAP_KEY_DOWN,       // bitmap-free: the code currently held, or 0
    SNAP_TEXTMODE,       // written BY the cart (textmode)
    SNAP_QUIT,           // written BY the cart (quit)
    SNAP_LEN,
};

// The audio queue. One int16 op code plus three int16 args, appended by the
// cart and drained by the host after the tick. Deliberately fixed and small: a
// frame that asks for more sound than this is not a frame anybody wanted.
enum { AQ_SFX = 0, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME };
#define AQ_SLOTS 4
#define AQ_MAX   32

typedef struct {
    lua_State  *L;
    moy_console con;
    moy_canvas  canvas;
    moy_sheet   sheet;
    moy_map     map;
    int32_t    *snap;            // Python-owned array("i"), SNAP_LEN entries
    int32_t     pmem[256];
    int         pmem_dirty;
    int16_t    *aq;              // Python-owned array("h"): [n, (op,a,b,c)*]
    size_t      aq_cap;
    mp_obj_t    cfg;             // the cart's config dict, or MP_OBJ_NULL
    int         open;
} moycore_run;

static moycore_run RUN;

// -- the host callbacks ------------------------------------------------------
// Every one of these is a read or a write against the snapshot/queue above.
// None of them re-enters Python: that is the entire point of the module.

static int h_btn(void *user, moy_button b, int player)
{
    (void)user;
    if (!RUN.snap) return 0;
    int32_t mask = RUN.snap[player > 0 ? SNAP_BTN_P1 : SNAP_BTN];
    return (mask >> (int)b) & 1;
}

static int h_btnp(void *user, moy_button b, int player)
{
    (void)user;
    if (!RUN.snap) return 0;
    int32_t mask = RUN.snap[player > 0 ? SNAP_BTNP_P1 : SNAP_BTNP];
    return (mask >> (int)b) & 1;
}

static int h_players(void *user)
{
    (void)user;
    int n = RUN.snap ? (int)RUN.snap[SNAP_PLAYERS] : 1;
    return n < 1 ? 1 : n;
}

// The frame's base time, stamped when the tick begins. See h_time_ms.
static uint32_t g_tick_ms;

// time() -- the snapshot's base PLUS the milliseconds elapsed inside this tick.
//
// The base alone was the whole answer once, and it was wrong in a way no test
// could see: input is deliberately FROZEN for a frame (a cart polling btn() 60
// times must get one consistent answer), and time got bundled in with it. But a
// frozen clock is not a clock. Anything that measures its own work inside a
// frame reads zero, forever.
//
// Bench Lua is exactly that program: it grows a batch until the batch costs at
// least TARGET_MS, measured with time(). Against a frozen clock the cost is
// always 0, so it doubles the batch every frame and never stops -- on glass, a
// purple screen and "cls k=32768" climbing. The cart was fine.
//
// The base still comes from the host, so the console keeps authority over what
// "since the cart started" means (and over anything that would reset it); C
// only adds the part the host cannot see. No crossing either way -- this is a
// hardware counter read.
static uint32_t h_time_ms(void *user)
{
    uint32_t base;
    (void)user;
    base = RUN.snap ? (uint32_t)RUN.snap[SNAP_TIME_MS] : 0;
    return base + ((uint32_t)mp_hal_ticks_ms() - g_tick_ms);
}

static int32_t h_pmem_get(void *user, int slot)
{
    (void)user;
    if (slot < 0 || slot > 255) return 0;
    return RUN.pmem[slot];
}

static void h_pmem_set(void *user, int slot, int32_t value)
{
    (void)user;
    if (slot < 0 || slot > 255) return;
    if (RUN.pmem[slot] != value) {
        RUN.pmem[slot] = value;
        RUN.pmem_dirty = 1;
    }
}

static void aq_push(int op, int a, int b, int c)
{
    if (!RUN.aq || RUN.aq_cap < 1 + AQ_SLOTS) return;
    int n = RUN.aq[0];
    if (n < 0) n = 0;
    if ((size_t)(1 + (n + 1) * AQ_SLOTS) > RUN.aq_cap || n >= AQ_MAX) return;
    int16_t *p = RUN.aq + 1 + n * AQ_SLOTS;
    p[0] = (int16_t)op; p[1] = (int16_t)a; p[2] = (int16_t)b; p[3] = (int16_t)c;
    RUN.aq[0] = (int16_t)(n + 1);
}

static void h_sfx(void *user, int n, int chan) { (void)user; aq_push(AQ_SFX, n, chan, 0); }
static void h_music(void *user, int t, int loop) { (void)user; aq_push(AQ_MUSIC, t, loop, 0); }
static void h_music_stop(void *user) { (void)user; aq_push(AQ_MUSIC_STOP, 0, 0, 0); }
static void h_sound_stop(void *user, int chan) { (void)user; aq_push(AQ_SOUND_STOP, chan, 0, 0); }
static void h_volume(void *user, int level) { (void)user; aq_push(AQ_VOLUME, level, 0, 0); }

static void h_beep(void *user, float freq_hz, float dur_s)
{
    (void)user;
    // Milliseconds and whole hertz: the queue is int16 and a beep's precision
    // beyond that is inaudible. dur is clamped to the int16 ceiling (~32s),
    // which is longer than any beep anybody meant.
    int ms = (int)(dur_s * 1000.0f);
    if (ms < 0) ms = 0;
    if (ms > 32000) ms = 32000;
    int hz = (int)freq_hz;
    if (hz < 0) hz = 0;
    if (hz > 32000) hz = 32000;
    aq_push(AQ_BEEP, hz, ms, 0);
}

static int h_touch(void *user, int out_xyth[4])
{
    (void)user;
    if (!RUN.snap || !RUN.snap[SNAP_TOUCH_DOWN]) return 0;
    out_xyth[0] = RUN.snap[SNAP_TOUCH_X];
    out_xyth[1] = RUN.snap[SNAP_TOUCH_Y];
    out_xyth[2] = RUN.snap[SNAP_TOUCH_DOWN];
    out_xyth[3] = RUN.snap[SNAP_TOUCH_MS];
    return 1;
}

static int h_key(void *user, int code)
{
    (void)user;
    if (!RUN.snap) return 0;
    if (code < 0) return RUN.snap[SNAP_KEY];          // the last typed code
    return RUN.snap[SNAP_KEY_DOWN] == code;
}

static int h_keyp(void *user, int code)
{
    (void)user;
    if (!RUN.snap) return 0;
    if (code < 0) return RUN.snap[SNAP_KEY];
    return RUN.snap[SNAP_KEY] == code;
}

static void h_textmode(void *user, int on)
{
    (void)user;
    if (RUN.snap) RUN.snap[SNAP_TEXTMODE] = on ? 1 : 0;
}

static void h_quit(void *user)
{
    (void)user;
    if (RUN.snap) RUN.snap[SNAP_QUIT] = 1;
}

static const char *h_cfg(void *user, const char *key)
{
    (void)user;
    if (RUN.cfg == MP_OBJ_NULL || key == NULL) return NULL;
    mp_obj_t k = mp_obj_new_str(key, strlen(key));
    mp_map_elem_t *e = mp_map_lookup(mp_obj_dict_get_map(RUN.cfg), k,
                                     MP_MAP_LOOKUP);
    if (e == NULL || e->value == MP_OBJ_NULL) return NULL;
    if (!mp_obj_is_str(e->value)) return NULL;
    // The dict owns the string; libmoy only reads it during the call.
    return mp_obj_str_get_str(e->value);
}

// -- helpers -----------------------------------------------------------------

static void *buf_w(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_WRITE);
    if (len) *len = bi.len;
    return bi.buf;
}

static void *buf_r(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_READ);
    if (len) *len = bi.len;
    return bi.buf;
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
#ifndef MOYCORE_POOL
#define MOYCORE_POOL 1
#endif

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

// -- the extension trampoline ------------------------------------------------
//
// Deliberately the SHAPE moy_lua already proved rather than a new idea: a
// Python callables list held against the gc, an upvalue carrying the index,
// and an nlr-protected call so a raising Python verb becomes a Lua error
// instead of unwinding through the VM. The marshalling is narrow on purpose --
// numbers, strings, booleans, nil, and tuples fanned out to multiple returns
// (touch() needs that) -- because objects have never crossed this boundary and
// the handle glue is how layers travel.

#define MOYCORE_MAX_ARGS 10

static char g_pyerr[192];

static bool call_py(mp_obj_t fn, size_t n, const mp_obj_t *args, mp_obj_t *ret)
{
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        *ret = mp_call_function_n_kw(fn, n, 0, args);
        nlr_pop();
        return true;
    }
    strcpy(g_pyerr, "console api error");
    nlr_buf_t nlr2;
    if (nlr_push(&nlr2) == 0) {
        vstr_t vstr;
        mp_print_t print;
        vstr_init_print(&vstr, 64, &print);
        mp_obj_print_helper(&print, MP_OBJ_FROM_PTR(nlr.ret_val), PRINT_EXC);
        size_t len = vstr.len < sizeof(g_pyerr) - 1 ? vstr.len : sizeof(g_pyerr) - 1;
        memcpy(g_pyerr, vstr.buf, len);
        g_pyerr[len] = 0;
        nlr_pop();
    }
    return false;
}

static mp_obj_t lua_to_mp(lua_State *L, int i)
{
    switch (lua_type(L, i)) {
    case LUA_TNIL:
        return mp_const_none;
    case LUA_TBOOLEAN:
        return lua_toboolean(L, i) ? mp_const_true : mp_const_false;
    case LUA_TNUMBER: {
        if (lua_isinteger(L, i)) {
            lua_Integer v = lua_tointeger(L, i);
            if ((lua_Integer)(mp_int_t)v == v) {
                return mp_obj_new_int((mp_int_t)v);   // #107: no heap box
            }
            return mp_obj_new_int_from_ll(v);
        }
        return mp_obj_new_float((mp_float_t)lua_tonumber(L, i));
    }
    case LUA_TSTRING: {
        size_t len = 0;
        const char *sp = lua_tolstring(L, i, &len);
        return mp_obj_new_str(sp, len);
    }
    default:
        luaL_error(L, "cannot pass a %s to the console api",
                   lua_typename(L, lua_type(L, i)));
        return mp_const_none;                          // unreachable
    }
}

static int push_mp_to_lua(lua_State *L, mp_obj_t v)
{
    if (v == mp_const_none) {
        return 0;
    }
    if (v == mp_const_true || v == mp_const_false) {
        lua_pushboolean(L, v == mp_const_true);
        return 1;
    }
    if (mp_obj_is_int(v)) {
        lua_pushinteger(L, (lua_Integer)mp_obj_get_int(v));
        return 1;
    }
    if (mp_obj_is_float(v)) {
        lua_pushnumber(L, (lua_Number)mp_obj_get_float(v));
        return 1;
    }
    if (mp_obj_is_str(v)) {
        size_t len = 0;
        const char *sp = mp_obj_str_get_data(v, &len);
        lua_pushlstring(L, sp, len);
        return 1;
    }
    if (mp_obj_is_type(v, &mp_type_tuple)) {           // touch()/mouse() fan out
        size_t n = 0;
        mp_obj_t *items = NULL;
        mp_obj_tuple_get(v, &n, &items);
        if (n > MOYCORE_MAX_ARGS) n = MOYCORE_MAX_ARGS;
        for (size_t k = 0; k < n; k++) {
            push_mp_to_lua(L, items[k]);
        }
        return (int)n;
    }
    return luaL_error(L, "console api returned an unsupported value "
                         "(objects stay python-side; use the glue handles)");
}

static int l_tramp(lua_State *L)
{
    int n = lua_gettop(L);
    if (n > MOYCORE_MAX_ARGS) {
        return luaL_error(L, "console api: too many arguments");
    }
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
    mp_obj_t args[MOYCORE_MAX_ARGS];
    for (int i = 0; i < n; i++) {
        args[i] = lua_to_mp(L, i + 1);
    }
    mp_obj_t fn = mp_obj_subscr(MP_STATE_VM(moycore_calls),
                                MP_OBJ_NEW_SMALL_INT(idx), MP_OBJ_SENTINEL);
    mp_obj_t ret = mp_const_none;
    if (!call_py(fn, (size_t)n, args, &ret)) {
        return luaL_error(L, "%s", g_pyerr);
    }
    return push_mp_to_lua(L, ret);
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
// which is what a host without them (lupa) still takes.

// MOY_FLAGS wide: libmoy's own fget/fset/map(..., layers) read the console's
// flag table (SPEC.md 3.5), and this is that table here.
static uint8_t g_map_flags[MOY_FLAGS];

// The PICO-8 machine (libmoy moy_p8.c): 64KB of memory and a ROM snapshot,
// reseeded per run by moy_p8_open. The buffers are PYTHON-OWNED bytearrays
// handed over through p8_memory(), like the framebuffer, the sheet and the
// map -- NOT taken from the ESP heap. The S3 boards' MicroPython heap owns
// all but ~1.5KB of the PSRAM region, so an 81KB heap_caps_malloc there takes
// the last of Lua's own PSRAM fallback and every Lua cart dies with "not
// enough memory" -- seen on both S3 boards.
// See moy-spec proposals/p8-memory-map.md for what a byte costs.
static moy_p8 g_p8;
static uint8_t *g_p8mem, *g_p8rom;

// p8_memory(mem, rom) -- register the machine's buffers (65536 and 0x4300
// bytes, writable). Rooted in the VM state so the gc keeps them; None clears.
static mp_obj_t mod_p8_memory(mp_obj_t mem_obj, mp_obj_t rom_obj)
{
    g_p8mem = NULL;
    g_p8rom = NULL;
    MP_STATE_VM(moycore_p8mem) = MP_OBJ_NULL;
    MP_STATE_VM(moycore_p8rom) = MP_OBJ_NULL;
    if (mem_obj != mp_const_none) {
        size_t n = 0;
        uint8_t *p = buf_w(mem_obj, &n);
        if (n < MOY_P8_MEM) mp_raise_ValueError(MP_ERROR_TEXT("p8_memory: mem < 65536"));
        g_p8mem = p;
        MP_STATE_VM(moycore_p8mem) = mem_obj;
    }
    if (rom_obj != mp_const_none) {
        size_t n = 0;
        uint8_t *p = buf_w(rom_obj, &n);
        if (n < MOY_P8_ROM) mp_raise_ValueError(MP_ERROR_TEXT("p8_memory: rom < 0x4300"));
        g_p8rom = p;
        MP_STATE_VM(moycore_p8rom) = rom_obj;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_p8_memory_obj, mod_p8_memory);

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
    memset(g_map_flags, 0, sizeof(g_map_flags));
    s = (lua_type(L, 1) == LUA_TSTRING) ? lua_tolstring(L, 1, &len) : NULL;
    if (s == NULL) { lua_pushboolean(L, 0); return 1; }
    n = len / 2;
    if (n > sizeof(g_map_flags)) n = sizeof(g_map_flags);
    for (i = 0; i < n; i++) {
        int hi = mc_unhex((unsigned char)s[2 * i]);
        int lo = mc_unhex((unsigned char)s[2 * i + 1]);
        if (hi >= 0 && lo >= 0) g_map_flags[i] = (uint8_t)((hi << 4) | lo);
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
    if (!RUN.con.sheet || !RUN.con.map || lua_gettop(L) != 7) {
        lua_pushboolean(L, 0);
        return 1;
    }
    for (i = 0; i < 7; i++) {
        if (!lua_isnumber(L, i + 1)) { lua_pushboolean(L, 0); return 1; }
        v[i] = (int)lua_tointeger(L, i + 1);
    }
    celx = v[0]; cely = v[1]; sx = v[2]; sy = v[3];
    cw = v[4]; ch = v[5]; mask = v[6];
    cells = RUN.con.map->cells;
    mw = RUN.con.map->w;
    mh = RUN.con.map->h;
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
            if (mask != 0 && (g_map_flags[id] & mask) == 0) continue;
            moy_spr(RUN.con.canvas, RUN.con.sheet, id,
                    sx + cx * 8, py, 0, 1, 0);
        }
    }
    lua_pushboolean(L, 1);
    return 1;
}

// register(name, callable) -- add a verb libmoy does not bind. Must be called
// AFTER run_begin (the VM and the spec table exist by then) and BEFORE the
// cart executes, because a cart captures its globals into locals at load.
static mp_obj_t mod_register(mp_obj_t name_obj, mp_obj_t fn)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    mp_obj_t calls = MP_STATE_VM(moycore_calls);
    if (calls == MP_OBJ_NULL) {
        calls = mp_obj_new_list(0, NULL);
        MP_STATE_VM(moycore_calls) = calls;
    }
    size_t n = 0;
    mp_obj_t *items = NULL;
    mp_obj_list_get(calls, &n, &items);
    mp_obj_list_append(calls, fn);
    lua_pushinteger(RUN.L, (lua_Integer)n);
    lua_pushcclosure(RUN.L, l_tramp, 1);
    lua_setglobal(RUN.L, mp_obj_str_get_str(name_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_register_obj, mod_register);

// -- the per-verb profiler ---------------------------------------------------
//
// WHY IT EXISTS. A Lua/p8 cart draws through libmoy's own C verbs straight into
// the framebuffer -- moy_p8.c's header puts it plainly, "the canvas IS the
// screen region" -- so DeviceCanvas' meters read all-zero on one. DRAW2 says
// layer=0 batch=0 map=0 text=0 fill=0 and BATCH says 0 sprites while the frame
// spends 50ms somewhere. Every pass that asked where that went therefore hit an
// unopenable box and concluded "the cart's own code", which is a guess. This
// opens it: calls and time PER VERB, so a frame made of 3000 calls at 17us is
// distinguishable from one made of 40 at 1.2ms. Those are different problems
// with different fixes, and the ledger (#66, #67) has been unable to tell them
// apart on this tier since the tier existed.
//
// WHAT IT COSTS WITH IT OFF: nothing, and not "nearly nothing". Disarmed, the
// cart's globals ARE the vendored C functions, exactly as moy_lua_open and
// moy_p8_open left them -- there is no wrapper in the hot path and so no gate
// to test in it. That is the whole reason this gates at INSTALL rather than per
// call: an `if (prof)` inside a verb reached three thousand times a frame is a
// tax the shipping frame pays forever to answer a question asked twice a year.
//
// HOW IT WRAPS. Each C-function global is replaced by a closure over three
// upvalues: [1] the original's own upvalue (libmoy's p8 verbs carry the machine
// pointer there, register()'s trampolines their index), [2] the original
// function as a value, [3] the slot. The wrapper calls the original DIRECTLY --
// fn(L), never through lua_call -- so no Lua call frame is added and the
// original's lua_upvalueindex(1) still resolves, because the closure executing
// is ours and its upvalue 1 is where the original's was. Cost per call: two
// clock reads and two adds.
#define PROF_MAX 192
#define PROF_NAMES "moy.prof.names"   // registry: slot+1 -> the global's name
#define PROF_ORIG  "moy.prof.orig"    // registry: slot+1 -> the original value

// The clock is the CPU cycle counter where there is one. mp_hal_ticks_us() is
// esp_timer_get_time() on both S3 boards, and a systimer read costs about what
// the verbs being measured cost -- at three thousand calls a frame it would not
// perturb the measurement so much as become it. CCOUNT is one instruction.
#if defined(__has_include)
#  if __has_include(<esp_cpu.h>)
#    include <esp_cpu.h>
#    define PROF_CYCLES 1
#  endif
#endif
#ifdef PROF_CYCLES
#  define PROF_NOW() ((uint32_t)esp_cpu_get_cycle_count())
#else
#  define PROF_NOW() ((uint32_t)mp_hal_ticks_us())
#endif

static struct { uint32_t calls; uint32_t ticks; uint32_t self; } g_prof[PROF_MAX];
static uint32_t g_prof_child;     // ticks charged to callees of the running verb
static uint32_t g_prof_frames;    // ticks since the reset, for per-frame means
static uint32_t g_prof_hz;        // profiler ticks per second, measured
static int      g_prof_n;         // slots in use
static uint8_t  g_prof_arm;       // install on the next load()
static uint8_t  g_prof_on;        // wrappers are on the live VM

// Lua's base library, minus print -- which the p8 shim replaces with a verb we
// very much want to measure. Skipped rather than wrapped: pcall and error
// unwind straight through a wrapper, next is what a for loop calls directly
// rather than through the global, and none of them is a draw verb.
static const char *const PROF_SKIP[] = {
    "pcall", "xpcall", "error", "assert", "select", "next", "pairs", "ipairs",
    "setmetatable", "getmetatable", "rawget", "rawset", "rawequal", "rawlen",
    "type", "tostring", "tonumber", "unpack", NULL
};

// INCLUSIVE and SELF, because a verb that calls back into Lua would otherwise
// be read as the slowest thing in the cart. foreach() is the case that forced
// this: five calls a frame and ten milliseconds inclusive on moss moss -- all
// of it the Lua function foreach was handed, none of it foreach. Reported as
// verb cost that says "optimise foreach in C", which is precisely the wrong
// conclusion and precisely the kind this whole instrument exists to prevent.
// So a verb is charged what it spent MINUS what its callees spent, and the
// inclusive figure is kept alongside rather than thrown away: `t` says what C
// cost, `in` says how much of the frame ran underneath it.
static int prof_call(lua_State *L)
{
    lua_CFunction fn = lua_tocfunction(L, lua_upvalueindex(2));
    int slot = (int)lua_tointeger(L, lua_upvalueindex(3));
    uint32_t saved = g_prof_child;
    uint32_t t0, dt;
    int n;
    g_prof_child = 0;
    t0 = PROF_NOW();
    n = fn(L);
    dt = PROF_NOW() - t0;
    g_prof[slot].ticks += dt;
    g_prof[slot].self += (g_prof_child < dt) ? dt - g_prof_child : 0;
    g_prof[slot].calls++;
    g_prof_child = saved + dt;
    return n;
}

// Ticks per second, MEASURED against the millisecond clock rather than taken
// from a Kconfig: the two S3 boards and the two P4s do not run at one
// frequency, and a wrong constant here is a wrong answer everywhere downstream.
static uint32_t prof_calibrate(void)
{
#ifdef PROF_CYCLES
    uint32_t u0 = (uint32_t)mp_hal_ticks_us(), c0 = PROF_NOW(), u1, c1;
    do { u1 = (uint32_t)mp_hal_ticks_us(); } while (u1 - u0 < 2000u);
    c1 = PROF_NOW();
    return (uint32_t)(((uint64_t)(c1 - c0) * 1000000u) / (uint64_t)(u1 - u0));
#else
    return 1000000u;
#endif
}

static int prof_skipped(const char *name)
{
    int i;
    for (i = 0; PROF_SKIP[i]; i++)
        if (strcmp(name, PROF_SKIP[i]) == 0) return 1;
    return 0;
}

static void prof_uninstall(lua_State *L)
{
    int i, top;
    if (!g_prof_on) return;
    top = lua_gettop(L);
    lua_getfield(L, LUA_REGISTRYINDEX, PROF_NAMES);
    lua_getfield(L, LUA_REGISTRYINDEX, PROF_ORIG);
    for (i = 0; i < g_prof_n; i++) {
        lua_rawgeti(L, top + 1, i + 1);           // the name
        lua_rawgeti(L, top + 2, i + 1);           // the original
        lua_setglobal(L, lua_tostring(L, top + 3));
        lua_settop(L, top + 2);
    }
    lua_settop(L, top);
    lua_pushnil(L); lua_setfield(L, LUA_REGISTRYINDEX, PROF_NAMES);
    lua_pushnil(L); lua_setfield(L, LUA_REGISTRYINDEX, PROF_ORIG);
    g_prof_on = 0;
    g_prof_n = 0;
}

// Walk _G, collect every wrappable C-function global, then wrap them. TWO
// passes because Lua forbids adding a key to a table under traversal, and a
// name-by-name walk of a fixed list would miss exactly what matters: the p8
// shim RESOLVES its verbs at load (`spr = __moy_p8_spr or spr`) and assigns
// them to globals, so the name a cart calls is not the name libmoy registered.
static void prof_install(lua_State *L)
{
    int n = 0, i, top;
    if (g_prof_on) return;
    top = lua_gettop(L);
    lua_newtable(L);                              // top+1: names
    lua_newtable(L);                              // top+2: originals
    lua_rawgeti(L, LUA_REGISTRYINDEX, LUA_RIDX_GLOBALS);   // top+3
    lua_pushnil(L);
    while (lua_next(L, top + 3) != 0) {
        // key at top+4, value at top+5. The type test comes FIRST: lua_tostring
        // on a number key would convert it in place and derail lua_next.
        if (n < PROF_MAX && lua_type(L, top + 4) == LUA_TSTRING
                && lua_iscfunction(L, top + 5)
                && lua_tocfunction(L, top + 5) != prof_call
                && !prof_skipped(lua_tostring(L, top + 4))) {
            // Only ONE upvalue can be carried across (see the header). Nothing
            // in libmoy or this module registers a C closure with two, but a
            // future one must not be silently mis-wrapped into a verb reading
            // somebody else's upvalue -- so it is skipped, not guessed at.
            int nup = 0;
            while (nup < 2 && lua_getupvalue(L, top + 5, nup + 1) != NULL) nup++;
            lua_pop(L, nup);
            if (nup <= 1) {
                lua_pushvalue(L, top + 4);
                lua_rawseti(L, top + 1, n + 1);
                lua_pushvalue(L, top + 5);
                lua_rawseti(L, top + 2, n + 1);
                n++;
            }
        }
        lua_settop(L, top + 4);                   // drop the value, keep the key
    }
    lua_settop(L, top + 2);                       // drop _G

    for (i = 0; i < n; i++) {
        lua_rawgeti(L, top + 2, i + 1);           // top+3: the original
        if (lua_getupvalue(L, top + 3, 1) == NULL) lua_pushnil(L);  // top+4: uv1
        lua_pushvalue(L, top + 3);                // top+5: uv2, the original
        lua_pushinteger(L, i);                    // top+6: uv3, the slot
        lua_pushcclosure(L, prof_call, 3);        // top+4: the wrapper
        lua_rawgeti(L, top + 1, i + 1);           // top+5: the name
        lua_pushvalue(L, top + 4);
        lua_setglobal(L, lua_tostring(L, top + 5));
        lua_settop(L, top + 2);
    }
    lua_setfield(L, LUA_REGISTRYINDEX, PROF_ORIG);
    lua_setfield(L, LUA_REGISTRYINDEX, PROF_NAMES);
    lua_settop(L, top);

    memset(g_prof, 0, sizeof g_prof);
    g_prof_child = 0;
    g_prof_frames = 0;
    g_prof_n = n;
    if (!g_prof_hz) g_prof_hz = prof_calibrate();
    g_prof_on = 1;
}

// profile(on) -> the number of verbs wrapped, or None with no run.
//
// ARMS as well as installs. A cart captures its globals as it loads, so
// profiling a p8 port properly means the wrappers are in place BEFORE load()
// runs the shim -- arming makes that automatic for the next launch. Installing
// NOW is what lets a board already sitting in a level answer without being
// restarted, at the cost of missing whatever the shim already captured.
static mp_obj_t mod_profile(mp_obj_t on_obj)
{
    int on = mp_obj_is_true(on_obj);
    g_prof_arm = (uint8_t)on;
    if (!RUN.open) return mp_const_none;
    if (on) prof_install(RUN.L);
    else prof_uninstall(RUN.L);
    return mp_obj_new_int(g_prof_n);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_profile_obj, mod_profile);

// verb_stats() -> (hz, frames, ((name, calls, self, inclusive), ...)), or None
// when the profiler is not installed -- None rather than an empty tuple,
// because "not measuring" and "measured nothing" are different answers.
//
// TICKS, not microseconds: the clock is the CPU cycle counter on a board, and
// handing the host `hz` keeps the division off the device and the units honest
// on a tier that has no cycle counter at all. Only verbs actually CALLED are
// reported; a hundred zero rows is not a measurement and would not fit a line.
static mp_obj_t mod_verb_stats(void)
{
    int i, n = 0;
    mp_obj_t rows[PROF_MAX];
    mp_obj_t out[3];
    if (!RUN.open || !g_prof_on) return mp_const_none;
    lua_getfield(RUN.L, LUA_REGISTRYINDEX, PROF_NAMES);
    for (i = 0; i < g_prof_n; i++) {
        mp_obj_t t[4];
        const char *nm;
        if (!g_prof[i].calls) continue;
        lua_rawgeti(RUN.L, -1, i + 1);
        nm = lua_tostring(RUN.L, -1);
        t[0] = mp_obj_new_str(nm ? nm : "?", nm ? strlen(nm) : 1);
        lua_pop(RUN.L, 1);
        t[1] = mp_obj_new_int((mp_int_t)g_prof[i].calls);
        t[2] = mp_obj_new_int((mp_int_t)g_prof[i].self);
        t[3] = mp_obj_new_int((mp_int_t)g_prof[i].ticks);
        rows[n++] = mp_obj_new_tuple(4, t);
    }
    lua_pop(RUN.L, 1);
    out[0] = mp_obj_new_int((mp_int_t)g_prof_hz);
    out[1] = mp_obj_new_int((mp_int_t)g_prof_frames);
    out[2] = mp_obj_new_tuple(n, rows);
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_verb_stats_obj, mod_verb_stats);

// verb_reset() -- zero the counters and the frame count. The host samples by
// resetting, waiting and reading, so the WINDOW is the host's to choose rather
// than a cadence baked in here.
static mp_obj_t mod_verb_reset(void)
{
    memset(g_prof, 0, sizeof g_prof);
    g_prof_child = 0;
    g_prof_frames = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_verb_reset_obj, mod_verb_reset);

// -- the per-FUNCTION Lua profiler -------------------------------------------
//
// WHY A SECOND ONE. The verb profiler above closes half the box: on moss moss
// it says 6.5ms of a 44ms frame is C, which leaves ~37ms "in the interpreter"
// with no way to ask where. That residual is not one body of Lua but two.
// tools/p8_lua_port.py emits 1,348 lines defining 128 functions -- the whole
// PICO-8 standard library -- into every cart it converts, and moss moss's own
// code is 167 lines. Every call the cart makes runs cart Lua -> shim Lua -> C
// verb: `rectfill` does a skip test, four flr() calls, two swaps and a colour
// resolve before its one crossing, and pico off road makes 641 of those a
// frame. Whether that 37ms is the cart's logic or the shared shim decides
// whether an optimisation exists at all -- the shim is GENERATED, so a fix
// there lands on every ported cart at once, and cart code is the cart's.
//
// SAMPLING, and not a call/return hook, because of the population. A call-hook
// profiler pays its overhead per CALL, so it inflates exactly the functions
// that are small and called often -- which is what the shim is made of, and
// what the hypothesis is about. It would find the shim expensive whether or
// not it is. A count hook fires every N VM instructions whoever is running, so
// what it weighs is instructions executed, and the shim's share of those does
// not depend on how often it is entered.
//
// WHAT IT COSTS, stated narrowly. Lua 5.4 gates hooks per CallInfo through
// `trap`, so with no hook set the VM pays nothing at all -- but with
// LUA_MASKCOUNT set EVERY instruction detours through luaG_traceexec, and that
// tax is per-instruction and does NOT fall as the interval rises; only the
// per-sample work does. So a profiled cart runs slower than the real one and
// the honest claim is not "it does not perturb". It is that the tax is the
// same for every Lua function and therefore cancels out of a SHARE -- which is
// a testable claim rather than an assertion: run one cart at two sampling
// rates and the shares agree if it holds. That is why the SAMPLE share and not
// the wall-clock one is the number to read, and why lua_stats reports the
// interval it was taken at.
//
// WHAT IT CANNOT SEE, and this one is load-bearing: the COLLECTOR. Lua runs
// it inside the allocator at a checkGC point, not as counted VM instructions,
// so a collection generates no samples however long it takes. It shows up in
// the wall-clock figure -- time between samples, charged to whoever tripped it
// -- and never in the sample count. So a function whose wall-clock share badly
// exceeds its sample share is ALLOCATING rather than computing, and on a cart
// holding a megabyte of Lua heap that gap is the collector. Read the samples
// alone there and the answer comes back "the cart's own code" when the cost is
// collection the cart triggered.
// lua_gc_mode below is the lever for it, and the two shipped together.
//
// Disarmed there is no hook, no table and no allocation. The cart runs the
// vendored VM exactly as it shipped.
#define LPROF_SLOTS 256           // a power of two: the probe masks with it
#define LPROF_SRCS  4             // @cart, prelude, and room to be surprised

typedef struct {
    const char *src;              // the Proto's source text: stable per chunk
    int32_t line;                 // linedefined -- with src, this IS the Proto
    uint32_t calls;
    uint32_t samples;
    uint64_t cycles;              // 32 bits is 17.9s of a 240MHz counter, and
} lprof_slot_t;                   // a measurement window is longer than that

static lprof_slot_t *g_lp;                    // NULL unless installed
static const char *g_lp_src[LPROF_SRCS];      // the chunks seen, by identity
static char     g_lp_srcname[LPROF_SRCS][40];
static int      g_lp_nsrc;
static const char *g_lp_cart;                 // the cart chunk, once PINNED
static int32_t  g_lp_lo, g_lp_hi;             // the shim's lines within it
static uint32_t g_lp_samples, g_lp_calls, g_lp_ccalls;
static uint64_t g_lp_cycles, g_lp_shim_cy;
static uint32_t g_lp_shim_s, g_lp_shim_n;
static uint32_t g_lp_dropped;                 // samples past the last slot
static uint32_t g_lp_t0;                      // the previous sample's clock
static uint32_t g_lp_frames;
static int      g_lp_interval;
static int      g_lp_used;
static uint8_t  g_lp_on;
static uint8_t  g_lp_arm;                     // install at the next load()
static int      g_lp_arm_iv;
static int32_t  g_lp_arm_lo, g_lp_arm_hi;

// A Proto's slot, claimed on first sight. Open addressing over a power-of-two
// table, because the alternative inside a hook is an allocation inside a hook.
// A full table counts into g_lp_dropped rather than folding two functions into
// one row: a wrong attribution is worse than a missing one, and the totals --
// which are what the split is computed from -- stay whole either way.
static int lprof_slot(const char *src, int32_t line)
{
    uint32_t h = (uint32_t)(uintptr_t)src * 2654435761u;
    int i, probes;
    h ^= (uint32_t)line * 40503u;
    i = (int)((h >> 5) & (LPROF_SLOTS - 1));
    for (probes = 0; probes < LPROF_SLOTS; probes++) {
        if (g_lp[i].src == NULL) {
            g_lp[i].src = src;
            g_lp[i].line = line;
            g_lp_used++;
            return i;
        }
        if (g_lp[i].src == src && g_lp[i].line == line) return i;
        i = (i + 1) & (LPROF_SLOTS - 1);
    }
    return -1;
}

// The chunk names, kept once each rather than per row: every row shares one of
// two of them, and lua_stats hands the host an index into this.
static void lprof_note_src(const char *src, const char *short_src)
{
    int i;
    for (i = 0; i < g_lp_nsrc; i++)
        if (g_lp_src[i] == src) return;
    if (g_lp_nsrc >= LPROF_SRCS) return;
    g_lp_src[g_lp_nsrc] = src;
    // Copied by hand rather than with strncpy: a chunk name is routinely
    // longer than this buffer (Lua's short_src is 60 bytes, the buffer is
    // 40), and gcc's -Wstringop-truncation is an ERROR on the P4's RISC-V
    // toolchain -- truncating here is deliberate, so say so in code the
    // compiler cannot mistake for an accident. Always NUL-terminated.
    {
        const char *from = short_src ? short_src : "?";
        char *to = g_lp_srcname[g_lp_nsrc];
        size_t cap = sizeof(g_lp_srcname[0]) - 1, n = 0;
        while (n < cap && from[n] != '\0') {
            to[n] = from[n];
            n++;
        }
        to[n] = '\0';
    }
    g_lp_nsrc++;
}

// The hook. Two events answering different questions: COUNT is the sample --
// where the interpreter IS -- and CALL is an exact count, which sampling
// cannot give and the report needs beside it. C functions are counted but
// never given a slot: every one reports source "=[C]" and linedefined -1, so
// they would collide into a single meaningless row, and the verb profiler
// above already breaks that same total down by name.
static void lprof_hook(lua_State *L, lua_Debug *ar)
{
    uint32_t now = PROF_NOW();
    int slot, shim;
    if (!g_lp) return;
    if (!lua_getinfo(L, "S", ar)) return;
    if (ar->linedefined < 0) {                  // a C function
        if (ar->event != LUA_HOOKCOUNT) g_lp_ccalls++;
        return;
    }
    shim = (g_lp_cart && ar->source == g_lp_cart
            && ar->linedefined >= g_lp_lo && ar->linedefined <= g_lp_hi);
    slot = lprof_slot(ar->source, (int32_t)ar->linedefined);
    if (slot >= 0 && g_lp[slot].calls == 0 && g_lp[slot].samples == 0)
        lprof_note_src(ar->source, ar->short_src);
    if (ar->event == LUA_HOOKCOUNT) {
        // The interval since the last sample, charged to the function at
        // the END of it -- the ordinary reading of "the VM is here now". It
        // therefore carries any C verb called in between, which is why the two
        // columns part company on the draw functions and agree nearly
        // everywhere else. tick() re-bases the clock every frame so the host's
        // own frame never lands on a cart function.
        uint32_t dt = now - g_lp_t0;
        g_lp_t0 = now;
        g_lp_samples++;
        g_lp_cycles += dt;
        if (shim) { g_lp_shim_s++; g_lp_shim_cy += dt; }
        if (slot < 0) { g_lp_dropped++; return; }
        g_lp[slot].samples++;
        g_lp[slot].cycles += dt;
    } else {
        g_lp_calls++;
        if (shim) g_lp_shim_n++;
        if (slot >= 0) g_lp[slot].calls++;
    }
}

// PIN the cart chunk, so the shim's line range is read against the right one.
// The prelude (moycore.exec, "prelude") is a second chunk whose lines also
// start at 1, and a range applied to both would file its functions as shim.
//
// `_draw` is the anchor because the SHIM owns it -- the porter renames a p8
// cart's own `_draw` to `p8_draw` and the shim's `_draw` calls that -- so its
// linedefined lands inside the emitted block on every ported cart. Which makes
// it a check as well as an anchor: if the line the VM reports is outside the
// range the host passed, the two disagree about this cart, and the pin is
// REFUSED. Nothing is charged as shim then, and lua_stats says so, rather than
// reporting a confident split of the wrong file.
static void lprof_pin(lua_State *L, int32_t lo, int32_t hi)
{
    lua_Debug ar;
    g_lp_cart = NULL;
    g_lp_lo = lo;
    g_lp_hi = hi;
    if (lo <= 0 || hi < lo) return;
    lua_getglobal(L, "_draw");
    if (lua_type(L, -1) == LUA_TFUNCTION && !lua_iscfunction(L, -1)) {
        if (lua_getinfo(L, ">S", &ar)          // pops the function
                && ar.linedefined >= lo && ar.linedefined <= hi)
            g_lp_cart = ar.source;
    } else {
        lua_pop(L, 1);
    }
}

static void lprof_zero(void)
{
    if (g_lp) memset(g_lp, 0, LPROF_SLOTS * sizeof(lprof_slot_t));
    g_lp_nsrc = 0;
    g_lp_used = 0;
    g_lp_samples = g_lp_calls = g_lp_ccalls = 0;
    g_lp_cycles = g_lp_shim_cy = 0;
    g_lp_shim_s = g_lp_shim_n = 0;
    g_lp_dropped = 0;
    g_lp_frames = 0;
    g_lp_t0 = PROF_NOW();
}

// Drop the table and the flags WITHOUT touching the VM -- close() has already
// destroyed it by the time this runs, and a sethook on a freed lua_State is
// the kind of crash that reads as "the profiler broke the console".
static void lprof_forget(void)
{
    if (g_lp) { free(g_lp); g_lp = NULL; }
    g_lp_on = 0;
    g_lp_cart = NULL;
}

static void lprof_uninstall(lua_State *L)
{
    if (L && g_lp_on) lua_sethook(L, NULL, 0, 0);
    lprof_forget();
}

static int lprof_install(lua_State *L, int interval, int32_t lo, int32_t hi)
{
    int mask;
    if (g_lp_on) lprof_uninstall(L);
    g_lp = (lprof_slot_t *)malloc(LPROF_SLOTS * sizeof(lprof_slot_t));
    if (!g_lp) return 0;
    lprof_zero();
    lprof_pin(L, lo, hi);
    g_lp_interval = interval;
    // interval <= 0 arms the CALL hook ALONE. That is the isolation knob: the
    // per-instruction trap tax and the per-sample work are two different costs
    // and the header's claim about them was sized this way rather than guessed
    // at. Coroutines created after this inherit the hook (lua_newthread copies
    // hook/hookmask/basehookcount); ones already alive do not.
    mask = LUA_MASKCALL;
    if (interval > 0) mask |= LUA_MASKCOUNT;
    if (!g_prof_hz) g_prof_hz = prof_calibrate();
    lua_sethook(L, lprof_hook, mask, interval > 0 ? interval : 0);
    g_lp_on = 1;
    return LPROF_SLOTS;
}

// lua_profile(on [, interval [, shim_lo [, shim_hi]]]) -> slots, or None with
// no run. ARMS as well as installs, so a relaunch keeps measuring. Unlike the
// verb profiler nothing here is captured at load time, so installing into a
// cart already sitting in a level is a complete reading and not a partial one.
static mp_obj_t mod_lua_profile(size_t n_args, const mp_obj_t *args)
{
    int on = mp_obj_is_true(args[0]);
    int interval = n_args > 1 ? mp_obj_get_int(args[1]) : 1024;
    int32_t lo = n_args > 2 ? (int32_t)mp_obj_get_int(args[2]) : 0;
    int32_t hi = n_args > 3 ? (int32_t)mp_obj_get_int(args[3]) : 0;
    g_lp_arm = (uint8_t)on;
    g_lp_arm_iv = interval;
    g_lp_arm_lo = lo;
    g_lp_arm_hi = hi;
    if (!RUN.open) return mp_const_none;
    if (!on) { lprof_uninstall(RUN.L); return MP_OBJ_NEW_SMALL_INT(0); }
    return mp_obj_new_int(lprof_install(RUN.L, interval, lo, hi));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lua_profile_obj, 1, 4,
                                           mod_lua_profile);

// lua_stats([top]) -> (hz, frames, interval, totals, rows, srcs), or None when
// it is not installed -- None rather than empty, because "not measuring" and
// "measured nothing" are different answers.
//
//   totals = (samples, us, calls, c_calls, shim_samples, shim_us,
//             shim_calls, dropped, slots_used, pinned)
//   rows   = ((srcidx, linedefined, calls, samples, us), ...), by samples
//
// MICROSECONDS on the wire and not ticks, unlike verb_stats above: these sums
// run for the whole window rather than one verb's turn in it, and a 32-bit
// cycle counter at 240MHz is 17.9 seconds. The first on-glass run of this
// reported `shim=-34%`, which is what that overflow looks like from the other
// end. `hz` is still reported, for the record and for the interval.
//
// The SPLIT is summed in C rather than derived from the rows, so it stays
// exact however few rows are asked for -- and `pinned` says whether it means
// anything at all.
// Ticks -> microseconds against the rate measured at install. Done HERE and
// not on the host so nothing that can wrap ever reaches the wire.
static uint32_t lprof_us(uint64_t ticks)
{
    if (!g_prof_hz) return 0;
    return (uint32_t)((ticks * 1000000u) / (uint64_t)g_prof_hz);
}

static mp_obj_t mod_lua_stats(size_t n_args, const mp_obj_t *args)
{
    int top = n_args > 0 ? mp_obj_get_int(args[0]) : 16;
    uint8_t taken[LPROF_SLOTS / 8];
    mp_obj_t rows[64], srcs[LPROF_SRCS], tot[10], out[6];
    int i, n = 0;
    if (!RUN.open || !g_lp_on || !g_lp) return mp_const_none;
    if (top > 64) top = 64;
    if (top < 0) top = 0;
    memset(taken, 0, sizeof taken);
    // A selection rather than a sort: the table is 256 slots and `top` is
    // small, so this is cheaper than ordering rows nobody asked for -- and it
    // bounds the allocation the host pays for, which matters on a board whose
    // cart barely fits its heap in the first place.
    while (n < top) {
        int best = -1;
        mp_obj_t t[5];
        int s;
        for (i = 0; i < LPROF_SLOTS; i++) {
            if (!g_lp[i].src) continue;
            if (taken[i >> 3] & (1u << (i & 7))) continue;
            if (g_lp[i].samples == 0 && g_lp[i].calls == 0) continue;
            if (best < 0 || g_lp[i].samples > g_lp[best].samples
                    || (g_lp[i].samples == g_lp[best].samples
                        && g_lp[i].calls > g_lp[best].calls))
                best = i;
        }
        if (best < 0) break;
        taken[best >> 3] |= (uint8_t)(1u << (best & 7));
        for (s = 0; s < g_lp_nsrc; s++)
            if (g_lp_src[s] == g_lp[best].src) break;
        t[0] = MP_OBJ_NEW_SMALL_INT(s < g_lp_nsrc ? s : -1);
        t[1] = mp_obj_new_int((mp_int_t)g_lp[best].line);
        t[2] = mp_obj_new_int_from_uint(g_lp[best].calls);
        t[3] = mp_obj_new_int_from_uint(g_lp[best].samples);
        t[4] = mp_obj_new_int_from_uint(lprof_us(g_lp[best].cycles));
        rows[n++] = mp_obj_new_tuple(5, t);
    }
    for (i = 0; i < g_lp_nsrc; i++)
        srcs[i] = mp_obj_new_str(g_lp_srcname[i], strlen(g_lp_srcname[i]));
    tot[0] = mp_obj_new_int_from_uint(g_lp_samples);
    tot[1] = mp_obj_new_int_from_uint(lprof_us(g_lp_cycles));
    tot[2] = mp_obj_new_int_from_uint(g_lp_calls);
    tot[3] = mp_obj_new_int_from_uint(g_lp_ccalls);
    tot[4] = mp_obj_new_int_from_uint(g_lp_shim_s);
    tot[5] = mp_obj_new_int_from_uint(lprof_us(g_lp_shim_cy));
    tot[6] = mp_obj_new_int_from_uint(g_lp_shim_n);
    tot[7] = mp_obj_new_int_from_uint(g_lp_dropped);
    tot[8] = mp_obj_new_int_from_uint((uint32_t)g_lp_used);
    tot[9] = mp_obj_new_bool(g_lp_cart != NULL);
    out[0] = mp_obj_new_int((mp_int_t)g_prof_hz);
    out[1] = mp_obj_new_int((mp_int_t)g_lp_frames);
    out[2] = mp_obj_new_int((mp_int_t)g_lp_interval);
    out[3] = mp_obj_new_tuple(10, tot);
    out[4] = mp_obj_new_tuple(n, rows);
    out[5] = mp_obj_new_tuple(g_lp_nsrc, srcs);
    return mp_obj_new_tuple(6, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lua_stats_obj, 0, 1,
                                           mod_lua_stats);

// lua_reset() -- zero the counters and the frame count, keeping the hook and
// the pin. The host samples by resetting, waiting and reading, so the WINDOW
// is the host's to choose.
static mp_obj_t mod_lua_reset(void)
{
    lprof_zero();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lua_reset_obj, mod_lua_reset);

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

static mp_obj_t mod_lua_gc_mode(size_t n_args, const mp_obj_t *args)
{
    int mode = n_args > 0 ? mp_obj_get_int(args[0]) : -1;
    int a = n_args > 1 ? mp_obj_get_int(args[1]) : -1;
    int b = n_args > 2 ? mp_obj_get_int(args[2]) : -1;
    int c = n_args > 3 ? mp_obj_get_int(args[3]) : -1;
    mp_obj_t out[3];
    g_gc_mode = mode;
    g_gc_a = a; g_gc_b = b; g_gc_c = c;
    if (!RUN.open) return mp_const_none;
    lua_gc_apply(RUN.L, mode, a, b, c);
    // GCCOUNT, never GCCOLLECT: this is read DURING a measurement window and a
    // full collect here would be the thing that made the next frame cheap.
    out[0] = mp_obj_new_int(lua_gc(RUN.L, LUA_GCCOUNT));
    out[1] = mp_obj_new_bool(lua_gc(RUN.L, LUA_GCISRUNNING));
    out[2] = mp_obj_new_bool(g_gc_gen);
    return mp_obj_new_tuple(3, out);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_lua_gc_mode_obj, 0, 4,
                                           mod_lua_gc_mode);

// -- the module surface ------------------------------------------------------

// run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h,
//           snap, audio_q, pmem_bytes, cfg, flags)
//
// Builds the console and opens the VM with libmoy's verb table -- and STOPS.
// The cart is loaded by load() afterwards, because between the two the host
// registers its extension verbs, and a cart captures its globals into locals
// as it executes. Doing both here left no window for that, which is how the
// first version ended up needing a second runtime for carts using layers.
//
// Everything is a buffer the console already owns: the framebuffer the
// compositor presents, the sheet and tilemap the project holds, the snapshot
// and queue the loop refreshes. Nothing is copied and nothing is allocated
// here except the VM.
static mp_obj_t mod_run_begin(size_t n_args, const mp_obj_t *a)
{
    if (n_args != 13) mp_raise_TypeError(MP_ERROR_TEXT("run_begin: 13 args"));
    if (RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                               MP_ERROR_TEXT("moycore: a run is already open"));
    memset(&RUN, 0, sizeof(RUN));
    // #211's two meters belong to the RUN, so they start here and survive
    // close() -- the report is read at the exit boundary, after the VM is gone.
    g_sram_free_min = SIZE_MAX;
    g_psram_fallback = 0;

    size_t fblen = 0;
    moy_pixel *fb = (moy_pixel *)buf_w(a[0], &fblen);
    int w = mp_obj_get_int(a[1]), h = mp_obj_get_int(a[2]);
    if (w <= 0 || h <= 0 || fblen < (size_t)w * (size_t)h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: framebuffer too small"));
    moy_canvas_init(&RUN.canvas, fb, w, h);
#ifdef MOY_PIXEL_RGB565
    if (a[3] != mp_const_none) {
        size_t wlen = 0;
        const uint16_t *wire = (const uint16_t *)buf_r(a[3], &wlen);
        if (wlen < MOY_PALETTE * 2)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: wire table too small"));
        moy_canvas_wire(&RUN.canvas, wire);
    }
#endif

    if (a[4] != mp_const_none) {
        size_t slen = 0;
        RUN.sheet.pix = (uint8_t *)buf_r(a[4], &slen);
        if (slen < (size_t)MOY_SHEET_W * MOY_SHEET_H)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: sheet too small"));
    }
    if (a[5] != mp_const_none) {
        size_t mlen = 0;
        RUN.map.cells = (uint8_t *)buf_r(a[5], &mlen);
        RUN.map.w = mp_obj_get_int(a[6]);
        RUN.map.h = mp_obj_get_int(a[7]);
        if (RUN.map.w < 0 || RUN.map.h < 0
            || (size_t)RUN.map.w * (size_t)RUN.map.h > mlen)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: map too small"));
    }

    size_t snlen = 0;
    RUN.snap = (int32_t *)buf_w(a[8], &snlen);
    if (snlen < SNAP_LEN * sizeof(int32_t))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: snapshot too small"));
    size_t aqlen = 0;
    RUN.aq = (int16_t *)buf_w(a[9], &aqlen);
    RUN.aq_cap = aqlen / sizeof(int16_t);
    if (RUN.aq_cap < 1 + AQ_SLOTS)
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: audio queue too small"));
    RUN.aq[0] = 0;

    if (a[10] != mp_const_none) {                 // pmem image in, 256 int32
        size_t plen = 0;
        const int32_t *p = (const int32_t *)buf_r(a[10], &plen);
        size_t n = plen / sizeof(int32_t);
        if (n > 256) n = 256;
        memcpy(RUN.pmem, p, n * sizeof(int32_t));
    }
    RUN.cfg = (a[11] == mp_const_none) ? MP_OBJ_NULL : a[11];

    // SPEC.md 3.5 tile flags, COPIED rather than borrowed -- the one buffer
    // here that is not the caller's. The sheet, the map and the framebuffer
    // are Python-owned and handed over for the life of the run because they
    // are large and live in PSRAM; this is 512 bytes, it is written from C
    // (fset, the p8 shim's __moy_map_flags, a poke to 0x3000) and it must
    // survive a caller that passes a plain immutable `bytes`. A short blob
    // leaves the rest zero, exactly as a short flags.moyflags does.
    memset(g_map_flags, 0, sizeof(g_map_flags));
    if (a[12] != mp_const_none) {
        size_t flen = 0;
        const uint8_t *fp = buf_r(a[12], &flen);
        if (flen > sizeof(g_map_flags)) flen = sizeof(g_map_flags);
        memcpy(g_map_flags, fp, flen);
    }

    RUN.con.canvas = &RUN.canvas;
    RUN.con.sheet  = RUN.sheet.pix ? &RUN.sheet : NULL;
    RUN.con.map    = RUN.map.cells ? &RUN.map : NULL;
    // SEED IT. libmoy's rnd is xorshift32 over con->rng and treats 0 as "use
    // the golden-ratio constant" -- correct, deterministic, and therefore the
    // SAME sequence on every run of every cart. moy_lua never showed this
    // because its prelude shadowed rnd with Lua's math.random, which Lua 5.4
    // seeds per state; moving a cart to moycore made sakura's petals fall the
    // same way twice. SPEC.md 9 fixes rnd's range and explicitly not its
    // sequence (no conformance scene may call it), so the seed is a host
    // quality choice and this is the quality we want.
    RUN.con.rng    = (uint32_t)mp_hal_ticks_us();
    if (RUN.con.rng == 0) RUN.con.rng = 1;
    moy_host *hs = &RUN.con.host;
    hs->user = NULL;
    hs->btn = h_btn;  hs->btnp = h_btnp;  hs->players = h_players;
    hs->time_ms = h_time_ms;
    hs->pmem_get = h_pmem_get;  hs->pmem_set = h_pmem_set;
    hs->sfx = h_sfx;  hs->music = h_music;  hs->beep = h_beep;
    hs->music_stop = h_music_stop;  hs->sound_stop = h_sound_stop;
    hs->volume = h_volume;
    hs->touch = h_touch;  hs->key = h_key;  hs->keyp = h_keyp;
    hs->textmode = h_textmode;  hs->quit = h_quit;
    hs->cfg = h_cfg;

    RUN.L = lua_newstate(l_alloc, NULL);
    if (RUN.L == NULL) mp_raise_msg(&mp_type_MemoryError,
                                    MP_ERROR_TEXT("moycore: no VM"));
    if (moy_lua_open(RUN.L, &RUN.con) != 0) {
        lua_close(RUN.L); RUN.L = NULL;
#if MOYCORE_POOL
        pool_release();              // a run that never opened still owns chunks
#endif
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: sandbox failed"));
    }
    // The p8 shim's helpers, installed before anything the cart can capture.
    // Plain globals, not verb shadows: the shim's own map() picks them up
    // nil-safely and the console's map() is untouched.
    lua_pushcfunction(RUN.L, l_map_masked);
    lua_setglobal(RUN.L, "__moy_map_masked");
    lua_pushcfunction(RUN.L, l_map_flags);
    lua_setglobal(RUN.L, "__moy_map_flags");
    // ONE table: libmoy's fget/fset/map(..., layers) and the p8 shim's masked
    // walk read the same 512 bytes, seeded above from the cart's file. The
    // shim's __moy_map_flags(gff) overwrites it at cart boot, which is what a
    // p8 import wants -- its flags ride in the shim, not in a sidecar.
    RUN.con.flags = g_map_flags;
    // The PICO-8 machine, opened for every run that registered its buffers
    // (p8_memory): the shim probes for it, a moy cart never sees the globals
    // it does not ask for. No buffers means no machine and the shim's sparse
    // table -- never a failed run.
    if (g_p8mem) moy_p8_open(RUN.L, &RUN.con, &g_p8, g_p8mem, g_p8rom);

    g_prof_on = 0;               // a new VM: the old wrappers went with the old one
    g_prof_n = 0;
    lprof_forget();              // ...and the old one's hook died with it
    g_gc_gen = 0;                // a fresh lua_State is incremental
    RUN.open = 1;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_run_begin_obj, 13, 13, mod_run_begin);

// Run one chunk. Shared by exec() and load(); the only difference between them
// is whether _init follows.
static mp_obj_t run_chunk(mp_obj_t src_obj, mp_obj_t name_obj)
{
    size_t srclen = 0;
    const char *src = mp_obj_str_get_data(src_obj, &srclen);
    const char *name = mp_obj_str_get_str(name_obj);
    if (luaL_loadbuffer(RUN.L, src, srclen, name) != LUA_OK
        || lua_pcall(RUN.L, 0, 0, 0) != LUA_OK) {
        const char *msg = lua_tostring(RUN.L, -1);
        return mp_obj_new_str(msg ? msg : "load failed",
                              strlen(msg ? msg : "load failed"));
    }
    return mp_const_none;
}

// exec(src, chunkname) -> None, or the error text. A chunk that is NOT the
// cart: the glue PRELUDE, whose Lua-side wrappers are how object-valued verbs
// reach a cart at all.
//
// They cannot be register()ed, and that is a property of the boundary rather
// than a gap here: a registered verb marshals numbers, strings, booleans, nil
// and tuples, so `make_layer` -- which returns a Layer OBJECT -- comes back as
// "unsupported value" and the whole cart falls back to the trampoline runtime.
// moy_lua has always solved this the same way (int-handle registries plus Lua
// wrappers that hide them), so moycore runs the SAME prelude rather than
// growing an object marshaller. The placement verbs of #85/#109 took that
// route too (#214) and needed nothing here: a scene crosses as one STRING,
// which push_mp_to_lua already carried. Hence a chunk verb: the prelude has to
// execute after register() and before the cart, which is exactly the window
// load() closes.
static mp_obj_t mod_exec(mp_obj_t src_obj, mp_obj_t name_obj)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    return run_chunk(src_obj, name_obj);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_exec_obj, mod_exec);

// load(src, chunkname) -> None, or the error text. Runs the cart chunk and its
// _init. Call AFTER any register()s and any exec()s.
static mp_obj_t mod_load(mp_obj_t src_obj, mp_obj_t name_obj)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    // Before the chunk, because the chunk is where the p8 shim resolves its
    // verbs and captures them: wrapping after it would leave every draw call
    // the cart makes going to the unwrapped original.
    if (g_prof_arm) prof_install(RUN.L);
    mp_obj_t err_obj = run_chunk(src_obj, name_obj);
    if (err_obj != mp_const_none) return err_obj;
    // AFTER the chunk, unlike the verb profiler above and for the opposite
    // reason: the Lua profiler captures nothing at load, but its shim pin
    // reads the shim's own `_draw`, which does not exist until the chunk that
    // defines it has run.
    if (g_lp_arm) lprof_install(RUN.L, g_lp_arm_iv, g_lp_arm_lo, g_lp_arm_hi);
    char err[192];
    // _init runs here, before any tick has stamped the frame base, and it may
    // call time(). Stamp it now so the elapsed term starts from zero instead
    // of from whatever the counter last held.
    g_tick_ms = (uint32_t)mp_hal_ticks_ms();
    if (moy_lua_init(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    // The parse burst is over, and it is the run's high-water mark: a 130KB
    // source becomes tokens, short strings and tables that are all garbage by
    // the time _init returns. Nothing else collects here -- Lua's collector is
    // incremental and moy_lua_init does not ask -- so the burst would sit in
    // the pool until the cart's own churn walked it out, one step at a time,
    // holding chunks the frame loop is about to need. One full collect, once,
    // before the first frame; on moss moss it is the difference between the
    // cart loading and `not enough memory`.
    lua_gc(RUN.L, LUA_GCCOLLECT);
    // AFTER that collect, never before: an armed `stop` has to not apply to
    // the load burst, which is the run's high-water mark and the one thing
    // that must still be collected.
    if (g_gc_mode >= 0) lua_gc_apply(RUN.L, g_gc_mode, g_gc_a, g_gc_b, g_gc_c);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_load_obj, mod_load);

// gc() -> the VM's heap in KB after a FULL collect, or None with no run.
//
// A DIAGNOSTIC and a test verb, not a frame-loop one: a full collect is the
// stop-the-world kind, and the cart's own churn is what the incremental
// collector is tuned for. It exists because `collectgarbage` is one of the
// names SPEC.md 4.1 takes AWAY from a cart, so nothing else can ask the VM to
// settle -- which is exactly what tests/test_moycore_pool.py has to do to see
// a burst's garbage die and its chunks go back.
static mp_obj_t mod_gc(void)
{
    if (!RUN.open) return mp_const_none;
    lua_gc(RUN.L, LUA_GCCOLLECT);
    return mp_obj_new_int(lua_gc(RUN.L, LUA_GCCOUNT));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_gc_obj, mod_gc);

// tick(dt[, draw]) -> None on a clean frame, else the error text.
//
// The whole frame: reset the per-frame draw state (SPEC.md's rule that draw
// state must not leak between frames or from host UI into a cart), then
// _update and -- unless `draw` is false -- _draw. A logic-only tick is the
// Player's scheduler (#217) skipping _draw on a tick its divisor does not
// draw, which SPEC.md 5 sanctions; TICK_DRAW in the module table is how the
// glue knows this build takes the flag. The host refreshed the snapshot before
// calling and drains the audio queue after.
// The last tick's two halves, in microseconds. The loop's own clock cannot see
// them any more: it times `update()` and `draw()`, and moycore runs BOTH inside
// update(), so the diag's logic/render split read `logic = the whole frame,
// render = 0`. That is not a small cosmetic problem -- every per-cart number
// this project has recorded since #67 is a logic/render pair, and comparing the
// new lump against them reads as a doubling of logic that never happened.
// Timed here in C, where the halves still exist, and handed back for the loop
// to attribute.
static uint32_t g_upd_us, g_draw_us;

// -- the hardware performance counters (perf_counters / perf_stats / perf_reset)
//
// WHAT THIS IS FOR, and it is one question. Four levers have been tried on the
// S3 tick and all four measured NULL -- -O3 on the raster kernels, -O3 on the
// VM core, the whole VM core in IRAM, and the Lua heap's SRAM floor swept to
// both ends (#66, #77). The conclusion drawn from them is that the tick is
// bound by MEMORY rather than by the instructions it retires, and that
// conclusion is what closes the door on a JIT, on opcode fusion and on every
// other code-generation idea. But four nulls are an ELIMINATION, not a
// measurement: they are equally consistent with "the thing you changed was not
// the bottleneck". The ledger even holds both readings at once -- 2026-08-10
// says celeste's residue "is instruction-count -- interpreter dispatch at
// 240MHz", 2026-09-08 says the tick is bound by "the cart's DATA in PSRAM".
//
// IPC settles it, and nothing else does. Retired instructions over cycles,
// across a cart's own update and draw:
//
//   IPC high  -- the core is retiring most cycles: fewer or cheaper
//                instructions win, and the code-generation chapter re-opens.
//   IPC low   -- the core is STALLED most cycles: no code generator helps, and
//                the levers are allocation rate, data layout and cache
//                residency. That closes the chapter with a number.
//
// Counter 0 is always CYCLES, because every reading here is a ratio against
// it; counter 1 is whatever is being asked about, and the default is retired
// instructions. The Xtensa part has exactly two (XCHAL_NUM_PERF_COUNTERS), so
// a wider question is a sweep of paired runs rather than one capture -- which
// is also why the selector is an argument and not a constant.
//
// It is its OWN switch, like `verbs` and `luaprof` and for the same reason: an
// ordinary diag session must not arm it. Disarmed, `tick` tests one byte per
// frame and touches no register.
#if defined(__XTENSA__) && defined(__has_include)
#  if __has_include("eri.h") && __has_include("xtensa-debug-module.h")
#    include "eri.h"
#    include "xtensa-debug-module.h"
#    define MOY_PMU 1          // ERI: two counters, both selectable
#  endif
#elif defined(__riscv)
#  define MOY_PMU 2            // the standard CSRs, cycles and retired only
#endif
#ifndef MOY_PMU
#  define MOY_PMU 0            // the host, and the wasm head: no counters
#endif

#if MOY_PMU == 2
static inline uint32_t pm_csr_cycle(void)
{
    uint32_t v; __asm__ volatile ("csrr %0, mcycle" : "=r"(v)); return v;
}
static inline uint32_t pm_csr_instret(void)
{
    uint32_t v; __asm__ volatile ("csrr %0, minstret" : "=r"(v)); return v;
}
#endif

#define PM_UPD  0
#define PM_DRAW 1

static uint8_t  g_pm_on;              // counting, and `tick` is bracketing
static uint16_t g_pm_sel = 2;         // XTPERF_CNT_INSN
static uint16_t g_pm_mask = 0xffff;   // every subset of it
static uint32_t g_pm_frames;
static uint64_t g_pm_cyc[2];          // [PM_UPD], [PM_DRAW]
static uint64_t g_pm_evt[2];

// Both counters, as close together as the ISA allows. 32 bits and they wrap --
// at 240MHz the cycle count turns over every ~17.9s -- but every use below is
// an unsigned DIFFERENCE inside one frame, which wrapping leaves correct.
static inline void pm_read(uint32_t *cyc, uint32_t *evt)
{
#if MOY_PMU == 1
    *cyc = eri_read(ERI_PERFMON_PM0);
    *evt = eri_read(ERI_PERFMON_PM0 + (int)sizeof(int32_t));
#elif MOY_PMU == 2
    *cyc = pm_csr_cycle();
    *evt = pm_csr_instret();
#else
    *cyc = 0; *evt = 0;
#endif
}

#if MOY_PMU == 1
// tracelevel < 0 and kernelcnt 0 is the IDF's own "count everything" pair
// (xtensa_perfmon_apis.c): counting is gated on CINTLEVEL <= tracelevel, so
// the widest value counts interrupt context too -- which belongs in the
// measurement, because it is time the cart's frame actually spends.
static void pm_program(int id, uint16_t sel, uint16_t mask)
{
    uint32_t pmc = ((uint32_t)(0xf & PMCTRL_TRACELEVEL_MASK)
                        << PMCTRL_TRACELEVEL_SHIFT)
                 | ((uint32_t)(sel & PMCTRL_SELECT_MASK) << PMCTRL_SELECT_SHIFT)
                 | ((uint32_t)(mask & PMCTRL_MASK_MASK) << PMCTRL_MASK_SHIFT);
    eri_write(ERI_PERFMON_PM0 + id * (int)sizeof(int32_t), 0);
    eri_write(ERI_PERFMON_PMCTRL0 + id * (int)sizeof(int32_t), pmc);
}
#endif

// Does this silicon actually count? The counters are architecturally optional
// and a core that does not implement them reads a frozen zero rather than
// faulting, so the answer is taken by LOOKING: read, spend a little time, read
// again. A frozen counter reports as absent, which is the project's rule for a
// lever a board does not have -- None, never 0.
//
// IT HAS TO START THEM TO ASK. A stopped counter and an absent one read
// identically, so the probe arms cycles, samples, and puts PGM back exactly as
// it found it -- otherwise the first `perf_counters(1)` answers "no counters"
// about hardware that has two, which is what it did.
static int8_t g_pm_have = -1;          // -1 not yet asked, 0 no, 1 yes

static int pm_alive(void)
{
    if (g_pm_have >= 0) return g_pm_have;
#if MOY_PMU == 0
    g_pm_have = 0;
#else
    {
        uint32_t c0, e0, c1, e1, spin = 0;
#if MOY_PMU == 1
        uint32_t pgm = eri_read(ERI_PERFMON_PGM);
        pm_program(0, 0, 0xffff);                  /* XTPERF_CNT_CYCLES */
        eri_write(ERI_PERFMON_PGM, PGM_PMEN);
#endif
        pm_read(&c0, &e0);
        while (spin < 2000u) spin++;
        pm_read(&c1, &e1);
#if MOY_PMU == 1
        eri_write(ERI_PERFMON_PGM, pgm);           /* as we found it */
#endif
        (void)e0; (void)e1;
        g_pm_have = (int8_t)((c1 - c0) != 0u);
    }
#endif
    return g_pm_have;
}

static mp_obj_t mod_tick(size_t n_args, const mp_obj_t *args)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    char err[192];
    uint32_t t0, t1;
    int draw = n_args < 2 || mp_obj_is_true(args[1]);
    moy_reset_state(&RUN.canvas);
    float dt = (float)mp_obj_get_float(args[0]);
    g_tick_ms = (uint32_t)mp_hal_ticks_ms();   // h_time_ms counts from here
    // The sample clock re-bases per FRAME. Without this the gap from the last
    // sample of one frame to the first of the next -- the whole of the host's
    // Python frame, flush included -- is charged to whichever cart function
    // happened to be running when the tick ended.
    if (g_lp_on) g_lp_t0 = PROF_NOW();
    // The counters bracket the cart's OWN halves and nothing else: not the
    // reset above, not the error paths, not the host's frame around this call.
    // A frame that errors out is not counted at all -- half a tick would move
    // the ratio without being a tick.
    uint32_t pc0 = 0, pe0 = 0, pc1 = 0, pe1 = 0, pc2 = 0, pe2 = 0;
    if (g_pm_on) pm_read(&pc0, &pe0);
    t0 = (uint32_t)mp_hal_ticks_us();
    if (moy_lua_update(RUN.L, dt, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    t1 = (uint32_t)mp_hal_ticks_us();
    if (g_pm_on) pm_read(&pc1, &pe1);
    if (draw && moy_lua_draw(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    if (g_pm_on) {
        pm_read(&pc2, &pe2);
        g_pm_cyc[PM_UPD]  += (uint64_t)(uint32_t)(pc1 - pc0);
        g_pm_evt[PM_UPD]  += (uint64_t)(uint32_t)(pe1 - pe0);
        if (draw) {
            g_pm_cyc[PM_DRAW] += (uint64_t)(uint32_t)(pc2 - pc1);
            g_pm_evt[PM_DRAW] += (uint64_t)(uint32_t)(pe2 - pe1);
        }
        g_pm_frames++;
    }
    g_prof_frames++;
    g_lp_frames++;
    g_upd_us = t1 - t0;
    g_draw_us = draw ? (uint32_t)mp_hal_ticks_us() - t1 : 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_tick_obj, 1, 2, mod_tick);

// tick_split() -> (update_us, draw_us) for the last tick. Microseconds, not the
// loop's milliseconds: a cart frame this project cares about is single-digit ms
// and a 1ms tick would quantise the split into uselessness.
static mp_obj_t mod_tick_split(void)
{
    mp_obj_t t[2];
    t[0] = mp_obj_new_int((mp_int_t)g_upd_us);
    t[1] = mp_obj_new_int((mp_int_t)g_draw_us);
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_tick_split_obj, mod_tick_split);

// pmem_image(out) -> dirty flag. The host persists at boundaries (#66), so it
// asks for the image rather than being told about every poke.
static mp_obj_t mod_pmem_image(mp_obj_t out)
{
    size_t len = 0;
    int32_t *p = (int32_t *)buf_w(out, &len);
    size_t n = len / sizeof(int32_t);
    if (n > 256) n = 256;
    memcpy(p, RUN.pmem, n * sizeof(int32_t));
    int d = RUN.pmem_dirty;
    RUN.pmem_dirty = 0;
    return mp_obj_new_bool(d);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_pmem_image_obj, mod_pmem_image);

// retarget(fb) -- the double/triple-buffered tiers swap the framebuffer under
// the canvas every frame, exactly as DeviceCanvas.sync_back does.
static mp_obj_t mod_retarget(mp_obj_t fb_obj)
{
    if (!RUN.open) return mp_const_none;
    size_t len = 0;
    moy_pixel *fb = (moy_pixel *)buf_w(fb_obj, &len);
    if (len < (size_t)RUN.canvas.w * (size_t)RUN.canvas.h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("retarget: framebuffer too small"));
    RUN.canvas.pix = fb;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_retarget_obj, mod_retarget);

static mp_obj_t mod_close(void)
{
    if (RUN.L) lua_close(RUN.L);
    RUN.L = NULL;
#if MOYCORE_POOL
    // AFTER lua_close, never before: until it returns, the chunks still hold
    // live Lua objects. Nothing may survive a run -- a cart that churned its
    // way to twenty chunks must not keep them while the launcher is up.
    pool_release();
#endif
    RUN.open = 0;
    g_prof_on = 0;               // the wrappers died with the VM
    g_prof_n = 0;
    lprof_forget();              // the hook went with it; the table is ours
    RUN.snap = NULL;
    RUN.aq = NULL;
    RUN.cfg = MP_OBJ_NULL;
    MP_STATE_VM(moycore_calls) = MP_OBJ_NULL;   // un-root: the gc may reclaim
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_close_obj, mod_close);

// get_global(name) -- read a cart global. The parity suites compare a Lua
// cart's state against its Python twin's, which needs a way in; libmoy's
// binding owns the VM but not the host's curiosity about it.
static mp_obj_t mod_get_global(mp_obj_t name_obj)
{
    if (!RUN.open) return mp_const_none;
    lua_getglobal(RUN.L, mp_obj_str_get_str(name_obj));
    mp_obj_t out = mp_const_none;
    switch (lua_type(RUN.L, -1)) {
    case LUA_TBOOLEAN:
        out = lua_toboolean(RUN.L, -1) ? mp_const_true : mp_const_false;
        break;
    case LUA_TNUMBER:
        if (lua_isinteger(RUN.L, -1))
            out = mp_obj_new_int((mp_int_t)lua_tointeger(RUN.L, -1));
        else
            out = mp_obj_new_float((mp_float_t)lua_tonumber(RUN.L, -1));
        break;
    case LUA_TSTRING: {
        size_t len = 0;
        const char *sp = lua_tolstring(RUN.L, -1, &len);
        out = mp_obj_new_str(sp, len);
        break;
    }
    default:
        break;                       // tables/functions stay Lua-side
    }
    lua_pop(RUN.L, 1);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_global_obj, mod_get_global);

// view() -> (w, h) as the cart last declared it, or None.
//
// SPEC.md 6 made view a core verb that RECORDS on the console whether or not
// the host takes a callback, which means the host can read it after the tick
// instead of being called during one. So moybyte's view() costs zero crossings
// now: libmoy answers the cart, the console reads the answer here, and the WM
// composites accordingly. That is the whole shape the spec's host interface was
// built for, and it only became available because the verb moved into core.
static mp_obj_t mod_view(void)
{
    if (!RUN.open || RUN.con.view_w <= 0) return mp_const_none;
    mp_obj_t t[2];
    t[0] = mp_obj_new_int(RUN.con.view_w);
    t[1] = mp_obj_new_int(RUN.con.view_h);
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_view_obj, mod_view);

static mp_obj_t mod_active(void) { return mp_obj_new_bool(RUN.open); }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_active_obj, mod_active);

// set_sram_floor(kb) -> the effective kb. Clamped [16, 256]; returns the
// compiled default unchanged on a build with no region to choose between, so
// callers can set it unconditionally. run_desktop calls this once the boot-time
// internal claims (flush bounce, poller, audio reserve) have been taken.
static mp_obj_t mod_set_sram_floor(mp_obj_t kb_obj)
{
#ifdef MOYCORE_PSRAM
    mp_int_t kb = mp_obj_get_int(kb_obj);
    if (kb < 16) kb = 16;
    if (kb > 256) kb = 256;
    g_sram_floor = (size_t)kb * 1024;
    return mp_obj_new_int(kb);
#else
    (void)kb_obj;
    return mp_obj_new_int(MOYCORE_SRAM_FLOOR / 1024);
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_set_sram_floor_obj, mod_set_sram_floor);

// alloc_stats() -> (sram_live, psram_live, peak, sram_denied,
//                   pool_live, pool_cap, pool_chunks)
//
// The first four say whether the SRAM-first policy took: how the cart's heap
// actually SPLIT, its high-water mark, and how many allocations the floor
// pushed to PSRAM. They count the bytes LUA ASKED FOR, pool blocks included --
// a pool block is charged to region 1 because chunks are PSRAM-only.
//
// The last three are the pool itself, and they are separate rather than folded
// in because they measure a different thing: pool_live is the CLASS bytes of
// the live blocks (so it rounds each request up to its class), pool_cap is the
// usable bytes across every chunk, and their difference is the pool's slack --
// free lists, the un-carved tail of the current chunk, and the <=255 bytes each
// earlier chunk abandoned. So the PSRAM the VM actually holds is
// (psram_live - pool_live) + pool_cap, and psram_live alone would understate it.
//
// Live counters, not a sample -- read them while a cart runs. All seven read
// zero after close(), which is the leak check tests/test_moycore_pool.py makes.
//
// Off-board there is one region and mc_region calls it region 1, so sram_live
// and sram_denied are always 0 there; the tuple is not None, because the
// desktop MicroPython is where the pool's accounting is actually tested.
static mp_obj_t mod_alloc_stats(void)
{
    mp_obj_t items[7];
    items[0] = mp_obj_new_int((mp_int_t)g_live_r[0]);
    items[1] = mp_obj_new_int((mp_int_t)g_live_r[1]);
    items[2] = mp_obj_new_int((mp_int_t)g_peak);
    items[3] = mp_obj_new_int((mp_int_t)g_sram_denied);
#if MOYCORE_POOL
    items[4] = mp_obj_new_int((mp_int_t)g_pool_live);
    items[5] = mp_obj_new_int((mp_int_t)g_pool_cap);
    items[6] = mp_obj_new_int((mp_int_t)g_chunk_n);
#else
    items[4] = items[5] = items[6] = MP_OBJ_NEW_SMALL_INT(0);
#endif
    return mp_obj_new_tuple(7, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_alloc_stats_obj, mod_alloc_stats);

// sram_report() -> (sram_free_min, psram_fallback, floor), or None on a tier
// whose allocator has one region to choose from (#211).
//
// What the RUN just had, not what the session accumulated: run_begin resets
// both meters, close() does not, so the exit boundary reads the run that ended.
// `psram_fallback` is the field that matters -- a BOOLEAN about a regime
// change, because a cart that outgrows the floor does not fail, it silently
// starts allocating from PSRAM and gets about twice as slow (#67).
//
// None, never a zeroed tuple, where the concept does not apply: a Python cart
// has no Lua allocator and the host has no second region, and both must stay
// distinguishable from a run that fitted with nothing to spare.
static mp_obj_t mod_sram_report(void)
{
#ifndef MOYCORE_PSRAM
    if (!g_sram_sim) return mp_const_none;
#endif
    mp_obj_t t[3];
    t[0] = (g_sram_free_min == SIZE_MAX) ? mp_const_none
                                         : mp_obj_new_int((mp_int_t)g_sram_free_min);
    t[1] = mp_obj_new_bool(g_psram_fallback);
    t[2] = mp_obj_new_int((mp_int_t)g_sram_floor);
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_sram_report_obj, mod_sram_report);

#ifndef MOYCORE_PSRAM
// sram_sim(bytes) -- arm the host's simulated internal-SRAM region, 0 to
// disarm; returns what is armed. The board reads that figure from the heap and
// this file is the only place the floor arithmetic lives, so this is how the
// fallback flag and the low-water mark are driven where tests run. Absent from
// every firmware build.
static mp_obj_t mod_sram_sim(mp_obj_t bytes_obj)
{
    g_sram_sim = (size_t)mp_obj_get_int(bytes_obj);
    return mp_obj_new_int((mp_int_t)g_sram_sim);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_sram_sim_obj, mod_sram_sim);
#endif

// pool_check() -> 0 when the pool's own invariants hold, else a negative code
// (README.md lists them). A TEST verb: the invariant that a block masks back
// to the chunk it came from cannot be seen from Python and does not fault when
// it breaks -- it writes through another chunk's header instead.
static mp_obj_t mod_pool_check(void)
{
#if MOYCORE_POOL
    return MP_OBJ_NEW_SMALL_INT(pool_check());
#else
    return MP_OBJ_NEW_SMALL_INT(0);
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_pool_check_obj, mod_pool_check);

// perf_counters(on[, sel[, mask]]) -> the state it is in now, or None where
// there is no counter to arm. `sel`/`mask` choose counter 1 and are the Xtensa
// XTPERF_CNT_*/XTPERF_MASK_* numbers; the RISC-V side has only the two fixed
// CSRs and ignores them, which `perf_stats` reports rather than hides.
static mp_obj_t mod_perf_counters(size_t n_args, const mp_obj_t *args)
{
    int on = mp_obj_is_true(args[0]);
    if (!pm_alive()) return mp_const_none;
    if (n_args > 1) g_pm_sel = (uint16_t)mp_obj_get_int(args[1]);
    if (n_args > 2) g_pm_mask = (uint16_t)mp_obj_get_int(args[2]);
#if MOY_PMU == 1
    if (on) {
        eri_write(ERI_PERFMON_PGM, 0);
        pm_program(0, 0, 0xffff);              // XTPERF_CNT_CYCLES
        pm_program(1, g_pm_sel, g_pm_mask);
        eri_write(ERI_PERFMON_PGM, PGM_PMEN);
    } else {
        eri_write(ERI_PERFMON_PGM, 0);
    }
#endif
    g_pm_on = (uint8_t)(on ? 1 : 0);
    if (!g_prof_hz) g_prof_hz = prof_calibrate();
    return mp_obj_new_bool(on);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_perf_counters_obj, 1, 3,
                                           mod_perf_counters);

// perf_stats() -> (hz, frames, upd_cyc, upd_evt, draw_cyc, draw_evt, sel,
// mask, selectable), or None where there is no counter. The two halves are
// kept apart because the corpus splits that way: moss moss is update-bound and
// dank tomb draw-bound, and one IPC over both would average the answer away.
static mp_obj_t mod_perf_stats(void)
{
    mp_obj_t t[9];
    if (!pm_alive()) return mp_const_none;
    t[0] = mp_obj_new_int((mp_int_t)(g_prof_hz ? g_prof_hz : prof_calibrate()));
    t[1] = mp_obj_new_int((mp_int_t)g_pm_frames);
    t[2] = mp_obj_new_int_from_ull(g_pm_cyc[PM_UPD]);
    t[3] = mp_obj_new_int_from_ull(g_pm_evt[PM_UPD]);
    t[4] = mp_obj_new_int_from_ull(g_pm_cyc[PM_DRAW]);
    t[5] = mp_obj_new_int_from_ull(g_pm_evt[PM_DRAW]);
    t[6] = mp_obj_new_int((mp_int_t)g_pm_sel);
    t[7] = mp_obj_new_int((mp_int_t)g_pm_mask);
    t[8] = mp_obj_new_bool(MOY_PMU == 1);
    return mp_obj_new_tuple(9, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_perf_stats_obj, mod_perf_stats);

static mp_obj_t mod_perf_reset(void)
{
    g_pm_frames = 0;
    g_pm_cyc[0] = g_pm_cyc[1] = g_pm_evt[0] = g_pm_evt[1] = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_perf_reset_obj, mod_perf_reset);

static const mp_rom_map_elem_t moycore_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_OBJ_NEW_QSTR(MP_QSTR_moycore) },
    { MP_ROM_QSTR(MP_QSTR_run_begin),   MP_ROM_PTR(&mod_run_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_p8_memory), MP_ROM_PTR(&mod_p8_memory_obj) },
    { MP_ROM_QSTR(MP_QSTR_register),    MP_ROM_PTR(&mod_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_exec),        MP_ROM_PTR(&mod_exec_obj) },
    { MP_ROM_QSTR(MP_QSTR_load),        MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick),        MP_ROM_PTR(&mod_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_TICK_DRAW),   MP_ROM_INT(1) },
    { MP_ROM_QSTR(MP_QSTR_tick_split),  MP_ROM_PTR(&mod_tick_split_obj) },
    { MP_ROM_QSTR(MP_QSTR_profile),     MP_ROM_PTR(&mod_profile_obj) },
    { MP_ROM_QSTR(MP_QSTR_verb_stats),  MP_ROM_PTR(&mod_verb_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_verb_reset),  MP_ROM_PTR(&mod_verb_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_profile), MP_ROM_PTR(&mod_lua_profile_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_stats),   MP_ROM_PTR(&mod_lua_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_reset),   MP_ROM_PTR(&mod_lua_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_gc_mode), MP_ROM_PTR(&mod_lua_gc_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_counters), MP_ROM_PTR(&mod_perf_counters_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_stats),  MP_ROM_PTR(&mod_perf_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_reset),  MP_ROM_PTR(&mod_perf_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_pmem_image),  MP_ROM_PTR(&mod_pmem_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_retarget),    MP_ROM_PTR(&mod_retarget_obj) },
    { MP_ROM_QSTR(MP_QSTR_close),       MP_ROM_PTR(&mod_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_gc),          MP_ROM_PTR(&mod_gc_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),      MP_ROM_PTR(&mod_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_sram_floor), MP_ROM_PTR(&mod_set_sram_floor_obj) },
    { MP_ROM_QSTR(MP_QSTR_alloc_stats), MP_ROM_PTR(&mod_alloc_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_sram_report), MP_ROM_PTR(&mod_sram_report_obj) },
#ifndef MOYCORE_PSRAM
    { MP_ROM_QSTR(MP_QSTR_sram_sim),    MP_ROM_PTR(&mod_sram_sim_obj) },
#endif
    { MP_ROM_QSTR(MP_QSTR_pool_check), MP_ROM_PTR(&mod_pool_check_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_global),  MP_ROM_PTR(&mod_get_global_obj) },
    { MP_ROM_QSTR(MP_QSTR_view),        MP_ROM_PTR(&mod_view_obj) },
    // The snapshot layout, exported so the Python side cannot drift from it.
    { MP_ROM_QSTR(MP_QSTR_SNAP_LEN),    MP_ROM_INT(SNAP_LEN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTN),    MP_ROM_INT(SNAP_BTN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTNP),   MP_ROM_INT(SNAP_BTNP) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTN_P1), MP_ROM_INT(SNAP_BTN_P1) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_BTNP_P1), MP_ROM_INT(SNAP_BTNP_P1) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_PLAYERS), MP_ROM_INT(SNAP_PLAYERS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TIME_MS), MP_ROM_INT(SNAP_TIME_MS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_X), MP_ROM_INT(SNAP_TOUCH_X) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_Y), MP_ROM_INT(SNAP_TOUCH_Y) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_DOWN), MP_ROM_INT(SNAP_TOUCH_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TOUCH_MS), MP_ROM_INT(SNAP_TOUCH_MS) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_KEY),    MP_ROM_INT(SNAP_KEY) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_KEY_DOWN), MP_ROM_INT(SNAP_KEY_DOWN) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_TEXTMODE), MP_ROM_INT(SNAP_TEXTMODE) },
    { MP_ROM_QSTR(MP_QSTR_SNAP_QUIT),   MP_ROM_INT(SNAP_QUIT) },
    // ...and the audio queue's.
    { MP_ROM_QSTR(MP_QSTR_AQ_SLOTS),    MP_ROM_INT(AQ_SLOTS) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MAX),      MP_ROM_INT(AQ_MAX) },
    { MP_ROM_QSTR(MP_QSTR_AQ_SFX),      MP_ROM_INT(AQ_SFX) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MUSIC),    MP_ROM_INT(AQ_MUSIC) },
    { MP_ROM_QSTR(MP_QSTR_AQ_BEEP),     MP_ROM_INT(AQ_BEEP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_MUSIC_STOP), MP_ROM_INT(AQ_MUSIC_STOP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_SOUND_STOP), MP_ROM_INT(AQ_SOUND_STOP) },
    { MP_ROM_QSTR(MP_QSTR_AQ_VOLUME),   MP_ROM_INT(AQ_VOLUME) },
};
static MP_DEFINE_CONST_DICT(moycore_globals, moycore_globals_table);

const mp_obj_module_t moycore_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moycore_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moycore, moycore_user_cmodule);

// The registered Python callables, held against the gc for the run's lifetime:
// the Lua closures reference them only by INDEX, which the collector cannot
// see. Cleared at close().
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_calls);
// The PICO-8 machine's Python-owned buffers (p8_memory), kept alive here.
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_p8mem);
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_p8rom);
