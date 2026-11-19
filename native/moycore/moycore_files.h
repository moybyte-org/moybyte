// A cart's files (moy-spec SPEC.md 9.1) over the cart's own folder and its
// written files, the store a compiled run's are kept in
// (native/moy_store/moy_files.h). A Lua run's are bound into its console
// (moy.h's moy_host file fields), where libmoy's `read`, `write`, `erase` and
// `list` (moy_lua.c) hold every path to the manifest's "writable" and the
// 1 MiB cap before it reaches here. A Python cart's are the same store opened
// on its own (moycore_files_open), whose verbs hold its paths to the same
// rules (libmoy's moy_cartpath.h): modmoycore.c's CartFiles, and the host's
// runtime/host/moycore.py.
//
// Each op is one store op, run where the Lua run calls it -- the MicroPython
// task, or with no VM the VM service task (native/moy_play/moy_play.h) --
// inside the board's bus gate (moy_vol_gate_enter), and where the volume can
// raise (MicroPython's), under an nlr frame whose raise is the op's refusal.
// In the browser a write is refused when the page's keeper says it will not
// fit, and every write and erase is logged for the keeper as a compiled
// run's are (moycore_wasm.c's moycore_keep_*).

#ifndef MOYCORE_FILES_H
#define MOYCORE_FILES_H

#include <stddef.h>
#include <stdint.h>

#include "moy.h"

struct moycore_files;

// The files of the cart in folder `dir`, its "writable" entries `writable`
// (`n` bytes, NUL-joined; NULL or 0 for none), bound into `con` and held in
// `*slot`, replacing what the slot held. dir NULL unbinds. 0, or -1 when the
// store cannot be opened (the verbs then answer as a host with none).
int moycore_files_bind(moy_console *con, struct moycore_files **slot, const char *dir,
                       const char *writable, size_t n);
// The current Lua run's (moycore_lua.h's moycore_RUN): what the binding's
// `lua_files(dir, writable)` calls after the run opens. Its close unbinds.
int moycore_lua_files(const char *dir, const char *writable, size_t n);

// The files of the cart in folder `dir`, its "writable" entries as above, on
// their own: NULL when the store cannot be opened. Close lets them go.
struct moycore_files *moycore_files_open(const char *dir, const char *writable, size_t n);
void moycore_files_close(struct moycore_files *s);
// SPEC.md 9.1's verbs, each `name` NUL-terminated after its `n` bytes:
// read's bytes from `offset` into `dst` (with `dst` NULL how many there are),
// the written copy where the path is declared and one is kept, else the
// cart's own file, 0 for none; write's 0, -1, -2 or -3; erase's 0 or -1; and
// list's `index`th path beginning with `prefix`, its length (more than `cap`:
// only its length), or -1 past the last.
int32_t moycore_files_read(struct moycore_files *s, const char *name, size_t n,
                           uint32_t offset, uint8_t *dst, uint32_t len);
int32_t moycore_files_write(struct moycore_files *s, const char *path, size_t n,
                            const uint8_t *data, uint32_t len);
int32_t moycore_files_erase(struct moycore_files *s, const char *path, size_t n);
int32_t moycore_files_list(struct moycore_files *s, const char *prefix, size_t n,
                           uint32_t index, uint8_t *dst, uint32_t cap);

// The browser's keeper (moycore_wasm.c): whether a write of `len` bytes is
// refused for room, and a write ('p') or an erase ('d') of `path` logged for
// the cart whose files are kept in `keep_dir`. Weak: none in a build without
// it.
int moycore_keep_refuses(uint32_t len);
void moycore_keep_log(char op, const char *keep_dir, const char *path);

#endif // MOYCORE_FILES_H
