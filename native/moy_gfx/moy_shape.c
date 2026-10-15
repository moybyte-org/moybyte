/* The 2026-10 look's shapes: moy_shape.h says what is here and how it
 * composes. Integer throughout. The -O3 pragma comes before the includes for
 * moy_gfx_kernels.c's reason: mg_fill_run inlines into the span fills. */

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O3")
#endif

#include "moy_shape.h"

#include <stdlib.h>
#include <string.h>

#include "moy_gfx_kernels.h"

static int s_swapped = 1;
static uint32_t s_st[MOY_SHAPE_STATS];

void moy_shape_wire_swapped(int swapped) {
    s_swapped = swapped != 0;
}

void moy_shape_stats(uint32_t out[MOY_SHAPE_STATS]) {
    memcpy(out, s_st, sizeof(s_st));
}

static inline uint16_t can(uint16_t w) {
    return s_swapped ? (uint16_t)((w << 8) | (w >> 8)) : w;
}

#define wire can

uint16_t moy_shape_wire(uint16_t canonical) {
    return wire(canonical);
}

// Three colours, each at a weight out of 15, one canonical RGB565 word.
static inline uint16_t mix3(uint16_t a, int wa, uint16_t b, int wb, uint16_t c, int wc) {
    int r = (((a >> 11) & 31) * wa + ((b >> 11) & 31) * wb + ((c >> 11) & 31) * wc + 7) / 15;
    int g = (((a >> 5) & 63) * wa + ((b >> 5) & 63) * wb + ((c >> 5) & 63) * wc + 7) / 15;
    int bl = ((a & 31) * wa + (b & 31) * wb + (c & 31) * wc + 7) / 15;
    return (uint16_t)((r << 11) | (g << 5) | bl);
}

// f over b at weight wt (0..256), canonical.
static inline uint16_t over(uint16_t f, uint16_t b, int wt) {
    int iw = 256 - wt;
    int r = (((f >> 11) & 31) * wt + ((b >> 11) & 31) * iw + 128) >> 8;
    int g = (((f >> 5) & 63) * wt + ((b >> 5) & 63) * iw + 128) >> 8;
    int bl = ((f & 31) * wt + (b & 31) * iw + 128) >> 8;
    return (uint16_t)((r << 11) | (g << 5) | bl);
}

static inline int imin(int a, int b) {
    return a < b ? a : b;
}

static inline int imax(int a, int b) {
    return a > b ? a : b;
}

// -- the target --------------------------------------------------------------------

typedef struct {
    uint16_t *px;
    int stride;
    int x0, y0, x1, y1;     // the clip in buffer coordinates, inside the buffer
    int cam_x, cam_y;
    // the hole of the row being drawn: [hx0, hx1) left alone
    int hx0, hx1;
    const moy_shape_hole_t *hole;
} tgt_t;

static int tgt_open(const moy_text_target_t *t, const moy_shape_hole_t *hole, tgt_t *g) {
    if (t == NULL || t->px == NULL || t->dw <= 0) {
        return 0;
    }
    int rows = (int)(t->cap / (size_t)t->dw);
    g->px = t->px;
    g->stride = t->dw;
    g->x0 = imax(t->cx0, 0);
    g->y0 = imax(t->cy0, 0);
    g->x1 = imin(t->cx1, t->dw);
    g->y1 = imin(t->cy1, rows);
    g->cam_x = t->cam_x;
    g->cam_y = t->cam_y;
    g->hole = hole != NULL && hole->w > 0 && hole->h > 0 ? hole : NULL;
    g->hx0 = g->hx1 = 0;
    return g->x1 > g->x0 && g->y1 > g->y0;
}

// The hole's span on buffer row Y: its full width, or less its radius on
// either side within its radius of its top and bottom (what its corners
// leave uncovered is drawn).
static void tgt_row(tgt_t *g, int Y) {
    g->hx0 = g->hx1 = 0;
    const moy_shape_hole_t *h = g->hole;
    if (h == NULL) {
        return;
    }
    int top = h->y - g->cam_y, i = Y - top;
    if (i < 0 || i >= h->h) {
        return;
    }
    int r = imax(0, imin(h->r, imin(h->w, h->h) / 2));
    int in = (i < r || i >= h->h - r) ? r : 0;
    g->hx0 = h->x - g->cam_x + in;
    g->hx1 = h->x - g->cam_x + h->w - in;
}

static inline int tgt_ok(const tgt_t *g, int X) {
    return X >= g->x0 && X < g->x1 && (X < g->hx0 || X >= g->hx1);
}

// An opaque run [X0, X1) of row Y, the clip and the hole applied.
static int run(const tgt_t *g, int Y, int X0, int X1, uint16_t w) {
    X0 = imax(X0, g->x0);
    X1 = imin(X1, g->x1);
    if (X1 <= X0) {
        return 0;
    }
    uint16_t *row = g->px + (size_t)Y * (size_t)g->stride;
    int n = 0;
    if (g->hx1 > g->hx0 && g->hx0 < X1 && g->hx1 > X0) {
        if (g->hx0 > X0) {
            mg_fill_run(row + X0, (size_t)(g->hx0 - X0), w);
            n += g->hx0 - X0;
        }
        if (g->hx1 < X1) {
            mg_fill_run(row + g->hx1, (size_t)(X1 - g->hx1), w);
            n += X1 - g->hx1;
        }
        return n;
    }
    mg_fill_run(row + X0, (size_t)(X1 - X0), w);
    return X1 - X0;
}

// -- corner coverage ---------------------------------------------------------------

#define COV_SLOTS 4
#define COV_BYTES ((MOY_SHAPE_RMAX * MOY_SHAPE_RMAX + 1) / 2)
// The slots are one allocation, made at the first corner (PSRAM where the
// board's allocator puts it); without it a corner's coverage is computed as
// it draws -- the same values.
static uint8_t *s_cov;
static uint8_t s_cov_r[COV_SLOTS];
static uint32_t s_cov_use[COV_SLOTS], s_tick;

// The coverage of cell (i, j) of radius r's corner box, the arc centred at
// (r, r): sixteen samples, at the centres of a 4x4 grid, in eighths.
static int cov_compute(int r, int i, int j) {
    int c = 8 * r, rr = c * c, n = 0;
    for (int u = 0; u < 4; u++) {
        int dy = c - (8 * i + 2 * u + 1);
        for (int s = 0; s < 4; s++) {
            int dx = c - (8 * j + 2 * s + 1);
            n += dx * dx + dy * dy <= rr;
        }
    }
    return (n * 15 + 8) / 16;
}

static const uint8_t *cov_table(int r) {
    if (r <= 0) {
        return NULL;
    }
    if (s_cov == NULL) {
        s_cov = (uint8_t *)malloc((size_t)COV_SLOTS * COV_BYTES);
        if (s_cov == NULL) {
            return NULL;
        }
    }
    s_tick++;
    int lru = 0;
    for (int k = 0; k < COV_SLOTS; k++) {
        if (s_cov_r[k] == r) {
            s_cov_use[k] = s_tick;
            return s_cov + (size_t)k * COV_BYTES;
        }
        if (s_cov_use[k] < s_cov_use[lru]) {
            lru = k;
        }
    }
    uint8_t *t = s_cov + (size_t)lru * COV_BYTES;
    memset(t, 0, COV_BYTES);
    for (int i = 0; i < r; i++) {
        for (int j = 0; j < r; j++) {
            int idx = i * r + j, v = cov_compute(r, i, j);
            t[idx >> 1] |= (uint8_t)((idx & 1) ? v : v << 4);
        }
    }
    s_cov_r[lru] = (uint8_t)r;
    s_cov_use[lru] = s_tick;
    s_st[MOY_SHAPE_ST_COV_BAKES]++;
    return t;
}

static inline int cov_read(const uint8_t *t, int r, int i, int j) {
    if (t == NULL) {
        return cov_compute(r, i, j);
    }
    int idx = i * r + j;
    return (idx & 1) ? t[idx >> 1] & 15 : t[idx >> 1] >> 4;
}

int moy_shape_coverage(int r, int i, int j) {
    r = imin(r, MOY_SHAPE_RMAX);
    if (r <= 0 || i < 0 || j < 0 || i >= r || j >= r) {
        return 15;
    }
    return cov_read(cov_table(r), r, i, j);
}

// A rounded rect's corners, its tables looked up once.
typedef struct {
    int w, h, r[4], stepped;
    const uint8_t *t[4];
} corners_t;

static void corners_set(corners_t *c, int w, int h, const int r[4], int stepped) {
    c->w = w;
    c->h = h;
    c->stepped = stepped;
    for (int k = 0; k < 4; k++) {
        c->r[k] = r[k];
        c->t[k] = cov_table(r[k]);
    }
}

// Pixel (i, j)'s coverage by the shape, 0..15 (a stepped corner 0 or 15).
static int corners_cov(const corners_t *c, int i, int j) {
    if (i < 0 || j < 0 || i >= c->h || j >= c->w) {
        return 0;
    }
    int v = 15;
    if (i < c->r[0] && j < c->r[0]) {
        v = cov_read(c->t[0], c->r[0], i, j);
    } else if (i < c->r[1] && j >= c->w - c->r[1]) {
        v = cov_read(c->t[1], c->r[1], i, c->w - 1 - j);
    } else if (i >= c->h - c->r[2] && j >= c->w - c->r[2]) {
        v = cov_read(c->t[2], c->r[2], c->h - 1 - i, c->w - 1 - j);
    } else if (i >= c->h - c->r[3] && j < c->r[3]) {
        v = cov_read(c->t[3], c->r[3], c->h - 1 - i, j);
    } else {
        return 15;
    }
    if (c->stepped) {
        v = v >= 8 ? 15 : 0;
    }
    return v;
}

// -- paints ------------------------------------------------------------------------

static const uint8_t k_bayer[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};

// Two canonical colours at weight f (0..256) of the second, each channel at
// eight bits and rounded back to RGB565.
static uint16_t lerp565(uint16_t a, uint16_t b, int f) {
    int ar = ((a >> 11) & 31) * 255 / 31, ag = ((a >> 5) & 63) * 255 / 63, ab = (a & 31) * 255 / 31;
    int br = ((b >> 11) & 31) * 255 / 31, bg = ((b >> 5) & 63) * 255 / 63, bb = (b & 31) * 255 / 31;
    int r = ar + (((br - ar) * f) >> 8), g = ag + (((bg - ag) * f) >> 8),
        bl = ab + (((bb - ab) * f) >> 8);
    return (uint16_t)((((r * 31 + 127) / 255) << 11) | (((g * 63 + 127) / 255) << 5)
                      | ((bl * 31 + 127) / 255));
}

typedef struct {
    uint16_t c0, c1;   // wire order
    unsigned bits;     // 4 bits for the row, bit 3 the column x % 4 == 0: c1
} prow_t;

// The paint's row `row` of `ph` rows on buffer row Y.
static void paint_row(const moy_shape_paint_t *p, int row, int ph, int Y, prow_t *o) {
    int n = imax(1, imin(p->n, MOY_SHAPE_STOPS));
    o->bits = 0;
    o->c0 = o->c1 = p->c[0];
    if (n == 1) {
        return;
    }
    int t = ph > 1 ? row * 1000 / (ph - 1) : 0;
    t = imax(0, imin(1000, t));
    if (p->mode == MOY_SHAPE_STEPS) {
        int bands = imax(2, p->steps);
        int b = imin(bands - 1, t * bands / 1001);
        t = b * 1000 / (bands - 1);
    }
    if (t <= p->t[0]) {
        return;
    }
    if (t >= p->t[n - 1]) {
        o->c0 = o->c1 = p->c[n - 1];
        return;
    }
    int k = 0;
    while (k < n - 2 && t >= p->t[k + 1]) {
        k++;
    }
    int span = imax(1, p->t[k + 1] - p->t[k]);
    int f = (t - p->t[k]) * 256 / span;
    if (p->mode == MOY_SHAPE_DITHER) {
        int level = (f * 16 + 128) >> 8, yy = Y & 3;
        o->c0 = p->c[k];
        o->c1 = p->c[k + 1];
        for (int x = 0; x < 4; x++) {
            if (k_bayer[yy * 4 + x] < level) {
                o->bits |= 8u >> x;
            }
        }
        if (o->bits == 15) {
            o->c0 = o->c1;
            o->bits = 0;
        }
        return;
    }
    o->c0 = o->c1 = wire(lerp565(can(p->c[k]), can(p->c[k + 1]), f));
}

static inline uint16_t prow_at(const prow_t *o, int X) {
    return (o->bits >> (3 - (X & 3))) & 1 ? o->c1 : o->c0;
}

// -- the rounded rect ----------------------------------------------------------------

typedef struct {
    corners_t out, in;
    int line, has_in;
    int bevel;
    uint16_t hi, lo;       // wire
    int fill;
} rr_t;

// The ring's colour at shape pixel (i, j): a bevel lights the top and left.
static uint16_t ring_col(const rr_t *s, int i, int j) {
    if (!s->bevel) {
        return s->hi;
    }
    int w = s->out.w, h = s->out.h;
    const int *r = s->out.r;
    int R0 = imax(r[0], s->line), R1 = imax(r[1], s->line), R2 = imax(r[2], s->line),
        R3 = imax(r[3], s->line);
    if (i < R0 && j < R0) {
        return s->hi;
    }
    if (i >= h - R2 && j >= w - R2) {
        return s->lo;
    }
    if (i < R1 && j >= w - R1) {
        return i < w - 1 - j ? s->hi : s->lo;     // top right
    }
    if (i >= h - R3 && j < R3) {
        return j < h - 1 - i ? s->hi : s->lo;     // bottom left
    }
    if (i < s->line || j < s->line) {
        return s->hi;
    }
    return s->lo;
}

// One edge pixel at buffer (X, Y), shape pixel (i, j). Answers 1 if written.
static int rr_px(const rr_t *s, const tgt_t *g, int mask, int i, int j, int X, uint16_t *row,
                 const prow_t *pr) {
    if (!tgt_ok(g, X)) {
        return 0;
    }
    int co = corners_cov(&s->out, i, j);
    if (co == 0) {
        return 0;
    }
    int ci = s->line ? (s->has_in ? corners_cov(&s->in, i - s->line, j - s->line) : 0) : co;
    int wr = co - ci, wf = s->fill ? ci : 0;
    int wu = 15 - wr - wf;
    if (wu >= 15) {
        return 0;
    }
    // A ring's pixel it covers half or more of is the inner's, whole: the
    // ring stays unbroken where its fringe lies under something else.
    int inner = co == 15 || wr >= 8;
    if (mask == MOY_SHAPE_INNER ? !inner : mask == MOY_SHAPE_FRINGE ? inner : 0) {
        return 0;
    }
    if (mask == MOY_SHAPE_INNER && wu) {
        wr += wu;
        wu = 0;
    }
    uint16_t rc = wr ? can(ring_col(s, i, j)) : 0;
    uint16_t fc = wf ? can(prow_at(pr, X)) : 0;
    uint16_t under = wu ? can(row[X]) : 0;
    if (wu) {
        s_st[MOY_SHAPE_ST_BLENDS]++;
    }
    row[X] = wire(mix3(under, wu, rc, wr, fc, wf));
    return 1;
}

static int clamp_r(int r, int w, int h) {
    return imax(0, imin(imin(r, MOY_SHAPE_RMAX), imin(w, h) / 2));
}

int moy_shape_rrect(const moy_text_target_t *t, int x, int y, int w, int h,
                    const moy_shape_rrect_t *sh) {
    tgt_t g;
    if (w <= 0 || h <= 0 || sh == NULL || !tgt_open(t, &sh->hole, &g)) {
        return 0;
    }
    s_st[MOY_SHAPE_ST_RRECTS]++;
    rr_t s;
    int r[4], ri[4];
    for (int k = 0; k < 4; k++) {
        r[k] = clamp_r(sh->r[k], w, h);
    }
    s.line = imax(0, imin(sh->line, imin(w, h) / 2));
    s.fill = sh->fill != NULL;
    s.bevel = sh->line_lo >= 0;
    s.hi = (uint16_t)sh->line_hi;
    s.lo = (uint16_t)(s.bevel ? sh->line_lo : sh->line_hi);
    corners_set(&s.out, w, h, r, sh->stepped);
    int iw = w - 2 * s.line, ih = h - 2 * s.line;
    s.has_in = s.line > 0 && iw > 0 && ih > 0;
    if (s.has_in) {
        for (int k = 0; k < 4; k++) {
            ri[k] = clamp_r(r[k] - s.line, iw, ih);
        }
        corners_set(&s.in, iw, ih, ri, sh->stepped);
    }
    if (s.line == 0 && !s.fill) {
        return 0;
    }
    int mask = sh->mask;
    int bx = x - g.cam_x, by = y - g.cam_y;
    int py0 = sh->fill != NULL && sh->fill->h > 0 ? sh->fill->y0 : y;
    int ph = sh->fill != NULL && sh->fill->h > 0 ? sh->fill->h : h;
    int n = 0;
    prow_t pr = {0, 0, 0};
    for (int i = 0; i < h; i++) {
        int Y = by + i;
        if (Y < g.y0 || Y >= g.y1) {
            continue;
        }
        tgt_row(&g, Y);
        uint16_t *row = g.px + (size_t)Y * (size_t)g.stride;
        if (s.fill) {
            paint_row(sh->fill, y + i - py0, ph, Y, &pr);
        }
        // the edge zones either side: a corner's box, or the ring's width
        int rl = i < r[0] ? r[0] : i >= h - r[3] ? r[3] : 0;
        int rr = i < r[1] ? r[1] : i >= h - r[2] ? r[2] : 0;
        int L = imin(imax(rl, s.line), w), R = imin(imax(rr, s.line), w - L);
        for (int j = 0; j < L; j++) {
            n += rr_px(&s, &g, mask, i, j, bx + j, row, &pr);
        }
        for (int j = w - R; j < w; j++) {
            n += rr_px(&s, &g, mask, i, j, bx + j, row, &pr);
        }
        if (mask == MOY_SHAPE_FRINGE || w - R <= L) {
            continue;
        }
        int X0 = bx + L, X1 = bx + w - R;
        int ring_row = s.line > 0 && (i < s.line || i >= h - s.line);
        if (ring_row) {
            int top = i < s.line;
            n += run(&g, Y, X0, X1, top || !s.bevel ? s.hi : s.lo);
        } else if (s.fill) {
            if (pr.bits == 0) {
                n += run(&g, Y, X0, X1, pr.c0);
            } else {
                for (int X = imax(X0, g.x0); X < imin(X1, g.x1); X++) {
                    if (tgt_ok(&g, X)) {
                        row[X] = prow_at(&pr, X);
                        n++;
                    }
                }
            }
        }
    }
    return n;
}

// -- shadows -------------------------------------------------------------------------

typedef struct {
    int r, b, stepped, C;
    uint8_t *T;      // C x C, 4 bits a cell
    uint8_t P[2 * MOY_SHAPE_BLUR_MAX];
    uint32_t use;
} mask_t;

#define MASK_SLOTS 3
static mask_t s_mask[MASK_SLOTS];

static uint32_t isqrt(uint32_t v) {
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v) {
        bit >>= 2;
    }
    while (bit) {
        if (v >= r + bit) {
            v -= r + bit;
            r = (r >> 1) + bit;
        } else {
            r >>= 1;
        }
        bit >>= 2;
    }
    return r;
}

// The alpha (0..15) at a signed distance, in sixteenths of a pixel, from the
// shape's edge, positive outside: a smoothstep over [-b, b].
static int falloff(int sd16, int b) {
    int b16 = b * 16;
    int tt = (sd16 + b16) * 256 / (2 * b16);
    tt = imax(0, imin(256, tt));
    int s = (tt * tt * (768 - 2 * tt)) >> 16;
    return ((256 - s) * 15 + 128) >> 8;
}

// The mask's corner cell (ii, jj): the pixel's centre (jj - b + 1/2,
// ii - b + 1/2) from the shape's corner.
static int mask_cell(int r, int b, int stepped, int ii, int jj) {
    if (b == 0) {
        if (ii >= r || jj >= r) {
            return 15;
        }
        int v = cov_compute(r, ii, jj);
        return stepped ? (v >= 8 ? 15 : 0) : v;
    }
    int r16 = r * 16;
    int qx = r16 - ((jj - b) * 16 + 8), qy = r16 - ((ii - b) * 16 + 8);
    int ox = imax(qx, 0), oy = imax(qy, 0);
    int sd = (int)isqrt((uint32_t)(ox * ox + oy * oy)) + imin(imax(qx, qy), 0) - r16;
    return falloff(sd, b);
}

static int mask_prof(int b, int k) {
    return falloff(-((k - b) * 16 + 8), b);
}

// The mask for (r, b): a cached one, a new one baked, or (no memory) a stand-in
// with no table, whose cells are computed as they draw -- the same values.
static const mask_t *mask_get(int r, int b, int stepped, mask_t *spare) {
    if (b > 0) {
        stepped = 0;
    }
    s_tick++;
    int lru = 0;
    for (int k = 0; k < MASK_SLOTS; k++) {
        mask_t *m = &s_mask[k];
        if (m->T != NULL && m->r == r && m->b == b && m->stepped == stepped) {
            m->use = s_tick;
            return m;
        }
        if (m->T == NULL || (s_mask[lru].T != NULL && m->use < s_mask[lru].use)) {
            lru = k;
        }
    }
    int C = b + imax(r, b);
    mask_t *m = &s_mask[lru];
    uint8_t *T = NULL;
    if (C > 0) {
        free(m->T);
        m->T = NULL;
        T = (uint8_t *)malloc((size_t)(C * C + 1) / 2);
    }
    if (T == NULL) {
        m = spare;
        m->T = NULL;
    } else {
        memset(T, 0, (size_t)(C * C + 1) / 2);
        for (int i = 0; i < C; i++) {
            for (int j = 0; j < C; j++) {
                int idx = i * C + j, v = mask_cell(r, b, stepped, i, j);
                T[idx >> 1] |= (uint8_t)((idx & 1) ? v : v << 4);
            }
        }
        m->T = T;
        s_st[MOY_SHAPE_ST_SHADOW_BAKES]++;
    }
    m->r = r;
    m->b = b;
    m->stepped = stepped;
    m->C = C;
    m->use = s_tick;
    for (int k = 0; k < 2 * b; k++) {
        m->P[k] = (uint8_t)mask_prof(b, k);
    }
    return m;
}

static inline int mask_at(const mask_t *m, int ii, int jj) {
    int C = m->C;
    if (ii < C && jj < C) {
        if (m->T == NULL) {
            return mask_cell(m->r, m->b, m->stepped, ii, jj);
        }
        int idx = ii * C + jj;
        return (idx & 1) ? m->T[idx >> 1] & 15 : m->T[idx >> 1] >> 4;
    }
    int k = ii < C ? ii : jj < C ? jj : 2 * m->b;
    return k < 2 * m->b ? m->P[k] : 15;
}

void moy_shape_shadow_pad(const moy_shape_shadow_t *s, int out[4]) {
    int b = imax(0, imin(s->blur, MOY_SHAPE_BLUR_MAX));
    out[0] = imax(0, b - s->dx);
    out[1] = imax(0, b - s->dy);
    out[2] = imax(0, b + s->dx);
    out[3] = imax(0, b + s->dy);
}

int moy_shape_shadow(const moy_text_target_t *t, int x, int y, int w, int h,
                     const moy_shape_shadow_t *s, const moy_shape_hole_t *hole) {
    tgt_t g;
    if (w <= 0 || h <= 0 || s == NULL || s->alpha <= 0 || !tgt_open(t, hole, &g)) {
        return 0;
    }
    s_st[MOY_SHAPE_ST_SHADOWS]++;
    int b = imax(0, imin(s->blur, MOY_SHAPE_BLUR_MAX));
    int r = clamp_r(s->r, w, h);
    mask_t spare;
    const mask_t *m = mask_get(r, b, s->stepped, &spare);
    int alpha = imin(s->alpha, 255);
    int wt[16];
    for (int k = 0; k < 16; k++) {
        wt[k] = (k * alpha * 256 + 15 * 255 / 2) / (15 * 255);
    }
    uint16_t col = can((uint16_t)s->col);
    int sx = x + s->dx - g.cam_x, sy = y + s->dy - g.cam_y;
    int n = 0;
    int Ya = imax(sy - b, g.y0), Yb = imin(sy + h + b, g.y1);
    int Xa = imax(sx - b, g.x0), Xb = imin(sx + w + b, g.x1);
    for (int Y = Ya; Y < Yb; Y++) {
        tgt_row(&g, Y);
        int i = Y - sy;
        int ii = imin(i, h - 1 - i) + b;
        uint16_t *row = g.px + (size_t)Y * (size_t)g.stride;
        for (int X = Xa; X < Xb; X++) {
            if (X >= g.hx0 && X < g.hx1) {
                X = g.hx1 - 1;
                continue;
            }
            int j = X - sx;
            int a = wt[mask_at(m, ii, imin(j, w - 1 - j) + b)];
            if (a == 0) {
                continue;
            }
            row[X] = wire(a >= 256 ? col : over(col, can(row[X]), a));
            n++;
        }
    }
    s_st[MOY_SHAPE_ST_BLENDS] += (uint32_t)n;
    return n;
}

void moy_shape_reset(int caches) {
    memset(s_st, 0, sizeof(s_st));
    if (!caches) {
        return;
    }
    memset(s_cov_r, 0, sizeof(s_cov_r));
    memset(s_cov_use, 0, sizeof(s_cov_use));
    for (int k = 0; k < MASK_SLOTS; k++) {
        free(s_mask[k].T);
        s_mask[k].T = NULL;
    }
}

static void flat_target(moy_text_target_t *t, uint16_t *dst, size_t cap, int dw, int cam_x,
                        int cam_y, int cx0, int cy0, int cx1, int cy1) {
    t->px = dst;
    t->cap = cap;
    t->dw = dw;
    t->cam_x = cam_x;
    t->cam_y = cam_y;
    t->cx0 = cx0;
    t->cy0 = cy0;
    t->cx1 = cx1;
    t->cy1 = cy1;
}

int mg_rrect(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h, int r, int opts,
             int32_t hi, int32_t lo, const moy_shape_paint_t *fill, int cam_x, int cam_y,
             int cx0, int cy0, int cx1, int cy1) {
    moy_text_target_t t;
    flat_target(&t, dst, cap, dw, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_shape_rrect_t s;
    memset(&s, 0, sizeof(s));
    for (int k = 0; k < 4; k++) {
        s.r[k] = (uint8_t)((opts >> k) & 1 ? imax(0, imin(r, 255)) : 0);
    }
    s.stepped = (opts & MOY_SHAPE_OPT_STEPPED) != 0;
    s.mask = (uint8_t)((opts >> 5) & 3);
    s.line = (uint8_t)((opts >> 8) & 255);
    s.line_hi = hi;
    s.line_lo = lo;
    s.fill = fill;
    return moy_shape_rrect(&t, x, y, w, h, &s);
}

int mg_shadow(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h, int r, int blur,
              int dx, int dy, int32_t col, int alpha, int stepped, const moy_shape_hole_t *hole,
              int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1) {
    moy_text_target_t t;
    flat_target(&t, dst, cap, dw, cam_x, cam_y, cx0, cy0, cx1, cy1);
    moy_shape_shadow_t s = {r, blur, dx, dy, col, alpha, (uint8_t)(stepped != 0)};
    return moy_shape_shadow(&t, x, y, w, h, &s, hole);
}
