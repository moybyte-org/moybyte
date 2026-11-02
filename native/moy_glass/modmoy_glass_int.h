// moy_glass's MicroPython binding, inside: what modmoy_glass.c and the
// compositors' bindings (modmoy_glass_banded.c, modmoy_glass_dsi.c,
// modmoy_glass_rot.c, one per moy_present_*.c) share -- the build's switches,
// the Buf and compositor objects the kernel's frame reads, and the helpers one
// file defines and another calls. Only those files include it.

#ifndef MODMOY_GLASS_INT_H
#define MODMOY_GLASS_INT_H

#include <string.h>

#include "py/binary.h"
#include "py/gc.h"
#include "py/objarray.h"
#include "py/objlist.h"
#include "py/objtuple.h"
#include "py/runtime.h"
#include "py/mphal.h"

#if __has_include("esp_heap_caps.h")
#define MOY_GLASS_BOARD 1
#include "esp_heap_caps.h"
#else
#include <stdlib.h>
#endif

#include "moy_buf.h"
#include "moy_canvas.h"
#include "moy_present.h"

// The compositors (moy_present.h) and front(): every console's present. A
// build that presents through something else -- the browser's worker blits
// its own framebuffer -- sets MOY_GLASS_PRESENT=0 and has none of them: no
// BandedCompositor, DsiCompositor, RotatedCompositor or front, absent, so
// `hasattr(moy_glass, "front")` is the probe.
#ifndef MOY_GLASS_PRESENT
#define MOY_GLASS_PRESENT 1
#endif

// The panel's backlight, the entry its module gives the recovery floor
// (MOY_KERNEL_PANEL), on every console.
#ifdef MOY_KERNEL_PANEL
extern void MOY_KERNEL_PANEL(backlight)(int on);
#endif

// A Buf: its row, and the view it hands out (modmoy_glass.c's -- Buf).
typedef struct {
    mp_obj_base_t base;
    uint32_t h;
    mp_obj_t view;              // the memoryview handed out
    mp_obj_t heap;              // a HEAP row's bytearray, or MP_OBJ_NULL
} glass_buf_obj_t;

// glass_buf, the module's buf(): a compositor takes its buffers through it.
mp_obj_t glass_buf(size_t n_args, const mp_obj_t *a);

#if MOY_GLASS_PRESENT

#ifndef MOY_GLASS_BANDED
#define MOY_GLASS_BANDED 0
#endif

#if MOY_GLASS_BANDED
// A banded board's panel transport (MOY_KERNEL_PANEL's kwait/kkick/kship):
// the banded compositor's, and the wait the VM's teardown takes.
extern bool MOY_KERNEL_PANEL(wait)(void);
extern int MOY_KERNEL_PANEL(kick)(int n);
extern int MOY_KERNEL_PANEL(ship)(int n);
#endif

// The banded compositor is built on a banded board and on the desktop
// MicroPython (a test's panel module double); a DSI board has none.
#if MOY_GLASS_BANDED || !defined(MOY_GLASS_BOARD)
#define MOY_GLASS_BANDED_TYPE 1

// The compositors' objects, which the kernel's frame reads too.
typedef struct {
    mp_obj_base_t base;
    moy_banded_t b;
    moy_banded_ops_t ops;
    mp_obj_t panel;
    mp_obj_t gfx;
    mp_obj_t fbs[MOY_PRESENT_MAX_FBS];
    mp_int_t w, h;
} glass_banded_obj_t;
#endif

typedef struct {
    mp_obj_base_t base;
    moy_dsi_t d;
    moy_dsi_ops_t ops;
    mp_obj_t dsi;
    mp_obj_t ppa;
    mp_obj_t gfx;
    mp_obj_t fbs;               // list of the scan buffers' memoryviews
    mp_obj_t stamp;             // the deferred stamp's argument tuple, or None
    mp_obj_t refreshes;         // the panel's refresh count, or None
    mp_int_t w, h;
    bool shift_fly;             // a shift_last's copy is queued
} glass_dsi_obj_t;

typedef struct {
    mp_obj_base_t base;
    moy_rot_t r;
    moy_rot_ops_t ops;
    mp_obj_t dsi, ppa, gfx;
    mp_obj_t bufs[10];          // by the engine's buffer ids
    mp_obj_t paint, quiet;      // this frame's game callables
    mp_obj_t stamp;             // the deferred stamp's tuple, or None
    mp_obj_t held;              // the glass Bufs the paint buffers and scratch are
    mp_obj_t rotate, rotate_scale, bounce, refreshes;   // the transport's verbs
    size_t scratch_n;
    bool shift_fly;             // a shift_last's copy is queued
    uint32_t shifts, shift_refused;     // shift_last: taken, refused behind a rotate
} glass_rot_obj_t;

// The compositors' types, for the kernel's frame and the module table.
extern const mp_obj_type_t glass_banded_type;   // modmoy_glass_banded.c
extern const mp_obj_type_t glass_dsi_type;   // modmoy_glass_dsi.c
extern const mp_obj_type_t glass_rot_type;   // modmoy_glass_rot.c

// modmoy_glass.c: shift_last and copy_last_into, every compositor's
void glass_shift_fence(mp_obj_t ppa, bool *fly);
bool glass_shift(mp_obj_t ppa, mp_obj_t dst, mp_obj_t src, mp_int_t W, mp_int_t H,
                 const mp_obj_t *a, bool *fly, int *b);
void glass_copy_into(mp_obj_t ppa, mp_obj_t src, mp_int_t W, mp_int_t H,
                     size_t n_args, const mp_obj_t *a);

// modmoy_glass_banded.c
void glass_banded_light(mp_obj_t panel, bool on);

// modmoy_glass_dsi.c
mp_obj_t glass_dsi_call0(mp_obj_t mod, qstr verb);
uint32_t glass_dsi_ticks(void *ctx);
bool glass_kernel_lit(void);
bool glass_copy(mp_obj_t ppa, mp_obj_t dst, mp_obj_t src, mp_int_t W, mp_int_t H,
                mp_int_t *r);
mp_obj_t glass_ppa_timeouts(mp_obj_t ppa);
mp_obj_t glass_dsi_overlap_stats(mp_obj_t self_in);

// modmoy_glass_rot.c
mp_obj_t glass_rot_overlap_stats(mp_obj_t self_in);

#endif // MOY_GLASS_PRESENT

#endif
