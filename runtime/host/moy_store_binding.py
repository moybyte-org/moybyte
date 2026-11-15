"""The store on the CPython host: native/moy_store over ctypes, as the module
`moy_store` the boards' modmoy_store.c registers, with the names CPython
runs -- the crash-safe write and its readers (moy_fs.h), a cart as it
travels (moy_pack.h), the tree removal and the shelf (moy_shelf.h). The
runtime package registers it under that name (runtime/__init__.py), so
runtime/carts' store modules call the one C on every tier.

The rows the host leaves out are face.json's `absent`: the card and the
internal volume, the store task, the catalogue, the seed and the journal,
whose CPython callers keep their own bodies (docs/refactor_2026-11.md §7).

The C is in the host's one library (runtime/host/host_native.py), loaded at
the first call (MOYHOST_LIB names one already built, for a CPython that runs
the staged tree, tests/unix_mp.py); the archive's rows (pack, unpack, adopt) are in the part
that needs the MicroPython tree's inflater (`make unix-micropython`). A code
becomes what modmoy_store.c makes of it: MemoryError for ENOMEM, else
OSError(errno). A relative path is the working folder's, as path_arg
makes it on a VM.
"""

import ctypes as c
import errno
import os
import types
from moy_store_face import moy_buf_t as _Buf


_NONE = -1
_EFBIG = errno.EFBIG
_PATH = 320
_P = c.c_char_p
_S = c.c_size_t
_U32 = c.c_uint32
_PB = c.POINTER(_Buf)
_PU32 = c.POINTER(c.c_uint32)

_SIGS = (
    ("moy_fs_root", [_P], c.c_int),
    ("moy_fs_roots_clear", [], None),
    ("moy_fs_publish", [_P, _P, _S], c.c_int),
    ("moy_fs_publish_bak", [_P, _P, _S], c.c_int),
    ("moy_fs_publish_mark", [_P, _P, _S], c.c_int),
    ("moy_fs_write", [_P, _P, _S], c.c_int),
    ("moy_fs_write_bytes", [_P, _P, _S], c.c_int),
    ("moy_fs_read", [_P, _P, _PB], c.c_int),
    ("moy_fs_read_file", [_P, _S, _PB], c.c_int),
    ("moy_fs_read_stamped", [_P, _PB], c.c_int),
    ("moy_fs_bak_stamp", [_P, _PU32, _PU32], c.c_int),
    ("moy_fs_stamp", [_P, _S, _PU32, _PU32], None),
    ("moy_fs_claim", [_P, _P, _U32, _U32], c.c_int),
    ("moy_fs_forget_bak", [_P], None),
    ("moy_fs_unmark", [_P], None),
    ("moy_fs_remove", [_P], c.c_int),
    ("moy_fs_mkdir", [_P], c.c_int),
    ("moy_fs_exists", [_P], c.c_int),
    ("moy_store_skip", [_P, _S, c.c_int], c.c_int),
    ("moy_cat_rmtree", [_P], None),
    ("moy_shelf_record", [_P, _PB], c.c_int),
    ("moy_shelf_without", [_P, _P, _PB], c.c_int),
    ("moy_shelf_take", [_P, _P, _P, _S], c.c_int),
    ("moy_shelf_written", [_P, _P, _P, _S], c.c_int),
    ("moy_shelf_remove", [_P, _P, _P, _S], c.c_int),
    ("moy_shelf_find", [_P, _P, _P, _S], c.c_int),
    ("moy_buf_free", [_PB], None),
)
# The archive's rows: the part the inflater builds (moy_ufiles_binding.part).
_PACK_SIGS = (
    ("moy_pack_paths", [_P, c.POINTER(_P), _S, _P, _P, c.c_int], c.c_int),
    ("moy_unpack_all", [_P, _P, _P, _S, c.c_int], c.c_int),
    ("moy_adopt", [_P, _P], c.c_int),
)

_LIB = [None]
_PACK = [None]


def _lib():
    if _LIB[0] is None:
        path = os.environ.get("MOYHOST_LIB")
        if not path:
            import host_native
            path = host_native.build()
        if path is None:
            raise ImportError("moy_store: no C compiler for the host build")
        lib = c.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = res
        _LIB[0] = lib
    return _LIB[0]


def _pack_lib():
    if _PACK[0] is None:
        lib = _lib()
        if not hasattr(lib, "moy_pack_paths"):
            raise ImportError("moy_store: no MicroPython tree for the archive's "
                              "inflater (make unix-micropython)")
        for name, args, res in _PACK_SIGS:
            fn = getattr(lib, name)
            fn.argtypes = args
            fn.restype = res
        _PACK[0] = lib
    return _PACK[0]


def _path(p):
    s = os.fspath(p)
    if not isinstance(s, str):
        raise TypeError("a store path is a str")
    if not s.startswith("/"):
        cwd = os.getcwd()
        s = cwd + ("" if cwd.endswith("/") else "/") + s
    return s.encode("utf-8", "surrogateescape")


def _data(d):
    if isinstance(d, str):
        return d.encode("utf-8")
    return bytes(d)


def _raise(rc):
    if rc == errno.ENOMEM:
        raise MemoryError("moy_store")
    raise OSError(rc, os.strerror(rc))


def _ok(rc):
    if rc:
        _raise(rc)


def _take(lib, buf, text=True):
    try:
        raw = c.string_at(buf.p, buf.n) if buf.p else b""
    finally:
        lib.moy_buf_free(c.byref(buf))
    return raw.decode("utf-8") if text else raw


def _text(lib, rc, buf):
    if rc == _NONE:
        return None
    _ok(rc)
    return _take(lib, buf)


def _out(fn, *args):
    out = c.create_string_buffer(_PATH)
    _ok(fn(*args, out, _PATH))
    return out.value.decode()


def binding():
    """The `moy_store` module's host names, the library built at their first
    use."""
    m = types.ModuleType("moy_store")
    m.__doc__ = __doc__
    roots = []

    def set_publish_root(root):
        r = os.fspath(root)
        _ok(_lib().moy_fs_root(r.encode("utf-8", "surrogateescape")))
        if r in roots:
            roots.remove(r)
        roots.append(r)

    def _reboot():
        """The roots registered again with no marker read: what a process
        that starts over knows. Host only."""
        _lib().moy_fs_roots_clear()
        for r in roots:
            _lib().moy_fs_root(r.encode("utf-8", "surrogateescape"))

    def _roots_clear():
        """No root registered: a process that has registered none. Host only."""
        _lib().moy_fs_roots_clear()
        del roots[:]

    def _publish(fn):
        def publish(path, data):
            b = _data(data)
            _ok(fn()(_path(path), b, len(b)))
        return publish

    publish = _publish(lambda: _lib().moy_fs_publish)
    publish_bak = _publish(lambda: _lib().moy_fs_publish_bak)
    publish_mark = _publish(lambda: _lib().moy_fs_publish_mark)
    write = _publish(lambda: _lib().moy_fs_write)
    write_bytes = _publish(lambda: _lib().moy_fs_write_bytes)

    def read_recover(path, at=None):
        lib, buf = _lib(), _Buf()
        _ok(lib.moy_fs_read(_path(path), None, c.byref(buf)))
        return _take(lib, buf)

    def read(path):
        lib, buf = _lib(), _Buf()
        _ok(lib.moy_fs_read_file(_path(path), c.c_size_t(-1).value, c.byref(buf)))
        return _take(lib, buf)

    def read_bytes(path, cap):
        lib, buf = _lib(), _Buf()
        rc = lib.moy_fs_read_file(_path(path), max(0, int(cap)), c.byref(buf))
        if rc == _EFBIG:
            return None
        _ok(rc)
        return _take(lib, buf, text=False)

    def read_stamped(path):
        lib, buf = _lib(), _Buf()
        return _text(lib, lib.moy_fs_read_stamped(_path(path), c.byref(buf)), buf)

    def bak_stamp(path):
        chars, crc = c.c_uint32(), c.c_uint32()
        if not _lib().moy_fs_bak_stamp(_path(path), c.byref(chars), c.byref(crc)):
            return None
        return (chars.value, crc.value)

    def stamp(data):
        b = _data(data)
        chars, crc = c.c_uint32(), c.c_uint32()
        _lib().moy_fs_stamp(b, len(b), c.byref(chars), c.byref(crc))
        return (chars.value, crc.value)

    def claim(path, dest, st):
        chars, crc = st
        return bool(_lib().moy_fs_claim(_path(path), _path(dest), int(chars) & 0xFFFFFFFF,
                                        int(crc) & 0xFFFFFFFF))

    def forget_bak(path):
        _lib().moy_fs_forget_bak(_path(path))

    def unmark(path):
        _lib().moy_fs_unmark(_path(path))

    def remove(path):
        _lib().moy_fs_remove(_path(path))

    def mkdir(path):
        _lib().moy_fs_mkdir(_path(path))

    def exists(path):
        return bool(_lib().moy_fs_exists(_path(path)))

    def skip(name, history):
        b = name.encode("utf-8", "surrogateescape")
        return bool(_lib().moy_store_skip(b, len(b), 1 if history else 0))

    def pack_paths(base, rels, folder, dest, history):
        rels = [r.encode("utf-8", "surrogateescape") for r in rels]
        arr = (_P * max(1, len(rels)))(*rels)
        rc = _pack_lib().moy_pack_paths(_path(base), arr, len(rels), folder.encode(),
                                        _path(dest), 1 if history else 0)
        if rc < 0:
            _raise(-rc)
        return rc

    def pack(cart, folder, dest, history):
        return pack_paths(cart, [""], folder, dest, history)

    def unpack(archive, dest, history=False):
        top = c.create_string_buffer(128)
        rc = _pack_lib().moy_unpack_all(_path(archive), _path(dest), top, len(top),
                                        1 if history else 0)
        if rc < 0:
            _raise(-rc)
        return (rc, top.value.decode())

    def adopt(stage, target):
        _ok(_pack_lib().moy_adopt(_path(stage), _path(target)))

    def rmtree(path):
        _lib().moy_cat_rmtree(_path(path))

    def shelf_record(root):
        lib, buf = _lib(), _Buf()
        return _text(lib, lib.moy_shelf_record(_path(root), c.byref(buf)), buf)

    def shelf_without(root, folder):
        lib, buf = _lib(), _Buf()
        return _text(lib, lib.moy_shelf_without(_path(root), folder.encode(),
                                                c.byref(buf)), buf)

    def shelf_take(root, folder):
        return _out(_lib().moy_shelf_take, _path(root), folder.encode())

    def shelf_written(root, folder):
        return _out(_lib().moy_shelf_written, _path(root), folder.encode())

    def shelf_remove(root, folder, text):
        raw = None if text is None else text.encode()
        _ok(_lib().moy_shelf_remove(_path(root), folder.encode(), raw, len(raw or b"")))

    def shelf_find(root, ident):
        out = c.create_string_buffer(_PATH)
        rc = _lib().moy_shelf_find(_path(root), ident.encode(), out, _PATH)
        if rc == _NONE:
            return None
        _ok(rc)
        return out.value.decode()

    for name, v in list(locals().items()):
        if callable(v) and name not in ("m", "roots"):
            setattr(m, name, v)
    return m
