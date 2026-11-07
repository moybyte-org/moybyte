// moy_ssh_run: the server's task and its hold on the face task
// (native/moy_tty), started and stopped by the console (moy_ssh.h, THE RUN).
// Shared by the board's module and the host harness.
//
// One word says what the server does: RUN, serve; STOP, end after the
// session served now; ENDING, it has left its loop for good, lets go of the
// face task and parks. Only the server moves STOP to ENDING, so a start that
// finds STOP may set RUN back: the server has not let go of its socket. A
// parked server resumes when its done flag is cleared, or when it finds the
// word RUN.

#include <unistd.h>

#include "moy_ssh.h"
#include "moy_tty.h"

#define SERVER_STACK (16 * 1024)

enum { RUN, STOP, ENDING };

typedef struct {
    uintptr_t server;
    volatile int stop, done;
    int lfd;
} run_t;

static run_t *s_r;

static int get(volatile int *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}

static void put(volatile int *p, int v) {
    __atomic_store_n(p, v, __ATOMIC_RELEASE);
}

static int swap(volatile int *p, int from, int to) {
    return __atomic_compare_exchange_n(p, &from, to, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

static void park(run_t *r, volatile int *done) {
    put(done, 1);
    while (get(done)) {
        if (get(&r->stop) == RUN && swap(done, 1, 0)) {
            break;
        }
        moy_tty_nap(MOY_TTY_FOREVER);
    }
}

static void server_task(void *arg) {
    run_t *r = arg;
    for (;;) {
        for (;;) {
            while (get(&r->stop) == RUN) {
                if (moy_ssh_serve_one(r->lfd, 500) < 0) {
                    swap(&r->stop, RUN, STOP);
                }
            }
            // a start withdrew the stop while this session ran: serve on
            if (swap(&r->stop, STOP, ENDING) || get(&r->stop) == ENDING) {
                break;
            }
        }
        close(r->lfd);
        r->lfd = -1;
        moy_tty_hold(MOY_TTY_SSH, 0);
        park(r, &r->done);
    }
}

static int parked(void) {
    return get(&s_r->done) && !moy_tty_busy(MOY_TTY_SSH);
}

// Until the server parks and its face ends, serving the hops they make
// meanwhile.
static void wait_parked(void) {
    while (!parked()) {
        moy_tty_tail();
        moy_tty_console_nap(20);
    }
}

// The server made, or woken when parked; 0 when it could not be made.
static int wake(uintptr_t *task, volatile int *done, void (*fn)(void *arg), const char *name,
                uint32_t stack) {
    put(done, 0);
    if (*task != 0) {
        moy_tty_poke(*task);
        return 1;
    }
    *task = moy_tty_spawn(fn, name, stack, s_r);
    if (*task == 0) {
        put(done, 1);
        return 0;
    }
    return 1;
}

int moy_ssh_run_start(int port) {
    if (s_r == NULL) {
        s_r = moy_ssh_calloc(1, sizeof *s_r);
        if (s_r == NULL) {
            return MOY_SSH_ENOMEM;
        }
        s_r->stop = ENDING;
        s_r->done = 1;
        s_r->lfd = -1;
    }
    run_t *r = s_r;
    if (moy_tty_serving()) {
        // a face's own command: the session it runs in cannot end now
        if (swap(&r->stop, STOP, RUN) || get(&r->stop) == RUN) {
            return moy_tty_hold(MOY_TTY_SSH, 1) == 0 ? 0 : MOY_SSH_ENOMEM;
        }
        // its client went while the command ran and the server left its
        // loop: it parks without waiting on anything, and serves again
        while (!get(&r->done)) {
            moy_tty_console_nap(5);
        }
    } else {
        if (get(&r->stop) == RUN) {
            return 0;
        }
        wait_parked();
    }
    r->lfd = moy_ssh_listen(port);
    if (r->lfd < 0) {
        return MOY_SSH_EADDRINUSE;
    }
    put(&r->stop, RUN);
    if (moy_tty_hold(MOY_TTY_SSH, 1) != 0
            || !wake(&r->server, &r->done, server_task, "moy_ssh", SERVER_STACK)) {
        // what was woken parks again
        put(&r->stop, ENDING);
        moy_tty_hold(MOY_TTY_SSH, 0);
        close(r->lfd);
        r->lfd = -1;
        return MOY_SSH_ENOMEM;
    }
    return 0;
}

void moy_ssh_run_stop(void) {
    if (s_r == NULL) {
        return;
    }
    swap(&s_r->stop, RUN, STOP);
    // asked from a face's own command, the stop stands until it ends
    if (!moy_tty_serving()) {
        wait_parked();
    }
}

int moy_ssh_run_listening(void) {
    return s_r != NULL && get(&s_r->stop) == RUN;
}

int moy_ssh_run_parked(void) {
    return s_r == NULL || (get(&s_r->stop) == ENDING && parked());
}

void moy_ssh_run_tasks(uintptr_t *server, uintptr_t *face) {
    moy_tty_stats_t t;
    moy_tty_stats(&t);
    *server = s_r != NULL ? s_r->server : 0;
    *face = t.task;
}
