"""T-Deck panel glue for the mainline build: the compositor over moy_lcd.

The MECHANISM is `banded_panel.BandedCompositor` in the shared `device/` tree
since 2026-08-21 (#206 item 1) -- the backend contract, the kick/drain overlap,
the core-0 feeder, the ping-pong and the meters all live there, in one body
this board and the Guition both run. Read that module's docstring first; what
is left here is what is this BOARD's.

THE ARITHMETIC ON THIS GLASS. 320x240x2 = 153,600 B is ~17 ms on this bus, and
  paid synchronously it caps the loop near 58 fps before anything is drawn --
  measured as flush=16.8..20.2 against the deleted fork build's 2.1 on the same
  panel, and worth ~2x across the cart roster. The overlap is what removes it.

  Band geometry is 32 rows and the bounce pair 40,960 B of internal SRAM. It
  was 48 rows, pinned by the retired 2 ms pump timer -- a band had to outlast
  the timer's period or the SPI starved between fires -- and under that timer
  32-row bands measured 53.9 -> 51.8 fps on Brick Siege, a real 4% loss.

  THE FEEDER REVERSED THAT VERDICT, and the ordering is the whole point: with
  the flush on its core-0 task there is no timer to outrun, and the same 48->32
  shrink measured 58.0 -> 58.6 fps for +16.1 KB of internal SRAM (both
  2026-08-21, d9aa73e). A band-size number measured against the timer says
  nothing about this build.

THE GAME FOLD IS PORTED (2026-09), and the 2026-08 decline it replaces was
  wrong about the shape rather than the value: it said the fold "needs moy_gfx
  kernels writing into the bounce slots, i.e. the slots exposed back to
  Python". It does not. The synthesis is C on the FEEDER --
  `native/moy_flush/moy_fold`, one body with the Guition's -- and no bounce
  slot is ever handed to Python. A small-canvas play frame now skips the
  153,600 B root composite AND the 153,600 B read-back the bands used to do:
  each band is built straight from the game snapshot, black outside the
  viewport, the game rows at integer scale inside. `fold_supported` is True
  here now, `DeviceCanvas.blit_game` arms instead of compositing, and the PUMP
  line's `fold=` climbs on every quiet play frame.

  What is still the Guition's alone is THE GAME WINDOW: shipping the game rect
  alone needs a panel whose GRAM keeps the bezels and a per-frame window arm,
  and this flush arms the full frame every time. So the transfer here is
  unchanged; what the fold buys is the PSRAM traffic, not the wire.
"""

from banded_panel import FoldingCompositor

WIDTH = 320
HEIGHT = 240

# The overlap. False -> flush() is one blocking moy_lcd.show(), byte-for-byte the
# pre-#66 path; `pump_if_pending` is then not defined at all (DeviceCanvas
# getattrs it and degrades) and sync() is a drain that always finds nothing.
# This is the one-flag fallback if the panel tears, glitches or hangs: the
# failure mode of an async flush is a torn frame, and this is how it gets ruled
# out in one reflash.
ASYNC_FLUSH = True

# The ASYNC LAYER COPY (#54 Stage 2 / #63), declared here and applied by
# `moy_runtime.run_desktop`.
#
# It lives in THIS module because the fact it rests on is the compositor's, not
# the canvas's. `device_canvas.py` is STAGED from the shared `device/` tree and
# must not be edited here, so run_desktop assigns `device_canvas.LAYER_COPY_ASYNC` from
# this constant BEFORE the first DeviceCanvas exists (`_async_ok` is latched in
# __init__; a later assignment would reach nothing).
#
# WHAT IT DOES. A cart that stamps a pre-rendered full-screen layer every frame
# -- `draw_layer(lay, 0, 0)`, and the Image form of `background()` -- pays a
# 153,600 B PSRAM->PSRAM copy inside its _draw. With this on, `DeviceCanvas`
# PREDICTS that restore from the previous frame and kicks it on the GDMA engine
# in `sync_back`, which runs before the cart's _update -- so by the time _draw
# asks for the layer the copy has already run alongside the kid's Python, and
# `blit_window_from` only waits out the tail. Measured 7 ms -> 0.04 ms on the
# fork (sakura). A misprediction is harmless: it paints a background the sync
# path then fully overwrites, and a layer EDITED this frame is a forced miss.
#
# WHY IT IS SAFE HERE, and why the reason it was off expired. The 2026-07-03
# hardware verdict against it was one specific thing: a second GDMA engine
# blitting PSRAM at full throttle, run against a panel DMA reading PSRAM
# DIRECTLY, starved the SPI FIFO and clocked out horizontal garbage bands. That
# target does not exist in `moy_lcd`: the panel DMA only ever reads the two
# INTERNAL SRAM bounce slots, it has done so since the first line of this port,
# and it does so on BOTH flush paths -- `show()` is `kick`+`drain`, so setting
# ASYNC_FLUSH = False does not bring the contention back either. The
# precondition is unconditional on this board, which is why this flag is not
# gated on `self._async`.
#
# WHAT IT CANNOT DO, so a flat reading is not a mystery. `_arm_layer_pred` only
# arms the prediction when the copy is ONE contiguous memcpy: `cam_x == 0` and
# the layer EXACTLY screen-wide. So the scroll carts, whose layers are wider
# than the screen (Sky Run at 800 px, layer_test at 512), keep the synchronous
# `blit_window` and are untouched by this flag; and Brick Siege has no layer at
# all -- its `background(col("dark_blue"))` is a `cls()`, a PSRAM fill -- so it
# cannot move by a microsecond. On the shipped roster the carts that CAN move
# are exactly three, the ones with a screen-wide `make_layer(W, H)` restored at
# (0, 0): sakura, letter_blitz and platformer. Every `background()` in
# system_carts takes a COLOUR (open_machine's `background(field)` included --
# `field` is a `col()`), so the Image form, which bakes a full-screen layer and
# is the other shape this arms for, has no cart exercising it here.
#
# TO REVERT: LAYER_COPY_ASYNC = False. One flag and one reflash, exactly like
# ASYNC_FLUSH above, and the two are independent -- so a torn or stale frame can
# be attributed to the flush or to the copy by flipping one at a time.
LAYER_COPY_ASYNC = True


class TDeckCompositor(FoldingCompositor):
    """RGB565 framebuffer(s) in PSRAM, pushed to the ST7789 by moy_lcd."""

    def __init__(self, nfbs=2, async_flush=None):
        # The import is HERE, not in the shared base, so this board's dependency
        # on this C module stays visible -- to a reader and to
        # tests/test_staging_closure.py, which derives what a build freezes from
        # the import graph.
        import moy_lcd

        if async_flush is None:
            async_flush = ASYNC_FLUSH
        FoldingCompositor.__init__(self, moy_lcd, nfbs, async_flush)

    # -- board bits ----------------------------------------------------------

    def sd_bracket(self, on):
        """Bracket an SD session: while on, every flush waits its frame out.

        The core-0 feeder made "no panel flush may overlap an SD session" a
        rule the VM-side sync() alone can no longer keep -- a paint DURING a
        session (boot's per-cart progress, a commit's toast) hands bands to
        core 0 while the VM sits inside an sdspi transaction on the same SPI
        host, which was measured 2026-08-21 as a Cache/MMU panic within
        seconds of cart loading. moy_lcd.sd_guard serializes exactly those
        frames (they still ship through the feeder; the kick just waits).
        `run_desktop`'s session wrappers call this around every session.

        This is the T-Deck's alone: the Guition's panel bus carries nothing
        else, so its compositor has no bracket and needs none.
        """
        g = getattr(self._lcd, "sd_guard", None)
        if g is not None:
            g(bool(on))


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand.

    Stays per-board rather than moving to the shared base: its whole reason to
    exist is having no instance to route through, so the native module has to
    be named somewhere, and naming it in a plain `import` is what keeps this
    board's C dependency greppable.
    """
    import moy_lcd
    moy_lcd.backlight(on)
