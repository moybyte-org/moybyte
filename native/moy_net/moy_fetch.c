// moy_fetch: the `http` device API's driver (moy_fetch.h).

#include "moy_fetch.h"

#include <stdio.h>
#include <string.h>

#include "moy_net.h"
#include "moy_ota.h"

#if defined(__has_include)
#if __has_include("moy_input.h")
#define FETCH_EVENTS 1
#include "moy_input.h"
#endif
#endif

#if defined(ESP_PLATFORM) && defined(MOY_API_HTTP)
#define FETCH_TASK 1
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
#define LOCK() portENTER_CRITICAL(&s_mux)
#define UNLOCK() portEXIT_CRITICAL(&s_mux)
static TaskHandle_t s_task;
#else
#define LOCK()
#define UNLOCK()
#endif

typedef struct {
    uint32_t owner;
    uint16_t gen;
    uint8_t state;
    uint8_t closing;                // closed while the worker serves it
    uint32_t flags;
    uint32_t cap;
    int status;
    int err;
    char *url;                      // MOY_FETCH_URL_MAX, kernel memory
    char *body;
    uint32_t n;
} slot_t;

static slot_t s_slot[MOY_FETCH_SLOTS];
static uint32_t s_src;

static uint32_t handle_of(int i) {
    return (uint32_t)s_slot[i].gen << 8 | (uint32_t)(i + 1);
}

static slot_t *slot_at(uint32_t h) {
    int i = (int)(h & 0xffu) - 1;
    if (i < 0 || i >= MOY_FETCH_SLOTS || s_slot[i].state == MOY_FETCH_FREE
        || s_slot[i].gen != (uint16_t)(h >> 8)) {
        return NULL;
    }
    return &s_slot[i];
}

static void slot_free(slot_t *s) {
    moy_net_free(s->url);
    moy_net_free(s->body);
    s->url = NULL;
    s->body = NULL;
    s->n = 0;
    s->closing = 0;
    s->gen = (uint16_t)((s->gen + 1u) & 0xffffu);
    s->state = MOY_FETCH_FREE;
}

static void post(uint32_t h, uint8_t state, int status) {
    #ifdef FETCH_EVENTS
    moy_input_t *t = moy_input_kernel();
    if (t != NULL && s_src != 0) {
        moy_input_post(t, s_src, MOY_EV_NET, (uint16_t)h, state, (uint32_t)status);
    }
    #else
    (void)h;
    (void)state;
    (void)status;
    #endif
}

static void open_source(void) {
    #ifdef FETCH_EVENTS
    if (s_src == 0) {
        moy_input_t *t = moy_input_kernel();
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "http", MOY_EV_FIFO, 16, &src) == MOY_INPUT_OK) {
            s_src = src;
        }
    }
    #endif
}

// One queued request served: the worker's, off the VM's thread on a board.
static void serve(int i) {
    slot_t *s = &s_slot[i];
    LOCK();
    if (s->state != MOY_FETCH_QUEUED) {
        UNLOCK();
        return;
    }
    s->state = MOY_FETCH_RUNNING;
    UNLOCK();
    int status = 0;
    char *body = NULL;
    size_t n = 0;
    int rc = moy_httpc_get_ex(s->url, MOY_FETCH_AGENT,
                              MOY_HTTPC_WORKER | ((s->flags & MOY_FETCH_APP) ? MOY_HTTPC_PLAIN : 0),
                              s->cap, &status, &body, &n);
    uint32_t h = handle_of(i);
    LOCK();
    if (s->closing) {
        UNLOCK();
        moy_net_free(body);
        slot_free(s);
        return;
    }
    s->body = body;
    s->n = (uint32_t)n;
    s->status = rc == 0 ? status : 0;
    s->err = rc == 0 ? 0 : -rc;
    s->state = rc == 0 ? MOY_FETCH_DONE : MOY_FETCH_FAILED;
    UNLOCK();
    post(h, s->state, rc == 0 ? status : rc);
}

void moy_fetch_run(void) {
    for (int i = 0; i < MOY_FETCH_SLOTS; i++) {
        serve(i);
    }
}

#ifdef FETCH_TASK
static void worker(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        moy_fetch_run();
    }
}
#endif

static int start(void) {
    #ifdef FETCH_TASK
    if (s_task == NULL
        && xTaskCreatePinnedToCore(worker, "moy_fetch", 6144, NULL, 5, &s_task, 0) != pdPASS) {
        s_task = NULL;
        return -MOY_FETCH_ENOMEM;
    }
    xTaskNotifyGive(s_task);
    #endif
    return 0;
}

int moy_fetch_present(void) {
    #if defined(ESP_PLATFORM) && !defined(MOY_API_HTTP)
    return 0;
    #else
    return 1;
    #endif
}

int moy_fetch_open(uint32_t owner, const char *url, uint32_t cap, uint32_t flags, uint32_t *h) {
    int tls;
    char host[MOY_URL_HOST_MAX];
    uint16_t port;
    const char *path;
    if (!moy_fetch_present()) {
        return -MOY_FETCH_EINVAL;
    }
    size_t ul = strlen(url);
    if (cap == 0 || cap > MOY_FETCH_CAP_MAX || ul >= MOY_FETCH_URL_MAX
        || moy_url_parse(url, &tls, host, sizeof(host), &port, &path) != 0) {
        return -MOY_FETCH_EINVAL;
    }
    if (tls && (flags & MOY_FETCH_APP)) {
        return -MOY_HTTPC_ETLS;
    }
    if (moy_httpc_init() != 0) {
        return -MOY_FETCH_ENOMEM;
    }
    int i = 0;
    while (i < MOY_FETCH_SLOTS && s_slot[i].state != MOY_FETCH_FREE) {
        i++;
    }
    if (i == MOY_FETCH_SLOTS) {
        return -MOY_FETCH_ENOSPC;
    }
    slot_t *s = &s_slot[i];
    s->url = moy_net_alloc(ul + 1);
    if (s->url == NULL) {
        return -MOY_FETCH_ENOMEM;
    }
    memcpy(s->url, url, ul + 1);
    open_source();
    s->owner = owner;
    s->flags = flags;
    s->cap = cap;
    s->status = 0;
    s->err = 0;
    s->body = NULL;
    s->n = 0;
    s->closing = 0;
    LOCK();
    s->state = MOY_FETCH_QUEUED;
    UNLOCK();
    *h = handle_of(i);
    int rc = start();
    if (rc != 0) {
        slot_free(s);
        return rc;
    }
    return 0;
}

int moy_fetch_state(uint32_t h, int *status, uint32_t *n, int *err) {
    slot_t *s = slot_at(h);
    if (s == NULL || s->closing) {
        return -MOY_FETCH_EBADF;
    }
    LOCK();
    int st = s->state;
    *status = s->status;
    *n = st == MOY_FETCH_DONE ? s->n : 0;
    *err = s->err;
    UNLOCK();
    return st;
}

int moy_fetch_read(uint32_t h, uint32_t off, void *buf, uint32_t n) {
    slot_t *s = slot_at(h);
    if (s == NULL || s->closing || s->state != MOY_FETCH_DONE) {
        return -MOY_FETCH_EBADF;
    }
    if (off >= s->n) {
        return 0;
    }
    uint32_t k = s->n - off < n ? s->n - off : n;
    memcpy(buf, s->body + off, k);
    return (int)k;
}

int moy_fetch_close(uint32_t h) {
    slot_t *s = slot_at(h);
    if (s == NULL || s->closing) {
        return -MOY_FETCH_EBADF;
    }
    LOCK();
    if (s->state == MOY_FETCH_RUNNING) {
        s->closing = 1;
        UNLOCK();
        return 0;
    }
    UNLOCK();
    slot_free(s);
    return 0;
}

uint32_t moy_fetch_sweep(uint32_t owner) {
    uint32_t k = 0;
    for (int i = 0; i < MOY_FETCH_SLOTS; i++) {
        if (s_slot[i].state != MOY_FETCH_FREE && !s_slot[i].closing && s_slot[i].owner == owner
            && moy_fetch_close(handle_of(i)) == 0) {
            k++;
        }
    }
    return k;
}

uint32_t moy_fetch_count(uint32_t owner) {
    uint32_t k = 0;
    for (int i = 0; i < MOY_FETCH_SLOTS; i++) {
        if (s_slot[i].state != MOY_FETCH_FREE && !s_slot[i].closing
            && (owner == 0 || s_slot[i].owner == owner)) {
            k++;
        }
    }
    return k;
}

uint32_t moy_fetch_source(void) {
    return s_src;
}

void moy_fetch_reset(void) {
    for (int i = 0; i < MOY_FETCH_SLOTS; i++) {
        if (s_slot[i].state != MOY_FETCH_FREE) {
            slot_free(&s_slot[i]);
        }
    }
    #ifdef FETCH_EVENTS
    if (s_src != 0) {
        moy_input_ring_close(moy_input_kernel(), s_src);
    }
    #endif
    s_src = 0;
}
