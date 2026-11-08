// moycore_lua: a Lua cart's VM over the console, with no MicroPython in it
// (docs/history/kernel_cartpath_2026-10.md §1, the modmoycore.c split).
//
// The cart's lua_State opened over moycore_run.c's console with libmoy's verb
// table, the p8 machine, the layer glue and the native superset; its chunks
// run as text; _init, then frames of _update and _draw; and the close. The
// VM's allocator lives here too: SRAM first above a floor, a small-object pool
// in PSRAM, and the census of both (README.md has the pool's design).
//
// What stays in modmoycore.c is the binding: the buffers Python hands over,
// register()'s trampolines into Python, and the profilers' surfaces. The
// profilers reach a load and a frame through the binding's hooks
// (moycore_lua_hooks), which do nothing while no profiler is armed.
//
// THE RUNS ARE ROWS: several runs are open at once, each a row of its own --
// its VM, its console, its layers, its paint images and scenes, its p8
// machine and its OWNER -- and `moycore_RUN` is the CURRENT row, the one
// every call below acts on. A caller makes its row current
// (moycore_run_use) before it calls in; a row is current on the VM's task
// alone, so nothing switches it under a call. The rows are MOYCORE_RUNS at
// most, each held only while its run is (moycore_run_new, moycore_run_end),
// internal SRAM first on a board, where the verbs read the console; while no
// run is, the current row is an idle one in PSRAM, made at the first ask and
// kept, so a call with no run reads zeros and the idle desk holds no row.
#ifndef MOYCORE_LUA_H
#define MOYCORE_LUA_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "lua.h"

#include "moy.h"
#include "moycore_layers.h"
#include "moycore_run.h"

// MOYCORE_POOL=0 compiles the small-object pool out (the A/B; per-board
// verdicts do not transfer).
#ifndef MOYCORE_POOL
#define MOYCORE_POOL 1
#endif

#ifndef MOYCORE_RUNS
#define MOYCORE_RUNS 4          // the owner tags a run takes: cart, cart2..cart4
#endif
#if MOYCORE_RUNS > 8
#error "a run's handle carries its slot in three bits"
#endif

typedef struct moycore_run {
    lua_State  *L;               // the cart's VM; NULL for a compiled cart
    int         wasm;            // a compiled cart's session is open
    struct moycore_wrun *wr;     // a compiled run's state (moycore_wasm.c), or NULL
    moycore_run_t c;             // the console (moycore_run.h)
    moycore_layers layers;       // the cart's layers' canvases (moycore_layers.h)
    int         open;
    // The run's own media and machine (moycore_lua.c).
    moy_p8      p8;              // the PICO-8 machine's state
    uint8_t    *p8mem, *p8rom;   // its buffers, the caller's (moycore_lua_p8_memory)
    void       *imgs;            // the paint images' IMAGE table (moy_htab_t)
    void       *lay;             // the layers made here, `nlay` of `caplay`
    int         nlay, caplay;
    void       *scenes;          // the .moyscene texts (moycore_scenes_t)
    uint32_t    owner;           // its OWNER row (moycore_lua_owner), or 0
    uint32_t    proc;            // its process row's handle (the binding's), or 0
    uint16_t    gen;             // its run's number, which its handle carries
    uint8_t     gc_gen;          // the collector is generational
    uint8_t     slot;            // its index, 0 .. MOYCORE_RUNS - 1
} moycore_run;

// The current row, NULL before the first ask; moycore_RUN reads it.
extern moycore_run *moycore_run_row;
moycore_run *moycore_run_first(void);
static inline moycore_run *moycore_row(void)
{
    return moycore_run_row != NULL ? moycore_run_row : moycore_run_first();
}
#define moycore_RUN (*moycore_row())

// A row for a new run, made current: the current one when it holds no run,
// else a free one. NULL when MOYCORE_RUNS are open, or with no memory.
moycore_run *moycore_run_new(void);
// `r` made the current row, its console the one libmoy's callbacks read.
void moycore_run_use(moycore_run *r);
// Row `slot`, or NULL where none is made.
moycore_run *moycore_run_at(int slot);
// A row's run as a handle (its number and slot, never 0), and the row a
// handle names while that run holds it, else NULL: a binding's name for a run.
uint32_t moycore_run_handle(const moycore_run *r);
moycore_run *moycore_run_of(uint32_t h);
// The rows holding a run (open, a VM or a compiled session).
int moycore_run_count(void);
// After a row's run has closed: the row zeroed, and a row past the first
// given back. The first row is current again when `r` was.
void moycore_run_end(moycore_run *r);
// The Lua heap of row `r` in bytes (Lua's own count, exact), 0 for none.
size_t moycore_run_heap(const moycore_run *r);

// What the profilers do around a load and a frame: the binding's table
// (modmoycore.c), constant, so it costs no RAM. A frame's three hooks share
// `s`, four words on the frame's stack.
typedef struct {
    void (*load_begin)(lua_State *L);   // before the first chunk
    void (*load_end)(lua_State *L);     // after the last, before _init
    void (*frame_begin)(uint32_t *s);   // before _update
    void (*frame_mid)(uint32_t *s);     // between _update and _draw
    void (*frame_end)(int drew, uint32_t *s);   // after the frame, when it raised nothing
} moycore_lua_hooks_t;

extern const moycore_lua_hooks_t moycore_lua_hooks;

// The VM opened over moycore_RUN.c, whose console the caller has built: 0, or
// -1 with the reason in `err`. `app` is the app's run (native/moy_app/
// moy_app_run.h), which the state copies and holds its role names over, or
// NULL for a run with none (a game).
struct moy_app_run;
int moycore_lua_open(const struct moy_app_run *app, char *err, size_t n);
// One chunk, as TEXT only (a binary chunk can reach outside the VM): 0, or -1
// with Lua's message in `err`.
int moycore_lua_exec(const char *src, size_t len, const char *name, char *err, size_t n);
// The load's two brackets: begin before the cart's first chunk, finish after
// its last (it runs _init, then settles the collector). 0, or -1 with `err`.
void moycore_lua_load_begin(void);
int moycore_lua_load_finish(char *err, size_t n);
// One frame: _update, then _draw unless `draw` is 0. 0, or -1 with `err`.
int moycore_lua_tick(float dt, int draw, char *err, size_t n);
// The frame's halves are the console's: moycore_run_split.
// The VM closed and the pool given back. Safe with no VM.
void moycore_lua_close(void);
// A digest of the cart's world: every global that is data (numbers by their
// bits, strings, booleans, tables three deep), order-independent so table
// layout cannot move it; functions and userdata are not the world. 0 with
// no VM. The lockstep desync check's Lua half (native/moy_play/moy_match.h).
uint32_t moycore_lua_digest(void);

// The runaway watch (native/moy_play/moy_play.h): from the watcher's task,
// make the open Lua run raise at its next instruction, `what` after its
// `chunk:N:` position; and, on the run's own task once the frame is over,
// take the hook off again. Both safe with no VM.
void moycore_lua_stuck(const char *what);
void moycore_lua_unstuck(void);

// One frame of the open run, whichever its runtime: the draw state reset,
// time()'s base stamped, then the Lua frame above or a compiled cart's two
// hooks on its engine's thread. 0, or -1 with the cart's error in `err`.
// Defined by the binding (modmoycore.c; a compiled cart's session is
// moycore_wasm.c's).
int moycore_frame(float dt, int draw, char *err, size_t n);

// The open compiled run's frame hand-off (libmoy/moy_wasm.h's `frame`), for
// the kernel's front (moy_play.c). moycore_wasm_take sets which layouts its
// blits leave in the cart's memory (MOYCORE_TAKE_565 blit565's words,
// MOYCORE_TAKE_PALETTE blit's indices); moycore_wasm_owed answers the frame
// owed, or NULL, a palette frame's colours in `*lut` (NULL for blit565's),
// and in `*kept` whether the canvas lacks the last frame shown, which the
// copy the board named holds; moycore_wasm_presented says the board showed
// the owed frame, `kept` its copy (NULL: the canvas holds it);
// moycore_wasm_settle writes the owed frame, or the kept one, into the
// canvas. Defined by the binding that runs compiled carts (moycore_wasm.c,
// the host's moyhost_wasm.c); weak where a build has none.
#define MOYCORE_TAKE_565 1
#define MOYCORE_TAKE_PALETTE 2
void moycore_wasm_take(int take);
const uint8_t *moycore_wasm_owed(const uint16_t **lut, bool *kept);
void moycore_wasm_presented(const uint8_t *kept);
void moycore_wasm_settle(void);

// The open run's frame is past the runaway watch's budget: from the
// watcher's task, end it -- a Lua run raises `what` at its next instruction
// (moycore_lua_stuck), a compiled cart is terminated and stops at its next
// import call. Defined by the binding, where the session is.
void moycore_stuck(const char *what);

// A paint image's .moyimg text, copied, for the run's image(name) (decoded
// at its first call, moycore_lua.c's __image_handle): 0, or -1 when the run
// holds its 256 or there is no memory. The run's close frees them.
int moycore_lua_image_put(const char *name, const char *text, size_t n);

// A scene's .moyscene text, copied, for the run's scene verbs
// (moycore_scene.h), in the order the cart's scenes are kept, the first the
// default active one: 0, or -1 past MOYCORE_SCENES or with no memory. Put
// again by name, it replaces. The run's close frees them.
// Layer `i`'s pixels and size, as the cart made it (its handle): 0, or -1
// for no such layer. What a harness reads a layer's drawing through.
int moycore_lua_layer(int i, moy_pixel **pix, int *w, int *h);

// The next run's OWNER row (native/moy_glass/moy_buf.h), set by the kernel's
// Player at its launch, which the run's row takes when it is made: the run's
// layer and image pixels are BUF rows on loan to it. 0 leaves them plain
// allocations.
void moycore_lua_owner(uint32_t owner);

int moycore_lua_scene_put(const char *name, const char *text, size_t n);

// The p8 machine's buffers (65536 and 0x4300 bytes), or NULLs: no machine.
// The next moycore_lua_open's: its row holds them from then, so two runs at
// once are handed two pairs.
void moycore_lua_p8_memory(uint8_t *mem, uint8_t *rom);

// The collector: `mode` as lua_gc_mode() spells it (-1 read only, 0 stop,
// 1 restart, 2 incremental, 3 generational), armed for the next load and
// applied to the live VM.
void moycore_lua_gc_arm(int mode, int a, int b, int c);
int moycore_lua_gc_generational(void);

// The allocator's meters (modmoycore.c's alloc_stats and sram_report).
typedef struct {
    size_t sram_live, psram_live, peak;
    uint32_t sram_denied;
    size_t pool_live, pool_cap;
    uint32_t pool_chunks;
    size_t sram_free_min;           // SIZE_MAX: nothing allocated this run
    uint8_t psram_fallback;
    size_t sram_floor;
} moycore_lua_meters_t;

void moycore_lua_meters(moycore_lua_meters_t *out);
// A run's two meters start again (the run's, read at its exit).
void moycore_lua_meters_reset(void);
// The internal-SRAM floor in bytes, clamped to [16, 256] KiB on a board with
// two regions; the compiled default where there is one region.
size_t moycore_lua_set_sram_floor(size_t bytes);
// Whether this tier has two regions to choose from (a board, or the host's
// simulated split armed by moycore_lua_sram_sim).
int moycore_lua_two_regions(void);
// The host's simulated internal region in bytes (0 disarms); what is armed.
size_t moycore_lua_sram_sim(size_t bytes);
// 0 when the pool's invariants hold, else README.md's negative code.
int moycore_lua_pool_check(void);

#endif // MOYCORE_LUA_H
