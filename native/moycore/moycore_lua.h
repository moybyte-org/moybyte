// moycore_lua: a Lua cart's VM over the console, with no MicroPython in it
// (docs/kernel_cartpath_2026-10.md §1, the modmoycore.c split).
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
// One run at a time: `moycore_RUN` is it.

#ifndef MOYCORE_LUA_H
#define MOYCORE_LUA_H

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

typedef struct {
    lua_State  *L;               // the cart's VM; NULL for a compiled cart
    int         wasm;            // a compiled cart's session is open
    moycore_run_t c;             // the console (moycore_run.h)
    moycore_layers layers;       // the cart's layers' canvases (moycore_layers.h)
    int         open;
} moycore_run;

extern moycore_run moycore_RUN;

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
// -1 with the reason in `err`.
int moycore_lua_open(char *err, size_t n);
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

// One frame of the open run, whichever its runtime: the draw state reset,
// time()'s base stamped, then the Lua frame above or a compiled cart's two
// hooks on its engine's thread. 0, or -1 with the cart's error in `err`.
// Defined by the binding (modmoycore.c), where a compiled cart's session is.
int moycore_frame(float dt, int draw, char *err, size_t n);

// The p8 machine's buffers (65536 and 0x4300 bytes), or NULLs: no machine.
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
