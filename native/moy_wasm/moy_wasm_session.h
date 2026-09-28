// A cart's SESSION on the engine (docs/wasm_tier_plan_2026-09.md, phase 3):
// the C surface moycore -- the host half, which owns the console and the
// import table -- drives a compiled cart through. The engine owns what it
// owned in phase 1 and nothing more: the runtime and its pool, the module
// file, the load, the provenance key, the instance, and the thread every one
// of those runs on.
//
// Everything WAMR does happens on the session's thread, so the host half's
// part is a table of callbacks the engine calls THERE, at five points: once the
// runtime is up (register the import table), once the module is loaded and its
// key checked (check its shape), once it is instantiated (bind it to the
// console), per hook call, and before teardown (unbind). The MicroPython task
// blocks in moy_wasm_session_open/call/close while the thread works -- and
// while it waits it serves moy_wasm_on_vm requests, which is how an import that
// needs the VM (a file read through the VFS, a config lookup in a dict) gets it
// without the thread ever touching MicroPython.
//
// One session at a time, and never beside a moy_wasm.start() run.

#ifndef MOY_WASM_SESSION_H
#define MOY_WASM_SESSION_H

#include <stddef.h>
#include <stdint.h>

#include "wasm_export.h"

typedef struct {
    void *user;
    // After the runtime is up, before the load. 0, or non-zero with `err` set.
    int (*runtime_up)(void *user, char *err, size_t errlen);
    // After the load and the key check, before instantiation.
    int (*loaded)(void *user, wasm_module_t module, char *err, size_t errlen);
    // After instantiation, with the exec env the calls will run on.
    int (*bound)(void *user, wasm_exec_env_t env, char *err, size_t errlen);
    // One call into the cart: `what` and `dt` are the host half's own.
    int (*call)(void *user, int what, float dt, char *err, size_t errlen);
    // Before teardown, whether or not the session got as far as `bound`.
    void (*unbound)(void *user);
} moy_wasm_ops;

// Open a session on the module file `path` (read through the VFS into
// PSRAM and its signature checked, as moy_wasm.start reads its module; a
// tampered module, or an unsigned one while `allow_unsigned` is 0, is refused
// before the thread exists). `allow_unsigned` is the owner's Unknown sources
// setting as the caller read it: non-zero lets a module with no signature
// load, its provenance key still checked.
// `want_sha` is the canonical .wasm's sha256 as 64 hex characters, which the
// module's key must name, or NULL to take whatever wasm the key names.
// `memory` is the linear memory the manifest declares, in bytes: the file is
// read into a block that size, which the linear memory takes back once the
// load is done. Returns 0 with the cart bound, or non-zero with the refusal
// in `err`; a file that cannot be read raises.
int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, const moy_wasm_ops *ops, char *err,
                          size_t errlen);

// Run ops->call(what, dt) on the session's thread and wait for it. 0, or
// ops->call's non-zero return with its `err`.
int moy_wasm_session_call(int what, float dt, char *err, size_t errlen);

// Tear the session down: ops->unbound, then the instance, the module and the
// runtime, and join the thread. Safe to call on no session.
void moy_wasm_session_close(void);

// Whether a session is open.
int moy_wasm_session_live(void);

// From the session's thread: run fn(arg) on the MicroPython task, which is
// blocked in a session call, and wait for it. fn runs with the VM available
// and must catch its own exceptions. Returns 0, or -1 when there is no task
// waiting to serve it (the call is then not made).
int moy_wasm_on_vm(void (*fn)(void *arg), void *arg);

#endif
