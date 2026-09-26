#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Build the compiled tier's showcase cart: Jet Teapot (ports/jet/README.md).

    python3 tools/jet_cart.py /tmp/carts                  # -> /tmp/carts/teapot.moy
    python3 tools/jet_cart.py /tmp/carts --chip esp32s3 --chip esp32p4
    python3 tools/jet_cart.py --toolchain                 # fetch wasi-sdk if absent

The cart's source is ports/jet/teapot.moy/: its manifest, config, model,
licences and `src/` (C++), all copied into the built cart as they are. This
compiles `src/` with the vendored Jet (ports/jet/jet/, tools/vendor_jet.py)
into the manifest's `main.wasm` with wasi-sdk 24's clang, on the console's
`"moy"` imports alone, and with `--chip` compiles and signs each board's
module with tools/wasm_module.py (its pinned compilers and the OTA signing
key). No module is ever committed.

JET IS COMPILED TWICE into the one module. Half-width buffers are a
compile-time switch in Jet, and config.json chooses the width at launch, so
the second build turns HALF_WIDTH_BUFFERS on and renames Jet's namespaces on
the command line (HALF_RENAMES); scene.cpp is compiled with each and defines
one table per build (`jet_full`, `jet_half`).

THE MEMORY is the manifest's, and the link matches it: the stack first (so an
overflow traps rather than running into the data), the static data (the frame
and depth buffers are a fixed arena in main.cpp), then the heap to the end.
The build refuses a memory that leaves the heap less than HEAP_MIN.

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
CART = os.path.join(PORT, "teapot.moy")
JET_SRC = os.path.join(PORT, "jet", "src")
CACHE = os.path.join(ROOT, ".build", "jet_cart")
WASI_SDK = os.path.join(ROOT, "experiments", "wasm_aot", "toolchain", "wasi-sdk")
LIBMOY_WASM = os.path.join(ROOT, "native", "moycore", "libmoy", "moy_wasm.c")

# wasi-sdk 24 (clang 18, wasi-libc, libc++): the release the compiled tier's
# other C cart builds with (experiments/wasm_aot/doom/build_cart.py).
WASI_SDK_URL = ("https://github.com/WebAssembly/wasi-sdk/releases/download/"
                "wasi-sdk-24/wasi-sdk-24.0-x86_64-linux.tar.gz")
WASI_SDK_SHA256 = "c6c38aab56e5de88adf6c1ebc9c3ae8da72f88ec2b656fb024eda8d4167a0bc5"

# The translation units: Jet's that the cart links, per build; the cart's own
# that differ per build; the cart's own compiled once.
JET_UNITS = ("BlendSpans", "Camera", "Light", "Material", "Object", "PostFX",
             "Renderer", "Scene", "Sprite2D", "TrigLUT")
SCENE_UNITS = ("scene.cpp",)
CART_UNITS = ("main.cpp", "runtime.cpp")

BUILDS = {
    "full": ["-DHALF_WIDTH_BUFFERS=0", "-DJET_SCENE_TABLE=jet_full"],
    "half": ["-DHALF_WIDTH_BUFFERS=1", "-DJET_SCENE_TABLE=jet_half"],
}
HALF_RENAMES = {"Renderer": "JetHalf", "Primitives": "JetHalfPrimitives",
                "Loader": "JetHalfLoader"}
BUILDS["half"] += ["-D%s=%s" % kv for kv in sorted(HALF_RENAMES.items())]

CXXFLAGS = ["--target=wasm32-wasi", "-std=c++17", "-O2", "-fno-exceptions",
            "-fno-rtti", "-fno-threadsafe-statics", "-Wall",
            # TrigLUT.hpp defines M_PI after the C library has.
            "-Wno-macro-redefined",
            # Jet's own, at the pin: helpers, locals and fields it keeps but
            # does not use, and a brace style.
            "-Wno-unused-function", "-Wno-unused-variable",
            "-Wno-unused-private-field", "-Wno-missing-braces"]

PAGE = 65536
STACK = 64 * 1024
# The heap the cart needs: the OBJ text, the loader's vertex map, the mesh and
# Jet's per-frame queues peak at about 270 KB (tests/test_jet_cart.py
# measures it against what the memory leaves).
HEAP_MIN = 384 * 1024


class BuildError(RuntimeError):
    pass


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def manifest():
    with open(os.path.join(CART, "manifest.json"), encoding="utf-8") as f:
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


def _units():
    """[(build or None, source path)] for every compile."""
    out = []
    for build in BUILDS:
        out += [(build, os.path.join(JET_SRC, u + ".cpp")) for u in JET_UNITS]
        out += [(build, os.path.join(CART, "src", u)) for u in SCENE_UNITS]
    out += [(None, os.path.join(CART, "src", u)) for u in CART_UNITS]
    return out


def _inputs_key(sdk, memory):
    """The hash of everything a build reads: the sources, the flags, the
    memory and the compiler."""
    h = hashlib.sha256()
    for d in (JET_SRC, os.path.join(CART, "src")):
        for name in sorted(os.listdir(d)):
            h.update(name.encode() + b"\0")
            with open(os.path.join(d, name), "rb") as f:
                h.update(f.read())
    h.update(json.dumps([CXXFLAGS, BUILDS, _units_rel(), memory, STACK]).encode())
    h.update(subprocess.run([os.path.join(sdk, "bin", "clang++"), "--version"],
                            capture_output=True, text=True).stdout.encode())
    return h.hexdigest()[:24]


def _units_rel():
    return [(b, os.path.relpath(p, ROOT)) for b, p in _units()]


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise BuildError("%s failed:\n%s%s" % (os.path.basename(cmd[0]), r.stdout, r.stderr))
    return r.stderr


def compile_wasm(sdk=None, memory=None, verbose=False):
    """main.wasm's bytes at `memory` bytes of linear memory (default: the
    manifest's). Cached by its inputs."""
    sdk = sdk or wasi_sdk()
    if sdk is None:
        raise BuildError("no wasi-sdk")
    memory = memory or manifest()["memory"] * PAGE
    key = _inputs_key(sdk, memory)
    cached = os.path.join(CACHE, "main-%s.wasm" % key)
    with _locked("build"):
        if not os.path.isfile(cached):
            _compile(sdk, memory, cached, verbose)
    with open(cached, "rb") as f:
        return f.read()


def _compile(sdk, memory, cached, verbose):
    cxx = os.path.join(sdk, "bin", "clang++")
    os.makedirs(CACHE, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        base = CXXFLAGS + ["-ffile-prefix-map=%s/=" % ROOT,
                           "-I", os.path.join(CART, "src"), "-I", JET_SRC]

        def one(i_unit):
            i, (build, src) = i_unit
            obj = os.path.join(tmp, "%03d_%s_%s.o" % (i, build or "cart",
                                                     os.path.basename(src)))
            warnings = _run([cxx] + base + BUILDS.get(build, []) + ["-c", src, "-o", obj])
            if warnings and verbose:
                sys.stderr.write(warnings)
            return obj

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            objs = list(pool.map(one, enumerate(_units())))
        out = os.path.join(tmp, "main.wasm")

        def link(mem, extra=()):
            _run([cxx, "--target=wasm32-wasi", "-nostartfiles", "-Wl,--no-entry",
                  "-Wl,--strip-all", "-Wl,--stack-first",
                  "-Wl,-z,stack-size=%d" % STACK,
                  "-Wl,--initial-memory=%d" % mem, "-Wl,--max-memory=%d" % mem]
                 + list(extra) + ["-o", out] + objs)
            with open(out, "rb") as f:
                return f.read()

        base_at = heap_base(link(memory, ["-Wl,--export=__heap_base"]))
        if memory - base_at < HEAP_MIN:
            raise BuildError("the manifest's memory (%d pages) leaves the heap %d KB "
                             "above the stack and data (%d KB); it needs %d KB"
                             % (memory // PAGE, (memory - base_at) // 1024,
                                base_at // 1024, HEAP_MIN // 1024))
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


def build(out_dir, chips=(), sdk=None, config=None, verbose=False):
    """The built cart folder `out_dir`/teapot.moy: the source folder, its
    main.wasm, and a signed module per chip in `chips`. `config` overrides
    config.json's keys (how a test builds a mode). Returns its path."""
    wasm = compile_wasm(sdk, verbose=verbose)
    man = manifest()
    cart = os.path.join(out_dir, os.path.basename(CART))
    if os.path.isdir(cart):
        shutil.rmtree(cart)
    shutil.copytree(CART, cart)
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
    ap.add_argument("out", nargs="?", help="where the built teapot.moy folder goes")
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
    try:
        cart = build(args.out, args.chip, verbose=args.verbose)
    except (BuildError, wasm_module.ToolError) as exc:
        print("jet_cart: %s" % exc, file=sys.stderr)
        return 2
    man = manifest()
    main_name = man.get("main", "main.wasm")
    print("%s: memory %d pages (%d KB)" % (cart, man["memory"], man["memory"] * PAGE // 1024))
    for name in sorted(os.listdir(cart)):
        path = os.path.join(cart, name)
        if os.path.isfile(path):
            print("  %-22s %9d bytes" % (name, os.path.getsize(path)))
    for chip in args.chip:
        size = os.path.getsize(os.path.join(cart, wasm_cart.aot_name(main_name, chip)))
        print("  %s module: %d bytes" % (chip, size))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
