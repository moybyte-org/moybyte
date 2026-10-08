// moy_idle: the idle ladder (docs/kernel_survival_2026-10.md §7.2, §13 answers
// 1 and 7). A pure state machine over the caller's clock: the loop ticks it
// after EVERY input source and the dev channel have been read, and it answers
// what the backlight should be, whether this frame's click is swallowed, and
// whether the frame the console paints next must repaint everything.
//
// The rungs, each a settings row whose value is seconds of no input, 0 = OFF:
//
//   DIM     the backlight at the board's dim level (a board whose light is
//           binary has no dim rung: moy_idle_init's `can_dim` false)
//   SAVER   the console draws the screensaver (a cover cycle on the S3s, a
//           wallpaper cart on the P4s) at the light the rung below left
//   BLANK   the backlight off; the board keeps rendering while dark
//   SLEEP   a hook only: no rung reaches it until a board has a gauge and a
//           reason (#130)
//
// Three behaviours carried from the Python IdleBlank this replaces:
//   1. the input that WAKES the screen from SAVER or BLANK is swallowed (a
//      wake tap never presses what it landed on); DIM shows the real screen,
//      so a tap there acts;
//   2. waking from SAVER or BLANK moves the kernel epoch (`repaint`), so the
//      console's gate repaints every retained buffer the saver or the dark
//      panel left behind;
//   3. an EXPLICIT blank (`power off`) outranks the activity of the frame that
//      asked for it -- the serial line is itself activity.

#ifndef MOY_IDLE_H
#define MOY_IDLE_H

#include <stdbool.h>
#include <stdint.h>

enum {
    MOY_IDLE_AWAKE = 0,
    MOY_IDLE_DIM = 1,
    MOY_IDLE_SAVER = 2,
    MOY_IDLE_BLANK = 3,
    MOY_IDLE_SLEEP = 4,
    MOY_IDLE_RUNGS = 5,
};

// The backlight levels the ladder asks for: full, the dim rung's, off.
#define MOY_IDLE_LIGHT_FULL 255
#define MOY_IDLE_LIGHT_DIM 48
#define MOY_IDLE_LIGHT_OFF 0

typedef struct {
    uint32_t after_s[MOY_IDLE_RUNGS];   // per rung; 0 = OFF. [AWAKE] unused
    uint32_t idle_at;                   // the clock at the last activity
    uint8_t state;
    bool can_dim;
    bool force;                         // `power off` pending
    bool wake;                          // `power on` pending
} moy_idle_t;

// What one tick decided.
typedef struct {
    int light;              // the backlight level to set now, or -1: unchanged
    bool swallow;           // this frame's click and press are dropped
    bool repaint;           // the console repaints every retained buffer
    bool entered;           // the state moved deeper this tick (say so)
} moy_idle_out_t;

void moy_idle_init(moy_idle_t *d, bool can_dim, uint32_t now);
// A rung's timeout, seconds; 0 = OFF. A dim timeout on a board that cannot dim
// is kept and never reached.
void moy_idle_set(moy_idle_t *d, int rung, uint32_t seconds);
uint32_t moy_idle_get(const moy_idle_t *d, int rung);
// `power off` / `power on`: applied at the next tick, outranking its activity.
void moy_idle_blank(moy_idle_t *d);
void moy_idle_wake(moy_idle_t *d);
void moy_idle_tick(moy_idle_t *d, uint32_t now, bool active, moy_idle_out_t *out);
// The light the current state shows.
int moy_idle_light(const moy_idle_t *d);
const char *moy_idle_name(int state);

#endif // MOY_IDLE_H
