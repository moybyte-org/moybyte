"""The vendored Jet, the film's code, and the carts' derived data are still upstream's.

ports/jet/jet/ is the subset of CubeCoders' Jet the Jet carts compile;
ports/jet/examples/ is ESP 88's code from JetExamples with the one change
tools/vendor_jet.py's PATCHES records; ports/jet/teapot.moy/teapot.obj,
ports/jet/esp88.moy/assets.bin and both carts' LICENSES.txt are derived from
JetExamples -- all by tools/vendor_jet.py and stamped in
ports/jet/jet_vendor.json. A fix to Jet or the film belongs upstream and
arrives here by re-vendoring; an edit made here survives only until the next
re-vendor silently reverts it. These are the checks that make it loud.
"""

import os
import re
import subprocess

import pytest

from vendor_check import check_files_match, check_manifest_not_empty, load_manifest, sha256

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "ports", "jet", "jet_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-jet")


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["upstream"]["commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["upstream"]["examples"]["commit"])


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "Jet or JetExamples", "vendor-jet")


def test_the_manifest_is_the_script_s_table(manifest):
    import tools.vendor_jet as v
    assert sorted(manifest["files"]) == v.vendored_paths()


def test_the_pin_is_the_script_s(manifest):
    """One pin: the script's JetExamples commit is the stamped one, so a bare
    `make vendor-jet` reproduces the copy."""
    import tools.vendor_jet as v
    assert manifest["upstream"]["examples"]["commit"] == v.EXAMPLES_COMMIT


@pytest.mark.parametrize("copy", ("jet", "examples"))
def test_every_file_in_the_copy_is_in_the_manifest(manifest, copy):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(os.path.join(ROOT, "ports", "jet", copy)):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            assert rel in listed, (
                "%s sits in the vendored copy but not in the manifest; add it to "
                "tools/vendor_jet.py's FILES or FILM_FILES, or delete it" % rel)


def test_the_film_carries_one_change():
    """PATCHES' text is in the vendored file it names, once, and the text it
    replaced is gone: the one place the film's code differs from upstream."""
    from tools import vendor_jet
    assert list(vendor_jet.PATCHES) == [vendor_jet.FILM_DIR + "/World.hpp"]
    for rel, changes in vendor_jet.PATCHES.items():
        with open(os.path.join(ROOT, "ports", "jet", "examples", rel), encoding="utf-8") as f:
            text = f.read()
        for old, new in changes:
            assert text.count(new) == 1 and old not in text, rel


def _declared(header):
    """{name: bytes} of every array the cart's Assets.hpp declares."""
    with open(header, encoding="utf-8") as f:
        text = f.read()
    out = {}
    for ctype, body in re.findall(r"inline (uint8_t|uint16_t) (.*?);", text, re.S):
        width = 1 if ctype == "uint8_t" else 2
        for name, dims in re.findall(r"(\w+)\[([^\]]+)\]", body):
            n = 1
            for d in dims.split("*"):
                n *= int(d)
            out[name] = n * width
    return out


def test_assets_bin_fills_what_the_cart_declares():
    """assets.bin holds a record for every array the cart's Assets.hpp
    declares, none larger than its array (the textures exactly its size), and
    the credits at the size CreditMask.hpp gives them."""
    from tools import vendor_jet
    src = os.path.join(ROOT, "ports", "jet", "esp88.moy", "src")
    with open(os.path.join(ROOT, "ports", "jet", "esp88.moy", "assets.bin"), "rb") as f:
        records = vendor_jet.read_assets(f.read())
    declared = _declared(os.path.join(src, "Assets.hpp"))
    assert set(records) == set(declared) | {"credits"}
    for name, size in declared.items():
        got = len(records[name])
        assert 0 < got <= size, (name, got, size)
        if not name.endswith("Palette"):
            assert got == size, (name, got, size)
    with open(os.path.join(src, "CreditMask.hpp"), encoding="utf-8") as f:
        dims = dict(re.findall(r"#define FILM_CREDITS_([WH]) (\d+)", f.read()))
    num, den = vendor_jet.FILM_SCALE
    assert (int(dims["W"]), int(dims["H"])) == (vendor_jet.CREDITS_W * num // den,
                                                vendor_jet.CREDITS_H * num // den)
    assert len(records["credits"]) == int(dims["W"]) * int(dims["H"])


def test_the_model_round_trips_exactly():
    """Every coordinate in teapot.obj is an integer over the scale Jet's loader
    multiplies it back by, so the loaded mesh is the example's integers."""
    from tools import vendor_jet
    with open(os.path.join(ROOT, "ports", "jet", "teapot.moy", "teapot.obj")) as f:
        lines = f.read().splitlines()
    v = [ln.split()[1:] for ln in lines if ln.startswith("v ")]
    vn = [ln.split()[1:] for ln in lines if ln.startswith("vn ")]
    faces = [ln for ln in lines if ln.startswith("f ")]
    assert (len(v), len(vn), len(faces)) == (822, 822, 1560)
    for row, scale in ((v, vendor_jet.POSITION_SCALE), (vn, vendor_jet.NORMAL_SCALE)):
        for xyz in row:
            for c in xyz:
                assert float(c) * scale == int(float(c) * scale), c


def _clones():
    base = os.path.join(ROOT, ".build", "jet")
    jet = os.environ.get("MOYBYTE_JET") or os.path.join(base, "Jet")
    examples = os.environ.get("MOYBYTE_JET_EXAMPLES") or os.path.join(base, "JetExamples")
    if not (os.path.isdir(os.path.join(jet, ".git"))
            and os.path.isdir(os.path.join(examples, ".git"))):
        return None
    return jet, examples


def _has(clone, commit):
    return subprocess.run(["git", "-C", clone, "cat-file", "-e", commit + "^{commit}"],
                          capture_output=True).returncode == 0


def test_the_copy_is_upstream_at_the_pinned_commits(manifest):
    """Re-derive every file from the clones' git OBJECTS at the pins -- the
    Jet commit read from JetExamples' submodule, not from the stamp -- and
    compare, whatever the clones' working trees or HEADs are doing."""
    clones = _clones()
    if clones is None:
        pytest.skip("no clones of Jet and JetExamples (.build/jet/; see "
                    "tools/vendor_jet.py)")
    jet, examples = clones
    up = manifest["upstream"]
    if not (_has(examples, up["examples"]["commit"]) and _has(jet, up["commit"])):
        pytest.skip("the clones do not have the pinned commits")
    from tools import vendor_jet
    files, upstream = vendor_jet.from_clones(jet, examples, up["examples"]["commit"])
    assert upstream["commit"] == up["commit"], (
        "JetExamples at %s pins Jet %s, the stamp says %s"
        % (up["examples"]["commit"][:12], upstream["commit"][:12], up["commit"][:12]))
    assert sorted(files) == sorted(manifest["files"])
    for rel, data in sorted(files.items()):
        got = sha256(os.path.join(ROOT, rel))
        assert vendor_jet.sha256_bytes(data) == got, (
            "%s differs from what upstream at the pins derives -- fix it upstream "
            "and re-run `make vendor-jet`" % rel)
