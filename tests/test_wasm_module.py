"""tools/wasm_module.py writes the provenance key the board's check wants.

The board side (native/moy_wasm/modmoy_wasm.c) compares a module's
`moybyte.key` section against "fork <commit>" plus its chip's block in
moy_wasm_key.h, byte for byte. The tool derives the same text and the wamrc
flags from that header and from the vendored fork commit, so the two cannot
disagree by construction; these tests pin the derivation. The on-glass suites
compare the tool's key against the one a live board reports (`moy_wasm.KEY`),
which is the end-to-end half.
"""

import os
import re
import shutil

import pytest

from tools import wasm_module as wm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["target", "cpu", "abi", "features", "opt", "size", "bounds",
          "stack-bounds", "xip"]


def test_the_header_has_a_block_per_console_chip():
    blocks = wm.targets()
    assert sorted(blocks) == ["esp32p4", "esp32s3"]
    for chip, fields in blocks.items():
        assert [n for n, _v in fields] == FIELDS, chip


def test_the_s3_block_keeps_the_large_code_model():
    """The S3 fetches AOT text through a fetch-only alias, so a literal pool
    is unreadable: --size-level=0 is what makes a module run there at all."""
    assert dict(wm.targets()["esp32s3"])["size"] == "0"


def test_the_key_names_the_vendored_fork():
    commit = wm.fork_commit()
    pin = open(os.path.join(ROOT, "native", "moy_wasm", "wamr_pin.h")).read()
    assert '"%s"' % commit in pin
    tail = wm.key_tail("esp32s3")
    assert tail.startswith("fork %s\ntarget xtensa\n" % commit)
    assert tail.endswith("xip 0\n")


def test_the_full_key_leads_with_the_magic_and_the_wasm_hash():
    text = wm.key_text(b"\0asm\x01\0\0\0", "esp32p4")
    lines = text.splitlines()
    assert lines[0] == "moybyte-aot 1"
    assert re.fullmatch(r"wasm [0-9a-f]{64}", lines[1])
    assert text.endswith(wm.key_tail("esp32p4"))


def test_the_flags_are_the_key_s_fields():
    assert wm.wamrc_flags("esp32s3") == [
        "--target=xtensa", "--cpu=esp32s3", "--opt-level=3", "--size-level=0",
        "--bounds-checks=1", "--stack-bounds-checks=1"]
    assert wm.wamrc_flags("esp32p4") == [
        "--target=riscv32", "--cpu=generic-rv32", "--target-abi=ilp32f",
        "--cpu-features=+m,+a,+f,+c", "--opt-level=3", "--size-level=3",
        "--bounds-checks=1", "--stack-bounds-checks=1"]


def test_an_override_changes_the_key_and_the_flags_together():
    assert "size 3\n" in wm.key_tail("esp32s3", override={"size": "3"})
    assert "--size-level=3" in wm.wamrc_flags("esp32s3", override={"size": "3"})
    with pytest.raises(wm.ToolError):
        wm.fields("esp32s3", {"nonsense": "1"})


def test_the_c_check_is_built_from_the_same_header():
    """Routing, not behaviour: the board's expected key is the header's chip
    block after the fork line, which is what key_tail() writes."""
    src = open(os.path.join(ROOT, "native", "moy_wasm", "modmoy_wasm.c")).read()
    assert '"fork " MOY_WASM_FORK_COMMIT "\\n" KEY_TARGET' in src
    for chip in wm.targets():
        assert "#define KEY_TARGET MOY_WASM_KEY_%s" % chip.upper() in src


def test_a_custom_section_round_trips():
    wasm = b"\0asm\x01\0\0\0" + b"\x01\x04\x01\x60\x00\x00"   # one empty type
    out = wm.with_custom_section(wasm, wm.KEY_SECTION, b"payload" * 40)
    secs = wm.sections(out)
    assert secs[0][0] == 1
    assert secs[-1] == (0, wm.KEY_SECTION, b"payload" * 40)
    with pytest.raises(wm.ToolError):
        wm.with_custom_section(out, wm.KEY_SECTION, b"again")


def test_the_compilers_are_pinned_by_hash():
    for target, pin in wm.COMPILERS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]), target
        assert pin["url"].startswith("https://github.com/moybyte-org/"), target


def _compilers_here():
    return all(os.path.isfile(os.path.join(wm.DIST, p["file"]))
               for p in wm.COMPILERS.values())


@pytest.mark.skipif(not _compilers_here(), reason="no pinned wamrc in "
                    "experiments/wasm_aot/toolchain/dist (tools/wasm_module.py "
                    "compilers fetches them)")
@pytest.mark.parametrize("chip", ["esp32s3", "esp32p4"])
def test_the_hello_module_carries_its_key(tmp_path, chip):
    if not (shutil.which("clang") or os.path.isfile(
            os.path.join(wm.SPIKE, "toolchain", "wasi-sdk", "bin", "clang"))):
        pytest.skip("no clang with a wasm32 backend")
    from test_wasm_signing import TEST_KEYS, sign_with_test_key
    out = str(tmp_path / "hello.aot")
    wasm = wm.hello_wasm()
    text = wm.build(wasm, chip, out, sign_with=sign_with_test_key)
    data = open(out, "rb").read()
    assert data[:4] == b"\0aot"
    assert text.encode() in data
    assert wm.verify(data, chip, TEST_KEYS) is None
