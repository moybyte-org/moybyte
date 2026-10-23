// The toolkit's interaction rules (moy_ui.h): ScrollRegion and DragTap
// as pure functions over state buffers their CALLER owns -- a bytearray in a
// Python object, or a span of a compiled app's memory. The kernel keeps
// nothing between calls: no table, no handle, nothing to end at a VM stop.

#include <math.h>
#include <string.h>

#include "moy_ui.h"

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
