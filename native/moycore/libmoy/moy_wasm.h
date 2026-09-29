/* libmoy's wasm binding: proposals/wasm-runtime.md's import table.
 *
 * TRACKS THE PROPOSAL, which is not part of core 0.3. The table is
 * proposals/wasm-imports.json; test/wasm_table_check.py holds this file's
 * NativeSymbol array equal to it, row for row and signature for signature.
 *
 * BUILT ONLY WHEN ASKED, over one of two engines. src/moy_wasm.c compiles to
 * nothing unless one of these is defined:
 *
 *   MOY_WASM     over WAMR. It is the only file in libmoy that includes
 *                WAMR's wasm_export.h, from whatever include path the caller
 *                provides -- WAMR is not vendored here and nothing else in
 *                the library needs it.
 *   MOY_WASM_JS  over a JavaScript embedder's own engine: the cart is a
 *                sibling WebAssembly module whose "moy" imports are adapters
 *                over this table's functions (port/wasm/page/cart.js). The
 *                embedder supplies the two functions at the end of this file
 *                that reach the cart's memory, which C cannot address.
 *
 * Either way the verbs are the same C functions, and so are the rules they
 * enforce. It also needs the direct-colour build (MOY_PIXEL_RGB565): a palette
 * blit's 256 colours do not fit the 64 indices an indexed canvas holds.
 *
 * The shape is moy_lua_open's. The host owns the engine and hands the binding
 * a moy_console; the binding owns nothing but the per-run state below. Load
 * policy -- AOT or not, where the module lives, the stack size, signing, the
 * memory a cart may have -- stays in the port. Over WAMR:
 *
 *   wasm_runtime_init();
 *   moy_wasm_register(storage);                // once, before any load
 *   module = wasm_runtime_load(...);
 *   if (moy_wasm_check(module, wasm, size, manifest_pages, err, sizeof err))
 *       refuse;
 *   inst = wasm_runtime_instantiate(module, stack, 0, ...);
 *   env  = wasm_runtime_create_exec_env(inst, stack);
 *   memset(&w, 0, sizeof w); w.read = my_read; w.read_user = me;
 *   w.frame = my_take;                         // optional: see `frame` below
 *   w.snd = my_queue;                          // optional: see `snd` below
 *   w.lanes = n; w.lane_go = ...;              // optional: see `lanes` below
 *   moy_wasm_open(&w, &con, env);
 *   moy_wasm_init(&w, err, sizeof err);        // then, per SPEC.md 5's tick:
 *   moy_wasm_update(&w, dt, err, sizeof err);
 *   moy_wasm_draw(&w, err, sizeof err);        // non-zero: do NOT present
 *   px = moy_wasm_frame(&w, &lut);             // a taken frame: show it, then
 *   moy_wasm_presented(&w, my_copy);           //   or moy_wasm_settle(&w)
 *   moy_wasm_close(&w);
 *
 * Over a JavaScript engine, where the embedder calls the cart's exports:
 *
 *   if (moy_wasm_check_bytes(wasm, size, manifest_pages, err, sizeof err))
 *       refuse;
 *   memset(&w, 0, sizeof w); w.read = my_read; w.read_user = me;
 *   moy_wasm_bind(&w, &con);
 *   ...the embedder instantiates the module; then, around each hook:
 *   moy_wasm_begin(&w, MOY_WASM_DRAW);         // then the export, then
 *   moy_wasm_end(&w, threw);                   // non-zero: do NOT present
 *   moy_wasm_close(&w);
 */

#ifndef MOY_WASM_H_INCLUDED
#define MOY_WASM_H_INCLUDED

#include <stdint.h>
#include <stddef.h>

#include "moy.h"

#ifdef MOY_WASM_JS
/* WAMR's NativeSymbol, field for field: the table is the same data under
 * either engine, and a JavaScript embedder reads it out of this memory. */
typedef struct NativeSymbol {
    const char *symbol;
    void *func_ptr;
    const char *signature;
    void *attachment;
} NativeSymbol;
#else
#include "wasm_export.h"
#endif

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

/* The sample stream (the proposal's `snd`): signed 16-bit mono frames at this
 * rate, and the most a host holds that its output has not yet taken. */
#define MOY_WASM_SND_RATE  22050
#define MOY_WASM_SND_DEPTH 2048

/* The three hooks, in the order moy_wasm_begin numbers them. */
#define MOY_WASM_INIT   0
#define MOY_WASM_UPDATE 1
#define MOY_WASM_DRAW   2

/* `par`'s items: the export each one is, the global that moves the cart's C
 * stack, and the most cores beside the calling one a host may run them on. */
#define MOY_WASM_ITEM    "_par"
#define MOY_WASM_SP      "__stack_pointer"
#ifndef MOY_WASM_LANES
#define MOY_WASM_LANES 3
#endif

#ifndef MOY_WASM_JS
/* One lane's own instance of the cart over the same memory, made on the
 * lane's thread the first time it runs items, and what it last trapped on. */
typedef struct moy_wasm_lane {
    struct moy_wasm *w;
    int k;                                  /* its number; 0 the calling core */
    int dead;                               /* could not run items: never again */
    wasm_module_inst_t inst;
    wasm_exec_env_t env;
    wasm_function_inst_t item;
    uint8_t *sp;
    volatile int32_t trapped;               /* the item it trapped on, or -1 */
    char trap[128];
} moy_wasm_lane;
#endif

typedef struct moy_wasm {
    /* -- the host's, set before the binding is opened ---------------------- */

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
    /* Frame hand-off, under WAMR only (a JavaScript engine's frame is a copy
     * that dies with the import call). NULL: every blit writes the screen.
     * Otherwise blit and blit565 offer their frame here, where it sits in the
     * cart's memory -- W x H index bytes for blit, whose 256 colours `lut`
     * holds in the screen's wire form, or W x H little-endian RGB565 words for
     * blit565, with `lut` NULL -- and a non-zero return TAKES it: the screen
     * is not written, and the frame is OWED until the host shows it
     * (moy_wasm_presented) or has it written (moy_wasm_settle). The binding
     * settles an owed frame itself before a verb draws on or reads the screen
     * and before the cart's next hook runs, so a host that takes a frame and
     * never shows it loses nothing. A taken frame is read after the call,
     * which is why the proposal has the cart leave it as blitted until _draw
     * returns. */
    int (*frame)(void *user, const uint8_t *pixels, const moy_pixel *lut);
    void *frame_user;
    /* The cart's sample stream (the proposal's `snd`). Queue up to `n`
     * frames -- `pcm` holds them as little-endian signed 16-bit mono at
     * MOY_WASM_SND_RATE, at any alignment -- and return how many were queued;
     * with `n` 0, queue nothing and return how many would be. The host holds
     * at most MOY_WASM_SND_DEPTH frames its output has not yet taken
     * (moy_audio.h's moy_stream is such a queue). NULL is a host with no
     * audio: the binding keeps the count itself, draining it at the rate by
     * con->host.time_ms, and drops every frame. */
    uint32_t (*snd)(void *user, const uint8_t *pcm, uint32_t n);
    void *snd_user;
#ifndef MOY_WASM_JS
    /* Cores for `par`'s items besides the calling one, under WAMR only. With
     * `lanes` 0 every item runs on the calling core, in order. Otherwise the
     * calling core and lanes 1..lanes (at most MOY_WASM_LANES) each take the
     * next untaken item until none is left: lane_go starts work(job) on lane
     * k's thread and returns at once, non-zero when it could not; lane_wait(k)
     * returns once that work has returned -- or at once, taking the work
     * back, when the lane has not started it, since by then every item is
     * taken. Every call for one lane must run on the same thread, one WAMR
     * can run on: the first makes the lane's own instance of the cart there
     * (wasm_runtime_instantiate_sibling). The binding frees those instances
     * in moy_wasm_close, so a lane's thread outlives the binding
     * (port/moy_lanes.c is such lanes over POSIX threads). */
    int lanes;
    int (*lane_go)(void *user, int lane, void (*work)(void *job), void *job);
    void (*lane_wait)(void *user, int lane);
    void *lane_user;
    /* The wasm stack of each lane's exec env, as wasm_runtime_create_exec_env
     * takes it; 0 is 64 KiB. */
    uint32_t lane_stack;
#endif

    /* -- the binding's own, set by moy_wasm_open / moy_wasm_bind ---------- */
    moy_console *con;
#ifdef MOY_WASM_JS
    const char *trap;                       /* the first trap of this call */
    char item_trap[128];                    /* an item's, when it was not ours */
#else
    wasm_exec_env_t env;
    wasm_module_inst_t inst;
    wasm_function_inst_t hooks[3];          /* _init, _update, _draw */
#endif
    moy_canvas *screen;                     /* con->canvas at open */
    moy_canvas *target;                     /* what the drawing verbs draw on */
    moy_canvas layers[MOY_WASM_LAYERS];     /* handle h is layers[h - 1] */
    int n_layers;
    int in_draw, blits, quitting;
    int items;                              /* par's items run: imports trap */
#ifndef MOY_WASM_JS
    wasm_function_inst_t item;              /* the cart's _par, or NULL */
    moy_wasm_lane lane[MOY_WASM_LANES + 1]; /* [0] is the calling core */
    int32_t job_n, job_arg;                 /* the items running now */
    uint32_t job_stacks, job_size;
    int job_lanes;                          /* the lanes beside [0] they run on */
    int32_t job_next;                       /* the next item to take */
#endif
    /* The last blit's frame while the screen does not hold it: in the cart's
     * memory until the host shows it (`owed`), then in the host's copy
     * (`kept`), or nowhere once it is on the screen. */
    const uint8_t *owed;
    const uint8_t *kept;
    int frame_565;                          /* blit565's words, not blit's indices */
    moy_pixel frame_lut[256];               /* a palette frame's colours, wire form */
    /* The stream with no `snd` host: frames queued and not yet drained, and
     * the clock reading (ms, and the part of a frame left over, in
     * thousandths) they were last drained to. */
    uint32_t snd_level, snd_ms, snd_rem;
    int snd_clocked;
} moy_wasm;

/* The import table, and its row count: a read-only template, so it costs the
 * host no writable memory. Each row's func_ptr is the verb, its first
 * argument the engine's handle on the call -- a WAMR exec env, or under
 * MOY_WASM_JS the moy_wasm itself -- and its signature WAMR's string for the
 * rest: 'i' an i32, 'f' an f32, '*~' a pointer into the cart's memory and the
 * length it covers, already translated to one the host can address. */
const NativeSymbol *moy_wasm_natives(uint32_t *count);

/* The proposal's module shape, from the module's bytes alone, so any engine
 * can refuse a module before it is instantiated: every import is a function
 * from "moy" named in the table at the row's type; _init, _update(f32) and
 * _draw are exported at their types and a memory as "memory"; the module
 * defines exactly one memory, not shared, whose minimum and maximum are both
 * `pages` (the manifest's "memory"); and it has no start function. `wasm` is
 * the whole of main.wasm. Returns 0, or non-zero with the first failure in
 * `err`. */
int moy_wasm_check_bytes(const uint8_t *wasm, size_t size, uint32_t pages,
                         char *err, size_t errlen);

/* Release the cart's layers through con->host.layer_free and unbind the
 * instance. The instance, and under WAMR its exec env and module, stay the
 * host's to destroy. Safe on a zeroed moy_wasm that was never opened and
 * after a failed open. */
void moy_wasm_close(moy_wasm *w);

#ifndef MOY_WASM_JS

/* Register the table under module "moy". Once, after wasm_runtime_init and
 * before the first wasm_runtime_load. `storage` is the host's: room for the
 * row count moy_wasm_natives reports. WAMR sorts it in place and keeps
 * pointing at it, so it stays allocated until wasm_runtime_destroy -- which
 * lets a host place it where its writable memory is cheap. Returns 0 on
 * success. */
int moy_wasm_register(NativeSymbol *storage);

/* The module shape moy_wasm_check_bytes checks, on a LOADED module before it
 * is instantiated -- so before its linear memory is allocated.
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

/* The frame the host took (see `frame` above) while it is owed: its pixels,
 * or NULL when nothing is owed, and in `*lut` a palette frame's colours
 * (NULL for a blit565 frame). */
const uint8_t *moy_wasm_frame(const moy_wasm *w, const moy_pixel **lut);

/* Write the owed frame into the screen now, exactly as the blit would have;
 * nothing when nothing is owed. */
void moy_wasm_settle(moy_wasm *w);

/* The host has shown the owed frame. `kept` is a copy the host keeps, byte
 * for byte what the cart blitted and alive until the cart's next blit: a verb
 * that draws on the screen before then first has the screen written from it.
 * NULL says the host wrote the frame into the screen itself. */
void moy_wasm_presented(moy_wasm *w, const uint8_t *kept);

#else /* MOY_WASM_JS */

/* Bind to `con`: the imports draw on con->canvas (the screen) and call
 * con->host. Leaves the host fields above untouched. */
void moy_wasm_bind(moy_wasm *w, moy_console *con);

/* Around each call the embedder makes to one of the cart's hooks. begin sets
 * up what the hook runs under (the screen as the target; for _draw the
 * background repaint and the one-blit allowance). end takes whether the
 * call threw, and returns 0 when the hook returned or the cart quit -- quit()
 * unwinds by throwing -- and non-zero on a trap. A trapped instance must not
 * be called again, and a frame whose _draw trapped must not be presented. */
void moy_wasm_begin(moy_wasm *w, int hook);
int  moy_wasm_end(moy_wasm *w, int threw);

/* The binding's own trap, raised during the import call that just returned,
 * or NULL. The adapter throws when this is set, which unwinds the cart exactly
 * as a trap does, and moy_wasm_end clears it. */
const char *moy_wasm_trapped(const moy_wasm *w);

/* The message of a trap moy_wasm_js_item caught, for an item that trapped
 * outside the binding: an out-of-bounds access, an unreachable. */
void moy_wasm_item_trap(moy_wasm *w, const char *msg);

/* -- the embedder's: the cart's own memory, and a par item on it ------- */

/* `n` bytes of the cart's linear memory at `offset`, copied where C can
 * address them and kept until the import call returns; NULL when the range
 * leaves the memory. */
uint8_t *moy_wasm_js_span(moy_wasm *w, uint32_t offset, uint32_t n);

/* Copy `n` bytes into the cart's linear memory at `offset`. 0 when the range
 * leaves the memory, and then nothing is written. */
int moy_wasm_js_store(moy_wasm *w, uint32_t offset, const uint8_t *src,
                      uint32_t n);

/* Run `par`'s item `i` on the cart: its _par(i, arg) with its exported
 * __stack_pointer at `sp`, then the stack pointer as it was. 0 when the item
 * returned; non-zero when it threw, with the trap recorded through
 * moy_wasm_item_trap unless the binding recorded its own. */
int moy_wasm_js_item(moy_wasm *w, int32_t i, int32_t arg, uint32_t sp);

#endif /* MOY_WASM_JS */

#ifdef __cplusplus
}
#endif
#endif /* MOY_WASM_H_INCLUDED */
