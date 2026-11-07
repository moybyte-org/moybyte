// The app ABI's wasm import adapter (docs/history/kernel_appabi_2026-10.md section
// 4): the role rows a compiled app imports, from module "moybyte.app" (the
// vendor extension its manifest declares, moy-spec SPEC.md 16.2), one native
// per row of roles.json with a wasm type, named "<role>_<verb>" at that type.
//
// A native runs on the thread that calls the cart's import: a board's session
// thread, whose stack is PSRAM (native/moy_wasm/moy_wasm_session.h), the
// browser's one thread, the host's. What it may do there is the design's
// rule: a row that writes and every shell row hop to the VM's task through
// moy_wasm_on_vm (a shell row enters MicroPython only there, through the ROLE
// door, moy_app_role); a read of a row the look or the clipboard writes
// (theme.token, gen, light, name, variant; clipboard.text, kind, seq)
// runs where it is called, under the state's seqlock, which every write of
// those rows takes (moy_app_seq). The rest of the reads (prefs, files) hop
// too: the settings rows and the store are the VM task's. Where there is no
// session thread (the host, the browser) the hop is a call.
//
// A native serves the run its call is made for (moy_app_wasm_run_of): the
// run's grant, which the Player made from the cart's permissions at its
// launch, and the roles those permissions name, held by the engine's per-run
// state for its instance. A module importing a row of a role its permissions
// do not name is refused at its load, the import named (moy_app_wasm_admit);
// the theme's reads need none, as a cart's theme() is ungated.

#ifndef MOY_APP_WASM_H
#define MOY_APP_WASM_H

#include <stddef.h>
#include <stdint.h>

#include "moy_app.h"

#define MOY_APP_EXT "moybyte.app"   // the extension, and the import module

// WAMR's NativeSymbol, field for field: an engine registers these rows under
// MOY_APP_EXT and hands them to libmoy's check as the extension's table.
typedef struct {
    const char *symbol;
    void *func_ptr;
    const char *signature;
    void *attachment;
} moy_app_native_t;

// The natives, in roles.json's order of the rows with a wasm type, and the
// table row each serves.
const moy_app_native_t *moy_app_wasm_natives(uint32_t *count);
int moy_app_wasm_row(uint32_t i);
// The table row an import name serves, or -1.
int moy_app_wasm_row_named(const char *name, size_t n);

// The run a native's call is for (moy_app_run.h): the engine's handle on the
// call -- a WAMR exec env, or the browser's binding -- to the run its glue
// holds for that instance, which the engine's glue defines
// (native/moycore/moycore_wasm.c on a board and in the browser,
// runtime/host/moyhost_wasm.c on the host). Weak here: with none, or a run
// whose state is gone, every native but the toolkit's answers -MOY_APP_STALE.
// No call reads another run's grant: two instances are two runs.
const moy_app_run_t *moy_app_wasm_run_of(void *e);

// The console's state, the one the theme is read from (moy_app_wasm_theme),
// as a Lua game's `ui` reads it: named at every launch on a tier binding,
// let go by forget when it is freed.
void moy_app_wasm_live(moy_appabi_t *a);
void moy_app_wasm_forget(const moy_appabi_t *a);

// The module's imports from MOY_APP_EXT against run `r`'s grant: 0, or
// non-zero with the first one whose role the grant does not hold named in
// `err` (every one, with no run). `head` is the module through its import
// section at least.
int moy_app_wasm_admit(const moy_app_run_t *r, const uint8_t *head, size_t n, char *err,
                       size_t errlen);

// Every import of a module, (module, name) in order, from its head: what the
// load check and the VM-free verdict read. Stops at the first section past
// the imports, or at bytes that do not parse (answering -1 then).
typedef int (*moy_app_import_fn)(void *ctx, const uint8_t *mod, size_t mn,
                                 const uint8_t *name, size_t nn);
int moy_app_wasm_imports(const uint8_t *head, size_t n, moy_app_import_fn fn, void *ctx);

// The live theme's token row and its name (NUL-terminated in `cap` bytes),
// read under the state's seqlock for the public UI API's RUN face
// (native/moy_ui/moy_ui_wasm.h): copied only when the generation differs
// from `have`. Answers a generation that is never 0, or 0 while no state
// was ever bound.
uint32_t moy_app_wasm_theme(int32_t out[MOY_APP_TOKENS], uint32_t have, char *name,
                            uint32_t cap);

// The hops made, the microseconds they took end to end, and the rows' own
// share of those on the VM's task: the hop's price per call is the difference
// over the count (the suites read it).
void moy_app_wasm_hops(uint32_t *count, uint32_t *us, uint32_t *work_us);

#endif // MOY_APP_WASM_H
