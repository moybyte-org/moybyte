// moy_sh_screen: the shell's screen face (docs/os_2026-11/system.md section
// 1): a session over the board's host table (moy_sh_board.h) whose terminal
// is a cell grid (moy_term.h), which the console's terminal app draws and
// feeds (runtime/apps/terminal_app.py).
//
// A RUN is the session's runner's (moy_sh_board.h): it holds the console's
// task while it computes, and parks where it waits (`sleep`, a role command
// between its steps), when moy_sh_screen_run answers MOY_SH_BUSY and the
// frames go on, the run's output reaching the grid as it comes; the face
// keeps the session's prompt down and lets nothing else enter the session
// until moy_sh_screen_ended gives the run's answer. Ctrl-C reaches a run in
// C: the face's own (moy_sh_screen_interrupt, what the Terminal's keys send
// while a run is parked), and, while one holds the task, the face's poll
// kicks the input drivers (as a frame's tail does) and reads the kernel input
// table's latches between merges (moy_input_holds): a key that types 0x03
// (Ctrl+C on any keyboard) or a press of `start` (the T-Deck's Mic) or `b`
// (a gamepad's B), the cancel key each input names, interrupts the running
// command. A role command waiting in Python asks the same poll through
// moy_sh_interrupted, which is how the serial face's 0x03 reaches it too.
// The poll yields the task now and then, as the serial face's does. A run a
// key interrupted while it held the task returns once the key is let go (a
// second and a half at most), so the next frame's merge sees no press of it.
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

// A face of `cols` by `rows` in its home (moy_sh_board_go_home); `root` names the files
// tree off a board (NULL: the board table's). NULL when memory refuses.
moy_sh_screen_t *moy_sh_screen_new(int cols, int rows, const char *root);
void moy_sh_screen_free(moy_sh_screen_t *f);

// What was typed (a line, its newline included), as moy_sh_run, or
// MOY_SH_BUSY while its run is parked. An app's line runs to its end.
int moy_sh_screen_run(moy_sh_screen_t *f, const char *text, size_t n);
// Whether a run is parked; and 1 once, when a parked run has ended, with its
// answer in *r.
int moy_sh_screen_busy(const moy_sh_screen_t *f);
// Every run on the caller's task to its end, never parked.
void moy_sh_screen_inline(moy_sh_screen_t *f);
int moy_sh_screen_ended(moy_sh_screen_t *f, int *r);
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

// What the face asks of the shell behind the screen: the prompt, the last
// status, a held `more` dropped, Tab's names (moy_sh_cands); and the memory
// a face takes (the board table's). A link's screen (moy_sh_screen_link.c)
// has no shell: no prompt, status 0, no names.
size_t moy_sh_screen_prompt(moy_sh_screen_t *f, int more, char *out, size_t cap);
int moy_sh_screen_status(moy_sh_screen_t *f);
void moy_sh_screen_cancel(moy_sh_screen_t *f);
int moy_sh_screen_cands(moy_sh_screen_t *f, const char *line, size_t col, moy_sh_cands_t *a);
void moy_sh_screen_heap(void *(**alloc)(void *ctx, size_t n), void (**free_)(void *ctx, void *p));

#endif // MOY_SH_SCREEN_H
