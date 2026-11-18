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
    if (!mua_armed(c) || c->fl.stage == 0) {
        return 1;
    }
    if (!mua_rc_meets(mua_rc_of(b), mua_rc_of(c->sh.r))) {
        return !(c->sh.dlg && c->fl.stage == 1);
    }
    return mua_floor_meets(c, b);
}

// The part open (moy_ui_api_table_part) closes: its ids no longer scoped.
static void part_out(moy_ui_ctx_t *c) {
    c->tp.open = 0;
    c->scope = 0;
}

// The parts of the table drawn last are done: the fades at the edges its
// rows run on past, over them.
void mua_parts_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    part_out(c);
    if (c->tp.id && c->tp.carry) {
        // the stops in the parts of the rows the frame carried, as the last
        // frame drew them, moved with their rows
        int dy = c->tp.y0 - c->tp.py0;
        for (int i = 0; i < c->n_prev && c->n_cur < MOY_UI_API_STOPS; i++) {
            const moy_ui_stop_t *q = &c->prev[i];
            int k = q->row - c->tp.v0;
            if (q->top != INT16_MIN || q->row < c->tp.v0 || q->row >= c->tp.e || k >= 64
                || (c->tp.ans >> k) & 1u) {
                continue;
            }
            moy_ui_stop_t *t = &c->cur[c->n_cur++];
            *t = *q;
            t->b.y = (int16_t)(t->b.y + dy);
            t->page = c->page_stop;
        }
    }
    if (c->tp.id && c->tp.fade) {
        mua_edge_fades(c, s, c->tp.view, c->tp.n * c->tp.rh, c->tp.off, MOY_UI_DEFAULT, 1, c->tp.rh);
    }
    c->tp.id = 0;
}

// -- blocks of fields an app sets through the engine (moy_ui_api_block_*) ------

// An instance's blocks are MOY_UI_BLOCKS of these (PSRAM on a board),
// taken at its first block set; its block_gen is one count for every block,
// so a block taken anew sets its fields past any generation a part kept.
struct mua_block {
    uint32_t id, gen, used;
    uint32_t v[MOY_UI_BLOCK_FIELDS];    // each field's value...
    uint32_t g[MOY_UI_BLOCK_FIELDS];    // ...and the generation that set it (0: unset)
};
typedef struct mua_block block_t;

// The block `id` names in instance I; with `make`, one taken for it (the
// free one, else the one set longest ago) where none holds it. NULL where
// the blocks' memory could not be had.
static block_t *block_of(moy_ui_inst_t *I, moy_ui_span_t id, int make) {
    if (I->blocks == NULL) {
        if (!make) {
            return NULL;
        }
        I->blocks = (block_t *)KEPT_ALLOC(MOY_UI_BLOCKS * sizeof(block_t));
        if (I->blocks == NULL) {
            return NULL;
        }
        memset(I->blocks, 0, MOY_UI_BLOCKS * sizeof(block_t));
    }
    uint32_t h = mua_hash(id.p, id.n, 0x626c6f63u) | 1u;
    block_t *take = NULL;
    for (int i = 0; i < MOY_UI_BLOCKS; i++) {
        block_t *b = &I->blocks[i];
        if (b->id == h) {
            b->used = ++I->block_tick;
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
    take->used = ++I->block_tick;
    return take;
}

int moy_ui_api_block_set(moy_ui_inst_t *I, moy_ui_span_t id, int field, uint32_t value) {
    if (field < 0 || field >= MOY_UI_BLOCK_FIELDS) {
        return 0;
    }
    if (I == NULL) {
        I = moy_ui_inst_main();
    }
    block_t *b = block_of(I, id, 1);
    if (b == NULL) {
        return 1;                       // (nothing kept: every set is a change)
    }
    if (b->g[field] != 0 && b->v[field] == value) {
        return 0;
    }
    b->v[field] = value;
    b->g[field] = b->gen = ++I->block_gen;
    return 1;
}

int32_t moy_ui_api_block_gen(moy_ui_inst_t *I, moy_ui_span_t id) {
    const block_t *b = block_of(I != NULL ? I : moy_ui_inst_main(), id, 0);
    return b == NULL ? 0 : (int32_t)b->gen;
}

uint32_t moy_ui_api_block_since(moy_ui_inst_t *I, moy_ui_span_t id, int32_t gen) {
    const block_t *b = block_of(I != NULL ? I : moy_ui_inst_main(), id, 0);
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

int32_t moy_ui_api_block_key(moy_ui_inst_t *I, moy_ui_span_t id) {
    const block_t *b = block_of(I != NULL ? I : moy_ui_inst_main(), id, 0);
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
    if (k == NULL || c->scroll != 0 || c->page_stop == NO_PAGE || c->sh.dlg) {
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
    k->scroll = to;
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
        uint32_t tid = mua_id_of(c, o, label);
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
    uint32_t id = mua_id_of(c, o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled || !placed ? NULL : mua_keep(c, id, MOY_UI_KEEP_PAGE);
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
    if (carry && st >= 0 && c->inst->audit_on) {
        // where its rows end is the carried rows' record's, not its own
        moy_ui_span_t nm = mua_name_of(o, label);
        c->cur[c->n_cur - 1].sig = mua_hash(nm.p, nm.n, (uint32_t)(c->cur[c->n_cur - 1].flags
                                                              & ~(STOP_AT_TOP | STOP_AT_END)));
        mua_sig_int(c, st);
    }
    if (st < 0) {
        st = 0;
    }
    int offset = kp != NULL ? kp->scroll : 0;
    // what the last frame drew, for a shift frame's floor
    int32_t was_drawn = kp != NULL ? kp->drawn : 0, was_b = kp != NULL ? kp->rows : 0;
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
        kp->rows = content;
        // a step or a tap moved it, else the app did (its own keys); a
        // table's first frame (no row height drawn yet) opens on its
        // current row, wherever the app put it
        int stepped = focused && ((xa ? c->step_x : c->step_y) || (st & MOY_UI_ST_ACTIVATED));
        int moved = !stepped && cur >= 0 && ((carry && cur != c->tc.cur) || kp->rh == 0);
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
                moy_ui_scroll_glide_to(&kp->sc, offset, to, 0, kp->rows, ext);
                moy_ui_scroll_tick(&kp->sc, (float)c->in[MOY_UI_IN_DT], &off, kp->rows, ext);
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
                    moy_ui_scroll_glide_to(&kp->sc, offset, near * rh, 1, kp->rows, ext);
                    moy_ui_scroll_tick(&kp->sc, (float)c->in[MOY_UI_IN_DT], &off, kp->rows, ext);
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
        kp->scroll = offset;
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
    c->tbl.y0 = (int16_t)(view.y - offset);
    c->tbl.rh = (int16_t)rh;
    c->tbl.n = (int16_t)count;
    c->tbl.view = view;
    c->tbl.px = c->tbl.pw = 0;
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
    int valid = kept && c->tc.id == id && c->tc.k >= 0 && c->tc.geo == geo
                && mua_same_box(c->tc.view, view) && c->tc.rh == rh && c->tc.n == count;
    int mark = (st & MOY_UI_ST_FOCUS) != 0 && o->mark != 0;
#define TC_CHANGED(i) (!valid || (i) < c->tc.r0 || (i) >= c->tc.r0 + c->tc.k \
                       || c->tc.h[(i) - c->tc.r0] != hv[(i) - vis0])
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
        if (c->inst->audit_on) {
            moy_ui_stop_t *vs = &c->cur[c->n_cur - 1];
            for (int i = c->n_cur - 1; i >= 0; i--) {
                if (c->cur[i].id == id && (c->cur[i].flags & STOP_PAGE)) {
                    vs = &c->cur[i];
                    break;
                }
            }
            vs->sig = carry ? 0 : mua_hash(cells, cells_n, (uint32_t)nc);
        }
        if (mua_armed(c) && c->fl.stage == 0 && id == c->sh.id) {
            // the view is R: its floor, from what the last frame drew
            table_r = 1;
            int early = c->fl.early;
            c->fl.stage = 1;
            if (!early) {
                c->fl.n = 0;
                c->fl.pend = 0;
            }
            c->fl.p0 = c->fl.pn = 0;
            c->fl.s0 = (uint8_t)c->n_cur;
            moy_ui_box_t r = c->sh.r;
            const moy_ui_stop_t *q = mua_table_of(c->prev, c->n_prev, id);
            int same = carry ? valid && (was_kd & MOY_UI_KD_GEN)
                             : o->has_gen && (was_kd & MOY_UI_KD_GEN) && o->gen == was_gen;
            if (q == NULL || !same || !mua_same_box(view, r) || offset - was_drawn != -c->sh.dy
                || was_b != count * rh || was_rh != rh) {
                c->fl.unlike = 1;
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
                if (c->inst->audit_on && !carry) {
                    const moy_ui_stop_t *pv = mua_page_of_keep(c->prev, c->n_prev, kp);
                    const moy_ui_stop_t *vs = mua_page_of_keep(c->cur, c->n_cur, kp);
                    if (pv == NULL || vs == NULL || pv->sig != vs->sig) {
                        c->inst->audit_fail++;     // rows unlike the last frame's under one gen
                    }
                }
            }
        } else if (xa && mua_armed(c) && c->fl.stage == 1 && !c->sh.dlg && !c->fl.mute
                   && mua_rc_meets(mua_rc_of(view), mua_rc_of(c->sh.r))) {
            mua_floor_add(c, mua_rc_of(view), 1);   // inside a page that moved or holds: whole
        } else if (carry && mua_armed(c) && c->fl.stage == 1 && !c->sh.dlg && !c->fl.mute
                   && mua_rc_meets(mua_rc_of(view), mua_rc_of(c->sh.r))) {
            // inside the R of a page that moved or holds: the rows whose
            // cells or marks changed join its floor, over the page's ground
            // the host moved its rows by the table's move (sh.in_dy)
            int inner = c->sh.in_id == id && offset - c->tc.off == -c->sh.in_dy;
            if (!valid || (offset != c->tc.off && !inner)) {
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
                if (cur != c->tc.cur || mark != c->tc.mark) {
                    for (int w = 0; w < 2; w++) {
                        int i = w ? cur : c->tc.cur;
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
    int keeping = part && placed && kp != NULL && !disabled && o->has_keep && c->kf.k > 0
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
        c->kf.box[0] = view;
        c->kf.n = (uint8_t)(view.w > 0 && view.h > 0);
        c->kf.i = c->kf.copy = c->kf.took = c->kf.ci = c->kf.sh = 0;
        c->kf.held = MOY_UI_KF_HELD;
    }
    int16_t tc_y0 = c->tc.y0;              // the last frame's, for the parts' stops
    if (carry) {
        c->tc.y0 = (int16_t)(view.y - offset);
        c->tc.id = id;
        c->tc.geo = geo;
        c->tc.view = view;
        c->tc.off = offset;
        c->tc.rh = (int16_t)rh;
        c->tc.n = (int16_t)count;
        c->tc.cur = (int16_t)cur;
        c->tc.mark = (uint8_t)mark;
        c->tc.r0 = (int16_t)vis0;
        c->tc.k = (int16_t)(kept ? nk : -1);
        if (kept) {
            memcpy(c->tc.h, hv, (size_t)nk * sizeof(hv[0]));
        }
    }
    clip_t k;
    mua_band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked);
        if (cap_h) {
            moy_ui_caption(s, (moy_ui_box_t){b.x, b.y, b.w, (int16_t)cap_h}, label, c->fs, c->cls);
        }
        // in a group, its rows are the group's: each on its card under a rule
        int grp = stacked && c->grp_open;
        moy_ui_box_t gcard = mua_card_of(c);
        if (g.pic && !part) {
            c->tbl.px = g.x[0];
            c->tbl.pw = g.w[0];
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
                                      i == 0 && c->grp_first, c->fs, mua_card_flags(c));
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
            if (!o->has_gen && !carry) {
                mua_thumb(c, s, view, count * rh, offset);   // as a region's (mua_nest_close)
            }
        }
        mua_clip_out(&kv);
        mua_clip_out(&k);
    }
    if (table_r) {
        mua_ground_due(c, s);
        if (c->fl.lost && c->inst->audit_on) {
            c->inst->audit_fail++;
        }
        c->fl.stage = 2;
        c->pic_tbl = 1;
    }
    if (part && !disabled) {
        // the parts the app draws next (moy_ui_api_table_part)
        rc_t pv = mua_rc_of(view);
        if (stacked) {
            mua_rc_cut(&pv, mua_rc_of(c->page));
        }
        c->tp.id = id;
        c->tp.view = mua_box_of(pv);
        c->tp.x1 = (uint8_t)xa;
        c->tp.x0 = (int16_t)(view.x + lead - offset);
        c->tp.spill = (int16_t)sp;
        c->tp.mid2 = xa ? 2 * view.x + view.w : 2 * view.y + view.h;
        c->tp.dist = 0;
        c->tp.y0 = (int16_t)(xa ? view.y : view.y + lead - offset);
        c->tp.rh = (int16_t)rh;
        c->tp.i = (int16_t)vis0;
        c->tp.e = (int16_t)vis1;
        c->tp.x = g.x[0];
        c->tp.w = g.w[0];
        c->tp.n = (int16_t)count;
        c->tp.off = offset;
        c->tp.v0 = (int16_t)vis0;
        c->tp.ans = 0;
        c->tp.py0 = tc_y0;
        c->tp.carry = (uint8_t)(carry && valid && mua_armed(c) && c->fl.stage != 0);
        c->tp.keep = (uint8_t)keeping;
        c->tp.np = (uint8_t)(o->has_across && nc < 256 ? nc : 1);
        // (rows side by side, and rows the app scrolls itself, fade at no edge)
        c->tp.fade = (uint8_t)(placed && !xa && !o->has_offset && pv.x0 < pv.x1 && pv.y0 < pv.y1);
        if (xa) {
            c->tp.w = (int16_t)(ph > 0 && ph < view.h ? ph : view.h);   // a row's height
        }
    }
    out[0] = cur;
    out[1] = opened;
}

int32_t moy_ui_api_table_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int32_t out[8]) {
    part_out(c);
    while (c->tp.id && c->tp.i < c->tp.e) {
        int i = c->tp.i++;
        int sp = c->tp.spill;
        int x = c->tp.x1 ? c->tp.x0 + i * c->tp.rh : c->tp.x;
        int y = c->tp.x1 ? c->tp.y0 : c->tp.y0 + i * c->tp.rh;
        int w = c->tp.x1 ? c->tp.rh : c->tp.w, h = c->tp.x1 ? c->tp.w : c->tp.rh;
        // what shows of the cell, and of the room past it a part may draw in
        rc_t q = {x - sp, y - sp, x + w + sp, y + h + sp};
        if (!c->tp.x1 && !sp) {
            q.x0 = c->tp.view.x;
            q.x1 = c->tp.view.x + c->tp.view.w;
        }
        if (!mua_rc_cut(&q, mua_rc_of(c->tp.view)) || !row_draws(c, mua_box_of(q))) {
            continue;
        }
        if (!c->tp.x1 && !sp) {
            q.x0 = x;
            q.x1 = x + w;
            if (!mua_rc_cut(&q, mua_rc_of(c->tp.view))) {
                continue;
            }
        }
        if (c->tp.keep) {
            // a kept table's part draws inside the floor rect it meets (one:
            // the floor's rects lie further apart than a part's reach)
            int k = 0;
            while (k < c->kf.n && !mua_rc_meets(q, mua_rc_of(c->kf.box[k]))) {
                k++;
            }
            if (k == c->kf.n || (c->kf.took & (1u << k)) || !mua_rc_cut(&q, mua_rc_of(c->kf.box[k]))) {
                continue;
            }
        }
        int along = c->tp.x1 ? 2 * x + w : 2 * y + h;
        c->tp.dist = (int32_t)(((int64_t)(along - c->tp.mid2) * 128) / (c->tp.rh > 0 ? c->tp.rh : 1));
        c->tp.open = 1;
        c->tp.row = (int16_t)i;
        if (i - c->tp.v0 < 64) {
            c->tp.ans |= (uint64_t)1 << (i - c->tp.v0);
        }
        c->scope = mua_hash((const uint8_t *)&i, sizeof(i), c->tp.id);
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
    return c->tp.open ? c->tp.dist : 0;
}

// -- a field's edit ------------------------------------------------------------------
//
// The text a field held when focus came onto it, or as Enter last left it,
// under its context and id in its instance (moy_ui_inst's `edits`, taken at
// the first edit): MOY_UI_API_EDITS at once, a new edit taking the place of
// the one used least recently where every place is taken. The field's
// record (moy_ui_keep_t, MOY_UI_KEEP_FIELD) says whether its edit is in
// progress and carries a cancel.

typedef struct mua_edit {
    const void *ctx;                // NULL: free
    uint32_t id, seq;
    uint16_t n;
    uint8_t buf[MOY_UI_API_UNDO];
} mua_edit_t;

static mua_edit_t *edit_of(moy_ui_inst_t *I, const moy_ui_ctx_t *c, uint32_t id, int make) {
    mua_edit_t *e = I->edits, *pick = NULL;
    for (int i = 0; e != NULL && i < MOY_UI_API_EDITS; i++) {
        if (e[i].ctx == c && e[i].id == id) {
            e[i].seq = ++I->edit_seq;
            return &e[i];
        }
        if (pick == NULL || (pick->ctx != NULL && (e[i].ctx == NULL || e[i].seq < pick->seq))) {
            pick = &e[i];
        }
    }
    if (!make) {
        return NULL;
    }
    if (e == NULL) {
        e = I->edits = (mua_edit_t *)KEPT_ALLOC(MOY_UI_API_EDITS * sizeof(mua_edit_t));
        if (e == NULL) {
            return NULL;
        }
        memset(e, 0, MOY_UI_API_EDITS * sizeof(mua_edit_t));
        pick = &e[0];
    }
    pick->ctx = c;
    pick->id = id;
    pick->n = 0;
    pick->seq = ++I->edit_seq;
    return pick;
}

void mua_edits_drop(moy_ui_inst_t *I, const moy_ui_ctx_t *c, uint32_t id) {
    mua_edit_t *e = I->edits;
    for (int i = 0; e != NULL && i < MOY_UI_API_EDITS; i++) {
        if (e[i].ctx == c && (id == 0 || e[i].id == id)) {
            e[i].ctx = NULL;
        }
    }
}

int32_t moy_ui_api_field(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                         uint8_t *buf, size_t cap, size_t len, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (len > cap) {
        len = cap;
        while (len > 0 && (buf[len] & 0xC0) == 0x80) {
            len--;                  // never half a character
        }
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
    int st = disabled ? 0
                      : mua_stop(c, mua_id_of(c, o, label), b, STOP_FIELD, mua_name_of(o, label));
    int at = !disabled && st >= 0 ? c->n_cur - 1 : -1;
    if (st < 0) {
        st = 0;
    }
    int done = 0;
    uint32_t id = at >= 0 ? c->cur[at].id : 0;
    moy_ui_inst_t *I = c->inst;
    // its record: made when focus comes onto it, kept while its edit is in
    // progress or it holds focus
    int focused = (st & MOY_UI_ST_FOCUS) && id != 0 && !c->elsewhere;
    moy_ui_keep_t *fk = id != 0 ? mua_keep(c, id, focused ? MOY_UI_KEEP_FIELD : 0) : NULL;
    if (fk != NULL && fk->kind != MOY_UI_KEEP_FIELD) {
        fk = NULL;
    } else if (fk != NULL && !focused && fk->cancel == 1) {
        fk->cancel = 0;                     // a cancel its focus left before it was given
    }
    mua_edit_t *e = fk != NULL ? edit_of(I, c, id, 0) : NULL;
    if (fk != NULL && fk->cancel == 2) {
        if (e != NULL && e->n <= cap) {
            memcpy(buf, e->buf, e->n);      // a cancel: the text the edit began from
            len = e->n;
        }
        fk->cancel = 0;
    }
    if (focused && fk != NULL && e == NULL && len <= MOY_UI_API_UNDO) {
        e = edit_of(I, c, id, 1);           // focus came onto it: an edit begins here
        if (e != NULL) {
            memcpy(e->buf, buf, len);
            e->n = (uint16_t)len;
        }
    }
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
    if ((st & MOY_UI_ST_FOCUS) && c->paste_n) {
        // the console's paste: what the room holds, whole characters
        size_t n = c->paste_n;
        c->paste_n = 0;
        if (n > cap - len) {
            n = cap - len;
            while (n > 0 && (c->paste[n] & 0xC0) == 0x80) {
                n--;
            }
        }
        memcpy(buf + len, c->paste, n);
        len += n;
    }
    if ((st & MOY_UI_ST_ACTIVATED) && c->tap_y == INT16_MIN) {
        done = 1;
    }
    if (fk != NULL) {
        if (e != NULL && done && len <= sizeof e->buf) {
            memcpy(e->buf, buf, len);       // Enter keeps what it confirmed
            e->n = (uint16_t)len;
        }
        fk->live = (uint8_t)(e != NULL && (len != e->n || memcmp(buf, e->buf, len) != 0));
        if (!fk->live && !focused) {
            mua_edits_drop(I, c, id);       // focus left it with nothing to give back
            fk->id = 0;
        }
    }
    if (at >= 0) {
        // a field's stop keeps its text's hash in its gen: text that changed
        // with no key typed into it (the app's own) draws its row again
        int32_t h = (int32_t)mua_hash(buf, len, 0x51ed270bu);
        c->cur[at].gen = h;
        int j = at - c->fl.s0;
        const moy_ui_stop_t *q = mua_armed(c) && c->fl.stage == 1 && !c->fl.unlike && !c->fl.mute
                                 && j >= 0 && j < c->fl.pn ? &c->prev[c->fl.p0 + j] : NULL;
        if (q != NULL && q->id == c->cur[at].id && q->gen != h && !mua_floor_holds(c, b)) {
            if (c->sh.dlg) {
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
        mua_band(c, s, b, stacked);
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
        int edge = o->border >= MOY_UI_O_ROLE ? moy_ui_token(s->look, o->border - MOY_UI_O_ROLE)
                                              : -1;
        moy_ui_field(s, fb, lb, t, o->hint, f | (disabled ? MOY_UI_F_DISABLED : 0), c->fs,
                     c->cls, c->grp_open, c->grp_open && c->grp_first, edge);
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
    c->fl.dirty = 1;                    // over the page: the next frame draws it whole
    mua_wash(c, s);
}

// A dialog's card `w` wide, its title band `top` high and its foot `foot`,
// for a body `body` high: centred on the surface, the body cut to the room
// the surface leaves it (never under one row), its height in *bh.
static moy_ui_box_t dlg_card(const moy_ui_ctx_t *c, int w, int top, int foot, int body,
                             int *bh_out) {
    moy_ui_box_t sf = c->surface;
    int pad = moy_ui_modal_band(c->fs, c->cls), rh = mua_row_h(c);
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
    *bh_out = bh;
    return card;
}

// A dialog's first frame drawn tall (mua_lend) has closed its body, `took`
// high: the card moves to its place, its title band and body where they
// stand in it, its foot (the buttons) as far up from its foot, and the
// stops they recorded move with them.
static void dlg_land(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int took) {
    moy_ui_box_t t = c->dlg.card;
    int rh = mua_row_h(c), pad = moy_ui_modal_band(c->fs, c->cls), bh;
    int top = pad + rh + pad / 2, foot = t.y + t.h - c->dlg.foot;
    moy_ui_box_t card = dlg_card(c, t.w, top, foot, took > 0 ? took : rh, &bh);
    mua_lend_put(c, s, card, top + bh, 0, 1);
    mua_lend_stops(c, card.y - t.y, card.y + card.h - (t.y + t.h));
    for (int i = 0; i < c->n_cur; i++) {
        if (c->cur[i].id == c->dlg.id && (c->cur[i].flags & STOP_PAGE)) {
            c->cur[i].b.h = (int16_t)bh;        // the body as it shows
        }
    }
    c->dlg.card = card;
    c->dlg.foot = (int16_t)(card.y + top + bh);
    mua_lend_end(c);
}

int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (title.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 2) {
            int used = c->nest[c->n_nest - 1].used;
            int took = mua_nest_close(c, s, 2);
            if (mua_lent_of(c, 2) != NULL) {
                dlg_land(c, s, took);
            } else if (took != used) {
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
    int rows = c->fl.on && c->sh.dlg && c->fl.stage == 0;
    if (!rows) {
        c->fl.susp++;
    }
    c->fl.dirty = 1;
    c->has_at = 0;
    if (kind < 0 || kind >= 5) {
        kind = 1;
    }
    int fs = c->fs, rh = mua_row_h(c);
    uint32_t id = mua_hash(title.p, title.n, DIALOG_SALT);
    moy_ui_keep_t *kb = mua_keep_of(c, id);
    int body = kb != NULL && kb->rows > 0 ? kb->rows : rh;
    if (!c->modal) {
        c->owe = 1;                     // its first frame: focus opens in it on the next
    }
    int pad = moy_ui_modal_band(fs, c->cls);
    int nb = moy_ui_fields(buttons, len);
    // the foot pads the buttons' focus ring as the top pads the title
    int reach = moy_ui_mark_reach(MOY_UI_ITEM_CELL) * fs;
    int foot = nb ? rh + 2 * pad + reach : pad;     // no buttons: the keys are the hint strip's
    int top = pad + rh + pad / 2;
    moy_ui_box_t sf = c->surface;
    int w = mua_pmetric(c, MOY_UI_M_VALUE_ROW_MODAL);
    if (w > sf.w - 2 * pad) {
        w = sf.w - 2 * pad;
    }
    int bh;
    moy_ui_box_t card = dlg_card(c, w, top, foot, body, &bh);
    // no size kept for it yet (its first frame): drawn as tall as the
    // surface lets it, its body laid out in that room, and moved to its
    // place once the body closes (dlg_land); where the canvas cannot lend
    // that, it shows at a one-row body and the next frame at its size
    int lent = 0;
    if (kb == NULL && !rows) {
        int tbh;
        moy_ui_box_t t = dlg_card(c, w, top, foot, sf.h, &tbh);
        if (mua_lend(c, s, t, 2)) {
            lent = 1;
            card = t;
            bh = tbh;
        }
    }
    int h = card.h;
    uint32_t btn = mua_hash(buttons, len, id);
    c->dlg.held = (uint8_t)(o->wash == 0 && o->has_gen);
    c->dlg.gen = o->gen;
    c->dlg.btn = btn;
    if (rows) {
        c->fl.stage = 1;
        if (id != c->sh.id || !mua_same_box(card, c->dw.card) || !c->dlg.held || o->gen != c->dw.gen
            || btn != c->dw.btn) {
            c->fl.mute = 1;             // not the dialog planned: nothing this frame
            c->owe = 1;
        }
    }
    if (o->wash != 0 && !c->fl.mute) {
        mua_wash(c, s);                     // everything under a dialog dims (D-01)
    }
    if (lent) {
        mua_lend_keep(c, s);
    }
    clip_t mk;
    moy_ui_box_t cast = {card.x, card.y, card.w, (int16_t)(card.h + MOY_UI_DROP * fs)};
    EACH_PIECE(c, mk, s, cast) {
        moy_ui_modal(s, card, title, fs, c->cls, rh);
    }
    c->dlg.id = id;
    c->dlg.card = card;
    // the buttons at its foot, right-aligned, recorded left to right, a
    // focus ring's reach and 4 pixels clear between them
    int gap = (reach / fs + 4 > 8 ? reach / fs + 4 : 8) * fs, bpad = (c->cls ? 16 : 12) * fs, minw = (c->cls ? 72 : 56) * fs;
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
    c->dlg.foot = (int16_t)(inner.y + inner.h);
    mua_nest_open(c, 2, id, inner, body, 0, NULL);
    if (rows) {
        // the last frame's stops from its body's page stop on: the
        // buttons, then the body's
        int p0 = 0;
        while (p0 < c->n_prev && !((c->prev[p0].flags & STOP_PAGE) && c->prev[p0].id == id)) {
            p0++;
        }
        c->fl.p0 = (uint8_t)(p0 < c->n_prev ? p0 + 1 : c->n_prev);
        c->fl.pn = (uint8_t)(c->n_prev - c->fl.p0);
        c->fl.s0 = c->n_cur;
    }
    int pick = -1;
    moy_ui_over_t none;
    moy_ui_over_none(&none);
    uint8_t body_stop = c->page_stop;
    c->page_stop = NO_PAGE;             // a button is no row of the body's
    int x = card.x + w - pad - total;
    int by = card.y + h - pad - reach - rh;
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
        // `o.disabled`: the last button (the dialog's verb) is off, no stop
        int off = o->disabled == 1 && i == nb - 1;
        uint32_t bid = mua_hash(t.p, t.n, id);
        if (!off && o->focus.p != NULL && o->focus.n == t.n && memcmp(o->focus.p, t.p, t.n) == 0) {
            c->dlg.open = bid;          // focus opens here when the dialog opens
        }
        int st = off ? 0 : mua_stop(c, bid, bb, STOP_DLG_BTN, t);
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
            moy_ui_button(s, bb, moy_ui_button_kind(kn, strlen(kn)),
                          mua_mark_flags(st, &none) | (off ? MOY_UI_F_DISABLED : 0),
                          mua_interact(st), &bt, MOY_UI_PHASE_ALL);
        }
        x += bw[i] + gap;
    }
    c->page_stop = body_stop;
    c->dlg.pick = (int8_t)pick;
    return pick;
}
