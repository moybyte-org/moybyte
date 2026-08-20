"""Build + load the host's libmoy LUA binding (moycore plan rung 4).

The host sim USED to run Lua carts through **lupa**, a second Lua embedding
with second semantics: 64-bit doubles where both boards build `LUA_32BITS`
(their FPUs are single-precision, so doubles would be soft-float). That was a
standing parity hole -- golden-frame parity for float-heavy carts was host-only,
and device integers wrapped at 2^31 where the host's did not.

This closed it by giving CPython the same program the boards run: libmoy's
binding of the spec verb table over the same vendored Lua 5.4, compiled with
the same `LUA_32BITS`, reached by ctypes. Same build-and-cache shape as
`audio_binding` and `gfx_binding`.

lupa was DELETED on 2026-08-14 once this was the only sane lane, so absence is
no longer graceful in the old sense: no compiler means `available()` is False
and there are no Lua carts on the host at all, exactly as a device build without
the native module has none. That is the same trade host AUDIO already makes,
where no compiler means silence rather than a second synth.

The shim it loads (`runtime/moyhost_lua.c`) is deliberately `modmoycore.c` with
the MicroPython removed -- same console, same snapshot-in/queue-out host
callbacks -- because a host and a device that disagree about what a verb does
is the whole disease.

PIXELS ARE RGB565 HERE, as they are on both boards: moycore's micropython.mk
compiles libmoy `-DMOY_PIXEL_RGB565=1`, and this compiles the same source the
same way. That is not a detail -- it changes sizeof(moy_pixel), so a library
built the other way addresses `y*w+x` over one byte instead of two and writes
half-width rows of raw palette indices into a direct-colour framebuffer. The
shim `#error`s without the define; the caller supplies the 64-entry wire table
the boards read off their canvas, because a 565 canvas resolves colour at DRAW
time and libmoy has to be told what an index looks like.

An INDEXED canvas (one byte a pixel) is bridged rather than refused -- see
HostLuaRun. No tier ships one since `runtime/canvas.py` was deleted.
"""

import ctypes
import os

from . import native_build

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = native_build.ROOT
_NATIVE = os.path.join(_ROOT, "native")
_LIBMOY = native_build.LIBMOY                        # the raster + moy.h
_BINDING_DIR = os.path.join(_NATIVE, "moycore", "libmoy")   # libmoy's Lua binding
_LUA = os.path.join(_NATIVE, "moy_lua", "lua")       # the vendored VM
_SHIM = os.path.join(_HERE, "moyhost_lua.c")
_CACHE = os.path.join(_ROOT, ".build", "host_lua")

# MOY_WITH_LUA compiles libmoy's binding at all; MOY_PIXEL_RGB565 is the boards'
# pixel, above. The Lua sources carry their own LUA_32BITS in luaconf.h, which
# is the point of using them rather than a system Lua: the host then wraps its
# integers where the boards wrap theirs.
_CFLAGS = native_build.BASE_CFLAGS + [
    "-DMOY_WITH_LUA=1", "-DMOY_PIXEL_RGB565=1",
    "-Wno-double-promotion", "-Wno-float-conversion",
]

# The sandbox's source set, matching the boards': the unused stdlibs -- and
# linit.c, whose luaL_openlibs references all of them -- stay out entirely, so
# there is no reachable implementation to be re-exposed by accident.
_LUA_SKIP = ("linit.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c",
             "lcorolib.c", "lutf8lib.c", "lua.c", "luac.c", "onelua.c")

_RASTER = ("moy.h", "moy_pixel.h", "moy_canvas.c", "moy_sprite.c", "moy_data.c")

_LIB = [None]


def _lua_names():
    """Every vendored Lua file this compiles, sources and headers alike.

    The headers are listed because `native_build` copies what it hashes into
    one self-contained build directory -- and because hashing them is the point:
    the module used to key its cache on the shim, the binding and moy.h, so a
    re-vendored VM (or a changed luaconf.h, which is where LUA_32BITS lives)
    kept serving the .so built from the previous drop.
    """
    if not os.path.isdir(_LUA):
        return []
    return sorted(n for n in os.listdir(_LUA)
                  if (n.endswith(".c") and n not in _LUA_SKIP)
                  or n.endswith(".h"))


def build(verbose=False):
    """Compile (or reuse) the cached .so; None when the pieces are absent."""
    lua = _lua_names()
    if not lua or not os.path.isfile(os.path.join(_BINDING_DIR, "moy_lua.c")):
        return None
    names = list(_RASTER) + ["moy_lua.c"] + lua
    return native_build.build(
        "moyhost_lua", _SHIM, names, _CACHE, cflags=_CFLAGS,
        libmoy_dir=(_LIBMOY, _BINDING_DIR, _LUA),
        # Lua's own math (pow/fmod/floor) -- a no-op against glibc >= 2.34,
        # which folded libm into libc, and required everywhere older.
        link_flags=["-lm"], verbose=verbose)


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
            # (pix, nbytes, w, h, indexed, wire, snap, aq, aq_cap)
            d.hl_new.argtypes = [_P, _I, _I, _I, _I, _P, _P, _P, _I]
            d.hl_new.restype = _P
            d.hl_set_sheet.argtypes = [_P, _P, _I]
            d.hl_set_map.argtypes = [_P, _P, _I, _I, _I]
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
    """One Lua cart run, in the same C the boards run.

    `buf` is the canvas's framebuffer, borrowed and never copied. TWO layouts
    are accepted and the difference is one argument:

    * **RGB565** (`indexed=False`) -- what the boards, the browser and
      `device_canvas.DeviceCanvas` hold: two bytes a pixel, colour resolved at
      draw time through `wire`, a 64-entry index -> 16-bit word table read off
      the canvas (`DeviceCanvas._wire`, which is byte-swapped on the T-Deck's
      panel and canonical elsewhere, and which a cart's own SPEC.md 3.1 palette
      rewrites). Omitting it leaves libmoy on the canonical 2.2 palette.
    * **INDEXED** (`indexed=True`) -- one byte a pixel. libmoy is compiled for
      direct colour and cannot write into that buffer, so the shim keeps a 565
      shadow with an IDENTITY wire table: every word it stores IS the palette
      index, and the frame is widened in and narrowed out. Lossless by
      construction rather than by a reverse lookup.

      NOTHING IN THE TREE PASSES THIS ANY MORE. It existed for the host's
      `runtime/canvas.py`, which was deleted 2026-08-15 when the sim moved onto
      the boards' canvas class; every tier is RGB565 now. Kept because it is the
      binding's own generic contract (and `len(buf)`-inferred, so a direct
      caller can still hand over an index buffer), not because a runtime uses
      it -- retiring it is a separate call, with `moyhost_lua.c`'s shadow.

    Left to itself the format is inferred from `len(buf)`, which is what the
    binding's own tests (and any other direct caller) rely on; `lua_host.py`
    passes it explicitly, from the canvas class it was handed.
    """

    AUDIO_MAX = 32

    @staticmethod
    def available():
        return _lib() is not None

    def __init__(self, buf, w, h, sheet=None, tilemap=None, wire=None,
                 indexed=None):
        d = _lib()
        if d is None:
            raise RuntimeError("no host lua binding")
        self._d = d
        self.buf = buf
        w, h = int(w), int(h)
        if indexed is None:
            indexed = len(buf) < w * h * 2
        self.indexed = bool(indexed)
        self.snap = (ctypes.c_int32 * SNAP_LEN)()
        self.aq = (ctypes.c_int32 * (1 + AQ_SLOTS * self.AUDIO_MAX))()
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        # Copied rather than borrowed: moy_canvas_wire copies it anyway, the
        # copy costs 64 assignments once per run, and it lets a caller hand
        # over any 64-length sequence (device_canvas holds an array("H"), the
        # module default is a tuple).
        self._wire = None
        if wire is not None and not self.indexed:
            if len(wire) != 64:
                raise ValueError("wire table must have 64 entries")
            self._wire = (ctypes.c_uint16 * 64)(*(int(c) & 0xFFFF for c in wire))
        self._r = d.hl_new(ctypes.cast(self._cbuf, _P), len(buf), w, h,
                           1 if self.indexed else 0,
                           None if self._wire is None else ctypes.cast(self._wire, _P),
                           ctypes.cast(self.snap, _P), ctypes.cast(self.aq, _P),
                           len(self.aq))
        if not self._r:
            # The C checks the buffer against w*h itself -- ctypes hands it a
            # bare pointer, so an undersized canvas would otherwise be a heap
            # overwrite with no Python-side trace.
            raise RuntimeError(
                "host lua: could not open a VM (canvas %dx%d needs %d bytes, "
                "got %d)" % (w, h, w * h * (1 if self.indexed else 2), len(buf)))
        self.snap[SNAP_PLAYERS] = 1
        self._sheet_ref = self._map_ref = None
        if sheet is not None:
            pix = sheet.pix if hasattr(sheet, "pix") else sheet
            if not isinstance(pix, bytearray):
                pix = bytearray(pix)
            self._sheet_ref = (ctypes.c_char * len(pix)).from_buffer(pix)
            d.hl_set_sheet(self._r, ctypes.cast(self._sheet_ref, _P), len(pix))
        if tilemap is not None:
            cells = tilemap.cells
            if not isinstance(cells, bytearray):
                cells = bytearray(cells)
            self._map_ref = (ctypes.c_char * len(cells)).from_buffer(cells)
            d.hl_set_map(self._r, ctypes.cast(self._map_ref, _P), len(cells),
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
