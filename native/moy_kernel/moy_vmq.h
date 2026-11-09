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
// MOY_VMQ_SLOTS calls and hops wait at once, every process's together; a
// process's end drops what it left. Any task may post; serve, next and answer
// are the VM's task's.

#ifndef MOY_VMQ_H
#define MOY_VMQ_H

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
// which a supervisor does not charge to the process.
int moy_vmq_hop(uint32_t pid, void (*fn)(void *), void *arg, uint32_t *waited_us);
// The same, wake(ctx) called once the hop is queued (NULL: none): a VM-task
// wait that serves the poster's hops (an ELF game's frame call) woken for it
// at once.
int moy_vmq_hop_wake(uint32_t pid, void (*fn)(void *), void *arg, uint32_t *waited_us,
                     void (*wake)(void *), void *ctx);

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

// Every slot freed (a test's teardown).
void moy_vmq_reset(void);

#endif // MOY_VMQ_H
