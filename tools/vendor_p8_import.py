#!/usr/bin/env python3
"""Re-vendor the PICO-8 asset converter from a moy-spec checkout.

    make vendor-p8-import                          # ../moy-spec beside this repo
    make vendor-p8-import SPEC=/path/to/moy-spec
    python3 tools/vendor_p8_import.py --check      # what would change, touch nothing

`p8_import.py` is moy-spec's `.p8`/`.p8.png` -> moy asset converter: sheet, SFX
bank and music tracks. It lives upstream because SPEC.md is what says what the
output must mean -- 8.1 pins `57 = A4 = 440 Hz`, which is what fixes PICO-8's
pitch offset at 24, and 8.1's keyed rest is what makes a ported slide glide from
the right note. libmoy's synth implements the other end of that same contract
(`moy_audio.c`: `p8key = pitch - 24.0f`), and it is vendored here too, so the
converter and the synth must come from ONE upstream version or they agree by
luck.

WHY THIS FILE EXISTS AT ALL. It used to be a hand-copy. Upstream corrected the
pitch offset (0 -> 24, two octaves) and the copy here never heard about it; this
repo's own tests had meanwhile pinned the wrong model, so re-syncing would have
meant deliberately breaking a green test, and nobody did. Every imported cart
played two octaves flat for ten days. Copying by hand worked exactly once; this
makes it a command, and leaves a stamp behind so `tests/test_p8_import_vendor.py`
can notice both halves of the failure -- the copy edited here, and upstream
edited without a re-vendor.

WHY NOT FOLD IT INTO tools/vendor_libmoy.py: that script vendors C that gets
COMPILED into the two boards' firmware, and its test's argument is about audio
having no pixel-conformance golden. This is a host-side Python tool with its own
tests. Same upstream, same idea, different consumers -- and one script whose
--check answer means two different things is worse than two small ones.

WHAT STAYS OURS: `tools/import_p8.py`, the moybyte driver on top -- the CLI, the
`.moy` folder writer, the guided PICO-8 -> Python port notes (#36). It imports
the converter from here; it never re-implements a line of it.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
MANIFEST = os.path.join(TOOLS, "p8_import_vendor.json")

# {vendored name: path in moy-spec}. Explicit, like vendor_libmoy.py's table:
# what we execute should be a decision somebody made, not whatever a glob found.
VENDOR = {
    "p8_import.py": "p8_import.py",
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
        if cand and os.path.isfile(os.path.join(cand, "p8_import.py")):
            return os.path.abspath(cand)
    return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", help="a moy-spec checkout (default: ../moy-spec)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)

    spec = find_spec(args.spec)
    if not spec:
        print("vendor-p8-import: no moy-spec checkout found.\n"
              "  Looked for ../moy-spec, $MOYBYTE_MOY_SPEC and .moy-spec/.\n"
              "  Clone it: git clone https://github.com/moybyte-org/moy-spec",
              file=sys.stderr)
        return 2

    commit = git(spec, "rev-parse", "HEAD") or "?"
    dirty = bool(git(spec, "status", "--porcelain", "--untracked-files=no"))
    date = git(spec, "log", "-1", "--format=%cs") or "?"
    print("vendor-p8-import: %s @ %s%s"
          % (spec, commit[:12], "  (DIRTY)" if dirty else ""))

    changed, missing = [], []
    for name, rel in sorted(VENDOR.items()):
        src, dst = os.path.join(spec, rel), os.path.join(TOOLS, name)
        if not os.path.isfile(src):
            missing.append(rel)
            continue
        if os.path.isfile(dst) and sha256(src) == sha256(dst):
            continue
        changed.append(os.path.relpath(dst, ROOT))
        if not args.check:
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

    # The stamp. `dirty` is recorded rather than refused -- converting a cart
    # against a work-in-progress moy-spec is how a converter change gets tried
    # before it lands upstream. It just must not be invisible afterwards.
    manifest = {
        "upstream": {
            "repo": "moybyte-org/moy-spec",
            "commit": commit,
            "date": date,
            "dirty": dirty,
        },
        "files": {("tools/" + name): sha256(os.path.join(TOOLS, name))
                  for name in sorted(VENDOR)},
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
