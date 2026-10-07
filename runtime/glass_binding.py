"""The glass (native/moy_glass) on CPython, by ctypes: the module `moy_glass`
as the boards and the browser import it, name for name, over the same C built
for the host (`moy_glass_host.c` holds its allocators).

`host_canvas.install()` puts this module where `import moy_glass` finds it.
What it adds to the C is what ctypes cannot do by itself: a Buf's and a
Canvas's lifetime ends with the Python object (`__del__`, which CPython runs
the moment the last reference goes), and a view of C memory is a plain
writable memoryview made by `PyMemoryView_FromMemory`, so the rest of the host
sees the same buffer type a board's moy_alloc view is.
"""

from __future__ import annotations

import ctypes
import os

from . import native_build

_HERE = os.path.dirname(os.path.abspath(__file__))
_GLASS = os.path.join(native_build.ROOT, "native", "moy_glass")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")
_SHIM = os.path.join(_GLASS, "moy_glass_host.c")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_glass")
_SOURCES = ("moy_buf.h", "moy_canvas.h", "moy_surface.h", "moy_glass.c",
            "moy_htab.h", "moy_htab.c")

ROLE_LAYER, ROLE_BAKE, ROLE_SCRATCH, ROLE_CACHE, ROLE_PAINT, ROLE_POOL = range(1, 7)
ORIGIN_HEAP, ORIGIN_POOL, ORIGIN_ALLOC = 0, 1, 2
CLASS_KERNEL, CLASS_CART = 0, 1
ROWS = 256
ST_LEN = 14
POOL_BYTES = 4 * 1024 * 1024
OK, STALE, FULL, NOMEM, BAD = range(5)

_U32 = ctypes.c_uint32
_I = ctypes.c_int
_P = ctypes.c_void_p
_Z = ctypes.c_size_t
_PU32 = ctypes.POINTER(ctypes.c_uint32)


class _Stats(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "rows", "peak", "pool_rows", "pool_bytes", "pool_bound", "heap_rows",
        "heap_bytes", "kernel_bytes", "cart_bytes", "owners", "evictions",
        "px_free", "px_largest")]


_SIGS = (
    ("moy_glass_host_init", [_U32], _I),
    ("moy_glass_host_live_bytes", [], _Z),
    ("moy_glass_host_px", [_U32], _P),
    ("moy_glass_host_row", [_U32, _PU32], _I),
    ("moy_glass_host_canvas_row", [_U32], _P),
    ("moy_glass_host_canvas_state_offset", [], _Z),
    ("moy_glass_host_canvas_pal_offset", [], _Z),
    ("moy_glass_host_surface_row", [_U32, ctypes.POINTER(ctypes.c_int32)], _I),
    ("moy_glass_host_surface_place", [_U32, _I, _I, _I, _I, _I], _I),
    ("moy_glass_set_pool_bound", [_U32], None),
    ("moy_glass_evict", [], None),
    ("moy_glass_stats", [ctypes.POINTER(_Stats)], None),
    ("moy_buf_new", [_PU32, _Z, ctypes.c_uint8, _U32], _I),
    ("moy_buf_heap", [_PU32, _Z, ctypes.c_uint8, _U32], _I),
    ("moy_buf_set_holder", [_U32, _U32], _I),
    ("moy_buf_release", [_U32], _I),
    ("moy_buf_slots", [], _U32),
    ("moy_buf_at", [_U32], _U32),
    ("moy_owner_new", [_PU32, ctypes.c_char_p, ctypes.c_uint8], _I),
    ("moy_owner_reclaim", [_U32, _U32], _I),
    ("moy_owner_end", [_U32], _I),
    ("moy_canvas_new", [_PU32, ctypes.c_uint16, ctypes.c_uint16, _U32, _U32], _I),
    ("moy_canvas_point", [_U32, _P, _U32, _U32], _I),
    ("moy_canvas_release", [_U32], _I),
    ("moy_canvas_count", [], _U32),
    ("moy_surface_get", [_PU32, ctypes.c_char_p, ctypes.c_uint8], _I),
    ("moy_surface_find", [ctypes.c_char_p], _U32),
    ("moy_surface_touch", [_U32], None),
    ("moy_surface_move", [_U32], None),
    ("moy_surface_animating", [_U32, ctypes.c_bool], None),
    ("moy_surface_epoch", [], None),
    ("moy_surface_mint", [], _U32),
    ("moy_surface_content_gen", [_U32], _U32),
    ("moy_surface_drop", [_U32], _I),
    ("moy_surface_sync", [ctypes.POINTER(ctypes.c_char_p), _Z, ctypes.c_char_p], None),
    ("moy_surface_kernel_epoch", [], _U32),
    ("moy_surface_kernel_bump", [], None),
)

_LIB = [None]

_FROM_MEMORY = ctypes.pythonapi.PyMemoryView_FromMemory
_FROM_MEMORY.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
_FROM_MEMORY.restype = ctypes.py_object
_PyBUF_WRITE = 0x200


def build(verbose=False):
    return native_build.build("moy_glass", _SHIM, _SOURCES, _CACHE,
                              libmoy_dir=(_GLASS, _SPINE), verbose=verbose)


def _lib():
    if _LIB[0] is None:
        path = build()
        if path is None:
            raise ImportError("moy_glass: no C compiler for the host build")
        d = ctypes.CDLL(path)
        for name, args, res in _SIGS:
            fn = getattr(d, name)
            fn.argtypes = args
            fn.restype = res
        if d.moy_glass_host_init(POOL_BYTES) != OK:
            raise MemoryError("glass: no tables")
        _LIB[0] = d
    return _LIB[0]


def _view(addr, n):
    return _FROM_MEMORY(addr, n, _PyBUF_WRITE)


def _raise(rc, what):
    if rc == STALE:
        raise ValueError("stale %s handle" % what)
    if rc == FULL:
        raise MemoryError("the glass's %s table is full" % what)
    if rc == BAD:
        raise ValueError("glass: bad argument")
    raise MemoryError


def _collect():
    import gc
    gc.collect()


class Buf:
    """A BUF row and its bytes; `view` is their writable memoryview."""

    __slots__ = ("h", "view", "_heap", "__weakref__")

    def __init__(self, h, view, heap=None):
        self.h = h
        self.view = view
        self._heap = heap

    def _row(self):
        if not self.h:
            return None
        out = (ctypes.c_uint32 * 6)()
        if _lib().moy_glass_host_row(self.h, out) != OK:
            self.h = 0
            return None
        return out

    @property
    def live(self):
        return self._row() is not None

    @property
    def nbytes(self):
        r = self._row()
        return r[0] if r is not None else 0

    @property
    def role(self):
        r = self._row()
        return r[1] if r is not None else -1

    @property
    def origin(self):
        r = self._row()
        return r[2] if r is not None else -1

    def release(self):
        h = self.h
        if h:
            self.h = 0
            lib = _LIB[0]
            if lib:
                lib.moy_buf_release(h)
        self._heap = None

    def __del__(self):
        try:
            self.release()
        except Exception:  # noqa: BLE001 -- interpreter teardown
            pass


def buf(nbytes, role, owner=0):
    lib = _lib()
    n = int(nbytes)
    if n <= 0:
        raise ValueError("size must be positive")
    h = ctypes.c_uint32()
    rc = lib.moy_buf_new(ctypes.byref(h), n, int(role), int(owner))
    if rc == FULL:
        _collect()
        rc = lib.moy_buf_new(ctypes.byref(h), n, int(role), int(owner))
    if rc == OK:
        return Buf(h.value, _view(lib.moy_glass_host_px(h.value), n))
    if rc != NOMEM:
        _raise(rc, "buffer")
    ba = bytearray(n)
    rc = lib.moy_buf_heap(ctypes.byref(h), n, int(role), int(owner))
    if rc != OK:
        _raise(rc, "buffer")
    return Buf(h.value, memoryview(ba), ba)


def owner(tag, cls=CLASS_CART):
    h = ctypes.c_uint32()
    rc = _lib().moy_owner_new(ctypes.byref(h), str(tag).encode(), int(cls))
    if rc != OK:
        _raise(rc, "owner")
    return h.value


def reclaim(h, roles=0):
    rc = _lib().moy_owner_reclaim(int(h), int(roles))
    if rc != OK:
        _raise(rc, "owner")


def owner_end(h):
    rc = _lib().moy_owner_end(int(h))
    if rc != OK:
        _raise(rc, "owner")


def row(h):
    out = (ctypes.c_uint32 * 6)()
    if _lib().moy_glass_host_row(int(h), out) != OK:
        _raise(STALE, "buffer")
    return tuple(out)


def set_holder(h, holder):
    if _lib().moy_buf_set_holder(int(h), int(holder)) != OK:
        _raise(STALE, "buffer")


def rows(own=None):
    lib = _lib()
    out = []
    for s in range(lib.moy_buf_slots()):
        h = lib.moy_buf_at(s)
        if not h:
            continue
        r = row(h)
        pooled = r[1] == ROLE_POOL
        if own is None:
            keep = not pooled
        elif own < 0:
            keep = pooled
        else:
            keep = not pooled and r[3] == own
        if keep:
            out.append(h)
    return out


def stats():
    s = _Stats()
    lib = _lib()
    lib.moy_glass_stats(ctypes.byref(s))
    return tuple(getattr(s, n) for n, _ in _Stats._fields_) + (lib.moy_canvas_count(),)


def set_pool_bound(n):
    _lib().moy_glass_set_pool_bound(int(n))


def evict():
    _lib().moy_glass_evict()


def live_bytes():
    """The host's pixel bytes outstanding (tests: every release frees)."""
    return _lib().moy_glass_host_live_bytes()


class Canvas:
    """A CANVAS row: `state` and `pal` view its draw state and colour table."""

    __slots__ = ("h", "state", "pal", "_target", "_arr")

    def __init__(self, w, h, caps=0, own=0):
        lib = _lib()
        ch = ctypes.c_uint32()
        rc = lib.moy_canvas_new(ctypes.byref(ch), int(w), int(h), int(caps), int(own))
        if rc == FULL:
            _collect()
            rc = lib.moy_canvas_new(ctypes.byref(ch), int(w), int(h), int(caps),
                                    int(own))
        if rc != OK:
            _raise(rc, "canvas")
        self.h = ch.value
        base = lib.moy_glass_host_canvas_row(self.h)
        self.state = _view(base + lib.moy_glass_host_canvas_state_offset(),
                           4 * ST_LEN).cast("i")
        self.pal = _view(base + lib.moy_glass_host_canvas_pal_offset(), 128).cast("H")
        self._target = None
        self._arr = None

    def point(self, target, buf_h=0):
        arr = (ctypes.c_char * len(target)).from_buffer(target)
        if _lib().moy_canvas_point(self.h, ctypes.addressof(arr), len(target) // 2,
                                   int(buf_h)) != OK:
            _raise(STALE, "canvas")
        self._target = target
        self._arr = arr

    def release(self):
        h = self.h
        if h:
            self.h = 0
            lib = _LIB[0]
            if lib:
                lib.moy_canvas_release(h)
        self._arr = None
        self._target = None

    def __del__(self):
        try:
            self.release()
        except Exception:  # noqa: BLE001 -- interpreter teardown
            pass


# -- surfaces -----------------------------------------------------------------

def surface(sid, domain=0):
    h = ctypes.c_uint32()
    rc = _lib().moy_surface_get(ctypes.byref(h), sid.encode(), int(domain))
    if rc != OK:
        _raise(rc, "surface")
    return h.value


def surface_find(sid):
    return _lib().moy_surface_find(sid.encode())


def touch(h):
    _lib().moy_surface_touch(int(h))


def move(h):
    _lib().moy_surface_move(int(h))


def animating(h, on=None):
    lib = _lib()
    if on is not None:
        lib.moy_surface_animating(int(h), bool(on))
        return None
    out = (ctypes.c_int32 * 9)()
    return lib.moy_glass_host_surface_row(int(h), out) == OK and bool(out[8])


def epoch():
    _lib().moy_surface_epoch()


def content_gen(h):
    return _lib().moy_surface_content_gen(int(h))


def gens(h):
    out = (ctypes.c_int32 * 9)()
    if _lib().moy_glass_host_surface_row(int(h), out) != OK:
        _raise(STALE, "surface")
    return (out[0], out[1])


def mint():
    return _lib().moy_surface_mint()


def place(h, x, y, w, hh, z):
    rc = _lib().moy_glass_host_surface_place(int(h), int(x), int(y), int(w),
                                             int(hh), int(z))
    if rc < 0:
        _raise(STALE, "surface")
    return bool(rc)


def placement(h):
    out = (ctypes.c_int32 * 9)()
    if _lib().moy_glass_host_surface_row(int(h), out) != OK:
        _raise(STALE, "surface")
    return [out[2], out[3], out[7], out[6]]


def drop(h):
    _lib().moy_surface_drop(int(h))


def sync(alive, prefix="win:"):
    names = [s.encode() for s in alive]
    arr = (ctypes.c_char_p * max(1, len(names)))(*names)
    _lib().moy_surface_sync(arr, len(names), prefix.encode())


def kernel_epoch():
    return _lib().moy_surface_kernel_epoch()


def kernel_bump():
    _lib().moy_surface_kernel_bump()
