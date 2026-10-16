// The theme's depth (moy_ui.h, docs/look_2026-10.md step 6): its look's
// radii, shadow and edges, installed once per theme and mode, and the window
// frame, its shadow and its outline's fringe and a card's shadow drawn with
// them through moy_shape (native/moy_gfx/moy_shape.h).

#include <stdlib.h>
#include <string.h>

#include "moy_canvas.h"
#include "moy_shape.h"
#include "moy_theme.h"
#include "moy_ui.h"

static moy_ui_depth_t s_depth;

const moy_ui_depth_t *moy_ui_depth(void) {
    return &s_depth;
}

static int leaf_of(const char *name) {
    for (int i = 0; i < MOY_THEME_LEAVES; i++) {
        if (strcmp(moy_theme_leaf_names[i], name) == 0) {
            return i;
        }
    }
    return -1;
}

static int32_t leaf(const moy_theme_look_t *lk, const char *name) {
    int i = leaf_of(name);
    return i < 0 ? MOY_THEME_NONE : lk->v[i];
}

static int16_t num(int32_t v, int dflt) {
    return (int16_t)(v == MOY_THEME_NONE || v == MOY_THEME_OFF ? dflt : v);
}

// A leaf in whole units: a `num` leaf holds hundredths.
static int16_t whole(const moy_theme_look_t *lk, const char *name, int dflt) {
    int i = leaf_of(name);
    int32_t v = i < 0 ? MOY_THEME_NONE : lk->v[i];
    if (v == MOY_THEME_NONE || v == MOY_THEME_OFF) {
        return (int16_t)dflt;
    }
    return (int16_t)(moy_theme_leaves[i].kind == MOY_THEME_LK_NUM ? (v + 50) / 100 : v);
}

static int depth_of(moy_ui_depth_t *d, moy_theme_look_t *lk, const char *name, size_t n,
                    int light);

static moy_ui_marks_t s_marks = {.active = 1};

const moy_ui_marks_t *moy_ui_marks(void) {
    return &s_marks;
}

void moy_ui_marks_active(int active) {
    s_marks.active = (uint8_t)(active != 0);
}

uint32_t moy_ui_marks_drawn(int reset) {
    uint32_t n = s_marks.drawn;
    if (reset) {
        s_marks.drawn = 0;
    }
    return n;
}

// A leaf of the marks: focus.*, current.* and sel_back, which a theme may set
// without a depth (Contrast keeps its frames and keys and draws the marks).
static int mark_leaf(const char *name) {
    return strncmp(name, "focus.", 6) == 0 || strncmp(name, "current.", 8) == 0
           || strcmp(name, "sel_back") == 0;
}

static uint8_t alpha_of(const moy_theme_look_t *lk, const char *name, int dflt) {
    int v = num(leaf(lk, name), dflt);
    return (uint8_t)((v < 0 ? 0 : v > 100 ? 100 : v) * 255 / 100);
}

// The marks of a look read with its defaults (DESIGN.md, the marks).
static void marks_of(moy_ui_marks_t *m, const moy_theme_look_t *lk, int light) {
    m->on = 1;
    m->dark = (uint8_t)!light;
    int st = num(leaf(lk, "focus.style"), MOY_UI_FOCUS_RING);
    m->style[MOY_UI_MARK_PART] = (uint8_t)st;
    m->style[MOY_UI_MARK_CARD] = (uint8_t)num(leaf(lk, "focus.card"), st);
    m->style[MOY_UI_MARK_SMALL] = (uint8_t)num(leaf(lk, "focus.small"), st);
    m->color = num(leaf(lk, "focus.color"), MOY_UI_T_ACCENT);
    m->ink_accent = num(leaf(lk, "focus.ink"), 0) == 1;
    m->ring_w = whole(lk, "focus.ring.w", 3);
    m->ring_gap = whole(lk, "focus.ring.gap", 3);
    m->ring_alpha = alpha_of(lk, "focus.ring.alpha", 100);
    m->outline_w = (uint8_t)whole(lk, "focus.outline.w", 0);
    m->outline_c = leaf(lk, "focus.outline.c");
    m->plate_alpha = alpha_of(lk, "focus.plate.alpha", light ? 14 : 22);
    m->grow = num(leaf(lk, "focus.lift.grow"), 5);
    m->rise = num(leaf(lk, "focus.lift.rise"), 3);
    m->rim_c = leaf(lk, "focus.lift.rim.c");
    m->rim_alpha = alpha_of(lk, "focus.lift.rim.alpha", 100);
    m->edge_w = num(leaf(lk, "focus.edge.w"), 4);
    m->underline_w = num(leaf(lk, "focus.underline.w"), 3);
    m->cur_style = (uint8_t)num(leaf(lk, "current.style"), MOY_UI_CUR_BAR);
    m->cur_color = num(leaf(lk, "current.color"), MOY_UI_T_VALUE);
    m->cur_alpha = alpha_of(lk, "current.alpha", light ? 14 : 24);
    m->sel_back = leaf(lk, "sel_back");
    if (m->color < 0 || m->color >= MOY_UI_TOKENS) {
        m->color = MOY_UI_T_ACCENT;
    }
    if (m->cur_color < 0 || m->cur_color >= MOY_UI_TOKENS) {
        m->cur_color = MOY_UI_T_VALUE;
    }
}

int moy_ui_depth_use(const char *name, size_t n, int light) {
    moy_ui_depth_t *d = &s_depth;
    memset(d, 0, sizeof(*d));
    uint8_t active = s_marks.active;
    uint32_t drawn = s_marks.drawn;
    memset(&s_marks, 0, sizeof(s_marks));
    s_marks.active = active;
    s_marks.drawn = drawn;
    moy_theme_look_t *look = name != NULL ? malloc(sizeof(moy_theme_look_t)) : NULL;
    if (look == NULL) {
        return 0;
    }
    int on = depth_of(d, look, name, n, light);
    free(look);
    return on;
}

static int depth_of(moy_ui_depth_t *d, moy_theme_look_t *lk, const char *name, size_t n,
                    int light) {
    int variant = light ? MOY_THEME_LIGHT : MOY_THEME_DARK;
    moy_theme_look(name, n, variant, 0, lk);
    int any = lk->n_paints || lk->n_tiles || lk->n_appbtn;
    int marks = 0;
    for (int i = 0; i < MOY_THEME_LEAVES; i++) {
        if (lk->v[i] != MOY_THEME_NONE) {
            if (mark_leaf(moy_theme_leaf_names[i])) {
                marks = 1;
            } else {
                any = 1;
            }
        }
    }
    if (!any && !marks) {
        return 0;
    }
    moy_theme_look(name, n, variant, 1, lk);
    if (marks || any) {
        marks_of(&s_marks, lk, light);
    }
    if (!any) {
        return 0;
    }
    d->on = 1;
    d->frames = (uint8_t)moy_theme_look_frames(name, n);
    d->win_r = (uint8_t)num(leaf(lk, "win_r"), 12);
    d->card_r = (uint8_t)num(leaf(lk, "card_r"), 10);
    d->key_r = (uint8_t)num(leaf(lk, "key_r"), 10);
    d->corners = (uint8_t)num(leaf(lk, "win_corners"), 15);
    d->stepped = num(leaf(lk, "render.shapes"), 0) == 1;
    d->edges = (uint8_t)num(leaf(lk, "edges"), 0);
    int kind = num(leaf(lk, "shadow.kind"), 0);
    int32_t c = leaf(lk, "shadow.c");
    if ((kind == 1 || kind == 2) && c != MOY_THEME_NONE && c != MOY_THEME_OFF) {
        d->shadow = (uint8_t)kind;
        d->shadow_565 = moy_theme_rgb565(c);
        d->alpha = kind == 1 ? (int16_t)((num(leaf(lk, "shadow.alpha"), 100) * 255 + 50) / 100)
                             : 255;
        d->blur = kind == 1 ? whole(lk, "shadow.blur", 0) : 0;
        d->dx = num(leaf(lk, "shadow.dx"), 0);
        d->dy = num(leaf(lk, "shadow.dy"), 0);
        d->back = num(leaf(lk, "shadow_back"), 55);
    }
    d->line = whole(lk, "win.frame.w", 0);
    d->line_back = whole(lk, "win.frame_back.w", d->line);
    d->line_c = leaf(lk, "win.frame.c");
    d->line_back_c = leaf(lk, "win.frame_back.c");
    d->body = leaf(lk, "win.body");
    if (d->line_back_c == MOY_THEME_NONE || d->line_back_c == MOY_THEME_OFF) {
        d->line_back_c = d->line_c;
    }
    return 1;
}

// -- geometry -------------------------------------------------------------------

// A shadow at `mode`'s size: the theme's, or a quarter of its blur at half its
// offset, or none.
static int shadow_of(const moy_ui_depth_t *d, int r, int active, int mode,
                     moy_shape_shadow_t *s) {
    if (!d->on || d->shadow == 0 || mode == MOY_UI_UNDER_NONE) {
        return 0;
    }
    s->r = r;
    s->blur = d->blur;
    s->dx = d->dx;
    s->dy = d->dy;
    if (mode == MOY_UI_UNDER_SMALL) {
        s->blur = d->blur > 0 ? (d->blur / 4 > 2 ? d->blur / 4 : 2) : 0;
        s->dx = d->dx / 2;
        s->dy = d->dy / 2;
    }
    s->col = moy_shape_wire(d->shadow_565);
    s->alpha = active ? d->alpha : d->alpha * d->back / 100;
    s->stepped = d->stepped;
    return s->alpha > 0;
}

void moy_ui_frame_geom(int w, int h, int th, int active, int mode, int out[MOY_UI_GEOM]) {
    const moy_ui_depth_t *d = &s_depth;
    memset(out, 0, sizeof(int) * MOY_UI_GEOM);
    if (!d->frames) {
        return;
    }
    int r = d->win_r;
    r = r < th ? r : th;
    r = r < w / 2 ? r : w / 2;
    r = r < h / 2 ? r : h / 2;
    r = r < 0 ? 0 : r > MOY_SHAPE_RMAX ? MOY_SHAPE_RMAX : r;
    out[0] = r;
    out[1] = (d->corners & (MOY_SHAPE_BR | MOY_SHAPE_BL)) && r > 1 ? r - 1 : 0;
    moy_shape_shadow_t s;
    if (shadow_of(d, r, active, mode, &s)) {
        moy_shape_shadow_pad(&s, &out[2]);
    }
}

// -- drawing --------------------------------------------------------------------

typedef struct {
    moy_canvas_row_t *row;
    moy_text_target_t t;
    int look;
} dd_t;

// A crossing counts once (moy_ui_count_call): the frame's own draw is inside
// moy_ui_frame's.
static void open_dd(const moy_ui_sink_t *s, dd_t *d, int crossing) {
    if (crossing) {
        moy_ui_count_call();
    }
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    const int32_t *st = row->st;
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
}

static uint16_t tok(const dd_t *d, int token) {
    return d->row->pal[moy_ui_token(d->look, token) & moy_ui_cmask()];
}

// A look's colour value in the buffer's order: an index through the row's
// table, RGB through RGB565.
static uint16_t val(const dd_t *d, int32_t v) {
    if (v >= 0 && v < 128) {
        return d->row->pal[v & moy_ui_cmask()];
    }
    return moy_shape_wire(moy_theme_rgb565(v));
}

static int rrect(const dd_t *d, int x, int y, int w, int h, int r, int corners, int stepped,
                 int mask, const moy_shape_paint_t *fill, int line, int32_t hi, int32_t lo,
                 const moy_shape_hole_t *hole) {
    moy_shape_rrect_t s;
    memset(&s, 0, sizeof(s));
    for (int k = 0; k < 4; k++) {
        s.r[k] = (uint8_t)((corners >> k) & 1 ? r : 0);
    }
    s.stepped = (uint8_t)stepped;
    s.mask = (uint8_t)mask;
    s.line = (uint8_t)line;
    s.line_hi = hi;
    s.line_lo = lo;
    s.fill = fill;
    if (hole != NULL) {
        s.hole = *hole;
    }
    return moy_shape_rrect(&d->t, x, y, w, h, &s);
}

static moy_shape_paint_t solid(uint16_t c) {
    moy_shape_paint_t p;
    memset(&p, 0, sizeof(p));
    p.n = 1;
    p.c[0] = c;
    return p;
}

// The frame's body in `mask`: the strip with the top corners, the sides and
// foot with the bottom ones (the content's rect left alone), and the ring.
static void frame_body(const dd_t *d, moy_ui_box_t w, int th, int active, int mask) {
    const moy_ui_depth_t *dp = &s_depth;
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(w.w, w.h, th, active, MOY_UI_UNDER_FULL, g);
    int r = g[0], foot = g[1], cn = dp->corners, st = dp->stepped;
    int line = active ? dp->line : dp->line_back;
    int32_t lc = active ? dp->line_c : dp->line_back_c;
    int32_t hi = -1, lo = -1;
    if (dp->edges == 1) {
        line = line > 0 ? line : 1;
        hi = tok(d, MOY_UI_T_SHADE_HI);
        lo = tok(d, MOY_UI_T_SHADE_LO);
    } else if (line > 0 && lc != MOY_THEME_NONE && lc != MOY_THEME_OFF) {
        hi = val(d, lc);
    } else {
        line = 0;
    }
    if (line == 0 || mask != MOY_SHAPE_FRINGE) {
        moy_shape_paint_t strip = solid(tok(d, active ? MOY_UI_T_TITLE_ACTIVE
                                                      : MOY_UI_T_TITLE_INACTIVE));
        rrect(d, w.x, w.y, w.w, th + 1, r, cn & (MOY_SHAPE_TL | MOY_SHAPE_TR), st, mask, &strip, 0,
              0, -1, NULL);
        moy_shape_paint_t body = solid(dp->body != MOY_THEME_NONE && dp->body != MOY_THEME_OFF
                                           ? val(d, dp->body)
                                           : tok(d, MOY_UI_T_SURFACE));
        moy_shape_hole_t content = {w.x + 1, w.y + 1 + th, w.w - 2, w.h - 2 - th - foot, 0};
        rrect(d, w.x, w.y + th + 1, w.w, w.h - th - 1, r, cn & (MOY_SHAPE_BL | MOY_SHAPE_BR), st,
              mask, &body, 0, 0, -1, &content);
    }
    if (line > 0) {
        rrect(d, w.x, w.y, w.w, w.h, r, cn, st, mask, NULL, line, hi, dp->edges == 1 ? lo : -1,
              NULL);
    }
    if (mask != MOY_SHAPE_FRINGE) {
        moy_target_fill(&d->t, w.x + line, w.y + th, w.w - 2 * line, 1, tok(d, MOY_UI_T_EDGE));
    }
}

void moy_ui_depth_frame(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active) {
    dd_t d;
    open_dd(s, &d, 0);
    frame_body(&d, w, th, active, MOY_SHAPE_INNER);
}

int moy_ui_frame_under(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active, int mode) {
    dd_t d;
    open_dd(s, &d, 1);
    if (!s_depth.frames) {
        return MOY_UI_OK;
    }
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(w.w, w.h, th, active, mode, g);
    moy_shape_shadow_t sh;
    if (shadow_of(&s_depth, g[0], active, mode, &sh)) {
        moy_shape_hole_t body = {w.x, w.y, w.w, w.h, g[0]};
        moy_shape_shadow(&d.t, w.x, w.y, w.w, w.h, &sh, &body);
    }
    frame_body(&d, w, th, active, MOY_SHAPE_FRINGE);
    return MOY_UI_OK;
}

int moy_ui_cast(const moy_ui_sink_t *s, moy_ui_box_t b, int part, int mode) {
    dd_t d;
    open_dd(s, &d, 1);
    const moy_ui_depth_t *dp = &s_depth;
    int r = part == MOY_UI_CAST_KEY ? dp->key_r : part == MOY_UI_CAST_CARD ? dp->card_r
                                                                            : dp->win_r;
    moy_shape_shadow_t sh;
    if (!shadow_of(dp, r, 1, mode, &sh)) {
        return 0;
    }
    moy_shape_hole_t body = {b.x, b.y, b.w, b.h, r};
    moy_shape_shadow(&d.t, b.x, b.y, b.w, b.h, &sh, &body);
    return 1;
}

// -- the marks (DESIGN.md, "The marks: focus, current, primary") --------------------

static int set(int32_t v) {
    return v != MOY_THEME_NONE && v != MOY_THEME_OFF;
}

// A rounded rect filled at `alpha` (0..255): a hard shadow with no offset.
static void tint(const dd_t *d, int x, int y, int w, int h, int r, uint16_t c, int alpha,
                 const moy_shape_hole_t *hole) {
    if (w <= 0 || h <= 0 || alpha <= 0) {
        return;
    }
    if (alpha >= 255 && hole == NULL) {
        moy_shape_paint_t p = solid(c);
        rrect(d, x, y, w, h, r, 15, s_depth.stepped, MOY_SHAPE_ALL, &p, 0, 0, -1, NULL);
        return;
    }
    moy_shape_shadow_t sh;
    memset(&sh, 0, sizeof(sh));
    sh.r = r;
    sh.col = c;
    sh.alpha = alpha > 255 ? 255 : alpha;
    sh.stepped = s_depth.stepped;
    moy_shape_hole_t none = {0, 0, 0, 0, 0};
    moy_shape_shadow(&d->t, x, y, w, h, &sh, hole != NULL ? hole : &none);
}

// A ring `t` wide whose outside is (x, y, w, h), at `alpha`.
static void ring_a(const dd_t *d, int x, int y, int w, int h, int r, int t, uint16_t c,
                   int alpha) {
    if (t <= 0) {
        return;
    }
    if (alpha >= 255) {
        rrect(d, x, y, w, h, r, 15, s_depth.stepped, MOY_SHAPE_ALL, NULL, t, c, -1, NULL);
        return;
    }
    moy_shape_hole_t in = {x + t, y + t, w - 2 * t, h - 2 * t, r > t ? r - t : 0};
    tint(d, x, y, w, h, r, c, alpha, &in);
}

static uint16_t mix565(uint16_t a, uint16_t b, int k) {      // k/255 of b over a
    int ar = a >> 11, ag = (a >> 5) & 63, ab = a & 31;
    int br = b >> 11, bg = (b >> 5) & 63, bb = b & 31;
    return (uint16_t)(((ar + (br - ar) * k / 255) << 11) | ((ag + (bg - ag) * k / 255) << 5)
                      | (ab + (bb - ab) * k / 255));
}

// The ink a part filled in colour index `ci` takes: black or white, the one
// that clears it (a light accent, Contrast's yellow, takes black).
static int ink_over(const dd_t *d, int ci) {
    uint16_t c = moy_shape_wire(d->row->pal[ci & moy_ui_cmask()]);
    int r = (c >> 11) * 255 / 31, g = ((c >> 5) & 63) * 255 / 63, b = (c & 31) * 255 / 31;
    return 2126 * r + 7152 * g + 722 * b > 1400000 ? 0 : 7;
}

int moy_ui_mark_reach(int cls) {
    const moy_ui_marks_t *m = &s_marks;
    if (!m->on) {
        return 0;
    }
    int o = m->outline_w;
    switch (m->style[cls < 0 || cls > 2 ? 0 : cls]) {
        case MOY_UI_FOCUS_RING:
            return cls == MOY_UI_MARK_SMALL ? 0 : m->ring_gap + m->ring_w + o;
        case MOY_UI_FOCUS_PLATE:
            return cls == MOY_UI_MARK_CARD ? 6 : 0;
        case MOY_UI_FOCUS_LIFT:
            return (cls == MOY_UI_MARK_CARD ? m->grow + m->rise : 2) + 1;
        case MOY_UI_FOCUS_INVERT:
            return cls == MOY_UI_MARK_CARD ? 5 : 0;
        default:
            return 0;
    }
}

int moy_ui_mark_class(moy_ui_box_t b, int cls, int fs) {
    if (cls != MOY_UI_MARK_AUTO) {
        return cls;
    }
    fs = fs < 1 ? 1 : fs;
    if (b.w >= 48 * fs && b.h >= 48 * fs) {
        return MOY_UI_MARK_CARD;
    }
    return b.w < 28 * fs && b.h < 28 * fs ? MOY_UI_MARK_SMALL : MOY_UI_MARK_PART;
}

// The theme's ring round (x, y, w, h): `ring.w` wide, `ring.gap` out, and the
// `outline` round it.
static void focus_ring_of(const dd_t *d, const moy_ui_marks_t *m, int x, int y, int w, int h,
                          int r, uint16_t c) {
    int o = m->ring_gap + m->ring_w;
    ring_a(d, x - o, y - o, w + 2 * o, h + 2 * o, r ? r + o : 0, m->ring_w, c, m->ring_alpha);
    if (m->outline_w > 0 && set(m->outline_c)) {
        int e = o + m->outline_w;
        ring_a(d, x - e, y - e, w + 2 * e, h + 2 * e, r ? r + e : 0, m->outline_w,
               val(d, m->outline_c), 255);
    }
}

int moy_ui_focus_in(const moy_ui_sink_t *s, moy_ui_box_t b, int cls, int r, int flags, int fs,
                    moy_ui_box_t *content, int *ink, int crossing) {
    const moy_ui_marks_t *m = &s_marks;
    int after = (flags & MOY_UI_MARK_AFTER) != 0;
    if (content != NULL) {
        *content = b;
    }
    *ink = -1;
    if (flags & MOY_UI_MARK_INK) {
        if (m->on && m->active) {
            int st = m->style[moy_ui_mark_class(b, cls, fs)];
            int ci = moy_ui_token(s->look, m->color);
            if (st == MOY_UI_FOCUS_INVERT) {
                dd_t d;
                open_dd(s, &d, 0);
                *ink = ink_over(&d, ci);
            } else if (m->ink_accent) {
                *ink = ci;
            }
        }
        return MOY_UI_OK;
    }
    if (!m->on && m->active && !after) {
        s_marks.drawn++;
        return moy_ui_focus_ring(s, b, fs);     // a theme with no marks: its ring
    }
    dd_t d;
    open_dd(s, &d, crossing);
    if (!m->active || !m->on) {
        return MOY_UI_OK;
    }
    int x = b.x, y = b.y, w = b.w, h = b.h;
    cls = moy_ui_mark_class(b, cls, fs);
    int st = m->style[cls];
    int ci = moy_ui_token(d.look, m->color);
    uint16_t c = d.row->pal[ci & moy_ui_cmask()];
    if (!after) {
        s_marks.drawn++;
        if (m->ink_accent) {
            *ink = ci;
        }
    }
    int card = cls == MOY_UI_MARK_CARD;
    switch (st) {
        case MOY_UI_FOCUS_RING:
            if (after) {
                break;
            }
            if (cls == MOY_UI_MARK_SMALL) {
                tint(&d, x, y, w, h, r, c, m->plate_alpha, NULL);
                ring_a(&d, x, y, w, h, r, 1, c, 255);
            } else {
                focus_ring_of(&d, m, x, y, w, h, r, c);
            }
            break;
        case MOY_UI_FOCUS_PLATE: {
            int g = card ? 6 : 0;
            if (!after) {
                tint(&d, x - g, y - g, w + 2 * g, h + 2 * g, r ? r + g : 0, c, m->plate_alpha,
                     NULL);
            }
            break;
        }
        case MOY_UI_FOCUS_LIFT: {
            int g = card ? m->grow : 1, up = card ? m->rise : 1;
            int bx = x - g, by = y - g - up, bw = w + 2 * g, bh = h + 2 * g;
            if (content != NULL && card) {
                *content = (moy_ui_box_t){(int16_t)bx, (int16_t)by, (int16_t)bw, (int16_t)bh};
            }
            if (!after) {
                moy_shape_shadow_t sh;
                memset(&sh, 0, sizeof(sh));
                sh.r = r;
                sh.blur = 10;
                sh.dy = 5;
                sh.col = moy_shape_wire(0);
                sh.alpha = m->dark ? 178 : 82;
                sh.stepped = s_depth.stepped;
                moy_shape_hole_t body = {bx, by, bw, bh, r};
                moy_shape_shadow(&d.t, bx, by, bw, bh, &sh, &body);
                if (!card) {
                    uint16_t white = moy_shape_wire(0xffff);
                    uint16_t f = m->dark ? moy_shape_wire(mix565(
                                               moy_shape_wire(tok(&d, MOY_UI_T_SURFACE_ALT)),
                                               0xffff, 20))
                                         : white;
                    tint(&d, bx, by, bw, bh, r, f, 255, NULL);
                }
            }
            if (after || !card) {
                if (set(m->rim_c)) {
                    ring_a(&d, bx - 1, by - 1, bw + 2, bh + 2, r ? r + 2 : 0, 1,
                           val(&d, m->rim_c), m->rim_alpha);
                }
            }
            break;
        }
        case MOY_UI_FOCUS_EDGE: {
            int t = m->edge_w * (fs < 1 ? 1 : fs);
            if (after) {
                break;
            }
            if (w >= 3 * h) {
                tint(&d, x, y, t, h, t / 2, c, 255, NULL);
            } else {
                tint(&d, x + 2, y + h - t, w - 4, t, t / 2, c, 255, NULL);
            }
            break;
        }
        case MOY_UI_FOCUS_UNDERLINE: {
            int t = m->underline_w * (fs < 1 ? 1 : fs);
            if (!after) {
                tint(&d, x + 3, y + h - t - 1, w - 6, t, t / 2, c, 255, NULL);
            }
            break;
        }
        default: {      // invert
            int g = card ? 5 : 0;
            if (after) {
                break;
            }
            tint(&d, x - g, y - g, w + 2 * g, h + 2 * g, r ? r + g : 0, c, 255, NULL);
            if (m->outline_w > 0 && set(m->outline_c)) {
                ring_a(&d, x - g, y - g, w + 2 * g, h + 2 * g, r ? r + g : 0, m->outline_w,
                       val(&d, m->outline_c), 255);
            }
            *ink = ink_over(&d, ci);
            break;
        }
    }
    return MOY_UI_OK;
}

int moy_ui_focus(const moy_ui_sink_t *s, moy_ui_box_t b, int cls, int r, int flags, int fs,
                 moy_ui_box_t *content, int *ink) {
    return moy_ui_focus_in(s, b, cls, r, flags, fs, content, ink, 1);
}

int moy_ui_current_in(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int flags, int fs, int *ink,
                      int crossing) {
    dd_t d;
    open_dd(s, &d, crossing);
    const moy_ui_marks_t *m = &s_marks;
    *ink = -1;
    if (!m->on) {
        return MOY_UI_OK;
    }
    int back = (flags & MOY_UI_MARK_BACK) != 0;
    if (flags & MOY_UI_MARK_INK) {
        if (m->cur_style == MOY_UI_CUR_FILL && !back) {
            *ink = moy_ui_token(d.look, MOY_UI_T_SELECTION_INK);
        }
        return MOY_UI_OK;
    }
    fs = fs < 1 ? 1 : fs;
    switch (m->cur_style) {
        case MOY_UI_CUR_PLATE:
            tint(&d, b.x, b.y, b.w, b.h, r, tok(&d, m->cur_color),
                 back ? m->cur_alpha * 6 / 10 : m->cur_alpha, NULL);
            break;
        case MOY_UI_CUR_FILL:
            if (back && set(m->sel_back)) {
                tint(&d, b.x, b.y, b.w, b.h, r, val(&d, m->sel_back), 255, NULL);
            } else {
                tint(&d, b.x, b.y, b.w, b.h, r,
                     tok(&d, back ? MOY_UI_T_TITLE_INACTIVE : MOY_UI_T_SELECTION), 255, NULL);
                if (!back) {
                    *ink = moy_ui_token(d.look, MOY_UI_T_SELECTION_INK);
                }
            }
            break;
        default:        // bar: beside a row, else under the part
            if (flags & MOY_UI_MARK_SIDE) {
                int bh = 16 * fs < b.h ? 16 * fs : b.h;
                tint(&d, b.x, b.y + (b.h - bh) / 2, 3, bh, 1, tok(&d, m->cur_color), 255, NULL);
            } else {
                tint(&d, b.x + 2 * fs, b.y + b.h - 3, b.w - 4 * fs, 3, 1,
                     tok(&d, m->cur_color), 255, NULL);
            }
            break;
    }
    return MOY_UI_OK;
}

int moy_ui_current(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int flags, int fs, int *ink) {
    return moy_ui_current_in(s, b, r, flags, fs, ink, 1);
}
