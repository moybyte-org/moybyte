// A text view: a text area in visual rows (docs/scroll_and_text_2026-10.md §5,
// docs/ui_api_2026-10.md's `ui.text_view`). One body for every text area
// that draws through the public UI API's verb (moy_ui_api_text_view: Python,
// Lua and a compiled app): the shell's Code tab and theme Config, the desk's
// made code, the text console's log, and an app's.
//
// A view is a rect R of rows `lh` pixels apart, each row a span of one line
// of text: a code line's whole length (scrolled across by `left` columns), or
// a wrapped paragraph's row. A row draws its ground, the current mark, a
// gutter number, an error's underline, the selection, the find matches, the
// text in colour runs, the error's words after it, a fade where the line runs
// past the view, and the caret -- in that order, each inside the row's band,
// so a row draws alone.
//
// The colour runs are a syntax's (Python, Lua, JSON: each byte's class,
// adjacent bytes of one class one run) or the text's own SGR escapes
// (`ESC [ n m`: a terminal's colours, which take no column), each run in a
// TONE the caller resolves from the theme (MOY_UI_TT_*).
//
// THE RECORD. A view keeps, in a moy_ui_text_t the caller owns, what it drew
// last: the key it drew under (its rect, metrics, colours, scroll across,
// gutter, the caller's look) and each row's gen, a hash of what the row draws
// (its text, its line's number where a gutter shows it, and its marks: the
// current row, the caret's column, the selection's span, the find matches,
// an error). moy_ui_text_plan compares this frame's rows with it before
// anything draws: a frame whose key moved, or that scrolled a view or more,
// draws whole; otherwise R's pixels move by the scroll (the caller's
// shift_last) and the FLOOR -- the rows whose gen moved, the band the move
// uncovered, and the rows an overlay covered last frame -- is all the frame
// draws inside R. moy_ui_text_paint draws the floor (or every row) and
// moy_ui_text_note records the frame.

#ifndef MOY_UI_TEXT_H
#define MOY_UI_TEXT_H

#include <stddef.h>
#include <stdint.h>

#include "moy_ui.h"

#define MOY_UI_TEXT_ROWS 96         // the rows a view records and one draw lays out
#define MOY_UI_TEXT_BREAKS 64       // the rows one wrapped line keeps its breaks for
#define MOY_UI_TEXT_MAGIC 0x54565731u

// The tones a run draws in: a syntax class's, or an SGR colour's.
enum {
    MOY_UI_TT_TEXT = 0, MOY_UI_TT_KEYWORD, MOY_UI_TT_STRING, MOY_UI_TT_NUMBER,
    MOY_UI_TT_COMMENT, MOY_UI_TT_BUILTIN, MOY_UI_TT_VALUE, MOY_UI_TT_DANGER,
    MOY_UI_TT_DIM, MOY_UI_TT_N
};

// What colours a row's text: none (one tone), a syntax, or SGR escapes.
enum {
    MOY_UI_SYN_NONE = 0, MOY_UI_SYN_PYTHON, MOY_UI_SYN_LUA, MOY_UI_SYN_JSON, MOY_UI_SYN_SGR
};

// The view, as words (the Python face's array('i'), the verb's own): where
// and how it draws this frame.
enum {
    MOY_UI_TV_X = 0, MOY_UI_TV_Y, MOY_UI_TV_W, MOY_UI_TV_H,  // R: the rows' rect
    MOY_UI_TV_LH,           // the rows' pitch
    MOY_UI_TV_CELL,         // a column's width
    MOY_UI_TV_TEXT_H,       // the glyph box: the caret's, a selection's height
    MOY_UI_TV_FS,           // the font scale its marks are measured in
    MOY_UI_TV_FONT,         // the face its text prints in (0 petme128)
    MOY_UI_TV_TX,           // where the gutter (else the text) starts
    MOY_UI_TV_MARK,         // the current mark's left; -1 no current mark
    MOY_UI_TV_GUTTER,       // the gutter's width in columns, its number's and a space; 0 none
    MOY_UI_TV_COLS,         // the columns the view shows
    MOY_UI_TV_TOP,          // the first row shown
    MOY_UI_TV_OFF,          // ...and the pixels of it scrolled out above R
    MOY_UI_TV_LEFT,         // the first column shown
    MOY_UI_TV_CARET_ROW, MOY_UI_TV_CARET_COL,  // the caret's line and column; row -1 none
    MOY_UI_TV_SEL_R0, MOY_UI_TV_SEL_C0, MOY_UI_TV_SEL_R1, MOY_UI_TV_SEL_C1,  // r0 -1 none
    MOY_UI_TV_ERR_ROW,      // the line an error stands on; -1 none
    MOY_UI_TV_FIND_CI,      // the find query matches across case
    MOY_UI_TV_SYNTAX,       // MOY_UI_SYN_*
    MOY_UI_TV_FLAGS,        // MOY_UI_TVF_*
    MOY_UI_TV_BG, MOY_UI_TV_CARET, MOY_UI_TV_SEL, MOY_UI_TV_GUT, MOY_UI_TV_GUT_CUR,
    MOY_UI_TV_VALUE, MOY_UI_TV_ERR,     // colour indices
    MOY_UI_TV_TONE0,        // MOY_UI_TT_N colour indices from here
    MOY_UI_TV_KEY = MOY_UI_TV_TONE0 + MOY_UI_TT_N,     // the caller's part of the key
    MOY_UI_TV_OVER_Y0, MOY_UI_TV_OVER_Y1,   // what an overlay covered last frame (y, R's frame)
    MOY_UI_TV_LINES,        // the lines in the text
    MOY_UI_TV_NUM0,         // what the gutter numbers the line `0` the rows name (0: 1)
    // the plan's answers (moy_ui_text_plan)
    MOY_UI_TV_DY,           // the move of R's pixels
    MOY_UI_TV_FLOOR,        // the pixel rows the floor draws (R's height: whole)
    MOY_UI_TV_WHOLE,        // the frame draws every row
    MOY_UI_TV_DRAWN,        // the rows the last draw drew
    MOY_UI_TV_FLOOR_Y0, MOY_UI_TV_FLOOR_Y1,  // the band the floor's rows span (R's on a whole frame)
    MOY_UI_TV_LEN
};

enum {
    MOY_UI_TVF_FADE = 1,    // a line past the view fades out at its right
    MOY_UI_TVF_GROUND = 2,  // each row fills its ground (else the caller filled R)
    MOY_UI_TVF_CLIP = 4,    // the rows draw clipped to R (a row R cuts)
};

// One visual row: a span of a line. `p`/`n` the line's bytes (UTF-8), the
// row its bytes [a, b) and the columns from `ca` (a wrapped paragraph's row;
// 0, n and 0 for a whole line), `line` its line's number from 0.
typedef struct {
    const uint8_t *p;
    uint32_t n, a, b;
    int32_t ca, line;
} moy_ui_trow_t;

typedef struct {
    uint32_t magic;
    uint32_t key;               // the key the record's rows drew under (0: none)
    int32_t top, off;           // where they lay
    int16_t rows;               // how many it holds
    int16_t n_floor;            // this frame's floor: how many rows it holds
    uint32_t gen[MOY_UI_TEXT_ROWS], gen2[MOY_UI_TEXT_ROWS];
    // this frame's plan: its key and rows' gens, the floor's rows
    uint32_t want;
    int32_t want_top, want_off;
    int16_t want_rows;
    uint8_t whole;
    uint8_t floor[(MOY_UI_TEXT_ROWS + 7) / 8];
    uint32_t ngen[MOY_UI_TEXT_ROWS], ngen2[MOY_UI_TEXT_ROWS];
} moy_ui_text_t;

// A view's record as the verb keeps it (moy_ui_api_text_view), by its owner
// (the context it draws in) and its id: the rows' record, the wrapped rows'
// count against the caller's gen, what an overlay covered, and where its rows
// lay last pass -- R, the pitch, a column's width, where the text starts and
// each row's line and columns -- which a hit test reads.
#define MOY_UI_TEXT_VIEWS 4
typedef struct {
    const void *owner;
    uint32_t id, used;
    int32_t gen, cols, n, total, wrap;
    int32_t at_cols, sgr;           // the columns a row holds and the syntax's SGR, last pass
    int32_t over0, over1;
    int32_t x, y, w, h, lh, cell, tx, left, off, rows;
    int32_t line[MOY_UI_TEXT_ROWS], ca[MOY_UI_TEXT_ROWS], cb[MOY_UI_TEXT_ROWS];
    moy_ui_text_t t;
} moy_ui_tview_t;
// Owner `owner`'s view `id`, or NULL where it keeps none; with `make`, the
// least recently drawn record taken for it where it has none (forgotten:
// its next frame draws whole). NULL where no memory could be had.
moy_ui_tview_t *moy_ui_text_view_of(const void *owner, uint32_t id, int make);
// Every view `owner` keeps, forgotten.
void moy_ui_text_views_drop(const void *owner);
// The rows one draw lays out (MOY_UI_TEXT_ROWS) and a wrapped line's breaks
// (MOY_UI_TEXT_BREAKS), shared by every face, one view drawing at a time:
// taken at first use, NULL where no memory could be had.
moy_ui_trow_t *moy_ui_text_scratch(void);
uint32_t *moy_ui_text_breaks(void);


// Forget the record: the next frame draws whole.
void moy_ui_text_reset(moy_ui_text_t *t);

// The rows R shows from v[TOP] with v[OFF] scrolled out: how many rows the
// view draws, a part row at the foot counted.
int moy_ui_text_rows(const int32_t *v);

// Plan this frame against the record: rows[0..n) the rows R shows (n is
// moy_ui_text_rows's, a row past the text's end with p NULL), `err` the
// error's words, `find` the find query (n 0: none). Writes v[DY] (R's pixels
// move by it), v[FLOOR] (the floor's height in pixels) and v[WHOLE]; answers
// v[WHOLE] == 0 (the frame may move R's pixels and draw its floor alone).
int moy_ui_text_plan(moy_ui_text_t *t, int32_t *v, const moy_ui_trow_t *rows, int n,
                     moy_ui_span_t err, moy_ui_span_t find);

// Draw the rows the plan named into sink `s` -- every row where `whole`, `t`
// NULL or the plan said whole, those the clip meets -- answering the rows
// drawn (also v[DRAWN]); and record the frame.
int moy_ui_text_paint(moy_ui_text_t *t, const moy_ui_sink_t *s, int32_t *v,
                      const moy_ui_trow_t *rows, int n, moy_ui_span_t err, moy_ui_span_t find,
                      int whole);
void moy_ui_text_note(moy_ui_text_t *t, int32_t *v, const moy_ui_trow_t *rows, int n,
                      moy_ui_span_t err, moy_ui_span_t find);

// The rows of a line wrapped at `cols` columns (a break after the last space
// that fits, else at the column): each row's byte start into starts[] (up to
// cap), the count answered. SGR escapes take no column.
int moy_ui_text_wrap(const uint8_t *p, size_t n, int cols, int sgr, uint32_t *starts,
                     int cap);

// The words a syntax colours: `words` NUL-separated, each in `tone` under
// `syntax` (Python's or Lua's); a word given again takes its new tone.
// MOY_UI_NOMEM where the table is full. _reset empties it.
int moy_ui_text_words(int syntax, const uint8_t *words, size_t n, int tone);
void moy_ui_text_words_reset(void);

// A line's colour runs under `syntax`, each as four words into out (up to
// cap words): its bytes' start and end, the column it starts at and its
// tone. The runs, counted past cap.
int moy_ui_text_runs(const uint8_t *p, size_t n, int syntax, int32_t *out, int cap);

// A line's columns: its characters, SGR escapes taking none where `sgr`.
int moy_ui_text_cols(const uint8_t *p, size_t n, int sgr);

#endif
