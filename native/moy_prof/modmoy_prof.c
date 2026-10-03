// Moybyte moy_prof: a PC-sampling profiler that sees the WHOLE IMAGE.
//
// The meters we already have each see one tier and no more. VERBS times C
// verbs, LUAPROF samples the Lua interpreter by FUNCTION, PERFCNT reads the
// CPU's own counters. None of them attributes time across IDF driver code, the
// MicroPython VM's internals, our kernels and the flush path at once -- so a
// cost that lives BETWEEN the tiers is unattributable, and
// docs/perf_native_gap_v1.md §9 has been carrying exactly such a hole ("the
// console's ~10 ms around the tick").
//
// The blind spot that motivates this one specifically: LUAPROF samples on a VM
// INSTRUCTION COUNT, and the collector runs inside the allocator rather than as
// counted instructions, so the collector is invisible to it BY CONSTRUCTION.
// #107 was found by reading code, not by measuring. A PC sampler can see it,
// because it samples wherever the program counter actually is.
//
// HOW THE PC IS TAKEN, and why the two boards differ:
//
//   RISC-V (P4)  -- `mepc` holds the interruptee's PC for the duration of the
//                   handler. One CSR read, exact, ~free. This is the same
//                   mechanism the DIABLITO ESP32-C6 Doom port samples with.
//   Xtensa (S3)  -- there is no equally clean read at interrupt level 1: the
//                   level-4 vector that does `rsr a0, EPC_4` belongs to IDF's
//                   interrupt watchdog (CONFIG_ESP_INT_WDT), and taking level 5
//                   means our own assembly vector. So this walks the windowed
//                   -ABI save areas with IDF's own backtrace helper and records
//                   SIX frames per sample.
//
// On the S3 the frames are, verified by symbolizing real samples rather than
// assumed:
//
//     [0] gptimer_default_isr     the ISR
//     [1] _xt_lowint1             the level-1 dispatcher
//     [2] THE INTERRUPTEE         <- the PC a profile should attribute to
//     [3..5] its call chain
//
// That index is EXPORTED as moy_prof.SELF (2 on Xtensa, 0 on RISC-V) and is
// hist()'s default, so no caller has to know which machine it is talking to --
// `SELF + 1` is "the caller" on any board that has one, which is how you tell
// "all the time is in memcpy" from "who keeps calling memcpy". RISC-V has no
// caller frame yet: there, sample slot 1 is deliberately left 0 rather than
// filled with something that would symbolize plausibly and mean nothing.
//
// dump() stays raw so that layout remains checkable: an aggregate cannot tell
// you what you got wrong, and this index is a property of IDF's dispatch path
// rather than a law. The ring costs internal SRAM only while armed.
//
// Disarmed this module costs the image its code size and nothing else: no
// timer exists, no ISR is installed, and nothing in a frame tests a flag.

#include "py/obj.h"
#include "py/runtime.h"

#ifdef ESP_IDF_VERSION

#include <string.h>
#include "esp_heap_caps.h"
#include "esp_attr.h"
#include "driver/gptimer.h"

#if defined(__XTENSA__)
#include "esp_debug_helpers.h"
#include "esp_cpu_utils.h"
#define MOY_PROF_FRAMES 6
#define MOY_PROF_SELF 2   // [0]=ISR [1]=_xt_lowint1 [2]=interruptee
#else
#include "riscv/csr.h"
#define MOY_PROF_FRAMES 2
#define MOY_PROF_SELF 0   // mepc IS the interruptee
#endif

typedef struct {
    uint32_t pc[MOY_PROF_FRAMES];
} moy_prof_sample_t;

static gptimer_handle_t s_timer = NULL;
static moy_prof_sample_t *s_ring = NULL;
static uint32_t s_cap = 0;        // ring capacity in samples
static volatile uint32_t s_head = 0;
static volatile uint32_t s_taken = 0;   // total samples recorded (may exceed cap)
static volatile uint32_t s_hz = 0;

// The sampling ISR. Deliberately NOT registered ESP_INTR_FLAG_IRAM: the Xtensa
// backtrace helper's entry half is not guaranteed resident, and an ISR that
// runs during a flash write would fault rather than lose a sample. Losing
// samples across SD writes is the cheaper mistake, and `taken` vs elapsed time
// is what tells you it happened.
static bool moy_prof_on_alarm(gptimer_handle_t timer,
                              const gptimer_alarm_event_data_t *edata,
                              void *user_ctx) {
    (void)timer; (void)edata; (void)user_ctx;
    if (s_ring == NULL || s_cap == 0) {
        return false;
    }
    moy_prof_sample_t *slot = &s_ring[s_head];

#if defined(__XTENSA__)
    esp_backtrace_frame_t frame;
    esp_backtrace_get_start(&frame.pc, &frame.sp, &frame.next_pc);
    for (int i = 0; i < MOY_PROF_FRAMES; i++) {
        slot->pc[i] = esp_cpu_process_stack_pc(frame.pc);
        if (!esp_backtrace_get_next_frame(&frame)) {
            for (int j = i + 1; j < MOY_PROF_FRAMES; j++) {
                slot->pc[j] = 0;
            }
            break;
        }
    }
#else
    // mepc is the interruptee's PC, held for the duration of the handler.
    //
    // There is no second frame here yet, and slot 1 stays 0 rather than
    // carrying something that merely looks like one: the interruptee's `ra`
    // lives in the saved interrupt frame, not in a register this C handler can
    // read, and `mscratch` is a scratch CSR that would symbolize to a
    // plausible-looking address while meaning nothing. Caller attribution on
    // this arch needs the frame pointer walk, which is not built.
    slot->pc[0] = (uint32_t)RV_READ_CSR(mepc);
    slot->pc[1] = 0;
#endif

    s_head = (s_head + 1) % s_cap;
    s_taken++;
    return false;   // no task woken
}

// start(hz=1000, samples=512) -- arm the sampler.
static mp_obj_t moy_prof_start(size_t n_args, const mp_obj_t *args) {
    if (s_timer != NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("already running"));
    }
    mp_int_t hz = (n_args > 0) ? mp_obj_get_int(args[0]) : 1000;
    mp_int_t cap = (n_args > 1) ? mp_obj_get_int(args[1]) : 512;
    if (hz < 1 || hz > 20000) {
        mp_raise_ValueError(MP_ERROR_TEXT("hz out of range 1..20000"));
    }
    if (cap < 16 || cap > 8192) {
        mp_raise_ValueError(MP_ERROR_TEXT("samples out of range 16..8192"));
    }

    // Internal SRAM: the ISR must not touch PSRAM through the cache it is
    // trying to measure, and on the Guition this allocation is a fifth of the
    // free internal region -- which is why it is freed on stop().
    size_t bytes = (size_t)cap * sizeof(moy_prof_sample_t);
    s_ring = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (s_ring == NULL) {
        mp_raise_msg(&mp_type_MemoryError,
                     MP_ERROR_TEXT("no internal SRAM for the sample ring"));
    }
    memset(s_ring, 0, bytes);
    s_head = 0;
    s_taken = 0;
    s_cap = (uint32_t)cap;
    s_hz = (uint32_t)hz;

    gptimer_config_t cfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 1000000,      // 1 MHz -> 1 us per tick
    };
    if (gptimer_new_timer(&cfg, &s_timer) != ESP_OK) {
        goto fail;
    }
    gptimer_event_callbacks_t cbs = { .on_alarm = moy_prof_on_alarm };
    if (gptimer_register_event_callbacks(s_timer, &cbs, NULL) != ESP_OK) {
        goto fail;
    }
    gptimer_alarm_config_t alarm = {
        .alarm_count = 1000000 / (uint32_t)hz,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    if (gptimer_set_alarm_action(s_timer, &alarm) != ESP_OK) {
        goto fail;
    }
    if (gptimer_enable(s_timer) != ESP_OK) {
        goto fail;
    }
    if (gptimer_start(s_timer) != ESP_OK) {
        gptimer_disable(s_timer);
        goto fail;
    }
    return mp_const_none;

fail:
    if (s_timer != NULL) {
        gptimer_del_timer(s_timer);
        s_timer = NULL;
    }
    heap_caps_free(s_ring);
    s_ring = NULL;
    s_cap = 0;
    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("could not arm the timer"));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_prof_start_obj, 0, 2, moy_prof_start);

// stop() -- disarm, KEEPING the ring so dump() still works. free() releases it.
static mp_obj_t moy_prof_stop(void) {
    if (s_timer != NULL) {
        gptimer_stop(s_timer);
        gptimer_disable(s_timer);
        gptimer_del_timer(s_timer);
        s_timer = NULL;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_stop_obj, moy_prof_stop);

static mp_obj_t moy_prof_free(void) {
    moy_prof_stop();
    heap_caps_free(s_ring);
    s_ring = NULL;
    s_cap = 0;
    s_head = 0;
    s_taken = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_free_obj, moy_prof_free);

// stats() -> (hz, taken, capacity, frames, wrapped)
static mp_obj_t moy_prof_stats(void) {
    mp_obj_t t[5] = {
        mp_obj_new_int_from_uint(s_hz),
        mp_obj_new_int_from_uint(s_taken),
        mp_obj_new_int_from_uint(s_cap),
        MP_OBJ_NEW_SMALL_INT(MOY_PROF_FRAMES),
        mp_obj_new_bool(s_taken > s_cap),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_stats_obj, moy_prof_stats);

// dump() -> list of per-sample tuples, oldest first. Raw on purpose: the host
// tool decides which frame is the interruptee, and an aggregate here would
// hide the evidence for that decision.
static mp_obj_t moy_prof_dump(void) {
    if (s_ring == NULL || s_cap == 0) {
        return mp_obj_new_list(0, NULL);
    }
    uint32_t n = (s_taken < s_cap) ? s_taken : s_cap;
    uint32_t start = (s_taken < s_cap) ? 0 : s_head;
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < n; i++) {
        moy_prof_sample_t *s = &s_ring[(start + i) % s_cap];
        mp_obj_t fr[MOY_PROF_FRAMES];
        for (int j = 0; j < MOY_PROF_FRAMES; j++) {
            fr[j] = mp_obj_new_int_from_uint(s->pc[j]);
        }
        mp_obj_list_append(out, mp_obj_new_tuple(MOY_PROF_FRAMES, fr));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_dump_obj, moy_prof_dump);

// hist(frame=2, top=40) -> [(pc, count), ...] densest first.
//
// Aggregating HERE and not on the host is a wire decision: 2,048 raw samples
// is ~123 KB of Python repr through a 115200 console, about 11 seconds, which
// would dwarf the capture it describes. A histogram of the top 40 is one short
// line.
//
// `frame` defaults to 2 because that is what the frames say on this board:
// 0 is gptimer_default_isr, 1 is _xt_lowint1, and 2 is the interruptee --
// verified by symbolizing raw samples rather than assumed. Pass 3+ to
// attribute to the caller instead, which is how you tell "everything is in
// memcpy" from "who called memcpy".
static mp_obj_t moy_prof_hist(size_t n_args, const mp_obj_t *args) {
    mp_int_t frame = (n_args > 0) ? mp_obj_get_int(args[0]) : MOY_PROF_SELF;
    mp_int_t top = (n_args > 1) ? mp_obj_get_int(args[1]) : 40;
    if (frame < 0 || frame >= MOY_PROF_FRAMES) {
        mp_raise_ValueError(MP_ERROR_TEXT("frame index out of range"));
    }
    if (s_ring == NULL || s_cap == 0 || top <= 0) {
        return mp_obj_new_list(0, NULL);
    }
    uint32_t n = (s_taken < s_cap) ? s_taken : s_cap;

    // The tally goes on the MicroPython heap, NOT in internal SRAM. Only the
    // ISR's ring has to be internal (an ISR must not fault into PSRAM through
    // the cache it is measuring); nothing touches this table from an
    // interrupt. Asking heap_caps for it was a real bug -- with a cart up, the
    // ring has already taken ~28KB of the ~49KB free and the tally then failed
    // with "no room to tally" on the board it was supposed to profile.
    uint32_t *seen = m_new(uint32_t, 2 * n);
    uint32_t distinct = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t pc = s_ring[i].pc[frame];
        if (pc == 0) {
            continue;
        }
        uint32_t j = 0;
        for (; j < distinct; j++) {
            if (seen[j * 2] == pc) {
                seen[j * 2 + 1]++;
                break;
            }
        }
        if (j == distinct) {
            seen[distinct * 2] = pc;
            seen[distinct * 2 + 1] = 1;
            distinct++;
        }
    }
    // Selection sort of the top `top` only -- a full sort of a few thousand
    // distinct PCs would cost more than the capture.
    if ((uint32_t)top > distinct) {
        top = (mp_int_t)distinct;
    }
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (mp_int_t k = 0; k < top; k++) {
        uint32_t best = k;
        for (uint32_t j = k + 1; j < distinct; j++) {
            if (seen[j * 2 + 1] > seen[best * 2 + 1]) {
                best = j;
            }
        }
        uint32_t tpc = seen[best * 2], tct = seen[best * 2 + 1];
        seen[best * 2] = seen[k * 2];       seen[k * 2] = tpc;
        seen[best * 2 + 1] = seen[k * 2 + 1]; seen[k * 2 + 1] = tct;
        mp_obj_t pair[2] = {
            mp_obj_new_int_from_uint(tpc),
            mp_obj_new_int_from_uint(tct),
        };
        mp_obj_list_append(out, mp_obj_new_tuple(2, pair));
    }
    m_del(uint32_t, seen, 2 * n);
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moy_prof_hist_obj, 0, 2, moy_prof_hist);

#else   // not ESP-IDF: the host/unix build gets a module that says so.

static mp_obj_t moy_prof_unsupported(void) {
    mp_raise_msg(&mp_type_RuntimeError,
                 MP_ERROR_TEXT("moy_prof samples a real CPU; device only"));
}
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_start_obj, moy_prof_unsupported);
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_stop_obj, moy_prof_unsupported);
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_free_obj, moy_prof_unsupported);
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_stats_obj, moy_prof_unsupported);
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_dump_obj, moy_prof_unsupported);
static MP_DEFINE_CONST_FUN_OBJ_0(moy_prof_hist_obj, moy_prof_unsupported);

#endif

static const mp_rom_map_elem_t moy_prof_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_prof) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&moy_prof_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&moy_prof_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_free), MP_ROM_PTR(&moy_prof_free_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&moy_prof_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_dump), MP_ROM_PTR(&moy_prof_dump_obj) },
    { MP_ROM_QSTR(MP_QSTR_hist), MP_ROM_PTR(&moy_prof_hist_obj) },
#ifdef ESP_IDF_VERSION
    // The frame index of the interrupted function. 2 on Xtensa (the ISR
    // and the level-1 dispatcher sit below it), 0 on RISC-V where mepc is
    // the interruptee outright. Callers add an offset to THIS.
    { MP_ROM_QSTR(MP_QSTR_SELF), MP_ROM_INT(MOY_PROF_SELF) },
    { MP_ROM_QSTR(MP_QSTR_FRAMES), MP_ROM_INT(MOY_PROF_FRAMES) },
#endif
};
static MP_DEFINE_CONST_DICT(moy_prof_globals, moy_prof_globals_table);

const mp_obj_module_t mp_module_moy_prof = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_prof_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_prof, mp_module_moy_prof);
