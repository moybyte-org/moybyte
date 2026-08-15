"""Build + load the host's libmoy LUA binding (moycore plan rung 4).

The host sim USED to run Lua carts through **lupa**, a second Lua embedding
with second semantics: 64-bit doubles where both boards build `LUA_32BITS`
(their FPUs are single-precision, so doubles would be soft-float). That was a
standing parity hole -- golden-frame parity for float-heavy carts was host-only,
and device integers wrapped at 2^31 where the host's did not.

This closed it by giving CPython the same program the boards run: libmoy's
binding of the spec verb table over the same vendored Lua 5.4, compiled with
the same `LUA_32BITS`, reached by ctypes. Same build-and-cache shape as
`audio_binding` and `raster_binding`.

lupa was DELETED on 2026-08-14 once this was the only sane lane, so absence is
no longer graceful in the old sense: no compiler means `available()` is False
and there are no Lua carts on the host at all, exactly as a device build without
the native module has none. That is the same trade host AUDIO already makes,
where no compiler means silence rather than a second synth.

The shim it loads (`runtime/moyhost_lua.c`) is deliberately `modmoycore.c` with
the MicroPython removed -- same console, same snapshot-in/queue-out host
callbacks -- because a host and a device that disagree about what a verb does
is the whole disease.
"""

import ctypes
import hashlib
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_NATIVE = os.path.join(_ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                       "native")
_LIBMOY = os.path.join(_NATIVE, "moy_gfx", "libmoy")
_BINDING = os.path.join(_NATIVE, "moycore", "libmoy", "moy_lua.c")
_LUA = os.path.join(_NATIVE, "moy_lua", "lua")
_SHIM = os.path.join(_HERE, "moyhost_lua.c")
_CACHE = os.path.join(_ROOT, ".build", "host_lua")

# MOY_WITH_LUA compiles libmoy's binding at all. The Lua sources carry their
# own LUA_32BITS in luaconf.h, which is the point of using them rather than a
# system Lua: the host then wraps its integers where the boards wrap theirs.
_CFLAGS = ["-std=c99", "-O2", "-fPIC", "-shared", "-DMOY_WITH_LUA=1",
           "-Wno-double-promotion", "-Wno-float-conversion"]

# The sandbox's source set, matching the boards': the unused stdlibs -- and
# linit.c, whose luaL_openlibs references all of them -- stay out entirely, so
# there is no reachable implementation to be re-exposed by accident.
_LUA_SKIP = ("linit.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c",
             "lcorolib.c", "lutf8lib.c", "lua.c", "luac.c", "onelua.c")

_LIB = [None]


def _cc():
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


def _lua_sources():
    if not os.path.isdir(_LUA):
        return []
    return sorted(n for n in os.listdir(_LUA)
                  if n.endswith(".c") and n not in _LUA_SKIP)


def _key(cc):
    h = hashlib.sha256()
    for path in (_SHIM, _BINDING, os.path.join(_LIBMOY, "moy.h")):
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            return None
    h.update(" ".join(_CFLAGS).encode())
    h.update(",".join(_lua_sources()).encode())
    try:
        ver = subprocess.run([cc, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.splitlines()[:1]
        h.update((ver[0] if ver else "").encode())
    except Exception:   # noqa: BLE001
        pass
    return h.hexdigest()[:16]


def build(verbose=False):
    """Compile (or reuse) the cached .so; None when the pieces are absent."""
    cc = _cc()
    if cc is None or not os.path.isfile(_BINDING) or not _lua_sources():
        return None
    key = _key(cc)
    if key is None:
        return None
    so_path = os.path.join(_CACHE, "moyhost_lua-%s.so" % key)
    if os.path.exists(so_path):
        return so_path
    os.makedirs(_CACHE, exist_ok=True)
    tmp = so_path + ".tmp"
    srcs = [_SHIM, _BINDING,
            os.path.join(_LIBMOY, "moy_canvas.c"),
            os.path.join(_LIBMOY, "moy_sprite.c"),
            os.path.join(_LIBMOY, "moy_data.c")]
    srcs += [os.path.join(_LUA, n) for n in _lua_sources()]
    cmd = [cc] + _CFLAGS + ["-I", _LIBMOY, "-I", _LUA] + srcs + ["-o", tmp, "-lm"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("moyhost_lua build failed:\n" + proc.stderr[-4000:])
    os.replace(tmp, so_path)
    if verbose:
        print("moyhost_lua: built", os.path.relpath(so_path, _ROOT))
    return so_path


_I, _P, _F, _C = ctypes.c_int, ctypes.c_void_p, ctypes.c_float, ctypes.c_char_p


def _lib():
    if _LIB[0] is None:
        try:
            path = build()
        except Exception:   # noqa: BLE001
            path = None
        if path is None:
            _LIB[0] = False
        else:
            d = ctypes.CDLL(path)
            d.hl_new.argtypes = [_P, _I, _I, _P, _P, _I]
            d.hl_new.restype = _P
            d.hl_set_sheet.argtypes = [_P, _P]
            d.hl_set_map.argtypes = [_P, _P, _I, _I]
            d.hl_retarget.argtypes = [_P, _P]
            d.hl_load.argtypes = [_P, _C, _I, _C, _P, _I]
            d.hl_load.restype = _I
            d.hl_exec.argtypes = [_P, _C, _I, _C, _P, _I]
            d.hl_exec.restype = _I
            d.hl_tick.argtypes = [_P, _F, _P, _I]
            d.hl_tick.restype = _I
            d.hl_pmem_image.argtypes = [_P, _P, _I]
            d.hl_pmem_image.restype = _I
            d.hl_pmem_load.argtypes = [_P, _P, _I]
            # NB: argtypes are set EXPLICITLY here, not from _SIGS -- and a
            # function left without them takes the default int conversion,
            # which truncates a 64-bit pointer and hands the C a garbage
            # host_lua*. That is a segfault, not a TypeError, so it presents as
            # "the process died" with no Python traceback.
            d.hl_get_view.argtypes = [_P, ctypes.POINTER(_I), ctypes.POINTER(_I)]
            d.hl_get_view.restype = _I
            d.hl_heap_bytes.argtypes = [_P]
            d.hl_heap_bytes.restype = _I
            d.hl_heap_peak_bytes.argtypes = [_P]
            d.hl_heap_peak_bytes.restype = _I
            d.hl_get_global_len.argtypes = [_P, _C]
            d.hl_get_global_len.restype = _I
            d.hl_get_global_num.argtypes = [_P, _C, ctypes.POINTER(ctypes.c_double)]
            d.hl_get_global_num.restype = _I
            d.hl_register.argtypes = [_P, _C, _I]
            d.hl_set_dispatch.argtypes = [_P, _P]
            d.hl_free.argtypes = [_P]
            _LIB[0] = d
    return _LIB[0] or None


SNAP_LEN = 14
SNAP_BTN, SNAP_BTNP, SNAP_BTN_P1, SNAP_BTNP_P1 = 0, 1, 2, 3
SNAP_PLAYERS, SNAP_TIME_MS = 4, 5
SNAP_TOUCH_X, SNAP_TOUCH_Y, SNAP_TOUCH_DOWN, SNAP_TOUCH_MS = 6, 7, 8, 9
SNAP_KEY, SNAP_KEY_DOWN, SNAP_TEXTMODE, SNAP_QUIT = 10, 11, 12, 13
AQ_SLOTS = 4
AQ_SFX, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME = range(6)


class HostLuaRun:
    """One Lua cart run, in the same C the boards run."""

    AUDIO_MAX = 32

    @staticmethod
    def available():
        return _lib() is not None

    def __init__(self, buf, w, h, sheet=None, tilemap=None):
        d = _lib()
        if d is None:
            raise RuntimeError("no host lua binding")
        self._d = d
        self.buf = buf
        self.snap = (ctypes.c_int32 * SNAP_LEN)()
        self.aq = (ctypes.c_int32 * (1 + AQ_SLOTS * self.AUDIO_MAX))()
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        self._r = d.hl_new(ctypes.cast(self._cbuf, _P), int(w), int(h),
                           ctypes.cast(self.snap, _P), ctypes.cast(self.aq, _P),
                           len(self.aq))
        if not self._r:
            raise RuntimeError("host lua: could not open a VM")
        self.snap[SNAP_PLAYERS] = 1
        self._sheet_ref = self._map_ref = None
        if sheet is not None:
            pix = sheet.pix if hasattr(sheet, "pix") else sheet
            if not isinstance(pix, bytearray):
                pix = bytearray(pix)
            self._sheet_ref = (ctypes.c_char * len(pix)).from_buffer(pix)
            d.hl_set_sheet(self._r, ctypes.cast(self._sheet_ref, _P))
        if tilemap is not None:
            cells = tilemap.cells
            if not isinstance(cells, bytearray):
                cells = bytearray(cells)
            self._map_ref = (ctypes.c_char * len(cells)).from_buffer(cells)
            d.hl_set_map(self._r, ctypes.cast(self._map_ref, _P),
                         int(tilemap.w), int(tilemap.h))

    # The dispatch callback's C signature; kept alive on the instance because
    # ctypes will collect a CFUNCTYPE object the C side is still holding.
    _DISPATCH = ctypes.CFUNCTYPE(_I, _I, _I, ctypes.POINTER(_I), _C,
                                 ctypes.POINTER(_I))

    def register(self, name, fn):
        """Add a verb libmoy does not bind. After __init__, before load()."""
        if not hasattr(self, "_ext"):
            self._ext = []
            self._cb = self._DISPATCH(self._dispatch)
            self._d.hl_set_dispatch(self._r, ctypes.cast(self._cb, _P))
        idx = len(self._ext)
        self._ext.append(fn)
        self._d.hl_register(self._r, name.encode(), idx)

    def _dispatch(self, idx, argc, iargs, sarg, out):
        """C -> Python. Returns 1 when it produced a value, 0 for nil."""
        try:
            fn = self._ext[idx]
            args = []
            if sarg:
                args.append(sarg.decode("utf-8", "replace"))
            args.extend(int(iargs[i]) for i in range(argc))
            r = fn(*args)
        except Exception:  # noqa: BLE001 -- a raising verb reads as nil, and
            return 0       # the console's own error path reports it
        if r is None or r is False:
            return 0
        try:
            out[0] = int(r)
        except (TypeError, ValueError):
            return 0
        return 1

    def exec(self, src, name="glue"):
        """Run a chunk that is NOT the cart -- the glue prelude. None, or the
        error text. See moyhost_lua.c's hl_exec for why the split exists."""
        err = ctypes.create_string_buffer(256)
        b = src.encode("utf-8") if isinstance(src, str) else bytes(src)
        if self._d.hl_exec(self._r, b, len(b), name.encode(),
                           ctypes.cast(err, _P), 256):
            return err.value.decode("utf-8", "replace")
        return None

    def load(self, src, name="@cart"):
        """Run the chunk and `_init`. Returns None, or the error text."""
        err = ctypes.create_string_buffer(256)
        b = src.encode("utf-8") if isinstance(src, str) else bytes(src)
        if self._d.hl_load(self._r, b, len(b), name.encode(),
                           ctypes.cast(err, _P), 256):
            return err.value.decode("utf-8", "replace")
        return None

    def tick(self, dt):
        """One whole cart frame. None, or the error text."""
        err = ctypes.create_string_buffer(256)
        if self._d.hl_tick(self._r, ctypes.c_float(dt),
                           ctypes.cast(err, _P), 256):
            return err.value.decode("utf-8", "replace")
        return None

    def audio(self):
        """Drain the queue: [(op, a, b, c), ...] in the order the cart made
        them."""
        n = self.aq[0]
        out = []
        for i in range(n):
            p = 1 + i * AQ_SLOTS
            out.append((self.aq[p], self.aq[p + 1], self.aq[p + 2], self.aq[p + 3]))
        self.aq[0] = 0
        return out

    def pmem(self):
        img = (ctypes.c_int32 * 256)()
        dirty = self._d.hl_pmem_image(self._r, ctypes.cast(img, _P), 256)
        return bool(dirty), list(img)

    def view(self):
        """(w, h) as the cart last declared with view(), or None."""
        w, h = _I(0), _I(0)
        if self._d.hl_get_view(self._r, ctypes.byref(w), ctypes.byref(h)):
            return (w.value, h.value)
        return None

    def heap_bytes(self, collect=True):
        """The cart's Lua heap -- live after a collect, or as-reached."""
        if collect:
            return self._d.hl_heap_bytes(self._r)
        return self._d.hl_heap_peak_bytes(self._r)

    def get_global(self, name):
        """A cart global as a number, or None."""
        v = ctypes.c_double(0.0)
        if self._d.hl_get_global_num(self._r, name.encode(), ctypes.byref(v)):
            f = v.value
            return int(f) if f == int(f) else f
        return None

    def get_global_len(self, name):
        """The length of a table global (Lua's #t), or None."""
        n = self._d.hl_get_global_len(self._r, name.encode())
        return None if n < 0 else n

    def close(self):
        if getattr(self, "_r", None):
            self._d.hl_free(self._r)
            self._r = None
