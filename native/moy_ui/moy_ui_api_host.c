// The public UI API's shift frames and an app's host (moy_ui_api.h): the
// plan a shift frame draws, and the begin an app host makes in one call.

#include "moy_ui_api_int.h"

// -- shift frames (docs/history/scroll_and_text_2026-10.md §3.3) ---------------------------

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

// -- an app's host (runtime/toolkit/ui_draw.py's AppHost) ------------------------------------

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
        if (c->sh_in_id && mua_rc_meets(q, mua_rc_of(c->sh_in))) {
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
    if (!any || !mua_rc_cut(&u, mua_rc_of(c->sh_r))) {
        return any ? 1 : 0;
    }
    c->sh_over = (moy_ui_box_t){(int16_t)u.x0, (int16_t)u.y0, (int16_t)(u.x1 - u.x0),
                                (int16_t)(u.y1 - u.y0)};
    if (c->fl_early) {
        mua_floor_add(c, u, 1);             // begin's floor is the pass's: it joins it now
    }
    return 1;
}

// Whether moving region R (plan p) out of the last frame costs the canvas
// less than drawing what of R the floor does not: `cost` per mille of a
// plain row's raster each row it copies costs (-1 unknown: it moves). A
// picture's rows cost more to draw than any copy, so a move that leaves one
// undrawn pays. A rows frame moves nothing (at most the bands the rows
// frames since drew, which a whole frame draws again too): it pays.
static int host_pays(int cost, const int32_t *p, int moves) {
    return cost < 0 || !moves || p[6] > 0 || p[5] * 1000 + cost * p[3] < 1000 * p[3];
}

// Whether moving the text view inside a still R (sh_in, by sh_in_dy) costs
// the canvas less than drawing every row of the view: the move copies the
// view's rows and leaves the band it uncovers and the two fades over the
// view to draw. -1 unknown: it moves.
static int view_pays(const moy_ui_ctx_t *c, int cost) {
    int dy = c->sh_in_dy < 0 ? -c->sh_in_dy : c->sh_in_dy, h = c->sh_in.h;
    int left = dy + 2 * FADE_H * c->fs;
    return cost < 0 || left * 1000 + cost * h < 1000 * h;
}

static void host_move(int32_t *hp, int x, int y, int w, int h, int dy) {
    int32_t *m = hp + MOY_UI_HP_MOVES + 5 * hp[MOY_UI_HP_N]++;
    m[0] = x;
    m[1] = y;
    m[2] = w;
    m[3] = h;
    m[4] = dy;
}

// The copy of (x0, y0)-(x1, y1) unmoved, where it holds any pixel.
static void host_move_rect(int32_t *hp, int ox, int oy, int x0, int y0, int x1, int y1) {
    if (x1 > x0 && y1 > y0) {
        host_move(hp, x0 + ox, y0 + oy, x1 - x0, y1 - y0, 0);
    }
}

// The shift frame the frame begun may be (docs/history/scroll_and_text_2026-10.md
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
    if (c->sh_in_dy && c->sh_in_tv && !p[4] && !view_pays(c, cost)) {
        // R holds still and the text view draws its rows in place of its move
        c->sh_view = c->sh_in_id;
        c->sh_in_id = 0;
        c->sh_in_dy = 0;
        c->sh_plan = MOY_UI_SHIFT_ROWS;
        moy_ui_api_shift_plan(c, p);
    }
    int got = MOY_UI_HB_SHIFT;
    if (p[7]) {
        if (!(hh & MOY_UI_HH_HELD)) {
            return 0;
        }
        got |= MOY_UI_HB_HELD;
    } else if (!host_pays(cost, p, p[4] || c->sh_in_dy)) {
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
    if (lag < 0 && c->sh_in_dy && !p[4]) {
        // R holds still around the view that moves inside it: what of R
        // lies outside the view comes over from the last frame's buffer,
        // the view's move bringing the rest
        rc_t r = {p[0], p[1], p[0] + p[2], p[1] + p[3]};
        rc_t v = mua_rc_of(c->sh_in);
        if (!mua_rc_cut(&v, r)) {
            v.x0 = v.x1 = r.x0;
            v.y0 = v.y1 = r.y0;
        }
        host_move_rect(hp, ox, oy, r.x0, r.y0, r.x1, v.y0);
        host_move_rect(hp, ox, oy, r.x0, v.y1, r.x1, r.y1);
        host_move_rect(hp, ox, oy, r.x0, v.y0, v.x0, v.y1);
        host_move_rect(hp, ox, oy, v.x1, v.y0, r.x1, v.y1);
    } else if (lag < 0) {
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
    mua_kept_begin(c, f, hp[MOY_UI_HP_RETAINED]);
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
    rc_t u = mua_rc_of(c->fl[0]);
    for (int i = 1; i < c->fl_n; i++) {
        rc_t f = mua_rc_of(c->fl[i]);
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
            f = mua_rc_of(c->fl[i]);
        } else if (i == c->fl_n && c->sh_dy) {
            f = mua_rc_of(c->sh_r);         // the region moved
        } else if (i == c->fl_n + 1 && c->sh_in_dy) {
            f = mua_rc_of(c->sh_in);        // ...and the view moved inside it
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
    rc_t r = mua_rc_of(c->sh_dlg ? c->surface : c->sh_r);
    const moy_ui_keep_t *h = c->sh_dlg ? NULL : holder_now(c);
    if (h != NULL) {
        r = mua_rc_of(c->cur[h->s0 - 1].b);
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
        if (!mua_rc_meets(q, mua_rc_of(c->sh_r))) {
            return 1;
        }
        rc_t got = {q.x0, q.y0, q.x0, q.y0};
        for (int i = 0; i < c->fl_n; i++) {
            rc_t f = q;
            if (mua_rc_cut(&f, mua_rc_of(c->fl[i]))) {
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
    if (f.w <= 0 || !mua_rc_cut(&q, mua_rc_of(f))) {
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
    rc_t b = mua_rc_of(pg);
    const moy_ui_keep_t *k = c->page_keep >= 0 ? &c->keep[c->page_keep] : NULL;
    if (mua_armed(c) && c->fl_stage == 1 && k != NULL && k->id == c->sh_id && !c->fl_struct) {
        rc_t u = {0, 0, 0, 0};
        for (int i = 0; i < c->fl_n; i++) {
            rc_t f = mua_rc_of(c->fl[i]);
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
    uint32_t sid = mua_hash(id.p, id.n, 0);
    int i = mua_find(c->prev, c->n_prev, sid);
    moy_ui_box_t b;
    int stacked;
    if (c->grid_open) {
        // a card of the open grid, where the next card would lie
        mua_card_finish(c, s);
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
        int pad = mua_pmetric(c, MOY_UI_M_VALUE_ROW_CARD_PAD);
        stacked = c->grid_open == 1;
        int y = stacked ? c->page.y + c->grid_y - c->scroll : c->grid_y;
        b = (moy_ui_box_t){(int16_t)(c->grid_x + col * c->grid_pitch - pad), (int16_t)y,
                           (int16_t)(c->grid_w + 2 * pad),
                           (int16_t)(c->grid_row - mua_pmetric(c, MOY_UI_M_VALUE_ROW_GRID_GAP))};
        c->at_last = (uint8_t)!stacked;
    } else {
        b = mua_place(c, h > 0 ? h : mua_row_h(c), &stacked);
        if (i >= 0) {
            b.x = c->prev[i].b.x;
            b.w = c->prev[i].b.w;
        }
    }
    uint16_t flags = i >= 0 ? (uint16_t)(c->prev[i].flags
                                          & (STOP_KEEP_LR | STOP_KEEP_UD | STOP_AT_TOP
                                             | STOP_AT_END | STOP_HOLD | STOP_FIELD))
                            : 0;
    int st = mua_stop(c, sid, b, flags, id);
    if (st < 0) {
        return 0;
    }
    if (i >= 0) {
        moy_ui_stop_t *q = &c->cur[c->n_cur - 1];
        q->flags |= c->prev[i].flags & (STOP_PIC | STOP_GEN | STOP_GRID);
        q->gen = c->prev[i].gen;
        if (mua_audit_on) {
            q->sig = c->prev[i].sig;
        }
    }
    return st;
}

uint32_t moy_ui_api_audit(int on) {
    uint32_t n = mua_audit_fail;
    mua_audit_fail = 0;
    if (on >= 0) {
        mua_audit_on = (uint32_t)(on != 0);
    }
    return n;
}
