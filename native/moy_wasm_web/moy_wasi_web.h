// moy_wasi_web: the names of WAMR's embedding API that the WASI adapter
// (native/moy_wasm/moy_wasi.c) calls, over the page's own WebAssembly engine,
// so the one adapter serves a command in the browser too (README.md beside
// this, "A WASI command here"). moy_wasi.h includes this in place of WAMR's
// wasm_export.h where MOY_WASI_WEB is defined: the site's build
// (firmware/web_runner/build.sh --site), whose console runs the shell.
//
// THE MODULE lives in the worker (worker.js's command engine,
// `Module.moyCommand`), a sibling of this VM as a compiled cart is. Its
// imports are the rows the adapter registers, each called by its table index
// with the module's own arguments: a pointer argument is an offset in the
// module's memory, which is what a native's pointer is here
// (wasm_runtime_addr_native_to_app answers it unchanged), and a row's body
// reaches the bytes behind one by copy (moy_wasi_web_reach), the copy written
// back to the module's memory when the row returns.
//
// THE RUN is the page's one thread, the VM's: `_start` is called from the
// shell door's crossing and runs to its end inside it, its rows served in
// place, so a command holds the worker as every run of the browser's shell
// does (moy_sh_screen.c, MOY_SH_RUN_BUDGET_S).
//
// moybyte.os's rows take pointers into the engine's memory, so a row served
// here takes each pointer and length its signature marks `*~` as a copy in
// this VM, the bytes it changed copied back (moy_web_cmd_row). The rows
// served are those served where they are called (moy_wasi_os_rows.h's
// MOY_WASI_OS_HERE: the files, prefs, clipboard, timers, on and at, send and
// log, the notifications and status items, the items handed, service_stop,
// the device handles and the crypto rows), in place, the page's one thread
// being the VM's. The rest go through the VM's queue (moy_vmq.h: the console
// calls, the rows the door's Python half serves, the screen), which a run
// holding that thread cannot wait on: each answers NOSYS, and a module
// importing them still runs.

#ifndef MOY_WASI_WEB_H
#define MOY_WASI_WEB_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct moy_wasi_web_run moy_wasi_web_run_t;
typedef struct moy_wasi_web_module moy_wasi_web_module_t;

// One record is the instance and its exec env: the page runs one `_start`.
typedef moy_wasi_web_run_t *wasm_exec_env_t;
typedef moy_wasi_web_run_t *wasm_module_inst_t;
typedef moy_wasi_web_module_t *wasm_module_t;
typedef void *wasm_function_inst_t;
typedef void *wasm_memory_inst_t;

typedef struct NativeSymbol {
    const char *symbol;
    void *func_ptr;
    const char *signature;
    void *attachment;
} NativeSymbol;

typedef enum {
    WASM_IMPORT_EXPORT_KIND_FUNC,
    WASM_IMPORT_EXPORT_KIND_TABLE,
    WASM_IMPORT_EXPORT_KIND_MEMORY,
    WASM_IMPORT_EXPORT_KIND_GLOBAL,
} wasm_import_export_kind_t;

typedef struct {
    const char *module_name;
    const char *name;
    wasm_import_export_kind_t kind;
    bool linked;                    // a registered row serves it
} wasm_import_t;

typedef struct {
    const char *name;
    wasm_import_export_kind_t kind;
} wasm_export_t;

#define MOY_WASI_WEB_WHY 160

struct moy_wasi_web_run {
    void *user;
    const moy_wasi_web_module_t *module;
    bool thrown;                    // the module unwinds at its row's return
    char why[MOY_WASI_WEB_WHY];
};

// A compiled module's imports and exports, as the page's engine lists them.
struct moy_wasi_web_module {
    uint32_t n_imports, n_exports;
    wasm_import_t *imports;
    wasm_export_t *exports;
    char *text;                     // the names, the page's listing
};

// `n` bytes of the run's module memory at offset `off`, copied into this VM
// for the row being served, or NULL where they are not all in it.
void *moy_wasi_web_reach(wasm_module_inst_t inst, uint32_t off, uint64_t n);

void *wasm_runtime_get_user_data(wasm_exec_env_t env);
void wasm_runtime_set_user_data(wasm_exec_env_t env, void *user);
wasm_module_inst_t wasm_runtime_get_module_inst(wasm_exec_env_t env);
uint64_t wasm_runtime_addr_native_to_app(wasm_module_inst_t inst, void *native);
bool wasm_runtime_validate_native_addr(wasm_module_inst_t inst, void *native, uint64_t size);
void wasm_runtime_set_exception(wasm_module_inst_t inst, const char *why);
const char *wasm_runtime_get_exception(wasm_module_inst_t inst);
void wasm_runtime_clear_exception(wasm_module_inst_t inst);
void wasm_runtime_terminate(wasm_module_inst_t inst);
wasm_function_inst_t wasm_runtime_lookup_function(wasm_module_inst_t inst, const char *name);
bool wasm_runtime_call_wasm(wasm_exec_env_t env, wasm_function_inst_t fn, uint32_t argc,
                            uint32_t argv[]);
wasm_memory_inst_t wasm_runtime_get_default_memory(wasm_module_inst_t inst);
uint64_t wasm_memory_get_cur_page_count(wasm_memory_inst_t mem);
uint64_t wasm_memory_get_bytes_per_page(wasm_memory_inst_t mem);
int32_t wasm_runtime_get_import_count(wasm_module_t module);
void wasm_runtime_get_import_type(wasm_module_t module, int32_t i, wasm_import_t *out);
int32_t wasm_runtime_get_export_count(wasm_module_t module);
void wasm_runtime_get_export_type(wasm_module_t module, int32_t i, wasm_export_t *out);
bool wasm_runtime_register_natives_sorted(const char *module, const NativeSymbol *rows, uint32_t n);
bool wasm_runtime_unregister_natives(const char *module, const NativeSymbol *rows);

// The module file `bytes` compiled by the page, its imports and exports
// listed into `m`: 0, or 1 with the reason in `err`.
int moy_wasi_web_load(const uint8_t *bytes, uint32_t len, moy_wasi_web_module_t *m, char *err,
                      size_t errlen);
// Instantiated with the registered rows as its imports, for `run`: 0, or 1
// with the reason in `err`.
int moy_wasi_web_instantiate(moy_wasi_web_run_t *run, char *err, size_t errlen);
// The instance dropped and the listing freed.
void moy_wasi_web_close(moy_wasi_web_module_t *m);

#endif // MOY_WASI_WEB_H
