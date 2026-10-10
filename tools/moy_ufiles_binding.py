"""The user-files layer on the CPython host: native/moy_store/moy_ufiles.h over
ctypes, as the module `moy_ufiles` the boards' modmoy_ufiles.c registers. The
runtime package registers it under that name (runtime/__init__.py), so
`moy_carts`' user-files names are the C every image runs.

The library is built the first time a name is used, not at import: it needs
the MicroPython tree's inflater and compressor (lib/uzlib, `make
unix-micropython`), and a tool that never touches a user file never builds it.
tools/moy_app_binding.py hands this same library's verb table (`ops()`) to the
files and wallpaper rows, so one process has one publish marker and one prune
counter.

Each call is moy_ufiles.h's ABI and nothing more; what this adds is what
modmoy_ufiles.c adds on a VM: a code becomes ValueError (BAD), MemoryError
(ENOMEM), OSError (an errno value) or None (NONE), JSON answers become their
values, and a list of names becomes a list.
"""

import ctypes as c
import errno as _errno
import json
import os
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
STORE = os.path.join(ROOT, "native", "moy_store")
SPINE = os.path.join(ROOT, "native", "moy_spine")
PNG = os.path.join(ROOT, "native", "moy_png")
UZLIB = os.path.join(ROOT, ".build", "unix_micropython", "micropython", "lib", "uzlib")
CACHE = os.path.join(ROOT, ".build", "host_ufiles")

NONE, BAD = -1, -2
NAME_MAX = 255
CARTS_DIR = "/sd/moybyte/carts"
_ENOMEM = 12

_NAMES = ("moy_ufiles.h", "moy_ufiles.c", "moy_fs.h", "moy_fs.c", "moy_vol.h",
          "moy_vol.c", "moy_journal.h", "moy_journal.c", "moy_cat.h", "moy_cat.c",
          "moy_load.h", "moy_arena.h", "moy_img.h", "moy_img.c", "moy_json.h",
          "moy_json.c", "moy_png.h", "moy_png.c", "uzlib.h", "uzlib_conf.h",
          "tinflate.c", "header.c", "adler32.c", "crc32.c", "lz77.c",
          "defl_static.c")
_COMPILE = ("moy_store_host.c", "moy_ufiles.c", "moy_fs.c", "moy_vol.c",
            "moy_journal.c", "moy_cat.c", "moy_img.c", "moy_json.c", "moy_png.c",
            "tinflate.c", "header.c", "adler32.c", "crc32.c", "lz77.c")


class _Buf(c.Structure):
    _fields_ = [("p", c.c_void_p), ("n", c.c_size_t)]


_P = c.c_char_p
_S = c.c_size_t
_U32 = c.c_uint32
_PB = c.POINTER(_Buf)
_PU32 = c.POINTER(c.c_uint32)
_PINT = c.POINTER(c.c_int)
_NAME = c.c_char * (NAME_MAX + 1)

_SIGS = (
    ("moy_uf_kind", [c.c_int], _P),
    ("moy_uf_kind_ext", [c.c_int], _P),
    ("moy_uf_kind_folder", [c.c_int], c.c_int),
    ("moy_uf_kind_base", [c.c_int], _P),
    ("moy_uf_vault_ext", [_P], _S),
    ("moy_uf_script_ext", [_P], _S),
    ("moy_uf_path", [c.c_int, _P, _P, _P, _PB], c.c_int),
    ("moy_uf_list", [_P, _P, _PB, _PU32], c.c_int),
    ("moy_uf_count", [_P, _P, _PU32], c.c_int),
    ("moy_uf_load", [_P, _P, _P, _PB, _PINT], c.c_int),
    ("moy_uf_save", [_P, _P, _P, _P, _S, c.c_char_p], c.c_int),
    ("moy_uf_new_name", [_P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_free_name", [_P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_rename", [_P, _P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_duplicate", [_P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_delete", [_P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_restore", [_P, _P, _P, c.c_char_p], c.c_int),
    ("moy_uf_trash_list", [_P, _PB, _PU32], c.c_int),
    ("moy_uf_prune_trash", [_P, _U32], c.c_int),
    ("moy_uf_history", [_P, _P, _P, _PB], c.c_int),
    ("moy_uf_history_ops", [_P, _P, _P, _PB], c.c_int),
    ("moy_uf_history_commit", [_P, _P, _P, _P, _S, _P, _S, _PINT], c.c_int),
    ("moy_uf_prune_history", [_P, _P, _P, _U32, _PU32], c.c_int),
    ("moy_uf_clear_history", [_P, _P, _P], c.c_int),
    ("moy_uf_prune_fails", [], _U32),
    ("moy_uf_sig", [_P, _S], _U32),
    ("moy_uf_stamp", [_P, _S, _P, _P, _U32, _PB], c.c_int),
    ("moy_uf_provenance", [_P, _S, _PB, c.POINTER(c.c_int64)], c.c_int),
    ("moy_uf_encode_image", [_U32, _U32, _P, _S, _PB], c.c_int),
    ("moy_uf_decode_image", [_P, _S, _PB, _PU32, _PU32], c.c_int),
    ("moy_uf_encode_cover", [_P, _S, _PB], c.c_int),
    ("moy_uf_decode_cover", [_P, _S, _PB], c.c_int),
    ("moy_uf_palette", [], c.c_void_p),
    ("moy_uf_copy_load", [_P, _PB], c.c_int),
    ("moy_uf_copy_save", [_P, _P, _S], c.c_int),
    ("moy_uf_clock_fix", [c.c_int64], None),
    ("moy_buf_free", [_PB], None),
    ("moy_fs_root", [_P], c.c_int),
)

_LIB = [None]


def library():
    """The host library (built once per content), or ImportError naming what
    is missing."""
    if _LIB[0] is not None:
        return _LIB[0]
    from runtime import native_build
    if not os.path.isdir(UZLIB):
        raise ImportError("moy_ufiles: no MicroPython tree for its inflater and "
                          "compressor (make unix-micropython)")
    path = native_build.build(
        "moy_ufiles", os.path.join(STORE, "moy_store_host.c"), _NAMES, CACHE,
        compile_names=_COMPILE, libmoy_dir=(STORE, SPINE, PNG, UZLIB))
    if path is None:
        raise ImportError("moy_ufiles: no C compiler for the host build")
    lib = c.CDLL(path)
    for name, args, res in _SIGS:
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res
    _LIB[0] = lib
    return lib


def ops():
    """The address of the library's verb table (moy_uf_ops), for moy_app."""
    return c.addressof(c.c_char.in_dll(library(), "moy_uf_ops"))


def _b(s):
    return s if isinstance(s, bytes) else str(s).encode()


def _raise(rc):
    if rc == BAD:
        raise ValueError("moy_ufiles: an argument the store refuses")
    if rc == _ENOMEM:
        raise MemoryError("moy_ufiles")
    raise OSError(rc, os.strerror(rc))


def _take(lib, buf, text=True):
    try:
        raw = c.string_at(buf.p, buf.n) if buf.p else b""
    finally:
        lib.moy_buf_free(c.byref(buf))
    return raw.decode() if text else raw


def _names(raw, count):
    parts = raw.split("\0")
    return parts[:count]


def binding():
    """The `moy_ufiles` module's names, the library built at their first use."""
    m = types.ModuleType("moy_ufiles")
    m.__doc__ = __doc__
    m.CARTS_DIR = CARTS_DIR
    m.FILES_DIR = "files"
    m.TRASH_DIR = "trash"
    m.TRASH_KEEP = 50
    m.HISTORY_DIR = ".history"
    m.HISTORY_EXT = ".jsonl"
    m.HISTORY_KEEP = 32
    m.DOC_EXT = ".md"
    m.SCRIPT_EXTS = (".py", ".lua")
    m.VAULT_EXTS = (".py", ".lua", ".txt", ".json")
    m.PROJECT_KIND = "project:"
    roots = set()

    def lib():
        return library()

    def _root(root):
        """Register the store's publish root once (ensure_dirs' marker root),
        as the console's boot does on a board, where the store is one."""
        r = str(root)
        if r not in roots:
            roots.add(r)
            parent = r.rsplit("/", 1)[0]
            lib().moy_fs_root(_b(parent or r))
        return _b(r)

    def _kinds():
        d = {}
        L = lib()
        i = 0
        while True:
            k = L.moy_uf_kind(i)
            if k is None:
                return d
            d[k.decode()] = (L.moy_uf_kind_ext(i).decode(), bool(L.moy_uf_kind_folder(i)),
                             L.moy_uf_kind_base(i).decode())
            i += 1

    def kinds():
        """{kind: (extension, folder_valued, auto-name base)}, the registry."""
        return _kinds()

    def script_ext(name):
        name = str(name)
        k = lib().moy_uf_script_ext(_b(name))
        return name[-k:] if k else ""

    def vault_ext(name):
        name = str(name)
        k = lib().moy_uf_vault_ext(_b(name))
        return name[-k:] if k else ""

    def project_kind(path_or_folder):
        name = str(path_or_folder)
        cut = max(name.rfind("/"), name.rfind("\\"))
        return m.PROJECT_KIND + (name[cut + 1:] if cut >= 0 else name)

    def project_folder(kind):
        k = str(kind)
        return k[len(m.PROJECT_KIND):] if k.startswith(m.PROJECT_KIND) else ""

    def _path(which, root, kind="", name=""):
        buf = _Buf()
        rc = lib().moy_uf_path(which, _b(root), _b(kind), _b(name), c.byref(buf))
        if rc:
            _raise(rc)
        return _take(lib(), buf)

    def files_root(root=CARTS_DIR):
        return _path(0, root)

    def file_kind_dir(kind, root=CARTS_DIR):
        return _path(1, root, kind)

    def file_path(kind, name, root=CARTS_DIR):
        return _path(2, root, kind, name)

    def history_path(kind, name, root=CARTS_DIR):
        return _path(3, root, kind, name)

    def trash_path(kind, name, root=CARTS_DIR):
        return _path(4, root, kind, name)

    def history_trash_path(kind, name, root=CARTS_DIR):
        return _path(5, root, kind, name)

    def project_dir(kind, root=CARTS_DIR):
        return _path(6, root, kind)

    def project_file_path(kind, name, root=CARTS_DIR):
        return _path(7, root, kind, name)

    def list_files(kind, root=CARTS_DIR):
        buf, n = _Buf(), c.c_uint32()
        rc = lib().moy_uf_list(_root(root), _b(kind), c.byref(buf), c.byref(n))
        if rc:
            _raise(rc)
        return _names(_take(lib(), buf), n.value)

    def count_files(kind, root=CARTS_DIR):
        n = c.c_uint32()
        rc = lib().moy_uf_count(_root(root), _b(kind), c.byref(n))
        if rc:
            _raise(rc)
        return n.value

    def load_file(kind, name, root=CARTS_DIR):
        buf, binary = _Buf(), c.c_int()
        rc = lib().moy_uf_load(_root(root), _b(kind), _b(name), c.byref(buf),
                               c.byref(binary))
        if rc == NONE:
            return None
        if rc:
            _raise(rc)
        return _take(lib(), buf, not binary.value)

    def _named(fn, *args):
        out = _NAME()
        rc = fn(*args, out)
        if rc:
            _raise(rc)
        return out.value.decode()

    def save_file(kind, name, text, root=CARTS_DIR):
        data = _b(text)
        return _named(lib().moy_uf_save, _root(root), _b(kind), _b(name), data, len(data))

    def new_file_name(kind, root=CARTS_DIR, base=None):
        return _named(lib().moy_uf_new_name, _root(root), _b(kind),
                      _b(base) if base else None)

    def free_file_name(kind, title, root=CARTS_DIR):
        return _named(lib().moy_uf_free_name, _root(root), _b(kind), _b(title))

    def rename_file(kind, name, new_title, root=CARTS_DIR):
        return _named(lib().moy_uf_rename, _root(root), _b(kind), _b(name), _b(new_title))

    def duplicate_file(kind, name, root=CARTS_DIR):
        return _named(lib().moy_uf_duplicate, _root(root), _b(kind), _b(name))

    def delete_file(kind, name, root=CARTS_DIR):
        return _named(lib().moy_uf_delete, _root(root), _b(kind), _b(name))

    def restore_file(kind, name, root=CARTS_DIR):
        return _named(lib().moy_uf_restore, _root(root), _b(kind), _b(name))

    def trash_list(root=CARTS_DIR):
        buf, n = _Buf(), c.c_uint32()
        rc = lib().moy_uf_trash_list(_root(root), c.byref(buf), c.byref(n))
        if rc:
            _raise(rc)
        parts = _take(lib(), buf).split("\0")
        return [(parts[2 * i], parts[2 * i + 1]) for i in range(n.value)]

    def prune_trash(root=CARTS_DIR, keep=50):
        rc = lib().moy_uf_prune_trash(_root(root), keep)
        if rc:
            _raise(rc)

    def empty_trash(root=CARTS_DIR):
        prune_trash(root, 0)

    def _json(fn, kind, name, root):
        buf = _Buf()
        rc = fn(_root(root), _b(kind), _b(name), c.byref(buf))
        if rc:
            _raise(rc)
        return json.loads(_take(lib(), buf))

    def load_history(kind, name, root=CARTS_DIR):
        return _json(lib().moy_uf_history, kind, name, root)

    def history_ops(kind, name, root=CARTS_DIR):
        return _json(lib().moy_uf_history_ops, kind, name, root)

    def history_commit(kind, name, ops, keyframe=None, root=CARTS_DIR):
        o = json.dumps(list(ops)).encode() if ops else None
        k = json.dumps(keyframe).encode() if keyframe is not None else None
        err = c.c_int()
        rc = lib().moy_uf_history_commit(_root(root), _b(kind), _b(name), o,
                                         len(o or b""), k, len(k or b""), c.byref(err))
        if rc:
            _raise(rc)
        if err.value:
            return ("moy_ufiles: an argument the store refuses" if err.value == BAD
                    else str(OSError(err.value, os.strerror(err.value))))
        return None

    def prune_history(kind, name, root=CARTS_DIR, keep=32):
        n = c.c_uint32()
        rc = lib().moy_uf_prune_history(_root(root), _b(kind), _b(name), keep, c.byref(n))
        if rc:
            _raise(rc)
        return n.value

    def clear_history(kind, name, root=CARTS_DIR):
        rc = lib().moy_uf_clear_history(_root(root), _b(kind), _b(name))
        if rc:
            _raise(rc)

    def history_prune_fails():
        return lib().moy_uf_prune_fails()

    def content_sig(text):
        if not text:
            return 0
        raw = _b(text)
        return lib().moy_uf_sig(raw, len(raw))

    def stamp_provenance(blob, kind, name, sig):
        if not isinstance(blob, (str, bytes)):
            return blob
        raw = _b(blob)
        buf = _Buf()
        rc = lib().moy_uf_stamp(raw, len(raw), _b(kind), _b(name),
                                int(sig) & 0xFFFFFFFF, c.byref(buf))
        if rc == NONE:
            return blob
        if rc:
            _raise(rc)
        return _take(lib(), buf)

    def read_provenance(blob):
        if not isinstance(blob, (str, bytes)):
            return (None, None)
        raw = _b(blob)
        buf, sig = _Buf(), c.c_int64()
        rc = lib().moy_uf_provenance(raw, len(raw), c.byref(buf), c.byref(sig))
        if rc == NONE:
            return (None, None)
        if rc:
            _raise(rc)
        return (_take(lib(), buf), sig.value)

    def encode_image(w, h, indices):
        pix = bytes(bytearray(indices))
        buf = _Buf()
        rc = lib().moy_uf_encode_image(int(w), int(h), pix, len(pix), c.byref(buf))
        if rc == BAD:
            raise ValueError("bad artwork size")
        if rc:
            _raise(rc)
        return _take(lib(), buf)

    def decode_image(blob):
        raw = _b(blob)
        buf, w, h = _Buf(), c.c_uint32(), c.c_uint32()
        rc = lib().moy_uf_decode_image(raw, len(raw), c.byref(buf), c.byref(w), c.byref(h))
        if rc == NONE:
            return None
        if rc:
            _raise(rc)
        return (w.value, h.value, _take(lib(), buf, False))

    def encode_cover(indices):
        pix = bytes(bytearray(indices))
        buf = _Buf()
        rc = lib().moy_uf_encode_cover(pix, len(pix), c.byref(buf))
        if rc:
            _raise(rc)
        return _take(lib(), buf, False)

    def decode_cover(data):
        raw = bytes(data)
        buf = _Buf()
        rc = lib().moy_uf_decode_cover(raw, len(raw), c.byref(buf))
        if rc == NONE:
            return None
        if rc:
            _raise(rc)
        return (128, 128, _take(lib(), buf, False))

    def palette():
        return c.string_at(lib().moy_uf_palette(), 192)

    def load_copy(root=CARTS_DIR):
        buf = _Buf()
        rc = lib().moy_uf_copy_load(_root(root), c.byref(buf))
        if rc == NONE:
            return None
        if rc:
            _raise(rc)
        return _take(lib(), buf)

    def save_copy(text, root=CARTS_DIR):
        data = _b(text)
        rc = lib().moy_uf_copy_save(_root(root), data, len(data))
        if rc:
            _raise(rc)

    def clock_fix(ts):
        lib().moy_uf_clock_fix(int(ts))

    for name, v in list(locals().items()):
        if callable(v) and not name.startswith("_") and name not in ("lib", "m"):
            setattr(m, name, v)
    return m
