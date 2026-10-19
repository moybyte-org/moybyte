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

// The look's paints the parts name, copied out of the look at install: a
// paint leaf reads as MOY_THEME_PAINT | its slot here.
#define DEPTH_PAINTS 8
static moy_theme_paint_t s_paint[DEPTH_PAINTS];
static int s_paints;

static int set(int32_t v);

static int32_t paint_leaf(const moy_theme_look_t *lk, const char *name) {
    int32_t v = leaf(lk, name);
    if (v >= MOY_THEME_PAINT && v < MOY_THEME_VIBRANCY) {
        int k = (int)(v - MOY_THEME_PAINT);
        if (k < 0 || k >= lk->n_paints || s_paints >= DEPTH_PAINTS) {
            return MOY_THEME_NONE;
        }
        s_paint[s_paints] = lk->paint[k];
        return MOY_THEME_PAINT | s_paints++;
    }
    return v == MOY_THEME_VIBRANCY ? MOY_THEME_NONE : v;
}

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
    m->cur_row = (uint8_t)num(leaf(lk, "current.row"), MOY_UI_CUR_ROW_PLATE);
    m->cur_ink = num(leaf(lk, "current.ink"), -1);
    if (m->cur_ink >= MOY_UI_TOKENS) {
        m->cur_ink = -1;
    }
    m->sel_back = leaf(lk, "sel_back");
    if (m->color < 0 || m->color >= MOY_UI_TOKENS) {
        m->color = MOY_UI_T_ACCENT;
    }
    if (m->cur_color < 0 || m->cur_color >= MOY_UI_TOKENS) {
        m->cur_color = MOY_UI_T_VALUE;
    }
}

// The look's keycap fields (moy_ui.h, the parts), silent until a look sets them.
static moy_ui_keycap_t s_keycap = {MOY_THEME_NONE, MOY_THEME_NONE, MOY_THEME_NONE, 255, 0, 4};

const moy_ui_keycap_t *moy_ui_keycap(void) {
    return &s_keycap;
}

static void keycap_none(void) {
    s_keycap.fill = s_keycap.border_c = s_keycap.ink = MOY_THEME_NONE;
    s_keycap.alpha = 255;
    s_keycap.border_w = 0;
    s_keycap.r = 4;
}

static void keycap_of(const moy_theme_look_t *lk) {
    int32_t f = leaf(lk, "keycap.fill");
    s_keycap.fill = f >= MOY_THEME_PAINT ? MOY_THEME_NONE : f;
    s_keycap.alpha = alpha_of(lk, "keycap.alpha", 100);
    s_keycap.border_w = whole(lk, "keycap.border.w", 0);
    s_keycap.border_c = leaf(lk, "keycap.border.c");
    s_keycap.ink = leaf(lk, "keycap.ink");
    s_keycap.r = num(leaf(lk, "keycap.r"), 4);
}

// The look's display fields (moy_ui.h, the parts), silent until a look sets them.
static moy_ui_display_t s_display = {MOY_THEME_NONE, 0, MOY_UI_BEVEL_NONE};

const moy_ui_display_t *moy_ui_display(void) {
    return &s_display;
}

static void display_of(const moy_theme_look_t *lk) {
    s_display.border_w = whole(lk, "lcd_border.w", 0);
    s_display.border_c = leaf(lk, "lcd_border.c");
    s_display.bevel = num(leaf(lk, "lcd_bevel"), MOY_UI_BEVEL_NONE);
}

int moy_ui_depth_use(const char *name, size_t n, int light) {
    moy_ui_depth_t *d = &s_depth;
    memset(d, 0, sizeof(*d));
    keycap_none();
    s_display.border_c = MOY_THEME_NONE;
    s_display.border_w = 0;
    s_display.bevel = MOY_UI_BEVEL_NONE;
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
    keycap_of(lk);
    display_of(lk);
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
    d->card_line = whole(lk, "card.frame.w", 0);
    d->card_line_c = leaf(lk, "card.frame.c");
    d->line = whole(lk, "win.frame.w", 0);
    d->line_back = whole(lk, "win.frame_back.w", d->line);
    d->line_c = leaf(lk, "win.frame.c");
    d->line_back_c = leaf(lk, "win.frame_back.c");
    d->body = leaf(lk, "win.body");
    if (d->line_back_c == MOY_THEME_NONE || d->line_back_c == MOY_THEME_OFF) {
        d->line_back_c = d->line_c;
    }
    s_paints = 0;
    d->header = (uint8_t)num(leaf(lk, "win.header"), MOY_UI_HEAD_PLAIN);
    d->align = (uint8_t)num(leaf(lk, "win.align"), 0);
    d->controls = (uint8_t)num(leaf(lk, "win.controls"), MOY_UI_CTL_THREE);
    d->icon = (uint8_t)num(leaf(lk, "win.icon"), 1);
    d->rule = (uint8_t)num(leaf(lk, "win.rule"), 1);
    d->fill = paint_leaf(lk, "win.fill");
    d->fill_back = paint_leaf(lk, "win.fill_back");
    d->grad = paint_leaf(lk, "win.grad");
    d->grad_back = paint_leaf(lk, "win.grad_back");
    d->bar_style = (uint8_t)num(leaf(lk, "bar.style"), 0);
    d->bar_h = (uint8_t)num(leaf(lk, "bar.h"), 40);
    d->bar_fill = paint_leaf(lk, "bar.fill");
    d->bar_line_w = whole(lk, "bar.line.w", 0);
    d->bar_line_c = leaf(lk, "bar.line.c");
    d->bar_ink = leaf(lk, "bar.ink");
    d->bar_dim = leaf(lk, "bar.dim");
    d->sys_fill = leaf(lk, "bar.sys.fill");
    d->chip_cur = leaf(lk, "bar.chip_cur");
    d->hh_style = (uint8_t)num(leaf(lk, "hhbar.style"), MOY_UI_HH_NONE);
    d->hh_h = (uint8_t)num(leaf(lk, "hhbar.h"), 28);
    d->hh_fill = paint_leaf(lk, "hhbar.fill");
    d->hh_line_w = whole(lk, "hhbar.line.w", 0);
    d->hh_line_c = leaf(lk, "hhbar.line.c");
    d->hh_ink = leaf(lk, "hhbar.ink");
    d->hh_dim = leaf(lk, "hhbar.dim");
    d->hh_sys_fill = leaf(lk, "hhbar.sys.fill");
    d->tile_r = (uint8_t)num(leaf(lk, "tile_r"), 12);
    d->tile_fill = paint_leaf(lk, "tile_fill");
    d->tile_line_w = whole(lk, "tile_outline.w", 0);
    d->tile_line_c = leaf(lk, "tile_outline.c");
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
    return d->row->pal[moy_ui_pidx(moy_ui_token(d->look, token))];
}

// A look's colour value in the buffer's order: an index through the row's
// table, RGB through RGB565.
static uint16_t val(const dd_t *d, int32_t v) {
    if (v >= 0 && v < 128) {
        return d->row->pal[moy_ui_pidx(v)];
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

static int is_set(int32_t v) {
    return v != MOY_THEME_NONE && v != MOY_THEME_OFF;
}

// Look value `v` as a fill: a colour, or a paint the install copied.
static int paint_of(const dd_t *d, int32_t v, moy_shape_paint_t *p) {
    if (!is_set(v)) {
        return 0;
    }
    if (v >= MOY_THEME_PAINT && v < MOY_THEME_VIBRANCY) {
        const moy_theme_paint_t *tp = &s_paint[(v - MOY_THEME_PAINT) % DEPTH_PAINTS];
        memset(p, 0, sizeof(*p));
        p->n = (uint8_t)(tp->n > MOY_SHAPE_STOPS ? MOY_SHAPE_STOPS : tp->n);
        p->mode = tp->mode;
        p->steps = tp->steps;
        for (int k = 0; k < p->n; k++) {
            p->t[k] = tp->t[k];
            p->c[k] = val(d, tp->c[k]);
        }
        return p->n > 0;
    }
    *p = solid(val(d, v));
    return 1;
}

// `d` with its clip narrowed to rows [y0, y1) of the canvas.
static dd_t rows_of(const dd_t *d, int y0, int y1) {
    dd_t c = *d;
    int a = y0 - d->t.cam_y, b = y1 - d->t.cam_y;
    c.t.cy0 = a > c.t.cy0 ? a : c.t.cy0;
    c.t.cy1 = b < c.t.cy1 ? b : c.t.cy1;
    return c;
}

// The header's fill by win.header (THEMES.md, the theme model): plain takes
// win.fill (a window behind win.fill_back, else win.fill), fill the value
// colour (behind: win.fill_back), grad win.grad and win.grad_back, and bar
// none: the header is the window's colour, a value bar across a focused one.
static int header_paint(const dd_t *d, int active, moy_shape_paint_t *p) {
    const moy_ui_depth_t *dp = &s_depth;
    switch (dp->header) {
        case MOY_UI_HEAD_FILL:
            if (active) {
                *p = solid(tok(d, MOY_UI_T_VALUE));
                return 1;
            }
            return paint_of(d, dp->fill_back, p);
        case MOY_UI_HEAD_GRAD:
            return paint_of(d, active ? dp->grad : dp->grad_back, p);
        case MOY_UI_HEAD_PLAIN:
            return paint_of(d, active || !is_set(dp->fill_back) ? dp->fill : dp->fill_back, p);
        default:
            return 0;
    }
}

int moy_ui_depth_title_ink(int active) {
    if (!active) {
        return MOY_UI_T_CHROME_INK_DIM;
    }
    return s_depth.header == MOY_UI_HEAD_FILL ? MOY_UI_T_VALUE_INK : MOY_UI_T_CHROME_INK;
}

// The frame's body in `mask`: the header with the top corners (a focused
// `bar` header's value bar across its top rows), the sides and foot with the
// bottom ones (the content's rect left alone), the ring and the rule.
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
        moy_shape_paint_t body = solid(is_set(dp->body) ? val(d, dp->body)
                                                        : tok(d, MOY_UI_T_SURFACE));
        moy_shape_paint_t head;
        if (!header_paint(d, active, &head)) {
            head = body;
        }
        int top = cn & (MOY_SHAPE_TL | MOY_SHAPE_TR);
        int band = dp->header == MOY_UI_HEAD_BAR && active ? MOY_UI_HEAD_BAR_H : 0;
        band = band < th ? band : 0;
        if (band) {
            moy_shape_paint_t value = solid(tok(d, MOY_UI_T_VALUE));
            dd_t bar = rows_of(d, w.y, w.y + band);
            rrect(&bar, w.x, w.y, w.w, th + 1, r, top, st, mask, &value, 0, 0, -1, NULL);
        }
        dd_t rest = rows_of(d, w.y + band, w.y + th + 1);
        rrect(&rest, w.x, w.y, w.w, th + 1, r, top, st, mask, &head, 0, 0, -1, NULL);
        moy_shape_hole_t content = {w.x + 1, w.y + 1 + th, w.w - 2, w.h - 2 - th - foot, 0};
        rrect(d, w.x, w.y + th + 1, w.w, w.h - th - 1, r, cn & (MOY_SHAPE_BL | MOY_SHAPE_BR), st,
              mask, &body, 0, 0, -1, &content);
    }
    if (line > 0) {
        rrect(d, w.x, w.y, w.w, w.h, r, cn, st, mask, NULL, line, hi, dp->edges == 1 ? lo : -1,
              NULL);
    }
    int rule = dp->header == MOY_UI_HEAD_GRAD ? 0 : dp->rule;
    rule = rule < th ? rule : th;
    if (mask != MOY_SHAPE_FRINGE && rule > 0) {
        uint16_t rc = rule > 1 && line > 0 && hi >= 0 ? (uint16_t)hi : tok(d, MOY_UI_T_EDGE);
        moy_target_fill(&d->t, w.x + line, w.y + th + 1 - rule, w.w - 2 * line, rule, rc);
    }
}

void moy_ui_depth_frame(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active) {
    dd_t d;
    open_dd(s, &d, 0);
    frame_body(&d, w, th, active, MOY_SHAPE_INNER);
}

static void under(const dd_t *d, moy_ui_box_t w, int th, int active, int mode) {
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(w.w, w.h, th, active, mode, g);
    moy_shape_shadow_t sh;
    if (shadow_of(&s_depth, g[0], active, mode, &sh)) {
        moy_shape_hole_t body = {w.x, w.y, w.w, w.h, g[0]};
        moy_shape_shadow(&d->t, w.x, w.y, w.w, w.h, &sh, &body);
    }
    frame_body(d, w, th, active, MOY_SHAPE_FRINGE);
}

int moy_ui_frame_under(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active, int mode) {
    dd_t d;
    open_dd(s, &d, 1);
    if (!s_depth.frames) {
        return MOY_UI_OK;
    }
    under(&d, w, th, active, mode);
    return MOY_UI_OK;
}

// The ring frame_body draws round a window: its width (0: none).
static int ring_of(int active) {
    const moy_ui_depth_t *dp = &s_depth;
    int line = active ? dp->line : dp->line_back;
    int32_t lc = active ? dp->line_c : dp->line_back_c;
    if (dp->edges == 1) {
        return line > 0 ? line : 1;
    }
    return line > 0 && is_set(lc) ? line : 0;
}

int moy_ui_frame_under_onto(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active,
                            int mode, moy_ui_box_t o, int oth, int oactive) {
    dd_t d;
    open_dd(s, &d, 1);
    const moy_ui_depth_t *dp = &s_depth;
    if (!dp->frames || o.w <= 0 || o.h <= 0) {
        return MOY_UI_OK;
    }
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(o.w, o.h, oth, oactive, MOY_UI_UNDER_FULL, g);
    int r[4];
    for (int k = 0; k < 4; k++) {
        r[k] = (dp->corners >> k) & 1 ? g[0] : 0;
    }
    int line = ring_of(oactive);
    // the clip narrowed to what the caster's shadow and fringe reach
    int p[MOY_UI_GEOM];
    moy_ui_frame_geom(w.w, w.h, th, active, mode, p);
    int ex = w.x - p[2] - d.t.cam_x, ey = w.y - p[3] - d.t.cam_y;
    int ex1 = ex + w.w + p[2] + p[4], ey1 = ey + w.h + p[3] + p[5];
    d.t.cx0 = d.t.cx0 > ex ? d.t.cx0 : ex;
    d.t.cy0 = d.t.cy0 > ey ? d.t.cy0 : ey;
    d.t.cx1 = d.t.cx1 < ex1 ? d.t.cx1 : ex1;
    d.t.cy1 = d.t.cy1 < ey1 ? d.t.cy1 : ey1;
    if (d.t.cx1 <= d.t.cx0 || d.t.cy1 <= d.t.cy0) {
        return MOY_UI_OK;
    }
    const moy_text_target_t t0 = d.t;
    int bx = o.x - t0.cam_x, by = o.y - t0.cam_y;
    // only the rows of `o` inside the clip; rows sharing a span draw as one band
    int i = t0.cy0 - by > 0 ? t0.cy0 - by : 0;
    int end = t0.cy1 - by < o.h ? t0.cy1 - by : o.h;
    while (i < end) {
        int a0, a1;
        moy_shape_inner_row(o.w, o.h, r, dp->stepped, line, i, &a0, &a1);
        int j = i + 1;
        while (j < end) {
            int b0, b1;
            moy_shape_inner_row(o.w, o.h, r, dp->stepped, line, j, &b0, &b1);
            if (b0 != a0 || b1 != a1) {
                break;
            }
            j++;
        }
        d.t.cx0 = bx + a0 > t0.cx0 ? bx + a0 : t0.cx0;
        d.t.cx1 = bx + a1 < t0.cx1 ? bx + a1 : t0.cx1;
        d.t.cy0 = by + i;
        d.t.cy1 = by + j;
        if (d.t.cx1 > d.t.cx0) {
            under(&d, w, th, active, mode);
        }
        i = j;
    }
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
    uint16_t c = moy_shape_wire(d->row->pal[moy_ui_pidx(ci)]);
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
    if (!m->on && m->active && !after && !(flags & MOY_UI_MARK_RING)) {
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
    uint16_t c = d.row->pal[moy_ui_pidx(ci)];
    if (flags & MOY_UI_MARK_RING) {
        if (!after) {
            focus_ring_of(&d, m, x, y, w, h, r, c);
        }
        return MOY_UI_OK;
    }
    if (!after) {
        if (!(flags & MOY_UI_MARK_AGAIN)) {
            s_marks.drawn++;
        }
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
    // the current content's second cue beside the mark (`current.ink`), the
    // fill's selection ink under its own
    int own = m->cur_ink >= 0 ? moy_ui_token(d.look, m->cur_ink) : -1;
    if (flags & MOY_UI_MARK_INK) {
        if (m->cur_style == MOY_UI_CUR_FILL && !back) {
            *ink = moy_ui_token(d.look, MOY_UI_T_SELECTION_INK);
        } else {
            *ink = own;
        }
        return MOY_UI_OK;
    }
    *ink = own;
    fs = fs < 1 ? 1 : fs;
    switch (m->cur_style) {
        case MOY_UI_CUR_PLATE:
            if ((flags & MOY_UI_MARK_SIDE) && m->cur_row == MOY_UI_CUR_ROW_OUTLINE) {
                ring_a(&d, b.x, b.y, b.w, b.h, r, fs, tok(&d, m->cur_color), back ? 153 : 255);
                break;
            }
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

// -- the bars' parts (docs/look_2026-10.md step 14) --------------------------------

// A band's fill and its line on the edge facing the content (`side`, a
// MOY_UI_BAND_*), solid over whatever backdrop or wallpaper is under it.
static void band(dd_t *d, moy_ui_box_t b, const moy_shape_paint_t *p, int lw, int32_t lc,
                 int side) {
    rrect(d, b.x, b.y, b.w, b.h, 0, 0, 0, MOY_SHAPE_ALL, p, 0, 0, -1, NULL);
    if (lw <= 0 || !is_set(lc)) {
        return;
    }
    lw = lw < b.h ? lw : b.h;
    uint16_t c = val(d, lc);
    switch (side) {
        case MOY_UI_BAND_TOP:
            moy_target_fill(&d->t, b.x, b.y, b.w, lw, c);
            break;
        case MOY_UI_BAND_LEFT:
            moy_target_fill(&d->t, b.x, b.y, lw, b.h, c);
            break;
        case MOY_UI_BAND_RIGHT:
            moy_target_fill(&d->t, b.x + b.w - lw, b.y, lw, b.h, c);
            break;
        default:
            moy_target_fill(&d->t, b.x, b.y + b.h - lw, b.w, lw, c);
            break;
    }
}

int moy_ui_bar_part(const moy_ui_sink_t *s, int part, moy_ui_box_t b, int on) {
    dd_t d;
    open_dd(s, &d, 1);
    const moy_ui_depth_t *dp = &s_depth;
    if (!dp->on) {
        return 0;
    }
    moy_shape_paint_t p;
    int ink;
    switch (part) {
        case MOY_UI_BARP_BAND:
            if (!paint_of(&d, dp->bar_fill, &p)) {
                p = solid(tok(&d, MOY_UI_T_BAR));
            }
            band(&d, b, &p, dp->bar_line_w, dp->bar_line_c, on);
            return 1;
        case MOY_UI_BARP_HH_BAND:
            if (dp->hh_style == MOY_UI_HH_NONE
                || (!paint_of(&d, dp->hh_fill, &p) && !paint_of(&d, dp->bar_fill, &p))) {
                p = solid(tok(&d, dp->hh_style == MOY_UI_HH_NONE ? MOY_UI_T_DESKTOP
                                                                  : MOY_UI_T_BAR));
            }
            band(&d, b, &p, dp->hh_line_w, dp->hh_line_c, on);
            return 1;
        case MOY_UI_BARP_SYS:
        case MOY_UI_BARP_HH_SYS: {
            // the tile: 36x34 at r 9 on the desk, 22x20 at r 7 on the handheld
            int32_t f = part == MOY_UI_BARP_HH_SYS && is_set(dp->hh_sys_fill) ? dp->hh_sys_fill
                                                                               : dp->sys_fill;
            int r = moy_ui_fdiv(b.h * (part == MOY_UI_BARP_SYS ? 9 : 7) + b.h / 2,
                                part == MOY_UI_BARP_SYS ? 34 : 20);
            p = solid(is_set(f) ? val(&d, f) : tok(&d, MOY_UI_T_SURFACE_ALT));
            rrect(&d, b.x, b.y, b.w, b.h, r, 15, dp->stepped, MOY_SHAPE_ALL, &p, 0, 0, -1, NULL);
            if (on) {
                moy_ui_current_in(s, b, r, 0, 1, &ink, 0);
            }
            return 1;
        }
        case MOY_UI_BARP_CHIP: {
            if (!on) {
                return 1;
            }
            int r = moy_ui_fdiv(b.h * 8 + 14, 28);
            p = solid(is_set(dp->chip_cur) ? val(&d, dp->chip_cur)
                                           : tok(&d, MOY_UI_T_SURFACE_ALT));
            rrect(&d, b.x, b.y, b.w, b.h, r, 15, dp->stepped, MOY_SHAPE_ALL, &p, 0, 0, -1, NULL);
            moy_ui_current_in(s, b, r, 0, 1, &ink, 0);
            return 1;
        }
        case MOY_UI_BARP_TILE:
            return moy_ui_bar_tile(s, b, MOY_THEME_NONE);
        default:
            return 0;
    }
}

int moy_ui_bar_tile(const moy_ui_sink_t *s, moy_ui_box_t b, int32_t fill) {
    dd_t d;
    open_dd(s, &d, 1);
    const moy_ui_depth_t *dp = &s_depth;
    if (!dp->on) {
        return 0;
    }
    // tile_r on the desk's 44-pixel tile, in proportion on another
    int r = moy_ui_fdiv(dp->tile_r * b.h + 22, 44);
    r = r < b.h / 2 ? r : b.h / 2;
    int ring = dp->tile_line_w > 0 && is_set(dp->tile_line_c);
    moy_shape_shadow_t sh;
    if (!ring && shadow_of(dp, r, 1, MOY_UI_UNDER_FULL, &sh)) {
        if (dp->shadow == 1) {
            sh.alpha = sh.alpha * MOY_UI_TILE_SHADOW / 100;
        }
        moy_shape_hole_t body = {b.x, b.y, b.w, b.h, r};
        moy_shape_shadow(&d.t, b.x, b.y, b.w, b.h, &sh, &body);
    }
    moy_shape_paint_t p;
    if (is_set(fill)) {
        p = solid(val(&d, fill));
    } else if (!paint_of(&d, dp->tile_fill, &p)) {
        p = solid(tok(&d, MOY_UI_T_SURFACE_ALT));
    }
    rrect(&d, b.x, b.y, b.w, b.h, r, 15, dp->stepped, MOY_SHAPE_ALL, &p, 0, 0, -1, NULL);
    if (ring) {
        rrect(&d, b.x, b.y, b.w, b.h, r, 15, dp->stepped, MOY_SHAPE_ALL, NULL,
              dp->tile_line_w, val(&d, dp->tile_line_c), -1, NULL);
    }
    return 1;
}
