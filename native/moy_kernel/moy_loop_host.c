// moy_loop_host: the loop's TRACE tier -- a fake clock, scripted inputs, a
// byte queue for the dev channel and a log of every stage the loop drives,
// so the host (ctypes) and the desktop MicroPython drive the one loop the
// boards run and pin what it did (tests/test_semantic_traces.py's loop trace).
//
// Nothing here touches a VM: the upcalls are the binding's, and the stages
// write tokens into one log the driver reads and clears.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_loop.h"
#include "moy_loop_host.h"

#define LOG_CAP 4096
#define FEED_CAP 4096

static struct {
    uint64_t clock_us;
    uint32_t cost_us[16];       // what each stage op costs on the fake clock
    bool click, active;
    bool can_dim;
    char log[LOG_CAP];
    size_t n;
    uint8_t feed[FEED_CAP];
    size_t fget, fput;
} H;

static void tok(const char *s) {
    size_t k = strlen(s);
    if (H.n + k + 2 >= LOG_CAP) {
        return;
    }
    if (H.n) {
        H.log[H.n++] = ' ';
    }
    memcpy(H.log + H.n, s, k);
    H.n += k;
    H.log[H.n] = 0;
}

static uint32_t h_ms(void) {
    return (uint32_t)(H.clock_us / 1000u);
}

static uint32_t h_us(void) {
    return (uint32_t)H.clock_us;
}

// The fake clock moves by what the stage `i` was told it costs.
static void cost(int i) {
    H.clock_us += H.cost_us[i];
}

static void h_sleep(uint32_t ms) {
    char b[24];
    snprintf(b, sizeof(b), "sleep=%u", (unsigned)ms);
    tok(b);
}

static void h_inputs(uint32_t now, bool *click, bool *active) {
    (void)now;
    tok("inputs");
    cost(MOY_LOOP_HOST_INPUTS);
    *click = H.click;
    *active = H.active;
}

static int h_getc(void) {
    if (H.fget == H.fput) {
        return -1;
    }
    int c = H.feed[H.fget];
    H.fget = (H.fget + 1) % FEED_CAP;
    return c;
}

static void h_pointer(uint32_t now, bool click, bool swallow) {
    (void)now;
    tok(swallow ? "pointer:swallow" : click ? "pointer:click" : "pointer");
    cost(MOY_LOOP_HOST_POINTER);
}

static void h_present(void) {
    tok("present");
    cost(MOY_LOOP_HOST_PRESENT);
}

static bool h_backlight(int level) {
    char b[24];
    snprintf(b, sizeof(b), "light=%d", level);
    tok(b);
    return H.can_dim;
}

static void h_fence(void) {
    tok("fence");
    cost(MOY_LOOP_HOST_FENCE);
}

static void h_repaint(void) {
    tok("repaint");
}

static void h_first_light(uint32_t ms) {
    (void)ms;
    tok("first_light");
}

static void h_tail(uint32_t now, bool drew) {
    (void)now;
    tok(drew ? "tail:drew" : "tail");
    cost(MOY_LOOP_HOST_TAIL);
}

static void h_healthy(void) {
    tok("healthy");
}

static void h_feed(void) {
    tok("feed");
}

static void h_say(const char *line) {
    char b[160];
    snprintf(b, sizeof(b), "say[%s]", line);
    for (char *p = b; *p; p++) {
        if (*p == ' ') {
            *p = '_';
        }
    }
    tok(b);
}

static void h_point(int32_t x, int32_t y, bool down, bool edge) {
    char b[48];
    snprintf(b, sizeof(b), "pt=%d,%d,%d,%d", (int)x, (int)y, down ? 1 : 0, edge ? 1 : 0);
    tok(b);
}

static const moy_loop_ops_t HOST_OPS = {
    .ticks_ms = h_ms,
    .ticks_us = h_us,
    .sleep_ms = h_sleep,
    .inputs = h_inputs,
    .getc = h_getc,
    .pointer = h_pointer,
    .present = h_present,
    .backlight = h_backlight,
    .fence = h_fence,
    .repaint = h_repaint,
    .first_light = h_first_light,
    .tail = h_tail,
    .healthy = h_healthy,
    .feed = h_feed,
    .say = h_say,
    .point = h_point,
};

void moy_loop_host_init(int fps_cap, bool can_dim, uint32_t clock_ms) {
    memset(&H, 0, sizeof(H));
    H.can_dim = can_dim;
    H.clock_us = (uint64_t)clock_ms * 1000u;
    moy_loop_init(&HOST_OPS, fps_cap);
    moy_idle_init(moy_loop_idle(), can_dim, clock_ms);
}

void moy_loop_host_clock(uint32_t ms) {
    H.clock_us = (uint64_t)ms * 1000u;
}

void moy_loop_host_advance(uint32_t us) {
    H.clock_us += us;
}

void moy_loop_host_cost(int stage, uint32_t us) {
    if (stage >= 0 && stage < 16) {
        H.cost_us[stage] = us;
    }
}

void moy_loop_host_input(bool click, bool active) {
    H.click = click;
    H.active = active;
}

void moy_loop_host_feed(const uint8_t *bytes, size_t n) {
    for (size_t i = 0; i < n; i++) {
        size_t next = (H.fput + 1) % FEED_CAP;
        if (next == H.fget) {
            return;
        }
        H.feed[H.fput] = bytes[i];
        H.fput = next;
    }
}

const char *moy_loop_host_log(void) {
    return H.log;
}

void moy_loop_host_clear(void) {
    H.n = 0;
    H.log[0] = 0;
}

void moy_loop_host_note(const char *token) {
    tok(token);
}

// ---- the driver tier ------------------------------------------------------------
//
// The host's and the browser's frame: their harness (runtime/host_api.py's
// ConsoleDriver, under host_app and the browser's worker) feeds the input and
// owns the clock, and each of its frames is one moy_loop_step over stages that
// are all absent but the clock -- the order, the three console upcalls, the
// ladder and the meters are the kernel's, as they are on a board.

static uint64_t s_drv_us;

static uint32_t d_ms(void) {
    return (uint32_t)(s_drv_us / 1000u);
}

static uint32_t d_us(void) {
    return (uint32_t)s_drv_us;
}

static const moy_loop_ops_t DRIVER_OPS = {
    .ticks_ms = d_ms,
    .ticks_us = d_us,
};

bool moy_loop_driver_on(void) {
    return moy_loop_ops() == &DRIVER_OPS;
}

void moy_loop_driver_init(int fps_cap) {
    moy_loop_init(&DRIVER_OPS, fps_cap);
    moy_idle_init(moy_loop_idle(), false, d_ms());
}

int moy_loop_driver_step(uint32_t dt_us) {
    if (!moy_loop_driver_on()) {
        moy_loop_driver_init(60);
    }
    s_drv_us += dt_us;
    return moy_loop_step();
}
