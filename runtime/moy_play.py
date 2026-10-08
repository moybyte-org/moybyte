"""The kernel's Player (native/moy_play) on CPython, by ctypes: the module
`moy_play` as the boards and the desktop MicroPython import it, name for name
(native/moy_play/modmoy_play.c has the list). A VM's `import moy_play` is the
native module, which always wins over this file; CPython has no such module,
so this is what it finds.

  Tick()           the tick model (native/moy_play/moy_tick.h): start,
                   steady_mode, uncap_mode, note_tick, plan, cycle, fits, and
                   its state as attributes
  census(path)     the VM-free rule over a cart folder's catalogue entry, as
                   the store's C reads it (moy_play_vm_free): (runtime,
                   vm_free, why), None when the folder is no cart. Host only.
  image_decode(t)  a `.moyimg` decoded by the C a VM-free run reads its
                   images with (native/moy_store/moy_img.c): (w, h, bytes) or
                   None. Host only; it needs the desktop MicroPython's tree,
                   whose inflater the boards compile.
  Files(cart)      a cart's written files by the C store
                   (native/moy_store/moy_files.c), with files_key,
                   files_path_of, files_folder and files_live: the parity
                   suite's view of it beside runtime/cart_files.py. Host only.
"""

from __future__ import annotations

import ctypes
import os

try:                                    # the host's runtime package
    from . import native_build
except ImportError:                     # loaded flat, runtime/ on sys.path
    import native_build

_PLAY = os.path.join(native_build.ROOT, "native", "moy_play")
_SPINE = os.path.join(native_build.ROOT, "native", "moy_spine")
_STORE = os.path.join(native_build.ROOT, "native", "moy_store")
_SHIM = os.path.join(_PLAY, "moy_play_host.c")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_play")
_SOURCES = ("moy_play.h", "moy_play_rule.c", "moy_rt.h", "moy_rt.c",
            "moy_tick.h", "moy_tick.c",
            "moy_cat.h", "moy_cat.c", "moy_load.h", "moy_arena.h", "moy_json.h",
            "moy_json.c", "moy_vol.h", "moy_vol.c", "moy_fs.h", "moy_fs.c",
            "moy_store_host.c")
_LIB = [None]

MAX_CATCHUP = 4
MAX_DIV = 4
EPS = 0.02
STEADY_S = 2.0
FREE_S = 0.25
LATE_CYCLE = 3
VERY_LATE = 0.8
STALL = 4
PROBE_K = 2
PROBE_LATE = 16
PROBE_BACK = 32
PROBE_MAX = 64
PROBE_DROP = 0.2
ALPHA = 0.125

_B, _U8, _U16, _U32 = ctypes.c_bool, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32
_R = None                               # the model's real: moy_tick_real_t

# moy_tick_t, field for field; "R" is the model's real.
_TICK = (("rate", ctypes.c_int), ("period", "R"), ("tick_ms", _U32),
         ("steady", _B), ("uncapped", _B), ("div", _U8), ("n", _U8),
         ("draw", _B), ("ticks", _U32), ("draws", _U32), ("misses", _U32),
         ("acc", "R"), ("phase", _U8), ("tick_cost", "R"), ("draw_frame", "R"),
         ("tick_frame", "R"), ("late", "R"), ("probing", _B), ("probe_from", _U8),
         ("probe_k", _U16), ("fail_cost", "R"), ("fresh_from", _U8),
         ("clean", _U16), ("late_wins", _U16), ("warm", _B), ("d_known", _B),
         ("t_known", _B), ("primed", _B), ("drew", _B), ("ticked", _B),
         ("cyc_ticks", _U32), ("cyc_misses", _U32), ("cyc_stall", _B),
         ("win_s", "R"), ("win_cycles", _U32), ("win_late", _U32),
         ("win_stalls", _U32))


def _tick_type(real):
    class _Tick(ctypes.Structure):
        _fields_ = [(n, real if t == "R" else t) for n, t in _TICK]
    return _Tick


def build(verbose=False, real="float"):
    """The host library; `real` "double" is the parity build, whose tick
    model runs in double precision to be held to a double reference."""
    flags = None if real == "float" else native_build.BASE_CFLAGS + ["-DMOY_TICK_REAL=" + real]
    return native_build.build("moy_play" if real == "float" else "moy_play_" + real,
                              _SHIM, _SOURCES, _CACHE, cflags=flags,
                              libmoy_dir=(_PLAY, _SPINE, _STORE), verbose=verbose)


def _lib(real="float"):
    key = 0 if real == "float" else 1
    while len(_LIB) <= key:
        _LIB.append(None)
    if _LIB[key] is None:
        path = build(real=real)
        if path is None:
            raise ImportError("moy_play: no C compiler for the host build")
        d = ctypes.CDLL(path)
        R = ctypes.c_float if real == "float" else ctypes.c_double
        d.tick_type = _tick_type(R)
        P = ctypes.POINTER(d.tick_type)
        for name, args, res in (
                ("moy_play_host_runtimes", [ctypes.c_int, ctypes.c_int], None),
                ("moy_play_census", [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t],
                 ctypes.c_int),
                ("moy_play_tick_size", [], ctypes.c_size_t),
                ("moy_tick_start", [P, ctypes.c_int, _B], None),
                ("moy_tick_mode", [P, _B, _B], None),
                ("moy_tick_plan", [P, R, ctypes.POINTER(_U8)], _B),
                ("moy_tick_note", [P, R], None),
                ("moy_tick_cycle", [P, ctypes.c_int], R),
                ("moy_tick_fits", [P, ctypes.c_int], _B)):
            f = getattr(d, name)
            f.argtypes = args
            f.restype = res
        if d.moy_play_tick_size() != ctypes.sizeof(d.tick_type):
            raise ImportError("moy_play: moy_tick_t's layout is not this file's")
        _LIB[key] = d
    return _LIB[key]


class Tick:
    """The tick model, one per run (moy_tick.h). `real` "double" is the
    parity build's (tests only)."""

    __slots__ = ("_d", "_t", "_n")

    def __init__(self, real="float"):
        d = self._d = _lib(real)
        self._t = d.tick_type()
        self._n = _U8()
        d.moy_tick_start(ctypes.byref(self._t), 30, True)
        self._t.rate = 0
        self._t.period = 0.0
        self._t.tick_ms = 0

    def start(self, rate, steady=True):
        """`rate` 60 opts in; anything else is the 30 the console guarantees."""
        self._d.moy_tick_start(ctypes.byref(self._t), 60 if rate == 60 else 30, bool(steady))

    def steady_mode(self, on):
        self._d.moy_tick_mode(ctypes.byref(self._t), bool(on), self._t.uncapped)

    def uncap_mode(self, on):
        self._d.moy_tick_mode(ctypes.byref(self._t), self._t.steady, bool(on))

    def note_tick(self, seconds):
        self._d.moy_tick_note(ctypes.byref(self._t), float(seconds))

    def plan(self, dt):
        return self._d.moy_tick_plan(ctypes.byref(self._t), float(dt), ctypes.byref(self._n))

    def cycle(self, div):
        return self._d.moy_tick_cycle(ctypes.byref(self._t), int(div))

    def fits(self, div):
        return self._d.moy_tick_fits(ctypes.byref(self._t), int(div))

    def __getattr__(self, name):
        return getattr(self._t, name)


def census(path, lua=True, wasm=True):
    """(runtime, vm_free, why) of the cart folder at `path` as the Player
    decides it, on an image whose runtime map has the rows named; None when
    the folder is no cart."""
    d = _lib()
    d.moy_play_host_runtimes(int(lua), int(wasm))
    out = ctypes.create_string_buffer(96)
    if d.moy_play_census(os.path.abspath(path).encode(), out, len(out)) != 0:
        return None
    rt, verdict, why = out.value.decode().split(" ")
    return rt, verdict == "free", why


_MPY_UZLIB = os.path.join(native_build.ROOT, ".build", "unix_micropython", "micropython",
                          "lib", "uzlib")
_IMG = [None]


def _img_lib():
    if _IMG[0] is None:
        if not os.path.isdir(_MPY_UZLIB):
            raise ImportError("moy_img: no MicroPython tree for its inflater "
                              "(make unix-micropython)")
        path = native_build.build(
            "moy_img", os.path.join(_STORE, "moy_img_host.c"),
            ("moy_img.h", "moy_img.c", "moy_json.h", "moy_json.c", "uzlib.h",
             "uzlib_conf.h", "tinflate.c", "header.c", "adler32.c", "crc32.c"),
            _CACHE, libmoy_dir=(_STORE, _SPINE, _MPY_UZLIB))
        if path is None:
            raise ImportError("moy_img: no C compiler for the host build")
        d = ctypes.CDLL(path)
        d.moy_img_host_decode.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                                          ctypes.c_size_t, ctypes.POINTER(_U32),
                                          ctypes.POINTER(_U32)]
        d.moy_img_host_decode.restype = ctypes.c_int
        _IMG[0] = d
    return _IMG[0]


def image_decode(text, cap=1 << 20):
    """(w, h, bytes) of a `.moyimg`'s text as the C decoder reads it, or None
    when it is not a picture (or holds more than `cap` - 1 pixels)."""
    raw = text.encode() if isinstance(text, str) else bytes(text)
    out = ctypes.create_string_buffer(cap)
    w, h = _U32(), _U32()
    rc = _img_lib().moy_img_host_decode(raw, len(raw), out, cap, ctypes.byref(w),
                                        ctypes.byref(h))
    if rc != 0:
        return None
    return w.value, h.value, out.raw[:w.value * h.value]


_FILES = [None]


def _files_lib():
    if _FILES[0] is None:
        path = native_build.build(
            "moy_files", os.path.join(_STORE, "moy_store_host.c"),
            ("moy_files.h", "moy_files.c", "moy_vol.h", "moy_vol.c"),
            _CACHE, libmoy_dir=(_STORE,))
        if path is None:
            raise ImportError("moy_files: no C compiler for the host build")
        d = ctypes.CDLL(path)
        _C, _S, _P = ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p
        for name, args, res in (
                ("moy_files_key", [_C, _S, _C, _S], _S),
                ("moy_files_path_of", [_C, _S, _C, _S], ctypes.c_int),
                ("moy_files_folder", [_C, _C, _S], ctypes.c_int),
                ("moy_files_open", [_C], _P),
                ("moy_files_close", [_P], None),
                ("moy_files_where", [_P, _C, _S, _C, _S], ctypes.c_int),
                ("moy_files_write", [_P, _C, _S, _C, _U32], ctypes.c_int32),
                ("moy_files_erase", [_P, _C, _S], ctypes.c_int32),
                ("moy_files_name", [_P, _C, _S, _U32, _C, _U32], ctypes.c_int32),
                ("moy_store_host_live", [], ctypes.c_long)):
            f = getattr(d, name)
            f.argtypes = args
            f.restype = res
        _FILES[0] = d
    return _FILES[0]


def files_key(path):
    """The key a written path (bytes) is kept under, by the C
    (native/moy_store/moy_files.c). Host only."""
    out = ctypes.create_string_buffer(3 * len(path) + 2)
    n = _files_lib().moy_files_key(path, len(path), out, len(out))
    return out.value.decode() if n else None


def files_path_of(name):
    """A key back to its path (bytes), or None. Host only."""
    raw = name.encode()
    out = ctypes.create_string_buffer(len(raw) + 1)
    n = _files_lib().moy_files_path_of(raw, len(raw), out, len(out))
    return None if n < 0 else out.raw[:n]


def files_folder(cart_path):
    """The folder the cart at `cart_path` keeps its written files in. Host only."""
    out = ctypes.create_string_buffer(1024)
    if _files_lib().moy_files_folder(cart_path.encode(), out, len(out)) != 0:
        return None
    return out.value.decode()


class Files:
    """A cart's written files by the C store (moy_files.h), the shape of
    runtime/cart_files.py's CartFiles: where, write, erase, name. Host only."""

    def __init__(self, cart_path):
        self._d = d = _files_lib()
        self._f = d.moy_files_open(cart_path.encode())
        if not self._f:
            raise MemoryError("moy_files_open")

    def where(self, path):
        out = ctypes.create_string_buffer(1024)
        if not self._d.moy_files_where(self._f, path, len(path), out, len(out)):
            return None
        return out.value.decode()

    def write(self, path, data):
        return self._d.moy_files_write(self._f, path, len(path), bytes(data), len(data))

    def erase(self, path):
        return self._d.moy_files_erase(self._f, path, len(path))

    def name(self, prefix, index):
        out = ctypes.create_string_buffer(512)
        n = self._d.moy_files_name(self._f, prefix, len(prefix), index, out, len(out))
        return None if n < 0 else out.raw[:n]

    def close(self):
        if self._f:
            self._d.moy_files_close(self._f)
            self._f = None


def files_live():
    """The bytes the C store holds now: 0 once every session closed."""
    return _files_lib().moy_store_host_live()
