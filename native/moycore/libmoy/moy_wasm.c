/* The wasm binding: SPEC.md 16's import table.
 *
 * The Lua binding's twin (src/moy_lua.c), and deliberately the same shape:
 * every verb is a thin call into the raster or the host seam, and the only
 * code here that is not glue is the marshalling SPEC.md 16.4 pins -- a
 * sentinel for each overload, an out pointer for several results, a handle
 * for a layer, bytes and a length for a string. The rules are SPEC.md 16's
 * and the table is wasm-imports.json; test/wasm_table_check.py holds
 * NATIVES below equal to it.
 *
 * Two engines drive the same verbs (include/moy_wasm.h): WAMR under MOY_WASM,
 * a JavaScript embedder's own engine under MOY_WASM_JS. What differs is four
 * small functions -- which binding a call belongs to, how a trap is raised,
 * and how the cart's memory is read and written -- and they are the first
 * thing below, and how par runs the cart's items: on the host's lanes over
 * WAMR, one after another through the embedder under JavaScript. Everything
 * else is one body of code.
 *
 * Every pointer a cart hands over is an offset into its linear memory and is
 * bounds-checked before it is touched: by the engine's adapter for a '*~'
 * pair in a signature (WAMR, or cart.js), here for the rest. A range outside
 * the memory, a handle make_layer never returned and a blit outside _draw are
 * a trap: the call that made it unwinds the cart and the host ends it.
 */

#if defined(MOY_WASM) || defined(MOY_WASM_JS)

#if defined(MOY_WASM) && defined(MOY_WASM_JS)
#error "moy_wasm.c runs over one engine: define MOY_WASM or MOY_WASM_JS, not both"
#endif

#ifndef MOY_PIXEL_RGB565
#error "moy_wasm.c needs the direct-colour build (MOY_PIXEL_RGB565): a palette blit's 256 colours do not fit an indexed canvas"
#endif

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "moy.h"
#include "moy_wasm.h"

/* NativeSymbol takes the function as a void *. ISO C does not define that
 * conversion and every compiler WAMR builds with does; __extension__ tells
 * -Wpedantic as much. */
#if defined(__GNUC__)
#define FN(f) (__extension__ (void *)(f))
#else
#define FN(f) ((void *)(f))
#endif

static const char *const HOOKS[3] = { "_init", "_update", "_draw" };

/* -- the engine: which binding, a trap, the cart's memory ------------------ */

#ifdef MOY_WASM

typedef wasm_exec_env_t env_t;

/* What an import called from one of par's items traps with. */
static const char ITEM_IMPORT[] = "moy: an import called from a par item";

/* The binding behind an import call, or NULL -- and a trap -- when the
 * instance has none yet (a start function reaching the console) or the call
 * came from one of par's items, on whichever core it ran. */
static moy_wasm *bound(env_t env)
{
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    moy_wasm *w = (moy_wasm *)wasm_runtime_get_custom_data(inst);
    if (!w)
        wasm_runtime_set_exception(inst, "moy: an import ran before the cart was bound");
    else if (w->items) {
        wasm_runtime_set_exception(inst, ITEM_IMPORT);
        return NULL;
    }
    return w;
}

static void trap(moy_wasm *w, const char *msg)
{
    wasm_runtime_set_exception(w->inst, msg);
}

/* `n` bytes at linear-memory offset `off`, or NULL with the instance trapped. */
static uint8_t *span(moy_wasm *w, uint32_t off, uint64_t n)
{
    if (!wasm_runtime_validate_app_addr(w->inst, (uint64_t)off, n)) return NULL;
    return (uint8_t *)wasm_runtime_addr_app_to_native(w->inst, (uint64_t)off);
}

/* `n` bytes into linear memory at `off`: 0, with the instance trapped, when
 * the range leaves it. */
static int store(moy_wasm *w, uint32_t off, const uint8_t *src, uint32_t n)
{
    uint8_t *p = span(w, off, n);
    if (!p) return 0;
    memcpy(p, src, n);
    return 1;
}

#else /* MOY_WASM_JS: the adapter calls each verb with the binding itself */

typedef moy_wasm *env_t;

/* A range that leaves linear memory, in the words WAMR uses for it. */
static const char OUT_OF_BOUNDS[] = "out of bounds memory access";

static const char ITEM_IMPORT[] = "moy: an import called from a par item";

static void trap(moy_wasm *w, const char *msg)
{
    if (!w->trap) w->trap = msg;
}

static moy_wasm *bound(env_t w)
{
    if (w->items) {
        trap(w, ITEM_IMPORT);
        return NULL;
    }
    return w;
}

/* A copy of `n` bytes at linear-memory offset `off`, alive until the import
 * returns, or NULL with the cart trapped. */
static uint8_t *span(moy_wasm *w, uint32_t off, uint64_t n)
{
    uint8_t *p = n > 0xFFFFFFFFu ? NULL : moy_wasm_js_span(w, off, (uint32_t)n);
    if (!p) trap(w, OUT_OF_BOUNDS);
    return p;
}

static int store(moy_wasm *w, uint32_t off, const uint8_t *src, uint32_t n)
{
    if (moy_wasm_js_store(w, off, src, n)) return 1;
    trap(w, OUT_OF_BOUNDS);
    return 0;
}

#endif

/* -- marshalling ------------------------------------------------------------ */

/* Several results: consecutive little-endian i32 at `out`, nothing when out
 * is 0. Returns 0 when `out` is not a valid range (the cart is trapped). */
static int results(moy_wasm *w, uint32_t out, const int32_t *v, int n)
{
    uint8_t b[16];
    int i;
    if (!out) return 1;
    for (i = 0; i < n && i < 4; i++) {
        uint32_t u = (uint32_t)v[i];
        b[i * 4 + 0] = (uint8_t)(u & 0xFFu);
        b[i * 4 + 1] = (uint8_t)((u >> 8) & 0xFFu);
        b[i * 4 + 2] = (uint8_t)((u >> 16) & 0xFFu);
        b[i * 4 + 3] = (uint8_t)(u >> 24);
    }
    return store(w, out, b, (uint32_t)i * 4u);
}

static moy_canvas *layer_of(moy_wasm *w, int32_t h)
{
    if (h < 1 || h > w->n_layers) return NULL;
    return &w->layers[h - 1];
}

/* -- the screen's frame ------------------------------------------------------ */

#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) \
    && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define MOY_WASM_LE 1
#else
#define MOY_WASM_LE 0
#endif

/* blit565's words into the screen, byte-swapped when the screen is. On a
 * little-endian host the frame's bytes ARE a canonical screen's, so that is a
 * copy, and a swapped screen takes two pixels a word when both ends are
 * aligned. Byte by byte otherwise: a cart may hand over a frame at any
 * address. */
static void write_565(moy_wasm *w, moy_pixel *d, const uint8_t *px, size_t n)
{
    size_t i = 0;
#if MOY_WASM_LE
    if (!w->wire_swapped) {
        memcpy(d, px, n * 2u);
        return;
    }
    if ((((uintptr_t)d | (uintptr_t)px) & 3u) == 0) {
        const uint32_t *s32 = (const uint32_t *)(const void *)px;
        uint32_t *d32 = (uint32_t *)(void *)d;
        for (; i + 1 < n; i += 2) {
            uint32_t v = *s32++;
            *d32++ = ((v & 0x00FF00FFu) << 8) | ((v >> 8) & 0x00FF00FFu);
        }
    }
#endif
    for (; i < n; i++) {
        uint16_t c = (uint16_t)(px[i * 2] | (px[i * 2 + 1] << 8));
        d[i] = w->wire_swapped ? (uint16_t)((c >> 8) | ((c & 0xFFu) << 8)) : c;
    }
}

/* Write a frame the way blit or blit565 does: `px` is the cart's frame or the
 * host's copy of it, in the layout frame_565 names. */
static void write_frame(moy_wasm *w, const uint8_t *px)
{
    moy_canvas *s = w->screen;
    size_t i, n = (size_t)s->w * (size_t)s->h;
    if (w->frame_565) {
        write_565(w, s->pix, px, n);
    } else {
        for (i = 0; i < n; i++) s->pix[i] = w->frame_lut[px[i]];
    }
}

/* The screen is about to be drawn on or read: give it the frame it lacks. */
static void settle(moy_wasm *w)
{
    const uint8_t *px = w->owed ? w->owed : w->kept;
    w->owed = w->kept = NULL;
    if (px) write_frame(w, px);
}

/* Every verb that draws on or reads its target takes it from here. */
static moy_canvas *tgt(moy_wasm *w)
{
    if (w->target == w->screen && (w->owed || w->kept)) settle(w);
    return w->target;
}

/* -- drawing (SPEC.md 6, 6.1): into the target ---------------------------- */

/* cls replaces the whole target, so a frame the screen lacks is dropped
 * rather than written first. */
static void w_cls(env_t e, int32_t c)
{
    moy_wasm *w = bound(e);
    if (!w) return;
    if (w->target == w->screen) w->owed = w->kept = NULL;
    moy_cls(w->target, c);
}

static int32_t w_pix(env_t e, int32_t x, int32_t y, int32_t c)
{
    moy_wasm *w = bound(e);
    if (!w) return 0;
    if (c >= 0) {
        moy_pix(tgt(w), x, y, c);
        return 0;
    }
    return moy_pget(tgt(w), x, y);
}

static void w_line(env_t e, int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_line(tgt(w), x0, y0, x1, y1, c); }

static void w_rect(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_rect(tgt(w), x, y, ww, hh, c); }

static void w_rectb(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_rectb(tgt(w), x, y, ww, hh, c); }

static void w_circ(env_t e, int32_t cx, int32_t cy, int32_t r, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_circ(tgt(w), cx, cy, r, c); }

static void w_circb(env_t e, int32_t cx, int32_t cy, int32_t r, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_circb(tgt(w), cx, cy, r, c); }

static void w_oval(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_oval(tgt(w), x, y, ww, hh, c); }

static void w_ovalb(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_ovalb(tgt(w), x, y, ww, hh, c); }

static void w_tri(env_t e, int32_t x1, int32_t y1, int32_t x2, int32_t y2,
                  int32_t x3, int32_t y3, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_tri(tgt(w), x1, y1, x2, y2, x3, y3, c); }

static void w_trib(env_t e, int32_t x1, int32_t y1, int32_t x2, int32_t y2,
                   int32_t x3, int32_t y3, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_trib(tgt(w), x1, y1, x2, y2, x3, y3, c); }

/* '*~': the engine's adapter has checked the bytes are inside linear
 * memory. */
static void w_print(env_t e, const uint8_t *s, uint32_t len, int32_t x, int32_t y, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_print(tgt(w), s, (size_t)len, x, y, c); }

static void w_camera(env_t e, int32_t x, int32_t y, uint32_t out)
{
    moy_wasm *w = bound(e);
    int32_t prev[2];
    if (!w) return;
    prev[0] = w->target->cam_x;
    prev[1] = w->target->cam_y;
    if (!results(w, out, prev, 2)) return;
    moy_camera(w->target, x, y);
}

static void w_clip(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh)
{ moy_wasm *w = bound(e); if (w) moy_clip(w->target, x, y, ww, hh); }

static void w_pal(env_t e, int32_t c0, int32_t c1, int32_t p)
{
    moy_wasm *w = bound(e);
    if (!w) return;
    if (c0 < 0) moy_pal_reset(w->target);
    else if (p == 1) moy_pal_screen(w->target, c0, c1);
    else moy_pal(w->target, c0, c1);
}

static void w_palt(env_t e, int32_t c, int32_t on)
{
    moy_wasm *w = bound(e);
    if (!w) return;
    if (c < 0) moy_palt_reset(w->target);
    else moy_palt(w->target, c, on != 0);
}

static void w_fillp(env_t e, int32_t p, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_fillp(w->target, p, c); }

static void w_tline(env_t e, int32_t x0, int32_t y0, int32_t x1, int32_t y1,
                    int32_t u, int32_t v, int32_t du, int32_t dv, int32_t ck)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet || !w->con->map) return;
    moy_tline(tgt(w), w->con->sheet, w->con->map, x0, y0, x1, y1, u, v, du, dv, ck);
}

/* -- the host-dependent core verbs (SPEC.md 6) ---------------------------- */

static void w_background(env_t e, int32_t c)
{
    moy_wasm *w = bound(e);
    if (!w) return;
    w->con->bg = c;
    w->con->has_bg = 1;
    if (w->con->host.background) w->con->host.background(w->con->host.user, c);
}

static void w_view(env_t e, int32_t vw, int32_t vh)
{
    moy_wasm *w = bound(e);
    moy_console *con;
    if (!w) return;
    con = w->con;
    con->view_w = vw < 0 ? 0 : vw;
    con->view_h = vh < 0 ? 0 : vh;
    if (con->host.view) con->host.view(con->host.user, con->view_w, con->view_h);
}

static int32_t w_make_layer(env_t e, int32_t lw, int32_t lh)
{
    moy_wasm *w = bound(e);
    moy_pixel *pix;
    moy_canvas *ly;
    if (!w || lw <= 0 || lh <= 0 || !w->con->host.layer_new
        || w->n_layers >= MOY_WASM_LAYERS)
        return 0;
    pix = w->con->host.layer_new(w->con->host.user, lw, lh);
    if (!pix) return 0;
    ly = &w->layers[w->n_layers];
    moy_canvas_init(ly, pix, lw, lh);
    moy_canvas_wire(ly, w->screen->wire);
    return ++w->n_layers;
}

static void w_draw_layer(env_t e, int32_t h, int32_t cx, int32_t cy)
{
    moy_wasm *w = bound(e);
    moy_canvas *ly;
    if (!w) return;
    ly = layer_of(w, h);
    if (!ly) {
        trap(w, "moy: draw_layer: not a layer handle");
        return;
    }
    if (w->owed || w->kept) settle(w);
    moy_blit_window(w->screen, ly, cx, cy);
}

/* The receiver of a Lua layer method: the drawing verbs draw into `h` from
 * here on, 0 being the screen. */
static void w_target(env_t e, int32_t h)
{
    moy_wasm *w = bound(e);
    moy_canvas *ly;
    if (!w) return;
    if (h == 0) {
        w->target = w->screen;
        return;
    }
    ly = layer_of(w, h);
    if (!ly) {
        trap(w, "moy: target: not a layer handle");
        return;
    }
    w->target = ly;
}

/* -- sprites and map (SPEC.md 7.1, 7.2) ----------------------------------- */

/* An absent sheet or map degrades exactly as it does in the Lua binding:
 * every sprite empty, every cell -1. */

static void w_spr(env_t e, int32_t n, int32_t x, int32_t y, int32_t ck,
                  int32_t scale, int32_t flip)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet) return;
    moy_spr(tgt(w), w->con->sheet, n, x, y, ck, scale, flip);
}

static void w_sspr(env_t e, int32_t sx, int32_t sy, int32_t sw, int32_t sh,
                   int32_t dx, int32_t dy, int32_t dw, int32_t dh,
                   int32_t ck, int32_t flip)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet) return;
    moy_sspr(tgt(w), w->con->sheet, sx, sy, sw, sh, dx, dy, dw, dh, ck, flip);
}

static void w_map(env_t e, int32_t mx, int32_t my, int32_t mw, int32_t mh,
                  int32_t sx, int32_t sy, int32_t ck, int32_t scale, int32_t layers)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet || !w->con->map) return;
    moy_map_draw_layers(tgt(w), w->con->map, w->con->sheet, mx, my, mw, mh,
                        sx, sy, ck, scale, layers, w->con->flags);
}

static int32_t w_sget(env_t e, int32_t x, int32_t y)
{
    moy_wasm *w = bound(e);
    return (w && w->con->sheet) ? moy_sheet_pget(w->con->sheet, x, y) : 0;
}

static void w_sset(env_t e, int32_t x, int32_t y, int32_t c)
{
    moy_wasm *w = bound(e);
    if (w && w->con->sheet) moy_sheet_pset(w->con->sheet, x, y, c);
}

static int32_t w_fget(env_t e, int32_t n, int32_t b)
{
    moy_wasm *w = bound(e);
    int v;
    if (!w) return 0;
    v = (w->con->flags && n >= 0 && n < MOY_FLAGS) ? w->con->flags[n] : 0;
    return b < 0 ? v : (v >> (b & 7)) & 1;
}

static void w_fset(env_t e, int32_t n, int32_t b, int32_t v)
{
    moy_wasm *w = bound(e);
    uint8_t *f;
    if (!w || !w->con->flags || n < 0 || n >= MOY_FLAGS) return;
    f = &w->con->flags[n];
    if (b < 0) *f = (uint8_t)(v & 0xFF);
    else if (v) *f = (uint8_t)(*f | (1u << (b & 7)));
    else *f = (uint8_t)(*f & ~(1u << (b & 7)));
}

static int32_t w_mget(env_t e, int32_t x, int32_t y)
{
    moy_wasm *w = bound(e);
    return (w && w->con->map) ? moy_mget(w->con->map, x, y) : -1;
}

static void w_mset(env_t e, int32_t x, int32_t y, int32_t tile)
{
    moy_wasm *w = bound(e);
    if (w && w->con->map) moy_mset(w->con->map, x, y, tile);
}

/* -- input (SPEC.md 7.3) --------------------------------------------------- */

/* Buttons by index, in SPEC.md 7.3's order, which is moy_button's. */
static int32_t w_btn(env_t e, int32_t b, int32_t player)
{
    moy_wasm *w = bound(e);
    if (!w || b < 0 || b >= MOY_BTN_COUNT || !w->con->host.btn) return 0;
    return w->con->host.btn(w->con->host.user, (moy_button)b, player) ? 1 : 0;
}

static int32_t w_btnp(env_t e, int32_t b, int32_t player)
{
    moy_wasm *w = bound(e);
    if (!w || b < 0 || b >= MOY_BTN_COUNT || !w->con->host.btnp) return 0;
    return w->con->host.btnp(w->con->host.user, (moy_button)b, player) ? 1 : 0;
}

static int32_t w_players(env_t e)
{
    moy_wasm *w = bound(e);
    int n;
    if (!w) return 1;
    n = w->con->host.players ? w->con->host.players(w->con->host.user) : 1;
    return n < 1 ? 1 : n;
}

static int32_t w_touch(env_t e, uint32_t out)
{
    moy_wasm *w = bound(e);
    int v[4];
    int32_t r[4];
    if (!w || !w->con->host.touch || !w->con->host.touch(w->con->host.user, v))
        return 0;
    r[0] = v[0];
    r[1] = v[1];
    r[2] = v[2] ? 1 : 0;
    r[3] = v[3] ? 1 : 0;
    return results(w, out, r, 4) ? 1 : 0;
}

static int32_t key_of(moy_wasm *w, int (*fn)(void *, int), int32_t code)
{
    if (!w) return 0;
    if (code < 0) return fn ? fn(w->con->host.user, -1) : 0;
    return (fn && fn(w->con->host.user, code)) ? 1 : 0;
}

static int32_t w_key(env_t e, int32_t code)
{ moy_wasm *w = bound(e); return key_of(w, w ? w->con->host.key : NULL, code); }

static int32_t w_keyp(env_t e, int32_t code)
{ moy_wasm *w = bound(e); return key_of(w, w ? w->con->host.keyp : NULL, code); }

static void w_textmode(env_t e, int32_t on)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.textmode) w->con->host.textmode(w->con->host.user, on != 0);
}

/* -- audio (SPEC.md 8.2): a host with none takes every call and is silent -- */

static void w_sfx(env_t e, int32_t n, int32_t chan)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.sfx) w->con->host.sfx(w->con->host.user, n, chan);
}

static void w_music(env_t e, int32_t track, int32_t loop)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.music) w->con->host.music(w->con->host.user, track, loop != 0);
}

static void w_music_stop(env_t e)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.music_stop) w->con->host.music_stop(w->con->host.user);
}

static void w_sound_stop(env_t e, int32_t chan)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.sound_stop)
        w->con->host.sound_stop(w->con->host.user, chan < 0 ? -1 : chan);
}

static void w_volume(env_t e, int32_t level)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.volume) w->con->host.volume(w->con->host.user, level);
}

static void w_beep(env_t e, float freq, float dur)
{
    moy_wasm *w = bound(e);
    if (w && w->con->host.beep) w->con->host.beep(w->con->host.user, freq, dur);
}

/* -- state and utility (SPEC.md 9) ----------------------------------------- */

static int32_t w_time(env_t e)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->host.time_ms) return 0;
    return (int32_t)w->con->host.time_ms(w->con->host.user);
}

static int32_t w_pmem(env_t e, int32_t slot, int32_t v, int32_t write)
{
    moy_wasm *w = bound(e);
    moy_host *h;
    if (!w || slot < 0 || slot >= 256) return 0;
    h = &w->con->host;
    if (write) {
        if (h->pmem_set) h->pmem_set(h->user, slot, v);
        return 0;
    }
    return h->pmem_get ? h->pmem_get(h->user, slot) : 0;
}

/* A cart's string as a C string: NULL when it is too long for `buf` or holds
 * a NUL, either of which reads as absent. */
static const char *c_string(const uint8_t *s, uint32_t len, char *buf)
{
    if (len > MOY_WASM_NAME_MAX || memchr(s, 0, len)) return NULL;
    memcpy(buf, s, len);
    buf[len] = 0;
    return buf;
}

static int32_t w_cfg(env_t e, const uint8_t *key, uint32_t klen, uint8_t *dst, uint32_t dlen)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_NAME_MAX + 1];
    const char *k, *v;
    size_t n;
    if (!w || !w->con->host.cfg) return -1;
    k = c_string(key, klen, buf);
    v = k ? w->con->host.cfg(w->con->host.user, k) : NULL;
    if (!v) return -1;
    n = strlen(v);
    memcpy(dst, v, n < dlen ? n : dlen);
    return (int32_t)n;
}

static float w_rnd(env_t e, float n)
{ moy_wasm *w = bound(e); return w ? moy_rnd(w->con, n) : 0.0f; }

static void w_srand(env_t e, int32_t seed)
{ moy_wasm *w = bound(e); if (w) moy_srand(w->con, (uint32_t)seed); }

/* floor, with no libm: the library's raster needs none and this does not
 * start. NaN floors to 0 and the range saturates, where C's conversion would
 * be undefined. */
static int32_t w_flr(env_t e, float x)
{
    int32_t t;
    (void)e;
    if (x != x) return 0;
    if (x >= 2147483648.0f) return INT32_MAX;
    if (x < -2147483648.0f) return INT32_MIN;
    t = (int32_t)x;
    if ((float)t > x) t--;
    return t;
}

static void w_quit(env_t e)
{
    moy_wasm *w = bound(e);
    if (!w) return;
    if (w->con->host.quit) w->con->host.quit(w->con->host.user);
    w->quitting = 1;
    trap(w, "moy.quit");
}

/* -- the framebuffer: blit, blit565 ---------------------------------------- */

static uint16_t wire_of(const moy_wasm *w, uint32_t r, uint32_t g, uint32_t b)
{
    uint16_t c = (uint16_t)(((r & 0xF8u) << 8) | ((g & 0xFCu) << 3) | (b >> 3));
    return w->wire_swapped ? (uint16_t)((c >> 8) | ((c & 0xFFu) << 8)) : c;
}

static int may_blit(moy_wasm *w)
{
    if (!w->in_draw) {
        trap(w, "moy: blit outside _draw");
        return 0;
    }
    if (w->blits) {
        trap(w, "moy: a second blit in one _draw");
        return 0;
    }
    w->blits = 1;
    return 1;
}

/* The frame goes to the host when it takes it, and into the screen when it
 * does not. Either way it replaces what the screen was owed before. */
static void hand_over(moy_wasm *w, const uint8_t *src)
{
    w->owed = w->kept = NULL;
#ifndef MOY_WASM_JS
    if (w->frame && w->frame(w->frame_user, src, w->frame_565 ? NULL : w->frame_lut)) {
        w->owed = src;
        return;
    }
#endif
    write_frame(w, src);
}

static void w_blit(env_t e, uint32_t frame, uint32_t pal)
{
    moy_wasm *w = bound(e);
    moy_canvas *s;
    const uint8_t *src, *p = NULL;
    size_t i, n;
    if (!w || !may_blit(w)) return;
    s = w->screen;
    n = (size_t)s->w * (size_t)s->h;
    src = span(w, frame, (uint64_t)n);
    if (!src) return;
    if (pal && !(p = span(w, pal, 768u))) return;
    for (i = 0; i < 256; i++)
        w->frame_lut[i] = p ? wire_of(w, p[i * 3], p[i * 3 + 1], p[i * 3 + 2])
                            : s->wire[i & 63];
    w->frame_565 = 0;
    hand_over(w, src);
}

static void w_blit565(env_t e, uint32_t frame)
{
    moy_wasm *w = bound(e);
    moy_canvas *s;
    const uint8_t *src;
    size_t n;
    if (!w || !may_blit(w)) return;
    s = w->screen;
    n = (size_t)s->w * (size_t)s->h;
    src = span(w, frame, (uint64_t)n * 2u);
    if (!src) return;
    w->frame_565 = 1;
    hand_over(w, src);
}

/* -- the cart's own files: read -------------------------------------------- */

/* Relative, '/'-separated, no empty, "." or ".." segment, no '\\'. */
static int inside_cart(const char *name)
{
    const char *seg = name, *p;
    if (!*name) return 0;
    for (;;) {
        size_t len;
        p = strchr(seg, '/');
        len = p ? (size_t)(p - seg) : strlen(seg);
        if (len == 0) return 0;
        if (len == 1 && seg[0] == '.') return 0;
        if (len == 2 && seg[0] == '.' && seg[1] == '.') return 0;
        if (memchr(seg, '\\', len)) return 0;
        if (!p) return 1;
        seg = p + 1;
    }
}

/* -- the cart's writable files: write, erase, list (SPEC.md 16.12) ---------- */

/* Is `path` one the manifest's "writable" declares? An entry ending in '/'
 * is a folder, and declares every path below it; any other entry declares
 * itself. An entry that breaks the path rule declares nothing. */
static int writable(const moy_wasm *w, const char *path)
{
    const char *e = w->writable;
    if (!e) return 0;
    for (; *e; e += strlen(e) + 1) {
        size_t n = strlen(e);
        char folder[MOY_WASM_PATH_MAX + 1];
        if (n > MOY_WASM_PATH_MAX) continue;
        if (e[n - 1] != '/') {
            if (inside_cart(e) && !strcmp(e, path)) return 1;
            continue;
        }
        memcpy(folder, e, n - 1);
        folder[n - 1] = 0;
        if (inside_cart(folder) && !strncmp(e, path, n) && path[n]) return 1;
    }
    return 0;
}

/* A path write or erase may name, as a C string in `buf`: NULL unless it is
 * well-formed, no longer than MOY_WASM_PATH_MAX, and writable. */
static const char *written_path(const moy_wasm *w, const uint8_t *s, uint32_t len,
                                char buf[MOY_WASM_PATH_MAX + 1])
{
    if (len > MOY_WASM_PATH_MAX || memchr(s, 0, len)) return NULL;
    memcpy(buf, s, len);
    buf[len] = 0;
    return inside_cart(buf) && writable(w, buf) ? buf : NULL;
}

/* write(path, data): the whole written copy, replaced. The host is never
 * handed a path the manifest does not declare, nor more than
 * MOY_WASM_WRITE_MAX bytes. */
static int32_t w_write(env_t e, const uint8_t *path, uint32_t plen,
                       const uint8_t *data, uint32_t len)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_PATH_MAX + 1];
    const char *p;
    int32_t r;
    if (!w) return MOY_WASM_FAILED;
    p = written_path(w, path, plen, buf);
    if (!p) return MOY_WASM_NOT_WRITABLE;
    if (len > MOY_WASM_WRITE_MAX) return MOY_WASM_NO_ROOM;
    if (!w->write) return MOY_WASM_FAILED;
    r = w->write(w->files_user, p, data, len);
    return (r == 0 || r == MOY_WASM_NO_ROOM) ? r : MOY_WASM_FAILED;
}

static int32_t w_erase(env_t e, const uint8_t *path, uint32_t plen)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_PATH_MAX + 1];
    const char *p;
    if (!w) return -1;
    p = written_path(w, path, plen, buf);
    if (!p || !w->erase) return -1;
    return w->erase(w->files_user, p) == 0 ? 0 : -1;
}

/* list(prefix, index, dst): a byte prefix, not a folder -- "saves/slot"
 * lists every slot -- and "" the whole of the cart's files. */
static int32_t w_list(env_t e, const uint8_t *prefix, uint32_t plen, int32_t index,
                      uint8_t *dst, uint32_t len)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_NAME_MAX + 1];
    const char *p;
    int32_t r;
    if (!w || !w->list || index < 0) return -1;
    p = c_string(prefix, plen, buf);
    if (!p) return -1;
    r = w->list(w->files_user, p, (uint32_t)index, dst, len);
    return r < 0 ? -1 : r;
}

/* read(name, ...): a writable path's written copy when it has one, and
 * otherwise the file in the cart's folder. */
static int32_t w_read(env_t e, const uint8_t *name, uint32_t nlen, uint32_t offset,
                      uint8_t *dst, uint32_t len)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_NAME_MAX + 1];
    const char *nm;
    uint32_t got;
    if (!w) return 0;
    nm = c_string(name, nlen, buf);
    if (!nm || !inside_cart(nm)) return 0;
    if (w->written && nlen <= MOY_WASM_PATH_MAX && writable(w, nm)) {
        int32_t r = w->written(w->files_user, nm, offset, dst, len);
        if (r >= 0) return (len && (uint32_t)r > len) ? (int32_t)len : r;
    }
    if (!w->read) return 0;
    got = w->read(w->read_user, nm, offset, dst, len);
    if (len && got > len) got = len;
    return (int32_t)got;
}

/* -- the sample stream: snd ---------------------------------------------- */

/* A host with no audio: the stream is a count that drains at the rate by the
 * console's clock, and every frame is dropped. The cart meets the backpressure
 * it would meet where the frames are played. The clock's leftover fraction of
 * a frame carries over, so a second of clock drains exactly the rate. */
static uint32_t silent_room(moy_wasm *w)
{
    moy_host *h = &w->con->host;
    uint32_t now = h->time_ms ? h->time_ms(h->user) : 0;
    if (w->snd_clocked) {
        uint64_t t = (uint64_t)(uint32_t)(now - w->snd_ms) * MOY_WASM_SND_RATE + w->snd_rem;
        uint64_t drained = t / 1000u;
        w->snd_rem = (uint32_t)(t % 1000u);
        w->snd_level = drained >= w->snd_level ? 0 : w->snd_level - (uint32_t)drained;
    }
    w->snd_ms = now;
    w->snd_clocked = 1;
    return MOY_WASM_SND_DEPTH - w->snd_level;
}

/* snd(pcm, n): n frames of little-endian signed 16-bit mono at `pcm`, or with
 * n 0 the question of how many the host would take. The frames are read only
 * when there are some, so a query may pass any pointer. */
static int32_t w_snd(env_t e, uint32_t pcm, uint32_t n)
{
    moy_wasm *w = bound(e);
    const uint8_t *p = NULL;
    uint32_t got, room;
    if (!w) return 0;
    if (n && !(p = span(w, pcm, (uint64_t)n * 2u))) return 0;
    if (w->snd) {
        got = w->snd(w->snd_user, p, n);
        return (int32_t)(n && got > n ? n : got);
    }
    room = silent_room(w);
    if (!n) return (int32_t)room;
    got = n < room ? n : room;
    w->snd_level += got;
    return (int32_t)got;
}

/* -- par: the cart's own work, across the cores ------------------------------ */

/* par(n, arg, stacks, size): _par(i, arg) for every i in [0, n), each with
 * the cart's C stack at the top of its own `size` bytes, stacks + (i + 1) *
 * size, returning when all have returned. Where the host has lanes, the
 * calling core and the lanes take the items in turn, the next untaken one
 * each, so a core that is busier takes fewer; otherwise they run here, in
 * order. An item calls no import -- `items` makes every one trap -- so it
 * touches nothing but the cart's memory, and the frame is the same whichever
 * core ran what.
 *
 * A trap in an item traps par with the message of the lowest item that
 * trapped, which is the one a run in order stops at: items are taken in
 * order and a core skips only items above one a core has already trapped
 * on, so every item below the lowest trap has run. */
static int par_ok(moy_wasm *w, int32_t n, uint32_t stacks, uint32_t size)
{
#ifndef MOY_WASM_JS
    if (!w->item) {
        trap(w, "moy: par without the _par and __stack_pointer exports");
        return 0;
    }
#endif
    if (n < 0) {
        trap(w, "moy: par's count is negative");
        return 0;
    }
    if (!size || (size & 15u) || (stacks & 15u)) {
        trap(w, "moy: par's stacks are not 16-byte aligned");
        return 0;
    }
    return span(w, stacks, (uint64_t)(uint32_t)n * size) != NULL;
}

#ifdef MOY_WASM

/* "Exception: " is WAMR's prefix on the message it hands back, and its own
 * again when the message is set. */
static const char *bare(const char *msg)
{
    static const char pre[] = "Exception: ";
    if (!msg) return "the cart trapped";
    return strncmp(msg, pre, sizeof pre - 1) ? msg : msg + sizeof pre - 1;
}

/* Item i on lane l. 0 when it trapped, which the lane records and clears. */
static int item_on(moy_wasm *w, moy_wasm_lane *l, int32_t i)
{
    uint32_t argv[2], saved, top;
    int ok;
    top = w->job_stacks + (uint32_t)(i + 1) * w->job_size;
    memcpy(&saved, l->sp, 4);
    memcpy(l->sp, &top, 4);
    argv[0] = (uint32_t)i;
    argv[1] = (uint32_t)w->job_arg;
    ok = wasm_runtime_call_wasm(l->env, l->item, 2, argv);
    memcpy(l->sp, &saved, 4);
    if (ok) return 1;
    snprintf(l->trap, sizeof l->trap, "%s", bare(wasm_runtime_get_exception(l->inst)));
    wasm_runtime_clear_exception(l->inst);
    l->trapped = i;
    return 0;
}

/* Has any core trapped on an item below i? */
static int trapped_below(moy_wasm *w, int32_t i)
{
    int k;
    for (k = 0; k <= w->job_lanes; k++)
        if (w->lane[k].trapped >= 0 && w->lane[k].trapped < i) return 1;
    return 0;
}

/* The next untaken item, or job_n when none is left. */
static int32_t take(moy_wasm *w)
{
#if defined(__GNUC__)
    if (w->job_lanes)
        return __atomic_fetch_add(&w->job_next, 1, __ATOMIC_RELAXED);
#endif
    return w->job_next++;
}

/* Lane l's items: the next untaken one until none is left or it traps. */
static void items_of(moy_wasm *w, moy_wasm_lane *l)
{
    int32_t i;
    while ((i = take(w)) < w->job_n)
        if (!trapped_below(w, i) && !item_on(w, l, i)) break;
}

static int lane_sp(moy_wasm_lane *l)
{
    wasm_global_inst_t g;
    if (!wasm_runtime_get_export_global_inst(l->inst, MOY_WASM_SP, &g)
        || g.kind != WASM_I32 || !g.is_mutable)
        return 0;
    l->sp = (uint8_t *)g.global_data;
    return 1;
}

/* On lane l's thread: its own instance the first time, then items. A lane
 * that cannot make one is dead and takes none, now or later. */
static void lane_work(void *job)
{
    moy_wasm_lane *l = (moy_wasm_lane *)job;
    moy_wasm *w = l->w;
    if (!l->inst) {
        char err[128];
        uint32_t stack = w->lane_stack ? w->lane_stack : 64u * 1024u;
        l->inst = wasm_runtime_instantiate_sibling(w->inst, stack, err, sizeof err);
        if (l->inst) {
            wasm_runtime_set_custom_data(l->inst, w);
            l->env = wasm_runtime_create_exec_env(l->inst, stack);
            l->item = wasm_runtime_lookup_function(l->inst, MOY_WASM_ITEM);
        }
        if (!l->inst || !l->env || !l->item || !lane_sp(l)) {
            l->dead = 1;
            return;
        }
    }
    items_of(w, l);
}

static void lanes_free(moy_wasm *w)
{
    int k;
    for (k = 1; k <= MOY_WASM_LANES; k++) {
        moy_wasm_lane *l = &w->lane[k];
        if (l->env) wasm_runtime_destroy_exec_env(l->env);
        if (l->inst) wasm_runtime_deinstantiate_sibling(l->inst);
        memset(l, 0, sizeof *l);
        l->w = w;
        l->k = k;
    }
}

static void w_par(env_t e, int32_t n, int32_t arg, uint32_t stacks, uint32_t size)
{
    moy_wasm *w = bound(e);
    int k, lanes = 0, go[MOY_WASM_LANES + 1];
    int32_t low = -1;
    const char *msg = NULL;
    if (!w || !par_ok(w, n, stacks, size) || n == 0) return;
#if defined(__GNUC__)
    if (w->lane_go && w->lane_wait) lanes = w->lanes;
#endif
    if (lanes > MOY_WASM_LANES) lanes = MOY_WASM_LANES;
    if (lanes > n - 1) lanes = n - 1;
    w->job_n = n;
    w->job_arg = arg;
    w->job_stacks = stacks;
    w->job_size = size;
    w->job_lanes = lanes;
    w->job_next = 0;
    for (k = 0; k <= lanes; k++) w->lane[k].trapped = -1;
    w->items = 1;
    for (k = 1; k <= lanes; k++)
        go[k] = !w->lane[k].dead
                && w->lane_go(w->lane_user, k, lane_work, &w->lane[k]) == 0;
    items_of(w, &w->lane[0]);
    for (k = 1; k <= lanes; k++)
        if (go[k]) w->lane_wait(w->lane_user, k);
    w->items = 0;
    for (k = 0; k <= lanes; k++) {
        if (w->lane[k].trapped >= 0 && (low < 0 || w->lane[k].trapped < low)) {
            low = w->lane[k].trapped;
            msg = w->lane[k].trap;
        }
    }
    if (msg) trap(w, msg);
}

#else /* MOY_WASM_JS: the embedder runs each item, here, in order */

static void w_par(env_t e, int32_t n, int32_t arg, uint32_t stacks, uint32_t size)
{
    moy_wasm *w = bound(e);
    int32_t i;
    if (!w || !par_ok(w, n, stacks, size)) return;
    w->items = 1;
    for (i = 0; i < n; i++)
        if (moy_wasm_js_item(w, i, arg, stacks + (uint32_t)(i + 1) * size)) break;
    w->items = 0;
}

void moy_wasm_item_trap(moy_wasm *w, const char *msg)
{
    if (w->trap) return;
    snprintf(w->item_trap, sizeof w->item_trap, "%s", msg ? msg : "the cart trapped");
    w->trap = w->item_trap;
}

#endif

/* -- the table ------------------------------------------------------------- */

/* One row per import, in the order of wasm-imports.json. The
 * signature strings are WAMR's: 'i' an i32, 'f' an f32, '*~' a pointer and
 * the length WAMR bounds-checks it by, before the parenthesis the result. */
static const NativeSymbol NATIVES[] = {
    {"cls", FN(w_cls), "(i)", NULL},
    {"background", FN(w_background), "(i)", NULL},
    {"view", FN(w_view), "(ii)", NULL},
    {"pix", FN(w_pix), "(iii)i", NULL},
    {"line", FN(w_line), "(iiiii)", NULL},
    {"rect", FN(w_rect), "(iiiii)", NULL},
    {"rectb", FN(w_rectb), "(iiiii)", NULL},
    {"circ", FN(w_circ), "(iiii)", NULL},
    {"circb", FN(w_circb), "(iiii)", NULL},
    {"oval", FN(w_oval), "(iiiii)", NULL},
    {"ovalb", FN(w_ovalb), "(iiiii)", NULL},
    {"print", FN(w_print), "(*~iii)", NULL},
    {"camera", FN(w_camera), "(iii)", NULL},
    {"clip", FN(w_clip), "(iiii)", NULL},
    {"pal", FN(w_pal), "(iii)", NULL},
    {"palt", FN(w_palt), "(ii)", NULL},
    {"fillp", FN(w_fillp), "(ii)", NULL},
    {"make_layer", FN(w_make_layer), "(ii)i", NULL},
    {"draw_layer", FN(w_draw_layer), "(iii)", NULL},
    {"tri", FN(w_tri), "(iiiiiii)", NULL},
    {"trib", FN(w_trib), "(iiiiiii)", NULL},
    {"tline", FN(w_tline), "(iiiiiiiii)", NULL},
    {"spr", FN(w_spr), "(iiiiii)", NULL},
    {"sspr", FN(w_sspr), "(iiiiiiiiii)", NULL},
    {"sget", FN(w_sget), "(ii)i", NULL},
    {"sset", FN(w_sset), "(iii)", NULL},
    {"fget", FN(w_fget), "(ii)i", NULL},
    {"fset", FN(w_fset), "(iii)", NULL},
    {"map", FN(w_map), "(iiiiiiiii)", NULL},
    {"mget", FN(w_mget), "(ii)i", NULL},
    {"mset", FN(w_mset), "(iii)", NULL},
    {"btn", FN(w_btn), "(ii)i", NULL},
    {"btnp", FN(w_btnp), "(ii)i", NULL},
    {"players", FN(w_players), "()i", NULL},
    {"touch", FN(w_touch), "(i)i", NULL},
    {"key", FN(w_key), "(i)i", NULL},
    {"keyp", FN(w_keyp), "(i)i", NULL},
    {"textmode", FN(w_textmode), "(i)", NULL},
    {"sfx", FN(w_sfx), "(ii)", NULL},
    {"music", FN(w_music), "(ii)", NULL},
    {"music_stop", FN(w_music_stop), "()", NULL},
    {"sound_stop", FN(w_sound_stop), "(i)", NULL},
    {"volume", FN(w_volume), "(i)", NULL},
    {"beep", FN(w_beep), "(ff)", NULL},
    {"time", FN(w_time), "()i", NULL},
    {"pmem", FN(w_pmem), "(iii)i", NULL},
    {"cfg", FN(w_cfg), "(*~*~)i", NULL},
    {"rnd", FN(w_rnd), "(f)f", NULL},
    {"srand", FN(w_srand), "(i)", NULL},
    {"flr", FN(w_flr), "(f)i", NULL},
    {"quit", FN(w_quit), "()", NULL},
    {"target", FN(w_target), "(i)", NULL},
    {"blit", FN(w_blit), "(ii)", NULL},
    {"blit565", FN(w_blit565), "(i)", NULL},
    {"read", FN(w_read), "(*~i*~)i", NULL},
    {"snd", FN(w_snd), "(ii)i", NULL},
    {"par", FN(w_par), "(iiii)", NULL},
    {"write", FN(w_write), "(*~*~)i", NULL},
    {"erase", FN(w_erase), "(*~)i", NULL},
    {"list", FN(w_list), "(*~i*~)i", NULL},
};

const NativeSymbol *moy_wasm_natives(uint32_t *count)
{
    if (count) *count = (uint32_t)(sizeof NATIVES / sizeof NATIVES[0]);
    return NATIVES;
}

#ifdef MOY_WASM
int moy_wasm_register(NativeSymbol *storage)
{
    uint32_t n;
    const NativeSymbol *t = moy_wasm_natives(&n);
    if (!storage) return -1;
    memcpy(storage, t, n * sizeof *storage);
    return wasm_runtime_register_natives(MOY_WASM_MODULE, storage, n) ? 0 : -1;
}
#endif

/* -- the module's shape ---------------------------------------------------- */

#if defined(__GNUC__)
__attribute__((format(printf, 3, 4)))
#endif
static int fail(char *err, size_t errlen, const char *fmt, ...)
{
    va_list ap;
    if (err && errlen) {
        va_start(ap, fmt);
        vsnprintf(err, errlen, fmt, ap);
        va_end(ap);
    }
    return -1;
}

static int leb(const uint8_t **p, const uint8_t *end, uint32_t *v)
{
    uint32_t r = 0;
    unsigned shift = 0;
    while (*p < end && shift < 35) {
        uint8_t b = *(*p)++;
        r |= (uint32_t)(b & 0x7Fu) << shift;
        if (!(b & 0x80u)) {
            *v = r;
            return 1;
        }
        shift += 7;
    }
    return 0;
}

/* The module's own memory, from its bytes: WAMR reshapes a memory the module
 * never grows into one page of the whole size, so its page counts cannot
 * answer this before instantiation. Sections run in id order and memory (5)
 * comes before code and data, so a prefix of the file is enough. */
static int memory_of(const uint8_t *wasm, size_t size, uint32_t *flags,
                     uint32_t *lo, uint32_t *hi)
{
    const uint8_t *p = wasm + 8, *end = wasm + size;
    if (size < 8 || memcmp(wasm, "\0asm\1\0\0\0", 8) != 0) return -1;
    while (p < end) {
        uint8_t id = *p++;
        uint32_t len, n;
        const uint8_t *next;
        if (!leb(&p, end, &len)) return -1;
        next = p + len;
        if (id == 5) {
            if (!leb(&p, end, &n)) return -1;
            if (n != 1) return n == 0 ? -2 : -3;
            if (!leb(&p, end, flags) || !leb(&p, end, lo)) return -1;
            *hi = *lo;
            if ((*flags & 1u) && !leb(&p, end, hi)) return -1;
            return (*flags & 1u) ? 0 : -4;
        }
        if (id > 5) return -2;
        if (len > (size_t)(end - p)) return -1;
        p = next;
    }
    return -1;
}

/* The memory rules of the module shape: one memory of the module's own, not
 * shared or 64-bit, whose minimum and maximum are both `pages`. */
static int check_memory(const uint8_t *wasm, size_t size, uint32_t pages,
                        char *err, size_t errlen)
{
    uint32_t flags = 0, lo = 0, hi = 0;
    int m = memory_of(wasm, size, &flags, &lo, &hi);
    if (m == -1)
        return fail(err, errlen, "the module's bytes do not reach a readable memory section");
    if (m == -2)
        return fail(err, errlen, "the module defines no memory of its own");
    if (m == -3)
        return fail(err, errlen, "the module defines more than one memory");
    if (m == -4)
        return fail(err, errlen, "the memory has no maximum");
    if (flags & ~1u)
        return fail(err, errlen, "the memory is shared or 64-bit");
    if (lo != pages || hi != pages)
        return fail(err, errlen, "the memory is %u..%u pages; the manifest declares %u",
                    (unsigned)lo, (unsigned)hi, (unsigned)pages);
    return 0;
}

/* -- the shape from the module's bytes -------------------------------------- */

typedef struct {
    const uint8_t *p, *end;
} reader;

static int rd_byte(reader *r, uint8_t *v)
{
    if (r->p >= r->end) return 0;
    *v = *r->p++;
    return 1;
}

static int rd_leb(reader *r, uint32_t *v) { return leb(&r->p, r->end, v); }

/* A name, left where it is: its bytes and their count. */
static int rd_name(reader *r, const uint8_t **s, uint32_t *n)
{
    if (!rd_leb(r, n) || *n > (size_t)(r->end - r->p)) return 0;
    *s = r->p;
    r->p += *n;
    return 1;
}

/* Skip a vector of single-byte value types; its start and count out. */
static int rd_types(reader *r, const uint8_t **v, uint32_t *n)
{
    if (!rd_leb(r, n) || *n > (size_t)(r->end - r->p)) return 0;
    *v = r->p;
    r->p += *n;
    return 1;
}

/* Function type `index` of the type section in `types`: its param and result
 * value types, each one byte. 0 when there is no such type or it is not a
 * plain function type. */
static int type_at(reader types, uint32_t index, const uint8_t **params,
                   uint32_t *np, const uint8_t **res, uint32_t *nr)
{
    uint32_t n, i, k;
    uint8_t form;
    if (!rd_leb(&types, &n) || index >= n) return 0;
    for (i = 0; i <= index; i++) {
        if (!rd_byte(&types, &form) || form != 0x60) return 0;
        if (!rd_types(&types, params, np) || !rd_types(&types, res, nr)) return 0;
        for (k = 0; k < *np; k++)
            if ((*params)[k] < 0x7C || (*params)[k] > 0x7F) return 0;
        for (k = 0; k < *nr; k++)
            if ((*res)[k] < 0x7C || (*res)[k] > 0x7F) return 0;
    }
    return 1;
}

/* A WAMR signature string as the value types it stands for. */
static int sig_types(const char *sig, uint8_t *params, uint32_t *np,
                     uint8_t *res, uint32_t *nr)
{
    uint8_t *out = params;
    uint32_t *n = np;
    *np = *nr = 0;
    if (*sig++ != '(') return 0;
    for (; *sig; sig++) {
        uint8_t t;
        if (*sig == ')') {
            out = res;
            n = nr;
            continue;
        }
        switch (*sig) {
        case 'i': case '*': case '~': case '$': t = 0x7F; break;
        case 'I': t = 0x7E; break;
        case 'f': t = 0x7D; break;
        case 'F': t = 0x7C; break;
        default: return 0;
        }
        if (*n >= 16) return 0;
        out[(*n)++] = t;
    }
    return 1;
}

/* Is the module's type `index` exactly the table row `row`'s? */
static int row_type_is(reader types, uint32_t index, const NativeSymbol *row)
{
    uint8_t want_p[16], want_r[16];
    uint32_t wnp, wnr, np, nr;
    const uint8_t *params, *res;
    if (!sig_types(row->signature, want_p, &wnp, want_r, &wnr)) return 0;
    if (!type_at(types, index, &params, &np, &res, &nr)) return 0;
    return np == wnp && nr == wnr && !memcmp(params, want_p, np)
        && !memcmp(res, want_r, nr);
}

static const NativeSymbol *row_named(const uint8_t *name, uint32_t n)
{
    uint32_t count, i;
    const NativeSymbol *t = moy_wasm_natives(&count);
    for (i = 0; i < count; i++)
        if (strlen(t[i].symbol) == n && !memcmp(t[i].symbol, name, n)) return &t[i];
    return NULL;
}

/* The type index of function `index` in the module's index space, imported
 * functions first. */
static int func_type_index(reader imports, reader funcs, uint32_t index,
                           uint32_t *type)
{
    uint32_t n, i, seen = 0, k;
    if (rd_leb(&imports, &n)) {
        for (i = 0; i < n; i++) {
            const uint8_t *s;
            uint32_t len, t;
            uint8_t kind;
            if (!rd_name(&imports, &s, &len) || !rd_name(&imports, &s, &len)
                || !rd_byte(&imports, &kind) || kind != 0 || !rd_leb(&imports, &t))
                return 0;
            if (seen++ == index) {
                *type = t;
                return 1;
            }
        }
    }
    if (!rd_leb(&funcs, &n) || index - seen >= n) return 0;
    for (k = 0; k <= index - seen; k++)
        if (!rd_leb(&funcs, type)) return 0;
    return 1;
}

/* What par asks of the module's exports, given what the check found: `par`
 * whether it imports par, `item` and `sp` 1 for _par at (i32, i32) -> () and
 * a mutable i32 __stack_pointer, -1 for either at another type, 0 absent. */
static int par_exports(int par, int item, int sp, char *err, size_t errlen)
{
    if (item < 0)
        return fail(err, errlen, "%s is not at SPEC.md 16's type", MOY_WASM_ITEM);
    if (sp < 0)
        return fail(err, errlen, "%s is not a mutable i32 global", MOY_WASM_SP);
    if (par && !item)
        return fail(err, errlen, "imports par but has no %s export", MOY_WASM_ITEM);
    if (par && !sp)
        return fail(err, errlen, "imports par but does not export its %s", MOY_WASM_SP);
    return 0;
}

/* Global `index` of the module's own globals in `globals`: its type byte and
 * mutability. Imported globals are refused before this is asked. */
static int global_at(reader globals, uint32_t index, uint8_t *type, uint8_t *mut)
{
    uint32_t n, i;
    uint8_t op;
    if (!rd_leb(&globals, &n) || index >= n) return 0;
    for (i = 0; i <= index; i++) {
        if (!rd_byte(&globals, type) || !rd_byte(&globals, mut)) return 0;
        /* The initializer: skip to its end opcode. A constant expression's
         * immediates are LEBs and fixed-width floats, none of which holds a
         * bare 0x0B before its own end; read op by op to be sure. */
        for (;;) {
            if (!rd_byte(&globals, &op)) return 0;
            if (op == 0x0B) break;
            if (op == 0x41 || op == 0x42 || op == 0x23 || op == 0xD2) {
                uint32_t v;
                if (op == 0x42) {           /* i64.const: up to ten bytes */
                    uint8_t b;
                    do { if (!rd_byte(&globals, &b)) return 0; } while (b & 0x80u);
                } else if (!rd_leb(&globals, &v)) {
                    return 0;
                }
            } else if (op == 0x43) {
                if ((size_t)(globals.end - globals.p) < 4) return 0;
                globals.p += 4;
            } else if (op == 0x44) {
                if ((size_t)(globals.end - globals.p) < 8) return 0;
                globals.p += 8;
            } else if (op == 0xD0) {
                if (!rd_byte(&globals, &op)) return 0;
            } else {
                return 0;
            }
        }
    }
    return 1;
}

int moy_wasm_check_bytes(const uint8_t *wasm, size_t size, uint32_t pages,
                         char *err, size_t errlen)
{
    reader r, sec[12];
    int have[12] = {0}, seen[3] = {0, 0, 0}, memory = 0, h, par = 0, item = 0, sp = 0;
    uint32_t n = 0, i;

    if (!wasm || size < 8 || memcmp(wasm, "\0asm\1\0\0\0", 8) != 0)
        return fail(err, errlen, "not a WebAssembly module");
    r.p = wasm + 8;
    r.end = wasm + size;
    while (r.p < r.end) {
        uint8_t id;
        uint32_t len;
        if (!rd_byte(&r, &id) || !rd_leb(&r, &len) || len > (size_t)(r.end - r.p))
            return fail(err, errlen, "the module's sections do not parse");
        if (id < 12) {
            sec[id].p = r.p;
            sec[id].end = r.p + len;
            have[id] = 1;
        }
        r.p += len;
    }
    for (i = 0; i < 12; i++)
        if (!have[i]) sec[i].p = sec[i].end = wasm + size;

    if (check_memory(wasm, size, pages, err, errlen)) return -1;

    r = sec[2];
    if (have[2] && !rd_leb(&r, &n))
        return fail(err, errlen, "the import section does not parse");
    for (i = 0; have[2] && i < n; i++) {
        const uint8_t *mod, *name;
        uint32_t ml, nl, type;
        uint8_t kind;
        const NativeSymbol *row;
        if (!rd_name(&r, &mod, &ml) || !rd_name(&r, &name, &nl) || !rd_byte(&r, &kind))
            return fail(err, errlen, "the import section does not parse");
        if (ml != strlen(MOY_WASM_MODULE) || memcmp(mod, MOY_WASM_MODULE, ml))
            return fail(err, errlen, "imports %.*s.%.*s: a compiled cart imports only from module \"moy\"",
                        (int)ml, (const char *)mod, (int)nl, (const char *)name);
        if (kind != 0)
            return fail(err, errlen, "imports %.*s.%.*s as something other than a function",
                        (int)ml, (const char *)mod, (int)nl, (const char *)name);
        if (!rd_leb(&r, &type))
            return fail(err, errlen, "the import section does not parse");
        row = row_named(name, nl);
        if (!row || !row_type_is(sec[1], type, row))
            return fail(err, errlen, "imports %.*s.%.*s, which is not in the import table at that type",
                        (int)ml, (const char *)mod, (int)nl, (const char *)name);
        if (nl == 3 && !memcmp(name, "par", 3)) par = 1;
    }

    r = sec[7];
    if (have[7] && !rd_leb(&r, &n))
        return fail(err, errlen, "the export section does not parse");
    for (i = 0; have[7] && i < n; i++) {
        const uint8_t *name, *params, *res;
        uint32_t nl, index, type, np, nr;
        uint8_t kind;
        if (!rd_name(&r, &name, &nl) || !rd_byte(&r, &kind) || !rd_leb(&r, &index))
            return fail(err, errlen, "the export section does not parse");
        if (kind == 2 && nl == 6 && !memcmp(name, "memory", 6)) {
            memory = 1;
            continue;
        }
        if (kind == 3 && nl == strlen(MOY_WASM_SP) && !memcmp(name, MOY_WASM_SP, nl)) {
            uint8_t gt, gm;
            sp = global_at(sec[6], index, &gt, &gm) && gt == 0x7F && gm == 1 ? 1 : -1;
            continue;
        }
        if (kind != 0) continue;
        if (nl == strlen(MOY_WASM_ITEM) && !memcmp(name, MOY_WASM_ITEM, nl)) {
            item = func_type_index(sec[2], sec[3], index, &type)
                   && type_at(sec[1], type, &params, &np, &res, &nr)
                   && nr == 0 && np == 2 && params[0] == 0x7F && params[1] == 0x7F ? 1 : -1;
            continue;
        }
        for (h = 0; h < 3; h++) {
            if (strlen(HOOKS[h]) != nl || memcmp(HOOKS[h], name, nl)) continue;
            if (!func_type_index(sec[2], sec[3], index, &type)
                || !type_at(sec[1], type, &params, &np, &res, &nr)
                || nr != 0 || np != (h == 1 ? 1u : 0u)
                || (np == 1 && params[0] != 0x7D))
                return fail(err, errlen, "%s is not at SPEC.md 16's type", HOOKS[h]);
            seen[h] = 1;
        }
    }
    for (h = 0; h < 3; h++)
        if (!seen[h])
            return fail(err, errlen, "no %s export", HOOKS[h]);
    if (!memory)
        return fail(err, errlen, "no memory export");
    if (have[8])
        return fail(err, errlen, "the module has a start function; nothing may run before _init");
    return par_exports(par, item, sp, err, errlen);
}

#ifdef MOY_WASM

/* -- the shape from a loaded module (WAMR) ---------------------------------- */

/* Is `t` (params) -> () with the given param kinds? */
static int hook_type(wasm_func_type_t t, uint32_t nparams, wasm_valkind_t kind)
{
    uint32_t i;
    if (wasm_func_type_get_param_count(t) != nparams) return 0;
    if (wasm_func_type_get_result_count(t) != 0) return 0;
    for (i = 0; i < nparams; i++)
        if (wasm_func_type_get_param_valkind(t, i) != kind) return 0;
    return 1;
}

int moy_wasm_check(wasm_module_t module, const uint8_t *wasm, size_t size,
                   uint32_t pages, char *err, size_t errlen)
{
    int32_t i, n;
    int seen[3] = {0, 0, 0}, memory = 0, par = 0, item = 0, sp = 0;
    wasm_import_t im;
    wasm_export_t ex;

    if (check_memory(wasm, size, pages, err, errlen)) return -1;

    n = wasm_runtime_get_import_count(module);
    for (i = 0; i < n; i++) {
        wasm_runtime_get_import_type(module, i, &im);
        if (strcmp(im.module_name, MOY_WASM_MODULE) != 0)
            return fail(err, errlen, "imports %s.%s: a compiled cart imports only from module \"moy\"",
                        im.module_name, im.name);
        if (im.kind != WASM_IMPORT_EXPORT_KIND_FUNC)
            return fail(err, errlen, "imports %s.%s as something other than a function",
                        im.module_name, im.name);
        if (!im.linked)
            return fail(err, errlen, "imports %s.%s, which is not in the import table at that type",
                        im.module_name, im.name);
        if (!strcmp(im.name, "par")) par = 1;
    }

    n = wasm_runtime_get_export_count(module);
    for (i = 0; i < n; i++) {
        int h;
        wasm_runtime_get_export_type(module, i, &ex);
        if (ex.kind == WASM_IMPORT_EXPORT_KIND_MEMORY && !strcmp(ex.name, "memory")) {
            memory = 1;
            continue;
        }
        if (ex.kind == WASM_IMPORT_EXPORT_KIND_GLOBAL && !strcmp(ex.name, MOY_WASM_SP)) {
            sp = wasm_global_type_get_valkind(ex.u.global_type) == WASM_I32
                 && wasm_global_type_get_mutable(ex.u.global_type) ? 1 : -1;
            continue;
        }
        if (ex.kind != WASM_IMPORT_EXPORT_KIND_FUNC) continue;
        if (!strcmp(ex.name, MOY_WASM_ITEM)) {
            item = hook_type(ex.u.func_type, 2u, WASM_I32) ? 1 : -1;
            continue;
        }
        for (h = 0; h < 3; h++) {
            if (strcmp(ex.name, HOOKS[h]) != 0) continue;
            if (!hook_type(ex.u.func_type, h == 1 ? 1u : 0u, WASM_F32))
                return fail(err, errlen, "%s is not at SPEC.md 16's type", HOOKS[h]);
            seen[h] = 1;
        }
    }
    for (i = 0; i < 3; i++)
        if (!seen[i])
            return fail(err, errlen, "no %s export", HOOKS[i]);
    if (!memory)
        return fail(err, errlen, "no memory export");
    return par_exports(par, item, sp, err, errlen);
}

#endif /* MOY_WASM */

/* -- running the cart ------------------------------------------------------ */

/* What every hook runs under: the screen as the target, and for _draw the
 * background repaint (moy_lua_draw's, when the host did not take it over) and
 * the one blit a _draw may make. */
static void begin(moy_wasm *w, int hook)
{
    moy_console *con = w->con;
    w->target = w->screen;
    /* A frame the host took and never showed reaches the screen before the
     * cart can change it. */
    if (w->owed) settle(w);
    if (hook == MOY_WASM_DRAW) {
        if (con->has_bg && !con->host.background) {
            w->kept = NULL;
            moy_cls(w->screen, con->bg);
        }
        w->in_draw = 1;
        w->blits = 0;
    }
}

static void end(moy_wasm *w)
{
    w->target = w->screen;
    w->in_draw = 0;
}

static void attach(moy_wasm *w, moy_console *con)
{
    w->con = con;
    w->screen = w->target = con->canvas;
    w->n_layers = 0;
    w->in_draw = w->blits = w->quitting = w->items = 0;
    w->owed = w->kept = NULL;
    w->snd_level = w->snd_ms = w->snd_rem = 0;
    w->snd_clocked = 0;
}

#ifdef MOY_WASM

int moy_wasm_open(moy_wasm *w, moy_console *con, wasm_exec_env_t env)
{
    int h, k;
    attach(w, con);
    w->env = env;
    w->inst = wasm_runtime_get_module_inst(env);
    for (h = 0; h < 3; h++) {
        w->hooks[h] = wasm_runtime_lookup_function(w->inst, HOOKS[h]);
        if (!w->hooks[h]) return -1;
    }
    for (k = 0; k <= MOY_WASM_LANES; k++) {
        memset(&w->lane[k], 0, sizeof w->lane[k]);
        w->lane[k].w = w;
        w->lane[k].k = k;
    }
    w->item = wasm_runtime_lookup_function(w->inst, MOY_WASM_ITEM);
    w->lane[0].inst = w->inst;
    w->lane[0].env = env;
    if (w->item && !lane_sp(&w->lane[0])) w->item = NULL;
    w->lane[0].item = w->item;
    wasm_runtime_set_custom_data(w->inst, w);
    return 0;
}

static int call(moy_wasm *w, int h, uint32_t argc, uint32_t *argv, char *err, size_t errlen)
{
    int ok;
    begin(w, h);
    ok = wasm_runtime_call_wasm(w->env, w->hooks[h], argc, argv);
    end(w);
    if (ok) return 0;
    if (w->quitting) {                      /* SPEC.md 9: an ending, not a failure */
        wasm_runtime_clear_exception(w->inst);
        return 0;
    }
    w->owed = NULL;                         /* a trapped _draw's frame is never shown */
    if (err && errlen) {
        const char *msg = wasm_runtime_get_exception(w->inst);
        snprintf(err, errlen, "%s", msg ? msg : "the cart trapped");
    }
    return 1;
}

int moy_wasm_init(moy_wasm *w, char *err, size_t errlen)
{
    uint32_t argv[1] = {0};
    return call(w, MOY_WASM_INIT, 0, argv, err, errlen);
}

int moy_wasm_update(moy_wasm *w, float dt, char *err, size_t errlen)
{
    uint32_t argv[1];
    memcpy(&argv[0], &dt, sizeof dt);
    return call(w, MOY_WASM_UPDATE, 1, argv, err, errlen);
}

int moy_wasm_draw(moy_wasm *w, char *err, size_t errlen)
{
    uint32_t argv[1] = {0};
    return call(w, MOY_WASM_DRAW, 0, argv, err, errlen);
}

const uint8_t *moy_wasm_frame(const moy_wasm *w, const moy_pixel **lut)
{
    if (lut) *lut = (w->owed && !w->frame_565) ? w->frame_lut : NULL;
    return w->owed;
}

void moy_wasm_settle(moy_wasm *w)
{
    if (w->owed) settle(w);
}

void moy_wasm_presented(moy_wasm *w, const uint8_t *kept)
{
    if (!w->owed) return;
    w->owed = NULL;
    w->kept = kept;
}

#else /* MOY_WASM_JS */

void moy_wasm_bind(moy_wasm *w, moy_console *con)
{
    attach(w, con);
    w->trap = NULL;
}

void moy_wasm_begin(moy_wasm *w, int hook)
{
    w->trap = NULL;
    begin(w, hook);
}

/* A cart that swallowed the adapter's throw still trapped: w->trap says so. */
int moy_wasm_end(moy_wasm *w, int threw)
{
    end(w);
    if (!threw && !w->trap) return 0;
    return w->quitting ? 0 : 1;             /* SPEC.md 9: an ending, not a failure */
}

const char *moy_wasm_trapped(const moy_wasm *w)
{
    return w->trap;
}

#endif

void moy_wasm_close(moy_wasm *w)
{
    int i;
    moy_host *h;
    if (!w || !w->con) return;
    h = &w->con->host;
    for (i = 0; i < w->n_layers; i++)
        if (h->layer_free) h->layer_free(h->user, w->layers[i].pix);
    w->n_layers = 0;
    w->owed = w->kept = NULL;
#ifdef MOY_WASM
    lanes_free(w);
    if (w->inst) wasm_runtime_set_custom_data(w->inst, NULL);
#endif
}

#else

/* Without an engine this file is empty on purpose: libmoy has no dependencies
 * unless a host asks for this binding. ISO C wants one declaration. */
typedef int moy_wasm_not_built;

#endif /* MOY_WASM || MOY_WASM_JS */
