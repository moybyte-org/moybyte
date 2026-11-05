// moy_sh_floor: the recovery console's screen (docs/os_2026-11/system.md
// section 2): the Terminal's face (moy_sh_face.h) under a head that says why
// the console is down and offers its choices, drawn through the toolkit's
// engine with no VM. The kernel's floor (native/moy_kernel/moy_kernel.c)
// brings the panel up, feeds each frame's input from its C drivers and puts
// the pixels on the glass; the host renders the same C for the goldens
// (tests/test_recovery_floor.py).
//
// THE HEAD: the reason (the floor's first line, moy_recovery_view's), what
// the crash record says, as many of its lines as fit a quarter of the screen,
// the hint, and the choices as a row of buttons -- Try again, Safe start,
// Reboot -- above a rule. A tap (the engine's press and release over a
// button) picks one, and so does the word typed at the prompt: `retry`,
// `safe`, `reboot`, and `repl`, which no button offers. The prompt's words
// are the floor's, never the shell's: the shell's own `reboot` would come
// back to the floor while the counts that sent it there stand.
//
// THE LOOK is the default seed theme's dark mode (moy_theme.h), put in the
// kernel's look (MOY_UI_LOOK_KERNEL), its shape and depth installed and its
// colours placed over MOY64 in the row's palette. The sink is RUN, so every
// text role draws in petme128: the theme's faces are a frozen Python module
// (runtime/toolkit/fontset.py's fonts_data) no C reaches with the VM down.
//
// THE SCREEN is LOGICAL (w x h), drawn whole each frame the floor owes into a
// buffer of its own; the caller turns it onto its panel. Its words are RGB565
// high byte first where `swap` (the S3 panels, the host), canonical where not
// (the P4's DPI scan-out): the palette and the glyph lane (moy_text_wire)
// take that order.
//
// Its memory is the shell's board table's `alloc` (PSRAM on a board): the
// floor, the context, the shape, the pixels, and the face's own.

#ifndef MOY_SH_FLOOR_H
#define MOY_SH_FLOOR_H

#include <stddef.h>
#include <stdint.h>

#include "moy_sh_face.h"

#define MOY_SH_FLOOR_LINES 8            // the head's text lines at most
#define MOY_SH_FLOOR_COLS 64            // a line's bytes, NUL included

// The choices, in the head's order, and the prompt's fourth.
enum {
    MOY_SH_FLOOR_NONE = -1,
    MOY_SH_FLOOR_RETRY = 0,             // the console, as usual
    MOY_SH_FLOOR_SAFE,                  // the console with its settings ignored
    MOY_SH_FLOOR_REBOOT,                // a restart, the floor's counts started over
    MOY_SH_FLOOR_REPL,                  // the VM with main.py skipped (typed only)
    MOY_SH_FLOOR_ROWS = 3               // the buttons
};

typedef struct {
    int32_t w, h;                       // the logical screen
    int32_t desk;                       // the desk's metrics (and no key row)
    int32_t fs;                         // the font scale
    int32_t swap;                       // the words high byte first
    int32_t input;                      // moy_ui.h's MOY_UI_INPUT_* the board reads
} moy_sh_floor_geom_t;

// A frame's input: the byte typed (0 none; a held one is typed once), a held
// key's repeats of `repeat_key`, each direction's steps, the pointer (its
// logical place and moy_ui_api.h's MOY_UI_IN_PTR flags: 1 live, 2 held, 4 the
// press edge) and the ms since the last frame.
typedef struct {
    int32_t key, repeats, repeat_key;
    int32_t left, right, up, down;
    int32_t x, y, ptr;
    int32_t dt;
} moy_sh_floor_in_t;

typedef struct moy_sh_floor moy_sh_floor_t;

// A floor over a face whose session sees `root` (NULL: the board table's
// files tree), its look installed. NULL when memory refuses.
moy_sh_floor_t *moy_sh_floor_new(const moy_sh_floor_geom_t *g, const char *root);
void moy_sh_floor_free(moy_sh_floor_t *fl);

// What the head says: `lines` (the first the reason) and the hint.
void moy_sh_floor_say(moy_sh_floor_t *fl, const char *const *lines, int n, const char *hint);

// One frame: the input taken, and where it moved anything the screen shows,
// the screen drawn. Answers whether the pixels were drawn; *choice gets the
// choice picked this frame (MOY_SH_FLOOR_NONE: none).
int moy_sh_floor_frame(moy_sh_floor_t *fl, const moy_sh_floor_in_t *in, int *choice);

// The pixels, w * h words, as the last frame drew them.
const uint16_t *moy_sh_floor_pixels(const moy_sh_floor_t *fl);
// Button `i`'s box as the last frame drew it: 1, or 0 for none.
int moy_sh_floor_row(const moy_sh_floor_t *fl, int i, int32_t box[4]);
// A choice's word at the prompt and on the serial line (`retry`, `safe`,
// `reboot`, `repl`), and the choice a word names (MOY_SH_FLOOR_NONE: none).
const char *moy_sh_floor_word(int choice);
int moy_sh_floor_choice(const char *word);
// The face under the head.
moy_sh_face_t *moy_sh_floor_face(moy_sh_floor_t *fl);

#endif // MOY_SH_FLOOR_H
