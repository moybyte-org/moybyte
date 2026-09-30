/* moy_cart.h -- the WebAssembly binding's imports, for a C or C++ cart.
 *
 * SPEC.md 16 is the contract and wasm-imports.json is its import table; this
 * file is that table as C. Every row is here, in the table's order, as
 * `moy_<name>`, imported from module "moy" under the row's own name, at the
 * row's exact wasm type. libmoy/test/wasm_table_check.py holds the two equal,
 * so a row the table gains is a declaration here in the same change.
 *
 * A cart includes this, defines its hooks and links for wasm32:
 *
 *     #include "moy_cart.h"
 *     MOY_EXPORT("_init")   void init(void) { }
 *     MOY_EXPORT("_update") void update(float dt) { }
 *     MOY_EXPORT("_draw")   void draw(void) { moy_cls(1); }
 *
 * A cart imports only what it calls: an import the linker never sees used is
 * not in the module. How each Lua verb became one wasm function -- optional
 * arguments passed explicitly, a negative sentinel for a narrower form, several
 * results written at an out pointer, strings as bytes and a length -- is
 * SPEC.md 16.4; each declaration's comment gives its row's rule.
 *
 * Pointers are offsets into the cart's own linear memory; a range that leaves
 * it traps (SPEC.md 16.8). A boolean is 1 or 0.
 *
 * MIT licensed, like the rest of this repository. */
#ifndef MOY_CART_H
#define MOY_CART_H

#include <stdint.h>

#define MOY_IMPORT(name) __attribute__((import_module("moy"), import_name(#name)))
#define MOY_EXPORT(name) __attribute__((export_name(name)))

/* btn and btnp's buttons, SPEC.md 7.3's order. */
enum { MOY_LEFT, MOY_RIGHT, MOY_UP, MOY_DOWN, MOY_A, MOY_B, MOY_RUN };

#ifdef __cplusplus
extern "C" {
#endif

/* -- drawing (SPEC.md 6) -------------------------------------------------- */

MOY_IMPORT(cls) void moy_cls(int32_t c);                       /* Lua's default c is 0 */
MOY_IMPORT(background) void moy_background(int32_t c);
MOY_IMPORT(view) void moy_view(int32_t w, int32_t h);           /* Lua's defaults: W, H */
/* c >= 0 writes and returns 0; c < 0 reads the index at x, y (0 off the canvas). */
MOY_IMPORT(pix) int32_t moy_pix(int32_t x, int32_t y, int32_t c);
MOY_IMPORT(line) void moy_line(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t c);
MOY_IMPORT(rect) void moy_rect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c);
MOY_IMPORT(rectb) void moy_rectb(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c);
MOY_IMPORT(circ) void moy_circ(int32_t cx, int32_t cy, int32_t r, int32_t c);
MOY_IMPORT(circb) void moy_circb(int32_t cx, int32_t cy, int32_t r, int32_t c);
MOY_IMPORT(oval) void moy_oval(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c);
MOY_IMPORT(ovalb) void moy_ovalb(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c);
/* len bytes at s, one byte per 8-pixel cell; a cart formats its own numbers. */
MOY_IMPORT(print) void moy_print(const char *s, int32_t len, int32_t x, int32_t y, int32_t c);
/* camera() is moy_camera(0, 0, 0); the previous offset is written at out as
 * two int32 unless out is 0. */
MOY_IMPORT(camera) void moy_camera(int32_t x, int32_t y, int32_t *out);
MOY_IMPORT(clip) void moy_clip(int32_t x, int32_t y, int32_t w, int32_t h); /* clip() is (0, 0, W, H) */
/* p 1 is the screen palette; c0 < 0 is pal() and resets both palettes. */
MOY_IMPORT(pal) void moy_pal(int32_t c0, int32_t c1, int32_t p);
MOY_IMPORT(palt) void moy_palt(int32_t c, int32_t on);         /* c < 0 is palt(): reset */
/* fillp() is moy_fillp(0, -1); c < 0 leaves the holes untouched. */
MOY_IMPORT(fillp) void moy_fillp(int32_t p, int32_t c);
/* A handle, or 0 where the host declined. A layer lives until the cart ends. */
MOY_IMPORT(make_layer) int32_t moy_make_layer(int32_t w, int32_t h);
MOY_IMPORT(draw_layer) void moy_draw_layer(int32_t layer, int32_t cx, int32_t cy);

/* -- the 3D verbs (SPEC.md 6.1) ------------------------------------------ */

MOY_IMPORT(tri) void moy_tri(int32_t x1, int32_t y1, int32_t x2, int32_t y2, int32_t x3,
                             int32_t y3, int32_t c);
MOY_IMPORT(trib) void moy_trib(int32_t x1, int32_t y1, int32_t x2, int32_t y2, int32_t x3,
                               int32_t y3, int32_t c);
/* u, v, du, dv are 16.16 fixed point; Lua's default colorkey is -1. */
MOY_IMPORT(tline) void moy_tline(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t u,
                                 int32_t v, int32_t du, int32_t dv, int32_t colorkey);

/* -- sprites, map, input (SPEC.md 7) ------------------------------------- */

/* Lua's defaults: colorkey -1, scale 1, flip 0. */
MOY_IMPORT(spr) void moy_spr(int32_t n, int32_t x, int32_t y, int32_t colorkey, int32_t scale,
                             int32_t flip);
/* Lua's defaults: dw = sw, dh = sh, colorkey -1, flip 0. */
MOY_IMPORT(sspr) void moy_sspr(int32_t sx, int32_t sy, int32_t sw, int32_t sh, int32_t dx,
                               int32_t dy, int32_t dw, int32_t dh, int32_t colorkey,
                               int32_t flip);
MOY_IMPORT(sget) int32_t moy_sget(int32_t x, int32_t y);
MOY_IMPORT(sset) void moy_sset(int32_t x, int32_t y, int32_t c);
/* b < 0 returns the flag byte; otherwise 1 or 0 for bit b. */
MOY_IMPORT(fget) int32_t moy_fget(int32_t n, int32_t b);
/* b < 0 writes v as the byte; otherwise sets bit b when v is non-zero. */
MOY_IMPORT(fset) void moy_fset(int32_t n, int32_t b, int32_t v);
/* w and h are passed (Lua defaults them to the map's edge); Lua's other
 * defaults: colorkey -1, scale 1, layers 0. */
MOY_IMPORT(map) void moy_map(int32_t mx, int32_t my, int32_t w, int32_t h, int32_t sx,
                             int32_t sy, int32_t colorkey, int32_t scale, int32_t layers);
MOY_IMPORT(mget) int32_t moy_mget(int32_t x, int32_t y);        /* -1: empty or out of range */
MOY_IMPORT(mset) void moy_mset(int32_t x, int32_t y, int32_t tile);
/* b is MOY_LEFT .. MOY_RUN, where Lua passes the name; 1 or 0. */
MOY_IMPORT(btn) int32_t moy_btn(int32_t b, int32_t player);
MOY_IMPORT(btnp) int32_t moy_btnp(int32_t b, int32_t player);
MOY_IMPORT(players) int32_t moy_players(void);                   /* at least 1 */
/* 0 with no pointer; otherwise 1, with x, y, tapped, held written at out as
 * four int32 unless out is 0. */
MOY_IMPORT(touch) int32_t moy_touch(int32_t *out);
/* code < 0 returns the last typed code (0 for none); otherwise 1 or 0. */
MOY_IMPORT(key) int32_t moy_key(int32_t code);
MOY_IMPORT(keyp) int32_t moy_keyp(int32_t code);
MOY_IMPORT(textmode) void moy_textmode(int32_t on);

/* -- audio (SPEC.md 8) ---------------------------------------------------- */

MOY_IMPORT(sfx) void moy_sfx(int32_t n, int32_t chan);           /* chan -1: round-robin */
MOY_IMPORT(music) void moy_music(int32_t track, int32_t loop);   /* Lua's default loop is 1 */
MOY_IMPORT(music_stop) void moy_music_stop(void);
MOY_IMPORT(sound_stop) void moy_sound_stop(int32_t chan);        /* chan < 0: every channel */
MOY_IMPORT(volume) void moy_volume(int32_t level);
MOY_IMPORT(beep) void moy_beep(float freq, float dur);           /* Lua's default dur is 0.15 */

/* -- state and utility (SPEC.md 9) ---------------------------------------- */

MOY_IMPORT(time) int32_t moy_time(void);          /* ms since the cart started: the clock */
/* write 0 reads the slot (v ignored); write 1 stores v and returns 0. */
MOY_IMPORT(pmem) int32_t moy_pmem(int32_t slot, int32_t v, int32_t write);
/* config.json's value for the key, as text, into dst (at most dst_len bytes);
 * its whole length, or -1 for an absent key. */
MOY_IMPORT(cfg) int32_t moy_cfg(const char *key, int32_t key_len, char *dst, int32_t dst_len);
MOY_IMPORT(rnd) float moy_rnd(float n);                            /* Lua's default n is 1.0 */
MOY_IMPORT(srand) void moy_srand(int32_t seed);
MOY_IMPORT(flr) int32_t moy_flr(float x);
MOY_IMPORT(quit) void moy_quit(void);             /* does not return */

/* -- the binding's own (SPEC.md 16) --------------------------------------- */

/* The drawing verbs a Lua layer answers draw into layer from here on; 0 is
 * the screen, which the target is whenever the host calls a hook. */
MOY_IMPORT(target) void moy_target(int32_t layer);
/* The whole frame: W x H palette indices, row-major. pal is 0 for the cart's
 * palette, or 768 bytes of RGB for this frame. Only inside _draw, at most one
 * blit or blit565 per _draw; the frame and palette stay as handed over until
 * _draw returns. */
MOY_IMPORT(blit) void moy_blit(const uint8_t *frame, const uint8_t *pal);
/* The whole frame: W x H RGB565 words, little-endian, row-major. */
MOY_IMPORT(blit565) void moy_blit565(const uint16_t *frame);
/* Up to len bytes of the cart's own file name from offset into dst, and how
 * many; with len 0, how many remain from offset. Absent or outside the
 * cart's folder reads 0. */
MOY_IMPORT(read) int32_t moy_read(const char *name, int32_t name_len, int32_t offset, void *dst,
                                  int32_t len);
/* n frames of signed 16-bit mono at 22050 Hz: queues what fits and returns
 * how many; with n 0, the room. The host holds 2048 frames. */
MOY_IMPORT(snd) int32_t moy_snd(const int16_t *pcm, int32_t n);
/* The cart's _par(i, arg) export for every i in [0, n), across the host's
 * cores, returning when all have; item i's stack is the size bytes below
 * stacks + (i + 1) * size (16-aligned). An item calls no import. The cart
 * exports _par and __stack_pointer (-Wl,--export=__stack_pointer). */
MOY_IMPORT(par) void moy_par(int32_t n, int32_t arg, void *stacks, int32_t size);

#ifdef __cplusplus
}
#endif

#endif /* MOY_CART_H */
