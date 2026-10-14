// The toolkit's primitives (moy_ui.h): each is the Python body it replaced
// (tests/ui_twin.py), op for op -- the same fills, edges, glyphs and texts in
// the same order, with every `//` a floor division and every truncation at a
// codepoint. They draw through a sink, never a canvas object.

#include <string.h>

#include "moy_ui.h"
#include "moy_canvas.h"

// -- the font text draws in ---------------------------------------------------------
//
// A primitive's text reads the metrics of the font that draws it
// (docs/theming_2026-09.md §4.7): its role's (moy_ui_font_use), and
// petme128's on a compiled run's canvas and on the game canvas, which a theme
// does not reach. With petme128 every width is 8 px a byte and every line 8
// rows, at the scale.

typedef struct {
    uint32_t id;
    int w, line;                // the glyph box's width (a cell), the line advance
} tfont_t;

static tfont_t tfont_of(uint32_t id, const moy_font_info_t *fi) {
    tfont_t f = {id, fi->w, fi->line};
    return f;
}

static tfont_t role_font(int role) {
    moy_font_info_t fi;
    uint32_t id = moy_ui_font(role, &fi);
    return tfont_of(id, &fi);
}

static tfont_t petme(void) {
    tfont_t f = {MOY_FONT_PETME128, MOY_TEXT_CELL, MOY_TEXT_CELL};
    return f;
}

// The pixels `n` bytes of `p` draw in, and the bytes of `t` that fit `maxw`.
static int t_w(const tfont_t *f, const uint8_t *p, size_t n, int scale) {
    return moy_text_width(f->id, p, n, scale);
}

static int t_fit(const tfont_t *f, moy_ui_span_t t, int maxw, int scale) {
    return (int)moy_text_fit_px(f->id, t.p, t.n, maxw, scale);
}

// -- the sink --------------------------------------------------------------------
//
// Both sinks are a canvas row by the time a primitive draws: ROW's is the
// glass's (moy_ui_sink.c), RUN's a row a compiled run's canvas lends
// (moy_ui_wasm.c).

typedef struct {
    moy_canvas_row_t *row;
    moy_text_target_t t;
    int tfs;                    // the scale print draws at: the row's font scale
    tfont_t f;                  // the font text draws in
    int run;                    // a compiled run's canvas: petme128 whatever the role
    int look;
    const moy_ui_shape_t *sh;   // the theme's shape
    // The part being drawn: its text effect and that effect's colour, and the
    // pressed-label offset (use_part). Zero until a primitive names its part.
    int fx, fx_c, pdx, pdy;
} draw_t;

static void open_draw(const moy_ui_sink_t *s, draw_t *d) {
    moy_ui_count_call();
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
    d->tfs = s->fs;
    d->run = s->kind == MOY_UI_SINK_RUN;
    d->f = d->run ? petme() : role_font(MOY_UI_FONT_UI);
    d->look = s->look;
    d->sh = moy_ui_shape();
    d->fx = d->fx_c = d->pdx = d->pdy = 0;
}

// Text that follows draws in `role`'s font.
static void use_font(draw_t *d, int role) {
    if (!d->run) {
        d->f = role_font(role);
    }
}

// A primitive on the game canvas (game_btn, game_icon_btn, text_field): its
// text is petme128's whatever the theme names (§6).
static void open_game(const moy_ui_sink_t *s, draw_t *d) {
    open_draw(s, d);
    d->f = petme();
}

// Name the part (a kind) and its state for the text and glyphs that follow:
// its text effect, and in the pressed state the label's offset at scale fs.
static void use_part(draw_t *d, int kind, int state, int fs) {
    if (d->sh == NULL) {
        return;
    }
    int role = 0;
    d->fx = moy_ui_shape_effect(d->sh, kind, state, &role);
    d->fx_c = d->fx ? moy_ui_token(d->look, role) : -1;
    if (d->fx_c < 0) {
        d->fx = 0;
    }
    if (state == MOY_UI_PRESSED) {
        d->pdx = d->sh->metric[MOY_THEME_M_PRESS_DX] * fs;
        d->pdy = d->sh->metric[MOY_THEME_M_PRESS_DY] * fs;
    }
}

static void fill(draw_t *d, int x, int y, int w, int h, int ci) {
    moy_target_fill(&d->t, x, y, w, h, d->row->pal[ci & 63]);
    d->row->st[MOY_ST_N_FILL]++;
}

// The canvas's rectb: four clipped fills, one op.
static void rectb(draw_t *d, int x, int y, int w, int h, int ci) {
    uint16_t c = d->row->pal[ci & 63];
    moy_target_fill(&d->t, x, y, w, 1, c);
    moy_target_fill(&d->t, x, y + h - 1, w, 1, c);
    moy_target_fill(&d->t, x, y, 1, h, c);
    moy_target_fill(&d->t, x + w - 1, y, 1, h, c);
    d->row->st[MOY_ST_N_FILL]++;
}

static void rings(draw_t *d, int x, int y, int w, int h, int ci, int n) {
    rectb(d, x, y, w, h, ci);
    for (int i = 1; i < n; i++) {
        rectb(d, x + i, y + i, w - 2 * i, h - 2 * i, ci);
    }
}

static void text_at(draw_t *d, const uint8_t *s, size_t n, int x, int y, int ci) {
    moy_text(&d->t, d->f.id, MOY_TEXT_PY, s, n, x, y, d->row->pal[ci & 63],
             d->tfs, MOY_TEXT_LEFT, -1);
    d->row->st[MOY_ST_N_TEXT]++;
}

// A label in the part's effect (docs/theming_2026-09.md §4.3): `shadow` is
// the label once more in the effect's colour one pixel down and right,
// under it; `emboss` is that up and left in the effect's colour and down and
// right in the label's own.
static void text(draw_t *d, const uint8_t *s, size_t n, int x, int y, int ci) {
    x += d->pdx;
    y += d->pdy;
    if (d->fx == MOY_THEME_FX_SHADOW) {
        text_at(d, s, n, x + 1, y + 1, d->fx_c);
    } else if (d->fx == MOY_THEME_FX_EMBOSS) {
        text_at(d, s, n, x - 1, y - 1, d->fx_c);
        text_at(d, s, n, x + 1, y + 1, ci);
    }
    text_at(d, s, n, x, y, ci);
}

static void glyph(draw_t *d, int g, int x, int y, int w, int h, int ci, int scale) {
    d->row->st[MOY_ST_N_FILL] += moy_ui_glyph_draw(&d->t, g, x + d->pdx, y + d->pdy, w, h,
                                                   d->row->pal[ci & 63], scale);
}

// The part's surface in `state`, painted in place of its field and edge when
// the theme gives it one: 1, or 0 for "draw the field and edge as ever".
static int surface(draw_t *d, moy_ui_box_t b, int part, int state, int field, int edge) {
    const moy_ui_surface_t *sf = moy_ui_shape_surface(d->sh, part, state);
    if (sf == NULL) {
        return 0;
    }
    d->row->st[MOY_ST_N_FILL] += moy_ui_paint_surface(&d->t, d->row->pal, d->look, b, sf, field,
                                                      edge, d->sh->metric[MOY_THEME_M_RADIUS]);
    return 1;
}

int moy_ui_sink_glyph(const moy_ui_sink_t *s, int g, int x, int y, int w, int h, int ci,
                      int scale) {
    draw_t d;
    open_draw(s, &d);
    glyph(&d, g, x, y, w, h, ci, scale);
    return MOY_UI_OK;
}

int moy_ui_fdiv(int a, int b) {
    int q = a / b, r = a % b;
    return (r != 0 && ((r < 0) != (b < 0))) ? q - 1 : q;
}

static int metric(int kind, int slot) {
    int n = 0;
    const int16_t *m = moy_ui_metrics(kind, &n);
    return m != NULL && slot < n ? m[slot] : 0;
}

int moy_ui_state_of(int flags, int interact) {
    if (flags & MOY_UI_F_DISABLED) {
        return MOY_UI_DISABLED;
    }
    if (interact == MOY_UI_PRESSED) {
        return MOY_UI_PRESSED;
    }
    if (flags & MOY_UI_F_HOT) {
        return MOY_UI_HOT;
    }
    if (flags & MOY_UI_F_ON) {
        return MOY_UI_ON;
    }
    if (interact == MOY_UI_HOVER) {
        return MOY_UI_HOVER;
    }
    return MOY_UI_REST;
}

// The (field, ink, edge) a widget paints: the caller's where it gave one,
// else the style's for its kind and state under the sink's look.
static void colours(const draw_t *d, int kind, int state, const int16_t over[3],
                    int16_t out[3]) {
    moy_ui_colors(d->look, kind, state, out);
    for (int i = 0; i < 3; i++) {
        if (over != NULL && over[i] != MOY_UI_FROM_STYLE) {
            out[i] = over[i];
        }
    }
}

static int dflt(int16_t v, int d) {
    return v == MOY_UI_DEFAULT ? d : v;
}

// -- row ---------------------------------------------------------------------------

int moy_ui_row(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
               const moy_ui_row_t *r, int phase) {
    draw_t d;
    open_draw(s, &d);
    int fs = r->fs, x = b.x, y = b.y, w = b.w, h = b.h;
    int16_t c[3];
    int state = moy_ui_state_of(flags, interact);
    colours(&d, kind, state, r->colors, c);
    int field = c[0], ink = c[1], edge = c[2];
    if ((phase & MOY_UI_PHASE_FRAME) && !surface(&d, b, kind, state, field, edge)) {
        if (field >= 0) {
            fill(&d, x, y, w, h, field);
        }
        int n = metric(MOY_UI_K_ROW, MOY_UI_M_ROW_EDGE);
        if ((flags & MOY_UI_F_EDGE) && edge >= 0 && n > 0) {
            rings(&d, x, y, w, h, edge, n);
        }
    }
    if (!(phase & MOY_UI_PHASE_CONTENT)) {
        return MOY_UI_OK;
    }
    use_part(&d, kind, state, fs);
    int pad = dflt(r->pad, metric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs);
    int ts = fs * r->scale;     // the label's measuring scale
    int tx = x + pad;
    int right = x + w - dflt(r->pad_right, pad);
    if (flags & MOY_UI_F_ICON) {
        tx += metric(MOY_UI_K_ROW, MOY_UI_M_ROW_ICON) * fs + pad;
    } else if (flags & MOY_UI_F_GLYPH) {
        int box = dflt(r->glyph_size, metric(MOY_UI_K_ROW, MOY_UI_M_ROW_GLYPH)) * fs;
        if (r->glyph >= 0) {
            glyph(&d, r->glyph, tx, y + moy_ui_fdiv(h - box, 2), box, box,
                  r->glyph_ink == MOY_UI_FROM_STYLE ? ink : r->glyph_ink, d.tfs);
        }
        tx += box + pad;
    }
    int ty = y + (r->text_dy == MOY_UI_DEFAULT ? moy_ui_fdiv(h - d.f.line * ts, 2)
                                               : r->text_dy);
    if (r->value.p != NULL) {
        size_t vn = (size_t)t_fit(&d.f, r->value, right - tx, ts);
        if (vn) {
            int vw = t_w(&d.f, r->value.p, vn, ts);
            text(&d, r->value.p, vn, right - vw, ty,
                 r->value_ink == MOY_UI_FROM_STYLE ? ink : r->value_ink);
            right -= vw + metric(MOY_UI_K_ROW, MOY_UI_M_ROW_GAP) * d.f.w * ts;
        }
    }
    if (r->label.n) {
        size_t ln = (size_t)t_fit(&d.f, r->label, right - tx, ts);
        if (ln) {
            text(&d, r->label.p, ln, tx, ty, ink);
        }
    }
    return MOY_UI_OK;
}

// -- cell --------------------------------------------------------------------------

int moy_ui_cell(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                const moy_ui_cell_t *k, int phase, moy_ui_box_t *art) {
    draw_t d;
    open_draw(s, &d);
    int fs = k->fs, x = b.x, y = b.y, w = b.w, h = b.h;
    int state = moy_ui_state_of(flags, interact);
    int16_t c[3];
    colours(&d, kind, state, k->colors, c);
    int field = c[0], ink = c[1], edge = c[2];
    int pad = dflt(k->pad, metric(MOY_UI_K_CELL, MOY_UI_M_CELL_PAD) * fs);
    int has_label = (flags & MOY_UI_F_LABEL) != 0;
    int cap = dflt(k->caption_h, has_label ? metric(MOY_UI_K_CELL, MOY_UI_M_CELL_CAPTION) * fs
                                           : 0);
    int n = metric(MOY_UI_K_CELL, MOY_UI_M_CELL_EDGE);
    if (phase & MOY_UI_PHASE_FRAME) {
        if (surface(&d, b, kind, state, field, edge)) {
            n = 0;                          // the surface drew the edge
        } else if (field >= 0) {
            fill(&d, x, y, w, h, field);
        }
        use_part(&d, kind, state, fs);
        if (has_label && cap > 0) {
            int cap_y = y + h - cap;
            int cap_ink = ink;
            if (flags & MOY_UI_F_BAND) {
                int16_t bc[3];
                moy_ui_colors(d.look, MOY_UI_K_CELL_BAND, state, bc);
                int bf = k->band_fill == MOY_UI_FROM_STYLE ? bc[0] : k->band_fill;
                int bi = k->band_ink == MOY_UI_FROM_STYLE ? bc[1] : k->band_ink;
                if (bf >= 0) {
                    fill(&d, x, cap_y, w, cap, bf);
                }
                cap_ink = bi;
            }
            size_t ln = (size_t)t_fit(&d.f, k->label, w - 2 * pad, fs);
            if (ln) {
                int lw = t_w(&d.f, k->label.p, ln, fs);
                int lx;
                if (k->text_dx != MOY_UI_DEFAULT) {
                    lx = x + k->text_dx;
                } else if (flags & MOY_UI_F_BAND) {
                    lx = x + pad + metric(MOY_UI_K_CELL, MOY_UI_M_CELL_INDENT) * fs;
                } else {
                    lx = x + moy_ui_fdiv(w - lw, 2);
                }
                int ly = k->text_dy != MOY_UI_DEFAULT ? y + k->text_dy
                                                      : cap_y + moy_ui_fdiv(cap - d.f.line * fs, 2);
                text(&d, k->label.p, ln, lx, ly, cap_ink);
            }
        }
        if (edge >= 0 && n > 0 && !(flags & MOY_UI_F_EDGE_LAST)) {
            rings(&d, x, y, w, h, edge, n);
        }
    }
    int ax = x + pad, ay = y + pad;
    int aw = w - 2 * pad > 0 ? w - 2 * pad : 0;
    int ah = h - pad - cap > 0 ? h - pad - cap : 0;
    if (art != NULL) {
        art->x = (int16_t)ax;
        art->y = (int16_t)ay;
        art->w = (int16_t)aw;
        art->h = (int16_t)ah;
    }
    if (phase & MOY_UI_PHASE_CONTENT) {
        if (!(phase & MOY_UI_PHASE_FRAME)) {
            n = moy_ui_shape_surface(d.sh, kind, state) != NULL ? 0 : n;
            use_part(&d, kind, state, fs);
        }
        if (!(flags & MOY_UI_F_ICON) && (flags & MOY_UI_F_GLYPH) && k->glyph >= 0) {
            int box = metric(MOY_UI_K_CELL, MOY_UI_M_CELL_GLYPH) * fs;
            glyph(&d, k->glyph, ax + moy_ui_fdiv(aw - box, 2), ay + moy_ui_fdiv(ah - box, 2),
                  box, box, ink, d.tfs);
        }
        if (edge >= 0 && n > 0 && (flags & MOY_UI_F_EDGE_LAST)) {
            rings(&d, x, y, w, h, edge, n);
        }
    }
    return MOY_UI_OK;
}

// -- label ---------------------------------------------------------------------------

int moy_ui_label(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t, int ink,
                 int align) {
    draw_t d;
    open_draw(s, &d);
    size_t n = (size_t)t_fit(&d.f, t, b.w, d.tfs);
    int w = t_w(&d.f, t.p, n, d.tfs);
    int x = b.x;
    if (align == MOY_TEXT_CENTER) {
        x += moy_ui_fdiv(b.w - w, 2);
    } else if (align == MOY_TEXT_RIGHT) {
        x += b.w - w;
    }
    if (n) {
        text(&d, t.p, n, x, b.y + moy_ui_fdiv(b.h - d.f.line * d.tfs, 2), ink);
    }
    return w;
}

// -- scrolling ------------------------------------------------------------------------

int moy_ui_bar_rect(moy_ui_box_t view, int content, int offset, int horizontal, int fs,
                    int bar_w, moy_ui_box_t *out) {
    int vis = horizontal ? view.w : view.h;
    int m = content - vis > 0 ? content - vis : 0;
    if (m <= 0 || vis <= 0) {
        return 0;
    }
    int bw = bar_w * fs;
    int min = metric(MOY_UI_K_SCROLLBAR, MOY_UI_M_SCROLLBAR_MIN) * fs;
    int th = moy_ui_fdiv(vis * vis, content);
    if (th < min) {
        th = min;
    }
    int tp = moy_ui_fdiv((vis - th) * offset, m);
    if (horizontal) {
        *out = (moy_ui_box_t){(int16_t)(view.x + tp), (int16_t)(view.y + view.h - bw),
                              (int16_t)th, (int16_t)bw};
    } else {
        *out = (moy_ui_box_t){(int16_t)(view.x + view.w - bw), (int16_t)(view.y + tp),
                              (int16_t)bw, (int16_t)th};
    }
    return 1;
}

int moy_ui_scrollbar(const moy_ui_sink_t *s, moy_ui_box_t view, int content, int offset,
                     int horizontal, int fs, int bar_w) {
    moy_ui_box_t r;
    if (!moy_ui_bar_rect(view, content, offset, horizontal, fs, bar_w, &r)) {
        return MOY_UI_OK;
    }
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    moy_ui_colors(d.look, MOY_UI_K_SCROLLBAR, MOY_UI_REST, c);
    int bw = bar_w * fs;
    if (c[0] >= 0) {
        if (horizontal) {
            fill(&d, view.x, view.y + view.h - bw, view.w, bw, c[0]);
        } else {
            fill(&d, view.x + view.w - bw, view.y, bw, view.h, c[0]);
        }
    }
    if (c[1] >= 0) {
        fill(&d, r.x, r.y, r.w, r.h, c[1]);
    }
    return MOY_UI_OK;
}

int moy_ui_scroll_cues(const moy_ui_sink_t *s, int ux, int uy, int dx, int dy, int can_up,
                       int can_dn, int ci) {
    draw_t d;
    open_draw(s, &d);
    if (can_up) {
        text(&d, (const uint8_t *)"^", 1, ux, uy, ci);
    }
    if (can_dn) {
        text(&d, (const uint8_t *)"v", 1, dx, dy, ci);
    }
    return MOY_UI_OK;
}

int moy_ui_fill_uncovered(const moy_ui_sink_t *s, moy_ui_box_t in, moy_ui_box_t out,
                          int ci) {
    if (in.w <= 0 || in.h <= 0) {
        return MOY_UI_OK;
    }
    draw_t d;
    open_draw(s, &d);
    int ix = in.x, iy = in.y, ix1 = in.x + in.w, iy1 = in.y + in.h;
    int ox = out.x, oy = out.y, ox1 = out.x + out.w, oy1 = out.y + out.h;
    if (ox >= ix1 || ox1 <= ix || oy >= iy1 || oy1 <= iy) {
        fill(&d, ix, iy, in.w, in.h, ci);
        return MOY_UI_OK;
    }
    if (iy < oy) {
        fill(&d, ix, iy, in.w, oy - iy, ci);
    }
    if (iy1 > oy1) {
        fill(&d, ix, oy1, in.w, iy1 - oy1, ci);
    }
    int my0 = iy > oy ? iy : oy;
    int my1 = iy1 < oy1 ? iy1 : oy1;
    if (my1 > my0) {
        if (ix < ox) {
            fill(&d, ix, my0, ox - ix, my1 - my0, ci);
        }
        if (ix1 > ox1) {
            fill(&d, ox1, my0, ix1 - ox1, my1 - my0, ci);
        }
    }
    return MOY_UI_OK;
}

// -- list ----------------------------------------------------------------------------

int moy_ui_list(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, const uint8_t *items,
                size_t len, int count, int row_h, int offset, int sel, int fs, int bar_w) {
    if (row_h <= 0) {
        return MOY_UI_BAD;
    }
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    int32_t *st = row->st;
    // The rows clip to the list's box, inside whatever clip the canvas has.
    int32_t saved[4] = {st[MOY_ST_CX0], st[MOY_ST_CY0], st[MOY_ST_CX1], st[MOY_ST_CY1]};
    int bx0 = b.x - st[MOY_ST_CAM_X], by0 = b.y - st[MOY_ST_CAM_Y];
    int bx1 = bx0 + b.w, by1 = by0 + b.h;
    st[MOY_ST_CX0] = bx0 > saved[0] ? bx0 : saved[0];
    st[MOY_ST_CY0] = by0 > saved[1] ? by0 : saved[1];
    st[MOY_ST_CX1] = bx1 < saved[2] ? bx1 : saved[2];
    st[MOY_ST_CY1] = by1 < saved[3] ? by1 : saved[3];
    size_t at = 0;
    for (int i = 0; i < count && at <= len; i++) {
        size_t e = at;
        while (e < len && items[e] != 0) {
            e++;
        }
        int ry = b.y + i * row_h - offset;
        if (ry + row_h > b.y && ry < b.y + b.h) {
            moy_ui_row_t r;
            memset(&r, 0, sizeof(r));
            r.label.p = items + at;
            r.label.n = (uint16_t)(e - at);
            r.glyph = -1;
            r.glyph_size = r.pad = r.pad_right = r.text_dy = MOY_UI_DEFAULT;
            r.glyph_ink = r.value_ink = MOY_UI_FROM_STYLE;
            r.colors[0] = r.colors[1] = r.colors[2] = MOY_UI_FROM_STYLE;
            r.scale = 1;
            r.fs = (int16_t)fs;
            moy_ui_box_t rb = {b.x, (int16_t)ry, (int16_t)(b.w - bar_w * fs), (int16_t)row_h};
            moy_ui_row(s, rb, kind, (i == sel ? MOY_UI_F_ON : 0) | MOY_UI_F_EDGE, -1, &r,
                       MOY_UI_PHASE_ALL);
        }
        at = e + 1;
    }
    st[MOY_ST_CX0] = saved[0];
    st[MOY_ST_CY0] = saved[1];
    st[MOY_ST_CX1] = saved[2];
    st[MOY_ST_CY1] = saved[3];
    return moy_ui_scrollbar(s, b, count * row_h, offset, 0, fs, bar_w);
}

// -- buttons -------------------------------------------------------------------------

// The button's label run: where its icon or glyph and its label start, the
// label `lw` pixels wide.
static int button_off(moy_ui_box_t b, int iw, int lw, int fs) {
    int pad = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_PAD) * fs;
    int tw = iw + lw;
    int align = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_ALIGN);
    int off;
    if (align == MOY_TEXT_CENTER) {
        off = moy_ui_fdiv(b.w - tw, 2);
    } else if (align == MOY_TEXT_RIGHT) {
        off = b.w - pad - tw;
    } else {
        off = pad;
    }
    return off < pad ? pad : off;
}

static int button_iw(int flags, int fs) {
    int pad = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_PAD) * fs;
    if (flags & MOY_UI_F_ICON) {
        return metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_ICON) * fs + pad;
    }
    if (flags & MOY_UI_F_GLYPH) {
        return metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_GLYPH) * fs + pad;
    }
    return 0;
}

int moy_ui_button_x(moy_ui_box_t b, int flags, moy_ui_span_t label, int fs) {
    int iw = button_iw(flags, fs);
    int pad = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_PAD) * fs;
    tfont_t f = role_font(MOY_UI_FONT_UI);
    int ln = t_fit(&f, label, b.w - 2 * pad - iw, fs);
    return b.x + button_off(b, iw, t_w(&f, label.p, (size_t)ln, fs), fs);
}

int moy_ui_button(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                  const moy_ui_button_t *k, int phase) {
    draw_t d;
    open_draw(s, &d);
    int fs = k->fs, x = b.x, y = b.y, w = b.w, h = b.h;
    int16_t c[3];
    int state = moy_ui_state_of(flags & ~MOY_UI_F_HOT, interact);
    colours(&d, kind, state, NULL, c);
    int bg = c[0], ink = c[1], edge = c[2];
    if ((phase & MOY_UI_PHASE_FRAME) && !surface(&d, b, kind, state, bg, edge)) {
        fill(&d, x, y, w, h, bg);
        int n = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_EDGE);
        if (n > 0) {
            rings(&d, x, y, w, h, edge, n);
        }
    }
    if (!(phase & MOY_UI_PHASE_CONTENT)) {
        return MOY_UI_OK;
    }
    use_part(&d, kind, state, fs);
    int iw = button_iw(flags, fs);
    int pad = metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_PAD) * fs;
    int ln = t_fit(&d.f, k->label, w - 2 * pad - iw, fs);
    int tx = x + button_off(b, iw, t_w(&d.f, k->label.p, (size_t)ln, fs), fs);
    if (!(flags & MOY_UI_F_ICON) && (flags & MOY_UI_F_GLYPH) && k->glyph >= 0) {
        glyph(&d, k->glyph, tx, y, metric(MOY_UI_K_BUTTON, MOY_UI_M_BUTTON_GLYPH) * fs, h, ink,
              d.tfs);
    }
    if (ln) {
        text(&d, k->label.p, (size_t)ln, tx + iw, y + moy_ui_fdiv(h - d.f.line * fs, 2), ink);
    }
    return MOY_UI_OK;
}

int moy_ui_chip(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                const moy_ui_button_t *k, int phase) {
    draw_t d;
    open_draw(s, &d);
    int fs = k->fs, x = b.x, y = b.y, w = b.w, h = b.h;
    int16_t c[3];
    int state = moy_ui_state_of(flags, interact);
    colours(&d, kind, state, k->colors, c);
    int bg = c[0], ink = c[1], edge = c[2];
    if ((phase & MOY_UI_PHASE_FRAME) && !surface(&d, b, kind, state, bg, edge)) {
        if (bg >= 0) {
            fill(&d, x, y, w, h, bg);
        }
        int n = metric(MOY_UI_K_CHIP, MOY_UI_M_CHIP_EDGE);
        if (n > 0 && edge >= 0) {
            rings(&d, x, y, w, h, edge, n);
        }
    }
    if (!(phase & MOY_UI_PHASE_CONTENT)) {
        return MOY_UI_OK;
    }
    use_part(&d, kind, state, fs);
    if (flags & MOY_UI_F_GLYPH) {
        // The glyph REPLACES the label.
        if (k->glyph >= 0) {
            glyph(&d, k->glyph, x, y, w, h, ink, d.tfs);
        }
        return MOY_UI_OK;
    }
    int ln = t_fit(&d.f, k->label, w - metric(MOY_UI_K_CHIP, MOY_UI_M_CHIP_CLIP), fs);
    int lw = t_w(&d.f, k->label.p, (size_t)ln, fs);
    int minx = metric(MOY_UI_K_CHIP, MOY_UI_M_CHIP_MIN_X);
    int align = metric(MOY_UI_K_CHIP, MOY_UI_M_CHIP_ALIGN);
    int ox;
    if (align == MOY_TEXT_CENTER) {
        ox = moy_ui_fdiv(w - lw, 2);
    } else if (align == MOY_TEXT_RIGHT) {
        ox = w - minx - lw;
    } else {
        ox = minx;
    }
    if (ox < minx) {
        ox = minx;
    }
    int oy = moy_ui_fdiv(h - d.f.line * fs, 2);
    int miny = metric(MOY_UI_K_CHIP, MOY_UI_M_CHIP_MIN_Y);
    if (oy < miny) {
        oy = miny;
    }
    text(&d, k->label.p, (size_t)ln, x + ox, y + oy, ink);
    return MOY_UI_OK;
}

// -- tabs and status ------------------------------------------------------------------

// The tab chips laid left to right (draw-what-fits; every tab collapses to
// its icon when the labelled row would not fit). `labels` are NUL-separated.
int moy_ui_tab_rects(moy_ui_box_t r, const uint8_t *labels, size_t len, int count, int fs,
                     int gap, moy_ui_box_t *out, int *labels_on) {
    int pad = metric(MOY_UI_K_TAB, MOY_UI_M_TAB_PAD) * fs;
    int icon_w = metric(MOY_UI_K_TAB, MOY_UI_M_TAB_ICON) * fs;
    tfont_t f = role_font(MOY_UI_FONT_UI);
    if (gap == MOY_UI_DEFAULT) {
        gap = metric(MOY_UI_K_TAB, MOY_UI_M_TAB_GAP) * fs;
    }
    int total = 0;
    size_t at = 0;
    for (int i = 0; i < count && at <= len; i++) {
        size_t e = at;
        while (e < len && labels[e] != 0) {
            e++;
        }
        int n = (int)(e - at);
        total += (n ? icon_w + pad + t_w(&f, labels + at, (size_t)n, fs) + pad
                    : icon_w + 2 * pad) + gap;
        at = e + 1;
    }
    int on = !(total - gap > r.w);
    *labels_on = on;
    int x = r.x, placed = 0;
    at = 0;
    for (int i = 0; i < count && at <= len; i++) {
        size_t e = at;
        while (e < len && labels[e] != 0) {
            e++;
        }
        int n = (int)(e - at);
        int cw = (on && n) ? icon_w + pad + t_w(&f, labels + at, (size_t)n, fs) + pad
                           : icon_w + 2 * pad;
        if (x + cw > r.x + r.w) {
            break;
        }
        out[placed++] = (moy_ui_box_t){(int16_t)x, r.y, (int16_t)cw, r.h};
        x += cw + gap;
        at = e + 1;
    }
    return placed;
}

// One tab chip: its field (the ON look when `active`), the icon slot the
// caller fills between the phases, its label when `labels_on`.
int moy_ui_tab(const moy_ui_sink_t *s, moy_ui_box_t r, int active, int labels_on,
               moy_ui_span_t label, int ink, int fs, int phase) {
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    int state = active ? MOY_UI_ON : MOY_UI_REST;
    moy_ui_colors(d.look, MOY_UI_K_TAB, state, c);
    int ink_i = active ? c[1] : (ink == MOY_UI_FROM_STYLE ? c[1] : ink);
    if ((phase & MOY_UI_PHASE_FRAME) && !surface(&d, r, MOY_UI_K_TAB, state, c[0], c[2])
        && c[0] >= 0) {
        fill(&d, r.x, r.y, r.w, r.h, c[0]);
    }
    if ((phase & MOY_UI_PHASE_CONTENT) && labels_on) {
        use_part(&d, MOY_UI_K_TAB, state, fs);
        int pad = metric(MOY_UI_K_TAB, MOY_UI_M_TAB_PAD) * fs;
        int box = metric(MOY_UI_K_TAB, MOY_UI_M_TAB_ICON) * fs;
        text(&d, label.p, label.n, r.x + pad + box + pad,
             r.y + moy_ui_fdiv(r.h - d.f.line * fs, 2), ink_i);
    }
    return MOY_UI_OK;
}

int moy_ui_status(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                  int count, int fs) {
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    moy_ui_colors(d.look, MOY_UI_K_STATUS, MOY_UI_REST, c);
    fill(&d, b.x, b.y, b.w, b.h, c[0]);
    int rule = metric(MOY_UI_K_STATUS, MOY_UI_M_STATUS_RULE);
    if (c[2] >= 0 && rule > 0) {
        fill(&d, b.x, b.y, b.w, rule * fs, c[2]);
    }
    int tx = b.x + metric(MOY_UI_K_STATUS, MOY_UI_M_STATUS_PAD) * fs;
    int ty = b.y + moy_ui_fdiv(b.h - d.f.line * fs
                                   + metric(MOY_UI_K_STATUS, MOY_UI_M_STATUS_DY) * fs,
                               2);
    int gap = metric(MOY_UI_K_STATUS, MOY_UI_M_STATUS_GAP);
    size_t at = 0;
    for (int i = 0; i < count && at <= len; i++) {
        size_t e = at;
        while (e < len && items[e] != 0) {
            e++;
        }
        int n = (int)(e - at);
        int iw = t_w(&d.f, items + at, (size_t)n, fs);
        if (tx + iw > b.x + b.w) {
            break;
        }
        text(&d, items + at, (size_t)n, tx, ty, c[1]);
        tx += iw + gap * d.f.w * fs;
        at = e + 1;
    }
    return MOY_UI_OK;
}

// -- the game-canvas family -------------------------------------------------------------

int moy_ui_game_btn(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t label, int fill_c,
                    int fs) {
    draw_t d;
    open_game(s, &d);
    fill(&d, b.x, b.y, b.w, b.h, fill_c);
    rectb(&d, b.x, b.y, b.w, b.h, metric(MOY_UI_K_GAME_BTN, MOY_UI_M_GAME_BTN_RING));
    int pad = metric(MOY_UI_K_GAME_BTN, MOY_UI_M_GAME_BTN_PAD);
    int ink = metric(MOY_UI_K_GAME_BTN, MOY_UI_M_GAME_BTN_INK);
    // The frozen quirk (style.json's quirks): at scale 1 the label centres
    // with one line unscaled; above it, at the scale.
    if (fs <= 1) {
        text(&d, label.p, label.n, b.x + pad, b.y + moy_ui_fdiv(b.h - d.f.line, 2), ink);
    } else {
        text(&d, label.p, label.n, b.x + pad * fs, b.y + moy_ui_fdiv(b.h - d.f.line * fs, 2),
             ink);
    }
    return MOY_UI_OK;
}

int moy_ui_game_icon_btn(const moy_ui_sink_t *s, moy_ui_box_t b, int g, moy_ui_span_t label,
                         int fill_c, int fs, int phase) {
    draw_t d;
    open_game(s, &d);
    int ink = metric(MOY_UI_K_GAME_ICON_BTN, MOY_UI_M_GAME_ICON_BTN_INK);
    if (phase & MOY_UI_PHASE_FRAME) {
        fill(&d, b.x, b.y, b.w, b.h, fill_c);
        rectb(&d, b.x, b.y, b.w, b.h,
              metric(MOY_UI_K_GAME_ICON_BTN, MOY_UI_M_GAME_ICON_BTN_RING));
    }
    if (!(phase & MOY_UI_PHASE_CONTENT)) {
        return MOY_UI_OK;
    }
    if (g >= 0) {
        glyph(&d, g, b.x + metric(MOY_UI_K_GAME_ICON_BTN, MOY_UI_M_GAME_ICON_BTN_PAD) * fs,
              b.y, metric(MOY_UI_K_GAME_ICON_BTN, MOY_UI_M_GAME_ICON_BTN_GLYPH) * fs, b.h, ink,
              d.tfs);
    }
    if (label.n) {
        text(&d, label.p, label.n,
             b.x + metric(MOY_UI_K_GAME_ICON_BTN, MOY_UI_M_GAME_ICON_BTN_LABEL) * fs,
             b.y + moy_ui_fdiv(b.h - d.f.line * fs, 2), ink);
    }
    return MOY_UI_OK;
}

int moy_ui_mini_btn(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t label, int fill_c,
                    int lx, int ly) {
    draw_t d;
    open_draw(s, &d);
    fill(&d, b.x, b.y, b.w, b.h, fill_c);
    text(&d, label.p, label.n, lx + metric(MOY_UI_K_MINI_BTN, MOY_UI_M_MINI_BTN_PAD_X),
         ly + metric(MOY_UI_K_MINI_BTN, MOY_UI_M_MINI_BTN_PAD_Y),
         metric(MOY_UI_K_MINI_BTN, MOY_UI_M_MINI_BTN_INK));
    return MOY_UI_OK;
}

// -- frames -----------------------------------------------------------------------------

int moy_ui_toolbar(const moy_ui_sink_t *s, moy_ui_box_t b) {
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    moy_ui_colors(d.look, MOY_UI_K_TOOLBAR, MOY_UI_REST, c);
    if (!surface(&d, b, MOY_UI_K_TOOLBAR, MOY_UI_REST, c[0], c[2])) {
        fill(&d, b.x, b.y, b.w, b.h, c[0]);
    }
    return MOY_UI_OK;
}

int moy_ui_dialog(const moy_ui_sink_t *s, moy_ui_box_t b, int ring, int fill_c) {
    draw_t d;
    open_draw(s, &d);
    fill(&d, b.x, b.y, b.w, b.h,
         fill_c == MOY_UI_FROM_STYLE ? metric(MOY_UI_K_DIALOG, MOY_UI_M_DIALOG_FILL) : fill_c);
    int c = ring == MOY_UI_FROM_STYLE ? metric(MOY_UI_K_DIALOG, MOY_UI_M_DIALOG_RING) : ring;
    int n = metric(MOY_UI_K_DIALOG, MOY_UI_M_DIALOG_EDGE);
    if (n > 0) {
        rings(&d, b.x, b.y, b.w, b.h, c, n);
    }
    return MOY_UI_OK;
}

int moy_ui_text_field(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t,
                      moy_ui_span_t hint, const int16_t colors[5], int fs) {
    draw_t d;
    open_game(s, &d);
    int16_t c[5];
    static const int slot[5] = {MOY_UI_M_TEXT_FIELD_FIELD, MOY_UI_M_TEXT_FIELD_RING,
                                MOY_UI_M_TEXT_FIELD_INK, MOY_UI_M_TEXT_FIELD_HINT,
                                MOY_UI_M_TEXT_FIELD_CARET};
    for (int i = 0; i < 5; i++) {
        c[i] = colors != NULL ? colors[i] : (int16_t)metric(MOY_UI_K_TEXT_FIELD, slot[i]);
    }
    int px = metric(MOY_UI_K_TEXT_FIELD, MOY_UI_M_TEXT_FIELD_PAD_X) * fs;
    int py = metric(MOY_UI_K_TEXT_FIELD, MOY_UI_M_TEXT_FIELD_PAD_Y) * fs;
    if (c[0] >= 0) {
        fill(&d, b.x, b.y, b.w, b.h, c[0]);
    }
    if (c[1] >= 0) {
        rectb(&d, b.x, b.y, b.w, b.h, c[1]);
    }
    if (t.n) {
        text(&d, t.p, t.n, b.x + px, b.y + py, c[2]);
    } else if (hint.n) {
        text(&d, hint.p, hint.n, b.x + px, b.y + py, c[3]);
    }
    if (c[4] >= 0) {
        fill(&d, b.x + px + t_w(&d.f, t.p, t.n, fs), b.y + py,
             metric(MOY_UI_K_TEXT_FIELD, MOY_UI_M_TEXT_FIELD_CARET_W) * fs,
             metric(MOY_UI_K_TEXT_FIELD, MOY_UI_M_TEXT_FIELD_CARET_H) * fs, c[4]);
    }
    return MOY_UI_OK;
}

moy_ui_box_t moy_ui_inset(moy_ui_box_t r, int dx, int dy) {
    int w = r.w - 2 * dx, h = r.h - 2 * dy;
    return (moy_ui_box_t){(int16_t)(r.x + dx), (int16_t)(r.y + dy), (int16_t)(w > 0 ? w : 0),
                          (int16_t)(h > 0 ? h : 0)};
}

int moy_ui_panel(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                 moy_ui_box_t *content) {
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    moy_ui_colors(d.look, MOY_UI_K_PANEL, MOY_UI_REST, c);
    if (!surface(&d, b, MOY_UI_K_PANEL, MOY_UI_REST, c[0], c[2])) {
        fill(&d, b.x, b.y, b.w, b.h, c[0]);
        int n = metric(MOY_UI_K_PANEL, MOY_UI_M_PANEL_EDGE) * fs;
        for (int i = 0; i < n; i++) {       // the border grows OUTWARD with fs
            rectb(&d, b.x - i, b.y - i, b.w + 2 * i, b.h + 2 * i, c[2]);
        }
    }
    int in = metric(MOY_UI_K_PANEL, MOY_UI_M_PANEL_INSET);
    moy_ui_box_t ct = moy_ui_inset(b, in, in);
    if (title.n) {
        int sh = metric(MOY_UI_K_PANEL, MOY_UI_M_PANEL_STRIP) * fs;
        if (sh > ct.h) {
            sh = ct.h;
        }
        if (sh < 0) {
            sh = 0;
        }
        moy_ui_box_t strip = {ct.x, ct.y, ct.w, (int16_t)sh};
        ct = (moy_ui_box_t){ct.x, (int16_t)(ct.y + sh), ct.w, (int16_t)(ct.h - sh)};
        int16_t tc[3];
        moy_ui_colors(d.look, MOY_UI_K_PANEL_TITLE, MOY_UI_REST, tc);
        if (!surface(&d, strip, MOY_UI_K_PANEL_TITLE, MOY_UI_REST, tc[0], tc[2])) {
            fill(&d, strip.x, strip.y, strip.w, strip.h, tc[0]);
        }
        use_part(&d, MOY_UI_K_PANEL_TITLE, MOY_UI_REST, fs);
        use_font(&d, MOY_UI_FONT_TITLE);
        text(&d, title.p, title.n, strip.x + metric(MOY_UI_K_PANEL, MOY_UI_M_PANEL_PAD) * fs,
             strip.y + moy_ui_fdiv(strip.h - d.f.line * fs, 2), tc[1]);
    }
    if (content != NULL) {
        *content = ct;
    }
    return MOY_UI_OK;
}

int moy_ui_focus_ring(const moy_ui_sink_t *s, moy_ui_box_t b, int fs) {
    draw_t d;
    open_draw(s, &d);
    int16_t c[3];
    moy_ui_colors(d.look, MOY_UI_K_FOCUS, MOY_UI_REST, c);
    int n = metric(MOY_UI_K_FOCUS, MOY_UI_M_FOCUS_RINGS);
    if (n < fs) {
        n = fs;
    }
    int gap = metric(MOY_UI_K_FOCUS, MOY_UI_M_FOCUS_GAP);
    for (int i = 0; i < n; i++) {
        int dd = fs + gap + i;
        rectb(&d, b.x - dd, b.y - dd, b.w + 2 * dd, b.h + 2 * dd, c[2]);
    }
    return MOY_UI_OK;
}

int moy_ui_paint(const moy_ui_sink_t *s, moy_ui_box_t b, int part, int state, int *painted) {
    if (part < 0 || part >= MOY_THEME_PARTS || state < 0 || state >= MOY_THEME_STATES) {
        return MOY_UI_BAD;
    }
    draw_t d;
    open_draw(s, &d);
    int16_t c[3] = {-1, -1, -1};
    if (part < MOY_UI_KINDS) {
        moy_ui_colors(d.look, part, state < MOY_UI_STATES ? state : MOY_UI_REST, c);
    }
    *painted = surface(&d, b, part, state, c[0], c[2]);
    return MOY_UI_OK;
}

// -- window frames ------------------------------------------------------------------------
//
// Classic is D1's frame (docs/theming_2026-09.md §1): a one-pixel ring and
// the strip's bottom line in shade_dark, a hard black shadow, the strip with
// the app's identity stripe at its left and the title beside it, and a square
// close. The other styles draw in the same footprint (moy_ui.h).

#define FRAME_BLACK 0               // MOY64 black: classic's shadow
#define FRAME_RED 8                 // MOY64 red: tab's close glyph

// The identity stripe's colours, the PICO-8 accents: a title picks one by a
// hash of its bytes, so a window keeps its colour across frames and boots.
static const uint8_t k_stripe[6] = {12, 11, 9, 14, 10, 8};

static int stripe_of(moy_ui_span_t t) {
    uint32_t h = 2166136261u;
    for (uint16_t i = 0; i < t.n; i++) {
        h = (h ^ t.p[i]) * 16777619u;
    }
    return k_stripe[h % 6u];
}

// The stripe's width at font scale fs: classic only.
static int stripe_w(int style, int fs) {
    return style == MOY_THEME_FRAME_CLASSIC ? 3 * fs : 0;
}

// Each style's own title alignment and button order (style.json's
// theme.frame_buttons letters: C close, M minimise, Z maximise, T the title).
static const uint8_t k_frame_align[MOY_THEME_FRAMES] = {
    MOY_THEME_ALIGN_LEFT, MOY_THEME_ALIGN_LEFT, MOY_THEME_ALIGN_CENTER, MOY_THEME_ALIGN_LEFT,
    MOY_THEME_ALIGN_LEFT,
};
static const char *const k_frame_order[MOY_THEME_FRAMES] = {"TMZC", "CTMZ", "CTZM", "TMZC",
                                                            "TMZC"};

typedef struct {
    int style, align, fs, th;
    int n;
    moy_ui_frame_button_t b[3];
    int maxc;                       // the title's room, in cells of its font
    int tn, tw;                     // the title's bytes drawn, and their width
    int tx, ty;
    int16_t tab_w;                  // tab: the tab's width from the ring
} frame_lay_t;

static int fb_id(uint8_t letter) {
    return letter == 'C' ? MOY_UI_FB_CLOSE : letter == 'Z' ? MOY_UI_FB_MAX : MOY_UI_FB_MIN;
}

// A button box's size and the gap between two, per style.
static void frame_box(int style, int fs, int th, int *bw, int *bh, int *gap) {
    if (style == MOY_THEME_FRAME_PLATINUM) {
        *bw = *bh = 11 * fs;
        *gap = 4 * fs;
    } else if (style == MOY_THEME_FRAME_WIN9X) {
        *bw = 16 * fs;
        *bh = 14 * fs;
        *gap = 2 * fs;
    } else {
        *bw = *bh = 16 * fs;
        *gap = 2 * fs;
    }
    (void)th;
}

static void frame_layout(const moy_ui_shape_t *sh, moy_ui_box_t w, int th, moy_ui_span_t title,
                         int has_min, int fs, frame_lay_t *L) {
    int style = sh != NULL && sh->frame[0] ? sh->frame[0] - 1 : MOY_THEME_FRAME_CLASSIC;
    L->style = style;
    L->align = sh != NULL && sh->frame[1] ? sh->frame[1] - 1 : k_frame_align[style];
    L->fs = fs;
    L->th = th;
    const char *order = sh != NULL && sh->frame[2] ? (const char *)&sh->frame[2]
                                                   : k_frame_order[style];
    int bw, bh, gap;
    frame_box(style, fs, th, &bw, &bh, &gap);
    int margin = style == MOY_THEME_FRAME_PLATINUM ? 1 + 6 * fs
                 : style == MOY_THEME_FRAME_WIN9X  ? 4
                                                   : 2;
    int by = style == MOY_THEME_FRAME_WIN9X ? w.y + 2 + moy_ui_fdiv(th - 2 - bh, 2)
                                            : w.y + 1 + moy_ui_fdiv(th - bh, 2);
    // the letters each side of the title
    uint8_t left[3], right[3];
    int nl = 0, nr = 0, side = 0;
    for (int i = 0; i < 4; i++) {
        uint8_t c = (uint8_t)order[i];
        if (c == 'T') {
            side = 1;
        } else if (c != 'M' || has_min) {
            if (side) {
                right[nr++] = c;
            } else {
                left[nl++] = c;
            }
        }
    }
    int lw = nl ? nl * bw + (nl - 1) * gap : 0;
    int rw = nr ? nr * bw + (nr - 1) * gap : 0;
    tfont_t f = role_font(MOY_UI_FONT_TITLE);
    int cell = f.w * fs;
    int x0 = (nl ? w.x + margin + lw : w.x + stripe_w(style, fs)) + 4 * fs;
    int rx;                          // the right group's left edge
    if (style == MOY_THEME_FRAME_TAB) {
        int room = w.x + w.w - margin - (nr ? rw + 4 * fs : 0) - x0;
        L->maxc = room > 0 ? moy_ui_fdiv(room, cell) : 0;
        L->tn = t_fit(&f, title, room, fs);
        L->tw = t_w(&f, title.p, (size_t)L->tn, fs);
        rx = x0 + L->tw + 4 * fs;
        int end = nr ? rx + rw + margin : rx;
        L->tab_w = (int16_t)(end - (w.x + 1));
        L->tx = x0;
    } else {
        rx = w.x + w.w - margin - rw;
        int limit = nr ? rx : w.x + w.w;
        L->maxc = limit - x0 > 0 ? moy_ui_fdiv(limit - x0, cell) : 0;
        L->tn = t_fit(&f, title, limit - x0, fs);
        L->tw = t_w(&f, title.p, (size_t)L->tn, fs);
        int tw = L->tw;
        int tx = x0;
        if (L->align == MOY_THEME_ALIGN_CENTER) {
            tx = w.x + moy_ui_fdiv(w.w - tw, 2);
        } else if (L->align == MOY_THEME_ALIGN_RIGHT) {
            tx = limit - 4 * fs - tw;
        }
        tx = tx > limit - tw ? limit - tw : tx;
        L->tx = tx < x0 ? x0 : tx;
        L->tab_w = 0;
    }
    L->ty = w.y + 1 + moy_ui_fdiv(th - f.line * fs, 2);
    L->n = 0;
    int x = w.x + margin;
    for (int i = 0; i < nl; i++, x += bw + gap) {
        L->b[L->n++] = (moy_ui_frame_button_t){
            {(int16_t)x, (int16_t)by, (int16_t)bw, (int16_t)bh}, (uint8_t)fb_id(left[i]), 0};
    }
    x = rx;
    for (int i = 0; i < nr; i++, x += bw + gap) {
        L->b[L->n++] = (moy_ui_frame_button_t){
            {(int16_t)x, (int16_t)by, (int16_t)bw, (int16_t)bh}, (uint8_t)fb_id(right[i]), 1};
    }
}

int moy_ui_frame_buttons(moy_ui_box_t win, int title_h, moy_ui_span_t title, int has_min, int fs,
                         moy_ui_frame_button_t out[3]) {
    frame_lay_t L;
    frame_layout(moy_ui_shape(), win, title_h, title, has_min, fs, &L);
    for (int i = 0; i < L.n; i++) {
        out[i] = L.b[i];
    }
    return L.n;
}

static const char *const k_fb_glyph[3] = {"close", "app", "minus"};

static int fb_glyph(int id) {
    return moy_ui_glyph_id(k_fb_glyph[id], strlen(k_fb_glyph[id]));
}

// A style's own surface for a part, painted unless the theme names one.
static void own_surface(draw_t *d, moy_ui_box_t b, int part, int state,
                        const moy_ui_surface_t *own) {
    if (!surface(d, b, part, state, -1, -1)) {
        d->row->st[MOY_ST_N_FILL] += moy_ui_paint_surface(&d->t, d->row->pal, d->look, b, own,
                                                          -1, -1, 0);
    }
}

static moy_ui_surface_t surf_of(int edge, int fill, int a, int b, int border) {
    moy_ui_surface_t sf = {(uint8_t)edge, (uint8_t)fill, (uint8_t)(border >= 0),
                           (int8_t)MOY_UI_TERM_TOKEN(a), (int8_t)MOY_UI_TERM_TOKEN(b),
                           (int8_t)(border >= 0 ? MOY_UI_TERM_TOKEN(border) : 0)};
    return sf;
}

int moy_ui_frame(const moy_ui_sink_t *s, moy_ui_box_t w, int th, moy_ui_span_t title, int active,
                 int has_min, int fs) {
    draw_t d;
    open_draw(s, &d);
    frame_lay_t L;
    frame_layout(d.sh, w, th, title, has_min, fs, &L);
    int st = active ? MOY_THEME_S_ACTIVE : MOY_THEME_S_INACTIVE;
    int look = d.look;
    int edge = L.style == MOY_THEME_FRAME_CLASSIC
                   ? moy_ui_token(look, MOY_UI_T_SHADE_DARK)
                   : moy_ui_token(look, active ? MOY_UI_T_CHROME_INK : MOY_UI_T_DIM);
    int t_bg = active ? MOY_UI_T_TITLE_ACTIVE : MOY_UI_T_TITLE_INACTIVE;
    int bg = moy_ui_token(look, t_bg);
    int fg = moy_ui_token(look, active ? MOY_UI_T_TITLE_INK : MOY_UI_T_CHROME_INK_DIM);
    int dark = L.style == MOY_THEME_FRAME_CLASSIC ? FRAME_BLACK
                                                  : moy_ui_token(look, MOY_UI_T_SHADE_DARK);
    moy_ui_box_t strip = {(int16_t)(w.x + 1), (int16_t)(w.y + 1), (int16_t)(w.w - 2), (int16_t)th};
    fill(&d, w.x + 3, w.y + w.h, w.w, 3, dark);
    fill(&d, w.x + w.w, w.y + 3, 3, w.h, dark);
    if (L.style == MOY_THEME_FRAME_WIN9X) {
        int hi = moy_ui_token(look, MOY_UI_T_SHADE_HI);
        int panel = moy_ui_token(look, MOY_UI_T_PANEL);
        fill(&d, w.x, w.y, w.w - 1, 1, hi);
        fill(&d, w.x, w.y, 1, w.h - 1, hi);
        fill(&d, w.x, w.y + w.h - 1, w.w, 1, dark);
        fill(&d, w.x + w.w - 1, w.y, 1, w.h, dark);
        fill(&d, strip.x, strip.y, strip.w, strip.h, panel);
        moy_ui_box_t bar = {(int16_t)(w.x + 2), (int16_t)(w.y + 2), (int16_t)(w.w - 4),
                            (int16_t)(th - 2)};
        if (!surface(&d, bar, MOY_THEME_P_WINDOW_TITLE, st, bg, -1)) {
            fill(&d, bar.x, bar.y, bar.w, bar.h, bg);
        }
    } else {
        rectb(&d, w.x, w.y, w.w, w.h, edge);
        if (L.style == MOY_THEME_FRAME_TAB) {
            fill(&d, strip.x, strip.y, strip.w, strip.h, moy_ui_token(look, MOY_UI_T_PANEL));
            moy_ui_box_t tab = {strip.x, strip.y, L.tab_w, strip.h};
            moy_ui_surface_t own = surf_of(MOY_UI_EDGE_RAISED, MOY_UI_FILL_SOLID, t_bg, t_bg, -1);
            own_surface(&d, tab, MOY_THEME_P_WINDOW_TITLE, st, &own);
        } else if (!surface(&d, strip, MOY_THEME_P_WINDOW_TITLE, st, bg, edge)) {
            fill(&d, strip.x, strip.y, strip.w, strip.h, bg);
        }
        if (L.style == MOY_THEME_FRAME_PLATINUM && active) {
            // the pinstripes either side of the title, cleared round it and the boxes
            moy_ui_box_t pin = {(int16_t)(w.x + 1 + 2 * fs), (int16_t)(w.y + 1 + 3 * fs),
                                (int16_t)(w.w - 2 - 4 * fs), (int16_t)(th - 6 * fs)};
            moy_ui_surface_t ps =
                surf_of(MOY_UI_EDGE_FLAT, MOY_UI_FILL_PINSTRIPE, MOY_UI_T_SHADE_LO, t_bg, -1);
            d.row->st[MOY_ST_N_FILL] +=
                moy_ui_paint_surface(&d.t, d.row->pal, look, pin, &ps, -1, -1, 0);
            if (L.tn > 0) {
                fill(&d, L.tx - 4 * fs, pin.y, L.tw + 2 * 4 * fs, pin.h, bg);
            }
            for (int i = 0; i < L.n; i++) {
                moy_ui_box_t b = L.b[i].box;
                fill(&d, b.x - 2 * fs, pin.y, b.w + 4 * fs, pin.h, bg);
            }
        }
        if (L.style != MOY_THEME_FRAME_FLAT) {
            fill(&d, strip.x, w.y + th, strip.w, 1, edge);
        }
        if (L.style == MOY_THEME_FRAME_CLASSIC) {
            fill(&d, strip.x, strip.y, stripe_w(L.style, fs), th - 1, stripe_of(title));
        }
    }
    if (L.maxc > 0) {
        use_part(&d, MOY_THEME_P_WINDOW_TITLE, st, fs);
        use_font(&d, MOY_UI_FONT_TITLE);
        text(&d, title.p, (size_t)L.tn, L.tx, L.ty, fg);
        d.fx = 0;
    }
    for (int i = 0; i < L.n; i++) {
        moy_ui_box_t b = L.b[i].box;
        int id = L.b[i].id;
        int ink = fg;
        if (L.style == MOY_THEME_FRAME_WIN9X) {
            moy_ui_surface_t own =
                surf_of(MOY_UI_EDGE_BEVEL2, MOY_UI_FILL_SOLID, MOY_UI_T_PANEL, MOY_UI_T_PANEL, -1);
            own_surface(&d, b, MOY_THEME_P_WINDOW_BUTTON, st, &own);
            ink = moy_ui_token(look, MOY_UI_T_CHROME_INK);
        } else if (L.style == MOY_THEME_FRAME_PLATINUM) {
            moy_ui_surface_t own =
                surf_of(active ? MOY_UI_EDGE_RAISED : MOY_UI_EDGE_FLAT, MOY_UI_FILL_SOLID, t_bg,
                        t_bg, active ? MOY_UI_T_CHROME_INK : MOY_UI_T_DIM);
            own_surface(&d, b, MOY_THEME_P_WINDOW_BUTTON, st, &own);
            int m = 3 * fs;
            if (id == MOY_UI_FB_MAX) {
                rectb(&d, b.x + m, b.y + m, b.w - 2 * m - fs, b.h - 2 * m - fs, edge);
            } else if (id == MOY_UI_FB_MIN) {
                fill(&d, b.x + m, b.y + m + fs, b.w - 2 * m, fs, edge);
                fill(&d, b.x + m, b.y + b.h - m - 2 * fs, b.w - 2 * m, fs, edge);
            }
            continue;
        } else {
            surface(&d, b, MOY_THEME_P_WINDOW_BUTTON, st, bg, edge);
            if (id == MOY_UI_FB_CLOSE && L.style == MOY_THEME_FRAME_CLASSIC) {
                int m = 2 * fs;
                rectb(&d, b.x + m, b.y + m, b.w - 2 * m, b.h - 2 * m, fg);
            } else if (id == MOY_UI_FB_CLOSE && L.style != MOY_THEME_FRAME_FLAT) {
                ink = FRAME_RED;
            }
        }
        glyph(&d, fb_glyph(id), b.x, b.y, b.w, b.h, ink, d.tfs);
    }
    return MOY_UI_OK;
}

// A one-pixel row, left out where the clip leaves it out: a clock cell's
// repaint is the band under a clip that stops above its bottom line.
static void line_row(draw_t *d, int x, int y, int w, int ci) {
    int row = y - d->t.cam_y;
    if (row >= d->t.cy0 && row < d->t.cy1) {
        fill(d, x, y, w, 1, ci);
    }
}

int moy_ui_frame_band(const moy_ui_sink_t *s, moy_ui_box_t b, int light, int side) {
    draw_t d;
    open_draw(s, &d);
    int look = d.look;
    int bg = moy_ui_token(look, light ? MOY_UI_T_SURFACE : MOY_UI_T_BAR);
    int line = moy_ui_token(look, light ? MOY_UI_T_BORDER : MOY_UI_T_BAR_EDGE);
    if (surface(&d, b, MOY_THEME_P_BAR, MOY_UI_REST, bg, line)) {
        return MOY_UI_OK;
    }
    const moy_ui_shape_t *sh = d.sh;
    int style = sh != NULL && sh->frame[0] ? sh->frame[0] - 1 : MOY_THEME_FRAME_CLASSIC;
    fill(&d, b.x, b.y, b.w, b.h, bg);
    int across = side == MOY_UI_BAND_LEFT || side == MOY_UI_BAND_RIGHT;
    if (style == MOY_THEME_FRAME_WIN9X) {
        int hi = moy_ui_token(look, MOY_UI_T_SHADE_HI);
        int dark = moy_ui_token(look, MOY_UI_T_SHADE_DARK);
        if (across) {
            fill(&d, b.x, b.y, 1, b.h, hi);
            fill(&d, b.x + b.w - 1, b.y, 1, b.h, dark);
        } else {
            line_row(&d, b.x, b.y, b.w, hi);
            line_row(&d, b.x, b.y + b.h - 1, b.w, dark);
        }
    } else if (side == MOY_UI_BAND_TOP) {
        line_row(&d, b.x, b.y, b.w, line);
    } else if (side == MOY_UI_BAND_LEFT) {
        fill(&d, b.x, b.y, 1, b.h, line);
    } else if (side == MOY_UI_BAND_RIGHT) {
        fill(&d, b.x + b.w - 1, b.y, 1, b.h, line);
    } else {
        line_row(&d, b.x, b.y + b.h - 1, b.w, line);
    }
    return MOY_UI_OK;
}

// -- rect algebra (pure: every rect is x, y, w, h) -------------------------------------

void moy_ui_inset32(const int32_t r[4], int32_t dx, int32_t dy, int32_t out[4]) {
    int32_t w = r[2] - 2 * dx, h = r[3] - 2 * dy;
    out[0] = r[0] + dx;
    out[1] = r[1] + dy;
    out[2] = w > 0 ? w : 0;
    out[3] = h > 0 ? h : 0;
}

void moy_ui_cut(const int32_t r[4], int side, int32_t n, int32_t band[4], int32_t rest[4]) {
    int32_t x = r[0], y = r[1], w = r[2], h = r[3];
    int32_t lim = side < 2 ? h : w;
    if (n > lim) {
        n = lim;
    }
    if (n < 0) {
        n = 0;
    }
    int32_t b[4], o[4];
    switch (side) {
        case MOY_UI_CUT_TOP:
            b[0] = x; b[1] = y; b[2] = w; b[3] = n;
            o[0] = x; o[1] = y + n; o[2] = w; o[3] = h - n;
            break;
        case MOY_UI_CUT_BOTTOM:
            b[0] = x; b[1] = y + h - n; b[2] = w; b[3] = n;
            o[0] = x; o[1] = y; o[2] = w; o[3] = h - n;
            break;
        case MOY_UI_CUT_LEFT:
            b[0] = x; b[1] = y; b[2] = n; b[3] = h;
            o[0] = x + n; o[1] = y; o[2] = w - n; o[3] = h;
            break;
        default:
            b[0] = x + w - n; b[1] = y; b[2] = n; b[3] = h;
            o[0] = x; o[1] = y; o[2] = w - n; o[3] = h;
            break;
    }
    memcpy(band, b, sizeof(b));
    memcpy(rest, o, sizeof(o));
}

int moy_ui_split(const int32_t r[4], int n, int32_t gap, int horizontal, int32_t (*out)[4],
                 int cap) {
    if (n <= 1) {
        if (cap > 0) {
            memcpy(out[0], r, 4 * sizeof(int32_t));
        }
        return 1;
    }
    int32_t x = r[0], y = r[1], w = r[2], h = r[3];
    int32_t span = horizontal ? w : h;
    int32_t c = moy_ui_fdiv((int)(span - (n - 1) * gap), n);
    int32_t at = horizontal ? x : y;
    for (int i = 0; i < n && i < cap; i++) {
        int32_t len = i < n - 1 ? c : (horizontal ? x + w : y + h) - at;
        if (horizontal) {
            out[i][0] = at; out[i][1] = y; out[i][2] = len; out[i][3] = h;
        } else {
            out[i][0] = x; out[i][1] = at; out[i][2] = w; out[i][3] = len;
        }
        at += c + gap;
    }
    return n;
}

void moy_ui_row_content(const int32_t r[4], int fs, int32_t pad, int32_t pad_right, int icon,
                        int glyph_, int32_t glyph_size, int32_t out[4]) {
    if (pad == MOY_UI_DEFAULT) {
        pad = metric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    }
    if (pad_right == MOY_UI_DEFAULT) {
        pad_right = pad;
    }
    int32_t tx = r[0] + pad;
    if (icon) {
        tx += metric(MOY_UI_K_ROW, MOY_UI_M_ROW_ICON) * fs + pad;
    } else if (glyph_) {
        tx += (glyph_size == MOY_UI_DEFAULT ? metric(MOY_UI_K_ROW, MOY_UI_M_ROW_GLYPH)
                                            : glyph_size) * fs + pad;
    }
    int32_t w = r[0] + r[2] - pad_right - tx;
    out[0] = tx;
    out[1] = r[1];
    out[2] = w > 0 ? w : 0;
    out[3] = r[3];
}

void moy_ui_cell_art(const int32_t r[4], int fs, int32_t pad, int32_t caption_h,
                     int32_t out[4]) {
    if (pad == MOY_UI_DEFAULT) {
        pad = metric(MOY_UI_K_CELL, MOY_UI_M_CELL_PAD) * fs;
    }
    int32_t w = r[2] - 2 * pad, h = r[3] - pad - caption_h;
    out[0] = r[0] + pad;
    out[1] = r[1] + pad;
    out[2] = w > 0 ? w : 0;
    out[3] = h > 0 ? h : 0;
}
