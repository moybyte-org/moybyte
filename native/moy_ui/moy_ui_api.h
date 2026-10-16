// The public UI API's engine (docs/ui_api_2026-10.md): the verbs every app
// and cart draws its interface with -- page, label, button, toggle, slider,
// list, group, info, link, cycle, seg, swatches, hints, part, focus,
// keys_taken, at -- and the focus and navigation they share.
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
// the eight words; -1 absent), current, disabled, nav, mark (0 or 1; -1
// absent). A wasm app hands it over as a pointer and its length.
enum {
    MOY_UI_O_FILL = 0, MOY_UI_O_INK, MOY_UI_O_BORDER, MOY_UI_O_ID, MOY_UI_O_CURRENT,
    MOY_UI_O_DISABLED, MOY_UI_O_NAV, MOY_UI_O_MARK, MOY_UI_O_WORDS
};
#define MOY_UI_O_ROLE 0x1000000
#define MOY_UI_O_NONE (-1)

typedef struct {
    int32_t fill, ink, border;      // MOY_UI_O_NONE, RGB, or MOY_UI_O_ROLE + a token
    moy_ui_span_t id;               // p NULL: the label's
    int8_t current, disabled, nav, mark;    // -1 absent
} moy_ui_over_t;

// Every key absent.
void moy_ui_over_none(moy_ui_over_t *o);
// The block of `n` bytes at `w` (NULL or 0: every key absent) into *o, its id
// pointing into the block. MOY_UI_BAD where it is short or its id overruns.
int moy_ui_over_block(moy_ui_over_t *o, const uint8_t *w, size_t n);

typedef struct {
    uint32_t id;
    moy_ui_box_t b;                 // on screen, as drawn
    uint8_t flags;                  // STOP_* (moy_ui_api.c)
    uint8_t page;                   // the index of its page's stop, or 0xff
} moy_ui_stop_t;

typedef struct {
    uint32_t id;
    int32_t a, b;                   // a page: its scroll, its rows' height; a list: its row
    uint8_t seen;
} moy_ui_keep_t;

typedef struct {
    uint32_t magic;
    int32_t in[MOY_UI_IN_LEN];
    int32_t last_key, last_ptr;
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
    uint8_t n_prev, n_cur, has_at, gesture;    // gesture: 0 none, 1 down, 2 a drag
    moy_ui_stop_t prev[MOY_UI_API_STOPS], cur[MOY_UI_API_STOPS];
    moy_ui_keep_t keep[MOY_UI_API_KEEP];
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
    // rows stacked on it, its last row's box and that row's marks.
    int16_t grp_top;
    uint16_t grp_flags;
    uint8_t grp_open, grp_n, grp_any, grp_first;
    moy_ui_box_t grp_last;
} moy_ui_ctx_t;

// A context's size, for a face that owns it as bytes; and a fresh one.
size_t moy_ui_api_size(void);
void moy_ui_api_reset(moy_ui_ctx_t *c);
// A context the bytes at `p` hold (resetting them the first time), or NULL
// where `n` is short or `p` misaligned.
moy_ui_ctx_t *moy_ui_api_ctx(void *p, size_t n);

// A frame begins: `in` (MOY_UI_IN_LEN words, NULL none), the surface a page
// fills, the size class (0 the handheld's rows, 1 the desk's) and the font
// scale. Answers 1 when the frame is SETTLED: its input moved no focus,
// activated nothing, pressed nothing and scrolled nothing, so a page drawn
// again draws the same pixels.
int moy_ui_api_begin(moy_ui_ctx_t *c, const int32_t *in, moy_ui_box_t surface, int cls, int fs);

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
// MOY_UI_V_CYCLE and MOY_UI_V_SEG take `opts` NUL-separated, MOY_UI_V_SWATCH
// `n` colours (0xRRGGBB, little-endian words). Left and right are its own
// while focused (a cycle wraps); activation takes the next option, a tap the
// one tapped. Answers the new cur.
int32_t moy_ui_api_choice(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind, moy_ui_span_t label,
                          const uint8_t *opts, size_t len, int32_t cur, const moy_ui_over_t *o);
// Keycap hints (`items` NUL-separated key and action pairs) in a band at the
// page's foot, which its rows then stack above; ui.at places them instead.
void moy_ui_api_hints(moy_ui_ctx_t *c, const moy_ui_sink_t *s, const uint8_t *items, size_t len,
                      const moy_ui_over_t *o);
// out: x, y, w, h, state.
void moy_ui_api_part(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_span_t id, int w, int h,
                     const moy_ui_over_t *o, int32_t out[5]);
// Focus moved to `id` (id.p NULL: read only). The focused id's text into
// `buf` (NUL-terminated within `cap`); answers its length, -1 with none.
int moy_ui_api_focus(moy_ui_ctx_t *c, moy_ui_span_t id, char *buf, size_t cap);
int moy_ui_api_keys_taken(const moy_ui_ctx_t *c);
void moy_ui_api_at(moy_ui_ctx_t *c, int x, int y, int w, int h);
// A duplicate id drawn this frame, its text, once per id (NULL none).
const char *moy_ui_api_dup(moy_ui_ctx_t *c);

#endif // MOY_UI_API_H
