/* The host's libmoy LUA shim (moycore plan rung 4).
 *
 * The host sim runs Lua carts through lupa -- a second Lua embedding, with a
 * second set of semantics: lupa is 64-bit doubles where both boards build
 * LUA_32BITS (their FPUs are single-precision, so doubles would be soft-float),
 * which is a standing parity hole the plan records. This shim closes it by
 * giving CPython the SAME program the boards run: libmoy's binding of the spec
 * verb table, over the same vendored Lua, built the same way.
 *
 * Structurally it is modmoycore.c with the MicroPython removed -- the same
 * console, the same snapshot-in/queue-out host callbacks -- because the two
 * must not drift. What differs is only how the host talks to it: plain C
 * signatures ctypes can call, and buffers the caller owns.
 */

#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"

enum { SNAP_BTN = 0, SNAP_BTNP, SNAP_BTN_P1, SNAP_BTNP_P1, SNAP_PLAYERS,
       SNAP_TIME_MS, SNAP_TOUCH_X, SNAP_TOUCH_Y, SNAP_TOUCH_DOWN,
       SNAP_TOUCH_MS, SNAP_KEY, SNAP_KEY_DOWN, SNAP_TEXTMODE, SNAP_QUIT,
       SNAP_LEN };
enum { AQ_SFX = 0, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME };
#define AQ_SLOTS 4

typedef struct {
    lua_State  *L;
    moy_console con;
    moy_canvas  canvas;
    moy_sheet   sheet;
    moy_map     map;
    int32_t    *snap;
    int32_t     pmem[256];
    int         pmem_dirty;
    int32_t    *aq;          /* [n, (op,a,b,c)*] -- int32 here, unlike the
                                board's int16: a host has no reason to squeeze
                                it, and ctypes arrays are plainer this way */
    int         aq_cap;
    int         has_sheet, has_map;
} host_lua;

static host_lua *CUR;        /* the callbacks take void*; one run at a time */

static int h_btn(void *u, moy_button b, int p)
{ (void)u; return CUR && CUR->snap ? (CUR->snap[p > 0 ? SNAP_BTN_P1 : SNAP_BTN] >> (int)b) & 1 : 0; }
static int h_btnp(void *u, moy_button b, int p)
{ (void)u; return CUR && CUR->snap ? (CUR->snap[p > 0 ? SNAP_BTNP_P1 : SNAP_BTNP] >> (int)b) & 1 : 0; }
static int h_players(void *u)
{ (void)u; int n = (CUR && CUR->snap) ? CUR->snap[SNAP_PLAYERS] : 1; return n < 1 ? 1 : n; }
static uint32_t h_time(void *u)
{ (void)u; return (CUR && CUR->snap) ? (uint32_t)CUR->snap[SNAP_TIME_MS] : 0; }
static int32_t h_pget(void *u, int s)
{ (void)u; return (CUR && s >= 0 && s < 256) ? CUR->pmem[s] : 0; }
static void h_pset(void *u, int s, int32_t v)
{ (void)u; if (CUR && s >= 0 && s < 256 && CUR->pmem[s] != v) { CUR->pmem[s] = v; CUR->pmem_dirty = 1; } }

static void aq_push(int op, int a, int b, int c)
{
    if (!CUR || !CUR->aq || CUR->aq_cap < 1 + AQ_SLOTS) return;
    int n = CUR->aq[0];
    if (n < 0) n = 0;
    if (1 + (n + 1) * AQ_SLOTS > CUR->aq_cap) return;
    int32_t *p = CUR->aq + 1 + n * AQ_SLOTS;
    p[0] = op; p[1] = a; p[2] = b; p[3] = c;
    CUR->aq[0] = n + 1;
}

static void h_sfx(void *u, int n, int ch) { (void)u; aq_push(AQ_SFX, n, ch, 0); }
static void h_music(void *u, int t, int l) { (void)u; aq_push(AQ_MUSIC, t, l, 0); }
static void h_mstop(void *u) { (void)u; aq_push(AQ_MUSIC_STOP, 0, 0, 0); }
static void h_sstop(void *u, int ch) { (void)u; aq_push(AQ_SOUND_STOP, ch, 0, 0); }
static void h_vol(void *u, int l) { (void)u; aq_push(AQ_VOLUME, l, 0, 0); }
static void h_beep(void *u, float hz, float s)
{ (void)u; aq_push(AQ_BEEP, (int)hz, (int)(s * 1000.0f), 0); }

static int h_touch(void *u, int out[4])
{
    (void)u;
    if (!CUR || !CUR->snap || !CUR->snap[SNAP_TOUCH_DOWN]) return 0;
    out[0] = CUR->snap[SNAP_TOUCH_X]; out[1] = CUR->snap[SNAP_TOUCH_Y];
    out[2] = CUR->snap[SNAP_TOUCH_DOWN]; out[3] = CUR->snap[SNAP_TOUCH_MS];
    return 1;
}
static int h_key(void *u, int code)
{ (void)u; if (!CUR || !CUR->snap) return 0;
  return code < 0 ? CUR->snap[SNAP_KEY] : (CUR->snap[SNAP_KEY_DOWN] == code); }
static int h_keyp(void *u, int code)
{ (void)u; if (!CUR || !CUR->snap) return 0;
  return code < 0 ? CUR->snap[SNAP_KEY] : (CUR->snap[SNAP_KEY] == code); }
static void h_textmode(void *u, int on)
{ (void)u; if (CUR && CUR->snap) CUR->snap[SNAP_TEXTMODE] = on ? 1 : 0; }
static void h_quit(void *u)
{ (void)u; if (CUR && CUR->snap) CUR->snap[SNAP_QUIT] = 1; }
static const char *h_cfg(void *u, const char *k) { (void)u; (void)k; return NULL; }

/* -- extension verbs -------------------------------------------------------
 *
 * The superset (make_layer/draw_layer/image/view/background) is not libmoy's,
 * and is not reimplemented here either: it is registered on top of libmoy's
 * table as a trampoline back into Python. Same correction as the device glue
 * -- a cart needing a Python-backed verb needs one engine that can hold one,
 * not a second engine.
 *
 * The contract is deliberately narrow, because it is exactly what those verbs
 * take and return: up to four integer arguments plus at most one string
 * (image("bg") is the only string-taking verb), and an integer result or
 * nothing. Objects have never crossed this boundary -- layers and images
 * travel as int handles, which is what the prelude's wrappers speak. */
/* Eight, not four: the widest wrapper is the prelude's
 * __layer_spr(lid, tile, x, y, ck, scale, flip) at seven. Four silently
 * TRUNCATED it -- the extra arguments never reached Python, the closure raised
 * on its missing parameters, and hl_tramp reads a raising verb as nil. A layer
 * sprite would simply not draw, with nothing printed anywhere. */
#define HL_MAX_IARGS 8

typedef int (*hl_dispatch_fn)(int idx, int argc, const int *iargs,
                              const char *sarg, int *out);

static hl_dispatch_fn CUR_DISPATCH;

static int hl_tramp(lua_State *L)
{
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
    int n = lua_gettop(L);
    int iargs[HL_MAX_IARGS];
    const char *sarg = NULL;
    int ic = 0;
    for (int i = 1; i <= n; i++) {
        if (lua_type(L, i) == LUA_TSTRING) {
            if (sarg == NULL) sarg = lua_tostring(L, i);
        } else if (ic < HL_MAX_IARGS) {
            iargs[ic++] = (int)lua_tointeger(L, i);
        }
    }
    if (CUR_DISPATCH == NULL) return 0;
    int out = 0;
    int has = CUR_DISPATCH(idx, ic, iargs, sarg, &out);
    if (has) { lua_pushinteger(L, out); return 1; }
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

host_lua *hl_new(uint8_t *pix, int w, int h, int32_t *snap,
                 int32_t *aq, int aq_cap)
{
    host_lua *r = (host_lua *)calloc(1, sizeof(host_lua));
    if (!r) return NULL;
    moy_canvas_init(&r->canvas, pix, w, h);
    r->snap = snap; r->aq = aq; r->aq_cap = aq_cap;
    if (aq && aq_cap > 0) aq[0] = 0;
    r->con.canvas = &r->canvas;
    moy_host *hs = &r->con.host;
    hs->user = NULL;
    hs->btn = h_btn; hs->btnp = h_btnp; hs->players = h_players;
    hs->time_ms = h_time; hs->pmem_get = h_pget; hs->pmem_set = h_pset;
    hs->sfx = h_sfx; hs->music = h_music; hs->beep = h_beep;
    hs->music_stop = h_mstop; hs->sound_stop = h_sstop; hs->volume = h_vol;
    hs->touch = h_touch; hs->key = h_key; hs->keyp = h_keyp;
    hs->textmode = h_textmode; hs->quit = h_quit; hs->cfg = h_cfg;
    r->L = luaL_newstate();
    if (!r->L) { free(r); return NULL; }
    CUR = r;
    if (moy_lua_open(r->L, &r->con) != 0) { lua_close(r->L); free(r); CUR = NULL; return NULL; }
    return r;
}

void hl_set_sheet(host_lua *r, uint8_t *pix)
{
    if (pix) { moy_sheet_init(&r->sheet, pix); r->con.sheet = &r->sheet; }
    else r->con.sheet = NULL;
}

void hl_set_map(host_lua *r, uint8_t *cells, int w, int h)
{
    if (cells && w > 0 && h > 0) { moy_map_init(&r->map, cells, w, h); r->con.map = &r->map; }
    else r->con.map = NULL;
}

void hl_retarget(host_lua *r, uint8_t *pix) { r->canvas.pix = pix; }

/* Run one chunk. 0 on success; the message lands in err. */
int hl_exec(host_lua *r, const char *src, int len, const char *name,
            char *err, int errlen)
{
    CUR = r;
    if (luaL_loadbuffer(r->L, src, (size_t)len, name) != LUA_OK
        || lua_pcall(r->L, 0, 0, 0) != LUA_OK) {
        const char *m = lua_tostring(r->L, -1);
        if (err && errlen > 0) { strncpy(err, m ? m : "load failed", errlen - 1); err[errlen - 1] = 0; }
        return 1;
    }
    return 0;
}

/* Load a chunk and run _init. 0 on success; the message lands in err.
 *
 * The split exists for the GLUE PRELUDE (runtime/lua_ext.py), which has to run
 * after hl_register and before the cart: moybyte's object-valued verbs reach
 * Lua as int-handle functions plus wrappers, because this dispatch marshals
 * ints and one string and a Layer is neither. */
int hl_load(host_lua *r, const char *src, int len, const char *name,
            char *err, int errlen)
{
    if (hl_exec(r, src, len, name, err, errlen) != 0) return 1;
    return moy_lua_init(r->L, err, (size_t)errlen);
}

int hl_tick(host_lua *r, float dt, char *err, int errlen)
{
    CUR = r;
    moy_reset_state(&r->canvas);
    if (moy_lua_update(r->L, dt, err, (size_t)errlen) != 0) return 1;
    return moy_lua_draw(r->L, err, (size_t)errlen);
}

int hl_pmem_image(host_lua *r, int32_t *out, int n)
{
    if (n > 256) n = 256;
    memcpy(out, r->pmem, (size_t)n * sizeof(int32_t));
    int d = r->pmem_dirty;
    r->pmem_dirty = 0;
    return d;
}

void hl_pmem_load(host_lua *r, const int32_t *in, int n)
{
    if (n > 256) n = 256;
    memcpy(r->pmem, in, (size_t)n * sizeof(int32_t));
}

/* Read a cart global as a double; returns 0 when absent or not a number, 1
 * otherwise. Numbers only: the parity suites compare counters and positions,
 * and a richer marshalling here would be a second contract to keep. */
int hl_get_global_num(host_lua *r, const char *name, double *out)
{
    lua_getglobal(r->L, name);
    int ok = 0;
    if (lua_type(r->L, -1) == LUA_TNUMBER) { *out = (double)lua_tonumber(r->L, -1); ok = 1; }
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

/* What the cart last declared with view(), or 0 when it has not. libmoy
 * records it on the console (SPEC.md 6), so the host reads rather than being
 * called -- see the device glue for why that matters. */
int hl_get_view(host_lua *r, int *w, int *h)
{
    if (r->con.view_w <= 0) return 0;
    *w = r->con.view_w;
    *h = r->con.view_h;
    return 1;
}

void hl_free(host_lua *r)
{
    if (!r) return;
    if (r->L) lua_close(r->L);
    if (CUR == r) CUR = NULL;
    free(r);
}
