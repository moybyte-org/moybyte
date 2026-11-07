// The host harness of the web terminal: the kernel's webhost (native/moy_net)
// on a TCP port with its sign-in, its GET /term handing the upgraded socket to the
// bridge (moy_tty_ws.c), the face and the board's shell (moy_tty_sh.c over
// native/moy_sh's board table) on threads, and the console's task a thread
// whose loop polls the webhost and serves the hops, as a board's frame does.
// The files tree is the folder MOY_SH_ROOT names. The door's crossings are
// the VM's NEEDS_VM but the console's rows. The face's pair is a socketpair.
//
// Standard input takes a line at a time: `ssh` opens a face as SSH does
// (a `sleep 30`), so the page finds the face another's; `nossh` ends it;
// `skew MS` moves the webhost's clock by MS (negative: back, so a lockout's
// wait already running lasts that much longer). Each answers a line.
// `token` prints `TOKEN <hex>`, a QR's token; `drop` forgets every paired
// browser, cutting a terminal one holds; `signout` ends every session as a
// password change does (`SIGNEDOUT N`). They run on the console's thread,
// whose state the sign-in is. SYSTEM_DIR `-` is the open host.
// The main thread prints a STATS line when a figure moves.
// Usage: tty_main PORT SYSTEM_DIR CARTS_DIR

#include <fcntl.h>
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
#include "moy_net.h"
#include "moy_tty.h"

extern const moy_tty_shell_t moy_tty_moy_sh;
extern uint64_t moy_net_ms_skew;

static pthread_t console_tid;
static volatile int console_up;

bool moy_loop_vm(void) {
    if (console_up && !pthread_equal(pthread_self(), console_tid)) {
        fprintf(stderr, "tty_main: the door reached off the console's task\n");
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

int32_t moy_loop_role(uint32_t grant, uint32_t row, const uint8_t *arg, size_t n,
                      uint8_t *ans, size_t cap) {
    (void)grant;
    (void)row;
    static const char console_text[] =
        "name: moy\nboard: host harness\nfirmware: 0.0.0\ndisplay: 800x480\n"
        "theme: moy\nwifi: lab (192.168.1.2)\n";
    static const char console_read[] = "read\0/proc/console";
    if (n == sizeof console_read - 1 && memcmp(arg, console_read, n) == 0
            && cap >= sizeof console_text) {
        memcpy(ans, console_text, sizeof console_text - 1);
        return (int32_t)(sizeof console_text - 1);
    }
    if ((n == 3 && memcmp(arg, "mem", 3) == 0) || (n > 3 && memcmp(arg, "df\0", 3) == 0)) {
        return (int32_t)snprintf((char *)ans, cap, "%u %u", 8000000u, 6000000u);
    }
    return MOY_LOOP_ROLE_NEEDS_VM;
}

static const char *s_system, *s_carts;
static int s_port;
static int s_cmd;                       // 1 token, 2 drop, 3 signout: the console's to run


static void *console(void *arg) {
    (void)arg;
    console_tid = pthread_self();
    console_up = 1;
    moy_web_cfg_t cfg = {0};
    cfg.port = (uint16_t)s_port;
    cfg.carts = s_carts;
    cfg.system = s_system;
    cfg.defer = "";
    if (moy_web_start(&cfg) != 0) {
        perror("listen");
        exit(1);
    }
    moy_tty_set_shell(&moy_tty_moy_sh);
    printf("listening on %d\n", s_port);
    fflush(stdout);
    for (;;) {
        moy_web_poll();
        moy_tty_tail();
        int cmd = __atomic_exchange_n(&s_cmd, 0, __ATOMIC_ACQ_REL);
        if (cmd == 1) {
            char t[33];
            moy_web_token(t);
            printf("TOKEN %s\n", t);
            fflush(stdout);
        } else if (cmd == 2) {
            printf("DROPPED %d\n", moy_web_key_drop("all"));
            fflush(stdout);
        } else if (cmd == 3) {
            printf("SIGNEDOUT %d\n", moy_web_signout());
            fflush(stdout);
        }
        usleep(16000);
    }
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s PORT SYSTEM_DIR CARTS_DIR\n", argv[0]);
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    s_port = atoi(argv[1]);
    s_system = strcmp(argv[2], "-") ? argv[2] : NULL;
    s_carts = argv[3];
    pthread_t t;
    pthread_create(&t, NULL, console, NULL);
    fcntl(0, F_SETFL, fcntl(0, F_GETFL, 0) | O_NONBLOCK);
    char line[64];
    size_t ln = 0;
    int ssh_fd = -1;
    moy_tty_ws_stats_t last = {0};
    int first = 1;
    for (;;) {
        char c;
        while (read(0, &c, 1) == 1) {
            if (c != '\n') {
                if (ln < sizeof line - 1) {
                    line[ln++] = c;
                }
                continue;
            }
            line[ln] = 0;
            ln = 0;
            static pid_t pid;
            if (strcmp(line, "token") == 0) {
                __atomic_store_n(&s_cmd, 1, __ATOMIC_RELEASE);
            } else if (strcmp(line, "drop") == 0) {
                __atomic_store_n(&s_cmd, 2, __ATOMIC_RELEASE);
            } else if (strcmp(line, "signout") == 0) {
                __atomic_store_n(&s_cmd, 3, __ATOMIC_RELEASE);
            } else if (strcmp(line, "ssh") == 0 && ssh_fd < 0) {
                moy_tty_hold(MOY_TTY_SSH, 1);
                int r = moy_tty_open(MOY_TTY_SSH, "sleep 30", 0, 80, 24, NULL, &ssh_fd, &pid);
                printf("SSH %d\n", r);
                fflush(stdout);
            } else if (strcmp(line, "nossh") == 0 && ssh_fd >= 0) {
                moy_tty_signal(pid, SIGINT);
                close(ssh_fd);
                ssh_fd = -1;
                moy_tty_reap_all(MOY_TTY_SSH, 5000);
                moy_tty_hold(MOY_TTY_SSH, 0);
                printf("NOSSH\n");
                fflush(stdout);
            } else if (strncmp(line, "skew ", 5) == 0) {
                moy_net_ms_skew += (uint64_t)strtoll(line + 5, NULL, 10);
                printf("SKEW %s\n", line + 5);
                fflush(stdout);
            }
        }
        moy_tty_ws_stats_t w;
        moy_tty_ws_stats(&w);
        if (first || memcmp(&w, &last, sizeof w) != 0) {
            first = 0;
            last = w;
            moy_tty_stats_t s;
            moy_tty_stats(&s);
            printf("STATS sessions=%u refused=%u in=%u out=%u frames=%u live=%d faces=%u "
                   "busy=%u arena_peak=%zu\n", w.sessions, w.refused, w.in_bytes, w.out_bytes,
                   w.frames_out, w.live, s.faces, s.busy, s.arena_peak);
            fflush(stdout);
        }
        usleep(10000);
    }
}
