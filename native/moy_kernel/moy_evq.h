// moy_evq: every process's event queue and the one wait (docs/os_2026-11/
// processes.md section 1, "The service model").
//
// A QUEUE is a process's, by pid: the messages the OS routed to it,
// MOY_EVQ_DEPTH at most. The OS opens it at the process's start with the
// process's app id and the names its manifest lists under `receives`, and
// closes it at its end; a closed queue's waiter is woken and its events freed.
//
// BACKPRESSURE. A sender holds MOY_EVQ_SENDER unread messages on one queue at
// most: past that, or on a queue holding MOY_EVQ_DEPTH, a post or a send
// answers FULL and queues nothing, so a flood stops at its sender and never
// pushes out another sender's messages. The system (pid 0 and 1, one sender)
// is never refused: its post drops its own oldest there, else the queue's
// oldest.
//
// IN TURN. A take hands the oldest message of the sender served longest ago
// (one never served first), so each sender's messages arrive in its order and
// senders take turns: a message from a second sender is the next take behind
// a flood. A STEP (moy_evq_step) hands MOY_EVQ_BATCH at most: past that a
// take answers EMPTY with the rest still queued, until the next step. Each
// tier steps where its process's wake begins (the scheduler's mail() for the
// VM's queues, a Lua service's run, a compiled program's poll), so one wake
// sums at most MOY_EVQ_BATCH messages' work under the supervisor's slice. A
// queue never stepped is not bounded (a compiled app's moybyte_recv).
//
// A MESSAGE is a name, the sender's pid and app (STAMPED here from the
// sender's own queue, so a process cannot claim another app's name; pid 0 and
// 1 are the system, MOY_EVQ_SYSTEM) and its arguments, a JSON array. The OS
// events are messages from the system: the radios' "net" and "ble" among
// them.
//
// THE RULE. A message reaches a process that LISTENS on its name (a language
// library's `on(name, fn)`), and then only when
//   - the sender is the system, or
//   - the sender runs the receiver's own app, or
//   - the receiver's manifest lists the name under `receives`;
// any other is DENIED. A `send` names an app, and reaches each listening
// process of it but the sender itself.
//
// THE WAIT. A process with a task of its own (a Lua, wasm or ELF service on a
// board) blocks in moy_evq_wait until an event is queued, it is kicked (a
// stop, an answer from the VM) or its own nearest deadline passes: the one
// kernel wait each language wraps (Lua's every/on, WASI's poll_oneoff over its
// event descriptor, ELF's poll, the shell loop for the Python VM's
// processes). A queue flagged MOY_EVQ_VM is the VM's task's to serve: its
// processes run in the one MicroPython VM, whose loop looks at
// moy_evq_ready() rather than blocking per process.
//
// THE VM'S WAKE. The VM's task blocks on no queue either: a process on a task
// of its own that needs it WANTS it, and a board's loop asks once a frame
// (moy_loop.h's MOY_SVC_TASKS), so the scheduler's Python runs only when one
// did (runtime/system/scheduler.py, `woken`). A want is a bit, kept until the
// VM takes it:
//   MOY_EVQ_WANT_MAIL  a message queued on a MOY_EVQ_VM queue
//   MOY_EVQ_WANT_POLL  a call or hop on the VM's queue (moy_vmq.h), a process
//                      on a task ended or killed (its reap is due), a rung
//                      the supervisor moved (moy_super.h), or a name a queue
//                      that is not the VM's listens on taken or given up
//
// THE EVENT DESCRIPTOR. A compiled program reads its queue as descriptor
// MOY_EVQ_FD, the same number on both compiled tiers (WASI's fd_read and
// poll_oneoff, native/moy_wasm/moy_wasi.h; an ELF module's read and poll,
// native/moy_elf/moy_os.h). It is readable while an event is queued, and a
// read takes one RECORD (moy_evq_read): the event's moy_evq_ev_t, then its
// arguments, then a NUL. A read never blocks; the wait is the poll's, with
// the program's nearest deadline as its timeout. sdk/include/moybyte/os.h
// carries the record, MOY_EVQ_FD and MOY_EVQ_VERSION from here.
//
// TIMERS. A process on a task of its own (a compiled program's moy_every and
// moy_after; docs/os_2026-11/processes.md section 1) holds its timers on its
// queue, MOY_EVQ_TIMERS at most: each posts it MOY_EVQ_TIMER from the system,
// its one argument the timer's id, when it is due, and again every `every`
// ms after (a tick its queue cannot take is dropped). They are the kernel's,
// so they run on through a VM stop, and they go with the queue. The kernel's
// frame runs them (moy_evq_timers_run, a loop job while one is set); a
// harness with no frames, and the host's scheduler, call it. An ALARM
// (moy_at, a Lua service's moybyte.at: the console turns its wall time into
// ms from now) is a timer of the same table that goes off once, posts
// MOY_EVQ_AT instead and names its process to the console's screen wake
// (moy_evq_woke), so a kept service's alarm goes off through a VM stop too.
// moy_evq_at sets one at a wall time, on the caller's own task, for a
// process whose queue was opened MOY_EVQ_ALARMS (its `alarms` permission):
// every tier's alarm but a Python process's (moy_app's moybyte.at row, a Lua
// service's moybyte.at).
// An alarm keeps its wall time: where the wall clock steps (an NTP answer,
// the first one too: native/moy_net/moy_wifi.c, sntp_sync_time) its due moves
// by the step (moy_evq_clock), one the step passed going off at the next run.
//
// Any task may call any function; no ISR may. Posting allocates the
// message's copy (outside the lock); nothing else allocates but an open.

#ifndef MOY_EVQ_H
#define MOY_EVQ_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MOY_EVQ_OK = 0,
    MOY_EVQ_STALE = 1,          // no queue for that pid
    MOY_EVQ_FULL = 2,           // every queue is taken; the receiver holds the sender's
                                // MOY_EVQ_SENDER, or MOY_EVQ_DEPTH (BACKPRESSURE)
    MOY_EVQ_NOMEM = 3,
    MOY_EVQ_DENIED = 4,         // the rule refused it
    MOY_EVQ_DEAF = 5,           // the receiver does not listen on that name
    MOY_EVQ_EMPTY = 6,          // nothing queued
    MOY_EVQ_RANGE = 7,          // the caller's buffer is too small; the event stays
};

#define MOY_EVQ_PROCS 24u           // queues at once
#define MOY_EVQ_DEPTH 64u           // events a queue holds
#define MOY_EVQ_SENDER 16u          // ... of one sender's
#define MOY_EVQ_BATCH 4u            // events a step hands
#define MOY_EVQ_PEERS 8u            // senders a queue remembers serving (IN TURN)
#define MOY_EVQ_NAME 24u            // a message's name, its NUL counted
#define MOY_EVQ_APP 48u             // an app id, its NUL counted
#define MOY_EVQ_LISTEN 8u           // names a process listens on at once
#define MOY_EVQ_SYSTEM "system"     // the app a message from pid 0 or 1 is from
#define MOY_EVQ_TIMERS 16u          // timers a queue holds
#define MOY_EVQ_TIMER "moybyte.timer"   // a timer's message
#define MOY_EVQ_AT "moybyte.at"         // an alarm's message
#define MOY_EVQ_TIMER_ID 0x40000000u    // a timer's id has this bit: apart from the console's wakes

#define MOY_EVQ_VM 0x01u            // the VM's task serves it (a Python process)
#define MOY_EVQ_ALARMS 0x02u        // its process holds `alarms` (moy_evq_at)

#define MOY_EVQ_FD 31               // the event descriptor
#define MOY_EVQ_VERSION 1           // the record's layout

// Process `pid`'s queue opened (or, open already, its app and receives
// replaced): `app` its app id, `receives` the names its manifest lists, one
// per line (NULL for none). OK, FULL, NOMEM.
int moy_evq_open(uint32_t pid, const char *app, const char *receives, uint32_t flags);
// Its queue closed: its events freed, its waiter woken, and the process's
// device handles closed (moy_dev_sweep, moy_dev.h): a queue's close is its
// process's end. OK, STALE.
int moy_evq_close(uint32_t pid);
// Whether `pid` has a queue.
bool moy_evq_has(uint32_t pid);
// The app id `pid`'s queue was opened with, MOY_EVQ_SYSTEM for pid 0 and 1,
// NULL for none.
const char *moy_evq_app(uint32_t pid);
// `pid` listens on `name` (counted: each `on` one, each cancel one less), or
// stops. OK, STALE, FULL where it listens on MOY_EVQ_LISTEN names already.
int moy_evq_listen(uint32_t pid, const char *name, bool on);
// Whether `pid`'s queue listens on `name` (false for no queue).
bool moy_evq_hears(uint32_t pid, const char *name);

// Message `name` with `args` (a JSON array, n bytes) from process `from` to
// process `to`, under the rule. OK, STALE (either has no queue; pid 0 and 1
// need none to send), DEAF, DENIED, FULL (BACKPRESSURE), NOMEM.
int moy_evq_post(uint32_t to, uint32_t from, const char *name, const char *args, size_t n);
// Message `name` from process `from` to every process of app `app` that
// listens on it, the sender apart, under the rule: how many took it (0 or
// more), -MOY_EVQ_STALE where the sender has no queue, -MOY_EVQ_FULL where
// none took it and a listener was full (BACKPRESSURE). *denied (may be NULL)
// counts the listeners the rule refused.
int moy_evq_send(uint32_t from, const char *app, const char *name, const char *args, size_t n,
                 uint32_t *denied);
// An OS event: message `name` from the system (the kernel, pid 0) to every
// process that listens on it, the shell included. How many took it.
int moy_evq_route(const char *name, const char *args, size_t n);

typedef struct {
    uint32_t from;              // the sender's pid
    uint32_t n;                 // its arguments' bytes
    char app[MOY_EVQ_APP];      // the sender's app id
    char name[MOY_EVQ_NAME];
} moy_evq_ev_t;

// `pid`'s next event taken (IN TURN): its record in *ev, its arguments into
// `args` (cap bytes, NUL-terminated where they fit). OK, STALE, EMPTY (none,
// or the step's MOY_EVQ_BATCH taken), RANGE (ev->n says what it needs; the
// event stays queued).
int moy_evq_take(uint32_t pid, moy_evq_ev_t *ev, char *args, size_t cap);
// `pid`'s next event taken as the event descriptor's RECORD into `buf`
// (cap bytes): its moy_evq_ev_t, its arguments and a NUL, *len the record's
// bytes (the NUL not counted). OK, STALE, EMPTY, RANGE (the event stays; its
// moy_evq_ev_t is written where cap holds one, its `n` what the arguments
// need).
int moy_evq_read(uint32_t pid, void *buf, size_t cap, size_t *len);
// The events queued on `pid`'s queue (0 for none).
uint32_t moy_evq_queued(uint32_t pid);
// A MOY_EVQ_VM queue with an event its step may still hand, whose process
// the supervisor has not suspended (moy_super.h), taken in turn so none
// starves: its pid, 0 for none.
uint32_t moy_evq_ready(void);
// A new STEP for `pid`'s queue: MOY_EVQ_BATCH takes afresh; the events it
// holds. `pid` 0: every MOY_EVQ_VM queue's; how many of them hold an event
// and are not suspended.
uint32_t moy_evq_step(uint32_t pid);

// Block the calling task until `pid`'s queue has an event, it is kicked, or
// `ms` passes (0: look and return). How many events are queued then. A tier
// with no tasks never blocks.
uint32_t moy_evq_wait(uint32_t pid, uint32_t ms);
// `pid`'s waiter woken with no event (a stop, an answer, a resume).
void moy_evq_kick(uint32_t pid);
// `pid`'s waiter forgotten, so no wake reaches it: its task is about to be
// deleted while the queue lives on (the queue's owner closes it later).
void moy_evq_unwait(uint32_t pid);

// The VM's end (a stop, a soft reset): every MOY_EVQ_VM queue closed, the
// VM's processes having ended with it; a queue of a process on a task of its
// own (a service that runs through the stop) stays. How many it closed.
uint32_t moy_evq_vm_stop(void);
// How many queues are open.
uint32_t moy_evq_live(void);

#define MOY_EVQ_WANT_MAIL 0x01u
#define MOY_EVQ_WANT_POLL 0x02u
// The VM wanted for `bits` (THE VM'S WAKE), from any task.
void moy_evq_want(uint32_t bits);
// What is wanted, left set (the loop's question).
uint32_t moy_evq_wants(void);
// What is wanted of `mask`, taken (the VM's task, before it does it).
uint32_t moy_evq_wanted(uint32_t mask);
// How many queues that are not the VM's listen on `name`.
uint32_t moy_evq_listening(const char *name);

// TIMERS: a timer of `pid`'s queue, due `ms` from now and then every `every`
// ms (0: once), the queue made to listen on MOY_EVQ_TIMER: OK with *id,
// STALE (no queue), FULL (MOY_EVQ_TIMERS held, or no name left to listen
// on). Cancel: whether `pid` held timer `id`. How many it holds.
int moy_evq_timer(uint32_t pid, uint32_t ms, uint32_t every, uint32_t *id);
// An alarm: a timer due once, `ms` from now, posting MOY_EVQ_AT (listened on).
int moy_evq_alarm(uint32_t pid, uint32_t ms, uint32_t *id);
// An alarm at wall time `t`, Unix seconds (the clock time() reads; one already
// past is due now): moy_evq_alarm's answers, and DENIED where `pid`'s queue
// was not opened MOY_EVQ_ALARMS.
int moy_evq_at(uint32_t pid, int64_t t, uint32_t *id);
// The wall clock stepped by `step_ms` (later: positive): each alarm's due
// moved by as much, so it keeps its wall time.
void moy_evq_clock(int64_t step_ms);
// The pid whose alarm went off next, oldest first (the console's screen wake;
// eight kept at most), 0 for none.
uint32_t moy_evq_woke(void);
bool moy_evq_timer_cancel(uint32_t pid, uint32_t id);
uint32_t moy_evq_timers(uint32_t pid);
// The due timers posted: the ms to the next one, UINT32_MAX for none.
uint32_t moy_evq_timers_run(void);

// Every queue closed (a test's teardown).
void moy_evq_reset(void);

#endif // MOY_EVQ_H
