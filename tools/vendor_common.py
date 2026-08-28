"""What the two moy-spec vendor scripts share.

`vendor_libmoy.py` (C that gets compiled into the boards) and
`vendor_p8_import.py` (a host-side Python tool) stay SEPARATE -- each says why in
its own header, and one script whose --check answer means two different things is
worse than two small ones. What they cannot sensibly differ about is the
mechanism: how a checkout is found, how a copy is compared, what a change report
reads like, and the stamp a re-vendor leaves behind. That lives here, so the only
thing the two can diverge on is what they vendor.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM_REPO = "moybyte-org/moy-spec"


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


def find_spec(probe_rel, explicit=None):
    """A moy-spec checkout: --spec, $MOYBYTE_MOY_SPEC, or a sibling."""
    for cand in (explicit, os.environ.get("MOYBYTE_MOY_SPEC"),
                 os.path.join(os.path.dirname(ROOT), "moy-spec"),
                 os.path.join(ROOT, ".moy-spec")):
        if cand and os.path.isfile(os.path.join(cand, probe_rel)):
            return os.path.abspath(cand)
    return None


def parse_args(doc, argv):
    ap = argparse.ArgumentParser(description=doc.splitlines()[0])
    ap.add_argument("--spec", help="a moy-spec checkout (default: ../moy-spec)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    return ap.parse_args(argv)


def open_spec(tool, probe_rel, explicit=None):
    """`(spec, commit, date, dirty)` for the checkout to vendor from, or None.

    Prints the header line every run leads with; on None it has already said on
    stderr where it looked. `tool` is the make target's name.
    """
    spec = find_spec(probe_rel, explicit)
    if not spec:
        print("%s: no moy-spec checkout found.\n"
              "  Looked for ../moy-spec, $MOYBYTE_MOY_SPEC and .moy-spec/.\n"
              "  Clone it: git clone https://github.com/moybyte-org/moy-spec"
              % tool, file=sys.stderr)
        return None
    commit = git(spec, "rev-parse", "HEAD") or "?"
    dirty = bool(git(spec, "status", "--porcelain", "--untracked-files=no"))
    date = git(spec, "log", "-1", "--format=%cs") or "?"
    print("%s: %s @ %s%s" % (tool, spec, commit[:12], "  (DIRTY)" if dirty else ""))
    return spec, commit, date, dirty


def copy_if_changed(src, dst, check):
    """True when `dst` is not already `src`. Writes nothing under --check."""
    if os.path.isfile(dst) and sha256(src) == sha256(dst):
        return False
    if not check:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    return True


def report_changes(changed, missing, check):
    """The change report. An exit code, or None to carry on and stamp."""
    if missing:
        print("  !! not in that checkout: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    for path in changed:
        print("  %s %s" % ("would update" if check else "updated", path))
    if not changed:
        print("  already up to date")
    if check:
        return 1 if changed else 0
    return None


def stamp(manifest, commit, date, dirty, files):
    """Record what was vendored and from where.

    `dirty` is recorded rather than refused: vendoring from a work-in-progress
    moy-spec is exactly how a change gets tried before it lands upstream. It just
    must not be invisible afterwards.
    """
    with open(manifest, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "upstream": {
                "repo": UPSTREAM_REPO,
                "commit": commit,
                "date": date,
                "dirty": dirty,
            },
            "files": files,
        }, f, indent=2, sort_keys=True)
        f.write("\n")
    print("  stamped %s" % os.path.relpath(manifest, ROOT))
    if dirty:
        print("  NOTE: that checkout had uncommitted changes -- this copy "
              "corresponds to no commit.")
