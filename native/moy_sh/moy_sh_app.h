// moy_sh_app: the shell's text apps (docs/os_2026-11/system.md section 1,
// "full-screen text apps"): `edit` (moy_sh_edit.c) and `top` (moy_sh_top.c),
// each one C implementation that every face
// hosts the same way -- the Terminal's (moy_sh_screen.c under moy_sh_face.c),
// the recovery floor's (the same face) and the serial face (moy_sh_board.c).
//
// A COMMAND OPENS ONE. `edit` and `top` ask the session's host to put the app in front (the host table's
// `app`); the app opens inside that run (reading its file, saying on stderr
// why it would not) and takes the face when the run's line ends. A face
// with no screen answers MOY_SH_NOT_FOUND and the command says "needs a
// screen". While an app is in front the shell's prompt is the app's.
//
// A FULL-SCREEN APP takes the whole grid: a screen of its own (a moy_term of
// the face's size with no scrollback, so the shell's grid is untouched and is
// back, as it was, when the app ends: the alternate screen and its restore),
// which it writes as any VT100 app writes its stdout, cursor moves and SGR
// inks. It reads KEYS as a terminal sends them (moy_sh_app_key decodes them):
// the bytes typed, control keys as themselves, the arrows, Home, End, PgUp,
// PgDn and Delete as xterm's escapes, a lone ESC as Esc. A face turns its
// input into those bytes: a keyboard's keys, the trackball's and a d-pad's
// steps as the arrows, a key row's buttons as the bytes each names. A RESIZE
// is the app's screen resized and drawn whole. A tick (`tick`) comes with
// every frame and every serial line, for an app that shows a clock (top).
//
// A LINE APP keeps the face's prompt line, its editing and its history: the
// prompt is the app's, and Enter hands the line to the app instead of the
// shell.
//
// ITS MEMORY is the host table's `alloc` (PSRAM on a board): the front, the
// app's record and its screen at the open, given back at the end; a key or a
// tick allocates nothing but what the app itself asks (an edit's growth, a
// save's copy, top's row fetch).

#ifndef MOY_SH_APP_H
#define MOY_SH_APP_H

#include <stddef.h>
#include <stdint.h>

#include "moy_sh.h"
#include "moy_term.h"

#define MOY_SH_APP_GO 0                 // the app goes on
#define MOY_SH_APP_END 1                // the app ended: the face takes the shell back

// moy_sh_app_key's names for what is not one byte.
enum {
    MOY_SH_KEY_UP = 0x100, MOY_SH_KEY_DOWN, MOY_SH_KEY_RIGHT, MOY_SH_KEY_LEFT,
    MOY_SH_KEY_HOME, MOY_SH_KEY_END, MOY_SH_KEY_PGUP, MOY_SH_KEY_PGDN, MOY_SH_KEY_DEL,
};

typedef struct moy_sh_app moy_sh_app_t;

typedef struct {
    const char *name;                   // the command's
    size_t state;                       // its record's bytes, zeroed at the open
    int full;                           // full-screen (1) or a line app (0)
    int text;                           // types text: a key row offers the shell's symbols
    const char *const *keys;            // its key row: label, bytes, ..., NULL
    // Inside the opening run: 0, or the exit status it ends with (having said
    // why on stderr).
    int (*open)(moy_sh_app_t *a, void *st, int argc, char **argv);
    // Keys as a terminal sends them: MOY_SH_APP_GO or MOY_SH_APP_END.
    int (*keys_in)(moy_sh_app_t *a, void *st, const char *p, size_t n);
    // A full-screen app's screen drawn whole (at the open and after a resize).
    void (*draw)(moy_sh_app_t *a, void *st);
    // A frame's tick, `ms` the monotonic clock: whether it drew (may be NULL).
    int (*tick)(moy_sh_app_t *a, void *st, uint32_t ms);
    // A line app's line, and the prompt it shows.
    int (*line)(moy_sh_app_t *a, void *st, const char *text);
    const char *(*prompt)(moy_sh_app_t *a, void *st);
    // The end, either way (may be NULL).
    void (*close)(moy_sh_app_t *a, void *st);
} moy_sh_app_ops_t;

// A line run in the face's session, as the face runs a typed one.
typedef int (*moy_sh_app_run_fn)(void *ctx, const char *text, size_t n);

// The app argv[0] names, or NULL.
const moy_sh_app_ops_t *moy_sh_app_find(const char *name);

// Open the app argv[0] names in front of a face of `cols` by `rows`, from
// inside the run of the command that names it: NULL with *status the exit
// code where it would not open (or memory refused). `run` runs a line app's
// lines; `ctrl` says the input types control keys (a keyboard's Ctrl).
moy_sh_app_t *moy_sh_app_open(moy_sh_t *s, int cols, int rows, int ctrl,
                              moy_sh_app_run_fn run, void *ctx, int argc, char **argv,
                              int *status);
void moy_sh_app_close(moy_sh_app_t *a);

int moy_sh_app_keys(moy_sh_app_t *a, const char *p, size_t n);
int moy_sh_app_line(moy_sh_app_t *a, const char *text);
int moy_sh_app_tick(moy_sh_app_t *a, uint32_t ms);
void moy_sh_app_resize(moy_sh_app_t *a, int cols, int rows);
void moy_sh_app_set_ctrl(moy_sh_app_t *a, int ctrl);

const moy_sh_app_ops_t *moy_sh_app_ops(const moy_sh_app_t *a);
// A full-screen app's screen (NULL for a line app).
moy_term_t *moy_sh_app_term(const moy_sh_app_t *a);
const char *moy_sh_app_prompt(moy_sh_app_t *a);

// -- for an app's own code -----------------------------------------------------------

moy_sh_t *moy_sh_app_session(const moy_sh_app_t *a);
void moy_sh_app_size(const moy_sh_app_t *a, int *cols, int *rows);
int moy_sh_app_ctrl(const moy_sh_app_t *a);
void moy_sh_app_write(moy_sh_app_t *a, const char *s);
void moy_sh_app_writen(moy_sh_app_t *a, const char *p, size_t n);
// `s` (no escapes) cut to `cols` characters, so a row never wraps onto the next.
void moy_sh_app_clip(moy_sh_app_t *a, const char *s, int cols);
int moy_sh_app_run(moy_sh_app_t *a, const char *text, size_t n);
void *moy_sh_app_alloc(moy_sh_app_t *a, size_t n);
void moy_sh_app_free(moy_sh_app_t *a, void *p);
// The next key of `*p` before `end` (a byte, or a MOY_SH_KEY_*), *p moved past it.
int moy_sh_app_key(const char **p, const char *end);
// The bytes a terminal sends for a key name ("up", "down", "left", "right",
// "home", "end", "pgup", "pgdn", "del"): NULL for none.
const char *moy_sh_app_key_bytes(const char *name);

#endif // MOY_SH_APP_H
