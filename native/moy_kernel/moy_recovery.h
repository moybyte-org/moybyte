// moy_recovery: the kernel's own screens (docs/kernel_spine_2026-10.md §7,
// docs/look_2026-10.md step 33, GAPS 55).
//
// What the kernel draws when the console cannot come up -- the recovery
// floor -- and while no VM runs and the web console serves: in the 2026-10
// look, through moy_scr (native/moy_gfx/moy_scr.h) and the outline faces
// compiled into C, straight into a panel module's framebuffer 0. No canvas
// state, no compositor, no PPA, no allocation: a turned panel draws through
// the band the caller lends, and the faces through its scratch. Portable C:
// the host builds it for tests/test_moy_kernel.py, which renders every
// console's geometry and pins the hashes the boards print.
//
// Recovery is in Moy Night whatever the stored theme (the store may be what
// failed): Moy, "Moybyte didn't start" and why, the three choices as rows
// of a card, what the kernel knows in the code face, and the keys at the
// foot. The web-console screen is the shell's page without its QR, on the
// ground the boot splash draws on.
//
// The screen is LOGICAL landscape (w x h); `rot` turns it onto the panel's
// framebuffer counter-clockwise, the PPA's convention (device/dsi_panel.py's
// rotate_rect): 90 maps (x, y) to (y, w-1-x), 270 maps it to (h-1-y, x).

#ifndef MOY_RECOVERY_H
#define MOY_RECOVERY_H

#include <stddef.h>
#include <stdint.h>

#include "moy_crash.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int w, h;                        // the logical screen
    int fb_w, fb_h;                  // the framebuffer, panel-native
    int rot;                         // 0, 90, 180 or 270
    int swap;                        // the framebuffer holds RGB565 high byte first
    int touch;                       // a finger picks a row: rows at the tap floor
    uint16_t *band;                  // a turned panel's band, w * band_rows pixels
    int band_rows;
    void *scratch;                   // lent to the outline faces (moy_path_lend)
    size_t scratch_n;
} moy_rgeom_t;

#define MOY_RV_LINES   8
#define MOY_RV_COLS    64
#define MOY_RV_CHOICES 3             // RETRY, SAFE, REPL: MOY_BOOT_START + index

typedef struct {
    char line[MOY_RV_LINES][MOY_RV_COLS];
    int nlines;
    char hint[MOY_RV_COLS];
    int sel;                         // the highlighted choice, 0..2
} moy_rview_t;

// The geometry, with no band and no scratch.
void moy_rgeom_init(moy_rgeom_t *g, int w, int h, int rot, int swap);

// The lines the screen says: why it is up (line 0), what the record names,
// the firmware, and with `serial` the words serial takes. `rec` may be NULL.
void moy_recovery_view(moy_rview_t *v, int why, const moy_crash_rec_t *rec,
                       const char *label, const char *hint, int serial);

// The draws that failed (0: none).
int moy_recovery_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v);

// What the plain web-console screen says, from the web console's paired URL
// ("http://a.b.c.d[:port]/?pin=..."): the sentence, the address and the pin;
// with no address, that the console is restarting.
void moy_web_screen_view(moy_rview_t *v, const char *url);
// The screen on `pal`'s ground (moy_scr.h's splash palette; NULL: Moy Day).
int moy_web_screen_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v,
                          const uint16_t *pal);

// The choice under a logical point, or -1.
int moy_recovery_hit(const moy_rgeom_t *g, int x, int y);

// The serial protocol's name for a choice: RETRY, SAFE or REPL
// (tools/kernel_gate.py parses it), whatever the screen says.
const char *moy_recovery_choice_name(int choice);

#ifdef __cplusplus
}
#endif

#endif // MOY_RECOVERY_H
