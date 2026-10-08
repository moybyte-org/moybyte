// moy_play: the kernel's Player (docs/kernel_cartpath_2026-10.md §3). What a
// cart's run is, decided and driven below the VM.
//
// THE VM-FREE RULE (§2), one function the census and the Player both call,
// decided from the catalogue entry alone:
//   1. the runtime is "lua" or "wasm" and this image has its row (moy_rt_get);
//   2. the type is "game" (a manifest that names none is a game when it is a
//      spec cart, an app otherwise: runtime/moy_carts.py's rule);
//   3. every permission is in the native set: graphics, input, audio,
//      multiplayer. A permission the kernel does not know keeps the VM.
// A cart that fails it runs with the VM up, and `why` names the first clause
// it failed.
//
// THE PLAYER (moy_play.c). One run at a time, named by a handle that is
// refused (STALE) once it ended. `launch` reads the cart's catalogue entry
// for its runtime and verdict and takes that runtime's row from the map;
// `bind` gives the run its input table, its audio session and the tick model
// a paced run notes its costs into; `open` asks the row whether its runtime
// is open. `frame` runs the ticks the tick model planned: each takes the
// latched press edges (a paced run), fills the snapshot the cart reads from
// the input table and the pointer `input` published, runs the row's tick,
// notes its update half, and plays the audio it queued straight into the
// session; a quit() ends the frame where it stands. `end` closes the books:
// the run's upcalls by class since its launch, kept with its info for `state`.
//
// While the VM is up the console's frame upcall still drives the Player, the
// binding builds the run's console over Python's buffers before `open`, and
// closes it after `end`.

#ifndef MOY_PLAY_H
#define MOY_PLAY_H

#include <stdbool.h>
#include <stdint.h>

#include "moy_cat.h"

// The Player's answers, beside MOY_HTAB_*'s (moy_route.h's shape).
enum {
    MOY_PLAY_OK = 0, MOY_PLAY_STALE = 1, MOY_PLAY_FULL = 2, MOY_PLAY_NOMEM = 3,
    MOY_PLAY_NOCART = 4,    // no such cart, or it will not load
    MOY_PLAY_NORT = 5,      // its runtime is not in this image
    MOY_PLAY_NEWER = 6,     // a compiled cart imports what this console lacks
    MOY_PLAY_FIT = 7,       // a compiled cart's footprint is over what is free
    MOY_PLAY_RAISED = 8,    // the load, _init or a frame raised: the error panel
    MOY_PLAY_ENDED = 9,     // the run is over; its route is decided
    MOY_PLAY_NEEDS_VM = 10, // a Python-row call while no VM runs
};

// Why a run keeps the VM: the rule's clauses, in the order they are tested.
enum {
    MOY_PLAY_FREE = 0,          // no reason: the run needs no VM
    MOY_PLAY_WHY_BROKEN = 1,    // the manifest would not read
    MOY_PLAY_WHY_RUNTIME = 2,   // the runtime is Python, or one no row runs VM-free
    MOY_PLAY_WHY_NORT = 3,      // the runtime's row is not in this image
    MOY_PLAY_WHY_TYPE = 4,      // not a game
    MOY_PLAY_WHY_PERM = 5,      // a permission outside the native set
    MOY_PLAY_WHY_COUNT = 6,
};

bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why);

// -- the Player ------------------------------------------------------------------

#include "moy_tick.h"

struct moy_input;                               // moy_input.h

enum { MOY_PLAY_PACED = 1u };                   // launch flags: on the tick model
enum { MOY_PLAY_QUIT = 1u, MOY_PLAY_VIEW = 2u };    // what a frame reports
enum {                                          // why a run ended
    MOY_PLAY_END_HOLD = 0, MOY_PLAY_END_QUIT = 1, MOY_PLAY_END_MENU = 2,
    MOY_PLAY_END_CRASH = 3, MOY_PLAY_END_LINK = 4, MOY_PLAY_END_SERIAL = 5,
};

// libmoy's seven buttons are the input table's first seven bits.
#define MOY_PLAY_BUTTON_MASK 0x7Fu
// A run handle's low byte: generation << 8 | this.
#define MOY_PLAY_TAG 0x5Au
#define MOY_PLAY_UPC 5          // MOY_UPC_CLASSES, the loop's crossing classes

typedef struct {
    int32_t x, y;               // the pointer in the cart's own coordinates
    int32_t touch;              // touch()'s flags: 1 live, 2 held, 4 the press edge
} moy_play_in_t;

typedef struct {
    char runtime[12];           // the manifest's, with the store's default
    char error[192];            // what the cart raised, when it did
    uint32_t frames, ticks;
    uint32_t upcalls[MOY_PLAY_UPC];    // since the launch, frozen at the end
    // The driving task's stack high-water mark (bytes never used) after the
    // open and after the last frame; MOY_PLAY_NO_STACK where there is no
    // meter (off a board).
    uint32_t stack_open, stack_frame;
    uint8_t why;                // MOY_PLAY_FREE, or the rule's clause
    uint8_t end_why;
    bool vm_free;
    bool raised;
    bool ended;
} moy_play_info_t;

int  moy_play_launch(const char *cart, const char *caller, uint32_t flags, uint32_t *run);
int  moy_play_bind(uint32_t run, struct moy_input *in, uint32_t audio, moy_tick_t *tick);
int  moy_play_open(uint32_t run);
int  moy_play_input(uint32_t run, const moy_play_in_t *in);
// `ticks` logic ticks of `dt`, the last drawing when `render`; `out` gets
// MOY_PLAY_QUIT and MOY_PLAY_VIEW. OK, RAISED (the error in `info`), ENDED,
// NEEDS_VM or STALE.
int  moy_play_frame(uint32_t run, uint8_t ticks, float dt, bool render, uint32_t *out);
int  moy_play_end(uint32_t run, int why);
// The live run's info, or the last one's once it ended.
int  moy_play_info(uint32_t run, moy_play_info_t *out);
uint32_t moy_play_current(void);       // the live run, or 0
// The calling task's stack high-water mark in bytes, MOY_PLAY_NO_STACK off a
// board. FreeRTOS keeps the task's lifetime minimum, so a reading falls only
// when the cart's path goes deeper than anything the task ran before it.
#define MOY_PLAY_NO_STACK 0xFFFFFFFFu
uint32_t moy_play_stack_free(void);
uint32_t moy_play_last(void);          // the last run launched, live or ended
// The map's rows this image has: lua and wasm where moycore and its engine
// are built, python where a VM registers it.
void moy_play_rows(bool lua, bool wasm, bool python);
// The reason's word, for `info`, `state` and the census: "free", "broken",
// "runtime", "absent", "type", "permission".
const char *moy_play_why_name(uint8_t why);
// The census's line for an entry: "<runtime> <free|vm> <why>", the runtime
// with the store's default ("lua" for a spec cart that names none, "python"
// otherwise, "?" for one that is no string). snprintf's answer.
int moy_play_census_line(const moy_cat_entry_t *e, char *out, size_t n);

#endif // MOY_PLAY_H
