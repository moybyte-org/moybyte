// moycore_svc: the Lua tier's services (docs/os_2026-11/processes.md
// sections 1 and 5, tiers.md section 2). A service is a Lua state of its own,
// with no console and no canvas, that sleeps on its timers between callbacks
// and lives until it is stopped, exits or strikes out, whatever app is on
// screen.
//
// WHERE IT RUNS. On the P4s each service runs on a task of its own, pinned to
// core 0 (MOYCORE_SVC_CORE) at MOYCORE_SVC_PRIO: the task makes the state,
// runs the chunk and `moy_start`, then waits for its next deadline or a stop
// and runs what is due, so a callback never runs on the VM's task. The S3s get
// no user service in Lua (the overview's section 5): a start there is BAD.
// The host and the desktop MicroPython have no task to give: their services
// run when moycore_svc_step is called (the scheduler's poll, once a frame), on
// the caller's task, the same code with the caller's clock.
//
// THE STATE has base (no dofile, no loadfile), coroutine, table, string and
// math, `os.time` and `os.clock`, and `moybyte`:
//   moybyte.every(ms, fn) -> id    fn every `ms` (1 at least), from now
//   moybyte.after(ms, fn) -> id    fn once, `ms` from now
//   moybyte.cancel(id)
//   moybyte.log(severity, text)    the OS log under the service's pid
//   moybyte.exit(code)             the service ends with `code`
// and `print`, which logs at info. Its allocator counts every byte it holds,
// in PSRAM on a board, so the task manager's figure is exact
// (moycore_svc_info's `heap`), and refuses past MOYCORE_SVC_HEAP_MAX.
//
// THE LIFE. The chunk, then `moy_start()` where it is defined, each under
// MOYCORE_SVC_STOP_MS: an error ends the service with exit 1, and one that
// leaves no timer ends it with 0. Each due timer's callback runs under
// MOYCORE_SVC_ABORT_US; a callback over it is aborted and struck, and the
// MOYCORE_SVC_STRIKES'th strike ends the service with 137; an error ends it
// with 1. A stop (TERM) runs `moy_stop()` under MOYCORE_SVC_STOP_MS: 0 when
// it returns, 137 (KILL) when it is aborted. A service whose last timer is
// gone has exited, 0. The state is closed on the service's task as it ends.
//
// THE ABORT. Every Lua call (the chunk, a hook, a callback) runs on a fresh
// coroutine with a count hook; past its budget the hook yields the coroutine,
// which is never resumed again, so a `pcall` in the service cannot catch the
// abort. Inside a C function that cannot yield (a sort's comparator) the hook
// raises instead, and yields at the first Lua instruction after it.
//
// Calls from the VM's task: start, stop, info, reap, reset; step is the
// stepping caller's. A row's state, exit, heap and cpu are written by the
// service's task and read by the VM's; a row is reaped (its slot freed) only
// after it has exited and its task is done.

#ifndef MOYCORE_SVC_H
#define MOYCORE_SVC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MOYCORE_SVC_OK = 0,
    MOYCORE_SVC_STALE = 1,      // no service by that handle
    MOYCORE_SVC_FULL = 2,       // every row is taken
    MOYCORE_SVC_NOMEM = 3,
    MOYCORE_SVC_BAD = 4,        // no Lua services on this chip
};

// A service's states.
enum {
    MOYCORE_SVC_STARTING = 1,
    MOYCORE_SVC_RUNNING = 2,
    MOYCORE_SVC_STOPPING = 3,
    MOYCORE_SVC_EXITED = 4,
};

#define MOYCORE_SVCS 4u                 // services at once
#define MOYCORE_SVC_TIMERS 16u          // timers a service holds
#define MOYCORE_SVC_STOP_MS 2000u       // a start's or a stop's limit (TERM to KILL)
#define MOYCORE_SVC_ABORT_US 300000u    // a callback's limit
#define MOYCORE_SVC_STRIKES 3u          // aborted callbacks to the end
#define MOYCORE_SVC_HEAP_MAX (1024u * 1024u)
#define MOYCORE_SVC_WHY 96u
#define MOYCORE_SVC_CORE 0
#define MOYCORE_SVC_PRIO 2
#define MOYCORE_SVC_STACK 12288u

#define MOYCORE_SVC_EXIT_CRASHED 1
#define MOYCORE_SVC_EXIT_KILLED 137

typedef struct {
    uint8_t state;
    uint8_t strikes;
    int32_t exit;               // once EXITED
    uint32_t heap;              // bytes its state holds now
    uint32_t cpu_us;            // its calls' time, summed (wrapping)
    uint32_t pid;
    char why[MOYCORE_SVC_WHY];  // the error that ended it, "" for none
} moycore_svc_info_t;

// Start service `pid` (the OS log's) running Lua source `src` of `n` bytes,
// named `id`: OK with *h, FULL, NOMEM, BAD where this chip runs none. The
// start itself runs on the service's task, or at the next step.
int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n, uint32_t *h);
// The TERM: its `moy_stop` runs next on its task. OK, STALE.
int moycore_svc_stop(uint32_t h);
int moycore_svc_info(uint32_t h, moycore_svc_info_t *out);
// An exited service's row freed: OK, STALE, or BAD while it has not exited.
int moycore_svc_reap(uint32_t h);
// The rows' handles, in slot order (0 where empty).
uint32_t moycore_svc_at(uint32_t slot);
// Whether each service has a task of its own here (the P4s).
bool moycore_svc_tasks(void);
// Where services have no task: run what is due on every service at
// `now_ms`. Answers the milliseconds to the next deadline (UINT32_MAX: none).
uint32_t moycore_svc_step(uint32_t now_ms);
// Every service ended where it stands and its row freed (a test's teardown;
// a stepped tier only).
void moycore_svc_reset(void);

#endif // MOYCORE_SVC_H
