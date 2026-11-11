// moy_super: the one supervisor of background callbacks (docs/os_2026-11/
// processes.md section 1, "The service model", and section 3's rungs), for
// every tier: the Python scheduler's wakes and asyncio steps, a Lua service's
// callbacks, a compiled service's events alike.
//
// A tier runs each callback under its own abort at MOY_SUPER_ABORT_US (the
// Python guard's timer, a Lua count hook) and charges what it cost here.
// A callback over MOY_SUPER_SLICE_US is an OVERRUN:
//
//   the first overrun          a warning in the OS log
//   every later one, and
//   every aborted callback     a strike, logged at err
//   MOY_SUPER_STRIKES strikes  the process is SUSPENDED: its tier serves none
//                              of its wakes until moy_super_resume
//
// The system (pid 0 and 1) is never struck. A charge within the slice and
// not aborted is not a call a tier makes: it changes nothing. The rows are
// keyed by pid, the oldest forgotten when MOY_SUPER_ROWS are taken; a new
// start is a new pid, so its rungs start afresh. Any task may call.

#ifndef MOY_SUPER_H
#define MOY_SUPER_H

#include <stdbool.h>
#include <stdint.h>

#define MOY_SUPER_SLICE_US 20000u       // a callback over this is an overrun
#define MOY_SUPER_ABORT_US 300000u      // a tier's abort: past a collection's pause
#define MOY_SUPER_STRIKES 3u            // strikes to suspension
#define MOY_SUPER_ROWS 24u

// What a charge did.
enum { MOY_SUPER_FINE = 0, MOY_SUPER_WARNED = 1, MOY_SUPER_STRUCK = 2, MOY_SUPER_SUSPENDED = 3 };

// A callback of process `pid` cost `cost_us`, `aborted` where its tier's abort
// ended it: the rung it reached, logged under `pid`.
int moy_super_charge(uint32_t pid, uint32_t cost_us, bool aborted);
// Whether `pid` is suspended.
bool moy_super_held(uint32_t pid);
// A suspended `pid` served again, its rungs from the start: whether it was.
bool moy_super_resume(uint32_t pid);
// `pid`'s overruns and strikes so far (0 and 0 for none).
void moy_super_info(uint32_t pid, uint32_t *overruns, uint32_t *strikes);
// `pid` ended: its row forgotten.
void moy_super_forget(uint32_t pid);
// Every row forgotten (a test's teardown).
void moy_super_reset(void);

#endif // MOY_SUPER_H
