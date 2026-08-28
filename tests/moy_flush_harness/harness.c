// The deterministic stand-in for FreeRTOS + the ESP-IDF surface. See
// harness.h for why it is cooperative and not threaded.
#define _GNU_SOURCE

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ucontext.h>

#include "harness.h"

#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_heap_caps.h"
#include "py/mphal.h"
#include "py/obj.h"
#include "py/objtuple.h"

// ---------------------------------------------------------------------------
// Contexts
// ---------------------------------------------------------------------------

#define H_MAX_TASKS 4
#define H_STACK_BYTES (256 * 1024)

enum { H_RUNNABLE, H_BLOCKED, H_DONE };

typedef struct {
    ucontext_t ctx;
    char *stack;
    const char *name;
    int state;
    int64_t wake_us;                 // INT64_MAX = no timeout
    const void *waiting_on;
    uint32_t notify;
    TaskFunction_t fn;
    void *arg;
    int used;
} h_task_t;

static h_task_t g_tasks[H_MAX_TASKS];
static int g_ntasks = 1;             // 0 is the VM context, always present
static int g_cur;
static int g_task_creates;
static bool g_fail_task_create;

static const int g_notify_obj = 0;   // waiting_on sentinels
static const int g_delay_obj = 0;

static int64_t g_now = 5000000;      // not zero: nothing may assume a t0 of 0
static int64_t g_isr_latency;
static int64_t g_watchdog = INT64_MAX;
static long g_switches;

const char *h_scenario = "?";

// ---------------------------------------------------------------------------
// Failure
// ---------------------------------------------------------------------------

void h_fail(const char *fmt, ...) {
    va_list ap;
    printf("FAIL %s: ", h_scenario);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n  (virtual clock %lld us, context %d/%s)\n", (long long)g_now,
           g_cur, g_tasks[g_cur].name ? g_tasks[g_cur].name : "vm");
    fflush(stdout);
    exit(1);
}

// ---------------------------------------------------------------------------
// The completion-event queue (the done-ISR)
// ---------------------------------------------------------------------------

#define H_MAX_EVENTS 256

typedef struct {
    int64_t when;
    h_ev_fn fn;
    void *arg;
    bool used;
    unsigned seq;                    // ties break in insertion order
} h_ev_t;

static h_ev_t g_events[H_MAX_EVENTS];
static unsigned g_ev_seq;

void h_at(int64_t when_us, h_ev_fn fn, void *arg) {
    for (int i = 0; i < H_MAX_EVENTS; i++) {
        if (!g_events[i].used) {
            g_events[i].when = when_us;
            g_events[i].fn = fn;
            g_events[i].arg = arg;
            g_events[i].seq = g_ev_seq++;
            g_events[i].used = true;
            return;
        }
    }
    h_fail("event queue full");
}

int h_pending_events(void) {
    int n = 0;
    for (int i = 0; i < H_MAX_EVENTS; i++) {
        if (g_events[i].used) { n++; }
    }
    return n;
}

static int h_earliest_event(void) {
    int best = -1;
    for (int i = 0; i < H_MAX_EVENTS; i++) {
        if (!g_events[i].used) { continue; }
        if (best < 0 || g_events[i].when < g_events[best].when
                || (g_events[i].when == g_events[best].when
                    && g_events[i].seq < g_events[best].seq)) {
            best = i;
        }
    }
    return best;
}

static void h_fire(int i) {
    h_ev_t ev = g_events[i];
    g_events[i].used = false;
    if (ev.when > g_now) { g_now = ev.when; }
    ev.fn(ev.arg);
}

// ---------------------------------------------------------------------------
// The clock and the scheduler
// ---------------------------------------------------------------------------

int64_t h_now(void) { return g_now; }

// Moves the watchdog with the clock: a scenario that starts near the 32-bit
// wrap is not spending its budget to get there.
void h_set_clock(int64_t us) {
    if (g_watchdog != INT64_MAX) { g_watchdog += us - g_now; }
    g_now = us;
}

void h_set_isr_latency(int64_t us) { g_isr_latency = us; }

void h_set_watchdog(int64_t us_from_now) { g_watchdog = g_now + us_from_now; }

static void h_watchdog_check(void) {
    if (g_now > g_watchdog) {
        h_fail("watchdog: the virtual clock ran past the scenario budget -- a "
               "wait that should be bounded is not");
    }
    if (++g_switches > 4000000L) {
        h_fail("watchdog: 4M context switches -- a loop that should terminate "
               "does not");
    }
}

static void h_wake_due(void) {
    for (int i = 0; i < g_ntasks; i++) {
        if (g_tasks[i].state == H_BLOCKED && g_tasks[i].wake_us <= g_now) {
            g_tasks[i].state = H_RUNNABLE;
        }
    }
}

static bool h_any_runnable(void) {
    for (int i = 0; i < g_ntasks; i++) {
        if (g_tasks[i].state == H_RUNNABLE) { return true; }
    }
    return false;
}

static int64_t h_next_wake(void) {
    int64_t best = INT64_MAX;
    for (int i = 0; i < g_ntasks; i++) {
        if (g_tasks[i].state == H_BLOCKED && g_tasks[i].wake_us < best) {
            best = g_tasks[i].wake_us;
        }
    }
    int e = h_earliest_event();
    if (e >= 0 && g_events[e].when < best) { best = g_events[e].when; }
    return best;
}

// Advance to `target`, firing every completion that falls on the way. The
// ISR-to-resume latency is NOT applied here but on the woken context's own
// wake time (vTaskNotifyGiveFromISR): an interrupt still fires on time even
// when the task it wakes is scheduled late, and only the second half is what
// last_flush_us must not fold in.
static void h_advance_to(int64_t target) {
    for (;;) {
        int e = h_earliest_event();
        if (e >= 0 && g_events[e].when <= target) {
            h_fire(e);
            h_wake_due();
            if (h_any_runnable()) { return; }
            continue;
        }
        break;
    }
    if (target > g_now) { g_now = target; }
    h_wake_due();
}

static void h_switch_to(int next) {
    int prev = g_cur;
    if (next == prev) { return; }
    g_cur = next;
    swapcontext(&g_tasks[prev].ctx, &g_tasks[next].ctx);
}

static int h_pick_next(void) {
    for (int n = 1; n <= g_ntasks; n++) {
        int i = (g_cur + n) % g_ntasks;
        if (g_tasks[i].state == H_RUNNABLE) { return i; }
    }
    return g_tasks[g_cur].state == H_RUNNABLE ? g_cur : -1;
}

// Called by whoever just blocked (or yielded). Returns when this context is
// scheduled again -- or never, for a deleted one.
static void h_dispatch(void) {
    for (;;) {
        h_watchdog_check();
        int n = h_pick_next();
        if (n >= 0) {
            h_switch_to(n);
            return;
        }
        int64_t next = h_next_wake();
        if (next == INT64_MAX) {
            h_fail("deadlock: every context is blocked forever and no "
                   "completion is pending");
        }
        h_advance_to(next);
    }
}

static void h_block_on(const void *obj, int64_t wake_us) {
    h_task_t *me = &g_tasks[g_cur];
    me->state = H_BLOCKED;
    me->waiting_on = obj;
    me->wake_us = wake_us;
    h_dispatch();
    me->waiting_on = NULL;
    me->wake_us = INT64_MAX;
}

void h_yield(void) { h_dispatch(); }

void h_block_us(int64_t us) { h_block_on(&g_delay_obj, g_now + us); }

void h_advance(int64_t us) {
    int64_t target = g_now + us;
    for (;;) {
        int e = h_earliest_event();
        if (e >= 0 && g_events[e].when <= target) { h_fire(e); continue; }
        break;
    }
    if (target > g_now) { g_now = target; }
    h_wake_due();
    h_watchdog_check();
}

void h_isr_yield_requested(void) { /* the yield is the scheduler's, below */ }

// ---------------------------------------------------------------------------
// Tasks
// ---------------------------------------------------------------------------

static void h_trampoline(void) {
    h_task_t *me = &g_tasks[g_cur];
    me->fn(me->arg);
    h_fail("task %s returned without vTaskDelete", me->name);
}

BaseType_t xTaskCreatePinnedToCore(TaskFunction_t pxTaskCode,
                                   const char *const pcName,
                                   const uint32_t usStackDepth,
                                   void *const pvParameters,
                                   UBaseType_t uxPriority,
                                   TaskHandle_t *const pxCreatedTask,
                                   const BaseType_t xCoreID) {
    (void)usStackDepth;
    g_task_creates++;
    if (g_fail_task_create) { return pdFAIL; }
    if (g_ntasks >= H_MAX_TASKS) { h_fail("too many tasks"); }
    // The feeder's placement contract, checked against LITERALS rather than
    // against the header's own macros (which would assert nothing): core 0,
    // because MicroPython's VM task is pinned to core 1, and priority 12,
    // below WiFi's 23 and lwIP's 18.
    CHECK_EQ(xCoreID, 0);
    CHECK_EQ((int)uxPriority, 12);
    int i = g_ntasks++;
    h_task_t *t = &g_tasks[i];
    memset(t, 0, sizeof(*t));
    t->stack = malloc(H_STACK_BYTES);
    if (t->stack == NULL) { h_fail("no memory for a task stack"); }
    t->name = pcName;
    t->fn = pxTaskCode;
    t->arg = pvParameters;
    t->state = H_RUNNABLE;
    t->wake_us = INT64_MAX;
    t->used = 1;
    getcontext(&t->ctx);
    t->ctx.uc_stack.ss_sp = t->stack;
    t->ctx.uc_stack.ss_size = H_STACK_BYTES;
    t->ctx.uc_link = NULL;
    makecontext(&t->ctx, h_trampoline, 0);
    if (pxCreatedTask) { *pxCreatedTask = t; }
    return pdPASS;
}

void vTaskDelete(TaskHandle_t xTaskToDelete) {
    h_task_t *me = &g_tasks[g_cur];
    if (xTaskToDelete != NULL && xTaskToDelete != me) {
        h_fail("vTaskDelete of another task is not modelled");
    }
    me->state = H_DONE;
    h_dispatch();
    h_fail("a deleted task was resumed");
}

uint32_t ulTaskNotifyTake(BaseType_t xClearCountOnExit, TickType_t xTicksToWait) {
    h_task_t *me = &g_tasks[g_cur];
    if (me->notify == 0 && xTicksToWait != 0) {
        h_block_on(&g_notify_obj,
                   xTicksToWait == portMAX_DELAY
                       ? INT64_MAX
                       : g_now + (int64_t)xTicksToWait * H_TICK_US);
    }
    uint32_t v = me->notify;
    if (xClearCountOnExit) {
        me->notify = 0;
    } else if (v) {
        me->notify--;
    }
    return v;
}

void vTaskNotifyGiveFromISR(TaskHandle_t xTaskToNotify,
                            BaseType_t *pxHigherPriorityTaskWoken) {
    // Both of these are what the engine's ONE volatile read of moy_flush.task
    // exists to prevent; they are failures here rather than undefined
    // behaviour, so deleting that guard is a red scenario.
    if (xTaskToNotify == NULL) {
        h_fail("the done-ISR notified a NULL task handle");
    }
    h_task_t *t = xTaskToNotify;
    if (t->state == H_DONE) {
        h_fail("the done-ISR notified a task that has already exited");
    }
    t->notify++;
    if (t->state == H_BLOCKED && t->waiting_on == &g_notify_obj) {
        int64_t ready = g_now + g_isr_latency;
        if (ready <= g_now) {
            t->state = H_RUNNABLE;
        } else if (ready < t->wake_us) {
            t->wake_us = ready;          // scheduled, not yet running
        }
        if (pxHigherPriorityTaskWoken) { *pxHigherPriorityTaskWoken = pdTRUE; }
    }
}

void mp_hal_delay_ms(uint32_t ms) { h_block_us((int64_t)ms * 1000); }

// ---------------------------------------------------------------------------
// Binary semaphores
// ---------------------------------------------------------------------------

struct h_sem {
    int count;
    int created;
};

#define H_MAX_SEMS 8
static struct h_sem g_sems[H_MAX_SEMS];
static int g_nsems;
static int g_sem_fail_at = -1;
static struct h_sem *g_switch_give;

void h_fail_sem_at(int nth) { g_sem_fail_at = nth; }

void h_switch_on_give(struct h_sem *s) { g_switch_give = s; }

SemaphoreHandle_t xSemaphoreCreateBinary(void) {
    if (g_sem_fail_at >= 0 && g_nsems == g_sem_fail_at) {
        g_nsems++;
        return NULL;
    }
    if (g_nsems >= H_MAX_SEMS) { h_fail("too many semaphores"); }
    struct h_sem *s = &g_sems[g_nsems++];
    s->count = 0;
    s->created = 1;
    return s;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) {
    if (sem == NULL) { h_fail("take on a NULL semaphore"); }
    if (sem->count > 0) { sem->count = 0; return pdTRUE; }
    if (ticks == 0) { return pdFALSE; }
    h_block_on(sem, ticks == portMAX_DELAY
                        ? INT64_MAX
                        : g_now + (int64_t)ticks * H_TICK_US);
    if (sem->count > 0) { sem->count = 0; return pdTRUE; }
    return pdFALSE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t sem) {
    if (sem == NULL) { h_fail("give on a NULL semaphore"); }
    sem->count = 1;                              // binary: never counts up
    for (int i = 0; i < g_ntasks; i++) {
        if (g_tasks[i].state == H_BLOCKED && g_tasks[i].waiting_on == sem) {
            g_tasks[i].state = H_RUNNABLE;
            break;
        }
    }
    if (sem == g_switch_give) { h_yield(); }
    return pdTRUE;
}

// ---------------------------------------------------------------------------
// The allocation registry
// ---------------------------------------------------------------------------

#define H_MAX_BLOCKS 16

typedef struct {
    void *p;
    size_t size;
    uint32_t caps;
    bool freed;
} h_block_t;

static h_block_t g_blocks[H_MAX_BLOCKS];
static int g_nblocks;
static int g_malloc_n;
static int g_malloc_fail_at = -1;

void h_fail_malloc_at(int nth) { g_malloc_fail_at = nth; }

void h_fail_task_create(bool on) { g_fail_task_create = on; }

static h_block_t *h_find(const void *p) {
    for (int i = 0; i < g_nblocks; i++) {
        if (g_blocks[i].p == p) { return &g_blocks[i]; }
    }
    return NULL;
}

void *heap_caps_malloc(size_t size, uint32_t caps) {
    if (g_malloc_fail_at >= 0 && g_malloc_n == g_malloc_fail_at) {
        g_malloc_n++;
        return NULL;
    }
    g_malloc_n++;
    if (g_nblocks >= H_MAX_BLOCKS) { h_fail("too many heap blocks"); }
    void *p = malloc(size);
    if (p == NULL) { h_fail("the host is out of memory"); }
    memset(p, 0, size);
    g_blocks[g_nblocks].p = p;
    g_blocks[g_nblocks].size = size;
    g_blocks[g_nblocks].caps = caps;
    g_blocks[g_nblocks].freed = false;
    g_nblocks++;
    return p;
}

// Poisoned, remembered, NOT returned to libc -- see harness.h. A write into a
// freed bounce slot must be catchable, not merely lucky.
void heap_caps_free(void *ptr) {
    if (ptr == NULL) { return; }
    h_block_t *b = h_find(ptr);
    if (b == NULL) { h_fail("free of a pointer this harness never handed out"); }
    if (b->freed) { h_fail("double free"); }
    memset(b->p, 0xDE, b->size);
    b->freed = true;
}

bool h_is_freed(const void *p) {
    h_block_t *b = h_find(p);
    if (b == NULL) { h_fail("h_is_freed on an unknown pointer"); }
    return b->freed;
}

int h_live_allocs(void) {
    int n = 0;
    for (int i = 0; i < g_nblocks; i++) {
        if (!g_blocks[i].freed) { n++; }
    }
    return n;
}

size_t h_alloc_size(const void *p) {
    h_block_t *b = h_find(p);
    if (b == NULL) { h_fail("h_alloc_size on an unknown pointer"); }
    return b->size;
}

uint32_t h_alloc_caps(const void *p) {
    h_block_t *b = h_find(p);
    if (b == NULL) { h_fail("h_alloc_caps on an unknown pointer"); }
    return b->caps;
}

// ---------------------------------------------------------------------------
// The GIL
// ---------------------------------------------------------------------------

static int g_gil_exits;
static int g_gil_depth;

void h_gil_exit(void) {
    g_gil_exits++;
    if (--g_gil_depth < -1) { h_fail("the GIL was released twice over"); }
}

void h_gil_enter(void) {
    if (++g_gil_depth > 0) { h_fail("the GIL was taken without releasing it"); }
}

int h_gil_exits(void) { return g_gil_exits; }
int h_gil_depth(void) { return g_gil_depth; }

// ---------------------------------------------------------------------------
// MicroPython objects
// ---------------------------------------------------------------------------

struct h_mp_obj {
    long long ival;
    bool is_unsigned;
    bool is_tuple;
    int n;
    const struct h_mp_obj *items[16];
};

#define H_MAX_OBJS 64
static struct h_mp_obj g_objs[H_MAX_OBJS];
static int g_nobjs;

static struct h_mp_obj *h_obj_new(void) {
    if (g_nobjs >= H_MAX_OBJS) { h_fail("too many mp objects"); }
    struct h_mp_obj *o = &g_objs[g_nobjs++];
    memset(o, 0, sizeof(*o));
    return o;
}

mp_obj_t mp_obj_new_int(long long v) {
    struct h_mp_obj *o = h_obj_new();
    o->ival = v;
    return o;
}

mp_obj_t mp_obj_new_int_from_uint(unsigned long long v) {
    struct h_mp_obj *o = h_obj_new();
    o->ival = (long long)v;
    o->is_unsigned = true;
    return o;
}

mp_obj_t mp_obj_new_tuple(size_t n, const mp_obj_t *items) {
    struct h_mp_obj *o = h_obj_new();
    o->is_tuple = true;
    o->n = (int)n;
    if (n > 16) { h_fail("tuple too long for the harness"); }
    for (size_t i = 0; i < n; i++) { o->items[i] = items[i]; }
    return o;
}

int h_tuple_len(const struct h_mp_obj *t) {
    if (!t->is_tuple) { h_fail("not a tuple"); }
    return t->n;
}

long long h_tuple_int(const struct h_mp_obj *t, int i) {
    if (!t->is_tuple || i >= t->n) { h_fail("tuple index %d", i); }
    return t->items[i]->ival;
}

bool h_tuple_is_unsigned(const struct h_mp_obj *t, int i) {
    if (!t->is_tuple || i >= t->n) { h_fail("tuple index %d", i); }
    return t->items[i]->is_unsigned;
}

// ---------------------------------------------------------------------------
// Entry
// ---------------------------------------------------------------------------

void h_harness_init(const char *scenario) {
    h_scenario = scenario;
    g_tasks[0].state = H_RUNNABLE;
    g_tasks[0].name = "vm";
    g_tasks[0].wake_us = INT64_MAX;
    g_tasks[0].used = 1;
    h_set_watchdog(30 * 1000 * 1000);
}

int h_task_creates(void) { return g_task_creates; }
