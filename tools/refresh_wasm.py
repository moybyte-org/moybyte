#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Refresh a board's compiled-cart modules after a format-version bump.

    python3 tools/refresh_wasm.py --board tdeck
    python3 tools/refresh_wasm.py --board p4 system_carts ports

Docs: docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware"
(2026-09-30, ESP 88). A firmware update that bumps
native/moy_wasm/moy_wasm_key.h's MOY_WASM_FORMAT_VERSION does not strand a
cart already on a board -- it plays on the interpreter, with a short toast,
until its module is refreshed -- but a compiled cart is faster with one, so
this is the "replace the stale ones" half of that story: walk every compiled
(`"runtime": "wasm"`) cart folder this tree has the SOURCE for, under the
given paths (default: `system_carts/`, `ports/`), and push each one at
`--board` through `tools/push_cart.py` -- which already does the real work:
`push_cart.compiled_module` compiles a fresh UNSIGNED module whenever the
cart's own does not carry the key this board's build wants (wrong format,
wrong chip, wrong main.wasm) and leaves a module that already matches alone,
so a refresh run costs nothing beyond the (cheap) key check for a cart that
was never stale. `tools/push_cart.py`'s own rule decides what "matches"
means; this tool only decides WHICH carts to visit.

This walks SOURCE folders this repo (or the invoking checkout) has locally,
never the board's store -- there is no download path off a console
(`runtime/moy_sync.py` declines binary files for the same reason a compiled
cart's module never crosses between a browser and a board). A cart installed
on a console with no local source -- pushed by hand, or by someone else's
checkout -- is refreshed the same way any push refreshes it: `moy push`/
`tools/push_cart.py` again, from wherever its source lives.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_PATHS = ("system_carts", "ports")


def _is_compiled_cart(folder):
    try:
        with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(man, dict) and man.get("runtime") == "wasm"


def find_compiled_carts(paths):
    """Every `*.moy` folder under `paths` (repo-relative or absolute) whose
    manifest declares `"runtime": "wasm"`, sorted for a stable run order."""
    out = []
    for p in paths:
        base = p if os.path.isabs(p) else os.path.join(ROOT, p)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, _files in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if dirpath.endswith(".moy") and _is_compiled_cart(dirpath):
                out.append(dirpath)
                dirnames[:] = []        # a cart folder nests no carts
    return sorted(out)


def refresh(paths, board, port="auto", log=print):
    """Push every compiled cart under `paths` at `board`. Returns the number
    refreshed; a cart `push_cart` refuses (SystemExit) is reported and
    skipped rather than stopping the run -- one bad cart should not block
    the rest of the shelf."""
    import push_cart
    carts = find_compiled_carts(paths)
    if not carts:
        log("no compiled carts under %s" % ", ".join(paths))
        return 0
    done = 0
    for cart in carts:
        log("-- %s --" % os.path.relpath(cart, ROOT))
        try:
            rc = push_cart.main([cart, "--board", board, "--port", port])
        except SystemExit as exc:
            log("  refused: %s" % exc)
            continue
        if rc == 0:
            done += 1
        else:
            log("  push_cart exited %r" % rc)
    log("refreshed %d of %d compiled cart(s) on %s" % (done, len(carts), board))
    return done


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS),
                    help="where to look for compiled cart folders "
                         "(default: %s)" % ", ".join(DEFAULT_PATHS))
    ap.add_argument("--board", required=True,
                    help="which board to push to (tools/board.py's names)")
    ap.add_argument("--port", default="auto")
    a = ap.parse_args(argv)
    done = refresh(a.paths, a.board, a.port)
    return 0 if done or not find_compiled_carts(a.paths) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
