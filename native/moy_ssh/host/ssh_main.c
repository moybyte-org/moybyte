// The host harness: the server over a TCP port with the board's shell
// (moy_ssh_sh.c over native/moy_sh's board table, the VM stopped), its face
// task a thread and the console's task a thread whose loop serves the hops,
// as a board's frame tail does. The files tree is the folder MOY_SH_ROOT
// names. Usage: ssh_main PORT SYSTEM_DIR

#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "moy_lease.h"
#include "moy_loop.h"
#include "moy_ssh.h"
#include "moy_ssh_face.h"

extern const moy_ssh_shell_t moy_ssh_moy_sh;

// -- the stand-in kernel: the VM stopped, nothing in front ---------------------

// The door is the console task's alone (moy_ssh.h, TASKS): reached from any
// other thread it ends the harness, which a test reads as a dead server.
static pthread_t console_tid;
static volatile int console_up;

bool moy_loop_vm(void) {
    if (console_up && !pthread_equal(pthread_self(), console_tid)) {
        fprintf(stderr, "ssh_main: the door reached off the console's task\n");
        abort();
    }
    return false;
}

void moy_loop_say_line(const char *line) {
    printf("%s\n", line);
    fflush(stdout);
}

void moy_loop_say(const char *fmt, ...) {
    char buf[384];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    moy_loop_say_line(buf);
}

const moy_loop_ops_t *moy_loop_ops(void) {
    return NULL;
}

void moy_devch_unread(const uint8_t *bytes, size_t n) {
    (void)bytes;
    (void)n;
}

int moy_lease_offer_at(uint32_t i, moy_lease_offer_t *out) {
    (void)i;
    (void)out;
    return MOY_LEASE_STALE;
}

uint32_t moy_lease_slots(void) {
    return 0;
}

uint32_t moy_lease_at(uint32_t slot) {
    (void)slot;
    return 0;
}

int moy_lease_get(uint32_t h, moy_lease_row_t *out) {
    (void)h;
    memset(out, 0, sizeof *out);
    return MOY_LEASE_STALE;
}

bool moy_play_front_live(void) {
    return false;
}

bool moy_play_front_end(int why) {
    (void)why;
    return false;
}

int32_t moy_loop_role(uint32_t grant, uint32_t row, const uint8_t *arg, size_t n,
                      uint8_t *ans, size_t cap) {
    (void)grant;
    (void)row;
    (void)arg;
    (void)n;
    (void)ans;
    (void)cap;
    return MOY_LOOP_ROLE_NEEDS_VM;
}

// -- the tasks ----------------------------------------------------------------------

static volatile int stop;

static void *console(void *arg) {
    (void)arg;
    console_tid = pthread_self();
    console_up = 1;
    for (;;) {
        moy_ssh_tail();
        usleep(16000);
    }
    return NULL;
}

static void *face(void *arg) {
    (void)arg;
    moy_ssh_face_task(&stop);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s PORT SYSTEM_DIR\n", argv[0]);
        return 2;
    }
    if (moy_ssh_setup(argv[2]) != 0) {
        return 1;
    }
    moy_ssh_face_set_shell(&moy_ssh_moy_sh);
    int lfd = moy_ssh_listen(atoi(argv[1]));
    if (lfd < 0) {
        perror("listen");
        return 1;
    }
    pthread_t t;
    pthread_create(&t, NULL, face, NULL);
    pthread_create(&t, NULL, console, NULL);
    printf("listening on %s\n", argv[1]);
    fflush(stdout);
    for (;;) {
        if (moy_ssh_serve_one(lfd, 1000) == 0) {
            moy_ssh_stats_t s;
            moy_ssh_stats(&s);
            printf("STATS sessions=%u signed_in=%u refused=%u heap_now=%zu heap_peak=%zu "
                   "session_peak=%zu to_shell_ms=%u arena_peak=%zu\n", s.sessions, s.signed_in,
                   s.refused, s.heap_now, s.heap_peak, s.session_peak, s.handshake_ms,
                   s.arena_peak);
            fflush(stdout);
        }
    }
}
