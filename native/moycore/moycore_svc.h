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
//   moybyte.on(name, fn) -> id     fn(...) for each message `name` the OS
//                                  routes to it (MOYCORE_SVC_MESSAGES); a
//                                  message wake, which `cancel` ends too
//   moybyte.notify(summary[, body[, icon]]) -> id
//                                  a notification, the console's
//                                  moybyte.notify under the service's pid
// and `print`, which logs at info. Its allocator counts every byte it holds,
// in PSRAM on a board, so the task manager's figure is exact
// (moycore_svc_info's `heap`), and refuses past MOYCORE_SVC_HEAP_MAX.
//
// MESSAGES. moycore_svc_send queues a message on the service's row (its
// arguments a JSON array, decoded into the handler's arguments), at most
// MOYCORE_SVC_MAIL, the oldest dropped; a service with no wake on its name
// takes none. The service runs each at its next step, after its due timers:
// on its task, at once (a send wakes it).
//
// CONSOLE CALLS. What the console hands a service at its start (`env`, the
// JSON array [values, calls]): each of `values`' members a global, and each
// name in `calls` a global function whose call the console answers, as
// `moybyte.notify` is (its name "moybyte.notify"). Such a call enters the
// console, so it runs on the VM's task: the service's call yields its
// coroutine with the call posted on its row (its name and its arguments as a
// JSON array), the VM's task takes it (moycore_svc_call), runs it and
// answers (moycore_svc_answer: a JSON value, or none where the console
// refused, which the call answers as nil and why), and the service's next
// step resumes it with the answer. A call waits its answer apart from its
// budget: the abort counts the time it runs, not the time the VM takes to
// answer. A call from a coroutine the service made itself, or from where it
// cannot yield, is an error.
//
// THE LIFE. The chunk, then `moy_start()` where it is defined, each under
// MOYCORE_SVC_STOP_MS: an error ends the service with exit 1, and one that
// leaves no wake (a timer or a message wake) ends it with 0. Each due timer's callback runs under
// MOYCORE_SVC_ABORT_US; a callback over it is aborted and struck, and the
// MOYCORE_SVC_STRIKES'th strike ends the service with 137; an error ends it
// with 1. A message's handler is a callback alike. A stop (TERM) runs
// `moy_stop()` under MOYCORE_SVC_STOP_MS: 0 when it returns, 137 (KILL) when
// it is aborted. A service whose last wake is gone has exited, 0. The state
// is closed on the service's task as it ends.
//
// THE ABORT. Every Lua call (the chunk, a hook, a callback) runs on a fresh
// coroutine with a count hook; past its budget the hook yields the coroutine,
// which is never resumed again, so a `pcall` in the service cannot catch the
// abort. Inside a C function that cannot yield (a sort's comparator) the hook
// raises instead, and yields at the first Lua instruction after it.
//
// Calls from the VM's task: start, stop, send, call, answer, info, reap,
// reset; step is the stepping caller's. A row's state, exit, heap and cpu are
// written by the service's task and read by the VM's, its mail is taken
// under a lock, and a console call passes between the two with the row's
// `req` (posted by the service, answered by the VM); a row is reaped (its
// slot freed) only after it has exited and its task is done.

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
#define MOYCORE_SVC_ONS 4u              // message wakes a service holds
#define MOYCORE_SVC_MAIL 16u            // messages queued on a service (moybyte.py's MAIL_MAX)
#define MOYCORE_SVC_NAME 24u            // a message's or a console call's name, its NUL counted
// The messages the OS routes to a process (runtime/system/moybyte.py's
// MESSAGES): the notify role's events.
#define MOYCORE_SVC_MESSAGES "achieve", "award"
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
    uint8_t wakes;              // its timers and message wakes
    uint8_t mail;               // the messages queued on it
} moycore_svc_info_t;

// Start service `pid` (the OS log's) running Lua source `src` of `n` bytes,
// named `id`, with what the console hands it in `env` (envn bytes, the JSON
// array [values, calls]; NULL for nothing): OK with *h, FULL, NOMEM, BAD
// where this chip runs none. The start itself runs on the service's task, or
// at the next step.
int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n,
                      const char *env, size_t envn, uint32_t *h);
// Message `name` with `args` (a JSON array, n bytes) to the service: OK
// queued, STALE, BAD where it holds no wake on `name`.
int moycore_svc_send(uint32_t h, const char *name, const char *args, size_t n);
// The console call the service waits on, unanswered: OK with its name and
// its arguments (a JSON array, *n bytes), which stay the row's until it is
// answered; STALE; BAD where it waits on none.
int moycore_svc_call(uint32_t h, const char **name, const char **args, size_t *n);
// The console's answer to that call: a JSON value of `n` bytes, or NULL
// where the console refused it. OK, STALE, BAD where it waits on none.
int moycore_svc_answer(uint32_t h, const char *ans, size_t n);
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
