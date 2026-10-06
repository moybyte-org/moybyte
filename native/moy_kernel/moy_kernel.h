// moy_kernel: the kernel's entry, its crash intake and its recovery floor, as
// the binding (modmoy_kernel.c) reaches them. moy_kernel.c has the design.

#ifndef MOY_KERNEL_H
#define MOY_KERNEL_H

#include <stdbool.h>

#include "moy_crash.h"

// How this boot started the VM: MOY_BOOT_START, SAFE or REPL.
int moy_kernel_mode(void);

// The console painted its first frame (DeviceBoot.first_frame), or a
// developer's Ctrl-C asked for the REPL: this start is not a failed one.
void moy_kernel_boot_ok(void);

// What the console's boot said when it failed, for the record.
void moy_kernel_boot_failed(const char *what);

// The ledger's OPEN id for a role, mirrored into RTC memory.
void moy_kernel_arm(int role, const char *id);

// The last crash this board recorded (NVS), or NULL. `fresh`: the record this
// boot's intake took, once.
const moy_crash_rec_t *moy_kernel_last_crash(void);
const moy_crash_rec_t *moy_kernel_take_crash(void);

const char *moy_kernel_reset_name(int reason);

// Dev only: arm a MOY_TEST_* one-shot and restart; crash the board now.
void moy_kernel_test_restart(int test);
void moy_kernel_test_crash(bool abort_not_fault);

#endif // MOY_KERNEL_H
