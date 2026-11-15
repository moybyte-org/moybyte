// The public UI API's RUN face (docs/ui_api_2026-10.md): the verbs a Lua
// cart calls as `ui.*` (native/moycore/moycore_ui.h), the `ui` rows of
// native/os_calls.json a compiled app imports from `moybyte.app` (`ui_<verb>`,
// decoded by moy_ui_wasm.c) and those a native or WASI app calls on the copy
// it links (sdk/src/moyui_link.c), over native/moy_ui/moy_ui_api.h. One
// context per run, where a Python app's verbs pass their own
// (moy_ui_api.h's moy_ui_api_<verb>): the two entry forms are on purpose.
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

#ifndef MOY_UI_RUN_H
#define MOY_UI_RUN_H

#include <stddef.h>
#include <stdint.h>

#include "moy_ui.h"

#include "moy_ui_api.h"

// A byte span as the engine takes it, its length capped at 0xFFFF.
static inline moy_ui_span_t moy_ui_span_of(const uint8_t *p, size_t n) {
    moy_ui_span_t s = { p, (uint16_t)(n > 0xFFFFu ? 0xFFFFu : n) };
    return s;
}

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
// Whether the run open now drew a page whose focus is a field
// (moy_ui_api_typing): 0 where it drew none. Begins no frame.
int moy_ui_run_typing(void);
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
// A note on the page (moy_ui_api_note): *out the y under it.
int moy_ui_run_note(void *target, moy_ui_span_t icon, const uint8_t *lines, size_t n,
                    int32_t *out);
// An empty page's head (moy_ui_api_empty): *out the y under it.
int moy_ui_run_empty(void *target, moy_ui_span_t icon, moy_ui_span_t title, const uint8_t *lines,
                     size_t n, int32_t *out);
int moy_ui_run_bar(void *target, moy_ui_span_t title, const uint8_t *tools, size_t n, uint32_t off,
                   int32_t *out);
// The host's back over the run's frame drawn last (moy_ui_api_back): 1 where
// the app takes it, 0 where the host leaves the app; `take` 0 only asks.
int moy_ui_run_back(int take);
// The host's cancel over the run's frame drawn last (moy_ui_api_cancel): 1
// where its focused field has an edit in progress, which `take` gives back.
int moy_ui_run_cancel(int take);
// The host's bar over the run's frame drawn last, as moy_ui_api_bar_lent,
// moy_ui_api_bar_chip and moy_ui_api_bar_tap are over a context: what a Lua
// or compiled app lent it with ui.bar (0: nothing, or no run open), and a
// tool of it the bar activated.
int moy_ui_run_bar_lent(const char **title, int16_t *slots, int *n, uint32_t *off,
                        const char **chip);
void moy_ui_run_bar_tap(int i);
// The console's Paste fired: the run's frame begun next takes `text`
// (moy_ui_api_paste), its focused field inserting it. The console's host
// calls it for a Lua or compiled run; a native or WASI app's linked toolkit
// for the "paste" message its console posts ahead of the frame carrying
// Ctrl+V (sdk/src/moyui_link.c).
void moy_ui_run_paste(const uint8_t *text, size_t n);
// The frame's steps beyond its presses, for the run's frame begun next
// (moy_ui_api.h's MOY_UI_IN_REP and MOY_UI_IN_KEYREP: a held key's repeats,
// the trackball's steps, a held key's typing): the console's host calls it
// each frame of a Lua or compiled app, as an app's own host fills them.
void moy_ui_run_steps(int32_t rep, int32_t keyrep);

#endif // MOY_UI_RUN_H
