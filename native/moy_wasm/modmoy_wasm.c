// moy_wasm: the WebAssembly cart tier's ENGINE (docs/wasm_tier_plan_2026-09.md,
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
// signature (moy_wasm_key.h) -- a module whose signature does not verify never
// reaches the runtime, and one with no signature reaches it only when the
// caller allows unsigned modules (the owner's Unknown sources setting) --
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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/idf_additions.h"   // pxTaskGetStackStart

#include "mbedtls/sha256.h"

#include "wasm_export.h"
#include "moy_wasm_footprint.h"
#include "moy_wasm_key.h"
#include "moy_wasm_session.h"
#include "moy_wasm_thread.h"

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

static run_t g_run;
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

static const char KEY_TAIL[] = "fork " MOY_WASM_FORK_COMMIT "\n" KEY_TARGET;

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

// The module's signature (moy_wasm_key.h): NULL when the file may load, with
// *module_len set to the module's length without any trailer; otherwise the
// refusal. The RSA check is moy_ota.verify_sig -- the body and the keys every
// OTA manifest is checked with -- so this runs on the MicroPython task, before
// the run's thread exists.
//
// A file with no trailer loads only when `unsigned_ok`, the owner's Unknown
// sources setting as the caller read it; its provenance key is checked on the
// thread as every module's is. A trailer that is present must verify whatever
// `unsigned_ok` says: a signature that does not is a module changed after it
// was signed, or one signed with a key this image does not trust, and neither
// is a module somebody built without signing.
static const char *verify_module(const uint8_t *file, uint32_t len, bool unsigned_ok,
                                 uint32_t *module_len)
{
    const uint32_t magic = sizeof(MOY_WASM_SIG_MAGIC) - 1;
    if (len < magic + 4 || memcmp(file + len - magic, MOY_WASM_SIG_MAGIC, magic) != 0) {
        *module_len = len;
        return unsigned_ok ? NULL : "unsigned module";
    }
    const uint8_t *lp = file + len - magic - 4;
    uint32_t k = lp[0] | (uint32_t)lp[1] << 8 | (uint32_t)lp[2] << 16 | (uint32_t)lp[3] << 24;
    if (k < 64 || k > 1024 || k > len - magic - 4) {
        return "malformed signature";
    }
    uint32_t n = len - magic - 4 - k;

    uint8_t digest[32];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    for (uint32_t at = 0; at < n; at += 65536) {
        mbedtls_sha256_update(&ctx, file + at, n - at < 65536 ? n - at : 65536);
    }
    mbedtls_sha256_finish(&ctx, digest);
    mbedtls_sha256_free(&ctx);

    static const char hex[] = "0123456789abcdef";
    char text[sizeof(MOY_WASM_SIG_SCHEME) + sizeof(KEY_CHIP) + 16 + 64];
    int tl = snprintf(text, sizeof(text), MOY_WASM_SIG_SCHEME "\n%s\n%u\n", KEY_CHIP,
                      (unsigned)n);
    for (int i = 0; i < 32; i++) {
        text[tl++] = hex[digest[i] >> 4];
        text[tl++] = hex[digest[i] & 15];
    }
    const char *why = "bad signature";
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        vstr_t sig;
        vstr_init(&sig, 2 * k);
        const uint8_t *sp = lp - k;
        for (uint32_t i = 0; i < k; i++) {
            vstr_add_byte(&sig, hex[sp[i] >> 4]);
            vstr_add_byte(&sig, hex[sp[i] & 15]);
        }
        mp_obj_t ota = mp_import_name(MP_QSTR_moy_ota, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        mp_obj_t ok = mp_call_function_2(mp_load_attr(ota, MP_QSTR_verify_sig),
                                         mp_obj_new_bytes((const byte *)text, tl),
                                         mp_obj_new_str_from_vstr(&sig));
        if (mp_obj_is_true(ok)) {
            why = NULL;
        }
        nlr_pop();
    } else {
        why = "the signature could not be checked";
    }
    *module_len = n;
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
// hold). `allow_unsigned` lets a module with no signature load, as the Unknown
// sources setting does for a cart. done() says when it ended, result()
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

    run_t *r = &g_run;
    if (r->started) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("a run is live; collect it with result()"));
    }
    if (moy_wasm_session_live()) {
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
    const char *why = verify_module(r->file, r->file_len, a[ARG_allow_unsigned].u_bool,
                                    &r->file_len);
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
    return mp_obj_new_bool(!g_run.started || g_run.finished);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_done_obj, mod_done);

// terminate() -> whether an instance was live to be told. wasm_runtime_terminate
// raises "terminated by user" in the instance; README.md says when AOT code
// notices it.
static mp_obj_t mod_terminate(void)
{
    run_t *r = &g_run;
    bool told = false;
    if (r->started && g_lock) {
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

typedef struct {
    // the MicroPython side's
    bool live;
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
    lane_t lane[LANES_MAX + 1];     // [1..]: the par lanes, started on demand
} sess_t;

// The live session, or NULL. Allocated per session, from PSRAM, so an idle
// desk carries a pointer and not the struct.
static sess_t *g_sess;

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
    sess_t *s = g_sess;
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

    size_t pool_size = pool_bytes(s->file_len);
    uint8_t *pool = heap_caps_malloc(pool_size, PSRAM_CAPS);
    if (!pool) {
        rc = sess_fail(s, OUT_OF_MEMORY, "no PSRAM for the runtime pool");
        goto opened;
    }
    RuntimeInitArgs init;
    memset(&init, 0, sizeof(init));
    init.mem_alloc_type = Alloc_With_Pool;
    init.mem_alloc_option.pool.heap_buf = pool;
    init.mem_alloc_option.pool.heap_size = pool_size;
    if (!wasm_runtime_full_init(&init)) {
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
    {
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
    // included, so the file goes back now.
    heap_caps_free(s->file);
    s->file = NULL;
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
    env = wasm_runtime_create_exec_env(inst, MOY_WASM_EXEC_STACK);
    if (!env) {
        rc = sess_fail(s, OUT_OF_MEMORY, "no pool for the exec env");
        goto opened;
    }
    wasm_runtime_set_native_stack_boundary(
        env, pxTaskGetStackStart(NULL) + MOY_WASM_STACK_GUARD);
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
        xSemaphoreGive(s->back);
    }
    if (up && ops->unbound) {
        ops->unbound(ops->user);
    }
    lanes_stop(s);
    if (env) {
        wasm_runtime_destroy_exec_env(env);
    }
    if (inst) {
        wasm_runtime_deinstantiate(inst);
    }
    if (module) {
        wasm_runtime_unload(module);
    }
    if (up) {
        wasm_runtime_destroy();
    }
    if (pool) {
        heap_caps_free(pool);
    }
    xSemaphoreGive(s->back);               // closed
    return NULL;
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
    heap_caps_free(s);
    if (g_sess == s) {
        g_sess = NULL;
    }
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

int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, const moy_wasm_ops *ops, char *err,
                          size_t errlen)
{
    if (moy_wasm_session_live() || g_run.started) {
        snprintf(err, errlen, "a wasm run is already live");
        return 1;
    }
    sess_t *s = heap_caps_calloc(1, sizeof(sess_t), PSRAM_CAPS);
    if (!s) {
        snprintf(err, errlen, OUT_OF_MEMORY ": no PSRAM for the cart's session");
        return 1;
    }
    s->go = xSemaphoreCreateBinary();
    s->back = xSemaphoreCreateBinary();
    s->vm_done = xSemaphoreCreateBinary();
    if (!s->go || !s->back || !s->vm_done) {
        sess_free(s);
        snprintf(err, errlen, OUT_OF_MEMORY ": no memory for the cart's session");
        return 1;
    }
    s->ops = ops;
    if (want_sha && strlen(want_sha) == 64) {
        memcpy(s->want_sha, want_sha, 65);
    }
    g_sess = s;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        read_module(mp_obj_new_str(path, strlen(path)), memory, &s->file, &s->file_len);
        nlr_pop();
    } else {
        sess_free(s);
        nlr_jump(nlr.ret_val);
    }
    const char *why = verify_module(s->file, s->file_len, allow_unsigned != 0, &s->file_len);
    if (why) {
        snprintf(err, errlen, "refused: %s", why);
        sess_free(s);
        return 1;
    }
    s->waiting = true;
    int pe = moy_wasm_spawn(&s->tid, sess_thread, s, MOY_WASM_STACK_BYTES,
                            MOY_WASM_STACK_PSRAM, MOY_WASM_CORE, MOY_WASM_PRIO);
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
    run_t *r = &g_run;
    if (!r->started) {
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

static MP_DEFINE_STR_OBJ(mod_key_obj, "fork " MOY_WASM_FORK_COMMIT "\n" KEY_TARGET);
static MP_DEFINE_STR_OBJ(mod_fork_obj, MOY_WASM_FORK_COMMIT);
static MP_DEFINE_STR_OBJ(mod_chip_obj, KEY_CHIP);

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
    { MP_ROM_QSTR(MP_QSTR_footprint), MP_ROM_PTR(&mod_footprint_obj) },
    // The key tail this build wants (after the wasm line), the fork commit it
    // runs, and the board's default run stack (bytes, in PSRAM).
    { MP_ROM_QSTR(MP_QSTR_KEY), MP_ROM_PTR(&mod_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_FORK), MP_ROM_PTR(&mod_fork_obj) },
    { MP_ROM_QSTR(MP_QSTR_STACK), MP_ROM_PTR(&mod_stack_obj) },
    // The runtime pool's base size; a module adds a share of its own.
    { MP_ROM_QSTR(MP_QSTR_POOL), MP_ROM_INT(MOY_WASM_POOL_BYTES) },
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
