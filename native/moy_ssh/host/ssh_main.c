// The host harness: the server over a TCP port with the board's shell
// (moy_ssh_sh.c over native/moy_sh's board table), its two tasks the run's
// (moy_ssh_run.c) on threads, and the console's task a thread that starts
// the run and whose loop serves the hops, as a board's frame tail does. The
// files tree is the folder MOY_SH_ROOT names. Behind the door the VM answers
// one role, `passwd`, as runtime/system/ssh_service.py does: the hash
// written or removed, the run started or stopped. Every other crossing is
// the VM's NEEDS_VM. The main thread prints the server's STATS line when
// a connection ends, the server starts or stops listening, or its tasks
// park. The face's socket pair is a board's: a byte one end sends crosses
// another task before the other end can read it, as lwIP's loopback hands it
// to its own, and an end closed with bytes it never read is a reset, as
// lwIP's close is. Usage: ssh_main PORT SYSTEM_DIR

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
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
    // `role JOB passwd ARGS...`
    static const char verb[] = "role";
    static const char name[] = "passwd";
    const char *p = (const char *)arg, *end = p + n;
    if (n < sizeof verb || memcmp(p, verb, sizeof verb) != 0) {
        return MOY_LOOP_ROLE_NEEDS_VM;
    }
    p += sizeof verb;
    p += strnlen(p, (size_t)(end - p)) + 1;     // the job
    if (p > end || (size_t)(end - p) < sizeof name - 1 || memcmp(p, name, sizeof name - 1) != 0
            || (p + sizeof name - 1 < end && p[sizeof name - 1] != 0)) {
        return MOY_LOOP_ROLE_NEEDS_VM;
    }
    p += sizeof name - 1;
    if (p < end) {
        p++;
    }
    return passwd(p, (size_t)(end - p));
}

// -- the pair: a board's loopback --------------------------------------------------

// Linked with --wrap=moy_ssh_pair: the face's end and Dropbear's are each a
// socketpair's, and a thread per direction carries the bytes between them,
// a chunk at a time and a little late, then the end of the stream. An end
// closed with bytes in it unread is lwIP's reset: tcp_close answers it with
// a RST and drops what that end had not yet sent, so the leg carrying them
// drops what it holds and ends the stream.
#define LEG_CHUNK 4096
#define LEG_LAG_US 1000

int __real_moy_ssh_pair(int fds[2]);

typedef struct {
    int from, to;
    int *legs;                  // the legs still running; the last closes both ends
    int ends[2];
} leg_t;

// AF_UNIX marks a socket ECONNRESET when its peer closes with bytes unread.
static int reset(int fd) {
    int err = 0;
    socklen_t n = sizeof err;
    return getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &n) == 0 && err == ECONNRESET;
}

static void *leg(void *arg) {
    leg_t *l = arg;
    char buf[LEG_CHUNK];
    for (;;) {
        ssize_t n = read(l->from, buf, sizeof buf);
        if (n <= 0) {
            break;
        }
        usleep(LEG_LAG_US);
        if (reset(l->from)) {
            break;
        }
        const char *p = buf;
        while (n > 0) {
            ssize_t w = write(l->to, p, (size_t)n);
            if (w <= 0) {
                break;
            }
            p += w;
            n -= w;
        }
        if (n > 0) {
            break;
        }
    }
    shutdown(l->to, SHUT_WR);
    shutdown(l->from, SHUT_RD);
    if (__atomic_sub_fetch(l->legs, 1, __ATOMIC_ACQ_REL) == 0) {
        close(l->ends[0]);
        close(l->ends[1]);
        free(l->legs);
    }
    free(l);
    return NULL;
}

int __wrap_moy_ssh_pair(int fds[2]) {
    int db[2], face[2];
    if (__real_moy_ssh_pair(db) != 0) {
        return -1;
    }
    if (__real_moy_ssh_pair(face) != 0) {
        close(db[0]);
        close(db[1]);
        return -1;
    }
    int *legs = malloc(sizeof *legs);
    leg_t *up = calloc(1, sizeof *up), *down = calloc(1, sizeof *down);
    *legs = 2;
    *up = (leg_t){ face[1], db[1], legs, { db[1], face[1] } };
    *down = (leg_t){ db[1], face[1], legs, { db[1], face[1] } };
    pthread_t t;
    pthread_create(&t, NULL, leg, up);
    pthread_detach(t);
    pthread_create(&t, NULL, leg, down);
    pthread_detach(t);
    fds[0] = db[0];
    fds[1] = face[0];
    return 0;
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
    int listening = -1, parked = -1;
    for (;;) {
        moy_ssh_stats_t s;
        moy_ssh_stats(&s);
        s.listening = moy_ssh_run_listening();
        int now_parked = moy_ssh_run_parked();
        if (s.ended != ended || s.listening != listening || now_parked != parked) {
            ended = s.ended;
            listening = s.listening;
            parked = now_parked;
            printf("STATS ended=%u sessions=%u signed_in=%u refused=%u heap_now=%zu heap_peak=%zu "
                   "session_peak=%zu to_shell_ms=%u arena_peak=%zu listening=%d parked=%d\n",
                   s.ended, s.sessions, s.signed_in, s.refused, s.heap_now, s.heap_peak,
                   s.session_peak, s.handshake_ms, s.arena_peak, s.listening, parked);
            fflush(stdout);
        }
        usleep(20000);
    }
}
