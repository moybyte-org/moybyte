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

int moy_ui_depth_use(const char *name, size_t n, int light) {
    moy_ui_depth_t *d = &s_depth;
    memset(d, 0, sizeof(*d));
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
    for (int i = 0; !any && i < MOY_THEME_LEAVES; i++) {
        any = lk->v[i] != MOY_THEME_NONE;
    }
    if (!any) {
        return 0;
    }
    moy_theme_look(name, n, variant, 1, lk);
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
