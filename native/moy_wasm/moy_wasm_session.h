// A cart's SESSION on the engine (docs/wasm_tier_plan_2026-09.md, phase 3):
// the C surface moycore -- the host half, which owns the console and the
// import table -- drives a compiled cart through. Two engines implement it:
// the boards' (modmoy_wasm.c beside this file, over WAMR) and the browser's
// (native/moy_wasm_web, the page's own WebAssembly engine, under
// MOY_WASM_JS). The engine owns the module file, the load, the instance and
// where they run; the host half binds and calls.
//
// The boards' engine owns what it owned in phase 1 and nothing more: the
// runtime and its pool, the module file, the load, the provenance key, the
// instance, and the thread every one of those runs on. Everything WAMR does
// happens on the session's thread, so the host half's part is a table of
// callbacks the engine calls THERE, at five points: once the runtime is up
// (register the import table), once the module is loaded and its key checked
// (check its shape), once it is instantiated (bind it to the console), per
// hook call, and before teardown (unbind). The MicroPython task blocks in
// moy_wasm_session_open/call/close while the thread works -- and while it
// waits it serves moy_wasm_on_vm requests, which is how an import that needs
// the VM's task (a cart's file op, C over the volume the VM's mount table
// resolves, on a stack an internal-flash write may run on) gets it without
// the thread ever touching MicroPython.
//
// The browser's engine calls the same five on the VM's own thread, which is
// the page's one thread: moy_wasm_on_vm runs its request at once, and there
// are no lanes (a cart's par items run in order, through the binding).
//
// One session at a time, and never beside a moy_wasm.start() run. A cart's
// par items also run on the session's lanes, threads on the other cores that
// the host half hands libmoy's binding (moy_wasm_session_lane_go below).

#ifndef MOY_WASM_SESSION_H
#define MOY_WASM_SESSION_H

#include <stddef.h>
#include <stdint.h>

#ifdef MOY_WASM_JS
// The browser's engine: the loaded module is the file's bytes, which the
// host half checks with moy_wasm_check_bytes; and the instance is the page's,
// so what `bound` is handed is where to name the binding (the moy_wasm) the
// page's adapters call the import table with.
typedef struct {
    const uint8_t *bytes;
    size_t size;
} moy_wasm_web_module;
typedef struct {
    void *binding;
} moy_wasm_web_env;
typedef const moy_wasm_web_module *moy_wasm_module;
typedef moy_wasm_web_env *moy_wasm_env;
#else
#include "wasm_export.h"
typedef wasm_module_t moy_wasm_module;
typedef wasm_exec_env_t moy_wasm_env;
#endif

typedef struct {
    void *user;
    // After the runtime is up, before the load. 0, or non-zero with `err` set.
    int (*runtime_up)(void *user, char *err, size_t errlen);
    // After the load and the key check, before instantiation.
    int (*loaded)(void *user, moy_wasm_module module, char *err, size_t errlen);
    // After instantiation, with the exec env the calls will run on.
    int (*bound)(void *user, moy_wasm_env env, char *err, size_t errlen);
    // One call into the cart: `what` and `dt` are the host half's own.
    int (*call)(void *user, int what, float dt, char *err, size_t errlen);
    // Before teardown, whether or not the session got as far as `bound`.
    void (*unbound)(void *user);
} moy_wasm_ops;

// Open a session on the module file `path` (read through the VFS into
// PSRAM; a tampered AOT module, or an unsigned one while `allow_unsigned` is
// 0, is refused before the thread exists). `allow_unsigned` is the owner's
// Unknown sources setting as the caller read it: non-zero lets an AOT module
// with no signature load, its provenance key still checked.
// `want_sha` is the canonical .wasm's sha256 as 64 hex characters, which an
// AOT module's key must name, or NULL to take whatever wasm the key names.
// `memory` is the linear memory the manifest declares, in bytes: the file is
// read into a block that size, which the linear memory takes back once the
// load is done. Returns 0 with the cart bound, or non-zero with the refusal
// in `err`; a file that cannot be read raises.
//
// `interp` is non-zero when `path` is a cart's own main.wasm, run on the
// interpreter tier (docs/wasm_tier_plan_2026-09.md, "A cart survives its
// firmware", 2026-09-30): no moybyte.key section, no signature and no
// Unknown sources check -- WAMR's bytecode validation is the sandbox, not
// provenance -- and moy_wasm_session_lanes() reports 0 lanes for the
// session's life, so a cart's par items run in declaration order instead of
// across cores (the same fallback a board with MOY_WASM_ITEM_LANES 0 takes).
// The browser's engine runs only main.wasm, so every open there is this one.
int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, int interp, const moy_wasm_ops *ops,
                          char *err, size_t errlen);

// Run ops->call(what, dt) on the session's thread and wait for it. 0, or
// ops->call's non-zero return with its `err`.
int moy_wasm_session_call(int what, float dt, char *err, size_t errlen);

// Tear the session down: ops->unbound, then the instance, the module and the
// runtime, and join the thread. Safe to call on no session.
void moy_wasm_session_close(void);

// Whether a session is open.
int moy_wasm_session_live(void);

// From any task, while a session call runs: raise "terminated by user" in
// the session's instance, which AOT code reads when an import call returns
// (README.md's "A runaway export cannot be stopped mid-loop"). The runaway
// watch's compiled half (native/moy_play/moy_play.h). Safe with no session.
void moy_wasm_session_terminate(void);

// From the session's thread: run fn(arg) on the MicroPython task, which is
// blocked in a session call, and wait for it. fn runs with the VM available
// and must catch its own exceptions. Returns 0, or -1 when there is no task
// waiting to serve it (the call is then not made).
int moy_wasm_on_vm(void (*fn)(void *arg), void *arg);

#ifdef MOY_WASM_JS
// From ops->call, the browser's engine: run the cart's hook export `hook`
// (MOY_WASM_INIT, _UPDATE with `dt`, _DRAW) on the page's instance. 0 when it
// returned; 1 when it threw, with what it threw in `err`. Over WAMR the
// binding calls the export itself (moy_wasm_init/update/draw); a JavaScript
// engine's binding brackets the call instead (moy_wasm_begin/end), and this
// is the call.
int moy_wasm_session_export(int hook, float dt, char *err, size_t errlen);
#endif

// The wasm stack of an exec env the session's instances run on.
#define MOY_WASM_EXEC_STACK (8 * 1024)

// The session's LANES: threads on the cores the session's thread is not on,
// where a cart's par items run beside the calling core
// (libmoy/moy_wasm.h's `lanes`). How many this board gives: 0 on a board
// with one core, or one that declines.
int moy_wasm_session_lanes(void);

// From the session's thread: run fn(arg) on lane `lane` (1..lanes) and return
// at once, starting the lane's thread the first time. 0, or non-zero when the
// lane could not be started. Every call for a lane runs on the same thread,
// which the session joins before it tears the runtime down.
int moy_wasm_session_lane_go(int lane, void (*fn)(void *arg), void *arg);

// Wait for the fn lane `lane` was last given to return.
void moy_wasm_session_lane_wait(int lane);

#endif
