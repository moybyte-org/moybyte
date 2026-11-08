// moy_wasi: WASI preview1 for a compiled command (docs/os_2026-11/tiers.md
// section 3): the imports a module built by wasi-sdk, Rust's
// `wasm32-wasip1` or Zig makes from `wasi_snapshot_preview1`, plus
// `moybyte.term`'s `term_size`, the one thing WASI lacks. A command is its
// module's `_start`, run in a shell session's process (moy_sh.h's spawn).
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
// consent grants (`files`: the user's files folder; `files:<kind>`: that
// kind's), named relative to the store and handed in as `roots`. Descriptor 3
// is the preopen ".", the session's working folder, so a relative path is the
// shell's; each root follows under its absolute shell path, so an absolute
// one under it resolves too. A path that leaves every root answers
// NOTCAPABLE, whichever preopen it came through.
//
// WHERE IT RUNS. A native is called on the thread running the module: a
// board's session thread (moy_wasm_session.h), the host's caller. Its body
// touches the session, so it runs where the session may be touched: through
// `hop` (a board's moy_wasm_on_vm, the MicroPython task the door's crossing
// runs on), in place where `hop` is NULL. Every call checks the session's
// Ctrl-C and the run's budget (at most every MOY_WASI_POLL_US), and either
// ends the run as proc_exit does: 130, or 137 with a line on stderr. A loop
// calling no import is not stopped (moy_wasm/README.md, "Stopping a run").
//
// IN THE BACKGROUND (`&`), the program's stdout and stderr are the OS log's
// lines under its pid, as a Python command's are (runtime/shell/sh_door.py),
// and its stdin is empty.

#ifndef MOY_WASI_H
#define MOY_WASI_H

#include <stddef.h>
#include <stdint.h>

#include "wasm_export.h"
#include "moy_sh.h"

#define MOY_WASI_MODULE "wasi_snapshot_preview1"
#define MOY_WASI_TERM "moybyte.term"

#define MOY_WASI_FDS 32                 // a command's descriptors, stdio included
#define MOY_WASI_POLL_US 10000          // Ctrl-C and the budget, at most this often
#define MOY_WASI_STACK (8 * 1024)       // the exec env's wasm stack, every tier's

#define MOY_WASI_TRAPPED (-1)           // moy_wasi_main: the module trapped
#define MOY_WASI_NOT_COMMAND (-2)       // moy_wasi_main: no `_start`
#define MOY_WASI_INTERRUPTED 130
#define MOY_WASI_KILLED 137

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

// The natives (WAMR's NativeSymbol rows) for WASI's module and for
// moybyte.term, copied into `store` (moy_wasi_natives' count of each), which
// WAMR sorts in place and keeps while they are registered: 0, or -1.
uint32_t moy_wasi_count(uint32_t *term);
int moy_wasi_register(NativeSymbol *store);
void moy_wasi_unregister(NativeSymbol *store);

// Whether a loaded module is a command this console runs: every import a
// function of WASI's module or moybyte.term that the natives serve, and a
// `_start`. 0, or non-zero with the first reason in `err`.
int moy_wasi_check(wasm_module_t module, char *err, size_t errlen);

// Run the command's `_start` on `env` (the thread that loaded it): its exit
// status (proc_exit's, 0 when `_start` returns, 130 or 137 when it was
// stopped), or MOY_WASI_TRAPPED with the trap in `err`.
int moy_wasi_main(moy_wasi_t *w, wasm_exec_env_t env, char *err, size_t errlen);

#endif // MOY_WASI_H
