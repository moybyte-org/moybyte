// moy_wasm: the WebAssembly cart tier's ENGINE (docs/history/wasm_tier_plan_2026-09.md,
// phase 1). The vendored WAMR runtime (wamr/, AOT only) and the thread a
// module runs on -- nothing else. There are no verbs here: the import table is
// C in libmoy beside the Lua binding, and moycore hosts it (phases 2 and 3).
//
// A RUN is one pthread, and everything WAMR does happens on it: the runtime's
// init with its pool in PSRAM, the load, the provenance check, the
// instantiation, the calls, the unload and the runtime's teardown. WAMR asks
// pthread_self() for the calling thread and IDF's answer asserts on a task that
// is not a pthread -- the MicroPython task is one such task -- and a run's
// stack is its own setting per board rather than whatever the VM's task has.
//
// The Python side reads the module FILE (through the VFS, so the T-Deck's SD
// and the P4s' flash store look the same) into a PSRAM buffer, checks its
// admission (moy_ota_admit_module) -- a module whose signature does not verify
// never reaches the runtime, and one with no signature reaches it only when
// the cart's consent admits it (app_perms.admits_unsigned) --
// starts the thread, and collects a report when it ends. One run at a time.
//
// A cart the Player runs is a SESSION (phase 3, moy_wasm_session.h): the same
// thread and the same load and key check, held open across the cart's life,
// with moycore -- the host half, which owns the console and the import table --
// supplying the callbacks the thread calls at each step. The engine binds no
// verb itself.
//
// See README.md for the memory placement, the provenance key, the session and
// the answer to "can a runaway export be stopped".

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>

#include "py/obj.h"
#include "py/objstr.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "py/mperrno.h"
#include "py/stream.h"
#include "py/builtin.h"
#include "py/mpthread.h"

#include "sdkconfig.h"
#include "esp_heap_caps.h"
#include "esp_task.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/idf_additions.h"   // pxTaskGetStackStart

#include "wasm_export.h"
#include "moy_wasm_footprint.h"

// WAMR's allocator over a block (wamr/core/shared/mem-alloc/mem_alloc.h):
// a session's pool.
typedef void *mem_allocator_t;
mem_allocator_t mem_allocator_create(void *mem, uint32_t size);
int mem_allocator_destroy(mem_allocator_t allocator);
void *mem_allocator_malloc(mem_allocator_t allocator, uint32_t size);
void *mem_allocator_realloc(mem_allocator_t allocator, void *ptr, uint32_t size);
void mem_allocator_free(mem_allocator_t allocator, void *ptr);
#include "moy_wasm_key.h"
#include "moy_wasm_session.h"
#include "moy_wasm_thread.h"
#include "moy_wasi.h"
#include "moy_sh.h"
#include "moy_log.h"
#include "moy_proc_exit.h"
#include "../moy_net/moy_ota.h"
#include "../moy_store/moy_vol.h"

// A VM runs (native/moy_kernel's loop): weak, so an image without the kernel
// always has one.
bool moy_loop_vm(void) __attribute__((weak));

// -- per-board settings (mpconfigboard.h) -------------------------------------

// The run stack and the runtime pool are moy_wasm_footprint.h's: what a load
// takes is stated once, for the engine and for the Player's fit check.
//
// Where the thread runs: the MicroPython task's core and priority, so a run
// takes the VM's time; only a cart's par lanes (below) reach another core.
#ifndef MOY_WASM_CORE
#define MOY_WASM_CORE MP_TASK_COREID
#endif
#ifndef MOY_WASM_PRIO
#define MOY_WASM_PRIO (ESP_TASK_PRIO_MIN + 1)
#endif

// A cart's par lanes (moy_wasm_session.h): one thread on every core but the
// session's, at the session's priority, so a lane takes only what the tasks
// above it on that core -- the flush feed and fold, the radios, IDF's own --
// leave. A board declines them by setting MOY_WASM_ITEM_LANES to 0.
#ifndef MOY_WASM_ITEM_LANES
#define MOY_WASM_ITEM_LANES (portNUM_PROCESSORS - 1)
#endif
#ifndef MOY_WASM_LANE_PRIO
#define MOY_WASM_LANE_PRIO MOY_WASM_PRIO
#endif

// Bytes kept below the native-stack boundary handed to the runtime: the AOT
// code's stack check traps short of it, and the runtime's own frames and any
// native it calls live in what is left.
#define MOY_WASM_STACK_GUARD (2 * 1024)
#define MOY_WASM_FILE_MAX (8 * 1024 * 1024)
#define MOY_WASM_MAX_ARGS 8
#define ERR_MAX 160

// -- the run -----------------------------------------------------------------

typedef struct {
    // what the run does (written by start, read by the thread)
    uint8_t *file;                 // the module file, PSRAM
    uint32_t file_len;
    char export_name[64];
    uint32_t argc;
    uint32_t argv[MOY_WASM_MAX_ARGS];
    uint32_t loops;
    uint32_t stack_bytes;
    bool stack_psram;
    uint32_t pool_asked;           // a measurement's pool size; 0 is the engine's rule
    // what it found (written by the thread, read after the join)
    bool ok;
    char err[ERR_MAX];
    char wasm_hash[65];
    uint32_t loops_done;
    uint32_t value;
    uint32_t has_value;
    uint32_t mismatches;           // a later loop's result differing from the first's
    int64_t load_us, load_us_max, inst_us, call_us, call_us_min, call_us_max;
    uint32_t stack_free_min;       // the thread's stack high-water mark, as bytes never used
    uint32_t pool;                 // the runtime pool's size for this module
    uint32_t pool_peak;
    // shared while the thread runs
    wasm_module_inst_t live;       // guarded by g_lock; what terminate() stops
    volatile bool terminated;
    // the MicroPython side's bookkeeping
    pthread_t tid;
    bool started;
    bool threaded;                 // the thread was spawned (a refused module never has one)
    int64_t t_start;
    int64_t t_end;
    volatile bool finished;
    size_t sram_before;            // internal free when the run was started
} run_t;

// PSRAM, taken by the first start: an idle console holds none of it in
// internal SRAM.
static run_t *g_run;
// Every open cart session (below), which a run is never started beside.
static struct sess *g_sessions;
static SemaphoreHandle_t g_lock;
static StaticSemaphore_t g_lock_buf;

#define SRAM_CAPS (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#define PSRAM_CAPS (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)

#if CONFIG_IDF_TARGET_ESP32S3
#define KEY_TARGET MOY_WASM_KEY_ESP32S3
#define KEY_CHIP "esp32s3"
#elif CONFIG_IDF_TARGET_ESP32P4
#define KEY_TARGET MOY_WASM_KEY_ESP32P4
#define KEY_CHIP "esp32p4"
#else
#error "moy_wasm: no provenance key for this target (moy_wasm_key.h)"
#endif

static const char KEY_TAIL[] = "format " MOY_WASM_FORMAT_VERSION "\n" KEY_TARGET;

// What a failure for want of memory says first, whichever allocation it was:
// the Player reads it as a cart this board cannot fit, the notice a cart
// too big for the board gets before it loads (moy_wasm_footprint.h).
#define OUT_OF_MEMORY "out of memory"

// WAMR's text for a load or an instantiation that could not allocate.
static bool alloc_failed(const char *err)
{
    return strstr(err, "allocate") != NULL;
}

static void fail(run_t *r, const char *what, const char *detail)
{
    r->ok = false;
    // Truncated on purpose: an error is a line on a serial console.
    snprintf(r->err, sizeof(r->err), "%.40s%s%.110s", what, detail ? ": " : "",
             detail ? detail : "");
}

static bool is_hex(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        uint8_t c = p[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            return false;
        }
    }
    return true;
}

// The module's key against this build's: absent, malformed or different is a
// refusal, and the reason names the first field that differs.
static bool check_key(run_t *r, wasm_module_t module)
{
    uint32_t len = 0;
    const uint8_t *key = wasm_runtime_get_custom_section(module, MOY_WASM_KEY_SECTION, &len);
    if (!key) {
        fail(r, "refused", "no " MOY_WASM_KEY_SECTION " section (not built by tools/wasm_module.py)");
        return false;
    }
    const size_t magic = sizeof(MOY_WASM_KEY_MAGIC) - 1;
    const size_t wasm_line = 5 + 64 + 1;           // "wasm " + hash + "\n"
    if (len < magic + wasm_line || memcmp(key, MOY_WASM_KEY_MAGIC, magic) != 0
        || memcmp(key + magic, "wasm ", 5) != 0 || !is_hex(key + magic + 5, 64)
        || key[magic + wasm_line - 1] != '\n') {
        fail(r, "refused", "malformed provenance key");
        return false;
    }
    memcpy(r->wasm_hash, key + magic + 5, 64);
    r->wasm_hash[64] = 0;
    const uint8_t *got = key + magic + wasm_line;
    size_t got_len = len - magic - wasm_line;
    const size_t want_len = sizeof(KEY_TAIL) - 1;
    if (got_len == want_len && memcmp(got, KEY_TAIL, want_len) == 0) {
        return true;
    }
    // Name the first line that differs, as "got | want".
    size_t i = 0;
    size_t line = 0;
    while (i < got_len && i < want_len && got[i] == (uint8_t)KEY_TAIL[i]) {
        if (got[i] == '\n') {
            line = i + 1;
        }
        i++;
    }
    char g[48], w[48];
    size_t gn = 0, wn = 0;
    while (line + gn < got_len && got[line + gn] != '\n' && gn < sizeof(g) - 1) {
        g[gn] = (char)got[line + gn];
        gn++;
    }
    while (line + wn < want_len && KEY_TAIL[line + wn] != '\n' && wn < sizeof(w) - 1) {
        w[wn] = KEY_TAIL[line + wn];
        wn++;
    }
    g[gn] = w[wn] = 0;
    snprintf(r->err, sizeof(r->err), "refused: key mismatch '%s' (this build: '%s')", g, w);
    r->ok = false;
    return false;
}

static size_t pool_bytes(uint32_t module_len)
{
    return (size_t)moy_wasm_pool_bytes(module_len);
}

static void set_live(run_t *r, wasm_module_inst_t inst)
{
    xSemaphoreTake(g_lock, portMAX_DELAY);
    r->live = inst;
    xSemaphoreGive(g_lock);
}

// One load, check, instantiate, call, teardown. False ends the run.
static bool one_pass(run_t *r, uint32_t pass)
{
    char err[ERR_MAX];
    bool good = false;
    wasm_module_inst_t inst = NULL;
    wasm_exec_env_t env = NULL;

    // Freeable: the module copies what it keeps, so the same file image can be
    // loaded again and nothing points into it once the load returns.
    LoadArgs la;
    memset(&la, 0, sizeof(la));
    la.name = "";
    la.wasm_binary_freeable = true;
    int64_t t0 = esp_timer_get_time();
    wasm_module_t module = wasm_runtime_load_ex(r->file, r->file_len, &la, err, sizeof(err));
    int64_t dt = esp_timer_get_time() - t0;
    if (!module) {
        fail(r, alloc_failed(err) ? OUT_OF_MEMORY : "load", err);
        return false;
    }
    if (pass == 0) {
        r->load_us = dt;
    }
    if (dt > r->load_us_max) {
        r->load_us_max = dt;
    }
    if (!check_key(r, module)) {
        goto out;
    }

    t0 = esp_timer_get_time();
    inst = wasm_runtime_instantiate(module, MOY_WASM_EXEC_STACK, 0, err, sizeof(err));
    if (!inst) {
        fail(r, alloc_failed(err) ? OUT_OF_MEMORY : "instantiate", err);
        goto out;
    }
    env = wasm_runtime_create_exec_env(inst, MOY_WASM_EXEC_STACK);
    if (!env) {
        fail(r, OUT_OF_MEMORY, "no pool for the exec env");
        goto out;
    }
    if (pass == 0) {
        r->inst_us = esp_timer_get_time() - t0;
    }
    // The boundary the AOT code's stack check compares against: this thread's
    // own stack, less the guard the runtime and its natives need.
    wasm_runtime_set_native_stack_boundary(
        env, pxTaskGetStackStart(NULL) + MOY_WASM_STACK_GUARD);

    wasm_function_inst_t fn = wasm_runtime_lookup_function(inst, r->export_name);
    if (!fn) {
        fail(r, "no export", r->export_name);
        goto out;
    }
    uint32_t np = wasm_func_get_param_count(fn, inst);
    uint32_t nr = wasm_func_get_result_count(fn, inst);
    if (np != r->argc || nr > 1) {
        snprintf(r->err, sizeof(r->err), "%s takes %u args and returns %u values; called with %u",
                 r->export_name, (unsigned)np, (unsigned)nr, (unsigned)r->argc);
        r->ok = false;
        goto out;
    }
    if (np) {
        wasm_valkind_t kinds[MOY_WASM_MAX_ARGS];
        wasm_func_get_param_types(fn, inst, kinds);
        for (uint32_t i = 0; i < np; i++) {
            if (kinds[i] != WASM_I32) {
                fail(r, "unsupported", "only i32 arguments");
                goto out;
            }
        }
    }
    if (nr) {
        wasm_valkind_t kind;
        wasm_func_get_result_types(fn, inst, &kind);
        if (kind != WASM_I32) {
            fail(r, "unsupported", "only an i32 result");
            goto out;
        }
    }

    uint32_t argv[MOY_WASM_MAX_ARGS];
    memcpy(argv, r->argv, sizeof(argv));
    set_live(r, inst);
    t0 = esp_timer_get_time();
    bool called = wasm_runtime_call_wasm(env, fn, r->argc, argv);
    dt = esp_timer_get_time() - t0;
    set_live(r, NULL);
    if (!called) {
        const char *ex = wasm_runtime_get_exception(inst);
        fail(r, "trap", ex ? ex : "unknown");
        goto out;
    }
    if (pass == 0) {
        r->call_us = dt;
        r->call_us_min = dt;
    }
    if (dt < r->call_us_min) {
        r->call_us_min = dt;
    }
    if (dt > r->call_us_max) {
        r->call_us_max = dt;
    }
    if (nr) {
        if (!r->has_value) {
            r->value = argv[0];
            r->has_value = 1;
        } else if (argv[0] != r->value) {
            r->mismatches++;
        }
    }
    good = true;

out:
    if (env) {
        wasm_runtime_destroy_exec_env(env);
    }
    if (inst) {
        wasm_runtime_deinstantiate(inst);
    }
    wasm_runtime_unload(module);
    return good;
}

static void run_passes(run_t *r)
{
    r->pool = r->pool_asked ? r->pool_asked : pool_bytes(r->file_len);
    uint8_t *pool = heap_caps_malloc(r->pool, PSRAM_CAPS);
    if (!pool) {
        fail(r, OUT_OF_MEMORY, "no PSRAM for the runtime pool");
        return;
    }
    RuntimeInitArgs init;
    memset(&init, 0, sizeof(init));
    init.mem_alloc_type = Alloc_With_Pool;
    init.mem_alloc_option.pool.heap_buf = pool;
    init.mem_alloc_option.pool.heap_size = r->pool;
    if (!wasm_runtime_full_init(&init)) {
        fail(r, "runtime init", NULL);
        heap_caps_free(pool);
        return;
    }
    r->ok = true;
    for (uint32_t i = 0; i < r->loops; i++) {
        if (!one_pass(r, i)) {
            break;
        }
        r->loops_done = i + 1;
        if (r->terminated) {
            break;
        }
    }
    mem_alloc_info_t info;
    if (wasm_runtime_get_mem_alloc_info(&info)) {
        r->pool_peak = info.highmark_size;
    }
    wasm_runtime_destroy();
    heap_caps_free(pool);
}

static void *run_thread(void *arg)
{
    run_t *r = arg;
    run_passes(r);
    r->stack_free_min = uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t);
    r->t_end = esp_timer_get_time();
    r->finished = true;
    return NULL;
}

// -- the MicroPython side ------------------------------------------------------

static void release(run_t *r)
{
    if (r->file) {
        heap_caps_free(r->file);
        r->file = NULL;
    }
}

// The whole file, through the VFS, into PSRAM. Called between frames, like
// every other store read. Raises; on success the caller owns *out.
//
// `hold` is the linear memory the module will ask for once it is loaded, and
// the file is read into a block that size (moy_wasm_file_block): the load
// allocates the text and the runtime's pool while the file is held, so a file
// read into a block of its own size leaves a hole the linear memory cannot
// use and the free PSRAM around it in two pieces. Freed after the load, a
// block the linear memory's size is where the linear memory goes.
// The same read with no VM (a run the kernel started after stopping it,
// docs/history/kernel_cartpath_2026-10.md section 5.2): the kernel's volume, inside the
// board's bus gate. 0, or an errno with nothing held.
static int read_module_c(const char *path, uint32_t hold, uint8_t **out, uint32_t *out_len)
{
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    int gated = moy_vol_gate_enter(path);
    int e = moy_vol_at(path, &v, &rest);
    if (e == 0) {
        e = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    uint32_t size = 0;
    if (e == 0) {
        e = moy_vol_size(f, &size);
    }
    uint8_t *buf = NULL;
    if (e == 0 && (size == 0 || size > MOY_WASM_FILE_MAX)) {
        e = MOY_EFBIG;
    }
    if (e == 0) {
        buf = heap_caps_malloc((size_t)moy_wasm_file_block(hold, (uint64_t)size), PSRAM_CAPS);
        if (!buf) {
            buf = heap_caps_malloc((size_t)size, PSRAM_CAPS);
        }
        e = buf ? 0 : MOY_ENOMEM;
    }
    for (uint32_t at = 0; e == 0 && at < size;) {
        size_t got = 0;
        e = moy_vol_read(f, buf + at, size - at < 65536 ? size - at : 65536, &got);
        if (e == 0 && got == 0) {
            e = MOY_EIO;
        }
        at += (uint32_t)got;
    }
    if (f != NULL) {
        int ce = moy_vol_close(f);
        e = e ? e : ce;
    }
    moy_vol_gate_leave(gated);
    if (e != 0) {
        heap_caps_free(buf);
        return e;
    }
    *out = buf;
    *out_len = size;
    return 0;
}

static void read_module(mp_obj_t path, uint32_t hold, uint8_t **out, uint32_t *out_len)
{
    mp_obj_t args[2] = { path, MP_OBJ_NEW_QSTR(MP_QSTR_rb) };
    mp_obj_t f = mp_call_function_n_kw(MP_OBJ_FROM_PTR(&mp_builtin_open_obj), 2, 0, args);
    int e = 0;
    mp_off_t size = mp_stream_seek(f, 0, MP_SEEK_END, &e);
    if (e == 0) {
        mp_stream_seek(f, 0, MP_SEEK_SET, &e);
    }
    if (e != 0 || size <= 0 || size > MOY_WASM_FILE_MAX) {
        mp_stream_close(f);
        mp_raise_ValueError(MP_ERROR_TEXT("module file is empty, unreadable or too big"));
    }
    uint8_t *buf = heap_caps_malloc((size_t)moy_wasm_file_block(hold, (uint64_t)size),
                                    PSRAM_CAPS);
    if (!buf) {
        buf = heap_caps_malloc((size_t)size, PSRAM_CAPS);
    }
    if (!buf) {
        mp_stream_close(f);
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("no PSRAM for the module file"));
    }
    mp_uint_t got = mp_stream_rw(f, buf, (mp_uint_t)size, &e, MP_STREAM_RW_READ);
    mp_stream_close(f);
    if (e != 0 || got != (mp_uint_t)size) {
        heap_caps_free(buf);
        mp_raise_OSError(e ? e : MP_EIO);
    }
    *out = buf;
    *out_len = (uint32_t)size;
}

// The module's admission (moy_ota_admit_module, native/moy_net/moy_ota.h):
// NULL when the file may load, with *module_len set to the module's length
// without any trailer; otherwise the refusal. `consented` is the cart's
// consent as the caller read it (app_perms.admits_unsigned); its provenance
// key is checked on the thread as every module's is.
static const char *admit(const uint8_t *file, uint32_t len, bool consented,
                         uint32_t *module_len)
{
    size_t n = len;
    const char *why = moy_ota_admit_module(file, len, MOY_WASM_SIG_SCHEME, KEY_CHIP,
                                           consented, NULL, 0, &n);
    *module_len = (uint32_t)n;
    return why;
}

static void read_file(run_t *r, mp_obj_t path)
{
    read_module(path, 0, &r->file, &r->file_len);
}

// start(path, export, args=(), loops=1, stack=None, psram_stack=None, pool=None,
//       allow_unsigned=False)
//
// Reads the module file and checks its signature, then runs `loops` passes of load / check / instantiate
// / call export(*args) / unload on a new thread, and returns at once. `stack`,
// `psram_stack` and `pool` override the board's settings and the pool's
// sizing rule for this run (a measurement, not a product knob: result()'s
// `pool_peak` under a pool bigger than the rule's is what the rule must
// hold). `allow_unsigned` lets a module with no signature load, as a cart's
// consent to `full_access` does. done() says when it ended, result()
// collects it; a module refused for its signature ends at once, with no
// thread.
static mp_obj_t mod_start(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args)
{
    enum { ARG_path, ARG_export, ARG_args, ARG_loops, ARG_stack, ARG_psram_stack, ARG_pool,
           ARG_allow_unsigned };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_path, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_export, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_args, MP_ARG_OBJ, {.u_obj = mp_const_empty_tuple} },
        { MP_QSTR_loops, MP_ARG_INT, {.u_int = 1} },
        { MP_QSTR_stack, MP_ARG_KW_ONLY | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_psram_stack, MP_ARG_KW_ONLY | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_pool, MP_ARG_KW_ONLY | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_allow_unsigned, MP_ARG_KW_ONLY | MP_ARG_BOOL, {.u_bool = false} },
    };
    mp_arg_val_t a[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, a);

    if (g_run == NULL) {
        g_run = heap_caps_calloc(1, sizeof(run_t), PSRAM_CAPS);
        if (g_run == NULL) {
            mp_raise_type(&mp_type_MemoryError);
        }
    }
    run_t *r = g_run;
    if (r->started) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("a run is live; collect it with result()"));
    }
    if (g_sessions != NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("a cart's session is live"));
    }
    if (!g_lock) {
        g_lock = xSemaphoreCreateMutexStatic(&g_lock_buf);
    }
    size_t argc;
    mp_obj_t *items;
    mp_obj_get_array(a[ARG_args].u_obj, &argc, &items);
    if (argc > MOY_WASM_MAX_ARGS) {
        mp_raise_ValueError(MP_ERROR_TEXT("too many arguments"));
    }
    size_t name_len;
    const char *name = mp_obj_str_get_data(a[ARG_export].u_obj, &name_len);
    if (name_len == 0 || name_len >= sizeof(r->export_name)) {
        mp_raise_ValueError(MP_ERROR_TEXT("bad export name"));
    }
    mp_int_t loops = a[ARG_loops].u_int;
    if (loops < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("loops must be at least 1"));
    }
    mp_int_t stack = a[ARG_stack].u_obj == mp_const_none
                         ? MOY_WASM_STACK_BYTES : mp_obj_get_int(a[ARG_stack].u_obj);
    if (stack < 4096 || stack > 256 * 1024) {
        mp_raise_ValueError(MP_ERROR_TEXT("stack must be 4KB..256KB"));
    }
    mp_int_t pool = a[ARG_pool].u_obj == mp_const_none ? 0 : mp_obj_get_int(a[ARG_pool].u_obj);
    if (pool != 0 && (pool < 16 * 1024 || pool > 16 * 1024 * 1024)) {
        mp_raise_ValueError(MP_ERROR_TEXT("pool must be 16KB..16MB"));
    }

    memset(r, 0, sizeof(*r));
    memcpy(r->export_name, name, name_len);
    r->argc = (uint32_t)argc;
    for (size_t i = 0; i < argc; i++) {
        r->argv[i] = (uint32_t)mp_obj_get_int_truncated(items[i]);
    }
    r->loops = (uint32_t)loops;
    r->stack_bytes = (uint32_t)stack;
    r->stack_psram = a[ARG_psram_stack].u_obj == mp_const_none
                         ? MOY_WASM_STACK_PSRAM : mp_obj_is_true(a[ARG_psram_stack].u_obj);
    r->pool_asked = (uint32_t)pool;
    read_file(r, a[ARG_path].u_obj);
    const char *why = admit(r->file, r->file_len, a[ARG_allow_unsigned].u_bool, &r->file_len);
    if (why) {
        fail(r, "refused", why);
        r->started = true;
        r->finished = true;
        return mp_const_none;
    }

    // The low-water mark covers the run's whole cost, its thread's stack and
    // control block included, so the monitor starts before the thread exists.
    heap_caps_monitor_local_minimum_free_size_start();
    r->sram_before = heap_caps_get_free_size(SRAM_CAPS);
    r->t_start = esp_timer_get_time();

    int pe = moy_wasm_spawn(&r->tid, run_thread, r, r->stack_bytes, r->stack_psram,
                            MOY_WASM_CORE, MOY_WASM_PRIO);
    if (pe != 0) {
        heap_caps_monitor_local_minimum_free_size_stop();
        release(r);
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("could not start the run's thread"));
    }
    r->started = true;
    r->threaded = true;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mod_start_obj, 2, mod_start);

static mp_obj_t mod_done(void)
{
    return mp_obj_new_bool(g_run == NULL || !g_run->started || g_run->finished);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_done_obj, mod_done);

// terminate() -> whether an instance was live to be told. wasm_runtime_terminate
// raises "terminated by user" in the instance; README.md says when AOT code
// notices it.
static mp_obj_t mod_terminate(void)
{
    run_t *r = g_run;
    bool told = false;
    if (r != NULL && r->started && g_lock) {
        xSemaphoreTake(g_lock, portMAX_DELAY);
        if (r->live) {
            wasm_runtime_terminate(r->live);
            told = true;
        }
        r->terminated = true;
        xSemaphoreGive(g_lock);
    }
    return mp_obj_new_bool(told);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_terminate_obj, mod_terminate);

// -- the session (moy_wasm_session.h) ----------------------------------------

enum { SESS_CALL = 1, SESS_CLOSE = 2 };

#define LANES_MAX (MOY_WASM_ITEM_LANES > 0 ? MOY_WASM_ITEM_LANES : 1)

// A lane: its thread, the work it was given and the two handshakes.
typedef struct {
    bool up;
    pthread_t tid;
    SemaphoreHandle_t go, done;
    void (*volatile fn)(void *);
    void *volatile arg;
    volatile bool quit;
    volatile int64_t t_go;          // when the work was handed over
} lane_t;

// What the lanes did since lanes() last read it: work handed over, work
// taken back unstarted, and the microseconds from handing it over to the
// lane starting it and from starting to returning.
typedef struct {
    uint32_t jobs, taken_back;
    uint64_t wait_us, run_us;
} lane_stats_t;
static lane_stats_t g_lane_stats[LANES_MAX + 1];

typedef struct sess {
    // the MicroPython side's
    bool live;
    bool interp;                    // running main.wasm on the interpreter --
                                     // no key, no signature, no par lanes
    pthread_t tid;
    const moy_wasm_ops *ops;
    char want_sha[65];
    // handed to the thread, freed by it once the load is done
    uint8_t *file;
    uint32_t file_len;
    // the handshake: `go` wakes the thread for a request, `back` wakes the
    // task when the request is done OR when the thread asks for the VM, and
    // `vm_done` tells the thread its VM request ran
    SemaphoreHandle_t go, back, vm_done;
    volatile int op;
    volatile int what;
    volatile float dt;
    volatile int rc;
    char err[ERR_MAX];
    void (*volatile vm_fn)(void *);
    void *volatile vm_arg;
    volatile bool waiting;          // the task is in sess_wait and can serve
    TaskHandle_t thread;            // the session's task, once it runs
    uint8_t *stack_lo;              // its stack's low end when the boundary was set
    wasm_module_inst_t volatile inst;   // what moy_wasm_session_terminate stops
    lane_t lane[LANES_MAX + 1];     // [1..]: the par lanes, started on demand
    // its share of the runtime: the pool its load, instance and exec envs
    // are allocated from (rt_malloc), and whether the runtime is being made
    uint8_t *pool;
    size_t pool_size;
    mem_allocator_t alloc;
    volatile bool rt_init;
    struct sess *next;              // the next open session
} sess_t;

// The session the calls act on (moy_wasm_session_use), or NULL; g_sessions
// above lists every open one. Allocated per session, from PSRAM, so an idle
// desk carries two pointers and not the structs.
static sess_t *g_sess;

// -- the runtime's allocator ---------------------------------------------------
//
// One runtime serves every open session (WAMR counts its inits): it is made
// by the first session's thread and goes with the last's. Each session keeps
// its own pool, sized by moy_wasm_footprint.h's rule for its module, and what
// the runtime allocates for a session -- its module, instance, exec envs and
// import table registration -- comes from that session's pool, freed with
// it. The runtime's own structures, made while the first session brings it
// up, come from PSRAM's heap, so they outlive the session that made them. A
// free goes to the pool the block lies in. The session being served is the
// current one: the VM's task calls one session at a time.

static sess_t *pool_of(const void *p)
{
    for (sess_t *s = g_sessions; s != NULL; s = s->next) {
        if (s->pool != NULL && (const uint8_t *)p >= s->pool
            && (const uint8_t *)p < s->pool + s->pool_size) {
            return s;
        }
    }
    return NULL;
}

// The session whose thread this is, else the current one: a command's
// session runs its module while the VM's task serves another's (a WASI
// command, below), so what its thread allocates or asks of the VM is its own.
static sess_t *sess_here(void)
{
    TaskHandle_t me = xTaskGetCurrentTaskHandle();
    for (sess_t *s = g_sessions; s != NULL; s = s->next) {
        if (s->thread == me) {
            return s;
        }
    }
    return g_sess;
}

static void *rt_malloc(unsigned int n)
{
    sess_t *s = sess_here();
    if (s != NULL && s->alloc != NULL && !s->rt_init) {
        return mem_allocator_malloc(s->alloc, n);
    }
    return heap_caps_malloc(n, PSRAM_CAPS);
}

static void *rt_realloc(void *p, unsigned int n)
{
    if (p == NULL) {
        return rt_malloc(n);
    }
    sess_t *s = pool_of(p);
    return s != NULL ? mem_allocator_realloc(s->alloc, p, n) : heap_caps_realloc(p, n, PSRAM_CAPS);
}

static void rt_free(void *p)
{
    if (p == NULL) {
        return;
    }
    sess_t *s = pool_of(p);
    if (s != NULL) {
        mem_allocator_free(s->alloc, p);
    } else {
        heap_caps_free(p);
    }
}

// On the task: wait for the thread's answer, serving every VM request that
// arrives first. The caller set `waiting` before it woke the thread, so a
// request the thread makes at once is never refused as unserved.
static void sess_wait(sess_t *s)
{
    for (;;) {
        xSemaphoreTake(s->back, portMAX_DELAY);
        void (*fn)(void *) = s->vm_fn;
        if (fn == NULL) {
            break;
        }
        s->vm_fn = NULL;
        fn(s->vm_arg);
        xSemaphoreGive(s->vm_done);
    }
    s->waiting = false;
}

int moy_wasm_on_vm(void (*fn)(void *arg), void *arg)
{
    sess_t *s = sess_here();
    if (!s || !s->live || !s->waiting || xTaskGetCurrentTaskHandle() != s->thread) {
        return -1;
    }
    s->vm_arg = arg;
    s->vm_fn = fn;
    xSemaphoreGive(s->back);
    xSemaphoreTake(s->vm_done, portMAX_DELAY);
    return 0;
}

// -- the lanes ----------------------------------------------------------------

static void *lane_thread(void *arg)
{
    lane_t *l = arg;
    lane_stats_t *st = &g_lane_stats[l - g_sess->lane];
    wasm_runtime_init_thread_env();
    for (;;) {
        xSemaphoreTake(l->go, portMAX_DELAY);
        if (l->quit) {
            break;
        }
        int64_t t0 = esp_timer_get_time();
        l->fn(l->arg);
        int64_t t1 = esp_timer_get_time();
        st->wait_us += (uint64_t)(t0 - l->t_go);
        st->run_us += (uint64_t)(t1 - t0);
        xSemaphoreGive(l->done);
    }
    wasm_runtime_destroy_thread_env();
    return NULL;
}

int moy_wasm_session_lanes(void)
{
    // An interpreted session declines lanes the same way a one-core board
    // does (README.md): no sibling instance trick is attempted over the
    // interpreter's module, so a cart's par items run in declaration order
    // on the calling core instead (native/moy_wasm/README.md, "A cart
    // survives its firmware", 2026-09-30).
    if (g_sess && g_sess->interp) {
        return 0;
    }
    return MOY_WASM_ITEM_LANES;
}

int moy_wasm_session_lane_go(int lane, void (*fn)(void *arg), void *arg)
{
    sess_t *s = g_sess;
    if (!s || lane < 1 || lane > MOY_WASM_ITEM_LANES) {
        return 1;
    }
    lane_t *l = &s->lane[lane];
    if (!l->up) {
        if (!l->go) {
            l->go = xSemaphoreCreateBinary();
            l->done = xSemaphoreCreateBinary();
        }
        if (!l->go || !l->done) {
            return 1;
        }
        // The lanes take the cores in turn, skipping the session's own.
        int core = (MOY_WASM_CORE + lane) % portNUM_PROCESSORS;
        if (moy_wasm_spawn(&l->tid, lane_thread, l, MOY_WASM_STACK_BYTES,
                           MOY_WASM_STACK_PSRAM, core, MOY_WASM_LANE_PRIO) != 0) {
            return 1;
        }
        l->up = true;
    }
    l->fn = fn;
    l->arg = arg;
    l->t_go = esp_timer_get_time();
    g_lane_stats[lane].jobs++;
    xSemaphoreGive(l->go);
    return 0;
}

void moy_wasm_session_lane_wait(int lane)
{
    sess_t *s = g_sess;
    if (s && lane >= 1 && lane <= MOY_WASM_ITEM_LANES && s->lane[lane].up) {
        // Work the lane has not picked up yet is taken back rather than
        // waited for: the binding waits only once every item is taken, so
        // the lane would find none, and its core may be busy for a while
        // with the tasks above it.
        if (xSemaphoreTake(s->lane[lane].go, 0) == pdTRUE) {
            g_lane_stats[lane].taken_back++;
            return;
        }
        xSemaphoreTake(s->lane[lane].done, portMAX_DELAY);
    }
}

// Join every lane's thread; from the session's thread, before the runtime
// goes.
static void lanes_stop(sess_t *s)
{
    for (int k = 1; k <= MOY_WASM_ITEM_LANES; k++) {
        lane_t *l = &s->lane[k];
        if (l->up) {
            l->quit = true;
            xSemaphoreGive(l->go);
            pthread_join(l->tid, NULL);
            l->up = false;
        }
        if (l->go) {
            vSemaphoreDelete(l->go);
            l->go = NULL;
        }
        if (l->done) {
            vSemaphoreDelete(l->done);
            l->done = NULL;
        }
    }
}

static int sess_fail(sess_t *s, const char *what, const char *detail)
{
    snprintf(s->err, sizeof(s->err), "%.40s%s%.110s", what, detail ? ": " : "",
             detail ? detail : "");
    return 1;
}

// A "native stack overflow" carries the numbers the check compared, from
// this thread's stack's low end at the open: where that end is now (the
// RISC-V port moves it up as it carves coprocessor save areas), the boundary
// the env holds and the one handed to it (MOY_WASM_STACK_GUARD), the least
// this thread has had free, the largest frame an AOT entry check asks for,
// and whether the env was last entered here.
static void stack_note(sess_t *s, wasm_exec_env_t env)
{
    if (env == NULL || strstr(s->err, "native stack overflow") == NULL) {
        return;
    }
    void *bound, *user;
    pthread_t by;
    uint32_t size_max;
    moy_wasm_env_stack(env, &bound, &user, &by, &size_max);
    uint8_t *lo = s->stack_lo;
    size_t n = strlen(s->err);
    snprintf(s->err + n, sizeof(s->err) - n,
             " [stack %p%+d bound %+d set %+d free %u frame %u %s]", (void *)lo,
             (int)(pxTaskGetStackStart(NULL) - lo), (int)((uint8_t *)bound - lo),
             (int)((uint8_t *)user - lo),
             (unsigned)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)),
             (unsigned)size_max, pthread_equal(by, pthread_self()) ? "here" : "elsewhere");
    printf("moy_wasm: %s\n", s->err);
}

static void *sess_thread(void *arg)
{
    sess_t *s = arg;
    const moy_wasm_ops *ops = s->ops;
    char err[ERR_MAX];
    int rc = 0;
    bool up = false;
    wasm_module_t module = NULL;
    wasm_module_inst_t inst = NULL;
    wasm_exec_env_t env = NULL;
    s->thread = xTaskGetCurrentTaskHandle();

    // The interpreter's own, smaller rule when there is no AOT module for
    // this console (moy_wasm_footprint.h's "The interpreter tier"): the AOT
    // rule above is sized for relocations and symbol tables a main.wasm
    // session never builds.
    size_t pool_size = s->interp ? (size_t)moy_wasm_interp_pool_bytes(s->file_len)
                                 : pool_bytes(s->file_len);
    uint8_t *pool = heap_caps_malloc(pool_size, PSRAM_CAPS);
    if (!pool) {
        rc = sess_fail(s, OUT_OF_MEMORY, "no PSRAM for the runtime pool");
        goto opened;
    }
    s->alloc = mem_allocator_create(pool, (uint32_t)pool_size);
    if (s->alloc == NULL) {
        rc = sess_fail(s, "runtime init", "the pool would not take an allocator");
        goto opened;
    }
    s->pool_size = pool_size;
    s->pool = pool;
    RuntimeInitArgs init;
    memset(&init, 0, sizeof(init));
    init.mem_alloc_type = Alloc_With_Allocator;
    init.mem_alloc_option.allocator.malloc_func = (void *)rt_malloc;
    init.mem_alloc_option.allocator.realloc_func = (void *)rt_realloc;
    init.mem_alloc_option.allocator.free_func = (void *)rt_free;
    s->rt_init = true;
    bool inited = wasm_runtime_full_init(&init);
    s->rt_init = false;
    if (!inited) {
        rc = sess_fail(s, "runtime init", NULL);
        goto opened;
    }
    up = true;
    err[0] = 0;
    if (ops->runtime_up && ops->runtime_up(ops->user, err, sizeof(err))) {
        rc = sess_fail(s, "import table", err);
        goto opened;
    }
    LoadArgs la;
    memset(&la, 0, sizeof(la));
    la.name = "";
    la.wasm_binary_freeable = true;
    module = wasm_runtime_load_ex(s->file, s->file_len, &la, err, sizeof(err));
    if (!module) {
        rc = sess_fail(s, alloc_failed(err) ? OUT_OF_MEMORY : "load", err);
        goto opened;
    }
    // An interpreted session is main.wasm itself: no moybyte.key section, no
    // signature, nothing to check -- its sandbox is WAMR's bytecode
    // validation, not provenance (moy_wasm_key.h's "WHY A FORMAT VERSION").
    if (!s->interp) {
        run_t scratch;
        memset(&scratch, 0, sizeof(scratch));
        if (!check_key(&scratch, module)) {
            memcpy(s->err, scratch.err, sizeof(s->err));
            rc = 1;
            goto opened;
        }
        if (s->want_sha[0] && memcmp(scratch.wasm_hash, s->want_sha, 64) != 0) {
            rc = sess_fail(s, "refused", "the module was compiled from another main.wasm");
            goto opened;
        }
    }
    // A freeable load copies what the module keeps, its data segments
    // included, so the file goes back now -- true for an AOT module (the
    // fork's AOT loader, README.md's "A cart's module file is gone before
    // its memory is allocated"), but NOT for an interpreted one: WAMR's
    // classic interpreter walks function bodies from the loaded module's
    // OWN buffer rather than compiling them out of it, so freeing it early
    // left the interpreter executing out of freed (and soon reused) PSRAM,
    // which read back as garbage bytecode -- an early, spurious
    // "Exception: unreachable" on every interpreted run. An interpreted
    // session keeps the file until teardown instead.
    if (!s->interp) {
        heap_caps_free(s->file);
        s->file = NULL;
    }
    err[0] = 0;
    if (ops->loaded && ops->loaded(ops->user, module, err, sizeof(err))) {
        rc = sess_fail(s, "refused", err);
        goto opened;
    }
    inst = wasm_runtime_instantiate(module, MOY_WASM_EXEC_STACK, 0, err, sizeof(err));
    if (!inst) {
        rc = sess_fail(s, alloc_failed(err) ? OUT_OF_MEMORY : "instantiate", err);
        goto opened;
    }
    s->inst = inst;
    env = wasm_runtime_create_exec_env(inst, MOY_WASM_EXEC_STACK);
    if (!env) {
        rc = sess_fail(s, OUT_OF_MEMORY, "no pool for the exec env");
        goto opened;
    }
    s->stack_lo = pxTaskGetStackStart(NULL);
    wasm_runtime_set_native_stack_boundary(env, s->stack_lo + MOY_WASM_STACK_GUARD);
    err[0] = 0;
    if (ops->bound && ops->bound(ops->user, env, err, sizeof(err))) {
        rc = sess_fail(s, "bind", err);
        goto opened;
    }

opened:
    s->rc = rc;
    xSemaphoreGive(s->back);               // the open is answered
    for (;;) {
        xSemaphoreTake(s->go, portMAX_DELAY);
        if (s->op == SESS_CLOSE) {
            break;
        }
        // A failed open is closed by its opener and never called.
        s->err[0] = 0;
        s->rc = ops->call(ops->user, s->what, s->dt, s->err, sizeof(s->err));
        if (s->rc != 0) {
            stack_note(s, env);
        }
        xSemaphoreGive(s->back);
    }
    if (up && ops->unbound) {
        ops->unbound(ops->user);
    }
    lanes_stop(s);
    if (env) {
        wasm_runtime_destroy_exec_env(env);
    }
    s->inst = NULL;
    if (inst) {
        wasm_runtime_deinstantiate(inst);
    }
    if (module) {
        wasm_runtime_unload(module);
    }
    if (up) {
        wasm_runtime_destroy();             // the last session's takes it down
    }
    s->pool = NULL;
    if (s->alloc != NULL) {
        mem_allocator_destroy(s->alloc);
        s->alloc = NULL;
    }
    if (pool) {
        heap_caps_free(pool);
    }
    xSemaphoreGive(s->back);               // closed
    return NULL;
}

void moy_wasm_session_terminate(void)
{
    sess_t *s = g_sess;
    if (s && s->live && s->inst) {
        wasm_runtime_terminate(s->inst);
    }
}

int moy_wasm_session_live(void)
{
    return g_sess != NULL && g_sess->live;
}

static void sess_free(sess_t *s)
{
    if (s->file) {
        heap_caps_free(s->file);
    }
    if (s->go) {
        vSemaphoreDelete(s->go);
    }
    if (s->back) {
        vSemaphoreDelete(s->back);
    }
    if (s->vm_done) {
        vSemaphoreDelete(s->vm_done);
    }
    for (sess_t **p = &g_sessions; *p != NULL; p = &(*p)->next) {
        if (*p == s) {
            *p = s->next;
            break;
        }
    }
    if (g_sess == s) {
        g_sess = NULL;
    }
    heap_caps_free(s);
}

void *moy_wasm_session_current(void)
{
    return g_sess;
}

void moy_wasm_session_use(void *s)
{
    g_sess = s;
}

void moy_wasm_session_close(void)
{
    sess_t *s = g_sess;
    if (!s || !s->live) {
        return;
    }
    s->op = SESS_CLOSE;
    s->waiting = true;
    xSemaphoreGive(s->go);
    sess_wait(s);
    MP_THREAD_GIL_EXIT();
    pthread_join(s->tid, NULL);
    MP_THREAD_GIL_ENTER();
    sess_free(s);
}

// moy_wasm_session_open with the session's thread on `core` at `prio`.
static int sess_open(const char *path, const char *want_sha, uint32_t memory,
                     int allow_unsigned, int interp, const moy_wasm_ops *ops,
                     int core, int prio, char *err, size_t errlen)
{
    if (g_run != NULL && g_run->started) {
        snprintf(err, errlen, "a wasm run is already live");
        return 1;
    }
    sess_t *s = heap_caps_calloc(1, sizeof(sess_t), PSRAM_CAPS);
    if (!s) {
        snprintf(err, errlen, OUT_OF_MEMORY ": no PSRAM for the cart's session");
        return 1;
    }
    s->next = g_sessions;
    g_sessions = s;
    s->go = xSemaphoreCreateBinary();
    s->back = xSemaphoreCreateBinary();
    s->vm_done = xSemaphoreCreateBinary();
    if (!s->go || !s->back || !s->vm_done) {
        sess_free(s);
        snprintf(err, errlen, OUT_OF_MEMORY ": no memory for the cart's session");
        return 1;
    }
    s->ops = ops;
    s->interp = interp != 0;
    if (want_sha && strlen(want_sha) == 64) {
        memcpy(s->want_sha, want_sha, 65);
    }
    g_sess = s;
    if (moy_loop_vm != NULL && !moy_loop_vm()) {
        int e = read_module_c(path, memory, &s->file, &s->file_len);
        if (e != 0) {
            sess_free(s);
            snprintf(err, errlen, "the module file would not read (errno %d)", e);
            return 1;
        }
    } else {
        nlr_buf_t nlr;
        if (nlr_push(&nlr) == 0) {
            read_module(mp_obj_new_str(path, strlen(path)), memory, &s->file, &s->file_len);
            nlr_pop();
        } else {
            sess_free(s);
            nlr_jump(nlr.ret_val);
        }
    }
    // main.wasm carries no signature and needs none: the interpreter is the
    // sandbox (moy_wasm_key.h). An AOT module is native code, and is admitted.
    if (!s->interp) {
        const char *why = admit(s->file, s->file_len, allow_unsigned != 0, &s->file_len);
        if (why) {
            snprintf(err, errlen, "refused: %s", why);
            sess_free(s);
            return 1;
        }
    }
    s->waiting = true;
    int pe = moy_wasm_spawn(&s->tid, sess_thread, s, MOY_WASM_STACK_BYTES,
                            MOY_WASM_STACK_PSRAM, core, prio);
    if (pe != 0) {
        sess_free(s);
        snprintf(err, errlen, "could not start the cart's thread");
        return 1;
    }
    s->live = true;
    sess_wait(s);
    if (s->rc != 0) {
        snprintf(err, errlen, "%s", s->err);
        moy_wasm_session_close();
        return 1;
    }
    return 0;
}

int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, int interp, const moy_wasm_ops *ops,
                          char *err, size_t errlen)
{
    return sess_open(path, want_sha, memory, allow_unsigned, interp, ops, MOY_WASM_CORE,
                     MOY_WASM_PRIO, err, errlen);
}

int moy_wasm_session_call(int what, float dt, char *err, size_t errlen)
{
    sess_t *s = g_sess;
    if (!s || !s->live) {
        snprintf(err, errlen, "no cart session");
        return 1;
    }
    s->op = SESS_CALL;
    s->what = what;
    s->dt = dt;
    s->waiting = true;
    xSemaphoreGive(s->go);
    sess_wait(s);
    if (s->rc != 0) {
        snprintf(err, errlen, "%s", s->err);
    }
    return s->rc;
}

static void put(mp_obj_t d, qstr k, mp_obj_t v)
{
    mp_obj_dict_store(d, MP_OBJ_NEW_QSTR(k), v);
}

#define INT(v) mp_obj_new_int((mp_int_t)(v))

// result() -> dict, after waiting for the run to end (the VM lock is released
// while it waits). None when nothing was started.
static mp_obj_t mod_result(void)
{
    run_t *r = g_run;
    if (r == NULL || !r->started) {
        return mp_const_none;
    }
    size_t sram_min = r->sram_before;
    size_t psram_min = 0;
    if (r->threaded) {
        MP_THREAD_GIL_EXIT();
        pthread_join(r->tid, NULL);
        MP_THREAD_GIL_ENTER();
        sram_min = heap_caps_get_minimum_free_size(SRAM_CAPS);
        psram_min = heap_caps_get_minimum_free_size(PSRAM_CAPS);
        heap_caps_monitor_local_minimum_free_size_stop();
    }
    r->started = false;
    uint32_t module_bytes = r->file_len;
    release(r);

    mp_obj_t d = mp_obj_new_dict(24);
    put(d, MP_QSTR_ok, mp_obj_new_bool(r->ok));
    put(d, MP_QSTR_error, r->ok ? mp_const_none : mp_obj_new_str(r->err, strlen(r->err)));
    put(d, MP_QSTR_value, r->has_value ? mp_obj_new_int_from_uint(r->value) : mp_const_none);
    put(d, MP_QSTR_mismatches, INT(r->mismatches));
    put(d, MP_QSTR_loops, INT(r->loops_done));
    put(d, MP_QSTR_wasm, r->wasm_hash[0] ? mp_obj_new_str(r->wasm_hash, 64) : mp_const_none);
    put(d, MP_QSTR_module_bytes, INT(module_bytes));
    put(d, MP_QSTR_load_us, INT(r->load_us));
    put(d, MP_QSTR_load_us_max, INT(r->load_us_max));
    put(d, MP_QSTR_inst_us, INT(r->inst_us));
    put(d, MP_QSTR_call_us, INT(r->call_us));
    put(d, MP_QSTR_call_us_min, INT(r->call_us_min));
    put(d, MP_QSTR_call_us_max, INT(r->call_us_max));
    put(d, MP_QSTR_run_us, INT(r->t_end - r->t_start));
    put(d, MP_QSTR_stack, INT(r->stack_bytes));
    put(d, MP_QSTR_stack_psram, mp_obj_new_bool(r->stack_psram));
    put(d, MP_QSTR_stack_used, INT(r->stack_bytes - r->stack_free_min));
    put(d, MP_QSTR_pool, INT(r->pool));
    put(d, MP_QSTR_pool_peak, INT(r->pool_peak));
    put(d, MP_QSTR_sram_before, INT(r->sram_before));
    put(d, MP_QSTR_sram_min, INT(sram_min));
    put(d, MP_QSTR_psram_min, INT(psram_min));
    put(d, MP_QSTR_terminated, mp_obj_new_bool(r->terminated));
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_result_obj, mod_result);

// footprint(memory, module_bytes) -> (total, block): what loading a module
// file of `module_bytes` for a cart declaring `memory` bytes of linear memory
// takes from PSRAM at its peak, and the largest single free block it needs
// (moy_wasm_footprint.h). mem()'s psram_free and psram_largest are what a
// board can give it.
static mp_obj_t mod_footprint(mp_obj_t memory_in, mp_obj_t module_in)
{
    mp_int_t memory = mp_obj_get_int(memory_in);
    mp_int_t module = mp_obj_get_int(module_in);
    if (memory < 0 || module < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("sizes must not be negative"));
    }
    uint64_t total, block;
    moy_wasm_footprint((uint64_t)memory, (uint64_t)module, &total, &block);
    mp_obj_t t[2] = { mp_obj_new_int_from_ull(total), mp_obj_new_int_from_ull(block) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_footprint_obj, mod_footprint);

// interp_footprint(memory, module_bytes) -> (total, block): footprint()'s
// twin for a session with no AOT module for this console (moy_wasm_session's
// `interp`): the module file is never freed back to the linear memory, so
// the two are separate, simultaneous allocations over the interpreter's own
// pool (moy_wasm_footprint.h's `moy_wasm_interp_footprint`).
static mp_obj_t mod_interp_footprint(mp_obj_t memory_in, mp_obj_t module_in)
{
    mp_int_t memory = mp_obj_get_int(memory_in);
    mp_int_t module = mp_obj_get_int(module_in);
    if (memory < 0 || module < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("sizes must not be negative"));
    }
    uint64_t total, block;
    moy_wasm_interp_footprint((uint64_t)memory, (uint64_t)module, &total, &block);
    mp_obj_t t[2] = { mp_obj_new_int_from_ull(total), mp_obj_new_int_from_ull(block) };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_interp_footprint_obj, mod_interp_footprint);

// mem() -> (internal_free, internal_largest, internal_min_since_boot,
//           psram_free, psram_largest)
static mp_obj_t mod_mem(void)
{
    mp_obj_t t[5] = {
        INT(heap_caps_get_free_size(SRAM_CAPS)),
        INT(heap_caps_get_largest_free_block(SRAM_CAPS)),
        INT(heap_caps_get_minimum_free_size(SRAM_CAPS)),
        INT(heap_caps_get_free_size(PSRAM_CAPS)),
        INT(heap_caps_get_largest_free_block(PSRAM_CAPS)),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mem_obj, mod_mem);

// lanes() -> [(jobs, taken_back, wait_us, run_us)] per lane since the last
// call, which resets them: how often a cart's par items were handed to each
// lane, how often the lane had not started when every item was already
// taken, and the time from handing over to starting, and from starting to
// finishing.
static mp_obj_t mod_lanes(void)
{
    mp_obj_t l = mp_obj_new_list(0, NULL);
    for (int k = 1; k <= MOY_WASM_ITEM_LANES; k++) {
        lane_stats_t *st = &g_lane_stats[k];
        mp_obj_t t[4] = { INT(st->jobs), INT(st->taken_back),
                          mp_obj_new_int_from_ull(st->wait_us),
                          mp_obj_new_int_from_ull(st->run_us) };
        mp_obj_list_append(l, mp_obj_new_tuple(4, t));
        memset(st, 0, sizeof(*st));
    }
    return l;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_lanes_obj, mod_lanes);

// tlanes() -> [(name, ran, calls, frames, max_us, struck, why)] per timed lane
// of the last session (moy_wasm_session.h), [] on a board without them.
static mp_obj_t mod_tlanes(void)
{
    static const qstr names[MOY_WASM_TLANES] = { MP_QSTR_moy_audio, MP_QSTR_moy_timer };
    moy_wasm_tlane_stats_t st[MOY_WASM_TLANES];
    int n = moy_wasm_tlanes_stats(st);
    mp_obj_t l = mp_obj_new_list(0, NULL);
    for (int k = 0; k < n; k++) {
        mp_obj_t t[7] = { MP_OBJ_NEW_QSTR(names[k]), mp_obj_new_bool(st[k].ran),
                          INT(st[k].calls), INT(st[k].frames), INT(st[k].max_us),
                          mp_obj_new_bool(st[k].struck),
                          mp_obj_new_str(st[k].why, strlen(st[k].why)) };
        mp_obj_list_append(l, mp_obj_new_tuple(7, t));
    }
    return l;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_tlanes_obj, mod_tlanes);

static MP_DEFINE_STR_OBJ(mod_key_obj, "format " MOY_WASM_FORMAT_VERSION "\n" KEY_TARGET);
static MP_DEFINE_STR_OBJ(mod_format_obj, MOY_WASM_FORMAT_VERSION);
static MP_DEFINE_STR_OBJ(mod_fork_obj, MOY_WASM_FORK_COMMIT);
static MP_DEFINE_STR_OBJ(mod_chip_obj, KEY_CHIP);

// -- a WASI command (moy_wasi.h) ----------------------------------------------------
//
// A command runs in steps (moy_wasi.h's "IN STEPS"), so the console's frame
// goes on while it runs. command_start(path, want_sha, interp,
// allow_unsigned, argv, roots, bg_pid, budget_ms) -> (status, err) runs the
// module `path` as a command in the shell session the door's crossing runs
// in (moy_sh_running), a session of its own opened for it and its call into
// `_start` begun on the session's thread, whichever session was current made
// current again: (0, None) with it running, or (-3, the reason it did not
// run). command_step(max_ms) serves its calls for `max_ms` at most, the
// natives' bodies hopping to this task (moy_wasm_on_vm): (-100, None) while
// it goes on, else its end; command_stop() ends it as Ctrl-C does. Its end
// is (the exit status, None) or (-1, the trap), its session closed. `roots`
// is NUL-separated. One command at a time.

typedef struct {
    moy_wasi_t *w;
    NativeSymbol *store;
    wasm_exec_env_t env;
    moy_wasm_ops ops;               // the session's, read while it lives
    sess_t *sess;
    volatile bool returned;         // `_start` has returned on the session's thread
    int status;
    char err[ERR_MAX];
} cmd_t;

static cmd_t *g_cmd;

static int cmd_up(void *user, char *err, size_t errlen)
{
    cmd_t *c = user;
    uint32_t term = 0, n = moy_wasi_count(&term);
    c->store = wasm_runtime_malloc((n + term) * sizeof(NativeSymbol));
    if (c->store == NULL || moy_wasi_register(c->store) != 0) {
        snprintf(err, errlen, OUT_OF_MEMORY ": the WASI table would not register");
        return 1;
    }
    return 0;
}

static int cmd_loaded(void *user, moy_wasm_module module, char *err, size_t errlen)
{
    (void)user;
    return moy_wasi_check(module, err, errlen);
}

static int cmd_bound(void *user, moy_wasm_env env, char *err, size_t errlen)
{
    cmd_t *c = user;
    (void)err;
    (void)errlen;
    c->env = env;
    return 0;
}

static int cmd_call(void *user, int what, float dt, char *err, size_t errlen)
{
    cmd_t *c = user;
    (void)what;
    (void)dt;
    (void)err;
    (void)errlen;
    c->status = moy_wasi_main(c->w, c->env, c->err, sizeof(c->err));
    return 0;
}

static void cmd_unbound(void *user)
{
    cmd_t *c = user;
    if (c->store != NULL) {
        moy_wasi_unregister(c->store);
        wasm_runtime_free(c->store);
        c->store = NULL;
    }
    c->env = NULL;
}

// The runner (moy_wasi_runner_t): the session's handshake, as sess_wait
// serves it, a wait at a time.
static bool cmd_done(void *job)
{
    return ((cmd_t *)job)->returned;
}

static void cmd_serve(void *job, int64_t us)
{
    cmd_t *c = job;
    sess_t *s = c->sess;
    TickType_t t = (TickType_t)((us + 999) / 1000 / portTICK_PERIOD_MS + 1);
    if (xSemaphoreTake(s->back, t) == pdTRUE) {
        void (*fn)(void *) = s->vm_fn;
        if (fn == NULL) {
            s->waiting = false;
            c->returned = true;
        } else {
            s->vm_fn = NULL;
            fn(s->vm_arg);
            xSemaphoreGive(s->vm_done);
        }
    }
    if (esp_task_wdt_status(NULL) == ESP_OK) {
        esp_task_wdt_reset();
    }
}

static void cmd_terminate(void *job)
{
    sess_t *s = ((cmd_t *)job)->sess;
    if (s->inst) {
        wasm_runtime_terminate(s->inst);
    }
}

static const moy_wasi_runner_t cmd_runner = { cmd_done, cmd_serve, cmd_terminate };

static mp_obj_t cmd_answer(int status, const char *err)
{
    mp_obj_t out[2] = {
        mp_obj_new_int(status),
        status < 0 && status != MOY_WASI_RUNNING ? mp_obj_new_str(err, strlen(err))
                                                 : mp_const_none,
    };
    return mp_obj_new_tuple(2, out);
}

// The command's end, `_start` returned: its session closed, the session
// current before made current again, its record freed. Its answer.
static mp_obj_t cmd_end(void)
{
    cmd_t *c = g_cmd;
    void *was = moy_wasm_session_current();
    if (c->sess != NULL) {
        moy_wasm_session_use(c->sess);
        moy_wasm_session_close();
        moy_wasm_session_use(was == c->sess ? NULL : was);
    }
    int code = moy_wasi_status(c->w, c->status);
    int status = code == MOY_WASI_TRAPPED ? -1 : code < 0 ? -3 : code;
    char err[ERR_MAX];
    snprintf(err, sizeof(err), "%s", c->err);
    g_cmd = NULL;
    moy_wasi_free(c->w);
    heap_caps_free(c);
    return cmd_answer(status, err);
}

static mp_obj_t mod_command_start(size_t n_args, const mp_obj_t *args)
{
    const char *path = mp_obj_str_get_str(args[0]);
    const char *sha = args[1] == mp_const_none ? NULL : mp_obj_str_get_str(args[1]);
    int interp = mp_obj_is_true(args[2]);
    int unsigned_ok = mp_obj_is_true(args[3]);
    size_t argc;
    mp_obj_t *items;
    mp_obj_get_array(args[4], &argc, &items);
    size_t roots_n = 0;
    const char *roots = args[5] == mp_const_none ? NULL : mp_obj_str_get_data(args[5], &roots_n);
    uint32_t bg_pid = (uint32_t)mp_obj_get_int(args[6]);
    uint32_t budget = (uint32_t)mp_obj_get_int(args[7]);
    (void)n_args;
    (void)roots_n;
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        return cmd_answer(-3, "no shell session is running");
    }
    if (g_cmd != NULL) {
        return cmd_answer(-3, "a command is running");
    }
    const char **argv = m_new(const char *, argc ? argc : 1);
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    cmd_t *c = heap_caps_calloc(1, sizeof(cmd_t), PSRAM_CAPS);
    if (c == NULL) {
        mp_raise_type(&mp_type_MemoryError);
    }
    c->status = -3;
    c->w = moy_wasi_new(s, (int)argc, argv, roots, bg_pid, budget, moy_wasm_on_vm);
    m_del(const char *, argv, argc ? argc : 1);
    if (c->w == NULL) {
        heap_caps_free(c);
        mp_raise_type(&mp_type_MemoryError);
    }
    c->ops = (moy_wasm_ops){
        .user = c, .runtime_up = cmd_up, .loaded = cmd_loaded, .bound = cmd_bound,
        .call = cmd_call, .unbound = cmd_unbound,
    };
    void *was = moy_wasm_session_current();
    g_cmd = c;
    char err[ERR_MAX];
    err[0] = 0;
    int opened = 1;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        opened = moy_wasm_session_open(path, sha, 0, unsigned_ok, interp, &c->ops, err,
                                       sizeof(err));
        nlr_pop();
    } else {
        g_cmd = NULL;
        moy_wasm_session_use(was);
        moy_wasi_free(c->w);
        heap_caps_free(c);
        nlr_jump(nlr.ret_val);
    }
    if (opened != 0) {
        g_cmd = NULL;
        moy_wasm_session_use(was);
        moy_wasi_free(c->w);
        heap_caps_free(c);
        return cmd_answer(-3, err);
    }
    sess_t *ss = moy_wasm_session_current();
    c->sess = ss;
    ss->op = SESS_CALL;
    ss->what = 0;
    ss->dt = 0.0f;
    ss->waiting = true;
    xSemaphoreGive(ss->go);
    moy_wasm_session_use(was);
    return cmd_answer(0, "");
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_command_start_obj, 8, 8, mod_command_start);

static mp_obj_t mod_command_step(mp_obj_t max_ms_in)
{
    cmd_t *c = g_cmd;
    if (c == NULL) {
        return cmd_answer(-3, "no command is running");
    }
    uint32_t max_us = (uint32_t)mp_obj_get_int(max_ms_in) * 1000u;
    if (moy_wasi_step(c->w, &cmd_runner, c, max_us) == MOY_WASI_RUNNING) {
        return cmd_answer(MOY_WASI_RUNNING, "");
    }
    return cmd_end();
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_command_step_obj, mod_command_step);

static mp_obj_t mod_command_stop(void)
{
    cmd_t *c = g_cmd;
    if (c == NULL) {
        return cmd_answer(-3, "no command is running");
    }
    moy_wasi_stop(c->w, &cmd_runner, c);
    return cmd_end();
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_command_stop_obj, mod_command_stop);

// -- WASI services (moy_wasi.h, A SERVICE) ---------------------------------------------
//
// The P4s: each service is a session of its own (a task, its pool, its
// module), its task on core 0 at MOY_WASM_SVC_PRIO, as a Lua service's
// (moycore_svc.h); its call into `_start` begun there and left running, its
// calls run in place on that task (no session, no hop), so the VM's task
// never serves it. The S3s run none, as they run no Lua service
// (moycore_svc.c's SVC_NONE): the functions are absent and the glue refuses.
//
// service_start(path, want_sha, interp, allow_unsigned, pid, id) -> None or
// why; service_stop(pid) -> its exit status, or -1 for none: its descriptor
// hung up, killed (137) past MOY_WASI_STOP_US; service_reap() -> [(pid,
// status)] of the ended ones, the watchdog (moy_wasi_watch) run over the
// live ones first; service_info(pid) -> (linear memory bytes, its wakes'
// time in us) or None once reaped. A service's session closes at its reap.
#if CONFIG_IDF_TARGET_ESP32P4
#define MOY_WASM_SVC 1
#endif

#ifdef MOY_WASM_SVC

#ifndef MOY_WASM_SVC_CORE
#define MOY_WASM_SVC_CORE 0
#endif
#ifndef MOY_WASM_SVC_PRIO
#define MOY_WASM_SVC_PRIO 2
#endif

typedef struct wsvc {
    struct wsvc *next;
    uint32_t pid;
    moy_wasi_t *w;
    NativeSymbol *store;
    wasm_exec_env_t env;
    moy_wasm_ops ops;               // the session's, read while it lives
    sess_t *sess;
    volatile bool returned;         // `_start` has returned on the session's thread
    bool stopped;                   // its stop answered for it: reaped unreported
    int main;
    char err[ERR_MAX];
} wsvc_t;

static wsvc_t *g_svcs;

static int svc_up(void *user, char *err, size_t errlen)
{
    wsvc_t *v = user;
    uint32_t term = 0, n = moy_wasi_count(&term);
    v->store = wasm_runtime_malloc((n + term) * sizeof(NativeSymbol));
    if (v->store == NULL || moy_wasi_register(v->store) != 0) {
        snprintf(err, errlen, OUT_OF_MEMORY ": the WASI table would not register");
        return 1;
    }
    return 0;
}

static int svc_bound(void *user, moy_wasm_env env, char *err, size_t errlen)
{
    (void)err;
    (void)errlen;
    ((wsvc_t *)user)->env = env;
    return 0;
}

static int svc_call(void *user, int what, float dt, char *err, size_t errlen)
{
    wsvc_t *v = user;
    (void)what;
    (void)dt;
    (void)err;
    (void)errlen;
    v->main = moy_wasi_main(v->w, v->env, v->err, sizeof(v->err));
    v->returned = true;
    return 0;
}

static void svc_unbound(void *user)
{
    wsvc_t *v = user;
    if (v->store != NULL) {
        moy_wasi_unregister(v->store);
        wasm_runtime_free(v->store);
        v->store = NULL;
    }
    v->env = NULL;
}

static wsvc_t *svc_of(uint32_t pid)
{
    for (wsvc_t *v = g_svcs; v != NULL; v = v->next) {
        if (v->pid == pid && !v->stopped) {
            return v;
        }
    }
    return NULL;
}

// Its exit status once `_start` has returned: the call's answer taken, its
// session closed (the current one kept), its record freed.
static int svc_end(wsvc_t *v)
{
    sess_t *ss = v->sess;
    xSemaphoreTake(ss->back, portMAX_DELAY);
    ss->waiting = false;
    void *was = moy_wasm_session_current();
    moy_wasm_session_use(ss);
    moy_wasm_session_close();
    moy_wasm_session_use(was == ss ? NULL : was);
    int code = moy_wasi_service_status(v->w, v->main, v->err);
    for (wsvc_t **p = &g_svcs; *p != NULL; p = &(*p)->next) {
        if (*p == v) {
            *p = v->next;
            break;
        }
    }
    moy_wasi_free(v->w);
    heap_caps_free(v);
    return code;
}

// Waits up to `us` for `_start` to return, the task's watchdog fed: whether it has.
static bool svc_wait(wsvc_t *v, int64_t us)
{
    int64_t end = esp_timer_get_time() + us;
    while (!v->returned && esp_timer_get_time() < end) {
        vTaskDelay(1);
        if (esp_task_wdt_status(NULL) == ESP_OK) {
            esp_task_wdt_reset();
        }
    }
    return v->returned;
}

static mp_obj_t mod_service_start(size_t n_args, const mp_obj_t *args)
{
    const char *path = mp_obj_str_get_str(args[0]);
    const char *sha = args[1] == mp_const_none ? NULL : mp_obj_str_get_str(args[1]);
    int interp = mp_obj_is_true(args[2]);
    int unsigned_ok = mp_obj_is_true(args[3]);
    uint32_t pid = (uint32_t)mp_obj_get_int(args[4]);
    const char *id = mp_obj_str_get_str(args[5]);
    (void)n_args;
    if (svc_of(pid) != NULL) {
        return mp_obj_new_str("a service runs as that process", 30);
    }
    wsvc_t *v = heap_caps_calloc(1, sizeof(wsvc_t), PSRAM_CAPS);
    if (v == NULL) {
        mp_raise_type(&mp_type_MemoryError);
    }
    v->pid = pid;
    v->w = moy_wasi_service(pid, id);
    if (v->w == NULL) {
        heap_caps_free(v);
        mp_raise_type(&mp_type_MemoryError);
    }
    v->ops = (moy_wasm_ops){
        .user = v, .runtime_up = svc_up, .loaded = cmd_loaded, .bound = svc_bound,
        .call = svc_call, .unbound = svc_unbound,
    };
    void *was = moy_wasm_session_current();
    char err[ERR_MAX];
    err[0] = 0;
    int opened = 1;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        opened = sess_open(path, sha, 0, unsigned_ok, interp, &v->ops, MOY_WASM_SVC_CORE,
                           MOY_WASM_SVC_PRIO, err, sizeof(err));
        nlr_pop();
    } else {
        moy_wasm_session_use(was);
        moy_wasi_free(v->w);
        heap_caps_free(v);
        nlr_jump(nlr.ret_val);
    }
    if (opened != 0) {
        moy_wasm_session_use(was);
        moy_wasi_free(v->w);
        heap_caps_free(v);
        return mp_obj_new_str(err, strlen(err));
    }
    sess_t *ss = moy_wasm_session_current();
    v->sess = ss;
    ss->op = SESS_CALL;
    ss->what = 0;
    ss->dt = 0.0f;
    ss->waiting = true;
    v->next = g_svcs;
    g_svcs = v;
    xSemaphoreGive(ss->go);
    moy_wasm_session_use(was);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_service_start_obj, 6, 6, mod_service_start);

static mp_obj_t mod_service_stop(mp_obj_t pid_in)
{
    wsvc_t *v = svc_of((uint32_t)mp_obj_get_int(pid_in));
    if (v == NULL) {
        return MP_OBJ_NEW_SMALL_INT(-1);
    }
    if (!v->returned) {
        moy_wasi_hangup(v->w);
        if (!svc_wait(v, MOY_WASI_STOP_US)) {
            moy_wasi_kill(v->w, MOY_PROC_KILLED);
            if (!svc_wait(v, 100000)) {
                v->stopped = true;
                return MP_OBJ_NEW_SMALL_INT(MOY_PROC_KILLED);
            }
        }
    }
    return MP_OBJ_NEW_SMALL_INT(svc_end(v));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_service_stop_obj, mod_service_stop);

static mp_obj_t mod_service_reap(void)
{
    mp_obj_t out = mp_obj_new_list(0, NULL);
    wsvc_t *next;
    for (wsvc_t *v = g_svcs; v != NULL; v = next) {
        next = v->next;
        if (!v->returned) {
            const char *why = v->stopped ? NULL : moy_wasi_watch(v->w);
            if (why != NULL) {
                char line[96];
                int k = snprintf(line, sizeof(line), "killed: %s", why);
                moy_log_write(MOY_LOG_ERR, v->pid, line, (size_t)k);
                moy_wasi_kill(v->w, MOY_PROC_KILLED);
            }
            continue;
        }
        bool stopped = v->stopped;
        uint32_t pid = v->pid;
        int code = svc_end(v);
        if (!stopped) {
            mp_obj_t row[2] = { mp_obj_new_int_from_uint(pid), mp_obj_new_int(code) };
            mp_obj_list_append(out, mp_obj_new_tuple(2, row));
        }
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_service_reap_obj, mod_service_reap);

static mp_obj_t mod_service_info(mp_obj_t pid_in)
{
    wsvc_t *v = svc_of((uint32_t)mp_obj_get_int(pid_in));
    if (v == NULL) {
        return mp_const_none;
    }
    uint32_t mapped, cost;
    moy_wasi_figures(v->w, &mapped, &cost);
    mp_obj_t row[2] = { mp_obj_new_int_from_uint(mapped), mp_obj_new_int_from_uint(cost) };
    return mp_obj_new_tuple(2, row);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_service_info_obj, mod_service_info);

#endif // MOY_WASM_SVC

static const mp_rom_obj_tuple_t mod_stack_obj = {
    {&mp_type_tuple}, 2,
    { MP_ROM_INT(MOY_WASM_STACK_BYTES), MOY_WASM_STACK_PSRAM ? MP_ROM_TRUE : MP_ROM_FALSE }
};

static const mp_rom_map_elem_t moy_wasm_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_wasm) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_done), MP_ROM_PTR(&mod_done_obj) },
    { MP_ROM_QSTR(MP_QSTR_result), MP_ROM_PTR(&mod_result_obj) },
    { MP_ROM_QSTR(MP_QSTR_terminate), MP_ROM_PTR(&mod_terminate_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&mod_mem_obj) },
    { MP_ROM_QSTR(MP_QSTR_lanes), MP_ROM_PTR(&mod_lanes_obj) },
    { MP_ROM_QSTR(MP_QSTR_tlanes), MP_ROM_PTR(&mod_tlanes_obj) },
    { MP_ROM_QSTR(MP_QSTR_footprint), MP_ROM_PTR(&mod_footprint_obj) },
    { MP_ROM_QSTR(MP_QSTR_interp_footprint), MP_ROM_PTR(&mod_interp_footprint_obj) },
    { MP_ROM_QSTR(MP_QSTR_command_start), MP_ROM_PTR(&mod_command_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_command_step), MP_ROM_PTR(&mod_command_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_command_stop), MP_ROM_PTR(&mod_command_stop_obj) },
#ifdef MOY_WASM_SVC
    { MP_ROM_QSTR(MP_QSTR_service_start), MP_ROM_PTR(&mod_service_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_service_stop), MP_ROM_PTR(&mod_service_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_service_reap), MP_ROM_PTR(&mod_service_reap_obj) },
    { MP_ROM_QSTR(MP_QSTR_service_info), MP_ROM_PTR(&mod_service_info_obj) },
#endif
    // The key tail this build wants (after the wasm line), the compiled-code
    // format version and the fork commit the engine was vendored from
    // (diagnostic only -- FORK plays no part in the key since 2026-09-30, ESP
    // 88), and the board's default run stack (bytes, in PSRAM).
    { MP_ROM_QSTR(MP_QSTR_KEY), MP_ROM_PTR(&mod_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_FORMAT), MP_ROM_PTR(&mod_format_obj) },
    { MP_ROM_QSTR(MP_QSTR_FORK), MP_ROM_PTR(&mod_fork_obj) },
    { MP_ROM_QSTR(MP_QSTR_STACK), MP_ROM_PTR(&mod_stack_obj) },
    // The runtime pool's base size; a module adds a share of its own. An
    // interpreted session (no module for this console) uses its own,
    // smaller base instead (moy_wasm_interp_pool_bytes).
    { MP_ROM_QSTR(MP_QSTR_POOL), MP_ROM_INT(MOY_WASM_POOL_BYTES) },
    { MP_ROM_QSTR(MP_QSTR_INTERP_POOL), MP_ROM_INT(MOY_WASM_INTERP_POOL_BYTES) },
    // The chip the key's block is for: the name a cart's compiled module
    // carries beside its main.wasm (tools/wasm_cart.py's aot_name).
    { MP_ROM_QSTR(MP_QSTR_CHIP), MP_ROM_PTR(&mod_chip_obj) },
};
static MP_DEFINE_CONST_DICT(moy_wasm_globals, moy_wasm_globals_table);

const mp_obj_module_t moy_wasm_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_wasm_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_wasm, moy_wasm_user_cmodule);
