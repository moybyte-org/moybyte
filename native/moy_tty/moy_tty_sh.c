// moy_tty_sh: the shell a stream face runs (moy_tty.h) -- a moy_sh session
// over the board's host table (native/moy_sh/moy_sh_board.h), its terminal
// the face's socket, its Ctrl-C the face's poll, its input the stream's.
//
// The session runs on the face's task. What crosses the door into Python or
// touches the store hops to the console's task (moy_tty_face_hop): the
// table's entries that do are wrapped here (every one moy_sh_board.c's door
// serves, `mem`'s Python heap among them, but `role`), and the shell's own
// store calls go through the table's `hop`. A role command runs on this
// task, each of its door's crossings a hop (moy_sh_board.c's `door`) and its
// waits between its steps here. The kernel's log and sleeps run in place.
//
// A text app (moy_sh_app.h) opens in front of the face at the client's size;
// a full-screen one is shown on the alternate screen, its grid's changed rows
// sent as VT100 with its cursor, and the shell's screen comes back after.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "moy_sh.h"
#include "moy_sh_app.h"
#include "moy_sh_board.h"
#include "moy_tty.h"

#define ARENA (32u * 1024u)             // a session's arena (README.md, Memory)
#define NAMES_BYTES 4096u               // a Tab's names
#define ROW_BYTES 2048u

typedef struct {
    moy_tty_t *f;
    moy_sh_t *s;
    moy_sh_host_t board;                // the board's table, the wrapped entries' own
    moy_sh_app_t *app;                  // the text app in front, or NULL
    uint32_t *rows;                     // a full-screen app's rows as last sent (hashes)
    int nrows;
    int cursor[3];                      // its cursor as last sent: row, column, shown
    int polls;
} sh_t;

// -- the hops -----------------------------------------------------------------------

enum { H_VREAD, H_VWRITE, H_VLIST, H_LOOKUP, H_SPAWN, H_STATUS, H_KILL, H_PROCS, H_DF,
       H_MEM };

typedef struct {
    int op;
    sh_t *h;
    moy_sh_t *s;
    const char *path;
    const char *p;
    size_t n;
    char **pp;
    size_t *pn;
    void *fn;
    void *arg;
    int argc;
    char **argv;
    int i, j;
    int *pi;
    uint64_t *u1, *u2;
    uint32_t *mem;
    int r;
} call_t;

static void call_here(void *arg) {
    call_t *c = arg;
    const moy_sh_host_t *b = &c->h->board;
    void *ctx = c->h;
    switch (c->op) {
        case H_VREAD:
            c->r = b->vread(ctx, c->path, c->pp, c->pn);
            break;
        case H_VWRITE:
            c->r = b->vwrite(ctx, c->path, c->p, c->n);
            break;
        case H_VLIST:
            c->r = b->vlist(ctx, c->path, (moy_sh_ent_fn)c->fn, c->arg);
            break;
        case H_LOOKUP:
            c->r = b->lookup(ctx, c->path);
            break;
        case H_SPAWN:
            c->r = b->spawn(ctx, c->s, c->argc, c->argv, c->i, c->pi);
            break;
        case H_STATUS:
            c->r = b->status(ctx, c->i, c->pi);
            break;
        case H_KILL:
            c->r = b->kill(ctx, c->i, c->j);
            break;
        case H_PROCS:
            c->r = b->procs(ctx, (moy_sh_ps_fn)c->fn, c->arg);
            break;
        case H_MEM:
            c->r = b->mem(ctx, c->mem);
            break;
        default:
            c->r = b->df(ctx, c->path, c->u1, c->u2);
            break;
    }
}

static int call(call_t *c, void *ctx) {
    c->h = ctx;
    moy_sh_hop(c->s != NULL ? c->s : c->h->s, call_here, c);
    return c->r;
}

static int w_vread(void *ctx, const char *path, char **p, size_t *n) {
    call_t c = { .op = H_VREAD, .path = path, .pp = p, .pn = n };
    return call(&c, ctx);
}

static int w_vwrite(void *ctx, const char *path, const char *p, size_t n) {
    call_t c = { .op = H_VWRITE, .path = path, .p = p, .n = n };
    return call(&c, ctx);
}

static int w_vlist(void *ctx, const char *dir, moy_sh_ent_fn fn, void *arg) {
    call_t c = { .op = H_VLIST, .path = dir, .fn = (void *)fn, .arg = arg };
    return call(&c, ctx);
}

static int w_lookup(void *ctx, const char *name) {
    call_t c = { .op = H_LOOKUP, .path = name };
    return call(&c, ctx);
}

static int w_spawn(void *ctx, moy_sh_t *s, int argc, char **argv, int bg, int *pid) {
    call_t c = { .op = H_SPAWN, .s = s, .argc = argc, .argv = argv, .i = bg, .pi = pid };
    return call(&c, ctx);
}

static int w_status(void *ctx, int pid, int *code) {
    call_t c = { .op = H_STATUS, .i = pid, .pi = code };
    return call(&c, ctx);
}

static int w_kill(void *ctx, int pid, int sig) {
    call_t c = { .op = H_KILL, .i = pid, .j = sig };
    return call(&c, ctx);
}

static int w_procs(void *ctx, moy_sh_ps_fn fn, void *arg) {
    call_t c = { .op = H_PROCS, .fn = (void *)fn, .arg = arg };
    return call(&c, ctx);
}

// On the face's task: a role command's crossings hop one at a time, and it
// waits between its steps here, so the console's frame goes on.
static int w_role(void *ctx, moy_sh_t *s, int argc, char **argv) {
    return ((sh_t *)ctx)->board.role(ctx, s, argc, argv);
}

static int w_df(void *ctx, const char *path, uint64_t *total, uint64_t *free_) {
    call_t c = { .op = H_DF, .path = path, .u1 = total, .u2 = free_ };
    return call(&c, ctx);
}

// The Python heap's figures cross the door with the rest.
static int w_mem(void *ctx, uint32_t out[6]) {
    call_t c = { .op = H_MEM, .mem = out };
    return call(&c, ctx);
}

static void w_hop(void *ctx, void (*fn)(void *arg), void *arg) {
    moy_tty_face_hop(((sh_t *)ctx)->f, fn, arg);
}

// -- the terminal ---------------------------------------------------------------------

static void sh_out(void *ctx, int stream, const char *p, size_t n) {
    (void)stream;
    moy_tty_out(((sh_t *)ctx)->f, p, n);
}

// The face's Ctrl-C, and now and then a tick for the tasks below the face's.
static int sh_poll(void *ctx) {
    sh_t *h = ctx;
    if ((++h->polls & 63) == 0) {
        moy_sh_board_yield();
    }
    return moy_tty_poll(h->f);
}

static long sh_in(void *ctx, void *buf, size_t n) {
    return moy_tty_in(((sh_t *)ctx)->f, buf, n);
}

// -- the text apps ------------------------------------------------------------------

static int app_run(void *ctx, const char *text, size_t n) {
    return moy_sh_run(((sh_t *)ctx)->s, text, n);
}

static int sh_app(void *ctx, moy_sh_t *s, int argc, char **argv) {
    sh_t *h = ctx;
    if (h->app != NULL || moy_sh_app_find(argv[0]) == NULL) {
        return h->app != NULL ? 1 : MOY_SH_NOT_FOUND;
    }
    int cols, rows, status;
    moy_tty_size(h->f, &cols, &rows);
    h->app = moy_sh_app_open(s, cols, rows, 1, app_run, h, argc, argv, &status);
    return status;
}

static void app_end(sh_t *h) {
    moy_sh_app_t *a = h->app;
    int full = moy_sh_app_ops(a)->full;
    h->app = NULL;
    moy_sh_app_close(a);
    moy_tty_free(h->rows);
    h->rows = NULL;
    h->nrows = 0;
    if (full) {
        static const char back[] = "\x1b[0m\x1b[?25h\x1b[?1049l";
        moy_tty_raw(h->f, back, sizeof back - 1);
    }
}

static int sh_front(void *sh) {
    sh_t *h = sh;
    if (h->app == NULL) {
        return MOY_TTY_FRONT_SHELL;
    }
    return moy_sh_app_ops(h->app)->full ? MOY_TTY_FRONT_FULL : MOY_TTY_FRONT_LINE;
}

static int sh_keys(void *sh, const char *p, size_t n) {
    sh_t *h = sh;
    if (h->app == NULL) {
        return 1;
    }
    if (moy_sh_app_keys(h->app, p, n) == MOY_SH_APP_END) {
        app_end(h);
        return 1;
    }
    return 0;
}

static int sh_line(void *sh, const char *text) {
    sh_t *h = sh;
    if (h->app == NULL) {
        return 1;
    }
    if (moy_sh_app_line(h->app, text) == MOY_SH_APP_END) {
        app_end(h);
        return 1;
    }
    return 0;
}

static uint32_t hash(const char *p, size_t n) {
    uint32_t x = 2166136261u;
    for (size_t i = 0; i < n; i++) {
        x = (x ^ (uint8_t)p[i]) * 16777619u;
    }
    return x | 1u;
}

// A full-screen app's grid to the terminal: the rows that changed since the
// last show (every row when `whole`), then its cursor where a row was sent or
// the cursor changed. A still app sends nothing.
static void sh_show(void *sh, int whole) {
    sh_t *h = sh;
    moy_term_t *t = h->app != NULL ? moy_sh_app_term(h->app) : NULL;
    if (t == NULL) {
        return;
    }
    int cols, rows;
    moy_term_size(t, &cols, &rows);
    if (h->rows == NULL || h->nrows != rows) {
        moy_tty_free(h->rows);
        h->rows = moy_tty_calloc((size_t)rows, sizeof *h->rows);
        h->nrows = h->rows != NULL ? rows : 0;
        whole = 1;
    }
    char *row = moy_tty_calloc(1, ROW_BYTES + 32);
    if (row == NULL) {
        return;
    }
    int sent = whole;
    if (whole) {
        static const char clear[] = "\x1b[?1049h\x1b[0m\x1b[H\x1b[2J";
        moy_tty_raw(h->f, clear, sizeof clear - 1);
    }
    for (int i = 0; i < h->nrows; i++) {
        int k = snprintf(row, 32, "\x1b[%d;1H", i + 1);
        size_t n = moy_term_line(t, i, row + k, ROW_BYTES);
        uint32_t x = hash(row + k, n);
        if (!whole && x == h->rows[i]) {
            continue;
        }
        h->rows[i] = x;
        sent = 1;
        memcpy(row + k + n, "\x1b[0m\x1b[K", 7);
        moy_tty_raw(h->f, row, (size_t)k + n + 7);
    }
    int c[3];
    moy_term_cursor(t, c);
    if (!sent && memcmp(c, h->cursor, sizeof c) == 0) {
        moy_tty_free(row);
        return;
    }
    memcpy(h->cursor, c, sizeof c);
    // the cursor's view line, then its column
    int k = snprintf(row, 32, "\x1b[%d;%dH\x1b[?25%c", c[0] + 1, c[1] + 1, c[2] ? 'h' : 'l');
    moy_tty_raw(h->f, row, (size_t)k);
    moy_tty_free(row);
}

static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

static void sh_tick(void *sh) {
    sh_t *h = sh;
    if (h->app != NULL) {
        moy_sh_app_tick(h->app, now_ms());
    }
}

static void sh_resize(void *sh, int cols, int rows) {
    sh_t *h = sh;
    if (h->app != NULL) {
        moy_sh_app_resize(h->app, cols, rows);
        sh_show(h, 1);
    }
}

// -- the session --------------------------------------------------------------------

static void sh_size(void *ctx, int *cols, int *rows) {
    moy_tty_size(((sh_t *)ctx)->f, cols, rows);
}

static void *sh_open(moy_tty_t *f) {
    sh_t *h = moy_tty_calloc(1, sizeof *h);
    if (h == NULL) {
        return NULL;
    }
    h->f = f;
    moy_sh_host_t host;
    moy_sh_board_host(&host, h, sh_out, sh_poll);
    h->board = host;
    host.vread = w_vread;
    host.vwrite = w_vwrite;
    host.vlist = w_vlist;
    host.lookup = w_lookup;
    host.spawn = w_spawn;
    host.status = w_status;
    host.kill = w_kill;
    host.procs = w_procs;
    host.role = w_role;
    host.df = w_df;
    host.mem = w_mem;
    host.hop = w_hop;
    host.in = sh_in;
    host.app = sh_app;
    host.size = sh_size;
    // /etc is the folder SSH reads its sign-in from, where it set one
    const char *sys = moy_tty_etc();
    if (sys != NULL) {
        size_t rn = strlen(host.root);
        host.etc = strncmp(sys, host.root, rn) == 0 ? sys + rn : sys;
    }
    h->s = moy_sh_new(&host, ARENA, "/", "/");
    if (h->s == NULL) {
        moy_tty_free(h);
        return NULL;
    }
    moy_sh_board_go_home(h->s, sys != NULL);
    if (!moy_tty_pty(f)) {
        moy_sh_setvar(h->s, "TERM", "dumb");   // a command's bytes, not a screen
    }
    return h;
}

static int sh_run(void *sh, const char *text, size_t n) {
    return moy_sh_run(((sh_t *)sh)->s, text, n);
}

static size_t sh_prompt(void *sh, int more, char *out, size_t cap) {
    sh_t *h = sh;
    if (h->app != NULL && !moy_sh_app_ops(h->app)->full) {
        const char *p = moy_sh_app_prompt(h->app);
        size_t n = strlen(p);
        n = n < cap ? n : cap - 1;
        memcpy(out, p, n);
        return n;
    }
    return moy_sh_prompt(h->s, more, out, cap);
}

static int sh_status(void *sh) {
    return moy_sh_status(((sh_t *)sh)->s);
}

static void sh_cancel(void *sh) {
    moy_sh_cancel(((sh_t *)sh)->s);
}

static void sh_close(void *sh) {
    sh_t *h = sh;
    if (h->app != NULL) {
        app_end(h);
    }
    moy_tty_note_arena(moy_sh_arena_in_use(h->s));
    moy_sh_free(h->s);
    moy_tty_free(h);
}

// Tab: one name, the rest of it (a space after a file's); several, what they
// share past the word, else the list.
static size_t sh_complete(void *sh, const char *line, size_t col, char *ins, size_t cap,
                          char *list, size_t list_cap, int *nlist) {
    sh_t *h = sh;
    *nlist = 0;
    moy_sh_cands_t *a = moy_tty_calloc(1, sizeof *a);
    char *buf = moy_tty_calloc(1, NAMES_BYTES);
    size_t k = 0;
    if (a != NULL && buf != NULL) {
        a->buf = buf;
        a->cap = NAMES_BYTES;
        moy_sh_cands(h->s, line, col, a);
        size_t wn = a->word_n;
        if (a->n == 1) {
            const char *nm = a->names[0];
            size_t nn = strlen(nm);
            k = nn - wn < cap - 1 ? nn - wn : 0;
            memcpy(ins, nm + wn, k);
            if (k == nn - wn && nm[nn - 1] != '/') {
                ins[k++] = ' ';
            }
        } else if (a->n > 1) {
            size_t common = moy_sh_cands_common(a);
            if (common > wn && common - wn < cap) {
                k = common - wn;
                memcpy(ins, a->names[0] + wn, k);
            } else {
                size_t used = 0;
                for (int i = 0; i < a->n; i++) {
                    const char *nm = a->names[i] + a->stem_n;
                    size_t nn = strlen(nm) + 1;
                    if (used + nn > list_cap) {
                        break;
                    }
                    memcpy(list + used, nm, nn);
                    used += nn;
                    (*nlist)++;
                }
            }
        }
    }
    moy_tty_free(buf);
    moy_tty_free(a);
    return k;
}

const moy_tty_shell_t moy_tty_moy_sh = {
    sh_open, sh_run, sh_prompt, sh_status, sh_cancel, sh_close,
    sh_front, sh_keys, sh_line, sh_show, sh_tick, sh_resize, sh_complete,
};
