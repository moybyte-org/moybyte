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

// A version 2 font's tables, pointing into its file.
typedef struct {
    const uint8_t *index;       // MOY_FONT_INDEX bytes: a byte's record
    const uint8_t *rec;         // count records of MOY_FONT_RECORD bytes
    const uint8_t *kern;        // nkern pairs of MOY_FONT_PAIR bytes, ascending
    const uint8_t *cov;         // the glyphs' coverage
    int nkern, bpp, cell, h;
} font2_t;

// A row: petme128's (version 0: no advance table, every glyph one 8px cell,
// drawn by mg_text_raw's cell loop, which moy_gfx_kernels.c says why it keeps),
// a version 1 font's (mg_font_t, drawn by mg_text_font) or a version 2
// font's (font2_t, drawn a glyph at a time by mg_glyph_cov).
typedef struct {
    mg_font_t f;
    font2_t f2;
    uint8_t version, line, ascent;
} row_t;

static const row_t PETME128 = {{moy_font_data, NULL, MOY_TEXT_CELL, MOY_TEXT_CELL, 1,
                                FONT_FIRST, FONT_GLYPHS},
                               {0}, 0, MOY_TEXT_CELL, MOY_TEXT_ASCENT};

// A loaded font: the file, owned, and the row read out of it. An id is the
// slot's number (1 up) in its low byte and the slot's generation above, which
// moves on every free, so an id outlives its font only as a refusal.
typedef struct {
    row_t r;
    uint8_t *buf;
    uint32_t gen;
} font_slot_t;

static font_slot_t s_fonts[MOY_FONT_SLOTS];
static uint32_t s_print = MOY_FONT_PETME128;

static font_slot_t *slot_of(uint32_t font) {
    uint32_t k = font & 0xffu;
    if (k < 1 || k > MOY_FONT_SLOTS) {
        return NULL;
    }
    font_slot_t *sl = &s_fonts[k - 1];
    return sl->buf != NULL && sl->gen == font >> 8 ? sl : NULL;
}

static const row_t *font_row(uint32_t font) {
    if (font == MOY_FONT_PETME128) {
        return &PETME128;
    }
    font_slot_t *sl = slot_of(font);
    return sl != NULL ? &sl->r : NULL;
}

static uint32_t u16_at(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8;
}

static uint32_t u24_at(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16;
}

// Check a version 1 file whose magic, version and size are checked.
static int font_check1(const uint8_t *src, size_t n) {
    int w = src[5], h = src[6], line = src[7], ascent = src[8];
    int first = src[9], count = src[10];
    if (w < 1 || w > MOY_FONT_CELL_MAX || h < 1 || h > MOY_FONT_CELL_MAX || line < h
        || line > MOY_FONT_LINE_MAX || ascent > h || count < 1 || first + count > 256
        || src[11] != 0) {
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

// Check a version 2 file whose magic, version and size are checked: every
// table inside the file and filling it exactly, each record's coverage laid
// after the one before, a glyph's pad bits clear, the pairs ascending.
static int font_check2(const uint8_t *src, size_t n) {
    int bpp = src[5], line = src[6], ascent = src[7], cell = src[8], count = src[9];
    size_t nkern = u16_at(src + 10);
    if ((bpp != 1 && bpp != 2 && bpp != 4) || line < 1 || ascent > line || cell < 1
        || count < 1) {
        return MOY_FONT_BAD;
    }
    size_t recs = MOY_FONT_HEADER + MOY_FONT_INDEX;
    size_t pairs = recs + (size_t)count * MOY_FONT_RECORD;
    size_t cov = pairs + nkern * MOY_FONT_PAIR;
    if (cov > n) {
        return MOY_FONT_BAD;
    }
    for (int b = 0; b < MOY_FONT_INDEX; b++) {
        if (src[MOY_FONT_HEADER + b] >= count) {
            return MOY_FONT_BAD;
        }
    }
    size_t at = 0;
    for (int i = 0; i < count; i++) {
        const uint8_t *r = src + recs + (size_t)i * MOY_FONT_RECORD;
        int w = r[3], h = r[4];
        if ((w == 0) != (h == 0) || u24_at(r) != at) {
            return MOY_FONT_BAD;
        }
        size_t bits = (size_t)w * (size_t)h * (size_t)bpp;
        size_t bytes = (bits + 7) / 8;
        if (bytes > n - cov - at) {
            return MOY_FONT_BAD;
        }
        if (bits % 8 && src[cov + at + bytes - 1] & (0xffu >> (bits % 8))) {
            return MOY_FONT_BAD;
        }
        at += bytes;
    }
    if (cov + at != n) {
        return MOY_FONT_BAD;
    }
    uint32_t prev = 0;
    for (size_t k = 0; k < nkern; k++) {
        const uint8_t *p = src + pairs + k * MOY_FONT_PAIR;
        uint32_t key = (uint32_t)p[0] << 8 | p[1];
        if ((k > 0 && key <= prev) || p[2] == 0) {
            return MOY_FONT_BAD;
        }
        prev = key;
    }
    return MOY_FONT_OK;
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
    if (src[4] == 1) {
        return font_check1(src, n);
    }
    if (src[4] == 2) {
        return font_check2(src, n);
    }
    return MOY_FONT_BAD;
}

// A checked file's row, pointing into `buf`.
static void read_row(row_t *r, const uint8_t *buf) {
    memset(r, 0, sizeof(*r));
    r->version = buf[4];
    if (r->version == 1) {
        int count = buf[10];
        size_t advs = ((size_t)count + 3) & ~(size_t)3;
        r->f.adv = buf + MOY_FONT_HEADER;
        r->f.glyphs = buf + MOY_FONT_HEADER + advs;
        r->f.w = buf[5];
        r->f.h = buf[6];
        r->f.bpc = (uint8_t)((buf[6] + 7) / 8);
        r->f.first = buf[9];
        r->f.count = (uint8_t)count;
        r->line = buf[7];
        r->ascent = buf[8];
        return;
    }
    font2_t *f = &r->f2;
    int count = buf[9];
    f->index = buf + MOY_FONT_HEADER;
    f->rec = f->index + MOY_FONT_INDEX;
    f->kern = f->rec + (size_t)count * MOY_FONT_RECORD;
    f->nkern = (int)u16_at(buf + 10);
    f->cov = f->kern + (size_t)f->nkern * MOY_FONT_PAIR;
    f->bpp = buf[5];
    f->cell = buf[8];
    r->line = buf[6];
    r->ascent = buf[7];
    r->f.count = (uint8_t)count;
    int deep = 0;               // the most rows any glyph's ink reaches below the baseline
    for (int i = 0; i < count; i++) {
        const uint8_t *g = f->rec + (size_t)i * MOY_FONT_RECORD;
        int below = (int)g[4] - (int8_t)g[6];
        if (g[4] && below > deep) {
            deep = below;
        }
    }
    f->h = r->ascent + deep;
}

int moy_font_load(const uint8_t *src, size_t n, uint32_t *id) {
    int rc = font_check(src, n);
    if (rc != MOY_FONT_OK) {
        return rc;
    }
    int k = 0;
    while (k < MOY_FONT_SLOTS && s_fonts[k].buf != NULL) {
        k++;
    }
    if (k == MOY_FONT_SLOTS) {
        return MOY_FONT_FULL;
    }
    font_slot_t *sl = &s_fonts[k];
    uint8_t *buf = FONT_ALLOC(n);
    if (buf == NULL) {
        return MOY_FONT_NOMEM;
    }
    memcpy(buf, src, n);
    sl->buf = buf;
    read_row(&sl->r, buf);
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
    memset(&sl->r, 0, sizeof(sl->r));
    if (s_print == font) {
        s_print = MOY_FONT_PETME128;
    }
    return 1;
}

void moy_font_reset(void) {
    for (int k = 0; k < MOY_FONT_SLOTS; k++) {
        if (s_fonts[k].buf != NULL) {
            moy_font_free((s_fonts[k].gen << 8) | (uint32_t)(k + 1));
        }
    }
}

void moy_text_vm_stop(void) {
    moy_font_reset();
}

uint32_t moy_text_print_font(void) {
    return s_print;
}

int64_t moy_text_print_font_use(uint32_t font) {
    if (font_row(font) == NULL) {
        return -1;
    }
    uint32_t was = s_print;
    s_print = font;
    return was;
}

int moy_font_info(uint32_t font, moy_font_info_t *out) {
    const row_t *r = font_row(font);
    if (r == NULL) {
        return 0;
    }
    out->line = r->line;
    out->ascent = r->ascent;
    out->version = r->version;
    if (r->version == 2) {
        out->w = r->f2.cell;
        out->h = r->f2.h;
        out->first = 0;
        out->count = r->f.count;
        out->bpp = r->f2.bpp;
        return 1;
    }
    out->w = r->f.w;
    out->h = r->f.h;
    out->first = r->f.first;
    out->count = r->f.count;
    out->bpp = 1;
    return 1;
}

// One byte's advance in a version 1 font, unscaled.
static int advance(const mg_font_t *f, uint8_t b) {
    int gi = (int)b - f->first;
    if (gi < 0 || gi >= f->count) {
        gi = 0;
    }
    return f->adv[gi];
}

// --- a version 2 font's glyphs ---------------------------------------------------

static const uint16_t BYTE_MAP[] = MOY_FONT_BYTE_MAP;

// A byte that continues a UTF-8 sequence (10xxxxxx).
static int cont(uint8_t b) {
    return (b & 0xC0) == 0x80;
}

// The byte the codepoint at s[*i] draws at (moy_text.h), *i moved past it: a
// malformed sequence is its first byte alone, at byte 0.
static int next_byte(const uint8_t *s, size_t n, size_t *i) {
    uint8_t b = s[(*i)++];
    if (b < 0x80) {
        return b;
    }
    int len = b >= 0xC2 && b <= 0xDF ? 2 : b >= 0xE0 && b <= 0xEF ? 3
              : b >= 0xF0 && b <= 0xF4 ? 4 : 0;
    if (len == 0 || *i + (size_t)(len - 1) > n) {
        return 0;
    }
    uint32_t cp = b & (0x7Fu >> len);
    for (int k = 1; k < len; k++) {
        if (!cont(s[*i + (size_t)k - 1])) {
            return 0;
        }
        cp = cp << 6 | (s[*i + (size_t)k - 1] & 0x3Fu);
    }
    if ((len == 3 && cp < 0x800) || (len == 4 && (cp < 0x10000 || cp > 0x10FFFF))) {
        return 0;       // an overlong form, or past Unicode
    }
    *i += (size_t)(len - 1);
    if (cp >= 0xA0 && cp <= 0xFF) {
        return (int)cp;
    }
    for (size_t k = 0; k < sizeof(BYTE_MAP) / sizeof(BYTE_MAP[0]); k++) {
        if (BYTE_MAP[k] == cp) {
            return 0x80 + (int)k;
        }
    }
    return 0;
}

static const uint8_t *rec_of(const font2_t *f, int b) {
    return f->rec + (size_t)f->index[b] * MOY_FONT_RECORD;
}

// The kerning between bytes a and b, unscaled: a binary search of the pairs.
static int kern(const font2_t *f, int a, int b) {
    uint32_t key = (uint32_t)a << 8 | (uint32_t)b;
    int lo = 0, hi = f->nkern;
    while (lo < hi) {
        int mid = (lo + hi) >> 1;
        const uint8_t *p = f->kern + (size_t)mid * MOY_FONT_PAIR;
        uint32_t k = (uint32_t)p[0] << 8 | p[1];
        if (k == key) {
            return (int8_t)p[2];
        }
        if (k < key) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return 0;
}

// A walk over a version 2 font's glyphs: each step's byte and the pen's
// position before it, kerning applied, unscaled.
typedef struct {
    const font2_t *f;
    const uint8_t *s;
    size_t n, i;
    int prev, pen;
} walk_t;

static void walk_start(walk_t *w, const font2_t *f, const uint8_t *s, size_t n) {
    w->f = f;
    w->s = s;
    w->n = n;
    w->i = 0;
    w->prev = -1;
    w->pen = 0;
}

// The next glyph's byte, *at the pen's x for it; -1 at the end. The pen then
// stands past its advance.
static int walk_next(walk_t *w, int *at) {
    if (w->i >= w->n) {
        return -1;
    }
    int b = next_byte(w->s, w->n, &w->i);
    if (w->prev >= 0 && w->f->nkern) {
        w->pen += kern(w->f, w->prev, b);
    }
    *at = w->pen;
    w->pen += rec_of(w->f, b)[7];
    w->prev = b;
    return b;
}

static int width2(const font2_t *f, const uint8_t *s, size_t n) {
    walk_t w;
    int at;
    walk_start(&w, f, s, n);
    while (walk_next(&w, &at) >= 0) {
    }
    return w.pen;
}

// The bytes of the longest run of whole glyphs that draws in maxw pixels,
// unscaled; its width into *wout.
static size_t fit2(const font2_t *f, const uint8_t *s, size_t n, int maxw, int *wout) {
    walk_t w;
    int at;
    size_t keep = 0;
    int kept = 0;
    walk_start(&w, f, s, n);
    while (walk_next(&w, &at) >= 0) {
        if (w.pen > maxw) {
            break;
        }
        keep = w.i;
        kept = w.pen;
    }
    if (wout != NULL) {
        *wout = kept;
    }
    return keep;
}

// Draw one glyph of byte b with its pen at (x, y + ascent), unscaled offsets
// at `scale`, through the target.
static void glyph2(const moy_text_target_t *t, const row_t *r, int b, int x, int y,
                   uint16_t col, int scale) {
    const font2_t *f = &r->f2;
    const uint8_t *g = rec_of(f, b);
    if (g[3] == 0) {
        return;
    }
    int gx = x + (int8_t)g[5] * scale - t->cam_x;
    int gy = y + ((int)r->ascent - (int8_t)g[6]) * scale - t->cam_y;
    mg_glyph_cov(t->px, t->cap, t->dw, f->cov + u24_at(g), g[3], g[4], f->bpp, gx, gy, col,
                 scale, t->cx0, t->cy0, t->cx1, t->cy1);
}

// --- measuring -----------------------------------------------------------------

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
    const row_t *r = font_row(font);
    if (r == NULL) {
        return 0;
    }
    if (scale < 1) {
        scale = 1;
    }
    if (r->version == 2) {
        return width2(&r->f2, s, n) * scale;
    }
    const mg_font_t *f = &r->f;
    if (f->adv == NULL) {
        return (int)n * f->w * scale;
    }
    int w = 0;
    for (size_t i = 0; i < n; i++) {
        w += advance(f, s[i]);
    }
    return w * scale;
}

size_t moy_text_fit_px(uint32_t font, const uint8_t *s, size_t n, int maxw, int scale) {
    const row_t *r = font_row(font);
    if (r == NULL || maxw < 0) {
        return 0;
    }
    if (scale < 1) {
        scale = 1;
    }
    if (r->version == 2) {
        return fit2(&r->f2, s, n, maxw / scale, NULL);
    }
    const mg_font_t *f = &r->f;
    if (f->adv == NULL) {
        return moy_text_fit(s, n, (size_t)(maxw / (f->w * scale)));
    }
    size_t k = 0;
    for (int at = 0; k < n; k++) {
        at += advance(f, s[k]) * scale;
        if (at > maxw) {
            break;
        }
    }
    return moy_text_fit(s, n, k);
}

// The ellipsis's advance in a version 2 font, unscaled, with the pair before
// it from byte `last`; 0 where the font does not cover it.
static int ellipsis_w(const font2_t *f, int last) {
    if (f->index[MOY_FONT_ELLIPSIS] == 0) {
        return 0;
    }
    int k = last >= 0 && f->nkern ? kern(f, last, MOY_FONT_ELLIPSIS) : 0;
    return k + rec_of(f, MOY_FONT_ELLIPSIS)[7];
}

// The last glyph byte of s[0..n) in a version 2 font, -1 for none.
static int last_byte(const uint8_t *s, size_t n) {
    size_t i = 0;
    int b = -1;
    while (i < n) {
        b = next_byte(s, n, &i);
    }
    return b;
}

size_t moy_text_cut_px(uint32_t font, const uint8_t *s, size_t n, int maxw, int scale,
                       int *ellipsis) {
    *ellipsis = 0;
    const row_t *r = font_row(font);
    if (r == NULL || maxw < 0) {
        return 0;
    }
    if (scale < 1) {
        scale = 1;
    }
    size_t all = moy_text_fit_px(font, s, n, maxw, scale);
    if (all == n || r->version != 2 || r->f2.index[MOY_FONT_ELLIPSIS] == 0) {
        return all;
    }
    const font2_t *f = &r->f2;
    int room = maxw / scale;
    size_t k = fit2(f, s, n, room, NULL);
    for (;;) {
        while (k > 0 && s[k - 1] == ' ') {
            k--;
        }
        if (k == 0 || width2(f, s, k) + ellipsis_w(f, last_byte(s, k)) <= room) {
            break;
        }
        k--;                    // one glyph back: to the codepoint before s[k]
        while (k > 0 && cont(s[k])) {
            k--;
        }
    }
    if (ellipsis_w(f, last_byte(s, k)) + (k ? width2(f, s, k) : 0) > room) {
        return all;             // not even the ellipsis fits
    }
    *ellipsis = 1;
    return k;
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

// A version 2 font's text at (x, y), with the ellipsis after it when `ell`.
static void draw2(const moy_text_target_t *t, const row_t *r, const uint8_t *s, size_t n,
                  int x, int y, uint16_t col, int scale, int ell) {
    walk_t w;
    int at, b;
    walk_start(&w, &r->f2, s, n);
    while ((b = walk_next(&w, &at)) >= 0) {
        glyph2(t, r, b, x + at * scale, y, col, scale);
    }
    if (ell) {
        int k = w.prev >= 0 && r->f2.nkern ? kern(&r->f2, w.prev, MOY_FONT_ELLIPSIS) : 0;
        glyph2(t, r, MOY_FONT_ELLIPSIS, x + (w.pen + k) * scale, y, col, scale);
    }
}

int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw) {
    const row_t *r = font_row(font);
    if (source > MOY_TEXT_DATA || r == NULL) {
        return -1;          // a literal never reaches the raster
    }
    if (scale < 1) {
        scale = 1;
    }
    int ell = 0;
    if (maxw >= 0) {
        n = moy_text_cut_px(font, s, n, maxw, scale, &ell);
    }
    const mg_font_t *f = &r->f;
    int w;
    if (r->version == 2) {
        w = (width2(&r->f2, s, n) + (ell ? ellipsis_w(&r->f2, last_byte(s, n)) : 0)) * scale;
    } else {
        w = f->adv == NULL ? (int)n * f->w * scale : moy_text_width(font, s, n, scale);
    }
    if (maxw >= 0) {
        if (align == MOY_TEXT_CENTER) {
            int d = maxw - w;
            x += d >= 0 ? d / 2 : -((1 - d) / 2);    // floor, as `//` is
        } else if (align == MOY_TEXT_RIGHT) {
            x += maxw - w;
        }
    }
    if ((n > 0 || ell) && t != NULL && t->px != NULL) {
        if (r->version == 2) {
            draw2(t, r, s, n, x, y, col, scale, ell);
        } else if (f->adv == NULL) {
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

// One byte's advance in pixels, for petme128's row (a cell, no table) too.
static size_t adv_px(const mg_font_t *f, uint8_t b) {
    return f->adv == NULL ? (size_t)f->w : (size_t)advance(f, b);
}

// How many leading bytes of s[0..n) fit in `maxw` pixels of r's advances.
static size_t fit_bytes(const row_t *r, const uint8_t *s, size_t n, size_t maxw) {
    if (r->version == 2) {
        return fit2(&r->f2, s, n, maxw > 0x7fffffff ? 0x7fffffff : (int)maxw, NULL);
    }
    const mg_font_t *f = &r->f;
    size_t k = 0, at = 0;
    for (; k < n; k++) {
        at += adv_px(f, s[k]);
        if (at > maxw) {
            break;
        }
    }
    return k;
}

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

// The width of s[0..n): its bytes, or with `r` its advances in pixels.
static size_t span_w(const row_t *r, const uint8_t *s, size_t n) {
    if (r == NULL) {
        return n;
    }
    if (r->version == 2) {
        return (size_t)width2(&r->f2, s, n);
    }
    const mg_font_t *f = &r->f;
    size_t w = 0;
    for (size_t i = 0; i < n; i++) {
        w += adv_px(f, s[i]);
    }
    return w;
}

// `cols` is a line's width: in bytes, or with `f` in pixels of f's advances.
static void wrap_words(sink_t *k, const uint8_t *s, size_t n, size_t cols,
                       const row_t *f) {
    size_t i = 0;
    int have = 0;               // a line is open
    size_t ls = 0, le = 0;      // its span
    size_t lw = 0;              // its width, words joined by single spaces
    size_t sp = span_w(f, (const uint8_t *)" ", 1);
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
        size_t ww = span_w(f, s + ws, i - ws);
        if (have && lw + sp + ww <= cols) {
            if (ws - le != 1 || s[le] != ' ') {
                collapse = 1;
            }
            le = i;
            lw += sp + ww;
            continue;
        }
        if (!have && ww <= cols) {
            have = 1;
            ls = ws;
            le = i;
            lw = ww;
            collapse = 0;
            continue;
        }
        if (have) {
            emit(k, ls, le - ls, collapse);
            have = 0;
        }
        if (ww <= cols) {
            have = 1;
            ls = ws;
            le = i;
            lw = ww;
            collapse = 0;
        } else {
            // A word longer than a line: a line of its own, cut at a codepoint.
            size_t wn = i - ws;
            size_t fit = f == NULL ? moy_text_fit(s + ws, wn, cols)
                                   : moy_text_fit(s + ws, wn, fit_bytes(f, s + ws, wn, cols));
            if (fit > 0) {
                emit(k, ws, fit, 0);
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
        wrap_words(&k, s, n, (size_t)cols, mode == MOY_WRAP_WORDS_PX ? font_row(font) : NULL);
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
