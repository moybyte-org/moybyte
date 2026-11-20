#!/usr/bin/env python3
"""Check an app written against `uitree` without drawing it:

    tools/uitree_check.py MODULE_OR_PATH... [--snap]

For each app module, every error is printed with its code, its file:line and
its fix, and the run goes on past it:

1. Import the module: every declaration (state types, actions and hooks,
   `takes=`, `when=` with its `off=`, label tables, derives, work).
2. Read its source: every `s.NAME` read and write against the declared
   names, a literal written against its type, a nested write, a read or a
   write inside a page function, a row a handler is handed written into.
3. Run each page function once (a module-level function whose body opens
   `with ui.page(...)`). A page function reads no state, so one run visits
   every node: props, templates, conditions, actions, `takes=` at every
   site, switches and label tables, checked against the types.
4. Evaluate every bound prop for each size class (rows the models hold at
   their declared start; no handler runs, `opened` included).

Exit status 0 when there is no error. A module is named as Python imports
it (`perms_ui`) or by its path.
"""
import ast
import importlib
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import runtime  # noqa: E402,F401  -- the flat module namespace: `import uitree` resolves
import uitree as S  # noqa: E402

CLASSES = (("desk", "wide"), ("desk", "tall"), ("handheld", "wide"), ("handheld", "tall"))


class Recorder(S.Builder):
    """The engine as the checker sees it: builds, never draws."""

    def __init__(self, decl, build):
        self.decl = decl
        self.actions = decl.actions
        self.init_builder(decl.state, build)


class Result:
    def __init__(self, name):
        self.name = name
        self.errors = []
        self.pages = []
        self.nodes = self.props = 0
        self.snapshots = []

    def text(self):
        out = [e.text() for e in self.errors]
        out.append("check: %s: %d error%s; %d page%s, %d nodes, %d bound props evaluated over %d size classes"
                   % (self.name, len(self.errors), "" if len(self.errors) == 1 else "s",
                      len(self.pages), "" if len(self.pages) == 1 else "s", self.nodes, self.props,
                      len(CLASSES)))
        return "\n".join(out)


def _err(code, what, path, line, *, fix, **kw):
    return S.UIError(code, what, where=(os.path.basename(path), line), fix=fix, **kw)


def page_functions(tree):
    """Module-level functions whose body opens `with ui.page(...)`."""
    out = []
    for f in tree.body:
        if not isinstance(f, ast.FunctionDef):
            continue
        for node in ast.walk(f):
            if isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "attr", "") == "page"
                    for i in node.items):
                out.append(f.name)
                break
    return out


def _sname(x, names):
    if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id in names:
        return x.attr
    return None


def _truth_tests(node):
    if isinstance(node, (ast.If, ast.While, ast.IfExp)):
        yield node.test
    elif isinstance(node, ast.BoolOp):
        yield from node.values
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield node.operand


def source_pass(path, tree, mod, decl, pages):
    """What the source says about state without running a handler."""
    st = decl.state
    names = {k for k, v in vars(mod).items() if v is st}
    out = []
    builds = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in pages:
            builds.update(id(x) for x in ast.walk(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "after" and \
                len(node.args) == 2 and isinstance(node.args[1], ast.Constant):
            name = node.args[1].value
            if name not in decl.actions:
                out.append(_err("A01", "ui.after names no action %r" % name, path, node.lineno,
                                expected="one of " + (", ".join(decl.actions) or "(none declared)"),
                                fix='declare it: ui.hook(%r, handler)' % name))
            elif decl.actions[name].takes != "none":
                out.append(_err("A02", "%r is reached from ui.after, which hands nothing" % name,
                                path, node.lineno, fix='a timer\'s hook is takes="none"'))
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                pairs = [(t, node.value)]
                if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple) and \
                        len(t.elts) == len(node.value.elts):
                    pairs = list(zip(t.elts, node.value.elts))
                for tgt, val in pairs:
                    out += _store(tgt, val, node, names, st, path, builds)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and \
                isinstance(node.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
            for a, b in ((node.left, node.comparators[0]), (node.comparators[0], node.left)):
                if _sname(a, names) in st._t and isinstance(b, ast.Constant) and b.value is None:
                    out.append(_err("S08", "s.%s compared with None: it is never None" % _sname(a, names),
                                    path, node.lineno, expected="a value of %s" % st._t[_sname(a, names)],
                                    fix="compare with its empty value, or declare it ui.opt(...)"))
        for test in _truth_tests(node):
            n = _sname(test, names)
            if n in st._t and isinstance(st._t[n], S.Enum):
                out.append(_err("T06", "s.%s used as a truth value: an enum's every value is "
                                "a non-empty text" % n, path, test.lineno,
                                fix="compare it: s.%s != %r" % (n, st._t[n].values[0])))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names:
            if node.attr not in st._t:
                near = S._near(node.attr, st._t)
                out.append(_err("S01", "no state named %r" % node.attr, path, node.lineno,
                                fix=("did you mean %s?" % near) if near else
                                "declare it: ui.state(..., %s=<initial value>)" % node.attr))
            elif isinstance(node.ctx, ast.Load) and id(node) in builds:
                out.append(_err("B01", "s.%s read inside a page function" % node.attr, path, node.lineno,
                                fix='"{%s}" in a text, "%s" in a condition' % (node.attr, node.attr)))
    return out


def _store(tgt, val, node, names, st, path, builds):
    out = []
    base, depth = tgt, 0
    while isinstance(base, (ast.Attribute, ast.Subscript)):
        base = base.value
        depth += 1
    if not (isinstance(base, ast.Name) and base.id in names):
        return out
    if depth > 1:
        out.append(_err("S03", "a nested write into state", path, node.lineno,
                        fix="replace the value whole, or s.<model>.update(key, field=...)"))
        return out
    if not isinstance(tgt, ast.Attribute) or tgt.attr not in st._t:
        return out
    if id(node) in builds:
        out.append(_err("B02", "s.%s written inside a page function" % tgt.attr, path, node.lineno,
                        fix="write it in a handler; a page names its actions"))
    t = st._t[tgt.attr]
    if isinstance(node, ast.AugAssign):
        if t not in (S.INT, S.NUM, S.TEXT):
            out.append(_err("S02", "s.%s %s= on %s" % (tgt.attr, type(node.op).__name__, t), path, node.lineno,
                            fix="assign the whole new value"))
        return out
    try:
        v = ast.literal_eval(val)
    except (ValueError, SyntaxError, TypeError):
        return out
    try:
        S._check(t, v, "s." + tgt.attr)
    except S.UIError as e:
        e.where = (os.path.basename(path), node.lineno)
        out.append(e)
    return out


def row_writes(path, tree, decl):
    """A handler that is handed a row writing into it."""
    fns = {(n.lineno, n.name): n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    out = []
    for a in decl.actions.values():
        code = getattr(a.fn, "__code__", None)
        if code is None or a.takes != "row":
            continue
        fn = fns.get((code.co_firstlineno, code.co_name))
        if fn is None or not fn.args.args:
            continue
        p = fn.args.args[0].arg
        for node in ast.walk(fn):
            for t in getattr(node, "targets", None) or ([node.target] if isinstance(node, ast.AugAssign) else []):
                if isinstance(t, (ast.Attribute, ast.Subscript)) and \
                        isinstance(t.value, ast.Name) and t.value.id == p:
                    out.append(_err("S03", "a write into the row %r is handed (%s.%s = ...)"
                                    % (a.name, p, getattr(t, "attr", "[]")), path, node.lineno,
                                    fix="write it through the model: s.<model>.update(%s.<key>, field=...)" % p))
    return out


def dry(app):
    """Every bound prop, evaluated for each size class and each row."""
    n_ok = 0
    for cls in CLASSES:
        app.classes = cls
        S._NOW.app = app

        def walk(n, rows):
            nonlocal n_ok
            for row in rows:
                for g in n.p.values():
                    g(row)
                    n_ok += 1
            if n.kind in ("rows", "cards", "table"):
                rows = list(app.s._v[n.l["model"]]) or []
            for k in n.kids:
                walk(k, rows)
        try:
            walk(app.root, [None])
        finally:
            S._NOW.app = None
    return n_ok


def _nodes(n):
    yield n
    for k in n.kids:
        yield from _nodes(k)


def _import(target):
    """The module, imported fresh in the checker's collecting run."""
    if target.endswith(".py") or os.sep in target:
        path = os.path.abspath(target)
        name = os.path.basename(path)[:-3]
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod, path
    sys.modules.pop(target, None)
    mod = importlib.import_module(target)
    if getattr(mod, "__file__", None) is None:
        raise SystemExit("%s: not a module file" % target)
    return mod, mod.__file__


def check(target, snap=False):
    """The Result of checking one app module: every error, with file:line."""
    res = Result(target)
    S._NOW.collect = res.errors
    try:
        try:
            mod, path = _import(target)
        except S.UIError as e:
            res.errors.append(e)
            return res
        res.name = os.path.basename(path)
        try:
            decl = S.decl_of(mod)
        except S.UIError as e:
            res.errors.append(e)
            return res
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        res.pages = page_functions(tree)
        res.errors += source_pass(path, tree, mod, decl, set(res.pages))
        res.errors += row_writes(path, tree, decl)
        if not res.pages:
            res.errors.append(_err("P01", "no page function", path, 1,
                                   fix="def build():\n    with ui.page(\"Title\"):\n        ..."))
        for name in res.pages:
            app = Recorder(decl, getattr(mod, name))
            try:
                app._build()
            except S.UIError as e:
                res.errors.append(e)
                continue
            if app.root is None:
                continue
            res.nodes += sum(1 for _ in _nodes(app.root))
            try:
                res.props += dry(app)
            except S.UIError as e:
                res.errors.append(e)
            if snap:
                res.snapshots.append(S.snapshot(app))
    finally:
        S._NOW.collect = None
    seen, uniq = set(), []
    for e in res.errors:
        k = (e.code, tuple(e.where), e.what)
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    res.errors = uniq
    return res


def main(argv):
    snap = "--snap" in argv
    targets = [a for a in argv if a != "--snap"]
    if not targets or any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0 if targets else 2
    bad = 0
    for t in targets:
        r = check(t, snap)
        print(r.text())
        for sn in r.snapshots:
            print(sn)
        bad += len(r.errors)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
