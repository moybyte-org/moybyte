// libFuzzer target: a board's AOT module file through what a board does
// before any of it runs -- the admission (moy_ota_admit_module: the
// signature trailer, against a throwaway key), WAMR's AOT loader for the
// chip, the provenance key (modmoy_wasm.c's check_key, restated), then
// instantiation without a start function. MOY_FUZZ_CONSENT=0 fuzzes a cart
// without full_access, where only a verified module reaches the loader.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wasm_export.h"
#include "moy_ota.h"
#include "moy_wasm_key.h"
#include "fuzzkey.h"

#ifdef BUILD_TARGET_XTENSA
#define KEY_TARGET MOY_WASM_KEY_ESP32S3
#else
#define KEY_TARGET MOY_WASM_KEY_ESP32P4
#endif
static const char KEY_TAIL[] = "format " MOY_WASM_FORMAT_VERSION "\n" KEY_TARGET;

#define ONE_ALLOC_MAX (32u << 20)
#define LIVE_MAX (128u << 20)
static size_t live;
typedef struct { size_t n; size_t pad[3]; } hdr_t;
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

static int check_key(wasm_module_t module)
{
    uint32_t len = 0;
    const uint8_t *key = wasm_runtime_get_custom_section(module, MOY_WASM_KEY_SECTION, &len);
    if (!key) return 0;
    const size_t magic = sizeof(MOY_WASM_KEY_MAGIC) - 1;
    const size_t wasm_line = 5 + 64 + 1;
    if (len < magic + wasm_line || memcmp(key, MOY_WASM_KEY_MAGIC, magic) != 0
        || memcmp(key + magic, "wasm ", 5) != 0 || key[magic + wasm_line - 1] != '\n')
        return 0;
    const uint8_t *got = key + magic + wasm_line;
    size_t got_len = len - magic - wasm_line;
    return got_len == sizeof(KEY_TAIL) - 1 && memcmp(got, KEY_TAIL, got_len) == 0;
}

static int up, consent = 1;
static moy_ota_key_t fkey;

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
        const char *c = getenv("MOY_FUZZ_CONSENT");
        consent = !(c && c[0] == '0');
        memcpy(fkey.n, FUZZ_KEY_N, sizeof fkey.n);
        up = 1;
    }
    if (size > (8u << 20)) return 0;
    uint8_t *buf = malloc(size ? size : 1);
    memcpy(buf, data, size);
    size_t n = size;
    const char *why = moy_ota_admit_module(buf, size, MOY_WASM_SIG_SCHEME, FUZZ_CHIP, consent,
                                           &fkey, 1, &n);
    if (why) {
        free(buf);
        return 0;
    }
    if (n > size) abort();
    char err[128];
    LoadArgs la;
    memset(&la, 0, sizeof la);
    la.name = "";
    la.wasm_binary_freeable = true;
    wasm_module_t m = wasm_runtime_load_ex(buf, (uint32_t)n, &la, err, sizeof err);
    if (m) {
        if (check_key(m)) {
            wasm_module_inst_t inst = wasm_runtime_instantiate(m, 16 * 1024, 0, err, sizeof err);
            if (inst) wasm_runtime_deinstantiate(inst);
        }
        wasm_runtime_unload(m);
    }
    free(buf);
    return 0;
}
