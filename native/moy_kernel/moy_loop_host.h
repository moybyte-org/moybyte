// moy_loop_host: the loop's trace tier. moy_loop_host.c has the why.

#ifndef MOY_LOOP_HOST_H
#define MOY_LOOP_HOST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// The loop on the trace ops: the clock at `clock_ms`, a light that can dim or
// not, the ladder's rungs all OFF.
void moy_loop_host_init(int fps_cap, bool can_dim, uint32_t clock_ms);
void moy_loop_host_clock(uint32_t ms);
// The clock moved on by `us` (an upcall's own cost, in a test).
void moy_loop_host_advance(uint32_t us);
// What a stage op costs on the fake clock each time it runs.
enum {
    MOY_LOOP_HOST_INPUTS, MOY_LOOP_HOST_POINTER, MOY_LOOP_HOST_PRESENT,
    MOY_LOOP_HOST_TAIL, MOY_LOOP_HOST_FENCE,
};
void moy_loop_host_cost(int stage, uint32_t us);
// What the next frames' inputs answer.
void moy_loop_host_input(bool click, bool active);
// Bytes for the dev channel's reader.
void moy_loop_host_feed(const uint8_t *bytes, size_t n);
// The stage tokens since the last clear, space-separated; and the clear.
const char *moy_loop_host_log(void);
void moy_loop_host_clear(void);
// A token of the driver's own, in order with the loop's.
void moy_loop_host_note(const char *token);

#endif // MOY_LOOP_HOST_H
