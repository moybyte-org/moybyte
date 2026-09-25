/* libmoy's wasm binding: proposals/wasm-runtime.md's import table, over WAMR.
 *
 * TRACKS THE PROPOSAL, which is not part of core 0.3. The table is
 * proposals/wasm-imports.json; test/wasm_table_check.py holds this file's
 * NativeSymbol array equal to it, row for row and signature for signature.
 *
 * BUILT ONLY WHEN ASKED. src/moy_wasm.c compiles to nothing unless MOY_WASM is
 * defined, and it is the only file in libmoy that includes WAMR's
 * wasm_export.h, from whatever include path the caller provides -- WAMR is
 * not vendored here and nothing else in the library needs it. It also needs
 * the direct-colour build (MOY_PIXEL_RGB565): a palette blit's 256 colours do
 * not fit the 64 indices an indexed canvas holds.
 *
 * The shape is moy_lua_open's. The host owns the engine and hands the binding
 * a module instance's exec env and a moy_console; the binding owns nothing but
 * the per-run state below. Load policy -- AOT or not, where the module lives,
 * the stack size, signing, the memory floor -- stays in the port.
 *
 *   wasm_runtime_init();
 *   moy_wasm_register(storage);                // once, before any load
 *   module = wasm_runtime_load(...);
 *   if (moy_wasm_check(module, wasm, size, manifest_pages, err, sizeof err))
 *       refuse;
 *   inst = wasm_runtime_instantiate(module, stack, 0, ...);
 *   env  = wasm_runtime_create_exec_env(inst, stack);
 *   memset(&w, 0, sizeof w); w.read = my_read; w.read_user = me;
 *   moy_wasm_open(&w, &con, env);
 *   moy_wasm_init(&w, err, sizeof err);        // then, per SPEC.md 5's tick:
 *   moy_wasm_update(&w, dt, err, sizeof err);
 *   moy_wasm_draw(&w, err, sizeof err);        // non-zero: do NOT present
 *   moy_wasm_close(&w);
 */

#ifndef MOY_WASM_H_INCLUDED
#define MOY_WASM_H_INCLUDED

#include <stdint.h>
#include <stddef.h>

#include "moy.h"
#include "wasm_export.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MOY_WASM_MODULE "moy"

/* Layers a cart may hold at once. SPEC.md 1.1 guarantees one; past this,
 * make_layer answers 0 exactly as it does when the host's layer_new
 * declines. */
#ifndef MOY_WASM_LAYERS
#define MOY_WASM_LAYERS 8
#endif

/* The longest file name `read` and key `cfg` accept, in bytes. A longer one
 * reads as absent. */
#define MOY_WASM_NAME_MAX 255

typedef struct moy_wasm {
    /* -- the host's, set before moy_wasm_open ------------------------------ */

    /* The cart's own files (the proposal's `read`). `name` is NUL-terminated
     * and already checked to stay inside the cart's folder: relative,
     * '/'-separated, no empty, "." or ".." segment. Copy at most `len` bytes
     * from `offset` into `dst` and return how many were copied; with `len` 0
     * copy nothing and return how many bytes remain from `offset`. An absent
     * file answers 0. NULL: every read answers 0. */
    uint32_t (*read)(void *user, const char *name, uint32_t offset,
                     uint8_t *dst, uint32_t len);
    void *read_user;
    /* 1 when the canvas's wire words (moy_canvas_wire) are canonical RGB565
     * with the two bytes swapped. A palette blit and blit565 then encode their
     * colours the same way; 0 means canonical, moy_canvas_init's default. */
    int wire_swapped;

    /* -- the binding's own, set by moy_wasm_open ------------------------- */
    moy_console *con;
    wasm_exec_env_t env;
    wasm_module_inst_t inst;
    wasm_function_inst_t hooks[3];          /* _init, _update, _draw */
    moy_canvas *screen;                     /* con->canvas at open */
    moy_canvas *target;                     /* what the drawing verbs draw on */
    moy_canvas layers[MOY_WASM_LAYERS];     /* handle h is layers[h - 1] */
    int n_layers;
    int in_draw, blits, quitting;
} moy_wasm;

/* The import table as WAMR native symbols, and its row count: a read-only
 * template, so it costs the host no writable memory. */
const NativeSymbol *moy_wasm_natives(uint32_t *count);

/* Register the table under module "moy". Once, after wasm_runtime_init and
 * before the first wasm_runtime_load. `storage` is the host's: room for the
 * row count moy_wasm_natives reports. WAMR sorts it in place and keeps
 * pointing at it, so it stays allocated until wasm_runtime_destroy -- which
 * lets a host place it where its writable memory is cheap. Returns 0 on
 * success. */
int moy_wasm_register(NativeSymbol *storage);

/* The proposal's module shape, checked on a LOADED module before it is
 * instantiated -- so before its linear memory is allocated: every import is a
 * function from "moy" that the table resolved at the table's type; _init,
 * _update(f32) and _draw are exported at their types and a memory as
 * "memory"; the module defines exactly one memory, not shared, whose minimum
 * and maximum are both `pages` (the manifest's "memory"). Returns 0, or
 * non-zero with the first failure in `err`.
 *
 * `wasm` is the cart's main.wasm, or any prefix of it that reaches its memory
 * section: WAMR reshapes a memory the module never grows, so the declared
 * size is read from the module's bytes, which a host loading a compiled form
 * of the module still has. The imports and exports come from `module`, which
 * may be either form.
 *
 * WAMR does not report a start function, so this cannot refuse one; instead a
 * start function that reaches any import traps at instantiation, because no
 * binding exists until moy_wasm_open. `moy check` refuses it statically. */
int moy_wasm_check(wasm_module_t module, const uint8_t *wasm, size_t size,
                   uint32_t pages, char *err, size_t errlen);

/* Bind the instance behind `env` to `con`: its imports draw on con->canvas
 * (the screen) and call con->host. Looks up the three hooks. Returns 0, or
 * non-zero if the module lacks one. Leaves the host fields above untouched. */
int moy_wasm_open(moy_wasm *w, moy_console *con, wasm_exec_env_t env);

/* The three hooks. 0 on success -- quit() included, which calls
 * con->host.quit and unwinds; non-zero on a trap, with the runtime's message
 * in `err`. A trapped instance must not be called again, and a frame whose
 * moy_wasm_draw trapped must not be presented: it is partial. */
int moy_wasm_init  (moy_wasm *w, char *err, size_t errlen);
int moy_wasm_update(moy_wasm *w, float dt, char *err, size_t errlen);
int moy_wasm_draw  (moy_wasm *w, char *err, size_t errlen);

/* Release the cart's layers through con->host.layer_free and unbind the
 * instance. The instance, exec env and module stay the host's to destroy.
 * Safe on a zeroed moy_wasm that was never opened and after a failed open. */
void moy_wasm_close(moy_wasm *w);

#ifdef __cplusplus
}
#endif
#endif /* MOY_WASM_H_INCLUDED */
