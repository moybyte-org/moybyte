// The native target of a compiled cart (sdk/include/moybyte/cart.h): a game
// (native/moy_elf/moy_os.h, A GAME) whose main is this file's, running the
// cart's hooks once a frame over libmoy's raster, as moy-spec's wasm binding
// runs them (native/moycore/libmoy/moy_wasm.c: its verbs' bodies are the model
// for these), and the extension rows over what the console handed the game
// at its start (moy_game_data; runtime/run/elf_run.py's game_data writes it).
// What the wasm binding hands its console the module hands across the game's
// channels (native/moy_elf/moy_os.h, A GAME'S CHANNELS): the audio verbs and
// the stream, the pmem image once a frame it moved, the cart's files, and a
// frame blit made direct colour, presented as RGB565 on its MIXED screen.
// Its prefs are the app rows (moy_prefs_*) over the grant the Player makes a
// game's run.
//
// The data: "MOYG", its whole size and its entries' count as two u32, then
// the entries, each a kind byte, a name-length byte, the name and a u32 size
// before its bytes (every number little-endian, nothing aligned):
//
//   1 sheet  MOY_SHEET_W * MOY_SHEET_H indices
//   2 flags  MOY_FLAGS bytes
//   3 map    its width and height as two u16, then a cell a byte (tile + 1)
//   4 cfg    the config as "key\0value\0" pairs (lua_ext.cfg_blob's)
//   5 pmem   256 int32
//   6 scene  `name`'s actors: a u32 count, that many MOYCORE_ACTOR-byte
//            records, then each one's "flags" object as a u32 length and its
//            JSON text; the first scene is the active one at the start
//   7 image  `name`'s indices: its width and height as two u16, then a byte a
//            pixel, one past the palette transparent
//   8 wire   the console canvas's 64 words for the palette's indices, in its
//            byte order (what a direct-colour frame's drawn pixels become)
//
// Built by tools/elf_cart.py into a cart's module (its `cart` object) with
// libmoy's raster and the SDK's C library from libmoyos.a.

#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "moy.h"
#include "moybyte/frame.h"
#include "moybyte/os.h"

// libmoy's verbs whose names the cart's header gives the cart's own verbs,
// reached under these from below it.
static void r_cls(moy_canvas *c, int col) { moy_cls(c, col); }
static void r_pix(moy_canvas *c, int x, int y, int col) { moy_pix(c, x, y, col); }
static void r_line(moy_canvas *c, int x0, int y0, int x1, int y1, int col) { moy_line(c, x0, y0, x1, y1, col); }
static void r_rect(moy_canvas *c, int x, int y, int w, int h, int col) { moy_rect(c, x, y, w, h, col); }
static void r_rectb(moy_canvas *c, int x, int y, int w, int h, int col) { moy_rectb(c, x, y, w, h, col); }
static void r_circ(moy_canvas *c, int x, int y, int r, int col) { moy_circ(c, x, y, r, col); }
static void r_circb(moy_canvas *c, int x, int y, int r, int col) { moy_circb(c, x, y, r, col); }
static void r_oval(moy_canvas *c, int x, int y, int w, int h, int col) { moy_oval(c, x, y, w, h, col); }
static void r_ovalb(moy_canvas *c, int x, int y, int w, int h, int col) { moy_ovalb(c, x, y, w, h, col); }
static void r_tri(moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col) { moy_tri(c, x1, y1, x2, y2, x3, y3, col); }
static void r_trib(moy_canvas *c, int x1, int y1, int x2, int y2, int x3, int y3, int col) { moy_trib(c, x1, y1, x2, y2, x3, y3, col); }
static void r_print(moy_canvas *c, const uint8_t *s, size_t n, int x, int y, int col) { moy_print(c, s, n, x, y, col); }
static void r_camera(moy_canvas *c, int x, int y) { moy_camera(c, x, y); }
static void r_clip(moy_canvas *c, int x, int y, int w, int h) { moy_clip(c, x, y, w, h); }
static void r_pal(moy_canvas *c, int c0, int c1) { moy_pal(c, c0, c1); }
static void r_palt(moy_canvas *c, int col, int on) { moy_palt(c, col, on); }
static void r_fillp(moy_canvas *c, int p, int col) { moy_fillp(c, p, col); }
static void r_tline(moy_canvas *c, const moy_sheet *s, const moy_map *m, int x0, int y0, int x1, int y1, int32_t u, int32_t v, int32_t du, int32_t dv, int ck) { moy_tline(c, s, m, x0, y0, x1, y1, u, v, du, dv, ck); }
static void r_spr(moy_canvas *c, const moy_sheet *s, int n, int x, int y, int ck, int scale, int flip) { moy_spr(c, s, n, x, y, ck, scale, flip); }
static void r_sspr(moy_canvas *c, const moy_sheet *s, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, int ck, int flip) { moy_sspr(c, s, sx, sy, sw, sh, dx, dy, dw, dh, ck, flip); }
static int r_mget(const moy_map *m, int x, int y) { return moy_mget(m, x, y); }
static void r_mset(moy_map *m, int x, int y, int tile) { moy_mset(m, x, y, tile); }
static float r_rnd(moy_console *con, float n) { return moy_rnd(con, n); }
static void r_srand(moy_console *con, uint32_t seed) { moy_srand(con, seed); }
typedef moy_map map_t;          // libmoy's map, a name the header gives a verb

#include "moybyte/cart.h"

#define LAYERS 8            // moy_wasm.h's MOY_WASM_LAYERS
#define SCENES 16
#define IMAGES 32
#define ACTOR 32            // moycore_lua.h's MOYCORE_ACTOR

typedef struct {
    const char *name;
    uint32_t name_n;
    uint32_t n;             // actors
    const uint8_t *rows;    // n records
    const uint8_t *flags;   // n (u32 length, text)
} scene_t;

typedef struct {
    const char *name;
    uint32_t name_n;
    uint32_t w, h;
    const uint8_t *pix;
} image_t;

static moy_canvas g_screen;
static moy_canvas *g_target = &g_screen;
static moy_canvas g_layer[LAYERS];
static int g_layers;
static moy_console g_con;
static moy_sheet g_sheet_s;
static map_t g_map_s;
static const char *g_cfg;
static uint32_t g_cfg_n;
static int32_t g_pmem[256];
static scene_t g_scene[SCENES];
static int g_scenes, g_active;
static image_t g_image[IMAGES];
static int g_images;
static int g_pmem_moved;
static moy_frame_t g_f;
static uint32_t g_ms;
static uint16_t g_wire[MOY_PALETTE];
static int g_has_wire;
// A direct-colour frame (blit's palette, blit565): its words in the
// console's byte order, held until a cls or the next blit, as the wasm
// binding keeps it (moy_wasm.c's `kept`); the screen's pixels since then
// are drawn over it, every other one UNDRAWN.
#define UNDRAWN 0xFFu
static uint16_t *g_direct, *g_out;
static int g_direct_on;

static uint32_t u32_at(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint32_t u16_at(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8;
}

static void scene_of(const char *name, uint32_t name_n, const uint8_t *d, uint32_t n) {
    if (g_scenes >= SCENES || n < 4) {
        return;
    }
    uint32_t k = u32_at(d);
    if ((uint64_t)k * ACTOR > n - 4) {
        return;
    }
    scene_t *s = &g_scene[g_scenes++];
    s->name = name;
    s->name_n = name_n;
    s->n = k;
    s->rows = d + 4;
    s->flags = d + 4 + (size_t)k * ACTOR;
}

static void image_of(const char *name, uint32_t name_n, const uint8_t *d, uint32_t n) {
    if (g_images >= IMAGES || n < 4) {
        return;
    }
    uint32_t w = u16_at(d), h = u16_at(d + 2);
    if ((uint64_t)w * h > n - 4) {
        return;
    }
    image_t *im = &g_image[g_images++];
    im->name = name;
    im->name_n = name_n;
    im->w = w;
    im->h = h;
    im->pix = d + 4;
}

// What the console handed the game, read whole into memory of the module's
// own and laid over the tables above.
static void load_data(void) {
    uint8_t head[12];
    if (moy_game_data(0, head, sizeof head) != (int)sizeof head || memcmp(head, "MOYG", 4) != 0) {
        return;
    }
    uint32_t total = u32_at(head + 4);
    uint8_t *d = total >= sizeof head ? malloc(total) : NULL;
    if (d == NULL) {
        return;
    }
    int got = moy_game_data(0, d, total);
    if (got < (int)sizeof head) {
        free(d);
        return;
    }
    const uint8_t *p = d + sizeof head, *end = d + got;
    uint32_t count = u32_at(head + 8);
    for (uint32_t i = 0; i < count && end - p >= 2; i++) {
        uint8_t kind = p[0], nn = p[1];
        if ((size_t)(end - p) < 2u + nn + 4u) {
            break;
        }
        const char *name = (const char *)(p + 2);
        uint32_t n = u32_at(p + 2 + nn);
        const uint8_t *v = p + 2 + nn + 4;
        if ((size_t)(end - v) < n) {
            break;
        }
        switch (kind) {
        case 1:
            if (n >= MOY_SHEET_W * MOY_SHEET_H) {
                moy_sheet_init(&g_sheet_s, (uint8_t *)v);
                g_con.sheet = &g_sheet_s;
            }
            break;
        case 2:
            if (n >= MOY_FLAGS) {
                g_con.flags = (uint8_t *)v;
            }
            break;
        case 3:
            if (n >= 4 && (uint64_t)u16_at(v) * u16_at(v + 2) <= n - 4) {
                moy_map_init(&g_map_s, (uint8_t *)v + 4, (int)u16_at(v), (int)u16_at(v + 2));
                g_con.map = &g_map_s;
            }
            break;
        case 4:
            g_cfg = (const char *)v;
            g_cfg_n = n;
            break;
        case 5:
            for (uint32_t k = 0; k < 256 && k * 4 + 4 <= n; k++) {
                g_pmem[k] = (int32_t)u32_at(v + k * 4);
            }
            break;
        case 6:
            scene_of(name, nn, v, n);
            break;
        case 7:
            image_of(name, nn, v, n);
            break;
        case 8:
            if (n >= 2u * MOY_PALETTE) {
                for (uint32_t k = 0; k < MOY_PALETTE; k++) {
                    g_wire[k] = (uint16_t)u16_at(v + 2u * k);
                }
                g_has_wire = 1;
            }
            break;
        default:
            break;
        }
        p = v + n;
    }
}

static int same(const char *a, uint32_t an, const char *b, uint32_t bn) {
    return an == bn && memcmp(a, b, an) == 0;
}

// -- the drawing verbs ----------------------------------------------------------

void moy_cls(int32_t c) {
    if (g_target == &g_screen) {
        g_direct_on = 0;            // cls replaces the whole screen, a blit's frame too
    }
    r_cls(g_target, c);
}

void moy_background(int32_t c) {
    g_con.bg = c;
    g_con.has_bg = 1;
}

void moy_view(int32_t w, int32_t h) {
    g_con.view_w = w < 0 ? 0 : w;
    g_con.view_h = h < 0 ? 0 : h;
}

int32_t moy_pix(int32_t x, int32_t y, int32_t c) {
    if (c >= 0) {
        r_pix(g_target, x, y, c);
        return 0;
    }
    return moy_pget(g_target, x, y);
}

void moy_line(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t c) { r_line(g_target, x0, y0, x1, y1, c); }
void moy_rect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c) { r_rect(g_target, x, y, w, h, c); }
void moy_rectb(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c) { r_rectb(g_target, x, y, w, h, c); }
void moy_circ(int32_t cx, int32_t cy, int32_t r, int32_t c) { r_circ(g_target, cx, cy, r, c); }
void moy_circb(int32_t cx, int32_t cy, int32_t r, int32_t c) { r_circb(g_target, cx, cy, r, c); }
void moy_oval(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c) { r_oval(g_target, x, y, w, h, c); }
void moy_ovalb(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c) { r_ovalb(g_target, x, y, w, h, c); }

void moy_print(const char *s, int32_t len, int32_t x, int32_t y, int32_t c) {
    if (s != NULL && len > 0) {
        r_print(g_target, (const uint8_t *)s, (size_t)len, x, y, c);
    }
}

void moy_camera(int32_t x, int32_t y, int32_t *out) {
    if (out != NULL) {
        out[0] = g_target->cam_x;
        out[1] = g_target->cam_y;
    }
    r_camera(g_target, x, y);
}

void moy_clip(int32_t x, int32_t y, int32_t w, int32_t h) { r_clip(g_target, x, y, w, h); }

void moy_pal(int32_t c0, int32_t c1, int32_t p) {
    if (c0 < 0) {
        moy_pal_reset(g_target);
    } else if (p == 1) {
        moy_pal_screen(g_target, c0, c1);
    } else {
        r_pal(g_target, c0, c1);
    }
}

void moy_palt(int32_t c, int32_t on) {
    if (c < 0) {
        moy_palt_reset(g_target);
    } else {
        r_palt(g_target, c, on != 0);
    }
}

void moy_fillp(int32_t p, int32_t c) { r_fillp(g_target, p, c); }

int32_t moy_make_layer(int32_t w, int32_t h) {
    if (w <= 0 || h <= 0 || w > 4096 || h > 4096 || g_layers >= LAYERS) {
        return 0;
    }
    void *px = mmap(NULL, (size_t)w * (size_t)h, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (px == MAP_FAILED) {
        return 0;
    }
    moy_canvas_init(&g_layer[g_layers], (moy_pixel *)px, w, h);
    return ++g_layers;
}

static moy_canvas *layer_of(int32_t h) {
    return h >= 1 && h <= g_layers ? &g_layer[h - 1] : NULL;
}

void moy_draw_layer(int32_t layer, int32_t cx, int32_t cy) {
    moy_canvas *l = layer_of(layer);
    if (l != NULL) {
        moy_blit_window(&g_screen, l, cx, cy);
    }
}

void moy_target(int32_t layer) {
    moy_canvas *l = layer == 0 ? &g_screen : layer_of(layer);
    if (l != NULL) {
        g_target = l;
    }
}

void moy_tri(int32_t x1, int32_t y1, int32_t x2, int32_t y2, int32_t x3, int32_t y3, int32_t c) { r_tri(g_target, x1, y1, x2, y2, x3, y3, c); }
void moy_trib(int32_t x1, int32_t y1, int32_t x2, int32_t y2, int32_t x3, int32_t y3, int32_t c) { r_trib(g_target, x1, y1, x2, y2, x3, y3, c); }

void moy_tline(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t u, int32_t v, int32_t du,
               int32_t dv, int32_t colorkey) {
    if (g_con.sheet != NULL && g_con.map != NULL) {
        r_tline(g_target, g_con.sheet, g_con.map, x0, y0, x1, y1, u, v, du, dv, colorkey);
    }
}

// -- sprites and map -------------------------------------------------------------

void moy_spr(int32_t n, int32_t x, int32_t y, int32_t colorkey, int32_t scale, int32_t flip) {
    if (g_con.sheet != NULL) {
        r_spr(g_target, g_con.sheet, n, x, y, colorkey, scale, flip);
    }
}

void moy_sspr(int32_t sx, int32_t sy, int32_t sw, int32_t sh, int32_t dx, int32_t dy, int32_t dw,
              int32_t dh, int32_t colorkey, int32_t flip) {
    if (g_con.sheet != NULL) {
        r_sspr(g_target, g_con.sheet, sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip);
    }
}

int32_t moy_sget(int32_t x, int32_t y) { return g_con.sheet != NULL ? moy_sheet_pget(g_con.sheet, x, y) : 0; }

void moy_sset(int32_t x, int32_t y, int32_t c) {
    if (g_con.sheet != NULL) {
        moy_sheet_pset(g_con.sheet, x, y, c);
    }
}

int32_t moy_fget(int32_t n, int32_t b) {
    int v = (g_con.flags != NULL && n >= 0 && n < MOY_FLAGS) ? g_con.flags[n] : 0;
    return b < 0 ? v : (v >> (b & 7)) & 1;
}

void moy_fset(int32_t n, int32_t b, int32_t v) {
    if (g_con.flags == NULL || n < 0 || n >= MOY_FLAGS) {
        return;
    }
    uint8_t *f = &g_con.flags[n];
    if (b < 0) {
        *f = (uint8_t)(v & 0xFF);
    } else if (v) {
        *f = (uint8_t)(*f | (1u << (b & 7)));
    } else {
        *f = (uint8_t)(*f & ~(1u << (b & 7)));
    }
}

void moy_map(int32_t mx, int32_t my, int32_t w, int32_t h, int32_t sx, int32_t sy,
             int32_t colorkey, int32_t scale, int32_t layers) {
    if (g_con.sheet != NULL && g_con.map != NULL) {
        moy_map_draw_layers(g_target, g_con.map, g_con.sheet, mx, my, w, h, sx, sy, colorkey,
                            scale, layers, g_con.flags);
    }
}

int32_t moy_mget(int32_t x, int32_t y) { return g_con.map != NULL ? r_mget(g_con.map, x, y) : -1; }

void moy_mset(int32_t x, int32_t y, int32_t tile) {
    if (g_con.map != NULL) {
        r_mset(g_con.map, x, y, tile);
    }
}

// -- input -------------------------------------------------------------------------

int32_t moy_btn(int32_t b, int32_t player) {
    return player == 0 && b >= 0 && b < MOY_BTN_COUNT && (g_f.held & MOY_BIT(b)) ? 1 : 0;
}

int32_t moy_btnp(int32_t b, int32_t player) {
    return player == 0 && b >= 0 && b < MOY_BTN_COUNT && (g_f.pressed & MOY_BIT(b)) ? 1 : 0;
}

int32_t moy_players(void) { return 1; }

// The pointer word's flags: 1 live, 2 held, 4 the press edge; 0 no pointer.
int32_t moy_touch(int32_t *out) {
    int32_t st = g_f.in[MOY_FRAME_PTR];
    if (st == 0) {
        return 0;
    }
    if (out != NULL) {
        out[0] = g_f.in[MOY_FRAME_X];
        out[1] = g_f.in[MOY_FRAME_Y];
        out[2] = (st & 4) != 0;
        out[3] = (st & 2) != 0;
    }
    return 1;
}

int32_t moy_key(int32_t code) {
    int32_t k = g_f.in[MOY_FRAME_KEY];
    return code < 0 ? k : k != 0 && k == code;
}

int32_t moy_keyp(int32_t code) { return moy_key(code); }

void moy_textmode(int32_t on) { (void)on; }

// -- audio: the run's session, through the console (moy_game_sound) --------------

// native/moy_elf/moy_os.h's MOY_OS_SOUND_*.
enum { SOUND_SFX, SOUND_MUSIC, SOUND_BEEP, SOUND_MUSIC_STOP, SOUND_STOP, SOUND_VOLUME };

void moy_sfx(int32_t n, int32_t chan) { moy_game_sound(SOUND_SFX, n, chan); }
void moy_music(int32_t track, int32_t loop) { moy_game_sound(SOUND_MUSIC, track, loop != 0); }
void moy_music_stop(void) { moy_game_sound(SOUND_MUSIC_STOP, 0, 0); }
void moy_sound_stop(int32_t chan) { moy_game_sound(SOUND_STOP, chan < 0 ? -1 : chan, 0); }
void moy_volume(int32_t level) { moy_game_sound(SOUND_VOLUME, level, 0); }

// moycore_run.c's h_beep: whole hertz and milliseconds, each 0..32000.
void moy_beep(float freq, float dur) {
    int ms = (int)(dur * 1000.0f);
    int hz = (int)freq;
    ms = ms < 0 ? 0 : ms > 32000 ? 32000 : ms;
    hz = hz < 0 ? 0 : hz > 32000 ? 32000 : hz;
    moy_game_sound(SOUND_BEEP, hz, ms);
}

// The stream with no speaker to reach: moy_wasm.c's silent_room, a count that
// drains at the rate by the cart's clock.
#define SND_RATE 22050u             // moy_wasm.h's MOY_WASM_SND_RATE
#define SND_DEPTH 2048u             // and MOY_WASM_SND_DEPTH
static uint32_t g_snd_level, g_snd_ms, g_snd_rem;
static int g_snd_clocked;

static uint32_t silent_room(void) {
    uint32_t now = (uint32_t)moy_time();
    if (g_snd_clocked) {
        uint64_t t = (uint64_t)(uint32_t)(now - g_snd_ms) * SND_RATE + g_snd_rem;
        uint64_t drained = t / 1000u;
        g_snd_rem = (uint32_t)(t % 1000u);
        g_snd_level = drained >= g_snd_level ? 0 : g_snd_level - (uint32_t)drained;
    }
    g_snd_ms = now;
    g_snd_clocked = 1;
    return SND_DEPTH - g_snd_level;
}

int32_t moy_snd(const int16_t *pcm, int32_t n) {
    if (n < 0) {
        return 0;
    }
    int got = moy_game_snd(n ? pcm : NULL, (size_t)n);
    if (got >= 0) {
        return got;
    }
    uint32_t room = silent_room();
    if (n == 0) {
        return (int32_t)room;
    }
    uint32_t k = (uint32_t)n < room ? (uint32_t)n : room;
    g_snd_level += k;
    return (int32_t)k;
}

// -- state and utility ---------------------------------------------------------------

int32_t moy_time(void) { return (int32_t)g_ms; }

int32_t moy_pmem(int32_t slot, int32_t v, int32_t write) {
    if (slot < 0 || slot >= 256) {
        return 0;
    }
    if (write) {
        if (g_pmem[slot] != v) {
            g_pmem[slot] = v;
            g_pmem_moved = 1;
        }
        return 0;
    }
    return g_pmem[slot];
}

int32_t moy_cfg(const char *key, int32_t key_len, char *dst, int32_t dst_len) {
    const char *p = g_cfg, *end = g_cfg + g_cfg_n;
    if (p == NULL || key == NULL || key_len < 0) {
        return -1;
    }
    while (p < end) {
        const char *k = p;
        size_t kn = strnlen(k, (size_t)(end - k));
        const char *v = k + kn + 1;
        if (v > end) {
            break;
        }
        size_t vn = strnlen(v, (size_t)(end - v));
        if (kn == (size_t)key_len && memcmp(k, key, kn) == 0) {
            if (dst != NULL && dst_len > 0) {
                memcpy(dst, v, vn < (size_t)dst_len ? vn : (size_t)dst_len);
            }
            return (int32_t)vn;
        }
        p = v + vn + 1;
    }
    return -1;
}

float moy_rnd(float n) { return r_rnd(&g_con, n); }

void moy_srand(int32_t seed) { r_srand(&g_con, (uint32_t)seed); }

int32_t moy_flr(float x) {
    if (x != x) {
        return 0;
    }
    if (x >= 2147483648.0f) {
        return INT32_MAX;
    }
    if (x < -2147483648.0f) {
        return INT32_MIN;
    }
    int32_t t = (int32_t)x;
    if ((float)t > x) {
        t--;
    }
    return t;
}

// The pmem image to the console where it moved (moy_game_pmem).
static void pmem_hand(void) {
    if (g_pmem_moved) {
        g_pmem_moved = 0;
        moy_game_pmem(g_pmem, 256);
    }
}

void moy_quit(void) {
    pmem_hand();
    _exit(0);
}

// -- the framebuffer: blit, blit565 ---------------------------------------------------

static size_t screen_n(void) {
    return (size_t)g_screen.w * (size_t)g_screen.h;
}

// The direct frame's words (mapped at the first), its pixels marked UNDRAWN
// on the screen: 0 where there is no room or no console colours to draw it
// with.
static int direct_begin(void) {
    size_t n = screen_n();
    if (!g_has_wire) {
        return 0;
    }
    if (g_direct == NULL) {
        void *a = mmap(NULL, 4u * n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (a == MAP_FAILED) {
            return 0;
        }
        g_direct = (uint16_t *)a;
        g_out = g_direct + n;
    }
    memset(g_screen.pix, UNDRAWN, n);
    g_direct_on = 1;
    return 1;
}

static uint16_t wire_of(uint32_t r, uint32_t g, uint32_t b) {
    uint16_t c = (uint16_t)(((r & 0xF8u) << 8) | ((g & 0xFCu) << 3) | (b >> 3));
    return g_f.in[MOY_FRAME_SWAP] ? (uint16_t)((c >> 8) | ((c & 0xFFu) << 8)) : c;
}

// moy_wasm.c's w_blit: an index frame through the palette's colours, or
// through `pal`'s 256 RGB triples, which only a direct frame can show.
void moy_blit(const uint8_t *frame, const uint8_t *pal) {
    size_t n = screen_n();
    if (frame == NULL) {
        return;
    }
    if (pal == NULL || !direct_begin()) {
        g_direct_on = 0;
        for (size_t i = 0; i < n; i++) {
            g_screen.pix[i] = (moy_pixel)(frame[i] & (MOY_PALETTE - 1));
        }
        return;
    }
    uint16_t lut[256];
    for (int i = 0; i < 256; i++) {
        lut[i] = wire_of(pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]);
    }
    for (size_t i = 0; i < n; i++) {
        g_direct[i] = lut[frame[i]];
    }
}

// moy_wasm.c's w_blit565: little-endian RGB565 words, in the console's order.
void moy_blit565(const uint16_t *frame) {
    size_t n = screen_n();
    if (frame == NULL || !direct_begin()) {
        return;
    }
    int swap = g_f.in[MOY_FRAME_SWAP] != 0;
    for (size_t i = 0; i < n; i++) {
        uint16_t c = frame[i];
        g_direct[i] = swap ? (uint16_t)((c >> 8) | ((c & 0xFFu) << 8)) : c;
    }
}

// The frame to present: the screen's indices, or with a direct frame up the
// words it shows (a pixel drawn since in the console's colour for it).
static void present(void *px) {
    size_t n = screen_n();
    if (!g_direct_on) {
        moy_present(px, n);
        return;
    }
    const moy_pixel *s = g_screen.pix;
    for (size_t i = 0; i < n; i++) {
        g_out[i] = s[i] == UNDRAWN ? g_direct[i] : g_wire[s[i] & (MOY_PALETTE - 1)];
    }
    moy_present(g_out, 2u * n);
}

// -- the cart's files (SPEC.md 9.1), the console's (moy_game_read ...) --------------

int32_t moy_read(const char *name, int32_t name_len, int32_t offset, void *dst, int32_t len) {
    if (name_len < 0 || offset < 0 || len < 0) {
        return 0;
    }
    return moy_game_read(name, (size_t)name_len, (uint32_t)offset, len > 0 ? dst : NULL,
                         (size_t)len);
}

int32_t moy_write(const char *path, int32_t path_len, const void *data, int32_t len) {
    if (path_len < 0 || len < 0) {
        return -1;
    }
    return moy_game_write(path, (size_t)path_len, len > 0 ? data : NULL, (size_t)len);
}

int32_t moy_erase(const char *path, int32_t path_len) {
    if (path_len < 0) {
        return -1;
    }
    return moy_game_erase(path, (size_t)path_len);
}

int32_t moy_list(const char *prefix, int32_t prefix_len, int32_t index, char *dst,
                 int32_t dst_len) {
    if (prefix_len < 0 || index < 0 || dst_len < 0) {
        return -1;
    }
    return moy_game_list(prefix, (size_t)prefix_len, (uint32_t)index, dst_len > 0 ? dst : NULL,
                         (size_t)dst_len);
}

// The hooks, each 0 where the cart defines none.
#define HOOK __attribute__((weak, visibility("hidden")))
extern void init(void) HOOK;
extern void update(float dt) HOOK;
extern void draw(void) HOOK;
extern void par_item(int32_t i, int32_t arg) HOOK;

// moy_wasm.c's w_par: the calling thread and a lane take the items in turn,
// the next untaken one each, and par returns when every one has. The lane is
// a thread of the game's, on the other core where the console gives it one
// (moy_os.h's moy_os_caps_t), as a wasm run's lane is; each item runs on its
// thread's own C stack, so `stacks` is the wasm module's alone.
static pthread_mutex_t g_par_lock = PTHREAD_MUTEX_INITIALIZER;
static int32_t g_par_next, g_par_n, g_par_arg;

static void par_take(void) {
    for (;;) {
        pthread_mutex_lock(&g_par_lock);
        int32_t i = g_par_next < g_par_n ? g_par_next++ : -1;
        pthread_mutex_unlock(&g_par_lock);
        if (i < 0) {
            return;
        }
        par_item(i, g_par_arg);
    }
}

static void *par_lane(void *u) {
    (void)u;
    par_take();
    return NULL;
}

void moy_par(int32_t n, int32_t arg, void *stacks, int32_t size) {
    (void)stacks;
    (void)size;
    if (par_item == NULL || n <= 0) {
        return;
    }
    g_par_n = n;
    g_par_arg = arg;
    g_par_next = 0;
    pthread_t lane;
    int go = n > 1 && pthread_create(&lane, NULL, par_lane, NULL) == 0;
    par_take();
    if (go) {
        pthread_join(lane, NULL);
    }
}

// -- Moybyte's extensions -------------------------------------------------------------

static scene_t *scene_at(const char *name, uint32_t name_n) {
    if (name == NULL || name_n == 0) {
        return g_active < g_scenes ? &g_scene[g_active] : NULL;
    }
    for (int i = 0; i < g_scenes; i++) {
        if (same(g_scene[i].name, g_scene[i].name_n, name, name_n)) {
            return &g_scene[i];
        }
    }
    return NULL;
}

int32_t scene(const char *name, uint32_t name_n, moy_actor_t *out, uint32_t cap) {
    scene_t *s = scene_at(name, name_n);
    if (s == NULL) {
        return -1;
    }
    uint32_t k = out != NULL ? cap / ACTOR : 0;
    if (k > s->n) {
        k = s->n;
    }
    if (k > 0) {
        memcpy(out, s->rows, (size_t)k * ACTOR);
    }
    return (int32_t)s->n;
}

int32_t load_scene(const char *name, uint32_t name_n) {
    if (name == NULL || name_n == 0) {
        return -1;
    }
    for (int i = 0; i < g_scenes; i++) {
        if (same(g_scene[i].name, g_scene[i].name_n, name, name_n)) {
            g_active = i;
            return (int32_t)g_scene[i].n;
        }
    }
    return -1;
}

int32_t scene_name(int32_t i, char *dst, uint32_t cap) {
    if (i < 0 || i >= g_scenes) {
        return -1;
    }
    uint32_t n = g_scene[i].name_n;
    if (dst != NULL) {
        memcpy(dst, g_scene[i].name, n < cap ? n : cap);
    }
    return (int32_t)n;
}

int32_t scene_flags(const char *name, uint32_t name_n, int32_t i, char *dst, uint32_t cap) {
    scene_t *s = scene_at(name, name_n);
    if (s == NULL || i < 0 || (uint32_t)i >= s->n) {
        return -1;
    }
    const uint8_t *p = s->flags;
    for (int32_t k = 0; k < i; k++) {
        p += 4 + u32_at(p);
    }
    uint32_t n = u32_at(p);
    if (dst != NULL) {
        memcpy(dst, p + 4, n < cap ? n : cap);
    }
    return (int32_t)n;
}

int32_t image(const char *name, uint32_t name_n) {
    for (int i = 0; i < g_images; i++) {
        if (same(g_image[i].name, g_image[i].name_n, name, name_n)) {
            return i + 1;
        }
    }
    return 0;
}

int32_t image_size(int32_t h) {
    if (h < 1 || h > g_images) {
        return 0;
    }
    const image_t *im = &g_image[h - 1];
    return (int32_t)((im->w & 0xFFFFu) << 16 | (im->h & 0xFFFFu));
}

// moycore_wasm_ext.h's mx_image_spr: an index past the palette and a
// palt-transparent one skipped, the rest through the target's store, camera
// and clip applied.
void image_spr(int32_t h, int32_t x, int32_t y) {
    if (h < 1 || h > g_images) {
        return;
    }
    const image_t *im = &g_image[h - 1];
    moy_canvas *c = g_target;
    int64_t x0 = (int64_t)x - c->cam_x, y0 = (int64_t)y - c->cam_y;
    for (uint32_t sy = 0; sy < im->h; sy++) {
        int64_t ty = y0 + sy;
        if (ty < c->clip_y0 || ty >= c->clip_y1) {
            continue;
        }
        const uint8_t *src = im->pix + (size_t)sy * im->w;
        moy_pixel *row = c->pix + (size_t)ty * (size_t)c->w;
        for (uint32_t sx = 0; sx < im->w; sx++) {
            int64_t tx = x0 + sx;
            uint8_t p = src[sx];
            if (tx < c->clip_x0 || tx >= c->clip_x1 || p >= MOY_PALETTE || c->palt[p]) {
                continue;
            }
            row[tx] = c->store[p];
        }
    }
}

int32_t mouse(int32_t *out, uint32_t cap) {
    int32_t st = g_f.in[MOY_FRAME_PTR];
    int32_t v[7] = { st ? g_f.in[MOY_FRAME_X] : 0, st ? g_f.in[MOY_FRAME_Y] : 0,
                     (st & 4) != 0, 0, 0, 0, 0 };
    for (uint32_t i = 0; i < 7 && (i + 1) * 4 <= cap; i++) {
        out[i] = v[i];
    }
    return st != 0;
}

// moybyte.prefs: the app's prefs rows (moy_prefs_get ...) over the grant the
// Player makes a game's run, their errno the code moy_app_wasm.c answers
// (moy_app_err.h's MOY_APP_ERRNO), each text NUL-ended for the row.
static int32_t prefs_code(void) {
    switch (errno) {
    case EACCES: return -4;
    case ERANGE: return -2;
    case ENOMEM: return -3;
    case ENODEV: return -5;
    case EIO: return -6;
    case EINVAL: return -7;
    case ENOENT: return -8;
    case EAGAIN: return -9;
    case EBADMSG: return -10;
    case EBUSY: return -11;
    default: return -7;
    }
}

static char *text_of(const char *p, uint32_t n, char *stack, size_t room) {
    char *t = n < room ? stack : malloc((size_t)n + 1u);
    if (t != NULL) {
        if (n > 0) {
            memcpy(t, p, n);
        }
        t[n] = 0;
    }
    return t;
}

int32_t game_prefs_get(const char *key, uint32_t key_n, char *json, uint32_t cap) {
    char kb[64];
    char *k = text_of(key, key_n, kb, sizeof kb);
    if (k == NULL) {
        return -3;
    }
    int32_t r = moy_prefs_get(k, cap > 0 ? json : NULL, cap);
    r = r < 0 ? prefs_code() : r;
    if (k != kb) {
        free(k);
    }
    return r;
}

int32_t game_prefs_set(const char *key, uint32_t key_n, const char *json, uint32_t json_n) {
    char kb[64], jb[256];
    char *k = text_of(key, key_n, kb, sizeof kb);
    char *j = text_of(json, json_n, jb, sizeof jb);
    int32_t r = -3;
    if (k != NULL && j != NULL) {
        r = moy_prefs_set(k, j);
        r = r < 0 ? prefs_code() : r;
    }
    if (k != NULL && k != kb) {
        free(k);
    }
    if (j != NULL && j != jb) {
        free(j);
    }
    return r;
}

int32_t game_prefs_clear(const char *key, uint32_t key_n) {
    char kb[64];
    char *k = text_of(key, key_n, kb, sizeof kb);
    if (k == NULL) {
        return -3;
    }
    int32_t r = moy_prefs_clear(k);
    r = r < 0 ? prefs_code() : r;
    if (k != kb) {
        free(k);
    }
    return r;
}

// -- the game ------------------------------------------------------------------------

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    int w = 0, h = 0;
    if (moy_screen(&w, &h) != 0) {
        return 2;
    }
    void *px = mmap(NULL, (size_t)w * (size_t)h, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (px == MAP_FAILED) {
        return 2;
    }
    moy_canvas_init(&g_screen, (moy_pixel *)px, w, h);
    moy_console_init(&g_con, &g_screen, NULL, NULL);
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    r_srand(&g_con, (uint32_t)ts.tv_nsec ^ (uint32_t)ts.tv_sec);
    load_data();
    if (init != NULL) {
        init();
    }
    while (moy_game_frame(&g_f, NULL)) {
        g_ms += g_f.ms;
        g_target = &g_screen;
        if (update != NULL) {
            update((float)g_f.ms / 1000.0f);
        }
        g_target = &g_screen;
        if (g_con.has_bg) {
            g_direct_on = 0;
            r_cls(&g_screen, g_con.bg);
        }
        if (draw != NULL) {
            draw();
        }
        g_target = &g_screen;
        pmem_hand();
        present(px);
    }
    pmem_hand();
    return 0;
}
