#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Nikola Jovicic
"""Refresh a board's compiled-cart modules after a format-version bump.

    python3 tools/board.py tdeck refresh-wasm
    python3 tools/board.py p4 refresh-wasm

Docs: docs/wasm_tier_plan_2026-09.md, "A cart survives its firmware"
(2026-09-30, ESP 88). A firmware update that bumps
native/moy_wasm/moy_wasm_key.h's MOY_WASM_FORMAT_VERSION does not strand a
cart already on a board -- it plays on the interpreter, with a short notice,
until its module is refreshed -- but a compiled cart is faster with one, so
this is the "replace the stale ones" half of that story.

WHAT IT WALKS: the board's OWN store, not this checkout's source tree. It
asks the live console for every `"runtime": "wasm"` cart it actually has
(`ws.carts.all`), because that -- not `system_carts/` or `ports/` -- is what
a format bump strands. Walking source folders instead used to refuse every
Jet cart outright: `ports/jet/*.moy` carries no `main.wasm` at all
(`tools/jet_cart.py` builds it from C++ on demand, "no module is ever
committed"), so a push from that folder always failed, and the tool still
exited 0 because OTHER carts it found locally had pushed fine.

GETTING A CART'S main.wasm: BUILT FROM A KNOWN LOCAL SOURCE, never read back
off the board. There is no bulk pull channel -- `recv` is push-only, and
carrying kilobytes of a module through the `py`/base64 round trip this tool's
own docstring used to warn against ("a board whose firmware predates `recv`
... gets one line saying to flash it") is exactly the slow path push_cart
already declines. A cart this checkout can name a recipe for -- right now
`tools/jet_cart.py` for everything under `ports/jet/`, matched by the
manifest TITLE the board reports (never a folder or store name: the device
seeds a cart's folder from the title's own slug, and a push's `--dest` keeps
the board's own folder name, which need not match the source's) -- gets a
freshly built `main.wasm`; a cart whose OWN folder under `system_carts/` or
`ports/` already carries one is pushed as it is. A cart with neither is
refused by name, the same answer this tool's docstring already gave a
hand-pushed cart with no source here: push it again from wherever its source
lives (Doom's is moybyte-org/carts, never this tree, by licence).

WHAT COUNTS AS STALE: whether `main.<chip>.f<format>.aot` -- this board's own
key, the name `device/moycore_glue.aot_path` looks for -- already sits beside
the cart's `main.wasm` on the board. When it does, the cart is already native
and nothing is rebuilt or pushed: a refresh costs one `listdir` for a cart
that was never stale. When it does not, this builds (or finds) main.wasm and
pushes through `tools/push_cart.py`'s own `--dest`, which compiles a fresh
UNSIGNED module itself whenever the source it was handed carries none for
this chip -- the same thing an ordinary push already does for any compiled
cart.

WHAT IT REMOVES: every `.aot` file left in the cart's folder besides the one
this board confirmed or wrote -- a foreign chip's module, or one from a
format this engine has moved past. A cart it pushes is left that way by the
push itself (`push_cart.push_files` keeps only the modules a push carries);
a cart already current is tidied here, since a refresh's whole job is what
one SPECIFIC board's storage holds now that its format is pinned.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

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
    manifest declares `"runtime": "wasm"`, sorted for a stable run order.
    Used only to find a LOCAL SOURCE with its own `main.wasm` already built
    (below) -- cart DISCOVERY for a refresh is the board's own store."""
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


def _manifest(folder):
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def known_sources(paths=DEFAULT_PATHS):
    """{title: ("jet", cart_name) | ("folder", local_path)} for every cart
    this checkout can produce a `main.wasm` for, keyed by the manifest TITLE
    a board's `ws.carts.all` reports -- never a folder or store name, which a
    board is free to install a cart under a different one of its own
    (`jet_push` installs "teapot.moy" as "jet_teapot.moy", for one).

    Jet's carts (`ports/jet/`) go first: `tools/jet_cart.py` builds their
    `main.wasm` from C++ on demand, so they are always current with THIS
    checkout's vendored Jet regardless of what is or is not already in their
    source folder. Anything else under `paths` whose own folder already
    carries a built `main.wasm` -- a `moy build`-produced cart, or a fixture
    committed with one -- is used exactly as it sits, no build step."""
    out = {}
    try:
        import jet_cart
    except ImportError:
        jet_cart = None
    if jet_cart is not None:
        for name in jet_cart.CARTS:
            out[jet_cart.manifest(name)["title"]] = ("jet", name)
    jet_dir = os.path.join(ROOT, "ports", "jet") + os.sep
    for folder in find_compiled_carts(paths):
        if folder.startswith(jet_dir):
            continue                    # no main.wasm here; jet_cart built it above
        try:
            man = _manifest(folder)
        except (OSError, ValueError):
            continue
        main = man.get("main", "main.wasm")
        if man.get("title") and os.path.isfile(os.path.join(folder, main)):
            out.setdefault(man["title"], ("folder", folder))
    return out


def materialize(title, known, work):
    """A local folder carrying `title`'s `main.wasm`, built or copied into
    `work`; None when `known` (`known_sources()`'s map) names no recipe for
    it. Never signs or compiles a per-chip module here -- `push_cart` already
    does that against whichever board it is handed to."""
    spec = known.get(title)
    if spec is None:
        return None
    kind, name = spec
    if kind == "jet":
        import jet_cart
        return jet_cart.build(work, chips=(), cart=name)
    dst = os.path.join(work, os.path.basename(name))
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(name, dst)
    return dst


def _listdir(board, port, path, verbose=False):
    import push_cart
    b = push_cart.connect(board, port, verbose)
    try:
        return b.pyval("sorted(__import__('os').listdir(%r))" % path,
                       timeout=20, strict=True)
    finally:
        b.close()


def _remove(board, port, path, names, verbose=False):
    import push_cart
    b = push_cart.connect(board, port, verbose)
    try:
        for n in names:
            b.pyval("__import__('os').remove(%r) or 1" % (path + "/" + n),
                    timeout=20, strict=True)
    finally:
        b.close()


def refresh(board, port, paths=DEFAULT_PATHS, log=print, verbose=False):
    """Refresh every compiled cart `board`'s own store has right now.
    Returns (refreshed, total): `total` is 0 when the board carries none (a
    board with no wasm engine at all reports the same), never an error."""
    import push_cart
    import wasm_cart
    chip = push_cart.board_chip(board)
    if not chip:
        log("%s declares no chip -- it takes no compiled cart modules" % board)
        return 0, 0
    carts = _list_installed(board, port, verbose)
    if not carts:
        log("no compiled carts on %s's store" % board)
        return 0, 0
    known = known_sources(paths)
    work = tempfile.mkdtemp(prefix="refresh_wasm-")
    done = 0
    try:
        for title, cart_path, main in carts:
            log("-- %s (%s) --" % (title, cart_path))
            wanted = wasm_cart.aot_name(main, chip)
            try:
                names = _listdir(board, port, cart_path, verbose)
            except Exception as exc:  # noqa: BLE001 -- a board hiccup, not this cart's fault
                log("  refused: could not read %s (%s)" % (cart_path, exc))
                continue
            if wanted not in names:
                src = materialize(title, known, work)
                if src is None:
                    log("  refused: no known source for %r -- push it again "
                        "from wherever its source lives" % title)
                    continue
                try:
                    rc = push_cart.main([src, "--board", board, "--port", port,
                                         "--dest", cart_path])
                except SystemExit as exc:
                    log("  refused: %s" % exc)
                    continue
                if rc != 0:
                    log("  push_cart exited %r" % rc)
                    continue
                done += 1
                continue
            log("  already current (%s)" % wanted)
            done += 1
            stale = [n for n in names if n.endswith(".aot") and n != wanted]
            if stale:
                try:
                    _remove(board, port, cart_path, stale, verbose)
                    log("  removed %s (not this console's module)"
                        % ", ".join(stale))
                except Exception as exc:  # noqa: BLE001 -- the cart itself is fine either way
                    log("  could not remove %s: %s" % (", ".join(stale), exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log("refreshed %d of %d compiled cart(s) on %s" % (done, len(carts), board))
    return done, len(carts)


def _list_installed(board, port, verbose=False):
    """[(title, path, main), ...] for every compiled cart `board`'s own
    store lists right now."""
    import push_cart
    b = push_cart.connect(board, port, verbose)
    try:
        return b.pyval(
            "[[c['title'], c['path'], c.get('main', 'main.wasm')] "
            "for c in ws.carts.all if c.get('runtime') == 'wasm']",
            timeout=30, strict=True)
    finally:
        b.close()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS),
                    help="local folders to look for a matching SOURCE in, "
                         "beside ports/jet (default: %s)"
                         % ", ".join(DEFAULT_PATHS))
    ap.add_argument("--board", required=True,
                    help="which board to refresh (tools/board.py's names)")
    ap.add_argument("--port", default="auto")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    done, total = refresh(a.board, a.port, a.paths, verbose=a.verbose)
    return 0 if done == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
