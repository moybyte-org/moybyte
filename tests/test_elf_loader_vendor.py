"""The vendored elf_loader is the pinned release plus our patch -- and nothing else.

native/moy_elf/elf_loader/ is made by tools/vendor_elf_loader.py
(native/moy_elf/UPSTREAM.md) and stamped in native/moy_elf/elf_loader_vendor.json.
A change to elf_loader is a change to native/moy_elf/patches/moybyte.patch,
re-vendored; an edit made in the copy survives only until the next re-vendor.
"""

import os

import pytest

from vendor_check import check_files_match, load_manifest, sha256

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "native", "moy_elf")
MANIFEST = os.path.join(MODULE, "elf_loader_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-elf-loader")


@pytest.fixture(scope="module")
def vendor():
    import tools.vendor_elf_loader as v
    return v


def test_manifest_is_not_empty(manifest):
    assert manifest["files"], "the vendor manifest lists no files"
    assert manifest["sha256"] and manifest["version"]


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "elf_loader at the pin plus native/moy_elf/patches",
                      "vendor-elf-loader")


def test_the_manifest_is_the_script_s_table(manifest, vendor):
    assert sorted(manifest["files"]) == sorted(
        "native/moy_elf/elf_loader/" + f for f in vendor.FILES)
    assert (manifest["version"], manifest["sha256"]) == (vendor.VERSION, vendor.SHA256)


def test_every_file_in_the_copy_is_in_the_manifest(manifest):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(os.path.join(MODULE, "elf_loader")):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            assert rel in listed, (
                "%s sits in the vendored elf_loader but not in the manifest; add it to "
                "tools/vendor_elf_loader.py's FILES or delete it" % rel)


def test_the_patch_is_the_stamped_one(manifest):
    """A patch edited without re-vendoring leaves the copy behind it."""
    assert sha256(os.path.join(MODULE, "patches", "moybyte.patch")) == manifest["patch"]
