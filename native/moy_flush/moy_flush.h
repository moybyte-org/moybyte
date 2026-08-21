// moy_flush: the SHARED banded-flush engine under the two S3 panel drivers
// (the T-Deck's moy_lcd/ST7789-over-esp_lcd and the Guition's moy_axs/QSPI-
// over-raw-spi_master). It is a support library, not a MicroPython module --
// it registers nothing; each board's panel module links it and keeps its own
// verb table. Boards without a flush at all deny it in board.toml (the P4
// scans a PSRAM framebuffer continuously and has no bands to feed).
//
// WHY THIS EXISTS (2026-08-21). moy_axs deliberately did NOT share moy_lcd's
// C body at bring-up (docs/board_ports_2026-08.md Phase C: the two panels
// disagree about what a "continuation" is -- per-band CS cycles vs one CS for
// the whole frame -- and that difference reaches every transaction). That
// verdict still stands for the TRANSPORT. But when the T-Deck's flush moved
// onto the Guition's core-0 feeder design (d9aa73e), the concurrency half of
// the two files became copies BY DESIGN -- moy_lcd's own comments said
// "handoff protocol, copied from moy_axs verbatim" -- and a protocol with
// documented races that lives twice is a protocol whose next fix lands once.
// So the engine is ONE body here, and what stays per-board is exactly what
// Phase C said could not share: the transport, the window arming, the band
// synthesis (plain memcpy vs the Guition's rotate/fold), and each board's
// bus-sharing rules (the T-Deck's sd_guard).
//
// THE SPLIT. The engine owns the frame STATE MACHINE, the FEEDER and the
// BOUNCE SLOTS:
//
//   * kick (VM side): latch the finished frame's pacing diag, set up the band
//     bookkeeping, hand the frame to the feeder. Returns in microseconds.
//   * the CORE-0 FEEDER task: one whole flush per handoff -- frame_begin,
//     then synthesize+queue every band as its bounce slot frees (sleeping on
//     the done-ISR's task notify), then the tail wait, then frame_end.
//   * drain (VM side): wait the feeder's frame out, GIL released.
//   * the two internal-SRAM bounce slots, allocated once at start(): the
//     engine is what PACES them (band k may reuse slot k % slots once
//     done >= k-(slots-1)), so it is what hands the verified-free slot to the
//     board rather than trusting the board to recompute the same index.
//   * the pacing meters the PUMP diag line prints (pump/idle/gaps/feed/
//     blocked) and the stats()/pump_stats() tuples both boards export.
//
// The BOARD owns, via three hooks (all run on the FEEDER task, so none may
// raise -- return an esp_err_t and the engine latches it):
//
//   frame_begin()                arm the panel for this frame's write, and
//                                reset whatever per-frame state the board
//                                keeps. moy_lcd: CASET/RASET over esp_lcd
//                                (whose tx_param also RECYCLES every stale
//                                in-flight transaction -- see the reset-order
//                                invariant below). moy_axs: acquire the bus,
//                                arm the window, ship the QSPI pixel header.
//   queue_band(slot,src,k,y,rows,last)
//                                synthesize band k INTO `slot` -- which the
//                                engine has already verified is free, and
//                                whose idle gap it has already accounted --
//                                and queue it on the board's transport.
//                                `src` is the frame's framebuffer, `y` its
//                                row offset within the shipped rect, `last`
//                                is for the Guition's CS_KEEP_ACTIVE chain,
//                                and `k == 0` is the T-Deck's RAMWR band.
//   frame_end(ok)                release what frame_begin took. Runs on EVERY
//                                handoff, including a failed frame_begin --
//                                guard your own resources (moy_axs checks its
//                                own bus-held flag).
//
// Band GEOMETRY stays with the board: band_rows/bounce_slots/band_bytes cross
// in the ops struct, each frame's row count is a kick() argument (the
// Guition's game window ships a sub-rect, and a sub-rect's WIDTH never
// reaches the engine at all -- only queue_band knows how many bytes a row
// is), and each board keeps its own one-DMA-chunk _Static_assert next to its
// own BAND_BYTES.
//
// THE HANDOFF PROTOCOL (shipped on the Guition 2026-08-19, ported to the
// T-Deck 2026-08-21; every clause was a race once):
//   * kick may only run with the feeder IDLE -- its callers drain first.
//   * the feeder clears frame_busy LAST and then gives done_sem.
//   * kick clears a stale done credit (a give drain's fast path never took)
//     before every handoff, so the binary semaphore never carries a previous
//     frame's give into the next.
//   * one compiler barrier on each side of both semaphores: the hardware
//     needs nothing (this state lives in internal SRAM, which the S3 does not
//     cache, and each core's stores are in order), but GCC may legally cache
//     a file-scope value across an external call -- i.e. read a pre-handoff
//     copy after the wait.
//
// THE RESET-ORDER INVARIANT: done/target restart at 0 INSIDE the feeder,
// AFTER frame_begin -- never at kick. On the T-Deck this is load-bearing for
// timed-out-flush recovery: esp_lcd's tx_param recycles ALL in-flight color
// transactions before its command, so by the time the arm returns every stale
// band's completion ISR has run and the counters restart clean. On the
// Guition a timed-out band's ISR never fires at all (raw spi_master, see its
// queue-slot-leak note), so the placement is merely harmless there -- and it
// is strictly BETTER than the reset-at-kick that file shipped with, because
// it shrinks the window in which a stale completion can credit the new
// frame's pacing. One placement serves both, and it is the one that can never
// double-count.
//
// The ISR half is moy_flush_band_done_from_isr() below: STATIC INLINE so each
// board's completion callback keeps its own placement (moy_axs's post_cb is
// IRAM_ATTR; an out-of-line flash-resident helper would quietly break that).
// The yield must happen inside the callback -- esp_lcd's spi post_cb IGNORES
// the on_color_trans_done bool return, so returning true wakes nobody until
// the next FreeRTOS tick.

#ifndef MOYBYTE_MOY_FLUSH_H
#define MOYBYTE_MOY_FLUSH_H

#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "esp_err.h"
#include "esp_timer.h"

#include "py/obj.h"

// portYIELD_FROM_ISR's ARG form expands this IDF trace hook, whose empty
// default lives at the tail of FreeRTOS.h -- which a MicroPython usermod TU's
// include order (py/mpstate.h pulls freertos headers first) never reaches.
// Same empty default, defensively. Both panel modules used to carry this
// guard themselves; it lives with the ISR helper now.
#ifndef traceISR_EXIT_TO_SCHEDULER
#define traceISR_EXIT_TO_SCHEDULER()
#endif

// A full frame is ~15-20 ms on either board; this is a bug fence, not a knob.
#define MOY_FLUSH_TIMEOUT_US 500000

// THE CORE-0 FEEDER's placement, shared design on both S3 boards:
// MicroPython's VM task is pinned to core 1 (mphalport.h MP_TASK_COREID), so
// the feeder runs on core 0 -- shared with the mostly-idle WiFi/BT stacks,
// priority BELOW both (WiFi 23, lwIP 18; the bounce slots absorb a radio
// burst) -- woken per band by the done-ISR, which each board also pins to
// core 0 via its bus config's isr_cpu_id.
#define MOY_FLUSH_FEED_CORE   0
#define MOY_FLUSH_FEED_PRIO   12
#define MOY_FLUSH_FEED_STACK  4096

// Both boards run two slots (three was tried and reverted on the T-Deck --
// see #66's A/B). The array is fixed-size so the engine's state is one static
// allocation; raising this is a memory decision, not a code change.
#define MOY_FLUSH_MAX_SLOTS   4

// The handoff's compiler barrier (see THE HANDOFF PROTOCOL above).
#define MOY_FLUSH_HANDOFF_BARRIER() __asm__ volatile ("" ::: "memory")

// The board's half of the engine. All three hooks run on the FEEDER task --
// no MP context, so none may raise (an nlr raise there would abort): return
// the error and the engine latches it into tx_err for the next VM-side
// kick/show to report.
typedef struct {
    esp_err_t (*frame_begin)(void);
    esp_err_t (*queue_band)(uint8_t *slot, const uint8_t *src, int k, int y,
                            int rows, bool last);
    void (*frame_end)(bool ok);
    const char *task_name;   // shows in task lists; also what the spike tests grep
    int band_rows;
    int band_bytes;          // one bounce slot, at the board's full band width
    int bounce_slots;
} moy_flush_ops_t;

// The engine's state, exported as ONE struct rather than a wall of accessors:
// this is shared code between two in-tree consumers, not an ABI (the repo
// declines those -- docs/board_ports_2026-08.md), and the boards' own verbs
// read a handful of fields directly (pending() reads frame_busy, moy_axs's
// fold_fence reads bnc_next/bnc_total and its retrieve loop reads done).
// Volatile exactly where the feeder and the VM core both look: these live in
// internal SRAM, which the S3 does not cache, so plain in-order stores are
// visible cross-core -- volatile (plus the handoff barrier) only stops GCC
// from caching a pre-handoff read.
typedef struct {
    // What the panel has ACCEPTED (written by the done-ISR; single 32-bit
    // stores, so readers need no lock; done_us is deliberately the LOW 32
    // bits of the timer -- a 64-bit store would tear against a reader and the
    // only use is a wrap-safe uint32 subtraction).
    volatile uint32_t done;
    volatile uint32_t done_us;
    volatile uint32_t flushes;
    volatile uint32_t last_flush_us;   // kick -> LAST COMPLETION, ISR-stamped
    // The in-flight frame. bnc_next is what the feeder has FED, target what
    // the transport has been HANDED, done what the panel has ACCEPTED; band k
    // may reuse bounce slot k % slots once done >= k-(slots-1).
    volatile int bnc_total;
    volatile int bnc_next;
    const uint8_t *bnc_src;            // the FRONT fb (immutable while it ships)
    int frame_rows;                    // rows THIS frame ships (kick argument)
    uint32_t target;
    bool in_pump;                      // reentrancy guard (feeder-only; cheap)
    volatile esp_err_t tx_err;         // latched; reported by the next kick/show
    int64_t flush_t0;
    volatile uint32_t timeouts;
    volatile uint32_t tx_errs;
    // The internal DMA-capable bounce slots (the panel DMA never reads PSRAM
    // -- #66, and the reason this whole band machinery exists).
    uint8_t *bounce[MOY_FLUSH_MAX_SLOTS];
    // The feeder + handoff.
    TaskHandle_t volatile task;
    SemaphoreHandle_t kick_sem;        // MP -> feeder: a prepared frame waits
    SemaphoreHandle_t done_sem;        // feeder -> MP: that frame finished
    volatile bool frame_busy;          // the feeder owns the transport + bookkeeping
    volatile bool frame_clean;         // the finished frame's verdict
    volatile bool task_exit;
    // Feed pacing for the frame in flight, latched into the *_last pair at
    // the next kick (the frame is complete by then -- drain ran first). This
    // is the PUMP diag line's data; it exists because "the flush is slow" and
    // "the flush is fed late" look identical from outside: idle_us ~ 0 means
    // the ceiling is real transfer time and a faster feeder buys nothing.
    volatile uint32_t pump_us;         // feeder CPU us in the pump this frame
    volatile uint32_t idle_us;         // us the SPI sat starved for a band
    volatile uint32_t idle_n;          // bands fed that late
    volatile int32_t feed_us;          // frame start -> last band queued
    uint32_t kick_us;
    uint32_t block_us;                 // VM CPU us blocked in drain this frame
    uint32_t pump_last_us;
    uint32_t idle_last_us;
    uint32_t idle_last_n;
    int32_t feed_last_us;
    uint32_t block_last_us;
} moy_flush_state_t;

extern moy_flush_state_t moy_flush;

// Allocate the bounce slots and create the semaphores + the feeder task
// (idempotent; all three survive a soft reset by design). False = out of
// memory, with whatever it had already taken handed back -- the caller must
// then tear its whole init down, because there is no feederless flush path to
// fall back to. Call it where the board allocates its other DMA memory: the
// task simply waits on kick_sem until the board's first kick.
bool moy_flush_start(const moy_flush_ops_t *ops);

// Stop the feeder and free the bounce slots before the transport goes away
// under them (deinit). The caller drains first. The semaphores are kept, as
// they are across a soft reset.
void moy_flush_stop(void);

// Clear the band bookkeeping (soft reset / free-all): a leftover bnc_total
// would make the next frame re-feed a dead frame's bands into a window that
// no longer describes them. Leaves frame_clean alone.
void moy_flush_reset(void);

// VM side: latch the last frame's diag, set up the bookkeeping and hand
// framebuffer `src` (frame_rows rows of it) to the feeder. The caller must
// have drained first -- that is what makes the diag latch and the counter
// writes race-free here -- and must have taken tx_err (moy_flush_take_err)
// so the previous frame's latched error was raised where it can be.
void moy_flush_kick(const uint8_t *src, int frame_rows);

// VM side: wait the feeder's frame out. Fast path (frame already finished,
// the ordinary overlap cadence) is one volatile read; otherwise the GIL is
// released across a semaphore wait. The feeder's own deadline guarantees the
// wait terminates; the bounded loop is insurance against a wedged feeder,
// sized never to fire. False = the frame did not go out cleanly (a timeout's
// bands may still be in flight -- the next frame's frame_begin recovers).
bool moy_flush_drain(void);

// Read-and-clear the latched transport error (the feeder runs the SPI, so an
// error surfaces one frame late -- raise the finished frame's before handing
// the next one over).
esp_err_t moy_flush_take_err(void);

// The (flushes, last_flush_us) and (pump, idle, gaps, feed, bands, blocked,
// timeouts, errs) tuples both boards export verbatim. `bands` is the board's
// full-frame band count -- a REPORTED constant, passed in because the Guition
// windows some frames.
mp_obj_t moy_flush_stats_tuple(void);
mp_obj_t moy_flush_pump_stats_tuple(int bands);

// The done-ISR's shared half: count the band, stamp the clock, wake the
// feeder (a bounce slot just freed, or the tail completed). STATIC INLINE so
// it keeps the caller's IRAM placement; the yield happens here because
// esp_lcd's spi post_cb ignores the callback's return value.
static inline void moy_flush_band_done_from_isr(void) {
    moy_flush.done++;
    moy_flush.done_us = (uint32_t)esp_timer_get_time();
    if (moy_flush.task != NULL) {
        BaseType_t hp = pdFALSE;
        vTaskNotifyGiveFromISR((TaskHandle_t)moy_flush.task, &hp);
        portYIELD_FROM_ISR(hp);
    }
}

#endif // MOYBYTE_MOY_FLUSH_H
