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
// The reason's word, for `info`, `state` and the census: "free", "broken",
// "runtime", "absent", "type", "permission".
const char *moy_play_why_name(uint8_t why);
// The census's line for an entry: "<runtime> <free|vm> <why>", the runtime
// with the store's default ("lua" for a spec cart that names none, "python"
// otherwise, "?" for one that is no string). snprintf's answer.
int moy_play_census_line(const moy_cat_entry_t *e, char *out, size_t n);

#endif // MOY_PLAY_H
