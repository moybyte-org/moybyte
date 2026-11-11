#!/usr/bin/env python3
"""Build a native (ELF) cart: its C, compiled once per chip against the OS
table, into a `.moy` a console runs (docs/os_2026-11/tiers.md section 1,
sdk/README.md).

    python3 tools/elf_cart.py tests/fixtures/elf/elf_cat.moy /tmp/cat.moy --chip esp32s3 --chip esp32p4
    python3 tools/elf_cart.py SRC DST --chip host          # the host's, for its tests
    python3 tools/elf_cart.py SRC DST --chip esp32s3 --unsigned
    python3 tools/elf_cart.py module a.c b.c --chip esp32p4 -o out.elf
    python3 tools/elf_cart.py sdk --chip esp32s3            # the flags another build takes
    python3 tools/elf_cart.py finish built.elf --chip esp32s3   # strip and sign one it linked

A cart in this tree carries its SOURCE, `src/*.c`; this compiles it per chip
with that chip's toolchain (the ESP-IDF's, from ~/.espressif) into
`main.<chip>.elf` beside the manifest, writes the chips into the manifest's
`"moybyte": {"native": [...]}`, and copies everything else as it is. A module
is a relocatable ELF that imports nothing: it links the SDK's runtime,
libmoyos.a (sdk/src/: the entry, and a stub per row of the OS table that calls
it by ordinal; sdk/libc/: the SDK's C library, and on RISC-V its libm;
libmoy's raster, native/moy_gfx/libmoy), the
toolchain's PURE C library members and its libm and libgcc, every symbol
hidden. The host's
chip links the host's C library dynamically behind the SDK's own.

An app's own service (`"moybyte": {"entries": {"service": "<stem>.elf"}}`,
docs/app_api_v1.md section 7) is built the same way from `src/<stem>/*.c`
into `<stem>.<chip>.elf`, signed alike. A cart with no `src/*.c` of its own
(a Python or Lua app) gets the service's modules alone, and no `native`
field: that names the chips of the cart's own main.

A board module is compiled with -g and stripped; its unstripped twin is kept
under .build/sdk/symbols/ by the sha256 of the stripped module, the name a
board's core dump gives it (tools/coredump.py).

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
SDK_LIBC = os.path.join(SDK, "libc")
SDK_SYSROOT = os.path.join(SDK, "sysroot", "include")
SDK_LIBM = os.path.join(SDK_LIBC, "m")
# libmoy's raster, the copy the console compiles (native/moy_gfx/libmoy,
# vendored verbatim): its index build, which a game draws its screen with.
LIBMOY = os.path.join(ROOT, "native", "moy_gfx", "libmoy")
LIBMOY_SOURCES = ("moy_canvas.c", "moy_sprite.c", "moy_data.c")
# The toolkit an app links (sdk/include/moybyte/ui.h): native/moy_ui's
# engine and RUN face, the text and shape raster under it and libmoy at
# RGB565, behind the SDK's front (sdk/src/moyui_link.c). An app's module
# takes it before libmoyos, so its libmoy is this RGB565 one.
NATIVE = os.path.join(ROOT, "native")
UI_DIRS = [os.path.join(NATIVE, d) for d in ("moy_ui", "moy_gfx", "moy_glass", "moy_spine")]
UI_SOURCES = (
    [os.path.join(NATIVE, "moy_ui", n) for n in (
        "moy_ui.c", "moy_ui_glyph.c", "moy_ui_draw.c", "moy_ui_paint.c", "moy_ui_depth.c",
        "moy_ui_parts.c", "moy_ui_interact.c", "moy_ui_api.c", "moy_ui_api_text.c",
        "moy_ui_api_kept.c", "moy_ui_api_table.c", "moy_ui_api_pop.c", "moy_ui_api_host.c",
        "moy_ui_text.c", "moy_ui_tables.c", "moy_theme.c", "moy_ui_wasm.c")]
    + [os.path.join(NATIVE, "moy_gfx", n) for n in (
        "moy_text.c", "moy_shape.c", "moy_gfx_kernels.c")]
    + [os.path.join(NATIVE, "moy_spine", n) for n in ("moy_str.c", "moy_str_table.c",
                                                       "moy_json.c")]
    + [os.path.join(LIBMOY, n) for n in LIBMOY_SOURCES]
    + [os.path.join(SDK_SRC, "moyui_link.c")])
CACHE = os.path.join(ROOT, ".build", "sdk")
# Each board module's unstripped twin, named by the sha256 of the module a
# board loads (stripped, its signature trailer off): a board's core dump
# names its modules by that sha (native/moy_elf/moy_elf_maps.h) and
# tools/coredump.py loads their symbols from here.
SYMBOLS = os.path.join(CACHE, "symbols")
TABLE = os.path.join(ROOT, "native", "os_calls.json")
ELF_H = os.path.join(ROOT, "native", "moy_elf", "moy_elf.h")

# Per chip: the toolchain's folder and prefix, the code flags, the link
# flags, the sections strip takes off (elf_loader's project_elf(), 2.1.0),
# and whether libmoyos carries the SDK's libm (sdk/libc/m/): the RISC-V
# toolchain's libm addresses its constants absolutely, which no module can
# relocate.
# The S3's ld (esp-14.2.0) double-frees on some module links, depending on
# the output path; --no-keep-memory re-reads symbol tables instead of
# caching them, and those links come out byte-identical.
# The S3's ld (binutils 2.43.1) also counts a dynamic relocation for each FDE
# of an `.eh_frame`, then drops the FDEs of the functions --gc-sections
# removed without their relocations, and aborts in
# elf_xtensa_finish_dynamic_sections when fewer come out than it counted.
# libgcc's 64-bit division helpers carry FDEs, so that abort takes any module
# that links them but keeps no caller of them: one that calls malloc and no
# printf (lc_stdlib's assert reaches the printf family). A module unwinds
# nothing, so the S3 links libgcc without its `.eh_frame` ("bare_libgcc",
# libgcc() below).
CHIPS = {
    "esp32s3": {"tools": "xtensa-esp-elf", "prefix": "xtensa-esp32s3-elf-",
                "flags": ["-mlongcalls"], "link": ["-Wl,--no-keep-memory"],
                "strip": [".xt.lit", ".xt.prop", ".xtensa.info"], "libm": False,
                "bare_libgcc": True},
    "esp32p4": {"tools": "riscv32-esp-elf", "prefix": "riscv32-esp-elf-",
                "flags": ["-march=rv32imafc_zicsr_zifencei", "-mabi=ilp32f"], "link": [],
                "strip": [".riscv.attributes"], "libm": True, "bare_libgcc": False},
    "host": {"tools": None, "prefix": "", "flags": [], "link": [], "strip": [], "libm": False,
             "bare_libgcc": False},
}

CFLAGS = ["-Os", "-g", "-fPIC", "-ffunction-sections", "-fdata-sections", "-fvisibility=hidden",
          "-U_FORTIFY_SOURCE", "-D_FORTIFY_SOURCE=0", "-fno-stack-protector"]
# The SDK's libm keeps each operation as its source writes it: its range
# reductions and extra-precision steps count on every product rounding.
LIBM_FLAGS = ["-ffp-contract=off"]
LINK = ["-shared", "-nostartfiles", "-e", "_moy_entry", "-Wl,--gc-sections",
        "-Wl,--exclude-libs,ALL"]

# The toolchain's C library objects a board's module may take as they are:
# each touches no state of its own (no reentrancy record, locale, errno or
# heap), so it is position-independent wherever it was built.
# pure_libc() takes the members that define these and refuses one that
# needs anything but another such member or libgcc.
PURE = ("memcpy memmove memset memcmp memchr memrchr memccpy mempcpy memmem bzero bcmp "
        "bcopy strlen strnlen strcmp strncmp strcpy strncpy stpcpy stpncpy strcat strncat "
        "strchr strrchr strchrnul strstr strspn strcspn strpbrk strtok_r strsep strlcpy "
        "strlcat index rindex qsort bsearch abs labs llabs div ldiv lldiv imaxabs ffs").split()


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
    """The OS function table's length: its version, the ordinals ever appended."""
    with open(TABLE) as f:
        return json.load(f)["version"]


def includes(chip):
    """The SDK's include folders for `chip`: its own, libmoy's (moy.h), and
    on a board the sysroot's headers a board's C library lacks (sockets,
    poll, mmap)."""
    out = ["-I", SDK_INCLUDE, "-I", LIBMOY]
    if chip != "host":
        out += ["-isystem", SDK_SYSROOT]
    return out


def _sdk_sources():
    srcs = [os.path.join(SDK_SRC, n) for n in ("moyos_crt.c", "moyos_stubs.c", "moyos_rt.h")]
    srcs.append(os.path.join(SDK_INCLUDE, "moybyte", "os.h"))
    srcs += sorted(glob.glob(os.path.join(SDK_LIBC, "*.[ch]")))
    srcs += sorted(glob.glob(os.path.join(SDK_SYSROOT, "**", "*.h"), recursive=True))
    srcs += sorted(glob.glob(os.path.join(SDK_LIBM, "**", "*.[ch]"), recursive=True))
    srcs += [os.path.join(LIBMOY, n) for n in LIBMOY_SOURCES + ("moy.h", "moy_pixel.h")]
    return srcs


def libmoyos(chip):
    """The SDK runtime for `chip` (built once per content into .build/sdk/):
    the entry, one object per row, so a module links only what it calls,
    the SDK's C library (sdk/libc/; on RISC-V, with its libm) and libmoy's
    raster."""
    cc, ar, _strip = toolchain(chip)
    srcs = _sdk_sources()
    h = hashlib.sha256()
    for p in srcs:
        h.update(os.path.relpath(p, SDK).encode())
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
    base = [cc] + flags + includes(chip) + ["-I", SDK_SRC, "-c"]
    objs = [os.path.join(tmp, "crt.o")]
    _run(base + [os.path.join(SDK_SRC, "moyos_crt.c"), "-o", objs[0]])
    for i in range(_rows()):
        o = os.path.join(tmp, "row%03d.o" % i)
        _run(base + ["-DMOY_OS_ONLY=%d" % i, os.path.join(SDK_SRC, "moyos_stubs.c"), "-o", o])
        objs.append(o)
    for src in sorted(glob.glob(os.path.join(SDK_LIBC, "*.c"))):
        o = os.path.join(tmp, "lc_" + os.path.basename(src)[:-2] + ".o")
        _run(base + ["-fno-builtin", "-I", SDK_LIBC, src, "-o", o])
        objs.append(o)
    for name in LIBMOY_SOURCES:
        o = os.path.join(tmp, "moy_" + name[:-2] + ".o")
        _run(base + [os.path.join(LIBMOY, name), "-o", o])
        objs.append(o)
    if CHIPS[chip]["libm"]:
        for part, inc in (("libm", "libm"), ("libm_dbl", "libm_dbl"), ("", "libm")):
            for src in sorted(glob.glob(os.path.join(SDK_LIBM, part, "*.c"))):
                o = os.path.join(tmp, "m_%s_%s.o" % (part or "lc", os.path.basename(src)[:-2]))
                _run(base + LIBM_FLAGS + ["-I", os.path.join(SDK_LIBM, inc), src, "-o", o])
                objs.append(o)
    _run([ar, "rcs", os.path.join(tmp, "libmoyos.a")] + objs)
    if os.path.isdir(out_dir):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        os.replace(tmp, out_dir)
    return lib


def libmoyui(chip):
    """The toolkit an app links for `chip` (UI_SOURCES), built once per
    content into .build/sdk/."""
    cc, ar, _strip = toolchain(chip)
    h = hashlib.sha256()
    heads = []
    for d in UI_DIRS + [LIBMOY, SDK_INCLUDE]:
        heads += sorted(glob.glob(os.path.join(d, "**", "*.h"), recursive=True))
    for p in UI_SOURCES + heads:
        h.update(os.path.relpath(p, ROOT).encode())
        with open(p, "rb") as f:
            h.update(f.read())
    flags = CFLAGS + CHIPS[chip]["flags"] + ["-DMOY_PIXEL_RGB565=1"]
    h.update(repr((cc, flags)).encode())
    out_dir = os.path.join(CACHE, chip, "ui-" + h.hexdigest()[:16])
    lib = os.path.join(out_dir, "libmoyui.a")
    if os.path.exists(lib):
        return lib
    tmp = out_dir + ".%d.tmp" % os.getpid()
    os.makedirs(tmp, exist_ok=True)
    inc = []
    for d in UI_DIRS:
        inc += ["-I", d]
    base = [cc] + flags + includes(chip) + inc + ["-c"]
    objs = []
    for src in UI_SOURCES:
        o = os.path.join(tmp, "ui_%s_%s.o" % (os.path.basename(os.path.dirname(src)),
                                               os.path.basename(src)[:-2]))
        _run(base + [src, "-o", o])
        objs.append(o)
    _run([ar, "rcs", os.path.join(tmp, "libmoyui.a")] + objs)
    if os.path.isdir(out_dir):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        os.replace(tmp, out_dir)
    return lib


def _tool(chip, name):
    cc = toolchain(chip)[0]
    return cc[:-3] + name if cc.endswith("gcc") else name


def pure_libc(chip):
    """The toolchain's PURE members of its C library for board `chip`, as
    one archive (built once per toolchain into .build/sdk/)."""
    cc, ar, _strip = toolchain(chip)
    flags = CHIPS[chip]["flags"]
    libc = _run([cc] + flags + ["-print-file-name=libc.a"]).strip()
    libgcc = _run([cc] + flags + ["-print-libgcc-file-name"]).strip()
    h = hashlib.sha256(repr((libc, os.path.getsize(libc), PURE)).encode()).hexdigest()[:16]
    out_dir = os.path.join(CACHE, chip, "pure-" + h)
    lib = os.path.join(out_dir, "libcpure.a")
    if os.path.exists(lib):
        return lib
    nm = _tool(chip, "nm")
    tmp = out_dir + ".%d.tmp" % os.getpid()
    os.makedirs(tmp, exist_ok=True)

    def table(archive):
        defined, undefined = {}, {}
        member = None
        for line in _run([nm, "-A", archive]).splitlines():
            parts = line.rsplit(" ", 2)
            where = parts[0].split(":")
            if len(where) < 2:
                continue
            member = where[-2]
            kind, sym = parts[-2], parts[-1]
            if kind == "U":
                undefined.setdefault(member, set()).add(sym)
            elif kind.upper() in "TDRBWVC" and kind != "w":
                defined.setdefault(member, set()).add(sym)
        return defined, undefined

    defined, undefined = table(libc)
    take = sorted(m for m, syms in defined.items() if syms & set(PURE))
    gcc_defined = set().union(*table(libgcc)[0].values())
    have = set().union(*(defined[m] for m in take))
    for m in take:
        stray = undefined.get(m, set()) - have - gcc_defined
        if stray:
            raise ToolError("%s's %s needs %s, which no module links"
                            % (os.path.basename(libc), m, ", ".join(sorted(stray))))
    subprocess.run([ar, "x", libc] + take, cwd=tmp, check=True)
    _run([ar, "rcs", os.path.join(tmp, "libcpure.a")] + [os.path.join(tmp, m) for m in take])
    if os.path.isdir(out_dir):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        os.replace(tmp, out_dir)
    return lib


def libgcc(chip):
    """libgcc as a module for board `chip` links it: the toolchain's, or
    where the chip is "bare_libgcc", a copy without `.eh_frame` (built once
    per toolchain into .build/sdk/)."""
    if not CHIPS[chip]["bare_libgcc"]:
        return "-lgcc"
    cc = toolchain(chip)[0]
    src = _run([cc] + CHIPS[chip]["flags"] + ["-print-libgcc-file-name"]).strip()
    h = hashlib.sha256(repr((src, os.path.getsize(src))).encode()).hexdigest()[:16]
    out_dir = os.path.join(CACHE, chip, "libgcc-" + h)
    lib = os.path.join(out_dir, "libgcc.a")
    if os.path.exists(lib):
        return lib
    tmp = out_dir + ".%d.tmp" % os.getpid()
    os.makedirs(tmp, exist_ok=True)
    _run([_tool(chip, "objcopy"), "--remove-section=.eh_frame", "--remove-section=.rela.eh_frame",
          src, os.path.join(tmp, "libgcc.a")])
    if os.path.isdir(out_dir):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        os.replace(tmp, out_dir)
    return lib


def sig_scheme():
    text = open(ELF_H, encoding="utf-8").read()
    return re.search(r'#define MOY_ELF_SIG_SCHEME "([^"]+)"', text).group(1)


def libs(chip, ui=False):
    """What a module for `chip` links after its own objects; with `ui`, an
    app's, the toolkit first."""
    first = [libmoyui(chip)] if ui else []
    if chip == "host":
        return first + [libmoyos(chip), "-lm"]
    # A group, so a helper any of them needs comes from the first that
    # defines it: the SDK's own before the toolchain's.
    return ["-nostdlib", "-Wl,--no-undefined", "-Wl,--start-group"] + first + [
        libmoyos(chip), pure_libc(chip), "-lm", libgcc(chip), "-Wl,--end-group"]


def is_app(man):
    """Whether a manifest is an app's, whose main module links the toolkit
    (sdk/include/moybyte/ui.h) and whose screen is RGB565."""
    return (man.get("moybyte") or {}).get("type") == "app"


def build_module(sources, chip, out, cflags=(), signed=True, sign_with=None, ui=False):
    """Compile `sources` (C files) for `chip` into the module file `out`,
    an app's linking the toolkit (`ui`), signed unless `signed` is False
    (the host's never is). Returns `out`."""
    cc, _ar, _strip = toolchain(chip)
    cmd = ([cc] + CFLAGS + CHIPS[chip]["flags"] + list(cflags) + includes(chip)
           + list(sources) + LINK + CHIPS[chip]["link"] + libs(chip, ui))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    _run(cmd + ["-o", out])
    return finish(out, chip, signed=signed, sign_with=sign_with)


def finish(out, chip, signed=True, sign_with=None):
    """A linked module made one a console loads: stripped and, unless
    `signed` is False, signed (the host's is neither). Returns `out`."""
    strip = toolchain(chip)[2]
    if chip != "host":
        with open(out, "rb") as f:
            linked = f.read()
        args = [strip, "--strip-unneeded", "--remove-section=.comment"]
        args += ["--remove-section=%s" % s for s in CHIPS[chip]["strip"]]
        _run(args + [out])
        keep_symbols(linked, out)
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


def keep_symbols(linked, module):
    """Keep `linked`, the bytes of a module before its strip, under SYMBOLS
    by the sha256 of `module`, the stripped file. Returns that path."""
    with open(module, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    os.makedirs(SYMBOLS, exist_ok=True)
    path = os.path.join(SYMBOLS, sha + ".elf")
    with open(path, "wb") as f:
        f.write(linked)
    return path


def service_entry(man):
    """The stem of the `.elf` service entry the manifest declares, or None."""
    got = (man.get("moybyte") or {}).get("entries")
    f = got.get("service") if isinstance(got, dict) else None
    ok = isinstance(f, str) and f.endswith(".elf") and "/" not in f and "." not in f[:-4]
    return f[:-4] if ok else None


def build(src, dst, chips, signed=True, sign_with=None):
    """Copy cart folder `src` to `dst` and build its module for each of
    `chips` from src/*.c, and its service entry's from src/<stem>/*.c.
    Returns {chip: module path, "service." + chip: module path}."""
    with open(os.path.join(src, "manifest.json")) as f:
        man = json.load(f)
    stem = service_entry(man)
    ui = is_app(man)
    sources = sorted(glob.glob(os.path.join(src, "src", "*.c")))
    svc = sorted(glob.glob(os.path.join(src, "src", stem, "*.c"))) if stem else []
    if stem and not svc:
        raise ToolError("%s has no src/%s/*.c for its service" % (src, stem))
    if not sources and not svc:
        raise ToolError("%s has no src/*.c" % src)
    if os.path.abspath(src) != os.path.abspath(dst):
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    out = {}
    for chip in chips:
        if sources:
            out[chip] = build_module(sources, chip, os.path.join(dst, module_name(chip)),
                                     signed=signed, sign_with=sign_with, ui=ui)
        if svc:
            out["service." + chip] = build_module(
                svc, chip, os.path.join(dst, "%s.%s.elf" % (stem, chip)),
                signed=signed, sign_with=sign_with)
    if not sources:
        return out
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


def sdk(chip):
    """What another build needs to make a module for `chip` (sdk/cmake/):
    its compiler, its flags, its link flags and libraries, its strip."""
    cc, _ar, strip = toolchain(chip)
    return {"CC": cc, "STRIP": strip,
            "CFLAGS": " ".join(CFLAGS + CHIPS[chip]["flags"] + includes(chip)),
            "LDFLAGS": " ".join(LINK + CHIPS[chip]["link"]), "LIBS": " ".join(libs(chip))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="a cart folder with src/*.c, or `module`, `sdk` or `finish`")
    ap.add_argument("rest", nargs="*", help="the built cart's folder, the module's C files, "
                                            "or the linked module to finish")
    ap.add_argument("--chip", action="append", default=[], help="a chip to build for")
    ap.add_argument("-o", "--out", help="the module file (module mode)")
    ap.add_argument("--unsigned", action="store_true", help="leave the signature off")
    args = ap.parse_args(argv)
    try:
        if args.src == "sdk":
            if len(args.chip) != 1:
                raise ToolError("sdk mode takes one --chip")
            for k, v in sdk(args.chip[0]).items():
                print("%s=%s" % (k, v))
            return 0
        if args.src == "finish":
            if len(args.chip) != 1 or len(args.rest) != 1:
                raise ToolError("finish mode takes one --chip and the module")
            print(finish(args.rest[0], args.chip[0], signed=not args.unsigned))
            return 0
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
