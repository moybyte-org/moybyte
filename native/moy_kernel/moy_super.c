// moy_super: the one supervisor of background callbacks (moy_super.h has the
// ladder).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99
#endif

#include "moy_super.h"

#include <stdio.h>
#include <string.h>

#include "moy_sync.h"

// The OS log (moy_log.h), where the image has it.
void moy_log_write(int level, uint32_t pid, const char *text, size_t n) __attribute__((weak));
enum { LOG_ERR = 3, LOG_WARNING = 4 };

typedef struct {
    uint32_t pid;               // 0: a free row
    uint32_t age;               // when it was last charged, to forget the oldest
    uint8_t overruns;
    uint8_t strikes;
    uint8_t held;
} row_t;

static row_t s_row[MOY_SUPER_ROWS];
static uint32_t s_age;
static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;

static void say(int level, uint32_t pid, const char *text) {
    if (moy_log_write != NULL) {
        moy_log_write(level, pid, text, strlen(text));
    }
}

// `pid`'s row, made where `make` (the oldest forgotten). Under the lock.
static row_t *row_of(uint32_t pid, bool make) {
    row_t *oldest = NULL;
    for (uint32_t i = 0; i < MOY_SUPER_ROWS; i++) {
        row_t *r = &s_row[i];
        if (r->pid == pid) {
            return r;
        }
        if (oldest == NULL || (oldest->pid != 0 && (r->pid == 0 || r->age < oldest->age))) {
            oldest = r;
        }
    }
    if (!make) {
        return NULL;
    }
    memset(oldest, 0, sizeof *oldest);
    oldest->pid = pid;
    return oldest;
}

int moy_super_charge(uint32_t pid, uint32_t cost_us, bool aborted) {
    if (pid <= 1u || (!aborted && cost_us <= MOY_SUPER_SLICE_US)) {
        return MOY_SUPER_FINE;
    }
    int rung;
    uint32_t strikes;
    MOY_SYNC_LOCK(&s_lock);
    row_t *r = row_of(pid, true);
    r->age = ++s_age;
    if (r->overruns < 255u) {
        r->overruns++;
    }
    if (!aborted && r->overruns == 1u) {
        rung = MOY_SUPER_WARNED;
    } else {
        if (r->strikes < 255u) {
            r->strikes++;
        }
        rung = MOY_SUPER_STRUCK;
        if (r->strikes >= MOY_SUPER_STRIKES && !r->held) {
            r->held = 1;
            rung = MOY_SUPER_SUSPENDED;
        }
    }
    strikes = r->strikes;
    MOY_SYNC_UNLOCK(&s_lock);
    char line[96];
    if (rung == MOY_SUPER_WARNED) {
        snprintf(line, sizeof line, "callback ran %u ms, over its %u ms slice",
                 (unsigned)(cost_us / 1000u), (unsigned)(MOY_SUPER_SLICE_US / 1000u));
        say(LOG_WARNING, pid, line);
        return rung;
    }
    if (aborted) {
        snprintf(line, sizeof line, "strike %u of %u: aborted after %u ms", (unsigned)strikes,
                 (unsigned)MOY_SUPER_STRIKES, (unsigned)(MOY_SUPER_ABORT_US / 1000u));
    } else {
        snprintf(line, sizeof line, "strike %u of %u: callback ran %u ms", (unsigned)strikes,
                 (unsigned)MOY_SUPER_STRIKES, (unsigned)(cost_us / 1000u));
    }
    say(LOG_ERR, pid, line);
    if (rung == MOY_SUPER_SUSPENDED) {
        say(LOG_ERR, pid, "suspended");
    }
    return rung;
}

bool moy_super_held(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    row_t *r = row_of(pid, false);
    bool held = r != NULL && r->held;
    MOY_SYNC_UNLOCK(&s_lock);
    return held;
}

bool moy_super_resume(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    row_t *r = row_of(pid, false);
    bool was = r != NULL && r->held;
    if (r != NULL) {
        memset(r, 0, sizeof *r);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return was;
}

void moy_super_info(uint32_t pid, uint32_t *overruns, uint32_t *strikes) {
    MOY_SYNC_LOCK(&s_lock);
    row_t *r = row_of(pid, false);
    *overruns = r != NULL ? r->overruns : 0u;
    *strikes = r != NULL ? r->strikes : 0u;
    MOY_SYNC_UNLOCK(&s_lock);
}

void moy_super_forget(uint32_t pid) {
    MOY_SYNC_LOCK(&s_lock);
    row_t *r = row_of(pid, false);
    if (r != NULL) {
        memset(r, 0, sizeof *r);
    }
    MOY_SYNC_UNLOCK(&s_lock);
}

void moy_super_reset(void) {
    MOY_SYNC_LOCK(&s_lock);
    memset(s_row, 0, sizeof s_row);
    s_age = 0;
    MOY_SYNC_UNLOCK(&s_lock);
}
