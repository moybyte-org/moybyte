"""The import tables WAMR registers where they are (the fork's
wasm_runtime_register_natives_sorted, native/moy_wasm/README.md): const rows
the build has already put in strcmp order of their names, never copied and
never sorted at run time, so on a board they stay in flash.

- the fork's call takes rows in order, refuses rows out of order or a name
  twice, and never writes to the rows it is given;
- the tables registered that way -- WASI's, moybyte.term's and moybyte.os's
  (native/moy_wasm/moy_wasi.c) and the app ABI's moybyte.app
  (native/moy_app/moy_app_wasm.c) -- register, and lie in the host library's
  read-only memory.
"""

import ctypes

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)


@pytest.fixture(scope="module")
def lib():
    from runtime import wasm_binding, wasm_host
    if not wasm_host.available():
        pytest.skip("no host wasm tier: %s" % wasm_binding.why_unavailable())
    d = wasm_binding._lib()
    d.wasm_runtime_register_natives_sorted.argtypes = [
        ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    d.wasm_runtime_register_natives_sorted.restype = ctypes.c_bool
    d.wasm_runtime_unregister_natives.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    d.wasm_runtime_unregister_natives.restype = ctypes.c_bool
    d.moy_wasi_register.argtypes = []
    d.moy_wasi_register.restype = ctypes.c_int
    d.moy_wasi_unregister.argtypes = []
    d.moy_wasi_unregister.restype = None
    d.moy_app_wasm_natives.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
    d.moy_app_wasm_natives.restype = ctypes.c_void_p
    return d


def _rows(names):
    from runtime import wasm_binding
    keep = [n.encode() for n in names]
    t = (wasm_binding._NativeSymbol * len(keep))()
    for i, n in enumerate(keep):
        t[i].symbol = n
        t[i].signature = b"()i"
    return t, keep


@pytest.mark.parametrize("names", [["b", "a"], ["a", "a"], ["a", "c", "b"]])
def test_rows_out_of_order_or_twice_are_refused(lib, names):
    t, _keep = _rows(names)
    before = bytes(t)
    assert not lib.wasm_runtime_register_natives_sorted(b"moytest.rows", t, len(names))
    assert bytes(t) == before


def test_rows_in_order_register_unwritten(lib):
    t, _keep = _rows(["a", "ab", "b", "fd_close", "fd_write"])
    before = bytes(t)
    assert lib.wasm_runtime_register_natives_sorted(b"moytest.rows", t, len(t))
    assert bytes(t) == before
    assert lib.wasm_runtime_unregister_natives(b"moytest.rows", t)
    assert not lib.wasm_runtime_unregister_natives(b"moytest.rows", t)


def _read_only(addr):
    """Whether `addr` lies in a mapping of this process without write access."""
    with open("/proc/self/maps") as f:
        for line in f:
            span, perms = line.split()[:2]
            lo, hi = (int(x, 16) for x in span.split("-"))
            if lo <= addr < hi:
                return "w" not in perms
    raise AssertionError("0x%x is in no mapping" % addr)


def test_the_wasi_tables_register_in_their_order(lib):
    """moy_wasi_register registers its three const tables with the sorted
    call, which refuses a table out of order; registered a second time beside
    the host's own registration, its unregister leaves one serving."""
    assert lib.moy_wasi_register() == 0
    lib.moy_wasi_unregister()


def test_the_app_table_is_sorted_and_read_only(lib):
    from runtime import wasm_binding
    n = ctypes.c_uint32(0)
    p = lib.moy_app_wasm_natives(ctypes.byref(n))
    rows = ctypes.cast(p, ctypes.POINTER(wasm_binding._NativeSymbol))
    names = [rows[i].symbol for i in range(n.value)]
    assert n.value > 0 and names == sorted(names) and len(set(names)) == len(names)
    assert _read_only(p)
