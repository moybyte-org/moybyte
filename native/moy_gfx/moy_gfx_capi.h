// The C-level draw API moy_gfx exports to SIBLING native modules -- today
// that is moy_lua's libmoy-direct draw verbs (#67/#189): a Lua cart's
// pix/rect/line/circ/tri/print run as lua_CFunctions that call straight into
// these, so a draw never crosses into Python at all.
//
// The contract mirrors the Python-facing draw gates exactly: every function
// reads the SAME DrawCtx a canvas keeps in step from its cold paths
// (camera/clip/reset_state -> the state array, pal/palt -> the RGB565 table),
// and the raster underneath is the same gate_fill / libmoy kernels the MP
// verbs end in -- so a pixel drawn through this API is byte-identical to one
// drawn through the canvas method it shadows. That equivalence is pinned by
// tests/test_gfx_binding.py on the unix-port MicroPython build.
//
// Consumers include this RELATIVELY ("../moy_gfx/moy_gfx_capi.h"): the build
// stages native modules as sibling directories on every target that has
// moy_gfx (T-Deck ext_mod/, P4 .staged/, the unix test build), and the wasm
// runner -- which builds moy_lua WITHOUT moy_gfx -- simply fails the
// __has_include probe and compiles the direct path out. Definitions live in
// modmoy_gfx.c; nothing here allocates or raises.

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "py/obj.h"

// Opaque: the layout stays private to modmoy_gfx.c.
typedef struct _moy_gfx_draw_ctx_obj_t moy_gfx_draw_ctx_t;

// The DrawCtx behind a Python-side ctx object (canvas._gate_ctx), or NULL if
// `obj` is not one.
moy_gfx_draw_ctx_t *moy_gfx_capi_ctx(mp_obj_t obj);

// False until set_buf has pointed the ctx at a destination buffer.
bool moy_gfx_capi_ready(const moy_gfx_draw_ctx_t *c);

// True when the canvas's sprite queue holds quads: the caller must upcall
// canvas.flush_batch BEFORE drawing (the #63 order rule -- queued sprites
// belong under the primitive about to land). The upcall is the caller's job
// because only the caller knows how to protect it (moy_lua must not let an MP
// exception longjmp through Lua frames).
bool moy_gfx_capi_batch_pending(const moy_gfx_draw_ctx_t *c);

// #67 stage-1 (moycore): the C-side sprite-batch protocol. A canvas may
// register the cart's INDEXED sheet + palt with its ctx (DrawCtx.set_batch_src
// -- the Lua glue does, at bind time), after which a consumer that owns a
// batch token can flush the pending run without ever entering Python:
// the same array-mode walk blit_batch performs (libmoy moy_spr per quad,
// colour via the ctx's pal-resolved table, camera/clip from the state array).
//
// batch_src: a source is registered (callers gate their fast path on this).
bool moy_gfx_capi_batch_src(const moy_gfx_draw_ctx_t *c);

// flush_batch: flush the pending run in C. Returns true when the queue is
// handled (flushed, or nothing pending); false when the caller MUST fall back
// to the canvas.flush_batch upcall -- no registered source, no destination,
// or the pending run belongs to ANOTHER writer (its sheet is whatever
// canvas._batch_sheet says, which only the Python flush knows). Pure C: no
// upcalls, no allocation, never raises. The caller owns the pump feed and any
// profiling counters, exactly as with the draw verbs above.
bool moy_gfx_capi_flush_batch(moy_gfx_draw_ctx_t *c, int token);

// #67 stage-1b: the sheet-sampling verbs, against the ctx-registered sources.
// sspr needs set_batch_src (the sheet); tline needs set_map_src too. Callers
// gate their fast path on these probes and fall back to the trampoline
// otherwise. Same purity contract as the draw verbs: no upcalls, no
// allocation, never raise; the #63 order-rule flush is the caller's job
// BEFORE calling (they sample under the current state).
bool moy_gfx_capi_map_src(const moy_gfx_draw_ctx_t *c);

// The registered map cells themselves (set_map_src's buffer: tile id + 1 per
// byte, 0 = empty; mw/mh in tiles), or NULL when none is registered. For
// consumers that WALK the map -- moy_lua's flag-masked p8 map() (#66 M0)
// emits batch quads per cell; the pixels still ride blit_batch's walk.
const uint8_t *moy_gfx_capi_map_cells(const moy_gfx_draw_ctx_t *c,
                                      int *mw, int *mh);
void moy_gfx_capi_sspr(moy_gfx_draw_ctx_t *c, int sx, int sy, int sw, int sh,
                       int dx, int dy, int ddw, int ddh, int ck, int flip);
void moy_gfx_capi_tline(moy_gfx_draw_ctx_t *c, int x0, int y0, int x1, int y1,
                        int32_t u, int32_t v, int32_t du, int32_t dv, int ck);

// The canvas object the ctx was made for (the flush_batch upcall target).
mp_obj_t moy_gfx_capi_canvas(const moy_gfx_draw_ctx_t *c);

// The DRAW2 profiling flag (ST_PROF), so callers gate their own timers the
// way the gates do -- the ticks_us pair costs ~6us on the S3, real money
// against a 1x1 fill.
bool moy_gfx_capi_prof(const moy_gfx_draw_ctx_t *c);

// Pump bookkeeping (#163 door 1, T-Deck root canvas): decrement the shared
// cadence counter by `nops`; when the pump is due, returns the registered
// callable for the CALLER to invoke (protected), else MP_OBJ_NULL. Ctxs with
// no pump (every canvas but the T-Deck root) always return MP_OBJ_NULL.
mp_obj_t moy_gfx_capi_pump_due(moy_gfx_draw_ctx_t *c, int nops);

// Draw verbs. Coordinates are canvas coords (camera applied inside), `ci` a
// MOY64 palette index resolved through the ctx's pal-remapped RGB565 table.
// All clip to the ctx clip rect intersected with the buffer; all are pure C
// (no upcalls, no allocation, never raise). fill/rectb/print bump the ctx's
// ST_N_FILL / ST_N_TEXT liveness counters like the gates do.
void moy_gfx_capi_fill(moy_gfx_draw_ctx_t *c, int x, int y, int w, int h, int ci);
void moy_gfx_capi_rectb(moy_gfx_draw_ctx_t *c, int x, int y, int w, int h, int ci);
void moy_gfx_capi_line(moy_gfx_draw_ctx_t *c, int x0, int y0, int x1, int y1, int ci);
void moy_gfx_capi_circ(moy_gfx_draw_ctx_t *c, int cx, int cy, int r, int ci,
                       bool outline);
void moy_gfx_capi_tri(moy_gfx_draw_ctx_t *c, int x1, int y1, int x2, int y2,
                      int x3, int y3, int ci);
void moy_gfx_capi_print(moy_gfx_draw_ctx_t *c, const uint8_t *s, size_t slen,
                        int x, int y, int ci);
