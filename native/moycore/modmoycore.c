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
// Verbs moybyte adds ON TOP of the spec -- layers/images, scenes, tables,
// texts, view() -- are not IMPLEMENTED here, and they do not need to be: they
// are REGISTERED here, as Lua globals backed by the same Python closures they
// always had (register() below).
//
// That distinction is the whole design, and getting it wrong cost a rewrite:
// EVERY Lua cart runs here -- libmoy's table first, extra verbs on top as
// trampolines. A cart needing a Python-backed verb needs ONE engine that can
// hold one, never a second runtime (the first cut shipped two and the
// deletion commit records what that cost).

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
#ifdef MOYCORE_PSRAM
static size_t g_sram_floor = MOYCORE_SRAM_FLOOR;
// The census the floor is set from: live bytes per region, the peak, and how
// often the floor sent an allocation to PSRAM. Deliberately four numbers where
// moy_lua reports sixteen -- the size-class buckets were for CHOOSING the
// policy and the policy is chosen; these are for confirming it took.
static size_t g_live_r[2], g_live_total, g_peak;
static uint32_t g_sram_denied;

static inline int mc_region(const void *p)
{
#ifdef MOYCORE_PTR_REGION
    return esp_ptr_internal(p) ? 0 : 1;
#else
    (void)p;                     // no way to ask: report everything as PSRAM
    return 1;
#endif
}
#endif

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

// The VM's allocator: the system heap, NOT MicroPython's gc heap. That is the
// point -- a cart's whole Lua world stays outside what MP sweeps, so cart churn
// cannot lengthen a shell collect.
//
// INTERNAL SRAM FIRST on the boards, which is not a preference but a measured
// requirement: moy_lua found the all-PSRAM version made the S3's whole _update
// about twice as slow, because the VM's hot working set (the Lua stack, the
// cart's TValue arrays) is latency-bound and the S3's PSRAM is a 120MHz OCT
// bus. Same 48KB headroom floor so the WiFi/DMA pools cannot be starved, and
// the same PSRAM fallback so a big cart still loads, just slower. Off-board
// (host, unix pin, wasm) this compiles down to plain realloc.
static void *l_alloc(void *ud, void *ptr, size_t osize, size_t nsize)
{
    (void)ud;
#ifdef MOYCORE_PSRAM
    if (ptr == NULL) osize = 0;      // lua_Alloc contract: a type tag, not a size
    if (nsize == 0) {
        if (ptr != NULL) {
            g_live_r[mc_region(ptr)] -= osize;
            g_live_total -= osize;
            free(ptr);
        }
        return NULL;
    }
    void *np = NULL;
    int tried_sram = 0;
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
            >= nsize + g_sram_floor) {
        tried_sram = 1;
        np = heap_caps_realloc(ptr, nsize, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (np == NULL) {
        if (!tried_sram) g_sram_denied++;   // the FLOOR turned it away, not a
        np = heap_caps_realloc(ptr, nsize,  // failed internal allocation
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (np != NULL) {
        // A realloc may MOVE the block between regions, so the old bytes come
        // off whichever region the old ADDRESS was in and the new bytes go on
        // wherever the block landed. Reading the region of a pointer realloc
        // has already released is safe: it is an address-range comparison, not
        // a dereference.
        if (ptr != NULL) {
            g_live_r[mc_region(ptr)] -= osize;
            g_live_total -= osize;
        }
        g_live_r[mc_region(np)] += nsize;
        g_live_total += nsize;
        if (g_live_total > g_peak) g_peak = g_live_total;
    }
    return np;                       // NULL: Lua runs an emergency GC and retries
#else
    (void)osize;                     // no region to account for off-board
    if (nsize == 0) { free(ptr); return NULL; }
    return realloc(ptr, nsize);
#endif
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

// MOY_FLAGS wide since 2026-09: libmoy's own fget/fset/map(..., layers)
// read the console's table (SPEC.md 3.5), and this is that table here.
static uint8_t g_map_flags[MOY_FLAGS];

// The PICO-8 machine (libmoy moy_p8.c): 64KB of memory and a ROM snapshot,
// reseeded per run by moy_p8_open. The buffers are PYTHON-OWNED bytearrays
// handed over through p8_memory(), like the framebuffer, the sheet and the
// map -- NOT taken from the ESP heap. The S3 boards' MicroPython heap owns
// all but ~1.5KB of the PSRAM region, so an 81KB heap_caps_malloc there took
// the last of Lua's own PSRAM fallback and every Lua cart died with "not
// enough memory" (2026-09-02, both S3 boards, first flash of this machine).
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

// -- the module surface ------------------------------------------------------

// run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h,
//           snap, audio_q, pmem_bytes, cfg)
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
    if (n_args != 12) mp_raise_TypeError(MP_ERROR_TEXT("run_begin: 12 args"));
    if (RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                               MP_ERROR_TEXT("moycore: a run is already open"));
    memset(&RUN, 0, sizeof(RUN));

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
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: sandbox failed"));
    }
    // The p8 shim's helpers, installed before anything the cart can capture.
    // Plain globals, not verb shadows: the shim's own map() picks them up
    // nil-safely and the console's map() is untouched.
    lua_pushcfunction(RUN.L, l_map_masked);
    lua_setglobal(RUN.L, "__moy_map_masked");
    lua_pushcfunction(RUN.L, l_map_flags);
    lua_setglobal(RUN.L, "__moy_map_flags");
    memset(g_map_flags, 0, sizeof(g_map_flags));
    RUN.con.flags = g_map_flags;
    // The PICO-8 machine, opened for every run that registered its buffers
    // (p8_memory): the shim probes for it, a moy cart never sees the globals
    // it does not ask for. No buffers means no machine and the shim's sparse
    // table -- never a failed run.
    if (g_p8mem) moy_p8_open(RUN.L, &RUN.con, &g_p8, g_p8mem, g_p8rom);

    RUN.open = 1;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_run_begin_obj, 12, 12, mod_run_begin);

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
// growing an object marshaller. Hence a chunk verb: the prelude has to execute
// after register() and before the cart, which is exactly the window load()
// closes.
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
    mp_obj_t err_obj = run_chunk(src_obj, name_obj);
    if (err_obj != mp_const_none) return err_obj;
    char err[192];
    // _init runs here, before any tick has stamped the frame base, and it may
    // call time(). Stamp it now so the elapsed term starts from zero instead
    // of from whatever the counter last held.
    g_tick_ms = (uint32_t)mp_hal_ticks_ms();
    if (moy_lua_init(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_load_obj, mod_load);

// tick(dt) -> None on a clean frame, else the error text.
//
// The whole frame: reset the per-frame draw state (SPEC.md's rule that draw
// state must not leak between frames or from host UI into a cart), then
// _update and _draw. The host refreshed the snapshot before calling and drains
// the audio queue after.
// The last tick's two halves, in microseconds. The loop's own clock cannot see
// them any more: it times `update()` and `draw()`, and moycore runs BOTH inside
// update(), so the diag's logic/render split read `logic = the whole frame,
// render = 0`. That is not a small cosmetic problem -- every per-cart number
// this project has recorded since #67 is a logic/render pair, and comparing the
// new lump against them reads as a doubling of logic that never happened.
// Timed here in C, where the halves still exist, and handed back for the loop
// to attribute.
static uint32_t g_upd_us, g_draw_us;

static mp_obj_t mod_tick(mp_obj_t dt_obj)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    char err[192];
    uint32_t t0, t1;
    moy_reset_state(&RUN.canvas);
    float dt = (float)mp_obj_get_float(dt_obj);
    g_tick_ms = (uint32_t)mp_hal_ticks_ms();   // h_time_ms counts from here
    t0 = (uint32_t)mp_hal_ticks_us();
    if (moy_lua_update(RUN.L, dt, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    t1 = (uint32_t)mp_hal_ticks_us();
    if (moy_lua_draw(RUN.L, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    g_upd_us = t1 - t0;
    g_draw_us = (uint32_t)mp_hal_ticks_us() - t1;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_tick_obj, mod_tick);

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
    RUN.open = 0;
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

// alloc_stats() -> (sram_live, psram_live, peak, sram_denied), or None where
// there is only one region. The four numbers that say whether the SRAM-first
// policy took: how the cart's heap actually SPLIT, its high-water mark, and how
// many allocations the floor pushed to PSRAM. Live counters, not a sample --
// read them while a cart runs.
static mp_obj_t mod_alloc_stats(void)
{
#ifdef MOYCORE_PSRAM
    mp_obj_t items[4];
    items[0] = mp_obj_new_int((mp_int_t)g_live_r[0]);
    items[1] = mp_obj_new_int((mp_int_t)g_live_r[1]);
    items[2] = mp_obj_new_int((mp_int_t)g_peak);
    items[3] = mp_obj_new_int((mp_int_t)g_sram_denied);
    return mp_obj_new_tuple(4, items);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_alloc_stats_obj, mod_alloc_stats);

static const mp_rom_map_elem_t moycore_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_OBJ_NEW_QSTR(MP_QSTR_moycore) },
    { MP_ROM_QSTR(MP_QSTR_run_begin),   MP_ROM_PTR(&mod_run_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_p8_memory), MP_ROM_PTR(&mod_p8_memory_obj) },
    { MP_ROM_QSTR(MP_QSTR_register),    MP_ROM_PTR(&mod_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_exec),        MP_ROM_PTR(&mod_exec_obj) },
    { MP_ROM_QSTR(MP_QSTR_load),        MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick),        MP_ROM_PTR(&mod_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick_split),  MP_ROM_PTR(&mod_tick_split_obj) },
    { MP_ROM_QSTR(MP_QSTR_pmem_image),  MP_ROM_PTR(&mod_pmem_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_retarget),    MP_ROM_PTR(&mod_retarget_obj) },
    { MP_ROM_QSTR(MP_QSTR_close),       MP_ROM_PTR(&mod_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),      MP_ROM_PTR(&mod_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_sram_floor), MP_ROM_PTR(&mod_set_sram_floor_obj) },
    { MP_ROM_QSTR(MP_QSTR_alloc_stats), MP_ROM_PTR(&mod_alloc_stats_obj) },
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
