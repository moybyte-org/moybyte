// The public UI API's engine (docs/ui_api_2026-10.md): the verbs every app
// and cart draws its interface with -- page, label, button, toggle, slider,
// list, group, info, link, cycle, seg, swatches, hints, icon, progress, table,
// scroll, field, dialog, popover, menu, tabs, grid, card, part, focus, keys_taken, at -- and
// the focus and navigation they share.
//
// Immediate mode over a CONTEXT the caller owns (moy_ui_ctx_t, plain bytes:
// the Python face's bytearray per app, the run's one static): the focused id,
// the stops the previous frame drew, each id's kept state (a page's scroll, a
// list's row) and the layout cursor. A frame begins with moy_ui_api_begin,
// which resolves the frame's input once over the previous frame's stops --
// focus moves, activation, the pointer's press and drag, the page scroll that
// keeps focus in view -- and every verb after it draws through a sink
// (moy_ui.h) with the theme's marks: a stop's `focus=` and `on`.
//
// An id is its label's FNV-1a hash, or the override's `id`; a page's is its
// title's under a page salt. A verb's answer is its value: true the frame a
// button is activated, a toggle's and a slider's new value, a list's new row.

#ifndef MOY_UI_API_H
#define MOY_UI_API_H

#include <stddef.h>
#include <stdint.h>

#include "moy_ui.h"

#define MOY_UI_API_STOPS 40         // the stops one frame records
#define MOY_UI_API_KEEP 16          // the ids whose state is kept
#define MOY_UI_API_CAPS 16          // the group captions one frame records
#define MOY_UI_API_NAME 32          // the focused id's text, NUL-terminated

// The input a frame resolves over, the run snapshot's shape: the buttons held
// and pressed this frame (bit i is MOY_INPUT_NAMES[i]: left, right, up, down,
// a, b, run), the pointer (x, y and its flags: LIVE 1, HELD 2, the press edge
// 4) and the key typed this frame (9 is Tab).
enum {
    MOY_UI_IN_BTN = 0, MOY_UI_IN_BTNP, MOY_UI_IN_X, MOY_UI_IN_Y, MOY_UI_IN_PTR, MOY_UI_IN_KEY,
    MOY_UI_IN_LEN
};
enum { MOY_UI_P_LIVE = 1, MOY_UI_P_HELD = 2, MOY_UI_P_EDGE = 4 };
enum {
    MOY_UI_B_LEFT = 1, MOY_UI_B_RIGHT = 2, MOY_UI_B_UP = 4, MOY_UI_B_DOWN = 8, MOY_UI_B_A = 16,
    MOY_UI_B_B = 32, MOY_UI_B_RUN = 64
};
#define MOY_UI_KEY_TAB 9

// A part's state bits (§5).
enum { MOY_UI_ST_FOCUS = 1, MOY_UI_ST_ACTIVATED = 2, MOY_UI_ST_PRESSED = 4, MOY_UI_ST_HOVER = 8 };

// The override block (§3) as words, in its order: fill, ink, border (a role
// as MOY_UI_O_ROLE + its index in the token row, moy_ui_token_names; an RGB
// colour 0xRRGGBB; -1 absent), the id's length in bytes (its text follows
// the nine words; -1 absent), current, disabled, nav, mark, steps (0 or 1;
// -1 absent). A wasm app hands it over as a pointer and its length.
enum {
    MOY_UI_O_FILL = 0, MOY_UI_O_INK, MOY_UI_O_BORDER, MOY_UI_O_ID, MOY_UI_O_CURRENT,
    MOY_UI_O_DISABLED, MOY_UI_O_NAV, MOY_UI_O_MARK, MOY_UI_O_STEPS, MOY_UI_O_WORDS
};
#define MOY_UI_O_ROLE 0x1000000
#define MOY_UI_O_NONE (-1)

typedef struct {
    int32_t fill, ink, border;      // MOY_UI_O_NONE, RGB, or MOY_UI_O_ROLE + a token
    moy_ui_span_t id;               // p NULL: the label's
    int8_t current, disabled, nav, mark;    // -1 absent
    int8_t steps;                   // a slider: a segment per step, no number
} moy_ui_over_t;

// Every key absent.
void moy_ui_over_none(moy_ui_over_t *o);
// The block of `n` bytes at `w` (NULL or 0: every key absent) into *o, its id
// pointing into the block. MOY_UI_BAD where it is short or its id overruns.
int moy_ui_over_block(moy_ui_over_t *o, const uint8_t *w, size_t n);

typedef struct {
    uint32_t id;
    moy_ui_box_t b;                 // on screen, as drawn
    uint16_t flags;                 // STOP_* (moy_ui_api.c)
    uint8_t page;                   // the index of its page's stop, or 0xff
} moy_ui_stop_t;

// The page a scroll region, a dialog's body or a popover's stands in, kept
// while it is open.
typedef struct {
    moy_ui_box_t page;
    int16_t y, scroll;
    int8_t keep;
    uint8_t stop, kind;             // kind: 1 a scroll region, 2 a dialog, 3 a popover
    int16_t used;                   // a dialog or popover: the body height it was laid out for
} moy_ui_nest_t;
#define MOY_UI_API_NEST 2

typedef struct {
    uint32_t id;
    int32_t a, b;                   // a page: its scroll, its rows' height; a list: its row
    int16_t over;                   // a page: how far a scroll into view went past its rows
    int16_t foot;                   // a page: the hint band its foot gave up last frame
    uint8_t seen;
} moy_ui_keep_t;

typedef struct {
    int16_t y, h;
    uint8_t page;
} moy_ui_cap_t;

typedef struct {
    uint32_t magic;
    int32_t in[MOY_UI_IN_LEN];
    int32_t last_key, last_ptr, last_typed;
    uint32_t focus;                 // the focused id, 0 none
    moy_ui_box_t focus_box;         // where it last drew
    char focus_name[MOY_UI_API_NAME];
    uint32_t act;                   // the id activated this frame
    uint32_t press;                 // the id the pointer went down on
    uint32_t drag_page;             // the page a drag scrolls
    int16_t tap_x, tap_y;           // where the activating tap was released
    int16_t press_x, press_y, drag_y;
    int16_t last_x, last_y;         // the pointer last frame
    int8_t step_x, step_y;          // a key the focused stop keeps: -1 or 1
    uint8_t keys, reveal, cls, fs;
    uint8_t win;                    // the page lies in a window's body
    uint8_t app;                    // its buttons' row of app_buttons + 1, 0 none
    uint8_t n_prev, n_cur, has_at, gesture;    // gesture: 0 none, 1 down, 2 a scroll, 3 sideways
    moy_ui_stop_t prev[MOY_UI_API_STOPS], cur[MOY_UI_API_STOPS];
    moy_ui_keep_t keep[MOY_UI_API_KEEP];
    // The group captions drawn last frame and this one: where on screen, and
    // the page stop they lie on (a scroll into view keeps them whole).
    moy_ui_cap_t cap_prev[MOY_UI_API_CAPS], cap_cur[MOY_UI_API_CAPS];
    uint8_t n_cap_prev, n_cap_cur;
    moy_ui_box_t surface;           // what a page fills where ui.at does not place it
    moy_ui_box_t page;              // the content rect rows stack in
    moy_ui_box_t at;
    int16_t y;                      // the row cursor, in content coordinates
    int16_t scroll;                 // the page's scroll
    int8_t page_keep;               // the page's kept state, or -1
    uint8_t page_stop;              // its stop, or 0xff
    moy_ui_box_t reveal_box;        // a list row to keep in view next frame
    uint32_t dup, dup_told;         // a duplicate id this frame (its hash), the last told
    char dup_name[MOY_UI_API_NAME];
    // The open group (ui.group): its card's top in content coordinates, the
    // rows stacked on it, its last row's box and that row's marks, and the
    // marks of the row before it.
    int16_t grp_top;
    uint16_t grp_flags, grp_prev;
    uint8_t grp_open, grp_n, grp_any, grp_first;
    moy_ui_box_t grp_last;
    // ui.icon's: the next row's icon, then the row's being drawn (an icon
    // set's slot and the glyph table's glyph, -1 none).
    int16_t icon_slot, icon_glyph, row_slot, row_glyph;
    // ui.menu's danger rows for the next menu: bit i, its row i (moy_ui_api_menu_danger)
    uint32_t menu_danger;
    // ui.scroll's and ui.dialog's open pages, innermost last.
    moy_ui_nest_t nest[MOY_UI_API_NEST];
    uint8_t n_nest;
    // The frame begun over a dialog's stops (MODAL: the others take no input);
    // a layout from a stale measure owes the next frame (OWE).
    uint8_t modal, owe;
    int8_t dlg_pick;                // the dialog button activated this frame, -1 none
    uint32_t modal_from;            // the focus a dialog took
    int32_t typed;                  // the key a focused field takes this frame, 0 none
    // ui.popover's: the card drawn this frame and last (w 0: none), the
    // rect it hangs from, and whether this frame's input dismissed it (a
    // press outside the card drawn last, or Esc).
    moy_ui_box_t pop_card, pop_prev, pop_at;
    uint8_t pop_gone;
    uint8_t pop_mid;                // it opened with nothing to hang from: centred
    uint32_t pop_id;                // the popover drawn this frame (0: none)
    // ui.grid's: open (2: placed by ui.at), its columns, the card next, the
    // first column's left edge and the columns' pitch, the pictures' size,
    // the top of the card row being laid out (content coordinates; a placed
    // grid's on screen) and a card row's height.
    uint8_t grid_open, grid_cols;
    uint16_t grid_i;
    int16_t grid_x, grid_pitch, grid_w, grid_h, grid_y, grid_row;
    moy_ui_box_t grid_at;
    // The card drawn last, its picture finished (corners, ring, a lifted
    // rim) by the next card or the grid's end, once the app drew into it.
    uint8_t fin_on, fin_flags;
    uint16_t fin_ground;
    moy_ui_box_t fin_pic, fin_content;
} moy_ui_ctx_t;

// A context's size, for a face that owns it as bytes; and a fresh one.
size_t moy_ui_api_size(void);
void moy_ui_api_reset(moy_ui_ctx_t *c);
// A context the bytes at `p` hold (resetting them the first time), or NULL
// where `n` is short or `p` misaligned.
moy_ui_ctx_t *moy_ui_api_ctx(void *p, size_t n);

// A frame begins: `in` (MOY_UI_IN_LEN words, NULL none), the surface a page
// fills, its flags (moy_ui.h's MOY_UI_DESK: the desk's rows, else the
// handheld's; MOY_UI_WINDOW: the page lies in a window's body, so its ground
// is the window's; and, in the bits from MOY_UI_APP_SHIFT, the row of the
// theme's app_buttons its buttons take plus one, 0 none, which only a shell
// app's host passes) and the font scale. Answers 1 when the frame is SETTLED: its input moved no focus,
// activated nothing, pressed nothing and scrolled nothing, so a page drawn
// again draws the same pixels.
#define MOY_UI_APP_SHIFT 8
int moy_ui_api_begin(moy_ui_ctx_t *c, const int32_t *in, moy_ui_box_t surface, int flags,
                     int fs);

// The verbs (§1). `o` may be NULL. Text is a span.
void moy_ui_api_page(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     const moy_ui_over_t *o, moy_ui_box_t *content);
void moy_ui_api_label(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      const moy_ui_over_t *o);
// `kind` a button kind's index (moy_ui_button_kinds: normal, primary, tonal,
// danger, toggle); an unknown one draws as normal.
int moy_ui_api_button(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int kind,
                      const moy_ui_over_t *o);
int moy_ui_api_toggle(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int on,
                      const moy_ui_over_t *o);
int32_t moy_ui_api_slider(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                          int32_t v, int32_t lo, int32_t hi, const moy_ui_over_t *o);
// `items` NUL-separated in `len` bytes; `cur` from 0, -1 none. Answers the new cur.
int32_t moy_ui_api_list(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, int32_t cur, const moy_ui_over_t *o);
// A group (§1): `caption` heads a card the rows stacked after it sit on,
// until the next group or a caption of length 0, which ends it.
void moy_ui_api_group(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t caption,
                      const moy_ui_over_t *o);
// A row with a value at its right: `kind` MOY_UI_V_INFO (no stop) or
// MOY_UI_V_LINK, which answers true the frame it is activated.
int moy_ui_api_value(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                     moy_ui_span_t value, const moy_ui_over_t *o);
// A choice of `n` options with `cur` (from 0, -1 none) current: `kind`
// MOY_UI_V_CYCLE, MOY_UI_V_SEG and MOY_UI_V_TABS (a tab row: its options
// from the left, its label only its id) take `opts` NUL-separated,
// MOY_UI_V_SWATCH `n` colours (0xRRGGBB, little-endian words). Left and right
// are its own while focused (a cycle wraps); activation takes the next
// option, a tap the one tapped. Answers the new cur.
int32_t moy_ui_api_choice(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                          const uint8_t *opts, size_t len, int32_t cur, const moy_ui_over_t *o);
// Keycap hints (`items` NUL-separated pairs of an action or keys and what it
// does, moy_ui_hints) in a band at the page's foot, which its rows then stack
// above; ui.at places them instead. Under the input touch there is no band
// and a ui.at before it is spent.
void moy_ui_api_hints(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items, size_t len,
                      const moy_ui_over_t *o);
// The width those hints take with the input in use, their margin at either
// end included (0 under touch): what a band placed to end where they do is.
int moy_ui_api_hints_w(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items,
                       size_t len);
// The next row (a label, info, link, cycle, seg, swatch picker, toggle,
// slider or progress) draws icon `name` before its label: the live icon
// set's slot of that name (moy_ui_icon_slot), else the glyph table's.
// Answers whether either names it.
int moy_ui_api_icon(moy_ui_ctx_t *c, moy_ui_span_t name);
// A row showing `v` of `hi` done as a bar and its percent at the right; no stop.
void moy_ui_api_progress(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label, int32_t v,
                         int32_t hi, const moy_ui_over_t *o);
// A table (§1): `cols` its headers and `cells` its rows' cells row by row
// (both NUL-separated), `cur` the current row (from 0, -1 none), which
// follows its focus. out[0] the new cur, out[1] 1 the frame a current row is
// opened (Enter on it, or a tap on the row already current).
void moy_ui_api_table(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                      const uint8_t *cols, size_t cols_n, const uint8_t *cells, size_t cells_n,
                      int32_t cur, const moy_ui_over_t *o, int32_t out[2]);
// A scroll region (§1): `id` begins one in ui.at's rect (else the page's
// room below its rows), whose stacked rows scroll in it; an id of length 0
// ends it.
void moy_ui_api_scroll(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                       const moy_ui_over_t *o);
// A text field (§1) editing the `len` bytes at `buf` (`cap` its room) while
// focused: answers the new length, plus MOY_UI_FIELD_DONE the frame Enter
// is pressed in it.
#define MOY_UI_FIELD_DONE 0x10000
int32_t moy_ui_api_field(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                         uint8_t *buf, size_t cap, size_t len, const moy_ui_over_t *o);
// A dialog (§1): `title` begins a modal card centred on the surface, its
// `buttons` (NUL-separated, the last of `kind`, the others normal) at its
// foot, its body the rows stacked until a title of length 0 ends it. Answers
// the button activated this frame (from 0), -1 none; the end answers -1.
int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o);
// A popover (§1): `id` begins a card hung under ui.at's rect (else under the
// stop that holds focus as it opens), above it where the surface has no room
// below, `w` wide (0: the rect's width), its body the rows stacked until an
// id of length 0 ends it. While it is drawn nothing outside it takes input,
// as under a dialog. Answers 1, or 0 the frame a press outside the card or
// Esc dismissed it; the end answers 0.
#define MOY_UI_KEY_ESC 27
int moy_ui_api_popover(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w,
                       const moy_ui_over_t *o);
// A menu (§1): its caption (`label`; of length 0 none, and then a rule when
// rows sit above it in its page), then a row a stop per item of `items`
// (NUL-separated, `len` bytes), the icon named at the same place in `icons`
// (NUL-separated, `ilen` bytes; an empty name, or none, no icon) before its
// label. An item's id is its text. Stacked on the page, or from ui.at's
// rect's top left across its width. Answers the item activated this frame
// (from 0), -1 none. The rows moy_ui_api_menu_danger named last are danger
// rows: their label and icon in the danger buttons' ink.
int32_t moy_ui_api_menu(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, const uint8_t *icons, size_t ilen,
                        const moy_ui_over_t *o);
// The next ui.menu's danger rows (a verb that destroys: Delete): bit i its row i.
void moy_ui_api_menu_danger(moy_ui_ctx_t *c, uint32_t rows);
// A card grid (§1): `id` begins one whose cards' pictures are w x h, as
// many columns as the row holds, the grid's rows stacking on the page (or
// from ui.at's rect's top left, across its width); an id of length 0 ends it.
void moy_ui_api_grid(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o);
// A card in the open grid: `title` (its id) and `sub` under its picture, the
// current mark where o->current. out: the picture's x, y, w, h -- the app
// draws into it before its next ui call -- and the state bits (§5).
void moy_ui_api_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t sub, const moy_ui_over_t *o, int32_t out[5]);
// Whether the frame drawn last laid a dialog or a popover out from a stale measure, so
// the next frame is owed though no input came.
int moy_ui_api_owes(const moy_ui_ctx_t *c);
// out: x, y, w, h, state.
void moy_ui_api_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o, int32_t out[5]);
// Focus moved to `id` (id.p NULL: read only). The focused id's text into
// `buf` (NUL-terminated within `cap`); answers its length, -1 with none.
int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap);
int moy_ui_api_keys_taken(const moy_ui_ctx_t *c);
void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h);
// Where stop `id` drew in the frame drawn last (the host's: a popover beside
// a row, a test's tap), or the card of the popover `id` drawn then: its box
// into out, 1; 0 where it drew no such stop or popover.
int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]);
// A duplicate id drawn this frame, its text, once per id (NULL none).
const char *moy_ui_api_dup(moy_ui_ctx_t *c);

#endif // MOY_UI_API_H
