// The tick model (moy_tick.h has the design).

#include <string.h>

#include "moy_tick.h"

static void reset_window(moy_tick_t *t) {
    t->win_s = ((moy_tick_real_t)0.0);
    t->win_cycles = 0;
    t->win_late = 0;
    t->win_stalls = 0;
}

static void reset(moy_tick_t *t) {
    t->acc = ((moy_tick_real_t)0.0);
    t->phase = 0;
    t->ticks = t->draws = t->misses = 0;
    t->tick_cost = t->draw_frame = t->tick_frame = t->late = ((moy_tick_real_t)0.0);
    t->probing = false;
    t->probe_from = 0;
    t->probe_k = MOY_TICK_PROBE_K;
    t->fail_cost = ((moy_tick_real_t)0.0);
    t->fresh_from = 0;
    t->clean = t->late_wins = 0;
    t->warm = true;
    t->d_known = t->t_known = t->primed = t->drew = t->ticked = false;
    t->cyc_ticks = t->cyc_misses = 0;
    t->cyc_stall = false;
    reset_window(t);
    t->n = 0;
    t->draw = false;
}

void moy_tick_start(moy_tick_t *t, int rate_hz, bool steady) {
    bool uncapped = t->uncapped;
    memset(t, 0, sizeof(*t));
    t->uncapped = uncapped;
    t->rate = rate_hz == 60 ? 60 : 30;
    t->period = ((moy_tick_real_t)1.0) / (moy_tick_real_t)t->rate;
    t->tick_ms = 1000u / (uint32_t)t->rate;
    t->steady = steady;
    t->div = 1;
    reset(t);
}

void moy_tick_mode(moy_tick_t *t, bool steady, bool uncap) {
    t->steady = steady;
    t->uncapped = uncap;
    reset_window(t);
}

void moy_tick_note(moy_tick_t *t, moy_tick_real_t tick_s) {
    t->tick_cost = tick_s;
}

moy_tick_real_t moy_tick_cycle(const moy_tick_t *t, int div) {
    moy_tick_real_t tf = t->t_known ? t->tick_frame : ((moy_tick_real_t)0.0);
    return t->draw_frame + (moy_tick_real_t)(div - 1) * tf;
}

bool moy_tick_fits(const moy_tick_t *t, int div) {
    return moy_tick_cycle(t, div) <= (moy_tick_real_t)div * t->period;
}

static void ema_d(moy_tick_t *t, moy_tick_real_t x) {
    if (t->d_known) {
        t->draw_frame += (x - t->draw_frame) * MOY_TICK_ALPHA;
    } else {
        t->draw_frame = x;
        t->d_known = true;
    }
}

static void ema_t(moy_tick_t *t, moy_tick_real_t x) {
    if (t->t_known) {
        t->tick_frame += (x - t->tick_frame) * MOY_TICK_ALPHA;
    } else {
        t->tick_frame = x;
        t->t_known = true;
    }
}

// A divisor arrived at rather than probed down to owes nothing to the probe
// that failed at the one before it.
static void probe_reset(moy_tick_t *t) {
    t->probe_k = MOY_TICK_PROBE_K;
    t->fail_cost = ((moy_tick_real_t)0.0);
}

static void set_div(moy_tick_t *t, int div) {
    t->div = (uint8_t)div;
    t->phase = (uint8_t)(t->phase % div);
    t->cyc_ticks = 0;
    t->cyc_misses = 0;
    t->cyc_stall = false;
    t->fresh_from = 0;
    t->late = ((moy_tick_real_t)0.0);         // the old N's lateness says nothing about this one
}

// Whether N-1 has earned a window: K clean windows, or the cycle a failed
// probe here cost has since dropped by PROBE_DROP (the scene got cheaper).
static bool may_probe(const moy_tick_t *t) {
    if (t->clean >= t->probe_k) {
        return true;
    }
    return t->fail_cost > ((moy_tick_real_t)0.0)
           && moy_tick_cycle(t, t->div - 1) <= t->fail_cost * (((moy_tick_real_t)1.0) - MOY_TICK_PROBE_DROP);
}

// N-1 did not hold: back to `back`, remember what its cycle cost while it did
// not, and wait longer before asking again.
static void probe_failed(moy_tick_t *t, int back) {
    int k = t->probe_k * 2;
    if (k < MOY_TICK_PROBE_BACK) {
        k = MOY_TICK_PROBE_BACK;
    }
    if (k > MOY_TICK_PROBE_MAX) {
        k = MOY_TICK_PROBE_MAX;
    }
    set_div(t, back);
    t->fail_cost = moy_tick_cycle(t, back - 1);
    t->probe_k = (uint16_t)k;
}

// One verdict on N from the closing window's evidence: at most one step.
static void decide(moy_tick_t *t) {
    uint32_t cyc = t->win_cycles;
    uint32_t n_late = t->win_late;
    bool late = n_late * MOY_TICK_LATE_CYCLE > cyc;
    // A stall is one late event and never evidence about a scene, so it comes
    // off before asking whether the window was VERY late.
    bool very = cyc > 0
                && (moy_tick_real_t)((int32_t)n_late - (int32_t)t->win_stalls) >= (moy_tick_real_t)cyc * MOY_TICK_VERY_LATE;
    moy_tick_real_t per = t->period;
    if (late && ((t->t_known && t->d_known && t->tick_frame >= t->draw_frame)
                 || t->tick_cost >= per)) {
        // No divisor helps a loop whose tick-only frame costs at least what a
        // drawing one does, nor one whose logic tick already costs a period.
        if (t->div != 1) {
            set_div(t, 1);
        }
        t->probing = false;
        probe_reset(t);
        t->clean = 0;
        t->late_wins = 0;
        return;
    }
    if (t->probing) {
        // The window that tried N-1 keeps it only if it came back CLEAN.
        t->probing = false;
        t->late_wins = 0;
        if (n_late * MOY_TICK_PROBE_LATE > cyc) {
            probe_failed(t, t->probe_from);
        } else {
            probe_reset(t);
            t->fresh_from = t->probe_from;
            t->clean = 1;
        }
        return;
    }
    if (!late) {
        t->late_wins = 0;
        t->clean++;
        if (t->div > 1 && t->clean >= MOY_TICK_PROBE_K && may_probe(t)) {
            t->probing = true;
            t->probe_from = t->div;
            t->clean = 0;
            set_div(t, t->div - 1);
        }
        return;
    }
    t->clean = 0;
    if (t->fresh_from) {
        // The first late window after a probe passed: that probe was
        // optimistic, so go back now.
        t->late_wins = 0;
        probe_failed(t, t->fresh_from);
        return;
    }
    t->late_wins++;
    if (!very && t->late_wins < 2) {
        return;
    }
    t->late_wins = 0;
    for (int d = t->div + 1; d <= MOY_TICK_MAX_DIV; d++) {
        if (moy_tick_fits(t, d)) {
            set_div(t, d);
            probe_reset(t);
            return;
        }
    }
}

bool moy_tick_plan(moy_tick_t *t, moy_tick_real_t dt, uint8_t *n_out) {
    moy_tick_real_t per = t->period;
    bool stall = dt > (moy_tick_real_t)MOY_TICK_STALL * per;
    if (t->primed && !t->warm) {
        if (t->drew) {
            ema_d(t, dt);
        } else if (t->ticked) {
            ema_t(t, dt);
        } else {
            ema_t(t, dt + t->tick_cost);
        }
    }
    t->primed = true;
    if (stall) {
        t->cyc_stall = true;
    }
    moy_tick_real_t acc = t->acc + dt;
    int n = 0;
    moy_tick_real_t floor_ = per * (((moy_tick_real_t)1.0) - MOY_TICK_EPS);
    if (acc >= floor_) {
        acc -= per;
        n = 1;
        if (t->tick_cost * ((moy_tick_real_t)2.0) < per) {
            while (n < MOY_TICK_MAX_CATCHUP && acc >= floor_) {
                acc -= per;
                n++;
            }
        }
        if (acc > per) {            // what cannot be paid is written off
            acc = per;
            t->misses++;
            t->cyc_misses++;
        }
    }
    t->acc = acc;
    t->n = (uint8_t)n;
    if (n_out != NULL) {
        *n_out = (uint8_t)n;
    }
    bool draw = false;
    if (n) {
        t->ticks += (uint32_t)n;
        t->cyc_ticks += (uint32_t)n;
        int ph = t->phase + n;
        if (ph >= t->div) {
            draw = true;
            ph %= t->div;
        }
        t->phase = (uint8_t)ph;
    }
    moy_tick_real_t win = t->steady ? MOY_TICK_STEADY_S : MOY_TICK_FREE_S;
    if (t->uncapped) {
        t->draws++;
        t->draw = true;
        t->drew = true;
        t->ticked = false;
        t->win_s += dt;
        if (t->win_s >= win) {
            t->warm = false;
            reset_window(t);
        }
        return true;
    }
    if (draw) {
        t->draws++;
        uint32_t cyc = t->cyc_ticks;
        uint32_t late = (cyc > t->div ? cyc - t->div : 0) + t->cyc_misses;
        if (t->cyc_stall) {
            if (late > 1) {
                late = 1;
            }
            t->win_stalls += late;
        }
        t->cyc_ticks = 0;
        t->cyc_misses = 0;
        t->cyc_stall = false;
        t->win_cycles++;
        t->win_late += late;
        t->late += ((moy_tick_real_t)late - t->late) * MOY_TICK_ALPHA;
    }
    t->draw = draw;
    t->drew = draw;
    t->ticked = n > 0 && !draw;
    t->win_s += dt;
    if (t->win_s >= win) {
        if (t->warm) {
            t->warm = false;
        } else {
            decide(t);
        }
        reset_window(t);
    }
    return draw;
}

void moy_tick_stats(const moy_tick_t *t, moy_tick_stats_t *out) {
    out->div = t->div;
    out->ticks = t->ticks;
    out->draws = t->draws;
    out->misses = t->misses;
    out->probing = t->probing;
    out->late = t->late;
    out->draw_frame = t->draw_frame;
    out->tick_frame = t->tick_frame;
}
