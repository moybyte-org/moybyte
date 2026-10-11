// moy_ui: the widget toolkit's bodies in C, with style and strings as data
// (docs/kernel_toolkit_2026-10.md). This file is the style resolution: the
// kinds, the states, each kind's (field, ink, edge) in each state resolved
// against a theme's token row, each kind's metric slots, and the skin by name.
// The data is style.json's, generated into moy_ui_tables.c
// (tools/gen_ui_tables.py); nothing here holds a colour or a pad.
//
// A LOOK is the style resolved against one token row: per kind and state,
// three palette indices, -1 for "paint nothing". A look flattens lazily, one
// kind at a time, and again when the skin moves. There are MOY_UI_LOOKS of
// them, allocated once in PSRAM where a board has it: four for the Python
// face's ring of theme dicts (the live theme, and the throwaway or preview
// dicts a surface passes beside it), and one for a compiled run, so no
// flatten is shared between the VM's task and a run's session thread.
//
// The fallbacks are the Python toolkit's, never errors: an unknown kind
// resolves as `row`, a state the kind does not name as its REST, a token the
// theme does not hold through its alias chain to its fallback, or to -1.

#ifndef MOY_UI_H
#define MOY_UI_H

#include <stddef.h>
#include <stdint.h>

#include "moy_text.h"
#include "moy_ui_tables.h"

enum { MOY_UI_OK = 0, MOY_UI_BAD = 1, MOY_UI_STALE = 2, MOY_UI_NOMEM = 3 };

#define MOY_UI_ABSENT INT32_MIN     // a token the theme does not hold
#define MOY_UI_LOOKS 5
#define MOY_UI_LOOK_RUN 4           // the compiled run's look

// A kind's id by name: an unknown name is row's. kind_known: -1 instead.
int moy_ui_kind(const char *name, size_t n);
int moy_ui_kind_known(const char *name, size_t n);
// `button(kind=...)`'s names -> the kind it draws as; an unknown one is "button".
int moy_ui_button_kind(const char *name, size_t n);
// A state's id by name, or -1.
int moy_ui_state(const char *name, size_t n);

// The skin: install one by name (an unknown name installs the default) and
// answer the index installed; the active one; the generation, which moves on
// every install.
int moy_ui_skin_use(const char *name, size_t n);
int moy_ui_skin(void);
uint32_t moy_ui_gen(void);

// Point `look` at a token row (MOY_UI_TOKENS values, MOY_UI_ABSENT where the
// theme is silent): its flatten starts over.
int moy_ui_look_set(int look, const int32_t tokens[MOY_UI_TOKENS]);
// (field, ink, edge) of `kind` in `state` under `look`, -1 for nothing.
int moy_ui_colors(int look, int kind, int state, int16_t out[3]);
// The active skin's metric slots for `kind` (its count in *n, 0: none).
const int16_t *moy_ui_metrics(int kind, int *n);

// -- glyphs (moy_ui_glyph.c) ----------------------------------------------------

// A glyph's id by name, or -1 (draws nothing: the caller's label stays).
int moy_ui_glyph_id(const char *name, size_t n);
// Draw glyph `g` centred in the rect (x, y, w, h), each glyph pixel a
// `scale` square, into a target. Returns the spans filled (a span is one
// fill, as the canvas counts them).
int moy_ui_glyph_draw(const moy_text_target_t *t, int g, int x, int y, int w, int h,
                      uint16_t col, int scale);

// -- the ROW sink (moy_ui.c) ------------------------------------------------------

// Draw glyph `g` into canvas row `canvas` (a CANVAS handle, checked as any
// handle is): its camera, clip and size from the row's state, `ci` through
// the row's palette, the row's fill counter bumped per span. MOY_UI_STALE
// when the row is gone.
int moy_ui_row_glyph(uint32_t canvas, int g, int x, int y, int w, int h, int ci, int scale);

// -- the primitives (moy_ui_draw.c) ----------------------------------------------
//
// Each takes a sink, a box, a kind id and the widget's state flags, then its
// content, and draws what the Python body it replaced drew, op for op
// (tests/ui_twin.py is that body). Text is a UTF-8 span; a glyph is a glyph id
// or -1; a colour override is an index, -1 for "paint nothing", or
// MOY_UI_FROM_STYLE; a geometry knob left to the style is MOY_UI_DEFAULT.

typedef struct { int16_t x, y, w, h; } moy_ui_box_t;
typedef struct { const uint8_t *p; uint16_t n; } moy_ui_span_t;

// A sink: where a primitive draws. ROW: a canvas row of the glass, named by
// its CANVAS handle (moy_ui_sink_row checks it); `fs` is the row's font
// scale, the scale its text draws at; `look` the look its colours resolve in.
enum { MOY_UI_SINK_ROW = 1, MOY_UI_SINK_RUN = 2 };
typedef struct {
    uint8_t kind;
    uint8_t fs;
    uint8_t look;
    void *dst;
} moy_ui_sink_t;
int moy_ui_sink_row(moy_ui_sink_t *s, uint32_t canvas, int look);
// Every primitive counts its entry: the gate's "one crossing per widget".
void moy_ui_count_call(void);
uint32_t moy_ui_calls(void);

#define MOY_UI_DEFAULT INT16_MIN
#define MOY_UI_FROM_STYLE (-2)

enum {
    MOY_UI_F_ON = 1, MOY_UI_F_HOT = 2, MOY_UI_F_DISABLED = 4, MOY_UI_F_EDGE = 8,
    MOY_UI_F_BAND = 16, MOY_UI_F_EDGE_LAST = 32, MOY_UI_F_LABEL = 64, MOY_UI_F_ICON = 128,
    MOY_UI_F_GLYPH = 256,
};
// A widget whose caller draws an icon image (or its own glyph) between the
// frame and the content draws in two calls, FRAME then CONTENT; every other
// call is ALL.
enum { MOY_UI_PHASE_FRAME = 1, MOY_UI_PHASE_CONTENT = 2, MOY_UI_PHASE_ALL = 3 };

// The six-state ladder: flags' on/hot/disabled, and `interact` (PRESSED,
// HOVER or -1) from the pointer pump.
int moy_ui_state_of(int flags, int interact);
// Floor division, as Python's `//` (C's `/` truncates).
int moy_ui_fdiv(int a, int b);

typedef struct {
    moy_ui_span_t label, value;     // value.p NULL: no value column
    int16_t glyph;                  // its id, or -1 (with F_GLYPH: a slot the caller fills)
    int16_t glyph_size, glyph_ink, value_ink;
    int16_t pad, pad_right, text_dy;
    int16_t scale, fs;              // the label's width scale; the geometry's font scale
    int16_t colors[3];
} moy_ui_row_t;
int moy_ui_row(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
               const moy_ui_row_t *r, int phase);

typedef struct {
    moy_ui_span_t label;            // drawn when F_LABEL
    int16_t glyph;
    int16_t pad, caption_h, band_fill, band_ink, text_dx, text_dy, fs;
    int16_t colors[3];
} moy_ui_cell_t;
// Also answers the art box, the caller's picture's place.
int moy_ui_cell(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                const moy_ui_cell_t *k, int phase, moy_ui_box_t *art);

// Text in a box: truncated to fit at a codepoint, placed by `align`,
// centred vertically. Answers the drawn width.
int moy_ui_label(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t, int ink, int align);

// The scrollbar's thumb over a scroll state (0: everything fits), and the bar.
int moy_ui_bar_rect(moy_ui_box_t view, int content, int offset, int horizontal, int fs,
                    int bar_w, moy_ui_box_t *out);
int moy_ui_scrollbar(const moy_ui_sink_t *s, moy_ui_box_t view, int content, int offset,
                     int horizontal, int fs, int bar_w);
// The overflow chevrons, '^' above and 'v' below.
int moy_ui_scroll_cues(const moy_ui_sink_t *s, int ux, int uy, int dx, int dy, int can_up,
                       int can_dn, int ci);
// Fill the parts of `in` that `out` does not cover.
int moy_ui_fill_uncovered(const moy_ui_sink_t *s, moy_ui_box_t in, moy_ui_box_t out, int ci);
// A scrolling list: `count` rows of `items` (NUL-separated UTF-8 in `len`
// bytes), `row_h` apart, scrolled by `offset`, `sel` the selected row, each a
// `kind` row clipped to the box, and the bar.
int moy_ui_list(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, const uint8_t *items,
                size_t len, int count, int row_h, int offset, int sel, int fs, int bar_w);

#endif // MOY_UI_H
