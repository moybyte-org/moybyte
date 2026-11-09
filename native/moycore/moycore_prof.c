// moycore's profilers: the per-verb profiler (`profile`, `verb_stats`) and
// the per-function Lua profiler (`lua_profile`, `lua_stats`), and the Lua
// collector's mode (`lua_gc_mode`).

#include "modmoycore_int.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define PROF_ALLOC(n) heap_caps_calloc(1, (n), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
#else
#define PROF_ALLOC(n) calloc(1, (n))
#endif

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

// The counters, PSRAM made at the first install and kept: a meter asked for
// twice a year holds no internal RAM until it is.
typedef struct { uint32_t calls; uint32_t ticks; uint32_t self; } prof_slot_t;
static prof_slot_t *g_prof;
#define PROF_BYTES (sizeof(prof_slot_t) * PROF_MAX)
static uint32_t g_prof_child;     // ticks charged to callees of the running verb
uint32_t moycore_prof_frames;    // ticks since the reset, for per-frame means
uint32_t moycore_prof_hz;        // profiler ticks per second, measured
int      moycore_prof_n;         // slots in use
uint8_t  moycore_prof_arm;       // install on the next load()
uint8_t  moycore_prof_on;        // wrappers are on the live VM

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
uint32_t moycore_prof_calibrate(void)
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
    if (!moycore_prof_on) return;
    top = lua_gettop(L);
    lua_getfield(L, LUA_REGISTRYINDEX, PROF_NAMES);
    lua_getfield(L, LUA_REGISTRYINDEX, PROF_ORIG);
    for (i = 0; i < moycore_prof_n; i++) {
        lua_rawgeti(L, top + 1, i + 1);           // the name
        lua_rawgeti(L, top + 2, i + 1);           // the original
        lua_setglobal(L, lua_tostring(L, top + 3));
        lua_settop(L, top + 2);
    }
    lua_settop(L, top);
    lua_pushnil(L); lua_setfield(L, LUA_REGISTRYINDEX, PROF_NAMES);
    lua_pushnil(L); lua_setfield(L, LUA_REGISTRYINDEX, PROF_ORIG);
    moycore_prof_on = 0;
    moycore_prof_n = 0;
}

// Walk _G, collect every wrappable C-function global, then wrap them. TWO
// passes because Lua forbids adding a key to a table under traversal, and a
// name-by-name walk of a fixed list would miss exactly what matters: the p8
// shim RESOLVES its verbs at load (`spr = __moy_p8_spr or spr`) and assigns
// them to globals, so the name a cart calls is not the name libmoy registered.
void moycore_prof_install(lua_State *L)
{
    int n = 0, i, top;
    if (moycore_prof_on) return;
    if (g_prof == NULL && (g_prof = PROF_ALLOC(PROF_BYTES)) == NULL) return;
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

    memset(g_prof, 0, PROF_BYTES);
    g_prof_child = 0;
    moycore_prof_frames = 0;
    moycore_prof_n = n;
    if (!moycore_prof_hz) moycore_prof_hz = moycore_prof_calibrate();
    moycore_prof_on = 1;
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
    moycore_prof_arm = (uint8_t)on;
    if (!RUN.L) return mp_const_none;
    if (on) moycore_prof_install(RUN.L);
    else prof_uninstall(RUN.L);
    return mp_obj_new_int(moycore_prof_n);
}
MP_DEFINE_CONST_FUN_OBJ_1(moycore_profile_obj, mod_profile);

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
    if (!RUN.L || !moycore_prof_on) return mp_const_none;
    lua_getfield(RUN.L, LUA_REGISTRYINDEX, PROF_NAMES);
    for (i = 0; i < moycore_prof_n; i++) {
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
    out[0] = mp_obj_new_int((mp_int_t)moycore_prof_hz);
    out[1] = mp_obj_new_int((mp_int_t)moycore_prof_frames);
    out[2] = mp_obj_new_tuple(n, rows);
    return mp_obj_new_tuple(3, out);
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_verb_stats_obj, mod_verb_stats);

// verb_reset() -- zero the counters and the frame count. The host samples by
// resetting, waiting and reading, so the WINDOW is the host's to choose rather
// than a cadence baked in here.
static mp_obj_t mod_verb_reset(void)
{
    if (g_prof != NULL) memset(g_prof, 0, PROF_BYTES);
    g_prof_child = 0;
    moycore_prof_frames = 0;
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_verb_reset_obj, mod_verb_reset);

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
uint32_t moycore_lp_t0;                      // the previous sample's clock
uint32_t moycore_lp_frames;
static int      g_lp_interval;
static int      g_lp_used;
uint8_t  moycore_lp_on;
uint8_t  moycore_lp_arm;                     // install at the next load()
int      moycore_lp_arm_iv;
int32_t  moycore_lp_arm_lo, moycore_lp_arm_hi;

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
        uint32_t dt = now - moycore_lp_t0;
        moycore_lp_t0 = now;
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
    moycore_lp_frames = 0;
    moycore_lp_t0 = PROF_NOW();
}

// Drop the table and the flags WITHOUT touching the VM -- close() has already
// destroyed it by the time this runs, and a sethook on a freed lua_State is
// the kind of crash that reads as "the profiler broke the console".
void moycore_lprof_forget(void)
{
    if (g_lp) { free(g_lp); g_lp = NULL; }
    moycore_lp_on = 0;
    g_lp_cart = NULL;
}

static void lprof_uninstall(lua_State *L)
{
    if (L && moycore_lp_on) lua_sethook(L, NULL, 0, 0);
    moycore_lprof_forget();
}

int moycore_lprof_install(lua_State *L, int interval, int32_t lo, int32_t hi)
{
    int mask;
    if (moycore_lp_on) lprof_uninstall(L);
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
    if (!moycore_prof_hz) moycore_prof_hz = moycore_prof_calibrate();
    lua_sethook(L, lprof_hook, mask, interval > 0 ? interval : 0);
    moycore_lp_on = 1;
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
    moycore_lp_arm = (uint8_t)on;
    moycore_lp_arm_iv = interval;
    moycore_lp_arm_lo = lo;
    moycore_lp_arm_hi = hi;
    if (!RUN.L) return mp_const_none;
    if (!on) { lprof_uninstall(RUN.L); return MP_OBJ_NEW_SMALL_INT(0); }
    return mp_obj_new_int(moycore_lprof_install(RUN.L, interval, lo, hi));
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_profile_obj, 1, 4,
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
    if (!moycore_prof_hz) return 0;
    return (uint32_t)((ticks * 1000000u) / (uint64_t)moycore_prof_hz);
}

static mp_obj_t mod_lua_stats(size_t n_args, const mp_obj_t *args)
{
    int top = n_args > 0 ? mp_obj_get_int(args[0]) : 16;
    uint8_t taken[LPROF_SLOTS / 8];
    mp_obj_t rows[64], srcs[LPROF_SRCS], tot[10], out[6];
    int i, n = 0;
    if (!RUN.open || !moycore_lp_on || !g_lp) return mp_const_none;
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
    out[0] = mp_obj_new_int((mp_int_t)moycore_prof_hz);
    out[1] = mp_obj_new_int((mp_int_t)moycore_lp_frames);
    out[2] = mp_obj_new_int((mp_int_t)g_lp_interval);
    out[3] = mp_obj_new_tuple(10, tot);
    out[4] = mp_obj_new_tuple(n, rows);
    out[5] = mp_obj_new_tuple(g_lp_nsrc, srcs);
    return mp_obj_new_tuple(6, out);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_stats_obj, 0, 1,
                                    mod_lua_stats);

// lua_reset() -- zero the counters and the frame count, keeping the hook and
// the pin. The host samples by resetting, waiting and reading, so the WINDOW
// is the host's to choose.
static mp_obj_t mod_lua_reset(void)
{
    lprof_zero();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_lua_reset_obj, mod_lua_reset);

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

static mp_obj_t mod_lua_gc_mode(size_t n_args, const mp_obj_t *args)
{
    int mode = n_args > 0 ? mp_obj_get_int(args[0]) : -1;
    int a = n_args > 1 ? mp_obj_get_int(args[1]) : -1;
    int b = n_args > 2 ? mp_obj_get_int(args[2]) : -1;
    int c = n_args > 3 ? mp_obj_get_int(args[3]) : -1;
    mp_obj_t out[3];
    moycore_lua_gc_arm(mode, a, b, c);
    if (!RUN.L) return mp_const_none;
    // GCCOUNT, never GCCOLLECT: this is read DURING a measurement window and a
    // full collect here would be the thing that made the next frame cheap.
    out[0] = mp_obj_new_int(lua_gc(RUN.L, LUA_GCCOUNT));
    out[1] = mp_obj_new_bool(lua_gc(RUN.L, LUA_GCISRUNNING));
    out[2] = mp_obj_new_bool(moycore_lua_gc_generational());
    return mp_obj_new_tuple(3, out);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_gc_mode_obj, 0, 4,
                                    mod_lua_gc_mode);
