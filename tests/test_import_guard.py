"""tests/import_guard.py: a module this checkout does not hold fails to import
in the suite, as it does in CI, through either door it once came in by."""

import importlib.machinery
import os
import subprocess
import sys
import types

import pytest

import import_guard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_finder_resolves_a_runtime_submodule_outside_the_path_it_is_given(
        tmp_path):
    # The editable finder's shape: handed an empty directory as the package
    # path, it still answered with the main checkout's file.
    for finder in sys.meta_path:
        find = getattr(finder, "find_spec", None)
        if find is None:
            continue
        spec = find("runtime.moy_image", [str(tmp_path)])
        assert spec is None, (finder, spec and spec.origin)


def test_the_conftest_installs_the_staged_copy_refusal_before_the_path():
    kinds = [type(f) if not isinstance(f, type) else f for f in sys.meta_path]
    guard = next(i for i, f in enumerate(sys.meta_path)
                 if isinstance(f, import_guard.StagedCopyRefusal))
    assert guard < kinds.index(importlib.machinery.PathFinder)
    assert import_guard.foreign_editable_finders(ROOT) == []


def _editable(name, mapping):
    mod = types.ModuleType(name)
    mod.MAPPING = mapping
    finder = type("_EditableFinder", (), {"__module__": name})
    return mod, finder


def test_an_editable_finder_mapping_outside_the_checkout_is_dropped(
        monkeypatch, tmp_path):
    away, away_f = _editable("__editable___probe_away_finder",
                             {"runtime": str(tmp_path / "elsewhere")})
    home, home_f = _editable("__editable___probe_home_finder",
                             {"runtime": os.path.join(ROOT, "runtime")})
    monkeypatch.setitem(sys.modules, away.__name__, away)
    monkeypatch.setitem(sys.modules, home.__name__, home)
    meta = [home_f, away_f]
    assert import_guard.drop_foreign_editable_finders(ROOT, meta) == [away_f]
    assert meta == [home_f]


def test_every_board_staging_tree_is_guarded():
    dirs = import_guard.staging_dirs(ROOT)
    fw = os.path.join(ROOT, "firmware")
    assert os.path.realpath(
        os.path.join(fw, "lilygo_t_deck_plus_mainline", "modules")) in dirs
    assert os.path.realpath(
        os.path.join(fw, "web_runner", ".build", "stage", "modules")) in dirs
    assert "_ota_build.py" in dirs[os.path.realpath(
        os.path.join(fw, "lilygo_t_deck_plus_mainline", "modules"))]


@pytest.fixture
def staged_tree(tmp_path, monkeypatch):
    """A git-tracked staging tree on sys.path: one authored file, one kept
    build product, one stale copy of a source that no longer exists."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in ("guard_probe_authored", "guard_probe_kept",
                 "guard_probe_stale"):
        (tmp_path / (name + ".py")).write_text("X = 1\n")
    subprocess.run(["git", "add", "guard_probe_authored.py"], cwd=tmp_path,
                   check=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    return import_guard.StagedCopyRefusal(
        {os.path.realpath(str(tmp_path)): {"guard_probe_kept.py"}})


def test_a_stale_staged_copy_is_refused(staged_tree):
    with pytest.raises(ImportError, match="build-staged copy"):
        staged_tree.find_spec("guard_probe_stale")
    assert staged_tree.find_spec("guard_probe_authored") is None
    assert staged_tree.find_spec("guard_probe_kept") is None
    assert staged_tree.find_spec("guard_probe_absent") is None


def test_a_staging_tree_off_the_path_is_not_consulted(staged_tree,
                                                      monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path
                                      if "guard_probe" not in p and
                                      not os.path.isfile(os.path.join(
                                          p, "guard_probe_stale.py"))])
    assert staged_tree.find_spec("guard_probe_stale") is None


def test_the_installed_refusal_reaches_a_bare_import(staged_tree,
                                                     monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [staged_tree] + sys.meta_path)
    monkeypatch.delitem(sys.modules, "guard_probe_stale", raising=False)
    with pytest.raises(ImportError, match="build-staged copy"):
        import guard_probe_stale  # noqa: F401
