// moy_scr: a whole screen drawn with no canvas. The header has the design.

#include "moy_scr.h"

#include "moy_path.h"

static inline uint16_t wire(const moy_scr_t *s, uint16_t c) {
    return s->swap ? (uint16_t)((c >> 8) | (c << 8)) : c;
}

uint16_t moy_scr_mix(uint16_t a, uint16_t b, int k) {
    int ar = a >> 11, ag = (a >> 5) & 63, ab = a & 31;
    int br = b >> 11, bg = (b >> 5) & 63, bb = b & 31;
    ar += ((br - ar) * k + 128) >> 8;
    ag += ((bg - ag) * k + 128) >> 8;
    ab += ((bb - ab) * k + 128) >> 8;
    return (uint16_t)((ar << 11) | (ag << 5) | ab);
}

// ---- the passes -----------------------------------------------------------------

// The band's rows, turned into the framebuffer.
static void turn(const moy_scr_t *s) {
    for (int j = s->y0; j < s->y1; j++) {
        const uint16_t *row = s->px + (size_t)(j - s->y0) * (size_t)s->stride;
        for (int i = 0; i < s->w; i++) {
            int fx, fy;
            switch (s->rot) {
                case 90:  fx = j;            fy = s->w - 1 - i; break;
                case 180: fx = s->w - 1 - i; fy = s->h - 1 - j; break;
                case 270: fx = s->h - 1 - j; fy = i;            break;
                default:  fx = i;            fy = j;            break;
            }
            s->fb[(size_t)fy * (size_t)s->fb_w + (size_t)fx] = row[i];
        }
    }
}

int moy_scr_draw(moy_scr_t *s, moy_scr_paint_t paint, const void *ctx) {
    s->fails = 0;
    if (s->scratch != NULL) {
        moy_path_lend(s->scratch, s->scratch_n);
    }
    moy_path_wire_swapped(s->swap);
    if (s->rot == 0 && s->band == NULL) {
        s->px = s->fb;
        s->stride = s->fb_w;
        s->y0 = 0;
        s->y1 = s->h;
        paint(s, ctx);
    } else if (s->band != NULL && s->band_rows > 0) {
        for (int y = 0; y < s->h; y += s->band_rows) {
            s->px = s->band;
            s->stride = s->w;
            s->y0 = y;
            s->y1 = y + s->band_rows < s->h ? y + s->band_rows : s->h;
            paint(s, ctx);
            turn(s);
        }
    } else {
        s->fails++;
    }
    if (s->scratch != NULL) {
        moy_path_lend(NULL, 0);
    }
    return s->fails;
}

// ---- what a pass draws ----------------------------------------------------------

void moy_scr_fill(moy_scr_t *s, int x, int y, int w, int h, uint16_t c) {
    if (x < 0) { w += x; x = 0; }
    if (y < s->y0) { h -= s->y0 - y; y = s->y0; }
    if (x + w > s->w) { w = s->w - x; }
    if (y + h > s->y1) { h = s->y1 - y; }
    if (w <= 0 || h <= 0) {
        return;
    }
    uint16_t px = wire(s, c);
    for (int j = y; j < y + h; j++) {
        uint16_t *row = s->px + (size_t)(j - s->y0) * (size_t)s->stride;
        for (int i = x; i < x + w; i++) {
            row[i] = px;
        }
    }
}

// cos of 0, 15, ... 90 degrees, of 1024
static const int16_t COS[7] = {1024, 989, 887, 724, 512, 265, 0};
#define ARC 7

// A rounded rect's contour (26.6), clockwise from the top-left corner's
// left end; reversed for a hole. The points written.
static int contour(int32_t *p, int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, int rev) {
    int32_t cx[4] = {x + r, x + w - r, x + w - r, x + r};
    int32_t cy[4] = {y + r, y + r, y + h - r, y + h - r};
    int n = 0;
    for (int c = 0; c < 4; c++) {
        for (int k = 0; k < ARC; k++) {
            int32_t a = r * COS[k] / 1024, b = r * COS[ARC - 1 - k] / 1024;   // cos, sin
            int32_t dx, dy;
            switch (c) {
                case 0:  dx = -a; dy = -b; break;
                case 1:  dx = b;  dy = -a; break;
                case 2:  dx = a;  dy = b;  break;
                default: dx = -b; dy = a;  break;
            }
            p[2 * n] = cx[c] + dx;
            p[2 * n + 1] = cy[c] + dy;
            n++;
        }
    }
    if (rev) {
        for (int i = 0, j = n - 1; i < j; i++, j--) {
            int32_t tx = p[2 * i], ty = p[2 * i + 1];
            p[2 * i] = p[2 * j];
            p[2 * i + 1] = p[2 * j + 1];
            p[2 * j] = tx;
            p[2 * j + 1] = ty;
        }
    }
    return n;
}

void moy_scr_rrect(moy_scr_t *s, int x, int y, int w, int h, int r, int t, uint16_t c) {
    if (w <= 0 || h <= 0 || y >= s->y1 || y + h <= s->y0 || x >= s->w || x + w <= 0) {
        return;
    }
    if (r > w / 2) { r = w / 2; }
    if (r > h / 2) { r = h / 2; }
    if (r <= 0 && t <= 0) {
        moy_scr_fill(s, x, y, w, h, c);
        return;
    }
    int32_t pts[2 * 2 * 4 * ARC];
    int32_t ends[2];
    int n = contour(pts, x * 64, y * 64, w * 64, h * 64, r * 64, 0);
    ends[0] = n;
    int ne = 1;
    if (t > 0 && 2 * t < w && 2 * t < h) {
        int ri = r - t > 0 ? r - t : 0;
        n += contour(pts + 2 * n, (x + t) * 64, (y + t) * 64, (w - 2 * t) * 64,
                     (h - 2 * t) * 64, ri * 64, 1);
        ends[ne++] = n;
    }
    int rows = s->y1 - s->y0;
    int cx0 = x > 0 ? x : 0;
    if (mg_path_fill(s->px, (size_t)s->stride * (size_t)rows, s->stride, pts, n, ends, ne,
                     wire(s, c), 0, s->y0, cx0, 0, s->w, rows) < 0) {
        s->fails++;
    }
}

// ---- the icons ------------------------------------------------------------------
//
// Three of the default smooth set's marks (native/moy_ui/icons/smooth), as
// the strokes they are drawn from: polylines on its 24-unit grid, a point in
// quarter units, each line ended by -1. A stroke is two units wide, each
// segment a quad squared off at its ends, so the joints close.

static const int16_t IC_RETRY[] = {
    // the arc, clockwise from three o'clock round to the arrow's foot
    84, 48, 83, 57, 79, 66, 73, 73, 66, 79, 57, 83, 48, 84, 39, 83, 30, 79, 23, 73, 17, 66, 13,
    57, 12, 48, 13, 39, 17, 30, 23, 23, 30, 17, 39, 13, 48, 12, 57, 13, 66, 17, 73, 23, 84, 32,
    -1,
    84, 12, 84, 32, 64, 32, -1, -2,
};
static const int16_t IC_HOME[] = {
    12, 40, 48, 12, 84, 40, 84, 84, 12, 84, 12, 40, -1,
    36, 84, 36, 52, 60, 52, 60, 84, -1, -2,
};
static const int16_t IC_CODE[] = {
    72, 64, 88, 48, 72, 32, -1,
    24, 32, 8, 48, 24, 64, -1,
    58, 16, 38, 80, -1, -2,
};

static const int16_t *const ICONS[MOY_SCR_ICONS] = {IC_RETRY, IC_HOME, IC_CODE};

static int64_t isqrt64(int64_t v) {
    int64_t r = 0, b = (int64_t)1 << 62;
    while (b > v) {
        b >>= 2;
    }
    while (b != 0) {
        if (v >= r + b) {
            v -= r + b;
            r = (r >> 1) + b;
        } else {
            r >>= 1;
        }
        b >>= 2;
    }
    return r;
}

#define IC_SEGS 48

void moy_scr_icon(moy_scr_t *s, int icon, int x, int y, int size, uint16_t c) {
    if (icon < 0 || icon >= MOY_SCR_ICONS || y >= s->y1 || y + size <= s->y0) {
        return;
    }
    int32_t pts[IC_SEGS * 8], ends[IC_SEGS];
    int n = 0, ne = 0;
    int32_t hw = size * 64 / 24;                    // half of two units, 26.6
    const int16_t *p = ICONS[icon];
    while (*p != -2 && ne < IC_SEGS) {
        while (p[2] != -1 && ne < IC_SEGS) {
            int64_t ax = x * 64 + (int64_t)p[0] * size * 64 / 96;
            int64_t ay = y * 64 + (int64_t)p[1] * size * 64 / 96;
            int64_t bx = x * 64 + (int64_t)p[2] * size * 64 / 96;
            int64_t by = y * 64 + (int64_t)p[3] * size * 64 / 96;
            int64_t dx = bx - ax, dy = by - ay, len = isqrt64(dx * dx + dy * dy);
            p += 2;
            if (len == 0) {
                continue;
            }
            int64_t ex = dx * hw / len, ey = dy * hw / len;   // along, half a stroke
            int64_t nx = -ey, ny = ex;                         // across
            int32_t q[8] = {(int32_t)(ax - ex + nx), (int32_t)(ay - ey + ny),
                            (int32_t)(bx + ex + nx), (int32_t)(by + ey + ny),
                            (int32_t)(bx + ex - nx), (int32_t)(by + ey - ny),
                            (int32_t)(ax - ex - nx), (int32_t)(ay - ey - ny)};
            for (int i = 0; i < 8; i++) {
                pts[2 * n + i] = q[i];
            }
            n += 4;
            ends[ne++] = n;
        }
        p += 3;
    }
    int rows = s->y1 - s->y0;
    if (n > 0 && mg_path_fill(s->px, (size_t)s->stride * (size_t)rows, s->stride, pts, n,
                              ends, ne, wire(s, c), 0, s->y0, x > 0 ? x : 0, 0, s->w,
                              rows) < 0) {
        s->fails++;
    }
}

int moy_scr_width(int face, int size, const char *t, size_t n) {
    int w = moy_path_width(face, size * 64, (const uint8_t *)t, n);
    return w < 0 ? 0 : (w + 32) >> 6;
}

int moy_scr_cap(int face, int size) {
    // the outline faces' capitals stand 0.7 of the em (Atkinson's 'H' and
    // JetBrains Mono's alike)
    (void)face;
    return (size * 7 + 5) / 10;
}

int moy_scr_text(moy_scr_t *s, int face, int size, int x, int base, const char *t, size_t n,
                 uint16_t c) {
    int adv = moy_scr_width(face, size, t, n);
    if (base - size >= s->y1 || base + size / 2 < s->y0 || n == 0) {
        return adv;
    }
    int rows = s->y1 - s->y0;
    int cx0 = x - 4 > 0 ? x - 4 : 0;
    if (mg_path_text(s->px, (size_t)s->stride * (size_t)rows, s->stride, face, size * 64,
                     x * 64, base * 64, (const uint8_t *)t, n, wire(s, c), 0, s->y0, cx0, 0,
                     s->w, rows) < 0) {
        s->fails++;
    }
    return adv;
}

// chrome.py's "moy" art: '.' is clear, a hex digit a MOY64 index.
static const char *const MOY_ART[16] = {
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
};

static int art_colour(char ch, uint16_t *out) {
    switch (ch) {
        case '0': *out = 0x0000u; return 1;      // black
        case '2': *out = 0x792Au; return 1;      // dark_purple
        case '6': *out = 0xC618u; return 1;      // light_grey
        case '7': *out = 0xFF9Du; return 1;      // white
        case 'd': *out = 0x83B3u; return 1;      // indigo
        default: return 0;
    }
}

void moy_scr_moy(moy_scr_t *s, int x, int y, int scale) {
    if (y >= s->y1 || y + 16 * scale <= s->y0) {
        return;
    }
    for (int j = 0; j < 16; j++) {
        for (int i = 0; i < 16; i++) {
            uint16_t c;
            if (art_colour(MOY_ART[j][i], &c)) {
                moy_scr_fill(s, x + i * scale, y + j * scale, scale, scale, c);
            }
        }
    }
}

// ---- the boot splash ------------------------------------------------------------

// Moy Day's (native/moy_ui/themes/moy.json, light): the panel, the ink, the
// dim ink, the accent, and the border mixed a quarter toward the dim ink
// (ui.progress's track); tests/test_moy_kernel.py holds them to the theme.
const uint16_t moy_splash_day[MOY_SPLASH_PAL] = {
    0xEF7Eu, 0x10A3u, 0x5B2Fu, 0x49FAu, 0xB5B8u, 0xB5B8u,
};

typedef struct {
    const uint16_t *pal;
    int permille;
    const char *status;
    size_t n;
} splash_t;

static void splash_paint(moy_scr_t *s, const void *ctx) {
    const splash_t *a = ctx;
    const uint16_t *pal = a->pal;
    int W = s->w, H = s->h, desk = W >= 800;
    int scale = (W < H ? W : H) / 56;
    if (scale < 3) {
        scale = 3;
    }
    int n = 16 * scale;
    int fw = desk ? 40 : (W > 320 ? 26 : 22);
    int fm = desk ? 17 : 12;
    int cap = moy_scr_cap(MOY_SCR_BOLD, fw);
    int gap = n / 4;
    int block = n + gap + cap;
    if (a->permille >= 0) {
        block += desk ? 34 : 22;
    }
    if (a->n > 0) {
        block += desk ? 30 : 20;
    }
    int top = (H - block) / 2;
    moy_scr_fill(s, 0, 0, W, H, pal[MOY_SPLASH_BG]);
    moy_scr_moy(s, (W - n) / 2, top, scale);
    int y = top + n + gap + cap;
    static const char word[] = "moybyte";
    int ww = moy_scr_width(MOY_SCR_BOLD, fw, word, sizeof(word) - 1);
    moy_scr_text(s, MOY_SCR_BOLD, fw, (W - ww) / 2, y, word, sizeof(word) - 1,
                 pal[MOY_SPLASH_INK]);
    if (a->permille >= 0) {
        int tw = desk ? 320 : W * 3 / 8;
        int th = desk ? 6 : 4;
        y += desk ? 28 : 18;
        int tx = (W - tw) / 2;
        int edged = pal[MOY_SPLASH_EDGE] != pal[MOY_SPLASH_TRACK];
        moy_scr_rrect(s, tx, y, tw, th, th / 2, 0, pal[MOY_SPLASH_TRACK]);
        if (edged) {
            moy_scr_rrect(s, tx, y, tw, th, th / 2, 1, pal[MOY_SPLASH_EDGE]);
        }
        int pm = a->permille > 1000 ? 1000 : a->permille;
        int fill = tw * pm / 1000;
        if (pm > 0) {
            moy_scr_rrect(s, tx, y, fill > th ? fill : th, th, th / 2, 0,
                          pal[MOY_SPLASH_VALUE]);
        }
        y += th;
    }
    if (a->n > 0) {
        y += desk ? 30 : 20;
        int sw = moy_scr_width(MOY_SCR_SANS, fm, a->status, a->n);
        moy_scr_text(s, MOY_SCR_SANS, fm, (W - sw) / 2, y, a->status, a->n,
                     pal[MOY_SPLASH_DIM]);
    }
}

int moy_splash(moy_scr_t *s, const uint16_t *pal, int permille, const char *status, size_t n) {
    splash_t a = {pal != NULL ? pal : moy_splash_day, permille, status, status ? n : 0};
    return moy_scr_draw(s, splash_paint, &a);
}

int moy_splash_buf(uint16_t *buf, size_t cap, int stride, int x0, int y0, int w, int h,
                   int swap, const uint16_t *pal, int permille, const char *status, size_t n) {
    if (x0 < 0 || y0 < 0 || w <= 0 || h <= 0 || x0 + w > stride
            || (size_t)(y0 + h - 1) * (size_t)stride + (size_t)(x0 + w) > cap) {
        return -1;
    }
    moy_scr_t s = {0};
    s.fb = buf + (size_t)y0 * (size_t)stride + (size_t)x0;
    s.fb_w = stride;
    s.w = w;
    s.h = h;
    s.swap = swap;
    return moy_splash(&s, pal, permille, status, n);
}
