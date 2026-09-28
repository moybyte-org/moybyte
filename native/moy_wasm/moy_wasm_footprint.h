// What a compiled cart's load takes from the heap, by the engine's own sizing.
//
// The engine allocates by these (modmoy_wasm.c: the pool, the block a module
// file is read into) and reports them before a load (moy_wasm.footprint), so
// the Player can refuse a cart the board cannot fit with a notice rather than
// a failed load. The host twin (runtime/moyhost_wasm.c) compiles this same
// file, so a host with a configured limit refuses by the same arithmetic.
//
// A board overrides a setting in its mpconfigboard.h, which is read before
// this file.

#ifndef MOY_WASM_FOOTPRINT_H
#define MOY_WASM_FOOTPRINT_H

#include <stdint.h>

// The run thread's stack: its size, and whether it lives in PSRAM. Each board
// sets both with the measurement that chose them beside the setting.
#ifndef MOY_WASM_STACK_BYTES
#define MOY_WASM_STACK_BYTES (16 * 1024)
#endif
#ifndef MOY_WASM_STACK_PSRAM
#define MOY_WASM_STACK_PSRAM (1)
#endif

// The runtime's allocator pool, PSRAM, held only while a run is live. It
// carries the module's and the instance's structures, the loader's copies of
// the data segments and its relocation tables while it relocates, and the
// exec env; the text and the linear memory are separate PSRAM mappings. The
// pool is MOY_WASM_POOL_BYTES plus one byte in MOY_WASM_POOL_SHARE of the
// module's. The load's peak follows the data segments and the relocations
// rather than the text, so the fixed part carries most of it and a board
// whose loader holds more sets its own (the P4 boards' mpconfigboard.h, with
// Doom's measurements). start()'s `pool` argument measures a module against
// it: the run's `pool_peak` under a larger pool is what the rule must hold.
#ifndef MOY_WASM_POOL_BYTES
#define MOY_WASM_POOL_BYTES (256 * 1024)
#endif
#ifndef MOY_WASM_POOL_SHARE
#define MOY_WASM_POOL_SHARE 4
#endif

// The runtime pool for a module of `module_len` bytes.
static inline uint64_t moy_wasm_pool_bytes(uint64_t module_len)
{
    return MOY_WASM_POOL_BYTES + module_len / MOY_WASM_POOL_SHARE;
}

// The free block the heap needs to serve an allocation of `n` bytes: TLSF
// rounds a request up to its size class's boundary (a thirty-second of the
// request) before it looks, and the mapping carries a header.
static inline uint64_t moy_wasm_heap_block(uint64_t n)
{
    return n + n / 32 + 1024;
}

// The block a module file is read into: one the linear memory of `memory`
// bytes fits once the file is freed, so the memory takes it back -- a file
// read into a block its own size leaves a hole below the text the linear
// memory cannot use. Never smaller than the file.
static inline uint64_t moy_wasm_file_block(uint64_t memory, uint64_t file_len)
{
    uint64_t b = memory ? moy_wasm_heap_block(memory) : 0;
    return b > file_len ? b : file_len;
}

// A load's footprint for a cart declaring `memory` bytes of linear memory
// whose module file is `module_len` bytes. `*total` is what the load holds at
// its peak: the file's block (which the linear memory takes back), the pool,
// the text and data the loader maps (at most the module's own size) and the
// run stack when it is in PSRAM. `*block` is the largest single free block
// that peak asks the heap for.
static inline void moy_wasm_footprint(uint64_t memory, uint64_t module_len,
                                      uint64_t *total, uint64_t *block)
{
    uint64_t file = moy_wasm_file_block(memory, module_len);
    uint64_t pool = moy_wasm_pool_bytes(module_len);
    uint64_t big = file > pool ? file : pool;
    *total = file + pool + module_len + (MOY_WASM_STACK_PSRAM ? MOY_WASM_STACK_BYTES : 0);
    *block = moy_wasm_heap_block(big > module_len ? big : module_len);
}

#endif
