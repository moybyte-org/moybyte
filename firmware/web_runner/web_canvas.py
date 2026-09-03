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
(the wasm-raster spike -- docs/history/moycore_plan_2026-08.md 6 -- re-measured in this
VM): a full
1024x600 clear costs 0.10ms through the C kernel against 4.4ms interpreted, and
a whole desk repaint 0.06ms against 37ms. The dispatch cost of ~100-150 verb
calls per frame, not the pixels, is the shell's real budget here -- the same
shape as the P4.
"""

from device_canvas import SystemCanvas

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


class WebSystemCanvas(SystemCanvas):
    """The system-surface contract (#39/#73) over `WebCompositor` -- and since
    the SystemCanvas unification, nothing but this pin: the font_scale text,
    the font-scale layers and `blit_cover` are the ONE shared body in
    device_canvas.py, the same file the boards freeze.

    RETAINED_FRAMES is 1, not the class default 2: this compositor holds ONE
    persistent buffer, so a partial-repaint or scroll-as-blit surface must
    measure against the last paint. (The P4's 2/3 describes its panel ping-pong.
    Getting this wrong is not theoretical -- an omitted `RETAINED_FRAMES = 1` on
    that board's layers ghosted every card in a picker drag, on glass.)
    """

    RETAINED_FRAMES = 1


def make_canvas(w, h, font_scale=1):
    """A system canvas of (w, h) over its own buffer."""
    return WebSystemCanvas(WebCompositor(w, h), font_scale=font_scale)
