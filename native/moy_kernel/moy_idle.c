// moy_idle: the idle ladder. moy_idle.h has the contract.

#include "moy_idle.h"

static const char *const NAMES[MOY_IDLE_RUNGS] = {
    "awake", "dim", "saver", "blank", "sleep",
};

void moy_idle_init(moy_idle_t *d, bool can_dim, uint32_t now) {
    for (int i = 0; i < MOY_IDLE_RUNGS; i++) {
        d->after_s[i] = 0;
    }
    d->idle_at = now;
    d->state = MOY_IDLE_AWAKE;
    d->can_dim = can_dim;
    d->force = false;
    d->wake = false;
}

void moy_idle_set(moy_idle_t *d, int rung, uint32_t seconds) {
    if (rung > MOY_IDLE_AWAKE && rung < MOY_IDLE_RUNGS) {
        d->after_s[rung] = seconds;
    }
}

uint32_t moy_idle_get(const moy_idle_t *d, int rung) {
    return rung > MOY_IDLE_AWAKE && rung < MOY_IDLE_RUNGS ? d->after_s[rung] : 0;
}

void moy_idle_blank(moy_idle_t *d) {
    d->force = true;
}

void moy_idle_wake(moy_idle_t *d) {
    d->wake = true;
}

int moy_idle_light(const moy_idle_t *d) {
    switch (d->state) {
        case MOY_IDLE_AWAKE:
            return MOY_IDLE_LIGHT_FULL;
        case MOY_IDLE_DIM:
            return MOY_IDLE_LIGHT_DIM;
        case MOY_IDLE_SAVER:
            // The saver keeps the light the rung below it left.
            return d->can_dim && d->after_s[MOY_IDLE_DIM] ? MOY_IDLE_LIGHT_DIM
                                                          : MOY_IDLE_LIGHT_FULL;
        default:
            return MOY_IDLE_LIGHT_OFF;
    }
}

const char *moy_idle_name(int state) {
    return state >= 0 && state < MOY_IDLE_RUNGS ? NAMES[state] : "?";
}

// The deepest rung whose timeout `idle_ms` has reached; SLEEP is a hook only.
static int deepest(const moy_idle_t *d, uint32_t idle_ms) {
    int to = MOY_IDLE_AWAKE;
    for (int r = MOY_IDLE_DIM; r <= MOY_IDLE_BLANK; r++) {
        uint32_t s = d->after_s[r];
        if (s == 0 || (r == MOY_IDLE_DIM && !d->can_dim)) {
            continue;
        }
        if (idle_ms >= s * 1000u) {
            to = r;
        }
    }
    return to;
}

static void go(moy_idle_t *d, int to, moy_idle_out_t *out) {
    int was = d->state;
    if (to == was) {
        return;
    }
    int before = moy_idle_light(d);
    d->state = (uint8_t)to;
    int light = moy_idle_light(d);
    if (light != before) {
        out->light = light;
    }
    if (to > was) {
        out->entered = true;
    } else if (was >= MOY_IDLE_SAVER) {
        // Leaving the saver or the dark panel: the console repaints what is
        // under it, and the input that woke it is not a press.
        out->repaint = true;
        out->swallow = true;
    }
}

void moy_idle_tick(moy_idle_t *d, uint32_t now, bool active, moy_idle_out_t *out) {
    out->light = -1;
    out->swallow = false;
    out->repaint = false;
    out->entered = false;
    if (d->force) {
        d->force = false;
        d->wake = false;
        d->idle_at = now;
        go(d, MOY_IDLE_BLANK, out);
        return;
    }
    if (active || d->wake) {
        d->wake = false;
        d->idle_at = now;
        go(d, MOY_IDLE_AWAKE, out);
        return;
    }
    int to = deepest(d, now - d->idle_at);
    if (to > d->state) {
        go(d, to, out);
    }
}
