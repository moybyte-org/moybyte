// The theme's depth (moy_ui.h, docs/history/look_2026-10.md step 6): its look's
// radii, shadow and edges, installed once per theme and mode, and the window
// frame, its shadow and its outline's fringe and a card's shadow drawn with
// them through moy_shape (native/moy_gfx/moy_shape.h).

#include <stdlib.h>
#include <string.h>

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define MARKS_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#define MARKS_ALLOC(n) malloc(n)
#endif

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

static void depth_of(moy_ui_depth_t *d, moy_theme_look_t *lk, const char *name, size_t n,
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

// The shell's word on whether focus draws; under touch it draws in no case.
static uint8_t s_shown = 1;

void moy_ui_marks_refresh(void) {
    s_marks.active = (uint8_t)(s_shown && moy_ui_input() != MOY_UI_INPUT_TOUCH);
}

void moy_ui_marks_active(int active) {
    s_shown = (uint8_t)(active != 0);
    moy_ui_marks_refresh();
}

int moy_ui_marks_shown(void) {
    return s_shown;
}

uint32_t moy_ui_marks_drawn(int reset) {
    uint32_t n = s_marks.drawn;
    if (reset) {
        s_marks.drawn = 0;
    }
    return n;
}

static uint8_t alpha_of(const moy_theme_look_t *lk, const char *name, int dflt) {
    int v = num(leaf(lk, name), dflt);
    return (uint8_t)((v < 0 ? 0 : v > 100 ? 100 : v) * 255 / 100);
}

// The marks the look declares (moy_ui.h, the marks), resolved over the
// engine's defaults and held to the legibility checks (moy_theme_marks), and
// a lifted card's numbers: PSRAM's, allocated at the first install and kept,
// so the marks cost internal SRAM nothing.
typedef struct {
    moy_ui_mark_t m[MOY_UI_ITEMS * MOY_UI_MSTATES];
    int8_t grow, rise;
    uint8_t rim_alpha;
    int32_t rim_c;
} kmarks_t;
static kmarks_t *s_km;
static const moy_ui_mark_t k_none = {MOY_UI_MK_NONE, 0, 0, 0, -1, -1, 255, 0, MOY_THEME_NONE};

const moy_ui_mark_t *moy_ui_mark_of(int kind, int state) {
    if (s_km == NULL || kind < 0 || kind >= MOY_UI_ITEMS || state < 0 || state >= MOY_UI_MSTATES) {
        return &k_none;
    }
    return &s_km->m[kind * MOY_UI_MSTATES + state];
}

static void marks_of(moy_ui_marks_t *m, const moy_theme_look_t *lk, int light, const char *name,
                     size_t n) {
    m->dark = (uint8_t)!light;
    m->plate_alpha = alpha_of(lk, "focus.plate.alpha", light ? 14 : 22);
    if (s_km == NULL) {
        s_km = MARKS_ALLOC(sizeof(kmarks_t));
    }
    if (s_km == NULL) {
        return;
    }
    int variant = light ? MOY_THEME_LIGHT : MOY_THEME_DARK;
    int32_t row[MOY_THEME_ROLES];
    moy_theme_resolve(name, n, variant, row, NULL);
    moy_theme_marks(name, n, variant, row, s_km->m, NULL);
    s_km->grow = (int8_t)num(leaf(lk, "marks.card.focus.lift.grow"), 0);
    s_km->rise = (int8_t)num(leaf(lk, "marks.card.focus.lift.rise"), 3);
    s_km->rim_c = leaf(lk, "marks.card.focus.lift.rim.c");
    s_km->rim_alpha = alpha_of(lk, "marks.card.focus.lift.rim.alpha", 100);
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
static moy_ui_display_t s_display = {MOY_THEME_NONE, 0, MOY_UI_BEVEL_NONE, MOY_THEME_NONE};

const moy_ui_display_t *moy_ui_display(void) {
    return &s_display;
}

// The look's scrim (moy_ui_scrim): its colour (a look value) and alpha.
static int32_t s_scrim_c = MOY_THEME_NONE;
static uint8_t s_scrim_a = 178;

static void display_of(const moy_theme_look_t *lk) {
    s_display.border_w = whole(lk, "lcd_border.w", 0);
    s_display.border_c = leaf(lk, "lcd_border.c");
    s_display.bevel = num(leaf(lk, "lcd_bevel"), MOY_UI_BEVEL_NONE);
    s_display.field_c = leaf(lk, "field.border");
    s_scrim_c = leaf(lk, "scrim.c");
    s_scrim_a = alpha_of(lk, "scrim.alpha", 70);
}

void moy_ui_depth_use(const char *name, size_t n, int light) {
    moy_ui_depth_t *d = &s_depth;
    memset(d, 0, sizeof(*d));
    keycap_none();
    s_display.border_c = MOY_THEME_NONE;
    s_display.border_w = 0;
    s_display.bevel = MOY_UI_BEVEL_NONE;
    s_display.field_c = MOY_THEME_NONE;
    s_scrim_c = MOY_THEME_NONE;
    s_scrim_a = 178;
    uint8_t active = s_marks.active;
    uint32_t drawn = s_marks.drawn;
    memset(&s_marks, 0, sizeof(s_marks));
    s_marks.active = active;
    s_marks.drawn = drawn;
    if (name == NULL) {
        name = moy_theme_seed_names[0];
        n = strlen(name);
    }
    moy_theme_look_t *look = malloc(sizeof(moy_theme_look_t));
    if (look != NULL) {
        depth_of(d, look, name, n, light);
        free(look);
    }
}

static void depth_of(moy_ui_depth_t *d, moy_theme_look_t *lk, const char *name, size_t n,
                     int light) {
    moy_theme_look(name, n, light ? MOY_THEME_LIGHT : MOY_THEME_DARK, 1, lk);
    keycap_of(lk);
    display_of(lk);
    marks_of(&s_marks, lk, light, name, n);
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
    d->sys_line_w = whole(lk, "bar.sys_line.w", 0);
    d->sys_line_c = leaf(lk, "bar.sys_line.c");
}

// -- geometry -------------------------------------------------------------------

// A shadow at `mode`'s size: the theme's, or a quarter of its blur at half its
// offset, or none.
static int shadow_of(const moy_ui_depth_t *d, int r, int active, int mode,
                     moy_shape_shadow_t *s) {
    if (d->shadow == 0 || mode == MOY_UI_UNDER_NONE) {
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
// the window's colour with a value bar across a focused one, a window behind
// in win.fill_back where the look names one.
static int header_paint(const dd_t *d, int active, moy_shape_paint_t *p) {
    const moy_ui_depth_t *dp = &s_depth;
    switch (dp->header) {
        case MOY_UI_HEAD_BAR:
            return !active && is_set(dp->fill_back) && paint_of(d, dp->fill_back, p);
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
// bottom ones (the content's rect left alone: in from each side by the foot
// under `matte`), the ring and the rule.
static void frame_body(const dd_t *d, moy_ui_box_t w, int th, int active, int mask,
                       int matte) {
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
        int side = matte ? foot : 0;
        moy_shape_hole_t content = {w.x + 1 + side, w.y + 1 + th, w.w - 2 - 2 * side,
                                    w.h - 2 - th - foot, 0};
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

void moy_ui_depth_frame(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active, int matte) {
    dd_t d;
    open_dd(s, &d, 0);
    frame_body(&d, w, th, active, MOY_SHAPE_INNER, matte);
}

static void under(const dd_t *d, moy_ui_box_t w, int th, int active, int mode) {
    int g[MOY_UI_GEOM];
    moy_ui_frame_geom(w.w, w.h, th, active, mode, g);
    moy_shape_shadow_t sh;
    if (shadow_of(&s_depth, g[0], active, mode, &sh)) {
        moy_shape_hole_t body = {w.x, w.y, w.w, w.h, g[0]};
        moy_shape_shadow(&d->t, w.x, w.y, w.w, w.h, &sh, &body);
    }
    frame_body(d, w, th, active, MOY_SHAPE_FRINGE, 0);
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

int moy_ui_soft(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int k) {
    dd_t d;
    open_dd(s, &d, 1);
    moy_shape_shadow_t sh;
    if (!shadow_of(&s_depth, r, 1, MOY_UI_UNDER_FULL, &sh)) {
        return 0;
    }
    sh.alpha = sh.alpha * k / 255;
    if (sh.alpha <= 0) {
        return 0;
    }
    moy_shape_hole_t body = {b.x, b.y, b.w, b.h, r};
    moy_shape_shadow(&d.t, b.x, b.y, b.w, b.h, &sh, &body);
    return 1;
}

// -- the marks (moy_ui.h, the marks) -------------------------------------------------

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

// The corners MOY_UI_MARK_TOP and MOY_UI_MARK_FOOT in `flags` name, as
// moy_shape's (all four with neither).
static int corners_of(int flags) {
    if (!(flags & (MOY_UI_MARK_TOP | MOY_UI_MARK_FOOT))) {
        return 15;
    }
    return ((flags & MOY_UI_MARK_TOP) ? MOY_SHAPE_TL | MOY_SHAPE_TR : 0)
           | ((flags & MOY_UI_MARK_FOOT) ? MOY_SHAPE_BL | MOY_SHAPE_BR : 0);
}

// tint's rect whose radius rounds only the corners `flags` names
// (corners_of): a row's mark on a card's first or last row, under the card's
// own corners.
static void tint_at(const dd_t *d, int x, int y, int w, int h, int r, int flags, uint16_t c,
                    int alpha) {
    int top = (flags & MOY_UI_MARK_TOP) != 0, foot = (flags & MOY_UI_MARK_FOOT) != 0;
    if (r <= 0 || top == foot) {
        tint(d, x, y, w, h, top || !foot ? r : 0, c, alpha, NULL);
        return;
    }
    // the rect run on past its square side by the radius, cut back to it
    dd_t k = rows_of(d, y, y + h);
    tint(&k, x, top ? y : y - r, w, h + r, r, c, alpha, NULL);
}

// Whether two colours (wire order) have a luminance ratio of num/den or more,
// on squares for the sRGB curve (moy_ui_parts.c's ratio_ge).
static int clears_by(uint16_t a, uint16_t b, int num, int den) {
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

// A ring `t` wide whose outside is (x, y, w, h), at `alpha`, its radius on
// `corners` (moy_shape's).
static void ring_c(const dd_t *d, int x, int y, int w, int h, int r, int corners, int t,
                   uint16_t c, int alpha) {
    if (t <= 0 || w <= 0 || h <= 0) {
        return;
    }
    if (alpha >= 255) {
        rrect(d, x, y, w, h, r, corners, s_depth.stepped, MOY_SHAPE_ALL, NULL, t, c, -1, NULL);
        return;
    }
    moy_shape_hole_t in = {x + t, y + t, w - 2 * t, h - 2 * t, r > t ? r - t : 0};
    tint(d, x, y, w, h, r, c, alpha, &in);
}

static void ring_a(const dd_t *d, int x, int y, int w, int h, int r, int t, uint16_t c,
                   int alpha) {
    ring_c(d, x, y, w, h, r, 15, t, c, alpha);
}

// The ink a part filled in colour index `ci` takes: black or white, the one
// that clears it (a light accent, Contrast's yellow, takes black).
static int ink_over(const dd_t *d, int ci) {
    uint16_t c = moy_shape_wire(d->row->pal[moy_ui_pidx(ci)]);
    int r = (c >> 11) * 255 / 31, g = ((c >> 5) & 63) * 255 / 63, b = (c & 31) * 255 / 31;
    return 2126 * r + 7152 * g + 722 * b > 1400000 ? 0 : 7;
}

// The colour index mark k paints in: its own, else (a `both` mark's) the ink
// over the fill `under` it lands on, else the accent.
static int km_ci(const dd_t *d, const moy_ui_mark_t *k, int under) {
    if (k->c >= 0) {
        return moy_ui_token(d->look, k->c);
    }
    return under >= 0 ? ink_over(d, under) : moy_ui_token(d->look, MOY_UI_T_ACCENT);
}

// The ink an item's content takes on fill mark k of colour index ci: the
// mark's own `ink`, else the selection's or the value's ink over theirs, else
// black or white, the one that clears it.
static int km_fill_ink(const dd_t *d, const moy_ui_mark_t *k, int ci) {
    if (k->ink >= 0) {
        return moy_ui_token(d->look, k->ink);
    }
    if (ci == moy_ui_token(d->look, MOY_UI_T_SELECTION)) {
        return moy_ui_token(d->look, MOY_UI_T_SELECTION_INK);
    }
    if (ci == moy_ui_token(d->look, MOY_UI_T_VALUE)) {
        return moy_ui_token(d->look, MOY_UI_T_VALUE_INK);
    }
    return ink_over(d, ci);
}

// Mark k on box (x, y, w, h) of radius r (its corners `flags`'s TOP and FOOT)
// at scale f, in colour index ci at `alpha` of its own strength. A key rings
// a line's or a fill's own edge inside it, and a ring's, a bar's or an
// underline's outside, so it never lands on a neighbour.
static void km_draw(const dd_t *d, const moy_ui_mark_t *k, int x, int y, int w, int h, int r,
                    int flags, int f, int ci, int alpha) {
    uint16_t c = d->row->pal[moy_ui_pidx(ci)];
    int t = k->w * f, g = k->gap * f, kw = k->key_w * f;
    int bx = x, by = y, bw = w, bh = h, br = r, inner = 1, cn = corners_of(flags);
    switch (k->mark) {
        case MOY_UI_MK_FILL:
            tint_at(d, x, y, w, h, r, flags, c, alpha);
            t = 0;
            break;
        case MOY_UI_MK_TINT:
            tint_at(d, x, y, w, h, r, flags, c, k->alpha * alpha / 255);
            t = 0;
            break;
        case MOY_UI_MK_BAR: {
            int in = h > 8 * f ? 2 * f : 0;
            bx = x + g, by = y + in, bw = t, bh = h - 2 * in, br = 0, inner = 0, cn = 15;
            tint(d, bx, by, bw, bh, 0, c, alpha, NULL);
            break;
        }
        case MOY_UI_MK_RING: {
            int o = g + t;
            bx = x - o, by = y - o, bw = w + 2 * o, bh = h + 2 * o, br = r ? r + o : 0;
            inner = 0;
            ring_c(d, bx, by, bw, bh, br, cn, t, c, alpha);
            break;
        }
        case MOY_UI_MK_INSET:
        case MOY_UI_MK_OUTLINE: {
            int o = k->mark == MOY_UI_MK_INSET ? g : 0;
            bx = x + o, by = y + o, bw = w - 2 * o, bh = h - 2 * o, br = r > o ? r - o : 0;
            ring_c(d, bx, by, bw, bh, br, cn, t, c, alpha);
            break;
        }
        case MOY_UI_MK_UNDERLINE:
            bx = x + g, by = y + h - t, bw = w - 2 * g, bh = t, br = 0, inner = 0, cn = 15;
            tint(d, bx, by, bw, bh, 0, c, alpha, NULL);
            break;
        default:
            return;
    }
    if (kw > 0 && set(k->key_c)) {
        uint16_t kc = val(d, k->key_c);
        if (inner) {
            ring_c(d, bx + t, by + t, bw - 2 * t, bh - 2 * t, br > t ? br - t : 0, cn, kw, kc,
                   alpha);
        } else {
            ring_c(d, bx - kw, by - kw, bw + 2 * kw, bh + 2 * kw, br ? br + kw : 0, cn, kw, kc,
                   alpha);
        }
    }
}

// Whether danger ink `danger` (a colour index, -1 none) keeps its own ink
// under a fill in colour index `ci`: the fill would not clear it 4.5:1.
static int keeps_danger(const dd_t *d, int danger, int ci) {
    return danger >= 0 && ci >= 0
           && !clears_by(d->row->pal[moy_ui_pidx(danger)], d->row->pal[moy_ui_pidx(ci)], 9, 2);
}

static int clamp_kind(int kind) {
    return kind < 0 || kind >= MOY_UI_ITEMS ? MOY_UI_ITEM_CELL : kind;
}

static int lifts(int kind) {
    return kind == MOY_UI_ITEM_CARD && s_km != NULL && s_km->grow > 0;
}

int moy_ui_mark_reach(int kind) {
    kind = clamp_kind(kind);
    const moy_ui_mark_t *k = moy_ui_mark_of(kind, MOY_UI_MS_FOCUS);
    int r = k->mark == MOY_UI_MK_RING ? k->gap + k->w + k->key_w
            : k->mark == MOY_UI_MK_BAR || k->mark == MOY_UI_MK_UNDERLINE ? k->key_w : 0;
    if (lifts(kind)) {
        r += s_km->grow + s_km->rise + 1;
    }
    return r;
}

int moy_ui_mark_solid(int kind) {
    const moy_ui_mark_t *k = moy_ui_mark_of(clamp_kind(kind), MOY_UI_MS_ON);
    return k->mark == MOY_UI_MK_FILL && k->alpha >= 255;
}

int moy_ui_mark_colour(const moy_ui_sink_t *s, int kind, int state) {
    const moy_ui_mark_t *k = moy_ui_mark_of(clamp_kind(kind), state);
    if (k->mark == MOY_UI_MK_NONE) {
        return -1;
    }
    return k->c >= 0 ? moy_ui_token(s->look, k->c) : moy_ui_token(s->look, MOY_UI_T_ACCENT);
}

int moy_ui_focus_in(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int r, int flags, int fs,
                    moy_ui_box_t *content, int *ink, int crossing) {
    const moy_ui_marks_t *m = &s_marks;
    int after = (flags & MOY_UI_MARK_AFTER) != 0;
    int danger = (flags & MOY_UI_MARK_DANGER) ? *ink : -1;
    int f = fs < 1 ? 1 : fs;
    if (content != NULL) {
        *content = b;
    }
    *ink = -1;
    kind = clamp_kind(kind);
    const moy_ui_mark_t *k = moy_ui_mark_of(kind, MOY_UI_MS_FOCUS);
    const moy_ui_mark_t *on = moy_ui_mark_of(kind, MOY_UI_MS_ON);
    const moy_ui_mark_t *bk = moy_ui_mark_of(kind, MOY_UI_MS_BOTH);
    int both = (flags & MOY_UI_MARK_BOTH) != 0;
    if (flags & MOY_UI_MARK_INK) {
        if (m->active) {
            dd_t d;
            open_dd(s, &d, 0);
            int under = both && on->mark == MOY_UI_MK_FILL ? km_ci(&d, on, -1) : -1;
            int ci = km_ci(&d, k, under);
            if (k->mark == MOY_UI_MK_FILL && danger >= 0) {
                *ink = danger;                      // L5: a danger part keeps its ink
            } else if (k->mark == MOY_UI_MK_FILL) {
                *ink = km_fill_ink(&d, k, ci);
            } else if (k->ink >= 0) {
                *ink = moy_ui_token(d.look, k->ink);
            }
            if (both && bk->ink >= 0) {
                *ink = moy_ui_token(d.look, bk->ink);
            }
        }
        return MOY_UI_OK;
    }
    int sel = (flags & MOY_UI_MARK_SELECTED) != 0;
    dd_t d;
    open_dd(s, &d, crossing);
    if (!(m->active || sel)) {
        return MOY_UI_OK;
    }
    int x = b.x, y = b.y, w = b.w, h = b.h;
    if (flags & MOY_UI_MARK_RING) {
        // a cursor: the cell's focus colour as a ring, whatever its mark
        if (!after) {
            const moy_ui_mark_t *cm = moy_ui_mark_of(MOY_UI_ITEM_CELL, MOY_UI_MS_FOCUS);
            moy_ui_mark_t ring = *cm;
            ring.mark = MOY_UI_MK_RING;
            if (cm->mark != MOY_UI_MK_RING) {
                ring.w = 2;
                ring.gap = 2;
            }
            km_draw(&d, &ring, x, y, w, h, r, 0, f, km_ci(&d, cm, -1), 255);
            if (flags & MOY_UI_MARK_COUNT) {
                s_marks.drawn++;
            }
        }
        return MOY_UI_OK;
    }
    if (flags & MOY_UI_MARK_PILL) {
        const moy_ui_mark_t *cm = moy_ui_mark_of(MOY_UI_ITEM_CELL, MOY_UI_MS_FOCUS);
        uint16_t c = d.row->pal[moy_ui_pidx(km_ci(&d, cm, -1))];
        if (after) {
            ring_a(&d, x, y, w, h, r, 2 * f, c, 255);
            if (cm->key_w > 0 && set(cm->key_c)) {
                int e = cm->key_w * f;
                ring_a(&d, x - e, y - e, w + 2 * e, h + 2 * e, r ? r + e : 0, e,
                       val(&d, cm->key_c), 255);
            }
        } else {
            if (!(flags & (MOY_UI_MARK_AGAIN | MOY_UI_MARK_SELECTED))) {
                s_marks.drawn++;
            }
            tint(&d, x, y, w, h, r, c, m->plate_alpha, NULL);
        }
        return MOY_UI_OK;
    }
    if (lifts(kind)) {
        // a lifted card: raised and grown on its shadow, the mark round the
        // lifted box, its rim after its content
        int g = s_km->grow, up = s_km->rise;
        x -= g, y -= g + up, w += 2 * g, h += 2 * g;
        if (content != NULL) {
            *content = (moy_ui_box_t){(int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h};
        }
        if (after) {
            if (set(s_km->rim_c) && !sel) {
                ring_a(&d, x - 1, y - 1, w + 2, h + 2, r ? r + 2 : 0, 1, val(&d, s_km->rim_c),
                       s_km->rim_alpha);
            }
            return MOY_UI_OK;
        }
        moy_shape_shadow_t sh;
        memset(&sh, 0, sizeof(sh));
        sh.r = r;
        sh.blur = 10;
        sh.dy = 5;
        sh.col = moy_shape_wire(0);
        sh.alpha = m->dark ? 178 : 82;
        sh.stepped = s_depth.stepped;
        moy_shape_hole_t body = {x, y, w, h, r};
        moy_shape_shadow(&d.t, x, y, w, h, &sh, &body);
    }
    if (after) {
        return MOY_UI_OK;
    }
    if (!(flags & (MOY_UI_MARK_AGAIN | MOY_UI_MARK_SELECTED))) {
        s_marks.drawn++;
    }
    int under = both && on->mark == MOY_UI_MK_FILL ? km_ci(&d, on, -1) : -1;
    int ci = km_ci(&d, k, under);
    moy_ui_mark_t line;
    if (k->mark == MOY_UI_MK_FILL && keeps_danger(&d, danger, ci)) {
        // a danger part's ink the fill would not clear 4.5:1 (L5): the
        // mark is a line inside the part in the fill's colour, and the part
        // keeps its ink
        line = *k;
        line.mark = MOY_UI_MK_INSET;
        line.w = 3;
        line.gap = 0;
        line.key_w = 0;
        k = &line;
    }
    km_draw(&d, k, x, y, w, h, r, flags, f, ci, 255);
    // a danger part keeps its ink (L5): on a fill it clears 4.5:1, or
    // inside the line that stands for a fill it would not
    int got = danger >= 0 && (k->mark == MOY_UI_MK_FILL || k == &line) ? danger
              : k->mark == MOY_UI_MK_FILL ? km_fill_ink(&d, k, ci)
              : k->ink >= 0 ? moy_ui_token(d.look, k->ink) : -1;
    if (both && bk->mark != MOY_UI_MK_NONE) {
        int bu = k->mark == MOY_UI_MK_FILL ? ci : under;
        km_draw(&d, bk, x, y, w, h, r, flags, f, km_ci(&d, bk, bu), 255);
        if (bk->ink >= 0) {
            got = moy_ui_token(d.look, bk->ink);
        }
    }
    *ink = got;
    return MOY_UI_OK;
}

int moy_ui_focus(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int r, int flags, int fs,
                 moy_ui_box_t *content, int *ink) {
    return moy_ui_focus_in(s, b, kind, r, flags, fs, content, ink, 1);
}

int moy_ui_current_in(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int r, int flags, int fs,
                      int *ink, int crossing) {
    dd_t d;
    open_dd(s, &d, crossing);
    const moy_ui_mark_t *k = moy_ui_mark_of(clamp_kind(kind), MOY_UI_MS_ON);
    *ink = -1;
    if (k->mark == MOY_UI_MK_NONE) {
        return MOY_UI_OK;
    }
    int back = (flags & MOY_UI_MARK_BACK) != 0;
    int ci = km_ci(&d, k, -1);
    int solid = k->mark == MOY_UI_MK_FILL;
    // a fill in a window behind, or a fill or frame under content keeping
    // its own colours (a code line's, too short for a frame's width): a
    // one-unit outline, the content in its own ink
    int frame = k->mark == MOY_UI_MK_INSET || k->mark == MOY_UI_MK_OUTLINE;
    int outline = (solid && back) || ((solid || frame) && (flags & MOY_UI_MARK_KEEP));
    int own = k->ink >= 0 ? moy_ui_token(d.look, k->ink) : -1;
    if (flags & MOY_UI_MARK_INK) {
        *ink = outline ? -1 : solid ? km_fill_ink(&d, k, ci) : own;
        return MOY_UI_OK;
    }
    fs = fs < 1 ? 1 : fs;
    if (outline) {
        ring_c(&d, b.x, b.y, b.w, b.h, r, corners_of(flags), fs, d.row->pal[moy_ui_pidx(ci)],
               255);
        return MOY_UI_OK;
    }
    km_draw(&d, k, b.x, b.y, b.w, b.h, r, flags, fs, ci, back ? 153 : 255);
    *ink = solid ? km_fill_ink(&d, k, ci) : own;
    return MOY_UI_OK;
}

int moy_ui_current(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int r, int flags, int fs,
                   int *ink) {
    return moy_ui_current_in(s, b, kind, r, flags, fs, ink, 1);
}

int moy_ui_plate(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int flags, int ci, int alpha) {
    dd_t d;
    open_dd(s, &d, 0);
    if (ci >= 0) {
        tint_at(&d, b.x, b.y, b.w, b.h, r, flags, d.row->pal[moy_ui_pidx(ci)], alpha);
    }
    return MOY_UI_OK;
}

// -- the bars' parts (docs/history/look_2026-10.md step 14) --------------------------------

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
            int lw = dp->sys_line_w > 0 && is_set(dp->sys_line_c) ? dp->sys_line_w : 0;
            rrect(&d, b.x, b.y, b.w, b.h, r, 15, dp->stepped, MOY_SHAPE_ALL, &p, lw,
                  lw ? val(&d, dp->sys_line_c) : 0, -1, NULL);
            if (on) {
                moy_ui_current_in(s, b, MOY_UI_ITEM_CELL, r, 0, 1, &ink, 0);
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
            moy_ui_current_in(s, b, MOY_UI_ITEM_CELL, r, 0, 1, &ink, 0);
            return 1;
        }
        case MOY_UI_BARP_TILE:
            return moy_ui_bar_tile(s, b, MOY_THEME_NONE);
        default:
            return 0;
    }
}

// The scrim's strength, 0..255, for the draws until it is set back (a
// surface that names its own: the control center's half): the one it
// replaces.
int moy_ui_scrim_alpha(int a) {
    int was = s_scrim_a;
    s_scrim_a = (uint8_t)(a < 0 ? 0 : a > 255 ? 255 : a);
    return was;
}

int moy_ui_scrim(const moy_ui_sink_t *s, moy_ui_box_t b) {
    dd_t d;
    open_dd(s, &d, 1);
    uint16_t c = is_set(s_scrim_c) ? val(&d, s_scrim_c) : moy_shape_wire(0);
    tint(&d, b.x, b.y, b.w, b.h, 0, c, s_scrim_a, NULL);
    return MOY_UI_OK;
}

int moy_ui_scrim_io(const moy_ui_sink_t *s, moy_ui_box_t b, const void *src, void *also,
                    size_t len) {
    if (src == NULL && also == NULL) {
        return moy_ui_scrim(s, b);
    }
    dd_t d;
    open_dd(s, &d, 1);
    if (len < d.t.cap * 2u) {
        return MOY_UI_BAD;
    }
    uint16_t c = is_set(s_scrim_c) ? val(&d, s_scrim_c) : moy_shape_wire(0);
    if (s_scrim_a >= 255) {
        // opaque: nothing under it is read; what it filled goes to `also`
        tint(&d, b.x, b.y, b.w, b.h, 0, c, s_scrim_a, NULL);
        int rows = (int)(d.t.cap / (size_t)d.t.dw);
        int x0 = b.x - d.t.cam_x, y0 = b.y - d.t.cam_y, x1 = x0 + b.w, y1 = y0 + b.h;
        x0 = x0 > d.t.cx0 ? x0 : d.t.cx0;
        x0 = x0 > 0 ? x0 : 0;
        y0 = y0 > d.t.cy0 ? y0 : d.t.cy0;
        y0 = y0 > 0 ? y0 : 0;
        x1 = x1 < d.t.cx1 ? x1 : d.t.cx1;
        x1 = x1 < d.t.dw ? x1 : d.t.dw;
        y1 = y1 < d.t.cy1 ? y1 : d.t.cy1;
        y1 = y1 < rows ? y1 : rows;
        for (int y = y0; also != NULL && x1 > x0 && y < y1; y++) {
            size_t o = (size_t)y * (size_t)d.t.dw + (size_t)x0;
            memcpy((uint16_t *)also + o, d.t.px + o, (size_t)(x1 - x0) * 2u);
        }
        return MOY_UI_OK;
    }
    moy_shape_shadow_io(src != NULL ? (const uint16_t *)src : d.t.px, (uint16_t *)also);
    tint(&d, b.x, b.y, b.w, b.h, 0, c, s_scrim_a, NULL);
    moy_shape_shadow_io(NULL, NULL);
    return MOY_UI_OK;
}

int moy_ui_bar_tile(const moy_ui_sink_t *s, moy_ui_box_t b, int32_t fill) {
    dd_t d;
    open_dd(s, &d, 1);
    const moy_ui_depth_t *dp = &s_depth;
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
