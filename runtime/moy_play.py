# Map (grep -n a name to jump there):
#   Tick              the tick model
#   census            the VM-free rule over a cart folder
#   Files             a cart's written files by the C store
#   launch            the Player: launch, bind, open, frame, end, info, lockstep
#   front_ops         the run in the kernel loop's front, on the host
#   Match             the radio link and its lockstep session
#   chrome_pill       the chrome's pieces: pill, panel, toast, banner, strip, menu
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
  front_ops(buf, w, h), front_frame, front_end, state_json
                   the kernel loop's half of a run in front (a board's loop
                   calls the C), over a buffer standing in for a compositor.
                   Host only.
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
                ("moy_files_read", [_P, _C, _U32, _C, _U32], ctypes.c_int32),
                ("moy_files_read_written", [_P, _C, _S, _U32, _C, _U32], ctypes.c_int32),
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
    runtime/cart_files.py's CartFiles: where, read, write, erase, name, and
    `shipped`, a read of the cart's own folder. Host only."""

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

    def read(self, path, offset, n):
        """CartFiles.read: bytes, with `n` 0 how many remain, None with no
        written copy."""
        out = ctypes.create_string_buffer(max(n, 1))
        r = self._d.moy_files_read_written(self._f, path, len(path), offset, out, n)
        if r < 0:
            return None
        return r if n == 0 else out.raw[:r]

    def shipped(self, name, offset, n):
        """read on `name` in the cart's own folder: bytes, or with `n` 0 how
        many remain."""
        out = ctypes.create_string_buffer(max(n, 1))
        r = self._d.moy_files_read(self._f, name.encode(), offset, out, n)
        return r if n == 0 else out.raw[:r]

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


# -- the Player (native/moy_play/moy_play.h), name for name with modmoy_play.c --
#
# In the host's Lua library (runtime/lua_binding.py), beside moycore_lua.c, as
# on a board: a run's frame is the Lua run's. The handle is an int; a bad one
# is RuntimeError("moy_play: ..."), as the module raises.

QUIT, VIEW = 1, 2
END_HOLD, END_QUIT, END_MENU, END_CRASH, END_LINK, END_SERIAL = range(6)
_WHAT = ("ok", "stale run", "full", "no memory", "no such cart",
         "runtime not in this image", "newer", "does not fit", "raised", "ended",
         "needs the VM")
_WHY = ("free", "broken", "runtime", "absent", "type", "permission")


def _play():
    try:
        from . import lua_binding
    except ImportError:
        import lua_binding                  # type: ignore[no-redef]
    d = lua_binding.play_lib()
    if d is None:
        raise RuntimeError("moy_play: no host Player (a C compiler builds it)")
    return d, lua_binding


def _rc(rc):
    if rc != 0:
        raise RuntimeError("moy_play: %s" % (_WHAT[rc] if 0 <= rc < len(_WHAT) else "?"))


# The stop verdict's words (moy_play.h's MOY_PLAY_STOPS and KEEP_*), in order.
STOP_WHYS = ("stops", "rule", "lever", "fits", "route", "lease", "ota", "front", "big")
# launch()'s flags beyond `paced` (moy_play.h).
HOME = 2
UNSIGNED = 4


def launch(path, paced, flags=0):
    d, _ = _play()
    run = _U32(0)
    _rc(d.hl_play_launch(str(path).encode(), (1 if paced else 0) | int(flags),
                         ctypes.byref(run)))
    return run.value


def bind(run, inp, audio, tick):
    """The run's input table (an InputTable: its C table), its audio
    session's handle and the Tick a paced run notes into (None: unpaced)."""
    d, _ = _play()
    t = getattr(inp, "_t", None)
    if not t:
        raise TypeError("bind: not an input table")
    tp = None if tick is None else ctypes.cast(ctypes.byref(tick._t), ctypes.c_void_p)
    _rc(d.hl_play_bind(int(run), t, int(audio or 0), tp))
    _BOUND[:] = [inp, tick]


_BOUND = []


def open(run):  # noqa: A001 -- the module's verb
    d, _ = _play()
    _rc(d.hl_play_open(int(run)))


def frame(run, ticks, dt, render, x, y, touch):
    d, lb = _play()
    out = _U32(0)
    rc = d.hl_play_frame(int(run), int(ticks), float(dt), 1 if render else 0,
                         int(x), int(y), int(touch), ctypes.byref(out))
    if rc == 8:                             # MOY_PLAY_RAISED: the cart's own text
        i = lb.PlayInfo()
        d.hl_play_info(int(run), ctypes.byref(i))
        raise RuntimeError(i.error.decode("utf-8", "replace"))
    _rc(rc)
    return out.value


def end(run, why=END_QUIT):
    d, _ = _play()
    d.hl_play_end(int(run), int(why))
    del _BOUND[:]


def lockstep(run):
    """1 the frame simulates, 0 it stalls or waits, None: no match. The host
    has no kernel link, so a host run is always solo."""
    d, _ = _play()
    f = d.moy_play_lockstep
    f.argtypes = [_U32, _U32, ctypes.POINTER(_U8)]
    f.restype = _B
    t = _U8(0)
    import time
    if not f(int(run), int(time.monotonic() * 1000) & 0xFFFFFFFF, ctypes.byref(t)):
        return None
    return t.value


def info(run=None):
    """(runtime, vm_free, why, frames, ticks, upcalls, ended, error, stack_open,
    stack_frame, end_why, stop, vm_down), or None for a handle that names no
    run. `stop` is the launch's stop verdict ("stops", or the clause that kept
    the VM: STOP_WHYS)."""
    d, lb = _play()
    i = lb.PlayInfo()
    if d.hl_play_info(d.hl_play_last() if run is None else int(run), ctypes.byref(i)) != 0:
        return None
    st = (None if i.stack_open == 0xFFFFFFFF else i.stack_open,
          None if i.stack_frame == 0xFFFFFFFF else i.stack_frame)
    return (i.runtime.decode(), bool(i.vm_free), _WHY[i.why] if i.why < len(_WHY) else "?",
            i.frames, i.ticks, tuple(i.upcalls), bool(i.ended),
            i.error.decode("utf-8", "replace") if i.raised else None) + st + (
                i.end_why, STOP_WHYS[i.stop_why] if i.stop_why < len(STOP_WHYS) else "?",
                bool(i.vm_down))


def current():
    """The live run's handle, or 0."""
    d, _ = _play()
    d.moy_play_current.restype = _U32
    return int(d.moy_play_current())


def front(run):
    """moy_play_front: True when the run took the kernel loop's foreground.
    The host has no compositor to give it one; `front_ops` stands a buffer in
    for one, and without it the run is refused (NORT) and the console's frame
    drives it."""
    d, _ = _play()
    d.moy_play_front.argtypes = [_U32]
    return d.moy_play_front(int(run)) == 0


def front_live():
    d, _ = _play()
    d.moy_play_front_live.restype = ctypes.c_bool
    return bool(d.moy_play_front_live())


# The kernel loop's half of a run in front, on the host (no VM module has
# these: on a board the loop calls them). Host only.

_FCANVAS = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                            ctypes.POINTER(ctypes.c_int))
_FPRESENT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool)
_FMAP = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32),
                         ctypes.POINTER(ctypes.c_int32))


class _FrontOps(ctypes.Structure):
    """moy_front_ops_t (native/moy_play/moy_play.h)."""
    _fields_ = [("canvas", _FCANVAS), ("present", _FPRESENT), ("map", _FMAP),
                ("ctx", ctypes.c_void_p)]


_FRONT = []


def front_ops(buf, w, h, presents=None):
    """Stand a compositor in for the front: the run draws into `buf` (a
    ctypes array of w*h uint16), and each present appends whether the frame
    drew to `presents`. None withdraws."""
    d, _ = _play()
    d.moy_play_front_ops.argtypes = [ctypes.c_void_p]
    if buf is None:
        d.moy_play_front_ops(None)
        del _FRONT[:]
        return True

    def canvas(ctx, pw, ph):
        pw[0], ph[0] = w, h
        return ctypes.addressof(buf)

    def present(ctx, drew):
        if presents is not None:
            presents.append(bool(drew))

    def map_(ctx, x, y):
        return 0 <= x[0] < w and 0 <= y[0] < h

    cbs = (_FCANVAS(canvas), _FPRESENT(present), _FMAP(map_))
    ops = _FrontOps(cbs[0], cbs[1], cbs[2], None)
    _FRONT[:] = [buf, cbs, ops]
    d.moy_play_front_ops(ctypes.byref(ops))
    return True


def front_frame(now_ms, dt_us):
    """One kernel loop frame of the run in front: 1 drew, 0 did not, -1 the
    front gave the frame back."""
    d, _ = _play()
    d.moy_play_front_frame.argtypes = [_U32, _U32]
    return d.moy_play_front_frame(int(now_ms) & 0xFFFFFFFF, int(dt_us))


def front_end(why):
    d, _ = _play()
    d.moy_play_front_end.argtypes = [ctypes.c_int]
    d.moy_play_front_end.restype = ctypes.c_bool
    return bool(d.moy_play_front_end(int(why)))


def state_json():
    """The kernel's `state` answer while a run is in front, as text."""
    d, _ = _play()
    out = ctypes.create_string_buffer(1024)
    d.moy_play_state_json.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    d.moy_play_state_json.restype = ctypes.c_size_t
    n = d.moy_play_state_json(out, len(out))
    return out.raw[:n].decode()


# -- the radio link and its lockstep session (native/moy_play/moy_match.h) -----
#
# `Match(io, name, board, entropy)` is one C instance: the host's tests run two
# consoles in one process, where a board has the kernel's one (its
# `moy_play.Match()` takes no io: native/moy_net's ring is its transport).
# `io` is the transport: send(mac, payload) -> bool, recv() -> (mac, msg) or
# None (raising on a radio error), add_peer(mac), recover() -> bool, say(line).

_M_TAPE = 256
_M_CART = 121
_M_CFG = 101
_M_FRAME = 250


class _Peer(ctypes.Structure):
    _fields_ = [("mac", _U8 * 6), ("name", ctypes.c_char * 24),
                ("board", ctypes.c_char * 16), ("cart", ctypes.c_char * _M_CART),
                ("state", _U8), ("seen", _U32), ("used", _B)]


_I32, _F32 = ctypes.c_int32, ctypes.c_float


class _Lockstep(ctypes.Structure):
    _fields_ = [("live", _B), ("index", ctypes.c_int), ("peer", ctypes.c_int),
                ("seed", _U32), ("session", _U8), ("tick_ms", _U32),
                ("has_next", _B), ("next_ms", _U32), ("delay", ctypes.c_int),
                ("redundancy", ctypes.c_int), ("frame", _I32), ("stalls", _U32),
                ("stall_ticks", _U32), ("packets_in", _U32), ("packets_out", _U32),
                ("waiting", _B), ("dead", _B), ("last_peer_frame", _I32),
                ("peer_need", _I32), ("has_sent", _B), ("last_sent", _I32),
                ("stall_mark", _U32), ("mine_f", _I32 * _M_TAPE),
                ("theirs_f", _I32 * _M_TAPE), ("mine_m", _U8 * _M_TAPE),
                ("theirs_m", _U8 * _M_TAPE), ("arr_f", _I32 * 64), ("arr_t", _U32 * 64),
                ("has_ema", _B), ("m_ema", _F32), ("win_mark", _I32),
                ("win_stalls", _U32), ("tps_f", _I32), ("has_tps", _B), ("tps_ms", _U32),
                ("held", _U8 * 2), ("prev", _U8 * 2), ("pressed", _U8 * 2),
                ("frame_seed", _U32), ("config", ctypes.c_char * _M_CFG)]


class _MatchS(ctypes.Structure):
    _fields_ = [("io", ctypes.c_void_p), ("active", _B), ("mac", _U8 * 6),
                ("name", ctypes.c_char * 24), ("board", ctypes.c_char * 16),
                ("cart", ctypes.c_char * _M_CART), ("cfg", ctypes.c_char * _M_CFG),
                ("state", _U8), ("peers", _Peer * 8), ("s", _Lockstep),
                ("session_id", _U8), ("start_frame", _U8 * _M_FRAME),
                ("start_len", _U16), ("start_peer", _U8 * 6), ("start_tries", _U32),
                ("beaconed", _B), ("beacon_at", _U32), ("rx", _U32), ("tx", _U32),
                ("drops", _U32), ("recovers", _U32), ("rng", _U32), ("defer_n", _U8),
                ("defer_mac", (_U8 * 6) * 8), ("defer_len", _U8 * 8),
                ("defer", (_U8 * _M_FRAME) * 8), ("inbox_n", _U8), ("inbox_head", _U8),
                ("inbox_len", _U8 * 8), ("inbox", (_U8 * _M_FRAME) * 8),
                ("inbox_drops", _U32), ("act", _U8), ("act_cart", ctypes.c_char * _M_CART),
                ("error", ctypes.c_char * 48)]


_SEND = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_U8),
                         ctypes.POINTER(_U8), ctypes.c_size_t)
_RECV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_U8),
                         ctypes.POINTER(_U8), _U32)
_ADD = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_U8))
_RECOVER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_SAY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)


class _Io(ctypes.Structure):
    _fields_ = [("send", _SEND), ("recv", _RECV), ("add_peer", _ADD),
                ("recover", _RECOVER), ("say", _SAY), ("ctx", ctypes.c_void_p)]


_MSIGS = (
    ("moy_match_init", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p,
                        ctypes.c_char_p, _U32], None),
    ("moy_match_start", [ctypes.c_void_p, ctypes.c_char_p], None),
    ("moy_match_stop", [ctypes.c_void_p], None),
    ("moy_match_announce", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], None),
    ("moy_match_set_config", [ctypes.c_void_p, ctypes.c_char_p], None),
    ("moy_match_offer_seeded", [ctypes.c_void_p, ctypes.c_char_p, _U32, _B, _U32],
     ctypes.c_int),
    ("moy_match_poll", [ctypes.c_void_p, _U32], ctypes.c_int),
    ("moy_match_drain_input", [ctypes.c_void_p, _U32, ctypes.c_int], ctypes.c_int),
    ("moy_match_end", [ctypes.c_void_p], None),
    ("moy_match_broadcast", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t], _B),
    ("moy_match_send_msg", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int),
    ("moy_match_take_msg", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t], ctypes.c_int),
    ("moy_match_action", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int),
    ("moy_match_dispatch", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
                            ctypes.c_size_t, _U32], None),
    ("moy_match_candidate", [ctypes.c_void_p, ctypes.c_char_p, _U32], ctypes.c_void_p),
    ("moy_lockstep_begin", [ctypes.c_void_p, ctypes.c_int, _U32, _U8, ctypes.c_char_p],
     ctypes.c_int),
    ("moy_lockstep_close", [ctypes.c_void_p], None),
    ("moy_lockstep_pending", [ctypes.c_void_p, _U32], _B),
    ("moy_lockstep_due", [ctypes.c_void_p, _U32], _B),
    ("moy_lockstep_advance", [ctypes.c_void_p, _U8, _U32, _B], ctypes.c_int),
    ("moy_lockstep_resend", [ctypes.c_void_p], None),
    ("moy_lockstep_packet", [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, _U32, _B],
     ctypes.c_int),
    ("moy_lockstep_seed_of", [_U32, _I32], _U32),
    ("moy_lockstep_tps", [ctypes.c_void_p, _U32], _U32),
    ("moy_lockstep_expand", [ctypes.c_void_p, _U32], _I32),
    ("moy_match_size", [], ctypes.c_size_t),
    ("moy_match_offset_s", [], ctypes.c_size_t),
)
_MREADY = [False]


def _mlib():
    d, _ = _play()
    if not _MREADY[0]:
        for name, args, res in _MSIGS:
            f = getattr(d, name)
            f.argtypes = args
            f.restype = res
        if d.moy_match_size() != ctypes.sizeof(_MatchS) \
                or d.moy_match_offset_s() != _MatchS.s.offset:
            raise RuntimeError("moy_play: moy_match_t's layout moved; mirror it here")
        _MREADY[0] = True
    return d


def seed_of(seed, frame):
    """The seed lockstep frame `frame` starts from (moy_lockstep_seed_of)."""
    return _mlib().moy_lockstep_seed_of(int(seed) & 0xFFFFFFFF, int(frame))


_LINK_FIELDS = ("active", "state", "rx", "tx", "drops", "recovers", "session_id",
                "start_tries", "start_len", "inbox_drops")


class Match:
    """One link and its session over the C (moy_match.h). Attributes read the
    instance live: the link's (`active`, `state`, `rx`, `tx`, `drops`,
    `recovers`, `error`, `cart`, `mac`, `session_id`, ...) and the session's
    (`live`, `index`, `frame`, `delay`, `waiting`, `dead`, `stalls`, ...)."""

    def __init__(self, io=None, name="", board="", entropy=0):
        if io is None:
            raise RuntimeError("moy_play.Match: the host has no kernel link")
        d = _mlib()
        self._d = d
        self._m = _MatchS()
        self._io_obj = io
        self._io = _Io(_SEND(self._send), _RECV(self._recv), _ADD(self._add),
                       _RECOVER(self._recover), _SAY(self._say), None)
        self._p = ctypes.cast(ctypes.byref(self._m), ctypes.c_void_p)
        d.moy_match_init(self._p, ctypes.cast(ctypes.byref(self._io), ctypes.c_void_p),
                         str(name).encode(), str(board).encode(),
                         int(entropy) & 0xFFFFFFFF)

    # -- the transport's trampolines --
    def _send(self, _ctx, mac, data, n):
        try:
            ok = self._io_obj.send(bytes(mac[:6]), bytes(data[:n]))
        except Exception:  # noqa: BLE001 -- a radio hiccup is a counted drop
            return -1
        return 0 if ok is not False else -1

    def _recv(self, _ctx, mac, data, cap):
        try:
            got = self._io_obj.recv()
        except Exception:  # noqa: BLE001 -- the documented desynced ring
            return -2
        if got is None:
            return -1
        src, msg = got
        msg = bytes(msg)[:cap]
        for i in range(6):
            mac[i] = src[i]
        for i in range(len(msg)):
            data[i] = msg[i]
        return len(msg)

    def _add(self, _ctx, mac):
        try:
            self._io_obj.add_peer(bytes(mac[:6]))
        except Exception:  # noqa: BLE001 -- already known is fine
            pass
        return 0

    def _recover(self, _ctx):
        rec = getattr(self._io_obj, "recover", None)
        try:
            return 0 if rec is None or rec() is not False else -1
        except Exception:  # noqa: BLE001
            return -1

    def _say(self, _ctx, line):
        say = getattr(self._io_obj, "say", None)
        text = line.decode("utf-8", "replace")
        if say is not None:
            say(text)
        else:
            print(text)

    # -- the instance --
    def __getattr__(self, name):
        m = self.__dict__.get("_m")
        if m is None:
            raise AttributeError(name)
        if name in _LINK_FIELDS:
            return getattr(m, name)
        if name in ("cart", "name", "board", "error", "cfg"):
            return getattr(m, name).decode("utf-8", "replace")
        if name == "mac":
            return bytes(m.mac)
        s = m.s
        if name == "config":
            c = s.config.decode("utf-8", "replace")
            return c or None
        if name == "m_ema":
            return s.m_ema if s.has_ema else None
        if name == "next_ms":
            return s.next_ms if s.has_next else None
        if name == "last_sent":
            return s.last_sent if s.has_sent else None
        if name == "tps_ms":
            return s.tps_ms if s.has_tps else None
        if name in ("held", "pressed"):
            return tuple(getattr(s, name))
        return getattr(s, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        m = self._m
        if name == "state":                 # white box: the tests' setters
            m.state = int(value)
            return
        if name == "cart":
            m.cart = (value or "").encode()[:_M_CART - 1]
            return
        s = m.s
        if name == "m_ema":
            s.has_ema = value is not None
            s.m_ema = 0.0 if value is None else float(value)
        elif name == "next_ms":
            s.has_next = value is not None
            s.next_ms = 0 if value is None else int(value) & 0xFFFFFFFF
        elif name in ("delay", "redundancy", "tick_ms", "frame", "stalls"):
            setattr(s, name, int(value))
        else:
            raise AttributeError(name)

    def arrival(self, f, t):
        """White-box: stamp frame `f`'s first arrival at `t` (the phase controller's ring)."""
        s = self._m.s
        s.arr_f[f & 63] = f
        s.arr_t[f & 63] = int(t) & 0xFFFFFFFF

    def start(self, mac):
        self._d.moy_match_start(self._p, bytes(mac)[:6].ljust(6, b"\0"))

    def stop(self):
        self._d.moy_match_stop(self._p)

    def announce(self, cart, state):
        self._d.moy_match_announce(self._p, (cart or "").encode(), int(state))

    def set_config(self, cfg):
        self._d.moy_match_set_config(self._p, None if cfg is None else cfg.encode())

    def offer(self, cart, now, seed=None):
        return bool(self._d.moy_match_offer_seeded(
            self._p, (cart or "").encode(), int(now) & 0xFFFFFFFF, seed is not None,
            0 if seed is None else int(seed) & 0xFFFFFFFF))

    def poll(self, now):
        return self._d.moy_match_poll(self._p, int(now) & 0xFFFFFFFF)

    def drain(self, now, budget):
        return self._d.moy_match_drain_input(self._p, int(now) & 0xFFFFFFFF, int(budget))

    def end(self):
        self._d.moy_match_end(self._p)

    def broadcast(self, payload):
        payload = bytes(payload)
        return bool(self._d.moy_match_broadcast(self._p, payload, len(payload)))

    def send_msg(self, body):
        body = bytes(body)
        return self._d.moy_match_send_msg(self._p, body, len(body))

    def take_msg(self):
        buf = ctypes.create_string_buffer(_M_FRAME)
        n = self._d.moy_match_take_msg(self._p, buf, _M_FRAME)
        return None if n < 0 else buf.raw[:n]

    def action(self):
        buf = ctypes.create_string_buffer(_M_CART)
        a = self._d.moy_match_action(self._p, buf, _M_CART)
        return buf.value.decode("utf-8", "replace") if a else None

    def dispatch(self, mac, msg, now):
        msg = bytes(msg)
        self._d.moy_match_dispatch(self._p, bytes(mac)[:6], msg, len(msg),
                                   int(now) & 0xFFFFFFFF)

    def peers(self):
        """[(mac, name, board, cart, state, seen)] of every peer row."""
        out = []
        for p in self._m.peers:
            if p.used:
                out.append((bytes(p.mac), p.name.decode("utf-8", "replace"),
                            p.board.decode("utf-8", "replace"),
                            p.cart.decode("utf-8", "replace"), p.state, p.seen))
        return out

    def candidate(self, cart, now):
        r = self._d.moy_match_candidate(self._p, (cart or "").encode(), int(now) & 0xFFFFFFFF)
        if not r:
            return None
        p = _Peer.from_address(r)
        return (bytes(p.mac), p.name.decode(), p.board.decode(), p.cart.decode(),
                p.state, p.seen)

    def begin(self, index, seed, session, cfg):
        return self._d.moy_lockstep_begin(self._p, int(index), int(seed) & 0xFFFFFFFF,
                                          int(session) & 0xFF,
                                          None if cfg is None else cfg.encode()) == 0

    def close(self):
        self._d.moy_lockstep_close(self._p)

    def pending(self, now):
        return bool(self._d.moy_lockstep_pending(self._p, int(now) & 0xFFFFFFFF))

    def due(self, now):
        return bool(self._d.moy_lockstep_due(self._p, int(now) & 0xFFFFFFFF))

    def advance(self, held, now=None):
        return self._d.moy_lockstep_advance(self._p, int(held) & 0xFF,
                                            0 if now is None else int(now) & 0xFFFFFFFF,
                                            now is not None)

    def resend(self):
        self._d.moy_lockstep_resend(self._p)

    def packet(self, data, now=None):
        data = bytes(data)
        return bool(self._d.moy_lockstep_packet(
            self._p, data, len(data), 0 if now is None else int(now) & 0xFFFFFFFF,
            now is not None))

    def tps(self, now):
        return self._d.moy_lockstep_tps(self._p, int(now) & 0xFFFFFFFF)

    def expand(self, f16):
        return self._d.moy_lockstep_expand(self._p, int(f16) & 0xFFFF)


# -- the chrome (native/moy_play/moy_chrome.h) -----------------------------------
#
# Each piece is a list of (op, c, scale, x, y, w, h, text) the shell replays
# through its canvas (runtime/chrome.py's `replay`); the kernel rasterises the
# same list for a frame it draws with no VM.

CH_RECT, CH_RECTB, CH_TEXT, CH_GLYPH, CH_ICON = 1, 2, 3, 4, 5
INKS = 26
MENU_ITEM, MENU_HEADER, MENU_SEP = 0, 1, 2


class _ChOp(ctypes.Structure):
    _fields_ = [("op", _U8), ("c", _U8), ("scale", _U8), ("pad", _U8),
                ("x", ctypes.c_int16), ("y", ctypes.c_int16), ("w", ctypes.c_int16),
                ("h", ctypes.c_int16), ("s", _U16), ("n", _U16)]


class _ChList(ctypes.Structure):
    _fields_ = [("n", _U16), ("tn", _U16), ("full", _B), ("op", _ChOp * 128),
                ("text", ctypes.c_char * 1536)]


class _ChLay(ctypes.Structure):
    _fields_ = [("clock_x", ctypes.c_int16), ("text_dy", ctypes.c_int16),
                ("cs", ctypes.c_int16), ("wifi", ctypes.c_int16 * 4),
                ("batt", ctypes.c_int16 * 4), ("menu", ctypes.c_int16 * 4),
                ("close", ctypes.c_int16 * 4)]


_CP, _CI, _CS = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p
_CSIGS = (
    ("moy_chrome_set_inks", [ctypes.POINTER(ctypes.c_int16), _B, _CI], None),
    ("moy_chrome_clear", [_CP], None),
    ("moy_chrome_pill", [_CP, _CI, _CI, _U32, _U32], None),
    ("moy_chrome_panel", [_CP, _CI, _CI, _B, _CS, _CS, _B], None),
    ("moy_chrome_toast", [_CP, _CS, _CS], None),
    ("moy_chrome_banner", [_CP, _CI, _CI, _CI, _CS, _CS, _B], None),
    ("moy_chrome_strip_crash", [_CP, _CI, _CI, _CS, _CS], None),
    ("moy_chrome_strip_band", [_CP, _CI, _CI, _B], None),
    ("moy_chrome_strip_right", [_CP, _CP, _CS, _CS, _B], None),
    ("moy_chrome_strip_title", [_CP, _CS, _CI, _CI, _CI], None),
    ("moy_chrome_menu", [_CP, _CP, _CP, _CI, _CI, _CI, _CI, _CI, _CI, _CI, _CI], None),
    ("moy_chrome_about", [_CP, _CI, _CI, _CI, _CS], None),
    ("moy_chrome_glyph_put", [_CS, ctypes.POINTER(_U16)], _CI),
    ("moy_chrome_raster", [_CP, _CP, _CI, _CI, _CP, _CI], None),
    ("moy_chrome_notice", [_CS, _CS, _B, _U32], None),
    ("moy_chrome_toast_arm", [_CS, _CS, _U32], None),
    ("moy_chrome_overlay", [_CP, _U32, _CI, _CI, _CI, _CI, _CI, _U32, _U32], _B),
)
_CREADY = [False]


def _clib():
    d, _ = _play()
    if not _CREADY[0]:
        for name, args, res in _CSIGS:
            f = getattr(d, name)
            f.argtypes = args
            f.restype = res
        _CREADY[0] = True
    return d


def _b(s):
    return (s or "").encode("utf-8", "replace") if not isinstance(s, bytes) else s


def _ops(lst):
    out = []
    raw = lst.text
    for i in range(lst.n):
        o = lst.op[i]
        t = raw[o.s:o.s + o.n].decode("utf-8", "replace") if o.n else ""
        out.append((o.op, o.c, o.scale, o.x, o.y, o.w, o.h, t))
    return out


def _chrome(name, *args, raster=None):
    d = _clib()
    lst = _ChList()
    p = ctypes.cast(ctypes.byref(lst), ctypes.c_void_p)
    getattr(d, name)(p, *args)
    if raster is not None:
        buf, w, h, pal, fs = raster
        pb = (_U16 * 64)(*pal)
        d.moy_chrome_raster(p, ctypes.cast(buf, ctypes.c_void_p), w, h,
                            ctypes.cast(pb, ctypes.c_void_p), fs)
    return _ops(lst)


def chrome_inks(inks, bar_light, rings):
    arr = (ctypes.c_int16 * INKS)(*[(-1 if v is None else int(v)) for v in inks])
    _clib().moy_chrome_set_inks(arr, bool(bar_light), int(rings))


def chrome_pill(cw, ch, held_ms, hold_ms, raster=None):
    return _chrome("moy_chrome_pill", int(cw), int(ch), int(held_ms), int(hold_ms),
                   raster=raster)


def chrome_panel(cw, ch, notice, title, text, compiled, raster=None):
    return _chrome("moy_chrome_panel", int(cw), int(ch), bool(notice), _b(title),
                   _b(text), bool(compiled), raster=raster)


def chrome_toast(title, glyph, raster=None):
    return _chrome("moy_chrome_toast", _b(title), _b(glyph), raster=raster)


def chrome_banner(lw, fs, status_h, title, sub, ok, raster=None):
    return _chrome("moy_chrome_banner", int(lw), int(fs), int(status_h), _b(title),
                   _b(sub), bool(ok), raster=raster)


def chrome_strip_crash(cw, edit, clock, wifi):
    return _chrome("moy_chrome_strip_crash", int(cw), int(edit), _b(clock), _b(wifi))


def chrome_strip_band(cw, bar_h, light):
    return _chrome("moy_chrome_strip_band", int(cw), int(bar_h), bool(light))


def chrome_strip_right(lay, clock, wifi, show_x):
    """`lay` None: the fixed game-canvas cluster; else (clock_x, text_dy, cs,
    wifi_rect, batt_rect, menu_rect, close_rect)."""
    lp = None
    if lay is not None:
        lp = _ChLay()
        lp.clock_x, lp.text_dy, lp.cs = int(lay[0]), int(lay[1]), int(lay[2])
        for k, r in zip(("wifi", "batt", "menu", "close"), lay[3:7]):
            a = getattr(lp, k)
            for i in range(4):
                a[i] = int(r[i])
        lp = ctypes.cast(ctypes.byref(lp), ctypes.c_void_p)
    return _chrome("moy_chrome_strip_right", lp, _b(clock), _b(wifi), bool(show_x))


def chrome_strip_title(title, zx, zw, dy):
    return _chrome("moy_chrome_strip_title", _b(title), int(zx), int(zw), int(dy))


def chrome_menu(rows, sel, x, y, w, h, fs, cs):
    """`rows`: [(kind, label)] with kind MENU_ITEM, MENU_HEADER or MENU_SEP."""
    n = len(rows)
    kinds = (_U8 * max(n, 1))(*[int(r[0]) for r in rows])
    keep = [_b(r[1] if len(r) > 1 else "") for r in rows]
    labels = (ctypes.c_char_p * max(n, 1))(*keep)
    return _chrome("moy_chrome_menu", ctypes.cast(kinds, ctypes.c_void_p),
                   ctypes.cast(labels, ctypes.c_void_p), n, int(sel), int(x), int(y),
                   int(w), int(h), int(fs), int(cs))


def chrome_about(cw, ch, fs, ver):
    return _chrome("moy_chrome_about", int(cw), int(ch), int(fs), _b(ver))


def chrome_glyph(kind, rows):
    arr = (_U16 * 12)(*[int(r) & 0xFFF for r in rows])
    return _clib().moy_chrome_glyph_put(_b(kind), arr) == 0


def chrome_notice(title, sub, ok, until_ms):
    _clib().moy_chrome_notice(_b(title), _b(sub), bool(ok), int(until_ms) & 0xFFFFFFFF)


def chrome_toast_arm(title, glyph, until_ms):
    _clib().moy_chrome_toast_arm(_b(title), _b(glyph), int(until_ms) & 0xFFFFFFFF)
