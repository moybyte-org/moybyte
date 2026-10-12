// The surface painter (moy_ui.h, docs/theming_2026-09.md §4.4): a compiled
// surface into a box, through the target every primitive fills through.
//
// Order: the shadow (the box's last column and row, the body shrinking by
// one), the fill under the corner mask, then the outlines from the outside
// in -- the border, then the edge's levels. An outline is one pixel; its
// top and left take the edge's light colour, its bottom and right the dark,
// so a raised box is lit from the top left. A pattern fill is fillp's
// semantics, a 4x4 two-colour pattern anchored to the buffer and opaque,
// written by libmoy's word lane (moy_pat_span).

#include "moy_ui.h"
#include "moy_pixel.h"

// The corner masks: cut[r][i] pixels left out at each end of row i of a box
// of radius r (and, the masks being symmetric, of column i).
static const uint8_t k_cut[4][3] = {{0, 0, 0}, {1, 0, 0}, {2, 1, 0}, {3, 1, 1}};

static int cut(int r, int i) {
    return i < r ? k_cut[r][i] : 0;
}

// The 4x4 patterns, bit 15 the top left, a set bit the second colour.
#define PAT_DITHER 0x5A5Au
#define PAT_PINSTRIPE 0x0F0Fu
static const uint8_t k_bayer[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};

// Of 16 cells, `level` take the second colour, spread by the Bayer matrix.
static unsigned gradient_pat(int level) {
    unsigned pat = 0;
    for (int i = 0; i < 16; i++) {
        if (k_bayer[i] < level) {
            pat |= 1u << (15 - i);
        }
    }
    return pat;
}

typedef struct {
    const moy_text_target_t *t;
    const uint16_t *pal;
    int fills;
} paint_t;

static void solid(paint_t *p, int x, int y, int w, int h, int ci) {
    if (ci < 0 || w <= 0 || h <= 0) {
        return;
    }
    moy_target_fill(p->t, x, y, w, h, p->pal[ci & 63]);
    p->fills++;
}

// An opaque two-colour pattern over a rect, clipped as moy_target_fill clips.
static void pattern(paint_t *p, int x, int y, int w, int h, unsigned pat, int a, int b) {
    const moy_text_target_t *t = p->t;
    int stride = t->dw;
    if (stride <= 0 || t->px == NULL || w <= 0 || h <= 0) {
        return;
    }
    p->fills++;
    x -= t->cam_x;
    y -= t->cam_y;
    int x0 = t->cx0 > x ? t->cx0 : x;
    int y0 = t->cy0 > y ? t->cy0 : y;
    int x1 = x + w < t->cx1 ? x + w : t->cx1;
    int y1 = y + h < t->cy1 ? y + h : t->cy1;
    int rows = (int)(t->cap / (size_t)stride);
    x0 = x0 < 0 ? 0 : x0;
    y0 = y0 < 0 ? 0 : y0;
    x1 = x1 > stride ? stride : x1;
    y1 = y1 > rows ? rows : y1;
    if (x1 <= x0) {
        return;
    }
    uint16_t on = p->pal[a & 63], off = p->pal[b & 63];
    for (int row = y0; row < y1; row++) {
        unsigned bits = (pat >> (12 - ((row & 3) << 2))) & 15u;
        moy_pat_span(t->px + (size_t)row * (size_t)stride + (size_t)x0, x0, bits, on, off,
                     (size_t)(x1 - x0));
    }
}

// The fill of one row band [y, y + h) of the box: a solid or a pattern,
// the corner rows each cut by the mask.
static void fill_rows(paint_t *p, moy_ui_box_t b, int r, int y, int h, unsigned pat, int a,
                      int c) {
    int top = r, bot = b.h - r;
    for (int i = y; i < y + h;) {
        int c0, n;
        if (i < top || i >= bot) {
            c0 = cut(r, i < top ? i : b.h - 1 - i);
            n = 1;
        } else {
            c0 = 0;
            n = (bot < y + h ? bot : y + h) - i;
        }
        if (pat == 0) {
            solid(p, b.x + c0, b.y + i, b.w - 2 * c0, n, a);
        } else {
            pattern(p, b.x + c0, b.y + i, b.w - 2 * c0, n, pat, a, c);
        }
        i += n;
    }
}

static void fill_box(paint_t *p, moy_ui_box_t b, int r, int kind, int a, int c) {
    if (kind == MOY_UI_FILL_SOLID || kind == MOY_UI_FILL_STYLE || a < 0 || c < 0) {
        // one colour, or a pattern with one side silent: what is there, solid
        int col = (kind == MOY_UI_FILL_SOLID || kind == MOY_UI_FILL_STYLE || a >= 0) ? a : c;
        if (col >= 0) {
            fill_rows(p, b, r, 0, b.h, 0, col, 0);
        }
        return;
    }
    if (kind == MOY_UI_FILL_DITHER || kind == MOY_UI_FILL_PINSTRIPE) {
        fill_rows(p, b, r, 0, b.h, kind == MOY_UI_FILL_DITHER ? PAT_DITHER : PAT_PINSTRIPE,
                  a, c);
        return;
    }
    // gradient: a top to c bottom, one band per dither level
    for (int i = 0; i < b.h;) {
        int lv = b.h > 1 ? (i * 16 + (b.h - 1) / 2) / (b.h - 1) : 0;
        int j = i + 1;
        while (j < b.h && (j * 16 + (b.h - 1) / 2) / (b.h - 1) == lv) {
            j++;
        }
        if (lv == 0) {
            fill_rows(p, b, r, i, j - i, 0, a, 0);
        } else if (lv == 16) {
            fill_rows(p, b, r, i, j - i, 0, c, 0);
        } else {
            fill_rows(p, b, r, i, j - i, gradient_pat(lv), a, c);
        }
        i = j;
    }
}

// One outline of box b at radius r: top, left and the top arcs in `lt`;
// bottom, right and the bottom arcs in `dk` (-1: not drawn). Square, the
// top-right and bottom-left corners are the dark side's, as DrawEdge's are.
static void outline(paint_t *p, moy_ui_box_t b, int r, int lt, int dk) {
    int x = b.x, y = b.y, w = b.w, h = b.h;
    if (r == 0) {
        solid(p, x, y, w - 1, 1, lt);
        solid(p, x, y, 1, h - 1, lt);
        solid(p, x, y + h - 1, w, 1, dk);
        solid(p, x + w - 1, y, 1, h, dk);
        return;
    }
    int c0 = cut(r, 0);
    solid(p, x + c0, y, w - 2 * c0, 1, lt);
    solid(p, x + c0, y + h - 1, w - 2 * c0, 1, dk);
    solid(p, x, y + r, 1, h - 2 * r, lt);
    solid(p, x + w - 1, y + r, 1, h - 2 * r, dk);
    for (int i = 1; i < r; i++) {
        int a = cut(r, i), e = cut(r, i - 1) - 1;
        int n = (e > a ? e : a) - a + 1;
        solid(p, x + a, y + i, n, 1, lt);
        solid(p, x + w - a - n, y + i, n, 1, lt);
        solid(p, x + a, y + h - 1 - i, n, 1, dk);
        solid(p, x + w - a - n, y + h - 1 - i, n, 1, dk);
    }
}

static int term(int look, int t, int dflt) {
    if (t == MOY_UI_TERM_STYLE) {
        return dflt;
    }
    return t >= 0 ? t : moy_ui_token(look, -1 - t);
}

int moy_ui_paint_surface(const moy_text_target_t *t, const uint16_t pal[64], int look,
                         moy_ui_box_t b, const moy_ui_surface_t *sf, int field, int edge,
                         int radius) {
    paint_t p = {t, pal, 0};
    if (b.w <= 0 || b.h <= 0) {
        return 0;
    }
    if (sf->flags & MOY_UI_SURF_SHADOW) {
        int dk = moy_ui_token(look, MOY_UI_T_SHADE_DARK);
        b.w--;
        b.h--;
        moy_ui_box_t sh = {(int16_t)(b.x + 1), (int16_t)(b.y + 1), b.w, b.h};
        int rs = radius;
        rs = rs > sh.w / 2 ? sh.w / 2 : rs;
        rs = rs > sh.h / 2 ? sh.h / 2 : rs;
        if (sh.w > 0 && sh.h > 0) {
            outline(&p, sh, rs, -1, dk);
        }
        if (b.w <= 0 || b.h <= 0) {
            return p.fills;
        }
    }
    int r = radius < 0 ? 0 : radius > 3 ? 3 : radius;
    r = r > b.w / 2 ? b.w / 2 : r;
    r = r > b.h / 2 ? b.h / 2 : r;
    int a = term(look, sf->a, field), c = term(look, sf->b, field);
    fill_box(&p, b, r, sf->fill, a, c);
    int hi = moy_ui_token(look, MOY_UI_T_SHADE_HI);
    int lo = moy_ui_token(look, MOY_UI_T_SHADE_LO);
    int dk = moy_ui_token(look, MOY_UI_T_SHADE_DARK);
    // the outlines, outside in: {top-left colour, bottom-right colour}
    int lines[3][2], n = 0;
    if (sf->flags & MOY_UI_SURF_BORDER) {
        int bc = term(look, sf->line, edge);
        lines[n][0] = bc;
        lines[n++][1] = bc;
    }
    int lv[2][2];
    int levels = 0;
    switch (sf->edge) {
        case MOY_UI_EDGE_RAISED:
            lv[0][0] = hi, lv[0][1] = lo;
            levels = 1;
            break;
        case MOY_UI_EDGE_SUNKEN:
            lv[0][0] = lo, lv[0][1] = hi;
            levels = 1;
            break;
        case MOY_UI_EDGE_ETCHED:
            lv[0][0] = lo, lv[0][1] = hi;
            lv[1][0] = hi, lv[1][1] = lo;
            levels = 2;
            break;
        case MOY_UI_EDGE_BEVEL2:
            lv[0][0] = a, lv[0][1] = dk;
            lv[1][0] = hi, lv[1][1] = lo;
            levels = 2;
            break;
        default:
            break;
    }
    for (int i = 0; i < levels; i++) {
        lines[n][0] = lv[i][0];
        lines[n++][1] = lv[i][1];
    }
    for (int k = 0; k < n; k++) {
        moy_ui_box_t in = {(int16_t)(b.x + k), (int16_t)(b.y + k), (int16_t)(b.w - 2 * k),
                           (int16_t)(b.h - 2 * k)};
        if (in.w <= 0 || in.h <= 0) {
            break;
        }
        int rk = r - k > 0 ? r - k : 0;
        rk = rk > in.w / 2 ? in.w / 2 : rk;
        rk = rk > in.h / 2 ? in.h / 2 : rk;
        outline(&p, in, rk, lines[k][0], lines[k][1]);
    }
    return p.fills;
}
