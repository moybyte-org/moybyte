// libFuzzer target: a main.wasm through WAMR's loader, instantiation and a
// bounded run of every exported function (the interpreted path).
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "wasm_export.h"

#define ONE_ALLOC_MAX (64u << 20)
#define LIVE_MAX (256u << 20)
static size_t live;

typedef struct { size_t n; size_t pad; } hdr_t;

static void *f_malloc(unsigned int n)
{
    if (n > ONE_ALLOC_MAX || live + n > LIVE_MAX) return NULL;
    hdr_t *h = malloc(sizeof(hdr_t) + n);
    if (!h) return NULL;
    h->n = n; live += n;
    return h + 1;
}
static void f_free(void *p)
{
    if (!p) return;
    hdr_t *h = (hdr_t *)p - 1;
    live -= h->n;
    free(h);
}
static void *f_realloc(void *p, unsigned int n)
{
    if (!p) return f_malloc(n);
    hdr_t *h = (hdr_t *)p - 1;
    if (n > ONE_ALLOC_MAX || live - h->n + n > LIVE_MAX) return NULL;
    size_t old = h->n;
    hdr_t *g = realloc(h, sizeof(hdr_t) + n);
    if (!g) return NULL;
    g->n = n; live = live - old + n;
    return g + 1;
}

static int up;

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (!up) {
        RuntimeInitArgs a;
        memset(&a, 0, sizeof a);
        a.mem_alloc_type = Alloc_With_Allocator;
        a.mem_alloc_option.allocator.malloc_func = (void *)f_malloc;
        a.mem_alloc_option.allocator.realloc_func = (void *)f_realloc;
        a.mem_alloc_option.allocator.free_func = (void *)f_free;
        if (!wasm_runtime_full_init(&a)) abort();
        wasm_runtime_set_log_level(WASM_LOG_LEVEL_FATAL);
        up = 1;
    }
    if (size > (4u << 20)) return 0;
    uint8_t *buf = malloc(size ? size : 1);
    memcpy(buf, data, size);
    char err[128];
    LoadArgs la;
    memset(&la, 0, sizeof la);
    la.name = "";
    la.wasm_binary_freeable = true;
    wasm_module_t m = wasm_runtime_load_ex(buf, (uint32_t)size, &la, err, sizeof err);
    if (m) {
        wasm_module_inst_t inst = wasm_runtime_instantiate(m, 32 * 1024, 0, err, sizeof err);
        if (inst) {
            wasm_exec_env_t env = wasm_runtime_create_exec_env(inst, 32 * 1024);
            int32_t nexp = wasm_runtime_get_export_count(m);
            int calls = 0;
            for (int32_t i = 0; env && i < nexp && calls < 8; i++) {
                wasm_export_t ex;
                wasm_runtime_get_export_type(m, i, &ex);
                if (ex.kind != WASM_IMPORT_EXPORT_KIND_FUNC) continue;
                wasm_function_inst_t f = wasm_runtime_lookup_function(inst, ex.name);
                if (!f) continue;
                uint32_t np = wasm_func_get_param_count(f, inst);
                uint32_t nr = wasm_func_get_result_count(f, inst);
                if (np > 16 || nr > 16) continue;
                wasm_valkind_t pk[16], rk[16];
                wasm_val_t pv[16], rv[16];
                wasm_func_get_param_types(f, inst, pk);
                memset(pv, 0, sizeof pv);
                for (uint32_t j = 0; j < np; j++) pv[j].kind = pk[j];
                calls++;
                wasm_runtime_set_instruction_count_limit(env, 200000);
                wasm_runtime_call_wasm_a(env, f, nr, rv, np, pv);
                wasm_runtime_clear_exception(inst);
                (void)rk;
            }
            if (env) wasm_runtime_destroy_exec_env(env);
            wasm_runtime_deinstantiate(inst);
        }
        wasm_runtime_unload(m);
    }
    free(buf);
    return 0;
}
