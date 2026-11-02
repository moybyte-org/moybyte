// The public UI API's tables, fields and dialogs (moy_ui_api.h): a table's
// rows and parts, the blocks of fields an app sets through the engine, a
// text field, and a dialog over its wash.

#include "moy_ui_api_int.h"

// -- a table's rows ------------------------------------------------------------------

// The flags table row i draws with (moy_ui_table_row's).
static int table_flags(const moy_ui_ctx_t *c, const moy_ui_over_t *o, uint32_t id, int i, int cur,
                       int st, int disabled, int grp) {
    int f = (i == cur ? MOY_UI_F_ON : 0) | (disabled ? MOY_UI_F_DISABLED : 0);
    if ((st & MOY_UI_ST_FOCUS) && o->mark != 0 && i == cur) {
        f |= MOY_UI_F_FOCUS;
        if (grp) {
            f &= ~MOY_UI_F_ON;          // a group's row: the focus mark alone
        }
    }
    if (grp && c->modal && c->modal_from == id) {
        f &= ~MOY_UI_F_ON;              // its focus under the dialog that took it
    }
    if (i == 0 && o->lead == 1) {
        f |= MOY_UI_F_LEAD;
    }
    if (o->current == 0) {
        f &= ~MOY_UI_F_ON;              // its parts draw the current row's mark
    }
    if (i == cur && o->bar == 1) {
        f |= MOY_UI_F_BAR;
    }
    return f;
}

// Where a table's row that begins at `at` among `cells` (nc fields a row)
// ends: the NUL after its last field, or cells_n.
static size_t table_row_end(const uint8_t *cells, size_t cells_n, int nc, size_t at) {
    size_t e = at;
    for (int j = 0; j < nc; j++) {
        if (e < cells_n) {
            e = (size_t)(moy_ui_nul(cells + e, cells + cells_n) - cells);
        }
        if (j < nc - 1) {
            e++;
        }
    }
    return e;
}

// Whether this frame's pass draws row box b of a table: on a frame drawn
// whole, or outside a shift frame's R, or where its floor reaches.
static int row_draws(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    if (!mua_armed(c) || c->fl_stage == 0) {
        return 1;
    }
    if (!mua_rc_meets(mua_rc_of(b), mua_rc_of(c->sh_r))) {
        return !(c->sh_dlg && c->fl_stage == 1);
    }
    return mua_floor_meets(c, b);
}

// The part open (moy_ui_api_table_part) closes: its ids no longer scoped.
static void part_out(moy_ui_ctx_t *c) {
    c->tp_open = 0;
    mua_scope = 0;
}

// The parts of the table drawn last are done: the fades at the edges its
// rows run on past, over them.
void mua_parts_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    part_out(c);
    if (c->tp_id && c->tp_carry) {
        // the stops in the parts of the rows the frame carried, as the last
        // frame drew them, moved with their rows
        int dy = c->tp_y0 - c->tp_py0;
        for (int i = 0; i < c->n_prev && c->n_cur < MOY_UI_API_STOPS; i++) {
            const moy_ui_stop_t *q = &c->prev[i];
            int k = q->row - c->tp_v0;
            if (q->top != INT16_MIN || q->row < c->tp_v0 || q->row >= c->tp_e || k >= 64
                || (c->tp_ans >> k) & 1u) {
                continue;
            }
            moy_ui_stop_t *t = &c->cur[c->n_cur++];
            *t = *q;
            t->b.y = (int16_t)(t->b.y + dy);
            t->page = c->page_stop;
        }
    }
    if (c->tp_id && c->tp_fade) {
        mua_edge_fades(c, s, c->tp_view, c->tp_n * c->tp_rh, c->tp_off, MOY_UI_DEFAULT, 1, c->tp_rh);
    }
    c->tp_id = 0;
}

// -- blocks of fields an app sets through the engine (moy_ui_api_block_*) ------

typedef struct {
    uint32_t id, gen, used;
    uint32_t v[MOY_UI_BLOCK_FIELDS];    // each field's value...
    uint32_t g[MOY_UI_BLOCK_FIELDS];    // ...and the generation that set it (0: unset)
} block_t;
static block_t *s_blocks;               // MOY_UI_BLOCKS of them, PSRAM on a board,
                                        // taken at the first block set
static uint32_t s_block_tick;
static uint32_t s_block_gen;            // one count for every block: a block taken anew
                                        // sets its fields past any generation a part kept

// The block `id` names; with `make`, one taken for it (the free one, else
// the one set longest ago) where none holds it. NULL where the blocks'
// memory could not be had.
static block_t *block_of(moy_ui_span_t id, int make) {
    if (s_blocks == NULL) {
        if (!make) {
            return NULL;
        }
        s_blocks = (block_t *)KEPT_ALLOC(MOY_UI_BLOCKS * sizeof(block_t));
        if (s_blocks == NULL) {
            return NULL;
        }
        memset(s_blocks, 0, MOY_UI_BLOCKS * sizeof(block_t));
    }
    uint32_t h = mua_hash(id.p, id.n, 0x626c6f63u) | 1u;
    block_t *take = NULL;
    for (int i = 0; i < MOY_UI_BLOCKS; i++) {
        block_t *b = &s_blocks[i];
        if (b->id == h) {
            b->used = ++s_block_tick;
            return b;
        }
        if (take == NULL || (take->id != 0 && (b->id == 0 || b->used < take->used))) {
            take = b;
        }
    }
    if (!make) {
        return NULL;
    }
    memset(take, 0, sizeof(*take));
    take->id = h;
    take->used = ++s_block_tick;
    return take;
}

int moy_ui_api_block_set(moy_ui_span_t id, int field, uint32_t value) {
    if (field < 0 || field >= MOY_UI_BLOCK_FIELDS) {
        return 0;
    }
    block_t *b = block_of(id, 1);
    if (b == NULL) {
        return 1;                       // (nothing kept: every set is a change)
    }
    if (b->g[field] != 0 && b->v[field] == value) {
        return 0;
    }
    b->v[field] = value;
    b->g[field] = b->gen = ++s_block_gen;
    return 1;
}

int32_t moy_ui_api_block_gen(moy_ui_span_t id) {
    const block_t *b = block_of(id, 0);
    return b == NULL ? 0 : (int32_t)b->gen;
}

uint32_t moy_ui_api_block_since(moy_ui_span_t id, int32_t gen) {
    const block_t *b = block_of(id, 0);
    uint32_t m = 0;
    if (b == NULL) {
        return ~0u;                     // (no block: nothing is known of its fields)
    }
    for (int i = 0; i < MOY_UI_BLOCK_FIELDS; i++) {
        if (b->g[i] > (uint32_t)gen) {
            m |= 1u << i;
        }
    }
    return m;
}

int32_t moy_ui_api_block_key(moy_ui_span_t id) {
    const block_t *b = block_of(id, 0);
    if (b == NULL) {
        return 0;
    }
    return (int32_t)(mua_hash((const uint8_t *)b->v, sizeof(b->v), 0x6b657973u) & 0x3fffffffu);
}

// -- tables, fields, dialogs --------------------------------------------------------

// A stacked table on its page's first frame, about to lay out at content
// `top` (its rows `head` below that, `count` of them `rh` tall): where its
// row `row` lies below what the page shows, the page's scroll moves there IN
// THIS FRAME, before the rows draw -- far enough that everything above the
// table goes out of sight with it, which is what lets the frame stand: what
// the page drew above at scroll 0 is painted over with its ground, and the
// stops and captions it recorded move with the scroll. 1 when it moved the
// scroll; 0 where the row shows already or the rows end before the scroll
// would stop (the next frame's reveal then does it).
static int page_open_on(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int top, int head, int rh,
                        int row, int count) {
    moy_ui_keep_t *k = mua_page_keep(c);
    if (k == NULL || c->scroll != 0 || c->page_stop == NO_PAGE || c->sh_dlg) {
        return 0;
    }
    int vis = c->page.h;
    int y1 = top + head + (row + 1) * rh;
    int fade = row < count - 1 ? FADE_H * c->fs : 0;
    if (y1 + fade <= vis) {
        return 0;                       // the row shows at the page's top
    }
    int to = y1 + fade - vis;
    if (to < top) {
        to = top;                       // what lies above goes out of sight with it
    }
    if (to > top + head + count * rh - vis) {
        return 0;                       // the table's rows end first
    }
    mua_ground_fill(c, s, (moy_ui_box_t){c->page.x, c->page.y, c->page.w,
                                     (int16_t)(top < c->page.h ? top : c->page.h)});
    for (int i = c->page_stop + 1; i < c->n_cur; i++) {
        if (c->cur[i].page == c->page_stop) {
            c->cur[i].b.y = (int16_t)(c->cur[i].b.y - to);
            if (c->cur[i].row >= 0) {
                c->cur[i].top = (int16_t)(c->cur[i].top - to);   // a list's row 0
            }
        }
    }
    for (int i = 0; i < c->n_cap_cur; i++) {
        if (c->cap_cur[i].page == c->page_stop) {
            c->cap_cur[i].y = (int16_t)(c->cap_cur[i].y - to);
        }
    }
    if (c->grp_open && c->grp_n) {
        c->grp_last.y = (int16_t)(c->grp_last.y - to);
    }
    c->scroll = (int16_t)to;
    k->a = to;
    k->drawn = to;
    return 1;
}

void moy_ui_api_table(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                      const uint8_t *cols, size_t cols_n, const uint8_t *cells, size_t cells_n,
                      int32_t cur, const moy_ui_over_t *o, int32_t out[2]) {
    o = mua_over(o);
    mua_parts_close(c, s);
    int nc = moy_ui_fields(cols, cols_n);
    if (nc < 1) {
        nc = 1;
    }
    int count = moy_ui_fields(cells, cells_n) / nc;
    int part = o->has_part == 1;
    int rh = part && o->part_h > 0 ? o->part_h : mua_row_h(c);
    // rows side by side (`axis` "x"): rh is a row's width along the axis,
    // ph its height (0: the view's), and the row is the part
    int xa = part && o->axis_x == 1, ph = 0;
    if (xa) {
        rh = o->part_w > 0 ? o->part_w : mua_row_h(c);
        ph = o->part_h;
    }
    int cap_h = label.n ? mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_CAPTION + (c->cls ? 1 : 0)) * c->fs
                        : 0;
    int named = 0;
    for (size_t i = 0; i < cols_n && !named; i++) {
        named = cols[i] != 0;
    }
    int head_h = named && !xa ? mua_pmetric(c, MOY_UI_M_VALUE_ROW_HEAD) : 0;
    int placed = c->has_at;
    int stacked;
    c->pic_tbl = 0;
    // a stacked table on its page's first frame opens on its current row,
    // as a placed one does, where it holds the focus or nothing does (the
    // keys another surface's this frame, or a focus left free, which the
    // first stop drawn takes): in this frame where the page can move before
    // the rows draw, else the next frame reveals it
    int fresh = 0, opened_on = 0;
    if (!placed && !xa && c->page_new && c->n_nest == 0 && o->disabled != 1 && count > 0
        && cur > 0) {
        uint32_t tid = mua_id_of(o, label);
        fresh = c->focus == tid || c->focus == 0;
        if (fresh) {
            opened_on = page_open_on(c, s, c->y, cap_h + head_h, rh,
                                     cur < count ? cur : count - 1, count);
        }
    }
    moy_ui_box_t b = mua_place(c, cap_h + head_h + (xa ? (ph > 0 ? ph : mua_row_h(c))
                                                   : (count ? count : 1) * rh), &stacked);
    moy_ui_box_t view = {b.x, (int16_t)(b.y + cap_h + head_h), b.w,
                         (int16_t)(b.h - cap_h - head_h > 0 ? b.h - cap_h - head_h : 0)};
    moy_ui_tgeo_t g;
    if (part && (nc == 1 || xa || o->has_across)) {
        memset(&g, 0, sizeof(g));       // the row is the part (its parts across, where `across`)
        g.n = 1;
        g.pic = 1;
        g.x[0] = view.x;
        g.w[0] = view.w;
    } else {
        moy_ui_table_geo(s, b, cols, cols_n, cells, cells_n, c->fs, c->cls, &g);
        part = part && g.pic;
        if (part && o->part_w > 0) {
            // the part takes its width; the first text column what is left
            int d = o->part_w - g.w[0];
            g.w[0] = (int16_t)o->part_w;
            g.x[1] = (int16_t)(g.x[1] + d);
            g.w[1] = (int16_t)(g.w[1] - d > 0 ? g.w[1] - d : 0);
        }
    }
    uint32_t id = mua_id_of(o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled || !placed ? NULL : mua_keep(c, id, 1);
    int carry = part && kp != NULL && !xa;
    if (cur >= count) {
        cur = count - 1;
    }
    int focused = !disabled && c->focus == id && count > 0;
    int row = cur;
    if (focused) {
        row = cur < 0 ? 0 : cur + (xa ? c->step_x : c->step_y);
    }
    // the view's extent along the rows' axis, and where row 0 starts in it
    // at scroll 0: centred, where a still view rests on a row's centre
    int ext = xa ? view.w : view.h;
    int lead = o->snap == 1 && count > 0 ? (ext - rh) / 2 : 0;
    int32_t content = o->snap == 1 && count > 0 ? (int32_t)(count - 1) * rh + ext
                                                : (int32_t)count * rh;
    if (row >= count) {
        row = count - 1;
    }
    if (row < 0 && cur >= 0) {
        row = 0;
    }
    if (placed && kp != NULL && c->n_cur < MOY_UI_API_STOPS) {
        // the rows' view: what a drag over them scrolls, by the table's kept offset
        moy_ui_stop_t *p = &c->cur[c->n_cur++];
        memset(p, 0, sizeof(*p));
        p->id = id;
        p->b = view;
        p->flags = (uint16_t)(STOP_PAGE | (mua_in_dialog(c) ? STOP_MODAL : 0));
        p->page = NO_PAGE;
        p->row = -1;
        p->top = 0;
    }
    int flags = xa ? STOP_KEEP_LR
                   : STOP_KEEP_UD | (row <= 0 ? STOP_AT_TOP : 0)
                         | (row >= count - 1 ? STOP_AT_END : 0) | (placed ? 0 : STOP_ROWS);
    int st = disabled ? 0 : mua_stop(c, id, b, flags, mua_name_of(o, label));
    if (carry && st >= 0 && mua_audit_on) {
        // where its rows end is the carried rows' record's, not its own
        moy_ui_span_t nm = mua_name_of(o, label);
        c->cur[c->n_cur - 1].sig = mua_hash(nm.p, nm.n, (uint32_t)(c->cur[c->n_cur - 1].flags
                                                              & ~(STOP_AT_TOP | STOP_AT_END)));
        mua_sig_int(c, st);
    }
    if (st < 0) {
        st = 0;
    }
    int offset = kp != NULL ? kp->a : 0;
    // what the last frame drew, for a shift frame's floor
    int32_t was_drawn = kp != NULL ? kp->drawn : 0, was_b = kp != NULL ? kp->b : 0;
    int32_t was_gen = kp != NULL ? kp->gen : 0;
    int was_kd = kp != NULL ? kp->kd : 0, was_rh = kp != NULL ? kp->rh : 0;
    int opened = 0;
    if (kp != NULL && o->has_offset) {
        offset = o->offset;             // the app's own scroll
    }
    if ((st & MOY_UI_ST_ACTIVATED) && count) {
        if (c->tap_y != INT16_MIN) {
            int t = xa ? c->tap_x - view.x - lead : c->tap_y - view.y;
            int r = moy_ui_fdiv(t + offset, rh > 0 ? rh : 1);
            if (t >= 0 && r >= 0 && r < count) {
                opened = r == cur;
                row = r;
            }
        } else {
            opened = row >= 0;
        }
    }
    if (focused || (st & MOY_UI_ST_ACTIVATED)) {
        cur = row;
    }
    if (kp != NULL) {
        // the rows' own scroll: a step glides it to the row, where the
        // frame has a clock (with none, it is there at once)
        kp->b = content;
        // a step or a tap moved it, else the app did (its own keys); a
        // table's first frame (no row height drawn yet) opens on its
        // current row, wherever the app put it
        int stepped = focused && ((xa ? c->step_x : c->step_y) || (st & MOY_UI_ST_ACTIVATED));
        int moved = !stepped && cur >= 0 && ((carry && cur != c->tc_cur) || kp->rh == 0);
        int snap = o->snap == 1 && count > 0 && !o->has_offset;
        if (cur >= 0 && (stepped || moved) && !o->has_offset) {
            int to = (kp->sc.flags & MOY_UI_SCROLL_GLIDE) ? kp->sc.target : offset;
            int was = to;
            if (snap) {
                to = cur * rh;          // the row at the centre
            } else if (cur * rh < to) {
                to = cur * rh;
            } else if ((cur + 1) * rh > to + ext) {
                to = (cur + 1) * rh - ext;
            }
            if (to != was && c->in[MOY_UI_IN_DT] > 0 && !moved) {
                int32_t off = offset;   // the glide's first step is this frame's
                moy_ui_scroll_glide_to(&kp->sc, offset, to, 0, kp->b, ext);
                moy_ui_scroll_tick(&kp->sc, (float)c->in[MOY_UI_IN_DT], &off, kp->b, ext);
                offset = off;
            } else if (to != was) {
                offset = to;            // the app moved its current row: it lands at once
                moy_ui_scroll_stop(&kp->sc);
            }
        }
        if (snap) {
            int near = (int)moy_ui_fdiv(offset + rh / 2, rh > 0 ? rh : 1);
            near = near < 0 ? 0 : near >= count ? count - 1 : near;
            if (kp->sc.flags & (MOY_UI_SCROLL_DRAG | MOY_UI_SCROLL_FLING)) {
                cur = near;             // a drag or a fling: the row at the centre is current
            } else if (!(kp->sc.flags & MOY_UI_SCROLL_GLIDE) && offset != near * rh) {
                // a view come to rest between rows glides on to the nearest
                cur = near;
                if (c->in[MOY_UI_IN_DT] > 0) {
                    int32_t off = offset;
                    moy_ui_scroll_glide_to(&kp->sc, offset, near * rh, 1, kp->b, ext);
                    moy_ui_scroll_tick(&kp->sc, (float)c->in[MOY_UI_IN_DT], &off, kp->b, ext);
                    offset = off;
                } else {
                    offset = near * rh;
                }
            }
        }
        int most = (int)(content - ext);
        if (!o->has_offset && offset > most) {
            offset = most;
        }
        if (!o->has_offset && offset < 0) {
            offset = 0;
        }
        kp->a = offset;
        kp->drawn = offset;
        kp->gen = o->gen;
        kp->rh = (int16_t)rh;
        kp->s0 = kp->sn = 0;
    }
    if (!xa) {
        mua_rows_note(c, id, cur, view.y);
    }
    fresh = fresh && !opened_on;        // a first frame its page could not move in
    if (!placed && cur >= 0
        && ((focused && c->step_y && !(c->step_rev == id && c->step_row == cur)) || fresh)) {
        c->reveal = 1;              // a step begin did not reveal: the next frame does
        c->reveal_box = (moy_ui_box_t){b.x, (int16_t)(view.y + cur * rh), b.w, (int16_t)rh};
        c->owe = 1;
        if (fresh) {
            c->jump = 1;            // a page's first frame: the current row at once
            c->reveal_id = id;
        }
    }
    c->tbl_y0 = (int16_t)(view.y - offset);
    c->tbl_rh = (int16_t)rh;
    c->tbl_n = (int16_t)count;
    c->tbl_view = view;
    c->tbl_px = c->tbl_pw = 0;
    if (st >= 0 && !disabled && !carry) {
        // (a carried table's rows are their record's: a row whose cells or
        // marks changed draws again)
        mua_sig_int(c, cur);
        mua_sig_mix(c, cells, cells_n);
    }
    // the rows that show, and (a table carrying its rows) each one's record
    int vis0 = 0, vis1 = count;
    int sp = part ? o->spill : 0;
    if (placed && rh > 0) {
        vis0 = (int)moy_ui_fdiv(offset - lead - sp, rh);
        vis1 = (int)moy_ui_fdiv(offset - lead + ext + sp + rh - 1, rh);
        vis0 = vis0 > 0 ? vis0 : 0;
        vis1 = vis1 < count ? vis1 : count;
        vis1 = vis1 > vis0 ? vis1 : vis0;
    }
    int nk = vis1 - vis0;
    int kept = carry && nk >= 0 && nk <= MOY_UI_API_TROWS;
    uint32_t hv[MOY_UI_API_TROWS];
    if (kept) {
        size_t at = 0;
        for (int i = 0; i < vis1 && at <= cells_n; i++) {
            size_t e = table_row_end(cells, cells_n, nc, at);
            if (i >= vis0) {
                size_t end = e < cells_n ? e : cells_n;
                hv[i - vis0] = mua_hash(cells + at, end - at, (uint32_t)nc);
            }
            at = e + 1;
        }
        if (o->has_gen && cur >= vis0 && cur < vis1) {
            // a part table's gen keys its current row: a gen that moved
            // draws that row's part again, its cells unchanged
            hv[cur - vis0] ^= (uint32_t)o->gen * 0x9e3779b1u + 0x7f4a7c15u;
        }
    }
    uint32_t geo = mua_hash((const uint8_t *)&g, sizeof(g), (uint32_t)rh);
    int valid = kept && c->tc_id == id && c->tc_k >= 0 && c->tc_geo == geo
                && mua_same_box(c->tc_view, view) && c->tc_rh == rh && c->tc_n == count;
    int mark = (st & MOY_UI_ST_FOCUS) != 0 && o->mark != 0;
#define TC_CHANGED(i) (!valid || (i) < c->tc_r0 || (i) >= c->tc_r0 + c->tc_k \
                       || c->tc_h[(i) - c->tc_r0] != hv[(i) - vis0])
    if ((g.pic || part) && !disabled && c->n_cur > 0 && c->cur[c->n_cur - 1].id == id) {
        // the app draws its pictures (its parts): with no gen, a picture
        // column's view never shifts; a part's rows carry their records
        c->cur[c->n_cur - 1].flags |= STOP_PIC | (o->has_gen || carry ? STOP_GEN : 0);
    }
    int table_r = 0;
    if (kp != NULL) {
        // (rows side by side are never a shift frame's region: a frame
        // that draws them draws them whole)
        kp->kd = (uint8_t)(MOY_UI_KD_PAGE | MOY_UI_KD_TABLE | MOY_UI_KD_DRAWN
                           | (o->has_gen || carry ? MOY_UI_KD_GEN : 0)
                           | (xa ? MOY_UI_KD_X | MOY_UI_KD_BAD : 0)
                           | (g.pic && !part && !o->has_gen ? MOY_UI_KD_BAD : 0));
        if (mua_audit_on) {
            moy_ui_stop_t *vs = &c->cur[c->n_cur - 1];
            for (int i = c->n_cur - 1; i >= 0; i--) {
                if (c->cur[i].id == id && (c->cur[i].flags & STOP_PAGE)) {
                    vs = &c->cur[i];
                    break;
                }
            }
            vs->sig = carry ? 0 : mua_hash(cells, cells_n, (uint32_t)nc);
        }
        if (mua_armed(c) && c->fl_stage == 0 && id == c->sh_id) {
            // the view is R: its floor, from what the last frame drew
            table_r = 1;
            int early = c->fl_early;
            c->fl_stage = 1;
            if (!early) {
                c->fl_n = 0;
                c->fl_pend = 0;
            }
            c->fl_p0 = c->fl_pn = 0;
            c->fl_s0 = (uint8_t)c->n_cur;
            moy_ui_box_t r = c->sh_r;
            const moy_ui_stop_t *q = mua_table_of(c->prev, c->n_prev, id);
            int same = carry ? valid && (was_kd & MOY_UI_KD_GEN)
                             : o->has_gen && (was_kd & MOY_UI_KD_GEN) && o->gen == was_gen;
            if (q == NULL || !same || !mua_same_box(view, r) || offset - was_drawn != -c->sh_dy
                || was_b != count * rh || was_rh != rh) {
                c->fl_struct = 1;
                mua_floor_rows(c, r.y, r.y + r.h, 1);
            } else {
                int base = view.y - offset;
                if (!early) {           // else begin's floor, the stops before R drawn through it
                    mua_table_floor(c, base, rh, q->row, (q->st & MOY_UI_ST_FOCUS) != 0,
                                    cur, (st & MOY_UI_ST_FOCUS) != 0, g.pic || part,
                                    c->page.y + c->page.h);
                }
                for (int i = vis0; carry && i < vis1; i++) {
                    if (TC_CHANGED(i)) {    // a row whose cells changed
                        if (early) {
                            mua_floor_add(c, (rc_t){r.x, base + i * rh, r.x + r.w, base + (i + 1) * rh}, 1);
                        } else {
                            mua_table_strip(c, base + i * rh, base + (i + 1) * rh, base, rh, 1);
                        }
                    }
                }
                if (mua_audit_on && !carry) {
                    const moy_ui_stop_t *pv = mua_page_of_keep(c->prev, c->n_prev, kp);
                    const moy_ui_stop_t *vs = mua_page_of_keep(c->cur, c->n_cur, kp);
                    if (pv == NULL || vs == NULL || pv->sig != vs->sig) {
                        mua_audit_fail++;     // rows unlike the last frame's under one gen
                    }
                }
            }
        } else if (xa && mua_armed(c) && c->fl_stage == 1 && !c->sh_dlg && !c->fl_mute
                   && mua_rc_meets(mua_rc_of(view), mua_rc_of(c->sh_r))) {
            mua_floor_add(c, mua_rc_of(view), 1);   // inside a page that moved or holds: whole
        } else if (carry && mua_armed(c) && c->fl_stage == 1 && !c->sh_dlg && !c->fl_mute
                   && mua_rc_meets(mua_rc_of(view), mua_rc_of(c->sh_r))) {
            // inside the R of a page that moved or holds: the rows whose
            // cells or marks changed join its floor, over the page's ground
            // the host moved its rows by the table's move (sh_in_dy)
            int inner = c->sh_in_id == id && offset - c->tc_off == -c->sh_in_dy;
            if (!valid || (offset != c->tc_off && !inner)) {
                mua_floor_add(c, mua_rc_of(view), 1);
            } else {
                int base = view.y - offset, reach = mua_mark_room(c);
                rc_t vr = mua_rc_of(view);
                if (inner) {
                    mua_table_in_floor(c, base, rh, -1, 0, -1, 0, c->page.y + c->page.h);
                }
                // a stop drawn in a part whose mark changed: its row
                for (int i = 0; i < c->n_prev; i++) {
                    const moy_ui_stop_t *q = &c->prev[i];
                    int now = c->focus != 0 && q->id == c->focus && !c->elsewhere
                                  ? MOY_UI_ST_FOCUS : 0;
                    rc_t w = {vr.x0, base + q->row * rh - reach, vr.x1,
                              base + (q->row + 1) * rh + reach};
                    if (q->top == INT16_MIN && q->row >= vis0 && q->row < vis1
                        && (q->st & MARKS) != now && mua_rc_cut(&w, vr)) {
                        mua_floor_add(c, w, 1);
                    }
                }
                if (cur != c->tc_cur || mark != c->tc_mark) {
                    for (int w = 0; w < 2; w++) {
                        int i = w ? cur : c->tc_cur;
                        rc_t q = {vr.x0, base + i * rh - reach, vr.x1, base + (i + 1) * rh + reach};
                        if (i >= 0 && mua_rc_cut(&q, vr)) {
                            mua_floor_add(c, q, 1);
                        }
                    }
                }
                for (int i = vis0; i < vis1; i++) {
                    rc_t q = {vr.x0, base + i * rh, vr.x1, base + (i + 1) * rh};
                    if (TC_CHANGED(i) && mua_rc_cut(&q, vr)) {
                        mua_floor_add(c, q, 1);
                    }
                }
            }
            // a row the floor reaches it holds whole, so its part draws whole
            for (int i = vis0; i < vis1; i++) {
                rc_t q = {view.x, view.y - offset + i * rh, view.x + view.w,
                          view.y - offset + (i + 1) * rh};
                if (mua_rc_cut(&q, mua_rc_of(view)) && mua_floor_meets(c, mua_box_of(q))
                    && !mua_floor_holds(c, mua_box_of(q))) {
                    mua_floor_add(c, q, 1);
                }
            }
        }
    }
#undef TC_CHANGED
    // a table that keeps its parts: its floor from the paint the buffer
    // holds; any other table's is its view
    kept_ring_t *kr = NULL;
    int keeping = part && placed && kp != NULL && !disabled && o->has_keep && c->kt_k > 0
                  && (kr = mua_ring_of(c, 1)) != NULL;
    if (keeping) {
        // each part's record: a row's own, or with `across` each cell's
        int np = o->has_across ? nc : 1;
        int nr = nk >= 0 && nk * np <= MOY_UI_KEPT_PARTS ? nk : -1;
        int at = o->has_across && o->at >= 0 && o->at < np ? o->at : 0;
        uint32_t kh[MOY_UI_KEPT_PARTS];
        size_t q = 0;
        for (int i = 0; nr > 0 && i < vis1 && q <= cells_n; i++) {
            size_t e = table_row_end(cells, cells_n, nc, q);
            size_t end = e < cells_n ? e : cells_n;
            if (i >= vis0) {
                uint32_t *hp = kh + (i - vis0) * np;
                if (np == 1) {
                    hp[0] = mua_hash(cells + q, end - q, (uint32_t)nc);
                } else {
                    size_t f0 = q;
                    for (int j = 0; j < np; j++) {
                        size_t f1 = end;
                        if (f0 < end) {
                            f1 = (size_t)(moy_ui_nul(cells + f0, cells + end) - cells);
                        }
                        hp[j] = mua_hash(cells + f0, f1 - f0, (uint32_t)j);
                        f0 = f1 + 1;
                    }
                }
                if (o->has_gen && i == cur) {
                    hp[at] ^= (uint32_t)o->gen * 0x9e3779b1u + 0x7f4a7c15u;
                }
            }
            q = e + 1;
        }
        int a0 = xa ? view.x : view.y;
        mua_kept_table(c, kr, o, id, geo, view, offset, rh, count, cur, at, mark, vis0, nr, np, kh,
                       a0 + lead - offset, sp, xa);
    } else {
        c->kf[0] = view;
        c->kf_n = (uint8_t)(view.w > 0 && view.h > 0);
        c->kf_i = c->kf_copy = c->kf_took = c->kf_ci = c->kf_sh = 0;
        c->kf_held = MOY_UI_KF_HELD;
    }
    int16_t tc_y0 = c->tc_y0;              // the last frame's, for the parts' stops
    if (carry) {
        c->tc_y0 = (int16_t)(view.y - offset);
        c->tc_id = id;
        c->tc_geo = geo;
        c->tc_view = view;
        c->tc_off = offset;
        c->tc_rh = (int16_t)rh;
        c->tc_n = (int16_t)count;
        c->tc_cur = (int16_t)cur;
        c->tc_mark = (uint8_t)mark;
        c->tc_r0 = (int16_t)vis0;
        c->tc_k = (int16_t)(kept ? nk : -1);
        if (kept) {
            memcpy(c->tc_h, hv, (size_t)nk * sizeof(hv[0]));
        }
    }
    clip_t k;
    mua_band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked, 0);
        if (cap_h) {
            moy_ui_caption(s, (moy_ui_box_t){b.x, b.y, b.w, (int16_t)cap_h}, label, c->fs, c->cls);
        }
        // in a group, its rows are the group's: each on its card under a rule
        int grp = stacked && c->grp_open;
        moy_ui_box_t gcard = mua_card_of(c);
        if (g.pic && !part) {
            c->tbl_px = g.x[0];
            c->tbl_pw = g.w[0];
        }
        if (head_h) {
            moy_ui_table_head(s, (moy_ui_box_t){b.x, (int16_t)(b.y + cap_h), b.w, (int16_t)head_h},
                              &g, cols, cols_n, c->fs, c->cls);
        }
        clip_t kv;
        int any = mua_clip_in(&kv, s, view);
        size_t at = 0;
        int reach = table_r ? mua_mark_room(c) : 0;
        for (int i = 0; any && xa && i < count && at <= cells_n; i++) {
            // rows side by side: each cell's marks, the row its part
            int rx = view.x + lead + i * rh - offset;
            if (rx >= view.x + view.w) {
                break;                  // (the rows after lie past the view too)
            }
            size_t e = table_row_end(cells, cells_n, nc, at);
            if (rx + rh > view.x) {
                int f = table_flags(c, o, id, i, cur, st, disabled, 0) | MOY_UI_F_PART;
                g.x[0] = (int16_t)rx;
                g.w[0] = (int16_t)rh;
                moy_ui_table_row(s, (moy_ui_box_t){(int16_t)rx, view.y, (int16_t)rh, view.h}, &g,
                                 cells + at, (e < cells_n ? e : cells_n) - at, f, c->fs, c->cls);
            }
            at = e + 1;
        }
        for (int i = 0; any && !xa && i < count && at <= cells_n; i++) {
            int ry = view.y + i * rh - offset;
            if (ry >= view.y + view.h) {
                break;                  // (the rows after lie past the view too)
            }
            size_t e = table_row_end(cells, cells_n, nc, at);
            if (ry + rh > view.y && (!table_r || mua_clip_meets(s, ry, rh, reach))) {
                int f = table_flags(c, o, id, i, cur, st, disabled, grp) | (part ? MOY_UI_F_PART : 0);
                if (grp) {
                    moy_ui_group_band(s, gcard, (moy_ui_box_t){b.x, (int16_t)ry, b.w, (int16_t)rh},
                                      i == 0 && c->grp_first, c->fs, c->cls);
                }
                size_t end = e < cells_n ? e : cells_n;
                moy_ui_table_row(s, (moy_ui_box_t){b.x, (int16_t)ry, b.w, (int16_t)rh}, &g,
                                 cells + at, end - at, f, c->fs, c->cls);
            }
            at = e + 1;
        }
        if (any && !part) {
            // the row the view's foot cuts draws clipped to it, and the
            // fade, a row tall, lies over what shows of it (the desk's
            // Files ends its view half a row into one); a part's table
            // draws them over its parts (mua_parts_close)
            mua_edge_fades(c, s, view, count * rh, offset, MOY_UI_DEFAULT, 1, rh);
        }
        mua_clip_out(&kv);
        mua_clip_out(&k);
    }
    if (table_r) {
        mua_ground_due(c, s);
        if (c->fl_lost && mua_audit_on) {
            mua_audit_fail++;
        }
        c->fl_stage = 2;
        c->pic_tbl = 1;
    }
    if (part && !disabled) {
        // the parts the app draws next (moy_ui_api_table_part)
        rc_t pv = mua_rc_of(view);
        if (stacked) {
            mua_rc_cut(&pv, mua_rc_of(c->page));
        }
        c->tp_id = id;
        c->tp_view = mua_box_of(pv);
        c->tp_x1 = (uint8_t)xa;
        c->tp_x0 = (int16_t)(view.x + lead - offset);
        c->tp_spill = (int16_t)sp;
        c->tp_mid2 = xa ? 2 * view.x + view.w : 2 * view.y + view.h;
        c->tp_dist = 0;
        c->tp_y0 = (int16_t)(xa ? view.y : view.y + lead - offset);
        c->tp_rh = (int16_t)rh;
        c->tp_i = (int16_t)vis0;
        c->tp_e = (int16_t)vis1;
        c->tp_x = g.x[0];
        c->tp_w = g.w[0];
        c->tp_n = (int16_t)count;
        c->tp_off = offset;
        c->tp_v0 = (int16_t)vis0;
        c->tp_ans = 0;
        c->tp_py0 = tc_y0;
        c->tp_carry = (uint8_t)(carry && valid && mua_armed(c) && c->fl_stage != 0);
        c->tp_keep = (uint8_t)keeping;
        c->tp_np = (uint8_t)(o->has_across && nc < 256 ? nc : 1);
        // (rows side by side, and rows the app scrolls itself, fade at no edge)
        c->tp_fade = (uint8_t)(placed && !xa && !o->has_offset && pv.x0 < pv.x1 && pv.y0 < pv.y1);
        if (xa) {
            c->tp_w = (int16_t)(ph > 0 && ph < view.h ? ph : view.h);   // a row's height
        }
    }
    out[0] = cur;
    out[1] = opened;
}

int32_t moy_ui_api_table_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int32_t out[8]) {
    part_out(c);
    while (c->tp_id && c->tp_i < c->tp_e) {
        int i = c->tp_i++;
        int sp = c->tp_spill;
        int x = c->tp_x1 ? c->tp_x0 + i * c->tp_rh : c->tp_x;
        int y = c->tp_x1 ? c->tp_y0 : c->tp_y0 + i * c->tp_rh;
        int w = c->tp_x1 ? c->tp_rh : c->tp_w, h = c->tp_x1 ? c->tp_w : c->tp_rh;
        // what shows of the cell, and of the room past it a part may draw in
        rc_t q = {x - sp, y - sp, x + w + sp, y + h + sp};
        if (!c->tp_x1 && !sp) {
            q.x0 = c->tp_view.x;
            q.x1 = c->tp_view.x + c->tp_view.w;
        }
        if (!mua_rc_cut(&q, mua_rc_of(c->tp_view)) || !row_draws(c, mua_box_of(q))) {
            continue;
        }
        if (!c->tp_x1 && !sp) {
            q.x0 = x;
            q.x1 = x + w;
            if (!mua_rc_cut(&q, mua_rc_of(c->tp_view))) {
                continue;
            }
        }
        if (c->tp_keep) {
            // a kept table's part draws inside the floor rect it meets (one:
            // the floor's rects lie further apart than a part's reach)
            int k = 0;
            while (k < c->kf_n && !mua_rc_meets(q, mua_rc_of(c->kf[k]))) {
                k++;
            }
            if (k == c->kf_n || (c->kf_took & (1u << k)) || !mua_rc_cut(&q, mua_rc_of(c->kf[k]))) {
                continue;
            }
        }
        int along = c->tp_x1 ? 2 * x + w : 2 * y + h;
        c->tp_dist = (int32_t)(((int64_t)(along - c->tp_mid2) * 128) / (c->tp_rh > 0 ? c->tp_rh : 1));
        c->tp_open = 1;
        c->tp_row = (int16_t)i;
        if (i - c->tp_v0 < 64) {
            c->tp_ans |= (uint64_t)1 << (i - c->tp_v0);
        }
        mua_scope = mua_hash((const uint8_t *)&i, sizeof(i), c->tp_id);
        out[0] = x;                     // the cell whole
        out[1] = y;
        out[2] = w;
        out[3] = h;
        out[4] = q.x0;                  // what shows of it, the app's clip
        out[5] = q.y0;
        out[6] = q.x1 - q.x0;
        out[7] = q.y1 - q.y0;
        return i;
    }
    mua_parts_close(c, s);
    return -1;
}

int32_t moy_ui_api_table_dist(const moy_ui_ctx_t *c) {
    return c->tp_open ? c->tp_dist : 0;
}

int32_t moy_ui_api_field(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                         uint8_t *buf, size_t cap, size_t len, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (len > cap) {
        len = cap;
    }
    int stacked;
    // in a group the field fits the row (moy_ui_field); elsewhere the row
    // grows to hold the field and its ring
    int h = mua_row_h(c), fh = c->grp_open ? h : moy_ui_field_h(s, c->fs, c->cls);
    // in a dialog's body a field is captioned: its label over it, the field
    // across the body
    int caph = label.n && !c->grp_open && c->n_nest && c->nest[c->n_nest - 1].kind == 2
                  ? mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_CAPTION + (c->cls ? 1 : 0)) * c->fs
                  : 0;
    moy_ui_box_t b = mua_place(c, caph + (fh > h ? fh : h), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : mua_stop(c, mua_id_of(o, label), b, STOP_FIELD, mua_name_of(o, label));
    int at = !disabled && st >= 0 ? c->n_cur - 1 : -1;
    if (st < 0) {
        st = 0;
    }
    int done = 0;
    if ((st & MOY_UI_ST_FOCUS) && c->typed) {
        int k = c->typed;
        c->typed = 0;
        for (int i = c->typed_n > 0 ? c->typed_n : 1; i > 0; i--) {
            if (k == 8) {           // Delete: the caret is the end, nothing after it
                while (len > 0) {
                    len--;
                    if ((buf[len] & 0xC0) != 0x80) {
                        break;
                    }
                }
            } else if (k >= 32 && k < 127 && len < cap) {
                buf[len++] = (uint8_t)k;
            }
        }
    }
    if ((st & MOY_UI_ST_ACTIVATED) && c->tap_y == INT16_MIN) {
        done = 1;
    }
    if (at >= 0) {
        // a field's stop keeps its text's hash in its gen: text that changed
        // with no key typed into it (the app's own) draws its row again
        int32_t h = (int32_t)mua_hash(buf, len, 0x51ed270bu);
        c->cur[at].gen = h;
        int j = at - c->fl_s0;
        const moy_ui_stop_t *q = mua_armed(c) && c->fl_stage == 1 && !c->fl_struct && !c->fl_mute
                                 && j >= 0 && j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
        if (q != NULL && q->id == c->cur[at].id && q->gen != h && !mua_floor_holds(c, b)) {
            if (c->sh_dlg) {
                mua_dlg_widen(c, b.y, b.y + b.h);
            } else {
                mua_floor_add(c, mua_rc_of(b), 1);
            }
        }
    }
    clip_t k;
    int f = mua_mark_flags(st, o) & ~MOY_UI_F_ON;
    mua_band_mark(c, b, stacked, f);
    if (!disabled) {
        mua_sig_mix(c, buf, len);
    }
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked, f);
        moy_ui_span_t t = {buf, (uint16_t)len};
        moy_ui_box_t fb = b;
        moy_ui_span_t lb = label;
        if (caph) {
            // the caption's letters over the field's left edge
            int gp = mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * c->fs;
            int fp = c->cls ? gp : mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
            moy_ui_caption(s, (moy_ui_box_t){(int16_t)(b.x + fp - gp), b.y, b.w, (int16_t)caph},
                           label, c->fs, c->cls);
            fb.y = (int16_t)(b.y + caph);
            fb.h = (int16_t)(b.h - caph);
            lb.n = 0;
        }
        moy_ui_field(s, fb, lb, t, o->hint, f | (disabled ? MOY_UI_F_DISABLED : 0), c->fs,
                     c->cls, c->grp_open, c->grp_open && c->grp_first);
        mua_clip_out(&k);
    }
    return (int32_t)len | (done ? MOY_UI_FIELD_DONE : 0);
}

// The wash under a dialog or a modal popover: the surface, less the page's
// hint band where it stands at the surface's foot (DESIGN: a wash never
// covers a bar or the hint strip; the host's bars are drawn over it).
moy_ui_box_t moy_ui_api_wash_box(const moy_ui_ctx_t *c) {
    moy_ui_box_t sf = c->surface;
    moy_ui_box_t hb = c->hint_band;
    if (hb.h > 0 && hb.y + hb.h >= sf.y + sf.h && hb.y > sf.y) {
        sf.h = (int16_t)(hb.y - sf.y);
    }
    return sf;
}

void mua_wash(const moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_scrim(s, moy_ui_api_wash_box(c));
}

void moy_ui_api_wash(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    c->fl_dirty = 1;                    // over the page: the next frame draws it whole
    mua_wash(c, s);
}

int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (title.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 2) {
            int used = c->nest[c->n_nest - 1].used;
            int took = mua_nest_close(c, s, 2);
            if (took != used) {
                c->owe = 1;             // laid out for another body: the next frame redraws
            }
        }
        return -1;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
    }
    mua_group_close(c, s);
    // a dialog's rows frame (dialog_plan) draws it through its floor;
    // otherwise it is an overlay, drawn whole over the page
    int rows = c->fl_on && c->sh_dlg && c->fl_stage == 0;
    if (!rows) {
        c->fl_susp++;
    }
    c->fl_dirty = 1;
    c->has_at = 0;
    if (kind < 0 || kind >= 5) {
        kind = 1;
    }
    int fs = c->fs, rh = mua_row_h(c);
    uint32_t id = mua_hash(title.p, title.n, DIALOG_SALT);
    moy_ui_keep_t *kb = mua_keep_of(c, id);
    int body = kb != NULL && kb->b > 0 ? kb->b : rh;
    if (!c->modal) {
        c->owe = 1;                     // its first frame: focus opens in it on the next
    }
    int pad = moy_ui_modal_band(fs, c->cls);
    int nb = moy_ui_fields(buttons, len);
    int foot = nb ? rh + 2 * pad : pad;     // no buttons: the keys are the hint strip's
    int top = pad + rh + pad / 2;
    moy_ui_box_t sf = c->surface;
    int w = mua_pmetric(c, MOY_UI_M_VALUE_ROW_MODAL);
    if (w > sf.w - 2 * pad) {
        w = sf.w - 2 * pad;
    }
    int bh = body;
    if (top + bh + foot > sf.h - 2 * pad) {
        bh = sf.h - 2 * pad - top - foot;
        if (bh < rh) {
            bh = rh;
        }
    }
    int h = top + bh + foot;
    moy_ui_box_t card = {(int16_t)(sf.x + moy_ui_fdiv(sf.w - w, 2)),
                         (int16_t)(sf.y + moy_ui_fdiv(sf.h - h, 2)), (int16_t)w, (int16_t)h};
    if (card.y < sf.y) {
        card.y = sf.y;
    }
    uint32_t btn = mua_hash(buttons, len, id);
    c->dlg_held = (uint8_t)(o->wash == 0 && o->has_gen);
    c->dlg_gen = o->gen;
    c->dlg_btn = btn;
    if (rows) {
        c->fl_stage = 1;
        if (id != c->sh_id || !mua_same_box(card, c->dw_card) || !c->dlg_held || o->gen != c->dw_gen
            || btn != c->dw_btn) {
            c->fl_mute = 1;             // not the dialog planned: nothing this frame
            c->owe = 1;
        }
    }
    if (o->wash != 0 && !c->fl_mute) {
        mua_wash(c, s);                     // everything under a dialog dims (D-01)
    }
    clip_t mk;
    moy_ui_box_t cast = {card.x, card.y, card.w, (int16_t)(card.h + MOY_UI_DROP * fs)};
    EACH_PIECE(c, mk, s, cast) {
        moy_ui_modal(s, card, title, fs, c->cls, rh);
    }
    c->dlg_id = id;
    c->dlg_card = card;
    // the buttons at its foot, right-aligned, recorded left to right
    int gap = 8 * fs, bpad = (c->cls ? 16 : 12) * fs, minw = (c->cls ? 72 : 56) * fs;
    int bw[8];
    int total = 0;
    if (nb > 8) {
        nb = 8;
    }
    for (int i = 0; i < nb; i++) {
        size_t at = 0;
        int k = 0;
        moy_ui_span_t t = {buttons, 0};
        for (size_t j = 0; j <= len; j++) {
            if (j == len || buttons[j] == 0) {
                if (k == i) {
                    t.p = buttons + at;
                    t.n = (uint16_t)(j - at);
                    break;
                }
                at = j + 1;
                k++;
            }
        }
        int tw = moy_ui_role_w(s, MOY_UI_FONT_UI, t) + 2 * bpad;
        bw[i] = tw > minw ? tw : minw;
        total += bw[i] + (i ? gap : 0);
    }
    if (total > w - 2 * pad && nb > 0) {
        int each = (w - 2 * pad - (nb - 1) * gap) / nb;
        for (int i = 0; i < nb; i++) {
            bw[i] = each;
        }
        total = nb * each + (nb - 1) * gap;
    }
    // the body's page first, so its stops and the buttons' are the dialog's
    moy_ui_box_t inner = {0, 0, 0, 0};
    int lp = c->cls ? mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * fs
                    : mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    inner.x = (int16_t)(card.x + pad - lp);
    inner.w = (int16_t)(w - 2 * (pad - lp));
    inner.y = (int16_t)(card.y + top);
    inner.h = (int16_t)bh;
    c->dlg_foot = (int16_t)(inner.y + inner.h);
    mua_nest_open(c, 2, id, inner, body, 0, NULL);
    if (rows) {
        // the last frame's stops from its body's page stop on: the
        // buttons, then the body's
        int p0 = 0;
        while (p0 < c->n_prev && !((c->prev[p0].flags & STOP_PAGE) && c->prev[p0].id == id)) {
            p0++;
        }
        c->fl_p0 = (uint8_t)(p0 < c->n_prev ? p0 + 1 : c->n_prev);
        c->fl_pn = (uint8_t)(c->n_prev - c->fl_p0);
        c->fl_s0 = c->n_cur;
    }
    int pick = -1;
    moy_ui_over_t none;
    moy_ui_over_none(&none);
    uint8_t body_stop = c->page_stop;
    c->page_stop = NO_PAGE;             // a button is no row of the body's
    int x = card.x + w - pad - total;
    int by = card.y + h - pad - rh;
    for (int i = 0; i < nb; i++) {
        size_t at = 0;
        int k = 0;
        moy_ui_span_t t = {buttons, 0};
        for (size_t j = 0; j <= len; j++) {
            if (j == len || buttons[j] == 0) {
                if (k == i) {
                    t.p = buttons + at;
                    t.n = (uint16_t)(j - at);
                    break;
                }
                at = j + 1;
                k++;
            }
        }
        moy_ui_box_t bb = {(int16_t)x, (int16_t)by, (int16_t)bw[i], (int16_t)rh};
        int st = mua_stop(c, mua_hash(t.p, t.n, id), bb, STOP_DLG_BTN, t);
        if (st < 0) {
            st = 0;
        }
        if (st & MOY_UI_ST_ACTIVATED) {
            pick = i;
        }
        int bk = i == nb - 1 ? kind : 0;
        const char *kn = moy_ui_button_kinds[bk][0];
        moy_ui_button_t bt;
        bt.label = t;
        bt.glyph = -1;
        bt.fs = fs;
        bt.colors[0] = bt.colors[1] = bt.colors[2] = MOY_UI_FROM_STYLE;
        bt.app = (int16_t)(c->app - 1);
        bt.icon = -1;
        clip_t bk_;
        EACH_PIECE(c, bk_, s, bb) {
            moy_ui_button(s, bb, moy_ui_button_kind(kn, strlen(kn)), mua_mark_flags(st, &none),
                          mua_interact(st), &bt, MOY_UI_PHASE_ALL);
        }
        x += bw[i] + gap;
    }
    c->page_stop = body_stop;
    c->dlg_pick = (int8_t)pick;
    return pick;
}
