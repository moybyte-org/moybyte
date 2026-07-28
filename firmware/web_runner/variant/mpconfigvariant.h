// Moybyte web runner variant (#151), modeled on the pyscript variant (the
// production browser deployment shape):
//  - SPLIT_HEAP_AUTO: gc collections DEFER to the JS<->Python call boundary
//    (external_call_depth == 0), so no emscripten_scan_registers -> no ASYNCIFY.
//    For the runner that lands collections between FRAMES -- the same
//    frame-boundary pattern the device loop uses.
//  - no periodic mp_js_hook (node REPL Ctrl-C plumbing; a call every 10 VM ops).
#define MICROPY_CONFIG_ROM_LEVEL                (MICROPY_CONFIG_ROM_LEVEL_FULL_FEATURES)
#define MICROPY_GC_SPLIT_HEAP                   (1)
#define MICROPY_GC_SPLIT_HEAP_AUTO              (1)
#define MICROPY_PY_WEAKREF                      (1)
#define MICROPY_VARIANT_ENABLE_JS_HOOK          (0)
