"""The suite imports what THIS checkout holds, or fails the way CI fails.

Two doors let a module the checkout no longer has import anyway on a dev
machine, while CI, with a fresh clone and its own venv, fails at once. The
deleted `runtime/editors.py` and `runtime/palette.py` (b00c13519) went through
both: every local suite and preflight passed, CI failed at `make setup`.

1. The shared venv's editable install. Worktrees link the main checkout's
   `.venv`, whose setuptools finder maps the `runtime` package to the MAIN
   checkout's `runtime/`. It sits on `sys.meta_path` and ignores the `path`
   it is handed, so `runtime.palette`, missing from this checkout's
   `runtime/`, resolves to the main checkout's file. `drop_foreign_editable_
   finders` removes every editable finder that maps a package outside this
   checkout; this checkout's root is first on `sys.path`, so nothing else
   changes.

2. Build-staged copies. A board build copies runtime/ and device/ modules
   into the board's gitignored `modules/` tree, and suites put that tree on
   `sys.path` for its authored device files. A source deleted since the last
   build keeps its staged copy there until the next build prunes it, so a bare
   `import palette` finds yesterday's file. `StagedCopyRefusal` turns a bare
   import that would land on an untracked file in a staging tree, one the
   board file does not `keep`, into an ImportError naming the copy. Current
   runtime/ and device/ names never reach it: the conftest resolves those
   from their sources first.
"""

import importlib.abc
import importlib.machinery
import os
import subprocess
import sys


def _under(path, root):
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def foreign_editable_finders(root, meta_path=None):
    """The setuptools editable finders on `meta_path` that map any package
    to a directory outside `root`."""
    root = os.path.realpath(root)
    meta_path = sys.meta_path if meta_path is None else meta_path
    out = []
    for finder in meta_path:
        modname = getattr(finder, "__module__", "") or ""
        if not modname.startswith("__editable__"):
            continue
        mapping = getattr(sys.modules.get(modname), "MAPPING", None) or {}
        if any(not _under(p, root) for p in mapping.values()):
            out.append(finder)
    return out


def drop_foreign_editable_finders(root, meta_path=None):
    """Remove `foreign_editable_finders` from `meta_path`; return them."""
    meta_path = sys.meta_path if meta_path is None else meta_path
    gone = foreign_editable_finders(root, meta_path)
    for finder in gone:
        meta_path.remove(finder)
    return gone


def staging_dirs(root):
    """{staging dir: names the board file `keep`s there} for every board."""
    sys.path.insert(0, os.path.join(root, "tools"))
    try:
        import board_config
    finally:
        sys.path.pop(0)
    out = {}
    fw = os.path.join(root, "firmware")
    for name in sorted(os.listdir(fw)):
        board = os.path.join(fw, name)
        if not os.path.isfile(os.path.join(board, "board.toml")):
            continue
        mods = board_config.load(board).get("modules", {})
        dest = os.path.realpath(os.path.join(board, mods.get("dest", "modules")))
        out[dest] = set(mods.get("keep", []))
    return out


def _tracked(directory):
    """Basenames git tracks directly in `directory`; None without git."""
    try:
        out = subprocess.run(["git", "ls-files", "--", "."], cwd=directory,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line for line in out.split() if "/" not in line}


class StagedCopyRefusal(importlib.abc.MetaPathFinder):
    """Refuse a bare import that resolves to a stale build-staged copy."""

    def __init__(self, dirs):
        self._dirs = dict(dirs)          # realpath -> kept names
        self._authored = {}              # realpath -> tracked | kept names
        self._seen_path = None
        self._on_path = False

    def _staging_on_path(self):
        key = tuple(sys.path)
        if key != self._seen_path:
            self._seen_path = key
            self._on_path = any(os.path.realpath(p or ".") in self._dirs
                                for p in key if isinstance(p, str))
        return self._on_path

    def _allowed(self, directory):
        if directory not in self._authored:
            tracked = _tracked(directory) if os.path.isdir(directory) else set()
            self._authored[directory] = (
                None if tracked is None else tracked | self._dirs[directory])
        return self._authored[directory]

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or not self._staging_on_path():
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname)
        origin = getattr(spec, "origin", None)
        if not origin or not os.path.isfile(origin):
            return None
        where = os.path.realpath(os.path.dirname(origin))
        if where not in self._dirs:
            return None
        allowed = self._allowed(where)
        if allowed is None or os.path.basename(origin) in allowed:
            return None
        raise ImportError(
            "%s: only a build-staged copy exists (%s); no runtime/ or device/ "
            "source by that name, so CI, which has no staged tree, cannot "
            "import it. A fresh board build prunes the copy."
            % (fullname, origin), name=fullname, path=origin)
