// game: a native game (native/moy_elf/moy_os.h, A GAME). Each frame it
// clears its screen to 1, fills an 8x8 square of 8 with libmoy's rectfill at
// its position, which the arrows move 4 pixels a frame, and writes index 11
// into its screen's last pixel itself, then presents the screen. A press of
// A traps (a store through address 0 where the module runs isolated, on a
// P4; elsewhere it ends as that trap ends a process, 139, since a host or an
// S3 module's fault is the whole console's), B returns 0 (it quits), and the
// stop returns 0.
#include <stdint.h>
#include <unistd.h>

#include "moybyte/game.h"

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
    moy_canvas c;
    uint8_t *px = moy_game_screen(&c);
    if (px == NULL) {
        return 2;
    }
    int x = 16, y = 16;
    moy_frame_t f;
    while (moy_game_frame(&f, NULL)) {
        if (f.pressed & MOY_BIT(MOY_BTN_A)) {
#if defined(__riscv)
            *(volatile int *)0 = 1;
#endif
            _exit(128 + 11);
        }
        if (f.pressed & MOY_BIT(MOY_BTN_B)) {
            return 0;
        }
        x += (f.held & MOY_BIT(MOY_BTN_RIGHT) ? 4 : 0) - (f.held & MOY_BIT(MOY_BTN_LEFT) ? 4 : 0);
        y += (f.held & MOY_BIT(MOY_BTN_DOWN) ? 4 : 0) - (f.held & MOY_BIT(MOY_BTN_UP) ? 4 : 0);
        moy_cls(&c, 1);
        moy_rect(&c, x, y, 8, 8, 8);
        px[(size_t)c.w * (size_t)c.h - 1] = 11;
        moy_present(px, (size_t)c.w * (size_t)c.h);
    }
    return 0;
}
