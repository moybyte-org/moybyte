// svc: a native service (docs/os_2026-11/processes.md section 1, the service
// model). main is its loop: it blocks in poll on its event descriptor, with
// its next tick's deadline as the timeout, and answers each "ping" with a
// "pong" carrying the same arguments, sent to the app the ping came from.
// A "note" posts a notification (an app call, answered on the VM's task) and
// answers "noted" with what it answered, then "owned" with what
// moy_service_running answers ("absent": -1 with errno ENOENT, the answer a
// `type: service` cart's process gets); a "wake" sets a timer that goes off
// at once and answers "timed" with the timer's message (the system's
// "moybyte.timer", its id) as it hears it, and a "rearm" sets one 10 ms off
// that its timer's message sets again each time it answers "timed"; a "quit"
// returns 7; a "wake" carrying ["pin"] opens gpio4 as an output driven high,
// reads it back, drives it low, reads it again and answers "pinned" with the
// two levels (or the failing answer and its errno), closing it (a name of
// its own would fill the eight a process listens on, the timer's among
// them); a "files" runs a round
// of documents saved, loaded, renamed and listed and prefs set
// (files_round); a "spin" runs
// on without polling again, sleeping in slices. Its stop is the
// descriptor's end of file. A thread it makes sleeps until the process's end
// sweeps it.
#include <errno.h>
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

// "<a> <b>" to the log.
static void say2(const char *a, const char *b) {
    char line[160];
    size_t k = strlen(a), n = strlen(b);
    if (k + n + 2 > sizeof line) {
        n = sizeof line - k - 2;
    }
    memcpy(line, a, k);
    line[k++] = ' ';
    memcpy(line + k, b, n);
    line[k + n] = 0;
    moy_log(MOY_LOG_INFO, line);
}

// `n` in decimal after `out`'s first `k` bytes; the new length.
static size_t num(char *out, size_t k, unsigned n) {
    char d[12];
    int i = sizeof d;
    do {
        d[--i] = (char)('0' + n % 10);
        n /= 10;
    } while (n);
    memcpy(out + k, d + i, sizeof d - (size_t)i);
    return k + sizeof d - (size_t)i;
}

// The files round (a "files" carrying [rounds]): each round saves a document
// "elf-<i>", loads it back, renames it "elf-r<i>" and loads that, and sets
// the pref "elf" to <i>; then lists the documents. Logs "files <ok> <listed>"
// and, for the first failure, the call and its answer.
static void files_round(int rounds) {
    static char saved[64], renamed[64], got[128], names[4096];
    int ok = 0;
    for (int i = 0; i < rounds; i++) {
        char title[32], body[64], again[32], pref[16];
        size_t t = num(title, 4, (unsigned)i);
        memcpy(title, "elf-", 4);
        title[t] = 0;
        size_t b = num(body, 9, (unsigned)i);
        memcpy(body, "elf body ", 9);
        body[b] = 0;
        size_t r = num(again, 5, (unsigned)i);
        memcpy(again, "elf-r", 5);
        again[r] = 0;
        int32_t n = moy_files_save("docs", title, body, b, saved, sizeof saved - 1);
        if (n < 0) {
            say("save", errno);
            continue;
        }
        saved[n] = 0;
        n = moy_files_load("docs", saved, got, sizeof got);
        if (n != (int32_t)b || memcmp(got, body, b) != 0) {
            say("load", n);
            continue;
        }
        n = moy_files_rename("docs", saved, again, renamed, sizeof renamed - 1);
        if (n < 0) {
            say("rename", errno);
            continue;
        }
        renamed[n] = 0;
        n = moy_files_load("docs", renamed, got, sizeof got);
        if (n != (int32_t)b || memcmp(got, body, b) != 0) {
            say2("reload", renamed);
            continue;
        }
        pref[num(pref, 0, (unsigned)i)] = 0;
        if (moy_prefs_set("elf", pref) < 0) {
            say("prefs", errno);
            continue;
        }
        ok++;
    }
    int32_t n = moy_files_list("docs", names, sizeof names);
    int listed = 0;
    for (int32_t at = 0; n > 0 && at < n;) {
        size_t len = 0;
        while (at + (int32_t)len < n && names[at + len] != 0) {
            len++;
        }
        listed += len > 5 && memcmp(names + at, "elf_r", 5) == 0;
        at += (int32_t)len + 1;
    }
    char line[48];
    size_t k = num(line, 0, (unsigned)ok);
    line[k++] = ' ';
    k = num(line, k, (unsigned)listed);
    line[k] = 0;
    say2("files", line);
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

static void pin_round(const char *app) {
    char b[48];
    int h = (int)moy_dev_open("gpio4", "mode=out value=1");
    int hi = h > 0 ? (int)moy_dev_get(h, "value") : -1;
    int lo = h > 0 && moy_dev_set(h, "value=0") == 0 ? (int)moy_dev_get(h, "value") : -1;
    int c = h > 0 ? (int)moy_dev_close(h) : -1;
    if (h > 0 && hi >= 0 && lo >= 0 && c == 0) {
        memcpy(b, "[1,1,0]", 8);
        b[3] = (char)('0' + hi);
        b[5] = (char)('0' + lo);
    } else {
        memcpy(b, "[0,0,0]", 8);
        b[3] = (char)('0' + (errno % 10));
    }
    moy_send(app, "pinned", b);
}

int main(int argc, char **argv) {
    (void)argc;
    pthread_t t;
    say("start", 0);
    static const char *const hears[] = { "ping", "note", "quit", "spin", "wake", "rearm",
                                         "files" };
    int rearm = 0;
    static char waker[MOY_EVQ_APP];
    for (unsigned i = 0; i < sizeof hears / sizeof hears[0]; i++) {
        if (moy_on(hears[i], 1) < 0) {
            return 1;
        }
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
        if (got <= 0) {
            continue;
        }
        if (strcmp(m.ev.name, "ping") == 0) {
            pings++;
            say("ping", (int)m.ev.n);
            moy_send(m.ev.app, "pong", m.b + sizeof m.ev);
        } else if (strcmp(m.ev.name, "note") == 0) {
            int r = moy_notify("Hello", "from a service");
            moy_send(m.ev.app, "noted", r >= 0 ? "[1]" : "[-1]");
            int own = moy_service_running();
            moy_send(m.ev.app, "owned", own < 0 && errno == ENOENT ? "[\"absent\"]"
                                       : own > 0 ? "[\"running\"]" : "[\"other\"]");
        } else if (strcmp(m.ev.name, "wake") == 0
                   && strncmp(m.b + sizeof m.ev, "[\"pin\"]", 7) == 0) {
            pin_round(m.ev.app);
        } else if (strcmp(m.ev.name, "wake") == 0) {
            memcpy(waker, m.ev.app, sizeof waker);
            say("after", moy_after(0) > 0);
        } else if (strcmp(m.ev.name, "rearm") == 0) {
            memcpy(waker, m.ev.app, sizeof waker);
            rearm = moy_after(10) > 0;
        } else if (strcmp(m.ev.name, "moybyte.timer") == 0 && waker[0]) {
            moy_send(waker, "timed", m.b + sizeof m.ev);
            if (rearm) {
                rearm = moy_after(10) > 0;
            }
        } else if (strcmp(m.ev.name, "files") == 0) {
            int rounds = 0;
            for (const char *d = m.b + sizeof m.ev + 1; *d >= '0' && *d <= '9'; d++) {
                rounds = rounds * 10 + (*d - '0');
            }
            files_round(rounds);
        } else if (strcmp(m.ev.name, "quit") == 0) {
            return 7;
        } else if (strcmp(m.ev.name, "spin") == 0) {
            for (;;) {
                usleep(10000);
            }
        }
    }
    say("stop", ticks >= 2 && pings >= 1 && strcmp(argv[0], "elf.svc") == 0);
    return 0;
}
