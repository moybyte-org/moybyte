// moy_evq: every process's event queue and the one wait (docs/os_2026-11/
// processes.md section 1, "The service model").
//
// A QUEUE is a process's, by pid: the messages the OS routed to it, oldest
// first, MOY_EVQ_DEPTH at most (a post to a full queue drops its oldest). The
// OS opens it at the process's start with the process's app id and the names
// its manifest lists under `receives`, and closes it at its end; a closed
// queue's waiter is woken and its events freed.
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
    MOY_EVQ_FULL = 2,           // every queue is taken
    MOY_EVQ_NOMEM = 3,
    MOY_EVQ_DENIED = 4,         // the rule refused it
    MOY_EVQ_DEAF = 5,           // the receiver does not listen on that name
    MOY_EVQ_EMPTY = 6,          // nothing queued
    MOY_EVQ_RANGE = 7,          // the caller's buffer is too small; the event stays
};

#define MOY_EVQ_PROCS 24u           // queues at once
#define MOY_EVQ_DEPTH 16u           // events a queue holds
#define MOY_EVQ_NAME 24u            // a message's name, its NUL counted
#define MOY_EVQ_APP 48u             // an app id, its NUL counted
#define MOY_EVQ_LISTEN 8u           // names a process listens on at once
#define MOY_EVQ_SYSTEM "system"     // the app a message from pid 0 or 1 is from

#define MOY_EVQ_VM 0x01u            // the VM's task serves it (a Python process)

// Process `pid`'s queue opened (or, open already, its app and receives
// replaced): `app` its app id, `receives` the names its manifest lists, one
// per line (NULL for none). OK, FULL, NOMEM.
int moy_evq_open(uint32_t pid, const char *app, const char *receives, uint32_t flags);
// Its queue closed: its events freed, its waiter woken. OK, STALE.
int moy_evq_close(uint32_t pid);
// Whether `pid` has a queue.
bool moy_evq_has(uint32_t pid);
// The app id `pid`'s queue was opened with, MOY_EVQ_SYSTEM for pid 0 and 1,
// NULL for none.
const char *moy_evq_app(uint32_t pid);
// `pid` listens on `name` (counted: each `on` one, each cancel one less), or
// stops. OK, STALE, FULL where it listens on MOY_EVQ_LISTEN names already.
int moy_evq_listen(uint32_t pid, const char *name, bool on);

// Message `name` with `args` (a JSON array, n bytes) from process `from` to
// process `to`, under the rule. OK, STALE (either has no queue; pid 0 and 1
// need none to send), DEAF, DENIED, NOMEM.
int moy_evq_post(uint32_t to, uint32_t from, const char *name, const char *args, size_t n);
// Message `name` from process `from` to every process of app `app` that
// listens on it, the sender apart, under the rule: how many took it (0 or
// more), or -MOY_EVQ_STALE where the sender has no queue. *denied (may be
// NULL) counts the listeners the rule refused.
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

// `pid`'s oldest event taken: its record in *ev, its arguments into `args`
// (cap bytes, NUL-terminated where they fit). OK, STALE, EMPTY, RANGE (ev->n
// says what it needs; the event stays queued).
int moy_evq_take(uint32_t pid, moy_evq_ev_t *ev, char *args, size_t cap);
// The events queued on `pid`'s queue (0 for none).
uint32_t moy_evq_queued(uint32_t pid);
// A MOY_EVQ_VM queue with an event queued whose process the supervisor has
// not suspended (moy_super.h), taken in turn so none starves: its pid, 0 for
// none.
uint32_t moy_evq_ready(void);

// Block the calling task until `pid`'s queue has an event, it is kicked, or
// `ms` passes (0: look and return). How many events are queued then. A tier
// with no tasks never blocks.
uint32_t moy_evq_wait(uint32_t pid, uint32_t ms);
// `pid`'s waiter woken with no event (a stop, an answer, a resume).
void moy_evq_kick(uint32_t pid);

// Every queue closed (a test's teardown).
void moy_evq_reset(void);

#endif // MOY_EVQ_H
