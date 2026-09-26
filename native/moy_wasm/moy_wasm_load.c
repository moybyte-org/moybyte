// The loader's half of freeing a module file early, compiled into the
// runtime's own library: it reads the AOT module's structures, whose layout
// is the runtime's configuration -- the defines this target is built with and
// the MicroPython component is not.
//
// A load with `wasm_binary_freeable` promises the file may go once
// wasm_runtime_load returns, and the loader copies what it keeps -- except the
// data segments. The loader decides whether to copy them from the module's
// is_binary_freeable, which wasm_runtime_load sets only after the AOT loader
// returns, so every segment is left pointing into the caller's buffer: the
// instantiation that copies them into linear memory reads a freed file, and
// the unload frees pointers the runtime never allocated. This gives the module
// the copies the flag promised; a segment the loader did copy is left alone.

#include <string.h>

#include "aot_runtime.h"
#include "wasm_memory.h"

#include "moy_wasm_load.h"

bool moy_wasm_own_data(wasm_module_t module, const uint8_t *file, uint32_t len)
{
    AOTModule *m = (AOTModule *)module;
    if (m->module_type != Wasm_Module_AoT) {
        return true;
    }
    for (uint32 i = 0; i < m->mem_init_data_count; i++) {
        AOTMemInitData *d = m->mem_init_data_list[i];
        if (!d || !d->bytes || d->byte_count == 0
            || d->bytes < file || d->bytes >= file + len) {
            continue;
        }
        uint8 *copy = wasm_runtime_malloc(d->byte_count);
        if (!copy) {
            return false;
        }
        memcpy(copy, d->bytes, d->byte_count);
        d->bytes = copy;
    }
    return true;
}
