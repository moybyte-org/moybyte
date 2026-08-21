"""Guition JC3248W535 panel glue: the compositor over moy_axs.

The MECHANISM is `banded_panel.BandedCompositor` in the shared `device/` tree
since 2026-08-21 (#206 item 1) -- the backend contract, the kick/drain overlap,
the core-0 feeder, the ping-pong and the meters, one body this board and the
T-Deck both run. `moy_axs` exports `moy_lcd`'s exact verb set (init/fb/nfbs/
kick/pump/drain/show/stats/pump_stats/backlight), which is the half of this
port that made the shared body possible. Read that module first; what is left
here is what is this BOARD's.

THE ARITHMETIC ON THIS GLASS. 480x320x2 = 307,200 B is ~15.4 ms at QSPI 40MHz
  x 4 lines -- and since the landscape decision (2026-08-18/19, the panel's
  MADCTL MV being dead here) the band copy is a ROTATE-gather rather than a
  memcpy: same PSRAM read traffic, plus an in-loop scatter into the uncached
  internal-SRAM bounce (moy_axs's LANDSCAPE block carries the design;
  pump_stats' pump= carries the measurement).

  ASYNC_FLUSH is the one-flag serialized fallback if the glass disagrees, and
  there is a QSPI wrinkle here the ST7789 never had: the whole frame ships
  under ONE CS assertion, so a starved feed leaves CS low with the clock idle
  mid-frame. The bridge latches per byte and should not care, but "should" is
  what the flag is for.

WHAT THIS BOARD HAS AND THE T-DECK DOES NOT: the #190-cousin GAME FOLD, below.
WHAT IT DOES NOT HAVE: the T-Deck's `LAYER_COPY_ASYNC` lever (unmeasured here
  -- a recorded A/B, not a default; device_canvas keeps its own False default
  when no one assigns it) and its `sd_bracket` (nothing else is known to share
  this board's QSPI host; sync() stays load-bearing anyway, as the idle-band
  drain and the backlight gate's fence).
"""

from banded_panel import BandedCompositor

WIDTH = 480
HEIGHT = 320

# False -> flush() is one blocking moy_axs.show(); pump_if_pending is then not
# defined at all (DeviceCanvas getattrs it and degrades) and sync() is a drain
# that always finds nothing. The one-reflash fallback if the panel tears.
ASYNC_FLUSH = True


class GuitionCompositor(BandedCompositor):
    """RGB565 framebuffer(s) in PSRAM, pushed to the AXS15231B by moy_axs."""

    def __init__(self, nfbs=2, async_flush=None):
        # The import is HERE, not in the shared base, so this board's dependency
        # on this C module stays visible -- to a reader and to
        # tests/test_staging_closure.py, which derives what a build freezes from
        # the import graph.
        import moy_axs

        if async_flush is None:
            async_flush = ASYNC_FLUSH
        BandedCompositor.__init__(self, moy_axs, nfbs, async_flush)

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
        #
        # Set on the instance rather than probed in the shared base on purpose:
        # the T-Deck must have NO such attribute at all (its docstring says so,
        # and `DeviceCanvas.blit_game` getattrs it), which is the established
        # way one board says "this board does not have that lever".
        self.fold_supported = hasattr(moy_axs, "arm_fold")

    # -- the game fold (#190's cousin; see __init__'s note) -------------------

    @property
    def fold_count(self):
        """Flushes FOLDED since boot -- the fold's liveness proof.

        A property, not a cached int: its readers take it as an attribute, and a
        frozen value is the exact symptom (something disarming every frame) the
        meter exists to distinguish from a healthy one.

        Guition-only on purpose -- the T-Deck must not grow the attribute at
        all, because absence is how a board says it lacks the lever and is what
        lets `state`'s `fold` read None there rather than a 0 that looks like a
        fold which never fires."""
        return self._lcd.fold_stats()[0]

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


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand.

    Stays per-board rather than moving to the shared base: its whole reason to
    exist is having no instance to route through, so the native module has to
    be named somewhere, and naming it in a plain `import` is what keeps this
    board's C dependency greppable.
    """
    import moy_axs
    moy_axs.backlight(on)
