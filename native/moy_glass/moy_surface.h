// The surface table (docs/surface_model_v1.md §2, §15; docs/kernel_survival_2026-10.md
// §3.3).
//
// One row of kind SURF per WM surface: its sid (a WM registry key such as
// "win:make", never a content kind), its domain, its placement and two
// generations, all minted from ONE monotonic counter so a reborn surface reads
// as changed to anyone who cached a gen of its predecessor. Compare gens with
// !=, never >.
//
// The un-attributed dirty is SET-LEVEL: moy_surface_epoch() mints one gen that
// moy_surface_content_gen() folds into every surface, known or not. The
// kernel's own draws (the idle wake, the saver, the parked screen, the floor,
// the HUD) bump a second counter, the KERNEL EPOCH, which the Python frame gate
// reads as dirty while a buffer it retains still holds the kernel's picture.
//
// The table is inert on every shipping tier until a window manager reads a gen
// (sprint 7); the producers are what a VM-free cart reaches in sprint 4.

#ifndef MOY_SURFACE_H
#define MOY_SURFACE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_SURF_SID 23u

typedef struct {
    char sid[MOY_SURF_SID + 1u];
    uint8_t domain;
    uint8_t animating;
    int16_t x, y;
    uint16_t w, h;
    uint16_t scale;
    int16_t z;
    uint32_t content_gen;
    uint32_t place_gen;
} moy_surf_row_t;

// The row for `sid`, created with fresh gens when it has none. OK, FULL, NOMEM.
int moy_surface_get(uint32_t *h, const char *sid, uint8_t domain);
// The handle of `sid`'s row, or 0.
uint32_t moy_surface_find(const char *sid);
int moy_surface_row(uint32_t h, moy_surf_row_t **row);
void moy_surface_touch(uint32_t h);             // Class A, attributed
void moy_surface_move(uint32_t h);              // placement only (L1)
void moy_surface_animating(uint32_t h, bool on);    // Class B
void moy_surface_epoch(void);                   // Class A, un-attributed
uint32_t moy_surface_mint(void);
// The gen a consumer compares: the row's own folded with the epoch; an
// unknown or released handle rides the epoch alone.
uint32_t moy_surface_content_gen(uint32_t h);
int moy_surface_drop(uint32_t h);
// Drop every row whose sid starts with `prefix` and is not among the `n` sids
// of `alive` (each NUL-terminated).
void moy_surface_sync(const char *const *alive, size_t n, const char *prefix);
uint32_t moy_surface_kernel_epoch(void);
void moy_surface_kernel_bump(void);             // the kernel drew: every buffer stale
uint32_t moy_surface_count(void);

#endif // MOY_SURFACE_H
