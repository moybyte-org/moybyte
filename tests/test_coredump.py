"""Core dumps (docs/os_2026-11/processes.md section 10): the loaded modules'
table a dump carries (native/moy_elf/moy_elf_maps.c, compiled here) read back
by tools/coredump.py's parser, the module's sections placed where the board
put them, the dump's partition found in a board's table, every board's image
configured for a partition its table has, and the console's Settings page
showing a dump and clearing it."""

import ctypes
import os
import re
import struct

import pytest

from runtime import native_build
from runtime import crash_guard
from tools import coredump
from tools.board_flash import _ENTRY

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "native", "moy_elf")


@pytest.fixture(scope="module")
def maps():
    so = native_build.build(
        "moy_elf_maps_host", os.path.join(ELF, "moy_elf_maps.c"), ["moy_elf_maps.h"],
        os.path.join(ROOT, ".build", "host_elf_maps"),
        cflags=native_build.BASE_CFLAGS + ["-Wall", "-Werror"], libmoy_dir=[ELF])
    if so is None:
        pytest.skip("no C compiler")
    lib = ctypes.CDLL(so)
    lib.moy_elf_maps.restype = ctypes.c_void_p
    lib.moy_elf_maps_add.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 4 + [ctypes.c_char_p]
    lib.moy_elf_maps_drop.argtypes = [ctypes.c_void_p]
    return lib


def _table(lib):
    return ctypes.string_at(lib.moy_elf_maps(), 16 + 4 * 52)


def _sha(n):
    return bytes([n]) * 32


def test_the_table_reads_back_from_a_dump_oldest_load_first(maps):
    for key in (1, 2, 3, 4, 5):
        maps.moy_elf_maps_drop(key)
    maps.moy_elf_maps_add(1, 7, 0x0, 0x48010000, 0x2000, _sha(0xA1))
    maps.moy_elf_maps_add(2, 9, 0x100, 0x42010100, 0x800, _sha(0xB2))
    blob = os.urandom(301).replace(b"MOY", b"moy") + _table(maps) + b"\x00" * 64
    got = coredump.parse_maps(blob)
    assert [(m["pid"], m["vaddr"], m["addr"], m["size"]) for m in got] == \
        [(7, 0x0, 0x48010000, 0x2000), (9, 0x100, 0x42010100, 0x800)]
    assert got[0]["sha"] == "a1" * 32 and got[0]["seq"] < got[1]["seq"]
    maps.moy_elf_maps_drop(1)
    assert [m["pid"] for m in coredump.parse_maps(_table(maps))] == [9]
    maps.moy_elf_maps_drop(2)
    assert coredump.parse_maps(_table(maps)) == []


def test_a_full_table_takes_the_oldest_loads_slot(maps):
    for key in range(1, 6):
        maps.moy_elf_maps_add(key, key, 0, 0x48000000 + key, 0x100, _sha(key))
    assert [m["pid"] for m in coredump.parse_maps(_table(maps))] == [2, 3, 4, 5]
    for key in range(1, 6):
        maps.moy_elf_maps_drop(key)


def test_the_slots_cover_every_process():
    h = open(os.path.join(ELF, "moy_elf_maps.h")).read()
    os_h = open(os.path.join(ELF, "moy_os.h")).read()
    slots = int(re.search(r"#define MOY_ELF_MAPS_SLOTS (\d+)u", h).group(1))
    procs = int(re.search(r"#define MOY_OS_PROCS (\d+)u", os_h).group(1))
    assert slots >= procs
    assert re.search(r'#define MOY_ELF_MAPS_MAGIC "(\w+)"', h).group(1).encode() \
        == coredump.MAPS_MAGIC


def test_no_table_reads_as_no_modules():
    assert coredump.parse_maps(b"\xff" * 4096) == []
    assert coredump.parse_maps(coredump.MAPS_MAGIC + b"\x00" * 8) == []


def _elf32(path, sections):
    """A little-endian ELF32 with these (name, addr, size, flags) sections."""
    names = b"\0" + b"".join(n.encode() + b"\0" for n, *_ in sections) + b".shstrtab\0"
    sh = struct.Struct("<IIIIIIIIII")
    shoff = 52 + len(names)
    heads = [sh.pack(*[0] * 10)]
    at = 1
    for n, addr, size, flags in sections:
        heads.append(sh.pack(at, 1, flags, addr, 0, size, 0, 0, 4, 0))
        at += len(n) + 1
    heads.append(sh.pack(at, 3, 0, 0, 52, len(names), 0, 0, 1, 0))
    ident = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    hdr = ident + struct.pack("<HHIIIIIHHHHHH", 3, 94, 1, 0, 0, shoff, 0, 52, 0, 0,
                              sh.size, len(heads), len(heads) - 1)
    with open(path, "wb") as f:
        f.write(hdr + names + b"".join(heads))
    return path


SECTIONS = [(".text", 0x100, 0x400, 0x6), (".rodata", 0x500, 0x80, 0x2),
            (".data", 0x1000, 0x40, 0x3), (".debug_info", 0, 0x900, 0)]


def test_a_p4_segment_places_every_section_at_one_offset(tmp_path):
    path = _elf32(str(tmp_path / "m.elf"), SECTIONS)
    secs = coredump.elf_sections(path)
    assert [s[0] for s in secs] == [".text", ".rodata", ".data"]
    at = coredump.place(secs, {"vaddr": 0, "addr": 0x48200000, "size": 0x1040})
    assert at == {".text": 0x48200100, ".rodata": 0x48200500, ".data": 0x48201000}


def test_an_s3_text_mapping_places_the_text_alone(tmp_path):
    path = _elf32(str(tmp_path / "m.elf"), SECTIONS)
    at = coredump.place(coredump.elf_sections(path),
                        {"vaddr": 0x100, "addr": 0x42380100, "size": 0x400})
    assert at == {".text": 0x42380100}


def test_gdb_loads_a_module_found_by_its_sha_and_names_one_it_lacks(tmp_path):
    sha = "ab" * 32
    _elf32(str(tmp_path / (sha + ".elf")), SECTIONS)
    recs = [{"seq": 1, "pid": 3, "vaddr": 0, "addr": 0x48200000, "size": 0x1040, "sha": sha},
            {"seq": 2, "pid": 4, "vaddr": 0, "addr": 0x48300000, "size": 0x100,
             "sha": "cd" * 32}]
    lines = coredump.gdb_script(recs, [str(tmp_path)]).splitlines()
    assert lines[0] == "set confirm off"
    assert lines[1] == ("add-symbol-file %s 0x48200100 -s .rodata 0x48200500 "
                        "-s .data 0x48201000" % (tmp_path / (sha + ".elf")))
    assert lines[2].startswith("# pid 4: module cdcdcdcdcdcdcdcd at 0x48300000, no symbols")


def _table_sector(entries):
    out = b"".join(_ENTRY.pack(b"\xaa\x50", t, s, off, size, label.encode().ljust(16, b"\0"), 0)
                   for label, t, s, off, size in entries)
    return out + b"\xff" * (0xC00 - len(out))


def test_the_partition_is_found_in_a_boards_table():
    table = _table_sector([("nvs", 1, 2, 0x9000, 0x14000), ("ota_0", 0, 0x10, 0x20000, 0x700000),
                           ("coredump", 1, 3, 0xE20000, 0x20000)])
    assert coredump.coredump_partition(table) == (0xE20000, 0x20000)
    assert coredump.coredump_partition(_table_sector([("nvs", 1, 2, 0x9000, 0x4000)])) is None


def test_an_erased_partition_holds_no_dump():
    assert coredump.dump_length(b"\xff" * 64) is None
    assert coredump.dump_length(struct.pack("<I", 1 << 20) + b"\0" * 60) is None
    assert coredump.dump_length(struct.pack("<I", 40) + b"\0" * 60) == 40


def _csv(board_dir):
    cfg = open(os.path.join(board_dir, "sdkconfig.board")).read()
    name = re.search(r'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="([^"]+)"', cfg).group(1)
    rows = []
    for line in open(os.path.join(board_dir, name)):
        line = line.split("#", 1)[0].strip()
        if line:
            rows.append([c.strip() for c in line.split(",")])
    return cfg, rows


BOARDS = sorted(d for d in __import__("glob").glob(os.path.join(ROOT, "firmware", "*", "boards", "*"))
                if os.path.exists(os.path.join(d, "sdkconfig.board")))


@pytest.mark.parametrize("board_dir", BOARDS, ids=os.path.basename)
def test_an_image_that_dumps_to_flash_has_a_partition_after_its_slots(board_dir):
    cfg, rows = _csv(board_dir)
    dumps = "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y" in cfg.splitlines()
    cores = [r for r in rows if r[1:3] == ["data", "coredump"]]
    assert bool(cores) == dumps, "sdkconfig.board and the partition CSV disagree"
    if not cores:
        return
    (core,) = cores
    ota_1 = next(r for r in rows if r[0] == "ota_1")
    # Right after the second slot, so the store (a listed vfs, or the tail
    # MicroPython builds from the last partition's end) follows it.
    assert int(core[3], 16) == int(ota_1[3], 16) + int(ota_1[4], 16)
    assert int(core[4], 16) % 0x1000 == 0 and int(core[4], 16) >= 0x10000
    vfs = [r for r in rows if r[0] == "vfs"]
    if vfs:
        assert int(vfs[0][3], 16) == int(core[3], 16) + int(core[4], 16)


class _Native:
    def __init__(self, size):
        self.size = size

    def dump(self):
        return self.size

    def clear_dump(self):
        self.size = None
        return True


def test_the_console_reads_the_dump_once_and_clears_it(monkeypatch):
    native = _Native(23456)
    monkeypatch.setattr(crash_guard, "_native", native)
    monkeypatch.setattr(crash_guard, "_dump", [])
    assert crash_guard.core_dump() == 23456
    native.size = 1
    assert crash_guard.core_dump() == 23456          # read once a boot
    assert crash_guard.clear_core_dump() is True
    assert crash_guard.core_dump() is None
    assert crash_guard.crash_lines(None, 23456) == ["NO CRASH RECORDED", "CORE DUMP 23 KB IN FLASH"]


def test_no_kernel_no_dump(monkeypatch):
    monkeypatch.setattr(crash_guard, "_native", None)
    monkeypatch.setattr(crash_guard, "_dump", [])
    assert crash_guard.core_dump() is None
    assert crash_guard.clear_core_dump() is False


def _elf32_symbols(path, symbols):
    """A little-endian ELF32 whose symtab holds these {name: value}."""
    strtab = b"\0" + b"".join(n.encode() + b"\0" for n in symbols)
    syms, at = [b"\0" * 16], 1
    for n, v in symbols.items():
        syms.append(struct.pack("<IIIBBH", at, v, 0, 0, 0, 1))
        at += len(n) + 1
    symtab = b"".join(syms)
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    sh = struct.Struct("<IIIIIIIIII")
    off_sym = 52
    off_str = off_sym + len(symtab)
    off_shs = off_str + len(strtab)
    shoff = off_shs + len(shstr)
    heads = [sh.pack(*[0] * 10), sh.pack(1, 2, 0, 0, off_sym, len(symtab), 2, 1, 4, 16),
             sh.pack(9, 3, 0, 0, off_str, len(strtab), 0, 0, 1, 0),
             sh.pack(17, 3, 0, 0, off_shs, len(shstr), 0, 0, 1, 0)]
    hdr = b"\x7fELF\x01\x01\x01" + b"\0" * 9 + struct.pack(
        "<HHIIIIIHHHHHH", 2, 94, 1, 0, 0, shoff, 0, 52, 0, 0, sh.size, 4, 3)
    with open(path, "wb") as f:
        f.write(hdr + symtab + strtab + shstr + b"".join(heads))
    return path


def test_the_build_refuses_a_table_outside_the_dumps_ram(tmp_path):
    inside = {"_coredump_dram_start": 0x3FCA0000, "_coredump_dram_end": 0x3FCA0100,
              coredump.MAPS_SYMBOL: 0x3FCA0010}
    assert coredump.check_elf(_elf32_symbols(str(tmp_path / "in.elf"), inside)) is None
    outside = dict(inside, **{coredump.MAPS_SYMBOL: 0x3FCB0000})
    assert "did not apply" in coredump.check_elf(_elf32_symbols(str(tmp_path / "out.elf"),
                                                                outside))
    none = {"_coredump_dram_start": 1, "_coredump_dram_end": 1}
    assert coredump.check_elf(_elf32_symbols(str(tmp_path / "zero.elf"), none)) is None


def test_the_symbol_the_build_checks_is_the_tables():
    src = open(os.path.join(ELF, "moy_elf_maps.c")).read()
    assert "static MOY_ELF_MAPS_ATTR moy_elf_maps_t %s =" % coredump.MAPS_SYMBOL in src
