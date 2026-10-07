// The banded compositor (moy_present.h): the T-Deck's and the Guition S3's
// frame state machine over their panel module's transport.

#include "moy_present.h"

void moy_banded_init(moy_banded_t *b, const moy_banded_ops_t *ops, int nfbs,
                     bool overlap) {
    if (nfbs < 1) {
        nfbs = 1;
    }
    if (nfbs > MOY_PRESENT_MAX_FBS) {
        nfbs = MOY_PRESENT_MAX_FBS;
    }
    b->ops = ops;
    b->nfbs = (uint8_t)nfbs;
    b->back = 0;
    // The overlap needs two distinct buffers to ping-pong between -- with one
    // the DMA would read exactly what the next frame draws into -- and a
    // transport that can split the flush.
    b->overlap = (overlap && nfbs > 1 && ops->kick != NULL) ? 1u : 0u;
    b->presents = 0;
}

static void swap(moy_banded_t *b) {
    if (b->nfbs > 1) {
        b->back = (uint8_t)((b->back + 1u) % b->nfbs);
    }
}

int moy_banded_present(moy_banded_t *b) {
    const moy_banded_ops_t *o = b->ops;
    b->presents++;
    if (!b->overlap) {
        int e = o->show(o->ctx, b->back);
        swap(b);
        return e;
    }
    // 1. The previous frame's residue: most of it went out behind the render
    //    that just ran.
    o->drain(o->ctx);
    // 2. The buffer just drawn becomes the FRONT; drawing moves to the other,
    //    which the drain just made safe to overwrite.
    uint8_t front = b->back;
    swap(b);
    // 3. Hand it to the feeder and return.
    return o->kick(o->ctx, front);
}

bool moy_banded_fence(moy_banded_t *b) {
    return b->ops->drain(b->ops->ctx);
}
