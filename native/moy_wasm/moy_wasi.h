// moy_wasi: WASI preview1 for a compiled command (docs/os_2026-11/tiers.md
// section 3): the imports a module built by wasi-sdk, Rust's
// `wasm32-wasip1` or Zig makes from `wasi_snapshot_preview1`, plus what WASI
// lacks: `moybyte.term`'s `term_size`, and `moybyte.os`'s `send`, `on`,
// `log`, `notify`, `award` and `progress`, the rows of native/os_calls.json
// a WASI program shares with an ELF module (moy_wasi_os_rows.h, generated;
// sdk/include/moybyte/os.h declares them under their C names, moy_send and
// the rest; the console calls are moy_wasi.c's THE CONSOLE CALLS). A command is its module's
// `_start`, run in a shell session's process (moy_sh.h's spawn).
//
// THE EVENT DESCRIPTOR. The run's process is its background pid, else the
// OS log's current one when the run was made (the shell door's process for
// it). Descriptor MOY_EVQ_FD reads that process's event queue (moy_evq.h,
// THE EVENT DESCRIPTOR): fd_read takes one record into its first buffer
// that is not empty (MSGSIZE where it is short, AGAIN while nothing is
// queued), and poll_oneoff reports it readable while an event is queued.
// A poll_oneoff that waits on it blocks on the module's thread in the one
// kernel wait (moy_evq_wait), until an event, the run's end or its nearest
// clock. Each wake, from a poll of the descriptor's return to the next
// poll of it, is charged to the one supervisor (moy_super.h), less what its
// calls waited for the session's task; while the supervisor holds the
// process suspended, its poll waits unserved. send, on and log are the
// kernel's calls on the module's thread, answering a number or a WASI errno
// below 0.
//
// ADAPTERS, NOT A SECOND OS. Each import keeps WASI's name and calls the C a
// shell built-in calls: stdio is the session's descriptors 0, 1 and 2 (the
// terminal, a pipe's buffer, a redirected file), a file is a descriptor of
// the session's files tree (moy_sh.h's "a spawned program's files"), args are
// the spawn's argv, env the session's exported variables, the clocks the
// kernel's, random the board's generator. WAMR's own libc-wasi stays out of
// the build (micropython.cmake: WASM_ENABLE_LIBC_WASI=0); these natives are
// registered under WASI's module name like any other table.
//
// FILES UNDER THE APP'S PERMISSIONS. A command reaches the folders its
// consent grants (`files`: the user's files folder; `files:<folder>`: that
// shared folder, runtime/shell/sh_door.py's `wasi_roots`), named relative to the store and handed in as `roots`. Descriptor 3
// is the preopen ".", the session's working folder, so a relative path is the
// shell's; each root follows under its absolute shell path, so an absolute
// one under it resolves too. A path that leaves every root answers
// NOTCAPABLE, whichever preopen it came through.
//
// WHERE IT RUNS. A native is called on the thread running the module: a
// board's session thread (moy_wasm_session.h), the host's job thread. Its
// body touches the session, so it runs where the session may be touched:
// through `hop` (a board's moy_wasm_on_vm, the MicroPython task the door's
// crossing runs on), in place where `hop` is NULL. A clock's wait
// (poll_oneoff, so sleep) waits on the module's thread, in slices. Every call
// checks the session's Ctrl-C and the run's budget (at most every
// MOY_WASI_POLL_US), and either ends the run as proc_exit does: 130, or 137
// with a line on stderr. A loop calling no import is not stopped
// (moy_wasm/README.md, "Stopping a run").
//
// IN STEPS. The task the session may be touched on serves the run a step at
// a time (moy_wasi_step, the shell door's job, runtime/shell/sh_door.py), so
// the console's frame goes on between the steps: each step serves the
// module's calls for a while (moy_sh_prog.h's moy_sh_prog_step, the native
// tier's loop too), and moy_wasi_stop ends the run as Ctrl-C does. The
// tier's runner is the other end of its `hop`.
//
// IN THE BACKGROUND (`&`), the program's stdout and stderr are the OS log's
// lines under its pid, as a Python command's are (runtime/shell/sh_door.py),
// and its stdin is empty.
//
// A SERVICE (docs/os_2026-11/processes.md section 1, the service model) is a
// run with no session, its `_start` on a task of its own (each tier's
// runner: runtime/host/moyhost_wasm.c, modmoy_wasm.c), a loop that blocks in
// poll_oneoff on its EVENT DESCRIPTOR with its nearest deadline as the
// timeout. Its argv is its cart's id and its env is empty; its stdout and
// stderr are the OS log's lines under its pid and its stdin is empty; it has
// no preopen, and a path answers NOTCAPABLE. Its calls run in place on its
// own thread (no `hop`): none touches a session. Its stop hangs the
// descriptor up (moy_wasi_hangup): poll_oneoff reports it ready with WASI's
// hangup flag and fd_read reads 0 bytes, its end of file, and `_start`
// returns within MOY_WASI_STOP_US or the run is killed (moy_wasi_kill).
//
// THE WATCHDOG (moy_wasi_watch, on the console's task, each tier's reap):
// a wake, from a poll of the descriptor's return to the next poll of it, is
// struck once for each MOY_SUPER_ABORT_US it runs (the strikes a callback's
// abort would have charged, moy_super.h), and its own poll then charges it
// nothing more. At the strike that suspends it, suspension cannot hold a
// wake that still runs, so the run is killed: its end is MOY_PROC_KILLED and
// its instance is terminated (wasm_runtime_terminate), which `_start` reads
// at its next import call, a wait's slice included; a loop that calls none
// runs on until it does. A service whose start runs MOY_WASI_STOP_US without
// a first poll is killed the same way.

#ifndef MOY_WASI_H
#define MOY_WASI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "wasm_export.h"
#include "moy_sh.h"

#define MOY_WASI_MODULE "wasi_snapshot_preview1"
#define MOY_WASI_TERM "moybyte.term"
#define MOY_WASI_OS "moybyte.os"

#define MOY_WASI_FDS 32                 // a command's descriptors, stdio included
#define MOY_WASI_POLL_US 10000          // Ctrl-C and the budget, at most this often
#define MOY_WASI_STACK (8 * 1024)       // the exec env's wasm stack, every tier's

#define MOY_WASI_STOP_US 2000000       // a service's start to its first poll; its stop to its end
#define MOY_WASI_TRAPPED (-1)           // moy_wasi_main: the module trapped
#define MOY_WASI_NOT_COMMAND (-2)       // moy_wasi_main: no `_start`

typedef struct moy_wasi moy_wasi_t;

// Run fn(arg) where the session may be touched, and wait for it: 0, or -1
// when it could not be.
typedef int (*moy_wasi_hop_fn)(void (*fn)(void *arg), void *arg);

// A command's run in session `s`: `argv` its arguments (argv[0] its name),
// `roots` the folders its permissions grant, NUL-separated and ending in an
// empty one, each relative to the console's store (NULL: none); `bg_pid` its
// pid when it runs in the background (0: the foreground); `budget_ms` how
// long it may run (0: no limit). The session's exported variables are read
// now, so call it where `s` may be touched. NULL when memory refuses.
moy_wasi_t *moy_wasi_new(moy_sh_t *s, int argc, const char *const *argv, const char *roots,
                         uint32_t bg_pid, uint32_t budget_ms, moy_wasi_hop_fn hop);
// Where the session may be touched: the descriptors the program left open
// closed, its last background line logged, the record freed.
void moy_wasi_free(moy_wasi_t *w);

// The natives (WAMR's NativeSymbol rows) for WASI's module, and for
// moybyte.term and moybyte.os together (`term`), copied into `store`
// (moy_wasi_count's counts), which WAMR sorts in place and keeps while they
// are registered: 0, or -1.
uint32_t moy_wasi_count(uint32_t *term);
int moy_wasi_register(NativeSymbol *store);
void moy_wasi_unregister(NativeSymbol *store);

// Whether a loaded module is a command this console runs: every import a
// function of WASI's module or moybyte.term that the natives serve, and a
// `_start`. 0, or non-zero with the first reason in `err`.
int moy_wasi_check(wasm_module_t module, char *err, size_t errlen);

// Run the command's `_start` on `env`: its exit status (proc_exit's, 0 when
// `_start` returns, 130 or 137 when it was stopped), or MOY_WASI_TRAPPED
// with the trap in `err`.
int moy_wasi_main(moy_wasi_t *w, wasm_exec_env_t env, char *err, size_t errlen);

// A tier's runner of a stepped run: whether `_start` has returned on the
// module's thread; one of its calls waited for (`us` at most) and served,
// the task's watchdog fed; and "terminated" raised in its instance.
typedef struct {
    bool (*done)(void *job);
    void (*serve)(void *job, int64_t us);
    void (*terminate)(void *job);
} moy_wasi_runner_t;

#define MOY_WASI_RUNNING (-100)         // moy_wasi_step: `_start` goes on

// Where the session may be touched, `_start` running on the module's thread
// under runner `r`: serve its calls for `max_us` at most. MOY_WASI_RUNNING
// while it goes on, else 0 with `_start` returned -- a run that Ctrl-C or
// its budget ended made to return first (its instance terminated, its calls
// served until it has) -- and moy_wasi_status its exit status.
int moy_wasi_step(moy_wasi_t *w, const moy_wasi_runner_t *r, void *job, uint32_t max_us);
// The run ended as Ctrl-C ends it (130, unless it had already exited), and
// `_start` made to return as moy_wasi_step makes it.
void moy_wasi_stop(moy_wasi_t *w, const moy_wasi_runner_t *r, void *job);
// Its exit status once `_start` has returned, moy_wasi_main having answered
// `main`: the exit's or the stop's where the run ended so, else `main`.
int moy_wasi_status(const moy_wasi_t *w, int main);

// A service's run as process `pid` (A SERVICE above), its argv `id`; the
// event queue is its scheduler's to open. NULL when memory refuses.
moy_wasi_t *moy_wasi_service(uint32_t pid, const char *id);
// Its stop, from any task: the event descriptor hangs up.
void moy_wasi_hangup(moy_wasi_t *w);
// Its run ended with `code` from any task, as proc_exit ends it, and its
// instance terminated while `_start` runs.
void moy_wasi_kill(moy_wasi_t *w, int code);
// The watchdog, on the console's task, at its reap: the wake's strikes
// charged; why the service is to be killed, or NULL.
const char *moy_wasi_watch(moy_wasi_t *w);
// Its exit status once `_start` has returned (moy_wasi_main's answer
// `main`, the trap in `err`): moy_wasi_status's, a trap's MOY_PROC_TRAPPED
// with its line logged under its pid, a run that did not start
// MOY_PROC_NOT_RUN.
int moy_wasi_service_status(const moy_wasi_t *w, int main, const char *err);
// Its linear memory's bytes while `_start` runs, and its wakes' time (us,
// wrapping).
void moy_wasi_figures(const moy_wasi_t *w, uint32_t *mapped, uint32_t *cost_us);

#endif // MOY_WASI_H
