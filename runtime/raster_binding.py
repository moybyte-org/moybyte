"""Build + load the host's libmoy RASTER binding (moycore plan rung 5).

`runtime/canvas.py` is a pure-Python transcription of the raster libmoy
implements in C. The two are kept in agreement by the spec's conformance
goldens, which is a real pin -- but it is a pin over two implementations, and
the zero-duplication directive says the second one should stop existing. This
module is how libmoy's C reaches CPython so it can: it compiles the vendored
canvas/sprite/data sources plus `runtime/moyhost_raster.c` into one shared
library and loads it with ctypes, caching the `.so` under
`<repo>/.build/host_raster/` keyed by a hash of the sources, flags and
compiler. The same shape `runtime/audio_binding.py` already uses for the synth.

**Built INDEXED**, without `MOY_PIXEL_RGB565`, which is what makes the swap
cheap: a libmoy pixel is then one byte holding a palette index, byte-for-byte
what `Canvas.buf` already is. The library draws into the bytearray Python owns
-- no conversion, no copy, and every existing reader of `.buf` (to_rgb888, the
editors, blit_indices, the GIF writer) sees exactly what it saw before.

Absence is graceful, like lupa and like the audio binding: no compiler means
`NativeRaster.available()` is False and callers keep the Python raster. That
matters because this binding is not yet the shipping path -- it is proven
against the conformance goldens first (`tests/test_host_raster_binding.py`),
and only then is canvas.py's interior worth moving.

MicroPython never imports this file; the boards bind the same C natively.
"""

import ctypes
import hashlib
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_LIBMOY = os.path.join(_ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                       "native", "moy_gfx", "libmoy")
_SHIM = os.path.join(_HERE, "moyhost_raster.c")
_CACHE = os.path.join(_ROOT, ".build", "host_raster")

# -O2, and NOT -O3: this has to agree with the goldens pixel for pixel, and the
# raster is integer throughout, so there is nothing for a more aggressive
# optimizer to win that would be worth re-arguing conformance over.
_CFLAGS = ["-std=c99", "-O2", "-fPIC", "-shared"]

# (name, argtypes) for every shim export. Pinned rather than inferred so a
# mismatched call fails loudly instead of corrupting a stack.
_I = ctypes.c_int
_P = ctypes.c_void_p
_SIGS = (
    ("hr_free", [_P]),
    ("hr_retarget", [_P, _P, _I, _I]),
    ("hr_set_sheet", [_P, _P]),
    ("hr_set_map", [_P, _P, _I, _I]),
    ("hr_reset_state", [_P]),
    ("hr_cls", [_P, _I]),
    ("hr_pix", [_P, _I, _I, _I]),
    ("hr_line", [_P, _I, _I, _I, _I, _I]),
    ("hr_rect", [_P, _I, _I, _I, _I, _I]),
    ("hr_rectb", [_P, _I, _I, _I, _I, _I]),
    ("hr_circ", [_P, _I, _I, _I, _I]),
    ("hr_circb", [_P, _I, _I, _I, _I]),
    ("hr_tri", [_P, _I, _I, _I, _I, _I, _I, _I]),
    ("hr_trib", [_P, _I, _I, _I, _I, _I, _I, _I]),
    ("hr_print", [_P, _P, _I, _I, _I, _I]),
    ("hr_camera", [_P, _I, _I]),
    ("hr_camera_reset", [_P]),
    ("hr_clip", [_P, _I, _I, _I, _I]),
    ("hr_clip_reset", [_P]),
    ("hr_pal", [_P, _I, _I]),
    ("hr_pal_reset", [_P]),
    ("hr_palt", [_P, _I, _I]),
    ("hr_palt_reset", [_P]),
    ("hr_spr", [_P, _I, _I, _I, _I, _I, _I]),
    ("hr_sspr", [_P, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I]),
    ("hr_map", [_P, _I, _I, _I, _I, _I, _I, _I, _I]),
    ("hr_tline", [_P, _I, _I, _I, _I, _I, _I, _I, _I, _I]),
    ("hr_mget", [_P, _I, _I]),
    ("hr_mset", [_P, _I, _I, _I]),
    ("hr_peek", [_P, _I, _I]),
)

_LIB = [None]          # the loaded CDLL, or False once we know there isn't one


def _cc():
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


def _sources():
    out = {}
    for name in ("moy.h", "moy_pixel.h", "moy_canvas.c", "moy_sprite.c",
                 "moy_data.c"):
        with open(os.path.join(_LIBMOY, name)) as fh:
            out[name] = fh.read()
    with open(_SHIM) as fh:
        out["moyhost_raster.c"] = fh.read()
    return out


def _key(cc, sources):
    h = hashlib.sha256()
    for name in sorted(sources):
        h.update(name.encode())
        h.update(sources[name].encode())
    h.update(" ".join(_CFLAGS).encode())
    try:
        ver = subprocess.run([cc, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.splitlines()[:1]
        h.update((ver[0] if ver else "").encode())
    except Exception:   # noqa: BLE001 -- version only refines the cache key
        pass
    return h.hexdigest()[:16]


def build(verbose=False):
    """Compile (or reuse) the cached .so. None with no compiler; raises on a
    compile failure, which is a broken tree rather than an absent toolchain."""
    cc = _cc()
    if cc is None:
        return None
    sources = _sources()
    so_path = os.path.join(_CACHE, "moyhost_raster-%s.so" % _key(cc, sources))
    if os.path.exists(so_path):
        return so_path
    os.makedirs(_CACHE, exist_ok=True)
    src_dir = so_path[:-3] + ".src"
    os.makedirs(src_dir, exist_ok=True)
    for name, text in sources.items():
        with open(os.path.join(src_dir, name), "w") as fh:
            fh.write(text)
    tmp = so_path + ".tmp"
    cmd = [cc] + _CFLAGS + ["-I", src_dir] + [
        os.path.join(src_dir, n) for n in
        ("moyhost_raster.c", "moy_canvas.c", "moy_sprite.c", "moy_data.c")
    ] + ["-o", tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("moyhost_raster build failed:\n" + proc.stderr)
    os.replace(tmp, so_path)                    # atomic vs a parallel test run
    if verbose:
        print("moyhost_raster: built", os.path.relpath(so_path, _ROOT))
    return so_path


def _lib():
    if _LIB[0] is None:
        try:
            path = build()
        except Exception:   # noqa: BLE001 -- a broken build is not a crash here
            path = None
        if path is None:
            _LIB[0] = False
        else:
            d = ctypes.CDLL(path)
            d.hr_new.argtypes = [_P, _I, _I]
            d.hr_new.restype = _P
            for name, args in _SIGS:
                fn = getattr(d, name)
                fn.argtypes = args
                fn.restype = _I if name in ("hr_mget", "hr_peek") else None
            _LIB[0] = d
    return _LIB[0] or None


class NativeRaster:
    """libmoy's raster, drawing into a Python-owned index buffer.

    Presents the cart-facing verb names `runtime/canvas.Canvas` presents, so
    the spec's own trace replayer drives either one unchanged -- which is how
    this gets compared to the goldens without a second replayer to be wrong.
    """

    @staticmethod
    def available():
        return _lib() is not None

    def __init__(self, w, h, buf=None):
        d = _lib()
        if d is None:
            raise RuntimeError("no host raster binding (no C compiler)")
        self._d = d
        self.w, self.h = int(w), int(h)
        self.buf = bytearray(self.w * self.h) if buf is None else buf
        self._cbuf = (ctypes.c_char * len(self.buf)).from_buffer(self.buf)
        self._r = d.hr_new(ctypes.cast(self._cbuf, _P), self.w, self.h)
        if not self._r:
            raise MemoryError("host raster")
        self._sheet_ref = None
        self._map_ref = None
        self._map_wh = (0, 0)

    def close(self):
        if getattr(self, "_r", None):
            self._d.hr_free(self._r)
            self._r = None

    # -- assets -------------------------------------------------------------

    def set_sheet(self, sheet):
        """Point at a SpriteSheet's pixels. The buffer is held by reference:
        libmoy reads it during a draw and never copies it."""
        if sheet is None:
            self._sheet_ref = None
            self._d.hr_set_sheet(self._r, None)
            return
        pix = sheet.pix if hasattr(sheet, "pix") else sheet
        if not isinstance(pix, (bytearray, memoryview)):
            pix = bytearray(pix)
        self._sheet_ref = (ctypes.c_char * len(pix)).from_buffer(pix)
        self._d.hr_set_sheet(self._r, ctypes.cast(self._sheet_ref, _P))

    def set_map(self, tilemap):
        if tilemap is None:
            self._map_ref = None
            self._map_wh = (0, 0)
            self._d.hr_set_map(self._r, None, 0, 0)
            return
        cells = tilemap.cells
        if not isinstance(cells, (bytearray, memoryview)):
            cells = bytearray(cells)
        self._map_ref = (ctypes.c_char * len(cells)).from_buffer(cells)
        self._map_wh = (int(tilemap.w), int(tilemap.h))
        self._d.hr_set_map(self._r, ctypes.cast(self._map_ref, _P),
                           int(tilemap.w), int(tilemap.h))

    # -- the verbs ----------------------------------------------------------

    def reset_state(self):
        self._d.hr_reset_state(self._r)

    def cls(self, c=0):
        self._d.hr_cls(self._r, int(c))

    def pix(self, x, y, c=None):
        if c is None:
            return self._d.hr_peek(self._r, int(x), int(y))
        self._d.hr_pix(self._r, int(x), int(y), int(c))
        return None

    def line(self, x0, y0, x1, y1, c):
        self._d.hr_line(self._r, int(x0), int(y0), int(x1), int(y1), int(c))

    def rect(self, x, y, w, h, c):
        self._d.hr_rect(self._r, int(x), int(y), int(w), int(h), int(c))

    def rectb(self, x, y, w, h, c):
        self._d.hr_rectb(self._r, int(x), int(y), int(w), int(h), int(c))

    def circ(self, cx, cy, r, c):
        self._d.hr_circ(self._r, int(cx), int(cy), int(r), int(c))

    def circb(self, cx, cy, r, c):
        self._d.hr_circb(self._r, int(cx), int(cy), int(r), int(c))

    def tri(self, x1, y1, x2, y2, x3, y3, c):
        self._d.hr_tri(self._r, int(x1), int(y1), int(x2), int(y2),
                       int(x3), int(y3), int(c))

    def trib(self, x1, y1, x2, y2, x3, y3, c):
        self._d.hr_trib(self._r, int(x1), int(y1), int(x2), int(y2),
                        int(x3), int(y3), int(c))

    def print(self, s, x, y, c, scale=1):
        # BYTES, not characters (SPEC.md 6: one 8px cell per byte). The legacy
        # per-call scale is accepted and ignored, as on every other backend.
        if isinstance(s, (bytes, bytearray)):
            b = bytes(s)
        else:
            b = str(s).encode("utf-8")
        self._d.hr_print(self._r, b, len(b), int(x), int(y), int(c))

    def camera(self, x=None, y=None):
        if x is None and y is None:
            self._d.hr_camera_reset(self._r)
        else:
            self._d.hr_camera(self._r, int(x or 0), int(y or 0))

    def clip(self, x=None, y=None, w=None, h=None):
        if x is None:
            self._d.hr_clip_reset(self._r)
        else:
            self._d.hr_clip(self._r, int(x), int(y), int(w), int(h))

    def pal(self, c0=None, c1=None):
        if c0 is None:
            self._d.hr_pal_reset(self._r)
        else:
            self._d.hr_pal(self._r, int(c0), int(c1))

    def palt(self, c=None, on=None):
        if c is None:
            self._d.hr_palt_reset(self._r)
        else:
            self._d.hr_palt(self._r, int(c), 1 if on else 0)

    def spr(self, n, x, y, colorkey=-1, scale=1, flip=0):
        self._d.hr_spr(self._r, int(n), int(x), int(y), int(colorkey),
                       int(scale), int(flip))

    def sspr(self, sx, sy, sw, sh, dx, dy, dw=None, dh=None,
             colorkey=-1, flip=0):
        self._d.hr_sspr(self._r, int(sx), int(sy), int(sw), int(sh),
                        int(dx), int(dy),
                        int(sw if dw is None else dw),
                        int(sh if dh is None else dh),
                        int(colorkey), int(flip))

    def map(self, mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # SPEC.md's defaults are "the rest of the map from (mx, my)", which the
        # binding resolves here rather than in C: the C side would need the map
        # dimensions a second time to do it, and one place that knows them is
        # the point of holding them.
        mw, mh = self._map_wh
        self._d.hr_map(self._r, int(mx), int(my),
                       int(mw - mx if w is None else w),
                       int(mh - my if h is None else h),
                       int(sx), int(sy), int(colorkey), int(scale))

    def tline(self, x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        self._d.hr_tline(self._r, int(x0), int(y0), int(x1), int(y1),
                         int(u), int(v), int(du), int(dv), int(colorkey))

    def mget(self, x, y):
        return self._d.hr_mget(self._r, int(x), int(y))

    def mset(self, x, y, tile):
        self._d.hr_mset(self._r, int(x), int(y), int(tile))
