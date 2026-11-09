// The public UI API's engine (moy_ui_api.h; docs/ui_api_2026-10.md §2).

#include "moy_ui_api_int.h"

// The main instance (moy_ui_api.h): the shell's, its Python apps' and the
// console's run's.
static moy_ui_inst_t s_main;

moy_ui_inst_t *moy_ui_inst_main(void) {
    return &s_main;
}

size_t moy_ui_inst_size(void) {
    return sizeof(moy_ui_inst_t);
}

void moy_ui_inst_init(moy_ui_inst_t *I) {
    memset(I, 0, sizeof(*I));
}

void moy_ui_inst_free(moy_ui_inst_t *I) {
    KEPT_FREE(I->kept);
    KEPT_FREE(I->text);
    KEPT_FREE(I->blocks);
    moy_ui_inst_init(I);
}

uint32_t mua_hash(const uint8_t *p, size_t n, uint32_t salt) {
    uint32_t h = 2166136261u ^ salt;
    for (size_t i = 0; i < n; i++) {
        h = (h ^ p[i]) * 16777619u;
    }
    return h ? h : 1u;
}

int mua_kmetric(int kind, int slot) {
    int n = 0;
    const int16_t *m = moy_ui_metrics(kind, &n);
    return m != NULL && slot < n ? m[slot] : 0;
}

static int metric(int slot) {
    return mua_kmetric(MOY_UI_K_ROW, slot);
}

int mua_row_h(const moy_ui_ctx_t *c) {
    return metric(c->cls ? MOY_UI_M_ROW_STACK_DESK : MOY_UI_M_ROW_STACK) * c->fs;
}

int mua_inside(moy_ui_box_t b, int x, int y) {
    return x >= b.x && y >= b.y && x < b.x + b.w && y < b.y + b.h;
}

static void name_copy(char *dst, moy_ui_span_t t) {
    size_t n = t.n < MOY_UI_API_NAME - 1 ? t.n : MOY_UI_API_NAME - 1;
    if (t.p != NULL && n) {
        memcpy(dst, t.p, n);
    }
    dst[n] = 0;
}

void moy_ui_over_none(moy_ui_over_t *o) {
    o->fill = o->ink = o->border = MOY_UI_O_NONE;
    o->id.p = NULL;
    o->id.n = 0;
    o->current = o->disabled = o->nav = o->mark = o->steps = -1;
    o->hint.p = NULL;
    o->hint.n = 0;
    o->wash = o->lead = -1;
    o->has_gen = 0;
    o->gen = 0;
    o->has_part = 0;
    o->part_w = o->part_h = 0;
    o->bar = -1;
    o->axis_x = o->snap = o->has_offset = 0;
    o->offset = 0;
    o->spill = 0;
    o->has_keep = o->whole = 0;
    o->keep = 0;
    o->has_across = 0;
    o->across_first = o->across_pitch = o->at = 0;
    o->over_x = o->over_y = o->over_w = o->over_h = 0;
    o->shift = 0;
    o->fade = 0;
}

int32_t mua_word(const uint8_t *w, int i) {
    int32_t v;
    memcpy(&v, w + 4 * i, 4);      // the app's block need not be aligned
    return v;
}

static int8_t flag(int32_t v) {
    return v < 0 ? -1 : (int8_t)(v != 0);
}

int moy_ui_over_block(moy_ui_over_t *o, const uint8_t *w, size_t n) {
    moy_ui_over_none(o);
    if (w == NULL || n == 0) {
        return MOY_UI_OK;
    }
    if (n < 4u * MOY_UI_O_WORDS) {
        return MOY_UI_BAD;
    }
    o->fill = mua_word(w, MOY_UI_O_FILL);
    o->ink = mua_word(w, MOY_UI_O_INK);
    o->border = mua_word(w, MOY_UI_O_BORDER);
    int32_t idn = mua_word(w, MOY_UI_O_ID);
    size_t at = 4u * MOY_UI_O_WORDS;
    if (idn >= 0) {
        if ((size_t)idn > n - at || idn > 0xffff) {
            return MOY_UI_BAD;
        }
        o->id.p = w + at;
        o->id.n = (uint16_t)idn;
        at += (size_t)idn;
    }
    // the block may go on: the hint's length as a word, then its text
    if (n - at >= 4u) {
        int32_t hn;
        memcpy(&hn, w + at, 4);
        at += 4u;
        if (hn >= 0) {
            if ((size_t)hn > n - at || hn > 0xffff) {
                return MOY_UI_BAD;
            }
            o->hint.p = w + at;
            o->hint.n = (uint16_t)hn;
            at += (size_t)hn;
        }
        // then a word of flags, and the gen's word where they say so
        if (n - at >= 4u) {
            int32_t fl;
            memcpy(&fl, w + at, 4);
            at += 4u;
            if (fl >= 0) {
                o->wash = (int8_t)((fl & MOY_UI_O_NO_WASH) ? -1 : (fl & MOY_UI_O_WASH) != 0);
                o->lead = (int8_t)((fl & MOY_UI_O_LEAD) != 0);
                if ((fl & MOY_UI_O_GEN) && n - at >= 4u) {
                    memcpy(&o->gen, w + at, 4);
                    o->has_gen = 1;
                    at += 4u;
                }
                if ((fl & MOY_UI_O_PART) && n - at >= 8u) {
                    o->has_part = 1;
                    o->part_w = (int16_t)mua_word(w + at, 0);
                    o->part_h = (int16_t)mua_word(w + at, 1);
                    at += 8u;
                }
                o->bar = (int8_t)((fl & MOY_UI_O_BAR) != 0 ? 1 : -1);
                o->axis_x = (int8_t)((fl & MOY_UI_O_AXIS_X) != 0);
                o->snap = (int8_t)((fl & MOY_UI_O_SNAP) != 0);
                if ((fl & MOY_UI_O_OFFSET) && n - at >= 4u) {
                    o->has_offset = 1;
                    o->offset = mua_word(w + at, 0);
                    at += 4u;
                }
                if ((fl & MOY_UI_O_SPILL) && n - at >= 4u) {
                    int32_t sp = mua_word(w + at, 0);
                    o->spill = (int16_t)(sp < 0 ? 0 : sp > 4096 ? 4096 : sp);
                    at += 4u;
                }
                o->whole = (int8_t)((fl & MOY_UI_O_WHOLE) != 0);
                if ((fl & MOY_UI_O_KEEP) && n - at >= 4u) {
                    o->has_keep = 1;
                    o->keep = mua_word(w + at, 0);
                    at += 4u;
                }
                if ((fl & MOY_UI_O_OVER) && n - at >= 16u) {
                    int32_t ow = mua_word(w + at, 2), oh = mua_word(w + at, 3);
                    if (ow > 0 && oh > 0) {
                        o->over_x = (int16_t)mua_word(w + at, 0);
                        o->over_y = (int16_t)mua_word(w + at, 1);
                        o->over_w = (int16_t)(ow > 0x7fff ? 0x7fff : ow);
                        o->over_h = (int16_t)(oh > 0x7fff ? 0x7fff : oh);
                    }
                    at += 16u;
                }
                if ((fl & MOY_UI_O_ACROSS) && n - at >= 8u) {
                    o->has_across = 1;
                    o->across_first = (int16_t)mua_word(w + at, 0);
                    o->across_pitch = (int16_t)mua_word(w + at, 1);
                    at += 8u;
                }
                if ((fl & MOY_UI_O_AT) && n - at >= 4u) {
                    o->at = (int16_t)mua_word(w + at, 0);
                    at += 4u;
                }
                o->shift = (int8_t)((fl & MOY_UI_O_SHIFT) != 0);
                if ((fl & MOY_UI_O_FADE) && n - at >= 4u) {
                    int32_t fh = mua_word(w + at, 0);
                    o->fade = (int16_t)(fh < 0 ? 0 : fh > 4096 ? 4096 : fh);
                    at += 4u;
                }
            }
        }
    }
    o->current = flag(mua_word(w, MOY_UI_O_CURRENT));
    o->disabled = flag(mua_word(w, MOY_UI_O_DISABLED));
    o->nav = flag(mua_word(w, MOY_UI_O_NAV));
    o->mark = flag(mua_word(w, MOY_UI_O_MARK));
    o->steps = flag(mua_word(w, MOY_UI_O_STEPS));
    return MOY_UI_OK;
}

const moy_ui_over_t *mua_over(const moy_ui_over_t *o) {
    static const moy_ui_over_t none = {MOY_UI_O_NONE, MOY_UI_O_NONE, MOY_UI_O_NONE, {NULL, 0},
                                       -1, -1, -1, -1, -1, {NULL, 0}, -1, -1, 0, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0,
                                       0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
    return o != NULL ? o : &none;
}

// An override colour as the primitives take it: a role is the look's token,
// a raw colour its nearest MOY64 index.
static int16_t colour(int look, int32_t v) {
    if (v < 0) {
        return MOY_UI_FROM_STYLE;
    }
    if (v >= MOY_UI_O_ROLE) {
        int t = moy_ui_token(look, v - MOY_UI_O_ROLE);
        return t < 0 ? MOY_UI_FROM_STYLE : (int16_t)t;
    }
    return (int16_t)moy_theme_nearest(MOY_THEME_RGB | (v & 0xffffff));
}

static void colours(const moy_ui_sink_t *s, const moy_ui_over_t *o, int16_t out[3]) {
    out[0] = colour(s->look, o->fill);
    out[1] = colour(s->look, o->ink);
    out[2] = colour(s->look, o->border);
}

size_t moy_ui_api_size(void) {
    return sizeof(moy_ui_ctx_t);
}

void moy_ui_api_reset_in(moy_ui_ctx_t *c, moy_ui_inst_t *I) {
    if (I == NULL) {
        I = &s_main;
    }
    c->inst = I;
    moy_ui_text_views_drop(I, c);
    mua_kept_drop(c);
    memset(c, 0, sizeof(*c));
    c->magic = MAGIC;
    c->inst = I;
    c->fs = 1;
    c->page_keep = -1;
    c->page_stop = NO_PAGE;
    c->icon_slot = c->icon_glyph = c->row_slot = c->row_glyph = -1;
    c->dlg_pick = -1;
    c->bar_act = -1;
    c->bar_chip_at = -1;
}

void moy_ui_api_reset(moy_ui_ctx_t *c) {
    moy_ui_api_reset_in(c, c->magic == MAGIC ? c->inst : NULL);
}

moy_ui_ctx_t *moy_ui_api_ctx_in(moy_ui_inst_t *I, void *p, size_t n) {
    if (p == NULL || n < sizeof(moy_ui_ctx_t) || ((uintptr_t)p & 3u) != 0u) {
        return NULL;
    }
    moy_ui_ctx_t *c = (moy_ui_ctx_t *)p;
    if (c->magic != MAGIC) {
        moy_ui_api_reset_in(c, I);
    }
    return c;
}

moy_ui_ctx_t *moy_ui_api_ctx(void *p, size_t n) {
    return moy_ui_api_ctx_in(NULL, p, n);
}

// -- the previous frame's stops ------------------------------------------------------

int mua_find(const moy_ui_stop_t *v, int n, uint32_t id) {
    for (int i = 0; i < n; i++) {
        if (v[i].id == id && !(v[i].flags & STOP_PAGE)) {
            return i;
        }
    }
    return -1;
}

// The stop under (x, y), the last drawn winning (it is on top).
static int stop_at(const moy_ui_stop_t *v, int n, int x, int y) {
    for (int i = n - 1; i >= 0; i--) {
        if (!(v[i].flags & STOP_PAGE) && mua_inside(v[i].b, x, y)) {
            return i;
        }
    }
    return -1;
}

int mua_page_at(const moy_ui_stop_t *v, int n, int x, int y) {
    for (int i = n - 1; i >= 0; i--) {
        if ((v[i].flags & STOP_PAGE) && mua_inside(v[i].b, x, y)) {
            return i;
        }
    }
    return -1;
}

// The topmost page at (x, y) that scrolls across (`across`), or down: its
// stop's index, or -1.
static int page_on(moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n, int x, int y, int across) {
    for (int i = n - 1; i >= 0; i--) {
        if ((v[i].flags & STOP_PAGE) && mua_inside(v[i].b, x, y)) {
            const moy_ui_keep_t *k = mua_keep_of(c, v[i].id);
            if ((k != NULL && (k->kd & MOY_UI_KD_X)) == (across != 0)) {
                return i;
            }
        }
    }
    return -1;
}

int mua_gap(int a0, int a1, int b0, int b1) {
    int lo = a0 > b0 ? a0 : b0, hi = a1 < b1 ? a1 : b1;
    return lo < hi ? 0 : lo - hi;
}

// The nearest stop from `f` in direction `dir` (an arrow's bit): the least
// gap along the arrow, then across it, then between the centres.
static int nearest(const moy_ui_stop_t *v, int n, const moy_ui_stop_t *f, int dir) {
    moy_ui_box_t a = f->b;
    int acx = 2 * a.x + a.w, acy = 2 * a.y + a.h;
    int best = -1;
    int32_t score = INT32_MAX;
    for (int i = 0; i < n; i++) {
        const moy_ui_stop_t *s = &v[i];
        if ((s->flags & STOP_PAGE) || s->id == f->id) {
            continue;
        }
        moy_ui_box_t b = s->b;
        int cx = 2 * b.x + b.w, cy = 2 * b.y + b.h;
        int along, across, off;
        if (dir == MOY_UI_B_DOWN || dir == MOY_UI_B_UP) {
            if (dir == MOY_UI_B_DOWN ? cy <= acy : cy >= acy) {
                continue;
            }
            along = dir == MOY_UI_B_DOWN ? b.y - (a.y + a.h) : a.y - (b.y + b.h);
            across = mua_gap(a.x, a.x + a.w, b.x, b.x + b.w);
            off = cx > acx ? cx - acx : acx - cx;
        } else {
            if (dir == MOY_UI_B_RIGHT ? cx <= acx : cx >= acx) {
                continue;
            }
            along = dir == MOY_UI_B_RIGHT ? b.x - (a.x + a.w) : a.x - (b.x + b.w);
            across = mua_gap(a.y, a.y + a.h, b.y, b.y + b.h);
            off = cy > acy ? cy - acy : acy - cy;
        }
        if (along < 0) {
            along = 0;
        }
        int32_t sc = (int32_t)along * 1024 + (int32_t)across * 2048 + off;
        if (across > 0) {
            sc += 1 << 24;          // a stop in line comes before one off to the side
        }
        if (sc < score) {
            score = sc;
            best = i;
        }
    }
    return best;
}

// The next or previous stop in call order, wrapping.
static int step_order(const moy_ui_stop_t *v, int n, int from, int back) {
    for (int k = 1; k <= n; k++) {
        int i = ((from + (back ? -k : k)) % n + n) % n;
        if (!(v[i].flags & STOP_PAGE)) {
            return i;
        }
    }
    return -1;
}

// -- kept state ----------------------------------------------------------------------

moy_ui_keep_t *mua_keep(moy_ui_ctx_t *c, uint32_t id, int make) {
    moy_ui_keep_t *free_ = NULL;
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        moy_ui_keep_t *k = &c->keep[i];
        if (k->id == id) {
            k->seen = 1;
            return k;
        }
        if (k->id == 0 && free_ == NULL) {
            free_ = k;
        }
    }
    if (!make || free_ == NULL) {
        return NULL;
    }
    free_->id = id;
    free_->a = free_->b = 0;
    free_->over = free_->foot = free_->peek = free_->rh = 0;
    free_->kd = 0;
    free_->seen = 1;
    memset(&free_->sc, 0, sizeof(free_->sc));
    return free_;
}

moy_ui_keep_t *mua_keep_of(moy_ui_ctx_t *c, uint32_t id) {
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        if (c->keep[i].id == id) {
            return &c->keep[i];
        }
    }
    return NULL;
}

// Scroll the page `p` stop holds so box `b` (as drawn last frame) shows.
// Scroll page p (stop index `page`) to show box b, keeping each group
// caption on it wholly in view or wholly out: a scroll down that would cut
// one at the page's top goes on past it, a scroll up brings it in, and a
// group's first row brings its caption. Where rows run on below what shows,
// the page's foot fades (mua_foot_fade): the box stands above that fade, as far
// as the rows go on.
// The span page `p`'s scroll ranges over: its rows' height (and the room a
// scroll into view past its rows keeps) and what shows of them, the page's
// stop, which a hint band at its foot has already given up (hints).
static void span_of(const moy_ui_stop_t *p, const moy_ui_keep_t *k, int32_t *content,
                    int32_t *extent) {
    *content = k->b + (k->over > 0 ? k->over : 0);
    *extent = (k->kd & MOY_UI_KD_X) ? p->b.w : p->b.h;
}

// The page stop kept as `k` among the frame's stops, or NULL.
const moy_ui_stop_t *mua_page_of_keep(const moy_ui_stop_t *v, int n,
                                      const moy_ui_keep_t *k) {
    for (int i = 0; i < n; i++) {
        if (v[i].id == k->id && (v[i].flags & STOP_PAGE)) {
            return &v[i];
        }
    }
    return NULL;
}

// The row a placed table's key step lands on (`t` the table's stop as the
// last frame drew it), its rows' scroll aimed there at begin as the table's
// pass would aim it, so the frame's plan knows the move.
static void table_reveal(moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n,
                         const moy_ui_stop_t *t) {
    moy_ui_keep_t *k = mua_keep_of(c, t->id);
    if (k == NULL || !(k->kd & MOY_UI_KD_TABLE) || k->rh <= 0) {
        return;
    }
    const moy_ui_stop_t *p = mua_page_of_keep(v, n, k);
    if (p == NULL) {
        return;
    }
    int rh = k->rh, count = k->b / rh;
    int cur = t->row + c->step_y;
    cur = cur >= count ? count - 1 : cur;
    cur = cur < 0 ? 0 : cur;
    int glide = (k->sc.flags & MOY_UI_SCROLL_GLIDE) != 0;
    int to = glide ? k->sc.target : k->a;
    int was = to;
    if (cur * rh < to) {
        to = cur * rh;
    } else if ((cur + 1) * rh > to + p->b.h) {
        to = (cur + 1) * rh - p->b.h;
    }
    if (to == was) {
        return;                         // the motion under way, if any, steps with every page's
    }
    int32_t offset = k->a;
    if (c->in[MOY_UI_IN_DT] <= 0) {
        int most = k->b - p->b.h;
        to = to > most ? most : to;
        k->a = to < 0 ? 0 : to;
        moy_ui_scroll_land(&k->sc, &offset);
        return;
    }
    if (moy_ui_scroll_moving(&k->sc)) {
        // the motion under way takes this frame's step first, as every
        // page's does; the new glide takes its own with them
        moy_ui_scroll_tick(&k->sc, (float)c->in[MOY_UI_IN_DT], &offset, k->b, p->b.h);
        k->a = offset;
    }
    moy_ui_scroll_glide_to(&k->sc, offset, to, 0, k->b, p->b.h);
}

// Glide page `p`'s scroll to `to`: 1 where that moves it.
int mua_aim(moy_ui_keep_t *k, const moy_ui_stop_t *p, int32_t to) {
    int32_t content, extent;
    span_of(p, k, &content, &extent);
    moy_ui_scroll_glide_to(&k->sc, k->a, to, 0, content, extent);
    return moy_ui_scroll_moving(&k->sc);
}

// Scroll page `p` so box `b` (as drawn last frame) shows where the page's
// scroll is going (its glide's target while one is live): the glide aims
// there (aim). 1 where the target moved.
static int reveal(moy_ui_ctx_t *c, const moy_ui_stop_t *p, int page, moy_ui_box_t b) {
    moy_ui_keep_t *k = mua_keep_of(c, p->id);
    if (k == NULL) {
        return 0;
    }
    int32_t base = (k->sc.flags & MOY_UI_SCROLL_GLIDE) ? k->sc.target : k->a;
    int32_t to = base;
    int sh = (int)(base - k->a);        // the page's window, moved to where it is going
    int top = p->b.y + sh, bottom = p->b.y + p->b.h + sh, d = 0;
    int32_t more = k->b - (base + p->b.h);       // the rows below what shows
    int fade = more > 0 ? FADE_H * c->fs : 0;
    if (b.y < top) {
        d = b.y - top;
    } else if (b.y + b.h > bottom - fade) {
        d = (b.y + b.h) - (bottom - fade);
        if (b.y + b.h <= bottom && d > more) {
            d = (int)more;                       // no further than the rows go
        }
    }
    if (d != 0) {
        int t = top + d;                // the page's top after, in this frame's pixels
        for (int i = 0; i < c->n_cap_prev; i++) {
            const moy_ui_cap_t *cp = &c->cap_prev[i];
            int cy = cp->y, ce = cp->y + cp->h;
            if (cp->page != page) {
                continue;
            }
            if (d > 0 && cy < t && t < ce && ce <= b.y) {
                t = ce;                 // down: past the caption, the row still in
            } else if (d < 0 && cy < t && (t < ce || (ce <= b.y && b.y - ce <= c->fs))
                       && b.y + b.h - cy <= p->b.h) {
                t = cy;                 // up: the caption in, with the row
            }
        }
        to += t - top;
        // a scroll down past a caption may go past the rows' end by as
        // much: the page keeps that room while the scroll stays there
        k->over = (int16_t)(d > 0 ? t - top - d : 0);
    }
    if (to < 0) {
        to = 0;
    }
    if (to == base) {
        return 0;
    }
    mua_aim(k, p, to);
    return 1;
}

// -- a frame -------------------------------------------------------------------------

static void floor_build(moy_ui_ctx_t *c, int p0, int n, int of);
static int floor_reach(moy_ui_ctx_t *c, int p0, int n, int base, int rh, int pic);

// A placed table's own stop (not its rows' view) among the frame's stops.
const moy_ui_stop_t *mua_table_of(const moy_ui_stop_t *v, int n, uint32_t id) {
    for (int i = 0; i < n; i++) {
        if (v[i].id == id && !(v[i].flags & STOP_PAGE)) {
            return &v[i];
        }
    }
    return NULL;
}

int mua_same_box(moy_ui_box_t a, moy_ui_box_t b) {
    return a.x == b.x && a.y == b.y && a.w == b.w && a.h == b.h;
}

// The innermost page holding the last frame's stop `ts` (the table or the
// text view kept as `t`) that may be R around it: drawn under its gen with
// no picture of no gen, its own scroll still.
static moy_ui_keep_t *view_holder(moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n,
                                  const moy_ui_keep_t *t, const moy_ui_stop_t *ts) {
    if (ts == NULL) {
        return NULL;
    }
    int ti = (int)(ts - v);
    moy_ui_keep_t *got = NULL;
    int32_t area = 0;
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        moy_ui_keep_t *k = &c->keep[i];
        if (k->id == 0 || k == t || (k->kd & (MOY_UI_KD_PAGE | MOY_UI_KD_TABLE)) != MOY_UI_KD_PAGE
            || (k->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD))
                   != (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN)
            || k->a != k->drawn || moy_ui_scroll_moving(&k->sc) || ti < k->s0
            || ti >= k->s0 + k->sn) {
            continue;
        }
        const moy_ui_stop_t *p = mua_page_of_keep(v, n, k);
        int32_t a = p != NULL ? (int32_t)p->b.w * p->b.h : 0;
        if (p != NULL && (got == NULL || a < area)) {
            got = k;
            area = a;
        }
    }
    return got;
}

// Rows y0..y1 of the carried table's view inside R (sh_in) join the floor,
// as whole rows from `base` (`rh` high), so a part lies in one rect of the
// floor or in none.
static void in_rows(moy_ui_ctx_t *c, int y0, int y1, int base, int rh);

// The frame begin just resolved, as a shift frame (docs/scroll_and_text_
// 2026-10.md §3.3): which page R it moves and by how much, where the input
// it applied and the last pass license one. `owed` the last pass owed this
// frame, `mua_over` it drew something over its page, `flags` begin's.
static void dialog_plan(moy_ui_ctx_t *c, int owed, int same);

static void shift_plan(moy_ui_ctx_t *c, int owed, int over, int flags) {
    c->sh_plan = MOY_UI_SHIFT_NONE;
    c->sh_id = 0;
    c->sh_rows = c->sh_pics = 0;
    c->sh_dlg = 0;
    c->sh_in_id = 0;
    c->sh_in_dy = 0;
    c->sh_in_tv = 0;
    c->sh_view = 0;
    c->sh_over.w = 0;
    int p = c->in[MOY_UI_IN_PTR];
    int live = p & MOY_UI_P_LIVE, held = live && (p & MOY_UI_P_HELD);
    int same = c->was_flags == (uint16_t)(flags & ~MOY_UI_KEY_EDGE) && c->was_fs == c->fs
               && c->was_hint == (uint8_t)moy_ui_hint_input()
               && mua_same_box(c->was_surface, c->surface);
    if (c->modal) {
        dialog_plan(c, owed, same);
        return;
    }

    // a press, an activation, a stepped value, a hover, an overlay: whole.
    // A press that went down on no stop and rests over none (`hover_off`:
    // a finger on a text view's rows, placing its caret) marks nothing
    // while held or let go: the rows the input changes are the floor's.
    int bare = c->hover_off;
    if (!same || owed || over || c->modal || c->pop_prev.w > 0 || c->pop_gone || c->act
        || c->step_x || ((p & MOY_UI_P_EDGE) && !bare) || (live && !held && !bare)
        || (held && c->gesture != 2 && !bare) || (p & MOY_UI_P_MIDDLE)) {
        return;
    }
    const moy_ui_stop_t *v = c->prev;
    int n = c->n_prev;
    // the one page whose scroll moved, else (a rows frame) the largest page
    // that may be R
    moy_ui_keep_t *r = NULL;
    int moved = 0;
    int32_t area = -1;
    moy_ui_keep_t *best = NULL;
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        moy_ui_keep_t *k = &c->keep[i];
        if (k->id == 0 || !(k->kd & MOY_UI_KD_PAGE)) {
            continue;
        }
        if (k->a != k->drawn) {
            moved++;
            r = k;
        }
        int ok = (k->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD | MOY_UI_KD_AT))
                 == (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN);
        const moy_ui_stop_t *ps = ok ? mua_page_of_keep(v, n, k) : NULL;
        if (ps != NULL && (int32_t)ps->b.w * ps->b.h > area) {
            area = (int32_t)ps->b.w * ps->b.h;
            best = k;
        }
    }
    if (moved > 1) {
        return;
    }
    // The table carrying its rows, moved or not, inside the page that holds
    // it: that page is R, the table's move inside it (the page's placed
    // stops lie still, so they are no bar), where it is the page R would be
    moy_ui_keep_t *in = NULL;
    moy_ui_keep_t *t = c->tc_id ? mua_keep_of(c, c->tc_id) : NULL;
    if (t != NULL && (t->kd & MOY_UI_KD_TABLE) && (r == NULL || r == t)) {
        moy_ui_keep_t *h = view_holder(c, v, n, t, mua_table_of(v, n, t->id));
        const moy_ui_stop_t *hp = h != NULL ? mua_page_of_keep(v, n, h) : NULL;
        const moy_ui_stop_t *bp = best != NULL ? mua_page_of_keep(v, n, best) : NULL;
        if (hp != NULL && (r == t || bp == NULL || (int32_t)hp->b.w * hp->b.h >= area)) {
            in = t;
            r = h;
        }
    }
    // A text view (a page of rows `rh` high, not a table's) that moved, or
    // the page a rows frame would take, inside the page that holds it: that
    // page is R and the view moves inside it, so the stops placed round the
    // view keep their pixels
    moy_ui_keep_t *tv = r != NULL ? r : best;
    if (in == NULL && tv != NULL && !(tv->kd & MOY_UI_KD_TABLE) && tv->rh > 0
        && (tv->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD | MOY_UI_KD_AT))
               == (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN)) {
        moy_ui_keep_t *h = view_holder(c, v, n, tv, mua_page_of_keep(v, n, tv));
        if (h != NULL && mua_page_of_keep(v, n, h) != NULL) {
            in = tv;
            r = h;
        }
    }
    if (r == NULL) {
        r = best;
    }
    if (r == NULL || (r->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD
                               | (in != NULL ? 0 : MOY_UI_KD_AT)))
                         != (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN)) {
        return;
    }
    uint32_t mover = in != NULL ? in->id : r->id;
    if (held && c->gesture == 2 && c->drag_page != mover) {
        return;                         // a drag of another page
    }
    int table = (r->kd & MOY_UI_KD_TABLE) != 0;
    if (c->step_y && !((table || in != NULL) && c->focus == mover)) {
        return;                         // a row step but a placed table's on its own rows
    }
    const moy_ui_stop_t *rp = mua_page_of_keep(v, n, r);
    int dy = (int)(r->drawn - r->a);
    if (rp == NULL || rp->b.w <= 0 || rp->b.h <= 0 || (dy < 0 ? -dy : dy) >= rp->b.h
        || r->s0 + r->sn > n) {
        return;
    }
    if (live && !held && dy
        && stop_at(v, n, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y] - dy) >= 0) {
        return;                         // the move slides a stop under the pointer: its hover mark
    }
    const moy_ui_stop_t *ip = NULL;
    int idy = 0;
    if (in != NULL) {
        ip = mua_page_of_keep(v, n, in);
        idy = (int)(in->drawn - in->a);
        if (ip == NULL || in->rh <= 0 || (idy < 0 ? -idy : idy) >= ip->b.h) {
            return;
        }
        if (live && !held && idy && mua_inside(ip->b, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y])
            && stop_at(v, n, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y] - idy) >= 0) {
            return;                     // the move slides a stop under the pointer
        }
        c->sh_in_id = in->id;
        c->sh_in = ip->b;
        c->sh_in_dy = (int16_t)idy;
        c->sh_in_tv = !(in->kd & MOY_UI_KD_TABLE);
    }
    c->sh_id = r->id;
    c->sh_r = rp->b;
    c->sh_dy = (int16_t)dy;
    c->sh_head = 0;
    c->fl_early = 0;
    // the floor this input asks for, as R's opening will build it: where it
    // is most of R, moving the rest costs more than it saves
    c->fl_n = 0;
    const moy_ui_stop_t *tp = table ? mua_table_of(v, n, r->id) : NULL;
    if (table && (tp == NULL || r->rh <= 0)) {
        c->sh_in_id = 0;
        return;
    }
    int pic = tp != NULL && (tp->flags & STOP_PIC);
    if (in != NULL) {
        // the page's floor, then the table's inside it, as its pass finds them
        floor_build(c, r->s0, r->sn, rp->b.y + rp->b.h);
        const moy_ui_stop_t *ts = mua_table_of(v, n, in->id);
        if (ts != NULL) {
            int count = in->b / in->rh, now = ts->row;
            int mark = (ts->st & MOY_UI_ST_FOCUS) != 0, will = c->focus == in->id && !c->elsewhere;
            if (c->step_y && now >= 0) {
                now += c->step_y;
                now = now >= count ? count - 1 : now;
                now = now < 0 ? 0 : now;
            }
            mua_table_in_floor(c, ip->b.y - in->a, in->rh, ts->row, mark, now, will,
                               rp->b.y + rp->b.h);
        }
    } else if (tp != NULL) {
        // the rows the step lands on, as the table's pass will find them
        int count = r->b / r->rh, now = tp->row;
        int mark = (tp->st & MOY_UI_ST_FOCUS) != 0, will = c->focus == r->id && !c->elsewhere;
        if (c->step_y && now >= 0) {
            now += c->step_y;
            now = now >= count ? count - 1 : now;
            now = now < 0 ? 0 : now;
        }
        mua_table_floor(c, rp->b.y - r->a, r->rh, tp->row, mark, now, will, pic,
                        rp->b.y + rp->b.h);
    } else {
        floor_build(c, r->s0, r->sn, rp->b.y + rp->b.h);
    }
    int32_t rows = 0;
    for (int i = 0; i < c->fl_n; i++) {
        rows += c->fl[i].h;
    }
    // the rows of R's pictures, where they lie this frame, the floor leaves
    // undrawn: what the move saves beyond plain rows (moy_ui_api_shift_plan)
    int32_t pics = 0;
    int top = rp->b.y, foot = rp->b.y + rp->b.h;
    if (pic) {
        pics = rp->b.h - rows;      // a picture on every row
    }
    for (int i = r->s0; i < r->s0 + r->sn; i++) {
        if (!(v[i].flags & STOP_PIC)) {
            continue;
        }
        int y0 = v[i].b.y + dy, y1 = y0 + v[i].b.h;
        y0 = y0 < top ? top : y0;
        y1 = y1 > foot ? foot : y1;
        int out = y1 - y0;
        for (int k = 0; k < c->fl_n && out > 0; k++) {
            int a = c->fl[k].y > y0 ? c->fl[k].y : y0;
            int b = c->fl[k].y + c->fl[k].h < y1 ? c->fl[k].y + c->fl[k].h : y1;
            out -= b > a ? b - a : 0;
        }
        pics += out > 0 ? out : 0;
    }
    // a stop drawn before R opens draws its part of R through the floor,
    // which is then the pass's from its start (floor_reach)
    if (!c->fl_early) {
        c->fl_n = 0;
        c->fl_pend = 0;
    }
    if (rows * 5 > (int32_t)rp->b.h * 3) {
        c->fl_n = 0;
        c->fl_pend = 0;
        c->fl_early = 0;
        c->sh_in_id = 0;
        return;
    }
    c->sh_rows = (int16_t)rows;
    c->sh_pics = (int16_t)(pics > 32767 ? 32767 : pics);
    c->sh_plan = dy || idy ? MOY_UI_SHIFT_MOVE : MOY_UI_SHIFT_ROWS;
}

// The frame begin just resolved under a dialog, as a dialog's rows frame:
// the last frame's dialog stood over a held page under its app's gen with
// nothing over it, and this frame's input typed into its field or moved its
// focus and nothing else. R is its card and the drop under it; the floor,
// built now and kept for the dialog's opening, the rows of the stops whose
// marks change and of the field typed into, every one inside the card's
// safe band (the rows its rounded corners leave whole: a corner or the drop
// drawn again would blend over itself).
static void dialog_plan(moy_ui_ctx_t *c, int owed, int same) {
    int p = c->in[MOY_UI_IN_PTR];
    if (!same || owed || !c->dw_ok || c->pop_prev.w > 0 || c->pop_gone || c->act || c->step_x
        || c->step_y || (p & (MOY_UI_P_LIVE | MOY_UI_P_EDGE | MOY_UI_P_MIDDLE))) {
        return;
    }
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        const moy_ui_keep_t *k = &c->keep[i];
        if (k->id && (k->kd & MOY_UI_KD_PAGE) && k->a != k->drawn) {
            return;                     // its body scrolled
        }
    }
    moy_ui_box_t card = c->dw_card;
    int rad = moy_ui_modal_round(c->fs, c->cls);
    c->sh_r = (moy_ui_box_t){card.x, card.y, card.w, (int16_t)(card.h + MOY_UI_DROP * c->fs)};
    c->dlg_s0 = (int16_t)(card.y + rad);
    c->dlg_s1 = (int16_t)(card.y + card.h - rad);
    c->sh_dy = 0;
    c->sh_head = 0;
    c->fl_n = 0;
    c->fl_lost = 0;
    floor_build(c, 0, c->n_prev, card.y + card.h);
    int32_t rows = 0;
    int ok = !c->fl_lost;
    for (int i = 0; i < c->fl_n && ok; i++) {
        ok = c->fl[i].y >= c->dlg_s0 && c->fl[i].y + c->fl[i].h <= c->dlg_s1;
        rows += c->fl[i].h;
    }
    c->fl_pend = 0;                     // the card is the floor's ground: it draws first
    if (!ok) {
        c->fl_n = 0;
        return;
    }
    c->sh_id = c->dw_id;
    c->sh_rows = (int16_t)rows;
    c->sh_pics = 0;
    c->sh_dlg = 1;
    c->sh_plan = MOY_UI_SHIFT_ROWS;
}

int moy_ui_api_begin(moy_ui_ctx_t *c, const int32_t *in, moy_ui_box_t surface, int flags,
                     int fs) {
    static const int32_t none[MOY_UI_IN_LEN] = {0};
    if (in == NULL) {
        in = none;
    }
    int was_held = (c->last_ptr & MOY_UI_P_LIVE) && (c->last_ptr & MOY_UI_P_HELD);
    memcpy(c->in, in, sizeof(c->in));
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        if (c->keep[i].id && !c->keep[i].seen) {
            c->keep[i].id = 0;
        }
        c->keep[i].seen = 0;
    }
    memcpy(c->prev, c->cur, c->n_cur * sizeof(moy_ui_stop_t));
    c->n_prev = c->n_cur;
    c->n_cur = 0;
    memcpy(c->cap_prev, c->cap_cur, c->n_cap_cur * sizeof(moy_ui_cap_t));
    c->n_cap_prev = c->n_cap_cur;
    c->n_cap_cur = 0;
    c->surface = surface;
    c->page = surface;
    c->page_new = 0;
    c->cls = (uint8_t)((flags & MOY_UI_DESK) ? 1 : 0);
    c->win = (uint8_t)((flags & MOY_UI_WINDOW) ? 1 : (flags & MOY_UI_PANEL) ? 2 : 0);
    c->elsewhere = (uint8_t)((flags & MOY_UI_ELSEWHERE) ? 1 : 0);
    c->tp_id = 0;                       // the parts a last frame left open are done
    c->tp_open = 0;
    c->scope = 0;
    if (c->elsewhere) {
        c->focus = 0;
    }
    c->app = (uint8_t)((flags >> MOY_UI_APP_SHIFT) & 0xff);
    if (flags & MOY_UI_KEY_EDGE) {
        c->last_key = c->last_typed = 0;    // whatever came before, this key is a press
    }
    c->fs = (uint8_t)(fs > 0 ? fs : 1);
    c->y = 0;
    c->scroll = 0;
    c->has_at = 0;
    c->page_keep = -1;
    c->page_stop = NO_PAGE;
    c->act = 0;
    c->keys = 0;
    c->edge = 0;
    c->step_x = c->step_y = 0;
    c->dup = 0;
    c->lost = 0;
    c->grp_open = c->grp_n = c->grp_any = 0;
    c->icon_slot = c->icon_glyph = c->row_slot = c->row_glyph = -1;
    c->n_nest = 0;
    c->grid_open = 0;
    c->fin_on = 0;
    c->card_slot = c->card_glyph = c->card_blank = c->card_blank_glyph = -1;
    c->card_tone = 0;
    c->spread_cols = 0;
    c->spread_foot = c->spread_top = c->spread_margin = 0;
    c->txt_lines = c->txt_centre = c->txt_tone = 0;
    c->txt_role = -1;
    c->dlg_pick = -1;
    c->typed = 0;
    c->pop_prev = c->pop_card;
    c->pop_card.w = 0;
    c->pop_gone = 0;
    c->pop_id = 0;
    c->pa_box.w = 0;
    c->pa_st = 0;
    c->sp_n = 0;
    c->sp_pn = 0;
    c->sp_cut = 0;
    c->sp_cur = -1;
    c->sp_held = 0;
    // the dialog the last frame drew, for this frame's plan, and the frames
    // it has stood as it is
    int dw = c->dlg_id != 0 && c->dlg_held && !c->fl_top && !c->fl_mute;
    int stood = dw && c->dw_n > 0 && c->dlg_id == c->dw_id && mua_same_box(c->dlg_card, c->dw_card)
                && c->dlg_gen == c->dw_gen && c->dlg_btn == c->dw_btn;
    c->dw_n = (uint8_t)(!dw ? 0 : stood ? (c->dw_n < 255 ? c->dw_n + 1 : 255) : 1);
    c->dw_ok = (uint8_t)dw;
    c->dw_id = c->dlg_id;
    c->dw_card = c->dlg_card;
    c->dw_gen = c->dlg_gen;
    c->dw_btn = c->dlg_btn;
    c->dlg_id = 0;
    c->dlg_held = 0;
    c->fl_top = c->fl_mute = 0;
    c->typed_box.w = 0;
    c->hint_band.h = 0;
    c->tip_at.w = 0;
    c->tip_shown = 0;
    c->bar_seen = 0;
    int settled = !c->owe;
    int owed_last = c->owe;
    int over_last = c->fl_dirty;
    int moved = 0;
    int dt = in[MOY_UI_IN_DT];
    c->owe = 0;
    c->kt_k = 0;                        // (a host's begin says what its canvas keeps)
    c->kf_n = c->kf_i = c->tp_keep = c->kf_copy = c->kf_took = c->kf_ci = 0;
    c->kf_held = MOY_UI_KF_HELD;
    c->fl_on = c->fl_stage = c->fl_n = c->fl_struct = c->fl_dirty = c->fl_susp = 0;
    c->fl_lost = c->at_last = c->fl_cast = c->fl_early = 0;
    c->pic_clip = (moy_ui_box_t){0, 0, 0, 0};
    c->pic_tbl = 0;
    c->fl_pend = 0;
    c->step_rev = 0;
    uint32_t focus0 = c->focus;
    moy_ui_stop_t *v = c->prev;
    int n = c->n_prev;

    // A dialog drawn last frame takes the input: only its stops are kept.
    c->modal = 0;
    for (int i = 0; i < n && !c->modal; i++) {
        c->modal = (v[i].flags & STOP_MODAL) != 0;
    }
    if (c->modal) {
        uint8_t to[MOY_UI_API_STOPS];
        int k = 0;
        for (int i = 0; i < n; i++) {
            to[i] = NO_PAGE;
            if (v[i].flags & STOP_MODAL) {
                to[i] = (uint8_t)k;
                v[k] = v[i];
                v[k].page = v[k].page < n ? to[v[k].page] : NO_PAGE;
                k++;
            }
        }
        n = c->n_prev = (uint8_t)k;
        if (!c->elsewhere && (c->focus == 0 || mua_find(v, n, c->focus) < 0)) {
            if (c->focus && !c->modal_from) {
                c->modal_from = c->focus;
            }
            int first = -1;
            for (int i = 0; i < n && first < 0; i++) {
                if (!(v[i].flags & (STOP_PAGE | STOP_DLG_BTN))) {
                    first = i;
                }
            }
            for (int i = 0; i < n && first < 0; i++) {
                if (!(v[i].flags & STOP_PAGE)) {
                    first = i;
                }
            }
            if (first >= 0) {
                c->focus = v[first].id;
                c->focus_box = v[first].b;
                c->focus_name[0] = 0;
            }
        }
    } else if (c->modal_from) {
        // the focus a dialog took comes back when it closes, unless the keys
        // are another surface's now
        if (!c->elsewhere && (c->focus == 0 || mua_find(v, n, c->focus) < 0)) {
            int i = mua_find(v, n, c->modal_from);
            if (i >= 0) {
                c->focus = v[i].id;
                c->focus_box = v[i].b;
                c->focus_name[0] = 0;
            }
        }
        c->modal_from = 0;
    }

    // Focus on an id no longer drawn passes to the nearest stop.
    int fi = c->focus ? mua_find(v, n, c->focus) : -1;
    if (c->focus && fi < 0 && n) {
        int best = -1;
        int32_t bd = INT32_MAX;
        moy_ui_box_t a = c->focus_box;
        for (int i = 0; i < n; i++) {
            if (v[i].flags & STOP_PAGE) {
                continue;
            }
            int dx = (2 * v[i].b.x + v[i].b.w) - (2 * a.x + a.w);
            int dy = (2 * v[i].b.y + v[i].b.h) - (2 * a.y + a.h);
            int32_t d = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
            if (d < bd) {
                bd = d;
                best = i;
            }
        }
        if (best >= 0) {
            c->focus = v[best].id;
            c->focus_box = v[best].b;
            c->focus_name[0] = 0;
            c->reveal = 1;
            fi = best;
        } else {
            c->focus = 0;
        }
    } else if (c->focus && fi < 0) {
        c->focus = 0;
    }

    // The keys, over the focused stop.
    int pressed = in[MOY_UI_IN_BTNP];
    int key = in[MOY_UI_IN_KEY];
    int tab = key == MOY_UI_KEY_TAB && c->last_key != MOY_UI_KEY_TAB;
    if (c->modal && c->pop_prev.w > 0 && key == MOY_UI_KEY_ESC && c->last_key != key) {
        c->pop_gone = 1;            // Esc dismisses a popover
        settled = 0;
    }
    c->last_key = key;
    const moy_ui_stop_t *f = fi >= 0 ? &v[fi] : NULL;
    int off = f == NULL || (f->flags & STOP_HOLD)
              || (f->page != NO_PAGE && f->page < n && (v[f->page].flags & STOP_NAV_OFF));
    int field = f != NULL && (f->flags & STOP_FIELD);
    c->typed_n = 0;
    if (field && key != c->last_typed && key != 0 && key != MOY_UI_KEY_TAB) {
        c->typed = key;             // a typed key, once per press
        c->typed_n = 1;
        c->keys = 1;
        settled = 0;
    }
    c->last_typed = key;
    int krep = in[MOY_UI_IN_KEYREP];
    int kbyte = krep & 0xFFFF, kn = (krep >> 16) & 0xFF;
    if (field && kn && (kbyte == 8 || kbyte == 127 || (kbyte >= 32 && kbyte < 127))
        && (c->typed == 0 || c->typed == kbyte)) {
        c->typed = kbyte;           // a held key types again: a step, never Enter
        c->typed_n += kn;
        c->keys = 1;
        settled = 0;
    }
    if (field && c->typed) {
        c->typed_box = f->b;        // its row joins a shift frame's floor
    }
    if (!off) {
        // the steps each direction makes: its press edge and its repeats
        uint32_t rw = (uint32_t)in[MOY_UI_IN_REP];
        int sx = ((pressed & MOY_UI_B_RIGHT) ? 1 : 0) + (int)((rw >> 8) & 0xFFu)
                 - ((pressed & MOY_UI_B_LEFT) ? 1 : 0) - (int)(rw & 0xFFu);
        int sy = ((pressed & MOY_UI_B_DOWN) ? 1 : 0) + (int)((rw >> 24) & 0xFFu)
                 - ((pressed & MOY_UI_B_UP) ? 1 : 0) - (int)((rw >> 16) & 0xFFu);
        int dir = (sx < 0 ? MOY_UI_B_LEFT : sx > 0 ? MOY_UI_B_RIGHT : 0)
                  | (sy < 0 ? MOY_UI_B_UP : sy > 0 ? MOY_UI_B_DOWN : 0);
        sx = sx < -127 ? -127 : sx > 127 ? 127 : sx;
        sy = sy < -127 ? -127 : sy > 127 ? 127 : sy;
        int go = pressed & (field ? MOY_UI_B_RUN : (MOY_UI_B_A | MOY_UI_B_RUN));
        if (field && (c->typed == 13 || c->typed == 10)) {
            go = MOY_UI_B_RUN;
        }
        if (dir || go || tab) {
            c->keys = 1;
        }
        int to = -1;
        if (go) {
            c->act = c->focus;
            if (field && (f->flags & STOP_MODAL)) {
                // Enter in a dialog's field is its last button, where it has one
                for (int i = n - 1; i >= 0; i--) {
                    if (v[i].flags & STOP_DLG_BTN) {
                        c->act = v[i].id;
                        break;
                    }
                }
            }
            c->tap_x = c->tap_y = INT16_MIN;
        } else if (tab) {
            to = step_order(v, n, fi, (in[MOY_UI_IN_MODS] & MOY_UI_MOD_SHIFT) != 0);
        } else if ((dir & (MOY_UI_B_LEFT | MOY_UI_B_RIGHT)) && (f->flags & STOP_KEEP_LR)) {
            c->step_x = (int8_t)sx;
        } else if ((dir & MOY_UI_B_UP) && (f->flags & STOP_KEEP_UD) && !(f->flags & STOP_AT_TOP)) {
            c->step_y = (int8_t)sy;
        } else if ((dir & MOY_UI_B_DOWN) && (f->flags & STOP_KEEP_UD)
                   && !(f->flags & STOP_AT_END)) {
            c->step_y = (int8_t)sy;
        } else if (dir) {
            // a step a stop, over last frame's stops: n steps walk n of them
            int one = (dir & MOY_UI_B_UP) ? MOY_UI_B_UP : (dir & MOY_UI_B_DOWN) ? MOY_UI_B_DOWN
                      : (dir & MOY_UI_B_LEFT) ? MOY_UI_B_LEFT : MOY_UI_B_RIGHT;
            int k = (one & (MOY_UI_B_UP | MOY_UI_B_DOWN)) ? (sy < 0 ? -sy : sy)
                                                          : (sx < 0 ? -sx : sx);
            const moy_ui_stop_t *at = f;
            for (; k > 0; k--) {
                int t = nearest(v, n, at, one);
                if (t < 0) {
                    break;
                }
                to = t;
                at = &v[t];
            }
            c->edge = (uint8_t)(to < 0 && one == MOY_UI_B_UP && !c->modal);
        }
        if (to >= 0) {
            c->focus = v[to].id;
            c->focus_box = v[to].b;
            c->focus_name[0] = 0;
            c->reveal = 1;
        }
        if (c->step_y && !(f->flags & (STOP_ROWS | STOP_PAGE)) && f->row >= 0) {
            table_reveal(c, v, n, f);
        }
        if (c->step_y && (f->flags & STOP_ROWS) && f->row >= 0) {
            // a row step on a list or a table on its page: the row's box is
            // last frame's at the next index, so the page scrolls to it now
            int rh = mua_row_h(c);
            c->step_rev = f->id;
            c->step_row = (int16_t)(f->row + c->step_y);
            c->reveal = 1;
            c->reveal_box = (moy_ui_box_t){f->b.x, (int16_t)(f->top + c->step_row * rh), f->b.w,
                                           (int16_t)rh};
        }
        if (c->step_y) {
            moved = 1;
        }
        if (c->act || c->step_x) {
            settled = 0;
        }
    }

    // The pointer: a press, a drag that scrolls its page, a release that taps.
    // A drag's direction is decided once, when it first passes the slop: a
    // mostly-vertical one scrolls the page from any row, a slider's and a
    // stepper's included; a mostly-horizontal one on a slider drags its knob.
    int p = in[MOY_UI_IN_PTR];
    int live = p & MOY_UI_P_LIVE, held = live && (p & MOY_UI_P_HELD);
    int x = in[MOY_UI_IN_X], y = in[MOY_UI_IN_Y];
    int slop = 4 * c->fs;
    if (live && (p & MOY_UI_P_EDGE)) {
        if (c->modal && c->pop_prev.w > 0 && !mua_inside(c->pop_prev, x, y)) {
            c->pop_gone = 1;        // a press outside a popover dismisses it
        }
        int h = stop_at(v, n, x, y);
        c->press = h >= 0 ? v[h].id : 0;
        c->press_bare = (uint8_t)(h < 0);
        int pg = mua_page_at(v, n, x, y);
        moy_ui_keep_t *pk = pg >= 0 ? mua_keep_of(c, v[pg].id) : NULL;
        if (pk != NULL && (pk->sc.flags & MOY_UI_SCROLL_FLING)) {
            moy_ui_scroll_stop(&pk->sc);
            c->press = 0;           // a press that catches a fling taps nothing
        }
        c->press_x = (int16_t)x;
        c->press_y = c->drag_y = (int16_t)y;
        c->gesture = 1;
        c->drag_page = 0;
        settled = 0;
    } else if (held && was_held && c->gesture) {
        if (c->gesture == 1) {
            int h = c->press ? mua_find(v, n, c->press) : -1;
            int keep_lr = h >= 0 && (v[h].flags & STOP_KEEP_LR);
            int own = h >= 0 && ((v[h].flags & STOP_HOLD) || (v[h].part && v[h].page == NO_PAGE));
            int dx = x - c->press_x, dy = y - c->press_y;
            dx = dx < 0 ? -dx : dx;
            dy = dy < 0 ? -dy : dy;
            int across = !own && dx > slop && dx > dy
                             ? page_on(c, v, n, c->press_x, c->press_y, 1) : -1;
            if (own) {
                // a part with nav off, or with no page under it to drag,
                // keeps its press: the pointer held on it is the part's
                // (ui.part_at)
            } else if (across >= 0) {
                // a mostly-horizontal drag over rows side by side scrolls them
                c->drag_page = v[across].id;
                c->drag_x = 1;
                c->drag_y = (int16_t)x;
                c->press = 0;
                c->gesture = 2;
                moy_ui_keep_t *k = mua_keep_of(c, c->drag_page);
                if (k != NULL) {
                    moy_ui_scroll_drag_start(&k->sc, c->press_x);
                }
            } else if (keep_lr && dx > slop && dx > dy) {
                c->gesture = 3;         // a slider's knob follows it; a stepper's release taps
                settled = 0;
            } else if (dy > slop) {
                int pg = page_on(c, v, n, c->press_x, c->press_y, 0);
                c->drag_page = pg >= 0 ? v[pg].id : 0;
                c->drag_x = 0;
                c->press = 0;
                c->gesture = 2;
                moy_ui_keep_t *k = c->drag_page ? mua_keep_of(c, c->drag_page) : NULL;
                if (k != NULL) {
                    moy_ui_scroll_drag_start(&k->sc, c->drag_y);
                }
            }
        }
        if (c->gesture == 2 && c->drag_page) {
            // the page follows the finger; every sample, a still one too,
            // feeds the release velocity
            moy_ui_keep_t *k = mua_keep_of(c, c->drag_page);
            const moy_ui_stop_t *pp = k != NULL ? mua_page_of_keep(v, n, k) : NULL;
            int at = c->drag_x ? x : y;
            if (pp != NULL) {
                int32_t content, extent, off = k->a;
                if (at != c->drag_y) {
                    k->over = 0;
                }
                span_of(pp, k, &content, &extent);
                moy_ui_scroll_drag_move(&k->sc, at, (float)dt, &off, content, extent);
                if (off != k->a) {
                    k->a = off;
                    moved = 1;
                }
            }
            c->drag_y = (int16_t)at;
        }
    } else if (!held && c->gesture) {
        if (c->gesture == 2 && c->drag_page) {
            // a release flings the page on at the finger's speed
            moy_ui_keep_t *k = mua_keep_of(c, c->drag_page);
            const moy_ui_stop_t *pp = k != NULL ? mua_page_of_keep(v, n, k) : NULL;
            if (pp != NULL) {
                int32_t content, extent;
                span_of(pp, k, &content, &extent);
                moy_ui_scroll_drag_end(&k->sc, k->a, content, extent);
            } else if (k != NULL) {
                k->sc.flags &= ~MOY_UI_SCROLL_DRAG;
            }
        }
        if (c->gesture != 2 && c->press && live) {
            int h = stop_at(v, n, x, y);
            if (h >= 0 && v[h].id == c->press) {
                c->act = c->press;
                c->focus = c->press;
                c->focus_box = v[h].b;
                c->focus_name[0] = 0;
                c->tap_x = (int16_t)x;
                c->tap_y = (int16_t)y;
            }
        }
        c->gesture = 0;
        c->press = 0;
        c->drag_page = 0;
        c->drag_x = 0;
        settled = 0;
    }
    // The wheel scrolls the page under the pointer; Shift turns it across.
    int wy = (in[MOY_UI_IN_MODS] & MOY_UI_MOD_SHIFT) ? 0 : in[MOY_UI_IN_WHEEL_Y];
    if (live && wy) {
        int pg = mua_page_at(v, n, x, y);
        moy_ui_keep_t *k = pg >= 0 ? mua_keep_of(c, v[pg].id) : NULL;
        if (k != NULL) {
            int32_t content, extent;
            k->over = 0;
            span_of(&v[pg], k, &content, &extent);
            int step = !(k->kd & MOY_UI_KD_TABLE) && k->rh > 0 ? k->rh : mua_row_h(c);
            moy_ui_scroll_step(&k->sc, k->a, wy, MOY_UI_WHEEL_ROWS * step, content, extent);
            moved = 1;
        }
    }
    // The middle button held drags the page it went down on by its travel.
    if (live && (p & MOY_UI_P_MIDDLE)) {
        if (!c->mid_page) {
            int pg = mua_page_at(v, n, x, y);
            c->mid_page = pg >= 0 ? v[pg].id : 0xFFFFFFFFu;
        } else if (y != c->mid_y) {
            moy_ui_keep_t *k = mua_keep_of(c, c->mid_page);
            if (k != NULL) {
                moy_ui_scroll_stop(&k->sc);
                k->a -= y - c->mid_y;
                k->over = 0;
                if (k->a < 0) {
                    k->a = 0;
                }
                moved = 1;
            }
        }
        c->mid_y = (int16_t)y;
    } else {
        c->mid_page = 0;
    }
    // A hover that moved, came or went owes a frame only where a stop lies
    // under it, then or now: a stop's hover mark is all a hover draws, so a
    // touch's linger running out over the page's ground draws nothing.
    int was_live = c->last_ptr & MOY_UI_P_LIVE;
    // a hover over no stop, then or now, draws nothing (a host's rows
    // frame), nor does a press held, or let go, that went down on none
    c->hover_off = (uint8_t)((c->press_bare || (!held && !was_held))
                             && (!live || stop_at(v, n, x, y) < 0)
                             && (!was_live || stop_at(v, n, c->last_x, c->last_y) < 0));
    if (!held && !was_held) {
        c->press_bare = 0;
    }
    if ((live && (x != c->last_x || y != c->last_y)) || live != was_live) {
        if (held || was_held || (live && stop_at(v, n, x, y) >= 0)
            || (was_live && stop_at(v, n, c->last_x, c->last_y) >= 0)) {
            settled = 0;
        }
    }
    c->last_x = (int16_t)x;
    c->last_y = (int16_t)y;
    c->last_ptr = p;

    // Keep the focused stop (or the list row it moved to) in view -- or the
    // stop a reveal names (a table on its page's first frame).
    uint32_t rid = c->reveal_id ? c->reveal_id : c->focus;
    if (c->reveal && rid) {
        int i = mua_find(v, n, rid);
        if (i >= 0 && v[i].page != NO_PAGE && v[i].page < n) {
            moy_ui_box_t b = c->reveal_box.w ? c->reveal_box : v[i].b;
            int first = !c->reveal_box.w;
            for (int j = 0; first && j < n; j++) {
                first = j == i || v[j].page != v[i].page || (v[j].flags & STOP_PAGE)
                        || v[j].b.y >= b.y;
            }
            moy_ui_keep_t *pk = first ? mua_keep_of(c, v[v[i].page].id) : NULL;
            if (pk != NULL && pk->a > 0
                && b.y + b.h + pk->a <= v[v[i].page].b.y + v[v[i].page].b.h) {
                mua_aim(pk, &v[v[i].page], 0);   // the page's first stop shows the page's top
                moved = 1;
            } else if (reveal(c, &v[v[i].page], v[i].page, b)) {
                moved = 1;
            }
            // an app's focus move, and a card's body (a dialog, a popover: an
            // overlay over the page), show at once
            moy_ui_keep_t *rk = c->jump || (v[v[i].page].flags & STOP_CARD)
                                ? mua_keep_of(c, v[v[i].page].id) : NULL;
            if (rk != NULL && (rk->sc.flags & MOY_UI_SCROLL_GLIDE)) {
                moy_ui_scroll_land(&rk->sc, &rk->a);
            }
        }
    }
    c->reveal = 0;
    c->jump = 0;
    c->reveal_box.w = 0;
    c->reveal_id = 0;
    // Every page's motion steps on the frame's clock; with none, it lands.
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        moy_ui_keep_t *k = &c->keep[i];
        if (k->id == 0 || !moy_ui_scroll_moving(&k->sc)) {
            continue;
        }
        const moy_ui_stop_t *pp = mua_page_of_keep(v, n, k);
        int32_t off = k->a;
        if (dt <= 0 || pp == NULL) {
            moy_ui_scroll_land(&k->sc, &off);
        } else {
            int32_t content, extent;
            span_of(pp, k, &content, &extent);
            moy_ui_scroll_tick(&k->sc, (float)dt, &off, content, extent);
        }
        if (off != k->a) {
            k->a = off;
            moved = 1;
        }
    }
    if (c->focus != focus0) {
        moved = 1;
    }
    c->moved = (uint8_t)moved;
    shift_plan(c, owed_last, over_last, flags);
    c->was_flags = (uint16_t)(flags & ~MOY_UI_KEY_EDGE);
    c->was_fs = c->fs;
    c->was_hint = (uint8_t)moy_ui_hint_input();
    c->was_surface = surface;
    return settled;
}

// -- layout --------------------------------------------------------------------------

// The parts' `desk` for what lies on the page's ground: the size class and
// whether the page is a window's body.
int mua_ground_of(const moy_ui_ctx_t *c) {
    return (c->cls ? MOY_UI_DESK : 0) | (c->win == 1 ? MOY_UI_WINDOW : 0)
           | (c->win == 2 ? MOY_UI_PANEL : 0);
}

moy_ui_keep_t *mua_page_keep(moy_ui_ctx_t *c) {
    return c->page_keep >= 0 ? &c->keep[c->page_keep] : NULL;
}

// A row of an open popover's body laid out at `t`, `h` tall, where the
// body is cut: whether the cut falls inside it at least half the fade
// under its middle (a part taller than a row: a row's middle down it), so
// the top of its words reads above the fade's dense half, and the deepest
// such cut at or above the cut. A rule is no row to peek: what shows of
// one is not a row running on.
static void peek_note(moy_ui_ctx_t *c, int t, int h) {
    int cut = c->pop_cut;
    if (cut <= 0 || c->n_nest == 0 || c->nest[c->n_nest - 1].kind != 3) {
        return;
    }
    int rh = mua_row_h(c);
    int at = t + (h < rh ? h : rh) / 2 + FADE_H * c->fs / 2;
    if (at <= cut && cut < t + h) {
        c->pop_inside = 1;
    }
    if (at <= cut && at > c->pop_peek) {
        c->pop_peek = (int16_t)at;
    }
}

// The next call's box: ui.at's, or the next row of the page. *stacked says
// which (a row clips to the page); a row a popover's cut may peek is `row`.
moy_ui_box_t mua_place_row(moy_ui_ctx_t *c, int h, int *stacked, int row) {
    c->row_slot = c->icon_slot;
    c->row_glyph = c->icon_glyph;
    c->icon_slot = c->icon_glyph = -1;
    c->at_last = c->has_at;
    if (c->has_at) {
        c->has_at = 0;
        *stacked = 0;
        return c->at;
    }
    *stacked = 1;
    moy_ui_box_t b = {c->page.x, (int16_t)(c->page.y + c->y - c->scroll), c->page.w, (int16_t)h};
    int gap = metric(MOY_UI_M_ROW_STACK_GAP) * c->fs;
    if (c->grp_open) {
        // a group's rows sit on its card, one under the next
        if (!c->cls) {
            int in = mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
            b.x = (int16_t)(b.x + in);
            b.w = (int16_t)(b.w - 2 * in);
        }
        gap = 0;
        c->grp_first = c->grp_n == 0;
        if (c->grp_n < 255) {
            c->grp_n++;
        }
        c->grp_last = b;
        c->grp_flags = 0;
    }
    if (row) {
        peek_note(c, c->y, h);
    }
    c->y = (int16_t)(c->y + h + gap);
    moy_ui_keep_t *k = mua_page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    return b;
}

moy_ui_box_t mua_place(moy_ui_ctx_t *c, int h, int *stacked) {
    return mua_place_row(c, h, stacked, 1);
}

// -- the floor (docs/history/scroll_and_text_2026-10.md §3.3) ---------------------------------
//
// On a shift frame a host has moved the pixels the last frame drew in R by
// the scroll's move, and the pass draws only what that leaves wrong: the
// FLOOR, a list of disjoint rects inside R. Every draw goes through the
// frame's PIECES -- everything outside R (four rects about it), and the
// floor's rects once R is open -- once per piece it touches, clipped to it,
// so a pixel in the floor takes every draw over it in the order a whole
// frame draws them, and a pixel of R outside it keeps what the move left.

rc_t mua_rc_of(moy_ui_box_t b) {
    return (rc_t){b.x, b.y, b.x + b.w, b.y + b.h};
}

int mua_rc_cut(rc_t *a, rc_t b) {
    a->x0 = a->x0 > b.x0 ? a->x0 : b.x0;
    a->y0 = a->y0 > b.y0 ? a->y0 : b.y0;
    a->x1 = a->x1 < b.x1 ? a->x1 : b.x1;
    a->y1 = a->y1 < b.y1 ? a->y1 : b.y1;
    return a->x0 < a->x1 && a->y0 < a->y1;
}

int mua_rc_meets(rc_t a, rc_t b) {
    return mua_rc_cut(&a, b);
}

moy_ui_box_t mua_box_of(rc_t r) {
    return (moy_ui_box_t){(int16_t)r.x0, (int16_t)r.y0, (int16_t)(r.x1 - r.x0),
                          (int16_t)(r.y1 - r.y0)};
}

// Whether the pass draws through the pieces now: a host armed the floor,
// and no dialog's or popover's body (an overlay, drawn whole) is open.
int mua_armed(const moy_ui_ctx_t *c) {
    return c->fl_on && !c->fl_susp;
}

// The pieces there are: four about R, then the floor once R is open (from
// the pass's start where a stop drawn before R reaches into it).
static int pieces(const moy_ui_ctx_t *c) {
    return 4 + (c->fl_stage || c->fl_early ? c->fl_n : 0);
}

static rc_t piece(const moy_ui_ctx_t *c, int i) {
    const int32_t big = 1 << 20;
    rc_t r = mua_rc_of(c->sh_r);
    switch (i) {
        case 0:
            return (rc_t){-big, -big, big, r.y0};
        case 1:
            return (rc_t){-big, r.y1, big, big};
        case 2:
            return (rc_t){-big, r.y0, r.x0, r.y1};
        case 3:
            return (rc_t){r.x1, r.y0, big, r.y1};
        default:
            return mua_rc_of(c->fl[i - 4]);
    }
}

// Whether box b lies wholly in the floor (its part inside R does).
int mua_floor_holds(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    rc_t q = mua_rc_of(b);
    if (!mua_rc_cut(&q, mua_rc_of(c->sh_r))) {
        return 1;
    }
    int32_t want = (q.x1 - q.x0) * (q.y1 - q.y0), got = 0;
    for (int i = 0; i < c->fl_n; i++) {
        rc_t f = mua_rc_of(c->fl[i]);
        if (mua_rc_cut(&f, q)) {
            got += (f.x1 - f.x0) * (f.y1 - f.y0);
        }
    }
    return got >= want;
}

int mua_floor_meets(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    rc_t q = mua_rc_of(b);
    for (int i = 0; i < c->fl_n; i++) {
        if (mua_rc_meets(mua_rc_of(c->fl[i]), q)) {
            return 1;
        }
    }
    return 0;
}

// The rect of the floor an app's picture in box b draws through: the one
// holding it, else the one it meets (R opening made a picture meet no more
// than one); w 0 where the frame draws it whole, w 0 and h -1 where it
// draws none of it.
moy_ui_box_t mua_floor_of(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    moy_ui_box_t none = {0, 0, 0, 0};
    if (!mua_armed(c) || c->fl_stage != 1 || !mua_rc_meets(mua_rc_of(b), mua_rc_of(c->sh_r))) {
        return none;
    }
    moy_ui_box_t met = {0, 0, 0, -1};
    for (int i = 0; i < c->fl_n; i++) {
        rc_t f = mua_rc_of(c->fl[i]), q = mua_rc_of(b);
        if (!mua_rc_cut(&q, f)) {
            continue;
        }
        rc_t inr = mua_rc_of(b);
        mua_rc_cut(&inr, mua_rc_of(c->sh_r));
        if (q.x0 == inr.x0 && q.y0 == inr.y0 && q.x1 == inr.x1 && q.y1 == inr.y1) {
            return c->fl[i];
        }
        met = c->fl[i];
    }
    if (mua_floor_holds(c, b)) {
        rc_t q = mua_rc_of(b);
        mua_rc_cut(&q, mua_rc_of(c->sh_r));
        return mua_box_of(q);           // a picture drawn again: all of it, in pieces
    }
    return met;
}

// A picture in box b this frame's pass leaves as the move left it: inside R
// on a shift frame, and no rect of the floor reaches it.
int mua_outside_floor(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    return mua_armed(c) && c->fl_stage == 1 && mua_rc_meets(mua_rc_of(b), mua_rc_of(c->sh_r))
           && !mua_floor_meets(c, b);
}

// Box b, inside R, joins the floor: what of it the floor does not hold yet,
// as rects; `ground` marks them for the page's ground before anything draws
// over them.
void mua_floor_add(moy_ui_ctx_t *c, rc_t b, int ground) {
    rc_t q[32];
    int n = 0;
    if (!mua_rc_cut(&b, mua_rc_of(c->sh_r))) {
        return;
    }
    q[n++] = b;
    for (int i = 0; i < c->fl_n && n > 0; i++) {
        rc_t e = mua_rc_of(c->fl[i]);
        rc_t out[32];
        int m = 0;
        for (int j = 0; j < n; j++) {
            rc_t a = q[j], x = a;
            if (!mua_rc_cut(&x, e)) {
                if (m < 32) {
                    out[m++] = a;
                }
                continue;
            }
            rc_t parts[4] = {{a.x0, a.y0, a.x1, x.y0}, {a.x0, x.y1, a.x1, a.y1},
                             {a.x0, x.y0, x.x0, x.y1}, {x.x1, x.y0, a.x1, x.y1}};
            for (int k = 0; k < 4; k++) {
                if (parts[k].x0 < parts[k].x1 && parts[k].y0 < parts[k].y1 && m < 32) {
                    out[m++] = parts[k];
                }
            }
        }
        memcpy(q, out, (size_t)m * sizeof(rc_t));
        n = m;
    }
    for (int j = 0; j < n; j++) {
        if (c->fl_n >= MOY_UI_API_FLOOR) {
            c->fl_lost = 1;
            return;
        }
        if (ground) {
            c->fl_pend |= 1u << c->fl_n;
        }
        c->fl[c->fl_n++] = mua_box_of(q[j]);
    }
}

// The rows y0..y1 of R, across it.
void mua_floor_rows(moy_ui_ctx_t *c, int y0, int y1, int ground) {
    moy_ui_box_t r = c->sh_r;
    mua_floor_add(c, (rc_t){r.x, y0, r.x + r.w, y1}, ground);
}

// Rows y0..y1 of a dialog's card join its rows frame's floor during the
// pass, each wanting the card under it (`mua_ground_fill`), cut to its body's
// foot: a widening the card's safe band cannot hold mutes the dialog (it
// draws nothing more this frame and owes the next).
void mua_dlg_widen(moy_ui_ctx_t *c, int y0, int y1) {
    y1 = y1 < c->dlg_foot ? y1 : c->dlg_foot;
    if (y1 <= y0) {
        return;
    }
    if (y0 < c->dlg_s0 || y1 > c->dlg_s1) {
        c->fl_mute = 1;
        c->owe = 1;
        return;
    }
    moy_ui_box_t r = c->sh_r;
    mua_floor_add(c, (rc_t){r.x, y0, r.x + r.w, y1}, 1);
}

// Rows y0..y1 of R join the floor as R opens: one strip across R, merged
// with every strip it meets or touches, every one wanting its ground.
static void floor_strip(moy_ui_ctx_t *c, int y0, int y1) {
    moy_ui_box_t r = c->sh_r;
    y0 = y0 > r.y ? y0 : r.y;
    y1 = y1 < r.y + r.h ? y1 : r.y + r.h;
    if (y0 >= y1) {
        return;
    }
    for (int i = 0; i < c->fl_n;) {
        moy_ui_box_t f = c->fl[i];
        if (f.y <= y1 && f.y + f.h >= y0) {
            y0 = f.y < y0 ? f.y : y0;
            y1 = f.y + f.h > y1 ? f.y + f.h : y1;
            c->fl[i] = c->fl[--c->fl_n];
            i = 0;
            continue;
        }
        i++;
    }
    if (c->fl_n >= MOY_UI_API_FLOOR) {
        c->fl_lost = 1;
        return;
    }
    c->fl[c->fl_n++] = (moy_ui_box_t){r.x, (int16_t)y0, r.w, (int16_t)(y1 - y0)};
    c->fl_pend = c->fl_n >= 32 ? 0xffffffffu : (1u << c->fl_n) - 1;
}

// How many of the floor's rects box b meets.
static int floor_count(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    rc_t q = mua_rc_of(b);
    int n = 0;
    for (int i = 0; i < c->fl_n; i++) {
        n += mua_rc_meets(mua_rc_of(c->fl[i]), q);
    }
    return n;
}

// The page's ground over box b, clipped to it.
void mua_ground_fill(const moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b) {
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    int32_t *st = row->st, saved[4];
    memcpy(saved, &st[MOY_ST_CX0], sizeof(saved));
    rc_t q = mua_rc_of(b);
    q.x0 -= st[MOY_ST_CAM_X];
    q.x1 -= st[MOY_ST_CAM_X];
    q.y0 -= st[MOY_ST_CAM_Y];
    q.y1 -= st[MOY_ST_CAM_Y];
    if (mua_rc_cut(&q, (rc_t){saved[0], saved[1], saved[2], saved[3]})) {
        st[MOY_ST_CX0] = q.x0;
        st[MOY_ST_CY0] = q.y0;
        st[MOY_ST_CX1] = q.x1;
        st[MOY_ST_CY1] = q.y1;
        static const uint8_t none[1] = {0};
        if (c->sh_dlg) {
            // a dialog's body lies on its card: the card again, under the cut
            moy_ui_modal(s, c->dw_card, (moy_ui_span_t){none, 0}, c->fs, c->cls, mua_row_h(c));
        } else {
            moy_ui_page_ground(s, b, (moy_ui_span_t){none, 0}, c->fs,
                               mua_ground_of(c), NULL);
        }
    }
    memcpy(&st[MOY_ST_CX0], saved, sizeof(saved));
}

// The floor's rects that want their ground get it.
void mua_ground_due(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    uint32_t m = c->fl_pend;
    c->fl_pend = 0;
    for (int i = 0; m && i < c->fl_n; i++, m >>= 1) {
        if (m & 1u) {
            mua_ground_fill(c, s, c->fl[i]);
        }
    }
}

int mua_clip_in(clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b) {
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    int32_t *st = row->st;
    k->row = row;
    k->on = 1;
    k->c = NULL;
    memcpy(k->saved, &st[MOY_ST_CX0], sizeof(k->saved));
    int x0 = b.x - st[MOY_ST_CAM_X], y0 = b.y - st[MOY_ST_CAM_Y];
    int x1 = x0 + b.w, y1 = y0 + b.h;
    st[MOY_ST_CX0] = x0 > k->saved[0] ? x0 : k->saved[0];
    st[MOY_ST_CY0] = y0 > k->saved[1] ? y0 : k->saved[1];
    st[MOY_ST_CX1] = x1 < k->saved[2] ? x1 : k->saved[2];
    st[MOY_ST_CY1] = y1 < k->saved[3] ? y1 : k->saved[3];
    return st[MOY_ST_CX0] < st[MOY_ST_CX1] && st[MOY_ST_CY0] < st[MOY_ST_CY1];
}

void mua_clip_out(clip_t *k) {
    if (k->on) {
        memcpy(&k->row->st[MOY_ST_CX0], k->saved, sizeof(k->saved));
        k->on = 0;
    }
}

// Whether rows y..y+h on screen, with a mark's reach about them, meet the
// clip a draw holds now.
int mua_clip_meets(const moy_ui_sink_t *s, int y, int h, int m) {
    const int32_t *st = ((const moy_canvas_row_t *)s->dst)->st;
    int y0 = y - st[MOY_ST_CAM_Y];
    return y0 + h + m > st[MOY_ST_CY0] && y0 - m < st[MOY_ST_CY1];
}

// The next piece the draw reaches, its clip set: 0 where none is left.
static int clip_piece(clip_t *k) {
    const moy_ui_ctx_t *c = k->c;
    int32_t *st = k->row->st;
    int32_t cx = st[MOY_ST_CAM_X], cy = st[MOY_ST_CAM_Y];
    int n = pieces(c);
    // a dialog on its rows frame draws through its floor alone: what lies
    // outside R is the page under it, the host's
    int floor_only = c->sh_dlg && c->fl_stage == 1;
    while (++k->i < n) {
        if (floor_only && k->i < 4) {
            continue;
        }
        rc_t p = piece(c, k->i);
        if ((k->stacked && !mua_rc_cut(&p, k->in)) || !mua_rc_meets(p, k->ext)) {
            continue;
        }
        p.x0 -= cx;
        p.x1 -= cx;
        p.y0 -= cy;
        p.y1 -= cy;
        if (!mua_rc_cut(&p, (rc_t){k->saved[0], k->saved[1], k->saved[2], k->saved[3]})) {
            continue;
        }
        st[MOY_ST_CX0] = p.x0;
        st[MOY_ST_CY0] = p.y0;
        st[MOY_ST_CX1] = p.x1;
        st[MOY_ST_CY1] = p.y1;
        k->on = 1;
        return 1;
    }
    return 0;
}

// Clip a box's draw to `in` when it stacks; answers whether any of it
// shows. On a shift frame it answers once per piece the box reaches (with
// what draws past its edges -- a mark's reach, a card's shadow), and
// mua_clip_next moves to the next: a draw is `for (ok = mua_clip_row(..); ok; ok =
// mua_clip_next(&k))`.
int mua_clip_row(moy_ui_ctx_t *c, clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b,
                 int stacked, moy_ui_box_t in) {
    k->on = 0;
    k->c = NULL;
    int cast = c->fl_cast;
    c->fl_cast = 0;
    if (stacked && (b.y + b.h <= in.y || b.y >= in.y + in.h)) {
        return 0;
    }
    if (!mua_armed(c)) {
        return stacked ? mua_clip_in(k, s, in) : 1;
    }
    if (c->fl_mute && c->fl_stage == 1) {
        return 0;                   // a dialog unlike the one planned draws nothing
    }
    mua_ground_due(c, s);
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    k->row = row;
    memcpy(k->saved, &row->st[MOY_ST_CX0], sizeof(k->saved));
    k->c = c;
    k->i = -1;
    k->stacked = stacked;
    k->in = mua_rc_of(in);
    // what draws past its box: a mark's reach, where the stop drawn last
    // carries one, and a card's shadow; a pixel's rounding otherwise
    int m = cast || (c->n_cur > 0 && (c->cur[c->n_cur - 1].st & MARKS)) ? 8 * c->fs : c->fs;
    k->ext = (rc_t){b.x - m, b.y - m, b.x + b.w + m, b.y + b.h + m};
    return clip_piece(k);
}

int mua_clip_next(clip_t *k) {
    mua_clip_out(k);
    return k->c != NULL && clip_piece(k);
}

// -- groups (ui.group) ---------------------------------------------------------------

// The open group's card: its x and width, its top on screen.
moy_ui_box_t mua_card_of(const moy_ui_ctx_t *c) {
    int in = c->cls ? 0 : mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
    return (moy_ui_box_t){(int16_t)(c->page.x + in),
                          (int16_t)(c->page.y + c->grp_top - c->scroll),
                          (int16_t)(c->page.w - 2 * in), 0};
}

// A stacked row b of an open group shows: its marks (MOY_UI_F_FOCUS,
// MOY_UI_F_ON) are kept for the card's foot -- whether or not a shift
// frame's floor draws the row.
void mua_band_mark(moy_ui_ctx_t *c, moy_ui_box_t b, int stacked, int marks) {
    if (stacked && c->grp_open && b.y + b.h > c->page.y && b.y < c->page.y + c->page.h) {
        c->grp_flags = (uint16_t)(marks & (MOY_UI_F_FOCUS | MOY_UI_F_ON | MOY_UI_F_PRESSED
                                           | MOY_UI_F_HOT));
    }
}

// The card behind stacked row b, drawn under the row's clip (mua_band_mark
// keeps its marks).
void mua_band(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked) {
    if (!stacked || !c->grp_open) {
        return;
    }
    // a row the page's foot cuts inside its fade draws no rule: the fade
    // says the rows run on, and a rule there reads as a second foot line
    int foot = c->page.y + c->page.h;
    int cut = b.y + b.h > foot && b.y >= foot - FADE_H * c->fs;
    moy_ui_group_band(s, mua_card_of(c), b, c->grp_first || cut, c->fs, mua_ground_of(c));
}

// The open group ends: its card's foot under its last row, and the gap the
// next group's caption keeps.
void mua_group_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->grp_open) {
        return;
    }
    c->grp_open = 0;
    c->grp_any = 1;
    if (c->grp_n) {
        clip_t k;
        EACH_ROW(c, k, s, c->grp_last, 1, c->page) {
            moy_ui_group_end(s, mua_card_of(c), c->grp_last, c->grp_flags, c->fs,
                             mua_ground_of(c));
            mua_clip_out(&k);
        }
    }
    c->y = (int16_t)(c->y + mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_GAP) * c->fs);
}

// Whether a dialog's or a popover's body (or a dialog's buttons) is being
// laid out: its stops are modal.
int mua_in_dialog(const moy_ui_ctx_t *c) {
    for (int i = 0; i < c->n_nest; i++) {
        if (c->nest[i].kind >= 2) {
            return 1;
        }
    }
    return 0;
}

// A stop recorded for the next frame's navigation: its state bits, or -1
// where it is no stop (a duplicate id, or a full frame).
// R's foot on screen.
static int r_foot(const moy_ui_ctx_t *c) {
    return c->sh_r.y + c->sh_r.h;
}

// A stop `ui.at` placed both frames that moved where the floor holds both
// its boxes (the last frame's moved with R, and its mark's reach where it
// carries one): it moves nothing else, and the floor already redraws it.
static int placed_move(const moy_ui_ctx_t *c, const moy_ui_stop_t *q, const moy_ui_stop_t *s) {
    if (q->id != s->id || !(q->flags & STOP_AT) || !(s->flags & STOP_AT)) {
        return 0;
    }
    int m = ((q->st | s->st) & MARKS) ? mua_mark_room(c) : 0;
    moy_ui_box_t a = {(int16_t)(q->b.x - m), (int16_t)(q->b.y + c->sh_dy - m),
                      (int16_t)(q->b.w + 2 * m), (int16_t)(q->b.h + 2 * m)};
    moy_ui_box_t b = {(int16_t)(s->b.x - m), (int16_t)(s->b.y - m), (int16_t)(s->b.w + 2 * m),
                      (int16_t)(s->b.h + 2 * m)};
    return mua_floor_holds(c, a) && mua_floor_holds(c, b);
}

// A stop drawn in a part of the table carrying its rows inside R: it moves
// with its row, so R's other stops are matched with the last frame's past it.
static int in_part(const moy_ui_ctx_t *c, const moy_ui_stop_t *s) {
    return c->sh_in_id && s->top == INT16_MIN;
}

// The last frame's stop R's stop i is matched with, past the parts' stops
// of both frames (fl_ci, fl_pi: the next of each); NULL for a part's.
static const moy_ui_stop_t *in_match(moy_ui_ctx_t *c, int i) {
    int end = c->fl_p0 + c->fl_pn;
    const moy_ui_stop_t *q = NULL;
    while (c->fl_ci <= i) {
        q = NULL;
        if (!in_part(c, &c->cur[c->fl_ci])) {
            while (c->fl_pi < end && in_part(c, &c->prev[c->fl_pi])) {
                c->fl_pi++;
            }
            q = c->fl_pi < end ? &c->prev[c->fl_pi] : NULL;
            if (c->fl_pi < end) {
                c->fl_pi++;
            }
        }
        c->fl_ci++;
    }
    return q;
}

// The stops of `v` (n of them) but the parts' (in_part).
static int in_count(const moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n) {
    int k = 0;
    for (int i = 0; i < n; i++) {
        k += !in_part(c, &v[i]);
    }
    return k;
}

// Stop i was just recorded inside R on a shift frame: where it is not the
// last frame's stop of that place (another id, another box once moved, a
// mark of another state), the floor takes R from its top down -- the floor
// only widens, and every stop after it draws whole -- but for a placed
// stop that moved inside the floor (`placed_move`).
static void floor_check(moy_ui_ctx_t *c, int i) {
    if (!mua_armed(c) || c->fl_stage != 1 || c->fl_struct || i < c->fl_s0) {
        return;
    }
    const moy_ui_stop_t *s = &c->cur[i];
    int j = i - c->fl_s0;
    const moy_ui_stop_t *q = j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
    if (c->sh_in_id) {
        if (in_part(c, s)) {
            in_match(c, i);
            return;                     // it moves with its row, which the table's floor holds
        }
        q = in_match(c, i);
    }
    if (q == NULL || q->id != s->id || q->b.x != s->b.x || q->b.w != s->b.w
        || q->b.h != s->b.h || q->b.y + c->sh_dy != s->b.y
        || (((q->st ^ s->st) & MARKS) && !mua_floor_holds(c, s->b))) {
        if (q != NULL && placed_move(c, q, s)) {
            return;
        }
        c->fl_struct = 1;
        if (c->sh_dlg) {
            mua_dlg_widen(c, s->b.y, c->dlg_foot);
        } else {
            mua_floor_rows(c, s->b.y, r_foot(c), 1);
        }
    }
}

// Stop i was just recorded outside R on a shift frame (before R opens or
// after it closes), its marks set. The pieces clip its draw to what lies
// outside R and the floor, so where its box or its mark's reach meets R the
// floor must be a piece as it draws (floor_reach: from the pass's start for
// a stop before R) and, where R moved or its marks changed, hold that part
// of R. The audit (tests only) counts a failure otherwise.
static void reach_check(const moy_ui_ctx_t *c, int i) {
    const moy_ui_stop_t *s = &c->cur[i];
    // a dialog's rows frame: what lies outside its card is the page under
    // it, which the card covers where they meet
    if (!c->inst->audit_on || !mua_armed(c) || c->fl_stage == 1 || (s->flags & STOP_PAGE)
        || s->id == c->sh_id || c->sh_dlg) {
        return;
    }
    int was = 0;
    for (int j = 0; j < c->n_prev; j++) {
        if (c->prev[j].id == s->id && !(c->prev[j].flags & STOP_PAGE)) {
            was = c->prev[j].st & MARKS;
            break;
        }
    }
    int m = (s->st & MARKS) || was ? mua_mark_room(c) : 0;
    moy_ui_box_t b = {(int16_t)(s->b.x - m), (int16_t)(s->b.y - m), (int16_t)(s->b.w + 2 * m),
                      (int16_t)(s->b.h + 2 * m)};
    if (!mua_rc_meets(mua_rc_of(b), mua_rc_of(c->sh_r))) {
        return;
    }
    if ((c->fl_stage == 0 && !c->fl_early)
        || ((c->sh_dy || (s->st & MARKS) != was) && !mua_floor_holds(c, b))) {
        c->inst->audit_fail++;
    }
}

// The L8 record (tests only): what the stop just recorded draws, mixed in.
void mua_sig_mix(moy_ui_ctx_t *c, const void *p, size_t n) {
    if (c->inst->audit_on && c->n_cur > 0) {
        moy_ui_stop_t *s = &c->cur[c->n_cur - 1];
        s->sig = mua_hash((const uint8_t *)p, n, s->sig);
    }
}

void mua_sig_int(moy_ui_ctx_t *c, int32_t v) {
    mua_sig_mix(c, &v, sizeof(v));
}

int mua_stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags, moy_ui_span_t name) {
    for (int i = 0; i < c->n_cur; i++) {
        if (c->cur[i].id == id && !(c->cur[i].flags & STOP_PAGE)) {
            if (c->dup != id) {
                c->dup = id;
                name_copy(c->dup_name, name);
            }
            return -1;
        }
    }
    if (c->n_cur >= MOY_UI_API_STOPS) {
        if (c->lost < 0xffff) {
            c->lost++;                  // drawn, but no stop: said once (moy_ui_api_lost)
        }
        return -1;
    }
    int dlg = mua_in_dialog(c);
    moy_ui_stop_t *s = &c->cur[c->n_cur++];
    s->id = id;
    s->b = b;
    s->flags = (uint16_t)(flags | (dlg ? STOP_MODAL : 0) | (c->at_last ? STOP_AT : 0));
    s->page = c->page_stop;
    s->part = 0;
    s->row = -1;
    s->top = 0;
    if (c->tp_open) {
        s->row = c->tp_row;             // drawn in a table's part: carried with its row
        s->top = INT16_MIN;
    }
    s->st = 0;
    s->gen = 0;
    s->sig = c->inst->audit_on ? mua_hash(name.p, name.n, (uint32_t)s->flags) : 0;
    if (c->modal && !dlg) {
        reach_check(c, c->n_cur - 1);
        floor_check(c, c->n_cur - 1);
        return 0;                   // under a dialog: drawn, and no input reaches it
    }
    // focus free takes the first stop drawn -- in a dialog its body's, not
    // a button its card draws first
    if (c->focus == 0 && !c->elsewhere && !(flags & STOP_DLG_BTN)) {
        c->focus = id;
        c->focus_name[0] = 0;
    }
    int st = 0;
    if (c->focus == id) {
        c->focus_box = b;
        if (c->focus_name[0] == 0) {
            name_copy(c->focus_name, name);
        }
        st |= MOY_UI_ST_FOCUS;
    }
    if (c->act == id) {
        st |= MOY_UI_ST_ACTIVATED;
    }
    int p = c->in[MOY_UI_IN_PTR];
    if ((p & MOY_UI_P_LIVE) && mua_inside(b, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y])) {
        if (p & MOY_UI_P_HELD) {
            if (c->press == id && c->gesture == 1) {
                st |= MOY_UI_ST_PRESSED;
            }
        } else {
            st |= MOY_UI_ST_HOVER;
        }
    }
    s->st = (uint8_t)st;
    mua_sig_int(c, st);
    reach_check(c, c->n_cur - 1);
    floor_check(c, c->n_cur - 1);
    return st;
}

// The stop just recorded is a card or a part: with `gen` the app says what
// it shows there is a function of it. `pic`: the app draws the picture (a
// card with no tile of the engine's, a part), which a shift frame's floor
// holds whole or not at all, and which in R with no gen moves with no
// shift (MOY_UI_KD_BAD). Answers whether, on a shift frame, its gen moved:
// it then draws again.
int mua_pic_note(moy_ui_ctx_t *c, const moy_ui_over_t *o, int pic) {
    if (c->n_cur == 0) {
        return 0;
    }
    int i = c->n_cur - 1;
    moy_ui_stop_t *s = &c->cur[i];
    s->flags |= (pic ? STOP_PIC : 0) | (o->has_gen ? STOP_GEN : 0);
    s->gen = o->gen;
    if (!mua_armed(c) || c->fl_stage != 1 || c->fl_struct || i < c->fl_s0) {
        return 0;
    }
    int j = i - c->fl_s0;
    const moy_ui_stop_t *q = j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
    return q != NULL && (!(q->flags & STOP_GEN) || !o->has_gen || q->gen != o->gen);
}

// A picture whose gen moved draws again: the rect it shows in, where its
// mark puts it (a lift) -- inside it what the card draws over the app's
// picture, around it the mark, which the gen does not move.
void mua_pic_redraw(moy_ui_ctx_t *c, moy_ui_box_t r) {
    if (!mua_floor_holds(c, r)) {
        mua_floor_add(c, mua_rc_of(r), 1);
    }
}

int mua_interact(int st) {
    return (st & MOY_UI_ST_PRESSED) ? MOY_UI_PRESSED : (st & MOY_UI_ST_HOVER) ? MOY_UI_HOVER : -1;
}

int mua_mark_flags(int st, const moy_ui_over_t *o) {
    int f = 0;
    if ((st & MOY_UI_ST_FOCUS) && o->mark != 0) {
        f |= MOY_UI_F_FOCUS;
    }
    if (o->current == 1) {
        f |= MOY_UI_F_ON;
    }
    return f;
}

uint32_t mua_id_of(const moy_ui_ctx_t *c, const moy_ui_over_t *o, moy_ui_span_t label) {
    return o->id.p != NULL ? mua_hash(o->id.p, o->id.n, c->scope)
                           : mua_hash(label.p, label.n, c->scope);
}

moy_ui_span_t mua_name_of(const moy_ui_over_t *o, moy_ui_span_t label) {
    return o->id.p != NULL ? o->id : label;
}

static void row_init(moy_ui_row_t *r, moy_ui_span_t label, int fs, const int16_t colors[3]) {
    memset(r, 0, sizeof(*r));
    r->label = label;
    r->glyph = -1;
    r->glyph_size = r->pad = r->pad_right = r->text_dy = MOY_UI_DEFAULT;
    r->glyph_ink = r->value_ink = MOY_UI_FROM_STYLE;
    r->scale = 1;
    r->fs = (int16_t)fs;
    memcpy(r->colors, colors, sizeof(r->colors));
}

// A value row (moy_ui.h, the parts) in box b, its marks from the stop's state.
void mua_vrow_init(const moy_ui_ctx_t *c, moy_ui_vrow_t *r, moy_ui_span_t label) {
    memset(r, 0, sizeof(*r));
    r->label = label;
    r->cur = -1;
    r->fs = c->fs;
    r->desk = (int16_t)mua_ground_of(c);
    r->card = c->grp_open;
    r->first = c->grp_open && c->grp_first;
    r->icon_slot = c->row_slot;
    r->icon_glyph = c->row_glyph;
}

void mua_vrow_draw(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                   int kind, int st, const moy_ui_over_t *o, const moy_ui_vrow_t *r) {
    clip_t k;
    int f = mua_mark_flags(st, o) | ((st & MOY_UI_ST_PRESSED) ? MOY_UI_F_PRESSED : 0);
    int danger = r->kind == MOY_UI_VROW_DANGER ? MOY_UI_F_HOT : 0;
    if (c->inst->audit_on && c->n_cur > 0) {
        const moy_ui_box_t sb = c->cur[c->n_cur - 1].b;
        if (sb.x == b.x && sb.y == b.y && sb.w == b.w && sb.h == b.h) {
            int32_t w[5] = {kind, r->cur, r->v, r->lo, r->hi};
            mua_sig_mix(c, w, sizeof(w));
            mua_sig_mix(c, r->value.p, r->value.n);
        }
    }
    mua_band_mark(c, b, stacked, f | danger);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked);
        moy_ui_vrow(s, b, kind, f | (o->disabled == 1 ? MOY_UI_F_DISABLED : 0), r);
        mua_clip_out(&k);
    }
}

// The stop a value row is, with its state bits (0: none, or a disabled row).
int mua_vrow_stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags,
                  const moy_ui_over_t *o, moy_ui_span_t label) {
    if (o->disabled == 1) {
        return 0;
    }
    int st = mua_stop(c, id, b, flags, mua_name_of(o, label));
    return st < 0 ? 0 : st;
}

// -- the verbs -----------------------------------------------------------------------

// How far a focus mark reaches past its box, any class, at the scale.
int mua_mark_room(const moy_ui_ctx_t *c) {
    int r = 0;
    for (int i = 0; i < 3; i++) {
        int m = moy_ui_mark_reach(i);
        r = m > r ? m : r;
    }
    return (r + 2) * c->fs;
}

// R opens on a shift frame: its floor, from the stops the last frame drew
// in it and this frame's input -- the band the move exposed, the fades at
// either end and the outer page's foot fade over it, the rows of every stop
// whose marks may move (the last frame's marked stops, the stop focus is
// on) with the row after each, and every picture the floor reaches, whole.
// Where R is not what the last frame drew (its box, its scroll, its gen),
// the floor is all of R. Every rect wants the page's ground first.
// The floor R's opening builds, from the last frame's stops (`p0`, `n` of
// them) and this frame's input: the band the move uncovered, the fades at
// either end and the outer page's foot (`of`, its bottom), the rows of
// every stop whose marks change and the row after each, a mark R's edge
// cuts while it moves, and the rows between two strips a picture reaches.
static void floor_build(moy_ui_ctx_t *c, int p0, int n, int of) {
    moy_ui_box_t r = c->sh_r;
    int top = r.y, foot = r.y + r.h, dy = c->sh_dy;
    if (c->sh_over.w > 0) {
        mua_floor_add(c, mua_rc_of(c->sh_over), 1);     // what the console drew over R
    }
    if (dy < 0) {
        floor_strip(c, foot + dy, foot);
    } else if (dy > 0) {
        floor_strip(c, top, top + dy);
    }
    if (dy) {
        // R's edges, where it holds cards: a card's text a whole frame
        // cuts there draws otherwise than the moved one, so what moved
        // across an edge is drawn again
        int cards = 0;
        for (int j = 0; j < n && !cards; j++) {
            cards = (c->prev[p0 + j].flags & STOP_GRID) != 0;
        }
        if (cards) {
            int e = 12 * c->fs;
            floor_strip(c, top, top + e);
            floor_strip(c, foot - e, foot);
        }
        int f = FADE_H * c->fs;
        floor_strip(c, foot - f + (dy < 0 ? dy : 0), foot);
        if (c->sh_head) {
            floor_strip(c, top, top + f + (dy > 0 ? dy : 0));
        }
        floor_strip(c, of - f, of);
    }
    int reach = mua_mark_room(c);
    const moy_ui_stop_t *v = &c->prev[p0];
    for (int j = 0; j < n; j++) {
        if (c->sh_in_id && v[j].top == INT16_MIN) {
            continue;                   // a part's stop moves with its row (the table's floor)
        }
        // a mark drawn last frame this one does not draw, or the other way:
        // the frame's press, hover and activation are none (the plan)
        // (a page's stop wears none: a table's rows' view shares its id)
        int now = c->focus != 0 && v[j].id == c->focus && !c->elsewhere
                          && !(v[j].flags & STOP_PAGE)
                      ? MOY_UI_ST_FOCUS : 0;
        // a mark R's edge cuts is drawn to the edge: it moves with no shift
        int cut = dy && (now || (v[j].st & MARKS))
                  && (v[j].b.y - reach < top || v[j].b.y + v[j].b.h + reach > foot
                      || v[j].b.y + dy - reach < top || v[j].b.y + v[j].b.h + dy + reach > foot);
        if ((v[j].st & MARKS) == now && !cut) {
            continue;
        }
        int y0 = v[j].b.y - reach, y1 = v[j].b.y + v[j].b.h;
        if (j + 1 < n && v[j + 1].b.y + v[j + 1].b.h > y1) {
            y1 = v[j + 1].b.y + v[j + 1].b.h;
        }
        floor_strip(c, y0 + dy, y1 + reach + dy);
    }
    if (c->typed_box.w > 0) {
        // the field typed into: its text and its caret
        floor_strip(c, c->typed_box.y + dy, c->typed_box.y + c->typed_box.h + dy);
    }
    c->fl_early = (uint8_t)floor_reach(c, p0, n, 0, 1, 0);
    for (int pass = 0; pass < 4; pass++) {
        int grew = 0;
        for (int j = 0; j < n; j++) {
            if (!(v[j].flags & STOP_PIC) || v[j].id == c->sh_in_id) {
                continue;               // (the carried table's parts each lie in their row)
            }
            moy_ui_box_t b = v[j].b;
            b.y = (int16_t)(b.y + dy);
            // a picture the app draws clips to the one rect it lies in
            // (ui.card_clip); one two rects reach takes their rows between
            if (floor_count(c, b) > 1) {
                floor_strip(c, b.y, b.y + b.h);
                grew = 1;
            }
        }
        if (!grew) {
            break;
        }
    }
}

// Rows y0..y1 of a placed table's view join the floor; where the app draws
// a picture on its rows, as whole rows (row 0's top at `base`), so a
// picture lies in one rect of the floor or in none (ui.card_clip).
void mua_table_strip(moy_ui_ctx_t *c, int y0, int y1, int base, int rh, int pic) {
    if (pic) {
        y0 = base + moy_ui_fdiv(y0 - base, rh) * rh;
        y1 = base + moy_ui_fdiv(y1 - base + rh - 1, rh) * rh;
    }
    floor_strip(c, y0, y1);
}

static void in_rows(moy_ui_ctx_t *c, int y0, int y1, int base, int rh) {
    y0 = base + moy_ui_fdiv(y0 - base, rh) * rh;
    y1 = base + moy_ui_fdiv(y1 - base + rh - 1, rh) * rh;
    rc_t q = {c->sh_in.x, y0, c->sh_in.x + c->sh_in.w, y1};
    if (mua_rc_cut(&q, mua_rc_of(c->sh_in))) {
        mua_floor_add(c, q, 1);
    }
}

// The table carrying its rows moves inside R (its view sh_in, by
// sh_in_dy): its floor -- the band the move uncovered, the rows its edges
// fade over (a row tall, the last frame's moved and this one's) and the
// outer page's foot fade (`of`, its bottom), and where the focused row or
// its mark moved (`was`, `was_mark` the last frame's; `now`, `mark` this
// one's), each row reached a mark's reach about it. `base` is row 0's top
// on screen this frame.
void mua_table_in_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                        int mark, int of) {
    int top = c->sh_in.y, foot = c->sh_in.y + c->sh_in.h, dy = c->sh_in_dy;
    if (dy < 0) {
        in_rows(c, foot + dy, foot, base, rh);
    } else if (dy > 0) {
        in_rows(c, top, top + dy, base, rh);
    }
    if (dy) {
        int f = rh < c->sh_in.h ? rh : c->sh_in.h;
        in_rows(c, top, top + f + (dy > 0 ? dy : 0), base, rh);
        in_rows(c, foot - f + (dy < 0 ? dy : 0), foot, base, rh);
        in_rows(c, of - FADE_H * c->fs, of, base, rh);
    }
    if (was != now || was_mark != mark) {
        int reach = mua_mark_room(c);
        if (was >= 0) {
            in_rows(c, base + was * rh - reach, base + (was + 1) * rh + reach, base, rh);
        }
        if (now >= 0) {
            in_rows(c, base + now * rh - reach, base + (now + 1) * rh + reach, base, rh);
        }
    }
}

// The text view moving inside R (its view sh_in, by sh_in_dy): the fades
// the move carries join the floor -- its foot's and the outer page's (`of`,
// its bottom) over it. The band the move uncovers and the rows whose gens
// moved are the view's pass's own (moy_ui_text_plan).
void mua_view_in_floor(moy_ui_ctx_t *c, int of) {
    int dy = c->sh_in_dy;
    if (!dy) {
        return;
    }
    int foot = c->sh_in.y + c->sh_in.h, f = FADE_H * c->fs;
    rc_t a = {c->sh_in.x, foot - f + (dy < 0 ? dy : 0), c->sh_in.x + c->sh_in.w, foot};
    rc_t b = {c->sh_in.x, of - f, c->sh_in.x + c->sh_in.w, of};
    if (mua_rc_cut(&a, mua_rc_of(c->sh_in))) {
        mua_floor_add(c, a, 1);
    }
    if (mua_rc_cut(&b, mua_rc_of(c->sh_in))) {
        mua_floor_add(c, b, 1);
    }
}

// What the last frame drew into R from outside it -- a stop's box reaching
// into R, a mark's reach about a stop beside it -- joins the floor where R's
// pixels no longer hold it: on a move, the rows it drew and the rows the
// move carried them to; on a rows frame, the rows of a stop whose marks
// change. `p0`, `n`: R's own stops among the last frame's; on a placed
// table's view, rows whole from `base` (`rh` high) where it draws pictures.
// Answers whether such a stop was drawn before R opened: its part of R
// the floor takes draws through it, which is then the pass's from its start.
static int floor_reach(moy_ui_ctx_t *c, int p0, int n, int base, int rh, int pic) {
    moy_ui_box_t r = c->sh_r;
    int dy = c->sh_dy, reach = mua_mark_room(c), early = 0, at = c->n_prev;
    for (int i = 0; i < c->n_prev; i++) {
        if (c->prev[i].id == c->sh_id && (c->prev[i].flags & STOP_PAGE)) {
            at = i;
            break;
        }
    }
    for (int i = 0; i < c->n_prev; i++) {
        const moy_ui_stop_t *v = &c->prev[i];
        if ((i >= p0 && i < p0 + n) || (v->flags & STOP_PAGE) || v->id == c->sh_id
            || (v->top == INT16_MIN && c->tc_id == c->sh_id)) {
            continue;                   // R's own (a part's stop moves with its row)
        }
        int now = c->focus != 0 && v->id == c->focus && !c->elsewhere ? MOY_UI_ST_FOCUS : 0;
        int m = now || (v->st & MARKS) ? reach : 0;
        int x0 = v->b.x - m, x1 = v->b.x + v->b.w + m;
        int y0 = v->b.y - m, y1 = v->b.y + v->b.h + m;
        if (x1 <= r.x || x0 >= r.x + r.w || y1 <= r.y || y0 >= r.y + r.h) {
            continue;
        }
        early |= i < at;            // whatever of it the floor takes, it draws
        if (dy || (v->st & MARKS) != now) {
            mua_table_strip(c, y0, y1, base, rh, pic);
        }
        if (dy) {
            mua_table_strip(c, y0 + dy, y1 + dy, base, rh, pic);
        }
    }
    return early;
}

// A placed table's view R opens on a shift frame: its floor -- the band
// the move uncovered, the fades a row tall at either end (the last frame's
// moved and this one's) and the outer page's foot fade (`of`, its bottom),
// and where the focused row or its mark moved (`was`, `was_mark` the last
// frame's; `now`, `mark` this one's), each row reached a mark's reach
// about it. `base` is row 0's top on screen this frame.
void mua_table_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                     int mark, int pic, int of) {
    moy_ui_box_t r = c->sh_r;
    int top = r.y, foot = r.y + r.h, dy = c->sh_dy;
    if (dy < 0) {
        mua_table_strip(c, foot + dy, foot, base, rh, pic);
    } else if (dy > 0) {
        mua_table_strip(c, top, top + dy, base, rh, pic);
    }
    if (dy) {
        int f = rh < r.h ? rh : r.h;
        mua_table_strip(c, top, top + f + (dy > 0 ? dy : 0), base, rh, pic);
        mua_table_strip(c, foot - f + (dy < 0 ? dy : 0), foot, base, rh, pic);
        mua_table_strip(c, of - FADE_H * c->fs, of, base, rh, pic);
    }
    if (was != now || was_mark != mark) {
        int reach = mua_mark_room(c);
        if (was >= 0) {
            mua_table_strip(c, base + was * rh - reach, base + (was + 1) * rh + reach, base, rh, pic);
        }
        if (now >= 0) {
            mua_table_strip(c, base + now * rh - reach, base + (now + 1) * rh + reach, base, rh, pic);
        }
    }
    c->fl_early = (uint8_t)floor_reach(c, 0, 0, base, rh, pic);
}

static void floor_open(moy_ui_ctx_t *c, const moy_ui_keep_t *k, moy_ui_box_t ct, int scroll,
                       const moy_ui_over_t *o, moy_ui_box_t outer) {
    moy_ui_box_t r = c->sh_r;
    int top = r.y, foot = r.y + r.h, dy = c->sh_dy;
    int early = c->fl_early;
    c->fl_stage = 1;
    if (!early) {
        c->fl_n = 0;
        c->fl_pend = 0;
    }
    c->fl_p0 = k->s0;
    c->fl_pn = k->sn;
    if (o == NULL || !o->has_gen || !(k->kd & MOY_UI_KD_GEN) || o->gen != k->gen
        || ct.x != r.x || ct.y != r.y || ct.w != r.w || ct.h != r.h
        || scroll - k->drawn != -dy || c->fl_p0 + c->fl_pn > c->n_prev) {
        c->fl_struct = 1;
        mua_floor_rows(c, top, foot, 1);
        return;
    }
    if (early) {
        return;                     // begin's floor, which the stops before R drew through
    }
    floor_build(c, c->fl_p0, c->fl_pn, outer.y + outer.h);
}

// Make `ct` the page the rows stack in: its scroll kept by `id` (clamped by
// the rows it held last frame) and its stop, which a drag scrolls; `o` its
// override block (its gen), `outer` the page it lies in.
static void open_page(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t ct, int flags,
                      const moy_ui_over_t *o, moy_ui_box_t outer) {
    c->page_new = (uint8_t)(mua_keep_of(c, id) == NULL);
    moy_ui_keep_t *k = mua_keep(c, id, 1);
    c->page = ct;
    c->y = 0;
    c->scroll = 0;
    c->page_keep = -1;
    if (k != NULL) {
        // the rows' height less what shows of them: the page less the hint
        // band its foot gave up last frame
        int16_t foot = k->foot;
        int32_t most = k->b - (ct.h - k->foot);
        k->foot = 0;
        if (k->a <= most && !(k->sc.flags & MOY_UI_SCROLL_GLIDE)) {
            k->over = 0;            // (a glide past the rows keeps its room until it lands)
        }
        most += k->over;
        if (k->a > most) {
            k->a = most;
        }
        if (k->a < 0) {
            k->a = 0;
        }
        c->scroll = (int16_t)k->a;
        k->b = 0;
        c->page_keep = (int8_t)(k - c->keep);
        if (mua_armed(c) && c->fl_stage == 0 && id == c->sh_id) {
            moy_ui_box_t r = ct;
            r.h = (int16_t)(r.h - foot);
            floor_open(c, k, r, c->scroll, o, outer);
        }
        int held = o != NULL && o->has_gen && o->gen == k->gen && k->drawn == c->scroll
                   && (k->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN))
                          == (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN);
        k->drawn = c->scroll;
        k->gen = o != NULL ? o->gen : 0;
        k->kd = (uint8_t)(MOY_UI_KD_PAGE | (o != NULL && o->has_gen ? MOY_UI_KD_GEN : 0)
                          | (held ? MOY_UI_KD_HELD : 0));
    }
    c->page_stop = NO_PAGE;
    if (c->n_cur < MOY_UI_API_STOPS) {
        moy_ui_stop_t *p = &c->cur[c->n_cur];
        memset(p, 0, sizeof(*p));
        p->id = id;
        p->b = ct;
        p->flags = (uint16_t)(STOP_PAGE | flags);
        p->page = NO_PAGE;
        p->row = -1;
        c->page_stop = c->n_cur++;
    }
    if (c->fl_stage == 1 && id == c->sh_id) {
        c->fl_s0 = c->n_cur;
        c->fl_ci = c->fl_s0;
        c->fl_pi = c->fl_p0;
    }
}

// The open page is done (its nest closes, ui.end, the next page): what it
// holds, for the next frame's plan -- the stops drawn in it and whether any
// is one no shift may move: a stop ui.at placed, a picture with no gen. R
// closing on a shift frame: the ground under the stops its tail lost, the
// ground its floor still wants, and under the audit its stops' records
// against the last frame's.
void mua_page_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = mua_page_keep(c);
    if (k == NULL || c->page_stop == NO_PAGE || c->cur[c->page_stop].id != k->id) {
        return;
    }
    int s0 = c->page_stop + 1, sn = c->n_cur - s0;
    int bad = 0, at = 0;
    for (int i = s0; i < c->n_cur; i++) {
        uint16_t f = c->cur[i].flags;
        at |= (f & STOP_AT) != 0;
        if ((f & STOP_PIC) && !(f & STOP_GEN)) {
            bad = 1;
        }
    }
    if (mua_armed(c) && c->fl_stage == 1 && k->id == c->sh_id) {
        int cn = c->sh_in_id ? in_count(c, &c->cur[s0], sn) : sn;
        int pn = c->sh_in_id ? in_count(c, &c->prev[c->fl_p0], c->fl_pn) : c->fl_pn;
        if (!c->fl_struct && !c->fl_mute && cn < pn) {
            int y = sn > 0 ? c->cur[c->n_cur - 1].b.y + c->cur[c->n_cur - 1].b.h
                           : (c->sh_dlg ? c->page.y : c->sh_r.y);
            if (c->sh_dlg) {
                mua_dlg_widen(c, y, c->dlg_foot);
            } else {
                mua_floor_rows(c, y, r_foot(c), 1);
            }
        }
        mua_ground_due(c, s);
        if (c->inst->audit_on && !c->fl_struct && !c->fl_mute) {
            for (int j = 0, k = 0; j < sn && k < c->fl_pn; j++, k++) {
                const moy_ui_stop_t *a = &c->cur[s0 + j];
                if (in_part(c, a)) {
                    k--;                // its row's floor holds it
                    continue;
                }
                while (k < c->fl_pn && in_part(c, &c->prev[c->fl_p0 + k])) {
                    k++;
                }
                if (k >= c->fl_pn) {
                    break;
                }
                const moy_ui_stop_t *q = &c->prev[c->fl_p0 + k];
                if (a->sig != q->sig && !mua_floor_holds(c, a->b)) {
                    c->inst->audit_fail++;
                }
            }
        }
        if (c->fl_lost && c->inst->audit_on) {
            c->inst->audit_fail++;
        }
        c->fl_stage = 2;
    }
    k->s0 = (uint8_t)s0;
    k->sn = (uint8_t)(sn > 0 ? sn : 0);
    k->kd |= MOY_UI_KD_DRAWN | (bad ? MOY_UI_KD_BAD : 0) | (at ? MOY_UI_KD_AT : 0);
}

void moy_ui_api_page(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     const moy_ui_over_t *o, moy_ui_box_t *content) {
    o = mua_over(o);
    mua_grid_end(c, s);
    mua_group_close(c, s);
    if (c->n_nest == 0) {
        mua_page_close(c, s);
    }
    c->grp_any = 0;
    moy_ui_box_t b = c->surface;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    }
    moy_ui_box_t ct = b;
    clip_t k;
    int drew = 0;
    EACH_PIECE(c, k, s, b) {
        moy_ui_page_ground(s, b, title, c->fs, mua_ground_of(c), &ct);
        drew = 1;
    }
    if (!drew) {
        // nothing of it is drawn this frame: its content rect all the same
        moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
        int32_t saved[4];
        memcpy(saved, &row->st[MOY_ST_CX0], sizeof(saved));
        row->st[MOY_ST_CX1] = row->st[MOY_ST_CX0];
        moy_ui_page_ground(s, b, title, c->fs, mua_ground_of(c), &ct);
        memcpy(&row->st[MOY_ST_CX0], saved, sizeof(saved));
    }
    // a page's id is its title, or `o.id` where two pages share one
    moy_ui_span_t pid = o->id.p != NULL ? o->id : title;
    open_page(c, mua_hash(pid.p, pid.n, PAGE_SALT), ct, o->nav == 0 ? STOP_NAV_OFF : 0, o, ct);
    if (content != NULL) {
        *content = ct;
    }
}

void moy_ui_api_label(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      const moy_ui_over_t *o) {
    o = mua_over(o);
    int stacked;
    if (c->txt_lines || c->txt_centre || c->txt_tone || c->txt_role >= 0) {
        // the Python face's text options: lines wrapped, centred, a tone, a role
        int lines = c->txt_lines, centre = c->txt_centre == 1, tone = c->txt_tone;
        int role = c->txt_role >= 0 ? c->txt_role : MOY_UI_FONT_UI;
        int sunk = c->txt_centre == 3 ? (c->cls ? 10 : 8) * c->fs : 0;
        int pad = c->txt_centre == 2
                      ? (mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET)
                         + mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * c->fs
                      : mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
        c->txt_lines = c->txt_centre = c->txt_tone = 0;
        c->txt_role = -1;
        moy_ui_box_t m = c->has_at ? c->at : c->page;
        if (!c->has_at) {
            m.x = (int16_t)(m.x + pad);
            m.w = (int16_t)(m.w - 2 * pad);
        }
        moy_ui_box_t well = m;
        if (sunk) {
            // the inset across the row, its text `sunk` pixels inside it
            m.x = (int16_t)(m.x + sunk);
            m.w = (int16_t)(m.w - 2 * sunk);
        }
        int h = moy_ui_text_block(s, m, text, role, tone, lines, centre, c->icon_slot,
                                  c->icon_glyph, c->fs, mua_ground_of(c), 0);
        if (sunk) {
            int hb = h + 2 * sunk - (c->cls ? 0 : 2 * c->fs);
            moy_ui_box_t b = mua_place(c, hb, &stacked);
            well.y = b.y;
            well.h = (int16_t)hb;
            clip_t k;
            EACH_ROW(c, k, s, well, stacked, c->page) {
                moy_ui_well(s, well, 0, moy_ui_token(s->look, MOY_UI_T_SUNK), c->fs,
                            mua_ground_of(c));
                m.y = (int16_t)(b.y + sunk - (c->cls ? 0 : c->fs));
                m.h = (int16_t)h;
                moy_ui_text_block(s, m, text, role, tone, lines, centre, c->row_slot,
                                  c->row_glyph, c->fs, mua_ground_of(c), 1);
                mua_clip_out(&k);
            }
            return;
        }
        int one = lines <= 1 && !c->has_at && h < mua_row_h(c);
        moy_ui_box_t b = mua_place(c, one ? mua_row_h(c) : h, &stacked);
        b.x = m.x;
        b.w = m.w;
        if (one) {
            b.y = (int16_t)(b.y + (mua_row_h(c) - h) / 2);
            b.h = (int16_t)h;
        }
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_text_block(s, b, text, role, tone, lines, centre, c->row_slot, c->row_glyph,
                              c->fs, mua_ground_of(c), 1);
            mua_clip_out(&k);
        }
        return;
    }
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    clip_t k;
    if ((stacked && c->grp_open) || o->ink == MOY_UI_O_NONE) {
        mua_band_mark(c, b, stacked, 0);
    }
    EACH_ROW(c, k, s, b, stacked, c->page) {
        // a group's row, or a label on the page's ground: the parts' ink
        if ((stacked && c->grp_open) || o->ink == MOY_UI_O_NONE) {
            mua_band(c, s, b, stacked);
            moy_ui_vrow_t r;
            mua_vrow_init(c, &r, text);
            moy_ui_vrow(s, b, MOY_UI_V_LABEL, 0, &r);
            continue;
        }
        int16_t pc[3];
        moy_ui_colors(s->look, MOY_UI_K_PANEL, MOY_UI_REST, pc);
        int16_t ink = colour(s->look, o->ink);
        int pad = mua_kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
        moy_ui_box_t t = {(int16_t)(b.x + pad), b.y, (int16_t)(b.w - 2 * pad), b.h};
        int li = ink == MOY_UI_FROM_STYLE ? pc[1] : ink;
        if (c->row_slot >= 0 || c->row_glyph >= 0) {
            int side = 16 * c->fs;
            moy_ui_box_t ib = {t.x, (int16_t)(b.y + moy_ui_fdiv(b.h - side, 2)), (int16_t)side,
                               (int16_t)side};
            if (moy_ui_icon_in(s, c->row_slot, c->row_glyph, ib, li, c->fs)) {
                t.x = (int16_t)(t.x + side + 4 * c->fs);
                t.w = (int16_t)(t.w - side - 4 * c->fs);
            }
        }
        moy_ui_label(s, t, text, li, MOY_TEXT_LEFT);
    }
}

int moy_ui_api_button(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int kind,
                      const moy_ui_over_t *o) {
    o = mua_over(o);
    if (kind < 0 || kind >= 5) {
        kind = 0;
    }
    const char *kn = moy_ui_button_kinds[kind][0];
    int kid = moy_ui_button_kind(kn, strlen(kn));
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : mua_stop(c, mua_id_of(c, o, label), b, 0, mua_name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    mua_band_mark(c, b, stacked, mua_mark_flags(st, o));
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked);
        moy_ui_button_t bt;
        bt.label = label;
        bt.glyph = -1;
        bt.fs = c->fs;
        colours(s, o, bt.colors);
        bt.app = (int16_t)(c->app - 1);
        bt.icon = -1;
        int fl = mua_mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0);
        if (c->row_slot >= 0 || c->row_glyph >= 0) {
            fl |= MOY_UI_F_ICON;
            bt.icon = c->row_slot;
            bt.glyph = c->row_glyph;
        }
        moy_ui_button(s, b, kid, fl, mua_interact(st), &bt, MOY_UI_PHASE_ALL);
        mua_clip_out(&k);
    }
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int moy_ui_api_toggle(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int on,
                      const moy_ui_over_t *o) {
    o = mua_over(o);
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    int st = mua_vrow_stop(c, mua_id_of(c, o, label), b, 0, o, label);
    on = on ? 1 : 0;
    if (st & MOY_UI_ST_ACTIVATED) {
        on = !on;
    }
    moy_ui_vrow_t r;
    mua_vrow_init(c, &r, label);
    r.cur = (int16_t)on;
    mua_vrow_draw(c, s, b, stacked, MOY_UI_V_TOGGLE, st, o, &r);
    return on;
}

int32_t moy_ui_api_slider(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                          int32_t v, int32_t lo, int32_t hi, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (hi < lo) {
        int32_t t = lo;
        lo = hi;
        hi = t;
    }
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    uint32_t id = mua_id_of(c, o, label);
    int st = mua_vrow_stop(c, id, b, STOP_KEEP_LR, o, label);
    if ((st & MOY_UI_ST_FOCUS) && c->step_x) {
        v += c->step_x;
    }
    moy_ui_vrow_t r;
    mua_vrow_init(c, &r, label);
    r.lo = lo;
    r.hi = hi;
    r.steps = o->steps == 1;
    int p = c->in[MOY_UI_IN_PTR];
    if (o->disabled != 1 && c->press == id && c->gesture == 3 && (p & MOY_UI_P_LIVE)
        && (p & MOY_UI_P_HELD) && b.w > 0) {
        v = moy_ui_vrow_at(s, b, MOY_UI_V_SLIDER, &r, c->in[MOY_UI_IN_X]);
    } else if ((st & MOY_UI_ST_ACTIVATED) && c->tap_x != INT16_MIN && b.w > 0) {
        v = moy_ui_vrow_at(s, b, MOY_UI_V_SLIDER, &r, c->tap_x);
    }
    if (v < lo) {
        v = lo;
    }
    if (v > hi) {
        v = hi;
    }
    r.v = v;
    mua_vrow_draw(c, s, b, stacked, MOY_UI_V_SLIDER, st, o, &r);
    return v;
}

void moy_ui_api_group_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    mua_grid_end(c, s);
    mua_group_close(c, s);
    c->has_at = 0;
    c->grp_open = 1;
    c->grp_n = 0;
    c->grp_top = c->y;
}

void moy_ui_api_group(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t caption,
                      const moy_ui_over_t *o) {
    (void)o;
    mua_grid_end(c, s);
    mua_group_close(c, s);
    c->has_at = 0;
    if (caption.n == 0) {
        return;
    }
    int h = mua_kmetric(MOY_UI_K_GROUP, c->cls ? MOY_UI_M_GROUP_CAPTION_DESK : MOY_UI_M_GROUP_CAPTION)
            * c->fs;
    moy_ui_box_t b = {c->page.x, (int16_t)(c->page.y + c->y - c->scroll), c->page.w, (int16_t)h};
    peek_note(c, c->y, h);
    c->y = (int16_t)(c->y + h);
    moy_ui_keep_t *k = mua_page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    if (c->n_cap_cur < MOY_UI_API_CAPS && c->page_stop != NO_PAGE) {
        c->cap_cur[c->n_cap_cur++] = (moy_ui_cap_t){b.y, b.h, c->page_stop};
    }
    clip_t cl;
    moy_ui_box_t cb = b;
    if (!c->cls) {
        int in = mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
        cb.x = (int16_t)(cb.x + in);
        cb.w = (int16_t)(cb.w - 2 * in);
    }
    EACH_ROW(c, cl, s, b, 1, c->page) {
        moy_ui_caption(s, cb, caption, c->fs, c->cls);
        mua_clip_out(&cl);
    }
    c->grp_open = 1;
    c->grp_n = 0;
    c->grp_top = c->y;
}

int moy_ui_api_value(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                     moy_ui_span_t value, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (kind != MOY_UI_V_LINK) {
        kind = MOY_UI_V_INFO;
    }
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    int st = kind == MOY_UI_V_LINK ? mua_vrow_stop(c, mua_id_of(c, o, label), b, 0, o, label) : 0;
    moy_ui_vrow_t r;
    mua_vrow_init(c, &r, label);
    r.value = value;
    mua_vrow_draw(c, s, b, stacked, kind, st, o, &r);
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int32_t moy_ui_api_choice(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                          const uint8_t *opts, size_t len, int32_t cur, const moy_ui_over_t *o) {
    o = mua_over(o);
    if (kind != MOY_UI_V_SEG && kind != MOY_UI_V_SWATCH && kind != MOY_UI_V_TABS) {
        kind = MOY_UI_V_CYCLE;
    }
    int n = 0;
    if (kind == MOY_UI_V_SWATCH) {
        n = (int)(len / 4);
    } else {
        for (size_t i = 0; i < len; i++) {
            n += opts[i] == 0;
        }
        if (len && opts[len - 1] != 0) {
            n++;
        }
    }
    if (n > 255) {
        n = 255;
    }
    int stacked;
    moy_ui_box_t b = mua_place(c, mua_row_h(c), &stacked);
    int st = mua_vrow_stop(c, mua_id_of(c, o, label), b, STOP_KEEP_LR, o, label);
    moy_ui_vrow_t r;
    mua_vrow_init(c, &r, label);
    r.n = (int16_t)n;
    if (kind == MOY_UI_V_SWATCH) {
        r.colours = opts;
    } else {
        r.value.p = opts;
        r.value.n = (uint16_t)(len > 0xffff ? 0xffff : len);
    }
    if (n > 0) {
        int step = (st & MOY_UI_ST_FOCUS) ? c->step_x : 0;
        if (st & MOY_UI_ST_ACTIVATED) {
            int at = c->tap_x != INT16_MIN ? moy_ui_vrow_at(s, b, kind, &r, c->tap_x) : -1;
            if (at >= 0) {
                cur = at;
                step = 0;
            } else if (c->tap_x == INT16_MIN || kind == MOY_UI_V_CYCLE) {
                step = 1;               // a key, or a tap on a cycle: the next
            }
        }
        if (step) {
            cur = cur < 0 ? (step > 0 ? 0 : n - 1) : cur + step;
            if (kind == MOY_UI_V_CYCLE || (st & MOY_UI_ST_ACTIVATED)) {
                cur = (cur % n + n) % n;
            } else {
                cur = cur < 0 ? 0 : cur >= n ? n - 1 : cur;
            }
        }
        if (cur >= n) {
            cur = n - 1;
        }
    } else {
        cur = -1;
    }
    r.cur = (int16_t)cur;
    if (kind == MOY_UI_V_CYCLE) {
        size_t at = 0;
        int i = 0;
        r.value.n = 0;
        while (at <= len && n > 0 && cur >= 0) {
            size_t e = at;
            while (e < len && opts[e] != 0) {
                e++;
            }
            if (i == cur) {
                r.value.p = opts + at;
                r.value.n = (uint16_t)(e - at);
                break;
            }
            i++;
            at = e + 1;
        }
    }
    mua_vrow_draw(c, s, b, stacked, kind, st, o, &r);
    return cur;
}

// The band a rule takes between a card's rows: the row stack's gap and a
// unit, twice over.
int mua_rule_h(const moy_ui_ctx_t *c) {
    return (2 * metric(MOY_UI_M_ROW_STACK_GAP) + (c->cls ? 9 : 5)) * c->fs;
}

void moy_ui_api_hints(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items, size_t len,
                      const moy_ui_over_t *o) {
    (void)o;
    moy_ui_box_t b;
    if (moy_ui_hint_input() == MOY_UI_INPUT_TOUCH) {
        // touch alone shows no keys: no band, and the rows take its height
        c->has_at = 0;
        return;
    }
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    } else if (c->n_nest && c->nest[c->n_nest - 1].kind >= 2) {
        // in a dialog's or a popover's body: a band at the row cursor, under a
        // rule that parts it from the rows above
        mua_group_close(c, s);
        int h = mua_kmetric(MOY_UI_K_VALUE_ROW, c->cls ? MOY_UI_M_VALUE_ROW_HINTS_DESK : MOY_UI_M_VALUE_ROW_HINTS)
                * c->fs;
        int stacked;
        moy_ui_box_t r = mua_place(c, mua_rule_h(c), &stacked);
        moy_ui_rule(s, r, c->fs, c->cls);
        b = mua_place(c, h, &stacked);
    } else {
        int h = mua_kmetric(MOY_UI_K_VALUE_ROW, c->cls ? MOY_UI_M_VALUE_ROW_HINTS_DESK : MOY_UI_M_VALUE_ROW_HINTS)
                * c->fs;
        if (h > c->page.h) {
            h = c->page.h;
        }
        b = (moy_ui_box_t){c->page.x, (int16_t)(c->page.y + c->page.h - h), c->page.w,
                           (int16_t)h};
        c->page.h = (int16_t)(c->page.h - h);
        if (c->page_stop != NO_PAGE) {
            c->cur[c->page_stop].b.h = c->page.h;
        }
        moy_ui_keep_t *k = mua_page_keep(c);
        if (k != NULL) {
            k->foot = (int16_t)h;
        }
    }
    if (c->n_nest == 0) {
        c->hint_band = b;
    }
    clip_t k;
    EACH_PIECE(c, k, s, b) {
        moy_ui_hints(s, b, items, len, c->fs, mua_ground_of(c));
    }
}

int moy_ui_api_hints_w(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items,
                       size_t len) {
    int16_t sp[32];
    moy_ui_box_t b = {0, 0, 0x3fff, 0};
    int n = moy_ui_hints_at(s, b, items, len, c->fs, mua_ground_of(c), 0, sp, 16);
    int w = 0;
    for (int k = 0; k < n && k < 16; k++) {
        w = sp[2 * k + 1] > w ? sp[2 * k + 1] : w;
    }
    return w ? w + MOY_UI_HINT_LEAD(c->cls) * c->fs : 0;
}

// The stop `id` just drew is a list's or a table's: its focused row and row
// 0's top on screen, which a key step on its page reveals at the next begin.
void mua_rows_note(moy_ui_ctx_t *c, uint32_t id, int row, int top) {
    if (c->n_cur > 0 && c->cur[c->n_cur - 1].id == id) {
        c->cur[c->n_cur - 1].row = (int16_t)row;
        c->cur[c->n_cur - 1].top = (int16_t)top;
    }
}

int32_t moy_ui_api_list(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, int32_t cur, const moy_ui_over_t *o) {
    o = mua_over(o);
    int count = 0;
    for (size_t i = 0; i < len; i++) {
        count += items[i] == 0;
    }
    if (len && items[len - 1] != 0) {
        count++;
    }
    int rh = mua_row_h(c);
    int placed = c->has_at;
    if (label.n && !placed) {
        moy_ui_api_label(c, s, label, NULL);
    }
    int stacked;
    moy_ui_box_t b = mua_place(c, (count ? count : 1) * rh, &stacked);
    uint32_t id = mua_id_of(c, o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled ? NULL : mua_keep(c, id, 1);
    int row = kp != NULL ? kp->a : 0;
    if (kp != NULL && kp->b == 0 && kp->a == 0 && cur > 0) {
        row = cur;                  // a list first drawn starts on its current row
    }
    int focused = !disabled && c->focus == id;
    if (focused && c->step_y) {
        row += c->step_y;
    }
    if (row >= count) {
        row = count - 1;
    }
    if (row < 0) {
        row = 0;
    }
    int flags = STOP_KEEP_UD | (row == 0 ? STOP_AT_TOP : 0) | (row >= count - 1 ? STOP_AT_END : 0)
                | (placed ? 0 : STOP_ROWS);
    int st = disabled ? 0 : mua_stop(c, id, b, flags, mua_name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    int offset = 0;
    if (placed && kp != NULL) {
        offset = kp->b - 1;         // b holds the offset + 1 (0: never drawn)
        if (offset < 0) {
            offset = 0;
        }
    }
    if ((st & MOY_UI_ST_ACTIVATED) && count) {
        if (c->tap_y != INT16_MIN) {
            int r = (c->tap_y - b.y + offset) / (rh > 0 ? rh : 1);
            if (r >= 0 && r < count) {
                row = r;
            }
        }
        cur = row;
    }
    if (placed) {
        if (row * rh < offset) {
            offset = row * rh;
        } else if ((row + 1) * rh > offset + b.h) {
            offset = (row + 1) * rh - b.h;
        }
    }
    if (kp != NULL) {
        kp->a = row;
        kp->b = placed ? offset + 1 : 1;
    }
    moy_ui_box_t rb = {b.x, (int16_t)(b.y + row * rh - offset), b.w, (int16_t)rh};
    mua_rows_note(c, id, row, b.y);
    if (focused && c->step_y && !(c->step_rev == id && c->step_row == row)) {
        c->reveal = 1;              // a step begin did not reveal: the next frame does
        c->reveal_box = rb;
        c->owe = 1;
    }
    clip_t k;
    mua_band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked);
        clip_t kb;
        int any = mua_clip_in(&kb, s, b);
        int16_t col[3];
        colours(s, o, col);
        int px = c->in[MOY_UI_IN_X], py = c->in[MOY_UI_IN_Y], p = c->in[MOY_UI_IN_PTR];
        size_t at = 0;
        for (int i = 0; any && i < count && at <= len; i++) {
            size_t e = at;
            while (e < len && items[e] != 0) {
                e++;
            }
            int ry = b.y + i * rh - offset;
            if (ry + rh > b.y && ry < b.y + b.h) {
                moy_ui_span_t t = {items + at, (uint16_t)(e - at)};
                moy_ui_row_t r;
                row_init(&r, t, c->fs, col);
                moy_ui_box_t ib = {b.x, (int16_t)ry, b.w, (int16_t)rh};
                int f = MOY_UI_F_EDGE | (i == cur ? MOY_UI_F_ON : 0)
                        | (disabled ? MOY_UI_F_DISABLED : 0);
                if ((st & MOY_UI_ST_FOCUS) && o->mark != 0 && i == row) {
                    f |= MOY_UI_F_FOCUS;
                }
                int ia = -1;
                if ((p & MOY_UI_P_LIVE) && mua_inside(ib, px, py)) {
                    ia = (st & MOY_UI_ST_PRESSED) ? MOY_UI_PRESSED
                         : (st & MOY_UI_ST_HOVER) ? MOY_UI_HOVER : -1;
                }
                moy_ui_row(s, ib, MOY_UI_K_ROW_LIST, f, ia, &r, MOY_UI_PHASE_ALL);
            }
            at = e + 1;
        }
        mua_clip_out(&kb);
        mua_clip_out(&k);
    }
    return cur < count ? cur : count - 1;
}

int moy_ui_api_icon(moy_ui_ctx_t *c, moy_ui_span_t name) {
    c->icon_slot = (int16_t)moy_ui_icon_slot(name.p, name.n);
    c->icon_glyph = (int16_t)moy_ui_icon_glyph(name.p, name.n);
    return c->icon_slot >= 0 || c->icon_glyph >= 0;
}

void moy_ui_api_progress(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int32_t v,
                         int32_t hi, const moy_ui_over_t *o) {
    o = mua_over(o);
    int stacked;
    int count = o->steps == 1;
    // a count stacked: its label on a row and its bar under it, in from the
    // page's edges as a group's caption is
    int placed = c->has_at;
    int top = count && !placed ? (c->cls ? 14 : 6) * c->fs : 0;
    int h = count ? top + mua_row_h(c) + (c->cls ? 10 : 7) * c->fs : mua_row_h(c);
    moy_ui_box_t b = mua_place(c, h, &stacked);
    if (count && !placed) {
        int in = (mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET)
                  + mua_kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * c->fs;
        b.x = (int16_t)(b.x + in);
        b.w = (int16_t)(b.w - 2 * in);
        b.y = (int16_t)(b.y + top);
        b.h = (int16_t)(b.h - top);
    }
    moy_ui_vrow_t r;
    mua_vrow_init(c, &r, label);
    r.lo = 0;
    r.hi = hi > 0 ? hi : 1;
    r.v = v < 0 ? 0 : v > r.hi ? r.hi : v;
    r.steps = count;
    mua_vrow_draw(c, s, b, stacked, MOY_UI_V_PROGRESS, 0, o, &r);
}

void moy_ui_api_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o, int32_t out[5]) {
    o = mua_over(o);
    int stacked;
    int placed = c->has_at;
    moy_ui_box_t b = mua_place(c, h > 0 ? h : mua_row_h(c), &stacked);
    if (!placed && w > 0 && w < b.w) {
        b.w = (int16_t)w;
    }
    int disabled = o->disabled == 1;
    uint32_t pid = o->id.p != NULL ? mua_hash(o->id.p, o->id.n, 0) : mua_hash(id.p, id.n, 0);
    int st = disabled ? 0
                      : mua_stop(c, pid, b, o->nav == 0 ? STOP_HOLD : 0, o->id.p != NULL ? o->id : id);
    if (st >= 0 && !disabled && c->n_cur > 0 && c->cur[c->n_cur - 1].id == pid) {
        c->cur[c->n_cur - 1].part = 1;
    }
    if (!disabled && st >= 0 && mua_pic_note(c, o, 1)) {
        mua_pic_redraw(c, b);
    }
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    mua_band_mark(c, b, stacked, mua_mark_flags(st, o));
    EACH_ROW(c, k, s, b, stacked, c->page) {
        mua_band(c, s, b, stacked);
        int16_t oc[3];
        colours(s, o, oc);
        moy_ui_well(s, b, mua_mark_flags(st, o), oc[0] == MOY_UI_FROM_STYLE ? -1 : oc[0], c->fs,
                    c->cls);
        mua_clip_out(&k);
    }
    int none = mua_outside_floor(c, b);
    c->pic_clip = mua_floor_of(c, b);
    c->pic_tbl = 0;
    out[0] = none ? 0 : b.x;
    out[1] = none ? 0 : b.y;
    out[2] = none ? 0 : b.w;
    out[3] = none ? 0 : b.h;
    out[4] = st;
    c->pa_box = b;
    if (none) {
        c->pa_box.w = 0;
    }
    c->pa_st = (uint8_t)st;
}

int moy_ui_api_part_at(const moy_ui_ctx_t *c, int32_t out[2]) {
    moy_ui_box_t b = c->pa_box;
    int x, y;
    if ((c->pa_st & MOY_UI_ST_ACTIVATED) && c->tap_x != INT16_MIN) {
        x = c->tap_x;
        y = c->tap_y;
    } else if (c->pa_st & (MOY_UI_ST_PRESSED | MOY_UI_ST_HOVER)) {
        x = c->in[MOY_UI_IN_X];
        y = c->in[MOY_UI_IN_Y];
    } else {
        return 0;
    }
    if (b.w <= 0 || b.h <= 0 || !mua_inside(b, x, y)) {
        return 0;
    }
    out[0] = x - b.x;
    out[1] = y - b.y;
    return 1;
}

// -- ui.split ------------------------------------------------------------------------
//
// A split's kept state (its keep slot, by its id): `a` the divider held + 1
// (0 none), `b` where the press took it (along the split's axis), `gen` the
// size of the pane before it then, `mua_over` the position its gap is dragged
// to, `s0` whether the drag took (past the slop), `sn` the side it stops at
// + 1, `drawn` the focused id the split saw last.

// Divider gap `g`'s press box: `hw` across and `hl` along it, centred (0:
// the gap's).
static moy_ui_box_t split_hitbox(moy_ui_box_t g, int across, int hw, int hl) {
    moy_ui_box_t h = g;
    int16_t *ax = across ? &h.x : &h.y, *aw = across ? &h.w : &h.h;
    int16_t *lx = across ? &h.y : &h.x, *lw = across ? &h.h : &h.w;
    if (hw > 0) {
        *ax = (int16_t)(*ax + (*aw - hw) / 2);
        *aw = (int16_t)hw;
    }
    if (hl > 0) {
        *lx = (int16_t)(*lx + (*lw - hl) / 2);
        *lw = (int16_t)hl;
    }
    return h;
}

// The panes of split rect `r` at `sizes`: each one's start and length along
// the axis, and its rect at out.
static void split_lay(moy_ui_box_t r, int across, int gap, const int32_t *sizes, int n,
                      int32_t *at, int32_t *sz, int32_t *out) {
    int o0 = across ? r.x : r.y, len = across ? r.w : r.h;
    int p0 = o0;
    for (int i = 0; i < n; i++) {
        int v = i < n - 1 ? sizes[i] : o0 + len - p0;
        at[i] = p0;
        sz[i] = v < 0 ? 0 : v;
        moy_ui_box_t b = across ? (moy_ui_box_t){(int16_t)p0, r.y, (int16_t)sz[i], r.h}
                                : (moy_ui_box_t){r.x, (int16_t)p0, r.w, (int16_t)sz[i]};
        out[4 * i] = b.x;
        out[4 * i + 1] = b.y;
        out[4 * i + 2] = b.w;
        out[4 * i + 3] = b.h;
        p0 += sz[i] + gap;
    }
}

// The panes split `r` cuts, recorded for the split frame: a pane recorded
// before that holds `r` is cut again (a split inside it), no pane of its
// own; `cur` (-1 none) is the one the keys reach.
static void split_note(moy_ui_ctx_t *c, moy_ui_box_t r, int n, const int32_t *out, int cur) {
    for (int j = 0; j < c->sp_pn; j++) {
        moy_ui_box_t p = c->sp_pane[j];
        if (r.x >= p.x && r.y >= p.y && r.x + r.w <= p.x + p.w && r.y + r.h <= p.y + p.h) {
            c->sp_cut = (uint16_t)(c->sp_cut | (1u << j));
        }
    }
    for (int i = 0; i < n && c->sp_pn < MOY_UI_API_PANES; i++) {
        if (i == cur) {
            c->sp_cur = (int8_t)c->sp_pn;
        }
        c->sp_pane[c->sp_pn++] = (moy_ui_box_t){(int16_t)out[4 * i], (int16_t)out[4 * i + 1],
                                                (int16_t)out[4 * i + 2], (int16_t)out[4 * i + 3]};
    }
}

int moy_ui_api_split(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int32_t *sizes,
                     int n, const int32_t *mins, const int32_t *opt, int32_t *out) {
    if (n < 1 || n > MOY_UI_SPLIT_MAX) {
        return -1;
    }
    moy_ui_box_t r;
    if (c->has_at) {
        r = c->at;
        c->has_at = 0;
    } else {
        int top = c->page.y + c->y - c->scroll, bottom = c->page.y + c->page.h;
        r = (moy_ui_box_t){c->page.x, (int16_t)top, c->page.w,
                           (int16_t)(bottom > top ? bottom - top : 0)};
    }
    int flags = opt[MOY_UI_SPO_FLAGS];
    int across = !(flags & MOY_UI_SPLIT_Y);
    int drag = (flags & MOY_UI_SPLIT_DRAG) != 0, focus = (flags & MOY_UI_SPLIT_FOCUS) != 0;
    int gap = opt[MOY_UI_SPO_GAP] > 0 ? opt[MOY_UI_SPO_GAP] : drag ? 6 * c->fs : 0;
    int32_t at[MOY_UI_SPLIT_MAX], sz[MOY_UI_SPLIT_MAX];
    split_lay(r, across, gap, sizes, n, at, sz, out);
    int32_t *w = out + 4 * n;
    w[0] = -1;
    w[1] = 0;
    w[2] = -1;
    w[3] = 0;
    int cur = focus ? opt[MOY_UI_SPO_CUR] : -1;
    if (!drag && !focus) {
        return -1;
    }
    moy_ui_keep_t *k = mua_keep(c, mua_hash(id.p, id.n, 0x5911u), 1);
    int p = c->in[MOY_UI_IN_PTR], px = c->in[MOY_UI_IN_X], py = c->in[MOY_UI_IN_Y];
    int live = (p & MOY_UI_P_LIVE) != 0, held = live && (p & MOY_UI_P_HELD);
    int press = held && (p & MOY_UI_P_EDGE) && !c->modal;
    int hw = opt[MOY_UI_SPO_HIT_W], hl = opt[MOY_UI_SPO_HIT_L];
    int took = 0;
    if (drag) {
        if (k != NULL && press && k->a == 0) {
            for (int i = 0; i < n - 1; i++) {
                int ga = at[i] + sz[i];
                moy_ui_box_t g = across ? (moy_ui_box_t){(int16_t)ga, r.y, (int16_t)gap, r.h}
                                        : (moy_ui_box_t){r.x, (int16_t)ga, r.w, (int16_t)gap};
                if (gap > 0 && mua_inside(split_hitbox(g, across, hw, hl), px, py)) {
                    k->a = i + 1;
                    k->b = across ? px : py;
                    k->gen = sz[i];
                    k->over = (int16_t)ga;
                    k->s0 = (uint8_t)(opt[MOY_UI_SPO_SLOP] <= 0);
                    k->sn = 0;
                    took = 1;
                    break;
                }
            }
        }
        if (k != NULL && k->a > 0 && k->a < n) {
            int i = k->a - 1;
            int moved = (across ? px : py) - k->b;
            if (!k->s0 && (moved >= opt[MOY_UI_SPO_SLOP] || -moved >= opt[MOY_UI_SPO_SLOP])) {
                k->s0 = 1;
            }
            int lo = mins != NULL ? mins[i] : 0;
            int hi = sz[i] + sz[i + 1] - (mins != NULL ? mins[i + 1] : 0);
            hi = hi > lo ? hi : lo;
            int want = k->gen + moved, size = want < lo ? lo : want > hi ? hi : want;
            if (live) {
                k->over = (int16_t)(at[i] + size);
                k->sn = (uint8_t)(want <= lo ? 1 : want >= hi ? 2 : 0);
            }
            w[0] = i;
            w[1] = k->over;
            w[2] = (int)k->sn - 1;
            w[3] = k->s0 ? MOY_UI_SPD_LIVE : 0;
            c->sp_held = (uint8_t)(c->sp_held | held);
            if (!held) {
                w[3] |= MOY_UI_SPD_LET_GO;
                if (k->s0) {
                    int pair = sz[i] + sz[i + 1];
                    int got = k->over - at[i];
                    sizes[i] = got;
                    sizes[i + 1] = pair - got;
                    split_lay(r, across, gap, sizes, n, at, sz, out);
                }
                k->a = 0;
            }
        } else if (k != NULL) {
            k->a = 0;
        }
    }
    split_note(c, r, n, out, cur);
    if (focus) {
        if (press && !took) {
            for (int i = 0; i < n; i++) {
                if (mua_inside((moy_ui_box_t){(int16_t)out[4 * i], (int16_t)out[4 * i + 1],
                                          (int16_t)out[4 * i + 2], (int16_t)out[4 * i + 3]},
                               px, py)) {
                    cur = i;
                    break;
                }
            }
        } else if (k != NULL && c->focus != 0 && (uint32_t)k->drawn != c->focus) {
            int fx = c->focus_box.x + c->focus_box.w / 2, fy = c->focus_box.y + c->focus_box.h / 2;
            for (int i = 0; i < n; i++) {
                if (mua_inside((moy_ui_box_t){(int16_t)out[4 * i], (int16_t)out[4 * i + 1],
                                          (int16_t)out[4 * i + 2], (int16_t)out[4 * i + 3]},
                               fx, fy)) {
                    cur = i;
                    break;
                }
            }
        }
        if (k != NULL) {
            k->drawn = (int32_t)c->focus;
        }
    }
    if (drag && gap > 0) {
        int dw = opt[MOY_UI_SPO_HANDLE_W] > 0 ? opt[MOY_UI_SPO_HANDLE_W] : gap;
        int dl = opt[MOY_UI_SPO_HANDLE_L] > 0 ? opt[MOY_UI_SPO_HANDLE_L] : 6 * gap;
        int line = opt[MOY_UI_SPO_LINE] > 0 ? opt[MOY_UI_SPO_LINE] : c->fs;
        for (int i = 0; i < n - 1; i++) {
            int ga = at[i] + sz[i];
            moy_ui_box_t g = across ? (moy_ui_box_t){(int16_t)ga, r.y, (int16_t)gap, r.h}
                                    : (moy_ui_box_t){r.x, (int16_t)ga, r.w, (int16_t)gap};
            if (c->sp_n < MOY_UI_API_DIVS) {
                c->sp_hit[c->sp_n++] = split_hitbox(g, across, hw, hl);
            }
            clip_t kc;
            EACH_PIECE(c, kc, s, g) {
                moy_ui_divider(s, g, across, dw, dl, 0, 0, line);
            }
            if (w[0] == i && (w[3] & MOY_UI_SPD_LIVE) && !(w[3] & MOY_UI_SPD_LET_GO)) {
                // the line where the drag takes the gap, which may lie past it
                int l0 = w[1] + ((across ? g.w : g.h) - line) / 2;
                moy_ui_box_t lb = across ? (moy_ui_box_t){(int16_t)l0, g.y, (int16_t)line, g.h}
                                         : (moy_ui_box_t){g.x, (int16_t)l0, g.w, (int16_t)line};
                EACH_PIECE(c, kc, s, lb) {
                    moy_ui_divider(s, g, across, 0, 0, 1, w[1], line);
                }
            }
        }
    }
    return cur;
}

int moy_ui_api_split_at(const moy_ui_ctx_t *c, int x, int y) {
    for (int i = 0; i < c->sp_n; i++) {
        if (mua_inside(c->sp_hit[i], x, y)) {
            return 1;
        }
    }
    return 0;
}

int moy_ui_api_split_frame(moy_ui_ctx_t *c, int32_t gen, int known, int still, int top,
                           const int32_t lag[4], int32_t out[4]) {
    int whole = gen == MOY_UI_SPLIT_WHOLE;
    int same = !whole && gen == c->sf_gen;
    c->sf_gen = gen;
    int i = c->sp_cur;
    int32_t pop[4];
    if (!same || !known || !still || i < 0 || i >= c->sp_pn || (c->sp_cut & (1u << i))
        || c->sp_held || moy_ui_api_pop_box(c, pop) || moy_ui_api_owes(c)
        || moy_ui_api_moving(c)) {
        return MOY_UI_SF_WHOLE;
    }
    moy_ui_box_t p = c->sp_pane[i];
    int t = top < 0 ? 0 : top > p.h ? p.h : top;
    out[0] = p.x;
    out[1] = p.y + t;
    out[2] = p.w;
    out[3] = p.h - t;
    if (lag[2] > lag[0] && lag[3] > lag[1]
        && (lag[0] < out[0] || lag[1] < out[1] || lag[2] > out[0] + out[2]
            || lag[3] > out[1] + out[3])) {
        return MOY_UI_SF_LAGS;
    }
    return MOY_UI_SF_ALONE;
}

int moy_ui_api_split_toward(const moy_ui_ctx_t *c, int x, int y, int dx, int dy,
                            int32_t out[4]) {
    int i = -1;
    for (int j = 0; j < c->sp_pn && i < 0; j++) {
        if (!(c->sp_cut & (1u << j)) && mua_inside(c->sp_pane[j], x, y)) {
            i = j;
        }
    }
    if (i < 0) {
        return 0;
    }
    moy_ui_box_t f = c->sp_pane[i];
    int cx = f.x + f.w / 2, cy = f.y + f.h / 2;
    int best = -1, bk = 0;
    for (int j = 0; j < c->sp_pn; j++) {
        if (j == i || (c->sp_cut & (1u << j))) {
            continue;
        }
        moy_ui_box_t p = c->sp_pane[j];
        int px = p.x + p.w / 2 - cx, py = p.y + p.h / 2 - cy;
        int along = px * dx + py * dy, across = px * dy + py * dx;
        if (along <= 0) {
            continue;
        }
        int k = along + 2 * (across < 0 ? -across : across);
        if (best < 0 || k < bk) {
            best = j;
            bk = k;
        }
    }
    if (best < 0) {
        return 0;
    }
    moy_ui_box_t p = c->sp_pane[best];
    out[0] = p.x;
    out[1] = p.y;
    out[2] = p.w;
    out[3] = p.h;
    return 1;
}

int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap) {
    if (id.p != NULL) {
        uint32_t h = mua_hash(id.p, id.n, 0);
        if (h != c->focus) {
            c->focus = h;
            c->reveal = 1;
            c->jump = 1;                // an app's focus move is no step: it shows at once
            c->owe = 1;                 // the next frame reveals it
            int i = mua_find(c->cur, c->n_cur, h);
            if (i < 0) {
                i = mua_find(c->prev, c->n_prev, h);
                if (i >= 0) {
                    c->focus_box = c->prev[i].b;
                }
            } else {
                c->focus_box = c->cur[i].b;
            }
        }
        name_copy(c->focus_name, id);
    }
    if (!c->focus) {
        if (cap) {
            buf[0] = 0;
        }
        return -1;
    }
    size_t n = strlen(c->focus_name);
    if (cap) {
        size_t m = n < cap - 1 ? n : cap - 1;
        memcpy(buf, c->focus_name, m);
        buf[m] = 0;
    }
    return (int)n;
}

void moy_ui_api_focus_ahead(moy_ui_ctx_t *c, moy_ui_span_t id) {
    uint8_t owe = c->owe;
    moy_ui_api_focus(c, id, NULL, 0);
    c->owe = owe;
}

int moy_ui_api_keys_taken(const moy_ui_ctx_t *c) {
    return c->keys;
}

void moy_ui_api_keys_back(moy_ui_ctx_t *c) {
    c->elsewhere = 0;
}

int moy_ui_api_edge(const moy_ui_ctx_t *c) {
    return c->edge;
}

int moy_ui_api_tap_armed(const moy_ui_ctx_t *c) {
    return c->press != 0 && c->gesture != 2;
}

void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h) {
    c->at.x = (int16_t)x;
    c->at.y = (int16_t)y;
    c->at.w = (int16_t)(w > 0 ? w : 0);
    c->at.h = (int16_t)(h > 0 ? h : 0);
    c->has_at = 1;
}

int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]) {
    if (c->pop_id != 0 && c->pop_card.w > 0 && mua_hash(id.p, id.n, POPOVER_SALT) == c->pop_id) {
        out[0] = c->pop_card.x;
        out[1] = c->pop_card.y;
        out[2] = c->pop_card.w;
        out[3] = c->pop_card.h;
        return 1;
    }
    if (c->dlg_id != 0 && mua_hash(id.p, id.n, DIALOG_SALT) == c->dlg_id) {
        out[0] = c->dlg_card.x;
        out[1] = c->dlg_card.y;
        out[2] = c->dlg_card.w;
        out[3] = c->dlg_card.h;
        return 1;
    }
    int i = mua_find(c->cur, c->n_cur, mua_hash(id.p, id.n, 0));
    if (i < 0 && c->dlg_id != 0) {
        i = mua_find(c->cur, c->n_cur, mua_hash(id.p, id.n, c->dlg_id));   // a button at its foot
    }
    if (i < 0) {
        return 0;
    }
    out[0] = c->cur[i].b.x;
    out[1] = c->cur[i].b.y;
    out[2] = c->cur[i].b.w;
    out[3] = c->cur[i].b.h;
    return 1;
}

int moy_ui_api_lost(moy_ui_ctx_t *c) {
    if (c->lost == c->lost_told) {
        return 0;
    }
    c->lost_told = c->lost;
    return c->lost;
}

const char *moy_ui_api_dup(moy_ui_ctx_t *c) {
    if (c->dup == 0 || c->dup == c->dup_told) {
        return NULL;
    }
    c->dup_told = c->dup;
    return c->dup_name;
}

// -- nests and scroll regions (tables, fields, dialogs: moy_ui_api_table.c) -----------

int mua_pmetric(const moy_ui_ctx_t *c, int slot) {
    return mua_kmetric(MOY_UI_K_VALUE_ROW, slot + (c->cls ? 1 : 0)) * c->fs;
}

// Open a nested page (a scroll region, kind 1; a dialog's body, kind 2; a
// popover's, kind 3) in
// box b, the outer one kept until it closes.
void mua_nest_open(moy_ui_ctx_t *c, int kind, uint32_t id, moy_ui_box_t b, int used,
                   int flags, const moy_ui_over_t *o) {
    moy_ui_box_t outer = c->page;
    moy_ui_nest_t *n = &c->nest[c->n_nest++];
    n->page = c->page;
    n->y = c->y;
    n->scroll = c->scroll;
    n->keep = c->page_keep;
    n->stop = c->page_stop;
    n->kind = (uint8_t)kind;
    n->used = (int16_t)used;
    open_page(c, id, b, flags | (kind >= 2 || mua_in_dialog(c) ? STOP_MODAL : 0)
                        | (kind >= 2 ? STOP_CARD : 0), o, outer);
}

// A list's edges in view `v`: where rows `rows` high scrolled by `scroll`
// run on below it, a fade over its last `h` pixels toward colour `ci`;
// with `head`, where rows are scrolled off above it, one over its first.
uint32_t moy_ui_api_fades(moy_ui_inst_t *I, int reset) {
    if (I == NULL) {
        I = &s_main;
    }
    uint32_t n = I->fades;
    if (reset) {
        I->fades = 0;
    }
    return n;
}

void mua_edge_fades(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t v, int rows,
                    int scroll, int ci, int head, int h) {
    if (h > v.h) {
        h = v.h;
    }
    clip_t k;
    if (rows > scroll + v.h) {
        moy_ui_box_t b = {v.x, (int16_t)(v.y + v.h - h), v.w, (int16_t)h};
        EACH_PIECE(c, k, s, b) {
            moy_ui_fade(s, b, ci, 0, c->fs, mua_ground_of(c));
        }
        c->inst->fades++;
    }
    if (head && scroll > 0) {
        moy_ui_box_t b = {v.x, v.y, v.w, (int16_t)h};
        EACH_PIECE(c, k, s, b) {
            moy_ui_fade(s, b, ci, 1, c->fs, mua_ground_of(c));
        }
        c->inst->fades++;
    }
}

void mua_thumb(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t v, int rows,
               int scroll) {
    if (rows <= v.h) {
        return;
    }
    clip_t k;
    EACH_PIECE(c, k, s, v) {
        moy_ui_thumb(s, v, rows, scroll, c->fs, mua_ground_of(c));
    }
}

// The page's (or a scroll region's) edges, its groups closed: where its rows
// run on below what shows, a fade over its last FADE_H rows of the scale,
// and where they are scrolled off above it, one over its first.

void mua_foot_fade(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = mua_page_keep(c);
    if (k != NULL && !c->no_foot) {
        mua_edge_fades(c, s, c->page, k->b, c->scroll, MOY_UI_DEFAULT, 0, FADE_H * c->fs);
    }
}

// Close the innermost nested page where it is of `kind`: answers the height
// its rows took.
int mua_nest_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind) {
    if (c->n_nest == 0 || c->nest[c->n_nest - 1].kind != kind) {
        return -1;
    }
    mua_group_close(c, s);
    if (kind == 1) {
        mua_foot_fade(c, s);
        // a thumb where the region never shifts: one under the app's gen
        // shifts its rows on a shift frame, and the thumb is no row
        moy_ui_keep_t *kk = mua_page_keep(c);
        if (kk != NULL && !c->no_foot && !(kk->kd & MOY_UI_KD_GEN)) {
            mua_thumb(c, s, c->page, kk->b, c->scroll);
        }
    } else if (kind == 3 && mua_page_keep(c) != NULL) {
        // a popover's body cut to its room: a fade toward the card at each
        // edge its rows run on past
        mua_edge_fades(c, s, c->page, mua_page_keep(c)->b, c->scroll, MOY_UI_FADE_CARD, 1,
                       FADE_H * c->fs);
    }
    mua_page_close(c, s);
    if (kind >= 2 && c->fl_susp) {
        c->fl_susp--;
    }
    int took = c->y;
    moy_ui_nest_t *n = &c->nest[--c->n_nest];
    c->page = n->page;
    c->y = (int16_t)(n->y + (kind == 1 ? n->used : 0));
    c->scroll = n->scroll;
    c->page_keep = n->keep;
    c->page_stop = n->stop;
    return took;
}

int32_t moy_ui_api_scroll(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                          const moy_ui_over_t *o) {
    o = mua_over(o);
    if (id.n == 0) {
        mua_nest_close(c, s, 1);
        return 0;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
    }
    mua_group_close(c, s);
    moy_ui_box_t b;
    int used = 0;
    int placed = c->has_at;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    } else {
        int top = c->page.y + c->y - c->scroll;
        int bottom = c->page.y + c->page.h;
        b = (moy_ui_box_t){c->page.x, (int16_t)top, c->page.w,
                           (int16_t)(bottom > top ? bottom - top : 0)};
        used = b.h;
    }
    mua_nest_open(c, 1, mua_hash(id.p, id.n, SCROLL_SALT), b, used,
                  (o->nav == 0 ? STOP_NAV_OFF : 0) | (placed ? STOP_AT : 0), o);
    return 0;
}
