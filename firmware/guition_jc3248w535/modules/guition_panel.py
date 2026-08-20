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

The flush arithmetic this board inherits: 480x320x2 = 307,200 B is ~15.4ms at
QSPI 40MHz x 4 lines -- and since the landscape decision (2026-08-18/19, the
panel's MADCTL MV being dead on this glass) the band copy is a ROTATE-gather
rather than a memcpy: same PSRAM read traffic, plus an in-loop scatter into
the uncached internal-SRAM bounce (moy_axs's LANDSCAPE block carries the
design; pump_stats' pump= carries the measurement). The kick/pump/drain
overlap ships from day one, with ASYNC_FLUSH the one-flag serialized fallback
if the glass disagrees (a QSPI wrinkle the ST7789 never had: the whole frame
ships under ONE CS assertion, so a starved pump leaves CS low with the clock
idle mid-frame; the bridge latches per byte and should not care, but "should"
is what the flag is for).

THE FEED LEFT THE VM CORE (2026-08-19, #202's recorded strategic lever):
moy_axs runs the whole band feed on a FreeRTOS task pinned to core 0 --
MicroPython's task is pinned to core 1 on this port -- woken per band by the
SPI done-ISR. So this compositor has NO pump timer and does NOT export
`pump_if_pending`: the 2ms soft-timer feeder and DeviceCanvas's every-N-ops
draw pokes are retired on this board (they fed a pump that no longer needs
the VM core; `moy_axs.pump` survives as a no-op for the verb-set shape).
tdeck_panel keeps both -- its moy_lcd still feeds from the VM core.
"""

WIDTH = 480
HEIGHT = 320

# False -> flush() is one blocking moy_axs.show(); pump_if_pending is then not
# defined at all (DeviceCanvas getattrs it and degrades) and sync() is a drain
# that always finds nothing. The one-reflash fallback if the panel tears.
ASYNC_FLUSH = True


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

        # The #190-cousin GAME FOLD: DeviceCanvas.blit_game probes
        # `fold_supported` and, instead of compositing the game into the root
        # fb, hands the (scratch-snapshotted) game frame to arm_scale_fold --
        # the flush then SYNTHESIZES every band (black bezels + game pixels
        # read straight from the scratch) and the root is neither written by
        # a composite nor read by the pump: half the per-frame PSRAM traffic
        # on a play frame. The shared frame walk disarms when an overlay
        # paints the root (console.py's _fold_live), and the disarm performs
        # the skipped composite in C. Scale-1 only in the C; other scales
        # (cart-declared views) composite here in the fallback below.
        self.fold_supported = hasattr(moy_axs, "arm_fold")

        if async_flush is None:
            async_flush = ASYNC_FLUSH
        self._async = bool(async_flush) and len(self._fbs) > 1 \
            and hasattr(moy_axs, "kick")
        self.bounce_flush = self._async
        # No pump timer and no pump_if_pending here -- the CORE-0 FEEDER owns
        # the feed (see the module docstring).

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

    # -- the game fold (#190's cousin; see __init__'s note) -------------------

    def fold_fence(self):
        self._lcd.fold_fence()

    def arm_scale_fold(self, src_buf, vw, vh, ox, oy, scale):
        if scale == 1:
            try:
                self._lcd.arm_fold(src_buf, vw, vh, ox, oy)
                return
            except (ValueError, OSError):
                pass                    # geometry the C declines: composite below
        # Fallback: perform the composite blit_game skipped (bezels + scaled
        # blit into the root), so declining is invisible one level up.
        g = self._gfx
        if g is None:
            return
        fb = self.framebuffer()
        g.fill(fb, self._w * self._h, 0)
        g.blit565_scale(fb, self._w, self._h, ox, oy, src_buf, vw, vh, scale)

    def disarm_scale_fold(self):
        self._lcd.disarm_fold(self._back)

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
