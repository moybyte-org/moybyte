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

import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = os.path.join(ROOT, "native")
MANIFEST = os.path.join(NATIVE, "libmoy_vendor.json")


from vendor_check import (check_files_match, check_manifest_not_empty,
                          load_manifest, pinned_spec_or_skip, sha256)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-libmoy")


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "moy-spec's libmoy", "vendor-libmoy")


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


def test_no_drift_from_the_pinned_upstream(manifest):
    """When a moy-spec checkout is at the pinned commit, the bytes agree
    (vendor_check.pinned_spec_or_skip carries the skip ladder)."""
    spec = pinned_spec_or_skip(manifest,
                               os.path.join("libmoy", "include", "moy.h"))
    import tools.vendor_libmoy as v
    for dest, files in sorted(v.VENDOR.items()):
        for name, rel in sorted(files.items()):
            src, dst = os.path.join(spec, rel), os.path.join(dest, name)
            if not os.path.isfile(src):
                continue
            assert sha256(src) == sha256(dst), (
                "%s differs from moy-spec's %s at the SAME commit -- one side "
                "was edited in place. Re-run `make vendor-libmoy` if upstream "
                "is right; otherwise the edit belongs upstream." % (name, rel))
