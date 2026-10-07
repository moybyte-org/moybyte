"""The store's crash-safe write in C (native/moy_store, docs/kernel_store_2026-10.md
section 5), held to runtime/moy_fs.py, which CPython keeps as its twin.

  * the power-cut matrix: every scripted write sequence on oofatfs over a RAM
    card and littlefs2 over a RAM flash, cut at every block write, reads back
    as the version before or the version written, whole -- and the seeded
    random sequences with a random cut -- under ASan and UBSan;
  * the C store over POSIX through ctypes answers as the twin does, call for
    call, and leaves no scratch behind;
  * the desktop MicroPython, whose moy_fs delegates to the native module, and
    CPython over the twin play one session on a real folder and print the same
    lines.

tests/test_store_on_vfs.py runs the store on FAT and littlefs volumes under
the desktop MicroPython, where every write is the C store's.
"""

import ctypes
import os
import re
import subprocess
import sys

import pytest

from runtime import moy_fs
from tools import moy_index_spike as spike
from unix_mp import require_unix_mp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _no_toolchain(what):
    if os.environ.get("CI"):
        pytest.fail(what)
    pytest.skip(what)


def test_the_power_cut_matrix_holds_on_fat_and_littlefs():
    got = spike.fuzz_matrix(spike.FS)
    if got is None:
        _no_toolchain("no sanitizer toolchain or no MicroPython tree "
                      "(make unix-micropython)")
    ok, secs, out = got
    assert ok, out[-4000:]
    assert "cuts, ok" in out, out
    # The FAT medium sits behind the card's read cache with every hit compared
    # with the card: the matrix is the cache's too, so it must have been hit.
    hits = re.search(r"card cache (\d+) hits checked", out)
    assert hits and int(hits.group(1)) > 0, out


def test_the_seeded_fuzz_runs_clean_under_the_sanitizers():
    if not os.path.isdir(os.path.join(spike.MPY, "lib", "oofatfs")):
        _no_toolchain("no MicroPython tree (make unix-micropython)")
    got = spike.fuzz_seeded("c", seed=1, runs=300, comp=spike.FS)
    if got is None:
        _no_toolchain("no C compiler with AddressSanitizer here")
    ok, secs, out = got
    assert ok, out[-4000:]
    assert "300 programs" in out, out


# -- the C store over POSIX, through ctypes ----------------------------------------


class _Buf(ctypes.Structure):
    _fields_ = [("p", ctypes.c_void_p), ("n", ctypes.c_size_t)]


def _lib():
    san = os.environ.get("MOY_FS_SANITIZE") == "1"
    path = spike.host_library("c", sanitize=san, comp=spike.FS)
    if path is None:
        _no_toolchain("no C compiler")
    lib = ctypes.CDLL(path)
    c, u32, sz = ctypes.c_char_p, ctypes.c_uint32, ctypes.c_size_t
    for name, args, res in (
            ("moy_fs_root", [c], ctypes.c_int),
            ("moy_fs_roots_clear", [], None),
            ("moy_fs_publish", [c, c, sz], ctypes.c_int),
            ("moy_fs_read", [c, c, ctypes.POINTER(_Buf)], ctypes.c_int),
            ("moy_fs_read_stamped", [c, ctypes.POINTER(_Buf)], ctypes.c_int),
            ("moy_fs_read_file", [c, sz, ctypes.POINTER(_Buf)], ctypes.c_int),
            ("moy_fs_write_bytes", [c, c, sz], ctypes.c_int),
            ("moy_fs_claim", [c, c, u32, u32], ctypes.c_int),
            ("moy_buf_free", [ctypes.POINTER(_Buf)], None),
            ("moy_store_host_live", [], ctypes.c_long)):
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    return lib


class _CStore:
    """runtime/moy_fs.py's calls over the C store, as the twin answers them."""

    def __init__(self, lib):
        self.lib = lib

    def _take(self, rc, b):
        if rc == -1:
            return None
        if rc:
            raise OSError(rc, os.strerror(rc))
        try:
            return ctypes.string_at(b.p, b.n).decode("utf-8")
        finally:
            self.lib.moy_buf_free(ctypes.byref(b))

    def set_publish_root(self, root):
        assert self.lib.moy_fs_root(root.encode()) == 0

    def publish(self, path, text):
        data = text.encode()
        rc = self.lib.moy_fs_publish(path.encode(), data, len(data))
        if rc:
            raise OSError(rc, os.strerror(rc))

    def read_recover(self, path):
        b = _Buf()
        return self._take(self.lib.moy_fs_read(path.encode(), None,
                                               ctypes.byref(b)), b)

    def read_stamped(self, path):
        b = _Buf()
        return self._take(self.lib.moy_fs_read_stamped(path.encode(),
                                                       ctypes.byref(b)), b)

    def claim(self, path, dest, stamp):
        return bool(self.lib.moy_fs_claim(path.encode(), dest.encode(), *stamp))


class _PyStore:
    def set_publish_root(self, root):
        moy_fs.set_publish_root(root)

    def publish(self, path, text):
        moy_fs._write_atomic(path, text)

    def read_recover(self, path):
        return moy_fs._read_recover(path)

    def read_stamped(self, path):
        return moy_fs._read_stamped(path)

    def claim(self, path, dest, stamp):
        return moy_fs._claim_bak(path, dest, stamp)


def _session(store, d):
    """One run of the store's cases in folder `d`: what each answered."""
    out = []
    root = os.path.join(d, "carts")
    cart = os.path.join(root, "a.moy")
    os.makedirs(os.path.join(cart, "journal"))
    store.set_publish_root(root)
    a = os.path.join(cart, "main.py")
    v1, v2 = "print('é')\n" * 40, "draw()\n" * 90
    store.publish(a, v1)
    out.append(("published", store.read_recover(a) == v1))
    store.publish(a, v2)
    with open(a, "w") as f:                 # the publish torn: a prefix
        f.write(v2[:100])
    out.append(("torn prefix", store.read_recover(a) == v2))
    with open(a) as f:
        out.append(("healed", f.read() == v2))
    with open(a, "w") as f:                 # someone else wrote it
        f.write("x" * len(v2))
    out.append(("foreign", store.read_recover(a) == "x" * len(v2),
                os.path.exists(a + ".bak")))
    store.publish(a, v1)
    dest = os.path.join(cart, "journal", "1")
    out.append(("claim", store.claim(a, dest, moy_fs._stamp_of(v1)),
                store.read_stamped(dest) == v1, os.path.exists(a + ".bak")))
    out.append(("claim wrong stamp", store.claim(a, dest + "x", (1, 2))))
    legacy = os.path.join(cart, "legacy")
    with open(legacy, "w") as f:
        f.write("old text\n")
    out.append(("legacy", store.read_stamped(legacy)))
    with open(legacy, "w"):
        pass
    out.append(("empty", store.read_stamped(legacy)))
    with open(legacy, "w") as f:
        f.write("#moyfs1 5 1\nhello")
    out.append(("torn bak", store.read_stamped(legacy)))
    gone = os.path.join(cart, "gone.py")
    store.publish(gone, v1)
    os.remove(gone)
    out.append(("missing", store.read_recover(gone) == v1, os.path.exists(gone)))
    try:
        store.read_recover(os.path.join(cart, "never.py"))
        out.append(("never", "read"))
    except OSError as e:
        out.append(("never", e.args[0]))
    return out


def test_the_c_store_answers_as_the_twin_on_posix(tmp_path):
    lib = _lib()
    c = _session(_CStore(lib), str(tmp_path / "c"))
    lib.moy_fs_roots_clear()
    assert lib.moy_store_host_live() == 0
    p = _session(_PyStore(), str(tmp_path / "py"))
    assert c == p


# -- the desktop MicroPython over the native module ----------------------------------

VM_SESSION = r'''
import os, sys
sys.path.insert(0, @RUNTIME@)
import moy_fs
D = @DIR@
native = getattr(moy_fs, "_native", None) is not None
print("native", native)
root = D + "/carts"
cart = root + "/a.moy"
for p in (root, cart, cart + "/journal"):
    moy_fs._mkdir(p)
moy_fs.set_publish_root(root)
a = cart + "/main.py"
v1, v2 = "print('é')\n" * 40, "draw()\n" * 90
moy_fs._write_atomic(a, v1)
print("published", moy_fs._read_recover(a) == v1, moy_fs._bak_stamp(a))
moy_fs._write_atomic(a, v2)
with open(a, "w") as f:
    f.write(v2[:100])
print("torn", moy_fs._read_recover(a) == v2, moy_fs._read(a) == v2)
os.chdir(cart)
print("at", moy_fs._read_recover(a, "main.py") == v2)
os.chdir(D)
dest = cart + "/journal/1"
print("claim", moy_fs._claim_bak(a, dest, moy_fs._stamp_of(v2)),
      moy_fs._read_stamped(dest) == v2, moy_fs._exists(a + ".bak"))
b = cart + "/sprites.moygfx"
moy_fs._write_bytes(b, b"\x00\x01" * 300)
print("bytes", moy_fs._read_bytes(b, 600) == b"\x00\x01" * 300,
      moy_fs._read_bytes(b, 599))
moy_fs._remove(b)
print("removed", moy_fs._exists(b))
try:
    moy_fs._read(cart + "/never")
except OSError as e:
    print("never", e.args[0])
'''


def _vm_session(cmd, d):
    script = (VM_SESSION.replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
              .replace("@DIR@", repr(str(d))))
    os.makedirs(str(d))
    out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout.splitlines()


def test_the_vm_store_is_the_native_one_and_answers_as_the_twin(tmp_path):
    exe = require_unix_mp(
        "moy_store",
        why="the store's file verbs on the VM a board runs, over the native "
            "module (docs/kernel_store_2026-10.md section 10, slice 2).")
    vm = _vm_session([exe], tmp_path / "vm")
    cp = _vm_session([sys.executable], tmp_path / "cpython")
    assert vm[0] == "native True" and cp[0] == "native False"
    assert vm[1:] == cp[1:]
