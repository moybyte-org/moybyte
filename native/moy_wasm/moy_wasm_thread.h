#ifndef MOY_WASM_THREAD_H
#define MOY_WASM_THREAD_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

// A joinable pthread named "moy_wasm" with its stack of `stack_bytes` in PSRAM
// or internal SRAM, pinned to `core` at `prio`. 0, or an errno.
int moy_wasm_spawn(pthread_t *tid, void *(*fn)(void *), void *arg,
                   uint32_t stack_bytes, bool psram, int core, int prio);

// What the runtime's native-stack check reads: off the exec env, the
// boundary it compares against, the one the embedder handed it and the thread
// that last entered it; off its AOT module, the largest entry of the stack
// sizes each function's entry check subtracts (0 for an interpreted one).
void moy_wasm_env_stack(void *env, void **bound, void **user, pthread_t *by,
                        uint32_t *size_max);

#endif
