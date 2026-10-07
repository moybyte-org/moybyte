// The glass's canvases (docs/kernel_survival_2026-10.md §3.1).
//
// A canvas is a row of kind CANVAS: where it draws (the pixels, their capacity,
// the BUF row they are when they are one), its size and stride, its owner, the
// draw state the gates read -- camera, clip, size, font scale and the profiling
// counters, in the order device/device_canvas.py's _ST_* names them -- and the
// 64-entry RGB565 table colour indices resolve through.
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

// The draw state, in device_canvas.py's _ST_* order.
enum {
    MOY_ST_CAM_X = 0, MOY_ST_CAM_Y,
    MOY_ST_CX0, MOY_ST_CY0, MOY_ST_CX1, MOY_ST_CY1,
    MOY_ST_W, MOY_ST_H,
    MOY_ST_FONT_SCALE,
    MOY_ST_PROF,
    MOY_ST_N_FILL, MOY_ST_N_TEXT,
    MOY_ST_T_FILL, MOY_ST_T_TEXT,
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
    uint16_t pal[64];
} moy_canvas_row_t;

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
