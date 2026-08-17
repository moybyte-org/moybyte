"""Shared vendor-pin machinery (2026-08-18).

Two suites pin vendored moy-spec copies -- libmoy (test_libmoy_vendor) and the
p8 converter (test_p8_import_vendor) -- and each carried its own sha256 walk,
manifest fixture, checkout probe and pinned-commit gate. The hash/manifest/
checkout core is ONE body here; what stays per-file is the vendor-specific
half: which files, which make target re-vendors, and the drift walk over that
vendor table's own shape.
"""

import hashlib
import json
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path, make_target):
    assert os.path.isfile(path), (
        "no %s -- run `make %s`" % (os.path.relpath(path, ROOT), make_target))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_manifest_not_empty(manifest):
    """A manifest listing nothing passes every other check."""
    assert manifest["files"], "the vendor manifest lists no files"
    assert manifest["upstream"]["commit"], "no upstream commit recorded"


def check_files_match(manifest, what, make_target):
    """Nobody edited the copy: every listed file hashes to its manifest entry."""
    for rel, want in sorted(manifest["files"].items()):
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), "%s is in the manifest but not on disk" % rel
        assert sha256(path) == want, (
            "%s does not match the vendor manifest.\n"
            "This file is a COPY of %s and must not be edited here: fix it "
            "upstream and re-run `make %s`. If you did re-vendor, the manifest "
            "is stale -- run it again to re-stamp." % (rel, what, make_target))


def spec_checkout(probe_rel):
    """A sibling moy-spec checkout that has `probe_rel`, or None."""
    for cand in (os.environ.get("MOYBYTE_MOY_SPEC"),
                 os.path.join(os.path.dirname(ROOT), "moy-spec"),
                 os.path.join(ROOT, ".moy-spec")):
        if cand and os.path.isfile(os.path.join(cand, probe_rel)):
            return cand
    return None


def pinned_spec_or_skip(manifest, probe_rel):
    """The checkout, gated to the manifest's pinned commit -- or a skip.

    Skipped without a checkout (most CI runs, and anyone who only has this
    repo), and skipped when the checkout is at a different commit: that is
    upstream moving, not drift.
    """
    spec = spec_checkout(probe_rel)
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
    return spec
