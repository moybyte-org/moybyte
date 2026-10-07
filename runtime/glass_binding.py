# Map (grep -n a name to jump there):
#   Buf                          a BUF row and its view (buf() makes one)
#   owner                        an OWNER row: a lifetime that holds loans
#   Canvas                       a CANVAS row: pixels, draw state, colour table
#   surface                      the surface table's verbs
#   DsiCompositor                the Waveshare's present, over a dsi and a ppa
#   RotatedCompositor            the Guition P4's rotated present
#   rotate_rect                  a landscape rect -> the portrait rect it lands on
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
_SOURCES = ("moy_buf.h", "moy_canvas.h", "moy_surface.h", "moy_present.h",
            "moy_glass.c", "moy_present_banded.c", "moy_present_dsi.c", "moy_present_rot.c", "moy_htab.h", "moy_htab.c")

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


# -- present: the DSI compositor (native/moy_glass/moy_present.h) ----------------
#
# The same C state machine the boards' moy_glass.DsiCompositor drives, with its
# transport the two modules passed in (a test's moy_dsi and moy_ppa doubles):
# the face below is the MP binding's, name for name.

_ticks_us = None    # a test's clock; None reads time.perf_counter_ns

# overlap_stats()'s fields, in order, on every DSI compositor: the PERF line
# labels both P4s' tuples with them. A slot a path does not have is None.
OVERLAP_FIELDS = ("deferred", "obsolete", "fences", "fence_us",
                  "game_n", "game_us", "timeouts")


def _now_us():
    if _ticks_us is not None:
        return _ticks_us() & 0xFFFFFFFF
    import time
    return (time.perf_counter_ns() // 1000) & 0xFFFFFFFF


_SHOW = ctypes.CFUNCTYPE(None, _P, ctypes.c_int)
_VOID = ctypes.CFUNCTYPE(None, _P)
_BOOL = ctypes.CFUNCTYPE(ctypes.c_bool, _P)
_TICKS = ctypes.CFUNCTYPE(ctypes.c_uint32, _P)


class _DsiOps(ctypes.Structure):
    _fields_ = [("show", _SHOW), ("msync", _VOID), ("ppa_sync", _VOID),
                ("ppa_done", _BOOL), ("ticks_us", _TICKS), ("ctx", _P)]


class _Dsi(ctypes.Structure):
    _fields_ = ([("ops", ctypes.POINTER(_DsiOps)), ("nfbs", ctypes.c_uint8),
                 ("back", ctypes.c_uint8), ("composite_pending", ctypes.c_bool),
                 ("pending", ctypes.c_int8), ("npend", ctypes.c_uint8),
                 ("nbusy", ctypes.c_uint8), ("pend", ctypes.c_uint8 * 4),
                 ("kind", ctypes.c_uint8 * 4), ("busy", ctypes.c_uint8 * 4)]
                + [(n, ctypes.c_uint32) for n in ("deferred", "obsolete", "fences",
                                                  "fence_us", "game_n", "game_us")])


def _dsi_lib():
    lib = _lib()
    if not getattr(lib, "_dsi_sigs", False):
        lib.moy_dsi_init.argtypes = [ctypes.POINTER(_Dsi), ctypes.POINTER(_DsiOps),
                                     ctypes.c_int]
        lib.moy_dsi_present.argtypes = [ctypes.POINTER(_Dsi), ctypes.c_bool]
        lib.moy_dsi_present_pending.argtypes = [ctypes.POINTER(_Dsi)]
        lib.moy_dsi_fence.argtypes = [ctypes.POINTER(_Dsi)]
        for f in (lib.moy_dsi_init, lib.moy_dsi_present,
                  lib.moy_dsi_present_pending, lib.moy_dsi_fence):
            f.restype = None
        lib._dsi_sigs = True
    return lib


class DsiCompositor:
    """moy_glass.DsiCompositor on CPython: the backend contract plus the
    deferred present, over `dsi` and `ppa`."""

    def __init__(self, dsi, ppa=None):
        self._dsi = dsi
        self._ppa = ppa
        self._stamp_pending = None
        dsi.backlight(False)        # dark until the first composed frame
        dsi.init()
        self._w = dsi.WIDTH
        self._h = dsi.HEIGHT
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None
        n = dsi.nfbs() if hasattr(dsi, "nfbs") else 1
        self._fbs = [dsi.fb(i) for i in range(n)] if n > 1 else [dsi.fb()]
        if self._gfx is not None:
            for f in self._fbs:
                self._gfx.fill(f, self._w * self._h, 0)

        def show(_ctx, i):
            dsi.show(i)

        def msync(_ctx):
            dsi.flush()

        def ppa_sync(_ctx):
            if ppa is not None:
                ppa.sync()

        def ppa_done(_ctx):
            done = getattr(ppa, "done", None) if ppa is not None else None
            return True if done is None else bool(done())

        self._cb = (_SHOW(show), _VOID(msync), _VOID(ppa_sync), _BOOL(ppa_done),
                    _TICKS(lambda _ctx: _now_us()))
        self._ops = _DsiOps(*self._cb, None)
        self._d = _Dsi()
        _dsi_lib().moy_dsi_init(ctypes.byref(self._d), ctypes.byref(self._ops), n)

    @property
    def _composite_pending(self):
        return self._d.composite_pending

    @_composite_pending.setter
    def _composite_pending(self, on):
        self._d.composite_pending = bool(on)

    @property
    def _back(self):
        return self._d.back

    @property
    def _pending(self):
        return None if self._d.pending < 0 else self._d.pending

    @property
    def _pend3(self):
        d = self._d
        return [(d.pend[i], "stamp" if d.kind[i] else "game") for i in range(d.npend)]

    @property
    def _busy3(self):
        d = self._d
        return [d.busy[i] for i in range(d.nbusy)]

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._fbs[self._d.back]

    back_buffer = framebuffer

    def gfx(self):
        return self._gfx

    def flush(self):
        kicked = False
        st = self._stamp_pending
        if st is not None:
            self._stamp_pending = None
            try:
                self._ppa.blit_async(*(tuple(st[:8]) + (1,)))
                kicked = True
            except Exception:  # noqa: BLE001 -- refusal -> draw it on the CPU
                print("Moybyte P4 stamp kick failed -> CPU")
                if self._gfx is not None:
                    try:
                        self._gfx.blit565(*(tuple(st[:8]) + (-1,)))
                    except Exception:  # noqa: BLE001 -- worst case: one stale frame
                        pass
        _dsi_lib().moy_dsi_present(ctypes.byref(self._d), kicked)

    def present_pending(self):
        _dsi_lib().moy_dsi_present_pending(ctypes.byref(self._d))

    def sync(self):
        _dsi_lib().moy_dsi_fence(ctypes.byref(self._d))

    def snap_fence(self):
        self._ppa.snap_wait()

    def frame_fence(self):
        self._ppa.snap_wait()
        self._ppa.sync()

    def overlap_stats(self):
        try:
            timeouts = self._ppa.stats()[2]
        except Exception:  # noqa: BLE001 -- no PPA
            timeouts = 0
        d = self._d
        return (d.deferred, d.obsolete, d.fences, d.fence_us, d.game_n,
                d.game_us, timeouts)

    def underruns(self):
        try:
            return self._dsi.underruns()
        except Exception:  # noqa: BLE001
            return None

    def set_backlight(self, on=True):
        self._dsi.backlight(bool(on))


# -- present: the rotated DSI compositor (native/moy_glass/moy_present.h) --------

class _Rect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int16), ("y", ctypes.c_int16),
                ("w", ctypes.c_int16), ("h", ctypes.c_int16)]


_I32 = ctypes.c_int32
_ROT_SHOW = ctypes.CFUNCTYPE(None, _P, ctypes.c_int)
_ROT_REFRESH = ctypes.CFUNCTYPE(_I32, _P)
_ROT_WAIT = ctypes.CFUNCTYPE(None, _P, ctypes.c_int)
_ROT_ROTATE = ctypes.CFUNCTYPE(ctypes.c_int, _P, ctypes.c_bool, ctypes.c_bool,
                               *([ctypes.c_int] * 13))
_ROT_SCALE = ctypes.CFUNCTYPE(ctypes.c_int, _P, ctypes.c_bool, ctypes.c_bool,
                              *([ctypes.c_int] * 10), ctypes.POINTER(ctypes.c_int16))
_ROT_BOUNCE = ctypes.CFUNCTYPE(ctypes.c_int, _P, *([ctypes.c_int] * 13), ctypes.c_bool)
_ROT_SCRATCH = ctypes.CFUNCTYPE(None, _P, _Z)


class _RotOps(ctypes.Structure):
    _fields_ = [("show", _ROT_SHOW), ("refreshes", _ROT_REFRESH), ("ppa_sync", _VOID),
                ("ppa_done", _BOOL), ("ppa_wait", _ROT_WAIT), ("snap_wait", _VOID),
                ("rotate", _ROT_ROTATE), ("rotate_scale", _ROT_SCALE),
                ("bounce", _ROT_BOUNCE), ("paint", _VOID), ("quiet", _BOOL),
                ("scratch", _ROT_SCRATCH), ("ticks_us", _TICKS), ("ctx", _P)]


class _RotGame(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_int) for n in ("sw", "sh", "ox", "oy", "scale")]
                + [("direct", ctypes.c_bool), ("frame", ctypes.c_bool)]
                + [(n, ctypes.c_int16) for n in ("bx", "by", "bw", "bh")]
                + [("rows", ctypes.c_int), ("npatch", ctypes.c_int),
                   ("patches", ctypes.c_int16 * 24)])


class _RotStamp(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int) for n in ("dw", "dh", "x", "y", "sw", "sh")]


_ROT_METERS = ("vsync_waits", "full_n", "full_us", "rect_n", "rect_us", "copies",
               "grown", "dmg_n", "dmg_rects", "dmg_declined", "bounced", "stamp_n",
               "refused", "def_n", "pres_n", "late_n", "fences", "fence_us",
               "wait_n", "wait_us")


class _Rot(ctypes.Structure):
    _fields_ = ([("ops", ctypes.POINTER(_RotOps))]
                + [(n, ctypes.c_int) for n in ("pw", "ph", "w", "h", "angle", "strip_h")]
                + [("seq", _I32 * 3), ("rseq", _I32 * 3), ("shows", _I32)]
                + [(n, ctypes.c_int) for n in ("front", "back", "pi", "pending", "keep")]
                + [(n, ctypes.c_bool) for n in ("async_", "bounce", "has_game",
                                                "has_stamp")]
                + [("bounce_min_px", _I32)]
                + [("stale_full", ctypes.c_bool * 3), ("nstale", ctypes.c_uint8 * 3),
                   ("stale", (_Rect * 8) * 3), ("ndamage", ctypes.c_int),
                   ("damage", _Rect * 32), ("game", _RotGame), ("stamp", _RotStamp)]
                + [(n, ctypes.c_uint32) for n in _ROT_METERS])


def _rot_lib():
    lib = _lib()
    if not getattr(lib, "_rot_sigs", False):
        R = ctypes.POINTER(_Rot)
        for name, args in (("moy_rot_init", [R, ctypes.POINTER(_RotOps), ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.c_bool,
                                              ctypes.c_bool]),
                           ("moy_rot_set_angle", [R, ctypes.c_int]),
                           ("moy_rot_note_damage", [R] + [ctypes.c_int] * 4),
                           ("moy_rot_flush", [R]), ("moy_rot_present_pending", [R]),
                           ("moy_rot_fence", [R])):
            f = getattr(lib, name)
            f.argtypes = args
            f.restype = None
        lib.moy_rot_on_glass.argtypes = [R]
        lib.moy_rot_on_glass.restype = ctypes.c_int
        for name in ("moy_rot_rect", "moy_unrot_rect"):
            f = getattr(lib, name)
            f.argtypes = [ctypes.c_int] * 7
            f.restype = _Rect
        lib._rot_sigs = True
    return lib


def rotate_rect(x, y, w, h, angle, lw, lh):
    if angle not in (90, 270):
        raise ValueError("angle 90 or 270")
    r = _rot_lib().moy_rot_rect(x, y, w, h, angle, lw, lh)
    return (r.x, r.y, r.w, r.h)


def unrotate_rect(px, py, pw, ph, angle, lw, lh):
    if angle not in (90, 270):
        raise ValueError("angle 90 or 270")
    r = _rot_lib().moy_unrot_rect(px, py, pw, ph, angle, lw, lh)
    return (r.x, r.y, r.w, r.h)


class RotatedCompositor:
    """moy_glass.RotatedCompositor on CPython: the landscape desk over a
    portrait `dsi`, rotated on `ppa`."""

    retained_frames = 2
    rotated = True
    STALE_LIMIT = 6         # more distinct stale rects than this: a full rotate

    def __init__(self, dsi, ppa, angle=90):
        rotate_rect(0, 0, 1, 1, angle, 2, 2)
        dsi.backlight(False)
        dsi.init()
        if not ppa.init():
            raise OSError("moy_ppa init failed: a portrait panel needs the rotate")
        self._dsi = dsi
        self._ppa = ppa
        self._pw = dsi.WIDTH
        self._ph = dsi.HEIGHT
        self._w = self._ph
        self._h = self._pw
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None
        self._fbs = [dsi.fb(0), dsi.fb(1), dsi.fb(2)]
        self._held = [buf(self._w * self._h * 2, ROLE_PAINT) for _ in range(2)]
        self._paints = [b.view for b in self._held]
        if self._gfx is not None:
            for f in self._fbs:
                self._gfx.fill(f, self._pw * self._ph, 0)
            for f in self._paints:
                self._gfx.fill(f, self._w * self._h, 0)
        self._scratch = None
        self._scratch_n = 0
        self._game = None
        self._stamp = None
        self._composite_pending = False   # the Waveshare's flag; inert here
        bufs = self._bufs

        def rotate(_c, nb, wb, dst, dw, dh, dx, dy, src, sw, sh, sx, sy, w, h, angle):
            try:
                ppa.rotate(bufs(dst), dw, dh, dx, dy, bufs(src), sw, sh, sx, sy, w, h,
                           angle, nb, wb)
            except OSError:
                if not nb:
                    raise
                return -1
            return 0

        def rotate_scale(_c, nb, wb, dst, dw, dh, dx, dy, src, sw, sh, scale, angle,
                         blk):
            extra = (blk[0], blk[1], blk[2], blk[3]) if blk else ()
            try:
                ppa.rotate_scale(bufs(dst), dw, dh, dx, dy, bufs(src), sw, sh, scale,
                                 angle, nb, wb, *extra)
            except OSError:
                if not nb:
                    raise
                return -1
            return 0

        def bounce(_c, fb, pw, ph, fx, fy, paint, w, h, sx, sy, bw, bh, angle, nb):
            return ppa.rotate_bounce(bufs(fb), pw, ph, fx, fy, bufs(paint), w, h, sx,
                                     sy, bw, bh, angle, nb)

        def refreshes(_c):
            return dsi.refreshes()

        def scratch(_c, n):
            if self._scratch is None or self._scratch_n < n:
                b = buf(n, ROLE_PAINT)
                self._held.append(b)
                self._scratch = b.view
                self._scratch_n = n

        rb = getattr(ppa, "rotate_bounce", None)
        self._cb = dict(
            show=_ROT_SHOW(lambda _c, n: dsi.show(n)),
            refreshes=(_ROT_REFRESH(refreshes) if hasattr(dsi, "refreshes")
                       else ctypes.cast(None, _ROT_REFRESH)),
            ppa_sync=_VOID(lambda _c: ppa.sync()),
            ppa_done=_BOOL(lambda _c: bool(ppa.done())),
            ppa_wait=_ROT_WAIT(lambda _c, k: ppa.wait(k)),
            snap_wait=_VOID(lambda _c: ppa.snap_wait()),
            rotate=_ROT_ROTATE(rotate), rotate_scale=_ROT_SCALE(rotate_scale),
            bounce=(_ROT_BOUNCE(bounce) if rb is not None
                    else ctypes.cast(None, _ROT_BOUNCE)),
            paint=_VOID(lambda _c: self._game[0]()),
            quiet=_BOOL(lambda _c: bool(self._game[1]())),
            scratch=_ROT_SCRATCH(scratch),
            ticks_us=_TICKS(lambda _c: _now_us()))
        self._ops = _RotOps(**self._cb)
        self._r = _Rot()
        _rot_lib().moy_rot_init(ctypes.byref(self._r), ctypes.byref(self._ops),
                                self._pw, self._ph, angle, hasattr(ppa, "wait"),
                                rb is not None)

    def _bufs(self, i):
        if i < 3:
            return self._fbs[i]
        if i < 5:
            return self._paints[i - 3]
        if i == MOY_ROT_SCRATCH:
            return self._scratch
        if i == MOY_ROT_GAME:
            return self._game[2]
        if i == MOY_ROT_PIC:
            return self._game[3]
        return self._stamp[0 if i == MOY_ROT_STAMP_DST else 1]

    # -- the contract -------------------------------------------------------
    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._paints[self._r.pi]

    back_buffer = framebuffer

    def gfx(self):
        return self._gfx

    @property
    def angle(self):
        return self._r.angle

    @property
    def BOUNCE_MIN_PX(self):
        return self._r.bounce_min_px

    @BOUNCE_MIN_PX.setter
    def BOUNCE_MIN_PX(self, n):
        self._r.bounce_min_px = int(n)

    @property
    def strip_h(self):
        return self._r.strip_h

    @strip_h.setter
    def strip_h(self, h):
        self._r.strip_h = int(h)

    def set_angle(self, angle):
        rotate_rect(0, 0, 1, 1, angle, self._w, self._h)
        _rot_lib().moy_rot_set_angle(ctypes.byref(self._r), angle)

    def mark_game(self, src, sw, sh, ox, oy, scale, paint, quiet, direct, frame=None):
        g = self._r.game
        g.sw, g.sh, g.ox, g.oy, g.scale = int(sw), int(sh), int(ox), int(oy), int(scale)
        g.direct = bool(direct)
        g.frame = frame is not None
        pic = None
        if frame is not None:
            bx, by, bw, bh, pic, rows, pr, npatch = frame
            g.bx, g.by, g.bw, g.bh, g.rows = bx, by, bw, bh, rows
            g.npatch = min(int(npatch), 4)
            for k in range(6 * g.npatch):
                g.patches[k] = pr[k]
        self._game = (paint, quiet, src, pic)
        self._r.has_game = True

    @property
    def _stamp_pending(self):
        return self._stamp_tuple if self._r.has_stamp else None

    @_stamp_pending.setter
    def _stamp_pending(self, st):
        if st is None:
            self._r.has_stamp = False
            return
        dst, dw, dh, x, y, src, sw, sh = st
        self._stamp_tuple = st
        self._stamp = (dst, src)
        s = self._r.stamp
        s.dw, s.dh, s.x, s.y, s.sw, s.sh = dw, dh, x, y, sw, sh
        self._r.has_stamp = True

    def note_damage(self, x, y, w, h):
        _rot_lib().moy_rot_note_damage(ctypes.byref(self._r), int(x), int(y), int(w),
                                       int(h))

    def flush(self):
        _rot_lib().moy_rot_flush(ctypes.byref(self._r))
        self._game = self._game if self._r.has_game else None

    def present_pending(self):
        _rot_lib().moy_rot_present_pending(ctypes.byref(self._r))

    def sync(self):
        _rot_lib().moy_rot_fence(ctypes.byref(self._r))

    def snap_fence(self):
        self._ppa.snap_wait()

    def frame_fence(self):
        if self._r.has_game and self._r.game.frame:
            self._r.has_game = False
        self._ppa.snap_wait()
        self._ppa.sync()

    # -- meters -----------------------------------------------------------------
    def _m(self, *names):
        return tuple(getattr(self._r, n) for n in names)

    def async_stats(self):
        return self._m("def_n", "pres_n", "late_n", "wait_us", "stamp_n", "refused",
                       "bounced")

    def rotate_stats(self):
        return self._m("full_n", "full_us", "rect_n", "rect_us", "copies")

    def damage_stats(self):
        return self._m("dmg_n", "dmg_rects", "dmg_declined", "grown")

    def overlap_stats(self):
        try:
            timeouts = self._ppa.stats()[2]
        except Exception:  # noqa: BLE001
            timeouts = 0
        r = self._r
        return (r.def_n, None, r.fences, r.fence_us, r.wait_n, r.wait_us, timeouts)

    def underruns(self):
        try:
            return self._dsi.underruns()
        except Exception:  # noqa: BLE001
            return None

    def set_backlight(self, on=True):
        self._dsi.backlight(bool(on))

    # -- what the tests read ---------------------------------------------------
    @property
    def _pending(self):
        return None if self._r.pending < 0 else self._r.pending

    @property
    def _front(self):
        return self._r.front

    @property
    def _keep(self):
        return self._r.keep

    @property
    def _stale(self):
        r = self._r
        return [None if r.stale_full[i] else
                [(q.x, q.y, q.w, q.h) for q in r.stale[i][:r.nstale[i]]]
                for i in range(3)]

    @property
    def _bounced(self):
        return self._r.bounced

    @property
    def _bounce(self):
        return True if self._r.bounce else None

    @property
    def _async(self):
        return self._r.async_

    @_async.setter
    def _async(self, on):
        self._r.async_ = bool(on)

    @property
    def _vsync_waits(self):
        return self._r.vsync_waits

    def _on_glass(self):
        return _rot_lib().moy_rot_on_glass(ctypes.byref(self._r))


MOY_ROT_SCRATCH, MOY_ROT_GAME, MOY_ROT_PIC, MOY_ROT_STAMP_DST = 5, 6, 7, 8
