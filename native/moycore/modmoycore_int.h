// moycore's MicroPython binding, inside: what modmoycore.c and the files
// beside it share -- moycore_prof.c (the per-verb and per-function
// profilers), moycore_pmu.c (the hardware performance counters) and
// moycore_wasm.c (the compiled cart) -- the build's switches, the clocks the
// frame's hooks read, and the state and helpers one file defines and another
// uses. Only those files include it.

#ifndef MODMOYCORE_INT_H
#define MODMOYCORE_INT_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "py/obj.h"
#include "py/objarray.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "py/objlist.h"
#include "py/objstr.h"
#include "py/stream.h"
#include "py/builtin.h"

#include "lua.h"
#include "lauxlib.h"

#include "moy.h"
#include "moycore_layers.h"
#include "../moy_gfx/moy_gfx_capi.h"   // the layer restore draw_layer takes
#include "moycore_run.h"
#include "moycore_lua.h"
#if defined(__has_include)
#if __has_include("moy_app_run.h")
#include "moy_app_run.h"            // an app's run (native/moy_app)
#define MOYCORE_APP 1
#endif
#endif
#include "../moy_kernel/moy_loop.h"     // the crossings, counted by class

// The board allocator, for a compiled cart's memory (the VM's allocator is
// moycore_lua.c's): present on an ESP-IDF build, absent on the host/unix/wasm
// ones, which then use plain calloc.
#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define MOYCORE_PSRAM 1
#endif
#endif

#define RUN moycore_RUN

// The clock is the CPU cycle counter where there is one. mp_hal_ticks_us() is
// esp_timer_get_time() on both S3 boards, and a systimer read costs about what
// the verbs being measured cost -- at three thousand calls a frame it would not
// perturb the measurement so much as become it. CCOUNT is one instruction.
#if defined(__has_include)
#  if __has_include(<esp_cpu.h>)
#    include <esp_cpu.h>
#    define PROF_CYCLES 1
#  endif
#endif
#ifdef PROF_CYCLES
#  define PROF_NOW() ((uint32_t)esp_cpu_get_cycle_count())
#else
#  define PROF_NOW() ((uint32_t)mp_hal_ticks_us())
#endif

// The hardware performance counters (moycore_pmu.c says what they are for)
// and how this build reads them; the frame's hooks bracket a tick with them.
#if defined(__XTENSA__) && defined(__has_include)
#  if __has_include("eri.h") && __has_include("xtensa-debug-module.h")
#    include "eri.h"
#    include "xtensa-debug-module.h"
#    define MOY_PMU 1          // ERI: two counters, both selectable
#  endif
#elif defined(__riscv)
#  define MOY_PMU 2            // the standard CSRs, cycles and retired only
#endif
#ifndef MOY_PMU
#  define MOY_PMU 0            // the host, and the wasm head: no counters
#endif

#if MOY_PMU == 2
static inline uint32_t pm_csr_cycle(void)
{
    uint32_t v; __asm__ volatile ("csrr %0, mcycle" : "=r"(v)); return v;
}
static inline uint32_t pm_csr_instret(void)
{
    uint32_t v; __asm__ volatile ("csrr %0, minstret" : "=r"(v)); return v;
}
#endif

#define PM_UPD  0
#define PM_DRAW 1

// Both counters, as close together as the ISA allows. 32 bits and they wrap --
// at 240MHz the cycle count turns over every ~17.9s -- but every use of them is
// an unsigned DIFFERENCE inside one frame, which wrapping leaves correct.
static inline void moycore_pm_read(uint32_t *cyc, uint32_t *evt)
{
#if MOY_PMU == 1
    *cyc = eri_read(ERI_PERFMON_PM0);
    *evt = eri_read(ERI_PERFMON_PM0 + (int)sizeof(int32_t));
#elif MOY_PMU == 2
    *cyc = pm_csr_cycle();
    *evt = pm_csr_instret();
#else
    *cyc = 0; *evt = 0;
#endif
}

// An engine for compiled carts is in the build (moycore_wasm.c).
#if MOY_WASM || MOY_WASM_JS
#define MOYCORE_WASM 1
#else
#define MOYCORE_WASM 0
#endif

#if MOYCORE_WASM
#include "libmoy/moy_wasm.h"
#include "moy_wasm_session.h"
#include "../moy_store/moy_files.h"
#include "../moy_store/moy_vol.h"
#if MOY_AUDIO_SND
#include "moy_audio_snd.h"
#if MOY_AUDIO_SND_RATE != MOY_WASM_SND_RATE || MOY_AUDIO_SND_DEPTH != MOY_WASM_SND_DEPTH
#error "moy_audio's stream is not the binding's"
#endif
#endif
#endif

// The state one file keeps and another reads or resets.
extern uint32_t moycore_prof_frames;   // moycore_prof.c
extern uint32_t moycore_prof_hz;   // moycore_prof.c
extern int      moycore_prof_n;   // moycore_prof.c
extern uint8_t  moycore_prof_arm;   // moycore_prof.c
extern uint8_t  moycore_prof_on;   // moycore_prof.c
extern uint32_t moycore_lp_t0;   // moycore_prof.c
extern uint32_t moycore_lp_frames;   // moycore_prof.c
extern uint8_t  moycore_lp_on;   // moycore_prof.c
extern uint8_t  moycore_lp_arm;   // moycore_prof.c
extern int      moycore_lp_arm_iv;   // moycore_prof.c
extern int32_t  moycore_lp_arm_lo, moycore_lp_arm_hi;   // moycore_prof.c
extern uint8_t  moycore_pm_on;   // moycore_pmu.c
extern uint32_t moycore_pm_frames;   // moycore_pmu.c
extern uint64_t moycore_pm_cyc[2];   // moycore_pmu.c
extern uint64_t moycore_pm_evt[2];   // moycore_pmu.c
extern const uint8_t *moycore_whead;   // moycore_wasm.c
extern size_t moycore_whead_len;   // moycore_wasm.c
extern uint32_t moycore_wpages;   // moycore_wasm.c

// modmoycore.c
void *moycore_buf_w(mp_obj_t o, size_t *len);
void *moycore_buf_r(mp_obj_t o, size_t *len);
// The app's run a launch hands over (modmoycore.c), or NULL.
const struct moy_app_run *moycore_app_run(mp_obj_t o);

// moycore_prof.c
uint32_t moycore_prof_calibrate(void);
void moycore_prof_install(lua_State *L);
void moycore_lprof_forget(void);
int moycore_lprof_install(lua_State *L, int interval, int32_t lo, int32_t hi);
MP_DECLARE_CONST_FUN_OBJ_1(moycore_profile_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_verb_stats_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_verb_reset_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_profile_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_stats_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_lua_reset_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_lua_gc_mode_obj);

// moycore_pmu.c
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_perf_counters_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_perf_stats_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_perf_read_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_perf_reset_obj);

// moycore_wasm.c
int moycore_wasm_begin(const char *path, const char *sha, const char *dir,
                       const char *writable, size_t writable_len,
                       int swapped, int allow_unsigned, int interp, uint32_t ring,
                       const struct moy_app_run *app, const int32_t *layout,
                       char *err, size_t errlen);
// A responsive app's `_layout(w, h, fs)` on the current compiled run: 1 where
// its module exports one and it returned, 0 where it exports none, -1 with
// `err` (a trap, which ends the run).
int moycore_wasm_layout(int32_t w, int32_t h, int32_t fs, char *err, size_t errlen);
int moycore_wasm_tick_c(float dt, int draw, char *err, size_t errlen);
void moycore_wasm_end(void);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_wasm_open_obj);
MP_DECLARE_CONST_FUN_OBJ_1(moycore_files_keep_obj);
MP_DECLARE_CONST_FUN_OBJ_2(moycore_files_room_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_files_kept_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_wasm_quit_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_wasm_ran_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_wasm_table_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_take_frames_obj);
MP_DECLARE_CONST_FUN_OBJ_1(moycore_frame_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_frame_kept_obj);
MP_DECLARE_CONST_FUN_OBJ_0(moycore_frame_settle_obj);
MP_DECLARE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_frame_presented_obj);

#endif
