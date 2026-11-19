// Dual C: one C source built to main.wasm and to a native module per chip
// (sdk/include/moybyte/cart.h). Its frame draws with every kind of verb the
// adapter serves -- shapes, text, the sheet, the map, a layer, a paint image,
// a scene's actors, the palette, the camera, the clip and a fill pattern --
// from the same data on both targets, so tests/test_compiled_cart.py holds the
// two frames equal. Right moves a square by cfg("step") pixels a frame, a
// pointer down draws one at it, and B quits.
//
// Its cfg("mode") picks what else it does, each held equal on both targets
// too: 0 (none) also runs par's items and a file's round trip, 1 counts its
// runs in pmem, a pref and a written file, 2 blits a frame through a
// palette once and draws over it each frame, 3 blits RGB565, 4 blits indices
// with no palette, and 5 plays the audio verbs at its second update and
// keeps the stream full at each.

#include <stdint.h>

#include "moybyte/cart.h"

static moy_actor_t actors[8];
static int32_t n_actors, n_second, n_names, lay, dot, dot_size;
static int32_t x = 40, step, pressed;
static char word[8];
static int32_t word_n;
static int32_t mode, frames;
static int32_t par_out[8];
static uint8_t par_stacks[8 * 1024] __attribute__((aligned(16)));
static int32_t files_got[6];
static int32_t kept[3];
static uint8_t frame8[320 * 240];
static uint16_t frame16[320 * 240];
static uint8_t pal[768];
static int16_t pcm[256];

void par_item(int32_t i, int32_t arg) {
    int32_t v = 0;
    for (int32_t k = 0; k <= i * 50; k++) {
        v += (k * k + arg) % 97;
    }
    par_out[i] = v;
}

static int32_t digits(const char *b, int32_t n) {
    int32_t v = 0;
    for (int32_t i = 0; i < n && b[i] >= '0' && b[i] <= '9'; i++) {
        v = v * 10 + (b[i] - '0');
    }
    return v;
}

static int32_t text(int32_t v, char *b) {
    char t[12];
    int32_t k = 0, n = 0;
    do {
        t[k++] = (char)('0' + v % 10);
        v /= 10;
    } while (v > 0);
    while (k > 0) {
        b[n++] = t[--k];
    }
    return n;
}

// A file written, read back, listed and erased: the same every run.
static void round_trip(void) {
    char got[16], name[32];
    files_got[0] = moy_write("notes/a.txt", 11, "hello", 5);
    files_got[1] = moy_read("notes/a.txt", 11, 1, got, sizeof got);
    files_got[2] = files_got[1] > 0 ? got[0] : 0;
    files_got[3] = moy_list("notes/", 6, 0, name, sizeof name);
    files_got[4] = moy_erase("notes/a.txt", 11);
    files_got[5] = moy_read("map.moymap", 10, 0, 0, 0) > 0;
}

// One more run, counted in pmem slot 0, the pref "runs" and notes/runs.txt.
static void count_runs(void) {
    char b[16];
    kept[0] = moy_pmem(0, 0, 0) + 1;
    moy_pmem(0, kept[0], 1);
    int32_t n = game_prefs_get("runs", 4, b, sizeof b);
    kept[1] = (n > 0 ? digits(b, n) : 0) + 1;
    game_prefs_set("runs", 4, b, (uint32_t)text(kept[1], b));
    n = moy_read("notes/runs.txt", 14, 0, b, sizeof b);
    kept[2] = (n > 0 ? digits(b, n) : 0) + 1;
    moy_write("notes/runs.txt", 14, b, text(kept[2], b));
}

static void sound(void) {
    moy_volume(5);
    moy_sfx(1, -1);
    moy_sfx(2, 3);
    moy_music(0, 1);
    moy_beep(440.0f, 0.25f);
    moy_sound_stop(2);
    moy_music_stop();
    moy_sound_stop(-1);
    for (int i = 0; i < 256; i++) {
        pcm[i] = (int16_t)(i * 97);
    }
}

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
    n = moy_cfg("mode", 4, b, sizeof b);
    mode = n > 0 ? b[0] - '0' : 0;
    if (mode == 0) {
        round_trip();
    } else if (mode == 1) {
        count_runs();
    }
    for (int i = 0; i < 320 * 240; i++) {
        int x = i % 320, y = i / 320;
        frame8[i] = (uint8_t)((x / 4) ^ (y / 3));
        frame16[i] = (uint16_t)((x * 31 / 320) << 11 | (y * 63 / 240) << 5 | ((x + y) & 31));
    }
    for (int i = 0; i < 256; i++) {
        pal[i * 3] = (uint8_t)i;
        pal[i * 3 + 1] = (uint8_t)(255 - i);
        pal[i * 3 + 2] = (uint8_t)(i * 7);
    }
    lay = moy_make_layer(64, 48);
    moy_target(lay);
    moy_cls(2);
    moy_circ(32, 24, 20, 9);
    image_spr(dot, 4, 4);
    moy_target(0);
}

void update(float dt) {
    (void)dt;
    if (mode == 5) {
        if (frames++ == 1) {
            sound();
        }
        for (int32_t room = moy_snd(0, 0); room >= 256; room -= 256) {
            moy_snd(pcm, 256);      // the stream kept full: it never runs dry
        }
    }
    if (moy_btn(MOY_RIGHT, 0)) {
        x += step;
    }
    if (moy_btnp(MOY_B, 0)) {
        moy_quit();
    }
    int32_t t[4];
    pressed = moy_touch(t) ? t[0] << 16 | t[1] : -1;
}

// Mode 2's frame once and drawn over after, 3's and 4's each frame.
static int blitted(void) {
    if (mode == 2) {
        if (frames++ == 0) {
            moy_blit(frame8, pal);
        }
    } else if (mode == 3) {
        moy_blit565(frame16);
    } else if (mode == 4) {
        moy_blit(frame8, 0);
    } else {
        return 0;
    }
    moy_rect(20, 20, 40, 30, 8);
    moy_circ(160, 120, 24, 12);
    moy_print("blit", 4, 100, 200, 7);
    return 1;
}

void draw(void) {
    if (blitted()) {
        return;
    }
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
    if (mode == 0) {
        moy_par(8, 5, par_stacks, 1024);
        for (int i = 0; i < 8; i++) {
            num(par_out[i], 8 + i * 38, 226, 9);
        }
        for (int i = 0; i < 6; i++) {
            num(files_got[i], 230 + (i % 3) * 28, 8 + (i / 3) * 10, 10);
        }
    } else if (mode == 1) {
        for (int i = 0; i < 3; i++) {
            num(kept[i], 230 + i * 28, 30, 10);
        }
    }
}
