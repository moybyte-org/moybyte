// The one UI text path (moy_text.h).

#include "moy_text.h"
#include "moy_gfx_kernels.h"
#include "moy.h"

#define FONT_GLYPHS 96
#define FONT_FIRST 32

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
    (void)s;
    if (font != MOY_FONT_PETME128) {
        return 0;
    }
    if (scale < 1) {
        scale = 1;
    }
    return (int)n * MOY_TEXT_CELL * scale;
}

int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw) {
    if (source > MOY_TEXT_DATA || font != MOY_FONT_PETME128) {
        return -1;          // a literal never reaches the raster
    }
    if (scale < 1) {
        scale = 1;
    }
    int adv = MOY_TEXT_CELL * scale;
    if (maxw >= 0) {
        n = moy_text_fit(s, n, (size_t)(maxw / adv));
        int w = (int)n * adv;
        if (align == MOY_TEXT_CENTER) {
            int d = maxw - w;
            x += d >= 0 ? d / 2 : -((1 - d) / 2);    // floor, as `//` is
        } else if (align == MOY_TEXT_RIGHT) {
            x += maxw - w;
        }
    }
    if (n > 0 && t != NULL && t->px != NULL) {
        mg_text_raw(t->px, t->cap, t->dw, s, n, x, y, col, moy_font_data, FONT_GLYPHS,
                    FONT_FIRST, scale, t->cam_x, t->cam_y, t->cx0, t->cy0, t->cx1, t->cy1);
    }
    return (int)n * adv;
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
    if (font != MOY_FONT_PETME128 || cols < 1 || n > 0xFFFF) {
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
