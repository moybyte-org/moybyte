// moy_flush: the shared banded-flush engine. See moy_flush.h for the split,
// the handoff protocol and the reset-order invariant -- every paragraph of it
// was learned on one of the two boards this file now serves, and this .c is
// only the mechanism those paragraphs describe.

#include "py/runtime.h"
#include "py/objtuple.h"
#include "py/mphal.h"
#include "py/mpthread.h"

#include "freertos/idf_additions.h"   // xTaskCreatePinnedToCore

#include "esp_heap_caps.h"

#include "moy_flush.h"

moy_flush_state_t moy_flush = { .frame_clean = true, .feed_us = -1,
                                .feed_last_us = -1 };

static const moy_flush_ops_t *s_ops;

// ---------------------------------------------------------------------------
// The feeder
// ---------------------------------------------------------------------------

// Synthesize + queue every band whose bounce slot has freed. One board-side
// band synthesis (a memcpy on the T-Deck, a rotate/fold gather on the
// Guition) and one transport queue call per band, neither of which may block:
// the whole point of the split is that the feeder returns to its wait between
// bands so the done-ISR paces it.
//
// Runs on the FEEDER task only. Errors LATCH instead of raising -- this task
// has no MP context (an nlr raise here would abort) -- and the latch also
// disarms the guard below, so a failed queue stops the feed for this frame.
static void moy_flush_pump(void) {
    if (moy_flush.in_pump || moy_flush.bnc_total == 0
            || moy_flush.bnc_next >= moy_flush.bnc_total
            || moy_flush.tx_err != ESP_OK) {
        return;
    }
    moy_flush.in_pump = true;
    uint32_t p0 = (uint32_t)esp_timer_get_time();
    const int slots = s_ops->bounce_slots;
    const int band_rows = s_ops->band_rows;
    const int total = moy_flush.bnc_total;
    int k = moy_flush.bnc_next;
    while (k < total && (int32_t)moy_flush.done >= k - (slots - 1)) {
        // Pacing probe: about to feed band k while everything already queued
        // has COMPLETED means the transport has been idle since that last
        // completion. Band 0 follows a drained bus by design, never a gap.
        if (k > 0 && moy_flush.done >= moy_flush.target) {
            uint32_t gap = (uint32_t)esp_timer_get_time() - moy_flush.done_us;
            if (gap > 0 && gap < 1000000u) {   // a torn/absurd read is not a gap
                moy_flush.idle_us += gap;
                moy_flush.idle_n++;
            }
        }
        int y = k * band_rows;
        int rows = (y + band_rows <= moy_flush.frame_rows)
                   ? band_rows : (moy_flush.frame_rows - y);
        esp_err_t err = s_ops->queue_band(moy_flush.bounce[k % slots],
                                          moy_flush.bnc_src, k, y, rows,
                                          k == total - 1);
        if (err != ESP_OK) {
            moy_flush.tx_err = err;
            moy_flush.tx_errs++;
            break;
        }
        moy_flush.target++;
        k++;
        moy_flush.bnc_next = k;
    }
    uint32_t now = (uint32_t)esp_timer_get_time();
    moy_flush.pump_us += now - p0;
    if (moy_flush.bnc_next >= moy_flush.bnc_total && moy_flush.feed_us < 0) {
        moy_flush.feed_us = (int32_t)(now - moy_flush.kick_us);
    }
    moy_flush.in_pump = false;
}

// One whole flush, on core 0: arm the panel, then feed every band as its slot
// frees -- SLEEPING on the completion ISR's task notify rather than spinning
// or waiting out a timer -- then wait the tail out, then release.
static void moy_flush_run_frame(void) {
    bool ok = true;
    esp_err_t err = s_ops->frame_begin();
    if (err != ESP_OK) {
        moy_flush.tx_err = err;
        moy_flush.tx_errs++;
        ok = false;
        goto done;
    }
    // THE RESET-ORDER INVARIANT (moy_flush.h): the counters restart here,
    // after the arm, never at kick.
    moy_flush.done = 0;
    moy_flush.target = 0;
    moy_flush.flush_t0 = esp_timer_get_time();
    moy_flush.kick_us = (uint32_t)moy_flush.flush_t0;
    int64_t deadline = moy_flush.flush_t0 + MOY_FLUSH_TIMEOUT_US;
    // A queue error stops the FEED (there is no point synthesizing more bands
    // into a stream the transport refused) but the tail wait below still
    // runs: already-queued bands are unaffected and still reading a slot.
    while (moy_flush.bnc_next < moy_flush.bnc_total
            && moy_flush.tx_err == ESP_OK) {
        int before = moy_flush.bnc_next;
        moy_flush_pump();
        if (moy_flush.bnc_next == before && moy_flush.tx_err == ESP_OK) {
            if (esp_timer_get_time() > deadline) {
                ok = false;
                break;
            }
            // The done-ISR's notify is the wake; the tick timeout is only
            // insurance. 2 ticks, NOT pdMS_TO_TICKS(a small ms): FREERTOS_HZ
            // is 100 on these builds, so pdMS_TO_TICKS(5) is ZERO ticks -- a
            // busy spin (moy_axs's lesson, kept).
            ulTaskNotifyTake(pdTRUE, 2);
        }
    }
    // Whatever was QUEUED must finish before this frame may be handed back as
    // done -- this is the promise the T-Deck's SD fence relies on.
    while (ok && moy_flush.tx_err == ESP_OK
            && moy_flush.done < moy_flush.target) {
        if (esp_timer_get_time() > deadline) {
            ok = false;
            break;
        }
        ulTaskNotifyTake(pdTRUE, 2);
    }
    if (ok && moy_flush.tx_err == ESP_OK) {
        moy_flush.flushes++;
        // kick -> LAST COMPLETION, taken from the ISR's own stamp, not from
        // the clock now: this task is reached late under the overlap, so
        // `now - flush_t0` would fold in scheduling latency. (The original
        // form of this lesson: tdeck_smoke.panel() sleeps 120ms between
        // flushes and would have reported the transfer as 120ms.)
        moy_flush.last_flush_us =
            moy_flush.done_us - (uint32_t)moy_flush.flush_t0;
        moy_flush.frame_clean = true;
    } else if (!ok) {
        moy_flush.timeouts++;
    }
done:
    // Runs on EVERY handoff, including a failed frame_begin.
    s_ops->frame_end(ok);
    moy_flush.bnc_total = 0;
    moy_flush.bnc_next = 0;
    moy_flush.bnc_src = NULL;
    // LAST: hand the frame back (THE HANDOFF PROTOCOL in moy_flush.h).
    MOY_FLUSH_HANDOFF_BARRIER();
    moy_flush.frame_busy = false;
    xSemaphoreGive(moy_flush.done_sem);
}

static void moy_flush_task_fn(void *arg) {
    (void)arg;
    for (;;) {
        if (xSemaphoreTake(moy_flush.kick_sem, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (moy_flush.task_exit) {
            break;
        }
        MOY_FLUSH_HANDOFF_BARRIER();
        moy_flush_run_frame();
    }
    moy_flush.task = NULL;
    vTaskDelete(NULL);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

static void moy_flush_free_bounce(void) {
    for (int i = 0; i < MOY_FLUSH_MAX_SLOTS; i++) {
        if (moy_flush.bounce[i]) {
            heap_caps_free(moy_flush.bounce[i]);
            moy_flush.bounce[i] = NULL;
        }
    }
}

bool moy_flush_start(const moy_flush_ops_t *ops) {
    if (ops == NULL || ops->bounce_slots < 1
            || ops->bounce_slots > MOY_FLUSH_MAX_SLOTS) {
        return false;
    }
    s_ops = ops;
    for (int i = 0; i < ops->bounce_slots; i++) {
        if (moy_flush.bounce[i] == NULL) {
            moy_flush.bounce[i] = heap_caps_malloc(
                ops->band_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
            if (moy_flush.bounce[i] == NULL) {
                moy_flush_free_bounce();
                return false;
            }
        }
    }
    if (moy_flush.kick_sem == NULL) {
        moy_flush.kick_sem = xSemaphoreCreateBinary();
    }
    if (moy_flush.done_sem == NULL) {
        moy_flush.done_sem = xSemaphoreCreateBinary();
    }
    if (moy_flush.kick_sem == NULL || moy_flush.done_sem == NULL) {
        moy_flush_free_bounce();
        return false;
    }
    if (moy_flush.task == NULL) {
        moy_flush.task_exit = false;
        if (xTaskCreatePinnedToCore(moy_flush_task_fn, ops->task_name,
                                    MOY_FLUSH_FEED_STACK, NULL,
                                    MOY_FLUSH_FEED_PRIO,
                                    (TaskHandle_t *)&moy_flush.task,
                                    MOY_FLUSH_FEED_CORE) != pdPASS) {
            moy_flush_free_bounce();
            return false;
        }
    }
    return true;
}

void moy_flush_stop(void) {
    if (moy_flush.task != NULL) {
        moy_flush.task_exit = true;
        xSemaphoreGive(moy_flush.kick_sem);
        for (int i = 0; i < 100 && moy_flush.task != NULL; i++) {
            mp_hal_delay_ms(1);
        }
        moy_flush.task_exit = false;
    }
    moy_flush_free_bounce();
    moy_flush_reset();
}

void moy_flush_reset(void) {
    moy_flush.bnc_total = 0;
    moy_flush.bnc_next = 0;
    moy_flush.bnc_src = NULL;
    moy_flush.frame_rows = 0;
    moy_flush.done = 0;
    moy_flush.target = 0;
    moy_flush.tx_err = ESP_OK;
    moy_flush.frame_busy = false;
}

// ---------------------------------------------------------------------------
// The VM side
// ---------------------------------------------------------------------------

void moy_flush_kick(const uint8_t *src, int frame_rows) {
    // Latch the pacing of the frame that just finished (drain ran before this).
    moy_flush.pump_last_us = moy_flush.pump_us;
    moy_flush.idle_last_us = moy_flush.idle_us;
    moy_flush.idle_last_n = moy_flush.idle_n;
    moy_flush.feed_last_us = moy_flush.feed_us;
    moy_flush.block_last_us = moy_flush.block_us;
    moy_flush.pump_us = 0;
    moy_flush.idle_us = 0;
    moy_flush.idle_n = 0;
    moy_flush.feed_us = -1;
    moy_flush.block_us = 0;
    // A new frame starts on a clean error slate -- the previous one's latched
    // error has done its jobs by now (it disarmed the pump and stopped the
    // feeder's loop, it is counted in tx_errs, and the caller raised it before
    // calling here). Leaving it set would keep the pump disarmed.
    moy_flush.tx_err = ESP_OK;
    moy_flush.bnc_next = 0;
    moy_flush.bnc_src = src;
    moy_flush.frame_rows = frame_rows;
    moy_flush.bnc_total =
        (frame_rows + s_ops->band_rows - 1) / s_ops->band_rows;
    // done/target are NOT reset here -- see THE RESET-ORDER INVARIANT.
    moy_flush.frame_clean = false;
    // A stale done credit survives when drain's fast path never took the
    // semaphore; clear it so the next give is THIS frame's.
    xSemaphoreTake(moy_flush.done_sem, 0);
    MOY_FLUSH_HANDOFF_BARRIER();
    moy_flush.frame_busy = true;
    xSemaphoreGive(moy_flush.kick_sem);
}

bool moy_flush_drain(void) {
    if (!moy_flush.frame_busy) {
        MOY_FLUSH_HANDOFF_BARRIER();
        return moy_flush.frame_clean;
    }
    uint32_t b0 = (uint32_t)esp_timer_get_time();
    MP_THREAD_GIL_EXIT();
    for (int i = 0; moy_flush.frame_busy && i < 4; i++) {
        xSemaphoreTake(moy_flush.done_sem, pdMS_TO_TICKS(300));
    }
    MP_THREAD_GIL_ENTER();
    MOY_FLUSH_HANDOFF_BARRIER();
    moy_flush.block_us += (uint32_t)esp_timer_get_time() - b0;
    return moy_flush.frame_clean && !moy_flush.frame_busy;
}

esp_err_t moy_flush_take_err(void) {
    esp_err_t e = moy_flush.tx_err;
    moy_flush.tx_err = ESP_OK;
    return e;
}

// ---------------------------------------------------------------------------
// The meters
// ---------------------------------------------------------------------------

// stats() -> (flushes, last_flush_us). `last_flush_us` is the kick ->
// fully-out WALL span of the last completed frame, i.e. the real cost of
// moving the bytes. It does NOT shrink when the overlap lands -- the transfer
// still takes what it takes; what shrinks is how much of it the CPU waits
// for. That number is pump_stats()[5].
mp_obj_t moy_flush_stats_tuple(void) {
    mp_obj_t t[2] = {
        mp_obj_new_int_from_uint(moy_flush.flushes),
        mp_obj_new_int_from_uint(moy_flush.last_flush_us),
    };
    return mp_obj_new_tuple(2, t);
}

// pump_stats() -> (pump_us, idle_us, idle_n, feed_us, bands, blocked_us,
// timeouts, errs) for the last fully-shipped frame. `BandedCompositor.
// bounce_stats()` hands ALL EIGHT up -- to the PUMP diag line (#66 lever 4) on
// a board that has one, and to the dev channel's `state` snapshot on a board
// that does not (the Guition denies device_diag):
//   pump    CPU us inside the band feed -- the synthesis. Since the CORE-0
//           FEEDER this runs on core 0 and is NOT billed to the frame; it
//           stays reported because a rising value still means real work (and
//           a zero means the feeder never ran)
//   idle    us the transport sat starved because a band was fed after the
//           previous one had already finished. THE pacing number: ~0 means
//           the ceiling is real transfer time and a faster feeder buys nothing
//   gaps    how many bands were fed that late
//   feed    frame start -> last band queued
//   blocked us the VM CPU actually spent waiting in drain -- what the feeder
//           is supposed to drive toward ~0
//   timeouts / errs  both must stay 0. A queue error that happens during a
//           drain cannot be raised (drain must not throw into the frame loop),
//           so `errs` is the only place it is visible at all.
mp_obj_t moy_flush_pump_stats_tuple(int bands) {
    mp_obj_t t[8] = {
        mp_obj_new_int_from_uint(moy_flush.pump_last_us),
        mp_obj_new_int_from_uint(moy_flush.idle_last_us),
        mp_obj_new_int_from_uint(moy_flush.idle_last_n),
        mp_obj_new_int(moy_flush.feed_last_us),
        MP_OBJ_NEW_SMALL_INT(bands),
        mp_obj_new_int_from_uint(moy_flush.block_last_us),
        mp_obj_new_int_from_uint(moy_flush.timeouts),
        mp_obj_new_int_from_uint(moy_flush.tx_errs),
    };
    return mp_obj_new_tuple(8, t);
}
