#!/usr/bin/env python3
"""Re-vendor libmoy from a moy-spec checkout.

    make vendor-libmoy                          # ../moy-spec beside this repo
    make vendor-libmoy SPEC=/path/to/moy-spec
    python3 tools/vendor_libmoy.py --check      # what would change, touch nothing

libmoy is moy-spec's C implementation of the console -- SPEC.md §8's synth today,
and whatever else this repo comes to compile rather than re-implement. Copying it
by hand worked exactly once; this makes it a command, and leaves behind enough
evidence for a test to notice when someone edits the copy instead of the original.

WHY VENDOR AT ALL, rather than a submodule or a package: the two boards' builds
are ESP-IDF component trees that want sources present on disk at fixed relative
paths, and the web runner stages the same directories as usermods. A submodule
would put a network fetch and a detached HEAD in the middle of every build for
64 KB of C. Vendoring is how C libraries have always travelled.

WHY PER-CONSUMER DIRECTORIES rather than one shared native/libmoy/: each module's
build fragment (micropython.cmake / micropython.mk) names sources relative to
ITSELF, and build.sh stages module directories individually into ext_mod/ and
usermods/. A shared sibling would have to be staged too and reached with `../`,
which works until a staging tree flattens. The file sets are disjoint anyway --
the synth needs moy_audio.*, the raster needs moy.h and the canvas sources -- so
nothing is duplicated. What IS shared is the stamp: one commit, one manifest,
recorded in native/libmoy_vendor.json, so the two can never quietly diverge to
different upstream versions.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.vendor_common import (copy_if_changed, open_spec, parse_args,  # noqa: E402
                                 report_changes, sha256, stamp)

NATIVE = os.path.join(ROOT, "native")
MANIFEST = os.path.join(NATIVE, "libmoy_vendor.json")
SPEC_PROBE = os.path.join("libmoy", "include", "moy.h")

# What each consumer takes, as {destination: {vendored name: path in moy-spec}}.
# Deliberately explicit rather than a glob: a glob would silently start vendoring
# whatever upstream adds next, and the point of this file is that what we compile
# is a decision somebody made.
VENDOR = {
    # SPEC.md 8. modmoy_audio.c is a thin MicroPython binding over this; the
    # bank, both sequencers and the mixer are libmoy's. See its UPSTREAM.md.
    os.path.join(NATIVE, "moy_audio", "libmoy"): {
        "moy_audio.c": "libmoy/src/moy_audio.c",
        "moy_audio.h": "libmoy/include/moy_audio.h",
        "LICENSE": "LICENSE",
    },
    # SPEC.md 6/6.1's raster. Only some of moy_gfx's verbs route through this so
    # far (see its UPSTREAM.md for which, and why the rest do not) -- but the
    # whole translation units come over, because taking half a .c file is how a
    # vendored copy stops being a copy.
    os.path.join(NATIVE, "moy_gfx", "libmoy"): {
        "moy.h": "libmoy/include/moy.h",
        "moy_pixel.h": "libmoy/src/moy_pixel.h",
        "moy_canvas.c": "libmoy/src/moy_canvas.c",
        "moy_sprite.c": "libmoy/src/moy_sprite.c",
        "moy_data.c": "libmoy/src/moy_data.c",
        "LICENSE": "LICENSE",
    },
    # SPEC.md 4's Lua binding and the cart loop it drives (moycore stage 2).
    # This is the whole reason stage 1's remaining verb crossings were absorbed
    # rather than written: moy_lua.c registers all 38 spec verbs as C functions
    # against a moy_console, and moy.h exports moy_lua_open/init/update/draw.
    #
    # ONLY the binding comes here. The raster it calls is moy_gfx's copy, which
    # this module's include path points at -- because the two are linked into
    # the SAME binary, and a second compilation of moy_canvas.c would be a
    # duplicate-symbol error rather than a tidier vendoring. Sibling include
    # paths between native modules are already how moy_lua reaches moy_gfx's C
    # API, so this is the established shape and not a new one.
    os.path.join(NATIVE, "moycore", "libmoy"): {
        "moy_lua.c": "libmoy/src/moy_lua.c",
        "moy_p8.c": "libmoy/src/moy_p8.c",
        "LICENSE": "LICENSE",
    },
}


def vendored_files():
    """Every vendored path, repo-relative, in a stable order."""
    out = []
    for dest, files in sorted(VENDOR.items()):
        for name in sorted(files):
            out.append(os.path.relpath(os.path.join(dest, name), ROOT)
                       .replace(os.sep, "/"))
    return out


def main(argv):
    args = parse_args(__doc__, argv)

    found = open_spec("vendor-libmoy", SPEC_PROBE, args.spec)
    if not found:
        return 2
    spec, commit, date, dirty = found

    changed, missing = [], []
    for dest, files in sorted(VENDOR.items()):
        for name, rel in sorted(files.items()):
            src = os.path.join(spec, rel)
            dst = os.path.join(dest, name)
            if not os.path.isfile(src):
                missing.append(rel)
                continue
            if copy_if_changed(src, dst, args.check):
                changed.append(os.path.relpath(dst, ROOT))

    code = report_changes(changed, missing, args.check)
    if code is not None:
        return code

    stamp(MANIFEST, commit, date, dirty,
          {p: sha256(os.path.join(ROOT, p)) for p in vendored_files()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
