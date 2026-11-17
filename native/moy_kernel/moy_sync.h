// moy_sync: the lock and the one wait the kernel's queues share (moy_evq.h,
// moy_vmq.h), and the tables they keep (THE TABLE). Internal to
// native/moy_kernel and the device drivers' handle tables.
//
// THE LOCK is a critical section on a board and a spinlock elsewhere: held
// around a few loads and stores, never around an allocation or a wait.
//
// THE WAIT. A waiter blocks until a word it watches changes from what it saw,
// or `ms` passes: a task notification on a board (the waker notifies the task
// the waiter recorded), one condition variable every waiter shares where
// there are threads (the host, the desktop MicroPython), and no blocking at
// all where there are none (the browser), whose callers are stepped.

#ifndef MOY_SYNC_H
#define MOY_SYNC_H

#include "moy_platform.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "moy_kmem.h"

#ifdef MOY_ON_BOARD
#define MOY_SYNC_LOCK_T portMUX_TYPE
#define MOY_SYNC_LOCK_INIT portMUX_INITIALIZER_UNLOCKED
#define MOY_SYNC_LOCK(l) portENTER_CRITICAL(l)
#define MOY_SYNC_UNLOCK(l) portEXIT_CRITICAL(l)
typedef TaskHandle_t moy_sync_who_t;
static inline moy_sync_who_t moy_sync_me(void) {
    return xTaskGetCurrentTaskHandle();
}
#else
#define MOY_SYNC_LOCK_T volatile char
#define MOY_SYNC_LOCK_INIT 0
#define MOY_SYNC_LOCK(l) while (__atomic_test_and_set((l), __ATOMIC_ACQUIRE)) { }
#define MOY_SYNC_UNLOCK(l) __atomic_clear((l), __ATOMIC_RELEASE)
typedef void *moy_sync_who_t;
static inline moy_sync_who_t moy_sync_me(void) {
    return (void *)1;
}
#endif

#ifdef MOY_THREADS
extern pthread_mutex_t moy_sync_mu;
extern pthread_cond_t moy_sync_cv;
#endif

// Block until *word differs from `seen` or `ms` passes (0: do not block).
// Answers whether it changed.
static inline bool moy_sync_wait(volatile uint32_t *word, uint32_t seen, uint32_t ms) {
    if (__atomic_load_n(word, __ATOMIC_ACQUIRE) != seen) {
        return true;
    }
    if (ms == 0) {
        return false;
    }
    #if defined(MOY_ON_BOARD)
    TickType_t t = pdMS_TO_TICKS(ms);
    ulTaskNotifyTake(pdTRUE, t > 0 ? t : 1);
    #elif defined(MOY_THREADS)
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_sec += ms / 1000u;
    ts.tv_nsec += (long)(ms % 1000u) * 1000000L;
    if (ts.tv_nsec >= 1000000000L) {
        ts.tv_sec++;
        ts.tv_nsec -= 1000000000L;
    }
    pthread_mutex_lock(&moy_sync_mu);
    while (__atomic_load_n(word, __ATOMIC_ACQUIRE) == seen) {
        if (pthread_cond_timedwait(&moy_sync_cv, &moy_sync_mu, &ts) != 0) {
            break;
        }
    }
    pthread_mutex_unlock(&moy_sync_mu);
    #endif
    return __atomic_load_n(word, __ATOMIC_ACQUIRE) != seen;
}

// *word moved on and its waiter (`who`, NULL for none recorded) woken.
static inline void moy_sync_wake(volatile uint32_t *word, moy_sync_who_t who) {
    __atomic_add_fetch(word, 1u, __ATOMIC_RELEASE);
    #if defined(MOY_ON_BOARD)
    if (who != NULL) {
        xTaskNotifyGive(who);
    }
    #elif defined(MOY_THREADS)
    (void)who;
    pthread_mutex_lock(&moy_sync_mu);
    pthread_cond_broadcast(&moy_sync_cv);
    pthread_mutex_unlock(&moy_sync_mu);
    #else
    (void)who;
    #endif
}

// THE TABLE. Kernel data is PSRAM (moy_kernel.h), so a table a task fills is
// one block made at its first use, never .bss the idle desk pays for: *t made
// now if it is not yet, zeroed, PSRAM first (calloc where there is none), and
// never freed, so its rows' generations outlive their ends. Tasks may race to
// make it; the first to publish wins and the others give theirs back. NULL
// when there is no memory. Never under THE LOCK: it allocates.
static inline void *moy_sync_table(void **t, size_t n) {
    void *p = __atomic_load_n(t, __ATOMIC_ACQUIRE);
    if (p != NULL) {
        return p;
    }
    if ((p = moy_kalloc(n, MOY_KMEM_PSRAM | MOY_KMEM_INTERNAL_OK | MOY_KMEM_ZERO)) == NULL) {
        return NULL;
    }
    void *none = NULL;
    if (!__atomic_compare_exchange_n(t, &none, p, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        moy_kfree(p);
        return none;
    }
    return p;
}

// *t as it stands: NULL until moy_sync_table made it, which a reader takes
// as a table with no rows.
static inline void *moy_sync_table_at(void *const *t) {
    return __atomic_load_n(t, __ATOMIC_ACQUIRE);
}

// The two for a typed pointer `t`: MOY_SYNC_TABLE(t) makes *t's one row
// block; MOY_SYNC_AT(t) reads it.
#define MOY_SYNC_TABLE(t) ((__typeof__(t))moy_sync_table((void **)&(t), sizeof *(t)))
#define MOY_SYNC_AT(t) ((__typeof__(t))moy_sync_table_at((void *const *)&(t)))

#endif // MOY_SYNC_H
