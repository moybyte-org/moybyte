"""The shelf's record and its removal on the CPython host: native/moy_store/
moy_shelf.h over ctypes, with the names the boards' `moy_store` module gives
them (`shelf_record`, `shelf_without`, `shelf_take`, `shelf_written`,
`shelf_remove`, `shelf_find`), so
runtime/carts/cart_index.py calls the one C on every tier.

The C is in the host's one library (runtime/host/host_native.py, the store's
part), loaded at the first call. A code becomes what modmoy_store.c makes of
it: None for "there is none", MemoryError, else OSError.
"""

import ctypes as c
import os


class _Buf(c.Structure):
    _fields_ = [("p", c.c_void_p), ("n", c.c_size_t)]


_NONE = -1
_ENOMEM = 12
_PATH = 320

_SIGS = (
    ("moy_shelf_record", [c.c_char_p, c.POINTER(_Buf)], c.c_int),
    ("moy_shelf_without", [c.c_char_p, c.c_char_p, c.POINTER(_Buf)], c.c_int),
    ("moy_shelf_take", [c.c_char_p, c.c_char_p, c.c_char_p, c.c_size_t], c.c_int),
    ("moy_shelf_written", [c.c_char_p, c.c_char_p, c.c_char_p, c.c_size_t], c.c_int),
    ("moy_shelf_remove", [c.c_char_p, c.c_char_p, c.c_char_p, c.c_size_t], c.c_int),
    ("moy_shelf_find", [c.c_char_p, c.c_char_p, c.c_char_p, c.c_size_t], c.c_int),
    ("moy_buf_free", [c.POINTER(_Buf)], None),
)

_LIB = [None]


def _lib():
    if _LIB[0] is None:
        import host_native
        path = host_native.build()
        if path is None:
            raise ImportError("moy_shelf: no C compiler for the host build")
        lib = c.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = lib
    return _LIB[0]


def _b(s):
    return s.encode()


def _raise(rc):
    if rc == _ENOMEM:
        raise MemoryError("moy_shelf")
    raise OSError(rc, os.strerror(rc))


def _text(lib, rc, buf):
    if rc == _NONE:
        return None
    if rc:
        _raise(rc)
    try:
        return c.string_at(buf.p, buf.n).decode()
    finally:
        lib.moy_buf_free(c.byref(buf))


def shelf_record(root):
    lib, buf = _lib(), _Buf()
    return _text(lib, lib.moy_shelf_record(_b(root), c.byref(buf)), buf)


def shelf_without(root, folder):
    lib, buf = _lib(), _Buf()
    return _text(lib, lib.moy_shelf_without(_b(root), _b(folder), c.byref(buf)), buf)


def _path(fn, root, folder):
    out = c.create_string_buffer(_PATH)
    rc = fn(_b(root), _b(folder), out, _PATH)
    if rc:
        _raise(rc)
    return out.value.decode()


def shelf_take(root, folder):
    return _path(_lib().moy_shelf_take, root, folder)


def shelf_written(root, folder):
    return _path(_lib().moy_shelf_written, root, folder)


def shelf_remove(root, folder, text):
    raw = None if text is None else _b(text)
    rc = _lib().moy_shelf_remove(_b(root), _b(folder), raw, len(raw or b""))
    if rc:
        _raise(rc)


def shelf_find(root, ident):
    out = c.create_string_buffer(_PATH)
    rc = _lib().moy_shelf_find(_b(root), _b(ident), out, _PATH)
    if rc == _NONE:
        return None
    if rc:
        _raise(rc)
    return out.value.decode()
