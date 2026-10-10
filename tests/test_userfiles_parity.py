"""The user-files layer (native/moy_store/moy_ufiles.h) against the Python it
replaced, byte for byte.

tests/userfiles_workload.py drives one seeded sequence of the layer's verbs
and digests every answer and every byte the store holds after each one. The
digests in tests/fixtures/userfiles_golden.json were made from the Python
layer (runtime/moy_files.py and runtime/moy_file_ops.py) before it was
deleted; each binding of the C must reproduce them:

  * the C over the host's ctypes binding (tools/moy_ufiles_binding.py), the one
    the CPython console runs;
  * the C as the boards' module `moy_ufiles`, on the desktop MicroPython in
    the boards' object model (32-bit, REPR_C), each verb called over a pipe so
    the workload's own Python (its random walk, its digests) runs once.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import userfiles_workload as W               # noqa: E402
from unix_mp import require_unix_mp           # noqa: E402

TS = 1234567      # the journal's clock, fixed as the golden's was
GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "userfiles_golden.json").read_text())["log"]


def _check(log):
    assert len(log) == len(GOLDEN)
    for i, (got, want) in enumerate(zip(log, GOLDEN)):
        assert list(got) == list(want), "step %d (%s) differs" % (i, want[0])


def test_the_host_binding_writes_the_python_layers_bytes(tmp_path):
    import moy_ufiles_binding
    uf = moy_ufiles_binding.binding()
    uf.clock_fix(TS)
    try:
        _check(W.run(W.python_api(uf), str(tmp_path / "sd" / "moybyte" / "carts")))
    finally:
        uf.clock_fix(-1)


# The desktop MicroPython's side of the pipe: one verb a line, its answer a line.
_SERVER = r'''
import binascii, json, sys
import moy_ufiles as m
m.clock_fix(%d)

def enc(v):
    if isinstance(v, (bytes, bytearray)):
        return {"bytes": binascii.hexlify(v).decode()}
    if isinstance(v, tuple):
        return {"tuple": [enc(x) for x in v]}
    if isinstance(v, list):
        return [enc(x) for x in v]
    if isinstance(v, dict):
        return {k: enc(x) for k, x in v.items()}
    return v

def dec(v):
    if isinstance(v, dict) and "bytes" in v:
        return binascii.unhexlify(v["bytes"])
    if isinstance(v, list):
        return [dec(x) for x in v]
    if isinstance(v, dict):
        return {k: dec(x) for k, x in v.items()}
    return v

while True:
    line = sys.stdin.readline()
    if not line:
        break
    fn, args, kw = json.loads(line)
    try:
        out = ["ok", enc(getattr(m, fn)(*dec(args), **dec(kw)))]
    except ValueError as e:
        out = ["ValueError", str(e)]
    except OSError as e:
        out = ["OSError", e.args[0] if e.args else 0]
    sys.stdout.write(json.dumps(out) + "\n")
''' % TS


class _Pipe:
    """The module on the far side of a pipe, verb by verb."""

    def __init__(self, exe):
        self.p = subprocess.Popen([exe, "-c", _SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)

    def _enc(self, v):
        if isinstance(v, (bytes, bytearray)):
            return {"bytes": bytes(v).hex()}
        if isinstance(v, (list, tuple)):
            return [self._enc(x) for x in v]
        if isinstance(v, dict):
            return {k: self._enc(x) for k, x in v.items()}
        return v

    def _dec(self, v):
        if isinstance(v, dict) and "bytes" in v:
            return bytes.fromhex(v["bytes"])
        if isinstance(v, dict) and "tuple" in v:
            return tuple(self._dec(x) for x in v["tuple"])
        if isinstance(v, list):
            return [self._dec(x) for x in v]
        if isinstance(v, dict):
            return {k: self._dec(x) for k, x in v.items()}
        return v

    def __getattr__(self, fn):
        def call(*args, **kw):
            self.p.stdin.write(json.dumps([fn, self._enc(args), self._enc(kw)]) + "\n")
            self.p.stdin.flush()
            how, val = json.loads(self.p.stdout.readline())
            if how == "ValueError":
                raise ValueError(val)
            if how == "OSError":
                raise OSError(val, "")
            return self._dec(val)
        return call

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


@pytest.mark.parametrize("board_model", (False, True))
def test_the_boards_module_writes_the_python_layers_bytes(tmp_path, board_model):
    exe = require_unix_mp("moy_ufiles", board_model=board_model)
    pipe = _Pipe(exe)
    try:
        _check(W.run(W.python_api(pipe), str(tmp_path / "sd" / "moybyte" / "carts")))
    finally:
        pipe.close()


def test_the_palette_is_the_canvas_palette():
    import moy_ufiles_binding
    from device import device_canvas
    assert moy_ufiles_binding.binding().palette() == bytes(device_canvas.MOY64_RGB)


def test_the_sync_reads_the_layers_kinds():
    """The sync names the files root and its kinds from the on-card layout
    (runtime/moy_store_base.py), which the Zero carries without the layer:
    they are the layer's own."""
    import moy_ufiles_binding
    from runtime import moy_store_base
    uf = moy_ufiles_binding.binding()
    assert tuple(uf.kinds()) == moy_store_base.FILE_KIND_NAMES
    for root in ("/sd/moybyte/carts", "carts", "/carts", "/a/b/c/"):
        assert moy_store_base.files_root(root) == uf.files_root(root), root
