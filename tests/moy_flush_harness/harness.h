// The harness's own API (#208 rank 1): what the stub headers under stubs/ are
// wired to, and what a scenario in main.c drives the engine WITH.
//
// The shape is the moy_ppa precedent: there is no ESP-IDF and no board on a dev
// machine, so the REAL native/moy_flush/moy_flush.c is compiled unmodified
// against a minimal stand-in for the surface it uses. It is compiled, never
// transcribed -- a transcription cannot catch the C being wrong, which is the
// whole lesson of the 2026-08-06 provisional_tline failure.
//
// WHY A COOPERATIVE DISCRETE-EVENT SCHEDULER AND NOT PTHREADS. moy_flush's
// subject matter is a two-party protocol across a core boundary with three
// bounded waits, and its header says every clause of it was a race once. A
// pthread harness would reproduce the races non-deterministically -- i.e. it
// would be a flaky test that reads as protection. So: exactly one context runs
// at a time, switches happen ONLY inside the blocking primitives (or at an
// explicit yield), and the clock is virtual and advanced by the harness. Every
// scenario is then a fixed interleaving, and a timeout is an assertion about
// numbers rather than a wall-clock wait.
//
// THE TWO PARTIES. Context 0 is the VM task (MicroPython's core-1 task: it
// calls kick/drain/stop). The feeder is whatever xTaskCreatePinnedToCore made,
// on its own ucontext stack. The done-ISR is a timer EVENT: the board double
// schedules one per queued band, and it runs at its due time on whichever stack
// the scheduler happens to be on -- which is what an interrupt does.
#ifndef MOY_FLUSH_HARNESS_H
#define MOY_FLUSH_HARNESS_H

#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct h_sem;                       // stubs/freertos/semphr.h names it

// -- the virtual clock -------------------------------------------------------

// One FreeRTOS tick, matching the boards: CONFIG_FREERTOS_HZ is 100 on both S3
// builds, which is what makes pdMS_TO_TICKS(5) round to ZERO ticks -- the busy
// spin moy_flush's feed loop comments about.
#define H_TICK_US 10000

int64_t h_now(void);
void h_set_clock(int64_t us);

// Burn `us` of CPU in the RUNNING context. Fires any completion event that
// falls inside the span (an interrupt does not need the CPU to be idle) but
// never switches contexts -- a task keeps the core until it blocks.
void h_advance(int64_t us);

// The running context BLOCKS for `us` -- what a board hook that waits on
// hardware does. Other contexts run while it is out.
void h_block_us(int64_t us);

// Give the CPU up without blocking (the switch-on-give modelling below).
void h_yield(void);

// The gap between a completion ISR and the feeder actually being resumed on
// core 0. Zero by default; a scenario raises it to prove last_flush_us comes
// from the ISR's own stamp rather than from the clock when the feeder wakes.
void h_set_isr_latency(int64_t us);

// The scenario's virtual-time budget. A bounded wait whose bound is deleted
// does not hang the harness: it runs the clock past this and FAILS.
void h_set_watchdog(int64_t us_from_now);

// -- completion events (the DMA done-ISR) ------------------------------------

typedef void (*h_ev_fn)(void *arg);

void h_at(int64_t when_us, h_ev_fn fn, void *arg);
int h_pending_events(void);

// -- FreeRTOS modelling knobs ------------------------------------------------

// After a give of `s`, hand the CPU straight to the woken context. The two
// parties really do run on different cores, so the VM can observe the state
// between the feeder's give and its very next store; this is how a scenario
// pins "the feeder clears frame_busy LAST and THEN gives done_sem". Off by
// default (a give then just marks the waiter runnable).
void h_switch_on_give(struct h_sem *s);

// Injection for the lifecycle failure paths.
void h_fail_malloc_at(int nth);       // the nth heap_caps_malloc returns NULL
void h_fail_sem_at(int nth);          // the nth xSemaphoreCreateBinary is NULL
void h_fail_task_create(bool on);

// -- the allocation registry -------------------------------------------------
//
// heap_caps_free does NOT hand the block back to libc: it POISONS it and
// remembers it. A failed moy_flush_stop() must leave the bounce slots
// allocated, because the feeder it gave up on still writes them -- and the way
// to catch a regression there is for the write to land somewhere the harness
// can see, not in reused libc memory that happens not to crash.
bool h_is_freed(const void *p);
int h_live_allocs(void);
size_t h_alloc_size(const void *p);
uint32_t h_alloc_caps(const void *p);

// -- the GIL ----------------------------------------------------------------

int h_gil_exits(void);
int h_gil_depth(void);

// -- MicroPython object peeking ---------------------------------------------

struct h_mp_obj;
int h_tuple_len(const struct h_mp_obj *t);
long long h_tuple_int(const struct h_mp_obj *t, int i);
bool h_tuple_is_unsigned(const struct h_mp_obj *t, int i);

// -- scenario plumbing -------------------------------------------------------

void h_harness_init(const char *scenario);
int h_task_creates(void);

// -- failure -----------------------------------------------------------------

extern const char *h_scenario;
void h_fail(const char *fmt, ...) __attribute__((noreturn))
    __attribute__((format(printf, 1, 2)));

#define CHECK(cond)                                                           \
    do {                                                                      \
        if (!(cond)) {                                                        \
            h_fail("%s:%d: CHECK(%s)", __FILE__, __LINE__, #cond);            \
        }                                                                     \
    } while (0)

#define CHECK_EQ(actual, expected)                                            \
    do {                                                                      \
        long long h_a_ = (long long)(actual);                                 \
        long long h_e_ = (long long)(expected);                               \
        if (h_a_ != h_e_) {                                                   \
            h_fail("%s:%d: %s == %s -- got %lld, want %lld", __FILE__,         \
                   __LINE__, #actual, #expected, h_a_, h_e_);                 \
        }                                                                     \
    } while (0)

#endif // MOY_FLUSH_HARNESS_H
