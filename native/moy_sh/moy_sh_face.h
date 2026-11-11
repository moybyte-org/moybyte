// moy_sh_face: the Terminal's face (docs/os_2026-11/system.md section 1): a
// screen session (moy_sh_screen.h) with the prompt line under its grid, the
// history, completion, the key rows and their input, drawn through the
// public UI API's engine (native/moy_ui/moy_ui_api.h). It runs whether a VM
// runs or not: the console's Terminal app (runtime/apps/terminal_app.py)
// hands it its frame's context, sink and input, and the kernel's recovery
// console (native/moy_kernel) its own.
//
// THE PROMPT is one row under the grid's log, a text view of the prompt and
// what is typed, its caret the view's. Enter echoes the line into the grid
// (the prompt dim, the text in cyan) and runs it on the session's runner
// (moy_sh_screen.h): a run holds the caller's thread while it computes, and
// where it waits it parks, the frames going on and its output streaming
// into the grid. While a run is parked the prompt row shows no prompt, Enter
// and Tab do nothing, Ctrl-C and the board's cancel stop the run, and each
// frame is owed (its caret blinks); the next call after the run ended
// brings the prompt back. Tab completes the word left of the caret: a command's name from the
// shell's built-ins (moy_sh_complete), a path's from its folder; with several
// candidates and no longer common stem it lists them in the grid and offers
// the first MOY_SH_FACE_CANDS on the key row. Left and right move the caret,
// up and down walk the lines run before.
//
// THE KEY ROW is what the input in use cannot type (moy_ui_input,
// moy_ui_hint_input). A desk has a keyboard and draws none. The T-Deck's
// holds Tab and the shell's symbols its keyboard has no key for
// (moy_sh_face_tdeck_row, from moy_kbd.c's matrix); its trackball moves the
// caret and walks the history, Enter runs, Mic stops, and down past the
// newest line enters the row: the ball moves along it, Enter presses the key
// and up, or a typed key, goes back to the prompt. A gamepad's and a
// finger's hold Tab, Ctrl-C, the arrows and Run, and under touch a row of
// the shell's symbols sits under them.
//
// A TEXT APP in front (moy_sh_app.h) is drawn and
// fed by the same face. A full-screen one takes the whole page, the prompt
// row and its hints gone: the log shows the app's own screen with its cursor
// as the caret, sized to the page (a resize is the app's), and every key
// goes to it as a terminal sends it -- a typed key its byte, Enter a CR,
// Delete and the arrows (the trackball's and a d-pad's steps) xterm's
// escapes. The key row holds the app's own keys (top's Kill and Quit),
// then on the T-Deck Tab and its missing
// symbols where the app types text, on a gamepad and under touch the arrows;
// the T-Deck enters its row with Mic (the board's cancel,
// moy_sh_face_cancel) and leaves it with Mic or up, and a keyboard's Esc is
// the app's. A line app keeps the prompt row, its prompt the app's: Enter
// hands it the line, Tab types four spaces, Ctrl-D on an empty line or the
// cancel key (Mic) ends it. A frame's input brings the app its tick.
//
// Its memory is the board table's `alloc` (PSRAM), taken at moy_sh_face_new
// and at the session's first need, nothing per frame.

#ifndef MOY_SH_FACE_H
#define MOY_SH_FACE_H

#include <stddef.h>
#include <stdint.h>

#include "moy_sh_screen.h"
#include "moy_ui_api.h"

#define MOY_SH_FACE_LINE 256            // the prompt line's bytes
#define MOY_SH_FACE_HISTORY 64          // the lines run before it keeps
#define MOY_SH_FACE_CANDS 24            // the candidates a key row offers
#define MOY_SH_FACE_CAND 64             // a candidate's bytes
#define MOY_SH_FACE_SYMBOLS "|><&;$~/-*\"'\\{}[]()=!#?:"   // the shell's symbols
#define MOY_SH_FACE_TOUCH_SYMBOLS "|><&;$~/-*\"'"          // the touch row's

// The key row a frame draws.
enum { MOY_SH_ROW_NONE = 0, MOY_SH_ROW_TDECK, MOY_SH_ROW_PAD, MOY_SH_ROW_TOUCH };

// moy_sh_face_input's answer.
#define MOY_SH_FACE_HIT 1               // what the page shows changed
#define MOY_SH_FACE_KEYS 2              // the page's stops take this frame's keys:
                                        // the host's key resolution is owed
// moy_sh_face_draw's answer.
#define MOY_SH_FACE_AGAIN 1             // the pass changed what it drew: the next frame is owed
#define MOY_SH_FACE_DAMAGE 2            // a key of a row changed the page: the next frame draws it

typedef struct moy_sh_face moy_sh_face_t;

// A frame's page: the content rect under the host's bar, the font scale,
// whether it is a desk's, and the hints in place of the face's own while the
// keys are on the shell's bar (NUL-separated items; NULL: the face's).
typedef struct {
    int32_t cx, cy, cw, ch;
    int32_t fs;
    int32_t desk;
    const uint8_t *bar;
    uint32_t bar_n;
} moy_sh_face_frame_t;

// A face whose session sees `root` (NULL: the board table's files tree).
// NULL when memory refuses.
moy_sh_face_t *moy_sh_face_new(const char *root);
void moy_sh_face_free(moy_sh_face_t *f);

// The session, made at its first need: NULL where memory refuses.
moy_sh_screen_t *moy_sh_face_screen(moy_sh_face_t *f);
// The session given back, its arena and grid with it: the next need makes
// a new one. The line, the history and the row stay.
void moy_sh_face_close_session(moy_sh_face_t *f);
// The face shown again: the prompt, not the key row, has the keys.
void moy_sh_face_open(moy_sh_face_t *f);

// What a key, a key of the row and a frame's input do.
// One typed key: Enter runs the line, Tab completes, Ctrl-C (3) drops the
// line (or is swallowed: the one that stopped a run), anything the line
// editor takes edits it. Whether it was taken.
int moy_sh_face_key(moy_sh_face_t *f, int32_t code);
// A key of the key row by its name ("Tab", "^C", "left", "right", "up",
// "down", "Run", or a symbol to type).
void moy_sh_face_press(moy_sh_face_t *f, const char *name);
// A frame's keys: the key typed (0 none; a held one is typed once), a held
// key's repeats of `repeat_key`, and the steps of each direction (a press
// edge and its repeats, the trackball's). MOY_SH_FACE_* bits.
int moy_sh_face_input(moy_sh_face_t *f, int desk, int32_t key, int32_t repeats,
                      int32_t repeat_key, int32_t left, int32_t right, int32_t up,
                      int32_t down);

// The board's cancel key (the T-Deck's Mic, a keyboard's Esc where the host
// keeps it) for the text app in front: whether one took it. With none in
// front it is the host's (the console's key).
int moy_sh_face_cancel(moy_sh_face_t *f, int desk);
// The text app in front: its name (NULL: the shell's prompt), and whether
// it is a full-screen one.
const char *moy_sh_face_app(const moy_sh_face_t *f);
int moy_sh_face_full(const moy_sh_face_t *f);

// The key row the input in use gets on a desk or not (MOY_SH_ROW_*).
int moy_sh_face_row(int desk);
// Whether this frame's keys are another surface's for the engine's stops:
// the prompt's, not a row's (a frame begun with MOY_UI_ELSEWHERE).
int moy_sh_face_elsewhere(const moy_sh_face_t *f, int desk);

// The frame's pass, begun by the caller (moy_ui_api_begin or its host's)
// over `s`: the page, the hints, the key rows, the log and the prompt, the
// grid resized to the log. MOY_SH_FACE_* bits.
int moy_sh_face_draw(moy_sh_face_t *f, moy_ui_ctx_t *c, const moy_ui_sink_t *s,
                     const moy_sh_face_frame_t *fr);

// The line and its caret (a column in bytes), and setting them.
const char *moy_sh_face_line(const moy_sh_face_t *f, int32_t *col);
void moy_sh_face_set_line(moy_sh_face_t *f, const char *text, int32_t col);
// Enter and Tab on the line as it stands.
void moy_sh_face_submit(moy_sh_face_t *f);
// Whether a run of the session is parked (one that ended is settled first).
int moy_sh_face_busy(moy_sh_face_t *f);
// Every run on the caller's task to its end (the recovery floor, whose loop
// runs no frames' jobs): set before the first open.
void moy_sh_face_inline(moy_sh_face_t *f);
void moy_sh_face_complete(moy_sh_face_t *f);
// Up (-1) or down (+1) through the lines run before; candidate `i` of the
// key row picked (the rest of its word typed).
void moy_sh_face_recall(moy_sh_face_t *f, int d);
void moy_sh_face_pick(moy_sh_face_t *f, int i);
// Whether the command typed goes on (the continuation prompt), and the
// prompt the line shows into `out` (its length).
int moy_sh_face_more(const moy_sh_face_t *f);
size_t moy_sh_face_prompt(moy_sh_face_t *f, char *out, size_t cap);
// The grid's lines taken again where its generation moved since the last
// take: whether they were (a frame takes them itself).
int moy_sh_face_take(moy_sh_face_t *f);
// The key row has the keys (T-Deck), and its candidates.
int moy_sh_face_in_row(const moy_sh_face_t *f);
void moy_sh_face_set_in_row(moy_sh_face_t *f, int on);
int moy_sh_face_cands(const moy_sh_face_t *f);
const char *moy_sh_face_cand(const moy_sh_face_t *f, int i);
void moy_sh_face_clear_cands(moy_sh_face_t *f);
// The history's lines, oldest first, and the step the walk stands at.
int moy_sh_face_history(const moy_sh_face_t *f, int i, const char **line);
// The grid's columns and rows as the last frame laid them.
void moy_sh_face_size(const moy_sh_face_t *f, int32_t *cols, int32_t *rows);
// The log's lines as the last frame took them: the count, and line `i`'s
// SGR text into `out` (its length).
int32_t moy_sh_face_lines(const moy_sh_face_t *f);
size_t moy_sh_face_log_line(moy_sh_face_t *f, int32_t i, char *out, size_t cap);

// The T-Deck's row after Tab: the shell's symbols its keyboard cannot type,
// in MOY_SH_FACE_SYMBOLS' order, into `out` (NUL-terminated); their count.
int moy_sh_face_tdeck_row(char *out, size_t cap);

#endif // MOY_SH_FACE_H
