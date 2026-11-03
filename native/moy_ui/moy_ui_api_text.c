// The public UI API's text view (ui.text_view): its lines, wrapped rows and
// marks drawn, and the queries an app asks of a view (moy_ui_api.h).

#include "moy_ui_api_int.h"

// -- ui.text_view ------------------------------------------------------------------------

static int16_t s_tones[6] = {-1, -1, -1, -1, -1, -1};

void moy_ui_text_tones(const int16_t tones[6]) {
    memcpy(s_tones, tones, sizeof(s_tones));
}

static moy_ui_trow_t *s_trows;
static uint32_t *s_starts;

// Line i's rows at `cols` columns: its breaks into s_starts (the first
// TEXT_WRAP_ROWS of them), the count answered.
static int text_line_rows(const moy_ui_lines_t *L, int32_t i, int wrap, int cols, int sgr,
                          const uint8_t **p, size_t *n) {
    *p = L->get(L->src, i, n);
    if (*p == NULL) {
        *n = 0;
        *p = (const uint8_t *)"";
    }
    if (!wrap) {
        s_starts[0] = 0;
        return 1;
    }
    return moy_ui_text_wrap(*p, *n, cols, sgr, s_starts, TEXT_WRAP_ROWS);
}

static int tone_of(int look, int tt) {
    static const uint8_t token[MOY_UI_TT_N] = {
        MOY_UI_T_INK, MOY_UI_T_VALUE, MOY_UI_T_INK, MOY_UI_T_INK, MOY_UI_T_INK_DIM,
        MOY_UI_T_INK_ACCENT, MOY_UI_T_VALUE, MOY_UI_T_DANGER, MOY_UI_T_INK_DIM,
    };
    if (tt < 6 && s_tones[tt] >= 0) {
        return s_tones[tt];
    }
    return moy_ui_token(look, token[tt]);
}

// The visual row line `line` starts on, where each line wraps at `cols`.
static int32_t text_row_of(const moy_ui_lines_t *L, int32_t line, int cols, int sgr) {
    int32_t row = 0;
    for (int32_t i = 0; i < line && i < L->n; i++) {
        const uint8_t *p;
        size_t n;
        row += text_line_rows(L, i, 1, cols, sgr, &p, &n);
    }
    return row;
}

// The view's rows in box b through the pieces the frame draws. The view
// grounds all of b itself, so a floor rect inside b takes no page ground
// first; and pieces that stack into one band (the same columns, rows
// touching) clip as one, so a row that crosses from one into the next is
// laid out and drawn once.
static void text_paint(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int32_t *v,
                       int nrows, moy_ui_span_t err, moy_ui_span_t find) {
    rc_t in = mua_rc_of(b);
    uint32_t bit = 1;
    for (int i = 0; i < c->fl_n; i++, bit <<= 1) {
        rc_t f = mua_rc_of(c->fl[i]);
        if (f.x0 >= in.x0 && f.y0 >= in.y0 && f.x1 <= in.x1 && f.y1 <= in.y1) {
            c->fl_pend &= ~bit;
        }
    }
    int32_t *st = ((moy_canvas_row_t *)s->dst)->st;
    rc_t q[4 + MOY_UI_API_FLOOR];
    int nq = 0;
    clip_t k;
    for (int ok = mua_clip_row(c, &k, s, b, 0, b); ok; ok = mua_clip_next(&k)) {
        if (k.c == NULL) {
            // no pieces: the one clip there is
            moy_ui_text_paint(NULL, s, v, s_trows, nrows, err, find, 1);
            continue;
        }
        rc_t p = {st[MOY_ST_CX0], st[MOY_ST_CY0], st[MOY_ST_CX1], st[MOY_ST_CY1]};
        rc_t bb = {in.x0 - st[MOY_ST_CAM_X], in.y0 - st[MOY_ST_CAM_Y], in.x1 - st[MOY_ST_CAM_X],
                   in.y1 - st[MOY_ST_CAM_Y]};
        if (!mua_rc_meets(p, bb)) {
            continue;                   // a piece beside the view (its mark's reach)
        }
        for (int i = 0; i < nq; i++) {
            if (q[i].x0 == p.x0 && q[i].x1 == p.x1 && q[i].y0 <= p.y1 && p.y0 <= q[i].y1) {
                // one band with p: p takes it in and looks again from the
                // first, grown
                p.y0 = p.y0 < q[i].y0 ? p.y0 : q[i].y0;
                p.y1 = p.y1 > q[i].y1 ? p.y1 : q[i].y1;
                q[i] = q[--nq];
                i = -1;
            }
        }
        if (nq < (int)(sizeof(q) / sizeof(q[0]))) {
            q[nq++] = p;
        }
    }
    if (nq == 0) {
        return;
    }
    int32_t saved[4];
    memcpy(saved, &st[MOY_ST_CX0], sizeof(saved));
    for (int i = 0; i < nq; i++) {
        st[MOY_ST_CX0] = q[i].x0;
        st[MOY_ST_CY0] = q[i].y0;
        st[MOY_ST_CX1] = q[i].x1;
        st[MOY_ST_CY1] = q[i].y1;
        moy_ui_text_paint(NULL, s, v, s_trows, nrows, err, find, 1);
    }
    memcpy(&st[MOY_ST_CX0], saved, sizeof(saved));
}

// A text view's paper (sheet `b`, corners `r`): its bands `mua_band` tall at
// its top and foot, through every piece of the frame they reach.
static void text_paper(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int r,
                       int band) {
    int ci = moy_ui_token(s->look, MOY_UI_T_SURFACE);
    for (int i = 0; i < 2; i++) {
        moy_ui_box_t e = {b.x, (int16_t)(i ? b.y + b.h - band : b.y), b.w, (int16_t)band};
        clip_t k;
        EACH_ROW(c, k, s, e, 1, e) {
            moy_ui_paper(s, b, r, ci);
        }
    }
}

int32_t moy_ui_api_text_view(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                             const moy_ui_lines_t *L, const int32_t *opt,
                             const moy_ui_span_t *marks, const moy_ui_over_t *o,
                             int32_t out[2]) {
    o = mua_over(o);
    out[0] = out[1] = 0;
    s_trows = moy_ui_text_scratch();
    s_starts = moy_ui_text_breaks();
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
    }
    if (id.n == 0 || s_trows == NULL) {
        return 0;
    }
    mua_group_close(c, s);
    int fs = c->fs;
    moy_font_info_t fi;
    // the caller's font where it names a loaded one, outside a cart's code
    uint32_t font = (uint32_t)opt[MOY_UI_TXO_FONT];
    if (font == 0 || moy_ui_cmask() != 127 || !moy_font_info(font, &fi)) {
        font = moy_ui_font(MOY_UI_FONT_MONO, &fi);
    }
    int cell = (fi.w > 0 ? fi.w : 8) * fs, th, lh;
    if (font != 0 && fi.first == 0) {
        th = fi.h * fs;
        lh = th;
    } else {
        th = 8 * fs;
        lh = ((fi.line > 0 ? fi.line : 8) + 2) * fs;
    }
    // its box: ui.at's, or `rows` rows of the page (the rest of it)
    moy_ui_box_t b;
    int used = 0, placed = c->has_at;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    } else {
        int top = c->page.y + c->y - c->scroll, bottom = c->page.y + c->page.h;
        int h = opt[MOY_UI_TXO_ROWS] > 0 ? opt[MOY_UI_TXO_ROWS] * lh : bottom - top;
        b = (moy_ui_box_t){c->page.x, (int16_t)top, c->page.w, (int16_t)(h > 0 ? h : 0)};
        used = b.h;
    }
    if (mua_armed(c) && c->sh_in_id == mua_hash(id.p, id.n, TEXT_SALT) && c->fl_stage != 1) {
        moy_ui_api_shift_break(c);      // planned inside a page this pass did not draw round it
    }
    int pad = TEXT_PAD * fs;
    if (opt[MOY_UI_TXO_PAPER] >= 0) {
        // its paper: the bands above and under the rows drawn through the
        // frame's pieces, so a frame that keeps them draws neither
        int r = opt[MOY_UI_TXO_PAPER], band = r > pad ? r : pad;
        if (2 * band < b.h) {
            text_paper(c, s, b, r, band);
            b.y = (int16_t)(b.y + band);
            b.h = (int16_t)(b.h - 2 * band);
        }
    }
    int wrap = opt[MOY_UI_TXO_WRAP] != 0, syn = opt[MOY_UI_TXO_SYNTAX];
    int sgr = syn == MOY_UI_SYN_SGR;
    int gc = 0;
    if (opt[MOY_UI_TXO_GUTTER]) {
        for (int32_t n = L->n > 0 ? L->n : 1; n > 0; n /= 10) {
            gc++;
        }
        gc++;
    }
    // a cut line runs on to the view's right edge, where it fades; a
    // wrapped one breaks the inset in from it
    int cols = (b.w - (wrap ? 2 : 1) * pad - gc * cell) / (cell > 0 ? cell : 1);
    cols = cols < 1 ? 1 : cols;
    uint32_t rid = mua_hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k0 = mua_keep_of(c, rid);
    // the view's record: a view new to the context starts a fresh one
    moy_ui_tview_t *w = moy_ui_text_view_of(c, rid, 1);
    if (w != NULL && k0 == NULL) {
        memset(&w->gen, 0, sizeof(*w) - offsetof(moy_ui_tview_t, gen));
        moy_ui_text_reset(&w->t);
    }
    // its rows: a line each, or each line's wrapped rows (kept against the
    // caller's gen)
    int32_t gen = opt[MOY_UI_TXO_GEN];
    int32_t total = 0;
    if (!wrap) {
        total = L->n;
    } else if (w != NULL && gen != 0 && w->gen == gen && w->cols == cols && w->n == L->n) {
        total = w->total;
    } else {
        for (int32_t i = 0; i < L->n; i++) {
            const uint8_t *p;
            size_t n;
            total += text_line_rows(L, i, 1, cols, sgr, &p, &n);
        }
        if (w != NULL) {
            w->gen = gen;
            w->cols = cols;
            w->n = L->n;
            w->total = total;
        }
    }
    if (w != NULL) {
        w->wrap = wrap;                 // a wrapped view's rows are its pass's to aim
        w->at_cols = cols;              // ...and moy_ui_api_text_reveal's to find
        w->sgr = sgr;
    }
    int32_t content = total * lh;
    content = content > 32767 ? 32767 : content;
    int32_t was_b = k0 != NULL ? k0->b : 0;
    int was_foot = k0 != NULL && was_b > 0 && k0->a >= was_b - b.h;
    // its rows are its own gens (the view's record): the region carries a
    // gen whatever the app gives, so its frames may move and draw rows
    moy_ui_over_t og = *o;
    if (!og.has_gen) {
        og.has_gen = 1;
        og.gen = 0;
    }
    mua_nest_open(c, 1, rid, b, used, (o->nav == 0 ? STOP_NAV_OFF : 0) | (placed ? STOP_AT : 0), &og);
    moy_ui_keep_t *k = mua_page_keep(c);
    int32_t scroll = c->scroll;
    if (k != NULL) {
        k->rh = (int16_t)lh;            // the wheel's step is its lines
        int32_t most = content - b.h;
        most = most < 0 ? 0 : most;
        int32_t aim_to = -1;
        if (opt[MOY_UI_TXO_TOP] >= 0) {
            int32_t row = wrap ? text_row_of(L, opt[MOY_UI_TXO_TOP], cols, sgr)
                               : opt[MOY_UI_TXO_TOP];
            aim_to = row * lh;
            aim_to = aim_to > most ? most : aim_to;
        } else if (opt[MOY_UI_TXO_FOLLOW] && (k0 == NULL || (was_foot && content > was_b))
                   && scroll < most) {
            aim_to = most;              // a view that follows its foot starts there
        }
        if (aim_to >= 0 && aim_to != scroll && !(k->sc.flags & MOY_UI_SCROLL_DRAG)) {
            if (k0 == NULL || c->in[MOY_UI_IN_DT] <= 0) {
                // a view's first frame, or a frame with no time: it lands
                k->a = aim_to;
                moy_ui_scroll_land(&k->sc, &k->a);
                c->scroll = scroll = aim_to;
            } else {
                // past its frame's start the pixels have moved for it: it
                // glides there from the next frame (a host that lands it
                // aims before its frame, moy_ui_api_text_aim)
                moy_ui_scroll_glide_to(&k->sc, scroll, aim_to, 0, content, b.h);
            }
        }
    }
    // the rows R shows
    int32_t v[MOY_UI_TV_LEN];
    memset(v, 0, sizeof(v));
    v[MOY_UI_TV_X] = b.x;
    v[MOY_UI_TV_Y] = b.y;
    v[MOY_UI_TV_W] = b.w;
    v[MOY_UI_TV_H] = b.h;
    v[MOY_UI_TV_LH] = lh;
    v[MOY_UI_TV_CELL] = cell;
    v[MOY_UI_TV_TEXT_H] = th;
    v[MOY_UI_TV_FS] = fs;
    v[MOY_UI_TV_FONT] = (int32_t)font;
    v[MOY_UI_TV_TX] = b.x + pad;
    v[MOY_UI_TV_MARK] = opt[MOY_UI_TXO_MARK] ? b.x + TEXT_MARK * fs : -1;
    v[MOY_UI_TV_GUTTER] = gc;
    v[MOY_UI_TV_COLS] = cols;
    v[MOY_UI_TV_TOP] = scroll / lh;
    v[MOY_UI_TV_OFF] = scroll % lh;
    v[MOY_UI_TV_LEFT] = wrap || opt[MOY_UI_TXO_LEFT] < 0 ? 0 : opt[MOY_UI_TXO_LEFT];
    v[MOY_UI_TV_CARET_ROW] = opt[MOY_UI_TXO_CARET_LINE];
    v[MOY_UI_TV_CARET_COL] = opt[MOY_UI_TXO_CARET_COL];
    v[MOY_UI_TV_SEL_R0] = opt[MOY_UI_TXO_SEL_R0];
    v[MOY_UI_TV_SEL_C0] = opt[MOY_UI_TXO_SEL_C0];
    v[MOY_UI_TV_SEL_R1] = opt[MOY_UI_TXO_SEL_R1];
    v[MOY_UI_TV_SEL_C1] = opt[MOY_UI_TXO_SEL_C1];
    v[MOY_UI_TV_ERR_ROW] = opt[MOY_UI_TXO_ERR_LINE];
    v[MOY_UI_TV_FIND_CI] = opt[MOY_UI_TXO_FIND_CI] != 0;
    v[MOY_UI_TV_SYNTAX] = syn;
    v[MOY_UI_TV_FLAGS] = MOY_UI_TVF_GROUND | MOY_UI_TVF_CLIP | (wrap ? 0 : MOY_UI_TVF_FADE);
    v[MOY_UI_TV_LINES] = L->n;
    int look = s->look;
    v[MOY_UI_TV_BG] = moy_ui_token(look, MOY_UI_T_SURFACE);
    v[MOY_UI_TV_CARET] = moy_ui_token(look, MOY_UI_T_INK_ACCENT);
    v[MOY_UI_TV_SEL] = moy_ui_token(look, MOY_UI_T_HILITE);
    v[MOY_UI_TV_GUT] = moy_ui_token(look, MOY_UI_T_INK_DIM);
    v[MOY_UI_TV_GUT_CUR] = moy_ui_token(look, MOY_UI_T_INK);
    v[MOY_UI_TV_VALUE] = moy_ui_token(look, MOY_UI_T_VALUE);
    v[MOY_UI_TV_ERR] = moy_ui_token(look, MOY_UI_T_DANGER);
    for (int i = 0; i < MOY_UI_TT_N; i++) {
        v[MOY_UI_TV_TONE0 + i] = tone_of(look, i);
    }
    v[MOY_UI_TV_KEY] = (int32_t)(c->cls | (c->win << 1));
    if (w != NULL) {
        v[MOY_UI_TV_OVER_Y0] = w->over0;
        v[MOY_UI_TV_OVER_Y1] = w->over1;
        w->over0 = w->over1 = 0;
    }
    moy_ui_span_t none = {(const uint8_t *)"", 0};
    moy_ui_span_t err = marks != NULL && v[MOY_UI_TV_ERR_ROW] >= 0 ? marks[0] : none;
    moy_ui_span_t find = marks != NULL ? marks[1] : none;
    err = err.p == NULL ? none : err;
    find = find.p == NULL ? none : find;
    int nrows = moy_ui_text_rows(&v[0]);
    nrows = nrows < MOY_UI_TEXT_ROWS ? nrows : MOY_UI_TEXT_ROWS;
    int32_t top = v[MOY_UI_TV_TOP];
    if (!wrap) {
        for (int i = 0; i < nrows; i++) {
            moy_ui_trow_t *r = &s_trows[i];
            int32_t ln = top + i;
            r->ca = 0;
            r->a = 0;
            size_t n = 0;
            r->p = ln < L->n ? L->get(L->src, ln, &n) : NULL;
            r->line = ln;
            r->n = r->b = (uint32_t)n;
        }
        out[0] = top;
    } else {
        int32_t row = 0;
        int got = 0;
        out[0] = -1;
        for (int32_t i = 0; i < L->n && got < nrows; i++) {
            const uint8_t *p;
            size_t n;
            int m = text_line_rows(L, i, 1, cols, sgr, &p, &n);
            if (row + m <= top) {
                row += m;
                continue;
            }
            for (int j = 0; j < m && got < nrows; j++, row++) {
                if (row < top) {
                    continue;
                }
                moy_ui_trow_t *r = &s_trows[got++];
                uint32_t a = j < TEXT_WRAP_ROWS ? s_starts[j] : (uint32_t)n;
                uint32_t e = j + 1 < m && j + 1 < TEXT_WRAP_ROWS ? s_starts[j + 1] : (uint32_t)n;
                r->p = p;
                r->n = (uint32_t)n;
                r->a = a;
                r->b = e;
                r->ca = moy_ui_text_cols(p, a, sgr);
                r->line = i;
                if (out[0] < 0) {
                    out[0] = i;
                }
            }
        }
        for (; got < nrows; got++) {
            s_trows[got].p = NULL;
            s_trows[got].line = -1;
        }
        out[0] = out[0] < 0 ? 0 : out[0];
    }
    out[1] = nrows;
    moy_ui_text_t *t = w != NULL ? &w->t : NULL;
    if (mua_armed(c) && c->fl_stage == 1) {
        if (c->sh_in_id == rid) {
            mua_view_in_floor(c, c->sh_r.y + c->sh_r.h);    // its move inside R
        }
        // a floor frame: the rows whose gen moved join the floor
        // the foot's fade, where the rows' height moved under it this frame
        if (!opt[MOY_UI_TXO_MARK] && content != was_b
            && (content > scroll + b.h || was_b > scroll + b.h)) {
            int fh = FADE_H * fs;
            mua_floor_add(c, (rc_t){b.x, b.y + b.h - fh, b.x + b.w, b.y + b.h}, 1);
        }
        int planned = t != NULL && moy_ui_text_plan(t, v, s_trows, nrows, err, find);
        if (!planned || c->sh_view == rid) {
            // no record to say which rows moved, or a move drawn as rows: all of them
            mua_floor_add(c, mua_rc_of(b), 1);
        } else {
            // each run of rows whose gen moved, one band
            for (int i = 0; i < nrows;) {
                if (!((t->floor[i >> 3] >> (i & 7)) & 1)) {
                    i++;
                    continue;
                }
                int j = i + 1;
                while (j < nrows && ((t->floor[j >> 3] >> (j & 7)) & 1)) {
                    j++;
                }
                int y = b.y + i * lh - v[MOY_UI_TV_OFF];
                rc_t q = {b.x, y, b.x + b.w, y + (j - i) * lh};
                if (mua_rc_cut(&q, mua_rc_of(b))) {
                    mua_floor_add(c, q, 1);     // (its rows cut at its box: its paper is R's)
                }
                i = j;
            }
        }
        if (c->fl_lost) {
            moy_ui_api_shift_break(c);  // a floor too broken up to hold: the pass draws whole
        }
    }
    int64_t was = moy_text_print_font_use(font);
    text_paint(c, s, b, v, nrows, err, find);
    if (was >= 0) {
        moy_text_print_font_use((uint32_t)was);
    }
    if (t != NULL) {
        moy_ui_text_note(t, v, s_trows, nrows, err, find);
        // where its rows lay, for a hit test
        w->x = b.x;
        w->y = b.y;
        w->w = b.w;
        w->h = b.h;
        w->lh = lh;
        w->cell = cell;
        w->tx = b.x + pad + gc * cell;
        w->left = v[MOY_UI_TV_LEFT];
        w->off = v[MOY_UI_TV_OFF];
        w->rows = nrows;
        for (int i = 0; i < nrows; i++) {
            const moy_ui_trow_t *r = &s_trows[i];
            w->line[i] = r->line;
            w->ca[i] = r->p != NULL ? r->ca : 0;
            w->cb[i] = r->p == NULL ? 0 : !wrap ? -1
                       : r->ca + moy_ui_text_cols(r->p + r->a, r->b - r->a, sgr);
        }
    }
    // its rows' height, the scroll's range; an editor's view (`mark`) keeps
    // its caret's line in view, and its foot draws no fade over the code
    c->y = (int16_t)content;
    if (k != NULL) {
        k->b = content;
    }
    mua_no_foot = opt[MOY_UI_TXO_MARK] != 0;
    mua_nest_close(c, s, 1);
    mua_no_foot = 0;
    return 0;
}

// -- a text view's queries -----------------------------------------------------------

int moy_ui_api_text_at(const moy_ui_ctx_t *c, moy_ui_span_t id, int px, int py,
                       int32_t out[2]) {
    out[0] = out[1] = 0;
    const moy_ui_tview_t *w = moy_ui_text_view_of(c, mua_hash(id.p, id.n, TEXT_SALT), 0);
    if (w == NULL || w->lh <= 0 || px < w->x || px >= w->x + w->w || py < w->y
        || py >= w->y + w->h) {
        return 0;
    }
    int i = (py - w->y + w->off) / w->lh;
    if (i < 0 || i >= w->rows || w->line[i] < 0) {
        return 0;
    }
    int col = px < w->tx ? 0 : (px - w->tx) / (w->cell > 0 ? w->cell : 1);
    col += w->cb[i] < 0 ? w->left : w->ca[i];
    if (w->cb[i] >= 0 && col > w->cb[i]) {
        col = w->cb[i];
    }
    out[0] = w->line[i];
    out[1] = col;
    return 1;
}

int moy_ui_api_text_aim(moy_ui_ctx_t *c, moy_ui_span_t id, int32_t line, int32_t lines) {
    uint32_t rid = mua_hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k = mua_keep_of(c, rid);
    const moy_ui_tview_t *w = moy_ui_text_view_of(c, rid, 0);
    if (k == NULL || w == NULL || w->wrap || w->lh <= 0 || w->h <= 0
        || (k->sc.flags & MOY_UI_SCROLL_DRAG)) {
        return 0;                       // its pass aims it (a wrapped view's rows are its pass's)
    }
    int32_t content = lines * w->lh;
    content = content > 32767 ? 32767 : content;
    int32_t most = content - w->h;
    most = most < 0 ? 0 : most;
    int32_t aim;
    if (line < 0) {
        // the foot, where the view stood at it and the lines grew
        if (!(k->b > 0 && k->a >= k->b - w->h) || content <= k->b) {
            return 0;
        }
        aim = most;
    } else {
        aim = line * w->lh;
        aim = aim > most ? most : aim;
    }
    if (aim == k->a) {
        return 0;
    }
    k->b = content;
    k->a = aim;
    k->over = 0;
    moy_ui_scroll_land(&k->sc, &k->a);
    return 1;
}

int moy_ui_api_text_reveal(moy_ui_ctx_t *c, moy_ui_span_t id, const moy_ui_lines_t *L,
                           int32_t line, int32_t col) {
    uint32_t rid = mua_hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k = mua_keep_of(c, rid);
    const moy_ui_tview_t *w = moy_ui_text_view_of(c, rid, 0);
    if (k == NULL || w == NULL || w->lh <= 0 || w->h <= 0 || line < 0
        || (k->sc.flags & MOY_UI_SCROLL_DRAG)) {
        return 0;                       // a view new to the context, or held by a finger
    }
    // the visual row (line, col) lies on, and the rows the lines make
    int32_t row = line, total = L->n;
    if (w->wrap) {
        s_starts = moy_ui_text_breaks();
        if (s_starts == NULL) {
            return 0;
        }
        total = 0;
        for (int32_t i = 0; i < L->n; i++) {
            const uint8_t *p;
            size_t n;
            int m = text_line_rows(L, i, 1, w->at_cols, w->sgr, &p, &n);
            if (i == line) {
                row = total;
                // the last of its rows that starts at or before the column
                for (int j = 1; col >= 0 && j < m && j < TEXT_WRAP_ROWS
                                && moy_ui_text_cols(p, s_starts[j], w->sgr) <= col; j++) {
                    row = total + j;
                }
            }
            total += m;
        }
    }
    int32_t content = total * w->lh;
    content = content > 32767 ? 32767 : content;
    int32_t most = content - w->h;
    most = most < 0 ? 0 : most;
    int32_t y0 = row * w->lh, aim = k->a;
    if (col < 0 || y0 < aim) {
        aim = y0;                       // its line's top, or a row above the view
    } else if (y0 + w->lh > aim + w->h) {
        aim = y0 + w->lh - w->h;        // a row under it: as far as it shows
    }
    aim = aim > most ? most : aim;
    aim = aim < 0 ? 0 : aim;
    if (aim == k->a) {
        return 0;
    }
    k->b = content;
    k->a = aim;
    k->over = 0;
    moy_ui_scroll_land(&k->sc, &k->a);
    return 1;
}

void moy_ui_api_text_cover(const moy_ui_ctx_t *c, moy_ui_span_t id, int y0, int y1) {
    moy_ui_tview_t *w = moy_ui_text_view_of(c, mua_hash(id.p, id.n, TEXT_SALT), 0);
    if (w == NULL || y1 <= y0) {
        return;
    }
    if (w->over1 <= w->over0) {
        w->over0 = y0;
        w->over1 = y1;
        return;
    }
    w->over0 = y0 < w->over0 ? y0 : w->over0;
    w->over1 = y1 > w->over1 ? y1 : w->over1;
}
