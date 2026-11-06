// The host harness: the server over a TCP port with the board's shell
// (moy_ssh_sh.c over native/moy_sh's board table), its two tasks the run's
// (moy_ssh_run.c) on threads, and the console's task a thread that starts
// the run and whose loop serves the hops, as a board's frame tail does. The
// files tree is the folder MOY_SH_ROOT names. Behind the door the VM answers
// one role, `passwd`, as runtime/system/ssh_service.py does: the hash
// written or removed, the run started or stopped. Every other crossing is
// the VM's NEEDS_VM. The main thread prints the server's STATS line when
// a connection ends. Usage: ssh_main PORT SYSTEM_DIR

#include <pthread.h>
#include <signal.h>
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

// -- the stand-in kernel: nothing in front, the VM's one role ---------------------

// The door is the console task's alone (moy_ssh.h, TASKS): reached from any
// other thread it ends the harness, which a test reads as a dead server.
static pthread_t console_tid;
static volatile int console_up;

bool moy_loop_vm(void) {
    if (console_up && !pthread_equal(pthread_self(), console_tid)) {
        fprintf(stderr, "ssh_main: the door reached off the console's task\n");
        abort();
    }
    return true;
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

static int s_port;
static const char *s_system;

// `passwd PASSWORD` and `passwd -d` (runtime/shell/sh_roles.py r_passwd).
static int32_t passwd(const char *arg, size_t n) {
    char path[512];
    snprintf(path, sizeof path, "%s/password", s_system);
    if (n == 2 && memcmp(arg, "-d", 2) == 0) {
        unlink(path);
        moy_ssh_run_stop();
        return 0;
    }
    char pw[128], out[MOY_SSH_HASH_MAX];
    if (n == 0 || n >= sizeof pw || memchr(arg, 0, n) != NULL) {
        return 2;
    }
    memcpy(pw, arg, n);
    pw[n] = 0;
    FILE *f;
    if (moy_ssh_hash(pw, "00112233445566778899aabbccddeeff", out, sizeof out) != 0
            || (f = fopen(path, "w")) == NULL) {
        return 1;
    }
    fprintf(f, "%s\n", out);
    fclose(f);
    return moy_ssh_run_start(s_port) == 0 ? 0 : 1;
}

int32_t moy_loop_role(uint32_t grant, uint32_t row, const uint8_t *arg, size_t n,
                      uint8_t *ans, size_t cap) {
    (void)grant;
    (void)row;
    (void)ans;
    (void)cap;
    static const char verb[] = "role\0passwd";
    if (n >= sizeof verb - 1 && memcmp(arg, verb, sizeof verb - 1) == 0
            && (n == sizeof verb - 1 || arg[sizeof verb - 1] == 0)) {
        size_t k = n == sizeof verb - 1 ? n : sizeof verb;
        return passwd((const char *)arg + k, n - k);
    }
    return MOY_LOOP_ROLE_NEEDS_VM;
}

// -- the tasks ----------------------------------------------------------------------

static void *console(void *arg) {
    (void)arg;
    console_tid = pthread_self();
    console_up = 1;
    if (moy_ssh_run_start(s_port) != 0) {
        perror("listen");
        exit(1);
    }
    printf("listening on %d\n", s_port);
    fflush(stdout);
    for (;;) {
        moy_ssh_tail();
        usleep(16000);
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s PORT SYSTEM_DIR\n", argv[0]);
        return 2;
    }
    // a client gone mid-write is the session's end, as on a board's lwIP
    signal(SIGPIPE, SIG_IGN);
    s_port = atoi(argv[1]);
    s_system = argv[2];
    if (moy_ssh_setup(s_system) != 0) {
        return 1;
    }
    moy_ssh_face_set_shell(&moy_ssh_moy_sh);
    pthread_t t;
    pthread_create(&t, NULL, console, NULL);
    uint32_t ended = UINT32_MAX;
    int listening = -1;
    for (;;) {
        moy_ssh_stats_t s;
        moy_ssh_stats(&s);
        s.listening = moy_ssh_run_listening();
        if (s.ended != ended || s.listening != listening) {
            ended = s.ended;
            listening = s.listening;
            printf("STATS sessions=%u signed_in=%u refused=%u heap_now=%zu heap_peak=%zu "
                   "session_peak=%zu to_shell_ms=%u arena_peak=%zu listening=%d\n",
                   s.sessions, s.signed_in, s.refused, s.heap_now, s.heap_peak,
                   s.session_peak, s.handshake_ms, s.arena_peak, s.listening);
            fflush(stdout);
        }
        usleep(20000);
    }
}
