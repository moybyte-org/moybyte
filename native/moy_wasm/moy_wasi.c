// moy_wasi: WASI preview1's imports over the shell's C (moy_wasi.h).

#ifndef ESP_PLATFORM
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#endif

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "moy_wasi.h"
#include "moy_sh_board.h"
#include "moy_log.h"

// A board is told by the header it has: ESP_PLATFORM is not defined for a
// usermod's sources on the esp32 port (moy_net.h's MOY_NET_CLOCK_EPOCH).
#if __has_include("esp_heap_caps.h")
#define MOY_WASI_BOARD 1
#endif

#ifdef MOY_WASI_BOARD
#include <sys/time.h>
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#else
#include <sys/random.h>
#endif

// -- WASI's numbers (preview1's witx) ---------------------------------------------

enum {
    W_OK = 0, W_2BIG = 1, W_ACCES = 2, W_AGAIN = 6, W_BADF = 8, W_BUSY = 10, W_EXIST = 20,
    W_FAULT = 21, W_FBIG = 22, W_INTR = 27, W_INVAL = 28, W_IO = 29, W_ISDIR = 31,
    W_LOOP = 32, W_MFILE = 33, W_NAMETOOLONG = 37, W_NOENT = 44, W_NOMEM = 48,
    W_NOSPC = 51, W_NOSYS = 52, W_NOTDIR = 54, W_NOTEMPTY = 55, W_NOTSOCK = 57,
    W_NOTSUP = 58, W_PERM = 63, W_PIPE = 64, W_ROFS = 69, W_SPIPE = 70, W_XDEV = 75,
    W_NOTCAPABLE = 76,
};

enum { FT_UNKNOWN = 0, FT_CHAR = 2, FT_DIR = 3, FT_FILE = 4 };

#define R_FD_READ (1ull << 1)
#define R_FD_SEEK (1ull << 2)
#define R_FD_TELL (1ull << 5)
#define R_FD_WRITE (1ull << 6)
#define R_ALL 0x1fffffffull

#define O_CREAT_W 1
#define O_DIRECTORY_W 2
#define O_EXCL_W 4
#define O_TRUNC_W 8
#define FDF_APPEND 1

#define EXIT_MSG "moy_wasi: exit"
#define PATH_CAP 512
#define LINE_CAP 200
#define MAGIC 0x77617369u

enum { K_FREE = 0, K_STREAM, K_DIR };

typedef struct {
    int kind;
    int sh;                     // K_STREAM: the session's descriptor
    char *path;                 // the shell path it was opened by (a K_DIR's folder)
    char *name;                 // a preopen's name, else NULL
    uint16_t flags;             // fdflags
} wfd_t;

struct moy_wasi {
    uint32_t magic;
    moy_sh_t *s;
    moy_wasi_hop_fn hop;
    int argc;
    char **argv;                // one block: the pointers, then the strings
    size_t args_n;              // the strings' bytes, NULs included
    char *env;                  // "K=V\0" each
    size_t env_n;
    int envc;
    char *roots;                // absolute shell paths, NUL-separated, an empty one last
    wfd_t fd[MOY_WASI_FDS];
    uint32_t bg_pid;
    char line[2][LINE_CAP];
    size_t line_n[2];
    int64_t deadline_us;        // 0: none
    int64_t polled_us;
    uint32_t budget_ms;
    int exited;
    int code;
};

// -- the host's pieces ------------------------------------------------------------

static void *w_alloc(size_t n) {
#ifdef MOY_WASI_BOARD
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return p != NULL ? p : calloc(1, n);
#else
    return calloc(1, n);
#endif
}

static void w_free(void *p) {
    free(p);
}

static char *w_strdup(const char *p) {
    size_t n = strlen(p) + 1;
    char *q = w_alloc(n);
    if (q != NULL) {
        memcpy(q, p, n);
    }
    return q;
}

static int64_t mono_us(void) {
#ifdef MOY_WASI_BOARD
    return esp_timer_get_time();
#else
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000000 + t.tv_nsec / 1000;
#endif
}

// Nanoseconds since 1970. A board's clock counts from 2000 (moy_wifi.c), as
// `date` reads it.
static uint64_t real_ns(void) {
#ifdef MOY_WASI_BOARD
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return ((uint64_t)tv.tv_sec + 946684800u) * 1000000000ull + (uint64_t)tv.tv_usec * 1000u;
#else
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
#endif
}

static void fill_random(uint8_t *p, size_t n) {
#ifdef MOY_WASI_BOARD
    esp_fill_random(p, n);
#else
    while (n > 0) {
        ssize_t got = getrandom(p, n, 0);
        if (got <= 0) {
            break;
        }
        p += got;
        n -= (size_t)got;
    }
#endif
}

static uint16_t wasi_errno(int e) {
    switch (e < 0 ? -e : e) {
        case 0: return W_OK;
        case E2BIG: return W_2BIG;
        case EACCES: return W_ACCES;
        case EAGAIN: return W_AGAIN;
        case EBADF: return W_BADF;
        case EBUSY: return W_BUSY;
        case EEXIST: return W_EXIST;
        case EFAULT: return W_FAULT;
        case EFBIG: return W_FBIG;
        case EINTR: return W_INTR;
        case EINVAL: return W_INVAL;
        case EISDIR: return W_ISDIR;
        case ELOOP: return W_LOOP;
        case EMFILE: return W_MFILE;
        case ENAMETOOLONG: return W_NAMETOOLONG;
        case ENOENT: return W_NOENT;
        case ENOMEM: return W_NOMEM;
        case ENOSPC: return W_NOSPC;
        case ENOSYS: return W_NOSYS;
        case ENOTDIR: return W_NOTDIR;
        case ENOTEMPTY: return W_NOTEMPTY;
        case EPERM: return W_PERM;
        case EPIPE: return W_PIPE;
        case EROFS: return W_ROFS;
        case ESPIPE: return W_SPIPE;
        case EXDEV: return W_XDEV;
        default: return W_IO;
    }
}

// -- a run's record ----------------------------------------------------------------

static int env_add(void *arg, const char *kv) {
    moy_wasi_t *w = arg;
    size_t n = strlen(kv) + 1;
    char *e = w_alloc(w->env_n + n);
    if (e == NULL) {
        return ENOMEM;
    }
    if (w->env != NULL) {
        memcpy(e, w->env, w->env_n);
        w_free(w->env);
    }
    memcpy(e + w->env_n, kv, n);
    w->env = e;
    w->env_n += n;
    w->envc++;
    return 0;
}

static int fd_take(moy_wasi_t *w, int from) {
    for (int i = from; i < MOY_WASI_FDS; i++) {
        if (w->fd[i].kind == K_FREE) {
            return i;
        }
    }
    return -1;
}

static int preopen(moy_wasi_t *w, const char *name, const char *path) {
    int i = fd_take(w, 3);
    if (i < 0) {
        return -1;
    }
    w->fd[i].kind = K_DIR;
    w->fd[i].sh = -1;
    w->fd[i].name = w_strdup(name);
    w->fd[i].path = w_strdup(path);
    return w->fd[i].name != NULL && w->fd[i].path != NULL ? 0 : -1;
}

moy_wasi_t *moy_wasi_new(moy_sh_t *s, int argc, const char *const *argv, const char *roots,
                         uint32_t bg_pid, uint32_t budget_ms, moy_wasi_hop_fn hop) {
    moy_wasi_t *w = w_alloc(sizeof *w);
    if (w == NULL) {
        return NULL;
    }
    w->magic = MAGIC;
    w->s = s;
    w->hop = hop;
    w->bg_pid = bg_pid;
    w->budget_ms = budget_ms;
    w->deadline_us = budget_ms ? mono_us() + (int64_t)budget_ms * 1000 : 0;
    w->argc = argc;
    for (int i = 0; i < argc; i++) {
        w->args_n += strlen(argv[i]) + 1;
    }
    w->argv = w_alloc((size_t)argc * sizeof(char *) + w->args_n + 1);
    if (w->argv == NULL) {
        moy_wasi_free(w);
        return NULL;
    }
    char *at = (char *)(w->argv + argc);
    for (int i = 0; i < argc; i++) {
        size_t n = strlen(argv[i]) + 1;
        memcpy(at, argv[i], n);
        w->argv[i] = at;
        at += n;
    }
    for (int i = 0; i < 3; i++) {
        w->fd[i].kind = K_STREAM;
        w->fd[i].sh = i;
    }
    char cwd[PATH_CAP];
    snprintf(cwd, sizeof cwd, "%s", moy_sh_cwd(s));
    if (moy_sh_environ(s, env_add, w) != 0 || preopen(w, ".", cwd) != 0) {
        moy_wasi_free(w);
        return NULL;
    }
    const char *store = moy_sh_board_store(s);
    size_t rn = 1;
    for (const char *r = roots; r != NULL && *r; r += strlen(r) + 1) {
        rn += strlen(store) + 1 + strlen(r) + 1;
    }
    w->roots = w_alloc(rn);
    if (w->roots == NULL) {
        moy_wasi_free(w);
        return NULL;
    }
    char *o = w->roots;
    for (const char *r = roots; r != NULL && *r; r += strlen(r) + 1) {
        char path[PATH_CAP];
        snprintf(path, sizeof path, "%s/%s", store, r);
        if (moy_sh_abs(s, path, o, rn - (size_t)(o - w->roots)) != 0) {
            continue;
        }
        if (preopen(w, o, o) != 0) {
            moy_wasi_free(w);
            return NULL;
        }
        o += strlen(o) + 1;
    }
    *o = 0;
    return w;
}

static void bg_flush(moy_wasi_t *w, int k) {
    if (w->line_n[k] > 0) {
        moy_log_write(k ? MOY_LOG_ERR : MOY_LOG_INFO, w->bg_pid, w->line[k], w->line_n[k]);
        w->line_n[k] = 0;
    }
}

void moy_wasi_free(moy_wasi_t *w) {
    if (w == NULL) {
        return;
    }
    if (w->bg_pid) {
        bg_flush(w, 0);
        bg_flush(w, 1);
    }
    for (int i = 0; i < MOY_WASI_FDS; i++) {
        if (w->fd[i].kind == K_STREAM && i >= 3 && w->fd[i].sh >= 3) {
            moy_sh_fd_close(w->s, w->fd[i].sh);
        }
        w_free(w->fd[i].path);
        w_free(w->fd[i].name);
    }
    w_free(w->argv);
    w_free(w->env);
    w_free(w->roots);
    w->magic = 0;
    w_free(w);
}

// -- a call --------------------------------------------------------------------------

typedef struct call call_t;
typedef void (*body_fn)(call_t *c);

struct call {
    moy_wasi_t *w;
    wasm_module_inst_t inst;
    body_fn fn;
    uint32_t a[9];
    uint64_t b[2];
    uint32_t r;
};

static void say(moy_wasi_t *w, const char *text) {
    moy_sh_fd_write(w->s, 2, text, strlen(text));
}

// The run's end as proc_exit's, from inside a call.
static void stop(moy_wasi_t *w, int code) {
    w->exited = 1;
    w->code = code;
}

static void run_body(void *arg) {
    call_t *c = arg;
    moy_wasi_t *w = c->w;
    int64_t now = mono_us();
    if (!w->exited && now - w->polled_us >= MOY_WASI_POLL_US) {
        w->polled_us = now;
        if (moy_sh_interrupted(w->s)) {
            stop(w, MOY_WASI_INTERRUPTED);
        } else if (w->deadline_us && now >= w->deadline_us) {
            char msg[64];
            snprintf(msg, sizeof msg, "killed: ran past %u s\n", (unsigned)(w->budget_ms / 1000));
            say(w, msg);
            stop(w, MOY_WASI_KILLED);
        }
    }
    if (w->exited) {
        c->r = W_INTR;
        return;
    }
    c->fn(c);
}

static uint32_t go(wasm_exec_env_t env, body_fn fn, call_t *c) {
    moy_wasi_t *w = (moy_wasi_t *)wasm_runtime_get_user_data(env);
    if (w == NULL || w->magic != MAGIC) {
        return W_NOSYS;
    }
    c->w = w;
    c->inst = wasm_runtime_get_module_inst(env);
    c->fn = fn;
    c->r = W_NOSYS;
    if (w->hop == NULL) {
        run_body(c);
    } else if (w->hop(run_body, c) != 0) {
        c->r = W_IO;                    // no task to touch the session on
    }
    if (w->exited) {
        wasm_runtime_set_exception(c->inst, EXIT_MSG);
    }
    return c->r;
}

// `n` bytes of the module's memory at `off`, or NULL.
static void *mem(call_t *c, uint32_t off, uint64_t n) {
    uint64_t lo, hi;
    if (!wasm_runtime_get_app_addr_range(c->inst, off, &lo, &hi) || (uint64_t)off + n > hi) {
        return NULL;
    }
    return wasm_runtime_addr_app_to_native(c->inst, off);
}

static bool put32(call_t *c, uint32_t off, uint32_t v) {
    void *p = mem(c, off, 4);
    if (p != NULL) {
        memcpy(p, &v, 4);
    }
    return p != NULL;
}

static bool put64(call_t *c, uint32_t off, uint64_t v) {
    void *p = mem(c, off, 8);
    if (p != NULL) {
        memcpy(p, &v, 8);
    }
    return p != NULL;
}

static wfd_t *fd_of(call_t *c, uint32_t fd) {
    return fd < MOY_WASI_FDS && c->w->fd[fd].kind != K_FREE ? &c->w->fd[fd] : NULL;
}

static bool under(const char *path, const char *dir) {
    size_t n = strlen(dir);
    if (n == 1 && dir[0] == '/') {
        return true;
    }
    return strncmp(path, dir, n) == 0 && (path[n] == 0 || path[n] == '/');
}

// The shell path `rel` names under directory descriptor `fd`, checked against
// the roots: W_OK with it in `out`, or a WASI errno.
static uint32_t resolve(call_t *c, uint32_t fd, uint32_t p, uint32_t n, char *out) {
    wfd_t *d = fd_of(c, fd);
    if (d == NULL) {
        return W_BADF;
    }
    if (d->kind != K_DIR) {
        return W_NOTDIR;
    }
    const char *rel = mem(c, p, n);
    if (rel == NULL) {
        return W_FAULT;
    }
    char path[PATH_CAP];
    size_t dn = strlen(d->path);
    if (dn + 1 + n + 1 > sizeof path) {
        return W_NAMETOOLONG;
    }
    memcpy(path, d->path, dn);
    path[dn] = '/';
    memcpy(path + dn + 1, rel, n);
    path[dn + 1 + n] = 0;
    if (memchr(rel, 0, n) != NULL) {
        return W_INVAL;
    }
    if (moy_sh_abs(c->w->s, n && rel[0] == '/' ? rel : path, out, PATH_CAP) != 0) {
        return W_NAMETOOLONG;
    }
    for (const char *r = c->w->roots; *r; r += strlen(r) + 1) {
        if (under(out, r)) {
            return W_OK;
        }
    }
    return W_NOTCAPABLE;
}

// -- args, env, clocks, random, the process ----------------------------------------

static void b_sizes(call_t *c) {
    moy_wasi_t *w = c->w;
    bool env = c->a[2] != 0;
    c->r = put32(c, c->a[0], env ? (uint32_t)w->envc : (uint32_t)w->argc)
           && put32(c, c->a[1], env ? (uint32_t)w->env_n : (uint32_t)w->args_n) ? W_OK : W_FAULT;
}

static void b_strings(call_t *c) {
    moy_wasi_t *w = c->w;
    bool env = c->a[2] != 0;
    size_t total = env ? w->env_n : w->args_n;
    int count = env ? w->envc : w->argc;
    const char *src = env ? w->env : (w->argc ? w->argv[0] : "");
    char *dst = mem(c, c->a[1], total);
    if (dst == NULL || mem(c, c->a[0], (uint64_t)count * 4) == NULL) {
        c->r = W_FAULT;
        return;
    }
    if (total) {
        memcpy(dst, src, total);
    }
    size_t off = 0;
    for (int i = 0; i < count; i++) {
        put32(c, c->a[0] + 4u * (uint32_t)i, c->a[1] + (uint32_t)off);
        off += strlen(src + off) + 1;
    }
    c->r = W_OK;
}

// A pointer argument's wasm offset. The rows carry WAMR's libc-wasi
// signatures because wamrc compiles them in: a module's AOT code checks and
// translates each `*` argument to a native pointer at the call site and calls
// the native directly, and the interpreter does the same from the row, so
// both tiers hand over the native pointer and every body works in offsets.
static uint32_t app_off(wasm_exec_env_t e, void *p) {
    return (uint32_t)wasm_runtime_addr_native_to_app(wasm_runtime_get_module_inst(e), p);
}

static uint32_t w_args_sizes_get(wasm_exec_env_t e, void *argc_at, void *size_at) {
    uint32_t argc = app_off(e, argc_at);
    uint32_t size = app_off(e, size_at);
    call_t c = { .a = { argc, size, 0 } };
    return go(e, b_sizes, &c);
}

static uint32_t w_args_get(wasm_exec_env_t e, void *argv_at, void *buf_at) {
    uint32_t argv = app_off(e, argv_at);
    uint32_t buf = app_off(e, buf_at);
    call_t c = { .a = { argv, buf, 0 } };
    return go(e, b_strings, &c);
}

static uint32_t w_environ_sizes_get(wasm_exec_env_t e, void *count_at, void *size_at) {
    uint32_t count = app_off(e, count_at);
    uint32_t size = app_off(e, size_at);
    call_t c = { .a = { count, size, 1 } };
    return go(e, b_sizes, &c);
}

static uint32_t w_environ_get(wasm_exec_env_t e, void *env_at, void *buf_at) {
    uint32_t env = app_off(e, env_at);
    uint32_t buf = app_off(e, buf_at);
    call_t c = { .a = { env, buf, 1 } };
    return go(e, b_strings, &c);
}

static void b_clock(call_t *c) {
    uint32_t id = c->a[0];
    if (id > 3) {
        c->r = W_INVAL;
        return;
    }
    uint64_t v = c->a[2] ? 1000u : id == 0 ? real_ns() : (uint64_t)mono_us() * 1000u;
    c->r = put64(c, c->a[1], v) ? W_OK : W_FAULT;
}

static uint32_t w_clock_res_get(wasm_exec_env_t e, uint32_t id, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { id, out, 1 } };
    return go(e, b_clock, &c);
}

static uint32_t w_clock_time_get(wasm_exec_env_t e, uint32_t id, uint64_t precision, void *out_at) {
    uint32_t out = app_off(e, out_at);
    (void)precision;
    call_t c = { .a = { id, out, 0 } };
    return go(e, b_clock, &c);
}

static void b_random(call_t *c) {
    uint8_t *p = mem(c, c->a[0], c->a[1]);
    if (p == NULL) {
        c->r = W_FAULT;
        return;
    }
    fill_random(p, c->a[1]);
    c->r = W_OK;
}

static uint32_t w_random_get(wasm_exec_env_t e, void *buf_at, uint32_t n) {
    uint32_t buf = app_off(e, buf_at);
    call_t c = { .a = { buf, n } };
    return go(e, b_random, &c);
}

static void b_exit(call_t *c) {
    stop(c->w, (int)(c->a[0] & 0xffu));
    c->r = W_OK;
}

static void w_proc_exit(wasm_exec_env_t e, uint32_t code) {
    call_t c = { .a = { code } };
    go(e, b_exit, &c);
}

static void b_ok(call_t *c) {
    c->r = W_OK;
}

static uint32_t w_sched_yield(wasm_exec_env_t e) {
    call_t c = { .a = { 0 } };
    return go(e, b_ok, &c);
}

static void b_nosys(call_t *c) {
    c->r = W_NOSYS;
}

static uint32_t w_proc_raise(wasm_exec_env_t e, uint32_t sig) {
    call_t c = { .a = { sig } };
    return go(e, b_nosys, &c);
}

// -- streams -------------------------------------------------------------------------

// A background program's bytes on fd 1 or 2: the log's lines under its pid.
static void bg_write(moy_wasi_t *w, int k, const char *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (p[i] == '\n' || w->line_n[k] == LINE_CAP) {
            bg_flush(w, k);
            if (p[i] == '\n') {
                continue;
            }
        }
        w->line[k][w->line_n[k]++] = p[i];
    }
}

// fd_read, fd_write and their positioned twins: a[0] fd, a[1] iovs, a[2]
// their count, a[3] where the count goes, a[4] 1 to write, b[0] an offset
// (a[5] 1 when there is one).
static void b_rw(call_t *c) {
    moy_wasi_t *w = c->w;
    wfd_t *f = fd_of(c, c->a[0]);
    bool writing = c->a[4] != 0;
    if (f == NULL || f->kind != K_STREAM) {
        c->r = f == NULL ? W_BADF : W_ISDIR;
        return;
    }
    const uint32_t *iov = mem(c, c->a[1], (uint64_t)c->a[2] * 8);
    if (iov == NULL) {
        c->r = W_FAULT;
        return;
    }
    int64_t back = -1;
    if (c->a[5]) {
        back = moy_sh_fd_seek(w->s, f->sh, 0, 1);
        if (back < 0 || moy_sh_fd_seek(w->s, f->sh, (int64_t)c->b[0], 0) < 0) {
            c->r = back < 0 ? wasi_errno((int)-back) : W_INVAL;
            return;
        }
    }
    bool bg = w->bg_pid && (f->sh == 1 || f->sh == 2) && c->a[0] == (uint32_t)f->sh;
    uint32_t done = 0;
    uint16_t err = W_OK;
    for (uint32_t i = 0; i < c->a[2]; i++) {
        uint32_t base, len;
        memcpy(&base, iov + 2 * i, 4);
        memcpy(&len, iov + 2 * i + 1, 4);
        char *p = len ? mem(c, base, len) : NULL;
        if (len && p == NULL) {
            err = W_FAULT;
            break;
        }
        if (!len) {
            continue;
        }
        long got;
        if (writing && bg) {
            bg_write(w, f->sh - 1, p, len);
            got = (long)len;
        } else if (!writing && w->bg_pid && f->sh == 0) {
            got = 0;
        } else {
            if (writing && (f->flags & FDF_APPEND) && f->sh >= 3) {
                moy_sh_fd_seek(w->s, f->sh, 0, 2);
            }
            errno = 0;
            got = writing ? moy_sh_fd_write(w->s, f->sh, p, len)
                          : moy_sh_fd_read(w->s, f->sh, p, len);
            if (got < 0) {
                err = wasi_errno(errno ? errno : EIO);
                break;
            }
        }
        done += (uint32_t)got;
        if ((uint32_t)got < len) {
            break;
        }
    }
    if (back >= 0) {
        moy_sh_fd_seek(w->s, f->sh, back, 0);
    }
    if (err != W_OK && done == 0) {
        c->r = err;
        return;
    }
    c->r = put32(c, c->a[3], done) ? W_OK : W_FAULT;
}

static uint32_t w_fd_read(wasm_exec_env_t e, uint32_t fd, void *iovs_at, uint32_t n, void *out_at) {
    uint32_t iovs = app_off(e, iovs_at);
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, iovs, n, out, 0, 0 } };
    return go(e, b_rw, &c);
}

static uint32_t w_fd_write(wasm_exec_env_t e, uint32_t fd, void *iovs_at, uint32_t n, void *out_at) {
    uint32_t iovs = app_off(e, iovs_at);
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, iovs, n, out, 1, 0 } };
    return go(e, b_rw, &c);
}

static uint32_t w_fd_pread(wasm_exec_env_t e, uint32_t fd, void *iovs_at, uint32_t n, uint64_t at, void *out_at) {
    uint32_t iovs = app_off(e, iovs_at);
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, iovs, n, out, 0, 1 }, .b = { at } };
    return go(e, b_rw, &c);
}

static uint32_t w_fd_pwrite(wasm_exec_env_t e, uint32_t fd, void *iovs_at, uint32_t n, uint64_t at, void *out_at) {
    uint32_t iovs = app_off(e, iovs_at);
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, iovs, n, out, 1, 1 }, .b = { at } };
    return go(e, b_rw, &c);
}

// fd_seek and fd_tell: a[0] fd, a[1] whence, a[2] where the offset goes, b[0] the offset.
static void b_seek(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL || f->kind != K_STREAM) {
        c->r = f == NULL ? W_BADF : W_ISDIR;
        return;
    }
    if (c->a[1] > 2) {
        c->r = W_INVAL;
        return;
    }
    int64_t at = moy_sh_fd_seek(c->w->s, f->sh, (int64_t)c->b[0], (int)c->a[1]);
    if (at < 0) {
        c->r = wasi_errno((int)-at);
        return;
    }
    c->r = put64(c, c->a[2], (uint64_t)at) ? W_OK : W_FAULT;
}

static uint32_t w_fd_seek(wasm_exec_env_t e, uint32_t fd, uint64_t off, uint32_t whence, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, whence, out }, .b = { off } };
    return go(e, b_seek, &c);
}

static uint32_t w_fd_tell(wasm_exec_env_t e, uint32_t fd, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, 1, out }, .b = { 0 } };
    return go(e, b_seek, &c);
}

static void b_close(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL) {
        c->r = W_BADF;
        return;
    }
    int r = 0;
    if (f->kind == K_STREAM && f->sh >= 3) {
        r = moy_sh_fd_close(c->w->s, f->sh);
    }
    w_free(f->path);
    w_free(f->name);
    memset(f, 0, sizeof *f);
    c->r = wasi_errno(r);
}

static uint32_t w_fd_close(wasm_exec_env_t e, uint32_t fd) {
    call_t c = { .a = { fd } };
    return go(e, b_close, &c);
}

// A descriptor's filetype, rights and size.
static int describe(call_t *c, wfd_t *f, uint64_t *rights, uint32_t *size) {
    *size = 0;
    if (f->kind == K_DIR) {
        *rights = R_ALL;
        return FT_DIR;
    }
    int what = moy_sh_fd_what(c->w->s, f->sh, size);
    *rights = what == MOY_SH_FD_FILE ? R_ALL : R_ALL & ~(R_FD_SEEK | R_FD_TELL);
    return what == MOY_SH_FD_TERM ? FT_CHAR : what == MOY_SH_FD_FILE ? FT_FILE : FT_UNKNOWN;
}

static void b_fdstat(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    uint8_t *p = mem(c, c->a[1], 24);
    if (f == NULL || p == NULL) {
        c->r = f == NULL ? W_BADF : W_FAULT;
        return;
    }
    uint64_t rights;
    uint32_t size;
    memset(p, 0, 24);
    p[0] = (uint8_t)describe(c, f, &rights, &size);
    memcpy(p + 2, &f->flags, 2);
    memcpy(p + 8, &rights, 8);
    memcpy(p + 16, &rights, 8);
    c->r = W_OK;
}

static uint32_t w_fd_fdstat_get(wasm_exec_env_t e, uint32_t fd, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, out } };
    return go(e, b_fdstat, &c);
}

static void b_setflags(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL) {
        c->r = W_BADF;
        return;
    }
    f->flags = (uint16_t)(c->a[1] & FDF_APPEND);
    c->r = W_OK;
}

static uint32_t w_fd_fdstat_set_flags(wasm_exec_env_t e, uint32_t fd, uint32_t flags) {
    call_t c = { .a = { fd, flags } };
    return go(e, b_setflags, &c);
}

static void b_fd_ok(call_t *c) {
    c->r = fd_of(c, c->a[0]) != NULL ? W_OK : W_BADF;
}

static void b_fd_notsup(call_t *c) {
    c->r = fd_of(c, c->a[0]) != NULL ? W_NOTSUP : W_BADF;
}

static uint32_t w_fd_fdstat_set_rights(wasm_exec_env_t e, uint32_t fd, uint64_t base,
                                     uint64_t inh) {
    (void)base;
    (void)inh;
    call_t c = { .a = { fd } };
    return go(e, b_fd_ok, &c);
}

static uint32_t w_fd_advise(wasm_exec_env_t e, uint32_t fd, uint64_t off, uint64_t len,
                          uint32_t advice) {
    (void)off;
    (void)len;
    (void)advice;
    call_t c = { .a = { fd } };
    return go(e, b_fd_ok, &c);
}

static uint32_t w_fd_datasync(wasm_exec_env_t e, uint32_t fd) {
    call_t c = { .a = { fd } };
    return go(e, b_fd_ok, &c);
}

static uint32_t w_fd_sync(wasm_exec_env_t e, uint32_t fd) {
    call_t c = { .a = { fd } };
    return go(e, b_fd_ok, &c);
}

static uint32_t w_fd_allocate(wasm_exec_env_t e, uint32_t fd, uint64_t off, uint64_t len) {
    (void)off;
    (void)len;
    call_t c = { .a = { fd } };
    return go(e, b_fd_notsup, &c);
}

static uint32_t w_fd_filestat_set_size(wasm_exec_env_t e, uint32_t fd, uint64_t size) {
    (void)size;
    call_t c = { .a = { fd } };
    return go(e, b_fd_notsup, &c);
}

static uint32_t w_fd_filestat_set_times(wasm_exec_env_t e, uint32_t fd, uint64_t a, uint64_t m,
                                      uint32_t flags) {
    (void)a;
    (void)m;
    (void)flags;
    call_t c = { .a = { fd } };
    return go(e, b_fd_notsup, &c);
}

static uint64_t ino_of(const char *path) {
    uint64_t h = 1469598103934665603ull;
    for (const char *p = path; *p; p++) {
        h = (h ^ (unsigned char)*p) * 1099511628211ull;
    }
    return h;
}

static void filestat(uint8_t *p, const char *path, int type, uint64_t size, int64_t mtime) {
    uint64_t one = 1, ino = path != NULL ? ino_of(path) : 0;
    uint64_t t = mtime > 0 ? (uint64_t)mtime * 1000000000ull : 0;
    memset(p, 0, 64);
    memcpy(p + 8, &ino, 8);
    p[16] = (uint8_t)type;
    memcpy(p + 24, &one, 8);
    memcpy(p + 32, &size, 8);
    memcpy(p + 40, &t, 8);
    memcpy(p + 48, &t, 8);
    memcpy(p + 56, &t, 8);
}

static void b_filestat(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    uint8_t *p = mem(c, c->a[1], 64);
    if (f == NULL || p == NULL) {
        c->r = f == NULL ? W_BADF : W_FAULT;
        return;
    }
    uint64_t rights;
    uint32_t size;
    int type = describe(c, f, &rights, &size);
    moy_sh_stat_t st = { 0, 0, 0 };
    if (f->path != NULL) {
        moy_sh_stat_at(c->w->s, f->path, &st);
    }
    filestat(p, f->path, type, size, st.mtime);
    c->r = W_OK;
}

static uint32_t w_fd_filestat_get(wasm_exec_env_t e, uint32_t fd, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, out } };
    return go(e, b_filestat, &c);
}

static void b_prestat(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL || f->name == NULL) {
        c->r = W_BADF;
        return;
    }
    size_t n = strlen(f->name);
    if (c->a[2]) {
        char *p = mem(c, c->a[1], c->a[2]);
        if (p == NULL) {
            c->r = W_FAULT;
            return;
        }
        if (c->a[2] < n) {
            c->r = W_NAMETOOLONG;
            return;
        }
        memcpy(p, f->name, n);
        c->r = W_OK;
        return;
    }
    uint8_t *p = mem(c, c->a[1], 8);
    if (p == NULL) {
        c->r = W_FAULT;
        return;
    }
    uint32_t len = (uint32_t)n;
    memset(p, 0, 8);
    memcpy(p + 4, &len, 4);
    c->r = W_OK;
}

static uint32_t w_fd_prestat_get(wasm_exec_env_t e, uint32_t fd, void *out_at) {
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, out, 0 } };
    return go(e, b_prestat, &c);
}

static uint32_t w_fd_prestat_dir_name(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n) {
    uint32_t p = app_off(e, p_at);
    call_t c = { .a = { fd, p, n ? n : 1 } };
    if (n == 0) {
        return W_NAMETOOLONG;
    }
    return go(e, b_prestat, &c);
}

static void b_renumber(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL || c->a[1] >= MOY_WASI_FDS) {
        c->r = W_BADF;
        return;
    }
    if (c->a[0] == c->a[1]) {
        c->r = W_OK;
        return;
    }
    wfd_t *t = &c->w->fd[c->a[1]];
    if (t->kind == K_STREAM && t->sh >= 3) {
        moy_sh_fd_close(c->w->s, t->sh);
    }
    w_free(t->path);
    w_free(t->name);
    *t = *f;
    memset(f, 0, sizeof *f);
    c->r = W_OK;
}

static uint32_t w_fd_renumber(wasm_exec_env_t e, uint32_t fd, uint32_t to) {
    call_t c = { .a = { fd, to } };
    return go(e, b_renumber, &c);
}

// -- folders -------------------------------------------------------------------------

typedef struct {
    call_t *c;
    uint8_t *buf;
    uint32_t cap, used;
    uint64_t at, from;
    const char *dir;
} readdir_t;

static int dirent_put(readdir_t *r, const char *name, int type) {
    uint64_t next = ++r->at;
    if (next <= r->from) {
        return 0;
    }
    uint8_t head[24];
    uint32_t n = (uint32_t)strlen(name);
    char path[PATH_CAP];
    snprintf(path, sizeof path, "%s/%s", r->dir, name);
    uint64_t ino = ino_of(path);
    memset(head, 0, sizeof head);
    memcpy(head, &next, 8);
    memcpy(head + 8, &ino, 8);
    memcpy(head + 16, &n, 4);
    head[20] = (uint8_t)type;
    uint32_t k = r->cap - r->used < sizeof head ? r->cap - r->used : (uint32_t)sizeof head;
    memcpy(r->buf + r->used, head, k);
    r->used += k;
    k = r->cap - r->used < n ? r->cap - r->used : n;
    memcpy(r->buf + r->used, name, k);
    r->used += k;
    return r->used >= r->cap;
}

static int dirent_ent(void *arg, const char *name, int is_dir, uint32_t size) {
    (void)size;
    return dirent_put(arg, name, is_dir ? FT_DIR : FT_FILE);
}

static void b_readdir(call_t *c) {
    wfd_t *f = fd_of(c, c->a[0]);
    if (f == NULL || f->kind != K_DIR) {
        c->r = f == NULL ? W_BADF : W_NOTDIR;
        return;
    }
    readdir_t r = { c, mem(c, c->a[1], c->a[2]), c->a[2], 0, 0, c->b[0], f->path };
    if (r.buf == NULL && c->a[2]) {
        c->r = W_FAULT;
        return;
    }
    if (r.cap && !dirent_put(&r, ".", FT_DIR) && !dirent_put(&r, "..", FT_DIR)) {
        int e = moy_sh_list(c->w->s, f->path, dirent_ent, &r);
        if (e < 0 || e > 1) {
            c->r = wasi_errno(e);
            return;
        }
    }
    c->r = put32(c, c->a[3], r.used) ? W_OK : W_FAULT;
}

static uint32_t w_fd_readdir(wasm_exec_env_t e, uint32_t fd, void *buf_at, uint32_t n, uint64_t cookie, void *out_at) {
    uint32_t buf = app_off(e, buf_at);
    uint32_t out = app_off(e, out_at);
    call_t c = { .a = { fd, buf, n, out }, .b = { cookie } };
    return go(e, b_readdir, &c);
}

// path_open: a[0] dirfd, a[1] path, a[2] its length, a[3] oflags, a[4]
// fdflags, a[5] where the descriptor goes, b[0] the base rights.
static void b_open(call_t *c) {
    char path[PATH_CAP];
    uint32_t r = resolve(c, c->a[0], c->a[1], c->a[2], path);
    if (r != W_OK) {
        c->r = r;
        return;
    }
    moy_wasi_t *w = c->w;
    uint32_t of = c->a[3];
    moy_sh_stat_t st;
    int exists = moy_sh_stat_at(w->s, path, &st) == 0;
    int fd = fd_take(w, 3);
    if (fd < 0) {
        c->r = W_MFILE;
        return;
    }
    if (mem(c, c->a[5], 4) == NULL) {
        c->r = W_FAULT;
        return;
    }
    wfd_t *f = &w->fd[fd];
    if ((of & O_DIRECTORY_W) || (exists && st.is_dir)) {
        if (!exists) {
            c->r = W_NOENT;
            return;
        }
        if (!st.is_dir) {
            c->r = W_NOTDIR;
            return;
        }
        if (of & (O_CREAT_W | O_TRUNC_W)) {
            c->r = (of & O_EXCL_W) ? W_EXIST : W_ISDIR;
            return;
        }
        f->path = w_strdup(path);
        if (f->path == NULL) {
            c->r = W_NOMEM;
            return;
        }
        f->kind = K_DIR;
        f->sh = -1;
    } else {
        uint64_t rights = c->b[0];
        int flags = (rights & R_FD_READ) || !(rights & R_FD_WRITE) ? MOY_SH_O_READ : 0;
        if ((rights & R_FD_WRITE) || (of & (O_CREAT_W | O_TRUNC_W))) {
            flags |= MOY_SH_O_WRITE;
        }
        flags |= (of & O_CREAT_W ? MOY_SH_O_CREAT : 0) | (of & O_TRUNC_W ? MOY_SH_O_TRUNC : 0) |
                 (of & O_EXCL_W ? MOY_SH_O_EXCL : 0) |
                 (c->a[4] & FDF_APPEND ? MOY_SH_O_APPEND : 0);
        int sh = moy_sh_fd_open(w->s, path, flags);
        if (sh < 0) {
            c->r = wasi_errno(sh);
            return;
        }
        f->path = w_strdup(path);
        f->kind = K_STREAM;
        f->sh = sh;
        f->flags = (uint16_t)(c->a[4] & FDF_APPEND);
    }
    put32(c, c->a[5], (uint32_t)fd);
    c->r = W_OK;
}

static uint32_t w_path_open(wasm_exec_env_t e, uint32_t dirfd, uint32_t dirflags, void *p_at, uint32_t n, uint32_t oflags, uint64_t rights, uint64_t inh, uint32_t fdflags, void *out_at) {
    uint32_t p = app_off(e, p_at);
    uint32_t out = app_off(e, out_at);
    (void)dirflags;
    (void)inh;
    call_t c = { .a = { dirfd, p, n, oflags, fdflags, out }, .b = { rights } };
    return go(e, b_open, &c);
}

// The folder ops: a[0] dirfd, a[1] path, a[2] length, a[3] which.
enum { OP_MKDIR, OP_RMDIR, OP_UNLINK };

static void b_dirop(call_t *c) {
    char path[PATH_CAP];
    uint32_t r = resolve(c, c->a[0], c->a[1], c->a[2], path);
    if (r != W_OK) {
        c->r = r;
        return;
    }
    moy_sh_t *s = c->w->s;
    moy_sh_stat_t st;
    int exists = moy_sh_stat_at(s, path, &st) == 0;
    switch (c->a[3]) {
        case OP_MKDIR:
            c->r = exists ? W_EXIST : wasi_errno(moy_sh_mkdir_at(s, path));
            break;
        case OP_RMDIR:
            c->r = !exists ? W_NOENT : !st.is_dir ? W_NOTDIR : wasi_errno(moy_sh_remove_at(s, path));
            break;
        default:
            c->r = !exists ? W_NOENT : st.is_dir ? W_ISDIR : wasi_errno(moy_sh_remove_at(s, path));
            break;
    }
}

static uint32_t w_path_create_directory(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n) {
    uint32_t p = app_off(e, p_at);
    call_t c = { .a = { fd, p, n, OP_MKDIR } };
    return go(e, b_dirop, &c);
}

static uint32_t w_path_remove_directory(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n) {
    uint32_t p = app_off(e, p_at);
    call_t c = { .a = { fd, p, n, OP_RMDIR } };
    return go(e, b_dirop, &c);
}

static uint32_t w_path_unlink_file(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n) {
    uint32_t p = app_off(e, p_at);
    call_t c = { .a = { fd, p, n, OP_UNLINK } };
    return go(e, b_dirop, &c);
}

static void b_rename(call_t *c) {
    char from[PATH_CAP], to[PATH_CAP];
    uint32_t r = resolve(c, c->a[0], c->a[1], c->a[2], from);
    if (r == W_OK) {
        r = resolve(c, c->a[3], c->a[4], c->a[5], to);
    }
    c->r = r != W_OK ? r : wasi_errno(moy_sh_rename_at(c->w->s, from, to));
}

static uint32_t w_path_rename(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n, uint32_t fd2, void *p2_at, uint32_t n2) {
    uint32_t p = app_off(e, p_at);
    uint32_t p2 = app_off(e, p2_at);
    call_t c = { .a = { fd, p, n, fd2, p2, n2 } };
    return go(e, b_rename, &c);
}

static void b_pathstat(call_t *c) {
    char path[PATH_CAP];
    uint32_t r = resolve(c, c->a[0], c->a[1], c->a[2], path);
    uint8_t *p = mem(c, c->a[3], 64);
    if (r != W_OK || p == NULL) {
        c->r = r != W_OK ? r : W_FAULT;
        return;
    }
    moy_sh_stat_t st;
    int e = moy_sh_stat_at(c->w->s, path, &st);
    if (e != 0) {
        c->r = wasi_errno(e);
        return;
    }
    filestat(p, path, st.is_dir ? FT_DIR : FT_FILE, st.size, st.mtime);
    c->r = W_OK;
}

static uint32_t w_path_filestat_get(wasm_exec_env_t e, uint32_t fd, uint32_t flags, void *p_at, uint32_t n, void *out_at) {
    uint32_t p = app_off(e, p_at);
    uint32_t out = app_off(e, out_at);
    (void)flags;
    call_t c = { .a = { fd, p, n, out } };
    return go(e, b_pathstat, &c);
}

// What the files tree has none of (links), or keeps no record of (times): a
// path that resolves answers NOTSUP, one that does not its reason.
static void b_path_notsup(call_t *c) {
    char path[PATH_CAP];
    uint32_t r = resolve(c, c->a[0], c->a[1], c->a[2], path);
    c->r = r != W_OK ? r : c->a[3];
}

static uint32_t w_path_filestat_set_times(wasm_exec_env_t e, uint32_t fd, uint32_t flags, void *p_at, uint32_t n, uint64_t a, uint64_t m, uint32_t fst) {
    uint32_t p = app_off(e, p_at);
    (void)flags;
    (void)a;
    (void)m;
    (void)fst;
    call_t c = { .a = { fd, p, n, W_NOTSUP } };
    return go(e, b_path_notsup, &c);
}

static uint32_t w_path_readlink(wasm_exec_env_t e, uint32_t fd, void *p_at, uint32_t n, void *buf_at, uint32_t len, void *out_at) {
    uint32_t p = app_off(e, p_at);
    uint32_t buf = app_off(e, buf_at);
    uint32_t out = app_off(e, out_at);
    (void)buf;
    (void)len;
    (void)out;
    call_t c = { .a = { fd, p, n, W_INVAL } };
    return go(e, b_path_notsup, &c);
}

static uint32_t w_path_link(wasm_exec_env_t e, uint32_t fd, uint32_t flags, void *p_at, uint32_t n, uint32_t fd2, void *p2_at, uint32_t n2) {
    uint32_t p = app_off(e, p_at);
    uint32_t p2 = app_off(e, p2_at);
    (void)flags;
    (void)fd2;
    (void)p2;
    (void)n2;
    call_t c = { .a = { fd, p, n, W_NOTSUP } };
    return go(e, b_path_notsup, &c);
}

static uint32_t w_path_symlink(wasm_exec_env_t e, void *p_at, uint32_t n, uint32_t fd, void *p2_at, uint32_t n2) {
    uint32_t p = app_off(e, p_at);
    uint32_t p2 = app_off(e, p2_at);
    (void)p;
    (void)n;
    call_t c = { .a = { fd, p2, n2, W_NOTSUP } };
    return go(e, b_path_notsup, &c);
}

// -- poll_oneoff: clocks wait, every descriptor is ready ---------------------------

static void b_poll(call_t *c) {
    moy_wasi_t *w = c->w;
    uint32_t n = c->a[2];
    const uint8_t *in = mem(c, c->a[0], (uint64_t)n * 48);
    uint8_t *out = mem(c, c->a[1], (uint64_t)n * 32);
    if (n == 0) {
        c->r = W_INVAL;
        return;
    }
    if (in == NULL || out == NULL) {
        c->r = W_FAULT;
        return;
    }
    uint32_t events = 0;
    int64_t wait_us = -1;
    int clock_at = -1;
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t *s = in + 48 * i;
        if (s[8] != 0) {
            uint8_t *ev = out + 32 * events++;
            uint32_t fd;
            memcpy(&fd, s + 16, 4);
            memset(ev, 0, 32);
            memcpy(ev, s, 8);
            uint16_t err = fd_of(c, fd) != NULL ? W_OK : W_BADF;
            memcpy(ev + 8, &err, 2);
            ev[10] = s[8];
            continue;
        }
        uint64_t timeout;
        uint16_t flags;
        uint32_t id;
        memcpy(&id, s + 16, 4);
        memcpy(&timeout, s + 24, 8);
        memcpy(&flags, s + 40, 2);
        int64_t us = (int64_t)(timeout / 1000u);
        if (flags & 1) {
            int64_t now = id == 0 ? (int64_t)(real_ns() / 1000u) : mono_us();
            us = us > now ? us - now : 0;
        }
        if (wait_us < 0 || us < wait_us) {
            wait_us = us;
            clock_at = (int)i;
        }
    }
    if (events == 0 && clock_at >= 0) {
        int64_t end = mono_us() + wait_us;
        const moy_sh_host_t *h = moy_sh_host(w->s);
        for (int64_t left = wait_us; left > 0; left = end - mono_us()) {
            int ms = left > 20000 ? 20 : (int)((left + 999) / 1000);
            if (h->sleep_ms != NULL) {
                h->sleep_ms(h->ctx, ms);
            }
            if (moy_sh_interrupted(w->s)) {
                stop(w, MOY_WASI_INTERRUPTED);
                c->r = W_INTR;
                return;
            }
            if (h->sleep_ms == NULL) {
                break;
            }
        }
        uint8_t *ev = out;
        memset(ev, 0, 32);
        memcpy(ev, in + 48 * (uint32_t)clock_at, 8);
        events = 1;
    }
    c->r = put32(c, c->a[3], events) ? W_OK : W_FAULT;
}

static uint32_t w_poll_oneoff(wasm_exec_env_t e, void *in_at, void *out_at, uint32_t n, void *nout_at) {
    uint32_t in = app_off(e, in_at);
    uint32_t out = app_off(e, out_at);
    uint32_t nout = app_off(e, nout_at);
    call_t c = { .a = { in, out, n, nout } };
    return go(e, b_poll, &c);
}

// -- sockets: no descriptor is one -------------------------------------------------

static void b_notsock(call_t *c) {
    c->r = fd_of(c, c->a[0]) != NULL ? W_NOTSOCK : W_BADF;
}

static uint32_t w_sock_accept(wasm_exec_env_t e, uint32_t fd, uint32_t flags, void *out_at) {
    uint32_t out = app_off(e, out_at);
    (void)flags;
    (void)out;
    call_t c = { .a = { fd } };
    return go(e, b_notsock, &c);
}

static uint32_t w_sock_recv(wasm_exec_env_t e, uint32_t fd, void *a_at, uint32_t b, uint32_t f, void *o1_at, void *o2_at) {
    uint32_t a = app_off(e, a_at);
    uint32_t o1 = app_off(e, o1_at);
    uint32_t o2 = app_off(e, o2_at);
    (void)a;
    (void)b;
    (void)f;
    (void)o1;
    (void)o2;
    call_t c = { .a = { fd } };
    return go(e, b_notsock, &c);
}

static uint32_t w_sock_send(wasm_exec_env_t e, uint32_t fd, void *a_at, uint32_t b, uint32_t f, void *o_at) {
    uint32_t a = app_off(e, a_at);
    uint32_t o = app_off(e, o_at);
    (void)a;
    (void)b;
    (void)f;
    (void)o;
    call_t c = { .a = { fd } };
    return go(e, b_notsock, &c);
}

static uint32_t w_sock_shutdown(wasm_exec_env_t e, uint32_t fd, uint32_t how) {
    (void)how;
    call_t c = { .a = { fd } };
    return go(e, b_notsock, &c);
}

// -- moybyte.term ------------------------------------------------------------------

static void b_term_size(call_t *c) {
    const moy_sh_host_t *h = moy_sh_host(c->w->s);
    int cols = 80, rows = 0;
    if (h->size != NULL) {
        h->size(h->ctx, &cols, &rows);
    }
    c->r = put32(c, c->a[0], (uint32_t)cols) && put32(c, c->a[1], (uint32_t)rows) ? W_OK : W_FAULT;
}

static uint32_t w_term_size(wasm_exec_env_t e, uint32_t cols, uint32_t rows) {
    call_t c = { .a = { cols, rows } };
    return go(e, b_term_size, &c);
}

// -- the tables --------------------------------------------------------------------

#define ROW(name, sig) { #name, (void *)w_##name, sig, NULL }

static const NativeSymbol WASI[] = {
    ROW(args_get, "(**)i"),
    ROW(args_sizes_get, "(**)i"),
    ROW(environ_get, "(**)i"),
    ROW(environ_sizes_get, "(**)i"),
    ROW(clock_res_get, "(i*)i"),
    ROW(clock_time_get, "(iI*)i"),
    ROW(fd_advise, "(iIIi)i"),
    ROW(fd_allocate, "(iII)i"),
    ROW(fd_close, "(i)i"),
    ROW(fd_datasync, "(i)i"),
    ROW(fd_fdstat_get, "(i*)i"),
    ROW(fd_fdstat_set_flags, "(ii)i"),
    ROW(fd_fdstat_set_rights, "(iII)i"),
    ROW(fd_filestat_get, "(i*)i"),
    ROW(fd_filestat_set_size, "(iI)i"),
    ROW(fd_filestat_set_times, "(iIIi)i"),
    ROW(fd_pread, "(i*iI*)i"),
    ROW(fd_prestat_dir_name, "(i*~)i"),
    ROW(fd_prestat_get, "(i*)i"),
    ROW(fd_pwrite, "(i*iI*)i"),
    ROW(fd_read, "(i*i*)i"),
    ROW(fd_readdir, "(i*~I*)i"),
    ROW(fd_renumber, "(ii)i"),
    ROW(fd_seek, "(iIi*)i"),
    ROW(fd_sync, "(i)i"),
    ROW(fd_tell, "(i*)i"),
    ROW(fd_write, "(i*i*)i"),
    ROW(path_create_directory, "(i*~)i"),
    ROW(path_filestat_get, "(ii*~*)i"),
    ROW(path_filestat_set_times, "(ii*~IIi)i"),
    ROW(path_link, "(ii*~i*~)i"),
    ROW(path_open, "(ii*~iIIi*)i"),
    ROW(path_readlink, "(i*~*~*)i"),
    ROW(path_remove_directory, "(i*~)i"),
    ROW(path_rename, "(i*~i*~)i"),
    ROW(path_symlink, "(*~i*~)i"),
    ROW(path_unlink_file, "(i*~)i"),
    ROW(poll_oneoff, "(**i*)i"),
    ROW(proc_exit, "(i)"),
    ROW(proc_raise, "(i)i"),
    ROW(random_get, "(*~)i"),
    ROW(sched_yield, "()i"),
    ROW(sock_accept, "(ii*)i"),
    ROW(sock_recv, "(i*ii**)i"),
    ROW(sock_send, "(i*ii*)i"),
    ROW(sock_shutdown, "(ii)i"),
};

static const NativeSymbol TERM[] = {
    ROW(term_size, "(ii)i"),
};

#define N_WASI (uint32_t)(sizeof WASI / sizeof WASI[0])
#define N_TERM (uint32_t)(sizeof TERM / sizeof TERM[0])

uint32_t moy_wasi_count(uint32_t *term) {
    if (term != NULL) {
        *term = N_TERM;
    }
    return N_WASI;
}

int moy_wasi_register(NativeSymbol *store) {
    memcpy(store, WASI, sizeof WASI);
    memcpy(store + N_WASI, TERM, sizeof TERM);
    if (!wasm_runtime_register_natives(MOY_WASI_MODULE, store, N_WASI)) {
        return -1;
    }
    if (!wasm_runtime_register_natives(MOY_WASI_TERM, store + N_WASI, N_TERM)) {
        wasm_runtime_unregister_natives(MOY_WASI_MODULE, store);
        return -1;
    }
    return 0;
}

void moy_wasi_unregister(NativeSymbol *store) {
    wasm_runtime_unregister_natives(MOY_WASI_MODULE, store);
    wasm_runtime_unregister_natives(MOY_WASI_TERM, store + N_WASI);
}

int moy_wasi_check(wasm_module_t module, char *err, size_t errlen) {
    int32_t n = wasm_runtime_get_import_count(module);
    for (int32_t i = 0; i < n; i++) {
        wasm_import_t im;
        wasm_runtime_get_import_type(module, i, &im);
        bool ours = strcmp(im.module_name, MOY_WASI_MODULE) == 0
                    || strcmp(im.module_name, MOY_WASI_TERM) == 0;
        if (!ours || im.kind != WASM_IMPORT_EXPORT_KIND_FUNC || !im.linked) {
            snprintf(err, errlen, "imports %s.%s, which a command on this console cannot",
                     im.module_name, im.name);
            return 1;
        }
    }
    int32_t m = wasm_runtime_get_export_count(module);
    for (int32_t i = 0; i < m; i++) {
        wasm_export_t ex;
        wasm_runtime_get_export_type(module, i, &ex);
        if (ex.kind == WASM_IMPORT_EXPORT_KIND_FUNC && strcmp(ex.name, "_start") == 0) {
            return 0;
        }
    }
    snprintf(err, errlen, "not a command: it exports no _start");
    return 1;
}

int moy_wasi_main(moy_wasi_t *w, wasm_exec_env_t env, char *err, size_t errlen) {
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    wasm_function_inst_t start = wasm_runtime_lookup_function(inst, "_start");
    if (start == NULL) {
        snprintf(err, errlen, "not a command: it exports no _start");
        return MOY_WASI_NOT_COMMAND;
    }
    wasm_runtime_set_user_data(env, w);
    bool ok = wasm_runtime_call_wasm(env, start, 0, NULL);
    wasm_runtime_set_user_data(env, NULL);
    if (w->exited) {
        wasm_runtime_clear_exception(inst);
        return w->code;
    }
    if (!ok) {
        const char *why = wasm_runtime_get_exception(inst);
        snprintf(err, errlen, "%s", why != NULL ? why : "trapped");
        return MOY_WASI_TRAPPED;
    }
    return 0;
}
