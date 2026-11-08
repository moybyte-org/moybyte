// moy_sh_prog: what every compiled program the shell spawns shares, whatever
// its tier (a WASI command, native/moy_wasm/moy_wasi.h; a native module's
// POSIX rows, native/moy_elf/moy_os.h): the folders its permissions reach,
// its standard streams in the background, and the poll that ends its run on
// Ctrl-C or past its budget. Each tier's adapter keeps one in its record and
// calls these where its own calls begin; the files, the streams and the
// session are moy_sh.h's "a spawned program's files".
//
// THE ROOTS are the folders the program's `files` permissions grant, handed in
// relative to the console's store and kept as absolute shell paths. A path the
// program names reaches the tree only when it is under one of them.
//
// IN THE BACKGROUND (`&`) the program's stdout and stderr are the OS log's
// lines under its pid, at most MOY_SH_PROG_LINE bytes a line, and its stdin is
// empty.
//
// Static inline: each adapter is its own library on a board, and the two are
// one copy of this.

#ifndef MOY_SH_PROG_H
#define MOY_SH_PROG_H

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "moy_log.h"
#include "moy_sh.h"
#include "moy_sh_board.h"

#define MOY_SH_PROG_LINE 200
#define MOY_SH_PROG_PATH 512
#define MOY_SH_PROG_POLL_US 10000       // Ctrl-C and the budget, at most this often
#define MOY_SH_PROG_INTERRUPTED 130
#define MOY_SH_PROG_KILLED 137

typedef struct {
    moy_sh_t *s;
    char *roots;                // absolute shell paths, NUL-separated, an empty one last
    uint32_t bg_pid;            // 0: the foreground
    uint32_t budget_ms;         // 0: no limit
    int64_t deadline_us;
    int64_t polled_us;
    char line[2][MOY_SH_PROG_LINE];
    size_t line_n[2];
    int exited;
    int code;
} moy_sh_prog_t;

// Fill `p` for a run in session `s` from `now_us`: `roots` NUL-separated and
// ending in an empty one, relative to the store (NULL: none). The roots'
// block comes from alloc. 0, or -1 when it refuses.
static inline int moy_sh_prog_init(moy_sh_prog_t *p, moy_sh_t *s, const char *roots,
                                   uint32_t bg_pid, uint32_t budget_ms, int64_t now_us,
                                   void *(*alloc)(size_t n)) {
    memset(p, 0, sizeof *p);
    p->s = s;
    p->bg_pid = bg_pid;
    p->budget_ms = budget_ms;
    p->deadline_us = budget_ms ? now_us + (int64_t)budget_ms * 1000 : 0;
    const char *store = moy_sh_board_store(s);
    size_t rn = 1;
    for (const char *r = roots; r != NULL && *r; r += strlen(r) + 1) {
        rn += strlen(store) + 1 + strlen(r) + 1;
    }
    p->roots = alloc(rn);
    if (p->roots == NULL) {
        return -1;
    }
    char *o = p->roots;
    for (const char *r = roots; r != NULL && *r; r += strlen(r) + 1) {
        char path[MOY_SH_PROG_PATH];
        snprintf(path, sizeof path, "%s/%s", store, r);
        if (moy_sh_abs(s, path, o, rn - (size_t)(o - p->roots)) == 0) {
            o += strlen(o) + 1;
        }
    }
    *o = 0;
    return 0;
}

static inline void moy_sh_prog_flush(moy_sh_prog_t *p, int k) {
    if (p->line_n[k] > 0) {
        moy_log_write(k ? MOY_LOG_ERR : MOY_LOG_INFO, p->bg_pid, p->line[k], p->line_n[k]);
        p->line_n[k] = 0;
    }
}

// The run's end: its last background lines logged, the roots' block freed.
static inline void moy_sh_prog_fini(moy_sh_prog_t *p, void (*release)(void *q)) {
    if (p->bg_pid) {
        moy_sh_prog_flush(p, 0);
        moy_sh_prog_flush(p, 1);
    }
    release(p->roots);
    p->roots = NULL;
}

// The run ends with `code` (exit's, or the poll's).
static inline void moy_sh_prog_stop(moy_sh_prog_t *p, int code) {
    if (!p->exited) {
        p->exited = 1;
        p->code = code;
    }
}

// At the start of a call, on the session: whether the run goes on. Ctrl-C
// ends it with MOY_SH_PROG_INTERRUPTED, its budget with MOY_SH_PROG_KILLED
// and a line on stderr; checked at most every MOY_SH_PROG_POLL_US.
static inline bool moy_sh_prog_poll(moy_sh_prog_t *p, int64_t now_us) {
    if (!p->exited && now_us - p->polled_us >= MOY_SH_PROG_POLL_US) {
        p->polled_us = now_us;
        if (moy_sh_interrupted(p->s)) {
            moy_sh_prog_stop(p, MOY_SH_PROG_INTERRUPTED);
        } else if (p->deadline_us && now_us >= p->deadline_us) {
            char msg[64];
            int n = snprintf(msg, sizeof msg, "killed: ran past %u s\n",
                             (unsigned)(p->budget_ms / 1000));
            moy_sh_fd_write(p->s, 2, msg, (size_t)n);
            moy_sh_prog_stop(p, MOY_SH_PROG_KILLED);
        }
    }
    return !p->exited;
}

// A run in steps, every tier's (the shell door's job, runtime/shell/sh_door.py):
// the task that may touch the session serves the program's calls for `max_us`
// at most, then goes on with its own work (the console's frame) until the next
// step. `done(job)` answers whether the program's main has returned;
// `serve(job, us)` waits at most `us` for one of its calls, serves it and feeds
// the task's watchdog. MOY_SH_PROG_RUNNING while the run goes on, else 0: main
// returned, or the poll ended the run (Ctrl-C, the budget), which the tier then
// ends.
#define MOY_SH_PROG_RUNNING (-100)

static inline int moy_sh_prog_step(moy_sh_prog_t *p, uint32_t max_us, int64_t (*now)(void),
                                   bool (*done)(void *job), void (*serve)(void *job, int64_t us),
                                   void *job) {
    int64_t end = now() + (int64_t)max_us;
    while (!done(job)) {
        int64_t t = now();
        if (!moy_sh_prog_poll(p, t)) {
            return 0;
        }
        if (t >= end) {
            return MOY_SH_PROG_RUNNING;
        }
        serve(job, end - t < MOY_SH_PROG_POLL_US ? end - t : MOY_SH_PROG_POLL_US);
    }
    return 0;
}

static inline bool moy_sh_prog_under(const char *path, const char *dir) {
    size_t n = strlen(dir);
    if (n == 1 && dir[0] == '/') {
        return true;
    }
    return strncmp(path, dir, n) == 0 && (path[n] == 0 || path[n] == '/');
}

// Whether absolute shell path `path` is under one of the roots.
static inline bool moy_sh_prog_reaches(const moy_sh_prog_t *p, const char *path) {
    for (const char *r = p->roots; *r; r += strlen(r) + 1) {
        if (moy_sh_prog_under(path, r)) {
            return true;
        }
    }
    return false;
}

// Whether descriptor `fd` is a background run's stdin, stdout or stderr,
// which the session does not serve.
static inline bool moy_sh_prog_bg_stdio(const moy_sh_prog_t *p, int fd) {
    return p->bg_pid && fd >= 0 && fd <= 2;
}

// A background run's bytes on fd 1 or 2: the log's lines under its pid.
static inline void moy_sh_prog_bg_write(moy_sh_prog_t *p, int fd, const char *b, size_t n) {
    int k = fd - 1;
    for (size_t i = 0; i < n; i++) {
        if (b[i] == '\n' || p->line_n[k] == MOY_SH_PROG_LINE) {
            moy_sh_prog_flush(p, k);
            if (b[i] == '\n') {
                continue;
            }
        }
        p->line[k][p->line_n[k]++] = b[i];
    }
}

#endif // MOY_SH_PROG_H
