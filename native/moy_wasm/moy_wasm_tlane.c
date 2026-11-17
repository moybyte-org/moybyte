// The session's TIMED LANES (moy_wasm_session.h has the contract;
// docs/os_2026-11/hardware.md section 2 the design).
//
// Each lane is a pthread (WAMR's ESP-IDF platform names its threads by
// pthread_self, which a bare FreeRTOS task has not) pinned to the core the
// session is not on, at MOY_WASM_TLANE_PRIO: above the board's own tasks there
// (the input, the radios' readers, the media decode), below the esp_timer
// task the monitor and the timers run on and the WiFi driver's. The audio
// lane wakes on the monitor's tick and fills the ring while it has room; the
// timer lane wakes on its one-shot esp_timer.
//
// The lane's state is one PSRAM block made at start and freed at stop; the
// stats are the board's (MOY_WASM_TIMED_LANES), kept for the meter.

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "py/mpconfig.h"               // the board's MOY_WASM_TIMED_LANES
#include "moy_wasm_footprint.h"
#include "moy_wasm_session.h"

#ifndef MOY_WASM_TIMED_LANES
#define MOY_WASM_TIMED_LANES 0
#endif

#if !MOY_WASM_TIMED_LANES

int moy_wasm_tlanes_start(moy_wasm_env env, const moy_wasm_tlane_sink_t *sink,
                          char *err, size_t errlen) {
    (void)env;
    (void)sink;
    (void)err;
    (void)errlen;
    return 0;
}

void moy_wasm_tlanes_stop(void) {
}

int moy_wasm_tlanes_stats(moy_wasm_tlane_stats_t *out) {
    (void)out;
    return 0;
}

int moy_wasm_tlane_here(void) {
    return 0;
}

#else

#include <pthread.h>

#include "py/mphal.h"                   // MP_TASK_COREID
#include "esp_heap_caps.h"
#include "moy_kmem.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"   // pxTaskGetStackStart
#include "freertos/task.h"
#include "moy_wasm_thread.h"

#ifndef MOY_WASM_TLANE_PRIO
#define MOY_WASM_TLANE_PRIO 15
#endif
#ifndef MOY_WASM_CORE
#define MOY_WASM_CORE MP_TASK_COREID
#endif

#define TICK_US 1000                    // the monitor's period, and the audio lane's wake
#define AUDIO_MIN 32                    // the room worth a call
#define STACK_MIN 1024                  // a lane stack's least size in the cart's memory
#define STACK_GUARD (2 * 1024)          // native stack kept below the runtime's boundary
#define SP_EXPORT "__stack_pointer"
#define STACK_EXPORT "moy_lane_stack"

// THE SHARES' LANES row (native/moy_play/moy_play.h's MOY_SHARE_LANES;
// tests/test_audio_ring.py holds the two equal), its ledger weak.
#define TL_SHARE_LANES 4
#if __has_include("moy_play.h")
#include "moy_play.h"
#else
int moy_share_take(uint8_t row, uint32_t n);
void moy_share_give(uint8_t row, uint32_t n);
#endif
#pragma weak moy_share_take
#pragma weak moy_share_give

static const char *const EXPORTS[MOY_WASM_TLANES] = {"moy_audio", "moy_timer"};

typedef struct {
    int kind;
    bool up;                            // its thread was started
    volatile bool quit, struck, hung;   // hung: suspended by the monitor
    pthread_t tid;
    TaskHandle_t volatile task;
    wasm_module_inst_t inst;            // the sibling
    wasm_exec_env_t env;
    wasm_function_inst_t fn;
    uint8_t *sp;                        // its __stack_pointer's value
    uint32_t top;                       // its stack's top in the cart's memory
    volatile int64_t t_call;            // when the running call began, 0: none
    esp_timer_handle_t timer;           // the timer lane's next call
} lane_t;

typedef struct {
    lane_t lane[MOY_WASM_TLANES];
    moy_wasm_tlane_sink_t sink;
    esp_timer_handle_t tick;
    uint32_t taken;                     // bytes of the LANES row
} tls_t;

static tls_t *g_tl;
static moy_wasm_tlane_stats_t g_stats[MOY_WASM_TLANES];

static void strike(lane_t *l, const char *why) {
    if (l->struck) {
        return;
    }
    l->struck = true;
    moy_wasm_tlane_stats_t *st = &g_stats[l->kind];
    st->struck = 1;
    snprintf(st->why, sizeof(st->why), "%s", why);
    if (g_tl != NULL && g_tl->sink.mute != NULL) {
        g_tl->sink.mute();
    }
    printf("LANE %s struck: %s\n", EXPORTS[l->kind], why);
}

// One call into the cart on the lane: the stack pointer at `sp`, the clock
// the monitor reads around it. False when it trapped (the lane is struck).
static bool lane_call(lane_t *l, uint32_t sp, uint32_t argc, uint32_t *argv) {
    memcpy(l->sp, &sp, 4);
    int64_t t0 = moy_now_us();
    __atomic_store_n(&l->t_call, t0, __ATOMIC_RELEASE);
    bool ok = wasm_runtime_call_wasm(l->env, l->fn, argc, argv);
    __atomic_store_n(&l->t_call, 0, __ATOMIC_RELEASE);
    uint32_t us = (uint32_t)(moy_now_us() - t0);
    moy_wasm_tlane_stats_t *st = &g_stats[l->kind];
    st->calls++;
    if (us > st->max_us) {
        st->max_us = us;
    }
    if (!ok) {
        char why[48];
        const char *e = wasm_runtime_get_exception(l->inst);
        snprintf(why, sizeof(why), "trap: %s", e != NULL ? e : "?");
        wasm_runtime_clear_exception(l->inst);
        strike(l, why);
    }
    return ok;
}

static void lane_audio(lane_t *l) {
    while (!l->struck && !l->quit) {
        uint32_t room = g_tl->sink.write(NULL, 0);
        if (room < AUDIO_MIN) {
            return;
        }
        uint32_t n = room < MOY_WASM_TLANE_CHUNK ? room : MOY_WASM_TLANE_CHUNK;
        uint32_t buf = (l->top - n * 2u) & ~15u;
        uint32_t argv[2] = {buf, n};
        if (!lane_call(l, buf, 2, argv)) {
            return;
        }
        uint32_t got = argv[0] < n ? argv[0] : n;
        if (got == 0u) {
            return;
        }
        g_tl->sink.write(wasm_runtime_addr_app_to_native(l->inst, buf), got);
        g_stats[l->kind].frames += got;
        if (got < n) {
            return;
        }
    }
}

static void lane_timer(lane_t *l) {
    uint32_t argv[1] = {(uint32_t)moy_now_us()};
    if (!lane_call(l, l->top & ~15u, 1, argv)) {
        return;
    }
    int32_t next = (int32_t)argv[0];
    if (next > 0 && !l->quit) {
        esp_timer_start_once(l->timer, next < MOY_WASM_TLANE_TIMER_MIN
                                           ? MOY_WASM_TLANE_TIMER_MIN : (uint64_t)next);
    }
}

static void *lane_thread(void *arg) {
    lane_t *l = arg;
    wasm_runtime_init_thread_env();
    wasm_runtime_set_native_stack_boundary(l->env, pxTaskGetStackStart(NULL) + STACK_GUARD);
    l->task = xTaskGetCurrentTaskHandle();
    if (l->kind == MOY_WASM_TLANE_TIMER) {
        xTaskNotifyGive(l->task);       // its first call, as the lanes start
    }
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (l->quit) {
            break;
        }
        if (l->struck) {
            continue;
        }
        if (l->kind == MOY_WASM_TLANE_AUDIO) {
            lane_audio(l);
        } else {
            lane_timer(l);
        }
    }
    wasm_runtime_destroy_thread_env();
    return NULL;
}

// The monitor, on the esp_timer task: a call past its deadline suspends its
// lane; the audio lane is woken.
static void on_tick(void *arg) {
    tls_t *t = arg;
    int64_t now = moy_now_us();
    for (int k = 0; k < MOY_WASM_TLANES; k++) {
        lane_t *l = &t->lane[k];
        int64_t t0 = __atomic_load_n(&l->t_call, __ATOMIC_ACQUIRE);
        if (l->task == NULL || l->struck || t0 == 0) {
            continue;
        }
        if (now - t0 > MOY_WASM_TLANE_DEADLINE_US) {
            vTaskSuspend(l->task);
            l->hung = true;
            char why[48];
            snprintf(why, sizeof(why), "deadline: %u us",
                     (unsigned)(now - t0));
            strike(l, why);
        }
    }
    lane_t *a = &t->lane[MOY_WASM_TLANE_AUDIO];
    if (a->task != NULL && !a->struck) {
        xTaskNotifyGive(a->task);
    }
}

static void on_timer(void *arg) {
    lane_t *l = arg;
    if (l->task != NULL && !l->struck) {
        xTaskNotifyGive(l->task);
    }
}

// Lane k's sibling, exec env and stack, on the session's thread. 0, 1 when
// the module does not export the lane, -1 with `err` set.
static int lane_make(tls_t *t, int k, wasm_exec_env_t env, char *err, size_t errlen) {
    lane_t *l = &t->lane[k];
    wasm_module_inst_t main = wasm_runtime_get_module_inst(env);
    l->kind = k;
    if (wasm_runtime_lookup_function(main, EXPORTS[k]) == NULL) {
        return 1;
    }
    wasm_function_inst_t sf = wasm_runtime_lookup_function(main, STACK_EXPORT);
    uint32_t argv[1] = {(uint32_t)k};
    if (sf == NULL || !wasm_runtime_call_wasm(env, sf, 1, argv)) {
        snprintf(err, errlen, "%s wants an export %s", EXPORTS[k], STACK_EXPORT);
        wasm_runtime_clear_exception(main);
        return -1;
    }
    l->top = argv[0] & ~15u;
    if (l->top < STACK_MIN || !wasm_runtime_validate_app_addr(main, l->top - STACK_MIN, STACK_MIN)) {
        snprintf(err, errlen, "%s(%d) is no stack in the cart's memory", STACK_EXPORT, k);
        return -1;
    }
    char e[128];
    l->inst = wasm_runtime_instantiate_sibling(main, MOY_WASM_EXEC_STACK, e, sizeof(e));
    if (l->inst == NULL) {
        snprintf(err, errlen, "lane %s: %s", EXPORTS[k], e);
        return -1;
    }
    wasm_global_inst_t g;
    l->env = wasm_runtime_create_exec_env(l->inst, MOY_WASM_EXEC_STACK);
    if (l->env == NULL) {
        snprintf(err, errlen, "out of memory: no pool for lane %s", EXPORTS[k]);
        return -1;
    }
    l->fn = wasm_runtime_lookup_function(l->inst, EXPORTS[k]);
    if (l->fn == NULL || !wasm_runtime_get_export_global_inst(l->inst, SP_EXPORT, &g)
        || g.kind != WASM_I32 || !g.is_mutable) {
        snprintf(err, errlen, "lane %s wants an export %s", EXPORTS[k], SP_EXPORT);
        return -1;
    }
    l->sp = (uint8_t *)g.global_data;
    if (k == MOY_WASM_TLANE_TIMER) {
        esp_timer_create_args_t ta = {.callback = on_timer, .arg = l, .name = "moy_tlane"};
        if (esp_timer_create(&ta, &l->timer) != ESP_OK) {
            snprintf(err, errlen, "lane %s: no timer", EXPORTS[k]);
            return -1;
        }
    }
    return 0;
}

static void lane_free(lane_t *l) {
    if (l->timer != NULL) {
        esp_timer_stop(l->timer);
        esp_timer_delete(l->timer);
        l->timer = NULL;
    }
    if (l->up) {
        if (l->hung) {
            // Suspended inside a call that never returned: its task goes as it
            // stands, never to run again; the pthread's record is left behind.
            vTaskDelete(l->task);
        } else {
            l->quit = true;
            xTaskNotifyGive(l->task);
            pthread_join(l->tid, NULL);
        }
        l->up = false;
    }
    if (l->env != NULL) {
        wasm_runtime_destroy_exec_env(l->env);
        l->env = NULL;
    }
    if (l->inst != NULL) {
        wasm_runtime_deinstantiate_sibling(l->inst);
        l->inst = NULL;
    }
}

void moy_wasm_tlanes_stop(void) {
    tls_t *t = g_tl;
    if (t == NULL) {
        return;
    }
    if (t->tick != NULL) {
        esp_timer_stop(t->tick);
        esp_timer_delete(t->tick);
    }
    for (int k = 0; k < MOY_WASM_TLANES; k++) {
        lane_free(&t->lane[k]);
    }
    if (t->taken && moy_share_give != NULL) {
        moy_share_give(TL_SHARE_LANES, t->taken);
    }
    g_tl = NULL;
    moy_kfree(t);
}

int moy_wasm_tlanes_start(moy_wasm_env env, const moy_wasm_tlane_sink_t *sink,
                          char *err, size_t errlen) {
    memset(g_stats, 0, sizeof(g_stats));
    if (g_tl != NULL) {
        moy_wasm_tlanes_stop();
    }
    tls_t *t = moy_kalloc(sizeof(tls_t), MOY_KMEM_PSRAM | MOY_KMEM_ZERO);
    if (t == NULL) {
        snprintf(err, errlen, "out of memory: no PSRAM for the lanes");
        return -1;
    }
    g_tl = t;
    t->sink = *sink;
    int mask = 0;
    for (int k = 0; k < MOY_WASM_TLANES; k++) {
        int rc = lane_make(t, k, env, err, errlen);
        if (rc < 0) {
            moy_wasm_tlanes_stop();
            return -1;
        }
        if (rc == 0) {
            mask |= 1 << k;
        }
    }
    if (mask == 0) {
        moy_wasm_tlanes_stop();
        return 0;
    }
    uint32_t need = (uint32_t)__builtin_popcount(mask) * MOY_WASM_STACK_BYTES;
    if (moy_share_take != NULL && moy_share_take(TL_SHARE_LANES, need) != 0) {
        snprintf(err, errlen, "the lanes do not fit THE SHARES' LANES row");
        moy_wasm_tlanes_stop();
        return -1;
    }
    t->taken = need;
    int core = (MOY_WASM_CORE + 1) % portNUM_PROCESSORS;
    for (int k = 0; k < MOY_WASM_TLANES; k++) {
        lane_t *l = &t->lane[k];
        if (!(mask & (1 << k))) {
            continue;
        }
        if (moy_wasm_spawn(&l->tid, lane_thread, l, MOY_WASM_STACK_BYTES,
                           MOY_WASM_STACK_PSRAM, core, MOY_WASM_TLANE_PRIO) != 0) {
            snprintf(err, errlen, "lane %s: no thread", EXPORTS[k]);
            moy_wasm_tlanes_stop();
            return -1;
        }
        l->up = true;
        g_stats[k].ran = 1;
    }
    esp_timer_create_args_t ta = {.callback = on_tick, .arg = t, .name = "moy_tlanes"};
    if (esp_timer_create(&ta, &t->tick) != ESP_OK
        || esp_timer_start_periodic(t->tick, TICK_US) != ESP_OK) {
        snprintf(err, errlen, "the lanes' monitor would not start");
        moy_wasm_tlanes_stop();
        return -1;
    }
    return mask;
}

int moy_wasm_tlanes_stats(moy_wasm_tlane_stats_t *out) {
    memcpy(out, g_stats, sizeof(g_stats));
    return MOY_WASM_TLANES;
}

int moy_wasm_tlane_here(void) {
    tls_t *t = g_tl;
    TaskHandle_t me = xTaskGetCurrentTaskHandle();
    for (int k = 0; t != NULL && k < MOY_WASM_TLANES; k++) {
        if (t->lane[k].up && t->lane[k].task == me) {
            return 1;
        }
    }
    return 0;
}

#endif
