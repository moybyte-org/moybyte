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
//     and the per-sprite cost is a C append, not a VM dispatch.
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
#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"
#include "py/unicode.h"        // utf8_check -- see lua_to_mp's LUA_TSTRING case

#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

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

static lua_State *g_L = NULL;
static size_t g_live = 0;
static size_t g_peak = 0;

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

static int l_tramp(lua_State *L) {
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
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
        // run break (first item / state change / foreign writer / full queue):
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
    // root list FIRST: [canvas, sheet, arr, callables]
    mp_obj_t items[4] = {a[0], a[1], a[2], mp_obj_new_list(0, NULL)};
    MP_STATE_VM(moy_lua_root) = mp_obj_new_list(4, items);
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
