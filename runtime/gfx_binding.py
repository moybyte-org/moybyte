"""moy_gfx's compositor surface, on CPython, over `moyhost_gfx.c`.

`device_canvas.py` is the raster on the boards and in the browser, and it
reaches its kernel through a lazy `import moy_gfx`. This module is that kernel
for the host, so the same canvas class can run here -- which is what lets the
two canvas implementations become one instead of being kept in agreement by
hand.

Function names, argument order and return values match the native module
EXACTLY, because the whole point is that `device_canvas.py` does not know which
one it got. `tests/test_gfx_binding.py` diffs this surface against the verbs
device_canvas actually calls, so a signature that drifts fails there rather
than at a call site months later.

BUFFERS. The native module takes MicroPython buffer objects and derives their
capacity through `moy_gfx_buf_w`; ctypes hands over a bare pointer, so capacity
travels as an explicit argument and the C keeps every bound it had. Buffers are
borrowed, never copied -- `from_buffer` shares the bytearray's memory, so a blit
writes into the caller's framebuffer exactly as the native module does. The
ctypes array is held in a local across each call because letting it fall out of
scope would release the buffer export mid-flight.
"""

from __future__ import annotations

import ctypes
import os

from . import native_build

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHIM = os.path.join(_HERE, "moyhost_gfx.c")
_CACHE = os.path.join(native_build.ROOT, ".build", "host_gfx")

# RGB565, matching the boards: -DMOY_PIXEL_RGB565=1 is what makes a libmoy pixel
# two bytes instead of one. The shim itself has no libmoy dependency yet (these
# verbs are moy_gfx's own compositor), but the flag is set here so that adding
# the sprite-backed verbs later cannot accidentally compile them indexed.
_CFLAGS = native_build.BASE_CFLAGS + ["-DMOY_PIXEL_RGB565=1"]

_LIB = [None]

_I = ctypes.c_int
_Z = ctypes.c_size_t
_P = ctypes.c_void_p

_SIGS = (
    ("hg_fill", [_P, _Z, _I, _I], None),
    ("hg_fill_rect", [_P, _Z, _I, _I, _I, _I, _I, _I], None),
    ("hg_scroll_rect", [_P, _Z, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_blit565", [_P, _Z, _I, _I, _I, _I, _P, _Z, _I, _I, _I,
                    _I, _I, _I, _I], None),
    ("hg_blit_window", [_P, _Z, _I, _I, _P, _Z, _I, _I, _I], None),
    ("hg_copy_async", [_P, _Z, _I, _P, _Z, _I, _I], _I),
    ("hg_copy_wait", [], _I),
)


def build(verbose=False):
    return native_build.build("moyhost_gfx", _SHIM, (), _CACHE,
                              cflags=_CFLAGS, compile_names=["moyhost_gfx.c"],
                              verbose=verbose)


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
            for name, args, res in _SIGS:
                fn = getattr(d, name)
                fn.argtypes = args
                fn.restype = res
            _LIB[0] = d
    return _LIB[0] or None


def available():
    return _lib() is not None


def _buf(b):
    """(ctypes array, pixel capacity) sharing `b`'s memory -- no copy.

    Returned as a pair so the caller can keep the array alive for the duration
    of the call: a bytearray's buffer export is released when this object is
    collected, and a pointer taken from a released export is a use-after-free
    waiting for a garbage collection to land in the wrong place.
    """
    arr = (ctypes.c_char * len(b)).from_buffer(b)
    return arr, len(b) // 2


def _rbuf(b):
    """Read-only variant: bytes and read-only memoryviews refuse from_buffer."""
    try:
        return _buf(b)
    except TypeError:
        arr = (ctypes.c_char * len(b)).from_buffer_copy(b)
        return arr, len(b) // 2


# -- the surface device_canvas calls -----------------------------------------


def fill(buf, npix, color):
    arr, cap = _buf(buf)
    _lib().hg_fill(ctypes.cast(arr, _P), cap, int(npix), int(color) & 0xFFFF)


def fill_rect(buf, stride, x, y, w, h, color):
    arr, cap = _buf(buf)
    _lib().hg_fill_rect(ctypes.cast(arr, _P), cap, int(stride), int(x), int(y),
                        int(w), int(h), int(color) & 0xFFFF)


def scroll_rect(buf, stride, rx, ry, rw, rh, dx, dy):
    arr, cap = _buf(buf)
    _lib().hg_scroll_rect(ctypes.cast(arr, _P), cap, int(stride), int(rx),
                          int(ry), int(rw), int(rh), int(dx), int(dy))


def blit565(dst, dw, dh, dx, dy, src, sw, sh, key,
            cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    sarr, scap = _rbuf(src)
    _lib().hg_blit565(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                      int(dx), int(dy),
                      ctypes.cast(sarr, _P), scap, int(sw), int(sh), int(key),
                      int(cx0), int(cy0),
                      int(dw) if cx1 is None else int(cx1),
                      int(dh) if cy1 is None else int(cy1))


def blit_window(dst, dw, dh, src, src_w, sx, sy):
    darr, dcap = _buf(dst)
    sarr, scap = _rbuf(src)
    _lib().hg_blit_window(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                          ctypes.cast(sarr, _P), scap, int(src_w),
                          int(sx), int(sy))


def copy_async(dst, dst_off, src, src_off, npix):
    """Always False on the host: no GDMA, so the caller takes its sync path --
    the same branch a board takes when its DMA driver declines."""
    return False


def copy_wait():
    return True
