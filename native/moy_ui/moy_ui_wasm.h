// The public UI API's wasm rows (docs/ui_api_2026-10.md): the `ui` rows of
// native/os_calls.json a compiled app imports from `moybyte.app`
// (`ui_<verb>`, named by native/moy_app/moy_app_wasm.c's table), each
// decoded onto the RUN face (moy_ui_run.h), and the same rows a native or
// WASI app calls under sdk/include/moybyte/ui.h's names.

#ifndef MOY_UI_WASM_H
#define MOY_UI_WASM_H

#include <stdint.h>

#include "moy_ui_run.h"

// MOY_UI_WASM_ROWS, each row's prototype (which must be the ones below) and
// the natives by import name, generated from native/os_calls.json.
#include "moy_ui_wasm_rows.h"

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
// ui_note: the y under the note; `lines` its paragraphs, NUL-separated.
int32_t moy_ui_wasm_note(void *e, const uint8_t *icon, uint32_t in, const uint8_t *lines,
                         uint32_t len);
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
