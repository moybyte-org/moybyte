// The public UI API's engine, inside (moy_ui_api.h is its outside): what
// its files share -- the stop flags, the floor's rects and clips, and the
// helpers one file defines and another calls, each named mua_*. Only the
// engine's files include it (README.md's file map).

#ifndef MOY_UI_API_INT_H
#define MOY_UI_API_INT_H

#include <string.h>

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define KEPT_ALLOC(n) heap_caps_malloc_prefer((n), 2, MALLOC_CAP_SPIRAM, MALLOC_CAP_DEFAULT)
#else
#include <stdlib.h>
#define KEPT_ALLOC(n) malloc(n)
#endif

#include "moy_ui_api.h"
#include "moy_canvas.h"
#include "moy_theme.h"

#define MAGIC 0x6d756931u           // "mui1"
#define PAGE_SALT 0x9e3779b9u
#define SCROLL_SALT 0x85ebca6bu
#define DIALOG_SALT 0xc2b2ae35u
#define POPOVER_SALT 0x27d4eb2fu
#define NO_PAGE 0xffu
// A fade's rows at the scale: its first two barely tint, so ten read as
// D-09's eight (docs/history/look_2026-10.md).
#define FADE_H 10

// A stop's flags.
enum {
    STOP_PAGE = 1,          // a page's content rect: never focused, what a drag scrolls
    STOP_KEEP_LR = 2,       // a slider: left and right are its own
    STOP_KEEP_UD = 4,       // a list: up and down are its own...
    STOP_AT_TOP = 8,        // ...but up at its first row
    STOP_AT_END = 16,       // ...and down at its last
    STOP_HOLD = 32,         // nav = false on a part: focus, the keys, a held press are its
    STOP_NAV_OFF = 64,      // nav = false on a page: the engine moves no focus in it
    STOP_MODAL = 128,       // a dialog's or a popover's: while one is drawn, the others take no input
    STOP_DLG_BTN = 256,     // a dialog's button (focus opens on its body first)
    STOP_FIELD = 512,       // a text field: it takes the typed keys, and `a` is a letter
    STOP_ROWS = 1024,       // a list's or a table's rows on its page: `row` and `top` hold
    STOP_CARD = 2048,       // a dialog's or a popover's body: its scroll into view lands at once
    STOP_AT = 4096,         // placed by ui.at
    STOP_PIC = 8192,        // the app draws a picture in it (a card, a part)
    STOP_GEN = 16384,       // ...whose gen it gave
    STOP_GRID = 32768,      // a card of a grid
};

// A rect by its edges: the floor's (moy_ui_api.c's floor section).
typedef struct {
    int32_t x0, y0, x1, y1;
} rc_t;

// The state bits whose marks a stop draws: a stop whose bits moved is
// drawn again on a shift frame.
#define MARKS (MOY_UI_ST_FOCUS | MOY_UI_ST_ACTIVATED | MOY_UI_ST_PRESSED | MOY_UI_ST_HOVER)

// What a stacked row draws inside: the page's content rect; on a shift
// frame, each piece of the frame's region in turn.
typedef struct {
    moy_canvas_row_t *row;
    int32_t saved[4];
    int on;
    moy_ui_ctx_t *c;                // NULL: one clip, no pieces
    int i, stacked;
    rc_t in, ext;
} clip_t;

// A draw over box b that no row clips (a fade, a hint band, a page's
// ground): through every piece it reaches on a shift frame, once otherwise.
#define EACH_PIECE(c, k, s, b) \
    for (int k##_ok = mua_clip_row((c), &(k), (s), (b), 0, (b)); k##_ok; k##_ok = mua_clip_next(&(k)))
// A stacked row's draw: clipped to `in`, through every piece it reaches.
#define EACH_ROW(c, k, s, b, stacked, in) \
    for (int k##_ok = mua_clip_row((c), &(k), (s), (b), (stacked), (in)); k##_ok; \
         k##_ok = mua_clip_next(&(k)))

// A text view's (moy_ui_api_text.c).
#define TEXT_SALT 0x27d4eb2fu
#define TEXT_PAD 8                  // the text's inset from the view's left, at the scale
#define TEXT_MARK 3                 // the current line's mark, in from the view's left
#define TEXT_WRAP_ROWS MOY_UI_TEXT_BREAKS

// A context's kept ring (moy_ui_api_kept.c).
typedef struct {
    const moy_ui_ctx_t *owner;          // NULL: free
    uint32_t used;                      // its last begin (kept_mem_t's count)
    uint8_t ri, n;                      // the newest slot, the slots held
    moy_ui_kept_t kt[MOY_UI_KEPT_RING];
} kept_ring_t;

// The engine's state one file keeps and another reads.
extern uint32_t mua_scope;   // moy_ui_api.c
extern uint32_t mua_audit_on, mua_audit_fail;   // moy_ui_api.c
extern int mua_no_foot;   // moy_ui_api.c

// moy_ui_api.c
uint32_t mua_hash(const uint8_t *p, size_t n, uint32_t salt);
int mua_kmetric(int kind, int slot);
int mua_row_h(const moy_ui_ctx_t *c);
int mua_inside(moy_ui_box_t b, int x, int y);
int32_t mua_word(const uint8_t *w, int i);
const moy_ui_over_t *mua_over(const moy_ui_over_t *o);
int mua_find(const moy_ui_stop_t *v, int n, uint32_t id);
int mua_page_at(const moy_ui_stop_t *v, int n, int x, int y);
int mua_gap(int a0, int a1, int b0, int b1);
moy_ui_keep_t *mua_keep(moy_ui_ctx_t *c, uint32_t id, int make);
moy_ui_keep_t *mua_keep_of(moy_ui_ctx_t *c, uint32_t id);
const moy_ui_stop_t *mua_page_of_keep(const moy_ui_stop_t *v, int n,
                                      const moy_ui_keep_t *k);
int mua_aim(moy_ui_keep_t *k, const moy_ui_stop_t *p, int32_t to);
const moy_ui_stop_t *mua_table_of(const moy_ui_stop_t *v, int n, uint32_t id);
int mua_same_box(moy_ui_box_t a, moy_ui_box_t b);
int mua_ground_of(const moy_ui_ctx_t *c);
moy_ui_keep_t *mua_page_keep(moy_ui_ctx_t *c);
moy_ui_box_t mua_place_row(moy_ui_ctx_t *c, int h, int *stacked, int row);
moy_ui_box_t mua_place(moy_ui_ctx_t *c, int h, int *stacked);
rc_t mua_rc_of(moy_ui_box_t b);
int mua_rc_cut(rc_t *a, rc_t b);
int mua_rc_meets(rc_t a, rc_t b);
moy_ui_box_t mua_box_of(rc_t r);
int mua_armed(const moy_ui_ctx_t *c);
int mua_floor_holds(const moy_ui_ctx_t *c, moy_ui_box_t b);
int mua_floor_meets(const moy_ui_ctx_t *c, moy_ui_box_t b);
moy_ui_box_t mua_floor_of(const moy_ui_ctx_t *c, moy_ui_box_t b);
int mua_outside_floor(const moy_ui_ctx_t *c, moy_ui_box_t b);
void mua_floor_add(moy_ui_ctx_t *c, rc_t b, int ground);
void mua_floor_rows(moy_ui_ctx_t *c, int y0, int y1, int ground);
void mua_dlg_widen(moy_ui_ctx_t *c, int y0, int y1);
void mua_ground_fill(const moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b);
void mua_ground_due(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
int mua_clip_in(clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b);
void mua_clip_out(clip_t *k);
int mua_clip_meets(const moy_ui_sink_t *s, int y, int h, int m);
int mua_clip_row(moy_ui_ctx_t *c, clip_t *k, const moy_ui_sink_t *s, moy_ui_box_t b,
                 int stacked, moy_ui_box_t in);
int mua_clip_next(clip_t *k);
moy_ui_box_t mua_card_of(const moy_ui_ctx_t *c);
void mua_band_mark(moy_ui_ctx_t *c, moy_ui_box_t b, int stacked, int marks);
void mua_band(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
              int marks);
void mua_group_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
int mua_in_dialog(const moy_ui_ctx_t *c);
void mua_sig_mix(moy_ui_ctx_t *c, const void *p, size_t n);
void mua_sig_int(moy_ui_ctx_t *c, int32_t v);
int mua_stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags, moy_ui_span_t name);
int mua_pic_note(moy_ui_ctx_t *c, const moy_ui_over_t *o, int pic);
void mua_pic_redraw(moy_ui_ctx_t *c, moy_ui_box_t r);
int mua_interact(int st);
int mua_mark_flags(int st, const moy_ui_over_t *o);
uint32_t mua_id_of(const moy_ui_over_t *o, moy_ui_span_t label);
moy_ui_span_t mua_name_of(const moy_ui_over_t *o, moy_ui_span_t label);
void mua_vrow_init(const moy_ui_ctx_t *c, moy_ui_vrow_t *r, moy_ui_span_t label);
void mua_vrow_draw(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t b, int stacked,
                   int kind, int st, const moy_ui_over_t *o, const moy_ui_vrow_t *r);
int mua_vrow_stop(moy_ui_ctx_t *c, uint32_t id, moy_ui_box_t b, int flags,
                  const moy_ui_over_t *o, moy_ui_span_t label);
int mua_mark_room(const moy_ui_ctx_t *c);
void mua_table_strip(moy_ui_ctx_t *c, int y0, int y1, int base, int rh, int pic);
void mua_table_in_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                        int mark, int of);
void mua_view_in_floor(moy_ui_ctx_t *c, int of);
void mua_table_floor(moy_ui_ctx_t *c, int base, int rh, int was, int was_mark, int now,
                     int mark, int pic, int of);
void mua_page_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
int mua_rule_h(const moy_ui_ctx_t *c);
void mua_rows_note(moy_ui_ctx_t *c, uint32_t id, int row, int top);
int mua_pmetric(const moy_ui_ctx_t *c, int slot);
void mua_nest_open(moy_ui_ctx_t *c, int kind, uint32_t id, moy_ui_box_t b, int used,
                   int flags, const moy_ui_over_t *o);
void mua_edge_fades(moy_ui_ctx_t *c, const moy_ui_sink_t *s, moy_ui_box_t v, int rows,
                    int scroll, int ci, int head, int h);
void mua_foot_fade(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
int mua_nest_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s, int kind);

// moy_ui_api_kept.c
kept_ring_t *mua_ring_of(const moy_ui_ctx_t *c, int make);
void mua_kept_drop(const moy_ui_ctx_t *c);
void mua_kept_begin(moy_ui_ctx_t *c, int32_t f, int k);
void mua_kept_table(moy_ui_ctx_t *c, kept_ring_t *r, const moy_ui_over_t *o, uint32_t id,
                    uint32_t geo, moy_ui_box_t view, int32_t offset, int rh, int count,
                    int cur, int at, int mark, int vis0, int nk, int np, const uint32_t *hv,
                    int base, int sp, int xa);

// moy_ui_api_table.c
void mua_parts_close(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
void mua_wash(const moy_ui_ctx_t *c, const moy_ui_sink_t *s);

// moy_ui_api_pop.c
void mua_card_finish(moy_ui_ctx_t *c, const moy_ui_sink_t *s);
void mua_grid_end(moy_ui_ctx_t *c, const moy_ui_sink_t *s);

#endif
