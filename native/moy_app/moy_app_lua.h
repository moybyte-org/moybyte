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
// door (moy_app_door_bind).

#ifndef MOY_APP_LUA_H
#define MOY_APP_LUA_H

#include "lua.h"
#include "moy_app_run.h"

// Open run `run`'s names in `L`: 1 for a live run, else 0 and none (`run`
// NULL: a game).
int moy_app_lua_open(lua_State *L, const struct moy_app_run *run);

// The JSON the prefs names read and write, for another binding's state (a
// Lua service's messages and console calls, native/moycore/moycore_svc.h):
// the one JSON value `text` holds (n bytes) pushed as prefs.get decodes it,
// 1, or 0 with nothing pushed where it holds none; and the value at `idx` as
// prefs.set encodes it, pushed as a string whose bytes are answered (raising
// on a value that is not JSON).
int moy_app_lua_json_push(lua_State *L, const char *text, size_t n);
const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n);

#endif // MOY_APP_LUA_H
