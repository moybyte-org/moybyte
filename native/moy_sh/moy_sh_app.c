// moy_sh_app: the shell's text apps. moy_sh_app.h has the contract.

#include <stdio.h>
#include <string.h>

#include "moy_sh_app.h"

#if MOY_SH_LINK
// The face of a console with no shell of its own (moy_sh_link.c): its one app.
extern const moy_sh_app_ops_t moy_sh_remote_app;
static const moy_sh_app_ops_t *const APPS[] = {&moy_sh_remote_app};
#else
extern const moy_sh_app_ops_t moy_sh_edit_app;
extern const moy_sh_app_ops_t moy_sh_top_app;
static const moy_sh_app_ops_t python_app;

static const moy_sh_app_ops_t *const APPS[] = {&moy_sh_edit_app, &python_app, &moy_sh_top_app};
#endif
#define NAPPS ((int)(sizeof APPS / sizeof APPS[0]))

struct moy_sh_app {
    const moy_sh_app_ops_t *ops;
    void *st;
    moy_sh_t *s;
    moy_term_t *t;                      // a full-screen app's own screen
    void *(*alloc)(void *ctx, size_t n);
    void (*free)(void *ctx, void *p);
    void *actx;
    moy_sh_app_run_fn run;
    void *ctx;
    int cols, rows, ctrl;
};

const moy_sh_app_ops_t *moy_sh_app_find(const char *name) {
    for (int i = 0; name != NULL && i < NAPPS; i++) {
        if (strcmp(APPS[i]->name, name) == 0) {
            return APPS[i];
        }
    }
    return NULL;
}

static void *t_alloc(void *ctx, size_t n) {
    moy_sh_app_t *a = ctx;
    return a->alloc(a->actx, n);
}

static void t_free(void *ctx, void *p) {
    moy_sh_app_t *a = ctx;
    a->free(a->actx, p);
}

moy_sh_app_t *moy_sh_app_new(moy_sh_t *s, const moy_sh_app_heap_t *h, int cols, int rows,
                             int ctrl, moy_sh_app_run_fn run, void *ctx, int argc, char **argv,
                             int *status) {
    const moy_sh_app_ops_t *ops = moy_sh_app_find(argc > 0 ? argv[0] : NULL);
    *status = 1;
    if (ops == NULL) {
        return NULL;
    }
    moy_sh_app_t *a = h->alloc(h->ctx, sizeof *a);
    if (a == NULL) {
        return NULL;
    }
    memset(a, 0, sizeof *a);
    a->ops = ops;
    a->s = s;
    a->alloc = h->alloc;
    a->free = h->free;
    a->actx = h->ctx;
    a->run = run;
    a->ctx = ctx;
    a->cols = cols > 1 ? cols : 2;
    a->rows = rows > 3 ? rows : 4;
    a->ctrl = ctrl;
    a->st = h->alloc(h->ctx, ops->state ? ops->state : 1);
    if (a->st == NULL) {
        h->free(h->ctx, a);
        return NULL;
    }
    memset(a->st, 0, ops->state ? ops->state : 1);
    if (ops->full) {
        a->t = moy_term_new(a->cols, a->rows, a->rows, t_alloc, t_free, a);
        if (a->t == NULL) {
            h->free(h->ctx, a->st);
            h->free(h->ctx, a);
            return NULL;
        }
    }
    int r = ops->open(a, a->st, argc, argv);
    if (r != 0) {
        *status = r;
        if (a->t != NULL) {
            moy_term_free(a->t);
        }
        h->free(h->ctx, a->st);
        h->free(h->ctx, a);
        return NULL;
    }
    *status = 0;
    if (ops->full && ops->draw != NULL) {
        ops->draw(a, a->st);
    }
    return a;
}

#if !MOY_SH_LINK
moy_sh_app_t *moy_sh_app_open(moy_sh_t *s, int cols, int rows, int ctrl,
                              moy_sh_app_run_fn run, void *ctx, int argc, char **argv,
                              int *status) {
    const moy_sh_host_t *h = moy_sh_host(s);
    moy_sh_app_heap_t heap = {h->alloc, h->free, h->ctx};
    return moy_sh_app_new(s, &heap, cols, rows, ctrl, run, ctx, argc, argv, status);
}
#endif

void moy_sh_app_close(moy_sh_app_t *a) {
    if (a == NULL) {
        return;
    }
    if (a->ops->close != NULL) {
        a->ops->close(a, a->st);
    }
    if (a->t != NULL) {
        moy_term_free(a->t);
    }
    a->free(a->actx, a->st);
    a->free(a->actx, a);
}

int moy_sh_app_keys(moy_sh_app_t *a, const char *p, size_t n) {
    return a->ops->keys_in != NULL ? a->ops->keys_in(a, a->st, p, n) : MOY_SH_APP_GO;
}

int moy_sh_app_line(moy_sh_app_t *a, const char *text) {
    return a->ops->line != NULL ? a->ops->line(a, a->st, text) : MOY_SH_APP_GO;
}

int moy_sh_app_tick(moy_sh_app_t *a, uint32_t ms) {
    return a->ops->tick != NULL ? a->ops->tick(a, a->st, ms) : 0;
}

void moy_sh_app_resize(moy_sh_app_t *a, int cols, int rows) {
    if (a->t == NULL || cols < 2 || rows < 4 || (cols == a->cols && rows == a->rows)) {
        return;
    }
    if (moy_term_resize(a->t, cols, rows) != 0) {
        return;
    }
    a->cols = cols;
    a->rows = rows;
    if (a->ops->draw != NULL) {
        a->ops->draw(a, a->st);
    }
}

void moy_sh_app_set_ctrl(moy_sh_app_t *a, int ctrl) {
    if (a->ctrl != (ctrl != 0)) {
        a->ctrl = ctrl != 0;
        if (a->ops->draw != NULL) {
            a->ops->draw(a, a->st);
        }
    }
}

const moy_sh_app_ops_t *moy_sh_app_ops(const moy_sh_app_t *a) {
    return a->ops;
}

moy_term_t *moy_sh_app_term(const moy_sh_app_t *a) {
    return a->t;
}

const char *moy_sh_app_prompt(moy_sh_app_t *a) {
    return a->ops->prompt != NULL ? a->ops->prompt(a, a->st) : "";
}

moy_sh_t *moy_sh_app_session(const moy_sh_app_t *a) {
    return a->s;
}

void moy_sh_app_size(const moy_sh_app_t *a, int *cols, int *rows) {
    *cols = a->cols;
    *rows = a->rows;
}

int moy_sh_app_ctrl(const moy_sh_app_t *a) {
    return a->ctrl;
}

void moy_sh_app_writen(moy_sh_app_t *a, const char *p, size_t n) {
    if (a->t != NULL) {
        moy_term_write(a->t, p, n);
    }
}

void moy_sh_app_write(moy_sh_app_t *a, const char *s) {
    moy_sh_app_writen(a, s, strlen(s));
}

void moy_sh_app_clip(moy_sh_app_t *a, const char *s, int cols) {
    size_t n = 0;
    for (int k = 0; s[n] && k < cols; k++) {
        n++;
        while ((s[n] & 0xC0) == 0x80) {
            n++;
        }
    }
    moy_sh_app_writen(a, s, n);
}

int moy_sh_app_run(moy_sh_app_t *a, const char *text, size_t n) {
    return a->run != NULL ? a->run(a->ctx, text, n) : MOY_SH_DONE;
}

void *moy_sh_app_alloc(moy_sh_app_t *a, size_t n) {
    return a->alloc(a->actx, n ? n : 1);
}

void moy_sh_app_free(moy_sh_app_t *a, void *p) {
    if (p != NULL) {
        a->free(a->actx, p);
    }
}

// -- keys --------------------------------------------------------------------------

static const struct {
    const char *name, *bytes;
    int key;
} NAMED[] = {
    {"up", "\x1b[A", MOY_SH_KEY_UP},
    {"down", "\x1b[B", MOY_SH_KEY_DOWN},
    {"right", "\x1b[C", MOY_SH_KEY_RIGHT},
    {"left", "\x1b[D", MOY_SH_KEY_LEFT},
    {"home", "\x1b[H", MOY_SH_KEY_HOME},
    {"end", "\x1b[F", MOY_SH_KEY_END},
    {"pgup", "\x1b[5~", MOY_SH_KEY_PGUP},
    {"pgdn", "\x1b[6~", MOY_SH_KEY_PGDN},
    {"del", "\x1b[3~", MOY_SH_KEY_DEL},
};
#define NNAMED ((int)(sizeof NAMED / sizeof NAMED[0]))

const char *moy_sh_app_key_bytes(const char *name) {
    for (int i = 0; i < NNAMED; i++) {
        if (strcmp(NAMED[i].name, name) == 0) {
            return NAMED[i].bytes;
        }
    }
    return NULL;
}

int moy_sh_app_key(const char **pp, const char *end) {
    const unsigned char *p = (const unsigned char *)*pp;
    if (p >= (const unsigned char *)end) {
        return -1;
    }
    if (p[0] != 0x1b || p + 1 >= (const unsigned char *)end || (p[1] != '[' && p[1] != 'O')) {
        *pp += 1;
        return p[0];
    }
    // ESC [ params final, or ESC O final (an application-mode arrow)
    const unsigned char *q = p + 2;
    int num = 0;
    while (q < (const unsigned char *)end && ((*q >= '0' && *q <= '9') || *q == ';')) {
        if (*q != ';') {
            num = num * 10 + (*q - '0');
        }
        q++;
    }
    if (q >= (const unsigned char *)end) {
        *pp = end;
        return 0x1b;
    }
    *pp = (const char *)q + 1;
    switch (*q) {
        case 'A': return MOY_SH_KEY_UP;
        case 'B': return MOY_SH_KEY_DOWN;
        case 'C': return MOY_SH_KEY_RIGHT;
        case 'D': return MOY_SH_KEY_LEFT;
        case 'H': return MOY_SH_KEY_HOME;
        case 'F': return MOY_SH_KEY_END;
        case '~':
            return num == 1 || num == 7 ? MOY_SH_KEY_HOME
                 : num == 4 || num == 8 ? MOY_SH_KEY_END
                 : num == 5 ? MOY_SH_KEY_PGUP
                 : num == 6 ? MOY_SH_KEY_PGDN
                 : num == 3 ? MOY_SH_KEY_DEL : 0;
        default: return 0;
    }
}

#if !MOY_SH_LINK
// -- python: the console's REPL as a line app ---------------------------------------

#define REPL_MORE 100                   // `python --repl LINE`: the statement goes on
#define REPL_END 101                    // the REPL ended (SystemExit)

typedef struct {
    int more;
} py_t;

static int py_open(moy_sh_app_t *a, void *st, int argc, char **argv) {
    (void)st;
    (void)argc;
    (void)argv;
    moy_sh_t *s = a->s;
    const moy_sh_host_t *h = moy_sh_host(s);
    char *args[] = {"python", "--repl", NULL};
    int r = h->role != NULL ? h->role(h->ctx, s, 2, args) : MOY_SH_NOT_FOUND;
    if (r == 0) {
        return 0;
    }
    const char *why = r == MOY_SH_NEEDS_VM ? "python: needs the console\n"
                    : r == MOY_SH_NOT_FOUND ? "python: not served here\n" : NULL;
    if (why != NULL) {
        moy_sh_fd_write(s, 2, why, strlen(why));
        return 1;
    }
    return r;
}

// `python --repl ARG...` run in the session, ARG single-quoted.
static int py_send(moy_sh_app_t *a, const char *flag, const char *text) {
    size_t n = strlen(text), cap = 32 + n * 4;
    char *cmd = moy_sh_app_alloc(a, cap);
    if (cmd == NULL) {
        return 1;
    }
    size_t k = (size_t)snprintf(cmd, cap, "python --repl %s'", flag);
    for (size_t i = 0; i < n; i++) {
        if (text[i] == '\'') {
            memcpy(cmd + k, "'\\''", 4);
            k += 4;
        } else if (text[i] != '\n' && text[i] != '\r') {
            cmd[k++] = text[i];
        }
    }
    cmd[k++] = '\'';
    cmd[k++] = '\n';
    int r = moy_sh_app_run(a, cmd, k);
    moy_sh_app_free(a, cmd);
    return r == MOY_SH_DONE ? moy_sh_status(a->s) : REPL_END;
}

static int py_line(moy_sh_app_t *a, void *st, const char *text) {
    py_t *p = st;
    int r = py_send(a, "", text);
    p->more = r == REPL_MORE;
    return r == REPL_END ? MOY_SH_APP_END : MOY_SH_APP_GO;
}

static int py_keys(moy_sh_app_t *a, void *st, const char *k, size_t n) {
    py_t *p = st;
    for (size_t i = 0; i < n; i++) {
        if (k[i] == 3 && p->more) {
            py_send(a, "--drop ", "");
            p->more = 0;
        } else if (k[i] == 4 || k[i] == 0x1b) {
            return MOY_SH_APP_END;
        }
    }
    return MOY_SH_APP_GO;
}

static const char *py_prompt(moy_sh_app_t *a, void *st) {
    (void)a;
    return ((py_t *)st)->more ? "... " : ">>> ";
}

static const char *const PY_KEYS[] = {"Exit", "\x04", NULL};

static const moy_sh_app_ops_t python_app = {
    .name = "python",
    .state = sizeof(py_t),
    .full = 0,
    .text = 1,
    .keys = PY_KEYS,
    .open = py_open,
    .keys_in = py_keys,
    .line = py_line,
    .prompt = py_prompt,
};
#endif
