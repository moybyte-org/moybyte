"""One module namespace: a bare runtime name and its `runtime.` name are one
module object (`runtime.FlatFinder`), so no file that ships flat imports a
runtime module twice over, once by its device name and once through the
package, behind an `except ImportError`."""

import ast
import importlib
import os
import subprocess
import sys

import runtime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Every tree whose files a board or the browser stages by basename.
SHIPPED = ("runtime", "device", "firmware", "system_carts", "tests/fixtures")
NATIVE_FACES = {"moy_spine", "moy_app", "moy_ufiles", "moy_ui"}


def test_each_flat_name_is_its_runtime_module():
    names = sorted(runtime.FlatFinder.names)
    assert len(names) > 200
    for name in names:
        importlib.import_module("runtime." + name)
    split = [n for n in names
             if importlib.import_module(n) is not sys.modules["runtime." + n]
             or sys.modules[n].__spec__.name != "runtime." + n]
    assert split == []


def _binds(stmts):
    """{bound name: (module, attribute)} for an arm of plain imports, with
    the package spelled away; None for an arm that does anything else."""
    out, via = {}, False
    for s in stmts:
        if isinstance(s, ast.Import):
            for a in s.names:
                head, _, tail = a.name.partition(".")
                if head == "runtime" and tail and a.asname:
                    out[a.asname] = (tail, None)
                    via = True
                elif "." not in a.name:
                    out[a.asname or a.name] = (a.name, None)
                else:
                    return None, via
        elif isinstance(s, ast.ImportFrom):
            mod = s.module or ""
            pkg = s.level > 0 or mod == "runtime" or mod.startswith("runtime.")
            via |= pkg
            mod = mod[8:] if mod.startswith("runtime.") else mod
            for a in s.names:
                if pkg and mod in ("", "runtime"):
                    out[a.asname or a.name] = (a.name, None)
                else:
                    out[a.asname or a.name] = (mod, a.name)
        else:
            return None, via
    return out, via


def _two_arm(node, flat):
    if len(node.handlers) != 1:
        return False
    h = node.handlers[0]
    if not (isinstance(h.type, ast.Name) and h.type.id == "ImportError"):
        return False
    body = list(node.body)
    handler = list(h.body)
    if len(handler) == 1 and isinstance(handler[0], ast.Try):
        handler = handler[0].body          # an optional module's inner arm
    (a, via_a), (b, via_b) = _binds(body), _binds(handler)
    if a is None or b is None or via_a == via_b or a != b:
        return False
    return {m for m, _ in a.values()} <= flat


def _through_package(node):
    """`__import__("runtime." + name)`: the same fallback, spelled at run time."""
    if not (isinstance(node, ast.Call) and node.args and getattr(
            node.func, "id", getattr(node.func, "attr", None))
            in ("__import__", "import_module")):
        return False
    arg = node.args[0]
    while isinstance(arg, ast.BinOp):
        arg = arg.left
    return isinstance(arg, ast.Constant) and str(arg.value).startswith("runtime.")


def test_no_shipped_file_imports_through_both_names():
    flat = runtime.FlatFinder.names | NATIVE_FACES
    found = []
    tracked = subprocess.run(["git", "ls-files", "--", *SHIPPED], cwd=ROOT,
                             capture_output=True, text=True, check=True)
    for rel in tracked.stdout.split():
        if not rel.endswith(".py"):
            continue
        with open(os.path.join(ROOT, rel)) as fh:
            tree = ast.parse(fh.read())
        staged = not rel.startswith("runtime/host/")
        found += ["%s:%d" % (rel, n.lineno) for n in ast.walk(tree)
                  if isinstance(n, ast.Try) and _two_arm(n, flat)
                  or staged and _through_package(n)]
    assert found == [], ("import the device name alone: runtime.FlatFinder "
                         "makes it the runtime module on the host", found)
