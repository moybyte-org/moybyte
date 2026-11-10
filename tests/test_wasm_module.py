"""tools/wasm_module.py writes the provenance key the board's check wants.

The board side (native/moy_wasm/modmoy_wasm.c) compares a module's
`moybyte.key` section against "format <MOY_WASM_FORMAT_VERSION>" plus its
chip's block in moy_wasm_key.h, byte for byte (owner, 2026-09-30, ESP 88: the
key stopped naming the fork commit, which moved on every re-vendor for
reasons that did not touch what an installed module needs from the runtime it
loads into). The tool derives the same text and the wamrc flags from that
header, so the two cannot disagree by construction; these tests pin the
derivation. The on-glass suites compare the tool's key against the one a live
board reports (`moy_wasm.KEY`), which is the end-to-end half.
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


def test_the_key_names_this_trees_format_version_not_the_fork():
    fmt = wm.format_version()
    header = open(os.path.join(ROOT, "native", "moy_wasm", "moy_wasm_key.h")).read()
    assert '#define MOY_WASM_FORMAT_VERSION "%s"' % fmt in header
    tail = wm.key_tail("esp32s3")
    assert tail.startswith("format %s\ntarget xtensa\n" % fmt)
    assert tail.endswith("xip 0\n")
    assert "fork" not in tail


def test_the_fork_commit_is_still_read_for_diagnostics_only():
    """FORK (moy_wasm.FORK) still reports which fork commit the engine was
    vendored from -- useful, but no longer part of the key."""
    commit = wm.fork_commit()
    pin = open(os.path.join(ROOT, "native", "moy_wasm", "wamr_pin.h")).read()
    assert '"%s"' % commit in pin
    assert re.fullmatch(r"[0-9a-f]{40}", commit)


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
        "--cpu-features=+m,+a,+f,+c,+fast-unaligned-access", "--opt-level=3",
        "--size-level=3", "--bounds-checks=1", "--stack-bounds-checks=1"]


def test_an_override_changes_the_key_and_the_flags_together():
    assert "size 3\n" in wm.key_tail("esp32s3", override={"size": "3"})
    assert "--size-level=3" in wm.wamrc_flags("esp32s3", override={"size": "3"})
    with pytest.raises(wm.ToolError):
        wm.fields("esp32s3", {"nonsense": "1"})


def test_the_c_check_is_built_from_the_same_header():
    """Routing, not behaviour: the board's expected key is the header's chip
    block after the format line, which is what key_tail() writes."""
    src = open(os.path.join(ROOT, "native", "moy_wasm", "modmoy_wasm.c")).read()
    assert '"format " MOY_WASM_FORMAT_VERSION "\\n" KEY_TARGET' in src
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


def _alloc_section_aligns(obj):
    """{section name: alignment} of an ELF32 object's allocated sections."""
    import struct
    shoff, = struct.unpack_from("<I", obj, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", obj, 0x2e)
    hdrs = [struct.unpack_from("<10I", obj, shoff + i * shentsize) for i in range(shnum)]
    names = hdrs[shstrndx][4]

    def name(off):
        return obj[names + off:obj.index(b"\0", names + off)].decode()
    return {name(h[0]): h[8] for h in hdrs if h[2] & 0x2}


@pytest.mark.skipif(not _compilers_here(), reason="no pinned wamrc in "
                    "experiments/wasm_aot/toolchain/dist (tools/wasm_module.py "
                    "compilers fetches them)")
@pytest.mark.parametrize("chip", ["esp32s3", "esp32p4"])
def test_every_section_fits_the_alignment_a_mapping_starts_on(tmp_path, chip):
    """The loader places a module's data sections at page-aligned offsets
    from the start of one os_mmap mapping, so a section is only as aligned as
    the mapping, and the compiled code relies on the section's own alignment
    (RISC-V addresses a stack-size entry as the table's %hi with the entry's
    offset in its %lo alone). The vendored ESP-IDF os_mmap's MMAP_ALIGN must
    cover every allocated section these flags emit."""
    if not (shutil.which("clang") or os.path.isfile(
            os.path.join(wm.SPIKE, "toolchain", "wasi-sdk", "bin", "clang"))):
        pytest.skip("no clang with a wasm32 backend")
    import subprocess
    memmap = open(os.path.join(ROOT, "native", "moy_wasm", "wamr", "core", "shared",
                               "platform", "esp-idf", "espidf_memmap.c"),
                  encoding="utf-8").read()
    align = int(re.search(r"#define MMAP_ALIGN (\d+)", memmap).group(1))
    target = dict(wm.fields(chip))["target"]
    for name, wasm in (("hello", wm.hello_wasm()), ("misaligned", wm.misaligned_wasm()),
                       ("conversions", wm.conversions_wasm())):
        src, obj = tmp_path / (name + ".wasm"), tmp_path / (name + ".o")
        src.write_bytes(wasm)
        subprocess.run([wm.compiler(target)] + wm.wamrc_flags(chip)
                       + ["--format=object", "-o", str(obj), str(src)],
                       check=True, capture_output=True, cwd=str(tmp_path))
        aligns = _alloc_section_aligns(obj.read_bytes())
        assert aligns and max(aligns.values()) <= align, (name, chip, aligns)
