// svc: a native service's hooks, each called by the OS on the service's task:
// a timer, the key events, a thread that sleeps until it is swept, its stop.
#include <pthread.h>
#include <string.h>
#include <unistd.h>

#include "moybyte/os.h"

static int g_ticks;
static int g_events;

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

static void *nap(void *arg) {
    (void)arg;
    for (;;) {
        sleep(10);
    }
    return NULL;
}

int moy_start(void) {
    pthread_t t;
    say("start", 0);
    if (moy_timer_every(20000) < 0 || moy_events(1u << MOY_EV_KEY_DOWN) < 0) {
        return 1;
    }
    pthread_create(&t, NULL, nap, NULL);
    write(1, "started\n", 8);
    return 0;
}

void moy_timer(uint32_t t_us) {
    (void)t_us;
    if (++g_ticks <= 2) {
        say("tick", g_ticks);
    }
}

void moy_on(const moy_event_t *e) {
    g_events++;
    say("key", e->a);
}

void moy_stop(void) {
    say("stop", g_ticks >= 2 && g_events >= 1);
}
