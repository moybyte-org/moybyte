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
#define MOY_UI_API_TIP 48           // a tooltip's or a lent title's text, NUL-terminated
#define MOY_UI_API_CHIP 12          // a lent chip's words (ui.bar), NUL-terminated
#define MOY_UI_API_BAR 6            // the tools an app lends its bar

// The input a frame resolves over, the run snapshot's shape: the buttons held
// and pressed this frame (bit i is MOY_INPUT_NAMES[i]: left, right, up, down,
// a, b, run), the pointer (x, y and its flags: LIVE 1, HELD 2, the press edge
// 4), the key typed this frame (9 is Tab) and the modifiers held with it
// (moy_input.h's MOY_INPUT_MOD_*: Shift+Tab steps back), and the wheel's
// notches this frame, across and down (positive right and down): a notch
// down glides the page under the pointer MOY_UI_WHEEL_ROWS rows. No page
// scrolls across, so Shift's wheel, which turns it across, scrolls none.
// Then the loop's tick in ms, the clock the scrolling nests' motion runs on
// (moy_ui.h's moy_ui_scroll_t): the host's injected dt, never a clock read;
// 0, no clock, lands every glide at once and flings nothing.
enum {
    MOY_UI_IN_BTN = 0, MOY_UI_IN_BTNP, MOY_UI_IN_X, MOY_UI_IN_Y, MOY_UI_IN_PTR, MOY_UI_IN_KEY,
    MOY_UI_IN_MODS, MOY_UI_IN_WHEEL_X, MOY_UI_IN_WHEEL_Y, MOY_UI_IN_DT,
    MOY_UI_IN_LEN
};
#define MOY_UI_MOD_SHIFT 2
#define MOY_UI_WHEEL_ROWS 3
// MIDDLE: the pointer's middle button is held (a mouse's), which drags the
// page under it by its travel.
enum { MOY_UI_P_LIVE = 1, MOY_UI_P_HELD = 2, MOY_UI_P_EDGE = 4, MOY_UI_P_MIDDLE = 8 };
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
// -1 absent); then, where the block goes on, the hint's length as a word and
// its text (a field's placeholder), and then a word of flags (bit 0: wash, a
// popover washing what it covers; bit 1: lead, a table whose first row is a
// verb). A wasm app hands it over as a pointer and its length.
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
    moy_ui_span_t hint;             // a field's placeholder (p NULL: none)
    int8_t wash;                    // a popover: wash what it covers (-1 absent)
    int8_t lead;                    // a table: its first row is a verb (-1 absent)
} moy_ui_over_t;
#define MOY_UI_O_WASH 1             // the flags word's bits: `wash`
#define MOY_UI_O_LEAD 2             // and `lead`

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
    // A list's or a table's rows on its page (STOP_ROWS): the focused row
    // and row 0's top on screen, so a key step reveals its row at begin.
    int16_t row, top;
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
    int16_t peek;                   // a popover: the body height its cut peeks a row at, 0 none
    uint8_t seen;
    moy_ui_scroll_t sc;             // a page: its scroll's motion (a drag, a fling, a glide)
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
    uint32_t mid_page;              // the page a middle-button drag scrolls
    int16_t mid_y;
    int16_t tap_x, tap_y;           // where the activating tap was released
    int16_t press_x, press_y, drag_y;
    int16_t last_x, last_y;         // the pointer last frame
    int8_t step_x, step_y;          // a key the focused stop keeps: -1 or 1
    uint8_t keys, reveal, cls, fs;
    uint8_t win;                    // the page lies in a window's body
    uint8_t elsewhere;              // the keys are another surface's: no focus
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
    // The frame's begin moved the focus or a page's scroll (drawn by the
    // pass, owing nothing); the list or table whose key step begin revealed,
    // and the row it revealed.
    uint8_t moved;
    uint8_t jump;                   // the reveal is the app's focus move: no glide
    int16_t step_row;
    uint32_t step_rev;
    // An up press over a page's topmost stop, outside a dialog: the frame's
    // keys went past the page's top (the shell walks focus onto its bar).
    uint8_t edge;
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
    // Its body's cut while its rows lay out (content coordinates, 0: not
    // cut): the deepest row middle at or above it, and whether it already
    // falls well inside a row (a cut body peeks a row under its fade).
    int16_t pop_cut, pop_peek;
    uint8_t pop_inside;
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
    int16_t fin_slot, fin_glyph;
    int8_t fin_tone;
    // The next card's badge and tone (moy_ui_api_card_mark), the next
    // grid's spread (moy_ui_api_grid_spread) and the next label's text
    // options (moy_ui_api_text): each cleared once used and by begin.
    int16_t card_slot, card_glyph, card_blank, card_blank_glyph;
    int8_t card_tone;
    uint8_t spread_cols, spread_wrap, grid_wrap;
    int16_t spread_foot, spread_top, spread_margin;
    int8_t txt_lines, txt_centre, txt_tone, txt_role;
    // The table drawn last: row 0's top (scrolled), the rows' height and
    // count, the view they show in, and its picture column's x and side (0:
    // none).
    int16_t tbl_y0, tbl_rh, tbl_n, tbl_px, tbl_pw;
    moy_ui_box_t tbl_view;
    // The dialog drawn this frame: its id (0: none) and its card.
    uint32_t dlg_id;
    moy_ui_box_t dlg_card;
    // The page's hint band this frame (h 0: none), which a wash leaves clear.
    moy_ui_box_t hint_band;
    // ui.tool's tooltip: the tool a resting pointer is over (w 0: none) and
    // its words, drawn over the page at ui.end.
    moy_ui_box_t tip_at;
    char tip[MOY_UI_API_TIP];
    // ui.bar's: the title and tools the app lends its host's bar, the frame
    // that lent them (bar_seen), and the tool the host's bar activated for
    // the next ui.bar to answer (-1 none).
    char bar_title[MOY_UI_API_TIP];
    int16_t bar_slot[MOY_UI_API_BAR];
    uint8_t bar_n, bar_off, bar_seen, bar_back;
    int8_t bar_act;
    // the one tool lent as words, not an icon (Paint's zoom chip), and its place
    char bar_chip[MOY_UI_API_CHIP];
    int8_t bar_chip_at;
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
// is the window's; MOY_UI_ELSEWHERE: the keys are another surface's, so no
// stop holds focus this frame; and, in the bits from MOY_UI_APP_SHIFT, the row of the
// theme's app_buttons its buttons take plus one, 0 none, which only a shell
// app's host passes) and the font scale. Answers 1 when the frame OWES
// nothing: what its input changed, begin applied before the pass -- a focus
// moved, a page scrolled or a scroll's motion stepped (moy_ui_api_moved) --
// so the pass draws it and the frame after draws the same pixels. A frame
// owes the next for what applies after its pass: an activation, a typed
// key, a press or its release, a value a key stepped, a dismissed popover,
// a pointer that moved over the stops, a layout found stale or a reveal set
// during the pass (moy_ui_api_owes).
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
// Its wash is the surface, less the page's hint band at its foot; with the
// override `wash` false it draws none, where the page under it was washed
// already (`moy_ui_api_wash`, the page an app holds under its dialog).
int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o);
// Washes what a dialog begun now would: the surface, less the page's hint
// band at its foot.
void moy_ui_api_wash(const moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// The box that wash covers (moy_ui_scrim_io washes it over another frame).
moy_ui_box_t moy_ui_api_wash_box(const moy_ui_ctx_t *c);
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
// rows: their label and icon in the theme's menu danger ink (its look's
// `danger_ink`), else the danger buttons' ink.
int32_t moy_ui_api_menu(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t label,
                        const uint8_t *items, size_t len, const uint8_t *icons, size_t ilen,
                        const moy_ui_over_t *o);
// The next ui.menu's danger rows (a verb that destroys: Delete): bit i its row i.
void moy_ui_api_menu_danger(moy_ui_ctx_t *c, uint32_t rows);
// The Python face's options the override block does not carry, each for the
// next call of its verb: a label's text (`lines` its most, 0 one unwrapped;
// `centre` 1 centred, 2 in from the page's edges as a group's caption is, 3
// on a `sunk` inset across the row, the text inside its padding;
// `tone` MOY_UI_TONE_*; `role` a font role, -1 the label's own),
// a card's badge (icon `badge`, empty none), tone and blank picture (icon
// `blank` on a tinted tile, empty none: moy_ui_card_blank), a grid's spread (its
// `cols` columns spread across its rect, the gaps taking the rest; `foot`
// the card's foot under its picture, `top` the room over its first row and
// `margin` its rect's at either side, in pixels at the scale, 0 the style's;
// `wrap` its cards' titles take two lines where their foot holds them).
void moy_ui_api_text(moy_ui_ctx_t *c, int lines, int centre, int tone, int role);
void moy_ui_api_card_mark(moy_ui_ctx_t *c, moy_ui_span_t badge, int tone,
                          moy_ui_span_t blank);
void moy_ui_api_grid_spread(moy_ui_ctx_t *c, int cols, int foot, int top, int margin,
                            int wrap);
// A group with no caption (ui.group's `card`): its rows on its card, from
// the page's next row, until a group call ends it.
void moy_ui_api_group_card(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// An empty page's head (ui.empty) at `at`'s rect, else the page's next row:
// the icon on its disc, the title and the lines (NUL-separated), centred.
// Its foot's y, where the page's verbs go.
int moy_ui_api_empty(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t icon,
                     moy_ui_span_t title, const uint8_t *lines, size_t n);
// A month (ui.month) at `at`'s rect, else stacked on the page: `title`,
// the weekdays' initials `days`, `n` days from column `first` (0 Monday),
// `today` marked (0 none); on the desk and under touch its head carries the
// two chevrons. -1 or 1 the frame a press lands on one, else 0.
int moy_ui_api_month(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                     moy_ui_span_t days, int first, int n, int today);
// A toast (ui.toast) over the foot of `at`'s rect, else of the page: the
// `check` icon, else `kind`'s (the icon slot it names), and `text`; `flat`
// with no soft shadow. Its pill's box in out[4].
void moy_ui_api_toast(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t text,
                      moy_ui_span_t kind, int flat, int32_t out[4]);
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
// The table drawn last this frame (ui.table): out row 0's top (scrolled),
// the rows' height, the view's x, y, w and h, the picture column's x and
// side, where an app draws a row's own picture over its cell; 0 where that
// table has no picture column or none was drawn.
int moy_ui_api_table_rows(const moy_ui_ctx_t *c, int32_t out[8]);
// The table drawn last, its pictures drawn: the fades at the edges its rows
// run on past, again over its picture column, so a cut row's picture fades
// with its row (Files' covers).
void moy_ui_api_table_pics(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// The page's end, after its rows: where they run on below what shows, a
// fade at the foot of the page, over the hint band's top edge where the
// page has one; and over everything, the tooltip of a tool a resting
// pointer is over (ui.tool).
void moy_ui_api_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// A tool (§1): an icon button in ui.at's rect (else a row of the page),
// flat but for its marks: the current mark where o->current, the focus mark
// by its size, at half strength where o->disabled. `label` is its word,
// its text after a tab the key it answers to: on the handheld the word
// stands under the icon; on the desk it is the tooltip, with the key, that a
// resting pointer shows under the tool (drawn at ui.end). Answers true the
// frame it is activated.
int moy_ui_api_tool(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t icon,
                    moy_ui_span_t label, const moy_ui_over_t *o);
// The app's bar (§1): `title` and the tools (`tools` NUL-separated icon slot
// names -- one that names no icon is a chip of those words, `len` bytes, at most MOY_UI_API_BAR; bit i of `off` the tool i drawn
// at half strength, which nothing activates) lent to the host's bar for this
// frame; the host draws them (moy_ui_api_bar_lent) and taps them
// (moy_ui_api_bar_tap). MOY_UI_BAR_BACK in `off`: the app has a page to step
// back from, so the bar's way back is the app's. Answers the tool the host's
// bar activated since the last frame (from 0), MOY_UI_BAR_UP its way back,
// -1 none.
#define MOY_UI_BAR_BACK 0x80000000u
#define MOY_UI_BAR_UP (-2)
int32_t moy_ui_api_bar(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                       const uint8_t *tools, size_t len, uint32_t off);
// A rule (§1): a line in the structure colour, one pixel at the scale
// across ui.at's rect (down it where the rect is taller than wide), else a
// row of the page's with the line across its middle.
void moy_ui_api_rule(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// The host's side of ui.bar: whether the frame drawn last lent a bar, its
// title then, its tools' slots into `slots` (room for MOY_UI_API_BAR) and
// their count into *n, the half-strength bits and MOY_UI_BAR_BACK into *off.
int moy_ui_api_bar_lent(const moy_ui_ctx_t *c, const char **title, int16_t *slots, int *n,
                        uint32_t *off);
// The words of the one tool the bar lent last that names no icon (a compact
// cycle's chip, Paint's zoom), "" for none; its place among the tools at *at.
const char *moy_ui_api_bar_chip(const moy_ui_ctx_t *c, int *at);
// The host's bar activated tool `i` of the bar lent last (MOY_UI_BAR_UP its
// way back); the next ui.bar answers it.
void moy_ui_api_bar_tap(moy_ui_ctx_t *c, int i);
// Whether the frame drawn last owes the next though no input comes: a
// dialog or a popover laid out from a stale measure, or a reveal its pass
// set (a list's or a table's row stepped where begin could not reveal it, a
// focus moved by the app).
int moy_ui_api_owes(const moy_ui_ctx_t *c);
// Whether this frame's begin moved the focus or a page's scroll.
int moy_ui_api_moved(const moy_ui_ctx_t *c);
// Whether a page of the context moves on its own -- a fling coasts or a
// glide eases -- so the next frame is due though no input comes: the
// declared animation (docs/surface_model_v1.md §3's Class B) a host's frame
// gate reads.
int moy_ui_api_moving(const moy_ui_ctx_t *c);
// The scroll of the page that lies under (x, y) as the frame drawn last laid
// it out -- the offset a drag at that point moves, in pixels -- or -1 where
// no page lies there. A measuring tool's probe (tools/input_bench.py).
int32_t moy_ui_api_offset(moy_ui_ctx_t *c, int x, int y);
// The fades drawn at the edges lists run on past (a page's foot, a scroll
// region's, a cut popover body's, a table's), in every context since the
// count was last reset; `reset` zeroes it after reading. The review kit's
// net that a list with hidden rows says so.
uint32_t moy_ui_api_fades(int reset);
// out: x, y, w, h, state.
void moy_ui_api_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o, int32_t out[5]);
// Focus moved to `id` (id.p NULL: read only). The focused id's text into
// `buf` (NUL-terminated within `cap`); answers its length, -1 with none.
int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap);
// Focus moved to `id` ahead of the next frame, between frames (a host's):
// that frame's begin shows it, so it owes no frame of its own as a move
// inside a pass does.
void moy_ui_api_focus_ahead(moy_ui_ctx_t *c, moy_ui_span_t id);
int moy_ui_api_keys_taken(const moy_ui_ctx_t *c);
// Whether this frame's up press found no stop above the focused one, outside
// a dialog: the keys went past the page's top.
int moy_ui_api_edge(const moy_ui_ctx_t *c);
void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h);
// Where stop `id` drew in the frame drawn last (the host's: a popover beside
// a row, a test's tap), or the card of the popover `id` drawn then, or of
// the dialog titled `id`, or the button labelled `id` at that dialog's foot:
// its box into out, 1; 0 where it drew no such stop, popover or dialog.
int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]);
// A duplicate id drawn this frame, its text, once per id (NULL none).
const char *moy_ui_api_dup(moy_ui_ctx_t *c);

#endif // MOY_UI_API_H
