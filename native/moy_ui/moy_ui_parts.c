// The parts the public UI API's verbs draw (moy_ui.h, the parts;
// docs/media/look_2026-10/DESIGN.md, Components): the value rows, a group's
// caption and card, and the keycap hints, through moy_shape and the text lane.

#include <string.h>

#include "moy_canvas.h"
#include "moy_shape.h"
#include "moy_str.h"
#include "moy_theme.h"
#include "moy_ui.h"

typedef struct {
    const moy_ui_sink_t *s;
    moy_canvas_row_t *row;
    moy_text_target_t t;
    int look, run, fs, desk, win;
} pd_t;

static void open_pd(const moy_ui_sink_t *s, pd_t *d, int fs, int desk) {
    moy_ui_count_call();
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    const int32_t *st = row->st;
    d->s = s;
    d->row = row;
    d->t.px = row->px;
    d->t.cap = row->cap;
    d->t.dw = (int)st[MOY_ST_W];
    d->t.cam_x = (int)st[MOY_ST_CAM_X];
    d->t.cam_y = (int)st[MOY_ST_CAM_Y];
    d->t.cx0 = (int)st[MOY_ST_CX0];
    d->t.cy0 = (int)st[MOY_ST_CY0];
    d->t.cx1 = (int)st[MOY_ST_CX1];
    d->t.cy1 = (int)st[MOY_ST_CY1];
    d->look = s->look;
    d->run = s->kind == MOY_UI_SINK_RUN;
    d->fs = fs < 1 ? 1 : fs;
    d->desk = (desk & MOY_UI_DESK) != 0;
    d->win = (desk & MOY_UI_WINDOW) != 0;
}

static int metric(int kind, int slot) {
    int n = 0;
    const int16_t *m = moy_ui_metrics(kind, &n);
    return m != NULL && slot < n ? m[slot] : 0;
}

// A parts metric at the scale: its `_desk` slot (the next) on the desk.
static int pm(const pd_t *d, int slot, int has_desk) {
    return metric(MOY_UI_K_VALUE_ROW, slot + (has_desk && d->desk ? 1 : 0)) * d->fs;
}

static int gm(int slot, int fs) {
    return metric(MOY_UI_K_GROUP, slot) * (fs < 1 ? 1 : fs);
}

// A token's colour index, `alt`'s where the theme is silent.
static int tok(const pd_t *d, int token, int alt) {
    int t = moy_ui_token(d->look, token);
    if (t < 0) {
        t = moy_ui_token(d->look, alt);
    }
    return t < 0 ? 0 : t;
}

static uint16_t pal(const pd_t *d, int ci) {
    return d->row->pal[moy_ui_pidx(ci)];
}

// A look colour (an index, or RGB) in the buffer's order.
static uint16_t val(const pd_t *d, int32_t v) {
    if (v >= 0 && v < 128) {
        return pal(d, v);
    }
    return moy_shape_wire(moy_theme_rgb565(v));
}

static int set(int32_t v) {
    return v != MOY_THEME_NONE && v != MOY_THEME_OFF;
}

static uint16_t mix(uint16_t a, uint16_t b, int k) {         // k/255 of b over a, wire order
    uint16_t ca = moy_shape_wire(a), cb = moy_shape_wire(b);
    int ar = ca >> 11, ag = (ca >> 5) & 63, ab = ca & 31;
    int br = cb >> 11, bg = (cb >> 5) & 63, bb = cb & 31;
    return moy_shape_wire((uint16_t)(((ar + (br - ar) * k / 255) << 11)
                                     | ((ag + (bg - ag) * k / 255) << 5)
                                     | (ab + (bb - ab) * k / 255)));
}

// The plate a focus mark lays in colour `c` at `alpha` (0..255) over `g`, all
// wire order, as the depth paints it (moy_ui_depth.c's tint: moy_shape's
// blend of a pixel the shape covers whole, rounded), so a part held apart
// from the plate is held from the pixels it lies on.
static uint16_t plate_of(uint16_t g, uint16_t c, int alpha) {
    uint16_t a = moy_shape_wire(g), b = moy_shape_wire(c);
    int wt = (15 * alpha * 256 + 15 * 255 / 2) / (15 * 255), iw = 256 - wt;
    int r = (((b >> 11) & 31) * wt + ((a >> 11) & 31) * iw + 128) >> 8;
    int gg = (((b >> 5) & 63) * wt + ((a >> 5) & 63) * iw + 128) >> 8;
    int bl = ((b & 31) * wt + (a & 31) * iw + 128) >> 8;
    return moy_shape_wire((uint16_t)((r << 11) | (gg << 5) | bl));
}

// Whether two colours (wire order) have a luminance ratio of num/den or more,
// on squares for the sRGB curve.
static int ratio_ge(uint16_t a, uint16_t b, int num, int den) {
    int64_t l[2];
    uint16_t c[2] = {moy_shape_wire(a), moy_shape_wire(b)};
    for (int i = 0; i < 2; i++) {
        int r = (c[i] >> 11) * 255 / 31, g = ((c[i] >> 5) & 63) * 255 / 63;
        int bl = (c[i] & 31) * 255 / 31;
        l[i] = 2126LL * r * r + 7152LL * g * g + 722LL * bl * bl + 32512500LL;
    }
    int64_t hi = l[0] > l[1] ? l[0] : l[1], lo = l[0] > l[1] ? l[1] : l[0];
    return den * hi >= num * lo;
}

// Whether two colours tell apart (1.5:1). A part whose two states would paint
// in one colour draws one of them as an outline instead (Contrast's toggles
// and tracks, where `value` and `border` are both the ink).
static int apart(uint16_t a, uint16_t b) {
    return ratio_ge(a, b, 3, 2);
}

// Colour `c` kept apart (1.6:1, WCAG) from the ground `g` it lies on: moved toward
// `to` as far as it takes, aimed at 13:8 so the ratio survives the RGB565
// rounding of either colour. A part of the structure ramp (an Off track, a
// rest track or step) holds its ratio on a focused row's plate as on its card.
static int32_t lum_w(uint16_t w) {           // a wire-order colour's WCAG luminance
    uint16_t c = moy_shape_wire(w);
    int32_t r = (c >> 11) * 255 / 31, g = ((c >> 5) & 63) * 255 / 63, b = (c & 31) * 255 / 31;
    return moy_theme_lum_of(MOY_THEME_RGB | (r << 16) | (g << 8) | b);
}

static uint16_t hold(uint16_t c, uint16_t g, uint16_t to) {
    int32_t lg = lum_w(g);
    for (int k = 0; k <= 255; k += 8) {
        uint16_t m = mix(c, to, k);
        int32_t lm = lum_w(m);
        int32_t hi = lm > lg ? lm : lg, lo = lm > lg ? lg : lm;
        if (8 * (hi + 500) >= 13 * (lo + 500)) {
            return m;
        }
    }
    return to;
}

// Whether the look is a dark one: the marks say.
static int dark(const pd_t *d) {
    (void)d;
    return moy_ui_marks()->dark;
}

// Whether the marks' current plate is solid (Contrast's): its parts take the
// inverted treatment, a tile its colour and the ink on it.
static int solid_marks(void) {
    const moy_ui_marks_t *m = moy_ui_marks();
    return m->cur_style == MOY_UI_CUR_PLATE && m->cur_alpha >= 255;
}

// The colour a page's ground is (moy_ui_page_ground) in the buffer's order,
// in *c. In a window: the window's body as moy_ui_depth_frame paints it.
static uint16_t ground(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (d->win) {
        return set(dp->body) ? val(d, dp->body)
                             : pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_SURFACE));
    }
    return pal(d, tok(d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE));
}

static void fill(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    moy_target_fill(&d->t, x, y, w, h, c);
    d->row->st[MOY_ST_N_FILL]++;
}

// A rounded rect `r` filled in `c` (no fill: c_fill 0), ringed `line` wide in `ring`.
static void rrect(pd_t *d, int x, int y, int w, int h, int r, int has_fill, uint16_t c,
                  int line, uint16_t ring) {
    if (w <= 0 || h <= 0) {
        return;
    }
    moy_shape_paint_t p;
    memset(&p, 0, sizeof(p));
    p.n = 1;
    p.c[0] = c;
    moy_shape_rrect_t sp;
    memset(&sp, 0, sizeof(sp));
    for (int k = 0; k < 4; k++) {
        sp.r[k] = (uint8_t)(r < 0 ? 0 : r);
    }
    sp.stepped = moy_ui_depth()->stepped;
    sp.line = (uint8_t)line;
    sp.line_hi = ring;
    sp.line_lo = -1;
    sp.fill = has_fill ? &p : NULL;
    moy_shape_rrect(&d->t, x, y, w, h, &sp);
    d->row->st[MOY_ST_N_FILL]++;
}

// -- text ------------------------------------------------------------------------------

typedef struct {
    uint32_t id;
    int line, ascent;
    int deep;                       // the rows from the line's top to its deepest ink
} pf_t;

// The font a role's text draws in; the caption role takes the ui role's
// where a theme installs a font for that and none for it.
static pf_t font_of(const pd_t *d, int role) {
    pf_t f = {MOY_FONT_PETME128, MOY_TEXT_CELL, MOY_TEXT_CELL - 1, MOY_TEXT_CELL};
    if (!d->run) {
        moy_font_info_t fi;
        f.id = moy_ui_font(role, &fi);
        f.line = fi.line;
        f.ascent = fi.ascent;
        f.deep = fi.h;
        if (role == MOY_UI_FONT_CAPTION && f.id == MOY_FONT_PETME128) {
            f.id = moy_ui_font(MOY_UI_FONT_UI, &fi);
            f.line = fi.line;
            f.ascent = fi.ascent;
            f.deep = fi.h;
        }
    }
    return f;
}

// The row of a font's line its letters' ink starts at (a capital's top, an
// ascender's), probed once a font by drawing them a row at a time: the
// room a line keeps above its letters (an accent's) is not the letters'.
static int ink_top(const pf_t *f) {
    static uint32_t s_font = 0xFFFFFFFFu;
    static int s_top = 0;
    if (f->id == s_font) {
        return s_top;
    }
    static const uint8_t probe[] = "MHdlkbij";
    uint16_t px[96];
    int top = 0;
    for (int r = 0; r < f->line; r++) {
        memset(px, 0, sizeof(px));
        moy_text_target_t t = {px, 96, 96, 0, r, 0, 0, 96, 1};
        moy_text(&t, f->id, MOY_TEXT_DATA, probe, sizeof(probe) - 1, 0, 0, 0xFFFF, 1,
                 MOY_TEXT_LEFT, -1);
        int ink = 0;
        for (int i = 0; i < 96 && !ink; i++) {
            ink = px[i] != 0;
        }
        if (ink) {
            top = r;
            break;
        }
    }
    s_font = f->id;
    s_top = top;
    return top;
}

static int tw(const pf_t *f, const uint8_t *p, size_t n, int scale) {
    return moy_text_width(f->id, p, n, scale);
}

// Text whose line is centred in the rows y..y+h, from x, the table's where
// `table`; its width.
static int text_t(pd_t *d, const pf_t *f, int table, const uint8_t *p, size_t n, int x, int y,
                  int h, uint16_t c, int scale) {
    if (n == 0) {
        return 0;
    }
    moy_ui_span_t t = {p, (uint16_t)n};
    return moy_ui_text_in(d->s, f->id, table, t, x, y + moy_ui_fdiv(h - f->line * scale, 2), c);
}

static int text(pd_t *d, const pf_t *f, const uint8_t *p, size_t n, int x, int y, int h,
                uint16_t c, int scale) {
    return text_t(d, f, 0, p, n, x, y, h, c, scale);
}

// The scale text draws at on the sink.
static int tscale(const moy_ui_sink_t *s) {
    return s->fs < 1 ? 1 : s->fs;
}

// -- signs -----------------------------------------------------------------------------

// A chevron `w` wide pointing right (dir 1) or left (-1), one pixel at the
// scale thick and 2w - 1 rows tall.
static int chevron_h(const pd_t *d, int w) {
    int half = w / d->fs - 1;
    return (2 * (half > 0 ? half : 0) + 1) * d->fs;
}

static void chevron(pd_t *d, int x, int y, int w, int dir, uint16_t c) {
    int k = d->fs, half = w / k - 1, rows = chevron_h(d, w) / k;
    for (int i = 0; i < rows; i++) {
        int o = i <= half ? i : rows - 1 - i;
        int cx = dir > 0 ? o : half - o;
        moy_target_fill(&d->t, x + cx * k, y + i * k, k, k, c);
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// A sign's strokes in a cap box `h` tall: the stroke is the scale's pixel, the
// arrow `len` strokes long (odd, so it has a middle) with a head of `head`
// strokes a side, both growing with the box from 7 and 2 in the handheld's 15.
static void sign_size(const pd_t *d, int h, int *len, int *head) {
    int n = h / d->fs;
    *len = (n * 7 / 15) | 1;
    *head = *len / 3;
}

// The enter sign in the cap box: a hook, its arrow at the left.
static void enter_sign(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    int k = d->fs, len, head;
    sign_size(d, h, &len, &head);
    int e = (h / k) * 6 / 15, t = (h / k) * 4 / 15;
    int mx = x + w - e * k, my = y + h / 2 + k;
    moy_target_fill(&d->t, mx, y + t * k, k, my - y - t * k + k, c);
    moy_target_fill(&d->t, x + e * k, my, mx - x - e * k, k, c);
    for (int i = 1; i <= head; i++) {
        moy_target_fill(&d->t, x + (e - 1) * k + i * k, my - i * k, k, 2 * i * k + k, c);
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// A tall arrow's strokes in a box `rows` strokes tall (a letter's
// capitals, where no keycap holds it): its length and its head's strokes a
// side. The head spans the box's odd rows; up or down the shaft does too,
// and across it runs a third longer than the head is tall.
static void tall_size(int rows, int dir, int *len, int *head) {
    int n = rows > 2 ? (rows - 1) | 1 : 1;
    *len = dir < 2 ? n + n / 3 : n;
    *head = n / 2;
}

// An arrow in the cap box: `dir` 0 left, 1 right, 2 up, 3 down; `tall`,
// as tall as the box (`tall_size`).
static void arrow_sign(pd_t *d, int x, int y, int w, int h, int dir, int tall, uint16_t c) {
    int k = d->fs, n, head, cx = x + w / 2, cy = y + h / 2;
    if (tall) {
        tall_size(h / k, dir, &n, &head);
        cy = y + (h - k) / 2;
    } else {
        sign_size(d, h, &n, &head);
    }
    int len = n * k;
    if (dir < 2) {
        int x0 = cx - len / 2;
        moy_target_fill(&d->t, x0, cy, len, k, c);
        for (int i = 1; i <= head; i++) {
            int ax = dir == 0 ? x0 + i * k : x0 + len - k - i * k;
            moy_target_fill(&d->t, ax, cy - i * k, k, 2 * i * k + k, c);
        }
    } else {
        int y0 = cy - len / 2;
        moy_target_fill(&d->t, cx, y0, k, len, c);
        for (int i = 1; i <= head; i++) {
            int ay = dir == 2 ? y0 + i * k : y0 + len - k - i * k;
            moy_target_fill(&d->t, cx - i * k, ay, 2 * i * k + k, k, c);
        }
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// The T-Deck's trackball rolled left and right in the cap box: a ball
// between a head pointing left and one pointing right.
static void ball_sign(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    int k = d->fs, n = h / k;
    int dia = (n * 5 / 15) | 1, hd = n >= 15 ? 3 : n >= 11 ? 2 : 1, total = 2 * hd + 2 + dia;
    int x0 = x + (w - total * k) / 2, mid = y + (h - k) / 2;
    for (int i = 0; i < hd; i++) {
        int rl = hd - 1 - i;          // the left head's tip is its first column
        moy_target_fill(&d->t, x0 + i * k, mid - i * k, k, (2 * i + 1) * k, c);
        moy_target_fill(&d->t, x0 + (hd + 2 + dia + i) * k, mid - rl * k, k, (2 * rl + 1) * k, c);
    }
    int bx = x0 + (hd + 1) * k, by = mid + k / 2 - dia * k / 2;
    rrect(d, bx, by, dia * k, dia * k, dia * k / 2, 1, c, 0, 0);
    d->row->st[MOY_ST_N_FILL]++;
}

// The T-Deck's trackball itself in the cap box (what it picks, rolled and
// clicked): the ball alone.
static void ball_only_sign(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    int k = d->fs, n = h / k;
    int dia = (n * 7 / 15) | 1;
    int bx = x + (w - dia * k) / 2, by = y + (h - dia * k) / 2;
    rrect(d, bx, by, dia * k, dia * k, dia * k / 2, 1, c, 0, 0);
    d->row->st[MOY_ST_N_FILL]++;
}

// A gamepad's d-pad pressed left or right in the cap box: its cross, the
// left and right arms solid, the up and down ones outlined.
static void dpad_sign(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    int k = d->fs, n = h / k;
    int arm = (n * 3 / 15) | 1, reach = n * 4 / 15, size = 2 * reach + arm;
    int x0 = x + (w - size * k) / 2, y0 = y + (h - size * k) / 2;
    moy_target_fill(&d->t, x0, y0 + reach * k, size * k, arm * k, c);
    int ax = x0 + reach * k, aw = arm * k, ah = reach * k + k;
    for (int s = 0; s < 2; s++) {
        int ay = s ? y0 + (reach + arm - 1) * k : y0;
        moy_target_fill(&d->t, ax, ay, k, ah, c);
        moy_target_fill(&d->t, ax + aw - k, ay, k, ah, c);
        moy_target_fill(&d->t, ax, s ? ay + ah - k : ay, aw, k, c);
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// -- the current mark on an option -------------------------------------------------------

// The current mark on box b (radius r): the theme's (`side` MOY_UI_MARK_SIDE on
// a row, whose current `bar` is beside it). The ink the option takes.
static int current(const moy_ui_sink_t *s, pd_t *d, moy_ui_box_t b, int r, int ink, int side) {
    int mi = -1;
    moy_ui_current_in(s, b, r, (moy_ui_marks_shown() ? 0 : MOY_UI_MARK_BACK) | side, d->fs, &mi,
                      0);
    return mi >= 0 ? mi : ink;
}

// -- icons -------------------------------------------------------------------------------

static int icon_d(pd_t *d, int slot, int glyph, moy_ui_box_t b, int ci) {
    int box = b.w < b.h ? b.w : b.h;
    int clear;
    const uint8_t *moy = slot == MOY_UI_ICON_MOY ? moy_ui_moy(&clear) : NULL;
    if (moy != NULL) {
        // Moy is pixel art: its tile at the largest whole scale the box holds
        int sc = box >= 16 ? box / 16 : 1, x0 = b.x + moy_ui_fdiv(b.w - 16 * sc, 2);
        int y0 = b.y + moy_ui_fdiv(b.h - 16 * sc, 2);
        for (int i = 0; i < 256; i++) {
            if (moy[i] != clear) {
                moy_target_fill(&d->t, x0 + (i & 15) * sc, y0 + (i >> 4) * sc, sc, sc,
                                pal(d, moy[i]));
            }
        }
        d->row->st[MOY_ST_N_FILL]++;
        return 1;
    }
    uint32_t f24 = moy_ui_icon_font(1), f16 = moy_ui_icon_font(0);
    uint32_t font = box >= 24 && f24 ? f24 : f16 ? f16 : 0;
    if (slot >= 0 && font && !d->run && moy_ui_cmask() != 63) {   // a cart's: the glyph
        int size = font == f24 ? 24 : 16;
        int w = moy_text_glyph(&d->t, font, MOY_UI_ICON_FIRST + slot,
                               b.x + moy_ui_fdiv(b.w - size, 2),
                               b.y + moy_ui_fdiv(b.h - size, 2), pal(d, ci), 1);
        d->row->st[MOY_ST_N_TEXT]++;
        if (w > 0) {
            return 1;
        }
    }
    if (glyph >= 0) {
        d->row->st[MOY_ST_N_FILL] += moy_ui_glyph_draw(&d->t, glyph, b.x, b.y, b.w, b.h,
                                                       pal(d, ci), d->fs);
        return 1;
    }
    return 0;
}

int moy_ui_icon_in(const moy_ui_sink_t *s, int slot, int glyph, moy_ui_box_t b, int ci, int fs) {
    pd_t d;
    open_pd(s, &d, fs, 0);
    return icon_d(&d, slot, glyph, b, ci);
}

// -- value rows -------------------------------------------------------------------------

static int pad_of(const pd_t *d, const moy_ui_vrow_t *r) {
    return r->card || r->desk ? gm(MOY_UI_M_GROUP_PAD, d->fs)
                              : metric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * d->fs;
}

// The span of option i of `opts` (NUL-separated in n bytes).
static moy_ui_span_t option(moy_ui_span_t opts, int i) {
    moy_ui_span_t o = {opts.p, 0};
    int at = 0;
    size_t j = 0, from = 0;
    for (; j <= opts.n; j++) {
        if (j == opts.n || opts.p[j] == 0) {
            if (at == i) {
                o.p = opts.p + from;
                o.n = (uint16_t)(j - from);
                return o;
            }
            at++;
            from = j + 1;
        }
    }
    o.n = 0;
    return o;
}

static uint32_t colour_at(const uint8_t *p, int i) {
    const uint8_t *q = p + 4 * i;
    return (uint32_t)q[0] | ((uint32_t)q[1] << 8) | ((uint32_t)q[2] << 16);
}

// A swatch's colour on the sink: exact on the shell's glass, MOY64's nearest
// on a cart's canvas (a run, or inside the app-cart boundary).
static uint16_t swatch(const pd_t *d, uint32_t rgb) {
    if (d->run || moy_ui_cmask() == 63) {
        return pal(d, moy_theme_nearest(MOY_THEME_RGB | (int32_t)(rgb & 0xffffff)));
    }
    return moy_shape_wire(moy_theme_rgb565(MOY_THEME_RGB | (int32_t)(rgb & 0xffffff)));
}

// The geometry the draw and the hit test share: the options' boxes (seg,
// swatch) from the right, or the slider's track.
typedef struct {
    int right;                      // the value column's right edge
    int gap;                        // the seg's space between options
    int dot, step;                  // the swatches'
    int ring, apart;                // the current swatch's ring (its reach past the dot)
                                    // and the room its neighbours stand off by
    moy_ui_box_t track;
} vgeo_t;

static void geo(const pd_t *d, moy_ui_box_t b, const moy_ui_vrow_t *r, vgeo_t *g) {
    g->right = b.x + b.w - pad_of(d, r);
    g->gap = pm(d, MOY_UI_M_VALUE_ROW_SEG, 1);
    g->dot = pm(d, MOY_UI_M_VALUE_ROW_DOT, 1);
    g->step = pm(d, MOY_UI_M_VALUE_ROW_DOT_STEP, 1);
    // the ring's outer pixel stands 3 px clear of a neighbouring dot
    g->ring = 3 * d->fs;
    g->apart = g->ring + 4 * d->fs - (g->step - g->dot);
    g->apart = g->apart > 0 ? g->apart : 0;
    int tw_ = pm(d, MOY_UI_M_VALUE_ROW_TRACK, 1);
    g->track = (moy_ui_box_t){(int16_t)(g->right - tw_), (int16_t)(b.y + b.h / 2 - d->fs),
                              (int16_t)tw_, (int16_t)(3 * d->fs)};
}

// Option i's box of a seg: x from the right, `ph` tall.
static moy_ui_box_t seg_box(const pd_t *d, const pf_t *f, moy_ui_box_t b, const moy_ui_vrow_t *r,
                            const vgeo_t *g, int i, int scale) {
    int x = g->right - g->gap / 2;
    int ph = b.h - (d->desk ? 8 : 4) * d->fs;
    for (int j = r->n - 1; j >= 0; j--) {
        moy_ui_span_t o = option(r->value, j);
        int ow = tw(f, o.p, o.n, scale);
        x -= ow;
        if (j == i) {
            return (moy_ui_box_t){(int16_t)(x - g->gap / 2), (int16_t)(b.y + (b.h - ph) / 2),
                                  (int16_t)(ow + g->gap), (int16_t)ph};
        }
        x -= g->gap + (d->desk ? 4 : 2) * d->fs;
    }
    return (moy_ui_box_t){0, 0, 0, 0};
}

// Option i's box of a tab row: x from the left, `ph` tall.
static moy_ui_box_t tab_box(const pd_t *d, const pf_t *f, moy_ui_box_t b, const moy_ui_vrow_t *r,
                            const vgeo_t *g, int i, int scale) {
    int x = b.x + pad_of(d, r);
    int ph = b.h - pm(d, MOY_UI_M_VALUE_ROW_TAB, 1);
    for (int j = 0; j < r->n; j++) {
        moy_ui_span_t o = option(r->value, j);
        int ow = tw(f, o.p, o.n, scale) + g->gap;
        if (j == i) {
            return (moy_ui_box_t){(int16_t)x, (int16_t)(b.y + (b.h - ph) / 2), (int16_t)ow,
                                  (int16_t)ph};
        }
        x += ow + (d->desk ? 4 : 2) * d->fs;
    }
    return (moy_ui_box_t){0, 0, 0, 0};
}

// A stepped slider's segments (o.steps): one per step from lo to hi, the
// power popover's gauge (6 wide on an 8 pitch, 8 tall, at the scale) where
// they fit the track, narrower where they do not, right-aligned in it.
static int step_geo(const pd_t *d, const vgeo_t *g, const moy_ui_vrow_t *r, int *pitch) {
    int n = r->hi > r->lo ? r->hi - r->lo : 1;
    int p = 8 * d->fs, room = (g->track.w + 2 * d->fs) / n;
    *pitch = p < room ? p : room < 2 ? 2 : room;
    return g->track.x + g->track.w - (n * *pitch - 2 * d->fs);
}

static moy_ui_box_t dot_box(moy_ui_box_t b, const moy_ui_vrow_t *r, const vgeo_t *g, int i) {
    int x = g->right - g->dot - (r->n - 1 - i) * g->step;
    if (r->cur >= 0 && r->cur < r->n && i <= r->cur) {
        x -= (i < r->cur ? 2 : 1) * g->apart;   // the current dot's pitch opens for its ring
    }
    return (moy_ui_box_t){(int16_t)x, (int16_t)(b.y + (b.h - g->dot) / 2), (int16_t)g->dot,
                          (int16_t)g->dot};
}

int32_t moy_ui_vrow_at(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, const moy_ui_vrow_t *r,
                       int x) {
    pd_t d;
    open_pd(s, &d, r->fs, r->desk);
    vgeo_t g;
    geo(&d, b, r, &g);
    if (kind == MOY_UI_V_SLIDER && r->steps) {
        int pitch, x0 = step_geo(&d, &g, r, &pitch);
        int32_t v = x < x0 ? r->lo : r->lo + 1 + (x - x0) / pitch;
        return v > r->hi ? r->hi : v;
    }
    if (kind == MOY_UI_V_SLIDER) {
        int dx = x - g.track.x;
        dx = dx < 0 ? 0 : dx > g.track.w ? g.track.w : dx;
        if (g.track.w <= 0 || r->hi <= r->lo) {
            return r->lo;
        }
        return r->lo + (int32_t)(((int64_t)dx * (r->hi - r->lo) + g.track.w / 2) / g.track.w);
    }
    for (int i = 0; i < r->n; i++) {
        moy_ui_box_t o;
        if (kind == MOY_UI_V_SEG) {
            pf_t f = font_of(&d, MOY_UI_FONT_CAPTION);
            o = seg_box(&d, &f, b, r, &g, i, tscale(s));
        } else if (kind == MOY_UI_V_TABS) {
            pf_t f = font_of(&d, MOY_UI_FONT_UI);
            o = tab_box(&d, &f, b, r, &g, i, tscale(s));
        } else if (kind == MOY_UI_V_SWATCH) {
            o = dot_box(b, r, &g, i);
            int h = (g.step - g.dot) / 2;
            o.x = (int16_t)(o.x - h);
            o.w = (int16_t)(o.w + 2 * h);
        } else {
            return -1;
        }
        if (x >= o.x && x < o.x + o.w) {
            return i;
        }
    }
    return -1;
}

static moy_ui_box_t on_card_mark(const pd_t *d, moy_ui_box_t b, int first, int last, int *flags,
                                 int *r);
// The keys a cap draws as a sign: the arrows, enter, the trackball rolled,
// the d-pad pressed left or right, and the trackball itself.
enum { SIGN_ENTER = 4, SIGN_BALL_LR = 5, SIGN_DPAD_LR = 6, SIGN_BALL = 7, SIGNS = 8 };
static int key_sign(moy_ui_span_t k);

int moy_ui_vrow(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags,
                const moy_ui_vrow_t *r) {
    pd_t d;
    open_pd(s, &d, r->fs, r->desk);
    const moy_ui_marks_t *m = moy_ui_marks();
    int sc = tscale(s);
    int on_card = r->card && !r->desk;
    int ink = on_card ? tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK)
                      : tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK);
    int dim = on_card ? tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_CHROME_INK_DIM)
                      : tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM);
    int lab = ink, rowfill = -1, danger = -1;
    if (r->kind == MOY_UI_VROW_DANGER) {
        // a verb that destroys: in the theme's menu danger ink where it
        // names one, else in the danger buttons' ink, which every theme
        // holds to text's contrast (a theme may make it its ink)
        moy_ui_btn_style_t bs;
        if (moy_ui_danger_ink() >= 0) {
            lab = ink = moy_ui_danger_ink();
        } else if (moy_ui_button_style(d.look, MOY_UI_K_BUTTON_DANGER, MOY_UI_REST, -1, &bs)
                       == MOY_UI_OK
                   && bs.c[1] >= 0) {
            lab = ink = bs.c[1];
        }
        danger = lab;
    }
    if (flags & MOY_UI_F_DISABLED) {
        lab = ink = dim = moy_ui_disabled() >= 0 ? moy_ui_disabled() : dim;
    } else if (flags & MOY_UI_F_FOCUS) {
        int mi = -1, mf = 0, mr = 0;
        moy_ui_box_t mb = on_card ? on_card_mark(&d, b, r->first, 0, &mf, &mr) : b;
        moy_ui_focus_in(s, mb, MOY_UI_MARK_PART, mr, mf, d.fs, NULL, &mi, 0);
        lab = mi >= 0 ? mi : ink;
        if (mi >= 0 && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT) {
            // the whole row is on the mark: its controls too take the
            // mark's ink, the mark's colour showing through them
            ink = dim = mi;
            int fc = moy_ui_token(d.look, m->color);
            rowfill = fc >= 0 ? fc : -1;
        }
    }
    int pad = pad_of(&d, r);
    vgeo_t g;
    geo(&d, b, r, &g);
    pf_t fl = font_of(&d, MOY_UI_FONT_UI), fv = font_of(&d, MOY_UI_FONT_CAPTION);
    int rh = (d.desk ? 28 : 20) * d.fs, bh = (d.desk ? 8 : 6) * d.fs;
    if (kind == MOY_UI_V_PROGRESS && b.h >= rh + bh + d.fs) {
        // a progress row given room for it: its label and percent on a
        // line, the bar the width of the row under them
        int span = r->hi > r->lo ? r->hi - r->lo : 1;
        int pct = (int)((int64_t)100 * (r->v - r->lo) / span);
        char num[8];
        int n = 0;
        if (pct >= 100) {
            num[n++] = '1';
        }
        if (pct >= 10) {
            num[n++] = (char)('0' + (pct / 10) % 10);
        }
        num[n++] = (char)('0' + pct % 10);
        num[n++] = '%';
        int vw = 0;
        if (!r->steps) {
            vw = tw(&fv, (const uint8_t *)num, (size_t)n, sc);
            text(&d, &fv, (const uint8_t *)num, (size_t)n, b.x + b.w - vw, b.y, rh, pal(&d, dim),
                 sc);
        }
        if (r->label.n) {
            size_t ln = moy_text_fit_px(fl.id, r->label.p, r->label.n, b.w - vw - 8 * d.fs, sc);
            text(&d, &fl, r->label.p, ln, b.x, b.y, rh, pal(&d, lab), sc);
        }
        int ty = b.y + rh + (d.desk ? 2 : 1) * d.fs;
        uint16_t line = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
        uint16_t dimc = pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK));
        uint16_t val_c = pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT));
        uint16_t rest = mix(line, dimc, 64);
        if (ratio_ge(val_c, rest, 3, 1)) {
            rrect(&d, b.x, ty, b.w, bh, bh / 2, 1, rest, 0, 0);
        } else {
            // a track the fill would not keep 3:1 against: an outline in
            // the dim ink on the ground (Contrast's)
            rrect(&d, b.x, ty, b.w, bh, bh / 2, 1, ground(&d), d.fs, dimc);
        }
        int fw = (int)((int64_t)b.w * (r->v - r->lo) / span);
        if (fw > 0) {
            rrect(&d, b.x, ty, fw > bh ? fw : bh, bh, bh / 2, 1,
                  pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT)), 0, 0);
        }
        return MOY_UI_OK;
    }
    // the ground the row's parts lie on: its card or its page, under the
    // edge mark's plate where the row has focus
    uint16_t rg = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    if (!on_card) {
        rg = ground(&d);
    }
    if ((flags & MOY_UI_F_FOCUS) && m->active
        && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_EDGE) {
        rg = plate_of(rg, pal(&d, moy_ui_token(d.look, m->color)), m->plate_alpha);
    }
    int right = g.right;
    int chev = pm(&d, MOY_UI_M_VALUE_ROW_CHEV, 1), gap = pm(&d, MOY_UI_M_VALUE_ROW_GAP, 1);
    int cy = b.y + (b.h - chevron_h(&d, chev)) / 2;
    uint16_t cink = pal(&d, ink), cdim = pal(&d, dim);
    switch (kind) {
        case MOY_UI_V_INFO:
        case MOY_UI_V_LINK:
        case MOY_UI_V_CYCLE: {
            int x = right;
            if (kind != MOY_UI_V_INFO) {
                x -= chev;
                chevron(&d, x, cy, chev, 1, cdim);
                x -= kind == MOY_UI_V_CYCLE ? gap + d.fs : gap;
            }
            // the value in the label's face and weight, set apart by its ink;
            // a key the hints draw as a sign, as the sign (no face's arrows)
            int sign = kind == MOY_UI_V_INFO && r->keys ? key_sign(r->value) : -1;
            if (sign >= 0 && sign <= SIGN_ENTER) {
                int kh = pm(&d, MOY_UI_M_VALUE_ROW_KEY, 1);
                int sw = sign == SIGN_ENTER ? kh + 3 * d.fs : kh;
                int ky = b.y + (b.h - kh) / 2;
                if (sign != SIGN_ENTER) {
                    // an arrow stands as tall as the value face's capitals,
                    // on their line: the letters beside it in the column
                    // ("F", "Ctrl C") are what it is read against
                    int top = ink_top(&fl), len, head;
                    kh = sw = (fl.ascent - top) * sc;
                    ky = b.y + moy_ui_fdiv(b.h - fl.line * sc, 2) + top * sc;
                    tall_size(kh / d.fs, sign, &len, &head);
                    if (len * d.fs > sw) {
                        sw = len * d.fs;
                    }
                }
                x -= sw;
                if (sign == SIGN_ENTER) {
                    enter_sign(&d, x, ky, sw, kh, cdim);
                } else {
                    arrow_sign(&d, x, ky, sw, kh, sign, 1, cdim);
                }
            } else {
                int vw = tw(&fl, r->value.p, r->value.n, sc);
                x -= vw;
                text(&d, &fl, r->value.p, r->value.n, x, b.y, b.h,
                     kind == MOY_UI_V_CYCLE ? cink : cdim, sc);
            }
            if (kind == MOY_UI_V_CYCLE) {
                x -= gap + d.fs + chev;
                chevron(&d, x, cy, chev, -1, cdim);
            }
            right = x;
            break;
        }
        case MOY_UI_V_SEG:
            for (int i = r->n - 1; i >= 0; i--) {
                moy_ui_box_t o = seg_box(&d, &fv, b, r, &g, i, sc);
                moy_ui_span_t t = option(r->value, i);
                int c = dim;
                if (i == r->cur && rowfill >= 0) {
                    // on a row its focus mark fills, the current mark would
                    // be the plate's own colour: a pill in the mark's ink,
                    // its label in the plate's colour
                    rrect(&d, o.x, o.y, o.w, o.h, (d.desk ? 6 : 4) * d.fs, 1, cink, 0, 0);
                    c = rowfill;
                } else if (i == r->cur) {
                    c = current(s, &d, o, (d.desk ? 6 : 4) * d.fs, ink, 0);
                }
                text(&d, &fv, t.p, t.n, o.x + g.gap / 2, b.y, b.h, pal(&d, c), sc);
                right = o.x;
            }
            break;
        case MOY_UI_V_TABS:
            // from the left, in the label's face; the label is the row's id
            for (int i = 0; i < r->n; i++) {
                moy_ui_box_t o = tab_box(&d, &fl, b, r, &g, i, sc);
                moy_ui_span_t t = option(r->value, i);
                int c = dim;
                if (i == r->cur) {
                    c = current(s, &d, o, (d.desk ? 6 : 4) * d.fs, lab, 0);
                }
                text(&d, &fl, t.p, t.n, o.x + g.gap / 2, b.y, b.h, pal(&d, c), sc);
            }
            return MOY_UI_OK;
        case MOY_UI_V_SWATCH: {
            // the row's ground: its focus plate, else its card or the page's
            uint16_t gnd = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
            if (rowfill >= 0) {
                gnd = pal(&d, rowfill);
            } else if (!on_card) {
                gnd = ground(&d);
            }
            // a dot in its ground's colour would be lost: then every dot of
            // the row wears a keyline in the row's ink
            int key = 0;
            for (int i = 0; i < r->n && !key; i++) {
                key = !apart(swatch(&d, colour_at(r->colours, i)), gnd);
            }
            for (int i = r->n - 1; i >= 0; i--) {
                moy_ui_box_t o = dot_box(b, r, &g, i);
                if (i == r->cur) {
                    // the current colour: a ring in the row's ink, the
                    // row's ground showing between it and the dot
                    int h = g.ring;
                    moy_ui_box_t c = {(int16_t)(o.x - h), (int16_t)(o.y - h),
                                      (int16_t)(o.w + 2 * h), (int16_t)(o.h + 2 * h)};
                    rrect(&d, c.x, c.y, c.w, c.h, c.w / 2, 0, 0, d.fs, cink);
                }
                rrect(&d, o.x, o.y, o.w, o.h, o.w / 2, 1, swatch(&d, colour_at(r->colours, i)),
                      key ? d.fs : 0, cink);
                right = o.x;
            }
            break;
        }
        case MOY_UI_V_TOGGLE: {
            int sw = pm(&d, MOY_UI_M_VALUE_ROW_SWITCH_W, 0), sh = pm(&d, MOY_UI_M_VALUE_ROW_SWITCH_H, 0);
            int x = right - sw, y = b.y + (b.h - sh) / 2, kn = sh - 2 * d.fs;
            int dk = dark(&d);
            uint16_t on_c = pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT));
            uint16_t off_c = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
            off_c = rowfill >= 0 ? off_c : hold(off_c, rg, cdim);
            uint16_t track = r->cur ? on_c : off_c;
            // one knob in both states, the mode's white (the ink in a dark
            // one): the state is the track's; an On track it would not show
            // on takes the value's ink
            uint16_t knob = dk ? cink : moy_shape_wire(0xffff);
            if (r->cur && !apart(knob, track)) {
                knob = pal(&d, tok(&d, MOY_UI_T_VALUE_INK, MOY_UI_T_SURFACE));
            }
            // Off in the On track's colour reads as On: then Off is the
            // track's outline and an ink knob
            int hollow = !r->cur && !apart(on_c, off_c);
            if (hollow) {
                knob = cink;
            }
            if (rowfill >= 0) {     // on a filled row: On is a track in its ink, Off its outline
                hollow = !r->cur;
                track = cink;
                knob = r->cur ? pal(&d, rowfill) : cink;
            }
            if (flags & MOY_UI_F_DISABLED) {
                track = mix(track, pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), 128);
            }
            if (hollow) {
                rrect(&d, x, y, sw, sh, sh / 2, 0, 0, d.fs, track);
            } else {
                rrect(&d, x, y, sw, sh, sh / 2, 1, track, 0, 0);
            }
            rrect(&d, r->cur ? x + sw - d.fs - kn : x + d.fs, y + d.fs, kn, kn, kn / 2, 1, knob,
                  0, 0);
            const uint8_t *word = (const uint8_t *)moy_str(r->cur ? MOY_S_UI_ON : MOY_S_UI_OFF);
            size_t wn = strlen((const char *)word);
            int ww = tw(&fv, word, wn, sc);
            right = x - gap - ww;
            text_t(&d, &fv, 1, word, wn, right, b.y, b.h, cdim, sc);
            break;
        }
        case MOY_UI_V_PROGRESS:
        case MOY_UI_V_SLIDER: {
            moy_ui_box_t t = g.track;
            int dk = dark(&d);
            uint16_t line = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
            uint16_t base = dk ? line : mix(line, pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK)),
                                            90);
            uint16_t val_c = rowfill >= 0 ? cink : pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT));
            if (rowfill >= 0) {
                base = cink;        // on a filled row the rest is the track's outline
            } else {
                base = hold(base, rg, cdim);
            }
            // the rest of the track in the value's colour reads as filled:
            // then it is the track's outline
            int hollow = !apart(base, val_c);
            int span = r->hi > r->lo ? r->hi - r->lo : 1;
            if (kind == MOY_UI_V_SLIDER && r->steps) {
                int pitch, x0 = step_geo(&d, &g, r, &pitch);
                int sw = pitch - 2 * d.fs, sh = 8 * d.fs, sy = b.y + (b.h - sh) / 2;
                uint16_t on = (flags & MOY_UI_F_DISABLED) ? cdim : val_c;
                sw = sw > 1 ? sw : 1;
                for (int i = 0; i < span; i++) {
                    if (i < r->v - r->lo) {
                        fill(&d, x0 + i * pitch, sy, sw, sh, on);
                    } else if (hollow) {
                        rrect(&d, x0 + i * pitch, sy, sw, sh, 0, 0, 0, d.fs, base);
                    } else {
                        fill(&d, x0 + i * pitch, sy, sw, sh, base);
                    }
                }
                right = x0;
                break;
            }
            int fw = (int)((int64_t)t.w * (r->v - r->lo) / span);
            if (hollow) {
                rrect(&d, t.x, t.y, t.w, t.h, d.fs, 0, 0, d.fs, base);
            } else {
                rrect(&d, t.x, t.y, t.w, t.h, d.fs, 1, base, 0, 0);
            }
            rrect(&d, t.x, t.y, fw, t.h, d.fs, 1, (flags & MOY_UI_F_DISABLED) ? cdim : val_c, 0,
                  0);
            int kw = 7 * d.fs, kh = 9 * d.fs;
            if (kind == MOY_UI_V_SLIDER) {
                rrect(&d, t.x + fw - kw / 2, t.y + t.h / 2 - kh / 2, kw, kh, 2 * d.fs, 1,
                      dk ? cink : val_c, 0, 0);
            } else {
                kw = 0;
            }
            char num[12];
            int n = 0;
            {
                int32_t v = kind == MOY_UI_V_SLIDER ? r->v
                            : (int32_t)((int64_t)100 * (r->v - r->lo) / span);
                char tmp[12];
                int k = 0, neg = v < 0;
                uint32_t u = neg ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
                do {
                    tmp[k++] = (char)('0' + u % 10u);
                    u /= 10u;
                } while (u);
                if (neg) {
                    num[n++] = '-';
                }
                while (k) {
                    num[n++] = tmp[--k];
                }
            }
            if (kind == MOY_UI_V_PROGRESS) {
                num[n++] = '%';
            }
            int vw = tw(&fv, (const uint8_t *)num, (size_t)n, sc);
            right = t.x - gap - kw / 2 - vw;
            text(&d, &fv, (const uint8_t *)num, (size_t)n, right, b.y, b.h, cdim, sc);
            break;
        }
        default:
            break;
    }
    int tx = b.x + pad;
    if (r->icon_slot >= 0 || r->icon_glyph >= 0) {
        int side = 16 * d.fs;
        moy_ui_box_t ib = {(int16_t)tx, (int16_t)(b.y + moy_ui_fdiv(b.h - side, 2)),
                           (int16_t)side, (int16_t)side};
        // a focus that took a danger row's label off its red (red on the
        // mark would not read as text) leaves the icon in it where the red
        // holds 3:1 on the mark: the row still says that it destroys
        int ic = lab;
        if (danger >= 0 && lab != danger && (flags & MOY_UI_F_FOCUS)
            && !(flags & MOY_UI_F_DISABLED)
            && ratio_ge(pal(&d, danger), rowfill >= 0 ? pal(&d, rowfill) : rg, 3, 1)) {
            ic = danger;
        }
        if (icon_d(&d, r->icon_slot, r->icon_glyph, ib, ic)) {
            tx += side + pm(&d, MOY_UI_M_VALUE_ROW_GAP, 1);
        }
    }
    int room = right - gap - tx;
    if (r->label.n && room > 0) {
        size_t ln = moy_text_fit_px(fl.id, r->label.p, r->label.n, room, sc);
        text(&d, &fl, r->label.p, ln, tx, b.y, b.h, pal(&d, lab), sc);
    }
    return MOY_UI_OK;
}

static const uint8_t s_dots[] = {0xE2, 0x80, 0xA6};
static void text_cut(pd_t *d, const pf_t *f, moy_ui_span_t t, int x, int y, int h, int room,
                     uint16_t c, int sc);

// Text centred across x..x+w in the rows y..y+h, cut to `room` with an
// ellipsis where the font has one.
static void centred_cut(pd_t *d, const pf_t *f, moy_ui_span_t t, int x, int w, int y, int h,
                        int room, uint16_t c, int sc) {
    int ell = 0;
    size_t k = moy_text_cut_px(f->id, t.p, t.n, room, sc, &ell);
    int tw_ = tw(f, t.p, k, sc) + (ell ? tw(f, s_dots, sizeof(s_dots), sc) : 0);
    int tx = x + (w - tw_) / 2;
    int at = text(d, f, t.p, k, tx, y, h, c, sc);
    if (ell) {
        text(d, f, s_dots, sizeof(s_dots), tx + at, y, h, c, sc);
    }
}

// -- cards (ui.card) -------------------------------------------------------------------

// The card's picture radius at the scale, under the depth's card radius.
static int card_r(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    int r = pm(d, MOY_UI_M_VALUE_ROW_CARD_R, 1), cr = dp->card_r * d->fs;
    return cr < r ? cr : r;
}

// The colour index a tone's text takes on the page's ground (MOY_UI_TONE_*).
static int tone_ink(const pd_t *d, int tone) {
    if (tone == MOY_UI_TONE_DIM) {
        return tok(d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM);
    }
    if (tone == MOY_UI_TONE_VALUE) {
        return tok(d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);
    }
    if (tone == MOY_UI_TONE_DANGER) {
        moy_ui_btn_style_t bs;
        if (moy_ui_danger_ink() >= 0) {
            return moy_ui_danger_ink();
        }
        if (moy_ui_button_style(d->look, MOY_UI_K_BUTTON_DANGER, MOY_UI_REST, -1, &bs)
                == MOY_UI_OK
            && bs.c[1] >= 0) {
            return bs.c[1];
        }
        return tok(d, MOY_UI_T_DANGER, MOY_UI_T_INK);
    }
    return tok(d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK);
}

int moy_ui_card(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_box_t pic, moy_ui_span_t title,
                moy_ui_span_t sub, int flags, int fs, int desk, int tone, moy_ui_box_t *content,
                uint16_t *gnd) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_marks_t *m = moy_ui_marks();
    int r = card_r(&d);
    uint16_t gc = ground(&d);
    // a solid current plate (Contrast's) inverts the labels on it; behind,
    // it is its outline and the ground stays
    int solid = m->cur_style == MOY_UI_CUR_PLATE && m->cur_alpha >= 255;
    int cur_ink = -1;
    if (flags & MOY_UI_F_ON) {
        int pr = pm(&d, MOY_UI_M_VALUE_ROW_PLATE_R, 1);
        cur_ink = current(s, &d, b, pr, -1, 0);
        int alpha = moy_ui_marks_shown() ? m->cur_alpha : m->cur_alpha * 6 / 10;
        if (m->cur_style == MOY_UI_CUR_PLATE && (moy_ui_marks_shown() || !solid)) {
            gc = mix(gc, pal(&d, moy_ui_token(d.look, m->cur_color)), alpha);
        } else if (m->cur_style == MOY_UI_CUR_FILL && m->active) {
            gc = pal(&d, tok(&d, MOY_UI_T_SELECTION, MOY_UI_T_ACCENT));
        }
    }
    *content = pic;
    if ((flags & MOY_UI_F_FOCUS) && m->active) {
        int mi = -1;
        moy_ui_focus_in(s, pic, MOY_UI_MARK_CARD, r, 0, d.fs, content, &mi, 0);
    }
    *gnd = gc;
    // the title and the sub line, centred in the band under the picture
    int sc = tscale(s);
    int th = pm(&d, MOY_UI_M_VALUE_ROW_CARD_TITLE, 1);
    int y = pic.y + pic.h + pm(&d, MOY_UI_M_VALUE_ROW_CARD_TOP, 1) / 2;
    int room = b.w - 2 * d.fs;
    int ink = tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK);
    int dim = tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM);
    if ((flags & MOY_UI_F_ON) && solid && moy_ui_marks_shown() && cur_ink >= 0) {
        ink = dim = cur_ink;
    }
    if (tone != MOY_UI_TONE_INK && tone != MOY_UI_TONE_DIM && tone != MOY_UI_TONE_LOCKED
        && dim != ink) {
        dim = tone_ink(&d, tone);
        if (!sub.n && tone == MOY_UI_TONE_VALUE) {
            ink = dim;                  // a card with no sub line: its title takes the tone
        }
    }
    pf_t ft = font_of(&d, MOY_UI_FONT_UI), fsub = font_of(&d, MOY_UI_FONT_CAPTION);
    int sh = fsub.line * sc + 4 * d.fs;
    // on the handheld the sub line sits under the title's line, a desk's a
    // band below it
    int sy = y + th - d.fs - (d.desk ? 0 : 4 * d.fs);
    // a title wider than the card breaks at its last word that fits, onto a
    // second line, where the card's foot holds the line and the sub under it
    size_t head = title.n;
    int tl = th - (d.desk ? 4 : 2) * d.fs;   // the second line's step
    if ((flags & MOY_UI_F_WRAP) && title.n && room > 0 && tw(&ft, title.p, title.n, sc) > room
        && sy + tl + (sub.n ? 2 * d.fs + fsub.ascent * sc : 0) <= b.y + b.h) {
        head = 0;
        for (size_t i = 1; i < title.n; i++) {
            if (title.p[i] == ' ' && tw(&ft, title.p, i, sc) <= room) {
                head = i;
            }
        }
    }
    if (head > 0 && head < title.n) {
        moy_ui_span_t a = {title.p, (uint16_t)head};
        moy_ui_span_t z = {title.p + head + 1, (uint16_t)(title.n - head - 1)};
        centred_cut(&d, &ft, a, b.x, b.w, y, th, room, pal(&d, ink), sc);
        centred_cut(&d, &ft, z, b.x, b.w, y + tl, th, room, pal(&d, ink), sc);
        sy += tl;
    } else if (title.n && room > 0) {
        centred_cut(&d, &ft, title, b.x, b.w, y, th, room, pal(&d, ink), sc);
    }
    if (sub.n && room > 0 && sy + 2 * d.fs + fsub.ascent * sc <= b.y + b.h) {  // where the foot holds it
        centred_cut(&d, &fsub, sub, b.x, b.w, sy, sh, room, pal(&d, dim), sc);
    }
    return MOY_UI_OK;
}

// The integer square root of v.
static int isqrt(int32_t v) {
    int32_t x = 0, bit = 1 << 30;
    while (bit > v) {
        bit >>= 2;
    }
    while (bit) {
        if (v >= x + bit) {
            v -= x + bit;
            x = (x >> 1) + bit;
        } else {
            x >>= 1;
        }
        bit >>= 2;
    }
    return (int)x;
}

// The pixels of box c's corners outside radius r, blended toward `g` by how
// much of each lies outside the arc.
static void corners(pd_t *d, moy_ui_box_t c, int r, uint16_t g) {
    if (r <= 0) {
        return;
    }
    moy_text_target_t *t = &d->t;
    for (int k = 0; k < 4; k++) {
        int right = k == 1 || k == 2, bottom = k >= 2;
        for (int j = 0; j < r; j++) {
            for (int i = 0; i < r; i++) {
                // the pixel's centre from the arc's centre, in sixteenths
                int dx = (r - i) * 16 - 8, dy = (r - j) * 16 - 8;
                int dist = isqrt(dx * dx + dy * dy);
                int out = dist - (r * 16 - 8);  // past the arc, in sixteenths
                if (out <= 0) {
                    continue;
                }
                int k255 = out >= 16 ? 255 : out * 255 / 16;
                int x = (right ? c.x + c.w - 1 - i : c.x + i) - t->cam_x;
                int y = (bottom ? c.y + c.h - 1 - j : c.y + j) - t->cam_y;
                if (x < t->cx0 || x >= t->cx1 || y < t->cy0 || y >= t->cy1) {
                    continue;
                }
                uint16_t *p = &t->px[(size_t)y * (size_t)t->dw + (size_t)x];
                *p = mix(*p, g, k255);
            }
        }
    }
    d->row->st[MOY_ST_N_FILL]++;
}

int moy_ui_card_end(const moy_ui_sink_t *s, moy_ui_box_t pic, moy_ui_box_t content, int flags,
                    uint16_t gnd, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int r = card_r(&d);
    corners(&d, content, r, gnd);
    rrect(&d, content.x, content.y, content.w, content.h, r, 0, 0, d.fs,
          pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)));
    if ((flags & MOY_UI_F_FOCUS) && moy_ui_marks()->active) {
        int mi = -1;
        moy_ui_focus_in(s, pic, MOY_UI_MARK_CARD, r, MOY_UI_MARK_AFTER, d.fs, NULL, &mi, 0);
    }
    return MOY_UI_OK;
}

int moy_ui_card_badge(const moy_ui_sink_t *s, moy_ui_box_t content, int slot, int glyph,
                      int tone, uint16_t gnd, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    moy_text_target_t *t = &d.t;
    if (tone == MOY_UI_TONE_DANGER) {
        // the cover veiled: what the console cannot take stands back
        for (int y = content.y; y < content.y + content.h; y++) {
            int ty = y - t->cam_y;
            if (ty < t->cy0 || ty >= t->cy1) {
                continue;
            }
            for (int x = content.x; x < content.x + content.w; x++) {
                int tx = x - t->cam_x;
                if (tx >= t->cx0 && tx < t->cx1) {
                    uint16_t *p = &t->px[(size_t)ty * (size_t)t->dw + (size_t)tx];
                    *p = mix(*p, gnd, 140);
                }
            }
        }
        d.row->st[MOY_ST_N_FILL]++;
    }
    if (tone == MOY_UI_TONE_LOCKED && slot < 0 && glyph < 0) {
        static const uint8_t lock[] = "lock";
        slot = moy_ui_icon_slot(lock, 4);
        glyph = moy_ui_icon_glyph(lock, 4);
    }
    if (slot < 0 && glyph < 0) {
        return MOY_UI_OK;
    }
    int dd = (d.desk ? 26 : 18) * d.fs, o = (d.desk ? 16 : 11) * d.fs;
    moy_ui_box_t disc = {(int16_t)(content.x + content.w - o - dd / 2),
                         (int16_t)(content.y + o - dd / 2), (int16_t)dd, (int16_t)dd};
    moy_ui_soft(s, disc, dd / 2, 102);
    uint16_t df = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    int ring = 0;
    uint16_t rc = 0;
    if (tone == MOY_UI_TONE_LOCKED && dark(&d)) {
        // the veiled tile is dark too: a raised disc, ringed
        ring = d.fs;
        if (solid_marks()) {
            df = ground(&d);
            rc = pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK));
        } else {
            df = pal(&d, tok(&d, MOY_UI_T_SURFACE_ALT, MOY_UI_T_SURFACE));
            rc = mix(pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)),
                     pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK)), 140);
        }
    }
    rrect(&d, disc.x, disc.y, disc.w, disc.h, dd / 2, 1, df, ring, rc);
    icon_d(&d, slot, glyph, disc,
           tone == MOY_UI_TONE_DANGER ? tone_ink(&d, tone) : tone_ink(&d, MOY_UI_TONE_VALUE));
    return MOY_UI_OK;
}

int moy_ui_card_blank(const moy_ui_sink_t *s, moy_ui_box_t content, int slot, int glyph,
                      int tone, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int v = tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);
    uint16_t surf = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    int solid = solid_marks();
    uint16_t tile = mix(surf, pal(&d, v), dark(&d) ? 77 : 46);
    if (tone == MOY_UI_TONE_LOCKED) {
        int dis = moy_ui_disabled();
        v = dis >= 0 ? dis : tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK);
        if (solid) {
            tile = ground(&d);
            v = tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK);
        } else if (dark(&d)) {
            tile = mix(pal(&d, tok(&d, MOY_UI_T_SURFACE_ALT, MOY_UI_T_SURFACE)), ground(&d), 128);
        } else {
            tile = pal(&d, tok(&d, MOY_UI_T_SUNK, MOY_UI_T_SURFACE));
        }
    } else if (solid) {
        tile = pal(&d, v);
        v = tok(&d, MOY_UI_T_VALUE_INK, MOY_UI_T_PANEL);
    }
    fill(&d, content.x, content.y, content.w, content.h, tile);
    if (tone == MOY_UI_TONE_LOCKED && solid) {
        rrect(&d, content.x, content.y, content.w, content.h, card_r(&d), 0, 0, 2 * d.fs,
              pal(&d, v));
    }
    uint32_t f24 = moy_ui_icon_font(1), f16 = moy_ui_icon_font(0);
    uint32_t font = f24 ? f24 : f16;
    int half = (content.w < content.h ? content.w : content.h) / 2;
    if (slot >= 0 && font && !d.run && moy_ui_cmask() != 63) {
        if (font == f24 && half < 24 && f16) {
            font = f16;
        }
        int size = font == f24 ? 24 : 16;
        int sc = half / size > 1 ? half / size : 1;
        int side = size * sc;
        moy_text_glyph(&d.t, font, MOY_UI_ICON_FIRST + slot, content.x + (content.w - side) / 2,
                       content.y + (content.h - side) / 2, pal(&d, v), sc);
        d.row->st[MOY_ST_N_TEXT]++;
        return MOY_UI_OK;
    }
    icon_d(&d, slot, glyph, content, v);
    return MOY_UI_OK;
}

int moy_ui_tile(const moy_ui_sink_t *s, moy_ui_box_t b, int slot, int glyph, int tone, int r,
                int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    uint16_t surf = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    moy_ui_card_blank(s, b, slot, glyph, tone, fs, desk);
    corners(&d, b, r * d.fs, surf);
    if (tone == MOY_UI_TONE_LOCKED) {
        moy_ui_card_badge(s, b, -1, -1, tone, surf, fs, desk);
    }
    return MOY_UI_OK;
}

// The confetti's MOY64 colours: red, yellow, green, blue, pink, orange.
static const uint8_t s_confetti[6] = {8, 10, 11, 12, 14, 9};

int moy_ui_unlock(const moy_ui_sink_t *s, int cx, int y, moy_ui_span_t head, moy_ui_span_t name,
                  int slot, int glyph, int burst, int fs, int desk, moy_ui_box_t *out) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int sc = tscale(s);
    pf_t fh = font_of(&d, MOY_UI_FONT_CAPTION), fn = font_of(&d, MOY_UI_FONT_TITLE);
    int n = (d.desk ? 44 : 32) * d.fs, pad = (d.desk ? 10 : 6) * d.fs;
    int hw = tw(&fh, head.p, head.n, sc), nw = tw(&fn, name.p, name.n, sc);
    int w = (hw > nw ? hw : nw) + n + 3 * pad + (d.desk ? 14 : 10) * d.fs;
    if (w > d.t.dw - 16 * d.fs) {
        w = d.t.dw - 16 * d.fs;         // kept on the glass, its lines cut to it
    }
    int h = n + 2 * pad;
    moy_ui_box_t b = {(int16_t)(cx - w / 2), (int16_t)y, (int16_t)w, (int16_t)h};
    if (out != NULL) {
        *out = b;
    }
    if (burst > 0) {
        // a secret's: square bits in the MOY64 colours bursting from the
        // banner's edges, `burst` (0..255) of the way out
        int bx = b.x + b.w / 2, by = b.y + b.h / 2;
        for (int k = 0; k < 34; k++) {
            int ang = (k * 2458) % 6283;        // k times the golden angle, in milliradians
            int reach = (6 + (k * 7) % 12) * d.fs * burst / 255;
            // cos and sin by the parabola (within 6%), on [-pi, pi]
            int a = ang > 3141 ? ang - 6283 : ang;
            int sn = (4 * a * (3141 - (a < 0 ? -a : a))) / 9870;   // sin * 1000
            int ac = a + 1571 > 3141 ? a + 1571 - 6283 : a + 1571;
            int cs = (4 * ac * (3141 - (ac < 0 ? -ac : ac))) / 9870;
            int ex = bx + cs * (b.w / 2 + reach) / 1000;
            int ey = by + sn * (b.h / 2 + reach * 4 / 5) / 1000;
            int dd = (k % 3 ? 3 : 2) * d.fs;
            fill(&d, ex - dd / 2, ey - dd / 2, dd, dd, pal(&d, s_confetti[k % 6]));
        }
    }
    moy_ui_modal(s, b, (moy_ui_span_t){NULL, 0}, fs, desk, 0);
    moy_ui_box_t t = {(int16_t)(b.x + pad), (int16_t)(b.y + pad), (int16_t)n, (int16_t)n};
    moy_ui_tile(s, t, slot, glyph, MOY_UI_TONE_INK, d.desk ? 8 : 6, fs, desk);
    int tx = b.x + pad + n + pad + 2 * d.fs;
    int room = b.x + b.w - pad - tx;
    uint16_t hc = pal(&d, solid_marks() ? tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)
                                        : tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT));
    int mid = b.y + h / 2;
    text_cut(&d, &fh, head, tx, mid - (d.desk ? 3 : 2) * d.fs - fh.ascent * sc, fh.line * sc,
             room, hc, sc);
    text_cut(&d, &fn, name, tx, mid + (d.desk ? 19 : 14) * d.fs - fn.ascent * sc, fn.line * sc,
             room, pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)), sc);
    return MOY_UI_OK;
}

// -- text blocks, empty pages, toasts ------------------------------------------------------

#define TEXT_LINES 16

int moy_ui_text_block(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t, int role,
                      int tone, int lines, int centre, int slot, int glyph, int fs, int desk,
                      int draw) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    pf_t f = font_of(&d, role);
    int sc = tscale(s);
    int lh = f.line * sc - (d.desk ? 0 : 2 * d.fs);
    moy_ui_box_t ib = {b.x, b.y, 0, 0};
    if (slot >= 0 || glyph >= 0) {
        int side = 16 * d.fs, gap = (d.desk ? 6 : 4) * d.fs;
        ib.w = ib.h = (int16_t)side;
        ib.y = (int16_t)(b.y + moy_ui_fdiv(lh - side, 2));
        b.x = (int16_t)(b.x + side + gap);
        b.w = (int16_t)(b.w - side - gap);
    }
    moy_text_line_t ln[TEXT_LINES];
    int n = 1;
    lines = lines < 1 ? 1 : lines > TEXT_LINES ? TEXT_LINES : lines;
    if (lines > 1 && t.n > 0) {
        n = moy_text_wrap(f.id, t.p, t.n, b.w / sc, MOY_WRAP_WORDS_PX, ln, TEXT_LINES);
        n = n < 1 ? 1 : n;
    }
    int shown = n < lines ? n : lines;
    if (!draw || t.n == 0) {
        return shown * lh;
    }
    uint16_t c = pal(&d, tone_ink(&d, tone));
    if (ib.w > 0) {
        icon_d(&d, slot, glyph, ib, tone_ink(&d, tone));
    }
    uint8_t buf[256];
    for (int i = 0; i < shown; i++) {
        moy_ui_span_t part = t;
        if (lines > 1 && i < TEXT_LINES) {
            if (i == shown - 1 && n > shown) {
                // the last line shown: the rest of the text, cut with an ellipsis
                part.p = t.p + ln[i].off;
                part.n = (uint16_t)(t.n - ln[i].off);
            } else if (ln[i].collapse && ln[i].len <= sizeof(buf)) {
                part.p = buf;
                part.n = (uint16_t)moy_text_collapse(t.p, &ln[i], buf);
            } else {
                part.p = t.p + ln[i].off;
                part.n = ln[i].len;
            }
        }
        if (centre) {
            centred_cut(&d, &f, part, b.x, b.w, b.y + i * lh, lh, b.w, c, sc);
        } else {
            text_cut(&d, &f, part, b.x, b.y + i * lh, lh, b.w, c, sc);
        }
    }
    return shown * lh;
}

int moy_ui_empty(const moy_ui_sink_t *s, moy_ui_box_t b, int slot, int glyph, moy_ui_span_t title,
                 const uint8_t *lines, size_t n, int fs, int desk, int draw) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int sc = tscale(s);
    int k = d.fs;
    int dd = (d.desk ? 72 : 52) * k;
    pf_t ft = font_of(&d, MOY_UI_FONT_TITLE), fl = font_of(&d, MOY_UI_FONT_CAPTION);
    // baselines: the title's under the disc, then a line's each pitch
    int tb = b.y + dd + (d.desk ? 32 : 22) * k;
    int lb = tb + (d.desk ? 22 : 17) * k, pitch = (d.desk ? 20 : 15) * k;
    int count = 0;
    for (size_t i = 0, from = 0; i <= n; i++) {
        if (i == n || lines[i] == 0) {
            count += i > from || i < n;
            from = i + 1;
        }
    }
    int end = (count ? lb + (count - 1) * pitch : tb) + pitch;
    if (!draw) {
        return end - b.y;
    }
    uint16_t g = ground(&d);
    int v = tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);
    uint16_t disc = dark(&d) ? pal(&d, tok(&d, MOY_UI_T_SURFACE_ALT, MOY_UI_T_SURFACE))
                             : mix(g, pal(&d, v), 26);
    moy_ui_box_t db = {(int16_t)(b.x + (b.w - dd) / 2), b.y, (int16_t)dd, (int16_t)dd};
    rrect(&d, db.x, db.y, db.w, db.h, dd / 2, 1, disc, 0, 0);
    icon_d(&d, slot, glyph, db, v);
    centred_cut(&d, &ft, title, b.x, b.w, tb - ft.ascent * sc, ft.line * sc, b.w,
                pal(&d, tone_ink(&d, MOY_UI_TONE_INK)), sc);
    uint16_t dim = pal(&d, tone_ink(&d, MOY_UI_TONE_DIM));
    int y = lb;
    for (size_t i = 0, from = 0; i <= n; i++) {
        if (i == n || lines[i] == 0) {
            if (i > from || i < n) {
                moy_ui_span_t l = {lines + from, (uint16_t)(i - from)};
                centred_cut(&d, &fl, l, b.x, b.w, y - fl.ascent * sc, fl.line * sc, b.w, dim, sc);
                y += pitch;
            }
            from = i + 1;
        }
    }
    return end - b.y;
}

int moy_ui_toast(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t text_, int slot, int glyph,
                 int fs, int desk, int flat, moy_ui_box_t *out) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int sc = tscale(s);
    pf_t f = font_of(&d, MOY_UI_FONT_UI);
    int h = (d.desk ? 30 : 20) * d.fs, pad = (d.desk ? 12 : 8) * d.fs;
    int side = 16 * d.fs, gap = 6 * d.fs;
    int room = b.w - 2 * pad - side - gap;
    int ell = 0;
    size_t k = moy_text_cut_px(f.id, text_.p, text_.n, room, sc, &ell);
    int w = 2 * pad + side + gap + tw(&f, text_.p, k, sc)
            + (ell ? tw(&f, s_dots, sizeof(s_dots), sc) : 0);
    moy_ui_box_t p = {(int16_t)(b.x + (b.w - w) / 2), (int16_t)(b.y + b.h - h), (int16_t)w,
                      (int16_t)h};
    if (out != NULL) {
        *out = p;
    }
    uint16_t g = ground(&d);
    if (!flat) {
        moy_ui_soft(s, p, h / 2, 153);
    }
    rrect(&d, p.x, p.y, p.w, p.h, h / 2, 1,
          mix(g, pal(&d, tone_ink(&d, MOY_UI_TONE_INK)), 240), 0, 0);
    moy_ui_box_t ib = {(int16_t)(p.x + pad), (int16_t)(p.y + (h - side) / 2), (int16_t)side,
                       (int16_t)side};
    int gi = tok(&d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE);
    icon_d(&d, slot, glyph, ib, gi);
    text_cut(&d, &f, text_, p.x + pad + side + gap, p.y, h, room, g, sc);
    return MOY_UI_OK;
}

// -- the perf HUD --------------------------------------------------------------------------

// A number of tenths as digits ("33.0" with `point`, else whole), into out;
// its length.
static int tenths_of(char *out, int v, int point) {
    if (v < 0) {
        v = 0;
    }
    if (!point) {
        v = (v + 5) / 10;
    }
    char t[8];
    int n = 0;
    int w = point ? v / 10 : v;
    do {
        t[n++] = (char)('0' + w % 10);
        w /= 10;
    } while (w && n < 6);
    int k = 0;
    while (n) {
        out[k++] = t[--n];
    }
    if (point) {
        out[k++] = '.';
        out[k++] = (char)('0' + v % 10);
    }
    return k;
}

// The i-th of the NUL-separated `words`.
static moy_ui_span_t word_at(moy_ui_span_t words, int i) {
    size_t at = 0;
    for (size_t j = 0; j <= words.n; j++) {
        if (j == words.n || words.p[j] == 0) {
            if (i-- == 0) {
                return (moy_ui_span_t){words.p + at, (uint16_t)(j - at)};
            }
            at = j + 1;
        }
    }
    return (moy_ui_span_t){words.p, 0};
}

// A part's fill: its hue; Contrast's the ink solid, hatched and an outline.
static void perf_fill(pd_t *d, int i, int x, int y, int w, int h, uint16_t c, int solid) {
    if (w <= 0 || h <= 0) {
        return;
    }
    if (!solid) {
        fill(d, x, y, w, h, c);
    } else if (i == 0) {
        fill(d, x, y, w, h, c);
    } else if (i == 1) {
        for (int yy = 0; yy < h; yy++) {
            for (int xx = (3 - yy % 3) % 3; xx < w; xx += 3) {
                fill(d, x + xx, y + yy, d->fs, 1, c);
            }
        }
    } else {
        rrect(d, x, y, w, h, 0, 0, 0, d->fs, c);
    }
}

int moy_ui_perf(const moy_ui_sink_t *s, int right, int bottom, int fps, const int16_t *parts,
                int frame, int detail, moy_ui_span_t words, int fs, int desk, int draw,
                moy_ui_box_t *out) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int sc = tscale(s), k = d.fs;
    int solid = solid_marks(), dk = dark(&d);
    pf_t fm = font_of(&d, MOY_UI_FONT_MONO), fu = font_of(&d, MOY_UI_FONT_UI);
    uint16_t surf = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    uint16_t ink = pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK));
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_CHROME_INK_DIM));
    uint16_t line = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
    uint16_t edge = solid ? ink : mix(line, dim, 77);
    // the chip: a flat pill, the number in the code face's digits, "fps" dim
    char num[8];
    int nn = tenths_of(num, fps * 10, 0);
    moy_ui_span_t unit = word_at(words, 5);
    int ch = (d.desk ? 26 : 16) * k, cpad = (d.desk ? 10 : 6) * k, gap = 3 * k;
    int nw = tw(&fm, (const uint8_t *)num, (size_t)nn, sc);
    int cw = cpad + nw + gap + tw(&fu, unit.p, unit.n, sc) + cpad;
    moy_ui_box_t chip = {(int16_t)(right - cw), (int16_t)(bottom - ch), (int16_t)cw, (int16_t)ch};
    moy_ui_box_t all = chip;
    // the card over it: the frame against its budget, then each part's row
    int w = (d.desk ? 176 : 150) * k, rh = (d.desk ? 20 : 13) * k, pad = (d.desk ? 10 : 7) * k;
    int bh = (d.desk ? 6 : 4) * k, bgap = (d.desk ? 8 : 6) * k;
    int h = pad + rh + bh + bgap + 4 * rh + pad;
    moy_ui_box_t card = {(int16_t)(right - w), (int16_t)(chip.y - (d.desk ? 8 : 4) * k - h),
                         (int16_t)w, (int16_t)h};
    if (detail) {
        all.x = card.x < chip.x ? card.x : chip.x;
        all.w = (int16_t)(right - all.x);
        all.y = card.y;
        all.h = (int16_t)(bottom - card.y);
    }
    if (out != NULL) {
        *out = all;
    }
    if (!draw) {
        return MOY_UI_OK;
    }
    rrect(&d, chip.x, chip.y, chip.w, chip.h, ch / 2, 1, surf, k, edge);
    text(&d, &fm, (const uint8_t *)num, (size_t)nn, chip.x + cpad, chip.y, ch, ink, sc);
    text(&d, &fu, unit.p, unit.n, chip.x + cpad + nw + gap, chip.y, ch, dim, sc);
    if (!detail) {
        return MOY_UI_OK;
    }
    int r = (d.desk ? 8 : 6) * k;
    rrect(&d, card.x, card.y, card.w, card.h, r, 1, surf, solid ? 2 * k : k, solid ? ink : line);
    uint16_t hue[3] = {
        pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT)),
        moy_shape_wire(moy_theme_rgb565(MOY_THEME_RGB | (dk ? 0x5fd0c0 : 0x138a7e))),
        moy_shape_wire(moy_theme_rgb565(MOY_THEME_RGB | (dk ? 0xf0b85a : 0xb7791f))),
    };
    if (solid) {
        hue[0] = hue[1] = hue[2] = ink;
    }
    char v[8];
    int x0 = card.x + pad, x1 = card.x + w - pad, y = card.y + pad;
    moy_ui_span_t head = word_at(words, 0), ms = word_at(words, 6);
    text(&d, &fu, head.p, head.n, x0, y, rh, ink, sc);
    int msw = tw(&fm, ms.p, ms.n, sc);
    int vn = tenths_of(v, frame, 1);
    text(&d, &fm, ms.p, ms.n, x1 - msw, y, rh, ink, sc);
    int vw = tw(&fm, (const uint8_t *)v, (size_t)vn, sc) + tw(&fm, (const uint8_t *)" ", 1, sc);
    text(&d, &fm, (const uint8_t *)v, (size_t)vn, x1 - msw - vw, y, rh, ink, sc);
    y += rh;
    int bw = x1 - x0, bx = x0;
    fill(&d, x0, y, bw, bh, solid ? pal(&d, tok(&d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE))
                                  : mix(line, surf, 51));
    int budget = 333;                       // 33.3 ms, a 30 fps frame
    for (int i = 0; i < 3; i++) {
        int seg = bw * (parts[i] < 0 ? 0 : parts[i]) / budget;
        seg = bx + seg > x1 ? x1 - bx : seg;
        perf_fill(&d, i, bx, y, seg - k, bh, hue[i], solid);
        bx += seg;
    }
    y += bh + bgap;
    int sw = (d.desk ? 8 : 6) * k;
    for (int i = 0; i < 4; i++) {
        if (i < 3) {
            perf_fill(&d, i, x0, y + (rh - sw) / 2, sw, sw, hue[i], solid);
        }
        moy_ui_span_t nm = word_at(words, i + 1);
        text(&d, &fu, nm.p, nm.n, x0 + sw + (d.desk ? 8 : 5) * k, y, rh, solid ? ink : dim, sc);
        vn = tenths_of(v, parts[i], 1);
        int w2 = tw(&fm, (const uint8_t *)v, (size_t)vn, sc);
        text(&d, &fm, (const uint8_t *)v, (size_t)vn, x1 - w2, y, rh, ink, sc);
        y += rh;
    }
    return MOY_UI_OK;
}

// -- a month --------------------------------------------------------------------------

int moy_ui_month_h(int weeks, int fs, int desk) {
    int k = fs < 1 ? 1 : fs;
    return ((desk ? 30 : 22) + (desk ? 22 : 26) + weeks * (desk ? 30 : 21)) * k;
}

int moy_ui_month(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title,
                 moy_ui_span_t days, int first, int n, int today, int chevrons, int fs, int desk,
                 moy_ui_box_t chev[2]) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int sc = tscale(s), k = d.fs;
    pf_t ft = font_of(&d, MOY_UI_FONT_TITLE), fc = font_of(&d, MOY_UI_FONT_CAPTION);
    pf_t fu = font_of(&d, MOY_UI_FONT_UI);
    int head = (d.desk ? 30 : 22) * k, cap = (d.desk ? 22 : 26) * k;
    int pitch = (d.desk ? 30 : 21) * k;
    uint16_t ink = pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK));
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_CHROME_INK_DIM));
    int cw = b.w / 7, gx = b.x + (b.w - 7 * cw) / 2;
    int room = b.w;
    if (chevrons) {
        // the two chevrons at the head's right, each a tap's square
        int side = (d.desk ? 24 : 26) * k;
        for (int i = 0; i < 2; i++) {
            moy_ui_box_t cb = {(int16_t)(b.x + b.w - (2 - i) * side), b.y, (int16_t)side,
                               (int16_t)head};
            if (chev != NULL) {
                chev[i] = cb;
            }
            static const uint8_t back[] = "back", fwd[] = "forward";
            const uint8_t *nm = i ? fwd : back;
            size_t nn = i ? 7 : 4;
            moy_ui_box_t ib = {(int16_t)(cb.x + (cb.w - 16 * k) / 2),
                               (int16_t)(cb.y + (cb.h - 16 * k) / 2), (int16_t)(16 * k),
                               (int16_t)(16 * k)};
            icon_d(&d, moy_ui_icon_slot(nm, nn), moy_ui_icon_glyph(nm, nn), ib,
                   tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK));
        }
        room = b.w - 2 * side;
    }
    text_cut(&d, &ft, title, gx, b.y, head, room - (gx - b.x), ink, sc);
    // the weekdays' initials, one a column
    size_t at = 0;
    for (int i = 0; i < 7 && at < days.n; i++) {
        size_t e = at + 1;
        while (e < days.n && (days.p[e] & 0xC0) == 0x80) {
            e++;
        }
        int w = tw(&fc, days.p + at, e - at, sc);
        text(&d, &fc, days.p + at, e - at, gx + i * cw + (cw - w) / 2, b.y + head, cap, dim, sc);
        at = e;
    }
    int gy = b.y + head + cap;
    int disc = (d.desk ? 28 : 20) * k;
    disc = disc > cw ? cw : disc;
    int solid = solid_marks();
    for (int day = 1; day <= n; day++) {
        int i = first + day - 1;
        int col = i % 7, row = i / 7;
        int cx = gx + col * cw, cy = gy + row * pitch;
        char num[3];
        int nl = day < 10 ? 1 : 2;
        num[0] = (char)(day < 10 ? '0' + day : '0' + day / 10);
        num[1] = (char)('0' + day % 10);
        const pf_t *f = day == today ? &ft : &fu;
        uint16_t c = col >= 5 ? dim : ink;
        if (day == today) {
            // today on a disc of `value`, its number bold in `value_ink`
            int dx = cx + (cw - disc) / 2, dy = cy + (pitch - disc) / 2;
            rrect(&d, dx, dy, disc, disc, disc / 2, 1,
                  pal(&d, solid ? tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)
                                : tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT)), 0, 0);
            c = pal(&d, solid ? tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)
                              : tok(&d, MOY_UI_T_VALUE_INK, MOY_UI_T_PANEL));
        }
        int w = tw(f, (const uint8_t *)num, (size_t)nl, sc);
        text(&d, f, (const uint8_t *)num, (size_t)nl, cx + (cw - w) / 2, cy, pitch, c, sc);
    }
    return MOY_UI_OK;
}

// -- the page ---------------------------------------------------------------------------

int moy_ui_page_ground(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                       int desk, moy_ui_box_t *content) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    fill(&d, b.x, b.y, b.w, b.h, ground(&d));
    moy_ui_box_t ct = b;
    if (title.n) {
        int sh = metric(MOY_UI_K_PANEL, MOY_UI_M_PANEL_STRIP) * d.fs;
        sh = sh > b.h ? b.h : sh;
        pf_t f = font_of(&d, MOY_UI_FONT_TITLE);
        int sc = tscale(s);
        size_t n = moy_text_fit_px(f.id, title.p, title.n, b.w - 2 * gm(MOY_UI_M_GROUP_PAD, fs),
                                   sc);
        text(&d, &f, title.p, n, b.x + gm(MOY_UI_M_GROUP_PAD, fs), b.y, sh,
             pal(&d, tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK)), sc);
        ct.y = (int16_t)(ct.y + sh);
        ct.h = (int16_t)(ct.h - sh);
    }
    if (content != NULL) {
        *content = ct;
    }
    return MOY_UI_OK;
}

int moy_ui_fade(const moy_ui_sink_t *s, moy_ui_box_t b, int ci, int up, int fs, int desk) {
    if (b.w <= 0 || b.h <= 0) {
        return MOY_UI_OK;
    }
    pd_t d;
    open_pd(s, &d, fs, desk);
    uint16_t g;
    if (ci == MOY_UI_FADE_WELL) {
        g = pal(&d, tok(&d, MOY_UI_T_LCD, MOY_UI_T_SUNK));
    } else if (ci == MOY_UI_FADE_CARD) {
        g = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    } else if (ci != MOY_UI_DEFAULT) {
        g = pal(&d, ci);
    } else {
        g = ground(&d);
    }
    moy_text_target_t *t = &d.t;
    int x0 = b.x - t->cam_x, x1 = x0 + b.w;
    x0 = x0 > t->cx0 ? x0 : t->cx0;
    x1 = x1 < t->cx1 ? x1 : t->cx1;
    for (int j = 0; j < b.h && x0 < x1; j++) {
        int k = (j + 1) * 255 / (b.h + 1);          // the edge's row nearly the colour
        int y = (up ? b.y + b.h - 1 - j : b.y + j) - t->cam_y;
        if (y < t->cy0 || y >= t->cy1) {
            continue;
        }
        // a row mostly lies over a few colours: each mixed once
        uint16_t *row = &t->px[(size_t)y * (size_t)t->dw];
        uint16_t from = row[x0], to = mix(from, g, k);
        for (int x = x0; x < x1; x++) {
            if (row[x] != from) {
                from = row[x];
                to = mix(from, g, k);
            }
            row[x] = to;
        }
    }
    d.row->st[MOY_ST_N_FILL]++;
    return MOY_UI_OK;
}

// -- a part's ground --------------------------------------------------------------------

int moy_ui_well(const moy_ui_sink_t *s, moy_ui_box_t b, int flags, int fill, int fs,
                 int desk) {
    if (b.w <= 0 || b.h <= 0) {
        return MOY_UI_OK;
    }
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_depth_t *dp = moy_ui_depth();
    const moy_ui_display_t *ds = moy_ui_display();
    int cap = desk ? 10 : 8;
    int r = (dp->key_r < cap ? dp->key_r : cap) * d.fs;
    moy_shape_paint_t p;
    memset(&p, 0, sizeof(p));
    p.n = 1;
    p.c[0] = pal(&d, fill >= 0 ? fill : tok(&d, MOY_UI_T_LCD, MOY_UI_T_SUNK));
    moy_shape_rrect_t sp;
    memset(&sp, 0, sizeof(sp));
    for (int k = 0; k < 4; k++) {
        sp.r[k] = (uint8_t)(r > 255 ? 255 : r);
    }
    sp.stepped = dp->stepped;
    sp.fill = &p;
    sp.line_lo = -1;
    if (ds->bevel != MOY_UI_BEVEL_NONE) {
        uint16_t hi = pal(&d, tok(&d, MOY_UI_T_SHADE_HI, MOY_UI_T_SURFACE));
        uint16_t lo = pal(&d, tok(&d, MOY_UI_T_SHADE_LO, MOY_UI_T_EDGE));
        int in = ds->bevel == MOY_UI_BEVEL_IN;
        sp.line = (uint8_t)d.fs;
        sp.line_hi = in ? lo : hi;          // the top and left edges
        sp.line_lo = in ? hi : lo;
    } else if (ds->border_w > 0 && set(ds->border_c)) {
        sp.line = (uint8_t)(ds->border_w * d.fs);
        sp.line_hi = val(&d, ds->border_c);
    }
    moy_shape_rrect(&d.t, b.x, b.y, b.w, b.h, &sp);
    d.row->st[MOY_ST_N_FILL]++;
    const moy_ui_marks_t *m = moy_ui_marks();
    int mi = -1;
    if (flags & MOY_UI_F_ON) {
        moy_ui_current_in(s, b, r, moy_ui_marks_shown() ? 0 : MOY_UI_MARK_BACK, d.fs, &mi, 0);
    }
    if ((flags & MOY_UI_F_FOCUS) && m->active) {
        moy_ui_focus_in(s, b, MOY_UI_MARK_AUTO, r, 0, d.fs, NULL, &mi, 0);
    }
    return MOY_UI_OK;
}

// -- groups ---------------------------------------------------------------------------

int moy_ui_group_r(int fs) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    int r = gm(MOY_UI_M_GROUP_R, fs);
    int cr = dp->card_r * (fs < 1 ? 1 : fs);
    return cr < r ? cr : r;
}

// The card's ring: the look's `card.frame`, else a dark theme rings its cards.
static int card_line(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (dp->card_line > 0) {
        return dp->card_line * d->fs;
    }
    if (dark(d)) {
        return d->fs;
    }
    // a light card in its page's ground's colour (Contrast Light's white on
    // white) is its line alone
    uint16_t g = ground(d);
    return !ratio_ge(pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), g, 21, 20)
               ? d->fs
               : 0;
}

// The card's shape from its top to `bottom` (exclusive), drawn where the clip lets it.
static void card(pd_t *d, moy_ui_box_t c, int bottom) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    uint16_t lc = dp->card_line > 0 && set(dp->card_line_c)
                      ? val(d, dp->card_line_c)
                      : pal(d, tok(d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
    rrect(d, c.x, c.y, c.w, bottom - c.y, moy_ui_group_r(d->fs), 1,
          pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), card_line(d), lc);
}

// Where a row on a card wears a focus mark that fills it (invert, or the
// edge's plate): inside the card's frame, so the frame runs past it unbroken
// (its top too on the card's first row, its foot on the last), or, for
// invert, on it where the mark's outline is as wide, the outline then
// standing for the frame, so the mark has one weight all round. The plate
// takes the card's corners on its first and last rows (*flags
// MOY_UI_MARK_TOP and MOY_UI_MARK_FOOT, *r their radius inside the frame).
static moy_ui_box_t on_card_mark(const pd_t *d, moy_ui_box_t b, int first, int last, int *flags,
                                 int *r) {
    const moy_ui_marks_t *m = moy_ui_marks();
    int st = m->style[MOY_UI_MARK_PART];
    *flags = 0;
    *r = 0;
    if (d->desk || (st != MOY_UI_FOCUS_INVERT && st != MOY_UI_FOCUS_EDGE)) {
        return b;
    }
    int f = card_line(d);
    if (st == MOY_UI_FOCUS_INVERT) {
        f -= m->outline_w > 0 && set(m->outline_c) ? m->outline_w : 0;
    } else {
        *flags = (first ? MOY_UI_MARK_TOP : 0) | (last ? MOY_UI_MARK_FOOT : 0);
        int cr = moy_ui_group_r(d->fs) - (f > 0 ? f : 0);
        *r = *flags && cr > 0 ? cr : 0;
    }
    f = f > 0 ? f : 0;
    int top = first ? f : 0, foot = last ? f : 0;
    return (moy_ui_box_t){(int16_t)(b.x + f), (int16_t)(b.y + top), (int16_t)(b.w - 2 * f),
                          (int16_t)(b.h - top - foot)};
}

static void clip_to(pd_t *d, int x, int y, int w, int h) {
    int x0 = x - d->t.cam_x, y0 = y - d->t.cam_y;
    d->t.cx0 = x0 > d->t.cx0 ? x0 : d->t.cx0;
    d->t.cy0 = y0 > d->t.cy0 ? y0 : d->t.cy0;
    d->t.cx1 = x0 + w < d->t.cx1 ? x0 + w : d->t.cx1;
    d->t.cy1 = y0 + h < d->t.cy1 ? y0 + h : d->t.cy1;
}

int moy_ui_group_band(const moy_ui_sink_t *s, moy_ui_box_t c, moy_ui_box_t row, int first,
                      int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    uint16_t line = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
    if (desk) {
        if (!first) {
            fill(&d, c.x, row.y, c.w, d.fs, line);
        }
        return MOY_UI_OK;
    }
    pd_t k = d;
    clip_to(&k, c.x, row.y, c.w, row.h);
    card(&k, c, row.y + row.h + moy_ui_group_r(fs) + 2 * d.fs);
    if (!first) {
        int pad = gm(MOY_UI_M_GROUP_PAD, fs);
        fill(&d, c.x + pad, row.y, c.w - 2 * pad, d.fs, line);
    }
    return MOY_UI_OK;
}

int moy_ui_group_end(const moy_ui_sink_t *s, moy_ui_box_t c, moy_ui_box_t last, int flags,
                     int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int r = moy_ui_group_r(fs), line = card_line(&d);
    int bottom = last.y + last.h;
    if (desk) {
        return MOY_UI_OK;
    }
    if (line > 0) {             // the ring's foot between the corners
        pd_t k = d;
        clip_to(&k, c.x + r, bottom - line, c.w - 2 * r, line);
        card(&k, c, bottom);
    }
    if (r <= 0) {
        return MOY_UI_OK;
    }
    uint16_t gc = ground(&d);
    moy_canvas_row_t *row = d.row;
    int32_t saved[4];
    memcpy(saved, &row->st[MOY_ST_CX0], sizeof(saved));
    for (int side = 0; side < 2; side++) {
        int x = side ? c.x + c.w - r : c.x;
        pd_t k = d;
        clip_to(&k, x, bottom - r, r, r);
        if (k.t.cx0 >= k.t.cx1 || k.t.cy0 >= k.t.cy1) {
            continue;
        }
        fill(&k, x, bottom - r, r, r, gc);
        card(&k, c, bottom);
        if (flags & (MOY_UI_F_FOCUS | MOY_UI_F_ON)) {
            row->st[MOY_ST_CX0] = k.t.cx0;
            row->st[MOY_ST_CY0] = k.t.cy0;
            row->st[MOY_ST_CX1] = k.t.cx1;
            row->st[MOY_ST_CY1] = k.t.cy1;
            int ink;
            if (flags & MOY_UI_F_ON) {
                moy_ui_current_in(s, last, 0, 0, fs, &ink, 0);
            }
            if (flags & MOY_UI_F_FOCUS) {
                int mf = 0, mr = 0;
                moy_ui_box_t mb = on_card_mark(&d, last, 0, 1, &mf, &mr);
                moy_ui_focus_in(s, mb, MOY_UI_MARK_PART, mr, MOY_UI_MARK_AGAIN | mf, fs, NULL,
                                &ink, 0);
            }
            memcpy(&row->st[MOY_ST_CX0], saved, sizeof(saved));
        }
    }
    return MOY_UI_OK;
}

int moy_ui_caption(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    pf_t f = font_of(&d, MOY_UI_FONT_CAPTION);
    int sc = tscale(s);
    int x = b.x + gm(MOY_UI_M_GROUP_PAD, fs);
    size_t n = moy_text_fit_px(f.id, t.p, t.n, b.w - 2 * gm(MOY_UI_M_GROUP_PAD, fs), sc);
    int h = f.line * sc;
    text(&d, &f, t.p, n, x, b.y + b.h - h - d.fs, h,
         pal(&d, tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM)), sc);
    return MOY_UI_OK;
}

int moy_ui_line(const moy_ui_sink_t *s, moy_ui_box_t b, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    fill(&d, b.x, b.y, b.w, b.h, pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)));
    return MOY_UI_OK;
}

int moy_ui_rule(const moy_ui_sink_t *s, moy_ui_box_t b, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int in = gm(MOY_UI_M_GROUP_PAD, fs);
    fill(&d, b.x + in, b.y + (b.h - d.fs) / 2, b.w - 2 * in, d.fs,
         pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)));
    return MOY_UI_OK;
}

// -- keycap hints -----------------------------------------------------------------------

static int s_input = MOY_UI_INPUT_KEYS;
static int s_keys = MOY_UI_INPUT_KEYS;

void moy_ui_set_input(int input) {
    s_input = input >= 0 && input < MOY_UI_INPUTS ? input : MOY_UI_INPUT_KEYS;
    s_keys = s_input;
    moy_ui_marks_refresh();         // touch shows no focus
}

int moy_ui_input(void) {
    return s_input;
}

void moy_ui_set_hint_input(int input) {
    s_keys = input >= 0 && input < MOY_UI_INPUTS ? input : MOY_UI_INPUT_KEYS;
}

int moy_ui_hint_input(void) {
    return s_keys;
}

// Each action's name.
static const char *const ACT_NAMES[MOY_UI_ACTS] = {"confirm", "back", "change", "tabs", "menu",
                                                   "cancel", "flip", "rotate", "shift", "clear",
                                                   "copy", "more", "search", "move", "scroll"};

// The keys each input does each action with. The T-Deck's are its keyboard's
// (native/moy_input/moy_kbd.c): Enter is `run`, K is `b` (back, as the bar's
// chevron is), W A S D are the directions, and so is a roll of the trackball
// on every surface but a running cart, a canvas and a caret, its click Enter
// (`ball_lr`, its sign; owner calls 2026-10-23 and 2026-10-26,
// Workstation.ball_steps), Q and E are the
// home's tab keys and Mic is `start`. A gamepad's are its face buttons, its
// d-pad (`dpad_lr`), its shoulders and Start. Touch has none. Cancel is a
// field's: in one the T-Deck's K types a letter, so Mic, which never types,
// cancels there (owner call, 2026-10-18). The sprite tab's operations are
// letters on a keyboard (PICO-8's), its shift the arrows where the keyboard
// has them -- the T-Deck's trackball moves the grid's cursor -- and a
// gamepad reaches them through its more menu, on Y (2026-10-19). Search is
// the slash a keyboard types: on the T-Deck the Sym+G chord (moy_kbd.c's
// symbol layer).
static const char *const ACT_KEYS[MOY_UI_INPUTS][MOY_UI_ACTS] = {
    {"enter", "Esc", "left right", "Q E", "Super", "Esc", "F V", "R", "left right up down",
     "Del", "Ctrl C", "M", "/", "up down left right", "up down"},
    {"enter", "K", "A D ball_lr", "Q E", "Mic", "Mic", "F V", "R", "", "Del", "", "M", "Sym G",
     "WASD", "W S"},
    {"A", "B", "dpad_lr", "L R", "Start", "B", "", "", "", "", "", "Y", "", "up down left right",
     "up down"},
    {"", "", "", "", "", "", "", "", "", "", "", "", "", "", ""},
};

static int span_is(moy_ui_span_t k, const char *s) {
    return s != NULL && strlen(s) == k.n && memcmp(s, k.p, k.n) == 0;
}

int moy_ui_hint_action(moy_ui_span_t k) {
    for (int a = 0; a < MOY_UI_ACTS; a++) {
        if (span_is(k, ACT_NAMES[a])) {
            return a;
        }
    }
    return -1;
}

const char *moy_ui_hint_keys(int input, int act) {
    if (input < 0 || input >= MOY_UI_INPUTS || act < 0 || act >= MOY_UI_ACTS) {
        return "";
    }
    return ACT_KEYS[input][act];
}

int moy_ui_key_moves(int input, moy_ui_span_t k) {
    return input == MOY_UI_INPUT_TDECK && k.n == 1
           && (k.p[0] == 'W' || k.p[0] == 'A' || k.p[0] == 'S' || k.p[0] == 'D');
}

static int key_sign(moy_ui_span_t k) {
    static const char *const names[SIGNS] = {"left", "right", "up", "down", "enter",
                                             "ball_lr", "dpad_lr", "ball"};
    for (int i = 0; i < SIGNS; i++) {
        if (span_is(k, names[i])) {
            return i;
        }
    }
    return -1;
}

// Whether `k` is one codepoint of UTF-8.
static int one_glyph(moy_ui_span_t k) {
    if (k.n == 0) {
        return 0;
    }
    uint8_t c = k.p[0];
    size_t n = c < 0x80 ? 1 : c < 0xE0 ? 2 : c < 0xF0 ? 3 : 4;
    return n == k.n;
}

int moy_ui_hints(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                 int fs, int desk) {
    moy_ui_hints_at(s, b, items, len, fs, desk, 1, NULL, 0);
    return MOY_UI_OK;
}

int moy_ui_hints_at(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                    int fs, int desk, int draw, int16_t *spans, int cap) {
    if (s_keys == MOY_UI_INPUT_TOUCH) {
        return 0;
    }
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_keycap_t *kc = moy_ui_keycap();
    // what they do in the rows' face (B-23); the caps' legends in the caption
    // face a step under it, or in the word's own where the caption is more
    // than a pixel smaller (the desk's), so a cap never reads a size down
    pf_t fh = font_of(&d, MOY_UI_FONT_UI), fk = font_of(&d, MOY_UI_FONT_CAPTION);
    if (fk.ascent + 1 < fh.ascent) {
        fk = fh;
    }
    int sc = tscale(s);
    int kh = pm(&d, MOY_UI_M_VALUE_ROW_KEY, 1), hgap = pm(&d, MOY_UI_M_VALUE_ROW_HINT_GAP, 1);
    int dk = dark(&d);
    uint16_t bg = ground(&d);
    uint16_t ink = pal(&d, tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK));
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM));
    uint16_t capc = set(kc->fill) ? val(&d, kc->fill) : mix(bg, ink, dk ? 41 : 26);
    uint16_t capi = set(kc->ink) ? val(&d, kc->ink) : ink;
    int r = kc->r * d.fs;
    int x = b.x + MOY_UI_HINT_LEAD(d.desk) * d.fs, y = b.y + (b.h - kh) / 2;
    size_t i = 0;
    int field = 0, pairs = 0, shown = 1;
    while (i < len) {
        size_t j = i;
        while (j < len && items[j] != 0) {
            j++;
        }
        moy_ui_span_t t = {items + i, (uint16_t)(j - i)};
        if ((field & 1) == 0) {
            if (spans != NULL && pairs < cap) {
                spans[2 * pairs] = (int16_t)x;
            }
            int act = moy_ui_hint_action(t);
            if (act >= 0) {
                const char *keys = moy_ui_hint_keys(s_keys, act);
                t.p = (const uint8_t *)keys;
                t.n = (uint16_t)strlen(keys);
            } else if (moy_ui_key_moves(s_keys, t)) {
                t.n = 0;
            }
            shown = t.n > 0;
            size_t a = 0;
            while (a < t.n) {
                size_t e = a;
                while (e < t.n && t.p[e] != ' ') {
                    e++;
                }
                moy_ui_span_t k = {t.p + a, (uint16_t)(e - a)};
                if (k.n) {
                    // a chord ("Shift+enter") is one cap: its modifiers' legend,
                    // then the last key's sign where it has one
                    moy_ui_span_t mods = {k.p, 0};
                    for (uint16_t m = k.n; m > 1; m--) {
                        if (k.p[m - 1] == '+') {
                            moy_ui_span_t last = {k.p + m, (uint16_t)(k.n - m)};
                            if (key_sign(last) >= 0) {
                                mods.n = m;
                                k = last;
                            }
                            break;
                        }
                    }
                    int sign = key_sign(k);
                    int lw = sign >= 0 ? 0 : tw(&fk, k.p, k.n, sc);
                    int mw = mods.n ? tw(&fk, mods.p, mods.n, sc) + 4 * d.fs : 0;
                    // a sign's cap and a one-glyph legend's are square, a
                    // word's grows with it; the enter sign's is a little wider
                    int w = sign == SIGN_ENTER ? kh + 3 * d.fs
                            : sign >= 0 || (one_glyph(k) && lw + 4 * d.fs <= kh)
                                ? kh
                                : lw + (d.desk ? 10 : 8) * d.fs;
                    w = (w < kh ? kh : w) + mw;
                    if (!draw) {
                        // measured only
                    } else if (kc->alpha >= 255) {
                        rrect(&d, x, y, w, kh, r, 1, capc, 0, 0);
                    } else {
                        rrect(&d, x, y, w, kh, r, 1, mix(bg, capc, kc->alpha), 0, 0);
                    }
                    if (draw && kc->border_w > 0 && set(kc->border_c)) {
                        rrect(&d, x, y, w, kh, r, 0, 0, kc->border_w * d.fs,
                              val(&d, kc->border_c));
                    }
                    if (draw && mods.n) {
                        text(&d, &fk, mods.p, mods.n, x + 4 * d.fs, y, kh, capi, sc);
                    }
                    int sx = x + mw, sw = w - mw;
                    if (!draw) {
                        // measured only
                    } else if (sign == SIGN_ENTER) {
                        enter_sign(&d, sx, y, sw, kh, capi);
                    } else if (sign == SIGN_BALL_LR) {
                        ball_sign(&d, sx, y, sw, kh, capi);
                    } else if (sign == SIGN_DPAD_LR) {
                        dpad_sign(&d, sx, y, sw, kh, capi);
                    } else if (sign == SIGN_BALL) {
                        ball_only_sign(&d, sx, y, sw, kh, capi);
                    } else if (sign >= 0) {
                        arrow_sign(&d, sx, y, sw, kh, sign, 0, capi);
                    } else {
                        text(&d, &fk, k.p, k.n, sx + (sw - lw) / 2, y, kh, capi, sc);
                    }
                    x += w + 3 * d.fs;
                }
                a = e + 1;
            }
            if (shown) {
                x += d.fs;
            }
        } else {
            if (shown) {
                x += draw ? text(&d, &fh, t.p, t.n, x, b.y, b.h, dim, sc)
                          : tw(&fh, t.p, t.n, sc);
            }
            if (spans != NULL && pairs < cap) {
                spans[2 * pairs + 1] = shown ? (int16_t)x : spans[2 * pairs];
            }
            pairs++;
            if (shown) {
                x += hgap;
            }
        }
        field++;
        i = j + 1;
    }
    return pairs;
}

// -- tables ----------------------------------------------------------------------------

// The span of field i of `p` (NUL-separated in n bytes), empty past its end.
static moy_ui_span_t field_at(const uint8_t *p, size_t n, int i) {
    moy_ui_span_t s = {p, 0};
    moy_ui_span_t all = {p, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n)};
    if (p == NULL || i < 0) {
        return s;
    }
    return option(all, i);
}

// The fields of `p` (NUL-separated in n bytes, a trailing NUL ending the last).
int moy_ui_fields(const uint8_t *p, size_t n) {
    int k = 0;
    for (size_t i = 0; i < n; i++) {
        k += p[i] == 0;
    }
    return n && p[n - 1] != 0 ? k + 1 : k;
}

int moy_ui_table_geo(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *cols, size_t cols_n,
                     const uint8_t *cells, size_t cells_n, int fs, int desk, moy_ui_tgeo_t *g) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    memset(g, 0, sizeof(*g));
    int n = moy_ui_fields(cols, cols_n);
    if (n > MOY_UI_TABLE_COLS) {
        n = MOY_UI_TABLE_COLS;
    }
    g->n = (int16_t)n;
    if (n == 0) {
        return MOY_UI_OK;
    }
    int sc = tscale(s);
    pf_t fl = font_of(&d, MOY_UI_FONT_UI), fv = font_of(&d, MOY_UI_FONT_CAPTION);
    moy_ui_span_t c0 = field_at(cols, cols_n, 0);
    g->pic = c0.n == 0 && n > 1;
    int first = g->pic ? 1 : 0;
    for (int j = first + 1; j < n; j++) {
        moy_ui_span_t h = field_at(cols, cols_n, j);
        if (h.n) {
            g->head = 1;
        }
        int w = tw(&fv, h.p, h.n, sc);
        g->w[j] = (int16_t)w;
    }
    if (field_at(cols, cols_n, first).n) {
        g->head = 1;
    }
    // the widest cell of each column after the first text column
    int cells_total = moy_ui_fields(cells, cells_n);
    size_t at = 0;
    for (int i = 0; i < cells_total && at <= cells_n; i++) {
        size_t e = at;
        while (e < cells_n && cells[e] != 0) {
            e++;
        }
        int j = i % n;
        if (j > first) {
            int w = tw(&fv, cells + at, e - at, sc);
            if (w > g->w[j]) {
                g->w[j] = (int16_t)w;
            }
        }
        at = e + 1;
    }
    int pad = pm(&d, MOY_UI_M_VALUE_ROW_INSET, 1);
    int gap = pm(&d, MOY_UI_M_VALUE_ROW_COL, 1);
    int pic = g->pic ? pm(&d, MOY_UI_M_VALUE_ROW_PIC, 1) + pm(&d, MOY_UI_M_VALUE_ROW_GAP, 1) + 2 * d.fs
                     : 0;
    // The first text column shows at least eleven characters: the columns
    // between it and the last drop, the nearest the last first, then the last.
    uint8_t n_ch = 'n';
    int need = 11 * tw(&fl, &n_ch, 1, sc);
    int dropped = 0;
    for (;;) {
        int used = 2 * pad + pic;
        for (int j = first + 1; j < n; j++) {
            if (g->w[j] > 0) {
                used += g->w[j] + gap;
            }
        }
        int room = b.w - used;
        int victim = -1;
        if (room < need) {
            for (int j = n - 2; j > first && victim < 0; j--) {
                if (g->w[j] > 0) {
                    victim = j;
                }
            }
            if (victim < 0 && n - 1 > first && g->w[n - 1] > 0) {
                victim = n - 1;
            }
        }
        if (victim < 0) {
            g->x[first] = (int16_t)(b.x + pad + pic);
            g->w[first] = (int16_t)(room > 0 ? room : 0);
            break;
        }
        g->w[victim] = 0;
        dropped++;
    }
    int x = b.x + b.w - pad;
    for (int j = n - 1; j > first; j--) {
        if (g->w[j] > 0) {
            x -= g->w[j];
            g->x[j] = (int16_t)x;
            x -= gap;
        }
    }
    if (g->pic) {
        g->x[0] = (int16_t)(b.x + pad);
        g->w[0] = (int16_t)pm(&d, MOY_UI_M_VALUE_ROW_PIC, 1);
    }
    return dropped;
}

// Text cut to `room` with an ellipsis where the font has one.
static void text_cut(pd_t *d, const pf_t *f, moy_ui_span_t t, int x, int y, int h, int room,
                     uint16_t c, int sc) {
    if (room <= 0 || t.n == 0) {
        return;
    }
    int ell = 0;
    size_t k = moy_text_cut_px(f->id, t.p, t.n, room, sc, &ell);
    int w = text(d, f, t.p, k, x, y, h, c, sc);
    if (ell) {
        static const uint8_t dots[] = {0xE2, 0x80, 0xA6};
        text(d, f, dots, sizeof(dots), x + w, y, h, c, sc);
    }
}

int moy_ui_table_head(const moy_ui_sink_t *s, moy_ui_box_t b, const moy_ui_tgeo_t *g,
                      const uint8_t *cols, size_t cols_n, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    pf_t fv = font_of(&d, MOY_UI_FONT_CAPTION);
    int sc = tscale(s);
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM));
    int first = g->pic ? 1 : 0;
    for (int j = first; j < g->n; j++) {
        if (g->w[j] <= 0) {
            continue;
        }
        moy_ui_span_t h = field_at(cols, cols_n, j);
        int x = g->x[j];
        if (j > first) {
            x += g->w[j] - tw(&fv, h.p, h.n, sc);
        }
        text_cut(&d, &fv, h, x, b.y, b.h, g->w[j], dim, sc);
    }
    return MOY_UI_OK;
}

int moy_ui_table_row(const moy_ui_sink_t *s, moy_ui_box_t b, const moy_ui_tgeo_t *g,
                     const uint8_t *cells, size_t cells_n, int flags, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_marks_t *m = moy_ui_marks();
    int sc = tscale(s);
    int ink = tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK);
    int dim = tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM);
    int lab = ink, meta = dim;
    if ((flags & MOY_UI_F_LEAD) && !(flags & MOY_UI_F_DISABLED)) {
        lab = tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);   // the table's verb, in `value`
    }
    int r = (desk ? 7 : 4) * d.fs;
    if (flags & MOY_UI_F_DISABLED) {
        lab = dim;
    } else {
        if (flags & MOY_UI_F_ON) {
            int c = current(s, &d, b, r, ink, MOY_UI_MARK_SIDE);
            if (c != ink) {
                lab = meta = c;
            }
        }
        if (flags & MOY_UI_F_FOCUS) {
            int mi = -1;
            moy_ui_focus_in(s, b, MOY_UI_MARK_PART, r, 0, d.fs, NULL, &mi, 0);
            if (mi >= 0) {
                lab = mi;
                if (m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT) {
                    meta = mi;
                }
            }
        }
    }
    if ((flags & MOY_UI_F_BAR) && !(flags & MOY_UI_F_DISABLED)) {
        moy_target_fill(&d.t, b.x, b.y, 2 * d.fs, b.h, pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT)));
    }
    pf_t fl = font_of(&d, MOY_UI_FONT_UI), fv = font_of(&d, MOY_UI_FONT_CAPTION);
    int first = g->pic ? 1 : 0;
    if (g->pic && !(flags & MOY_UI_F_PART)) {
        moy_ui_span_t name = field_at(cells, cells_n, 0);
        int side = g->w[0];
        moy_ui_box_t ib = {g->x[0], (int16_t)(b.y + moy_ui_fdiv(b.h - side, 2)), (int16_t)side,
                           (int16_t)side};
        int slot = moy_ui_icon_slot(name.p, name.n), glyph = moy_ui_icon_glyph(name.p, name.n);
        icon_d(&d, slot, glyph, ib, lab != ink ? lab : dim);
    }
    for (int j = first; j < g->n; j++) {
        if (g->w[j] <= 0) {
            continue;
        }
        moy_ui_span_t t = field_at(cells, cells_n, j);
        if (j == first) {
            text_cut(&d, &fl, t, g->x[j], b.y, b.h, g->w[j], pal(&d, lab), sc);
            if ((flags & MOY_UI_F_ON) && !(flags & MOY_UI_F_DISABLED)
                && moy_ui_current_rows_under()) {
                int w = tw(&fl, t.p, t.n, sc);
                moy_ui_box_t u = {g->x[j], b.y, (int16_t)(w < g->w[j] ? w : g->w[j]), b.h};
                moy_ui_current_under(s, u, d.fs);
            }
        } else {
            int w = tw(&fv, t.p, t.n, sc);
            text_cut(&d, &fv, t, g->x[j] + g->w[j] - w, b.y, b.h, g->w[j], pal(&d, meta), sc);
        }
    }
    return MOY_UI_OK;
}

// -- a text field ------------------------------------------------------------------------

// A field's box: its letters' ink with 3 pixels at the scale above and
// below it, inside its 1-pixel ring; its height, and where in it the text's
// line starts (*line_dy).
static int field_box_h(const pd_t *d, const pf_t *f, int sc, int *line_dy) {
    int top = ink_top(f) * sc, deep = f->deep * sc;
    int pad = 3 * d->fs + d->fs;
    if (line_dy != NULL) {
        *line_dy = pad - top;
    }
    return (deep > top ? deep - top : f->line * sc) + 2 * pad;
}

int moy_ui_field_h(const moy_ui_sink_t *s, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    pf_t fl = font_of(&d, MOY_UI_FONT_UI);
    // the box, and a pixel at the scale each side for its focus ring and
    // one between that and the row's edge
    return field_box_h(&d, &fl, tscale(s), NULL) + 4 * d.fs;
}

int moy_ui_field(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t label, moy_ui_span_t text_,
                 moy_ui_span_t hint, int flags, int fs, int desk, int card, int first) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_marks_t *m = moy_ui_marks();
    int sc = tscale(s);
    int pad = (card || desk ? gm(MOY_UI_M_GROUP_PAD, fs)
                            : metric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * d.fs);
    int gap = pm(&d, MOY_UI_M_VALUE_ROW_GAP, 1);
    pf_t fl = font_of(&d, MOY_UI_FONT_UI);
    int on_card = card && !desk;
    uint16_t ink = pal(&d, on_card ? tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK)
                                   : tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK));
    int focused = (flags & MOY_UI_F_FOCUS) != 0;
    // in a group, the row's focus mark, as a value row wears it, under the
    // label and the field (on an invert mark the field's ring takes the
    // mark's ink); elsewhere -- a dialog's body, a page -- the field's own
    // ring is its focus, as a text field's is on every desk
    int mi = -1, invert = 0;
    if (focused && card) {
        int mf = 0, mr = 0;
        moy_ui_box_t mb = on_card ? on_card_mark(&d, b, first, 0, &mf, &mr) : b;
        moy_ui_focus_in(s, mb, MOY_UI_MARK_PART, mr, mf, d.fs, NULL, &mi, 0);
        invert = mi >= 0 && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT;
    }
    // the box centred in the row, the label on the text's line
    int line_dy = 0;
    int fh = field_box_h(&d, &fl, sc, &line_dy);
    int fy = b.y + moy_ui_fdiv(b.h - fh, 2);
    if (card) {
        // in a group the field fits the row as a value does: between the
        // rule over the row and the next one's, a pixel at the scale clear
        // of each, its letters centred in what is left (a handheld's 20
        // pixel row holds the box with its letters 2 pixels inside its ring)
        int room = b.h - d.fs, most = room - 2 * d.fs;
        if (fh > most && most > 0) {
            line_dy -= moy_ui_fdiv(fh - most, 2);
            fh = most;
        }
        fy = b.y + d.fs + moy_ui_fdiv(room - fh, 2);
    }
    int ty = fy + line_dy;
    int x = b.x + pad, right = b.x + b.w - pad;
    if (label.n) {
        x += moy_ui_text_in(s, fl.id, 0, label, x, ty, mi >= 0 ? pal(&d, mi) : ink) + gap;
    }
    if (card) {
        // in a group the field is the row's value: as wide as a slider's
        // track, at the row's right, the label keeping the room left of it
        int col = right - pm(&d, MOY_UI_M_VALUE_ROW_TRACK, 1);
        x = col > x ? col : x;
    }
    moy_ui_box_t f = {(int16_t)x, (int16_t)fy, (int16_t)(right - x), (int16_t)fh};
    if (f.w <= 0 || f.h <= 0) {
        return MOY_UI_OK;
    }
    const moy_ui_depth_t *dp = moy_ui_depth();
    int r = (dp->key_r < (desk ? 8 : 6) ? dp->key_r : (desk ? 8 : 6)) * d.fs;
    // focused, the field is being typed in: its ring is two pixels at the
    // scale, the second outside the box, in the focus colour (the mark's ink
    // on an invert mark), whether or not focus marks show
    uint16_t under = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    int fc = moy_ui_token(d.look, m->color);
    fc = fc >= 0 && ratio_ge(pal(&d, fc), under, 3, 1) ? fc : -1;  // Contrast's yellow on white
    int ring_c = !focused ? tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)
                 : invert ? mi
                 : fc >= 0 ? fc
                           : tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);
    // at rest the look's field outline where it names one (`field.border`)
    int32_t fb = moy_ui_display()->field_c;
    uint16_t ring = !focused && set(fb) ? val(&d, fb) : pal(&d, ring_c);
    if (focused) {
        rrect(&d, f.x - d.fs, f.y - d.fs, f.w + 2 * d.fs, f.h + 2 * d.fs, r ? r + d.fs : 0, 0, 0,
              d.fs, ring);
    }
    rrect(&d, f.x, f.y, f.w, f.h, r, 1, pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)),
          d.fs, ring);
    if (focused) {
        int fi = -1;
        moy_ui_focus_in(s, f, MOY_UI_MARK_PART, r, MOY_UI_MARK_AFTER, d.fs, NULL, &fi, 0);
    }
    // the text from the corner's radius and 3 pixels at the scale, the
    // caret's room kept inside the ring on the right
    int inner = 3 * d.fs + d.fs;
    int tx = f.x + (r > d.fs ? r : d.fs) + 3 * d.fs;
    int room = f.x + f.w - inner - tx - (focused ? 2 * d.fs : 0);
    uint16_t tink = pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_CHROME_INK));
    // the text's tail shows where it is wider than the field
    size_t from = 0;
    while (from < text_.n && tw(&fl, text_.p + from, text_.n - from, sc) > room) {
        from++;
        while (from < text_.n && (text_.p[from] & 0xC0) == 0x80) {
            from++;
        }
    }
    moy_ui_span_t tt = {text_.p + from, (uint16_t)(text_.n - from)};
    int w = tt.n ? moy_ui_text_in(s, fl.id, 0, tt, tx, ty, tink) : 0;
    if (!tt.n && hint.n && room > 0) {
        // the placeholder, in the disabled ink, as far as it fits
        int hc = moy_ui_disabled() >= 0 ? moy_ui_disabled() : tok(&d, MOY_UI_T_INK_DIM,
                                                                  MOY_UI_T_CHROME_INK_DIM);
        size_t hn = moy_text_fit_px(fl.id, hint.p, hint.n, room, sc);
        moy_ui_span_t ht = {hint.p, (uint16_t)hn};
        moy_ui_text_in(s, fl.id, 0, ht, tx + (focused ? 2 * d.fs : 0), ty, pal(&d, hc));
    }
    if (focused) {
        // the caret: a pixel at the scale past the text, from a pixel over
        // the letters' ink to a pixel under it, never past the box's inside
        int cx = tx + w + d.fs, cmax = f.x + f.w - inner;
        cx = cx > cmax ? cmax : cx;
        fill(&d, cx, f.y + 3 * d.fs, d.fs, f.h - 6 * d.fs,
             pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT)));
    }
    return MOY_UI_OK;
}

// -- a modal card --------------------------------------------------------------------------

int moy_ui_modal_band(int fs, int desk) {
    int n = 0;
    const int16_t *m = moy_ui_metrics(MOY_UI_K_VALUE_ROW, &n);
    int slot = MOY_UI_M_VALUE_ROW_INSET + (desk ? 1 : 0);
    return (m != NULL && slot < n ? m[slot] : 0) * (fs < 1 ? 1 : fs);
}

int moy_ui_modal_round(int fs, int desk) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    return (dp->card_r < (desk ? 12 : 8) ? dp->card_r : (desk ? 12 : 8)) * (fs < 1 ? 1 : fs);
}

int moy_ui_modal(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs, int desk,
                 int title_h) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int r = moy_ui_modal_round(fs, desk);
    // flat: no soft shadow blended over what still reads under it, but a
    // hard drop MOY_UI_DROP pixels at the scale in an opaque shade of the
    // ground, then the surface and its edge (Contrast's: no drop, the ink's
    // ring two pixels at the scale)
    uint16_t edge = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
    uint16_t surf = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    if (solid_marks()) {
        rrect(&d, b.x, b.y, b.w, b.h, r, 1, surf, 2 * d.fs,
              pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)));
    } else {
        int dk = dark(&d);
        rrect(&d, b.x, b.y + MOY_UI_DROP * d.fs, b.w, b.h, r, 1, mix(ground(&d), 0, dk ? 115 : 31),
              0, 0);
        edge = mix(edge, pal(&d, dk ? tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)
                                    : tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK)), dk ? 77 : 51);
        rrect(&d, b.x, b.y, b.w, b.h, r, 1, surf, d.fs, edge);
    }
    if (title.n) {
        int pad = moy_ui_modal_band(fs, desk);
        pf_t f = font_of(&d, MOY_UI_FONT_TITLE);
        int sc = tscale(s);
        moy_ui_span_t t = title;
        text_cut(&d, &f, t, b.x + pad, b.y + pad, title_h, b.w - 2 * pad,
                 pal(&d, tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK)), sc);
    }
    return MOY_UI_OK;
}

int moy_ui_role_w(const moy_ui_sink_t *s, int role, moy_ui_span_t t) {
    pd_t d;
    open_pd(s, &d, s->fs, 0);
    pf_t f = font_of(&d, role);
    return tw(&f, t.p, t.n, tscale(s));
}

// -- an empty page, a tool, a tooltip ------------------------------------------------

// An icon (slot, else glyph) centred in box b in the wire colour `col`: the
// mask the box holds, else the glyph; 0 where neither draws.
static int icon_col(pd_t *d, int slot, int glyph, moy_ui_box_t b, uint16_t col) {
    int box = b.w < b.h ? b.w : b.h;
    uint32_t f24 = moy_ui_icon_font(1), f16 = moy_ui_icon_font(0);
    uint32_t font = box >= 24 && f24 ? f24 : f16 ? f16 : 0;
    if (slot >= 0 && font && !d->run && moy_ui_cmask() != 63) {
        int size = font == f24 ? 24 : 16;
        int w = moy_text_glyph(&d->t, font, MOY_UI_ICON_FIRST + slot,
                               b.x + moy_ui_fdiv(b.w - size, 2),
                               b.y + moy_ui_fdiv(b.h - size, 2), col, 1);
        d->row->st[MOY_ST_N_TEXT]++;
        if (w > 0) {
            return 1;
        }
    }
    if (glyph >= 0) {
        d->row->st[MOY_ST_N_FILL] += moy_ui_glyph_draw(&d->t, glyph, b.x, b.y, b.w, b.h, col,
                                                       d->fs);
        return 1;
    }
    return 0;
}

int moy_ui_tool_draw(const moy_ui_sink_t *s, moy_ui_box_t b, int slot, int glyph,
                     moy_ui_span_t word, int flags, int interact, int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int k = d.fs, r = (b.w < 30 * k ? 6 : 8) * k;
    uint16_t g = ground(&d);
    int ink_i = tok(&d, MOY_UI_T_INK, MOY_UI_T_INK);
    int word_i = tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK);
    int disabled = (flags & MOY_UI_F_DISABLED) != 0;
    int on = (flags & MOY_UI_F_ON) != 0;
    uint16_t under = g;
    if (on) {
        int mi = -1;
        moy_ui_current_in(s, b, r, moy_ui_marks_shown() ? 0 : MOY_UI_MARK_BACK, d.fs, &mi, 0);
        if (mi >= 0) {
            ink_i = word_i = mi;
        }
    } else if (!disabled && interact >= 0) {
        // a resting pointer's plate, the ink a tenth over the ground; pressed, a fifth
        under = plate_of(g, pal(&d, ink_i), interact == MOY_UI_PRESSED ? 51 : 26);
        rrect(&d, b.x, b.y, b.w, b.h, r, 1, under, 0, 0);
    }
    if ((flags & MOY_UI_F_FOCUS) && moy_ui_marks()->active) {
        int mi = -1;
        moy_ui_focus_in(s, b, MOY_UI_MARK_AUTO, r, 0, d.fs, NULL, &mi, 0);
        if (mi >= 0) {
            ink_i = mi;
        }
    }
    uint16_t ink = pal(&d, ink_i), wink = pal(&d, word_i);
    if (disabled) {
        ink = plate_of(under, ink, 102);            // half strength: 0.4 of the ink
        wink = plate_of(under, wink, 102);
    }
    int sc = tscale(s);
    if (d.desk || word.n == 0) {
        // the icon by the tool's size: 16 under 30, 20 under 40, else 24
        int side = (d.desk ? (b.w < 40 * k ? 20 : 24) : 16) * k;
        moy_ui_box_t ib = {(int16_t)(b.x + moy_ui_fdiv(b.w - side, 2)),
                           (int16_t)(b.y + moy_ui_fdiv(b.h - side, 2)), (int16_t)side,
                           (int16_t)side};
        icon_col(&d, slot, glyph, ib, ink);
        return MOY_UI_OK;
    }
    // the handheld's: the icon, its word under it in the caption face
    pf_t f = font_of(&d, MOY_UI_FONT_CAPTION);
    int side = 16 * k, lh = f.line * sc;
    int top = b.y + moy_ui_fdiv(b.h - side - lh, 2);
    moy_ui_box_t ib = {(int16_t)(b.x + moy_ui_fdiv(b.w - side, 2)), (int16_t)top, (int16_t)side,
                       (int16_t)side};
    icon_col(&d, slot, glyph, ib, ink);
    int w = tw(&f, word.p, word.n, sc);
    text(&d, &f, word.p, word.n, b.x + moy_ui_fdiv(b.w - w, 2), top + side, lh, wink, sc);
    return MOY_UI_OK;
}

int moy_ui_tip_draw(const moy_ui_sink_t *s, moy_ui_box_t at, moy_ui_span_t t, moy_ui_box_t in,
                    int fs, int desk) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    int k = d.fs, sc = tscale(s);
    pf_t f = font_of(&d, MOY_UI_FONT_UI);
    int pad = 10 * k, h = f.line * sc + 2 * 5 * k;
    int w = tw(&f, t.p, t.n, sc) + 2 * pad;
    int x = at.x + at.w / 2 - w / 2, y = at.y + at.h + 4 * k;
    if (x + w > in.x + in.w - 4 * k) {
        x = in.x + in.w - 4 * k - w;
    }
    if (x < in.x + 4 * k) {
        x = in.x + 4 * k;
    }
    if (y + h > in.y + in.h) {
        y = at.y - 4 * k - h;           // no room under it: over it
    }
    uint16_t g = ground(&d);
    rrect(&d, x, y, w, h, 6 * k, 1, pal(&d, tok(&d, MOY_UI_T_INK, MOY_UI_T_INK)), 0, 0);
    text(&d, &f, t.p, t.n, x + pad, y, h, g, sc);
    return MOY_UI_OK;
}
