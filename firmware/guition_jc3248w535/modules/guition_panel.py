"""Guition JC3248W535 panel glue: the compositor over moy_axs.

The third sibling of `tdeck_panel.TDeckCompositor` (whose module docstring
carries the design prose -- the contract, the overlap, the two feeders) and
`p4_display.P4Compositor`. It satisfies the same small backend interface the
shared console reaches through:

    size() framebuffer() back_buffer() gfx() flush() sync()

`moy_axs` exports moy_lcd's exact verb set (kick/pump/drain/show/fb/stats/
pump_stats), so this file is tdeck_panel minus the pieces that are T-Deck
hardware: no LAYER_COPY_ASYNC lever (unmeasured here -- a recorded A/B, not a
default; device_canvas keeps its own False default when no one assigns it) and
no SD-on-the-panel-bus prose (nothing else is known to share this board's QSPI
host; sync() stays load-bearing anyway, as the idle-band drain and the
backlight gate's fence).

The flush arithmetic this board inherits: 320x480x2 = 307,200 B is ~15.4ms at
QSPI 40MHz x 4 lines. Paid synchronously that caps the loop near 60fps before
a pixel is drawn -- the T-Deck's exact pre-#66 shape -- so the kick/pump/drain
overlap ships from day one, with ASYNC_FLUSH the one-flag serialized fallback
if the glass disagrees (a QSPI wrinkle the ST7789 never had: the whole frame
ships under ONE CS assertion, so a starved pump leaves CS low with the clock
idle mid-frame; the bridge latches per byte and should not care, but "should"
is what the flag is for).
"""

WIDTH = 320
HEIGHT = 480

# False -> flush() is one blocking moy_axs.show(); pump_if_pending is then not
# defined at all (DeviceCanvas getattrs it and degrades) and sync() is a drain
# that always finds nothing. The one-reflash fallback if the panel tears.
ASYNC_FLUSH = True

# Soft-timer pump period -- the T-Deck's shipped 2ms (a band here is ~1.5ms of
# transfer, two slots buffer ~3ms, so a 2ms feeder stays ahead). Timer 3 of
# the S3's four; nothing else in this image takes one. 0 disables the timer.
PUMP_TIMER_MS = 2
PUMP_TIMER_ID = 3


class GuitionCompositor:
    """RGB565 framebuffer(s) in PSRAM, pushed to the AXS15231B by moy_axs."""

    def __init__(self, nfbs=2, async_flush=None):
        import moy_axs

        self._lcd = moy_axs
        # Dark until the first composed frame (#45): moy_axs.init parks the
        # backlight low; run_desktop / the smoke lights it after a flush.
        moy_axs.init(nfbs=nfbs)
        self._w = moy_axs.WIDTH
        self._h = moy_axs.HEIGHT
        self._fbs = [moy_axs.fb(i) for i in range(moy_axs.nfbs())]
        self._back = 0
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None

        if async_flush is None:
            async_flush = ASYNC_FLUSH
        self._async = bool(async_flush) and len(self._fbs) > 1 \
            and hasattr(moy_axs, "kick")
        self.bounce_flush = self._async
        self._pump_timer = None
        if self._async:
            self.pump_if_pending = moy_axs.pump
            if PUMP_TIMER_MS:
                try:
                    from machine import Timer
                    self._pump_timer = Timer(PUMP_TIMER_ID)
                    self._pump_timer.init(period=PUMP_TIMER_MS,
                                          mode=Timer.PERIODIC,
                                          callback=moy_axs.pump)
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte panel: pump timer unavailable "
                          "(draw-poke + drain only):", exc)
                    self._pump_timer = None

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
        lcd = self._lcd
        if not self._async:
            lcd.show(self._back)
            self._swap()
            return
        lcd.drain()
        front = self._back
        self._swap()
        lcd.kick(front)

    def _swap(self):
        n = len(self._fbs)
        if n > 1:
            self._back = (self._back + 1) % n

    def sync(self):
        """Leave NO panel transfer in flight. The fence the backlight gate and
        the idle-band drain take; also what anything reaching around the
        console must call first."""
        self._lcd.drain()

    # -- diagnostics ---------------------------------------------------------

    @property
    def pump_last_us(self):
        return self._lcd.pump_stats()[0] if self._async else 0

    def bounce_stats(self):
        if not self._async:
            return (0, 0, 0, -1, 0)
        st = self._lcd.pump_stats()
        return (st[0], st[1], st[2], st[3], st[4])

    # -- board bits ----------------------------------------------------------

    def set_backlight(self, on=True):
        self._lcd.backlight(on)

    def stats(self):
        return self._lcd.stats()


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand."""
    import moy_axs
    moy_axs.backlight(on)
