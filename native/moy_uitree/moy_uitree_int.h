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
} uit_slot_t;

// Template ops.
enum { OP_LIT = 1, OP_VAL, OP_COUNT, OP_PLURAL, OP_BYTES, OP_FMT, OP_CHOICE };
// What a value op reads.
enum { RK_SLOT = 1, RK_FIELD, RK_ROWOF, RK_SYS, RK_FOCUS, RK_INPUT };
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
    uint8_t letter;
} uit_act_t;

// A prop as the build gave it, for the dump.
enum { PK_TMPL = 1, PK_COND, PK_LIT, PK_ACT, PK_STATE };
typedef struct {
    uint16_t name, src, repr;   // pool (repr UIT_NONE: none)
    uint8_t kind;
    int16_t idx;
    uint16_t next;
} uit_prop_t;

typedef struct {
    uint8_t kind, lit;          // lit: a label's tone, a button's kind
    uint16_t parent, kid, last, next;
    uint16_t key;               // pool
    int16_t act;
    int16_t shown, focus;       // conds
    int16_t t_main, t_value, t_icon, t_aux;     // templates
    int16_t slot;               // a toggle's state, a rows' model
    int16_t cur;                // a rows' cur= slot
    int16_t changed;            // a rows' changed= action
    uint16_t props;
    uint16_t btn, nbtn;         // a dialog's buttons in e->btns
} uit_node_t;

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
};

int uit_grow(void **p, int *cap, int need, size_t size);
#define UIT_GROW(e, arr, n, cap) uit_grow((void **)&(e)->arr, &(e)->cap, (e)->n + 1, sizeof(*(e)->arr))
int uit_fail(uit_t *e, int code, const char *fmt, ...);
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

// moy_uitree_walk.c
int uit_act_can(uit_t *e, int a, int model, int row);
int uit_shown(uit_t *e, int node, int model, int row);
void uit_cur_moved(uit_t *e, int node, const char *v, size_t n, const char *why);
int uit_model_of(uit_t *e, int node);

#endif
