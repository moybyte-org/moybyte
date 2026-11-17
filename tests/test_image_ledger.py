"""The image ledger: a link map and an ELF read into parts, and the ledger
written from records and checked against them (tools/image_ledger.py)."""

import os
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import image_ledger as il                                         # noqa: E402

TDECK = os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline")

MAP = """\
Archive member included to satisfy reference by file (symbol)

 .rodata        0x00000000     0x9999 never/counted.o

Linker script and memory map

.flash.text     0x42000020      0x100
 .text.mp_execute_bytecode
                0x42000020       0x60 esp-idf/main/libmain.a(vm.c.obj)
 .text.moy_ui_draw
                0x42000080       0x40 CMakeFiles/micropython.elf.dir/work/x/firmware/b/native/.staged/moy_ui/moy_ui.c.obj
                0x42000080                moy_ui_draw
 *fill*         0x420000c0       0x20
 .text.esf_buf_alloc
                0x420000e0       0x40 /idf/components/esp_wifi/lib/esp32s3/libpp.a(esf_buf.o)

.flash.rodata   0x3c000020      0x200
 .rodata        0x3c000020       0x80 CMakeFiles/micropython.elf.dir/work/x/firmware/b/native/.staged/moy_web/moy_web_blob.gen.c.obj
 .rodata.mp_qstr_frozen_const_pool
                0x3c0000a0       0x60 esp-idf/main/libmain.a(frozen_content.c.obj)
 .rodata.a.str1.4
                0x3c000100       0x90 esp-idf/main/libmain.a(vm.c.obj)
 .rodata.b.str1.4
                0x3c000100       0x50 esp-idf/mbedtls/mbedtls/library/libmbedcrypto.a(sha256.c.obj)
 .rodata.c.str1.4
                0x3c000140       0x40 esp-idf/hal/libhal.a(gpio_hal.c.obj)
 .rodata.tab    0x3c000180       0x60 /x/lib/esp32s3/no-rtti/libc_nano.a(lib_a-ctype_.o)
 .rodata        0x3c0001e0       0x40 libmoy_wamr.a(wasm_runtime.c.obj)

.dram0.bss      0x3fc90000     0x1000
 .bss.big       0x3fc90000     0x1000 esp-idf/main/libmain.a(gc.c.obj)
"""


def _elf(path, sections):
    """A little-endian ELF32 whose only content is its section headers:
    `sections` is [(name, sh_type, sh_flags, size)]."""
    names = b"\0" + b"".join(n.encode() + b"\0" for n, *_ in sections) + b".shstrtab\0"
    shoff = 52 + len(names)
    heads = [struct.pack("<IIIIIIIIII", *([0] * 10))]
    at = 1
    for n, typ, flags, size in sections:
        heads.append(struct.pack("<IIIIIIIIII", at, typ, flags, 0, 0, size, 0, 0, 4, 0))
        at += len(n) + 1
    heads.append(struct.pack("<IIIIIIIIII", at, 3, 0, 0, 52, len(names), 0, 0, 1, 0))
    hdr = bytearray(52)
    hdr[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", hdr, 0x20, shoff)
    struct.pack_into("<HHH", hdr, 0x2E, 40, len(heads), len(heads) - 1)
    with open(path, "wb") as f:
        f.write(bytes(hdr) + names + b"".join(heads))


@pytest.fixture
def build(tmp_path):
    (tmp_path / "micropython.map").write_text(MAP)
    _elf(str(tmp_path / "micropython.elf"),
         [(".flash.text", 1, 6, 0x100), (".flash.rodata", 1, 2, 0x200),
          (".dram0.bss", 8, 3, 0x1000), (".debug_info", 1, 0, 0x5000)])
    return tmp_path


def test_parts_are_the_loaded_sections_by_object(build):
    got = il.breakdown(str(build / "micropython.map"), str(build / "micropython.elf"),
                       0x300 + 0x40)
    p = got["parts"]
    assert got["loaded"] == 0x300                     # no bss, no debug
    assert p["MicroPython"] == 0x60
    assert p["our C"] == 0x40 + 0x40                  # moy_ui, and WAMR's archive
    assert got["native"] == {"moy_ui": 0x40, "moy_wasm": 0x40}
    assert p["radios: Wi-Fi, BT, lwIP"] == 0x40
    assert p["browser console (baked)"] == 0x80
    assert p["frozen Python console"] == 0x60
    assert p["C library, compiler runtime"] == 0x60
    assert p["image format"] == 0x40
    # Merged strings: three listed at 0x130 bytes, 0x80 by their addresses.
    assert p["C string literals (merged)"] == 0x80
    assert "mbedTLS" not in p
    assert sum(v for k, v in p.items() if k != "image format") == got["loaded"]


def test_the_tdeck_table_reads_its_slot_and_flash():
    rows = il.partitions(TDECK)
    assert il.flash_bytes(TDECK) == 16 << 20
    assert il.slot_bytes(rows) == [r for r in rows if r[0] == "ota_1"][0][4]
    end = max(off + size for _n, _t, _s, off, size in rows)
    assert end <= 16 << 20


def _record(date, image=7_000_000, board="tdeck"):
    return {"format": il.FORMAT, "board": board, "date": date, "commit": "abcd1234",
            "flash": 16 << 20, "slot": 7 << 20, "image": image, "floor": 256 << 10,
            "partitions": [["ota_0", "app", "ota_0", 0x20000, 7 << 20],
                           ["vfs", "data", "fat", 0xE40000, 0x1C0000]],
            "parts": {"browser console (baked)": 1_900_000, "image format": 100},
            "native": {"moy_ui": 300_000}, "loaded": image - 100}


def test_the_ledger_is_what_its_records_give(tmp_path):
    d = tmp_path / "records"
    il.append(_record("2026-11-17T10:00:00"), str(d))
    il.append(_record("2026-11-17T11:00:00", image=7_100_000), str(d))
    out = tmp_path / "image.md"
    assert il.main(["--out", str(out), "--dir", str(d)]) == 0
    text = out.read_text()
    assert text.startswith(il.GENERATED)
    assert "tdeck@2026-11-17T11:00:00" in text.splitlines()[1]
    assert "7,100,000" in text
    # A newer record does not turn it red: the file names the record it took.
    il.append(_record("2026-11-17T12:00:00", image=7_200_000), str(d))
    assert il.main(["--check", "--out", str(out), "--dir", str(d)]) == 0
    out.write_text(text.replace("7,100,000", "7,000,001"))
    assert il.main(["--check", "--out", str(out), "--dir", str(d)]) == 1


def test_check_skips_where_the_records_are_absent(tmp_path):
    d = tmp_path / "records"
    il.append(_record("2026-11-17T10:00:00"), str(d))
    out = tmp_path / "image.md"
    il.main(["--out", str(out), "--dir", str(d)])
    assert il.main(["--check", "--out", str(out), "--dir", str(tmp_path / "none")]) == 0




def test_the_committed_ledger_names_its_records():
    dates = il.file_dates(il.LEDGER)
    assert dates, "docs/perf/image.md names no records: regenerate it"
    assert il.main(["--check"]) == 0
