"""The strike ledger's C twin (native/moy_spine/moy_ledger.c) against the
Python one (runtime/crash_guard.py), on the same session.

One seeded driver runs under CPython, where `CrashGuard` is the Python class,
and under the desktop MicroPython with the native spine, where `crash_guard`
swaps the native class in. It makes thousands of calls over slots of every
shape the Python ledger tolerates (absent, not an object, a strikes value that is
not a dict, counts that are strings or floats, members it does not own) and
logs every answer and the store's whole text after each: the two logs must be
equal line for line, so the C twin writes the bytes the Python one does.
"""

import subprocess
import sys

from tests import unix_mp  # noqa: F401  (the helpers' home; imported for its path)
from tests.unix_mp import require_unix_mp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)
import moy_spine as sp
from crash_guard import CrashGuard, KEY, WALLPAPER_KEY

mirrors = []
try:
    import crash_guard
    if hasattr(sp, "set_mirror"):
        sp.set_mirror(lambda role, cid: mirrors.append((role, cid)))
    else:
        crash_guard._native = type("N", (), {"arm": staticmethod(
            lambda role, cid: mirrors.append((role, cid)))})
except Exception as e:
    print("T hook", e)

seed = 12345
def rnd(n):
    global seed
    seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
    return (seed >> 8) % n

SLOTS = (None, "[1]", '{"strikes": 5}', '{"strikes": {"a": "2", "b": 2.7, "c": true, "d": null}}',
         '{"x": 1, "strikes": {"a": 1}, "open": "a", "proven": {"a": "p1"}, "y": [1, 2]}',
         '{"open": "zz"}', '{"proven": 3, "strikes": {}}', '{"strikes": {"b\\u00e9": 2}}')
IDS = ("a", "b", "c", "d", "calc", "sp ace", 'q"uote', "back\\slash", "1", "")
PROOFS = (None, "p1", "p2", "%x-%08x" % (7, 99), 5)

def show(x):
    if isinstance(x, str):
        return "s:" + x.encode().hex()
    if isinstance(x, (list, tuple)):
        return "(" + ",".join(show(i) for i in x) + ")"
    return repr(x)


def run(round_no):
    saves = []
    store = sp.Settings(lambda text: saves.append(text) or True)
    slot = SLOTS[round_no % len(SLOTS)]
    if slot is not None:
        store.set_text(KEY, slot)
        store.set_text(WALLPAPER_KEY, slot)
    guards = (CrashGuard(store), CrashGuard(store, key=WALLPAPER_KEY))
    for step in range(60):
        g = guards[rnd(2)]
        cid = IDS[rnd(len(IDS))]
        op = rnd(9)
        try:
            if op == 0:
                r = g.arm(cid)
            elif op == 1:
                r = g.arm(cid, PROOFS[rnd(len(PROOFS))])
            elif op == 2:
                r = g.frame()
            elif op == 3:
                r = g.heal()
            elif op == 4:
                r = g.release()
            elif op == 5:
                r = g.forgive(cid)
            elif op == 6:
                r = (g.strikes(cid), g.disabled(cid))
            elif op == 7:
                r = (g.last_open(), g.broken_ids())
            else:
                r = g.arm(cid, "p1")
        except Exception as e:
            r = "%s" % type(e).__name__
        print("T", round_no, step, op, show(cid), show(r), len(saves), len(mirrors), show(mirrors[-1:]))
        print("T", store.dump())
for k in range(24):
    run(k)
print("DRIVER_DONE")
'''


def _run(exe, tmp_path, tag, prelude=""):
    script = tmp_path / ("ledger_%s.py" % tag)
    script.write_text(prelude + DRIVER.replace("@RUNTIME@", repr(str(ROOT / "runtime"))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr or out.stdout
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1] == "DRIVER_DONE", out.stdout[-2000:]
    return lines


NATIVE = r'''import sys
_path = sys.path[:]
sys.path[:] = []
import moy_spine
sys.path[:] = _path
assert not hasattr(moy_spine, "__file__")
sys.modules["moy_spine"] = moy_spine
'''


def test_the_native_ledger_writes_what_the_python_one_does(tmp_path):
    exe = require_unix_mp(
        "moy_spine",
        why="The native strike ledger against the Python one: the same answers "
            "and the same bytes in the settings store, call for call.")
    want = _run(sys.executable, tmp_path, "cpython")
    got = _run(exe, tmp_path, "native", NATIVE)
    assert len(want) == len(got)
    for i, (a, b) in enumerate(zip(want, got)):
        assert a == b, "line %d:\n python %s\n native %s" % (i, a, b)
