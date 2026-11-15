"""The store is one body (docs/refactor_2026-11.md, phase 13): native/moy_store
on every tier, CPython included (runtime/host/moy_store_binding.py).

  * no module under runtime/carts defines a body for a function the C store
    exports: a def named after one of its rows (or the name runtime/carts'
    store modules call it by) only calls the row;
  * the trial's store (tests/fixtures/store_trial/, STORE_FORMAT 1) opens,
    unchanged, on the current tree, and a format step goes through the one
    door with the export before it and the warm bar after it;
  * a store archived and restored into an empty root reads back equal, file
    for file, thumbnails aside, and a re-seed after it leaves an edited
    built-in as restored.
"""

import ast
import json
import os
import shutil

import pytest

from runtime import moy_backup, moy_carts, moy_seed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "store_trial")

body = """def _draw():
    cls(1)
"""
ROSTER = [{"title": "Bench", "type": "game", "src": body, "cfg": {"speed": 2},
           "edit": [], "version": 3},
          {"title": "Untouched", "type": "game", "src": body, "cfg": {}, "edit": [],
           "version": 1}]


def _rows():
    """Every row of the C store's face, whichever tiers carry it."""
    with open(os.path.join(ROOT, "native", "moy_store", "face.json")) as f:
        rows = json.load(f)["moy_store"]["rows"]
    return {r["py"].split("(")[0] for r in rows}


def _aliases(tree):
    """The names a module binds the C store to: `import moy_store as x`,
    `from moy_fs import _native as x`."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.asname or a.name for a in n.names if a.name == "moy_store"}
        elif isinstance(n, ast.ImportFrom) and n.module == "moy_fs":
            out |= {a.asname or a.name for a in n.names if a.name == "_native"}
    return out


def _names_alias(node, alias):
    return any(isinstance(x, ast.Name) and x.id in alias for x in ast.walk(node))


def _fallback(node, alias):
    """`if <store> ...:`, or a try over the store that catches its absence."""
    if isinstance(node, ast.If):
        return _names_alias(node.test, alias)
    if isinstance(node, ast.Try):
        absent = any(isinstance(x, ast.Name) and x.id in ("ImportError", "AttributeError")
                     for h in node.handlers if h.type is not None for x in ast.walk(h.type))
        return absent and any(_names_alias(b, alias) for b in node.body)
    return False


def test_no_store_module_keeps_a_body_of_a_row_the_c_exports():
    """A twin is a def that reaches a row through the store AND branches on
    whether the store is there, a name bound to a row that is also def'd, or a
    hasattr gate on a row: none is left for any row of the store's face."""
    rows = _rows()
    bodies = []
    folder = os.path.dirname(os.path.abspath(moy_seed.__file__))
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".py"):
            continue
        with open(os.path.join(folder, f)) as fh:
            tree = ast.parse(fh.read())
        alias = _aliases(tree)
        bound, defs = set(), set()
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                defs.add(n.name)
            elif (isinstance(n, ast.Assign) and isinstance(n.value, ast.Attribute)
                  and isinstance(n.value.value, ast.Name) and n.value.value.id in alias
                  and n.value.attr in rows):
                bound |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        bodies += ["%s: %s" % (f, n) for n in sorted(bound & defs)]
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            reaches = any(isinstance(c, ast.Attribute) and isinstance(c.value, ast.Name)
                          and c.value.id in alias and c.attr in rows for c in ast.walk(fn))
            fallback = any(_fallback(b, alias) for b in ast.walk(fn))
            if reaches and fallback:
                bodies.append("%s: %s" % (f, fn.name))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "hasattr" and len(n.args) == 2
                    and isinstance(n.args[0], ast.Name) and n.args[0].id in alias
                    and isinstance(n.args[1], ast.Constant) and n.args[1].value in rows):
                bodies.append("%s: hasattr(%s)" % (f, n.args[1].value))
    assert bodies == [], "a body of a row the C store exports: %s" % bodies


# A name runtime/carts keeps for what a row computes: its def is the row's call
# and nothing else. `slug` stays a body of its own, since callers name a
# slug without the folder around it and the C has no row for one.
FRONTS = {"cart_folder": "folder"}


def test_a_name_kept_for_a_row_is_the_call_of_that_row():
    """`cart_folder` computes what the store's `folder` row computes: the def
    returns that row's call, whether or not some other body recomputes it."""
    rows = _rows()
    folder = os.path.dirname(os.path.abspath(moy_seed.__file__))
    found, bad = set(), []
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".py"):
            continue
        with open(os.path.join(folder, f)) as fh:
            tree = ast.parse(fh.read())
        alias = _aliases(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name not in FRONTS:
                continue
            found.add(fn.name)
            row = FRONTS[fn.name]
            assert row in rows
            body = [n for n in fn.body
                    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            call = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in alias and call.func.attr == row):
                bad.append("%s: %s" % (f, fn.name))
    assert found == set(FRONTS), "a front the test names is gone: %s" % (set(FRONTS) - found)
    assert bad == [], "a body of a row the C store exports: %s" % bad


def _tree(base, skip_thumbs=False):
    out = {}
    for d, dirs, files in os.walk(base):
        if skip_thumbs:
            dirs[:] = [x for x in dirs if x != "thumbs"]
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, base)] = f.read()
    return out


@pytest.fixture
def trial(tmp_path):
    base = str(tmp_path / "moybyte")
    shutil.copytree(FIXTURE, base)
    return base


def test_the_trial_store_opens_unchanged(trial):
    root = trial + "/carts"
    before = _tree(trial)
    assert moy_seed.load_store_version(root)[0] == moy_seed.STORE_FORMAT == 1
    moy_carts.ensure_dirs(root)
    moy_seed.sweep_store(root, ROSTER)
    moy_seed.seed_builtins(ROSTER, root)
    shelf = {c["title"] for c in moy_carts.catalogue(root)}
    assert shelf >= {"My Game", "Bench", "Untouched"}
    after = _tree(trial)
    after.pop(".publish", None)
    # A later RETIRED generation re-stamps the generation alone.
    assert after.pop("store.ver").split()[:2] == before.pop("store.ver").split()[:2]
    assert after == before
    assert "# edited" in moy_carts.load(root + "/moybyte.bench.moy")["src"]


def test_a_format_step_goes_through_the_door_after_the_export(trial, monkeypatch):
    root = trial + "/carts"
    seen = []

    def step(r):
        backups = os.listdir(moy_backup.backups_dir(r))
        seen.append((r, backups))
        with open(r + "/moybyte.bench.moy/stepped", "w") as f:
            f.write("2")

    monkeypatch.setattr(moy_seed, "STORE_FORMAT", 2)
    monkeypatch.setitem(moy_seed.FORMAT_STEPS, 1, step)
    moy_seed.sweep_store(root, ROSTER)
    assert seen == [(root, ["moybyte-format-1.zip"])]
    assert moy_seed.load_store_version(root) == (2, moy_seed.RETIRED_GEN)
    # The bar: a warm store's door is one small read.
    reads = []
    real = moy_seed._read

    def counted(p):
        reads.append(p)
        return real(p)
    monkeypatch.setattr(moy_seed, "_read", counted)
    assert moy_seed.sweep_store(root, ROSTER) == 0
    assert reads == [moy_seed.store_version_path(root)]


def test_an_export_that_cannot_be_written_runs_no_step(trial, monkeypatch):
    root = trial + "/carts"
    monkeypatch.setattr(moy_seed, "STORE_FORMAT", 2)
    monkeypatch.setitem(moy_seed.FORMAT_STEPS, 1, lambda r: pytest.fail("stepped"))

    def full(r, fmt):
        raise OSError(28, "full")
    assert moy_seed.sweep_store(root, ROSTER, export=full) == 0
    assert moy_seed.load_store_version(root)[0] == 1


def test_a_newer_stores_stamp_is_left_alone(trial):
    root = trial + "/carts"
    with open(moy_seed.store_version_path(root), "w") as f:
        f.write("moystore 9 1\n")
    assert moy_seed.sweep_store(root, ROSTER) == 0
    assert moy_seed.load_store_version(root) == (9, 1)


@pytest.mark.parametrize("line", ("", "8", "moystore 1\n", "moystore 1 8", "moystore x 8\n",
                                  "moystore 0 8\n", "moystore 1 8 9\n"))
def test_a_stamp_the_reader_does_not_read_is_no_stamp(tmp_path, line):
    root = str(tmp_path / "carts")
    os.makedirs(root)
    with open(moy_seed.store_version_path(root), "w") as f:
        f.write(line)
    assert moy_seed.load_store_version(root) is None


def test_an_archive_restored_into_an_empty_root_reads_back_equal(trial, tmp_path):
    root = trial + "/carts"
    thumbs = root + "/local.my_game.moy/thumbs"
    os.makedirs(thumbs)
    with open(thumbs + "/t.mct", "wb") as f:
        f.write(b"thumb")
    path, n = moy_backup.archive(root)
    assert path == moy_backup.backups_dir(root) + "/" + moy_backup.NAME and n > 0
    assert moy_backup.members(root)[:2] == ["backup.json", "store.ver"]

    fresh = str(tmp_path / "fresh" / "moybyte")
    croot = fresh + "/carts"
    os.makedirs(croot)
    assert moy_backup.restore(path, croot) == n

    want = _tree(trial, skip_thumbs=True)
    want = {k: v for k, v in want.items() if not k.endswith(".bak")
            and not k.startswith("files/Backups/") and k != ".publish"
            and not k.startswith("carts/moybyte.untouched.moy/")}
    got = {k: v for k, v in _tree(fresh).items() if k != ".publish"}
    assert got == want

    # The seed after it: the edited built-in at its version is left as restored,
    # the untouched one written.
    moy_seed.seed_builtins(ROSTER, croot)
    bench = moy_carts.load(croot + "/moybyte.bench.moy")
    assert "# edited" in bench["src"]
    assert os.path.isdir(croot + "/moybyte.untouched.moy")


def test_a_newer_firmwares_seed_keeps_a_restored_built_ins_saves(trial, tmp_path):
    root = trial + "/carts"
    path, _n = moy_backup.archive(root)
    fresh = str(tmp_path / "fresh" / "moybyte") + "/carts"
    os.makedirs(fresh)
    moy_backup.restore(path, fresh)
    newer = [dict(ROSTER[0], version=4)]
    moy_seed.seed_builtins(newer, fresh)
    assert "# edited" not in moy_carts.load(fresh + "/moybyte.bench.moy")["src"]
    with open(fresh + "/moybyte.bench.moy/pmem.json") as f:
        assert f.read() == "[42, 7]"


def test_what_is_not_a_backup_restores_nothing(trial, tmp_path):
    root = trial + "/carts"
    cart = root + "/local.my_game.moy"
    moy_carts.pack(cart, str(tmp_path / "cart.moy"))
    fresh = str(tmp_path / "fresh" / "moybyte") + "/carts"
    os.makedirs(fresh)
    with pytest.raises(ValueError):
        moy_backup.restore(str(tmp_path / "cart.moy"), fresh)
    assert os.listdir(fresh) == []


VM = r'''
import sys
sys.path.insert(0, @RUNTIME@)
import moy_backup, moy_seed
root = @BASE@ + "/carts"
path, n = moy_backup.archive(root)
print("archive", path[len(@BASE@):], n)
print("restore", moy_backup.restore(path, @FRESH@ + "/carts"))
'''


def test_the_backup_runs_on_the_boards_vm(trial, tmp_path):
    """MicroPython runs the archive and the restore as CPython does."""
    import subprocess
    from unix_mp import require_unix_mp, runtime_tree
    exe = require_unix_mp("moy_store", why="the backup on the VM a board runs")
    fresh = str(tmp_path / "fresh" / "moybyte")
    os.makedirs(fresh + "/carts")
    script = (VM.replace("@RUNTIME@", repr(runtime_tree())).replace("@BASE@", repr(trial))
              .replace("@FRESH@", repr(fresh)))
    out = subprocess.run([exe, "-c", script], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.split("\n")[:2] == [
        "archive /files/Backups/moybyte-backup.zip 16", "restore 16"]
    assert "# edited" in moy_carts.load(fresh + "/carts/moybyte.bench.moy")["src"]
