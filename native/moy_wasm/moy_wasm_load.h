#ifndef MOY_WASM_LOAD_H
#define MOY_WASM_LOAD_H

#include <stdbool.h>
#include <stdint.h>

#include "wasm_export.h"

// After a freeable load of the AOT module `module` from `file` (`len`
// bytes): give the module its own copy -- from the runtime's allocator -- of
// every data segment that still points into `file`, so the file can be freed
// before the module is instantiated. False when a copy cannot be allocated.
bool moy_wasm_own_data(wasm_module_t module, const uint8_t *file, uint32_t len);

#endif
