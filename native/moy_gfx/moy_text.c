// The one UI text path (moy_text.h).

#include <string.h>

#include "moy_text.h"
#include "moy_gfx_kernels.h"
#include "moy.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define FONT_ALLOC(n) heap_caps_malloc((n), MALLOC_CAP_SPIRAM)
#define FONT_FREE(p) heap_caps_free(p)
#else
#include <stdlib.h>
#define FONT_ALLOC(n) malloc(n)
#define FONT_FREE(p) free(p)
#endif

#define FONT_GLYPHS 96
#define FONT_FIRST 32

// --- the font rows -------------------------------------------------------------

// Petme128's row: no advance table, every glyph one 8px cell, drawn by
// mg_text_raw's cell loop (moy_gfx_kernels.c says why it keeps its own).
static const mg_font_t PETME128 = {moy_font_data, NULL, MOY_TEXT_CELL, MOY_TEXT_CELL, 1,
                                   FONT_FIRST, FONT_GLYPHS};

// A loaded font: the file, owned, and the row read out of it. An id is the
// slot's number (1 up) in its low byte and the slot's generation above, which
// moves on every free, so an id outlives its font only as a refusal.
typedef struct {
    mg_font_t f;
    uint8_t *buf;
    uint8_t line, ascent;
    uint32_t gen;
} font_slot_t;

static font_slot_t s_fonts[MOY_FONT_SLOTS];

static font_slot_t *slot_of(uint32_t font) {
    uint32_t k = font & 0xffu;
    if (k < 1 || k > MOY_FONT_SLOTS) {
        return NULL;
    }
    font_slot_t *sl = &s_fonts[k - 1];
    return sl->buf != NULL && sl->gen == font >> 8 ? sl : NULL;
}

static const mg_font_t *font_row(uint32_t font) {
    if (font == MOY_FONT_PETME128) {
        return &PETME128;
    }
    font_slot_t *sl = slot_of(font);
    return sl != NULL ? &sl->f : NULL;
}

// Check a font file (moy_text.h; docs/theming_2026-09.md §4.7 has the format).
// Every field is read a byte at a time, so `src` may sit at any address.
static int font_check(const uint8_t *src, size_t n) {
    if (n > MOY_FONT_BYTES) {
        return MOY_FONT_BIG;
    }
    if (n < MOY_FONT_HEADER) {
        return MOY_FONT_SHORT;
    }
    if (memcmp(src, MOY_FONT_MAGIC, 4) != 0) {
        return MOY_FONT_MAGIC_BAD;
    }
    if (src[4] > MOY_FONT_VERSION) {
        return MOY_FONT_NEWER;
    }
    uint32_t size = (uint32_t)src[12] | (uint32_t)src[13] << 8 | (uint32_t)src[14] << 16
                    | (uint32_t)src[15] << 24;
    if (size != n) {
        return MOY_FONT_SHORT;
    }
    int w = src[5], h = src[6], line = src[7], ascent = src[8];
    int first = src[9], count = src[10];
    if (src[4] != MOY_FONT_VERSION || w < 1 || w > MOY_FONT_CELL_MAX || h < 1
        || h > MOY_FONT_CELL_MAX || line < h || line > MOY_FONT_LINE_MAX || ascent > h
        || count < 1 || first + count > 256 || src[11] != 0) {
        return MOY_FONT_BAD;
    }
    size_t bpc = ((size_t)h + 7) / 8;
    size_t advs = ((size_t)count + 3) & ~(size_t)3;
    if (MOY_FONT_HEADER + advs + (size_t)count * (size_t)w * bpc != n) {
        return MOY_FONT_BAD;
    }
    const uint8_t *adv = src + MOY_FONT_HEADER;
    for (size_t i = 0; i < advs; i++) {
        if (i < (size_t)count ? adv[i] > MOY_FONT_LINE_MAX : adv[i] != 0) {
            return MOY_FONT_BAD;
        }
    }
    // A column's last byte holds the rows past 8 * (bpc - 1); a bit below h is refused.
    uint8_t below = (uint8_t)(0xffu << (h - 8 * (bpc - 1)) & 0xffu);
    const uint8_t *g = adv + advs;
    for (size_t c = 0; c < (size_t)count * (size_t)w; c++) {
        if (g[c * bpc + bpc - 1] & below) {
            return MOY_FONT_BAD;
        }
    }
    return MOY_FONT_OK;
}

int moy_font_load(const uint8_t *src, size_t n, uint32_t *id) {
    int rc = font_check(src, n);
    if (rc != MOY_FONT_OK) {
        return rc;
    }
    font_slot_t *sl = NULL;
    int k = 0;
    while (k < MOY_FONT_SLOTS && s_fonts[k].buf != NULL) {
        k++;
    }
    if (k == MOY_FONT_SLOTS) {
        return MOY_FONT_FULL;
    }
    sl = &s_fonts[k];
    uint8_t *buf = FONT_ALLOC(n);
    if (buf == NULL) {
        return MOY_FONT_NOMEM;
    }
    memcpy(buf, src, n);
    int count = buf[10];
    size_t advs = ((size_t)count + 3) & ~(size_t)3;
    sl->buf = buf;
    sl->f.adv = buf + MOY_FONT_HEADER;
    sl->f.glyphs = buf + MOY_FONT_HEADER + advs;
    sl->f.w = buf[5];
    sl->f.h = buf[6];
    sl->f.bpc = (uint8_t)((buf[6] + 7) / 8);
    sl->f.first = buf[9];
    sl->f.count = (uint8_t)count;
    sl->line = buf[7];
    sl->ascent = buf[8];
    sl->gen = (sl->gen + 1) & 0xffffffu;
    if (sl->gen == 0) {
        sl->gen = 1;
    }
    *id = (sl->gen << 8) | (uint32_t)(k + 1);
    return MOY_FONT_OK;
}

int moy_font_free(uint32_t font) {
    font_slot_t *sl = slot_of(font);
    if (sl == NULL) {
        return 0;
    }
    FONT_FREE(sl->buf);
    sl->buf = NULL;
    memset(&sl->f, 0, sizeof(sl->f));
    return 1;
}

int moy_font_info(uint32_t font, moy_font_info_t *out) {
    const mg_font_t *f = font_row(font);
    if (f == NULL) {
        return 0;
    }
    font_slot_t *sl = font == MOY_FONT_PETME128 ? NULL : slot_of(font);
    out->w = f->w;
    out->h = f->h;
    out->line = sl != NULL ? sl->line : MOY_TEXT_CELL;
    out->ascent = sl != NULL ? sl->ascent : MOY_TEXT_ASCENT;
    out->first = f->first;
    out->count = f->count;
    return 1;
}

// One byte's advance in font `f`, unscaled.
static int advance(const mg_font_t *f, uint8_t b) {
    int gi = (int)b - f->first;
    if (gi < 0 || gi >= f->count) {
        gi = 0;
    }
    return f->adv[gi];
}

// --- measuring -----------------------------------------------------------------

// A byte that continues a UTF-8 sequence (10xxxxxx).
static int cont(uint8_t b) {
    return (b & 0xC0) == 0x80;
}

size_t moy_text_fit(const uint8_t *s, size_t n, size_t cells) {
    if (n <= cells) {
        return n;
    }
    size_t k = cells;
    while (k > 0 && cont(s[k])) {
        k--;        // s[k] continues the codepoint that s[k - 1] is part of
    }
    return k;
}

int moy_text_width(uint32_t font, const uint8_t *s, size_t n, int scale) {
    const mg_font_t *f = font_row(font);
    if (f == NULL) {
        return 0;
    }
    if (scale < 1) {
        scale = 1;
    }
    if (f->adv == NULL) {
        return (int)n * f->w * scale;
    }
    int w = 0;
    for (size_t i = 0; i < n; i++) {
        w += advance(f, s[i]);
    }
    return w * scale;
}

void moy_target_fill(const moy_text_target_t *t, int x, int y, int w, int h, uint16_t col) {
    int stride = t->dw;
    if (stride <= 0 || t->px == NULL) {
        return;
    }
    x -= t->cam_x;
    y -= t->cam_y;
    int x0 = t->cx0 > x ? t->cx0 : x;
    int y0 = t->cy0 > y ? t->cy0 : y;
    int x1 = x + w < t->cx1 ? x + w : t->cx1;
    int y1 = y + h < t->cy1 ? y + h : t->cy1;
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (x1 > stride) {
        x1 = stride;
    }
    int rows = (int)(t->cap / (size_t)stride);
    if (y1 > rows) {
        y1 = rows;
    }
    if (x1 <= x0 || y1 <= y0) {
        return;
    }
    for (int row = y0; row < y1; row++) {
        mg_fill_run(t->px + (size_t)row * (size_t)stride + (size_t)x0, (size_t)(x1 - x0), col);
    }
}

int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw) {
    const mg_font_t *f = font_row(font);
    if (source > MOY_TEXT_DATA || f == NULL) {
        return -1;          // a literal never reaches the raster
    }
    if (scale < 1) {
        scale = 1;
    }
    int w;
    if (f->adv == NULL) {
        int adv = f->w * scale;
        if (maxw >= 0) {
            n = moy_text_fit(s, n, (size_t)(maxw / adv));
        }
        w = (int)n * adv;
    } else {
        if (maxw >= 0) {
            size_t k = 0;
            for (int at = 0; k < n; k++) {
                at += advance(f, s[k]) * scale;
                if (at > maxw) {
                    break;
                }
            }
            n = moy_text_fit(s, n, k);
        }
        w = moy_text_width(font, s, n, scale);
    }
    if (maxw >= 0) {
        if (align == MOY_TEXT_CENTER) {
            int d = maxw - w;
            x += d >= 0 ? d / 2 : -((1 - d) / 2);    // floor, as `//` is
        } else if (align == MOY_TEXT_RIGHT) {
            x += maxw - w;
        }
    }
    if (n > 0 && t != NULL && t->px != NULL) {
        if (f->adv == NULL) {
            mg_text_raw(t->px, t->cap, t->dw, s, n, x, y, col, moy_font_data, FONT_GLYPHS,
                        FONT_FIRST, scale, t->cam_x, t->cam_y, t->cx0, t->cy0, t->cx1, t->cy1);
        } else {
            mg_text_font(t->px, t->cap, t->dw, s, n, x, y, col, f, scale, t->cam_x, t->cam_y,
                         t->cx0, t->cy0, t->cx1, t->cy1);
        }
    }
    return w;
}

// --- wrap ----------------------------------------------------------------------

// The whitespace str.split() splits on, in ASCII.
static int space(uint8_t b) {
    return b == ' ' || (b >= '\t' && b <= '\r') || (b >= 0x1c && b <= 0x1f);
}

typedef struct {
    moy_text_line_t *lines;
    int cap, count;
} sink_t;

static void emit(sink_t *k, size_t off, size_t len, int collapse) {
    if (k->count < k->cap) {
        moy_text_line_t *l = &k->lines[k->count];
        l->off = (uint16_t)off;
        l->len = (uint16_t)len;
        l->collapse = (uint8_t)collapse;
    }
    k->count++;
}

static void wrap_words(sink_t *k, const uint8_t *s, size_t n, size_t cols) {
    size_t i = 0;
    int have = 0;               // a line is open
    size_t ls = 0, le = 0;      // its span
    size_t lw = 0;              // its width, words joined by single spaces
    int collapse = 0;
    for (;;) {
        while (i < n && space(s[i])) {
            i++;
        }
        if (i >= n) {
            break;
        }
        size_t ws = i;
        while (i < n && !space(s[i])) {
            i++;
        }
        size_t wn = i - ws;
        if (have && lw + 1 + wn <= cols) {
            if (ws - le != 1 || s[le] != ' ') {
                collapse = 1;
            }
            le = i;
            lw += 1 + wn;
            continue;
        }
        if (!have && wn <= cols) {
            have = 1;
            ls = ws;
            le = i;
            lw = wn;
            collapse = 0;
            continue;
        }
        if (have) {
            emit(k, ls, le - ls, collapse);
            have = 0;
        }
        if (wn <= cols) {
            have = 1;
            ls = ws;
            le = i;
            lw = wn;
            collapse = 0;
        } else {
            // A word longer than a line: a line of its own, cut at a codepoint.
            size_t f = moy_text_fit(s + ws, wn, cols);
            if (f > 0) {
                emit(k, ws, f, 0);
            }
        }
    }
    if (have) {
        emit(k, ls, le - ls, collapse);
    }
}

// The bytes of the codepoint at s[0] (at least 1, at most what is left).
static size_t cp_len(const uint8_t *s, size_t n) {
    size_t k = 1;
    while (k < n && cont(s[k])) {
        k++;
    }
    return k;
}

static void wrap_panel(sink_t *k, const uint8_t *s, size_t n, size_t cols) {
    size_t t = 0;
    for (;;) {
        size_t pe = t;
        while (pe < n && s[pe] != '\n') {
            pe++;
        }
        size_t ls = t, ln = 0;
        size_t w = t;
        for (;;) {
            size_t we = w;
            while (we < pe && s[we] != ' ') {
                we++;
            }
            size_t wn = we - w;
            while (wn > cols) {
                if (ln) {
                    emit(k, ls, ln, 0);
                    ln = 0;
                }
                size_t c = moy_text_fit(s + w, wn, cols);
                if (c == 0) {
                    c = cp_len(s + w, wn);   // a codepoint wider than a line
                }
                emit(k, w, c, 0);
                w += c;
                wn -= c;
            }
            if (ln == 0) {
                ls = w;
                ln = wn;
            } else if (ln + 1 + wn <= cols) {
                ln += 1 + wn;
            } else {
                emit(k, ls, ln, 0);
                ls = w;
                ln = wn;
            }
            if (we >= pe) {
                break;
            }
            w = we + 1;
        }
        emit(k, ls, ln, 0);
        if (pe >= n) {
            return;
        }
        t = pe + 1;
    }
}

int moy_text_wrap(uint32_t font, const uint8_t *s, size_t n, int cols, int mode,
                  moy_text_line_t *lines, int cap) {
    sink_t k = {lines, cap, 0};
    if (font_row(font) == NULL || cols < 1 || n > 0xFFFF) {
        return 0;
    }
    if (mode == MOY_WRAP_PANEL) {
        wrap_panel(&k, s, n, (size_t)cols);
    } else {
        wrap_words(&k, s, n, (size_t)cols);
    }
    return k.count;
}

size_t moy_text_collapse(const uint8_t *s, const moy_text_line_t *line, uint8_t *out) {
    const uint8_t *p = s + line->off;
    size_t n = line->len, o = 0;
    int gap = 0;
    for (size_t i = 0; i < n; i++) {
        if (space(p[i])) {
            gap = 1;
            continue;
        }
        if (gap && o) {
            out[o++] = ' ';
        }
        gap = 0;
        out[o++] = p[i];
    }
    return o;
}
