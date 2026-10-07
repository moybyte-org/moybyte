// The DSI compositor (moy_present.h): the Waveshare P4's frame state machine
// over moy_dsi's scan buffers and the PPA's async composite.

#include <string.h>

#include "moy_present.h"

void moy_dsi_init(moy_dsi_t *d, const moy_dsi_ops_t *ops, int nfbs) {
    if (nfbs < 1) {
        nfbs = 1;
    }
    if (nfbs > MOY_PRESENT_MAX_FBS) {
        nfbs = MOY_PRESENT_MAX_FBS;
    }
    memset(d, 0, sizeof(*d));
    d->ops = ops;
    d->nfbs = (uint8_t)nfbs;
    d->pending = -1;
    // Two or more: scan buffer 0, draw into 1.
    if (nfbs > 1) {
        ops->show(ops->ctx, 0);
        d->back = 1;
    } else {
        ops->msync(ops->ctx);
    }
}

static bool busy(const moy_dsi_t *d, int fb) {
    for (int i = 0; i < d->nbusy; i++) {
        if (d->busy[i] == fb) {
            return true;
        }
    }
    return false;
}

// Fence every in-flight PPA op, then show the queued frames in order (the
// last show wins the next VSYNC; the earlier ones were sequential).
static void drain_pending(moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    o->ppa_sync(o->ctx);
    d->nbusy = 0;
    for (int i = 0; i < d->npend; i++) {
        o->show(o->ctx, d->pend[i]);
    }
    d->npend = 0;
}

static bool queued_game(const moy_dsi_t *d) {
    for (int i = 0; i < d->npend; i++) {
        if (d->kind[i] == MOY_DSI_GAME) {
            return true;
        }
    }
    return false;
}

void moy_dsi_present(moy_dsi_t *d, bool stamp_kicked) {
    const moy_dsi_ops_t *o = d->ops;
    bool game_pending = d->composite_pending;
    if (stamp_kicked) {
        d->composite_pending = true;
    }
    int n = d->nfbs;
    if (n <= 1) {
        o->msync(o->ctx);                 // single buffer: the CPU-cache msync only
        return;
    }
    if (n >= 3) {
        if (d->composite_pending) {
            // An async composite (or drag stamp) targets this buffer: queue its
            // show and draw the next frame into another.
            if (d->npend < MOY_PRESENT_MAX_FBS) {
                d->pend[d->npend] = d->back;
                d->kind[d->npend] = (stamp_kicked && !game_pending)
                                    ? MOY_DSI_STAMP : MOY_DSI_GAME;
                d->npend++;
            }
            if (d->nbusy < MOY_PRESENT_MAX_FBS) {
                d->busy[d->nbusy++] = d->back;
            }
            d->composite_pending = false;
            d->deferred++;
        } else {
            // A newer frame supersedes every queued show: drop them.
            if (d->npend) {
                d->obsolete += (uint32_t)d->npend;
                d->npend = 0;
            }
            o->show(o->ctx, d->back);     // msync + zero-copy switch
        }
        d->back = (uint8_t)((d->back + 1u) % (unsigned)n);
        if (busy(d, d->back)) {
            uint32_t t0 = o->ticks_us(o->ctx);
            drain_pending(d);
            d->fences++;
            d->fence_us += o->ticks_us(o->ctx) - t0;
        }
        return;
    }
    if (d->composite_pending) {
        d->pending = d->back;
        d->composite_pending = false;
        d->deferred++;
        return;
    }
    o->show(o->ctx, d->back);             // msync + zero-copy scan-out switch
    d->back ^= 1u;
}

void moy_dsi_present_pending(moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    if (d->nfbs >= 3) {
        if (!d->npend) {
            return;
        }
        if (queued_game(d)) {
            // The cart's next draw overwrites the composite's SOURCE: fence.
            uint32_t t0 = o->ticks_us(o->ctx);
            drain_pending(d);
            d->game_n++;
            d->game_us += o->ticks_us(o->ctx) - t0;
            return;
        }
        if (o->ppa_done == NULL || o->ppa_done(o->ctx)) {
            drain_pending(d);             // the sync is ~free once done
        }
        return;
    }
    if (d->pending < 0) {
        return;
    }
    uint32_t t0 = o->ticks_us(o->ctx);
    o->ppa_sync(o->ctx);                  // fence the async composite
    d->game_n++;
    d->game_us += o->ticks_us(o->ctx) - t0;
    o->show(o->ctx, d->pending);
    d->back ^= 1u;                        // the other buffer is free to draw
    d->pending = -1;
}

void moy_dsi_fence(moy_dsi_t *d) {
    if (d->nbusy) {
        drain_pending(d);
    } else if (d->pending >= 0) {
        moy_dsi_present_pending(d);
    }
}
