/* libmoy's PICO-8 machine (moy_p8.c) compiled the way libmoy_binding.c
 * compiles the Lua binding: the file is VENDORED (tests/test_libmoy_vendor.py
 * reds an edit), so the pragmas a 32-bit Lua trips live here instead.
 * Opt-in upstream; the console opens it in run_begin for every cart, since
 * the p8 shim probes for it nil-safe and nothing else reaches the globals. */
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#endif

#include "libmoy/moy_p8.c"

#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
