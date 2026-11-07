// A run's grant: what a tier binding serves one run of an app from (a Lua
// state, moy_app_lua.h; a wasm instance, moy_app_wasm.h). It names the state
// its grant is a row of, the grant, and the role mask its permissions name,
// and it is a value: the run's owner holds it (the engine's per-run state
// today, a process-table row in docs/os_2026-11/processes.md section 1), and
// every call the run makes is answered from it and no other. Two runs hold
// two of these, and neither reaches the other's grant.
//
// A run with no grant (a game) has `a` NULL. A copy is safe past its grant's
// end (its calls answer STALE: the grant table's generation) and past its
// state's free (moy_app_run_live). It depends on nothing but this header, so
// an engine's glue can hold one without the app ABI's other headers.

#ifndef MOY_APP_RUN_H
#define MOY_APP_RUN_H

#include <stdint.h>

struct moy_app;

typedef struct moy_app_run {
    struct moy_app *a;      // the state, or NULL: no app run
    uint32_t sn;            // the state's serial, which a freed state's successor never has
    uint32_t g;             // the grant
    uint32_t roles;         // 1 << MOY_ROLE_* for each role the permissions name
} moy_app_run_t;

#endif // MOY_APP_RUN_H
