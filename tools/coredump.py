#!/usr/bin/env python3
"""Read a board's core dump and decode it, native modules included.

    tools/coredump.py read tdeck [-o dump.bin] [--port P]   the partition, over the cable
    tools/coredump.py info dump.bin --board tdeck            the IDF's report: registers,
                                                             every task's backtrace
    tools/coredump.py gdb dump.bin --board tdeck             gdb on the dump
    tools/coredump.py maps dump.bin [--board tdeck]          the native modules it names
    tools/coredump.py show tdeck [--gdb]                     read, then info (or gdb)
    tools/coredump.py erase tdeck                            erase it over the cable
    tools/coredump.py check-elf micropython.elf              the build's check (below)

A panic writes an ESP-IDF ELF core dump to the board's `coredump` partition
(each board's sdkconfig.board and partition CSV; docs/os_2026-11/processes.md
section 10). `read` takes it off the board with esptool as tools/board_flash.py
reaches the ROM loader: the board's table at 0x8000 says where the partition
is, the dump is cut to the length its header gives, and the board boots again
the way its board.toml `[flash] after` says. A port another process holds is
refused, as tools/board.py refuses it. The console reads the same dump through
`moy_crash.dump()` and Settings -> LAST CRASH clears it.

`info` and `gdb` run the IDF's esp-coredump (inside the IDF's own environment,
its export.sh) against the firmware ELF the image was built from, by default
the board's build tree (`--elf` names another). esp-coredump refuses an ELF
whose sha is not the dump's.

A NATIVE MODULE is relocated into PSRAM, so its pcs mean nothing to gdb by
themselves. The kernel keeps a table of the modules loaded now, each by the
sha256 of its bytes and its code's load address, in internal RAM every dump
carries (native/moy_elf/moy_elf_maps.h). This finds the table in the dump by
its magic, finds each module's unstripped twin by that sha (tools/elf_cart.py
keeps one under .build/sdk/symbols/ for every module it builds; `--symbols`
adds a folder), and hands gdb an `add-symbol-file` per module with each of its
sections at the address the board gave it: the module's frames show its own
functions and lines. On an S3 only its text is mapped (its data sections sit
elsewhere, and PSRAM data is not in a dump); on a P4 the loader's one segment
places every section. Every board build runs `check-elf` on its firmware ELF:
the table must sit between the linker's _coredump_dram_start and _end, or a
dump would not carry it.
"""

import argparse
import glob
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Run bare, the shebang finds the system python, which has no esptool; the
# reads run in sys.executable, so it re-runs itself under the checkout's venv.
_VENV = os.path.join(ROOT, ".venv")
if (__name__ == "__main__" and os.path.exists(os.path.join(_VENV, "bin", "python"))
        and os.path.realpath(sys.prefix) != os.path.realpath(_VENV)):
    os.execv(os.path.join(_VENV, "bin", "python"),
             [os.path.join(_VENV, "bin", "python"), os.path.abspath(__file__)] + sys.argv[1:])

SYMBOLS = os.path.join(ROOT, ".build", "sdk", "symbols")

MAPS_MAGIC = b"MOYMAPS1"                 # moy_elf_maps.h's MOY_ELF_MAPS_MAGIC
_MAPS_HEAD = struct.Struct("<8sII")      # magic, slots, seq
_MAP = struct.Struct("<IIIII32s")        # seq, pid, vaddr, addr, size, sha
MAPS_SLOTS_MAX = 64
MAPS_SYMBOL = "s_moy_elf_maps"           # moy_elf_maps.c's table

PART_TYPE_DATA = 0x01
PART_SUBTYPE_COREDUMP = 0x03
BLANK = 0xFFFFFFFF

_SHF_ALLOC = 0x2


class DumpError(RuntimeError):
    """What stops a step, said so a person can act on it."""


# -- the modules a dump names --------------------------------------------------


def parse_maps(blob):
    """The module records of the first table in `blob` (a raw dump, or any
    bytes holding moy_elf_maps_t): [{seq, pid, vaddr, addr, size, sha}], the
    loaded slots only, oldest load first. [] when no table is found."""
    at = blob.find(MAPS_MAGIC)
    while at >= 0:
        if at + _MAPS_HEAD.size <= len(blob):
            _magic, slots, _seq = _MAPS_HEAD.unpack_from(blob, at)
            end = at + _MAPS_HEAD.size + slots * _MAP.size
            if 0 < slots <= MAPS_SLOTS_MAX and end <= len(blob):
                out = []
                for i in range(slots):
                    seq, pid, vaddr, addr, size, sha = _MAP.unpack_from(
                        blob, at + _MAPS_HEAD.size + i * _MAP.size)
                    if seq:
                        out.append({"seq": seq, "pid": pid, "vaddr": vaddr, "addr": addr,
                                    "size": size, "sha": sha.hex()})
                return sorted(out, key=lambda m: m["seq"])
        at = blob.find(MAPS_MAGIC, at + 1)
    return []


def elf_sections(path):
    """[(name, sh_addr, sh_size)] of the SHF_ALLOC sections of the ELF32 at
    `path` (a module, linked from vaddr 0)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise DumpError("%s is not a 32-bit ELF" % path)
    end = "<" if data[5] == 1 else ">"
    shoff, = struct.unpack_from(end + "I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from(end + "HHH", data, 0x2E)
    sh = struct.Struct(end + "IIIIIIIIII")
    heads = [sh.unpack_from(data, shoff + i * shentsize) for i in range(shnum)]
    stroff = heads[shstrndx][4]

    def name(off):
        z = data.index(b"\0", stroff + off)
        return data[stroff + off:z].decode("ascii", "replace")

    return [(name(h[0]), h[3], h[5]) for h in heads if h[2] & _SHF_ALLOC and h[5]]


def place(sections, rec):
    """{section: runtime address} for each section inside the record's mapping
    (vaddr .. vaddr+size): where the board put it."""
    lo, hi = rec["vaddr"], rec["vaddr"] + rec["size"]
    return {n: rec["addr"] + (a - lo) for n, a, s in sections if lo <= a and a + s <= hi}


def find_symbols(sha, dirs=()):
    """The unstripped module whose stripped twin hashes to `sha`, or None."""
    for d in tuple(dirs) + (SYMBOLS,):
        p = os.path.join(d, sha + ".elf")
        if os.path.exists(p):
            return p
    return None


def gdb_script(maps, dirs=()):
    """The gdb lines that load each recorded module's symbols where the board
    put it, a comment for each one whose symbols are not here."""
    lines = ["set confirm off"]
    for m in maps:
        path = find_symbols(m["sha"], dirs)
        if path is None:
            lines.append("# pid %d: module %s at %#x, no symbols (build it with "
                         "tools/elf_cart.py here, or --symbols DIR)"
                         % (m["pid"], m["sha"][:16], m["addr"]))
            continue
        at = place(elf_sections(path), m)
        if ".text" not in at:
            lines.append("# pid %d: module %s: its .text is outside the recorded mapping"
                         % (m["pid"], m["sha"][:16]))
            continue
        args = ["add-symbol-file", path, "%#x" % at.pop(".text")]
        for n in sorted(at, key=at.get):
            args += ["-s", n, "%#x" % at[n]]
        lines.append(" ".join(args))
    return "\n".join(lines) + "\n"


def elf_symbols(path, names):
    """{name: value} of the named symbols in the ELF32 at `path`'s symtab."""
    with open(path, "rb") as f:
        data = f.read()
    end = "<" if data[5] == 1 else ">"
    shoff, = struct.unpack_from(end + "I", data, 0x20)
    shentsize, shnum = struct.unpack_from(end + "HH", data, 0x2E)
    sh = struct.Struct(end + "IIIIIIIIII")
    heads = [sh.unpack_from(data, shoff + i * shentsize) for i in range(shnum)]
    want, out = {n.encode() for n in names}, {}
    for h in heads:
        if h[1] != 2:                                  # SHT_SYMTAB
            continue
        stroff = heads[h[6]][4]
        for at in range(h[4], h[4] + h[5], 16):
            name, value = struct.unpack_from(end + "II", data, at)
            z = data.index(b"\0", stroff + name)
            n = data[stroff + name:z]
            if n in want:
                out[n.decode()] = value
    return out


def check_elf(path):
    """None when the firmware ELF at `path` puts the modules' table inside
    the internal RAM a core dump carries (or has no table), else why not."""
    got = elf_symbols(path, (MAPS_SYMBOL, "_coredump_dram_start", "_coredump_dram_end"))
    if MAPS_SYMBOL not in got:
        return None
    lo, hi = got.get("_coredump_dram_start"), got.get("_coredump_dram_end")
    at = got[MAPS_SYMBOL]
    if lo is None or not lo <= at < hi:
        return ("%s is at %#x, outside the core dump's internal RAM (%s..%s): its "
                "COREDUMP_DRAM_ATTR did not apply" % (MAPS_SYMBOL, at,
                                                     lo and hex(lo), hi and hex(hi)))
    return None


# -- the dump in flash ---------------------------------------------------------


def dump_length(blob):
    """The length the dump's header gives, or None for an erased partition or
    one whose first word cannot be a dump's."""
    if len(blob) < 4:
        return None
    n, = struct.unpack_from("<I", blob, 0)
    return n if n != BLANK and 16 <= n <= len(blob) else None


def coredump_partition(table):
    """(offset, size) of the coredump partition in a table sector, or None."""
    from tools.board_flash import parse_table
    for _label, typ, sub, off, size in parse_table(table):
        if typ == PART_TYPE_DATA and sub == PART_SUBTYPE_COREDUMP:
            return off, size
    return None


def _board(name):
    from tools import board_config
    from tools.p4_autotest import board_dirs
    dirs = board_dirs(ROOT)
    if name not in dirs:
        raise DumpError("no board %r (have %s)" % (name, ", ".join(sorted(dirs))))
    return dirs[name], board_config.load(dirs[name])


def _port(name, port):
    from tools import board as B
    port = B.stable(port or B.resolve(name, B.boards())[0])
    B.refuse_if_held(port)
    return port


def _flash_args(cfg):
    fl = cfg.get("flash", {})
    return cfg["board"]["chip"], fl.get("baud"), fl.get("stub"), fl.get("before"), \
        fl.get("after", "hard_reset")


def read(name, out, port=None):
    """The board's dump into `out`, cut to its length: the length, or None
    when the partition holds none. The board boots again afterwards."""
    from tools.board_flash import PT_OFFSET, PT_SIZE, _esptool
    _d, cfg = _board(name)
    chip, baud, stub, before, after = _flash_args(cfg)
    port = _port(name, port)
    entry = ["--before", str(before)] if before else []
    with tempfile.TemporaryDirectory() as tmp:
        table = os.path.join(tmp, "table.bin")
        if _esptool(chip, port, baud, *entry, "--after", "no_reset", "read_flash",
                    hex(PT_OFFSET), hex(PT_SIZE), table, stub=stub):
            raise DumpError("esptool could not read %s's partition table" % name)
        with open(table, "rb") as f:
            part = coredump_partition(f.read())
        raw = os.path.join(tmp, "dump.bin")
        if part is None:
            _esptool(chip, port, baud, "--after", str(after), "read_mac", stub=stub)
            raise DumpError("%s's partition table has no coredump partition: the "
                            "board needs a cable flash of an image whose table has one "
                            "(tools/board.py %s flash)" % (name, name))
        if _esptool(chip, port, baud, "--after", str(after), "read_flash",
                    hex(part[0]), hex(part[1]), raw, stub=stub):
            raise DumpError("esptool could not read %s's coredump partition" % name)
        with open(raw, "rb") as f:
            blob = f.read()
    n = dump_length(blob)
    if n is None:
        return None
    with open(out, "wb") as f:
        f.write(blob[:n])
    return n


def erase(name, port=None):
    from tools.board_flash import PT_OFFSET, PT_SIZE, _esptool
    _d, cfg = _board(name)
    chip, baud, stub, before, after = _flash_args(cfg)
    port = _port(name, port)
    entry = ["--before", str(before)] if before else []
    with tempfile.TemporaryDirectory() as tmp:
        table = os.path.join(tmp, "table.bin")
        if _esptool(chip, port, baud, *entry, "--after", "no_reset", "read_flash",
                    hex(PT_OFFSET), hex(PT_SIZE), table, stub=stub):
            raise DumpError("esptool could not read %s's partition table" % name)
        with open(table, "rb") as f:
            part = coredump_partition(f.read())
    if part is None:
        raise DumpError("%s's partition table has no coredump partition" % name)
    if _esptool(chip, port, baud, "--after", str(after), "erase_region",
                hex(part[0]), hex(part[1]), stub=stub):
        raise DumpError("esptool could not erase %s's coredump partition" % name)


# -- decoding ------------------------------------------------------------------


def firmware_elf(name):
    """The firmware ELF of the board's last build in this tree."""
    d, _cfg = _board(name)
    hits = []
    for b in sorted(os.listdir(os.path.join(d, "boards"))):
        hits += glob.glob(os.path.join(d, ".build", "micropython", "ports", "esp32",
                                       "build-%s" % b, "micropython.elf"))
    if not hits:
        raise DumpError("no firmware ELF under %s/.build: build the image the board "
                        "runs (%s/build.sh), or pass --elf" % (d, d))
    return hits[0]


def idf_dir():
    """An ESP-IDF checkout with its export.sh: $IDF_PATH, else a board's."""
    cands = [os.environ.get("IDF_PATH", "")]
    cands += sorted(glob.glob(os.path.join(ROOT, "firmware", "*", ".build", "esp-idf")))
    for c in cands:
        if c and os.path.exists(os.path.join(c, "export.sh")):
            return c
    raise DumpError("no ESP-IDF checkout: build a board first (its build.sh fetches "
                    "one), or set IDF_PATH")


def decode(dump, chip, elf, verb="info_corefile", dirs=(), extra=()):
    """Run esp-coredump's `verb` on `dump` with the modules' symbols loaded:
    its exit status."""
    with open(dump, "rb") as f:
        maps = parse_maps(f.read())
    with tempfile.NamedTemporaryFile("w", suffix=".gdbinit", delete=False) as g:
        g.write(gdb_script(maps, dirs))
        init = g.name
    try:
        for m in maps:
            print("module pid %d sha %s: %#x+%#x at %#x"
                  % (m["pid"], m["sha"][:16], m["vaddr"], m["size"], m["addr"]))
        args = ["--chip", chip, verb, "--core", dump, "--core-format", "raw",
                "--extra-gdbinit-file", init] + list(extra) + [elf]
        script = 'source "$1/export.sh" >/dev/null 2>&1 || exit 2; shift; ' \
                 'exec python -m esp_coredump "$@"'
        return subprocess.call(["bash", "-c", script, "coredump", idf_dir()] + args)
    finally:
        os.unlink(init)


# -- the command line ----------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coredump.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("read", help="the board's dump into a file, over the cable")
    p.add_argument("board")
    p.add_argument("-o", "--out")
    p.add_argument("--port")
    for verb, what in (("info", "the IDF's report on a dump"), ("gdb", "gdb on a dump")):
        p = sub.add_parser(verb, help=what)
        p.add_argument("dump")
        p.add_argument("--board", required=True)
        p.add_argument("--elf", help="the firmware ELF (default: the board's build tree)")
        p.add_argument("--symbols", action="append", default=[],
                       help="a folder of <sha>.elf module twins; repeatable")
    p = sub.add_parser("maps", help="the native modules a dump names, and gdb's lines")
    p.add_argument("dump")
    p.add_argument("--symbols", action="append", default=[])
    p = sub.add_parser("show", help="read the board's dump, then decode it")
    p.add_argument("board")
    p.add_argument("--port")
    p.add_argument("--gdb", action="store_true", help="open gdb instead of the report")
    p.add_argument("--elf")
    p.add_argument("--symbols", action="append", default=[])
    p = sub.add_parser("check-elf", help="the build's check: the modules' table is "
                                         "in the RAM a dump carries")
    p.add_argument("elf")
    p = sub.add_parser("erase", help="erase the board's dump, over the cable")
    p.add_argument("board")
    p.add_argument("--port")
    a = ap.parse_args(argv)
    try:
        if a.verb == "maps":
            with open(a.dump, "rb") as f:
                maps = parse_maps(f.read())
            if not maps:
                print("no native module was loaded")
            sys.stdout.write(gdb_script(maps, a.symbols))
            return 0
        if a.verb == "check-elf":
            why = check_elf(a.elf)
            if why:
                print("!! %s" % why, file=sys.stderr)
                return 1
            return 0
        if a.verb == "erase":
            erase(a.board, a.port)
            print("%s: core dump erased" % a.board)
            return 0
        if a.verb in ("read", "show"):
            out = getattr(a, "out", None) or "coredump-%s.bin" % a.board
            n = read(a.board, out, a.port)
            if n is None:
                print("%s: no core dump in flash" % a.board)
                return 1
            print("%s: %d bytes -> %s" % (a.board, n, out))
            if a.verb == "read":
                return 0
            a.dump = out
        _d, cfg = _board(a.board)
        verb = "dbg_corefile" if (a.verb == "gdb" or getattr(a, "gdb", False)) \
            else "info_corefile"
        return decode(a.dump, cfg["board"]["chip"], a.elf or firmware_elf(a.board), verb,
                      a.symbols)
    except DumpError as e:
        print("coredump: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
