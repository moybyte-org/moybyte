// moy_guard: a Python callback run under the cooperative scheduler's slice
// and stopped when it never returns, while the shell goes on
// (docs/os_2026-11/processes.md §3; the scheduler is
// runtime/system/scheduler.py).
//
//   call(fn, budget_us)  fn() under an abort point and a one-shot timer of
//                      `budget_us` (0: no timer): fn's result, or ABORTED once
//                      the timer fired and the VM abort unwound to here.
//                      fn's exception is the caller's. The abort is armed on
//                      the main thread only (the VM takes it there alone,
//                      py/scheduler.c); on any other thread call() is a timed
//                      plain call.
//   cost()             the last call's microseconds, less the garbage
//                      collections inside it where the image counts them
//                      (tools/patch_gc_meters.py): what the callback cost, not
//                      what a collection it happened to trigger cost
//   can_abort()        whether a call() from here can be aborted
//   stats()            (calls, aborts, last_land_us, max_land_us): the land is
//                      the time from the timer's fire to the guard's return
//   ABORTED            the sentinel call() answers for an aborted callback
//
// WHAT AN ABORT SKIPS. Every Python handler between the guard and the point
// the VM took the abort: try/finally, with/__exit__, except BaseException. So
// a lock the callback took stays held and a file it opened stays open until
// its finaliser. What the VM itself could be left holding, the guard puts
// back: the globals and locals the callback's frames switched to, the gc lock
// depth, the scheduler's lock (an abort inside a scheduled callback), the
// outer abort point. A callback blocked in C with no pending check (a lock
// wait, a socket read with no timeout) is never aborted, and the abort lands
// at most one collection after the timer when it fires inside one.
//
// Built where MICROPY_ENABLE_VM_ABORT is: every ESP32 board taking moy_kernel
// (micropython.cmake) and the unix builds (`make unix-micropython`). The
// browser's VM has no abort and no module; the scheduler then calls plainly.

#include <string.h>

#include "py/runtime.h"
#include "py/mphal.h"
#include "py/mpthread.h"
#include "py/nlr.h"
#include "py/gc.h"

#if MICROPY_ENABLE_VM_ABORT

static const mp_obj_base_t aborted_obj = {&mp_type_object};

// The collections' total pause, from tools/patch_gc_meters.py's counters.
extern uint32_t moybyte_gc_pause[3] __attribute__((weak));

static volatile uint32_t s_fired_at;
static volatile bool s_armed;
static uint32_t s_calls, s_aborts, s_last_land, s_max_land, s_cost;

static uint32_t gc_us(void) {
    return &moybyte_gc_pause != NULL ? moybyte_gc_pause[1] : 0u;
}

static void fire(void) {
    if (!s_armed) {
        return;
    }
    s_fired_at = (uint32_t)mp_hal_ticks_us();
    mp_sched_vm_abort();
}

#ifdef ESP_IDF_VERSION
#include "esp_timer.h"

static esp_timer_handle_t s_timer;

static void on_timer(void *arg) {
    (void)arg;
    fire();
}

static void timer_arm(uint32_t us) {
    if (s_timer == NULL) {
        const esp_timer_create_args_t a = {.callback = on_timer, .name = "moy_guard"};
        if (esp_timer_create(&a, &s_timer) != ESP_OK) {
            return;
        }
    }
    s_armed = true;
    esp_timer_start_once(s_timer, us);
}

static void timer_disarm(void) {
    s_armed = false;
    if (s_timer != NULL) {
        esp_timer_stop(s_timer);
    }
}

#else
#include <signal.h>
#include <sys/time.h>

static bool s_handler;

static void on_alarm(int sig) {
    (void)sig;
    fire();
}

static void timer_arm(uint32_t us) {
    if (!s_handler) {
        struct sigaction sa;
        memset(&sa, 0, sizeof(sa));
        sa.sa_handler = on_alarm;
        sa.sa_flags = SA_RESTART;
        sigaction(SIGALRM, &sa, NULL);
        s_handler = true;
    }
    s_armed = true;
    struct itimerval it = {{0, 0}, {us / 1000000u, us % 1000000u}};
    setitimer(ITIMER_REAL, &it, NULL);
}

static void timer_disarm(void) {
    s_armed = false;
    struct itimerval it = {{0, 0}, {0, 0}};
    setitimer(ITIMER_REAL, &it, NULL);
}
#endif

static void costed(uint32_t t0, uint32_t g0) {
    uint32_t wall = (uint32_t)mp_hal_ticks_us() - t0;
    uint32_t gc = gc_us() - g0;
    s_cost = gc < wall ? wall - gc : 0u;
}

static mp_obj_t guard_call(mp_obj_t fn, mp_obj_t budget_in) {
    uint32_t budget = (uint32_t)mp_obj_get_int(budget_in);
    uint32_t g0 = gc_us();
    uint32_t t0 = (uint32_t)mp_hal_ticks_us();
    s_calls++;
    if (budget == 0 || !mp_thread_is_main_thread()) {
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            mp_obj_t r = mp_call_function_0(fn);
            nlr_pop();
            costed(t0, g0);
            return r;
        }
        costed(t0, g0);
        nlr_jump(nlr.ret_val);
    }
    nlr_buf_t *outer = nlr_get_abort();
    mp_obj_dict_t *globals = mp_globals_get();
    mp_obj_dict_t *locals = mp_locals_get();
    uint16_t gc_depth = MP_STATE_THREAD(gc_lock_depth);
    bool sched_unlocked = MP_STATE_VM(sched_state) >= 0;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        MP_STATE_VM(vm_abort) = false;
        nlr_set_abort(&nlr);
        timer_arm(budget);
        mp_obj_t r = mp_call_function_0(fn);
        timer_disarm();
        nlr_set_abort(outer);
        MP_STATE_VM(vm_abort) = false;
        nlr_pop();
        costed(t0, g0);
        return r;
    }
    timer_disarm();
    nlr_set_abort(outer);
    MP_STATE_VM(vm_abort) = false;
    costed(t0, g0);
    if (nlr.ret_val != NULL) {
        nlr_jump(nlr.ret_val);
    }
    mp_globals_set(globals);
    mp_locals_set(locals);
    MP_STATE_THREAD(gc_lock_depth) = gc_depth;
    while (sched_unlocked && MP_STATE_VM(sched_state) < 0) {
        mp_sched_unlock();
    }
    s_aborts++;
    s_last_land = (uint32_t)mp_hal_ticks_us() - s_fired_at;
    if (s_last_land > s_max_land) {
        s_max_land = s_last_land;
    }
    return MP_OBJ_FROM_PTR(&aborted_obj);
}
static MP_DEFINE_CONST_FUN_OBJ_2(guard_call_obj, guard_call);

static mp_obj_t guard_cost(void) {
    return mp_obj_new_int_from_uint(s_cost);
}
static MP_DEFINE_CONST_FUN_OBJ_0(guard_cost_obj, guard_cost);

static mp_obj_t guard_can_abort(void) {
    return mp_obj_new_bool(mp_thread_is_main_thread());
}
static MP_DEFINE_CONST_FUN_OBJ_0(guard_can_abort_obj, guard_can_abort);

static mp_obj_t guard_stats(void) {
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(s_calls), mp_obj_new_int_from_uint(s_aborts),
        mp_obj_new_int_from_uint(s_last_land), mp_obj_new_int_from_uint(s_max_land),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(guard_stats_obj, guard_stats);

static const mp_rom_map_elem_t moy_guard_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_guard) },
    { MP_ROM_QSTR(MP_QSTR_ABORTED), MP_ROM_PTR(&aborted_obj) },
    { MP_ROM_QSTR(MP_QSTR_call), MP_ROM_PTR(&guard_call_obj) },
    { MP_ROM_QSTR(MP_QSTR_cost), MP_ROM_PTR(&guard_cost_obj) },
    { MP_ROM_QSTR(MP_QSTR_can_abort), MP_ROM_PTR(&guard_can_abort_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&guard_stats_obj) },
};
static MP_DEFINE_CONST_DICT(moy_guard_globals, moy_guard_globals_table);

const mp_obj_module_t moy_guard_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_guard_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_guard, moy_guard_module);

#endif
