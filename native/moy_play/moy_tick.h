// The tick model (#217; docs/kernel_cartpath_2026-10.md §3.4): ONE scheduler
// for a cart's logic and its draw.
//
// Logic runs at the cart's declared rate -- 30, or 60 by manifest -- and is
// never reduced, because reducing it IS slowdown for a frame-counted cart
// (every imported PICO-8 cart, and the kid cart that does `x += 1`). A late
// frame runs extra ticks to catch up only while a tick is CHEAP (under half
// the period, measured per tick); past that line a late frame slows time, as
// PICO-8's does, and debt past one period is written off and REPORTED as a
// miss.
//
// Draw runs on an integer divisor N of the tick: 30-from-60 is perfectly even,
// where "draw 45 of 60" is 1-1-2 delivery that judders. Whether an N HOLDS is
// measured, never predicted: a draw cycle is N ticks by design, and every
// tick it runs beyond that (catch-up the design did not plan for), every
// period written off, and every stall is a late event; a window is late when
// its cycles ran more than a third of a tick late on average, and VERY late
// when they averaged most of a whole one -- a cart losing a third of its time
// or more, which a second window of waiting would spend again. N steps UP
// after two late windows in a row, or after ONE very late window, to the
// smallest larger N whose cycle -- one drawing frame plus N-1 tick-only
// frames, D + (N-1)*T on slow EMAs -- fits its periods; that is the only
// thing `fits` predicts. N steps DOWN only by PROBING: after K clean windows
// try N-1 for one window, and keep it only if that window came back CLEAN, a
// stricter bar than the one that HOLDS an N. A failed probe records what its
// cycle cost and the next probe waits for EVIDENCE -- that cycle costing a
// fifth less, so the scene really did get cheaper -- or for a long back-off
// ceiling, whichever comes first, so a cart held in one heavy scene stops
// probing instead of dropping into slow motion for a window every back-off
// period; and the first late window after a probe PASSED steps back at once,
// because that probe was the optimistic one. A loop whose tick-only frame
// costs at least what a drawing one does, or whose logic tick costs a period,
// cannot be helped by drawing less often, so N pins at 1 and the misses say
// so. The first window after a start is warm-up: it teaches nothing and
// decides nothing. STEADY and FREE are one parameter -- how long a window is.
//
// Pure arithmetic on an INJECTED dt, never a clock, so a test walks an exact
// trajectory; allocation-free; and in `float`, because the boards' FPUs are
// single-precision: the host runs the same arithmetic, so the trajectory a
// trace pins is the board's.

#ifndef MOY_TICK_H
#define MOY_TICK_H

#include <stdbool.h>
#include <stdint.h>

// The model's real number: moy_tick_real_t on every tier. A parity build sets it to
// double to hold the logic to a double-precision reference.
#ifndef MOY_TICK_REAL
#define MOY_TICK_REAL float
#endif
typedef MOY_TICK_REAL moy_tick_real_t;

#define MOY_TICK_MAX_CATCHUP 4    // logic ticks one frame may run to catch up
#define MOY_TICK_MAX_DIV 4        // past this, drawing less often buys nothing a kid would keep
#define MOY_TICK_EPS ((moy_tick_real_t)0.02)        // of a period: absorbs an integer-ms host frame (33 vs 33.33)
#define MOY_TICK_STEADY_S ((moy_tick_real_t)2.0)    // a STEADY window, seconds
#define MOY_TICK_FREE_S ((moy_tick_real_t)0.25)     // a FREE window: follows load in a quarter second, judders for it
#define MOY_TICK_LATE_CYCLE 3     // a window is late when its cycles averaged over 1/3 tick late
#define MOY_TICK_VERY_LATE ((moy_tick_real_t)0.8)   // ...and VERY late at four fifths of a whole one: step up on ONE
#define MOY_TICK_STALL 4          // a frame over this many periods is ONE late event, not many ticks
#define MOY_TICK_PROBE_K 2        // clean windows at N before N-1 is tried
#define MOY_TICK_PROBE_LATE 16    // a probe holds only under 1/16 of a tick late per cycle
#define MOY_TICK_PROBE_BACK 32    // windows a FAILED probe waits before time alone retries it
#define MOY_TICK_PROBE_MAX 64     // the ceiling on that wait: ~2 minutes of STEADY windows
#define MOY_TICK_PROBE_DROP ((moy_tick_real_t)0.2)  // ...or sooner, once the cycle it failed on costs this much less
#define MOY_TICK_ALPHA ((moy_tick_real_t)0.125)     // the frame-cost EMAs: a hitch is one sample in eight

typedef struct {
    int rate;               // 30 or 60
    moy_tick_real_t period;
    uint32_t tick_ms;
    bool steady, uncapped;
    uint8_t div;            // N
    uint8_t n;              // the plan's answer: logic ticks this frame
    bool draw;              // the plan's answer: this frame draws
    uint32_t ticks, draws, misses;
    moy_tick_real_t acc;
    uint8_t phase;
    moy_tick_real_t tick_cost;        // the last logic tick, seconds (the CHEAP rule)
    moy_tick_real_t draw_frame;       // D: a drawing loop frame, wall seconds (EMA)
    moy_tick_real_t tick_frame;       // T: a loop frame that did not draw (EMA)
    moy_tick_real_t late;             // late events per draw cycle (EMA, a diagnostic)
    bool probing;           // this window tries N-1; probe_from is the N it left
    uint8_t probe_from;
    uint16_t probe_k;
    moy_tick_real_t fail_cost;        // what the cycle cost when a probe last failed here
    uint8_t fresh_from;     // a probe just PASSED: the N one late window returns to
    uint16_t clean;         // clean windows in a row at this N
    uint16_t late_wins;     // late windows in a row at this N
    bool warm;              // the first window teaches and decides nothing
    bool d_known, t_known, primed, drew, ticked;
    uint32_t cyc_ticks;     // ticks since the last draw
    uint32_t cyc_misses;    // periods written off since the last draw
    bool cyc_stall;
    moy_tick_real_t win_s;
    uint32_t win_cycles, win_late, win_stalls;
} moy_tick_t;

typedef struct {
    uint8_t div;
    uint32_t ticks, draws, misses;
    bool probing;
    moy_tick_real_t late, draw_frame, tick_frame;
} moy_tick_stats_t;

// Arm for a run at `rate_hz` (60 opt-in; anything else is the 30 the console
// guarantees), with a fresh divisor of 1. `uncapped` is kept: it is the
// console's switch, not the run's.
void moy_tick_start(moy_tick_t *t, int rate_hz, bool steady);
void moy_tick_mode(moy_tick_t *t, bool steady, bool uncap);
// Account one loop frame of `dt` seconds: `*n` logic ticks to run now, and
// whether this frame draws.
bool moy_tick_plan(moy_tick_t *t, moy_tick_real_t dt, uint8_t *n);
// What the logic tick that just ran cost, on the host clock.
void moy_tick_note(moy_tick_t *t, moy_tick_real_t tick_s);
void moy_tick_stats(const moy_tick_t *t, moy_tick_stats_t *out);
// What a draw cycle at `div` costs on the slow EMAs, and whether it fits its
// div periods: the one prediction the model makes, to pick an N to step up to.
moy_tick_real_t moy_tick_cycle(const moy_tick_t *t, int div);
bool moy_tick_fits(const moy_tick_t *t, int div);

#endif // MOY_TICK_H
