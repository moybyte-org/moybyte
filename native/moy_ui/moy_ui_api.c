// The public UI API's engine (moy_ui_api.h; docs/ui_api_2026-10.md §2).

#include <string.h>

#include "moy_ui_api.h"
#include "moy_canvas.h"
#include "moy_theme.h"

#define MAGIC 0x6d756931u           // "mui1"
#define PAGE_SALT 0x9e3779b9u
#define SCROLL_SALT 0x85ebca6bu
#define DIALOG_SALT 0xc2b2ae35u
#define POPOVER_SALT 0x27d4eb2fu
#define NO_PAGE 0xffu
// A fade's rows at the scale: its first two barely tint, so ten read as
// D-09's eight (docs/look_2026-10.md).
#define FADE_H 10

// A stop's flags.
enum {
    STOP_PAGE = 1,          // a page's content rect: never focused, what a drag scrolls
    STOP_KEEP_LR = 2,       // a slider: left and right are its own
    STOP_KEEP_UD = 4,       // a list: up and down are its own...
    STOP_AT_TOP = 8,        // ...but up at its first row
    STOP_AT_END = 16,       // ...and down at its last
    STOP_HOLD = 32,         // nav = false on a part: focus stays, the keys are the cart's
    STOP_NAV_OFF = 64,      // nav = false on a page: the engine moves no focus in it
    STOP_MODAL = 128,       // a dialog's or a popover's: while one is drawn, the others take no input
    STOP_DLG_BTN = 256,     // a dialog's button (focus opens on its body first)
    STOP_FIELD = 512,       // a text field: it takes the typed keys, and `a` is a letter
    STOP_ROWS = 1024,       // a list's or a table's rows on its page: `row` and `top` hold
    STOP_CARD = 2048,       // a dialog's or a popover's body: its scroll into view lands at once
    STOP_AT = 4096,         // placed by ui.at
    STOP_PIC = 8192,        // the app draws a picture in it (a card, a part)
    STOP_GEN = 16384,       // ...whose gen it gave
    STOP_GRID = 32768,      // a card of a grid
};

static uint32_t hash(const uint8_t *p, size_t n, uint32_t salt) {
    uint32_t h = 2166136261u ^ salt;
    for (size_t i = 0; i < n; i++) {
        h = (h ^ p[i]) * 16777619u;
    }
    return h ? h : 1u;
}

static int kmetric(int kind, int slot) {
    int n = 0;
    const int16_t *m = moy_ui_metrics(kind, &n);
    return m != NULL && slot < n ? m[slot] : 0;
}

static int metric(int slot) {
    return kmetric(MOY_UI_K_ROW, slot);
}

static int row_h(const moy_ui_ctx_t *c) {
    return metric(c->cls ? MOY_UI_M_ROW_STACK_DESK : MOY_UI_M_ROW_STACK) * c->fs;
}

static int inside(moy_ui_box_t b, int x, int y) {
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
}

static int32_t word(const uint8_t *w, int i) {
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
    o->fill = word(w, MOY_UI_O_FILL);
    o->ink = word(w, MOY_UI_O_INK);
    o->border = word(w, MOY_UI_O_BORDER);
    int32_t idn = word(w, MOY_UI_O_ID);
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
                }
            }
        }
    }
    o->current = flag(word(w, MOY_UI_O_CURRENT));
    o->disabled = flag(word(w, MOY_UI_O_DISABLED));
    o->nav = flag(word(w, MOY_UI_O_NAV));
    o->mark = flag(word(w, MOY_UI_O_MARK));
    o->steps = flag(word(w, MOY_UI_O_STEPS));
    return MOY_UI_OK;
}

static const moy_ui_over_t *over(const moy_ui_over_t *o) {
    static const moy_ui_over_t none = {MOY_UI_O_NONE, MOY_UI_O_NONE, MOY_UI_O_NONE, {NULL, 0},
                                       -1, -1, -1, -1, -1, {NULL, 0}, -1, -1, 0, 0};
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

void moy_ui_api_reset(moy_ui_ctx_t *c) {
    memset(c, 0, sizeof(*c));
    c->magic = MAGIC;
    c->fs = 1;
    c->page_keep = -1;
    c->page_stop = NO_PAGE;
    c->icon_slot = c->icon_glyph = c->row_slot = c->row_glyph = -1;
    c->dlg_pick = -1;
    c->bar_act = -1;
    c->bar_chip_at = -1;
}

moy_ui_ctx_t *moy_ui_api_ctx(void *p, size_t n) {
    if (p == NULL || n < sizeof(moy_ui_ctx_t) || ((uintptr_t)p & 3u) != 0u) {
        return NULL;
    }
    moy_ui_ctx_t *c = (moy_ui_ctx_t *)p;
    if (c->magic != MAGIC) {
        moy_ui_api_reset(c);
    }
    return c;
}

// -- the previous frame's stops ------------------------------------------------------

static int find(const moy_ui_stop_t *v, int n, uint32_t id) {
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
        if (!(v[i].flags & STOP_PAGE) && inside(v[i].b, x, y)) {
            return i;
        }
    }
    return -1;
}

static int page_at(const moy_ui_stop_t *v, int n, int x, int y) {
    for (int i = n - 1; i >= 0; i--) {
        if ((v[i].flags & STOP_PAGE) && inside(v[i].b, x, y)) {
            return i;
        }
    }
    return -1;
}

static int gap(int a0, int a1, int b0, int b1) {
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
            across = gap(a.x, a.x + a.w, b.x, b.x + b.w);
            off = cx > acx ? cx - acx : acx - cx;
        } else {
            if (dir == MOY_UI_B_RIGHT ? cx <= acx : cx >= acx) {
                continue;
            }
            along = dir == MOY_UI_B_RIGHT ? b.x - (a.x + a.w) : a.x - (b.x + b.w);
            across = gap(a.y, a.y + a.h, b.y, b.y + b.h);
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

static moy_ui_keep_t *keep(moy_ui_ctx_t *c, uint32_t id, int make) {
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

static moy_ui_keep_t *keep_of(moy_ui_ctx_t *c, uint32_t id) {
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
// the page's foot fades (foot_fade): the box stands above that fade, as far
// as the rows go on.
// The span page `p`'s scroll ranges over: its rows' height (and the room a
// scroll into view past its rows keeps) and what shows of them, the page's
// stop, which a hint band at its foot has already given up (hints).
static void span_of(const moy_ui_stop_t *p, const moy_ui_keep_t *k, int32_t *content,
                    int32_t *extent) {
    *content = k->b + (k->over > 0 ? k->over : 0);
    *extent = p->b.h;
}

// The page stop kept as `k` among the frame's stops, or NULL.
static const moy_ui_stop_t *page_of_keep(const moy_ui_stop_t *v, int n,
                                         const moy_ui_keep_t *k) {
    for (int i = 0; i < n; i++) {
        if (v[i].id == k->id && (v[i].flags & STOP_PAGE)) {
            return &v[i];
        }
    }
    return NULL;
}

// Glide page `p`'s scroll to `to`: 1 where that moves it.
static int aim(moy_ui_keep_t *k, const moy_ui_stop_t *p, int32_t to);

// The row a placed table's key step lands on (`t` the table's stop as the
// last frame drew it), its rows' scroll aimed there at begin as the table's
// pass would aim it, so the frame's plan knows the move.
static void table_reveal(moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n,
                         const moy_ui_stop_t *t) {
    moy_ui_keep_t *k = keep_of(c, t->id);
    if (k == NULL || !(k->kd & MOY_UI_KD_TABLE) || k->rh <= 0) {
        return;
    }
    const moy_ui_stop_t *p = page_of_keep(v, n, k);
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

static int aim(moy_ui_keep_t *k, const moy_ui_stop_t *p, int32_t to) {
    int32_t content, extent;
    span_of(p, k, &content, &extent);
    moy_ui_scroll_glide_to(&k->sc, k->a, to, 0, content, extent);
    return moy_ui_scroll_moving(&k->sc);
}

// Scroll page `p` so box `b` (as drawn last frame) shows where the page's
// scroll is going (its glide's target while one is live): the glide aims
// there (aim). 1 where the target moved.
static int reveal(moy_ui_ctx_t *c, const moy_ui_stop_t *p, int page, moy_ui_box_t b) {
    moy_ui_keep_t *k = keep_of(c, p->id);
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
    aim(k, p, to);
    return 1;
}

// -- a frame -------------------------------------------------------------------------

static void floor_build(moy_ui_ctx_t *c, int p0, int n, int of);
static int floor_reach(moy_ui_ctx_t *c, int p0, int n, int base, int rh, int pic);
static int mark_room(const moy_ui_ctx_t *c);
static void table_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                        int mark, int pic, int of);

// A placed table's own stop (not its rows' view) among the frame's stops.
static const moy_ui_stop_t *table_of(const moy_ui_stop_t *v, int n, uint32_t id) {
    for (int i = 0; i < n; i++) {
        if (v[i].id == id && !(v[i].flags & STOP_PAGE)) {
            return &v[i];
        }
    }
    return NULL;
}

static int same_box(moy_ui_box_t a, moy_ui_box_t b) {
    return a.x == b.x && a.y == b.y && a.w == b.w && a.h == b.h;
}

// The frame begin just resolved, as a shift frame (docs/scroll_and_text_
// 2026-10.md §3.3): which page R it moves and by how much, where the input
// it applied and the last pass license one. `owed` the last pass owed this
// frame, `over` it drew something over its page, `flags` begin's.
static void dialog_plan(moy_ui_ctx_t *c, int owed, int same);

static void shift_plan(moy_ui_ctx_t *c, int owed, int over, int flags) {
    c->sh_plan = MOY_UI_SHIFT_NONE;
    c->sh_id = 0;
    c->sh_rows = c->sh_pics = 0;
    c->sh_dlg = 0;
    int p = c->in[MOY_UI_IN_PTR];
    int live = p & MOY_UI_P_LIVE, held = live && (p & MOY_UI_P_HELD);
    int same = c->was_flags == (uint16_t)(flags & ~MOY_UI_KEY_EDGE) && c->was_fs == c->fs
               && c->was_hint == (uint8_t)moy_ui_hint_input()
               && same_box(c->was_surface, c->surface);
    if (c->modal) {
        dialog_plan(c, owed, same);
        return;
    }
    // a press, an activation, a stepped value, a hover, an overlay: whole
    if (!same || owed || over || c->modal || c->pop_prev.w > 0 || c->pop_gone || c->act
        || c->step_x || (p & MOY_UI_P_EDGE) || (live && !held)
        || (held && c->gesture != 2) || (p & MOY_UI_P_MIDDLE)) {
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
        int ok = (k->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD))
                 == (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN);
        const moy_ui_stop_t *ps = ok ? page_of_keep(v, n, k) : NULL;
        if (ps != NULL && (int32_t)ps->b.w * ps->b.h > area) {
            area = (int32_t)ps->b.w * ps->b.h;
            best = k;
        }
    }
    if (moved > 1) {
        return;
    }
    if (r == NULL) {
        r = best;
    }
    if (r == NULL || (r->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN | MOY_UI_KD_BAD))
                         != (MOY_UI_KD_DRAWN | MOY_UI_KD_GEN)) {
        return;
    }
    if (held && c->drag_page != r->id) {
        return;                         // a drag of another page
    }
    int table = (r->kd & MOY_UI_KD_TABLE) != 0;
    if (c->step_y && !(table && c->focus == r->id)) {
        return;                         // a row step but a placed table's on its own rows
    }
    const moy_ui_stop_t *rp = page_of_keep(v, n, r);
    int dy = (int)(r->drawn - r->a);
    if (rp == NULL || rp->b.w <= 0 || rp->b.h <= 0 || (dy < 0 ? -dy : dy) >= rp->b.h
        || r->s0 + r->sn > n) {
        return;
    }
    c->sh_id = r->id;
    c->sh_r = rp->b;
    c->sh_dy = (int16_t)dy;
    c->sh_head = 0;
    c->fl_early = 0;
    // the floor this input asks for, as R's opening will build it: where it
    // is most of R, moving the rest costs more than it saves
    c->fl_n = 0;
    const moy_ui_stop_t *tp = table ? table_of(v, n, r->id) : NULL;
    if (table && (tp == NULL || r->rh <= 0)) {
        return;
    }
    int pic = tp != NULL && (tp->flags & STOP_PIC);
    if (tp != NULL) {
        // the rows the step lands on, as the table's pass will find them
        int count = r->b / r->rh, now = tp->row;
        int mark = (tp->st & MOY_UI_ST_FOCUS) != 0, will = c->focus == r->id && !c->elsewhere;
        if (c->step_y && now >= 0) {
            now += c->step_y;
            now = now >= count ? count - 1 : now;
            now = now < 0 ? 0 : now;
        }
        table_floor(c, rp->b.y - r->a, r->rh, tp->row, mark, now, will, pic,
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
        return;
    }
    c->sh_rows = (int16_t)rows;
    c->sh_pics = (int16_t)(pics > 32767 ? 32767 : pics);
    c->sh_plan = dy ? MOY_UI_SHIFT_MOVE : MOY_UI_SHIFT_ROWS;
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
    c->cls = (uint8_t)((flags & MOY_UI_DESK) ? 1 : 0);
    c->win = (uint8_t)((flags & MOY_UI_WINDOW) ? 1 : 0);
    c->elsewhere = (uint8_t)((flags & MOY_UI_ELSEWHERE) ? 1 : 0);
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
    // the dialog the last frame drew, for this frame's plan, and the frames
    // it has stood as it is
    int dw = c->dlg_id != 0 && c->dlg_held && !c->fl_top && !c->fl_mute;
    int stood = dw && c->dw_n > 0 && c->dlg_id == c->dw_id && same_box(c->dlg_card, c->dw_card)
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
        if (!c->elsewhere && (c->focus == 0 || find(v, n, c->focus) < 0)) {
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
        if (!c->elsewhere && (c->focus == 0 || find(v, n, c->focus) < 0)) {
            int i = find(v, n, c->modal_from);
            if (i >= 0) {
                c->focus = v[i].id;
                c->focus_box = v[i].b;
                c->focus_name[0] = 0;
            }
        }
        c->modal_from = 0;
    }

    // Focus on an id no longer drawn passes to the nearest stop.
    int fi = c->focus ? find(v, n, c->focus) : -1;
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
            int rh = row_h(c);
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
        if (c->modal && c->pop_prev.w > 0 && !inside(c->pop_prev, x, y)) {
            c->pop_gone = 1;        // a press outside a popover dismisses it
        }
        int h = stop_at(v, n, x, y);
        c->press = h >= 0 ? v[h].id : 0;
        int pg = page_at(v, n, x, y);
        moy_ui_keep_t *pk = pg >= 0 ? keep_of(c, v[pg].id) : NULL;
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
            int h = c->press ? find(v, n, c->press) : -1;
            int keep_lr = h >= 0 && (v[h].flags & STOP_KEEP_LR);
            int dx = x - c->press_x, dy = y - c->press_y;
            dx = dx < 0 ? -dx : dx;
            dy = dy < 0 ? -dy : dy;
            if (keep_lr && dx > slop && dx > dy) {
                c->gesture = 3;         // a slider's knob follows it; a stepper's release taps
                settled = 0;
            } else if (dy > slop) {
                int pg = page_at(v, n, c->press_x, c->press_y);
                c->drag_page = pg >= 0 ? v[pg].id : 0;
                c->press = 0;
                c->gesture = 2;
                moy_ui_keep_t *k = c->drag_page ? keep_of(c, c->drag_page) : NULL;
                if (k != NULL) {
                    moy_ui_scroll_drag_start(&k->sc, c->drag_y);
                }
            }
        }
        if (c->gesture == 2 && c->drag_page) {
            // the page follows the finger; every sample, a still one too,
            // feeds the release velocity
            moy_ui_keep_t *k = keep_of(c, c->drag_page);
            const moy_ui_stop_t *pp = k != NULL ? page_of_keep(v, n, k) : NULL;
            if (pp != NULL) {
                int32_t content, extent, off = k->a;
                if (y != c->drag_y) {
                    k->over = 0;
                }
                span_of(pp, k, &content, &extent);
                moy_ui_scroll_drag_move(&k->sc, y, (float)dt, &off, content, extent);
                if (off != k->a) {
                    k->a = off;
                    moved = 1;
                }
            }
            c->drag_y = (int16_t)y;
        }
    } else if (!held && c->gesture) {
        if (c->gesture == 2 && c->drag_page) {
            // a release flings the page on at the finger's speed
            moy_ui_keep_t *k = keep_of(c, c->drag_page);
            const moy_ui_stop_t *pp = k != NULL ? page_of_keep(v, n, k) : NULL;
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
        settled = 0;
    }
    // The wheel scrolls the page under the pointer; Shift turns it across.
    int wy = (in[MOY_UI_IN_MODS] & MOY_UI_MOD_SHIFT) ? 0 : in[MOY_UI_IN_WHEEL_Y];
    if (live && wy) {
        int pg = page_at(v, n, x, y);
        moy_ui_keep_t *k = pg >= 0 ? keep_of(c, v[pg].id) : NULL;
        if (k != NULL) {
            int32_t content, extent;
            k->over = 0;
            span_of(&v[pg], k, &content, &extent);
            moy_ui_scroll_step(&k->sc, k->a, wy, MOY_UI_WHEEL_ROWS * row_h(c), content, extent);
            moved = 1;
        }
    }
    // The middle button held drags the page it went down on by its travel.
    if (live && (p & MOY_UI_P_MIDDLE)) {
        if (!c->mid_page) {
            int pg = page_at(v, n, x, y);
            c->mid_page = pg >= 0 ? v[pg].id : 0xFFFFFFFFu;
        } else if (y != c->mid_y) {
            moy_ui_keep_t *k = keep_of(c, c->mid_page);
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
    if ((live && (x != c->last_x || y != c->last_y)) || live != was_live) {
        if (held || was_held || (live && stop_at(v, n, x, y) >= 0)
            || (was_live && stop_at(v, n, c->last_x, c->last_y) >= 0)) {
            settled = 0;
        }
    }
    c->last_x = (int16_t)x;
    c->last_y = (int16_t)y;
    c->last_ptr = p;

    // Keep the focused stop (or the list row it moved to) in view.
    if (c->reveal && c->focus) {
        int i = find(v, n, c->focus);
        if (i >= 0 && v[i].page != NO_PAGE && v[i].page < n) {
            moy_ui_box_t b = c->reveal_box.w ? c->reveal_box : v[i].b;
            int first = !c->reveal_box.w;
            for (int j = 0; first && j < n; j++) {
                first = j == i || v[j].page != v[i].page || (v[j].flags & STOP_PAGE)
                        || v[j].b.y >= b.y;
            }
            moy_ui_keep_t *pk = first ? keep_of(c, v[v[i].page].id) : NULL;
            if (pk != NULL && pk->a > 0
                && b.y + b.h + pk->a <= v[v[i].page].b.y + v[v[i].page].b.h) {
                aim(pk, &v[v[i].page], 0);   // the page's first stop shows the page's top
                moved = 1;
            } else if (reveal(c, &v[v[i].page], v[i].page, b)) {
                moved = 1;
            }
            // an app's focus move, and a card's body (a dialog, a popover: an
            // overlay over the page), show at once
            moy_ui_keep_t *rk = c->jump || (v[v[i].page].flags & STOP_CARD)
                                ? keep_of(c, v[v[i].page].id) : NULL;
            if (rk != NULL && (rk->sc.flags & MOY_UI_SCROLL_GLIDE)) {
                moy_ui_scroll_land(&rk->sc, &rk->a);
            }
        }
    }
    c->reveal = 0;
    c->jump = 0;
    c->reveal_box.w = 0;
    // Every page's motion steps on the frame's clock; with none, it lands.
    for (int i = 0; i < MOY_UI_API_KEEP; i++) {
        moy_ui_keep_t *k = &c->keep[i];
        if (k->id == 0 || !moy_ui_scroll_moving(&k->sc)) {
            continue;
        }
        const moy_ui_stop_t *pp = page_of_keep(v, n, k);
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
static int ground_of(const moy_ui_ctx_t *c) {
    return (c->cls ? MOY_UI_DESK : 0) | (c->win ? MOY_UI_WINDOW : 0);
}

static moy_ui_keep_t *page_keep(moy_ui_ctx_t *c) {
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
    int rh = row_h(c);
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
static moy_ui_box_t place_row(moy_ui_ctx_t *c, int h, int *stacked, int row) {
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
            int in = kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
            b.x = (int16_t)(b.x + in);
            b.w = (int16_t)(b.w - 2 * in);
        }
        gap = 0;
        c->grp_first = c->grp_n == 0;
        if (c->grp_n < 255) {
            c->grp_n++;
        }
        c->grp_last = b;
        c->grp_prev = c->grp_first ? 0 : c->grp_flags;
        c->grp_flags = 0;
    }
    if (row) {
        peek_note(c, c->y, h);
    }
    c->y = (int16_t)(c->y + h + gap);
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    return b;
}

static moy_ui_box_t place(moy_ui_ctx_t *c, int h, int *stacked) {
    return place_row(c, h, stacked, 1);
}

// -- the floor (docs/scroll_and_text_2026-10.md §3.3) ---------------------------------
//
// On a shift frame a host has moved the pixels the last frame drew in R by
// the scroll's move, and the pass draws only what that leaves wrong: the
// FLOOR, a list of disjoint rects inside R. Every draw goes through the
// frame's PIECES -- everything outside R (four rects about it), and the
// floor's rects once R is open -- once per piece it touches, clipped to it,
// so a pixel in the floor takes every draw over it in the order a whole
// frame draws them, and a pixel of R outside it keeps what the move left.

typedef struct {
    int32_t x0, y0, x1, y1;
} rc_t;

// The state bits whose marks a stop draws: a stop whose bits moved is
// drawn again on a shift frame.
#define MARKS (MOY_UI_ST_FOCUS | MOY_UI_ST_ACTIVATED | MOY_UI_ST_PRESSED | MOY_UI_ST_HOVER)

static uint32_t s_audit_on, s_audit_fail;

static rc_t rc_of(moy_ui_box_t b) {
    return (rc_t){b.x, b.y, b.x + b.w, b.y + b.h};
}

static int rc_cut(rc_t *a, rc_t b) {
    a->x0 = a->x0 > b.x0 ? a->x0 : b.x0;
    a->y0 = a->y0 > b.y0 ? a->y0 : b.y0;
    a->x1 = a->x1 < b.x1 ? a->x1 : b.x1;
    a->y1 = a->y1 < b.y1 ? a->y1 : b.y1;
    return a->x0 < a->x1 && a->y0 < a->y1;
}

static int rc_meets(rc_t a, rc_t b) {
    return rc_cut(&a, b);
}

static moy_ui_box_t box_of(rc_t r) {
    return (moy_ui_box_t){(int16_t)r.x0, (int16_t)r.y0, (int16_t)(r.x1 - r.x0),
                          (int16_t)(r.y1 - r.y0)};
}

// Whether the pass draws through the pieces now: a host armed the floor,
// and no dialog's or popover's body (an overlay, drawn whole) is open.
static int armed(const moy_ui_ctx_t *c) {
    return c->fl_on && !c->fl_susp;
}

// The pieces there are: four about R, then the floor once R is open (from
// the pass's start where a stop drawn before R reaches into it).
static int pieces(const moy_ui_ctx_t *c) {
    return 4 + (c->fl_stage || c->fl_early ? c->fl_n : 0);
}

static rc_t piece(const moy_ui_ctx_t *c, int i) {
    const int32_t big = 1 << 20;
    rc_t r = rc_of(c->sh_r);
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
            return rc_of(c->fl[i - 4]);
    }
}

// Whether box b lies wholly in the floor (its part inside R does).
static int floor_holds(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    rc_t q = rc_of(b);
    if (!rc_cut(&q, rc_of(c->sh_r))) {
        return 1;
    }
    int32_t want = (q.x1 - q.x0) * (q.y1 - q.y0), got = 0;
    for (int i = 0; i < c->fl_n; i++) {
        rc_t f = rc_of(c->fl[i]);
        if (rc_cut(&f, q)) {
            got += (f.x1 - f.x0) * (f.y1 - f.y0);
        }
    }
    return got >= want;
}

static int floor_meets(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    rc_t q = rc_of(b);
    for (int i = 0; i < c->fl_n; i++) {
        if (rc_meets(rc_of(c->fl[i]), q)) {
            return 1;
        }
    }
    return 0;
}

// The rect of the floor an app's picture in box b draws through: the one
// holding it, else the one it meets (R opening made a picture meet no more
// than one); w 0 where the frame draws it whole, w 0 and h -1 where it
// draws none of it.
static moy_ui_box_t floor_of(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    moy_ui_box_t none = {0, 0, 0, 0};
    if (!armed(c) || c->fl_stage != 1 || !rc_meets(rc_of(b), rc_of(c->sh_r))) {
        return none;
    }
    moy_ui_box_t met = {0, 0, 0, -1};
    for (int i = 0; i < c->fl_n; i++) {
        rc_t f = rc_of(c->fl[i]), q = rc_of(b);
        if (!rc_cut(&q, f)) {
            continue;
        }
        rc_t inr = rc_of(b);
        rc_cut(&inr, rc_of(c->sh_r));
        if (q.x0 == inr.x0 && q.y0 == inr.y0 && q.x1 == inr.x1 && q.y1 == inr.y1) {
            return c->fl[i];
        }
        met = c->fl[i];
    }
    if (floor_holds(c, b)) {
        rc_t q = rc_of(b);
        rc_cut(&q, rc_of(c->sh_r));
        return box_of(q);           // a picture drawn again: all of it, in pieces
    }
    return met;
}

// A picture in box b this frame's pass leaves as the move left it: inside R
// on a shift frame, and no rect of the floor reaches it.
static int outside_floor(const moy_ui_ctx_t *c, moy_ui_box_t b) {
    return armed(c) && c->fl_stage == 1 && rc_meets(rc_of(b), rc_of(c->sh_r))
           && !floor_meets(c, b);
}

// Box b, inside R, joins the floor: what of it the floor does not hold yet,
// as rects; `ground` marks them for the page's ground before anything draws
// over them.
static void floor_add(moy_ui_ctx_t *c, rc_t b, int ground) {
    rc_t q[32];
    int n = 0;
    if (!rc_cut(&b, rc_of(c->sh_r))) {
        return;
    }
    q[n++] = b;
    for (int i = 0; i < c->fl_n && n > 0; i++) {
        rc_t e = rc_of(c->fl[i]);
        rc_t out[32];
        int m = 0;
        for (int j = 0; j < n; j++) {
            rc_t a = q[j], x = a;
            if (!rc_cut(&x, e)) {
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
        c->fl[c->fl_n++] = box_of(q[j]);
    }
}

// The rows y0..y1 of R, across it.
static void floor_rows(moy_ui_ctx_t *c, int y0, int y1, int ground) {
    moy_ui_box_t r = c->sh_r;
    floor_add(c, (rc_t){r.x, y0, r.x + r.w, y1}, ground);
}

// Rows y0..y1 of a dialog's card join its rows frame's floor during the
// pass, each wanting the card under it (`ground_fill`), cut to its body's
// foot: a widening the card's safe band cannot hold mutes the dialog (it
// draws nothing more this frame and owes the next).
static void dlg_widen(moy_ui_ctx_t *c, int y0, int y1) {
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
    floor_add(c, (rc_t){r.x, y0, r.x + r.w, y1}, 1);
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
    rc_t q = rc_of(b);
    int n = 0;
    for (int i = 0; i < c->fl_n; i++) {
        n += rc_meets(rc_of(c->fl[i]), q);
    }
    return n;
}

// The page's ground over box b, clipped to it.
static void ground_fill(const moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b) {
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    int32_t *st = row->st, saved[4];
    memcpy(saved, &st[MOY_ST_CX0], sizeof(saved));
    rc_t q = rc_of(b);
    q.x0 -= st[MOY_ST_CAM_X];
    q.x1 -= st[MOY_ST_CAM_X];
    q.y0 -= st[MOY_ST_CAM_Y];
    q.y1 -= st[MOY_ST_CAM_Y];
    if (rc_cut(&q, (rc_t){saved[0], saved[1], saved[2], saved[3]})) {
        st[MOY_ST_CX0] = q.x0;
        st[MOY_ST_CY0] = q.y0;
        st[MOY_ST_CX1] = q.x1;
        st[MOY_ST_CY1] = q.y1;
        static const uint8_t none[1] = {0};
        if (c->sh_dlg) {
            // a dialog's body lies on its card: the card again, under the cut
            moy_ui_modal(s, c->dw_card, (moy_ui_span_t){none, 0}, c->fs, c->cls, row_h(c));
        } else {
            moy_ui_page_ground(s, b, (moy_ui_span_t){none, 0}, c->fs,
                               (c->cls ? MOY_UI_DESK : 0) | (c->win ? MOY_UI_WINDOW : 0), NULL);
        }
    }
    memcpy(&st[MOY_ST_CX0], saved, sizeof(saved));
}

// The floor's rects that want their ground get it.
static void ground_due(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    uint32_t m = c->fl_pend;
    c->fl_pend = 0;
    for (int i = 0; m && i < c->fl_n; i++, m >>= 1) {
        if (m & 1u) {
            ground_fill(c, s, c->fl[i]);
        }
    }
}

// What a stacked row draws inside: the page's content rect; on a shift
// frame, each piece of the frame's region in turn.
typedef struct {
    moy_canvas_row_t *row;
    int32_t saved[4];
    int on;
    moy_ui_ctx_t *c;                // NULL: one clip, no pieces
    int i, stacked;
    rc_t in, ext;
} clip_t;

static int clip_in(clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b) {
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

static void clip_out(clip_t *k) {
    if (k->on) {
        memcpy(&k->row->st[MOY_ST_CX0], k->saved, sizeof(k->saved));
        k->on = 0;
    }
}

// Whether rows y..y+h on screen, with a mark's reach about them, meet the
// clip a draw holds now.
static int clip_meets(const moy_ui_sink_t *s, int y, int h, int m) {
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
        if ((k->stacked && !rc_cut(&p, k->in)) || !rc_meets(p, k->ext)) {
            continue;
        }
        p.x0 -= cx;
        p.x1 -= cx;
        p.y0 -= cy;
        p.y1 -= cy;
        if (!rc_cut(&p, (rc_t){k->saved[0], k->saved[1], k->saved[2], k->saved[3]})) {
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
// clip_next moves to the next: a draw is `for (ok = clip_row(..); ok; ok =
// clip_next(&k))`.
static int clip_row(moy_ui_ctx_t *c, clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b,
                    int stacked, moy_ui_box_t in) {
    k->on = 0;
    k->c = NULL;
    int cast = c->fl_cast;
    c->fl_cast = 0;
    if (stacked && (b.y + b.h <= in.y || b.y >= in.y + in.h)) {
        return 0;
    }
    if (!armed(c)) {
        return stacked ? clip_in(k, s, in) : 1;
    }
    if (c->fl_mute && c->fl_stage == 1) {
        return 0;                   // a dialog unlike the one planned draws nothing
    }
    ground_due(c, s);
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    k->row = row;
    memcpy(k->saved, &row->st[MOY_ST_CX0], sizeof(k->saved));
    k->c = c;
    k->i = -1;
    k->stacked = stacked;
    k->in = rc_of(in);
    // what draws past its box: a mark's reach, where the stop drawn last
    // carries one, and a card's shadow; a pixel's rounding otherwise
    int m = cast || (c->n_cur > 0 && (c->cur[c->n_cur - 1].st & MARKS)) ? 8 * c->fs : c->fs;
    k->ext = (rc_t){b.x - m, b.y - m, b.x + b.w + m, b.y + b.h + m};
    return clip_piece(k);
}

static int clip_next(clip_t *k) {
    clip_out(k);
    return k->c != NULL && clip_piece(k);
}

// A draw over box b that no row clips (a fade, a hint band, a page's
// ground): through every piece it reaches on a shift frame, once otherwise.
#define EACH_PIECE(c, k, s, b) \
    for (int k##_ok = clip_row((c), &(k), (s), (b), 0, (b)); k##_ok; k##_ok = clip_next(&(k)))
// A stacked row's draw: clipped to `in`, through every piece it reaches.
#define EACH_ROW(c, k, s, b, stacked, in) \
    for (int k##_ok = clip_row((c), &(k), (s), (b), (stacked), (in)); k##_ok; \
         k##_ok = clip_next(&(k)))

// -- groups (ui.group) ---------------------------------------------------------------

// The open group's card: its x and width, its top on screen.
static moy_ui_box_t card_of(const moy_ui_ctx_t *c) {
    int in = c->cls ? 0 : kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
    return (moy_ui_box_t){(int16_t)(c->page.x + in),
                          (int16_t)(c->page.y + c->grp_top - c->scroll),
                          (int16_t)(c->page.w - 2 * in), 0};
}

// A stacked row b of an open group shows: its marks (MOY_UI_F_FOCUS,
// MOY_UI_F_ON) are kept for the card's foot -- whether or not a shift
// frame's floor draws the row.
static void band_mark(moy_ui_ctx_t *c, moy_ui_box_t b, int stacked, int marks) {
    if (stacked && c->grp_open && b.y + b.h > c->page.y && b.y < c->page.y + c->page.h) {
        c->grp_flags = (uint16_t)(marks & (MOY_UI_F_FOCUS | MOY_UI_F_ON));
    }
}

// The card behind stacked row b, drawn under the row's clip (band_mark
// keeps its marks).
static void band(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                 int marks) {
    if (!stacked || !c->grp_open) {
        return;
    }
    // a focus mark that fills its row (invert, or the edge's plate) is the
    // line between it and its neighbours: neither it nor the row under it
    // draws a rule
    const moy_ui_marks_t *m = moy_ui_marks();
    int st = m->style[MOY_UI_MARK_PART];
    int fills = m->active && (st == MOY_UI_FOCUS_INVERT || st == MOY_UI_FOCUS_EDGE);
    int after_mark = (c->grp_prev & MOY_UI_F_FOCUS) && fills;
    int own = (marks & MOY_UI_F_FOCUS) && fills;
    // a row the page's foot cuts inside its fade draws no rule: the fade
    // says the rows run on, and a rule there reads as a second foot line
    int foot = c->page.y + c->page.h;
    int cut = b.y + b.h > foot && b.y >= foot - FADE_H * c->fs;
    moy_ui_group_band(s, card_of(c), b, c->grp_first || after_mark || own || cut, c->fs,
                      c->cls);
}

// The open group ends: its card's foot under its last row, and the gap the
// next group's caption keeps.
static void group_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->grp_open) {
        return;
    }
    c->grp_open = 0;
    c->grp_any = 1;
    if (c->grp_n) {
        clip_t k;
        EACH_ROW(c, k, s, c->grp_last, 1, c->page) {
            moy_ui_group_end(s, card_of(c), c->grp_last, c->grp_flags, c->fs, c->cls);
            clip_out(&k);
        }
    }
    c->y = (int16_t)(c->y + kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_GAP) * c->fs);
}

// Whether a dialog's or a popover's body (or a dialog's buttons) is being
// laid out: its stops are modal.
static int in_dialog(const moy_ui_ctx_t *c) {
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

// Stop i was just recorded inside R on a shift frame: where it is not the
// last frame's stop of that place (another id, another box once moved, a
// mark of another state), the floor takes R from its top down -- the floor
// only widens, and every stop after it draws whole.
static void floor_check(moy_ui_ctx_t *c, int i) {
    if (!armed(c) || c->fl_stage != 1 || c->fl_struct || i < c->fl_s0) {
        return;
    }
    const moy_ui_stop_t *s = &c->cur[i];
    int j = i - c->fl_s0;
    const moy_ui_stop_t *q = j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
    if (q == NULL || q->id != s->id || q->b.x != s->b.x || q->b.w != s->b.w
        || q->b.h != s->b.h || q->b.y + c->sh_dy != s->b.y
        || (((q->st ^ s->st) & MARKS) && !floor_holds(c, s->b))) {
        c->fl_struct = 1;
        if (c->sh_dlg) {
            dlg_widen(c, s->b.y, c->dlg_foot);
        } else {
            floor_rows(c, s->b.y, r_foot(c), 1);
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
    if (!s_audit_on || !armed(c) || c->fl_stage == 1 || (s->flags & STOP_PAGE)
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
    int m = (s->st & MARKS) || was ? mark_room(c) : 0;
    moy_ui_box_t b = {(int16_t)(s->b.x - m), (int16_t)(s->b.y - m), (int16_t)(s->b.w + 2 * m),
                      (int16_t)(s->b.h + 2 * m)};
    if (!rc_meets(rc_of(b), rc_of(c->sh_r))) {
        return;
    }
    if ((c->fl_stage == 0 && !c->fl_early)
        || ((c->sh_dy || (s->st & MARKS) != was) && !floor_holds(c, b))) {
        s_audit_fail++;
    }
}

// The L8 record (tests only): what the stop just recorded draws, mixed in.
static void sig_mix(moy_ui_ctx_t *c, const void *p, size_t n) {
    if (s_audit_on && c->n_cur > 0) {
        moy_ui_stop_t *s = &c->cur[c->n_cur - 1];
        s->sig = hash((const uint8_t *)p, n, s->sig);
    }
}

static void sig_int(moy_ui_ctx_t *c, int32_t v) {
    sig_mix(c, &v, sizeof(v));
}

static int stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags, moy_ui_span_t name) {
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
        return -1;
    }
    int dlg = in_dialog(c);
    moy_ui_stop_t *s = &c->cur[c->n_cur++];
    s->id = id;
    s->b = b;
    s->flags = (uint16_t)(flags | (dlg ? STOP_MODAL : 0) | (c->at_last ? STOP_AT : 0));
    s->page = c->page_stop;
    s->row = -1;
    s->top = 0;
    s->st = 0;
    s->gen = 0;
    s->sig = s_audit_on ? hash(name.p, name.n, (uint32_t)s->flags) : 0;
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
    if ((p & MOY_UI_P_LIVE) && inside(b, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y])) {
        if (p & MOY_UI_P_HELD) {
            if (c->press == id && c->gesture == 1) {
                st |= MOY_UI_ST_PRESSED;
            }
        } else {
            st |= MOY_UI_ST_HOVER;
        }
    }
    s->st = (uint8_t)st;
    sig_int(c, st);
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
static int pic_note(moy_ui_ctx_t *c, const moy_ui_over_t *o, int pic) {
    if (c->n_cur == 0) {
        return 0;
    }
    int i = c->n_cur - 1;
    moy_ui_stop_t *s = &c->cur[i];
    s->flags |= (pic ? STOP_PIC : 0) | (o->has_gen ? STOP_GEN : 0);
    s->gen = o->gen;
    if (!armed(c) || c->fl_stage != 1 || c->fl_struct || i < c->fl_s0) {
        return 0;
    }
    int j = i - c->fl_s0;
    const moy_ui_stop_t *q = j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
    return q != NULL && (!(q->flags & STOP_GEN) || !o->has_gen || q->gen != o->gen);
}

// A picture whose gen moved draws again: the rect it shows in, where its
// mark puts it (a lift) -- inside it what the card draws over the app's
// picture, around it the mark, which the gen does not move.
static void pic_redraw(moy_ui_ctx_t *c, moy_ui_box_t r) {
    if (!floor_holds(c, r)) {
        floor_add(c, rc_of(r), 1);
    }
}

static int interact(int st) {
    return (st & MOY_UI_ST_PRESSED) ? MOY_UI_PRESSED : (st & MOY_UI_ST_HOVER) ? MOY_UI_HOVER : -1;
}

static int mark_flags(int st, const moy_ui_over_t *o) {
    int f = 0;
    if ((st & MOY_UI_ST_FOCUS) && o->mark != 0) {
        f |= MOY_UI_F_FOCUS;
    }
    if (o->current == 1) {
        f |= MOY_UI_F_ON;
    }
    return f;
}

static uint32_t id_of(const moy_ui_over_t *o, moy_ui_span_t label) {
    return o->id.p != NULL ? hash(o->id.p, o->id.n, 0) : hash(label.p, label.n, 0);
}

static moy_ui_span_t name_of(const moy_ui_over_t *o, moy_ui_span_t label) {
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
static void vrow_init(const moy_ui_ctx_t *c, moy_ui_vrow_t *r, moy_ui_span_t label) {
    memset(r, 0, sizeof(*r));
    r->label = label;
    r->cur = -1;
    r->fs = c->fs;
    r->desk = c->cls;
    r->card = c->grp_open;
    r->first = c->grp_open && c->grp_first;
    r->icon_slot = c->row_slot;
    r->icon_glyph = c->row_glyph;
}

static void vrow_draw(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                      int kind, int st, const moy_ui_over_t *o, const moy_ui_vrow_t *r) {
    clip_t k;
    int f = mark_flags(st, o) & ~MOY_UI_F_ON;
    if (s_audit_on && c->n_cur > 0) {
        const moy_ui_box_t sb = c->cur[c->n_cur - 1].b;
        if (sb.x == b.x && sb.y == b.y && sb.w == b.w && sb.h == b.h) {
            int32_t w[5] = {kind, r->cur, r->v, r->lo, r->hi};
            sig_mix(c, w, sizeof(w));
            sig_mix(c, r->value.p, r->value.n);
        }
    }
    band_mark(c, b, stacked, f);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, f);
        moy_ui_vrow(s, b, kind, f | (o->disabled == 1 ? MOY_UI_F_DISABLED : 0), r);
        clip_out(&k);
    }
}

// The stop a value row is, with its state bits (0: none, or a disabled row).
static int vrow_stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags,
                     const moy_ui_over_t *o, moy_ui_span_t label) {
    if (o->disabled == 1) {
        return 0;
    }
    int st = stop(c, id, b, flags, name_of(o, label));
    return st < 0 ? 0 : st;
}

static void grid_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s);

// -- the verbs -----------------------------------------------------------------------

// How far a focus mark reaches past its box, any class, at the scale.
static int mark_room(const moy_ui_ctx_t *c) {
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
    int reach = mark_room(c);
    const moy_ui_stop_t *v = &c->prev[p0];
    for (int j = 0; j < n; j++) {
        // a mark drawn last frame this one does not draw, or the other way:
        // the frame's press, hover and activation are none (the plan)
        int now = c->focus != 0 && v[j].id == c->focus && !c->elsewhere ? MOY_UI_ST_FOCUS : 0;
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
            if (!(v[j].flags & STOP_PIC)) {
                continue;
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
static void table_strip(moy_ui_ctx_t *c, int y0, int y1, int base, int rh, int pic) {
    if (pic) {
        y0 = base + moy_ui_fdiv(y0 - base, rh) * rh;
        y1 = base + moy_ui_fdiv(y1 - base + rh - 1, rh) * rh;
    }
    floor_strip(c, y0, y1);
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
    int dy = c->sh_dy, reach = mark_room(c), early = 0, at = c->n_prev;
    for (int i = 0; i < c->n_prev; i++) {
        if (c->prev[i].id == c->sh_id && (c->prev[i].flags & STOP_PAGE)) {
            at = i;
            break;
        }
    }
    for (int i = 0; i < c->n_prev; i++) {
        const moy_ui_stop_t *v = &c->prev[i];
        if ((i >= p0 && i < p0 + n) || (v->flags & STOP_PAGE) || v->id == c->sh_id) {
            continue;
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
            table_strip(c, y0, y1, base, rh, pic);
        }
        if (dy) {
            table_strip(c, y0 + dy, y1 + dy, base, rh, pic);
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
static void table_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                        int mark, int pic, int of) {
    moy_ui_box_t r = c->sh_r;
    int top = r.y, foot = r.y + r.h, dy = c->sh_dy;
    if (dy < 0) {
        table_strip(c, foot + dy, foot, base, rh, pic);
    } else if (dy > 0) {
        table_strip(c, top, top + dy, base, rh, pic);
    }
    if (dy) {
        int f = rh < r.h ? rh : r.h;
        table_strip(c, top, top + f + (dy > 0 ? dy : 0), base, rh, pic);
        table_strip(c, foot - f + (dy < 0 ? dy : 0), foot, base, rh, pic);
        table_strip(c, of - FADE_H * c->fs, of, base, rh, pic);
    }
    if (was != now || was_mark != mark) {
        int reach = mark_room(c);
        if (was >= 0) {
            table_strip(c, base + was * rh - reach, base + (was + 1) * rh + reach, base, rh, pic);
        }
        if (now >= 0) {
            table_strip(c, base + now * rh - reach, base + (now + 1) * rh + reach, base, rh, pic);
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
        floor_rows(c, top, foot, 1);
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
    moy_ui_keep_t *k = keep(c, id, 1);
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
        if (armed(c) && c->fl_stage == 0 && id == c->sh_id) {
            moy_ui_box_t r = ct;
            r.h = (int16_t)(r.h - foot);
            floor_open(c, k, r, c->scroll, o, outer);
        }
        k->drawn = c->scroll;
        k->gen = o != NULL ? o->gen : 0;
        k->kd = (uint8_t)(MOY_UI_KD_PAGE | (o != NULL && o->has_gen ? MOY_UI_KD_GEN : 0));
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
    }
}

// The open page is done (its nest closes, ui.end, the next page): what it
// holds, for the next frame's plan -- the stops drawn in it and whether any
// is one no shift may move: a stop ui.at placed, a picture with no gen. R
// closing on a shift frame: the ground under the stops its tail lost, the
// ground its floor still wants, and under the audit its stops' records
// against the last frame's.
static void page_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = page_keep(c);
    if (k == NULL || c->page_stop == NO_PAGE || c->cur[c->page_stop].id != k->id) {
        return;
    }
    int s0 = c->page_stop + 1, sn = c->n_cur - s0;
    int bad = 0;
    for (int i = s0; i < c->n_cur; i++) {
        uint16_t f = c->cur[i].flags;
        if ((f & STOP_AT) || ((f & STOP_PIC) && !(f & STOP_GEN))) {
            bad = 1;
        }
    }
    if (armed(c) && c->fl_stage == 1 && k->id == c->sh_id) {
        if (!c->fl_struct && !c->fl_mute && sn < c->fl_pn) {
            int y = sn > 0 ? c->cur[c->n_cur - 1].b.y + c->cur[c->n_cur - 1].b.h
                           : (c->sh_dlg ? c->page.y : c->sh_r.y);
            if (c->sh_dlg) {
                dlg_widen(c, y, c->dlg_foot);
            } else {
                floor_rows(c, y, r_foot(c), 1);
            }
        }
        ground_due(c, s);
        if (s_audit_on && !c->fl_struct && !c->fl_mute) {
            for (int j = 0; j < sn && j < c->fl_pn; j++) {
                const moy_ui_stop_t *a = &c->cur[s0 + j], *q = &c->prev[c->fl_p0 + j];
                if (a->sig != q->sig && !floor_holds(c, a->b)) {
                    s_audit_fail++;
                }
            }
        }
        if (c->fl_lost && s_audit_on) {
            s_audit_fail++;
        }
        c->fl_stage = 2;
    }
    k->s0 = (uint8_t)s0;
    k->sn = (uint8_t)(sn > 0 ? sn : 0);
    k->kd |= MOY_UI_KD_DRAWN | (bad ? MOY_UI_KD_BAD : 0);
}

void moy_ui_api_page(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     const moy_ui_over_t *o, moy_ui_box_t *content) {
    o = over(o);
    grid_end(c, s);
    group_close(c, s);
    if (c->n_nest == 0) {
        page_close(c, s);
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
        moy_ui_page_ground(s, b, title, c->fs, ground_of(c), &ct);
        drew = 1;
    }
    if (!drew) {
        // nothing of it is drawn this frame: its content rect all the same
        moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
        int32_t saved[4];
        memcpy(saved, &row->st[MOY_ST_CX0], sizeof(saved));
        row->st[MOY_ST_CX1] = row->st[MOY_ST_CX0];
        moy_ui_page_ground(s, b, title, c->fs, ground_of(c), &ct);
        memcpy(&row->st[MOY_ST_CX0], saved, sizeof(saved));
    }
    open_page(c, hash(title.p, title.n, PAGE_SALT), ct, o->nav == 0 ? STOP_NAV_OFF : 0, o, ct);
    if (content != NULL) {
        *content = ct;
    }
}

void moy_ui_api_label(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      const moy_ui_over_t *o) {
    o = over(o);
    int stacked;
    if (c->txt_lines || c->txt_centre || c->txt_tone || c->txt_role >= 0) {
        // the Python face's text options: lines wrapped, centred, a tone, a role
        int lines = c->txt_lines, centre = c->txt_centre == 1, tone = c->txt_tone;
        int role = c->txt_role >= 0 ? c->txt_role : MOY_UI_FONT_UI;
        int sunk = c->txt_centre == 3 ? (c->cls ? 10 : 8) * c->fs : 0;
        int pad = c->txt_centre == 2
                      ? (kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET)
                         + kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * c->fs
                      : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
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
                                  c->icon_glyph, c->fs, ground_of(c), 0);
        if (sunk) {
            int hb = h + 2 * sunk - (c->cls ? 0 : 2 * c->fs);
            moy_ui_box_t b = place(c, hb, &stacked);
            well.y = b.y;
            well.h = (int16_t)hb;
            clip_t k;
            EACH_ROW(c, k, s, well, stacked, c->page) {
                moy_ui_well(s, well, 0, moy_ui_token(s->look, MOY_UI_T_SUNK), c->fs,
                            ground_of(c));
                m.y = (int16_t)(b.y + sunk - (c->cls ? 0 : c->fs));
                m.h = (int16_t)h;
                moy_ui_text_block(s, m, text, role, tone, lines, centre, c->row_slot,
                                  c->row_glyph, c->fs, ground_of(c), 1);
                clip_out(&k);
            }
            return;
        }
        int one = lines <= 1 && !c->has_at && h < row_h(c);
        moy_ui_box_t b = place(c, one ? row_h(c) : h, &stacked);
        b.x = m.x;
        b.w = m.w;
        if (one) {
            b.y = (int16_t)(b.y + (row_h(c) - h) / 2);
            b.h = (int16_t)h;
        }
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_text_block(s, b, text, role, tone, lines, centre, c->row_slot, c->row_glyph,
                              c->fs, ground_of(c), 1);
            clip_out(&k);
        }
        return;
    }
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    clip_t k;
    if ((stacked && c->grp_open) || o->ink == MOY_UI_O_NONE) {
        band_mark(c, b, stacked, 0);
    }
    EACH_ROW(c, k, s, b, stacked, c->page) {
        // a group's row, or a label on the page's ground: the parts' ink
        if ((stacked && c->grp_open) || o->ink == MOY_UI_O_NONE) {
            band(c, s, b, stacked, 0);
            moy_ui_vrow_t r;
            vrow_init(c, &r, text);
            moy_ui_vrow(s, b, MOY_UI_V_LABEL, 0, &r);
            continue;
        }
        int16_t pc[3];
        moy_ui_colors(s->look, MOY_UI_K_PANEL, MOY_UI_REST, pc);
        int16_t ink = colour(s->look, o->ink);
        int pad = kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
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
    o = over(o);
    if (kind < 0 || kind >= 5) {
        kind = 0;
    }
    const char *kn = moy_ui_button_kinds[kind][0];
    int kid = moy_ui_button_kind(kn, strlen(kn));
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : stop(c, id_of(o, label), b, 0, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    band_mark(c, b, stacked, mark_flags(st, o));
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, mark_flags(st, o));
        moy_ui_button_t bt;
        bt.label = label;
        bt.glyph = -1;
        bt.fs = c->fs;
        colours(s, o, bt.colors);
        bt.app = (int16_t)(c->app - 1);
        bt.icon = -1;
        int fl = mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0);
        if (c->row_slot >= 0 || c->row_glyph >= 0) {
            fl |= MOY_UI_F_ICON;
            bt.icon = c->row_slot;
            bt.glyph = c->row_glyph;
        }
        moy_ui_button(s, b, kid, fl, interact(st), &bt, MOY_UI_PHASE_ALL);
        clip_out(&k);
    }
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int moy_ui_api_toggle(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int on,
                      const moy_ui_over_t *o) {
    o = over(o);
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int st = vrow_stop(c, id_of(o, label), b, 0, o, label);
    on = on ? 1 : 0;
    if (st & MOY_UI_ST_ACTIVATED) {
        on = !on;
    }
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
    r.cur = (int16_t)on;
    vrow_draw(c, s, b, stacked, MOY_UI_V_TOGGLE, st, o, &r);
    return on;
}

int32_t moy_ui_api_slider(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                          int32_t v, int32_t lo, int32_t hi, const moy_ui_over_t *o) {
    o = over(o);
    if (hi < lo) {
        int32_t t = lo;
        lo = hi;
        hi = t;
    }
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    uint32_t id = id_of(o, label);
    int st = vrow_stop(c, id, b, STOP_KEEP_LR, o, label);
    if ((st & MOY_UI_ST_FOCUS) && c->step_x) {
        v += c->step_x;
    }
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
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
    vrow_draw(c, s, b, stacked, MOY_UI_V_SLIDER, st, o, &r);
    return v;
}

void moy_ui_api_group_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    grid_end(c, s);
    group_close(c, s);
    c->has_at = 0;
    c->grp_open = 1;
    c->grp_n = 0;
    c->grp_top = c->y;
}

void moy_ui_api_group(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t caption,
                      const moy_ui_over_t *o) {
    (void)o;
    grid_end(c, s);
    group_close(c, s);
    c->has_at = 0;
    if (caption.n == 0) {
        return;
    }
    int h = kmetric(MOY_UI_K_GROUP, c->cls ? MOY_UI_M_GROUP_CAPTION_DESK : MOY_UI_M_GROUP_CAPTION)
            * c->fs;
    moy_ui_box_t b = {c->page.x, (int16_t)(c->page.y + c->y - c->scroll), c->page.w, (int16_t)h};
    peek_note(c, c->y, h);
    c->y = (int16_t)(c->y + h);
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    if (c->n_cap_cur < MOY_UI_API_CAPS && c->page_stop != NO_PAGE) {
        c->cap_cur[c->n_cap_cur++] = (moy_ui_cap_t){b.y, b.h, c->page_stop};
    }
    clip_t cl;
    moy_ui_box_t cb = b;
    if (!c->cls) {
        int in = kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
        cb.x = (int16_t)(cb.x + in);
        cb.w = (int16_t)(cb.w - 2 * in);
    }
    EACH_ROW(c, cl, s, b, 1, c->page) {
        moy_ui_caption(s, cb, caption, c->fs, c->cls);
        clip_out(&cl);
    }
    c->grp_open = 1;
    c->grp_n = 0;
    c->grp_top = c->y;
}

int moy_ui_api_value(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                     moy_ui_span_t value, const moy_ui_over_t *o) {
    o = over(o);
    if (kind != MOY_UI_V_LINK) {
        kind = MOY_UI_V_INFO;
    }
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int st = kind == MOY_UI_V_LINK ? vrow_stop(c, id_of(o, label), b, 0, o, label) : 0;
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
    r.value = value;
    vrow_draw(c, s, b, stacked, kind, st, o, &r);
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int32_t moy_ui_api_choice(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                          const uint8_t *opts, size_t len, int32_t cur, const moy_ui_over_t *o) {
    o = over(o);
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
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int st = vrow_stop(c, id_of(o, label), b, STOP_KEEP_LR, o, label);
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
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
    vrow_draw(c, s, b, stacked, kind, st, o, &r);
    return cur;
}

// The band a rule takes between a card's rows: the row stack's gap and a
// unit, twice over.
static int rule_h(const moy_ui_ctx_t *c) {
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
        group_close(c, s);
        int h = kmetric(MOY_UI_K_VALUE_ROW, c->cls ? MOY_UI_M_VALUE_ROW_HINTS_DESK : MOY_UI_M_VALUE_ROW_HINTS)
                * c->fs;
        int stacked;
        moy_ui_box_t r = place(c, rule_h(c), &stacked);
        moy_ui_rule(s, r, c->fs, c->cls);
        b = place(c, h, &stacked);
    } else {
        int h = kmetric(MOY_UI_K_VALUE_ROW, c->cls ? MOY_UI_M_VALUE_ROW_HINTS_DESK : MOY_UI_M_VALUE_ROW_HINTS)
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
        moy_ui_keep_t *k = page_keep(c);
        if (k != NULL) {
            k->foot = (int16_t)h;
        }
    }
    if (c->n_nest == 0) {
        c->hint_band = b;
    }
    clip_t k;
    EACH_PIECE(c, k, s, b) {
        moy_ui_hints(s, b, items, len, c->fs, ground_of(c));
    }
}

int moy_ui_api_hints_w(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items,
                       size_t len) {
    int16_t sp[32];
    moy_ui_box_t b = {0, 0, 0x3fff, 0};
    int n = moy_ui_hints_at(s, b, items, len, c->fs, ground_of(c), 0, sp, 16);
    int w = 0;
    for (int k = 0; k < n && k < 16; k++) {
        w = sp[2 * k + 1] > w ? sp[2 * k + 1] : w;
    }
    return w ? w + MOY_UI_HINT_LEAD(c->cls) * c->fs : 0;
}

// The stop `id` just drew is a list's or a table's: its focused row and row
// 0's top on screen, which a key step on its page reveals at the next begin.
static void rows_note(moy_ui_ctx_t *c, uint32_t id, int row, int top) {
    if (c->n_cur > 0 && c->cur[c->n_cur - 1].id == id) {
        c->cur[c->n_cur - 1].row = (int16_t)row;
        c->cur[c->n_cur - 1].top = (int16_t)top;
    }
}

int32_t moy_ui_api_list(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, int32_t cur, const moy_ui_over_t *o) {
    o = over(o);
    int count = 0;
    for (size_t i = 0; i < len; i++) {
        count += items[i] == 0;
    }
    if (len && items[len - 1] != 0) {
        count++;
    }
    int rh = row_h(c);
    int placed = c->has_at;
    if (label.n && !placed) {
        moy_ui_api_label(c, s, label, NULL);
    }
    int stacked;
    moy_ui_box_t b = place(c, (count ? count : 1) * rh, &stacked);
    uint32_t id = id_of(o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled ? NULL : keep(c, id, 1);
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
    int st = disabled ? 0 : stop(c, id, b, flags, name_of(o, label));
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
    rows_note(c, id, row, b.y);
    if (focused && c->step_y && !(c->step_rev == id && c->step_row == row)) {
        c->reveal = 1;              // a step begin did not reveal: the next frame does
        c->reveal_box = rb;
        c->owe = 1;
    }
    clip_t k;
    band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, 0);
        clip_t kb;
        int any = clip_in(&kb, s, b);
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
                if ((p & MOY_UI_P_LIVE) && inside(ib, px, py)) {
                    ia = (st & MOY_UI_ST_PRESSED) ? MOY_UI_PRESSED
                         : (st & MOY_UI_ST_HOVER) ? MOY_UI_HOVER : -1;
                }
                moy_ui_row(s, ib, MOY_UI_K_ROW_LIST, f, ia, &r, MOY_UI_PHASE_ALL);
            }
            at = e + 1;
        }
        clip_out(&kb);
        clip_out(&k);
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
    o = over(o);
    int stacked;
    int count = o->steps == 1;
    // a count stacked: its label on a row and its bar under it, in from the
    // page's edges as a group's caption is
    int placed = c->has_at;
    int top = count && !placed ? (c->cls ? 14 : 6) * c->fs : 0;
    int h = count ? top + row_h(c) + (c->cls ? 10 : 7) * c->fs : row_h(c);
    moy_ui_box_t b = place(c, h, &stacked);
    if (count && !placed) {
        int in = (kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET)
                  + kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * c->fs;
        b.x = (int16_t)(b.x + in);
        b.w = (int16_t)(b.w - 2 * in);
        b.y = (int16_t)(b.y + top);
        b.h = (int16_t)(b.h - top);
    }
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
    r.lo = 0;
    r.hi = hi > 0 ? hi : 1;
    r.v = v < 0 ? 0 : v > r.hi ? r.hi : v;
    r.steps = count;
    vrow_draw(c, s, b, stacked, MOY_UI_V_PROGRESS, 0, o, &r);
}

void moy_ui_api_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o, int32_t out[5]) {
    o = over(o);
    int stacked;
    int placed = c->has_at;
    moy_ui_box_t b = place(c, h > 0 ? h : row_h(c), &stacked);
    if (!placed && w > 0 && w < b.w) {
        b.w = (int16_t)w;
    }
    int disabled = o->disabled == 1;
    uint32_t pid = o->id.p != NULL ? hash(o->id.p, o->id.n, 0) : hash(id.p, id.n, 0);
    int st = disabled ? 0
                      : stop(c, pid, b, o->nav == 0 ? STOP_HOLD : 0, o->id.p != NULL ? o->id : id);
    if (!disabled && st >= 0 && pic_note(c, o, 1)) {
        pic_redraw(c, b);
    }
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    band_mark(c, b, stacked, mark_flags(st, o));
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, mark_flags(st, o));
        int16_t oc[3];
        colours(s, o, oc);
        moy_ui_well(s, b, mark_flags(st, o), oc[0] == MOY_UI_FROM_STYLE ? -1 : oc[0], c->fs,
                    c->cls);
        clip_out(&k);
    }
    int none = outside_floor(c, b);
    c->pic_clip = floor_of(c, b);
    c->pic_tbl = 0;
    out[0] = none ? 0 : b.x;
    out[1] = none ? 0 : b.y;
    out[2] = none ? 0 : b.w;
    out[3] = none ? 0 : b.h;
    out[4] = st;
}

int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap) {
    if (id.p != NULL) {
        uint32_t h = hash(id.p, id.n, 0);
        if (h != c->focus) {
            c->focus = h;
            c->reveal = 1;
            c->jump = 1;                // an app's focus move is no step: it shows at once
            c->owe = 1;                 // the next frame reveals it
            int i = find(c->cur, c->n_cur, h);
            if (i < 0) {
                i = find(c->prev, c->n_prev, h);
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

int moy_ui_api_edge(const moy_ui_ctx_t *c) {
    return c->edge;
}

void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h) {
    c->at.x = (int16_t)x;
    c->at.y = (int16_t)y;
    c->at.w = (int16_t)(w > 0 ? w : 0);
    c->at.h = (int16_t)(h > 0 ? h : 0);
    c->has_at = 1;
}

int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]) {
    if (c->pop_id != 0 && c->pop_card.w > 0 && hash(id.p, id.n, POPOVER_SALT) == c->pop_id) {
        out[0] = c->pop_card.x;
        out[1] = c->pop_card.y;
        out[2] = c->pop_card.w;
        out[3] = c->pop_card.h;
        return 1;
    }
    if (c->dlg_id != 0 && hash(id.p, id.n, DIALOG_SALT) == c->dlg_id) {
        out[0] = c->dlg_card.x;
        out[1] = c->dlg_card.y;
        out[2] = c->dlg_card.w;
        out[3] = c->dlg_card.h;
        return 1;
    }
    int i = find(c->cur, c->n_cur, hash(id.p, id.n, 0));
    if (i < 0 && c->dlg_id != 0) {
        i = find(c->cur, c->n_cur, hash(id.p, id.n, c->dlg_id));   // a button at its foot
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

const char *moy_ui_api_dup(moy_ui_ctx_t *c) {
    if (c->dup == 0 || c->dup == c->dup_told) {
        return NULL;
    }
    c->dup_told = c->dup;
    return c->dup_name;
}

// -- tables, scroll regions, fields, dialogs ------------------------------------------

static int pmetric(const moy_ui_ctx_t *c, int slot) {
    return kmetric(MOY_UI_K_VALUE_ROW, slot + (c->cls ? 1 : 0)) * c->fs;
}

// Open a nested page (a scroll region, kind 1; a dialog's body, kind 2; a
// popover's, kind 3) in
// box b, the outer one kept until it closes.
static void nest_open(moy_ui_ctx_t *c, int kind, uint32_t id, moy_ui_box_t b, int used,
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
    open_page(c, id, b, flags | (kind >= 2 || in_dialog(c) ? STOP_MODAL : 0)
                        | (kind >= 2 ? STOP_CARD : 0), o, outer);
}

// A list's edges in view `v`: where rows `rows` high scrolled by `scroll`
// run on below it, a fade over its last `h` pixels toward colour `ci`;
// with `head`, where rows are scrolled off above it, one over its first.
static uint32_t s_fades;

uint32_t moy_ui_api_fades(int reset) {
    uint32_t n = s_fades;
    if (reset) {
        s_fades = 0;
    }
    return n;
}

static void edge_fades(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t v, int rows,
                       int scroll, int ci, int head, int h) {
    if (h > v.h) {
        h = v.h;
    }
    clip_t k;
    if (rows > scroll + v.h) {
        moy_ui_box_t b = {v.x, (int16_t)(v.y + v.h - h), v.w, (int16_t)h};
        EACH_PIECE(c, k, s, b) {
            moy_ui_fade(s, b, ci, 0, c->fs, ground_of(c));
        }
        s_fades++;
    }
    if (head && scroll > 0) {
        moy_ui_box_t b = {v.x, v.y, v.w, (int16_t)h};
        EACH_PIECE(c, k, s, b) {
            moy_ui_fade(s, b, ci, 1, c->fs, ground_of(c));
        }
        s_fades++;
    }
}

// The page's (or a scroll region's) foot, its groups closed: where its rows
// run on below what shows, a fade over its last FADE_H rows of the scale.
static void foot_fade(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL) {
        edge_fades(c, s, c->page, k->b, c->scroll, MOY_UI_DEFAULT, 0, FADE_H * c->fs);
    }
}

// Close the innermost nested page where it is of `kind`: answers the height
// its rows took.
static int nest_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind) {
    if (c->n_nest == 0 || c->nest[c->n_nest - 1].kind != kind) {
        return -1;
    }
    group_close(c, s);
    if (kind == 1) {
        foot_fade(c, s);
    } else if (kind == 3 && page_keep(c) != NULL) {
        // a popover's body cut to its room: a fade toward the card at each
        // edge its rows run on past
        edge_fades(c, s, c->page, page_keep(c)->b, c->scroll, MOY_UI_FADE_CARD, 1,
                   FADE_H * c->fs);
    }
    page_close(c, s);
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

void moy_ui_api_scroll(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                       const moy_ui_over_t *o) {
    o = over(o);
    if (id.n == 0) {
        nest_close(c, s, 1);
        return;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return;
    }
    group_close(c, s);
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
    nest_open(c, 1, hash(id.p, id.n, SCROLL_SALT), b, used,
              (o->nav == 0 ? STOP_NAV_OFF : 0) | (placed ? STOP_AT : 0), o);
}


// -- ui.text_view ------------------------------------------------------------------------

static int16_t s_tones[6] = {-1, -1, -1, -1, -1, -1};

void moy_ui_text_tones(const int16_t tones[6]) {
    memcpy(s_tones, tones, sizeof(s_tones));
}

#define TEXT_SALT 0x27d4eb2fu
#define TEXT_PAD 4                  // the text's inset from the view's left, at the scale
#define TEXT_WRAP_ROWS MOY_UI_TEXT_BREAKS
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

void moy_ui_api_text_view(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                          const moy_ui_lines_t *L, const int32_t *opt, const moy_ui_over_t *o,
                          int32_t out[2]) {
    o = over(o);
    out[0] = out[1] = 0;
    s_trows = moy_ui_text_scratch();
    s_starts = moy_ui_text_breaks();
    if (c->n_nest >= MOY_UI_API_NEST || id.n == 0 || s_trows == NULL) {
        return;
    }
    group_close(c, s);
    int fs = c->fs;
    moy_font_info_t fi;
    uint32_t font = moy_ui_font(MOY_UI_FONT_MONO, &fi);
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
    int wrap = opt[MOY_UI_TXO_WRAP] != 0, syn = opt[MOY_UI_TXO_SYNTAX];
    int sgr = syn == MOY_UI_SYN_SGR;
    int gc = 0;
    if (opt[MOY_UI_TXO_GUTTER]) {
        for (int32_t n = L->n > 0 ? L->n : 1; n > 0; n /= 10) {
            gc++;
        }
        gc++;
    }
    int pad = TEXT_PAD * fs;
    int cols = (b.w - 2 * pad - gc * cell) / (cell > 0 ? cell : 1);
    cols = cols < 1 ? 1 : cols;
    // its rows: a line each, or each line's wrapped rows
    int32_t total = 0;
    if (!wrap) {
        total = L->n;
    } else {
        for (int32_t i = 0; i < L->n; i++) {
            const uint8_t *p;
            size_t n;
            total += text_line_rows(L, i, 1, cols, sgr, &p, &n);
        }
    }
    int32_t content = total * lh;
    content = content > 32767 ? 32767 : content;
    uint32_t rid = hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k0 = keep_of(c, rid);
    int32_t was_b = k0 != NULL ? k0->b : 0;
    int was_foot = k0 != NULL && was_b > 0 && k0->a >= was_b - b.h;
    // its rows are its own gens (the view's record): the region carries a
    // gen whatever the app gives, so its frames may move and draw rows
    moy_ui_over_t og = *o;
    if (!og.has_gen) {
        og.has_gen = 1;
        og.gen = 0;
    }
    nest_open(c, 1, rid, b, used, (o->nav == 0 ? STOP_NAV_OFF : 0) | (placed ? STOP_AT : 0), &og);
    moy_ui_keep_t *k = page_keep(c);
    int32_t scroll = c->scroll;
    if (k != NULL) {
        int32_t most = content - b.h;
        most = most < 0 ? 0 : most;
        if (opt[MOY_UI_TXO_FOLLOW] && was_foot && content > was_b && scroll < most
            && !(k->sc.flags & MOY_UI_SCROLL_DRAG)) {
            moy_ui_scroll_glide_to(&k->sc, scroll, most, 0, content, b.h);
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
    v[MOY_UI_TV_MARK] = -1;
    v[MOY_UI_TV_GUTTER] = gc;
    v[MOY_UI_TV_COLS] = cols;
    v[MOY_UI_TV_TOP] = scroll / lh;
    v[MOY_UI_TV_OFF] = scroll % lh;
    v[MOY_UI_TV_CARET_ROW] = opt[MOY_UI_TXO_CARET_LINE];
    v[MOY_UI_TV_CARET_COL] = opt[MOY_UI_TXO_CARET_COL];
    v[MOY_UI_TV_SEL_R0] = -1;
    v[MOY_UI_TV_ERR_ROW] = -1;
    v[MOY_UI_TV_SYNTAX] = syn;
    v[MOY_UI_TV_FLAGS] = MOY_UI_TVF_GROUND | MOY_UI_TVF_CLIP | (wrap ? 0 : MOY_UI_TVF_FADE);
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
    int nrows = moy_ui_text_rows(&v[0]);
    nrows = nrows < MOY_UI_TEXT_ROWS ? nrows : MOY_UI_TEXT_ROWS;
    int32_t top = v[MOY_UI_TV_TOP];
    if (!wrap) {
        for (int i = 0; i < nrows; i++) {
            moy_ui_trow_t *r = &s_trows[i];
            int32_t ln = top + i;
            r->line = ln;
            r->ca = 0;
            r->a = 0;
            size_t n = 0;
            r->p = ln < L->n ? L->get(L->src, ln, &n) : NULL;
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
    // the record: this view's, or none where another view of the context
    // holds it this frame
    moy_ui_text_t *t = &c->text;
    int recorded = c->text_id == 0 || c->text_id == rid;
    if (recorded) {
        c->text_id = rid;
    }
    moy_ui_span_t none = {(const uint8_t *)"", 0};
    if (recorded && armed(c) && c->fl_stage == 1) {
        // a floor frame: the rows whose gen moved join the floor
        // the foot's fade, where the rows' height moved under it this frame
        if (content != was_b && (content > scroll + b.h || was_b > scroll + b.h)) {
            int fh = FADE_H * fs;
            floor_add(c, (rc_t){b.x, b.y + b.h - fh, b.x + b.w, b.y + b.h}, 1);
        }
        if (!moy_ui_text_plan(t, v, s_trows, nrows, none, none)) {
            floor_add(c, rc_of(b), 1);
        } else {
            for (int i = 0; i < nrows; i++) {
                if ((t->floor[i >> 3] >> (i & 7)) & 1) {
                    int y = b.y + i * lh - v[MOY_UI_TV_OFF];
                    floor_add(c, (rc_t){b.x, y, b.x + b.w, y + lh}, 1);
                }
            }
        }
    }
    if (!recorded && armed(c) && c->fl_stage == 1) {
        floor_add(c, rc_of(b), 1);      // no record to say which rows moved: all of them
    }
    int64_t was = moy_text_print_font_use(font);
    clip_t kk;
    EACH_PIECE(c, kk, s, b) {
        moy_ui_text_paint(NULL, s, v, s_trows, nrows, none, none, 1);
    }
    if (was >= 0) {
        moy_text_print_font_use((uint32_t)was);
    }
    if (recorded) {
        moy_ui_text_note(t, v, s_trows, nrows, none, none);
    }
    // its rows' height, the scroll's range
    c->y = (int16_t)content;
    if (k != NULL) {
        k->b = content;
    }
    nest_close(c, s, 1);
}

void moy_ui_api_table(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                      const uint8_t *cols, size_t cols_n, const uint8_t *cells, size_t cells_n,
                      int32_t cur, const moy_ui_over_t *o, int32_t out[2]) {
    o = over(o);
    int nc = moy_ui_fields(cols, cols_n);
    if (nc < 1) {
        nc = 1;
    }
    int count = moy_ui_fields(cells, cells_n) / nc;
    int rh = row_h(c);
    int cap_h = label.n ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_CAPTION + (c->cls ? 1 : 0)) * c->fs
                        : 0;
    int named = 0;
    for (size_t i = 0; i < cols_n && !named; i++) {
        named = cols[i] != 0;
    }
    int head_h = named ? pmetric(c, MOY_UI_M_VALUE_ROW_HEAD) : 0;
    int placed = c->has_at;
    int stacked;
    c->pic_tbl = 0;
    moy_ui_box_t b = place(c, cap_h + head_h + (count ? count : 1) * rh, &stacked);
    moy_ui_box_t view = {b.x, (int16_t)(b.y + cap_h + head_h), b.w,
                         (int16_t)(b.h - cap_h - head_h > 0 ? b.h - cap_h - head_h : 0)};
    uint32_t id = id_of(o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled || !placed ? NULL : keep(c, id, 1);
    if (cur >= count) {
        cur = count - 1;
    }
    int focused = !disabled && c->focus == id && count > 0;
    int row = cur;
    if (focused) {
        row = cur < 0 ? 0 : cur + c->step_y;
    }
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
        p->flags = (uint16_t)(STOP_PAGE | (in_dialog(c) ? STOP_MODAL : 0));
        p->page = NO_PAGE;
        p->row = -1;
        p->top = 0;
    }
    int flags = STOP_KEEP_UD | (row <= 0 ? STOP_AT_TOP : 0) | (row >= count - 1 ? STOP_AT_END : 0)
                | (placed ? 0 : STOP_ROWS);
    int st = disabled ? 0 : stop(c, id, b, flags, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    int offset = kp != NULL ? kp->a : 0;
    // what the last frame drew, for a shift frame's floor
    int32_t was_drawn = kp != NULL ? kp->drawn : 0, was_b = kp != NULL ? kp->b : 0;
    int32_t was_gen = kp != NULL ? kp->gen : 0;
    int was_kd = kp != NULL ? kp->kd : 0, was_rh = kp != NULL ? kp->rh : 0;
    int opened = 0;
    if ((st & MOY_UI_ST_ACTIVATED) && count) {
        if (c->tap_y != INT16_MIN) {
            int r = moy_ui_fdiv(c->tap_y - view.y + offset, rh > 0 ? rh : 1);
            if (c->tap_y >= view.y && r >= 0 && r < count) {
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
        kp->b = count * rh;
        if (focused && (c->step_y || (st & MOY_UI_ST_ACTIVATED)) && cur >= 0) {
            int to = (kp->sc.flags & MOY_UI_SCROLL_GLIDE) ? kp->sc.target : offset;
            int was = to;
            if (cur * rh < to) {
                to = cur * rh;
            } else if ((cur + 1) * rh > to + view.h) {
                to = (cur + 1) * rh - view.h;
            }
            if (to != was && c->in[MOY_UI_IN_DT] > 0) {
                int32_t off = offset;   // the glide's first step is this frame's
                moy_ui_scroll_glide_to(&kp->sc, offset, to, 0, kp->b, view.h);
                moy_ui_scroll_tick(&kp->sc, (float)c->in[MOY_UI_IN_DT], &off, kp->b, view.h);
                offset = off;
            } else if (to != was) {
                offset = to;
            }
        }
        int most = count * rh - view.h;
        if (offset > most) {
            offset = most;
        }
        if (offset < 0) {
            offset = 0;
        }
        kp->a = offset;
        kp->drawn = offset;
        kp->gen = o->gen;
        kp->rh = (int16_t)rh;
        kp->s0 = kp->sn = 0;
    }
    rows_note(c, id, cur, view.y);
    if (!placed && focused && c->step_y && cur >= 0
        && !(c->step_rev == id && c->step_row == cur)) {
        c->reveal = 1;              // a step begin did not reveal: the next frame does
        c->reveal_box = (moy_ui_box_t){b.x, (int16_t)(view.y + cur * rh), b.w, (int16_t)rh};
        c->owe = 1;
    }
    c->tbl_y0 = (int16_t)(view.y - offset);
    c->tbl_rh = (int16_t)rh;
    c->tbl_n = (int16_t)count;
    c->tbl_view = view;
    c->tbl_px = c->tbl_pw = 0;
    if (st >= 0 && !disabled) {
        sig_int(c, cur);
        sig_mix(c, cells, cells_n);
    }
    moy_ui_tgeo_t g;
    moy_ui_table_geo(s, b, cols, cols_n, cells, cells_n, c->fs, c->cls, &g);
    if (g.pic && !disabled && c->n_cur > 0 && c->cur[c->n_cur - 1].id == id) {
        // the app draws its pictures: with no gen, the view never shifts
        c->cur[c->n_cur - 1].flags |= STOP_PIC | (o->has_gen ? STOP_GEN : 0);
    }
    int table_r = 0;
    if (kp != NULL) {
        kp->kd = (uint8_t)(MOY_UI_KD_PAGE | MOY_UI_KD_TABLE | MOY_UI_KD_DRAWN
                           | (o->has_gen ? MOY_UI_KD_GEN : 0)
                           | (g.pic && !o->has_gen ? MOY_UI_KD_BAD : 0));
        if (s_audit_on) {
            moy_ui_stop_t *vs = &c->cur[c->n_cur - 1];
            for (int i = c->n_cur - 1; i >= 0; i--) {
                if (c->cur[i].id == id && (c->cur[i].flags & STOP_PAGE)) {
                    vs = &c->cur[i];
                    break;
                }
            }
            vs->sig = hash(cells, cells_n, (uint32_t)nc);
        }
        if (armed(c) && c->fl_stage == 0 && id == c->sh_id) {
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
            const moy_ui_stop_t *q = table_of(c->prev, c->n_prev, id);
            if (q == NULL || !o->has_gen || !(was_kd & MOY_UI_KD_GEN) || o->gen != was_gen
                || !same_box(view, r) || offset - was_drawn != -c->sh_dy
                || was_b != count * rh || was_rh != rh) {
                c->fl_struct = 1;
                floor_rows(c, r.y, r.y + r.h, 1);
            } else {
                if (!early) {           // else begin's floor, the stops before R drawn through it
                    table_floor(c, view.y - offset, rh, q->row, (q->st & MOY_UI_ST_FOCUS) != 0,
                                cur, (st & MOY_UI_ST_FOCUS) != 0, g.pic, c->page.y + c->page.h);
                }
                if (s_audit_on) {
                    const moy_ui_stop_t *pv = page_of_keep(c->prev, c->n_prev, kp);
                    const moy_ui_stop_t *vs = page_of_keep(c->cur, c->n_cur, kp);
                    if (pv == NULL || vs == NULL || pv->sig != vs->sig) {
                        s_audit_fail++;     // rows unlike the last frame's under one gen
                    }
                }
            }
        }
    }
    clip_t k;
    band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, 0);
        if (cap_h) {
            moy_ui_caption(s, (moy_ui_box_t){b.x, b.y, b.w, (int16_t)cap_h}, label, c->fs, c->cls);
        }
        // in a group, its rows are the group's: each on its card under a rule
        int grp = stacked && c->grp_open;
        moy_ui_box_t gcard = card_of(c);
        if (g.pic) {
            c->tbl_px = g.x[0];
            c->tbl_pw = g.w[0];
        }
        if (head_h) {
            moy_ui_table_head(s, (moy_ui_box_t){b.x, (int16_t)(b.y + cap_h), b.w, (int16_t)head_h},
                              &g, cols, cols_n, c->fs, c->cls);
        }
        clip_t kv;
        int any = clip_in(&kv, s, view);
        size_t at = 0;
        int reach = table_r ? mark_room(c) : 0;
        for (int i = 0; any && i < count && at <= cells_n; i++) {
            size_t e = at;
            for (int j = 0; j < nc; j++) {
                while (e < cells_n && cells[e] != 0) {
                    e++;
                }
                if (j < nc - 1) {
                    e++;
                }
            }
            int ry = view.y + i * rh - offset;
            if (ry + rh > view.y && ry < view.y + view.h && (!table_r || clip_meets(s, ry, rh, reach))) {
                int f = (i == cur ? MOY_UI_F_ON : 0) | (disabled ? MOY_UI_F_DISABLED : 0);
                if ((st & MOY_UI_ST_FOCUS) && o->mark != 0 && i == cur) {
                    f |= MOY_UI_F_FOCUS;
                    if (grp) {
                        f &= ~MOY_UI_F_ON;      // a group's row: the focus mark alone
                    }
                }
                if (grp && c->modal && c->modal_from == id) {
                    f &= ~MOY_UI_F_ON;          // its focus under the dialog that took it
                }
                if (i == 0 && o->lead == 1) {
                    f |= MOY_UI_F_LEAD;
                }
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
        if (any) {
            // the row the view's foot cuts draws clipped to it, and the
            // fade, a row tall, lies over what shows of it (the desk's
            // Files ends its view half a row into one)
            edge_fades(c, s, view, count * rh, offset, MOY_UI_DEFAULT, 1, rh);
        }
        clip_out(&kv);
        clip_out(&k);
    }
    if (table_r) {
        ground_due(c, s);
        if (c->fl_lost && s_audit_on) {
            s_audit_fail++;
        }
        c->fl_stage = 2;
        c->pic_tbl = 1;
    }
    out[0] = cur;
    out[1] = opened;
}

int32_t moy_ui_api_field(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                         uint8_t *buf, size_t cap, size_t len, const moy_ui_over_t *o) {
    o = over(o);
    if (len > cap) {
        len = cap;
    }
    int stacked;
    // in a group the field fits the row (moy_ui_field); elsewhere the row
    // grows to hold the field and its ring
    int h = row_h(c), fh = c->grp_open ? h : moy_ui_field_h(s, c->fs, c->cls);
    // in a dialog's body a field is captioned: its label over it, the field
    // across the body
    int caph = label.n && !c->grp_open && c->n_nest && c->nest[c->n_nest - 1].kind == 2
                  ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_CAPTION + (c->cls ? 1 : 0)) * c->fs
                  : 0;
    moy_ui_box_t b = place(c, caph + (fh > h ? fh : h), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : stop(c, id_of(o, label), b, STOP_FIELD, name_of(o, label));
    int at = !disabled && st >= 0 ? c->n_cur - 1 : -1;
    if (st < 0) {
        st = 0;
    }
    int done = 0;
    if ((st & MOY_UI_ST_FOCUS) && c->typed) {
        int k = c->typed;
        c->typed = 0;
        for (int i = c->typed_n > 0 ? c->typed_n : 1; i > 0; i--) {
            if (k == 8 || k == 127) {
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
        int32_t h = (int32_t)hash(buf, len, 0x51ed270bu);
        c->cur[at].gen = h;
        int j = at - c->fl_s0;
        const moy_ui_stop_t *q = armed(c) && c->fl_stage == 1 && !c->fl_struct && !c->fl_mute
                                 && j >= 0 && j < c->fl_pn ? &c->prev[c->fl_p0 + j] : NULL;
        if (q != NULL && q->id == c->cur[at].id && q->gen != h && !floor_holds(c, b)) {
            if (c->sh_dlg) {
                dlg_widen(c, b.y, b.y + b.h);
            } else {
                floor_add(c, rc_of(b), 1);
            }
        }
    }
    clip_t k;
    int f = mark_flags(st, o) & ~MOY_UI_F_ON;
    band_mark(c, b, stacked, f);
    if (!disabled) {
        sig_mix(c, buf, len);
    }
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, f);
        moy_ui_span_t t = {buf, (uint16_t)len};
        moy_ui_box_t fb = b;
        moy_ui_span_t lb = label;
        if (caph) {
            // the caption's letters over the field's left edge
            int gp = kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * c->fs;
            int fp = c->cls ? gp : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
            moy_ui_caption(s, (moy_ui_box_t){(int16_t)(b.x + fp - gp), b.y, b.w, (int16_t)caph},
                           label, c->fs, c->cls);
            fb.y = (int16_t)(b.y + caph);
            fb.h = (int16_t)(b.h - caph);
            lb.n = 0;
        }
        moy_ui_field(s, fb, lb, t, o->hint, f | (disabled ? MOY_UI_F_DISABLED : 0), c->fs,
                     c->cls, c->grp_open, c->grp_open && c->grp_first);
        clip_out(&k);
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

static void wash(const moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_scrim(s, moy_ui_api_wash_box(c));
}

void moy_ui_api_wash(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    c->fl_dirty = 1;                    // over the page: the next frame draws it whole
    wash(c, s);
}

int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o) {
    o = over(o);
    if (title.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 2) {
            int used = c->nest[c->n_nest - 1].used;
            int took = nest_close(c, s, 2);
            if (took != used) {
                c->owe = 1;             // laid out for another body: the next frame redraws
            }
        }
        return -1;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return -1;
    }
    group_close(c, s);
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
    int fs = c->fs, rh = row_h(c);
    uint32_t id = hash(title.p, title.n, DIALOG_SALT);
    moy_ui_keep_t *kb = keep_of(c, id);
    int body = kb != NULL && kb->b > 0 ? kb->b : rh;
    if (!c->modal) {
        c->owe = 1;                     // its first frame: focus opens in it on the next
    }
    int pad = moy_ui_modal_band(fs, c->cls);
    int nb = moy_ui_fields(buttons, len);
    int foot = nb ? rh + 2 * pad : pad;     // no buttons: the keys are the hint strip's
    int top = pad + rh + pad / 2;
    moy_ui_box_t sf = c->surface;
    int w = pmetric(c, MOY_UI_M_VALUE_ROW_MODAL);
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
    uint32_t btn = hash(buttons, len, id);
    c->dlg_held = (uint8_t)(o->wash == 0 && o->has_gen);
    c->dlg_gen = o->gen;
    c->dlg_btn = btn;
    if (rows) {
        c->fl_stage = 1;
        if (id != c->sh_id || !same_box(card, c->dw_card) || !c->dlg_held || o->gen != c->dw_gen
            || btn != c->dw_btn) {
            c->fl_mute = 1;             // not the dialog planned: nothing this frame
            c->owe = 1;
        }
    }
    if (o->wash != 0 && !c->fl_mute) {
        wash(c, s);                     // everything under a dialog dims (D-01)
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
    int lp = c->cls ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * fs
                    : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    inner.x = (int16_t)(card.x + pad - lp);
    inner.w = (int16_t)(w - 2 * (pad - lp));
    inner.y = (int16_t)(card.y + top);
    inner.h = (int16_t)bh;
    c->dlg_foot = (int16_t)(inner.y + inner.h);
    nest_open(c, 2, id, inner, body, 0, NULL);
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
        int st = stop(c, hash(t.p, t.n, id), bb, STOP_DLG_BTN, t);
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
            moy_ui_button(s, bb, moy_ui_button_kind(kn, strlen(kn)), mark_flags(st, &none),
                          interact(st), &bt, MOY_UI_PHASE_ALL);
        }
        x += bw[i] + gap;
    }
    c->page_stop = body_stop;
    c->dlg_pick = (int8_t)pick;
    return pick;
}

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
    o = over(o);
    if (id.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 3) {
            int used = c->nest[c->n_nest - 1].used;
            moy_ui_keep_t *pk = page_keep(c);
            int took = nest_close(c, s, 3);
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
        return 0;
    }
    group_close(c, s);
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
    int fs = c->fs, rh = row_h(c);
    uint32_t pid = hash(id.p, id.n, POPOVER_SALT);
    moy_ui_keep_t *kb = keep_of(c, pid);
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
    // the card keeps `gap` clear of every edge of the surface and never
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
        wash(c, s);                     // a row's or a card's menu (DESIGN, Menus and washes)
    }
    moy_ui_modal(s, card, (moy_ui_span_t){none, 0}, fs, c->cls, rh);
    int lp = c->cls ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * fs
                    : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    moy_ui_box_t inner = {(int16_t)(card.x + pad - lp), (int16_t)(card.y + pad),
                          (int16_t)(w - 2 * (pad - lp)), (int16_t)bh};
    nest_open(c, 3, pid, inner, body, 0, NULL);
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
    o = over(o);
    group_close(c, s);
    int fs = c->fs, rh = row_h(c);
    // placed: the rows from ui.at's rect's top left, across its width
    int placed = c->has_at;
    moy_ui_box_t at = c->at;
    c->has_at = 0;
    int ay = at.y;
    if (label.n) {
        int h = kmetric(MOY_UI_K_GROUP, c->cls ? MOY_UI_M_GROUP_CAPTION_DESK
                                               : MOY_UI_M_GROUP_CAPTION) * fs;
        int stacked = 0;
        moy_ui_box_t b = {at.x, (int16_t)ay, at.w, (int16_t)h};
        if (placed) {
            ay += h;
        } else {
            b = place(c, h, &stacked);
        }
        // the caption starts where its rows' icons do: at the rows' pad
        // (moy_ui_vrow's), where the caption's own is a group's
        int rpad = (c->grp_open || c->cls) ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)
                                           : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD);
        int dx = (rpad - kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD)) * fs;
        moy_ui_box_t cb = {(int16_t)(b.x + dx), b.y, (int16_t)(b.w - dx), b.h};
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_caption(s, cb, label, fs, c->cls);
            clip_out(&k);
        }
    } else if (!placed && c->y > 0) {
        int stacked;
        moy_ui_box_t b = place_row(c, rule_h(c), &stacked, 0);
        clip_t k;
        EACH_ROW(c, k, s, b, stacked, c->page) {
            moy_ui_rule(s, b, fs, c->cls);
            clip_out(&k);
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
            b = place(c, rh, &stacked);
        }
        int st = vrow_stop(c, hash(t.p, t.n, 0), b, 0, o, t);
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
        vrow_init(c, &r, t);
        r.value = key;
        r.keys = 1;
        if (i < 32 && ((c->menu_danger >> i) & 1u)) {
            r.kind = MOY_UI_VROW_DANGER;
        }
        vrow_draw(c, s, b, stacked, MOY_UI_V_INFO, st, o, &r);
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
    grid_end(c, s);
    int slot = moy_ui_icon_slot(icon.p, icon.n), glyph = moy_ui_icon_glyph(icon.p, icon.n);
    moy_ui_box_t m = c->has_at ? c->at : c->page;
    int h = moy_ui_empty(s, m, slot, glyph, title, lines, n, c->fs, ground_of(c), 0);
    int stacked;
    moy_ui_box_t b = place(c, h, &stacked);
    b.x = m.x;
    b.w = m.w;
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_empty(s, b, slot, glyph, title, lines, n, c->fs, ground_of(c), 1);
        clip_out(&k);
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
        group_close(c, s);
        b = place(c, h, &stacked);
        int pad = kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
        b.x = (int16_t)(b.x + pad);
        b.w = (int16_t)(b.w - 2 * pad);
    }
    int chevrons = c->cls || moy_ui_input() == MOY_UI_INPUT_TOUCH;
    moy_ui_box_t ch[2] = {{0, 0, 0, 0}, {0, 0, 0, 0}};
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_month(s, b, title, days, first, n, today, chevrons, c->fs, c->cls, ch);
        clip_out(&k);
    }
    // a press on a chevron pages the month
    int p = c->in[MOY_UI_IN_PTR];
    if (chevrons && (p & MOY_UI_P_LIVE) && (p & MOY_UI_P_EDGE)) {
        int x = c->in[MOY_UI_IN_X], y = c->in[MOY_UI_IN_Y];
        for (int i = 0; i < 2; i++) {
            if (inside(ch[i], x, y)) {
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
    moy_ui_toast(s, m, text, slot, glyph, c->fs, ground_of(c), flat, &p);
    if (out != NULL) {
        out[0] = p.x;
        out[1] = p.y;
        out[2] = p.w;
        out[3] = p.h;
    }
}

// -- card grids ----------------------------------------------------------------------

// The card drawn last: its picture finished over what the app drew in it.
static void card_finish(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
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
                              c->fin_ground, c->fs, ground_of(c));
        }
        moy_ui_card_end(s, c->fin_pic, c->fin_content, c->fin_flags ? MOY_UI_F_FOCUS : 0,
                        c->fin_ground, c->fs, ground_of(c));
        clip_out(&k);
    }
}

// The open grid ends: its last card finished, the page's cursor under its
// last row, and a group it opened in holding its rows' card from there.
static void grid_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->grid_open) {
        return;
    }
    card_finish(c, s);
    if (c->grid_open == 1) {
        if (c->grid_i > 0) {
            c->y = (int16_t)(c->grid_y + c->grid_row);
        }
        moy_ui_keep_t *k = page_keep(c);
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
    (void)over(o);
    grid_end(c, s);
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
        group_close(c, s);          // a grid lies on the page's ground
    }
    int pad = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD);
    int gap = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_GAP);
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
                                     : pmetric(c, MOY_UI_M_VALUE_ROW_GRID_TOP)));
        c->grid_y = c->y;
    }
    int cols = (area.w - 2 * pad + gap) / (w + gap);
    cols = cols < 1 ? 1 : cols > 255 ? 255 : cols;
    int span = cols * w + (cols - 1) * gap;
    c->grid_cols = (uint8_t)cols;
    c->grid_i = 0;
    c->grid_x = (int16_t)(area.x + (area.w - span) / 2);
    c->grid_pitch = (int16_t)(w + gap);
    int foot = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_FOOT);
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
    c->grid_row = (int16_t)(pmetric(c, MOY_UI_M_VALUE_ROW_CARD_TOP) + h + foot
                            + pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP));
}

void moy_ui_api_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t sub, const moy_ui_over_t *o, int32_t out[5]) {
    o = over(o);
    card_finish(c, s);
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
        moy_ui_keep_t *k = page_keep(c);
        if (k != NULL) {
            k->b = c->y;
        }
    }
    if (c->grid_i < 0xffff) {
        c->grid_i++;
    }
    c->icon_slot = c->icon_glyph = -1;
    int pad = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD), top = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_TOP);
    int stacked = c->grid_open == 1;
    int y = stacked ? c->page.y + c->grid_y - c->scroll : c->grid_y;
    moy_ui_box_t pic = {(int16_t)(c->grid_x + col * c->grid_pitch), (int16_t)(y + top), c->grid_w,
                        c->grid_h};
    moy_ui_box_t b = {(int16_t)(pic.x - pad), (int16_t)y, (int16_t)(pic.w + 2 * pad),
                      (int16_t)(c->grid_row - pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP))};
    int st = 0, moved = 0;
    c->at_last = (uint8_t)!stacked;
    if (o->disabled != 1) {
        st = stop(c, id_of(o, title), b, 0, name_of(o, title));
        if (st >= 0) {
            // a blank card's picture is the engine's tile: the app draws none
            moved = pic_note(c, o, c->card_blank < 0 && c->card_blank_glyph < 0);
            c->cur[c->n_cur - 1].flags |= STOP_GRID;
            sig_mix(c, sub.p, sub.n);
        }
        st = st < 0 ? 0 : st;
    }
    int f = mark_flags(st, o);
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
                    ground_of(c), c->card_tone, &content, &g);
        memcpy(&row->st[MOY_ST_CX0], saved, sizeof(saved));
        pic_redraw(c, content);
    }
    c->fl_cast = 1;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        moy_ui_card(s, b, pic, title, sub, f | (c->grid_wrap ? MOY_UI_F_WRAP : 0), c->fs,
                    ground_of(c), c->card_tone, &content, &g);
        if (c->card_blank >= 0 || c->card_blank_glyph >= 0) {
            moy_ui_card_blank(s, content, c->card_blank, c->card_blank_glyph, c->card_tone,
                              c->fs, ground_of(c));
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
    if (outside_floor(c, b)) {
        return;                     // a shift frame's: what the app drew there stands
    }
    c->pic_clip = floor_of(c, content);
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
    edge_fades(c, s, pic, c->tbl_n * c->tbl_rh, v.y - c->tbl_y0, MOY_UI_DEFAULT, 1, c->tbl_rh);
}

void moy_ui_api_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (c->n_nest == 0) {
        group_close(c, s);
        foot_fade(c, s);
        page_close(c, s);
    }
    if (c->tip_at.w > 0 && c->tip[0]) {
        moy_ui_span_t t = {(const uint8_t *)c->tip, (uint16_t)strlen(c->tip)};
        c->fl_dirty = 1;                // over the page, whole
        c->fl_top = 1;
        moy_ui_tip_draw(s, c->tip_at, t, c->surface, c->fs, ground_of(c));
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
    o = over(o);
    int slot = moy_ui_icon_slot(icon.p, icon.n), glyph = moy_ui_icon_glyph(icon.p, icon.n);
    moy_ui_span_t word = tool_word(label);
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : stop(c, id_of(o, word), b, 0, name_of(o, word));
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    EACH_ROW(c, k, s, b, stacked, c->page) {
        int fl = mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0);
        moy_ui_tool_draw(s, b, slot, glyph, word, fl, interact(st), c->fs, ground_of(c));
        clip_out(&k);
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
            moy_ui_line(s, b, c->fs, ground_of(c));
        }
        return;
    }
    group_close(c, s);
    int stacked;
    moy_ui_box_t r = place(c, rule_h(c), &stacked);
    clip_t k;
    EACH_ROW(c, k, s, r, stacked, c->page) {
        moy_ui_rule(s, r, c->fs, c->cls);
        clip_out(&k);
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
    int pg = page_at(c->cur, c->n_cur, x, y);
    moy_ui_keep_t *k = pg >= 0 ? keep_of(c, c->cur[pg].id) : NULL;
    return k != NULL ? k->a : -1;
}

// -- shift frames (docs/scroll_and_text_2026-10.md §3.3) ---------------------------

int moy_ui_api_shift_plan(const moy_ui_ctx_t *c, int32_t out[8]) {
    out[0] = c->sh_r.x;
    out[1] = c->sh_r.y;
    out[2] = c->sh_r.w;
    out[3] = c->sh_r.h;
    out[4] = c->sh_dy;
    out[5] = c->sh_rows;
    out[6] = c->sh_pics;
    out[7] = c->sh_dlg ? c->dw_n : 0;
    return c->sh_plan;
}

void moy_ui_api_shift_arm(moy_ui_ctx_t *c) {
    if (c->sh_plan != MOY_UI_SHIFT_NONE) {
        c->fl_on = 1;
    }
}

int moy_ui_api_shift_drawn(const moy_ui_ctx_t *c, int *clean) {
    if (clean != NULL) {
        // a dialog over a held page leaves R (its card) its own where
        // nothing drew over it
        *clean = c->dlg_id ? c->dlg_held && !c->fl_top && !c->fl_mute : !c->fl_dirty;
    }
    if (!c->fl_on || c->fl_stage == 0) {
        return MOY_UI_SHIFT_NONE;
    }
    return c->sh_plan | (c->fl_struct ? MOY_UI_SHIFT_WIDE : 0);
}

int moy_ui_api_floor(const moy_ui_ctx_t *c, int32_t *out, int cap) {
    if (!c->fl_on || c->fl_stage == 0) {
        return -1;
    }
    for (int i = 0; i < c->fl_n && i < cap; i++) {
        out[4 * i] = c->fl[i].x;
        out[4 * i + 1] = c->fl[i].y;
        out[4 * i + 2] = c->fl[i].w;
        out[4 * i + 3] = c->fl[i].h;
    }
    return c->fl_n;
}

int moy_ui_api_pop_box(const moy_ui_ctx_t *c, int32_t out[4]) {
    moy_ui_box_t b = c->tip_shown ? c->surface
                     : c->pop_id ? c->pop_card : (moy_ui_box_t){0, 0, 0, 0};
    out[0] = b.x;
    out[1] = b.y;
    out[2] = b.w;
    out[3] = b.h;
    return b.w > 0 && b.h > 0;
}

int moy_ui_api_floor_box(const moy_ui_ctx_t *c, int32_t out[4]) {
    if (!c->fl_on || c->fl_stage == 0 || c->fl_n == 0) {
        return 0;
    }
    rc_t u = rc_of(c->fl[0]);
    for (int i = 1; i < c->fl_n; i++) {
        rc_t f = rc_of(c->fl[i]);
        u.x0 = f.x0 < u.x0 ? f.x0 : u.x0;
        u.y0 = f.y0 < u.y0 ? f.y0 : u.y0;
        u.x1 = f.x1 > u.x1 ? f.x1 : u.x1;
        u.y1 = f.y1 > u.y1 ? f.y1 : u.y1;
    }
    out[0] = u.x0;
    out[1] = u.y0;
    out[2] = u.x1 - u.x0;
    out[3] = u.y1 - u.y0;
    return 1;
}

int moy_ui_api_card_clip(const moy_ui_ctx_t *c, int32_t r[4]) {
    if (c->pic_tbl) {
        // a picture on a row of the table drawn last as R: the floor's rect
        // its row lies in, else none of it
        rc_t q = {r[0], r[1], r[0] + r[2], r[1] + r[3]};
        if (!rc_meets(q, rc_of(c->sh_r))) {
            return 1;
        }
        rc_t got = {q.x0, q.y0, q.x0, q.y0};
        for (int i = 0; i < c->fl_n; i++) {
            rc_t f = q;
            if (rc_cut(&f, rc_of(c->fl[i]))) {
                got = f;
                break;
            }
        }
        r[0] = got.x0;
        r[1] = got.y0;
        r[2] = got.x1 - got.x0;
        r[3] = got.y1 - got.y0;
        return 1;
    }
    moy_ui_box_t f = c->pic_clip;
    if (f.w <= 0 && f.h >= 0) {
        return 0;
    }
    rc_t q = {r[0], r[1], r[0] + r[2], r[1] + r[3]};
    if (f.w <= 0 || !rc_cut(&q, rc_of(f))) {
        q.x1 = q.x0;
        q.y1 = q.y0;
    }
    r[0] = q.x0;
    r[1] = q.y0;
    r[2] = q.x1 - q.x0;
    r[3] = q.y1 - q.y0;
    return 1;
}

int moy_ui_api_band(const moy_ui_ctx_t *c, int32_t out[4]) {
    moy_ui_box_t pg = c->page;
    if (c->page_stop == NO_PAGE) {
        return 0;
    }
    rc_t b = rc_of(pg);
    const moy_ui_keep_t *k = c->page_keep >= 0 ? &c->keep[c->page_keep] : NULL;
    if (armed(c) && c->fl_stage == 1 && k != NULL && k->id == c->sh_id && !c->fl_struct) {
        rc_t u = {0, 0, 0, 0};
        for (int i = 0; i < c->fl_n; i++) {
            rc_t f = rc_of(c->fl[i]);
            if (u.x1 <= u.x0) {
                u = f;
            } else {
                u.x0 = f.x0 < u.x0 ? f.x0 : u.x0;
                u.y0 = f.y0 < u.y0 ? f.y0 : u.y0;
                u.x1 = f.x1 > u.x1 ? f.x1 : u.x1;
                u.y1 = f.y1 > u.y1 ? f.y1 : u.y1;
            }
        }
        b = u;
        if (b.x1 <= b.x0) {
            b = (rc_t){pg.x, pg.y, pg.x, pg.y};
        }
    }
    out[0] = b.x0 - pg.x;
    out[1] = b.y0 - pg.y + c->scroll;
    out[2] = b.x1 - b.x0;
    out[3] = b.y1 - b.y0;
    return 1;
}

int moy_ui_api_keep(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int h) {
    uint32_t sid = hash(id.p, id.n, 0);
    int i = find(c->prev, c->n_prev, sid);
    moy_ui_box_t b;
    int stacked;
    if (c->grid_open) {
        // a card of the open grid, where the next card would lie
        card_finish(c, s);
        int col = c->grid_i % c->grid_cols;
        if (col == 0 && c->grid_i > 0) {
            c->grid_y = (int16_t)(c->grid_y + c->grid_row);
        }
        if (col == 0 && c->grid_open == 1) {
            c->y = (int16_t)(c->grid_y + c->grid_row);
            moy_ui_keep_t *k = page_keep(c);
            if (k != NULL) {
                k->b = c->y;
            }
        }
        if (c->grid_i < 0xffff) {
            c->grid_i++;
        }
        int pad = pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD);
        stacked = c->grid_open == 1;
        int y = stacked ? c->page.y + c->grid_y - c->scroll : c->grid_y;
        b = (moy_ui_box_t){(int16_t)(c->grid_x + col * c->grid_pitch - pad), (int16_t)y,
                           (int16_t)(c->grid_w + 2 * pad),
                           (int16_t)(c->grid_row - pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP))};
        c->at_last = (uint8_t)!stacked;
    } else {
        b = place(c, h > 0 ? h : row_h(c), &stacked);
        if (i >= 0) {
            b.x = c->prev[i].b.x;
            b.w = c->prev[i].b.w;
        }
    }
    uint16_t flags = i >= 0 ? (uint16_t)(c->prev[i].flags
                                          & (STOP_KEEP_LR | STOP_KEEP_UD | STOP_AT_TOP
                                             | STOP_AT_END | STOP_HOLD | STOP_FIELD))
                            : 0;
    int st = stop(c, sid, b, flags, id);
    if (st < 0) {
        return 0;
    }
    if (i >= 0) {
        moy_ui_stop_t *q = &c->cur[c->n_cur - 1];
        q->flags |= c->prev[i].flags & (STOP_PIC | STOP_GEN | STOP_GRID);
        q->gen = c->prev[i].gen;
        if (s_audit_on) {
            q->sig = c->prev[i].sig;
        }
    }
    return st;
}

uint32_t moy_ui_api_audit(int on) {
    uint32_t n = s_audit_fail;
    s_audit_fail = 0;
    if (on >= 0) {
        s_audit_on = (uint32_t)(on != 0);
    }
    return n;
}
