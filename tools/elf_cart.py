#!/usr/bin/env python3
"""Build a native (ELF) cart: its C, compiled once per chip against the OS
table, into a `.moy` a console runs (docs/os_2026-11/tiers.md section 1,
sdk/README.md).

    python3 tools/elf_cart.py tests/fixtures/elf/elf_cat.moy /tmp/cat.moy --chip esp32s3 --chip esp32p4
    python3 tools/elf_cart.py SRC DST --chip host          # the host's, for its tests
    python3 tools/elf_cart.py SRC DST --chip esp32s3 --unsigned
    python3 tools/elf_cart.py module a.c b.c --chip esp32p4 -o out.elf

A cart in this tree carries its SOURCE, `src/*.c`; this compiles it per chip
with that chip's toolchain (the ESP-IDF's, from ~/.espressif) into
`main.<chip>.elf` beside the manifest, writes the chips into the manifest's
`"moybyte": {"native": [...]}`, and copies everything else as it is. A module
is a relocatable ELF that imports nothing: it links the SDK's runtime,
libmoyos.a (sdk/src/: the entry, and a stub per row of the OS table that calls
it by ordinal), and the rest of the C library statically from the
toolchain's own (memcpy, strlen, the soft-float helpers), every one of its
symbols hidden. The host's chip links the host's C library dynamically.

EVERY MODULE IS SIGNED, as a compiled cart's AOT module is (tools/wasm_module.py,
the OTA signing key), over the text native/moy_elf/moy_elf.h's
MOY_ELF_SIG_SCHEME names; `--unsigned` leaves it off. The host's chip is never
signed.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SDK = os.path.join(ROOT, "sdk")
SDK_INCLUDE = os.path.join(SDK, "include")
SDK_SRC = os.path.join(SDK, "src")
CACHE = os.path.join(ROOT, ".build", "sdk")
TABLE = os.path.join(ROOT, "native", "moy_elf", "os_table.json")
ELF_H = os.path.join(ROOT, "native", "moy_elf", "moy_elf.h")

# Per chip: the toolchain's folder and prefix, the code flags, and the
# sections strip takes off (elf_loader's project_elf(), 2.1.0).
CHIPS = {
    "esp32s3": {"tools": "xtensa-esp-elf", "prefix": "xtensa-esp32s3-elf-",
                "flags": ["-mlongcalls"],
                "strip": [".xt.lit", ".xt.prop", ".xtensa.info"]},
    "esp32p4": {"tools": "riscv32-esp-elf", "prefix": "riscv32-esp-elf-",
                "flags": ["-march=rv32imafc_zicsr_zifencei", "-mabi=ilp32f"],
                "strip": [".riscv.attributes"]},
    "host": {"tools": None, "prefix": "", "flags": [], "strip": []},
}

CFLAGS = ["-Os", "-fPIC", "-ffunction-sections", "-fdata-sections", "-fvisibility=hidden",
          "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0", "-fno-stack-protector"]
LINK = ["-shared", "-nostartfiles", "-e", "_moy_entry", "-Wl,--gc-sections",
        "-Wl,--exclude-libs,ALL"]


class ToolError(Exception):
    pass


def module_name(chip):
    """A cart's module for `chip`, beside its manifest."""
    return "main.%s.elf" % chip


def toolchain(chip):
    """(cc, ar, strip) for `chip`."""
    spec = CHIPS.get(chip)
    if spec is None:
        raise ToolError("no chip %r (have %s)" % (chip, ", ".join(sorted(CHIPS))))
    if spec["tools"] is None:
        cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
        if cc is None:
            raise ToolError("no host C compiler")
        return cc, shutil.which("ar") or "ar", shutil.which("strip") or "strip"
    base = os.path.expanduser("~/.espressif/tools/%s" % spec["tools"])
    bins = sorted(glob.glob(os.path.join(base, "*", spec["tools"], "bin")))
    if not bins:
        raise ToolError("no %s toolchain under %s (the ESP-IDF's install puts it there)"
                        % (spec["tools"], base))
    b = bins[-1]
    return tuple(os.path.join(b, spec["prefix"] + t) for t in ("gcc", "ar", "strip"))


def available(chip):
    try:
        toolchain(chip)
        return True
    except ToolError:
        return False


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise ToolError("%s failed:\n%s%s" % (os.path.basename(cmd[0]), p.stdout, p.stderr))
    return p.stdout


def _rows():
    with open(TABLE) as f:
        return len(json.load(f)["rows"])


def libmoyos(chip):
    """The SDK runtime for `chip` (built once per content into .build/sdk/):
    the entry and one object per row, so a module links only what it calls."""
    cc, ar, _strip = toolchain(chip)
    srcs = [os.path.join(SDK_SRC, n) for n in ("moyos_crt.c", "moyos_stubs.c", "moyos_rt.h")]
    srcs.append(os.path.join(SDK_INCLUDE, "moybyte", "os.h"))
    h = hashlib.sha256()
    for p in srcs:
        with open(p, "rb") as f:
            h.update(f.read())
    flags = CFLAGS + CHIPS[chip]["flags"]
    h.update(repr((cc, flags)).encode())
    out_dir = os.path.join(CACHE, chip, h.hexdigest()[:16])
    lib = os.path.join(out_dir, "libmoyos.a")
    if os.path.exists(lib):
        return lib
    tmp = out_dir + ".%d.tmp" % os.getpid()
    os.makedirs(tmp, exist_ok=True)
    base = [cc] + flags + ["-I", SDK_INCLUDE, "-I", SDK_SRC, "-c"]
    objs = [os.path.join(tmp, "crt.o")]
    _run(base + [os.path.join(SDK_SRC, "moyos_crt.c"), "-o", objs[0]])
    for i in range(_rows()):
        o = os.path.join(tmp, "row%03d.o" % i)
        _run(base + ["-DMOY_OS_ONLY=%d" % i, os.path.join(SDK_SRC, "moyos_stubs.c"), "-o", o])
        objs.append(o)
    _run([ar, "rcs", os.path.join(tmp, "libmoyos.a")] + objs)
    if os.path.isdir(out_dir):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        os.replace(tmp, out_dir)
    return lib


def sig_scheme():
    text = open(ELF_H, encoding="utf-8").read()
    return re.search(r'#define MOY_ELF_SIG_SCHEME "([^"]+)"', text).group(1)


def build_module(sources, chip, out, cflags=(), signed=True, sign_with=None):
    """Compile `sources` (C files) for `chip` into the module file `out`,
    signed unless `signed` is False (the host's never is). Returns `out`."""
    cc, _ar, strip = toolchain(chip)
    lib = libmoyos(chip)
    cmd = ([cc] + CFLAGS + CHIPS[chip]["flags"] + list(cflags) + ["-I", SDK_INCLUDE]
           + list(sources) + LINK)
    if chip == "host":
        cmd += [lib]
    else:
        cmd += ["-nostdlib", "-Wl,--no-undefined", lib, "-lc", "-lm", "-lgcc"]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    _run(cmd + ["-o", out])
    if chip != "host":
        args = [strip, "--strip-unneeded", "--remove-section=.comment"]
        args += ["--remove-section=%s" % s for s in CHIPS[chip]["strip"]]
        _run(args + [out])
    if signed and chip != "host":
        from tools import wasm_module
        key = sign_with if sign_with is not None else wasm_module.signing_key()
        if key is None:
            raise ToolError("no signing key (make ota-keygen, or $MOYBYTE_OTA_SIGNING_KEY); "
                            "--unsigned builds without one")
        with open(out, "rb") as f:
            data = f.read()
        with open(out, "wb") as f:
            f.write(wasm_module.sign(data, chip, key, scheme=sig_scheme()))
    return out


def build(src, dst, chips, signed=True, sign_with=None):
    """Copy cart folder `src` to `dst` and build its module for each of
    `chips` from src/*.c. Returns {chip: module path}."""
    sources = sorted(glob.glob(os.path.join(src, "src", "*.c")))
    if not sources:
        raise ToolError("%s has no src/*.c" % src)
    if os.path.abspath(src) != os.path.abspath(dst):
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    out = {}
    for chip in chips:
        out[chip] = build_module(sources, chip, os.path.join(dst, module_name(chip)),
                                 signed=signed, sign_with=sign_with)
    man_path = os.path.join(dst, "manifest.json")
    with open(man_path) as f:
        man = json.load(f)
    ven = man.setdefault("moybyte", {})
    have = [c for c in CHIPS if os.path.exists(os.path.join(dst, module_name(c)))]
    ven["native"] = have
    with open(man_path, "w") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="a cart folder with src/*.c, or `module`")
    ap.add_argument("rest", nargs="*", help="the built cart's folder, or the module's C files")
    ap.add_argument("--chip", action="append", default=[], help="a chip to build for")
    ap.add_argument("-o", "--out", help="the module file (module mode)")
    ap.add_argument("--unsigned", action="store_true", help="leave the signature off")
    args = ap.parse_args(argv)
    try:
        if args.src == "module":
            if len(args.chip) != 1 or not args.out:
                raise ToolError("module mode takes one --chip and -o")
            print(build_module(args.rest, args.chip[0], args.out, signed=not args.unsigned))
            return 0
        if len(args.rest) != 1 or not args.chip:
            raise ToolError("give SRC DST and at least one --chip")
        for chip, path in build(args.src, args.rest[0], args.chip,
                                signed=not args.unsigned).items():
            print("%s %s %d" % (chip, path, os.path.getsize(path)))
    except ToolError as exc:
        print("elf_cart: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
