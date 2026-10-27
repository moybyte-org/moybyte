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

// The corner and shadow caches are PSRAM where the board has it. Each is under
// the boards' SPIRAM_MALLOC_ALWAYSINTERNAL, so a plain malloc would put it in
// internal SRAM, and the desk draws corners and shadows from its first frame:
// the idle desk would hold them there for good.
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define SHAPE_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#define SHAPE_ALLOC(n) malloc(n)
#endif

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

// A shadow's weight (0..256) where its mask covers k of 15 at `alpha`.
static inline int weight(int k, int alpha) {
    return (k * alpha * 256 + 15 * 255 / 2) / (15 * 255);
}

uint16_t moy_shape_over(uint16_t f, uint16_t b, int alpha) {
    int a = weight(15, alpha < 0 ? 0 : alpha > 255 ? 255 : alpha);
    return a >= 256 ? f : over(f, b, a);
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

static int hole_inset(int r, int i);

// The hole's span on buffer row Y: its full width, or within its radius of
// its top and bottom less the pixels its corners do not cover whole (what
// its corners leave uncovered is drawn).
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
    int r = imax(0, imin(imin(h->r, MOY_SHAPE_RMAX), imin(h->w, h->h) / 2));
    int in = i < r ? hole_inset(r, i) : i >= h->h - r ? hole_inset(r, h->h - 1 - i) : 0;
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

#define COV_SLOTS 10
#define COV_BYTES ((MOY_SHAPE_RMAX * MOY_SHAPE_RMAX + 1) / 2)
// A slot is its radius's coverage, then each row's extents (cov_rows), smooth
// and stepped.
#define COV_SLOT (COV_BYTES + 4 * MOY_SHAPE_RMAX)
// The slots are one allocation, made at the first corner (SHAPE_ALLOC);
// without it a corner's coverage is computed as it draws -- the same values.
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

static inline int cov_read(const uint8_t *t, int r, int i, int j);

static const uint8_t *cov_table(int r) {
    if (r <= 0) {
        return NULL;
    }
    if (s_cov == NULL) {
        s_cov = (uint8_t *)SHAPE_ALLOC((size_t)COV_SLOTS * COV_SLOT);
        if (s_cov == NULL) {
            return NULL;
        }
    }
    s_tick++;
    int lru = 0;
    for (int k = 0; k < COV_SLOTS; k++) {
        if (s_cov_r[k] == r) {
            s_cov_use[k] = s_tick;
            return s_cov + (size_t)k * COV_SLOT;
        }
        if (s_cov_use[k] < s_cov_use[lru]) {
            lru = k;
        }
    }
    uint8_t *t = s_cov + (size_t)lru * COV_SLOT;
    memset(t, 0, COV_BYTES);
    for (int i = 0; i < r; i++) {
        for (int j = 0; j < r; j++) {
            int idx = i * r + j, v = cov_compute(r, i, j);
            t[idx >> 1] |= (uint8_t)((idx & 1) ? v : v << 4);
        }
    }
    // A row's coverage rises from the box's outer edge to the arc's centre:
    // its columns before the first covered one, and its first whole one.
    uint8_t *m = t + COV_BYTES;
    for (int i = 0; i < r; i++) {
        for (int st = 0; st < 2; st++) {
            int z = r, a = r;
            for (int j = r - 1; j >= 0; j--) {
                int v = cov_read(t, r, i, j);
                if (st) {
                    v = v >= 8 ? 15 : 0;
                }
                if (v == 15) {
                    a = j;
                }
                if (v > 0) {
                    z = j;
                }
            }
            m[st * 2 * MOY_SHAPE_RMAX + i] = (uint8_t)z;
            m[st * 2 * MOY_SHAPE_RMAX + MOY_SHAPE_RMAX + i] = (uint8_t)a;
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

// The columns of row i of a corner box of radius r the arc does not cover
// whole: where a hole's span on that row starts.
static int hole_inset(int r, int i) {
    const uint8_t *t = cov_table(r);
    for (int j = 0; j < r; j++) {
        if (cov_read(t, r, i, j) == 15) {
            return j;
        }
    }
    return r;
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

// Row i of a corner's box of radius r, counted from the box's outer edge:
// columns [0, *z) it does not cover, [*a, r) it covers whole (cov_table).
static inline void cov_rows(const corners_t *c, int k, int i, int *z, int *a) {
    const uint8_t *m = c->t[k] + COV_BYTES + (c->stepped ? 2 * MOY_SHAPE_RMAX : 0);
    *z = m[i];
    *a = m[MOY_SHAPE_RMAX + i];
}

// Row i of a box's left (side 0) or right (side 1) edge zone, as distances in
// from that side: [0, *z) uncovered, [*a, ...) covered whole. A row outside the
// box is uncovered (*z, *a past any zone).
static void cov_side(const corners_t *c, int side, int i, int *z, int *a) {
    *z = *a = 0;
    if (i < 0 || i >= c->h) {
        *z = *a = 1 << 14;
        return;
    }
    int top = side ? 1 : 0, foot = side ? 2 : 3;
    if (i < c->r[top]) {
        cov_rows(c, top, i, z, a);
    } else if (i >= c->h - c->r[foot]) {
        cov_rows(c, foot, c->h - 1 - i, z, a);
    }
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
    if (wu == 0 && (wr == 0 || wf == 0)) {
        row[X] = wr ? ring_col(s, i, j) : prow_at(pr, X);   // whole: no mix
        return 1;
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

// Whether rr_px's MOY_SHAPE_INNER writes shape pixel (i, j) opaque, for a
// fill and a ring together: covered whole, or half or more of it the ring's.
static int inner_px(const corners_t *out, const corners_t *in, int has_in, int line, int i,
                    int j) {
    int co = corners_cov(out, i, j);
    if (co == 15) {
        return 1;
    }
    if (line <= 0 || co == 0) {
        return 0;
    }
    int ci = has_in ? corners_cov(in, i - line, j - line) : 0;
    return co - ci >= 8;
}

void moy_shape_inner_row(int w, int h, const int r[4], int stepped, int line, int i, int *x0,
                         int *x1) {
    *x0 = *x1 = 0;
    if (w <= 0 || h <= 0 || i < 0 || i >= h) {
        return;
    }
    int rr[4], ri[4];
    for (int k = 0; k < 4; k++) {
        rr[k] = clamp_r(r[k], w, h);
    }
    corners_t out, in;
    corners_set(&out, w, h, rr, stepped);
    line = imax(0, imin(line, imin(w, h) / 2));
    int iw = w - 2 * line, ih = h - 2 * line;
    int has_in = line > 0 && iw > 0 && ih > 0;
    if (has_in) {
        for (int k = 0; k < 4; k++) {
            ri[k] = clamp_r(rr[k] - line, iw, ih);
        }
        corners_set(&in, iw, ih, ri, stepped);
    }
    int a = 0, b = w - 1;
    while (a < w && !inner_px(&out, &in, has_in, line, i, a)) {
        a++;
    }
    while (b >= a && !inner_px(&out, &in, has_in, line, i, b)) {
        b--;
    }
    *x0 = a;
    *x1 = b + 1 > a ? b + 1 : a;
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
    // the corners' row extents where their tables are (cov_table)
    int rows = s_cov != NULL;
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
        // Each edge zone: the columns the shape does not reach are skipped,
        // and those the fill covers whole join the middle's run; only the
        // arc's and the ring's own pixels go one at a time.
        int jl0 = 0, jl1 = L, jr0 = w - R, jr1 = w;
        int whole = 0;
        if (rows) {
            int z, a, zi, ai;
            cov_side(&s.out, 0, i, &z, &a);
            jl0 = imin(z, L);
            cov_side(&s.out, 1, i, &zi, &ai);
            jr1 = imax(w - zi, w - R);
            whole = s.fill && pr.bits == 0 && mask != MOY_SHAPE_FRINGE
                    && !(s.line > 0 && (!s.has_in || i < s.line || i >= h - s.line));
            if (whole) {
                int al = a, ar = ai;
                if (s.line > 0) {
                    int z2, a2;
                    cov_side(&s.in, 0, i - s.line, &z2, &a2);
                    al = imax(al, s.line + a2);
                    cov_side(&s.in, 1, i - s.line, &z2, &a2);
                    ar = imax(ar, s.line + a2);
                }
                jl1 = imax(jl0, imin(al, L));
                jr0 = imin(jr1, imax(w - ar, w - R));
            }
        }
        for (int j = jl0; j < jl1; j++) {
            n += rr_px(&s, &g, mask, i, j, bx + j, row, &pr);
        }
        for (int j = jr0; j < jr1; j++) {
            n += rr_px(&s, &g, mask, i, j, bx + j, row, &pr);
        }
        if (whole) {
            n += run(&g, Y, bx + jl1, bx + jr0, pr.c0);
            continue;
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
        T = (uint8_t *)SHAPE_ALLOC((size_t)(C * C + 1) / 2);
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

// A long run's tables, each channel's 32 or 64 results of over() at one
// weight `a` of canonical `col`, as words the buffer holds: kept for the
// next run at the same colour and weight (a scrim's every row).
static struct {
    uint16_t tr[32], tg[64], tb[32];
    int col, a, swapped;
} s_run = {{0}, {0}, {0}, -1, -1, -1};

static void run_tables(uint16_t col, int a) {
    if (s_run.col == col && s_run.a == a && s_run.swapped == s_swapped) {
        return;
    }
    int iw = 256 - a;
    int fr = ((col >> 11) & 31) * a + 128, fg = ((col >> 5) & 63) * a + 128,
        fb = (col & 31) * a + 128;
    for (int v = 0; v < 32; v++) {
        s_run.tr[v] = wire((uint16_t)(((fr + v * iw) >> 8) << 11));
        s_run.tb[v] = wire((uint16_t)((fb + v * iw) >> 8));
    }
    for (int v = 0; v < 64; v++) {
        s_run.tg[v] = wire((uint16_t)(((fg + v * iw) >> 8) << 5));
    }
    s_run.col = col;
    s_run.a = a;
    s_run.swapped = s_swapped;
}

// Pixels [X0, X1) of a row at one weight `a` of canonical `col`, as the
// per-pixel loop writes them (over, rounded). A long run blends through a
// table of each channel's 32 or 64 results, the same sums over() makes.
// Answers the pixels written.
static int shadow_run(uint16_t *row, int X0, int X1, uint16_t col, int a) {
    if (a == 0 || X1 <= X0) {
        return 0;
    }
    if (a >= 256) {
        mg_fill_run(row + X0, (size_t)(X1 - X0), wire(col));
        return X1 - X0;
    }
    if (X1 - X0 < 64) {
        uint16_t from = row[X0], to = wire(over(col, can(from), a));
        for (int X = X0; X < X1; X++) {
            if (row[X] != from) {
                from = row[X];
                to = wire(over(col, can(from), a));
            }
            row[X] = to;
        }
        return X1 - X0;
    }
    // Three lookups a pixel, into copies on the stack (internal RAM).
    uint16_t tr[32], tg[64], tb[32];
    run_tables(col, a);
    memcpy(tr, s_run.tr, sizeof(tr));
    memcpy(tg, s_run.tg, sizeof(tg));
    memcpy(tb, s_run.tb, sizeof(tb));
    uint16_t *p = row + X0, *e = row + X1;
    if (s_swapped) {
        // The words as the buffer holds them: each channel read straight off
        // the swapped word, the tables' results swapped already.
        for (; p < e; p++) {
            uint16_t w = *p;
            *p = (uint16_t)(tr[(w >> 3) & 31] | tg[((w & 7) << 3) | (w >> 13)]
                            | tb[(w >> 8) & 31]);
        }
        return X1 - X0;
    }
    for (; p < e; p++) {
        uint16_t c = *p;
        *p = (uint16_t)(tr[c >> 11] | tg[(c >> 5) & 63] | tb[c & 31]);
    }
    return X1 - X0;
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
        wt[k] = weight(k, alpha);
    }
    uint16_t col = can((uint16_t)s->col);
    int sx = x + s->dx - g.cam_x, sy = y + s->dy - g.cam_y;
    int n = 0;
    int Ya = imax(sy - b, g.y0), Yb = imin(sy + h + b, g.y1);
    int Xa = imax(sx - b, g.x0), Xb = imin(sx + w + b, g.x1);
    // Columns whose mask cell lies past its corner box take the row's one
    // weight (mask_at): [Ia, Ib) on the buffer.
    int C = m->C, Ia = imax(sx + C - b, Xa), Ib = imin(sx + w - (C - b), Xb);
    for (int Y = Ya; Y < Yb; Y++) {
        tgt_row(&g, Y);
        int i = Y - sy;
        int ii = imin(i, h - 1 - i) + b;
        uint16_t *row = g.px + (size_t)Y * (size_t)g.stride;
        int X = Xa;
        while (X < Xb) {
            if (X >= g.hx0 && X < g.hx1) {
                X = g.hx1;
                continue;
            }
            if (X >= Ia && X < Ib) {
                int stop = g.hx1 > g.hx0 && g.hx0 > X && g.hx0 < Ib ? g.hx0 : Ib;
                n += shadow_run(row, X, stop, col, wt[mask_at(m, ii, C)]);
                X = stop;
                continue;
            }
            int j = X - sx;
            int a = wt[mask_at(m, ii, imin(j, w - 1 - j) + b)];
            if (a != 0) {
                row[X] = wire(a >= 256 ? col : over(col, can(row[X]), a));
                n++;
            }
            X++;
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

int mg_cover(uint16_t *dst, size_t cap, int dw, int x, int y, int w, int h,
             const uint16_t *src, size_t scap, int sw, int sx, int sy, int cw, int ch,
             int r, int stepped, int cam_x, int cam_y, int cx0, int cy0, int cx1, int cy1) {
    moy_text_target_t t;
    tgt_t g;
    flat_target(&t, dst, cap, dw, cam_x, cam_y, cx0, cy0, cx1, cy1);
    if (w <= 0 || h <= 0 || src == NULL || sw <= 0 || cw <= 0 || ch <= 0 || sx < 0 || sy < 0
        || sx + cw > sw || cw > 32767 || ch > 32767 || !tgt_open(&t, NULL, &g)) {
        return 0;
    }
    if ((size_t)(sy + ch) * (size_t)sw > scap) {
        return 0;
    }
    int rr = clamp_r(r, w, h);
    int rad[4] = {rr, rr, rr, rr};
    corners_t c;
    corners_set(&c, w, h, rad, stepped);
    int bx = x - g.cam_x, by = y - g.cam_y;
    int X0 = imax(bx, g.x0), X1 = imin(bx + w, g.x1);
    int Y0 = imax(by, g.y0), Y1 = imin(by + h, g.y1);
    if (X1 <= X0 || Y1 <= Y0) {
        return 0;
    }
    uint32_t fx = ((uint32_t)cw << 16) / (uint32_t)w;
    uint32_t fy = ((uint32_t)ch << 16) / (uint32_t)h;
    uint32_t u0 = (uint32_t)(X0 - bx) * fx + (fx >> 1);
    int n = 0;
    for (int Y = Y0; Y < Y1; Y++) {
        int i = Y - by;
        const uint16_t *srow = src + (size_t)(sy + (int)(((uint32_t)i * fy + (fy >> 1)) >> 16))
                                         * (size_t)sw + sx;
        uint16_t *row = g.px + (size_t)Y * (size_t)g.stride;
        int L = 0, R = 0;
        if (i < rr || i >= h - rr) {
            L = R = rr;
        }
        int a = imax(X0, bx + L), b = imin(X1, bx + w - R);
        uint32_t u = u0;
        for (int X = X0; X < X1; X++, u += fx) {
            if (X >= a && X < b) {
                // the run between the corners: a straight sample, a copy
                // where the picture is at its own width
                if (fx == 0x10000u) {
                    memcpy(row + X, srow + (u >> 16), (size_t)(b - X) * sizeof(uint16_t));
                } else {
                    uint32_t v = u;
                    for (int Z = X; Z < b; Z++, v += fx) {
                        row[Z] = srow[v >> 16];
                    }
                }
                n += b - X;
                u += fx * (uint32_t)(b - X - 1);
                X = b - 1;
                continue;
            }
            int co = corners_cov(&c, i, X - bx);
            if (co == 0) {
                continue;
            }
            uint16_t p = srow[u >> 16];
            if (co < 15) {
                p = wire(mix3(can(row[X]), 15 - co, can(p), co, 0, 0));
                s_st[MOY_SHAPE_ST_BLENDS]++;
            }
            row[X] = p;
            n++;
        }
    }
    return n;
}

int mg_tint(uint16_t *dst, size_t dcap, const uint16_t *src, size_t scap, int sw, int sx,
            int sy, int w, int h, uint16_t col, int alpha) {
    if (dst == NULL || src == NULL || w <= 0 || h <= 0 || sw <= 0 || sx < 0 || sy < 0
        || sx + w > sw || (size_t)w * (size_t)h > dcap
        || (size_t)(sy + h) * (size_t)sw > scap) {
        return 0;
    }
    int wt = (imax(0, imin(alpha, 255)) * 256 + 127) / 255;
    uint16_t c = can(col);
    // over(c, b, wt) a channel at a time, through each channel's 32 or 64
    // results (shadow_run's tables): the same sums, a lookup a channel.
    int iw = 256 - wt;
    int fr = ((c >> 11) & 31) * wt + 128, fg = ((c >> 5) & 63) * wt + 128,
        fb = (c & 31) * wt + 128;
    uint16_t tr[32], tg[64], tb[32];
    for (int v = 0; v < 32; v++) {
        tr[v] = (uint16_t)(((fr + v * iw) >> 8) << 11);
        tb[v] = (uint16_t)((fb + v * iw) >> 8);
    }
    for (int v = 0; v < 64; v++) {
        tg[v] = (uint16_t)(((fg + v * iw) >> 8) << 5);
    }
    for (int i = 0; i < h; i++) {
        const uint16_t *s = src + (size_t)(sy + i) * (size_t)sw + sx;
        uint16_t *d = dst + (size_t)i * (size_t)w;
        for (int j = 0; j < w; j++) {
            uint16_t b = can(s[j]);
            d[j] = wire((uint16_t)(tr[b >> 11] | tg[(b >> 5) & 63] | tb[b & 31]));
        }
    }
    return w * h;
}
