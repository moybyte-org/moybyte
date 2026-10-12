// The toolkit's rows for a compiled app: roles.json's `ui` role
// (docs/kernel_toolkit_2026-10.md §3.5), the natives behind the
// `moybyte.app` imports `ui_<verb>` that native/moy_app/moy_app_wasm.c's
// table names.
//
// A row draws into the run's draw target through the RUN sink: the canvas a
// cart's own verbs draw on (moycore_ui_target: an owed or kept frame on the
// screen written in first, as libmoy's verbs do), lent to the primitives as
// a canvas row of its own -- its pixels, its size, the whole canvas as the
// clip, no camera, font scale 1 (the app is cart-shaped), the canvas's
// index-to-pixel words as the palette. Around each row the run's libmoy draw
// state (camera, clip, pal, palt, the screen palette, fillp) is saved, reset,
// and restored after, so a widget looks the same whatever the cart left set
// and the cart finds its state as it left it.
//
// The colours are the RUN look's (MOY_UI_LOOK_RUN): the style resolved
// against the live theme's token row, rebuilt when its generation moves and
// read under native/moy_app's seqlock, so a row runs on the run's own thread
// with no hop and no upcall.
//
// State a row keeps between frames is the app's, in its own memory: a hit
// buffer, a pump and a drag, each 4-byte aligned (a misaligned or short one
// answers -MOY_UI_BAD). The layouts are docs/app_api_v1.md's.

#ifndef MOY_UI_WASM_H
#define MOY_UI_WASM_H

#include <stdint.h>

#include "moy_ui.h"

#define MOY_UI_WASM_ROWS 9

// ui_dragtap's state in the app's memory: 40 bytes.
typedef struct {
    moy_ui_dragtap_t tap;
    moy_ui_scroll_t scroll;
    int32_t offset;
    int32_t tx, ty;             // where the last clean tap landed
} moy_ui_wasm_drag_t;

// The run's draw target, from the module that drives the run (modmoycore.c on
// an image, runtime/moyhost_wasm.c on the host): a libmoy moy_canvas, or NULL
// while no compiled run is open.
void *moycore_ui_target(void);

// The live theme's token row and its name, copied when the generation differs
// from `have`: native/moy_app/moy_app_wasm.c's moy_app_wasm_theme. Answers
// the generation, 0 while no run is bound.
typedef uint32_t (*moy_ui_theme_fn)(int32_t out[MOY_UI_TOKENS], uint32_t have, char *name,
                                    uint32_t cap);
// Where the run's theme is read when the app ABI lives in another library
// (the host's); an image links moy_app_wasm_theme itself.
void moy_ui_wasm_theme_from(moy_ui_theme_fn fn);

// The rows, in roles.json's order: each an import's native, the engine's
// handle on the call first, a '*~' a checked pointer and its length. An
// answer below 0 is moy_ui.h's code negated.
int32_t moy_ui_wasm_kind(void *e, const uint8_t *name, uint32_t n);
int32_t moy_ui_wasm_glyph_id(void *e, const uint8_t *name, uint32_t n);
int32_t moy_ui_wasm_button(void *e, int32_t x, int32_t y, int32_t w, int32_t h, int32_t kind,
                           int32_t flags, int32_t interact, const uint8_t *label, uint32_t ln,
                           int32_t glyph);
int32_t moy_ui_wasm_label(void *e, int32_t x, int32_t y, int32_t w, int32_t h,
                          const uint8_t *text, uint32_t n, int32_t ink, int32_t align);
int32_t moy_ui_wasm_list(void *e, int32_t x, int32_t y, int32_t w, int32_t h, int32_t kind,
                         const uint8_t *items, uint32_t n, int32_t count, int32_t row_h,
                         int32_t offset, int32_t sel);
int32_t moy_ui_wasm_hits_clear(void *e, uint8_t *buf, uint32_t n);
int32_t moy_ui_wasm_hits_add(void *e, uint8_t *buf, uint32_t n, int32_t x, int32_t y,
                             int32_t w, int32_t h, int32_t id);
int32_t moy_ui_wasm_hits_pump(void *e, uint8_t *buf, uint32_t n, int32_t px, int32_t py,
                              int32_t down, int32_t pointing, uint8_t *pump, uint32_t pn);
int32_t moy_ui_wasm_dragtap(void *e, uint8_t *st, uint32_t n, int32_t x, int32_t y, int32_t w,
                            int32_t h, int32_t horizontal, int32_t px, int32_t py,
                            int32_t click, int32_t down, int32_t slop, int32_t content,
                            float dt_ms);

// The native serving `name` ("ui_button"), or NULL; and the rows' names.
void *moy_ui_wasm_native(const char *name);
const char *moy_ui_wasm_name(int i);

#endif // MOY_UI_WASM_H
