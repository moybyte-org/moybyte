// moy_wd: the task watchdog the console's frame feeds (moy_kernel.h, #160).
// A frame that never ends panics the board through moy_kernel.c's wrapper,
// whose record names this file's reason.

#include "moy_kernel_int.h"

// DRAM, because the panic wrapper reads it with the flash cache possibly off.
DRAM_ATTR char moy_k_twdt_what[MOY_CRASH_WHAT_LEN] = "task watchdog";
static bool s_wd_armed;
static uint32_t s_wd_last_us, s_wd_max_gap_ms, s_wd_frames;

// One call per console frame. The first subscribes the VM task (it runs on it)
// and the record's reason; every call resets the watchdog and keeps the longest
// gap between two (the worst frame the console took), which the gate reads.
// A call from another task once it is armed (a worker's network wait, which
// feeds through the client's tick) is not the VM's frame and changes nothing.
void moy_kernel_feed(void) {
    // The clock's low 32 bits: a gap under 71 minutes subtracts true, and
    // nothing here divides 64 bits once a frame.
    uint32_t now = (uint32_t)moy_now_us();
    if (s_wd_armed && esp_task_wdt_status(NULL) != ESP_OK) {
        return;
    }
    if (!s_wd_armed) {
        if (esp_task_wdt_add(NULL) != ESP_OK) {
            return;
        }
        moy_str_fmt(moy_k_twdt_what, sizeof(moy_k_twdt_what), MOY_S_RECOVERY_HUNG,
                    (unsigned)CONFIG_ESP_TASK_WDT_TIMEOUT_S);
        s_wd_armed = true;
        s_wd_max_gap_ms = 0;
        s_wd_frames = 0;
    } else if ((now - s_wd_last_us) / 1000u > s_wd_max_gap_ms) {
        s_wd_max_gap_ms = (now - s_wd_last_us) / 1000u;
    }
    s_wd_last_us = now;
    s_wd_frames++;
    esp_task_wdt_reset();
}

// The console's loop ended (a quit, a Ctrl-C): nothing feeds the watchdog now.
void moy_kernel_rest(void) {
    if (s_wd_armed) {
        esp_task_wdt_delete(NULL);
        s_wd_armed = false;
    }
}

bool moy_kernel_watchdog(uint32_t *timeout_ms, uint32_t *max_gap_ms, uint32_t *frames, bool reset) {
    bool armed = s_wd_armed;
    *timeout_ms = (uint32_t)CONFIG_ESP_TASK_WDT_TIMEOUT_S * 1000u;
    *max_gap_ms = s_wd_max_gap_ms;
    *frames = s_wd_frames;
    if (reset) {
        s_wd_max_gap_ms = 0;
        s_wd_frames = 0;
    }
    return armed;
}
