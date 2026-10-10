"""A test file run on the boards' VM: the desktop MicroPython, over a native
module or the Python twin, in either object model.

MicroPython has no pytest, so the file is exec'd under a stand-in for the bits
of pytest the interface suites use (raises with a match, fixture, parametrize)
and each test is called with the module under test. tests/test_moy_index_twins.py
and tests/test_moy_spine_twins.py share it.
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The bits of pytest the interface suites use, for a VM that has none.
PYTEST_STANDIN = '''
_PARAMS = {}


class _Raises:
    def __init__(self, exc, match=None):
        self.exc = exc
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, t, v, tb):
        if t is None:
            raise AssertionError("did not raise %r" % (self.exc,))
        if not issubclass(t, self.exc):
            return False
        if self.match is not None and self.match not in str(v):
            raise AssertionError("%r does not say %r" % (str(v), self.match))
        self.value = v
        return True


def raises(exc, match=None):
    return _Raises(exc, match)


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

# @ARG@ is what each test is called with: the module, or the class under test.
VM_RUNNER = '''
import sys
sys.path[:] = [@DIR@]
import @MOD@
assert @NATIVE@ == (not hasattr(@MOD@, "__file__")), @MOD@
import pytest
ns = {"__name__": "test_@MOD@"}
exec(open(@TEST@).read(), ns)
skip = @SKIP@
passed = failed = 0
for name in sorted(ns):
    fn = ns[name]
    if not name.startswith("test_") or name in skip:
        continue
    cases = [{}]
    if fn in pytest._PARAMS:
        pname, values = pytest._PARAMS[fn]
        cases = [{pname: v} for v in values]
    for kw in cases:
        try:
            fn(@ARG@, **kw)
            passed += 1
        except Exception as e:
            failed += 1
            print("FAIL", name, kw, repr(e))
print("RESULT", passed, failed)
'''


# A module whose Python twin is a suite's oracle rather than a runtime file.
TWINS = {"moy_spine": os.path.join(HERE, "spine_twin.py")}


def run(exe, tmp_path, module, test_file, native, arg, skip=(), extra=()):
    """`test_file` on `exe`, over module `module` (a top-level name): (passed,
    failed, output). The `runtime` package's `module` re-exports the top-level
    one, which is the builtin when no `module`.py is on the path (native), else
    the Python twin. `extra` are runtime/ modules the test file imports, put on
    the path as they are."""
    d = tmp_path / ("vm_%s_%s" % (module, "native" if native else "python"))
    (d / "runtime").mkdir(parents=True)
    (d / "pytest.py").write_text(PYTEST_STANDIN)
    (d / "runtime" / "__init__.py").write_text("")
    (d / "runtime" / (module + ".py")).write_text("from %s import *\n" % module)
    for name in extra:
        with open(os.path.join(ROOT, "runtime", name + ".py")) as f:
            (d / (name + ".py")).write_text(f.read())
        (d / "runtime" / (name + ".py")).write_text("from %s import *\n" % name)
    if not native:
        with open(TWINS.get(module) or os.path.join(ROOT, "runtime", module + ".py")) as f:
            (d / (module + ".py")).write_text(f.read())
    script = d / "run.py"
    script.write_text(VM_RUNNER.replace("@DIR@", repr(str(d)))
                      .replace("@MOD@", module)
                      .replace("@NATIVE@", repr(native))
                      .replace("@TEST@", repr(os.path.join(HERE, test_file)))
                      .replace("@SKIP@", repr(set(skip)))
                      .replace("@ARG@", arg))
    env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
    out = subprocess.run([exe, str(script)], capture_output=True, text=True,
                         timeout=300, env=env)
    text = out.stdout + out.stderr
    last = [ln for ln in text.splitlines() if ln.startswith("RESULT ")]
    assert out.returncode == 0 and last, text
    _, passed, failed = last[-1].split()
    return int(passed), int(failed), text
