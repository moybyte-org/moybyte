// THE SAVER (moy_save.h has the contract).

#include "moy_platform.h"

#include <string.h>

#include "moy_pmem.h"
#include "moy_save.h"
#include "moy_vol.h"

#if defined(MOY_ON_BOARD)
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "moy_tasks.h"
#pragma weak moy_task_spawn
#define SAVE_TASK 1
#else
#define SAVE_TASK 0
#endif

typedef struct {
    char path[MOY_SAVE_PATH];
    int32_t cells[MOY_PMEM_CELLS];
    uint32_t at;            // when it was queued (us)
    uint8_t queued;         // cells holds a record not yet written
    uint8_t writing;        // the saver is writing this path's previous record
} slot_t;

static slot_t *s_slots;      // MOY_SAVE_SLOTS and the record being written, once the task runs
static moy_mutex_t s_mu = MOY_MUTEX_INIT;
static moy_save_stats_t s_stats;

static void stat_save(uint32_t at, int rc, int now) {
    uint32_t us = (uint32_t)moy_now_us() - at;
    if (rc != 0) {
        s_stats.errors++;
        return;
    }
    s_stats.saves++;
    s_stats.now += now != 0;
    s_stats.last_us = us;
    if (us > s_stats.max_us) {
        s_stats.max_us = us;
    }
}

// One record written under the store's lock and the bus gate.
static int put(const char *path, const int32_t *cells) {
    int gate = moy_vol_gate_enter(path);
    if (gate < 0) {
        return MOY_ENODEV;
    }
    int rc = moy_pmem_write(path, cells);
    moy_vol_gate_leave(gate);
    return rc;
}

// On the caller's task. A path the saver refuses has no record queued, and
// one it takes reaches here only when no slot holds it (MOY_EBUSY).
static int write_now(const char *path, const int32_t *cells) {
    uint32_t at = (uint32_t)moy_now_us();
    int rc = put(path, cells);
    moy_mutex_lock(&s_mu);
    stat_save(at, rc, 1);
    moy_mutex_unlock(&s_mu);
    return rc;
}

#if SAVE_TASK

static TaskHandle_t s_task;

// Whether the saver writes `path`: a card, or POSIX,
// as the saver's task resolves the path (the volume table: internal flash
// and the VM's own mounts are not among them).
static int saveable(const char *path) {
    moy_vol_t v;
    const char *rest;
    int rc = moy_vol_table_at(path, &v, &rest);
    return rc == 0 && (v.kind == MOY_VOL_KIND_FAT || v.kind == MOY_VOL_KIND_POSIX);
}

// The next queued record, copied into the spare slot's cells: its slot, or NULL.
static slot_t *take(uint32_t *at) {
    slot_t *got = NULL;
    moy_mutex_lock(&s_mu);
    for (int i = 0; i < MOY_SAVE_SLOTS; i++) {
        slot_t *s = &s_slots[i];
        if (s->queued && !s->writing) {
            memcpy(s_slots[MOY_SAVE_SLOTS].cells, s->cells, sizeof s->cells);
            *at = s->at;
            s->queued = 0;
            s->writing = 1;
            got = s;
            break;
        }
    }
    moy_mutex_unlock(&s_mu);
    return got;
}

static void saver(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        uint32_t at;
        slot_t *s;
        while ((s = take(&at)) != NULL) {
            int rc = put(s->path, s_slots[MOY_SAVE_SLOTS].cells);
            moy_mutex_lock(&s_mu);
            stat_save(at, rc, 0);
            s->writing = 0;
            if (!s->queued) {
                s->path[0] = 0;
            }
            moy_mutex_unlock(&s_mu);
        }
    }
}

static int start(void) {
    if (s_task != NULL) {
        return 1;
    }
    if (moy_task_spawn == NULL) {
        return 0;
    }
    if (s_slots == NULL) {
        s_slots = moy_store_keep((MOY_SAVE_SLOTS + 1) * sizeof(slot_t));
        if (s_slots == NULL) {
            return 0;
        }
        memset(s_slots, 0, (MOY_SAVE_SLOTS + 1) * sizeof(slot_t));
    }
    return moy_task_spawn(MOY_TASK_SAVE, saver, NULL, &s_task) && s_task != NULL;
}

// 0: queued; else the caller writes it.
static int queue(const char *path, const int32_t *cells) {
    if (!saveable(path)) {
        return MOY_ENODEV;
    }
    moy_mutex_lock(&s_mu);
    if (!start()) {
        moy_mutex_unlock(&s_mu);
        return MOY_EBUSY;
    }
    slot_t *s = NULL, *free_slot = NULL;
    for (int i = 0; i < MOY_SAVE_SLOTS; i++) {
        slot_t *k = &s_slots[i];
        if (k->path[0] != 0 && strcmp(k->path, path) == 0) {
            s = k;
            break;
        }
        if (free_slot == NULL && k->path[0] == 0) {
            free_slot = k;
        }
    }
    if (s == NULL && free_slot == NULL) {
        moy_mutex_unlock(&s_mu);
        return MOY_EBUSY;
    }
    if (s == NULL) {
        s = free_slot;
        strcpy(s->path, path);
    } else if (s->queued) {
        s_stats.replaced++;
    }
    memcpy(s->cells, cells, sizeof s->cells);
    s->queued = 1;
    s->at = (uint32_t)moy_now_us();
    moy_mutex_unlock(&s_mu);
    xTaskNotifyGive(s_task);
    return 0;
}

#else

static int queue(const char *path, const int32_t *cells) {
    (void)path;
    (void)cells;
    return MOY_ENODEV;
}

#endif

int moy_save_pmem(const char *path, const int32_t *cells) {
    if (strlen(path) >= MOY_SAVE_PATH) {
        return MOY_EINVAL;
    }
    return queue(path, cells) == 0 ? 0 : write_now(path, cells);
}

int moy_save_busy(const char *path) {
    int busy = 0;
    moy_mutex_lock(&s_mu);
    for (int i = 0; s_slots != NULL && i < MOY_SAVE_SLOTS && !busy; i++) {
        slot_t *s = &s_slots[i];
        busy = s->path[0] != 0 && (s->queued || s->writing)
               && (path == NULL || strcmp(s->path, path) == 0);
    }
    moy_mutex_unlock(&s_mu);
    return busy;
}

void moy_save_wait(const char *path) {
    #if SAVE_TASK
    while (moy_save_busy(path)) {
        vTaskDelay(1);
    }
    #else
    (void)path;
    #endif
}

void moy_save_stats(moy_save_stats_t *out, int reset) {
    moy_mutex_lock(&s_mu);
    *out = s_stats;
    uint32_t busy = 0;
    for (int i = 0; s_slots != NULL && i < MOY_SAVE_SLOTS; i++) {
        busy += s_slots[i].path[0] != 0 && (s_slots[i].queued || s_slots[i].writing);
    }
    out->busy = busy;
    if (reset) {
        memset(&s_stats, 0, sizeof s_stats);
    }
    moy_mutex_unlock(&s_mu);
}
