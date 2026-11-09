#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Build a compiled ("runtime": "wasm") cart folder a host or a board can run.

    python3 tools/wasm_cart.py tests/fixtures/wasm/hello.moy /tmp/hello.moy
    python3 tools/wasm_cart.py tests/fixtures/wasm/hello.moy /tmp/hello.moy \\
        --chip esp32s3                  # ...plus the board's compiled module

A compiled cart's portable artifact is its `main.wasm` (moy-spec SPEC.md
16.1), and no module is ever committed here: a cart in
this tree carries its SOURCE -- `src/main.wat` -- and this assembles it with the
vendored `tools/wat.py` (pure Python: no wabt, no wasm-ld, no network) into the
manifest's `main`. A WASI command may carry `src/main.c` or `src/main.rs`
instead, built by wasi-sdk or rustc's `wasm32-wasip1` as any program is
(`assemble`). Everything else in the folder, `src/` included, is copied as
it is, so the Code tab has the source to show.

`--chip` adds the per-chip AOT module a board loads, built by
`tools/wasm_module.py` with the pinned compiler, carrying the provenance key
that board's build wants and signed with the OTA signing key (that tool says
where the key comes from). `--unsigned` leaves the signature off: a board runs
such a module natively only for a cart whose consent grants "full_access" (its
manifest asks for it), which is how a cart rebuilt from its source runs on its
builder's own console.

A `.moy` carries `main.wasm` plus any number of these, one per chip and
compiled-code format version it has been built for (SPEC.md 16: "a host may
keep a compiled form of the module beside it"), so the cart stays portable off
a console and nothing is ever evicted to make room for a fresh one -- each
module just counts toward the cart's size. How a board finds ITS module is
host policy, and it is one rule, `aot_name`: the manifest's `main` with
`.wasm` replaced by `.<chip>.f<format>.aot`, in the cart's folder --
`device/moycore_glue.py`'s `aot_path` states the same rule (reading the
running engine's own chip and format, `_moy_wasm.CHIP`/`_moy_wasm.FORMAT`) and
`tests/test_wasm_cart.py` holds the two equal. A module built for a format
this console's engine has moved past is simply the wrong file name -- it is
never opened, never refused, and the cart runs on the interpreter instead
(native/moy_wasm/README.md, "A cart survives its firmware").

An app's own service (`"moybyte": {"entries": {"service": "<stem>.wasm"}}`,
docs/app_api_v1.md section 7) is a WASI command built the same way from
`src/<stem>.wat`, `.c` or `.rs` into `<stem>.wasm`, with its per-chip modules
`aot_name(<stem>.wasm, chip)` beside it, signed alike; the cart's own `main`
may be a Python or Lua script, which is copied as it is.
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import wat  # noqa: E402

def service_entry(man):
    """The `.wasm` service entry the manifest declares, or None."""
    got = (man.get("moybyte") or {}).get("entries")
    f = got.get("service") if isinstance(got, dict) else None
    return f if isinstance(f, str) and f.endswith(".wasm") and "/" not in f else None


def aot_name(main, chip, format=None):
    """The compiled module's file name beside `main` for `chip` and a
    compiled-code format version (default: this tree's own,
    tools.wasm_module.format_version())."""
    if format is None:
        from tools import wasm_module
        format = wasm_module.format_version()
    stem = main[:-5] if main.endswith(".wasm") else main
    return "%s.%s.f%s.aot" % (stem, chip, format)


def manifest(cart):
    with open(os.path.join(cart, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


class ToolchainMissing(RuntimeError):
    """The cart's source needs a compiler this machine lacks."""


def assemble(src, stem="main"):
    """The module the cart at `src` builds from its source: `src/main.wat`
    by the vendored assembler; a WASI command's `src/main.c` by wasi-sdk's
    clang (`tools/jet_cart.py` finds or fetches it) or `src/main.rs` by
    rustc for `wasm32-wasip1`, each as the toolchain builds any program,
    with no flag of ours but the SDK's headers on a C program's include path
    (`moybyte/os.h`: the event descriptor and the moybyte.os imports) and,
    for a C cart that is no WASI program (an app or a game: no command or
    service), clang's reactor model, so the exports it names (`_init`,
    `_update`, `_draw`) are its loop and no `_start` or `main` is linked,
    and its memory fixed at the manifest's `memory` pages, as a cart's is;
    its DWARF stripped (`strip_debug`). A `stem` other than main is an app's
    service entry, `src/<stem>.*`, always a WASI command."""
    wat_src = os.path.join(src, "src", stem + ".wat")
    if os.path.isfile(wat_src):
        with open(wat_src, encoding="utf-8") as f:
            return wat.assemble(f.read())
    import subprocess
    import tempfile
    c_src = os.path.join(src, "src", stem + ".c")
    rs_src = os.path.join(src, "src", stem + ".rs")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "main.wasm")
        if os.path.isfile(c_src):
            from tools import jet_cart
            sdk = jet_cart.wasi_sdk()
            if sdk is None:
                raise ToolchainMissing("no wasi-sdk")
            cmd = [os.path.join(sdk, "bin", "clang"), "--target=wasm32-wasip1",
                   "--sysroot=" + os.path.join(sdk, "share", "wasi-sysroot"), "-O2",
                   "-I", os.path.join(ROOT, "sdk", "include"), "-o", out, c_src]
            man = manifest(src)
            if stem == "main" and (man.get("moybyte") or {}).get("type") \
                    not in ("command", "service"):
                mem = "%d" % (int(man.get("memory") or 1) * 65536)
                cmd[-3:-3] = ["-mexec-model=reactor", "-Wl,--initial-memory=" + mem,
                              "-Wl,--max-memory=" + mem]
        elif os.path.isfile(rs_src):
            rustc = shutil.which("rustc")
            if rustc is None or not rust_wasip1():
                raise ToolchainMissing("no rustc with the wasm32-wasip1 target")
            cmd = [rustc, "--target", "wasm32-wasip1", "-O", "--edition", "2021",
                   "-o", out, rs_src]
        else:
            raise FileNotFoundError("%s has no src/%s.wat, .c or .rs" % (src, stem))
        # a sanitizer the caller's process preloads (tools/asan_pytest.sh) is not the compiler's
        env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
        subprocess.run(cmd, check=True, capture_output=True, env=env)
        with open(out, "rb") as f:
            return strip_debug(f.read())


def strip_debug(blob):
    """`blob` less its DWARF (the `.debug_*` custom sections a toolchain's
    libraries carry in), as a release build's strip leaves it: the code, the
    data and every other section byte for byte."""
    out = bytearray(blob[:8])
    i = 8
    while i < len(blob):
        start = i
        sid = blob[i]
        i += 1
        n = shift = 0
        while True:
            b = blob[i]
            i += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                break
        body = blob[i:i + n]
        i += n
        if sid == 0 and body[1:1 + body[0]].startswith(b".debug_"):
            continue
        out += blob[start:i]
    return bytes(out)


def rust_wasip1():
    """Whether rustc here has the wasm32-wasip1 standard library."""
    import subprocess
    rustc = shutil.which("rustc")
    if rustc is None:
        return False
    got = subprocess.run([rustc, "--print", "target-libdir", "--target", "wasm32-wasip1"],
                         capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "LD_PRELOAD"})
    return got.returncode == 0 and os.path.isdir(got.stdout.strip())


def build(src, dst, chips=(), signed=True):
    """Copy the cart at `src` to `dst`, assemble its source into its `main`
    and its service entry's, and compile a module of each per chip, signed
    unless `signed` is False. Returns {"main": path, chip: path, "service":
    path, "service." + chip: path, ...}, the main's where it is compiled."""
    man = manifest(src)
    entry = service_entry(man)
    if man.get("runtime") != "wasm" and entry is None:
        raise ValueError("%s is not a compiled cart (runtime %r) and has no wasm service"
                         % (src, man.get("runtime")))
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    jobs = []
    if man.get("runtime") == "wasm":
        jobs.append(("main", "main", man.get("main", "main.wasm")))
    if entry is not None:
        jobs.append(("service", entry[:-5], entry))
    out = {}
    for key, stem, name in jobs:
        blob = assemble(src, stem)
        out[key] = os.path.join(dst, name)
        with open(out[key], "wb") as f:
            f.write(blob)
        if chips:
            from tools import wasm_module
            for chip in chips:
                k = chip if key == "main" else key + "." + chip
                out[k] = os.path.join(dst, aot_name(name, chip))
                wasm_module.build(blob, chip, out[k], signed=signed)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cart", help="the cart folder carrying src/main.wat")
    ap.add_argument("out", help="where the runnable cart folder goes")
    ap.add_argument("--chip", action="append", default=[],
                    help="also compile the module for this chip (esp32s3, "
                         "esp32p4); repeatable")
    ap.add_argument("--unsigned", action="store_true",
                    help="leave the modules' signatures off (a board runs them "
                         "natively only for a cart granted \"full_access\")")
    args = ap.parse_args(argv)
    for name, path in sorted(build(args.cart, args.out, args.chip,
                                   signed=not args.unsigned).items()):
        print("%-8s %s (%d bytes)" % (name, path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
