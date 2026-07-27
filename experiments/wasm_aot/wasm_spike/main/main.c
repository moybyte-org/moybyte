/*
 * #158 spike, device half: WAMR on the ESP32-P4, running the SAME 6502
 * micro-core already measured under Lua (moy_lua) on this board.
 *
 * Runs BOTH modules in one boot for a direct A/B under identical conditions:
 *   1. core6502.wasm          -> fast interpreter
 *   2. core6502_riscv32.aot   -> AOT (wamrc --target=riscv32 --target-abi=ilp32f)
 *
 * NB: the runtime must live on a real pthread -- WAMR's platform layer calls
 * pthread_self(), and IDF's shim asserts when that is reached from a plain
 * FreeRTOS task like app_main ("Failed to find current thread ID!").
 *
 * CPU/cache are pinned in sdkconfig.defaults to the console build's settings
 * (360 MHz, L2 256KB) so the numbers compare directly with the Lua run.
 */
#include <stdio.h>
#include <string.h>
#include <pthread.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "wasm_export.h"
#include "bh_platform.h"

#include "core6502_wasm.h"
#include "core6502_aot.h"
#include "core6502_aot_xip.h"

#define SPIKE_STACK_SIZE (32 * 1024)

static int
bench(wasm_module_inst_t inst, wasm_exec_env_t env, const char *fn,
      uint32_t n, int64_t *out_us, uint32_t *out_ret)
{
    wasm_function_inst_t func = wasm_runtime_lookup_function(inst, fn);
    if (!func) {
        printf("SPIKE ERR lookup %s failed\n", fn);
        return 0;
    }
    uint32_t argv[1];
    argv[0] = n;
    int64_t t0 = esp_timer_get_time();
    if (!wasm_runtime_call_wasm(env, func, 1, argv)) {
        printf("SPIKE ERR call %s: %s\n", fn, wasm_runtime_get_exception(inst));
        return 0;
    }
    *out_us = esp_timer_get_time() - t0;
    *out_ret = argv[0];
    return 1;
}

static void
run_module(const char *label, uint8_t *buf, uint32_t len)
{
    char err[128];
    wasm_module_t module = NULL;
    wasm_module_inst_t inst = NULL;
    wasm_exec_env_t env = NULL;

    size_t before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);

    module = wasm_runtime_load(buf, len, err, sizeof(err));
    if (!module) {
        printf("SPIKE ERR %s load: %s\n", label, err);
        return;
    }
    inst = wasm_runtime_instantiate(module, 16 * 1024, 16 * 1024,
                                    err, sizeof(err));
    if (!inst) {
        printf("SPIKE ERR %s instantiate: %s\n", label, err);
        wasm_runtime_unload(module);
        return;
    }
    env = wasm_runtime_create_exec_env(inst, 16 * 1024);
    if (!env) {
        printf("SPIKE ERR %s exec_env\n", label);
        goto cleanup;
    }

    size_t after = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    printf("SPIKE %s loaded module_bytes=%u internal_bytes=%d\n",
           label, (unsigned)len, (int)(before - after));

    const uint32_t ns[] = { 20000, 100000, 400000, 2000000 };
    for (int i = 0; i < 4; i++) {
        int64_t us = 0;
        uint32_t cyc = 0;
        if (bench(inst, env, "step", ns[i], &us, &cyc)) {
            printf("SPIKE %s step n=%u us=%lld cycles=%u\n",
                   label, (unsigned)ns[i], (long long)us, (unsigned)cyc);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }

    int64_t us = 0;
    uint32_t v = 0;
    if (bench(inst, env, "bad_count", 0, &us, &v)) {
        printf("SPIKE %s bad_opcodes=%u\n", label, (unsigned)v);
    }
    if (bench(inst, env, "spin", 1000000, &us, &v)) {
        printf("SPIKE %s spin n=1000000 us=%lld\n", label, (long long)us);
    }

cleanup:
    if (env) {
        wasm_runtime_destroy_exec_env(env);
    }
    wasm_runtime_deinstantiate(inst);
    wasm_runtime_unload(module);
}

static void *
spike_main(void *arg)
{
    (void)arg;

    /* WAMR's esp-idf os_mmap() allocates AOT text with MALLOC_CAP_EXEC. If the
     * P4 registers no EXEC-capable heap, plain AOT can never load and XIP
     * (execute-in-place from flash .rodata) is the only route. Print both so
     * the failure mode is data, not inference. */
    printf("SPIKE boot heap_internal=%u exec=%u largest_exec=%u\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_EXEC),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_EXEC));

    RuntimeInitArgs init_args;
    memset(&init_args, 0, sizeof(RuntimeInitArgs));
    init_args.mem_alloc_type = Alloc_With_Allocator;
    init_args.mem_alloc_option.allocator.malloc_func = (void *)os_malloc;
    init_args.mem_alloc_option.allocator.realloc_func = (void *)os_realloc;
    init_args.mem_alloc_option.allocator.free_func = (void *)os_free;

    if (!wasm_runtime_full_init(&init_args)) {
        printf("SPIKE ERR runtime init\n");
        return NULL;
    }

    /* AOT first, on the freshest heap -- the plain build needs a contiguous
     * EXEC allocation and must not be judged on a heap the interpreter has
     * already churned. */
    run_module("aot", core6502_aot, core6502_aot_len);

    /* XIP AOT: the code executes where it lies, so it must be reachable on the
     * INSTRUCTION bus. Embedding it in .rodata puts it in the DROM (data)
     * mapping -> executing it faults ("Instruction access fault", measured).
     * So map its flash partition with ESP_PARTITION_MMAP_INST instead. */
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, 0xff, "wasmaot");
    if (!part) {
        printf("SPIKE ERR no wasmaot partition\n");
    }
    else {
        const void *inst_ptr = NULL;
        esp_partition_mmap_handle_t mh;
        esp_err_t e = esp_partition_mmap(part, 0, part->size,
                                         ESP_PARTITION_MMAP_INST,
                                         &inst_ptr, &mh);
        if (e != ESP_OK) {
            printf("SPIKE ERR mmap INST: %d\n", (int)e);
        }
        else {
            printf("SPIKE xip mapped at %p (inst bus)\n", inst_ptr);
            run_module("aotxip", (uint8_t *)inst_ptr, core6502_aot_xip_len);
            esp_partition_munmap(mh);
        }
    }

    run_module("interp", core6502_wasm, core6502_wasm_len);

    printf("SPIKE done\n");
    return NULL;
}

void
app_main(void)
{
    vTaskDelay(pdMS_TO_TICKS(1500));   /* let the serial host attach */

    pthread_t tid;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, SPIKE_STACK_SIZE);
    if (pthread_create(&tid, &attr, spike_main, NULL) != 0) {
        printf("SPIKE ERR pthread_create\n");
        return;
    }
    pthread_join(tid, NULL);
    printf("SPIKE exit\n");
}
