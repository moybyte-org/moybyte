// moy_status: the bar's status items, the kernel's table (docs/os_2026-11/
// processes.md section 7; runtime/system/status_items.py is the console's
// face over it).
//
// An ITEM is a slot of MOY_STATUS_CAP: the process it is of, its icon (a
// name of the console's icon set), the state its icon shows (ON, PAUSED,
// ERROR), its order among the others (lower first, ties by age), whether it
// is shown, and whether it is a compiled program's (whose section is the
// panel it describes, the console's to draw) or a Python program's (whose
// section is its own Python). Its handle is the slot's number from 1.
//
// The table is written from any task -- a compiled program's status rows
// run on its own task (native/moy_app/moy_app.c, how "here"), with or
// without the VM -- and the console reads it to draw: every write moves
// the generation (moy_status_gen), and the console reads the slots again
// once it moved. What the item's section draws stays the console's: a
// compiled program's panel is described through the VM (status_panel).
//
// Any task may call any function; no ISR may. Nothing allocates.

#ifndef MOY_STATUS_H
#define MOY_STATUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { MOY_STATUS_ON = 0, MOY_STATUS_PAUSED = 1, MOY_STATUS_ERROR = 2 };

#define MOY_STATUS_CAP 4u           // items at once
#define MOY_STATUS_ICON 24u         // an icon's name, its NUL not counted

// The answers: a handle (1 .. CAP) or one of these, negated.
enum {
    MOY_STATUS_EFULL = 2,           // every slot is taken
    MOY_STATUS_EBAD = 7,            // an empty or long icon, a state out of range
    MOY_STATUS_EABSENT = 8,         // no such item of that process (of that kind)
};

typedef struct {
    uint32_t pid;                   // 0: the slot is free
    char icon[MOY_STATUS_ICON + 1u];
    int32_t order;
    uint32_t age;                   // the add's count: older first among equal orders
    uint8_t state;
    bool shown;
    bool compiled;
} moy_status_item_t;

// An item of process `pid`'s, not shown yet: its handle, or -EFULL, -EBAD.
int moy_status_add(uint32_t pid, const char *icon, size_t n, int32_t order, bool compiled);
// Item `h` shown in `state`. `pid` 0 names any process's item, else the item
// must be that process's compiled one. 0, -EABSENT, -EBAD.
int moy_status_show(uint32_t pid, int h, int state);
// Item `h` freed, as moy_status_show names it. 0, -EABSENT.
int moy_status_remove(uint32_t pid, int h);
// Every item of process `pid` freed (its end). How many.
uint32_t moy_status_sweep(uint32_t pid);
// Moves with every write.
uint32_t moy_status_gen(void);
// Slot `h`'s item copied whole into `out` (a free slot's pid is 0): whether
// `h` names a slot.
bool moy_status_get(int h, moy_status_item_t *out);
// Every slot freed (a test's teardown).
void moy_status_reset(void);

#endif // MOY_STATUS_H
