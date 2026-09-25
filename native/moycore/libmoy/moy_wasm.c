/* The wasm binding: proposals/wasm-runtime.md's import table over WAMR.
 *
 * The Lua binding's twin (src/moy_lua.c), and deliberately the same shape:
 * every verb is a thin call into the raster or the host seam, and the only
 * code here that is not glue is the marshalling the proposal pins -- a
 * sentinel for each overload, an out pointer for several results, a handle
 * for a layer, bytes and a length for a string. The rules are the proposal's
 * and the table is proposals/wasm-imports.json; test/wasm_table_check.py holds
 * NATIVES below equal to it.
 *
 * Every pointer a cart hands over is an offset into its linear memory and is
 * bounds-checked before it is touched: by WAMR for a '*~' pair in a
 * signature, here for the rest. A range outside the memory, a handle
 * make_layer never returned and a blit outside _draw set the instance's
 * exception, which is a trap: the call that made it returns non-zero and the
 * host ends the cart.
 */

#ifdef MOY_WASM

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

typedef wasm_exec_env_t env_t;

static const char *const HOOKS[3] = { "_init", "_update", "_draw" };

/* The binding behind an import call, or NULL -- and a trap -- when the
 * instance has none yet: a start function reaching the console. */
static moy_wasm *bound(env_t env)
{
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    moy_wasm *w = (moy_wasm *)wasm_runtime_get_custom_data(inst);
    if (!w)
        wasm_runtime_set_exception(inst, "moy: an import ran before the cart was bound");
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

/* Several results: consecutive little-endian i32 at `out`, nothing when out
 * is 0. Returns 0 when `out` is not a valid range (the instance is trapped). */
static int results(moy_wasm *w, uint32_t out, const int32_t *v, int n)
{
    uint8_t *p;
    int i;
    if (!out) return 1;
    p = span(w, out, (uint64_t)n * 4u);
    if (!p) return 0;
    for (i = 0; i < n; i++) {
        uint32_t u = (uint32_t)v[i];
        p[i * 4 + 0] = (uint8_t)(u & 0xFFu);
        p[i * 4 + 1] = (uint8_t)((u >> 8) & 0xFFu);
        p[i * 4 + 2] = (uint8_t)((u >> 16) & 0xFFu);
        p[i * 4 + 3] = (uint8_t)(u >> 24);
    }
    return 1;
}

static moy_canvas *layer_of(moy_wasm *w, int32_t h)
{
    if (h < 1 || h > w->n_layers) return NULL;
    return &w->layers[h - 1];
}

/* -- drawing (SPEC.md 6, 6.1): into the target ---------------------------- */

static void w_cls(env_t e, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_cls(w->target, c); }

static int32_t w_pix(env_t e, int32_t x, int32_t y, int32_t c)
{
    moy_wasm *w = bound(e);
    if (!w) return 0;
    if (c >= 0) {
        moy_pix(w->target, x, y, c);
        return 0;
    }
    return moy_pget(w->target, x, y);
}

static void w_line(env_t e, int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_line(w->target, x0, y0, x1, y1, c); }

static void w_rect(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_rect(w->target, x, y, ww, hh, c); }

static void w_rectb(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_rectb(w->target, x, y, ww, hh, c); }

static void w_circ(env_t e, int32_t cx, int32_t cy, int32_t r, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_circ(w->target, cx, cy, r, c); }

static void w_circb(env_t e, int32_t cx, int32_t cy, int32_t r, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_circb(w->target, cx, cy, r, c); }

static void w_oval(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_oval(w->target, x, y, ww, hh, c); }

static void w_ovalb(env_t e, int32_t x, int32_t y, int32_t ww, int32_t hh, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_ovalb(w->target, x, y, ww, hh, c); }

static void w_tri(env_t e, int32_t x1, int32_t y1, int32_t x2, int32_t y2,
                  int32_t x3, int32_t y3, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_tri(w->target, x1, y1, x2, y2, x3, y3, c); }

static void w_trib(env_t e, int32_t x1, int32_t y1, int32_t x2, int32_t y2,
                   int32_t x3, int32_t y3, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_trib(w->target, x1, y1, x2, y2, x3, y3, c); }

/* '*~': WAMR has checked the bytes are inside linear memory. */
static void w_print(env_t e, const uint8_t *s, uint32_t len, int32_t x, int32_t y, int32_t c)
{ moy_wasm *w = bound(e); if (w) moy_print(w->target, s, (size_t)len, x, y, c); }

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
    moy_tline(w->target, w->con->sheet, w->con->map, x0, y0, x1, y1, u, v, du, dv, ck);
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
    moy_spr(w->target, w->con->sheet, n, x, y, ck, scale, flip);
}

static void w_sspr(env_t e, int32_t sx, int32_t sy, int32_t sw, int32_t sh,
                   int32_t dx, int32_t dy, int32_t dw, int32_t dh,
                   int32_t ck, int32_t flip)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet) return;
    moy_sspr(w->target, w->con->sheet, sx, sy, sw, sh, dx, dy, dw, dh, ck, flip);
}

static void w_map(env_t e, int32_t mx, int32_t my, int32_t mw, int32_t mh,
                  int32_t sx, int32_t sy, int32_t ck, int32_t scale, int32_t layers)
{
    moy_wasm *w = bound(e);
    if (!w || !w->con->sheet || !w->con->map) return;
    moy_map_draw_layers(w->target, w->con->map, w->con->sheet, mx, my, mw, mh,
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

static void w_blit(env_t e, uint32_t frame, uint32_t pal)
{
    moy_wasm *w = bound(e);
    moy_canvas *s;
    moy_pixel lut[256];
    const uint8_t *src, *p = NULL;
    size_t i, n;
    if (!w || !may_blit(w)) return;
    s = w->screen;
    n = (size_t)s->w * (size_t)s->h;
    src = span(w, frame, (uint64_t)n);
    if (!src) return;
    if (pal && !(p = span(w, pal, 768u))) return;
    for (i = 0; i < 256; i++)
        lut[i] = p ? wire_of(w, p[i * 3], p[i * 3 + 1], p[i * 3 + 2])
                   : s->wire[i & 63];
    for (i = 0; i < n; i++) s->pix[i] = lut[src[i]];
}

static void w_blit565(env_t e, uint32_t frame)
{
    moy_wasm *w = bound(e);
    moy_canvas *s;
    const uint8_t *src;
    size_t i, n;
    if (!w || !may_blit(w)) return;
    s = w->screen;
    n = (size_t)s->w * (size_t)s->h;
    src = span(w, frame, (uint64_t)n * 2u);
    if (!src) return;
    for (i = 0; i < n; i++) {
        uint16_t c = (uint16_t)(src[i * 2] | (src[i * 2 + 1] << 8));
        s->pix[i] = w->wire_swapped ? (uint16_t)((c >> 8) | ((c & 0xFFu) << 8)) : c;
    }
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

static int32_t w_read(env_t e, const uint8_t *name, uint32_t nlen, uint32_t offset,
                      uint8_t *dst, uint32_t len)
{
    moy_wasm *w = bound(e);
    char buf[MOY_WASM_NAME_MAX + 1];
    const char *nm;
    uint32_t got;
    if (!w || !w->read) return 0;
    nm = c_string(name, nlen, buf);
    if (!nm || !inside_cart(nm)) return 0;
    got = w->read(w->read_user, nm, offset, dst, len);
    if (len && got > len) got = len;
    return (int32_t)got;
}

/* -- the table ------------------------------------------------------------- */

/* One row per import, in the order of proposals/wasm-imports.json. The
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
};

const NativeSymbol *moy_wasm_natives(uint32_t *count)
{
    if (count) *count = (uint32_t)(sizeof NATIVES / sizeof NATIVES[0]);
    return NATIVES;
}

int moy_wasm_register(NativeSymbol *storage)
{
    uint32_t n;
    const NativeSymbol *t = moy_wasm_natives(&n);
    if (!storage) return -1;
    memcpy(storage, t, n * sizeof *storage);
    return wasm_runtime_register_natives(MOY_WASM_MODULE, storage, n) ? 0 : -1;
}

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
    int seen[3] = {0, 0, 0}, memory = 0, m;
    uint32_t flags = 0, lo = 0, hi = 0;
    wasm_import_t im;
    wasm_export_t ex;

    m = memory_of(wasm, size, &flags, &lo, &hi);
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
    }

    n = wasm_runtime_get_export_count(module);
    for (i = 0; i < n; i++) {
        int h;
        wasm_runtime_get_export_type(module, i, &ex);
        if (ex.kind == WASM_IMPORT_EXPORT_KIND_MEMORY && !strcmp(ex.name, "memory")) {
            memory = 1;
            continue;
        }
        if (ex.kind != WASM_IMPORT_EXPORT_KIND_FUNC) continue;
        for (h = 0; h < 3; h++) {
            if (strcmp(ex.name, HOOKS[h]) != 0) continue;
            if (!hook_type(ex.u.func_type, h == 1 ? 1u : 0u, WASM_F32))
                return fail(err, errlen, "%s is not at the proposal's type", HOOKS[h]);
            seen[h] = 1;
        }
    }
    for (i = 0; i < 3; i++)
        if (!seen[i])
            return fail(err, errlen, "no %s export", HOOKS[i]);
    if (!memory)
        return fail(err, errlen, "no memory export");
    return 0;
}

/* -- running the cart ------------------------------------------------------ */

int moy_wasm_open(moy_wasm *w, moy_console *con, wasm_exec_env_t env)
{
    int h;
    w->con = con;
    w->env = env;
    w->inst = wasm_runtime_get_module_inst(env);
    w->screen = w->target = con->canvas;
    w->n_layers = 0;
    w->in_draw = w->blits = w->quitting = 0;
    for (h = 0; h < 3; h++) {
        w->hooks[h] = wasm_runtime_lookup_function(w->inst, HOOKS[h]);
        if (!w->hooks[h]) return -1;
    }
    wasm_runtime_set_custom_data(w->inst, w);
    return 0;
}

static int call(moy_wasm *w, int h, uint32_t argc, uint32_t *argv, char *err, size_t errlen)
{
    int ok;
    w->target = w->screen;
    ok = wasm_runtime_call_wasm(w->env, w->hooks[h], argc, argv);
    w->target = w->screen;
    if (ok) return 0;
    if (w->quitting) {                      /* SPEC.md 9: an ending, not a failure */
        wasm_runtime_clear_exception(w->inst);
        return 0;
    }
    if (err && errlen) {
        const char *msg = wasm_runtime_get_exception(w->inst);
        snprintf(err, errlen, "%s", msg ? msg : "the cart trapped");
    }
    return 1;
}

int moy_wasm_init(moy_wasm *w, char *err, size_t errlen)
{
    uint32_t argv[1] = {0};
    return call(w, 0, 0, argv, err, errlen);
}

int moy_wasm_update(moy_wasm *w, float dt, char *err, size_t errlen)
{
    uint32_t argv[1];
    memcpy(&argv[0], &dt, sizeof dt);
    return call(w, 1, 1, argv, err, errlen);
}

int moy_wasm_draw(moy_wasm *w, char *err, size_t errlen)
{
    uint32_t argv[1] = {0};
    int r;
    moy_console *con = w->con;
    /* background(c) repaints before every _draw, as moy_lua_draw does, when
     * the host did not take it over. */
    if (con->has_bg && !con->host.background) moy_cls(w->screen, con->bg);
    w->in_draw = 1;
    w->blits = 0;
    r = call(w, 2, 0, argv, err, errlen);
    w->in_draw = 0;
    return r;
}

void moy_wasm_close(moy_wasm *w)
{
    int i;
    moy_host *h;
    if (!w || !w->con) return;
    h = &w->con->host;
    for (i = 0; i < w->n_layers; i++)
        if (h->layer_free) h->layer_free(h->user, w->layers[i].pix);
    w->n_layers = 0;
    if (w->inst) wasm_runtime_set_custom_data(w->inst, NULL);
}

#else

/* Without MOY_WASM this file is empty on purpose: libmoy has no dependencies
 * unless a host asks for this binding. ISO C wants one declaration. */
typedef int moy_wasm_not_built;

#endif /* MOY_WASM */
