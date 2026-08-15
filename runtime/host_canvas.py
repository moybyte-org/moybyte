"""Run the BOARDS' canvas class on CPython.

`device_canvas.DeviceCanvas` is the raster on both boards and in the browser.
This is what it needs to also be the raster on the host, so there stops being a
second one: a compositor to draw into, the two MicroPython modules it imports by
name, and (`HostSystemCanvas`, below) the system-surface contract the shared
console draws its chrome through.

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
from array import array

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
    """A DeviceCanvas over a HostCompositor -- the boards' raster, on CPython.

    The GAME tier: a cart's fixed 320x240 surface, whose text is always 8px
    (SPEC.md 6). Chrome wants `make_system_canvas` instead.
    """
    install()
    from device_canvas import DeviceCanvas          # noqa: E402 -- after install
    return DeviceCanvas(HostCompositor(w, h))


class _Rgb888Lut(dict):
    """RGB565 word -> three RGB bytes, for `HostSystemCanvas.to_rgb888`.

    A dict rather than a list because the framebuffer's words are sparse (64 of
    65536 for a stock palette), and `__missing__` is what makes the sparse case
    safe: a word no palette index produced still has to become a colour.

    That happens for pixels this canvas did not resolve through the palette --
    a sprite variant whose bake nudged a pixel off `_RGB_KEY` (device_canvas
    `_cache_rgb`), or anything blitted in as raw 565. Bit-expanding 5/6/5 to
    8/8/8 is the RIGHT answer for exactly those and the WRONG answer for
    everything else, which is why it lives down here in the fallback and not in
    the main path: MOY64 is not a bit-expansion of its own 565 form (only 1 of
    the first 16 indices survives the round trip), so an expansion-based
    to_rgb888 would recolour the entire console.
    """

    def __init__(self, swapped):
        dict.__init__(self)
        # The buffer holds words in this build's WIRE order. Under CPython
        # `moy_dsi` is absent, so device_canvas picks the T-Deck's byte-swapped
        # PAL565_SW and the fallback has to swap back before it decomposes.
        self._swapped = swapped

    def __missing__(self, word):
        w = word
        if self._swapped:
            w = (((w & 0xFF) << 8) | (w >> 8)) & 0xFFFF
        r = (w >> 11) & 0x1F
        g = (w >> 5) & 0x3F
        b = w & 0x1F
        rgb = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        self[word] = rgb                 # memoized: a stray word costs once
        return rgb


_SYSTEM_CLS = [None]


def _system_canvas_class():
    """`HostSystemCanvas`, built on first use.

    It is defined inside a function, not at module scope, because subclassing
    `DeviceCanvas` means IMPORTING it, and that import only works after
    `install()` -- which registers `moy_gfx` and `framebuf` in `sys.modules` for
    the rest of the process. `runtime/console.py` and `runtime/player.py` both
    decide behaviour on a bare `import moy_gfx`, so merely importing a canvas
    module must not flip those probes behind their back. Callers reach the class
    by name (module `__getattr__`) or through `make_system_canvas`; either way
    install() is what they asked for.
    """
    if _SYSTEM_CLS[0] is not None:
        return _SYSTEM_CLS[0]
    install()
    from device_canvas import (                     # noqa: E402 -- after install
        DeviceCanvas, _LayerComp, _FONT8, _FONT8_FIRST,
        _PAL565_INDEX, _PAL565_WIRE_BUF, _ST_FONT_SCALE,
        PAL565, PAL565_WIRE)
    from . import font as _font

    _SWAPPED = PAL565_WIRE is not PAL565

    class HostSystemCanvas(DeviceCanvas):
        """DeviceCanvas + the system-surface contract (#39/#73), on the host.

        The third of these, and deliberately the same shape as the other two:
        `WebSystemCanvas` (the wasm head) and `P4SystemCanvas` (the board with a
        big system canvas and a separate 320x240 game canvas). What the console
        asks a SYSTEM surface for beyond the raster is font_scale text,
        font-scale-carrying layers, the wallpaper cover-blit, and -- host only,
        because only the host has a screen to hand to pygame or a GIF -- an
        RGB888 readout.

        Its purpose is to make `runtime/canvas.py` deletable: everything the
        host's `SystemCanvas` does and `DeviceCanvas` does not, does it here,
        over the raster all three tiers already share.
        """

        # NOT the class default 2. That describes the P4's DPI buffer rotation;
        # `HostCompositor` holds ONE persistent buffer, so a partial-repaint or
        # scroll-as-blit surface (#113) must measure against the LAST paint.
        # Getting this wrong is not theoretical -- an omitted RETAINED_FRAMES=1
        # on the P4's layers ghosted every card in a picker drag, on glass.
        RETAINED_FRAMES = 1

        def __init__(self, comp, font_scale=1):
            # BEFORE the base __init__, which seeds the native draw gate's state
            # array from font_scale (_install_draw_gates runs in there). Set
            # afterwards, every system surface would gate at 1x until the next
            # set_font_scale. Both other subclasses carry this same warning.
            self.font_scale = max(1, int(font_scale))
            self._rgb888_cache = None     # (wire table, _Rgb888Lut) -- see to_rgb888
            DeviceCanvas.__init__(self, comp)

        def set_font_scale(self, scale):
            # Called UNGUARDED from console.py (the settings row and the
            # font-scale relayout), so its absence is a boot crash, not a
            # degradation. The gate state array is None here (the host kernel
            # has no make_draw_ctx) but the guard stays: it is what makes this
            # method correct on a build that does gate.
            self.font_scale = max(1, int(scale))
            st = self._gate_state
            if st is not None:
                st[_ST_FONT_SCALE] = self.font_scale

        def print(self, s, x, y, c, scale=1):
            # petme128 at font_scale. The per-call `scale` arg stays IGNORED,
            # exactly as on the host's SystemCanvas and both boards (SPEC.md 6:
            # cart text is always 8px; system-UI scaling is the #39 font_scale
            # path, not this argument).
            fs = self.font_scale
            gt = self._gfx_text
            if gt is not None:
                if fs <= 1:
                    DeviceCanvas.print(self, s, x, y, c)
                    return
                self.flush_batch()       # a non-spr primitive breaks the batch
                gt(self._buf, self._stride, self._bh, str(s), int(x), int(y),
                   self._col(c), _FONT8, _FONT8_FIRST, fs,
                   self._cam_x, self._cam_y,
                   self._clip_x0, self._clip_y0, self._clip_x1, self._clip_y1)
                return
            # NO KERNEL TEXT OP, which on the host is the only case that runs:
            # `runtime/moyhost_gfx.c` compiles libmoy's raster plus moybyte's
            # compositing verbs, and text is neither, so `_gfx_text` is None and
            # the branch above is for the day it isn't.
            #
            # So this rasterizes petme128 itself, for TWO reasons, and the
            # second is not optional. (1) Delegating at fs > 1 would render
            # scaled chrome at 1x -- silently, and only on the host. (2)
            # DeviceCanvas's own no-kernel fallback is `framebuf.text`, which
            # has NO CLIP RECT (its docstring says so: "same glyphs, no clip
            # rect"), and the console clips text to panels everywhere -- an
            # editor's code column, a window's title strip. Going through
            # _put/_fill instead carries camera, clip and pal exactly as
            # runtime/canvas.py's SystemCanvas.print does, which is what makes
            # the two byte-identical.
            self.flush_batch()
            col = self._col(c)
            if fs <= 1:
                put = self._put

                def emit(px, py):
                    put(px, py, col)

                _font.draw(emit, s, int(x), int(y))
                return
            fill = self._fill

            def block(bx, by, n):
                fill(bx, by, n, n, col)

            _font.draw_scaled(block, s, int(x), int(y), fs)

        def new_layer(self, w, h, owner=None):
            # Font-scale-carrying layers, like SystemCanvas.new_layer: the WM's
            # per-window content buffers and the bar's strip cache print through
            # these, so they must scale like the surface they composite onto.
            # Without this override they would be bare DeviceCanvases and every
            # cached strip would lose its scaled text (bar_layer, launcher_layer,
            # wm_windowed, host_api all build layers this way).
            #
            # No PSRAM pooling and no pre-collect: there is no moy_alloc here, so
            # _LayerComp falls back to a gc-heap bytearray and CPython's
            # allocator has nothing to defragment. `owner` is accepted and
            # ignored so the shared callers need no branch.
            lay = HostSystemCanvas(_LayerComp(int(w), int(h), self._gfx),
                                   font_scale=self.font_scale)
            lay._nocache = True          # a layer's own map() rasters directly
            lay.RETAINED_FRAMES = 1      # see the class note; ONE buffer
            return lay

        def blit_cover(self, gc):
            """wallpaper._backdrop_blit's raster path: the smallest integer
            upscale of the 320x240 wallpaper frame that COVERS the whole
            desktop, centered and cropped (ox/oy <= 0), which is what makes the
            backdrop full-bleed instead of a letterboxed rectangle floating in
            black.

            Not optional on a 565 canvas, and its absence is SILENT:
            `_backdrop_blit` probes for this method and otherwise falls back to
            expanding the wallpaper's palette-INDEX buffer. A 565 canvas has no
            index buffer, so that path finds `buf` missing and returns having
            drawn nothing -- a black desk with correct chrome on top, which is
            exactly what the wasm head's first build produced.

            `gc` is the GAME canvas, and on the host it may still be an indexed
            `runtime.canvas.Canvas` -- see `_cover_py`.
            """
            gw, gh = gc.w, gc.h
            if gw <= 0 or gh <= 0:
                return
            sw, sh = self.w, self.h
            scale = max(1, (sw + gw - 1) // gw, (sh + gh - 1) // gh)
            ox = (sw - gw * scale) // 2
            oy = (sh - gh * scale) // 2
            fb = getattr(gc, "flush_batch", None)
            if fb is not None:
                fb()
            self.flush_batch()
            g = self._gfx
            words = getattr(gc, "_buf", None)
            scaled = getattr(g, "blit565_scale", None) if g is not None else None
            if scaled is not None and words is not None:
                # The P4/web path (one kernel call). Absent from the host
                # binding today; probed so it is used the day it lands.
                scaled(self._buf, sw, sh, int(ox), int(oy),
                       words, gw, gh, int(scale))
                return
            self._cover_py(gc, ox, oy, scale)

        def _cover_py(self, gc, ox, oy, scale):
            # The same loop shape as wallpaper._backdrop_blit's index path, in
            # 565: expand each source row ONCE, then slice the visible crop into
            # every destination row it covers -- row-level copies, no per-pixel
            # inner loop. Deliberately a twin of that code rather than a new
            # idea, so the two stay readable against each other.
            #
            # An INDEXED source is the migration case, and it is the only case
            # that runs until runtime/canvas.py is gone: the host's game canvas
            # (and therefore the wallpaper's frame) is still a `Canvas` of
            # palette indices while this class holds the system surface. Without
            # it the first wiring of a HostSystemCanvas into build_workstation
            # dies on `gc._buf` at the first desktop frame. It resolves through
            # THIS canvas's table, which is what the index path it replaces does
            # -- there the indices are copied raw and the destination's palette
            # resolves them later. Delete this branch with canvas.py.
            words = getattr(gc, "_buf", None)
            src = memoryview(words).cast("H") if words is not None else None
            idx = None if src is not None else gc.buf
            wire = self._wire
            dst = self._buf
            gw, gh = gc.w, gc.h
            sw, sh = self.w, self.h
            stride, bx, by = self._stride, self._ox, self._oy
            crop_x = -ox if ox < 0 else 0
            dst_x = ox if ox > 0 else 0
            span = min(sw - dst_x, gw * scale - crop_x)
            if span <= 0:
                return
            for gy in range(gh):
                if src is not None:
                    row = src[gy * gw:gy * gw + gw]
                else:
                    row = [wire[v & 63] for v in idx[gy * gw:gy * gw + gw]]
                if scale == 1 and src is not None:
                    er = row
                else:
                    er = array("H", [v for v in row for _ in range(scale)])
                seg = memoryview(er).cast("B")[crop_x * 2:(crop_x + span) * 2]
                for s in range(scale):
                    dy = oy + gy * scale + s
                    if dy < 0 or dy >= sh:
                        continue
                    base = ((by + dy) * stride + bx + dst_x) * 2
                    dst[base:base + span * 2] = seg

        # -- output ----------------------------------------------------------

        def to_rgb888(self):
            """The surface as w*h*3 row-major RGB bytes -- what pygame blits and
            what the GIF export writes. Byte-identical to `Canvas.to_rgb888()`,
            which is the acceptance test for replacing it.

            Word -> palette INDEX -> RGB, never a 5/6/5 bit-expansion. The
            expansion looks right (it is what every "convert 565" snippet does)
            and is wrong here: MOY64 is an authored table, and expanding its own
            565 form reproduces only 1 of the first 16 entries. The reverse LUT
            is `device_canvas._PAL565_INDEX`, the same table `pix()` reads an
            index back through, so a colour read costs the same answer whichever
            verb asks; and the RGB comes from this canvas's `palette` property,
            so a cart-supplied table (SPEC.md 3.1) resolves to the cart's
            colours rather than MOY64's.
            """
            self.flush_batch()           # complete any queued sprites (#63)
            get = self._rgb888_lut().__getitem__
            mv = memoryview(self._buf).cast("H")
            w, h = self.w, self.h
            stride = self._stride
            if self._ox == 0 and self._oy == 0 and stride == w:
                return b"".join(map(get, mv[:w * h]))
            # A viewport canvas (#155) draws into a sub-rect of a wider buffer;
            # the readout is still the LOGICAL surface, row by row.
            rows = []
            for row in range(h):
                s = (self._oy + row) * stride + self._ox
                rows.append(b"".join(map(get, mv[s:s + w])))
            return b"".join(rows)

        def _rgb888_lut(self):
            # Rebuilt only when the wire table changes identity, which is
            # exactly when a cart palette is applied or restored (the setter
            # allocates a private table; the stock canvas keeps pointing at the
            # shared module buffer).
            wire = self._wire
            cached = self._rgb888_cache
            if cached is not None and cached[0] is wire:
                return cached[1]
            pal = self.palette
            if wire is _PAL565_WIRE_BUF:
                rev = _PAL565_INDEX      # the module's own, already built
            else:
                rev = {}
                for i in range(len(pal) - 1, -1, -1):
                    rev[wire[i]] = i     # first index wins, like _PAL565_INDEX
            lut = _Rgb888Lut(_SWAPPED)
            for word, i in rev.items():
                lut[word] = bytes(pal[i])
            self._rgb888_cache = (wire, lut)
            return lut

    _SYSTEM_CLS[0] = HostSystemCanvas
    return HostSystemCanvas


def __getattr__(name):
    # PEP 562: `from runtime.host_canvas import HostSystemCanvas` builds the
    # class on demand (see _system_canvas_class for why it cannot be defined at
    # module scope).
    if name == "HostSystemCanvas":
        return _system_canvas_class()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def make_system_canvas(w, h, font_scale=1):
    """A system canvas of (w, h) over its own buffer -- the CHROME tier.

    The two-domain seam (#39) as two factories: the desktop/launcher/settings
    and every editor tab draw here and reflow with the size, while a running
    cart draws on the fixed game canvas `make_canvas` returns.
    """
    return _system_canvas_class()(HostCompositor(w, h), font_scale=font_scale)
