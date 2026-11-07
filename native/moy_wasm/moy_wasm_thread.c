// The run thread's creation and a look at an exec env's stack fields,
// compiled into the runtime's own library because that is the target that
// sees the pthread component (esp_pthread.h) and the runtime's internals
// (wasm_exec_env.h); the MicroPython component sees neither, and a usermod
// cannot add a component to it.

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <errno.h>

#include "esp_heap_caps.h"
#include "esp_pthread.h"

#include "wasm_exec_env.h"
#include "aot_runtime.h"
#include "moy_wasm_thread.h"

int moy_wasm_spawn(pthread_t *tid, void *(*fn)(void *), void *arg,
                   uint32_t stack_bytes, bool psram, int core, int prio)
{
    esp_pthread_cfg_t cfg = esp_pthread_get_default_config();
    cfg.stack_size = stack_bytes;
    cfg.prio = prio;
    cfg.pin_to_core = core;
    cfg.thread_name = "moy_wasm";
    cfg.stack_alloc_caps = psram ? (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
                                 : (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (esp_pthread_set_cfg(&cfg) != ESP_OK) {
        return EINVAL;
    }
    int err = pthread_create(tid, NULL, fn, arg);
    // The configuration is per calling thread: put the default back so no
    // other pthread this task creates inherits the run's stack placement.
    esp_pthread_cfg_t dflt = esp_pthread_get_default_config();
    esp_pthread_set_cfg(&dflt);
    return err;
}

void moy_wasm_env_stack(void *env, void **bound, void **user, pthread_t *by,
                        uint32_t *size_max)
{
    WASMExecEnv *e = env;
    *bound = e->native_stack_boundary;
    *user = e->user_native_stack_boundary;
    *by = e->handle;
    *size_max = 0;
    AOTModuleInstance *inst = (AOTModuleInstance *)e->module_inst;
    if (inst == NULL || inst->module_type != Wasm_Module_AoT) {
        return;
    }
    uint32 n = 0;
    const uint32 *sizes = aot_get_data_section_addr((AOTModule *)inst->module,
                                                    AOT_STACK_SIZES_SECTION_NAME, &n);
    for (uint32 i = 0; sizes != NULL && i < n / sizeof(uint32); i++) {
        if (sizes[i] > *size_max) {
            *size_max = sizes[i];
        }
    }
}
