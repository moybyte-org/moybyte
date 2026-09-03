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

THE GAME FOLD is `banded_panel.FoldingCompositor`, shared with the T-Deck since
  `moy_fold` made the synthesis C both panels can run. This board's half of it
  stays in `moy_axs`: the rotate the
  gather runs in, and THE GAME WINDOW (the owner's 2026-08-19 insight -- the
  bezels around the game never change, and this panel's GRAM keeps them, so a
  steady play frame arms CASET/RASET to the game rect and ships that alone).
  `fold_stats` therefore has a fourth field here that the T-Deck's does not.

WHAT IT DOES NOT HAVE: the T-Deck's `LAYER_COPY_ASYNC` lever (unmeasured here
  -- a recorded A/B, not a default; device_canvas keeps its own False default
  when no one assigns it) and its `sd_bracket` (nothing else is known to share
  this board's QSPI host; sync() stays load-bearing anyway, as the idle-band
  drain and the backlight gate's fence).
"""

from banded_panel import FoldingCompositor

WIDTH = 480
HEIGHT = 320

# False -> flush() is one blocking moy_axs.show(); pump_if_pending is then not
# defined at all (DeviceCanvas getattrs it and degrades) and sync() is a drain
# that always finds nothing. The one-reflash fallback if the panel tears.
ASYNC_FLUSH = True


class GuitionCompositor(FoldingCompositor):
    """RGB565 framebuffer(s) in PSRAM, pushed to the AXS15231B by moy_axs."""

    def __init__(self, nfbs=2, async_flush=None):
        # The import is HERE, not in the shared base, so this board's dependency
        # on this C module stays visible -- to a reader and to
        # tests/test_staging_closure.py, which derives what a build freezes from
        # the import graph.
        import moy_axs

        if async_flush is None:
            async_flush = ASYNC_FLUSH
        FoldingCompositor.__init__(self, moy_axs, nfbs, async_flush)


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand.

    Stays per-board rather than moving to the shared base: its whole reason to
    exist is having no instance to route through, so the native module has to
    be named somewhere, and naming it in a plain `import` is what keeps this
    board's C dependency greppable.
    """
    import moy_axs
    moy_axs.backlight(on)
