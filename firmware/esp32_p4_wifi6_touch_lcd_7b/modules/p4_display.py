"""P4 display glue (#58): the compositor shim over moy_dsi + the backlight.

The EK79007 panel runs MIPI-DSI DPI mode: the DSI peripheral CONTINUOUSLY scans
a PSRAM framebuffer, so there is no per-frame flush transfer at all (the
T-Deck's ~28ms tx_color ceiling is structurally gone). `P4Compositor` adapts
that to the compositor interface DeviceCanvas + the shared Workstation expect
(size/framebuffer/back_buffer/gfx/flush/sync).

DOUBLE-BUFFERED (glass-learned 2026-07-08): drawing straight into the scanned
framebuffer made every repaint visibly flash -- the scan-out raced the painter,
so each tap's full redraw (wallpaper -> windows -> bar) showed its intermediate
states ("everything refreshes on each tap"). The panel now owns TWO
framebuffers: draws land in the BACK one, flush() msyncs it and switches the
scan-out to it zero-copy (moy_dsi.show -- the DPI driver recognizes its own fb
pointer, no pixel copy), then the buffers swap roles. DeviceCanvas.sync_back()
re-points the draw target each frame (the #40 machinery). This requires every
DRAWN frame to fully repaint -- which the console's draw stack does (the
wallpaper/backdrop covers the canvas first). Degrades to the single-buffer
msync path if the build's moy_dsi predates show()/nfbs().
"""

BACKLIGHT_GPIO = 32     # active-LOW (board fact, hardware-confirmed)

_bl_pin = None


def set_backlight(on):
    global _bl_pin
    from machine import Pin
    if _bl_pin is None:
        _bl_pin = Pin(BACKLIGHT_GPIO, Pin.OUT, value=0 if on else 1)
    else:
        _bl_pin.value(0 if on else 1)


class P4Compositor:
    def __init__(self):
        import moy_dsi
        # Dark until the first composed frame (#45): the fresh DPI framebuffers
        # are uninitialized PSRAM -- scan-out garbage the user must never see lit.
        set_backlight(False)
        moy_dsi.init()
        self._dsi = moy_dsi
        self._w = moy_dsi.WIDTH
        self._h = moy_dsi.HEIGHT
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None
        try:
            n = moy_dsi.nfbs()
        except AttributeError:
            n = 1               # older moy_dsi build: single-buffer degrade
        self._fbs = [moy_dsi.fb(i) for i in range(n)] if n > 1 else [moy_dsi.fb()]
        # First paint: black out the garbage in BOTH buffers so neither swap can
        # ever reveal it, then present a clean field before the backlight lights.
        if self._gfx is not None:
            for f in self._fbs:
                self._gfx.fill(f, self._w * self._h, 0)
        if n > 1:
            moy_dsi.show(0)     # scan 0; draw into 1
            self._back = 1
        else:
            moy_dsi.flush()
            self._back = 0

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._fbs[self._back]

    def back_buffer(self):
        return self._fbs[self._back]

    def gfx(self):
        return self._gfx

    def flush(self):
        if len(self._fbs) > 1:
            self._dsi.show(self._back)   # msync + zero-copy scan-out switch
            self._back ^= 1              # next frame draws the other buffer
        else:
            self._dsi.flush()            # single-buffer: CPU-cache msync only

    def sync(self):
        pass                             # no in-flight DMA to drain
