// moycore's hardware performance counters: `perf_counters`, `perf_stats`,
// `perf_read` and `perf_reset` over the counters modmoycore_int.h reads.

#include "modmoycore_int.h"

// -- the hardware performance counters (perf_counters / perf_stats / perf_reset)
//
// WHAT THIS IS FOR, and it is one question. Four levers have been tried on the
// S3 tick and all four measured NULL -- -O3 on the raster kernels, -O3 on the
// VM core, the whole VM core in IRAM, and the Lua heap's SRAM floor swept to
// both ends (#66, #77). The conclusion drawn from them is that the tick is
// bound by MEMORY rather than by the instructions it retires, and that
// conclusion is what closes the door on a JIT, on opcode fusion and on every
// other code-generation idea. But four nulls are an ELIMINATION, not a
// measurement: they are equally consistent with "the thing you changed was not
// the bottleneck". The ledger even holds both readings at once -- 2026-08-10
// says celeste's residue "is instruction-count -- interpreter dispatch at
// 240MHz", 2026-09-08 says the tick is bound by "the cart's DATA in PSRAM".
//
// IPC settles it, and nothing else does. Retired instructions over cycles,
// across a cart's own update and draw:
//
//   IPC high  -- the core is retiring most cycles: fewer or cheaper
//                instructions win, and the code-generation chapter re-opens.
//   IPC low   -- the core is STALLED most cycles: no code generator helps, and
//                the levers are allocation rate, data layout and cache
//                residency. That closes the chapter with a number.
//
// Counter 0 is always CYCLES, because every reading here is a ratio against
// it; counter 1 is whatever is being asked about, and the default is retired
// instructions. The Xtensa part has exactly two (XCHAL_NUM_PERF_COUNTERS), so
// a wider question is a sweep of paired runs rather than one capture -- which
// is also why the selector is an argument and not a constant.
//
// It is its OWN switch, like `verbs` and `luaprof` and for the same reason: an
// ordinary diag session must not arm it. Disarmed, `tick` tests one byte per
// frame and touches no register.

uint8_t  moycore_pm_on;              // counting, and `tick` is bracketing
static uint16_t g_pm_sel = 2;         // XTPERF_CNT_INSN
static uint16_t g_pm_mask = 0xffff;   // every subset of it
uint32_t moycore_pm_frames;
uint64_t moycore_pm_cyc[2];          // [PM_UPD], [PM_DRAW]
uint64_t moycore_pm_evt[2];

#if MOY_PMU == 1
// tracelevel < 0 and kernelcnt 0 is the IDF's own "count everything" pair
// (xtensa_perfmon_apis.c): counting is gated on CINTLEVEL <= tracelevel, so
// the widest value counts interrupt context too -- which belongs in the
// measurement, because it is time the cart's frame actually spends.
static void pm_program(int id, uint16_t sel, uint16_t mask)
{
    uint32_t pmc = ((uint32_t)(0xf & PMCTRL_TRACELEVEL_MASK)
                        << PMCTRL_TRACELEVEL_SHIFT)
                 | ((uint32_t)(sel & PMCTRL_SELECT_MASK) << PMCTRL_SELECT_SHIFT)
                 | ((uint32_t)(mask & PMCTRL_MASK_MASK) << PMCTRL_MASK_SHIFT);
    eri_write(ERI_PERFMON_PM0 + id * (int)sizeof(int32_t), 0);
    eri_write(ERI_PERFMON_PMCTRL0 + id * (int)sizeof(int32_t), pmc);
}
#endif

// Does this silicon actually count? The counters are architecturally optional
// and a core that does not implement them reads a frozen zero rather than
// faulting, so the answer is taken by LOOKING: read, spend a little time, read
// again. A frozen counter reports as absent, which is the project's rule for a
// lever a board does not have -- None, never 0.
//
// IT HAS TO START THEM TO ASK. A stopped counter and an absent one read
// identically, so the probe arms cycles, samples, and puts PGM back exactly as
// it found it -- otherwise the first `perf_counters(1)` answers "no counters"
// about hardware that has two, which is what it did.
static int8_t g_pm_have = -1;          // -1 not yet asked, 0 no, 1 yes

static int pm_alive(void)
{
    if (g_pm_have >= 0) return g_pm_have;
#if MOY_PMU == 0
    g_pm_have = 0;
#else
    {
        uint32_t c0, e0, c1, e1, spin = 0;
#if MOY_PMU == 1
        uint32_t pgm = eri_read(ERI_PERFMON_PGM);
        pm_program(0, 0, 0xffff);                  /* XTPERF_CNT_CYCLES */
        eri_write(ERI_PERFMON_PGM, PGM_PMEN);
#endif
        moycore_pm_read(&c0, &e0);
        while (spin < 2000u) spin++;
        moycore_pm_read(&c1, &e1);
#if MOY_PMU == 1
        eri_write(ERI_PERFMON_PGM, pgm);           /* as we found it */
#endif
        (void)e0; (void)e1;
        g_pm_have = (int8_t)((c1 - c0) != 0u);
    }
#endif
    return g_pm_have;
}

// perf_counters(on[, sel[, mask]]) -> the state it is in now, or None where
// there is no counter to arm. `sel`/`mask` choose counter 1 and are the Xtensa
// XTPERF_CNT_*/XTPERF_MASK_* numbers; the RISC-V side has only the two fixed
// CSRs and ignores them, which `perf_stats` reports rather than hides.
static mp_obj_t mod_perf_counters(size_t n_args, const mp_obj_t *args)
{
    int on = mp_obj_is_true(args[0]);
    if (!pm_alive()) return mp_const_none;
    if (n_args > 1) g_pm_sel = (uint16_t)mp_obj_get_int(args[1]);
    if (n_args > 2) g_pm_mask = (uint16_t)mp_obj_get_int(args[2]);
#if MOY_PMU == 1
    if (on) {
        eri_write(ERI_PERFMON_PGM, 0);
        pm_program(0, 0, 0xffff);              // XTPERF_CNT_CYCLES
        pm_program(1, g_pm_sel, g_pm_mask);
        eri_write(ERI_PERFMON_PGM, PGM_PMEN);
    } else {
        eri_write(ERI_PERFMON_PGM, 0);
    }
#endif
    moycore_pm_on = (uint8_t)(on ? 1 : 0);
    if (!moycore_prof_hz) moycore_prof_hz = moycore_prof_calibrate();
    return mp_obj_new_bool(on);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_perf_counters_obj, 1, 3,
                                    mod_perf_counters);

// perf_stats() -> (hz, frames, upd_cyc, upd_evt, draw_cyc, draw_evt, sel,
// mask, selectable), or None where there is no counter. The two halves are
// kept apart because the corpus splits that way: moss moss is update-bound and
// dank tomb draw-bound, and one IPC over both would average the answer away.
static mp_obj_t mod_perf_stats(void)
{
    mp_obj_t t[9];
    if (!pm_alive()) return mp_const_none;
    t[0] = mp_obj_new_int((mp_int_t)(moycore_prof_hz ? moycore_prof_hz : moycore_prof_calibrate()));
    t[1] = mp_obj_new_int((mp_int_t)moycore_pm_frames);
    t[2] = mp_obj_new_int_from_ull(moycore_pm_cyc[PM_UPD]);
    t[3] = mp_obj_new_int_from_ull(moycore_pm_evt[PM_UPD]);
    t[4] = mp_obj_new_int_from_ull(moycore_pm_cyc[PM_DRAW]);
    t[5] = mp_obj_new_int_from_ull(moycore_pm_evt[PM_DRAW]);
    t[6] = mp_obj_new_int((mp_int_t)g_pm_sel);
    t[7] = mp_obj_new_int((mp_int_t)g_pm_mask);
    t[8] = mp_obj_new_bool(MOY_PMU == 1);
    return mp_obj_new_tuple(9, t);
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_perf_stats_obj, mod_perf_stats);

// perf_read() -> (cycles, event): both counters as they stand, 32 bits that
// wrap, for a caller timing its own window between two reads (the difference
// masked to 32 bits); None where there is no counter. perf_counters(1, ...)
// arms them and picks what the second counts.
static mp_obj_t mod_perf_read(void)
{
    uint32_t cyc, evt;
    if (!pm_alive()) return mp_const_none;
    moycore_pm_read(&cyc, &evt);
    mp_obj_t t[2] = { mp_obj_new_int_from_uint(cyc),
                      mp_obj_new_int_from_uint(evt) };
    return mp_obj_new_tuple(2, t);
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_perf_read_obj, mod_perf_read);

static mp_obj_t mod_perf_reset(void)
{
    moycore_pm_frames = 0;
    moycore_pm_cyc[0] = moycore_pm_cyc[1] = moycore_pm_evt[0] = moycore_pm_evt[1] = 0;
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_perf_reset_obj, mod_perf_reset);
