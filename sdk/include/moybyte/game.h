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

#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "moy.h"
#include "moybyte/os.h"

#define MOY_BIT(b) (1u << (b))

// A frame: the buttons held and those pressed since the last frame, a bit
// each in moy_button's order (MOY_BIT(MOY_BTN_A)), and its milliseconds.
typedef struct {
    uint32_t held;
    uint32_t pressed;
    uint32_t ms;
} moy_frame_t;

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

static inline const char *moy_game_num_(const char *s, uint32_t *out) {
    uint32_t v = 0;
    while (*s != 0 && (*s < '0' || *s > '9')) {
        s++;
    }
    while (*s >= '0' && *s <= '9') {
        v = v * 10u + (uint32_t)(*s++ - '0');
    }
    *out = v;
    return s;
}

// The console's next frame into *f: 1, or 0 once the game is stopped. Every
// other message read on the way goes to `other` (NULL: dropped). Frames that
// queued while the game was busy are read as one: the last one's held
// buttons, every one's presses.
static inline int moy_game_frame(moy_frame_t *f,
                                 void (*other)(const moy_evq_ev_t *ev, const char *args)) {
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    int got = 0;
    uint32_t pressed = 0;
    for (;;) {
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        if (poll(&p, 1, got ? 0 : -1) < 0) {
            return 0;
        }
        if (p.revents & POLLHUP) {
            return 0;
        }
        if (!(p.revents & POLLIN)) {
            if (got) {
                f->pressed = pressed;
                return 1;
            }
            continue;
        }
        ssize_t n = read(MOY_EVQ_FD, m.b, sizeof m.b - 1);
        if (n == 0) {
            return 0;
        }
        if (n < (ssize_t)sizeof m.ev) {
            continue;
        }
        m.b[n] = 0;
        const char *args = m.b + sizeof m.ev;
        if (m.ev.from <= 1u && strcmp(m.ev.name, "frame") == 0) {
            uint32_t p2 = 0;
            args = moy_game_num_(args, &f->held);
            args = moy_game_num_(args, &p2);
            moy_game_num_(args, &f->ms);
            pressed |= p2;
            got = 1;
        } else if (other != NULL) {
            other(&m.ev, args);
        }
    }
}

#endif // MOYBYTE_GAME_H
