"""The store index's native twins beyond the interface suite: the part of
sprint 1a's harness (tools/moy_index_spike.py) CI runs.

  * tests/test_moy_index.py itself, run on the boards' VM -- the desktop
    MicroPython, both object models -- over the native module, under a
    stand-in for the little of pytest it uses;
  * a seeded random walk of the API against the Python twin, every answer and
    every exception compared, for each native binding on the host;
  * the API-sequence fuzz (native/moy_index/fuzz_index.c) under
    AddressSanitizer and UndefinedBehaviorSanitizer: #224's containment.

The store trace over the native index is tests/test_semantic_traces.py's.
"""

import os
import random
import subprocess

import pytest

from runtime import moy_index
from tools import moy_index_spike
from unix_mp import find_unix_mp, require_unix_mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NATIVE = moy_index_spike.host_bindings()

# The bits of pytest tests/test_moy_index.py uses, for a VM that has none.
PYTEST_STANDIN = '''
_PARAMS = {}


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, t, v, tb):
        if t is None:
            raise AssertionError("did not raise %r" % (self.exc,))
        if not issubclass(t, self.exc):
            return False
        self.value = v
        return True


def raises(exc):
    return _Raises(exc)


def fixture(*a, **k):
    return lambda fn: fn


class _Mark:
    def parametrize(self, name, values):
        def deco(fn):
            _PARAMS[fn] = (name, values)
            return fn
        return deco


mark = _Mark()
'''

VM_RUNNER = '''
import sys
sys.path[:] = [@DIR@]
import moy_index
assert @NATIVE@ == (not hasattr(moy_index, "__file__")), moy_index
import pytest
ns = {"__name__": "test_moy_index"}
exec(open(@TEST@).read(), ns)
passed = failed = 0
for name in sorted(ns):
    fn = ns[name]
    if not name.startswith("test_"):
        continue
    cases = [{}]
    if fn in pytest._PARAMS:
        pname, values = pytest._PARAMS[fn]
        cases = [{pname: v} for v in values]
    for kw in cases:
        try:
            fn(moy_index.Index, **kw)
            passed += 1
        except Exception as e:
            failed += 1
            print("FAIL", name, kw, repr(e))
print("RESULT", passed, failed)
'''


def _vm_suite(exe, tmp_path, native):
    """tests/test_moy_index.py on `exe`: (passed, failed, output). The
    `runtime` package's moy_index re-exports the top-level one: the builtin
    when no moy_index.py is on the path (native), else the Python twin."""
    d = tmp_path / ("vm_%s" % ("native" if native else "python"))
    (d / "runtime").mkdir(parents=True)
    (d / "pytest.py").write_text(PYTEST_STANDIN)
    (d / "runtime" / "__init__.py").write_text("")
    (d / "runtime" / "moy_index.py").write_text("from moy_index import *\n")
    if not native:
        with open(os.path.join(ROOT, "runtime", "moy_index.py")) as f:
            (d / "moy_index.py").write_text(f.read())
    script = d / "run.py"
    script.write_text(VM_RUNNER.replace("@DIR@", repr(str(d)))
                      .replace("@NATIVE@", repr(native))
                      .replace("@TEST@", repr(os.path.join(HERE, "test_moy_index.py"))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=300)
    text = out.stdout + out.stderr
    last = [ln for ln in text.splitlines() if ln.startswith("RESULT ")]
    assert out.returncode == 0 and last, text
    _, passed, failed = last[-1].split()
    return int(passed), int(failed), text


@pytest.mark.parametrize("native", [True, False], ids=["native", "python"])
def test_the_suite_passes_on_the_vm(tmp_path, native):
    exe = require_unix_mp(
        "moy_index",
        why="tests/test_moy_index.py on the VM a board runs, over the native "
            "store index (sprint 1a's twin).")
    passed, failed, text = _vm_suite(exe, tmp_path, native)
    assert failed == 0 and passed >= 16, text
    board = find_unix_mp("moy_index", board_model=True)
    if board is not None:
        passed, failed, text = _vm_suite(board, tmp_path / "r32", native)
        assert failed == 0 and passed >= 16, text


def _outcome(fn, *a):
    try:
        return ("ok", fn(*a))
    except moy_index.StaleHandle:
        return ("stale",)
    except TypeError:
        return ("type",)
    except OSError as e:
        return ("oserror", e.args[0])


@pytest.mark.parametrize("name", sorted(NATIVE))
def test_a_random_walk_agrees_with_the_python_twin(name):
    """Thousands of calls in a seeded order, with paths that repeat, handles
    live, released and forged, and the table filled to the brim and drained:
    the native twin answers each exactly as runtime/moy_index.py does."""
    rnd = random.Random(1)
    ref, twin = moy_index.Index(), NATIVE[name]()
    paths = ["/carts/%d.moy" % i for i in range(5000)] + ["", "/é/ü.moy",
                                                           "/a\x00b"]
    seen = [0, -1, 1 << 40, None, "7", 2.0]
    for step in range(30000):
        k = rnd.random()
        if step % 6000 < 1500 and k < 0.6:     # bursts that fill the table
            p = paths[rnd.randrange(len(paths))]
            got = (_outcome(ref.intern, p), _outcome(twin.intern, p))
        elif k < 0.35:
            p = paths[rnd.randrange(300)]
            got = (_outcome(ref.intern, p), _outcome(twin.intern, p))
        elif k < 0.5:
            p = paths[rnd.randrange(400)]
            got = (_outcome(ref.find, p), _outcome(twin.find, p))
        else:
            hs = ref.handles()
            h = (rnd.choice(hs) if hs and rnd.random() < 0.6
                 else rnd.choice(seen))
            op = rnd.choice(("path", "valid", "release"))
            got = (_outcome(getattr(ref, op), h), _outcome(getattr(twin, op), h))
            if op == "release" and got[0] == ("ok", None):
                seen.append(h)
        assert got[0] == got[1], (step, got)
        if got[0][0] == "ok" and isinstance(got[0][1], int):
            seen.append(got[0][1])
        if step % 997 == 0:
            assert ref.handles() == twin.handles()
            assert ref.count() == twin.count()
    assert ref.handles() == twin.handles()


def test_the_api_fuzz_runs_clean_under_the_sanitizers():
    """fuzz_index's seeded programs over the C twin, built with ASan and
    UBSan: every answer checked against its model, every byte returned."""
    got = moy_index_spike.fuzz_seeded("c", seed=1, runs=300)
    if got is None:
        if os.environ.get("CI"):
            pytest.fail("no C compiler with AddressSanitizer here")
        pytest.skip("no C compiler with AddressSanitizer here")
    ok, secs, out = got
    assert ok, out[-4000:]
    assert "300 programs" in out, out
