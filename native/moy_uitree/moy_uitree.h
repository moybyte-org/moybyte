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
    UIT_E_BLOB = -7,        // a compiled page this engine does not read (`uit_load`)
};

// State types.
enum { UIT_BOOL = 1, UIT_INT, UIT_NUM, UIT_TEXT, UIT_ENUM, UIT_ROWOF, UIT_MODEL };

// Node kinds (level 1).
enum {
    UIT_PAGE = 1, UIT_GROUP, UIT_LABEL, UIT_INFO, UIT_LINK, UIT_BUTTON, UIT_TOGGLE,
    UIT_ROWS, UIT_DIALOG, UIT_SEG, UIT_SLIDER, UIT_FIELD, UIT_EMPTY, UIT_PROGRESS, UIT_TEXT_VIEW,
    UIT_SCROLL, UIT_MENU, UIT_ITEM, UIT_SPLIT, UIT_CANVAS, UIT_TABS, UIT_SWITCH, UIT_CASE, UIT_TABLE,
    UIT_COL, UIT_FOOT, UIT_CARDS, UIT_CARD, UIT_PICTURE, UIT_POPOVER,
    // the placed kinds: a page placed by the engine's layout (uit_set_placed) holds them
    UIT_TOOLS, UIT_ROW, UIT_STATUS, UIT_TEXT_EDITOR, UIT_PANES, UIT_PANE, UIT_LEGACY, UIT_SLOT,
    UIT_PANEL, UIT_LENT, UIT_KINDS
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
enum { UIT_A_HOOK = 1, UIT_A_ROLE_CONFIRM = 2, UIT_A_ROLE_BACK = 4, UIT_A_ROW = 8, UIT_A_SHIFT_ENTER = 16 };

typedef struct uit uit_t;

uit_t *uit_new(void);
void uit_free(uit_t *e);
// The visit is over: what it made (ids, orders, drafts, the lists' cursor
// syncs, events, the trace, the scratch) is freed, and the declarations, the
// lowered page and each state slot's value are cut to what they hold; the
// focus stays. The page is drawn again with no build once the state that
// moved since is pushed: after a uit_open (a new visit), or as it was (a host
// that drew it again without one).
void uit_close(uit_t *e);
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
// Action `a` acts on the current row of row_of slot `slot` (row=): it is off
// while that row_of names no row, and a node outside a list hands it that row.
int uit_action_row(uit_t *e, int a, int slot);

// -- state, written by the front end (a handler's commit) ------------------
int uit_set_int(uit_t *e, int slot, int32_t v);
int uit_set_num(uit_t *e, int slot, double v);
int uit_set_text(uit_t *e, int slot, const char *s, size_t n);
// An optional state (ui.opt) absent: it draws as nothing and is false in a
// condition until a value is set again.
int uit_set_absent(uit_t *e, int slot);
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
// The app opens (again): no focus, list cursor, row order or event carried
// over; a page the store keeps from the last visit draws as a fresh build.
void uit_open(uit_t *e);
// Focus goes to node `key` (or ROWKEY/KEY) in the next frame (a push's
// return, ui.focus); NULL clears it.
void uit_want_focus(uit_t *e, const char *key);

// -- the placed layout ----------------------------------------------------------
// The page is placed (1) or flows (0, the default). A placed page's children
// are stacked down its room by the engine's one layout rule, the rule of the
// Editor's pages and the window manager's surfaces alike: a container gives
// each shown child a rect, fixed rows their height and the fills sharing
// the rest, a text editor whole lines.
void uit_set_placed(uit_t *e, int on);
// The box the page's ground covers past its content (w 0: none): the
// toolkit's page opens there and the page lays out in the content.
void uit_set_ground(uit_t *e, int x, int y, int w, int h);
// The layout alone, nothing drawn, over `x, y, w, h` (a host's placement
// before its frame): each placed region's rect, as uit_rect reads them.
int uit_place(uit_t *e, int x, int y, int w, int h, int flags, int fs);
// Where node `key` was placed by the last frame or placement: 1 and its rect
// into out, else 0 (not placed: hidden, lent, inside a flow).
int uit_rect(const uit_t *e, const char *key, int32_t out[4]);

// Back on a placed page's shown row or group that says what Back does there
// (`close=`, the innermost): its action, its key into buf; -1 where its
// action is off, -2 where none shows.
int uit_back_closer(uit_t *e, char *buf, size_t cap);
// Enter on a placed page's popover row (`uitree_editor.Engine._keys`): the
// focused link's action queued on its row, as A takes it. 1 where a popover's
// link holds focus (its action off too), else 0.
int uit_take_focused(uit_t *e);
// A part's rect the front end placed this frame (a text editor's caret or
// word, "code.caret"), that a popover's anchor= may name; the frame's own
// draws record them (four a frame, the rest let go).
void uit_anchor(uit_t *e, const char *name, int x, int y, int w, int h);
// Node `node`'s condition prop `name` now (a text editor's gutter=, which its
// front end draws by): 1 or 0, -1 where it has none.
int uit_cond_of(uit_t *e, int node, const char *name);

// -- a row lent to a site ---------------------------------------------------------
// The shown `tools lend=SITE` row's cells before layout, its host's to draw
// (the shell's bar): UIT_LC_INTS ints a cell into `cells` (its kind, node, a
// tab's value index, its UIT_LC_* flags, the action a take runs or -1) and
// four texts a cell into `text`, each NUL-ended (its node's key, a tab's
// value, its label, its icon; "" none). Answers the cells (`max` at most),
// -1 where no row is lent to the site, UIT_E_ARG where `text` is too small.
enum { UIT_LC_TAB = 0, UIT_LC_BUTTON, UIT_LC_TEXT };
enum { UIT_LC_ALONE = 1, UIT_LC_CUR = 2, UIT_LC_ON = 4, UIT_LC_MARKED = 8, UIT_LC_PRIMARY = 16 };
#define UIT_LC_INTS 5
int uit_lent(uit_t *e, const char *site, int32_t *cells, int max, char *text, size_t cap);
// The lent row's layout in a zone `x0` wide `w` at cell height `h` and scale
// `fs`, the host's widths handed in: UIT_LP_INTS ints a cell, in (its kind,
// UIT_LC_* flags, its width named: a tab's or a button's with its label, 0
// where it has none) and out (x, w, labelled: w -1 past the zone's room).
// The densities, tried in turn: every label; icons with the current tab
// named and the buttons' words where every tab named in its place holds
// them; the same with the buttons as icons; icons alone. `pad` widens the
// current tab's icon each side. Answers the density placed at (2, 1, 0);
// the hairlines' x into `hair` (`*nhair` of them, n at most) and into
// `*need` the narrowest zone that holds every cell as an icon at `h`.
#define UIT_LP_INTS 6
int uit_lent_place(int32_t *cells, int n, int32_t x0, int32_t w, int32_t h, int fs, int32_t pad,
                   int32_t *hair, int *nhair, int32_t *need);

// -- the panes (`ui.panes`, `ui.pane`, `ui.legacy`) --------------------------------
// What a place, a frame or uit_splits lays the panes out by: the desk unit
// `du` (pixels), the pane the keys reach (its key, "" none), whether the
// pointer hovers and the host's clock in ms (a divider's double tap).
void uit_panes_set(uit_t *e, int du, const char *pane_cur, int mouse, uint32_t clock);
// A placed page's unit, which a row's literal gap=, pad= and a canvas's w= are in (0: the
// engine's own, 1 on a desk and the font scale on a handheld).
void uit_set_unit(uit_t *e, int unit);
// The last pass's panes as ints into `out` (`max` at most): UIT_PN_HEAD
// words (the outermost panes' node or -1, the pane the placement stood
// alone or -1, the least size's w and h, then the counts of the four lists
// and of the splits), then the last placement's panes arranged (node, x, y,
// w, h, depth, path[4]), its dividers (x, y, w, h, depth, path[4], index,
// axis), the bodies it placed and the panes this pass drew (as the panes),
// then each split of this pass (node, n, axis, gap, x, y, w, h, depth,
// path[4], then node, size, minimum, held a child: -1 it grows). Answers
// the words written.
#define UIT_PN_HEAD 9
int uit_panes(const uit_t *e, int32_t *out, int max);
// The last pass's splits' dividers drawn into a pass the host has open
// (its own context and sink): a press holds one, a drag's let-go writes
// the held sizes beside it (traced under UIT_F_TRACE, queued as writes), a
// double tap puts them back. Into `out`: the split and divider held (-1
// none), where its drag takes it, the side it stopped at, whether it moved,
// the pane it holds at its minimum and that minimum (-1 none), the held
// sizes written and whether the page owes a frame. Answers the events queued.
enum { UIT_SP_SPLIT, UIT_SP_DIV, UIT_SP_AT, UIT_SP_STOP, UIT_SP_LIVE, UIT_SP_FLASH, UIT_SP_MIN, UIT_SP_WRITES,
       UIT_SP_OWE, UIT_SP_N };
int uit_splits(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int flags, int32_t out[UIT_SP_N]);
// What a host asks of panes or pane `node` now, as the layout reads it:
// whether it is shown (a pane with no tab on offer, a split with no child
// shown, is not), its least size along axis `arg` (0 across, 1 down; pixels,
// gutters in), its held size (pixels, -1 it grows), whether its tab value
// `arg` (an index of its tabs= enum) is on offer. UIT_E_ARG for another node.
enum { UIT_PQ_SHOWN, UIT_PQ_MIN, UIT_PQ_HELD, UIT_PQ_OFFERS };
int uit_pane_ask(uit_t *e, int node, int what, int arg);
// Whether optional state `slot` is absent.
int uit_absent(const uit_t *e, int slot);

// -- the frame ----------------------------------------------------------------
// One frame of the page into `content` of a frame the host began: answers
// the events queued (UIT_F_* flags; `input` an UIT_IN_*). Bit 16 set in the
// answer: the hints band names another node's verbs, so the frame owes a
// redraw of the whole page.
int uit_frame(uit_t *e, moy_ui_ctx_t *c, const moy_ui_sink_t *s, int x, int y, int w, int h,
              int flags, int fs, int input);
// Whether the frame drawn last is what a frame now would draw, so a host
// may leave its pixels and skip uit_frame: the same build, no state's
// version and no draft moved, the same focus, no event, trace line or
// wanted focus waiting, the same flags, content rect, font scale and input,
// and the toolkit's context at rest -- no input this frame (a key, a
// repeat, a press, the wheel, an activation), no begin that moved focus or
// a scroll, no page moving on its own and no frame owed. Called after the
// host began the frame (its begin has taken the input). 0 before a first
// frame and after an open, a build or a close.
int uit_still(uit_t *e, const moy_ui_ctx_t *c, int x, int y, int w, int h, int flags, int fs, int input);
// A canvas's draw: the walk calls it inside the frame, in the page's order,
// with the canvas's node and the rect it draws in; the front end binds what
// each node draws (a Python function, a Lua one, C).
typedef void (*uit_draw_fn)(void *ud, int node, int x, int y, int w, int h);
void uit_on_draw(uit_t *e, uit_draw_fn fn, void *ud);
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
// Back on a shown popover: its close= action, -1 where it names none (Back
// still closes nothing else), -2 where no popover shows; its key into buf.
int uit_back_popover(uit_t *e, char *buf, size_t cap);
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

// A page compiled ahead of time (moy_uitree_aot.c; the format is
// native/moy_uitree/README.md's "A compiled page"). UIT_FORMAT moves with any
// change to what a blob holds; the layout hash is this engine's own, over
// every record's fields and widths and the numbers they hold, and a blob of
// another format or layout is refused, never read.
#define UIT_FORMAT 2
#define UIT_BLOB_HEAD 32
uint32_t uit_layout_hash(void);
// The store after a build's lowering (its declarations and its one page) as
// a blob: its length (at most `cap` bytes are written, none where it does not
// fit). `variant` and `source` are the compiler's (the layout variant, a hash
// of what it was built from); `front` is the front end's own bytes, kept
// whole at the blob's end (the header's front offset and length).
int uit_save(uit_t *e, char *buf, size_t cap, const char *variant, uint32_t source, const char *front,
             size_t nfront);
// A new store filled from a blob of page `page_id`, variant `variant`, as
// uit_build_end leaves a build: 0, or UIT_E_BLOB naming why it is refused
// (format, layout, damage, another page, placed where the store is not or
// the other way, a store not new). The blob is
// copied; the caller may let it go.
int uit_load(uit_t *e, const char *blob, size_t n, const char *page_id, const char *variant);
// Every engine's blocks together: [0] bytes live, [1] the most live since the
// last uit_mem_peak_reset, [2] blocks live. A block's count is what was asked.
void uit_mem(uint32_t out[3]);
void uit_mem_peak_reset(void);

#endif
