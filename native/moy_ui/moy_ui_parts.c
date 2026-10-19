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

// Whether the look is a dark one: the marks say, else the panel's luminance.
static int dark(const pd_t *d) {
    const moy_ui_marks_t *m = moy_ui_marks();
    if (m->on) {
        return m->dark;
    }
    uint16_t c = moy_shape_wire(pal(d, tok(d, MOY_UI_T_PANEL, MOY_UI_T_PANEL)));
    int r = (c >> 11) * 255 / 31, g = ((c >> 5) & 63) * 255 / 63, b = (c & 31) * 255 / 31;
    return 2126 * r + 7152 * g + 722 * b < 1280000;
}

// The colour a page's ground is (moy_ui_page_ground) in the buffer's order,
// in *c; 0 for none. Under a depth, in a window: the window's body as
// moy_ui_depth_frame paints it.
static int ground(const pd_t *d, uint16_t *c) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (dp->on) {
        if (d->win) {
            *c = set(dp->body) ? val(d, dp->body)
                               : pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_SURFACE));
        } else {
            *c = pal(d, tok(d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE));
        }
        return 1;
    }
    int16_t pc[3];
    moy_ui_colors(d->look, MOY_UI_K_PANEL, MOY_UI_REST, pc);
    if (pc[0] < 0) {
        return 0;
    }
    *c = pal(d, pc[0]);
    return 1;
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

// An arrow in the cap box: `dir` 0 left, 1 right, 2 up, 3 down.
static void arrow_sign(pd_t *d, int x, int y, int w, int h, int dir, uint16_t c) {
    int k = d->fs, n, head, cx = x + w / 2, cy = y + h / 2;
    sign_size(d, h, &n, &head);
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
// a row, whose current `bar` is beside it), or under a theme without marks the
// selection colour (`ring` a ring instead). The ink the option takes.
static int current(const moy_ui_sink_t *s, pd_t *d, moy_ui_box_t b, int r, int ring, int ink,
                   int side) {
    const moy_ui_marks_t *m = moy_ui_marks();
    if (m->on) {
        int mi = -1;
        moy_ui_current_in(s, b, r, (m->active ? 0 : MOY_UI_MARK_BACK) | side, d->fs, &mi, 0);
        return mi >= 0 ? mi : ink;
    }
    int sel = tok(d, MOY_UI_T_SELECTION, MOY_UI_T_ACCENT);
    if (ring) {
        rrect(d, b.x, b.y, b.w, b.h, r, 0, 0, d->fs, pal(d, sel));
        return ink;
    }
    rrect(d, b.x, b.y, b.w, b.h, r, 1, pal(d, sel), 0, 0);
    return tok(d, MOY_UI_T_SELECTION_INK, MOY_UI_T_INK);
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
        uint8_t code = (uint8_t)(MOY_UI_ICON_FIRST + slot);
        int w = moy_text(&d->t, font, MOY_TEXT_DATA, &code, 1, b.x + moy_ui_fdiv(b.w - size, 2),
                         b.y + moy_ui_fdiv(b.h - size, 2), pal(d, ci), 1, MOY_TEXT_LEFT, -1);
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

static moy_ui_box_t on_card_mark(const pd_t *d, moy_ui_box_t b, int first, int last);

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
    int lab = ink, rowfill = -1;
    if (r->kind == MOY_UI_VROW_DANGER) {
        // a verb that destroys: in the danger buttons' ink, which every
        // theme holds to text's contrast (a theme may make it its ink)
        moy_ui_btn_style_t bs;
        if (moy_ui_button_style(d.look, MOY_UI_K_BUTTON_DANGER, MOY_UI_REST, -1, &bs)
                == MOY_UI_OK
            && bs.c[1] >= 0) {
            lab = ink = bs.c[1];
        }
    }
    if (flags & MOY_UI_F_DISABLED) {
        lab = ink = dim = moy_ui_disabled() >= 0 ? moy_ui_disabled() : dim;
    } else if (flags & MOY_UI_F_FOCUS) {
        if (m->on) {
            int mi = -1;
            moy_ui_focus_in(s, on_card ? on_card_mark(&d, b, r->first, 0) : b, MOY_UI_MARK_PART,
                            0, 0, d.fs, NULL, &mi, 0);
            lab = mi >= 0 ? mi : ink;
            if (mi >= 0 && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT) {
                // the whole row is on the mark: its controls too take the
                // mark's ink, the mark's colour showing through them
                ink = dim = mi;
                int fc = moy_ui_token(d.look, m->color);
                rowfill = fc >= 0 ? fc : -1;
            }
        } else if (m->active) {
            int16_t c[3];
            moy_ui_colors(d.look, MOY_UI_K_ROW, MOY_UI_ON, c);
            if (c[0] >= 0) {
                fill(&d, b.x, b.y, b.w, b.h, pal(&d, c[0]));
                rowfill = c[0];
            }
            if (c[1] >= 0) {
                lab = ink = dim = c[1];
            }
        }
    }
    int pad = pad_of(&d, r);
    vgeo_t g;
    geo(&d, b, r, &g);
    pf_t fl = font_of(&d, MOY_UI_FONT_UI), fv = font_of(&d, MOY_UI_FONT_CAPTION);
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
            // the value in the label's face and weight, set apart by its ink
            int vw = tw(&fl, r->value.p, r->value.n, sc);
            x -= vw;
            text(&d, &fl, r->value.p, r->value.n, x, b.y, b.h,
                 kind == MOY_UI_V_CYCLE ? cink : cdim, sc);
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
                    c = current(s, &d, o, (d.desk ? 6 : 4) * d.fs, 0, ink, 0);
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
                    c = current(s, &d, o, (d.desk ? 6 : 4) * d.fs, 0, lab, 0);
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
                ground(&d, &gnd);
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
            uint16_t track = r->cur ? on_c : off_c;
            uint16_t knob = r->cur ? pal(&d, tok(&d, MOY_UI_T_VALUE_INK, MOY_UI_T_SURFACE))
                                   : dk ? cink : moy_shape_wire(0xffff);
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
        if (icon_d(&d, r->icon_slot, r->icon_glyph, ib, lab)) {
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

// -- cards (ui.card) -------------------------------------------------------------------

// The card's picture radius at the scale, under the depth's card radius; 0
// without a depth.
static int card_r(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (!dp->on) {
        return 0;
    }
    int r = pm(d, MOY_UI_M_VALUE_ROW_CARD_R, 1), cr = dp->card_r * d->fs;
    return cr < r ? cr : r;
}

int moy_ui_card(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_box_t pic, moy_ui_span_t title,
                moy_ui_span_t sub, int flags, int fs, int desk, moy_ui_box_t *content,
                uint16_t *gnd) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_marks_t *m = moy_ui_marks();
    int r = card_r(&d);
    uint16_t gc;
    if (!ground(&d, &gc)) {
        gc = pal(&d, tok(&d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE));
    }
    if (flags & MOY_UI_F_ON) {
        int pr = moy_ui_depth()->on ? pm(&d, MOY_UI_M_VALUE_ROW_PLATE_R, 1) : 0;
        current(s, &d, b, pr, 0, 0, 0);
        if (m->on) {
            int alpha = m->active ? m->cur_alpha : m->cur_alpha * 6 / 10;
            if (m->cur_style == MOY_UI_CUR_PLATE) {
                gc = mix(gc, pal(&d, moy_ui_token(d.look, m->cur_color)), alpha);
            } else if (m->cur_style == MOY_UI_CUR_FILL && m->active) {
                gc = pal(&d, tok(&d, MOY_UI_T_SELECTION, MOY_UI_T_ACCENT));
            }
        } else {
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
    if ((flags & MOY_UI_F_ON) && !m->on) {
        ink = dim = tok(&d, MOY_UI_T_SELECTION_INK, MOY_UI_T_INK);
    }
    pf_t ft = font_of(&d, MOY_UI_FONT_UI), fsub = font_of(&d, MOY_UI_FONT_CAPTION);
    if (title.n && room > 0) {
        size_t n = moy_text_fit_px(ft.id, title.p, title.n, room, sc);
        int w = tw(&ft, title.p, n, sc);
        text(&d, &ft, title.p, n, b.x + (b.w - w) / 2, y, th, pal(&d, ink), sc);
    }
    int sh = fsub.line * sc + 4 * d.fs;
    if (sub.n && room > 0 && y + th - d.fs + sh <= b.y + b.h) {     // where the foot holds it
        size_t n = moy_text_fit_px(fsub.id, sub.p, sub.n, room, sc);
        int w = tw(&fsub, sub.p, n, sc);
        text(&d, &fsub, sub.p, n, b.x + (b.w - w) / 2, y + th - d.fs, sh, pal(&d, dim), sc);
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
    if ((flags & MOY_UI_F_FOCUS) && moy_ui_marks()->active && moy_ui_marks()->on) {
        int mi = -1;
        moy_ui_focus_in(s, pic, MOY_UI_MARK_CARD, r, MOY_UI_MARK_AFTER, d.fs, NULL, &mi, 0);
    }
    return MOY_UI_OK;
}

// -- the page ---------------------------------------------------------------------------

int moy_ui_page_ground(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                       int desk, moy_ui_box_t *content) {
    if (!moy_ui_depth()->on) {
        return moy_ui_panel(s, b, title, fs, content);
    }
    pd_t d;
    open_pd(s, &d, fs, desk);
    uint16_t gc;
    ground(&d, &gc);
    fill(&d, b.x, b.y, b.w, b.h, gc);
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
    int r = 0;
    if (dp->on) {
        int cap = desk ? 10 : 8;
        r = (dp->key_r < cap ? dp->key_r : cap) * d.fs;
    }
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
    if ((flags & MOY_UI_F_ON) && m->on) {
        moy_ui_current_in(s, b, r, m->active ? 0 : MOY_UI_MARK_BACK, d.fs, &mi, 0);
    }
    if ((flags & MOY_UI_F_FOCUS) && m->active) {
        if (m->on) {
            moy_ui_focus_in(s, b, MOY_UI_MARK_AUTO, r, 0, d.fs, NULL, &mi, 0);
        } else {
            moy_ui_focus_ring(s, b, d.fs);
        }
    }
    return MOY_UI_OK;
}

// -- groups ---------------------------------------------------------------------------

int moy_ui_group_r(int fs) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (!dp->on) {
        return 0;
    }
    int r = gm(MOY_UI_M_GROUP_R, fs);
    int cr = dp->card_r * (fs < 1 ? 1 : fs);
    return cr < r ? cr : r;
}

// The card's ring: the look's `card.frame`, else a theme without a depth,
// or a dark one, rings its cards.
static int card_line(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    if (dp->on && dp->card_line > 0) {
        return dp->card_line * d->fs;
    }
    if (!dp->on || dark(d)) {
        return d->fs;
    }
    // a light card in its page's ground's colour (Contrast Light's white on
    // white) is its line alone
    uint16_t g;
    return ground(d, &g) && !ratio_ge(pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), g, 21, 20)
               ? d->fs
               : 0;
}

// The card's shape from its top to `bottom` (exclusive), drawn where the clip lets it.
static void card(pd_t *d, moy_ui_box_t c, int bottom) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    uint16_t lc = dp->on && dp->card_line > 0 && set(dp->card_line_c)
                      ? val(d, dp->card_line_c)
                      : pal(d, tok(d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
    rrect(d, c.x, c.y, c.w, bottom - c.y, moy_ui_group_r(d->fs), 1,
          pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), card_line(d), lc);
}

// Where a row on a card wears an invert focus mark, which fills it: inside
// the card's frame, so the frame runs past it unbroken (its top too on the
// card's first row, its foot on the last), or on it where the mark's outline
// is as wide, the outline then standing for the frame, so the mark has one
// weight all round.
static moy_ui_box_t on_card_mark(const pd_t *d, moy_ui_box_t b, int first, int last) {
    const moy_ui_marks_t *m = moy_ui_marks();
    if (d->desk || !m->on || m->style[MOY_UI_MARK_PART] != MOY_UI_FOCUS_INVERT) {
        return b;
    }
    int f = card_line(d) - (m->outline_w > 0 && set(m->outline_c) ? m->outline_w : 0);
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
    uint16_t gc;
    int has_gc = ground(&d, &gc);
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
        if (has_gc) {
            fill(&k, x, bottom - r, r, r, gc);
        }
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
            if ((flags & MOY_UI_F_FOCUS) && moy_ui_marks()->on) {
                moy_ui_focus_in(s, on_card_mark(&d, last, 0, 1), MOY_UI_MARK_PART, 0,
                                MOY_UI_MARK_AGAIN, fs, NULL, &ink, 0);
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

void moy_ui_set_input(int input) {
    s_input = input >= 0 && input < MOY_UI_INPUTS ? input : MOY_UI_INPUT_KEYS;
    moy_ui_marks_refresh();         // touch shows no focus
}

int moy_ui_input(void) {
    return s_input;
}

// Each action's name.
static const char *const ACT_NAMES[MOY_UI_ACTS] = {"confirm", "back", "change", "tabs", "menu"};

// The keys each input does each action with. The T-Deck's are its keyboard's
// (native/moy_input/moy_kbd.c): Enter is `run`, K is `b` (back, as the bar's
// chevron is), the trackball steers (`ball_lr`, its sign), Q and E are the
// home's tab keys and Mic is `start`. A gamepad's are its face buttons, its
// d-pad (`dpad_lr`), its shoulders and Start. Touch has none.
static const char *const ACT_KEYS[MOY_UI_INPUTS][MOY_UI_ACTS] = {
    {"enter", "Esc", "left right", "Q E", "Super"},
    {"enter", "K", "ball_lr", "Q E", "Mic"},
    {"A", "B", "dpad_lr", "L R", "Start"},
    {"", "", "", "", ""},
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

// The keys a cap draws as a sign: the arrows, enter, the trackball rolled
// and the d-pad pressed left or right.
enum { SIGN_ENTER = 4, SIGN_BALL_LR = 5, SIGN_DPAD_LR = 6, SIGNS = 7 };

static int key_sign(moy_ui_span_t k) {
    static const char *const names[SIGNS] = {"left", "right", "up", "down", "enter",
                                             "ball_lr", "dpad_lr"};
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
    if (s_input == MOY_UI_INPUT_TOUCH) {
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
    uint16_t bg;
    if (!ground(&d, &bg)) {
        bg = pal(&d, 0);
    }
    uint16_t ink = pal(&d, tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK));
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM));
    uint16_t capc = set(kc->fill) ? val(&d, kc->fill) : mix(bg, ink, dk ? 41 : 26);
    uint16_t capi = set(kc->ink) ? val(&d, kc->ink) : ink;
    int r = kc->r * d.fs;
    int x = b.x + 8 * d.fs, y = b.y + (b.h - kh) / 2;
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
                const char *keys = moy_ui_hint_keys(s_input, act);
                t.p = (const uint8_t *)keys;
                t.n = (uint16_t)strlen(keys);
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
                    int sign = key_sign(k);
                    int lw = sign >= 0 ? 0 : tw(&fk, k.p, k.n, sc);
                    // a sign's cap and a one-glyph legend's are square, a
                    // word's grows with it; the enter sign's is a little wider
                    int w = sign == SIGN_ENTER ? kh + 3 * d.fs
                            : sign >= 0 || (one_glyph(k) && lw + 4 * d.fs <= kh)
                                ? kh
                                : lw + (d.desk ? 10 : 8) * d.fs;
                    w = w < kh ? kh : w;
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
                    if (!draw) {
                        // measured only
                    } else if (sign == SIGN_ENTER) {
                        enter_sign(&d, x, y, w, kh, capi);
                    } else if (sign == SIGN_BALL_LR) {
                        ball_sign(&d, x, y, w, kh, capi);
                    } else if (sign == SIGN_DPAD_LR) {
                        dpad_sign(&d, x, y, w, kh, capi);
                    } else if (sign >= 0) {
                        arrow_sign(&d, x, y, w, kh, sign, capi);
                    } else {
                        text(&d, &fk, k.p, k.n, x + (w - lw) / 2, y, kh, capi, sc);
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
    int r = moy_ui_depth()->on ? (desk ? 7 : 4) * d.fs : 0;
    if (flags & MOY_UI_F_DISABLED) {
        lab = dim;
    } else {
        if (flags & MOY_UI_F_ON) {
            int c = current(s, &d, b, r, 0, ink, MOY_UI_MARK_SIDE);
            if (c != ink) {
                lab = meta = c;
            }
        }
        if (flags & MOY_UI_F_FOCUS) {
            if (m->on) {
                int mi = -1;
                moy_ui_focus_in(s, b, MOY_UI_MARK_PART, r, 0, d.fs, NULL, &mi, 0);
                if (mi >= 0) {
                    lab = mi;
                    if (m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT) {
                        meta = mi;
                    }
                }
            } else if (m->active) {
                int16_t c[3];
                moy_ui_colors(d.look, MOY_UI_K_ROW, MOY_UI_ON, c);
                if (c[0] >= 0) {
                    fill(&d, b.x, b.y, b.w, b.h, pal(&d, c[0]));
                }
                if (c[1] >= 0) {
                    lab = meta = c[1];
                }
            }
        }
    }
    pf_t fl = font_of(&d, MOY_UI_FONT_UI), fv = font_of(&d, MOY_UI_FONT_CAPTION);
    int first = g->pic ? 1 : 0;
    if (g->pic) {
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
                 int flags, int fs, int desk, int card, int first) {
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
    if (focused && m->on && card) {
        moy_ui_focus_in(s, on_card ? on_card_mark(&d, b, first, 0) : b, MOY_UI_MARK_PART, 0, 0,
                        d.fs, NULL, &mi, 0);
        invert = mi >= 0 && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT;
    }
    // the box centred in the row, the label on the text's line
    int line_dy = 0;
    int fh = field_box_h(&d, &fl, sc, &line_dy);
    int fy = b.y + moy_ui_fdiv(b.h - fh, 2), ty = fy + line_dy;
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
    int r = dp->on ? (dp->key_r < (desk ? 8 : 6) ? dp->key_r : (desk ? 8 : 6)) * d.fs : 0;
    // focused, the field is being typed in: its ring is two pixels at the
    // scale, the second outside the box, in the focus colour (the mark's ink
    // on an invert mark), whether or not focus marks show
    uint16_t under = pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL));
    int fc = m->on ? moy_ui_token(d.look, m->color) : -1;
    fc = fc >= 0 && ratio_ge(pal(&d, fc), under, 3, 1) ? fc : -1;  // Contrast's yellow on white
    int ring_c = !focused ? tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)
                 : invert ? mi
                 : fc >= 0 ? fc
                           : tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT);
    if (focused) {
        rrect(&d, f.x - d.fs, f.y - d.fs, f.w + 2 * d.fs, f.h + 2 * d.fs, r ? r + d.fs : 0, 0, 0,
              d.fs, pal(&d, ring_c));
    }
    rrect(&d, f.x, f.y, f.w, f.h, r, 1, pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)),
          d.fs, pal(&d, ring_c));
    if (focused && m->on) {
        int fi = -1;
        moy_ui_focus_in(s, f, MOY_UI_MARK_PART, r, MOY_UI_MARK_AFTER, d.fs, NULL, &fi, 0);
    } else if (focused && m->active) {
        moy_ui_focus_ring(s, f, d.fs);
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

int moy_ui_modal(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs, int desk,
                 int title_h) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_depth_t *dp = moy_ui_depth();
    int r = dp->on ? (dp->card_r < (desk ? 12 : 8) ? dp->card_r : (desk ? 12 : 8)) * d.fs : 0;
    if (dp->on) {
        moy_ui_cast(s, b, MOY_UI_CAST_CARD, MOY_UI_UNDER_FULL);
    }
    rrect(&d, b.x, b.y, b.w, b.h, r, 1, pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)),
          d.fs, pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)));
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
