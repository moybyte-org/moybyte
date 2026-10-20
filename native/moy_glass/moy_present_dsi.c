// The DSI compositor (moy_present.h): the Waveshare P4's frame state machine
// over moy_dsi's scan buffers and the PPA's async composite.

#include <string.h>

#include "moy_present.h"

// The longest the pre-frame hook waits for the refresh that takes the buffer
// it hands out off glass: one panel refresh, with room.
#define MOY_DSI_REFRESH_WAIT_US 40000

// Show `n`: its scan-out switch takes effect at the panel's next refresh,
// and until then the buffer the show left is still scanned.
static void show_fb(moy_dsi_t *d, int n) {
    const moy_dsi_ops_t *o = d->ops;
    o->show(o->ctx, n);
    d->shows++;
    d->seq[n] = d->shows;
    d->rseq[n] = o->refreshes != NULL ? o->refreshes(o->ctx) : 0;
    d->front = (uint8_t)n;
}

// The buffer the panel scans NOW: the latest show a refresh has followed
// (the last one shown where the transport counts no refreshes).
static int on_glass(const moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    if (o->refreshes == NULL) {
        return d->front;
    }
    int32_t now = o->refreshes(o->ctx);
    int best = d->front;
    uint32_t seq = 0;
    for (int i = 0; i < d->nfbs; i++) {
        if (d->seq[i] > seq && now - d->rseq[i] > 0) {
            seq = d->seq[i];
            best = i;
        }
    }
    return best;
}

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
        show_fb(d, 0);
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
        show_fb(d, d->pend[i]);
    }
    d->npend = 0;
}

// Hand the next frame a buffer that is off glass. Once every other buffer
// holds a queued show, the one drawn into next is the one being scanned:
// fence the queue and show it, then wait out the refresh that takes the
// switch. Drawn into while scanned, a frame reaches glass half drawn -- a
// dragged window gone for a refresh, the desk under it showing through.
static void claim_back(moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    uint32_t t0 = o->ticks_us(o->ctx);
    bool waited = false;
    if (d->back == d->front && d->npend) {
        drain_pending(d);
        waited = true;
    }
    if (o->refreshes != NULL && on_glass(d) == d->back) {
        while (on_glass(d) == d->back
               && (uint32_t)(o->ticks_us(o->ctx) - t0) < MOY_DSI_REFRESH_WAIT_US) {
        }
        waited = true;
    }
    if (waited) {
        d->fences++;
        d->fence_us += o->ticks_us(o->ctx) - t0;
    }
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
            show_fb(d, d->back);          // msync + zero-copy switch
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
    show_fb(d, d->back);                  // msync + zero-copy scan-out switch
    d->back ^= 1u;
}

void moy_dsi_show_landed(moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    if (d->nfbs >= 3 && d->npend && !queued_game(d)
            && (o->ppa_done == NULL || o->ppa_done(o->ctx))) {
        drain_pending(d);                 // the sync is ~free once done
    }
}

void moy_dsi_present_pending(moy_dsi_t *d) {
    const moy_dsi_ops_t *o = d->ops;
    if (d->nfbs >= 3) {
        if (d->npend && queued_game(d)) {
            // The cart's next draw overwrites the composite's SOURCE: fence.
            uint32_t t0 = o->ticks_us(o->ctx);
            drain_pending(d);
            d->game_n++;
            d->game_us += o->ticks_us(o->ctx) - t0;
        } else if (d->npend && (o->ppa_done == NULL || o->ppa_done(o->ctx))) {
            drain_pending(d);             // the sync is ~free once done
        }
        claim_back(d);
        return;
    }
    if (d->pending < 0) {
        return;
    }
    uint32_t t0 = o->ticks_us(o->ctx);
    o->ppa_sync(o->ctx);                  // fence the async composite
    d->game_n++;
    d->game_us += o->ticks_us(o->ctx) - t0;
    show_fb(d, d->pending);
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
