"""What an installed Python cart may import (runtime/carts/app_imports.py,
docs/os_2026-11/processes.md §2): the scan agrees with `ast`, the list
names the app API and no console module behind it, every Python cart the
tree carries passes, and the installer refuses one that does not."""

import ast
import json
import subprocess
from pathlib import Path

import pytest

from cart_store_fixtures import MemNet, Repo
from runtime import app_imports, cart_index as ci
from tools.board_config import runtime_file

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://carts.example/repo"

# Python carts the tree carries that import past the list, each with why.
BEYOND = {
    "moybyte.achievements.moy": "the console's own service, seeded with the "
                                "firmware and never installed",
}


def _ast_imports(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add("." if node.level else node.module.split(".")[0])
    return out


def _sources():
    for top in ("runtime", "device", "system_carts", "tests", "tools"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def test_the_scan_reads_what_ast_reads_over_the_whole_tree():
    wrong = []
    n = 0
    for path in _sources():
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        n += 1
        want = _ast_imports(tree)
        got = set(mod for _line, mod in app_imports.imports(src))
        if got != want:
            wrong.append("%s: scan %s, ast %s" % (path.relative_to(ROOT),
                                                  sorted(got - want), sorted(want - got)))
    assert n > 1000
    assert not wrong, "\n".join(wrong)


FORMS = [
    ("import a, b.c as d\n", [(1, "a"), (1, "b")]),
    ("from x.y import (\n  p,\n  q)\n", [(1, "x")]),
    ("if t: import a; import b\n", [(1, "a"), (1, "b")]),
    ('s = """\nimport no\n"""\nimport yes\n', [(4, "yes")]),
    ("x = 1  # import no\ndef f():\n    from . import z\n", [(3, ".")]),
    ("raise E from e\nyield_ = 'from a import b'\n", []),
    ("v = \\\n  1\nimport late\n", [(3, "late")]),
]


@pytest.mark.parametrize("src, want", FORMS)
def test_the_scan_reads_each_form(src, want):
    assert app_imports.imports(src) == want


def test_the_scan_reads_each_form_under_micropython(tmp_path):
    """The board runs the scan: MicroPython reads each form as CPython does."""
    from unix_mp import require_unix_mp, runtime_tree
    exe = require_unix_mp(why="the installer's scan runs on the boards")
    (tmp_path / "forms.json").write_text(json.dumps([src for src, _want in FORMS]))
    script = tmp_path / "run.py"
    script.write_text("import sys, json\nsys.path.insert(0, %r)\nimport app_imports\n"
                      "print(json.dumps([app_imports.imports(s) for s in "
                      "json.load(open(%r))]))\n" % (runtime_tree(), str(tmp_path / "forms.json")))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got == [[list(p) for p in want] for _src, want in FORMS]


def test_the_list_names_the_app_api_and_nothing_else_of_the_console():
    for mod in app_imports.PUBLIC:
        assert runtime_file(mod + ".py").is_file(), mod
    ours = {p.stem for p in (ROOT / "runtime").rglob("*.py")} \
        | {p.stem for p in (ROOT / "device").glob("*.py")}
    assert not ours & set(app_imports.STANDARD), sorted(ours & set(app_imports.STANDARD))


def _python_carts():
    for man in sorted(ROOT.glob("system_carts/*.moy/manifest.json")) \
            + sorted((ROOT / "tests" / "fixtures").rglob("*.moy/manifest.json")):
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if m.get("runtime") != "python":
            continue
        main = man.parent / (m.get("main") or "main.py")
        if main.is_file():
            yield man.parent, main


def test_every_python_cart_in_the_tree_imports_only_from_the_list():
    seen = set()
    bad = []
    for folder, main in _python_carts():
        refused = app_imports.refused(main.read_text(encoding="utf-8"))
        if folder.name in BEYOND:
            seen.add(folder.name)
            assert refused, "%s no longer imports past the list" % folder.name
        elif refused:
            bad.append("%s: %s" % (main.relative_to(ROOT), refused))
    assert not bad, "\n".join(bad)
    assert seen == set(BEYOND)


def _store(tmp_path):
    root = tmp_path / "moybyte" / "carts"
    root.mkdir(parents=True)
    return str(root)


def _install(repo, cid, root):
    cart = next(c for c in ci.parse_index(repo.index(), repo.url("index.json"))
                if c["id"] == cid)
    p = ci.plan(cart, "esp32s3", 2)
    job = ci.Install(cart, p, MemNet(repo.routes()), root, lambda fn: fn(), [])
    n = 0
    while job.step():
        n += 1
        assert n < 10000
    return job


def _py_cart(repo, cid, main):
    man = {"format": "moy-1", "title": "App", "runtime": "python", "main": "main.py",
           "moybyte": {"type": "app"}}
    repo.add(cid, runtime="python", files={
        "manifest.json": json.dumps(man).encode(), "main.py": main,
        "LICENSES.txt": b"MIT License\n"})


def test_the_installer_refuses_an_app_that_imports_the_console(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    _py_cart(repo, "x.peek", b"import math\ndef f():\n    import console, wm\n")
    job = _install(repo, "x.peek", root)
    assert job.error == ci.NOT_FOR_APPS % "console, wm"
    assert "main.py line 3 imports console" in job.detail
    assert not (Path(root) / "x.peek.moy").exists()
    assert "x.peek.moy" not in ci.load_record(root)


def test_the_installer_takes_an_app_that_imports_the_app_api(tmp_path):
    root = _store(tmp_path)
    repo = Repo(BASE)
    _py_cart(repo, "x.fine", b"import moybyte, ui\nfrom math import pi\n")
    job = _install(repo, "x.fine", root)
    assert job.error is None, job.detail
    assert (Path(root) / "x.fine.moy" / "main.py").is_file()
