// moy_recovery: what the kernel says when the console cannot come up, and its
// own screen while no VM runs and the web console serves.
//
// THE FLOOR comes up when the VM fails to start (its first heap area is not
// there, or the console's boot ends before boot_ok), the boot-loop guard
// trips or the console crashed its crash count's limit of boots in a row
// (moy_crash.h), and only on a boot no VM ran in (moy_kernel.c). Its screen
// is the recovery console (native/moy_sh/moy_sh_floor.h): the Terminal's face
// under the lines moy_recovery_view makes here and the choices Try again
// (RETRY: the VM as usual), Safe start (SAFE: system/settings neither read
// nor written, the defaults, a fill for the wallpaper) and Reboot (a restart
// with the counts started over); `repl` (REPL: main.py skipped) is a typed
// word only. Serial drives every floor, the headless one included: while the
// VM is down the kernel drains the console's RX ring and reads `retry`,
// `safe`, `reboot` and `repl`, runs `sh LINE` in the C shell (the card is
// brought up from C as the floor starts, moy_store_card_up), answers `state`
// with "screen": "recovery", takes `type TEXT`, `key N` and `tap X Y` into the
// screen's input and answers `screen` with what the face shows, and prints
// `KERNEL recovery reason=<r> sel=<choice> crc=<the screen's crc32>` with its
// lines, its input, its buttons' boxes and its hint every five seconds, which
// is how tools/kernel_gate.py drives a board. `sel` is the choice the floor
// takes on its own: SAFE where no input came up and the board names an idle
// time (MOY_KERNEL_IDLE_SAFE_MS: the headless Zero, a Guition P4 whose touch
// failed its upload), after that long with nothing typed; RETRY elsewhere,
// where it takes none. `sel=` names stay RETRY, SAFE and REPL.
//
// The web-console screen is drawn here in the 2026-10 look, through moy_scr
// (native/moy_gfx/moy_scr.h) and the outline faces compiled into C, straight
// into a panel module's framebuffer 0: the shell's page without its QR, on the
// ground the boot splash draws on. No canvas state, no compositor, no PPA, no
// allocation: a turned panel draws through the band the caller lends, and the
// faces through its scratch. Portable C: the host builds it for
// tests/test_moy_kernel.py, which renders every console's geometry.
//
// The screen is LOGICAL landscape (w x h); `rot` turns it onto the panel's
// framebuffer counter-clockwise, the PPA's convention (device/dsi_panel.py's
// rotate_rect): 90 maps (x, y) to (y, w-1-x), 270 maps it to (h-1-y, x).

#ifndef MOY_RECOVERY_H
#define MOY_RECOVERY_H

#include <stddef.h>
#include <stdint.h>

#include "moy_crash.h"

// The screens draw only where the image carries moy_gfx's raster; an image
// without it (the headless Zero) has no panel, and its floor is serial only.
#if __has_include("moy_scr.h")
#define MOY_KERNEL_SCR 1
#include "moy_scr.h"
#else
#define MOY_KERNEL_SCR 0
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int w, h;                        // the logical screen
    int fb_w, fb_h;                  // the framebuffer, panel-native
    int rot;                         // 0, 90, 180 or 270
    int swap;                        // the framebuffer holds RGB565 high byte first
    int touch;                       // a touch-only board: the handheld screen's touch sizes
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
} moy_rview_t;

// The geometry, with no band and no scratch.
void moy_rgeom_init(moy_rgeom_t *g, int w, int h, int rot, int swap);

// The lines the screen says: why it is up (line 0), what the record names,
// the firmware, and with `serial` the words serial takes. `rec` may be NULL.
void moy_recovery_view(moy_rview_t *v, int why, const moy_crash_rec_t *rec,
                       const char *label, const char *hint, int serial);

// What the plain web-console screen says, from the web console's paired URL
// ("http://a.b.c.d[:port]/?pin=..."): the sentence, the address and the pin;
// with no address, that the console is restarting.
void moy_web_screen_view(moy_rview_t *v, const char *url);
// The screen on `pal`'s ground (moy_scr.h's splash palette; NULL: Moy Day).
int moy_web_screen_render(uint16_t *fb, const moy_rgeom_t *g, const moy_rview_t *v,
                          const uint16_t *pal);

// The serial protocol's name for a choice: RETRY, SAFE or REPL
// (tools/kernel_gate.py parses it), whatever the screen says.
const char *moy_recovery_choice_name(int choice);

#ifdef __cplusplus
}
#endif

#endif // MOY_RECOVERY_H
