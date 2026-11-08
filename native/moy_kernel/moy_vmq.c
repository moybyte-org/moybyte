// moy_vmq: the one queue of calls into the VM's task (moy_vmq.h has the
// design).

#if defined(__has_include) && !__has_include("esp_heap_caps.h") && !defined(_POSIX_C_SOURCE)
#define _POSIX_C_SOURCE 200809L     // clock_gettime under -std=c99
#endif

#include "moy_vmq.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "moy_evq.h"
#include "moy_sync.h"

#ifdef MOY_SYNC_RTOS
#include "esp_heap_caps.h"
#include "esp_timer.h"
#else
#include <time.h>
#endif

enum { FREE, HOP, HOP_DONE, CALL, ANSWERED };

typedef struct {
    uint8_t kind;               // FREE .. ANSWERED
    uint16_t gen;
    uint32_t pid;
    uint32_t order;             // when it was posted, to serve the oldest first
    void (*fn)(void *);
    void *arg;
    char name[MOY_VMQ_NAME];
    char *args;
    size_t n;
    char *ans;                  // NULL: refused
    size_t ans_n;
} slot_t;

static slot_t s_slot[MOY_VMQ_SLOTS];
static volatile uint32_t s_done[MOY_VMQ_SLOTS];     // a hop's wait word
static moy_sync_who_t s_who[MOY_VMQ_SLOTS];
static uint16_t s_gen;
static uint32_t s_order;
static MOY_SYNC_LOCK_T s_lock = MOY_SYNC_LOCK_INIT;

static int64_t now_us(void) {
    #ifdef MOY_SYNC_RTOS
    return esp_timer_get_time();
    #else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
    #endif
}

static char *copy(const char *p, size_t n) {
    #ifdef MOY_SYNC_RTOS
    char *c = heap_caps_malloc(n + 1u, MALLOC_CAP_SPIRAM);
    #else
    char *c = malloc(n + 1u);
    #endif
    if (c != NULL) {
        if (n) {
            memcpy(c, p, n);
        }
        c[n] = 0;
    }
    return c;
}

// A free slot claimed as `kind` for `pid`: its index, or -1. Under the lock.
static int claim(uint8_t kind, uint32_t pid) {
    for (uint32_t i = 0; i < MOY_VMQ_SLOTS; i++) {
        if (s_slot[i].kind == FREE) {
            slot_t *s = &s_slot[i];
            if (++s_gen == 0) {
                s_gen = 1;
            }
            s->kind = kind;
            s->gen = s_gen;
            s->pid = pid;
            s->order = ++s_order;
            return (int)i;
        }
    }
    return -1;
}

static int slot_of(uint32_t ticket) {
    uint32_t i = (ticket & 0xFFu) - 1u;
    if (ticket == 0 || i >= MOY_VMQ_SLOTS || s_slot[i].kind == FREE
        || s_slot[i].gen != (uint16_t)(ticket >> 8)) {
        return -1;
    }
    return (int)i;
}

int moy_vmq_hop(uint32_t pid, void (*fn)(void *), void *arg, uint32_t *waited_us) {
    int64_t t = now_us();
    MOY_SYNC_LOCK(&s_lock);
    int i = claim(HOP, pid);
    uint32_t seen = 0;
    if (i >= 0) {
        s_slot[i].fn = fn;
        s_slot[i].arg = arg;
        s_who[i] = moy_sync_me();
        seen = s_done[i];
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (i < 0) {
        return MOY_VMQ_FULL;
    }
    while (__atomic_load_n(&s_slot[i].kind, __ATOMIC_ACQUIRE) != HOP_DONE) {
        moy_sync_wait(&s_done[i], seen, 1000u);
        seen = __atomic_load_n(&s_done[i], __ATOMIC_ACQUIRE);
    }
    MOY_SYNC_LOCK(&s_lock);
    s_slot[i].kind = FREE;
    s_who[i] = NULL;
    MOY_SYNC_UNLOCK(&s_lock);
    if (waited_us != NULL) {
        *waited_us = (uint32_t)(now_us() - t);
    }
    // A kick the wait took is its event queue's again.
    moy_evq_kick(pid);
    return MOY_VMQ_OK;
}

int moy_vmq_post(uint32_t pid, const char *name, const char *args, size_t n, uint32_t *ticket) {
    char *a = copy(args, n);
    if (a == NULL) {
        return MOY_VMQ_NOMEM;
    }
    MOY_SYNC_LOCK(&s_lock);
    int i = claim(CALL, pid);
    if (i >= 0) {
        slot_t *s = &s_slot[i];
        size_t k = strlen(name);
        k = k < MOY_VMQ_NAME - 1u ? k : MOY_VMQ_NAME - 1u;
        memcpy(s->name, name, k);
        s->name[k] = 0;
        s->args = a;
        s->n = n;
        s->ans = NULL;
        s->ans_n = 0;
        *ticket = ((uint32_t)s->gen << 8) | ((uint32_t)i + 1u);
    }
    MOY_SYNC_UNLOCK(&s_lock);
    if (i < 0) {
        free(a);
        return MOY_VMQ_FULL;
    }
    return MOY_VMQ_OK;
}

int moy_vmq_result(uint32_t ticket, char **ans, size_t *n) {
    char *args = NULL;
    int rc = MOY_VMQ_OK;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(ticket);
    if (i < 0 || (s_slot[i].kind != CALL && s_slot[i].kind != ANSWERED)) {
        rc = MOY_VMQ_STALE;
    } else if (s_slot[i].kind == CALL) {
        rc = MOY_VMQ_WAITING;
    } else {
        slot_t *s = &s_slot[i];
        *ans = s->ans;
        *n = s->ans_n;
        args = s->args;
        s->args = s->ans = NULL;
        s->kind = FREE;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    free(args);
    return rc;
}

void moy_vmq_drop(uint32_t pid) {
    for (uint32_t i = 0; i < MOY_VMQ_SLOTS; i++) {
        char *a = NULL, *b = NULL;
        MOY_SYNC_LOCK(&s_lock);
        slot_t *s = &s_slot[i];
        if (s->pid == pid && (s->kind == CALL || s->kind == ANSWERED)) {
            a = s->args;
            b = s->ans;
            s->args = s->ans = NULL;
            s->kind = FREE;
        }
        MOY_SYNC_UNLOCK(&s_lock);
        free(a);
        free(b);
    }
}

uint32_t moy_vmq_serve(void) {
    uint32_t calls = 0;
    for (;;) {
        int pick = -1;
        MOY_SYNC_LOCK(&s_lock);
        calls = 0;
        for (uint32_t i = 0; i < MOY_VMQ_SLOTS; i++) {
            if (s_slot[i].kind == HOP
                && (pick < 0 || s_slot[i].order < s_slot[pick].order)) {
                pick = (int)i;
            }
            calls += s_slot[i].kind == CALL;
        }
        MOY_SYNC_UNLOCK(&s_lock);
        if (pick < 0) {
            return calls;
        }
        s_slot[pick].fn(s_slot[pick].arg);
        MOY_SYNC_LOCK(&s_lock);
        moy_sync_who_t who = s_who[pick];
        __atomic_store_n(&s_slot[pick].kind, (uint8_t)HOP_DONE, __ATOMIC_RELEASE);
        MOY_SYNC_UNLOCK(&s_lock);
        moy_sync_wake(&s_done[pick], who);
    }
}

int moy_vmq_next(uint32_t *ticket, uint32_t *pid, const char **name, const char **args, size_t *n) {
    int pick = -1;
    MOY_SYNC_LOCK(&s_lock);
    for (uint32_t i = 0; i < MOY_VMQ_SLOTS; i++) {
        if (s_slot[i].kind == CALL && (pick < 0 || s_slot[i].order < s_slot[pick].order)) {
            pick = (int)i;
        }
    }
    if (pick >= 0) {
        slot_t *s = &s_slot[pick];
        *ticket = ((uint32_t)s->gen << 8) | ((uint32_t)pick + 1u);
        *pid = s->pid;
        *name = s->name;
        *args = s->args;
        *n = s->n;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    return pick >= 0 ? MOY_VMQ_OK : MOY_VMQ_EMPTY;
}

int moy_vmq_answer(uint32_t ticket, const char *ans, size_t n) {
    char *a = NULL;
    if (ans != NULL && (a = copy(ans, n)) == NULL) {
        return MOY_VMQ_NOMEM;
    }
    uint32_t pid = 0;
    MOY_SYNC_LOCK(&s_lock);
    int i = slot_of(ticket);
    if (i >= 0 && s_slot[i].kind == CALL) {
        s_slot[i].ans = a;
        s_slot[i].ans_n = a != NULL ? n : 0;
        s_slot[i].kind = ANSWERED;
        pid = s_slot[i].pid;
        a = NULL;
    } else {
        i = -1;
    }
    MOY_SYNC_UNLOCK(&s_lock);
    free(a);
    if (i < 0) {
        return MOY_VMQ_STALE;
    }
    moy_evq_kick(pid);
    return MOY_VMQ_OK;
}

void moy_vmq_reset(void) {
    for (uint32_t i = 0; i < MOY_VMQ_SLOTS; i++) {
        if (s_slot[i].kind != HOP) {
            free(s_slot[i].args);
            free(s_slot[i].ans);
            memset(&s_slot[i], 0, sizeof s_slot[i]);
        }
    }
}
