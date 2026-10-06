"""Processes that ask for one host library at the same moment build it once.

pytest-xdist's workers all bind their native twins while they collect, and on a
cold cache N compilers wrote one output path and the later renames found their
file gone (FileNotFoundError at collection). runtime/native_build serialises the
build on a lock beside the library: this starts several processes together on an
empty cache and wants every one to get the same finished library.
"""

import os
import subprocess
import sys

import pytest

from runtime import native_build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = '''
import sys
sys.path.insert(0, %(root)r)
from runtime import native_build
print(native_build.build("race", %(shim)r, [], %(cache)r, cflags=["-std=c99", "-O1", "-fPIC", "-shared"]))
'''


def test_concurrent_builds_of_one_library_share_one_result(tmp_path):
    if native_build.cc() is None:
        pytest.skip("no C compiler here")
    shim = tmp_path / "race.c"
    # enough code that the compile is not instantaneous
    shim.write_text("int race(int x) { return x; }\n" + "".join(
        "int f%d(int x) { return x * %d + f%d(x - 1); }\n" % (i, i, i - 1)
        if i else "int f0(int x) { return x; }\n" for i in range(1500)))
    cache = tmp_path / "cache"
    code = CHILD % {"root": ROOT, "shim": str(shim), "cache": str(cache)}
    procs = [subprocess.Popen([sys.executable, "-c", code],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(8)]
    outs = [p.communicate() for p in procs]
    for p, (out, err) in zip(procs, outs):
        assert p.returncode == 0, err
    paths = {out.strip() for out, _ in outs}
    assert len(paths) == 1 and os.path.exists(paths.pop())
    assert [f for f in os.listdir(cache) if f.endswith(".tmp")] == []
