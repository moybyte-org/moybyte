// The engine's insides, shared by its three files (moy_uitree.h is the API).
#ifndef MOY_UITREE_INT_H
#define MOY_UITREE_INT_H

#include "moy_uitree.h"

#ifndef UIT_MALLOC
#include <stdlib.h>
#define UIT_MALLOC malloc
#define UIT_REALLOC realloc
#define UIT_FREE free
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
    // a model
    uint8_t ftype[UIT_FIELDS];
    uint16_t fname[UIT_FIELDS];
    int nrows;
    char *cells;                // every cell's text, NUL-terminated, row by row
    uint32_t *at;               // nrows * nf offsets into cells
    uint32_t cells_n;
    // the list's cursor sync (_sync): the value focus last landed for this cur slot
    char *sync;
    uint8_t sync_set;
    uint8_t in_gen;             // the page's gen counts it (all but a cur= nothing else reads)
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
enum { LT_WORD = 1, LT_CHAR = 2, LT_WRAP = 3, LT_FACE_SHIFT = 2, LT_ALIGN_SHIFT = 5, LT_LINES = 256 };

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
                                // lines= in btn and its text options in nbtn (LT_*)
} uit_node_t;

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
} uit_order_t;

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
    uint8_t gen_safe;           // no template names @focus, @input or @touch
};

// Each kind's name, as the trace and the dump spell it.
extern const char *const uit_kind_names[UIT_KINDS];

int uit_grow(void **p, int *cap, int need, size_t size);
#define UIT_GROW(e, arr, n, cap) uit_grow((void **)&(e)->arr, &(e)->cap, (e)->n + 1, sizeof(*(e)->arr))
int uit_fail(uit_t *e, int code, const char *fmt, ...);
// A mistake the spec names: UIT_E_CHECK, its code kept for uit_error_code.
int uit_check_fail(uit_t *e, const char *code, const char *fmt, ...);
const char *uit_str(const uit_t *e, uint16_t off);

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
