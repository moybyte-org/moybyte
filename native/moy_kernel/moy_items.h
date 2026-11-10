// moy_items: the items handed to processes (docs/os_2026-11/between_apps.md,
// an item opened with or picked for an app), as the store names them, so a
// compiled program's item_read and item_write run on its own task under the
// store's lock, with or without the VM (native/moy_app/moy_app.c, item_row).
//
// The console registers each user file it hands a process (moy_items_hand:
// the handle the process's `moy_open` message carries, the file's kind and
// name in the user-files store, and whether the grant is "rw"), and the
// process's end forgets them (moy_items_end). An item the table does not
// hold -- shared data with no file, a handle past MOY_ITEMS, a handle never
// handed -- is the console's to answer (runtime/system/moybyte.py,
// hand_item; the row's door, runtime/shell/shell_servers.py's ConsoleCalls).
//
// Any task may call any function; no ISR may. Nothing allocates.

#ifndef MOY_ITEMS_H
#define MOY_ITEMS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_ITEMS 16u               // items held at once, every process's
#define MOY_ITEMS_KIND 23u          // a kind's bytes (a user-files folder name)
#define MOY_ITEMS_NAME 255u         // a name's bytes (MOY_UF_NAME_MAX)

// Item `h` of process `pid` is user file (kind, name): true where the table
// took it (false: full, or a kind or name too long, which the console then
// answers).
bool moy_items_hand(uint32_t pid, int32_t h, const char *kind, size_t kn, const char *name,
                    size_t nn, bool rw);
// Item `h` of process `pid`: whether the table holds it, its kind and name
// copied into `kind` and `name` (kcap and ncap bytes, each with room for its
// NUL, else not held) and whether it is "rw".
bool moy_items_get(uint32_t pid, int32_t h, char *kind, size_t kcap, char *name, size_t ncap,
                   bool *rw);
// Process `pid` ended (or one handle of it went: `h` not 0): its items forgotten.
void moy_items_end(uint32_t pid, int32_t h);
// Everything forgotten (a test's teardown).
void moy_items_reset(void);

#endif // MOY_ITEMS_H
