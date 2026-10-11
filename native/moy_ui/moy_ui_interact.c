// The toolkit's interaction rules (moy_ui.h): Hits, ScrollRegion and DragTap
// as pure functions over state buffers their CALLER owns -- a bytearray in a
// Python object, or a span of a compiled app's memory. The kernel keeps
// nothing between calls: no table, no handle, nothing to end at a VM stop.

#include <math.h>
#include <string.h>

#include "moy_ui.h"

// -- Hits -------------------------------------------------------------------------------

void moy_ui_hits_clear(int32_t *b) {
    b[MOY_UI_HITS_N] = 0;
}

int moy_ui_hits_add(int32_t *b, size_t words, int32_t x, int32_t y, int32_t w, int32_t h,
                    int32_t id) {
    int32_t n = b[MOY_UI_HITS_N];
    size_t cap = (words - MOY_UI_HITS_HDR) / 5;
    if (n < 0 || (size_t)n >= cap) {
        b[MOY_UI_HITS_FULL]++;
        return MOY_UI_FULL;
    }
    int32_t *r = b + MOY_UI_HITS_HDR + (size_t)n * 5;
    r[0] = x;
    r[1] = y;
    r[2] = w;
    r[3] = h;
    r[4] = id;
    b[MOY_UI_HITS_N] = n + 1;
    return MOY_UI_OK;
}

int32_t moy_ui_hits_at(const int32_t *b, int32_t px, int32_t py) {
    for (int32_t i = b[MOY_UI_HITS_N] - 1; i >= 0; i--) {    // drawn later = on top
        const int32_t *r = b + MOY_UI_HITS_HDR + (size_t)i * 5;
        if (r[0] <= px && px < r[0] + r[2] && r[1] <= py && py < r[1] + r[3]) {
            return r[4];
        }
    }
    return -1;
}

// One pointer sample. `p` carries the previous sample's held state and the
// press edge's target (armed) in, and the new hover, pressed and armed out.
void moy_ui_hits_pump(const int32_t *b, int32_t px, int32_t py, int down, int pointing,
                      moy_ui_pump_t *p) {
    int32_t hit = moy_ui_hits_at(b, px, py);
    if (down) {
        if (!p->down) {
            p->armed = hit;                     // the press EDGE picks the target
        }
        p->pressed = (p->armed >= 0 && hit == p->armed) ? p->armed : -1;
        p->hover = -1;                          // a pointer that is down never hovers
    } else {
        p->armed = -1;
        p->pressed = -1;
        p->hover = pointing ? hit : -1;
    }
    p->down = down ? 1 : 0;
}

// -- ScrollRegion -------------------------------------------------------------------------

static int32_t clamp(int32_t off, int32_t content, int32_t extent) {
    int32_t m = content - extent > 0 ? content - extent : 0;
    return off < 0 ? 0 : (off > m ? m : off);
}

void moy_ui_scroll_stop(moy_ui_scroll_t *s) {
    s->flags &= ~MOY_UI_SCROLL_FLING;
    s->vel = 0.0f;
}

void moy_ui_scroll_drag_start(moy_ui_scroll_t *s, int32_t p) {
    s->drag = p;
    s->flags |= MOY_UI_SCROLL_DRAG;
    moy_ui_scroll_stop(s);                  // a new touch CATCHES a live fling
}

int moy_ui_scroll_drag_move(moy_ui_scroll_t *s, int32_t p, float dt_ms, int32_t *offset,
                            int32_t content, int32_t extent) {
    if (!(s->flags & MOY_UI_SCROLL_DRAG)) {
        return 0;
    }
    int32_t d = s->drag - p;
    *offset = clamp(*offset + d, content, extent);
    s->drag = p;
    // The release velocity: an EMA over the samples, fed the loop's dt; a
    // held-still finger decays it to ~0, so hold-then-release is a stop.
    if (dt_ms != 0.0f) {
        s->vel += ((float)d / dt_ms - s->vel) * MOY_UI_VEL_EMA;
    }
    return 1;
}

void moy_ui_scroll_drag_end(moy_ui_scroll_t *s, int32_t offset, int32_t content,
                            int32_t extent) {
    s->flags &= ~MOY_UI_SCROLL_DRAG;
    if (fabsf(s->vel) >= MOY_UI_MIN_FLING && content - extent > 0) {
        s->flags |= MOY_UI_SCROLL_FLING;
        s->foff = (float)offset;
    }
}

int moy_ui_scroll_tick(moy_ui_scroll_t *s, float dt_ms, int32_t *offset, int32_t content,
                       int32_t extent) {
    if (!(s->flags & MOY_UI_SCROLL_FLING) || dt_ms <= 0.0f) {
        return 0;
    }
    s->foff += s->vel * dt_ms;
    s->vel *= powf(MOY_UI_FRICTION, dt_ms);
    int32_t m = content - extent > 0 ? content - extent : 0;
    if (s->foff <= 0.0f) {
        s->foff = 0.0f;
        moy_ui_scroll_stop(s);
    } else if (s->foff >= (float)m) {
        s->foff = (float)m;
        moy_ui_scroll_stop(s);
    } else if (fabsf(s->vel) < MOY_UI_STOP_VEL) {
        moy_ui_scroll_stop(s);
    }
    *offset = (int32_t)(s->foff + 0.5f);
    return 1;
}

// -- DragTap ---------------------------------------------------------------------------------

// One pointer sample over a region: 1 with the press point in tx/ty on a
// clean tap release, else 0. While a drag is live the offset follows the
// finger.
int moy_ui_dragtap_frame(moy_ui_dragtap_t *t, moy_ui_scroll_t *s, const int32_t view[4],
                         int horizontal, int32_t px, int32_t py, int click, int down,
                         int32_t slop, float dt_ms, int32_t *offset, int32_t content,
                         int32_t *tx, int32_t *ty) {
    int32_t axis = horizontal ? px : py;
    int32_t extent = horizontal ? view[2] : view[3];
    if (click) {
        if (view[0] <= px && px < view[0] + view[2] && view[1] <= py
                && py < view[1] + view[3]) {
            t->px = px;
            t->py = py;
            t->flags = MOY_UI_TAP_PRESS
                       | ((s->flags & MOY_UI_SCROLL_FLING) ? MOY_UI_TAP_CAUGHT : 0);
            moy_ui_scroll_drag_start(s, axis);
        }
        return 0;
    }
    if (down) {
        if ((t->flags & MOY_UI_TAP_PRESS) && !(t->flags & MOY_UI_TAP_DRAGGING)) {
            int32_t ax = px - t->px, ay = py - t->py;
            if ((ax < 0 ? -ax : ax) > slop || (ay < 0 ? -ay : ay) > slop) {
                t->flags |= MOY_UI_TAP_DRAGGING;
            }
        }
        if (t->flags & MOY_UI_TAP_DRAGGING) {
            moy_ui_scroll_drag_move(s, axis, dt_ms, offset, content, extent);
        }
        return 0;
    }
    moy_ui_scroll_drag_end(s, *offset, content, extent);
    int f = t->flags;
    t->flags = 0;
    if (!(f & MOY_UI_TAP_PRESS) || (f & (MOY_UI_TAP_DRAGGING | MOY_UI_TAP_CAUGHT))) {
        return 0;
    }
    *tx = t->px;
    *ty = t->py;
    return 1;
}
