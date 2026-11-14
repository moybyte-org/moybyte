"""The Workstation's services, read from the source: each `self.<attr> =
<Class>(self)` its `__init__` makes, with the module console.py imports the
class from (docs/refactor_2026-11.md, phase 9)."""

import ast

from tools.board_config import runtime_file

CONSOLE = runtime_file("console.py")


def classdef(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("%s is not a class in %s" % (name, path))


def services():
    """[(attr, class name, module, ClassDef)] in the order `__init__` builds
    them."""
    tree = ast.parse(CONSOLE.read_text(encoding="utf-8"))
    imported = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = node.module.split(".")[-1]
    ws = classdef(CONSOLE, "Workstation")
    init = next(f for f in ws.body
                if isinstance(f, ast.FunctionDef) and f.name == "__init__")
    out = []
    for stmt in init.body:
        if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
            continue
        tgt, val = stmt.targets[0], stmt.value
        if (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                and tgt.value.id == "self" and isinstance(val, ast.Call)
                and isinstance(val.func, ast.Name)
                and [ast.unparse(a) for a in val.args] == ["self"]
                and val.func.id in imported
                and imported[val.func.id].startswith("console_")):
            mod = imported[val.func.id]
            out.append((tgt.attr, val.func.id, mod,
                        classdef(runtime_file(mod + ".py"), val.func.id)))
    return out
