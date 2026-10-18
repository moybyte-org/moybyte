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
    o->steps = flag(word(w, MOY_UI_O_STEPS));
    return MOY_UI_OK;
}

static const moy_ui_over_t *over(const moy_ui_over_t *o) {
    static const moy_ui_over_t none = {MOY_UI_O_NONE, MOY_UI_O_NONE, MOY_UI_O_NONE, {NULL, 0},
                                       -1, -1, -1, -1, -1};
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
    c->surface = surface;
    c->page = surface;
    c->cls = (uint8_t)((flags & MOY_UI_DESK) ? 1 : 0);
    c->win = (uint8_t)((flags & MOY_UI_WINDOW) ? 1 : 0);
    c->app = (uint8_t)((flags >> MOY_UI_APP_SHIFT) & 0xff);
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
    c->grp_open = c->grp_n = c->grp_any = 0;
    c->icon_slot = c->icon_glyph = c->row_slot = c->row_glyph = -1;
    c->n_nest = 0;
    c->grid_open = 0;
    c->fin_on = 0;
    c->dlg_pick = -1;
    c->typed = 0;
    c->pop_prev = c->pop_card;
    c->pop_card.w = 0;
    c->pop_gone = 0;
    c->pop_id = 0;
    int settled = !c->owe;
    c->owe = 0;
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
        if (c->focus == 0 || find(v, n, c->focus) < 0) {
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
                settled = 0;
            }
        }
    } else if (c->modal_from) {
        if (c->focus == 0 || find(v, n, c->focus) < 0) {
            int i = find(v, n, c->modal_from);
            if (i >= 0) {
                c->focus = v[i].id;
                c->focus_box = v[i].b;
                c->focus_name[0] = 0;
                settled = 0;
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
    if (field && key != c->last_typed && key != 0 && key != MOY_UI_KEY_TAB) {
        c->typed = key;             // a typed key, once per press
        c->keys = 1;
        settled = 0;
    }
    c->last_typed = key;
    if (!off) {
        int dir = pressed & (MOY_UI_B_LEFT | MOY_UI_B_RIGHT | MOY_UI_B_UP | MOY_UI_B_DOWN);
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
        if (c->modal && c->pop_prev.w > 0 && !inside(c->pop_prev, x, y)) {
            c->pop_gone = 1;        // a press outside a popover dismisses it
        }
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
            int first = !c->reveal_box.w;
            for (int j = 0; first && j < n; j++) {
                first = j == i || v[j].page != v[i].page || (v[j].flags & STOP_PAGE)
                        || v[j].b.y >= b.y;
            }
            moy_ui_keep_t *pk = first ? keep_of(c, v[v[i].page].id) : NULL;
            if (pk != NULL && pk->a > 0
                && b.y + b.h + pk->a <= v[v[i].page].b.y + v[v[i].page].b.h) {
                pk->a = 0;              // the page's first stop shows the page's top
                settled = 0;
            } else if (reveal(c, &v[v[i].page], b)) {
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

// The parts' `desk` for what lies on the page's ground: the size class and
// whether the page is a window's body.
static int ground_of(const moy_ui_ctx_t *c) {
    return (c->cls ? MOY_UI_DESK : 0) | (c->win ? MOY_UI_WINDOW : 0);
}

static moy_ui_keep_t *page_keep(moy_ui_ctx_t *c) {
    return c->page_keep >= 0 ? &c->keep[c->page_keep] : NULL;
}

// The next call's box: ui.at's, or the next row of the page. *stacked says
// which (a row clips to the page).
static moy_ui_box_t place(moy_ui_ctx_t *c, int h, int *stacked) {
    c->row_slot = c->icon_slot;
    c->row_glyph = c->icon_glyph;
    c->icon_slot = c->icon_glyph = -1;
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
        c->grp_flags = 0;
    }
    c->y = (int16_t)(c->y + h + gap);
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

// -- groups (ui.group) ---------------------------------------------------------------

// The open group's card: its x and width, its top on screen.
static moy_ui_box_t card_of(const moy_ui_ctx_t *c) {
    int in = c->cls ? 0 : kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
    return (moy_ui_box_t){(int16_t)(c->page.x + in),
                          (int16_t)(c->page.y + c->grp_top - c->scroll),
                          (int16_t)(c->page.w - 2 * in), 0};
}

// The card behind stacked row b, drawn under the row's clip; the row's marks
// (MOY_UI_F_FOCUS, MOY_UI_F_ON) are kept for the card's foot.
static void band(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                 int marks) {
    if (!stacked || !c->grp_open) {
        return;
    }
    c->grp_flags = (uint16_t)(marks & (MOY_UI_F_FOCUS | MOY_UI_F_ON));
    moy_ui_group_band(s, card_of(c), b, c->grp_first, c->fs, c->cls);
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
        if (clip_row(&k, s, c->grp_last, 1, c->page)) {
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
    s->flags = (uint16_t)(flags | (dlg ? STOP_MODAL : 0));
    s->page = c->page_stop;
    if (c->modal && !dlg) {
        return 0;                   // under a dialog: drawn, and no input reaches it
    }
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

// A value row (moy_ui.h, the parts) in box b, its marks from the stop's state.
static void vrow_init(const moy_ui_ctx_t *c, moy_ui_vrow_t *r, moy_ui_span_t label) {
    memset(r, 0, sizeof(*r));
    r->label = label;
    r->cur = -1;
    r->fs = c->fs;
    r->desk = c->cls;
    r->card = c->grp_open;
    r->icon_slot = c->row_slot;
    r->icon_glyph = c->row_glyph;
}

static void vrow_draw(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                      int kind, int st, const moy_ui_over_t *o, const moy_ui_vrow_t *r) {
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        int f = mark_flags(st, o) & ~MOY_UI_F_ON;
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

// Make `ct` the page the rows stack in: its scroll kept by `id` (clamped by
// the rows it held last frame) and its stop, which a drag scrolls.
static void open_page(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t ct, int flags) {
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
        p->flags = (uint16_t)(STOP_PAGE | flags);
        p->page = NO_PAGE;
        c->page_stop = c->n_cur++;
    }
}

void moy_ui_api_page(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     const moy_ui_over_t *o, moy_ui_box_t *content) {
    o = over(o);
    grid_end(c, s);
    group_close(c, s);
    c->grp_any = 0;
    moy_ui_box_t b = c->surface;
    if (c->has_at) {
        b = c->at;
        c->has_at = 0;
    }
    moy_ui_box_t ct;
    moy_ui_page_ground(s, b, title, c->fs, ground_of(c), &ct);
    open_page(c, hash(title.p, title.n, PAGE_SALT), ct, o->nav == 0 ? STOP_NAV_OFF : 0);
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
    // a group's row, or a label on a depth's ground: the parts' ink
    if ((stacked && c->grp_open) || (o->ink == MOY_UI_O_NONE && moy_ui_depth()->on)) {
        band(c, s, b, stacked, 0);
        moy_ui_vrow_t r;
        vrow_init(c, &r, text);
        moy_ui_vrow(s, b, MOY_UI_V_LABEL, 0, &r);
        clip_out(&k);
        return;
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
    if (o->disabled != 1 && c->press == id && c->gesture == 1 && (p & MOY_UI_P_LIVE)
        && (p & MOY_UI_P_HELD) && b.w > 0) {
        v = moy_ui_vrow_at(s, b, MOY_UI_V_SLIDER, &r, c->in[MOY_UI_IN_X]);
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
    c->y = (int16_t)(c->y + h);
    moy_ui_keep_t *k = page_keep(c);
    if (k != NULL) {
        k->b = c->y;
    }
    clip_t cl;
    if (clip_row(&cl, s, b, 1, c->page)) {
        if (!c->cls) {
            int in = kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_INSET) * c->fs;
            b.x = (int16_t)(b.x + in);
            b.w = (int16_t)(b.w - 2 * in);
        }
        moy_ui_caption(s, b, caption, c->fs, c->cls);
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
    }
    moy_ui_hints(s, b, items, len, c->fs, ground_of(c));
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
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    moy_ui_vrow_t r;
    vrow_init(c, &r, label);
    r.lo = 0;
    r.hi = hi > 0 ? hi : 1;
    r.v = v < 0 ? 0 : v > r.hi ? r.hi : v;
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
    if (st < 0) {
        st = 0;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        band(c, s, b, stacked, mark_flags(st, o));
        int16_t oc[3];
        colours(s, o, oc);
        moy_ui_well(s, b, mark_flags(st, o), oc[0] == MOY_UI_FROM_STYLE ? -1 : oc[0], c->fs,
                    c->cls);
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

int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]) {
    if (c->pop_id != 0 && c->pop_card.w > 0 && hash(id.p, id.n, POPOVER_SALT) == c->pop_id) {
        out[0] = c->pop_card.x;
        out[1] = c->pop_card.y;
        out[2] = c->pop_card.w;
        out[3] = c->pop_card.h;
        return 1;
    }
    int i = find(c->cur, c->n_cur, hash(id.p, id.n, 0));
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
                      int flags) {
    moy_ui_nest_t *n = &c->nest[c->n_nest++];
    n->page = c->page;
    n->y = c->y;
    n->scroll = c->scroll;
    n->keep = c->page_keep;
    n->stop = c->page_stop;
    n->kind = (uint8_t)kind;
    n->used = (int16_t)used;
    open_page(c, id, b, flags | (kind >= 2 || in_dialog(c) ? STOP_MODAL : 0));
}

// Close the innermost nested page where it is of `kind`: answers the height
// its rows took.
static int nest_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind) {
    if (c->n_nest == 0 || c->nest[c->n_nest - 1].kind != kind) {
        return -1;
    }
    group_close(c, s);
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
    nest_open(c, 1, hash(id.p, id.n, SCROLL_SALT), b, used, o->nav == 0 ? STOP_NAV_OFF : 0);
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
        p->id = id;
        p->b = view;
        p->flags = (uint16_t)(STOP_PAGE | (in_dialog(c) ? STOP_MODAL : 0));
        p->page = NO_PAGE;
    }
    int flags = STOP_KEEP_UD | (row <= 0 ? STOP_AT_TOP : 0) | (row >= count - 1 ? STOP_AT_END : 0);
    int st = disabled ? 0 : stop(c, id, b, flags, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    int offset = kp != NULL ? kp->a : 0;
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
        if (focused && (c->step_y || (st & MOY_UI_ST_ACTIVATED)) && cur >= 0) {
            if (cur * rh < offset) {
                offset = cur * rh;
            } else if ((cur + 1) * rh > offset + view.h) {
                offset = (cur + 1) * rh - view.h;
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
    }
    if (!placed && focused && c->step_y && cur >= 0) {
        c->reveal = 1;
        c->reveal_box = (moy_ui_box_t){b.x, (int16_t)(view.y + cur * rh), b.w, (int16_t)rh};
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        band(c, s, b, stacked, 0);
        if (cap_h) {
            moy_ui_caption(s, (moy_ui_box_t){b.x, b.y, b.w, (int16_t)cap_h}, label, c->fs, c->cls);
        }
        moy_ui_tgeo_t g;
        moy_ui_table_geo(s, b, cols, cols_n, cells, cells_n, c->fs, c->cls, &g);
        if (head_h) {
            moy_ui_table_head(s, (moy_ui_box_t){b.x, (int16_t)(b.y + cap_h), b.w, (int16_t)head_h},
                              &g, cols, cols_n, c->fs, c->cls);
        }
        clip_t kv;
        int any = clip_in(&kv, s, view);
        size_t at = 0;
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
            if (ry + rh > view.y && ry < view.y + view.h) {
                int f = (i == cur ? MOY_UI_F_ON : 0) | (disabled ? MOY_UI_F_DISABLED : 0);
                if ((st & MOY_UI_ST_FOCUS) && o->mark != 0 && i == cur) {
                    f |= MOY_UI_F_FOCUS;
                }
                size_t end = e < cells_n ? e : cells_n;
                moy_ui_table_row(s, (moy_ui_box_t){b.x, (int16_t)ry, b.w, (int16_t)rh}, &g,
                                 cells + at, end - at, f, c->fs, c->cls);
            }
            at = e + 1;
        }
        clip_out(&kv);
        clip_out(&k);
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
    moy_ui_box_t b = place(c, row_h(c), &stacked);
    int disabled = o->disabled == 1;
    int st = disabled ? 0 : stop(c, id_of(o, label), b, STOP_FIELD, name_of(o, label));
    if (st < 0) {
        st = 0;
    }
    int done = 0;
    if ((st & MOY_UI_ST_FOCUS) && c->typed) {
        int k = c->typed;
        c->typed = 0;
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
    if ((st & MOY_UI_ST_ACTIVATED) && c->tap_y == INT16_MIN) {
        done = 1;
    }
    clip_t k;
    if (clip_row(&k, s, b, stacked, c->page)) {
        int f = mark_flags(st, o) & ~MOY_UI_F_ON;
        band(c, s, b, stacked, f);
        moy_ui_span_t t = {buf, (uint16_t)len};
        moy_ui_field(s, b, label, t, f | (disabled ? MOY_UI_F_DISABLED : 0), c->fs, c->cls,
                     c->grp_open);
        clip_out(&k);
    }
    return (int32_t)len | (done ? MOY_UI_FIELD_DONE : 0);
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
    int foot = rh + 2 * pad;
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
    moy_ui_modal(s, card, title, fs, c->cls, rh);
    // the buttons at its foot, right-aligned, recorded left to right
    int nb = moy_ui_fields(buttons, len);
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
    nest_open(c, 2, id, inner, body, 0);
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
        moy_ui_button(s, bb, moy_ui_button_kind(kn, strlen(kn)), mark_flags(st, &none),
                      interact(st), &bt, MOY_UI_PHASE_ALL);
        x += bw[i] + gap;
    }
    c->page_stop = body_stop;
    c->dlg_pick = (int8_t)pick;
    return pick;
}

// -- popovers -------------------------------------------------------------------------

int moy_ui_api_popover(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w,
                       const moy_ui_over_t *o) {
    (void)over(o);
    if (id.n == 0) {
        if (c->n_nest && c->nest[c->n_nest - 1].kind == 3) {
            int used = c->nest[c->n_nest - 1].used;
            int took = nest_close(c, s, 3);
            if (took != used) {
                c->owe = 1;             // laid out for another body: the next frame redraws
            }
        }
        return 0;
    }
    if (c->n_nest >= MOY_UI_API_NEST) {
        return 0;
    }
    group_close(c, s);
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
    int bh = body;
    if (2 * pad + bh > sf.h - 2 * pad) {
        bh = sf.h - 4 * pad;
        if (bh < rh) {
            bh = rh;
        }
    }
    int h = 2 * pad + bh;
    int gap = 4 * fs;
    int x = a.x;
    if (x + w > sf.x + sf.w) {
        x = sf.x + sf.w - w;
    }
    if (x < sf.x) {
        x = sf.x;
    }
    int y = a.y + a.h + gap;
    if (c->pop_mid) {
        x = sf.x + moy_ui_fdiv(sf.w - w, 2);
        y = sf.y + moy_ui_fdiv(sf.h - h, 2);
    } else if (y + h > sf.y + sf.h) {
        y = a.y - gap - h;          // no room below: above it
        if (y < sf.y) {
            y = sf.y + sf.h - h;    // nor above: as low as it fits
            if (y < sf.y) {
                y = sf.y;
            }
        }
    }
    moy_ui_box_t card = {(int16_t)x, (int16_t)y, (int16_t)w, (int16_t)h};
    static const uint8_t none[1] = {0};
    moy_ui_modal(s, card, (moy_ui_span_t){none, 0}, fs, c->cls, rh);
    int lp = c->cls ? kmetric(MOY_UI_K_GROUP, MOY_UI_M_GROUP_PAD) * fs
                    : kmetric(MOY_UI_K_ROW, MOY_UI_M_ROW_PAD) * fs;
    moy_ui_box_t inner = {(int16_t)(card.x + pad - lp), (int16_t)(card.y + pad),
                          (int16_t)(w - 2 * (pad - lp)), (int16_t)bh};
    nest_open(c, 3, pid, inner, body, 0);
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
        clip_t k;
        if (clip_row(&k, s, b, stacked, c->page)) {
            moy_ui_caption(s, b, label, fs, c->cls);
            clip_out(&k);
        }
    } else if (!placed && c->y > 0) {
        int stacked;
        moy_ui_box_t b = place(c, rule_h(c), &stacked);
        clip_t k;
        if (clip_row(&k, s, b, stacked, c->page)) {
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
        moy_ui_vrow_t r;
        vrow_init(c, &r, t);
        vrow_draw(c, s, b, stacked, MOY_UI_V_INFO, st, o, &r);
    }
    return pick;
}

// -- card grids ----------------------------------------------------------------------

// The card drawn last: its picture finished over what the app drew in it.
static void card_finish(moy_ui_ctx_t *c, const moy_ui_sink_t *s) {
    if (!c->fin_on) {
        return;
    }
    c->fin_on = 0;
    clip_t k;
    if (clip_row(&k, s, c->fin_content, c->grid_open == 1, c->page)) {
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
        c->y = (int16_t)(c->y + pmetric(c, MOY_UI_M_VALUE_ROW_GRID_TOP));
        c->grid_y = c->y;
    }
    int cols = (area.w - 2 * pad + gap) / (w + gap);
    cols = cols < 1 ? 1 : cols > 255 ? 255 : cols;
    int span = cols * w + (cols - 1) * gap;
    c->grid_cols = (uint8_t)cols;
    c->grid_i = 0;
    c->grid_x = (int16_t)(area.x + (area.w - span) / 2);
    c->grid_pitch = (int16_t)(w + gap);
    c->grid_w = (int16_t)w;
    c->grid_h = (int16_t)h;
    c->grid_row = (int16_t)(pmetric(c, MOY_UI_M_VALUE_ROW_CARD_TOP) + h
                            + pmetric(c, MOY_UI_M_VALUE_ROW_CARD_FOOT)
                            + pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP));
}

void moy_ui_api_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t sub, const moy_ui_over_t *o, int32_t out[5]) {
    o = over(o);
    card_finish(c, s);
    memset(out, 0, 5 * sizeof(int32_t));
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
    int st = 0;
    if (o->disabled != 1) {
        st = stop(c, id_of(o, title), b, 0, name_of(o, title));
        st = st < 0 ? 0 : st;
    }
    int f = mark_flags(st, o);
    clip_t k;
    moy_ui_box_t content = pic;
    uint16_t g = 0;
    if (!clip_row(&k, s, b, stacked, c->page)) {
        out[4] = st;
        return;                     // scrolled out of view: nothing to draw into
    }
    moy_ui_card(s, b, pic, title, sub, f, c->fs, ground_of(c), &content, &g);
    clip_out(&k);
    c->fin_on = 1;
    c->fin_flags = (uint8_t)(f & MOY_UI_F_FOCUS ? 1 : 0);
    c->fin_ground = g;
    c->fin_pic = pic;
    c->fin_content = content;
    out[0] = content.x;
    out[1] = content.y;
    out[2] = content.w;
    out[3] = content.h;
    out[4] = st;
}

int moy_ui_api_owes(const moy_ui_ctx_t *c) {
    return c->owe;
}
