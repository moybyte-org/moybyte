// The public UI API's RUN face (docs/ui_api_2026-10.md): the verbs a Lua
// cart calls as `ui.*` (native/moycore/moycore_ui.h) and the `ui` rows of
// native/os_calls.json a compiled app imports from `moybyte.app` (`ui_<verb>`, named
// by native/moy_app/moy_app_wasm.c's table), over native/moy_ui/moy_ui_api.h.
//
// A verb draws into the run's draw target through the RUN sink: the canvas a
// cart's own verbs draw on (for a compiled app moycore_ui_target, an owed or
// kept frame on the screen written in first, as libmoy's verbs do), lent to
// the primitives as a canvas row of its own -- its pixels, its size, the
// whole canvas as the clip, no camera, the canvas's index-to-pixel words as
// the palette. How it draws is the snapshot's face word (SNAP_FACE, the
// console's Player.ui_face), so a run's page is the page a Python app's frame
// draws with the same calls: the font scale of the canvas, the desk's rows,
// and for a responsive app (one that defines `_layout`) the shell's look --
// a ROW sink in MOY_UI_LOOK_APP, the theme's faces and icons, its block at
// the palette's 64..127 -- where a game's or a fixed app's draws as a cart's,
// in petme128 and the look below. Around each
// verb the run's libmoy draw state (camera, clip, pal, palt, the screen
// palette, fillp) is saved, reset, and restored after, so a widget looks the
// same whatever the cart left set and the cart finds its state as it left it.
//
// A cart's colours are the RUN look's (MOY_UI_LOOK_RUN): the style resolved
// against the live theme's token row, rebuilt when its generation moves and
// read under native/moy_app's seqlock, so a verb runs on the run's own thread
// with no hop and no upcall.
//
// The engine's state is the RUN's own, not the app's memory: one context
// (one run at a time), reset when a run opens (moycore_ui_snap's serial) and
// begun by the first verb of each tick over the run's snapshot, so a page
// moves focus and takes its keys from the input the cart's btnp reads.

#ifndef MOY_UI_WASM_H
#define MOY_UI_WASM_H

#include <stdint.h>

#include "moy_ui.h"

#include "moy_ui_api.h"

// MOY_UI_WASM_ROWS, each row's prototype (which must be the ones below) and
// the natives by import name, generated from native/os_calls.json.
#include "moy_ui_wasm_rows.h"

// The run's draw target, from the module that drives the run (moycore_wasm.c on
// an image, runtime/host/moyhost_wasm.c on the host): a libmoy moy_canvas, or NULL
// while no compiled run is open.
void *moycore_ui_target(void);

// The run's snapshot (native/moycore/moycore_run.h: SNAP_LEN words, NULL
// with no run), the tick it is of and the run's serial: moycore_run.c's.
const int32_t *moycore_ui_snap(uint32_t *tick, uint32_t *serial);
// The run's ticks' dt in ms since the last call (the host's injected clock,
// moycore_run.c's), which the frame begun next takes as its motion's.
int32_t moycore_ui_dt_take(void);
// The snapshot's slots the engine reads (moycore_run.h's SNAP_*).
enum {
    MOYCORE_UI_SNAP_BTN = 0, MOYCORE_UI_SNAP_BTNP = 1, MOYCORE_UI_SNAP_X = 6,
    MOYCORE_UI_SNAP_Y = 7, MOYCORE_UI_SNAP_PTR = 8, MOYCORE_UI_SNAP_KEY = 10,
    MOYCORE_UI_SNAP_FACE = 14, MOYCORE_UI_SNAP_ROOM_X = 15, MOYCORE_UI_SNAP_ROOM_Y = 16,
    MOYCORE_UI_SNAP_ROOM_W = 17, MOYCORE_UI_SNAP_ROOM_H = 18
};
// The face word's bits: the surface is a window's body (the page's ground is
// the window's), the frame lays out on the desk's rows (MOY_UI_DESK), it is a
// responsive app's (the shell's look), the board's tap floor stands
// (moy_ui.h's MOY_UI_TAP), and from FS_SHIFT the font scale its text draws at
// (0: 1). A game's is its window bit alone.
enum {
    MOYCORE_UI_FACE_WINDOW = 1, MOYCORE_UI_FACE_DESK = 2, MOYCORE_UI_FACE_LOOK = 4,
    MOYCORE_UI_FACE_TAP = 8, MOYCORE_UI_FACE_FS_SHIFT = 8
};

// The verbs over `target`, a libmoy moy_canvas (NULL: -MOY_UI_STALE). 0 or a
// moy_ui.h code negated (-MOY_UI_DEEP: a scroll, text view, dialog or
// popover opened with MOY_UI_API_NEST open already); an answer is written at
// `out`.
int moy_ui_run_page(void *target, moy_ui_span_t title, const moy_ui_over_t *o, int32_t out[4]);
int moy_ui_run_label(void *target, moy_ui_span_t text, const moy_ui_over_t *o);
int moy_ui_run_button(void *target, moy_ui_span_t label, int kind, const moy_ui_over_t *o,
                      int32_t *out);
int moy_ui_run_toggle(void *target, moy_ui_span_t label, int on, const moy_ui_over_t *o,
                      int32_t *out);
int moy_ui_run_slider(void *target, moy_ui_span_t label, int32_t v, int32_t lo, int32_t hi,
                      const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_list(void *target, moy_ui_span_t label, const uint8_t *items, size_t n,
                    int32_t cur, const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_part(void *target, moy_ui_span_t id, int w, int h, const moy_ui_over_t *o,
                    int32_t out[5]);
// Where the pointer is inside the part answered last (moy_ui_api_part_at):
// 1 with x, y at out, else 0.
int moy_ui_run_part_at(void *target, int32_t out[2]);
// The panes (moy_ui_api_split): the focused pane at *cur, the panes' rects
// and the drag's words at out, a divider let go after a drag writing `sizes`.
int moy_ui_run_split(void *target, moy_ui_span_t id, int32_t *sizes, int n, const int32_t *mins,
                     const int32_t *opt, int32_t *out, int32_t *cur);
int moy_ui_run_focus(void *target, moy_ui_span_t id, char *buf, size_t cap);
int moy_ui_run_keys_taken(void *target);
void moy_ui_run_at(void *target, int x, int y, int w, int h);
int moy_ui_run_group(void *target, moy_ui_span_t caption, const moy_ui_over_t *o);
// `kind` MOY_UI_V_INFO or MOY_UI_V_LINK (moy_ui.h); *out: activated.
int moy_ui_run_value(void *target, int kind, moy_ui_span_t label, moy_ui_span_t value,
                     const moy_ui_over_t *o, int32_t *out);
// `kind` MOY_UI_V_CYCLE, MOY_UI_V_SEG, MOY_UI_V_SWATCH or MOY_UI_V_TABS; *out: the new cur.
int moy_ui_run_choice(void *target, int kind, moy_ui_span_t label, const uint8_t *opts, size_t n,
                      int32_t cur, const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_hints(void *target, const uint8_t *items, size_t n, const moy_ui_over_t *o);
int moy_ui_run_icon(void *target, moy_ui_span_t name, int32_t *out);
int moy_ui_run_progress(void *target, moy_ui_span_t label, int32_t v, int32_t hi,
                        const moy_ui_over_t *o);
int moy_ui_run_table(void *target, moy_ui_span_t label, const uint8_t *cols, size_t cols_n,
                     const uint8_t *cells, size_t cells_n, int32_t cur, const moy_ui_over_t *o,
                     int32_t out[2]);
// The next part of the table drawn last (moy_ui_api_table_part): its row,
// -1 after the last; the cell and what shows of it at out, the run's canvas
// clipped to what shows until the next call (its clip put back then).
int moy_ui_run_table_part(void *target, int32_t out[8]);
// The part answered last's distance from its view's centre, in 256ths of a
// row (moy_ui_api_table_dist).
int32_t moy_ui_run_table_dist(void *target);
// The next rect of the floor of the table drawn last (moy_ui_api_table_floor)
// at out: 1, then 0, -1 or -2 after the last.
int32_t moy_ui_run_table_floor(void *target, int32_t out[4]);
// Whether the buffer the frame paints holds part `id` under `key`
// (moy_ui_api_held).
int moy_ui_run_held(void *target, moy_ui_span_t id, int32_t key);
// Whether the buffer the last frame painted holds part `id` under `key`
// (moy_ui_api_held_last), and whether every buffer holds every part the
// frame named (moy_ui_api_held_all).
int moy_ui_run_held_last(void *target, moy_ui_span_t id, int32_t key);
int moy_ui_run_held_all(void *target);
// The faces the table drawn last keeps (moy_ui_api_slots), the slot of
// one (moy_ui_api_slot) and the next part an idle frame draws the face of
// (moy_ui_api_slot_next).
int32_t moy_ui_run_slots(void *target, int32_t cap, int32_t key);
int32_t moy_ui_run_slot(void *target, moy_ui_span_t name, int32_t gen, int mint);
int32_t moy_ui_run_slot_next(void *target);
int moy_ui_run_scroll(void *target, moy_ui_span_t id, const moy_ui_over_t *o);
int moy_ui_run_text_view(void *target, moy_ui_span_t id, const moy_ui_lines_t *lines,
                         const int32_t *opt, const moy_ui_over_t *o, int32_t out[2]);
int moy_ui_run_field(void *target, moy_ui_span_t label, uint8_t *buf, size_t cap, size_t len,
                     const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_dialog(void *target, moy_ui_span_t title, const uint8_t *buttons, size_t n,
                      int kind, const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_popover(void *target, moy_ui_span_t id, int w, const moy_ui_over_t *o,
                       int32_t *out);
int moy_ui_run_menu(void *target, moy_ui_span_t label, const uint8_t *items, size_t n,
                    const uint8_t *icons, size_t in, const moy_ui_over_t *o, int32_t *out);
int moy_ui_run_grid(void *target, moy_ui_span_t id, int w, int h, const moy_ui_over_t *o);
int moy_ui_run_card(void *target, moy_ui_span_t title, moy_ui_span_t sub, const moy_ui_over_t *o,
                    int32_t out[5]);
int moy_ui_run_tool(void *target, moy_ui_span_t icon, moy_ui_span_t label, const moy_ui_over_t *o,
                    int32_t *out);
int moy_ui_run_rule(void *target);
// A chat's bubble (moy_ui_api_bubble): out its x, y, w, h and state.
int moy_ui_run_bubble(void *target, moy_ui_span_t text, moy_ui_span_t head, moy_ui_span_t status,
                      const int32_t *opt, int n, const moy_ui_over_t *o, int32_t out[5]);
// An empty page's head (moy_ui_api_empty): *out the y under it.
int moy_ui_run_empty(void *target, moy_ui_span_t icon, moy_ui_span_t title, const uint8_t *lines,
                     size_t n, int32_t *out);
int moy_ui_run_bar(void *target, moy_ui_span_t title, const uint8_t *tools, size_t n, uint32_t off,
                   int32_t *out);
// The host's back over the run's frame drawn last (moy_ui_api_back): 1 where
// the app takes it, 0 where the host leaves the app; `take` 0 only asks.
int moy_ui_run_back(int take);
// The host's bar over the run's frame drawn last, as moy_ui_api_bar_lent,
// moy_ui_api_bar_chip and moy_ui_api_bar_tap are over a context: what a Lua
// or compiled app lent it with ui.bar (0: nothing, or no run open), and a
// tool of it the bar activated.
int moy_ui_run_bar_lent(const char **title, int16_t *slots, int *n, uint32_t *off,
                        const char **chip);
void moy_ui_run_bar_tap(int i);

// The rows, in the role table's order: each an import's native, the engine's
// handle on the call first, a '*~' a checked pointer and its length (an
// override block, moy_ui_api.h's, of length 0 is none). An answer below 0 is
// moy_ui.h's code negated; ui_focus answers the focused id's length (0 none)
// and, given an id of length 0, reads without moving.
int32_t moy_ui_wasm_page(void *e, const uint8_t *title, uint32_t tn, const uint8_t *o,
                         uint32_t on, uint8_t *out, uint32_t cap);
int32_t moy_ui_wasm_label(void *e, const uint8_t *text, uint32_t n, const uint8_t *o,
                          uint32_t on);
int32_t moy_ui_wasm_button(void *e, const uint8_t *label, uint32_t n, int32_t kind,
                           const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_toggle(void *e, const uint8_t *label, uint32_t n, int32_t on_,
                           const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_slider(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t lo,
                           int32_t hi, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_list(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, int32_t cur, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_part(void *e, const uint8_t *id, uint32_t n, int32_t w, int32_t h,
                         const uint8_t *o, uint32_t on, uint8_t *out, uint32_t cap);
int32_t moy_ui_wasm_focus(void *e, const uint8_t *id, uint32_t n, uint8_t *buf, uint32_t cap);
int32_t moy_ui_wasm_keys_taken(void *e);
int32_t moy_ui_wasm_at(void *e, int32_t x, int32_t y, int32_t w, int32_t h);
int32_t moy_ui_wasm_group(void *e, const uint8_t *caption, uint32_t n, const uint8_t *o,
                          uint32_t on);
int32_t moy_ui_wasm_info(void *e, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_link(void *e, const uint8_t *label, uint32_t n, const uint8_t *value,
                         uint32_t vn, const uint8_t *o, uint32_t on);
// The options NUL-separated (ui_swatches: 0xRRGGBB words), `cur` from 0.
int32_t moy_ui_wasm_cycle(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                          uint32_t len, int32_t cur, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_seg(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                        uint32_t len, int32_t cur, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_swatches(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                             uint32_t len, int32_t cur, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_hints(void *e, const uint8_t *items, uint32_t n, const uint8_t *o,
                          uint32_t on);
// ui_icon answers 1 where `name` names an icon, 0 where it does not.
int32_t moy_ui_wasm_icon(void *e, const uint8_t *name, uint32_t n, const uint8_t *o, uint32_t on);
int32_t moy_ui_wasm_progress(void *e, const uint8_t *label, uint32_t n, int32_t v, int32_t hi,
                             const uint8_t *o, uint32_t on);
// ui_table writes the new cur and whether the current row was opened at
// `out`; its headers and cells each end in a NUL, the cells row by row.
int32_t moy_ui_wasm_table(void *e, const uint8_t *label, uint32_t n, const uint8_t *cols,
                          uint32_t cn, const uint8_t *cells, uint32_t en, int32_t cur,
                          const uint8_t *o, uint32_t on, uint8_t *out, uint32_t cap);
// ui_table_part answers the next part's row of the table drawn last (from
// 0; -1 after the last) and writes its cell's x, y, w and h at `out`.
int32_t moy_ui_wasm_table_part(void *e, uint8_t *out, uint32_t cap);
// ui_table_dist answers moy_ui_run_table_dist.
int32_t moy_ui_wasm_table_dist(void *e);
// ui_table_floor answers moy_ui_run_table_floor, its rect's x, y, w and h
// at `out`.
int32_t moy_ui_wasm_table_floor(void *e, uint8_t *out, uint32_t cap);
// ui_held answers moy_ui_run_held.
int32_t moy_ui_wasm_held(void *e, const uint8_t *id, uint32_t n, int32_t key);
// ui_held_last and ui_held_all answer moy_ui_run_held_last and _held_all.
int32_t moy_ui_wasm_held_last(void *e, const uint8_t *id, uint32_t n, int32_t key);
int32_t moy_ui_wasm_held_all(void *e);
// ui_faces, ui_face and ui_slot_next answer moy_ui_run_slots, _face and
// _slot_next.
int32_t moy_ui_wasm_slots(void *e, int32_t cap, int32_t key);
int32_t moy_ui_wasm_slot(void *e, const uint8_t *name, uint32_t n, int32_t gen, int32_t mint);
int32_t moy_ui_wasm_slot_next(void *e);
// ui_part_at answers 1 where the pointer is inside the part answered last
// and writes its x and y from the part's top-left at `out`, else 0.
int32_t moy_ui_wasm_part_at(void *e, uint8_t *out, uint32_t cap);
// ui_split: the panes' sizes as little-endian words (written back where a
// divider is let go after a drag), their minimums (length 0: none) and the
// MOY_UI_SPO_* words; writes each pane's x, y, w, h and then the
// MOY_UI_SPLIT_OUT words at `out`, and answers the focused pane (-1 none),
// or -0x100 less a MOY_UI_* code where it fails (a bad block: -0x101).
int32_t moy_ui_wasm_split(void *e, const uint8_t *id, uint32_t n, uint8_t *sizes, uint32_t sn,
                          const uint8_t *mins, uint32_t mn, const uint8_t *opt, uint32_t on,
                          uint8_t *out, uint32_t cap);
// ui_text_view: its lines NUL-separated, its options the first of the MOY_UI_TXO_* words
// (little-endian i32s); writes the first line shown and the rows shown at
// `out`.
int32_t moy_ui_wasm_text_view(void *e, const uint8_t *id, uint32_t n, const uint8_t *lines,
                              uint32_t ln, const uint8_t *opt, uint32_t optn, const uint8_t *o,
                              uint32_t on, uint8_t *out, uint32_t cap);
// ui_scroll with an id of length 0 ends the region.
//
// ui_scroll, ui_text_view, ui_dialog and ui_popover each open a nest: one
// opened with MOY_UI_API_NEST open already opens none, draws nothing and
// answers -MOY_UI_DEEP (-5).
int32_t moy_ui_wasm_scroll(void *e, const uint8_t *id, uint32_t n, const uint8_t *o, uint32_t on);
// ui_field edits the `len` bytes at buf (its room `cap`): the new length,
// plus 0x10000 the frame Enter is pressed in it.
int32_t moy_ui_wasm_field(void *e, const uint8_t *label, uint32_t n, uint8_t *buf, uint32_t cap,
                          int32_t len, const uint8_t *o, uint32_t on);
// ui_dialog: the button activated (from 0), -1 none; a title of length 0 ends it.
int32_t moy_ui_wasm_dialog(void *e, const uint8_t *title, uint32_t n, const uint8_t *buttons,
                           uint32_t bn, int32_t kind, const uint8_t *o, uint32_t on);
// ui_popover: 1, 0 the frame it is dismissed; an id of length 0 ends it.
int32_t moy_ui_wasm_popover(void *e, const uint8_t *id, uint32_t n, int32_t w, const uint8_t *o,
                            uint32_t on);
// ui_menu: the item activated (from 0), -1 none.
int32_t moy_ui_wasm_menu(void *e, const uint8_t *label, uint32_t n, const uint8_t *items,
                         uint32_t len, const uint8_t *icons, uint32_t ilen, const uint8_t *o,
                         uint32_t on);
// ui_tool: 1 the frame it is activated.
int32_t moy_ui_wasm_tool(void *e, const uint8_t *icon, uint32_t in, const uint8_t *label,
                         uint32_t n, const uint8_t *o, uint32_t on);
// ui_rule: a divider (moy_ui_api_rule).
int32_t moy_ui_wasm_rule(void *e);
// ui_bubble writes the bubble's x, y, w, h and the state bits at `out`;
// `opt` its options as little-endian words (MOY_UI_BBO_* order).
int32_t moy_ui_wasm_bubble(void *e, const uint8_t *text, uint32_t n, const uint8_t *head,
                           uint32_t hn, const uint8_t *status, uint32_t sn, const uint8_t *opt,
                           uint32_t optn, const uint8_t *o, uint32_t on, uint8_t *out,
                           uint32_t cap);
// ui_empty: the y under the empty page's head; `lines` NUL-separated.
int32_t moy_ui_wasm_empty(void *e, const uint8_t *icon, uint32_t in, const uint8_t *title,
                          uint32_t n, const uint8_t *lines, uint32_t len);
// ui_bar: the tool the host's bar activated (from 0), -2 its way back, -1
// none; bit i of `off` its tool i at half strength, bit 31 the app's way back.
int32_t moy_ui_wasm_bar(void *e, const uint8_t *title, uint32_t n, const uint8_t *tools,
                        uint32_t len, int32_t off);
// ui_tabs: the new cur, as ui_seg's.
int32_t moy_ui_wasm_tabs(void *e, const uint8_t *label, uint32_t n, const uint8_t *opts,
                         uint32_t len, int32_t cur, const uint8_t *o, uint32_t on);
// ui_grid with an id of length 0 ends the grid.
int32_t moy_ui_wasm_grid(void *e, const uint8_t *id, uint32_t n, int32_t w, int32_t h,
                         const uint8_t *o, uint32_t on);
// ui_card writes the picture's x, y, w, h and the state bits at `out`.
int32_t moy_ui_wasm_card(void *e, const uint8_t *title, uint32_t n, const uint8_t *sub,
                         uint32_t sn, const uint8_t *o, uint32_t on, uint8_t *out, uint32_t cap);

// The native serving `name` ("ui_button"), or NULL; and the rows' names.
void *moy_ui_wasm_native(const char *name);
const char *moy_ui_wasm_name(int i);

#endif // MOY_UI_WASM_H
