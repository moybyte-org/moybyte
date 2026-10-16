// moy_ui: the widget toolkit's bodies in C, with style and strings as data
// (docs/kernel_toolkit_2026-10.md). This file is the style resolution: the
// kinds, the states, each kind's (field, ink, edge) in each state resolved
// against a theme's token row, and each kind's metric slots.
// The data is style.json's, generated into moy_ui_tables.c
// (tools/gen_ui_tables.py); nothing here holds a colour or a pad.
//
// A LOOK is the style resolved against one token row: per kind and state,
// three palette indices, -1 for "paint nothing". A look flattens lazily, one
// kind at a time, and again when its row is set. There are MOY_UI_LOOKS of
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

typedef struct { int16_t x, y, w, h; } moy_ui_box_t;

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

// Point `look` at a token row (MOY_UI_TOKENS values, MOY_UI_ABSENT where the
// theme is silent): its flatten starts over.
int moy_ui_look_set(int look, const int32_t tokens[MOY_UI_TOKENS]);
// (field, ink, edge) of `kind` in `state` under `look`, -1 for nothing.
int moy_ui_colors(int look, int kind, int state, int16_t out[3]);
// A kind's own states (rest, hover, hot) whose (field, ink) misses the
// contrast floor take its floor forms (style.json's "floors"), in order.

// -- button kinds (docs/look_2026-10.md step 10) -------------------------------------
//
// `button(kind=...)`'s names are style.json's button_kinds, each drawn as a
// style kind. The live theme's `buttons[kind]` and `app_buttons[app][kind]`
// (MOY_THEME_BTN_LEAVES each, a colour placed in the live block, MOY_THEME_NONE
// where silent) restyle them: an app's leaf, then the theme's, then, for a
// leaf the kind takes from `normal` (DESIGN.md), normal's the same way, then
// the style's. `app` NULL sets the theme's row.
void moy_ui_buttons_clear(void);
int moy_ui_buttons_set(const char *app, size_t n, int bkind,
                       const int32_t leaves[MOY_THEME_BTN_LEAVES]);
// The row of app `name`'s overrides, or -1 where the theme has none for it.
int moy_ui_buttons_app(const char *name, size_t n);
// The button kind (an index of moy_ui_button_kinds) style kind `kind` is, or -1.
int moy_ui_button_of(int kind);
// A button's resolved style in `state` for app row `app` (-1 none,
// MOY_UI_NO_THEME the style alone: a compiled run, whose colours are MOY64): its
// (field, ink, edge), -1 for nothing; the ring width in pixels, the shape (an
// index of the field's enum), the radius, the font (key, op) and the bevel,
// -1 where silent (the style's).
#define MOY_UI_NO_THEME (-2)
typedef struct {
    int16_t c[3];
    int8_t edge_w, shape, r, font, bevel;
} moy_ui_btn_style_t;
int moy_ui_button_style(int look, int kind, int state, int app, moy_ui_btn_style_t *out);

// The metric slots of `kind` (its count in *n, 0: none).
const int16_t *moy_ui_metrics(int kind, int *n);

// A token of `look`'s row: its palette index, or -1 where the theme is silent.
int moy_ui_token(int look, int token);
// The sprite sheet's slots under `look` (style.json's theme.sprite_slots
// resolved against its row when the row is set): MOY_THEME_SPRITE_SLOTS
// palette indices, -1 for transparent. NULL for no look.
const int16_t *moy_ui_sprite_slots(int look);

// -- the theme's shape: surfaces, metrics, text effects ----------------------------
//
// A SURFACE (docs/theming_2026-09.md §4.4) is one line of text in a theme,
// compiled once by the loader (moy_theme.c) into a record: an edge, a fill and
// its two colours, an optional border and an optional shadow. A colour is a
// TERM: a palette index (0-63), a token of the look's row
// (MOY_UI_TERM_TOKEN), or MOY_UI_TERM_STYLE, the part's own field (a fill) or
// edge (a border) as the style resolves it.
enum {
    MOY_UI_EDGE_FLAT = 0, MOY_UI_EDGE_RAISED = 1, MOY_UI_EDGE_SUNKEN = 2,
    MOY_UI_EDGE_ETCHED = 3, MOY_UI_EDGE_BEVEL2 = 4,
};
// MOY_UI_FILL_STYLE: no fill clause, the part's own field.
enum {
    MOY_UI_FILL_STYLE = 0, MOY_UI_FILL_SOLID = 1, MOY_UI_FILL_DITHER = 2,
    MOY_UI_FILL_PINSTRIPE = 3, MOY_UI_FILL_GRADIENT = 4,
};
enum { MOY_UI_SURF_BORDER = 1, MOY_UI_SURF_SHADOW = 2 };
#define MOY_UI_SHADOW 2             // a surface's shadow depth in pixels (D1's hard shadow)
#define MOY_UI_TERM_STYLE INT8_MIN
#define MOY_UI_TERM_TOKEN(t) ((int8_t)(-1 - (t)))
typedef struct {
    uint8_t edge, fill, flags;
    int8_t a, b, line;              // the fill's colours (b: a pattern's second); the border's
} moy_ui_surface_t;

// A theme's shape, resolved once (moy_theme_shape): its metrics, each part's
// text effect and surface per state ({effect, role} with {0, 0xff} for none,
// {0, MOY_UI_FX_NAMED_NONE} for a `none` the theme names, which a chain stops at;
// a stid, 0 for none, else 1 + its record in `surf`), parts and states as
// the theme names them (MOY_THEME_PARTS: the kinds, then the chrome's parts).
#define MOY_UI_STIDS 64
#define MOY_UI_FRAME_FIELDS 6
#define MOY_UI_FX_NAMED_NONE 0xfd
#define MOY_UI_SHAPE_NAME 25        // a theme's name, NUL-terminated (moy_theme.h's max + 1)
typedef struct {
    int8_t metric[MOY_THEME_METRICS];
    uint8_t n_surf, n_fx;
    uint8_t effect[MOY_THEME_PARTS][MOY_THEME_STATES][2];
    uint8_t stid[MOY_THEME_PARTS][MOY_THEME_STATES];
    moy_ui_surface_t surf[MOY_UI_STIDS];
    // The window frame (moy_ui_frame): [0] the style + 1, [1] the title's
    // alignment + 1, [2..5] the buttons' letters in order; 0 where the theme
    // is silent, which draws the style's own (a silent style is classic).
    uint8_t frame[MOY_UI_FRAME_FIELDS];
    // The theme the shape is of ("" for the empty one): what a library of its
    // own (the host's compiled-app library) compares to install the same.
    char name[MOY_UI_SHAPE_NAME];
} moy_ui_shape_t;
extern const int moy_ui_shape_size;     // sizeof, for the bindings' layout check
// The shape of a theme that names nothing: metrics 0, no effect, no surface.
void moy_ui_shape_clear(moy_ui_shape_t *sh);
// Install the shape every primitive draws with (NULL: the empty one), one
// for the process. Each state a part leaves unnamed takes what its chain
// (style.json's theme.chain) reaches, here, once.
int moy_ui_shape_use(const moy_ui_shape_t *sh);
const moy_ui_shape_t *moy_ui_shape(void);
// The font each role's text draws in (docs/theming_2026-09.md §4.7), the
// roles in style.json's theme.font_roles order. A role draws in petme128
// until a font is installed for it, and again once that font is freed.
enum { MOY_UI_FONT_UI = 0, MOY_UI_FONT_TITLE = 1, MOY_UI_FONT_MONO = 2, MOY_UI_FONT_CAPTION = 3 };
// Install `font` (an id moy_font_load gave, or 0 for petme128) for `role`:
// 1, or 0 for an unknown role or font, which installs nothing.
int moy_ui_font_use(int role, uint32_t font);
// The font `role` draws in, its metrics into *out.
uint32_t moy_ui_font(int role, moy_font_info_t *out);
// The app-cart boundary (§4.7): while a cart's code runs, every role draws in
// petme128 and so does the print font (moy_text.h), which comes back when the
// cart's code returns, and a colour index is masked to 0..63 (a cart's
// colour is its index modulo 64) where the shell's reaches the theme's block
// at 64..127 (moy_canvas.h). Answers whether it was on.
int moy_ui_cart_text(int on);
// What a colour index is masked with now: 127, or 63 inside the boundary.
int moy_ui_cmask(void);
// The shell's print font (moy_text_print_font_use), the one put aside while a
// cart's code runs: the font it replaced, or -1 for one not loaded.
int64_t moy_ui_print_font(uint32_t font);
// The record and the effect `part` paints in `state` under an installed
// shape: NULL / 0 where it names none.
const moy_ui_surface_t *moy_ui_shape_surface(const moy_ui_shape_t *sh, int part, int state);
int moy_ui_shape_effect(const moy_ui_shape_t *sh, int part, int state, int *role);
// The readability check of a pattern fill (docs/theming_2026-09.md §7): each
// part and state of `sh` whose surface is a dither or a pinstripe, its label's
// ink in `look` against both of the pattern's colours on theme.contrast. The
// first `cap` (part, state) pairs that miss go to `out`; answers how many miss.
int moy_ui_pattern_unreadable(int look, const moy_ui_shape_t *sh, uint8_t (*out)[2], int cap);

// The painter (moy_ui_paint.c): surface `sf` into box `b` of a target, its
// terms resolved through `look` (`field` and `edge` stand for
// MOY_UI_TERM_STYLE; -1 paints nothing), its outline cut by the corner mask
// of `radius` (0-3). Never draws outside the box. Answers the fills it made.
int moy_ui_paint_surface(const moy_text_target_t *t, const uint16_t pal[128], int look,
                         moy_ui_box_t b, const moy_ui_surface_t *sf, int field, int edge,
                         int radius);

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

// -- depth (moy_ui_depth.c, docs/look_2026-10.md step 6) -------------------------
//
// The theme's look as the shapes draw it: radii, the shadow and the edges,
// resolved once per theme and mode (moy_ui_depth_use) and read by the window
// frame and the casts. A theme with no `look` installs depth off, and its
// frame draws as its style always has. Colours are look values (moy_theme.h):
// an index, a slot, or RGB.
typedef struct {
    uint8_t on, win_r, card_r, key_r, corners, stepped, shadow, edges;
    uint8_t frames;                 // the window frame is the depth's
                                    // (moy_theme_look_frames)
    uint16_t shadow_565;            // canonical RGB565
    int16_t alpha, blur, dx, dy, back;  // alpha 0..255; back: a back window's share, %
    int16_t line, line_back;        // win.frame's and win.frame_back's widths
    int32_t line_c, line_back_c, body;
} moy_ui_depth_t;
// Install theme `name`'s depth in a mode (light 1: its `light` look). Answers on.
int moy_ui_depth_use(const char *name, size_t n, int light);
const moy_ui_depth_t *moy_ui_depth(void);
// A shadow's size: the theme's, a smaller one (a quarter of the blur, half the
// offset: a window being dragged), or none.
enum { MOY_UI_UNDER_FULL = 0, MOY_UI_UNDER_SMALL = 1, MOY_UI_UNDER_NONE = 2 };
enum { MOY_UI_CAST_WINDOW = 0, MOY_UI_CAST_CARD = 1, MOY_UI_CAST_KEY = 2 };
// A w x h window's frame geometry under the depth: [0] its corners' radius,
// [1] its foot (the rows under the content its bottom corners need), [2..5]
// how far its shadow at `mode`'s size reaches left, up, right and down. All
// zero unless the depth draws the frame (`frames`).
#define MOY_UI_GEOM 6
void moy_ui_frame_geom(int w, int h, int th, int active, int mode, int out[MOY_UI_GEOM]);
// The frame's own pixels under the depth (moy_ui_frame draws it): the strip,
// the sides and the foot inside the rounded outline, the ring, the rule.
void moy_ui_depth_frame(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active);
// What a window leaves to the layer under it: its shadow at `mode`'s size and
// its outline's partly covered pixels, blended over what that layer just
// painted (runtime/wm_desk.py draws it, clipped to what it repainted).
int moy_ui_frame_under(const moy_ui_sink_t *s, moy_ui_box_t w, int th, int active, int mode);
// The shadow box `b` casts as a part (MOY_UI_CAST_*), its own pixels left
// alone. Answers whether the theme casts one.
int moy_ui_cast(const moy_ui_sink_t *s, moy_ui_box_t b, int part, int mode);

// -- the marks (moy_ui_depth.c; docs/media/look_2026-10/DESIGN.md, the marks) ----
//
// Focus and current are theme properties the engine draws: `focus` by the
// part's size class (a CARD 48 px and up, a SMALL part under 28 px both ways,
// any other a PART), `current` as a plate, a fill or a bar. They resolve with
// the depth (moy_ui_depth_use) from the look's focus.*, current.* and
// sel_back, which a theme may set without a depth. A theme with no marks
// draws its focus as its ring (moy_ui_focus_ring) and no current mark.
// Focus is drawn only while the marks are ACTIVE: the shell clears that while
// a window behind draws, so a screen shows one focus mark, in the focused
// window. `drawn` counts the focus marks drawn, for the nets.
enum { MOY_UI_MARK_PART = 0, MOY_UI_MARK_CARD = 1, MOY_UI_MARK_SMALL = 2, MOY_UI_MARK_AUTO = 3 };
// A mark's flags: AFTER the part's content (a lifted card's rim), BACK in a
// window behind, a current bar at the SIDE of a row rather than under it.
// INK draws nothing and answers only the ink (a part's CONTENT phase).
enum { MOY_UI_MARK_AFTER = 1, MOY_UI_MARK_BACK = 2, MOY_UI_MARK_SIDE = 4, MOY_UI_MARK_INK = 8 };
// focus.style's and current.style's values, in style.json's order
enum {
    MOY_UI_FOCUS_RING = 0, MOY_UI_FOCUS_PLATE = 1, MOY_UI_FOCUS_LIFT = 2, MOY_UI_FOCUS_EDGE = 3,
    MOY_UI_FOCUS_UNDERLINE = 4, MOY_UI_FOCUS_INVERT = 5,
};
enum { MOY_UI_CUR_BAR = 0, MOY_UI_CUR_PLATE = 1, MOY_UI_CUR_FILL = 2 };
typedef struct {
    uint8_t on, active, dark, ink_accent;
    uint8_t style[3];               // per class: PART, CARD, SMALL
    uint8_t ring_alpha, plate_alpha, rim_alpha, cur_alpha, cur_style, outline_w;
    int16_t color, cur_color;       // tokens
    int16_t ring_w, ring_gap, grow, rise, edge_w, underline_w;
    int32_t outline_c, rim_c, sel_back;     // look colours
    uint32_t drawn;
} moy_ui_marks_t;
const moy_ui_marks_t *moy_ui_marks(void);
void moy_ui_marks_active(int active);
// The focus marks drawn since the last reset.
uint32_t moy_ui_marks_drawn(int reset);
// How far class `cls`'s focus mark reaches past its box's top edge (0: inside it).
int moy_ui_mark_reach(int cls);
// The size class of box `b` at scale fs, or `cls` where it names one.
int moy_ui_mark_class(moy_ui_box_t b, int cls, int fs);
// The focus mark on box `b` (corner radius r), drawn after the part's fill
// and before its content; a lifted card's rim is a second call, AFTER its
// content. *content gets the box the content draws in (a lifted card's,
// grown and raised), *ink the colour index the content takes (-1: its own).
int moy_ui_focus(const moy_ui_sink_t *s, moy_ui_box_t b, int cls, int r, int flags, int fs,
                 moy_ui_box_t *content, int *ink);
// The current mark under box `b`'s content; *ink as moy_ui_focus's.
int moy_ui_current(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int flags, int fs, int *ink);
// The two as a primitive draws them, inside its own crossing.
int moy_ui_focus_in(const moy_ui_sink_t *s, moy_ui_box_t b, int cls, int r, int flags, int fs,
                    moy_ui_box_t *content, int *ink, int crossing);
int moy_ui_current_in(const moy_ui_sink_t *s, moy_ui_box_t b, int r, int flags, int fs, int *ink,
                      int crossing);

#define MOY_UI_DEFAULT INT16_MIN
#define MOY_UI_FROM_STYLE (-2)

enum {
    MOY_UI_F_ON = 1, MOY_UI_F_HOT = 2, MOY_UI_F_DISABLED = 4, MOY_UI_F_EDGE = 8,
    MOY_UI_F_BAND = 16, MOY_UI_F_EDGE_LAST = 32, MOY_UI_F_LABEL = 64, MOY_UI_F_ICON = 128,
    MOY_UI_F_GLYPH = 256, MOY_UI_F_FOCUS = 512,
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

// What a label meets (moy_text.h's paint, docs/look_2026-10.md step 4): `bg`
// the colour index the caller has filled the whole box with (-1: not known,
// the glyph lane reads), and a shadow in colour index `shadow` (-1: none) at
// `shadow_a` (1..255) of the coverage, (dx, dy) pixels at the font scale
// from the text. moy_ui_label is this with neither.
typedef struct { int16_t bg, shadow, shadow_a, dx, dy; } moy_ui_label_paint_t;
int moy_ui_label_paint(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t, int ink,
                       int align, const moy_ui_label_paint_t *lp);

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
    int16_t colors[3];              // an override of the kind's, MOY_UI_FROM_STYLE each where none
    int16_t app;                    // button: its app's row of overrides, or -1
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
// A toolbar band; `*ink` is the label ink that reads on what it painted.
int moy_ui_toolbar(const moy_ui_sink_t *s, moy_ui_box_t b, int *ink);
int moy_ui_dialog(const moy_ui_sink_t *s, moy_ui_box_t b, int ring, int fill_c);
// `colors` NULL: the style's (field, ring, ink, placeholder ink, caret).
int moy_ui_text_field(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t t,
                      moy_ui_span_t hint, const int16_t colors[5], int fs);
moy_ui_box_t moy_ui_inset(moy_ui_box_t r, int dx, int dy);
// A panel; answers its content box (inside the border, below a title strip).
int moy_ui_panel(const moy_ui_sink_t *s, moy_ui_box_t b, moy_ui_span_t title, int fs,
                 moy_ui_box_t *content);
int moy_ui_focus_ring(const moy_ui_sink_t *s, moy_ui_box_t b, int fs);
// Paint `part`'s sprite in `state` (theme part and state ids) into the box
// when the role sheet sets its cell, else its surface, as a primitive paints
// its frame. *painted: 0 when the part has neither there (nothing drawn).
int moy_ui_paint(const moy_ui_sink_t *s, moy_ui_box_t b, int part, int state, int *painted);
// A theme part's id by name, or -1.
int moy_ui_part(const char *name, size_t n);

// -- window frames (docs/theming_2026-09.md §4.5) ---------------------------------------
//
// A window's decoration in the built-in style the installed shape chooses
// (style.json's theme.frames), with its title alignment and button order.
// Every style draws inside one footprint, the one the WMs' damage, hit-tests
// and content rects are built on: the window box's one-pixel ring, the
// `title_h` strip under its top edge, and the three-pixel drop shadow right
// of and below the box. The `window.title` and `window.button` surfaces a
// theme names are painted in place of the style's own; the title takes
// `window.title`'s text effect. The role sheet's `window` 9-slice replaces
// the ring's top row and the strip, and a title button's sprite its surface
// and glyph (docs/theming_2026-09.md §4.6). States are MOY_THEME_S_ACTIVE /
// _INACTIVE.
enum { MOY_UI_FB_CLOSE = 0, MOY_UI_FB_MAX = 1, MOY_UI_FB_MIN = 2 };
typedef struct {
    moy_ui_box_t box;
    uint8_t id;                     // MOY_UI_FB_*
    uint8_t right;                  // 1: in the group right of the title
} moy_ui_frame_button_t;
// The strip's buttons as the frame draws them, left to right; the count. A
// window without a minimise (`has_min` 0) has two.
int moy_ui_frame_buttons(moy_ui_box_t win, int title_h, moy_ui_span_t title, int has_min, int fs,
                         moy_ui_frame_button_t out[3]);
int moy_ui_frame(const moy_ui_sink_t *s, moy_ui_box_t win, int title_h, moy_ui_span_t title,
                 int active, int has_min, int fs);
// The bar's band in the frame's style (`bar`'s surface where the theme names
// one): `light` is a window's light toolbar; `side` the band's edge line, on
// the side that faces the content (a top bar's bottom row, a left bar's
// right column); a Win9x frame shades both long sides instead.
enum { MOY_UI_BAND_BOTTOM = 0, MOY_UI_BAND_TOP = 1, MOY_UI_BAND_RIGHT = 2, MOY_UI_BAND_LEFT = 3 };
int moy_ui_frame_band(const moy_ui_sink_t *s, moy_ui_box_t b, int light, int side);

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
