// Moybyte's own extensions for a compiled cart (moy-spec SPEC.md 16.2 and
// 10): the import modules through which a "runtime": "wasm" cart reaches the
// superset verbs a Python and a Lua cart hold by name, each module named for
// the extension the cart's manifest declares to import from it. The rows are
// Moybyte's (docs/moy_cart_api.md, "A compiled cart's Moybyte verbs"), typed
// by libmoy's check as the "moy" table's are; sdk/include/moybyte/cart.h
// declares them for C.
//
//   moybyte.scenes
//     scene(name, name_n, out, cap) -> i32     scene `name`'s actors (name_n 0:
//                                              the active one), as many
//                                              MOYCORE_ACTOR-byte records as
//                                              fit at `out`; the actor count,
//                                              -1 for no such scene
//     load_scene(name, name_n) -> i32          make it the active scene: its
//                                              actor count, -1 for no such
//                                              scene (the active one unchanged)
//     scene_name(i, dst, cap) -> i32           the i-th scene's name (0 the
//                                              default): its length, -1 past
//                                              the last
//     scene_flags(name, name_n, i, dst, cap)   the i-th actor's "flags" object
//                                     -> i32   as JSON text: its length, 0 for
//                                              none, -1 for no such actor
//   moybyte.images
//     image(name, name_n) -> i32               the paint image's handle, 0 for
//                                              none (Python's None)
//     image_size(h) -> i32                     its width << 16 | its height, 0
//                                              for no such handle
//     image_spr(h, x, y)                       it placed at x, y on the draw
//                                              target (the screen, or the layer
//                                              `target` chose), under its
//                                              camera, clip, pal and palt; an
//                                              index past the palette is not
//                                              drawn
//   moybyte.mouse
//     mouse(out, cap) -> i32                   x, y, left, middle, right,
//                                              scroll_x, scroll_y as seven
//                                              int32 at `out` (as many as fit),
//                                              as Python's mouse() answers
//                                              them; 1 while a pointer is
//                                              down, else 0
//
// A name and a buffer are a pointer and its length ('*~'), never
// NUL-terminated; an answer longer than its buffer is cut, its whole length
// still answered. The scenes and paint images are the run's own tables
// (moycore_lua.h), handed over before the load as a Lua run's are; the
// pointer is the run's snapshot (moycore_superset.h's mouse). A call from
// one of par's items traps, as a "moy" import does.
//
// Included by moycore_wasm.c (the boards and the browser) and by
// runtime/host/moyhost_wasm.c (the host): one body, as libmoy's table is.

#ifndef MOYCORE_WASM_EXT_H
#define MOYCORE_WASM_EXT_H

#include <stdint.h>
#include <string.h>

#include "moy.h"
#if __has_include("libmoy/moy_wasm.h")
#include "libmoy/moy_wasm.h"   // the boards' and the browser's staging
#else
#include "moy_wasm.h"          // the host's include path
#endif
#include "moycore_lua.h"
#include "moycore_run.h"

// The extensions by bit, in the order Python lists them
// (runtime/run/lua_ext.py's WASM_EXTS).
#define MOYCORE_EXT_SCENES 1u
#define MOYCORE_EXT_IMAGES 2u
#define MOYCORE_EXT_MOUSE  4u
#define MOYCORE_EXT_COUNT  3u

static const char MX_ITEM[] = "moy: an import called from a par item";

// The binding behind a call, or NULL with the cart trapped: a call from one
// of par's items, or before the cart was bound.
static moy_wasm *mx_bound(void *e)
{
#ifdef MOY_WASM_JS
    moy_wasm *w = (moy_wasm *)e;
    if (w != NULL && w->items) {
        if (!w->trap) w->trap = MX_ITEM;
        return NULL;
    }
    return w;
#else
    wasm_module_inst_t inst = e ? wasm_runtime_get_module_inst((wasm_exec_env_t)e) : NULL;
    moy_wasm *w = inst ? (moy_wasm *)wasm_runtime_get_custom_data(inst) : NULL;
    if (inst == NULL) return NULL;
    if (w == NULL) {
        wasm_runtime_set_exception(inst, "moy: an import ran before the cart was bound");
        return NULL;
    }
    if (w->items) {
        wasm_runtime_set_exception(inst, MX_ITEM);
        return NULL;
    }
    return w;
#endif
}

// -- moybyte.scenes -----------------------------------------------------------

static int32_t mx_scene(void *e, const uint8_t *name, uint32_t n, uint8_t *out, uint32_t cap)
{
    if (!mx_bound(e)) return -1;
    return moycore_scene_rows(n ? (const char *)name : NULL, n, out, cap);
}

static int32_t mx_load_scene(void *e, const uint8_t *name, uint32_t n)
{
    if (!mx_bound(e)) return -1;
    return moycore_scene_load((const char *)name, n);
}

static int32_t mx_scene_name(void *e, int32_t i, uint8_t *dst, uint32_t cap)
{
    if (!mx_bound(e)) return -1;
    return moycore_scene_name(i, dst, cap);
}

static int32_t mx_scene_flags(void *e, const uint8_t *name, uint32_t n, int32_t i,
                              uint8_t *dst, uint32_t cap)
{
    if (!mx_bound(e)) return -1;
    return moycore_scene_flags(n ? (const char *)name : NULL, n, i, dst, cap);
}

// -- moybyte.images -----------------------------------------------------------

static int32_t mx_image(void *e, const uint8_t *name, uint32_t n)
{
    if (!mx_bound(e)) return 0;
    int32_t h = moycore_image_find((const char *)name, n);
    return h > 0 ? h : 0;
}

static int32_t mx_image_size(void *e, int32_t h)
{
    uint32_t w = 0, ht = 0;
    if (!mx_bound(e) || moycore_image_pix(h, &w, &ht) == NULL) return 0;
    return (int32_t)((w & 0xFFFFu) << 16 | (ht & 0xFFFFu));
}

// The canvas Image path (moycore_scene.h's mcs_blit at scale 1): an index
// past the palette and a palt-transparent one skipped, the rest through the
// target's draw-time store, camera and clip applied.
static void mx_image_spr(void *e, int32_t h, int32_t x, int32_t y)
{
    moy_wasm *w = mx_bound(e);
    uint32_t iw = 0, ih = 0;
    const uint8_t *pix = w ? moycore_image_pix(h, &iw, &ih) : NULL;
    if (pix == NULL) return;
#ifndef MOY_WASM_JS
    // A frame the screen lacks is written first (a JavaScript engine's
    // frame never stays in the cart's memory).
    if (w->target == w->screen && (w->owed || w->kept)) {
        if (!w->owed) {
            w->owed = w->kept;
            w->kept = NULL;
        }
        moy_wasm_settle(w);
    }
#endif
    moy_canvas *c = w->target;
    int64_t x0 = (int64_t)x - c->cam_x, y0 = (int64_t)y - c->cam_y;
    for (uint32_t sy = 0; sy < ih; sy++) {
        int64_t ty = y0 + sy;
        if (ty < c->clip_y0 || ty >= c->clip_y1) continue;
        const uint8_t *src = pix + (size_t)sy * iw;
        moy_pixel *row = c->pix + (size_t)ty * (size_t)c->w;
        for (uint32_t sx = 0; sx < iw; sx++) {
            int64_t tx = x0 + sx;
            uint8_t p = src[sx];
            if (tx < c->clip_x0 || tx >= c->clip_x1 || p >= MOY_PALETTE || c->palt[p]) continue;
            row[tx] = c->store[p];
        }
    }
}

// -- moybyte.mouse ------------------------------------------------------------

static int32_t mx_mouse(void *e, uint8_t *out, uint32_t cap)
{
    if (!mx_bound(e)) return 0;
    const moycore_run_t *c = moycore_run_cur;
    int st = (c && c->snap) ? c->snap[SNAP_TOUCH_DOWN] : 0;
    int32_t v[7] = {
        st ? c->snap[SNAP_TOUCH_X] : 0, st ? c->snap[SNAP_TOUCH_Y] : 0,
        (st & 4) != 0, 0, 0, 0, 0,
    };
    for (uint32_t i = 0; i < 7 && (i + 1) * 4 <= cap; i++) {
        uint32_t u = (uint32_t)v[i];
        out[i * 4] = (uint8_t)u;
        out[i * 4 + 1] = (uint8_t)(u >> 8);
        out[i * 4 + 2] = (uint8_t)(u >> 16);
        out[i * 4 + 3] = (uint8_t)(u >> 24);
    }
    return st != 0;
}

// -- the tables ---------------------------------------------------------------

// Each in strcmp order of its names, which WAMR's sorted registration takes
// as it stands (moy_wasm_register_rows).
static const NativeSymbol MX_SCENES[] = {
    { "load_scene", (void *)mx_load_scene, "(*~)i", NULL },
    { "scene", (void *)mx_scene, "(*~*~)i", NULL },
    { "scene_flags", (void *)mx_scene_flags, "(*~i*~)i", NULL },
    { "scene_name", (void *)mx_scene_name, "(i*~)i", NULL },
};

static const NativeSymbol MX_IMAGES[] = {
    { "image", (void *)mx_image, "(*~)i", NULL },
    { "image_size", (void *)mx_image_size, "(i)i", NULL },
    { "image_spr", (void *)mx_image_spr, "(iii)", NULL },
};

static const NativeSymbol MX_MOUSE[] = {
    { "mouse", (void *)mx_mouse, "(*~)i", NULL },
};

#define MX_ROWS(t) (uint32_t)(sizeof(t) / sizeof((t)[0]))

// The extensions, bit i the i-th (moycore_run.h's MOYCORE_EXT_NAMES).
static const char *const MX_NAMES[MOYCORE_EXT_COUNT] = { MOYCORE_EXT_NAMES };
static const moy_wasm_ext MOYCORE_EXTS[MOYCORE_EXT_COUNT] = {
    { MX_NAMES[0], MX_SCENES, MX_ROWS(MX_SCENES) },
    { MX_NAMES[1], MX_IMAGES, MX_ROWS(MX_IMAGES) },
    { MX_NAMES[2], MX_MOUSE, MX_ROWS(MX_MOUSE) },
};

// The extensions of `mask` (the ones the cart declares) into `out`, which
// holds MOYCORE_EXT_COUNT: how many.
static inline uint32_t moycore_wasm_exts(uint32_t mask, moy_wasm_ext *out)
{
    uint32_t n = 0;
    for (uint32_t i = 0; i < MOYCORE_EXT_COUNT; i++)
        if (mask & (1u << i)) out[n++] = MOYCORE_EXTS[i];
    return n;
}

#endif // MOYCORE_WASM_EXT_H
