// The serial face's host harness: moy_sh_board.c, the board's host table,
// over the kernel's real log and a stand-in loop, fed a script a line at a
// time as the dev channel's `sh` word feeds it; every line the face says is
// printed, as the channel would carry it.
//
//   board_main [-vm] [-screen] SCRIPT      MOY_SH_ROOT names the files tree
//
// With -screen the same lines go through the screen face (moy_sh_screen.c)
// instead, a grid of SCREEN_COLS columns (the serial face's line, so the two
// break a long line alike), and the lines each adds to its grid are printed
// as the serial face says them: one transcript, both faces.
//
// With no -vm the VM is stopped: the door answers NEEDS_VM and a run is in
// front (pid 9, "moybyte.doom", which `kill` ends). With -vm the door is a
// stand-in console: `ps` answers two rows, `role` echoes its argv through the
// running session's stdout, `lookup`/`spawn` know `hello`, `kill` refuses
// pid 1, `read /proc/7/status` and `list /proc` answer, the rest is ABSENT.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_lease.h"
#include "moy_log.h"
#include "moy_loop.h"
#include "moy_sh_board.h"
#include "moy_sh_screen.h"

static int vm = 0;
static int front = 1;

bool moy_loop_vm(void) {
    return vm;
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
    static const char *const names[] = { "gpio", "uart1", "spi2" };
    if (i >= 3) {
        return MOY_LEASE_STALE;
    }
    memset(out, 0, sizeof *out);
    strcpy(out->name, names[i]);
    out->cls = (uint8_t)i + 1;          // uart1 is class 0's stand-in below
    if (i == 1) {
        out->cls = 0;
    }
    return MOY_LEASE_OK;
}

uint32_t moy_lease_slots(void) {
    return 1;
}

uint32_t moy_lease_at(uint32_t slot) {
    return slot == 0 ? 5 : 0;
}

int moy_lease_get(uint32_t h, moy_lease_row_t *out) {
    memset(out, 0, sizeof *out);
    out->owner = 9;                     // the run in front holds uart1
    out->cls = h == 5 ? 0 : 1;
    return MOY_LEASE_OK;
}

bool moy_play_front_live(void) {
    return front;
}

bool moy_play_front_end(int why) {
    (void)why;
    printf("[the run in front ends]\n");
    front = 0;
    return true;
}

static int32_t text(uint8_t *ans, size_t cap, const char *t) {
    size_t n = strlen(t);
    if (n > cap) {
        return -2;
    }
    memcpy(ans, t, n);
    return (int32_t)n;
}

int32_t moy_loop_role(uint32_t grant, uint32_t row, const uint8_t *arg, size_t n,
                      uint8_t *ans, size_t cap) {
    (void)grant;
    if (!vm) {
        return MOY_LOOP_ROLE_NEEDS_VM;
    }
    if (row != MOY_SH_DOOR) {
        return -7;
    }
    char *f[16];
    int k = 0;
    char buf[1024];
    memcpy(buf, arg, n);
    buf[n] = 0;
    for (size_t i = 0; i <= n && k < 16; k++) {
        f[k] = buf + i;
        i += strlen(buf + i) + 1;
    }
    if (!strcmp(f[0], "ps")) {
        return text(ans, cap, "1 0 shell running 0 console\n7 1 service background 2048 achievements\n");
    }
    if (!strcmp(f[0], "kill")) {
        return atoi(f[1]) == 1 ? 1 : 0;     // EPERM
    }
    if (!strcmp(f[0], "status")) {
        return atoi(f[1]) == 7 ? text(ans, cap, "running") : -8;
    }
    if (!strcmp(f[0], "lookup")) {
        return !strcmp(f[1], "hello");
    }
    if (!strcmp(f[0], "spawn") && !strcmp(f[2], "hello")) {
        moy_sh_fd_write(moy_sh_running(), 1, "hello from the console\n", 23);
        return 0;
    }
    if (!strcmp(f[0], "role")) {
        char line[256];
        int m = snprintf(line, sizeof line, "console %s:", f[1]);
        for (int i = 2; i < k; i++) {
            m += snprintf(line + m, sizeof line - m, " %s", f[i]);
        }
        line[m++] = '\n';
        moy_sh_fd_write(moy_sh_running(), 1, line, (size_t)m);
        return !strcmp(f[1], "perm") ? 0 : -8;
    }
    if (!strcmp(f[0], "read") && !strcmp(f[1], "/proc/7/status")) {
        return text(ans, cap, "Name:\tachievements\nPid:\t7\n");
    }
    if (!strcmp(f[0], "list") && !strcmp(f[1], "/proc/7")) {
        return text(ans, cap, "log\nstatus\n");
    }
    if (!strcmp(f[0], "list") && !strcmp(f[1], "/proc")) {
        return text(ans, cap, "1/\n7/\n");
    }
    if (!strcmp(f[0], "mem")) {
        return text(ans, cap, "2000000 1500000");
    }
    if (!strcmp(f[0], "df")) {
        return text(ans, cap, "1048576 524288");
    }
    return -8;
}

#define SCREEN_COLS ((int)MOY_SH_SERIAL_LINE)
#define SCREEN_ROWS 24

// The screen face's answer to one line: the grid's lines from `*next` to the
// cursor's, after a partial last line is ended as the face ends one.
static void screen_line(moy_sh_screen_t **fp, const char *text, uint32_t *next) {
    if (*fp == NULL) {
        *fp = moy_sh_screen_new(SCREEN_COLS, SCREEN_ROWS, getenv("MOY_SH_ROOT"));
        if (*fp == NULL) {
            printf("SH error no session\n");
            return;
        }
        *next = 0;
    }
    moy_sh_screen_t *f = *fp;
    moy_term_t *t = moy_sh_screen_term(f);
    size_t n = strlen(text);
    char *buf = malloc(n + 1);
    memcpy(buf, text, n);
    buf[n] = '\n';
    int r = moy_sh_screen_run(f, buf, n + 1);
    free(buf);
    int cur[3];
    moy_term_cursor(t, cur);
    if (cur[1] != 0) {
        moy_term_write(t, "\n", 1);
    }
    uint32_t v[3];
    moy_term_take(t, v);
    moy_term_cursor(t, cur);
    uint32_t end = v[0] + (uint32_t)cur[0];
    char row[4 * SCREEN_COLS + 64];
    for (uint32_t i = *next < v[0] ? v[0] : *next; i < end; i++) {
        moy_term_line(t, (int)(i - v[0]), row, sizeof row);
        printf("SH %s\n", row);
    }
    *next = end;
    moy_sh_t *s = moy_sh_screen_session(f);
    if (r == MOY_SH_MORE) {
        printf("SH more\n");
    } else if (r == MOY_SH_EXIT) {
        printf("SH exit %d\n", moy_sh_status(s));
        moy_sh_screen_free(f);
        *fp = NULL;
    } else {
        printf("SH end %d\n", moy_sh_status(s));
    }
}

int main(int argc, char **argv) {
    int screen = 0;
    for (;;) {
        if (argc > 1 && !strcmp(argv[1], "-vm")) {
            vm = 1;
        } else if (argc > 1 && !strcmp(argv[1], "-screen")) {
            screen = 1;
        } else {
            break;
        }
        argc--;
        argv++;
    }
    if (argc != 2) {
        fprintf(stderr, "usage: board_main [-vm] [-screen] SCRIPT\n");
        return 2;
    }
    moy_log_init(8192);
    moy_log_pin_clock(1500000);
    moy_log_say(6, 0, "kernel up");
    uint32_t run = moy_log_spawn("moybyte.doom");
    while (run < 9) {
        run = moy_log_spawn("moybyte.doom");
    }
    moy_log_say(4, run, "a slow frame\nand its second line");
    moy_log_set_current(run);
    FILE *f = fopen(argv[1], "r");
    if (f == NULL) {
        perror(argv[1]);
        return 2;
    }
    char line[4096];
    moy_sh_screen_t *face = NULL;
    uint32_t next = 0;
    while (fgets(line, sizeof line, f) != NULL) {
        line[strcspn(line, "\n")] = 0;
        printf("> sh %s\n", line);
        if (screen) {
            screen_line(&face, line, &next);
        } else {
            moy_sh_board_line(line);
        }
    }
    fclose(f);
    moy_sh_screen_free(face);
    moy_sh_t *s = moy_sh_running();
    if (s != NULL) {
        void *ctx = moy_sh_ctx(s);
        moy_sh_free(s);
        free(ctx);
    }
    return 0;
}
