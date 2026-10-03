"""Every symbol a compiled module calls out to resolves on the board it runs on.

An AOT module is relocated as it loads: each call the compiler emitted to a
helper outside the module -- a soft-float or 64-bit integer routine the core
has no instruction for, a runtime entry such as `aot_set_exception_with_id`
-- names a symbol, and the vendored loader resolves the name in its target's
table (`target_sym_map` in native/moy_wasm/wamr/core/iwasm/aot/arch/
aot_reloc_<arch>.c). A name missing there fails the load with "resolve symbol
<name> failed", and `device/moycore_glue.WasmRun` then plays the cart on the
interpreter (#229: on the P4, f32 -> i64 is `__fixsfdi`, which rv32f has no
instruction for, and the table registered only the int64 -> float pair).

Which helpers a module needs is the COMPILER's decision, so this asks the
compiler: a module of every numeric instruction wasm has -- each conversion
between f32/f64 and i32/u32/i64/u64, trapping and saturating, integer and
float arithmetic and compares, the sign extensions -- plus memory, an import
call and `call_indirect`, is compiled by the pinned wamrc with each console
chip's flags (tools/wasm_module.py, the key's fields), and every symbol its
relocations name must be one the loader resolves: by the loader's own rules
for the module's sections and functions, or in the table as that chip's
build compiles it -- the file run through a C preprocessor with the
definitions native/moy_wasm/micropython.cmake gives the runtime, the core's
config.h defaults and the cross compiler's own predefined macros.

Skips on a bench without the pinned compilers in experiments/wasm_aot/
toolchain/dist (`python3 tools/wasm_module.py compilers` fetches them);
fails under CI, which fetches them, or with MOYBYTE_REQUIRE_WAMRC set.
"""

import os
import re
import shutil
import struct
import subprocess

import pytest

from tools import wasm_module as wm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "native", "moy_wasm", "wamr", "core")
AOT = os.path.join(CORE, "iwasm", "aot")
CMAKE = os.path.join(ROOT, "native", "moy_wasm", "micropython.cmake")

# Each console chip: the micropython.cmake branch its build takes, and the
# cross compiler's predefined macros for the board's -march/-mabi -- every
# __riscv one `riscv32-esp-elf-gcc -march=rv32imafc_zicsr_zifencei_xesppie
# -mabi=ilp32f -dM -E` gives (ESP-IDF v5.5.1, esp-14.2.0_20241119). The
# Xtensa table reads no predefined macro but newlib's, which picks a square
# root's address and never its name.
CHIPS = {
    "esp32p4": {
        "branch": "CONFIG_IDF_TARGET_ESP32P4",
        "predefined": {
            "__riscv": "1", "__riscv_a": "2001000", "__riscv_arch_test": "1",
            "__riscv_atomic": "1", "__riscv_c": "2000000",
            "__riscv_cmodel_medlow": "1", "__riscv_compressed": "1",
            "__riscv_div": "1", "__riscv_f": "2002000", "__riscv_fdiv": "1",
            "__riscv_flen": "32", "__riscv_float_abi_single": "1",
            "__riscv_fsqrt": "1", "__riscv_i": "2001000",
            "__riscv_m": "2000000", "__riscv_misaligned_slow": "1",
            "__riscv_mul": "1", "__riscv_muldiv": "1",
            "__riscv_xesppie": "1000000", "__riscv_xlen": "32",
            "__riscv_zicsr": "2000000", "__riscv_zifencei": "2000000",
        },
    },
    "esp32s3": {
        "branch": "CONFIG_IDF_TARGET_ARCH_XTENSA",
        "predefined": {"__XTENSA__": "1", "__xtensa__": "1", "__NEWLIB__": "4"},
    },
}


# -- the module -------------------------------------------------------------------

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C
_T = {"i32": I32, "i64": I64, "f32": F32, "f64": F64}


def _numeric():
    """(name, opcode bytes, params, results) for every numeric instruction."""
    ops = []

    def add(names, first, params, results):
        for i, name in enumerate(names.split()):
            ops.append((name, bytes([first + i]), params, results))

    cmp_i = "eq ne lt_s lt_u gt_s gt_u le_s le_u ge_s ge_u"
    cmp_f = "eq ne lt gt le ge"
    add("i32.eqz", 0x45, [I32], [I32])
    add(" ".join("i32." + n for n in cmp_i.split()), 0x46, [I32, I32], [I32])
    add("i64.eqz", 0x50, [I64], [I32])
    add(" ".join("i64." + n for n in cmp_i.split()), 0x51, [I64, I64], [I32])
    add(" ".join("f32." + n for n in cmp_f.split()), 0x5B, [F32, F32], [I32])
    add(" ".join("f64." + n for n in cmp_f.split()), 0x61, [F64, F64], [I32])
    bin_i = ("add sub mul div_s div_u rem_s rem_u and or xor shl shr_s shr_u "
             "rotl rotr")
    for t, base in (("i32", 0x67), ("i64", 0x79)):
        add(" ".join(t + "." + n for n in "clz ctz popcnt".split()), base,
            [_T[t]], [_T[t]])
        add(" ".join(t + "." + n for n in bin_i.split()), base + 3,
            [_T[t]] * 2, [_T[t]])
    for t, base in (("f32", 0x8B), ("f64", 0x99)):
        add(" ".join(t + "." + n for n in
                     "abs neg ceil floor trunc nearest sqrt".split()), base,
            [_T[t]], [_T[t]])
        add(" ".join(t + "." + n for n in
                     "add sub mul div min max copysign".split()), base + 7,
            [_T[t]] * 2, [_T[t]])
    conv = [
        ("i32.wrap_i64", "i64", "i32"),
        ("i32.trunc_f32_s", "f32", "i32"), ("i32.trunc_f32_u", "f32", "i32"),
        ("i32.trunc_f64_s", "f64", "i32"), ("i32.trunc_f64_u", "f64", "i32"),
        ("i64.extend_i32_s", "i32", "i64"), ("i64.extend_i32_u", "i32", "i64"),
        ("i64.trunc_f32_s", "f32", "i64"), ("i64.trunc_f32_u", "f32", "i64"),
        ("i64.trunc_f64_s", "f64", "i64"), ("i64.trunc_f64_u", "f64", "i64"),
        ("f32.convert_i32_s", "i32", "f32"), ("f32.convert_i32_u", "i32", "f32"),
        ("f32.convert_i64_s", "i64", "f32"), ("f32.convert_i64_u", "i64", "f32"),
        ("f32.demote_f64", "f64", "f32"),
        ("f64.convert_i32_s", "i32", "f64"), ("f64.convert_i32_u", "i32", "f64"),
        ("f64.convert_i64_s", "i64", "f64"), ("f64.convert_i64_u", "i64", "f64"),
        ("f64.promote_f32", "f32", "f64"),
        ("i32.reinterpret_f32", "f32", "i32"), ("i64.reinterpret_f64", "f64", "i64"),
        ("f32.reinterpret_i32", "i32", "f32"), ("f64.reinterpret_i64", "i64", "f64"),
        ("i32.extend8_s", "i32", "i32"), ("i32.extend16_s", "i32", "i32"),
        ("i64.extend8_s", "i64", "i64"), ("i64.extend16_s", "i64", "i64"),
        ("i64.extend32_s", "i64", "i64"),
    ]
    for i, (name, src, dst) in enumerate(conv):
        ops.append((name, bytes([0xA7 + i]), [_T[src]], [_T[dst]]))
    sat = [("i32", "f32"), ("i32", "f32"), ("i32", "f64"), ("i32", "f64"),
           ("i64", "f32"), ("i64", "f32"), ("i64", "f64"), ("i64", "f64")]
    for i, (dst, src) in enumerate(sat):
        name = "%s.trunc_sat_%s_%s" % (dst, src, "su"[i % 2])
        ops.append((name, b"\xFC" + _uleb(i), [_T[src]], [_T[dst]]))
    return ops


def _memory():
    """(name, opcode bytes, params, results): loads and stores of every
    width, memory.size/grow/copy/fill."""
    ops = []
    loads = [("i32.load", I32, 2), ("i64.load", I64, 3), ("f32.load", F32, 2),
             ("f64.load", F64, 3), ("i32.load8_s", I32, 0),
             ("i32.load8_u", I32, 0), ("i32.load16_s", I32, 1),
             ("i32.load16_u", I32, 1), ("i64.load8_s", I64, 0),
             ("i64.load8_u", I64, 0), ("i64.load16_s", I64, 1),
             ("i64.load16_u", I64, 1), ("i64.load32_s", I64, 2),
             ("i64.load32_u", I64, 2)]
    for i, (name, t, align) in enumerate(loads):
        ops.append((name, bytes([0x28 + i, align, 0]), [I32], [t]))
    stores = [("i32.store", I32, 2), ("i64.store", I64, 3), ("f32.store", F32, 2),
              ("f64.store", F64, 3), ("i32.store8", I32, 0),
              ("i32.store16", I32, 1), ("i64.store8", I64, 0),
              ("i64.store16", I64, 1), ("i64.store32", I64, 2)]
    for i, (name, t, align) in enumerate(stores):
        ops.append((name, bytes([0x36 + i, align, 0]), [I32, t], []))
    ops += [("memory.size", b"\x3F\x00", [], [I32]),
            ("memory.grow", b"\x40\x00", [I32], [I32]),
            ("memory.copy", b"\xFC\x0A\x00\x00", [I32, I32, I32], []),
            ("memory.fill", b"\xFC\x0B\x00", [I32, I32, I32], [])]
    return ops


def _uleb(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _vec(items):
    return _uleb(len(items)) + b"".join(items)


def _section(sid, body):
    return bytes([sid]) + _uleb(len(body)) + body


def _name(s):
    return _uleb(len(s)) + s.encode()


def _runtime():
    """Instructions the runtime serves, each body whole: (name, code, params,
    results). Function 0 is the import; table 0 and the passive element
    segment hold it, and data segment 0 is passive."""
    null = b"\xD0\x70"
    return [
        ("call", b"\x20\x00\x10\x00", [I32], [I32]),
        ("call_indirect", b"\x20\x00\x20\x00\x11\x00\x00", [I32], [I32]),
        ("memory.init", b"\x20\x00\x20\x01\x20\x02\xFC\x08\x00\x00",
         [I32, I32, I32], []),
        ("data.drop", b"\xFC\x09\x00", [], []),
        ("table.get", b"\x20\x00\x25\x00\xD1", [I32], [I32]),
        ("table.set", b"\x20\x00\xD2\x00\x26\x00", [I32], []),
        ("table.size", b"\xFC\x10\x00", [], [I32]),
        ("table.grow", null + b"\x20\x00\xFC\x0F\x00", [I32], [I32]),
        ("table.fill", b"\x20\x00" + null + b"\x20\x01\xFC\x11\x00",
         [I32, I32], []),
        ("table.copy", b"\x20\x00\x20\x01\x20\x02\xFC\x0E\x00\x00",
         [I32, I32, I32], []),
        ("table.init", b"\x20\x00\x20\x01\x20\x02\xFC\x0C\x00\x00",
         [I32, I32, I32], []),
        ("elem.drop", b"\xFC\x0D\x00", [], []),
    ]


def libcall_wasm():
    """A module with one exported function per instruction, each applied to
    the function's parameters so that nothing folds: every numeric
    instruction, every load and store width, and what the runtime serves --
    memory and table operations, an import call, `call_indirect`."""
    types, sigs = [], {}

    def type_of(params, results):
        key = (tuple(params), tuple(results))
        if key not in sigs:
            sigs[key] = len(types)
            types.append(b"\x60" + _vec([bytes([p]) for p in params])
                         + _vec([bytes([r]) for r in results]))
        return sigs[key]

    assert type_of([I32], [I32]) == 0          # the import's, for call_indirect
    fns = [(name, b"".join(b"\x20" + _uleb(i) for i in range(len(params))) + op,
            params, results)
           for name, op, params, results in _numeric() + _memory()]
    fns += _runtime()
    funcs, exports, code = [], [], []
    for i, (name, body, params, results) in enumerate(fns):
        funcs.append(_uleb(type_of(params, results)))
        exports.append(_name(name) + b"\x00" + _uleb(i + 1))
        code.append(_uleb(len(body) + 2) + b"\x00" + body + b"\x0B")
    return (b"\0asm\x01\0\0\0"
            + _section(1, _vec(types))
            + _section(2, _vec([_name("env") + _name("f") + b"\x00\x00"]))
            + _section(3, _vec(funcs))
            + _section(4, _vec([b"\x70\x00\x01"]))
            + _section(5, _vec([b"\x01\x01\x01"]))
            + _section(7, _vec(exports))
            + _section(9, _vec([b"\x01\x00\x01\x00"]))
            + _section(12, _uleb(1))
            + _section(10, _vec(code))
            + _section(11, _vec([b"\x01" + _name("moy")])))


# -- the AOT file's relocations ---------------------------------------------------

def relocation_groups(aot):
    """{section name: [symbol, ...]} from a 32-bit target's AOT file: the
    relocation section's symbol table and groups (aot_emit_aot_file.c's
    layout, the one aot_loader.c's load_relocation_section reads)."""
    def u32(o):
        return struct.unpack_from("<I", aot, o)[0]

    def align4(o):
        return (o + 3) & ~3

    assert aot[:4] == b"\0aot", "not an AOT file"
    o = 8
    while o + 8 <= len(aot):
        o = align4(o)
        kind, size = u32(o), u32(o + 4)
        body = o + 8
        if kind == 5:
            break
        o = body + size
    else:
        raise AssertionError("no relocation section")
    p = body
    count = u32(p)
    offsets = [u32(p + 4 + 4 * i) for i in range(count)]
    p += 4 + 4 * count
    total = u32(p)
    strings = p + 4
    symbols = []
    for off in offsets:
        n = struct.unpack_from("<H", aot, strings + off)[0]
        symbols.append(aot[strings + off + 2:strings + off + 2 + n].rstrip(b"\0").decode())
    p = align4(strings + total)
    ngroups = u32(p)
    p += 4
    groups = {}
    for _ in range(ngroups):
        p = align4(p)
        syms = groups.setdefault(symbols[u32(p)], [])
        n = u32(p + 4)
        p += 8
        for _r in range(n):
            syms.append(symbols[u32(p + 12)])
            p += 16
    return groups


# What aot_loader.c's do_text_relocation resolves without the table, on a
# non-Windows build without static PGO; any other name goes to
# resolve_target_sym. A data section's relocations resolve to the text alone.
TEXT_GROUPS = {".rel.text", ".rela.text", ".rel.ltext", ".rela.ltext",
               ".rela.literal"}
LOADER_SECTIONS = {".text", ".ltext", ".literal", ".data", ".sdata", ".rdata",
                   ".rodata", ".srodata", ".aot_stack_sizes"}
LOADER_PREFIXES = ("aot_func#", ".rodata.cst", ".srodata.cst", ".rodata.str")


def unresolved(groups, table):
    """The symbols the loader would fail on: "resolve symbol %s failed" in a
    text group, "invalid relocation symbol %s" in a data one."""
    missing = set()
    for group, symbols in groups.items():
        for sym in symbols:
            if group not in TEXT_GROUPS:
                if sym not in (".text", ".ltext"):
                    missing.add(sym)
            elif not (sym in LOADER_SECTIONS or sym.startswith(LOADER_PREFIXES)
                      or sym in table):
                missing.add(sym)
    return missing


# -- the table, as a chip's build compiles it ------------------------------------

def _preprocessor():
    for cc in ("cc", "gcc", "clang"):
        if shutil.which(cc):
            return shutil.which(cc)
    return None


def build_config(chip):
    """(reloc file, definitions) the chip's build compiles the table with:
    MOY_WAMR_DEFS, then its arch branch's file and definitions."""
    text = open(CMAKE, encoding="utf-8").read()
    common = re.search(r"set\(MOY_WAMR_DEFS\s+([^)]*)\)", text).group(1).split()
    branch = re.search(r"\n(?:if|elseif)\(%s\)(.*?)\n(?:elseif|else|endif)\("
                       % CHIPS[chip]["branch"], text, re.S).group(1)
    reloc = re.search(r"aot/arch/(aot_reloc_\w+\.c)", branch).group(1)
    extra = [d for m in re.findall(r"list\(APPEND MOY_WAMR_DEFS ([^)]*)\)", branch)
             for d in m.split()]
    defs = [d.replace("${MOY_WASM_FAST_INTERP}", "0") for d in common + extra]
    return reloc, defs


def table(chip):
    """The names in `target_sym_map` as the chip's build compiles it: the
    reloc file up to the table, after config.h and aot_reloc.h's REG_ macros,
    run through a C preprocessor with the build's definitions and the cross
    compiler's predefined macros (and none of the host's)."""
    reloc, defs = build_config(chip)
    src = open(os.path.join(AOT, "arch", reloc), encoding="utf-8").read()
    src = src[:src.index("};", src.index("target_sym_map[]")) + 2]
    header = open(os.path.join(AOT, "aot_reloc.h"), encoding="utf-8").read()
    macros = header[header.index("#define REG_SYM("):
                    header.index("#define CHECK_RELOC_OFFSET")]
    config = open(os.path.join(CORE, "config.h"), encoding="utf-8").read()
    unit = re.sub(r"^\s*#\s*include\b.*$", "", "\n".join((config, macros, src)),
                  flags=re.M)
    predefined = ["%s=%s" % kv for kv in sorted(CHIPS[chip]["predefined"].items())]
    cmd = ([_preprocessor(), "-E", "-P", "-undef", "-nostdinc", "-x", "c", "-"]
           + ["-D" + d for d in defs + predefined])
    out = subprocess.run(cmd, input=unit, capture_output=True, text=True,
                         check=True).stdout
    body = out[out.index("target_sym_map[] = {"):]
    return set(re.findall(r'\{\s*"([^"]+)"\s*,', body[:body.index("};")]))


def _compilers_here():
    return all(os.path.isfile(os.path.join(wm.DIST, p["file"]))
               for p in wm.COMPILERS.values())


def _require(ok, what):
    """A skip on a bench; a failure where CI fetched the compilers
    (`CI`, or MOYBYTE_REQUIRE_WAMRC), the doctrine of tests/unix_mp.py."""
    if ok:
        return
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_WAMRC"):
        pytest.fail("%s -- the guard against a module that cannot load "
                    "would not run" % what)
    pytest.skip(what)


# -- the checks ----------------------------------------------------------------------

@pytest.mark.parametrize("chip", sorted(CHIPS))
def test_the_table_is_read_as_the_chips_build_compiles_it(chip):
    """The evaluation is not vacuous: the common runtime entries are there,
    and on the P4 the single-precision helpers an FPU makes unnecessary are
    not, while the double-precision ones are (rv32f has no D)."""
    _require(_preprocessor(), "no C preprocessor (cc, gcc or clang)")
    names = table(chip)
    assert {"aot_set_exception_with_id", "aot_invoke_native", "memset",
            "sqrtf", "aot_memory_init", "aot_table_grow"} <= names
    if chip == "esp32p4":
        assert {"__adddf3", "__fixdfdi", "__floatdisf", "__udivdi3"} <= names
        assert not {"__addsf3", "__mulsi3", "__udivsi3"} & names


@pytest.mark.parametrize("chip", sorted(CHIPS))
def test_every_symbol_a_module_calls_resolves_on_its_chip(chip, tmp_path):
    """The guard: the pinned compiler, the chip's own flags, every instruction
    -- and nothing the module's relocations name is missing from the table
    the board was built with."""
    _require(_compilers_here(), "no pinned wamrc in experiments/wasm_aot/"
             "toolchain/dist (`python3 tools/wasm_module.py compilers` "
             "fetches them)")
    _require(_preprocessor(), "no C preprocessor (cc, gcc or clang)")
    out = str(tmp_path / ("libcalls_%s.aot" % chip))
    wm.build(libcall_wasm(), chip, out, signed=False)
    with open(out, "rb") as f:
        groups = relocation_groups(f.read())
    called = {s for syms in groups.values() for s in syms}
    assert any(s.startswith("__") for s in called), sorted(called)
    reloc = build_config(chip)[0]
    missing = unresolved(groups, table(chip))
    assert not missing, (
        "a module compiled for %s calls %s, which %s does not register as "
        "that chip builds it: the load fails with 'resolve symbol ... failed' "
        "and the cart plays on the interpreter. Register them in the WAMR "
        "fork and re-vendor (native/moy_wasm/README.md)."
        % (chip, ", ".join(sorted(missing)), reloc))
