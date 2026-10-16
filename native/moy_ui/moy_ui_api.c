// The public UI API's engine (moy_ui_api.h; docs/ui_api_2026-10.md §2).

#include <string.h>

#include "moy_ui_api.h"
#include "moy_canvas.h"
#include "moy_theme.h"

#define MAGIC 0x6d756931u           // "mui1"
#define PAGE_SALT 0x9e3779b9u
#define NO_PAGE 0xffu

// A stop's flags.
enum {
    STOP_PAGE = 1,          // a page's content rect: never focused, what a drag scrolls
    STOP_KEEP_LR = 2,       // a slider: left and right are its own
    STOP_KEEP_UD = 4,       // a list: up and down are its own...
    STOP_AT_TOP = 8,        // ...but up at its first row
    STOP_AT_END = 16,       // ...and down at its last
    STOP_HOLD = 32,         // nav = false on a part: focus stays, the keys are the cart's
    STOP_NAV_OFF = 64,      // nav = false on a page: the engine moves no focus in it
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
    o->current = o->disabled = o->nav = o->mark = -1;
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
    if (idn >= 0) {
        if ((size_t)idn > n - 4u * MOY_UI_O_WORDS || idn > 0xffff) {
            return MOY_UI_BAD;
        }
        o->id.p = w + 4u * MOY_UI_O_WORDS;
        o->id.n = (uint16_t)idn;
    }
    o->current = flag(word(w, MOY_UI_O_CURRENT));
    o->disabled = flag(word(w, MOY_UI_O_DISABLED));
    o->nav = flag(word(w, MOY_UI_O_NAV));
    o->mark = flag(word(w, MOY_UI_O_MARK));
    return MOY_UI_OK;
}

static const moy_ui_over_t *over(const moy_ui_over_t *o) {
    static const moy_ui_over_t none = {MOY_UI_O_NONE, MOY_UI_O_NONE, MOY_UI_O_NONE, {NULL, 0},
                                       -1, -1, -1, -1};
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
    free_->seen = 1;
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
static int reveal(moy_ui_ctx_t *c, const moy_ui_stop_t *p, moy_ui_box_t b) {
    moy_ui_keep_t *k = keep_of(c, p->id);
    if (k == NULL) {
        return 0;
    }
    int32_t was = k->a;
    if (b.y < p->b.y) {
        k->a -= p->b.y - b.y;
    } else if (b.y + b.h > p->b.y + p->b.h) {
        k->a += (b.y + b.h) - (p->b.y + p->b.h);
    }
    if (k->a < 0) {
        k->a = 0;
    }
    return k->a != was;
}

// -- a frame -------------------------------------------------------------------------

int moy_ui_api_begin(moy_ui_ctx_t *c, const int32_t *in, moy_ui_box_t surface, int cls, int fs) {
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
    c->surface = surface;
    c->page = surface;
    c->cls = (uint8_t)(cls ? 1 : 0);
    c->fs = (uint8_t)(fs > 0 ? fs : 1);
    c->y = 0;
    c->scroll = 0;
    c->has_at = 0;
    c->page_keep = -1;
    c->page_stop = NO_PAGE;
    c->act = 0;
    c->keys = 0;
    c->step_x = c->step_y = 0;
    c->dup = 0;
    int settled = 1;
    uint32_t focus0 = c->focus;
    const moy_ui_stop_t *v = c->prev;
    int n = c->n_prev;

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
    c->last_key = key;
    const moy_ui_stop_t *f = fi >= 0 ? &v[fi] : NULL;
    int off = f == NULL || (f->flags & STOP_HOLD)
              || (f->page != NO_PAGE && f->page < n && (v[f->page].flags & STOP_NAV_OFF));
    if (!off) {
        int dir = pressed & (MOY_UI_B_LEFT | MOY_UI_B_RIGHT | MOY_UI_B_UP | MOY_UI_B_DOWN);
        int go = pressed & (MOY_UI_B_A | MOY_UI_B_RUN);
        if (dir || go || tab) {
            c->keys = 1;
        }
        int to = -1;
        if (go) {
            c->act = c->focus;
            c->tap_x = c->tap_y = INT16_MIN;
        } else if (tab) {
            to = step_order(v, n, fi, 0);
        } else if ((dir & (MOY_UI_B_LEFT | MOY_UI_B_RIGHT)) && (f->flags & STOP_KEEP_LR)) {
            c->step_x = (int8_t)((dir & MOY_UI_B_LEFT) ? -1 : 1);
        } else if ((dir & MOY_UI_B_UP) && (f->flags & STOP_KEEP_UD) && !(f->flags & STOP_AT_TOP)) {
            c->step_y = -1;
        } else if ((dir & MOY_UI_B_DOWN) && (f->flags & STOP_KEEP_UD)
                   && !(f->flags & STOP_AT_END)) {
            c->step_y = 1;
        } else if (dir) {
            int one = (dir & MOY_UI_B_UP) ? MOY_UI_B_UP : (dir & MOY_UI_B_DOWN) ? MOY_UI_B_DOWN
                      : (dir & MOY_UI_B_LEFT) ? MOY_UI_B_LEFT : MOY_UI_B_RIGHT;
            to = nearest(v, n, f, one);
        }
        if (to >= 0) {
            c->focus = v[to].id;
            c->focus_box = v[to].b;
            c->focus_name[0] = 0;
            c->reveal = 1;
        }
        if (c->act || c->step_x || c->step_y) {
            settled = 0;
        }
    }

    // The pointer: a press, a drag that scrolls its page, a release that taps.
    int p = in[MOY_UI_IN_PTR];
    int live = p & MOY_UI_P_LIVE, held = live && (p & MOY_UI_P_HELD);
    int x = in[MOY_UI_IN_X], y = in[MOY_UI_IN_Y];
    int slop = 4 * c->fs;
    if (live && (p & MOY_UI_P_EDGE)) {
        int h = stop_at(v, n, x, y);
        c->press = h >= 0 ? v[h].id : 0;
        c->press_x = (int16_t)x;
        c->press_y = c->drag_y = (int16_t)y;
        c->gesture = 1;
        c->drag_page = 0;
        settled = 0;
    } else if (held && was_held && c->gesture) {
        if (c->gesture == 1) {
            int h = c->press ? find(v, n, c->press) : -1;
            int slider = h >= 0 && (v[h].flags & STOP_KEEP_LR);
            int dy = y - c->press_y;
            if (slider) {
                settled = 0;
            } else if (dy > slop || dy < -slop) {
                int pg = page_at(v, n, c->press_x, c->press_y);
                c->drag_page = pg >= 0 ? v[pg].id : 0;
                c->press = 0;
                c->gesture = 2;
            }
        }
        if (c->gesture == 2 && c->drag_page && y != c->drag_y) {
            moy_ui_keep_t *k = keep_of(c, c->drag_page);
            if (k != NULL) {
                k->a -= y - c->drag_y;
                if (k->a < 0) {
                    k->a = 0;
                }
            }
            c->drag_y = (int16_t)y;
            settled = 0;
        }
    } else if (!held && c->gesture) {
        if (c->gesture == 1 && c->press && live) {
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
    if (live && (x != c->last_x || y != c->last_y)) {
        settled = 0;                // a hover moved
    }
    if ((p & MOY_UI_P_LIVE) != (c->last_ptr & MOY_UI_P_LIVE)) {
        settled = 0;
    }
    c->last_x = (int16_t)x;
    c->last_y = (int16_t)y;
    c->last_ptr = p;

    // Keep the focused stop (or the list row it moved to) in view.
    if (c->reveal && c->focus) {
        int i = find(v, n, c->focus);
        if (i >= 0 && v[i].page != NO_PAGE && v[i].page < n) {
            moy_ui_box_t b = c->reveal_box.w ? c->reveal_box : v[i].b;
            if (reveal(c, &v[v[i].page], b)) {
                settled = 0;
            }
        }
    }
    c->reveal = 0;
    c->reveal_box.w = 0;
    if (c->focus != focus0) {
        settled = 0;
    }
    return settled;
}

// -- layout --------------------------------------------------------------------------

static moy_ui_keep_t *page_keep(moy_ui_ctx_t *c) {
    return c->page_keep >= 0 ? &c->keep[c->page_keep] : NULL;
}

// The next call's box: ui.at's, or the next row of the page. *stacked says
// which (a row clips to the page).
static moy_ui_box_t place(moy_ui_ctx_t *c, int h, int *stacked) {
    if (c->has_at) {
        c->has_at = 0;
        *stacked = 0;
        return c->at;
    }
    *stacked = 1;
    moy_ui_box_t b = {c->page.x, (int16_t)(c->page.y + c->y - c->scroll), c->page.w, (int16_t)h};
    c->y = (int16_t)(c->y + h + metric(MOY_UI_M_ROW_STACK_GAP) * c->fs);
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    return b;
}

// What a stacked row draws inside: the page's content rect.
typedef struct {
    moy_canvas_row_t *row;
    int32_t saved[4];
    int on;
} clip_t;

static int clip_in(clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b) {
    moy_canvas_row_t *row = (moy_canvas_row_t *)s->dst;
    int32_t *st = row->st;
    k->row = row;
    k->on = 1;
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

// Clip a box's draw to `in` when it stacks; answers whether any of it shows.
static int clip_row(clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                    moy_ui_box_t in) {
    k->on = 0;
    if (!stacked) {
        return 1;
    }
    if (b.y + b.h <= in.y || b.y >= in.y + in.h) {
        return 0;
    }
    return clip_in(k, s, in);
}

// A stop recorded for the next frame's navigation: its state bits, or -1
// where it is no stop (a duplicate id, or a full frame).
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
    moy_ui_stop_t *s = &c->cur[c->n_cur++];
    s->id = id;
    s->b = b;
    s->flags = (uint8_t)flags;
    s->page = c->page_stop;
    if (c->focus == 0) {
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
    return st;
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

// -- the verbs -----------------------------------------------------------------------

void moy_ui_api_page(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     const moy_ui_over_t *o, moy_ui_box_t *content) {
    o = over(o);
    moy_ui_box_t b = c->surface;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    }
    moy_ui_box_t ct;
    moy_ui_panel(s, b, title, c->fs, &ct);
    uint32_t id = hash(title.p, title.n, PAGE_SALT);
    moy_ui_keep_t *k = keep(c, id, 1);
    c->page = ct;
    c->y = 0;
    c->scroll = 0;
    c->page_keep = -1;
    if (k != NULL) {
        int32_t most = k->b - ct.h;
        if (k->a > most) {
            k->a = most;
        }
        if (k->a < 0) {
            k->a = 0;
        }
        c->scroll = (int16_t)k->a;
        k->b = 0;
        c->page_keep = (int8_t)(k - c->keep);
    }
    c->page_stop = NO_PAGE;
    if (c->n_cur < MOY_UI_API_STOPS) {
        moy_ui_stop_t *p = &c->cur[c->n_cur];
        p->id = id;
        p->b = ct;
        p->flags = (uint8_t)(STOP_PAGE | (o->nav == 0 ? STOP_NAV_OFF : 0));
        p->page = NO_PAGE;
        c->page_stop = c->n_cur++;
    }
    if (content != NULL) {
        *content = ct;
    }
}

void moy_ui_api_label(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      const moy_ui_over_t *o) {
    o = over(o);
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    clip_t k;
    if (!clip_row(&k, s, b, stacked, c->page)) {
        return;
    }
    int16_t pc[3];
    moy_ui_colors(s->look, MOY_UI_K_PANEL, MOY_UI_REST, pc);
    int16_t ink = colour(s->look, o->ink);
    int pad = kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * c->fs;
    moy_ui_box_t t = {(int16_t)(b.x + pad), b.y, (int16_t)(b.w - 2 * pad), b.h};
    moy_ui_label(s, t, text, ink == MOY_UI_FROM_STYLE ? pc[1] : ink, MOY_TEXT_LEFT);
    clip_out(&k);
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
    if (clip_row(&k, s, b, stacked, c->page)) {
        moy_ui_button_t bt;
        bt.label = label;
        bt.glyph = -1;
        bt.fs = c->fs;
        colours(s, o, bt.colors);
        bt.app = -1;
        moy_ui_button(s, b, kid, mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0),
                      interact(st), &bt, MOY_UI_PHASE_ALL);
        clip_out(&k);
    }
    return (st & MOY_UI_ST_ACTIVATED) != 0;
}

int moy_ui_api_toggle(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int on,
                      const moy_ui_over_t *o) {
    o = over(o);
    int stacked;
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : stop(c, id_of(o, label), b, 0, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    on = on ? 1 : 0;
    if (st & MOY_UI_ST_ACTIVATED) {
        on = !on;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        int16_t col[3];
        colours(s, o, col);
        moy_ui_row_t r;
        row_init(&r, label, c->fs, col);
        int f = mark_flags(st, o) | (on ? MOY_UI_F_ON : 0) | (disabled ? MOY_UI_F_DISABLED : 0);
        moy_ui_row(s, b, MOY_UI_K_ROW, f, interact(st), &r, MOY_UI_PHASE_ALL);
        clip_out(&k);
    }
    return on;
}

static int put_int(char *buf, int32_t v) {
    char t[12];
    int n = 0, neg = v < 0;
    uint32_t u = neg ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
    do {
        t[n++] = (char)('0' + u % 10u);
        u /= 10u;
    } while (u);
    int m = 0;
    if (neg) {
        buf[m++] = '-';
    }
    while (n) {
        buf[m++] = t[--n];
    }
    return m;
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
    int disabled = o->disabled == 1;
    uint32_t id = id_of(o, label);
    int st = disabled ? 0 : stop(c, id, b, STOP_KEEP_LR, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    if ((st & MOY_UI_ST_FOCUS) && c->step_x) {
        v += c->step_x;
    }
    int p = c->in[MOY_UI_IN_PTR];
    if (!disabled && c->press == id && c->gesture == 1 && (p & MOY_UI_P_LIVE)
        && (p & MOY_UI_P_HELD) && b.w > 0) {
        int32_t dx = c->in[MOY_UI_IN_X] - b.x;
        if (dx < 0) {
            dx = 0;
        }
        if (dx > b.w) {
            dx = b.w;
        }
        v = lo + (int32_t)(((int64_t)dx * (hi - lo) + b.w / 2) / b.w);
    }
    if (v < lo) {
        v = lo;
    }
    if (v > hi) {
        v = hi;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        int16_t col[3];
        colours(s, o, col);
        moy_ui_row_t r;
        row_init(&r, label, c->fs, col);
        char num[12];
        r.value.p = (const uint8_t *)num;
        r.value.n = (uint16_t)put_int(num, v);
        moy_ui_row(s, b, MOY_UI_K_ROW, mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0),
                   interact(st), &r, MOY_UI_PHASE_ALL);
        clip_out(&k);
    }
    return v;
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
    int flags = STOP_KEEP_UD | (row == 0 ? STOP_AT_TOP : 0) | (row >= count - 1 ? STOP_AT_END : 0);
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
    if (focused && c->step_y) {
        c->reveal = 1;
        c->reveal_box = rb;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
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
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        moy_ui_cell_t cell;
        memset(&cell, 0, sizeof(cell));
        cell.glyph = -1;
        cell.pad = cell.caption_h = cell.text_dx = cell.text_dy = MOY_UI_DEFAULT;
        cell.band_fill = cell.band_ink = MOY_UI_FROM_STYLE;
        cell.fs = c->fs;
        colours(s, o, cell.colors);
        moy_ui_cell(s, b, MOY_UI_K_CELL, mark_flags(st, o) | (disabled ? MOY_UI_F_DISABLED : 0),
                    interact(st), &cell, MOY_UI_PHASE_ALL, NULL);
        clip_out(&k);
    }
    out[0] = b.x;
    out[1] = b.y;
    out[2] = b.w;
    out[3] = b.h;
    out[4] = st;
}

int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap) {
    if (id.p != NULL) {
        uint32_t h = hash(id.p, id.n, 0);
        if (h != c->focus) {
            c->focus = h;
            c->reveal = 1;
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

int moy_ui_api_keys_taken(const moy_ui_ctx_t *c) {
    return c->keys;
}

void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h) {
    c->at.x = (int16_t)x;
    c->at.y = (int16_t)y;
    c->at.w = (int16_t)(w > 0 ? w : 0);
    c->at.h = (int16_t)(h > 0 ? h : 0);
    c->has_at = 1;
}

const char *moy_ui_api_dup(moy_ui_ctx_t *c) {
    if (c->dup == 0 || c->dup == c->dup_told) {
        return NULL;
    }
    c->dup_told = c->dup;
    return c->dup_name;
}
