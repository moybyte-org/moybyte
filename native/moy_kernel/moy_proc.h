// moy_proc: the process table (docs/os_2026-11/processes.md section 1) for
// the processes that run outside the shell's VM: a Lua or compiled run today,
// an ELF one later. A row is kind MOY_KIND_PROC (moy_htab.h); the table is in
// PSRAM, made at the first add.
//
// A ROW is a process: its pid (moy_log.h's, the OS log's name for it), its
// tier and kind, its state and exit code, the cart id it runs, the OWNER its
// loans are on, the run that drives it (the kernel's Player's handle), the
// ENGINE the tier runs it in (a Lua state, a wasm instance's binding) and its
// grant, a moy_app_run_t VALUE the row owns. It is not an OWNER row: a VM
// stop ends every OWNER and the row outlives one.
//
// WHO MAKES IT. The kernel's Player adds one per Lua or compiled run at its
// launch (native/moy_play/moy_play.c) and names it NEXT; the runtime's row,
// made after the launch, takes it (moycore_lua.c), and the runtime's open
// names the row's engine and copies the run's grant into it (moycore_lua.c,
// moycore_wasm.c, the host's moyhost_wasm.c), so an engine's call finds its
// grant by the engine (moy_proc_app_of: moy_app_wasm_run_of's lookup). The
// Player ends the row at the run's end, after the runtime's close: the Lua
// state closes first, so a file it left open is saved, then the row and its
// copy of the grant go, and the console ends the grant after.
//
// Every call is the VM's task's, or the run's own task while the VM's waits
// on it; nothing allocates but the table, at the first add.

#ifndef MOY_PROC_H
#define MOY_PROC_H

#include <stdbool.h>
#include <stdint.h>

#include "moy_app_run.h"

enum {
    MOY_PROC_OK = 0,
    MOY_PROC_STALE = 1,         // no live row by that handle
    MOY_PROC_FULL = 2,
    MOY_PROC_NOMEM = 3,
};

// The tiers.
enum { MOY_PROC_PYTHON = 1, MOY_PROC_LUA = 2, MOY_PROC_WASM = 3, MOY_PROC_ELF = 4 };
// The kinds (processes.md section 1's table).
enum { MOY_PROC_APP = 1, MOY_PROC_GAME = 2, MOY_PROC_WALLPAPER = 3,
       MOY_PROC_COMMAND = 4, MOY_PROC_SERVICE = 5 };
// The states the table keeps; the shell's finer ones (throttled, minimised)
// are its window manager's.
enum { MOY_PROC_RUNNING = 1, MOY_PROC_RAISED = 2, MOY_PROC_EXITED = 3 };

#define MOY_PROC_ROWS 8u
#define MOY_PROC_ID 32u

typedef struct {
    uint32_t pid;
    uint32_t owner;             // its OWNER row (moy_buf.h), 0 none
    uint32_t run;               // the Player's run handle, 0 none
    void *engine;               // the tier's engine for it, NULL before its open
    moy_app_run_t app;          // its grant, `g` 0: none (a game)
    int32_t exit;
    uint8_t tier;
    uint8_t kind;
    uint8_t state;
    char id[MOY_PROC_ID];       // the cart id, cut to fit
} moy_proc_row_t;

// A row for process `pid`: OK with *h, FULL, NOMEM.
int moy_proc_add(uint32_t pid, uint8_t tier, uint8_t kind, const char *id,
                 uint32_t owner, uint32_t run, uint32_t *h);
// The row ends with `code` (its exit), released.
int moy_proc_end(uint32_t h, int32_t code);
// The row the next runtime row takes (0: none), and taking it: once.
void moy_proc_next(uint32_t h);
uint32_t moy_proc_take(void);
int moy_proc_get(uint32_t h, moy_proc_row_t **row);
int moy_proc_state(uint32_t h, uint8_t state);
// The runtime's open: the engine that runs it, and its grant copied (NULL:
// none).
int moy_proc_bind(uint32_t h, void *engine, const moy_app_run_t *app);
// The live row of `pid` / of `engine`, or 0.
uint32_t moy_proc_of_pid(uint32_t pid);
uint32_t moy_proc_of_engine(const void *engine);
// The grant the row of `engine` holds, or NULL: no row, or a run with none.
const moy_app_run_t *moy_proc_app_of(const void *engine);
// The live rows in slot order: slots() bounds the walk, at(slot) is a handle
// or 0.
uint32_t moy_proc_slots(void);
uint32_t moy_proc_at(uint32_t slot);
uint32_t moy_proc_count(void);
// Every row dropped (a test's teardown).
void moy_proc_reset(void);

#endif // MOY_PROC_H
