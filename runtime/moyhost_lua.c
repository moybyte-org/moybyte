/* clock_gettime/CLOCK_MONOTONIC are POSIX, and this builds at -std=c99,
 * which hides them. Must precede every include. */
#define _POSIX_C_SOURCE 200809L

/* The host's libmoy LUA shim (moycore plan rung 4).
 *
 * The host sim used to run Lua carts through lupa -- a second Lua embedding
 * with second semantics (64-bit doubles where both boards build LUA_32BITS) --
 * until this shim replaced it (lupa deleted 2026-08-14). CPython gets the
 * SAME program the boards run: libmoy's binding of the spec verb table, over
 * the same vendored Lua, built the same way.
 *
 * The console and its snapshot-in/queue-out host callbacks are
 * moyhost_console.h's -- modmoycore.c's host half with the MicroPython
 * removed, one copy shared with the wasm shim (moyhost_wasm.c), because a
 * host and a device that disagree about what a verb does is the disease. What
 * differs from the board is only how the host talks to it: plain C
 * signatures ctypes can call, and buffers the caller owns.
 *
 * THE PIXEL FORMAT IS RGB565, as it is on both boards (moycore's micropython.mk
 * sets the same -DMOY_PIXEL_RGB565=1). A libmoy built for indices computes
 * y*w+x over ONE byte per pixel; the same source built for direct colour
 * computes it over two, and the two cannot share a library -- so a shim that
 * was compiled the other way would write half-width rows of raw indices into a
 * 565 framebuffer and there is nothing at runtime that would say so. The #error
 * in moyhost_console.h is that check, moved to compile time.
 *
 * A canvas that is STILL INDEXED is bridged rather than refused (`indexed=1`):
 * libmoy draws into a private 565 shadow whose wire table is the IDENTITY, so a
 * "colour word" is literally the palette index, and the two buffers differ only
 * in width -- widen in, narrow out, per frame, losslessly. That exists for the
 * host's transition to the boards' canvas class (#161) and nothing else: when
 * `runtime/host_app.py` hands over a DeviceCanvas, delete the bridge and the
 * `indexed` argument with it.
 */

#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moyhost_console.h"

typedef struct {
    hc_console  hc;          /* the console, shared with moyhost_wasm.c */
    lua_State  *L;
    int         has_sheet, has_map;
    moy_p8      p8;          /* the PICO-8 machine (libmoy moy_p8.c), opened
                                at hl_load so it seeds from the assets */
    uint8_t    *p8mem, *p8rom;
    uint8_t    *idx;         /* the transitional INDEX buffer, or NULL when the
                                caller's canvas is already RGB565 */
    moy_pixel  *shadow;      /* the 565 buffer libmoy draws into when it is */
    int         npix;
} host_lua;

static host_lua *CUR;        /* the run hl_tramp and hl_set_dispatch serve */

/* -- extension verbs -------------------------------------------------------
 *
 * The superset (make_layer/draw_layer/image/view/background) is not libmoy's,
 * and is not reimplemented here either: it is registered on top of libmoy's
 * table as a trampoline back into Python. Same correction as the device glue
 * -- a cart needing a Python-backed verb needs one engine that can hold one,
 * not a second engine.
 *
 * The contract is what moycore's l_tramp already had, because a host and a
 * device that disagree about what an argument IS is the same disease as one
 * that disagree about what a verb does: up to eight arguments, each an
 * integer, a string, a boolean or nil, each WHERE THE CART PUT IT, and an
 * integer, a string, a boolean or nothing back. Objects still never cross --
 * layers, images and actors travel as int handles, which is what the prelude's
 * wrappers speak, and a whole scene crosses as one encoded string.
 *
 * It used to be an int vector plus "the first string", with a boolean landing
 * as 0 and every later string dropped: __actor_flag(id, "hidden", true) would
 * have arrived as ("hidden", id, 0). Nothing mixed the kinds until the
 * placement verbs did, which is why that survived this long; every existing
 * verb (all ints, or the lone string of image("bg")) sees what it always saw. */
/* Eight, not four: the widest wrapper is the prelude's
 * __layer_spr(lid, tile, x, y, ck, scale, flip) at seven. Four silently
 * TRUNCATED it -- the extra arguments never reached Python, the closure raised
 * on its missing parameters, and hl_tramp reads a raising verb as nil. A layer
 * sprite would simply not draw, with nothing printed anywhere. */
#define HL_MAX_IARGS 8

/* Argument i: HL_NUM takes iargs[i], HL_STR sargs[i], HL_BOOL iargs[i] != 0,
 * HL_NIL nothing. Result: 0 nothing, 1 the integer in *out, 2 the *out bytes
 * at *sout, 3 the boolean *out != 0. */
#define HL_NUM  0
#define HL_STR  1
#define HL_BOOL 2
#define HL_NIL  3

typedef int (*hl_dispatch_fn)(int idx, int argc, const int *kinds,
                              const int *iargs, const char **sargs,
                              int *out, const char **sout);

static hl_dispatch_fn CUR_DISPATCH;

static int hl_tramp(lua_State *L)
{
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
    int n = lua_gettop(L);
    int kinds[HL_MAX_IARGS];
    int iargs[HL_MAX_IARGS];
    const char *sargs[HL_MAX_IARGS];
    int ic = 0;
    for (int i = 1; i <= n && ic < HL_MAX_IARGS; i++) {
        int t = lua_type(L, i);
        sargs[ic] = NULL;
        iargs[ic] = 0;
        if (t == LUA_TSTRING) {
            kinds[ic] = HL_STR;
            sargs[ic] = lua_tostring(L, i);
        } else if (t == LUA_TBOOLEAN) {
            kinds[ic] = HL_BOOL;
            iargs[ic] = lua_toboolean(L, i);
        } else if (t == LUA_TNIL || t == LUA_TNONE) {
            kinds[ic] = HL_NIL;
        } else {
            kinds[ic] = HL_NUM;
            iargs[ic] = (int)lua_tointeger(L, i);
        }
        ic++;
    }
    if (CUR_DISPATCH == NULL) return 0;
    int out = 0;
    const char *sout = NULL;
    int has = CUR_DISPATCH(idx, ic, kinds, iargs, sargs, &out, &sout);
    if (has == 1) { lua_pushinteger(L, out); return 1; }
    if (has == 2 && sout != NULL) {
        lua_pushlstring(L, sout, (size_t)out);
        return 1;
    }
    if (has == 3) { lua_pushboolean(L, out); return 1; }
    return 0;
}

void hl_set_dispatch(host_lua *r, hl_dispatch_fn fn) { (void)r; CUR_DISPATCH = fn; }

/* Register `name` as a Lua global calling back with `idx`. After hl_new and
 * BEFORE hl_load: a cart captures its globals into locals as it executes. */
void hl_register(host_lua *r, const char *name, int idx)
{
    lua_pushinteger(r->L, idx);
    lua_pushcclosure(r->L, hl_tramp, 1);
    lua_setglobal(r->L, name);
}

/* -- the indexed bridge (transitional; see the header note) ----------------
 *
 * With an identity wire table every word libmoy stores is `store[pal[i]]`,
 * i.e. the remapped INDEX -- the exact byte the indexed build would have
 * written, and the exact byte `runtime/canvas.py` holds. So the two buffers are
 * the same picture at two widths and the conversion is a widen and a narrow
 * with no palette in it: nothing can be lost, and no reverse lookup can pick
 * the wrong index when two palette entries share a colour. */
static void hl_widen(host_lua *r)
{
    int i;
    if (!r->idx) return;
    for (i = 0; i < r->npix; i++) r->shadow[i] = r->idx[i];
}

static void hl_narrow(host_lua *r)
{
    int i;
    if (!r->idx) return;
    /* & 63 for the same reason the indexed canvas masks: SPEC.md 2 has 64
     * colours, so a wider word cannot be a legal index. */
    for (i = 0; i < r->npix; i++) r->idx[i] = (uint8_t)(r->shadow[i] & 63);
}

/* `nbytes` is the caller's buffer size, and it is CHECKED rather than trusted:
 * ctypes hands over a bare pointer, so a w/h that outruns the allocation is a
 * heap overwrite with no Python-side trace. NULL back is the answer, which the
 * binding turns into an ordinary exception.
 *
 * `wire` is the 64-entry index -> 16-bit word table (the boards pass their
 * canvas's, byte-swapped or not); NULL means libmoy's canonical RGB565 of the
 * SPEC.md 2.2 palette. Ignored when `indexed`, which owns its table. */
host_lua *hl_new(void *pix, int nbytes, int w, int h, int indexed,
                 const uint16_t *wire, int32_t *snap, int32_t *aq, int aq_cap)
{
    host_lua *r;
    long npix = (long)w * (long)h;
    int bpp = indexed ? 1 : (int)sizeof(moy_pixel);
    if (w <= 0 || h <= 0 || npix > (long)(nbytes / bpp)) return NULL;
    r = (host_lua *)calloc(1, sizeof(host_lua));
    if (!r) return NULL;
    r->npix = (int)npix;
    if (indexed) {
        int i;
        uint16_t ident[MOY_PALETTE];
        r->idx = (uint8_t *)pix;
        r->shadow = (moy_pixel *)calloc((size_t)npix, sizeof(moy_pixel));
        if (!r->shadow) { free(r); return NULL; }
        moy_canvas_init(&r->hc.canvas, r->shadow, w, h);
        for (i = 0; i < MOY_PALETTE; i++) ident[i] = (uint16_t)i;
        moy_canvas_wire(&r->hc.canvas, ident);
    } else {
        moy_canvas_init(&r->hc.canvas, (moy_pixel *)pix, w, h);
        if (wire) moy_canvas_wire(&r->hc.canvas, wire);
    }
    hc_open(&r->hc, snap, aq, aq_cap);
    r->L = luaL_newstate();
    if (!r->L) { free(r->shadow); free(r); return NULL; }
    CUR = r;
    HC = &r->hc;
    if (moy_lua_open(r->L, &r->hc.con) != 0) {
        lua_close(r->L); free(r->shadow); free(r); CUR = NULL; HC = NULL; return NULL;
    }
    return r;
}

/* The sheet, flags and map: moyhost_console.h's, which says why each is
 * checked or copied. Call hl_set_flags BEFORE hl_load, which is where the p8
 * machine copies the table into 0x3000. */
void hl_set_sheet(host_lua *r, uint8_t *pix, int nbytes)
{ hc_set_sheet(&r->hc, pix, nbytes); }

void hl_set_flags(host_lua *r, const uint8_t *flags, int nbytes)
{ hc_set_flags(&r->hc, flags, nbytes); }

void hl_set_map(host_lua *r, uint8_t *cells, int nbytes, int w, int h)
{ hc_set_map(&r->hc, cells, nbytes, w, h); }

/* The cart's config.json as "key\0value\0" pairs (lua_binding.cfg_blob), so
 * `cfg("speed", 3)` answers here what it answers on a board. */
void hl_set_cfg(host_lua *r, const char *blob, int len)
{ hc_set_cfg(&r->hc, blob, len); }

/* Point the run at another buffer of the SAME size -- a compositor that
 * ping-pongs. The bridged case swaps the index buffer and keeps the shadow,
 * which is the whole reason this is not a bare assignment any more. */
void hl_retarget(host_lua *r, void *pix)
{
    if (r->idx) r->idx = (uint8_t *)pix;
    else        r->hc.canvas.pix = (moy_pixel *)pix;
}

/* Run one chunk. 0 on success; the message lands in err. Text only ("t"):
 * a binary chunk is unverified bytecode, refused here exactly as moycore's
 * run_chunk refuses it on a board (native/moycore/modmoycore.c says why). */
int hl_exec(host_lua *r, const char *src, int len, const char *name,
            char *err, int errlen)
{
    CUR = r;
    HC = &r->hc;
    if (luaL_loadbufferx(r->L, src, (size_t)len, name, "t") != LUA_OK
        || lua_pcall(r->L, 0, 0, 0) != LUA_OK) {
        const char *m = lua_tostring(r->L, -1);
        if (err && errlen > 0) { strncpy(err, m ? m : "load failed", errlen - 1); err[errlen - 1] = 0; }
        return 1;
    }
    return 0;
}

/* Run the cart's chunks in order, then _init. 0 on success; the message lands
 * in err.
 *
 * A LIST, because SPEC.md 4 lets a cart be several scripts and the whole list
 * has to sit inside THIS call rather than be dribbled in through hl_exec. Two
 * things bracket the cart and both would be on the wrong side otherwise: the
 * p8 machine is opened below, and a shim chunk run before that resolves its
 * verbs to the slow Lua fallbacks instead of the C ones; and hl_widen covers
 * the chunks, which are allowed to draw.
 *
 * hl_exec stays for the GLUE PRELUDE (runtime/lua_ext.py), which has to run
 * after hl_register and before any of this: moybyte's object-valued verbs
 * reach Lua as int-handle functions plus wrappers, because this dispatch
 * marshals ints and strings and a Layer is neither. */
int hl_load(host_lua *r, const char **srcs, const int *lens, const char **names,
            int n, char *err, int errlen)
{
    /* The PICO-8 machine: opened here rather than in hl_new because it seeds
     * memory from the sheet and map, which hl_set_sheet/hl_set_map supply in
     * between. Lazily allocated, freed with the run; no memory, no machine. */
    if (!r->p8mem) r->p8mem = (uint8_t *)malloc(MOY_P8_MEM);
    if (!r->p8rom) r->p8rom = (uint8_t *)malloc(MOY_P8_ROM);
    if (r->p8mem) moy_p8_open(r->L, &r->hc.con, &r->p8, r->p8mem, r->p8rom);
    int rc = 0, i;
    /* The chunks and _init are all allowed to draw (a title screen a cart never
     * repaints is the standing case), so they get the same bridge a frame gets
     * -- and the same single exit, so a chunk that draws and then errors still
     * lands what it drew. */
    hl_widen(r);
    for (i = 0; i < n; i++) {
        rc = hl_exec(r, srcs[i], lens[i], names[i], err, errlen);
        if (rc) break;
    }
    if (rc == 0) {
        g_tick_ms = hc_now_ms();          /* _init may call time(), below */
        rc = moy_lua_init(r->L, err, (size_t)errlen);
    }
    hl_narrow(r);
    return rc;
}

/* `draw` 0 is a logic-only tick: the Player's scheduler (#217) skips _draw on
 * the ticks its divisor does not draw, which SPEC.md 5 sanctions. */
int hl_tick(host_lua *r, float dt, int draw, char *err, int errlen)
{
    int rc;
    CUR = r;
    HC = &r->hc;
    g_tick_ms = hc_now_ms();              /* h_time counts from here */
    moy_reset_state(&r->hc.canvas);
    hl_widen(r);
    /* ONE exit, so a cart that draws and THEN errors still lands its pixels --
     * the crash-to-code panel is drawn over the frame the cart died on. */
    rc = moy_lua_update(r->L, dt, err, (size_t)errlen);
    if (rc == 0 && draw) rc = moy_lua_draw(r->L, err, (size_t)errlen);
    hl_narrow(r);
    return rc;
}

int hl_pmem_image(host_lua *r, int32_t *out, int n)
{ return hc_pmem_image(&r->hc, out, n); }

void hl_pmem_load(host_lua *r, const int32_t *in, int n)
{ hc_pmem_load(&r->hc, in, n); }

/* Read a cart global as a double; returns 0 when absent or not a number, 1
 * otherwise. Numbers only: the parity suites compare counters and positions,
 * and a richer marshalling here would be a second contract to keep. An
 * INTEGER goes through lua_tointeger: under LUA_32BITS lua_Number is a float,
 * so lua_tonumber on an integer above 2^24 would round it, and a double holds
 * every int32 exactly. */
int hl_get_global_num(host_lua *r, const char *name, double *out)
{
    lua_getglobal(r->L, name);
    int ok = 0;
    if (lua_type(r->L, -1) == LUA_TNUMBER) {
        if (lua_isinteger(r->L, -1)) *out = (double)lua_tointeger(r->L, -1);
        else *out = (double)lua_tonumber(r->L, -1);
        ok = 1;
    }
    lua_pop(r->L, 1);
    return ok;
}

/* The length of a table global (Lua's #t), or -1 when it is not a table. The
 * parity suites assert on cart-world SIZES -- 120 petals, one player -- which
 * is the cheapest true thing to ask about a table without marshalling it. */
int hl_get_global_len(host_lua *r, const char *name)
{
    lua_getglobal(r->L, name);
    int n = -1;
    if (lua_type(r->L, -1) == LUA_TTABLE) n = (int)lua_rawlen(r->L, -1);
    lua_pop(r->L, 1);
    return n;
}

/* The Lua heap in bytes -- what SPEC.md 1.1's "Cart heap" row budgets. Taken
 * after a full collect so it is live data rather than uncollected garbage:
 * the floor has to cover what a cart KEEPS, and a host may collect whenever. */
int hl_heap_bytes(host_lua *r)
{
    lua_gc(r->L, LUA_GCCOLLECT, 0);
    return lua_gc(r->L, LUA_GCCOUNT, 0) * 1024 + lua_gc(r->L, LUA_GCCOUNTB, 0);
}

/* The same WITHOUT collecting: what the heap actually reaches mid-play, which
 * is the number that decides whether a host must reserve headroom. */
int hl_heap_peak_bytes(host_lua *r)
{
    return lua_gc(r->L, LUA_GCCOUNT, 0) * 1024 + lua_gc(r->L, LUA_GCCOUNTB, 0);
}

/* What the cart last declared with view(), or 0 when it has not. */
int hl_get_view(host_lua *r, int *w, int *h)
{ return hc_get_view(&r->hc, w, h); }

void hl_free(host_lua *r)
{
    if (r) { free(r->p8mem); free(r->p8rom); }
    if (!r) return;
    if (r->L) lua_close(r->L);
    if (CUR == r) CUR = NULL;
    hc_close(&r->hc);
    free(r->shadow);
    free(r);
}
