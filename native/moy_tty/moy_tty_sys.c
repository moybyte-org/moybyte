// moy_tty_sys: the face's tasks, the hop to the console's task, the socket
// pair and the memory (moy_tty.h). Shared by the board and the host
// harnesses; what differs is behind ESP_PLATFORM.

#include "moy_tasks.h"

#include "moy_kmem.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "moy_tty.h"
#include "moy_tty_int.h"

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "lwip/sockets.h"

uint32_t moy_tty_ms(void) {
    return (uint32_t)moy_now_ms();
}

uintptr_t moy_tty_self(void) {
    return (uintptr_t)xTaskGetCurrentTaskHandle();
}

void moy_tty_nap(int ms) {
    ulTaskNotifyTake(pdTRUE, ms < 0 ? portMAX_DELAY : pdMS_TO_TICKS(ms) ? pdMS_TO_TICKS(ms) : 1);
}

void moy_tty_poke(uintptr_t t) {
    if (t != 0) {
        xTaskNotifyGive((TaskHandle_t)t);
    }
}

// The console's task waits with vTaskDelay: its notifications are
// MicroPython's (mp_hal_wake_main_task), never the face's.
void moy_tty_console_nap(int ms) {
    vTaskDelay(pdMS_TO_TICKS(ms) ? pdMS_TO_TICKS(ms) : 1);
}

// Priority 1 on core 0, the stack and the record PSRAM.
uintptr_t moy_tty_spawn(void (*fn)(void *arg), const char *name, uint32_t stack, void *arg) {
    TaskHandle_t h = NULL;
    if (!moy_task_spawn_as(MOY_TASK_TTY, name, stack, 0, 0, fn, arg, &h)) {
        return 0;
    }
    return (uintptr_t)h;
}

void *moy_tty_calloc(size_t n, size_t size) {
    return moy_kalloc(n * size, MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
}

void moy_tty_free(void *p) {
    heap_caps_free(p);
}

// lwIP has no socketpair: a listener on the loopback, connected once.
int moy_tty_pair(int fds[2]) {
    int l = socket(AF_INET, SOCK_STREAM, 0);
    if (l < 0) {
        return -1;
    }
    struct sockaddr_in a;
    socklen_t al = sizeof a;
    memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;
    int c = -1, s = -1;
    if (bind(l, (struct sockaddr *)&a, sizeof a) == 0 && listen(l, 1) == 0
            && getsockname(l, (struct sockaddr *)&a, &al) == 0) {
        c = socket(AF_INET, SOCK_STREAM, 0);
        if (c >= 0 && connect(c, (struct sockaddr *)&a, sizeof a) == 0) {
            s = accept(l, NULL, NULL);
        }
    }
    close(l);
    if (s < 0) {
        if (c >= 0) {
            close(c);
        }
        return -1;
    }
    int one = 1;
    setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    fds[0] = c;
    fds[1] = s;
    return 0;
}
#else
#include <pthread.h>
#include <time.h>

uint32_t moy_tty_ms(void) {
    return (uint32_t)moy_now_ms();
}

uintptr_t moy_tty_self(void) {
    return (uintptr_t)pthread_self();
}

void moy_tty_nap(int ms) {
    usleep((ms < 0 || ms > 2 ? 2 : ms) * 1000);
}

void moy_tty_poke(uintptr_t t) {
    (void)t;
}

void moy_tty_console_nap(int ms) {
    usleep(ms * 1000);
}

uintptr_t moy_tty_spawn(void (*fn)(void *arg), const char *name, uint32_t stack, void *arg) {
    (void)name;
    (void)stack;
    pthread_t t;
    if (pthread_create(&t, NULL, (void *(*)(void *))fn, arg) != 0) {
        return 0;
    }
    pthread_detach(t);
    return (uintptr_t)t;
}

void *moy_tty_calloc(size_t n, size_t size) {
    return moy_kalloc(n * size, MOY_KMEM_ZERO);
}

void moy_tty_free(void *p) {
    free(p);
}

int moy_tty_pair(int fds[2]) {
    return socketpair(AF_UNIX, SOCK_STREAM, 0, fds);
}
#endif

#define FACE_STACK (24 * 1024)

static moy_tty_state_t *s_t;

moy_tty_state_t *moy_tty_state(void) {
    return s_t;
}

int moy_tty_setup(void) {
    if (s_t == NULL) {
        s_t = moy_tty_calloc(1, sizeof *s_t);
        if (s_t == NULL) {
            return MOY_TTY_ENOMEM;
        }
    }
    s_t->tail = moy_tty_self();
    return 0;
}

// -- the console's task ---------------------------------------------------------

void moy_tty_hop(void (*fn)(void *arg), void *arg) {
    moy_tty_state_t *t = s_t;
    if (t == NULL || t->tail == 0 || moy_tty_self() == t->tail) {
        fn(arg);
        return;
    }
    moy_tty_hop_t h = { fn, arg, 0, moy_tty_self() };
    moy_tty_hop_t *none = NULL;
    // one hop at a time: the transports' and the face's take turns
    while (!__atomic_compare_exchange_n(&t->hop, &none, &h, 0, __ATOMIC_ACQ_REL,
                                        __ATOMIC_ACQUIRE)) {
        none = NULL;
        moy_tty_nap(5);
    }
    while (!__atomic_load_n(&h.done, __ATOMIC_ACQUIRE)) {
        moy_tty_nap(100);
    }
}

int moy_tty_serving(void) {
    return s_t != NULL && s_t->serving;
}

void moy_tty_tail(void) {
    moy_tty_state_t *t = s_t;
    if (t == NULL) {
        return;
    }
    t->tail = moy_tty_self();
    moy_tty_hop_t *h = __atomic_load_n(&t->hop, __ATOMIC_ACQUIRE);
    if (h != NULL && !t->serving) {
        t->serving = 1;
        h->fn(h->arg);
        t->serving = 0;
        uintptr_t w = h->waiter;
        __atomic_store_n(&t->hop, NULL, __ATOMIC_RELEASE);
        __atomic_store_n(&h->done, 1, __ATOMIC_RELEASE);
        moy_tty_poke(w);
    }
}

// -- the face task ------------------------------------------------------------------

// Runs each face that opens; naps while a transport holds it, parks while
// none does and no face runs.
static void face_task(void *arg) {
    moy_tty_state_t *t = arg;
    t->task = moy_tty_self();
    for (;;) {
        if (moy_tty_face_step(t)) {
            continue;
        }
        int held = __atomic_load_n(&t->holds, __ATOMIC_ACQUIRE);
        moy_tty_nap(held ? MOY_TTY_WAIT_MS : MOY_TTY_FOREVER);
    }
}

int moy_tty_hold(int via, int on) {
    if (moy_tty_setup_state() != 0) {
        return MOY_TTY_ENOMEM;
    }
    moy_tty_state_t *t = s_t;
    if (!on) {
        __atomic_and_fetch(&t->holds, ~via, __ATOMIC_ACQ_REL);
        return 0;
    }
    __atomic_or_fetch(&t->holds, via, __ATOMIC_ACQ_REL);
    if (t->task == 0) {
        t->task = moy_tty_spawn(face_task, "moy_tty", FACE_STACK, t);
        if (t->task == 0) {
            __atomic_and_fetch(&t->holds, ~via, __ATOMIC_ACQ_REL);
            return MOY_TTY_ENOMEM;
        }
    } else {
        moy_tty_poke(t->task);
    }
    return 0;
}

int moy_tty_setup_state(void) {
    if (s_t != NULL) {
        return 0;
    }
    s_t = moy_tty_calloc(1, sizeof *s_t);
    return s_t != NULL ? 0 : MOY_TTY_ENOMEM;
}
