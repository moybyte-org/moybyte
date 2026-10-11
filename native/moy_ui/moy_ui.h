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

enum { MOY_UI_OK = 0, MOY_UI_BAD = 1, MOY_UI_STALE = 2, MOY_UI_NOMEM = 3, MOY_UI_FULL = 4 };

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

// -- the primitives (moy_ui_draw.c) ----------------------------------------------
//
// Each takes a sink, a box, a kind id and the widget's state flags, then its
// content, and draws what the Python body it replaced drew, op for op
// (tests/ui_twin.py is that body). Text is a UTF-8 span; a glyph is a glyph id
// or -1; a colour override is an index, -1 for "paint nothing", or
// MOY_UI_FROM_STYLE; a geometry knob left to the style is MOY_UI_DEFAULT.

typedef struct { int16_t x, y, w, h; } moy_ui_box_t;
typedef struct { const uint8_t *p; uint16_t n; } moy_ui_span_t;

// A sink: where a primitive draws, always through a canvas row (`dst`). ROW:
// a canvas row of the glass, named by its CANVAS handle (moy_ui_sink_row
// checks it). RUN: a compiled run's libmoy canvas lent as a row of its own,
// its draw state saved, reset and restored around the call (moy_ui_wasm.h).
// `fs` is the scale text draws at; `look` the look its colours resolve in.
enum { MOY_UI_SINK_ROW = 1, MOY_UI_SINK_RUN = 2 };
typedef struct {
    uint8_t kind;
    uint8_t fs;
    uint8_t look;
    void *dst;
} moy_ui_sink_t;
int moy_ui_sink_row(moy_ui_sink_t *s, uint32_t canvas, int look);
// A glyph through a sink (moy_ui_glyph_draw's raster, a fill per span).
int moy_ui_sink_glyph(const moy_ui_sink_t *s, int g, int x, int y, int w, int h, int ci,
                      int scale);
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

// -- buttons and frames ---------------------------------------------------------------

typedef struct {
    moy_ui_span_t label;
    int16_t glyph;                  // its id, or -1 (with F_GLYPH: a slot the caller fills)
    int16_t fs;
    int16_t colors[3];              // chip's override; button reads its kind's
} moy_ui_button_t;
// A button: the field, its rings, the icon or glyph, the label (centred by
// the style). button_x: where its icon or glyph lands, for a caller's own.
int moy_ui_button(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                  const moy_ui_button_t *k, int phase);
int moy_ui_button_x(moy_ui_box_t b, int flags, moy_ui_span_t label, int fs);
// A chip: a glyph, when it has one, REPLACES its label.
int moy_ui_chip(const moy_ui_sink_t *s, moy_ui_box_t b, int kind, int flags, int interact,
                const moy_ui_button_t *k, int phase);
// The tab ladder's chips (`labels` NUL-separated), every tab icon-only when the
// labelled row would not fit; the count placed. One tab, `ink` its inactive ink.
int moy_ui_tab_rects(moy_ui_box_t r, const uint8_t *labels, size_t len, int count, int fs,
                     int gap, moy_ui_box_t *out, int *labels_on);
int moy_ui_tab(const moy_ui_sink_t *s, moy_ui_box_t r, int active, int labels_on,
               moy_ui_span_t label, int ink, int fs, int phase);
// The status band: its items (NUL-separated) left to right while they fit.
int moy_ui_status(const moy_ui_sink_t *s, moy_ui_box_t b, const uint8_t *items, size_t len,
                  int count, int fs);
// The game-canvas family: caller-coloured, theme-free.
int moy_ui_game_btn(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t label, int fill_c,
                    int fs);
int moy_ui_game_icon_btn(const moy_ui_sink_t *s, moy_ui_box_t b, int g, moy_ui_span_t label,
                         int fill_c, int fs, int phase);
int moy_ui_mini_btn(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t label, int fill_c,
                    int lx, int ly);
int moy_ui_toolbar(const moy_ui_sink_t *s, moy_ui_box_t b);
int moy_ui_dialog(const moy_ui_sink_t *s, moy_ui_box_t b, int ring, int fill_c);
// `colors` NULL: the style's (field, ring, ink, placeholder ink, caret).
int moy_ui_text_field(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t,
                      moy_ui_span_t hint, const int16_t colors[5], int fs);
moy_ui_box_t moy_ui_inset(moy_ui_box_t r, int dx, int dy);
// A panel; answers its content box (inside the border, below a title strip).
int moy_ui_panel(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                 moy_ui_box_t *content);
int moy_ui_focus_ring(const moy_ui_sink_t *s, moy_ui_box_t b, int fs);

// -- rect algebra (int32: a scrolled content can outgrow a box) ---------------------------

void moy_ui_inset32(const int32_t r[4], int32_t dx, int32_t dy, int32_t out[4]);
enum { MOY_UI_CUT_TOP = 0, MOY_UI_CUT_BOTTOM = 1, MOY_UI_CUT_LEFT = 2, MOY_UI_CUT_RIGHT = 3 };
void moy_ui_cut(const int32_t r[4], int side, int32_t n, int32_t band[4], int32_t rest[4]);
// n equal spans across (horizontal) or down r, the slack to the last; the count.
int moy_ui_split(const int32_t r[4], int n, int32_t gap, int horizontal, int32_t (*out)[4],
                 int cap);
void moy_ui_row_content(const int32_t r[4], int fs, int32_t pad, int32_t pad_right, int icon,
                        int glyph, int32_t glyph_size, int32_t out[4]);
void moy_ui_cell_art(const int32_t r[4], int fs, int32_t pad, int32_t caption_h,
                     int32_t out[4]);

// -- interaction over caller-owned state (moy_ui_interact.c) ------------------------------
//
// Hits, ScrollRegion and DragTap's rules as pure functions over buffers the
// CALLER owns (a bytearray inside the Python object, a span of a compiled
// app's memory); the kernel holds nothing between calls.

// A hit buffer: int32 words, a header then five words per rect (x, y, w, h,
// id). Its capacity is the caller's; an add past it answers FULL and is
// counted in the header.
enum { MOY_UI_HITS_N = 0, MOY_UI_HITS_FULL = 1, MOY_UI_HITS_HDR = 2 };
#define MOY_UI_HITS_WORDS(cap) (MOY_UI_HITS_HDR + 5 * (cap))
void moy_ui_hits_clear(int32_t *b);
int moy_ui_hits_add(int32_t *b, size_t words, int32_t x, int32_t y, int32_t w, int32_t h,
                    int32_t id);
// The topmost id at (px, py) -- the last added wins -- or -1.
int32_t moy_ui_hits_at(const int32_t *b, int32_t px, int32_t py);
// The pump (#177's rules): hover needs a POINTING cursor and a pointer that
// is up; the press EDGE arms the rect under it, pressed holds while the
// pointer stays on the armed rect; release clears both.
typedef struct {
    int32_t hover, pressed, armed;
    int32_t down;
} moy_ui_pump_t;
void moy_ui_hits_pump(const int32_t *b, int32_t px, int32_t py, int down, int pointing,
                      moy_ui_pump_t *p);

// A scroll state's motion; the offset, the content extent and the view are
// the caller's, passed in. Single-precision, as the boards' MicroPython
// computes; every dt is injected, never read from a clock.
#define MOY_UI_FRICTION 0.995f      // per-ms decay: a fling coasts ~1 s
#define MOY_UI_MIN_FLING 0.15f      // px/ms: a release slower than this is a stop
#define MOY_UI_STOP_VEL 0.02f       // px/ms: coasting below this comes to rest
#define MOY_UI_VEL_EMA 0.5f         // the release velocity's EMA weight
enum { MOY_UI_SCROLL_DRAG = 1, MOY_UI_SCROLL_FLING = 2 };
typedef struct {
    float vel, foff;
    int32_t drag;
    int32_t flags;
} moy_ui_scroll_t;
void moy_ui_scroll_stop(moy_ui_scroll_t *s);
void moy_ui_scroll_drag_start(moy_ui_scroll_t *s, int32_t p);
int moy_ui_scroll_drag_move(moy_ui_scroll_t *s, int32_t p, float dt_ms, int32_t *offset,
                            int32_t content, int32_t extent);
void moy_ui_scroll_drag_end(moy_ui_scroll_t *s, int32_t offset, int32_t content,
                            int32_t extent);
int moy_ui_scroll_tick(moy_ui_scroll_t *s, float dt_ms, int32_t *offset, int32_t content,
                       int32_t extent);

// Press/drag/release over a region: a press in the view arms a tap; travel
// past `slop` turns it into a scroll; a clean release fires it; a press that
// caught a fling never taps.
enum { MOY_UI_TAP_PRESS = 1, MOY_UI_TAP_DRAGGING = 2, MOY_UI_TAP_CAUGHT = 4 };
typedef struct {
    int32_t px, py;
    int32_t flags;
} moy_ui_dragtap_t;
int moy_ui_dragtap_frame(moy_ui_dragtap_t *t, moy_ui_scroll_t *s, const int32_t view[4],
                         int horizontal, int32_t px, int32_t py, int click, int down,
                         int32_t slop, float dt_ms, int32_t *offset, int32_t content,
                         int32_t *tx, int32_t *ty);

#endif // MOY_UI_H
