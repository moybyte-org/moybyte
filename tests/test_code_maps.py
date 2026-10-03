"""A big module opens with a map, and the map names what is there.

Every module over MAP_BYTES under runtime/, device/ and tools/ (and
tests/on_glass.py) carries a comment block above its docstring:

    # Map (grep -n a name to jump there):
    #   Workstation              the console: canvases, the process stack, ...
    #   Workstation.frame        one console frame
    #   -- the layer stack       the compositor and router's layer list

so an agent jumps with `grep -n` instead of reading the file whole. An entry
is a top-level def or class, a `Class.method`, or `-- ` and the words of a
section banner in the file. This pins that the map is there, that every name
in it resolves (a rename breaks the map here, not in the next reader's
session), and that every public top-level class is on it.
"""

import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_BYTES = 30_000
HEADER = "# Map (grep -n a name to jump there):"
# Vendored from moy-spec (`make vendor-p8-import`): their maps belong upstream.
VENDORED = {"tools/p8_import.py", "tools/p8_lua_port.py"}


def big_modules():
    out = []
    for d in ("runtime", "device", "tools"):
        for base, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(base, f), ROOT)
                if (f.endswith(".py") and rel not in VENDORED
                        and os.path.getsize(os.path.join(ROOT, rel)) > MAP_BYTES):
                    out.append(rel)
    return out + ["tests/on_glass.py"]


def read_map(lines):
    """([(name, description)], the index of the first line after the map)
    for the map block at the top of `lines`, or (None, 0)."""
    i = 1 if lines and lines[0].startswith("#!") else 0
    if lines[i:i + 1] != [HEADER]:
        return None, 0
    out = []
    for j in range(i + 1, len(lines)):
        if not lines[j].startswith("#   "):
            break
        m = re.match(r"(\S.*?)(?: {2,}(.*))?$", lines[j][4:].rstrip())
        out.append((m.group(1), m.group(2) or ""))
    return out, i + 1 + len(out)


def defined(tree):
    """{top-level name: node} and {(class, method)}."""
    top, methods = {}, set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            top[n.name] = n
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.add((n.name, m.name))
    return top, methods


@pytest.mark.parametrize("rel", big_modules())
def test_a_big_module_opens_with_a_map_that_resolves(rel):
    with open(os.path.join(ROOT, rel)) as f:
        src = f.read()
    lines = src.splitlines()
    entries, after = read_map(lines)
    assert entries, "%s is over %d bytes and opens without `%s`" % (
        rel, MAP_BYTES, HEADER)
    assert len(entries) <= 30, "%s: a map is short -- %d entries" % (
        rel, len(entries))
    top, methods = defined(ast.parse(src))
    comments = [ln.strip() for ln in lines[after:] if ln.strip().startswith("#")]
    named = set()
    for name, desc in entries:
        assert desc, "%s: `%s` says nothing about itself" % (rel, name)
        if name.startswith("-- "):
            words = name[3:]
            assert any(words in c for c in comments), (
                "%s: no section banner says %r" % (rel, words))
            continue
        cls, _, meth = name.partition(".")
        if meth:
            assert (cls, meth) in methods, "%s: no %s" % (rel, name)
        else:
            assert name in top, "%s: no top-level %s" % (rel, name)
        named.add(cls)
    public = {n for n, node in top.items()
              if isinstance(node, ast.ClassDef) and not n.startswith("_")}
    assert not public - named, "%s: classes missing from its map: %s" % (
        rel, ", ".join(sorted(public - named)))
