// The public UI API's kept ring and kept faces (moy_ui_api.h): a kept
// table's record per paint, and the faces a table's parts keep drawn.

#include "moy_ui_api_int.h"

// -- a kept table (o->keep) and the parts `held` names ------------------------------

// The engine's kept memory, one block taken at the first context that keeps
// (PSRAM where the board has it: a static would hold it in internal SRAM
// from boot, and a context's bytes would carry it whether it keeps or not):
// a ring for each of the KEPT_OWNERS contexts that kept last, the one
// begun least recently given up for another, and the faces' one record.
#define KEPT_OWNERS 4

typedef struct {
    kept_ring_t ring[KEPT_OWNERS];
    uint32_t used;
    moy_ui_faces_t fc;
} kept_mem_t;

static kept_mem_t *s_kept;

static kept_mem_t *kept_mem(void) {
    if (s_kept == NULL) {
        s_kept = (kept_mem_t *)KEPT_ALLOC(sizeof(kept_mem_t));
        if (s_kept != NULL) {
            memset(s_kept, 0, sizeof(*s_kept));
        }
    }
    return s_kept;
}

// The ring the engine keeps for context c, else NULL; with `make` one taken
// for it where it has none, holding the paint begun (kt_f) alone.
kept_ring_t *mua_ring_of(const moy_ui_ctx_t *c, int make) {
    kept_mem_t *m = make ? kept_mem() : s_kept;
    if (m == NULL) {
        return NULL;
    }
    kept_ring_t *old = &m->ring[0];
    for (int i = 0; i < KEPT_OWNERS; i++) {
        kept_ring_t *r = &m->ring[i];
        if (r->owner == c) {
            return r;
        }
        if (r->owner == NULL || (old->owner != NULL && r->used < old->used)) {
            old = r;
        }
    }
    if (!make) {
        return NULL;
    }
    memset(old, 0, sizeof(*old));
    old->owner = c;
    old->used = ++m->used;
    old->n = 1;
    old->kt[0].f = c->kt_f;
    old->kt[0].kept = 1;
    return old;
}

// A context given up (its bytes reset): its ring goes back.
void mua_kept_drop(const moy_ui_ctx_t *c) {
    kept_ring_t *r = mua_ring_of(c, 0);
    if (r != NULL) {
        memset(r, 0, sizeof(*r));
    }
}

static int kept_slot(const kept_ring_t *r, int back) {
    int i = r->ri - back;
    return i < 0 ? i + MOY_UI_KEPT_RING : i;
}

// The paint begun for frame f, `k` buffers presented in turn (0: none kept):
// a slot of the context's ring where it has one, the one begun before it
// again where f is its frame (a pass begun twice in one painted frame).
void mua_kept_begin(moy_ui_ctx_t *c, int32_t f, int k) {
    c->kt_f = f;
    c->kt_k = (uint8_t)(k < 0 ? 0 : k >= MOY_UI_KEPT_RING ? MOY_UI_KEPT_RING - 1 : k);
    kept_ring_t *r = mua_ring_of(c, 0);
    if (r == NULL) {
        return;
    }
    r->used = ++s_kept->used;
    if (!r->n || r->kt[r->ri].f != f) {
        r->ri = (uint8_t)(r->ri + 1 >= MOY_UI_KEPT_RING ? 0 : r->ri + 1);
        if (r->n < MOY_UI_KEPT_RING) {
            r->n++;
        }
    }
    moy_ui_kept_t *p = &r->kt[r->ri];
    p->f = f;
    p->kept = 1;
    p->has_tbl = 0;
    p->nh = 0;
    p->over.w = 0;
}

// The paint whose pixels the buffer this frame paints holds: kt_k back,
// where the paints between were this context's, one a frame, and that one
// the app's alone; else NULL.
static const moy_ui_kept_t *kept_held(const moy_ui_ctx_t *c, const kept_ring_t *r) {
    int k = c->kt_k;
    if (k < 1 || r->n <= k) {
        return NULL;
    }
    for (int b = 1; b <= k; b++) {
        if (r->kt[kept_slot(r, b)].f != c->kt_f - b) {
            return NULL;
        }
    }
    const moy_ui_kept_t *p = &r->kt[kept_slot(r, k)];
    return p->kept ? p : NULL;
}

// Whether paints a and b left their kept tables alike.
static int kept_same(const moy_ui_kept_t *a, const moy_ui_kept_t *b) {
    if (!a->has_tbl || !b->has_tbl || a->id != b->id || a->key != b->key || a->geo != b->geo
        || !mua_same_box(a->view, b->view) || a->off != b->off || a->rh != b->rh || a->n != b->n
        || a->cur != b->cur || a->at != b->at || a->mark != b->mark || a->r0 != b->r0
        || a->k != b->k || a->np != b->np || a->k < 0) {
        return 0;
    }
    return memcmp(a->h, b->h, (size_t)a->k * a->np * sizeof(a->h[0])) == 0;
}

int moy_ui_api_held(moy_ui_ctx_t *c, moy_ui_span_t id, int32_t key) {
    kept_ring_t *r = c->kt_k < 1 ? NULL : mua_ring_of(c, 1);
    if (r == NULL) {
        return 0;
    }
    uint32_t h = mua_hash(id.p, id.n, 0x6b657074u);
    moy_ui_kept_t *now = &r->kt[r->ri];
    int i = 0;
    while (i < now->nh && now->hid[i] != h) {
        i++;
    }
    if (i < MOY_UI_KEPT_HELD) {
        now->hid[i] = h;
        now->hkey[i] = key;
        if (i == now->nh) {
            now->nh++;
        }
    }
    const moy_ui_kept_t *was = kept_held(c, r);
    if (was == NULL) {
        return 0;
    }
    for (int j = 0; j < was->nh; j++) {
        if (was->hid[j] == h) {
            return was->hkey[j] == key;
        }
    }
    return 0;
}

// The kept table's floor, rects f[0..n) (x0, y0, x1, y1 each), merged into
// their bounding rect where one row's reach -- rh along the axis (y, or x
// where `xa`), sp past it on either side, across the whole view -- could
// meet two: a row is answered once, inside the one rect it meets. More
// than the floor holds become one.
static int kept_merge(rc_t *f, int n, int rh, int sp, int xa) {
    int merged = 1;
    while (merged) {
        merged = 0;
        for (int i = 0; i < n && !merged; i++) {
            for (int j = i + 1; j < n; j++) {
                int a0 = xa ? f[i].x0 : f[i].y0, a1 = xa ? f[i].x1 : f[i].y1;
                int b0 = xa ? f[j].x0 : f[j].y0, b1 = xa ? f[j].x1 : f[j].y1;
                int gap = a0 > b1 ? a0 - b1 : b0 > a1 ? b0 - a1 : 0;
                if (gap < rh + 2 * sp) {
                    f[i].x0 = f[j].x0 < f[i].x0 ? f[j].x0 : f[i].x0;
                    f[i].y0 = f[j].y0 < f[i].y0 ? f[j].y0 : f[i].y0;
                    f[i].x1 = f[j].x1 > f[i].x1 ? f[j].x1 : f[i].x1;
                    f[i].y1 = f[j].y1 > f[i].y1 ? f[j].y1 : f[i].y1;
                    f[j] = f[--n];
                    merged = 1;
                    break;
                }
            }
        }
    }
    while (n > MOY_UI_KEPT_FLOOR) {
        rc_t q = f[--n];
        f[0].x0 = q.x0 < f[0].x0 ? q.x0 : f[0].x0;
        f[0].y0 = q.y0 < f[0].y0 ? q.y0 : f[0].y0;
        f[0].x1 = q.x1 > f[0].x1 ? q.x1 : f[0].x1;
        f[0].y1 = q.y1 > f[0].y1 ? q.y1 : f[0].y1;
    }
    return n;
}

// Which of the floor's rects f[0..n) the buffer the last frame painted
// holds as this frame draws them (kf_copy, a bit each): where that paint
// is the frame before this one's, the app's alone, of this table under
// its key, geometry and scroll, and not the paint this frame's buffer
// holds (the canvas presents two buffers or more), a rect every part of
// whose reach meets it -- its cells hashed (hv, rows vis0 .. vis0 + nk -
// 1), whether it is the current part and that part's marks -- shows as
// it did there, and nothing that paint drew over the table meets.
static void kept_copy(moy_ui_ctx_t *c, const kept_ring_t *r, const moy_ui_kept_t *now,
                      const rc_t *f, int n, const uint32_t *hv, int vis0, int nk, int np,
                      int base, int rh, int sp, int xa, int c0, int pitch) {
    if (c->kt_k < 2 || r->n < 2) {
        return;
    }
    const moy_ui_kept_t *p = &r->kt[kept_slot(r, 1)];
    if (p->f != c->kt_f - 1 || !p->kept || !p->has_tbl || p->id != now->id
        || p->key != now->key || p->geo != now->geo || !mua_same_box(p->view, now->view)
        || p->off != now->off || p->rh != now->rh || p->n != now->n || p->np != np
        || p->k < 0) {
        return;
    }
    rc_t vr = mua_rc_of(now->view), ov = mua_rc_of(p->over);
    for (int q = 0; q < n; q++) {
        if (p->over.w > 0 && mua_rc_meets(f[q], ov)) {
            continue;
        }
        int same = 1;
        for (int i = vis0; same && i < vis0 + nk; i++) {
            int a0 = base + i * rh - sp, a1 = base + (i + 1) * rh + sp;
            int pr = i - p->r0;
            for (int j = 0; j < np; j++) {
                int x0 = xa ? vr.y0 : vr.x0, x1 = xa ? vr.y1 : vr.x1;
                if (np > 1) {
                    x0 = c0 + j * pitch - sp;
                    x1 = c0 + (j + 1) * pitch + sp;
                }
                rc_t e = xa ? (rc_t){a0, x0, a1, x1} : (rc_t){x0, a0, x1, a1};
                if (!mua_rc_meets(e, f[q])) {
                    continue;
                }
                int on = i == now->cur && j == now->at, was = i == p->cur && j == p->at;
                if (pr < 0 || pr >= p->k || p->h[pr * np + j] != hv[(i - vis0) * np + j]
                    || on != was || (on && p->mark != now->mark)) {
                    same = 0;
                    break;
                }
            }
        }
        if (same) {
            c->kf_copy |= (uint8_t)(1u << q);
        }
    }
}

// A placed table of parts that keeps them (o->keep): this frame's paint of
// it recorded in the context's ring r, and the floor this frame draws planned from the paint the
// buffer holds (moy_ui_api_table_floor). `hv` the record of rows vis0 ..
// vis0 + nk - 1, `np` parts a row (nk -1: too many to keep); `base` row
// 0's start along the axis.
void mua_kept_table(moy_ui_ctx_t *c, kept_ring_t *r, const moy_ui_over_t *o, uint32_t id,
                    uint32_t geo, moy_ui_box_t view, int32_t offset, int rh, int count,
                    int cur, int at, int mark, int vis0, int nk, int np, const uint32_t *hv,
                    int base, int sp, int xa) {
    moy_ui_kept_t *now = &r->kt[r->ri];
    now->has_tbl = 1;
    now->id = id;
    now->key = o->keep;
    now->geo = geo;
    now->view = view;
    now->off = offset;
    now->rh = (int16_t)rh;
    now->n = (int16_t)count;
    now->cur = (int16_t)cur;
    now->at = (int8_t)at;
    now->mark = (uint8_t)mark;
    now->r0 = (int16_t)vis0;
    now->k = (int16_t)nk;
    now->np = (uint8_t)np;
    if (nk > 0) {
        memcpy(now->h, hv, (size_t)nk * np * sizeof(hv[0]));
    }
    now->over = (moy_ui_box_t){o->over_x, o->over_y, o->over_w, o->over_h};
    if (o->keep < 0) {
        now->kept = 0;
    }
    const moy_ui_kept_t *was = kept_held(c, r);
    rc_t vr = mua_rc_of(view), f[16];
    int n = 0, whole = o->whole == 1 || was == NULL || !now->kept || nk < 0 || !was->has_tbl
                       || was->id != id || was->key != o->keep || was->geo != geo
                       || !mua_same_box(was->view, view) || was->off != offset || was->rh != rh
                       || was->n != count || was->k < 0 || was->np != np;
    // the parts across a row: from `across0` on the cross axis, a pitch apart
    int c0 = (xa ? view.y : view.x) + o->across_first, pitch = o->across_pitch;
    // The buffer lacks the table (at this scroll): where the frame before
    // this one painted it, the app's alone and kept under this key and
    // geometry, and the app's ground is the same along the axis
    // (o->shift), the view is moved out of that buffer by the scrolls'
    // difference (moy_ui_api_table_shift) and the floor is what the move
    // leaves to draw: the band it uncovers, the parts that changed since
    // that paint or did not show in it, what was drawn over it (moved with
    // it) and the fade at the view's end, whose blended pixels moved.
    const moy_ui_kept_t *from = NULL;
    int d = 0;
    c->kf_sh = 0;
    if (whole && o->whole != 1 && o->shift && now->kept && nk >= 0 && r->n >= 2 && c->kt_k >= 1) {
        from = &r->kt[kept_slot(r, 1)];
        d = from->off - offset;
        int v0 = xa ? vr.x0 : vr.y0, v1 = xa ? vr.x1 : vr.y1;
        if (from->f != c->kt_f - 1 || !from->kept || !from->has_tbl || from->id != id
            || from->key != o->keep || from->geo != geo || !mua_same_box(from->view, view)
            || from->rh != rh || from->n != count || from->np != np || from->k < 0
            || (d < 0 ? -d : d) >= v1 - v0) {
            from = NULL;
        }
    }
    const moy_ui_kept_t *ref = from != NULL ? from : was;
    int moved = (from != NULL || !whole)
                && (cur != ref->cur || at != ref->at || mark != ref->mark);
    if (from != NULL) {
        whole = 0;
        int v0 = xa ? vr.x0 : vr.y0, v1 = xa ? vr.x1 : vr.y1;
        int x0 = xa ? vr.y0 : vr.x0, x1 = xa ? vr.y1 : vr.x1;
        if (d > 0) {
            f[n++] = xa ? (rc_t){v0, x0, v0 + d, x1} : (rc_t){x0, v0, x1, v0 + d};
        } else if (d < 0) {
            f[n++] = xa ? (rc_t){v1 + d, x0, v1, x1} : (rc_t){x0, v1 + d, x1, v1};
        }
    }
    for (int i = vis0; !whole && i < vis0 + nk; i++) {
        int r = i - ref->r0;
        for (int j = 0; j < np; j++) {
            int mk = moved && ((i == cur && j == at) || (i == ref->cur && j == ref->at));
            if (!mk && r >= 0 && r < ref->k && ref->h[r * np + j] == hv[(i - vis0) * np + j]) {
                continue;
            }
            if (n == 16) {
                whole = 1;
                break;
            }
            int a0 = base + i * rh - sp, a1 = base + (i + 1) * rh + sp;
            int x0 = xa ? vr.y0 : vr.x0, x1 = xa ? vr.y1 : vr.x1;
            if (np > 1) {
                x0 = c0 + j * pitch - sp;
                x1 = c0 + (j + 1) * pitch + sp;
            }
            f[n] = xa ? (rc_t){a0, x0, a1, x1} : (rc_t){x0, a0, x1, a1};
            n += mua_rc_cut(&f[n], vr);
        }
    }
    if (!whole && ref->over.w > 0 && n < 16) {
        // what was drawn over the paint the buffer holds (moved with it,
        // out of the last frame's): the floor takes it
        f[n] = mua_rc_of(ref->over);
        if (xa) {
            f[n].x0 += d;
            f[n].x1 += d;
        } else {
            f[n].y0 += d;
            f[n].y1 += d;
        }
        n += mua_rc_cut(&f[n], vr);
    }
    if (!whole && from != NULL && o->fade > 0 && n < 16) {
        int v1 = xa ? vr.x1 : vr.y1, h = o->fade + (d < 0 ? -d : d);
        f[n] = xa ? (rc_t){v1 - h, vr.y0, v1, vr.y1} : (rc_t){vr.x0, v1 - h, vr.x1, v1};
        n += mua_rc_cut(&f[n], vr);
    }
    if (whole) {
        f[0] = vr;
        n = vr.x1 > vr.x0 && vr.y1 > vr.y0;
    } else if (from != NULL) {
        c->kf_sh = 1;
        c->kf_shift = d;
        c->kf_sv = view;
    }
    n = kept_merge(f, n, rh, sp, xa);
    c->kf_i = 0;
    c->kf_n = (uint8_t)n;
    for (int i = 0; i < n; i++) {
        c->kf[i] = mua_box_of(f[i]);
    }
    c->kf_copy = c->kf_took = c->kf_ci = 0;
    if (o->whole != 1 && now->kept && nk > 0 && from == NULL) {
        kept_copy(c, r, now, f, n, hv, vis0, nk, np, base, rh, sp, xa, c0, pitch);
    }
    // every buffer holds it where the paints before this one, a buffer
    // each, left it as this one does, with nothing over it
    // (one drawn over this paint: the frames after, which draw it again,
    // bring no buffer to the table alone)
    int held = now->kept && r->n >= c->kt_k, over = now->over.w > 0;
    for (int b = 1; held && b < c->kt_k; b++) {
        const moy_ui_kept_t *p = &r->kt[kept_slot(r, b)];
        held = p->f == c->kt_f - b && p->kept && p->over.w <= 0 && kept_same(p, now);
    }
    c->kf_held = (uint8_t)(over ? MOY_UI_KF_OVER : held ? MOY_UI_KF_HELD : 0);
    if (!held && !over) {
        c->owe = 1;                     // the frames that bring each buffer to it
    }
}

// Whether every buffer holds the parts `held` named this frame as this
// frame names them: the paints before this one, a buffer each, named each
// under its key.
static int kept_parts(const moy_ui_ctx_t *c) {
    const kept_ring_t *r = mua_ring_of(c, 0);
    const moy_ui_kept_t *now = r != NULL ? &r->kt[r->ri] : NULL;
    if (now == NULL || !now->nh) {
        return 1;                       // (nothing named)
    }
    if (!now->kept || r->n < c->kt_k) {
        return 0;
    }
    for (int b = 1; b < c->kt_k; b++) {
        const moy_ui_kept_t *p = &r->kt[kept_slot(r, b)];
        if (p->f != c->kt_f - b || !p->kept) {
            return 0;
        }
        for (int j = 0; j < now->nh; j++) {
            int q = 0;
            while (q < p->nh && p->hid[q] != now->hid[j]) {
                q++;
            }
            if (q == p->nh || p->hkey[q] != now->hkey[j]) {
                return 0;
            }
        }
    }
    return 1;
}

int moy_ui_api_held_last(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t key) {
    const kept_ring_t *r = c->kt_k < 1 ? NULL : mua_ring_of(c, 0);
    if (r == NULL || r->n < 2) {
        return 0;
    }
    const moy_ui_kept_t *p = &r->kt[kept_slot(r, 1)];
    if (p->f != c->kt_f - 1 || !p->kept) {
        return 0;
    }
    uint32_t h = mua_hash(id.p, id.n, 0x6b657074u);
    for (int j = 0; j < p->nh; j++) {
        if (p->hid[j] == h) {
            return p->hkey[j] == key;
        }
    }
    return 0;
}

int moy_ui_api_held_all(const moy_ui_ctx_t *c) {
    return c->kt_k > 0 && kept_parts(c);
}

int32_t moy_ui_api_table_copy(moy_ui_ctx_t *c, int32_t out[4], int back) {
    if (back) {
        if (c->kf_ci > 0) {
            c->kf_took &= (uint8_t)~(1u << (c->kf_ci - 1));
        }
        return 0;
    }
    while (c->kf_ci < c->kf_n && !(c->kf_copy & (1u << c->kf_ci))) {
        c->kf_ci++;
    }
    if (c->kf_ci >= c->kf_n) {
        return 0;
    }
    moy_ui_box_t b = c->kf[c->kf_ci];
    c->kf_took |= (uint8_t)(1u << c->kf_ci++);
    out[0] = b.x;
    out[1] = b.y;
    out[2] = b.w;
    out[3] = b.h;
    return 1;
}

int32_t moy_ui_api_table_shift(moy_ui_ctx_t *c, int32_t out[5], int back) {
    if (back) {
        if (c->kf_sh == 2) {
            // the canvas could not move it: the view draws whole
            c->kf[0] = c->kf_sv;
            c->kf_n = 1;
            c->kf_i = c->kf_copy = c->kf_took = c->kf_ci = c->kf_sh = 0;
        }
        return 0;
    }
    if (c->kf_sh != 1) {
        return 0;
    }
    c->kf_sh = 2;
    out[0] = c->kf_sv.x;
    out[1] = c->kf_sv.y;
    out[2] = c->kf_sv.w;
    out[3] = c->kf_sv.h;
    out[4] = c->kf_shift;
    return 1;
}

int32_t moy_ui_api_table_floor(moy_ui_ctx_t *c, int32_t out[4]) {
    while (c->kf_i < c->kf_n && (c->kf_took & (1u << c->kf_i))) {
        c->kf_i++;
    }
    if (c->kf_i >= c->kf_n) {
        if (c->kf_held == MOY_UI_KF_HELD && c->kt_k > 0 && !kept_parts(c)) {
            c->kf_held = 0;
            c->owe = 1;                 // a part beside it lags in a buffer
        }
        return c->kf_held == MOY_UI_KF_HELD ? 0 : c->kf_held == MOY_UI_KF_OVER ? -2 : -1;
    }
    moy_ui_box_t b = c->kf[c->kf_i++];
    out[0] = b.x;
    out[1] = b.y;
    out[2] = b.w;
    out[3] = b.h;
    return 1;
}

// -- the faces a table's parts keep drawn (moy_ui_api_slots) -------------------------

// The engine keeps one context's faces at a time, in its kept memory: the
// ticket it gave last (s_fc_owner) names that context.
static uint32_t s_fc_owner;
static uint32_t s_fc_ids;

static int faces_mine(const moy_ui_ctx_t *c) {
    return c->fc_id != 0 && s_fc_owner == c->fc_id && s_kept != NULL && s_kept->fc.cap > 0;
}

int32_t moy_ui_api_slots(moy_ui_ctx_t *c, int32_t cap, int32_t key) {
    cap = cap < 0 ? 0 : cap > MOY_UI_FACE_SLOTS ? MOY_UI_FACE_SLOTS : cap;
    kept_mem_t *m = cap > 0 ? kept_mem() : s_kept;
    if (m == NULL) {
        return 0;
    }
    moy_ui_faces_t *fc = &m->fc;
    if (c->fc_id == 0 || s_fc_owner != c->fc_id) {
        if (++s_fc_ids == 0) {
            s_fc_ids = 1;
        }
        c->fc_id = s_fc_ids;
        s_fc_owner = c->fc_id;
        fc->cap = 0;
    }
    if (fc->cap != cap || fc->key != key) {
        fc->cap = (uint16_t)cap;
        fc->key = key;
        fc->tick = 0;
        memset(fc->used, 0, sizeof(fc->used));
    }
    fc->w = fc->wn = 0;
    fc->np = c->tp_np ? c->tp_np : 1;
    fc->rows = c->tp_n;
    fc->r0 = c->tp_v0;
    fc->r1 = c->tp_id ? c->tp_e : c->tp_v0;
    return cap;
}

int32_t moy_ui_api_slot(moy_ui_ctx_t *c, moy_ui_span_t name, int32_t gen, int mint) {
    if (!faces_mine(c)) {
        return -1;
    }
    moy_ui_faces_t *fc = &s_kept->fc;
    uint32_t key = mua_hash(name.p, name.n, 0x66616365u) ^ ((uint32_t)gen * 0x9e3779b1u);
    if (++fc->tick == 0) {             // (the uses wrapped: every face forgotten)
        memset(fc->used, 0, sizeof(fc->used));
        fc->tick = 1;
    }
    int lru = 0;
    for (int j = 0; j < fc->cap; j++) {
        if (fc->used[j] && fc->pk[j] == key) {
            fc->used[j] = fc->tick;
            return j;
        }
        if (fc->used[j] < fc->used[lru]) {
            lru = j;
        }
    }
    if (!mint) {
        return -1;
    }
    fc->pk[lru] = key;
    fc->used[lru] = fc->tick;
    return lru | MOY_UI_SLOT_MINT;
}

int32_t moy_ui_api_slot_next(moy_ui_ctx_t *c) {
    if (!faces_mine(c)) {
        return -1;
    }
    moy_ui_faces_t *fc = &s_kept->fc;
    int np = fc->np, shown = (fc->r1 - fc->r0) * np, n = fc->rows * np;
    for (;;) {
        int w = fc->w, p = -1;
        if (fc->wn >= fc->cap) {
            w = -1;                      // (as many as the slots: the round is over)
        } else if (w < shown) {
            p = fc->r0 * np + w;
        } else {
            // the rows past either end, nearest first: past the end, then
            // before the start (its parts from its last), each a row
            // further out than the last
            int q = w - shown, d = q / (2 * np), j = q % np;
            if (fc->r1 + d >= fc->rows && fc->r0 - 1 - d < 0) {
                w = -1;                  // (no row further out on either side)
            } else if ((q / np) % 2) {
                p = fc->r0 - 1 - d >= 0 ? (fc->r0 - d) * np - 1 - j : -1;
            } else {
                p = fc->r1 + d < fc->rows ? (fc->r1 + d) * np + j : -1;
            }
        }
        if (w < 0) {
            fc->w = fc->wn = 0;      // (the next asks from the first again)
            return -1;
        }
        fc->w++;
        if (p >= 0 && p < n) {
            fc->wn++;
            return p;
        }
    }
}
