// A program's frame loop (native/moy_elf/moy_os.h, A GAME): moy_game_frame
// waits on the event descriptor for the console's next frame message and
// reads its buttons, and an app's input words after them. A native game
// includes it through game.h; a native app drawing with the toolkit it links
// (moybyte/ui.h) through ui.h.

#ifndef MOYBYTE_FRAME_H
#define MOYBYTE_FRAME_H

#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

#include "moybyte/os.h"

// A button's bit in a frame's masks (moy.h's moy_button order).
#define MOY_BIT(b) (1u << (b))

// An app's input words (moy_os.h's MOY_OS_FRAME_IN_*): the pointer's x and
// y on the screen, its state (0 none), the key typed this frame (0 none),
// whether the screen is a window's body and whether the console's RGB565
// words are byte-swapped. A game's frames carry none: 0.
#define MOY_FRAME_IN 6
#define MOY_FRAME_X 0
#define MOY_FRAME_Y 1
#define MOY_FRAME_PTR 2
#define MOY_FRAME_KEY 3
#define MOY_FRAME_WINDOW 4
#define MOY_FRAME_SWAP 5

// A frame: the buttons held and those pressed since the last frame, a bit
// each in moy_button's order (MOY_BIT(MOY_BTN_A)), its milliseconds, and
// the input words.
typedef struct {
    uint32_t held;
    uint32_t pressed;
    uint32_t ms;
    int32_t in[MOY_FRAME_IN];
} moy_frame_t;

// The next number at or after `s` into *out (0 for none): the text after it.
static inline const char *moy_game_num_(const char *s, int32_t *out) {
    int32_t v = 0, neg = 0;
    while (*s != 0 && *s != '-' && (*s < '0' || *s > '9')) {
        s++;
    }
    if (*s == '-') {
        neg = 1;
        s++;
    }
    while (*s >= '0' && *s <= '9') {
        v = (int32_t)((uint32_t)v * 10u + (uint32_t)(*s++ - '0'));
    }
    *out = neg ? -v : v;
    return s;
}

// The console's next frame into *f: 1, or 0 once the program is stopped.
// Every other message read on the way goes to `other` (NULL: dropped).
// Frames that queued while the program was busy are read as one: the last
// one's held buttons and input words, every one's presses and milliseconds.
// A frame carrying a key or a pointer state ends the read, so a later one
// never writes over its input words.
static inline int moy_game_frame(moy_frame_t *f,
                                 void (*other)(const moy_evq_ev_t *ev, const char *args)) {
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    int got = 0;
    uint32_t pressed = 0, ms = 0;
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
                f->ms = ms;
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
            int32_t v[3 + MOY_FRAME_IN];
            for (int i = 0; i < 3 + MOY_FRAME_IN; i++) {
                args = moy_game_num_(args, &v[i]);
            }
            f->held = (uint32_t)v[0];
            pressed |= (uint32_t)v[1];
            ms += (uint32_t)v[2];
            memcpy(f->in, v + 3, sizeof f->in);
            got = 1;
            if (f->in[MOY_FRAME_KEY] != 0 || f->in[MOY_FRAME_PTR] != 0) {
                f->pressed = pressed;
                f->ms = ms;
                return 1;
            }
        } else if (other != NULL) {
            other(&m.ev, args);
        }
    }
}

#endif // MOYBYTE_FRAME_H
