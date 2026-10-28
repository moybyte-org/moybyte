// The glass's canvases (docs/kernel_survival_2026-10.md §3.1).
//
// A canvas is a row of kind CANVAS: where it draws (the pixels, their capacity,
// the BUF row they are when they are one), its size and stride, its owner, the
// draw state the gates read -- camera, clip, size, font scale and the profiling
// counters, in the order device/device_canvas.py's _ST_* names them -- and the
// 128-entry RGB565 table colour indices resolve through: entries 0..63 are
// MOY64 through the canvas's palette and pal(), written by the canvas class;
// 64..127 are the live theme's block (docs/look_2026-10.md step 5), written
// here for every row at once and never remapped. A colour index is masked
// with moy_canvas_cmask, 127 for the shell and 63 while a cart's code runs
// (moy_ui_cart_text), so a cart's colour stays its index modulo 64.
//
// THE DRAW GATES (native/moy_gfx's draw context) hold a canvas's slot and the
// generation it had when the gate was made, and check the two before every op:
// an index and a compare, never a table walk, and a canvas released under a
// gate is refused instead of drawn into. A row's address is stable for the
// table's life (the table is reserved whole at init), so the binding may hand
// its state and palette out as views.

#ifndef MOY_CANVAS_H
#define MOY_CANVAS_H

#include <stddef.h>
#include <stdint.h>

#include "moy_buf.h"

#define MOY_CANVAS_PAL 128      // a row's colour table
#define MOY_CANVAS_BLOCK 64     // ...whose upper half is the theme's block

// The draw state, in device_canvas.py's _ST_* order.
enum {
    MOY_ST_CAM_X = 0, MOY_ST_CAM_Y,
    MOY_ST_CX0, MOY_ST_CY0, MOY_ST_CX1, MOY_ST_CY1,
    MOY_ST_W, MOY_ST_H,
    MOY_ST_FONT_SCALE,
    MOY_ST_PROF,
    MOY_ST_N_FILL, MOY_ST_N_TEXT,
    MOY_ST_T_FILL, MOY_ST_T_TEXT,
    MOY_ST_FILLP,           // 1 while a fill pattern is set: rect/rectb gates decline
    MOY_ST_DMA_MIN,         // a rect this many pixels or more goes to the canvas's DMA fill
    MOY_ST_LEN
};

typedef struct {
    uint16_t *px;               // where it draws, or NULL before the first point
    uint32_t cap;               // ...in pixels
    uint32_t buf;               // the BUF row px is, or 0 (a compositor's buffer)
    uint32_t owner;
    uint16_t w, h;
    uint32_t caps;
    int32_t st[MOY_ST_LEN];
    uint16_t pal[MOY_CANVAS_PAL];
} moy_canvas_row_t;

// The live theme's block, in this build's wire order: into every row's
// entries 64..127 and every row made from now on. NULL clears it to 0.
void moy_canvas_block(const uint16_t block[MOY_CANVAS_BLOCK]);
// The block as it is now (what the canvas class's Python verbs read).
uint16_t *moy_canvas_block_words(void);
// 127, or 63 while a cart's code runs.
extern uint8_t moy_canvas_cmask;
void moy_canvas_cart(int on);

int moy_canvas_new(uint32_t *h, uint16_t w, uint16_t h_px, uint32_t caps,
                   uint32_t owner);
int moy_canvas_get(uint32_t h, moy_canvas_row_t **row);
// Draw into `px` (cap pixels): a compositor's back buffer after a swap, or a
// layer's own pixels. `buf` names the BUF row they are, or 0.
int moy_canvas_point(uint32_t h, uint16_t *px, uint32_t cap, uint32_t buf);
int moy_canvas_release(uint32_t h);

// The gate's check: the canvas table, and the slot/generation word a gate
// recorded at its making (moy_canvas_word). NULL when the row is gone.
const moy_htab_t *moy_canvas_table(void);
uint32_t moy_canvas_word(uint32_t h);

static inline moy_canvas_row_t *moy_canvas_check(const moy_htab_t *t,
                                                 uint32_t slot, uint32_t word) {
    if (t == NULL || slot >= t->used || t->meta[slot] != word) {
        return (moy_canvas_row_t *)0;
    }
    return (moy_canvas_row_t *)moy_htab_row(t, slot);
}

uint32_t moy_canvas_count(void);

#endif // MOY_CANVAS_H
