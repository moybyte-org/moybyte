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
    STOP_HOLD = 32,         // nav = false on a part: focus, the keys, a held press are its
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

// The salt of the ids drawn now: a part's (its table's and its row's) while
// one is open (moy_ui_api_table_part), else none.
static uint32_t s_scope;

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
    o->has_part = 0;
    o->part_w = o->part_h = 0;
    o->bar = -1;
    o->axis_x = o->snap = o->has_offset = 0;
    o->offset = 0;
    o->spill = 0;
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
                    at += 4u;
                }
                if ((fl & MOY_UI_O_PART) && n - at >= 8u) {
                    o->has_part = 1;
                    o->part_w = (int16_t)word(w + at, 0);
                    o->part_h = (int16_t)word(w + at, 1);
                    at += 8u;
                }
                o->bar = (int8_t)((fl & MOY_UI_O_BAR) != 0 ? 1 : -1);
                o->axis_x = (int8_t)((fl & MOY_UI_O_AXIS_X) != 0);
                o->snap = (int8_t)((fl & MOY_UI_O_SNAP) != 0);
                if ((fl & MOY_UI_O_OFFSET) && n - at >= 4u) {
                    o->has_offset = 1;
                    o->offset = word(w + at, 0);
                    at += 4u;
                }
                if ((fl & MOY_UI_O_SPILL) && n - at >= 4u) {
                    int32_t sp = word(w + at, 0);
                    o->spill = (int16_t)(sp < 0 ? 0 : sp > 4096 ? 4096 : sp);
                    at += 4u;
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
                                       -1, -1, -1, -1, -1, {NULL, 0}, -1, -1, 0, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0};
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
    moy_ui_text_views_drop(c);
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

static moy_ui_keep_t *keep_of(moy_ui_ctx_t *c, uint32_t id);

// The topmost page at (x, y) that scrolls across (`across`), or down: its
// stop's index, or -1.
static int page_on(moy_ui_ctx_t *c, const moy_ui_stop_t *v, int n, int x, int y, int across) {
    for (int i = n - 1; i >= 0; i--) {
        if ((v[i].flags & STOP_PAGE) && inside(v[i].b, x, y)) {
            const moy_ui_keep_t *k = keep_of(c, v[i].id);
            if ((k != NULL && (k->kd & MOY_UI_KD_X)) == (across != 0)) {
                return i;
            }
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
    *extent = (k->kd & MOY_UI_KD_X) ? p->b.w : p->b.h;
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
        const moy_ui_stop_t *p = page_of_keep(v, n, k);
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

static void table_in_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                           int mark, int of);

static void view_in_floor(moy_ui_ctx_t *c, int of);

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
    c->sh_in_id = 0;
    c->sh_in_dy = 0;
    c->sh_over.w = 0;
    int p = c->in[MOY_UI_IN_PTR];
    int live = p & MOY_UI_P_LIVE, held = live && (p & MOY_UI_P_HELD);
    int same = c->was_flags == (uint16_t)(flags & ~MOY_UI_KEY_EDGE) && c->was_fs == c->fs
               && c->was_hint == (uint8_t)moy_ui_hint_input()
               && same_box(c->was_surface, c->surface);
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
        const moy_ui_stop_t *ps = ok ? page_of_keep(v, n, k) : NULL;
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
    moy_ui_keep_t *t = c->tc_id ? keep_of(c, c->tc_id) : NULL;
    if (t != NULL && (t->kd & MOY_UI_KD_TABLE) && (r == NULL || r == t)) {
        moy_ui_keep_t *h = view_holder(c, v, n, t, table_of(v, n, t->id));
        const moy_ui_stop_t *hp = h != NULL ? page_of_keep(v, n, h) : NULL;
        const moy_ui_stop_t *bp = best != NULL ? page_of_keep(v, n, best) : NULL;
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
        moy_ui_keep_t *h = view_holder(c, v, n, tv, page_of_keep(v, n, tv));
        if (h != NULL && page_of_keep(v, n, h) != NULL) {
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
    const moy_ui_stop_t *rp = page_of_keep(v, n, r);
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
        ip = page_of_keep(v, n, in);
        idy = (int)(in->drawn - in->a);
        if (ip == NULL || in->rh <= 0 || (idy < 0 ? -idy : idy) >= ip->b.h) {
            return;
        }
        if (live && !held && idy && inside(ip->b, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y])
            && stop_at(v, n, c->in[MOY_UI_IN_X], c->in[MOY_UI_IN_Y] - idy) >= 0) {
            return;                     // the move slides a stop under the pointer
        }
        c->sh_in_id = in->id;
        c->sh_in = ip->b;
        c->sh_in_dy = (int16_t)idy;
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
        c->sh_in_id = 0;
        return;
    }
    int pic = tp != NULL && (tp->flags & STOP_PIC);
    if (in != NULL) {
        // the page's floor, then the table's inside it, as its pass finds them
        floor_build(c, r->s0, r->sn, rp->b.y + rp->b.h);
        const moy_ui_stop_t *ts = table_of(v, n, in->id);
        if (ts != NULL) {
            int count = in->b / in->rh, now = ts->row;
            int mark = (ts->st & MOY_UI_ST_FOCUS) != 0, will = c->focus == in->id && !c->elsewhere;
            if (c->step_y && now >= 0) {
                now += c->step_y;
                now = now >= count ? count - 1 : now;
                now = now < 0 ? 0 : now;
            }
            table_in_floor(c, ip->b.y - in->a, in->rh, ts->row, mark, now, will,
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
    c->win = (uint8_t)((flags & MOY_UI_WINDOW) ? 1 : 0);
    c->elsewhere = (uint8_t)((flags & MOY_UI_ELSEWHERE) ? 1 : 0);
    c->tp_id = 0;                       // the parts a last frame left open are done
    c->tp_open = 0;
    s_scope = 0;
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
        c->press_bare = (uint8_t)(h < 0);
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
                moy_ui_keep_t *k = keep_of(c, c->drag_page);
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
        c->drag_x = 0;
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
            int step = !(k->kd & MOY_UI_KD_TABLE) && k->rh > 0 ? k->rh : row_h(c);
            moy_ui_scroll_step(&k->sc, k->a, wy, MOY_UI_WHEEL_ROWS * step, content, extent);
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
        int i = find(v, n, rid);
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
    c->reveal_id = 0;
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

// A stop `ui.at` placed both frames that moved where the floor holds both
// its boxes (the last frame's moved with R, and its mark's reach where it
// carries one): it moves nothing else, and the floor already redraws it.
static int placed_move(const moy_ui_ctx_t *c, const moy_ui_stop_t *q, const moy_ui_stop_t *s) {
    if (q->id != s->id || !(q->flags & STOP_AT) || !(s->flags & STOP_AT)) {
        return 0;
    }
    int m = ((q->st | s->st) & MARKS) ? mark_room(c) : 0;
    moy_ui_box_t a = {(int16_t)(q->b.x - m), (int16_t)(q->b.y + c->sh_dy - m),
                      (int16_t)(q->b.w + 2 * m), (int16_t)(q->b.h + 2 * m)};
    moy_ui_box_t b = {(int16_t)(s->b.x - m), (int16_t)(s->b.y - m), (int16_t)(s->b.w + 2 * m),
                      (int16_t)(s->b.h + 2 * m)};
    return floor_holds(c, a) && floor_holds(c, b);
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
    if (!armed(c) || c->fl_stage != 1 || c->fl_struct || i < c->fl_s0) {
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
        || (((q->st ^ s->st) & MARKS) && !floor_holds(c, s->b))) {
        if (q != NULL && placed_move(c, q, s)) {
            return;
        }
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
        if (c->lost < 0xffff) {
            c->lost++;                  // drawn, but no stop: said once (moy_ui_api_lost)
        }
        return -1;
    }
    int dlg = in_dialog(c);
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
    return o->id.p != NULL ? hash(o->id.p, o->id.n, s_scope) : hash(label.p, label.n, s_scope);
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
    if (c->sh_over.w > 0) {
        floor_add(c, rc_of(c->sh_over), 1);     // what the console drew over R
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
    int reach = mark_room(c);
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
static void table_strip(moy_ui_ctx_t *c, int y0, int y1, int base, int rh, int pic) {
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
    if (rc_cut(&q, rc_of(c->sh_in))) {
        floor_add(c, q, 1);
    }
}

// The table carrying its rows moves inside R (its view sh_in, by
// sh_in_dy): its floor -- the band the move uncovered, the rows its edges
// fade over (a row tall, the last frame's moved and this one's) and the
// outer page's foot fade (`of`, its bottom), and where the focused row or
// its mark moved (`was`, `was_mark` the last frame's; `now`, `mark` this
// one's), each row reached a mark's reach about it. `base` is row 0's top
// on screen this frame.
static void table_in_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
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
        int reach = mark_room(c);
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
static void view_in_floor(moy_ui_ctx_t *c, int of) {
    int dy = c->sh_in_dy;
    if (!dy) {
        return;
    }
    int foot = c->sh_in.y + c->sh_in.h, f = FADE_H * c->fs;
    rc_t a = {c->sh_in.x, foot - f + (dy < 0 ? dy : 0), c->sh_in.x + c->sh_in.w, foot};
    rc_t b = {c->sh_in.x, of - f, c->sh_in.x + c->sh_in.w, of};
    if (rc_cut(&a, rc_of(c->sh_in))) {
        floor_add(c, a, 1);
    }
    if (rc_cut(&b, rc_of(c->sh_in))) {
        floor_add(c, b, 1);
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
    int dy = c->sh_dy, reach = mark_room(c), early = 0, at = c->n_prev;
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
    c->page_new = (uint8_t)(keep_of(c, id) == NULL);
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
static void page_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = page_keep(c);
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
    if (armed(c) && c->fl_stage == 1 && k->id == c->sh_id) {
        int cn = c->sh_in_id ? in_count(c, &c->cur[s0], sn) : sn;
        int pn = c->sh_in_id ? in_count(c, &c->prev[c->fl_p0], c->fl_pn) : c->fl_pn;
        if (!c->fl_struct && !c->fl_mute && cn < pn) {
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
    k->kd |= MOY_UI_KD_DRAWN | (bad ? MOY_UI_KD_BAD : 0) | (at ? MOY_UI_KD_AT : 0);
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
    // a page's id is its title, or `o.id` where two pages share one
    moy_ui_span_t pid = o->id.p != NULL ? o->id : title;
    open_page(c, hash(pid.p, pid.n, PAGE_SALT), ct, o->nav == 0 ? STOP_NAV_OFF : 0, o, ct);
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
    if (st >= 0 && !disabled && c->n_cur > 0 && c->cur[c->n_cur - 1].id == pid) {
        c->cur[c->n_cur - 1].part = 1;
    }
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
    if (b.w <= 0 || b.h <= 0 || !inside(b, x, y)) {
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
// size of the pane before it then, `over` the position its gap is dragged
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
    moy_ui_keep_t *k = keep(c, hash(id.p, id.n, 0x5911u), 1);
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
                if (gap > 0 && inside(split_hitbox(g, across, hw, hl), px, py)) {
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
                if (inside((moy_ui_box_t){(int16_t)out[4 * i], (int16_t)out[4 * i + 1],
                                          (int16_t)out[4 * i + 2], (int16_t)out[4 * i + 3]},
                           px, py)) {
                    cur = i;
                    break;
                }
            }
        } else if (k != NULL && c->focus != 0 && (uint32_t)k->drawn != c->focus) {
            int fx = c->focus_box.x + c->focus_box.w / 2, fy = c->focus_box.y + c->focus_box.h / 2;
            for (int i = 0; i < n; i++) {
                if (inside((moy_ui_box_t){(int16_t)out[4 * i], (int16_t)out[4 * i + 1],
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
        if (inside(c->sp_hit[i], x, y)) {
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
        if (!(c->sp_cut & (1u << j)) && inside(c->sp_pane[j], x, y)) {
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
static int s_no_foot;               // the region closing draws no fade at its foot

static void foot_fade(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL && !s_no_foot) {
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

int32_t moy_ui_api_scroll(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                          const moy_ui_over_t *o) {
    o = over(o);
    if (id.n == 0) {
        nest_close(c, s, 1);
        return 0;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
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
    return 0;
}


// -- ui.text_view ------------------------------------------------------------------------

static int16_t s_tones[6] = {-1, -1, -1, -1, -1, -1};

void moy_ui_text_tones(const int16_t tones[6]) {
    memcpy(s_tones, tones, sizeof(s_tones));
}

#define TEXT_SALT 0x27d4eb2fu
#define TEXT_PAD 8                  // the text's inset from the view's left, at the scale
#define TEXT_MARK 3                 // the current line's mark, in from the view's left
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
    rc_t in = rc_of(b);
    uint32_t bit = 1;
    for (int i = 0; i < c->fl_n; i++, bit <<= 1) {
        rc_t f = rc_of(c->fl[i]);
        if (f.x0 >= in.x0 && f.y0 >= in.y0 && f.x1 <= in.x1 && f.y1 <= in.y1) {
            c->fl_pend &= ~bit;
        }
    }
    int32_t *st = ((moy_canvas_row_t *)s->dst)->st;
    rc_t q[4 + MOY_UI_API_FLOOR];
    int nq = 0;
    clip_t k;
    for (int ok = clip_row(c, &k, s, b, 0, b); ok; ok = clip_next(&k)) {
        if (k.c == NULL) {
            // no pieces: the one clip there is
            moy_ui_text_paint(NULL, s, v, s_trows, nrows, err, find, 1);
            continue;
        }
        rc_t p = {st[MOY_ST_CX0], st[MOY_ST_CY0], st[MOY_ST_CX1], st[MOY_ST_CY1]};
        rc_t bb = {in.x0 - st[MOY_ST_CAM_X], in.y0 - st[MOY_ST_CAM_Y], in.x1 - st[MOY_ST_CAM_X],
                   in.y1 - st[MOY_ST_CAM_Y]};
        if (!rc_meets(p, bb)) {
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

// A text view's paper (sheet `b`, corners `r`): its bands `band` tall at
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
    o = over(o);
    out[0] = out[1] = 0;
    s_trows = moy_ui_text_scratch();
    s_starts = moy_ui_text_breaks();
    if (c->n_nest >= MOY_UI_API_NEST) {
        return MOY_UI_API_DEEP;
    }
    if (id.n == 0 || s_trows == NULL) {
        return 0;
    }
    group_close(c, s);
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
    if (armed(c) && c->sh_in_id == hash(id.p, id.n, TEXT_SALT) && c->fl_stage != 1) {
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
    uint32_t rid = hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k0 = keep_of(c, rid);
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
    nest_open(c, 1, rid, b, used, (o->nav == 0 ? STOP_NAV_OFF : 0) | (placed ? STOP_AT : 0), &og);
    moy_ui_keep_t *k = page_keep(c);
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
    if (armed(c) && c->fl_stage == 1) {
        if (c->sh_in_id == rid) {
            view_in_floor(c, c->sh_r.y + c->sh_r.h);    // its move inside R
        }
        // a floor frame: the rows whose gen moved join the floor
        // the foot's fade, where the rows' height moved under it this frame
        if (!opt[MOY_UI_TXO_MARK] && content != was_b
            && (content > scroll + b.h || was_b > scroll + b.h)) {
            int fh = FADE_H * fs;
            floor_add(c, (rc_t){b.x, b.y + b.h - fh, b.x + b.w, b.y + b.h}, 1);
        }
        if (t == NULL || !moy_ui_text_plan(t, v, s_trows, nrows, err, find)) {
            floor_add(c, rc_of(b), 1);  // no record to say which rows moved: all of them
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
                if (rc_cut(&q, rc_of(b))) {
                    floor_add(c, q, 1);     // (its rows cut at its box: its paper is R's)
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
    s_no_foot = opt[MOY_UI_TXO_MARK] != 0;
    nest_close(c, s, 1);
    s_no_foot = 0;
    return 0;
}

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
        while (e < cells_n && cells[e] != 0) {
            e++;
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
    if (!armed(c) || c->fl_stage == 0) {
        return 1;
    }
    if (!rc_meets(rc_of(b), rc_of(c->sh_r))) {
        return !(c->sh_dlg && c->fl_stage == 1);
    }
    return floor_meets(c, b);
}

// The part open (moy_ui_api_table_part) closes: its ids no longer scoped.
static void part_out(moy_ui_ctx_t *c) {
    c->tp_open = 0;
    s_scope = 0;
}

// The parts of the table drawn last are done: the fades at the edges its
// rows run on past, over them.
static void parts_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
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
        edge_fades(c, s, c->tp_view, c->tp_n * c->tp_rh, c->tp_off, MOY_UI_DEFAULT, 1, c->tp_rh);
    }
    c->tp_id = 0;
}

int moy_ui_api_text_at(const moy_ui_ctx_t *c, moy_ui_span_t id, int px, int py,
                       int32_t out[2]) {
    out[0] = out[1] = 0;
    const moy_ui_tview_t *w = moy_ui_text_view_of(c, hash(id.p, id.n, TEXT_SALT), 0);
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
    uint32_t rid = hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k = keep_of(c, rid);
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
    uint32_t rid = hash(id.p, id.n, TEXT_SALT);
    moy_ui_keep_t *k = keep_of(c, rid);
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
    moy_ui_tview_t *w = moy_ui_text_view_of(c, hash(id.p, id.n, TEXT_SALT), 0);
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
    moy_ui_keep_t *k = page_keep(c);
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
    ground_fill(c, s, (moy_ui_box_t){c->page.x, c->page.y, c->page.w,
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
    o = over(o);
    parts_close(c, s);
    int nc = moy_ui_fields(cols, cols_n);
    if (nc < 1) {
        nc = 1;
    }
    int count = moy_ui_fields(cells, cells_n) / nc;
    int part = o->has_part == 1;
    int rh = part && o->part_h > 0 ? o->part_h : row_h(c);
    // rows side by side (`axis` "x"): rh is a row's width along the axis,
    // ph its height (0: the view's), and the row is the part
    int xa = part && o->axis_x == 1, ph = 0;
    if (xa) {
        rh = o->part_w > 0 ? o->part_w : row_h(c);
        ph = o->part_h;
    }
    int cap_h = label.n ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_CAPTION + (c->cls ? 1 : 0)) * c->fs
                        : 0;
    int named = 0;
    for (size_t i = 0; i < cols_n && !named; i++) {
        named = cols[i] != 0;
    }
    int head_h = named && !xa ? pmetric(c, MOY_UI_M_VALUE_ROW_HEAD) : 0;
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
        uint32_t tid = id_of(o, label);
        fresh = c->focus == tid || c->focus == 0;
        if (fresh) {
            opened_on = page_open_on(c, s, c->y, cap_h + head_h, rh,
                                     cur < count ? cur : count - 1, count);
        }
    }
    moy_ui_box_t b = place(c, cap_h + head_h + (xa ? (ph > 0 ? ph : row_h(c))
                                                   : (count ? count : 1) * rh), &stacked);
    moy_ui_box_t view = {b.x, (int16_t)(b.y + cap_h + head_h), b.w,
                         (int16_t)(b.h - cap_h - head_h > 0 ? b.h - cap_h - head_h : 0)};
    moy_ui_tgeo_t g;
    if (part && (nc == 1 || xa)) {
        memset(&g, 0, sizeof(g));       // the row is the part
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
    uint32_t id = id_of(o, label);
    int disabled = o->disabled == 1;
    moy_ui_keep_t *kp = disabled || !placed ? NULL : keep(c, id, 1);
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
        p->flags = (uint16_t)(STOP_PAGE | (in_dialog(c) ? STOP_MODAL : 0));
        p->page = NO_PAGE;
        p->row = -1;
        p->top = 0;
    }
    int flags = xa ? STOP_KEEP_LR
                   : STOP_KEEP_UD | (row <= 0 ? STOP_AT_TOP : 0)
                         | (row >= count - 1 ? STOP_AT_END : 0) | (placed ? 0 : STOP_ROWS);
    int st = disabled ? 0 : stop(c, id, b, flags, name_of(o, label));
    if (carry && st >= 0 && s_audit_on) {
        // where its rows end is the carried rows' record's, not its own
        moy_ui_span_t nm = name_of(o, label);
        c->cur[c->n_cur - 1].sig = hash(nm.p, nm.n, (uint32_t)(c->cur[c->n_cur - 1].flags
                                                              & ~(STOP_AT_TOP | STOP_AT_END)));
        sig_int(c, st);
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
        rows_note(c, id, cur, view.y);
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
        sig_int(c, cur);
        sig_mix(c, cells, cells_n);
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
                hv[i - vis0] = hash(cells + at, end - at, (uint32_t)nc);
            }
            at = e + 1;
        }
        if (o->has_gen && cur >= vis0 && cur < vis1) {
            // a part table's gen keys its current row: a gen that moved
            // draws that row's part again, its cells unchanged
            hv[cur - vis0] ^= (uint32_t)o->gen * 0x9e3779b1u + 0x7f4a7c15u;
        }
    }
    uint32_t geo = hash((const uint8_t *)&g, sizeof(g), (uint32_t)rh);
    int valid = kept && c->tc_id == id && c->tc_k >= 0 && c->tc_geo == geo
                && same_box(c->tc_view, view) && c->tc_rh == rh && c->tc_n == count;
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
        if (s_audit_on) {
            moy_ui_stop_t *vs = &c->cur[c->n_cur - 1];
            for (int i = c->n_cur - 1; i >= 0; i--) {
                if (c->cur[i].id == id && (c->cur[i].flags & STOP_PAGE)) {
                    vs = &c->cur[i];
                    break;
                }
            }
            vs->sig = carry ? 0 : hash(cells, cells_n, (uint32_t)nc);
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
            int same = carry ? valid && (was_kd & MOY_UI_KD_GEN)
                             : o->has_gen && (was_kd & MOY_UI_KD_GEN) && o->gen == was_gen;
            if (q == NULL || !same || !same_box(view, r) || offset - was_drawn != -c->sh_dy
                || was_b != count * rh || was_rh != rh) {
                c->fl_struct = 1;
                floor_rows(c, r.y, r.y + r.h, 1);
            } else {
                int base = view.y - offset;
                if (!early) {           // else begin's floor, the stops before R drawn through it
                    table_floor(c, base, rh, q->row, (q->st & MOY_UI_ST_FOCUS) != 0,
                                cur, (st & MOY_UI_ST_FOCUS) != 0, g.pic || part,
                                c->page.y + c->page.h);
                }
                for (int i = vis0; carry && i < vis1; i++) {
                    if (TC_CHANGED(i)) {    // a row whose cells changed
                        if (early) {
                            floor_add(c, (rc_t){r.x, base + i * rh, r.x + r.w, base + (i + 1) * rh}, 1);
                        } else {
                            table_strip(c, base + i * rh, base + (i + 1) * rh, base, rh, 1);
                        }
                    }
                }
                if (s_audit_on && !carry) {
                    const moy_ui_stop_t *pv = page_of_keep(c->prev, c->n_prev, kp);
                    const moy_ui_stop_t *vs = page_of_keep(c->cur, c->n_cur, kp);
                    if (pv == NULL || vs == NULL || pv->sig != vs->sig) {
                        s_audit_fail++;     // rows unlike the last frame's under one gen
                    }
                }
            }
        } else if (xa && armed(c) && c->fl_stage == 1 && !c->sh_dlg && !c->fl_mute
                   && rc_meets(rc_of(view), rc_of(c->sh_r))) {
            floor_add(c, rc_of(view), 1);   // inside a page that moved or holds: whole
        } else if (carry && armed(c) && c->fl_stage == 1 && !c->sh_dlg && !c->fl_mute
                   && rc_meets(rc_of(view), rc_of(c->sh_r))) {
            // inside the R of a page that moved or holds: the rows whose
            // cells or marks changed join its floor, over the page's ground
            // the host moved its rows by the table's move (sh_in_dy)
            int inner = c->sh_in_id == id && offset - c->tc_off == -c->sh_in_dy;
            if (!valid || (offset != c->tc_off && !inner)) {
                floor_add(c, rc_of(view), 1);
            } else {
                int base = view.y - offset, reach = mark_room(c);
                rc_t vr = rc_of(view);
                if (inner) {
                    table_in_floor(c, base, rh, -1, 0, -1, 0, c->page.y + c->page.h);
                }
                // a stop drawn in a part whose mark changed: its row
                for (int i = 0; i < c->n_prev; i++) {
                    const moy_ui_stop_t *q = &c->prev[i];
                    int now = c->focus != 0 && q->id == c->focus && !c->elsewhere
                                  ? MOY_UI_ST_FOCUS : 0;
                    rc_t w = {vr.x0, base + q->row * rh - reach, vr.x1,
                              base + (q->row + 1) * rh + reach};
                    if (q->top == INT16_MIN && q->row >= vis0 && q->row < vis1
                        && (q->st & MARKS) != now && rc_cut(&w, vr)) {
                        floor_add(c, w, 1);
                    }
                }
                if (cur != c->tc_cur || mark != c->tc_mark) {
                    for (int w = 0; w < 2; w++) {
                        int i = w ? cur : c->tc_cur;
                        rc_t q = {vr.x0, base + i * rh - reach, vr.x1, base + (i + 1) * rh + reach};
                        if (i >= 0 && rc_cut(&q, vr)) {
                            floor_add(c, q, 1);
                        }
                    }
                }
                for (int i = vis0; i < vis1; i++) {
                    rc_t q = {vr.x0, base + i * rh, vr.x1, base + (i + 1) * rh};
                    if (TC_CHANGED(i) && rc_cut(&q, vr)) {
                        floor_add(c, q, 1);
                    }
                }
            }
            // a row the floor reaches it holds whole, so its part draws whole
            for (int i = vis0; i < vis1; i++) {
                rc_t q = {view.x, view.y - offset + i * rh, view.x + view.w,
                          view.y - offset + (i + 1) * rh};
                if (rc_cut(&q, rc_of(view)) && floor_meets(c, box_of(q))
                    && !floor_holds(c, box_of(q))) {
                    floor_add(c, q, 1);
                }
            }
        }
    }
#undef TC_CHANGED
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
    band_mark(c, b, stacked, 0);
    EACH_ROW(c, k, s, b, stacked, c->page) {
        band(c, s, b, stacked, 0);
        if (cap_h) {
            moy_ui_caption(s, (moy_ui_box_t){b.x, b.y, b.w, (int16_t)cap_h}, label, c->fs, c->cls);
        }
        // in a group, its rows are the group's: each on its card under a rule
        int grp = stacked && c->grp_open;
        moy_ui_box_t gcard = card_of(c);
        if (g.pic && !part) {
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
        for (int i = 0; any && xa && i < count && at <= cells_n; i++) {
            // rows side by side: each cell's marks, the row its part
            size_t e = table_row_end(cells, cells_n, nc, at);
            int rx = view.x + lead + i * rh - offset;
            if (rx + rh > view.x && rx < view.x + view.w) {
                int f = table_flags(c, o, id, i, cur, st, disabled, 0) | MOY_UI_F_PART;
                g.x[0] = (int16_t)rx;
                g.w[0] = (int16_t)rh;
                moy_ui_table_row(s, (moy_ui_box_t){(int16_t)rx, view.y, (int16_t)rh, view.h}, &g,
                                 cells + at, (e < cells_n ? e : cells_n) - at, f, c->fs, c->cls);
            }
            at = e + 1;
        }
        for (int i = 0; any && !xa && i < count && at <= cells_n; i++) {
            size_t e = table_row_end(cells, cells_n, nc, at);
            int ry = view.y + i * rh - offset;
            if (ry + rh > view.y && ry < view.y + view.h && (!table_r || clip_meets(s, ry, rh, reach))) {
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
            // draws them over its parts (parts_close)
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
    if (part && !disabled) {
        // the parts the app draws next (moy_ui_api_table_part)
        rc_t pv = rc_of(view);
        if (stacked) {
            rc_cut(&pv, rc_of(c->page));
        }
        c->tp_id = id;
        c->tp_view = box_of(pv);
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
        c->tp_carry = (uint8_t)(carry && valid && armed(c) && c->fl_stage != 0);
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
        if (!rc_cut(&q, rc_of(c->tp_view)) || !row_draws(c, box_of(q))) {
            continue;
        }
        if (!c->tp_x1 && !sp) {
            q.x0 = x;
            q.x1 = x + w;
            if (!rc_cut(&q, rc_of(c->tp_view))) {
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
        s_scope = hash((const uint8_t *)&i, sizeof(i), c->tp_id);
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
    parts_close(c, s);
    return -1;
}

int32_t moy_ui_api_table_dist(const moy_ui_ctx_t *c) {
    return c->tp_open ? c->tp_dist : 0;
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
        return MOY_UI_API_DEEP;
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
        return MOY_UI_API_DEEP;
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
    parts_close(c, s);
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

void moy_ui_api_shift_break(moy_ui_ctx_t *c) {
    c->fl_on = 0;
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

// Rows [y0, y1) join the `cap` bands at a (pairs y0, y1 in order, an empty
// pair unused): merged with the bands they meet or touch, and where all are
// taken, with the nearest.
static void band_add(int32_t *a, int cap, int32_t y0, int32_t y1) {
    int end = 2 * cap, i = 0;
    while (i < end && a[i + 1] > a[i] && a[i + 1] < y0) {
        i += 2;
    }
    if (i < end && a[i + 1] > a[i] && a[i] <= y1) {
        // it meets the band at i: grow that one, then fold the next ones in
        if (y0 < a[i]) {
            a[i] = y0;
        }
        if (y1 > a[i + 1]) {
            a[i + 1] = y1;
        }
        int j = i + 2;
        while (j < end && a[j + 1] > a[j] && a[j] <= a[i + 1]) {
            if (a[j + 1] > a[i + 1]) {
                a[i + 1] = a[j + 1];
            }
            j += 2;
        }
        int k = i + 2;
        for (; j < end; j += 2, k += 2) {
            a[k] = a[j];
            a[k + 1] = a[j + 1];
        }
        for (; k < end; k += 2) {
            a[k] = a[k + 1] = 0;
        }
        return;
    }
    if (a[end - 1] > a[end - 2]) {
        // every band taken: the rows join the band before or after them
        if (i >= end || (i > 0 && y0 - a[i - 1] <= a[i] - y1)) {
            i -= 2;
        }
        if (y0 < a[i]) {
            a[i] = y0;
        }
        if (y1 > a[i + 1]) {
            a[i + 1] = y1;
        }
        return;
    }
    // a free pair: the bands from i on move down one, and i takes the rows
    for (int k = end - 2; k > i; k -= 2) {
        a[k] = a[k - 2];
        a[k + 1] = a[k - 1];
    }
    a[i] = y0;
    a[i + 1] = y1;
}

void moy_ui_api_lag_note(const moy_ui_ctx_t *c, int32_t *hist, int slots, int bands,
                         int f, int kind) {
    if (slots <= 0 || bands <= 0 || f < 0) {
        return;
    }
    int32_t *h = hist + (f % slots) * (2 + 2 * bands);
    h[0] = f;
    h[1] = kind;
    for (int k = 0; k < 2 * bands; k++) {
        h[2 + k] = 0;
    }
    if (kind == 0 || !c->fl_on || c->fl_stage == 0) {
        return;
    }
    for (int i = 0; i < c->fl_n; i++) {
        if (c->fl[i].h > 0) {
            band_add(h + 2, bands, c->fl[i].y, c->fl[i].y + c->fl[i].h);
        }
    }
}

int moy_ui_api_lag(const int32_t *hist, int slots, int bands, int f, int n,
                   int32_t *out, int cap) {
    if (n > slots || cap <= 0) {
        return -1;
    }
    for (int k = 0; k < 2 * cap; k++) {
        out[k] = 0;
    }
    int stride = 2 + 2 * bands;
    for (int b = 1; b < n; b++) {
        int g = f - b;
        const int32_t *h = hist + (((g % slots) + slots) % slots) * stride;
        if (h[0] != g) {
            continue;                   // the page drew nothing that frame
        }
        if (h[1] != 1) {
            return -1;
        }
        for (int k = 0; k < bands; k++) {
            if (h[3 + 2 * k] > h[2 + 2 * k]) {
                band_add(out, cap, h[2 + 2 * k], h[3 + 2 * k]);
            }
        }
    }
    int m = 0;
    while (m < cap && out[2 * m + 1] > out[2 * m]) {
        m++;
    }
    return m;
}

// -- an app's host (runtime/ui_draw.py's AppHost) ------------------------------------

static int host_slot(const moy_ui_host_t *h, int back) {
    int i = h->ri - back;
    return i < 0 ? i + MOY_UI_HOST_RING : i;
}

// How many paints back the paint is whose pixels the buffer frame f draws
// into holds, `k` buffers presented in turn: k - 1, where the frames between
// were this host's paints, one after another, and that paint left the page
// whole; else -1.
static int host_held(const moy_ui_host_t *h, int f, int k) {
    if (k < 1 || k > h->rn) {
        return -1;
    }
    for (int b = 0; b < k; b++) {
        if (h->f[host_slot(h, b)] != f - 1 - b) {
            return -1;
        }
    }
    return h->whole[host_slot(h, k - 1)] ? k - 1 : -1;
}

// Whether what the console drew over the page after any of the last `n`
// paints meets rect x, y, w, h: a move of it out of those buffers would
// carry it.
static int host_over_meets(const moy_ui_host_t *h, int x, int y, int w, int ht, int n) {
    for (int b = 0; b < n && b < h->rn; b++) {
        int i = host_slot(h, b);
        const int32_t *o = h->over[i];
        if (h->over_on[i] && o[0] < x + w && x < o[0] + o[2] && o[1] < y + ht
            && y < o[1] + o[3]) {
            return 1;
        }
    }
    return 0;
}

// What the console drew over R after the passes whose pixels R keeps (the
// last `n` buffers' slots), where R does not move and the view inside it
// meets none of it: the floor draws it again (sh_over), and 1. 0 where it
// must draw whole.
static int host_over_floor(moy_ui_ctx_t *c, int ox, int oy, int n) {
    if (c->sh_dy || c->sh_dlg) {
        return 0;
    }
    rc_t u = {0, 0, 0, 0};
    int any = 0;
    for (int b = 0; b < n && b < c->host.rn; b++) {
        int i = host_slot(&c->host, b);
        const int32_t *o = c->host.over[i];
        if (!c->host.over_on[i] || o[2] <= 0 || o[3] <= 0) {
            continue;
        }
        rc_t q = {o[0] - ox, o[1] - oy, o[0] - ox + o[2], o[1] - oy + o[3]};
        if (c->sh_in_id && rc_meets(q, rc_of(c->sh_in))) {
            return 0;                   // (a view draws the rows its own record says)
        }
        if (!any) {
            u = q;
            any = 1;
            continue;
        }
        u.x0 = q.x0 < u.x0 ? q.x0 : u.x0;
        u.y0 = q.y0 < u.y0 ? q.y0 : u.y0;
        u.x1 = q.x1 > u.x1 ? q.x1 : u.x1;
        u.y1 = q.y1 > u.y1 ? q.y1 : u.y1;
    }
    if (!any || !rc_cut(&u, rc_of(c->sh_r))) {
        return any ? 1 : 0;
    }
    c->sh_over = (moy_ui_box_t){(int16_t)u.x0, (int16_t)u.y0, (int16_t)(u.x1 - u.x0),
                                (int16_t)(u.y1 - u.y0)};
    if (c->fl_early) {
        floor_add(c, u, 1);             // begin's floor is the pass's: it joins it now
    }
    return 1;
}

// Whether moving region R (plan p) out of the last frame costs the canvas
// less than drawing what of R the floor does not: `cost` per mille of a
// plain row's raster each row it copies costs (-1 unknown: it moves). A
// picture's rows cost more to draw than any copy, so a move that leaves one
// undrawn pays.
static int host_pays(int cost, const int32_t *p) {
    return cost < 0 || p[6] > 0 || p[5] * 1000 + cost * p[3] < 1000 * p[3];
}

static void host_move(int32_t *hp, int x, int y, int w, int h, int dy) {
    int32_t *m = hp + MOY_UI_HP_MOVES + 5 * hp[MOY_UI_HP_N]++;
    m[0] = x;
    m[1] = y;
    m[2] = w;
    m[3] = h;
    m[4] = dy;
}

// The shift frame the frame begun may be (docs/scroll_and_text_2026-10.md
// §3.3): the engine licenses it from the input it applied and the gens the
// last pass drew, the ring vouches that the frame before drew the page
// whole into the buffer the canvas's move reads, and the canvas moves that
// frame's pixels of the page's region R by the scroll's move; the pass then
// draws R's floor alone. Its moves into hp; the answer's MOY_UI_HB_* bits.
static int host_plan(moy_ui_ctx_t *c, const int32_t *pf, int32_t *hp) {
    moy_ui_host_t *h = &c->host;
    int32_t *p = hp + MOY_UI_HP_PLAN;
    int f = pf[MOY_UI_PF_FRAME], key = pf[MOY_UI_PF_KEY], w = pf[MOY_UI_PF_WIN];
    int same = hp[MOY_UI_HP_SAME], hh = hp[MOY_UI_HP_HOST];
    int ox = hp[MOY_UI_HP_OX], oy = hp[MOY_UI_HP_OY], cost = hp[MOY_UI_HP_COST];
    int again = h->again;
    h->again = 0;
    int i = h->ri, ok;
    if (w) {
        // the windowed WM's shown ring vouches for the buffer
        ok = w >= 2 && h->rn && h->whole[i] && (same & 2) && h->key[i] == key;
    } else {
        ok = (pf[MOY_UI_PF_ALONE] || pf[MOY_UI_PF_OVER]) && h->rn
             && hp[MOY_UI_HP_RETAINED] >= 1 && h->f[i] == f - 1 && (same & 3) == 3
             && h->whole[i] && h->key[i] == key;
    }
    // A pass that changed what it drew before the change (`again`) left the
    // last frame's page unlike the app's: this frame draws whole.
    if (!ok || again) {
        return 0;
    }
    // The engine's plan. A canvas whose copy costs about what the raster it
    // saves does draws the frame whole instead. A dialog's rows frame is the
    // host's to allow, and always pays: the frame it saves puts the page
    // under the dialog back too.
    if (moy_ui_api_shift_plan(c, p) == MOY_UI_SHIFT_NONE) {
        return 0;
    }
    int got = MOY_UI_HB_SHIFT;
    if (p[7]) {
        if (!(hh & MOY_UI_HH_HELD)) {
            return 0;
        }
        got |= MOY_UI_HB_HELD;
    } else if (!host_pays(cost, p)) {
        return 0;
    }
    // Where the buffer this frame paints holds the page as the frames
    // RETAINED_FRAMES back left it -- the WM's word (3, a direct render's),
    // or this ring's on a full-screen tier -- a rows frame moves only the
    // band the rows frames since drew.
    int k = hp[MOY_UI_HP_RETAINED] > 0 ? hp[MOY_UI_HP_RETAINED] : 1;
    int lag = -1;
    if (!p[4] && !c->sh_in_dy && (w == 3 || (!w && k > 1 && host_held(h, f, k) == k - 1))) {
        int32_t band[2 * MOY_UI_HOST_BANDS * MOY_UI_HOST_HIST];
        lag = moy_ui_api_lag(h->hist, MOY_UI_HOST_HIST, MOY_UI_HOST_BANDS, f, k, band,
                             MOY_UI_HOST_BANDS * MOY_UI_HOST_HIST);
        for (int b = 0; b < lag; b++) {
            host_move(hp, p[0] + ox, band[2 * b] + oy, p[2], band[2 * b + 1] - band[2 * b], 0);
        }
    }
    if (!w && host_over_meets(h, p[0] + ox, p[1] + oy, p[2], p[3], k)
        && !host_over_floor(c, ox, oy, k)) {
        hp[MOY_UI_HP_N] = 0;
        return 0;
    }
    if (lag < 0) {
        host_move(hp, p[0] + ox, p[1] + oy, p[2], p[3], p[4]);
    }
    h->mv_dy = (int16_t)p[4];
    if (c->sh_in_dy) {
        // the table's move inside R, over R's copy
        host_move(hp, c->sh_in.x + ox, c->sh_in.y + oy, c->sh_in.w, c->sh_in.h, c->sh_in_dy);
        h->mv_dy = c->sh_in_dy;
    }
    // the floor draws over rows the move wrote
    return got | (lag != 0 ? MOY_UI_HB_FENCE : 0);
}

static int rep_dirs(uint32_t rw) {
    return ((rw & 0xFF) ? 1 : 0) | ((rw & 0xFF00) ? 2 : 0) | ((rw & 0xFF0000) ? 4 : 0)
           | ((rw >> 24) ? 8 : 0);
}

int moy_ui_api_host_begin(moy_ui_ctx_t *c, int32_t *in, moy_ui_box_t surface, int flags,
                          int fs, const int32_t *pf, int32_t *hp) {
    moy_ui_host_t *h = &c->host;
    int f = pf[MOY_UI_PF_FRAME];
    if (c->dup != 0 && c->dup != c->dup_told) {
        return MOY_UI_HB_DUP;           // the host logs it (moy_ui_api_dup), then begins
    }
    // the frame begun last, as its pass left it: whether it left the page
    // whole, what was drawn over it after, its kind and its floor's bands
    int clean = 0;
    int drawn = moy_ui_api_shift_drawn(c, &clean);
    if (h->rn) {
        int i = h->ri;
        if (!clean) {
            h->whole[i] = 0;
        }
        h->over_on[i] = h->began1 && pf[MOY_UI_PF_OVER_F] == h->began1 - 1;
        if (h->over_on[i]) {
            memcpy(h->over[i], pf + MOY_UI_PF_OVER_X, sizeof(h->over[i]));
        }
    }
    if (h->began1) {
        moy_ui_api_lag_note(c, h->hist, MOY_UI_HOST_HIST, MOY_UI_HOST_BANDS, h->began1 - 1,
                            h->shifting && (drawn || clean) ? (h->dy ? 2 : 1) : 0);
    }
    in[MOY_UI_IN_DT] = pf[MOY_UI_PF_DT];
    int settled = moy_ui_api_begin(c, in, surface, flags, fs);
    h->shifting = 0;
    h->dy = 0;
    h->mv_dy = 0;
    hp[MOY_UI_HP_N] = 0;
    int got = host_plan(c, pf, hp);
    // this frame's paint
    int i = h->ri + 1 >= MOY_UI_HOST_RING ? 0 : h->ri + 1;
    h->ri = (uint8_t)i;
    h->f[i] = f;
    h->key[i] = pf[MOY_UI_PF_KEY];
    h->whole[i] = pf[MOY_UI_PF_ALONE] || pf[MOY_UI_PF_OVER] || pf[MOY_UI_PF_WIN];
    h->over_on[i] = 0;
    if (h->rn < MOY_UI_HOST_RING) {
        h->rn++;
    }
    h->began1 = f + 1;
    int moving = moy_ui_api_moving(c);
    if (settled) {
        got |= MOY_UI_HB_SETTLED;
    }
    if (moving) {
        got |= MOY_UI_HB_MOVING;
    }
    if ((in[MOY_UI_IN_BTNP] & 4) && c->edge) {
        got |= MOY_UI_HB_EDGE;
    }
    // The directions this frame's steps took, where they moved nothing and
    // the page owes nothing: a held key's further steps that way are no
    // frame, until a frame begins otherwise.
    int d = (in[MOY_UI_IN_BTNP] & 15) | rep_dirs((uint32_t)in[MOY_UI_IN_REP]);
    hp[MOY_UI_HP_DEAD] = d && !(in[MOY_UI_IN_BTNP] & ~15) && !in[MOY_UI_IN_KEY]
                                 && !hp[MOY_UI_HP_TAP] && !in[MOY_UI_IN_WHEEL_X]
                                 && !in[MOY_UI_IN_WHEEL_Y] && settled && !c->moved && !moving
                             ? d : 0;
    in[MOY_UI_IN_BTN] = in[MOY_UI_IN_BTNP] = in[MOY_UI_IN_KEY] = 0;
    in[MOY_UI_IN_WHEEL_X] = in[MOY_UI_IN_WHEEL_Y] = 0;
    return got;
}

int moy_ui_api_host_arm(moy_ui_ctx_t *c) {
    moy_ui_host_t *h = &c->host;
    moy_ui_api_shift_arm(c);
    h->shifting = 1;
    h->dy = h->mv_dy;
    return h->dy;
}

void moy_ui_api_host_whole(moy_ui_ctx_t *c) {
    c->host.again = 1;
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

// The innermost page this frame drew that holds R's page stop, where it
// drew under the gen it drew under the last frame, still, with no picture
// of no gen; NULL otherwise.
static const moy_ui_keep_t *holder_now(const moy_ui_ctx_t *c) {
    int ri = -1;
    for (int i = 0; i < c->n_cur; i++) {
        if (c->cur[i].id == c->sh_id && (c->cur[i].flags & STOP_PAGE)) {
            ri = i;
            break;
        }
    }
    const moy_ui_keep_t *got = NULL;
    int32_t area = 0;
    for (int i = 0; ri >= 0 && i < MOY_UI_API_KEEP; i++) {
        const moy_ui_keep_t *k = &c->keep[i];
        if (k->id == 0 || k->id == c->sh_id || !(k->kd & MOY_UI_KD_PAGE) || k->s0 < 1
            || ri < k->s0 || ri >= k->s0 + k->sn || k->s0 - 1 >= c->n_cur
            || c->cur[k->s0 - 1].id != k->id) {
            continue;
        }
        int32_t a = (int32_t)c->cur[k->s0 - 1].b.w * c->cur[k->s0 - 1].b.h;
        if (got == NULL || a < area) {
            got = k;
            area = a;
        }
    }
    if (got == NULL || (got->kd & (MOY_UI_KD_DRAWN | MOY_UI_KD_HELD | MOY_UI_KD_BAD
                                   | MOY_UI_KD_TABLE))
                           != (MOY_UI_KD_DRAWN | MOY_UI_KD_HELD)) {
        return NULL;
    }
    return got;
}

int moy_ui_api_change_box(const moy_ui_ctx_t *c, int32_t out[8]) {
    if (!c->fl_on || c->fl_stage == 0 || c->sh_plan == MOY_UI_SHIFT_NONE) {
        return 0;
    }
    rc_t u = {0, 0, 0, 0};
    int any = 0;
    for (int i = 0; i <= c->fl_n + 1; i++) {
        rc_t f;
        if (i < c->fl_n) {
            f = rc_of(c->fl[i]);
        } else if (i == c->fl_n && c->sh_dy) {
            f = rc_of(c->sh_r);         // the region moved
        } else if (i == c->fl_n + 1 && c->sh_in_dy) {
            f = rc_of(c->sh_in);        // ...and the view moved inside it
        } else {
            continue;
        }
        if (f.x1 <= f.x0 || f.y1 <= f.y0) {
            continue;
        }
        if (!any) {
            u = f;
            any = 1;
            continue;
        }
        u.x0 = f.x0 < u.x0 ? f.x0 : u.x0;
        u.y0 = f.y0 < u.y0 ? f.y0 : u.y0;
        u.x1 = f.x1 > u.x1 ? f.x1 : u.x1;
        u.y1 = f.y1 > u.y1 ? f.y1 : u.y1;
    }
    out[0] = u.x0;
    out[1] = u.y0;
    out[2] = u.x1;
    out[3] = u.y1;
    // a dialog's rows frame draws through its floor alone; any other draws
    // what lies outside R whole, which the page holding R drew as it did
    // where it held its gen (the app's word that what it shows is the same)
    rc_t r = rc_of(c->sh_dlg ? c->surface : c->sh_r);
    const moy_ui_keep_t *h = c->sh_dlg ? NULL : holder_now(c);
    if (h != NULL) {
        r = rc_of(c->cur[h->s0 - 1].b);
    }
    out[4] = r.x0;
    out[5] = r.y0;
    out[6] = r.x1;
    out[7] = r.y1;
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
