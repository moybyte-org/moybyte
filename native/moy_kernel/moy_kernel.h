// moy_kernel: the kernel's entry, its crash intake and its recovery floor, as
// the binding (modmoy_kernel.c) reaches them. moy_kernel.c has the entry's
// design; this header holds the rules every kernel module keeps. The kernel's
// decisions (its language, why the apps stay Python) are this directory's
// README.
//
// HANDLES, NOT OBJECTS. Kernel state holds no runtime's object beyond a call.
// A runtime holds integer handles -- index plus generation, moy_htab.h --
// validated against a live table on every use, so a handle that outlived its
// VM fails loudly ("loud beats corrupt"). That is what makes a VM stop safe.
// (The surface model's graveyard buried per-object generations because a web
// client's cache aliased a recreated surface; here the table's owner checks
// every use and there is no remote cache.)
//
// KERNEL DATA IS PSRAM. Moving state out of the GC heap into C can be a net
// internal SRAM loss (.bss, task stacks and small mallocs land internal, and
// the S3's internal budget is shared with WiFi, BLE and the panel's DMA), so
// kernel data is allocated with heap_caps(MALLOC_CAP_SPIRAM) unless it is
// latency-bound, and tests/test_moy_spine_twins.py asserts the placement. The
// kernel's share is configuration (native/moy_play/moy_play.h, "the shares").
//
// STOPPING THE VM. The VM runs the console; the kernel stops it for a run
// that needs no VM when moy_play.h's verdict says so, and a board without the
// lever (the P4s) never stops it. EMBED, NOT FORK (owner, 2026-10-05): MicroPython is a service
// the kernel starts and stops on its own task inside the esp32 port's build
// (MICROPY_ESP_IDF_ENTRY renames the port's app_main). A fork would rebase a
// multi-hunk patch over mp_task on every tag bump and inherit a teardown list
// wrong for a stop with the kernel up; the embed links against the port's
// functions, so a rename fails the build, and mp_task_calls.txt with
// tools/mp_task_calls.py fails it when the tag's call list moves (v1.29.0
// drops machine_timer_deinit_all, so the stop stops timers itself). The ORDER
// of a stop: the app's close(); the cooperative stops (threads joined,
// moycore's close() -- which ends a wasm session and frees the Lua state --
// the flush feeder drained and the fold disarmed, async copies waited:
// moy_glass_vm_stop); the port's deinit list, with the pin sweep replaced by
// one that removes only the pins a Python handler holds, so the kernel's GPIO
// ISRs survive; gc_sweep_all, whose finalisers close files, sockets, I2S and
// SD and return the glass rows Python held (moy_glass_vm_swept; the theme
// fonts, moy_text_vm_stop); the native-code arena freed; mp_deinit; the root
// section ZEROED (a stale root would mark whatever sits at its address in the
// next first area); the first heap area freed; the console's stdin ring and
// the interrupt character taken by the kernel, which drains the ring and
// re-polls the USB FIFO while no VM runs (a host writing through a stop
// otherwise stalls the console's input for good). The VM's task is NOT
// deleted: it drives the kernel's frame with no VM until the stop's run ends,
// then starts the next VM (a RETURN start). What C owns survives: the input
// task, the BLE central, the ESP-NOW link and its ring, the WiFi station and
// its lease table, the card's FATFS and the internal flash volume, the driver
// buffers and native tasks (the audio task silences the cart's sound). A stop
// is refused while a Python owner holds a WiFi lease or a hardware lease
// (moy_lease.h's MOY_LEASE_VM) or an OTA write runs.
// An app that is stopped comes back from persisted state: close() is the
// leaving hook, commit() the forced save, open() re-enters.

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

// kstop: N soft resets of the VM service, counted (the dev channel's word),
// heapcaps printed before and after each. Test-only, never in a kid's
// vocabulary: the executable guard that a teardown leaves the kernel's drivers
// alive, with moy_loop_task presenting across each window. `kstop N stop` runs
// N real VM stops instead, each printing a third line, `KSTOP i/n down`, with
// the heaps read while no VM runs and the route and lease set before the stop
// read back (`route=kstop lease=dev`); the start after it is a RETURN start.
// When the kernel lit the glass with the logo, ms after power-on; 0: it did not.
uint32_t moy_kernel_lit_ms(void);
void moy_kernel_kstop(int n);
// The console proved itself again: true while kstop has a reset left to run.
bool moy_kernel_kstop_next(void);

// THE VM STOP (docs/history/kernel_cartpath_2026-10.md section 5). moy_kernel_stop
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

// THE START'S STAMPS (docs/history/kernel_cartpath_2026-10.md section 5.5): ms after
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

// THE KERNEL'S SMALL-BLOCK POOL (moy_kernel.c). A kernel table or string that
// outlives the call that made it -- a store marker, a glass or audio table's
// rows, a spine table's growth -- made while a VM is up would land among the
// VM's heap areas, and a VM stop would find it splitting the free run it gives
// the next cart (#224). The store, the glass, the audio mixer and the spine
// take every block of at most MOY_KPOOL_MAX bytes from this pool, made before
// the VM's first area and below every area a VM holds; a larger block, or one
// the full pool cannot take, comes from PSRAM as before.
#define MOY_KPOOL_BYTES (64u * 1024u)
#define MOY_KPOOL_MAX 1024u
// A zeroed block from the pool, or NULL: too big, the pool full, or no pool.
void *moy_kpool_alloc(size_t n);
// Frees `p` when it is the pool's: false when it is not.
bool moy_kpool_free(void *p);
// (bytes in use at the high water, allocations the pool could not take)
void moy_kpool_stats(size_t *high, uint32_t *misses);

#endif // MOY_KERNEL_H
