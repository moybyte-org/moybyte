// moy_sync: the lock and the one wait the kernel's queues share (moy_evq.h,
// moy_vmq.h). Internal to native/moy_kernel.
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

#include <stdbool.h>
#include <stdint.h>

#if defined(__has_include) && __has_include("esp_heap_caps.h")
#define MOY_SYNC_RTOS 1
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#elif !defined(__EMSCRIPTEN__) && defined(__has_include) && __has_include(<pthread.h>)
#define MOY_SYNC_THREADS 1
#include <pthread.h>
#include <time.h>
#endif

#ifdef MOY_SYNC_RTOS
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

#ifdef MOY_SYNC_THREADS
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
    #if defined(MOY_SYNC_RTOS)
    TickType_t t = pdMS_TO_TICKS(ms);
    ulTaskNotifyTake(pdTRUE, t > 0 ? t : 1);
    #elif defined(MOY_SYNC_THREADS)
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
    #if defined(MOY_SYNC_RTOS)
    if (who != NULL) {
        xTaskNotifyGive(who);
    }
    #elif defined(MOY_SYNC_THREADS)
    (void)who;
    pthread_mutex_lock(&moy_sync_mu);
    pthread_cond_broadcast(&moy_sync_cv);
    pthread_mutex_unlock(&moy_sync_mu);
    #else
    (void)who;
    #endif
}

#endif // MOY_SYNC_H
