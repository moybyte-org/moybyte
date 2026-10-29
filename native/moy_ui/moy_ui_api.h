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
#include "moy_ui_text.h"

#define MOY_UI_API_STOPS 40         // the stops one frame records
#define MOY_UI_API_KEEP 16          // the ids whose state is kept
#define MOY_UI_API_CAPS 16          // the group captions one frame records
#define MOY_UI_API_NAME 32          // the focused id's text, NUL-terminated
#define MOY_UI_API_TIP 48           // a tooltip's or a lent title's text, NUL-terminated
#define MOY_UI_API_CHIP 12          // a lent chip's words (ui.bar), NUL-terminated
#define MOY_UI_API_BAR 6            // the tools an app lends its bar
#define MOY_UI_API_FLOOR 24         // the rects a shift frame's floor holds
#define MOY_UI_API_HOST 6           // the rects a host's rows frame names (moy_ui_api_shift_rows)
#define MOY_UI_API_TROWS 48         // the rows of a carried table a frame keeps the record of

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
// 0, no clock, lands every glide at once and flings nothing. Then the
// steps this frame beyond the press edges (a held key's repeats, the
// trackball's steps; moy_input_rep_word): four 8-bit counts, left, right <<
// 8, up << 16, down << 24, each a step as a press of its direction is; and a
// held key's typing repeats: the byte in the low 16 bits and the count above
// (MOY_UI_KEYREP). A repeat is a step, never a press: nothing activates,
// opens or commits on one.
enum {
    MOY_UI_IN_BTN = 0, MOY_UI_IN_BTNP, MOY_UI_IN_X, MOY_UI_IN_Y, MOY_UI_IN_PTR, MOY_UI_IN_KEY,
    MOY_UI_IN_MODS, MOY_UI_IN_WHEEL_X, MOY_UI_IN_WHEEL_Y, MOY_UI_IN_DT, MOY_UI_IN_REP,
    MOY_UI_IN_KEYREP,
    MOY_UI_IN_LEN
};
#define MOY_UI_KEYREP(byte, n) ((int32_t)(byte) | ((int32_t)(n) << 16))
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
// popover washing what it covers, a dialog over a page washed already where
// clear; bit 1: lead, a table whose first row is a verb; bit 2: gen, a word
// follows: the stop's content generation, §3.3 of
// docs/scroll_and_text_2026-10.md; bit 3: wash not given, bit 0 then
// ignored). A wasm app hands it over as a pointer and its length.
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
    int8_t has_gen;                 // `gen` given: 1, else 0
    int32_t gen;                    // what the app draws in the stop is a function of it
    // A table: its column 0 is a part `part_w` wide (0: the room its text
    // columns leave), its rows `part_h` tall (0: the theme's), where has_part.
    int8_t has_part;
    int16_t part_w, part_h;
    int8_t bar;                     // a table: its current row wears the cursor bar (-1 absent)
} moy_ui_over_t;
#define MOY_UI_O_WASH 1             // the flags word's bits: `wash`
#define MOY_UI_O_LEAD 2             // and `lead`
#define MOY_UI_O_GEN 4              // and `gen`, whose word follows
#define MOY_UI_O_NO_WASH 8          // and `wash` not given (it stays absent)
#define MOY_UI_O_PART 16            // and `part`, whose two words (w, h) follow the gen's
#define MOY_UI_O_BAR 32             // and `bar`

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
    uint8_t st;                     // the state bits it drew with (MOY_UI_ST_*)
    int32_t gen;                    // the app's generation for what it draws in it
    uint32_t sig;                   // its record (kind, text, value), under the audit
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
// What a verb that opens a nest (ui.scroll, ui.text_view, ui.dialog,
// ui.popover) answers when MOY_UI_API_NEST are open already: it opens none
// and draws nothing, and each face reports it (Python and Lua raise, a
// compiled app's import answers -MOY_UI_DEEP).
#define MOY_UI_API_DEEP INT32_MIN

typedef struct {
    uint32_t id;
    int32_t a, b;                   // a page: its scroll, its rows' height; a list: its row
    int16_t over;                   // a page: how far a scroll into view went past its rows
    int16_t foot;                   // a page: the hint band its foot gave up last frame
    int16_t peek;                   // a popover: the body height its cut peeks a row at, 0 none
    uint8_t seen;
    moy_ui_scroll_t sc;             // a page: its scroll's motion (a drag, a fling, a glide)
    // A page as its last pass drew it (moy_ui_api_shift_plan): the scroll it
    // drew at, the generation the app gave it, what it holds (MOY_UI_KD_*) and
    // the stops drawn inside it, from s0, sn of them.
    int32_t drawn, gen;
    uint8_t kd, s0, sn;
    int16_t rh;                     // a placed table: its row height as it drew
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
    int8_t step_x, step_y;          // the steps a key the focused stop keeps makes, signed
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
    uint16_t lost, lost_told;       // the stops past MOY_UI_API_STOPS this frame, the last told
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
    int32_t typed_n;                // ...and how many times (a held key's repeats)
    // ui.popover's: the card drawn this frame and last (w 0: none), the
    // rect it hangs from, and whether this frame's input dismissed it (a
    // press outside the card drawn last, or Esc).
    moy_ui_box_t pop_card, pop_prev, pop_at;
    uint8_t pop_gone;
    uint8_t tip_shown;              // ui.end drew a tooltip this frame
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
    // The table that carries its rows (a placed table with a part, one a
    // context), as its last pass drew them: its id, its columns' geometry,
    // its view, scroll, row height and count, its current row and whether
    // it wore the focus mark, and the record (its cells, hashed) of rows
    // tc_r0 .. tc_r0 + tc_k - 1 (tc_k -1: none kept), so a frame draws a
    // row again only where its record or its marks changed.
    uint32_t tc_id, tc_geo;
    moy_ui_box_t tc_view;
    int32_t tc_off;
    int16_t tc_rh, tc_n, tc_cur, tc_r0, tc_k, tc_y0;
    uint8_t tc_mark;
    uint32_t tc_h[MOY_UI_API_TROWS];
    // The parts the table drawn last answers (moy_ui_api_table_part): its
    // id, the view its rows show in, row 0's top on screen, the row height,
    // the next row and the end, the part's x and width; whether one is open
    // (tp_row its row), its fades owed; row 0's top the last frame drew, and
    // the rows answered from the first that shows (bit i: row tp_v0 + i),
    // so the stops in a carried row's part are carried with it.
    uint32_t tp_id;
    moy_ui_box_t tp_view;
    int16_t tp_y0, tp_rh, tp_i, tp_e, tp_x, tp_w, tp_n, tp_row, tp_py0, tp_v0;
    int32_t tp_off;
    uint8_t tp_open, tp_fade, tp_carry;
    uint64_t tp_ans;
    // The dialog drawn this frame: its id (0: none) and its card; whether
    // it stands over a held page (`wash` false) under the app's gen
    // (dlg_held), that gen, its buttons (a hash) and its body's foot on
    // screen. dw_*: the last frame's, and the frames it has stood as it is
    // (dw_n, a dialog's rows frame: moy_ui_api_shift_plan).
    uint32_t dlg_id;
    moy_ui_box_t dlg_card;
    uint8_t dlg_held;
    int32_t dlg_gen;
    uint32_t dlg_btn;
    int16_t dlg_foot;
    uint32_t dw_id;
    moy_ui_box_t dw_card;
    int32_t dw_gen;
    uint32_t dw_btn;
    uint8_t dw_ok, dw_n;
    // The focused field's box last frame where this frame types into it (w
    // 0: none): its row joins a shift frame's floor.
    moy_ui_box_t typed_box;
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
    // A shift frame (docs/scroll_and_text_2026-10.md §3.3): the region R that
    // moved (its keep's id and its box as the last frame drew it), the move
    // (dy, the shift_last a host makes: -1 x what the scroll moved), the plan
    // (MOY_UI_SHIFT_*) and whether R draws a fade at its head; then the
    // floor -- the rects inside R this pass draws, the rest of R being the
    // last frame's pixels moved -- once a host armed it (fl_on): where the
    // pass is in R (0 before it opens, 1 inside, 2 after), whether a stop
    // unlike the last frame's took the floor to R's foot, the rects that
    // still want their ground, the stops R holds, from fl_s0, and the last
    // frame's, from fl_p0, fl_pn of them; whether the pass drew over the
    // page (fl_dirty), how deep in an overlay it is (fl_susp) and whether
    // the floor ran out of rects (fl_lost). at_last: the stop placed last
    // was ui.at's.
    // A dialog's rows frame (sh_dlg) moves nothing: R is the last frame's
    // card and its drop, the floor the rows of its card's safe band
    // (dlg_s0..dlg_s1: the card's rows its rounded corners leave whole),
    // and its draws go through the floor alone. fl_mute: the dialog drawn
    // is not the one planned (its card, title, buttons or gen), so it draws
    // nothing this frame and owes the next. fl_top: something drew over a
    // dialog (a popover, a toast, a tooltip).
    uint32_t sh_id;
    moy_ui_box_t sh_r;
    int16_t sh_dy, sh_rows, sh_pics;
    uint8_t sh_plan, sh_head, sh_dlg, fl_mute, fl_top;
    int16_t dlg_s0, dlg_s1;
    uint8_t fl_on, fl_stage, fl_n, fl_struct, fl_dirty, fl_susp, fl_lost, at_last;
    uint8_t fl_cast;                // the next draw casts a shadow past its box (a card's)
    uint8_t fl_early;               // the floor is the pass's from its start (floor_reach)
    moy_ui_box_t pic_clip;          // the floor's rect the last card's picture draws through
    uint8_t pic_tbl;                // ...or the floor of the placed table drawn last as R
    uint8_t fl_s0, fl_p0, fl_pn;
    // sh_quiet: begin found nothing that draws the frame whole (no press,
    // activation, step, hover, overlay or owed frame), so a host may name the
    // rows its own drawing changed (hr, hr_n of them: moy_ui_api_shift_rows);
    // hover_off: no pointer held but one pressed over no stop
    // (`press_bare`, from its press to the next), and no stop under it this
    // frame or last.
    uint8_t sh_quiet, hr_n, hover_off, press_bare;
    moy_ui_box_t hr[MOY_UI_API_HOST];
    uint32_t fl_pend;
    moy_ui_box_t fl[MOY_UI_API_FLOOR];
    // what the last pass began under, which a shift frame's must equal
    moy_ui_box_t was_surface;
    uint16_t was_flags;
    uint8_t was_fs, was_hint;
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

// ui.text_view(id, lines, o): a text area in rows (moy_ui_text.h), a scroll
// region of its own -- a drag scrolls it and its release coasts, the wheel
// steps it -- in ui.at's rect or stacked `rows` rows tall (0: the rest of
// the page). Its rows are the lines `L` gives: whole lines cut at its
// width, or wrapped there (`wrap`); in the mono face, coloured by `syntax`
// (MOY_UI_SYN_*: SGR escapes by default, a terminal's), numbered where
// `gutter`, a caret at `caret_line`, `caret_col` (-1: none). An editor's
// marks: a selection from (sel_r0, sel_c0) to (sel_r1, sel_c1) (sel_r0 -1:
// none), an error on line `err_line` (-1: none) underlined, its words
// `marks[0]` after it, the matches of the find query `marks[1]` (across
// case where `find_ci`), the current line's mark (`mark`: an editor's view,
// which keeps its caret's line in view itself and draws no fade at its
// foot), the columns cut off at the left (`left`; a change draws the view
// whole). `gen`, where
// the caller gives one (non-zero), names the lines' content: the wrapped
// rows' count is kept against it, else counted every frame. `top` (-1:
// none) aims the view at that line, its first row at the view's top: a
// glide there, or a jump on the view's first frame or a frame with no dt.
// Where `follow`, a view starts at its foot, and at its foot when the text
// grows it glides to the new foot (a log, a terminal). A frame draws only the rows that changed and
// moves the rest, as a scroll region's floor (§3.3 of
// docs/scroll_and_text_2026-10.md). out: the first line shown and the rows
// shown. Its content scrolls at most 32767 pixels: past that, the rows at
// the end are cut.
//
// A view keeps a record by its id (moy_ui_text.h's MOY_UI_TEXT_VIEWS, every
// context's views together, the least recently drawn given up first): the
// rows it drew, which it plans the next frame against, and where they lay,
// which moy_ui_api_text_at reads. Its scroll is its region's, kept by the
// context like every page's.
typedef struct {
    void *src;
    int32_t n;
    const uint8_t *(*get)(void *src, int32_t i, size_t *len);
} moy_ui_lines_t;
enum {
    MOY_UI_TXO_ROWS = 0, MOY_UI_TXO_WRAP, MOY_UI_TXO_SYNTAX, MOY_UI_TXO_FOLLOW,
    MOY_UI_TXO_GUTTER, MOY_UI_TXO_CARET_LINE, MOY_UI_TXO_CARET_COL,
    MOY_UI_TXO_SEL_R0, MOY_UI_TXO_SEL_C0, MOY_UI_TXO_SEL_R1, MOY_UI_TXO_SEL_C1,
    MOY_UI_TXO_ERR_LINE, MOY_UI_TXO_FIND_CI, MOY_UI_TXO_LEFT, MOY_UI_TXO_MARK,
    MOY_UI_TXO_GEN, MOY_UI_TXO_TOP, MOY_UI_TXO_LEN
};
// The options' defaults, in MOY_UI_TXO_* order: what a face that takes
// fewer of them fills the rest with.
#define MOY_UI_TXO_DEFAULTS {0, 0, MOY_UI_SYN_SGR, 0, 0, -1, -1, -1, 0, -1, 0, -1, 0, 0, 0, 0, -1}
// Answers 0, or MOY_UI_API_DEEP.
int32_t moy_ui_api_text_view(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
                             const moy_ui_lines_t *L, const int32_t *opt,
                             const moy_ui_span_t *marks, const moy_ui_over_t *o,
                             int32_t out[2]);
// The text cell under (px, py) in view `id` as its last pass drew it: its
// line and column into out, 1; 0 where the point lies off its rows (a
// column left of the text is 0, past a row's end that row's end where it
// wraps, else the column the point is over).
int moy_ui_api_text_at(const moy_ui_ctx_t *c, moy_ui_span_t id, int px, int py,
                       int32_t out[2]);
// Rows y0..y1 of view `id` were drawn over after its pass this frame (an
// overlay the pass does not draw): its next frame draws them again.
void moy_ui_api_text_cover(const moy_ui_ctx_t *c, moy_ui_span_t id, int y0, int y1);
// The colour indices the syntax classes take under the live theme
// (MOY_UI_TT_KEYWORD .. MOY_UI_TT_BUILTIN, the theme's code_* content roles;
// TT_TEXT the code's ink), -1 where a role falls back to a token.
void moy_ui_text_tones(const int16_t tones[6]);

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
// ends it. Answers 0, or MOY_UI_API_DEEP.
int32_t moy_ui_api_scroll(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id,
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
// the button activated this frame (from 0), -1 none, or MOY_UI_API_DEEP; the
// end answers -1.
// Its wash is the surface, less the page's hint band at its foot; with the
// override `wash` false it draws none, where the page under it was washed
// already (`moy_ui_api_wash`, the page an app holds under its dialog).
int32_t moy_ui_api_dialog(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t title,
                          const uint8_t *buttons, size_t len, int kind, const moy_ui_over_t *o);
// Washes what a dialog begun now would: the surface, less the page's hint
// band at its foot.
void moy_ui_api_wash(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
// The box that wash covers (moy_ui_scrim_io washes it over another frame).
moy_ui_box_t moy_ui_api_wash_box(const moy_ui_ctx_t *c);
// A popover (§1): `id` begins a card hung under ui.at's rect (else under the
// stop that holds focus as it opens), above it where the surface has no room
// below, `w` wide (0: the rect's width), its body the rows stacked until an
// id of length 0 ends it. While it is drawn nothing outside it takes input,
// as under a dialog. Answers 1, or 0 the frame a press outside the card or
// Esc dismissed it, or MOY_UI_API_DEEP; the end answers 0.
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
// The next part of the table drawn last (one given o->part) this frame
// draws: its row (from 0), its cell's x, y, w and h into out[0..3] and what
// shows of it (the clip the app draws its part under) into out[4..7], the
// ids of the verbs drawn until the next call scoped by the row; -1 after
// the last, the table's fades drawn over its parts. A frame drawn whole
// answers the rows that show; a shift frame only the rows its floor holds
// (those that entered, and those whose cells or marks changed): what an
// app draws in a row's part is a function of that row's cells.
int32_t moy_ui_api_table_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int32_t out[8]);
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
// Whether the pointer is down on a stop its release may activate: a press
// that has not become a page's drag (nor caught a fling, which taps nothing).
int moy_ui_api_tap_armed(const moy_ui_ctx_t *c);
void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h);
// Where stop `id` drew in the frame drawn last (the host's: a popover beside
// a row, a test's tap), or the card of the popover `id` drawn then, or of
// the dialog titled `id`, or the button labelled `id` at that dialog's foot:
// its box into out, 1; 0 where it drew no such stop, popover or dialog.
int moy_ui_api_box(const moy_ui_ctx_t *c, moy_ui_span_t id, int32_t out[4]);
// A duplicate id drawn this frame, its text, once per id (NULL none).
const char *moy_ui_api_dup(moy_ui_ctx_t *c);
// The stops the frame drew past the MOY_UI_API_STOPS it records (each drawn,
// and taking no focus or input), once per count that differs from the one
// told last; 0 otherwise.
int moy_ui_api_lost(moy_ui_ctx_t *c);

// What the pass drew over the app's own rows (a text view's overlay) into
// out (x, y, w, h; w 0: none): the popover's card, or the whole surface
// where ui.end drew a tooltip.
int moy_ui_api_pop_box(const moy_ui_ctx_t *c, int32_t out[4]);

// -- shift frames (docs/scroll_and_text_2026-10.md §3.3) ---------------------------
//
// A frame whose change is one region R's scroll plus rows the engine knows
// of draws R's rows only where they changed: its host moves the pixels the
// last frame drew in R by the scroll's move (a canvas's shift_last) and the
// pass draws the FLOOR -- the band the move exposed, the rows whose marks
// moved, the fades -- leaving the rest of R as moved. What licenses it is
// known at begin: the input the engine applied (a drag, a fling's or a
// glide's step, a focus move, a key typed into a field), the generation
// every page and picture inside R carries (`gen` in the override block), and
// the last pass having drawn R whole with nothing over it. A page with no
// gen, a stop placed by ui.at inside R, a part with no gen, a dialog, a
// popover, a press, an activation, a stepped value, a hover, a tooltip: the
// frame draws whole. The floor only widens during the pass: a stop that
// differs from the last frame's draws from its top to R's foot (but for one
// ui.at placed both frames that moved where the floor holds both its
// boxes, which moves nothing else), a picture
// whose gen moved draws its own box, a stop gone from R's tail leaves the
// ground. What a stop outside R draws into it (a box reaching in, a mark's
// reach) is floor where R moved or its marks changed, and where such a stop
// draws before R opens the floor is the pass's from its start.
enum { MOY_UI_SHIFT_NONE = 0, MOY_UI_SHIFT_MOVE = 1, MOY_UI_SHIFT_ROWS = 2 };
#define MOY_UI_SHIFT_WIDE 8         // moy_ui_api_shift_drawn: the floor took R from a stop down
// What a page holds, for its next frame's plan (moy_ui_keep_t's kd).
enum {
    MOY_UI_KD_DRAWN = 1,            // a page whose pass drew it
    MOY_UI_KD_GEN = 2,              // ...under a generation
    MOY_UI_KD_BAD = 4,              // ...holding what no shift may move (MOY_UI_SHIFT_NONE)
    MOY_UI_KD_PAGE = 8,             // a page (or a placed table's rows): its `a` is a scroll
    MOY_UI_KD_TABLE = 16,           // ...a placed table's rows, R its view
    MOY_UI_KD_AT = 32,              // ...holding a stop ui.at placed, which no move carries
};
// After begin: the frame the engine licenses (MOY_UI_SHIFT_*). out: R's x,
// y, w, h on screen as the last frame drew it, dy, the shift that brings
// that frame's pixels to this one's scroll (0 on a rows frame), the rows of
// R its floor will draw as begin can know them (the floor only widens), the
// rows of R's pictures the floor leaves undrawn (summed per picture), and 0
// for a page, else a dialog's rows frame: the frames its card has stood as it
// is (at most 255). A dialog over a held page (`wash` false) whose app gives
// it a gen draws its rows alone on a frame whose input only typed into its
// field or moved its focus: R is its card and the drop under it as the last
// frame drew them, the floor the rows that input changed, and nothing outside
// R is the dialog's to draw -- the page under it is the host's, which puts it
// back on any other frame. A dialog that comes up otherwise than planned (its
// card, title, buttons, `wash` or gen) draws nothing that frame and owes the
// next.
int moy_ui_api_shift_plan(const moy_ui_ctx_t *c, int32_t out[8]);
// The host moved R's pixels (or the frame is a rows frame, whose R holds the
// last frame's as they are): this frame's pass draws R's floor alone.
void moy_ui_api_shift_arm(moy_ui_ctx_t *c);
// What the pass begun last drew (MOY_UI_SHIFT_*: NONE a whole frame, with
// MOY_UI_SHIFT_WIDE where a stop unlike the last frame's widened the floor
// to R's foot), and into *clean whether it left R's pixels the page's alone
// (nothing drawn over it: a tooltip, a toast, a dialog, a popover).
int moy_ui_api_shift_drawn(const moy_ui_ctx_t *c, int *clean);
// The floor's rects as the pass has them, x, y, w, h each, `cap` at most:
// their count, -1 where the pass draws whole (a test's probe).
int moy_ui_api_floor(const moy_ui_ctx_t *c, int32_t *out, int cap);
// The floor's bounding rect as the pass drew it, x, y, w, h on screen into
// out: 1, or 0 where the pass drew whole (or drew no floor at all).
int moy_ui_api_floor_box(const moy_ui_ctx_t *c, int32_t out[4]);
// A rows frame its host asks for (docs/scroll_and_text_2026-10.md §5): the
// host draws part of the page itself (a cart's editor handle, which is not a
// stop) and names the rects of it its drawing will change this frame, `n` of
// them as x, y, w, h each, MOY_UI_API_HOST at most. Called after begin, where
// begin found the frame quiet and planned no move (a rows frame it planned
// gives way to this one, whose floor is built over the host's page): R is the page the
// last pass drew under its gen, holding every rect, with no page scrolling;
// a page with stops ui.at placed qualifies, since nothing moves. The floor
// is what begin's input changes (the marks, a typed field) and the rects,
// each wanting the page's ground. Answers MOY_UI_SHIFT_ROWS, its plan as
// moy_ui_api_shift_plan's into out, or MOY_UI_SHIFT_NONE where the frame
// draws whole (no such page, a rect outside it, a floor that is most of it).
int moy_ui_api_shift_rows(moy_ui_ctx_t *c, const int32_t *r, int n, int32_t out[8]);
// While R is open on an armed frame: rect x, y, w, h joins the floor, and
// the ground every rect of the floor still wants is filled now, so the host
// draws over the page's ground what it changed there. 1 where it did; 0
// where the frame draws whole or R is not open (the host draws whole).
int moy_ui_api_floor_take(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int x, int y, int w, int h);
// The frame draws whole from here: a host saw an op that ignores the floor
// (a cart's cls) land on its page. The pass after it draws every stop.
void moy_ui_api_shift_break(moy_ui_ctx_t *c);
// ui.card_clip(rect): `r` (x, y, w, h, in and out) cut to the floor's rect
// the picture the last card or part answered lies in, which is where the
// app draws it on a shift frame; 0, `r` as it was, where the frame draws
// whole. An app that gives a picture a gen clips its picture to it. After
// a placed table that was R, `r` is a picture on its rows: cut to the rect
// of the floor its row lies in (the floor is whole rows there), else to
// nothing.
int moy_ui_api_card_clip(const moy_ui_ctx_t *c, int32_t r[4]);
// ui.band(): the rows of the page being laid out that this frame draws, as
// x, y, w, h in the page's coordinates (y from its rows' top): the floor's
// bounding rect on a shift frame, else what shows of the page. 0 where no
// page is open.
int moy_ui_api_band(const moy_ui_ctx_t *c, int32_t out[4]);
// ui.keep(id, h): a stop the app does not draw this frame (it lies outside
// the band), carried forward as the last frame drew it -- a card of the open
// grid, else a row `h` high -- so layout, focus and the shift frame's checks
// see it. Answers its state bits.
int moy_ui_api_keep(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int h);
// The L8 record assertion's net (tests only): with `on` 1 each stop's record
// is kept, and a shift frame whose stops differ from the last frame's
// outside the floor, or that draws a stop outside R reaching into it with
// the part of R it reaches not the floor's, counts a failure; answers the failures since the last
// call and zeroes them (`on` -1 reads, leaving the switch as it is).
uint32_t moy_ui_api_audit(int on);

#endif // MOY_UI_API_H
