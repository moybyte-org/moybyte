"""Run the BOARDS' canvas class on CPython.

`device_canvas.DeviceCanvas` is the raster on both boards and in the browser.
This is what it needs to also be the raster on the host, so there stops being a
second one: a compositor to draw into, and the two MicroPython modules it
imports by name.

The wasm head did this first and is the proof the shape works -- `web_canvas.py`
supplies a `WebCompositor` (one RGB565 buffer, no flush) and stages the same
canvas class. `HostCompositor` is that class's twin; the difference is only that
a browser's MicroPython already HAS `framebuf`, and CPython does not.

WHAT THE TWO SHIMS ARE FOR

`moy_gfx` is the native kernel. On the host it is `gfx_binding`, which is the
same libmoy compiled RGB565 and pinned against the real module by
tests/test_gfx_binding.py. Registering it in `sys.modules` rather than patching
device_canvas is deliberate: device_canvas must not learn that hosts exist.

`framebuf` is MicroPython's, and device_canvas imports it MANDATORILY -- in
`__init__`, so a canvas cannot be built without it -- even though every hot path
goes to moy_gfx. It is the no-kernel fallback for text and lines. The shim
implements only what device_canvas actually calls (fill, fill_rect, pixel, text,
line), and its `text` uses runtime/font.py, which is the SAME petme128 the device
draws: framebuf's built-in font is petme128 too, which is why device_canvas can
call its text a "same glyphs" fallback.
"""

from __future__ import annotations

import os
import sys

from . import gfx_binding

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_TDECK_MODULES = os.path.join(_ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                              "modules")

RGB565 = 1          # framebuf.RGB565's value; device_canvas passes it through


class _FrameBuffer:
    """The slice of MicroPython's framebuf that device_canvas uses.

    Everything here is a FALLBACK path -- with moy_gfx present (which on the
    host it always is, or there is no canvas at all) these are unreachable
    except for `text` on a build whose kernel predates the text op. Written for
    clarity rather than speed for exactly that reason.
    """

    def __init__(self, buf, w, h, fmt=RGB565):
        self.buf = buf
        self.w = int(w)
        self.h = int(h)
        self._mv = memoryview(buf).cast("H")

    def fill(self, col):
        gfx_binding.fill(self.buf, self.w * self.h, col)

    def fill_rect(self, x, y, w, h, col):
        gfx_binding.fill_rect(self.buf, self.w, x, y, w, h, col)

    def pixel(self, x, y, col=None):
        x = int(x)
        y = int(y)
        if not (0 <= x < self.w and 0 <= y < self.h):
            return None
        i = y * self.w + x
        if col is None:
            return self._mv[i]
        self._mv[i] = int(col) & 0xFFFF
        return None

    def line(self, x0, y0, x1, y1, col):
        x0 = int(x0); y0 = int(y0); x1 = int(x1); y1 = int(y1)
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.pixel(x0, y0, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def text(self, s, x, y, col=1):
        """petme128 8x8, from the ONE font module both tiers rasterize."""
        try:
            from . import font as _font
        except ImportError:                          # pragma: no cover
            import moy_font as _font
        _font.draw(lambda px, py: self.pixel(px, py, col), s, int(x), int(y))


class _FramebufModule:
    FrameBuffer = _FrameBuffer
    RGB565 = RGB565
    MONO_VLSB = 0
    RGB888 = 2


def install():
    """Put `moy_gfx` and `framebuf` where an `import` will find them.

    Idempotent, and it never displaces a real module: on a tier that HAS these
    (a board, the browser) the setdefault does nothing, which is what keeps this
    file from being a second implementation of anything.
    """
    sys.modules.setdefault("moy_gfx", gfx_binding)
    sys.modules.setdefault("framebuf", _FramebufModule)
    if _TDECK_MODULES not in sys.path:
        # device_canvas and its device_util leaf live in the board tree, which
        # is their canonical home -- the boards stage FROM there, and so does
        # the web runner. The host reaches them the same way rather than
        # acquiring a fourth copy.
        sys.path.append(_TDECK_MODULES)


class HostCompositor:
    """What a desktop has instead of a panel: one RGB565 buffer and no flush.

    The four methods DeviceCanvas asks for. Deliberately ABSENT, each one a
    `getattr` probe that must fail here: `pump_if_pending` (the T-Deck's
    SRAM-bounce flush pump), `fold_supported`/`fold_fence`/`arm_scale_fold`
    (the #190 S3 composite fold), and any `_fbs` list (the P4's DPI buffer
    rotation, which is what sets RETAINED_FRAMES > 1 there).
    """

    def __init__(self, w, h):
        self._w = int(w)
        self._h = int(h)
        self._buf = bytearray(self._w * self._h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def back_buffer(self):
        return self._buf

    def gfx(self):
        return gfx_binding


def make_canvas(w, h):
    """A DeviceCanvas over a HostCompositor -- the boards' raster, on CPython."""
    install()
    from device_canvas import DeviceCanvas          # noqa: E402 -- after install
    return DeviceCanvas(HostCompositor(w, h))
