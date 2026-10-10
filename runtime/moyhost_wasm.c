/* clock_gettime/CLOCK_MONOTONIC are POSIX, and this builds at -std=c99,
 * which hides them. Must precede every include. */
#define _POSIX_C_SOURCE 200809L

/* The host's WASM shim (docs/wasm_tier_plan_2026-09.md, phase 3), built into
 * the host's Lua library (runtime/lua_binding.py) wherever WAMR builds.
 *
 * A "runtime": "wasm" cart on the host runs through the SAME C the boards run:
 * libmoy's wasm binding (native/moycore/libmoy/moy_wasm.c, the import table as
 * WAMR native symbols) over moycore_RUN's console (native/moycore/
 * moycore_run.c), whose frames are the kernel's Player's (moy_play.c's wasm
 * row) as a Lua run's are, and WAMR itself, built for Linux from the fork the
 * boards vendor, at the commit native/moy_wasm/wamr_pin.h names
 * (runtime/wasm_binding.py fetches and builds it). The cart's written files
 * are native/moy_store/moy_files.c's, as on a board. There is no second engine:
 * a host and a device that disagree about what a verb does is the disease the
 * Lua shim was written to end, and this is its twin.
 *
 * How the host EXECUTES the module is host policy (the plan's decision): the
 * boards run a per-chip AOT module under a provenance key; the host runs the
 * cart's own main.wasm on WAMR's interpreter, which needs no compiler and no
 * key. Everything a cart can observe -- the verbs, the blit, the read, the
 * traps -- is the binding's, and the binding is the same file.
 *
 * The cart's `snd` queues into the run's moy_stream (libmoy's, as the boards'
 * speaker mixer uses), and the host's audio backend adds it into each block it
 * renders (hw_snd_mix), so the stream drains at the host output's pace.
 *
 * One run at a time, on whatever thread calls in: WAMR on Linux has no
 * pthread requirement, and the runtime is initialised once per process.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "moy.h"
#include "moy_audio.h"
#include "moy_wasm.h"
#include "moy_wasm_footprint.h"
#include "moycore_run.h"
#include "moycore_lua.h"
#include "moy_files.h"

#ifndef MOY_PIXEL_RGB565
#error "the host console is the RGB565 build -- compile with -DMOY_PIXEL_RGB565=1"
#endif

#ifndef MOY_WASM
#error "moyhost_wasm.c is the wasm build -- compile with -DMOY_WASM=1"
#endif

/* The exec env's stack: the interpreter's operand and call frames. */
#define HW_STACK (64 * 1024)
#define HW_PATH_MAX 1024

typedef struct {
    char        dir[HW_PATH_MAX];   /* the cart's folder: `read`'s only root */
    uint8_t    *bytes;       /* the module file, held while it is loaded */
    wasm_module_t module;
    wasm_module_inst_t inst;
    wasm_exec_env_t env;
    moy_wasm    w;
    int         bound;       /* moy_wasm_open succeeded */
    int         dead;        /* trapped: never called again */
    moy_stream  pcm;         /* the cart's `snd` */
    int16_t     pcm_ring[MOY_WASM_SND_DEPTH];
    moy_files_t *files;      /* the cart's written files (moy_files.h) */
    char       *writable;    /* the manifest's "writable", libmoy's form */
} host_wasm;

static int g_runtime;        /* 1 once WAMR is up and the table registered */

/* The shared run (moyhost_lua.c): claim it, and the frame hook the Player's
 * wasm row reaches through moycore_frame. */
void hl_claim(void (*release)(void));
extern int (*hl_wasm_tick)(float dt, int draw, char *err, size_t n);
extern void (*hl_wasm_stuck)(void);
static host_wasm *G_LIVE_W;
int hw_tick(host_wasm *r, float dt, int draw, char *err, int errlen);

/* The live run's console goes; the run object stays its owner's to free. */
static void hw_release_live(void)
{
    moycore_run_close(&moycore_RUN.c);
    memset(&moycore_RUN, 0, sizeof(moycore_RUN));
    moycore_run_cur = NULL;
    hl_wasm_tick = NULL;
    hl_wasm_stuck = NULL;
    G_LIVE_W = NULL;
}

static int hw_tick_live(float dt, int draw, char *err, size_t n)
{
    if (!G_LIVE_W) return 0;
    return hw_tick(G_LIVE_W, dt, draw, err, (int)n) != 0 ? -1 : 0;
}

/* The runaway watch, from its thread: the instance raises "terminated by
 * user" when its next import call returns. */
static void hw_stuck_live(void)
{
    if (G_LIVE_W && G_LIVE_W->inst) wasm_runtime_terminate(G_LIVE_W->inst);
}
/* The table's registration storage: WAMR sorts it in place and points at it
 * until the runtime is destroyed, which on the host is never. */
static NativeSymbol *g_natives;

/* The cart's own files, and nothing else (moy-spec SPEC.md 16.6's `read`). The name
 * arrives checked by the binding -- relative, no empty, "." or ".." segment --
 * so joining it to the folder cannot leave it. Anything but a regular file
 * reads as a missing one: stdio opens a folder on Linux, and its size query
 * answers garbage, where a board's VFS refuses to open it at all. */
static uint32_t hw_read(void *user, const char *name, uint32_t offset,
                        uint8_t *dst, uint32_t len)
{
    host_wasm *r = (host_wasm *)user;
    char path[HW_PATH_MAX + 260];
    struct stat st;
    FILE *f;
    long size;
    uint32_t got = 0;
    snprintf(path, sizeof path, "%s/%s", r->dir, name);
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode)) return 0;
    f = fopen(path, "rb");
    if (!f) return 0;
    if (fseek(f, 0, SEEK_END) == 0 && (size = ftell(f)) >= 0
        && (uint32_t)size > offset) {
        uint32_t left = (uint32_t)size - offset;
        if (len == 0) {
            got = left;
        } else if (fseek(f, (long)offset, SEEK_SET) == 0) {
            got = (uint32_t)fread(dst, 1, len < left ? len : left, f);
        }
    }
    fclose(f);
    return got;
}

/* The cart's samples, queued for the host's audio backend. */
static uint32_t hw_snd(void *user, const uint8_t *pcm, uint32_t n)
{
    host_wasm *r = (host_wasm *)user;
    return n ? moy_stream_write(&r->pcm, pcm, n) : moy_stream_room(&r->pcm);
}

/* A layer's pixels: the run's own, released by moy_wasm_close. */
static moy_pixel *hw_layer_new(void *u, int w, int h)
{
    (void)u;
    return (moy_pixel *)calloc((size_t)w * (size_t)h, sizeof(moy_pixel));
}

static void hw_layer_free(void *u, moy_pixel *p)
{
    (void)u;
    free(p);
}

static void put_err(char *err, int errlen, const char *what, const char *detail)
{
    if (err && errlen > 0)
        snprintf(err, (size_t)errlen, "%s%s%s", what, detail ? ": " : "",
                 detail ? detail : "");
}

/* What a failure for want of memory says first, as the boards' engine says
 * it (native/moy_wasm/modmoy_wasm.c): the Player reads it as the notice a
 * cart too big for the console gets. `what` otherwise. */
static const char *or_oom(const char *msg, const char *what)
{
    return strstr(msg, "allocate") ? "out of memory" : what;
}

/* A load's footprint by the boards' own sizing (moy_wasm_footprint.h): the
 * host twin refuses a cart over its configured limit by the same arithmetic
 * a board refuses one over its free PSRAM. */
void hw_footprint(uint64_t memory, uint64_t module_len, uint64_t *total,
                  uint64_t *block)
{
    moy_wasm_footprint(memory, module_len, total, block);
}

/* interp_footprint's twin: the host always interprets a cart's own main.wasm
 * (there is no AOT tier here), so this is the one WasmHostRuntime sizes by. */
void hw_interp_footprint(uint64_t memory, uint64_t module_len, uint64_t *total,
                         uint64_t *block)
{
    moy_wasm_interp_footprint(memory, module_len, total, block);
}

/* The app ABI's import adapter (native/moy_app/moy_app_wasm.h), which the
 * host's spine library carries: its rows for module "moybyte.app", the run's
 * grant and the load's grant check, handed over by runtime/wasm_host.py.
 * Registered with WAMR once, beside the "moy" table. */
static const NativeSymbol *g_app_rows;
static uint32_t g_app_n;
static NativeSymbol *g_app_store;
static uint32_t (*g_app_grant)(void);
static int (*g_app_admit)(const uint8_t *head, size_t n, char *err, size_t errlen);

static int app_register(void)
{
    if (!g_app_rows || g_app_store) return 1;
    if (!(g_app_store = (NativeSymbol *)malloc(g_app_n * sizeof *g_app_store))) return 0;
    memcpy(g_app_store, g_app_rows, g_app_n * sizeof *g_app_store);
    return wasm_runtime_register_natives("moybyte.app", g_app_store, g_app_n) ? 1 : 0;
}

void hw_app_ext(const NativeSymbol *rows, uint32_t n, uint32_t (*grant)(void),
                int (*admit)(const uint8_t *, size_t, char *, size_t))
{
    if (g_app_rows) return;
    g_app_rows = rows;
    g_app_n = n;
    g_app_grant = grant;
    g_app_admit = admit;
    if (g_runtime) app_register();
}

/* 1 when WAMR is up with the import table registered -- once per process. */
int hw_runtime(void)
{
    if (!g_runtime) {
        uint32_t n = 0;
        moy_wasm_natives(&n);
        if (!g_natives && !(g_natives = (NativeSymbol *)malloc(n * sizeof *g_natives)))
            return 0;
        if (!wasm_runtime_init()) return 0;
        wasm_runtime_set_log_level(WASM_LOG_LEVEL_FATAL);
        if (moy_wasm_register(g_natives) != 0) {
            wasm_runtime_destroy();
            return 0;
        }
        g_runtime = 1;
        app_register();
    }
    return 1;
}

/* The cart's written files (moy-spec SPEC.md 16.12): moy_files' C over the
 * host's volume, as a board's moycore reaches it. The binding holds every path
 * to the manifest's "writable" entries. */
static int32_t hw_written(void *u, const char *path, uint32_t offset, uint8_t *dst,
                          uint32_t len)
{
    host_wasm *r = (host_wasm *)u;
    return r->files ? moy_files_read_written(r->files, (const uint8_t *)path, strlen(path),
                                             offset, dst, len) : -1;
}

static int32_t hw_write(void *u, const char *path, const uint8_t *data, uint32_t len)
{
    host_wasm *r = (host_wasm *)u;
    return r->files ? moy_files_write(r->files, (const uint8_t *)path, strlen(path), data, len)
                    : MOY_WASM_FAILED;
}

static int32_t hw_erase(void *u, const char *path)
{
    host_wasm *r = (host_wasm *)u;
    return r->files ? moy_files_erase(r->files, (const uint8_t *)path, strlen(path)) : -1;
}

static int32_t hw_list(void *u, const char *prefix, uint32_t index, uint8_t *dst,
                       uint32_t len)
{
    host_wasm *r = (host_wasm *)u;
    return r->files ? moy_files_name(r->files, (const uint8_t *)prefix, strlen(prefix),
                                     index, dst, len) : -1;
}

/* The cart at `cart_path`'s written files and the manifest's "writable"
 * entries joined by NULs (`len` bytes), before hw_load. 0, or 1 when the
 * store will not open a session. */
int hw_set_files(host_wasm *r, const char *cart_path, const char *writable, int len)
{
    if (r->files) moy_files_close(r->files);
    r->files = moy_files_open(cart_path);
    free(r->writable);
    r->writable = NULL;
    if (writable && len > 0 && (r->writable = (char *)calloc((size_t)len + 2, 1)) != NULL)
        memcpy(r->writable, writable, (size_t)len);
    r->w.writable = r->writable;
    r->w.written = hw_written;
    r->w.write = hw_write;
    r->w.erase = hw_erase;
    r->w.list = hw_list;
    r->w.files_user = r;
    return r->files == NULL;
}

/* `nbytes` is the caller's buffer size, CHECKED: ctypes hands over a bare
 * pointer, so a w/h that outruns the allocation would be a heap overwrite
 * with no Python-side trace. `wire` is the canvas's 64-entry table, NULL for
 * canonical RGB565; `wire_swapped` says the canvas's words are byte-swapped,
 * which a palette blit must match. */
host_wasm *hw_new(void *pix, int nbytes, int w, int h, const uint16_t *wire,
                  int wire_swapped, int32_t *snap, int32_t *aq, int aq_cap)
{
    host_wasm *r;
    if (w <= 0 || h <= 0 || (long)w * (long)h > (long)(nbytes / (int)sizeof(moy_pixel)))
        return NULL;
    if (!hw_runtime()) return NULL;
    r = (host_wasm *)calloc(1, sizeof(host_wasm));
    if (!r) return NULL;
    hl_claim(hw_release_live);             /* one run at a time: this one */
    memset(&moycore_RUN, 0, sizeof(moycore_RUN));
    moy_canvas_init(&moycore_RUN.c.canvas, (moy_pixel *)pix, w, h);
    if (wire) moy_canvas_wire(&moycore_RUN.c.canvas, wire);
    moycore_run_open(&moycore_RUN.c, snap, aq, aq_cap);
    moycore_RUN.c.con.host.layer_new = hw_layer_new;
    moycore_RUN.c.con.host.layer_free = hw_layer_free;
    moycore_RUN.c.con.rng = (uint32_t)moycore_run_now_ms() | 1u;
    r->w.read = hw_read;
    r->w.read_user = r;
    r->w.wire_swapped = wire_swapped ? 1 : 0;
    moy_stream_init(&r->pcm, r->pcm_ring, MOY_WASM_SND_DEPTH, MOY_WASM_SND_RATE);
    r->w.snd = hw_snd;
    r->w.snd_user = r;
    moycore_run_cur = &moycore_RUN.c;
    moycore_RUN.open = 1;
    moycore_RUN.wasm = 1;
    G_LIVE_W = r;
    hl_wasm_tick = hw_tick_live;
    hl_wasm_stuck = hw_stuck_live;
    return r;
}

void hw_set_sheet(host_wasm *r, uint8_t *pix, int nbytes)
{ (void)r; moycore_run_set_sheet(&moycore_RUN.c, pix, nbytes > 0 ? (size_t)nbytes : 0); }

void hw_set_map(host_wasm *r, uint8_t *cells, int nbytes, int w, int h)
{ (void)r; moycore_run_set_map(&moycore_RUN.c, cells, nbytes > 0 ? (size_t)nbytes : 0, w, h); }

void hw_set_flags(host_wasm *r, const uint8_t *flags, int nbytes)
{ (void)r; moycore_run_set_flags(&moycore_RUN.c, flags, nbytes > 0 ? (size_t)nbytes : 0); }

void hw_set_cfg(host_wasm *r, const char *blob, int len)
{ (void)r; moycore_run_set_cfg(&moycore_RUN.c, blob, len > 0 ? (size_t)len : 0); }

/* Load the cart's module from `dir`/`main`, check it against moy-spec SPEC.md 16's
 * shape and the manifest's `pages` BEFORE its memory exists, instantiate it
 * and bind it to the console. 0, or non-zero with the refusal in `err`. */
int hw_load(host_wasm *r, const char *dir, const char *main, int pages,
            char *err, int errlen)
{
    char path[HW_PATH_MAX + 260], msg[256];
    uint8_t *copy;
    FILE *f;
    long n;
    snprintf(r->dir, sizeof r->dir, "%s", dir);
    snprintf(path, sizeof path, "%s/%s", dir, main);
    f = fopen(path, "rb");
    if (!f) {
        put_err(err, errlen, "no module", main);
        return 1;
    }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    r->bytes = (uint8_t *)malloc(n > 0 ? (size_t)n : 1);
    copy = (uint8_t *)malloc(n > 0 ? (size_t)n : 1);
    if (!r->bytes || !copy || n <= 0 || fread(r->bytes, 1, (size_t)n, f) != (size_t)n) {
        fclose(f);
        free(copy);
        put_err(err, errlen, "unreadable module", main);
        return 1;
    }
    fclose(f);
    /* WAMR may rewrite the buffer it loads from, so the check reads a copy. */
    memcpy(copy, r->bytes, (size_t)n);
    msg[0] = 0;
    r->module = wasm_runtime_load(r->bytes, (uint32_t)n, msg, sizeof msg);
    if (!r->module) {
        free(copy);
        put_err(err, errlen, or_oom(msg, "load"), msg);
        return 1;
    }
    if (pages <= 0) {
        free(copy);
        put_err(err, errlen, "refused", "the manifest declares no \"memory\"");
        return 1;
    }
    moy_wasm_ext x = { "moybyte.app", g_app_store, g_app_n };
    uint32_t nx = g_app_store && g_app_grant && g_app_grant() ? 1u : 0u;
    if (moy_wasm_check_ext(r->module, copy, (size_t)n, (uint32_t)pages, &x, nx,
                           msg, sizeof msg)
        || (nx && g_app_admit && g_app_admit(copy, (size_t)n, msg, sizeof msg))) {
        free(copy);
        put_err(err, errlen, "refused", msg);
        return 1;
    }
    free(copy);
    r->inst = wasm_runtime_instantiate(r->module, HW_STACK, 0, msg, sizeof msg);
    if (!r->inst) {
        put_err(err, errlen, or_oom(msg, "instantiate"), msg);
        return 1;
    }
    r->env = wasm_runtime_create_exec_env(r->inst, HW_STACK);
    if (!r->env) {
        put_err(err, errlen, "exec env", NULL);
        return 1;
    }
    moycore_run_cur = &moycore_RUN.c;
    if (moy_wasm_open(&r->w, &moycore_RUN.c.con, r->env) != 0) {
        put_err(err, errlen, "refused", "a hook is missing");
        return 1;
    }
    r->bound = 1;
    return 0;
}

/* A trap ends the run and its frame is never presented: the canvas holds
 * whatever the cart had drawn when it died, so it is cleared before the
 * console paints its report over it. */
static int trapped(host_wasm *r)
{
    r->dead = 1;
    moy_reset_state(&moycore_RUN.c.canvas);
    moy_cls(&moycore_RUN.c.canvas, 0);
    return 1;
}

int hw_init(host_wasm *r, char *err, int errlen)
{
    if (!r->bound || r->dead || r != G_LIVE_W) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    moycore_run_cur = &moycore_RUN.c;
    moycore_run_tick_begin();
    if (moy_wasm_init(&r->w, err, (size_t)errlen)) return trapped(r);
    return 0;
}

/* One tick: _update, then _draw unless `draw` is 0 (a logic-only tick, #217)
 * or the cart called quit(), which ends it where it stands. */
int hw_tick(host_wasm *r, float dt, int draw, char *err, int errlen)
{
    if (!r->bound || r->dead || r != G_LIVE_W) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    moycore_run_cur = &moycore_RUN.c;
    moycore_run_tick_begin();              /* h_time counts from here */
    moy_reset_state(&moycore_RUN.c.canvas);
    if (moy_wasm_update(&r->w, dt, err, (size_t)errlen)) return trapped(r);
    if (draw && !r->w.quitting && moy_wasm_draw(&r->w, err, (size_t)errlen))
        return trapped(r);
    return 0;
}

/* Add `n` frames of the cart's stream into `out`, a block the host's output
 * renders at `rate`, at master level `master` (0..7). */
void hw_snd_mix(host_wasm *r, int16_t *out, int n, int rate, int master)
{
    moy_stream_mix(&r->pcm, out, n, rate, master);
}

/* The stream's counters: frames queued, frames mixed out, output frames that
 * found it empty, and the room now. */
void hw_snd_counts(host_wasm *r, uint32_t *out)
{
    out[0] = r->pcm.in;
    out[1] = r->pcm.out;
    out[2] = r->pcm.starved;
    out[3] = moy_stream_room(&r->pcm);
}

void hw_retarget(host_wasm *r, void *pix)
{ if (r == G_LIVE_W) moycore_RUN.c.canvas.pix = (moy_pixel *)pix; }

int hw_pmem_image(host_wasm *r, int32_t *out, int n)
{ return r == G_LIVE_W ? moycore_run_pmem_image(&moycore_RUN.c, out, n) : 0; }

void hw_pmem_load(host_wasm *r, const int32_t *in, int n)
{ if (r == G_LIVE_W) moycore_run_pmem_load(&moycore_RUN.c, in, n); }

int hw_get_view(host_wasm *r, int *w, int *h)
{ return r == G_LIVE_W ? moycore_run_view(&moycore_RUN.c, w, h) : 0; }

void hw_free(host_wasm *r)
{
    if (!r) return;
    if (r == G_LIVE_W) hl_claim(NULL);     /* releases this one */
    moy_wasm_close(&r->w);
    if (r->env) wasm_runtime_destroy_exec_env(r->env);
    if (r->inst) wasm_runtime_deinstantiate(r->inst);
    if (r->module) wasm_runtime_unload(r->module);
    free(r->bytes);
    free(r->writable);
    if (r->files) moy_files_close(r->files);
    free(r);
}
