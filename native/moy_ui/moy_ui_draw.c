// The toolkit's primitives (moy_ui.h): each is the Python body it replaced
// (tests/ui_twin.py), op for op -- the same fills, edges, glyphs and texts in
// the same order, with every `//` a floor division and every truncation at a
// codepoint. They draw through a sink, never a canvas object.

#include <string.h>

#include "moy_ui.h"
#include "moy_canvas.h"

// -- the sink --------------------------------------------------------------------

typedef struct {
    moy_canvas_row_t *row;
    moy_text_target_t t;
    int tfs;                    // the scale print draws at: the row's font scale
    int look;
} draw_t;

int moy_ui_sink_row(moy_ui_sink_t *s, uint32_t canvas, int look) {
    moy_canvas_row_t *row;
    if (moy_canvas_get(canvas, &row) != 0 || row->px == NULL) {
        return MOY_UI_STALE;
    }
    if (look < 0 || look >= MOY_UI_LOOKS) {
        return MOY_UI_BAD;
    }
    s->kind = MOY_UI_SINK_ROW;
    s->fs = (uint8_t)(row->st[MOY_ST_FONT_SCALE] > 0 ? row->st[MOY_ST_FONT_SCALE] : 1);
    s->look = (uint8_t)look;
    s->dst = row;
    return MOY_UI_OK;
}

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
    d->look = s->look;
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

static void text(draw_t *d, const uint8_t *s, size_t n, int x, int y, int ci) {
    moy_text(&d->t, MOY_FONT_PETME128, MOY_TEXT_PY, s, n, x, y, d->row->pal[ci & 63],
             d->tfs, MOY_TEXT_LEFT, -1);
    d->row->st[MOY_ST_N_TEXT]++;
}

static void glyph(draw_t *d, int g, int x, int y, int w, int h, int ci, int scale) {
    d->row->st[MOY_ST_N_FILL] += moy_ui_glyph_draw(&d->t, g, x, y, w, h,
                                                   d->row->pal[ci & 63], scale);
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
    colours(&d, kind, moy_ui_state_of(flags, interact), r->colors, c);
    int field = c[0], ink = c[1], edge = c[2];
    if (phase & MOY_UI_PHASE_FRAME) {
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
    int pad = dflt(r->pad, metric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs);
    int fw = 8 * fs * r->scale;
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
    int ty = y + (r->text_dy == MOY_UI_DEFAULT ? moy_ui_fdiv(h - fw, 2) : r->text_dy);
    if (r->value.p != NULL) {
        int maxv = moy_ui_fdiv(right - tx, fw);
        size_t vn = moy_text_fit(r->value.p, r->value.n, maxv > 0 ? (size_t)maxv : 0);
        if (vn) {
            text(&d, r->value.p, vn, right - (int)vn * fw, ty,
                 r->value_ink == MOY_UI_FROM_STYLE ? ink : r->value_ink);
            right -= ((int)vn + metric(MOY_UI_K_ROW, MOY_UI_M_ROW_GAP)) * fw;
        }
    }
    if (r->label.n) {
        int maxc = moy_ui_fdiv(right - tx, fw);
        size_t ln = moy_text_fit(r->label.p, r->label.n, maxc > 0 ? (size_t)maxc : 0);
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
        if (field >= 0) {
            fill(&d, x, y, w, h, field);
        }
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
            int maxc = moy_ui_fdiv(w - 2 * pad, 8 * fs);
            size_t ln = moy_text_fit(k->label.p, k->label.n, maxc > 0 ? (size_t)maxc : 0);
            if (ln) {
                int lw = (int)ln * 8 * fs;
                int lx;
                if (k->text_dx != MOY_UI_DEFAULT) {
                    lx = x + k->text_dx;
                } else if (flags & MOY_UI_F_BAND) {
                    lx = x + pad + metric(MOY_UI_K_CELL, MOY_UI_M_CELL_INDENT) * fs;
                } else {
                    lx = x + moy_ui_fdiv(w - lw, 2);
                }
                int ly = k->text_dy != MOY_UI_DEFAULT ? y + k->text_dy
                                                      : cap_y + moy_ui_fdiv(cap - 8 * fs, 2);
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
    int adv = 8 * d.tfs;
    int maxc = moy_ui_fdiv(b.w, adv);
    size_t n = moy_text_fit(t.p, t.n, maxc > 0 ? (size_t)maxc : 0);
    int w = (int)n * adv;
    int x = b.x;
    if (align == MOY_TEXT_CENTER) {
        x += moy_ui_fdiv(b.w - w, 2);
    } else if (align == MOY_TEXT_RIGHT) {
        x += b.w - w;
    }
    if (n) {
        text(&d, t.p, n, x, b.y + moy_ui_fdiv(b.h - adv, 2), ink);
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
