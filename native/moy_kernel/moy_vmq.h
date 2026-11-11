// moy_vmq: the one queue of calls into the VM's task (docs/os_2026-11/
// processes.md section 1, "The service model"). A process on a task of its
// own (a Lua, wasm or ELF service on a board) that needs the console -- a
// notification, an intent, a role row of its app's run -- does not enter
// Python: it posts here, and the VM's task serves the queue at the
// scheduler's poll.
//
// A HOP is C to run on the VM's task: `fn(arg)` (a role row), run in place
// at the next moy_vmq_serve while the posting task waits for it. Only a task
// that is not the VM's may hop; on the VM's task (a stepped tier) a caller
// runs fn itself.
//
// A CALL is a console call by name with its arguments as a JSON array
// ("moybyte.notify"), answered by the console in Python with a JSON value or
// refused (none). It does not block: the poster gets a ticket and reads the
// answer when its process is woken (moy_vmq_answer kicks the poster's event
// queue, moy_evq.h), so a stepped service yields its call and resumes it.
//
// A post or hop wants the VM (moy_evq.h, THE VM'S WAKE), which serves the
// queue at the scheduler's poll that want runs.
//
// WITH NO VM (an S3's VM stop: the kernel's frame runs a compiled game, and
// the services on tasks of their own run on through it, docs/os_2026-11/
// processes.md section 5). The kernel says the VM is down (moy_vmq_vm) and
// its frame serves the hops while it is (a loop job, moy_loop.h): what is C
// runs as with the VM up. A hop that needs Python -- a role row the door
// serves, which answers MOY_APP_NEEDS_VM with no VM -- says so from inside
// its fn (moy_vmq_defer) and is run again once the VM is back, its poster
// waiting until then; a call waits for its answer the same way, since only
// the VM's console answers one. Nothing is refused for the VM being down.
//
// MOY_VMQ_SLOTS calls and hops wait at once, every process's together; a
// process's end drops what it left. Any task may post; serve, next and answer
// are the VM's task's, and the kernel's frame's while no VM runs.

#ifndef MOY_VMQ_H
#define MOY_VMQ_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    MOY_VMQ_OK = 0,
    MOY_VMQ_FULL = 1,           // every slot waits already
    MOY_VMQ_NOMEM = 2,
    MOY_VMQ_STALE = 3,          // no such ticket
    MOY_VMQ_EMPTY = 4,          // no call waits
    MOY_VMQ_WAITING = 5,        // posted, not answered yet
};

#define MOY_VMQ_SLOTS 8u
#define MOY_VMQ_NAME 24u            // a call's name, its NUL counted

// fn(arg) run on the VM's task for process `pid`, the calling task waiting
// until it has run: OK, FULL. *waited_us (may be NULL) is how long it waited,
// which a supervisor does not charge to the process. Queued, it kicks the
// screen slot of `pid` (moy_screen_slot_kick), whose frame call serves it.
int moy_vmq_hop(uint32_t pid, void (*fn)(void *), void *arg, uint32_t *waited_us);

// Console call `name` with `args` (a JSON array, n bytes) posted for process
// `pid`: OK with *ticket, FULL, NOMEM.
int moy_vmq_post(uint32_t pid, const char *name, const char *args, size_t n, uint32_t *ticket);
// Its answer: OK with *ans (NULL where the console refused it; the caller's
// to free) and *n, the ticket spent; WAITING; STALE.
int moy_vmq_result(uint32_t ticket, char **ans, size_t *n);
// What process `pid` left waiting, dropped (its end).
void moy_vmq_drop(uint32_t pid);

// The VM's task: the hops posted run, in order. How many calls wait for an
// answer.
uint32_t moy_vmq_serve(void);
// The oldest unanswered call: OK with its ticket, its process, its name and
// its arguments (n bytes), which stay the slot's until it is answered; EMPTY.
int moy_vmq_next(uint32_t *ticket, uint32_t *pid, const char **name, const char **args, size_t *n);
// The console's answer to a call: a JSON value of `n` bytes, or NULL where it
// refused. OK, STALE, NOMEM.
int moy_vmq_answer(uint32_t ticket, const char *ans, size_t n);

// The VM is up (true, at boot and when it comes back) or down (a VM stop):
// down, the kernel's frame serves the hops as a loop job and a deferred one
// waits. Whether it is up.
void moy_vmq_vm(bool up);
bool moy_vmq_vm_up(void);
// Inside a hop's fn while moy_vmq_serve runs it: while no VM runs, the hop is
// run again once one does, its poster waiting (true); with the VM up, false
// and the hop is done.
bool moy_vmq_defer(void);

// Every slot freed (a test's teardown).
void moy_vmq_reset(void);

#endif // MOY_VMQ_H
