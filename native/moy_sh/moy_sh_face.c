// moy_sh_face: the Terminal's face. moy_sh_face.h has the contract.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if __has_include("esp_timer.h")
#include "esp_timer.h"
#define NOW_MS() ((uint32_t)(esp_timer_get_time() / 1000))
#else
#include <time.h>
static uint32_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}
#define NOW_MS() now_ms()
#endif

#include "moy_sh_app.h"
#include "moy_sh_board.h"
#include "moy_sh_face.h"
#include "moy_text.h"

#define ECHO "\x1b[2m"
#define TYPED "\x1b[0;36m"
#define PLAIN "\x1b[0m"
#define SEPS " ;|&()<>"
#define MIN_COLS 8
#define RUN_MORE 1
#define RUN_EXIT 2
#define INTERRUPTED 130
#define NAMES_BYTES 8192u               // a completion's names, all of them
#define NAMES_MAX 512
#define LOG_ROWS_MAX 96                 // a text view's rows at most (MOY_UI_TEXT_ROWS)
#define PROMPT_ROW (MOY_SH_FACE_LINE * 2 + 32)

// The T-Deck's keyboard: whether its matrix types `c`, plain or with Sym
// (native/moy_input/moy_kbd.c). Weak, so a build without the driver links.
int moy_kbd_raw_key(size_t i, uint8_t *byte, uint8_t *bit, uint8_t *key, uint8_t *sym)
    __attribute__((weak));

struct moy_sh_face {
    moy_sh_screen_t *scr;
    void *(*alloc)(void *ctx, size_t n);
    void (*free)(void *ctx, void *p);
    char root[MOY_SH_SCREEN_ROOT];
    int has_root;
    int32_t cols, rows;
    // the prompt's line
    char line[MOY_SH_FACE_LINE];
    int32_t len, col;
    int more;
    // the lines run before
    char hist[MOY_SH_FACE_HISTORY][MOY_SH_FACE_LINE];
    int nhist, hi;
    // a completion's candidates: the name, where the typed word ends in it and
    // where its label starts (past the folder typed)
    char cand[MOY_SH_FACE_CANDS][MOY_SH_FACE_CAND];
    uint16_t cand_at[MOY_SH_FACE_CANDS], cand_label[MOY_SH_FACE_CANDS];
    int ncand;
    int in_row;                         // the T-Deck's keys on its row, not the prompt
    int enter;                          // a typed Enter owed to the row's focused key
    int swallow;                        // the Ctrl-C that stopped a run, still latched
    int inline_runs;                    // runs never park (moy_sh_face_inline)
    int32_t key;                        // the key typed last frame
    // the log: the grid's lines as the last frame took them
    uint32_t gen;
    int stale;
    const moy_term_t *showing;          // the grid the log took last: the shell's or an app's
    int32_t count;
    char *ring;                         // the getter's rows, `nslots` of `slot` bytes
    size_t slot, nslots, next;
    char prompt_row[PROMPT_ROW];
};

static const char *const KEYS[] = {"Tab", "^C", "left", "right", "up", "down", "Run"};
#define NKEYS 7
static const char HINTS_KEYS[] = "confirm\0Run\0Tab\0Complete\0Ctrl C\0Stop";
static const char HINTS_TDECK[] = "confirm\0Run\0cancel\0Stop";
static const char HINTS_PAD[] = "confirm\0Press\0back\0Back";
static const char HINTS_ROW[] = "confirm\0Press";
static const char HINTS_APP_TDECK[] = "cancel\0Keys";
static const char HINTS_APP_ROW[] = "confirm\0Press\0cancel\0Back";
static const char HINTS_LINE_TDECK[] = "confirm\0Run\0cancel\0Close";
static const char *const ARROWS[] = {"left", "right", "up", "down"};

static moy_ui_span_t span_n(const char *p, size_t n) {
    moy_ui_span_t s = {(const uint8_t *)p, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n)};
    return s;
}

static moy_ui_span_t span(const char *p) {
    return span_n(p, strlen(p));
}

static int utf8_len(const char *p, size_t n) {
    int k = 0;
    for (size_t i = 0; i < n; i++) {
        k += ((unsigned char)p[i] & 0xC0) != 0x80;
    }
    return k;
}

// -- the session -------------------------------------------------------------------

moy_sh_face_t *moy_sh_face_new(const char *root) {
    moy_sh_host_t h;
    moy_sh_board_host(&h, NULL, NULL, NULL);
    moy_sh_face_t *f = h.alloc(NULL, sizeof *f);
    if (f == NULL) {
        return NULL;
    }
    memset(f, 0, sizeof *f);
    f->alloc = h.alloc;
    f->free = h.free;
    if (root != NULL) {
        size_t n = strlen(root);
        if (n >= sizeof f->root) {
            h.free(NULL, f);
            return NULL;
        }
        memcpy(f->root, root, n + 1);
        f->has_root = 1;
    }
    f->cols = 40;
    f->rows = 12;
    f->stale = 1;
    return f;
}

void moy_sh_face_close_session(moy_sh_face_t *f) {
    if (f->scr != NULL) {
        moy_sh_screen_free(f->scr);
        f->scr = NULL;
    }
    f->count = 0;
    f->stale = 1;
    f->more = 0;
    f->ncand = 0;
}

void moy_sh_face_free(moy_sh_face_t *f) {
    if (f == NULL) {
        return;
    }
    moy_sh_face_close_session(f);
    if (f->ring != NULL) {
        f->free(NULL, f->ring);
    }
    f->free(NULL, f);
}

moy_sh_screen_t *moy_sh_face_screen(moy_sh_face_t *f) {
    if (f->scr == NULL) {
        f->scr = moy_sh_screen_new(f->cols, f->rows, f->has_root ? f->root : NULL);
        if (f->scr != NULL && f->inline_runs) {
            moy_sh_screen_inline(f->scr);
        }
        f->stale = 1;
    }
    return f->scr;
}

void moy_sh_face_open(moy_sh_face_t *f) {
    f->in_row = 0;
    f->stale = 1;
    moy_sh_face_screen(f);
}

static moy_sh_t *session(moy_sh_face_t *f) {
    return f->scr != NULL ? moy_sh_screen_session(f->scr) : NULL;
}

static moy_sh_app_t *app_of(const moy_sh_face_t *f) {
    return f->scr != NULL ? moy_sh_screen_app(f->scr) : NULL;
}

// A full-screen app in front: its screen is the log, and there is no prompt.
static moy_sh_app_t *full_app(const moy_sh_face_t *f) {
    moy_sh_app_t *a = app_of(f);
    return a != NULL && moy_sh_app_ops(a)->full ? a : NULL;
}

static moy_sh_app_t *line_app(const moy_sh_face_t *f) {
    moy_sh_app_t *a = app_of(f);
    return a != NULL && !moy_sh_app_ops(a)->full ? a : NULL;
}

// The grid the log shows: a full-screen app's, else the shell's.
static moy_term_t *shown(const moy_sh_face_t *f) {
    moy_sh_app_t *a = full_app(f);
    return a != NULL ? moy_sh_app_term(a) : moy_sh_screen_term(f->scr);
}

static void end_app(moy_sh_face_t *f) {
    moy_sh_screen_end_app(f->scr);
    f->in_row = 0;
    f->ncand = 0;
    f->stale = 1;
}

static void write_s(moy_sh_face_t *f, const char *s) {
    moy_term_write(moy_sh_screen_term(f->scr), s, strlen(s));
}

// A run of the session is parked: the session is the run's until it ends.
static int busy(const moy_sh_face_t *f) {
    return f->scr != NULL && moy_sh_screen_busy(f->scr);
}

static size_t prompt_of(moy_sh_face_t *f, char *out, size_t cap) {
    moy_sh_t *s = session(f);
    if (s == NULL || busy(f)) {
        out[0] = '\0';
        return 0;
    }
    moy_sh_app_t *a = line_app(f);
    if (a != NULL) {
        int n = snprintf(out, cap, "%s", moy_sh_app_prompt(a));
        return n < 0 ? 0 : (size_t)n < cap ? (size_t)n : cap - 1;
    }
    size_t n = moy_sh_prompt(s, f->more, out, cap);
    return n < cap ? n : cap - 1;
}

static void fresh_line(moy_sh_face_t *f) {
    int c[3];
    moy_term_cursor(moy_sh_screen_term(f->scr), c);
    if (c[1]) {
        write_s(f, "\n");
    }
}

// The line echoed into the grid: the prompt dim, the text typed, `tail`.
static void echo(moy_sh_face_t *f, const char *tail) {
    char p[MOY_SH_FACE_LINE];
    fresh_line(f);
    prompt_of(f, p, sizeof p);
    write_s(f, ECHO);
    write_s(f, p);
    write_s(f, TYPED);
    moy_term_write(moy_sh_screen_term(f->scr), f->line, (size_t)f->len);
    write_s(f, PLAIN);
    write_s(f, tail);
}

// -- the line ------------------------------------------------------------------------

static void set_line(moy_sh_face_t *f, const char *t, size_t n) {
    if (n >= sizeof f->line) {
        n = sizeof f->line - 1;
    }
    memcpy(f->line, t, n);
    f->line[n] = '\0';
    f->len = (int32_t)n;
    f->col = 0;
}

static void insert_ch(moy_sh_face_t *f, char ch) {
    if (f->len + 1 >= (int32_t)sizeof f->line) {
        return;
    }
    memmove(f->line + f->col + 1, f->line + f->col, (size_t)(f->len - f->col) + 1);
    f->line[f->col] = ch;
    f->len++;
    f->col++;
}

static void insert_s(moy_sh_face_t *f, const char *s, size_t n) {
    for (size_t i = 0; i < n; i++) {
        insert_ch(f, s[i]);
    }
    f->ncand = 0;
}

static void move_caret(moy_sh_face_t *f, int32_t d) {
    int32_t c = f->col + d;
    f->col = c < 0 ? 0 : c > f->len ? f->len : c;
}

// What the line editor takes of a key: Backspace, Delete (forward) and the
// printable bytes. Whether it took it.
static int edit_key(moy_sh_face_t *f, int32_t code) {
    if (code == 0x08) {
        if (f->col > 0) {
            memmove(f->line + f->col - 1, f->line + f->col, (size_t)(f->len - f->col) + 1);
            f->col--;
            f->len--;
        }
    } else if (code == 0x7F) {
        if (f->col < f->len) {
            memmove(f->line + f->col, f->line + f->col + 1, (size_t)(f->len - f->col));
            f->len--;
        }
    } else if (code >= 0x20 && code <= 0x7E) {
        insert_ch(f, (char)code);
    } else {
        return 0;
    }
    return 1;
}

const char *moy_sh_face_line(const moy_sh_face_t *f, int32_t *col) {
    if (col != NULL) {
        *col = f->col;
    }
    return f->line;
}

void moy_sh_face_set_line(moy_sh_face_t *f, const char *text, int32_t col) {
    set_line(f, text, strlen(text));
    move_caret(f, col);
}

// -- running, stopping, the history ---------------------------------------------------

static int blank(const char *s, int32_t n) {
    for (int32_t i = 0; i < n; i++) {
        if (s[i] != ' ' && s[i] != '\t' && s[i] != '\n' && s[i] != '\r') {
            return 0;
        }
    }
    return 1;
}

// After a run: the prompt's line, PS2 or a new session, and the Ctrl-C that
// stopped it kept from a second one.
static void ran(moy_sh_face_t *f, int r) {
    fresh_line(f);
    f->more = r == RUN_MORE;
    if (r == RUN_EXIT) {
        int code = moy_sh_status(session(f));
        moy_sh_face_close_session(f);
        if (moy_sh_face_screen(f) != NULL) {
            char msg[48];
            snprintf(msg, sizeof msg, "%s[the shell exited %d]%s\n", ECHO, code, PLAIN);
            write_s(f, msg);
        }
    } else if (moy_sh_status(session(f)) == INTERRUPTED) {
        f->swallow = 1;
    }
    f->stale = 1;
}

// A parked run that ended since: what follows its end.
static void settle(moy_sh_face_t *f) {
    int r;
    if (f->scr != NULL && moy_sh_screen_ended(f->scr, &r)) {
        ran(f, r);
    }
}

void moy_sh_face_submit(moy_sh_face_t *f) {
    settle(f);
    if (moy_sh_face_screen(f) == NULL || busy(f)) {
        return;
    }
    char text[MOY_SH_FACE_LINE + 1];
    memcpy(text, f->line, (size_t)f->len);
    int32_t n = f->len;
    f->ncand = 0;
    echo(f, "\n");
    set_line(f, "", 0);
    if (!blank(text, n)
            && (f->nhist == 0 || strncmp(f->hist[f->nhist - 1], text, MOY_SH_FACE_LINE) != 0
                || (int32_t)strlen(f->hist[f->nhist - 1]) != n)) {
        if (f->nhist == MOY_SH_FACE_HISTORY) {
            memmove(f->hist[0], f->hist[1], sizeof f->hist[0] * (MOY_SH_FACE_HISTORY - 1));
            f->nhist--;
        }
        memcpy(f->hist[f->nhist], text, (size_t)n);
        f->hist[f->nhist][n] = '\0';
        f->nhist++;
    }
    f->hi = f->nhist;
    moy_sh_app_t *a = line_app(f);
    if (a != NULL) {
        text[n] = '\0';
        if (moy_sh_app_line(a, text) == MOY_SH_APP_END) {
            end_app(f);
        }
        fresh_line(f);
        f->stale = 1;
        return;
    }
    text[n++] = '\n';
    int r = moy_sh_screen_run(f->scr, text, (size_t)n);
    if (r == MOY_SH_BUSY) {
        f->stale = 1;
        return;
    }
    ran(f, r);
}

// Ctrl-C at the prompt: the line dropped, and what `more` held; while a run
// is parked, the run's.
static void interrupt(moy_sh_face_t *f) {
    if (moy_sh_face_screen(f) == NULL) {
        return;
    }
    if (busy(f)) {
        moy_sh_screen_interrupt(f->scr);
        return;
    }
    echo(f, "^C\n");
    moy_sh_cancel(session(f));
    set_line(f, "", 0);
    moy_sh_app_t *a = line_app(f);
    if (a != NULL && moy_sh_app_keys(a, "\x03", 1) == MOY_SH_APP_END) {
        end_app(f);
    }
    f->more = 0;
    f->ncand = 0;
    f->hi = f->nhist;
}

// Up (-1) or down (+1) through the lines run before.
void moy_sh_face_recall(moy_sh_face_t *f, int d) {
    if (!f->nhist) {
        return;
    }
    int hi = f->hi + d;
    f->hi = hi < 0 ? 0 : hi > f->nhist ? f->nhist : hi;
    const char *t = f->hi < f->nhist ? f->hist[f->hi] : "";
    set_line(f, t, strlen(t));
    f->col = f->len;
    f->ncand = 0;
}

// -- completion ---------------------------------------------------------------------

typedef struct {
    char *buf;
    size_t used;
    const char *names[NAMES_MAX];
    int n;
    const char *prefix;                 // a folder's names must start with it
    const char *stem;                   // prepended to a folder's names
} names_t;

static int add_name(void *arg, const char *name, int is_dir, uint32_t size) {
    (void)size;
    names_t *a = arg;
    size_t pn = a->prefix ? strlen(a->prefix) : 0;
    if (a->prefix && strncmp(name, a->prefix, pn) != 0) {
        return 0;
    }
    size_t sn = a->stem ? strlen(a->stem) : 0, nn = strlen(name);
    size_t need = sn + nn + (is_dir ? 1 : 0) + 1;
    if (a->n >= NAMES_MAX || a->used + need > NAMES_BYTES) {
        return 0;
    }
    char *p = a->buf + a->used;
    memcpy(p, a->stem ? a->stem : "", sn);
    memcpy(p + sn, name, nn);
    if (is_dir) {
        p[sn + nn] = '/';
    }
    p[need - 1] = '\0';
    a->names[a->n++] = p;
    a->used += need;
    return 0;
}

static int by_name(const void *x, const void *y) {
    return strcmp(*(const char *const *)x, *(const char *const *)y);
}

static int is_sep(char c) {
    return c != '\0' && strchr(SEPS, c) != NULL;
}

void moy_sh_face_complete(moy_sh_face_t *f) {
    if (moy_sh_face_screen(f) == NULL || busy(f)) {
        return;
    }
    int32_t col = f->col, start = col;
    while (start > 0 && !is_sep(f->line[start - 1])) {
        start--;
    }
    char word[MOY_SH_FACE_LINE];
    size_t wn = (size_t)(col - start);
    memcpy(word, f->line + start, wn);
    word[wn] = '\0';
    int32_t b = start;
    while (b > 0 && f->line[b - 1] == ' ') {
        b--;
    }
    names_t *a = f->alloc(NULL, sizeof *a);
    if (a == NULL) {
        return;
    }
    memset(a, 0, sizeof *a);
    a->buf = f->alloc(NULL, NAMES_BYTES);
    if (a->buf == NULL) {
        f->free(NULL, a);
        return;
    }
    char stem[MOY_SH_FACE_LINE] = "";
    if (strchr(word, '/') == NULL && (b == 0 || is_sep(f->line[b - 1]))) {
        moy_sh_complete(session(f), word, add_name, a);
        qsort(a->names, (size_t)a->n, sizeof a->names[0], by_name);
        int k = 0;
        for (int i = 0; i < a->n; i++) {
            if (k == 0 || strcmp(a->names[k - 1], a->names[i]) != 0) {
                a->names[k++] = a->names[i];
            }
        }
        a->n = k;
    } else {
        // an argument with no slash names the working folder
        const char *slash = strrchr(word, '/');
        size_t sl = slash != NULL ? (size_t)(slash - word) + 1 : 0;
        memcpy(stem, word, sl);
        stem[sl] = '\0';
        char folder[MOY_SH_FACE_LINE] = ".";
        if (sl > 0) {
            memcpy(folder, stem, sl + 1);
        }
        while (sl > 1 && folder[sl - 1] == '/') {
            folder[--sl] = '\0';
        }
        a->prefix = word + strlen(stem);
        a->stem = stem;
        moy_sh_list(session(f), folder, add_name, a);
        qsort(a->names, (size_t)a->n, sizeof a->names[0], by_name);
    }
    size_t stem_n = strlen(stem);
    if (a->n == 1) {
        const char *nm = a->names[0];
        size_t nn = strlen(nm);
        insert_s(f, nm + wn, nn - wn);
        if (nm[nn - 1] != '/') {
            insert_s(f, " ", 1);
        }
    } else if (a->n > 1) {
        size_t common = strlen(a->names[0]);
        for (int i = 1; i < a->n; i++) {
            size_t k = 0;
            while (k < common && a->names[i][k] == a->names[0][k]) {
                k++;
            }
            common = k;
        }
        if (common > wn) {
            insert_s(f, a->names[0] + wn, common - wn);
        } else {
            echo(f, "\n");
            for (int i = 0; i < a->n; i++) {
                if (i) {
                    write_s(f, "  ");
                }
                write_s(f, a->names[i] + stem_n);
            }
            write_s(f, "\n");
            int k = 0;
            for (int i = 0; i < a->n && k < MOY_SH_FACE_CANDS; i++) {
                size_t nn = strlen(a->names[i]);
                if (nn >= MOY_SH_FACE_CAND) {
                    continue;
                }
                memcpy(f->cand[k], a->names[i], nn + 1);
                f->cand_at[k] = (uint16_t)wn;
                f->cand_label[k] = (uint16_t)stem_n;
                k++;
            }
            f->ncand = k;
            f->stale = 1;
        }
    }
    f->free(NULL, a->buf);
    f->free(NULL, a);
}

// A candidate from the key row: the rest of its word typed.
void moy_sh_face_pick(moy_sh_face_t *f, int i) {
    if (i < 0 || i >= f->ncand) {
        return;
    }
    char name[MOY_SH_FACE_CAND];
    memcpy(name, f->cand[i], sizeof name);
    size_t at = f->cand_at[i], n = strlen(name);
    f->ncand = 0;
    insert_s(f, name + at, n - at);
    if (n == 0 || name[n - 1] != '/') {
        insert_s(f, " ", 1);
    }
}

// -- input ----------------------------------------------------------------------------

// Bytes to the app in front, and the face's shell back where they end it.
static int feed(moy_sh_face_t *f, const char *p, size_t n) {
    moy_sh_app_t *a = app_of(f);
    if (a == NULL) {
        return 0;
    }
    if (moy_sh_app_keys(a, p, n) == MOY_SH_APP_END) {
        end_app(f);
    }
    return 1;
}

// A typed key as a terminal sends it: Enter a CR, the face's forward Delete
// xterm's, the rest its UTF-8.
static int feed_code(moy_sh_face_t *f, int32_t code) {
    char b[4];
    size_t n = 0;
    if (code == 0x7F) {
        const char *d = moy_sh_app_key_bytes("del");
        return feed(f, d, strlen(d));
    }
    if (code == 0x0A) {
        code = 0x0D;
    }
    if (code <= 0) {
        return 0;
    } else if (code < 0x80) {
        b[n++] = (char)code;
    } else if (code < 0x800) {
        b[n++] = (char)(0xC0 | (code >> 6));
        b[n++] = (char)(0x80 | (code & 0x3F));
    } else if (code < 0x10000) {
        b[n++] = (char)(0xE0 | (code >> 12));
        b[n++] = (char)(0x80 | ((code >> 6) & 0x3F));
        b[n++] = (char)(0x80 | (code & 0x3F));
    } else {
        return 0;
    }
    return feed(f, b, n);
}

void moy_sh_face_inline(moy_sh_face_t *f) {
    f->inline_runs = 1;
}

int moy_sh_face_busy(moy_sh_face_t *f) {
    settle(f);
    return busy(f);
}

int moy_sh_face_key(moy_sh_face_t *f, int32_t code) {
    settle(f);
    if (full_app(f) != NULL) {
        f->swallow = 0;
        return feed_code(f, code);
    }
    if (line_app(f) != NULL) {
        if (code == 4) {
            if (f->len == 0) {
                echo(f, "\n");
                end_app(f);
            }
            return 1;
        }
        if (code == 0x09) {
            insert_s(f, "    ", 4);
            return 1;
        }
    }
    if (code == 3) {
        if (f->swallow) {
            f->swallow = 0;
        } else {
            interrupt(f);
        }
        return 1;
    }
    f->swallow = 0;
    if (code == 0x0D || code == 0x0A) {
        moy_sh_face_submit(f);
        return 1;
    }
    if (code == 0x09) {
        moy_sh_face_complete(f);
        return 1;
    }
    if (edit_key(f, code)) {
        f->ncand = 0;
        return 1;
    }
    return 0;
}

void moy_sh_face_press(moy_sh_face_t *f, const char *name) {
    moy_sh_app_t *a = app_of(f);
    if (a != NULL) {
        for (const char *const *k = moy_sh_app_ops(a)->keys; k != NULL && k[0]; k += 2) {
            if (!strcmp(k[0], name)) {
                feed(f, k[1], strlen(k[1]));
                return;
            }
        }
        if (moy_sh_app_ops(a)->full) {
            const char *b = moy_sh_app_key_bytes(name);
            b = b != NULL ? b : !strcmp(name, "Tab") ? "\t" : !strcmp(name, "^C") ? "\x03"
              : !strcmp(name, "Run") ? "\r" : name;
            feed(f, b, strlen(b));
            return;
        }
        if (!strcmp(name, "Tab")) {
            insert_s(f, "    ", 4);
            return;
        }
    }
    if (!strcmp(name, "Tab")) {
        moy_sh_face_complete(f);
    } else if (!strcmp(name, "^C")) {
        interrupt(f);
    } else if (!strcmp(name, "Run")) {
        moy_sh_face_submit(f);
    } else if (!strcmp(name, "left") || !strcmp(name, "right")) {
        move_caret(f, name[0] == 'l' ? -1 : 1);
    } else if (!strcmp(name, "up") || !strcmp(name, "down")) {
        moy_sh_face_recall(f, name[0] == 'u' ? -1 : 1);
    } else {
        insert_s(f, name, strlen(name));
    }
}

int moy_sh_face_row(int desk) {
    if (desk) {
        return MOY_SH_ROW_NONE;
    }
    if (moy_ui_hint_input() == MOY_SH_ROW_TDECK) {
        return MOY_SH_ROW_TDECK;
    }
    int k = moy_ui_input();             // keys, tdeck, pad, touch: the rows' order
    return k >= MOY_SH_ROW_TDECK && k <= MOY_SH_ROW_TOUCH ? k : MOY_SH_ROW_NONE;
}

int moy_sh_face_cancel(moy_sh_face_t *f, int desk) {
    if (busy(f)) {
        moy_sh_screen_interrupt(f->scr);
        return 1;
    }
    moy_sh_app_t *a = app_of(f);
    if (a == NULL) {
        return 0;
    }
    if (moy_sh_app_ops(a)->full) {
        if (moy_sh_face_row(desk) == MOY_SH_ROW_TDECK) {
            f->in_row = !f->in_row;
        } else {
            feed(f, "\x1b", 1);
        }
        return 1;
    }
    if (f->in_row) {
        f->in_row = 0;
        return 1;
    }
    echo(f, "\n");
    set_line(f, "", 0);
    end_app(f);
    return 1;
}

const char *moy_sh_face_app(const moy_sh_face_t *f) {
    moy_sh_app_t *a = app_of(f);
    return a != NULL ? moy_sh_app_ops(a)->name : NULL;
}

int moy_sh_face_full(const moy_sh_face_t *f) {
    return full_app(f) != NULL;
}

int moy_sh_face_elsewhere(const moy_sh_face_t *f, int desk) {
    return moy_sh_face_row(desk) != MOY_SH_ROW_PAD && !f->in_row;
}

int moy_sh_face_input(moy_sh_face_t *f, int desk, int32_t key, int32_t repeats,
                      int32_t repeat_key, int32_t left, int32_t right, int32_t up,
                      int32_t down) {
    settle(f);
    int row = moy_sh_face_row(desk);
    if (row != MOY_SH_ROW_TDECK) {
        f->in_row = 0;
    }
    int hit = 0, enter = 0;
    if (app_of(f) != NULL && moy_sh_app_tick(app_of(f), NOW_MS())) {
        hit = 1;
    }
    if (full_app(f) != NULL) {
        if (key && key != f->key) {
            if (f->in_row && (key == 0x0D || key == 0x0A)) {
                enter = f->enter = 1;
            } else {
                f->in_row = 0;
                feed_code(f, key);
            }
            hit = 1;
        }
        f->key = key;
        if (repeats > 0 && (repeat_key == 8 || repeat_key == 127
                            || (repeat_key >= 32 && repeat_key < 127))) {
            for (int32_t i = 0; i < repeats; i++) {
                feed_code(f, repeat_key);
            }
            hit = 1;
        }
        if (row == MOY_SH_ROW_PAD) {
            return (hit ? MOY_SH_FACE_HIT : 0) | MOY_SH_FACE_KEYS;
        }
        if (f->in_row) {
            if (up) {
                f->in_row = 0;
                hit = 1;
            } else if (!enter) {
                return (hit ? MOY_SH_FACE_HIT : 0) | MOY_SH_FACE_KEYS;
            }
            return hit ? MOY_SH_FACE_HIT : 0;
        }
        int32_t steps[4] = {left, right, up, down};
        for (int d = 0; d < 4; d++) {
            const char *b = moy_sh_app_key_bytes(ARROWS[d]);
            for (int32_t i = 0; i < steps[d] && full_app(f) != NULL; i++) {
                feed(f, b, strlen(b));
                hit = 1;
            }
        }
        return hit ? MOY_SH_FACE_HIT : 0;
    }
    if (key && key != f->key) {
        if (f->in_row && (key == 0x0D || key == 0x0A)) {
            enter = f->enter = 1;       // the draw presses the row's focused key
            hit = 1;
        } else {
            f->in_row = 0;
            hit = moy_sh_face_key(f, key);
        }
    }
    f->key = key;
    if (repeats > 0 && (repeat_key == 8 || repeat_key == 127
                        || (repeat_key >= 32 && repeat_key < 127))) {
        for (int32_t i = 0; i < repeats; i++) {
            if (edit_key(f, repeat_key)) {
                f->ncand = 0;
                hit = 1;
            }
        }
    }
    if (row == MOY_SH_ROW_PAD) {
        return (hit ? MOY_SH_FACE_HIT : 0) | MOY_SH_FACE_KEYS;
    }
    if (f->in_row) {
        if (up) {
            f->in_row = 0;
            hit = 1;
        } else if (!enter) {
            return (hit ? MOY_SH_FACE_HIT : 0) | MOY_SH_FACE_KEYS;
        }
        return hit ? MOY_SH_FACE_HIT : 0;
    }
    int32_t dx = right - left, dy = down - up;
    if (dx) {
        move_caret(f, dx);
        hit = 1;
    }
    if (dy > 0 && row == MOY_SH_ROW_TDECK && f->hi >= f->nhist) {
        f->in_row = 1;
        hit = 1;
    } else if (dy) {
        moy_sh_face_recall(f, dy > 0 ? 1 : -1);
        hit = 1;
    }
    return hit ? MOY_SH_FACE_HIT : 0;
}

// -- the frame ----------------------------------------------------------------------

int moy_sh_face_tdeck_row(char *out, size_t cap) {
    const char *sym = MOY_SH_FACE_SYMBOLS;
    size_t k = 0;
    for (size_t i = 0; sym[i] && k + 1 < cap; i++) {
        int typed = 0;
        uint8_t byte, bit, plain, with_sym;
        for (size_t j = 0; moy_kbd_raw_key != NULL
                           && moy_kbd_raw_key(j, &byte, &bit, &plain, &with_sym); j++) {
            if (plain == (uint8_t)sym[i] || with_sym == (uint8_t)sym[i]) {
                typed = 1;
                break;
            }
        }
        if (!typed) {
            out[k++] = sym[i];
        }
    }
    out[k] = '\0';
    return (int)k;
}

static const uint8_t *log_get(void *src, int32_t i, size_t *n) {
    moy_sh_face_t *f = src;
    if (f->scr == NULL) {
        static const char none[] = "No shell on this console.";
        *n = sizeof none - 1;
        return (const uint8_t *)none;
    }
    if (f->ring == NULL || i < 0 || i >= f->count) {
        *n = 0;
        return NULL;
    }
    char *at = f->ring + (f->next++ % f->nslots) * f->slot;
    *n = moy_term_line(shown(f), (int)i, at, f->slot);
    return (const uint8_t *)at;
}

static const uint8_t *prompt_get(void *src, int32_t i, size_t *n) {
    moy_sh_face_t *f = src;
    if (i != 0) {
        *n = 0;
        return NULL;
    }
    *n = strlen(f->prompt_row);
    return (const uint8_t *)f->prompt_row;
}

int moy_sh_face_take(moy_sh_face_t *f) {
    if (f->scr == NULL) {
        if (f->count != 1 || f->stale) {
            f->count = 1;
            f->stale = 0;
            return 1;
        }
        return 0;
    }
    moy_term_t *t = shown(f);
    uint32_t g = moy_term_gen(t) & 0x3FFFFFFFu;
    if (!f->stale && g == f->gen && t == f->showing) {
        return 0;
    }
    f->showing = t;
    f->gen = g;
    f->stale = 0;
    uint32_t v[3];
    moy_term_take(t, v);
    int32_t count = (int32_t)(v[1] & 0x3FFFFFFFu);
    int c[3];
    moy_term_cursor(t, c);
    if (count > 1 && c[0] == count - 1 && c[1] == 0 && full_app(f) == NULL) {
        count--;                        // the cursor's empty line: the prompt row is it
    }
    f->count = count;
    return 1;
}

// The getter's rows: `rows` of the grid's widest line.
static void ring_fit(moy_sh_face_t *f, size_t rows) {
    size_t slot = (size_t)f->cols * MOY_TERM_COL_BYTES + 8u;
    if (f->ring != NULL && slot <= f->slot && rows <= f->nslots) {
        return;
    }
    if (f->ring != NULL) {
        f->free(NULL, f->ring);
    }
    f->ring = f->alloc(NULL, slot * rows);
    f->slot = f->ring != NULL ? slot : 0;
    f->nslots = f->ring != NULL ? rows : 0;
}

static void cut(int32_t r[4], int side, int32_t n, int32_t band[4]) {
    int32_t rest[4];
    moy_ui_cut(r, side, n, band, rest);
    memcpy(r, rest, sizeof rest);
}

static void at(moy_ui_ctx_t *c, const int32_t r[4]) {
    moy_ui_api_at(c, (int)r[0], (int)r[1], (int)r[2], (int)r[3]);
}

static int is_arrow(const char *name) {
    for (int i = 0; i < 4; i++) {
        if (!strcmp(name, ARROWS[i])) {
            return 1;
        }
    }
    return 0;
}

// A key's width in units: a word (an app's Save, Exit) three, Tab and the
// arrows two, a symbol one.
static int32_t units_of(const char *label) {
    int n = utf8_len(label, strlen(label));
    return n > 3 && !is_arrow(label) ? 3 : n > 2 ? 2 : 1;
}

// A row of keys across `rect`: the key row, the candidates or the symbols.
// Answers the label pressed, -1 none.
static int key_row(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const int32_t rect[4],
                   const char *const *labels, int n, int cands, int fs, int symbols) {
    if (n <= 0) {
        return -1;
    }
    int32_t gap = 2 * fs;
    int32_t cells[MOY_SH_FACE_CANDS + 16][4];
    int nc;
    if (cands) {
        int32_t fit = rect[2] / (24 * fs);
        n = n < (fit > 1 ? fit : 1) ? n : (fit > 1 ? fit : 1);
        if (n <= 1) {
            memcpy(cells[0], rect, sizeof cells[0]);
            nc = 1;
        } else {
            nc = moy_ui_split(rect, n, gap, 1, cells, MOY_SH_FACE_CANDS + 16);
        }
    } else {
        int32_t units = 0;
        for (int i = 0; i < n; i++) {
            units += units_of(labels[i]);
        }
        int32_t room = rect[2] - gap * (n - 1);
        int32_t unit = room >= 0 ? room / units : -((-room + units - 1) / units);
        int32_t rest[4];
        memcpy(rest, rect, sizeof rest);
        for (int i = 0; i < n; i++) {
            int32_t w = unit * units_of(labels[i]);
            if (i == n - 1) {
                w = rest[2];
            }
            int32_t band[4];
            cut(rest, MOY_UI_CUT_LEFT, w, band);
            memcpy(cells[i], band, sizeof band);
            if (i < n - 1) {
                cut(rest, MOY_UI_CUT_LEFT, gap, band);
            }
        }
        nc = n;
    }
    int hit = -1;
    for (int i = 0; i < nc; i++) {
        const char *name = labels[i];
        const char *shown = name;
        if (!strcmp(name, "left")) {
            shown = symbols ? "\xe2\x86\x90" : "<";
        } else if (!strcmp(name, "right")) {
            shown = symbols ? "\xe2\x86\x92" : ">";
        } else if (!strcmp(name, "up")) {
            shown = symbols ? "\xe2\x86\x91" : "^";
        } else if (!strcmp(name, "down")) {
            shown = symbols ? "\xe2\x86\x93" : "v";
        }
        at(c, cells[i]);
        if (moy_ui_api_button(c, s, span(shown), 0, NULL) && hit < 0) {
            hit = i;
        }
    }
    return hit;
}

// The hints a frame's strip shows: the row's, an app's, the prompt's.
static const char *hints_of(const moy_sh_face_t *f, size_t *n) {
    const char *h = NULL;
    int input = moy_ui_input();
    if (full_app(f) != NULL) {
        h = input == 1 ? (f->in_row ? HINTS_APP_ROW : HINTS_APP_TDECK)
          : input == 2 ? HINTS_ROW : NULL;
        *n = h == HINTS_APP_ROW ? sizeof HINTS_APP_ROW - 1 : h == HINTS_APP_TDECK
             ? sizeof HINTS_APP_TDECK - 1 : h == HINTS_ROW ? sizeof HINTS_ROW - 1 : 0;
        return h;
    }
    if (f->in_row) {
        *n = sizeof HINTS_ROW - 1;
        return HINTS_ROW;
    }
    switch (input) {
        case 0: *n = sizeof HINTS_KEYS - 1; return HINTS_KEYS;
        case 1:
            if (line_app(f) != NULL) {
                *n = sizeof HINTS_LINE_TDECK - 1;
                return HINTS_LINE_TDECK;
            }
            *n = sizeof HINTS_TDECK - 1;
            return HINTS_TDECK;
        case 2: *n = sizeof HINTS_PAD - 1; return HINTS_PAD;
        default: *n = 0; return NULL;
    }
}

int moy_sh_face_draw(moy_sh_face_t *f, moy_ui_ctx_t *c, const moy_ui_sink_t *s,
                     const moy_sh_face_frame_t *fr) {
    settle(f);
    // a parked run's output comes between frames, and its caret blinks
    int out = busy(f) ? MOY_SH_FACE_AGAIN : 0;
    int fs = fr->fs > 1 ? fr->fs : 1;
    int rk = moy_sh_face_row(fr->desk);
    if (f->scr != NULL) {
        moy_sh_screen_set_ctrl(f->scr, rk == MOY_SH_ROW_NONE);
    }
    moy_sh_app_t *app = app_of(f);
    moy_sh_app_t *fa = full_app(f);
    const moy_sh_app_ops_t *ops = app != NULL ? moy_sh_app_ops(app) : NULL;
    int text = ops == NULL || !ops->full || ops->text;
    moy_ui_box_t pg;
    moy_ui_api_page(c, s, span(""), NULL, &pg);
    int32_t page[4] = {fr->cx, fr->cy, fr->cw, fr->ch};
    size_t hn = 0;
    const char *hints = hints_of(f, &hn);
    if (hints != NULL && !fr->desk) {
        const uint8_t *items = fr->bar != NULL ? fr->bar : (const uint8_t *)hints;
        size_t n = fr->bar != NULL ? fr->bar_n : hn;
        if (moy_ui_api_hints_w(c, s, items, n) > 0) {
            int32_t band[4];
            cut(page, MOY_UI_CUT_BOTTOM, 20 * fs, band);
            at(c, band);
            moy_ui_api_hints(c, s, items, n, NULL);
        }
    }
    moy_font_info_t pf;
    int symbols = moy_font_info(moy_text_print_font(), &pf) && pf.first == 0;
    if (rk != MOY_SH_ROW_NONE) {
        int32_t key_h = (fr->desk ? 32 : 22) * fs;
        int32_t row[4];
        if (rk == MOY_SH_ROW_TOUCH && text) {
            static const char sym[] = MOY_SH_FACE_TOUCH_SYMBOLS;
            const char *labels[sizeof sym];
            char one[sizeof sym][2];
            int n = 0;
            for (; sym[n]; n++) {
                one[n][0] = sym[n];
                one[n][1] = '\0';
                labels[n] = one[n];
            }
            cut(page, MOY_UI_CUT_BOTTOM, key_h, row);
            int i = key_row(c, s, row, labels, n, 0, fs, symbols);
            if (i >= 0) {
                moy_sh_face_press(f, labels[i]);
                out |= MOY_SH_FACE_DAMAGE;
            }
        }
        cut(page, MOY_UI_CUT_BOTTOM, key_h, row);
        const char *labels[MOY_SH_FACE_CANDS + 16];
        char one[sizeof MOY_SH_FACE_SYMBOLS][2];
        int n = 0;
        int cands = f->ncand > 0 && app == NULL;
        for (const char *const *k = ops != NULL ? ops->keys : NULL; k != NULL && k[0]; k += 2) {
            labels[n++] = k[0];
        }
        if (cands) {
            for (; n < f->ncand; n++) {
                labels[n] = f->cand[n] + f->cand_label[n];
            }
        } else if (rk == MOY_SH_ROW_TDECK) {
            if (text) {
                char syms[sizeof MOY_SH_FACE_SYMBOLS];
                int k = moy_sh_face_tdeck_row(syms, sizeof syms);
                labels[n++] = "Tab";
                for (int j = 0; j < k; j++) {
                    one[j][0] = syms[j];
                    one[j][1] = '\0';
                    labels[n++] = one[j];
                }
            }
        } else if (fa != NULL) {
            for (int j = 0; j < 4; j++) {
                labels[n++] = ARROWS[j];
            }
        } else {
            for (int j = 0; j < NKEYS; j++) {
                labels[n++] = KEYS[j];
            }
        }
        int i = key_row(c, s, row, labels, n, cands, fs, symbols);
        if (i >= 0) {
            if (cands) {
                moy_sh_face_pick(f, i);
            } else {
                moy_sh_face_press(f, labels[i]);
            }
            out |= MOY_SH_FACE_DAMAGE;
        }
        if (f->enter) {
            f->enter = 0;
            char focus[MOY_SH_FACE_CAND];
            int fl = moy_ui_api_focus(c, span_n(NULL, 0), focus, sizeof focus);
            int found = -1;
            for (int j = 0; fl >= 0 && j < n && found < 0; j++) {
                if (!strcmp(labels[j], focus)) {
                    found = j;
                }
            }
            if (f->in_row && found >= 0) {
                if (cands) {
                    moy_sh_face_pick(f, found);
                } else {
                    moy_sh_face_press(f, labels[found]);
                }
                out |= MOY_SH_FACE_AGAIN | MOY_SH_FACE_DAMAGE;
            }
        }
        fa = full_app(f);               // a key of the row may have ended it
    }
    moy_font_info_t mono;
    moy_ui_font(MOY_UI_FONT_MONO, &mono);
    int32_t fw = mono.w * fs > 1 ? mono.w * fs : 1;
    int32_t pad = 8 * fs;
    int32_t cols = (page[2] - pad) / fw;
    cols = cols > MIN_COLS ? cols : MIN_COLS;
    int32_t lh = (mono.line + 2) * fs;
    int32_t band[4];
    if (fa == NULL) {
        cut(page, MOY_UI_CUT_BOTTOM, lh + 4 * fs, band);
    }
    if (moy_sh_face_take(f)) {
        moy_ui_api_text_aim(c, span("log"), fa != NULL ? 0 : -1, f->count);
    }
    int32_t row_px = (mono.h < mono.line ? mono.h : mono.line) * fs;
    size_t need = (size_t)(page[3] / (row_px > 0 ? row_px : 1)) + 2u;
    ring_fit(f, need < LOG_ROWS_MAX + 2u ? need : LOG_ROWS_MAX + 2u);
    at(c, page);
    int32_t opt[MOY_UI_TXO_LEN] = MOY_UI_TXO_DEFAULTS;
    opt[MOY_UI_TXO_FOLLOW] = fa == NULL;
    if (fa != NULL) {
        opt[MOY_UI_TXO_TOP] = 0;            // the app's screen from its first row
        int cur[3];
        moy_term_cursor(moy_sh_app_term(fa), cur);
        if (cur[2]) {
            opt[MOY_UI_TXO_CARET_LINE] = cur[0];
            opt[MOY_UI_TXO_CARET_COL] = cur[1];
        }
    }
    moy_ui_span_t marks[2] = {span(""), span("")};
    moy_ui_lines_t L = {f, f->count, log_get};
    int32_t got[2] = {0, 0};
    moy_ui_api_text_view(c, s, span("log"), &L, opt, marks, NULL, got);
    int32_t rows = got[1] > 1 ? got[1] : 1;
    if (fa != NULL) {
        int ac, ar;
        rows = got[1] > 2 ? got[1] - 1 : rows;  // the rows that fit whole: the view shows a part one
        moy_sh_app_size(fa, &ac, &ar);
        if (cols != ac || rows != ar) {
            moy_sh_app_resize(fa, (int)cols, (int)rows);
            moy_sh_app_size(fa, &ac, &ar);
            if (ac == cols && ar == rows) {
                f->stale = 1;
                out |= MOY_SH_FACE_AGAIN;
            }
        }
        return out;
    }
    // the prompt row: the prompt dim, the line typed, the caret's column
    char p[MOY_SH_FACE_LINE];
    size_t pn = prompt_of(f, p, sizeof p);
    snprintf(f->prompt_row, sizeof f->prompt_row, "%s%s%s%s%s", ECHO, p, TYPED, f->line, PLAIN);
    int32_t caret = utf8_len(p, pn) + utf8_len(f->line, (size_t)f->col);
    int32_t left = cols > 2 && caret - cols + 2 > 0 ? caret - cols + 2 : 0;
    int32_t popt[MOY_UI_TXO_LEN] = MOY_UI_TXO_DEFAULTS;
    popt[MOY_UI_TXO_CARET_LINE] = 0;
    popt[MOY_UI_TXO_CARET_COL] = caret;
    popt[MOY_UI_TXO_LEFT] = left;
    moy_ui_lines_t P = {f, 1, prompt_get};
    at(c, band);
    moy_ui_api_text_view(c, s, span("prompt"), &P, popt, marks, NULL, got);
    if (f->scr != NULL && (cols != f->cols || rows != f->rows)) {
        if (moy_term_resize(moy_sh_screen_term(f->scr), (int)cols, (int)rows) == 0) {
            f->cols = cols;
            f->rows = rows;
            f->stale = 1;
            out |= MOY_SH_FACE_AGAIN;
        }
    }
    return out;
}

// -- what the face shows --------------------------------------------------------------

int moy_sh_face_more(const moy_sh_face_t *f) {
    return f->more;
}

size_t moy_sh_face_prompt(moy_sh_face_t *f, char *out, size_t cap) {
    return prompt_of(f, out, cap);
}

int moy_sh_face_in_row(const moy_sh_face_t *f) {
    return f->in_row;
}

void moy_sh_face_set_in_row(moy_sh_face_t *f, int on) {
    f->in_row = on != 0;
}

int moy_sh_face_cands(const moy_sh_face_t *f) {
    return f->ncand;
}

const char *moy_sh_face_cand(const moy_sh_face_t *f, int i) {
    return i >= 0 && i < f->ncand ? f->cand[i] + f->cand_label[i] : NULL;
}

void moy_sh_face_clear_cands(moy_sh_face_t *f) {
    f->ncand = 0;
}

int moy_sh_face_history(const moy_sh_face_t *f, int i, const char **line) {
    if (line != NULL) {
        *line = i >= 0 && i < f->nhist ? f->hist[i] : NULL;
    }
    return f->nhist;
}

void moy_sh_face_size(const moy_sh_face_t *f, int32_t *cols, int32_t *rows) {
    *cols = f->cols;
    *rows = f->rows;
}

int32_t moy_sh_face_lines(const moy_sh_face_t *f) {
    return f->count;
}

size_t moy_sh_face_log_line(moy_sh_face_t *f, int32_t i, char *out, size_t cap) {
    if (f->scr == NULL || i < 0 || i >= f->count || cap == 0) {
        if (cap) {
            out[0] = '\0';
        }
        return 0;
    }
    size_t n = moy_term_line(shown(f), (int)i, out, cap);
    return n < cap ? n : cap - 1;
}
