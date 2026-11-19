// Dual C: one C source built to main.wasm and to a native module per chip
// (sdk/include/moybyte/cart.h). Its frame draws with every kind of verb the
// adapter serves -- shapes, text, the sheet, the map, a layer, a paint image,
// a scene's actors, the palette, the camera, the clip and a fill pattern --
// from the same data on both targets, so tests/test_compiled_cart.py holds the
// two frames equal. Right moves a square by cfg("step") pixels a frame, a
// pointer down draws one at it, and B quits.

#include <stdint.h>

#include "moybyte/cart.h"

static moy_actor_t actors[8];
static int32_t n_actors, n_second, n_names, lay, dot, dot_size;
static int32_t x = 40, step, pressed;
static char word[8];
static int32_t word_n;

static void num(int32_t v, int32_t x0, int32_t y0, int32_t c) {
    char b[12];
    int n = 0;
    if (v < 0) {
        b[n++] = '-';
        v = -v;
    }
    char t[12];
    int k = 0;
    do {
        t[k++] = (char)('0' + v % 10);
        v /= 10;
    } while (v > 0);
    while (k > 0) {
        b[n++] = t[--k];
    }
    moy_print(b, n, x0, y0, c);
}

void init(void) {
    n_actors = scene(0, 0, actors, sizeof actors);
    n_second = scene("second", 6, 0, 0);
    char nm[16];
    while (scene_name(n_names, nm, sizeof nm) >= 0) {
        n_names++;
    }
    dot = image("dot", 3);
    dot_size = image_size(dot);
    char b[8];
    int32_t n = moy_cfg("step", 4, b, sizeof b);
    step = n > 0 ? b[0] - '0' : 1;
    word_n = moy_cfg("word", 4, word, sizeof word);
    lay = moy_make_layer(64, 48);
    moy_target(lay);
    moy_cls(2);
    moy_circ(32, 24, 20, 9);
    image_spr(dot, 4, 4);
    moy_target(0);
}

void update(float dt) {
    (void)dt;
    if (moy_btn(MOY_RIGHT, 0)) {
        x += step;
    }
    if (moy_btnp(MOY_B, 0)) {
        moy_quit();
    }
    int32_t t[4];
    pressed = moy_touch(t) ? t[0] << 16 | t[1] : -1;
}

void draw(void) {
    moy_cls(1);
    moy_map(0, 0, 4, 2, 200, 8, -1, 2, 0);
    for (int i = 0; i < 4; i++) {
        moy_spr(i, 8 + i * 10, 8, 0, 1, i & 3);
    }
    moy_sspr(0, 0, 8, 8, 60, 8, 16, 16, -1, 0);
    moy_draw_layer(lay, -100, -60);
    for (int i = 0; i < n_actors && i < 8; i++) {
        moy_rect(actors[i].x, actors[i].y, 8, 8, 8);
    }
    image_spr(dot, 150, 100);
    num(n_actors, 8, 200, 7);
    num(n_second, 40, 200, 7);
    num(n_names, 72, 200, 7);
    num(dot_size, 104, 200, 7);
    if (word_n > 0) {
        moy_print(word, word_n < 8 ? word_n : 8, 8, 210, 11);
    }
    moy_pal(8, 12, 0);
    moy_rect(x, 120, 10, 10, 8);
    moy_pal(-1, 0, 0);
    int32_t prev[2];
    moy_camera(-5, -5, prev);
    moy_rectb(250, 150, 20, 20, 10);
    moy_camera(0, 0, 0);
    moy_clip(0, 0, 300, 230);
    moy_circb(290, 220, 14, 14);
    moy_clip(0, 0, 320, 240);
    moy_fillp(0x5A5A, 3);
    moy_oval(200, 180, 40, 20, 13);
    moy_fillp(0, -1);
    moy_line(0, 239, 319, 100, 6);
    moy_tri(100, 230, 130, 170, 160, 230, 4);
    moy_trib(170, 230, 190, 190, 210, 230, 15);
    moy_pix(5, 5, 7);
    num(moy_pix(5, 5, -1), 136, 200, 7);
    num(moy_fget(0, -1), 168, 200, 7);
    num(moy_mget(1, 0), 184, 200, 7);
    if (pressed >= 0) {
        moy_rect(pressed >> 16, pressed & 0xFFFF, 6, 6, 11);
    }
}
