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
// ESP_PLATFORM is not defined for a usermod's sources on the esp32 port, so a
// board is recognised by the header it has.
#if __has_include("esp_heap_caps.h")
#define FETCH_ON_BOARD 1
#endif
#endif

#if defined(FETCH_ON_BOARD) && defined(MOY_API_HTTP)
#define FETCH_TASK 1
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
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
    uint32_t cap;
    int status;
    int err;
    char *url;                      // MOY_FETCH_URL_MAX, kernel memory
    char *body;                     // a stream's: its window, a ring
    uint32_t n;
    uint8_t stream;
    int hc;                         // a stream's connection while its body comes, else 0
    uint32_t wn, rn;                // a stream's bytes in, bytes taken
} slot_t;

static slot_t *s_slot;                  // MOY_FETCH_SLOTS, kernel memory at the first open
static uint32_t s_src;

static uint32_t handle_of(int i) {
    return (uint32_t)s_slot[i].gen << 8 | (uint32_t)(i + 1);
}

static slot_t *slot_at(uint32_t h) {
    int i = (int)(h & 0xffu) - 1;
    if (s_slot == NULL || i < 0 || i >= MOY_FETCH_SLOTS || s_slot[i].state == MOY_FETCH_FREE
        || s_slot[i].gen != (uint16_t)(h >> 8)) {
        return NULL;
    }
    return &s_slot[i];
}

static void slot_free(slot_t *s) {
    if (s->hc > 0) {
        moy_httpc_close(s->hc);
    }
    s->hc = 0;
    s->stream = 0;
    s->wn = s->rn = 0;
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
    moy_input_t *t = moy_input_kernel();
    char was[8];
    if (s_src != 0 && (t == NULL || moy_input_source_name(t, s_src, was, sizeof(was)) != MOY_INPUT_OK
                       || strcmp(was, "http") != 0)) {
        s_src = 0;                      // the table it was opened on is gone
    }
    if (s_src == 0) {
        uint32_t src;
        if (t != NULL && moy_input_ring_open(t, "http", MOY_EV_FIFO, 16, &src) == MOY_INPUT_OK) {
            s_src = src;
        }
    }
    #endif
}

#ifdef FETCH_TASK
extern int moy_net_dialing(void) __attribute__((weak));

// The station the console asked for, waited for here rather than on the VM:
// a request made while it associates resolves its host once it has an address.
static void await_link(slot_t *s) {
    uint32_t t0 = moy_net_ms();
    while (moy_net_dialing != NULL && moy_net_dialing() && !s->closing
           && moy_net_ms() - t0 < MOY_NET_DIAL_MS) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
#endif

// -- a stream (moy_fetch_stream) --------------------------------------------------
//
// Its head is read as a request's is; then its body comes into the window, a
// ring the worker fills and moy_fetch_take empties, a read at a time: the
// worker waits while the window is full, so a download of any size holds no
// more than its window. One stream at a time keeps the worker (a request
// queued behind it waits).

static void stream_end(int i, int state, int err) {
    slot_t *s = &s_slot[i];
    moy_httpc_close(s->hc);
    LOCK();
    s->hc = 0;
    s->err = err;
    s->state = (uint8_t)state;
    UNLOCK();
    post(handle_of(i), (uint8_t)state, state == MOY_FETCH_DONE ? s->status : -err);
}

static void stream_head(int i) {
    slot_t *s = &s_slot[i];
    int status = 0;
    uint32_t clen = 0;                  // the window takes the body whatever it says
    int hc = moy_httpc_open_ex(s->url, MOY_FETCH_AGENT, MOY_HTTPC_WORKER, &status, &clen);
    if (s->closing) {
        s->hc = hc > 0 ? hc : 0;
        slot_free(s);                   // closed while its head came
        return;
    }
    LOCK();
    if (hc > 0) {
        s->hc = hc;
        s->status = status;
        s->state = MOY_FETCH_STREAM;
        UNLOCK();
        return;
    }
    s->err = -hc;
    s->state = MOY_FETCH_FAILED;
    UNLOCK();
    post(handle_of(i), MOY_FETCH_FAILED, hc);
}

// What came of the stream's body into its window: 1 when bytes came.
static int stream_pump(int i) {
    slot_t *s = &s_slot[i];
    LOCK();
    uint32_t used = s->wn - s->rn;
    UNLOCK();
    uint32_t room = s->cap - used;
    if (room == 0) {
        return 0;
    }
    uint32_t at = s->wn % s->cap;
    uint32_t k = s->cap - at < room ? s->cap - at : room;
    int r = moy_httpc_read_wait(s->hc, s->body + at, k, 20);
    if (r == -MOY_HTTPC_EAGAIN) {
        return 0;
    }
    if (r <= 0) {
        stream_end(i, r == 0 ? MOY_FETCH_DONE : MOY_FETCH_FAILED, r == 0 ? 0 : -r);
        return 0;
    }
    LOCK();
    s->wn += (uint32_t)r;
    UNLOCK();
    return 1;
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
    #ifdef FETCH_TASK
    await_link(s);
    #endif
    if (s->stream) {
        stream_head(i);
        return;
    }
    int status = 0;
    char *body = NULL;
    size_t n = 0;
    int rc = moy_httpc_get_ex(s->url, MOY_FETCH_AGENT, MOY_HTTPC_WORKER, s->cap, &status,
                              &body, &n);
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

// Every queued request served, and each stream's body read on until its
// window is full or nothing more has come: 1 while a stream is open.
static int run_once(void) {
    int streams = 0;
    for (int i = 0; s_slot != NULL && i < MOY_FETCH_SLOTS; i++) {
        serve(i);
        slot_t *s = &s_slot[i];
        if (s->state == MOY_FETCH_STREAM && s->hc > 0 && !s->closing) {
            for (int k = 0; k < 64 && stream_pump(i); k++) {
            }
        }
        if (s->stream && s->closing && (s->state == MOY_FETCH_STREAM
                                        || s->state == MOY_FETCH_DONE
                                        || s->state == MOY_FETCH_FAILED)) {
            slot_free(s);               // closed while its body came
            continue;
        }
        streams += s->state == MOY_FETCH_STREAM && s->hc > 0;
    }
    return streams;
}

void moy_fetch_run(void) {
    run_once();
}

#ifdef FETCH_TASK
static void worker(void *arg) {
    (void)arg;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        while (run_once()) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
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
    #if defined(FETCH_ON_BOARD) && !defined(MOY_API_HTTP)
    return 0;
    #else
    return 1;
    #endif
}

int moy_fetch_tls(void) {
    return moy_conn_verifies();
}

static int open_slot(uint32_t owner, const char *url, uint32_t cap, int stream, uint32_t *h);

int moy_fetch_open(uint32_t owner, const char *url, uint32_t cap, uint32_t *h) {
    return open_slot(owner, url, cap, 0, h);
}

int moy_fetch_stream(uint32_t owner, const char *url, uint32_t window, uint32_t *h) {
    return open_slot(owner, url, window, 1, h);
}

static int open_slot(uint32_t owner, const char *url, uint32_t cap, int stream, uint32_t *h) {
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
    if (tls && !moy_conn_verifies()) {
        return -MOY_HTTPC_ETLS;
    }
    if (moy_httpc_init() != 0) {
        return -MOY_FETCH_ENOMEM;
    }
    if (s_slot == NULL) {
        slot_t *t = moy_net_alloc(sizeof(slot_t) * MOY_FETCH_SLOTS);
        if (t == NULL) {
            return -MOY_FETCH_ENOMEM;
        }
        memset(t, 0, sizeof(slot_t) * MOY_FETCH_SLOTS);
        s_slot = t;
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
    if (stream) {
        s->body = moy_net_alloc(cap);
        if (s->body == NULL) {
            moy_net_free(s->url);
            s->url = NULL;
            return -MOY_FETCH_ENOMEM;
        }
    }
    open_source();
    s->owner = owner;
    s->cap = cap;
    s->status = 0;
    s->err = 0;
    if (!stream) {
        s->body = NULL;
    }
    s->n = 0;
    s->closing = 0;
    s->stream = (uint8_t)stream;
    s->hc = 0;
    s->wn = s->rn = 0;
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
    *n = s->stream ? s->wn - s->rn : st == MOY_FETCH_DONE ? s->n : 0;
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

int moy_fetch_take(uint32_t h, void *buf, uint32_t n) {
    slot_t *s = slot_at(h);
    if (s == NULL || s->closing || !s->stream) {
        return -MOY_FETCH_EBADF;
    }
    LOCK();
    uint32_t have = s->wn - s->rn;
    UNLOCK();
    uint32_t k = have < n ? have : n;
    uint32_t at = s->rn % s->cap;
    uint32_t first = s->cap - at < k ? s->cap - at : k;
    memcpy(buf, s->body + at, first);
    memcpy((char *)buf + first, s->body, k - first);
    LOCK();
    s->rn += k;
    UNLOCK();
    return (int)k;
}

int moy_fetch_close(uint32_t h) {
    slot_t *s = slot_at(h);
    if (s == NULL || s->closing) {
        return -MOY_FETCH_EBADF;
    }
    LOCK();
    #ifdef FETCH_TASK
    int serving = s->state == MOY_FETCH_RUNNING || (s->state == MOY_FETCH_STREAM && s->hc > 0);
    #else
    int serving = s->state == MOY_FETCH_RUNNING;
    #endif
    if (serving) {
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
    for (int i = 0; s_slot != NULL && i < MOY_FETCH_SLOTS; i++) {
        if (s_slot[i].state != MOY_FETCH_FREE && !s_slot[i].closing && s_slot[i].owner == owner
            && moy_fetch_close(handle_of(i)) == 0) {
            k++;
        }
    }
    return k;
}

uint32_t moy_fetch_count(uint32_t owner) {
    uint32_t k = 0;
    for (int i = 0; s_slot != NULL && i < MOY_FETCH_SLOTS; i++) {
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
    for (int i = 0; s_slot != NULL && i < MOY_FETCH_SLOTS; i++) {
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
