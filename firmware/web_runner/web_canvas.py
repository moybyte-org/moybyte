"""The wasm head's RASTER (moycore stage 4).

The browser stopped being the GPU. Until this landed, the web runner's system
canvas was `web_view.CommandCanvas`: it rasterized nothing and shipped each
frame's draw-command list to a JS replayer in the page. That whole stack --
recorder, wire protocol, replayer -- is deleted; the wasm now draws its own
pixels with the SAME kernel the boards run (`moy_gfx` + vendored libmoy,
compiled into this build as a usermod), and the page's only job is to blit the
finished framebuffer.

**There is no new canvas class here, and that is the point.** What is below is
PRESENTATION glue; the canvas itself is `device_canvas.DeviceCanvas`, staged
verbatim from the boards' device tree -- the same class, the same file, that
every board runs. Three architectures, one raster, one set of conformance
goldens. That is what the moycore plan's zero-duplication directive (§3.5) asks
for, and the reason it is possible at all is that DeviceCanvas talks to a
COMPOSITOR interface (`size`/`framebuffer`/`back_buffer`/`gfx`) rather than to
hardware. The browser's compositor is the host's: the one-buffer compositor
(`one_buffer.py`, staged from runtime/), one RGB565 buffer the worker reads by
address straight out of the wasm heap, with `moy_gfx` handed in. Everything
genuinely board-specific (the SRAM-bounce pump, the DPI ping-pong, GDMA async
copies, the PPA, PSRAM layer pooling) sits behind a `getattr` probe that finds
nothing here.

Why this is fast enough to be worth the deletion, measured before it was built
(the wasm-raster spike -- docs/history/moycore_plan_2026-08.md 6 -- re-measured in this
VM): a full
1024x600 clear costs 0.10ms through the C kernel against 4.4ms interpreted, and
a whole desk repaint 0.06ms against 37ms. The dispatch cost of ~100-150 verb
calls per frame, not the pixels, is the shell's real budget here -- the same
shape as the P4.
"""

from device_canvas import SystemCanvas

from one_buffer import OneBufferCompositor

try:
    import moy_gfx as _moy_gfx
except ImportError:      # a build without the usermod: DeviceCanvas falls back
    _moy_gfx = None      # to framebuf, which is slow but correct


def compositor(w, h):
    """The one-buffer compositor of (w, h), over the browser's raster."""
    return OneBufferCompositor(w, h, _moy_gfx)


class WebSystemCanvas(SystemCanvas):
    """The system-surface contract (#39/#73) over the one-buffer compositor -- and since
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
    return WebSystemCanvas(compositor(w, h), font_scale=font_scale)
