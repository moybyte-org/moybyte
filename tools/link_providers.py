#!/usr/bin/env python3
"""Which object provides each symbol a linked image defines, read from the
link's map, and the guard that no Rust object provides a C library's name.

    tools/link_providers.py check MAP [MAP ...]    exit 1 when one does: the guard
    tools/link_providers.py diff BASE NEW          the symbols whose provider moved
    tools/link_providers.py show MAP [SYMBOL ...]  each symbol's provider

A Rust staticlib bundles compiler_builtins and, on the riscv32 and x86
targets, compiler-rt's C objects, whose copies of memcpy, strlen, sinf or
__popcountsi2 replace the image's own whenever the archive comes first in the
link -- silently, because the names are the same
(native/moy_index/rust/build.sh: THE DROP). Every image's link runs `check`:
the boards' (tools/esp32_build_lib.sh), the browser's
(firmware/web_runner/build.sh) and the desktop MicroPython's (`make
unix-micropython`).

A Rust object is an archive member rustc named (`*.rcgu.o`), or any member of
an archive that holds one, since rustc bundles the C it carries into the same
archive under hashed names. One may define only Rust's own symbols: a mangled
name (`_ZN...`, `_R...`, or demangled with `::`), a local label (`.L...`,
which wasm-ld lists), the runtime's hooks (`__rust_*`, `rust_*`,
`DW.ref.rust_*`) and the C ABI the crate exports
(`moy_*`, or --allow PREFIX). Any other name is a C runtime name the image
already has a provider for.

Two map formats: GNU ld's (ESP-IDF's xtensa and riscv gcc, the host's gcc),
which lists each global symbol under the input section defining it, and
wasm-ld's (emscripten), whose Symbol column sits under its In column.
"""

import argparse
import os
import re
import sys

RUST_OWN = re.compile(r"^(_ZN|_R[0-9A-Z]|__rust_|__rustc|__rg_|__rdl_|__rde_"
                      r"|rust_|DW\.ref\.rust_|\.L)|::")
EXPORTS = ("moy_",)

_GNU_IN = re.compile(r"^ (\S+)\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+(\S+)$")
_GNU_IN_WRAPPED = re.compile(r"^\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+(\S+)$")
_GNU_SYM = re.compile(r"^\s+0x[0-9a-f]+\s+([^\s=]+)$")
_GNU_SECTION_ALONE = re.compile(r"^ (\S+)$")
_WASM_ROW = re.compile(r"^\s*\S+\s+[0-9a-f]+\s+[0-9a-f]+ (.*)$")


def _gnu(lines):
    """{symbol: file} from a GNU ld map's memory map."""
    out = {}
    try:
        start = lines.index("Linker script and memory map")
    except ValueError:
        start = 0
    current = None
    wrapped = False
    for line in lines[start:]:
        if line.startswith("Cross Reference Table"):
            break
        m = _GNU_IN.match(line)
        if m:
            current, wrapped = m.group(2), False
            continue
        if _GNU_SECTION_ALONE.match(line):
            current, wrapped = None, True
            continue
        if wrapped:
            m = _GNU_IN_WRAPPED.match(line)
            wrapped = False
            if m:
                current = m.group(1)
                continue
        m = _GNU_SYM.match(line)
        if m and current is not None:
            out[m.group(1)] = current
            continue
        current = None
    return out


def _wasm(lines):
    """{symbol: file} from a wasm-ld map: a row indented one column further
    than an In row names a symbol that input defines."""
    out = {}
    current = None
    for line in lines[1:]:
        m = _WASM_ROW.match(line)
        if not m:
            continue
        rest = m.group(1)
        depth = len(rest) - len(rest.lstrip(" "))
        if depth == 0:
            current = None
        elif depth <= 8:
            text = rest.strip()
            cut = text.rfind(":(")
            current = text[:cut] if cut > 0 else text
        elif current is not None:
            out[rest.strip()] = current
    return out


def providers(path):
    """{symbol: providing file} for every global symbol the image defines; a
    file is `path` or `archive(member)`, as the map spells it."""
    with open(path, errors="replace") as f:
        lines = f.read().split("\n")
    if lines and lines[0].split()[:3] == ["Addr", "Off", "Size"]:
        return _wasm(lines)
    return _gnu(lines)


def split(file):
    """(archive, member) for `archive(member)`, (file, None) otherwise."""
    m = re.match(r"^(.*\.a)\((.*)\)$", file)
    return (m.group(1), m.group(2)) if m else (file, None)


def rust_files(prov):
    """The files in `prov` that are Rust objects."""
    archives = set()
    for f in set(prov.values()):
        a, mem = split(f)
        if (mem or a).endswith(".rcgu.o"):
            archives.add(a)
    return {f for f in set(prov.values())
            if split(f)[0] in archives or f.endswith(".rcgu.o")}


def swaps(prov, allow=EXPORTS):
    """[(symbol, file)]: the C names a Rust object provides."""
    rust = rust_files(prov)
    return sorted((s, f) for s, f in prov.items()
                  if f in rust and not RUST_OWN.search(s)
                  and not s.startswith(tuple(allow)))


def short(file):
    a, mem = split(file)
    return "%s(%s)" % (os.path.basename(a), mem) if mem else os.path.basename(a)


def diff(base, new):
    """[(symbol, base file, new file)]: symbols both images define whose
    provider is another library or object, paths aside."""
    return sorted((s, short(base[s]), short(new[s])) for s in base.keys() & new.keys()
                  if _lib(base[s]) != _lib(new[s]))


def _lib(file):
    a, mem = split(file)
    name = os.path.basename(a)
    if mem and mem.endswith(".rcgu.o"):
        mem = mem.split("-", 1)[0]        # the crate, not its hash
    return name if mem is None else "%s(%s)" % (name, mem)


def check(paths, allow=EXPORTS, out=None):
    """The guard: 0 when no image has a C name from a Rust object."""
    out = out or sys.stdout
    bad = 0
    for path in paths:
        got = swaps(providers(path), allow)
        if got:
            bad = 1
            print("link_providers: %s: a Rust object provides %d C name%s "
                  "(native/moy_index/rust/build.sh: THE DROP):"
                  % (path, len(got), "" if len(got) == 1 else "s"), file=out)
            for s, f in got:
                print("  %-28s %s" % (s, short(f)), file=out)
    return bad


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("maps", nargs="+")
    c.add_argument("--allow", action="append", default=list(EXPORTS),
                   help="a prefix a Rust object may export (moy_)")
    d = sub.add_parser("diff")
    d.add_argument("base")
    d.add_argument("new")
    s = sub.add_parser("show")
    s.add_argument("map")
    s.add_argument("symbols", nargs="*")
    a = p.parse_args(argv)
    if a.cmd == "check":
        return check(a.maps, tuple(a.allow))
    if a.cmd == "diff":
        rows = diff(providers(a.base), providers(a.new))
        for sym, was, now in rows:
            print("%-28s %s -> %s" % (sym, was, now))
        print("%d symbols changed provider" % len(rows))
        return 0
    prov = providers(a.map)
    for sym in a.symbols or sorted(prov):
        print("%-28s %s" % (sym, short(prov[sym]) if sym in prov else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
