/* libmoy's PICO-8 machine (moy_p8.c) compiled the way libmoy_binding.c
 * compiles the Lua binding: the file is VENDORED (tests/test_libmoy_vendor.py
 * reds an edit), so the pragmas a 32-bit Lua trips live here instead.
 * Opt-in upstream; the console opens it in run_begin for every cart, since
 * the p8 shim probes for it nil-safe and nothing else reaches the globals. */
/* A/B 2026-09-02, same lever as moy_gfx's kernels, Mem Bench Lua on all
 * three boards: NULL on the byte verbs (the Lua call is the floor -- P4
 * 1.22 -> 1.19 us, S3 1.95 -> 1.95) and worth it on the bulk verbs (an 8K
 * memcpy P4 2.10 -> 1.63 ms, S3 4.3 -> 4.0), at no cost anywhere. The
 * numbers' home is #66. */
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC optimize("O3")
#endif

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#endif

#include "libmoy/moy_p8.c"

#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
