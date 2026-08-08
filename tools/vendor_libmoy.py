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

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_micropython", "native")
MANIFEST = os.path.join(NATIVE, "libmoy_vendor.json")

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
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git(spec, *args):
    try:
        return subprocess.check_output(("git", "-C", spec) + args,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def find_spec(explicit=None):
    """A moy-spec checkout: --spec, $MOYBYTE_MOY_SPEC, or a sibling."""
    for cand in (explicit, os.environ.get("MOYBYTE_MOY_SPEC"),
                 os.path.join(os.path.dirname(ROOT), "moy-spec"),
                 os.path.join(ROOT, ".moy-spec")):
        if cand and os.path.isfile(os.path.join(cand, "libmoy", "include", "moy.h")):
            return os.path.abspath(cand)
    return None


def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def vendored_files():
    """Every vendored path, repo-relative, in a stable order."""
    out = []
    for dest, files in sorted(VENDOR.items()):
        for name in sorted(files):
            out.append(os.path.relpath(os.path.join(dest, name), ROOT)
                       .replace(os.sep, "/"))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", help="a moy-spec checkout (default: ../moy-spec)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)

    spec = find_spec(args.spec)
    if not spec:
        print("vendor-libmoy: no moy-spec checkout found.\n"
              "  Looked for ../moy-spec, $MOYBYTE_MOY_SPEC and .moy-spec/.\n"
              "  Clone it: git clone https://github.com/moybyte-org/moy-spec",
              file=sys.stderr)
        return 2

    commit = git(spec, "rev-parse", "HEAD") or "?"
    dirty = bool(git(spec, "status", "--porcelain", "--untracked-files=no"))
    date = git(spec, "log", "-1", "--format=%cs") or "?"
    print("vendor-libmoy: %s @ %s%s" % (spec, commit[:12], "  (DIRTY)" if dirty else ""))

    changed, missing = [], []
    for dest, files in sorted(VENDOR.items()):
        for name, rel in sorted(files.items()):
            src = os.path.join(spec, rel)
            dst = os.path.join(dest, name)
            if not os.path.isfile(src):
                missing.append(rel)
                continue
            same = os.path.isfile(dst) and sha256(src) == sha256(dst)
            if same:
                continue
            changed.append(os.path.relpath(dst, ROOT))
            if not args.check:
                os.makedirs(dest, exist_ok=True)
                shutil.copyfile(src, dst)

    if missing:
        print("  !! not in that checkout: %s" % ", ".join(missing), file=sys.stderr)
        return 2

    for path in changed:
        print("  %s %s" % ("would update" if args.check else "updated", path))
    if not changed:
        print("  already up to date")

    if args.check:
        return 1 if changed else 0

    # The stamp. `dirty` is recorded rather than refused: vendoring from a
    # work-in-progress moy-spec is exactly how a change gets tested on a board
    # before it lands upstream. It just must not be invisible afterwards.
    manifest = {
        "upstream": {
            "repo": "moybyte-org/moy-spec",
            "commit": commit,
            "date": date,
            "dirty": dirty,
        },
        "files": {p: sha256(os.path.join(ROOT, p)) for p in vendored_files()},
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    print("  stamped %s" % os.path.relpath(MANIFEST, ROOT))
    if dirty:
        print("  NOTE: that checkout had uncommitted changes -- this copy "
              "corresponds to no commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
