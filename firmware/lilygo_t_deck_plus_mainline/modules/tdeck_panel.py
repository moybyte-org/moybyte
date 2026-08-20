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
ONLY where the band machinery lives: there, a Python object drives
`lcd_bus.tx_color` band by band and owns the bounce buffers, the completion
counter and the pacing stats; here all of that is `moy_lcd` -- one C module that
also owns the SPI host, so a band never crosses the Python boundary.

THE FLUSH OVERLAPS THE NEXT FRAME'S RENDER (#66/#43).
  320x240x2 = 153,600 B is ~17 ms on this bus, and paid synchronously it caps
  the loop near 58 fps before anything is drawn -- measured as flush=16.8..20.2
  against the fork build's 2.1 on the same glass, and worth ~2x across the cart
  roster. So `flush()` is the fork's three-step sequence:

      1. drain the PREVIOUS frame's bands (mostly already done -- the render
         that just ran is what hid them),
      2. swap the ping-pong so the buffer just drawn becomes the FRONT,
      3. kick it: queue the first two bands (~6 ms of transfer buffered) and
         RETURN, so the CPU renders the next frame while the panel reads.

  `flush()` therefore costs the drain RESIDUE plus the kick, not the transfer.
  The residue is what the console's `flush=` reports, and it is the number that
  should fall.

WHO FEEDS THE REST. Two feeders, both needed, both proven on the fork:
  * a PUMP_TIMER_MS machine.Timer -- esp32 timers schedule through mp_sched, so
    the callback lands between bytecodes. That is the only feeder during a
    cart's long Python `_update`, which has no other hook point.
  * `pump_if_pending`, poked by DeviceCanvas after each big native draw op (and,
    on a gated canvas, by moy_gfx's own draw context every GATE_PUMP_EVERY ops).
    The soft timer CANNOT fire while the interpreter sits inside one 15 ms C
    fill, which measured as PUMP idle=2-6 ms of starved SPI on the fork.

  Both are pure optimisations of WHEN the bands are fed. If the timer never
  starts and every poke is missed, `drain()` feeds them all itself and the flush
  is simply serialised again -- the pre-overlap cost, never a glitch. The front
  buffer is immutable while it ships (that is what the ping-pong is for), so the
  bands are tear-free by construction.

WHAT IS NOT PORTED, deliberately: the #190 flush-bounce scale fold, which
  SYNTHESISES each band for a small-canvas game instead of copying the root
  framebuffer. It needs moy_gfx kernels writing into the bounce slots, i.e. the
  slots exposed back to Python, and it is a separate lever with its own A/B.
  `fold_supported` is absent, so `DeviceCanvas.blit_game` takes its ordinary
  root composite path and the PUMP line prints fold=0. Nothing degrades.
"""

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

# Soft-timer pump period. 2 ms is the fork's shipped value, arrived at on
# hardware: a band is ~3 ms of transfer, so a 2 ms feeder stays ahead of the two
# buffered slots. Timer 3 of the S3's four (2 groups x 2); nothing else in this
# image takes one. 0 disables the timer and leaves the draw-verb poke + drain.
PUMP_TIMER_MS = 2
PUMP_TIMER_ID = 3


class TDeckCompositor:
    """RGB565 framebuffer(s) in PSRAM, pushed to the ST7789 by moy_lcd."""

    def __init__(self, nfbs=2, async_flush=None):
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

        # The overlap needs BOTH a moy_lcd that can split the flush and two
        # distinct buffers to ping-pong between: with one framebuffer the DMA
        # would read exactly what the next frame draws into.
        if async_flush is None:
            async_flush = ASYNC_FLUSH
        self._async = bool(async_flush) and len(self._fbs) > 1 \
            and hasattr(moy_lcd, "kick")
        # `bounce_flush` is what device_diag._diag_pump gates the PUMP line on.
        self.bounce_flush = self._async
        self._pump_timer = None
        if self._async:
            # The C function itself, not a bound method: DeviceCanvas stores this
            # and calls it after every big native op, and moy_gfx's draw context
            # mp_call_function_0's it from inside C. One call, no Python frame.
            self.pump_if_pending = moy_lcd.pump
            if PUMP_TIMER_MS:
                try:
                    from machine import Timer
                    self._pump_timer = Timer(PUMP_TIMER_ID)
                    # The callback is handed the timer object; moy_lcd.pump takes
                    # 0 or 1 args precisely so it can be wired here directly.
                    self._pump_timer.init(period=PUMP_TIMER_MS,
                                          mode=Timer.PERIODIC,
                                          callback=moy_lcd.pump)
                except Exception as exc:  # noqa: BLE001
                    # Not fatal, and worth saying out loud: the flush still works,
                    # it just loses the feeder that covers a cart's Python logic.
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
        # 1. Finish the previous frame. Most of it already happened behind the
        #    render that just ran, so this is the residue -- and it is what the
        #    console times as `flush=`.
        lcd.drain()
        # 2. The buffer just drawn becomes the FRONT; drawing moves to the other,
        #    which the drain above just made safe to overwrite.
        front = self._back
        self._swap()
        # 3. Queue the first bands and return.
        lcd.kick(front)

    def _swap(self):
        # Ping-pong so the next frame is drawn into a buffer the panel is not
        # reading. This is what DeviceCanvas.sync_back re-points at, and what
        # DeviceCanvas.RETAINED_FRAMES = 2 is calibrated against.
        n = len(self._fbs)
        if n > 1:
            self._back = (self._back + 1) % n

    def sync(self):
        """Leave NO panel transfer on the shared SPI bus.

        Load-bearing, not hygiene: the SD card shares this host, and an SD op
        overlapping an in-flight panel DMA is the documented way to hang this
        board. `run_desktop._with_sd_synced` calls this before every session.
        Also the fence anything that reaches around the console must take.
        """
        self._lcd.drain()

    # -- diagnostics (#66 lever 4) -------------------------------------------

    @property
    def pump_last_us(self):
        """CPU us spent feeding bands during the last shipped frame (HITCH's
        `pump=`). A property because device_diag reads it as an attribute."""
        return self._lcd.pump_stats()[0] if self._async else 0

    def bounce_stats(self):
        """(pump_us, idle_us, idle_n, feed_us, bands) -- the PUMP diag line.

        idle_us is the one to read: it is time the SPI sat starved because a band
        was fed only after the previous one had already finished. ~0 means the
        feeders are keeping up and the remaining flush cost is real transfer
        time; a big number means the pump period or the slot count is the wall.
        """
        if not self._async:
            return (0, 0, 0, -1, 0)
        st = self._lcd.pump_stats()
        return (st[0], st[1], st[2], st[3], st[4])

    # -- board bits ----------------------------------------------------------

    def set_backlight(self, on=True):
        self._lcd.backlight(on)

    def stats(self):
        """(flushes, last_flush_us) -- the WALL span of the last frame's
        transfer, kick to fully out. It does not shrink when the overlap lands;
        what shrinks is the share of it the CPU waits for (bounce_stats, and the
        console's own `flush=`)."""
        return self._lcd.stats()


def set_backlight(on=True):
    """Module-level backlight, for code that has no compositor in hand."""
    import moy_lcd
    moy_lcd.backlight(on)
