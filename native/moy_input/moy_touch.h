// moy_touch: what every touch driver is the same about -- the raw-to-glass
// mapping and the no-news contract -- in one body for the GT911 (T-Deck,
// Waveshare P4), the GSL3680 (Guition P4) and the AXS15231 (Guition S3).
//
// THE MAPPING: swap the axes, scale the controller's own space (an origin and
// a span, where it is not the glass's) onto the panel's, flip, clamp both ends.
// A touch past the panel edge reads bigger than its axis, which a flip turns
// negative, so the lower clamp matters as much as the upper.
//
// THE NO-NEWS CONTRACT, all three clauses:
//   1. HOLD the point while the finger is down and the hardware said nothing
//      this pass: a phantom release mid-drag ends the gesture (#74, #113).
//   2. Mark the repeat STALE (fresh = false): kinetic scrolling must not
//      average a delta the hardware never measured into a fling.
//   3. BOUND the hold (MOY_TOUCH_HOLD_MS): a missed finger-up frees the
//      pointer well under a second later, never wedges it down.
// Extrapolation is opt-in per board: a held pass glides the last point along
// the finger's measured velocity (fresh stays false); a press edge resets the
// velocity, and a fresh sample that trails the glide by under 32 px keeps the
// glide's point so the pointer never steps backwards.

#ifndef MOY_TOUCH_H
#define MOY_TOUCH_H

#include <stdbool.h>
#include <stdint.h>

#define MOY_TOUCH_HOLD_MS 400   // #74-measured: rides out the 20-45 ms stall clusters
#define MOY_TOUCH_HOLD_MS_STREAM 90  // a controller that streams while touched (the AXS)

typedef struct {
    int32_t w, h;               // the glass
    int32_t raw_w, raw_h;       // the controller's span; 0 when it is the glass's
    int32_t raw_x0, raw_y0;
    bool swap, flip_x, flip_y;
} moy_touch_map_t;

typedef struct {
    bool down;
    bool fresh;
    bool has_last;
    bool extrapolate;
    bool gliding;
    int32_t lx, ly;             // the last measured point
    int32_t gx, gy;             // the last displayed glide point
    int32_t w, h;               // the glide's clamp, 0 for none
    int32_t hold_ms;            // the hold's bound
    uint32_t ms;                // when (lx, ly) landed
    float vx, vy;               // px/ms over fresh samples
    float damp;
} moy_touch_held_t;

// What a poll answers: no pointer, or a point and whether it is the press edge.
typedef struct {
    bool down;
    bool edge;
    int32_t x, y;
} moy_touch_pt_t;

void moy_touch_map(const moy_touch_map_t *m, int32_t rx, int32_t ry, int32_t *x, int32_t *y);

void moy_touch_held_init(moy_touch_held_t *hp, bool extrapolate, int32_t w, int32_t h, float damp,
                         int32_t hold_ms);
// Exactly one of the three per poll pass; `now` is ms on the VM's tick clock
// (moy_input.h's MOY_INPUT_TICKS_PERIOD).
void moy_touch_sample(moy_touch_held_t *hp, int32_t x, int32_t y, uint32_t now, moy_touch_pt_t *out);
void moy_touch_release(moy_touch_held_t *hp, moy_touch_pt_t *out);
void moy_touch_hold(moy_touch_held_t *hp, uint32_t now, moy_touch_pt_t *out);

#endif // MOY_TOUCH_H
