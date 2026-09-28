#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Build a compiled ("runtime": "wasm") cart folder a host or a board can run.

    python3 tools/wasm_cart.py tests/fixtures/wasm/hello.moy /tmp/hello.moy
    python3 tools/wasm_cart.py tests/fixtures/wasm/hello.moy /tmp/hello.moy \\
        --chip esp32s3                  # ...plus the board's compiled module

A compiled cart's portable artifact is its `main.wasm` (proposals/
wasm-runtime.md, "The cart"), and no module is ever committed here: a cart in
this tree carries its SOURCE -- `src/main.wat` -- and this assembles it with the
vendored `tools/wat.py` (pure Python: no wabt, no wasm-ld, no network) into the
manifest's `main`. Everything else in the folder, `src/` included, is copied as
it is, so the Code tab has the source to show.

`--chip` adds the per-chip AOT module a board loads, built by
`tools/wasm_module.py` with the pinned compiler, carrying the provenance key
that board's build wants and signed with the OTA signing key (that tool says
where the key comes from). `--unsigned` leaves the signature off: a board runs
such a module only while its owner has Settings -> UNKNOWN SOURCES on, which
is how a cart rebuilt from its source runs on its builder's own console. How a board finds it is host policy, and it is one
rule, `aot_name`: the manifest's `main` with `.wasm` replaced by
`.<chip>.aot`, in the cart's folder -- `device/moycore_glue.py`'s `aot_path`
states the same rule and `tests/test_wasm_cart.py` holds the two equal. A
module compiled for another `main.wasm` is refused by its key's wasm hash.
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

SOURCE = os.path.join("src", "main.wat")


def aot_name(main, chip):
    """The compiled module's file name beside `main` for `chip`."""
    stem = main[:-5] if main.endswith(".wasm") else main
    return "%s.%s.aot" % (stem, chip)


def manifest(cart):
    with open(os.path.join(cart, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def build(src, dst, chips=(), signed=True):
    """Copy the cart at `src` to `dst`, assemble its source into its `main`,
    and compile a module per chip, signed unless `signed` is False. Returns
    {"main": path, chip: path, ...}."""
    man = manifest(src)
    if man.get("runtime") != "wasm":
        raise ValueError("%s is not a compiled cart (runtime %r)"
                         % (src, man.get("runtime")))
    main = man.get("main", "main.wasm")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    with open(os.path.join(src, SOURCE), encoding="utf-8") as f:
        blob = wat.assemble(f.read())
    out = {"main": os.path.join(dst, main)}
    with open(out["main"], "wb") as f:
        f.write(blob)
    if chips:
        from tools import wasm_module
        for chip in chips:
            out[chip] = os.path.join(dst, aot_name(main, chip))
            wasm_module.build(blob, chip, out[chip], signed=signed)
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
                         "only with Unknown sources on)")
    args = ap.parse_args(argv)
    for name, path in sorted(build(args.cart, args.out, args.chip,
                                   signed=not args.unsigned).items()):
        print("%-8s %s (%d bytes)" % (name, path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
