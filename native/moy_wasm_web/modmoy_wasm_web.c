// moy_wasm_web -- the browser's compiled-cart engine (README.md beside this).
//
// The session surface the boards' engine implements over WAMR
// (native/moy_wasm/moy_wasm_session.h), implemented over the page's own
// WebAssembly engine: the cart's main.wasm is compiled and instantiated by
// the browser as a SIBLING of this module, never an engine inside it. What
// the session does here is what it does on a board -- read the module file
// through the VFS, have the host half check its shape, instantiate it, have
// the host half bind it, call its hooks, tear it down -- and moycore drives
// it through the same calls, so a compiled cart runs on the same console,
// verbs and Player in a browser as on glass.
//
// The instance lives in JavaScript, in the worker that runs this VM
// (firmware/web_runner/worker.js's cart engine, `Module.moyEngine`). Its
// "moy" imports are adapters over libmoy's import table, generated from the
// table's own signature strings, and libmoy/embed.c (vendored from moy-spec)
// is what the binding asks of them in C. Everything here runs on the page's
// one thread, the VM's: there is no session thread to hand work to, so
// moy_wasm_on_vm runs its request at once and a cart's par items run in
// order through the binding (moy_wasm_session_lanes is 0).
//
// What a board does and this engine does not: a compiled (AOT) module, its
// provenance key, its signature, and its admission (moy_ota_admit_module).
// All four exist because an AOT module is native code whose sandbox is
// whatever the compiler emitted. main.wasm is not native code: the browser validates it and runs it
// bounds-checked in its own memory, which is the sandbox SPEC.md 16 assumes,
// so it needs no signature and no admission, and every open here is main.wasm
// (`interp`'s meaning on a board).
//
// The MicroPython module is `moy_wasm`, the engine's name on every tier, with
// the surface moycore_glue reads: CHIP and FORMAT (None: this engine has no
// compiled-module tier, so a cart never "needs an update" here), footprint
// and interp_footprint (one rule), and mem().

#include "moy_kmem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <emscripten.h>

#include "py/builtin.h"
#include "py/obj.h"
#include "py/runtime.h"
#include "py/stream.h"

#include "moy_wasm_session.h"

// The engine's reaches into the worker. `Module.moyEngine` is the cart engine
// worker.js installs before the console boots; a page without one has no
// compiled tier and says so as a load error. Opening hands it what of this VM
// its adapters call: a C function by its table index (the table's rows), the
// table itself and the trap (embed.c's exports), and the allocator.
EM_JS_DEPS(moy_wasm_web, "$stringToUTF8,$getWasmTableEntry,malloc,free,"
                         "moy_web_natives,moy_web_app_natives,moy_web_trapped,"
                         "moy_web_item_trap");

// The app ABI's import adapter (native/moy_app/moy_app_wasm.h): the rows of
// module "moybyte.app", which the page's adapters wrap as they wrap the "moy"
// table's (worker.js). Weak there: an image without native/moy_app answers no
// rows.
#if __has_include("moy_app_wasm.h")
#include "moy_app_wasm.h"
#else
const moy_app_native_t *moy_app_wasm_natives(uint32_t *count);
#endif
#pragma weak moy_app_wasm_natives
EMSCRIPTEN_KEEPALIVE const void *moy_web_app_natives(uint32_t *count)
{
    *count = 0;
    return moy_app_wasm_natives != NULL ? moy_app_wasm_natives(count) : NULL;
}

EM_JS(int, web_open, (const uint8_t *bytes, uint32_t len, char *err, int errlen), {
    var e = Module.moyEngine;
    var why = e ? e.open(HEAPU8.subarray(bytes, bytes + len), {
                      fn: getWasmTableEntry, natives: _moy_web_natives,
                      appNatives: _moy_web_app_natives,
                      trapped: _moy_web_trapped, itemTrap: _moy_web_item_trap,
                      malloc: _malloc, free: _free })
                : "instantiate: this page has no WebAssembly cart engine";
    if (!why) return 0;
    stringToUTF8(why, err, errlen);
    return 1;
});

EM_JS(void, web_bind, (void *binding), {
    Module.moyEngine.bind(binding);
});

EM_JS(int, web_hook, (int hook, float dt, char *err, int errlen), {
    var why = Module.moyEngine.hook(hook, dt);
    if (why === null) return 0;
    stringToUTF8(why, err, errlen);
    return 1;
});

EM_JS(void, web_close, (void), {
    if (Module.moyEngine) Module.moyEngine.close();
});

// The largest memory, in 64 KiB pages, the browser gives one module (the
// engine's own probe).
EM_JS(int, web_limit_pages, (void), {
    return Module.moyEngine ? Module.moyEngine.limitPages() : 0;
});

// The most a module file may be. SPEC.md 16 bounds a cart by its declared
// memory, not its code; this only refuses a file no cart's would be.
#define WEB_FILE_MAX (64u * 1024u * 1024u)

static struct {
    int live;
    const moy_wasm_ops *ops;
    moy_wasm_web_env env;
} g_sess;

// The module file, read through the VFS as every other store read is. Raises;
// on success the caller frees *out.
static void read_module(const char *path, uint8_t **out, uint32_t *out_len)
{
    mp_obj_t args[2] = { mp_obj_new_str(path, strlen(path)), MP_OBJ_NEW_QSTR(MP_QSTR_rb) };
    mp_obj_t f = mp_call_function_n_kw(MP_OBJ_FROM_PTR(&mp_builtin_open_obj), 2, 0, args);
    int e = 0;
    mp_off_t size = mp_stream_seek(f, 0, MP_SEEK_END, &e);
    if (e == 0) {
        mp_stream_seek(f, 0, MP_SEEK_SET, &e);
    }
    if (e != 0 || size <= 0 || (uint64_t)size > WEB_FILE_MAX) {
        mp_stream_close(f);
        mp_raise_ValueError(MP_ERROR_TEXT("module file is empty, unreadable or too big"));
    }
    uint8_t *buf = moy_kalloc((size_t)size, MOY_KMEM_LIBC);
    if (!buf) {
        mp_stream_close(f);
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("no memory for the module file"));
    }
    mp_uint_t got = mp_stream_rw(f, buf, (mp_uint_t)size, &e, MP_STREAM_RW_READ);
    mp_stream_close(f);
    if (e != 0 || got != (mp_uint_t)size) {
        free(buf);
        mp_raise_OSError(e ? e : MP_EIO);
    }
    *out = buf;
    *out_len = (uint32_t)size;
}

int moy_wasm_session_live(void)
{
    return g_sess.live;
}

// The page holds one session: the handle names it while it is open.
void *moy_wasm_session_current(void)
{
    return g_sess.live ? &g_sess : NULL;
}

void moy_wasm_session_use(void *session)
{
    (void)session;
}

// The page runs one thread and no watcher reaches into a running export.
void moy_wasm_session_terminate(void)
{
}

void moy_wasm_session_close(void)
{
    if (!g_sess.live) {
        return;
    }
    if (g_sess.ops->unbound) {
        g_sess.ops->unbound(g_sess.ops->user);
    }
    web_close();
    memset(&g_sess, 0, sizeof(g_sess));
}

int moy_wasm_session_open(const char *path, const char *want_sha, uint32_t memory,
                          int allow_unsigned, int interp, const moy_wasm_ops *ops,
                          char *err, size_t errlen)
{
    // A board's AOT module is checked by key and signature; the page runs
    // main.wasm only, which needs neither (the header above).
    (void)want_sha;
    (void)memory;
    (void)allow_unsigned;
    (void)interp;
    if (g_sess.live) {
        snprintf(err, errlen, "a wasm run is already live");
        return 1;
    }
    uint8_t *file = NULL;
    uint32_t len = 0;
    read_module(path, &file, &len);
    g_sess.live = 1;
    g_sess.ops = ops;
    char why[160];
    why[0] = 0;
    int rc = 0;
    if (ops->runtime_up && ops->runtime_up(ops->user, why, sizeof(why))) {
        snprintf(err, errlen, "import table: %s", why);
        rc = 1;
    }
    moy_wasm_web_module module = { file, len };
    if (!rc && ops->loaded && ops->loaded(ops->user, &module, why, sizeof(why))) {
        snprintf(err, errlen, "refused: %s", why);
        rc = 1;
    }
    if (!rc && web_open(file, len, err, (int)errlen)) {
        rc = 1;
    }
    // The page has compiled the module; nothing it keeps points into the file.
    free(file);
    if (!rc && ops->bound && ops->bound(ops->user, &g_sess.env, why, sizeof(why))) {
        snprintf(err, errlen, "bind: %s", why);
        rc = 1;
    }
    if (rc) {
        moy_wasm_session_close();
        return 1;
    }
    web_bind(g_sess.env.binding);
    return 0;
}

int moy_wasm_session_call(int what, float dt, char *err, size_t errlen)
{
    if (!g_sess.live) {
        snprintf(err, errlen, "no cart session");
        return 1;
    }
    err[0] = 0;
    return g_sess.ops->call(g_sess.ops->user, what, dt, err, errlen);
}

int moy_wasm_session_export(int hook, float dt, char *err, size_t errlen)
{
    return web_hook(hook, dt, err, (int)errlen);
}

// The VM is this thread, and it is always the one asking.
int moy_wasm_on_vm(void (*fn)(void *arg), void *arg)
{
    fn(arg);
    return 0;
}

int moy_wasm_session_lanes(void)
{
    return 0;
}

int moy_wasm_session_lane_go(int lane, void (*fn)(void *arg), void *arg)
{
    (void)lane;
    (void)fn;
    (void)arg;
    return -1;
}

void moy_wasm_session_lane_wait(int lane)
{
    (void)lane;
}

// -- the MicroPython module ---------------------------------------------------

// footprint(memory, module_bytes) -> (total, block): what a cart's load takes
// here -- its declared linear memory, one block of its own (the module's
// WebAssembly.Memory), and the module file the page compiles beside it.
// moy-spec's web player sizes a cart the same way. interp_footprint is the
// same rule: this engine has one tier.
static mp_obj_t mod_footprint(mp_obj_t memory_obj, mp_obj_t module_obj)
{
    mp_int_t memory = mp_obj_get_int(memory_obj);
    mp_int_t module = mp_obj_get_int(module_obj);
    mp_obj_t t[2] = {
        mp_obj_new_int_from_ull((unsigned long long)memory + (unsigned long long)module),
        mp_obj_new_int_from_ull((unsigned long long)memory),
    };
    return mp_obj_new_tuple(2, t);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_footprint_obj, mod_footprint);

// mem() -> (None, None, None, free, largest), a board's mem() in shape: its
// internal-RAM slots have nothing to report here, and its PSRAM slots --
// where a board's cart memory comes from -- are the largest memory this
// browser gives one module, measured once by allocating it (the engine's
// limitPages). A load that still cannot have its memory says so as
// "out of memory", which the Player turns into the same notice.
static mp_obj_t mod_mem(void)
{
    static int pages = -1;
    if (pages < 0) {
        pages = web_limit_pages();
    }
    mp_obj_t limit = mp_obj_new_int_from_ull((unsigned long long)pages * 65536ull);
    mp_obj_t t[5] = { mp_const_none, mp_const_none, mp_const_none, limit, limit };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mem_obj, mod_mem);

// -- a WASI command (native/moy_wasm/moy_wasi.h) ---------------------------------
//
// The site's build only (MOY_WASI_WEB, `build.sh --site`): the console that
// runs the shell. The boards' surface in shape (native/moy_wasm/modmoy_wasm.c):
// command_start(path, want_sha, interp, allow_unsigned, argv, roots, bg_pid,
// budget_ms[, run]) -> (0, None) or (-3, why), command_step(max_ms) and
// command_stop() -> (-100, None) while it goes on, else (the exit status,
// None) or (-1, the trap). The one adapter (moy_wasi.c) serves its rows, over
// the page's engine (moy_wasi_web.h): the start compiles, checks and
// instantiates the module in the shell session the door's crossing runs in;
// the first step runs `_start` to its end inside that crossing, its rows
// served in place, since the page has one thread and the line's run already
// holds it (MOY_SH_RUN_BUDGET_S); so no step ever finds `_start` in flight.
// One command at a time.
#if MOY_WASI_WEB

#include "py/nlr.h"
#include "moy_wasi.h"

#define CMD_ERR 192

typedef struct {
    moy_wasi_t *w;
    moy_wasi_web_module_t module;
    moy_wasi_web_run_t run;
    bool ran;
    int status;
    char err[CMD_ERR];
} cmd_t;

static cmd_t *g_cmd;

static bool cmd_done(void *job)
{
    (void)job;
    return true;
}

static void cmd_serve(void *job, int64_t us)
{
    (void)job;
    (void)us;
}

static void cmd_terminate(void *job)
{
    (void)job;
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

static void cmd_free(cmd_t *c)
{
    moy_wasi_web_close(&c->module);
    moy_wasi_unregister();
    moy_wasi_free(c->w);
    free(c);
}

static mp_obj_t cmd_end(void)
{
    cmd_t *c = g_cmd;
    int code = moy_wasi_status(c->w, c->status);
    int status = code == MOY_WASI_TRAPPED ? -1 : code < 0 ? -3 : code;
    char err[CMD_ERR];
    snprintf(err, sizeof err, "%s", c->err);
    g_cmd = NULL;
    cmd_free(c);
    return cmd_answer(status, err);
}

static const moy_app_run_t *run_arg(mp_obj_t o)
{
    if (o == mp_const_none) {
        return NULL;
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != sizeof(moy_app_run_t)) {
        mp_raise_ValueError(MP_ERROR_TEXT("not a run"));
    }
    return (const moy_app_run_t *)b.buf;
}

static mp_obj_t mod_command_start(size_t n_args, const mp_obj_t *args)
{
    const char *path = mp_obj_str_get_str(args[0]);
    size_t argc;
    mp_obj_t *items;
    mp_obj_get_array(args[4], &argc, &items);
    const char *roots = args[5] == mp_const_none ? NULL : mp_obj_str_get_str(args[5]);
    uint32_t bg_pid = (uint32_t)mp_obj_get_int(args[6]);
    uint32_t budget = (uint32_t)mp_obj_get_int(args[7]);
    const moy_app_run_t *run = n_args > 8 ? run_arg(args[8]) : NULL;
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        return cmd_answer(-3, "no shell session is running");
    }
    if (g_cmd != NULL) {
        return cmd_answer(-3, "a command is running");
    }
    uint8_t *file = NULL;
    uint32_t len = 0;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        read_module(path, &file, &len);
        nlr_pop();
    } else {
        return cmd_answer(-3, "the module file will not read");
    }
    cmd_t *c = moy_kalloc(sizeof *c, MOY_KMEM_LIBC | MOY_KMEM_ZERO);
    const char **argv = moy_kalloc((argc ? argc : 1) * sizeof *argv, MOY_KMEM_LIBC | MOY_KMEM_ZERO);
    if (c == NULL || argv == NULL) {
        free(c);
        free(argv);
        free(file);
        mp_raise_type(&mp_type_MemoryError);
    }
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    c->status = -3;
    c->w = moy_wasi_new(s, (int)argc, argv, roots, bg_pid, budget, NULL);
    free(argv);
    char why[CMD_ERR];
    why[0] = 0;
    int rc = c->w == NULL ? (snprintf(why, sizeof why, "out of memory"), 1) : 0;
    if (!rc && moy_wasi_register() != 0) {
        snprintf(why, sizeof why, "out of memory: the WASI table would not register");
        rc = 1;
    }
    if (!rc) {
        moy_wasi_set_run(c->w, run);
        rc = moy_wasi_web_load(file, len, &c->module, why, sizeof why);
    }
    free(file);
    if (!rc) {
        char e[CMD_ERR - 16];
        if (moy_wasi_check(&c->module, e, sizeof e) != 0) {
            snprintf(why, sizeof why, "refused: %s", e);
            rc = 1;
        }
    }
    if (!rc) {
        c->run.module = &c->module;
        rc = moy_wasi_web_instantiate(&c->run, why, sizeof why);
    }
    if (rc) {
        if (c->w != NULL) {
            cmd_free(c);
        } else {
            free(c);
        }
        return cmd_answer(-3, why);
    }
    g_cmd = c;
    return cmd_answer(0, "");
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_command_start_obj, 8, 9, mod_command_start);

static mp_obj_t mod_command_step(mp_obj_t max_ms_in)
{
    cmd_t *c = g_cmd;
    if (c == NULL) {
        return cmd_answer(-3, "no command is running");
    }
    if (!c->ran) {
        c->ran = true;
        c->status = moy_wasi_main(c->w, &c->run, c->err, sizeof c->err);
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
    c->ran = true;
    moy_wasi_stop(c->w, &cmd_runner, c);
    return cmd_end();
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_command_stop_obj, mod_command_stop);

#endif // MOY_WASI_WEB

static const mp_rom_map_elem_t moy_wasm_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_wasm) },
    // No compiled-module tier: a cart's main.wasm is what runs, at full speed.
    { MP_ROM_QSTR(MP_QSTR_CHIP), MP_ROM_NONE },
    { MP_ROM_QSTR(MP_QSTR_FORMAT), MP_ROM_NONE },
    { MP_ROM_QSTR(MP_QSTR_footprint), MP_ROM_PTR(&mod_footprint_obj) },
    { MP_ROM_QSTR(MP_QSTR_interp_footprint), MP_ROM_PTR(&mod_footprint_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&mod_mem_obj) },
#if MOY_WASI_WEB
    { MP_ROM_QSTR(MP_QSTR_command_start), MP_ROM_PTR(&mod_command_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_command_step), MP_ROM_PTR(&mod_command_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_command_stop), MP_ROM_PTR(&mod_command_stop_obj) },
#endif
};
static MP_DEFINE_CONST_DICT(moy_wasm_globals, moy_wasm_globals_table);

const mp_obj_module_t moy_wasm_web_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_wasm_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_wasm, moy_wasm_web_user_cmodule);
