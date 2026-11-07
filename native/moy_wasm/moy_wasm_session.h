// A cart's SESSION on the engine (docs/history/wasm_tier_plan_2026-09.md, phase 3):
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
// SEVERAL SESSIONS. The boards' engine holds a session per compiled run, as
// many as memory allows, never beside a moy_wasm.start() run: one runtime
// serves them all, each session's load and instance in its own pool
// (modmoy_wasm.c's allocator). Every call below acts on the CURRENT session:
// open makes its new session current, moy_wasm_session_use makes another
// one current, and close leaves none. The browser's engine holds one. A
// cart's par items also run on the session's lanes, threads on the other
// cores that the host half hands libmoy's binding (moy_wasm_session_lane_go
// below).

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
// interpreter tier (native/moy_wasm/README.md, "A cart survives its
// firmware", 2026-09-30): no moybyte.key section, no signature and no
// Unknown sources check -- WAMR's bytecode validation is the sandbox, not
// provenance -- and moy_wasm_session_lanes() reports 0 lanes for the
// session's life, so a cart's par items run in declaration order instead of
// across cores (the same fallback a board with MOY_WASM_ITEM_LANES 0 takes).
// The browser's engine runs only main.wasm, so every open there is this one.
int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, int interp, const moy_wasm_ops *ops,
                          char *err, size_t errlen);

// The current session, an opaque handle (NULL: none), and making one current
// again: the host half's run keeps the handle its open made current.
void *moy_wasm_session_current(void);
void moy_wasm_session_use(void *session);

// Run ops->call(what, dt) on the session's thread and wait for it. 0, or
// ops->call's non-zero return with its `err`.
int moy_wasm_session_call(int what, float dt, char *err, size_t errlen);

// Tear the session down: ops->unbound, then the instance, the module and,
// with the last session, the runtime, and join the thread. Safe to call on
// no session.
void moy_wasm_session_close(void);

// Whether the current session is open.
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

// TIMED LANES (docs/os_2026-11/hardware.md section 2), a board's that sets
// MOY_WASM_TIMED_LANES (the P4s; the S3s' wait on their core-0 spike): the OS
// calling a cart's own exports on a high-priority thread on the core the
// session is not on, AOT or interpreted, each on a sibling instance of the
// cart over its memory (wasm_runtime_instantiate_sibling, as par's lanes):
//
//   i32 moy_audio(i32 buf, i32 n)   render up to `n` frames of signed 16-bit
//                                   mono at 22,050 Hz into `buf`; how many.
//                                   Called while the render-ahead ring has
//                                   room (moy_audio_snd.h, the manifest's
//                                   "audio_ring" deep), n at most
//                                   MOY_WASM_TLANE_CHUNK; what it renders
//                                   goes into the ring, never inside the
//                                   feeder
//   i32 moy_timer(i32 t_us)         called at the deadline it last asked for,
//                                   `t_us` the wrapping microsecond clock
//                                   (esp_timer: 1 us resolution, dispatched
//                                   tens of microseconds late, no sooner than
//                                   MOY_WASM_TLANE_TIMER_MIN); the
//                                   microseconds to the next call, 0 or less
//                                   for none. Called once as the lanes start
//   i32 moy_lane_stack(i32 lane)    the top of lane `lane`'s own stack in the
//                                   cart's memory (0 audio, 1 timer), called
//                                   once on the session's thread; the lane's
//                                   __stack_pointer (SPEC.md 16.10's export)
//                                   starts there, below the audio lane's
//                                   `buf`, which is the stack's top
//                                   2 x n bytes
//
// A lane may call no import: one traps the lane (libmoy's binding refuses an
// unbound instance). THE DEADLINE MONITOR, an esp_timer above every lane,
// strikes a lane whose call has run past MOY_WASM_TLANE_DEADLINE_US: it
// suspends the lane's task (AOT code reads a termination only at an import
// return, so a spinning lane would otherwise hold its core), mutes the ring
// and strikes the lane for the session's life; a trap strikes it too. Each
// lane's task stack is MOY_WASM_STACK_BYTES taken from THE SHARES' LANES row.
#define MOY_WASM_TLANE_AUDIO 0
#define MOY_WASM_TLANE_TIMER 1
#define MOY_WASM_TLANES 2
#define MOY_WASM_TLANE_CHUNK 128
#define MOY_WASM_TLANE_DEADLINE_US 10000
#define MOY_WASM_TLANE_TIMER_MIN 100

// The ring the audio lane fills: n frames queued, how many it took (n 0: the
// room); and what mutes it once a lane is struck.
typedef struct {
    uint32_t (*write)(const uint8_t *pcm, uint32_t n);
    void (*mute)(void);
} moy_wasm_tlane_sink_t;

// From the session's thread, once _init has returned: start a lane for each
// of moy_audio and moy_timer the module exports (with moy_lane_stack and
// __stack_pointer). The lanes started as a mask of 1 << MOY_WASM_TLANE_*, 0
// for none (or a board without timed lanes), or -1 with `err` set (the LANES
// row, the pool or a stack refused them; none runs).
int moy_wasm_tlanes_start(moy_wasm_env env, const moy_wasm_tlane_sink_t *sink,
                          char *err, size_t errlen);
// From the session's thread, before the runtime goes: every lane stopped,
// a struck one's task deleted, and the LANES row given back.
void moy_wasm_tlanes_stop(void);

// What a session's lanes did, kept past its end: per lane, whether it ran,
// its calls, frames rendered, the longest call in microseconds, and whether
// and why it was struck.
typedef struct {
    uint8_t ran, struck;
    uint32_t calls, frames, max_us;
    char why[48];
} moy_wasm_tlane_stats_t;
// The lanes' stats into `out` (MOY_WASM_TLANES of them): how many this board
// runs (0 without timed lanes).
int moy_wasm_tlanes_stats(moy_wasm_tlane_stats_t *out);

#endif
