// moy_sh_board: the board's host table and the serial face. moy_sh_board.h
// has the contract.

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if __has_include("esp_heap_caps.h")
#define MOY_SH_ON_BOARD 1
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#else
#include <pthread.h>
#include <time.h>
#endif

#include "moy_devch.h"
#include "moy_lease.h"
#include "moy_log.h"
#include "moy_loop.h"
#include "moy_sh_app.h"
#include "moy_sh_board.h"

// The Player's run in front (native/moy_play: weak, so an image without it
// links).
bool moy_play_front_live(void) __attribute__((weak));
bool moy_play_front_end(int why) __attribute__((weak));
// The loop's jobs (native/moy_kernel/moy_loop.h): weak, so a harness
// without the loop links, and its faces' runs are then the caller's.
bool moy_loop_job(moy_loop_job_fn fn, void *arg) __attribute__((weak));
void moy_loop_job_drop(moy_loop_job_fn fn, void *arg) __attribute__((weak));

#define DOOR_ABSENT (-8)
#define DOOR_NEEDS_VM (-9)
#define DOOR_RUNNING (-100)             // a role command goes on: `step` it again
#define DOOR_FULL (-2)
#define ANSWER_CAP 8192u
#define SHELL_PID 1
#define POLL_BYTES 64
#define YIELD_MS 200u
#define ROLE_POLL_MS 10                 // a waiting role command's step, at most this often
#define INTERRUPTED 130
#define RUN_STACK (24u * 1024u)         // a runner's stack (the SSH face's task's)
#define ABORT_TURNS 256                 // a parked run's turns to end at its session's end

// -- memory ---------------------------------------------------------------------

// PSRAM; a board with none takes the default heap, one whose PSRAM is merely
// full refuses rather than spend internal SRAM.
static void *b_alloc(void *ctx, size_t n) {
    (void)ctx;
#ifdef MOY_SH_ON_BOARD
    void *p = heap_caps_malloc(n ? n : 1u, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == NULL && heap_caps_get_total_size(MALLOC_CAP_SPIRAM) == 0) {
        p = heap_caps_malloc(n ? n : 1u, MALLOC_CAP_8BIT);
    }
    return p;
#else
    return malloc(n ? n : 1u);
#endif
}

static void b_free(void *ctx, void *p) {
    (void)ctx;
#ifdef MOY_SH_ON_BOARD
    heap_caps_free(p);
#else
    free(p);
#endif
}

// -- the door ---------------------------------------------------------------------

// One crossing: `verb` and `argv` packed NUL-separated, the answer into a
// buffer from b_alloc (*ans, freed by the caller) of *n bytes. On the
// console's task: door() hops there (the running session's moy_sh_hop).
static int32_t cross(const char *verb, int argc, char *const *argv, char **ans, size_t *n) {
    *ans = NULL;
    *n = 0;
    if (!moy_loop_vm()) {
        return DOOR_NEEDS_VM;
    }
    size_t len = strlen(verb);
    for (int i = 0; i < argc; i++) {
        len += 1 + strlen(argv[i]);
    }
    char *arg = b_alloc(NULL, len + ANSWER_CAP);
    if (arg == NULL) {
        return -ENOMEM;
    }
    size_t k = strlen(verb);
    memcpy(arg, verb, k);
    for (int i = 0; i < argc; i++) {
        arg[k++] = 0;
        size_t m = strlen(argv[i]);
        memcpy(arg + k, argv[i], m);
        k += m;
    }
    char *out = arg + len;
    int32_t r = moy_loop_role(0, MOY_SH_DOOR, (const uint8_t *)arg, len, (uint8_t *)out,
                              ANSWER_CAP - 1);
    if (r >= 0 && r < (int32_t)ANSWER_CAP) {
        memmove(arg, out, (size_t)r);
        arg[r] = 0;
        *ans = arg;
        *n = (size_t)r;
    } else {
        b_free(NULL, arg);
    }
    return r;
}

typedef struct {
    const char *verb;
    int argc;
    char *const *argv;
    char **ans;
    size_t *n;
    int32_t r;
} door_t;

static void door_here(void *arg) {
    door_t *d = arg;
    d->r = cross(d->verb, d->argc, d->argv, d->ans, d->n);
}

static int32_t door(const char *verb, int argc, char *const *argv, char **ans, size_t *n) {
    door_t d = { verb, argc, argv, ans, n, 0 };
    moy_sh_hop(moy_sh_running(), door_here, &d);
    return d.r;
}

static int32_t door_int(const char *verb, int argc, char *const *argv) {
    char *ans;
    size_t n;
    int32_t r = door(verb, argc, argv, &ans, &n);
    b_free(NULL, ans);
    return r;
}

// -- the run in front ------------------------------------------------------------------

// Its pid: the log's current process while it runs with no VM (the Player
// named it before the VM stopped), else 0.
static int front_pid(void) {
    if (moy_play_front_live == NULL || !moy_play_front_live()) {
        return 0;
    }
    uint32_t pid = moy_log_current();
    return pid > SHELL_PID ? (int)pid : 0;
}

// -- processes -----------------------------------------------------------------------

static int b_procs(void *ctx, moy_sh_ps_fn fn, void *arg) {
    (void)ctx;
    moy_sh_ps_t row = { 0, 0, "kernel", "running", "kernel", 0, -1 };
    int r = fn(arg, &row);
    if (r) {
        return r;
    }
    char *ans;
    size_t n;
    if (door("ps", 0, NULL, &ans, &n) < 0) {
        moy_sh_ps_t console = { SHELL_PID, 0, "shell", "stopped", "console", 0, -1 };
        if ((r = fn(arg, &console)) != 0) {
            return r;
        }
        int pid = front_pid();
        if (pid) {
            moy_sh_ps_t run = { pid, SHELL_PID, "app", "running", moy_log_name((uint32_t)pid), 0,
                                -1 };
            return fn(arg, &run);
        }
        return 0;
    }
    // `pid ppid kind state mem cpu name` a line (cpu `-` unmeasured)
    for (char *line = ans; line != NULL && *line && r == 0;) {
        char *end = strchr(line, '\n');
        if (end != NULL) {
            *end = 0;
        }
        char *f[7];
        int k = 0;
        for (char *p = line; k < 7 && p != NULL; k++) {
            f[k] = p;
            p = k < 6 ? strchr(p, ' ') : NULL;
            if (p != NULL) {
                *p++ = 0;
            }
        }
        if (k == 7) {
            moy_sh_ps_t p = { atoi(f[0]), atoi(f[1]), f[2], f[3], f[6],
                              (uint32_t)strtoul(f[4], NULL, 10),
                              f[5][0] == '-' ? -1 : (int32_t)atoi(f[5]) };
            r = fn(arg, &p);
        }
        line = end != NULL ? end + 1 : NULL;
    }
    b_free(NULL, ans);
    return r;
}

static int b_kill(void *ctx, int pid, int sig) {
    (void)ctx;
    if (pid == 0) {
        return EPERM;
    }
    if (pid == front_pid()) {
        if (sig != 0 && moy_play_front_end != NULL) {
            moy_play_front_end(2);      // MOY_PLAY_END_MENU
        }
        return 0;
    }
    char a[12], b[12];
    snprintf(a, sizeof a, "%d", pid);
    snprintf(b, sizeof b, "%d", sig);
    char *argv[2] = { a, b };
    int32_t r = door_int("kill", 2, argv);
    if (r == DOOR_NEEDS_VM) {
        return pid == SHELL_PID ? EPERM : ESRCH;
    }
    return r < 0 ? ESRCH : (int)r;
}

static int b_status(void *ctx, int pid, int *code) {
    (void)ctx;
    if (pid == 0 || pid == front_pid()) {
        return 0;
    }
    char a[12];
    snprintf(a, sizeof a, "%d", pid);
    char *argv[1] = { a };
    char *ans;
    size_t n;
    int32_t r = door("status", 1, argv, &ans, &n);
    int st = -1;
    if (r >= 0) {
        if (strncmp(ans, "running", 7) == 0) {
            st = 0;
        } else if (strncmp(ans, "exited ", 7) == 0) {
            *code = atoi(ans + 7);
            st = 1;
        }
    } else if (r == DOOR_NEEDS_VM && pid == SHELL_PID) {
        st = 0;
    }
    b_free(NULL, ans);
    return st;
}

// -- commands ---------------------------------------------------------------------------

static int b_lookup(void *ctx, const char *name) {
    (void)ctx;
    char *argv[1] = { (char *)name };
    return door_int("lookup", 1, argv) == 1;
}

static int code_of(int32_t r) {
    if (r == DOOR_NEEDS_VM) {
        return MOY_SH_NEEDS_VM;
    }
    if (r == DOOR_ABSENT) {
        return MOY_SH_NOT_FOUND;
    }
    return r < 0 ? 1 : (int)r;
}

static int b_spawn(void *ctx, moy_sh_t *s, int argc, char **argv, int bg, int *pid) {
    (void)ctx;
    (void)s;
    char **all = b_alloc(NULL, ((size_t)argc + 1) * sizeof *all);
    if (all == NULL) {
        return 1;
    }
    all[0] = bg ? "1" : "0";
    memcpy(all + 1, argv, (size_t)argc * sizeof *argv);
    int32_t r = door_int("spawn", argc + 1, all);
    b_free(NULL, all);
    if (bg && r > 0) {
        *pid = (int)r;
        return 0;
    }
    return code_of(r);
}

// The commands the console serves: runtime/shell/sh_roles.py has an r_<name>
// for each (tests/test_sh_door.py holds the two equal).
static const char *const ROLES[] = {
    "ble", "http", "lease", "lora", "media", "mic", "midi", "notify", "perm",
    "passwd", "pkg", "python", "service", "settings", "snd", "usb", NULL,
};

// The files tree's root, as moy_sh_board_host gives it.
static const char *files_root(void) {
#ifdef MOY_SH_ON_BOARD
    return "";
#else
    return getenv("MOY_SH_ROOT") != NULL ? getenv("MOY_SH_ROOT") : "";
#endif
}

static void b_sleep(void *ctx, int ms);

typedef struct {
    moy_sh_t *s;
    int argc;
    char **argv;
    int r;
} store_role_t;

static void store_role_here(void *arg) {
    store_role_t *c = arg;
    char store[MOY_SH_STORE_PATH];
    snprintf(store, sizeof store, "%s%s", files_root(), moy_sh_board_store(c->s));
    c->r = moy_sh_store_role(c->s, store, c->argc, c->argv);
}

// A role command is a job the console steps: `role JOB ARGV...` starts it
// and runs its first step, `step JOB` each one after, while it answers
// RUNNING; between them the session waits (b_sleep: a runner's run parks,
// so the console's frame goes on). Ctrl-C `stop`s it. JOB names the session.
static int b_role(void *ctx, moy_sh_t *s, int argc, char **argv) {
    (void)ctx;
    char job[2 * sizeof(uintptr_t) + 1];
    snprintf(job, sizeof job, "%lx", (unsigned long)(uintptr_t)s);
    char **all = b_alloc(NULL, ((size_t)argc + 1) * sizeof *all);
    if (all == NULL) {
        return 1;
    }
    all[0] = job;
    memcpy(all + 1, argv, (size_t)argc * sizeof *argv);
    int32_t r = door_int("role", argc + 1, all);
    b_free(NULL, all);
    if (r == DOOR_NEEDS_VM) {
        store_role_t c = { s, argc, argv, MOY_SH_NOT_FOUND };
        moy_sh_hop(s, store_role_here, &c);
        return c.r != MOY_SH_NOT_FOUND ? c.r : code_of(r);
    }
    char *one[1] = { job };
    while (r == DOOR_RUNNING) {
        if (moy_sh_interrupted(s)) {
            door_int("stop", 1, one);
            return INTERRUPTED;
        }
        b_sleep(NULL, ROLE_POLL_MS);
        r = door_int("step", 1, one);
    }
    return code_of(r);
}

// -- the virtual files ---------------------------------------------------------------------

typedef struct {
    char *buf;
    size_t n, cap;
} text_t;

static void put(text_t *t, const char *p, size_t n) {
    if (t->n + n + 1 > t->cap) {
        size_t cap = (t->n + n + 1) * 2;
        char *q = b_alloc(NULL, cap);
        if (q == NULL) {
            return;
        }
        if (t->buf != NULL) {
            memcpy(q, t->buf, t->n);
            b_free(NULL, t->buf);
        }
        t->buf = q;
        t->cap = cap;
    }
    memcpy(t->buf + t->n, p, n);
    t->n += n;
    t->buf[t->n] = 0;
}

// The log's lines of process `name` (NULL: every one), or of `pid` (>= 0).
static void log_text(text_t *t, const char *name, int pid) {
    moy_log_cursor_t c = { 0, 0 };
    moy_log_rec_t r;
    char text[512], head[96];
    while (moy_log_read(&c, &r, text, sizeof text)) {
        const char *who = moy_log_name(r.pid);
        if ((pid >= 0 && (uint32_t)pid != r.pid) || (name != NULL && strstr(who, name) == NULL)) {
            continue;
        }
        size_t len = r.len < sizeof text ? r.len : sizeof text;
        int k = snprintf(head, sizeof head, "%lu.%03lu %s %s/%lu ",
                         (unsigned long)(r.t_us / 1000000u),
                         (unsigned long)(r.t_us / 1000u % 1000u), moy_log_level_name(r.level),
                         who, (unsigned long)r.pid);
        for (size_t i = 0; i < len;) {
            size_t j = i;
            while (j < len && text[j] != '\n') {
                j++;
            }
            put(t, head, (size_t)k);
            put(t, text + i, j - i);
            put(t, "\n", 1);
            i = j + 1;
        }
    }
}

// devfs.py's /proc/<pid>/log: the text of the process's last LOG_LINES lines.
#define LOG_LINES 64

static void log_lines(text_t *t, uint32_t pid) {
    moy_log_cursor_t c = { 0, 0 };
    moy_log_rec_t r;
    char text[512];
    uint32_t total = 0, at = 0;
    for (int pass = 0; pass < 2; pass++) {
        memset(&c, 0, sizeof c);
        while (moy_log_read(&c, &r, text, sizeof text)) {
            if (r.pid != pid) {
                continue;
            }
            size_t len = r.len < sizeof text ? r.len : sizeof text;
            for (size_t i = 0; i < len;) {
                size_t j = i;
                while (j < len && text[j] != '\n') {
                    j++;
                }
                if (pass == 0) {
                    total++;
                } else if (at++ + LOG_LINES >= total) {
                    put(t, text + i, j - i);
                    put(t, "\n", 1);
                }
                i = j + 1;
            }
        }
    }
}

// The names of the leases `pid` holds, as devfs.py's status names them, or "-".
static void leases_text(text_t *t, uint32_t pid) {
    int any = 0;
    moy_lease_row_t row;
    moy_lease_offer_t o;
    for (uint32_t slot = 0; slot < moy_lease_slots(); slot++) {
        uint32_t h = moy_lease_at(slot);
        if (h == 0 || moy_lease_get(h, &row) != MOY_LEASE_OK || row.owner != pid) {
            continue;
        }
        for (uint32_t i = 0; moy_lease_offer_at(i, &o) == MOY_LEASE_OK; i++) {
            if (o.cls == row.cls && o.unit == row.unit) {
                put(t, any ? " " : "", any ? 1 : 0);
                put(t, o.name, strlen(o.name));
                any = 1;
                break;
            }
        }
    }
    if (!any) {
        put(t, "-", 1);
    }
}

// /proc with no VM: the kernel, the console stopped and the run in front.
static int proc_row(int pid, const char **name, const char **kind, const char **state) {
    if (pid == 0) {
        *name = *kind = "kernel";
        *state = "running";
    } else if (pid == SHELL_PID) {
        *name = "console";
        *kind = "shell";
        *state = "stopped";
    } else if (pid == front_pid()) {
        *name = moy_log_name((uint32_t)pid);
        *kind = "app";
        *state = "running";
    } else {
        return 0;
    }
    return 1;
}

static int b_vread(void *ctx, const char *path, char **p, size_t *n) {
    (void)ctx;
    char *argv[1] = { (char *)path };
    int32_t r = door("read", 1, argv, p, n);
    if (r >= 0) {
        return 0;
    }
    if (r == DOOR_ABSENT) {
        return ENOENT;
    }
    if (r != DOOR_NEEDS_VM) {
        return r == DOOR_FULL ? EFBIG : EIO;
    }
    if (strncmp(path, "/dev/", 5) == 0) {
        return EAGAIN;                  // a device is the console's lease
    }
    int pid;
    char leaf[8];
    if (sscanf(path, "/proc/%d/%7s", &pid, leaf) != 2) {
        return ENOENT;
    }
    const char *name, *kind, *state;
    if (!proc_row(pid, &name, &kind, &state)) {
        return ENOENT;
    }
    text_t t = { NULL, 0, 0 };
    if (strcmp(leaf, "status") == 0) {
        char buf[160];
        int k = snprintf(buf, sizeof buf, "Name:\t%s\nPid:\t%d\nKind:\t%s\nState:\t%s\nLeases:\t",
                         name, pid, kind, state);
        put(&t, buf, (size_t)k);
        leases_text(&t, (uint32_t)pid);
        put(&t, "\n", 1);
    } else if (strcmp(leaf, "log") == 0) {
        log_lines(&t, (uint32_t)pid);
    } else {
        return ENOENT;
    }
    if (t.buf == NULL) {
        put(&t, "", 0);
    }
    *p = t.buf;
    *n = t.n;
    return t.buf != NULL ? 0 : ENOMEM;
}

static int b_vwrite(void *ctx, const char *path, const char *p, size_t n) {
    (void)ctx;
    char *bytes = b_alloc(NULL, n + 1);
    if (bytes == NULL) {
        return ENOMEM;
    }
    memcpy(bytes, p, n);
    bytes[n] = 0;
    char *argv[2] = { (char *)path, bytes };
    int32_t r = door_int("write", 2, argv);
    b_free(NULL, bytes);
    if (r == DOOR_NEEDS_VM) {
        return strncmp(path, "/dev/", 5) == 0 ? EAGAIN : EROFS;
    }
    return r == DOOR_ABSENT ? ENOENT : r < 0 ? EIO : (int)r;
}

static int b_vlist(void *ctx, const char *dir, moy_sh_ent_fn fn, void *arg) {
    (void)ctx;
    char *argv[1] = { (char *)dir };
    char *ans;
    size_t n;
    int32_t r = door("list", 1, argv, &ans, &n);
    if (r >= 0) {
        int stop = 0;
        for (char *name = ans; *name && !stop;) {
            char *end = strchr(name, '\n');
            if (end != NULL) {
                *end = 0;
            }
            size_t len = strlen(name);
            int is_dir = len > 0 && name[len - 1] == '/';
            if (is_dir) {
                name[len - 1] = 0;
            }
            if (*name) {
                stop = fn(arg, name, is_dir, 0);
            }
            if (end == NULL) {
                break;
            }
            name = end + 1;
        }
        b_free(NULL, ans);
        return stop;
    }
    if (r == DOOR_ABSENT) {
        return ENOENT;
    }
    if (strcmp(dir, "/bin") == 0) {
        return 0;                       // the installed commands are the console's
    }
    if (strcmp(dir, "/dev") == 0) {
        moy_lease_offer_t o;
        for (uint32_t i = 0; moy_lease_offer_at(i, &o) == MOY_LEASE_OK; i++) {
            int stop = strncmp(o.name, "spi", 3) != 0 ? fn(arg, o.name, 0, 0) : 0;
            if (stop) {
                return stop;
            }
        }
        return 0;
    }
    if (strcmp(dir, "/proc") == 0) {
        int pids[3] = { 0, SHELL_PID, front_pid() };
        char num[12];
        for (int i = 0; i < 3; i++) {
            if (i == 2 && pids[i] == 0) {
                break;
            }
            snprintf(num, sizeof num, "%d", pids[i]);
            int stop = fn(arg, num, 1, 0);
            if (stop) {
                return stop;
            }
        }
        return 0;
    }
    int pid;
    char rest;
    const char *name, *kind, *state;
    if (sscanf(dir, "/proc/%d%c", &pid, &rest) == 1 && proc_row(pid, &name, &kind, &state)) {
        int stop = fn(arg, "log", 0, 0);
        return stop ? stop : fn(arg, "status", 0, 0);
    }
    return ENOENT;
}

// -- figures, the log, the clock ----------------------------------------------------------------

static int two_figures(const char *verb, const char *path, uint64_t *a, uint64_t *b) {
    char *argv[1] = { (char *)path };
    char *ans;
    size_t n;
    int32_t r = door(verb, path != NULL ? 1 : 0, argv, &ans, &n);
    // strtoull, not sscanf: a board's nano scanf has no 64-bit conversion
    char *end = ans;
    if (r >= 0) {
        *a = strtoull(ans, &end, 10);
        *b = strtoull(end, &end, 10);
    }
    int ok = r >= 0 && end != ans && *end == 0;
    b_free(NULL, ans);
    return ok ? 0 : -1;
}

static int b_mem(void *ctx, uint32_t out[6]) {
    (void)ctx;
    memset(out, 0, 6 * sizeof out[0]);
#ifdef MOY_SH_ON_BOARD
    out[0] = (uint32_t)heap_caps_get_total_size(MALLOC_CAP_INTERNAL);
    out[1] = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    out[2] = (uint32_t)heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    out[3] = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
#endif
    uint64_t total, free;
    if (two_figures("mem", NULL, &total, &free) == 0) {
        out[4] = (uint32_t)total;
        out[5] = (uint32_t)free;
    }
    return out[0] || out[4] ? 0 : -1;
}

static int b_df(void *ctx, const char *path, uint64_t *total, uint64_t *free) {
    (void)ctx;
    return two_figures("df", path, total, free);
}

static void b_reboot(void *ctx) {
    (void)ctx;
#ifdef MOY_SH_ON_BOARD
    esp_restart();
#endif
}

static void nap(int ms) {
#ifdef MOY_SH_ON_BOARD
    vTaskDelay(pdMS_TO_TICKS(ms > 0 ? ms : 0));
#else
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
#endif
}

static moy_sh_runner_t *runner_here(void);
static void park(moy_sh_runner_t *r, int ms);

// On a runner's stack the run parks for `ms` (the frame goes on), else the
// task sleeps.
static void b_sleep(void *ctx, int ms) {
    (void)ctx;
    moy_sh_runner_t *r = runner_here();
    if (r != NULL) {
        park(r, ms);
    } else {
        nap(ms);
    }
}

static int b_log(void *ctx, moy_sh_t *s, int fd, const char *name, int follow) {
    (void)ctx;
    (void)follow;
    text_t t = { NULL, 0, 0 };
    log_text(&t, name, -1);
    if (t.buf != NULL) {
        moy_sh_fd_write(s, fd, t.buf, t.n);
        b_free(NULL, t.buf);
    }
    return 0;
}

void moy_sh_board_yield(void) {
    nap(1);
}

// -- the runner ----------------------------------------------------------------------------
//
// A task of the runner's own runs the session's line, strictly in turn with
// the caller: the caller's task waits while the run's goes, so the session
// and the face's grid have one writer at a time. The run hands the turn back
// to hop (runner_hop: the caller's task runs it and hands the turn back) and
// to park (b_sleep: the caller's task returns MOY_SH_BUSY, and the loop's
// job, `resume`, hands the run its turn again a frame later, once the park's
// time is up).

enum { R_RUN, R_PARKED, R_HOP, R_DONE };

struct moy_sh_runner {
    moy_sh_t *s;
    const char *text;
    size_t n;
    int state;
    int result;
    int abort;                          // the session goes: every park ends at once
    int quit;                           // the task ends
    uint32_t until;                     // ms, a park's end
    void (*fn)(void *arg);              // a hop
    void *arg;
    void (*done)(void *ctx, int r);     // a parked run ended (the face's)
    void *ctx;
    int (*poll)(void *ctx);             // the face's poll
    int live;                           // the task runs
    int inline_runs;                    // every run the caller's
#ifdef MOY_SH_ON_BOARD
    TaskHandle_t task;
    SemaphoreHandle_t go, back;
#else
    pthread_t task;
    pthread_mutex_t m;
    pthread_cond_t c;
    int turn;                           // 1: the run's, 0: the caller's
#endif
};

static uint32_t run_ms(void) {
#ifdef MOY_SH_ON_BOARD
    return (uint32_t)(esp_timer_get_time() / 1000);
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
#endif
}

#ifdef MOY_SH_ON_BOARD
static int on_run_task(const moy_sh_runner_t *r) {
    return r->live && xTaskGetCurrentTaskHandle() == r->task;
}

static void to_run(moy_sh_runner_t *r) {
    xSemaphoreGive(r->go);
    xSemaphoreTake(r->back, portMAX_DELAY);
}

static void to_caller(moy_sh_runner_t *r) {
    xSemaphoreGive(r->back);
    xSemaphoreTake(r->go, portMAX_DELAY);
}
#else
static int on_run_task(const moy_sh_runner_t *r) {
    return r->live && pthread_equal(pthread_self(), r->task);
}

static void pass(moy_sh_runner_t *r, int turn) {
    pthread_mutex_lock(&r->m);
    r->turn = turn;
    pthread_cond_broadcast(&r->c);
    while (r->turn == turn) {
        pthread_cond_wait(&r->c, &r->m);
    }
    pthread_mutex_unlock(&r->m);
}

static void to_run(moy_sh_runner_t *r) {
    pass(r, 1);
}

static void to_caller(moy_sh_runner_t *r) {
    pass(r, 0);
}
#endif

static void run_main(moy_sh_runner_t *r) {
    while (!r->quit) {
        r->result = moy_sh_run(r->s, r->text, r->n);
        r->state = R_DONE;
        to_caller(r);
    }
}

#ifdef MOY_SH_ON_BOARD
static void run_task(void *arg) {
    moy_sh_runner_t *r = arg;
    xSemaphoreTake(r->go, portMAX_DELAY);
    run_main(r);
    xSemaphoreGive(r->back);
    for (;;) {
        vTaskDelay(portMAX_DELAY);      // the caller deletes it
    }
}
#else
static void *run_task(void *arg) {
    moy_sh_runner_t *r = arg;
    pthread_mutex_lock(&r->m);
    while (!r->turn) {
        pthread_cond_wait(&r->c, &r->m);
    }
    pthread_mutex_unlock(&r->m);
    run_main(r);
    pthread_mutex_lock(&r->m);
    r->turn = 0;
    pthread_cond_broadcast(&r->c);
    pthread_mutex_unlock(&r->m);
    return NULL;
}
#endif

// The task, at the first run: 0, or -1 where none can be made (the run is
// then the caller's).
static int run_start(moy_sh_runner_t *r) {
    if (r->live) {
        return 0;
    }
#ifdef MOY_SH_ON_BOARD
    r->go = xSemaphoreCreateBinary();
    r->back = xSemaphoreCreateBinary();
    if (r->go == NULL || r->back == NULL
            || xTaskCreatePinnedToCoreWithCaps(run_task, "moy_sh", RUN_STACK, r,
                                               uxTaskPriorityGet(NULL), &r->task,
                                               xPortGetCoreID(),
                                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        if (r->go != NULL) {
            vSemaphoreDelete(r->go);
        }
        if (r->back != NULL) {
            vSemaphoreDelete(r->back);
        }
        r->go = r->back = NULL;
        return -1;
    }
#else
    pthread_mutex_init(&r->m, NULL);
    pthread_cond_init(&r->c, NULL);
    r->turn = 0;
    if (pthread_create(&r->task, NULL, run_task, r) != 0) {
        pthread_cond_destroy(&r->c);
        pthread_mutex_destroy(&r->m);
        return -1;
    }
#endif
    r->live = 1;
    return 0;
}

// The runner whose task this is, while its session runs.
static moy_sh_runner_t *runner_here(void) {
    moy_sh_t *s = moy_sh_running();
    moy_sh_runner_t *r = s != NULL ? moy_sh_host(s)->runner : NULL;
    return r != NULL && on_run_task(r) ? r : NULL;
}

static void park(moy_sh_runner_t *r, int ms) {
    if (r->abort) {
        return;
    }
    r->until = run_ms() + (uint32_t)(ms > 0 ? ms : 0);
    r->state = R_PARKED;
    to_caller(r);
}

static void runner_hop(void *ctx, void (*fn)(void *arg), void *arg) {
    (void)ctx;
    moy_sh_runner_t *r = runner_here();
    if (r == NULL) {
        fn(arg);
        return;
    }
    r->fn = fn;
    r->arg = arg;
    r->state = R_HOP;
    to_caller(r);
}

static int runner_poll(void *ctx) {
    moy_sh_t *s = moy_sh_running();
    moy_sh_runner_t *r = s != NULL ? moy_sh_host(s)->runner : NULL;
    if (r != NULL && r->abort) {
        return 1;
    }
    return r != NULL && r->poll != NULL ? r->poll(ctx) : 0;
}

// The run's turns until it parks or ends, the hops it asks run here.
static int turn(moy_sh_runner_t *r) {
    for (;;) {
        r->state = R_RUN;
        to_run(r);
        if (r->state == R_HOP) {
            r->fn(r->arg);
            continue;
        }
        return r->state == R_PARKED ? MOY_SH_BUSY : r->result;
    }
}

// The loop's job while a run is parked: its turn once the park is up.
static bool resume(void *arg) {
    moy_sh_runner_t *r = arg;
    if (r->state != R_PARKED) {
        return false;
    }
    if (!r->abort && (int32_t)(run_ms() - r->until) < 0) {
        return true;
    }
    int k = turn(r);
    if (k == MOY_SH_BUSY) {
        return true;
    }
    if (r->done != NULL) {
        r->done(r->ctx, k);
    }
    return false;
}

moy_sh_runner_t *moy_sh_board_runner(moy_sh_host_t *h, void (*done)(void *ctx, int r)) {
    if (moy_loop_job == NULL) {
        return NULL;
    }
    moy_sh_runner_t *r = b_alloc(NULL, sizeof *r);
    if (r == NULL) {
        return NULL;
    }
    memset(r, 0, sizeof *r);
    r->state = R_DONE;
    r->done = done;
    r->ctx = h->ctx;
    r->poll = h->poll;
    h->poll = runner_poll;
    h->hop = runner_hop;
    h->runner = r;
    return r;
}

int moy_sh_board_run(moy_sh_t *s, const char *text, size_t n) {
    moy_sh_runner_t *r = moy_sh_host(s)->runner;
    if (r == NULL || r->inline_runs || !moy_loop_vm() || r->abort || run_start(r) != 0) {
        return moy_sh_run(s, text, n);
    }
    if (r->state != R_DONE) {
        return MOY_SH_BUSY;
    }
    r->s = s;
    r->text = text;
    r->n = n;
    int k = turn(r);
    if (k == MOY_SH_BUSY && !moy_loop_job(resume, r)) {
        while ((k = turn(r)) == MOY_SH_BUSY) {
            int32_t left = (int32_t)(r->until - run_ms());
            nap(left > 0 ? (int)left : 0);
        }
    }
    return k;
}

void moy_sh_board_runner_inline(moy_sh_runner_t *r) {
    if (r != NULL) {
        r->inline_runs = 1;
    }
}

int moy_sh_board_busy(const moy_sh_t *s) {
    const moy_sh_runner_t *r = moy_sh_host(s)->runner;
    return r != NULL && r->state == R_PARKED;
}

void moy_sh_board_runner_end(moy_sh_runner_t *r) {
    if (r == NULL) {
        return;
    }
    if (r->state == R_PARKED) {
        r->abort = 1;
        for (int i = 0; i < ABORT_TURNS && r->state == R_PARKED; i++) {
            turn(r);
        }
    }
    if (moy_loop_job_drop != NULL) {
        moy_loop_job_drop(resume, r);
    }
    if (r->live && r->state != R_PARKED) {
        r->quit = 1;
#ifdef MOY_SH_ON_BOARD
        to_run(r);
        vTaskDeleteWithCaps(r->task);
        vSemaphoreDelete(r->go);
        vSemaphoreDelete(r->back);
#else
        pthread_mutex_lock(&r->m);
        r->turn = 1;
        pthread_cond_broadcast(&r->c);
        pthread_mutex_unlock(&r->m);
        pthread_join(r->task, NULL);
        pthread_cond_destroy(&r->c);
        pthread_mutex_destroy(&r->m);
#endif
    }
    // a run that would not end keeps its task and its record
    if (r->state != R_PARKED) {
        b_free(NULL, r);
    }
}

// The stores a console boots on, in the order the console's boot tries them
// (device/card_store.py's carts_loader: the card, else the internal flash).
#define STORES(X) X("/sd/moybyte") X("/moy") X("")

static int store_at(moy_sh_t *s) {
    const char *const carts[] = {
#define CARTS(p) p "/carts",
        STORES(CARTS)
#undef CARTS
    };
    for (int i = 0; i < (int)(sizeof carts / sizeof carts[0]); i++) {
        if (moy_sh_is_dir(s, carts[i])) {
            return i;
        }
    }
    return 0;
}

const char *moy_sh_board_store(moy_sh_t *s) {
    const char *const stores[] = {
#define STORE(p) p,
        STORES(STORE)
#undef STORE
    };
    return stores[store_at(s)];
}

int moy_sh_board_go_home(moy_sh_t *s, int keep_etc) {
    const char *const systems[] = {
#define SYSTEM(p) p "/system",
        STORES(SYSTEM)
#undef SYSTEM
    };
    const char *const homes[] = {
#define HOME(p) p "/files",
        STORES(HOME)
#undef HOME
    };
    int i = store_at(s);
    if (!keep_etc) {
        moy_sh_set_etc(s, systems[i]);
    }
    const char *dirs[] = { homes[i], NULL };
    return moy_sh_go_home(s, dirs);
}

void moy_sh_board_home_late(moy_sh_t *s, int *homed) {
    if (!*homed && !moy_sh_board_busy(s) && strcmp(moy_sh_cwd(s), "/") == 0) {
        *homed = moy_sh_board_go_home(s, 0) >= 0;
    }
}

void moy_sh_board_host(moy_sh_host_t *h, void *ctx,
                       void (*out)(void *ctx, int stream, const char *p, size_t n),
                       int (*poll)(void *ctx)) {
    memset(h, 0, sizeof *h);
    h->ctx = ctx;
    h->alloc = b_alloc;
    h->free = b_free;
    h->out = out;
    h->poll = poll;
    // off a board (the suite's harness) the files tree is a folder it names
    h->root = files_root();
    h->etc = "/sd/moybyte/system";      // until the session settles (moy_sh_board_go_home)
    h->vread = b_vread;
    h->vwrite = b_vwrite;
    h->vlist = b_vlist;
    h->lookup = b_lookup;
    h->spawn = b_spawn;
    h->status = b_status;
    h->kill = b_kill;
    h->procs = b_procs;
    h->roles = ROLES;
    h->role = b_role;
    h->mem = b_mem;
    h->df = b_df;
    h->reboot = b_reboot;
    h->sleep_ms = b_sleep;
    h->log = b_log;
}

// -- the serial face ------------------------------------------------------------------------------

typedef struct {
    char line[MOY_SH_SERIAL_LINE + 4];  // "SH " and the output's line
    size_t n;
    uint32_t yielded;                   // ms, the last time the poll yielded
    int stop;                           // a Ctrl-C byte the dev channel read
    moy_sh_t *s;
    moy_sh_app_t *app;                  // the text app in front (moy_sh_app.h)
    moy_sh_runner_t *runner;
    int homed;                          // the session has reached its home
} serial_t;

static void serial_flush(serial_t *f) {
    f->line[3 + f->n] = 0;
    moy_loop_say_line(f->line);
    f->n = 0;
}

static void serial_out(void *ctx, int stream, const char *p, size_t n) {
    (void)stream;
    serial_t *f = ctx;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n') {
            serial_flush(f);
        } else if (p[i] != '\r') {
            f->line[3 + f->n++] = p[i];
            if (f->n == MOY_SH_SERIAL_LINE) {
                serial_flush(f);
            }
        }
    }
}

// Ctrl-C (0x03) on the channel interrupts; every other byte goes back to the
// reader for the lines after this one. The loop's task yields now and then, so
// a long command starves nothing below it.
static int serial_poll(void *ctx) {
    serial_t *f = ctx;
    const moy_loop_ops_t *ops = moy_loop_ops();
    int stop = f->stop;
    f->stop = 0;
    if (ops != NULL && ops->getc != NULL) {
        for (int i = 0; i < POLL_BYTES; i++) {
            int c = ops->getc();
            if (c < 0) {
                break;
            }
            if (c == 3) {
                stop = 1;
                break;
            }
            uint8_t b = (uint8_t)c;
            moy_devch_unread(&b, 1);
        }
        if (ops->ticks_ms != NULL) {
            uint32_t now = ops->ticks_ms();
            if (now - f->yielded >= YIELD_MS) {
                f->yielded = now;
                b_sleep(NULL, 1);
            }
        }
    }
    return stop;
}

static int serial_run(void *ctx, const char *text, size_t n) {
    serial_t *f = ctx;
    return moy_sh_run(f->s, text, n);
}

static int serial_app(void *ctx, moy_sh_t *s, int argc, char **argv) {
    serial_t *f = ctx;
    if (f->app != NULL || moy_sh_app_find(argv[0]) == NULL) {
        return f->app != NULL ? 1 : MOY_SH_NOT_FOUND;
    }
    int status;
    f->app = moy_sh_app_open(s, MOY_SH_SERIAL_COLS, MOY_SH_SERIAL_ROWS, 1, serial_run, f,
                             argc, argv, &status);
    return status;
}

static uint32_t serial_ms(void) {
    const moy_loop_ops_t *ops = moy_loop_ops();
    return ops != NULL && ops->ticks_ms != NULL ? ops->ticks_ms() : 0;
}

// What the app shows: a full-screen one's screen a row a line, its inks
// dropped, then `SH app NAME`.
static void serial_show(serial_t *f) {
    moy_sh_app_t *a = f->app;
    moy_sh_app_tick(a, serial_ms());
    moy_term_t *t = moy_sh_app_term(a);
    if (t != NULL) {
        uint32_t v[3];
        moy_term_take(t, v);
        char row[MOY_SH_SERIAL_LINE * 2];
        for (int i = 0; i < (int)v[1]; i++) {
            size_t n = moy_term_line(t, i, row, sizeof row), k = 0;
            for (size_t j = 0; j < n; j++) {
                if (row[j] == 0x1b) {
                    while (j < n && row[j] != 'm') {
                        j++;
                    }
                } else if (k < MOY_SH_SERIAL_LINE) {
                    f->line[3 + k++] = row[j];
                }
            }
            f->n = k;
            serial_flush(f);
        }
    }
    moy_loop_say("SH app %s", moy_sh_app_ops(a)->name);
}

// A serial line to the app in front: a full-screen app's keys in caret
// notation (^X a control key, ^[ Esc, so ^[[A is up; ^? Delete; ^^ a caret),
// a line app's line (^D ends it).
static void serial_to_app(serial_t *f, const char *text) {
    moy_sh_app_t *a = f->app;
    char keys[MOY_SH_SERIAL_LINE];
    size_t k = 0;
    for (const char *p = text; *p && k < sizeof keys; p++) {
        if (p[0] == '^' && p[1]) {
            p++;
            keys[k++] = *p == '?' ? 0x7F : *p == '^' ? '^' : (char)(*p & 0x1F);
        } else {
            keys[k++] = *p;
        }
    }
    int r;
    if (moy_sh_app_ops(a)->full || (k == 1 && keys[0] < 0x20)) {
        r = moy_sh_app_keys(a, keys, k);
    } else {
        r = moy_sh_app_line(a, text);
    }
    if (f->n) {
        serial_flush(f);
    }
    if (r == MOY_SH_APP_END) {
        f->app = NULL;
        moy_sh_app_close(a);
        moy_loop_say("SH end %d", 0);
    } else {
        serial_show(f);
    }
}

// A run's end, said: at once, or a parked one's when it ends.
static void serial_end(void *ctx, int r) {
    serial_t *f = ctx;
    moy_sh_t *s = f->s;
    if (f->n) {
        serial_flush(f);
    }
    if (r == MOY_SH_MORE) {
        moy_loop_say_line("SH more");
    } else if (r == MOY_SH_EXIT) {
        moy_loop_say("SH exit %d", moy_sh_status(s));
        moy_sh_app_close(f->app);
        moy_sh_board_runner_end(f->runner);
        moy_sh_free(s);
        b_free(NULL, f);
    } else if (f->app != NULL) {
        serial_show(f);
    } else {
        moy_loop_say("SH end %d", moy_sh_status(s));
    }
}

bool moy_sh_board_break(void) {
    moy_sh_t *s = moy_sh_running();
    if (s == NULL || !moy_sh_board_busy(s)) {
        return false;
    }
    ((serial_t *)moy_sh_ctx(s))->stop = 1;
    return true;
}

bool moy_sh_board_line(const char *text) {
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        serial_t *f = b_alloc(NULL, sizeof *f);
        if (f == NULL) {
            moy_loop_say_line("SH error no memory");
            return false;
        }
        memset(f, 0, sizeof *f);
        memcpy(f->line, "SH ", 3);
        moy_sh_host_t h;
        moy_sh_board_host(&h, f, serial_out, serial_poll);
        h.app = serial_app;
        f->runner = moy_sh_board_runner(&h, serial_end);
        s = moy_sh_new(&h, MOY_SH_ARENA, "/", "/");
        if (s == NULL) {
            moy_sh_board_runner_end(f->runner);
            b_free(NULL, f);
            moy_loop_say_line("SH error no session");
            return false;
        }
        f->homed = moy_sh_board_go_home(s, 0) >= 0;
        moy_sh_setvar(s, "TERM", "dumb");  // the channel carries lines, not a screen
        f->s = s;
        moy_sh_park(s);
    }
    serial_t *f = moy_sh_ctx(s);
    moy_sh_board_home_late(s, &f->homed);
    if (moy_sh_board_busy(s)) {
        if (strcmp(text, "^C") == 0) {
            f->stop = 1;
        } else {
            moy_loop_say_line("SH busy");
        }
        return true;
    }
    if (f->app != NULL) {
        serial_to_app(f, text);
        return true;
    }
    size_t n = strlen(text);
    char *buf = b_alloc(NULL, n + 1);
    if (buf == NULL) {
        moy_loop_say_line("SH error no memory");
        return false;
    }
    memcpy(buf, text, n);
    buf[n] = '\n';
    int r = moy_sh_board_run(s, buf, n + 1);
    b_free(NULL, buf);
    if (r != MOY_SH_BUSY) {
        serial_end(f, r);
    }
    return true;
}

void moy_sh_board_end(void) {
    moy_sh_t *s = moy_sh_running();
    if (s != NULL) {
        serial_t *f = moy_sh_ctx(s);
        moy_sh_app_close(f->app);
        moy_sh_board_runner_end(f->runner);
        moy_sh_free(s);
        b_free(NULL, f);
    }
}
