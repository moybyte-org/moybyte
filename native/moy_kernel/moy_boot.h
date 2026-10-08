// moy_boot: the kernel's first light (docs/kernel_survival_2026-10.md §7.4,
// the owner's answer 9 in §13). Before any VM exists the kernel brings the
// panel up, draws the boot logo into framebuffer 0 and lights the glass; the
// console's themed boot screen follows once Python is up.
//
// The logo is runtime/console.py's draw_splash with no bar and no status, to
// the pixel: Moy (chrome.py's baked "moy" art, never the themed icon sheet) at
// min(w, h) / 56 times (at least 3) over the dark field, the two-tone
// `moybyte` wordmark in libmoy's 8x8 font under it. The two are one picture or
// the machine appears to start twice; tests/test_moy_kernel.py renders both
// on the host and holds them equal on every console's geometry.
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

void moy_boot_logo_render(uint16_t *fb, const moy_rgeom_t *g);

// The board half (moy_kernel.c): the kernel lit the glass with the logo this
// boot, so a compositor coming up keeps the light on and the picture showing.
bool moy_boot_lit(void);

#ifdef __cplusplus
}
#endif

#endif // MOY_BOOT_H
