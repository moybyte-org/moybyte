// The public UI API's popovers, menus, card grids, an empty page, tools and
// the app's bar (moy_ui_api.h).

#include "moy_ui_api_int.h"

// -- popovers -------------------------------------------------------------------------

// The body a popover shows of its rows' `body` in `cap`: all of it, else
// the cut, raised to the peek its keep's `peek` found last frame (a row's
// middle half the fade above the cut, `peek_note`), so a cut body always shows
// the top of a row's words over its fade (a phone sheet's peek); never
// under one row.
static int pop_body(moy_ui_ctx_t *c, const moy_ui_keep_t *kb, int body, int cap, int rh) {
    if (body <= cap) {
        return body;
    }
    c->pop_cut = (int16_t)(cap > 0 ? cap : 1);
    int bh = kb != NULL && kb->peek > 0 && kb->peek < cap ? kb->peek : cap;
    return bh < rh ? rh : bh;
}

int moy_ui_api_popover(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w,
                       const moy_ui_over_t *o) {
    o = mua_over(o);
    if (id.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 3) {
            int used = c->nest[c->n_nest - 1].used;
            moy_ui_keep_t *pk = mua_page_keep(c);
            int took = mua_nest_close(c, s, 3);
            if (took != used) {
                c->owe = 1;             // laid out for another body: the next frame redraws
            }
            // a cut body's rows run on: where the cut leaves no row's
            // middle half the fade above it, the next frame cuts at the
            // deepest row middle plus half the fade above it, so the top
            // of a row's words shows over the fade
            int peek = c->pop_cut > 0 && took > c->pop_cut && !c->pop_inside ? c->pop_peek : 0;
            if (pk != NULL && pk->peek != peek) {
                pk->peek = (int16_t)peek;
                c->owe = 1;
            }
            c->pop_cut = 0;
        }
        return 0;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
    }
    mua_group_close(c, s);
    c->fl_susp++;                       // an overlay: drawn whole, over the page
    c->fl_dirty = 1;
    c->fl_top = 1;
    // the rect it hangs from: ui.at's, else the one it opened under
    if (c->has_at) {
        c->pop_at = c->at;
        c->has_at = 0;
        c->pop_mid = 0;
    } else if (c->pop_prev.w == 0) {
        c->pop_at = c->focus_box;
        c->pop_mid = c->focus_box.w == 0;   // nothing to hang from: centred
    }
    moy_ui_box_t a = c->pop_at;
    int fs = c->fs, rh = mua_row_h(c);
    uint32_t pid = mua_hash(id.p, id.n, POPOVER_SALT);
    moy_ui_keep_t *kb = mua_keep_of(c, pid);
    int body = kb != NULL && kb->b > 0 ? kb->b : rh;
    if (!c->modal) {
        c->owe = 1;                     // its first frame: focus opens in it on the next
    }
    int pad = moy_ui_modal_band(fs, c->cls);
    moy_ui_box_t sf = c->surface;
    if (w <= 0) {
        w = a.w;
    }
    if (w > sf.w - 2 * pad) {
        w = sf.w - 2 * pad;
    }
    if (w < 2 * pad + rh) {
        w = 2 * pad + rh;
    }
    // the card keeps `mua_gap` clear of every edge of the surface and never
    // covers its anchor: below it, else above it, else on the side with
    // more room, its body cut to that room (it scrolls to focus, a fade at
    // the cut edge). An anchor that is a point (a finger's, `h` 0) starts
    // the card there and slides it to fit whole where the surface has the
    // room, as a phone's context menu does
    int gap = 4 * fs;
    int x0 = sf.x + gap, x1 = sf.x + sf.w - gap;
    int y0 = sf.y + gap, y1 = sf.y + sf.h - gap;
    if (w > x1 - x0) {
        w = x1 - x0;
    }
    int bh = body;
    int x = a.x;
    if (x + w > x1) {
        x = x1 - w;
    }
    if (x < x0) {
        x = x0;
    }
    int y;
    c->pop_cut = 0;
    c->pop_peek = 0;
    c->pop_inside = 0;
    if (c->pop_mid) {
        bh = pop_body(c, kb, bh, y1 - y0 - 2 * pad, rh);
        x = sf.x + moy_ui_fdiv(sf.w - w, 2);
        y = sf.y + moy_ui_fdiv(sf.h - (2 * pad + bh), 2);
    } else {
        // the room each side of the anchor, inside the surface's gap (an
        // anchor in a bar beside the surface hangs the card at its edge)
        int top = a.y + a.h + gap > y0 ? a.y + a.h + gap : y0;
        int foot = a.y - gap < y1 ? a.y - gap : y1;
        int below = y1 - top, above = foot - y0;
        int dn = 2 * pad + bh <= below || (2 * pad + bh > above && below >= above);
        int room = dn ? below : above;
        if (a.h == 0 && 2 * pad + bh > room && 2 * pad + bh <= y1 - y0) {
            y = dn ? y1 - (2 * pad + bh) : y0;
        } else {
            bh = pop_body(c, kb, bh, room - 2 * pad, rh);
            y = dn ? top : foot - (2 * pad + bh);
        }
    }
    int h = 2 * pad + bh;
    moy_ui_box_t card = {(int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h};
    static const uint8_t none[1] = {0};
    if (o->wash == 1) {
        mua_wash(c, s);                     // a row's or a card's menu (DESIGN, Menus and washes)
    }
    moy_ui_modal(s, card, (moy_ui_span_t){none, 0}, fs, c->cls, rh);
    int lp = c->cls ? mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * fs
                    : mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    moy_ui_box_t inner = {(int16_t)(card.x + pad - lp), (int16_t)(card.y + pad),
                          (int16_t)(w - 2 * (pad - lp)), (int16_t)bh};
    mua_nest_open(c, 3, pid, inner, body, 0, NULL);
    c->pop_card = card;
    c->pop_id = pid;
    return !c->pop_gone;
}

// -- menus ---------------------------------------------------------------------------

// Field i of a NUL-separated list (empty past its end).
static moy_ui_span_t field_at(const uint8_t *p, size_t len, int i) {
    static const uint8_t none[1] = {0};
    size_t at = 0;
    int k = 0;
    for (size_t j = 0; p != NULL && j <= len; j++) {
        if (j == len || p[j] == 0) {
            if (k == i) {
                return (moy_ui_span_t){p + at, (uint16_t)(j - at)};
            }
            at = j + 1;
            k++;
        }
    }
    return (moy_ui_span_t){none, 0};
}

int32_t moy_ui_api_menu(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, const uint8_t *icons, size_t ilen,
                        const moy_ui_over_t *o) {
    o = mua_over(o);
    mua_group_close(c, s);
    int fs = c->fs, rh = mua_row_h(c);
    // placed: the rows from ui.at's rect's top left, across its width
    int placed = c->has_at;
    moy_ui_box_t at = c->at;
    c->has_at = 0;
    int ay = at.y;
    if (label.n) {
        int h = mua_kmetric(MOY_UI_K_GROUP, c->cls ? MOY_UI_M_GROUP_CAPTION_DESK
                                               : MOY_UI_M_GROUP_CAPTION) * fs;
        int stacked = 0;
        moy_ui_box_t b = {at.x, (int16_t)ay, at.w, (int16_t)h};
        if (placed) {
            ay += h;
        } else {
            b = mua_place(c, h, &stacked);
        }
        // the caption starts where its rows' icons do: at the rows' pad
        // (moy_ui_vrow's), where the caption's own is a group's
        int rpad = (c->grp_open || c->cls) ? mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)
                                           : mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD);
        int dx = (rpad - mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * fs;
        moy_ui_box_t cb = {(int16_t)(b.x + dx), b.y, (int16_t)(b.w - dx), b.h};
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_caption(s, cb, label, fs, c->cls);
            mua_clip_out(&k);
        }
    } else if (!placed && c->y > 0) {
        int stacked;
        moy_ui_box_t b = mua_place_row(c, mua_rule_h(c), &stacked, 0);
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_rule(s, b, fs, c->cls);
            mua_clip_out(&k);
        }
    }
    int n = moy_ui_fields(items, len);
    int32_t pick = -1;
    for (int i = 0; i < n; i++) {
        moy_ui_span_t t = field_at(items, len, i);
        moy_ui_span_t ic = field_at(icons, ilen, i);
        if (ic.n) {
            moy_ui_api_icon(c, ic);
        }
        int stacked = 0;
        moy_ui_box_t b;
        if (placed) {
            c->row_slot = c->icon_slot;
            c->row_glyph = c->icon_glyph;
            c->icon_slot = c->icon_glyph = -1;
            b = (moy_ui_box_t){at.x, (int16_t)ay, at.w, (int16_t)rh};
            ay += rh;
        } else {
            b = mua_place(c, rh, &stacked);
        }
        int st = mua_vrow_stop(c, mua_hash(t.p, t.n, 0), b, 0, o, t);
        if (st & MOY_UI_ST_ACTIVATED) {
            pick = i;
        }
        // an item's text after a tab is the key it answers to, drawn as
        // an info row's value
        moy_ui_span_t key = {t.p, 0};
        for (uint16_t j = 0; j < t.n; j++) {
            if (t.p[j] == '\t') {
                key = (moy_ui_span_t){t.p + j + 1, (uint16_t)(t.n - j - 1)};
                t.n = j;
                break;
            }
        }
        if (moy_ui_key_moves(moy_ui_hint_input(), key)) {
            key.n = 0;
        }
        moy_ui_vrow_t r;
        mua_vrow_init(c, &r, t);
        r.value = key;
        r.keys = 1;
        if (i < 32 && ((c->menu_danger >> i) & 1u)) {
            r.kind = MOY_UI_VROW_DANGER;
        }
        mua_vrow_draw(c, s, b, stacked, MOY_UI_V_INFO, st, o, &r);
    }
    c->menu_danger = 0;
    return pick;
}

void moy_ui_api_menu_danger(moy_ui_ctx_t *c, uint32_t rows) {
    c->menu_danger = rows;
}

void moy_ui_api_text(moy_ui_ctx_t *c, int lines, int centre, int tone, int role) {
    c->txt_lines = (int8_t)(lines < 0 ? 0 : lines > 16 ? 16 : lines);
    c->txt_centre = (int8_t)(centre == 2 || centre == 3 ? centre : centre != 0);
    c->txt_tone = (int8_t)(tone < 0 || tone > MOY_UI_TONE_DANGER ? 0 : tone);
    c->txt_role = (int8_t)(role < 0 || role > MOY_UI_FONT_RESULT ? -1 : role);
}

void moy_ui_api_card_mark(moy_ui_ctx_t *c, moy_ui_span_t badge, int tone,
                          moy_ui_span_t blank) {
    c->card_slot = (int16_t)(badge.n ? moy_ui_icon_slot(badge.p, badge.n) : -1);
    c->card_glyph = (int16_t)(badge.n ? moy_ui_icon_glyph(badge.p, badge.n) : -1);
    c->card_tone = (int8_t)(tone < 0 || tone > MOY_UI_TONE_LOCKED ? 0 : tone);
    c->card_blank = (int16_t)(blank.n ? moy_ui_icon_slot(blank.p, blank.n) : -1);
    c->card_blank_glyph = (int16_t)(blank.n ? moy_ui_icon_glyph(blank.p, blank.n) : -1);
}

void moy_ui_api_grid_spread(moy_ui_ctx_t *c, int cols, int foot, int top, int margin,
                            int wrap) {
    c->spread_wrap = (uint8_t)(wrap != 0);
    c->spread_margin = (int16_t)(margin < 0 ? 0 : margin);
    c->spread_cols = (uint8_t)(cols < 0 ? 0 : cols > 255 ? 255 : cols);
    c->spread_foot = (int16_t)(foot < 0 ? 0 : foot);
    c->spread_top = (int16_t)(top < 0 ? 0 : top);
}

int moy_ui_api_empty(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t icon,
                     moy_ui_span_t title, const uint8_t *lines, size_t n) {
    mua_grid_end(c, s);
    int slot = moy_ui_icon_slot(icon.p, icon.n), glyph = moy_ui_icon_glyph(icon.p, icon.n);
    moy_ui_box_t m = c->has_at ? c->at : c->page;
    int h = moy_ui_empty(s, m, slot, glyph, title, lines, n, c->fs, mua_ground_of(c), 0);
    int stacked;
    moy_ui_box_t b = mua_place(c, h, &stacked);
    b.x = m.x;
    b.w = m.w;
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_empty(s, b, slot, glyph, title, lines, n, c->fs, mua_ground_of(c), 1);
        mua_clip_out(&k);
    }
    return b.y + h;
}

int moy_ui_api_month(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t days, int first, int n, int today) {
    int weeks = (first + n + 6) / 7;
    int h = moy_ui_month_h(weeks, c->fs, c->cls);
    int stacked;
    moy_ui_box_t b;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
        stacked = 0;
    } else {
        mua_group_close(c, s);
        b = mua_place(c, h, &stacked);
        int pad = mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
        b.x = (int16_t)(b.x + pad);
        b.w = (int16_t)(b.w - 2 * pad);
    }
    int chevrons = c->cls || moy_ui_input() == MOY_UI_INPUT_TOUCH;
    moy_ui_box_t ch[2] = {{0, 0, 0, 0}, {0, 0, 0, 0}};
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_month(s, b, title, days, first, n, today, chevrons, c->fs, c->cls, ch);
        mua_clip_out(&k);
    }
    // a press on a chevron pages the month
    int p = c->in[MOY_UI_IN_PTR];
    if (chevrons && (p & MOY_UI_P_LIVE) && (p & MOY_UI_P_EDGE)) {
        int x = c->in[MOY_UI_IN_X], y = c->in[MOY_UI_IN_Y];
        for (int i = 0; i < 2; i++) {
            if (mua_inside(ch[i], x, y)) {
                return i ? 1 : -1;
            }
        }
    }
    return 0;
}

void moy_ui_api_toast(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      moy_ui_span_t kind, int flat, int32_t out[4]) {
    moy_ui_box_t m = c->page;
    if (c->has_at) {
        m = c->at;
        c->has_at = 0;
    } else {
        m.y = (int16_t)(m.y + 8 * c->fs);
        m.h = (int16_t)(m.h - 16 * c->fs);
    }
    static const uint8_t check[] = "check";
    moy_ui_span_t ic = kind.n ? kind : (moy_ui_span_t){check, 5};
    int slot = moy_ui_icon_slot(ic.p, ic.n), glyph = moy_ui_icon_glyph(ic.p, ic.n);
    moy_ui_box_t p = {0, 0, 0, 0};
    c->fl_dirty = 1;                    // over the page, whole
    c->fl_top = 1;
    moy_ui_toast(s, m, text, slot, glyph, c->fs, mua_ground_of(c), flat, &p);
    if (out != NULL) {
        out[0] = p.x;
        out[1] = p.y;
        out[2] = p.w;
        out[3] = p.h;
    }
}

// -- card grids ----------------------------------------------------------------------

// The card drawn last: its picture finished over what the app drew in it.
void mua_card_finish(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->fin_on) {
        return;
    }
    c->fin_on = 0;
    clip_t k;
    c->fl_cast = 1;
    EACH_ROW(c, k, s, c->fin_content, c->grid_open == 1, c->page) {
        if (c->fin_slot >= 0 || c->fin_glyph >= 0 || c->fin_tone == MOY_UI_TONE_DANGER
            || c->fin_tone == MOY_UI_TONE_LOCKED) {
            moy_ui_card_badge(s, c->fin_content, c->fin_slot, c->fin_glyph, c->fin_tone,
                              c->fin_ground, c->fs, mua_ground_of(c));
        }
        moy_ui_card_end(s, c->fin_pic, c->fin_content, c->fin_flags ? MOY_UI_F_FOCUS : 0,
                        c->fin_ground, c->fs, mua_ground_of(c));
        mua_clip_out(&k);
    }
}

// The open grid ends: its last card finished, the page's cursor under its
// last row, and a group it opened in holding its rows' card from there.
void mua_grid_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->grid_open) {
        return;
    }
    mua_card_finish(c, s);
    if (c->grid_open == 1) {
        if (c->grid_i > 0) {
            c->y = (int16_t)(c->grid_y + c->grid_row);
        }
        moy_ui_keep_t *k = mua_page_keep(c);
        if (k != NULL) {
            k->b = c->y;
        }
        if (c->grp_open && c->grp_n == 0) {
            c->grp_top = c->y;
        }
    }
    c->grid_open = 0;
}

void moy_ui_api_grid(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o) {
    (void)mua_over(o);
    mua_grid_end(c, s);
    if (id.n == 0) {
        return;
    }
    int fs = c->fs;
    if (w <= 0) {
        w = 48 * fs;
    }
    if (h <= 0) {
        h = w * 3 / 4;
    }
    if (c->grp_open && c->grp_n > 0) {
        mua_group_close(c, s);          // a grid lies on the page's ground
    }
    int pad = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD);
    int gap = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_GAP);
    moy_ui_box_t area;
    if (c->has_at) {
        area = c->at;
        c->has_at = 0;
        c->grid_open = 2;
        c->grid_y = area.y;
    } else {
        area = c->page;
        c->grid_open = 1;
        c->y = (int16_t)(c->y + (c->spread_cols > 0 && c->spread_top > 0
                                     ? c->spread_top * c->fs
                                     : mua_pmetric(c, MOY_UI_M_VALUE_ROW_GRID_TOP)));
        c->grid_y = c->y;
    }
    int cols = (area.w - 2 * pad + gap) / (w + gap);
    cols = cols < 1 ? 1 : cols > 255 ? 255 : cols;
    int span = cols * w + (cols - 1) * gap;
    c->grid_cols = (uint8_t)cols;
    c->grid_i = 0;
    c->grid_x = (int16_t)(area.x + (area.w - span) / 2);
    c->grid_pitch = (int16_t)(w + gap);
    int foot = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_FOOT);
    if (c->spread_cols > 0) {
        // the columns spread across the rect, the gaps taking the rest
        cols = c->spread_cols;
        int m = c->spread_margin * c->fs;
        c->grid_cols = (uint8_t)cols;
        c->grid_x = (int16_t)(area.x + m);
        c->grid_pitch = (int16_t)(cols > 1 ? (area.w - 2 * m - w) / (cols - 1) : w);
        if (c->spread_foot > 0) {
            foot = c->spread_foot * c->fs;
        }
        c->spread_cols = 0;
        c->spread_foot = c->spread_top = c->spread_margin = 0;
    }
    c->grid_wrap = c->spread_wrap;
    c->spread_wrap = 0;
    c->grid_w = (int16_t)w;
    c->grid_h = (int16_t)h;
    c->grid_row = (int16_t)(mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_TOP) + h + foot
                            + mua_pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP));
}

void moy_ui_api_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t sub, const moy_ui_over_t *o, int32_t out[5]) {
    o = mua_over(o);
    mua_card_finish(c, s);
    memset(out, 0, 5 * sizeof(int32_t));
    c->pic_clip = (moy_ui_box_t){0, 0, 0, 0};
    c->pic_tbl = 0;
    if (!c->grid_open) {
        return;
    }
    int col = c->grid_i % c->grid_cols;
    if (col == 0 && c->grid_i > 0) {
        c->grid_y = (int16_t)(c->grid_y + c->grid_row);
    }
    if (col == 0 && c->grid_open == 1) {
        c->y = (int16_t)(c->grid_y + c->grid_row);
        moy_ui_keep_t *k = mua_page_keep(c);
        if (k != NULL) {
            k->b = c->y;
        }
    }
    if (c->grid_i < 0xffff) {
        c->grid_i++;
    }
    c->icon_slot = c->icon_glyph = -1;
    int pad = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD), top = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_TOP);
    int stacked = c->grid_open == 1;
    int y = stacked ? c->page.y + c->grid_y - c->scroll : c->grid_y;
    moy_ui_box_t pic = {(int16_t)(c->grid_x + col * c->grid_pitch), (int16_t)(y + top), c->grid_w,
                        c->grid_h};
    moy_ui_box_t b = {(int16_t)(pic.x - pad), (int16_t)y, (int16_t)(pic.w + 2 * pad),
                      (int16_t)(c->grid_row - mua_pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP))};
    int st = 0, moved = 0;
    c->at_last = (uint8_t)!stacked;
    if (o->disabled != 1) {
        st = mua_stop(c, mua_id_of(c, o, title), b, 0, mua_name_of(o, title));
        if (st >= 0) {
            // a blank card's picture is the engine's tile: the app draws none
            moved = mua_pic_note(c, o, c->card_blank < 0 && c->card_blank_glyph < 0);
            c->cur[c->n_cur - 1].flags |= STOP_GRID;
            mua_sig_mix(c, sub.p, sub.n);
        }
        st = st < 0 ? 0 : st;
    }
    int f = mua_mark_flags(st, o);
    clip_t k;
    moy_ui_box_t content = pic;
    uint16_t g = 0;
    int any = 0;
    if (moved) {
        // where the card puts its picture: measured under an empty clip
        moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
        int32_t saved[4];
        memcpy(saved, &row->st[MOY_ST_CX0], sizeof(saved));
        row->st[MOY_ST_CX1] = row->st[MOY_ST_CX0];
        moy_ui_card(s, b, pic, title, sub, f | (c->grid_wrap ? MOY_UI_F_WRAP : 0), c->fs,
                    mua_ground_of(c), c->card_tone, &content, &g);
        memcpy(&row->st[MOY_ST_CX0], saved, sizeof(saved));
        mua_pic_redraw(c, content);
    }
    c->fl_cast = 1;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_card(s, b, pic, title, sub, f | (c->grid_wrap ? MOY_UI_F_WRAP : 0), c->fs,
                    mua_ground_of(c), c->card_tone, &content, &g);
        if (c->card_blank >= 0 || c->card_blank_glyph >= 0) {
            moy_ui_card_blank(s, content, c->card_blank, c->card_blank_glyph, c->card_tone,
                              c->fs, mua_ground_of(c));
        }
        any = 1;
    }
    if (!any) {
        out[4] = st;
        c->card_slot = c->card_glyph = c->card_blank = c->card_blank_glyph = -1;
        c->card_tone = 0;
        return;                     // scrolled out of view: nothing to draw into
    }
    c->fin_slot = c->card_slot;
    c->fin_glyph = c->card_glyph;
    c->fin_tone = c->card_tone;
    c->card_slot = c->card_glyph = c->card_blank = c->card_blank_glyph = -1;
    c->card_tone = 0;
    c->fin_on = 1;
    c->fin_flags = (uint8_t)(f & MOY_UI_F_FOCUS ? 1 : 0);
    c->fin_ground = g;
    c->fin_pic = pic;
    c->fin_content = content;
    out[4] = st;
    if (mua_outside_floor(c, b)) {
        return;                     // a shift frame's: what the app drew there stands
    }
    c->pic_clip = mua_floor_of(c, content);
    c->pic_tbl = 0;
    out[0] = content.x;
    out[1] = content.y;
    out[2] = content.w;
    out[3] = content.h;
}

int moy_ui_api_table_rows(const moy_ui_ctx_t *c, int32_t out[8]) {
    out[0] = c->tbl_y0;
    out[1] = c->tbl_rh;
    out[2] = c->tbl_view.x;
    out[3] = c->tbl_view.y;
    out[4] = c->tbl_view.w;
    out[5] = c->tbl_view.h;
    out[6] = c->tbl_px;
    out[7] = c->tbl_pw;
    return c->tbl_pw > 0;
}

void moy_ui_api_table_pics(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (c->tbl_pw <= 0) {
        return;
    }
    moy_ui_box_t v = c->tbl_view;
    moy_ui_box_t pic = {c->tbl_px, v.y, c->tbl_pw, v.h};
    mua_edge_fades(c, s, pic, c->tbl_n * c->tbl_rh, v.y - c->tbl_y0, MOY_UI_DEFAULT, 1, c->tbl_rh);
}

void moy_ui_api_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    mua_parts_close(c, s);
    if (c->n_nest == 0) {
        mua_group_close(c, s);
        mua_foot_fade(c, s);
        mua_page_close(c, s);
    }
    if (c->tip_at.w > 0 && c->tip[0]) {
        moy_ui_span_t t = {(const uint8_t *)c->tip, (uint16_t)strlen(c->tip)};
        c->fl_dirty = 1;                // over the page, whole
        c->fl_top = 1;
        moy_ui_tip_draw(s, c->tip_at, t, c->surface, c->fs, mua_ground_of(c));
        c->tip_at.w = 0;
        c->tip_shown = 1;
    }
}

// -- an empty page, tools, the app's bar ----------------------------------------------

// The words of a tool's label before its tab, and the tooltip: the label
// with the tab as two spaces.
static moy_ui_span_t tool_word(moy_ui_span_t label) {
    moy_ui_span_t w = label;
    for (uint16_t i = 0; i < label.n; i++) {
        if (label.p[i] == '\t') {
            w.n = i;
            break;
        }
    }
    return w;
}

int moy_ui_api_tool(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t icon,
                    moy_ui_span_t label, const moy_ui_over_t *o) {
    o = mua_over(o);
    int slot = moy_ui_icon_slot(icon.p, icon.n), glyph = moy_ui_icon_glyph(icon.p, icon.n);
    moy_ui_span_t word = tool_word(label);
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : mua_stop(c, mua_id_of(c, o, word), b, 0, mua_name_of(o, word));
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        int fl = mua_mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0);
        moy_ui_tool_draw(s, b, slot, glyph, word, fl, mua_interact(st), c->fs, mua_ground_of(c));
        mua_clip_out(&k);
    }
    // a resting pointer's tooltip, on the desk: the label and its key
    if (c->cls && (st & MOY_UI_ST_HOVER) && moy_ui_input() != MOY_UI_INPUT_TOUCH) {
        size_t n = 0;
        for (uint16_t i = 0; i < label.n && n < MOY_UI_API_TIP - 2; i++) {
            if (label.p[i] == '\t') {
                c->tip[n++] = ' ';
                c->tip[n++] = ' ';
            } else {
                c->tip[n++] = (char)label.p[i];
            }
        }
        c->tip[n] = 0;
        c->tip_at = b;
    }
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int32_t moy_ui_api_bar(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                       const uint8_t *tools, size_t len, uint32_t off) {
    (void)s;
    size_t n = title.n < MOY_UI_API_TIP - 1 ? title.n : MOY_UI_API_TIP - 1;
    if (n) {
        memcpy(c->bar_title, title.p, n);
    }
    c->bar_title[n] = 0;
    int k = moy_ui_fields(tools, len);
    k = k < MOY_UI_API_BAR ? k : MOY_UI_API_BAR;
    c->bar_chip[0] = 0;
    c->bar_chip_at = -1;
    for (int i = 0; i < k; i++) {
        moy_ui_span_t t = field_at(tools, len, i);
        c->bar_slot[i] = (int16_t)moy_ui_icon_slot(t.p, t.n);
        if (c->bar_slot[i] < 0 && t.n) {
            size_t m = t.n < MOY_UI_API_CHIP - 1 ? t.n : MOY_UI_API_CHIP - 1;
            memcpy(c->bar_chip, t.p, m);
            c->bar_chip[m] = 0;
            c->bar_chip_at = (int8_t)i;
        }
    }
    c->bar_n = (uint8_t)k;
    c->bar_off = (uint8_t)(off & ((1u << MOY_UI_API_BAR) - 1u));
    c->bar_back = (off & MOY_UI_BAR_BACK) != 0;
    c->bar_seen = 1;
    int32_t got = c->bar_act;
    c->bar_act = -1;
    if (got == MOY_UI_BAR_UP) {
        return c->bar_back ? got : -1;
    }
    if (got >= k || (got >= 0 && (c->bar_off >> got) & 1u)) {
        got = -1;
    }
    return got;
}

void moy_ui_api_rule(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (c->has_at) {
        moy_ui_box_t b = c->at;
        c->has_at = 0;
        if (b.h > b.w) {
            b.w = (int16_t)c->fs;
        } else {
            b.h = (int16_t)c->fs;
        }
        clip_t k;
        EACH_PIECE(c, k, s, b) {
            moy_ui_line(s, b, c->fs, mua_ground_of(c));
        }
        return;
    }
    mua_group_close(c, s);
    int stacked;
    moy_ui_box_t r = mua_place(c, mua_rule_h(c), &stacked);
    clip_t k;
    EACH_ROW(c, k, s, r, stacked, c->page) {
        moy_ui_rule(s, r, c->fs, c->cls);
        mua_clip_out(&k);
    }
}

int moy_ui_api_bar_lent(const moy_ui_ctx_t *c, const char **title, int16_t *slots, int *n,
                        uint32_t *off) {
    if (!c->bar_seen) {
        return 0;
    }
    *title = c->bar_title;
    for (int i = 0; i < c->bar_n; i++) {
        slots[i] = c->bar_slot[i];
    }
    *n = c->bar_n;
    *off = c->bar_off | (c->bar_back ? MOY_UI_BAR_BACK : 0u);
    return 1;
}

const char *moy_ui_api_bar_chip(const moy_ui_ctx_t *c, int *at) {
    *at = c->bar_seen ? c->bar_chip_at : -1;
    return *at >= 0 ? c->bar_chip : "";
}

void moy_ui_api_bar_tap(moy_ui_ctx_t *c, int i) {
    c->bar_act = (int8_t)((i >= 0 && i < MOY_UI_API_BAR) || i == MOY_UI_BAR_UP ? i : -1);
}

int moy_ui_api_back(moy_ui_ctx_t *c, int take) {
    if (c->pop_card.w > 0) {
        if (take) {
            c->pop_back = 1;
        }
        return 1;
    }
    if (c->bar_seen && c->bar_back) {
        if (take) {
            c->bar_act = MOY_UI_BAR_UP;
        }
        return 1;
    }
    return 0;
}

int moy_ui_api_owes(const moy_ui_ctx_t *c) {
    return c->owe;
}

int moy_ui_api_moved(const moy_ui_ctx_t *c) {
    return c->moved;
}

int moy_ui_api_moving(const moy_ui_ctx_t *c) {
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        if (c->keep[i].id && moy_ui_scroll_moving(&c->keep[i].sc)) {
            return 1;
        }
    }
    return 0;
}

int32_t moy_ui_api_offset(moy_ui_ctx_t *c, int x, int y) {
    int pg = mua_page_at(c->cur, c->n_cur, x, y);
    moy_ui_keep_t *k = pg >= 0 ? mua_keep_of(c, c->cur[pg].id) : NULL;
    return k != NULL ? k->a : -1;
}
