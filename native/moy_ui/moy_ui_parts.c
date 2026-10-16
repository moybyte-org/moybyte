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
    int look, run, fs, desk;
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
    d->desk = desk != 0;
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
    return d->row->pal[ci & moy_ui_cmask()];
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

// The colour index a page's ground is (moy_ui_page_ground), -1 for none.
static int ground(const pd_t *d) {
    if (moy_ui_depth()->on) {
        return tok(d, MOY_UI_T_PANEL, MOY_UI_T_SURFACE);
    }
    int16_t pc[3];
    moy_ui_colors(d->look, MOY_UI_K_PANEL, MOY_UI_REST, pc);
    return pc[0];
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
    int line;
} pf_t;

// The font a role's text draws in; the caption role takes the ui role's
// where a theme installs a font for that and none for it.
static pf_t font_of(const pd_t *d, int role) {
    pf_t f = {MOY_FONT_PETME128, MOY_TEXT_CELL};
    if (!d->run) {
        moy_font_info_t fi;
        f.id = moy_ui_font(role, &fi);
        f.line = fi.line;
        if (role == MOY_UI_FONT_CAPTION && f.id == MOY_FONT_PETME128) {
            f.id = moy_ui_font(MOY_UI_FONT_UI, &fi);
            f.line = fi.line;
        }
    }
    return f;
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

// The enter sign in the cap box: a hook, its arrow at the left.
static void enter_sign(pd_t *d, int x, int y, int w, int h, uint16_t c) {
    int k = d->fs, mx = x + w - 6 * k, my = y + h / 2 + k;
    moy_target_fill(&d->t, mx, y + 4 * k, k, my - y - 4 * k + k, c);
    moy_target_fill(&d->t, x + 6 * k, my, mx - x - 6 * k, k, c);
    for (int i = 1; i <= 2; i++) {
        moy_target_fill(&d->t, x + 5 * k + i * k, my - i * k, k, 2 * i * k + k, c);
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// An arrow in the cap box: `dir` 0 left, 1 right, 2 up, 3 down.
static void arrow_sign(pd_t *d, int x, int y, int w, int h, int dir, uint16_t c) {
    int k = d->fs, len = 7 * k, cx = x + w / 2, cy = y + h / 2;
    if (dir < 2) {
        int x0 = cx - len / 2;
        moy_target_fill(&d->t, x0, cy, len, k, c);
        for (int i = 1; i <= 2; i++) {
            int ax = dir == 0 ? x0 + i * k : x0 + len - k - i * k;
            moy_target_fill(&d->t, ax, cy - i * k, k, 2 * i * k + k, c);
        }
    } else {
        int y0 = cy - len / 2;
        moy_target_fill(&d->t, cx, y0, k, len, c);
        for (int i = 1; i <= 2; i++) {
            int ay = dir == 2 ? y0 + i * k : y0 + len - k - i * k;
            moy_target_fill(&d->t, cx - i * k, ay, 2 * i * k + k, k, c);
        }
    }
    d->row->st[MOY_ST_N_FILL]++;
}

// -- the current mark on an option -------------------------------------------------------

// The current mark on box b (radius r): the theme's, or under a theme without
// marks the selection colour (`ring` a ring instead). The ink the option takes.
static int current(const moy_ui_sink_t *s, pd_t *d, moy_ui_box_t b, int r, int ring, int ink) {
    const moy_ui_marks_t *m = moy_ui_marks();
    if (m->on) {
        int mi = -1;
        moy_ui_current_in(s, b, r, m->active ? 0 : MOY_UI_MARK_BACK, d->fs, &mi, 0);
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
    moy_ui_box_t track;
} vgeo_t;

static void geo(const pd_t *d, moy_ui_box_t b, const moy_ui_vrow_t *r, vgeo_t *g) {
    g->right = b.x + b.w - pad_of(d, r);
    g->gap = pm(d, MOY_UI_M_VALUE_ROW_SEG, 1);
    g->dot = pm(d, MOY_UI_M_VALUE_ROW_DOT, 1);
    g->step = pm(d, MOY_UI_M_VALUE_ROW_DOT_STEP, 1);
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

static moy_ui_box_t dot_box(moy_ui_box_t b, const moy_ui_vrow_t *r, const vgeo_t *g, int i) {
    int x = g->right - g->dot - (r->n - 1 - i) * g->step;
    return (moy_ui_box_t){(int16_t)x, (int16_t)(b.y + (b.h - g->dot) / 2), (int16_t)g->dot,
                          (int16_t)g->dot};
}

int32_t moy_ui_vrow_at(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, const moy_ui_vrow_t *r,
                       int x) {
    pd_t d;
    open_pd(s, &d, r->fs, r->desk);
    vgeo_t g;
    geo(&d, b, r, &g);
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
    if (flags & MOY_UI_F_DISABLED) {
        lab = ink = dim;
    } else if (flags & MOY_UI_F_FOCUS) {
        if (m->on) {
            int mi = -1;
            moy_ui_focus_in(s, b, MOY_UI_MARK_PART, 0, 0, d.fs, NULL, &mi, 0);
            lab = mi >= 0 ? mi : ink;
            if (mi >= 0 && m->style[MOY_UI_MARK_PART] == MOY_UI_FOCUS_INVERT) {
                ink = dim = mi;     // the whole row is on the mark
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
            int vw = tw(&fv, r->value.p, r->value.n, sc);
            x -= vw;
            text(&d, &fv, r->value.p, r->value.n, x, b.y, b.h,
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
                if (i == r->cur) {
                    c = current(s, &d, o, (d.desk ? 6 : 4) * d.fs, 0, ink);
                }
                text(&d, &fv, t.p, t.n, o.x + g.gap / 2, b.y, b.h, pal(&d, c), sc);
                right = o.x;
            }
            break;
        case MOY_UI_V_SWATCH:
            for (int i = r->n - 1; i >= 0; i--) {
                moy_ui_box_t o = dot_box(b, r, &g, i);
                if (i == r->cur) {
                    int h = 3 * d.fs;
                    moy_ui_box_t c = {(int16_t)(o.x - h), (int16_t)(o.y - h),
                                      (int16_t)(o.w + 2 * h), (int16_t)(o.h + 2 * h)};
                    current(s, &d, c, c.w / 2, 1, ink);
                }
                rrect(&d, o.x, o.y, o.w, o.h, o.w / 2, 1, swatch(&d, colour_at(r->colours, i)), 0,
                      0);
                right = o.x;
            }
            break;
        case MOY_UI_V_TOGGLE: {
            int sw = pm(&d, MOY_UI_M_VALUE_ROW_SWITCH_W, 0), sh = pm(&d, MOY_UI_M_VALUE_ROW_SWITCH_H, 0);
            int x = right - sw, y = b.y + (b.h - sh) / 2, kn = sh - 2 * d.fs;
            int dk = dark(&d);
            uint16_t track = r->cur ? pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT))
                                    : pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
            uint16_t knob = r->cur ? pal(&d, tok(&d, MOY_UI_T_VALUE_INK, MOY_UI_T_SURFACE))
                                   : dk ? cink : moy_shape_wire(0xffff);
            if (r->cur && rowfill >= 0) {           // on a row filled in the value colour
                track = cink;
                knob = pal(&d, rowfill);
            }
            if (flags & MOY_UI_F_DISABLED) {
                track = mix(track, pal(&d, tok(&d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), 128);
            }
            rrect(&d, x, y, sw, sh, sh / 2, 1, track, 0, 0);
            rrect(&d, r->cur ? x + sw - d.fs - kn : x + d.fs, y + d.fs, kn, kn, kn / 2, 1, knob,
                  0, 0);
            const uint8_t *word = (const uint8_t *)moy_str(r->cur ? MOY_S_UI_ON : MOY_S_UI_OFF);
            size_t wn = strlen((const char *)word);
            int ww = tw(&fv, word, wn, sc);
            right = x - gap - ww;
            text_t(&d, &fv, 1, word, wn, right, b.y, b.h, cdim, sc);
            break;
        }
        case MOY_UI_V_SLIDER: {
            moy_ui_box_t t = g.track;
            int dk = dark(&d);
            uint16_t line = pal(&d, tok(&d, MOY_UI_T_BORDER, MOY_UI_T_EDGE));
            uint16_t base = dk ? line : mix(line, pal(&d, tok(&d, MOY_UI_T_INK_DIM, MOY_UI_T_INK)),
                                            90);
            uint16_t val_c = rowfill >= 0 ? cink : pal(&d, tok(&d, MOY_UI_T_VALUE, MOY_UI_T_ACCENT));
            int span = r->hi > r->lo ? r->hi - r->lo : 1;
            int fw = (int)((int64_t)t.w * (r->v - r->lo) / span);
            rrect(&d, t.x, t.y, t.w, t.h, d.fs, 1, base, 0, 0);
            rrect(&d, t.x, t.y, fw, t.h, d.fs, 1, (flags & MOY_UI_F_DISABLED) ? cdim : val_c, 0,
                  0);
            int kw = 7 * d.fs, kh = 9 * d.fs;
            rrect(&d, t.x + fw - kw / 2, t.y + t.h / 2 - kh / 2, kw, kh, 2 * d.fs, 1,
                  dk ? cink : val_c, 0, 0);
            char num[12];
            int n = 0;
            {
                int32_t v = r->v;
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
            int vw = tw(&fv, (const uint8_t *)num, (size_t)n, sc);
            right = t.x - gap - kw / 2 - vw;
            text(&d, &fv, (const uint8_t *)num, (size_t)n, right, b.y, b.h, cdim, sc);
            break;
        }
        default:
            break;
    }
    int tx = b.x + pad, room = right - gap - tx;
    if (r->label.n && room > 0) {
        size_t ln = moy_text_fit_px(fl.id, r->label.p, r->label.n, room, sc);
        text(&d, &fl, r->label.p, ln, tx, b.y, b.h, pal(&d, lab), sc);
    }
    return MOY_UI_OK;
}

// -- the page ---------------------------------------------------------------------------

int moy_ui_page_ground(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                       moy_ui_box_t *content) {
    if (!moy_ui_depth()->on) {
        return moy_ui_panel(s, b, title, fs, content);
    }
    pd_t d;
    open_pd(s, &d, fs, 0);
    fill(&d, b.x, b.y, b.w, b.h, pal(&d, ground(&d)));
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

// The card's ring: a theme without a depth, or a dark one, rings its cards.
static int card_line(const pd_t *d) {
    const moy_ui_depth_t *dp = moy_ui_depth();
    return !dp->on || dark(d) ? d->fs : 0;
}

// The card's shape from its top to `bottom` (exclusive), drawn where the clip lets it.
static void card(pd_t *d, moy_ui_box_t c, int bottom) {
    rrect(d, c.x, c.y, c.w, bottom - c.y, moy_ui_group_r(d->fs), 1,
          pal(d, tok(d, MOY_UI_T_SURFACE, MOY_UI_T_PANEL)), card_line(d),
          pal(d, tok(d, MOY_UI_T_BORDER, MOY_UI_T_EDGE)));
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
    int gc = ground(&d);
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
        if (gc >= 0) {
            fill(&k, x, bottom - r, r, r, pal(&k, gc));
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
                moy_ui_focus_in(s, last, MOY_UI_MARK_PART, 0, MOY_UI_MARK_AGAIN, fs, NULL, &ink,
                                0);
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

// -- keycap hints -----------------------------------------------------------------------

static int key_sign(moy_ui_span_t k) {
    static const char *const names[] = {"left", "right", "up", "down", "enter"};
    for (int i = 0; i < 5; i++) {
        if (strlen(names[i]) == k.n && memcmp(names[i], k.p, k.n) == 0) {
            return i;
        }
    }
    return -1;
}

int moy_ui_hints(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                 int fs, int desk) {
    moy_ui_hints_at(s, b, items, len, fs, desk, 1, NULL, 0);
    return MOY_UI_OK;
}

int moy_ui_hints_at(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                    int fs, int desk, int draw, int16_t *spans, int cap) {
    pd_t d;
    open_pd(s, &d, fs, desk);
    const moy_ui_keycap_t *kc = moy_ui_keycap();
    pf_t fk = font_of(&d, MOY_UI_FONT_CAPTION), fh = fk;
    int sc = tscale(s);
    int kh = pm(&d, MOY_UI_M_VALUE_ROW_KEY, 1), hgap = pm(&d, MOY_UI_M_VALUE_ROW_HINT_GAP, 1);
    int dk = dark(&d);
    int gc = ground(&d);
    uint16_t bg = pal(&d, gc >= 0 ? gc : 0);
    uint16_t ink = pal(&d, tok(&d, MOY_UI_T_CHROME_INK, MOY_UI_T_INK));
    uint16_t dim = pal(&d, tok(&d, MOY_UI_T_CHROME_INK_DIM, MOY_UI_T_INK_DIM));
    uint16_t capc = set(kc->fill) ? val(&d, kc->fill) : mix(bg, ink, dk ? 41 : 26);
    uint16_t capi = set(kc->ink) ? val(&d, kc->ink) : ink;
    int r = kc->r * d.fs;
    int x = b.x + 8 * d.fs, y = b.y + (b.h - kh) / 2;
    size_t i = 0;
    int field = 0, pairs = 0;
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
            size_t a = 0;
            while (a < t.n) {
                size_t e = a;
                while (e < t.n && t.p[e] != ' ') {
                    e++;
                }
                moy_ui_span_t k = {t.p + a, (uint16_t)(e - a)};
                if (k.n) {
                    int sign = key_sign(k);
                    int w = sign == 4 ? kh + 3 * d.fs
                            : sign >= 0 ? kh
                                        : tw(&fk, k.p, k.n, sc) + (desk ? 10 : 8) * d.fs;
                    w = w < kh ? kh : w;
                    if (!draw) {
                        // measured only
                    } else if (kc->alpha >= 255) {
                        rrect(&d, x, y, w, kh, r, 1, capc, 0, 0);
                    } else {
                        rrect(&d, x, y, w, kh, r, 1, mix(bg, capc, kc->alpha), 0, 0);
                    }
                    if (draw && kc->border_w > 0 && set(kc->border_c)) {
                        rrect(&d, x, y, w, kh, r, 0, 0, kc->border_w * d.fs, val(&d, kc->border_c));
                    }
                    if (!draw) {
                        // measured only
                    } else if (sign == 4) {
                        enter_sign(&d, x, y, w, kh, capi);
                    } else if (sign >= 0) {
                        arrow_sign(&d, x, y, w, kh, sign, capi);
                    } else {
                        int lw = tw(&fk, k.p, k.n, sc);
                        text(&d, &fk, k.p, k.n, x + (w - lw) / 2, y, kh, capi, sc);
                    }
                    x += w + 3 * d.fs;
                }
                a = e + 1;
            }
            x += d.fs;
        } else {
            x += draw ? text(&d, &fh, t.p, t.n, x, b.y, b.h, dim, sc) : tw(&fh, t.p, t.n, sc);
            if (spans != NULL && pairs < cap) {
                spans[2 * pairs + 1] = (int16_t)x;
            }
            pairs++;
            x += hgap;
        }
        field++;
        i = j + 1;
    }
    return pairs;
}
