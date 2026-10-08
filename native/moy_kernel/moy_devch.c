// moy_devch: the dev channel's reader, the kernel's words, the gestures.
// moy_devch.h has the contract.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_devch.h"
#include "moy_loop.h"

enum { G_NONE, G_TAP, G_SWIPE, G_DRAG };

typedef struct {
    uint8_t kind;
    bool pressed;               // tap: the press is merged this frame
    int32_t i, n, x0, y0, x1, y1, step;
} gesture_t;

static struct {
    const moy_devch_word_t *words;
    int nwords;
    char *buf;                  // the line so far (allocated by the tier)
    uint32_t len;
    uint32_t budget;
    bool armed;
    bool over;                  // the line outgrew the buffer: drop to its end
    uint32_t rx, lines, dropped;
    gesture_t g;
    uint8_t *back;              // bytes handed back, taken before getc's
    uint32_t back_n, back_i;
} D;

#define BACK_CAP 1024

void moy_devch_unread(const uint8_t *bytes, size_t n) {
    if (D.back == NULL) {
        D.back = moy_loop_alloc(BACK_CAP);
        if (D.back == NULL) {
            return;
        }
    }
    // What is left of an earlier hand-back goes first.
    if (D.back_i) {
        memmove(D.back, D.back + D.back_i, D.back_n - D.back_i);
        D.back_n -= D.back_i;
        D.back_i = 0;
    }
    while (n-- && D.back_n < BACK_CAP) {
        D.back[D.back_n++] = *bytes++;
    }
}

static int next_byte(const moy_loop_ops_t *ops) {
    if (D.back_i < D.back_n) {
        int c = D.back[D.back_i++];
        if (D.back_i == D.back_n) {
            D.back_i = D.back_n = 0;
        }
        return c;
    }
    return ops->getc != NULL ? ops->getc() : -1;
}

void moy_devch_init(void) {
    D.len = 0;
    D.over = false;
    D.budget = MOY_DEVCH_BYTES_PER_FRAME;
    D.armed = true;
    D.g.kind = G_NONE;
}

void moy_devch_words(const moy_devch_word_t *words, int n) {
    D.words = words;
    D.nwords = n;
}

void moy_devch_set_armed(bool on) {
    D.armed = on;
}

bool moy_devch_armed(void) {
    return D.armed;
}

void moy_devch_set_budget(uint32_t n) {
    D.budget = n ? n : MOY_DEVCH_BYTES_PER_FRAME;
}

void moy_devch_stats(moy_devch_stats_t *out) {
    out->rx = D.rx;
    out->lines = D.lines;
    out->dropped = D.dropped;
    out->armed = D.armed;
}

static void point(int32_t x, int32_t y, bool down, bool edge) {
    const moy_loop_ops_t *ops = moy_loop_ops();
    if (ops != NULL && ops->point != NULL) {
        ops->point(x, y, down, edge);
    }
}

// -- the gestures -------------------------------------------------------------------

void moy_devch_tap(int32_t x, int32_t y) {
    point(x, y, true, true);
    D.g.kind = G_TAP;
    D.g.pressed = true;     // released by the frame after the press is merged
    D.g.x0 = x;
    D.g.y0 = y;
}

void moy_devch_swipe(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t frames) {
    D.g.kind = G_SWIPE;
    D.g.i = 0;
    D.g.n = frames < 2 ? 2 : frames;
    D.g.x0 = x0;
    D.g.y0 = y0;
    D.g.x1 = x1;
    D.g.y1 = y1;
}

void moy_devch_drag(int32_t cx, int32_t cy, int32_t frames, int32_t step) {
    D.g.kind = G_DRAG;
    D.g.i = 0;
    D.g.n = frames < 8 ? 8 : frames;
    D.g.step = step < 1 ? 1 : step;
    D.g.x0 = cx;
    D.g.y0 = cy;
}

bool moy_devch_gesture(void) {
    return D.g.kind != G_NONE;
}

// One sample a frame. Answers true while a gesture plays (activity).
static bool gestures(void) {
    gesture_t *g = &D.g;
    switch (g->kind) {
        case G_TAP:
            if (g->pressed) {
                g->pressed = false;
            } else {
                point(g->x0, g->y0, false, false);
                g->kind = G_NONE;
            }
            return true;
        case G_DRAG:
            if (g->i >= g->n) {
                point(g->x0, g->y0, false, false);
                g->kind = G_NONE;
                moy_loop_say("REMOTE drag done");
            } else {
                // A triangle wave around the grab point: continuous movement,
                // so the drag stays engaged and every frame is dirty.
                int32_t t = g->i % 40;
                int32_t tri = t < 20 ? t : 40 - t;
                int32_t off = g->i == 0 ? 0 : (tri - 10) * g->step;
                point(g->x0 + off, g->y0, true, g->i == 0);
                g->i++;
            }
            return true;
        case G_SWIPE:
            if (g->i > g->n) {
                g->kind = G_NONE;
                moy_loop_say("REMOTE swipe done");
            } else {
                // The press edge at the start, held interpolation, and the
                // release sample at the end point, fling velocity intact.
                int32_t i = g->i < g->n - 1 ? g->i : g->n - 1;
                int32_t x = g->x0 + (int32_t)((int64_t)(g->x1 - g->x0) * i / (g->n - 1));
                int32_t y = g->y0 + (int32_t)((int64_t)(g->y1 - g->y0) * i / (g->n - 1));
                point(x, y, g->i < g->n, g->i == 0);
                g->i++;
            }
            return true;
        default:
            return false;
    }
}

// -- the kernel's words ----------------------------------------------------------

static bool parse_u32(const char *s, uint32_t *out) {
    if (s == NULL || *s == 0) {
        return false;
    }
    uint32_t v = 0;
    for (; *s; s++) {
        if (*s < '0' || *s > '9' || v > 100000000u) {
            return false;
        }
        v = v * 10u + (uint32_t)(*s - '0');
    }
    *out = v;
    return true;
}

static const char *const RUNG_WORDS[MOY_IDLE_RUNGS] = {NULL, "dim", "saver", "blank", "sleep"};

static void say_power(void) {
    moy_idle_t *d = moy_loop_idle();
    moy_loop_say("REMOTE power timeout=%us asleep=%s dim=%us saver=%us state=%s",
                 (unsigned)moy_idle_get(d, MOY_IDLE_BLANK),
                 d->state >= MOY_IDLE_BLANK ? "True" : "False",
                 (unsigned)moy_idle_get(d, MOY_IDLE_DIM),
                 (unsigned)moy_idle_get(d, MOY_IDLE_SAVER), moy_idle_name(d->state));
}

// power [secs | off | on | dim|saver|blank <secs>]: the idle ladder. A bare
// number is the blank rung, as it always was; 0 is OFF.
static bool w_power(int argc, char **argv, const char *line) {
    (void)line;
    moy_idle_t *d = moy_loop_idle();
    uint32_t v;
    if (argc == 2 && strcmp(argv[1], "off") == 0) {
        moy_idle_blank(d);
        moy_loop_say("REMOTE power off");
        return true;
    }
    if (argc == 2 && strcmp(argv[1], "on") == 0) {
        moy_idle_wake(d);
    } else if (argc == 2 && parse_u32(argv[1], &v)) {
        moy_idle_set(d, MOY_IDLE_BLANK, v);
        moy_idle_wake(d);
    } else if (argc == 3 && parse_u32(argv[2], &v)) {
        int rung = 0;
        for (int r = 1; r < MOY_IDLE_RUNGS; r++) {
            if (strcmp(argv[1], RUNG_WORDS[r]) == 0) {
                rung = r;
            }
        }
        if (rung == 0 || rung == MOY_IDLE_SLEEP) {
            moy_loop_say("REMOTE power ? %s", line);
            return true;
        }
        moy_idle_set(d, rung, v);
        moy_idle_wake(d);
    } else if (argc != 1) {
        moy_loop_say("REMOTE power ? %s", line);
        return true;
    }
    say_power();
    return true;
}

// bl 0|1: the panel light, the ladder's model kept honest.
static bool w_bl(int argc, char **argv, const char *line) {
    (void)line;
    const moy_loop_ops_t *ops = moy_loop_ops();
    if (ops == NULL || ops->backlight == NULL) {
        moy_loop_say("REMOTE bl: no backlight control on this board");
        return true;
    }
    bool on = !(argc == 2 && strcmp(argv[1], "0") == 0);
    moy_idle_t *d = moy_loop_idle();
    if (on) {
        moy_idle_wake(d);
        ops->backlight(MOY_IDLE_LIGHT_FULL);
    } else {
        moy_idle_blank(d);
        ops->backlight(MOY_IDLE_LIGHT_OFF);
    }
    moy_loop_say("REMOTE bl %s", on ? "on" : "off");
    return true;
}

static const moy_devch_word_t KERNEL_WORDS[] = {
    {"power", w_power},
    {"bl", w_bl},
};

int moy_devch_kernel_words(const moy_devch_word_t **out) {
    *out = KERNEL_WORDS;
    return (int)(sizeof(KERNEL_WORDS) / sizeof(KERNEL_WORDS[0]));
}

// -- dispatch ---------------------------------------------------------------------

static bool dispatch(char *line, bool *quit) {
    // Trim: the reader keeps what arrived between newlines verbatim.
    while (*line == ' ' || *line == '\t') {
        line++;
    }
    size_t n = strlen(line);
    while (n && (line[n - 1] == ' ' || line[n - 1] == '\t')) {
        line[--n] = 0;
    }
    if (n == 0) {
        return false;
    }
    D.lines++;
    // The tokens, on a copy: the console's words receive the line whole.
    char tok[96];
    size_t tn = n < sizeof(tok) - 1 ? n : sizeof(tok) - 1;
    memcpy(tok, line, tn);
    tok[tn] = 0;
    char *argv[MOY_DEVCH_ARGS];
    int argc = 0;
    for (char *p = tok; *p && argc < MOY_DEVCH_ARGS;) {
        while (*p == ' ' || *p == '\t') {
            p++;
        }
        if (!*p) {
            break;
        }
        argv[argc++] = p;
        while (*p && *p != ' ' && *p != '\t') {
            p++;
        }
        if (*p) {
            *p++ = 0;
        }
    }
    if (argc == 0) {
        return false;
    }
    if (strcmp(argv[0], "quit") == 0) {
        moy_loop_say("REMOTE quit -> REPL");
        *quit = true;
        return true;
    }
    for (int i = 0; i < D.nwords; i++) {
        if (strcmp(argv[0], D.words[i].name) == 0 && D.words[i].fn(argc, argv, line)) {
            return true;
        }
    }
    const moy_devch_word_t *kw;
    int nk = moy_devch_kernel_words(&kw);
    for (int i = 0; i < nk; i++) {
        if (strcmp(argv[0], kw[i].name) == 0 && kw[i].fn(argc, argv, line)) {
            return true;
        }
    }
    int r = moy_loop_word(line);
    if (r == MOY_UP_ABSENT) {
        moy_loop_say("REMOTE ? %s", line);
    } else if (r > 0 && (r & 1)) {
        *quit = true;
    }
    return true;
}

bool moy_devch_line(const char *line, bool *quit) {
    char tmp[256];
    size_t n = strlen(line);
    if (n >= sizeof(tmp)) {
        n = sizeof(tmp) - 1;
    }
    memcpy(tmp, line, n);
    tmp[n] = 0;
    return dispatch(tmp, quit);
}

// Well-formed UTF-8 (a line the console's words can decode).
static bool utf8(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        unsigned char c = *p++;
        int more = c < 0x80 ? 0 : (c & 0xE0) == 0xC0 ? 1 : (c & 0xF0) == 0xE0 ? 2
                   : (c & 0xF8) == 0xF0 ? 3 : -1;
        if (more < 0 || (more == 1 && c < 0xC2)) {
            return false;
        }
        while (more--) {
            if ((*p++ & 0xC0) != 0x80) {
                return false;
            }
        }
    }
    return true;
}

bool moy_devch_poll(bool *quit) {
    bool ran = false;
    const moy_loop_ops_t *ops = moy_loop_ops();
    if (D.armed && ops != NULL && (ops->getc != NULL || D.back_n)) {
        if (D.buf == NULL) {
            D.buf = moy_loop_alloc(MOY_DEVCH_LINE_MAX + 1);
            if (D.buf == NULL) {
                D.armed = false;
                moy_loop_say("Moybyte serial channel unavailable: no memory for its line");
                return gestures();
            }
        }
        for (uint32_t k = 0; k < D.budget; k++) {
            int c = next_byte(ops);
            if (c < 0) {
                break;
            }
            D.rx++;
            if (c == '\n' || c == '\r') {
                bool over = D.over;
                D.over = false;
                D.buf[D.len] = 0;
                D.len = 0;
                if (over) {
                    continue;
                }
                // Bytes that are not text cost the line they land in.
                if (!utf8(D.buf)) {
                    D.dropped++;
                    continue;
                }
                if (dispatch(D.buf, quit)) {
                    ran = true;
                }
                if (*quit) {
                    return true;
                }
                continue;
            }
            if (D.over) {
                continue;
            }
            if (D.len >= MOY_DEVCH_LINE_MAX) {
                // Not a command: a byte source with no newline in it.
                D.dropped++;
                D.len = 0;
                D.over = true;
                continue;
            }
            D.buf[D.len++] = (char)c;
        }
        if (D.lines == 0 && D.rx >= MOY_DEVCH_NOISE_LIMIT) {
            D.armed = false;
            moy_loop_say("Moybyte serial channel DISARMED: %u bytes arrived and not one "
                         "complete command. Something is injecting into stdin -- most "
                         "likely UART0 (U0RXD/GPIO44 floats on the expansion header) "
                         "feeding the same ring buffer. Rebuild with "
                         "MICROPY_HW_ENABLE_UART_REPL (0) to take its ISR off it.",
                         (unsigned)D.rx);
        }
    }
    return gestures() || ran;
}
