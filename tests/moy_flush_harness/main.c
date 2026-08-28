// The scenarios: native/moy_flush/moy_flush.c, EXECUTED (#208 rank 1).
//
// Each scenario runs in its own PROCESS (the pytest driver passes one name per
// run), because the engine's state is one file-scope struct plus a file-scope
// ops pointer -- sharing that between scenarios would make every one of them
// depend on the order of the others, which is the sort of coupling this file
// exists to catch, not to have.
//
// The board double below is the third consumer of moy_flush_ops_t, after
// moy_lcd and moy_axs. It keeps their shape (the three hooks run on the feeder
// and return esp_err_t; band geometry crosses in the ops struct) and adds the
// two things a real board cannot: it RECORDS every band it was handed, and it
// FAILS LOUDLY on the two hazards the engine's pacing exists to prevent -- a
// bounce slot rewritten while its DMA is still reading, and any write into a
// slot that has been handed back to the heap.
//
// WHAT THIS FILE DOES NOT REACH, so nobody reads its silence as coverage:
//   * the two HANDOFF BARRIERS. They are `__asm__ volatile ("" ::: "memory")`
//     -- a compile-time fence with no runtime behaviour at all, so no
//     scenario can distinguish their presence. What CAN be pinned is the
//     store ORDER either side of them, and both directions are
//     (frame_busy_clears_before_the_give, kick_marks_busy_before_the_give).
//   * the pump's `in_pump` reentrancy guard: moy_flush_pump is static and its
//     only caller is the feed loop, so there is no path that re-enters it.
//   * the feeder loop's `xSemaphoreTake(kick_sem, portMAX_DELAY) != pdTRUE`
//     continue: an infinite wait cannot time out.
//   * everything about a real transport -- band CONTENT, CS chains, DMA
//     alignment. That half is each board's, by design (moy_flush.h's split).
//
// MUTATION-CHECKED (2026-08-28): 42 perturbations of moy_flush.c/.h, 40 red.
// The two survivors are equivalent mutants and both are noted at their sites
// below -- the pump's absurd-gap ceiling (unreachable while the `k > 0` guard
// stands) and its write-once feed_us guard (the pump cannot re-enter the
// branch inside one frame). Anything else that survives a perturbation here is
// a hole in these scenarios, not a clause that does not matter.

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "harness.h"
#include "moy_flush.h"

// ---------------------------------------------------------------------------
// The board double
// ---------------------------------------------------------------------------

#define BAND_ROWS   40
#define BAND_W      480                       // the Guition's landscape width
#define BAND_BYTES  (BAND_ROWS * BAND_W * 2)
#define PANEL_ROWS  320
#define FULL_BANDS  (PANEL_ROWS / BAND_ROWS)  // 8

typedef struct {
    int k, y, rows;
    bool last;
    const uint8_t *slot;
    uint32_t done_at;                 // moy_flush.done when this band was fed
    int64_t t_in, t_out;
} band_rec_t;

static struct {
    // configuration
    esp_err_t begin_err;
    int64_t begin_block_us;
    int64_t end_block_us;
    bool begin_recycles;              // esp_lcd's tx_param, which recycles every
                                      // in-flight color transaction before its
                                      // command -- the T-Deck's recovery path
    esp_err_t queue_err;
    int queue_err_at;
    int64_t synth_us;                 // CPU the band synthesis costs
    int64_t tx_us;                    // one band on the wire; <0 = no completion
    bool tx_inline;                   // the completion ISR lands before
                                      // queue_band returns -- an interrupt is
                                      // allowed to arrive at any instruction
    int slots;
    const uint8_t *expect_src;

    // observation
    int begin_calls, end_calls;
    uint32_t done_at_begin[8], target_at_begin[8], done_after_begin[8];
    int64_t t_after_begin;
    bool end_ok[8];
    band_rec_t bands[FULL_BANDS * 4];
    int nbands;
    bool slot_busy[MOY_FLUSH_MAX_SLOTS];
    int slot_band[MOY_FLUSH_MAX_SLOTS];
    int stale[FULL_BANDS * 4];
    int nstale;
    int64_t bus_free_at;
} B;

static uint8_t g_fb[PANEL_ROWS * BAND_W * 2];

static void bd_complete(void *arg) {
    int k = (int)(intptr_t)arg;
    // Until this instant the DMA was READING that slot. Anything that handed
    // it back to the heap in the meantime -- moy_flush_stop() after a frame
    // that was returned with the bus still live -- is caught here, which is
    // how this file proves the hazard and not merely the patch.
    const uint8_t *slot = B.bands[k].slot;
    if (slot != NULL && h_is_freed(slot)) {
        h_fail("band %d's DMA completed out of a bounce slot that had already "
               "been freed -- the frame was handed back with the bus live", k);
    }
    B.slot_busy[k % B.slots] = false;         // the DMA has let the slot go
    moy_flush_band_done_from_isr();
}

static esp_err_t bd_frame_begin(void) {
    int n = B.begin_calls < 8 ? B.begin_calls : 7;
    B.done_at_begin[n] = moy_flush.done;
    B.target_at_begin[n] = moy_flush.target;
    B.begin_calls++;
    if (B.begin_block_us) { h_block_us(B.begin_block_us); }
    if (B.begin_recycles) {
        for (int i = 0; i < B.nstale; i++) {
            int k = B.stale[i];
            B.slot_busy[k % B.slots] = false;
            moy_flush_band_done_from_isr();
        }
        B.nstale = 0;
    }
    B.done_after_begin[n] = moy_flush.done;
    if (B.begin_err != ESP_OK) { return B.begin_err; }
    B.bus_free_at = h_now();
    B.t_after_begin = h_now();
    return ESP_OK;
}

static esp_err_t bd_queue_band(uint8_t *slot, const uint8_t *src, int k, int y,
                               int rows, bool last) {
    CHECK(slot != NULL);
    CHECK_EQ(k, B.nbands);                       // fed in order, one at a time
    CHECK(slot == moy_flush.bounce[k % B.slots]);   // the engine picks the slot
    if (h_is_freed(slot)) {
        h_fail("band %d was synthesized into a bounce slot that has been freed",
               k);
    }
    CHECK(src == B.expect_src);
    if (B.slot_busy[k % B.slots]) {
        h_fail("band %d overwrote bounce slot %d while band %d was still "
               "reading it", k, k % B.slots, B.slot_band[k % B.slots]);
    }
    band_rec_t *r = &B.bands[B.nbands++];
    r->k = k;
    r->y = y;
    r->rows = rows;
    r->last = last;
    r->slot = slot;
    r->done_at = moy_flush.done;
    r->t_in = h_now();
    memset(slot, 0xA5, (size_t)rows * BAND_W * 2);
    h_advance(B.synth_us);
    r->t_out = h_now();
    if (B.queue_err != ESP_OK && k == B.queue_err_at) { return B.queue_err; }
    B.slot_busy[k % B.slots] = true;
    B.slot_band[k % B.slots] = k;
    if (B.tx_inline) {
        bd_complete((void *)(intptr_t)k);
    } else if (B.tx_us >= 0) {
        int64_t start = h_now() > B.bus_free_at ? h_now() : B.bus_free_at;
        B.bus_free_at = start + B.tx_us;         // one transport, bands serial
        h_at(B.bus_free_at, bd_complete, (void *)(intptr_t)k);
    } else {
        B.stale[B.nstale++] = k;                 // in flight, never completing
    }
    return ESP_OK;
}

static void bd_frame_end(bool ok) {
    if (B.end_calls < 8) { B.end_ok[B.end_calls] = ok; }
    B.end_calls++;
    if (B.end_block_us) { h_block_us(B.end_block_us); }
}

static const moy_flush_ops_t OPS2 = {
    bd_frame_begin, bd_queue_band, bd_frame_end, "moyflush_feed",
    BAND_ROWS, BAND_BYTES, 2,
};

static const moy_flush_ops_t OPS1 = {
    bd_frame_begin, bd_queue_band, bd_frame_end, "moyflush_feed",
    BAND_ROWS, BAND_BYTES, 1,
};

static void bd_defaults(const moy_flush_ops_t *ops) {
    memset(&B, 0, sizeof B);
    B.slots = ops->bounce_slots;
    B.synth_us = 100;
    B.tx_us = 500;
    B.queue_err_at = -1;
    B.expect_src = g_fb;
}

static void bd_start(const moy_flush_ops_t *ops) {
    bd_defaults(ops);
    CHECK(moy_flush_start(ops));
}

static void bd_clear_bands(void) {
    B.nbands = 0;
    B.begin_calls = 0;
    B.end_calls = 0;
}

static bool run_frame(int rows) {
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, rows);
    return moy_flush_drain();
}

// The band table, printed with any failure that reaches for it.
static void dump_bands(void) {
    for (int i = 0; i < B.nbands; i++) {
        band_rec_t *r = &B.bands[i];
        printf("  band %d y=%d rows=%d last=%d done_at=%u in=%lld out=%lld\n",
               r->k, r->y, r->rows, (int)r->last, r->done_at,
               (long long)r->t_in, (long long)r->t_out);
    }
    fflush(stdout);
}

// ---------------------------------------------------------------------------
// The feed
// ---------------------------------------------------------------------------

static void sc_normal_frame(void) {
    bd_start(&OPS2);
    CHECK(run_frame(PANEL_ROWS));

    CHECK_EQ(B.begin_calls, 1);
    CHECK_EQ(B.end_calls, 1);
    CHECK_EQ(B.end_ok[0], true);
    if (B.nbands != FULL_BANDS) { dump_bands(); }
    CHECK_EQ(B.nbands, FULL_BANDS);
    for (int k = 0; k < FULL_BANDS; k++) {
        CHECK_EQ(B.bands[k].k, k);
        CHECK_EQ(B.bands[k].y, k * BAND_ROWS);
        CHECK_EQ(B.bands[k].rows, BAND_ROWS);
        CHECK_EQ(B.bands[k].last, k == FULL_BANDS - 1);
        CHECK(B.bands[k].slot == moy_flush.bounce[k % 2]);
    }
    CHECK_EQ(moy_flush.flushes, 1);
    CHECK_EQ(moy_flush.timeouts, 0);
    CHECK_EQ(moy_flush.tx_errs, 0);
    CHECK_EQ(moy_flush.frame_clean, true);
    CHECK_EQ(moy_flush.frame_busy, false);
    CHECK_EQ(moy_flush.done, FULL_BANDS);
    CHECK_EQ(moy_flush.target, FULL_BANDS);
    // The bookkeeping is handed back with the frame: a leftover bnc_total would
    // re-feed a dead frame's bands into a window that no longer describes them.
    CHECK_EQ(moy_flush.bnc_total, 0);
    CHECK_EQ(moy_flush.bnc_next, 0);
    CHECK(moy_flush.bnc_src == NULL);
    CHECK_EQ(moy_flush_take_err(), ESP_OK);
    CHECK_EQ(h_pending_events(), 0);
}

static void sc_partial_last_band(void) {
    bd_start(&OPS2);
    CHECK(run_frame(250));
    CHECK_EQ(B.nbands, 7);                        // ceil(250 / 40)
    for (int k = 0; k < 6; k++) { CHECK_EQ(B.bands[k].rows, BAND_ROWS); }
    CHECK_EQ(B.bands[6].y, 240);
    CHECK_EQ(B.bands[6].rows, 10);
    CHECK_EQ(B.bands[6].last, true);
    CHECK_EQ(moy_flush.flushes, 1);
}

static void sc_band_count_rounds_up(void) {
    bd_start(&OPS2);

    CHECK(run_frame(1));
    CHECK_EQ(B.nbands, 1);
    CHECK_EQ(B.bands[0].rows, 1);
    CHECK_EQ(B.bands[0].last, true);

    bd_clear_bands();
    CHECK(run_frame(BAND_ROWS));
    CHECK_EQ(B.nbands, 1);

    bd_clear_bands();
    CHECK(run_frame(BAND_ROWS + 1));
    CHECK_EQ(B.nbands, 2);
    CHECK_EQ(B.bands[1].rows, 1);

    // A zero-row frame is a NO-BAND frame, not a divide or a hang: the feed
    // loop and the tail wait both fall straight through and the frame is clean.
    bd_clear_bands();
    CHECK(run_frame(0));
    CHECK_EQ(B.nbands, 0);
    CHECK_EQ(moy_flush.frame_clean, true);
    CHECK_EQ(moy_flush.flushes, 4);
}

static void sc_slot_pacing_two_slots(void) {
    bd_start(&OPS2);
    B.synth_us = 0;
    B.tx_us = 2000;                                // the wire is the bottleneck
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, FULL_BANDS);
    int paced = 0;
    for (int k = 0; k < FULL_BANDS; k++) {
        // band k may reuse slot k % slots once done >= k - (slots - 1)
        if ((int32_t)B.bands[k].done_at < k - 1) {
            dump_bands();
            h_fail("band %d was fed at done=%u, before slot %d was free", k,
                   B.bands[k].done_at, k % 2);
        }
        if ((int32_t)B.bands[k].done_at == k - 1) { paced++; }
    }
    // ...and the predicate is LIVE: most of this frame waited on it.
    if (paced < FULL_BANDS - 2) {
        dump_bands();
        h_fail("only %d of %d bands were actually paced by a completion", paced,
               FULL_BANDS);
    }
}

static void sc_slot_pacing_one_slot(void) {
    // One slot: band k may not be fed until band k-1 has COMPLETED, so the
    // feeder is resumed at the exact microsecond of each completion and every
    // gap it measures is zero. That is what the pump's `gap > 0` guard is for
    // -- a zero-length gap is not a starved transport, and counting it would
    // put a non-zero `gaps` on the PUMP line of a perfectly fed frame.
    bd_start(&OPS1);
    B.synth_us = 0;
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, FULL_BANDS);
    for (int k = 0; k < FULL_BANDS; k++) { CHECK_EQ(B.bands[k].done_at, k); }
    CHECK_EQ(moy_flush.idle_n, 0);
    CHECK_EQ(moy_flush.idle_us, 0);
}

static void sc_reset_order(void) {
    // THE RESET-ORDER INVARIANT: done/target restart INSIDE the feeder, AFTER
    // frame_begin -- never at kick.
    bd_start(&OPS2);
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.done_at_begin[0], 0);
    bd_clear_bands();
    B.begin_calls = 1;                     // keep writing into slot [1]
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.done_at_begin[1], FULL_BANDS);
    CHECK_EQ(B.target_at_begin[1], FULL_BANDS);
    CHECK_EQ(moy_flush.done, FULL_BANDS);
}

static void sc_stale_completions_at_the_arm(void) {
    // The recovery the invariant is placed for. Frame 1 times out with two
    // bands still in flight; frame 2's arm recycles them (esp_lcd's tx_param
    // does exactly this), so their ISRs run BEFORE the counters restart and
    // cannot credit the new frame's pacing.
    bd_start(&OPS2);
    B.tx_us = -1;                                  // no completion ever comes
    B.begin_recycles = true;
    CHECK(!run_frame(PANEL_ROWS));
    CHECK_EQ(moy_flush.timeouts, 1);
    CHECK_EQ(B.nbands, 2);                         // both slots, then starved
    CHECK_EQ(B.nstale, 2);

    bd_clear_bands();
    B.begin_calls = 1;
    B.tx_us = 500;
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.done_after_begin[1], 2);            // the stale ISRs did run...
    CHECK_EQ(B.nbands, FULL_BANDS);                // ...and credited nothing
    CHECK_EQ(moy_flush.done, FULL_BANDS);
    CHECK_EQ(moy_flush.frame_clean, true);
    CHECK_EQ(moy_flush.timeouts, 1);
}

// ---------------------------------------------------------------------------
// The three bounded waits
// ---------------------------------------------------------------------------

static void sc_feed_timeout(void) {
    bd_start(&OPS2);
    B.tx_us = -1;
    int64_t t0 = h_now();
    CHECK(!run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, 2);                         // the two slots, then starved
    CHECK_EQ(moy_flush.timeouts, 1);
    CHECK_EQ(moy_flush.tx_errs, 0);
    CHECK_EQ(moy_flush.flushes, 0);
    CHECK_EQ(moy_flush.frame_clean, false);
    CHECK_EQ(B.end_calls, 1);
    CHECK_EQ(B.end_ok[0], false);
    CHECK_EQ(moy_flush.frame_busy, false);
    if (h_now() - t0 < MOY_FLUSH_TIMEOUT_US) {
        h_fail("the feed gave up after %lld us, before its own deadline",
               (long long)(h_now() - t0));
    }
}

static void sc_tail_timeout(void) {
    // Everything QUEUED must finish before the frame may be handed back -- the
    // promise the T-Deck's SD fence stands on. One band, so the feed loop is
    // done immediately and only the tail wait is left.
    bd_start(&OPS2);
    B.tx_us = -1;
    int64_t t0 = h_now();
    CHECK(!run_frame(BAND_ROWS));
    CHECK_EQ(B.nbands, 1);
    CHECK_EQ(moy_flush.target, 1);
    CHECK_EQ(moy_flush.done, 0);
    CHECK_EQ(moy_flush.timeouts, 1);
    CHECK_EQ(moy_flush.flushes, 0);
    CHECK_EQ(moy_flush.frame_clean, false);
    CHECK_EQ(B.end_ok[0], false);
    if (h_now() - t0 < MOY_FLUSH_TIMEOUT_US) {
        h_fail("the tail wait gave up after %lld us", (long long)(h_now() - t0));
    }
}

static void sc_drain_fast_path(void) {
    bd_start(&OPS2);
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    h_block_us(50000);                             // the frame finishes alone
    CHECK_EQ(moy_flush.frame_busy, false);
    CHECK(moy_flush_drain());
    // One volatile read: no GIL release, no semaphore, nothing billed.
    CHECK_EQ(h_gil_exits(), 0);
    CHECK_EQ(moy_flush.block_us, 0);
}

static void sc_drain_bounded_wait(void) {
    // drain's insurance loop: 4 waits of 300 ms. The feeder's own deadline is
    // what normally terminates it; this pins that a wedged feeder cannot hold
    // the VM forever.
    bd_start(&OPS2);
    B.begin_block_us = 2000000;
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK(!moy_flush_drain());
    CHECK_EQ(moy_flush.block_us, 1200000);
    CHECK_EQ(moy_flush.frame_busy, true);
    CHECK_EQ(h_gil_exits(), 1);
    CHECK_EQ(h_gil_depth(), 0);

    h_block_us(3000000);                           // let the wedged arm finish
    CHECK(moy_flush_drain());
    CHECK_EQ(moy_flush.flushes, 1);
    CHECK_EQ(B.nbands, FULL_BANDS);
}

static void sc_drain_reports_busy_during_frame_end(void) {
    // frame_clean is set BEFORE frame_end runs, so between them a frame is
    // both clean and not yet handed back. drain answers about the HANDOFF.
    bd_start(&OPS2);
    B.end_block_us = 2000000;
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK_EQ(moy_flush_drain(), false);
    CHECK_EQ(moy_flush.frame_clean, true);
    CHECK_EQ(moy_flush.frame_busy, true);
    CHECK_EQ(moy_flush.block_us, 1200000);

    h_block_us(2000000);
    CHECK(moy_flush_drain());
}

// ---------------------------------------------------------------------------
// The handoff
// ---------------------------------------------------------------------------

static void sc_kick_clears_a_stale_done_credit(void) {
    // A give that drain's fast path never took stays in the binary semaphore.
    // Carried into the next frame it spends one of drain's four waits, and the
    // frame that then needs all four is a frame drain gives up on.
    bd_start(&OPS2);
    B.synth_us = 0;
    B.tx_us = 0;
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    h_block_us(50000);
    CHECK(moy_flush_drain());                      // fast path: the give stands
    CHECK_EQ(h_gil_exits(), 0);

    bd_clear_bands();
    B.begin_block_us = 1000000;                    // needs the 4th wait
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK(moy_flush_drain());
    CHECK_EQ(moy_flush.block_us, 1000000);
    CHECK_EQ(moy_flush.flushes, 2);
}

static void sc_frame_busy_clears_before_the_give(void) {
    // The feeder clears frame_busy LAST and THEN gives done_sem. The two run
    // on different cores, so the VM really can be resumed between them: here
    // the give hands the CPU straight to the waiting drain.
    bd_start(&OPS2);
    h_switch_on_give(moy_flush.done_sem);
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK(moy_flush_drain());
    CHECK_EQ(moy_flush.frame_busy, false);
    // One wait, not two: a drain that woke to frame_busy still set would spend
    // another full 300 ms tick before it believed the frame was handed back.
    if (moy_flush.block_us >= 300000) {
        h_fail("drain blocked %u us -- it woke before the handoff was complete",
               moy_flush.block_us);
    }
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

static void sc_kick_marks_busy_before_the_give(void) {
    // The other end of the same handoff: kick sets frame_busy BEFORE it gives
    // kick_sem. The feeder is on the other core at a higher priority, so a
    // give it answers immediately can carry a whole frame -- and a frame_busy
    // stored after that has nothing left to clear it. Here the give hands the
    // CPU straight over, and the frame needs no waiting at all to finish.
    bd_start(&OPS2);
    h_switch_on_give(moy_flush.kick_sem);
    B.synth_us = 0;
    B.tx_inline = true;                            // nothing left to wait for
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK_EQ(B.nbands, FULL_BANDS);                // it really did all fit
    CHECK_EQ(moy_flush.frame_busy, false);         // ...and was handed back
    CHECK(moy_flush_drain());
    CHECK_EQ(moy_flush.block_us, 0);               // the fast path, not a wait
    CHECK_EQ(moy_flush.flushes, 1);
}

static void sc_frame_begin_failure(void) {
    bd_start(&OPS2);
    B.begin_err = ESP_ERR_INVALID_STATE;
    CHECK(!run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, 0);
    CHECK_EQ(moy_flush.tx_errs, 1);
    CHECK_EQ(moy_flush.timeouts, 0);               // a refused arm is not a hang
    CHECK_EQ(moy_flush.flushes, 0);
    // frame_end runs on EVERY handoff, including a failed arm -- both boards
    // guard their own resources inside it.
    CHECK_EQ(B.end_calls, 1);
    CHECK_EQ(B.end_ok[0], false);
    CHECK_EQ(moy_flush.frame_busy, false);
    CHECK_EQ(moy_flush_take_err(), ESP_ERR_INVALID_STATE);
    CHECK_EQ(moy_flush_take_err(), ESP_OK);        // read AND clear
}

static void sc_queue_error_stops_the_feed(void) {
    bd_start(&OPS2);
    B.queue_err = ESP_FAIL;
    B.queue_err_at = 3;
    CHECK(!run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, 4);                         // 0,1,2 queued; 3 refused
    CHECK_EQ(moy_flush.bnc_next, 0);               // handed back with the frame
    CHECK_EQ(moy_flush.tx_errs, 1);
    CHECK_EQ(moy_flush.timeouts, 0);
    CHECK_EQ(moy_flush.flushes, 0);
    CHECK_EQ(moy_flush.frame_clean, false);
    // THE FRAME IS NOT HANDED BACK UNTIL THE BUS IS QUIET, on this path as
    // much as any other (fixed 2026-08-28; the tail wait's condition used to
    // carry `tx_err == ESP_OK` and skipped itself here). Bands 0-2 were
    // accepted by the transport and every one of them has completed.
    CHECK_EQ(h_pending_events(), 0);
    CHECK_EQ(moy_flush.done, 3);
    CHECK_EQ(moy_flush.done, moy_flush.target);
    // frame_end still gets ok=true, and that is not the error being lost:
    // `ok` is the ARM-and-deadline verdict, the transport error travels in
    // tx_err and frame_clean, and both boards' frame_end ignores the argument
    // (moy_lcd `(void)ok`, moy_axs releases its bus either way).
    CHECK_EQ(B.end_calls, 1);
    CHECK_EQ(B.end_ok[0], true);
    CHECK_EQ(moy_flush_take_err(), ESP_FAIL);
}

static void sc_queue_error_still_bounds_the_tail_wait(void) {
    // The other half of the same fix: waiting for the bands the transport DID
    // take must not become a way to hang the feeder. A transport that refused
    // one band and then stopped completing the rest gives up at the frame's
    // own deadline -- the same absolute fence the feed loop uses -- and counts
    // itself, so `errs` and `timeouts` both name what happened.
    bd_start(&OPS2);
    B.tx_us = -1;                                  // nothing it took completes
    B.queue_err = ESP_FAIL;
    B.queue_err_at = 1;
    int64_t t0 = h_now();
    CHECK(!run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, 2);                         // band 0 taken, band 1 refused
    CHECK_EQ(moy_flush.target, 1);
    CHECK_EQ(moy_flush.done, 0);
    CHECK_EQ(moy_flush.tx_errs, 1);
    CHECK_EQ(moy_flush.timeouts, 1);
    CHECK_EQ(moy_flush.flushes, 0);
    CHECK_EQ(B.end_ok[0], false);
    if (h_now() - t0 < MOY_FLUSH_TIMEOUT_US) {
        h_fail("the tail wait gave up after %lld us on an error frame",
               (long long)(h_now() - t0));
    }
    if (h_now() - t0 > 2 * MOY_FLUSH_TIMEOUT_US) {
        h_fail("the error frame cost %lld us -- the two waits must share ONE "
               "absolute deadline", (long long)(h_now() - t0));
    }
}

static void sc_stop_after_a_queue_error_finds_a_quiet_bus(void) {
    // The hazard itself, in the shape a board meets it. Both deinits are
    // drain-then-stop, and stop() frees the bounce slots; a frame handed back
    // with a band still shipping means that free lands under a live DMA. On
    // the T-Deck the same window is moy_lcd_sd_guard(on), which drains and
    // then opens an sdspi session on the shared SPI host -- the documented
    // hang, which has no panic and no message.
    // Deliberately NO "is the bus quiet" assertion before the stop: this
    // scenario is here to be caught by the MECHANISM. Under the old tail wait
    // it fails inside bd_complete -- "band 2's DMA completed out of a bounce
    // slot that had already been freed" -- which is the bug, not a number
    // about the bug.
    bd_start(&OPS2);
    B.queue_err = ESP_FAIL;
    B.queue_err_at = 3;
    // The band has to still be shipping when stop() frees the slots, so it
    // must outlast stop's own handshake -- an idle feeder acknowledges inside
    // one millisecond. At the default 500 us the last band lands DURING that
    // handshake and the hazard hides, which is exactly how a race this shape
    // stays invisible on a bench.
    B.tx_us = 20000;
    CHECK(!run_frame(PANEL_ROWS));

    CHECK(moy_flush_stop());
    CHECK_EQ(h_live_allocs(), 0);
    h_block_us(50000);                             // anything still owed lands
    CHECK_EQ(moy_flush.done, moy_flush.target);
}

static void sc_kick_clears_the_latched_error(void) {
    // The latched error has done its jobs by the next kick (it disarmed the
    // pump, it stopped the feed, it was counted, the caller raised it). Left
    // set it would keep the pump disarmed -- i.e. every later frame feeds
    // nothing, silently.
    bd_start(&OPS2);
    B.queue_err = ESP_FAIL;
    B.queue_err_at = 3;
    CHECK(!run_frame(PANEL_ROWS));
    // No settling sleep here, deliberately, and no "is the bus quiet" assert
    // either: the error frame already waited its own bands out, so the very
    // next kick reuses bounce slot 0 immediately and the MECHANISM is what
    // must catch a regression. Under the old tail wait this fails inside
    // bd_queue_band -- "band 0 overwrote bounce slot 0 while band 2 was still
    // reading it". The scenario used to sleep 50 ms first, which was the bug
    // wearing a workaround.
    bd_clear_bands();
    B.queue_err = ESP_OK;
    B.queue_err_at = -1;
    // Deliberately WITHOUT moy_flush_take_err() first: the clear under test is
    // kick's own.
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, FULL_BANDS);
    CHECK_EQ(moy_flush.flushes, 1);
    CHECK_EQ(moy_flush.tx_errs, 1);
}

// ---------------------------------------------------------------------------
// The meters
// ---------------------------------------------------------------------------

static void sc_idle_gap_accounting(void) {
    // The pacing number. A gap is charged when the pump is about to feed band
    // k>0 and everything already queued has ALREADY completed -- i.e. the
    // transport has been sitting starved since that last completion.
    bd_start(&OPS2);
    B.synth_us = 0;
    B.tx_us = 100;
    h_set_isr_latency(1000);                       // the feeder is woken late
    CHECK(run_frame(PANEL_ROWS));
    int64_t t0 = B.t_after_begin;
    CHECK_EQ(B.nbands, FULL_BANDS);
    // Three times the feeder arrived 900 us after the transport had gone
    // quiet: two bands complete (100 us apart) while it is asleep, and it is
    // resumed 1000 us after the FIRST of them.
    CHECK_EQ(moy_flush.idle_n, 3);
    CHECK_EQ(moy_flush.idle_us, 2700);
    CHECK_EQ(moy_flush.feed_us, 3300);
    // kick -> LAST COMPLETION, from the ISR's own stamp: the 1000 us of
    // scheduling latency after it is not transfer time and must not be billed
    // (the frame's last band completes at 3500 and the feeder sees it at 4400).
    CHECK_EQ(moy_flush.last_flush_us, 3500);
    CHECK_EQ(moy_flush.kick_us, (uint32_t)t0);

    // A SECOND frame, identical -- and the reason it is here is band 0. Band 0
    // follows a drained bus BY DESIGN and must never be charged a gap; the
    // only reason it looks like one is that done_us still holds the PREVIOUS
    // frame's last completion, a few hundred microseconds ago. Without the
    // `k > 0` guard this frame charges four gaps instead of three.
    bd_clear_bands();
    B.begin_calls = 1;
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, FULL_BANDS);
    CHECK_EQ(moy_flush.idle_n, 3);
    CHECK_EQ(moy_flush.idle_us, 2700);
    CHECK_EQ(moy_flush.feed_us, 3300);
    CHECK_EQ(moy_flush.last_flush_us, 3500);

    // NOT covered, and it cannot be from here: the probe's other half, the
    // `gap < 1000000u` ceiling on a torn or absurd read. With the `k > 0`
    // guard in place every gap this file can produce is bounded by the frame's
    // own 500 ms deadline, and the harness's clock is a plain int64 that never
    // tears. Deleting that ceiling is therefore an EQUIVALENT MUTANT here --
    // it is the second guard of a pair whose first guard is pinned above.
}

static void sc_last_flush_us_is_the_isr_stamp(void) {
    bd_start(&OPS2);
    B.synth_us = 0;
    B.tx_us = 500;
    h_set_isr_latency(5000);
    CHECK(run_frame(BAND_ROWS));                   // one band
    CHECK_EQ(B.nbands, 1);
    CHECK_EQ(moy_flush.last_flush_us, 500);
}

static void sc_last_flush_us_survives_a_32bit_wrap(void) {
    // done_us is deliberately the LOW 32 bits of the timer (a 64-bit store
    // would tear against a reader), and its only use is a wrap-safe uint32
    // subtraction. Two crossings: the sign bit, and the wrap itself.
    bd_start(&OPS2);
    B.synth_us = 0;
    B.tx_us = 500;

    // 2^31: the stamp's top bit goes up. Anything that treats these
    // microseconds as SIGNED loses the frame here and nowhere else.
    h_set_clock(0x80000000LL - 200);
    CHECK(run_frame(BAND_ROWS));
    CHECK(moy_flush.done_us >= 0x80000000u);
    CHECK_EQ(moy_flush.last_flush_us, 500);

    // 2^32: the stamp wraps to a value SMALLER than the frame's own start.
    bd_clear_bands();
    h_set_clock(0x100000000LL - 200);
    CHECK(run_frame(BAND_ROWS));
    CHECK(moy_flush.done_us < 500);
    CHECK_EQ(moy_flush.last_flush_us, 500);
}

static void sc_pump_and_feed_meters(void) {
    bd_start(&OPS2);
    B.synth_us = 250;
    CHECK(run_frame(PANEL_ROWS));
    // pump_us is the feeder's CPU inside the band feed, accumulated ACROSS the
    // frame's several pump calls -- not just the last one.
    CHECK_EQ(moy_flush.pump_us, FULL_BANDS * 250);
    // feed_us is frame start -> LAST BAND QUEUED.
    CHECK_EQ(moy_flush.feed_us,
             (int32_t)(B.bands[FULL_BANDS - 1].t_out - B.t_after_begin));
    CHECK(moy_flush.feed_us > 0);
    // Its `feed_us < 0` write-once guard is an EQUIVALENT MUTANT: the pump
    // returns at its top once `bnc_next >= bnc_total`, so the branch that
    // stamps feed_us cannot be reached twice inside one frame, and kick puts
    // it back to -1 before the next.
}

static void sc_meters_latch_at_the_next_kick(void) {
    bd_start(&OPS2);
    B.synth_us = 50;
    B.tx_us = 100;
    h_set_isr_latency(1000);                       // starve it, so idle/gaps
    CHECK(run_frame(PANEL_ROWS));                  // are not zero like pump

    // Nothing is latched until the next kick: pump_stats reports the last
    // FULLY-SHIPPED frame, and this one has only just landed.
    mp_obj_t t = moy_flush_pump_stats_tuple(42);
    CHECK_EQ(h_tuple_len(t), 9);
    CHECK_EQ(h_tuple_int(t, 0), 0);
    CHECK_EQ(h_tuple_int(t, 3), -1);
    // feed is the one signed entry: -1 means "this frame has not fed its last
    // band yet", and mp_obj_new_int_from_uint would report 4294967295.
    CHECK_EQ(h_tuple_is_unsigned(t, 3), false);

    uint32_t pump = moy_flush.pump_us;
    uint32_t idle = moy_flush.idle_us;
    uint32_t gaps = moy_flush.idle_n;
    int32_t feed = moy_flush.feed_us;
    uint32_t blocked = moy_flush.block_us;
    // The layout check below only has power if the six values differ.
    long long v[6] = { pump, idle, gaps, feed, 42, blocked };
    for (int i = 0; i < 6; i++) {
        for (int j = i + 1; j < 6; j++) {
            if (v[i] == v[j]) {
                h_fail("meters %d and %d are both %lld -- this scenario cannot "
                       "tell the tuple's entries apart", i, j, v[i]);
            }
        }
    }

    bd_clear_bands();
    moy_flush_kick(g_fb, PANEL_ROWS);
    CHECK(moy_flush_drain());

    t = moy_flush_pump_stats_tuple(42);
    CHECK_EQ(h_tuple_len(t), 9);
    CHECK_EQ(h_tuple_int(t, 0), pump);             // pump
    CHECK_EQ(h_tuple_int(t, 1), idle);             // idle
    CHECK_EQ(h_tuple_int(t, 2), gaps);             // gaps
    CHECK_EQ(h_tuple_int(t, 3), feed);             // feed
    CHECK_EQ(h_tuple_int(t, 4), 42);               // bands, the caller's own
    CHECK_EQ(h_tuple_int(t, 5), blocked);          // blocked
    // ...and the live counters restarted at the same kick.
    CHECK_EQ(moy_flush.pump_us, FULL_BANDS * 50);

    mp_obj_t s = moy_flush_stats_tuple();
    CHECK_EQ(h_tuple_len(s), 2);
    CHECK_EQ(h_tuple_int(s, 0), 2);                // flushes
    CHECK_EQ(h_tuple_int(s, 1), moy_flush.last_flush_us);
}

static void sc_failure_counters_are_positional(void) {
    // timeouts / errs / stopfails must all stay 0 on a healthy board, so the
    // only thing that can tell entry 6 from entry 8 is three DIFFERENT
    // non-zero values.
    bd_start(&OPS2);

    B.queue_err = ESP_FAIL;                        // errs = 2
    B.queue_err_at = 0;
    CHECK(!run_frame(PANEL_ROWS));
    bd_clear_bands();
    CHECK(!run_frame(PANEL_ROWS));
    h_block_us(50000);

    bd_clear_bands();
    B.queue_err = ESP_OK;
    B.queue_err_at = -1;
    B.tx_us = -1;                                  // timeouts = 1
    CHECK(!run_frame(PANEL_ROWS));

    bd_clear_bands();
    B.tx_us = 500;
    B.begin_block_us = 5000000;                    // stopfails = 3
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    h_yield();                                     // the feeder takes the frame
    for (int i = 0; i < 3; i++) { CHECK(!moy_flush_stop()); }

    mp_obj_t t = moy_flush_pump_stats_tuple(42);
    CHECK_EQ(h_tuple_int(t, 6), 1);                // timeouts
    CHECK_EQ(h_tuple_int(t, 7), 2);                // errs
    CHECK_EQ(h_tuple_int(t, 8), 3);                // stopfails
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

static void sc_start_validation(void) {
    bd_defaults(&OPS2);
    CHECK(!moy_flush_start(NULL));
    moy_flush_ops_t bad = OPS2;
    bad.bounce_slots = 0;
    CHECK(!moy_flush_start(&bad));
    bad.bounce_slots = MOY_FLUSH_MAX_SLOTS + 1;
    CHECK(!moy_flush_start(&bad));
    CHECK_EQ(h_live_allocs(), 0);
    CHECK_EQ(h_task_creates(), 0);
    CHECK(moy_flush.task == NULL);
}

static void sc_start_out_of_memory(void) {
    // Whatever it had already taken is handed back -- the caller must tear its
    // whole init down, because there is no feederless flush path to fall to.
    bd_defaults(&OPS2);
    h_fail_malloc_at(1);
    CHECK(!moy_flush_start(&OPS2));
    CHECK(moy_flush.bounce[0] == NULL);
    CHECK(moy_flush.bounce[1] == NULL);
    CHECK_EQ(h_live_allocs(), 0);
    CHECK_EQ(h_task_creates(), 0);
    CHECK(moy_flush.task == NULL);
}

static void sc_start_semaphore_failure(void) {
    bd_defaults(&OPS2);
    h_fail_sem_at(0);
    CHECK(!moy_flush_start(&OPS2));
    CHECK_EQ(h_live_allocs(), 0);
    CHECK_EQ(h_task_creates(), 0);
}

static void sc_start_task_failure(void) {
    bd_defaults(&OPS2);
    h_fail_task_create(true);
    CHECK(!moy_flush_start(&OPS2));
    CHECK_EQ(h_live_allocs(), 0);
    CHECK(moy_flush.task == NULL);
    CHECK(moy_flush.kick_sem != NULL);             // the semaphores are kept
    CHECK(moy_flush.done_sem != NULL);
}

static void sc_start_is_idempotent(void) {
    bd_start(&OPS2);
    uint8_t *b0 = moy_flush.bounce[0];
    uint8_t *b1 = moy_flush.bounce[1];
    TaskHandle_t task = (TaskHandle_t)moy_flush.task;
    CHECK(moy_flush_start(&OPS2));
    CHECK(moy_flush.bounce[0] == b0);
    CHECK(moy_flush.bounce[1] == b1);
    CHECK((TaskHandle_t)moy_flush.task == task);
    CHECK_EQ(h_task_creates(), 1);
    CHECK_EQ(h_live_allocs(), 2);
    // The slots are the board's band_bytes of DMA-capable INTERNAL memory --
    // the panel DMA never reads PSRAM, which is why the bands exist at all.
    CHECK_EQ(h_alloc_size(b0), BAND_BYTES);
    CHECK_EQ(h_alloc_caps(b0), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    CHECK(run_frame(PANEL_ROWS));
}

static void sc_stop_success(void) {
    bd_start(&OPS2);
    CHECK(run_frame(PANEL_ROWS));
    SemaphoreHandle_t kick_sem = moy_flush.kick_sem;
    SemaphoreHandle_t done_sem = moy_flush.done_sem;

    CHECK(moy_flush_stop());
    CHECK(moy_flush.task == NULL);
    CHECK(moy_flush.bounce[0] == NULL);
    CHECK(moy_flush.bounce[1] == NULL);
    CHECK_EQ(h_live_allocs(), 0);
    CHECK_EQ(moy_flush.stop_fails, 0);
    CHECK_EQ(moy_flush.bnc_total, 0);
    CHECK_EQ(moy_flush.frame_busy, false);
    // The semaphores are kept, as they are across a soft reset.
    CHECK(moy_flush.kick_sem == kick_sem);
    CHECK(moy_flush.done_sem == done_sem);

    bd_clear_bands();
    CHECK(moy_flush_start(&OPS2));
    CHECK_EQ(h_task_creates(), 2);
    CHECK(moy_flush.bounce[0] != NULL);
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(B.nbands, FULL_BANDS);
}

static void sc_stop_failure_keeps_the_bounce(void) {
    // GIVING UP FREES NOTHING. The feeder that did not acknowledge still
    // writes the bounce slots and its done-ISR still notifies its handle, so
    // handing either back is the one thing a failed stop may not do.
    bd_start(&OPS2);
    B.begin_block_us = 5000000;
    B.expect_src = g_fb;
    moy_flush_kick(g_fb, PANEL_ROWS);
    // Let the feeder actually TAKE the frame first. On the boards it is
    // already running by now (core 0, priority 12); here the handoff is only
    // a semaphore give, and a stop that beat the feeder to its own loop top
    // would be testing a clean stop, not a failed one.
    h_yield();
    CHECK_EQ(B.begin_calls, 1);

    int64_t t0 = h_now();
    CHECK(!moy_flush_stop());
    CHECK_EQ(h_now() - t0, MOY_FLUSH_STOP_TIMEOUT_MS * 1000);
    CHECK_EQ(moy_flush.stop_fails, 1);
    CHECK(moy_flush.bounce[0] != NULL);
    CHECK(moy_flush.bounce[1] != NULL);
    CHECK(!h_is_freed(moy_flush.bounce[0]));
    CHECK(!h_is_freed(moy_flush.bounce[1]));
    CHECK_EQ(h_live_allocs(), 2);
    CHECK(moy_flush.task != NULL);
    CHECK_EQ(moy_flush.task_exit, true);           // the latch stays ARMED
    CHECK_EQ(moy_flush.frame_busy, true);          // and the bookkeeping stands
    CHECK_EQ(moy_flush.bnc_total, FULL_BANDS);

    // start() refuses to re-arm over a zombie: publishing s_ops under a
    // running frame and then handing it the next kick would have it exit.
    CHECK(!moy_flush_start(&OPS2));

    // The task leaves at whatever loop iteration it reaches. Everything it
    // does on the way -- including synthesizing all eight bands into the
    // slots stop() did not free -- must still land in live memory.
    h_block_us(6000000);
    CHECK(moy_flush.task == NULL);
    CHECK_EQ(B.nbands, FULL_BANDS);

    CHECK(moy_flush_stop());                       // now it frees
    CHECK_EQ(h_live_allocs(), 0);
    CHECK_EQ(moy_flush.stop_fails, 1);
    CHECK(moy_flush_start(&OPS2));                 // and start re-arms again
}

static void sc_reset_leaves_frame_clean(void) {
    bd_start(&OPS2);
    CHECK(run_frame(PANEL_ROWS));
    CHECK_EQ(moy_flush.frame_clean, true);
    moy_flush.bnc_total = 5;
    moy_flush.bnc_next = 2;
    moy_flush.bnc_src = g_fb;
    moy_flush.frame_rows = 7;
    moy_flush.done = 3;
    moy_flush.target = 4;
    moy_flush.tx_err = ESP_FAIL;
    moy_flush.frame_busy = true;

    moy_flush_reset();

    CHECK_EQ(moy_flush.bnc_total, 0);
    CHECK_EQ(moy_flush.bnc_next, 0);
    CHECK(moy_flush.bnc_src == NULL);
    CHECK_EQ(moy_flush.frame_rows, 0);
    CHECK_EQ(moy_flush.done, 0);
    CHECK_EQ(moy_flush.target, 0);
    CHECK_EQ(moy_flush.tx_err, ESP_OK);
    CHECK_EQ(moy_flush.frame_busy, false);
    // Both boards' init calls reset() and THEN sets frame_clean itself; the
    // verdict of the last frame is not this function's to touch.
    CHECK_EQ(moy_flush.frame_clean, true);
}

static void sc_isr_without_a_feeder(void) {
    // ONE read of the handle: the feeder NULLs it as it leaves, so a
    // test-then-use would notify whatever the second read saw.
    bd_start(&OPS2);
    CHECK(run_frame(PANEL_ROWS));
    CHECK(moy_flush_stop());
    CHECK(moy_flush.task == NULL);
    uint32_t before = moy_flush.done;
    moy_flush_band_done_from_isr();
    CHECK_EQ(moy_flush.done, before + 1);
    CHECK_EQ(moy_flush.done_us, (uint32_t)h_now());
}

// ---------------------------------------------------------------------------

typedef struct {
    const char *name;
    void (*fn)(void);
} scenario_t;

static const scenario_t SCENARIOS[] = {
    { "normal_frame", sc_normal_frame },
    { "partial_last_band", sc_partial_last_band },
    { "band_count_rounds_up", sc_band_count_rounds_up },
    { "slot_pacing_two_slots", sc_slot_pacing_two_slots },
    { "slot_pacing_one_slot", sc_slot_pacing_one_slot },
    { "reset_order", sc_reset_order },
    { "stale_completions_at_the_arm", sc_stale_completions_at_the_arm },
    { "feed_timeout", sc_feed_timeout },
    { "tail_timeout", sc_tail_timeout },
    { "drain_fast_path", sc_drain_fast_path },
    { "drain_bounded_wait", sc_drain_bounded_wait },
    { "drain_reports_busy_during_frame_end",
      sc_drain_reports_busy_during_frame_end },
    { "kick_clears_a_stale_done_credit", sc_kick_clears_a_stale_done_credit },
    { "frame_busy_clears_before_the_give", sc_frame_busy_clears_before_the_give },
    { "kick_marks_busy_before_the_give", sc_kick_marks_busy_before_the_give },
    { "frame_begin_failure", sc_frame_begin_failure },
    { "queue_error_stops_the_feed", sc_queue_error_stops_the_feed },
    { "queue_error_still_bounds_the_tail_wait",
      sc_queue_error_still_bounds_the_tail_wait },
    { "stop_after_a_queue_error_finds_a_quiet_bus",
      sc_stop_after_a_queue_error_finds_a_quiet_bus },
    { "kick_clears_the_latched_error", sc_kick_clears_the_latched_error },
    { "idle_gap_accounting", sc_idle_gap_accounting },
    { "last_flush_us_is_the_isr_stamp", sc_last_flush_us_is_the_isr_stamp },
    { "last_flush_us_survives_a_32bit_wrap",
      sc_last_flush_us_survives_a_32bit_wrap },
    { "pump_and_feed_meters", sc_pump_and_feed_meters },
    { "meters_latch_at_the_next_kick", sc_meters_latch_at_the_next_kick },
    { "failure_counters_are_positional", sc_failure_counters_are_positional },
    { "start_validation", sc_start_validation },
    { "start_out_of_memory", sc_start_out_of_memory },
    { "start_semaphore_failure", sc_start_semaphore_failure },
    { "start_task_failure", sc_start_task_failure },
    { "start_is_idempotent", sc_start_is_idempotent },
    { "stop_success", sc_stop_success },
    { "stop_failure_keeps_the_bounce", sc_stop_failure_keeps_the_bounce },
    { "reset_leaves_frame_clean", sc_reset_leaves_frame_clean },
    { "isr_without_a_feeder", sc_isr_without_a_feeder },
};

#define N_SCENARIOS ((int)(sizeof SCENARIOS / sizeof SCENARIOS[0]))

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--list") == 0) {
        for (int i = 0; i < N_SCENARIOS; i++) { printf("%s\n", SCENARIOS[i].name); }
        return 0;
    }
    if (argc != 2) {
        fprintf(stderr, "usage: %s <scenario>|--list\n", argv[0]);
        return 2;
    }
    for (int i = 0; i < N_SCENARIOS; i++) {
        if (strcmp(argv[1], SCENARIOS[i].name) != 0) { continue; }
        h_harness_init(SCENARIOS[i].name);
        SCENARIOS[i].fn();
        CHECK_EQ(h_gil_depth(), 0);
        printf("PASS %s\n", SCENARIOS[i].name);
        return 0;
    }
    fprintf(stderr, "unknown scenario: %s\n", argv[1]);
    return 2;
}
