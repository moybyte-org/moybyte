// The UI engine: a page an app's build function describes, held in a node
// store, drawn each frame by walking it through the toolkit's verbs
// (native/moy_ui/moy_ui_api.h) with no script run to draw. The build's calls
// fill the store (every text copied, every handle an index); state lives in
// typed, versioned slots the walk reads; templates and conditions are
// compiled at the call that names them and evaluated here. Handlers are the
// app's: the walk queues what the user did as events its front end drains
// after the frame (`uit_next`).
//
// One function table for every tier: the host binds it by ctypes
// (runtime/host/moy_uitree_binding.py), MicroPython, Lua and a compiled app
// through their faces. Its specification is the reference layer
// (runtime/uitree/uitree_app.py): same verbs, same arguments, same trace.
#ifndef MOY_UITREE_H
#define MOY_UITREE_H

#include <stddef.h>
#include <stdint.h>

#include "moy_ui.h"
#include "moy_ui_api.h"

// Answers below 0.
enum {
    UIT_E_NAME = -1,        // no state, action, field or kind of that name
    UIT_E_LANG = -2,        // a template or condition this engine does not compile
    UIT_E_KIND = -3,        // a kind or prop this engine does not draw
    UIT_E_NOMEM = -4,
    UIT_E_ARG = -5,         // a handle out of range, a value of the wrong type
    UIT_E_CHECK = -6,       // a mistake the spec names (T01-T05, C01-C02, N04): `uit_error_code`
};

// State types.
enum { UIT_BOOL = 1, UIT_INT, UIT_NUM, UIT_TEXT, UIT_ENUM, UIT_ROWOF, UIT_MODEL };

// Node kinds (level 1).
enum {
    UIT_PAGE = 1, UIT_GROUP, UIT_LABEL, UIT_INFO, UIT_LINK, UIT_BUTTON, UIT_TOGGLE,
    UIT_ROWS, UIT_DIALOG, UIT_SEG, UIT_SLIDER, UIT_FIELD, UIT_EMPTY, UIT_PROGRESS, UIT_TEXT_VIEW,
    UIT_SCROLL, UIT_MENU, UIT_ITEM, UIT_KINDS
};

// Events (`uit_next`).
enum {
    UIT_EV_NONE = 0,
    UIT_EV_ACTION = 1,      // a = action, b = model slot or -1, c = row index or -1; text: the source,
                            // then 0x02 and the id of the node that fired it where a node did
    UIT_EV_WRITE = 2,       // a = the slot the engine wrote
    UIT_EV_CELL = 3,        // a = the model slot, b = the row, c = the field the engine wrote (`uit_get_cell`)
};

// Frame flags (`uit_frame`).
enum {
    UIT_F_DESK = 1, UIT_F_ON_BAR = 2, UIT_F_BAND = 4, UIT_F_BARE = 8, UIT_F_TRACE = 16,
    UIT_F_UP = 32, UIT_F_DOWN = 64,
    UIT_F_GEN = 128,        // the host draws shift frames: the page carries its gen where it may
};
// The frame flags' bits 8..15: the pages on the stack (the band's Back).
#define UIT_F_PAGES_SHIFT 8

// Input kinds (@input).
enum { UIT_IN_KEYS = 0, UIT_IN_TDECK, UIT_IN_PAD, UIT_IN_TOUCH };

// Action flags.
enum { UIT_A_HOOK = 1, UIT_A_ROLE_CONFIRM = 2, UIT_A_ROLE_BACK = 4, UIT_A_ROW = 8 };

typedef struct uit uit_t;

uit_t *uit_new(void);
void uit_free(uit_t *e);
// The last error's text (a refused name, a source and where it stopped).
const char *uit_error(const uit_t *e);
// The spec's code of the last error when it was UIT_E_CHECK ("T02"), else "".
const char *uit_error_code(const uit_t *e);

// -- declarations (once, at open) ------------------------------------------
// A state slot: its index. `values` an enum's values, NUL-separated.
int uit_state(uit_t *e, const char *name, int type, const char *values, size_t vlen);
// A model's fields (names and types NUL-separated / one byte a field) and its
// key field's index, into slot `slot` (declared UIT_MODEL).
int uit_model(uit_t *e, int slot, const char *names, size_t nlen, const uint8_t *types, int nf,
              int key);
// A row_of slot's model.
int uit_row_of(uit_t *e, int slot, int model_slot);
// A label table (`ui.labels`): its index. `keys` and `words` NUL-separated,
// a word a key, a key the text of the value it names ("_" every other).
int uit_labels(uit_t *e, const char *name, const char *keys, size_t klen, const char *words,
               size_t wlen);
// An action: its index. `when` a condition source or NULL; `off` 0 none, 1
// hide, 2 grey; `letters` the letter keys it answers (lower case, "" none);
// `icon` the icon a menu's item of it draws where it names none ("" none).
int uit_action(uit_t *e, const char *name, const char *label, const char *hint, const char *when,
               int off, int flags, const char *letters, const char *icon);

// -- state, written by the front end (a handler's commit) ------------------
int uit_set_int(uit_t *e, int slot, int32_t v);
int uit_set_num(uit_t *e, int slot, double v);
int uit_set_text(uit_t *e, int slot, const char *s, size_t n);
// A model's rows: `n` rows of nf cells, each cell's text NUL-terminated in
// order (an int field's text its decimal digits).
int uit_set_model(uit_t *e, int slot, int n, const char *cells, size_t len);
int32_t uit_get_int(const uit_t *e, int slot);
// A text slot's bytes (n out), NULL for another type.
const char *uit_get_text(const uit_t *e, int slot, size_t *n);
// A model's cell's text (row `row`, field `f`), NULL out of range.
const char *uit_get_cell(const uit_t *e, int slot, int row, int f);

// -- the build (each page) -------------------------------------------------
// A fresh tree; `page_id` the toolkit id of the page (its build and level).
void uit_build_begin(uit_t *e, const char *page_id);
// A node of `kind` keyed `key` under `parent` (-1 for the page): its index.
int uit_node(uit_t *e, int kind, const char *key, int parent);
// A prop, in the order the build gave them. The engine decides from the kind
// and the name whether `src` is a template, a condition, a state's or an
// action's name or a literal; `repr` is how a literal prints in the dump.
int uit_prop(uit_t *e, int node, const char *name, const char *src, const char *repr);
// The node's action (a link, a button; a dialog's buttons one call each).
int uit_node_action(uit_t *e, int node, int action);
int uit_build_end(uit_t *e);
// The app opens (again): no focus, list cursor, row order or event carried over.
void uit_open(uit_t *e);
// Focus goes to node `key` (or ROWKEY/KEY) in the next frame (a push's
// return, ui.focus); NULL clears it.
void uit_want_focus(uit_t *e, const char *key);

// -- the frame ----------------------------------------------------------------
// One frame of the page into `content` of a frame the host began: answers
// the events queued (UIT_F_* flags; `input` an UIT_IN_*). Bit 16 set in the
// answer: the hints band names another node's verbs, so the frame owes a
// redraw of the whole page.
int uit_frame(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int x, int y, int w, int h,
              int flags, int fs, int input);
// The next event: its kind; its words into ev[0..2], its text (the source)
// into buf.
int uit_next(uit_t *e, int32_t ev[3], char *buf, size_t cap);
// The next trace line, "KIND\x01NAME\x01TEXT", into buf: its length, -1 none.
int uit_trace_next(uit_t *e, char *buf, size_t cap);

// -- asked between frames --------------------------------------------------
// Whether action `a` is on now, for row `row` of its model (-1 none).
int uit_can(uit_t *e, int a, int row);
// The action a typed letter `ch` fires (its letters hold it, lower-cased, and
// it is on; none while a dialog is shown), -1 none.
int uit_letter(uit_t *e, int ch);
// The shown dialog's node, -1 none.
int uit_dialog(uit_t *e);
// Back on a shown dialog: its first button's action (-1: no dialog shown),
// its key into buf.
int uit_back_dialog(uit_t *e, char *buf, size_t cap);
// Whether no node holds focus.
int uit_unfocused(const uit_t *e);
// The id focus is on after the last frame ("" none): its length.
int uit_focused(const uit_t *e, char *buf, size_t cap);
// The last frame's stop `i` (from 0): its kind (UIT_SEG with the cycle look
// answers UIT_KINDS), its id into buf; -1 past the last.
int uit_stop(const uit_t *e, int i, char *buf, size_t cap);
// A commit field's draft (typed, not yet written), NULL none.
const char *uit_draft(const uit_t *e, const char *key, size_t *n);
// Every way out of the page: each drawn commit field's draft written, its
// writes and its changed= queued as events: the events queued.
int uit_leave(uit_t *e);
// The tree as text (the reference's snapshot): its length; at most cap-1
// bytes are written.
int uit_dump(uit_t *e, char *buf, size_t cap);
// Bytes the store holds: nodes, templates, ops, the pool, state.
int uit_stats(const uit_t *e, int32_t out[8]);

#endif
