/* clock_gettime/CLOCK_MONOTONIC are POSIX, and this builds at -std=c99,
 * which hides them. Must precede every include. */
#define _POSIX_C_SOURCE 200809L

/* The host's WASM shim (docs/wasm_tier_plan_2026-09.md, phase 3).
 *
 * A "runtime": "wasm" cart on the host runs through the SAME C the boards run:
 * libmoy's wasm binding (native/moycore/libmoy/moy_wasm.c, the import table as
 * WAMR native symbols) over the console in moyhost_console.h -- the host half
 * moyhost_lua.c uses too -- and WAMR itself, built for Linux from the fork the
 * boards vendor, at the commit native/moy_wasm/wamr_pin.h names
 * (runtime/wasm_binding.py fetches and builds it). There is no second engine:
 * a host and a device that disagree about what a verb does is the disease the
 * Lua shim was written to end, and this is its twin.
 *
 * How the host EXECUTES the module is host policy (the plan's decision): the
 * boards run a per-chip AOT module under a provenance key; the host runs the
 * cart's own main.wasm on WAMR's interpreter, which needs no compiler and no
 * key. Everything a cart can observe -- the verbs, the blit, the read, the
 * traps -- is the binding's, and the binding is the same file.
 *
 * One run at a time, on whatever thread calls in: WAMR on Linux has no
 * pthread requirement, and the runtime is initialised once per process.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "moy.h"
#include "moy_wasm.h"
#include "moy_wasm_footprint.h"
#include "moyhost_console.h"

#ifndef MOY_WASM
#error "moyhost_wasm.c is the wasm build -- compile with -DMOY_WASM=1"
#endif

/* The exec env's stack: the interpreter's operand and call frames. */
#define HW_STACK (64 * 1024)
#define HW_PATH_MAX 1024

typedef struct {
    hc_console  hc;          /* the console, shared with moyhost_lua.c */
    char        dir[HW_PATH_MAX];   /* the cart's folder: `read`'s only root */
    uint8_t    *bytes;       /* the module file, held while it is loaded */
    wasm_module_t module;
    wasm_module_inst_t inst;
    wasm_exec_env_t env;
    moy_wasm    w;
    int         bound;       /* moy_wasm_open succeeded */
    int         dead;        /* trapped: never called again */
} host_wasm;

static int g_runtime;        /* 1 once WAMR is up and the table registered */
/* The table's registration storage: WAMR sorts it in place and points at it
 * until the runtime is destroyed, which on the host is never. */
static NativeSymbol *g_natives;

/* The cart's own files, and nothing else (the proposal's `read`). The name
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
    }
    return 1;
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
    moy_canvas_init(&r->hc.canvas, (moy_pixel *)pix, w, h);
    if (wire) moy_canvas_wire(&r->hc.canvas, wire);
    hc_open(&r->hc, snap, aq, aq_cap);
    r->hc.con.host.layer_new = hw_layer_new;
    r->hc.con.host.layer_free = hw_layer_free;
    r->hc.con.rng = (uint32_t)hc_now_ms() | 1u;
    r->w.read = hw_read;
    r->w.read_user = r;
    r->w.wire_swapped = wire_swapped ? 1 : 0;
    HC = &r->hc;
    return r;
}

void hw_set_sheet(host_wasm *r, uint8_t *pix, int nbytes)
{ hc_set_sheet(&r->hc, pix, nbytes); }

void hw_set_map(host_wasm *r, uint8_t *cells, int nbytes, int w, int h)
{ hc_set_map(&r->hc, cells, nbytes, w, h); }

void hw_set_flags(host_wasm *r, const uint8_t *flags, int nbytes)
{ hc_set_flags(&r->hc, flags, nbytes); }

void hw_set_cfg(host_wasm *r, const char *blob, int len)
{ hc_set_cfg(&r->hc, blob, len); }

/* Load the cart's module from `dir`/`main`, check it against the proposal's
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
    if (moy_wasm_check(r->module, copy, (size_t)n, (uint32_t)pages, msg, sizeof msg)) {
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
    HC = &r->hc;
    if (moy_wasm_open(&r->w, &r->hc.con, r->env) != 0) {
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
    moy_reset_state(&r->hc.canvas);
    moy_cls(&r->hc.canvas, 0);
    return 1;
}

int hw_init(host_wasm *r, char *err, int errlen)
{
    if (!r->bound || r->dead) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    HC = &r->hc;
    g_tick_ms = hc_now_ms();
    if (moy_wasm_init(&r->w, err, (size_t)errlen)) return trapped(r);
    return 0;
}

/* One tick: _update, then _draw unless `draw` is 0 (a logic-only tick, #217)
 * or the cart called quit(), which ends it where it stands. */
int hw_tick(host_wasm *r, float dt, int draw, char *err, int errlen)
{
    if (!r->bound || r->dead) {
        put_err(err, errlen, "the cart is not running", NULL);
        return 1;
    }
    HC = &r->hc;
    g_tick_ms = hc_now_ms();              /* h_time counts from here */
    moy_reset_state(&r->hc.canvas);
    if (moy_wasm_update(&r->w, dt, err, (size_t)errlen)) return trapped(r);
    if (draw && !r->w.quitting && moy_wasm_draw(&r->w, err, (size_t)errlen))
        return trapped(r);
    return 0;
}

void hw_retarget(host_wasm *r, void *pix)
{ r->hc.canvas.pix = (moy_pixel *)pix; }

int hw_pmem_image(host_wasm *r, int32_t *out, int n)
{ return hc_pmem_image(&r->hc, out, n); }

void hw_pmem_load(host_wasm *r, const int32_t *in, int n)
{ hc_pmem_load(&r->hc, in, n); }

int hw_get_view(host_wasm *r, int *w, int *h)
{ return hc_get_view(&r->hc, w, h); }

void hw_free(host_wasm *r)
{
    if (!r) return;
    moy_wasm_close(&r->w);
    if (r->env) wasm_runtime_destroy_exec_env(r->env);
    if (r->inst) wasm_runtime_deinstantiate(r->inst);
    if (r->module) wasm_runtime_unload(r->module);
    free(r->bytes);
    hc_close(&r->hc);
    free(r);
}
