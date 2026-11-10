// moy_wasi_web: WAMR's names for the WASI adapter over the page's engine
// (moy_wasi_web.h). The page's half is worker.js's command engine.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <emscripten.h>

#include "moy_wasi.h"

EM_JS_DEPS(moy_wasi_web, "$stringToUTF8,$lengthBytesUTF8,$getWasmTableEntry,malloc,free,"
                         "moy_web_cmd_row,moy_web_cmd_thrown");

// -- the rows ----------------------------------------------------------------------

#define TABLES 4

static struct {
    const char *module;
    const NativeSymbol *rows;
    uint32_t n;
} s_tables[TABLES];

static int32_t row_unserved(wasm_exec_env_t env) {
    (void)env;
    return -52;                     // WASI's NOSYS, as a moybyte.os row answers an errno
}

bool wasm_runtime_register_natives_sorted(const char *module, const NativeSymbol *rows, uint32_t n) {
    int free_at = -1;
    for (int i = 0; i < TABLES; i++) {
        if (s_tables[i].module != NULL && strcmp(s_tables[i].module, module) == 0) {
            free_at = i;
            break;
        }
        if (s_tables[i].module == NULL && free_at < 0) {
            free_at = i;
        }
    }
    if (free_at < 0) {
        return false;
    }
    s_tables[free_at].module = module;
    s_tables[free_at].rows = rows;
    s_tables[free_at].n = n;
    return true;
}

bool wasm_runtime_unregister_natives(const char *module, const NativeSymbol *rows) {
    for (int i = 0; i < TABLES; i++) {
        if (s_tables[i].module != NULL && strcmp(s_tables[i].module, module) == 0
            && s_tables[i].rows == rows) {
            memset(&s_tables[i], 0, sizeof s_tables[i]);
            return true;
        }
    }
    return false;
}

// Row `i` of every registered table, for the page's imports: its module and
// name, and the function the import calls (by its table index), a
// moybyte.os row's being the one that answers NOSYS. -1 past the last.
EMSCRIPTEN_KEEPALIVE int moy_web_cmd_row(int i, const char **module, const char **name) {
    for (int t = 0; t < TABLES; t++) {
        if (s_tables[t].module == NULL) {
            continue;
        }
        if ((uint32_t)i < s_tables[t].n) {
            const NativeSymbol *r = &s_tables[t].rows[i];
            *module = s_tables[t].module;
            *name = r->symbol;
            void *fn = strcmp(s_tables[t].module, MOY_WASI_OS) == 0 ? (void *)row_unserved
                                                                    : r->func_ptr;
            return (int)(uintptr_t)fn;
        }
        i -= (int)s_tables[t].n;
    }
    return -1;
}

static bool served(const char *module, const char *name) {
    for (int t = 0; t < TABLES; t++) {
        if (s_tables[t].module != NULL && strcmp(s_tables[t].module, module) == 0) {
            for (uint32_t k = 0; k < s_tables[t].n; k++) {
                if (strcmp(s_tables[t].rows[k].symbol, name) == 0) {
                    return true;
                }
            }
        }
    }
    return false;
}

// -- the module ----------------------------------------------------------------------

// The module compiled; its listing a malloc'd text the page writes, a line
// an entry: "i KIND MODULE NAME" or "e KIND NAME", tab-separated, KIND
// WebAssembly.Module.imports' word. NULL with the reason in `err`.
EM_JS(char *, web_cmd_load, (const uint8_t *bytes, uint32_t len, char *err, int errlen), {
    var e = Module.moyCommand;
    var got = e ? e.load(HEAPU8.slice(bytes, bytes + len))
                : { why: "this page has no WebAssembly command engine" };
    if (got.why !== undefined) {
        stringToUTF8(got.why, err, errlen);
        return 0;
    }
    var n = lengthBytesUTF8(got.listing) + 1;
    var p = _malloc(n);
    if (p) stringToUTF8(got.listing, p, n);
    return p;
});

EM_JS(int, web_cmd_instantiate, (void *run, char *err, int errlen), {
    var why = Module.moyCommand.instantiate(run, {
        fn: getWasmTableEntry, row: _moy_web_cmd_row, thrown: _moy_web_cmd_thrown,
        malloc: _malloc, free: _free });
    if (!why) return 0;
    stringToUTF8(why, err, errlen);
    return 1;
});

EM_JS(int, web_cmd_run, (char *why, int cap), {
    var r = Module.moyCommand.run();
    if (r === null) return 0;
    stringToUTF8(r, why, cap);
    return 1;
});

EM_JS(void *, web_cmd_span, (uint32_t off, uint32_t n), {
    return Module.moyCommand.span(off, n);
});

EM_JS(void, web_cmd_close, (void), {
    if (Module.moyCommand) Module.moyCommand.close();
});

static wasm_import_export_kind_t kind_of(const char *k) {
    if (strcmp(k, "function") == 0) {
        return WASM_IMPORT_EXPORT_KIND_FUNC;
    }
    if (strcmp(k, "table") == 0) {
        return WASM_IMPORT_EXPORT_KIND_TABLE;
    }
    if (strcmp(k, "memory") == 0) {
        return WASM_IMPORT_EXPORT_KIND_MEMORY;
    }
    return WASM_IMPORT_EXPORT_KIND_GLOBAL;
}

// The next tab- or newline-ended field of *at, cut in place.
static char *field(char **at) {
    char *f = *at;
    char *e = f + strcspn(f, "\t\n");
    if (*e) {
        *e++ = 0;
    }
    *at = e;
    return f;
}

int moy_wasi_web_load(const uint8_t *bytes, uint32_t len, moy_wasi_web_module_t *m, char *err,
                      size_t errlen) {
    memset(m, 0, sizeof *m);
    char *text = web_cmd_load(bytes, len, err, (int)errlen);
    if (text == NULL) {
        return 1;
    }
    uint32_t lines = 0;
    for (const char *p = text; *p; p++) {
        lines += *p == '\n';
    }
    m->text = text;
    m->imports = calloc(lines ? lines : 1, sizeof *m->imports);
    m->exports = calloc(lines ? lines : 1, sizeof *m->exports);
    if (m->imports == NULL || m->exports == NULL) {
        moy_wasi_web_close(m);
        snprintf(err, errlen, "out of memory");
        return 1;
    }
    for (char *at = text; *at;) {
        char *what = field(&at);
        wasm_import_export_kind_t kind = kind_of(field(&at));
        if (what[0] == 'i') {
            wasm_import_t *im = &m->imports[m->n_imports++];
            im->module_name = field(&at);
            im->name = field(&at);
            im->kind = kind;
            im->linked = kind == WASM_IMPORT_EXPORT_KIND_FUNC && served(im->module_name, im->name);
        } else {
            wasm_export_t *ex = &m->exports[m->n_exports++];
            ex->name = field(&at);
            ex->kind = kind;
        }
    }
    return 0;
}

int moy_wasi_web_instantiate(moy_wasi_web_run_t *run, char *err, size_t errlen) {
    return web_cmd_instantiate(run, err, (int)errlen);
}

void moy_wasi_web_close(moy_wasi_web_module_t *m) {
    web_cmd_close();
    free(m->imports);
    free(m->exports);
    free(m->text);
    memset(m, 0, sizeof *m);
}

int32_t wasm_runtime_get_import_count(wasm_module_t module) {
    return (int32_t)module->n_imports;
}

void wasm_runtime_get_import_type(wasm_module_t module, int32_t i, wasm_import_t *out) {
    *out = module->imports[i];
}

int32_t wasm_runtime_get_export_count(wasm_module_t module) {
    return (int32_t)module->n_exports;
}

void wasm_runtime_get_export_type(wasm_module_t module, int32_t i, wasm_export_t *out) {
    *out = module->exports[i];
}

// -- the run -------------------------------------------------------------------------

// Whether the module is to unwind when the row it called returns: the page's
// engine asks after every row.
EMSCRIPTEN_KEEPALIVE int moy_web_cmd_thrown(moy_wasi_web_run_t *run) {
    return run->thrown;
}

void *moy_wasi_web_reach(wasm_module_inst_t inst, uint32_t off, uint64_t n) {
    (void)inst;
    return n <= UINT32_MAX ? web_cmd_span(off, (uint32_t)n) : NULL;
}

void *wasm_runtime_get_user_data(wasm_exec_env_t env) {
    return env->user;
}

void wasm_runtime_set_user_data(wasm_exec_env_t env, void *user) {
    env->user = user;
}

wasm_module_inst_t wasm_runtime_get_module_inst(wasm_exec_env_t env) {
    return env;
}

uint64_t wasm_runtime_addr_native_to_app(wasm_module_inst_t inst, void *native) {
    (void)inst;
    return (uint64_t)(uintptr_t)native;
}

// No row is handed a pointer into this VM, so none validates.
bool wasm_runtime_validate_native_addr(wasm_module_inst_t inst, void *native, uint64_t size) {
    (void)inst;
    (void)native;
    (void)size;
    return false;
}

void wasm_runtime_set_exception(wasm_module_inst_t inst, const char *why) {
    if (why == NULL) {
        wasm_runtime_clear_exception(inst);
        return;
    }
    inst->thrown = true;
    snprintf(inst->why, sizeof inst->why, "%s", why);
}

const char *wasm_runtime_get_exception(wasm_module_inst_t inst) {
    return inst->thrown ? inst->why : NULL;
}

void wasm_runtime_clear_exception(wasm_module_inst_t inst) {
    inst->thrown = false;
    inst->why[0] = 0;
}

void wasm_runtime_terminate(wasm_module_inst_t inst) {
    wasm_runtime_set_exception(inst, "terminated");
}

wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t inst, const char *name) {
    const moy_wasi_web_module_t *m = inst->module;
    for (uint32_t i = 0; m != NULL && i < m->n_exports; i++) {
        if (m->exports[i].kind == WASM_IMPORT_EXPORT_KIND_FUNC
            && strcmp(m->exports[i].name, name) == 0) {
            return (wasm_function_inst_t)&m->exports[i];
        }
    }
    return NULL;
}

// `_start` (the one function a command's run calls) to its end: false where
// it trapped or a row unwound it, the reason its exception.
bool wasm_runtime_call_wasm(wasm_exec_env_t env, wasm_function_inst_t fn, uint32_t argc,
                            uint32_t argv[]) {
    (void)fn;
    (void)argc;
    (void)argv;
    char why[MOY_WASI_WEB_WHY];
    if (web_cmd_run(why, (int)sizeof why) == 0) {
        return true;
    }
    if (!env->thrown) {
        wasm_runtime_set_exception(env, why);
    }
    return false;
}

wasm_memory_inst_t wasm_runtime_get_default_memory(wasm_module_inst_t inst) {
    (void)inst;
    return NULL;
}

uint64_t wasm_memory_get_cur_page_count(wasm_memory_inst_t mem) {
    (void)mem;
    return 0;
}

uint64_t wasm_memory_get_bytes_per_page(wasm_memory_inst_t mem) {
    (void)mem;
    return 0;
}
