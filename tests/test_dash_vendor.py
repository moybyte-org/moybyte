"""The vendored dash is upstream at the pin, plus our patch, plus the
generators' output -- and nothing else.

native/moy_sh/dash/ is made by tools/vendor_dash.py (native/moy_sh/UPSTREAM.md)
and stamped in native/moy_sh/dash_vendor.json. A change to dash is a change to
native/moy_sh/patches/, re-vendored; an edit made in the copy survives only
until the next re-vendor silently reverts it. These checks make it loud.
"""

import os
import re
import subprocess

import pytest

from vendor_check import check_files_match, check_manifest_not_empty, load_manifest, sha256

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "native", "moy_sh")
MANIFEST = os.path.join(MODULE, "dash_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-dash")


@pytest.fixture(scope="module")
def vendor():
    import tools.vendor_dash as v
    return v


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["upstream"]["commit"])


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "dash at the pin plus native/moy_sh/patches", "vendor-dash")


def test_the_manifest_is_the_script_s_table(manifest, vendor):
    assert sorted(manifest["files"]) == vendor.vendored_paths()
    assert manifest["upstream"]["commit"] == vendor.PIN


def test_every_file_in_the_copy_is_in_the_manifest(manifest):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(os.path.join(MODULE, "dash")):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            assert rel in listed, (
                "%s sits in the vendored dash but not in the manifest; add it to "
                "tools/vendor_dash.py's FILES or delete it" % rel)


def test_the_patches_are_the_stamped_ones(manifest, vendor):
    """A patch edited without re-vendoring leaves the copy behind it."""
    stamped = manifest["patches"]
    on_disk = {os.path.relpath(p, ROOT).replace(os.sep, "/"): sha256(p)
               for p in vendor.patch_files()}
    assert stamped == on_disk, "native/moy_sh/patches changed: run `make vendor-dash`"


def test_the_generated_files_are_the_generators_output_over_the_copy(manifest, vendor):
    """token.h, builtins.c, nodes.c, syntax.c and init.c are made from the
    patched sources beside them: regenerated here, byte for byte."""
    dash = os.path.join(MODULE, "dash")

    def src(name):
        with open(os.path.join(dash, name), encoding="utf-8") as f:
            return f.read()

    made = {}
    made["token.h"], made["token_vars.h"] = vendor.mktokens(src("mktokens"))
    made["builtins.c"], made["builtins.h"] = vendor.mkbuiltins(src("builtins.def.in"))
    made["nodes.c"], made["nodes.h"] = vendor.mknodes(src("nodetypes"), src("nodes.c.pat"))
    made["syntax.c"], made["syntax.h"] = vendor.mksyntax(src("parser.h"))
    made["init.c"] = vendor.mkinit([(n, src(n)) for n in vendor.INIT_SOURCES])
    for name, text in made.items():
        assert src(name) == text, "%s is not its generator's output: run `make vendor-dash`" % name


def test_the_copy_carries_the_licence_and_no_gpl_helper(manifest):
    """dash is BSD-3-Clause (COPYING); mksignames.c, the one GPL file in its
    tree, is not vendored (the signal names are moy_sh_sys.c's)."""
    files = manifest["files"]
    assert "native/moy_sh/dash/COPYING" in files
    assert not any(f.endswith("mksignames.c") or f.endswith("signames.c") for f in files)


def test_a_clone_at_the_pin_rebuilds_the_copy(manifest, vendor):
    """With a dash clone at hand (.build/dash or $MOYBYTE_DASH): the pin, the
    patches and the generators make exactly the copy."""
    clone = os.environ.get("MOYBYTE_DASH", vendor.DEFAULT_CLONE)
    if not os.path.isdir(os.path.join(clone, ".git")):
        pytest.skip("no dash clone")
    try:
        subprocess.check_output(["git", "-C", clone, "cat-file", "-e",
                                 manifest["upstream"]["commit"] + "^{commit}"],
                                stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pytest.skip("the clone lacks the pinned commit")
    built = vendor.build(clone, manifest["upstream"]["commit"])
    for rel, data in built.items():
        with open(os.path.join(MODULE, "dash", rel), "rb") as f:
            assert f.read() == data, "%s differs from a rebuild at the pin" % rel
