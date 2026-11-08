// svc: a native service (docs/os_2026-11/processes.md section 1, the service
// model). main is its loop: it blocks in poll on its event descriptor, with
// its next tick's deadline as the timeout, and answers each "ping" with a
// "pong" carrying the same arguments, sent to the app the ping came from.
// Its stop is the descriptor's end of file. A thread it makes sleeps until
// the process's end sweeps it.
#include <poll.h>
#include <pthread.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "moybyte/os.h"

#define TICK_MS 20

static void say(const char *what, int n) {
    char b[48];
    size_t k = strlen(what);
    memcpy(b, what, k);
    b[k++] = ' ';
    char d[12];
    int i = sizeof d;
    unsigned u = (unsigned)n;
    do {
        d[--i] = (char)('0' + u % 10);
        u /= 10;
    } while (u);
    memcpy(b + k, d + i, sizeof d - (size_t)i);
    b[k + sizeof d - (size_t)i] = 0;
    moy_log(MOY_LOG_INFO, b);
}

static long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void *nap(void *arg) {
    (void)arg;
    for (;;) {
        sleep(10);
    }
    return NULL;
}

int main(int argc, char **argv) {
    (void)argc;
    pthread_t t;
    say("start", 0);
    if (moy_on("ping", 1) < 0) {
        return 1;
    }
    pthread_create(&t, NULL, nap, NULL);
    write(1, "started\n", 8);
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
            if (++ticks <= 2) {
                say("tick", ticks);
            }
            next += TICK_MS;
            continue;
        }
        ssize_t got = read(MOY_EVQ_FD, &m, sizeof m);
        if (got == 0) {
            break;                              // stopped
        }
        if (got > 0 && strcmp(m.ev.name, "ping") == 0) {
            pings++;
            say("ping", (int)m.ev.n);
            moy_send(m.ev.app, "pong", m.b + sizeof m.ev);
        }
    }
    say("stop", ticks >= 2 && pings >= 1 && strcmp(argv[0], "elf.svc") == 0);
    return 0;
}
