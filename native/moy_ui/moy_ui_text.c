// TextView (moy_ui_text.h): a text area in visual rows, its record and its
// rows drawn.

#include <string.h>

#include "moy_canvas.h"
#include "moy_gfx_kernels.h"
#include "moy_shape.h"
#include "moy_ui_text.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define TEXT_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#include <stdlib.h>
#define TEXT_ALLOC(n) malloc(n)
#endif

// -- the words a syntax colours ----------------------------------------------------
//
// One table for every caller: the keywords of each language and the builtins
// they share (runtime/code_layer.py installs them once, the cart API's verbs
// among them). A word installed again takes its new tone, as a dict's key
// assigned again does.

#define WORD_POOL 1024
#define WORD_MAX 160
#define WORD_SLOTS 256

// What the views share -- the rows one draw lays out, a wrapped line's
// breaks and the word table -- in one block taken at first use (PSRAM where
// the board has it): a static would hold it in internal SRAM from boot.
typedef struct {
    moy_ui_trow_t rows[MOY_UI_TEXT_ROWS];
    uint32_t starts[MOY_UI_TEXT_BREAKS];
    char pool[WORD_POOL];
    struct {
        uint16_t off;
        uint8_t len;
        uint8_t tone;           // python's tone in the low nibble, lua's in the high; 0xF none
    } word[WORD_MAX];
    uint8_t slot[WORD_SLOTS];   // word index + 1, 0 empty
    uint16_t pool_n, words;
} text_mem_t;

static text_mem_t *s_mem;

static text_mem_t *mem(void) {
    if (s_mem == NULL) {
        s_mem = (text_mem_t *)TEXT_ALLOC(sizeof(text_mem_t));
        if (s_mem != NULL) {
            memset(s_mem, 0, sizeof(*s_mem));
        }
    }
    return s_mem;
}

moy_ui_trow_t *moy_ui_text_scratch(void) {
    text_mem_t *m = mem();
    return m != NULL ? m->rows : NULL;
}

uint32_t *moy_ui_text_breaks(void) {
    text_mem_t *m = mem();
    return m != NULL ? m->starts : NULL;
}

static uint32_t word_hash(const uint8_t *p, size_t n) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; i++) {
        h = (h ^ p[i]) * 16777619u;
    }
    return h;
}

static int word_find(const uint8_t *p, size_t n) {
    text_mem_t *m = s_mem;
    if (m == NULL || n == 0 || n > 255) {
        return -1;
    }
    uint32_t h = word_hash(p, n);
    for (int k = 0; k < WORD_SLOTS; k++) {
        int i = m->slot[(h + (uint32_t)k) & (WORD_SLOTS - 1)];
        if (i == 0) {
            return -1;
        }
        i--;
        if (m->word[i].len == n && memcmp(m->pool + m->word[i].off, p, n) == 0) {
            return i;
        }
    }
    return -1;
}

void moy_ui_text_words_reset(void) {
    text_mem_t *m = s_mem;
    if (m != NULL) {
        m->pool_n = 0;
        m->words = 0;
        memset(m->slot, 0, sizeof(m->slot));
    }
}

int moy_ui_text_words(int syntax, const uint8_t *words, size_t n, int tone) {
    if ((syntax != MOY_UI_SYN_PYTHON && syntax != MOY_UI_SYN_LUA) || tone < 0
        || tone >= MOY_UI_TT_N) {
        return MOY_UI_BAD;
    }
    text_mem_t *m = mem();
    if (m == NULL) {
        return MOY_UI_NOMEM;
    }
    int shift = syntax == MOY_UI_SYN_LUA ? 4 : 0;
    size_t i = 0;
    while (i < n) {
        size_t j = i;
        while (j < n && words[j] != 0) {
            j++;
        }
        size_t len = j - i;
        if (len > 0 && len < 256) {
            int w = word_find(words + i, len);
            if (w < 0) {
                if (m->words >= WORD_MAX || m->pool_n + len > WORD_POOL) {
                    return MOY_UI_NOMEM;
                }
                w = m->words++;
                m->word[w].off = m->pool_n;
                m->word[w].len = (uint8_t)len;
                m->word[w].tone = 0xFF;
                memcpy(m->pool + m->pool_n, words + i, len);
                m->pool_n = (uint16_t)(m->pool_n + len);
                uint32_t h = word_hash(words + i, len);
                for (int k = 0; k < WORD_SLOTS; k++) {
                    uint8_t *sl = &m->slot[(h + (uint32_t)k) & (WORD_SLOTS - 1)];
                    if (*sl == 0) {
                        *sl = (uint8_t)(w + 1);
                        break;
                    }
                }
            }
            m->word[w].tone = (uint8_t)((m->word[w].tone & ~(0xF << shift)) | (tone << shift));
        }
        i = j + 1;
    }
    return MOY_UI_OK;
}

static int word_tone(const uint8_t *p, size_t n, int lua) {
    int w = word_find(p, n);
    if (w < 0) {
        return MOY_UI_TT_TEXT;
    }
    int t = (s_mem->word[w].tone >> (lua ? 4 : 0)) & 0xF;
    return t == 0xF ? MOY_UI_TT_TEXT : t;
}

// -- runs -----------------------------------------------------------------------------
//
// A line's colour runs, left to right: each a byte range [a, b) of one tone,
// the column it starts at. Bytes of an SGR escape belong to no run and take
// no column; a byte of a UTF-8 sequence's tail takes none either.

typedef void (*run_fn)(void *ctx, int tone, uint32_t a, uint32_t b, int col);

// What a byte starts (runtime/code_layer.py's _HL_CLASS): 1 a name, 2 a
// digit, 3 a quote, 4 `#`, 5 `-`, 0 anything else.
static int cls(uint8_t c) {
    if (c == 95 || (c >= 65 && c <= 90) || (c >= 97 && c <= 122)) {
        return 1;
    }
    if (c >= 48 && c <= 57) {
        return 2;
    }
    if (c == 34 || c == 39) {
        return 3;
    }
    return c == 35 ? 4 : c == 45 ? 5 : 0;
}

static int is_digit(uint8_t c) {
    return c >= '0' && c <= '9';
}

static int is_alpha(uint8_t c) {
    return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

// Columns in bytes [a, b): the bytes that start a character.
static int cols_in(const uint8_t *p, uint32_t a, uint32_t b) {
    int n = 0;
    for (uint32_t i = a; i < b; i++) {
        n += (p[i] & 0xC0) != 0x80;
    }
    return n;
}

typedef struct {
    run_fn fn;
    void *ctx;
    const uint8_t *p;
    int tone, col;
    uint32_t a, at;             // the open run's start; where the next byte is
} runs_t;

static void run_to(runs_t *r, int tone, uint32_t j) {
    if (tone != r->tone) {
        if (r->tone >= 0 && r->at > r->a) {
            r->fn(r->ctx, r->tone, r->a, r->at, r->col);
            r->col += cols_in(r->p, r->a, r->at);
        }
        r->tone = tone;
        r->a = r->at;
    }
    r->at = j;
}

static void run_end(runs_t *r) {
    if (r->tone >= 0 && r->at > r->a) {
        r->fn(r->ctx, r->tone, r->a, r->at, r->col);
    }
}

// A line of code (code_layer._highlight_runs, over bytes).
static void runs_code(runs_t *r, const uint8_t *p, uint32_t n, int lua) {
    uint32_t i = 0;
    while (i < n) {
        uint8_t b = p[i];
        int k = b < 128 ? cls(b) : 0;
        uint32_t j;
        int tone;
        if (k == 1) {
            j = i + 1;
            while (j < n && p[j] < 128 && (cls(p[j]) == 1 || cls(p[j]) == 2)) {
                j++;
            }
            tone = word_tone(p + i, j - i, lua);
        } else if (k == 2) {
            j = i + 1;
            while (j < n && (is_digit(p[j]) || p[j] == '.' || p[j] == 'x')) {
                j++;
            }
            tone = MOY_UI_TT_NUMBER;
        } else if (k == 3) {
            uint8_t q = b;
            j = i + 1;
            while (j < n) {
                uint8_t x = p[j];
                if (x == '\\' && j + 1 < n) {
                    j += 2;
                    continue;
                }
                j++;
                if (x == q) {
                    break;
                }
            }
            tone = MOY_UI_TT_STRING;
        } else if ((k == 4 && !lua) || (k == 5 && lua && i + 1 < n && p[i + 1] == '-')) {
            j = n;
            tone = MOY_UI_TT_COMMENT;
        } else {
            j = i + 1;
            while (j < n && (p[j] >= 128 || cls(p[j]) == 0)) {
                j++;
            }
            tone = MOY_UI_TT_TEXT;
        }
        if (j > n) {
            j = n;
        }
        run_to(r, tone, j);
        i = j;
    }
}

// A line of JSON (code_layer._highlight_json): a key as a builtin, a
// value's string as a string, a number, and true, false and null.
static void runs_json(runs_t *r, const uint8_t *p, uint32_t n) {
    uint32_t i = 0;
    while (i < n) {
        uint8_t ch = p[i];
        if (ch == '"') {
            uint32_t j = i + 1;
            while (j < n && p[j] != '"') {
                j += p[j] == '\\' ? 2 : 1;
            }
            j = j + 1 < n ? j + 1 : n;
            uint32_t k = j;
            while (k < n && p[k] == ' ') {
                k++;
            }
            run_to(r, k < n && p[k] == ':' ? MOY_UI_TT_BUILTIN : MOY_UI_TT_STRING, j);
            i = j;
            continue;
        }
        if (is_digit(ch) || (ch == '-' && i + 1 < n && is_digit(p[i + 1]))) {
            while (i < n && (is_digit(p[i]) || p[i] == '-' || p[i] == '+' || p[i] == '.'
                             || p[i] == 'e' || p[i] == 'E')) {
                i++;
            }
            run_to(r, MOY_UI_TT_NUMBER, i);
            continue;
        }
        if (is_alpha(ch)) {
            uint32_t j = i;
            while (j < n && is_alpha(p[j])) {
                j++;
            }
            size_t len = j - i;
            int kw = (len == 4 && (memcmp(p + i, "true", 4) == 0 || memcmp(p + i, "null", 4) == 0))
                     || (len == 5 && memcmp(p + i, "false", 5) == 0);
            run_to(r, kw ? MOY_UI_TT_KEYWORD : MOY_UI_TT_TEXT, j);
            i = j;
            continue;
        }
        i++;
        run_to(r, MOY_UI_TT_TEXT, i);
    }
}

// An SGR escape at p[i] (`ESC [ params final`): where it ends; 0 where
// none starts there.
static uint32_t sgr_at(const uint8_t *p, uint32_t n, uint32_t i, int *tone) {
    if (p[i] != 0x1B || i + 1 >= n || p[i + 1] != '[') {
        return 0;
    }
    uint32_t j = i + 2;
    while (j < n && !(p[j] >= 0x40 && p[j] <= 0x7E)) {
        j++;
    }
    if (j >= n) {
        return n;
    }
    if (p[j] == 'm') {
        // the last parameter that names a colour wins
        uint32_t k = i + 2;
        int v = 0, any = 0;
        for (;; k++) {
            if (k < j && is_digit(p[k])) {
                v = v * 10 + (p[k] - '0');
                any = 1;
                continue;
            }
            int c = any ? v : 0;
            if (c == 0 || c == 39 || c == 22) {
                *tone = MOY_UI_TT_TEXT;
            } else if (c == 2) {
                *tone = MOY_UI_TT_DIM;
            } else if ((c >= 30 && c <= 37) || (c >= 90 && c <= 97)) {
                static const uint8_t ansi[8] = {
                    MOY_UI_TT_TEXT, MOY_UI_TT_DANGER, MOY_UI_TT_STRING,
                    MOY_UI_TT_NUMBER, MOY_UI_TT_KEYWORD, MOY_UI_TT_BUILTIN,
                    MOY_UI_TT_VALUE, MOY_UI_TT_TEXT,
                };
                *tone = c == 90 ? MOY_UI_TT_COMMENT : ansi[c % 10];
            }
            v = 0;
            any = 0;
            if (k >= j) {
                break;
            }
        }
    }
    return j + 1;
}

static void runs_sgr(runs_t *r, const uint8_t *p, uint32_t n) {
    int tone = MOY_UI_TT_TEXT;
    uint32_t i = 0;
    while (i < n) {
        uint32_t e = sgr_at(p, n, i, &tone);
        if (e) {
            // close the run before it; the escape's bytes are no run's
            if (r->tone >= 0 && r->at > r->a) {
                r->fn(r->ctx, r->tone, r->a, r->at, r->col);
                r->col += cols_in(r->p, r->a, r->at);
            }
            r->tone = -1;
            r->a = r->at = e;
            i = e;
            continue;
        }
        uint32_t j = i + 1;
        while (j < n && p[j] != 0x1B) {
            j++;
        }
        run_to(r, tone, j);
        i = j;
    }
}

static void runs_of(const uint8_t *p, uint32_t n, int syntax, run_fn fn, void *ctx) {
    runs_t r = {fn, ctx, p, -1, 0, 0, 0};
    switch (syntax) {
        case MOY_UI_SYN_PYTHON:
        case MOY_UI_SYN_LUA:
            runs_code(&r, p, n, syntax == MOY_UI_SYN_LUA);
            break;
        case MOY_UI_SYN_JSON:
            runs_json(&r, p, n);
            break;
        case MOY_UI_SYN_SGR:
            runs_sgr(&r, p, n);
            break;
        default:
            run_to(&r, MOY_UI_TT_TEXT, n);
            break;
    }
    run_end(&r);
}

typedef struct {
    int32_t *out;
    int cap, n;
} runs_out_t;

static void run_out(void *ctx, int tone, uint32_t a, uint32_t b, int col) {
    runs_out_t *o = (runs_out_t *)ctx;
    if (o->n + 4 <= o->cap) {
        o->out[o->n] = (int32_t)a;
        o->out[o->n + 1] = (int32_t)b;
        o->out[o->n + 2] = col;
        o->out[o->n + 3] = tone;
    }
    o->n += 4;
}

int moy_ui_text_runs(const uint8_t *p, size_t n, int syntax, int32_t *out, int cap) {
    runs_out_t o = {out, cap, 0};
    runs_of(p, (uint32_t)n, syntax, run_out, &o);
    return o.n / 4;
}

int moy_ui_text_cols(const uint8_t *p, size_t n, int sgr) {
    if (!sgr) {
        return cols_in(p, 0, (uint32_t)n);
    }
    int c = 0, tone = 0;
    for (uint32_t i = 0; i < n;) {
        uint32_t e = sgr_at(p, (uint32_t)n, i, &tone);
        if (e) {
            i = e;
            continue;
        }
        c += (p[i] & 0xC0) != 0x80;
        i++;
    }
    return c;
}

int moy_ui_text_wrap(const uint8_t *p, size_t n, int cols, int sgr, uint32_t *starts,
                     int cap) {
    int k = 0;
    if (cap > 0) {
        starts[k] = 0;
    }
    k = 1;
    if (cols < 1) {
        return k;
    }
    uint32_t row = 0, brk = 0;      // the row's start; the byte after its last space
    int col = 0, tone = 0;
    for (uint32_t i = 0; i < n;) {
        uint32_t e = sgr ? sgr_at(p, (uint32_t)n, i, &tone) : 0;
        if (e) {
            i = e;
            continue;
        }
        uint32_t j = i + 1;
        while (j < n && (p[j] & 0xC0) == 0x80) {
            j++;
        }
        if (col == cols) {
            // the row is full: break after its last space, else here
            uint32_t at = brk > row ? brk : i;
            if (k < cap) {
                starts[k] = at;
            }
            k++;
            row = at;
            brk = row;
            col = sgr ? moy_ui_text_cols(p + at, i - at, 1) : cols_in(p, at, i);
        }
        if (p[i] == ' ') {
            brk = j;
        }
        col++;
        i = j;
    }
    return k;
}

// -- gens -------------------------------------------------------------------------------

typedef struct {
    uint32_t a, b;
} gen_t;

static void mix(gen_t *g, uint32_t v) {
    g->a = (g->a ^ (v & 0xFF)) * 16777619u;
    g->a = (g->a ^ ((v >> 8) & 0xFF)) * 16777619u;
    g->a = (g->a ^ ((v >> 16) & 0xFF)) * 16777619u;
    g->a = (g->a ^ (v >> 24)) * 16777619u;
    g->b = (g->b ^ v) * 0x9E3779B1u;
    g->b = (g->b << 13) | (g->b >> 19);
}

static void mix_bytes(gen_t *g, const uint8_t *p, size_t n) {
    uint32_t a = g->a, b = g->b;
    for (size_t i = 0; i < n; i++) {
        a = (a ^ p[i]) * 16777619u;
        b = (b + p[i]) * 0x85EBCA6Bu;
        b ^= b >> 15;
    }
    g->a = a;
    g->b = b;
}

// -- a row's marks ------------------------------------------------------------------

static int lower(int c) {
    return c >= 'A' && c <= 'Z' ? c + 32 : c;
}

// The next match of `q` in p[from, n) at or after `from`, by bytes: its byte
// index, or -1.
static int32_t find_at(const uint8_t *p, uint32_t n, uint32_t from, moy_ui_span_t q, int ci) {
    if (q.n == 0) {
        return (int32_t)from <= (int32_t)n ? (int32_t)from : -1;
    }
    for (uint32_t i = from; i + q.n <= n; i++) {
        uint32_t k = 0;
        while (k < q.n && (ci ? lower(p[i + k]) == lower(q.p[k]) : p[i + k] == q.p[k])) {
            k++;
        }
        if (k == q.n) {
            return (int32_t)i;
        }
    }
    return -1;
}

typedef struct {
    int32_t line, ca, cb;       // the row's line; its first column and the column after its last
    int cur, err, vcol;         // the current row; the error's; the caret's column on screen, or <0
    int sel0, sel1;             // the selection's visible columns (sel1 <= sel0: none)
    int fade;
} marks_t;

static void row_marks(const int32_t *v, const moy_ui_trow_t *r, marks_t *m) {
    int left = v[MOY_UI_TV_LEFT], cols = v[MOY_UI_TV_COLS];
    int sgr = v[MOY_UI_TV_SYNTAX] == MOY_UI_SYN_SGR;
    int len = r->p != NULL ? moy_ui_text_cols(r->p + r->a, r->b - r->a, sgr) : 0;
    m->line = r->line;
    m->ca = r->ca;
    m->cb = r->ca + len;
    m->cur = r->p != NULL && r->line == v[MOY_UI_TV_CARET_ROW] && v[MOY_UI_TV_CARET_ROW] >= 0;
    m->err = r->p != NULL && r->line == v[MOY_UI_TV_ERR_ROW] && v[MOY_UI_TV_ERR_ROW] >= 0;
    m->fade = (v[MOY_UI_TV_FLAGS] & MOY_UI_TVF_FADE) && len - left > cols;
    m->vcol = -1;
    if (m->cur) {
        int c = v[MOY_UI_TV_CARET_COL] - r->ca - left;
        if (c >= 0 && c <= cols) {
            m->vcol = c;
        }
    }
    m->sel0 = m->sel1 = 0;
    int r0 = v[MOY_UI_TV_SEL_R0];
    if (r->p != NULL && r0 >= 0) {
        int r1 = v[MOY_UI_TV_SEL_R1];
        if (r->line >= r0 && r->line <= r1) {
            int s = r->line == r0 ? v[MOY_UI_TV_SEL_C0] : 0;
            int e = r->line == r1 ? v[MOY_UI_TV_SEL_C1] : (int)(r->ca + len);
            s -= r->ca;
            e -= r->ca;
            int vs = s > left ? s : left, we = left + cols;
            int ve = e < we ? e : we;
            if (ve > vs) {
                m->sel0 = vs - left;
                m->sel1 = ve - left;
            }
        }
    }
}

// A row's gen: what it draws, hashed.
static void row_gen(const int32_t *v, const moy_ui_trow_t *r, const moy_ui_trow_t *above,
                    moy_ui_span_t err, moy_ui_span_t find, uint32_t *ga, uint32_t *gb) {
    gen_t g = {2166136261u, 0x12345u};
    // the error's underline of the row above reaches this row's top
    if (above != NULL && above->p != NULL && v[MOY_UI_TV_ERR_ROW] >= 0
        && above->line == v[MOY_UI_TV_ERR_ROW]
        && v[MOY_UI_TV_TEXT_H] + v[MOY_UI_TV_FS] > v[MOY_UI_TV_LH]) {
        mix(&g, 0xE7u);
    }
    if (r->p == NULL) {
        *ga = g.a ^ 0x5EEDu;
        *gb = g.b ^ 0xE11Du;
        return;
    }
    marks_t m;
    row_marks(v, r, &m);
    mix_bytes(&g, r->p, r->n);
    mix(&g, r->a);
    mix(&g, r->b);
    mix(&g, (uint32_t)r->ca);
    if (v[MOY_UI_TV_GUTTER] > 0) {
        mix(&g, (uint32_t)(r->line + v[MOY_UI_TV_NUM0]));
    }
    mix(&g, (uint32_t)(m.cur | (m.err << 1) | (m.fade << 2)));
    mix(&g, (uint32_t)m.vcol);
    mix(&g, (uint32_t)m.sel0);
    mix(&g, (uint32_t)m.sel1);
    if (m.err) {
        mix_bytes(&g, err.p, err.n);
    }
    if (find.n > 0) {
        int ci = v[MOY_UI_TV_FIND_CI] != 0;
        uint32_t at = 0;
        for (;;) {
            int32_t i = find_at(r->p, r->n, at, find, ci);
            if (i < 0) {
                break;
            }
            mix(&g, (uint32_t)i);
            at = (uint32_t)i + find.n;
        }
        mix(&g, 0xF1D0000u | find.n);    // a match draws the query's width
    }
    *ga = g.a;
    *gb = g.b;
}

// The view's key: everything every row draws by.
static uint32_t view_key(const int32_t *v) {
    gen_t g = {2166136261u, 7u};
    static const uint8_t words[] = {
        MOY_UI_TV_X, MOY_UI_TV_Y, MOY_UI_TV_W, MOY_UI_TV_H, MOY_UI_TV_LH, MOY_UI_TV_CELL,
        MOY_UI_TV_TEXT_H, MOY_UI_TV_FS, MOY_UI_TV_FONT, MOY_UI_TV_TX, MOY_UI_TV_MARK,
        MOY_UI_TV_GUTTER, MOY_UI_TV_COLS, MOY_UI_TV_LEFT, MOY_UI_TV_FIND_CI,
        MOY_UI_TV_SYNTAX, MOY_UI_TV_FLAGS, MOY_UI_TV_BG, MOY_UI_TV_CARET, MOY_UI_TV_SEL,
        MOY_UI_TV_GUT, MOY_UI_TV_GUT_CUR, MOY_UI_TV_VALUE, MOY_UI_TV_ERR, MOY_UI_TV_KEY,
    };
    for (size_t i = 0; i < sizeof(words); i++) {
        int32_t w = v[words[i]];
        if (words[i] == MOY_UI_TV_FLAGS) {
            w &= ~MOY_UI_TVF_GROUND;    // who fills R's ground draws no other pixel
        }
        mix(&g, (uint32_t)w);
    }
    for (int i = 0; i < MOY_UI_TT_N; i++) {
        mix(&g, (uint32_t)v[MOY_UI_TV_TONE0 + i]);
    }
    uint32_t k = g.a ^ g.b;
    return k ? k : 1;
}

// -- the record --------------------------------------------------------------------------

size_t moy_ui_text_size(void) {
    return sizeof(moy_ui_text_t);
}

void moy_ui_text_reset(moy_ui_text_t *t) {
    t->magic = MOY_UI_TEXT_MAGIC;
    t->key = 0;
    t->rows = 0;
    t->n_floor = 0;
    t->whole = 1;
}

int moy_ui_text_rows(const int32_t *v) {
    int lh = v[MOY_UI_TV_LH];
    if (lh <= 0 || v[MOY_UI_TV_H] <= 0) {
        return 0;
    }
    int off = v[MOY_UI_TV_OFF];
    return (v[MOY_UI_TV_H] + off + lh - 1) / lh;
}

static void floor_set(moy_ui_text_t *t, int i) {
    t->floor[i >> 3] |= (uint8_t)(1u << (i & 7));
}

static int floor_has(const moy_ui_text_t *t, int i) {
    return (t->floor[i >> 3] >> (i & 7)) & 1;
}

int moy_ui_text_plan(moy_ui_text_t *t, int32_t *v, const moy_ui_trow_t *rows, int n,
                     moy_ui_span_t err, moy_ui_span_t find) {
    if (t->magic != MOY_UI_TEXT_MAGIC) {
        moy_ui_text_reset(t);
    }
    int lh = v[MOY_UI_TV_LH];
    uint32_t key = view_key(v);
    int m = n < MOY_UI_TEXT_ROWS ? n : MOY_UI_TEXT_ROWS;
    for (int i = 0; i < m; i++) {
        row_gen(v, &rows[i], i > 0 ? &rows[i - 1] : NULL, err, find, &t->ngen[i], &t->ngen2[i]);
    }
    t->want = key;
    t->want_top = v[MOY_UI_TV_TOP];
    t->want_off = v[MOY_UI_TV_OFF];
    t->want_rows = (int16_t)(n <= MOY_UI_TEXT_ROWS ? n : 0);
    memset(t->floor, 0, sizeof(t->floor));
    t->n_floor = 0;
    v[MOY_UI_TV_DY] = 0;
    v[MOY_UI_TV_FLOOR] = v[MOY_UI_TV_H];
    v[MOY_UI_TV_FLOOR_Y0] = v[MOY_UI_TV_Y];
    v[MOY_UI_TV_FLOOR_Y1] = v[MOY_UI_TV_Y] + v[MOY_UI_TV_H];
    v[MOY_UI_TV_WHOLE] = 1;
    t->whole = 1;
    if (lh <= 0 || n > MOY_UI_TEXT_ROWS || t->key != key || t->rows <= 0) {
        return 0;
    }
    // R's pixels move by the scroll: old row k at y0 + (k - top0) lh - off0
    int32_t was = t->top * lh + t->off, now = v[MOY_UI_TV_TOP] * lh + v[MOY_UI_TV_OFF];
    int32_t dy = was - now;
    int h = v[MOY_UI_TV_H];
    if (dy <= -h || dy >= h) {
        return 0;
    }
    int ry = v[MOY_UI_TV_Y];
    // the band the move uncovers, in R's rows
    int band0 = dy > 0 ? ry : ry + h + dy, band1 = dy > 0 ? ry + dy : ry + h;
    int over0 = v[MOY_UI_TV_OVER_Y0] + dy, over1 = v[MOY_UI_TV_OVER_Y1] + dy;
    int px = 0, fy0 = ry + h, fy1 = ry;
    for (int i = 0; i < n; i++) {
        int y = ry + i * lh - v[MOY_UI_TV_OFF];
        int y1 = y + lh;
        int k = v[MOY_UI_TV_TOP] + i - t->top;    // its index in the record
        int same = k >= 0 && k < t->rows && t->gen[k] == t->ngen[i]
                   && t->gen2[k] == t->ngen2[i];
        // a row the record holds cut (R's edge cut it) is drawn again
        int old_y = ry + k * lh - t->off;
        if (same && (old_y < ry || old_y + lh > ry + h)) {
            same = 0;
        }
        if (dy != 0 && y < band1 && y1 > band0) {
            same = 0;
        }
        if (over1 > over0 && y < over1 && y1 > over0) {
            same = 0;
        }
        if (!same) {
            floor_set(t, i);
            t->n_floor++;
            int a = y > ry ? y : ry, b = y1 < ry + h ? y1 : ry + h;
            if (b > a) {
                px += b - a;
                fy0 = a < fy0 ? a : fy0;
                fy1 = b > fy1 ? b : fy1;
            }
        }
    }
    // an error's underline under its row's glyph box reaches into the row
    // after where the box fills the row: that row draws with it
    int leak = v[MOY_UI_TV_TEXT_H] + v[MOY_UI_TV_FS] > lh;
    for (int i = n - 2; leak && i >= 0; i--) {
        if (floor_has(t, i) && !floor_has(t, i + 1) && rows[i].p != NULL
            && rows[i].line == v[MOY_UI_TV_ERR_ROW]) {
            floor_set(t, i + 1);
            t->n_floor++;
            px += lh;
            int a = ry + (i + 1) * lh - v[MOY_UI_TV_OFF], b = a + lh;
            a = a > ry ? a : ry;
            b = b < ry + h ? b : ry + h;
            fy0 = a < fy0 ? a : fy0;
            fy1 = b > fy1 ? b : fy1;
        }
    }
    v[MOY_UI_TV_DY] = dy;
    v[MOY_UI_TV_FLOOR] = px;
    v[MOY_UI_TV_FLOOR_Y0] = fy1 > fy0 ? fy0 : ry;
    v[MOY_UI_TV_FLOOR_Y1] = fy1 > fy0 ? fy1 : ry;
    v[MOY_UI_TV_WHOLE] = 0;
    t->whole = 0;
    return 1;
}

// -- drawing ------------------------------------------------------------------------------

typedef struct {
    moy_canvas_row_t *row;
    moy_text_target_t t;
    const moy_ui_sink_t *s;
    const int32_t *v;
    int fs, scale;
    // the run printer's state
    const uint8_t *p;
    int left, cols, tx0, y;
    uint32_t a0, b0;            // the row's bytes
} dr_t;

static uint16_t c565(const dr_t *d, int ci) {
    return d->row->pal[moy_ui_pidx(ci)];
}

static void target(dr_t *d) {
    const int32_t *st = d->row->st;
    d->t.px = d->row->px;
    d->t.cap = d->row->cap;
    d->t.dw = (int)st[MOY_ST_W];
    d->t.cam_x = (int)st[MOY_ST_CAM_X];
    d->t.cam_y = (int)st[MOY_ST_CAM_Y];
    d->t.cx0 = (int)st[MOY_ST_CX0];
    d->t.cy0 = (int)st[MOY_ST_CY0];
    d->t.cx1 = (int)st[MOY_ST_CX1];
    d->t.cy1 = (int)st[MOY_ST_CY1];
}

// The canvas's rect: one fill, counted as its gate counts one.
static void rect(dr_t *d, int x, int y, int w, int h, int ci) {
    d->row->st[MOY_ST_N_FILL]++;
    moy_target_fill(&d->t, x, y, w, h, c565(d, ci));
}

// The canvas's print: one string in the print font at the font scale.
static void print(dr_t *d, const uint8_t *s, size_t n, int x, int y, int ci) {
    d->row->st[MOY_ST_N_TEXT]++;
    mg_text(d->t.px, d->t.cap, d->t.dw, s, n, x, y, c565(d, ci), NULL, 0, 0, d->scale,
            d->t.cam_x, d->t.cam_y, d->t.cx0, d->t.cy0, d->t.cx1, d->t.cy1);
}

// The canvas's shadow: a hard band of `ci` at `alpha`.
static void tint(dr_t *d, int x, int y, int w, int h, int ci, int alpha) {
    mg_shadow(d->t.px, d->t.cap, d->t.dw, x, y, w, h, 0, 0, 0, 0, c565(d, ci), alpha, 0, NULL,
              d->t.cam_x, d->t.cam_y, d->t.cx0, d->t.cy0, d->t.cx1, d->t.cy1);
}

// The clip to rect (x, y, w, h) inside the clip `in` (buffer space): the
// row's state and the target both.
static void clip_to(dr_t *d, const int32_t in[4], int x, int y, int w, int h) {
    int32_t *st = d->row->st;
    int x0 = x - d->t.cam_x, y0 = y - d->t.cam_y;
    int x1 = x0 + w, y1 = y0 + h;
    st[MOY_ST_CX0] = x0 > in[0] ? x0 : in[0];
    st[MOY_ST_CY0] = y0 > in[1] ? y0 : in[1];
    st[MOY_ST_CX1] = x1 < in[2] ? x1 : in[2];
    st[MOY_ST_CY1] = y1 < in[3] ? y1 : in[3];
    target(d);
}

static void clip_back(dr_t *d, const int32_t in[4]) {
    int32_t *st = d->row->st;
    st[MOY_ST_CX0] = in[0];
    st[MOY_ST_CY0] = in[1];
    st[MOY_ST_CX1] = in[2];
    st[MOY_ST_CY1] = in[3];
    target(d);
}

// The byte where column `c` of p[a, b) starts (b where c runs past it).
static uint32_t byte_of(const uint8_t *p, uint32_t a, uint32_t b, int c) {
    uint32_t i = a;
    while (i < b && c > 0) {
        i++;
        while (i < b && (p[i] & 0xC0) == 0x80) {
            i++;
        }
        c--;
    }
    return i;
}

// A run of the row's line: the part of it on the row and in the view,
// printed at its column (code_layer._draw_code_runs).
static void print_run(void *ctx, int tone, uint32_t a, uint32_t b, int col) {
    dr_t *d = (dr_t *)ctx;
    // the row's part of the line
    if (b <= d->a0 || a >= d->b0) {
        return;
    }
    const uint8_t *p = d->p;
    int ncol = cols_in(p, a, b);
    int ca = col, cb = col + ncol;
    if (a < d->a0) {
        ca = col + cols_in(p, a, d->a0);
        a = d->a0;
    }
    if (b > d->b0) {
        cb = cb - cols_in(p, d->b0, b);
        b = d->b0;
    }
    // columns relative to the row's first
    int rc0 = cols_in(p, d->a0, a), rc1 = rc0 + (cb - ca);
    int left = d->left, right = d->left + d->cols;
    if (rc1 <= left) {
        return;
    }
    if (rc0 >= right) {
        return;
    }
    uint32_t pa = a, pb = b;
    int at = rc0;
    if (rc0 < left) {
        pa = byte_of(p, a, b, left - rc0);
        at = left;
    }
    if (rc1 > right) {
        pb = byte_of(p, a, b, right - rc0);
    }
    if (pb <= pa) {
        return;
    }
    print(d, p + pa, pb - pa, d->tx0 + (at - left) * d->v[MOY_UI_TV_CELL], d->y,
          d->v[MOY_UI_TV_TONE0 + tone]);
}

// The current line's mark over columns [x0, x1) of it (code_layer.
// _current_line): the theme's current mark laid over the whole row from the
// mark's left, clipped to them, and the `value` bar where they start there.
static void current(dr_t *d, const int32_t in[4], int y, int x0, int x1) {
    const int32_t *v = d->v;
    int left = v[MOY_UI_TV_MARK], right = v[MOY_UI_TV_X] + v[MOY_UI_TV_W];
    int lh = v[MOY_UI_TV_LH];
    x1 = x1 < 0 ? right : x1;
    if (x1 <= x0) {
        return;
    }
    int whole = x0 == left && x1 == right;
    if (!whole) {
        clip_to(d, in, x0, y, x1 - x0, lh);
    }
    int ink;
    moy_ui_box_t b = {(int16_t)left, (int16_t)y, (int16_t)(right - left), (int16_t)lh};
    moy_ui_current(d->s, b, 0, MOY_UI_MARK_SIDE | MOY_UI_MARK_KEEP, d->s->fs, &ink);
    if (!whole) {
        clip_back(d, in);
    }
    if (x0 == left) {
        rect(d, x0, y, 2 * d->fs, lh, v[MOY_UI_TV_VALUE]);
    }
}

static const uint8_t s_fade_alpha[3] = {89, 153, 217};
#define FADE 12

static void draw_row(dr_t *d, const int32_t in[4], const moy_ui_trow_t *r,
                     const moy_ui_trow_t *above, int y, moy_ui_span_t err, moy_ui_span_t find) {
    const int32_t *v = d->v;
    int fs = d->fs, cell = v[MOY_UI_TV_CELL], lh = v[MOY_UI_TV_LH], th = v[MOY_UI_TV_TEXT_H];
    int gc = v[MOY_UI_TV_GUTTER], cols = v[MOY_UI_TV_COLS], left = v[MOY_UI_TV_LEFT];
    int tx = v[MOY_UI_TV_TX], tx0 = tx + gc * cell;
    if (v[MOY_UI_TV_FLAGS] & MOY_UI_TVF_GROUND) {
        rect(d, v[MOY_UI_TV_X], y, v[MOY_UI_TV_W], lh, v[MOY_UI_TV_BG]);
        // the error's underline of the row above, where it reaches this row,
        // under this row's marks as a whole draw lays it
        if (above != NULL && above->p != NULL && above->line == v[MOY_UI_TV_ERR_ROW]
            && v[MOY_UI_TV_ERR_ROW] >= 0 && th + fs > lh) {
            int u0 = y - lh + th, u1 = u0 + fs;
            u0 = u0 > y ? u0 : y;
            if (u1 > u0) {
                rect(d, tx0, u0, cols * cell, u1 - u0, v[MOY_UI_TV_ERR]);
            }
        }
    }
    if (r->p == NULL) {
        return;
    }
    marks_t m;
    row_marks(v, r, &m);
    int fx = tx0 + cols * cell - FADE * fs;
    int mark = v[MOY_UI_TV_MARK] >= 0;
    if (m.cur && mark) {
        current(d, in, y, v[MOY_UI_TV_MARK], m.fade ? fx : -1);
    }
    if (gc > 0) {
        if (r->ca == 0) {
            char num[12];
            int k = 0;
            uint32_t no = (uint32_t)(r->line + v[MOY_UI_TV_NUM0]) + 1;
            char tmp[12];
            do {
                tmp[k++] = (char)('0' + no % 10);
                no /= 10;
            } while (no && k < 11);
            int show = k < gc - 1 ? k : gc - 1;
            for (int i = 0; i < show; i++) {
                num[i] = tmp[show - 1 - i];
            }
            if (show > 0) {
                print(d, (const uint8_t *)num, (size_t)show, tx + (gc - 1 - show) * cell, y,
                      m.err ? v[MOY_UI_TV_ERR] : m.cur ? v[MOY_UI_TV_GUT_CUR]
                                                      : v[MOY_UI_TV_GUT]);
            }
        }
    } else if (m.err && v[MOY_UI_TV_MARK] >= 0) {
        rect(d, v[MOY_UI_TV_MARK], y, 2 * fs, th, v[MOY_UI_TV_ERR]);
    }
    if (m.err) {
        rect(d, tx0, y + th, cols * cell, fs, v[MOY_UI_TV_ERR]);
    }
    if (m.sel1 > m.sel0) {
        rect(d, tx0 + m.sel0 * cell, y, (m.sel1 - m.sel0) * cell, th, v[MOY_UI_TV_SEL]);
    }
    if (find.n > 0) {
        int ci = v[MOY_UI_TV_FIND_CI] != 0;
        int bar = th / 7 > 1 ? th / 7 : 1;
        uint32_t at = 0;
        int qcols = cols_in(find.p, 0, find.n);
        for (;;) {
            int32_t i = find_at(r->p, r->n, at, find, ci);
            if (i < 0) {
                break;
            }
            int c0 = cols_in(r->p, 0, (uint32_t)i) - r->ca, c1 = c0 + qcols;
            int vs = c0 > left ? c0 : left, we = left + cols;
            int ve = c1 < we ? c1 : we;
            if (ve > vs) {
                int mx = tx0 + (vs - left) * cell, mw = (ve - vs) * cell;
                tint(d, mx, y, mw, th, v[MOY_UI_TV_VALUE], 96);
                if (m.cur && c0 + r->ca == v[MOY_UI_TV_CARET_COL]) {
                    rect(d, mx, y + th - bar, mw, bar, v[MOY_UI_TV_VALUE]);
                }
            }
            at = (uint32_t)i + (find.n > 0 ? find.n : 1);
        }
    }
    d->p = r->p;
    d->a0 = r->a;
    d->b0 = r->b;
    d->left = left;
    d->cols = cols;
    d->tx0 = tx0;
    d->y = y;
    runs_of(r->p, r->b, v[MOY_UI_TV_SYNTAX], print_run, d);
    if (m.err && err.n > 0) {
        int len = m.cb - m.ca;
        int mcol = len - left;
        mcol = (mcol < cols ? mcol : cols);
        mcol = (mcol > 0 ? mcol : 0) + 1;
        if (mcol < cols - 2) {
            uint32_t eb = byte_of(err.p, 0, err.n, cols - mcol);
            print(d, err.p, eb, tx0 + mcol * cell, y, v[MOY_UI_TV_ERR]);
        }
    }
    if (m.fade) {
        int step = FADE * fs / 3;
        for (int i = 0; i < 3; i++) {
            tint(d, fx + i * step, y, step, lh, v[MOY_UI_TV_BG], s_fade_alpha[i]);
        }
        if (m.cur && mark) {
            current(d, in, y, fx, -1);
        }
    }
    if (m.vcol >= 0) {
        rect(d, tx0 + m.vcol * cell - fs, y + fs, fs, th - 2 * fs, v[MOY_UI_TV_CARET]);
    }
}

int moy_ui_text_paint(moy_ui_text_t *t, const moy_ui_sink_t *s, int32_t *v,
                      const moy_ui_trow_t *rows, int n, moy_ui_span_t err, moy_ui_span_t find,
                      int whole) {
    if (t != NULL && t->magic != MOY_UI_TEXT_MAGIC) {
        moy_ui_text_reset(t);
    }
    dr_t d;
    memset(&d, 0, sizeof(d));
    d.row = (moy_canvas_row_t *)s->dst;
    d.s = s;
    d.v = v;
    d.fs = v[MOY_UI_TV_FS] > 0 ? v[MOY_UI_TV_FS] : 1;
    d.scale = d.row->st[MOY_ST_FONT_SCALE] > 1 ? (int)d.row->st[MOY_ST_FONT_SCALE] : 1;
    target(&d);
    int32_t in[4] = {d.row->st[MOY_ST_CX0], d.row->st[MOY_ST_CY0], d.row->st[MOY_ST_CX1],
                     d.row->st[MOY_ST_CY1]};
    int clip = (v[MOY_UI_TV_FLAGS] & MOY_UI_TVF_CLIP) != 0;
    int32_t rin[4] = {in[0], in[1], in[2], in[3]};
    if (clip) {
        clip_to(&d, in, v[MOY_UI_TV_X], v[MOY_UI_TV_Y], v[MOY_UI_TV_W], v[MOY_UI_TV_H]);
        rin[0] = d.row->st[MOY_ST_CX0];
        rin[1] = d.row->st[MOY_ST_CY0];
        rin[2] = d.row->st[MOY_ST_CX1];
        rin[3] = d.row->st[MOY_ST_CY1];
    }
    // the plan this draw follows is this frame's: a draw with none planned
    // (or for other rows) draws whole
    int planned = t != NULL && !whole && !t->whole && t->want == view_key(v)
                  && t->want_top == v[MOY_UI_TV_TOP] && t->want_off == v[MOY_UI_TV_OFF]
                  && t->want_rows == n;
    int lh = v[MOY_UI_TV_LH], drawn = 0;
    int cy = d.t.cam_y;
    for (int i = 0; i < n && rin[1] < rin[3]; i++) {
        if (planned && (i >= MOY_UI_TEXT_ROWS || !floor_has(t, i))) {
            continue;
        }
        int y = v[MOY_UI_TV_Y] + i * lh - v[MOY_UI_TV_OFF];
        // a row the clip leaves out draws nothing (an error's underline
        // reaches the row after: the band is a row and its leak)
        if (y - cy >= rin[3] || y - cy + lh + d.fs <= rin[1]) {
            continue;
        }
        draw_row(&d, rin, &rows[i], i > 0 ? &rows[i - 1] : NULL, y, err, find);
        drawn++;
    }
    if (clip) {
        clip_back(&d, in);
    }
    v[MOY_UI_TV_DRAWN] = drawn;
    return drawn;
}

void moy_ui_text_note(moy_ui_text_t *t, int32_t *v, const moy_ui_trow_t *rows, int n,
                      moy_ui_span_t err, moy_ui_span_t find) {
    if (t->magic != MOY_UI_TEXT_MAGIC) {
        moy_ui_text_reset(t);
    }
    int lh = v[MOY_UI_TV_LH];
    uint32_t key = view_key(v);
    if (n <= MOY_UI_TEXT_ROWS && lh > 0) {
        int fresh = t->want == key && t->want_top == v[MOY_UI_TV_TOP]
                    && t->want_off == v[MOY_UI_TV_OFF] && t->want_rows == n;
        if (!fresh) {
            for (int i = 0; i < n; i++) {
                row_gen(v, &rows[i], i > 0 ? &rows[i - 1] : NULL, err, find, &t->ngen[i],
                        &t->ngen2[i]);
            }
        }
        memcpy(t->gen, t->ngen, (size_t)n * sizeof(uint32_t));
        memcpy(t->gen2, t->ngen2, (size_t)n * sizeof(uint32_t));
        t->rows = (int16_t)n;
        t->key = key;
        t->top = v[MOY_UI_TV_TOP];
        t->off = v[MOY_UI_TV_OFF];
    } else {
        t->key = 0;
        t->rows = 0;
    }
    t->whole = 1;
    t->want = 0;
}

int moy_ui_text_draw(moy_ui_text_t *t, const moy_ui_sink_t *s, int32_t *v,
                     const moy_ui_trow_t *rows, int n, moy_ui_span_t err, moy_ui_span_t find,
                     int whole) {
    int drawn = moy_ui_text_paint(t, s, v, rows, n, err, find, whole);
    moy_ui_text_note(t, v, rows, n, err, find);
    return drawn;
}
