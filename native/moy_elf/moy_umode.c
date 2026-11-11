// moy_umode: a native module's code in U-mode on the ESP32-P4 (moy_umode.h).

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

#include "sdkconfig.h"
#include "soc/soc.h"
#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_cache.h"
#include "esp_ipc.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_private/startup_internal.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/cache_ll.h"
#include "hal/assist_debug_ll.h"
#include "riscv/csr.h"

#include "moy_umode.h"

#if CONFIG_BOOTLOADER_REGION_PROTECTION_ENABLE
#error "a P4 board builds with CONFIG_BOOTLOADER_REGION_PROTECTION_ENABLE=n: moy_u_layout writes the PMP"
#endif

// -- the boot layout ---------------------------------------------------------------

// ESP-IDF's PMA table for the P4 (cpu_region_protect.c), as it is.
static IRAM_ATTR void pma_regions(void) {
    const unsigned PMA_NONE = PMA_L | PMA_EN;
    const unsigned PMA_RWX = PMA_L | PMA_EN | PMA_R | PMA_W | PMA_X;

    PMA_RESET_AND_ENTRY_SET_NAPOT(0, 0, SOC_CPU_SUBSYSTEM_LOW, PMA_NAPOT | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(1, SOC_CPU_SUBSYSTEM_HIGH, PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(2, SOC_TCM_LOW, PMA_TOR | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(3, SOC_TCM_HIGH, PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(4, CPU_PERIPH_LOW, PMA_TOR | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(5, CPU_PERIPH_HIGH, PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(6, SOC_IROM_LOW, PMA_TOR | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_NAPOT(7, SOC_DROM_HIGH, SOC_EXTRAM_LOW - SOC_DROM_HIGH, PMA_NAPOT | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(8, SOC_EXTRAM_HIGH, PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(9, SOC_IROM_MASK_LOW, PMA_TOR | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(10, SOC_IROM_MASK_HIGH, PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(11, SOC_IRAM_LOW, PMA_TOR | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_NAPOT(12, SOC_DRAM_HIGH, SOC_PERIPHERAL_LOW - SOC_DRAM_HIGH, PMA_NAPOT | PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_NAPOT(13, SOC_IROM_LOW, SOC_PERIPHERAL_LOW - SOC_IROM_LOW, PMA_NAPOT | PMA_RWX);
    PMA_RESET_AND_ENTRY_SET_TOR(14, CACHE_LL_L2MEM_NON_CACHE_ADDR(SOC_DRAM_HIGH), PMA_NONE);
    PMA_RESET_AND_ENTRY_SET_TOR(15, UINT32_MAX, PMA_TOR | PMA_NONE);
}

#define ROM_CFG ((uint32_t)(PMP_NAPOT | PMP_L | PMP_R | PMP_X))

// Each core's start, in place of IDF's esp_cpu_configure_region_protection
// (the boards build with CONFIG_BOOTLOADER_REGION_PROTECTION_ENABLE=n, which
// is what calls it, in the bootloader and the app alike). IDF's layout locks
// RW/RWX entries, a locked entry binds U-mode as it binds M-mode, and a lock
// holds until reset: set by the bootloader on core 0, no app could undo it.
ESP_SYSTEM_INIT_FN(moy_u_layout, SECONDARY, ESP_SYSTEM_INIT_ALL_CORES, 100, IRAM_ATTR) {
    pma_regions();
    RV_WRITE_CSR(pmpcfg0, 0);
    RV_WRITE_CSR(pmpcfg1, 0);
    RV_WRITE_CSR(pmpcfg2, 0);
    RV_WRITE_CSR(pmpcfg3, 0);
    PMP_ENTRY_SET(15, PMPADDR_NAPOT(SOC_IROM_MASK_LOW, SOC_IROM_MASK_HIGH), ROM_CFG);
    return ESP_OK;
}

// True when entries 0-14 are free on this core: false under a bootloader that
// locked its own (an OTA keeps the bootloader a cable flash wrote).
static bool layout_ours(void) {
    return ((RV_READ_CSR(pmpcfg0) | RV_READ_CSR(pmpcfg1) | RV_READ_CSR(pmpcfg2)) & 0x80808080u) == 0
           && RV_READ_CSR(pmpcfg3) >> 24 == ROM_CFG && (RV_READ_CSR(pmpcfg3) & 0x808080u) == 0;
}

// -- the vectors -------------------------------------------------------------------

extern const uint32_t _mtvt_table[];
extern const uint32_t u_mtvt[];
extern const uint32_t u_mtvt_want[];
extern void u_exc(void);
#define U_LINES 48

static int s_ready;
static uint32_t s_seal;     // the uctx canary's secret, drawn when the vectors go in

static void install(void *arg) {
    if (!layout_ours()) {
        *(int *)arg = -1;
        return;
    }
    uint32_t mtvec = RV_READ_CSR(mtvec);
    __asm__ volatile ("csrw 0x307, %0" :: "r"((uint32_t)u_mtvt));
    RV_WRITE_CSR(mtvec, (uint32_t)u_exc | (mtvec & 0x3fu));
}

int moy_u_ready(void) {
    if (s_ready == 0) {
        s_ready = 1;
        s_seal = esp_random() | 1u;
        for (int i = 0; i < U_LINES; i++) {
            if (_mtvt_table[i] != u_mtvt_want[i]) {
                s_ready = -1;
            }
        }
        if (s_ready > 0) {
            install(&s_ready);
#if !CONFIG_FREERTOS_UNICORE
            esp_ipc_call_blocking(!esp_cpu_get_core_id(), install, &s_ready);
#endif
        }
    }
    return s_ready > 0 ? 0 : -1;
}

uint32_t moy_u_new_id(void) {
    static uint32_t s_id;
    return __atomic_add_fetch(&s_id, 1u, __ATOMIC_RELAXED);
}

// -- the windows -------------------------------------------------------------------

#define SLOTS 5
#define CORES 2

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static DRAM_ATTR uint32_t s_id[CORES], s_gen[CORES], s_next[CORES];
static DRAM_ATTR uint32_t s_slot_lo[CORES][SLOTS];

static IRAM_ATTR void addr_set(int i, uint32_t v) {
    switch (i) {
    case 5: RV_WRITE_CSR(pmpaddr5, v); break;
    case 6: RV_WRITE_CSR(pmpaddr6, v); break;
    case 7: RV_WRITE_CSR(pmpaddr7, v); break;
    case 8: RV_WRITE_CSR(pmpaddr8, v); break;
    case 9: RV_WRITE_CSR(pmpaddr9, v); break;
    case 10: RV_WRITE_CSR(pmpaddr10, v); break;
    case 11: RV_WRITE_CSR(pmpaddr11, v); break;
    case 12: RV_WRITE_CSR(pmpaddr12, v); break;
    case 13: RV_WRITE_CSR(pmpaddr13, v); break;
    case 14: RV_WRITE_CSR(pmpaddr14, v); break;
    default: break;
    }
}

// Entry i's config byte (i in 5..14, unlocked) set to c.
static IRAM_ATTR void cfg_set(int i, uint32_t c) {
    uint32_t shift = (uint32_t)(i % 4) * 8u, mask = 0xffu << shift;
    c <<= shift;
    switch (i / 4) {
    case 1: RV_WRITE_CSR(pmpcfg1, (RV_READ_CSR(pmpcfg1) & ~mask) | c); break;
    case 2: RV_WRITE_CSR(pmpcfg2, (RV_READ_CSR(pmpcfg2) & ~mask) | c); break;
    case 3: RV_WRITE_CSR(pmpcfg3, (RV_READ_CSR(pmpcfg3) & ~mask) | c); break;
    default: break;
    }
}

static IRAM_ATTR uint32_t pmp_perm(uint32_t perm) {
    return ((perm & MOY_U_R) ? PMP_R : 0) | ((perm & MOY_U_W) ? PMP_W : 0)
           | ((perm & MOY_U_X) ? PMP_X : 0);
}

// The thread's windows on this core: its block and its stack, the slots empty.
static IRAM_ATTR void windows_load(const moy_u_space_t *s, int core) {
    const moy_u_proc_t *p = s->proc;
    RV_WRITE_CSR(pmpcfg0, 0);
    RV_WRITE_CSR(pmpcfg1, 0);
    RV_WRITE_CSR(pmpcfg2, 0);
    RV_WRITE_CSR(pmpcfg3, ROM_CFG << 24);
    RV_WRITE_CSR(pmpaddr0, p->lo >> 2);
    RV_WRITE_CSR(pmpaddr1, p->rx >> 2);
    RV_WRITE_CSR(pmpaddr2, p->hi >> 2);
    RV_WRITE_CSR(pmpaddr3, s->slo >> 2);
    RV_WRITE_CSR(pmpaddr4, s->shi >> 2);
    RV_WRITE_CSR(pmpcfg0, (uint32_t)(PMP_TOR | PMP_R | PMP_X) << 8
                 | (uint32_t)(PMP_TOR | PMP_R | PMP_W) << 16);
    RV_WRITE_CSR(pmpcfg1, (uint32_t)(PMP_TOR | PMP_R | PMP_W));
    s_id[core] = s->id;
    s_gen[core] = p->gen;
    s_next[core] = 0;
    for (int k = 0; k < SLOTS; k++) {
        s_slot_lo[core][k] = 0;
    }
}

// The extent holding addr with perm put in the next slot: false when none
// does, or the one that does is loaded already (an access across its edge).
static IRAM_ATTR bool window_fault(const moy_u_space_t *s, int core, uint32_t addr, uint32_t perm) {
    const moy_u_proc_t *p = s->proc;
    uint32_t lo = 0, hi = 0, got = 0;
    portENTER_CRITICAL_SAFE(&s_mux);
    for (uint32_t i = 0; i < p->n; i++) {
        if (addr >= p->ext[i].lo && addr < p->ext[i].hi && (p->ext[i].perm & perm) == perm) {
            lo = p->ext[i].lo;
            hi = p->ext[i].hi;
            got = p->ext[i].perm;
            break;
        }
    }
    portEXIT_CRITICAL_SAFE(&s_mux);
    if (got == 0 || lo == s->slo) {
        return false;
    }
    for (int k = 0; k < SLOTS; k++) {
        if (s_slot_lo[core][k] == lo) {
            return false;
        }
    }
    int k = (int)s_next[core];
    s_next[core] = (uint32_t)(k + 1) % SLOTS;
    s_slot_lo[core][k] = lo;
    int e = 5 + 2 * k;
    cfg_set(e + 1, 0);
    addr_set(e, lo >> 2);
    addr_set(e + 1, hi >> 2);
    cfg_set(e + 1, PMP_TOR | pmp_perm(got));
    return true;
}

int moy_u_add(moy_u_proc_t *p, uint32_t lo, uint32_t hi, uint32_t perm) {
    int r = -1;
    portENTER_CRITICAL_SAFE(&s_mux);
    if (p->n < MOY_U_EXTENTS) {
        p->ext[p->n].lo = lo;
        p->ext[p->n].hi = hi;
        p->ext[p->n].perm = perm;
        p->n++;
        r = 0;
    }
    portEXIT_CRITICAL_SAFE(&s_mux);
    return r;
}

void moy_u_drop(moy_u_proc_t *p, uint32_t lo) {
    portENTER_CRITICAL_SAFE(&s_mux);
    for (uint32_t i = 0; i < p->n; i++) {
        if (p->ext[i].lo == lo) {
            p->ext[i] = p->ext[--p->n];
            p->gen++;
            break;
        }
    }
    portEXIT_CRITICAL_SAFE(&s_mux);
}

// The region of the process holding p with perm: its end, or 0.
static uint32_t region_end(const moy_u_space_t *s, uint32_t a, uint32_t perm) {
    const moy_u_proc_t *p = s->proc;
    if (a >= p->lo && a < p->hi && !(perm & MOY_U_X)) {
        if (!(perm & MOY_U_W)) {
            return p->hi;
        }
        if (a >= p->rx) {
            return p->hi;
        }
        return 0;
    }
    if (a >= p->lo && a < p->rx && perm == MOY_U_X) {
        return p->rx;
    }
    uint32_t end = 0;
    portENTER_CRITICAL_SAFE(&s_mux);
    for (uint32_t i = 0; i < p->n; i++) {
        if (a >= p->ext[i].lo && a < p->ext[i].hi && (p->ext[i].perm & perm) == perm) {
            end = p->ext[i].hi;
            break;
        }
    }
    portEXIT_CRITICAL_SAFE(&s_mux);
    return end;
}

int moy_u_ok(const moy_u_space_t *s, const void *ptr, size_t n, uint32_t perm) {
    uint32_t a = (uint32_t)ptr;
    if (n == 0) {
        return 1;
    }
    if (a + n < a) {
        return 0;
    }
    uint32_t end = region_end(s, a, perm);
    return end != 0 && a + n <= end;
}

size_t moy_u_room(const moy_u_space_t *s, const void *ptr) {
    uint32_t a = (uint32_t)ptr;
    uint32_t end = region_end(s, a, MOY_U_R);
    return end != 0 ? end - a : 0;
}

int moy_u_str(const moy_u_space_t *s, const char *ptr) {
    uint32_t a = (uint32_t)ptr;
    uint32_t end = region_end(s, a, MOY_U_R);
    return end != 0 && memchr(ptr, 0, end - a) != NULL;
}

// -- the gate ----------------------------------------------------------------------

void moy_u_gate(uint32_t at, uint32_t version, uint32_t rows, uint32_t *ret) {
    uint32_t *w = (uint32_t *)at;
    w[0] = version;
    w[1] = rows;
    uint32_t *code = w + 2 + rows;
    for (uint32_t i = 0; i < rows; i++) {
        w[2 + i] = (uint32_t)code;
        code[0] = (i << 20) | (5u << 7) | 0x13u;    // addi t0, zero, i
        code[1] = 0x00000073u;                      // ecall
        code[2] = 0x00008067u;                      // ret
        code += MOY_U_GATE_STUB / 4u;
    }
    *ret = (uint32_t)code;
    code[0] = 0xfff00293u;                          // addi t0, zero, -1
    code[1] = 0x00000073u;                          // ecall
    size_t len = MOY_U_GATE(rows);
    esp_cache_msync(w, len, ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    esp_cache_msync(w, len, ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_TYPE_INST);
}


// -- the way in and out -----------------------------------------------------------

typedef struct {
    uint32_t ktp;
    moy_u_space_t *space;
    uint32_t glo, ghi;
    uint32_t ra, sp, s[12];
    uint32_t seal;              // moy_u_seal's canary over the way back
    uint32_t pad;
} moy_uctx_t;
_Static_assert(sizeof(moy_uctx_t) == 80, "moy_umode.S's UCTX");

typedef struct {
    moy_u_space_t *space;
    uint32_t entry, ret, sp;
    uint32_t a[5];
    uint32_t glo, ghi;
} moy_u_go_t;
_Static_assert(offsetof(moy_u_go_t, glo) == 36, "moy_umode.S's GO_GLO");

#define FRAME_WORDS 36
extern int moy_u_enter(const moy_u_go_t *go);
extern void moy_u_close(int mie);

static inline moy_uctx_t *uctx_of(uint32_t *f) {
    return (moy_uctx_t *)(f + FRAME_WORDS);
}

int moy_u_run(moy_u_space_t *s, uint32_t entry, uint32_t ret, uint32_t sp, const uint32_t a[5]) {
    moy_u_go_t go = { .space = s, .entry = entry, .ret = ret, .sp = sp & ~15u };
    memcpy(go.a, a, sizeof go.a);
    uint32_t core = (uint32_t)esp_cpu_get_core_id();
    go.glo = assist_debug_ll_sp_spill_get_min(core);
    go.ghi = assist_debug_ll_sp_spill_get_max(core);
    int r = moy_u_enter(&go);
    return r;
}

// THE SEAL. A U call's uctx carries a canary over what its way back rests on
// (its address, its space, the caller's ra and sp) and the task that made
// it, checked once at the call's end (u_leave): a uctx that is not the
// calling task's, or that something overwrote, ends the board with that
// name instead of a jump through whatever it holds.
static IRAM_ATTR uint32_t seal_of(const moy_uctx_t *u) {
    return s_seal ^ (uint32_t)u ^ (uint32_t)u->space ^ u->ra ^ (u->sp * 0x9e3779b1u)
           ^ (uint32_t)xTaskGetCurrentTaskHandle();
}

IRAM_ATTR void moy_u_seal(uint32_t *f) {
    moy_uctx_t *u = uctx_of(f);
    u->seal = seal_of(u);
}

// The kernel stack's guard back on: the thread runs kernel code.
static IRAM_ATTR void guard_on(const moy_uctx_t *u) {
    uint32_t core = (uint32_t)esp_cpu_get_core_id();
    assist_debug_ll_sp_spill_set_min(core, u->glo);
    assist_debug_ll_sp_spill_set_max(core, u->ghi);
    assist_debug_ll_sp_spill_monitor_enable(core);
}

// Every way into U-mode, interrupts off: an ending process's thread parks, the
// core's windows become the thread's, and the stack guard (which watches sp,
// U-mode's too) stops until the next trap.
IRAM_ATTR void moy_u_return(uint32_t *f) {
    moy_u_space_t *s = uctx_of(f)->space;
    if (*s->ending) {
        guard_on(uctx_of(f));
        moy_u_close(1);
        moy_os_u_park(s);
    }
    int core = esp_cpu_get_core_id();
    if (s_id[core] != s->id || s_gen[core] != s->proc->gen) {
        windows_load(s, core);
    }
    assist_debug_ll_sp_spill_monitor_disable((uint32_t)core);
}

// The call's end (u_leave): its seal checked, the guard back on.
IRAM_ATTR void moy_u_kernel(uint32_t *f) {
    moy_uctx_t *u = uctx_of(f);
    guard_on(u);
    if (u->seal != seal_of(u)) {
        esp_system_abort("moy_umode: a U call's saved kernel context is not its thread's");
    }
}

void moy_u_ecall(uint32_t *f) {
    guard_on(uctx_of(f));
    moy_os_u_row(uctx_of(f)->space, f);
}

// An exception in U-mode other than an ecall, interrupts off: 0 to retry (a
// window loaded), 1 to hand it to IDF's lazy FPU enable (moy_u_fpu_lazy, over
// mstatus as the trap left it: nothing on the way here touches FS). Anything
// else ends the thread's process.
IRAM_ATTR int moy_u_fault(uint32_t *f, uint32_t mcause, uint32_t mtval, uint32_t ill) {
    moy_u_space_t *s = uctx_of(f)->space;
    uint32_t code = mcause & 0xfffu;
    int core = esp_cpu_get_core_id();
    if (code == 2 && moy_u_fpu_lazy(mtval, ill, RV_READ_CSR(mstatus))) {
        return 1;
    }
    if (code == 1 && window_fault(s, core, f[0], MOY_U_X)) {
        return 0;
    }
    if ((code == 5 && window_fault(s, core, mtval, MOY_U_R))
        || (code == 7 && window_fault(s, core, mtval, MOY_U_W))) {
        return 0;
    }
    guard_on(uctx_of(f));
    moy_u_close(1);
    moy_os_u_fault(s, f, code, f[0], code == 1 ? f[0] : mtval);
}
