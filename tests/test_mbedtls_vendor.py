"""The vendored mbedTLS subset is still the release the boards' ESP-IDF carries.

native/moy_net/mbedtls/ is the crypto the host's library and the browser's
wasm compile for the sign-in (native/moy_net/mbedtls/README.md); a board links
ESP-IDF's own copy of the same release. tools/vendor_mbedtls.py copies it and
stamps native/moy_net/mbedtls_vendor.json, and an edit made here would make
the host and the wasm run crypto no board runs. These checks make it loud.
"""

import os

import pytest

from vendor_check import check_files_match, check_manifest_not_empty, load_manifest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "native", "moy_net", "mbedtls_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-mbedtls")


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "mbedTLS " + manifest["upstream"]["version"],
                      "vendor-mbedtls")


def test_the_manifest_is_the_script_s_table(manifest):
    import tools.vendor_mbedtls as v
    assert manifest["upstream"]["version"] == v.VERSION
    assert sorted(manifest["files"]) == sorted("native/moy_net/mbedtls/" + f
                                               for f in v.FILES)


def test_every_file_in_the_copy_is_in_the_manifest(manifest):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(os.path.join(ROOT, "native", "moy_net", "mbedtls")):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            if rel == "native/moy_net/mbedtls/README.md":
                continue
            assert rel in listed, "%s is in the copy but not the manifest" % rel
