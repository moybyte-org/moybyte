// moy_backdrop: a theme's backdrop rasterised (moy_backdrop.h).
//
// Integer only, so the host, the boards and the browser draw the same pixels.
// Geometry is in FX units (1/256 px). A shape's coverage of a pixel is its
// exact horizontal coverage averaged over four sub-rows (a wave's: its exact
// vertical coverage over four sub-columns), quantised to 16 levels. It runs once per theme load and mode switch, so it
// takes the build's optimisation, not the compositor's -O3.

#include <string.h>

#include "moy_backdrop.h"

#define FX 256
#define STRIP 64      // a wave's columns evaluated at once
#define SEG 24        // a wave's samples per segment between two points

static const uint8_t BAYER[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};
static const int8_t AT[MG_BD_KINDS] = {0, 2, 3, 4, 5, 5, -1};

// sin(d) for d in 0..90 degrees, x16384.
static const int16_t SIN[91] = {
    0, 286, 572, 857, 1143, 1428, 1713, 1997, 2280, 2563, 2845, 3126, 3406, 3686, 3964,
    4240, 4516, 4790, 5063, 5334, 5604, 5872, 6138, 6402, 6664, 6924, 7182, 7438, 7692,
    7943, 8192, 8438, 8682, 8923, 9162, 9397, 9630, 9860, 10087, 10311, 10531, 10749,
    10963, 11174, 11381, 11585, 11786, 11982, 12176, 12365, 12551, 12733, 12911, 13085,
    13255, 13421, 13583, 13741, 13894, 14044, 14189, 14330, 14466, 14598, 14726, 14849,
    14968, 15082, 15191, 15296, 15396, 15491, 15582, 15668, 15749, 15826, 15897, 15964,
    16026, 16083, 16135, 16182, 16225, 16262, 16294, 16322, 16344, 16362, 16374, 16382,
    16384,
};

typedef struct {
    int kind, mode, steps, alpha, ns, nat;
    int32_t t[MG_BD_STOPS];               // 0..65536 of the shape's rows
    uint8_t rgb[MG_BD_STOPS][3];          // each stop as its RGB565 shows it
    const uint8_t *at;
} shape_t;

typedef struct {
    uint16_t *px;
    int stride, ox, oy, w, h, swap;
    int x0, y0, x1, y1;                   // the clip, in surface coordinates
} dst_t;

typedef struct {
    uint8_t a[3], b[3];
    int f, dither;
} rowp_t;

static int rd16(const uint8_t *p) {
    return (int16_t)(p[0] | p[1] << 8);
}

static uint8_t q5(int v) {
    v = v < 0 ? 0 : v > 255 ? 255 : v;
    return (uint8_t)((v >> 3) << 3 | v >> 5);
}

static uint8_t q6(int v) {
    v = v < 0 ? 0 : v > 255 ? 255 : v;
    return (uint8_t)((v >> 2) << 2 | v >> 6);
}

// The record at p (n bytes left) into *s where s is given: its length, or 0
// where it is malformed.
static size_t bd_read(const uint8_t *p, size_t n, shape_t *s) {
    if (n < 6) {
        return 0;
    }
    int kind = p[0], mode = p[1], steps = p[2], ns = p[4], nat = p[5];
    if (kind >= MG_BD_KINDS || mode > 2 || ns < 1 || ns > MG_BD_STOPS
        || (mode == 1 && (steps < 2 || steps > 64))
        || (AT[kind] < 0 ? nat < 4 || nat > 2 * MG_BD_POINTS || (nat & 1) : nat != AT[kind])) {
        return 0;
    }
    size_t len = 6 + 5 * (size_t)ns + 2 * (size_t)nat;
    if (len > n) {
        return 0;
    }
    if (s != NULL) {
        s->kind = kind;
        s->mode = mode;
        s->steps = steps;
        s->alpha = p[3];
        s->ns = ns;
        s->nat = nat;
        for (int i = 0; i < ns; i++) {
            const uint8_t *c = p + 6 + 5 * i;
            int t = rd16(c);
            t = t < 0 ? 0 : t > 1000 ? 1000 : t;
            s->t[i] = (int32_t)((int64_t)t * 65536 / 1000);
            s->rgb[i][0] = q5(c[2]);
            s->rgb[i][1] = q6(c[3]);
            s->rgb[i][2] = q5(c[4]);
        }
        s->at = p + 6 + 5 * ns;
    }
    return len;
}

// -- paint and plot ---------------------------------------------------------------

// Row y's paint over the shape's rows gy0 .. gy0 + gh.
static void row_paint(const shape_t *s, int y, int gy0, int gh, rowp_t *rp) {
    rp->dither = 0;
    if (s->ns == 1) {
        memcpy(rp->a, s->rgb[0], 3);
        return;
    }
    int64_t T = (int64_t)(y - gy0) * 65536 / (gh > 1 ? gh - 1 : 1);
    T = T < 0 ? 0 : T > 65536 ? 65536 : T;
    if (s->mode == 1) {
        int n = s->steps;
        int64_t k = (T * n) >> 16;
        k = k > n - 1 ? n - 1 : k;
        T = k * 65536 / (n - 1);
    }
    int i = 0;
    while (i < s->ns - 1 && T > s->t[i + 1]) {
        i++;
    }
    if (i == s->ns - 1) {
        memcpy(rp->a, s->rgb[i], 3);
        return;
    }
    int64_t span = s->t[i + 1] - s->t[i];
    int64_t F = span ? (T - s->t[i]) * 65536 / span : 0;
    F = F < 0 ? 0 : F > 65536 ? 65536 : F;
    const uint8_t *a = s->rgb[i], *b = s->rgb[i + 1];
    if (s->mode == 2) {
        memcpy(rp->a, a, 3);
        memcpy(rp->b, b, 3);
        rp->f = (int)((F * 255 + 32768) >> 16);
        rp->dither = 1;
        return;
    }
    for (int k = 0; k < 3; k++) {
        int v = a[k] + (int)(((int64_t)(b[k] - a[k]) * F + 32768) >> 16);
        rp->a[k] = k == 1 ? q6(v) : q5(v);
    }
}

static uint16_t wire(const dst_t *d, uint16_t w) {
    return d->swap ? (uint16_t)(w << 8 | w >> 8) : w;
}

static uint16_t pack(const uint8_t *c) {
    return (uint16_t)((c[0] >> 3) << 11 | (c[1] >> 2) << 5 | c[2] >> 3);
}

// Pixel (x, y) of the surface under coverage m (0..255, its alpha applied).
static void plot(const dst_t *d, const rowp_t *rp, int x, int y, int m) {
    if (m <= 0) {
        return;
    }
    const uint8_t *c = rp->a;
    if (rp->dither && rp->f > BAYER[(y & 3) * 4 + (x & 3)] * 16 + 8) {
        c = rp->b;
    }
    uint16_t *p = d->px + (size_t)(d->oy + y) * (size_t)d->stride + (size_t)(d->ox + x);
    if (m >= 255) {
        *p = wire(d, pack(c));
        return;
    }
    uint16_t v = wire(d, *p);
    uint8_t o[3];
    o[0] = q5((v >> 11) << 3);
    o[1] = q6(((v >> 5) & 63) << 2);
    o[2] = q5((v & 31) << 3);
    for (int k = 0; k < 3; k++) {
        o[k] = (uint8_t)((c[k] * m + o[k] * (255 - m) + 127) / 255);
    }
    *p = wire(d, pack(o));
}

// A run of full coverage (alpha applied) on row y.
static void run(const dst_t *d, const rowp_t *rp, int xa, int xb, int y, int alpha) {
    if (alpha >= 255 && !rp->dither) {
        uint16_t w = wire(d, pack(rp->a));
        uint16_t *p = d->px + (size_t)(d->oy + y) * (size_t)d->stride + (size_t)(d->ox + xa);
        for (int x = xa; x < xb; x++) {
            *p++ = w;
        }
        return;
    }
    for (int x = xa; x < xb; x++) {
        plot(d, rp, x, y, alpha);
    }
}

// 0..256 coverage to 16 levels, then the alpha.
static int level(int cov, int alpha) {
    int a = (cov * 255 + 128) >> 8;
    int m = ((a * 15 + 127) / 255) * 17;
    return (m * alpha + 127) / 255;
}

// -- geometry ---------------------------------------------------------------------

static uint32_t isqrt64(uint64_t v) {
    uint64_t r = 0, b = (uint64_t)1 << 62;
    while (b > v) {
        b >>= 2;
    }
    while (b) {
        if (v >= r + b) {
            v -= r + b;
            r = (r >> 1) + b;
        } else {
            r >>= 1;
        }
        b >>= 2;
    }
    return (uint32_t)r;
}

static int32_t floor_fx(int32_t v) {
    return v >> 8;
}

static int32_t ceil_fx(int32_t v) {
    return -((-v) >> 8);
}

// sin and cos of `deg`, x16384.
static void sincos16(int deg, int32_t *s, int32_t *c) {
    deg %= 360;
    deg += deg < 0 ? 360 : 0;
    int q = deg / 90, r = deg % 90;
    int32_t sn = SIN[r], cs = SIN[90 - r];
    static const int8_t ss[4][2] = {{1, 1}, {1, -1}, {-1, -1}, {-1, 1}};
    if (q & 1) {
        int32_t t = sn;
        sn = cs;
        cs = t;
    }
    *s = ss[q][0] * sn;
    *c = ss[q][1] * cs;
}

typedef struct {
    int kind;
    int32_t cx, cy, rx, ry, x0, y0, x1, y1, r;   // FX
    int32_t ax, ay, bx, by;                       // a pie's two edges, x16384
    int pie;                                      // 0 none, 1 the wedge, 2 all but the wedge, 3 the circle
} geo_t;

// The interval of the line y = cy + dy where cross((vx, vy), p) >= 0 (sign
// `s` 1) or <= 0 (s -1), within [*l, *r).
static void half(const geo_t *g, int32_t dy, int32_t vx, int32_t vy, int s, int32_t *l, int32_t *r) {
    vx *= s;
    vy *= s;
    if (vy == 0) {
        if ((int64_t)vx * dy < 0) {
            *r = *l;
        }
        return;
    }
    int32_t b = g->cx + (int32_t)((int64_t)vx * dy / vy);
    if (vy > 0) {
        *r = *r < b ? *r : b;
    } else {
        *l = *l > b ? *l : b;
    }
}

// The shape's intervals [l, r) on the line y = yc (FX): their count, 0..2.
static int spans(const geo_t *g, int32_t yc, int32_t *l, int32_t *r) {
    switch (g->kind) {
        case MG_BD_FILL:
        case MG_BD_BAND:
            if (yc < g->y0 || yc >= g->y1) {
                return 0;
            }
            l[0] = g->x0;
            r[0] = g->x1;
            return 1;
        case MG_BD_RRECT: {
            if (yc < g->y0 || yc >= g->y1) {
                return 0;
            }
            int32_t dy = 0, in = 0;
            if (yc < g->y0 + g->r) {
                dy = g->y0 + g->r - yc;
            } else if (yc > g->y1 - g->r) {
                dy = yc - (g->y1 - g->r);
            }
            if (dy) {
                in = g->r - (int32_t)isqrt64((uint64_t)((int64_t)g->r * g->r - (int64_t)dy * dy));
            }
            l[0] = g->x0 + in;
            r[0] = g->x1 - in;
            return l[0] < r[0];
        }
        default: {
            int32_t dy = yc - g->cy;
            if (g->ry <= 0 || g->rx <= 0 || dy <= -g->ry || dy >= g->ry) {
                return 0;
            }
            uint32_t s = isqrt64((uint64_t)((int64_t)g->ry * g->ry - (int64_t)dy * dy));
            int32_t hw = (int32_t)((int64_t)g->rx * s / g->ry);
            int32_t cl = g->cx - hw, cr = g->cx + hw;
            if (g->kind != MG_BD_PIE || g->pie == 3) {
                l[0] = cl;
                r[0] = cr;
                return cl < cr;
            }
            if (g->pie == 0) {
                return 0;
            }
            int32_t wl = cl, wr = cr;
            if (g->pie == 1) {
                half(g, dy, g->ax, g->ay, 1, &wl, &wr);
                half(g, dy, g->bx, g->by, -1, &wl, &wr);
                l[0] = wl;
                r[0] = wr;
                return wl < wr;
            }
            half(g, dy, g->bx, g->by, 1, &wl, &wr);
            half(g, dy, g->ax, g->ay, -1, &wl, &wr);
            if (wl >= wr) {
                l[0] = cl;
                r[0] = cr;
                return cl < cr;
            }
            int k = 0;
            if (cl < wl) {
                l[k] = cl;
                r[k++] = wl;
            }
            if (wr < cr) {
                l[k] = wr;
                r[k++] = cr;
            }
            return k;
        }
    }
}

static int32_t over(int32_t a0, int32_t a1, int32_t b0, int32_t b1) {
    int32_t lo = a0 > b0 ? a0 : b0, hi = a1 < b1 ? a1 : b1;
    return hi > lo ? hi - lo : 0;
}

// Rows ya .. yb of a span shape.
static void draw_spans(const dst_t *d, const shape_t *s, const geo_t *g,
                       int gy0, int gh, int ya, int yb) {
    ya = ya > d->y0 ? ya : d->y0;
    yb = yb < d->y1 ? yb : d->y1;
    for (int y = ya; y < yb; y++) {
        int32_t L[4][2], R[4][2];
        int cnt[4], one = 1;
        int32_t minl = INT32_MAX, maxr = INT32_MIN, inl = INT32_MIN, inr = INT32_MAX;
        for (int k = 0; k < 4; k++) {
            cnt[k] = spans(g, y * FX + (2 * k + 1) * FX / 8, L[k], R[k]);
            for (int i = 0; i < cnt[k]; i++) {
                minl = L[k][i] < minl ? L[k][i] : minl;
                maxr = R[k][i] > maxr ? R[k][i] : maxr;
            }
            if (cnt[k] == 1) {
                inl = L[k][0] > inl ? L[k][0] : inl;
                inr = R[k][0] < inr ? R[k][0] : inr;
            } else {
                one = 0;
            }
        }
        if (minl >= maxr) {
            continue;
        }
        int32_t xa = floor_fx(minl), xb = ceil_fx(maxr);
        xa = xa > d->x0 ? xa : d->x0;
        xb = xb < d->x1 ? xb : d->x1;
        int32_t il = xb, ir = xb;
        if (one && inl < inr) {
            il = ceil_fx(inl);
            ir = floor_fx(inr);
            il = il > xa ? il : xa;
            ir = ir < xb ? ir : xb;
            if (il >= ir) {
                il = ir = xb;
            }
        }
        rowp_t rp;
        row_paint(s, y, gy0, gh, &rp);
        for (int32_t x = xa; x < xb;) {
            if (x == il) {
                run(d, &rp, il, ir, y, s->alpha);
                x = ir;
                continue;
            }
            int32_t cov = 0, p0 = x * FX, p1 = p0 + FX;
            for (int k = 0; k < 4; k++) {
                for (int i = 0; i < cnt[k]; i++) {
                    cov += over(p0, p1, L[k][i], R[k][i]);
                }
            }
            plot(d, &rp, x, y, level(cov / 4, s->alpha));
            x++;
        }
    }
}

// -- the wave ---------------------------------------------------------------------

// The curve's y at x (FX) along the polyline px/py of n points, from the
// segment at *cur.
static int32_t curve_y(const int32_t *px, const int32_t *py, int n, int32_t x, int *cur) {
    if (x <= px[0]) {
        return py[0];
    }
    if (x >= px[n - 1]) {
        return py[n - 1];
    }
    int j = *cur;
    if (j >= n - 1 || px[j] > x) {
        j = 0;
    }
    while (j < n - 2 && px[j + 1] <= x) {
        j++;
    }
    *cur = j;
    int32_t dx = px[j + 1] - px[j];
    if (dx <= 0) {
        return py[j];
    }
    return py[j] + (int32_t)((int64_t)(py[j + 1] - py[j]) * (x - px[j]) / dx);
}

static void draw_wave(const dst_t *d, const shape_t *s) {
    int np = s->nat / 2;
    int32_t qx[MG_BD_POINTS + 2] = {0}, qy[MG_BD_POINTS + 2] = {0};
    for (int i = 0; i < np; i++) {
        qx[i + 1] = (int32_t)((int64_t)rd16(s->at + 4 * i) * d->w * FX / 1000);
        qy[i + 1] = (int32_t)((int64_t)rd16(s->at + 4 * i + 2) * d->h * FX / 1000);
    }
    qx[0] = qx[1];
    qy[0] = qy[1];
    qx[np + 1] = qx[np];
    qy[np + 1] = qy[np];
    // Catmull-Rom through the points, SEG samples a segment.
    int32_t px[(MG_BD_POINTS - 1) * SEG + 1], py[(MG_BD_POINTS - 1) * SEG + 1];
    int n = 0;
    const int64_t N = SEG, N2 = N * N, N3 = N2 * N;
    for (int i = 1; i < np; i++) {
        for (int k = 0; k < SEG; k++) {
            for (int c = 0; c < 2; c++) {
                const int32_t *q = c ? qy : qx;
                int64_t p0 = q[i - 1], p1 = q[i], p2 = q[i + 1], p3 = q[i + 2];
                int64_t v = 2 * p1 * N3 + (p2 - p0) * k * N2
                            + (2 * p0 - 5 * p1 + 4 * p2 - p3) * k * k * N
                            + (3 * p1 - p0 - 3 * p2 + p3) * k * k * k;
                (c ? py : px)[n] = (int32_t)(v / (2 * N3));
            }
            n++;
        }
    }
    px[n] = qx[np];
    py[n] = qy[np];
    n++;
    int32_t top = py[0];
    for (int i = 1; i < n; i++) {
        top = py[i] < top ? py[i] : top;
    }
    int gy0 = top / FX, gh = d->h - gy0;
    int cur = 0;
    for (int xs = d->x0; xs < d->x1; xs += STRIP) {
        int xe = xs + STRIP < d->x1 ? xs + STRIP : d->x1;
        int32_t yt[STRIP][4];
        int full[STRIP];
        int32_t ymin = INT32_MAX;
        int fmax = 0;
        for (int x = xs; x < xe; x++) {
            int32_t hi = INT32_MIN;
            for (int j = 0; j < 4; j++) {
                int32_t v = curve_y(px, py, n, x * FX + (2 * j + 1) * FX / 8, &cur);
                yt[x - xs][j] = v;
                ymin = v < ymin ? v : ymin;
                hi = v > hi ? v : hi;
            }
            full[x - xs] = ceil_fx(hi);
            fmax = full[x - xs] > fmax ? full[x - xs] : fmax;
        }
        int ya = floor_fx(ymin);
        ya = ya > d->y0 ? ya : d->y0;
        for (int y = ya; y < d->y1; y++) {
            rowp_t rp;
            row_paint(s, y, gy0, gh, &rp);
            if (y >= fmax) {
                run(d, &rp, xs, xe, y, s->alpha);
                continue;
            }
            for (int x = xs; x < xe; x++) {
                if (y >= full[x - xs]) {
                    plot(d, &rp, x, y, s->alpha);
                    continue;
                }
                int32_t cov = 0;
                for (int j = 0; j < 4; j++) {
                    int32_t c = (y + 1) * FX - yt[x - xs][j];
                    cov += c < 0 ? 0 : c > FX ? FX : c;
                }
                plot(d, &rp, x, y, level(cov / 4, s->alpha));
            }
        }
    }
}

// -- one shape --------------------------------------------------------------------

static int32_t fx_of(int v, int dim) {
    return (int32_t)((int64_t)v * dim * FX / 1000);
}

// round(v * dim / 1000), half up.
static int px_of(int v, int dim) {
    int64_t a = (int64_t)v * dim * 2 + 1000, b = 2000;
    return (int)(a >= 0 ? a / b : -((-a + b - 1) / b));
}

static void draw_shape(const dst_t *d, const shape_t *s) {
    if (s->kind == MG_BD_WAVE) {
        draw_wave(d, s);
        return;
    }
    geo_t g;
    memset(&g, 0, sizeof(g));
    g.kind = s->kind;
    int a[5] = {0, 0, 0, 0, 0};
    for (int i = 0; i < s->nat; i++) {
        a[i] = rd16(s->at + 2 * i);
    }
    int gy0 = 0, gh = d->h, ya = 0, yb = d->h;
    switch (s->kind) {
        case MG_BD_FILL:
            g.x1 = d->w * FX;
            g.y1 = d->h * FX;
            break;
        case MG_BD_BAND:
            gy0 = px_of(a[0], d->h);
            gh = px_of(a[1], d->h) - gy0;
            g.x1 = d->w * FX;
            g.y0 = gy0 * FX;
            g.y1 = (gy0 + gh) * FX;
            ya = gy0;
            yb = gy0 + gh;
            break;
        case MG_BD_RRECT: {
            int x0 = px_of(a[0], d->w), w = px_of(a[2], d->w);
            gy0 = px_of(a[1], d->h);
            gh = px_of(a[3], d->h);
            g.x0 = x0 * FX;
            g.x1 = (x0 + w) * FX;
            g.y0 = gy0 * FX;
            g.y1 = (gy0 + gh) * FX;
            int32_t r = fx_of(a[4], d->h), m = (w < gh ? w : gh) * FX / 2;
            g.r = r < 0 ? 0 : r > m ? m : r;
            ya = gy0;
            yb = gy0 + gh;
            break;
        }
        default: {
            g.cx = fx_of(a[0], d->w);
            g.cy = fx_of(a[1], d->h);
            g.rx = s->kind == MG_BD_ELLIPSE ? fx_of(a[2], d->w) : fx_of(a[2], d->h);
            g.ry = s->kind == MG_BD_ELLIPSE ? fx_of(a[3], d->h) : fx_of(a[2], d->h);
            if (g.rx <= 0 || g.ry <= 0) {
                return;
            }
            gy0 = (g.cy - g.ry) / FX;
            gh = 2 * g.ry / FX + 2;
            ya = floor_fx(g.cy - g.ry);
            yb = ceil_fx(g.cy + g.ry);
            if (s->kind == MG_BD_PIE) {
                int sweep = a[4] - a[3];
                while (sweep < 0) {
                    sweep += 360;
                }
                g.pie = sweep >= 360 ? 3 : sweep == 0 ? 0 : sweep <= 180 ? 1 : 2;
                sincos16(a[3], &g.ay, &g.ax);
                sincos16(a[4], &g.by, &g.bx);
            }
            break;
        }
    }
    draw_spans(d, s, &g, gy0, gh, ya, yb);
}

int mg_backdrop(uint16_t *px, size_t cap, int stride, int ox, int oy, int w, int h,
                const uint8_t *rec, size_t n, uint16_t bg, int swap,
                int cx0, int cy0, int cx1, int cy1) {
    if (px == NULL || stride <= 0 || w <= 0 || h <= 0) {
        return -1;
    }
    int rows = (int)(cap / (size_t)stride);
    dst_t d = {px, stride, ox, oy, w, h, swap, 0, 0, w, h};
    d.x0 = cx0 - ox > 0 ? cx0 - ox : 0;
    d.y0 = cy0 - oy > 0 ? cy0 - oy : 0;
    d.x1 = cx1 - ox < w ? cx1 - ox : w;
    d.y1 = cy1 - oy < h ? cy1 - oy : h;
    d.x0 = d.x0 > -ox ? d.x0 : -ox;
    d.y0 = d.y0 > -oy ? d.y0 : -oy;
    d.x1 = d.x1 < stride - ox ? d.x1 : stride - ox;
    d.y1 = d.y1 < rows - oy ? d.y1 : rows - oy;
    if (d.x0 >= d.x1 || d.y0 >= d.y1) {
        return 0;
    }
    for (int y = d.y0; y < d.y1; y++) {
        uint16_t *p = px + (size_t)(oy + y) * (size_t)stride + (size_t)(ox + d.x0);
        for (int x = d.x0; x < d.x1; x++) {
            *p++ = bg;
        }
    }
    int count = 0;
    for (size_t k = 0; k < n; count++) {
        size_t len = bd_read(rec + k, n - k, NULL);
        if (len == 0 || count >= MG_BD_SHAPES) {
            return -1;
        }
        k += len;
    }
    for (size_t k = 0; k < n;) {
        shape_t s;
        k += bd_read(rec + k, n - k, &s);
        draw_shape(&d, &s);
    }
    return count;
}
