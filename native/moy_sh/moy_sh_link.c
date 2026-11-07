// moy_sh_link: the Terminal's face where the shell is another console's
// (moy_sh_link.h): the screen with no session behind it, and its one app,
// `remote`, whose grid the far shell's bytes write and whose keys go back.
//
// The far face draws a full-screen app on the alternate screen (`ESC [?1049h`
// ... `ESC [?1049l`, moy_tty_sh.c) and the grid has none, so the link keeps
// the shell's screen when the app's begins and draws it back when it ends.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_sh_app.h"
#include "moy_sh_link.h"
#include "moy_sh_screen.h"

#define IN_CAP (64u * 1024u)            // the far shell's bytes not yet drawn
#define OUT_CAP 4096u                   // keys not yet sent
#define DIM "\x1b[2m"
#define PLAIN "\x1b[0m"
#define ALT_ON "\x1b[?1049h"
#define ALT_OFF "\x1b[?1049l"
#define ALT_N 8u                        // either's length
#define ROW_CAP 2048u                   // a kept row, its inks and all

typedef struct {
    char in[IN_CAP];
    size_t in_n;
    char out[OUT_CAP];
    size_t out_n;
    int want;                           // MOY_SH_LINK_WANT_*, read once
    int cols, rows, sized;              // the grid; sized: it changed since read
    int state;                          // MOY_SH_LINK_*
    moy_sh_app_t *app;                  // the remote app open now, or NULL
    char *kept;                         // the shell's screen under an app's, or NULL
    int kept_rows, kept_cur[3];
    int said;                           // a state written to the grid, not yet drawn
} link_t;

static link_t *L;

static link_t *link_of(void) {
    if (L == NULL) {
        L = calloc(1, sizeof *L);
        if (L != NULL) {
            L->state = MOY_SH_LINK_IDLE;
        }
    }
    return L;
}

// -- what the page's transport asks (moy_sh_link.h) -------------------------------

int moy_sh_link_want(int *cols, int *rows, int *sized) {
    link_t *l = link_of();
    if (l == NULL) {
        return 0;
    }
    int w = l->want;
    l->want = MOY_SH_LINK_WANT_NONE;
    *cols = l->cols;
    *rows = l->rows;
    *sized = l->sized;
    l->sized = 0;
    return w;
}

size_t moy_sh_link_out(char *buf, size_t cap) {
    link_t *l = link_of();
    if (l == NULL) {
        return 0;
    }
    size_t n = l->out_n < cap ? l->out_n : cap;
    memcpy(buf, l->out, n);
    memmove(l->out, l->out + n, l->out_n - n);
    l->out_n -= n;
    return n;
}

size_t moy_sh_link_in(const char *p, size_t n) {
    link_t *l = link_of();
    if (l == NULL || l->state != MOY_SH_LINK_OPEN) {
        return n;                       // nothing shows a session's bytes now
    }
    size_t k = IN_CAP - l->in_n < n ? IN_CAP - l->in_n : n;
    memcpy(l->in + l->in_n, p, k);
    l->in_n += k;
    return k;
}

// The shell's screen, kept while an app has the alternate one.
static void keep(link_t *l) {
    moy_term_t *t = l->app != NULL ? moy_sh_app_term(l->app) : NULL;
    if (t == NULL || l->kept != NULL) {
        return;
    }
    int cols, rows;
    moy_term_size(t, &cols, &rows);
    l->kept = malloc((size_t)rows * ROW_CAP);
    if (l->kept == NULL) {
        return;
    }
    l->kept_rows = rows;
    for (int i = 0; i < rows; i++) {
        char *row = l->kept + (size_t)i * ROW_CAP;
        row[moy_term_line(t, i, row, ROW_CAP - 1)] = '\0';
    }
    moy_term_cursor(t, l->kept_cur);
}

// ...and drawn back when the app ends (or the session does).
static void restore(link_t *l) {
    if (l->kept == NULL) {
        return;
    }
    if (l->app != NULL) {
        char b[32];
        moy_sh_app_write(l->app, PLAIN "\x1b[H\x1b[2J");
        for (int i = 0; i < l->kept_rows; i++) {
            snprintf(b, sizeof b, "\x1b[%d;1H", i + 1);
            moy_sh_app_write(l->app, b);
            moy_sh_app_write(l->app, l->kept + (size_t)i * ROW_CAP);
            moy_sh_app_write(l->app, PLAIN);
        }
        snprintf(b, sizeof b, "\x1b[%d;%dH", l->kept_cur[0] + 1, l->kept_cur[1] + 1);
        moy_sh_app_write(l->app, b);
    }
    free(l->kept);
    l->kept = NULL;
}

// A state's words arrive between frames, with or without the far shell's
// bytes: the next tick draws them either way.
static void say(link_t *l, const char *text) {
    if (l->app != NULL) {
        moy_sh_app_write(l->app, text);
        l->said = 1;
    }
}

// What each state says, under what the far shell last drew.
void moy_sh_link_state(int state, int n) {
    link_t *l = link_of();
    if (l == NULL) {
        return;
    }
    l->state = state;
    char msg[160];
    msg[0] = '\0';
    switch (state) {
    case MOY_SH_LINK_CONNECTING:
        snprintf(msg, sizeof msg, "%sConnecting to the board...%s\r\n", DIM, PLAIN);
        break;
    case MOY_SH_LINK_OPEN:
        l->in_n = 0;
        l->out_n = 0;
        snprintf(msg, sizeof msg, "\x1b[A\x1b[2K");    // the "Connecting" line
        break;
    case MOY_SH_LINK_ENDED:
        restore(l);
        snprintf(msg, sizeof msg, "%s\r\n%s[the shell exited %d] Press Enter for a new session.%s"
                 "\r\n", PLAIN, DIM, n, PLAIN);
        break;
    case MOY_SH_LINK_LOST:
        restore(l);
        snprintf(msg, sizeof msg, "%s\r\n%sThe connection to the board was lost. Press Enter to "
                 "connect again.%s\r\n", PLAIN, DIM, PLAIN);
        break;
    case MOY_SH_LINK_UNREACHABLE:
        snprintf(msg, sizeof msg, "%sThe board does not answer. Press Enter to try again.%s\r\n",
                 DIM, PLAIN);
        break;
    case MOY_SH_LINK_REFUSED:
        snprintf(msg, sizeof msg, "%sThe board no longer takes this browser. Pair it again (reload "
                 "the page), then press Enter.%s\r\n", DIM, PLAIN);
        break;
    case MOY_SH_LINK_WAIT:
        snprintf(msg, sizeof msg, "%sToo many wrong sign-ins: wait %d s, then press Enter.%s\r\n",
                 DIM, (n + 999) / 1000, PLAIN);
        break;
    case MOY_SH_LINK_BUSY:
        snprintf(msg, sizeof msg, "%sThe board's shell is in an SSH session. Press Enter to try "
                 "again.%s\r\n", DIM, PLAIN);
        break;
    case MOY_SH_LINK_NONE:
        snprintf(msg, sizeof msg, "%sThis console has no shell.%s\r\n", DIM, PLAIN);
        break;
    default:
        break;
    }
    say(l, msg);
}

// -- the remote app -------------------------------------------------------------------

static void ask(link_t *l) {
    if (l->state == MOY_SH_LINK_NONE) {
        return;
    }
    l->want = MOY_SH_LINK_WANT_OPEN;
    moy_sh_link_state(MOY_SH_LINK_CONNECTING, 0);
}

static int r_open(moy_sh_app_t *a, void *st, int argc, char **argv) {
    (void)st;
    (void)argc;
    (void)argv;
    link_t *l = link_of();
    if (l == NULL) {
        return 1;
    }
    l->app = a;
    moy_sh_app_size(a, &l->cols, &l->rows);
    l->sized = 0;
    if (l->state == MOY_SH_LINK_NONE) {
        moy_sh_link_state(MOY_SH_LINK_NONE, 0);
    } else {
        ask(l);
    }
    return 0;
}

static int r_keys(moy_sh_app_t *a, void *st, const char *p, size_t n) {
    (void)a;
    (void)st;
    link_t *l = link_of();
    if (l->state == MOY_SH_LINK_OPEN) {
        size_t k = OUT_CAP - l->out_n < n ? OUT_CAP - l->out_n : n;
        memcpy(l->out + l->out_n, p, k);
        l->out_n += k;
    } else if (l->state != MOY_SH_LINK_CONNECTING && memchr(p, '\r', n) != NULL) {
        ask(l);
    }
    return MOY_SH_APP_GO;
}

// A resize: the grid's new size goes to the far shell, which draws again.
static void r_draw(moy_sh_app_t *a, void *st) {
    (void)st;
    link_t *l = link_of();
    int cols, rows;
    moy_sh_app_size(a, &cols, &rows);
    if (cols != l->cols || rows != l->rows) {
        l->cols = cols;
        l->rows = rows;
        l->sized = 1;
    }
}

static int r_tick(moy_sh_app_t *a, void *st, uint32_t ms) {
    (void)st;
    (void)ms;
    link_t *l = link_of();
    int said = l->said;
    l->said = 0;
    if (l->in_n == 0) {
        return said;
    }
    // the bytes up to each switch of screens, the switch done here
    const char *p = l->in, *end = l->in + l->in_n;
    while (p < end) {
        const char *q = p;
        int on = -1;
        while (q + ALT_N <= end) {
            if (q[0] == '\x1b' && memcmp(q, ALT_ON, ALT_N) == 0) {
                on = 1;
                break;
            }
            if (q[0] == '\x1b' && memcmp(q, ALT_OFF, ALT_N) == 0) {
                on = 0;
                break;
            }
            q++;
        }
        if (on < 0) {
            q = end;
        }
        moy_sh_app_writen(a, p, (size_t)(q - p));
        if (on == 1) {
            keep(l);
        } else if (on == 0) {
            restore(l);
        }
        p = on < 0 ? end : q + ALT_N;
    }
    l->in_n = 0;
    return 1;
}

static void r_close(moy_sh_app_t *a, void *st) {
    (void)a;
    (void)st;
    link_t *l = link_of();
    free(l->kept);
    l->kept = NULL;
    l->app = NULL;
    l->in_n = l->out_n = 0;
    l->said = 0;
    if (l->state != MOY_SH_LINK_NONE) {
        l->want = MOY_SH_LINK_WANT_CLOSE;
        l->state = MOY_SH_LINK_IDLE;
    }
}

static const char *const REMOTE_KEYS[] = {"Ctrl-C", "\x03", "Tab", "\t", "Esc", "\x1b", NULL};

const moy_sh_app_ops_t moy_sh_remote_app = {
    .name = "remote",
    .full = 1,
    .text = 1,
    .keys = REMOTE_KEYS,
    .open = r_open,
    .keys_in = r_keys,
    .draw = r_draw,
    .tick = r_tick,
    .close = r_close,
};

// -- the screen, with no session (moy_sh_screen.h) ------------------------------------

struct moy_sh_screen {
    moy_term_t *t;
    moy_sh_app_t *app;
    int ctrl;
};

static void *h_alloc(void *ctx, size_t n) {
    (void)ctx;
    return malloc(n ? n : 1u);
}

static void h_free(void *ctx, void *p) {
    (void)ctx;
    free(p);
}

static const moy_sh_app_heap_t HEAP = {h_alloc, h_free, NULL};

static void *t_alloc(void *ctx, size_t n) {
    (void)ctx;
    return h_alloc(NULL, n);
}

static void t_free(void *ctx, void *p) {
    (void)ctx;
    h_free(NULL, p);
}

static void open_remote(moy_sh_screen_t *f) {
    int cols, rows, status;
    char *argv[] = {"remote", NULL};
    moy_term_size(f->t, &cols, &rows);
    f->app = moy_sh_app_new(NULL, &HEAP, cols, rows, f->ctrl, NULL, NULL, 1, argv, &status);
}

moy_sh_screen_t *moy_sh_screen_new(int cols, int rows, const char *root) {
    (void)root;
    moy_sh_screen_t *f = calloc(1, sizeof *f);
    if (f == NULL) {
        return NULL;
    }
    f->t = moy_term_new(cols, rows, rows, t_alloc, t_free, f);
    if (f->t == NULL) {
        free(f);
        return NULL;
    }
    open_remote(f);
    return f;
}

void moy_sh_screen_free(moy_sh_screen_t *f) {
    if (f == NULL) {
        return;
    }
    moy_sh_screen_end_app(f);
    moy_term_free(f->t);
    free(f);
}

int moy_sh_screen_run(moy_sh_screen_t *f, const char *text, size_t n) {
    (void)f;
    (void)text;
    (void)n;
    return MOY_SH_DONE;
}

int moy_sh_screen_busy(const moy_sh_screen_t *f) {
    (void)f;
    return 0;
}

void moy_sh_screen_inline(moy_sh_screen_t *f) {
    (void)f;
}

int moy_sh_screen_ended(moy_sh_screen_t *f, int *r) {
    (void)f;
    (void)r;
    return 0;
}

void moy_sh_screen_interrupt(moy_sh_screen_t *f) {
    (void)f;
}

moy_sh_t *moy_sh_screen_session(moy_sh_screen_t *f) {
    (void)f;
    return NULL;
}

moy_term_t *moy_sh_screen_term(moy_sh_screen_t *f) {
    return f->t;
}

moy_sh_app_t *moy_sh_screen_app(const moy_sh_screen_t *f) {
    return f->app;
}

void moy_sh_screen_end_app(moy_sh_screen_t *f) {
    if (f->app != NULL) {
        moy_sh_app_close(f->app);
        f->app = NULL;
    }
}

void moy_sh_screen_set_ctrl(moy_sh_screen_t *f, int ctrl) {
    f->ctrl = ctrl;
    if (f->app != NULL) {
        moy_sh_app_set_ctrl(f->app, ctrl);
    }
}

size_t moy_sh_screen_prompt(moy_sh_screen_t *f, int more, char *out, size_t cap) {
    (void)f;
    (void)more;
    if (cap) {
        out[0] = '\0';
    }
    return 0;
}

int moy_sh_screen_status(moy_sh_screen_t *f) {
    (void)f;
    return 0;
}

void moy_sh_screen_cancel(moy_sh_screen_t *f) {
    (void)f;
}

int moy_sh_screen_cands(moy_sh_screen_t *f, const char *line, size_t col, moy_sh_cands_t *a) {
    (void)f;
    (void)line;
    (void)col;
    a->n = 0;
    a->word_n = a->stem_n = 0;
    return 0;
}

void moy_sh_screen_heap(void *(**alloc)(void *ctx, size_t n), void (**free_)(void *ctx, void *p)) {
    *alloc = h_alloc;
    *free_ = h_free;
}
