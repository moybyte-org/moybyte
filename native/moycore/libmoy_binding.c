/* The vendored Lua binding, compiled as one translation unit with the two
 * diagnostics a 32-bit Lua necessarily trips turned off.
 *
 * Same shape and same reason as moy_gfx's libmoy_kernels.c: a pragma that has
 * to apply to a VENDORED source cannot live in that source (editing the copy is
 * a red test -- tests/test_libmoy_vendor.py hashes it), and it cannot go in the
 * build fragment either, because py.mk folds CFLAGS_USERMOD in at its include
 * and the ports append their own -Wall AFTER that, which re-enables exactly
 * what the fragment tried to silence.
 *
 * What is silenced, and why it is not a bug being hidden: LUA_32BITS makes
 * lua_Number a float, because both boards' FPUs are single-precision and
 * doubles would be soft-float (moybyte's luaconf.h, 6ddaf7c). libmoy's binding
 * pushes and reads cart numbers through lua_Number, so every one of those is a
 * float<->double conversion that -Wdouble-promotion and -Wfloat-conversion
 * report. The conversions ARE the point of a 32-bit Lua; the warnings are the
 * port telling us we chose it.
 */

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#endif

#include "libmoy/moy_lua.c"

#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
