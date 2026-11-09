// svc: a WASI program in the service model (docs/os_2026-11/processes.md
// section 1). main is its loop: it blocks in poll on its event descriptor
// (WASI's poll_oneoff), with its next tick's deadline as the timeout, and
// answers each "ping" with a "pong" carrying the same arguments, sent to the
// app the ping came from; a "note" posts a notification and writes the OS
// log, printing the notification's answer; a "wake" sets a timer that goes
// off once, one that repeats and an alarm already due, heard as the system's
// messages "moybyte.timer" and "moybyte.at" carrying the wake's id (the
// repeating one cancelled at its first tick), then writes and reads back a
// pref and the clipboard and opens an item no app opens, printing each
// answer and its errno; a "bye" ends it, a "quit" returns
// 7 and a "spin" runs on without polling again, sleeping in slices. Its stop is the
// descriptor's end of file. The calls are the ones an ELF service makes
// (moybyte/os.h), here its moybyte.os imports. The terminal's test runs it
// as a command (tests/test_wasi_commands.py), the scheduler's as a service
// (tests/test_wasi_services.py).
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
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
    if (moy_on("ping", 1) < 0 || moy_on("bye", 1) < 0 || moy_on("quit", 1) < 0
        || moy_on("spin", 1) < 0 || moy_on("note", 1) < 0 || moy_on("wake", 1) < 0) {
        printf("no queue: %d\n", errno);
        return 1;
    }
    printf("listening\n");
    fflush(stdout);
    static union {
        moy_evq_ev_t ev;
        char b[sizeof(moy_evq_ev_t) + 256];
    } m;
    int ticks = 0, pings = 0, ticker = 0;
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
        if (got == 0) {
            printf("stopped after %d pings, %s\n", pings, ticks >= 2 ? "ticking" : "still");
            return 0;
        }
        if (got < 0) {
            continue;
        }
        if (strcmp(m.ev.name, "ping") == 0) {
            pings++;
            printf("ping %u from %s: %d\n", m.ev.n, m.ev.app,
                   moy_send(m.ev.app, "pong", m.b + sizeof m.ev));
            fflush(stdout);
        } else if (strcmp(m.ev.name, "note") == 0) {
            int id = moy_notify("Service Wasi", m.b + sizeof m.ev);
            printf("note %d errno %d\n", id, id < 0 ? errno : 0);
            fflush(stdout);
            moy_log(MOY_LOG_INFO, "noted");
        } else if (strcmp(m.ev.name, "wake") == 0) {
            printf("after %d\n", (int)moy_after(0));
            ticker = (int)moy_every(TICK_MS);
            printf("every %d\n", ticker);
            int id = (int)moy_at(1);
            printf("at %d errno %d\n", id, id < 0 ? errno : 0);
            char b[64];
            int r = moy_prefs_set("level", "42");
            printf("prefs.set %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_prefs_get("level", b, sizeof b);
            printf("prefs.get %.*s errno %d\n", r > 0 ? r : 0, b, r < 0 ? errno : 0);
            r = moy_clipboard_put_text("from wasi");
            printf("clipboard.put_text %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_clipboard_text(b, sizeof b);
            printf("clipboard.text %.*s errno %d\n", r > 0 ? r : 0, b, r < 0 ? errno : 0);
            r = moy_clipboard_text(b, 2);
            printf("clipboard.short %d errno %d\n", r, r < 0 ? errno : 0);
            r = moy_open_with("nothing/opens", 0);
            printf("open_with %d errno %d\n", r, r < 0 ? errno : 0);
            fflush(stdout);
        } else if (strncmp(m.ev.name, "moybyte.", 8) == 0) {
            printf("%s from %s %s\n", m.ev.name, m.ev.app, m.b + sizeof m.ev);
            if (strcmp(m.ev.name, "moybyte.timer") == 0 && atoi(m.b + sizeof m.ev + 1) == ticker) {
                printf("cancel %d\n", (int)moy_cancel(ticker));
            }
            fflush(stdout);
        } else if (strcmp(m.ev.name, "bye") == 0) {
            printf("bye after %d pings, %s\n", pings, ticks >= 2 ? "ticking" : "still");
            return 0;
        } else if (strcmp(m.ev.name, "quit") == 0) {
            return 7;
        } else if (strcmp(m.ev.name, "spin") == 0) {
            for (;;) {
                usleep(10000);
            }
        }
    }
}
