// moy_super: the one supervisor of background callbacks (moy_super.h has the
// ladder).

#include "moy_platform.h"

#include "moy_super.h"

#include <stdio.h>
#include <string.h>

#include "moy_evq.h"
#include "moy_sync.h"

// The OS log (moy_log.h), where the image has it.
#include "moy_log.h"
#pragma weak moy_log_write
enum { LOG_ERR = 3, LOG_WARNING = 4 };

typedef struct {
    uint32_t pid;               // 0: a free row
    uint32_t age;               // when it was last charged, to forget the oldest
    uint8_t overruns;
    uint8_t strikes;
    uint8_t held;
} row_t;

static row_t (*s_row)[MOY_SUPER_ROWS];  // made at the first overrun (moy_sync.h's THE TABLE)
static uint32_t s_age;
static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;

static void say(int level, uint32_t pid, const char *text) {
    if (moy_log_write != NULL) {
        moy_log_write(level, pid, text, strlen(text));
    }
}

// `pid`'s row, made where `make` (the oldest forgotten): NULL for none, or
// no table. Under the lock.
static row_t *row_of(uint32_t pid, bool make) {
    row_t *oldest = NULL;
    for (uint32_t i = 0; s_row != NULL && i < MOY_SUPER_ROWS; i++) {
        row_t *r = &(*s_row)[i];
        if (r->pid == pid) {
            return r;
        }
        if (oldest == NULL || (oldest->pid != 0 && (r->pid == 0 || r->age < oldest->age))) {
            oldest = r;
        }
    }
    if (!make || oldest == NULL) {
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
    if (MOY_SYNC_TABLE(s_row) == NULL) {
        return MOY_SUPER_FINE;          // no memory for the rows: nothing kept to strike
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
    moy_evq_want(MOY_EVQ_WANT_POLL);    // the row's rungs, read at the poll
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
    if (s_row != NULL) {
        memset(s_row, 0, sizeof *s_row);
    }
    s_age = 0;
    MOY_SYNC_UNLOCK(&s_lock);
}
