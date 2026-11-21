// The engine's insides, shared by its three files (moy_uitree.h is the API).
#ifndef MOY_UITREE_INT_H
#define MOY_UITREE_INT_H

#include "moy_uitree.h"

// Every block the engine holds comes from these three (moy_uitree.c), which
// count it (uit_mem) and take it from native/moy_kernel's allocator: PSRAM on
// a board, the C library's malloc on the host. ESP-IDF's own malloc serves a
// block under the always-internal threshold (8 KB on an S3) from internal
// SRAM, and an engine's store is many small blocks.
void *uit_malloc(size_t n);
void *uit_realloc(void *p, size_t n);
void uit_mfree(void *p);
#define UIT_MALLOC uit_malloc
#define UIT_REALLOC uit_realloc
#define UIT_FREE uit_mfree

// A refusal's words: the prose on the host, whose checker prints it; on a
// board (UIT_TERSE) the names involved alone, the code (the error's number,
// the spec's `uit_error_code`) saying what kind of mistake it is.
#ifdef UIT_TERSE
#define UIT_MSG(prose, terse) terse
#else
#define UIT_MSG(prose, terse) prose
#endif

#define UIT_NONE 0xFFFFu
#define UIT_ID_MAX 31           // bytes of a node id the toolkit keeps
#define UIT_FIELDS 16

// A growable byte buffer: the string pool, the scratch, the trace, the events' texts.
typedef struct {
    char *p;
    uint32_t n, cap;
} uit_buf_t;

int uit_buf_put(uit_buf_t *b, const char *s, size_t n);         // offset, NUL after; -1 no memory
int uit_buf_puts(uit_buf_t *b, const char *s);
int uit_buf_printf(uit_buf_t *b, const char *fmt, ...);         // appended, no NUL counted
void uit_buf_repr(uit_buf_t *b, const char *s, size_t n);       // Python's repr of a text
void uit_buf_free(uit_buf_t *b);

// A model's fields: each one's name (pool) and type.
typedef struct {
    uint8_t ftype[UIT_FIELDS];
    uint16_t fname[UIT_FIELDS];
} uit_fields_t;

// A state slot.
typedef struct {
    uint16_t name;              // pool
    uint8_t type;
    uint8_t nf, key;            // a model: its fields, its key field
    int16_t model;              // a row_of: its model's slot
    uint16_t ver;
    uint16_t vals, nvals;       // an enum: its values, NUL-separated in the pool
    int32_t i;
    double f;
    char *s;                    // a text, an enum's value, a row_of's key
    uint32_t sn, scap;
    // a model: its fields' names and types (made by uit_model, a model's alone)
    uit_fields_t *mf;
    int nrows;
    char *cells;                // every cell's text, NUL-terminated, row by row
    uint32_t *at;               // nrows * nf offsets into cells
    uint32_t cells_n;
    // the list's cursor sync (_sync): the value focus last landed for this cur slot
    char *sync;
    uint8_t sync_set;
    uint8_t in_gen;             // the page's gen counts it (all but a cur= nothing else reads)
    uint8_t absent;             // an optional state (ui.opt) with no value
} uit_slot_t;

// Template ops.
enum { OP_LIT = 1, OP_VAL, OP_COUNT, OP_PLURAL, OP_BYTES, OP_FMT, OP_CHOICE, OP_MAP };
// What a value op reads.
enum { RK_SLOT = 1, RK_FIELD, RK_ROWOF, RK_SYS, RK_FOCUS, RK_INPUT, RK_CURRENT };
// System bools (@desk ...).
enum { SYS_DESK = 1, SYS_HANDHELD, SYS_WIDE, SYS_TALL, SYS_TOUCH };

typedef struct {
    uint8_t op, rk;
    int16_t ref;                // a slot, a sys id, a cond
    int16_t f;                  // a field
    uint16_t c, d;              // pool offsets / lengths / sub-templates
} uit_op_t;

typedef struct {
    uint16_t src;               // pool
    uint16_t code, ncode;
    uint8_t row, lit;           // reads a row's field; nothing but literal text
    uint8_t type;               // what it renders: one piece's type, else UIT_TEXT
} uit_tmpl_t;

typedef struct {
    uint8_t neg, rk, cmp;       // cmp 0 truth, 1 ==, 2 !=
    int16_t ref, f;
    int32_t li;
    double lf;
    uint16_t ls;                // pool: the literal text
} uit_term_t;

typedef struct {
    uint16_t src;
    uint16_t first;
    uint8_t n, any;
} uit_cond_t;

typedef struct {
    uint16_t name, label;       // pool
    int16_t hint;               // a template
    int16_t when;               // a cond or -1
    uint8_t off, flags;
    uint8_t letter;             // its first letter, the band's
    uint16_t letters;           // pool: every letter it answers
    uint16_t icon;              // pool: its icon ("" none)
    int16_t row;                // row=: the row_of slot whose current row it acts on, or -1
} uit_act_t;

// A prop as the build gave it, for the dump.
enum { PK_TMPL = 1, PK_COND, PK_LIT, PK_ACT, PK_STATE };
typedef struct {
    uint16_t name, src, repr;   // pool (repr UIT_NONE: none)
    uint8_t kind;
    int16_t idx;
    uint16_t next;
} uit_prop_t;

// Node flags (`lit` of a two-way control).
enum { NL_CYCLE = 1, NL_STEPS = 1, NL_COMMIT = 1, NL_SECRET = 2, NL_FIELD = 4 };
// A label's text options (`nbtn`): wrap= in bits 0-1 (LT_WORD, LT_CHAR), its face's
// role + 1 in bits 2-4, the toolkit's `centre` for align= in bits 5-7, lines= given.
// A split's pad= and gap= (`btn`, `nbtn`): an index of GAP_NAMES; its align= (`lit`) of ALIGNS.
enum { GAP_S = 0, GAP_M, GAP_L, GAP_0 };
// A text view's options (`lit`); its syntax's index in `btn` (ui.py's _SYNTAXES).
enum { TV_FOLLOW = 1, TV_WRAP = 2, TV_PROSE = 4 };
// A tools row lent to a site (`lit`); a button drawn as its icon alone (`btn`).
enum { TL_LEND = 1, BT_ALONE = 1 };
enum { RW_MID = 1, RW_GROW = 2 };   // a row's lit: centred on the row it is a cell of; a fill of its stack
enum { CV_GROW = 1 };           // a canvas's lit: a fill along a row, its w= a basis
enum { LT_WORD = 1, LT_CHAR = 2, LT_WRAP = 3, LT_FACE_SHIFT = 2, LT_ALIGN_SHIFT = 5, LT_LINES = 256,
       LT_FIT = 512 };     // size="fit": its face only where its text fits the page's width

typedef struct {
    uint8_t kind, lit;          // a label's tone, a button's kind; a seg's cycle look, a slider's
                                // steps, a field's commit and secret (NL_*)
    uint16_t parent, kid, last, next;
    uint16_t key;               // pool
    int16_t act;
    int16_t shown, focus;       // conds
    int16_t t_main, t_value, t_icon, t_aux;     // templates (t_main a rows' group=): t_value a seg's row words and a
                                // slider's lo; t_aux a rows' empty=, a toggle's or a field's
                                // hint= and a slider's hi
    int16_t slot;               // a two-way control's state, a rows' model
    int16_t cur;                // a rows' cur= slot; a seg's label table; a toggle's row field (NL_FIELD,
                                // `slot` then its list's model)
    int16_t current;            // a rows' current= slot: the row whose key it holds is current
    int16_t changed;            // a rows' or a field's changed= action
    int16_t on;                 // a two-way control's on= action
    uint16_t props;
    uint16_t btn, nbtn;         // a dialog's buttons in e->btns; a field's max= in btn; a label's
                                // lines= in btn and its text options in nbtn (LT_*); a button's
                                // alone= in btn (BT_ALONE) and span= in nbtn; a grid's pad and
                                // gap (GAP_*) in btn and nbtn, its cols and rows in lit's nibbles
    int16_t mark;               // a button's marked= cond: drawn current while it holds
} uit_node_t;

// The panes (`ui.panes`, `ui.pane`): a split's children are the layout rule's cells along its
// axis, each held at its size or the one fill. Their props in the node's words:
//   both: `lit` PN_*; `current` the size= state (opt int); `changed` a start= state, else
//         start= in `btn` (desk units, PN_START)
//   panes: `nbtn` its gap (desk units); `slot` the focused= state, `cur` the grown= state
//   pane: `slot` its tabs= enum, `cur` its labels= table; its min= triples in e->btns from
//         `mark`, `nbtn` of them (the value's pool offset, PN_MIN_ANY "_" or PN_MIN_ONE a plain
//         pair; then w, h in desk units)
enum { PN_GROW = 1, PN_HEADLESS = 2, PN_COL = 4, PN_START = 8 };
enum { PN_MIN_ANY = -1, PN_MIN_ONE = -2 };
#define PN_KIDS 8               // a split's children at most
#define PN_DEPTH 4              // panes in panes at most
#define PN_MAX 16               // the panes, dividers and hosted bodies a pass records at most
#define PN_SPLITS 8
typedef struct {
    int16_t node, r[4];
    uint8_t depth, path[PN_DEPTH];
} uit_pa_t;

#define UIT_MCELLS 16           // the model row cells a pass records at most
typedef struct {
    int16_t node, model, row, r[4];
} uit_mc_t;                     // a row from a model: one row's cell                     // a pane arranged, a body placed, a pane drawn
typedef struct {
    int16_t r[4];
    uint8_t depth, path[PN_DEPTH], i, axis;
} uit_pd_t;                     // a divider arranged
typedef struct {
    int16_t node, r[4], gap;
    uint8_t nk, axis, depth, path[PN_DEPTH];
    int16_t kids[PN_KIDS];
    int32_t sizes[PN_KIDS], mins[PN_KIDS], held[PN_KIDS];      // held -1: it grows
} uit_ps_t;                     // a split as its pass laid it out
// What the panes of a page answer a host (made at the first pass over a page that has them).
typedef struct {
    uit_pa_t panes[PN_MAX];     // the last placement's arrangement, even under a pane alone
    uit_pd_t divs[PN_MAX];
    uit_pa_t placed[PN_MAX];    // each hosted body where the last placement put it
    uit_pa_t order[PN_MAX];     // the panes this pass drew
    uit_ps_t splits[PN_SPLITS]; // this pass's splits
    uint8_t npanes, ndivs, nplaced, norder, nsplits;
    uint8_t depth, ghost, arranging;
    uint8_t path[PN_DEPTH];
    int16_t root, alone;        // the outermost panes this pass; the pane the placement stood alone
    int16_t least[2];           // the outermost panes' least size, gutters in
    uint32_t tap_key, tap_at;   // the last tap on a divider (its split's id hashed, its index) and when
    int32_t writes;             // held sizes written or put back since the host last asked
    int32_t held[UIT_SP_N];     // the divider this pass's splits hold (UIT_SP_*)
    uint32_t clock;             // the host's clock, ms (a double tap)
    uint8_t mouse, owe;         // the pointer hovers; a let-go wrote held sizes this pass
} uit_arr_t;

// A label table: its name, keys and words NUL-separated in the pool.
typedef struct {
    uint16_t name, keys, words, n;
} uit_labels_t;

// A field's text as it is typed: its node's key; a commit field's draft
// while `has`.
typedef struct {
    char *key;
    char *buf;
    uint16_t cap, n;
    uint8_t has;
} uit_draft_t;

// A focus id this frame drew, and what it names.
typedef struct {
    char id[UIT_ID_MAX + 1];
    int16_t node, row, act;
    uint8_t kind;
    uint16_t gen;               // the build it was drawn under
} uit_id_t;

typedef struct {
    uint8_t kind;
    int16_t a, b, c;
    uint32_t text;              // into evtext
} uit_ev_t;

typedef struct {
    int16_t node, prop;
    char *rowkey, *val;
} uit_drew_t;

typedef struct {
    char *key;                  // a list's key
    char *keys;                 // its rows' keys, NUL-separated
    uint32_t n;                 // bytes of keys
    int has;
    uint16_t ver;               // its model's (`slot`) version as the keys were taken
    int16_t slot;
} uit_order_t;

// A rows list laid out in view only (`draw_vrows`): each row's height as it last drew (-1 never),
// the unit a row never drawn counts as, kept by the list's key while its row count, the page's
// width and the font scale hold.
typedef struct {
    uint32_t key;               // the list's key, hashed
    int32_t n;                  // its rows when the heights were made
    int16_t w, unit;
    uint8_t fs;
    int16_t *h;
    int cap;
} uit_vrows_t;

struct uit {
    char err[160];
    char code[4];               // the spec's code of a UIT_E_CHECK error
    int ccur;                   // while a prop compiles: its list's current= slot, or -1
    uit_buf_t pool;
    uit_slot_t *slots;
    int nslots, cslots;
    uit_act_t *acts;
    int nacts, cacts;
    uit_tmpl_t *tmpls;
    int ntmpls, ctmpls;
    uit_op_t *ops;
    int nops, cops;
    uit_cond_t *conds;
    int nconds, cconds;
    uit_term_t *terms;
    int nterms, cterms;
    uit_node_t *nodes;
    int nnodes, cnodes;
    uit_prop_t *props;
    int nprops, cprops;
    int16_t *btns;
    int nbtns, cbtns;
    uit_labels_t *labels;
    int nlabels, clabels;
    // where the declarations end: a build is truncated back to these
    int decl_done;
    uint32_t m_pool;
    uint16_t *intern;           // while declaring and building: the pool's texts by hash (uit_pool_put)
    int ninterned;
    int m_tmpls, m_ops, m_conds, m_terms;
    // the build
    uint16_t page_id;
    uint16_t gen;               // builds so far
    int fresh;
    char *want;                 // a node to focus next frame
    // a frame
    int flags, fs, input;
    int16_t cx, cy, cw, ch;
    int16_t pw;                 // the page's width, as its page call placed it
    uit_buf_t scratch;
    uit_id_t *ids, *prev;
    int nids, nprev, cids, cprev;
    uit_id_t focused;
    int has_focused;
    char focus_key[UIT_ID_MAX + 1];
    int dup;
    // events and trace
    uit_ev_t *evs;
    int nevs, cevs, evat;
    uit_buf_t evtext;
    uit_buf_t trace;
    uint32_t trace_at;
    uit_drew_t *drew;
    int ndrew, cdrew;
    uit_order_t *orders;
    int norders, corders;
    uit_draft_t *drafts;
    int ndrafts, cdrafts;
    uint32_t edits;             // drafts' edits: the page's gen moves with them
    uint8_t gen_safe;           // the page may carry a gen: no canvas, not too many states
    uint8_t gen_vals;           // the engine values its text names, which its gens count (GV_*)
    uint32_t gv;                // what they are this frame, hashed (the focus at the frame's begin, the input)
    // a text view's lines: where each line of text slot `tvl_slot` starts, as its version `tvl_ver` held
    uint32_t *tvl;
    int tvl_n, tvl_cap;
    int16_t tvl_slot;
    uint16_t tvl_ver;
    const char *tvl_s;
    uit_draw_fn draw_fn;        // a canvas's draw, the front end's
    void *draw_ud;
    int32_t rect[4];            // the page's room for its placed rows (`self.rect`)
    uint8_t in_cell;            // drawing a split's column: a button takes its natural width
    uint8_t tv_stop, tv_stop_now;   // a text view that runs on drew last frame / this frame (the band's PgUp PgDn)
    uint8_t in_pop;             // drawing a popover's body: a split there lays out as its cells (`_pop_split`)
    int16_t foot;               // the page's shown foot this frame, or -1
    int32_t page_gen;           // the gen the last frame's page carried, or -1 (none)
    int32_t foot_r[4];          // its rect
    uit_buf_t cells;            // a table's columns and cells, made again where its key moved
    uint32_t cells_key;         // what they were made from: the build, the model's and the cols' states' versions
    int16_t cells_node;         // the table they are, + 1 (0 none)
    uit_vrows_t *vr;            // the rows lists laid out in view only
    int nvr, cvr;
    // the placed layout: each node's rect as the last pass placed it (w -1: not placed)
    uint8_t placed, placing;
    int16_t ground[4];
    int16_t *rects;
    int crects;
    uit_mc_t mcells[UIT_MCELLS];   // a pass's model row cells (`put_cell`)
    uint8_t nmcells;
    uit_arr_t *arr;             // the panes' answers (a page with panes or a hosted body)
    uint8_t has_panes;
    int16_t du;                 // the desk unit the host hands (`uit_panes_set`)
    int16_t unit;               // a placed row's unit the host hands (0: 1 on a desk, else fs)
    int16_t pane_cur;           // the pane the keys reach, -1 none
    int16_t late[8];            // the popovers and dialogs a stack draws last, over it
    int nlate;
    // a part's rect a front end placed this frame, that a popover hangs from ("code.caret")
    char anc_name[4][UIT_ID_MAX + 1];
    int16_t anc[4][4];
    int nanc;
    // the frame drawn last, for uit_still: the versions it read and its focus
    uint32_t still_ver;
    uit_id_t still_focus;
    uint8_t still_ok, still_had;
};

// The engine values a page's gens count: @focus, @input or @touch.
enum { GV_FOCUS = 1, GV_INPUT = 2 };

// A list: rows and a table keep a model, a cur= and a removal order.
#define UIT_IS_LIST(kind) ((kind) == UIT_ROWS || (kind) == UIT_TABLE || (kind) == UIT_CARDS)
// The states a page's gen can count (more: it carries none).
#define UIT_GEN_SLOTS 128
// A cards list's region's gen (`_cell_gens`): the builds and edits, and the versions of what its
// rows read; -1 past UIT_GEN_SLOTS.
int32_t uit_cell_gen(uit_t *e, int k);
// What template `t` reads, as a key: the versions of the states it reads summed; `*engine` set
// where it reads an engine value (@focus, @input, a system bool, current=), which no key holds.
uint32_t uit_tmpl_key(const uit_t *e, int t, int *engine);
// The case of switch node `k` its state's value names, else its "_", else -1.
int uit_case_of(uit_t *e, int k);

// Each kind's name, as the trace and the dump spell it.
extern const char *const uit_kind_names[UIT_KINDS];

int uit_grow(void **p, int *cap, int need, size_t size);
#define UIT_GROW(e, arr, n, cap) uit_grow((void **)&(e)->arr, &(e)->cap, (e)->n + 1, sizeof(*(e)->arr))
int uit_fail(uit_t *e, int code, const char *fmt, ...);
// A mistake the spec names: UIT_E_CHECK, its code kept for uit_error_code.
int uit_check_fail(uit_t *e, const char *code, const char *fmt, ...);
const char *uit_str(const uit_t *e, uint16_t off);
// `n` bytes of `s` in the pool, NUL after: the offset of the same bytes where the declarations or
// the build put them already, else of a new copy; -1 no memory or no 16-bit offset left.
int uit_pool_put(uit_t *e, const char *s, size_t n);

// moy_uitree_lang.c
int uit_tmpl_compile(uit_t *e, const char *src, int model);   // a template's index
int uit_cond_compile(uit_t *e, const char *src, int model);   // a condition's index
// Rendered at the scratch's end: its offset; the text's length into *n.
uint32_t uit_tmpl_render(uit_t *e, int t, int model, int row, uint32_t *n);
int uit_cond_eval(uit_t *e, int c, int model, int row);
int uit_slot_find(const uit_t *e, const char *name, size_t n);
const char *uit_cell(const uit_t *e, int model, int row, int f);
int uit_field_find(const uit_t *e, int model, const char *name, size_t n);
int uit_row_find(const uit_t *e, int model, const char *key, size_t n);
const char *uit_type_name(int type);

// moy_uitree.c
void uit_trace(uit_t *e, const char *kind, const char *name, const char *fmt, ...);
int uit_event(uit_t *e, int kind, int a, int b, int c, const char *text, size_t n);
void uit_slot_repr(uit_t *e, uit_buf_t *b, int slot);
int uit_set_text_q(uit_t *e, int slot, const char *s, size_t n);   // no version moved
void uit_after_removal(uit_t *e, int node);
const char *uit_label_word(const uit_t *e, int table, const char *key, size_t n);

// moy_uitree_walk.c
int uit_act_can(uit_t *e, int a, int model, int row);
int uit_shown(uit_t *e, int node, int model, int row);
void uit_cur_moved(uit_t *e, int node, const char *v, size_t n, const char *why);
int uit_model_of(uit_t *e, int node);
// The rows node `node` is in (itself included), or -1.
int uit_list_of(const uit_t *e, int node);
// Whether row `row` of `model` is the current one of rows node `list` (its current=).
int uit_row_current(const uit_t *e, int list, int model, int row);
int uit_row_slot_current(const uit_t *e, int slot, int model, int row);

#endif
