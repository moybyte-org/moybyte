"""The host's Lua run and the kernel's Player, by ctypes.

CPython runs a Lua cart through the C a board runs, not a twin of it: the VM
is native/moycore/moycore_lua.c over moycore_run.c's console -- libmoy's verb
table over the vendored Lua 5.4 with `LUA_32BITS`, the small-object pool, the
layers, paint images and scenes in C -- and its frames are the kernel's Player
(native/moy_play/moy_play.c). `runtime/moyhost_lua.c` is the binding's half
(modmoycore.c's, on a board) and says what differs: plain C signatures, the
kernel's stateful modules forwarded to the host's other ctypes libraries
(`kernel` wires them), and a paint image decoded by the Python codec.

ONE RUN AT A TIME, as on a board: a new HostLuaRun closes the one before it,
and a closed run answers nothing. `runtime/moycore.py` is the module surface the
boards' glue (device/moycore_glue.py) drives, over this.

PIXELS ARE RGB565, as on both boards: the canvas's 64-entry wire table says
what an index looks like (`DeviceCanvas._wire`, byte-swapped on the T-Deck's
panel, rewritten by a cart's SPEC.md 3.1 palette). No compiler means
`available()` is False and there are no Lua carts on the host at all, exactly
as a device build without the native module has none.
"""

import ctypes
import os

from . import native_build
from .lua_ext import cfg_blob
from .moyimg import decode_moyimg

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = native_build.ROOT
_NATIVE = os.path.join(_ROOT, "native")
_LIBMOY = native_build.LIBMOY                        # the raster + moy.h
_BINDING_DIR = os.path.join(_NATIVE, "moycore", "libmoy")   # libmoy's Lua binding
_MOYCORE = os.path.join(_NATIVE, "moycore")          # the layer glue the boards run
_MOY_GFX = os.path.join(_NATIVE, "moy_gfx")          # the layer restore's kernels
_SPINE = os.path.join(_NATIVE, "moy_spine")          # the JSON scanner, the handle tables
_PLAY = os.path.join(_NATIVE, "moy_play")            # the kernel's Player
_STORE = os.path.join(_NATIVE, "moy_store")          # the catalogue a launch reads
_KERNEL = os.path.join(_NATIVE, "moy_kernel")        # the loop's upcall classes
_INPUT = os.path.join(_NATIVE, "moy_input")
_AUDIO = os.path.join(_NATIVE, "moy_audio")
_GLASS = os.path.join(_NATIVE, "moy_glass")
_LUA = os.path.join(_NATIVE, "moy_lua", "lua")       # the vendored VM
_SHIM = os.path.join(_HERE, "moyhost_lua.c")
_CACHE = os.path.join(_ROOT, ".build", "host_lua")

# MOY_WITH_LUA compiles libmoy's binding at all; MOY_PIXEL_RGB565 is the boards'
# pixel, above. The Lua sources carry their own LUA_32BITS in luaconf.h, which
# is the point of using them rather than a system Lua: the host then wraps its
# integers where the boards wrap theirs.
_CFLAGS = native_build.BASE_CFLAGS + [
    "-DMOY_WITH_LUA=1", "-DMOY_PIXEL_RGB565=1", "-DMOY_PLAY_HOST=1",
    "-Wno-double-promotion", "-Wno-float-conversion",
]

# The sandbox's source set, matching the boards': the unused stdlibs -- and
# linit.c, whose luaL_openlibs references all of them -- stay out entirely, so
# there is no reachable implementation to be re-exposed by accident.
_LUA_SKIP = ("linit.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c",
             "lutf8lib.c", "lua.c", "luac.c", "onelua.c")

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


def build(verbose=False, wasm=None):
    """Compile (or reuse) the cached .so; None when the pieces are absent.

    Where WAMR builds for the host (runtime/wasm_binding.wamr), the library
    carries the compiled-cart run too (runtime/moyhost_wasm.c), so a compiled
    cart's frames are the same Player's; `wasm` False leaves it out."""
    lua = _lua_names()
    if not lua or not os.path.isfile(os.path.join(_BINDING_DIR, "moy_lua.c")):
        return None
    names = (list(_RASTER) + [
        "moy_lua.c", "moy_p8.c", "moycore_run.h", "moycore_run.c", "moycore_lua.h",
        "moycore_lua.c", "moycore_layers.h", "moycore_superset.h", "moycore_scene.h",
        "moy_gfx_kernels.h", "moy_gfx_kernels.c",
        "moy_json.h", "moy_json.c", "moy_htab.h", "moy_htab.c",
        "moy_play.h", "moy_play.c", "moy_play_rule.c", "moy_rt.h", "moy_rt.c",
        "moy_tick.h", "moy_tick.c", "moy_match.h", "moy_match.c", "moy_match_kernel.c",
        "moy_crash.h", "moy_chrome.h", "moy_chrome.c",
        "moy_cat.h", "moy_cat.c", "moy_load.h", "moy_arena.h", "moy_vol.h", "moy_vol.c",
        "moy_fs.h", "moy_fs.c", "moy_store_host.c", "moy_img.h",
        "moy_loop.h", "moy_idle.h", "moy_perf.h", "moy_input.h", "moy_aud.h", "moy_buf.h",
    ] + lua)
    cflags = list(_CFLAGS)
    link = ["-lm"]
    dirs = (_LIBMOY, _BINDING_DIR, _LUA, _HERE, _MOYCORE, _MOY_GFX, _SPINE,
            _PLAY, _STORE, _KERNEL, _INPUT, _AUDIO, _GLASS)
    got = None
    if wasm is not False:
        from . import wasm_binding
        got = wasm_binding.wamr(verbose)
    compile_names = None
    name = "moyhost_lua"
    if got is not None:
        inc, lib = got
        names += ["moy_wasm.c", "moy_wasm.h", "moy_wasm_footprint.h", "moy_audio.c",
                  "moy_audio.h", "moy_files.h", "moy_files.c", "moyhost_wasm.c"]
        cflags += ["-DMOY_WASM=1", "-isystem", inc,
                   "-DMOYHOST_WAMR_PIN=%s" % wasm_binding.pin()]
        link = [lib, "-lm", "-lpthread", "-ldl"]
        dirs += (wasm_binding._ENGINE_DIR, wasm_binding._AUDIO_DIR)
        compile_names = [os.path.basename(_SHIM)] + [n for n in names if n.endswith(".c")]
        name = "moyhost_play"
    return native_build.build(
        name, _SHIM, names, _CACHE, cflags=cflags, compile_names=compile_names,
        libmoy_dir=dirs, link_flags=link, verbose=verbose)


_I, _P, _F, _C = ctypes.c_int, ctypes.c_void_p, ctypes.c_float, ctypes.c_char_p
_U32 = ctypes.c_uint32

_SIGS = (
    ("hl_kernel", [_I, _P], None),
    ("hl_img_decoder", [_P], None),
    ("hl_new", [_P, _I, _I, _I, _P, _P, _P, _I], _P),
    ("hl_open", [_P, _P, _I], _I),
    ("hl_set_sheet", [_P, _P, _I], None),
    ("hl_set_map", [_P, _P, _I, _I, _I], None),
    ("hl_set_flags", [_P, _P, _I], None),
    ("hl_set_cfg", [_P, _P, _I], None),
    ("hl_retarget", [_P, _P], None),
    ("hl_set_dispatch", [_P, _P], None),
    ("hl_register", [_P, _C, _I], None),
    ("hl_image_put", [_P, _C, _C, _I], _I),
    ("hl_scene_put", [_P, _C, _C, _I], _I),
    ("hl_layer_bind", [_P, _P, _I, _I, _I], _I),
    ("hl_layer_restore", [_P, _P], None),
    ("hl_layer_pixels", [_P, _I, ctypes.POINTER(_P), ctypes.POINTER(_I), ctypes.POINTER(_I)], _I),
    ("hl_exec", [_P, _C, _I, _C, _P, _I], _I),
    ("hl_load", [_P, ctypes.POINTER(_C), ctypes.POINTER(_I), ctypes.POINTER(_C), _I,
                 _P, _I], _I),
    ("hl_tick", [_P, _F, _I, _P, _I], _I),
    ("hl_pmem_image", [_P, _P, _I], _I),
    ("hl_pmem_load", [_P, _P, _I], None),
    ("hl_get_global_num", [_P, _C, ctypes.POINTER(ctypes.c_double)], _I),
    ("hl_get_global_str", [_P, _C, _P, _I], _I),
    ("hl_get_global_len", [_P, _C], _I),
    ("hl_heap_bytes", [_P], _I),
    ("hl_heap_peak_bytes", [_P], _I),
    ("hl_get_view", [_P, ctypes.POINTER(_I), ctypes.POINTER(_I)], _I),
    ("hl_split", [ctypes.POINTER(_U32), ctypes.POINTER(_U32)], None),
    ("hl_free", [_P], None),
    ("hl_play_rows", [_I], None),
    ("hl_play_launch", [_C, _I, ctypes.POINTER(_U32)], _I),
    ("hl_play_bind", [_U32, _P, _U32, _P], _I),
    ("hl_play_open", [_U32], _I),
    ("hl_play_frame", [_U32, _I, _F, _I, _I, _I, _I, ctypes.POINTER(_U32)], _I),
    ("hl_play_end", [_U32, _I], _I),
    ("hl_play_info", [_U32, _P], _I),
    ("hl_play_last", [], _U32),
    ("hl_play_info_size", [], ctypes.c_size_t),
)

# hl_kernel's slots: which host library function each forwarder calls.
(HK_COUNT, HK_UPCALLS, HK_MASKS, HK_PLAYERS, HK_LAST_KEY, HK_TICK_EDGES,
 HK_SFX, HK_BEEP, HK_MUSIC, HK_MUSIC_STOP, HK_STOP, HK_LEVEL) = range(12)

# A paint image's decode, by the Python codec (moyhost_lua.c says why).
_IMG_FN = ctypes.CFUNCTYPE(_I, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.POINTER(_U32), ctypes.POINTER(_U32))


def _img_decode(text, n, pix, cap, w, h):
    try:
        got = decode_moyimg(ctypes.string_at(text, n).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 -- not a picture
        return -1
    if got is None:
        return -1
    iw, ih, idx = got
    w[0], h[0] = iw, ih
    if pix:
        if cap < iw * ih + 1:
            return -2
        ctypes.memmove(pix, bytes(idx), iw * ih)
    return 0


_IMG_CB = _IMG_FN(_img_decode)


def _fn(lib, name):
    return ctypes.cast(getattr(lib, name), _P).value


def kernel(d):
    """Wire the Player's forwarders to the host's own libraries: the loop's
    upcall counters, the input tables, the audio sessions. A library the host
    cannot build stays unwired: a counter that counts nothing, a session that
    is silent."""
    try:
        from . import moy_loop
        lp = moy_loop._lib()
        d.hl_kernel(HK_COUNT, _fn(lp, "moy_loop_count"))
        d.hl_kernel(HK_UPCALLS, _fn(lp, "moy_loop_upcalls"))
    except Exception:  # noqa: BLE001 -- no loop library: no books
        pass
    try:
        from . import moy_input
        ip = moy_input._lib()
        for slot, name in ((HK_MASKS, "moy_input_masks"), (HK_PLAYERS, "moy_input_players"),
                           (HK_LAST_KEY, "moy_input_last_key"),
                           (HK_TICK_EDGES, "moy_input_tick_edges")):
            d.hl_kernel(slot, _fn(ip, name))
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import audio_binding
        ap = audio_binding._lib()
        for slot, name in ((HK_SFX, "moy_aud_sfx"), (HK_BEEP, "moy_aud_beep"),
                           (HK_MUSIC, "moy_aud_music"), (HK_MUSIC_STOP, "moy_aud_music_stop"),
                           (HK_STOP, "moy_aud_stop"), (HK_LEVEL, "moy_aud_level")):
            d.hl_kernel(slot, _fn(ap, name))
    except Exception:  # noqa: BLE001 -- no audio library: silence
        pass


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
            # Every argtype explicit: a function left without them takes the
            # default int conversion, which truncates a 64-bit pointer -- a
            # segfault, not a TypeError.
            for name, args, res in _SIGS:
                fn = getattr(d, name)
                fn.argtypes = args
                fn.restype = res
            if d.hl_play_info_size() != ctypes.sizeof(PlayInfo):
                raise ImportError("moy_play_info_t's layout is not this file's")
            d.hl_img_decoder(ctypes.cast(_IMG_CB, _P))
            d.hl_play_rows(1 if hasattr(d, "hw_runtime") else 0)
            kernel(d)
            _LIB[0] = d
    return _LIB[0] or None


SNAP_LEN = 14
SNAP_BTN, SNAP_BTNP, SNAP_BTN_P1, SNAP_BTNP_P1 = 0, 1, 2, 3
SNAP_PLAYERS, SNAP_TIME_MS = 4, 5
SNAP_TOUCH_X, SNAP_TOUCH_Y, SNAP_TOUCH_DOWN, SNAP_TOUCH_MS = 6, 7, 8, 9
SNAP_KEY, SNAP_KEY_DOWN, SNAP_TEXTMODE, SNAP_QUIT = 10, 11, 12, 13
AQ_SLOTS = 4
AQ_MAX = 32
AQ_SFX, AQ_MUSIC, AQ_BEEP, AQ_MUSIC_STOP, AQ_SOUND_STOP, AQ_VOLUME = range(6)


class PlayInfo(ctypes.Structure):
    """moy_play_info_t (native/moy_play/moy_play.h)."""
    _fields_ = [("runtime", ctypes.c_char * 12), ("error", ctypes.c_char * 192),
                ("frames", _U32), ("ticks", _U32), ("upcalls", _U32 * 5),
                ("stack_open", _U32), ("stack_frame", _U32),
                ("why", ctypes.c_uint8), ("end_why", ctypes.c_uint8),
                ("stop_why", ctypes.c_uint8), ("vm_down", ctypes.c_bool),
                ("vm_free", ctypes.c_bool), ("raised", ctypes.c_bool),
                ("ended", ctypes.c_bool), ("game", ctypes.c_bool),
                ("title", ctypes.c_char * 48), ("id", ctypes.c_char * 24),
                ("fit", _U32 * 5), ("stuck", ctypes.c_bool)]


class HostLuaRun:
    """The host's one Lua cart run, in the C a board runs (the module's
    docstring). `buf` is the canvas's RGB565 framebuffer, borrowed and never
    copied; `wire` its 64-entry index -> word table (None: libmoy's canonical
    palette). The sheet, map, flags and config are set before the VM opens,
    because the p8 machine seeds from them as it opens."""

    AUDIO_MAX = AQ_MAX

    @staticmethod
    def available():
        return _lib() is not None

    def __init__(self, buf, w, h, sheet=None, tilemap=None, wire=None,
                 indexed=None, flags=None, cfg=None, snap=None, aq=None):
        d = _lib()
        if d is None:
            raise RuntimeError("no host lua binding")
        self._d = d
        self.buf = buf
        w, h = int(w), int(h)
        if indexed:
            raise ValueError("host lua: the console is RGB565; an indexed canvas has no run")
        self.indexed = False
        # The snapshot and the audio queue: the caller's int32 arrays (the
        # boards' glue owns them, runtime/moycore.py), or the run's own.
        self.snap = (ctypes.c_int32 * SNAP_LEN)() if snap is None else \
            (ctypes.c_int32 * len(snap)).from_buffer(snap)
        self.aq = (ctypes.c_int32 * (1 + AQ_SLOTS * self.AUDIO_MAX))() if aq is None else \
            (ctypes.c_int32 * len(aq)).from_buffer(aq)
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        self._wire = None
        if wire is not None:
            if len(wire) != 64:
                raise ValueError("wire table must have 64 entries")
            self._wire = (ctypes.c_uint16 * 64)(*(int(c) & 0xFFFF for c in wire))
        self._r = d.hl_new(ctypes.cast(self._cbuf, _P), len(buf), w, h,
                           None if self._wire is None else ctypes.cast(self._wire, _P),
                           ctypes.cast(self.snap, _P), ctypes.cast(self.aq, _P),
                           len(self.aq))
        if not self._r:
            # The C checks the buffer against w*h itself: ctypes hands it a bare
            # pointer, so an undersized canvas would be a heap overwrite.
            raise RuntimeError(
                "host lua: could not open a run (canvas %dx%d needs %d bytes, "
                "got %d)" % (w, h, w * h * 2, len(buf)))
        if snap is None:
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
        if flags is not None:
            blob = bytes(flags)              # COPIED on the C side
            d.hl_set_flags(self._r, ctypes.c_char_p(blob), len(blob))
        # The config as lua_ext.cfg_blob's pairs: a dict is encoded here, a blob
        # (the boards' glue hands one over) is taken as it is.
        blob = cfg if isinstance(cfg, (bytes, bytearray)) else cfg_blob(cfg)
        if blob:
            d.hl_set_cfg(self._r, ctypes.c_char_p(blob), len(blob))
        err = ctypes.create_string_buffer(128)
        if d.hl_open(self._r, ctypes.cast(err, _P), 128):
            msg = err.value.decode("utf-8", "replace")
            self.close()
            raise RuntimeError("host lua: %s" % (msg or "no VM"))

    # The dispatch callback's C signature; kept alive on the instance because
    # ctypes collects a CFUNCTYPE object the C side still holds.
    _DISPATCH = ctypes.CFUNCTYPE(_I, _I, _I, ctypes.POINTER(_I),
                                 ctypes.POINTER(_I), ctypes.POINTER(_C),
                                 ctypes.POINTER(_I), ctypes.POINTER(_C))
    KIND_NUM, KIND_STR, KIND_BOOL, KIND_NIL = 0, 1, 2, 3

    def register(self, name, fn):
        """Add a verb libmoy does not bind, counted APP when the cart calls
        it. After __init__, before load()."""
        if not hasattr(self, "_ext"):
            self._ext = []
            self._cb = self._DISPATCH(self._dispatch)
            self._d.hl_set_dispatch(self._r, ctypes.cast(self._cb, _P))
        idx = len(self._ext)
        self._ext.append(fn)
        self._d.hl_register(self._r, name.encode(), idx)

    def image_put(self, name, text):
        """A paint image's .moyimg text for the run's image(name), decoded in
        the run at its first call (moycore_lua.c). Before load()."""
        raw = text.encode() if isinstance(text, str) else bytes(text)
        if self._d.hl_image_put(self._r, str(name).encode(), raw, len(raw)) != 0:
            raise MemoryError("image_put: the run holds its images")

    def scene_put(self, name, text):
        """A scene's .moyscene text for the run's scene verbs, which parse
        and draw it in C (native/moycore/moycore_scene.h). Before load(), in
        the cart's scene order."""
        raw = text.encode() if isinstance(text, str) else bytes(text)
        if self._d.hl_scene_put(self._r, str(name).encode(), raw, len(raw)) != 0:
            raise MemoryError("scene_put: the run holds its scenes")

    def layer_bind(self, buf, w, h):
        """Park a layer's RGB565 buffer for the prelude's next make_layer
        canvas (moycore_layers.h). The run keeps it exported until it
        closes."""
        ref = (ctypes.c_char * len(buf)).from_buffer(buf)
        if self._d.hl_layer_bind(self._r, ctypes.cast(ref, _P), len(buf),
                                 int(w), int(h)) != 0:
            raise ValueError("layer_bind: buffer too small")
        if not hasattr(self, "_layer_refs"):
            self._layer_refs = []
        self._layer_refs.append(ref)

    def layer(self, i):
        """Layer `i` as the cart made it: `.w`, `.h` and `._buf`, a live view
        of its RGB565 pixels in the run (a harness's window; None for no such
        layer)."""
        pix, w, h = _P(), _I(0), _I(0)
        if self._d.hl_layer_pixels(self._r, int(i), ctypes.byref(pix),
                                   ctypes.byref(w), ctypes.byref(h)) != 0:
            return None

        class _Layer:
            pass

        lay = _Layer()
        lay.w, lay.h = w.value, h.value
        lay._buf = (ctypes.c_ubyte * (w.value * h.value * 2)).from_address(pix.value)
        return lay

    def layer_restore(self, state):
        """draw_layer through the screen canvas's layer restore state (its
        `_lrs`), or None for libmoy's plain copy. The run keeps it exported
        until it closes or is handed another."""
        ref = None
        if state is not None:
            ref = (ctypes.c_char * len(state)).from_buffer(state)
        self._d.hl_layer_restore(self._r, ctypes.cast(ref, _P) if ref is not None else None)
        self._lrs_ref = ref

    def _dispatch(self, idx, argc, kinds, iargs, sargs, out, sout):
        """C -> Python. 0 nil, 1 the int in out, 2 the string, 3 the boolean."""
        try:
            fn = self._ext[idx]
            args = []
            for i in range(argc):
                k = kinds[i]
                if k == self.KIND_STR:
                    args.append(sargs[i].decode("utf-8", "replace"))
                elif k == self.KIND_BOOL:
                    args.append(bool(iargs[i]))
                elif k == self.KIND_NIL:
                    args.append(None)
                else:
                    args.append(int(iargs[i]))
            r = fn(*args)
        except Exception:  # noqa: BLE001 -- a raising verb reads as nil, and
            return 0       # the console's own error path reports it
        if r is None:
            return 0
        if r is True or r is False:
            out[0] = 1 if r else 0
            return 3
        if isinstance(r, str):
            # PINNED on the instance: sout[0] points into this bytes object
            # until hl_tramp copies it, during this same call.
            self._sret = r.encode("utf-8")
            sout[0] = self._sret
            out[0] = len(self._sret)
            return 2
        try:
            out[0] = int(r)
        except (TypeError, ValueError):
            return 0
        return 1

    def _err(self, rc, err):
        return err.value.decode("utf-8", "replace") if rc else None

    def exec(self, src, name="glue"):
        """Run a chunk that is NOT the cart -- the glue prelude. None, or the
        error text."""
        err = ctypes.create_string_buffer(256)
        b = src.encode("utf-8") if isinstance(src, str) else bytes(src)
        return self._err(self._d.hl_exec(self._r, b, len(b), name.encode(),
                                         ctypes.cast(err, _P), 256), err)

    def load(self, chunks):
        """The cart's chunks in order, then `_init`: None, or the error.
        `chunks` is [(src, chunkname), ...], SPEC.md 4's `sources`."""
        n = len(chunks)
        bufs = [s.encode("utf-8") if isinstance(s, str) else bytes(s)
                for s, _ in chunks]
        srcs = (_C * n)(*bufs)
        lens = (_I * n)(*[len(b) for b in bufs])
        names = (_C * n)(*[nm.encode() for _, nm in chunks])
        err = ctypes.create_string_buffer(256)
        return self._err(self._d.hl_load(self._r, srcs, lens, names, n,
                                         ctypes.cast(err, _P), 256), err)

    def tick(self, dt, draw=True):
        """One frame: _update, then _draw unless `draw` is False (a logic-only
        tick, #217). None, or the error text."""
        err = ctypes.create_string_buffer(256)
        return self._err(self._d.hl_tick(self._r, ctypes.c_float(dt), 1 if draw else 0,
                                         ctypes.cast(err, _P), 256), err)

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

    def retarget(self, buf):
        """Point the run at another framebuffer of the same size."""
        self._cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        self.buf = buf
        self._d.hl_retarget(self._r, ctypes.cast(self._cbuf, _P))

    def pmem_load(self, cells):
        img = (ctypes.c_int32 * 256)(*[int(c) for c in list(cells)[:256]])
        self._d.hl_pmem_load(self._r, ctypes.cast(img, _P), 256)

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

    def get_global(self, name):
        """A cart global as a number or a string, or None."""
        v = ctypes.c_double(0.0)
        if self._d.hl_get_global_num(self._r, name.encode(), ctypes.byref(v)):
            f = v.value
            return int(f) if f == int(f) else f
        n = self._d.hl_get_global_str(self._r, name.encode(), None, 0)
        if n >= 0:
            out = ctypes.create_string_buffer(max(n, 1))
            self._d.hl_get_global_str(self._r, name.encode(), ctypes.cast(out, _P), n)
            return out.raw[:n].decode("utf-8", "replace")
        return None

    def get_global_len(self, name):
        """The length of a table global (Lua's #t), or None."""
        n = self._d.hl_get_global_len(self._r, name.encode())
        return None if n < 0 else n

    def close(self):
        if getattr(self, "_r", None):
            self._d.hl_free(self._r)
            self._r = None
        self._layer_refs = []


# -- the kernel's Player (native/moy_play/moy_play.h) ------------------------------

def play_lib():
    """The library the Player is in: the Lua run's, or None."""
    return _lib()
