// moy_term: the terminal's cell grid. moy_term.h has the contract.

#include <string.h>

#include "moy_term.h"

#define BLANK ((uint32_t)' ' | (MOY_TERM_INK << MOY_TERM_FG_AT))
#define PARAMS 8
#define TAB 8
#define NONE 0xFFFFFFFFu

enum { GROUND, ESC, CSI, SKIP };

struct moy_term {
    moy_term_alloc_fn alloc;
    moy_term_free_fn free;
    void *ctx;
    int cols, rows, cap;
    uint32_t *cell;                     // cap lines of cols cells
    int head;                           // the ring's index of held line 0
    int n;                              // lines held, at least rows
    uint32_t base;                      // held line 0's number
    int cx, cy;                         // the cursor; cy a screen row
    int used;                           // the screen rows the view shows
    int wrap;                           // the last column written: wrap at the next
    int shown;
    uint32_t attr;
    int sx, sy;
    uint32_t sattr;
    uint8_t st, priv;
    int np;
    int par[PARAMS];
    uint32_t u8;                        // a UTF-8 sequence being read
    int u8n;
    uint32_t low;                       // the lowest line changed since the take
    uint32_t gen;
};

static uint32_t *held(const moy_term_t *t, int i) {
    return t->cell + (size_t)((t->head + i) % t->cap) * (size_t)t->cols;
}

static uint32_t *line(const moy_term_t *t, int y) {
    return held(t, t->n - t->rows + y);
}

static void fill(uint32_t *p, int n) {
    for (int i = 0; i < n; i++) {
        p[i] = BLANK;
    }
}

static void changed(moy_term_t *t, int y) {
    uint32_t at = t->base + (uint32_t)(t->n - t->rows + y);
    if (t->low == NONE || at < t->low) {
        t->low = at;
    }
    t->gen++;
}

static void see_cursor(moy_term_t *t) {
    if (t->cy + 1 > t->used) {
        t->used = t->cy + 1;
    }
    t->gen++;
}

static int clamp(int v, int lo, int hi) {
    return v < lo ? lo : v > hi ? hi : v;
}

static void blank_all(moy_term_t *t) {
    for (int i = 0; i < t->n; i++) {
        fill(held(t, i), t->cols);
    }
}

moy_term_t *moy_term_new(int cols, int rows, int lines, moy_term_alloc_fn alloc,
                         moy_term_free_fn free, void *ctx) {
    if (cols < 1 || rows < 1) {
        return NULL;
    }
    if (lines < rows) {
        lines = rows;
    }
    moy_term_t *t = alloc(ctx, sizeof *t);
    if (t == NULL) {
        return NULL;
    }
    memset(t, 0, sizeof *t);
    t->cell = alloc(ctx, (size_t)lines * (size_t)cols * sizeof(uint32_t));
    if (t->cell == NULL) {
        free(ctx, t);
        return NULL;
    }
    t->alloc = alloc;
    t->free = free;
    t->ctx = ctx;
    t->cols = cols;
    t->rows = rows;
    t->cap = lines;
    t->n = rows;
    t->used = 1;
    t->shown = 1;
    t->attr = MOY_TERM_INK << MOY_TERM_FG_AT;
    t->sattr = t->attr;
    t->low = 0;
    blank_all(t);
    return t;
}

void moy_term_free(moy_term_t *t) {
    if (t != NULL) {
        t->free(t->ctx, t->cell);
        t->free(t->ctx, t);
    }
}

void moy_term_size(const moy_term_t *t, int *cols, int *rows) {
    *cols = t->cols;
    *rows = t->rows;
}

uint32_t moy_term_gen(const moy_term_t *t) {
    return t->gen;
}

// -- the cursor's moves -------------------------------------------------------

static void scroll_up(moy_term_t *t) {
    if (t->n < t->cap) {
        t->n++;
    } else {
        t->head = (t->head + 1) % t->cap;
        t->base++;
    }
    fill(line(t, t->rows - 1), t->cols);
    changed(t, t->rows - 1);
}

static void index_down(moy_term_t *t) {
    if (t->cy < t->rows - 1) {
        t->cy++;
    } else {
        scroll_up(t);
    }
    see_cursor(t);
}

static void put(moy_term_t *t, uint32_t cp) {
    if (t->wrap) {
        t->wrap = 0;
        t->cx = 0;
        index_down(t);
    }
    line(t, t->cy)[t->cx] = cp | t->attr;
    changed(t, t->cy);
    see_cursor(t);
    if (t->cx == t->cols - 1) {
        t->wrap = 1;
    } else {
        t->cx++;
    }
}

static void erase(moy_term_t *t, int y, int x0, int x1) {
    if (x1 > x0) {
        fill(line(t, y) + x0, x1 - x0);
        changed(t, y);
    }
}

static void reset(moy_term_t *t) {
    for (int y = 0; y < t->rows; y++) {
        erase(t, y, 0, t->cols);
    }
    t->cx = t->cy = 0;
    t->wrap = 0;
    t->used = 1;
    t->shown = 1;
    t->attr = MOY_TERM_INK << MOY_TERM_FG_AT;
    t->sx = t->sy = 0;
    t->sattr = t->attr;
    t->gen++;
}

// ED 3: the scrollback goes; the screen's lines keep their numbers.
static void drop_scrollback(moy_term_t *t) {
    int k = t->n - t->rows;
    if (k > 0) {
        t->head = (t->head + k) % t->cap;
        t->base += (uint32_t)k;
        t->n = t->rows;
        t->gen++;
    }
}

static void sgr(moy_term_t *t) {
    uint32_t fg = MOY_TERM_FG(t->attr);
    uint32_t dim = t->attr & MOY_TERM_DIM;
    int np = t->np ? t->np : 1;
    for (int i = 0; i < np; i++) {
        int v = t->par[i];
        if (v == 0) {
            fg = MOY_TERM_INK;
            dim = 0;
        } else if (v == 2) {
            dim = MOY_TERM_DIM;
        } else if (v == 22) {
            dim = 0;
        } else if (v >= 30 && v <= 37) {
            fg = (uint32_t)(v - 30);
        } else if (v >= 90 && v <= 97) {
            fg = (uint32_t)(v - 90 + 8);
        } else if (v == 39) {
            fg = MOY_TERM_INK;
        } else if ((v == 38 || v == 48) && i + 1 < np) {
            int mode = t->par[i + 1];
            if (mode == 5 && i + 2 < np) {
                if (v == 38 && t->par[i + 2] < 16) {
                    fg = (uint32_t)t->par[i + 2];
                }
                i += 2;
            } else if (mode == 2) {
                i += 4;
            } else {
                i += 1;
            }
        }
    }
    t->attr = (fg << MOY_TERM_FG_AT) | dim;
}

static int arg(const moy_term_t *t, int i, int dflt) {
    return i < t->np && t->par[i] > 0 ? t->par[i] : dflt;
}

static void csi(moy_term_t *t, uint8_t final) {
    int last = t->rows - 1;
    if (t->priv) {
        if (t->par[0] == 25 && (final == 'h' || final == 'l')) {
            t->shown = final == 'h';
            t->gen++;
        }
        return;
    }
    switch (final) {
        case 'A':
            t->cy = clamp(t->cy - arg(t, 0, 1), 0, last);
            break;
        case 'B':
            t->cy = clamp(t->cy + arg(t, 0, 1), 0, last);
            break;
        case 'C':
            t->cx = clamp(t->cx + arg(t, 0, 1), 0, t->cols - 1);
            break;
        case 'D':
            t->cx = clamp(t->cx - arg(t, 0, 1), 0, t->cols - 1);
            break;
        case 'E':
            t->cy = clamp(t->cy + arg(t, 0, 1), 0, last);
            t->cx = 0;
            break;
        case 'F':
            t->cy = clamp(t->cy - arg(t, 0, 1), 0, last);
            t->cx = 0;
            break;
        case 'G':
            t->cx = clamp(arg(t, 0, 1) - 1, 0, t->cols - 1);
            break;
        case 'd':
            t->cy = clamp(arg(t, 0, 1) - 1, 0, last);
            break;
        case 'H':
        case 'f':
            t->cy = clamp(arg(t, 0, 1) - 1, 0, last);
            t->cx = clamp(arg(t, 1, 1) - 1, 0, t->cols - 1);
            break;
        case 'J': {
            int m = t->np ? t->par[0] : 0;
            if (m == 3) {
                drop_scrollback(t);
                return;
            }
            int y0 = m == 0 ? t->cy + 1 : 0;
            int y1 = m == 1 ? t->cy : t->rows;
            for (int y = y0; y < y1; y++) {
                erase(t, y, 0, t->cols);
            }
            if (m == 0) {
                erase(t, t->cy, t->cx, t->cols);
            } else if (m == 1) {
                erase(t, t->cy, 0, t->cx + 1);
            } else {
                t->used = 0;
            }
            break;
        }
        case 'K': {
            int m = t->np ? t->par[0] : 0;
            erase(t, t->cy, m == 0 ? t->cx : 0, m == 1 ? t->cx + 1 : t->cols);
            return;
        }
        case 'm':
            sgr(t);
            return;
        case 's':
            t->sx = t->cx;
            t->sy = t->cy;
            t->sattr = t->attr;
            return;
        case 'u':
            t->cx = t->sx;
            t->cy = t->sy;
            t->attr = t->sattr;
            break;
        default:
            return;
    }
    t->wrap = 0;
    see_cursor(t);
}

static void esc(moy_term_t *t, uint8_t c) {
    t->st = GROUND;
    switch (c) {
        case '[':
            t->st = CSI;
            t->np = 0;
            t->priv = 0;
            memset(t->par, 0, sizeof t->par);
            return;
        case '7':
            t->sx = t->cx;
            t->sy = t->cy;
            t->sattr = t->attr;
            return;
        case '8':
            t->cx = t->sx;
            t->cy = t->sy;
            t->attr = t->sattr;
            break;
        case 'D':
            index_down(t);
            break;
        case 'E':
            t->cx = 0;
            index_down(t);
            break;
        case 'M':
            if (t->cy > 0) {
                t->cy--;
            }
            break;
        case 'c':
            reset(t);
            drop_scrollback(t);
            return;
        default:
            if (c >= 0x20 && c <= 0x2F) {
                t->st = SKIP;         // an intermediate: the next byte ends it
            }
            return;
    }
    t->wrap = 0;
    see_cursor(t);
}

static void control(moy_term_t *t, uint8_t c) {
    switch (c) {
        case '\n':
        case 0x0B:
        case 0x0C:
            t->cx = 0;
            t->wrap = 0;
            index_down(t);
            break;
        case '\r':
            t->cx = 0;
            t->wrap = 0;
            t->gen++;
            break;
        case '\b':
            if (t->cx > 0) {
                t->cx--;
            }
            t->wrap = 0;
            t->gen++;
            break;
        case '\t':
            if (t->wrap) {
                break;
            }
            t->cx = clamp((t->cx / TAB + 1) * TAB, 0, t->cols - 1);
            t->gen++;
            break;
        case 0x1B:
            t->st = ESC;
            break;
        default:
            break;
    }
}

static void byte(moy_term_t *t, uint8_t c) {
    if (t->st == CSI) {
        if (c >= '0' && c <= '9') {
            if (t->np == 0) {
                t->np = 1;
            }
            if (t->np <= PARAMS && t->par[t->np - 1] < 10000) {
                t->par[t->np - 1] = t->par[t->np - 1] * 10 + (c - '0');
            }
        } else if (c == ';') {
            if (t->np == 0) {
                t->np = 1;
            }
            t->np++;
        } else if (c == '?' || c == '>' || c == '=') {
            t->priv = 1;
        } else if (c >= 0x40 && c <= 0x7E) {
            t->st = GROUND;
            if (t->np > PARAMS) {
                t->np = PARAMS;
            }
            csi(t, c);
        } else if (c == 0x18 || c == 0x1A) {
            t->st = GROUND;
        }
        return;
    }
    if (t->st == ESC) {
        esc(t, c);
        return;
    }
    if (t->st == SKIP) {
        t->st = GROUND;
        return;
    }
    if (t->u8n) {
        if ((c & 0xC0) == 0x80) {
            t->u8 = (t->u8 << 6) | (c & 0x3Fu);
            if (--t->u8n == 0) {
                put(t, t->u8 <= 0x10FFFFu && t->u8 >= 0x80 ? t->u8 : 0xFFFDu);
            }
            return;
        }
        t->u8n = 0;
        put(t, 0xFFFDu);
    }
    if (c < 0x20) {
        control(t, c);
    } else if (c < 0x7F) {
        put(t, c);
    } else if (c >= 0xC2 && c <= 0xF4) {
        t->u8n = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : 1;
        t->u8 = c & (c >= 0xF0 ? 0x07u : c >= 0xE0 ? 0x0Fu : 0x1Fu);
    } else if (c != 0x7F) {
        put(t, 0xFFFDu);
    }
}

void moy_term_write(moy_term_t *t, const char *p, size_t n) {
    for (size_t i = 0; i < n; i++) {
        byte(t, (uint8_t)p[i]);
    }
}

// -- the size -------------------------------------------------------------------

int moy_term_resize(moy_term_t *t, int cols, int rows) {
    if (cols < 1 || rows < 1) {
        return -1;
    }
    if (cols == t->cols && rows == t->rows) {
        return 0;
    }
    int cap = t->cap < rows ? rows : t->cap;
    uint32_t *cell = t->alloc(t->ctx, (size_t)cap * (size_t)cols * sizeof(uint32_t));
    if (cell == NULL) {
        return -1;
    }
    int ci = t->n - t->rows + t->cy;               // the cursor's held line
    int end = t->n - t->rows + t->used;            // past the last line shown
    int n = t->n;
    if (n - rows > ci) {
        n = ci + rows;                             // the lines below the cursor go
    }
    int keep = n < cap ? n : cap;
    int from = n - keep;
    for (int i = 0; i < keep; i++) {
        uint32_t *d = cell + (size_t)i * (size_t)cols;
        int m = cols < t->cols ? cols : t->cols;
        memcpy(d, held(t, from + i), (size_t)m * sizeof(uint32_t));
        fill(d + m, cols - m);
    }
    for (int i = keep; i < rows; i++) {
        fill(cell + (size_t)i * (size_t)cols, cols);
    }
    t->free(t->ctx, t->cell);
    t->cell = cell;
    t->cap = cap;
    t->head = 0;
    t->base += (uint32_t)from;
    t->n = keep < rows ? rows : keep;
    t->cols = cols;
    t->rows = rows;
    int top = t->n - rows;
    t->cy = clamp(ci - from - top, 0, rows - 1);
    t->cx = clamp(t->cx, 0, cols - 1);
    t->used = clamp(end - from - top, t->cy + 1, rows);
    t->wrap = 0;
    t->sx = clamp(t->sx, 0, cols - 1);
    t->sy = clamp(t->sy, 0, rows - 1);
    t->low = t->base;
    t->gen++;
    return 0;
}

// -- the view -------------------------------------------------------------------

void moy_term_take(moy_term_t *t, uint32_t out[3]) {
    uint32_t count = (uint32_t)(t->n - t->rows + t->used);
    out[0] = t->base;
    out[1] = count;
    out[2] = t->low == NONE ? t->base + count : (t->low < t->base ? t->base : t->low);
    t->low = NONE;
}

void moy_term_cursor(const moy_term_t *t, int out[3]) {
    out[0] = t->n - t->rows + t->cy;
    out[1] = t->cx;
    out[2] = t->shown;
}

static size_t utf8(uint32_t cp, char *o) {
    if (cp < 0x80) {
        o[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800) {
        o[0] = (char)(0xC0 | (cp >> 6));
        o[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        o[0] = (char)(0xE0 | (cp >> 12));
        o[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        o[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    o[0] = (char)(0xF0 | (cp >> 18));
    o[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    o[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    o[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

// The SGR that sets a cell's ink: its colour wins over dim, as a text view
// draws one tone a run.
static size_t ink(uint32_t c, char *o) {
    uint32_t fg = MOY_TERM_FG(c);
    if (fg < 8) {
        memcpy(o, "\x1b[30m", 5);
        o[3] = (char)('0' + fg);
        return 5;
    }
    if (fg < 16) {
        memcpy(o, "\x1b[90m", 5);
        o[3] = (char)('0' + fg - 8);
        return 5;
    }
    memcpy(o, (c & MOY_TERM_DIM) ? "\x1b[2m" : "\x1b[0m", 4);
    return 4;
}

size_t moy_term_line(const moy_term_t *t, int i, char *out, size_t cap) {
    if (cap == 0) {
        return 0;
    }
    size_t k = 0;
    int count = t->n - t->rows + t->used;
    if (i >= 0 && i < count && i < t->n) {
        const uint32_t *p = held(t, i);
        int end = t->cols;
        while (end > 0 && MOY_TERM_CP(p[end - 1]) == ' ') {
            end--;
        }
        uint32_t was = MOY_TERM_INK << MOY_TERM_FG_AT;
        const uint32_t look = (0x1Fu << MOY_TERM_FG_AT) | MOY_TERM_DIM;
        char b[16];
        for (int x = 0; x < end; x++) {
            size_t m = 0;
            uint32_t c = p[x];
            if (MOY_TERM_CP(c) != ' ' && (c & look) != (was & look)) {
                m = ink(c, b);
                was = c;
            }
            m += utf8(MOY_TERM_CP(c), b + m);
            if (k + m + 5 > cap) {
                break;
            }
            memcpy(out + k, b, m);
            k += m;
        }
        if ((was & look) != ((MOY_TERM_INK << MOY_TERM_FG_AT) & look) && k + 5 <= cap) {
            memcpy(out + k, "\x1b[0m", 4);
            k += 4;
        }
    }
    out[k < cap ? k : cap - 1] = 0;
    return k < cap ? k : cap - 1;
}
