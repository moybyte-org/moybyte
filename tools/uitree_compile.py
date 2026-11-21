#!/usr/bin/env python3
"""Compile app modules' pages ahead of time for the C engine
(runtime/uitree/uitree_aot.py; the format is native/moy_uitree/README.md's
"A compiled page").

    tools/uitree_compile.py MODULE... [--out FILE]

Each module is checked as tools/uitree_check.py checks it (every reference
check); each of its page functions is then built on the C engine through the
host's library, lowered, and saved. A page the C engine does not draw yet
(an EngineGap) is skipped and named. The output is a Python module
(`uitree_pages`, frozen into an image beside the modules it compiles) whose
PAGES maps "module.function" to the blob; it is written only when its bytes
change, and the same sources always give the same bytes.
"""

import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "runtime", "host"))

import uitree_check  # noqa: E402  (puts runtime/ on the path)

import uitree as S  # noqa: E402
import uitree_aot  # noqa: E402
import uitree_c  # noqa: E402

# What a blob is built from beyond its page's module: the engine on both sides.
ENGINE_SOURCES = (
    "runtime/uitree/uitree.py", "runtime/uitree/uitree_app.py", "runtime/uitree/uitree_c.py",
    "runtime/uitree/uitree_aot.py", "native/moy_uitree/moy_uitree.h", "native/moy_uitree/moy_uitree_int.h",
    "native/moy_uitree/moy_uitree.c", "native/moy_uitree/moy_uitree_lang.c",
    "native/moy_uitree/moy_uitree_walk.c", "native/moy_uitree/moy_uitree_aot.c")


def fnv(h, data):
    for b in data:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def source_hash(module_path):
    """FNV-1a over the page's module and the engine's sources, in that order."""
    h = 2166136261
    for p in (module_path,) + tuple(os.path.join(ROOT, f) for f in ENGINE_SOURCES):
        with open(p, "rb") as f:
            h = fnv(h, f.read())
    return h


class Compiled:
    def __init__(self):
        self.pages = {}         # "module.function" -> blob
        self.skipped = []       # (page, why)


def compile_module(target, out):
    """Check `target` (refused whole on any error) and add each page's blob to `out`."""
    res = uitree_check.check(target)
    if res.errors:
        raise SystemExit(res.text())
    mod = sys.modules[res.name[:-3]] if res.name.endswith(".py") else sys.modules[target]
    path = mod.__file__
    decl = S.decl_of(mod)
    with open(path, encoding="utf-8") as f:
        pages = uitree_check.page_functions(ast.parse(f.read(), path))
    src = source_hash(path)
    for name in pages:
        fn = getattr(mod, name)
        page = uitree_aot.key(fn)
        eng = uitree_c.CEngine(decl, fn, None, None, None)
        try:
            eng._made()
            eng._build()
            eng.lower(eng.root)
            blob = eng._x.save(uitree_aot.VARIANT, src, uitree_aot.front(eng))
        except (uitree_c.EngineGap, uitree_aot.Refused) as e:
            out.skipped.append((page, str(e)))
            continue
        finally:
            eng.forget()
        if not isinstance(blob, bytes):
            raise SystemExit("%s: the store would not save (%s)" % (page, blob))
        out.pages[page] = blob


def stale(pages, paths=None):
    """The pages whose blob was made from other sources than the ones here
    (its header's source hash): `paths` maps a module to the file read in
    its place."""
    out = []
    for page, blob in sorted(pages.items()):
        mod = page.rpartition(".")[0]
        path = (paths or {}).get(mod)
        if path is None:
            path = sys.modules[mod].__file__ if mod in sys.modules else __import__(mod).__file__
        if int.from_bytes(blob[12:16], "little") != source_hash(path):
            out.append(page)
    return out


def module_text(c):
    lines = ['"""Pages compiled ahead of time by tools/uitree_compile.py: generated, never edited."""',
             "", "PAGES = {"]
    for k in sorted(c.pages):
        lines.append("    %r: %r," % (k, uitree_aot.entry(c.pages[k])))
    lines.append("}")
    return "\n".join(lines) + "\n"


def compile_modules(targets):
    was = uitree_aot.BLOBS
    uitree_aot.BLOBS = {}           # a page is built here, never loaded
    try:
        c = Compiled()
        for t in targets:
            compile_module(t, c)
        return c
    finally:
        uitree_aot.BLOBS = was


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("modules", nargs="+")
    ap.add_argument("--out", help="the uitree_pages module to write (default: print a summary only)")
    a = ap.parse_args(argv)
    c = compile_modules(a.modules)
    for page, why in c.skipped:
        print("skipped %s: %s" % (page, why))
    total = sum(len(b) for b in c.pages.values())
    print("%d pages, %d bytes" % (len(c.pages), total))
    for k in sorted(c.pages):
        print("  %-40s %6d" % (k, len(c.pages[k])))
    if a.out:
        text = module_text(c)
        old = None
        if os.path.exists(a.out):
            with open(a.out, encoding="utf-8") as f:
                old = f.read()
        if old != text:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
