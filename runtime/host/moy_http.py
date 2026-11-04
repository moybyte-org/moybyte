"""The `http` device API's driver (native/moy_net/moy_fetch.h) on CPython, by
ctypes: the module `moy_http` as the boards import it, name for name
(native/moy_net/modmoy_http.c has the list). The C is the host's one library
(runtime/host/host_native.py) with the kernel's client under it, which speaks
plain TCP on the host; `run()` is the host's worker, serving what is queued."""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import host_native, native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import host_native
    import native_build

_NET = os.path.join(native_build.ROOT, "native", "moy_net")
_INPUT = os.path.join(native_build.ROOT, "native", "moy_input")

PART = native_build.Part("http", ("moy_fetch.h", "moy_fetch.c"), (_NET, _INPUT))

QUEUED, RUNNING, DONE, FAILED = 1, 2, 3, 4
APP = 0x01
CAP_MAX = 256 * 1024
ETLS = 93

_U32, _I = ctypes.c_uint32, ctypes.c_int

_SIGS = (
    ("moy_fetch_open", [_U32, ctypes.c_char_p, _U32, _U32, ctypes.POINTER(_U32)], _I),
    ("moy_fetch_state", [_U32, ctypes.POINTER(_I), ctypes.POINTER(_U32), ctypes.POINTER(_I)], _I),
    ("moy_fetch_read", [_U32, _U32, ctypes.c_void_p, _U32], _I),
    ("moy_fetch_close", [_U32], _I),
    ("moy_fetch_sweep", [_U32], _U32),
    ("moy_fetch_count", [_U32], _U32),
    ("moy_fetch_source", [], _U32),
    ("moy_fetch_run", [], None),
    ("moy_fetch_present", [], _I),
    ("moy_fetch_reset", [], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("moy_http: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = d
    return _LIB[0]


def open(owner, url, cap, flags):  # noqa: A001 -- the board module's name
    h = _U32()
    rc = _lib().moy_fetch_open(owner, url.encode(), cap, flags, ctypes.byref(h))
    return rc if rc < 0 else h.value


def state(h):
    status, n, err = _I(), _U32(), _I()
    st = _lib().moy_fetch_state(h, ctypes.byref(status), ctypes.byref(n), ctypes.byref(err))
    return st if st < 0 else (st, status.value, n.value, err.value)


def read(h, off, buf):
    c = (ctypes.c_char * len(buf)).from_buffer(buf)
    return _lib().moy_fetch_read(h, off, c, len(buf))


def close(h):
    return _lib().moy_fetch_close(h)


def sweep(owner):
    return _lib().moy_fetch_sweep(owner)


def count(owner):
    return _lib().moy_fetch_count(owner)


def source():
    return _lib().moy_fetch_source()


def run():
    _lib().moy_fetch_run()


def present():
    return bool(_lib().moy_fetch_present())


def reset():
    _lib().moy_fetch_reset()
