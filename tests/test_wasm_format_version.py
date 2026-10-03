"""The compiled-code FORMAT VERSION covers exactly what it says it covers.

docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware" (2026-09-30,
ESP 88): a module's key names MOY_WASM_FORMAT_VERSION
(native/moy_wasm/moy_wasm_key.h), not the WAMR fork commit, so a re-vendor no
longer stales every cart already installed on a board. The format is only
supposed to move when a change actually reaches what makes an old module
unsafe or wrong on a new runtime -- the AOT loader and runtime ABI, or the
pinned compiler -- and native/moy_wasm/wasm_format_version.json names exactly
which files and compiler pins that is. This is the guard: it fails the moment
one of them changes without the stamp (and, by the same commit, the version)
moving with it.
"""

import json
import os

import pytest

import tools.wasm_module as wm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP_FILE = os.path.join(ROOT, "native", "moy_wasm", "wasm_format_version.json")


@pytest.fixture(scope="module")
def spec():
    with open(STAMP_FILE, encoding="utf-8") as f:
        return json.load(f)


def test_the_stamp_file_is_well_formed(spec):
    assert spec["covers"], "wasm_format_version.json names no files to cover"
    assert spec["compilers"] == sorted(spec["compilers"])
    for target in spec["compilers"]:
        assert target in wm.COMPILERS
    for rel in spec["covers"]:
        assert rel.startswith("native/moy_wasm/wamr/"), rel
        assert os.path.isfile(os.path.join(ROOT, rel)), rel


def test_the_stamp_matches_the_covered_files_and_compiler_pins(spec):
    """The guard: change any covered file or compiler pin and this fails
    until `python3 tools/wasm_module.py format-stamp --write` (or a hand
    edit) records the new stamp -- which is the prompt to also bump
    MOY_WASM_FORMAT_VERSION in moy_wasm_key.h, in the same commit."""
    got = wm.format_stamp()
    assert got == spec["stamp"], (
        "native/moy_wasm/wasm_format_version.json's stamp is stale: the "
        "files and compiler pins it covers hash to %s now, not %s. If this "
        "change reaches what a module needs from the runtime it loads into, "
        "bump MOY_WASM_FORMAT_VERSION in moy_wasm_key.h too; either way, "
        "re-run `python3 tools/wasm_module.py format-stamp --write`."
        % (got, spec["stamp"]))


def test_the_covered_files_are_all_in_the_wamr_vendor_manifest():
    """Every format-relevant file is also a file tools/vendor_wamr.py
    actually copies from the fork -- the format's scope cannot name a file
    that is not vendored (or is authored here, which config.h and version.h
    are not)."""
    with open(os.path.join(ROOT, "native", "moy_wasm", "wamr_vendor.json"),
             encoding="utf-8") as f:
        vendored = set(json.load(f)["files"])
    with open(STAMP_FILE, encoding="utf-8") as f:
        covers = json.load(f)["covers"]
    for rel in covers:
        assert rel in vendored, "%s is not a vendored file" % rel


def test_the_format_version_is_a_bare_integer_string():
    v = wm.format_version()
    assert v.isdigit() and v == str(int(v)), v


def test_the_key_tail_names_the_format_version_not_a_fork_commit():
    tail = wm.key_tail("esp32s3")
    assert tail.startswith("format %s\n" % wm.format_version())
    assert "fork " not in tail
