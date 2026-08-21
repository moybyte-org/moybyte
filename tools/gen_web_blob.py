#!/usr/bin/env python3
"""Bake `firmware/web_runner/dist` INTO the firmware image.

The board serves a browser console (`moy_webhost`), and until now it served it
from a copy of the web build somebody had put on the board's storage by hand.
That copy drifts silently: on 2026-08-15 a board served a bundle old enough to
still carry a desktop-blackout bug that had been fixed in `dist/` hours before,
and nothing anywhere said so. The T-Deck cannot even be pushed to -- the push
tool hands the board a url over serial, and that board's USB-CDC RX is dead
under the desktop (CLAUDE.md, hard constraints), so its bundle went on by card
reader or not at all.

So the image carries one. This emits a C translation unit that `.incbin`s the
PRE-GZIPPED assets (the four in `moy_webhost.ASSETS`, 572,693 B against
1,155,953 B raw -- raw does not fit the T-Deck's slot at all) and exposes them
as a table the `moy_web` native module hands out as memoryviews. Storage still
WINS at serve time; see `moy_webhost._asset`.

WHY .incbin AND NOT ESP-IDF's EMBED_FILES: `EMBED_FILES` is an argument to
`idf_component_register`, and a MicroPython usermod is not a component -- it is
an INTERFACE library linked into the port's main component, whose registration
we do not own. `.incbin` is a toolchain feature, so
one generated file works on Xtensa, on RISC-V, and on the host compiler that
the test suite checks it with. What was never on the table is a Python `bytes`
literal: 573 KB of escaped source is ~2 MB of text through mpy-cross, and it
would land in the frozen heap rather than in flash.

Usage (both boards' build.sh call this before staging the module):

    tools/gen_web_blob.py --out <native>/moy_web/moy_web_blob.gen.c [--require]

With no bundle built it emits an EMPTY table and says so loudly; `--require`
(set by CI and by `MOYBYTE_REQUIRE_WEB_BUNDLE=1`) makes that a hard failure
instead. The default is soft because building the bundle needs emsdk (~1.7 GB)
and a firmware flash is the daily loop -- but an image published to a device
must never be the one that quietly has no console, which is what --require is
for. Same doctrine as `MOYBYTE_REQUIRE_UNIX_MP` (CLAUDE.md).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_config                                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_RUNNER = os.path.join(ROOT, "firmware", "web_runner")
TDECK_MODULES = os.path.join(
    ROOT, "device")
DEFAULT_DIST = os.path.join(ROOT, "firmware", "web_runner", "dist")
DEFAULT_OUT = os.path.join(
    ROOT, "native", "moy_web",
    "moy_web_blob.gen.c")


def asset_names():
    """The files a browser needs, read from `moy_webhost.ASSETS` itself.

    Not a second list. The allowlist the server serves from and the set the
    image bakes have to be the same set -- an asset in one and not the other is
    either a 404 on a board that has the bytes, or dead weight in a 5 MB slot.
    """
    path = os.path.join(TDECK_MODULES, "moy_webhost.py")
    # The modules dir on sys.path, because moy_webhost imports its sibling
    # transport by plain name the way the device does.
    if TDECK_MODULES not in sys.path:
        sys.path.insert(0, TDECK_MODULES)
    spec = importlib.util.spec_from_file_location("_moy_webhost_for_blob", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.ASSETS)


def pick(dist, name):
    """(path, served_name) for one asset -- the .gz if there is one.

    The board serves `<name>.gz` with `Content-Encoding: gzip` and the browser
    inflates it, so the gz is what gets baked; the served name keeps the `.gz`
    suffix exactly as the on-storage lookup does, which is what lets
    `_asset()` use one rule for both sources.
    """
    gz = os.path.join(dist, name + ".gz")
    if os.path.exists(gz):
        return gz, name + ".gz"
    raw = os.path.join(dist, name)
    if os.path.exists(raw):
        return raw, name
    return None, None


def collect(dist):
    """[(served_name, path, size, sha256)] or [] when the bundle is incomplete.

    ALL OR NOTHING: a console missing its wasm is not a partial console, it is
    a page that fails to boot with a browser-console error nobody will read.
    """
    out, missing = [], []
    for name in asset_names():
        path, served = pick(dist, name)
        if path is None:
            missing.append(name)
            continue
        data = open(path, "rb").read()
        out.append((served, path, len(data), hashlib.sha256(data).hexdigest()))
    if missing:
        return [], missing
    return out, []


def render(assets, dist):
    """The C file. One `.incbin` per asset plus the table that names them."""
    total = sum(a[2] for a in assets)
    digest = hashlib.sha256()
    for served, _path, size, sha in assets:
        digest.update(("%s:%d:%s;" % (served, size, sha)).encode())
    stamp = "%d %d %s" % (len(assets), total, digest.hexdigest()[:12])

    L = []
    L.append("/* AUTO-GENERATED by tools/gen_web_blob.py -- do not edit, do")
    L.append(" * not commit (gitignored). Regenerated by both boards' build.sh")
    L.append(" * before the moy_web module is staged.")
    L.append(" *")
    L.append(" * Source bundle: %s" % dist)
    for served, _path, size, sha in assets:
        L.append(" *   %-20s %8d B  %s" % (served, size, sha[:16]))
    L.append(" *   %-20s %8d B  total" % ("", total))
    L.append(" */")
    L.append('#include "moy_web_blob.h"')
    L.append("")
    if not assets:
        L.append("/* No web bundle was built when this image was compiled. The")
        L.append(" * module still exists and reports zero assets, so the server")
        L.append(" * falls through to its \"no bundle\" 404 rather than to an")
        L.append(" * ImportError in the middle of a request. */")
        L.append("static const unsigned char moy_web_blob_none[1] = { 0 };")
        L.append("const moy_web_asset_t moy_web_assets[1] = {")
        L.append('    { "", moy_web_blob_none, 0u },')
        L.append("};")
        L.append("const unsigned int moy_web_asset_count = 0u;")
        L.append('const char moy_web_stamp[] = "0 0 none";')
        return "\n".join(L) + "\n"

    # The blobs, in ONE asm block: `.section .rodata` puts them where the ESP32
    # linker maps flash (DROM, memory-mapped and readable by an ordinary load),
    # so nothing is copied into RAM at boot and a memoryview over one costs the
    # object header and no bytes. `.balign 4` because an unaligned symbol is
    # legal here but makes every downstream word access a trap on some targets;
    # `.previous` restores whatever section the compiler was in.
    L.append("__asm__(")
    L.append('    ".section .rodata\\n"')
    for i, (served, path, size, _sha) in enumerate(assets):
        # The path goes inside a C string inside an asm string. A quote or a
        # backslash in it would produce an assembler error naming a line
        # nobody wrote, so say the real thing instead.
        if '"' in path or "\\" in path:
            raise SystemExit("gen_web_blob: cannot .incbin a path containing "
                             "a quote or backslash: %s" % path)
        L.append('    /* %s (%d B) */' % (served, size))
        L.append('    ".balign 4\\n"')
        L.append('    ".global moy_web_blob%d\\n"' % i)
        L.append('    "moy_web_blob%d:\\n"' % i)
        L.append('    ".incbin \\"%s\\"\\n"' % path)
    L.append('    ".balign 4\\n"')
    L.append('    ".previous\\n"')
    L.append(");")
    L.append("")
    for i in range(len(assets)):
        L.append("extern const unsigned char moy_web_blob%d[];" % i)
    L.append("")
    L.append("const moy_web_asset_t moy_web_assets[] = {")
    for i, (served, _path, size, _sha) in enumerate(assets):
        L.append('    { "%s", moy_web_blob%d, %du },' % (served, i, size))
    L.append("};")
    L.append("const unsigned int moy_web_asset_count = %du;" % len(assets))
    # The stamp is CODE, not a comment, and that is deliberate: ccache hashes
    # the PREPROCESSED source, where comments are already gone -- and `.incbin`
    # content is not part of that hash at all. Without a real string carrying
    # the bundle's digest, a rebuild after a new web build would be a ccache
    # HIT and the image would keep the old console. It is also the answer to
    # "which bundle is on this board?": moy_web.stamp() over serial.
    L.append('const char moy_web_stamp[] = "%s";' % stamp)
    return "\n".join(L) + "\n"


def watched_sources(root=ROOT):
    """Every source the bundle is built FROM, relative to the repo root.

    DERIVED from the web runner's own `board.toml` staging declaration, never
    globbed. A hand-written `runtime/*.py` glob cannot see `device/`, and that
    tree holds `device_canvas.py` -- the class the browser RASTERIZES with since
    moycore stage 4. The single most consequential file in the bundle was the
    one file the staleness check could not look at.

    Plus the runner's own authored modules and page (`web_boot.py`,
    `web_canvas.py`, the driver html/js), which build.sh copies by name and no
    board file describes -- its header says so.
    """
    rels = set()
    try:
        staged = board_config.staged_modules(WEB_RUNNER, root=ROOT)
        pkgs = board_config.staged_packages(WEB_RUNNER, root=ROOT)
    except Exception:  # a broken board file is a build failure, not this warning
        staged, pkgs = {}, {}
    for src in staged.values():
        rels.add(os.path.relpath(str(src), ROOT))
    for pkg in pkgs.values():
        for base, _dirs, names in os.walk(str(pkg)):
            for n in names:
                if n.endswith(".py"):
                    rels.add(os.path.relpath(os.path.join(base, n), ROOT))
    web = os.path.join("firmware", "web_runner")
    d = os.path.join(root, web)
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.endswith((".py", ".js", ".html")):
            rels.add(os.path.join(web, name))
    return sorted(rels)


def stale_sources(assets, root=ROOT, limit=3):
    """Console sources NEWER than the bundle about to be baked in.

    The staleness baking cannot fix. `p4_push_web.py` compares dist against the
    BOARD, so a dist that is itself behind `runtime/` pushes -- and now bakes --
    a stale console while reporting success. The image then serves a browser
    console older than the firmware it is part of, which is the original bug
    wearing the fix's clothes.

    A warning, never fatal: mtimes are a heuristic (a checkout reorders them, a
    touched file means nothing), and a build that refuses on a heuristic is a
    build people learn to work around.
    """
    if not assets:
        return []
    oldest = min(os.path.getmtime(a[1]) for a in assets)
    out = []
    for rel in watched_sources(root):
        try:
            newer = os.path.getmtime(os.path.join(root, rel)) > oldest
        except OSError:
            continue
        if newer:
            out.append(rel)
            if len(out) >= limit:
                break
    return out


def write_if_changed(path, text):
    try:
        if open(path, "r").read() == text:
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", default=DEFAULT_DIST,
                    help="the web runner's built bundle (default: %(default)s)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="the C file to write (default: the moy_web module)")
    ap.add_argument("--require", action="store_true",
                    help="fail instead of emitting an empty bundle")
    ap.add_argument("--quiet", action="store_true",
                    help="no banner when there is no bundle -- for callers "
                         "that are not building a board image (the unix test "
                         "binary), where 'this image has no console' is not "
                         "the news it sounds like")
    args = ap.parse_args(argv)

    assets, missing = collect(args.dist)
    if missing:
        msg = ("no web console to bake into this image: %s missing from %s"
               % (", ".join(missing), args.dist))
        # Strict under CI unless explicitly opted out: an image PUBLISHED to a
        # device must never be the one that quietly has no console, which is
        # the whole failure this feature exists to end. A local flash is the
        # daily loop and building the bundle needs emsdk (~1.7GB), so there it
        # only warns. Same shape as MOYBYTE_REQUIRE_UNIX_MP (CLAUDE.md).
        req = os.environ.get("MOYBYTE_REQUIRE_WEB_BUNDLE")
        if args.require or req == "1" or (req is None
                                          and os.environ.get("CI")):
            sys.stderr.write(
                "!! %s\n"
                "!! build it first:  firmware/web_runner/build.sh\n"
                "!! (unset MOYBYTE_REQUIRE_WEB_BUNDLE / drop --require to build\n"
                "!!  an image whose WEB CONSOLE row serves nothing)\n" % msg)
            return 1
        if not args.quiet:
            sys.stderr.write(
                "\n"
                "!! ============================================================\n"
                "!! %s\n"
                "!! This image will have NO BROWSER CONSOLE baked in. The board\n"
                "!! will serve one only if a bundle is copied onto its storage.\n"
                "!! Build it with firmware/web_runner/build.sh and rebuild.\n"
                "!! ============================================================\n"
                "\n" % msg)

    changed = write_if_changed(args.out, render(assets, args.dist))
    if assets:
        total = sum(a[2] for a in assets)
        print("web console baked in: %d assets, %d B (%d KB)%s"
              % (len(assets), total, total // 1024,
                 "" if changed else " -- unchanged"))
        stale = [] if args.quiet else stale_sources(assets)
        if stale:
            sys.stderr.write(
                "!! the bundle being baked in is OLDER than console sources, "
                "e.g.\n%s\n!! rebuild it (firmware/web_runner/build.sh) or "
                "this image ships a stale browser console\n"
                % "\n".join("!!     " + s for s in stale))
    return 0


if __name__ == "__main__":
    sys.exit(main())
