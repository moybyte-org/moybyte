/* The host console both host shims run a cart against: moyhost_lua.c (a Lua
 * cart) and moyhost_wasm.c (a compiled one).
 *
 * It is native/moycore/modmoycore.c's host half with the MicroPython removed --
 * the same console, the same snapshot-in / queue-out callbacks -- and ONE copy
 * of it serves both shims, because a verb that answers one way for a Lua cart
 * and another for a wasm cart on the same host is the disease the host shims
 * exist to prevent. Each shim embeds an `hc_console` and points `HC` at the run
 * it is driving; the callbacks take `void *user` and read `HC`, one run at a
 * time, which is the device's shape too.
 *
 * Header-only (static functions) because each shim is its own shared library
 * built by runtime/native_build.py, and a header is the one thing both can
 * include without a third translation unit in either build.
 */

#ifndef MOYHOST_CONSOLE_H
#define MOYHOST_CONSOLE_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "moy.h"

#ifndef MOY_PIXEL_RGB565
#error "the host console is the RGB565 build -- compile with -DMOY_PIXEL_RGB565=1"
#endif

enum { SNAP_BTN = 0, SNAP_BTNP, SNAP_BTN_P1, SNAP_BTNP_P1, SNAP_PLAYERS,
       SNAP_TIME_MS, SNAP_TOUCH_X, SNAP_TOUCH_Y, SNAP_TOUCH_DOWN,
       SNAP_TOUCH_MS, SNAP_KEY, SNAP_KEY_DOWN, SNAP_TEXTMODE, SNAP_QUIT,
       SNAP_LEN };
enum { AQ_SFX = 0, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME };
#define AQ_SLOTS 4

typedef struct {
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
    uint8_t     flags[MOY_FLAGS];   /* SPEC.md 3.5, seeded by hc_set_flags */
    char       *cfg;         /* the cart's config as "key\0value\0" pairs, or
                                NULL, which answers every key absent */
    int         cfg_len;
} hc_console;

static hc_console *HC;       /* the callbacks take void*; one run at a time */

/* A monotonic millisecond counter -- the host twin of mp_hal_ticks_ms. Only
 * time() needs it, and only for the elapsed-inside-this-tick term. */
static uint32_t hc_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000u + (uint32_t)(ts.tv_nsec / 1000000));
}

static int h_btn(void *u, moy_button b, int p)
{ (void)u; return HC && HC->snap ? (HC->snap[p > 0 ? SNAP_BTN_P1 : SNAP_BTN] >> (int)b) & 1 : 0; }
static int h_btnp(void *u, moy_button b, int p)
{ (void)u; return HC && HC->snap ? (HC->snap[p > 0 ? SNAP_BTNP_P1 : SNAP_BTNP] >> (int)b) & 1 : 0; }
static int h_players(void *u)
{ (void)u; int n = (HC && HC->snap) ? HC->snap[SNAP_PLAYERS] : 1; return n < 1 ? 1 : n; }

/* time() = the snapshot's frame base PLUS the milliseconds elapsed inside this
 * tick. The base alone froze the clock for the whole frame -- correct for
 * input, which must answer consistently all frame, and wrong for a clock:
 * anything measuring its own work inside a frame read zero forever. (Bench Lua
 * grows a batch until it costs TARGET_MS, so it grew without bound.) The host
 * keeps authority over the base; C adds only what the host cannot see. */
static uint32_t g_tick_ms;

static uint32_t h_time(void *u)
{
    uint32_t base = (HC && HC->snap) ? (uint32_t)HC->snap[SNAP_TIME_MS] : 0;
    (void)u;
    return base + (hc_now_ms() - g_tick_ms);
}
static int32_t h_pget(void *u, int s)
{ (void)u; return (HC && s >= 0 && s < 256) ? HC->pmem[s] : 0; }
static void h_pset(void *u, int s, int32_t v)
{ (void)u; if (HC && s >= 0 && s < 256 && HC->pmem[s] != v) { HC->pmem[s] = v; HC->pmem_dirty = 1; } }

static void aq_push(int op, int a, int b, int c)
{
    if (!HC || !HC->aq || HC->aq_cap < 1 + AQ_SLOTS) return;
    int n = HC->aq[0];
    if (n < 0) n = 0;
    if (1 + (n + 1) * AQ_SLOTS > HC->aq_cap) return;
    int32_t *p = HC->aq + 1 + n * AQ_SLOTS;
    p[0] = op; p[1] = a; p[2] = b; p[3] = c;
    HC->aq[0] = n + 1;
}

static void h_sfx(void *u, int n, int ch) { (void)u; aq_push(AQ_SFX, n, ch, 0); }
static void h_music(void *u, int t, int l) { (void)u; aq_push(AQ_MUSIC, t, l, 0); }
static void h_mstop(void *u) { (void)u; aq_push(AQ_MUSIC_STOP, 0, 0, 0); }
static void h_sstop(void *u, int ch) { (void)u; aq_push(AQ_SOUND_STOP, ch, 0, 0); }
static void h_vol(void *u, int l) { (void)u; aq_push(AQ_VOLUME, l, 0, 0); }
static void h_beep(void *u, float hz, float s)
{ (void)u; aq_push(AQ_BEEP, (int)hz, (int)(s * 1000.0f), 0); }

/* The pointer slot is FLAGS, not a level: it is the only one h_touch has and
 * touch() must answer three things out of it. Mirrors runtime/widgets.py's
 * P_LIVE / P_HELD / P_CLICK -- 0 is "no pointer", which reads as nil. */
static int h_touch(void *u, int out[4])
{
    (void)u;
    int st;
    if (!HC || !HC->snap || !(st = HC->snap[SNAP_TOUCH_DOWN])) return 0;
    out[0] = HC->snap[SNAP_TOUCH_X]; out[1] = HC->snap[SNAP_TOUCH_Y];
    out[2] = (st & 4) != 0;                      /* tapped: the press edge */
    out[3] = (st & 2) != 0;                      /* held */
    return 1;
}
static int h_key(void *u, int code)
{ (void)u; if (!HC || !HC->snap) return 0;
  return code < 0 ? HC->snap[SNAP_KEY] : (HC->snap[SNAP_KEY_DOWN] == code); }
static int h_keyp(void *u, int code)
{ (void)u; if (!HC || !HC->snap) return 0;
  return code < 0 ? HC->snap[SNAP_KEY] : (HC->snap[SNAP_KEY] == code); }
static void h_textmode(void *u, int on)
{ (void)u; if (HC && HC->snap) HC->snap[SNAP_TEXTMODE] = on ? 1 : 0; }
static void h_quit(void *u)
{ (void)u; if (HC && HC->snap) HC->snap[SNAP_QUIT] = 1; }

/* A config value as its text: config.json's string without quotes, a number
 * as the host rendered it. Borrowed from the table and read during the call. */
static const char *h_cfg(void *u, const char *k)
{
    const char *p, *end;
    (void)u;
    if (!HC || !HC->cfg || !k) return NULL;
    p = HC->cfg;
    end = HC->cfg + HC->cfg_len;
    while (p < end) {
        const char *v = p + strlen(p) + 1;
        if (v >= end) break;
        if (strcmp(p, k) == 0) return v;
        p = v + strlen(v) + 1;
    }
    return NULL;
}

/* Build `c`'s console over the caller's snapshot and audio queue and install
 * the callbacks. The canvas is the shim's to initialise. */
static void hc_open(hc_console *c, int32_t *snap, int32_t *aq, int aq_cap)
{
    moy_host *hs = &c->con.host;
    c->snap = snap; c->aq = aq; c->aq_cap = aq_cap;
    if (aq && aq_cap > 0) aq[0] = 0;
    c->con.canvas = &c->canvas;
    c->con.flags = c->flags;
    hs->user = NULL;
    hs->btn = h_btn; hs->btnp = h_btnp; hs->players = h_players;
    hs->time_ms = h_time; hs->pmem_get = h_pget; hs->pmem_set = h_pset;
    hs->sfx = h_sfx; hs->music = h_music; hs->beep = h_beep;
    hs->music_stop = h_mstop; hs->sound_stop = h_sstop; hs->volume = h_vol;
    hs->touch = h_touch; hs->key = h_key; hs->keyp = h_keyp;
    hs->textmode = h_textmode; hs->quit = h_quit; hs->cfg = h_cfg;
}

/* Both of these CHECK the buffer they are handed: libmoy addresses a sheet
 * with SPEC.md 3.2's fixed 128x256 geometry and a map with the w*h it is told,
 * so anything shorter is an out-of-bounds READ on every sprite drawn.
 * modmoycore.c raises on the short buffer; there is no exception to raise from
 * here, and declining leaves the cart drawing nothing rather than reading
 * whatever the allocator had there. */
static void hc_set_sheet(hc_console *c, uint8_t *pix, int nbytes)
{
    if (pix && nbytes >= MOY_SHEET_W * MOY_SHEET_H) {
        moy_sheet_init(&c->sheet, pix);
        c->con.sheet = &c->sheet;
    } else {
        c->con.sheet = NULL;
    }
}

static void hc_set_map(hc_console *c, uint8_t *cells, int nbytes, int w, int h)
{
    if (cells && w > 0 && h > 0 && (long)w * (long)h <= (long)nbytes) {
        moy_map_init(&c->map, cells, w, h);
        c->con.map = &c->map;
    } else {
        c->con.map = NULL;
    }
}

/* SPEC.md 3.5's tile flags, COPIED into the run rather than borrowed like the
 * sheet and the map: the table is 512 bytes, C writes it (fset, a poke to
 * 0x3000) and the caller hands over whatever it has. A short blob leaves the
 * rest zero, as a short file does; NULL clears it. */
static void hc_set_flags(hc_console *c, const uint8_t *flags, int nbytes)
{
    if (nbytes > MOY_FLAGS) nbytes = MOY_FLAGS;
    memset(c->flags, 0, sizeof(c->flags));
    if (flags && nbytes > 0) memcpy(c->flags, flags, (size_t)nbytes);
}

/* The config table, COPIED: "key\0value\0" pairs, `len` bytes. */
static void hc_set_cfg(hc_console *c, const char *blob, int len)
{
    free(c->cfg);
    c->cfg = NULL;
    c->cfg_len = 0;
    if (blob && len > 0 && (c->cfg = (char *)malloc((size_t)len + 1)) != NULL) {
        memcpy(c->cfg, blob, (size_t)len);
        c->cfg[len] = 0;
        c->cfg_len = len;
    }
}

static int hc_pmem_image(hc_console *c, int32_t *out, int n)
{
    if (n > 256) n = 256;
    memcpy(out, c->pmem, (size_t)n * sizeof(int32_t));
    int d = c->pmem_dirty;
    c->pmem_dirty = 0;
    return d;
}

static void hc_pmem_load(hc_console *c, const int32_t *in, int n)
{
    if (n > 256) n = 256;
    memcpy(c->pmem, in, (size_t)n * sizeof(int32_t));
}

/* What the cart last declared with view(), or 0 when it has not. libmoy
 * records it on the console (SPEC.md 6), so the host reads rather than being
 * called -- see the device glue for why that matters. */
static int hc_get_view(hc_console *c, int *w, int *h)
{
    if (c->con.view_w <= 0) return 0;
    *w = c->con.view_w;
    *h = c->con.view_h;
    return 1;
}

static void hc_close(hc_console *c)
{
    free(c->cfg);
    c->cfg = NULL;
    c->cfg_len = 0;
    if (HC == c) HC = NULL;
}

#endif /* MOYHOST_CONSOLE_H */
