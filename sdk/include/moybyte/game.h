// A native game's frame loop and its screen (native/moy_elf/moy_os.h, A
// GAME; sdk/README.md). A game is main(argc, argv) over its event descriptor:
// moy_game_frame waits for the console's next frame, the module draws its
// screen, palette indices row by row, with libmoy's raster (moy.h: the verbs
// and pixels the Lua and wasm tiers draw with, linked into the module by the
// SDK) or by writing indices itself, and moy_present hands the whole screen
// over. main returns once moy_game_frame answers 0, the game's stop.
//
//     moy_canvas c;
//     uint8_t *px = moy_game_screen(&c);
//     moy_frame_t f;
//     while (px != NULL && moy_game_frame(&f, NULL)) {
//         moy_cls(&c, 1);
//         if (f.held & MOY_BIT(MOY_BTN_A)) { ... }
//         px[0] = 8;
//         moy_present(px, (size_t)c.w * (size_t)c.h);
//     }
//     return 0;

#ifndef MOYBYTE_GAME_H
#define MOYBYTE_GAME_H

#include <stdint.h>
#include <sys/mman.h>

#include "moy.h"
#include "moybyte/frame.h"
#include "moybyte/os.h"

// A screen-sized canvas over a mapping of its own: the buffer, or NULL for
// a process with no screen (a command or a service) or no memory for one.
static inline uint8_t *moy_game_screen(moy_canvas *c) {
    int w = 0, h = 0;
    if (moy_screen(&w, &h) != 0) {
        return NULL;
    }
    void *px = mmap(NULL, (size_t)w * (size_t)h, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (px == MAP_FAILED) {
        return NULL;
    }
    moy_canvas_init(c, (uint8_t *)px, w, h);
    return (uint8_t *)px;
}

#endif // MOYBYTE_GAME_H
