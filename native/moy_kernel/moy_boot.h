// moy_boot: the kernel's first light (docs/kernel_survival_2026-10.md §7.4,
// the owner's answer 9 in §13). Before any VM exists the kernel brings the
// panel up, draws the boot logo into framebuffer 0 and lights the glass; the
// console's themed boot screen follows once Python is up.
//
// The logo is runtime/console.py's draw_splash with no track and no status,
// to the pixel, because both are moy_scr's splash (native/moy_gfx/moy_scr.h):
// Moy at min(w, h) / 56 times (at least 3) over the `moybyte` wordmark in the
// bold outline face, on the stored theme's ground -- the palette the console
// keeps in the kernel's NVS (moy_kernel.splash), Moy Day before there is one.
// The two are one picture or the machine appears to start twice;
// tests/test_moy_kernel.py renders both on the host and holds them equal on
// every console's geometry.
//
// Portable C, no allocation: the host builds it beside moy_recovery.c.

#ifndef MOY_BOOT_H
#define MOY_BOOT_H

#include <stdbool.h>
#include <stdint.h>

#include "moy_recovery.h"

#ifdef __cplusplus
extern "C" {
#endif

// The draws that failed (0: none). `pal` NULL is Moy Day's.
int moy_boot_logo_render(uint16_t *fb, const moy_rgeom_t *g, const uint16_t *pal);

// The board half (moy_kernel.c): the kernel lit the glass with the logo this
// boot, so a compositor coming up keeps the light on and the picture showing.
bool moy_boot_lit(void);

// The splash's palette (moy_scr.h's MOY_SPLASH_PAL words) into `out`: 1
// the one the console keeps, 0 Moy Day's while none is kept, -1 on a board
// with no panel (nothing written).
int moy_kernel_splash_pal(uint16_t *out);
// Keep `pal` for the next boot's first light (NVS); whether it was written
// (false too when it is already the one kept).
bool moy_kernel_splash_keep(const uint16_t *pal);

#ifdef __cplusplus
}
#endif

#endif // MOY_BOOT_H
