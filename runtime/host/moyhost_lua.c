/* clock_gettime/CLOCK_MONOTONIC are POSIX, and this builds at -std=c99,
 * which hides them. Must precede every include. */
#define _POSIX_C_SOURCE 200809L

/* The host's Lua run and the kernel's Player, by ctypes
 * (runtime/host/lua_binding.py; docs/history/kernel_cartpath_2026-10.md §1).
 *
 * CPython gets the program the boards run, not a twin of it: the cart's VM is
 * native/moycore/moycore_lua.c over moycore_run.c's console -- the allocator,
 * the layers and paint images in C, the scenes (moycore_scene.h), the native
 * superset -- and its frames are native/moy_play/moy_play.c's: the launch
 * from the cart's catalogue entry, the snapshot from the input table, the
 * audio into the run's session, the upcall books. What differs from a board is
 * only how the host talks to it: plain C signatures ctypes can call, buffers
 * the caller owns, and these seams:
 *
 *   - the audio sessions are runtime/host/audio_binding.py's library, a library of
 *     its own (compiled double-widened for the parity harness), so the
 *     Player's moy_aud_* calls forward to the function pointers hl_kernel
 *     hands over. Unset, a session is STALE (silence). Every other module the
 *     Player calls -- the loop's upcall books, the input tables, the glass --
 *     is in this same library (runtime/host/host_native.py), as on a board;
 *   - a paint image decodes through moy_img.c where the build has the
 *     desktop MicroPython's inflater (MOYHOST_IMG_C), else through the Python
 *     codec (runtime/carts/moyimg.py, the reference moy_img.c is pinned to), handed
 *     over as hl_img_decoder;
 *   - register()'s verbs call back into Python through one dispatch, and each
 *     call is counted APP, as modmoycore.c's trampoline counts it.
 *
 * THE RUNS ARE ROWS, as on a board (moycore_lua.h): each hl_new opens a row
 * of its own beside the runs already open, and every call makes its handle's
 * row current first, so two runs interleave call by call. A call through a
 * handle whose run has closed is refused. Where every row is held, hl_new
 * closes the oldest Lua run first: a harness that dropped a run without
 * closing it leaves its row, which a board refuses and the host takes back.
 */

#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moycore_run.h"
#include "moycore_lua.h"
#include "moycore_layers.h"
#include "moy_gfx_kernels.h"     /* the layer restore draw_layer takes */
#include "moy_play.h"
#include "moy_loop.h"
#include "moy_input.h"
#include "moy_aud.h"
#include "moy_buf.h"
#include "moy_img.h"

#ifndef MOY_PIXEL_RGB565
#error "the host console is the RGB565 build -- compile with -DMOY_PIXEL_RGB565=1"
#endif

/* -- the audio sessions, forwarded ------------------------------------------- */

enum { HK_SFX = 0, HK_BEEP, HK_MUSIC, HK_MUSIC_STOP, HK_STOP, HK_LEVEL, HK_N };
static void *K[HK_N];

/* Hand over one of the audio library's functions (NULL: unset). */
void hl_kernel(int which, void *fn)
{
    if (which >= 0 && which < HK_N) K[which] = fn;
}

#define AUD(i, T, ...) (K[i] ? ((T)K[i])(__VA_ARGS__) : MOY_AUD_STALE)
int moy_aud_sfx(uint32_t s, int n, int chan)
{ return AUD(HK_SFX, int (*)(uint32_t, int, int), s, n, chan); }
int moy_aud_beep(uint32_t s, float f, float d)
{ return AUD(HK_BEEP, int (*)(uint32_t, float, float), s, f, d); }
int moy_aud_music(uint32_t s, int track, int loop)
{ return AUD(HK_MUSIC, int (*)(uint32_t, int, int), s, track, loop); }
int moy_aud_music_stop(uint32_t s)
{ return AUD(HK_MUSIC_STOP, int (*)(uint32_t), s); }
int moy_aud_stop(uint32_t s, int chan)
{ return AUD(HK_STOP, int (*)(uint32_t, int), s, chan); }
int moy_aud_level(uint32_t s, int level)
{ return AUD(HK_LEVEL, int (*)(uint32_t, int), s, level); }

/* -- the paint images' codec, Python's where moy_img.c is not built ----------- */

/* decode(text, n, pix, cap, &w, &h): 0 with the size (and, when `pix` is not
 * NULL, the indices), -1 for no picture, -2 for a buffer too small. */
typedef int (*hl_img_fn)(const char *text, size_t n, uint8_t *pix, size_t cap,
                         uint32_t *w, uint32_t *h);

#if MOYHOST_IMG_C
void hl_img_decoder(hl_img_fn fn) { (void)fn; }     /* moy_img.c decodes */
#else
static hl_img_fn IMG;

void hl_img_decoder(hl_img_fn fn) { IMG = fn; }

size_t moy_img_work_size(void) { return 1; }

int moy_img_head(const char *text, size_t n, uint32_t *w, uint32_t *h)
{
    return IMG && IMG(text, n, NULL, 0, w, h) == 0 ? MOY_IMG_OK : MOY_IMG_NOT;
}

int moy_img_decode(const char *text, size_t n, uint8_t *pix, size_t cap,
                   uint32_t *w, uint32_t *h, void *work)
{
    (void)work;
    if (!IMG) return MOY_IMG_NOT;
    int rc = IMG(text, n, pix, cap, w, h);
    return rc == 0 ? MOY_IMG_OK : rc == -2 ? MOY_IMG_ROOM : MOY_IMG_NOT;
}
#endif

/* -- the binding's half of moycore (modmoycore.c's, on a board) --------------- */

static void hk_load(lua_State *L) { (void)L; }
static void hk_frame(uint32_t *s) { (void)s; }
static void hk_frame_end(int drew, uint32_t *s) { (void)drew; (void)s; }
const moycore_lua_hooks_t moycore_lua_hooks = {
    hk_load, hk_load, hk_frame, hk_frame, hk_frame_end,
};

/* A compiled cart's frame, where the library has WAMR (moyhost_wasm.c), and
 * its runaway watch's end: the instance terminated. */
int (*hl_wasm_tick)(float dt, int draw, char *err, size_t n);
void (*hl_wasm_stuck)(void);

void moycore_stuck(const char *what)
{
    if (moycore_RUN.L) {
        moycore_lua_stuck(what);
        return;
    }
    if (hl_wasm_stuck) hl_wasm_stuck();
}

int moycore_frame(float dt, int draw, char *err, size_t n)
{
    moy_reset_state(&moycore_RUN.c.canvas);
    moycore_run_tick_begin();
    if (!moycore_RUN.L)
        return hl_wasm_tick ? hl_wasm_tick(dt, draw, err, n) : 0;
    moycore_run_tick_dt(dt);
    return moycore_lua_tick(dt, draw, err, n);
}

/* -- the run ------------------------------------------------------------------- */

typedef int (*hl_dispatch_fn)(int idx, int argc, const int *kinds,
                              const int *iargs, const char **sargs,
                              int *out, const char **sout);

typedef struct {
    uint32_t h;                 /* its row's run (moycore_run_handle), 0 once closed */
    uint32_t gen;               /* the order the runs were made in */
    hl_dispatch_fn dispatch;    /* register()'s verbs call back through it */
    uint8_t *p8mem, *p8rom;     /* its p8 machine's buffers */
} host_lua;

static uint32_t G_GEN;
static host_lua *G_ROWS[MOYCORE_RUNS];      /* the run holding each row */

#define RUN moycore_RUN

/* `r`'s row made current: 1, or 0 when its run has closed. */
static int sel(host_lua *r)
{
    moycore_run *row = r != NULL && r->h ? moycore_run_of(r->h) : NULL;
    if (row == NULL || G_ROWS[row->slot] != r) return 0;
    moycore_run_use(row);
    return 1;
}
#define LIVE(r) sel(r)

static void close_run(host_lua *r)
{
    if (!sel(r)) return;
    moycore_lua_close();
    if (RUN.layers.blit_state)
        mg_lr_forget((mg_lrestore_t *)RUN.layers.blit_state, &mg_copy_none);
    moycore_run_close(&RUN.c);
    G_ROWS[RUN.slot] = NULL;
    moycore_run_end(&RUN);
    r->h = 0;
}


/* A compiled run's row released (moyhost_wasm.c, where WAMR builds): its
 * console goes, and its calls report it is not running. */
void hw_release_row(moycore_run *row) __attribute__((weak));

/* A row for a new run, Lua or compiled (moyhost_wasm.c): a free one, else
 * the oldest run's, closed. */
moycore_run *hl_row_for_new(void)
{
    moycore_run *row = moycore_run_new();
    while (row == NULL) {
        moycore_run *old = NULL;
        for (int i = 0; i < MOYCORE_RUNS; i++) {
            moycore_run *ri = moycore_run_at(i);
            if (ri && (ri->L || ri->wasm) && (!old || ri->gen < old->gen)) old = ri;
        }
        if (old == NULL) return NULL;
        if (G_ROWS[old->slot]) close_run(G_ROWS[old->slot]);
        else if (hw_release_row) hw_release_row(old);
        else return NULL;
        row = moycore_run_new();
    }
    return row;
}

/* The console over the caller's RGB565 framebuffer (`wire`: the 64-entry
 * index -> panel word table, NULL for libmoy's canonical palette), its
 * snapshot and audio queue. The VM opens at hl_open, once the sheet, map,
 * flags and config are set: the p8 machine seeds from them as it opens.
 * NULL for a buffer short of w x h pixels. */
host_lua *hl_new(void *pix, int nbytes, int w, int h, const uint16_t *wire,
                 int32_t *snap, int32_t *aq, int aq_cap)
{
    host_lua *r;
    if (w <= 0 || h <= 0 || (long)w * (long)h > (long)(nbytes / (int)sizeof(moy_pixel)))
        return NULL;
    r = (host_lua *)calloc(1, sizeof(host_lua));
    if (!r) return NULL;
    if (hl_row_for_new() == NULL) {
        free(r);
        return NULL;
    }
    moycore_lua_meters_reset();
    moy_canvas_init(&RUN.c.canvas, (moy_pixel *)pix, w, h);
    if (wire) moy_canvas_wire(&RUN.c.canvas, wire);
    moycore_run_open(&RUN.c, snap, aq, aq_cap);
    RUN.c.con.rng = (uint32_t)moycore_run_now_us() | 1u;
    RUN.open = 1;
    r->gen = ++G_GEN;
    r->h = moycore_run_handle(&RUN);
    G_ROWS[RUN.slot] = r;
    return r;
}

/* `app` is the app's run (native/moy_app/moy_app_run.h) the state opens its
 * role names over, or NULL. */
int hl_open(host_lua *r, const struct moy_app_run *app, char *err, int errlen)
{
    if (!LIVE(r)) return 1;
    if (!r->p8mem) r->p8mem = (uint8_t *)malloc(MOY_P8_MEM);
    if (!r->p8rom) r->p8rom = (uint8_t *)malloc(MOY_P8_ROM);
    moycore_lua_p8_memory(r->p8mem && r->p8rom ? r->p8mem : NULL, r->p8rom);
    return moycore_lua_open(app, err, errlen > 0 ? (size_t)errlen : 0) != 0;
}

/* The sheet, flags and map: moycore_run.h's, which says why each is checked
 * or copied. Before hl_open. */
void hl_set_sheet(host_lua *r, uint8_t *pix, int nbytes)
{ if (LIVE(r)) moycore_run_set_sheet(&RUN.c, pix, nbytes > 0 ? (size_t)nbytes : 0); }

void hl_set_flags(host_lua *r, const uint8_t *flags, int nbytes)
{ if (LIVE(r)) moycore_run_set_flags(&RUN.c, flags, nbytes > 0 ? (size_t)nbytes : 0); }

void hl_flag_xor(host_lua *r, int n, int mask)
{ if (LIVE(r)) moycore_run_flag_xor(&RUN.c, n, mask); }

void hl_set_map(host_lua *r, uint8_t *cells, int nbytes, int w, int h)
{ if (LIVE(r)) moycore_run_set_map(&RUN.c, cells, nbytes > 0 ? (size_t)nbytes : 0, w, h); }

void hl_set_cfg(host_lua *r, const char *blob, int len)
{ if (LIVE(r)) moycore_run_set_cfg(&RUN.c, blob, len > 0 ? (size_t)len : 0); }

/* Point the run at another buffer of the same size: a compositor that
 * ping-pongs. */
void hl_retarget(host_lua *r, void *pix)
{ if (LIVE(r)) RUN.c.canvas.pix = (moy_pixel *)pix; }

/* -- register()'s verbs: one dispatch back into Python ------------------------
 *
 * Up to eight arguments, each an integer, a string, a boolean or nil, WHERE
 * THE CART PUT IT, and an integer, a string, a boolean, up to eight integers
 * (a tuple, fanned out as that many results: `content_rect()`) or nothing
 * back: modmoycore.c's trampoline marshals the same kinds. Objects never
 * cross. */
#define HL_MAX_IARGS 8
#define HL_NUM  0
#define HL_STR  1
#define HL_BOOL 2
#define HL_NIL  3

/* The verb's run is its second upvalue: each run calls back through its own
 * dispatch. */
static int hl_tramp(lua_State *L)
{
    int idx = (int)lua_tointeger(L, lua_upvalueindex(1));
    hl_dispatch_fn dispatch = ((host_lua *)lua_touserdata(L, lua_upvalueindex(2)))->dispatch;
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
    if (dispatch == NULL) return 0;
    moy_loop_count(MOY_UPC_APP);
    int out[HL_MAX_IARGS + 1] = {0};
    const char *sout = NULL;
    int has = dispatch(idx, ic, kinds, iargs, sargs, out, &sout);
    if (has == 1) { lua_pushinteger(L, out[0]); return 1; }
    if (has == 2 && sout != NULL) {
        lua_pushlstring(L, sout, (size_t)out[0]);
        return 1;
    }
    if (has == 3) { lua_pushboolean(L, out[0]); return 1; }
    if (has == 5 && out[0] >= 0 && out[0] <= HL_MAX_IARGS) {   /* a tuple */
        for (int i = 1; i <= out[0]; i++) lua_pushinteger(L, out[i]);
        return out[0];
    }
    return 0;
}

void hl_set_dispatch(host_lua *r, hl_dispatch_fn fn) { if (r) r->dispatch = fn; }

/* `name` as a Lua global calling back with `idx`. After hl_open and before
 * hl_load: a cart captures its globals into locals as it executes. */
void hl_register(host_lua *r, const char *name, int idx)
{
    if (!LIVE(r) || !RUN.L) return;
    lua_pushinteger(RUN.L, idx);
    lua_pushlightuserdata(RUN.L, r);
    lua_pushcclosure(RUN.L, hl_tramp, 2);
    lua_setglobal(RUN.L, name);
}

/* A paint image's and a scene's text, for the run's C (moycore_lua.h). */
int hl_image_put(host_lua *r, const char *name, const char *text, int n)
{ return !LIVE(r) || n < 0 || moycore_lua_image_put(name, text, (size_t)n) != 0; }

int hl_scene_put(host_lua *r, const char *name, const char *text, int n)
{ return !LIVE(r) || n < 0 || moycore_lua_scene_put(name, text, (size_t)n) != 0; }

/* Park a layer's buffer for the prelude's next __layer_canvas (a runtime
 * whose layers are Python's). */
int hl_layer_bind(host_lua *r, void *pix, int nbytes, int w, int h)
{
    if (!LIVE(r) || nbytes < 0) return 1;
    return moycore_layers_park(&RUN.layers, pix, (size_t)nbytes, w, h);
}

/* Layer `i`'s pixels and size (moycore_lua_layer): 0, or 1 for none. */
int hl_layer_pixels(host_lua *r, int i, void **pix, int *w, int *h)
{
    moy_pixel *p = NULL;
    if (!LIVE(r) || moycore_lua_layer(i, &p, w, h) != 0) return 1;
    *pix = p;
    return 0;
}

/* draw_layer through the screen canvas's layer restore (its `_lrs`, the
 * kernels' mg_lr_* state), as the boards' does, over the host's engine, which
 * refuses: the copy is synchronous. NULL is libmoy's plain copy. */
static void hl_blit(void *st, moy_canvas *dst, const moy_canvas *src,
                    int cam_x, int cam_y, int edited)
{
    mg_lr_blit((mg_lrestore_t *)st, &mg_copy_none, (uint16_t *)dst->pix,
               (size_t)dst->w * (size_t)dst->h, dst->w, dst->h,
               (const uint16_t *)src->pix, (size_t)src->w * (size_t)src->h,
               src->w, src->h, cam_x, cam_y, edited != 0);
}

void hl_layer_restore(host_lua *r, void *state)
{
    if (!LIVE(r)) return;
    if (RUN.layers.blit_state)
        mg_lr_forget((mg_lrestore_t *)RUN.layers.blit_state, &mg_copy_none);
    RUN.layers.blit = state ? hl_blit : NULL;
    RUN.layers.blit_state = state;
}

/* One chunk as TEXT (moycore_lua_exec). 0, or 1 with the message in err. */
int hl_exec(host_lua *r, const char *src, int len, const char *name,
            char *err, int errlen)
{
    if (!LIVE(r) || !RUN.L) {
        if (err && errlen > 0) { strncpy(err, "no run", errlen - 1); err[errlen - 1] = 0; }
        return 1;
    }
    return moycore_lua_exec(src, (size_t)len, name, err, errlen > 0 ? (size_t)errlen : 0) != 0;
}

/* The cart's chunks in order, then _layout with `layout`'s (w, h, fs) where it
 * is not NULL, then _init (modmoycore.c's load). */
int hl_load(host_lua *r, const char **srcs, const int *lens, const char **names,
            int n, const int32_t *layout, char *err, int errlen)
{
    if (!LIVE(r) || !RUN.L) return hl_exec(r, "", 0, "", err, errlen);
    moycore_lua_load_begin();
    for (int i = 0; i < n; i++)
        if (hl_exec(r, srcs[i], lens[i], names[i], err, errlen)) return 1;
    return moycore_lua_load_finish(layout, err, errlen > 0 ? (size_t)errlen : 0) != 0;
}

/* The cart's _layout(w, h, fs) (modmoycore.c's layout): 1 called, 0 none,
 * -1 with the message in err. */
int hl_layout(host_lua *r, int32_t w, int32_t h, int32_t fs, char *err, int errlen)
{
    if (!LIVE(r) || !RUN.L) return 0;
    return moycore_lua_layout(w, h, fs, err, errlen > 0 ? (size_t)errlen : 0);
}

/* One frame (moycore_frame); `draw` 0 is a logic-only tick. */
int hl_tick(host_lua *r, float dt, int draw, char *err, int errlen)
{
    if (!LIVE(r)) return hl_exec(r, "", 0, "", err, errlen);
    return moycore_frame(dt, draw, err, errlen > 0 ? (size_t)errlen : 0) != 0;
}

int hl_pmem_image(host_lua *r, int32_t *out, int n)
{ return LIVE(r) ? moycore_run_pmem_image(&RUN.c, out, n) : 0; }

void hl_pmem_load(host_lua *r, const int32_t *in, int n)
{ if (LIVE(r)) moycore_run_pmem_load(&RUN.c, in, n); }

/* A cart global as a double: 1, or 0 when absent or not a number. An integer
 * goes through lua_tointeger, which a double holds exactly. */
int hl_get_global_num(host_lua *r, const char *name, double *out)
{
    if (!LIVE(r) || !RUN.L) return 0;
    lua_getglobal(RUN.L, name);
    int ok = 0;
    if (lua_type(RUN.L, -1) == LUA_TNUMBER) {
        if (lua_isinteger(RUN.L, -1)) *out = (double)lua_tointeger(RUN.L, -1);
        else *out = (double)lua_tonumber(RUN.L, -1);
        ok = 1;
    }
    lua_pop(RUN.L, 1);
    return ok;
}

/* A string global's bytes into `out` (at most cap): its length, or -1 when it
 * is no string. */
int hl_get_global_str(host_lua *r, const char *name, char *out, int cap)
{
    if (!LIVE(r) || !RUN.L) return -1;
    lua_getglobal(RUN.L, name);
    int n = -1;
    if (lua_type(RUN.L, -1) == LUA_TSTRING) {
        size_t len = 0;
        const char *s = lua_tolstring(RUN.L, -1, &len);
        n = (int)len;
        if (out && cap > 0) memcpy(out, s, len < (size_t)cap ? len : (size_t)cap);
    }
    lua_pop(RUN.L, 1);
    return n;
}

/* The length of a table global (Lua's #t), or -1 when it is not a table. */
int hl_get_global_len(host_lua *r, const char *name)
{
    if (!LIVE(r) || !RUN.L) return -1;
    lua_getglobal(RUN.L, name);
    int n = -1;
    if (lua_type(RUN.L, -1) == LUA_TTABLE) n = (int)lua_rawlen(RUN.L, -1);
    lua_pop(RUN.L, 1);
    return n;
}

/* The Lua heap in bytes after a full collect: what a cart KEEPS. */
int hl_heap_bytes(host_lua *r)
{
    if (!LIVE(r) || !RUN.L) return 0;
    lua_gc(RUN.L, LUA_GCCOLLECT, 0);
    return lua_gc(RUN.L, LUA_GCCOUNT, 0) * 1024 + lua_gc(RUN.L, LUA_GCCOUNTB, 0);
}

/* The same without collecting: what the heap reaches mid-play. */
int hl_heap_peak_bytes(host_lua *r)
{
    if (!LIVE(r) || !RUN.L) return 0;
    return lua_gc(RUN.L, LUA_GCCOUNT, 0) * 1024 + lua_gc(RUN.L, LUA_GCCOUNTB, 0);
}

/* What the cart last declared with view(), or 0 when it has not. */
int hl_get_view(host_lua *r, int *w, int *h)
{ return LIVE(r) ? moycore_run_view(&RUN.c, w, h) : 0; }

/* The frame's two halves in microseconds. */
void hl_split(uint32_t *update_us, uint32_t *draw_us)
{ moycore_run_split(update_us, draw_us); }

void hl_free(host_lua *r)
{
    if (r == NULL) return;
    close_run(r);
    free(r->p8mem);
    free(r->p8rom);
    free(r);
}

/* The run's Lua heap in bytes, Lua's own count with no collect: exact, what
 * the task manager's row shows. */
int hl_heap_live(host_lua *r)
{
    moycore_run *row = r != NULL && r->h ? moycore_run_of(r->h) : NULL;
    return row != NULL ? (int)moycore_run_heap(row) : 0;
}

/* How many runs are open, Lua and compiled. */
int hl_runs(void) { return moycore_run_count(); }

/* The run's handle (moycore_run_handle), 0 once it has closed. */
uint32_t hl_handle(host_lua *r)
{
    moycore_run *row = r != NULL && r->h ? moycore_run_of(r->h) : NULL;
    return row != NULL && G_ROWS[row->slot] == r ? r->h : 0u;
}

/* -- the kernel's Player (moy_play.h) ------------------------------------------- */

/* The map's rows the host has: Lua, a compiled cart's where WAMR built, and
 * Python's. */
void hl_play_rows(int wasm) { moy_play_rows(true, wasm != 0, true); }

/* `flags`: bit 0 paced, then moy_play.h's MOY_PLAY_HOME and _UNSIGNED. */
int hl_play_launch(const char *cart, int flags, uint32_t *run)
{ return moy_play_launch(cart, NULL, (uint32_t)flags, run); }

int hl_play_bind(uint32_t run, moy_input_t *in, uint32_t audio, moy_tick_t *tick)
{ return moy_play_bind(run, in, audio, tick); }

int hl_play_open(uint32_t run) { return moy_play_open(run); }

/* The run's ticks; the pointer and the window flag the console published
 * first. `out` gets MOY_PLAY_QUIT and MOY_PLAY_VIEW. */
int hl_play_frame(uint32_t run, int ticks, float dt, int render, int x, int y, int touch,
                  int window, int rx, int ry, int rw, int rh, uint32_t *out)
{
    moy_play_in_t in = { x, y, touch, window != 0, { rx, ry, rw, rh } };
    int rc = moy_play_input(run, &in);
    if (rc != MOY_PLAY_OK) {
        *out = 0;
        return rc;
    }
    return moy_play_frame(run, (uint8_t)ticks, dt, render != 0, out);
}

int hl_play_end(uint32_t run, int why) { return moy_play_end(run, why); }

int hl_play_info(uint32_t run, moy_play_info_t *out) { return moy_play_info(run, out); }

uint32_t hl_play_last(void) { return moy_play_last(); }

size_t hl_play_info_size(void) { return sizeof(moy_play_info_t); }
