// moy_platform: what a native module is built for, decided once, and the
// clock and the mutex every module shares. Every image reaches it through the
// kernel's include directory, and a host or cart build through its own -I;
// only a board's mutex is out of line (moy_platform.c).
//
// WHAT IT IS BUILT FOR, one of three:
//   MOY_ON_BOARD   an ESP-IDF image. ESP_PLATFORM is not defined for a
//                  usermod's sources on the esp32 port (the executable
//                  compiles them too, and its copy is linked), so a board is
//                  known by a header only ESP-IDF has.
//   MOY_THREADS    a host with POSIX threads (the host library, the desktop
//                  MicroPython).
//   MOY_BROWSER    the browser's WebAssembly build: no threads, so a mutex
//                  is no lock at all and a caller is stepped, never blocked.
// Each is defined (to 1) only where it holds: test it with #ifdef or #if.
//
// INCLUDE IT FIRST, before any system header. A host built -std=c99 sees no
// POSIX unless asked, and the clock below is POSIX's; this header asks, and
// the ask only takes before the C library's first header.
//
// THE CLOCK. moy_now_us() and moy_now_ms() read one monotonic clock from an
// arbitrary origin: esp_timer on a board (safe from an ISR: always inlined),
// CLOCK_MONOTONIC elsewhere.
//
// THE MUTEX. moy_mutex_t is a lock a task may hold across a wait or an
// allocation, plain (MOY_MUTEX_INIT) or recursive (MOY_RMUTEX_INIT), made at
// its first use so a static one costs nothing until then. On a board it is
// a FreeRTOS mutex in the struct's own buffer; with threads a pthread mutex;
// in the browser nothing. One inside a block made at run time takes
// moy_mutex_init and, before the block is freed, moy_mutex_destroy. It is not
// moy_sync.h's critical section, which is
// held around a few loads and stores and never across a wait.

#ifndef MOY_PLATFORM_H
#define MOY_PLATFORM_H

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define MOY_ON_BOARD 1
#endif
#endif

#ifndef MOY_ON_BOARD
#if defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)
#define MOY_BROWSER 1
#elif defined(__has_include)
#if __has_include(<pthread.h>)
#define MOY_THREADS 1
#endif
#endif
#if defined(__STRICT_ANSI__) && !defined(_POSIX_C_SOURCE) && !defined(_XOPEN_SOURCE) \
    && !defined(_GNU_SOURCE) && !defined(_DEFAULT_SOURCE)
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#endif
#endif

#include <stdbool.h>
#include <stdint.h>

#ifdef MOY_ON_BOARD
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

__attribute__((always_inline)) static inline int64_t moy_now_us(void) {
    return esp_timer_get_time();
}
#else
#include <time.h>
#ifdef CLOCK_MONOTONIC
static inline int64_t moy_now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + (int64_t)ts.tv_nsec / 1000;
}
#endif
#endif

#if defined(MOY_ON_BOARD) || defined(CLOCK_MONOTONIC)
static inline uint64_t moy_now_ms(void) {
    return (uint64_t)(moy_now_us() / 1000);
}
#endif

#if defined(MOY_ON_BOARD)
// Out of line (moy_platform.c): its first use makes it under one spinlock.
typedef struct {
    SemaphoreHandle_t h;
    StaticSemaphore_t buf;
    bool recursive;
} moy_mutex_t;
#define MOY_MUTEX_INIT {.h = NULL}
#define MOY_RMUTEX_INIT {.h = NULL, .recursive = true}

void moy_mutex_init(moy_mutex_t *m, bool recursive);
void moy_mutex_lock(moy_mutex_t *m);
bool moy_mutex_trylock(moy_mutex_t *m);
void moy_mutex_unlock(moy_mutex_t *m);
void moy_mutex_destroy(moy_mutex_t *m);
#elif defined(MOY_THREADS)
#include <pthread.h>

// A recursive one is the plain one with its holder and depth beside it.
typedef struct {
    pthread_mutex_t mu;
    pthread_t owner;
    uint32_t depth;
    bool recursive;
} moy_mutex_t;
#define MOY_MUTEX_INIT {PTHREAD_MUTEX_INITIALIZER, 0, 0, false}
#define MOY_RMUTEX_INIT {PTHREAD_MUTEX_INITIALIZER, 0, 0, true}

static inline void moy_mutex_init(moy_mutex_t *m, bool recursive) {
    pthread_mutex_init(&m->mu, NULL);
    m->depth = 0;
    m->recursive = recursive;
}

static inline bool moy_mutex__mine(moy_mutex_t *m) {
    return __atomic_load_n(&m->depth, __ATOMIC_ACQUIRE) != 0
        && pthread_equal(m->owner, pthread_self());
}

static inline void moy_mutex_lock(moy_mutex_t *m) {
    if (m->recursive && moy_mutex__mine(m)) {
        __atomic_add_fetch(&m->depth, 1u, __ATOMIC_RELAXED);
        return;
    }
    pthread_mutex_lock(&m->mu);
    m->owner = pthread_self();
    __atomic_store_n(&m->depth, 1u, __ATOMIC_RELEASE);
}

static inline bool moy_mutex_trylock(moy_mutex_t *m) {
    if (m->recursive && moy_mutex__mine(m)) {
        __atomic_add_fetch(&m->depth, 1u, __ATOMIC_RELAXED);
        return true;
    }
    if (pthread_mutex_trylock(&m->mu) != 0) {
        return false;
    }
    m->owner = pthread_self();
    __atomic_store_n(&m->depth, 1u, __ATOMIC_RELEASE);
    return true;
}

static inline void moy_mutex_unlock(moy_mutex_t *m) {
    if (__atomic_sub_fetch(&m->depth, 1u, __ATOMIC_RELEASE) == 0) {
        pthread_mutex_unlock(&m->mu);
    }
}

static inline void moy_mutex_destroy(moy_mutex_t *m) {
    pthread_mutex_destroy(&m->mu);
}
#else
typedef struct {
    bool recursive;
} moy_mutex_t;
#define MOY_MUTEX_INIT {false}
#define MOY_RMUTEX_INIT {true}

static inline void moy_mutex_init(moy_mutex_t *m, bool recursive) {
    m->recursive = recursive;
}

static inline void moy_mutex_lock(moy_mutex_t *m) {
    (void)m;
}

static inline bool moy_mutex_trylock(moy_mutex_t *m) {
    (void)m;
    return true;
}

static inline void moy_mutex_unlock(moy_mutex_t *m) {
    (void)m;
}

static inline void moy_mutex_destroy(moy_mutex_t *m) {
    (void)m;
}
#endif

#endif // MOY_PLATFORM_H
