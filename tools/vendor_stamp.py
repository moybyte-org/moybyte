#!/usr/bin/env python3
"""Re-stamp a hand-kept vendored copy's hash pin.

    python3 tools/vendor_stamp.py native/moy_lua/lua_vendor.json
    python3 tools/vendor_stamp.py --check native/p4/moy_dsi/ek79007_vendor.json

Some vendored trees come from no script: the copy was made once and is
changed only by hand, each change recorded in the copy's MODIFICATIONS.md.
Their pin (`<name>_vendor.json`) names the upstream release in `upstream`,
the copy's directory in `dir`, and every file of it, MODIFICATIONS.md
excepted, with its sha256 in `files`; tests/test_vendor_pins.py holds the
copy to it. After a recorded change this rewrites `files` from the tree.
"""

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = "MODIFICATIONS.md"


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree(rel_dir, root=ROOT):
    """{repo-relative path: sha256} of every file under `rel_dir` but its record."""
    out = {}
    for dirpath, dirs, names in os.walk(os.path.join(root, rel_dir)):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel != rel_dir + "/" + RECORD:
                out[rel] = sha256(path)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="say what would change; write nothing")
    ap.add_argument("manifest")
    a = ap.parse_args(argv)
    with open(a.manifest, encoding="utf-8") as f:
        pin = json.load(f)
    now = tree(pin["dir"])
    was = pin.get("files", {})
    changed = sorted(set(now) ^ set(was) | {p for p in now if p in was and now[p] != was[p]})
    for p in changed:
        print("%s %s" % ("+" if p not in was else "-" if p not in now else "~", p))
    if a.check:
        return 1 if changed else 0
    pin["files"] = now
    with open(a.manifest, "w", encoding="utf-8") as f:
        json.dump(pin, f, indent=2, sort_keys=False)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
