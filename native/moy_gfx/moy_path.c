/* Filled paths and the outline faces: moy_path.h says what is here. Integer
 * throughout. The -O3 pragma comes before the includes for
 * moy_gfx_kernels.c's reason. */

#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O3")
#endif

#include "moy_path.h"

#include <stdlib.h>
#include <string.h>

#include "moy_path_faces.h"

// The scratch (a row's coverage and a glyph's edges) is PSRAM where the
// board has it, as moy_shape's caches are: kept, grown on need.
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define PATH_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#define PATH_FREE(p) heap_caps_free(p)
#else
#define PATH_ALLOC(n) malloc(n)
#define PATH_FREE(p) free(p)
#endif

#define ONE 64                  // 26.6: a pixel
#define FULL (2 * ONE * ONE)    // a covered pixel's area, doubled (the half is kept whole)
#define CURVE_STEPS 16          // the most lines a quadratic curve flattens into

static int s_swapped = 1;

void moy_path_wire_swapped(int swapped) {
    s_swapped = swapped != 0;
}

static inline uint16_t can(uint16_t w) {
    return s_swapped ? (uint16_t)((w << 8) | (w >> 8)) : w;
}

// -- the scratch ------------------------------------------------------------------

typedef struct {
    int32_t x0, y0, x1, y1;     // an edge, 26.6, y0 < y1
    int32_t dir;                // +1 down the path, -1 up it
} edge_t;

static edge_t *s_edges;
static int s_edge_cap;
static int32_t *s_acc;
static int s_acc_cap;

static int edges_room(int n) {
    if (n <= s_edge_cap) {
        return 1;
    }
    int cap = n < 256 ? 256 : n + n / 2;
    edge_t *e = PATH_ALLOC((size_t)cap * sizeof(edge_t));
    if (e == NULL) {
        return 0;
    }
    if (s_edges != NULL) {
        memcpy(e, s_edges, (size_t)s_edge_cap * sizeof(edge_t));
        PATH_FREE(s_edges);
    }
    s_edges = e;
    s_edge_cap = cap;
    return 1;
}

static int acc_room(int n) {
    if (n <= s_acc_cap) {
        return 1;
    }
    int cap = n < 512 ? 512 : n;
    int32_t *a = PATH_ALLOC((size_t)cap * sizeof(int32_t));
    if (a == NULL) {
        return 0;
    }
    if (s_acc != NULL) {
        PATH_FREE(s_acc);
    }
    s_acc = a;
    s_acc_cap = cap;
    return 1;
}

// One edge from (x0, y0) to (x1, y1); a level one adds nothing. 0 when the
// scratch could not grow.
static int edge_add(int *n, int32_t x0, int32_t y0, int32_t x1, int32_t y1) {
    if (y0 == y1) {
        return 1;
    }
    if (!edges_room(*n + 1)) {
        return 0;
    }
    edge_t *e = &s_edges[(*n)++];
    if (y0 < y1) {
        e->x0 = x0; e->y0 = y0; e->x1 = x1; e->y1 = y1; e->dir = 1;
    } else {
        e->x0 = x1; e->y0 = y1; e->x1 = x0; e->y1 = y0; e->dir = -1;
    }
    return 1;
}

// -- coverage ---------------------------------------------------------------------
//
// An edge crossing a row covers, to its right, the part of each pixel the
// edge leaves there. With the edge's x uniform over [xa, xb] down the row,
// the area a column X (26.6) has to its left of the edge is dy * phi(X),
// phi the expected positive part of X - x: 0 left of xa, (X - xa)^2 / 2L
// across it, X - (xa + xb) / 2 right of it. A pixel's share is the
// difference at its two sides, and the accumulator holds each pixel's share
// less its left neighbour's, so the sum across the row is the coverage.
// phi is kept doubled: no half is ever rounded.

static inline int64_t phi2(int64_t X, int64_t xa, int64_t xb) {
    if (X <= xa) {
        return 0;
    }
    if (X >= xb) {
        return 2 * X - xa - xb;
    }
    int64_t d = X - xa;
    return d * d / (xb - xa);
}

static inline int64_t x_at(const edge_t *e, int64_t y) {
    return e->x0 + (int64_t)(e->x1 - e->x0) * (y - e->y0) / (e->y1 - e->y0);
}

// The edges into the rows [by0, by1) over columns [bx0, bx1) of the target,
// blended in `col` (a wire word). The pixels touched.
static int raster(uint16_t *dst, size_t cap, int dw, int n, int bx0, int by0, int bx1, int by1,
                  uint16_t col) {
    int w = bx1 - bx0;
    if (w <= 0 || by1 <= by0 || n <= 0) {
        return 0;
    }
    if (!acc_room(w + 2)) {
        return -1;
    }
    int touched = 0;
    uint16_t cc = can(col);         // the colour's channels, canonical
    int cr = cc >> 11, cg = (cc >> 5) & 63, cb = cc & 31;
    for (int ry = by0; ry < by1; ry++) {
        int64_t top = (int64_t)ry * ONE, bot = top + ONE;
        int any = 0;
        int lo = w, hi = 0;
        for (int i = 0; i < n; i++) {
            const edge_t *e = &s_edges[i];
            if (e->y1 <= top || e->y0 >= bot) {
                continue;
            }
            if (!any) {
                memset(s_acc, 0, (size_t)(w + 2) * sizeof(int32_t));
                any = 1;
            }
            int64_t ya = e->y0 > top ? e->y0 : top;
            int64_t yb = e->y1 < bot ? e->y1 : bot;
            int64_t d = (yb - ya) * e->dir;
            int64_t xa = x_at(e, ya), xb = x_at(e, yb);
            if (xa > xb) {
                int64_t t = xa; xa = xb; xb = t;
            }
            int64_t c0 = xa >> 6, c1 = (xb >> 6) + 1;
            int64_t prev = 0;
            for (int64_t c = c0; c <= c1; c++) {
                int64_t cov = d * (phi2((c + 1) * ONE, xa, xb) - phi2(c * ONE, xa, xb));
                int64_t at = c - bx0;
                if (at >= w) {
                    break;
                }
                if (at < 0) {
                    at = 0;
                }
                s_acc[at] += (int32_t)(cov - prev);
                if ((int)at < lo) {
                    lo = (int)at;
                }
                if ((int)at > hi) {
                    hi = (int)at;
                }
                prev = cov;
            }
        }
        if (!any) {
            continue;
        }
        int32_t sum = 0;
        size_t row = (size_t)ry * (size_t)dw;
        for (int x = lo; x < w; x++) {
            sum += s_acc[x];
            if (x > hi && sum == 0) {
                break;
            }
            int32_t a = sum < 0 ? -sum : sum;
            if (a == 0) {
                continue;
            }
            size_t at = row + (size_t)(bx0 + x);
            if (at >= cap) {
                break;
            }
            touched++;
            if (a >= FULL) {
                dst[at] = col;
                continue;
            }
            int al = (int)((a * 256) / FULL);       // 0..255
            uint16_t p = can(dst[at]);
            int pr = p >> 11, pg = (p >> 5) & 63, pb = p & 31;
            pr += ((cr - pr) * al + 128) >> 8;
            pg += ((cg - pg) * al + 128) >> 8;
            pb += ((cb - pb) * al + 128) >> 8;
            dst[at] = can((uint16_t)((pr << 11) | (pg << 5) | pb));
        }
    }
    return touched;
}

// The rows and columns the edges reach, inside the clip and the buffer.
static int bounds(int n, int dw, size_t cap, int cx0, int cy0, int cx1, int cy1, int *bx0,
                  int *by0, int *bx1, int *by1) {
    if (n <= 0 || dw <= 0) {
        return 0;
    }
    int32_t xmin = s_edges[0].x0, xmax = xmin, ymin = s_edges[0].y0, ymax = s_edges[0].y1;
    for (int i = 0; i < n; i++) {
        const edge_t *e = &s_edges[i];
        int32_t a = e->x0 < e->x1 ? e->x0 : e->x1, b = e->x0 < e->x1 ? e->x1 : e->x0;
        xmin = a < xmin ? a : xmin;
        xmax = b > xmax ? b : xmax;
        ymin = e->y0 < ymin ? e->y0 : ymin;
        ymax = e->y1 > ymax ? e->y1 : ymax;
    }
    int rows = (int)(cap / (size_t)dw);
    int x0 = (int)(xmin >> 6), x1 = (int)((xmax + ONE - 1) >> 6) + 1;
    int y0 = (int)(ymin >> 6), y1 = (int)((ymax + ONE - 1) >> 6);
    (void)x0;
    // the coverage starts at the clip's left: what lies left of it sums in
    x0 = cx0 > 0 ? cx0 : 0;
    if (x1 > cx1) {
        x1 = cx1;
    }
    if (x1 > dw) {
        x1 = dw;
    }
    if (y0 < cy0) {
        y0 = cy0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (y1 > cy1) {
        y1 = cy1;
    }
    if (y1 > rows) {
        y1 = rows;
    }
    *bx0 = x0; *bx1 = x1; *by0 = y0; *by1 = y1;
    return x1 > x0 && y1 > y0;
}

int mg_path_fill(uint16_t *dst, size_t cap, int dw, const int32_t *pts, int npts,
                 const int32_t *ends, int nends, int col, int cam_x, int cam_y,
                 int cx0, int cy0, int cx1, int cy1) {
    int n = 0;
    int start = 0;
    int32_t ox = (int32_t)cam_x * ONE, oy = (int32_t)cam_y * ONE;
    for (int c = 0; c < nends; c++) {
        int end = (int)ends[c];
        if (end < start || end > npts) {
            return -1;
        }
        for (int i = start; i < end; i++) {
            int j = i + 1 < end ? i + 1 : start;
            if (!edge_add(&n, pts[2 * i] - ox, pts[2 * i + 1] - oy, pts[2 * j] - ox,
                          pts[2 * j + 1] - oy)) {
                return -1;
            }
        }
        start = end;
    }
    int bx0, by0, bx1, by1;
    if (!bounds(n, dw, cap, cx0, cy0, cx1, cy1, &bx0, &by0, &bx1, &by1)) {
        return 0;
    }
    return raster(dst, cap, dw, n, bx0, by0, bx1, by1, (uint16_t)col);
}

// -- the faces --------------------------------------------------------------------

static const moy_path_face_t *face_of(int face, int size) {
    if (face < 0 || face >= MOY_PATH_FACES || size <= 0 || size > MOY_PATH_SIZE_MAX) {
        return NULL;
    }
    return &PF_FACES[face];
}

// The next byte a glyph draws at: Latin-1 at its value, else `?`. *i moves
// past the codepoint.
static int next_byte(const uint8_t *s, size_t n, size_t *i) {
    uint8_t b = s[*i];
    if (b < 0x80) {
        (*i)++;
        return b < 0x20 ? '?' : b;
    }
    int len = (b & 0xE0) == 0xC0 ? 2 : (b & 0xF0) == 0xE0 ? 3 : (b & 0xF8) == 0xF0 ? 4 : 1;
    if (len == 1 || *i + (size_t)len > n) {
        (*i)++;
        return '?';
    }
    uint32_t cp = b & (0x7F >> len);
    for (int k = 1; k < len; k++) {
        uint8_t c = s[*i + (size_t)k];
        if ((c & 0xC0) != 0x80) {
            (*i)++;
            return '?';
        }
        cp = (cp << 6) | (c & 0x3F);
    }
    *i += (size_t)len;
    return cp >= 0xA0 && cp <= 0xFF ? (int)cp : '?';
}

static int kern(const moy_path_face_t *f, int a, int b) {
    int lo = 0, hi = (int)f->nkern - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        const moy_path_kern_t *k = &f->kerns[mid];
        int cmp = k->a != a ? k->a - a : k->b - b;
        if (cmp == 0) {
            return k->dx;
        }
        if (cmp < 0) {
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return 0;
}

static inline int32_t scale(int32_t v, int size, int upm) {
    return (int32_t)((int64_t)v * size / upm);
}

// A quadratic curve from (lx, ly) by (mx, my) to (tx, ty), 26.6, as lines:
// more of them the more it bends.
static int quad(int *n, int32_t lx, int32_t ly, int32_t mx, int32_t my, int32_t tx, int32_t ty) {
    int64_t ddx = (int64_t)lx - 2 * mx + tx, ddy = (int64_t)ly - 2 * my + ty;
    int64_t dd = (ddx < 0 ? -ddx : ddx) + (ddy < 0 ? -ddy : ddy);
    int steps = 1;
    while (steps < CURVE_STEPS && (int64_t)steps * steps * 16 < dd) {
        steps++;
    }
    int32_t ax = lx, ay = ly;
    int64_t nn = (int64_t)steps * steps;
    for (int i = 1; i <= steps; i++) {
        int64_t t = i, u = steps - i;
        int32_t bx = (int32_t)((u * u * lx + 2 * t * u * mx + t * t * tx) / nn);
        int32_t by = (int32_t)((u * u * ly + 2 * t * u * my + t * t * ty) / nn);
        if (!edge_add(n, ax, ay, bx, by)) {
            return 0;
        }
        ax = bx;
        ay = by;
    }
    return 1;
}

// A glyph's contours as edges, its origin at (x, y) (26.6, y down). A
// contour starts on its first on-curve point, else midway between its first
// two off-curve ones; two off-curve points in a row imply the on-curve one
// between them (TrueType's quadratic outlines).
static int glyph_edges(const moy_path_face_t *f, int g, int size, int32_t x, int32_t y, int *n) {
    const moy_path_glyph_t *gl = &f->glyphs[g - 0x20];
    const int16_t *pt = f->pts;
    int upm = f->upm;
    int start = gl->pt;
    for (int c = 0; c < gl->nend; c++) {
        int end = f->ends[gl->end + c];
        int cnt = end - start;
        if (cnt < 2) {
            start = end;
            continue;
        }
        int k0 = -1;
        for (int k = 0; k < cnt; k++) {
            if (pt[2 * (start + k)] & 1) {
                k0 = k;
                break;
            }
        }
        int32_t sx, sy;
        if (k0 >= 0) {
            sx = pt[2 * (start + k0)] >> 1;
            sy = pt[2 * (start + k0) + 1];
        } else {
            k0 = 0;
            sx = ((pt[2 * start] >> 1) + (pt[2 * (start + 1)] >> 1)) / 2;
            sy = (pt[2 * start + 1] + pt[2 * (start + 1) + 1]) / 2;
        }
        int32_t fx = x + scale(sx, size, upm), fy = y - scale(sy, size, upm);
        int32_t lx = fx, ly = fy;
        int has_ctl = 0;
        int32_t qx = 0, qy = 0;         // the pending off-curve point, font units
        for (int s = 1; s <= cnt; s++) {
            int k = (k0 + s) % cnt;
            int16_t raw = pt[2 * (start + k)];
            int32_t vx = raw >> 1, vy = pt[2 * (start + k) + 1];
            if (!(raw & 1)) {
                if (has_ctl) {
                    int32_t ex = (qx + vx) / 2, ey = (qy + vy) / 2;
                    int32_t tx = x + scale(ex, size, upm), ty = y - scale(ey, size, upm);
                    if (!quad(n, lx, ly, x + scale(qx, size, upm), y - scale(qy, size, upm),
                              tx, ty)) {
                        return 0;
                    }
                    lx = tx;
                    ly = ty;
                }
                qx = vx;
                qy = vy;
                has_ctl = 1;
                continue;
            }
            int32_t tx = x + scale(vx, size, upm), ty = y - scale(vy, size, upm);
            int ok = has_ctl ? quad(n, lx, ly, x + scale(qx, size, upm),
                                    y - scale(qy, size, upm), tx, ty)
                             : edge_add(n, lx, ly, tx, ty);
            if (!ok) {
                return 0;
            }
            lx = tx;
            ly = ty;
            has_ctl = 0;
        }
        if (has_ctl) {
            if (!quad(n, lx, ly, x + scale(qx, size, upm), y - scale(qy, size, upm), fx, fy)) {
                return 0;
            }
        } else if (!edge_add(n, lx, ly, fx, fy)) {
            return 0;
        }
        start = end;
    }
    return 1;
}

int mg_path_text(uint16_t *dst, size_t cap, int dw, int face, int size, int x, int y,
                 const uint8_t *s, size_t n, int col, int cam_x, int cam_y,
                 int cx0, int cy0, int cx1, int cy1) {
    const moy_path_face_t *f = face_of(face, size);
    if (f == NULL) {
        return -1;
    }
    int32_t pen = 0;
    int prev = 0;
    size_t i = 0;
    int32_t ox = (int32_t)x - cam_x * ONE, oy = (int32_t)y - cam_y * ONE;
    int32_t top = (int32_t)cy0 * ONE, bottom = (int32_t)cy1 * ONE;
    int32_t asc = scale(f->ascent, size, f->upm), desc = scale(-f->descent, size, f->upm);
    int visible = oy + desc + ONE > top && oy - asc - ONE < bottom;
    while (i < n) {
        int b = next_byte(s, n, &i);
        if (prev) {
            pen += scale(kern(f, prev, b), size, f->upm);
        }
        int32_t gx = ox + pen;
        int32_t adv = scale(f->glyphs[b - 0x20].adv, size, f->upm);
        if (visible && gx + adv + 2 * ONE > (int32_t)cx0 * ONE && gx - 2 * ONE < (int32_t)cx1 * ONE) {
            int ne = 0;
            if (!glyph_edges(f, b, size, gx, oy, &ne)) {
                return -1;
            }
            int bx0, by0, bx1, by1;
            if (bounds(ne, dw, cap, cx0, cy0, cx1, cy1, &bx0, &by0, &bx1, &by1)
                    && raster(dst, cap, dw, ne, bx0, by0, bx1, by1, (uint16_t)col) < 0) {
                return -1;
            }
        }
        pen += adv;
        prev = b;
    }
    return pen;
}

int moy_path_width(int face, int size, const uint8_t *s, size_t n) {
    const moy_path_face_t *f = face_of(face, size);
    if (f == NULL) {
        return -1;
    }
    int32_t pen = 0;
    int prev = 0;
    size_t i = 0;
    while (i < n) {
        int b = next_byte(s, n, &i);
        if (prev) {
            pen += scale(kern(f, prev, b), size, f->upm);
        }
        pen += scale(f->glyphs[b - 0x20].adv, size, f->upm);
        prev = b;
    }
    return pen;
}

int moy_path_metrics(int face, int out[3]) {
    if (face < 0 || face >= MOY_PATH_FACES) {
        out[0] = out[1] = out[2] = 0;
        return 0;
    }
    out[0] = PF_FACES[face].upm;
    out[1] = PF_FACES[face].ascent;
    out[2] = PF_FACES[face].descent;
    return 1;
}
