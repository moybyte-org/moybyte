// Moybyte moy_lua: the device Lua cart runtime (#67 Phase 1).
//
// One vendored Lua 5.4 VM hosting ONE running cart (the console runs one cart
// at a time), bridged to the existing engine with the two-tier scheme the #6
// spike hardware-proved:
//
//   * HOT ops: `spr` is a C lua_CFunction that appends (tile, x, y, flip)
//     int16 quads STRAIGHT into DeviceCanvas._batch_arr -- the exact protocol
//     of moy_gfx's spr_gate (same header stamping via a canvas.begin_batch
//     upcall on run breaks, same clamps), so the existing frame machinery
//     (flush_batch -> blit_batch) drains Lua sprites with zero new plumbing
//     and the per-sprite cost is a C append, not a VM dispatch. And since
//     #189, the whole solid draw family (pix/rect/rectb/line/circ/circb/tri/
//     trib/print) goes libmoy-DIRECT on builds with moy_gfx beside this
//     module: bind_draw() swaps in lua_CFunctions that draw through the
//     canvas's shared DrawCtx (moy_gfx_capi.h), so those verbs never enter
//     Python at all -- the p8 shim's soft font is ~15 pix() per glyph, which
//     made per-call dispatch the T-Deck's whole render residual.
//   * EVERYTHING else: registered Python callables (the cart's make_api
//     closures) exposed as Lua globals through one generic trampoline --
//     100% semantic parity with Python carts because it IS the same closure.
//     Scalars cross the boundary (nil/bool/int/float/str); a tuple return
//     becomes MULTIPLE Lua returns (touch() -> x, y, tapped, held). Objects
//     (layers/images) never cross: the moy_runtime glue keeps them in a
//     Python-side registry and routes them through int-handle helpers.
//
// Memory: a custom lua_Alloc in PSRAM (heap_caps), so the cart's whole Lua
// heap lives OUTSIDE the MicroPython gc heap -- close() frees every byte and
// cart churn can never fragment the console's heap (#66). The spike measured
// the full bridged sakura at 41KB live / 3.73ms per frame.
//
// Safety: cart code always runs under lua_pcall (errors -> a Python
// RuntimeError the Player panels). Python upcalls from Lua C functions are
// nlr-protected: an MP exception is converted to a lua_error, never longjmps
// through Lua frames. The stdlib is the plan's safe subset (base minus
// load/dofile/loadfile + math + string + table) -- no io/os/require.

#include <string.h>
#include <stdio.h>
#include <stdlib.h>     // free/realloc -- the no-PSRAM lua_Alloc path (unix)
#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"
#include "py/unicode.h"        // utf8_check -- see lua_to_mp's LUA_TSTRING case

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

// #189 libmoy-direct draw verbs: when moy_gfx is staged BESIDE this module
// (both boards + the unix test build -- the wasm runner is the one that
// isn't), its exported C draw API turns pix/rect/rectb/line/circ/circb/tri/
// trib/print into lua_CFunctions that draw through the shared DrawCtx without
// ever entering Python. Probe by layout, not by port: the relative include
// resolves exactly where the API can exist.
#if defined(__has_include)
#if __has_include("../moy_gfx/moy_gfx_capi.h")
#include "../moy_gfx/moy_gfx_capi.h"
#include "py/mphal.h"          // mp_hal_ticks_us -- the ST_PROF-gated timers
#define MOY_LUA_DRAW_DIRECT 1
#endif
#endif

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define MOY_LUA_PSRAM 1
#endif
#if __has_include("esp_memory_utils.h")
#include "esp_memory_utils.h"   // esp_ptr_internal: which region a ptr is in
#endif
#endif

// ---------------------------------------------------------------------------
// module state
//
// Rooted MP objects live in one root-pointer list so the gc never collects a
// registered callable / the canvas / the batch array while a cart runs:
//   moy_lua_root = [canvas, sheet, batch_arr, callables_list]
// The lua_State itself is C/PSRAM memory -- invisible to the MP gc.

#define ROOT_CANVAS 0
#define ROOT_SHEET 1
#define ROOT_ARR 2
#define ROOT_CALLS 3
#define ROOT_CTX 4     // #189: the bound DrawCtx (or None) -- gc must keep it

static lua_State *g_L = NULL;
static size_t g_live = 0;
static size_t g_peak = 0;

#ifdef MOY_LUA_DRAW_DIRECT
// The bound DrawCtx pointer (NULL = no direct verbs; the trampolines stand).
// The OBJECT is rooted at ROOT_CTX so this raw pointer can never dangle.
static moy_gfx_draw_ctx_t *g_ctx = NULL;
// Liveness/attribution counters, DRAW3's three buckets: 0 fill (pix/rect/
// rectb), 1 shape (line/circ/circb/tri/trib), 2 text (print). Counts always;
// microseconds only while the ctx's ST_PROF flag is up (the ticks pair costs
// ~6us on the S3 -- real money against a 1x1 fill). g_dfb counts odd-shape
// falls back to the Python trampoline (pix's 2-arg read, most of all).
static uint32_t g_dn[3];
static uint32_t g_dus[3];
static uint32_t g_dfb = 0;
// #67 stage-1 (moycore): the C-side batch protocol -- run breaks stamped and
// pending runs flushed without entering Python (moy_gfx_capi_flush_batch,
// gated on the glue having registered the sheet via ctx.set_batch_src).
// g_bf/g_bs mirror the canvas's _batch_flushes/_batch_sprites for the C lane
// (the Python counters go quiet for a Lua cart -- DRAW2's `batch` reads 0
// there; batch_stats() is where the C lane reports). g_bus is ST_PROF-gated
// like g_dus. g_bup counts the upcall FALLBACKS (foreign-token runs) -- the
// on-glass proof the upcall protocol is actually gone is g_bf>0 with g_bup==0.
static uint32_t g_bf = 0;
static uint32_t g_bs = 0;
static uint32_t g_bus = 0;
static uint32_t g_bup = 0;
// Defined with the #189 draw verbs below; l_spr's run break calls it too.
static bool lua_batch_flush_c(lua_State *L);
#endif

#ifdef MOY_LUA_PSRAM
// #67 SRAM-vs-PSRAM attribution (2026-08-10): the allocator is SRAM-first,
// but nobody had ever measured what the cart's heap actually WINS -- how many
// live bytes sit internal, and whether they are the hot small objects (stack
// segments, table nodes: the all-PSRAM ~2x verdict's likely cause) or cold
// big ones (loaded protos, long strings). Any structural SRAM proposal (an
// indexed SRAM canvas would take ~77KB from this same pool) prices itself
// against these numbers. Region 0 = internal SRAM, 1 = PSRAM; size classes
// <=64 / <=256 / <=2048 / >2048 bytes.
static size_t g_live_r[2];
static uint32_t g_alloc_n[2];      // cumulative allocations landed per region
static size_t g_alloc_b[2];        // cumulative bytes landed per region
static size_t g_live_cls[2][4];    // live bytes, region x size class
static size_t g_sram_denied = 0;   // bytes the headroom floor pushed to PSRAM

static inline int moy_lua_cls(size_t n) {
    return n <= 64 ? 0 : n <= 256 ? 1 : n <= 2048 ? 2 : 3;
}

static inline int moy_lua_region(const void *p) {
#if __has_include("esp_memory_utils.h")
    return esp_ptr_internal(p) ? 0 : 1;
#else
    (void)p;
    return 1;
#endif
}

// The SRAM-first headroom floor, now a runtime knob (set_sram_floor). 48KB
// was sized to leave room for a WiFi stack that might start at any moment --
// but the census showed that on a 269KB internal heap it leaves celeste's Lua
// 9KB (97% PSRAM, the measured-2x regime). run_desktop lowers it to 24KB
// AFTER wifi autoconnect has run (whatever needed internal has taken it by
// then); the accepted edge is a FIRST wifi start mid-cart failing -- close
// the cart (its heap frees wholesale) and retry.
static size_t g_sram_floor = 48 * 1024;
#endif
static int16_t *g_q = NULL;      // _batch_arr int16 view (buffer is gc-pinned via root)
static size_t g_qlen = 0;        // in int16 slots
static int g_token = 0;

static void *moy_lua_alloc(void *ud, void *ptr, size_t osize, size_t nsize) {
    (void)ud;
    if (ptr == NULL) {
        osize = 0;               // lua_Alloc contract: osize is a type tag here
    }
    if (nsize == 0) {
        if (ptr != NULL) {
#ifdef MOY_LUA_PSRAM
            int rf = moy_lua_region(ptr);
            g_live_r[rf] -= osize;
            g_live_cls[rf][moy_lua_cls(osize)] -= osize;
#endif
            free(ptr);
        }
        g_live -= osize;
        return NULL;
    }
#ifdef MOY_LUA_PSRAM
    // Internal SRAM FIRST: the VM's hot working set (Lua stack, the cart's
    // TValue arrays) is latency-bound, and on the S3 the all-PSRAM version
    // measured the whole _update ~2x slower than the #6 spike's SRAM-resident
    // state. Keep a headroom floor so the WiFi/DMA pools never starve, and
    // fall back to PSRAM so a big cart still loads (just slower).
    int ro = (ptr != NULL) ? moy_lua_region(ptr) : -1;  // before realloc frees it
    void *np = NULL;
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
            >= nsize + g_sram_floor) {
        np = heap_caps_realloc(ptr, nsize, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    } else {
        g_sram_denied += nsize;  // floor pressure: bytes that wanted SRAM
    }
    if (np == NULL) {
        np = heap_caps_realloc(ptr, nsize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (np == NULL) {            // both pools exhausted: Lua runs emergency GC
        return NULL;
    }
    {
        int rn = moy_lua_region(np);
        if (ro >= 0) {
            g_live_r[ro] -= osize;
            g_live_cls[ro][moy_lua_cls(osize)] -= osize;
        }
        g_live_r[rn] += nsize;
        g_live_cls[rn][moy_lua_cls(nsize)] += nsize;
        g_alloc_n[rn]++;
        g_alloc_b[rn] += nsize;
    }
#else
    void *np = realloc(ptr, nsize);
    if (np == NULL) {
        return NULL;
    }
#endif
    g_live = g_live - osize + nsize;
    if (g_live > g_peak) {
        g_peak = g_live;
    }
    return np;
}

// ---------------------------------------------------------------------------
// MP <-> Lua value marshalling (scalars only -- objects stay Python-side)

static mp_obj_t lua_to_mp(lua_State *L, int i) {
    switch (lua_type(L, i)) {
        case LUA_TNIL:
            return mp_const_none;
        case LUA_TBOOLEAN:
            return lua_toboolean(L, i) ? mp_const_true : mp_const_false;
        case LUA_TNUMBER:
            if (lua_isinteger(L, i)) {
                // #107: mp_obj_new_int_from_ll unconditionally heap-allocates
                // an mpz (~64B with its digit storage) -- paid for EVERY
                // integer arg of EVERY upcall. Celeste's ~17 draw upcalls x
                // ~5 floored coords each = ~11KB/frame of pure marshalling
                // garbage, i.e. a 160-200ms auto-collect every ~6s of play.
                // Small-int the common case; >31-bit values (never a p8
                // coordinate) keep the mpz path.
                lua_Integer v = lua_tointeger(L, i);
                if ((lua_Integer)(mp_int_t)v == v) {
                    return mp_obj_new_int((mp_int_t)v);
                }
                return mp_obj_new_int_from_ll(v);
            }
            return mp_obj_new_float((mp_float_t)lua_tonumber(L, i));
        case LUA_TSTRING: {
            size_t len;
            const char *s = lua_tolstring(L, i, &len);
            // Same #107 diet for strings: btn("left")/key("a") arrive as
            // already-interned names -- hand back the qstr, allocate only for
            // genuinely new text.
            qstr q = qstr_find_strn(s, len);
            if (q != MP_QSTRnull) {
                return MP_OBJ_NEW_QSTR(q);
            }
            // A Lua string is a BYTE string and may hold anything; a
            // MicroPython str must be valid UTF-8, and mp_obj_new_str raises
            // UnicodeError rather than accepting one that is not. That killed
            // the whole FRAME for a cart doing print("\255") -- legal under moy
            // SPEC.md 6, which draws nothing for that byte but still advances a
            // cell -- on every moy_lua host, the boards included.
            //
            // So hand back bytes for the ones a str cannot hold. Everything
            // downstream reads text through a buffer already: font.as_bytes
            // takes bytes directly, and moy_gfx.text is given the buffer of
            // whichever it gets. Checking costs a scan of strings that missed
            // the qstr cache, which is not the hot path -- interned verb and
            // button names never reach here.
            if (!utf8_check((const byte *)s, len)) {
                return mp_obj_new_bytes((const byte *)s, len);
            }
            // mp_obj_new_str would utf8_check AGAIN and repeat the qstr lookup
            // above; _copy is the tail of it, and its precondition (valid
            // utf-8) is exactly what the line above just established. So this
            // path now does one scan and one lookup where the original did one
            // scan and TWO -- slightly cheaper than before the check existed.
            return mp_obj_new_str_copy(&mp_type_str, (const byte *)s, len);
        }
        default:
            luaL_error(L, "cannot pass a %s to the console api",
                       luaL_typename(L, i));
            return mp_const_none;    // unreachable
    }
}

// Push one MP scalar; returns false (without pushing) on unsupported types.
static bool push_scalar(lua_State *L, mp_obj_t o) {
    if (o == mp_const_none) {
        lua_pushnil(L);
    } else if (o == mp_const_true || o == mp_const_false) {
        lua_pushboolean(L, o == mp_const_true);
    } else if (mp_obj_is_int(o)) {
        lua_pushinteger(L, (lua_Integer)mp_obj_get_int(o));
    } else if (mp_obj_is_float(o)) {
        lua_pushnumber(L, (lua_Number)mp_obj_get_float(o));
    } else if (mp_obj_is_str(o)) {
        size_t len;
        const char *s = mp_obj_str_get_data(o, &len);
        lua_pushlstring(L, s, len);
    } else {
        return false;
    }
    return true;
}

// Result -> Lua returns. A tuple fans out to MULTIPLE returns (touch()).
static int push_mp_to_lua(lua_State *L, mp_obj_t ret) {
    if (ret == mp_const_none) {
        return 0;
    }
    if (mp_obj_is_type(ret, &mp_type_tuple)) {
        size_t n;
        mp_obj_t *items;
        mp_obj_tuple_get(ret, &n, &items);
        luaL_checkstack(L, (int)n + 2, "tuple return");
        for (size_t i = 0; i < n; i++) {
            if (!push_scalar(L, items[i])) {
                return luaL_error(L, "console api returned an unsupported tuple");
            }
        }
        return (int)n;
    }
    if (!push_scalar(L, ret)) {
        return luaL_error(L, "console api returned an unsupported value "
                             "(objects stay python-side; use the glue handles)");
    }
    return 1;
}

// nlr-protected Python call from inside a Lua C function: an MP exception must
// never longjmp through Lua's frames -- convert it to a lua_error instead.
static char g_pyerr[192];

static bool call_py(mp_obj_t fn, size_t n, const mp_obj_t *args, mp_obj_t *ret) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        *ret = mp_call_function_n_kw(fn, n, 0, args);
        nlr_pop();
        return true;
    }
    // format the exception text best-effort (formatting itself may allocate,
    // so it gets its own protection; a formatting failure degrades to a stub)
    strcpy(g_pyerr, "console api error");
    nlr_buf_t nlr2;
    if (nlr_push(&nlr2) == 0) {
        mp_obj_t exc = MP_OBJ_FROM_PTR(nlr.ret_val);
        vstr_t vstr;
        mp_print_t print;
        vstr_init_print(&vstr, 64, &print);
        mp_obj_print_helper(&print, exc, PRINT_EXC);
        size_t len = vstr.len < sizeof(g_pyerr) - 1 ? vstr.len : sizeof(g_pyerr) - 1;
        memcpy(g_pyerr, vstr.buf, len);
        g_pyerr[len] = 0;
        nlr_pop();
    }
    return false;
}

// ---------------------------------------------------------------------------
// the generic trampoline: Lua global -> registered Python callable

// The widest verb in the moy spec: sspr(sx, sy, sw, sh, dx, dy, dw, dh,
// colorkey, flip) -- SPEC.md 7.1. The cap was 8, which is map()'s width, so
// every cart calling the full sspr form failed with "too many arguments" on
// every moy_lua host, the boards included. Found by moy-spec's conformance
// suite (the `provisional` scene). Keep this >= the widest spec signature.
#define MOY_API_MAX_ARGS 10

static int tramp_call(lua_State *L, int idx) {
    int n = lua_gettop(L);
    if (n > MOY_API_MAX_ARGS) {
        return luaL_error(L, "console api: too many arguments");
    }
    mp_obj_t args[MOY_API_MAX_ARGS];
    for (int i = 0; i < n; i++) {
        args[i] = lua_to_mp(L, i + 1);
    }
    mp_obj_t calls = mp_obj_subscr(MP_STATE_VM(moy_lua_root),
                                   MP_OBJ_NEW_SMALL_INT(ROOT_CALLS),
                                   MP_OBJ_SENTINEL);
    mp_obj_t fn = mp_obj_subscr(calls, MP_OBJ_NEW_SMALL_INT(idx), MP_OBJ_SENTINEL);
    mp_obj_t ret = mp_const_none;
    if (!call_py(fn, (size_t)n, args, &ret)) {
        return luaL_error(L, "%s", g_pyerr);
    }
    return push_mp_to_lua(L, ret);
}

static int l_tramp(lua_State *L) {
    return tramp_call(L, (int)lua_tointeger(L, lua_upvalueindex(1)));
}

// ---------------------------------------------------------------------------
// the HOT spr: quads straight into _batch_arr (moy_gfx spr_gate protocol)

static int l_spr(lua_State *L) {
    // spr(tile, x, y [, colorkey, scale, flip]) -- numbers only; image sprites
    // go through the layer/image glue handles in a Lua cart (Phase 1 scope).
    int n = lua_gettop(L);
    if (n < 3 || n > 6) {
        return luaL_error(L, "spr(tile, x, y[, colorkey, scale, flip])");
    }
    lua_Integer v[6] = {0, 0, 0, -1, 1, 0};
    for (int i = 0; i < n; i++) {
        if (lua_type(L, i + 1) != LUA_TNUMBER) {
            return luaL_error(L, "spr: arg %d must be a number "
                                 "(image sprites: use layers in lua carts)", i + 1);
        }
        // float coords truncate toward zero, matching Python int() / spr_gate
        v[i] = lua_isinteger(L, i + 1) ? lua_tointeger(L, i + 1)
                                       : (lua_Integer)lua_tonumber(L, i + 1);
    }
    int16_t *q = g_q;
    if (q == NULL) {
        return luaL_error(L, "spr: no batch bound (moy_lua.init first)");
    }
    lua_Integer k = q[0];
    if (k < 4) {
        k = 4;
    }
    if (k == 4 || (size_t)(k + 4) > g_qlen
        || q[3] != (int16_t)g_token
        || q[1] != (int16_t)v[3] || q[2] != (int16_t)v[4]) {
        // run break (first item / state change / foreign writer / full queue).
        bool stamped = false;
#ifdef MOY_LUA_DRAW_DIRECT
        // #67 stage-1 (moycore): with the sheet registered on the bound ctx,
        // the break never enters Python -- OUR pending run flushes through
        // the capi and the header is stamped right here. A FOREIGN run
        // (Python writer's token: its sheet is whatever canvas._batch_sheet
        // says) keeps the upcall below. canvas._batch_sheet is deliberately
        // NOT touched: DeviceCanvas.flush_batch resolves a C-stamped run
        // through its _lua_batch_sheet fallback, so a mid-frame Python flush
        // (a trampolined pal()/camera(), a Python-lane primitive) still
        // emits these quads.
        if (k <= 4 || q[3] == (int16_t)g_token) {
            if (lua_batch_flush_c(L)) {
                q[1] = (int16_t)v[3];
                q[2] = (int16_t)v[4];
                q[3] = (int16_t)g_token;
                stamped = true;
            }
        } else {
            g_bup++;                   // foreign-token break: the upcall lane
        }
#endif
        if (!stamped) {
            // canvas.begin_batch flushes any pending run and stamps this one --
            // the same upcall moy_gfx's spr_gate makes, nlr-protected here.
            nlr_buf_t nlr;
            if (nlr_push(&nlr) == 0) {
                mp_obj_t root = MP_STATE_VM(moy_lua_root);
                mp_obj_t canvas = mp_obj_subscr(root, MP_OBJ_NEW_SMALL_INT(ROOT_CANVAS),
                                                MP_OBJ_SENTINEL);
                mp_obj_t sheet = mp_obj_subscr(root, MP_OBJ_NEW_SMALL_INT(ROOT_SHEET),
                                               MP_OBJ_SENTINEL);
                mp_obj_t dest[2 + 4];
                mp_load_method(canvas, MP_QSTR_begin_batch, dest);
                dest[2] = sheet;
                dest[3] = MP_OBJ_NEW_SMALL_INT((mp_int_t)v[3]);
                dest[4] = MP_OBJ_NEW_SMALL_INT((mp_int_t)v[4]);
                dest[5] = MP_OBJ_NEW_SMALL_INT(g_token);
                mp_call_method_n_kw(4, 0, dest);
                nlr_pop();
            } else {
                return luaL_error(L, "spr: begin_batch failed");
            }
        }
        k = q[0];
        if (k < 4 || (size_t)(k + 4) > g_qlen) {
            return 0;                  // defensive: queue unusable, drop
        }
    }
    lua_Integer tid = v[0];
    if (tid < -32768 || tid > 32767) {
        tid = -1;                      // invalid tile id -> skipped at draw
    }
    lua_Integer x = v[1];
    if (x < -32768) x = -32768; else if (x > 32767) x = 32767;
    lua_Integer y = v[2];
    if (y < -32768) y = -32768; else if (y > 32767) y = 32767;
    q[k] = (int16_t)tid;
    q[k + 1] = (int16_t)x;
    q[k + 2] = (int16_t)y;
    q[k + 3] = (int16_t)(v[5] & 3);
    q[0] = (int16_t)(k + 4);
    return 0;
}

// ---------------------------------------------------------------------------
// #189: the libmoy-direct draw verbs
//
// One shared lua_CFunction, closed over (kind, fallback-trampoline-idx). The
// hot shapes -- all-number args at the verb's exact arity, plus a string head
// for print -- draw through moy_gfx's exported C API against the SAME DrawCtx
// the canvas keeps in step (camera/clip/pal all applied C-side), so a Lua
// draw never enters Python at all. Anything else (pix's 2-arg READ form, a
// nil, a table) falls back to the verb's original Python trampoline, which is
// where the odd forms were always handled -- semantics unchanged, purely a
// fast lane, exactly the draw-gate contract one layer down.

static void moy_lua_check_open(void);   // defined with the module functions

#ifdef MOY_LUA_DRAW_DIRECT

enum {
    DV_PIX = 0, DV_RECT, DV_RECTB, DV_LINE,
    DV_CIRC, DV_CIRCB, DV_TRI, DV_TRIB, DV_PRINT,
    // #67 stage-1b: the sheet-sampling verbs -- direct only when the glue
    // registered the sources (set_batch_src / set_map_src); else trampoline.
    DV_SSPR, DV_TLINE
};

// Flush the pending run C-side when the ctx owns a source and the run is OURS
// (#67 stage-1); feeds the bounce pump like flush_batch does. Returns false
// when the caller must fall back to the canvas upcall (no ctx/source, or a
// foreign-token run). Lua-raises (never returns) only if the pump upcall
// itself failed.
static bool lua_batch_flush_c(lua_State *L) {
    moy_gfx_draw_ctx_t *c = g_ctx;
    if (c == NULL || !moy_gfx_capi_batch_src(c)) {
        return false;
    }
    int16_t *q = g_q;
    uint32_t pend = (q != NULL && q[0] > 4) ? (uint32_t)((q[0] - 4) >> 2) : 0;
    bool prof = pend != 0 && moy_gfx_capi_prof(c);
    uint32_t t0 = prof ? (uint32_t)mp_hal_ticks_us() : 0;
    if (!moy_gfx_capi_flush_batch(c, g_token)) {
        return false;
    }
    if (pend != 0) {
        g_bf++;
        g_bs += pend;
        if (prof) {
            g_bus += (uint32_t)mp_hal_ticks_us() - t0;
        }
        mp_obj_t pump = moy_gfx_capi_pump_due(c, 1);
        if (pump != MP_OBJ_NULL) {
            mp_obj_t ret;
            if (!call_py(pump, 0, NULL, &ret)) {
                luaL_error(L, "%s", g_pyerr);   // no return
            }
        }
    }
    return true;
}

// A coordinate/colour arg: number only; floats truncate toward zero, exactly
// like the draw gates' gate_num (and Python int()).
static bool dv_num(lua_State *L, int i, int *out) {
    if (lua_type(L, i) != LUA_TNUMBER) {
        return false;
    }
    lua_Integer v = lua_isinteger(L, i) ? lua_tointeger(L, i)
                                        : (lua_Integer)lua_tonumber(L, i);
    *out = (int)v;
    return true;
}

static int l_draw_fallback(lua_State *L) {
    int fidx = (int)lua_tointeger(L, lua_upvalueindex(2));
    if (fidx < 0) {
        return luaL_error(L, "console api: unsupported call form");
    }
    g_dfb++;
    return tramp_call(L, fidx);
}

static int l_draw(lua_State *L) {
    moy_gfx_draw_ctx_t *c = g_ctx;
    if (c == NULL || !moy_gfx_capi_ready(c)) {
        return l_draw_fallback(L);
    }
    int kind = (int)lua_tointeger(L, lua_upvalueindex(1));
    int n = lua_gettop(L);
    int v[10];
    const char *s = NULL;
    size_t slen = 0;
    switch (kind) {
        case DV_PIX:                       // 2-arg READ form returns a value
            if (n != 3) return l_draw_fallback(L);
            break;
        case DV_RECT:
        case DV_RECTB:
        case DV_LINE:
            if (n != 5) return l_draw_fallback(L);
            break;
        case DV_CIRC:
        case DV_CIRCB:
            if (n != 4) return l_draw_fallback(L);
            break;
        case DV_TRI:
        case DV_TRIB:
            if (n != 7) return l_draw_fallback(L);
            break;
        case DV_SSPR:
            // sspr(sx, sy, sw, sh, dx, dy[, dw, dh[, ck[, flip]]]) -- the
            // 7-arg half-form keeps the trampoline (dh defaults pairwise).
            if (n < 6 || n == 7 || n > 10 || !moy_gfx_capi_batch_src(c)) {
                return l_draw_fallback(L);
            }
            break;
        case DV_TLINE:
            // tline(x0, y0, x1, y1, u, v, du, dv[, ck]) -- 16.16 fixed ints.
            if (n < 8 || n > 9 || !moy_gfx_capi_batch_src(c)
                || !moy_gfx_capi_map_src(c)) {
                return l_draw_fallback(L);
            }
            break;
        default:                           // DV_PRINT: (s, x, y, c[, scale]);
            if (n < 4 || n > 5 || lua_type(L, 1) != LUA_TSTRING) {
                return l_draw_fallback(L); // the legacy scale is IGNORED, like
            }                              // the print gate it mirrors
            s = lua_tolstring(L, 1, &slen);
            break;
    }
    if (kind == DV_PRINT) {
        for (int i = 0; i < 3; i++) {
            if (!dv_num(L, i + 2, &v[i])) return l_draw_fallback(L);
        }
    } else {
        for (int i = 0; i < n; i++) {
            if (!dv_num(L, i + 1, &v[i])) return l_draw_fallback(L);
        }
    }
    // #63 order rule: queued sprites were drawn under the current state and
    // must land BEFORE this primitive. Stage-1: OUR pending run flushes in C
    // (lua_batch_flush_c); a foreign run keeps the upcall the gates make --
    // nlr-protected, because an MP exception must never longjmp through Lua.
    if (moy_gfx_capi_batch_pending(c) && !lua_batch_flush_c(L)) {
        g_bup++;
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            mp_obj_t dest[2];
            mp_load_method(moy_gfx_capi_canvas(c), MP_QSTR_flush_batch, dest);
            mp_call_method_n_kw(0, 0, dest);
            nlr_pop();
        } else {
            return luaL_error(L, "draw: flush_batch failed");
        }
    }
    bool prof = moy_gfx_capi_prof(c);
    uint32_t t0 = prof ? (uint32_t)mp_hal_ticks_us() : 0;
    int bucket = 1;                        // shape (line/circ/circb/tri/trib)
    switch (kind) {
        case DV_PIX:
            moy_gfx_capi_fill(c, v[0], v[1], 1, 1, v[2]);
            bucket = 0;
            break;
        case DV_RECT:
            moy_gfx_capi_fill(c, v[0], v[1], v[2], v[3], v[4]);
            bucket = 0;
            break;
        case DV_RECTB:
            moy_gfx_capi_rectb(c, v[0], v[1], v[2], v[3], v[4]);
            bucket = 0;
            break;
        case DV_LINE:
            moy_gfx_capi_line(c, v[0], v[1], v[2], v[3], v[4]);
            break;
        case DV_CIRC:
            moy_gfx_capi_circ(c, v[0], v[1], v[2], v[3], false);
            break;
        case DV_CIRCB:
            moy_gfx_capi_circ(c, v[0], v[1], v[2], v[3], true);
            break;
        case DV_TRI:
            moy_gfx_capi_tri(c, v[0], v[1], v[2], v[3], v[4], v[5], v[6]);
            break;
        case DV_TRIB:                      // three lines, like the Python trib
            moy_gfx_capi_line(c, v[0], v[1], v[2], v[3], v[6]);
            moy_gfx_capi_line(c, v[2], v[3], v[4], v[5], v[6]);
            moy_gfx_capi_line(c, v[4], v[5], v[0], v[1], v[6]);
            break;
        case DV_SSPR:                      // defaults exactly as the MP verb
            if (n < 8) { v[6] = v[2]; v[7] = v[3]; }
            if (n < 9) v[8] = -1;
            if (n < 10) v[9] = 0;
            moy_gfx_capi_sspr(c, v[0], v[1], v[2], v[3], v[4], v[5],
                              v[6], v[7], v[8], v[9]);
            break;
        case DV_TLINE:
            if (n < 9) v[8] = -1;
            moy_gfx_capi_tline(c, v[0], v[1], v[2], v[3],
                               (int32_t)v[4], (int32_t)v[5],
                               (int32_t)v[6], (int32_t)v[7], v[8]);
            break;
        default:
            moy_gfx_capi_print(c, (const uint8_t *)s, slen, v[0], v[1], v[2]);
            bucket = 2;
            break;
    }
    g_dn[bucket]++;
    if (prof) {
        g_dus[bucket] += (uint32_t)mp_hal_ticks_us() - t0;
    }
    // #163 door 1: keep the T-Deck root canvas's bounce pump fed. The capi
    // hands back the callable when it is due; the invoke is protected here.
    mp_obj_t pump = moy_gfx_capi_pump_due(c, 1);
    if (pump != MP_OBJ_NULL) {
        mp_obj_t ret;
        if (!call_py(pump, 0, NULL, &ret)) {
            return luaL_error(L, "%s", g_pyerr);
        }
    }
    return 0;
}

#endif  // MOY_LUA_DRAW_DIRECT

// bind_draw(ctx) -> bool: install the direct draw verbs over their registered
// trampolines. Call AFTER register()ing the cart namespace (each verb's
// trampoline index is recovered from the live global -- it becomes the
// odd-shape fallback) and BEFORE exec()ing the cart, whose locals must
// capture the C functions. Returns False -- leaving every trampoline standing
// -- when the build has no moy_gfx beside it (wasm) or `ctx` is not a
// DrawCtx, so callers can pass whatever the canvas offered and not care.
static mp_obj_t moy_lua_bind_draw(mp_obj_t ctx_in) {
#ifndef MOY_LUA_DRAW_DIRECT
    (void)ctx_in;
    return mp_const_false;
#else
    moy_lua_check_open();
    moy_gfx_draw_ctx_t *c = moy_gfx_capi_ctx(ctx_in);
    if (c == NULL) {
        return mp_const_false;
    }
    // Root the ctx OBJECT: g_ctx is a raw pointer and must never dangle.
    mp_obj_subscr(MP_STATE_VM(moy_lua_root), MP_OBJ_NEW_SMALL_INT(ROOT_CTX),
                  ctx_in);
    static const struct {
        const char *name;
        uint8_t kind;
    } verbs[] = {
        {"pix", DV_PIX},   {"rect", DV_RECT},   {"rectb", DV_RECTB},
        {"line", DV_LINE}, {"circ", DV_CIRC},   {"circb", DV_CIRCB},
        {"tri", DV_TRI},   {"trib", DV_TRIB},   {"print", DV_PRINT},
        {"sspr", DV_SSPR}, {"tline", DV_TLINE},
    };
    lua_State *L = g_L;
    for (size_t i = 0; i < sizeof(verbs) / sizeof(verbs[0]); i++) {
        lua_Integer fidx = -1;
        if (lua_getglobal(L, verbs[i].name) == LUA_TFUNCTION
            && lua_tocfunction(L, -1) == l_tramp
            && lua_getupvalue(L, -1, 1) != NULL) {
            fidx = lua_tointeger(L, -1);
            lua_pop(L, 1);                 // the upvalue copy
        }
        lua_pop(L, 1);                     // whatever getglobal pushed
        lua_pushinteger(L, (lua_Integer)verbs[i].kind);
        lua_pushinteger(L, fidx);
        lua_pushcclosure(L, l_draw, 2);
        lua_setglobal(L, verbs[i].name);
    }
    g_ctx = c;
    g_dfb = 0;
    memset(g_dn, 0, sizeof(g_dn));
    memset(g_dus, 0, sizeof(g_dus));
    g_bf = g_bs = g_bus = g_bup = 0;
    return mp_const_true;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lua_bind_draw_obj, moy_lua_bind_draw);

// draw_stats() -> (n_fill, n_shape, n_text, us_fill, us_shape, us_text,
// n_fallback) since the last reset -- the on-glass proof the direct lane is
// live, in DRAW3's buckets (us only accumulates under perf capture). None on
// builds without the direct path, so callers can gate.
static mp_obj_t moy_lua_draw_stats(void) {
#ifdef MOY_LUA_DRAW_DIRECT
    mp_obj_t items[7];
    for (int i = 0; i < 3; i++) {
        items[i] = mp_obj_new_int((mp_int_t)g_dn[i]);
        items[3 + i] = mp_obj_new_int((mp_int_t)g_dus[i]);
    }
    items[6] = mp_obj_new_int((mp_int_t)g_dfb);
    return mp_obj_new_tuple(7, items);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_draw_stats_obj, moy_lua_draw_stats);

static mp_obj_t moy_lua_draw_stats_reset(void) {
#ifdef MOY_LUA_DRAW_DIRECT
    g_dfb = 0;
    memset(g_dn, 0, sizeof(g_dn));
    memset(g_dus, 0, sizeof(g_dus));
    g_bf = g_bs = g_bus = g_bup = 0;
#endif
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_draw_stats_reset_obj,
                                 moy_lua_draw_stats_reset);

// batch_stats() -> (n_flushes, n_sprites, us, n_upcall_falls) for the C-side
// batch lane (#67 stage-1) since the last bind/reset -- us only under perf
// capture, like draw_stats. The liveness proof is n_flushes>0 with
// n_upcall_falls==0 (the begin_batch/flush_batch upcalls are gone); a foreign
// -token interleave (Python chrome writing the same canvas mid-run) shows in
// n_upcall_falls and is correct, just slower. None on builds without the
// direct path.
static mp_obj_t moy_lua_batch_stats(void) {
#ifdef MOY_LUA_DRAW_DIRECT
    mp_obj_t items[4] = {
        mp_obj_new_int((mp_int_t)g_bf),
        mp_obj_new_int((mp_int_t)g_bs),
        mp_obj_new_int((mp_int_t)g_bus),
        mp_obj_new_int((mp_int_t)g_bup),
    };
    return mp_obj_new_tuple(4, items);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_batch_stats_obj, moy_lua_batch_stats);

// ---------------------------------------------------------------------------
// module functions

static void moy_lua_check_open(void) {
    if (g_L == NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moy_lua: not open"));
    }
}

static mp_obj_t moy_lua_close_(void) {
    if (g_L != NULL) {
        lua_close(g_L);
        g_L = NULL;
    }
    g_q = NULL;
    g_qlen = 0;
#ifdef MOY_LUA_DRAW_DIRECT
    g_ctx = NULL;                              // the rooted ctx obj goes below
#endif
    MP_STATE_VM(moy_lua_root) = MP_OBJ_NULL;   // un-root: the gc may reclaim
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_close_obj, moy_lua_close_);

// init(canvas, sheet, batch_arr, token): fresh state for one cart run.
static mp_obj_t moy_lua_init(size_t n_args, const mp_obj_t *a) {
    (void)n_args;
    moy_lua_close_();
    mp_buffer_info_t bi;
    mp_get_buffer_raise(a[2], &bi, MP_BUFFER_RW);
    if (bi.len < 2 * 8) {
        mp_raise_ValueError(MP_ERROR_TEXT("batch array too small"));
    }
    // root list FIRST: [canvas, sheet, arr, callables, draw-ctx-or-None]
    mp_obj_t items[5] = {a[0], a[1], a[2], mp_obj_new_list(0, NULL),
                         mp_const_none};
    MP_STATE_VM(moy_lua_root) = mp_obj_new_list(5, items);
    g_q = (int16_t *)bi.buf;
    g_qlen = bi.len / 2;
    g_token = (int)(mp_obj_get_int(a[3]) & 0x7FFF);
    g_live = 0;
    g_peak = 0;
#ifdef MOY_LUA_PSRAM
    memset(g_live_r, 0, sizeof(g_live_r));
    memset(g_alloc_n, 0, sizeof(g_alloc_n));
    memset(g_alloc_b, 0, sizeof(g_alloc_b));
    memset(g_live_cls, 0, sizeof(g_live_cls));
    g_sram_denied = 0;
#endif
    g_L = lua_newstate(moy_lua_alloc, NULL);
    if (g_L == NULL) {
        MP_STATE_VM(moy_lua_root) = MP_OBJ_NULL;
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moy_lua: no memory"));
    }
    lua_State *L = g_L;
    // the safe stdlib subset (#67 plan): base + math + string + table
    luaL_requiref(L, LUA_GNAME, luaopen_base, 1);
    lua_pop(L, 1);
    luaL_requiref(L, LUA_MATHLIBNAME, luaopen_math, 1);
    lua_pop(L, 1);
    luaL_requiref(L, LUA_STRLIBNAME, luaopen_string, 1);
    lua_pop(L, 1);
    luaL_requiref(L, LUA_TABLIBNAME, luaopen_table, 1);
    lua_pop(L, 1);
    // the moy spec's 4.1 ceiling: base minus the code loaders AND collectgarbage
    // (a cart must not steer the host's GC; the host lupa prelude nils it too)
    static const char *const strip[] = {"dofile", "loadfile", "load", "require",
                                        "collectgarbage"};
    for (size_t i = 0; i < sizeof(strip) / sizeof(strip[0]); i++) {
        lua_pushnil(L);
        lua_setglobal(L, strip[i]);
    }
    // the hot spr goes in now; trampolines and glue overwrite the rest
    lua_pushcfunction(L, l_spr);
    lua_setglobal(L, "spr");
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lua_init_obj, 4, 4, moy_lua_init);

// register(name, callable): expose a Python callable as a Lua global.
static mp_obj_t moy_lua_register(mp_obj_t name_in, mp_obj_t fn_in) {
    moy_lua_check_open();
    mp_obj_t calls = mp_obj_subscr(MP_STATE_VM(moy_lua_root),
                                   MP_OBJ_NEW_SMALL_INT(ROOT_CALLS),
                                   MP_OBJ_SENTINEL);
    mp_obj_list_append(calls, fn_in);
    mp_int_t idx = mp_obj_get_int(mp_obj_len(calls)) - 1;
    lua_State *L = g_L;
    lua_pushinteger(L, (lua_Integer)idx);
    lua_pushcclosure(L, l_tramp, 1);
    lua_setglobal(L, mp_obj_str_get_str(name_in));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(moy_lua_register_obj, moy_lua_register);

static void moy_lua_raise_lua_error(lua_State *L) {
    // the error value is at the stack top; copy it MP-side, pop it, raise.
    size_t len = 0;
    const char *msg = lua_tolstring(L, -1, &len);
    mp_obj_t text = msg ? mp_obj_new_str(msg, len)
                        : MP_OBJ_NEW_QSTR(MP_QSTR_moy_lua);
    lua_pop(L, 1);
    nlr_raise(mp_obj_new_exception_arg1(&mp_type_RuntimeError, text));
}

// exec(src[, chunkname]): load + run a chunk (the prelude, then the cart).
static mp_obj_t moy_lua_exec(size_t n_args, const mp_obj_t *a) {
    moy_lua_check_open();
    lua_State *L = g_L;
    size_t len;
    const char *src = mp_obj_str_get_data(a[0], &len);
    const char *name = n_args > 1 ? mp_obj_str_get_str(a[1]) : "cart";
    if (luaL_loadbuffer(L, src, len, name) != LUA_OK
        || lua_pcall(L, 0, 0, 0) != LUA_OK) {
        moy_lua_raise_lua_error(L);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lua_exec_obj, 1, 2, moy_lua_exec);

// has(name) -> the global is callable (a cart may define any subset of verbs).
static mp_obj_t moy_lua_has(mp_obj_t name_in) {
    moy_lua_check_open();
    lua_State *L = g_L;
    int t = lua_getglobal(L, mp_obj_str_get_str(name_in));
    lua_pop(L, 1);
    return mp_obj_new_bool(t == LUA_TFUNCTION);
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lua_has_obj, moy_lua_has);

// call(name[, number]): call a cart verb (_init / _update(dt) / _draw).
static mp_obj_t moy_lua_call(size_t n_args, const mp_obj_t *a) {
    moy_lua_check_open();
    lua_State *L = g_L;
    lua_getglobal(L, mp_obj_str_get_str(a[0]));
    int nargs = 0;
    if (n_args > 1) {
        lua_pushnumber(L, (lua_Number)mp_obj_get_float(a[1]));
        nargs = 1;
    }
    if (lua_pcall(L, nargs, 0, 0) != LUA_OK) {
        moy_lua_raise_lua_error(L);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_lua_call_obj, 1, 2, moy_lua_call);

static mp_obj_t moy_lua_mem_kb(void) {
    return mp_obj_new_int((mp_int_t)(g_live / 1024));
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_mem_kb_obj, moy_lua_mem_kb);

static mp_obj_t moy_lua_peak_kb(void) {
    return mp_obj_new_int((mp_int_t)(g_peak / 1024));
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_peak_kb_obj, moy_lua_peak_kb);

// (live_sram, live_psram, peak, n_sram, n_psram, b_sram, b_psram, denied,
//  live_cls_sram x4, live_cls_psram x4) -- bytes except the two counts.
// None on builds with no region choice (host/wasm), so callers can gate.
static mp_obj_t moy_lua_alloc_stats(void) {
#ifdef MOY_LUA_PSRAM
    mp_obj_t items[16];
    items[0] = mp_obj_new_int((mp_int_t)g_live_r[0]);
    items[1] = mp_obj_new_int((mp_int_t)g_live_r[1]);
    items[2] = mp_obj_new_int((mp_int_t)g_peak);
    items[3] = mp_obj_new_int((mp_int_t)g_alloc_n[0]);
    items[4] = mp_obj_new_int((mp_int_t)g_alloc_n[1]);
    items[5] = mp_obj_new_int((mp_int_t)g_alloc_b[0]);
    items[6] = mp_obj_new_int((mp_int_t)g_alloc_b[1]);
    items[7] = mp_obj_new_int((mp_int_t)g_sram_denied);
    for (int r = 0; r < 2; r++) {
        for (int c = 0; c < 4; c++) {
            items[8 + r * 4 + c] = mp_obj_new_int((mp_int_t)g_live_cls[r][c]);
        }
    }
    return mp_obj_new_tuple(16, items);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_lua_alloc_stats_obj, moy_lua_alloc_stats);

// set_sram_floor(kb) -> effective kb. Clamped [16, 256]; no-op (returns the
// compiled default) on builds with no region choice.
static mp_obj_t moy_lua_set_sram_floor(mp_obj_t kb_obj) {
#ifdef MOY_LUA_PSRAM
    mp_int_t kb = mp_obj_get_int(kb_obj);
    if (kb < 16) kb = 16;
    if (kb > 256) kb = 256;
    g_sram_floor = (size_t)kb * 1024;
    return mp_obj_new_int(kb);
#else
    (void)kb_obj;
    return mp_obj_new_int(48);
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_1(moy_lua_set_sram_floor_obj, moy_lua_set_sram_floor);

static const mp_rom_map_elem_t moy_lua_module_globals_table[] = {
    {MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_lua)},
    {MP_ROM_QSTR(MP_QSTR_init), MP_ROM_PTR(&moy_lua_init_obj)},
    {MP_ROM_QSTR(MP_QSTR_register), MP_ROM_PTR(&moy_lua_register_obj)},
    {MP_ROM_QSTR(MP_QSTR_bind_draw), MP_ROM_PTR(&moy_lua_bind_draw_obj)},
    {MP_ROM_QSTR(MP_QSTR_draw_stats), MP_ROM_PTR(&moy_lua_draw_stats_obj)},
    {MP_ROM_QSTR(MP_QSTR_draw_stats_reset),
     MP_ROM_PTR(&moy_lua_draw_stats_reset_obj)},
    {MP_ROM_QSTR(MP_QSTR_batch_stats), MP_ROM_PTR(&moy_lua_batch_stats_obj)},
    {MP_ROM_QSTR(MP_QSTR_exec), MP_ROM_PTR(&moy_lua_exec_obj)},
    {MP_ROM_QSTR(MP_QSTR_has), MP_ROM_PTR(&moy_lua_has_obj)},
    {MP_ROM_QSTR(MP_QSTR_call), MP_ROM_PTR(&moy_lua_call_obj)},
    {MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&moy_lua_close_obj)},
    {MP_ROM_QSTR(MP_QSTR_mem_kb), MP_ROM_PTR(&moy_lua_mem_kb_obj)},
    {MP_ROM_QSTR(MP_QSTR_peak_kb), MP_ROM_PTR(&moy_lua_peak_kb_obj)},
    {MP_ROM_QSTR(MP_QSTR_alloc_stats), MP_ROM_PTR(&moy_lua_alloc_stats_obj)},
    {MP_ROM_QSTR(MP_QSTR_set_sram_floor), MP_ROM_PTR(&moy_lua_set_sram_floor_obj)},
};
static MP_DEFINE_CONST_DICT(moy_lua_module_globals, moy_lua_module_globals_table);

const mp_obj_module_t mp_module_moy_lua = {
    .base = {&mp_type_module},
    .globals = (mp_obj_dict_t *)&moy_lua_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_lua, mp_module_moy_lua);
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_lua_root);
