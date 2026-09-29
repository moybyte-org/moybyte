#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Build the compiled tier's Jet carts: Jet Teapot and ESP 88 (ports/jet/README.md).

    python3 tools/jet_cart.py /tmp/carts                  # -> /tmp/carts/{teapot,esp88}.moy
    python3 tools/jet_cart.py /tmp/carts --cart esp88     # one of them
    python3 tools/jet_cart.py /tmp/carts --chip esp32s3 --chip esp32p4
    python3 tools/jet_cart.py --toolchain                 # fetch wasi-sdk if absent

A cart's source is its folder under ports/jet/ (CARTS): its manifest, config,
data, licences and `src/` (C++), all copied into the built cart as they are.
This compiles `src/` with the vendored Jet (ports/jet/jet/, tools/vendor_jet.py)
-- and for ESP 88 the film's vendored code (ports/jet/examples/) -- into the
manifest's `main.wasm` with wasi-sdk 24's clang, on the console's `"moy"`
imports alone, and with `--chip` compiles and signs each board's module with
tools/wasm_module.py (its pinned compilers and the OTA signing key). No module
is ever committed.

THE TEAPOT COMPILES JET TWICE into its one module. Half-width buffers are a
compile-time switch in Jet, and its config.json chooses the width at launch,
so the second build turns HALF_WIDTH_BUFFERS on and renames Jet's namespaces
on the command line (HALF_RENAMES); scene.cpp is compiled with each and
defines one table per build (`jet_full`, `jet_half`). ESP 88 compiles Jet once,
under the film's own configuration.

THE MEMORY is the manifest's, and the link matches it: the stack first (so an
overflow traps rather than running into the data), the static data (the frame
and the render buffers are fixed arrays in main.cpp), then the heap to the
end. The build refuses a memory that leaves the heap less than the cart's
`heap_min`.

THE TOOLCHAIN is wasi-sdk 24 -- $WASI_SDK_PATH, else
experiments/wasm_aot/toolchain/wasi-sdk (gitignored), fetched there by sha256
when absent. The module links wasi-libc and libc++ statically and defines the
few WASI calls they make (src/runtime.cpp), so nothing is imported from WASI.
Paths are mapped out of the binary (-ffile-prefix-map), and a built main.wasm
is cached under .build/jet_cart/ by the hash of everything that went into it.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import wasm_cart, wasm_module  # noqa: E402

PORT = os.path.join(ROOT, "ports", "jet")
JET_SRC = os.path.join(PORT, "jet", "src")
FILM_MAIN = os.path.join(PORT, "examples", "esp32-neon-film", "main")
CACHE = os.path.join(ROOT, ".build", "jet_cart")
WASI_SDK = os.path.join(ROOT, "experiments", "wasm_aot", "toolchain", "wasi-sdk")
LIBMOY_WASM = os.path.join(ROOT, "native", "moycore", "libmoy", "moy_wasm.c")

# wasi-sdk 24 (clang 18, wasi-libc, libc++): the release the compiled tier's
# other C cart builds with (experiments/wasm_aot/doom/build_cart.py).
WASI_SDK_URL = ("https://github.com/WebAssembly/wasi-sdk/releases/download/"
                "wasi-sdk-24/wasi-sdk-24.0-x86_64-linux.tar.gz")
WASI_SDK_SHA256 = "c6c38aab56e5de88adf6c1ebc9c3ae8da72f88ec2b656fb024eda8d4167a0bc5"

# Jet's translation units the teapot links.
JET_UNITS = ("BlendSpans", "Camera", "Light", "Material", "Object", "PostFX",
             "Renderer", "Scene", "Sprite2D", "TrigLUT")

BUILDS = {
    "full": ["-DHALF_WIDTH_BUFFERS=0", "-DJET_SCENE_TABLE=jet_full"],
    "half": ["-DHALF_WIDTH_BUFFERS=1", "-DJET_SCENE_TABLE=jet_half"],
}
HALF_RENAMES = {"Renderer": "JetHalf", "Primitives": "JetHalfPrimitives",
                "Loader": "JetHalfLoader"}
BUILDS["half"] += ["-D%s=%s" % kv for kv in sorted(HALF_RENAMES.items())]

PAGE = 65536
STACK = 64 * 1024


class Cart:
    """One Jet cart: its folder under ports/jet/, Jet's units it links, the
    builds Jet and `build_units` are compiled once each for (name: flags),
    the cart's own units compiled once, the directories searched after its
    `src/` and before Jet's, the stack, and the least heap its memory must
    leave (tests/test_jet_cart.py measures each cart's peak against it)."""

    def __init__(self, name, folder, jet_units, builds, build_units, units,
                 includes, stack, heap_min):
        self.name, self.folder = name, os.path.join(PORT, folder)
        self.jet_units, self.builds = jet_units, builds
        self.build_units, self.units = build_units, units
        self.includes, self.stack, self.heap_min = includes, stack, heap_min

    @property
    def src(self):
        return os.path.join(self.folder, "src")


CARTS = {
    # The heap holds the OBJ text, the loader's vertex map, the mesh and Jet's
    # per-frame queues: they peak at about 270 KB.
    "teapot": Cart("teapot", "teapot.moy", JET_UNITS, BUILDS, ("scene.cpp",),
                   ("main.cpp", "runtime.cpp"), (), STACK, 384 * 1024),
    # The film builds each cut's city, cars and cockpit on the heap when the
    # cut begins and frees it at the next, beside Jet's per-frame queues,
    # which keep the capacity of the busiest frame drawn so far: the
    # boulevard's city loaded after the pursuit's queue peaks at about
    # 1,260 KB. Its stack stays under 6 KB.
    "esp88": Cart("esp88", "esp88.moy", JET_UNITS + ("Primitives", "Texture"),
                  {"film": []}, (), ("main.cpp", "runtime.cpp"),
                  (FILM_MAIN, os.path.join(FILM_MAIN, "firmware")),
                  16 * 1024, 1280 * 1024),
}
DEFAULT_CART = "teapot"

CXXFLAGS = ["--target=wasm32-wasi", "-std=c++17", "-O2", "-fno-exceptions",
            "-fno-rtti", "-fno-threadsafe-statics",
            # The per-pixel shading calls in Jet's rasterizer (the lighting
            # term, a vector's length, the Phong highlight) sit in blocks
            # clang's static estimate calls cold, whose inline threshold is
            # 45, so they stayed calls: one call per pixel for each, which the
            # AOT compiler keeps. At 300 all three inline (the highlight's
            # cost is 275).
            "-mllvm", "-inline-cold-callsite-threshold=300",
            # The span walk's per-row step (TriangleSpans::beginRow, cost
            # 265) sits just over the default threshold of 225, so every
            # row paid a call and the AOT compiler's stack-check wrapper
            # around it. At 300 it inlines; the module comes out smaller.
            "-mllvm", "-inline-threshold=300",
            # A C cast from float to int is undefined out of range, which
            # wasm's saturating conversion honours in one instruction. The
            # trapping one clang emits without this comes with a range guard
            # of its own, and the AOT compiler adds the trap's NaN and range
            # checks behind it: three compare-and-branches per cast.
            "-mnontrapping-fptoint",
            "-Wall",
            # TrigLUT.hpp defines M_PI after the C library has.
            "-Wno-macro-redefined",
            # Jet's own, at the pin: helpers, locals and fields it keeps but
            # does not use, and a brace style.
            "-Wno-unused-function", "-Wno-unused-variable",
            "-Wno-unused-private-field", "-Wno-missing-braces"]

class BuildError(RuntimeError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def cart_spec(cart=DEFAULT_CART):
    try:
        return CARTS[cart]
    except KeyError:
        raise BuildError("no Jet cart %r: one of %s" % (cart, ", ".join(sorted(CARTS))))


def manifest(cart=DEFAULT_CART):
    with open(os.path.join(cart_spec(cart).folder, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


@contextlib.contextmanager
def _locked(name):
    """One builder at a time: parallel test workers share the toolchain
    directory and the cache."""
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, name + ".lock"), "w") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:                              # pragma: no cover
            pass
        yield


# -- the toolchain ---------------------------------------------------------------


def _find_sdk():
    for cand in (os.environ.get("WASI_SDK_PATH"), WASI_SDK):
        if cand and os.path.isfile(os.path.join(cand, "bin", "clang++")):
            return cand
    return None


def wasi_sdk(fetch=True):
    """The wasi-sdk 24 directory, fetched into the toolchain directory when
    absent and `fetch`; None when there is none."""
    found = _find_sdk()
    if found or not fetch:
        return found
    with _locked("wasi-sdk"):
        return _find_sdk() or _fetch_sdk()


def _fetch_sdk():
    print("jet_cart: fetching %s" % WASI_SDK_URL, file=sys.stderr)
    with urllib.request.urlopen(WASI_SDK_URL, timeout=600) as r:
        data = r.read()
    got = sha256_bytes(data)
    if got != WASI_SDK_SHA256:
        raise BuildError("%s hashes to %s; the pin is %s" % (WASI_SDK_URL, got,
                                                             WASI_SDK_SHA256))
    parent = os.path.dirname(WASI_SDK)
    os.makedirs(parent, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            top = tar.getmembers()[0].name.split("/")[0]
            tar.extractall(tmp)
        if not os.path.isdir(WASI_SDK):
            os.replace(os.path.join(tmp, top), WASI_SDK)
    return WASI_SDK


# -- the module ------------------------------------------------------------------


def _units(spec):
    """[(build or None, source path)] for every compile."""
    out = []
    for build in spec.builds:
        out += [(build, os.path.join(JET_SRC, u + ".cpp")) for u in spec.jet_units]
        out += [(build, os.path.join(spec.src, u)) for u in spec.build_units]
    out += [(None, os.path.join(spec.src, u)) for u in spec.units]
    return out


def _source_files(spec):
    """Every file under the directories a compile searches, sorted."""
    out = []
    for d in (JET_SRC, spec.src) + tuple(spec.includes):
        for dirpath, _dirs, names in os.walk(d):
            out += [os.path.join(dirpath, n) for n in names]
    return sorted(set(out))


def _inputs_key(spec, sdk, memory):
    """The hash of everything a build reads: the sources, the flags, the
    memory and the compiler."""
    h = hashlib.sha256()
    for path in _source_files(spec):
        h.update(os.path.relpath(path, ROOT).encode() + b"\0")
        with open(path, "rb") as f:
            h.update(f.read())
    h.update(json.dumps([CXXFLAGS, spec.builds, _units_rel(spec), memory,
                         spec.stack]).encode())
    h.update(subprocess.run([os.path.join(sdk, "bin", "clang++"), "--version"],
                            capture_output=True, text=True).stdout.encode())
    return h.hexdigest()[:24]


def _units_rel(spec):
    return [(b, os.path.relpath(p, ROOT)) for b, p in _units(spec)]


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise BuildError("%s failed:\n%s%s" % (os.path.basename(cmd[0]), r.stdout, r.stderr))
    return r.stderr


def compile_wasm(sdk=None, memory=None, verbose=False, cart=DEFAULT_CART):
    """The cart's main.wasm bytes at `memory` bytes of linear memory (default:
    the manifest's). Cached by its inputs."""
    spec = cart_spec(cart)
    sdk = sdk or wasi_sdk()
    if sdk is None:
        raise BuildError("no wasi-sdk")
    memory = memory or manifest(cart)["memory"] * PAGE
    key = _inputs_key(spec, sdk, memory)
    cached = os.path.join(CACHE, "%s-%s.wasm" % (spec.name, key))
    with _locked("build"):
        if not os.path.isfile(cached):
            _compile(spec, sdk, memory, cached, verbose)
    with open(cached, "rb") as f:
        return f.read()


def _compile(spec, sdk, memory, cached, verbose):
    cxx = os.path.join(sdk, "bin", "clang++")
    os.makedirs(CACHE, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        base = CXXFLAGS + ["-ffile-prefix-map=%s/=" % ROOT, "-I", spec.src]
        for d in spec.includes:
            base += ["-I", d]
        base += ["-I", JET_SRC]

        def one(i_unit):
            i, (build, src) = i_unit
            obj = os.path.join(tmp, "%03d_%s_%s.o" % (i, build or "cart",
                                                     os.path.basename(src)))
            warnings = _run([cxx] + base + spec.builds.get(build, [])
                            + ["-c", src, "-o", obj])
            if warnings and verbose:
                sys.stderr.write(warnings)
            return obj

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            objs = list(pool.map(one, enumerate(_units(spec))))
        out = os.path.join(tmp, "main.wasm")

        def link(mem, extra=()):
            _run([cxx, "--target=wasm32-wasi", "-nostartfiles", "-Wl,--no-entry",
                  "-Wl,--strip-all", "-Wl,--stack-first",
                  "-Wl,--export=__stack_pointer",
                  "-Wl,-z,stack-size=%d" % spec.stack,
                  "-Wl,--initial-memory=%d" % mem, "-Wl,--max-memory=%d" % mem]
                 + list(extra) + ["-o", out] + objs)
            with open(out, "rb") as f:
                return f.read()

        base_at = heap_base(link(memory, ["-Wl,--export=__heap_base"]))
        if memory - base_at < spec.heap_min:
            raise BuildError("%s: the manifest's memory (%d pages) leaves the heap %d KB "
                             "above the stack and data (%d KB); it needs %d KB"
                             % (spec.name, memory // PAGE, (memory - base_at) // 1024,
                                base_at // 1024, spec.heap_min // 1024))
        wasm = link(memory)
    check_imports(wasm)
    tmp_cached = cached + ".part"
    with open(tmp_cached, "wb") as f:
        f.write(wasm)
    os.replace(tmp_cached, cached)


def imports(wasm):
    """[(module, name)] a wasm binary imports."""
    out = []
    for sid, _name, body in wasm_module.sections(wasm):
        if sid != 2:
            continue
        n, i = wasm_module._read_leb(body, 0)
        for _ in range(n):
            ml, i = wasm_module._read_leb(body, i)
            mod = body[i:i + ml].decode()
            i += ml
            nl, i = wasm_module._read_leb(body, i)
            name = body[i:i + nl].decode()
            i += nl
            kind = body[i]
            i += 1
            if kind != 0:
                raise BuildError("imports %s.%s as something other than a function"
                                 % (mod, name))
            _t, i = wasm_module._read_leb(body, i)
            out.append((mod, name))
    return out


def console_imports():
    """The import names the console's table answers (libmoy's moy_wasm.c)."""
    with open(LIBMOY_WASM, encoding="utf-8") as f:
        return set(re.findall(r'\{"(\w+)", FN\(', f.read()))


def check_imports(wasm):
    table = console_imports()
    bad = ["%s.%s" % mn for mn in imports(wasm) if mn[0] != "moy" or mn[1] not in table]
    if bad:
        raise BuildError("the module imports what the console does not give a "
                         "cart: %s" % ", ".join(bad))


def heap_base(wasm):
    """The exported __heap_base global's value: where the stack and the
    static data end."""
    idx = None
    for sid, _name, body in wasm_module.sections(wasm):
        if sid == 7:
            n, i = wasm_module._read_leb(body, 0)
            for _ in range(n):
                nl, i = wasm_module._read_leb(body, i)
                name = body[i:i + nl].decode()
                i += nl
                kind = body[i]
                gi, i = wasm_module._read_leb(body, i + 1)
                if kind == 3 and name == "__heap_base":
                    idx = gi
    for sid, _name, body in wasm_module.sections(wasm):
        if sid == 6:
            n, i = wasm_module._read_leb(body, 0)
            for g in range(n):
                i += 2                           # valtype, mutability
                if body[i] != 0x41:
                    raise BuildError("a global not initialised by i32.const")
                v, i = wasm_module._read_leb(body, i + 1)
                i += 1                           # end
                if g == idx:
                    return v
    raise BuildError("no __heap_base export")


# -- the cart --------------------------------------------------------------------


def build(out_dir, chips=(), sdk=None, config=None, verbose=False, cart=DEFAULT_CART,
          memory=None):
    """The built cart folder `out_dir`/<cart>.moy: the source folder, its
    main.wasm, and a signed module per chip in `chips`. `config` overrides
    config.json's keys (how a test builds a mode), `memory` the manifest's
    (how a test measures). Returns its path."""
    spec = cart_spec(cart)
    wasm = compile_wasm(sdk, memory, verbose=verbose, cart=cart)
    man = manifest(cart)
    folder = spec.folder
    cart = os.path.join(out_dir, os.path.basename(folder))
    if os.path.isdir(cart):
        shutil.rmtree(cart)
    shutil.copytree(folder, cart)
    if memory:
        man["memory"] = memory // PAGE
        with open(os.path.join(cart, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2)
            f.write("\n")
    main = man.get("main", "main.wasm")
    with open(os.path.join(cart, main), "wb") as f:
        f.write(wasm)
    if config:
        path = os.path.join(cart, "config.json")
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.update(config)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
    for chip in chips:
        wasm_module.build(wasm, chip, os.path.join(cart, wasm_cart.aot_name(main, chip)))
    return cart


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", help="where the built cart folders go")
    ap.add_argument("--cart", action="append", choices=sorted(CARTS),
                    help="build this cart (repeatable; default: every one)")
    ap.add_argument("--toolchain", action="store_true",
                    help="only make sure wasi-sdk 24 is here (fetched by sha256 when "
                    "absent) and print where; what CI and preflight run before the "
                    "suite, so the download is one step of its own")
    ap.add_argument("--chip", action="append", default=[],
                    help="also compile and sign the module for this chip (esp32s3, "
                    "esp32p4); repeatable")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show the compiler's warnings")
    args = ap.parse_args(argv)
    if args.toolchain:
        try:
            print(wasi_sdk())
        except (BuildError, OSError) as exc:
            print("jet_cart: %s" % exc, file=sys.stderr)
            return 2
        return 0
    if not args.out:
        ap.error("the output folder is required")
    for name in args.cart or sorted(CARTS):
        try:
            cart = build(args.out, args.chip, verbose=args.verbose, cart=name)
        except (BuildError, wasm_module.ToolError) as exc:
            print("jet_cart: %s" % exc, file=sys.stderr)
            return 2
        man = manifest(name)
        main_name = man.get("main", "main.wasm")
        print("%s: memory %d pages (%d KB)" % (cart, man["memory"],
                                                man["memory"] * PAGE // 1024))
        for entry in sorted(os.listdir(cart)):
            path = os.path.join(cart, entry)
            if os.path.isfile(path):
                print("  %-22s %9d bytes" % (entry, os.path.getsize(path)))
        for chip in args.chip:
            size = os.path.getsize(os.path.join(cart, wasm_cart.aot_name(main_name, chip)))
            print("  %s module: %d bytes" % (chip, size))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
