// NOT vendored: a compilation shim that pulls libmoy's raster into ONE
// translation unit built at -O3. See libmoy/UPSTREAM.md for what is vendored.
//
// WHY THIS FILE EXISTS. modmoy_gfx.c carries an in-source
// `#pragma GCC optimize("O3")` because the ports build usermods at -O2 and the
// cmake route does not reach them (source-file properties are directory-scoped
// and the objects are compiled by the micropython.elf target -- verified via
// build.ninja, see the note in modmoy_gfx.c). A pragma only covers its own
// file, so when six verbs moved from modmoy_gfx.c into calls on libmoy they
// silently dropped from -O3 to -O2. Measured on an ESP32-P4 with the Bench
// cart: `line` went 31.2 -> 62.5 us/op, exactly 2x, and circb/tri/sspr/cls/
// print each picked up 3-10%. The verbs with an algorithmic win (circ's sqrt
// removal, map, the sprite path) stayed ahead regardless, which is why the
// tax went unnoticed.
//
// The pragma cannot go in the vendored sources -- editing those is a red test
// (tests/test_libmoy_vendor.py hashes every one). Including them here instead
// leaves the copies byte-identical to upstream and still gets them compiled at
// the same level as the kernel that calls them.
//
// -O3 and not -Ofast, for the same reason modmoy_gfx.c gives: -ffast-math
// reassociates float arithmetic and this raster is conformance-checked against
// the spec's goldens. The raster is integer throughout today, so there is
// nothing for -Ofast to perturb -- the rule stands because "no float rewriting
// in a conformance-checked raster" is the rule, not because of one call site.
#pragma GCC optimize("O3")

#include "libmoy/moy_canvas.c"
#include "libmoy/moy_sprite.c"
#include "libmoy/moy_data.c"
