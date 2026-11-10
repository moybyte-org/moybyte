/* clock_gettime/CLOCK_MONOTONIC are POSIX, and this builds at -std=c99,
 * which hides them. Must precede every include. */
#define _POSIX_C_SOURCE 200809L

/* The host's WASM shim (docs/history/wasm_tier_plan_2026-09.md, phase 3), built into
 * the host's one library (runtime/host/host_native.py) wherever WAMR builds.
 *
 * A "runtime": "wasm" cart on the host runs through the SAME C the boards run:
 * libmoy's wasm binding (native/moycore/libmoy/moy_wasm.c, the import table as
 * WAMR native symbols) over moycore_RUN's console (native/moycore/
 * moycore_run.c), whose frames are the kernel's Player's (moy_play.c's wasm
 * row) as a Lua run's are, and WAMR itself, built for Linux from the fork the
 * boards vendor, at the commit native/moy_wasm/wamr_pin.h names
 * (runtime/host/wasm_binding.py fetches and builds it). The cart's written files
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
 * pthread requirement, and the runtime is initialised once per process. A
 * WASI command's `_start` runs on a thread of its own (hw_wasi_start), and
 * so does each WASI service's, several at once (hw_wasi_service).
 */

#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "moy.h"
#include "moy_audio.h"
#include "moy_wasm.h"
#include "moy_wasm_footprint.h"
#include "moycore_run.h"
#include "moycore_lua.h"
#include "moy_files.h"
#include "moy_ui_wasm.h"
#include "moy_app_run.h"
#include "moy_log.h"
#include "moy_proc.h"
#include "moy_proc_exit.h"
#include "moy_sh.h"
#include "moy_wasi.h"
#include "moy_vmq.h"

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
    int16_t    *pcm_deep;    /* a deeper queue the manifest asked for, or NULL */
    moy_files_t *files;      /* the cart's written files (moy_files.h) */
    char       *writable;    /* the manifest's "writable", libmoy's form */
    moy_app_run_t app;       /* the app's run its "moybyte.app" imports serve */
    uint32_t    rh;          /* its console's row (moycore_run_handle), 0 once released */
    int         take;        /* the layouts its blits leave in its memory (moycore_lua.h) */
    void       *next;        /* the runs made and not freed */
} host_wasm;

static int g_runtime;        /* 1 once WAMR is up and the table registered */

/* A row for a new run (moyhost_lua.c), and the frame hook the Player's wasm
 * row reaches through moycore_frame. */
moycore_run *hl_row_for_new(void);
extern int (*hl_wasm_tick)(float dt, int draw, char *err, size_t n);
extern void (*hl_wasm_stuck)(void);
int hw_tick(host_wasm *r, float dt, int draw, char *err, int errlen);
static host_wasm *G_RUNS;

/* Several compiled runs at once, each over its own console row: the run a
 * call is for is the one whose row is current, and `r`'s row is made current
 * for the calls below (WRUN). The current row as it is for a run whose row
 * has gone. */
static host_wasm *hw_current(void)
{
    for (host_wasm *r = G_RUNS; r; r = (host_wasm *)r->next)
        if (r->rh && moycore_run_of(r->rh) == moycore_run_row) return r;
    return NULL;
}

static moycore_run *hw_row(host_wasm *r)
{
    moycore_run *row = r ? moycore_run_of(r->rh) : NULL;
    if (row) moycore_run_use(row);
    return row ? row : &moycore_RUN;
}
#define WRUN (*hw_row(r))

/* The run's console goes; the run object stays its owner's to free. */
static void hw_release(host_wasm *r)
{
    moycore_run *row = moycore_run_of(r->rh);
    if (row) {
        moycore_run_use(row);
        moycore_run_close(&row->c);
        moycore_run_end(row);
    }
    r->rh = 0;
}

void hw_release_row(moycore_run *row)
{
    uint32_t h = moycore_run_handle(row);
    for (host_wasm *r = G_RUNS; r; r = (host_wasm *)r->next)
        if (r->rh == h) {
            hw_release(r);
            return;
        }
    moycore_run_use(row);                   /* a row no run names */
    moycore_run_close(&row->c);
    moycore_run_end(row);
}

static int hw_tick_live(float dt, int draw, char *err, size_t n)
{
    host_wasm *r = hw_current();
    if (!r) return 0;
    return hw_tick(r, dt, draw, err, (int)n) != 0 ? -1 : 0;
}

/* The runaway watch, from its thread: the instance raises "terminated by
 * user" when its next import call returns. */
static void hw_stuck_live(void)
{
    host_wasm *r = hw_current();
    if (r && r->inst) wasm_runtime_terminate(r->inst);
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

/* The app ABI's import adapter (native/moy_app/moy_app_wasm.h), in this same
 * library as on a board's image: its rows for module "moybyte.app" (the
 * toolkit's among them, native/moy_ui's), the run's grant and the load's
 * grant check, registered with WAMR once, beside the "moy" table, as
 * moycore_wasm.c registers them. */
const NativeSymbol *moy_app_wasm_natives(uint32_t *count);
int moy_app_wasm_admit(const moy_app_run_t *r, const uint8_t *head, size_t n, char *err,
                       size_t errlen);
static const NativeSymbol *g_app_rows;
static uint32_t g_app_n;

/* Registered where they are: const, generated in WAMR's lookup order. */
static int app_register(void)
{
    g_app_rows = moy_app_wasm_natives(&g_app_n);
    return wasm_runtime_register_natives_sorted("moybyte.app", g_app_rows, g_app_n) ? 1 : 0;
}

/* G_RUNS: every run made and not freed, what an import's exec env is matched
 * against. */

/* The run a "moybyte.app" native's call is for (moy_app_wasm.h): its exec
 * env's instance, whose custom data is the run's libmoy binding, the engine
 * of its process row (moy_proc.h); a run no Player launched has none, and is
 * found among the runs made. Each run holds its own, so two runs at once are
 * two grants. */
const moy_app_run_t *moy_app_wasm_run_of(void *e)
{
    wasm_module_inst_t inst = e ? wasm_runtime_get_module_inst((wasm_exec_env_t)e) : NULL;
    void *w = inst ? wasm_runtime_get_custom_data(inst) : NULL;
    if (w && moy_proc_of_engine(w)) return moy_proc_app_of(w);
    for (host_wasm *r = G_RUNS; r && w; r = (host_wasm *)r->next)
        if ((void *)&r->w == w) return r->app.g ? &r->app : NULL;
    return NULL;
}

/* The canvas the ui rows draw on (native/moy_ui/moy_ui_wasm.h): the live
 * run's draw target, an owed or kept frame written in first, as the board's
 * module does. */
void *moycore_ui_target(void)
{
    host_wasm *r = hw_current();
    if (!r || !r->bound || r->dead || r->w.items) return NULL;
    moy_wasm *w = &r->w;
    if (w->target == w->screen && (w->owed || w->kept)) {
        if (!w->owed) {
            w->owed = w->kept;
            w->kept = NULL;
        }
        moy_wasm_settle(w);
    }
    return w->target;
}

/* The frame hand-off, as the board's module offers it: a blit of a layout
 * the run takes leaves its frame in the cart's memory (moycore_lua.h). */
static int hw_frame(void *user, const uint8_t *pixels, const moy_pixel *lut)
{
    (void)pixels;
    return ((host_wasm *)user)->take & (lut ? MOYCORE_TAKE_PALETTE : MOYCORE_TAKE_565);
}

void moycore_wasm_take(int take)
{
    host_wasm *r = hw_current();
    if (r) r->take = take & (MOYCORE_TAKE_565 | MOYCORE_TAKE_PALETTE);
}

const uint8_t *moycore_wasm_owed(const uint16_t **lut, bool *kept)
{
    host_wasm *r = hw_current();
    const moy_pixel *l = NULL;
    *lut = NULL;
    *kept = false;
    if (!r || !r->bound) return NULL;
    *kept = r->w.kept != NULL;
    const uint8_t *px = moy_wasm_frame(&r->w, &l);
    *lut = (const uint16_t *)l;
    return px;
}

void moycore_wasm_presented(const uint8_t *kept)
{
    host_wasm *r = hw_current();
    if (r && r->bound) moy_wasm_presented(&r->w, kept);
}

void moycore_wasm_settle(void)
{
    host_wasm *r = hw_current();
    if (!r || !r->bound) return;
    if (!r->w.owed && r->w.kept) {
        r->w.owed = r->w.kept;
        r->w.kept = NULL;
    }
    moy_wasm_settle(&r->w);
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
        if (!app_register()) {
            wasm_runtime_destroy();
            return 0;
        }
        g_runtime = 1;
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
    if (hl_row_for_new() == NULL) {        /* its console's own row */
        free(r);
        return NULL;
    }
    r->next = G_RUNS;
    G_RUNS = r;
    moy_canvas_init(&moycore_RUN.c.canvas, (moy_pixel *)pix, w, h);
    if (wire) moy_canvas_wire(&moycore_RUN.c.canvas, wire);
    moycore_run_open(&moycore_RUN.c, snap, aq, aq_cap);
    moycore_RUN.c.con.host.layer_new = hw_layer_new;
    moycore_RUN.c.con.host.layer_free = hw_layer_free;
    moycore_RUN.c.con.rng = (uint32_t)moycore_run_now_ms() | 1u;
    r->w.read = hw_read;
    r->w.read_user = r;
    r->w.frame = hw_frame;
    r->w.frame_user = r;
    r->w.wire_swapped = wire_swapped ? 1 : 0;
    moy_stream_init(&r->pcm, r->pcm_ring, MOY_WASM_SND_DEPTH, MOY_WASM_SND_RATE);
    r->w.snd = hw_snd;
    r->w.snd_user = r;
    moycore_run_cur = &moycore_RUN.c;
    moycore_RUN.open = 1;
    moycore_RUN.wasm = 1;
    r->rh = moycore_run_handle(&moycore_RUN);
    moy_proc_bind(moycore_RUN.proc, &r->w, NULL);
    hl_wasm_tick = hw_tick_live;
    hl_wasm_stuck = hw_stuck_live;
    return r;
}

/* The app's run, `len` bytes of a moy_app_run_t (moy_app's `run()`): what its
 * imports from "moybyte.app" serve and its load is checked against. Before
 * hw_load. 0, or 1 for a buffer that is not one. */
int hw_set_app(host_wasm *r, const void *app, int len)
{
    if (len != (int)sizeof(moy_app_run_t)) return 1;
    memcpy(&r->app, app, sizeof(r->app));
    moycore_run *row = moycore_run_of(r->rh);
    if (row) moy_proc_bind(row->proc, &r->w, &r->app);
    return 0;
}

/* The engine's handle the run's imports are called with: its exec env, or
 * NULL before its load. */
void *hw_env(host_wasm *r)
{
    return r->env;
}

void hw_set_sheet(host_wasm *r, uint8_t *pix, int nbytes)
{ (void)r; moycore_run_set_sheet(&WRUN.c, pix, nbytes > 0 ? (size_t)nbytes : 0); }

void hw_set_map(host_wasm *r, uint8_t *cells, int nbytes, int w, int h)
{ (void)r; moycore_run_set_map(&WRUN.c, cells, nbytes > 0 ? (size_t)nbytes : 0, w, h); }

void hw_set_flags(host_wasm *r, const uint8_t *flags, int nbytes)
{ (void)r; moycore_run_set_flags(&WRUN.c, flags, nbytes > 0 ? (size_t)nbytes : 0); }

void hw_set_cfg(host_wasm *r, const char *blob, int len)
{ (void)r; moycore_run_set_cfg(&WRUN.c, blob, len > 0 ? (size_t)len : 0); }

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
    moy_wasm_ext x = { "moybyte.app", g_app_rows, g_app_n };
    uint32_t nx = r->app.g ? 1u : 0u;
    if (moy_wasm_check_ext(r->module, copy, (size_t)n, (uint32_t)pages, &x, nx,
                           msg, sizeof msg)
        || (nx && moy_app_wasm_admit(&r->app, copy, (size_t)n, msg, sizeof msg))) {
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
    moycore_run_cur = &WRUN.c;
    if (moy_wasm_open(&r->w, &WRUN.c.con, r->env) != 0) {
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
    r->w.kept = NULL;
    moy_reset_state(&WRUN.c.canvas);
    moy_cls(&WRUN.c.canvas, 0);
    return 1;
}

int hw_init(host_wasm *r, char *err, int errlen)
{
    if (!r->bound || r->dead || !r->rh) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    moycore_run_cur = &WRUN.c;
    moycore_run_tick_begin();
    if (moy_wasm_init(&r->w, err, (size_t)errlen)) return trapped(r);
    return 0;
}

/* A responsive app's exported `_layout(w, h, fs)` (docs/app_api_v1.md §3),
 * of three i32 parameters and no result: 1 called, 0 the module exports
 * none, -1 with the trap in err (which ends the run). moycore_wasm.c's
 * call_layout is the boards' twin. */
int hw_layout(host_wasm *r, int32_t w, int32_t h, int32_t fs, char *err, int errlen)
{
    if (!r->bound || r->dead || !r->rh) {
        put_err(err, errlen, "the cart is not running", NULL);
        return -1;
    }
    wasm_function_inst_t fn = wasm_runtime_lookup_function(r->inst, "_layout");
    if (!fn || wasm_func_get_param_count(fn, r->inst) != 3
            || wasm_func_get_result_count(fn, r->inst) != 0)
        return 0;
    moycore_run_cur = &WRUN.c;
    uint32_t argv[3] = {(uint32_t)w, (uint32_t)h, (uint32_t)fs};
    if (wasm_runtime_call_wasm(r->env, fn, 3, argv)) return 1;
    if (r->w.quitting) {
        wasm_runtime_clear_exception(r->inst);
        return 1;
    }
    const char *msg = wasm_runtime_get_exception(r->inst);
    put_err(err, errlen, msg ? msg : "the cart trapped", NULL);
    trapped(r);
    return -1;
}

/* One tick: _update, then _draw unless `draw` is 0 (a logic-only tick, #217)
 * or the cart called quit(), which ends it where it stands. */
int hw_tick(host_wasm *r, float dt, int draw, char *err, int errlen)
{
    if (!r->bound || r->dead || !r->rh) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    moycore_run_cur = &WRUN.c;
    moycore_run_tick_begin();              /* h_time counts from here */
    moycore_run_tick_dt(dt);
    moy_reset_state(&WRUN.c.canvas);
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
{ if (r->rh) WRUN.c.canvas.pix = (moy_pixel *)pix; }

int hw_pmem_image(host_wasm *r, int32_t *out, int n)
{ return r->rh ? moycore_run_pmem_image(&WRUN.c, out, n) : 0; }

void hw_pmem_load(host_wasm *r, const int32_t *in, int n)
{ if (r->rh) moycore_run_pmem_load(&WRUN.c, in, n); }

int hw_get_view(host_wasm *r, int *w, int *h)
{ return r->rh ? moycore_run_view(&WRUN.c, w, h) : 0; }

/* The manifest's "audio_ring": `snd`'s queue `depth` frames deep, in the
 * boards' range (native/moy_audio/moy_audio_snd.h's 64..65536). 0, or -1
 * with the default queue kept. Before the load. */
int hw_set_ring(host_wasm *r, uint32_t depth)
{
    int16_t *ring = r->pcm_ring;
    if (depth < 64u || depth > 65536u) return -1;
    if (depth > MOY_WASM_SND_DEPTH) {
        ring = (int16_t *)calloc(depth, sizeof(int16_t));
        if (!ring) return -1;
    }
    free(r->pcm_deep);
    r->pcm_deep = ring == r->pcm_ring ? NULL : ring;
    moy_stream_init(&r->pcm, ring, depth, MOY_WASM_SND_RATE);
    return 0;
}

void hw_free(host_wasm *r)
{
    if (!r) return;
    if (r->rh) hw_release(r);
    for (host_wasm **p = &G_RUNS; *p; p = (host_wasm **)&(*p)->next)
        if (*p == r) {
            *p = (host_wasm *)r->next;
            break;
        }
    moy_wasm_close(&r->w);
    if (r->env) wasm_runtime_destroy_exec_env(r->env);
    if (r->inst) wasm_runtime_deinstantiate(r->inst);
    if (r->module) wasm_runtime_unload(r->module);
    free(r->bytes);
    free(r->writable);
    free(r->pcm_deep);
    if (r->files) moy_files_close(r->files);
    free(r);
}

/* A WASI command (native/moy_wasm/moy_wasi.h) in the shell session running on
 * the calling thread, the module `path`'s own main.wasm on the interpreter, as
 * a board's interpreted session runs it, run in steps as a board runs it: its
 * `_start` on a thread of its own, its calls served on the thread that steps
 * it (the runner below, moy_wasi.h's "IN STEPS"). One at a time. The natives
 * are registered once, beside the cart table, and stay. */
static int g_wasi;   /* moy_wasi_register has run */

typedef struct {
    moy_wasi_t *w;
    uint8_t *bytes;
    wasm_module_t module;
    wasm_module_inst_t inst;
    wasm_exec_env_t env;
    pthread_t tid;
    pthread_mutex_t m;
    pthread_cond_t c;
    void (*fn)(void *arg);      /* the call the module's thread waits on */
    void *arg;
    int served;
    int returned;               /* `_start` has returned */
    int main;                   /* moy_wasi_main's answer */
    char err[256];
} wasi_job;

static wasi_job *g_job;

/* The module's thread: a call's body handed to the stepping thread, waited for. */
static int job_hop(void (*fn)(void *arg), void *arg)
{
    wasi_job *j = g_job;
    if (!j) return -1;
    pthread_mutex_lock(&j->m);
    j->fn = fn;
    j->arg = arg;
    j->served = 0;
    pthread_cond_broadcast(&j->c);
    while (!j->served) pthread_cond_wait(&j->c, &j->m);
    pthread_mutex_unlock(&j->m);
    return 0;
}

static void *job_main(void *arg)
{
    wasi_job *j = (wasi_job *)arg;
    wasm_runtime_init_thread_env();
    int main = moy_wasi_main(j->w, j->env, j->err, sizeof j->err);
    wasm_runtime_destroy_thread_env();
    pthread_mutex_lock(&j->m);
    j->main = main;
    j->returned = 1;
    pthread_cond_broadcast(&j->c);
    pthread_mutex_unlock(&j->m);
    return NULL;
}

static bool job_done(void *job)
{
    wasi_job *j = (wasi_job *)job;
    pthread_mutex_lock(&j->m);
    int r = j->returned;
    pthread_mutex_unlock(&j->m);
    return r != 0;
}

static void job_serve(void *job, int64_t us)
{
    wasi_job *j = (wasi_job *)job;
    struct timespec at;
    clock_gettime(CLOCK_REALTIME, &at);
    int64_t ns = (int64_t)at.tv_nsec + us * 1000;
    at.tv_sec += (time_t)(ns / 1000000000);
    at.tv_nsec = (long)(ns % 1000000000);
    pthread_mutex_lock(&j->m);
    while (!j->fn && !j->returned && pthread_cond_timedwait(&j->c, &j->m, &at) == 0) {
    }
    void (*fn)(void *) = j->fn;
    void *arg = j->arg;
    j->fn = NULL;
    pthread_mutex_unlock(&j->m);
    if (fn) {
        fn(arg);
        pthread_mutex_lock(&j->m);
        j->served = 1;
        pthread_cond_broadcast(&j->c);
        pthread_mutex_unlock(&j->m);
    }
}

static void job_terminate(void *job)
{
    wasm_runtime_terminate(((wasi_job *)job)->inst);
}

static const moy_wasi_runner_t g_runner = { job_done, job_serve, job_terminate };

static void job_free(wasi_job *j)
{
    moy_wasi_free(j->w);
    if (j->env) wasm_runtime_destroy_exec_env(j->env);
    if (j->inst) wasm_runtime_deinstantiate(j->inst);
    if (j->module) wasm_runtime_unload(j->module);
    free(j->bytes);
    pthread_cond_destroy(&j->c);
    pthread_mutex_destroy(&j->m);
    free(j);
}

/* The WASI natives, registered once beside the cart table: whether they are. */
static int wasi_natives(char *err, int errlen)
{
    if (!hw_runtime()) {
        put_err(err, errlen, "the runtime would not start", NULL);
        return 0;
    }
    if (!g_wasi) {
        if (moy_wasi_register() != 0) {
            put_err(err, errlen, "the WASI table would not register", NULL);
            return 0;
        }
        g_wasi = 1;
    }
    return 1;
}

/* The module `path` read, loaded, checked as a WASI program and instantiated
 * with its exec env: 1, or 0 with the reason in `err` and what was made in
 * the out parameters for its owner to free. */
static int wasi_load(const char *path, uint8_t **bytes, wasm_module_t *module,
                     wasm_module_inst_t *inst, wasm_exec_env_t *env, char *err, int errlen)
{
    FILE *f;
    long n = 0;
    char msg[160];
    if (!(f = fopen(path, "rb"))) {
        put_err(err, errlen, "no module", path);
        return 0;
    }
    if (fseek(f, 0, SEEK_END) == 0 && (n = ftell(f)) > 0 && fseek(f, 0, SEEK_SET) == 0
        && (*bytes = (uint8_t *)malloc((size_t)n))
        && fread(*bytes, 1, (size_t)n, f) != (size_t)n) {
        free(*bytes);
        *bytes = NULL;
    }
    fclose(f);
    if (!*bytes) {
        put_err(err, errlen, "the module would not read", path);
        return 0;
    }
    msg[0] = 0;
    if (!(*module = wasm_runtime_load(*bytes, (uint32_t)n, msg, sizeof msg))) {
        put_err(err, errlen, "load", msg);
    } else if (moy_wasi_check(*module, msg, sizeof msg) != 0) {
        put_err(err, errlen, "refused", msg);
    } else if (!(*inst = wasm_runtime_instantiate(*module, MOY_WASI_STACK, 0, msg,
                                                  sizeof msg))) {
        put_err(err, errlen, or_oom(msg, "instantiate"), msg);
    } else if (!(*env = wasm_runtime_create_exec_env(*inst, MOY_WASI_STACK))) {
        put_err(err, errlen, "out of memory", "no exec env");
    } else {
        return 1;
    }
    return 0;
}

/* The run started: 0 with `_start` running, or -3 with the reason in `err`
 * (a module that would not load or run here, or a command already running). */
int hw_wasi_start(const char *path, int argc, const char **argv, const char *roots,
                  uint32_t bg_pid, uint32_t budget_ms, const moy_app_run_t *run, char *err,
                  int errlen)
{
    moy_sh_t *s = moy_sh_running();
    if (!s) {
        put_err(err, errlen, "needs a shell session", NULL);
        return -3;
    }
    if (g_job) {
        put_err(err, errlen, "a command is running", NULL);
        return -3;
    }
    if (!wasi_natives(err, errlen)) return -3;
    wasi_job *j = (wasi_job *)calloc(1, sizeof *j);
    if (!j) {
        put_err(err, errlen, "out of memory", "no record for the command");
        return -3;
    }
    pthread_mutex_init(&j->m, NULL);
    pthread_cond_init(&j->c, NULL);
    int ok = 0;
    if (!wasi_load(path, &j->bytes, &j->module, &j->inst, &j->env, err, errlen)) {
    } else if (!(j->w = moy_wasi_new(s, argc, argv, roots, bg_pid, budget_ms, job_hop))) {
        put_err(err, errlen, "out of memory", "no record for the command");
    } else {
        moy_wasi_set_run(j->w, run);
        g_job = j;
        if (pthread_create(&j->tid, NULL, job_main, j) == 0) {
            ok = 1;
        } else {
            g_job = NULL;
            put_err(err, errlen, "no thread for the command", NULL);
        }
    }
    if (!ok) {
        job_free(j);
        return -3;
    }
    return 0;
}

/* The run's end: its thread joined, its record freed, its exit status, or
 * MOY_WASI_TRAPPED with the trap in `err`. */
static int job_end(char *err, int errlen)
{
    wasi_job *j = g_job;
    pthread_join(j->tid, NULL);
    int code = moy_wasi_status(j->w, j->main);
    if (code < 0) put_err(err, errlen, j->err, NULL);
    g_job = NULL;
    job_free(j);
    return code;
}

/* The run's calls served for `max_us` at most: MOY_WASI_RUNNING while it goes
 * on, else its end (job_end's). -3 with no run. */
int hw_wasi_step(uint32_t max_us, char *err, int errlen)
{
    if (!g_job) {
        put_err(err, errlen, "no command is running", NULL);
        return -3;
    }
    if (moy_wasi_step(g_job->w, &g_runner, g_job, max_us) == MOY_WASI_RUNNING)
        return MOY_WASI_RUNNING;
    return job_end(err, errlen);
}

/* The run ended as Ctrl-C ends it: its end (job_end's). -3 with no run. */
int hw_wasi_stop(char *err, int errlen)
{
    if (!g_job) {
        put_err(err, errlen, "no command is running", NULL);
        return -3;
    }
    moy_wasi_stop(g_job->w, &g_runner, g_job);
    return job_end(err, errlen);
}

/* Whether a run is live: what a test reads for a leak. */
int hw_wasi_live(void)
{
    return g_job != NULL;
}

/* WASI services (moy_wasi.h's A SERVICE): each its `_start` on a pthread of
 * its own, several at once and apart from the command's job, its calls run
 * in place there (no session, no hop). Started, stopped, reaped and read on
 * the console's thread, which alone walks the list; a service's thread
 * touches only its own record. A record lives until its end is reaped. */
typedef struct wasi_svc {
    struct wasi_svc *next;
    uint32_t pid;
    moy_wasi_t *w;
    uint8_t *bytes;
    wasm_module_t module;
    wasm_module_inst_t inst;
    wasm_exec_env_t env;
    pthread_t tid;
    int threaded;
    int returned;               /* `_start` has returned (atomic) */
    int main;                   /* moy_wasi_main's answer */
    int stopped;                /* its stop answered for it: reaped unreported */
    int kept;                   /* a Player app's run: its end is its stop's, never the reap's */
    char err[256];
} wasi_svc;

static wasi_svc *g_svcs;

static void *svc_main(void *arg)
{
    wasi_svc *v = (wasi_svc *)arg;
    wasm_runtime_init_thread_env();
    v->main = moy_wasi_main(v->w, v->env, v->err, sizeof v->err);
    wasm_runtime_destroy_thread_env();
    __atomic_store_n(&v->returned, 1, __ATOMIC_RELEASE);
    return NULL;
}

static int svc_returned(wasi_svc *v)
{
    return __atomic_load_n(&v->returned, __ATOMIC_ACQUIRE);
}

static void svc_free(wasi_svc *v)
{
    if (v->threaded) pthread_join(v->tid, NULL);
    moy_wasi_free(v->w);
    if (v->env) wasm_runtime_destroy_exec_env(v->env);
    if (v->inst) wasm_runtime_deinstantiate(v->inst);
    if (v->module) wasm_runtime_unload(v->module);
    free(v->bytes);
    free(v);
}

static void svc_unlink(wasi_svc *v)
{
    for (wasi_svc **p = &g_svcs; *p; p = &(*p)->next) {
        if (*p == v) {
            *p = v->next;
            return;
        }
    }
}

static wasi_svc *svc_of(uint32_t pid)
{
    for (wasi_svc *v = g_svcs; v; v = v->next) {
        if (v->pid == pid && !v->stopped) return v;
    }
    return NULL;
}

/* Its exit status once `_start` has returned, its record freed. */
static int svc_end(wasi_svc *v)
{
    pthread_join(v->tid, NULL);
    v->threaded = 0;
    int code = moy_wasi_service_status(v->w, v->main, v->err);
    svc_unlink(v);
    svc_free(v);
    return code;
}

/* Waits up to `us` for `_start` to return: whether it has. */
/* The wait for a service to return runs on the VM's task, so it serves the
 * VM's queue: a row the service hops with (a device handle's read on its
 * tick) is answered, and the service reaches its poll and its end. */
static int svc_wait(wasi_svc *v, int64_t us)
{
    struct timespec ms = { 0, 1000000 };
    for (; us > 0 && !svc_returned(v); us -= 1000) {
        moy_vmq_serve();
        nanosleep(&ms, NULL);
    }
    return svc_returned(v);
}

/* Service `pid` started from the module `path`, its argv `id`: 0, or -3
 * with the reason in `err`. Its event queue is the scheduler's. */
int hw_wasi_service(const char *path, uint32_t pid, const char *id, const moy_app_run_t *run,
                    char *err, int errlen)
{
    if (svc_of(pid)) {
        put_err(err, errlen, "a service runs as that process", NULL);
        return -3;
    }
    if (!wasi_natives(err, errlen)) return -3;
    wasi_svc *v = (wasi_svc *)calloc(1, sizeof *v);
    if (!v) {
        put_err(err, errlen, "out of memory", "no record for the service");
        return -3;
    }
    v->pid = pid;
    if (!wasi_load(path, &v->bytes, &v->module, &v->inst, &v->env, err, errlen)) {
        svc_free(v);
        return -3;
    }
    if (!(v->w = moy_wasi_service(pid, id))) {
        put_err(err, errlen, "out of memory", "no record for the service");
        svc_free(v);
        return -3;
    }
    moy_wasi_set_run(v->w, run);
    if (pthread_create(&v->tid, NULL, svc_main, v) != 0) {
        put_err(err, errlen, "no thread for the service", NULL);
        svc_free(v);
        return -3;
    }
    v->threaded = 1;
    v->next = g_svcs;
    g_svcs = v;
    return 0;
}

/* Service `pid` stopped: its descriptor hung up, killed (137) past
 * MOY_WASI_STOP_US. Its exit status, or -1 for no such service. A killed one
 * that has not returned is reaped, unreported, once it does. */
int hw_wasi_service_stop(uint32_t pid)
{
    wasi_svc *v = svc_of(pid);
    if (!v) return -1;
    if (!svc_returned(v)) {
        moy_wasi_hangup(v->w);
        if (!svc_wait(v, MOY_WASI_STOP_US)) {
            moy_wasi_kill(v->w, MOY_PROC_KILLED);
            if (!svc_wait(v, 100000)) {
                v->stopped = 1;
                return MOY_PROC_KILLED;
            }
        }
    }
    return svc_end(v);
}

/* The services that ended, up to `n`, into `pids` and `codes`: their count.
 * The watchdog (moy_wasi_watch) runs over the live ones first, and kills a
 * wake past its strikes. */
int hw_wasi_service_reap(uint32_t *pids, int32_t *codes, int n)
{
    int got = 0;
    wasi_svc *next;
    for (wasi_svc *v = g_svcs; v; v = next) {
        next = v->next;
        if (!svc_returned(v)) {
            const char *why = v->stopped ? NULL : moy_wasi_watch(v->w);
            if (why) {
                char line[96];
                int k = snprintf(line, sizeof line, "killed: %s", why);
                moy_log_write(MOY_LOG_ERR, v->pid, line, (size_t)k);
                moy_wasi_kill(v->w, MOY_PROC_KILLED);
            }
            continue;
        }
        if (v->kept && !v->stopped) continue;
        int stopped = v->stopped;
        uint32_t pid = v->pid;
        int code = svc_end(v);
        if (!stopped && got < n) {
            pids[got] = pid;
            codes[got] = code;
            got++;
        }
    }
    return got;
}

/* Service `pid`'s linear memory bytes and its wakes' time (us): 0, or -1
 * once its end is reaped. */
int hw_wasi_service_info(uint32_t pid, uint32_t *mapped, uint32_t *cost_us)
{
    wasi_svc *v = svc_of(pid);
    if (!v) return -1;
    moy_wasi_figures(v->w, mapped, cost_us);
    return 0;
}

/* Service `pid` made a Player app's run (runtime/run/wasi_run.py), whose end
 * the reap leaves to its stop: 0, or -1 for no such service. */
int hw_wasi_service_keep(uint32_t pid)
{
    wasi_svc *v = svc_of(pid);
    if (!v) return -1;
    v->kept = 1;
    return 0;
}

/* Whether service `pid`'s `_start` has returned (1 for no such service). */
int hw_wasi_service_done(uint32_t pid)
{
    wasi_svc *v = svc_of(pid);
    return !v || svc_returned(v);
}

/* How many service records are live: what a test reads for a leak. */
int hw_wasi_services(void)
{
    int n = 0;
    for (wasi_svc *v = g_svcs; v; v = v->next) n++;
    return n;
}
