// moy_sh_edit: `edit FILE`, the terminal's text editor (#117), a full-screen
// app (moy_sh_app.h) in C, so the Terminal and the recovery floor edit a file
// with the same code and the floor edits `system/settings` with no VM.
//
// THE SCREEN: the file's name on the top row (`*` once changed), the text
// under it, and on the last row what was said last or the keys: with a
// keyboard's Ctrl `^S save  ^X exit`; without one (the T-Deck) the face's
// key row holds Save and Exit. A line wider than the screen scrolls
// sideways while the cursor is on it, marked `$` at the cut.
//
// THE KEYS: the bytes typed, Enter a new line, Backspace and Delete, the
// arrows, Home, End, PgUp and PgDn; ^S saves, ^X (or ^C, or Esc) closes, and
// asks first when the text changed: y saves and closes, n closes, anything
// else goes back to the text. Saving is the store's crash-safe write
// (native/moy_store/moy_fs.h's moy_fs_publish), as `settings set` writes.
//
// THE TEXT is a gap buffer from the host table's alloc (PSRAM on a board),
// at most EDIT_MAX bytes; a file that does not exist opens empty and is made
// at the first save.

#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "moy_fs.h"
#include "moy_sh_app.h"

#define EDIT_MAX (256u * 1024u)
#define PATH_CAP 512
#define NAME_CAP 48
#define MSG_CAP 80
#define TAB 8
#define GROW 1024u
#define TITLE "\x1b[0;36m"
#define DIM "\x1b[2m"
#define PLAIN "\x1b[0m"
#define CTRL(c) ((c) & 0x1F)

typedef struct {
    char path[PATH_CAP];                // the store's path
    char name[NAME_CAP];                // as the title shows it
    char *buf;
    size_t cap, g0, g1;                 // the gap: [g0, g1), the cursor at g0
    int changed;
    int ask;                            // the close's question is up
    int top;                            // the first line shown
    int want;                           // the column up and down aim at, -1 the cursor's
    char msg[MSG_CAP];
} ed_t;

// -- the text ---------------------------------------------------------------------

static size_t len(const ed_t *e) {
    return e->cap - (e->g1 - e->g0);
}

static char at(const ed_t *e, size_t i) {
    return i < e->g0 ? e->buf[i] : e->buf[i + (e->g1 - e->g0)];
}

static void move_to(ed_t *e, size_t pos) {
    if (pos < e->g0) {
        size_t k = e->g0 - pos;
        memmove(e->buf + e->g1 - k, e->buf + pos, k);
        e->g0 = pos;
        e->g1 -= k;
    } else if (pos > e->g0) {
        size_t k = pos - e->g0;
        memmove(e->buf + e->g0, e->buf + e->g1, k);
        e->g0 += k;
        e->g1 += k;
    }
}

static int room(moy_sh_app_t *a, ed_t *e, size_t need) {
    if (e->g1 - e->g0 >= need) {
        return 1;
    }
    size_t n = len(e);
    if (n + need > EDIT_MAX) {
        snprintf(e->msg, sizeof e->msg, "the file is at its %u KB", EDIT_MAX / 1024u);
        return 0;
    }
    size_t cap = e->cap + need + GROW;
    char *b = moy_sh_app_alloc(a, cap);
    if (b == NULL) {
        snprintf(e->msg, sizeof e->msg, "out of memory");
        return 0;
    }
    size_t tail = e->cap - e->g1;
    memcpy(b, e->buf, e->g0);
    memcpy(b + cap - tail, e->buf + e->g1, tail);
    moy_sh_app_free(a, e->buf);
    e->buf = b;
    e->g1 = cap - tail;
    e->cap = cap;
    return 1;
}

static void insert(moy_sh_app_t *a, ed_t *e, char c) {
    if (room(a, e, 1)) {
        e->buf[e->g0++] = c;
        e->changed = 1;
    }
}

static int cont(char c) {
    return ((unsigned char)c & 0xC0) == 0x80;
}

static void back_char(ed_t *e) {
    while (e->g0 > 0) {
        char c = e->buf[--e->g0];
        e->changed = 1;
        if (!cont(c)) {
            break;
        }
    }
}

static void del_char(ed_t *e) {
    if (e->g1 < e->cap) {
        e->g1++;
        e->changed = 1;
        while (e->g1 < e->cap && cont(e->buf[e->g1])) {
            e->g1++;
        }
    }
}

static size_t line_start(const ed_t *e, size_t pos) {
    while (pos > 0 && at(e, pos - 1) != '\n') {
        pos--;
    }
    return pos;
}

static size_t line_end(const ed_t *e, size_t pos) {
    size_t n = len(e);
    while (pos < n && at(e, pos) != '\n') {
        pos++;
    }
    return pos;
}

static int advance(int col, char c) {
    return c == '\t' ? (col / TAB + 1) * TAB : cont(c) ? col : col + 1;
}

static int col_of(const ed_t *e, size_t start, size_t pos) {
    int col = 0;
    for (size_t i = start; i < pos; i++) {
        col = advance(col, at(e, i));
    }
    return col;
}

static size_t pos_at(const ed_t *e, size_t start, int col) {
    size_t end = line_end(e, start), i = start;
    int c = 0;
    while (i < end) {
        int nx = advance(c, at(e, i));
        if (nx > col && !cont(at(e, i))) {
            break;
        }
        c = nx;
        i++;
        while (i < end && cont(at(e, i))) {
            i++;
        }
    }
    return i;
}

static int line_of(const ed_t *e, size_t pos) {
    int n = 0;
    for (size_t i = 0; i < pos; i++) {
        n += at(e, i) == '\n';
    }
    return n;
}

static int lines(const ed_t *e) {
    return line_of(e, len(e)) + 1;
}

static size_t start_of_line(const ed_t *e, int ln) {
    size_t n = len(e), i = 0;
    for (int k = 0; k < ln && i < n; i++) {
        k += at(e, i) == '\n';
    }
    return i;
}

static void vertical(ed_t *e, int d) {
    size_t s = line_start(e, e->g0);
    if (e->want < 0) {
        e->want = col_of(e, s, e->g0);
    }
    int ln = line_of(e, e->g0) + d, last = lines(e) - 1;
    ln = ln < 0 ? 0 : ln > last ? last : ln;
    move_to(e, pos_at(e, start_of_line(e, ln), e->want));
}

// -- the file -----------------------------------------------------------------------

static void save(moy_sh_app_t *a, ed_t *e) {
    size_t n = len(e), tail = e->cap - e->g1;
    char *text = moy_sh_app_alloc(a, n + 1);
    if (text == NULL) {
        snprintf(e->msg, sizeof e->msg, "not saved: out of memory");
        return;
    }
    memcpy(text, e->buf, e->g0);
    memcpy(text + e->g0, e->buf + e->g1, tail);
    int rc = moy_fs_publish(e->path, text, n);
    moy_sh_app_free(a, text);
    if (rc != 0) {
        snprintf(e->msg, sizeof e->msg, "not saved: %s", strerror(rc > 0 ? rc : EIO));
        return;
    }
    e->changed = 0;
    snprintf(e->msg, sizeof e->msg, "saved, %u bytes", (unsigned)n);
}

static int say_err(moy_sh_t *s, const char *name, const char *why) {
    char b[160];
    int n = snprintf(b, sizeof b, "edit: %s: %s\n", name, why);
    moy_sh_fd_write(s, 2, b, (size_t)n < sizeof b ? (size_t)n : sizeof b - 1);
    return 1;
}

static int ed_open(moy_sh_app_t *a, void *st, int argc, char **argv) {
    ed_t *e = st;
    moy_sh_t *s = moy_sh_app_session(a);
    if (argc != 2) {
        moy_sh_fd_write(s, 2, "usage: edit FILE\n", 17);
        return 2;
    }
    int r = moy_sh_resolve(s, argv[1], e->path, sizeof e->path);
    if (r != 0) {
        return say_err(s, argv[1], r == ENOENT ? "not a file in the store" : strerror(r));
    }
    const char *base = strrchr(argv[1], '/');
    base = base != NULL && base[1] ? base + 1 : argv[1];
    snprintf(e->name, sizeof e->name, "%s", base);
    moy_vol_stat_t vs;
    if (moy_fs_stat(e->path, &vs) == 0 && vs.is_dir) {
        return say_err(s, argv[1], "is a folder");
    }
    moy_buf_t b = {0};
    size_t n = 0;
    int rc = moy_fs_read(e->path, NULL, &b);
    if (rc == 0) {
        n = b.n;
    } else if (moy_fs_exists(e->path)) {
        return say_err(s, argv[1], rc > 0 ? strerror(rc) : "cannot be read");
    }
    if (n > EDIT_MAX) {
        moy_buf_free(&b);
        return say_err(s, argv[1], "is too big to edit");
    }
    e->cap = n + GROW;
    e->buf = moy_sh_app_alloc(a, e->cap);
    if (e->buf == NULL) {
        moy_buf_free(&b);
        return say_err(s, argv[1], "out of memory");
    }
    if (n) {
        memcpy(e->buf + e->cap - n, b.p, n);
    }
    moy_buf_free(&b);
    e->g0 = 0;
    e->g1 = e->cap - n;
    e->want = -1;
    if (rc != 0) {
        snprintf(e->msg, sizeof e->msg, "a new file");
    }
    return 0;
}

static void ed_close(moy_sh_app_t *a, void *st) {
    ed_t *e = st;
    moy_sh_app_free(a, e->buf);
    e->buf = NULL;
}

// -- the screen ---------------------------------------------------------------------

static void put_line(moy_sh_app_t *a, const ed_t *e, size_t from, int left, int cols) {
    size_t end = line_end(e, from);
    int width = col_of(e, from, end);
    int cut = width - left > cols - (left > 0);
    int room = cols - (left > 0) - cut;
    if (left > 0) {
        moy_sh_app_write(a, DIM "$" PLAIN);
    }
    int col = 0, shown = 0, kept = 0;
    for (size_t i = from; i < end; i++) {
        char c = at(e, i);
        if (cont(c)) {
            if (kept) {
                moy_sh_app_writen(a, &c, 1);
            }
            continue;
        }
        int nx = advance(col, c);
        kept = 0;
        if (c == '\t') {
            for (int k = col; k < nx; k++) {
                if (k >= left && shown < room) {
                    moy_sh_app_write(a, " ");
                    shown++;
                }
            }
        } else if (col >= left && shown < room) {
            moy_sh_app_writen(a, &c, 1);
            shown++;
            kept = 1;
        }
        col = nx;
    }
    if (cut) {
        moy_sh_app_write(a, DIM "$" PLAIN);
    }
}

static void ed_draw(moy_sh_app_t *a, void *st) {
    ed_t *e = st;
    int cols, rows;
    moy_sh_app_size(a, &cols, &rows);
    int h = rows - 2;
    int ln = line_of(e, e->g0);
    if (ln < e->top) {
        e->top = ln;
    } else if (ln >= e->top + h) {
        e->top = ln - h + 1;
    }
    char b[MSG_CAP + 64];
    moy_sh_app_write(a, "\x1b[0m\x1b[2J\x1b[H" TITLE);
    snprintf(b, sizeof b, "edit %s%s", e->name, e->changed ? " *" : "");
    moy_sh_app_clip(a, b, cols);
    moy_sh_app_write(a, PLAIN);
    size_t p = start_of_line(e, e->top), n = len(e);
    size_t cur = line_start(e, e->g0);
    int ccol = col_of(e, cur, e->g0);
    int left = ccol >= cols - 1 ? ccol - cols / 2 : 0;
    for (int r = 0; r < h && (p < n || r == 0 || at(e, p - 1) == '\n'); r++) {
        snprintf(b, sizeof b, "\x1b[%d;1H", r + 2);
        moy_sh_app_write(a, b);
        put_line(a, e, p, p == cur ? left : 0, cols);
        p = line_end(e, p);
        if (p >= n) {
            break;
        }
        p++;
    }
    snprintf(b, sizeof b, "\x1b[%d;1H" DIM, rows);
    moy_sh_app_write(a, b);
    if (e->ask) {
        moy_sh_app_write(a, PLAIN TITLE);
        moy_sh_app_clip(a, "Save the changes? y yes, n no", cols);
    } else if (e->msg[0]) {
        moy_sh_app_clip(a, e->msg, cols);
    } else if (moy_sh_app_ctrl(a)) {
        moy_sh_app_clip(a, "^S save  ^X exit", cols);
    }
    char where[24];
    int wn = snprintf(where, sizeof where, "  %d/%d", ln + 1, lines(e));
    if (wn > 0 && wn < cols) {
        snprintf(b, sizeof b, "\x1b[%d;%dH%s", rows, cols - wn + 1, where);
        moy_sh_app_write(a, b);
    }
    snprintf(b, sizeof b, PLAIN "\x1b[%d;%dH", ln - e->top + 2, ccol - left + 1 + (left > 0));
    moy_sh_app_write(a, b);
}

// -- the keys -----------------------------------------------------------------------

static int ed_keys(moy_sh_app_t *a, void *st, const char *p, size_t n) {
    ed_t *e = st;
    const char *end = p + n;
    int cols, rows;
    moy_sh_app_size(a, &cols, &rows);
    int k;
    while ((k = moy_sh_app_key(&p, end)) >= 0) {
        if (e->ask) {
            e->ask = 0;
            if (k == 'y' || k == 'Y' || k == '\r') {
                save(a, e);
                if (!e->changed) {
                    return MOY_SH_APP_END;
                }
            } else if (k == 'n' || k == 'N') {
                return MOY_SH_APP_END;
            }
            continue;
        }
        e->msg[0] = 0;
        int vert = 0;
        switch (k) {
            case CTRL('s'):
                save(a, e);
                break;
            case CTRL('x'):
            case CTRL('c'):
            case 0x1b:
                if (!e->changed) {
                    return MOY_SH_APP_END;
                }
                e->ask = 1;
                break;
            case '\r':
            case '\n':
                insert(a, e, '\n');
                break;
            case 0x08:
            case 0x7F:
                back_char(e);
                break;
            case MOY_SH_KEY_DEL:
                del_char(e);
                break;
            case MOY_SH_KEY_LEFT:
                if (e->g0 > 0) {
                    size_t q = e->g0 - 1;
                    while (q > 0 && cont(at(e, q))) {
                        q--;
                    }
                    move_to(e, q);
                }
                break;
            case MOY_SH_KEY_RIGHT:
                if (e->g1 < e->cap) {
                    size_t q = e->g0 + 1;
                    while (q < len(e) && cont(at(e, q))) {
                        q++;
                    }
                    move_to(e, q);
                }
                break;
            case MOY_SH_KEY_UP:
            case MOY_SH_KEY_DOWN:
                vertical(e, k == MOY_SH_KEY_UP ? -1 : 1);
                vert = 1;
                break;
            case MOY_SH_KEY_PGUP:
            case MOY_SH_KEY_PGDN:
                vertical(e, (k == MOY_SH_KEY_PGUP ? -1 : 1) * (rows > 3 ? rows - 3 : 1));
                vert = 1;
                break;
            case MOY_SH_KEY_HOME:
                move_to(e, line_start(e, e->g0));
                break;
            case MOY_SH_KEY_END:
                move_to(e, line_end(e, e->g0));
                break;
            default:
                if (k == '\t' || (k >= 0x20 && k < 0x100 && k != 0x7F)) {
                    insert(a, e, (char)k);
                }
                break;
        }
        if (!vert) {
            e->want = -1;
        }
    }
    ed_draw(a, e);
    return MOY_SH_APP_GO;
}

static const char *const ED_KEYS[] = {"Save", "\x13", "Exit", "\x18", NULL};

const moy_sh_app_ops_t moy_sh_edit_app = {
    .name = "edit",
    .state = sizeof(ed_t),
    .full = 1,
    .text = 1,
    .keys = ED_KEYS,
    .open = ed_open,
    .keys_in = ed_keys,
    .draw = ed_draw,
    .close = ed_close,
};
