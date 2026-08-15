"""moy_gfx's compositor surface, on CPython, over `moyhost_gfx.c`.

`device_canvas.py` is the raster on the boards and in the browser, and it
reaches its kernel through a lazy `import moy_gfx`. This module is that kernel
for the host, so the same canvas class can run here -- which is what lets the
two canvas implementations become one instead of being kept in agreement by
hand.

Function names, argument order and return values match the native module
EXACTLY, because the whole point is that `device_canvas.py` does not know which
one it got. `tests/test_gfx_binding.py` pins that two ways: it reads the verb
names `device_canvas.py` actually reaches for out of its source and asserts each
one exists here (so a verb the canvas calls and this surface lacks fails on the
day it is added, not at a call site months later), and it drives 131 ops through
both this binding and the REAL native module, comparing byte for byte.

TWO C PREFIXES, AND THE DIFFERENCE MATTERS. `mg_*` is
`native/moy_gfx/moy_gfx_kernels.c` -- the compositor, ONE copy, linked by this
shim and compiled into the MicroPython usermod. `hg_*` is `moyhost_gfx.c`, and
after the extraction it holds only what is genuinely host-side: the libmoy
bridge verbs (whose marshalling differs from the usermod's because ctypes and
MicroPython hand over arguments differently) and the async-copy refusal. A new
compositor loop belongs in the kernels file, where it gets an `mg_` name and
both tiers get it at once.

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

# The native module's own directory, searched alongside the vendored libmoy one:
# the compositor kernel is moy_gfx's, not libmoy's, and it lives with the
# usermod that also compiles it.
_MOY_GFX = os.path.dirname(native_build.LIBMOY)

# RGB565, matching the boards. This define is the whole difference between this
# library and the indexed one next door: it makes a libmoy pixel two bytes
# instead of one, changing sizeof(moy_pixel) and the layout of every struct.
# Compiling the sprite verbs without it would produce a library that links and
# then draws garbage.
_CFLAGS = native_build.BASE_CFLAGS + ["-DMOY_PIXEL_RGB565=1"]

# The sources compiled beside the shim. The first three are vendored libmoy --
# blit_batch, blit_map and the shape verbs are not pure compositing, they draw
# SPRITES, and sprites are libmoy's. The last two are moy_gfx's own compositor,
# out of the native module's directory: the SAME FILE the boards build, which is
# the whole point of the extraction. read_sources() searches both directories
# and refuses a name that appears in each, so this cannot silently pick up a
# stale copy.
_LIBMOY = ("moy.h", "moy_pixel.h", "moy_canvas.c", "moy_sprite.c", "moy_data.c",
           "moy_gfx_kernels.h", "moy_gfx_kernels.c")

# ...and where to find them. One tuple rather than a literal at the call site,
# because a caller that reconstructs the build (tests/test_native_build_no_
# compiler.py does) must search the same directories or it fails on a source it
# cannot find and reports it as a toolchain problem.
_SRC_DIRS = (native_build.LIBMOY, _MOY_GFX)

_LIB = [None]

_I = ctypes.c_int
_Z = ctypes.c_size_t
_P = ctypes.c_void_p

_SIGS = (
    # The shared compositor (native/moy_gfx/moy_gfx_kernels.c) -- the same
    # symbols the usermod's MicroPython wrappers call, reached here with no
    # forwarder in between.
    ("mg_fill", [_P, _Z, _I, _I], None),
    ("mg_fill_rect", [_P, _Z, _I, _I, _I, _I, _I, _I], None),
    ("mg_scroll_rect", [_P, _Z, _I, _I, _I, _I, _I, _I, _I], None),
    ("mg_blit565", [_P, _Z, _I, _I, _I, _I, _P, _Z, _I, _I, _I,
                    _I, _I, _I, _I], None),
    ("mg_blit565_scale", [_P, _Z, _I, _I, _I, _I, _P, _Z, _I, _I, _I], None),
    ("mg_blit_window", [_P, _Z, _I, _I, _P, _Z, _I, _I, _I], None),
    ("mg_blit_indices", [_P, _Z, _I, _I, _I, _I, _P, _Z, _I, _I, _P, _Z], None),
    ("mg_text", [_P, _Z, _I, _P, _Z, _I, _I, _I, _P, _I, _I, _I, _I, _I,
                 _I, _I, _I, _I], None),
    # ...and the host-side rest.
    ("hg_copy_async", [_P, _Z, _I, _P, _Z, _I, _I], _I),
    ("hg_copy_wait", [], _I),
    ("hg_blit_batch", [_P, _Z, _I, _I, _P, _I, _P, _Z, _I, _I, _P, _P,
                       _I, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_blit_map", [_P, _Z, _I, _I, _I, _P, _Z, _I, _I, _I, _I, _I, _I,
                     _P, _Z, _I, _I, _P, _P, _I, _I, _I, _I, _I, _I], None),
    # The solid-colour libmoy verbs: device_canvas calls circ/circb/line
    # DIRECTLY (guarded only on the kernel existing), and reaches tri/sspr/
    # tline through getattr with a Python fallback. Implemented alike, because
    # "optional" there means "the board would be slower", not "the host may
    # diverge" -- a Python lane here would be the per-platform difference this
    # whole exercise removes.
    ("hg_line", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_circ", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_circb", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_tri", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I,
                _I], None),
    ("hg_sspr", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _P, _Z, _I, _I,
                 _P, _P, _I, _I, _I, _I, _I, _I, _I, _I], None),
    ("hg_tline", [_P, _Z, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, _P, _Z, _I, _I,
                  _P, _Z, _I, _I, _P, _P, _I, _I, _I, _I, _I, _I, _I], None),
)


def build(verbose=False):
    return native_build.build("moyhost_gfx", _SHIM, _LIBMOY, _CACHE,
                              cflags=_CFLAGS, libmoy_dir=_SRC_DIRS,
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
    _lib().mg_fill(ctypes.cast(arr, _P), cap, int(npix), int(color) & 0xFFFF)


def fill_rect(buf, stride, x, y, w, h, color):
    arr, cap = _buf(buf)
    _lib().mg_fill_rect(ctypes.cast(arr, _P), cap, int(stride), int(x), int(y),
                        int(w), int(h), int(color) & 0xFFFF)


def scroll_rect(buf, stride, rx, ry, rw, rh, dx, dy):
    arr, cap = _buf(buf)
    _lib().mg_scroll_rect(ctypes.cast(arr, _P), cap, int(stride), int(rx),
                          int(ry), int(rw), int(rh), int(dx), int(dy))


def blit565(dst, dw, dh, dx, dy, src, sw, sh, key,
            cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    sarr, scap = _rbuf(src)
    _lib().mg_blit565(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                      int(dx), int(dy),
                      ctypes.cast(sarr, _P), scap, int(sw), int(sh), int(key),
                      int(cx0), int(cy0),
                      int(dw) if cx1 is None else int(cx1),
                      int(dh) if cy1 is None else int(cy1))


def blit565_scale(dst, dw, dh, dx, dy, src, sw, sh, scale):
    """Integer-upscale an RGB565 source into dst at (dx, dy), which may be
    negative -- the windowed composite primitive (game frame -> its window
    viewport, wallpaper frame -> the full-desktop cover backdrop).

    Its absence used to be silent and expensive: `HostSystemCanvas.blit_cover`
    probes for this name and otherwise expands every row in Python."""
    darr, dcap = _buf(dst)
    sarr, scap = _rbuf(src)
    _lib().mg_blit565_scale(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                            int(dx), int(dy),
                            ctypes.cast(sarr, _P), scap, int(sw), int(sh),
                            int(scale))


def blit_window(dst, dw, dh, src, src_w, sx, sy):
    darr, dcap = _buf(dst)
    sarr, scap = _rbuf(src)
    _lib().mg_blit_window(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                          ctypes.cast(sarr, _P), scap, int(src_w),
                          int(sx), int(sy))


def copy_async(dst, dst_off, src, src_off, npix):
    """Always False on the host: no GDMA, so the caller takes its sync path --
    the same branch a board takes when its DMA driver declines."""
    return False


def copy_wait():
    return True


def _quads(items):
    """(int16 quad buffer, count) from either shape device_canvas passes.

    The native verb accepts an array("h") whose slot 0 is the write cursor, OR a
    list of (tile, x, y[, flip]) tuples. Normalising the list form HERE rather
    than in C keeps one path in the kernel -- the shape differs, the pixels must
    not -- and the array form, which is the hot one, passes straight through
    with no copy at all.
    """
    import array
    if isinstance(items, array.array) and items.typecode == "h":
        n = items[0]
        if n < 4:
            n = 4
        if n > len(items):
            n = len(items)
        return items, (n - 4) // 4, 4
    flat = array.array("h")
    for it in items:
        if len(it) < 3:
            continue
        flat.extend((it[0], it[1], it[2], it[3] if len(it) > 3 else 0))
    return flat, len(flat) // 4, 0


def blit_batch(dst, dw, dh, items, sheet, sheetw, sheeth, lut, palt,
               key, scale, cam_x, cam_y, cx0, cy0, cx1, cy1):
    darr, dcap = _buf(dst)
    quads, n, off = _quads(items)
    qarr, _ = _rbuf(memoryview(quads).cast("B"))
    sharr, _ = _rbuf(sheet)
    lutarr, _ = _rbuf(memoryview(lut).cast("B"))
    ptarr = None
    if palt is not None:
        ptarr, _ = _rbuf(palt)
    _lib().hg_blit_batch(
        ctypes.cast(darr, _P), dcap, int(dw), int(dh),
        ctypes.c_void_p(ctypes.cast(qarr, _P).value + off * 2), int(n),
        ctypes.cast(sharr, _P), len(sheet), int(sheetw), int(sheeth),
        ctypes.cast(lutarr, _P),
        ctypes.cast(ptarr, _P) if ptarr is not None else None,
        int(key), int(scale), int(cam_x), int(cam_y),
        int(cx0), int(cy0), int(cx1), int(cy1))


def line(dst, dw, dh, x0, y0, x1, y1, col, cam_x=0, cam_y=0,
         cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    _lib().hg_line(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                   int(x0), int(y0), int(x1), int(y1), int(col) & 0xFFFF,
                   int(cam_x), int(cam_y), int(cx0), int(cy0),
                   int(dw) if cx1 is None else int(cx1),
                   int(dh) if cy1 is None else int(cy1))


def circ(dst, dw, dh, cx, cy, r, col, cam_x=0, cam_y=0,
         cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    _lib().hg_circ(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                   int(cx), int(cy), int(r), int(col) & 0xFFFF,
                   int(cam_x), int(cam_y), int(cx0), int(cy0),
                   int(dw) if cx1 is None else int(cx1),
                   int(dh) if cy1 is None else int(cy1))


def circb(dst, dw, dh, cx, cy, r, col, cam_x=0, cam_y=0,
          cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    _lib().hg_circb(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                    int(cx), int(cy), int(r), int(col) & 0xFFFF,
                    int(cam_x), int(cam_y), int(cx0), int(cy0),
                    int(dw) if cx1 is None else int(cx1),
                    int(dh) if cy1 is None else int(cy1))


def tri(dst, dw, dh, x1, y1, x2, y2, x3, y3, col, cam_x=0, cam_y=0,
        cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    _lib().hg_tri(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                  int(x1), int(y1), int(x2), int(y2), int(x3), int(y3),
                  int(col) & 0xFFFF, int(cam_x), int(cam_y),
                  int(cx0), int(cy0),
                  int(dw) if cx1 is None else int(cx1),
                  int(dh) if cy1 is None else int(cy1))


def blit_indices(dst, dw, dh, dx, dy, idx, iw, ih, pal):
    darr, dcap = _buf(dst)
    imv = memoryview(idx).cast("B")        # index bytes: 1 per pixel
    iarr, _ = _rbuf(imv)
    # `len()` on an array("H") is the ELEMENT count, not bytes -- so deriving
    # the palette's entry count by halving it produced 32 entries for a 64-entry
    # table and dropped every index above 31 (the C skips `p >= pcap`). Take the
    # byte length from the memoryview and halve THAT, which is right for both an
    # array and a bytes-like.
    pmv = memoryview(pal).cast("B")
    parr, _ = _rbuf(pmv)
    _lib().mg_blit_indices(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                           int(dx), int(dy),
                           ctypes.cast(iarr, _P), len(imv), int(iw), int(ih),
                           ctypes.cast(parr, _P), len(pmv) // 2)


def text(dst, dw, dh, s, x, y, color, font, first, scale,
         cam_x, cam_y, cx0, cy0, cx1, cy1):
    """petme128 in one call, with the camera and the clip rect honoured.

    Sixteen positional arguments and no defaults, because the native op takes
    exactly sixteen: a default here would let a call that TypeErrors on a board
    quietly work on the host, which is the divergence this shim exists to
    remove. `dh` is accepted and unused, like the native's (rows come from the
    buffer capacity) -- dropping it from the signature would be the tidier order
    the sspr note warns about.

    A str is UTF-8-encoded, which is the buffer MicroPython would have taken
    from it anyway; bytes pass straight through, so moy_lua's non-UTF-8 strings
    draw the same glyphs here as on glass (device_canvas._text_bytes).

    The kernel takes a GLYPH COUNT where this signature takes a blob, so the
    //8 happens here -- exactly as it happens in the usermod's wrapper. petme128
    is 8 bytes per glyph, column-major."""
    darr, dcap = _buf(dst)
    sarr, _ = _rbuf(s.encode("utf-8") if isinstance(s, str) else s)
    farr, _ = _rbuf(font)
    _lib().mg_text(ctypes.cast(darr, _P), dcap, int(dw),
                   ctypes.cast(sarr, _P), len(sarr),
                   int(x), int(y), int(color) & 0xFFFF,
                   ctypes.cast(farr, _P), len(farr) // 8, int(first), int(scale),
                   int(cam_x), int(cam_y), int(cx0), int(cy0),
                   int(cx1), int(cy1))


def sspr(dst, dw, dh, sheet, sheetw, sheeth, sx, sy, sw, sh, dx, dy,
         ddw, ddh, ck, flip, lut, palt,
         cam_x=0, cam_y=0, cx0=0, cy0=0, cx1=None, cy1=None):
    # Argument ORDER is the native module's, not a tidier one. It reads oddly --
    # the sheet before its own source rect, the palette after the flip -- and
    # the first draft here "improved" it, which the parity test caught as a
    # TypeError from the device side. The shim's whole job is that
    # device_canvas cannot tell which module it got.
    darr, dcap = _buf(dst)
    sharr, _ = _rbuf(sheet)
    lutarr, _ = _rbuf(memoryview(lut).cast("B"))
    ptarr = None
    if palt is not None:
        ptarr, _ = _rbuf(palt)
    _lib().hg_sspr(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                   int(sx), int(sy), int(sw), int(sh), int(dx), int(dy),
                   int(ddw), int(ddh),
                   ctypes.cast(sharr, _P), len(sheet), int(sheetw), int(sheeth),
                   ctypes.cast(lutarr, _P),
                   ctypes.cast(ptarr, _P) if ptarr is not None else None,
                   int(ck), int(flip), int(cam_x), int(cam_y),
                   int(cx0), int(cy0),
                   int(dw) if cx1 is None else int(cx1),
                   int(dh) if cy1 is None else int(cy1))


def tline(dst, dw, dh, cells, mw, mh, sheet, sheetw, sheeth,
          x0, y0, x1, y1, u, v, du, dv, ck, lut, palt,
          cam_x=0, cam_y=0, cx0=0, cy0=0, cx1=None, cy1=None):
    darr, dcap = _buf(dst)
    carr, _ = _rbuf(cells)
    sharr, _ = _rbuf(sheet)
    lutarr, _ = _rbuf(memoryview(lut).cast("B"))
    ptarr = None
    if palt is not None:
        ptarr, _ = _rbuf(palt)
    _lib().hg_tline(ctypes.cast(darr, _P), dcap, int(dw), int(dh),
                    int(x0), int(y0), int(x1), int(y1),
                    int(u), int(v), int(du), int(dv),
                    ctypes.cast(carr, _P), len(cells), int(mw), int(mh),
                    ctypes.cast(sharr, _P), len(sheet), int(sheetw), int(sheeth),
                    ctypes.cast(lutarr, _P),
                    ctypes.cast(ptarr, _P) if ptarr is not None else None,
                    int(ck), int(cam_x), int(cam_y), int(cx0), int(cy0),
                    int(dw) if cx1 is None else int(cx1),
                    int(dh) if cy1 is None else int(cy1))


def blit_map(dst, dw, dh, dsx, dsy, cells, mw, mh, mx, my, rw, rh,
             sheet, sheetw, sheeth, lut, palt, ck, scale, cx0, cy0, cx1, cy1):
    darr, dcap = _buf(dst)
    carr, _ = _rbuf(cells)
    sharr, _ = _rbuf(sheet)
    lutarr, _ = _rbuf(memoryview(lut).cast("B"))
    ptarr = None
    if palt is not None:
        ptarr, _ = _rbuf(palt)
    _lib().hg_blit_map(
        ctypes.cast(darr, _P), dcap, int(dw), int(dsx), int(dsy),
        ctypes.cast(carr, _P), len(cells), int(mw), int(mh),
        int(mx), int(my), int(rw), int(rh),
        ctypes.cast(sharr, _P), len(sheet), int(sheetw), int(sheeth),
        ctypes.cast(lutarr, _P),
        ctypes.cast(ptarr, _P) if ptarr is not None else None,
        int(ck), int(scale), int(cx0), int(cy0), int(cx1), int(cy1))
