#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Re-vendor the Jet carts from moybyte-org/carts into ports/jet.

    make vendor-jet-carts                         # ../carts at its HEAD
    make vendor-jet-carts CARTS_REPO=/path/to/carts COMMIT=<sha>
    python3 tools/vendor_jet_carts.py --check     # what would change

Jet Teapot and ESP 88 live in moybyte-org/carts, the carts repository, which
builds and publishes them. This repository keeps a copy of each cart's folder
because its tests and on-glass guards build them -- the host frame goldens,
the heap and two-core checks in tests/test_jet_cart.py, and every console
board's `jet_holds_its_floor` -- and a build here never fetches
(tools/vendor_libmoy.py says why). The copy is ports/jet/<cart>.moy/: every
file of the carts repository's carts/<id>/ but its own cart.json, recipe.py
and README.md, read from the clone's git OBJECTS at the commit, never its
working tree. A change to a cart belongs in the carts repository and arrives
here by re-vendoring; the stamp, ports/jet/jet_carts_vendor.json, is what
tests/test_jet_vendor.py holds the copy to.

What stays this repository's own under ports/jet: Jet and the film's code
(tools/vendor_jet.py, from CubeCoders), the build (tools/jet_cart.py, which
the carts' recipe also runs, at a pinned moybyte commit) and the README.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = os.path.join(ROOT, "ports", "jet")
STAMP = os.path.join(PORT, "jet_carts_vendor.json")
REPO = "moybyte-org/carts"
DEFAULT_CLONE = os.environ.get("MOYBYTE_CARTS") or os.path.join(
    os.path.dirname(ROOT), "carts")

# The carts repository's cart id -> the folder here.
CARTS = {"teapot": "teapot.moy", "esp88": "esp88.moy"}
# The carts repository's own files in a cart folder, never the cart's.
REPO_FILES = ("cart.json", "recipe.py", "README.md")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git(clone, *args, binary=False):
    r = subprocess.run(["git", "-C", clone] + list(args), capture_output=True)
    if r.returncode != 0:
        raise SystemExit("vendor-jet-carts: git %s: %s"
                         % (" ".join(args), r.stderr.decode(errors="replace").strip()))
    return r.stdout if binary else r.stdout.decode().strip()


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def from_clone(clone, commit="HEAD"):
    """({repo-relative path here: bytes}, upstream) at `commit`."""
    commit = git(clone, "rev-parse", commit + "^{commit}")
    date = git(clone, "log", "-1", "--format=%cs", commit)
    files = {}
    for cart_id, folder in sorted(CARTS.items()):
        top = "carts/%s/" % cart_id
        names = git(clone, "ls-tree", "-r", "--name-only", commit, "--", top).split("\n")
        names = [n for n in names if n]
        if not names:
            raise SystemExit("vendor-jet-carts: %s has no %s at %s" % (clone, top, commit[:12]))
        for name in names:
            inner = name[len(top):]
            if inner in REPO_FILES:
                continue
            files[rel(os.path.join(PORT, folder, *inner.split("/")))] = git(
                clone, "show", "%s:%s" % (commit, name), binary=True)
    return files, {"repo": REPO, "commit": commit, "date": date}


def vendored_now():
    """Every file under the cart folders here, repo-relative."""
    out = []
    for folder in CARTS.values():
        for dirpath, _dirs, names in os.walk(os.path.join(PORT, folder)):
            out += [rel(os.path.join(dirpath, n)) for n in names]
    return sorted(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--carts", default=DEFAULT_CLONE,
                    help="a clone of %s (default: $MOYBYTE_CARTS, else ../carts)" % REPO)
    ap.add_argument("--commit", default="HEAD", help="the commit to vendor (default: HEAD)")
    ap.add_argument("--check", action="store_true", help="report what would change; write nothing")
    args = ap.parse_args(argv)
    if not os.path.isdir(os.path.join(args.carts, ".git")):
        print("vendor-jet-carts: no clone at %s\n  git clone https://github.com/%s.git %s"
              % (args.carts, REPO, args.carts), file=sys.stderr)
        return 2
    files, upstream = from_clone(args.carts, args.commit)
    print("vendor-jet-carts: %s @ %s" % (args.carts, upstream["commit"][:12]))

    changed = []
    for path, data in sorted(files.items()):
        dst = os.path.join(ROOT, path)
        if os.path.isfile(dst):
            with open(dst, "rb") as f:
                if f.read() == data:
                    continue
        changed.append(path)
        if not args.check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    for path in vendored_now():
        if path not in files:
            changed.append(path + " (removed)")
            if not args.check:
                os.remove(os.path.join(ROOT, path))
    for path in changed:
        print("  %s %s" % ("would update" if args.check else "updated", path))
    if not changed:
        print("  already up to date")
    if args.check:
        return 1 if changed else 0
    with open(STAMP, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"upstream": upstream,
                   "files": {p: sha256_bytes(d) for p, d in files.items()}},
                  f, indent=2, sort_keys=True)
        f.write("\n")
    print("  stamped %s" % rel(STAMP))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
