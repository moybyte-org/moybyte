"""The vendored WAMR runtime is still the fork's code at the pinned commit.

native/moy_wasm/wamr/ is the AOT-plus-interpreter subset of
moybyte-org/wasm-micro-runtime (branch moybyte-2.4.5), copied by
tools/vendor_wamr.py and stamped in
native/moy_wasm/wamr_vendor.json. The runtime is carried as a fork pinned by
hash (docs/wasm_tier_plan_2026-09.md), so a fix belongs in the fork and
arrives here by re-vendoring; an edit made here survives only until the next
re-vendor silently reverts it. These are the checks that make it loud.
"""

import hashlib
import os
import re
import subprocess

import pytest

from vendor_check import check_files_match, check_manifest_not_empty, load_manifest, sha256

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "native", "moy_wasm")
MANIFEST = os.path.join(MODULE, "wamr_vendor.json")


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST, "vendor-wamr")


def test_manifest_is_not_empty(manifest):
    check_manifest_not_empty(manifest)
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["upstream"]["commit"])


def test_vendored_files_match_the_manifest(manifest):
    check_files_match(manifest, "the WAMR fork", "vendor-wamr")


def test_the_manifest_is_the_script_s_table(manifest):
    import tools.vendor_wamr as v
    assert sorted(manifest["files"]) == v.vendored_paths()


def test_every_file_in_the_copy_is_in_the_manifest(manifest):
    listed = set(manifest["files"])
    for dirpath, _dirs, names in os.walk(os.path.join(MODULE, "wamr")):
        for name in names:
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT).replace(os.sep, "/")
            assert rel in listed, (
                "%s sits in the vendored runtime but not in the manifest; add it "
                "to tools/vendor_wamr.py's FILES or delete it" % rel)


def test_the_copy_carries_no_compiler_or_wasi_or_libc(manifest):
    """AOT plus the interpreter tier (2026-09-30, "A cart survives its
    firmware"): no compiler, no WASI, no builtin libc, no JIT. Headers the AOT
    code shares types through may cross, the compilation tier's sources may
    not."""
    for rel in manifest["files"]:
        if not rel.endswith((".c", ".s", ".S")):
            continue
        for banned in ("/compilation/", "/libc-wasi/",
                       "/libc-builtin/", "/fast-jit/", "/samples/", "/tests/"):
            assert banned not in rel, rel


def test_the_interpreter_tier_is_classic_plus_fast_plus_the_loader(manifest):
    """Both of WAMR's interpreters are vendored (micropython.cmake compiles
    exactly one, chosen by MOY_WASM_FAST_INTERP, the same shape as the two
    reloc arches) alongside the plain .wasm loader -- never the mini loader,
    the same full-validation decision the AOT build already made."""
    interp = {rel for rel in manifest["files"] if "/interpreter/" in rel}
    for rel in ("core/iwasm/interpreter/wasm_loader.c",
                "core/iwasm/interpreter/wasm_runtime.c",
                "core/iwasm/interpreter/wasm_interp_classic.c",
                "core/iwasm/interpreter/wasm_interp_fast.c"):
        assert "native/moy_wasm/wamr/" + rel in interp, rel
    assert not any("mini_loader" in rel for rel in interp)


def test_one_pin_everywhere(manifest):
    """The commit the copy came from is the one the provenance check compares
    against and the one the spike's build.sh clones."""
    commit = manifest["upstream"]["commit"]
    pin_h = open(os.path.join(MODULE, "wamr_pin.h"), encoding="utf-8").read()
    assert '#define MOY_WASM_FORK_COMMIT "%s"' % commit in pin_h
    build_sh = open(os.path.join(ROOT, "experiments", "wasm_aot", "build.sh"),
                    encoding="utf-8").read()
    m = re.search(r'WAMR_PIN="\$\{WAMR_PIN:-([0-9a-f]{40})\}"', build_sh)
    assert m and m.group(1) == commit, (
        "experiments/wasm_aot/build.sh pins %s, the vendored copy is %s"
        % (m.group(1) if m else None, commit))


def _clone():
    for cand in (os.environ.get("MOYBYTE_WAMR"),
                 os.path.join(ROOT, "experiments", "wasm_aot", "wamr")):
        if cand and os.path.isdir(os.path.join(cand, ".git")):
            return cand
    return None


def test_the_copy_is_the_fork_at_the_pinned_commit(manifest):
    """Read from the clone's git OBJECTS at the pinned commit, so the check
    holds whatever the clone's working tree or HEAD is doing."""
    clone = _clone()
    if clone is None:
        pytest.skip("no clone of the WAMR fork (experiments/wasm_aot/wamr)")
    commit = manifest["upstream"]["commit"]
    if subprocess.run(["git", "-C", clone, "cat-file", "-e", commit + "^{commit}"],
                      capture_output=True).returncode != 0:
        pytest.skip("the clone does not have %s" % commit[:12])
    for rel in sorted(manifest["files"]):
        fork_rel = os.path.relpath(os.path.join(ROOT, rel),
                                   os.path.join(MODULE, "wamr")).replace(os.sep, "/")
        data = subprocess.check_output(["git", "-C", clone, "show",
                                        "%s:%s" % (commit, fork_rel)])
        assert hashlib.sha256(data).hexdigest() == sha256(os.path.join(ROOT, rel)), (
            "%s differs from the fork's %s at %s -- fix it in the fork and "
            "re-run `make vendor-wamr`" % (rel, fork_rel, commit[:12]))
