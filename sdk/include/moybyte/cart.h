// A compiled cart in C: one source, built to a wasm module and to a native
// module per chip (sdk/README.md, "A compiled cart"; tools/wasm_cart.py and
// tools/elf_cart.py). A cart includes this one header on both targets and
// calls moy-spec's verbs by the names libmoy's moy_cart.h gives them
// (`moy_cls`, `moy_spr`, ...), with Moybyte's extension rows below:
//
//     #include "moybyte/cart.h"
//     void init(void) { }
//     void update(float dt) { }
//     void draw(void) { moy_cls(1); }
//
// Its hooks are `init`, `update(dt)` and `draw`, and `par_item(i, arg)` where
// it calls moy_par. Built for wasm (`__wasm__`) the verbs are moy-spec's "moy"
// imports and the extension rows the imports of the module of their name,
// which the manifest declares ("extensions": ["moybyte.scenes"], ...; the
// rows are native/moycore/moycore_wasm_ext.h's). Built for a chip they are
// the SDK's adapter, sdk/src/moycart.c, linked into the module: libmoy's
// raster over the game's screen (native/moy_elf/moy_os.h, A GAME), its frame
// loop calling the hooks, and the sprites, map, flags, config, scenes and
// paint images the console hands the game at its start (moy_game_data). A
// name or a buffer is a pointer and its length, never NUL-terminated; an
// answer longer than its buffer is cut, and its whole length still answered.
//
// The native module does what the wasm module does, over the game's
// channels to the console (native/moy_elf/moy_os.h, A GAME'S CHANNELS): its
// audio verbs and its stream reach the run's session and the speaker, its
// pmem, prefs and written files last as the wasm module's do, its blits show
// through a palette and as RGB565, and moy_par's items run on two cores
// where the console gives a game a second one (a P4), as a wasm run's do.
// Where it differs: an import called from one of par's items traps in wasm
// and runs in the native module, which shares the cart's state with the
// other core, and a wasm module's traps (a blit outside _draw, a second blit
// in one) are no fault natively.

#ifndef MOYBYTE_CART_H
#define MOYBYTE_CART_H

#include <stdint.h>

#ifndef __wasm__
// The native module's names for the verbs: libmoy's raster, which the module
// links too, holds the moy_ names itself (its moy_cls takes a canvas).
#define moy_cls moy_cart_cls
#define moy_background moy_cart_background
#define moy_view moy_cart_view
#define moy_pix moy_cart_pix
#define moy_line moy_cart_line
#define moy_rect moy_cart_rect
#define moy_rectb moy_cart_rectb
#define moy_circ moy_cart_circ
#define moy_circb moy_cart_circb
#define moy_oval moy_cart_oval
#define moy_ovalb moy_cart_ovalb
#define moy_print moy_cart_print
#define moy_camera moy_cart_camera
#define moy_clip moy_cart_clip
#define moy_pal moy_cart_pal
#define moy_palt moy_cart_palt
#define moy_fillp moy_cart_fillp
#define moy_make_layer moy_cart_make_layer
#define moy_draw_layer moy_cart_draw_layer
#define moy_tri moy_cart_tri
#define moy_trib moy_cart_trib
#define moy_tline moy_cart_tline
#define moy_spr moy_cart_spr
#define moy_sspr moy_cart_sspr
#define moy_sget moy_cart_sget
#define moy_sset moy_cart_sset
#define moy_fget moy_cart_fget
#define moy_fset moy_cart_fset
#define moy_map moy_cart_map
#define moy_mget moy_cart_mget
#define moy_mset moy_cart_mset
#define moy_btn moy_cart_btn
#define moy_btnp moy_cart_btnp
#define moy_players moy_cart_players
#define moy_touch moy_cart_touch
#define moy_key moy_cart_key
#define moy_keyp moy_cart_keyp
#define moy_textmode moy_cart_textmode
#define moy_sfx moy_cart_sfx
#define moy_music moy_cart_music
#define moy_music_stop moy_cart_music_stop
#define moy_sound_stop moy_cart_sound_stop
#define moy_volume moy_cart_volume
#define moy_beep moy_cart_beep
#define moy_time moy_cart_time
#define moy_pmem moy_cart_pmem
#define moy_cfg moy_cart_cfg
#define moy_rnd moy_cart_rnd
#define moy_srand moy_cart_srand
#define moy_flr moy_cart_flr
#define moy_quit moy_cart_quit
#define moy_target moy_cart_target
#define moy_blit moy_cart_blit
#define moy_blit565 moy_cart_blit565
#define moy_read moy_cart_read
#define moy_snd moy_cart_snd
#define moy_par moy_cart_par
#define moy_write moy_cart_write
#define moy_erase moy_cart_erase
#define moy_list moy_cart_list
#endif

#if defined(__GNUC__) && !defined(__wasm__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wattributes"
#ifdef __clang__
#pragma GCC diagnostic ignored "-Wunknown-attributes"
#endif
#endif
#include "moy_cart.h"
#if defined(__GNUC__) && !defined(__wasm__)
#pragma GCC diagnostic pop
#endif

#ifdef __wasm__
#define MOYBYTE_SCENES(name) __attribute__((import_module("moybyte.scenes"), import_name(#name)))
#define MOYBYTE_IMAGES(name) __attribute__((import_module("moybyte.images"), import_name(#name)))
#define MOYBYTE_MOUSE(name) __attribute__((import_module("moybyte.mouse"), import_name(#name)))
#define MOYBYTE_PREFS(name) __attribute__((import_module("moybyte.prefs"), import_name(#name)))
#else
#undef MOY_EXPORT
#define MOY_EXPORT(name)
#define MOYBYTE_SCENES(name)
#define MOYBYTE_IMAGES(name)
#define MOYBYTE_MOUSE(name)
#define MOYBYTE_PREFS(name)
#endif

#ifdef __cplusplus
extern "C" {
#endif

// The hooks, each the export of moy-spec's name for it.
MOY_EXPORT("_init") void init(void);
MOY_EXPORT("_update") void update(float dt);
MOY_EXPORT("_draw") void draw(void);
MOY_EXPORT("_par") void par_item(int32_t i, int32_t arg);

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

// moybyte.prefs
// The game's prefs, each a JSON value under a key, kept across runs
// (docs/app_api_v1.md section 5). The value of `key` as JSON text into
// `json`: its length, or -8 for a key never set.
int32_t game_prefs_get(const char *key, uint32_t key_n, char *json,
                  uint32_t cap) MOYBYTE_PREFS(get);
// `key` set to the JSON value `json`: 0, or -7 for text that is no JSON value.
int32_t game_prefs_set(const char *key, uint32_t key_n, const char *json,
                  uint32_t json_n) MOYBYTE_PREFS(set);
// `key` gone: 0, or -8 for a key never set.
int32_t game_prefs_clear(const char *key, uint32_t key_n) MOYBYTE_PREFS(clear);

#ifdef __cplusplus
}
#endif

#endif // MOYBYTE_CART_H
