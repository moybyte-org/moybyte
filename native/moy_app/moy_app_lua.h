// The app ABI's Lua face (docs/os_2026-11/tiers.md section 2): the role
// binding a Lua app's state holds, over the same table and the same ROLE door
// (moy_app_role) the wasm import adapter reaches (moy_app_wasm.h), and Lua's
// `io` and `os` over the files tree under the app's permissions.
//
// The run it serves is the state's own (moy_app_run.h: the state, the grant
// the Player made from the cart's permissions, the roles they name), copied
// into the Lua state's registry at its open: the Player makes a Lua user
// app's run as it makes a compiled app's, and the Lua run's open hands it
// here, so two states are two runs and neither reaches the other's grant. A
// role the permissions do not name has no name in the state (nil, never a
// stub), and with no run (a game) nothing here is opened. The run's open
// files are saved at the state's close, so its owner closes the state
// before its grant ends.
//
// The names, each answering as the Python face (runtime/shell/system_api.py)
// answers, Python's (value, err) as two results and its None as nil:
//
//   files (files, files:<folder>)  files.ready() / list() / load(name) /
//                                save(name, blob) / delete(name) /
//                                rename(name, new) / duplicate(name) /
//                                new_name([title]) / save_text(name, text) /
//                                load_text(name), one kind: the grant's
//   prefs (prefs)                prefs.get(key[, default]) / set(key, value) /
//                                clear(key), each value JSON in the settings row
//   set_theme (appearance)       set_theme(name[, variant])
//   open_app (launch)            open_app(id)
//   the device handles           moybyte.dev_open(name[, cfg]) / dev_close(h) /
//   (lease, http, lora, ...)     dev_read(h[, cap]) / dev_write(h, bytes) /
//                                dev_xfer(h, bytes[, cap]) / dev_set(h, cfg) /
//                                dev_get(h, what), the kernel's table
//                                (moy_dev.h) under the grant's device
//                                permissions: the answer, or nil and the
//                                code's name; a read's bytes as a string
//   the status item (every run)  moybyte.status_show(icon[, state[, order]]) /
//                                status_state(item, state) /
//                                status_remove(item), the kernel's table
//                                (moy_status.h) as the run's process: the
//                                row's answer as a compiled program has it,
//                                the item's handle or 0, a refusal its
//                                negative code; its panel is the console
//                                call moybyte.status_panel(item[, rows])
//
// and with files, `io` (open, lines, close, type; a file's read, write, lines,
// seek, flush and close) and `os.remove` / `os.rename`: a path is a NAME of
// the grant's kind, its folder the process's working directory; a path that
// names a folder, climbs, or is absolute is refused (EACCES), never resolved.
// A file opened for writing is saved whole through the files role at its
// flush or close. `os.time`, `os.clock` and `os.difftime` are every app
// run's. `files.badge` and `themes()` are the Python face's own tables, which
// the Lua run's prelude hands over (runtime/run/lua_ext.py).
//
// Its caller is the VM's task: a Lua run's frame runs there, so a row is
// called in place, a shell row crossing to the Python console through the
// door (moy_app_door_bind). A state on a task of its own (a Lua service on a
// P4's core 0, native/moycore/moycore_svc.h) opens with a HOP, which runs
// what must run on the VM's task there and waits for it: every row it calls,
// and the open's own reads of the run.

#ifndef MOY_APP_LUA_H
#define MOY_APP_LUA_H

#include "lua.h"
#include "moy_app_run.h"

// Open run `run`'s names in `L`: 1 for a live run, else 0 and none (`run`
// NULL: a game) but the crypto rows, which every state holds.
int moy_app_lua_open(lua_State *L, const struct moy_app_run *run);

// A hop: fn(arg) run on the VM's task while the caller waits, 0; or -1 where
// nothing serves it, fn not run (a row then answers NEEDS_VM). With `here`,
// fn(arg) runs in place, on the caller's task, its time the console's (the
// store's) and not the call's; 0.
typedef int (*moy_app_lua_hop_fn)(lua_State *L, void (*fn)(void *arg), void *arg, int here);
// moy_app_lua_open for a state whose calls take `hop` (NULL: in place).
int moy_app_lua_open_hop(lua_State *L, const struct moy_app_run *run,
                         moy_app_lua_hop_fn hop);

// The crypto rows (native/moy_net/moy_crypto.h: moybyte.random, sha256,
// hmac_sha256, aes_gcm_seal, aes_gcm_open, aes_ctr, aes_ccm_seal,
// aes_ccm_open, x25519, ed25519_public, ed25519_sign, ed25519_verify) put
// into `L`'s global
// `moybyte` table, made where it has none: they need no grant, so a Lua
// service with none holds them too (native/moycore/moycore_svc.c). 1.
int moy_app_lua_open_crypto(lua_State *L);

// The JSON the prefs names read and write, for another binding's state (a
// Lua service's messages and console calls, native/moycore/moycore_svc.h):
// the one JSON value `text` holds (n bytes) pushed as prefs.get decodes it,
// 1, or 0 with nothing pushed where it holds none; and the value at `idx` as
// prefs.set encodes it, pushed as a string whose bytes are answered (raising
// on a value that is not JSON).
int moy_app_lua_json_push(lua_State *L, const char *text, size_t n);
const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n);

#endif // MOY_APP_LUA_H
