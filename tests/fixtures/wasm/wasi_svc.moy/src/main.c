// svc: a WASI program in the service model (docs/os_2026-11/processes.md
// section 1). main is its loop: it blocks in poll on its event descriptor
// (WASI's poll_oneoff), with its next tick's deadline as the timeout, and
// answers each "ping" with a "pong" carrying the same arguments, sent to the
// app the ping came from; a "bye" ends it. The calls are the ones an ELF
// service makes (moybyte/os.h), here its moybyte.os imports.
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "moybyte/os.h"

#define TICK_MS 20

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int main(void) {
    if (moy_on("ping", 1) < 0 || moy_on("bye", 1) < 0) {
        printf("no queue: %d\n", errno);
        return 1;
    }
    printf("listening\n");
    fflush(stdout);
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    int ticks = 0, pings = 0;
    long next = now_ms() + TICK_MS;
    for (;;) {
        long left = next - now_ms();
        struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };
        int n = poll(&p, 1, left > 0 ? (int)left : 0);
        if (n < 0) {
            return 1;
        }
        if (n == 0) {
            ticks++;
            next += TICK_MS;
            continue;
        }
        ssize_t got = read(MOY_EVQ_FD, &m, sizeof m);
        if (got < 0) {
            continue;
        }
        if (strcmp(m.ev.name, "ping") == 0) {
            pings++;
            printf("ping %u from %s: %d\n", m.ev.n, m.ev.app,
                   moy_send(m.ev.app, "pong", m.b + sizeof m.ev));
            fflush(stdout);
        } else if (strcmp(m.ev.name, "bye") == 0) {
            printf("bye after %d pings, %s\n", pings, ticks >= 2 ? "ticking" : "still");
            return 0;
        }
    }
}
