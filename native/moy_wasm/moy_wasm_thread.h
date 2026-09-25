#ifndef MOY_WASM_THREAD_H
#define MOY_WASM_THREAD_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

// A joinable pthread named "moy_wasm" with its stack of `stack_bytes` in PSRAM
// or internal SRAM, pinned to `core` at `prio`. 0, or an errno.
int moy_wasm_spawn(pthread_t *tid, void *(*fn)(void *), void *arg,
                   uint32_t stack_bytes, bool psram, int core, int prio);

#endif
