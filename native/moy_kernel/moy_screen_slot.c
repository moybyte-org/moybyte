// moy_screen_slot: a program's screen handed to the console (moy_screen_slot.h).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99 (moy_sync.h)
#endif

#include "moy_screen_slot.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_evq.h"
#include "moy_sync.h"

#ifdef MOY_SYNC_RTOS
#include "esp_heap_caps.h"
#define SLOT_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#define SLOT_ALLOC(n) malloc(n)
#endif

// A present waits in slices this long, so a closed slot answers it soon.
#define SLICE_MS 20u

enum { FREE, WRITING, READY, TAKEN };

typedef struct {
    uint32_t pid;               // 0: free
    int w, h, depth;
    uint8_t *px;
    volatile uint8_t state;
    volatile uint8_t closed;
    volatile uint32_t room;     // moved where the frame is let go or the slot closed
    volatile uint32_t shown;    // moved where a present made it READY
    moy_sync_who_t presenter;   // the program's task waiting on `room`
    moy_sync_who_t console;     // the console's task waiting on `shown`
} slot_t;

static slot_t s_slots[MOY_SCREEN_SLOTS];
static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;

static slot_t *slot_of(uint32_t pid) {
    for (uint32_t i = 0; i < MOY_SCREEN_SLOTS; i++) {
        if (pid != 0u && s_slots[i].pid == pid) {
            return &s_slots[i];
        }
    }
    return NULL;
}

int moy_screen_slot_open(uint32_t pid, int w, int h, int depth) {
    if (pid == 0u || w <= 0 || h <= 0 || depth < 1 || depth > 2) {
        return -EINVAL;
    }
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        for (uint32_t i = 0; i < MOY_SCREEN_SLOTS && s == NULL; i++) {
            if (s_slots[i].pid == 0u) {
                s = &s_slots[i];
            }
        }
        if (s == NULL) {
            return -ENOSPC;
        }
    }
    uint8_t *px = SLOT_ALLOC((size_t)w * (size_t)h * (size_t)depth);
    if (px == NULL) {
        return -ENOMEM;
    }
    free(s->px);
    memset(s, 0, sizeof(*s));
    s->w = w;
    s->h = h;
    s->depth = depth;
    s->px = px;
    __atomic_store_n(&s->pid, pid, __ATOMIC_RELEASE);
    return 0;
}

void moy_screen_slot_close(uint32_t pid) {
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        return;
    }
    MOY_SYNC_LOCK(&s_lock);
    s->closed = 1;
    bool writing = s->state == WRITING;
    MOY_SYNC_UNLOCK(&s_lock);
    moy_sync_wake(&s->room, s->presenter);
    // A present copying now finishes before the pixels go.
    while (writing) {
        moy_sync_wait(&s->shown, s->shown, 1u);
        MOY_SYNC_LOCK(&s_lock);
        writing = s->state == WRITING;
        MOY_SYNC_UNLOCK(&s_lock);
    }
    free(s->px);
    s->px = NULL;
    __atomic_store_n(&s->pid, 0u, __ATOMIC_RELEASE);
}

int moy_screen_slot_size(uint32_t pid, int *w, int *h, int *depth) {
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        return -ENODEV;
    }
    *w = s->w;
    *h = s->h;
    *depth = s->depth;
    return 0;
}

int moy_screen_slot_present(uint32_t pid, const void *px, size_t n) {
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        return -ENODEV;
    }
    if (px == NULL || n != (size_t)s->w * (size_t)s->h * (size_t)s->depth) {
        return -EINVAL;
    }
    s->presenter = moy_sync_me();
    for (;;) {
        uint32_t seen = __atomic_load_n(&s->room, __ATOMIC_ACQUIRE);
        MOY_SYNC_LOCK(&s_lock);
        bool closed = s->closed != 0;
        bool free_ = !closed && s->state == FREE;
        if (free_) {
            s->state = WRITING;
        }
        MOY_SYNC_UNLOCK(&s_lock);
        if (closed) {
            return -EINTR;
        }
        if (free_) {
            break;
        }
        moy_sync_wait(&s->room, seen, SLICE_MS);
    }
    memcpy(s->px, px, n);
    MOY_SYNC_LOCK(&s_lock);
    s->state = READY;
    MOY_SYNC_UNLOCK(&s_lock);
    moy_sync_wake(&s->shown, s->console);
    return 0;
}

int moy_screen_slot_frame(uint32_t pid, uint32_t held, uint32_t pressed, uint32_t ms,
                          const int32_t *in, int n_in, uint32_t max_ms) {
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        return -ENODEV;
    }
    s->console = moy_sync_me();
    MOY_SYNC_LOCK(&s_lock);
    bool let_go = s->state == TAKEN;
    if (let_go) {
        s->state = FREE;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (let_go) {
        moy_sync_wake(&s->room, s->presenter);
    }
    int out = 0;
    if (moy_evq_queued(pid) == 0u) {
        char args[40 + 12 * MOY_SCREEN_SLOT_IN];
        int k = snprintf(args, sizeof args, "[%u,%u,%u", (unsigned)held, (unsigned)pressed,
                         (unsigned)ms);
        for (int i = 0; i < n_in && i < MOY_SCREEN_SLOT_IN; i++) {
            k += snprintf(args + k, sizeof args - (size_t)k, ",%ld", (long)in[i]);
        }
        args[k++] = ']';
        if (moy_evq_post(pid, 0, "frame", args, (size_t)k) == MOY_EVQ_OK) {
            out |= MOY_SCREEN_SLOT_SENT;
        }
    }
    for (uint32_t waited = 0;; waited += SLICE_MS) {
        uint32_t seen = __atomic_load_n(&s->shown, __ATOMIC_ACQUIRE);
        MOY_SYNC_LOCK(&s_lock);
        bool ready = s->state == READY;
        if (ready) {
            s->state = TAKEN;
        }
        MOY_SYNC_UNLOCK(&s_lock);
        if (ready) {
            return out | MOY_SCREEN_SLOT_SHOWN;
        }
        if (waited >= max_ms) {
            return out;
        }
        uint32_t left = max_ms - waited;
        moy_sync_wait(&s->shown, seen, left < SLICE_MS ? left : SLICE_MS);
    }
}

const uint8_t *moy_screen_slot_pixels(uint32_t pid, int *w, int *h, int *depth) {
    slot_t *s = slot_of(pid);
    if (s == NULL) {
        return NULL;
    }
    *w = s->w;
    *h = s->h;
    *depth = s->depth;
    return s->px;
}
