// moycore_svc: the Lua tier's services (docs/os_2026-11/processes.md
// sections 1 and 5, tiers.md section 2). A service is a Lua state of its own,
// with no console and no canvas, that sleeps on its timers between callbacks
// and lives until it is stopped, exits or strikes out, whatever app is on
// screen.
//
// It is the Lua face of the one service model (docs/os_2026-11/processes.md
// section 1): its timers are this library's, its messages its process's
// event queue (native/moy_kernel/moy_evq.h), its rungs the one supervisor's
// (moy_super.h) and its calls into the console the VM's queue (moy_vmq.h).
//
// WHERE IT RUNS. On a board each service runs on a task of its own at
// MOYCORE_SVC_PRIO, pinned to core 0 on the P4s and to core 1 on the S3s,
// whose stack is in PSRAM there: the task makes the state, runs the chunk and
// `moy_start`, then blocks in the one kernel wait (moy_evq_wait) until its
// next deadline, a message, an answer or a stop, and runs what is due, so a
// callback never runs on the VM's task. The host and the desktop MicroPython have no task to give: their services
// run when moycore_svc_step is called (the scheduler's poll, every POLL_MS), on
// the caller's task, the same code with the caller's clock.
//
// THE STATE has base (no dofile, no loadfile), coroutine, table, string and
// math, `os.time` and `os.clock` (the service's own time), the role names
// its app's run grants (below), and `moybyte`:
//   moybyte.every(ms, fn) -> id    fn every `ms` (1 at least), from now
//   moybyte.after(ms, fn) -> id    fn once, `ms` from now
//   moybyte.cancel(id)
//   moybyte.log(severity, text)    the OS log under the service's pid
//   moybyte.exit(code)             the service ends with `code`
//   moybyte.on(name, fn) -> id     fn(sender, ...) for each message `name`
//                                  on its event queue, `sender` the app id
//                                  the OS stamped on it ("system" for the
//                                  OS's own: "net" and "ble" are
//                                  fn("system", up, why) as WiFi or BLE comes
//                                  up or goes down); a message wake, which
//                                  `cancel` ends too
//   moybyte.notify(summary[, body[, icon]]) -> id
//                                  a notification, the console's
//                                  moybyte.notify under the service's pid
//   moybyte.open_with(item[, choose]) -> bool
//   moybyte.share(data, mime) -> bool
//                                  an item (a URL or a user file's path, "Documents/todo.md") or
//                                  text routed to the app that opens it, the
//                                  console's own (runtime/system/intents.py)
//   moybyte.send(app, name, ...) -> n
//                                  message `name` to the processes of app
//                                  `app` that listen on it, under the rule
//                                  (moy_evq.h): how many took it
// and `print`, which logs at info. Its allocator counts every byte it holds,
// in PSRAM on a board, so the task manager's figure is exact
// (moycore_svc_info's `heap`), and refuses past MOYCORE_SVC_HEAP_MAX.
//
// MESSAGES are its process's event queue's (moy_evq.h), opened by the OS at
// its start with its app id and its manifest's `receives` (the start opens
// one with its id alone where none is open, and closes that one at its end).
// Each `on` listens on its name; a message taken from the queue is handed to
// each of its wakes on the name, its JSON array decoded into the handler's
// arguments after the sender. The service runs them at its next step, after
// its due timers: on its task, at once (a post wakes it).
//
// CONSOLE CALLS. What the console hands a service at its start (`env`, the
// JSON array [values, calls]): each of `values`' members a global, and each
// name in `calls` a global function whose call the console answers, as
// `moybyte.notify` is (its name "moybyte.notify"). Such a call enters the
// console, so it runs on the VM's task (`moybyte.open_with`, `moybyte.share`
// and `moybyte.send` are such calls too): the service's call yields its
// coroutine with the call posted on the VM's queue (moy_vmq_post: its name
// and its arguments as a JSON array), the VM's task takes it (moy_vmq_next),
// runs it and answers (moy_vmq_answer: a JSON value, or none where the
// console refused, which the call answers as nil and why), which wakes the
// service, and its next step resumes it with the answer. A call waits its answer apart from its
// budget: the abort counts the time it runs, not the time the VM takes to
// answer. A call from a coroutine the service made itself, or from where it
// cannot yield, is an error.
//
// ITS APP'S ROLES. The run a start is handed (native/moy_app/moy_app_run.h:
// the service's own grant, made from its permissions as its app's
// foreground run's is, owned by its pid) opens the same names a Lua app's
// state opens (native/moy_app/moy_app_lua.h): `files`, `io` and
// `os.remove`/`os.rename` with files, `prefs`, `set_theme`, `open_app`, and
// none it is not granted. Each row runs on the VM's task: on the service's
// own task the binding's hop is a hop on the VM's queue (moy_vmq_hop) and the
// task waits until the VM's task runs it at its next moy_vmq_serve (the
// scheduler's poll), its wait not counted against the call's budget; a
// stepped service is on the VM's task already and calls the row in place.
//
// THE LIFE. The chunk, then `moy_start()` where it is defined, each under
// MOYCORE_SVC_STOP_MS: an error ends the service with exit 1, and one that
// leaves no wake (a timer or a message wake) ends it with 0. Each due
// timer's callback runs under MOY_SUPER_ABORT_US and is charged to the one
// supervisor (moy_super.h): an overrun of MOY_SUPER_SLICE_US or an abort is a
// rung, and a suspended service serves none of its timers or messages until
// it is resumed; an error ends it with 1. A message's handler is a callback
// alike. A stop (TERM) runs
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
// Calls from the VM's task: start, stop, info, reap, reset; step is the
// stepping caller's. A row's state, exit, heap and cpu are written by the
// service's task and read by the VM's; messages, console calls and hops pass
// through the kernel's queues, each under its own lock; a row is reaped (its
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
    MOYCORE_SVC_BAD = 4,        // a reap before its service has exited
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
#define MOYCORE_SVC_STOP_MS 2000u       // a start's or a stop's limit (TERM to KILL)
#define MOYCORE_SVC_HEAP_MAX (1024u * 1024u)
#define MOYCORE_SVC_WHY 96u
#define MOYCORE_SVC_PRIO 2
#define MOYCORE_SVC_STACK 12288u

// A crash's exit status; a kill's is the process table's MOY_PROC_KILLED
// (moy_proc_exit.h).
#define MOYCORE_SVC_EXIT_CRASHED 1

typedef struct {
    uint8_t state;
    uint8_t strikes;            // the supervisor's (moy_super.h)
    int32_t exit;               // once EXITED
    uint32_t heap;              // bytes its state holds now
    uint32_t cpu_us;            // its calls' time, summed (wrapping)
    uint32_t pid;
    char why[MOYCORE_SVC_WHY];  // the error that ended it, "" for none
    uint8_t wakes;              // its timers and message wakes
    uint8_t mail;               // the messages queued on its event queue
} moycore_svc_info_t;

// Start service `pid` (the OS log's) running Lua source `src` of `n` bytes,
// named `id`, with what the console hands it in `env` (envn bytes, the JSON
// array [values, calls]; NULL for nothing) and its app's run `app` (copied;
// NULL for none, no role names): OK with *h, FULL, NOMEM, BAD where this chip
// runs none. The start itself runs on the service's task, or at the next step.
struct moy_app_run;
int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n,
                      const char *env, size_t envn, const struct moy_app_run *app,
                      uint32_t *h);
// The TERM: its `moy_stop` runs next on its task. OK, STALE.
int moycore_svc_stop(uint32_t h);
int moycore_svc_info(uint32_t h, moycore_svc_info_t *out);
// An exited service's row freed: OK, STALE, or BAD while it has not exited.
int moycore_svc_reap(uint32_t h);
// The rows' handles, in slot order (0 where empty).
uint32_t moycore_svc_at(uint32_t slot);
// Whether each service has a task of its own here (a board).
bool moycore_svc_tasks(void);
// Where services have no task: run what is due on every service at
// `now_ms`. Answers the milliseconds to the next deadline (UINT32_MAX: none).
uint32_t moycore_svc_step(uint32_t now_ms);
// Every service ended where it stands and its row freed (a test's teardown;
// a stepped tier only).
void moycore_svc_reset(void);
// The VM is going (a VM stop, on the VM's task while the VM stands): each
// service, TERMed by its scheduler just before, is given until
// MOYCORE_SVC_STOP_MS to end, its console calls refused and its hops served
// meanwhile, and its row and its event queue freed, since no reaper outlives
// the VM; one that has not ended by then is freed by the first start after
// it ends. A stepped tier's are reset.
void moycore_svc_vm_stop(void);

#endif // MOYCORE_SVC_H
