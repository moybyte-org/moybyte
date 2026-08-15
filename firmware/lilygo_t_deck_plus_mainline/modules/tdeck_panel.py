"""T-Deck panel glue for the mainline build: the compositor over moy_lcd.

This is the twin of the P4's `p4_display.P4Compositor`, and it satisfies the
SAME small interface the shared console reaches a backend through -- the one
`DeviceCanvas.__init__` and `moy_runtime.run_desktop` actually call:

    size()          -> (w, h)
    framebuffer()   -> the buffer to draw into THIS frame
    back_buffer()   -> alias of framebuffer(), named for the ping-pong call site
    gfx()           -> the native moy_gfx kernel, or None
    flush()         -> present what was drawn
    sync()          -> guarantee no panel DMA is in flight

Nothing new is invented here (docs/backend_contract_v1.md L8: strategy stays the
backend's, and a new backend implements the contract rather than a new
mechanism). What differs from `moy_compositor.Compositor` in the fork build is
ONLY where the pixels are pushed from: there, a Python object drives
`lcd_bus.tx_color` band by band and owns the whole async/bounce/pump machine;
here, `moy_lcd.show()` is one C call that does the banding, the internal-SRAM
bounce and the completion fence itself.

FLUSH IS BLOCKING at this stage, deliberately.
  `sync()` is therefore a no-op and there is no `pump_if_pending` -- DeviceCanvas
  looks that one up with getattr and degrades cleanly when it is absent (the P4
  has none either). The fork build's overlap (async completion callback + the
  soft-timer pump + the draw-verb poke) is a real ~2x on this board and it is
  the FIRST thing to port after the console boots, but it is a performance lever
  layered on a working panel, not part of proving the panel works.
"""

WIDTH = 320
HEIGHT = 240


class TDeckCompositor:
    """RGB565 framebuffer(s) in PSRAM, pushed to the ST7789 by moy_lcd."""

    def __init__(self, nfbs=2):
        import moy_lcd

        self._lcd = moy_lcd
        # Dark until the first composed frame (#45): a freshly-powered ST7789's
        # GRAM is noise, and moy_lcd.init leaves the backlight off for exactly
        # this reason. run_desktop lights it after the first flush.
        moy_lcd.init(nfbs=nfbs)
        self._w = moy_lcd.WIDTH
        self._h = moy_lcd.HEIGHT
        # Cache the memoryviews ONCE. back_buffer() is called every frame and on
        # every layer bind; re-creating a memoryview per call would allocate on
        # the hot path for no reason.
        self._fbs = [moy_lcd.fb(i) for i in range(moy_lcd.nfbs())]
        self._back = 0
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None

    # -- the contract --------------------------------------------------------

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._fbs[self._back]

    def back_buffer(self):
        return self._fbs[self._back]

    def gfx(self):
        return self._gfx

    def has_gfx(self):
        return self._gfx is not None

    def flush(self):
        self._lcd.show(self._back)
        # Ping-pong so the next frame is drawn into a buffer the panel is not
        # reading. With a blocking show() that is belt-and-braces today; it is
        # the shape the async flush needs, and it matches what the fork build
        # ships (DOUBLE_BUFFER=True), which is what DeviceCanvas.RETAINED_FRAMES
        # = 2 is calibrated against.
        n = len(self._fbs)
        if n > 1:
            self._back = (self._back + 1) % n

    def sync(self):
        # show() does not return until the last band's completion ISR has run,
        # so there is never in-flight panel DMA to drain. This still MUST exist:
        # run_desktop calls it before every SD session, because the card shares
        # this SPI host and an overlapping panel transfer corrupts the bus.
        pass

    # -- board bits ----------------------------------------------------------

    def set_backlight(self, on=True):
        self._lcd.backlight(on)

    def stats(self):
        """(flushes, last_flush_us) -- the serial-visible flush cost."""
        return self._lcd.stats()


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand."""
    import moy_lcd
    moy_lcd.backlight(on)
