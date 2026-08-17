"""The vendored libmoy is still what was vendored (#97).

libmoy is moy-spec's C implementation of the console, copied into this tree and
COMPILED IN rather than re-implemented -- SPEC.md 8.3 pins the synthesis to
PICO-8's measured output and deliberately exempts audio from pixel conformance,
so there is no golden frame to catch a drifting twin. Vendoring is what replaces
the missing net, and it only works while the copy is genuinely a copy.

Two ways it stops being one, and a test for each:

  1. Somebody edits the vendored file. It is the fastest way to fix a bug you
     found on a board, it works, and it survives exactly until the next
     re-vendor silently reverts it -- weeks later, in a build nobody connects to
     the change. The manifest's hashes make that a red test on the same day.

  2. Somebody edits moy-spec's copy and does not re-vendor. That one is only
     visible from here if a moy-spec checkout is around, so the test is
     conditional -- and it only fires when the checkout is at the SAME commit
     the manifest names. Upstream having moved on is not drift; it is upstream
     having moved on, which is what a pin is for.
"""

import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, "native")
MANIFEST = os.path.join(NATIVE, "libmoy_vendor.json")


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def manifest():
    assert os.path.isfile(MANIFEST), (
        "no %s -- run `make vendor-libmoy`" % os.path.relpath(MANIFEST, ROOT))
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def test_manifest_is_not_empty(manifest):
    """A manifest listing nothing passes every other test in this file."""
    assert manifest["files"], "the vendor manifest lists no files"
    assert manifest["upstream"]["commit"], "no upstream commit recorded"


def test_vendored_files_match_the_manifest(manifest):
    """Nobody edited the copy."""
    for rel, want in sorted(manifest["files"].items()):
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), "%s is in the manifest but not on disk" % rel
        assert _sha256(path) == want, (
            "%s does not match the vendor manifest.\n"
            "This file is a COPY of moy-spec's libmoy and must not be edited "
            "here: fix it upstream and re-run `make vendor-libmoy`. If you did "
            "re-vendor, the manifest is stale -- run it again to re-stamp." % rel)


def test_every_vendored_file_is_in_the_manifest(manifest):
    """And nobody added one beside it that nothing is checking."""
    listed = set(manifest["files"])
    for dirpath, dirnames, filenames in os.walk(NATIVE):
        if os.path.basename(dirpath) != "libmoy":
            continue
        for name in filenames:
            if name == "UPSTREAM.md":       # ours, not upstream's
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            assert rel in listed, (
                "%s sits in a vendored libmoy directory but is not in the "
                "manifest, so nothing checks it. Add it to tools/"
                "vendor_libmoy.py's VENDOR table, or delete it." % rel)


def _spec_checkout():
    for cand in (os.environ.get("MOYBYTE_MOY_SPEC"),
                 os.path.join(os.path.dirname(ROOT), "moy-spec"),
                 os.path.join(ROOT, ".moy-spec")):
        if cand and os.path.isfile(os.path.join(cand, "libmoy", "include", "moy.h")):
            return cand
    return None


def test_no_drift_from_the_pinned_upstream(manifest):
    """When a moy-spec checkout is at the pinned commit, the bytes agree.

    Skipped without a checkout (most CI runs, and anyone who only has this
    repo), and skipped when the checkout is at a different commit -- that is
    upstream moving, not drift.
    """
    spec = _spec_checkout()
    if not spec:
        pytest.skip("no moy-spec checkout")
    try:
        head = subprocess.check_output(("git", "-C", spec, "rev-parse", "HEAD"),
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pytest.skip("moy-spec checkout is not a git repo")
    if head != manifest["upstream"]["commit"]:
        pytest.skip("moy-spec is at %s, manifest pins %s -- upstream moved"
                    % (head[:12], manifest["upstream"]["commit"][:12]))
    if manifest["upstream"].get("dirty"):
        pytest.skip("vendored from a dirty tree; the commit does not describe it")

    import tools.vendor_libmoy as v
    for dest, files in sorted(v.VENDOR.items()):
        for name, rel in sorted(files.items()):
            src, dst = os.path.join(spec, rel), os.path.join(dest, name)
            if not os.path.isfile(src):
                continue
            assert _sha256(src) == _sha256(dst), (
                "%s differs from moy-spec's %s at the SAME commit -- one side "
                "was edited in place. Re-run `make vendor-libmoy` if upstream "
                "is right; otherwise the edit belongs upstream." % (name, rel))
