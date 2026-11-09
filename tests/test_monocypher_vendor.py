"""The vendored Monocypher is the pinned release, and nothing else.

native/moy_net/monocypher/ is made by tools/vendor_monocypher.py
(native/moy_net/monocypher/README.md) and stamped in
native/moy_net/monocypher_vendor.json. Monocypher is carried unpatched: a
change to it is a new release, re-vendored; an edit made in the copy survives
only until the next re-vendor, and these checks make it loud now.
"""

import os

import pytest

from vendor_check import check_files_match, load_manifest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = os.path.join(ROOT, "native", "moy_net", "monocypher")
MANIFEST = os.path.join(ROOT, "native", "moy_net", "monocypher_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-monocypher")


@pytest.fixture(scope="module")
def vendor():
    import tools.vendor_monocypher as v
    return v


def test_manifest_is_not_empty(manifest):
    assert manifest["files"], "the vendor manifest lists no files"
    assert manifest["sha512"] and manifest["version"]


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "Monocypher " + manifest["version"], "vendor-monocypher")


def test_the_manifest_is_the_script_s_table(manifest, vendor):
    assert sorted(manifest["files"]) == sorted(
        "native/moy_net/monocypher/" + f for f in vendor.FILES.values())
    assert (manifest["version"], manifest["sha512"], manifest["sha256"]) == (
        vendor.VERSION, vendor.SHA512, vendor.SHA256)


def test_every_file_in_the_copy_is_in_the_manifest(manifest):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(COPY):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            if rel == "native/moy_net/monocypher/README.md":
                continue
            assert rel in listed, (
                "%s sits in the vendored Monocypher but not in the manifest; add it to "
                "tools/vendor_monocypher.py's FILES or delete it" % rel)
