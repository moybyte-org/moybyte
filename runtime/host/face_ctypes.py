"""What every ctypes face shares: the one library they bind and the
converters a generated row calls (tools/gen_os/face.py writes the rows).

`lib(sigs)` answers the host's one native library (runtime/host/host_native.py)
with `sigs` -- (C name, argtypes, restype) rows -- bound on it, once per
tuple; every face's `_lib()` is this."""

import ctypes
import os

import host_native

_LIB = [None]
_BOUND = set()


def library():
    """The host's one native library, loaded once."""
    if _LIB[0] is None:
        path = host_native.build()
        if path is None:
            raise ImportError("the host's native modules need a C compiler")
        _LIB[0] = ctypes.CDLL(path)
    return _LIB[0]


def lib(sigs):
    """The library, `sigs` bound on it."""
    d = library()
    key = id(sigs)
    if key not in _BOUND:
        for name, args, res in sigs:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        _BOUND.add(key)
    return d


def cstr(v):
    """A str or bytes as the bytes a const char * reads."""
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if not isinstance(v, str):
        raise TypeError("can't convert %s to str" % type(v).__name__)
    return v.encode("utf-8")


def span(v):
    """A str or a bytes-like as the bytes a (pointer, size) pair reads."""
    if isinstance(v, str):
        return v.encode("utf-8")
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    raise TypeError("object with buffer protocol required")


def text(p):
    """A borrowed const char * answer as a str (NULL: None)."""
    return None if p is None else p.decode("utf-8", "replace")


def raise_errno(eno):
    raise OSError(eno, os.strerror(eno))


def wbuf(v):
    """A writable buffer (a bytearray, an array) as the ctypes array a
    (pointer, size) pair writes into."""
    mv = memoryview(v).cast("B")
    return (ctypes.c_char * len(mv)).from_buffer(mv)


def loaded():
    """Whether the library is loaded yet (a reset with nothing to reset)."""
    return _LIB[0] is not None
