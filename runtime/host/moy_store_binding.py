"""The store on the CPython host: native/moy_store over ctypes, as the module
`moy_store` the boards' modmoy_store.c registers, with the names CPython
runs -- the crash-safe write and its readers (moy_fs.h), the catalogue and
the whole-cart load (moy_cat.h, moy_load.h), the seed (moy_seed.h), the
journal (moy_journal.h), a cart as it travels (moy_pack.h), the tree removal,
the shelf (moy_shelf.h) and the removal record (moy_removed.h). The runtime
package registers it under that name (runtime/__init__.py), so runtime/carts'
store modules call the one C on every tier. An entry and a cart are the dicts
modmoy_store.c builds, from the generated mirrors of the C's structs
(runtime/host/moy_store_face.py).

The rows the host leaves out are face.json's `absent`: the card and the
internal volume, the store task and the meters, a VM's own.

The C is in the host's one library (runtime/host/host_native.py), loaded at
the first call (MOYHOST_LIB names one already built, for a CPython that runs
the staged tree, tests/unix_mp.py); the archive's rows (pack, unpack, adopt) and the seed are in the part
that needs the MicroPython tree's inflater (`make unix-micropython`). A code
becomes what modmoy_store.c makes of it: MemoryError for ENOMEM, else
OSError(errno). A relative path is the working folder's, as path_arg
makes it on a VM.
"""

import ctypes as c
import errno
import json
import os
import types
from moy_store_face import moy_buf_t as _Buf, moy_cart_t as _Cart, moy_cat_entry_t as _Entry


_NONE = -1
_EFBIG = errno.EFBIG
_PATH = 320
_P = c.c_char_p
_S = c.c_size_t
_U32 = c.c_uint32
_PB = c.POINTER(_Buf)
_PU32 = c.POINTER(c.c_uint32)
_PS = c.POINTER(c.c_size_t)
_PV = c.POINTER(c.c_void_p)
_JENT = c.CFUNCTYPE(c.c_int, c.c_void_p, c.c_uint32, c.c_void_p, c.c_size_t)


_CATFN = c.CFUNCTYPE(c.c_int, c.c_void_p, c.POINTER(_Entry))
_LOADFN = c.CFUNCTYPE(c.c_int, c.c_void_p, c.POINTER(_Cart))
_NOTE = c.CFUNCTYPE(None, c.c_void_p, c.c_char_p, c.c_char_p, c.c_char_p)

_SIGS = (
    ("moy_fs_root", [_P], c.c_int),
    ("moy_fs_roots_clear", [], None),
    ("moy_fs_publish", [_P, _P, _S], c.c_int),
    ("moy_pmem_read", [_P, c.POINTER(c.c_int32)], c.c_int),
    ("moy_save_pmem", [_P, c.POINTER(c.c_int32)], c.c_int),
    ("moy_pmem_tidy", [], c.c_int),
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
    ("moy_removed_load", [_P, _PB], c.c_int),
    ("moy_removed_note", [_P, _P], c.c_int),
    ("moy_removed_drop", [_P, _P], c.c_int),
    ("moy_cat_scan", [_P, _CATFN, _NOTE, c.c_void_p], c.c_int),
    ("moy_cat_entry", [_P, _CATFN, _NOTE, c.c_void_p], c.c_int),
    ("moy_cat_load", [_P, _LOADFN, _NOTE, c.c_void_p], c.c_int),
    ("moy_cat_copy", [_P, _P, _P], c.c_int),
    ("moy_cat_folder", [_P, _S, _P, _P, _S], _S),
    ("moy_json_ws", [c.c_void_p, c.c_void_p], c.c_void_p),
    ("moy_json_value", [c.c_void_p, c.c_void_p, _U32], c.c_void_p),
    ("moy_json_canon", [c.c_void_p, c.c_void_p, c.c_void_p, _S], _S),
    ("moy_journal_append", [_P, _P, _P, _S, c.c_int, _P, _S, c.c_int64, _PU32], c.c_int),
    ("moy_journal_undo", [_P, c.POINTER(_P), _S, _P, _S], c.c_int),
    ("moy_journal_redo", [_P, c.POINTER(_P), _S, _P, _S], c.c_int),
    ("moy_journal_can", [_P, c.c_int, c.POINTER(_P), _S], c.c_int),
    ("moy_journal_compact", [_P], c.c_int),
    ("moy_journal_list", [_P, _P, _JENT, c.c_void_p], c.c_int),
    ("moy_journal_snap", [_P, _U32, _PB], c.c_int),
    ("moy_journal_restore", [_P, _U32, c.c_int64, _PU32], c.c_int),
    ("moy_journal_graduate", [_P, c.c_int], c.c_int),
    ("moy_buf_free", [_PB], None),
)
# The archive's rows: the part the inflater builds (moy_ufiles_binding.part).
_PACK_SIGS = (
    ("moy_pack_paths", [_P, c.POINTER(_P), _S, _P, _P, c.c_int], c.c_int),
    ("moy_unpack_all", [_P, _P, _P, _S, c.c_int], c.c_int),
    ("moy_adopt", [_P, _P], c.c_int),
    ("moy_seed_inflate", [_P, _S, _PB], c.c_int),
    ("moy_seed_write", [_P, _P, _P, _S], c.c_int),
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


def _str(p, n):
    return c.string_at(p, n).decode("utf-8", "surrogateescape") if p else ""


def _json(span, dflt):
    """What json.loads makes of a span the C scanned, or `dflt` with none."""
    if not span.v:
        return dflt
    return json.loads(c.string_at(span.v, span.e - span.v).decode("utf-8", "surrogatepass"))


def _span(sp):
    if not sp.p:
        return None
    return json.loads(c.string_at(sp.p, sp.e - sp.p).decode("utf-8", "surrogatepass"))


def _int_of(i, dflt):
    if i.kind == 1:
        return i.value
    if i.kind == 2:
        return int(_json(i.json, None))
    return dflt


_INPUT_KINDS = ("buttons", "touch", "keyboard")


def _entry(at, e):
    """The entry's dict, as modmoy_store.c's entry_dict builds it; `at` its path."""
    d = {"path": at, "id": _str(e.id, e.id_n),
         "title": _json(e.title, "cart"), "author": _json(e.author, ""),
         "type": _json(e.type, "game" if e.spec else "app"),
         "runtime": _json(e.runtime, "lua" if e.spec else "python"),
         "main": _str(e.main, e.main_n),
         "memory": _int_of(e.memory, None) if e.compiled else None,
         "writable": _json(e.writable, None) if e.writable_ok else None,
         "audio_ring": _int_of(e.audio_ring, None),
         "api": _int_of(e.api, None),
         "version": _int_of(e.version, 0),
         "format": _json(e.format, "moybyte-cart-v1"),
         "graduated": bool(e.graduated), "glass": bool(e.glass), "wasi": bool(e.wasi),
         "pictures": _json(e.pictures, None), "native": _json(e.native, None),
         "fps": _json(e.fps, 30 if e.spec else 0), "palette": _json(e.palette, None),
         "extensions": _json(e.extensions, []),
         "icon": tuple(e.icon) if e.icon_ok else None,
         "edit": _json(e.edit, []), "permissions": _json(e.permissions, []),
         "required": _json(e.required, []), "handles": _json(e.handles, []),
         "receives": _json(e.receives, None), "entries": _json(e.entries, None),
         "achievements": _json(e.achievements, None)}
    if e.input_n == c.c_size_t(-1).value:
        d["input"] = None
    else:
        kinds = c.cast(e.input, c.POINTER(c.c_uint8))
        d["input"] = tuple(_INPUT_KINDS[kinds[i]] for i in range(e.input_n))
    if e.canvas == 1:
        d["canvas"] = (e.canvas_w, e.canvas_h)
    else:
        d["canvas"] = _json(e.canvas_raw, None) if e.canvas == 2 else None
    d["description"] = _str(e.desc, e.desc_n) if e.desc else None
    d["genre"] = e.genre.decode() if e.genre else None
    d["tags"] = [e.tags[i].value.decode("utf-8", "surrogateescape") for i in range(e.tags_n)]
    if e.animates:
        d["animates"] = True
    if e.broken is not None:
        d["broken"] = e.broken.decode("utf-8", "surrogateescape")
    names, lens = c.cast(e.scenes, _PV), c.cast(e.scene_n, _PS)
    d["scene_names"] = [_str(names[i], lens[i]) for i in range(e.scenes_n)]
    if e.rows:
        want, n = c.cast(e.want, _PV), c.cast(e.want_n, _PS)
        d["icon_rows"] = (e.pw, e.ph, [_str(want[i], n[i]) if want[i] else None
                                       for i in range(e.ph)])
    else:
        d["icon_rows"] = None
    return d


def _files_of(f, n):
    return [(_str(f[i].name, f[i].name_n), _str(f[i].text, f[i].n)) for i in range(n)]


def _update(d, v):
    """dict.update of what moy_cat let through: a mapping or [key, value] pairs."""
    if isinstance(v, dict):
        d.update(v)
    elif isinstance(v, list):
        for k, x in v:
            d[k] = x


def _whole(at, ct):
    """The cart's dict, as modmoy_store.c's load_one builds it."""
    d = _entry(at, ct.e)
    del d["icon_rows"]
    cfg = {}
    _update(cfg, _span(ct.cfg_manifest))
    _update(cfg, _span(ct.cfg_file))
    d["cfg"] = cfg
    d["flags"] = _str(ct.flags, ct.flags_n) if ct.flags else None
    d["src"] = _str(ct.src, ct.src_n)
    d["src_before"] = _files_of(ct.before, ct.before_n)
    d["src_after"] = _files_of(ct.after, ct.after_n)
    d["sprites"] = _str(ct.sprites, ct.sprites_n) if ct.sprites else None
    d["sounds"] = _span(ct.sounds)
    d["map"] = _str(ct.map, ct.map_n) if ct.map else None
    d["blocks"] = _span(ct.blocks)
    d["images"] = dict(_files_of(ct.images, ct.images_n))
    d["scenes"] = dict(_files_of(ct.scenes, ct.scenes_n))
    return d


def _note(_ctx, what, path, detail):
    parts = ["Moybyte cart %s:" % what.decode(), path.decode("utf-8", "surrogateescape")]
    if detail is not None:
        parts.append(detail.decode("utf-8", "surrogateescape"))
    print(*parts)


_NOTE_FN = _NOTE(_note)


def _walk_cat(call, path, make, fn_type):
    """`call`'s entries as `make` builds each; a raise inside the callback
    comes back out here."""
    out, err = [], []

    def each(_ctx, e):
        try:
            out.append(make(e.contents))
        except BaseException as exc:  # noqa: BLE001 -- carried across the C frame
            err.append(exc)
            return 1
        return 0
    rc = call(path, fn_type(each), _NOTE_FN, None)
    if err:
        raise err[0]
    return rc, out


def _names(files):
    """None (every file) or a sequence of names, as the C's array and count."""
    if files is None:
        return None, 0
    raw = [f.encode("utf-8") for f in files]
    return (_P * max(1, len(raw)))(*raw), len(raw)


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

    def pmem_read(path):
        cells = (c.c_int32 * 256)()
        _lib().moy_pmem_read(_path(path), cells)
        return list(cells)

    def pmem_save(path, cells):
        """The saver's verb (native/moy_store/moy_save.h) with no saver task,
        as the threadless tiers have it: the record written at once."""
        buf = (c.c_int32 * 256)()
        for i, v in enumerate(cells[:256]):
            buf[i] = ((int(v) + 0x80000000) & 0xFFFFFFFF) - 0x80000000
        _ok(_lib().moy_save_pmem(_path(path), buf))

    def save_wait(path=None):
        """Nothing is ever in flight here: pmem_save wrote it."""

    def pmem_tidy():
        """One step of the pmem log's quiet work (native/moy_store/moy_plog.h):
        0 here, where no volume has a log."""
        return _lib().moy_pmem_tidy()

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

    def removed_load(root):
        lib, buf = _lib(), _Buf()
        return _text(lib, lib.moy_removed_load(_path(root), c.byref(buf)), buf)

    def removed_note(root, folder):
        rc = _lib().moy_removed_note(_path(root), folder.encode())
        if rc == _NONE:
            return False
        _ok(rc)
        return True

    def removed_drop(root, folder):
        rc = _lib().moy_removed_drop(_path(root), folder.encode())
        if rc == _NONE:
            return False
        _ok(rc)
        return True

    def catalogue(root):
        r = os.fspath(root)
        rc, out = _walk_cat(_lib().moy_cat_scan, _path(r),
                            lambda e: _entry(r + "/" + _str(e.folder, e.folder_n), e),
                            _CATFN)
        if rc == _NONE:
            return None
        _ok(rc)
        return out

    def entry(path):
        p = os.fspath(path)
        rc, out = _walk_cat(_lib().moy_cat_entry, _path(p), lambda e: _entry(p, e), _CATFN)
        if rc and rc != _NONE:
            _raise(rc)
        return out[0] if out else None

    def load(path):
        p = os.fspath(path)
        rc, out = _walk_cat(_lib().moy_cat_load, _path(p), lambda ct: _whole(p, ct), _LOADFN)
        if rc and rc != _NONE:
            _raise(rc)
        return out[0] if out else None

    def copy_files(src, dst, main):
        _ok(_lib().moy_cat_copy(_path(src), _path(dst), main.encode()))

    def folder(title, ns):
        t = title.encode("utf-8")
        n = None if ns is None else ns.encode("utf-8")
        size = _lib().moy_cat_folder(t, len(t), n, None, 0)
        out = c.create_string_buffer(size + 1)
        _lib().moy_cat_folder(t, len(t), n, out, size + 1)
        return out.value.decode("utf-8")

    def seed(root, roster, present, progress=None, ns=None):
        lib, wrote = _pack_lib(), []
        total = len(roster)
        for i, (title, version, blob) in enumerate(roster):
            if progress is not None:
                try:
                    progress(i, total, title)
                except Exception:  # noqa: BLE001 -- a hook that raises is dropped
                    progress = None
            f = folder(title, ns)
            if f in present and int(version) <= present[f]:
                continue
            raw, text = bytes(blob), _Buf()
            rc = lib.moy_seed_inflate(raw, len(raw), c.byref(text))
            if rc == 0:
                try:
                    rc = lib.moy_seed_write(_path(root), f.encode("utf-8"),
                                            c.cast(text.p, _P), text.n)
                finally:
                    lib.moy_buf_free(c.byref(text))
                rc = -rc if rc < 0 else (-1 if rc == 1 else 0)
            if rc == -1:
                wrote.append(f)
            elif rc:
                _raise(rc)
        return wrote

    def _scanned(text):
        b = text.encode("utf-8")
        buf = c.create_string_buffer(b, len(b))
        lo = c.addressof(buf)
        hi = lo + len(b)
        s = _lib().moy_json_ws(lo, hi)
        e = _lib().moy_json_value(s, hi, 1)
        if not e or _lib().moy_json_ws(e, hi) != hi:
            raise ValueError("syntax error in JSON")
        return buf, s, e

    def canon(text):
        buf, s, e = _scanned(text)
        k = _lib().moy_json_canon(s, e, None, 0)
        if k == c.c_size_t(-1).value:
            raise ValueError("JSON nested too deep")
        out = c.create_string_buffer(k + 1)
        _lib().moy_json_canon(s, e, out, k)
        return out.raw[:k].decode("utf-8")

    def loads(text):
        buf, s, e = _scanned(text)
        return json.loads(c.string_at(s, e - s).decode("utf-8"))

    def journal_append(cart, file, data, grad, ops, ts):
        b = _data(data)
        o = None if ops is None else _data(ops)
        seq = c.c_uint32()
        _ok(_lib().moy_journal_append(_path(cart), file.encode(), b, len(b),
                                      -1 if grad is None else (1 if int(grad) else 0),
                                      o, len(o or b""), int(ts), c.byref(seq)))
        return seq.value or None

    def _walk(fn, cart, files):
        arr, n = _names(files)
        out = c.create_string_buffer(256)
        rc = fn(_path(cart), arr, n, out, len(out))
        if rc < 0:
            _raise(-rc)
        return out.value.decode() if rc else None

    def journal_undo(cart, files):
        return _walk(_lib().moy_journal_undo, cart, files)

    def journal_redo(cart, files):
        return _walk(_lib().moy_journal_redo, cart, files)

    def journal_can(cart, redo, files):
        arr, n = _names(files)
        which = 2 if redo == 2 else (1 if redo else 0)
        ok = _lib().moy_journal_can(_path(cart), which, arr, n)
        return ok if which == 2 else bool(ok)

    def journal_compact(cart):
        rc = _lib().moy_journal_compact(_path(cart))
        if rc < 0:
            _raise(-rc)
        return rc

    def journal_list(cart, file):
        out = []

        def ent(_ctx, _seq, line, n):
            out.append(json.loads(c.string_at(line, n).decode("utf-8")))
            return 0
        _ok(_lib().moy_journal_list(_path(cart), None if file is None else file.encode(),
                                    _JENT(ent), None))
        return out

    def journal_snap(cart, seq):
        lib, buf = _lib(), _Buf()
        return _text(lib, lib.moy_journal_snap(_path(cart), int(seq), c.byref(buf)), buf)

    def journal_restore(cart, seq, ts):
        out = c.c_uint32()
        rc = _lib().moy_journal_restore(_path(cart), int(seq), int(ts), c.byref(out))
        if rc == _NONE:
            return None
        _ok(rc)
        return out.value or None

    def graduate(cart, value):
        return bool(_lib().moy_journal_graduate(_path(cart), 1 if value else 0))

    for name, v in list(locals().items()):
        if callable(v) and name not in ("m", "roots"):
            setattr(m, name, v)
    return m
