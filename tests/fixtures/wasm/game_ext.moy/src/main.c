// Game Ext Wasm: a compiled GAME in C reaching what a Python or Lua game
// holds by name (docs/moy_cart_api.md, "A compiled cart's Moybyte verbs"):
// its badges and a device handle through moybyte.app, under the grant its
// run holds as an app's does, and its scenes, its paint image and the
// mouse through Moybyte's own extensions. Built by tools/wasm_cart.py.
//
// Its findings are its pmem slots, which every tier's `state` reads:
//    0  award("started")'s answer        1  progress("steps", 3)'s answer
//    2  the active scene's actor count   3  its first actor's x, 4 its y
//    5  its first actor's tile           6  1 when its tag is "player"
//    7  scene_name(0)'s length           8  1 when scene_name(1) is "second"
//    9  load_scene("second")'s count    10  the active scene's count after it
//   11  load_scene("nope")'s answer     12  the third actor's tag ("7"), 1 when so
//   13  main's first actor's flags' length, 14 its second's (0: none)
//   15  1 when image("dot") is a handle  16  its image_size
//   17  image("nope")'s answer          18  dev_open("pins")'s answer (1 a handle)
//   19  frames a pointer was down       20  presses (mouse's left edge)
//   21  the last x down                 22  the last y down
//   23  frames run
// It draws main's actors as 8x8 squares (colour 8), the paint image at
// (100, 100) and, while a pointer is down, a square at it (colour 11).
// tests/test_wasm_game_ext.py and tests/test_web_wasm_e2e.py drive it.

#include <stdint.h>

#include "moybyte/cart.h"

#define APP(name) __attribute__((import_module("moybyte.app"), import_name(#name)))
#define MOY(name) __attribute__((import_module("moy"), import_name(#name)))
#define EXPORT(name) __attribute__((export_name(#name)))

int32_t moybyte_award(const char *id, uint32_t id_n) APP(moybyte_award);
int32_t moybyte_progress(const char *id, uint32_t id_n, int32_t v) APP(moybyte_progress);
int32_t moybyte_dev_open(const char *name, uint32_t name_n, const char *cfg,
                         uint32_t cfg_n) APP(moybyte_dev_open);
void cls(int32_t c) MOY(cls);
void rect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t c) MOY(rect);
int32_t pmem(int32_t slot, int32_t v, int32_t write) MOY(pmem);

#define S(s) s, (uint32_t)(sizeof(s) - 1)

static moy_actor_t main_rows[8];
static int32_t main_n;
static int32_t dot;
static int32_t ptr[7];
static int32_t down_frames, presses, frames;

static void put(int32_t slot, int32_t v) {
    pmem(slot, v, 1);
}

static int same(const char *a, const char *b, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) {
        if (a[i] != b[i]) {
            return 0;
        }
    }
    return 1;
}

EXPORT(_init) void init(void) {
    char buf[64];
    put(0, moybyte_award(S("started")));
    put(1, moybyte_progress(S("steps"), 3));
    main_n = scene(0, 0, main_rows, sizeof main_rows);
    put(2, main_n);
    put(3, main_rows[0].x);
    put(4, main_rows[0].y);
    put(5, main_rows[0].tile);
    put(6, same(main_rows[0].tag, "player", 7));
    put(7, scene_name(0, buf, sizeof buf));
    put(8, scene_name(1, buf, sizeof buf) == 6 && same(buf, "second", 6));
    put(9, load_scene(S("second")));
    moy_actor_t second[4];
    put(10, scene(0, 0, second, sizeof second));
    put(11, load_scene(S("nope")));
    put(12, same(second[2].tag, "7", 2));
    put(13, scene_flags(S("main"), 0, buf, sizeof buf));
    put(14, scene_flags(S("main"), 1, buf, sizeof buf));
    dot = image(S("dot"));
    put(15, dot > 0);
    put(16, image_size(dot));
    put(17, image(S("nope")));
    int32_t pins = moybyte_dev_open(S("pins"), S("gpio20=1"));
    put(18, pins > 0 ? 1 : pins);
}

EXPORT(_update) void update(float dt) {
    (void)dt;
    frames++;
    if (mouse(ptr, sizeof ptr)) {
        down_frames++;
        put(21, ptr[0]);
        put(22, ptr[1]);
    }
    presses += ptr[2];
    put(19, down_frames);
    put(20, presses);
    put(23, frames);
}

EXPORT(_draw) void draw(void) {
    cls(1);
    for (int32_t i = 0; i < main_n && i < 8; i++) {
        rect(main_rows[i].x, main_rows[i].y, 8, 8, 8);
    }
    image_spr(dot, 100, 100);
    if (ptr[0] || ptr[1]) {
        rect(ptr[0] - 2, ptr[1] - 2, 5, 5, 11);
    }
}
