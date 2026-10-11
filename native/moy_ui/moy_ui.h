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

enum { MOY_UI_OK = 0, MOY_UI_BAD = 1, MOY_UI_STALE = 2, MOY_UI_NOMEM = 3 };

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

// -- the ROW sink (moy_ui.c) ------------------------------------------------------

// Draw glyph `g` into canvas row `canvas` (a CANVAS handle, checked as any
// handle is): its camera, clip and size from the row's state, `ci` through
// the row's palette, the row's fill counter bumped per span. MOY_UI_STALE
// when the row is gone.
int moy_ui_row_glyph(uint32_t canvas, int g, int x, int y, int w, int h, int ci, int scale);

#endif // MOY_UI_H
