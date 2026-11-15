// moycore_moybyte: one process's `moybyte` table in a Lua state, the one
// table a Lua service (moycore_svc.c) and a Lua app's run (moycore_lua.c)
// both hold: LUA_OWN's names (native/os_calls.json: send, on, every, after,
// at, cancel, log), the console calls (MOY_APP_LUA_CALLS, the same file's)
// and ERR, WARNING and INFO. A service adds `exit`; an app run's role
// binding (native/moy_app/moy_app_lua.h) opens its rows into the same table.
//
// THE WAKES are the kernel's (native/moy_kernel/moy_evq.h): every, after and
// at are timers of the process's event queue (moy_evq_timer, moy_evq_at, the
// latter under the `alarms` the queue was opened with), on is a listen on it,
// and every wake's id is the kernel's one counter's (moy_evq_id), so cancel
// takes any of them. Their callbacks live in the state's registry by id. The
// queue is never the VM's, so each timer posts MOY_EVQ_TIMER or MOY_EVQ_AT
// with its id as it goes off, and a message the queue hands reaches its
// handlers through moycore_mb_next: a timer's or an alarm's the callback of
// the id it carries, any other name each `on` wake on it in the order they
// were made, called with the sender's app id and the message's JSON array
// decoded, a null as nil.
//
// A CONSOLE CALL is the holder's: `console` runs with the call's name
// ("moybyte.notify") as its upvalue and the call's arguments. A service's
// yields its coroutine to the VM's task (moycore_svc.h, CONSOLE CALLS); an
// app run's calls the console's Python in place (moycore_mb_call).
//
// send is the kernel's (moy_evq_send) from the process: how many took it, or
// nil and "full" (BACKPRESSURE) or "no queue". log writes the OS log under
// the process's pid.

#ifndef MOYCORE_MOYBYTE_H
#define MOYCORE_MOYBYTE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "lua.h"
#include "moy_evq.h"

#define MOYCORE_MB_ONS 8u           // message wakes a process holds (MOY_EVQ_LISTEN)

// A process's block: its holder's (a service's row, an app run's), named in
// the state's registry while the state lives.
typedef struct moycore_mb {
    uint32_t pid;
    int fns;                    // the registry's table: a wake's id -> its callback
    int once;                   // ...: a once timer's or an alarm's id -> true
    struct {
        uint32_t id;            // 0: a free wake
        char name[MOY_EVQ_NAME];
    } on[MOYCORE_MB_ONS];
} moycore_mb_t;

// The global `moybyte` (the one the state holds, else a new one) given the
// names above for process `pid`, over block `mb`.
void moycore_mb_open(lua_State *L, moycore_mb_t *mb, uint32_t pid, lua_CFunction console);
// The state's block, NULL for none.
moycore_mb_t *moycore_mb_of(lua_State *L);
uint32_t moycore_mb_pid(const moycore_mb_t *mb);
// Whether it holds a wake: a timer of the kernel's or an `on`.
bool moycore_mb_wakes(const moycore_mb_t *mb);
// How many wakes it holds.
uint32_t moycore_mb_count(const moycore_mb_t *mb);
// The next handler of message `ev` (its arguments `msg`, n bytes of JSON
// array) from handler `*at` on (0 for the first), pushed with its arguments
// and `*at` moved past it: how many arguments, -1 for none left. A once
// timer's or an alarm's callback is spent as it is pushed.
int moycore_mb_next(lua_State *L, moycore_mb_t *mb, const moy_evq_ev_t *ev, const char *msg,
                    size_t n, uint32_t *at);
// Its wakes ended: its listens given up and its timers cancelled, the state
// about to close (it allocates nothing).
void moycore_mb_end(lua_State *L, moycore_mb_t *mb);
// Every message waiting on its queue handed to its handlers in place, each
// called protected (an app run's, at its frame's start): how many ran, or
// -1 with the first error's text in `err` (the messages after it wait).
int moycore_mb_serve(lua_State *L, moycore_mb_t *mb, char *err, size_t n);
// The state's global `fn` called protected with the values of `json` (n
// bytes, a JSON array), each in its place, a null as nil: 0, or -1 with the
// error's text in `err` (an app run's window events, moycore_lua_call).
int moycore_mb_global(lua_State *L, const char *fn, const char *json, size_t n, char *err,
                      size_t errn);
// An app run's console call: its arguments as one JSON array, handed to the
// function at the registry's "moy.call" (the console's, which the run's
// binding registers as `__moybyte_call(verb, args)`), whose answer it
// returns. The `console` an app run opens with.
int moycore_mb_call(lua_State *L);

#endif // MOYCORE_MOYBYTE_H
