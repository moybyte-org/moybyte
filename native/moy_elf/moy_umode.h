// moy_umode: a native module's code in RISC-V U-mode on the ESP32-P4, confined
// by PMP to its process's memory, reaching the kernel only through the OS
// table (docs/os_2026-11/tiers.md section 1, native/moy_elf/README.md,
// "Isolation"). P4 boards only: MOY_OS_UMODE is defined where this is built.
//
// THE PMP. Boot replaces ESP-IDF's layout (moy_umode.c's moy_u_layout on
// each core; the boards build with CONFIG_BOOTLOADER_REGION_PROTECTION_ENABLE
// =n, so neither the bootloader nor the app writes IDF's): IDF's PMA table as
// it is, the ROM locked RX in entry 15 and nothing else, so M-mode keeps what IDF's layout
// gave it (MicroPython's sdkconfig.base turns the IRAM/DRAM split off) and
// U-mode may touch nothing but what entries 0..14 open. Those are reloaded
// for the thread about to enter U-mode, on every way in (moy_u_return):
//
//   0..2   the module's block, a TOR chain: [lo, rx) RX, [rx, hi) RW
//   3..4   the thread's own U stack, RW
//   5..14  five slots for the process's other memory (its mappings, its
//          other threads' stacks), loaded when an access faults in one and
//          taken round robin
//
// A core keeps a thread's windows until another thread enters U-mode there or
// the process lets an extent go (its `gen`). Every window is 128-aligned and a
// multiple of 128 long, the PMP's granularity, so none shows a neighbour.
//
// THE TRAPS are moy_umode.S's: an interrupt in U-mode reaches ESP-IDF's
// handler as one of M-mode code on the thread's kernel stack (its FreeRTOS
// stack), so a context switch never sees U-mode; an ecall is a table row,
// checked against the process's memory by the row's own argument rule
// (moy_os_check.c, generated from os_table.json) before it runs; an FPU
// instruction reaches IDF's lazy enable; any other fault ends the process
// with a fault record (moy_os.c).

#ifndef MOY_UMODE_H
#define MOY_UMODE_H

#include <stddef.h>
#include <stdint.h>

#define MOY_U_R 1
#define MOY_U_W 2
#define MOY_U_X 4
#define MOY_U_ALIGN 128u
#define MOY_U_EXTENTS 48u       // a process's stacks and mappings at once

#define MOY_U_UP(n) (((uint32_t)(n) + MOY_U_ALIGN - 1u) & ~(MOY_U_ALIGN - 1u))
#define MOY_U_DOWN(n) ((uint32_t)(n) & ~(MOY_U_ALIGN - 1u))

// A fault's cause beyond the core's mcause codes (0..15).
#define MOY_U_BAD_ROW 0x100u     // an ecall with no such row
#define MOY_U_BAD_ARG 0x200u     // a row's pointer outside the process (| the row)

typedef struct {
    uint32_t lo, hi;
    uint32_t perm;
} moy_u_ext_t;

// A process's memory: the module's block and every extent it holds.
typedef struct {
    uint32_t lo, rx, hi;        // the block: [lo, rx) RX, [rx, hi) RW
    uint32_t vbase;             // the address of the module's vaddr 0
    volatile uint32_t gen;      // bumped when an extent goes
    uint32_t n;
    moy_u_ext_t ext[MOY_U_EXTENTS];
} moy_u_proc_t;

// A thread's: its process, its U stack and what moy_os knows it by.
typedef struct {
    moy_u_proc_t *proc;
    uint32_t id;                // unique for the boot: what a core's windows are keyed by
    uint32_t slo, shi;
    volatile uint8_t *ending;   // its process is ending: it stops at its next way into U-mode
    void *thr;                  // NULL: a call on the loading task (BIND)
    uint32_t fault;             // a call no thread owns: its fault's cause + 1
} moy_u_space_t;

// THE LAZY FPU. ESP-IDF turns the FPU off (mstatus.FS = Off) on every
// interrupt and every context switch, and turns it back on in _panic_handler
// when the next FPU instruction traps (vectors.S's coprocessor path, which
// also takes fflags/frm/fcsr CSR accesses). An interrupt can land between that
// enable and the instruction's retry, so the same instruction traps again with
// the FPU off; only a trap taken with the FPU ON is an illegal instruction.
#define MOY_U_MSTATUS_FS (3u << 13)

// Whether a U-mode illegal-instruction trap of `insn` (mtval), with `ill` the
// EXT_ILL reason and `mstatus` read at the trap, goes to IDF's lazy enable:
// an FPU instruction (never PIE or a hardware loop, which IDF aborts on from a
// task) taken while the FPU was off.
static inline int moy_u_fpu_lazy(uint32_t insn, uint32_t ill, uint32_t mstatus) {
    if ((mstatus & MOY_U_MSTATUS_FS) != 0u || (ill & 4u) || (insn & 0x7fu) == 0x2bu) {
        return 0;
    }
    if ((ill & 1u) || (insn & 0x5fu) == 0x07u || (insn & 0x6001u) == 0x6000u) {
        return 1;
    }
    return (insn & 0x7fu) == 0x73u && (insn >> 20) >= 1u && (insn >> 20) <= 3u;
}

// 0 once the vectors are this module's on both cores; -1 when ESP-IDF's
// table is not the one the stubs were written for (no module runs).
int moy_u_ready(void);

// A fresh id for a space.
uint32_t moy_u_new_id(void);

// The module's code at `entry` in U-mode on this task, with a0..a4 = a and sp
// = `sp`, until it returns to `ret` (the gate's return): its a0.
int moy_u_run(moy_u_space_t *s, uint32_t entry, uint32_t ret, uint32_t sp, const uint32_t a[5]);

// The process's extents: an add answers 0, or -1 when it has MOY_U_EXTENTS.
int moy_u_add(moy_u_proc_t *p, uint32_t lo, uint32_t hi, uint32_t perm);
void moy_u_drop(moy_u_proc_t *p, uint32_t lo);

// The rows' checks: [p, p+n) inside the thread's process with `perm`; a
// NUL-terminated string inside it.
int moy_u_ok(const moy_u_space_t *s, const void *p, size_t n, uint32_t perm);
int moy_u_str(const moy_u_space_t *s, const char *p);
// The bytes readable from p to the end of its region of the process (0:
// outside it): what a row reading a text in place may read at most.
size_t moy_u_room(const moy_u_space_t *s, const void *p);

// The row check for `row` over its argument registers (moy_os_check.c).
int moy_os_check(const moy_u_space_t *s, uint32_t row, const uint32_t *a);

// moy_os.c's side: a row from the frame's registers (a0..a7, the ordinal in
// t0), its result written back; a thread killed where it stands (never
// returns); a thread of an ending process parked.
void moy_os_u_row(moy_u_space_t *s, uint32_t *frame);
void moy_os_u_fault(moy_u_space_t *s, uint32_t *frame, uint32_t cause, uint32_t pc,
                    uint32_t addr) __attribute__((noreturn));
void moy_os_u_park(moy_u_space_t *s) __attribute__((noreturn));

// The gate a module's block starts with: the U-mode table its stubs call
// through (the S3's table layout, each row a stub that makes the ecall) and
// the return stub a hook comes back through.
#define MOY_U_GATE_STUB 12u
#define MOY_U_GATE(rows) MOY_U_UP(8u + 4u * (rows) + MOY_U_GATE_STUB * (rows) + 8u)
// Writes the gate at `at` (MOY_U_GATE(rows) bytes): the table's address is
// `at`, the return stub's *ret.
void moy_u_gate(uint32_t at, uint32_t version, uint32_t rows, uint32_t *ret);

#endif
