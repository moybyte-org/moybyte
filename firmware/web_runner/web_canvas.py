"""The wasm head's RASTER (moycore stage 4).

The browser stopped being the GPU. Until this landed, the web runner's system
canvas was `web_view.CommandCanvas`: it rasterized nothing and shipped each
frame's draw-command list to a JS replayer in the page. That whole stack --
recorder, wire protocol, replayer -- is deleted; the wasm now draws its own
pixels with the SAME kernel the boards run (`moy_gfx` + vendored libmoy,
compiled into this build as a usermod), and the page's only job is to blit the
finished framebuffer.

**There is no new canvas class here, and that is the point.** The two objects
below are ~100 lines of PRESENTATION glue; the canvas itself is
`device_canvas.DeviceCanvas`, staged verbatim from the T-Deck modules tree --
the same class, the same file, that both boards run. Three architectures, one
raster, one set of conformance goldens. That is what the moycore plan's
zero-duplication directive (§3.5) asks for, and the reason it is possible at all
is that DeviceCanvas talks to a COMPOSITOR interface (`size`/`framebuffer`/
`back_buffer`/`gfx`) rather than to hardware -- everything genuinely
board-specific (the SRAM-bounce pump, the DPI ping-pong, GDMA async copies, the
PPA, PSRAM layer pooling) sits behind a `getattr` probe that finds nothing here.

Why this is fast enough to be worth the deletion, measured before it was built
(`experiments/wasm_shell_raster/`, and re-measured in this VM): a full
1024x600 clear costs 0.10ms through the C kernel against 4.4ms interpreted, and
a whole desk repaint 0.06ms against 37ms. The dispatch cost of ~100-150 verb
calls per frame, not the pixels, is the shell's real budget here -- the same
shape as the P4.
"""

from device_canvas import DeviceCanvas, _LayerComp, _FONT8, _FONT8_FIRST

try:
    import moy_gfx as _moy_gfx
except ImportError:      # a build without the usermod: DeviceCanvas falls back
    _moy_gfx = None      # to framebuf, which is slow but correct


class WebCompositor:
    """What a browser has instead of a panel: one RGB565 buffer and no flush.

    `DeviceCanvas` asks a compositor for four things. On the boards the answers
    involve DMA-capable PSRAM, a double or triple buffer, and a driver; here the
    buffer is a plain bytearray the worker reads by address straight out of the
    wasm heap, so there is nothing to swap and nothing to flush -- `back_buffer`
    returns the one buffer, which makes `DeviceCanvas.sync_back` a no-op.

    Deliberately ABSENT (each one is a `getattr` probe in DeviceCanvas that must
    fail here): `pump_if_pending` (the T-Deck's SRAM-bounce flush pump),
    `fold_supported`/`fold_fence`/`arm_scale_fold` (the #190 S3 composite fold),
    and any `_fbs` list (the P4's DPI buffer rotation, which is what sets
    RETAINED_FRAMES > 1 there).
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
        return _moy_gfx


class WebSystemCanvas(DeviceCanvas):
    """DeviceCanvas + the system-surface contract (#39/#73): font_scale text and
    font-scale-carrying layers. The twin of `P4SystemCanvas` minus the hardware
    -- that board is the closest analogue, since it also drives a big system
    canvas with a separate 320x240 game canvas under the windowed WM.

    RETAINED_FRAMES is 1, not the class default 2: this compositor holds ONE
    persistent buffer, so a partial-repaint or scroll-as-blit surface must
    measure against the last paint. (The P4's 2/3 describes its panel ping-pong.
    Getting this wrong is not theoretical -- an omitted `RETAINED_FRAMES = 1` on
    that board's layers ghosted every card in a picker drag, on glass.)
    """

    RETAINED_FRAMES = 1

    def __init__(self, comp, font_scale=1):
        # BEFORE the base __init__, which seeds the native draw gate's state
        # array from font_scale -- set afterwards, every system surface would
        # gate at 1x until the next set_font_scale.
        self.font_scale = max(1, int(font_scale))
        DeviceCanvas.__init__(self, comp)

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))
        st = self._gate_state
        if st is not None:
            from device_canvas import _ST_FONT_SCALE
            st[_ST_FONT_SCALE] = self.font_scale

    def print(self, s, x, y, c, scale=1):
        # petme128 at font_scale through the native text kernel's scale arg; the
        # legacy per-call `scale` stays ignored exactly as on the host and the
        # boards (SPEC.md 6: cart text is always 8px).
        fs = self.font_scale
        if fs <= 1 or self._gfx_text is None:
            DeviceCanvas.print(self, s, x, y, c)
            return
        self.flush_batch()
        self._gfx_text(self._buf, self._stride, self._bh, str(s), int(x), int(y),
                       self._col(c), _FONT8, _FONT8_FIRST, fs,
                       self._cam_x, self._cam_y,
                       self._clip_x0, self._clip_y0,
                       self._clip_x1, self._clip_y1)

    def blit_cover(self, gc):
        """wallpaper._backdrop_blit's raster path: the smallest integer upscale
        of the 320x240 wallpaper frame that COVERS the whole desktop, centered
        and cropped (ox/oy <= 0), which is what makes the backdrop full-bleed
        instead of a letterboxed rectangle floating in black.

        Not optional on this canvas, and its absence is silent: `_backdrop_blit`
        probes for it and otherwise falls back to expanding the wallpaper's
        palette-INDEX buffer in Python. A 565 canvas has no index buffer, so
        that path finds `buf` missing and returns having drawn nothing -- a
        black desk with correct chrome on top, which is exactly what the first
        run of this build produced.
        """
        gw, gh = gc.w, gc.h
        sw, sh = self.w, self.h
        scale = max(1, (sw + gw - 1) // gw, (sh + gh - 1) // gh)
        ox = (sw - gw * scale) // 2
        oy = (sh - gh * scale) // 2
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        self.flush_batch()
        g = self._gfx
        if g is None:
            return
        g.blit565_scale(self._buf, self.w, self.h, int(ox), int(oy),
                        gc._buf, gc.w, gc.h, int(scale))

    def new_layer(self, w, h, owner=None):
        # Font-scale-carrying layers, like the host's SystemCanvas.new_layer:
        # the WM's per-window content buffers and the bar's strip cache print
        # through these, so they must scale like the surface they composite
        # onto. No PSRAM pooling or pre-collect here -- `owner` is accepted and
        # ignored so the shared callers need no branch.
        lay = WebSystemCanvas(_LayerComp(int(w), int(h), self._gfx),
                              font_scale=self.font_scale)
        lay._nocache = True
        lay.RETAINED_FRAMES = 1
        return lay


def make_canvas(w, h, font_scale=1):
    """A system canvas of (w, h) over its own buffer."""
    return WebSystemCanvas(WebCompositor(w, h), font_scale=font_scale)
