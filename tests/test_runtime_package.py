"""`import runtime` runs the package's own __init__ in a fresh interpreter.

`make setup` runs `python -m runtime.native_build` and `runtime.audio_binding`,
which execute `runtime/__init__.py`. The suite's conftest reaches modules
without running that `__init__`, so a stale eager import in it (the deleted
`editors` umbrella, 2026-11-01) passed every test and preflight and failed CI
at `make setup`. A fresh interpreter, started in the checkout under test, is
the only lane that sees it.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_runtime_package_imports_in_a_fresh_interpreter():
    # cwd first on sys.path, so a worktree imports its own runtime/; and the
    # shared venv's editable finder dropped, or a submodule this checkout
    # deleted would import from the main checkout's runtime/ (import_guard).
    code = ("import sys; sys.path.insert(0, 'tests'); import import_guard; "
            "sys.path.pop(0); import_guard.drop_foreign_editable_finders('.'); "
            "import runtime, os; print(os.path.dirname(runtime.__file__))")
    r = subprocess.run([sys.executable, "-c", code],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert os.path.realpath(r.stdout.strip()) == \
        os.path.realpath(os.path.join(ROOT, "runtime"))
