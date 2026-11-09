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
// The console itself -- the snapshot, the queue, pmem, the config table and
// the tile flags, with libmoy's host callbacks -- is moycore_run.c, and the
// cart's Lua VM -- its allocator and pool, its open, load, frame and close --
// is moycore_lua.c; MicroPython touches neither. This file binds them to the
// buffers Python hands run_begin; its profilers (moycore_prof.c), the
// hardware performance counters (moycore_pmu.c) and the compiled cart
// (moycore_wasm.c) are files beside it, sharing modmoycore_int.h.
//
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
// always had (register() below). What a cart DRAWS into a layer is the one
// exception, and it is libmoy's verb table again, not a copy of it: a layer
// method retargets the console's canvas for one call (moycore_layers.h).
//
// That distinction is the whole design, and getting it wrong cost a rewrite:
// EVERY Lua cart runs here -- libmoy's table first, extra verbs on top as
// trampolines. A cart needing a Python-backed verb needs ONE engine that can
// hold one, never a second runtime (the first cut shipped two and the
// deletion commit records what that cost).

#include "modmoycore_int.h"
#include "moycore_svc.h"
#include "py/mperrno.h"

// -- helpers -----------------------------------------------------------------

// The kernel's Player, where the image has it (weak: moycore stands alone).
bool moy_play_lock_seed(uint32_t *seed) __attribute__((weak));

void *moycore_buf_w(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_WRITE);
    if (len) *len = bi.len;
    return bi.buf;
}

void *moycore_buf_r(mp_obj_t o, size_t *len)
{
    mp_buffer_info_t bi;
    mp_get_buffer_raise(o, &bi, MP_BUFFER_READ);
    if (len) *len = bi.len;
    return bi.buf;
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
    moy_loop_count(MOY_UPC_APP);
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

// -- what a run keeps against the gc --------------------------------------------
//
// Each run row's Python objects (its registered callables, its layer restore
// state, its last view() answer, its p8 machine's buffers) live in one rooted
// list, KEEP_N entries a row, so the gc keeps them for exactly their run.
enum { KEEP_CALLS, KEEP_LR, KEEP_VIEW, KEEP_P8MEM, KEEP_P8ROM, KEEP_N };

static mp_obj_t *keep_items(void)
{
    mp_obj_t l = MP_STATE_VM(moycore_keep);
    if (l == MP_OBJ_NULL) return NULL;
    size_t n = 0;
    mp_obj_t *items = NULL;
    mp_obj_list_get(l, &n, &items);
    return items;
}

static mp_obj_t keep_get(int k)
{
    mp_obj_t *items = keep_items();
    mp_obj_t o = items ? items[RUN.slot * KEEP_N + k] : mp_const_none;
    return o == mp_const_none ? MP_OBJ_NULL : o;
}

// MP_OBJ_NULL clears, and allocates nothing (a close with no VM writes it).
static void keep_set(int k, mp_obj_t o)
{
    mp_obj_t *items = keep_items();
    if (items == NULL) {
        if (o == MP_OBJ_NULL) return;
        mp_obj_t l = mp_obj_new_list(MOYCORE_RUNS * KEEP_N, NULL);
        mp_obj_list_get(l, &(size_t){0}, &items);
        for (size_t i = 0; i < MOYCORE_RUNS * KEEP_N; i++) items[i] = mp_const_none;
        MP_STATE_VM(moycore_keep) = l;
    }
    items[RUN.slot * KEEP_N + k] = o == MP_OBJ_NULL ? mp_const_none : o;
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
    mp_obj_t fn = mp_obj_subscr(keep_get(KEEP_CALLS),
                                MP_OBJ_NEW_SMALL_INT(idx), MP_OBJ_SENTINEL);
    mp_obj_t ret = mp_const_none;
    if (!call_py(fn, (size_t)n, args, &ret)) {
        return luaL_error(L, "%s", g_pyerr);
    }
    return push_mp_to_lua(L, ret);
}

// -- the p8 machine and the shim's map walk: moycore_lua.c --------------------

// p8_memory(mem, rom) -- the machine's buffers (65536 and 0x4300 bytes,
// writable) for the next run_begin, which the run keeps against the gc; None
// gives it no machine.
static mp_obj_t mod_p8_memory(mp_obj_t mem_obj, mp_obj_t rom_obj)
{
    uint8_t *mem = NULL, *rom = NULL;
    moycore_lua_p8_memory(NULL, NULL);
    MP_STATE_VM(moycore_p8mem) = MP_OBJ_NULL;
    MP_STATE_VM(moycore_p8rom) = MP_OBJ_NULL;
    if (mem_obj != mp_const_none) {
        size_t n = 0;
        uint8_t *p = moycore_buf_w(mem_obj, &n);
        if (n < MOY_P8_MEM) mp_raise_ValueError(MP_ERROR_TEXT("p8_memory: mem < 65536"));
        mem = p;
        MP_STATE_VM(moycore_p8mem) = mem_obj;
    }
    if (rom_obj != mp_const_none) {
        size_t n = 0;
        uint8_t *p = moycore_buf_w(rom_obj, &n);
        if (n < MOY_P8_ROM) mp_raise_ValueError(MP_ERROR_TEXT("p8_memory: rom < 0x4300"));
        rom = p;
        MP_STATE_VM(moycore_p8rom) = rom_obj;
    }
    moycore_lua_p8_memory(mem, rom);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_p8_memory_obj, mod_p8_memory);

// register(name, callable) -- add a verb libmoy does not bind. Must be called
// AFTER run_begin (the VM and the spec table exist by then) and BEFORE the
// cart executes, because a cart captures its globals into locals at load.
static mp_obj_t mod_register(mp_obj_t name_obj, mp_obj_t fn)
{
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    mp_obj_t calls = keep_get(KEEP_CALLS);
    if (calls == MP_OBJ_NULL) {
        calls = mp_obj_new_list(0, NULL);
        keep_set(KEEP_CALLS, calls);
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

// image_put(name, text) -- a paint image's .moyimg text for the run's
// image(name), copied into C (moycore_lua.h); the cart's layers and images
// are then the run's own, and __layer_new, __layer_spr_img and
// __image_handle never cross into Python. Before the load.
static mp_obj_t mod_image_put(mp_obj_t name_obj, mp_obj_t text_obj)
{
    size_t n = 0;
    const char *text = mp_obj_str_get_data(text_obj, &n);
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: no run"));
    if (moycore_lua_image_put(mp_obj_str_get_str(name_obj), text, n) != 0)
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moycore: image_put"));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_image_put_obj, mod_image_put);

// scene_put(name, text) -- a scene's .moyscene text for the run's scene verbs
// (moycore_scene.h): the scenes are parsed and drawn in C, so no scene row
// crosses into Python. Before the load, in the cart's scene order.
static mp_obj_t mod_scene_put(mp_obj_t name_obj, mp_obj_t text_obj)
{
    size_t n = 0;
    const char *text = mp_obj_str_get_data(text_obj, &n);
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: no run"));
    if (moycore_lua_scene_put(mp_obj_str_get_str(name_obj), text, n) != 0)
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moycore: scene_put"));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_scene_put_obj, mod_scene_put);

// layer_bind(buf, w, h) -- park a cart layer's RGB565 buffer for the prelude's
// make_layer, which builds its canvas next (moycore_layers.h). Called from the
// __layer_new trampoline, so the VM is mid-call: this touches no Lua state.
static mp_obj_t mod_layer_bind(mp_obj_t buf_obj, mp_obj_t w_obj, mp_obj_t h_obj)
{
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    size_t len = 0;
    void *pix = moycore_buf_w(buf_obj, &len);
    if (moycore_layers_park(&RUN.layers, pix, len, mp_obj_get_int(w_obj),
                            mp_obj_get_int(h_obj)) != 0)
        mp_raise_ValueError(MP_ERROR_TEXT("layer_bind: buffer too small"));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_layer_bind_obj, mod_layer_bind);

// The screen canvas's layer restore (moy_gfx's mg_lr_*), which a native
// draw_layer takes: its predicted copy is the canvas's, kicked by its
// sync_back, so the two lanes share one prediction.
static void layer_blit(void *st, moy_canvas *dst, const moy_canvas *src,
                       int cam_x, int cam_y, int edited)
{
    moy_gfx_k_layer_blit(st, (uint16_t *)dst->pix, (size_t)dst->w * (size_t)dst->h,
                         dst->w, dst->h, (const uint16_t *)src->pix,
                         (size_t)src->w * (size_t)src->h, src->w, src->h,
                         cam_x, cam_y, edited != 0);
}

static void layer_restore_drop(void)
{
    if (RUN.layers.blit_state) moy_gfx_k_layer_forget(RUN.layers.blit_state);
    RUN.layers.blit = NULL;
    RUN.layers.blit_state = NULL;
    keep_set(KEEP_LR, MP_OBJ_NULL);
}

// layer_restore(state) -- draw_layer through the screen canvas's restore
// state (its `_lrs`), or None for libmoy's plain copy. After run_begin; the
// run's close forgets the prediction, whose layer the run frees.
static mp_obj_t mod_layer_restore(mp_obj_t st)
{
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    layer_restore_drop();
    if (st != mp_const_none) {
        size_t len = 0;
        void *p = moycore_buf_w(st, &len);
        if (len < moy_gfx_k_layer_size() || ((uintptr_t)p & 3u) != 0)
            mp_raise_ValueError(MP_ERROR_TEXT("layer_restore: not a restore state"));
        keep_set(KEEP_LR, st);
        RUN.layers.blit = layer_blit;
        RUN.layers.blit_state = p;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_layer_restore_obj, mod_layer_restore);

// -- the module surface ------------------------------------------------------

// run_begin(fb, w, h, wire, sheet_pix, map_cells, map_w, map_h,
//           snap, audio_q, pmem_bytes, cfg, flags, vm, app)
//
// Builds the console and opens the VM with libmoy's verb table -- and STOPS.
// `app` is the app's run, moy_app's `run()` (moycore_app_run), whose role
// names the Lua state opens, or None for a run with none.
// `vm` False builds the console alone, for a compiled cart: wasm_open() then
// hands it to the engine instead of a Lua state.
// The cart is loaded by load() afterwards, because between the two the host
// registers its extension verbs, and a cart captures its globals into locals
// as it executes. Doing both here left no window for that, which is how the
// first version ended up needing a second runtime for carts using layers.
//
// Everything is a buffer the console already owns: the framebuffer the
// compositor presents, the sheet and tilemap the project holds, the snapshot
// and queue the loop refreshes. Nothing is copied and nothing is allocated
// here except the VM.
// The app's run a launch hands over: the bytes moy_app's `run()` answers (a
// moy_app_run_t, native/moy_app/moy_app_run.h), read in place for the open
// that copies it, or NULL for None and in an image without native/moy_app.
const struct moy_app_run *moycore_app_run(mp_obj_t o)
{
    if (o == mp_const_none) return NULL;
#if MOYCORE_APP
    size_t n = 0;
    const void *p = moycore_buf_r(o, &n);
    if (n != sizeof(moy_app_run_t))
        mp_raise_ValueError(MP_ERROR_TEXT("moycore: not an app run"));
    return (const struct moy_app_run *)p;
#else
    return NULL;
#endif
}

static mp_obj_t mod_run_begin(size_t n_args, const mp_obj_t *a)
{
    if (n_args != 15) mp_raise_TypeError(MP_ERROR_TEXT("run_begin: 15 args"));
    const struct moy_app_run *app = moycore_app_run(a[14]);
    int vm = mp_obj_is_true(a[13]);
    if (moycore_run_new() == NULL)
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("moycore: every run row is held"));
    for (int k = 0; k < KEEP_N; k++) keep_set(k, MP_OBJ_NULL);   // may name a reset heap
    keep_set(KEEP_P8MEM, MP_STATE_VM(moycore_p8mem));
    keep_set(KEEP_P8ROM, MP_STATE_VM(moycore_p8rom));
    // #211's two meters belong to the RUN, so they start here and survive
    // close() -- the report is read at the exit boundary, after the VM is gone.
    moycore_lua_meters_reset();

    size_t fblen = 0;
    moy_pixel *fb = (moy_pixel *)moycore_buf_w(a[0], &fblen);
    int w = mp_obj_get_int(a[1]), h = mp_obj_get_int(a[2]);
    if (w <= 0 || h <= 0 || fblen < (size_t)w * (size_t)h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: framebuffer too small"));
    moy_canvas_init(&RUN.c.canvas, fb, w, h);
#ifdef MOY_PIXEL_RGB565
    if (a[3] != mp_const_none) {
        size_t wlen = 0;
        const uint16_t *wire = (const uint16_t *)moycore_buf_r(a[3], &wlen);
        if (wlen < MOY_PALETTE * 2)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: wire table too small"));
        moy_canvas_wire(&RUN.c.canvas, wire);
    }
#endif

    if (a[4] != mp_const_none) {
        size_t slen = 0;
        RUN.c.sheet.pix = (uint8_t *)moycore_buf_r(a[4], &slen);
        if (slen < (size_t)MOY_SHEET_W * MOY_SHEET_H)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: sheet too small"));
    }
    if (a[5] != mp_const_none) {
        size_t mlen = 0;
        RUN.c.map.cells = (uint8_t *)moycore_buf_r(a[5], &mlen);
        RUN.c.map.w = mp_obj_get_int(a[6]);
        RUN.c.map.h = mp_obj_get_int(a[7]);
        if (RUN.c.map.w < 0 || RUN.c.map.h < 0
            || (size_t)RUN.c.map.w * (size_t)RUN.c.map.h > mlen)
            mp_raise_ValueError(MP_ERROR_TEXT("run_begin: map too small"));
    }

    size_t snlen = 0;
    RUN.c.snap = (int32_t *)moycore_buf_w(a[8], &snlen);
    if (snlen < SNAP_LEN * sizeof(int32_t))
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: snapshot too small"));
    size_t aqlen = 0;
    int32_t *aq = (int32_t *)moycore_buf_w(a[9], &aqlen);
    if (aqlen / sizeof(int32_t) < 1 + AQ_SLOTS)
        mp_raise_ValueError(MP_ERROR_TEXT("run_begin: audio queue too small"));
    moycore_run_open(&RUN.c, RUN.c.snap, aq, (int)(aqlen / sizeof(int32_t)));

    if (a[10] != mp_const_none) {                 // pmem image in, 256 int32
        size_t plen = 0;
        const int32_t *p = (const int32_t *)moycore_buf_r(a[10], &plen);
        size_t n = plen / sizeof(int32_t);
        if (n > 256) n = 256;
        moycore_run_pmem_load(&RUN.c, p, (int)n);
    }
    // The config table, copied: "key\0value\0" pairs (lua_ext.cfg_blob).
    if (a[11] != mp_const_none) {
        size_t clen = 0;
        const char *blob = (const char *)moycore_buf_r(a[11], &clen);
        if (moycore_run_set_cfg(&RUN.c, blob, clen) != 0)
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("moycore: no room for the config"));
    }

    // SPEC.md 3.5 tile flags, COPIED rather than borrowed -- the one buffer
    // here that is not the caller's. The sheet, the map and the framebuffer
    // are Python-owned and handed over for the life of the run because they
    // are large and live in PSRAM; this is 512 bytes, it is written from C
    // (fset, the p8 shim's __moy_map_flags, a poke to 0x3000) and it must
    // survive a caller that passes a plain immutable `bytes`. A short blob
    // leaves the rest zero, exactly as a short flags.moyflags does.
    memset(RUN.c.flags, 0, sizeof(RUN.c.flags));
    if (a[12] != mp_const_none) {
        size_t flen = 0;
        const uint8_t *fp = moycore_buf_r(a[12], &flen);
        if (flen > sizeof(RUN.c.flags)) flen = sizeof(RUN.c.flags);
        memcpy(RUN.c.flags, fp, flen);
    }

    RUN.c.con.sheet  = RUN.c.sheet.pix ? &RUN.c.sheet : NULL;
    RUN.c.con.map    = RUN.c.map.cells ? &RUN.c.map : NULL;
    // SEED IT. libmoy's rnd is xorshift32 over con->rng and treats 0 as "use
    // the golden-ratio constant" -- correct, deterministic, and therefore the
    // SAME sequence on every run of every cart. moy_lua never showed this
    // because its prelude shadowed rnd with Lua's math.random, which Lua 5.4
    // seeds per state; moving a cart to moycore made sakura's petals fall the
    // same way twice. SPEC.md 9 fixes rnd's range and explicitly not its
    // sequence (no conformance scene may call it), so the seed is a host
    // quality choice and this is the quality we want.
    RUN.c.con.rng    = (uint32_t)mp_hal_ticks_us();
    if (RUN.c.con.rng == 0) RUN.c.con.rng = 1;
    // ...unless a lockstep match is live: then both consoles' _init draw from
    // the match's seed (native/moy_play/moy_match.h).
    uint32_t lock_seed;
    if (moy_play_lock_seed != NULL && moy_play_lock_seed(&lock_seed)) {
        RUN.c.con.rng = lock_seed;
    }

    mp_obj_t hnd = MP_OBJ_NEW_SMALL_INT(moycore_run_handle(&RUN));
    if (!vm) {
        // A compiled cart: the console and nothing on it until wasm_open().
        RUN.open = 1;
        return hnd;
    }
    char err[64];
    if (moycore_lua_open(app, err, sizeof(err)) != 0) {
        mp_raise_msg_varg(RUN.L == NULL ? &mp_type_MemoryError : &mp_type_RuntimeError,
                          MP_ERROR_TEXT("%s"), err);
    }
    moycore_prof_on = 0;               // a new VM: the old wrappers went with the old one
    moycore_prof_n = 0;
    moycore_lprof_forget();              // ...and the old one's hook died with it
    RUN.open = 1;
    return hnd;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_run_begin_obj, 15, 15, mod_run_begin);

// Run one chunk. Shared by exec() and load(); the only difference between them
// is whether _init follows.
//
// TEXT ONLY ("t"). Lua does not verify precompiled bytecode, and a crafted
// binary chunk can read and write outside the VM -- on a board, the firmware's
// own memory. SPEC.md 4.1 bans load/loadstring/dofile for that reason; a
// cart's source arriving here is the one door those bans do not cover. A chunk
// that opens with ESC, as every binary one does, fails with Lua's own load
// error (moycore_lua_exec). runtime/host/moyhost_lua.c's hl_exec is the host's
// twin of this call.
static mp_obj_t run_chunk(mp_obj_t src_obj, mp_obj_t name_obj)
{
    size_t srclen = 0;
    const char *src = mp_obj_str_get_data(src_obj, &srclen);
    const char *name = mp_obj_str_get_str(name_obj);
    char err[192];
    if (moycore_lua_exec(src, srclen, name, err, sizeof(err)) != 0) {
        return mp_obj_new_str(err, strlen(err));
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
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    return run_chunk(src_obj, name_obj);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_exec_obj, mod_exec);

// load(chunks) -> None, or the error text. `chunks` is a sequence of
// (src, chunkname) pairs -- THE WHOLE CART, in the order SPEC.md 4's `sources`
// gives. Call AFTER any register()s and any exec()s.
//
// A LIST rather than one chunk, and the list has to be run HERE rather than
// through exec(), because two things bracket the cart and a caller doing it by
// hand puts one of them on the wrong side. The verb profiler arms before the
// FIRST chunk: a p8 shim resolves and captures its verbs as its own chunk runs
// (`spr = __moy_p8_spr or spr`), so wrapping after it leaves every draw call
// the cart makes going to the unwrapped original -- which is silent, and
// `verbs` is the only meter that sees this tier at all.
static mp_obj_t mod_load(mp_obj_t chunks_obj)
{
    if (!RUN.L) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    size_t nchunks = 0;
    mp_obj_t *chunks = NULL;
    mp_obj_get_array(chunks_obj, &nchunks, &chunks);
    moycore_lua_load_begin();
    for (size_t i = 0; i < nchunks; i++) {
        size_t npair = 0;
        mp_obj_t *pair = NULL;
        mp_obj_get_array(chunks[i], &npair, &pair);
        if (npair != 2)
            mp_raise_ValueError(MP_ERROR_TEXT("moycore: load wants (src, name) pairs"));
        mp_obj_t err_obj = run_chunk(pair[0], pair[1]);
        if (err_obj != mp_const_none) return err_obj;
    }
    // _init, then the collector settled (moycore_lua_load_finish).
    char err[192];
    if (moycore_lua_load_finish(err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_load_obj, mod_load);

// gc() -> the VM's heap in KB after a FULL collect, or None with no run.
//
// A DIAGNOSTIC and a test verb, not a frame-loop one: a full collect is the
// stop-the-world kind, and a running cart's churn is what the generational
// collector load() installs is there for. It exists because `collectgarbage`
// is one of the names SPEC.md 4.1 takes AWAY from a cart, so nothing else can
// ask the VM to settle -- which is exactly what tests/test_moycore_pool.py has
// to do to see a burst's garbage die and its chunks go back.
static mp_obj_t mod_gc(void)
{
    if (!RUN.L) return mp_const_none;
    lua_gc(RUN.L, LUA_GCCOLLECT);
    return mp_obj_new_int(lua_gc(RUN.L, LUA_GCCOUNT));
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_gc_obj, mod_gc);

// The profilers around a frame and a load (moycore_lua.h's hooks): the Lua
// sampler's clock re-bases per FRAME, or the gap from the last sample of one
// frame to the first of the next -- the whole of the host's Python frame,
// flush included -- is charged to whichever cart function happened to be
// running when the tick ended; the counters bracket the cart's OWN halves and
// nothing else, and a frame that errors out is not counted at all -- half a
// tick would move the ratio without being a tick.
// `s`: the counters at _update's start (cycles, events), then at its end.
static void hk_frame_begin(uint32_t *s)
{
    if (moycore_lp_on) moycore_lp_t0 = PROF_NOW();
    if (moycore_pm_on) moycore_pm_read(&s[0], &s[1]);
}

static void hk_frame_mid(uint32_t *s)
{
    if (moycore_pm_on) moycore_pm_read(&s[2], &s[3]);
}

static void hk_frame_end(int drew, uint32_t *s)
{
    if (moycore_pm_on) {
        uint32_t pc2 = 0, pe2 = 0;
        moycore_pm_read(&pc2, &pe2);
        moycore_pm_cyc[PM_UPD]  += (uint64_t)(uint32_t)(s[2] - s[0]);
        moycore_pm_evt[PM_UPD]  += (uint64_t)(uint32_t)(s[3] - s[1]);
        if (drew) {
            moycore_pm_cyc[PM_DRAW] += (uint64_t)(uint32_t)(pc2 - s[2]);
            moycore_pm_evt[PM_DRAW] += (uint64_t)(uint32_t)(pe2 - s[3]);
        }
        moycore_pm_frames++;
    }
    moycore_prof_frames++;
    moycore_lp_frames++;
}

// The verb profiler arms before the FIRST chunk: a p8 shim resolves and
// captures its verbs as its own chunk runs (`spr = __moy_p8_spr or spr`), so
// wrapping after it leaves every draw call the cart makes going to the
// unwrapped original. The Lua profiler arms AFTER the chunks, for the opposite
// reason: it captures nothing at load, but its shim pin reads the shim's own
// `_draw`, which does not exist until the chunk that defines it has run.
static void hk_load_begin(lua_State *L)
{
    if (moycore_prof_arm) moycore_prof_install(L);
}

static void hk_load_end(lua_State *L)
{
    if (moycore_lp_arm)
        moycore_lprof_install(L, moycore_lp_arm_iv, moycore_lp_arm_lo, moycore_lp_arm_hi);
}

const moycore_lua_hooks_t moycore_lua_hooks = {
    hk_load_begin, hk_load_end, hk_frame_begin, hk_frame_mid, hk_frame_end,
};

// tick(dt[, draw]) -> None on a clean frame, else the error text.
//
// The whole frame: reset the per-frame draw state (SPEC.md's rule that draw
// state must not leak between frames or from host UI into a cart), then
// _update and -- unless `draw` is false -- _draw. A logic-only tick is the
// Player's scheduler (#217) skipping _draw on a tick its divisor does not
// draw, which SPEC.md 5 sanctions; TICK_DRAW in the module table is how the
// glue knows this build takes the flag. The caller refreshed the snapshot
// before calling and plays the audio queue after -- the kernel's Player
// (moy_play.c), which calls moycore_frame itself, or the glue's own _update.

void moycore_stuck(const char *what)
{
    if (RUN.L) {
        moycore_lua_stuck(what);
        return;
    }
#if MOYCORE_WASM
    if (RUN.wasm) moy_wasm_session_terminate();
#endif
}

int moycore_frame(float dt, int draw, char *err, size_t n)
{
    moy_reset_state(&RUN.c.canvas);
    moycore_run_tick_begin();   // h_time_ms counts from here
    moycore_run_tick_dt(dt);
    if (!RUN.L) {
        // A compiled cart: its two hooks on the engine's thread.
#if MOYCORE_WASM
        return moycore_wasm_tick_c(dt, draw, err, n) != 0 ? -1 : 0;
#else
        (void)dt; (void)draw; (void)err; (void)n;
        return 0;
#endif
    }
    return moycore_lua_tick(dt, draw, err, n);
}

static mp_obj_t mod_tick(size_t n_args, const mp_obj_t *args)
{
    if (!RUN.open) mp_raise_msg(&mp_type_RuntimeError,
                                MP_ERROR_TEXT("moycore: no run"));
    char err[192];
    int draw = n_args < 2 || mp_obj_is_true(args[1]);
    if (moycore_frame((float)mp_obj_get_float(args[0]), draw, err, sizeof(err)) != 0)
        return mp_obj_new_str(err, strlen(err));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_tick_obj, 1, 2, mod_tick);

// tick_split(out=None) -> (update_us, draw_us) for the last tick: the halves
// the loop's own clock cannot see, because it times `update()` and `draw()`
// and moycore runs BOTH inside update() -- every per-cart number this project
// has recorded since #67 is a logic/render pair, and the lump against them
// reads as a doubling of logic that never happened. Microseconds,
// not the loop's milliseconds: a cart frame this project cares about is
// single-digit ms and a 1ms tick would quantise the split into uselessness.
// Given `out` -- two int32 slots, an array("i") -- it writes the pair there and
// returns `out`: the Player asks after every tick, and a tuple an ask is
// garbage on every frame of every Lua and compiled cart.
static mp_obj_t mod_tick_split(size_t n_args, const mp_obj_t *args)
{
    if (n_args > 0 && args[0] != mp_const_none) {
        size_t len = 0;
        int32_t *o = (int32_t *)moycore_buf_w(args[0], &len);
        if (len < 2 * sizeof(int32_t))
            mp_raise_ValueError(MP_ERROR_TEXT("tick_split: needs two int32 slots"));
        moycore_run_split((uint32_t *)&o[0], (uint32_t *)&o[1]);
        return args[0];
    }
    uint32_t us[2];
    moycore_run_split(&us[0], &us[1]);
    mp_obj_t t[2];
    t[0] = mp_obj_new_int((mp_int_t)us[0]);
    t[1] = mp_obj_new_int((mp_int_t)us[1]);
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_tick_split_obj, 0, 1, mod_tick_split);

// pmem_image(out) -> dirty flag. The host persists at boundaries (#66), so it
// asks for the image rather than being told about every poke.
static mp_obj_t mod_pmem_image(mp_obj_t out)
{
    size_t len = 0;
    int32_t *p = (int32_t *)moycore_buf_w(out, &len);
    size_t n = len / sizeof(int32_t);
    if (n > 256) n = 256;
    return mp_obj_new_bool(moycore_run_pmem_image(&RUN.c, p, (int)n));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_pmem_image_obj, mod_pmem_image);

// flag_xor(n, mask) -- an Editor flag edit reaching the running cart: tile
// n's flag byte in the run's own table flips `mask`'s bits, so what the
// cart's fset wrote to the other bits stays.
static mp_obj_t mod_flag_xor(mp_obj_t n_obj, mp_obj_t mask_obj)
{
    if (RUN.open)
        moycore_run_flag_xor(&RUN.c, mp_obj_get_int(n_obj), mp_obj_get_int(mask_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_flag_xor_obj, mod_flag_xor);

// retarget(fb) -- the double/triple-buffered tiers swap the framebuffer under
// the canvas every frame, exactly as DeviceCanvas.sync_back does.
static mp_obj_t mod_retarget(mp_obj_t fb_obj)
{
    if (!RUN.open) return mp_const_none;
    size_t len = 0;
    moy_pixel *fb = (moy_pixel *)moycore_buf_w(fb_obj, &len);
    if (len < (size_t)RUN.c.canvas.w * (size_t)RUN.c.canvas.h * sizeof(moy_pixel))
        mp_raise_ValueError(MP_ERROR_TEXT("retarget: framebuffer too small"));
    RUN.c.canvas.pix = fb;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_retarget_obj, mod_retarget);

// The run closed, from C: what close() does, which a run the kernel opened
// with no VM (moycore_wasm_open_c) ends with too. Writes the module's root
// pointers to nothing, which with no VM is the zeroed root section's value.
void moycore_close_c(void)
{
#if MOYCORE_WASM
    moycore_wasm_end();                  // the session first: it draws on the console
#endif
    moycore_lua_close();         // the VM, then the pool it held
    layer_restore_drop();        // the prediction may name a layer the run frees
    RUN.open = 0;
    moycore_prof_on = 0;               // the wrappers died with the VM
    moycore_prof_n = 0;
    moycore_lprof_forget();              // the hook went with it; the table is ours
    moycore_run_close(&RUN.c);
    for (int k = 0; k < KEEP_N; k++) keep_set(k, MP_OBJ_NULL);   // un-root: the gc may reclaim
    moycore_run_end(&RUN);
}

static mp_obj_t mod_close(void)
{
    moycore_close_c();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_close_obj, mod_close);

// A compiled cart's run opened from C, with no VM (docs/history/kernel_cartpath_2026-10.md
// section 5.2, the load after the stop): run_begin's console over the
// caller's buffers, then wasm_open's session -- the same two steps the binding
// takes, every buffer the caller's for the run's life. 0, or non-zero with the
// refusal or the trap in `err` (the run is then open: moycore_close_c closes
// it). A run already open, or no engine in the build, refuses.
int moycore_wasm_open_c(const moycore_open_c_t *o, char *err, size_t n)
{
#if MOYCORE_WASM
    if (moycore_run_new() == NULL) {
        snprintf(err, n, "moycore: every run row is held");
        return 1;
    }
    moycore_lua_meters_reset();
    moy_canvas_init(&RUN.c.canvas, (moy_pixel *)o->fb, o->w, o->h);
#ifdef MOY_PIXEL_RGB565
    if (o->wire != NULL) moy_canvas_wire(&RUN.c.canvas, o->wire);
#endif
    RUN.c.snap = o->snap;
    moycore_run_open(&RUN.c, o->snap, o->aq, o->aq_cap);
    if (o->pmem != NULL) moycore_run_pmem_load(&RUN.c, o->pmem, 256);
    if (o->cfg != NULL && moycore_run_set_cfg(&RUN.c, o->cfg, o->cfg_len) != 0) {
        snprintf(err, n, "out of memory: no room for the config");
        moycore_run_close(&RUN.c);
        return 1;
    }
    memset(RUN.c.flags, 0, sizeof(RUN.c.flags));
    RUN.c.con.sheet = NULL;
    RUN.c.con.map = NULL;
    RUN.c.con.rng = (uint32_t)mp_hal_ticks_us();
    if (RUN.c.con.rng == 0) RUN.c.con.rng = 1;
    uint32_t lock_seed;
    if (moy_play_lock_seed != NULL && moy_play_lock_seed(&lock_seed)) {
        RUN.c.con.rng = lock_seed;
    }
    RUN.open = 1;
    moycore_whead = o->head;
    moycore_whead_len = o->head_len;
    moycore_wpages = o->pages;
    int rc = moycore_wasm_begin(o->module, o->sha, o->dir, o->writable, o->writable_len,
                                o->swapped, o->allow_unsigned, o->interp, o->ring, o->app,
                                err, n);
    moycore_whead = NULL;
    moycore_whead_len = 0;
    return rc;
#else
    (void)o;
    snprintf(err, n, "moycore: this build has no wasm engine");
    return 1;
#endif
}

// The kernel's stop (native/moy_kernel): nonzero while the VM goes for a VM
// stop rather than a soft reset. Weak, for an image without the stop.
int moy_kernel_stop_pending(void) __attribute__((weak));

// The VM is going (native/moy_kernel's soft reset, a VM stop's included): a
// run still open closes with it, while its objects are still the VM's; the
// Lua services run on through a VM stop and end with a soft reset.
void moycore_vm_stop(void)
{
    for (int i = 0; i < MOYCORE_RUNS; i++) {
        moycore_run *r = moycore_run_at(i);
        if (r == NULL || !r->open) continue;
        moycore_run_use(r);
        moycore_close_c();
    }
    moycore_svc_vm_stop(moy_kernel_stop_pending != NULL && moy_kernel_stop_pending() != 0);
    MP_STATE_VM(moycore_keep) = MP_OBJ_NULL;    // the list is the heap going
}

// get_global_len(name) -- a table global's length (Lua's #t), or None: the
// size of a cart's world, the cheapest true thing to ask about a table.
static mp_obj_t mod_get_global_len(mp_obj_t name_obj)
{
    if (!RUN.L) return mp_const_none;
    lua_getglobal(RUN.L, mp_obj_str_get_str(name_obj));
    mp_obj_t out = mp_const_none;
    if (lua_type(RUN.L, -1) == LUA_TTABLE)
        out = mp_obj_new_int((mp_int_t)lua_rawlen(RUN.L, -1));
    lua_pop(RUN.L, 1);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_get_global_len_obj, mod_get_global_len);

// get_global(name) -- read a cart global. The parity suites compare a Lua
// cart's state against its Python twin's, which needs a way in; libmoy's
// binding owns the VM but not the host's curiosity about it.
static mp_obj_t mod_get_global(mp_obj_t name_obj)
{
    if (!RUN.L) return mp_const_none;
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
//
// The console asks after every tick, so the answer is the SAME tuple for as
// long as the declaration stands (moycore_view, dropped by run_begin) and a new
// one only when the cart changes it: a caller holding the last answer sees a
// change as a different object with different values, and no change costs no
// allocation.
static mp_obj_t mod_view(void)
{
    if (!RUN.open || RUN.c.con.view_w <= 0) return mp_const_none;
    mp_obj_t last = keep_get(KEEP_VIEW);
    if (last != MP_OBJ_NULL) {
        mp_obj_tuple_t *lt = MP_OBJ_TO_PTR(last);
        if (MP_OBJ_SMALL_INT_VALUE(lt->items[0]) == RUN.c.con.view_w
            && MP_OBJ_SMALL_INT_VALUE(lt->items[1]) == RUN.c.con.view_h) {
            return last;
        }
    }
    mp_obj_t t[2];
    t[0] = MP_OBJ_NEW_SMALL_INT(RUN.c.con.view_w);
    t[1] = MP_OBJ_NEW_SMALL_INT(RUN.c.con.view_h);
    last = mp_obj_new_tuple(2, t);
    keep_set(KEEP_VIEW, last);
    return last;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_view_obj, mod_view);

static mp_obj_t mod_active(void) { return mp_obj_new_bool(RUN.open); }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_active_obj, mod_active);

// use(h) -- the run run_begin answered `h` made current, so every call after
// acts on it: True, or False when no run holds `h` any more.
static mp_obj_t mod_use(mp_obj_t h_obj)
{
    moycore_run *r = moycore_run_of((uint32_t)mp_obj_get_int(h_obj));
    if (r == NULL) return mp_const_false;
    moycore_run_use(r);
    return mp_const_true;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_use_obj, mod_use);

// runs() -- how many runs are open at once.
static mp_obj_t mod_runs(void) { return MP_OBJ_NEW_SMALL_INT(moycore_run_count()); }
static MP_DEFINE_CONST_FUN_OBJ_0(mod_runs_obj, mod_runs);

// heap(h) -- run `h`'s Lua heap in bytes, Lua's own count; None for no run
// or one with no VM.
static mp_obj_t mod_heap(mp_obj_t h_obj)
{
    moycore_run *r = moycore_run_of((uint32_t)mp_obj_get_int(h_obj));
    if (r == NULL || r->L == NULL) return mp_const_none;
    return mp_obj_new_int_from_uint(moycore_run_heap(r));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_heap_obj, mod_heap);

// set_sram_floor(kb) -> the effective kb. Clamped [16, 256]; returns the
// compiled default unchanged on a build with no region to choose between, so
// callers can set it unconditionally. run_desktop calls this once the boot-time
// internal claims (flush bounce, poller, audio reserve) have been taken.
static mp_obj_t mod_set_sram_floor(mp_obj_t kb_obj)
{
    mp_int_t kb = mp_obj_get_int(kb_obj);
    size_t b = moycore_lua_set_sram_floor(kb < 0 ? 0 : (size_t)kb * 1024);
    return mp_obj_new_int((mp_int_t)(b / 1024));
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
    moycore_lua_meters_t m;
    moycore_lua_meters(&m);
    mp_obj_t items[7];
    items[0] = mp_obj_new_int((mp_int_t)m.sram_live);
    items[1] = mp_obj_new_int((mp_int_t)m.psram_live);
    items[2] = mp_obj_new_int((mp_int_t)m.peak);
    items[3] = mp_obj_new_int((mp_int_t)m.sram_denied);
    items[4] = mp_obj_new_int((mp_int_t)m.pool_live);
    items[5] = mp_obj_new_int((mp_int_t)m.pool_cap);
    items[6] = mp_obj_new_int((mp_int_t)m.pool_chunks);
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
    if (!moycore_lua_two_regions()) return mp_const_none;
    moycore_lua_meters_t m;
    moycore_lua_meters(&m);
    mp_obj_t t[3];
    t[0] = (m.sram_free_min == SIZE_MAX) ? mp_const_none
                                         : mp_obj_new_int((mp_int_t)m.sram_free_min);
    t[1] = mp_obj_new_bool(m.psram_fallback);
    t[2] = mp_obj_new_int((mp_int_t)m.sram_floor);
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
    return mp_obj_new_int((mp_int_t)moycore_lua_sram_sim((size_t)mp_obj_get_int(bytes_obj)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_sram_sim_obj, mod_sram_sim);
#endif

// pool_check() -> 0 when the pool's own invariants hold, else a negative code
// (README.md lists them). A TEST verb: the invariant that a block masks back
// to the chunk it came from cannot be seen from Python and does not fault when
// it breaks -- it writes through another chunk's header instead.
static mp_obj_t mod_pool_check(void)
{
    return MP_OBJ_NEW_SMALL_INT(moycore_lua_pool_check());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_pool_check_obj, mod_pool_check);

// -- the Lua tier's services (moycore_svc.h) ---------------------------------
//   svc_start(pid, name, src[, env[, app]]) -> h   svc_stop(h)   svc_reap(h)
//                                 (app: its app's run, moy_app's `run()`)
//   svc_info(h) -> (state, exit, heap, cpu_us, strikes, why, wakes, mail), None when stale
//   svc_tasks() -> bool   svc_step(now_ms) -> ms to the next deadline   svc_reset()

static void svc_check(int rc)
{
    static const int err[] = { 0, MP_EBADF, MP_ENOSPC, MP_ENOMEM, MP_EINVAL };
    if (rc != MOYCORE_SVC_OK) {
        mp_raise_OSError(rc > 0 && rc < (int)(sizeof(err) / sizeof(err[0])) ? err[rc] : MP_EIO);
    }
}

static mp_obj_t mod_svc_start(size_t n_args, const mp_obj_t *args)
{
    size_t n;
    const char *text = mp_obj_str_get_data(args[2], &n);
    const char *env = NULL;
    size_t envn = 0;
    if (n_args > 3 && args[3] != mp_const_none) {
        env = mp_obj_str_get_data(args[3], &envn);
    }
    const struct moy_app_run *app = n_args > 4 ? moycore_app_run(args[4]) : NULL;
    uint32_t h;
    svc_check(moycore_svc_start((uint32_t)mp_obj_get_int(args[0]), mp_obj_str_get_str(args[1]),
                                text, n, env, envn, app, &h));
    return mp_obj_new_int_from_uint(h);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_svc_start_obj, 3, 5, mod_svc_start);

static mp_obj_t mod_svc_stop(mp_obj_t h)
{
    svc_check(moycore_svc_stop((uint32_t)mp_obj_get_int(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_svc_stop_obj, mod_svc_stop);

static mp_obj_t mod_svc_info(mp_obj_t h)
{
    moycore_svc_info_t i;
    if (moycore_svc_info((uint32_t)mp_obj_get_int(h), &i) != MOYCORE_SVC_OK) {
        return mp_const_none;
    }
    mp_obj_t t[8] = {
        MP_OBJ_NEW_SMALL_INT(i.state), mp_obj_new_int(i.exit), mp_obj_new_int_from_uint(i.heap),
        mp_obj_new_int_from_uint(i.cpu_us), MP_OBJ_NEW_SMALL_INT(i.strikes),
        mp_obj_new_str(i.why, strlen(i.why)), MP_OBJ_NEW_SMALL_INT(i.wakes),
        MP_OBJ_NEW_SMALL_INT(i.mail),
    };
    return mp_obj_new_tuple(8, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_svc_info_obj, mod_svc_info);

static mp_obj_t mod_svc_reap(mp_obj_t h)
{
    svc_check(moycore_svc_reap((uint32_t)mp_obj_get_int(h)));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_svc_reap_obj, mod_svc_reap);

static mp_obj_t mod_svc_tasks(void)
{
    return mp_obj_new_bool(moycore_svc_tasks());
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_svc_tasks_obj, mod_svc_tasks);

static mp_obj_t mod_svc_step(mp_obj_t now)
{
    return mp_obj_new_int_from_uint(moycore_svc_step((uint32_t)mp_obj_get_int_truncated(now)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_svc_step_obj, mod_svc_step);

static mp_obj_t mod_svc_reset(void)
{
    moycore_svc_reset();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_svc_reset_obj, mod_svc_reset);

static const mp_rom_map_elem_t moycore_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_OBJ_NEW_QSTR(MP_QSTR_moycore) },
    { MP_ROM_QSTR(MP_QSTR_run_begin),   MP_ROM_PTR(&mod_run_begin_obj) },
    { MP_ROM_QSTR(MP_QSTR_flag_xor),    MP_ROM_PTR(&mod_flag_xor_obj) },
    { MP_ROM_QSTR(MP_QSTR_p8_memory), MP_ROM_PTR(&mod_p8_memory_obj) },
    { MP_ROM_QSTR(MP_QSTR_register),    MP_ROM_PTR(&mod_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_exec),        MP_ROM_PTR(&mod_exec_obj) },
    { MP_ROM_QSTR(MP_QSTR_load),        MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick),        MP_ROM_PTR(&mod_tick_obj) },
    { MP_ROM_QSTR(MP_QSTR_TICK_DRAW),   MP_ROM_INT(1) },
    // The compiled cart: 1 when the engine is in this build (wasm_open works).
    { MP_ROM_QSTR(MP_QSTR_WASM),        MP_ROM_INT(MOYCORE_WASM) },
    { MP_ROM_QSTR(MP_QSTR_wasm_open),   MP_ROM_PTR(&moycore_wasm_open_obj) },
    { MP_ROM_QSTR(MP_QSTR_files_keep),  MP_ROM_PTR(&moycore_files_keep_obj) },
    { MP_ROM_QSTR(MP_QSTR_files_room),  MP_ROM_PTR(&moycore_files_room_obj) },
    { MP_ROM_QSTR(MP_QSTR_files_kept),  MP_ROM_PTR(&moycore_files_kept_obj) },
    { MP_ROM_QSTR(MP_QSTR_wasm_quit),   MP_ROM_PTR(&moycore_wasm_quit_obj) },
    { MP_ROM_QSTR(MP_QSTR_wasm_ran),    MP_ROM_PTR(&moycore_wasm_ran_obj) },
    { MP_ROM_QSTR(MP_QSTR_wasm_table),  MP_ROM_PTR(&moycore_wasm_table_obj) },
    { MP_ROM_QSTR(MP_QSTR_take_frames), MP_ROM_PTR(&moycore_take_frames_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame),       MP_ROM_PTR(&moycore_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_settle), MP_ROM_PTR(&moycore_frame_settle_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_kept),  MP_ROM_PTR(&moycore_frame_kept_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_presented), MP_ROM_PTR(&moycore_frame_presented_obj) },
    { MP_ROM_QSTR(MP_QSTR_tick_split),  MP_ROM_PTR(&mod_tick_split_obj) },
    { MP_ROM_QSTR(MP_QSTR_profile),     MP_ROM_PTR(&moycore_profile_obj) },
    { MP_ROM_QSTR(MP_QSTR_verb_stats),  MP_ROM_PTR(&moycore_verb_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_verb_reset),  MP_ROM_PTR(&moycore_verb_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_profile), MP_ROM_PTR(&moycore_lua_profile_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_stats),   MP_ROM_PTR(&moycore_lua_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_reset),   MP_ROM_PTR(&moycore_lua_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_lua_gc_mode), MP_ROM_PTR(&moycore_lua_gc_mode_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_counters), MP_ROM_PTR(&moycore_perf_counters_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_stats),  MP_ROM_PTR(&moycore_perf_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_reset),  MP_ROM_PTR(&moycore_perf_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_perf_read),   MP_ROM_PTR(&moycore_perf_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_pmem_image),  MP_ROM_PTR(&mod_pmem_image_obj) },
    { MP_ROM_QSTR(MP_QSTR_retarget),    MP_ROM_PTR(&mod_retarget_obj) },
    { MP_ROM_QSTR(MP_QSTR_layer_bind),  MP_ROM_PTR(&mod_layer_bind_obj) },
    { MP_ROM_QSTR(MP_QSTR_image_put),   MP_ROM_PTR(&mod_image_put_obj) },
    { MP_ROM_QSTR(MP_QSTR_scene_put),   MP_ROM_PTR(&mod_scene_put_obj) },
    { MP_ROM_QSTR(MP_QSTR_layer_restore), MP_ROM_PTR(&mod_layer_restore_obj) },
    { MP_ROM_QSTR(MP_QSTR_close),       MP_ROM_PTR(&mod_close_obj) },
    { MP_ROM_QSTR(MP_QSTR_gc),          MP_ROM_PTR(&mod_gc_obj) },
    { MP_ROM_QSTR(MP_QSTR_active),      MP_ROM_PTR(&mod_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_use),         MP_ROM_PTR(&mod_use_obj) },
    { MP_ROM_QSTR(MP_QSTR_runs),        MP_ROM_PTR(&mod_runs_obj) },
    { MP_ROM_QSTR(MP_QSTR_heap),        MP_ROM_PTR(&mod_heap_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_start),   MP_ROM_PTR(&mod_svc_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_stop),    MP_ROM_PTR(&mod_svc_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_info),    MP_ROM_PTR(&mod_svc_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_reap),    MP_ROM_PTR(&mod_svc_reap_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_tasks),   MP_ROM_PTR(&mod_svc_tasks_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_step),    MP_ROM_PTR(&mod_svc_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_svc_reset),   MP_ROM_PTR(&mod_svc_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_sram_floor), MP_ROM_PTR(&mod_set_sram_floor_obj) },
    { MP_ROM_QSTR(MP_QSTR_alloc_stats), MP_ROM_PTR(&mod_alloc_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_sram_report), MP_ROM_PTR(&mod_sram_report_obj) },
#ifndef MOYCORE_PSRAM
    { MP_ROM_QSTR(MP_QSTR_sram_sim),    MP_ROM_PTR(&mod_sram_sim_obj) },
#endif
    { MP_ROM_QSTR(MP_QSTR_pool_check), MP_ROM_PTR(&mod_pool_check_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_global),  MP_ROM_PTR(&mod_get_global_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_global_len), MP_ROM_PTR(&mod_get_global_len_obj) },
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
    { MP_ROM_QSTR(MP_QSTR_SNAP_WINDOW), MP_ROM_INT(SNAP_WINDOW) },
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

// What each run row keeps against the gc (keep_get): its registered Python
// callables, which the Lua closures reference only by INDEX the collector
// cannot see, its layer restore state, its last view() answer and its p8
// machine's buffers. Cleared at the row's close.
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_keep);
// The PICO-8 machine's buffers p8_memory hands the next run_begin.
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_p8mem);
MP_REGISTER_ROOT_POINTER(mp_obj_t moycore_p8rom);
