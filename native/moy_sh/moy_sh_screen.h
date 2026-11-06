// moy_sh_screen: the shell's screen face (docs/os_2026-11/system.md section
// 1): a session over the board's host table (moy_sh_board.h) whose terminal
// is a cell grid (moy_term.h), which the console's terminal app draws and
// feeds (runtime/apps/terminal_app.py).
//
// A RUN holds the VM's thread until its commands end, so no frame is drawn
// and no input merged while one goes. Ctrl-C reaches it in C: the face's
// poll kicks the input drivers (as a frame's tail does) and reads the kernel
// input table's latches between merges (moy_input_holds): a key that types
// 0x03 (Ctrl+C on any keyboard) or a press of `start` (the T-Deck's Mic) or
// `b` (a gamepad's B), the cancel key each input names, interrupts the
// running command. A role command waiting in Python asks the same poll
// through moy_sh_interrupted, which is how the serial face's 0x03 reaches it
// too. The poll yields the task now and then, as the serial face's does. A
// run a key interrupted returns once the key is let go (a second and a half
// at most), so the next frame's merge sees no press of it.
//
// A TEXT APP a command opens (moy_sh_app.h) is
// the screen's while it is in front; the face draws it and feeds it keys.
//
// Its memory is the board table's `alloc` (PSRAM): the face, the session's
// arena and the grid, all taken at moy_sh_screen_new, the first open, and an
// app's while it is open.

#ifndef MOY_SH_SCREEN_H
#define MOY_SH_SCREEN_H

#include <stddef.h>

#include "moy_sh.h"
#include "moy_term.h"

#define MOY_SH_SCREEN_LINES 200         // the scrollback's lines, past the screen's
#define MOY_SH_SCREEN_ROOT 256          // a root path's bytes

typedef struct moy_sh_screen moy_sh_screen_t;

// A face of `cols` by `rows` in its home folder "/"; `root` names the files
// tree off a board (NULL: the board table's). NULL when memory refuses.
moy_sh_screen_t *moy_sh_screen_new(int cols, int rows, const char *root);
void moy_sh_screen_free(moy_sh_screen_t *f);

// What was typed (a line, its newline included), as moy_sh_run.
int moy_sh_screen_run(moy_sh_screen_t *f, const char *text, size_t n);
// The face's own Ctrl-C: the next poll answers it.
void moy_sh_screen_interrupt(moy_sh_screen_t *f);

moy_sh_t *moy_sh_screen_session(moy_sh_screen_t *f);
moy_term_t *moy_sh_screen_term(moy_sh_screen_t *f);

// The text app in front (moy_sh_app.h), which a command of the session
// opened (NULL: the shell's prompt), and its end. The face says whether its
// input types control keys, which an app's own help names.
struct moy_sh_app;
struct moy_sh_app *moy_sh_screen_app(const moy_sh_screen_t *f);
void moy_sh_screen_end_app(moy_sh_screen_t *f);
void moy_sh_screen_set_ctrl(moy_sh_screen_t *f, int ctrl);

#endif // MOY_SH_SCREEN_H
