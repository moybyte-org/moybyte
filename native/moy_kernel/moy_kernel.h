// moy_kernel: the kernel's entry, its crash intake and its recovery floor, as
// the binding (modmoy_kernel.c) reaches them. moy_kernel.c has the design.

#ifndef MOY_KERNEL_H
#define MOY_KERNEL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

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

// The task watchdog the console's frame feeds (#160): feed once per frame (the
// first subscribes the VM task), rest when the loop ends. watchdog() answers
// whether it is armed, its timeout, and the longest gap between two feeds and
// the feeds counted since armed (or since `reset`).
void moy_kernel_feed(void);
void moy_kernel_rest(void);
bool moy_kernel_watchdog(uint32_t *timeout_ms, uint32_t *max_gap_ms, uint32_t *frames, bool reset);

// Dev only: arm a MOY_TEST_* one-shot and restart; crash the board now.
void moy_kernel_test_restart(int test);
void moy_kernel_test_crash(bool abort_not_fault);

// kstop: N soft resets of the VM service, counted (the dev channel's word).
// When the kernel lit the glass with the logo, ms after power-on; 0: it did not.
uint32_t moy_kernel_lit_ms(void);
void moy_kernel_kstop(int n);
// The console proved itself again: true while kstop has a reset left to run.
bool moy_kernel_kstop_next(void);

// THE VM STOP (docs/kernel_cartpath_2026-10.md section 5). moy_kernel_stop
// makes the next teardown a stop -- MOY_STOP_RUN for the run the Player
// launched, MOY_STOP_KSTOP for `kstop N stop` -- and ends the loop with
// MOY_LOOP_STOP at the frame's end. How the running VM started: BOOT (power-on,
// a soft reset) or RETURN (after a stop: the kernel's tables and the resume
// record are as the stopped VM left them).
enum { MOY_STOP_NONE = 0, MOY_STOP_KSTOP = 1, MOY_STOP_RUN = 2 };
enum { MOY_START_BOOT = 0, MOY_START_RETURN = 1 };
void moy_kernel_stop(int why);
int moy_kernel_stop_pending(void);
int moy_kernel_start(void);
// `kstop N stop`: N real stops and starts with no cart, each with a route and
// a lease set on the kernel's tables before it and read back after.
void moy_kernel_kstop_stop(int n);
// The resume record: what the launcher writes before a stop and reads at the
// return start (its place on the shelf), PSRAM, never flash.
#define MOY_KERNEL_RESUME_MAX 256
void moy_kernel_resume_set(const char *text, size_t n);
size_t moy_kernel_resume(const char **text);
// A line out through the kernel's serial path, with no VM's stream.
void moy_kernel_say(const char *line);
// One line of the heaps: `tag 1/1 when psram=FREE/LARGEST int=... dma=...`
// and `extra` (the gate's PSRAM reading after a stop is "STOP 1/1 down").
void moy_kernel_heaps(const char *tag, const char *when, const char *extra);
// DEV: PSRAM's large blocks by address (`HEAPWALK tag used|free ADDR SIZE`),
// now, or (`at_stop`) before every heaps line read at a stop's down point and
// at its run's end; `all` lists every used block, not only the large.
void moy_kernel_heapwalk(const char *tag);
void moy_kernel_heapwalk_at_stop(bool on, bool all);

// THE START'S STAMPS (docs/kernel_cartpath_2026-10.md section 5.5): ms after
// power-on of each part of a VM's start. EXIT is the last VM's end -- the
// stopped run's end at a RETURN start, the teardown's first moment at a soft
// reset, 0 at power-on -- and clears the rest; VM is the next VM's mp_init;
// IMPORTS, WS and WIRED are the console's (desktop_spine: its build begins,
// the Workstation is built, the wiring is done); FRAME is boot_ok, the first
// frame the new VM drew (the first after each VM only). 0: not reached.
enum {
    MOY_STAMP_EXIT = 0, MOY_STAMP_VM = 1, MOY_STAMP_IMPORTS = 2, MOY_STAMP_WS = 3,
    MOY_STAMP_WIRED = 4, MOY_STAMP_FRAME = 5, MOY_STAMPS = 6,
};
void moy_kernel_stamp(int part);
const uint32_t *moy_kernel_stamps(void);

#endif // MOY_KERNEL_H
