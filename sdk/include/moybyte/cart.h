// A compiled cart's Moybyte verbs (docs/moy_cart_api.md, "A compiled cart's
// Moybyte verbs"): the scenes, paint images and mouse a Python and a Lua cart
// hold by name, imported from the module of each extension's name, which the
// cart's manifest declares ("extensions": ["moybyte.scenes"], ...). The rows
// are native/moycore/moycore_wasm_ext.h's. A name or a buffer is a pointer
// and its length, never NUL-terminated; an answer longer than its buffer is
// cut, and its whole length still answered.

#ifndef MOYBYTE_CART_H
#define MOYBYTE_CART_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MOYBYTE_SCENES(name) __attribute__((import_module("moybyte.scenes"), import_name(#name)))
#define MOYBYTE_IMAGES(name) __attribute__((import_module("moybyte.images"), import_name(#name)))
#define MOYBYTE_MOUSE(name) __attribute__((import_module("moybyte.mouse"), import_name(#name)))

// One placed actor, as scene() writes it: Python's row fields, its tag cut at
// 15 bytes and NUL-padded.
typedef struct {
    int32_t tile, x, y, flip;
    char tag[16];
} moy_actor_t;

// moybyte.scenes
// Scene `name`'s actors (name_n 0: the active scene) into `out`, as many as
// `cap` bytes hold: the scene's actor count, or -1 for no such scene.
int32_t scene(const char *name, uint32_t name_n, moy_actor_t *out,
              uint32_t cap) MOYBYTE_SCENES(scene);
// Make `name` the active scene: its actor count, or -1 for no such scene
// (the active one unchanged). The active scene is the first one at each run.
int32_t load_scene(const char *name, uint32_t name_n) MOYBYTE_SCENES(load_scene);
// The i-th scene's name (0 the first, the default active one): its length,
// or -1 past the last.
int32_t scene_name(int32_t i, char *dst, uint32_t cap) MOYBYTE_SCENES(scene_name);
// Actor i's "flags" object of scene `name` (name_n 0: the active one) as its
// JSON text: its length, 0 for none, -1 for no such actor.
int32_t scene_flags(const char *name, uint32_t name_n, int32_t i, char *dst,
                    uint32_t cap) MOYBYTE_SCENES(scene_flags);

// moybyte.images
// Paint image `name` (images/<name>.moyimg): its handle, 0 for none.
int32_t image(const char *name, uint32_t name_n) MOYBYTE_IMAGES(image);
// Its width << 16 | its height, 0 for no such handle.
int32_t image_size(int32_t h) MOYBYTE_IMAGES(image_size);
// It placed at x, y on the draw target (the screen, or the layer `target`
// chose), under the target's camera, clip, pal and palt.
void image_spr(int32_t h, int32_t x, int32_t y) MOYBYTE_IMAGES(image_spr);

// moybyte.mouse
// x, y, left, middle, right, scroll_x, scroll_y into `out` (as many as `cap`
// bytes hold), as Python's mouse() answers them: `left` the press edge,
// middle, right and the scrolls 0. 1 while a pointer is down, else 0.
int32_t mouse(int32_t *out, uint32_t cap) MOYBYTE_MOUSE(mouse);

#ifdef __cplusplus
}
#endif

#endif // MOYBYTE_CART_H
