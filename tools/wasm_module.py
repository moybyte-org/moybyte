#!/usr/bin/env python3
"""Build a per-chip WebAssembly module a board will load: .wasm -> .aot + key.

    python3 tools/wasm_module.py build cart.wasm --chip esp32s3 -o cart_s3.aot
    python3 tools/wasm_module.py hello --chip esp32p4 -o hello_p4.aot
    python3 tools/wasm_module.py key --chip esp32s3          # print the key tail
    python3 tools/wasm_module.py compilers                   # where wamrc is, by hash

A per-architecture module is native code, so a board runs one only if its
provenance key says which wasm it came from, which runtime fork it was
compiled for and with which compiler flags (docs/wasm_tier_plan_2026-09.md,
native/moy_wasm/README.md). This tool is the one place a key is written:

  1. hash the canonical .wasm (the key's `wasm` line);
  2. append the key to a copy as the custom section `moybyte.key`;
  3. run wamrc with exactly the flags the key states, telling it to carry that
     section into the .aot (--emit-custom-sections).

The flags are not written here. They are the chip's block in
native/moy_wasm/moy_wasm_key.h -- the same text the board's check compares
against -- and the fork commit is native/moy_wasm/wamr_vendor.json's, the
runtime the board was built with. A module this tool builds therefore carries
the key the board wants by construction, and a module anything else builds is
refused.

THE COMPILERS are fixed binaries, pinned by sha256 below: the Xtensa one built
by experiments/wasm_aot/toolchain/build_wamrc_xtensa.sh (Espressif's LLVM, the
fork's wamr-compiler), the RISC-V one WAMR's own 2.4.5 release build. They are
looked up in experiments/wasm_aot/toolchain/dist/ (gitignored), fetched from
the fork's release assets into it when absent, and refused when their hash is
not the pin. $MOYBYTE_WAMRC_<TARGET> points at another binary for an
experiment, and says so. No module is ever committed; tests build theirs.

THE HELLO MODULE is experiments/wasm_aot/core6502.c, the spike's 6502 core,
compiled with clang's wasm32 backend and linked by a wasm-ld (wasi-sdk's, or
rust-lld's -flavor wasm, which is what the spike used).
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_HEADER = os.path.join(ROOT, "native", "moy_wasm", "moy_wasm_key.h")
VENDOR_STAMP = os.path.join(ROOT, "native", "moy_wasm", "wamr_vendor.json")
SPIKE = os.path.join(ROOT, "experiments", "wasm_aot")
DIST = os.path.join(SPIKE, "toolchain", "dist")
HELLO_SRC = os.path.join(SPIKE, "core6502.c")

KEY_SECTION = "moybyte.key"
KEY_MAGIC = "moybyte-aot 1\n"

# The compilers, by the key's `target` field. `sha256` is the pin; `url` is
# where a missing binary is fetched from (the fork's release assets).
RELEASE = "https://github.com/moybyte-org/wasm-micro-runtime/releases/download/wamrc-2.4.5-moybyte-1"
COMPILERS = {
    "xtensa": {
        "file": "wamrc-xtensa",
        "sha256": "d002751b22c9f52b11b2601c69f95d8f99c4d1456289962b5bc9e5a2c6318a3d",
        "url": RELEASE + "/wamrc-xtensa",
    },
    "riscv32": {
        "file": "wamrc-riscv32",
        "sha256": "2b9b8b8461e07b21ceedbad87b856fe64ad711e2eb517052a1eb0249584ec72e",
        "url": RELEASE + "/wamrc-riscv32",
    },
}


class ToolError(RuntimeError):
    pass


# -- the key ------------------------------------------------------------------


def targets(header=KEY_HEADER):
    """{chip: [(field, value), ...]} from the key header's blocks, in order."""
    text = open(header, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'#define MOY_WASM_KEY_(\w+) \\\n((?:[ \t]+"[^"\n]*"[ \t]*\\?\n)+)', text):
        chip = m.group(1).lower()
        fields = []
        for lit in re.findall(r'"([^"\n]*)"', m.group(2)):
            assert lit.endswith("\\n"), lit
            name, value = lit[:-2].split(" ", 1)
            fields.append((name, value))
        out[chip] = fields
    return out


def fork_commit(stamp=VENDOR_STAMP):
    with open(stamp, encoding="utf-8") as f:
        return json.load(f)["upstream"]["commit"]


def fields(chip, override=None):
    """The chip's key fields in order, with `override` ({name: value}) applied
    -- how a test builds a module with flags this board's build does not want."""
    blocks = targets()
    if chip not in blocks:
        raise ToolError("no key block for %r in moy_wasm_key.h (have %s)"
                        % (chip, ", ".join(sorted(blocks))))
    out = list(blocks[chip])
    for name, value in (override or {}).items():
        idx = [i for i, (n, _v) in enumerate(out) if n == name]
        if not idx:
            raise ToolError("no key field %r" % name)
        out[idx[0]] = (name, value)
    return out


def key_tail(chip, fork=None, override=None):
    """Everything after the key's wasm line, exactly as the board compares it."""
    lines = ["fork %s" % (fork or fork_commit())]
    lines += ["%s %s" % kv for kv in fields(chip, override)]
    return "\n".join(lines) + "\n"


def key_text(wasm, chip, fork=None, override=None):
    return (KEY_MAGIC + "wasm %s\n" % hashlib.sha256(wasm).hexdigest()
            + key_tail(chip, fork, override))


def wamrc_flags(chip, override=None):
    """The wamrc command line the key's fields state."""
    f = dict(fields(chip, override))
    args = ["--target=%s" % f["target"]]
    if f["cpu"] != "-":
        args.append("--cpu=%s" % f["cpu"])
    if f["abi"] != "-":
        args.append("--target-abi=%s" % f["abi"])
    if f["features"] != "-":
        args.append("--cpu-features=%s" % f["features"])
    args += ["--opt-level=%s" % f["opt"], "--size-level=%s" % f["size"],
             "--bounds-checks=%s" % f["bounds"],
             "--stack-bounds-checks=%s" % f["stack-bounds"]]
    if f["xip"] == "1":
        args.append("--xip")
    return args


# -- the wasm container --------------------------------------------------------


def _leb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_leb(data, i):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def sections(wasm):
    """[(id, name or None, payload)] of a wasm binary."""
    if wasm[:8] != b"\0asm\x01\0\0\0":
        raise ToolError("not a wasm binary (version 1)")
    out, i = [], 8
    while i < len(wasm):
        sid = wasm[i]
        size, j = _read_leb(wasm, i + 1)
        body = wasm[j:j + size]
        name = None
        if sid == 0:
            nlen, k = _read_leb(body, 0)
            name = body[k:k + nlen].decode()
            body = body[k + nlen:]
        out.append((sid, name, body))
        i = j + size
    return out


def with_custom_section(wasm, name, payload):
    """`wasm` with one more custom section appended."""
    if any(sid == 0 and n == name for sid, n, _b in sections(wasm)):
        raise ToolError("the wasm already carries a %r section" % name)
    nb = name.encode()
    body = _leb(len(nb)) + nb + payload
    return wasm + b"\x00" + _leb(len(body)) + body


# -- the compilers --------------------------------------------------------------


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compiler(target, fetch=True):
    """Path to the pinned wamrc for `target`, fetched into DIST if absent."""
    override = os.environ.get("MOYBYTE_WAMRC_%s" % target.upper())
    if override:
        print("wasm_module: using $MOYBYTE_WAMRC_%s=%s -- not the pinned "
              "compiler" % (target.upper(), override), file=sys.stderr)
        return override
    pin = COMPILERS[target]
    path = os.path.join(DIST, pin["file"])
    if not os.path.isfile(path):
        if not fetch:
            raise ToolError("no %s in %s" % (pin["file"], DIST))
        os.makedirs(DIST, exist_ok=True)
        tmp = path + ".part"
        try:
            urllib.request.urlretrieve(pin["url"], tmp)
        except Exception as exc:  # noqa: BLE001
            raise ToolError("no %s in %s and fetching %s failed: %s"
                            % (pin["file"], DIST, pin["url"], exc))
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    got = sha256_file(path)
    if got != pin["sha256"]:
        raise ToolError("%s hashes to %s, the pin is %s -- not the compiler "
                        "this tree names" % (path, got, pin["sha256"]))
    return path


# -- building -------------------------------------------------------------------


def build(wasm, chip, out, key=True, fork=None, override=None):
    """Compile `wasm` (bytes) for `chip` into `out`. Returns the key text.

    `fork` and `override` build a module this tree's boards must REFUSE: a key
    naming another runtime, or flags (and a key saying so) the board's build
    does not want."""
    target = dict(fields(chip, override))["target"]
    text = key_text(wasm, chip, fork, override)
    src = with_custom_section(wasm, KEY_SECTION, text.encode()) if key else wasm
    with tempfile.TemporaryDirectory() as tmp:
        wpath = os.path.join(tmp, "in.wasm")
        with open(wpath, "wb") as f:
            f.write(src)
        cmd = [compiler(target)] + wamrc_flags(chip, override)
        if key:
            cmd.append("--emit-custom-sections=%s" % KEY_SECTION)
        cmd += ["-o", out, wpath]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(out):
            raise ToolError("wamrc failed (%s):\n%s%s"
                            % (" ".join(cmd), r.stdout, r.stderr))
    return text if key else None


def _linker():
    """A wasm linker as an argv prefix, and the env it needs."""
    wasi = os.path.join(SPIKE, "toolchain", "wasi-sdk", "bin", "wasm-ld")
    if os.path.isfile(wasi):
        return [wasi], None
    found = shutil.which("wasm-ld")
    if found:
        return [found], None
    for lld in sorted(glob.glob(os.path.expanduser(
            "~/.rustup/toolchains/*/lib/rustlib/*/bin/rust-lld"))):
        libdir = os.path.join(lld.split("/lib/rustlib/")[0], "lib")
        env = dict(os.environ, LD_LIBRARY_PATH=libdir)
        return [lld, "-flavor", "wasm"], env
    raise ToolError("no wasm linker: wasi-sdk (experiments/wasm_aot/toolchain), "
                    "wasm-ld on PATH, or a rustup toolchain's rust-lld")


def hello_wasm():
    """The spike's 6502 core as a wasm module (bytes)."""
    clang = os.path.join(SPIKE, "toolchain", "wasi-sdk", "bin", "clang")
    if not os.path.isfile(clang):
        clang = shutil.which("clang")
    if not clang:
        raise ToolError("no clang with a wasm32 backend")
    link, env = _linker()
    with tempfile.TemporaryDirectory() as tmp:
        obj, out = os.path.join(tmp, "core6502.o"), os.path.join(tmp, "core6502.wasm")
        for cmd, e in (([clang, "--target=wasm32", "-O2", "-ffreestanding",
                         "-fno-builtin", "-c", "-o", obj, HELLO_SRC], None),
                       (link + ["--no-entry", "--export-dynamic", "-o", out, obj], env)):
            r = subprocess.run(cmd, capture_output=True, text=True, env=e)
            if r.returncode != 0:
                raise ToolError("%s failed:\n%s" % (cmd[0], r.stderr))
        with open(out, "rb") as f:
            return f.read()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="compile a .wasm for a chip")
    b.add_argument("wasm")
    h = sub.add_parser("hello", help="build the hello module (core6502) for a chip")
    for p in (b, h):
        p.add_argument("--chip", required=True, choices=sorted(targets()))
        p.add_argument("-o", "--out", required=True)
        p.add_argument("--no-key", action="store_true",
                       help="leave the key out (a module the board must refuse)")
        p.add_argument("--fork", help="write another fork commit into the key "
                       "(a module the board must refuse)")
        p.add_argument("--field", action="append", default=[], metavar="NAME=VALUE",
                       help="build with another value for a key field, e.g. "
                       "size=3 (a module the board must refuse); repeatable")
    k = sub.add_parser("key", help="print the key tail a chip's build wants")
    k.add_argument("--chip", required=True, choices=sorted(targets()))
    sub.add_parser("compilers", help="locate and verify the pinned compilers")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "key":
            sys.stdout.write(key_tail(args.chip))
            return 0
        if args.cmd == "compilers":
            for target in sorted(COMPILERS):
                print("%-8s %s" % (target, compiler(target)))
            return 0
        wasm = hello_wasm() if args.cmd == "hello" else open(args.wasm, "rb").read()
        override = dict(f.split("=", 1) for f in args.field)
        text = build(wasm, args.chip, args.out, key=not args.no_key,
                     fork=args.fork, override=override)
        print("%s: %d bytes%s" % (args.out, os.path.getsize(args.out),
                                  "" if text is None else ", wasm %s"
                                  % hashlib.sha256(wasm).hexdigest()[:16]))
        return 0
    except ToolError as exc:
        print("wasm_module: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
