#!/usr/bin/env python3
"""Try every patches/*.patch against the pinned upstream trees.

    tools/patch_check.py --mpy DIR --idf DIR

A patch is checked with `git apply --check --cached` in the tree it patches,
which reads the checkout's index: the build's patchers (`patch`,
tools/patch_*.py) write only the working tree, so a built and patched tree
still answers for the stock release. A tree must be at its pin first
(tools/upstream_pins.env): MicroPython's HEAD the tag's commit, ESP-IDF's
tools/cmake/version.cmake the release, and the index HEAD's own tree. A
patch's paths say whose it is: `components/...` is ESP-IDF's, every other
path MicroPython's.

Every patch is tried and reported, so one run names every patch a bump
breaks. Exit status 0 when all apply, 1 when one does not or a tree is not at
its pin.
"""

import argparse
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import upstream_pins  # noqa: E402

PATCHES = os.path.join(ROOT, "patches")
TREES = ("mpy", "idf")
NAMES = {"mpy": "MicroPython", "idf": "ESP-IDF"}


def paths(text):
    """The files a unified diff writes, without their a/ b/ prefixes."""
    out = []
    for line in text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].split("\t")[0].strip()
            if p != "/dev/null":
                out.append(p[2:] if p.startswith("b/") else p)
    return out


def tree_of(text):
    """"idf" or "mpy": whose sources the patch edits."""
    ps = paths(text)
    if not ps:
        raise ValueError("it names no file")
    kinds = {"idf" if p.startswith("components/") else "mpy" for p in ps}
    if len(kinds) > 1:
        raise ValueError("it edits both trees: %s" % ", ".join(ps))
    return kinds.pop()


def _git(tree, *args):
    return subprocess.run(("git", "-C", tree) + args, capture_output=True, text=True)


def idf_release(cmake_text):
    v = dict(re.findall(r"^set\(IDF_VERSION_(MAJOR|MINOR|PATCH) (\d+)\)", cmake_text, re.M))
    if len(v) != 3:
        return None
    return "v%s.%s.%s" % (v["MAJOR"], v["MINOR"], v["PATCH"])


def at_pin(kind, tree, pins):
    """None when `tree` holds the pinned release in its index, else why not."""
    if not os.path.isdir(tree):
        return "%s is not there" % tree
    if _git(tree, "rev-parse", "--git-dir").returncode:
        return "%s is not a git checkout" % tree
    if kind == "mpy":
        tag = pins["MOYBYTE_MPY_TAG"]
        want = _git(tree, "rev-parse", "-q", "--verify", "refs/tags/%s^{commit}" % tag).stdout.strip()
        head = _git(tree, "rev-parse", "HEAD").stdout.strip()
        if not want or want != head:
            return "%s is not at MicroPython %s" % (tree, tag)
    else:
        rel = pins["MOYBYTE_IDF_VER"]
        have = idf_release(_git(tree, "show", ":tools/cmake/version.cmake").stdout)
        if have != rel:
            return "%s is ESP-IDF %s, not %s" % (tree, have or "of no known release", rel)
    if _git(tree, "diff", "--cached", "--quiet", "HEAD").returncode:
        return "%s has staged changes; its index is not the release" % tree
    return None


def check(trees, patches=None, pins=None):
    """[(patch, tree kind or None, error or None)] for every patch."""
    pins = upstream_pins.load() if pins is None else pins
    patches = sorted(glob.glob(os.path.join(PATCHES, "*.patch"))) if patches is None else patches
    bad = {k: at_pin(k, trees[k], pins) for k in TREES if trees.get(k)}
    out = []
    for p in patches:
        with open(p, encoding="utf-8") as f:
            text = f.read()
        try:
            kind = tree_of(text)
        except ValueError as e:
            out.append((p, None, str(e)))
            continue
        if not trees.get(kind):
            out.append((p, kind, "no %s tree given" % NAMES[kind]))
        elif bad[kind]:
            out.append((p, kind, bad[kind]))
        else:
            r = _git(trees[kind], "apply", "--check", "--cached", os.path.abspath(p))
            out.append((p, kind, (r.stderr or r.stdout).strip() or None if r.returncode else None))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mpy", required=True, help="a MicroPython checkout")
    ap.add_argument("--idf", required=True, help="an ESP-IDF checkout")
    a = ap.parse_args(argv)
    pins = upstream_pins.load()
    rel = {"mpy": pins["MOYBYTE_MPY_TAG"], "idf": pins["MOYBYTE_IDF_VER"]}
    results = check({"mpy": a.mpy, "idf": a.idf}, pins=pins)
    failed = 0
    for p, kind, err in results:
        what = "%s %s" % (NAMES[kind], rel[kind]) if kind else "?"
        print("%-4s %-48s %s" % ("ok" if err is None else "FAIL", os.path.basename(p), what))
        if err:
            failed += 1
            for line in err.splitlines():
                print("       " + line)
    print("%d of %d patches apply" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
