// moy_crash: the crash record and the boot decision (docs/kernel_spine_2026-10.md
// §6, §8). Portable C: the board build compiles it into the kernel, and the
// host compiles it for tests/test_moy_kernel.py through ctypes, so nothing here
// may name the IDF or MicroPython.
//
// THE RECORD is one 128-byte struct. The panic wrapper (moy_kernel.c) fills it
// in RTC memory, which a panic, both watchdogs and a software reset keep; the
// next boot's intake adds the reset reason, copies it to NVS and clears the
// RTC copy. `crc` covers every byte after itself, so a record is either whole
// or absent: power-on garbage, or a wrapper that died half-way, reads as none.
//
// THE BOOT STATE (moy_kstate_t) sits beside it in RTC memory: the boot
// sequence, the VM starts since the console last proved itself (`unproven`),
// the one-shot choice the recovery screen made for the next boot, a dev test
// flag, and the ids the strike ledger holds OPEN, which the wrapper copies into
// the record. moy_boot_decide is the whole policy, and it is pure.

#ifndef MOY_CRASH_H
#define MOY_CRASH_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MOY_CRASH_MAGIC     0x4D4F5943u      // "MOYC"
#define MOY_CRASH_VERSION   1
#define MOY_KSTATE_MAGIC    0x4D4F594Bu      // "MOYK"

// What stopped the console. FAULT..DEBUG come from the panic handler; RESET is
// an unclean reset that left no record (brownout, the RTC watchdog); VM is a VM
// that died with the board up (a console that never proved itself, an
// nlr_jump_fail), recorded by the VM service before it restarts.
enum {
    MOY_CRASH_NONE = 0,
    MOY_CRASH_FAULT,
    MOY_CRASH_ABORT,
    MOY_CRASH_IWDT,
    MOY_CRASH_TWDT,
    MOY_CRASH_DEBUG,
    MOY_CRASH_RESET,
    MOY_CRASH_VM,
    MOY_CRASH_KINDS
};

// What a record's id names: the strike ledger's two roles
// (runtime/crash_guard.py's KEY, WALLPAPER_KEY), and a game the kernel's
// Player armed at its launch (docs/kernel_cartpath_2026-10.md §8.3). A game
// is named and earns no strikes: no ledger counts it.
enum { MOY_ROLE_NONE = 0, MOY_ROLE_APP, MOY_ROLE_WALLPAPER, MOY_ROLE_GAME };

#define MOY_CRASH_ID_LEN    24
#define MOY_CRASH_TASK_LEN  16
#define MOY_CRASH_WHAT_LEN  32

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t size;
    uint32_t crc;                    // crc32 of every byte after this field
    uint32_t boot;                   // the boot sequence it happened in
    uint32_t uptime_ms;
    uint32_t build;                  // the firmware build it happened on
    uint32_t pc;
    uint32_t cause;                  // EXCCAUSE (Xtensa), MCAUSE (RISC-V)
    uint32_t addr;                   // EXCVADDR (Xtensa), MTVAL (RISC-V)
    uint32_t bt[4];                  // the next frames' PCs; RISC-V: RA, then 0
    uint8_t kind;                    // MOY_CRASH_*
    uint8_t core;
    uint8_t role;                    // MOY_ROLE_* of `id`
    uint8_t reset;                   // esp_reset_reason(), set at intake
    char task[MOY_CRASH_TASK_LEN];
    char id[MOY_CRASH_ID_LEN];       // what the ledger held OPEN
    char what[MOY_CRASH_WHAT_LEN];   // the abort message, exception name or VM error
} moy_crash_rec_t;

// What the next boot does.
enum {
    MOY_BOOT_NONE = 0,               // `next` empty: decide from the counters
    MOY_BOOT_START,                  // the console, as usual
    MOY_BOOT_SAFE,                   // the console with its settings ignored
    MOY_BOOT_REPL,                   // the VM with main.py skipped
    MOY_BOOT_RECOVERY,               // the recovery screen, no VM
};

// Why the recovery screen is up.
enum {
    MOY_WHY_NONE = 0,
    MOY_WHY_VM_START,                // the console's boot ended before boot_ok
    MOY_WHY_BOOT_LOOP,               // MOY_BOOT_LOOP_STARTS starts, none proven
    MOY_WHY_HEAP,                    // the VM's first heap area was not there
};

// Dev-only one-shots, armed over the dev channel for the gate.
enum { MOY_TEST_NONE = 0, MOY_TEST_VM_START, MOY_TEST_HEAP };

#define MOY_BOOT_LOOP_STARTS 4

typedef struct {
    uint32_t magic;
    uint32_t boot;                   // +1 per boot that found the state valid
    uint8_t unproven;                // VM starts since the last boot_ok
    uint8_t next;                    // MOY_BOOT_*, consumed by the next decide
    uint8_t reason;                  // MOY_WHY_* that `next == RECOVERY` carries
    uint8_t test;                    // MOY_TEST_*, consumed by the next decide
    char open_app[MOY_CRASH_ID_LEN];
    char open_wallpaper[MOY_CRASH_ID_LEN];
    char open_game[MOY_CRASH_ID_LEN];
} moy_kstate_t;

typedef struct {
    uint8_t action;                  // MOY_BOOT_START / SAFE / REPL / RECOVERY
    uint8_t reason;                  // MOY_WHY_* when RECOVERY
    uint8_t test;                    // MOY_TEST_* this boot runs
} moy_boot_decision_t;

uint32_t moy_crash_crc32(uint32_t crc, const void *buf, size_t len);
void moy_crash_seal(moy_crash_rec_t *r);
int moy_crash_valid(const moy_crash_rec_t *r);
void moy_crash_strcpy(char *dst, size_t cap, const char *src);

const char *moy_crash_kind_name(int kind);
const char *moy_crash_why_name(int why);
const char *moy_crash_role_name(int role);

// A state that is not valid (power-on, brownout) starts over at boot 1.
void moy_kstate_open(moy_kstate_t *st);

// This boot's decision, from what the last boots left; consumes the
// one-shots and counts the start it decides on.
moy_boot_decision_t moy_boot_decide(moy_kstate_t *st);

// The console painted its first frame: the boot-loop count starts over.
void moy_boot_proven(moy_kstate_t *st);

// The OPEN id for `role` (NULL or "" clears it), and which one a crash names:
// the app's when one is open, else the game's, else the wallpaper's.
void moy_kstate_arm(moy_kstate_t *st, int role, const char *id);
int moy_kstate_open_id(const moy_kstate_t *st, const char **id);

#ifdef __cplusplus
}
#endif

#endif // MOY_CRASH_H
