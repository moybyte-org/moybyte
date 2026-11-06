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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#else
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

#define DOOR_ABSENT (-8)
#define DOOR_NEEDS_VM (-9)
#define DOOR_FULL (-2)
#define ANSWER_CAP 8192u
#define SHELL_PID 1
#define POLL_BYTES 64
#define YIELD_MS 200u

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
// buffer from b_alloc (*ans, freed by the caller) of *n bytes.
static int32_t door(const char *verb, int argc, char *const *argv, char **ans, size_t *n) {
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

static int b_role(void *ctx, moy_sh_t *s, int argc, char **argv) {
    (void)ctx;
    int32_t r = door_int("role", argc, argv);
    if (r == DOOR_NEEDS_VM) {
        int k = moy_sh_store_role(s, files_root(), argc, argv);
        if (k != MOY_SH_NOT_FOUND) {
            return k;
        }
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

static void b_sleep(void *ctx, int ms) {
    (void)ctx;
#ifdef MOY_SH_ON_BOARD
    vTaskDelay(pdMS_TO_TICKS(ms > 0 ? ms : 0));
#else
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
#endif
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
    b_sleep(NULL, 1);
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
    h->etc = MOY_SH_STORE "/system";
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
    moy_sh_t *s;
    moy_sh_app_t *app;                  // the text app in front (moy_sh_app.h)
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
    int stop = 0;
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
        s = moy_sh_new(&h, MOY_SH_ARENA, "/", "/");
        if (s == NULL) {
            b_free(NULL, f);
            moy_loop_say_line("SH error no session");
            return false;
        }
        f->s = s;
        moy_sh_park(s);
    }
    serial_t *f = moy_sh_ctx(s);
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
    int r = moy_sh_run(s, buf, n + 1);
    b_free(NULL, buf);
    if (f->n) {
        serial_flush(f);
    }
    if (r == MOY_SH_MORE) {
        moy_loop_say_line("SH more");
    } else if (r == MOY_SH_EXIT) {
        moy_loop_say("SH exit %d", moy_sh_status(s));
        moy_sh_app_close(f->app);
        moy_sh_free(s);
        b_free(NULL, f);
    } else if (f->app != NULL) {
        serial_show(f);
    } else {
        moy_loop_say("SH end %d", moy_sh_status(s));
    }
    return true;
}

void moy_sh_board_end(void) {
    moy_sh_t *s = moy_sh_running();
    if (s != NULL) {
        serial_t *f = moy_sh_ctx(s);
        moy_sh_app_close(f->app);
        moy_sh_free(s);
        b_free(NULL, f);
    }
}
